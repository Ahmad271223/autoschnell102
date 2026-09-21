# -*- coding: utf-8 -*-
"""Wunsch Ahmad 21.09.2026 (Pruefbericht AD-06): Notfall-Codes der
Zwei-Faktor-Anmeldung neu erzeugen, ohne sie abzuschalten.

Wegwerf-Datenbank je Test (echtes Mongo), keine HTTP-Aufrufe.
- POST /admin/me/mfa/codes-neu: nur mit aktuellem App-Code (kein
  Notfall-Code), Schluessel und Sitzung bleiben, 8 neue Codes ersetzen alle
  alten, Replay-Schutz, Verlaufs- und Audit-Eintrag; nicht aktiv -> 409
- falscher Code bei "Abschalten" und bei den neuen Codes -> 400 (nie 401,
  sonst meldet die Oberflaeche ab); 5 Fehlversuche -> 15 Minuten Sperre
- scripts/mfa_pruefen.py --abschalten setzt die Gnadenfrist auch ohne
  Zwei-Faktor-Daten (Tab nach "Abschalten" verloren)
Pruefung 21.09.2026 (MFA):
- gemeinsame Mengenbremse je Konto (10/min, Mongo, fail_closed) — viele
  GLEICHZEITIGE Rateversuche liefen vorher an der 5er-Sperre vorbei
- Ziffern anderer Schriften: nie 500; arabisch-indisch/vollbreit gelten
- mfa_pruefen.py: 'vorher' einmal bestimmt (zweiter Lauf = keine_daten)
"""
import asyncio
import importlib.util
import inspect
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import deps  # noqa: E402
import mfa as MFA  # noqa: E402
import routes.admin as ADMIN  # noqa: E402

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
SA = {"id": "sa", "role": "admin", "is_super_admin": True, "active": True, "username": "betreiber",
      "email": "betreiber@cashcar.local", "current_session_id": "sid-alt"}
CODE_FORM = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{8}$")


@pytest.fixture
def wegwerf(monkeypatch):
    from motor.motor_asyncio import AsyncIOMotorClient
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    name = f"autoschnell_mfacodes_{uuid.uuid4().hex[:10]}"
    db = client[name]
    for mod in (deps, ADMIN):
        monkeypatch.setattr(mod, "db", db)
    try:
        yield SimpleNamespace(db=db, run=loop.run_until_complete)
    finally:
        try:
            loop.run_until_complete(client.drop_database(name))
        finally:
            client.close()
            loop.close()


