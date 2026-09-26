# -*- coding: utf-8 -*-
"""Rollenpruefung 22.09.2026, Welle B3 (RP-556 / RP-543).

Wegwerf-Datenbank je Test (echtes Mongo), keine HTTP-Aufrufe.
RP-556 — Geraet wechseln OHNE Abschalten:
- POST /admin/me/mfa/wechsel {code}: aktueller App-Code Pflicht (Replay,
  Fehlversuche, Sperre wie beim Abschalten), legt ein Pending-Geheimnis
  NEBEN dem aktiven an; der alte Schluessel gilt weiter (Anmeldung klappt)
- POST /admin/me/mfa/aktivieren mit dem Code des NEUEN Geraets ersetzt das
  aktive Geheimnis per CAS, verwirft das Pending, erzeugt neue Notfall-Codes,
  Sitzung bleibt; falscher Code ersetzt nichts
- Pending verfaellt nach 15 Minuten (410, altes Geheimnis bleibt)
- ohne Wechsel bleibt /aktivieren bei aktiver MFA 409; /einrichten nennt
  den neuen Weg; Abschalten verwirft ein Pending
RP-543 — Erstinstallation: Seed legt das Betreiberkonto mit Frist an,
Anmeldung in der Frist mit Passwort, Einrichten + Aktivieren, danach
Anmeldung mit Code; nach Ablauf der Frist 403 mit dem Notweg, das
Notfall-Skript setzt die Frist neu.
"""
import asyncio
import importlib.util
import inspect
import os
import re
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

BACKEND = Path(__file__).resolve().parents[1]
WURZEL = BACKEND.parent
sys.path.insert(0, str(BACKEND))

import auth as AUTHMOD  # noqa: E402
import deps  # noqa: E402
import mfa as MFA  # noqa: E402
import routes.admin as ADMIN  # noqa: E402
import routes.auth as AUTH  # noqa: E402
import server as SRV  # noqa: E402

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
SA = {"id": "b3_sa", "role": "admin", "is_super_admin": True, "active": True,
      "username": "b3-betreiber", "email": "", "current_session_id": "sid-b3"}
CODE_FORM = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{8}$")


def _iso(**delta):
    return (datetime.now(timezone.utc) + timedelta(**delta)).isoformat()


def _request(ip="203.0.113.77"):
    return SimpleNamespace(client=SimpleNamespace(host=ip), headers={})


@pytest.fixture
def wegwerf(monkeypatch):
    from motor.motor_asyncio import AsyncIOMotorClient
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    name = f"autoschnell_b3mfa_{uuid.uuid4().hex[:10]}"
    db = client[name]
    for mod in (deps, ADMIN, AUTH, SRV):
        monkeypatch.setattr(mod, "db", db)
    monkeypatch.delenv("APP_ENV", raising=False)
    try:
        yield SimpleNamespace(db=db, run=loop.run_until_complete, name=name)
    finally:
        try:
            loop.run_until_complete(client.drop_database(name))
        finally:
            client.close()
            loop.close()


