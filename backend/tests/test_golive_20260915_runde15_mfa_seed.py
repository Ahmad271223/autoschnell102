# -*- coding: utf-8 -*-
"""Runde 15 (15.09.2026): Reviewer-Liste "MFA, Super-Admin-Seed, Konto-Limiter,
Chef-Anlage, Rollenwechsel, Kundennummer, Konto-Diagnose, 422-Redigierung".

Wegwerf-Datenbank je Test (echtes Mongo), keine HTTP-Aufrufe.
- aktive MFA wird nicht aus der Sitzung heraus ersetzt; Aktivieren mit CAS,
  Ablauf und neuer Sitzung; Abschalten und MFA-Login mit Geheimnis-CAS
- Seed: kein fremdes Konto hochstufen, kein zweiter Betreiber, Passwortregel,
  abweichendes .env-Passwort gemeldet
- Konto-Limiter liest gleitend und ist fail-closed
- Chef-Anlage laesst keinen Chef ohne Firma zurueck; Kundennummer null/Text
  wird repariert; Kaeufer -> Sucher bekommt eine Sucher-Nummer
- 422 spiegelt keine Passwoerter; Sucher-Login prueft die Firmensperre
"""
import asyncio
import inspect
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tests"))

import deps  # noqa: E402
import kontenanlage as KA  # noqa: E402
import mfa as MFA  # noqa: E402
import rate_limiter as RL  # noqa: E402
import routes.admin as ADMIN  # noqa: E402
import routes.auth as AUTH  # noqa: E402
import server as SRV  # noqa: E402

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
SA = {"id": "sa", "role": "admin", "is_super_admin": True, "active": True, "username": "betreiber",
      "current_session_id": "sid-alt"}


def _jetzt(delta_s: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=delta_s)).isoformat()


@pytest.fixture
def wegwerf(monkeypatch):
    from motor.motor_asyncio import AsyncIOMotorClient
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    name = f"autoschnell_r15_{uuid.uuid4().hex[:10]}"
    db = client[name]
    for mod in (deps, ADMIN, AUTH, SRV):
        monkeypatch.setattr(mod, "db", db)
    monkeypatch.delenv("APP_ENV", raising=False)
    try:
        yield SimpleNamespace(db=db, run=loop.run_until_complete)
    finally:
        try:
            loop.run_until_complete(client.drop_database(name))
        finally:
            client.close()
            loop.close()