def _jetzt_z() -> int:
    return int(time.time() // 30)


def _falscher_code(secret: str) -> str:
    """Ein 6-stelliger Code, der sicher zu KEINEM Zaehler im Toleranzfenster passt."""
    z = _jetzt_z()
    belegt = {MFA.totp(secret, z + d) for d in range(-2, 3)}
    return next(c for c in ("000000", "111111", "123456", "999999") if c not in belegt)


def _aktiver_admin(run, db, **mfa_extra):
    secret = MFA.secret_erzeugen()
    alt_codes, alt_hashes = MFA.wiederherstellungscodes()
    m = {"aktiv": True, "secret": MFA.verschluesseln(secret), "letzter_zaehler": _jetzt_z() - 5,
         "fehlversuche": 0, "wiederherstellung": alt_hashes[:5],
         "aktiviert_am": "2026-09-01T10:00:00+00:00", **mfa_extra}
    run(db.users.insert_one({**SA, "mfa": m}))
    return secret, alt_codes, m


def _codes_neu(run, code):
    return run(ADMIN.admin_mfa_codes_neu(ADMIN.MfaCodeIn(code=code), admin=SA))


# ============================================================ codes-neu
def test_codes_neu_ersetzt_nur_die_codes(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    secret, alt_codes, m_alt = _aktiver_admin(run, db)
    erg = _codes_neu(run, MFA.totp(secret))
    codes = erg["wiederherstellungscodes"]
    assert len(codes) == 8 and len(set(codes)) == 8
    assert all(CODE_FORM.match(c) for c in codes), codes
    u = run(db.users.find_one({"id": "sa"}))
    m = u["mfa"]
    # gespeichert genau wie bei der Aktivierung: nur SHA-256, nie Klartext
    assert m["wiederherstellung"] == [MFA.code_hash(c) for c in codes]
    assert not set(m["wiederherstellung"]) & {MFA.code_hash(c) for c in alt_codes}, \
        "alte Codes muessen sofort ungueltig sein"
    assert not any(c in str(u) for c in codes), "Klartext-Codes gehoeren nicht in die DB"
    # Schluessel, Aktivierung und Sitzung bleiben
    assert m["aktiv"] is True and m["secret"] == m_alt["secret"]
    assert m["aktiviert_am"] == m_alt["aktiviert_am"]
    assert u["current_session_id"] == "sid-alt", "neue Codes beenden die Sitzung nicht"
    assert m["letzter_zaehler"] > m_alt["letzter_zaehler"], "der App-Code ist verbraucht"
    assert m["fehlversuche"] == 0 and m.get("codes_erneuert_am")
    # Verlauf + Audit
    eintrag = run(db.zugangs_aenderungen.find_one({"art": "mfa_codes_neu"}))
    assert eintrag and eintrag["subject_user_id"] == "sa" and eintrag["admin_email"] == "betreiber"
    assert eintrag["alt"] == "5_wiederherstellungscodes"
    assert not any(c in str(eintrag) for c in codes)
    log = run(db.activity_logs.find_one({"action": "admin.mfa.codes_neu"}))
    assert log and log["user_id"] == "sa" and log["meta"] == {"alt_uebrig": 5, "neu": 8}
    # Status zeigt 8 uebrige Codes und den Zeitpunkt
    st = run(ADMIN.admin_mfa_status(admin=SA))
    assert st["aktiv"] is True and st["wiederherstellungscodes_uebrig"] == 8
    assert st["codes_erneuert_am"] == m["codes_erneuert_am"]


def test_codes_neu_replay_und_notfallcode(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    secret, alt_codes, _ = _aktiver_admin(run, db)
    code = MFA.totp(secret)
    erste = _codes_neu(run, code)["wiederherstellungscodes"]
    # derselbe App-Code ein zweites Mal: 400 mit klarem Hinweis, kein Fehlversuch
    with pytest.raises(HTTPException) as e:
        _codes_neu(run, code)
    assert e.value.status_code == 400 and "schon verwendet" in e.value.detail
    # ein Notfall-Code gilt hier nicht (sonst vermehrte er sich selbst)
    with pytest.raises(HTTPException) as e:
        _codes_neu(run, erste[0])
    assert e.value.status_code == 400 and "6-stelligen Code" in e.value.detail
    m = run(db.users.find_one({"id": "sa"}))["mfa"]
    assert m["wiederherstellung"] == [MFA.code_hash(c) for c in erste], "Codes unveraendert"
    assert m.get("fehlversuche", 0) == 0, "Replay/Notfall-Code zaehlen nicht als Rateversuch"


def test_codes_neu_falscher_code_400_und_sperre(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    secret, _, m_alt = _aktiver_admin(run, db)
    falsch = _falscher_code(secret)
    for i in range(ADMIN.MFA_FEHLVERSUCHE_MAX):
        with pytest.raises(HTTPException) as e:
            _codes_neu(run, falsch)
        assert e.value.status_code == 400, "nie 401 — die Oberflaeche wuerde abmelden"
        assert e.value.detail == "Code ungültig"
    m = run(db.users.find_one({"id": "sa"}))["mfa"]
    assert m.get("gesperrt_bis", "") > datetime.now(timezone.utc).isoformat(), "5 Fehler -> Sperre"
    assert m["wiederherstellung"] == m_alt["wiederherstellung"], "nichts ersetzt"
    # waehrend der Sperre hilft auch der richtige Code nicht
    with pytest.raises(HTTPException) as e:
        _codes_neu(run, MFA.totp(secret))
    assert e.value.status_code == 429
    assert run(db.activity_logs.count_documents({"action": "admin.mfa.code_falsch"})) == 5


def test_codes_neu_ohne_aktive_mfa_409(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    run(db.users.insert_one({**SA}))
    with pytest.raises(HTTPException) as e:
        _codes_neu(run, "123456")
    assert e.value.status_code == 409 and "nicht aktiv" in e.value.detail
    # halb fertige Einrichtung (nur pending_secret) ist ebenfalls "nicht aktiv"
    run(db.users.update_one({"id": "sa"}, {"$set": {"mfa": {
        "pending_secret": MFA.verschluesseln(MFA.secret_erzeugen()),
        "pending_seit": datetime.now(timezone.utc).isoformat()}}}))
    with pytest.raises(HTTPException) as e:
        _codes_neu(run, "123456")
    assert e.value.status_code == 409


def test_codes_neu_nach_geheimniswechsel_409(wegwerf, monkeypatch):
    """Compare-and-set: aendert sich das Geheimnis zwischen Pruefung und
    Schreiben (paralleles Abschalten/Neu-Einrichten), wird nichts ersetzt."""
    db, run = wegwerf.db, wegwerf.run
    secret, _, _ = _aktiver_admin(run, db)
    echt = ADMIN._mfa_app_code_bestaetigen

    async def _dazwischen(admin_id, m, code):
        z = await echt(admin_id, m, code)
        await db.users.update_one({"id": admin_id},
                                  {"$set": {"mfa.secret": MFA.verschluesseln("ANDERES")}})
        return z
    monkeypatch.setattr(ADMIN, "_mfa_app_code_bestaetigen", _dazwischen)
    with pytest.raises(HTTPException) as e:
        _codes_neu(run, MFA.totp(secret))
    assert e.value.status_code == 409
    assert run(db.zugangs_aenderungen.count_documents({})) == 0


# ============================================================ Abschalten
def test_abschalten_falscher_code_ist_400_nicht_401(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    secret, _, _ = _aktiver_admin(run, db)
    with pytest.raises(HTTPException) as e:
        run(ADMIN.admin_mfa_deaktivieren(ADMIN.MfaCodeIn(code=_falscher_code(secret)), admin=SA))
    assert e.value.status_code == 400 and e.value.detail == "Code ungültig"
    u = run(db.users.find_one({"id": "sa"}))
    assert u["mfa"]["aktiv"] is True and u["mfa"]["fehlversuche"] == 1
    erg = run(ADMIN.admin_mfa_deaktivieren(ADMIN.MfaCodeIn(code=MFA.totp(secret)), admin=SA))
    assert erg == {"ok": True, "aktiv": False}
    assert "mfa" not in run(db.users.find_one({"id": "sa"}))
    # Quelltext: keine 401 mehr in den Code-Pruefungen der eigenen Einstellungen
    for f in (ADMIN.admin_mfa_deaktivieren, ADMIN.admin_mfa_codes_neu,
              ADMIN._mfa_app_code_bestaetigen):
        assert "HTTPException(401" not in inspect.getsource(f), f.__name__


# ============================================================ Mengenbremse (Pruefung 21.09.2026)
@pytest.fixture
def bremse_an(monkeypatch):
    """Limiter wie in Produktion (der Testschalter RATE_LIMIT_ENABLED=false
    darf hier nicht durchschlagen). Zaehler liegt in der Wegwerf-DB."""
    import rate_limiter as RL
    monkeypatch.setattr(RL, "_RATE_LIMIT_ENABLED", True)
    return RL


def test_bremse_ist_gemeinsam_je_konto_und_fail_closed():
    lim = ADMIN._admin_mfa_limiter
    assert lim.name == "admin-mfa" and lim.fail_closed is True
    assert lim.max_attempts == 10 and lim.window_seconds == 60
    src = inspect.getsource(ADMIN._mfa_app_code_bestaetigen)
    # als ERSTES, vor Sperre, Formpruefung und TOTP — Schluessel ist die Konto-ID
    assert "await _admin_mfa_limiter.check(admin_id)" in src
    assert src.index("_admin_mfa_limiter.check") < src.index('m.get("gesperrt_bis")') \
        < src.index("code_pruefen")


def test_bremse_nach_zehn_eingaben_je_minute(wegwerf, bremse_an):
    db, run = wegwerf.db, wegwerf.run
    secret, alt_codes, m_alt = _aktiver_admin(run, db)
    for _ in range(10):                   # Notfall-Code: 400, zaehlt aber mit
        with pytest.raises(HTTPException) as e:
            _codes_neu(run, alt_codes[0])
        assert e.value.status_code == 400
    with pytest.raises(HTTPException) as e:
        _codes_neu(run, MFA.totp(secret))  # selbst der richtige Code
    assert e.value.status_code == 429 and e.value.detail == ADMIN.MFA_ZU_VIELE_EINGABEN
    m = run(db.users.find_one({"id": "sa"}))["mfa"]
    assert m["wiederherstellung"] == m_alt["wiederherstellung"], "nichts ersetzt"
    assert m.get("fehlversuche", 0) == 0 and not m.get("gesperrt_bis")
    # Gemeinsamer Zaehler in Mongo (alle Worker/Server), Schluessel = Konto-ID
    assert run(db.rate_limits.count_documents({"_id": {"$regex": "^admin-mfa:sa:"}})) >= 1
    # Abschalten teilt sich denselben Zaehler
    with pytest.raises(HTTPException) as e:
        run(ADMIN.admin_mfa_deaktivieren(ADMIN.MfaCodeIn(code=MFA.totp(secret)), admin=SA))
    assert e.value.status_code == 429
    assert run(db.users.find_one({"id": "sa"}))["mfa"]["aktiv"] is True


def test_bremse_haelt_gleichzeitige_rateversuche_auf(wegwerf, bremse_an):
    """Der eigentliche Befund: 30 GLEICHZEITIGE falsche Codes lasen alle den
    Stand vor der Sperre und wurden alle gegen TOTP geprueft. Jetzt kommen
    hoechstens 10 je Minute bis zur Code-Pruefung, der Rest bekommt 429."""
    db, run = wegwerf.db, wegwerf.run
    secret, _, _ = _aktiver_admin(run, db)
    falsch = _falscher_code(secret)

    async def schwung():
        return await asyncio.gather(
            *[ADMIN.admin_mfa_codes_neu(ADMIN.MfaCodeIn(code=falsch), admin=SA) for _ in range(30)],
            return_exceptions=True)

    erg = run(schwung())
    assert all(isinstance(x, HTTPException) for x in erg), erg
    stati = [x.status_code for x in erg]
    geprueft = run(db.activity_logs.count_documents({"action": "admin.mfa.code_falsch"}))
    assert geprueft <= ADMIN._admin_mfa_limiter.max_attempts, (geprueft, stati)
    assert stati.count(429) >= 30 - ADMIN._admin_mfa_limiter.max_attempts, stati
    assert set(stati) <= {400, 429}, stati


def test_bremse_ohne_gemeinsamen_zaehler_sperrt(wegwerf, bremse_an, monkeypatch):
    db, run = wegwerf.db, wegwerf.run
    secret, _, _ = _aktiver_admin(run, db)

    async def _kaputt(key):
        raise RuntimeError("Mongo weg")
    monkeypatch.setattr(ADMIN._admin_mfa_limiter, "_check_mongo", _kaputt)
    with pytest.raises(HTTPException) as e:
        _codes_neu(run, MFA.totp(secret))
    assert e.value.status_code == 429, "fail_closed: kein Zaehler je Prozess"


# ============================================================ Ziffern anderer Schriften
ARABISCH = "٠١٢٣٤٥٦٧٨٩"                # U+0660..U+0669
VOLLBREIT = "０１２３４５６７８９"      # U+FF10..U+FF19


def _umschreiben(code: str, ziffern: str) -> str:
    return "".join(ziffern[int(c)] for c in code)


@pytest.mark.parametrize("roh", ["١٢٣٤٥٦", "１２３４５６", "¹²³⁴⁵⁶", "①②③④⑤⑥", "12345٦x", "", " "])
def test_code_pruefen_wirft_nie_bei_fremden_ziffern(roh):
    """Pruefung 21.09.2026 (MFA): isdigit() liess '١٢٣٤٥٦' durch, und
    hmac.compare_digest warf bei Nicht-ASCII einen TypeError -> 500."""
    secret = MFA.secret_erzeugen()
    MFA.code_pruefen(secret, roh)          # darf nicht werfen
    MFA.code_pruefen(secret, roh, 10 ** 12)


def test_code_pruefen_versteht_arabische_und_vollbreite_ziffern():
    secret = MFA.secret_erzeugen()
    z = _jetzt_z()
    code = MFA.totp(secret, z)
    for ziffern in (ARABISCH, VOLLBREIT):
        assert MFA.code_pruefen(secret, _umschreiben(code, ziffern)) == z
    assert MFA.code_normalisieren(" ١٢٣ ٤٥٦ ") == "123456"
    # hochgestellte/umkreiste Ziffern sind keine Dezimalziffern -> nie gueltig
    assert not MFA.code_format_ok(MFA.code_normalisieren("¹²³⁴⁵⁶"))
    assert not MFA.code_format_ok(MFA.code_normalisieren("①②③④⑤⑥"))


def test_codes_neu_mit_fremden_ziffern_400_statt_500(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    secret, _, _ = _aktiver_admin(run, db)
    # nicht umsetzbar (hochgestellt): 400 mit Hinweis, kein Fehlversuch
    with pytest.raises(HTTPException) as e:
        _codes_neu(run, "¹²³⁴⁵⁶")
    assert e.value.status_code == 400 and "6-stelligen Code" in e.value.detail
    assert run(db.users.find_one({"id": "sa"}))["mfa"].get("fehlversuche", 0) == 0
    # falscher Code in vollbreiten Ziffern: normaler Fehlversuch, kein 500
    with pytest.raises(HTTPException) as e:
        _codes_neu(run, _umschreiben(_falscher_code(secret), VOLLBREIT))
    assert e.value.status_code == 400 and e.value.detail == "Code ungültig"
    assert run(db.users.find_one({"id": "sa"}))["mfa"]["fehlversuche"] == 1
    # richtiger Code auf einer arabisch-indischen Tastatur: funktioniert
    erg = _codes_neu(run, _umschreiben(MFA.totp(secret), ARABISCH))
    assert len(erg["wiederherstellungscodes"]) == 8


# ============================================================ Notweg-Skript
def _skript():
    spec = importlib.util.spec_from_file_location(
        "mfa_pruefen_test", BACKEND / "scripts" / "mfa_pruefen.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def sync_db(monkeypatch):
    from pymongo import MongoClient
    client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    name = f"autoschnell_mfaskript_{uuid.uuid4().hex[:10]}"
    monkeypatch.setenv("MONGO_URL", MONGO_URL)
    monkeypatch.setenv("DB_NAME", name)
    try:
        yield client[name]
    finally:
        client.drop_database(name)
        client.close()


def test_mfa_pruefen_abschalten_ohne_daten_setzt_gnadenfrist(sync_db, monkeypatch, capsys):
    sync_db.users.insert_one({"id": "sa", "username": "betreiber", "role": "admin",
                              "is_super_admin": True, "active": True,
                              "current_session_id": None})
    mod = _skript()
    # ohne --ja: nichts aendern
    monkeypatch.setattr(sys, "argv", ["mfa_pruefen.py", "--konto", "betreiber", "--abschalten"])
    assert mod.main() == 2
    assert "mfa" not in sync_db.users.find_one({"id": "sa"})
    monkeypatch.setattr(sys, "argv", ["mfa_pruefen.py", "--konto", "betreiber",
                                      "--abschalten", "--ja"])
    vorher = datetime.now(timezone.utc).isoformat()
    assert mod.main() == 0
    out = capsys.readouterr().out
    m = sync_db.users.find_one({"id": "sa"})["mfa"]
    assert m["aktiv"] is False and m["pflicht_ausgesetzt_bis"] > vorher, \
        "Gnadenfrist auch ohne Zwei-Faktor-Daten (ausgesperrt nach 'Abschalten')"
    frist = datetime.fromisoformat(m["pflicht_ausgesetzt_bis"])
    assert 29 * 60 < (frist - datetime.now(timezone.utc)).total_seconds() <= 30 * 60
    assert "nichts abzuschalten" in out and "Gnadenfrist" in out and "trotzdem" in out
    log = sync_db.activity_logs.find_one({"action": "auth.mfa.abgeschaltet.betreiber"})
    assert log and log["meta"]["vorher"] == "keine_daten"


def test_mfa_pruefen_abschalten_mit_aktiver_mfa(sync_db, monkeypatch, capsys):
    sync_db.users.insert_one({"id": "sa", "username": "betreiber", "role": "admin",
                              "is_super_admin": True, "active": True,
                              "current_session_id": "sid-x",
                              "mfa": {"aktiv": True, "secret": MFA.verschluesseln("ABC"),
                                      "wiederherstellung": ["h1"]}})
    monkeypatch.setattr(sys, "argv", ["mfa_pruefen.py", "--konto", "betreiber",
                                      "--abschalten", "--ja"])
    assert _skript().main() == 0
    u = sync_db.users.find_one({"id": "sa"})
    assert set(u["mfa"]) == {"aktiv", "pflicht_ausgesetzt_bis"} and u["mfa"]["aktiv"] is False
    assert u["current_session_id"] is None
    assert "ABGESCHALTET" in capsys.readouterr().out
    log = sync_db.activity_logs.find_one({"action": "auth.mfa.abgeschaltet.betreiber"})
    assert log["meta"]["vorher"] == "aktiv"


def _abschalten(mod, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["mfa_pruefen.py", "--konto", "betreiber",
                                      "--abschalten", "--ja"])
    return mod.main()


def test_mfa_pruefen_zweiter_lauf_meldet_keine_daten(sync_db, monkeypatch, capsys):
    """Pruefung 21.09.2026 (MFA): der dokumentierte zweite Lauf nach Ablauf der
    Frist fand nur {aktiv: False, pflicht_ausgesetzt_bis} vor — und schrieb
    'eingerichtet_nicht_aktiv' samt 'halb fertige Einrichtung verworfen'."""
    sync_db.users.insert_one({"id": "sa", "username": "betreiber", "role": "admin",
                              "is_super_admin": True, "active": True,
                              "mfa": {"aktiv": True, "secret": MFA.verschluesseln("ABC")}})
    mod = _skript()
    assert _abschalten(mod, monkeypatch) == 0
    erste_frist = sync_db.users.find_one({"id": "sa"})["mfa"]["pflicht_ausgesetzt_bis"]
    capsys.readouterr()
    assert _abschalten(mod, monkeypatch) == 0
    out = capsys.readouterr().out
    logs = list(sync_db.activity_logs.find({"action": "auth.mfa.abgeschaltet.betreiber"})
                .sort("created_at", 1))
    assert [x["meta"]["vorher"] for x in logs] == ["aktiv", "keine_daten"]
    assert logs[1]["meta"]["frist_vorher"] == erste_frist
    assert "halb fertige" not in out and "nichts abzuschalten" in out
    assert "frueheren Laufs" in out and "Gnadenfrist" in out
    m = sync_db.users.find_one({"id": "sa"})["mfa"]
    assert m["pflicht_ausgesetzt_bis"] >= erste_frist, "Frist neu gesetzt"


def test_mfa_pruefen_halbe_einrichtung(sync_db, monkeypatch, capsys):
    sync_db.users.insert_one({"id": "sa", "username": "betreiber", "role": "admin",
                              "is_super_admin": True, "active": True,
                              "mfa": {"pending_secret": MFA.verschluesseln("ABC"),
                                      "pending_seit": datetime.now(timezone.utc).isoformat()}})
    assert _abschalten(_skript(), monkeypatch) == 0
    out = capsys.readouterr().out
    assert "halb fertige Einrichtung" in out
    log = sync_db.activity_logs.find_one({"action": "auth.mfa.abgeschaltet.betreiber"})
    assert log["meta"]["vorher"] == "eingerichtet_nicht_aktiv" and "frist_vorher" not in log["meta"]


def test_mfa_pruefen_vorher_wird_einmal_bestimmt():
    q = (BACKEND / "scripts" / "mfa_pruefen.py").read_text(encoding="utf-8")
    assert '"vorher": vorher' in q
    assert 'elif vorher == "eingerichtet_nicht_aktiv":' in q
    assert "elif m:" not in q, "jedes nicht leere mfa galt als halbe Einrichtung"


# ============================================================ Oberflaeche
def test_einstellungen_haben_den_knopf():
    q = (BACKEND.parent / "frontend" / "src" / "pages" / "admin_v2" / "Settings.jsx").read_text(
        encoding="utf-8")
    assert "Neue Notfall-Codes erzeugen" in q
    assert '"/admin/me/mfa/codes-neu"' in q