def _jetzt_z() -> int:
    return int(time.time() // 30)


def _falscher_code(*secrets: str) -> str:
    z = _jetzt_z()
    belegt = {MFA.totp(s, z + d) for s in secrets for d in range(-2, 3)}
    return next(c for c in ("000000", "111111", "123456", "999999") if c not in belegt)


def _aktiver_admin(run, db, **mfa_extra):
    secret = MFA.secret_erzeugen()
    alt_codes, alt_hashes = MFA.wiederherstellungscodes()
    m = {"aktiv": True, "secret": MFA.verschluesseln(secret), "letzter_zaehler": _jetzt_z() - 5,
         "fehlversuche": 0, "wiederherstellung": alt_hashes[:5],
         "aktiviert_am": "2026-09-01T10:00:00+00:00", **mfa_extra}
    run(db.users.insert_one({**SA, "mfa": m}))
    return secret, alt_codes, m


def _mfa(run, db):
    return (run(db.users.find_one({"id": SA["id"]})) or {}).get("mfa") or {}


def _wechsel(run, code):
    return run(ADMIN.admin_mfa_wechsel(ADMIN.MfaCodeIn(code=code), admin=SA))


def _aktivieren(run, code, admin=SA):
    return run(ADMIN.admin_mfa_aktivieren(ADMIN.MfaCodeIn(code=code), admin=admin))


def _login_mfa(run, db, code):
    user = run(db.users.find_one({"id": SA["id"]}))
    return run(AUTH.login_mfa(AUTH.MfaLoginIn(mfa_token=AUTHMOD.create_mfa_token(user), code=code),
                              _request()))


# ============================================================ RP-556 Geraetewechsel
def test_rp556_wechsel_ersetzt_den_schluessel_erst_nach_bestaetigung(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    alt, alt_codes, m_alt = _aktiver_admin(run, db)
    erg = _wechsel(run, MFA.totp(alt))
    neu = erg["secret"]
    assert neu != alt and erg["wechsel"] is True and erg["gueltig_bis"] > deps.now_iso()
    assert erg["otpauth_uri"].startswith("otpauth://totp/") and neu in erg["otpauth_uri"]
    assert "15 Minuten" in erg["hinweis"]
    # Pending liegt NEBEN dem aktiven Geheimnis, das unveraendert weitergilt
    u = run(db.users.find_one({"id": SA["id"]}))
    m = u["mfa"]
    assert m["aktiv"] is True and m["secret"] == m_alt["secret"]
    assert m["pending_wechsel"] is True and MFA.entschluesseln(m["pending_secret"]) == neu
    assert neu not in str(u), "kein Klartext-Geheimnis in der DB"
    assert m["letzter_zaehler"] >= _jetzt_z() - 1, "der App-Code ist verbraucht"
    assert u["current_session_id"] == "sid-b3"
    st = run(ADMIN.admin_mfa_status(admin=SA))
    assert st["aktiv"] is True and st["wechsel_offen"] is True and st["wechsel_bis"]
    assert st["einrichtung_offen"] is False
    # Anmeldung mit dem ALTEN Schluessel klappt weiterhin
    sitzung = _login_mfa(run, db, MFA.totp(alt, _jetzt_z() + 1))
    assert sitzung["token"] and sitzung["user"]["mfa_aktiv"] is True
    # falscher Code vom neuen Geraet: 400, nichts ersetzt
    with pytest.raises(HTTPException) as e:
        _aktivieren(run, _falscher_code(alt, neu))
    assert e.value.status_code == 400 and "Code ungültig" in e.value.detail
    m = _mfa(run, db)
    assert m["secret"] == m_alt["secret"] and m["pending_wechsel"] is True
    # richtiger Code vom neuen Geraet: Geheimnis ersetzt, Pending weg, neue Codes
    erg2 = _aktivieren(run, MFA.totp(neu))
    assert erg2["ok"] is True and erg2["geraet_gewechselt"] is True and erg2["aktiv"] is True
    codes = erg2["wiederherstellungscodes"]
    assert len(codes) == 8 and len(set(codes)) == 8 and all(CODE_FORM.match(c) for c in codes)
    assert "token" not in erg2, "die Sitzung wurde schon mit zweitem Faktor ausgestellt"
    u = run(db.users.find_one({"id": SA["id"]}))
    m = u["mfa"]
    assert m["aktiv"] is True and MFA.entschluesseln(m["secret"]) == neu
    assert "pending_secret" not in m and "pending_wechsel" not in m and "pending_seit" not in m
    assert m["wiederherstellung"] == [MFA.code_hash(c) for c in codes]
    assert not set(m["wiederherstellung"]) & set(m_alt["wiederherstellung"]), "alte Codes weg"
    assert m["aktiviert_am"] == m_alt["aktiviert_am"] and m["geraet_gewechselt_am"]
    assert m["codes_erneuert_am"] == m["geraet_gewechselt_am"]
    assert not any(c in str(u) for c in codes) and neu not in str(u)
    assert u["current_session_id"] is not None, "Sitzung bleibt (Einzel-Sitzung)"
    # alter Schluessel gilt nicht mehr, neuer schon
    z = _jetzt_z()
    with pytest.raises(HTTPException) as e:
        _login_mfa(run, db, MFA.totp(alt, z + 1))
    assert e.value.status_code == 401
    sitzung = _login_mfa(run, db, MFA.totp(neu, z + 1))
    assert sitzung["token"]
    # Verlauf + Audit, ohne Klartext
    eintrag = run(db.zugangs_aenderungen.find_one({"art": "mfa_geraet_gewechselt"}))
    assert eintrag and eintrag["subject_user_id"] == SA["id"] and eintrag["admin_id"] == SA["id"]
    assert eintrag["alt"] == "5_wiederherstellungscodes"
    assert not any(c in str(eintrag) for c in codes) and neu not in str(eintrag)
    for aktion in ("admin.mfa.wechsel_begonnen", "admin.mfa.geraet_gewechselt"):
        assert run(db.activity_logs.find_one({"action": aktion, "user_id": SA["id"]})), aktion
    st = run(ADMIN.admin_mfa_status(admin=SA))
    assert st["wechsel_offen"] is False and st["wechsel_bis"] is None
    assert st["wiederherstellungscodes_uebrig"] == 8 and st["geraet_gewechselt_am"]


def test_rp556_wechsel_nur_mit_aktuellem_app_code(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    # nicht aktiv -> 409 (auch bei halb fertiger Einrichtung)
    run(db.users.insert_one({**SA, "mfa": {"pending_secret": MFA.verschluesseln("X"),
                                          "pending_seit": deps.now_iso()}}))
    with pytest.raises(HTTPException) as e:
        _wechsel(run, "123456")
    assert e.value.status_code == 409 and "nicht aktiv" in e.value.detail
    run(db.users.delete_one({"id": SA["id"]}))
    alt, alt_codes, m_alt = _aktiver_admin(run, db)
    # falscher Code: 400 (nie 401), Fehlversuch, KEIN Pending
    with pytest.raises(HTTPException) as e:
        _wechsel(run, _falscher_code(alt))
    assert e.value.status_code == 400 and e.value.detail == "Code ungültig"
    m = _mfa(run, db)
    assert m["fehlversuche"] == 1 and "pending_secret" not in m
    # Notfall-Code gilt hier nicht
    with pytest.raises(HTTPException) as e:
        _wechsel(run, alt_codes[0])
    assert e.value.status_code == 400 and "6-stelligen Code" in e.value.detail
    assert "pending_secret" not in _mfa(run, db)
    # richtiger Code, dann derselbe noch einmal: Replay -> 400, Pending bleibt das erste
    code = MFA.totp(alt)
    erstes = _wechsel(run, code)["secret"]
    with pytest.raises(HTTPException) as e:
        _wechsel(run, code)
    assert e.value.status_code == 400 and "schon verwendet" in e.value.detail
    m = _mfa(run, db)
    assert MFA.entschluesseln(m["pending_secret"]) == erstes and m["fehlversuche"] == 0
    # ein weiterer Aufruf mit dem NAECHSTEN Code ersetzt das Pending (der zuletzt
    # angezeigte Schluessel ist der einzige, der gilt); der aktive bleibt
    zweites = _wechsel(run, MFA.totp(alt, _jetzt_z() + 1))["secret"]
    m = _mfa(run, db)
    assert zweites != erstes and MFA.entschluesseln(m["pending_secret"]) == zweites
    assert m["secret"] == m_alt["secret"] and m["aktiv"] is True
    with pytest.raises(HTTPException) as e:
        _aktivieren(run, MFA.totp(erstes))
    assert e.value.status_code == 400, "der verworfene erste Schluessel gilt nicht"
    # Quelltext: dieselbe Code-Pruefung wie Abschalten/Notfall-Codes, Bremse beim Bestaetigen
    assert "await _mfa_app_code_bestaetigen(admin[\"id\"], m, body.code)" in inspect.getsource(
        ADMIN.admin_mfa_wechsel)
    q = inspect.getsource(ADMIN.admin_mfa_aktivieren)
    assert "_admin_mfa_limiter.check" in q and '"mfa.secret": m.get("secret")' in q
    assert "HTTPException(401" not in inspect.getsource(ADMIN.admin_mfa_wechsel)


def test_rp556_wechsel_verfaellt_nach_15_minuten(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    assert ADMIN.MFA_WECHSEL_MAX_S == 900
    pend = MFA.secret_erzeugen()
    alt, _, m_alt = _aktiver_admin(run, db, pending_secret=MFA.verschluesseln(pend),
                                   pending_seit=_iso(minutes=-16), pending_wechsel=True)
    st = run(ADMIN.admin_mfa_status(admin=SA))
    assert st["wechsel_offen"] is False and st["wechsel_bis"] is None
    with pytest.raises(HTTPException) as e:
        _aktivieren(run, MFA.totp(pend))
    assert e.value.status_code == 410 and "Gerät wechseln" in e.value.detail
    m = _mfa(run, db)
    assert m["aktiv"] is True and m["secret"] == m_alt["secret"], "altes Geheimnis bleibt"
    assert "pending_secret" not in m and "pending_wechsel" not in m
    # noch gueltig (10 Minuten alt): Status nennt das Ende der Frist
    run(db.users.update_one({"id": SA["id"]}, {"$set": {
        "mfa.pending_secret": MFA.verschluesseln(pend), "mfa.pending_seit": _iso(minutes=-10),
        "mfa.pending_wechsel": True}}))
    st = run(ADMIN.admin_mfa_status(admin=SA))
    assert st["wechsel_offen"] is True
    assert _iso(minutes=4) < st["wechsel_bis"] < _iso(minutes=6), st["wechsel_bis"]
    assert _aktivieren(run, MFA.totp(pend))["geraet_gewechselt"] is True


def test_rp556_ohne_wechsel_bleibt_aktivieren_409_und_abschalten_verwirft_pending(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    alt, _, m_alt = _aktiver_admin(run, db)
    with pytest.raises(HTTPException) as e:
        _aktivieren(run, MFA.totp(alt))
    assert e.value.status_code == 409 and "bereits aktiv" in e.value.detail
    # Altbestand: pending_secret OHNE Wechsel-Kennzeichen ersetzt nichts
    fremd = MFA.secret_erzeugen()
    run(db.users.update_one({"id": SA["id"]}, {"$set": {
        "mfa.pending_secret": MFA.verschluesseln(fremd), "mfa.pending_seit": deps.now_iso()}}))
    with pytest.raises(HTTPException) as e:
        _aktivieren(run, MFA.totp(fremd))
    assert e.value.status_code == 409
    assert _mfa(run, db)["secret"] == m_alt["secret"]
    # /einrichten bei aktiver MFA nennt den neuen Weg (und den alten)
    with pytest.raises(HTTPException) as e:
        run(ADMIN.admin_mfa_einrichten(admin=SA))
    assert e.value.status_code == 409
    assert "Gerät wechseln" in e.value.detail and "abschalten" in e.value.detail
    # Wechsel beginnen, dann abschalten: das Pending ist weg, /aktivieren findet nichts
    neu = _wechsel(run, MFA.totp(alt))["secret"]
    run(ADMIN.admin_mfa_deaktivieren(ADMIN.MfaCodeIn(code=MFA.totp(alt, _jetzt_z() + 1)),
                                     admin=SA))
    m = _mfa(run, db)
    assert m["aktiv"] is False and "pending_secret" not in m and m["pflicht_ausgesetzt_bis"]
    with pytest.raises(HTTPException) as e:
        _aktivieren(run, MFA.totp(neu))
    assert e.value.status_code == 400 and "Zuerst einrichten" in e.value.detail


def test_rp556_wechsel_und_bestaetigung_sind_compare_and_set(wegwerf, monkeypatch):
    db, run = wegwerf.db, wegwerf.run
    alt, _, _ = _aktiver_admin(run, db)
    echt = ADMIN._mfa_app_code_bestaetigen

    async def _dazwischen(admin_id, m, code):
        z = await echt(admin_id, m, code)
        await db.users.update_one({"id": admin_id},
                                  {"$set": {"mfa.secret": MFA.verschluesseln("ANDERES")}})
        return z
    monkeypatch.setattr(ADMIN, "_mfa_app_code_bestaetigen", _dazwischen)
    with pytest.raises(HTTPException) as e:
        _wechsel(run, MFA.totp(alt))
    assert e.value.status_code == 409
    assert "pending_secret" not in _mfa(run, db)
    monkeypatch.setattr(ADMIN, "_mfa_app_code_bestaetigen", echt)
    # Bestaetigung: aendert sich das AKTIVE Geheimnis zwischen Lesen und
    # Schreiben (paralleles Abschalten + Neu-Einrichten), wird nichts ersetzt
    run(db.users.update_one({"id": SA["id"]}, {"$set": {"mfa.secret": MFA.verschluesseln(alt)}}))
    neu = _wechsel(run, MFA.totp(alt, _jetzt_z() + 1))["secret"]
    echt_codes = MFA.wiederherstellungscodes
    from pymongo import MongoClient
    sync = MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)

    def _codes_dazwischen(anzahl=8):
        # laeuft synchron, waehrend die Schleife in /aktivieren steht: eigener
        # Sync-Client fuer den Schreibzugriff "dazwischen"
        sync[wegwerf.name].users.update_one(
            {"id": SA["id"]}, {"$set": {"mfa.secret": MFA.verschluesseln("DRITTES")}})
        return echt_codes(anzahl)
    monkeypatch.setattr(MFA, "wiederherstellungscodes", _codes_dazwischen)
    try:
        with pytest.raises(HTTPException) as e:
            _aktivieren(run, MFA.totp(neu))
    finally:
        sync.close()
    assert e.value.status_code == 409
    m = _mfa(run, db)
    assert MFA.entschluesseln(m["secret"]) == "DRITTES" and m["pending_wechsel"] is True
    assert run(db.zugangs_aenderungen.count_documents({})) == 0


# ============================================================ RP-543 Erstinstallation
def _skript():
    spec = importlib.util.spec_from_file_location(
        "mfa_pruefen_b3", BACKEND / "scripts" / "mfa_pruefen.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_rp543_erstinstallation_seed_anmeldung_einrichtung(wegwerf, monkeypatch):
    """Produktion, frische Datenbank: Seed -> Anmeldung mit Passwort in der
    Frist -> Zwei-Faktor einrichten und aktivieren -> Anmeldung mit Code.
    Frist verstrichen ohne Einrichtung -> 403 mit dem Notweg, der die Frist
    neu setzt."""
    db, run = wegwerf.db, wegwerf.run
    name, pw = "b3-erster-betreiber", "Kq4Lm9Xw2-Sicher!"
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("MFA_PFLICHT", raising=False)
    monkeypatch.delenv("SEED_MFA_FRIST_MIN", raising=False)
    monkeypatch.setenv("SUPER_ADMIN_USERNAME", name)
    monkeypatch.setenv("SUPER_ADMIN_PASSWORD", pw)
    assert AUTH.mfa_pflicht_aktiv() is True
    run(SRV.seed_super_admin())
    sa = run(db.users.find_one({"username": name}))
    assert sa and sa["is_super_admin"] and sa["role"] == "admin" and sa["active"]
    assert sa["mfa"]["aktiv"] is False
    assert _iso(minutes=50) < sa["mfa"]["pflicht_ausgesetzt_bis"] < _iso(minutes=70)
    assert run(db.dealers.count_documents({"id": sa["dealer_id"]})) == 1
    assert run(db.subscriptions.count_documents({"dealer_id": sa["dealer_id"],
                                                 "status": "active"})) == 1
    # 1) Anmeldung in der Frist: Benutzername + Passwort reichen
    erg = run(AUTH.login(AUTH.LoginIn(kontonummer=name, password=pw), _request()))
    assert erg["token"] and erg["user"]["id"] == sa["id"]
    assert erg["user"]["mfa_aktiv"] is False and "mfa" not in erg["user"]
    assert "password_hash" not in erg["user"]
    # 2) Einrichten + Aktivieren aus dieser Sitzung
    admin = run(db.users.find_one({"id": sa["id"]}, {"_id": 0}))
    setup = run(ADMIN.admin_mfa_einrichten(admin=admin))
    secret = setup["secret"]
    assert setup["otpauth_uri"].startswith("otpauth://totp/") and name in setup["otpauth_uri"]
    akt = _aktivieren(run, MFA.totp(secret), admin=admin)
    assert len(akt["wiederherstellungscodes"]) == 8 and akt["token"]
    m = run(db.users.find_one({"id": sa["id"]}))["mfa"]
    assert m["aktiv"] is True and "pflicht_ausgesetzt_bis" not in m
    # 3) Anmeldung verlangt jetzt den zweiten Faktor
    erg2 = run(AUTH.login(AUTH.LoginIn(kontonummer=name, password=pw), _request()))
    assert erg2["mfa_erforderlich"] is True and erg2["mfa_token"] and "token" not in erg2
    erg3 = run(AUTH.login_mfa(AUTH.MfaLoginIn(mfa_token=erg2["mfa_token"],
                                              code=MFA.totp(secret, _jetzt_z() + 1)),
                              _request()))
    assert erg3["token"] and erg3["user"]["mfa_aktiv"] is True
    # 4) Frist verstrichen, nichts eingerichtet: 403 nennt den Notweg
    run(db.users.update_one({"id": sa["id"]}, {"$set": {
        "mfa": {"aktiv": False, "pflicht_ausgesetzt_bis": _iso(minutes=-1)}}}))
    with pytest.raises(HTTPException) as e:
        run(AUTH.login(AUTH.LoginIn(kontonummer=name, password=pw), _request()))
    assert e.value.status_code == 403 and e.value.detail == AUTH.MFA_PFLICHT_HINWEIS
    assert "--konto <Benutzername> --abschalten --ja" in e.value.detail
    assert "30 Minuten" in e.value.detail and "Einstellungen" in e.value.detail
    assert run(db.activity_logs.count_documents({"action": "auth.login.mfa_fehlt"})) == 1
    # 5) der genannte Notweg setzt die Frist — Anmeldung klappt wieder
    monkeypatch.setenv("MONGO_URL", MONGO_URL)
    monkeypatch.setenv("DB_NAME", wegwerf.name)
    monkeypatch.setattr(sys, "argv", ["mfa_pruefen.py", "--konto", name, "--abschalten", "--ja"])
    assert _skript().main() == 0
    m = run(db.users.find_one({"id": sa["id"]}))["mfa"]
    assert m["aktiv"] is False and _iso(minutes=29) < m["pflicht_ausgesetzt_bis"] < _iso(minutes=31)
    erg4 = run(AUTH.login(AUTH.LoginIn(kontonummer=name, password=pw), _request()))
    assert erg4["token"]
    # ein zweiter Start laesst das Konto (und die Frist) in Ruhe
    run(SRV.seed_super_admin())
    assert run(db.users.count_documents({"is_super_admin": True})) == 1
    assert run(db.users.find_one({"id": sa["id"]}))["mfa"] == m


@pytest.mark.quelltext
def test_rp543_rp556_doku_oberflaeche_und_kompose_passen_zum_code():
    doku = (WURZEL / "DEPLOYMENT.md").read_text(encoding="utf-8")
    # Erste Anmeldung: Frist des Seeds und der Notweg stehen im Runbook
    erste = doku[doku.index("Erste Anmeldung mit `SUPER_ADMIN_USERNAME`"):]
    erste = erste[:erste.index("### Stufe 2")]
    assert "SEED_MFA_FRIST_MIN" in erste and "60 Minuten" in erste
    assert "--abschalten --ja" in erste and "30 Minuten" in erste
    seed = inspect.getsource(SRV.seed_super_admin)
    assert 'zahl_env("SEED_MFA_FRIST_MIN", 60' in seed and "pflicht_ausgesetzt_bis" in seed
    assert "SEED_MFA_FRIST_MIN" in (WURZEL / "docker-compose.yml").read_text(encoding="utf-8")
    # Geraetewechsel: Runbook beschreibt den neuen Weg, der alte bleibt
    zf = doku[doku.index("## Zwei-Faktor-Anmeldung für Admins (TOTP)"):]
    zf = zf[:zf.index("\n## ", 5)]
    assert "Gerät wechseln" in zf and "Neues Gerät bestätigen" in zf and "15 Minuten" in zf
    assert "mfa_pruefen.py --abschalten --ja" in zf
    r15 = doku[doku.index("**Zwei-Faktor wird nicht aus einer laufenden Sitzung ersetzt.**"):]
    r15 = r15[:r15.index("\n- **")]
    assert "/admin/me/mfa/wechsel" in r15 and "pending_wechsel" in r15
    assert "abschalten" in r15 and "30 Minuten" in r15
    # Oberflaeche: neuer Knopf, Abschalten bleibt, Kennungen unveraendert
    q = (WURZEL / "frontend" / "src" / "pages" / "admin_v2" / "Settings.jsx").read_text(
        encoding="utf-8")
    assert 'data-testid="mfa-geraet-wechseln"' in q and '"/admin/me/mfa/wechsel"' in q
    assert 'data-testid="mfa-deaktivieren"' in q and '"/admin/me/mfa/deaktivieren"' in q
    assert 'data-testid="mfa-aktivieren"' in q and 'data-testid="mfa-wechsel-offen"' in q
    assert "Gerät wechseln" in q