# ============================================================ MFA
def test_mfa_aktiv_wird_nicht_aus_der_sitzung_ersetzt(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    run(db.users.insert_one({**SA, "mfa": {"aktiv": True, "secret": MFA.verschluesseln("ABC"),
                                          "letzter_zaehler": 1}}))
    with pytest.raises(HTTPException) as e:
        run(ADMIN.admin_mfa_einrichten(admin=SA))
    assert e.value.status_code == 409 and "abschalten" in e.value.detail
    # Abschalten und MFA-Login binden sich an GENAU das geprueft Geheimnis
    assert '"mfa.secret": m.get("secret")' in inspect.getsource(ADMIN.admin_mfa_deaktivieren)
    q = inspect.getsource(AUTH.login_mfa)
    assert q.count('"mfa.aktiv": True, "mfa.secret": m.get("secret")') == 2


def test_mfa_aktivieren_ablauf_cas_und_neue_sitzung(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    secret = MFA.secret_erzeugen()
    run(db.users.insert_one({**SA, "mfa": {"pending_secret": MFA.verschluesseln(secret),
                                          "pending_seit": _jetzt(-2 * 3600)}}))
    with pytest.raises(HTTPException) as e:
        run(ADMIN.admin_mfa_aktivieren(ADMIN.MfaCodeIn(code=MFA.totp(secret)), admin=SA))
    assert e.value.status_code == 410, "abgelaufene Einrichtung"
    run(db.users.update_one({"id": "sa"}, {"$set": {"mfa.pending_seit": _jetzt()}}))
    erg = run(ADMIN.admin_mfa_aktivieren(ADMIN.MfaCodeIn(code=MFA.totp(secret)), admin=SA))
    assert erg["aktiv"] and erg["token"] and len(erg["wiederherstellungscodes"]) == 8
    u = run(db.users.find_one({"id": "sa"}))
    assert u["mfa"]["aktiv"] is True and u["current_session_id"] != "sid-alt", \
        "die Sitzung ohne zweiten Faktor endet, dieser Aufruf bekommt eine neue"
    # zweite Aktivierung (paralleler Tab mit demselben Pending-Geheimnis): 409
    with pytest.raises(HTTPException) as e:
        run(ADMIN.admin_mfa_aktivieren(ADMIN.MfaCodeIn(code=MFA.totp(secret)), admin=SA))
    assert e.value.status_code == 409
    # Aktivieren-Filter traegt das Pending-Geheimnis (CAS)
    assert '"mfa.pending_secret": m.get("pending_secret")' in inspect.getsource(ADMIN.admin_mfa_aktivieren)


def test_mfa_zweitschluessel(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "jwt-alt-jwt-alt-jwt-alt-jwt-alt-jwt")
    monkeypatch.delenv("DATEN_SCHLUESSEL", raising=False)
    alt = MFA.verschluesseln("GEHEIM")
    monkeypatch.setenv("DATEN_SCHLUESSEL", "daten-schluessel-neu-daten-schluessel")
    assert MFA.entschluesseln(alt) == "GEHEIM", "mit JWT_SECRET verschluesselt bleibt lesbar"
    neu = MFA.verschluesseln("GEHEIM2")
    assert MFA.entschluesseln(neu) == "GEHEIM2" and neu != alt
    monkeypatch.setenv("JWT_SECRET", "jwt-rotiert-jwt-rotiert-jwt-rotiert")
    assert MFA.entschluesseln(neu) == "GEHEIM2", "JWT-Rotation macht MFA nicht unlesbar"


# ============================================================ Seed
def _seed_env(monkeypatch, username="betreiber-neu", password="Kq4Lm9Xw2-Sicher!"):
    monkeypatch.setenv("SUPER_ADMIN_USERNAME", username)
    monkeypatch.setenv("SUPER_ADMIN_PASSWORD", password)


def test_seed_stuft_kein_fremdes_konto_hoch(wegwerf, monkeypatch):
    db, run = wegwerf.db, wegwerf.run
    _seed_env(monkeypatch)
    run(db.users.insert_one({"id": "fremd", "username": "betreiber-neu", "role": "dealer",
                             "dealer_id": "d1", "active": True, "password_hash": "h"}))
    run(SRV.seed_super_admin())
    u = run(db.users.find_one({"id": "fremd"}))
    assert u["role"] == "dealer" and not u.get("is_super_admin")
    assert run(db.betriebsalarme.find_one({"typ": "super_admin_seed_konflikt", "offen": True}))
    assert run(db.users.count_documents({"is_super_admin": True})) == 0


def test_seed_legt_keinen_zweiten_betreiber_an(wegwerf, monkeypatch):
    db, run = wegwerf.db, wegwerf.run
    run(db.users.insert_one({"id": "sa1", "username": "betreiber-alt", "role": "admin",
                             "is_super_admin": True, "active": True, "password_hash": "h"}))
    _seed_env(monkeypatch, username="betreiber-neu")
    run(SRV.seed_super_admin())
    assert run(db.users.count_documents({"is_super_admin": True})) == 1, "kein zweiter Super-Admin"
    assert run(db.betriebsalarme.find_one({"typ": "super_admin_doppelt", "offen": True}))


def test_seed_passwortregel_und_env_abweichung(wegwerf, monkeypatch):
    db, run = wegwerf.db, wegwerf.run
    _seed_env(monkeypatch, password="5837294615")            # nur Ziffern: verboten
    run(SRV.seed_super_admin())
    assert run(db.users.count_documents({"is_super_admin": True})) == 0
    _seed_env(monkeypatch, password="Kq4Lm9Xw2-Sicher!")
    run(SRV.seed_super_admin())
    sa = run(db.users.find_one({"username": "betreiber-neu"}))
    assert sa and sa["is_super_admin"] and sa["active"] is True
    # .env-Passwort geaendert: bleibt das gespeicherte, aber gemeldet
    _seed_env(monkeypatch, password="Anderes-Passwort-2026!")
    run(SRV.seed_super_admin())
    assert run(db.betriebsalarme.find_one({"typ": "super_admin_passwort_env_abweichend", "offen": True}))
    from auth import verify_password
    assert verify_password("Kq4Lm9Xw2-Sicher!", run(db.users.find_one({"username": "betreiber-neu"}))["password_hash"])
    assert "aktive_sa > 1" in (BACKEND / "server.py").read_text(encoding="utf-8")


# ============================================================ Limiter
def test_konto_limiter_gleitend_und_fail_closed(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    if not RL._RATE_LIMIT_ENABLED:
        pytest.skip("RATE_LIMIT_ENABLED=false")
    import time
    lim = RL.login_konto_limiter
    assert lim.fail_closed and RL.login_limiter.fail_closed and RL.driver_login_limiter.fail_closed
    fenster = int(time.time() // lim.window_seconds)
    run(db.rate_limits.insert_many([
        {"_id": f"{lim.name}:k1:{fenster}", "n": 5},
        {"_id": f"{lim.name}:k1:{fenster - 1}", "n": 20}]))
    stand = run(lim.stand("k1"))
    assert 5 <= stand <= 25, stand
    assert stand > 5 or (time.time() % lim.window_seconds) > lim.window_seconds - 2, \
        "das vorige Fenster zaehlt anteilig mit"
    run(lim.reset("k1"))
    assert run(lim.stand("k1")) == 0


# ============================================================ Chef-Anlage, Kundennummer, Rollenwechsel
def test_chef_anlage_laesst_keinen_chef_ohne_firma(wegwerf, monkeypatch):
    db, run = wegwerf.db, wegwerf.run
    orig = KA.firma_einfuegen

    async def firma_verschwindet(db_, doc, nummer_ziehen=None):
        nr = await orig(db_, doc, nummer_ziehen)
        await db_.dealers.delete_one({"id": doc["id"]})       # Firma weg vor dem user_id-Write
        return nr
    monkeypatch.setattr(KA, "firma_einfuegen", firma_verschwindet)
    with pytest.raises(RuntimeError):
        run(KA.firma_mit_chef_anlegen(db, {"id": "d1", "company_name": "X", "created_at": _jetzt()},
                                      {"id": "chef1", "password_hash": "h", "active": True}))
    assert run(db.users.count_documents({"id": "chef1"})) == 0, "kein Chef ohne Firma"
    assert run(db.dealers.count_documents({"id": "d1"})) == 0


def test_kundennummer_null_oder_text_wird_repariert(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    run(db.dealers.insert_many([
        {"id": "dn", "company_name": "Null", "kunden_nr": None, "created_at": _jetzt()},
        {"id": "dt", "company_name": "Text", "kunden_nr": "1005", "created_at": _jetzt()},
        {"id": "df", "company_name": "Float", "kunden_nr": 1007.0, "created_at": _jetzt()},
    ]))
    n_null = run(KA.kunden_nr_sicherstellen(db, "dn"))["kunden_nr"]
    n_text = run(KA.kunden_nr_sicherstellen(db, "dt"))["kunden_nr"]
    n_float = run(KA.kunden_nr_sicherstellen(db, "df"))["kunden_nr"]
    assert isinstance(n_null, int) and isinstance(n_text, int) and n_null != n_text
    assert n_float == 1007 and isinstance(n_float, int), "Gleitkommazahl: nur der Typ wird repariert"
    assert run(db.dealers.find_one({"id": "df"}))["kunden_nr"] == 1007


def test_kaeufer_zu_sucher_bekommt_suchernummer(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    run(db.users.insert_one({"id": "sa", **SA}))
    run(db.dealers.insert_one({"id": "d1", "company_name": "Firma", "kunden_nr": 1005, "user_id": "chef",
                               "created_at": _jetzt()}))
    run(db.users.insert_many([
        {"id": "chef", "role": "dealer", "dealer_id": "d1", "active": True, "kontonummer": "1005"},
        {"id": "kb", "role": "b2b_buyer", "dealer_id": None, "ehemalige_dealer_id": "d1", "active": True,
         "kontonummer": "6FE7K2M", "kontonummer_art": "kaeufer_code", "company_name": "Kaeufer"},
    ]))
    run(ADMIN.admin_update_user("kb", body={"role": "sucher"}, admin=SA))
    u = run(db.users.find_one({"id": "kb"}))
    assert u["role"] == "sucher" and u["dealer_id"] == "d1"
    assert u["kontonummer"] == "1005-1" and u["kontonummer_basis"] == 1005
    assert u["kontonummer_vorher"] == "6FE7K2M" and u["kontonummer_art"] == "sucher"
    # die Anmeldung findet ihn jetzt ueber die Nummer
    assert run(AUTH._konto_fuer_login("1005-1"))["id"] == "kb"


# ============================================================ Diagnose, 422, Firmensperre
def test_diagnose_422_und_firmensperre():
    q = inspect.getsource(ADMIN.admin_konto_pruefen)
    assert 'doc.get("active", fahrer is not None)' in q, "Diagnose = Anmeldesemantik (users: fehlend = gesperrt)"
    assert SRV._geheim_redigieren(("body", "password"), "Geheim123!") == "***"
    assert SRV._geheim_redigieren(("body", "new_password"), {"x": 1}) == "***"
    assert SRV._geheim_redigieren(("body",), {"password": "a", "email": "e"}) == {"password": "***", "email": "e"}
    assert SRV._geheim_redigieren(("body", "email"), "e@x.de") == "e@x.de"
    # Pruefbericht 20.09.2026 (B2): die Pruefung steht jetzt in EINER Funktion,
    # die Passwort- und Zwei-Faktor-Anmeldung beide aufrufen.
    assert "_firmensperre_pruefen(user)" in inspect.getsource(AUTH.login)
    assert "_firmensperre_pruefen(user)" in inspect.getsource(AUTH.login_mfa)
    assert "firma_gesperrt(user[\"dealer_id\"])" in inspect.getsource(AUTH._firmensperre_pruefen)
    assert ADMIN.AdminSelfPasswordIn.model_fields["current_password"].metadata
