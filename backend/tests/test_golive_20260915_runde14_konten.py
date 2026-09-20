# -*- coding: utf-8 -*-
"""Runde 14 (15.09.2026): Reviewer-Listen "Anmeldung, Konto-/Registrierungs-
anlage, Super-Admin-Freigabe, Passwortverwaltung" (6+4, 14, 5, 8 Punkte) und
"Fahrer-Datenschutz/Recovery" (10 + 5 Punkte).

Wegwerf-Datenbank je Test (echtes Mongo), keine HTTP-Aufrufe. Die Regeln:
- Sitzung nur per Compare-and-set auf den geprueften Kontozustand
- Logout beendet nur die eigene Sitzung; Entsperren verwirft die Sitzung
- Passwoerter nur ueber die eigenen Endpunkte, mit Buchstabenpflicht und
  ohne eigene Kontodaten, nie in laufender Loeschung
- Zugangs-Anfrage: eine je E-Mail und Art; Freigabe an die Anfragedaten gebunden
- Erstbericht-Reservierung verfaellt; Freigabe-Recovery verhungert nicht;
  Auto-Termin nie auf einen verschwindenden Vertrag; Fahrer-Pseudonym nachgeholt
- Fahrer sieht Verkaeuferdaten erst nach der Annahme; Sucher ohne Fahrer-Kontaktdaten
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
import cleanup_service as CS  # noqa: E402
import migrationen as MIG  # noqa: E402
import passwoerter as PWR  # noqa: E402
import rate_limiter as RL  # noqa: E402
import routes.admin as ADMIN  # noqa: E402
import routes.appointments as A  # noqa: E402
import routes.auth as AUTH  # noqa: E402
import routes.contracts as C  # noqa: E402
import routes.drivers as DRV  # noqa: E402
import routes.protocols as P  # noqa: E402

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
SA = {"id": "sa", "role": "admin", "is_super_admin": True, "active": True, "username": "betreiber"}
PW = "Sicher-Wie-Nie-77"


def _jetzt(delta_s: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=delta_s)).isoformat()


@pytest.fixture
def wegwerf(monkeypatch):
    from motor.motor_asyncio import AsyncIOMotorClient
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    name = f"autoschnell_r14_{uuid.uuid4().hex[:10]}"
    db = client[name]
    for mod in (deps, ADMIN, A, AUTH, C, DRV, P):
        monkeypatch.setattr(mod, "db", db)
    try:
        yield SimpleNamespace(db=db, run=loop.run_until_complete)
    finally:
        try:
            loop.run_until_complete(client.drop_database(name))
        finally:
            client.close()
            loop.close()


# ============================================================ Sitzung (CAS)
def test_sitzung_entsteht_nur_bei_unveraendertem_konto(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    user = {"id": "u1", "role": "dealer", "dealer_id": "d1", "active": True,
            "password_hash": "hash-alt", "kontonummer": "10023", "created_at": _jetzt()}
    run(db.users.insert_one(dict(user)))
    erg = run(AUTH._sitzung_ausstellen(user, "203.0.113.5", "Chrome auf Windows"))
    assert erg["token"] and run(db.users.find_one({"id": "u1"}))["current_session_id"] == erg["user"]["current_session_id"]
    # Passwort-Reset zwischen Pruefung und Sitzungs-Schreiben: keine Sitzung
    run(db.users.update_one({"id": "u1"}, {"$set": {"password_hash": "hash-neu", "current_session_id": None}}))
    with pytest.raises(HTTPException) as e:
        run(AUTH._sitzung_ausstellen(user, "203.0.113.5"))
    assert e.value.status_code == 401
    assert run(db.users.find_one({"id": "u1"}))["current_session_id"] is None, "Reset ueberholt"
    # Sperre zwischen Pruefung und Schreiben
    frisch = run(db.users.find_one({"id": "u1"}))
    run(db.users.update_one({"id": "u1"}, {"$set": {"active": False}}))
    with pytest.raises(HTTPException):
        run(AUTH._sitzung_ausstellen(frisch, "203.0.113.5"))
    # Loeschung laeuft
    run(db.users.update_one({"id": "u1"}, {"$set": {"active": True, "loeschung": {"status": "laeuft"}}}))
    with pytest.raises(HTTPException):
        run(AUTH._sitzung_ausstellen(frisch, "203.0.113.5"))
    # MFA-Zustand gehoert zur Bedingung (MFA-Race)
    bed = AUTH.sitzungs_bedingung({"id": "x", "password_hash": "h", "mfa": {"aktiv": True, "secret": "s"}})
    assert bed["mfa.aktiv"] is True and bed["mfa.secret"] == "s"
    assert AUTH.sitzungs_bedingung({"id": "x", "password_hash": "h"})["mfa.aktiv"] == {"$ne": True}
    # Fahrer-Login und MFA-Abschluss nutzen dieselbe Regel
    q = inspect.getsource(DRV.driver_login)
    assert '"password_hash": da.get("password_hash")' in q and '"loeschung.status": {"$ne": "laeuft"}' in q
    assert 'log_activity_sicher("", da["id"], "auth.login"' in q, "Fahrer-Login-Audit"
    assert "login_limiter.reset(login_schluessel(" in inspect.getsource(AUTH.login_mfa)
    assert "if not mfa_aktiv:\n        await login_limiter.reset(schluessel)" in inspect.getsource(AUTH.login)
    assert "mfa_pflicht_aktiv()" in inspect.getsource(AUTH.login)
    assert "pflicht_ausgesetzt_bis" in inspect.getsource(AUTH.login), "Gnadenfrist nach Notfall-Abschalten"
    assert "pflicht_ausgesetzt_bis" in (BACKEND / "scripts" / "mfa_pruefen.py").read_text(encoding="utf-8")


def test_logout_beendet_nur_die_eigene_sitzung(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    run(db.users.insert_one({"id": "u2", "role": "dealer", "dealer_id": "d1", "active": True,
                             "current_session_id": "neuere-sitzung"}))
    # der alte Logout (Token-SID "alte-sitzung") laeuft NACH der neueren Anmeldung
    run(AUTH.logout(user={"id": "u2", "dealer_id": "d1", "current_session_id": "alte-sitzung"}))
    assert run(db.users.find_one({"id": "u2"}))["current_session_id"] == "neuere-sitzung"
    run(AUTH.logout(user={"id": "u2", "dealer_id": "d1", "current_session_id": "neuere-sitzung"}))
    assert run(db.users.find_one({"id": "u2"}))["current_session_id"] is None
    run(db.driver_accounts.insert_one({"id": "f2", "active": True, "current_session_id": "b"}))
    run(DRV.driver_logout(driver={"id": "f2", "current_session_id": "a"}))
    assert run(db.driver_accounts.find_one({"id": "f2"}))["current_session_id"] == "b"


def test_entsperren_verwirft_waehrend_der_sperre_entstandene_sitzung(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    run(db.users.insert_one({"id": "u3", "role": "sucher", "dealer_id": "d1", "active": False,
                             "kontonummer": "10023-1", "current_session_id": "waehrend-sperre"}))
    erg = run(ADMIN.admin_user_set_active("u3", ADMIN.AdminActiveIn(active=True), admin=SA))
    u = run(db.users.find_one({"id": "u3"}))
    assert erg["ok"] and u["active"] is True and u["current_session_id"] is None
    # aktives Konto erneut aktiv setzen: laufende Sitzung bleibt
    run(db.users.update_one({"id": "u3"}, {"$set": {"current_session_id": "laeuft"}}))
    run(ADMIN.admin_user_set_active("u3", ADMIN.AdminActiveIn(active=True), admin=SA))
    assert run(db.users.find_one({"id": "u3"}))["current_session_id"] == "laeuft"
    run(db.driver_accounts.insert_one({"id": "f3", "active": False, "kontonummer": "FD-AAAA1111",
                                       "current_session_id": "waehrend-sperre"}))
    run(ADMIN.admin_driver_set_active("f3", ADMIN.AdminActiveIn(active=True), admin=SA))
    assert run(db.driver_accounts.find_one({"id": "f3"}))["current_session_id"] is None


# ============================================================ Passwoerter
def test_passwortwege_und_regeln(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    run(db.users.insert_many([
        {"id": "sa", "role": "admin", "is_super_admin": True, "active": True, "username": "betreiber",
         "password_hash": "h"},
        {"id": "u4", "role": "sucher", "dealer_id": "d1", "active": True, "kontonummer": "10023-2",
         "email": "anna.muster@firma.de", "first_name": "Anna", "last_name": "Muster", "password_hash": "h"},
        {"id": "u5", "role": "sucher", "dealer_id": "d1", "active": True, "kontonummer": "10023-3",
         "password_hash": "h", "loeschung": {"status": "laeuft"}},
    ]))
    # PUT setzt keine Passwoerter mehr (Nr. 1/2/10)
    with pytest.raises(HTTPException) as e:
        run(ADMIN.admin_update_user("u4", body={"password": PW}, admin=SA))
    assert e.value.status_code == 400 and "/admin/me/password" in e.value.detail
    # Super-Admin-Passwort nur ueber /admin/me/password
    with pytest.raises(HTTPException) as e:
        run(ADMIN.admin_user_set_password("sa", ADMIN.AdminUserPasswordIn(new_password=PW), admin=SA))
    assert e.value.status_code == 400
    # Konto in Loeschung: kein Passwort (Nr. 11/12)
    with pytest.raises(HTTPException) as e:
        run(ADMIN.admin_user_set_password("u5", ADMIN.AdminUserPasswordIn(new_password=PW), admin=SA))
    assert e.value.status_code == 409
    run(db.driver_accounts.insert_one({"id": "f5", "active": True, "kontonummer": "FD-BBBB2222",
                                       "loeschung": {"status": "laeuft"}, "password_hash": "h"}))
    with pytest.raises(HTTPException) as e:
        run(ADMIN.admin_driver_set_password("f5", ADMIN.AdminUserPasswordIn(new_password=PW), admin=SA))
    assert e.value.status_code == 409
    # Kontodaten im Passwort (Nr. 9)
    with pytest.raises(HTTPException) as e:
        run(ADMIN.admin_user_set_password("u4", ADMIN.AdminUserPasswordIn(new_password="Muster-2026-x!"), admin=SA))
    assert e.value.status_code == 400 and "Kontodaten" in e.value.detail
    erg = run(ADMIN.admin_user_set_password("u4", ADMIN.AdminUserPasswordIn(new_password=PW), admin=SA))
    assert erg["ok"] and "sperre_aufgehoben" in erg
    assert run(db.users.find_one({"id": "u4"}))["current_session_id"] is None
    # Regeln (Nr. 8): nur Ziffern reicht nicht, Buchstabe + Ziffer/Sonderzeichen
    with pytest.raises(ValueError, match="Buchstaben"):
        PWR.pruefe_passwort("5837294615")
    with pytest.raises(ValueError, match="Buchstaben"):
        PWR.pruefe_passwort("!!22334455$$")
    assert PWR.pruefe_passwort("Sicher-Wie-Nie-77") == "Sicher-Wie-Nie-77"
    with pytest.raises(ValueError, match="Kontodaten"):
        PWR.pruefe_passwort("auto-schnell-2026", persoenlich=["Auto Schnell GmbH"])
    with pytest.raises(ValueError, match="Kontodaten"):
        PWR.pruefe_passwort("xx10023xx!", persoenlich=["10023-2"])
    assert PWR.persoenliche_werte({"kontonummer": "10023", "email": "a@b.de", "phone": "1"}) == ["10023", "a@b.de"]
    # Unklare Sperre wird gemeldet, nicht verschluckt (Nr. 3)
    assert "hinweis" in inspect.getsource(ADMIN._konto_sperre_aufheben)
    assert "konto_sperre_nicht_aufgehoben" in inspect.getsource(ADMIN._konto_sperre_aufheben)
    # bekannte IP entschaerft die Sperre nur (Nr. 6)
    assert RL._BEKANNTE_IP_FAKTOR == 3 and "_BEKANNTE_IP_FAKTOR" in inspect.getsource(RL.konto_gesperrt)
    # Schema-Deckel fuer Login-Passwoerter
    assert AUTH.LoginIn.model_fields["password"].metadata and \
        DRV.DriverAccountLogin.model_fields["password"].metadata
    assert DRV.DriverZuteilungIn.model_fields["grund"].metadata


# ============================================================ Zugangs-Anfrage / Freigabe
def _request(ip="203.0.113.9"):
    return SimpleNamespace(client=SimpleNamespace(host=ip), headers={})


def test_zugangsanfrage_dublette_und_freigabe_bindung(wegwerf, monkeypatch):
    db, run = wegwerf.db, wegwerf.run

    async def _frei(*_a, **_k):
        return True
    monkeypatch.setattr(AUTH.register_limiter, "check", _frei)
    body = AUTH.ZugangsAnfrageIn(art="firma", company_name="Auto Schnell GmbH", contact_person="Anna Chef",
                                 email="Chef@Auto-Schnell.de", gewerblich_bestaetigt=True)
    r1 = run(AUTH.zugang_anfrage(body, _request()))
    r2 = run(AUTH.zugang_anfrage(body, _request("203.0.113.10")))
    assert r1["ok"] and r2["ok"] and r1["hinweis"] == r2["hinweis"]
    assert run(db.plan_requests.count_documents({"type": "zugang"})) == 1, "eine offene Anfrage je E-Mail und Art"
    assert run(db.activity_logs.count_documents({"action": "zugang.anfrage.dublette"})) == 1
    # andere Kontoart derselben Adresse: eigene Anfrage
    run(AUTH.zugang_anfrage(AUTH.ZugangsAnfrageIn(art="fahrer", company_name="Auto Schnell GmbH",
                                                  contact_person="Anna Chef", email="chef@auto-schnell.de",
                                                  gewerblich_bestaetigt=True), _request()))
    assert run(db.plan_requests.count_documents({"type": "zugang"})) == 2
    anfrage = run(db.plan_requests.find_one({"type": "zugang", "art": "firma"}, {"_id": 0}))
    # Freigabe an die Anfragedaten gebunden: fremde Firma/E-Mail -> 409
    with pytest.raises(HTTPException) as e:
        run(ADMIN._anfrage_reservieren(anfrage["id"], "firma",
                                       eingaben={"email": "chef@auto-schnell.de", "company_name": "Firma B"}))
    assert e.value.status_code == 409 and "daten_geaendert" in e.value.detail
    assert run(db.plan_requests.find_one({"id": anfrage["id"]})).get("anlage_marke") is None
    with pytest.raises(HTTPException) as e:
        run(ADMIN._anfrage_reservieren(anfrage["id"], "firma",
                                       eingaben={"email": "andere@firma-b.de", "company_name": "Auto Schnell GmbH"}))
    assert e.value.status_code == 409
    # gleiche Daten (Schreibweise egal) -> reserviert
    res = run(ADMIN._anfrage_reservieren(anfrage["id"], "firma",
                                         eingaben={"email": "CHEF@auto-schnell.de", "company_name": "auto  schnell gmbh"}))
    assert res and res.get("anlage_marke")
    run(ADMIN._anfrage_freigeben(res))
    # bewusst abweichend -> erlaubt
    res = run(ADMIN._anfrage_reservieren(anfrage["id"], "firma",
                                         eingaben={"email": "x@y.de", "company_name": "Firma B", "daten_geaendert": True}))
    assert res.get("anlage_marke")
    # Abschluss: veraenderter Datenstand -> nicht geschlossen, Alarm
    run(db.plan_requests.update_one({"id": anfrage["id"]}, {"$set": {"contact_email": "manipuliert@x.de"}}))
    run(ADMIN._anfrage_abschliessen(res, "konto-1", "10099", SA))
    assert run(db.plan_requests.find_one({"id": anfrage["id"]}))["status"] == "offen"
    assert run(db.betriebsalarme.find_one({"typ": "zugangsanfrage_nicht_geschlossen", "offen": True}))
    assert ADMIN.ANFRAGE_RESERVIERUNG_S == 300
    for m in (ADMIN.AdminUserIn, ADMIN.AdminKaeuferIn, ADMIN.AdminFahrerIn):
        assert "daten_geaendert" in m.model_fields
    assert "zugangsanfrage_eindeutig" in inspect.getsource(sys.modules["indizes"].konto_indizes) \
        if "indizes" in sys.modules else True


# ============================================================ Fahrer: Erstbericht-Lease, Freigabe-Recovery
def test_erstbericht_reservierung_verfaellt(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    run(db.appointments.insert_one({"id": "t1", "driver_id": "f1", "status": "abgeholt"}))
    assert run(DRV._erstbericht_reservieren("t1", "f1")) is not None
    assert run(DRV._erstbericht_reservieren("t1", "f1")) is None, "zweite Reservierung waehrend des Lease"
    alt = (datetime.now(timezone.utc) - timedelta(minutes=DRV.ERSTBERICHT_RESERVIERUNG_MIN + 5)).isoformat()
    run(db.appointments.update_one({"id": "t1"}, {"$set": {"erstbericht_reserviert_at": alt}}))
    assert run(DRV._erstbericht_reservieren("t1", "f1")) is not None, "abgelaufener Lease ist frei"
    assert "abholbericht_pseudonym_offen" in inspect.getsource(DRV.driver_submit_report)


def test_freigabe_recovery_verhungert_nicht(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    run(db.appointments.insert_many(
        [{"id": f"o{i}", "status": "offen"} for i in range(600)] + [{"id": "zu", "status": "storniert"}]))
    run(db.pickup_protocols.insert_many(
        [{"id": f"p{i}", "appointment_id": f"o{i}", "status": P.ZUR_FREIGABE, "version": 1} for i in range(600)]
        + [{"id": "pzu", "appointment_id": "zu", "status": P.ZUR_FREIGABE, "version": 1}]))
    assert run(P.freigaben_geschlossener_termine_zuruecknehmen(db)) == 1
    assert run(db.pickup_protocols.find_one({"id": "pzu"}))["status"] == "entwurf"
    assert run(db.pickup_protocols.count_documents({"status": P.ZUR_FREIGABE})) == 600


# ============================================================ Fahrer: Auto-Termin vs. Vertragsloeschung
def test_auto_termin_nicht_auf_verschwindenden_vertrag(wegwerf, monkeypatch):
    db, run = wegwerf.db, wegwerf.run
    run(db.generated_pdfs.insert_one({"id": "c1", "dealer_id": "d1", "vehicle_id": "v1", "user_id": "sa1"}))
    body = SimpleNamespace(seller_address="Musterstr. 5", seller_zip="30159", seller_city="Hannover",
                           seller_name="V", seller_phone="", seller_email="", pickup_date="2099-01-01",
                           pickup_time="", vehicle_id="v1")
    user = {"id": "sa1", "dealer_id": "d1", "role": "sucher"}
    original = C._termin_einfuegen

    async def _loeschung_dazwischen(doc):
        # Vertragsloeschung setzt den Grabstein und loest Termine — BEVOR der Insert landet
        await db.generated_pdfs.update_one({"id": "c1"}, {"$set": {"loeschung": {"status": "laeuft"}}})
        await original(doc)
    monkeypatch.setattr(C, "_termin_einfuegen", _loeschung_dazwischen)
    appt_id, hinweis = run(C._abholtermin_fuer_vertrag(user, body, {"make_label": "BMW"}, "c1", "k1"))
    assert appt_id is None and "gelöscht" in hinweis
    assert run(db.appointments.count_documents({"contract_id": "c1"})) == 0, "kein Waisen-Termin"
    # Nachholer fuer den Rest: alter Termin auf fehlenden Vertrag wird geloest
    alt = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
    run(db.appointments.insert_many([
        {"id": "ta", "contract_id": "weg", "seller_name": "X", "seller_phone": "1", "created_at": alt},
        {"id": "tb", "contract_id": "weg", "created_at": _jetzt()},
        {"id": "tc", "contract_id": "c1", "created_at": alt}]))
    assert run(CS.termin_vertragsverweise_bereinigen(db, datetime.now(timezone.utc))) == 1
    ta = run(db.appointments.find_one({"id": "ta"}))
    assert ta["contract_id"] is None and ta["seller_name"] == "" and ta["seller_phone"] == ""
    assert run(db.appointments.find_one({"id": "tb"}))["contract_id"] == "weg", "frische Anlage unberuehrt"
    assert run(db.appointments.find_one({"id": "tc"}))["contract_id"] == "c1"


# ============================================================ Fahrer: Pseudonym nachholen, Sicht vor Annahme
def test_fahrer_pseudonym_wird_nachgeholt(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    run(db.driver_accounts.insert_one({"id": "lebt", "active": True}))
    run(db.pickup_reports.insert_many([
        {"id": "r1", "driver_account_id": "geloescht-konto", "driver_name": "Max Klar", "created_at": _jetzt()},
        {"id": "r2", "driver_account_id": "lebt", "driver_name": "Lebt", "created_at": _jetzt()},
        {"id": "r3", "driver_account_id": "alt-konto", "driver_name": "Alt", "created_at": _jetzt(-3 * 86400)},
    ]))
    run(db.betriebsalarme.insert_one({"typ": "abholbericht_pseudonym_offen", "ref": "r3", "offen": True}))
    assert run(CS.abholberichte_pseudonym_nachholen(db, datetime.now(timezone.utc))) == 2
    r1 = run(db.pickup_reports.find_one({"id": "r1"}))
    assert r1["driver_account_id"] == DRV.fahrer_pseudonym("geloescht-konto") and r1["driver_name"] == "Fahrer (gelöscht)"
    assert run(db.pickup_reports.find_one({"id": "r2"}))["driver_name"] == "Lebt"
    assert run(db.pickup_reports.find_one({"id": "r3"}))["driver_name"] == "Fahrer (gelöscht)", "per Alarm-Marker"
    assert run(db.betriebsalarme.find_one({"typ": "abholbericht_pseudonym_offen", "ref": "r3"}))["offen"] is False
    assert "abholberichte_pseudonym_nachholen(db, now)" in inspect.getsource(CS._cleanup_once)
    assert "termin_vertragsverweise_bereinigen(db, now)" in inspect.getsource(CS._cleanup_once)


def test_verkaeuferdaten_erst_nach_annahme_und_sucher_ohne_kontaktdaten():
    assert DRV.ort_ohne_strasse("Musterstr. 5, 30159 Hannover") == "30159 Hannover"
    assert DRV.ort_ohne_strasse("Musterstr. 5 30159 Hannover") == "30159 Hannover"
    assert DRV.ort_ohne_strasse("Hannover") == "Hannover"
    assert DRV.ort_ohne_strasse("Musterstr. 5, Hannover") == "Hannover"
    assert DRV.ort_ohne_strasse("Musterstr. 5") == ""
    assert DRV.ort_ohne_strasse(None) == ""
    q = inspect.getsource(DRV.driver_appointments)
    assert '"kontakt_nach_annahme": vor_annahme' in q and "ort_ohne_strasse(" in q
    assert '"seller_phone": None if vor_annahme' in q
    da = {"id": "f", "driver_code": "FD-1", "display_name": "Max", "email": "m@x.de", "active": True}
    voll = DRV._fahrer_eintrag(da, {"added_at": "x"})
    knapp = DRV._fahrer_eintrag(da, {"added_at": "x"}, voll=False)
    assert voll["driver_code"] == "FD-1" and voll["email"] == "m@x.de"
    assert knapp["driver_code"] is None and knapp["email"] is None and knapp["name"] == "Max"
    # Pruefbericht 20.09.2026 (P0): die vollstaendigen Fahrerdaten haengen
    # jetzt am eingetragenen Hauptchef statt an der blossen Rolle — ein
    # zweites dealer-Konto der Firma sah sie sonst auch. Die Zusage dieses
    # Tests (Sucher sieht nur Name und Status) ist dieselbe geblieben und
    # wird oben am echten Verhalten geprueft.
    assert "voll = await ist_haupt_chef(user)" in inspect.getsource(DRV.list_drivers)
    assert inspect.getsource(A.list_appointments).count('if chef_sicht else None') == 2
    assert inspect.getsource(A.get_appointment).count('if chef_sicht else None') == 2
    # Fotos je Bericht gedeckelt
    with pytest.raises(ValueError):
        DRV.PickupReportIn(deviations=[DRV.DeviationIn(label="x", photo_b64="a" * 7_000_000) for _ in range(6)])
    DRV.PickupReportIn(deviations=[DRV.DeviationIn(label="x", photo_b64="a" * 1_000_000) for _ in range(6)])


# ============================================================ Migration
def test_migration_konten_aktiv_feld(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    run(db.users.insert_many([{"id": "ohne"}, {"id": "mit", "active": True}]))
    run(db.driver_accounts.insert_many([{"id": "fohne"}, {"id": "fmit", "active": False}]))
    erg = run(MIG.m8_konten_aktiv_feld(db))
    assert erg == {"users_gesperrt": 1, "fahrer_aktiv": 1}
    assert run(db.users.find_one({"id": "ohne"}))["active"] is False
    assert run(db.users.find_one({"id": "mit"}))["active"] is True
    assert run(db.driver_accounts.find_one({"id": "fohne"}))["active"] is True
    assert run(db.driver_accounts.find_one({"id": "fmit"}))["active"] is False
    # Nachpruefung 20.09.2026 (N3): m9 traegt den Chef-Zeiger im Altbestand
    # nach. Geprueft wird deshalb, dass m8 weiter DRIN ist und die Zielversion
    # mit der Liste zusammenpasst — nicht mehr eine feste Zahl.
    assert "konten_aktiv_feld" in MIGRATION_NAMEN()
    assert MIG.ZIEL_VERSION == max(n for n, _, _ in MIG.MIGRATIONEN), (
        "ZIEL_VERSION passt nicht zur Migrationsliste")


def MIGRATION_NAMEN():
    return [n for _, n, _ in MIG.MIGRATIONEN]
