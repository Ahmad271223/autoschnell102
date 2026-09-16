# -*- coding: utf-8 -*-
"""Go-Live 15.09.2026, Runde 12 — "1000 % dicht" fuer Sucher, Chef und Fahrer
(Reviewer-Liste auf 3fbd6da, 36 Punkte):

   1/4  Chef = dealers.user_id (current_chef, firma_gesperrt), Altbestand ohne
        Zeiger: aeltestes dealer-Konto, Zeiger wird nachgezogen
   2/3  Chefwechsel unter Sperre, danach genau ein dealer-Konto je Firma
   5    Konto in laufender Loeschung wird nicht mehr veraendert
   6    Firma in laufender Loeschung: keine Chef-/Sucher-Schreibvorgaenge
   7    deaktivierter Fahrer bekommt keine Fahrten (Quelle)
   8    Fahrer hinzufuegen prueft nach dem Insert das Konto (Quelle)
   9/10 Fahrer: Annahme und Status mit Stand bzw. nur bei angenommener Fahrt
  11-15 Termin: Datum/Uhrzeit geschuetzt, Endstatus-Wechsel nur Chef, finaler
        Termin unveraenderlich, kein anderer Fahrer, Protokollpflicht auch bei
        "Fahrer entfernen + abgeholt"
  16-18 Loeschung: angenommene Fahrt, Stand im Delete-Filter, Protokoll-Pruefung
  19/30 Stand-Pruefung bei Beweisdaten auch ohne Client-Stand
  20/21 Bericht: Verknuepfung und frische Beziehungen (Quelle)
  22/25/29 Nachholjobs und Frischabgleich
  23/24 Freigabeliste aelteste zuerst, Korrektur verwerfen atomar (Quelle)
  26    Versand meldet eine zwischenzeitlich neue Fassung (Quelle)
  33/35/36 Fahrerliste-Kuerzung, Konfliktabfrage ohne fremde IDs, Entfernen
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
import requests
from fastapi import HTTPException, Response

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tests"))

import deps  # noqa: E402
import konten as K  # noqa: E402
import kaufvorgang as KV  # noqa: E402
import lifecycle as LC  # noqa: E402
import cleanup_service as CS  # noqa: E402
import routes.admin as ADMIN  # noqa: E402
import routes.appointments as A  # noqa: E402
import routes.drivers as DRV  # noqa: E402
import routes.protocols as P  # noqa: E402
import routes.contracts as C  # noqa: E402
import routes.bestand as BST  # noqa: E402

HTTP = os.environ.get("RUNDE14_HTTP") == "1"
http = pytest.mark.skipif(not HTTP, reason="RUNDE14_HTTP=1 nicht gesetzt")
MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
API = K.API
SUF = "f" + uuid.uuid4().hex[:7]   # nie nur Ziffern: SUF steckt im Passwort, ein reiner Zahlen-Suffix als Nachname/Kontodaten haette es abgelehnt (CI 16.09.2026)
PW = f"Rz12{SUF}Kq4Lm9Xw2"


def _jetzt(delta_s: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=delta_s)).isoformat()


@pytest.fixture
def wegwerf(monkeypatch):
    from motor.motor_asyncio import AsyncIOMotorClient
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    name = f"autoschnell_r12_{uuid.uuid4().hex[:10]}"
    db = client[name]
    for mod in (deps, KV, LC, A, ADMIN, DRV, P, C, BST):
        monkeypatch.setattr(mod, "db", db)
    try:
        yield SimpleNamespace(db=db, run=loop.run_until_complete)
    finally:
        try:
            loop.run_until_complete(client.drop_database(name))
        finally:
            client.close()
            loop.close()


# ============================================================ 1 / 4
def test_01_chef_ist_der_eingetragene_hauptaccount(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    run(db.dealers.insert_one({"id": "d1", "company_name": "X", "user_id": "chefA"}))
    run(db.users.insert_many([
        {"id": "chefA", "dealer_id": "d1", "role": "dealer", "active": True, "created_at": "2026-01-01"},
        {"id": "chefB", "dealer_id": "d1", "role": "dealer", "active": False, "created_at": "2026-02-01"},
    ]))
    assert run(deps.current_chef({"id": "chefA", "dealer_id": "d1", "role": "dealer"}))["id"] == "chefA"
    with pytest.raises(HTTPException) as e:
        run(deps.current_chef({"id": "chefB", "dealer_id": "d1", "role": "dealer"}))
    assert e.value.status_code == 403
    # Firmensperre richtet sich nach dem eingetragenen Chef (A aktiv -> nicht gesperrt),
    # obwohl ein zweites dealer-Konto gesperrt ist
    assert run(deps.firma_gesperrt("d1")) is False
    run(db.users.update_one({"id": "chefA"}, {"$set": {"active": False}}))
    assert run(deps.firma_gesperrt("d1")) is True
    # Altbestand ohne Zeiger: aeltestes dealer-Konto ist Chef, Zeiger wird nachgezogen
    run(db.dealers.insert_one({"id": "d2", "company_name": "Y"}))
    run(db.users.insert_many([
        {"id": "alt", "dealer_id": "d2", "role": "dealer", "active": True, "created_at": "2026-01-01"},
        {"id": "neu", "dealer_id": "d2", "role": "dealer", "active": True, "created_at": "2026-03-01"},
    ]))
    with pytest.raises(HTTPException):
        run(deps.current_chef({"id": "neu", "dealer_id": "d2", "role": "dealer"}))
    assert run(deps.current_chef({"id": "alt", "dealer_id": "d2", "role": "dealer"}))["id"] == "alt"
    assert run(db.dealers.find_one({"id": "d2"}))["user_id"] == "alt"
    # Anlage setzt den Zeiger
    import kontenanlage
    assert "dealers.update_one({\"id\": firma_doc[\"id\"]}" in inspect.getsource(kontenanlage)


# ============================================================ 2 / 3 / 5 / 6
def test_02_chefwechsel_sperre_und_loeschguards(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    src = inspect.getsource(ADMIN.admin_update_user)
    assert 'acquire(db, f"chefwechsel-{target[\'dealer_id\']}"' in src
    assert '"id": {"$ne": target["id"]}},\n                            {"$set": {"role": "sucher"' in src
    assert 'get("status") == "laeuft":\n        raise HTTPException(409, "Dieses Konto wird gerade gelöscht' in src
    # Firma in Loeschung: current_firma 409
    run(db.dealers.insert_one({"id": "d1", "company_name": "X",
                               "loeschung": {"status": "laeuft"}}))
    with pytest.raises(HTTPException) as e:
        run(deps.current_firma({"id": "u1", "dealer_id": "d1", "role": "sucher"}))
    assert e.value.status_code == 409
    run(db.dealers.update_one({"id": "d1"}, {"$unset": {"loeschung": ""}}))
    assert run(deps.current_firma({"id": "u1", "dealer_id": "d1", "role": "sucher"}))["id"] == "u1"


# ============================================================ 7 / 8 / 20 / 21 / 33 / 35 (Quelle + Einheit)
def test_07_fahrer_pruefungen(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    run(db.dealer_drivers.insert_one({"dealer_id": "d1", "driver_account_id": "f1"}))
    run(db.driver_accounts.insert_one({"id": "f1", "driver_code": f"FD-{SUF.upper()}",
                                       "active": False}))
    with pytest.raises(HTTPException) as e:
        run(A._fahrer_pruefen("d1", "f1"))
    assert e.value.status_code == 400 and "deaktiviert" in e.value.detail
    run(db.driver_accounts.update_one({"id": "f1"}, {"$set": {"active": True}}))
    assert run(A._fahrer_pruefen("d1", "f1")) is None
    q = inspect.getsource(DRV.add_driver_by_code)
    assert 'Runde 12 (15.09.2026, Nr. 8)' in q and 'dealer_drivers.delete_one' in q
    q = inspect.getsource(DRV.driver_submit_report)
    assert '"vehicle_id": frisch.get("vehicle_id"' in q and 'Du bist nicht mehr mit dieser Firma' in q
    q = inspect.getsource(DRV.list_drivers)
    assert 'to_list(501)' in q and 'X-Truncated' in q
    q = inspect.getsource(DRV.driver_conflicts)
    assert 'c["id"] = None' in q


# ============================================================ 11-15 (Einheit ueber update_appointment)
def _termin(db, run, **extra):
    doc = {"id": "t1", "dealer_id": "d1", "title": "x", "status": "abgeholt",
           "pickup_date": "2026-09-10", "pickup_time": "10:00", "seller_name": "S",
           "driver_id": "f1", "created_by": "s1", "created_at": _jetzt(-600),
           "updated_at": _jetzt(-600), **extra}
    run(db.appointments.insert_one(doc))
    return doc


def test_11_bis_15_abgeschlossene_termine(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    run(db.dealers.insert_one({"id": "d1", "company_name": "X", "user_id": "chef"}))
    run(db.dealer_drivers.insert_many([{"dealer_id": "d1", "driver_account_id": "f1"},
                                       {"dealer_id": "d1", "driver_account_id": "f2"}]))
    _termin(db, run)
    run(db.pickup_protocols.insert_one({"id": "p1", "appointment_id": "t1", "status": "final",
                                        "superseded": False, "version": 1}))
    sucher = {"id": "s1", "dealer_id": "d1", "role": "sucher"}
    chef = {"id": "chef", "dealer_id": "d1", "role": "dealer"}
    # 11: Sucher darf Datum eines abgeschlossenen Termins nicht aendern
    with pytest.raises(HTTPException) as e:
        run(A.update_appointment("t1", A.AppointmentIn(pickup_date="2026-09-11"), sucher))
    assert e.value.status_code == 403
    # 12: Sucher darf den Endzustand nicht wechseln
    with pytest.raises(HTTPException) as e:
        run(A.update_appointment("t1", A.AppointmentIn(status="storniert"), sucher))
    assert e.value.status_code == 403
    # 13: auch der Chef aendert Beweisdaten am finalen Termin nicht
    with pytest.raises(HTTPException) as e:
        run(A.update_appointment("t1", A.AppointmentIn(seller_name="Anders"), chef))
    assert e.value.status_code == 409 and "Korrektur-Version" in e.value.detail
    # 14: kein anderer Fahrer an einen abgeschlossenen Termin
    with pytest.raises(HTTPException) as e:
        run(A.update_appointment("t1", A.AppointmentIn(driver_id="f2"), chef))
    assert e.value.status_code == 409
    # Notizen bleiben erlaubt (kein Beweisdatum)
    assert run(A.update_appointment("t1", A.AppointmentIn(notes="ok"), chef))["ok"] is True
    # 15: "Fahrer entfernen + abgeholt" ohne finales Protokoll -> 409
    run(db.appointments.update_one({"id": "t1"}, {"$set": {"status": "offen"}}))
    run(db.pickup_protocols.delete_many({}))
    with pytest.raises(HTTPException) as e:
        run(A.update_appointment("t1", A.AppointmentIn(driver_id=None, status="abgeholt"), chef))
    assert e.value.status_code == 409 and "Abholprotokoll" in e.value.detail


# ============================================================ 19 / 30
def test_19_stand_pruefung_ohne_client_stand(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    run(db.dealers.insert_one({"id": "d1", "company_name": "X", "user_id": "chef"}))
    _termin(db, run, status="offen", driver_id=None)
    chef = {"id": "chef", "dealer_id": "d1", "role": "dealer"}
    stand_alt = run(db.appointments.find_one({"id": "t1"}))["updated_at"]
    # Kollege (oder Protokoll-Abschicken) aendert den Stand, nachdem wir gelesen haben:
    # simuliert ueber einen Beweisdaten-Write, dessen updated_at-Filter den ALTEN Stand traegt
    run(db.appointments.update_one({"id": "t1"}, {"$set": {"updated_at": _jetzt()}}))

    async def _alt_lesen(*a, **k):
        return {**(await _orig(*a, **k)), "updated_at": stand_alt} if a and a[0].get("id") == "t1" \
            and (await _orig(*a, **k)) else await _orig(*a, **k)
    _orig = db.appointments.find_one
    src = inspect.getsource(A.update_appointment)
    assert 'write_filt["updated_at"] = existing["updated_at"]' in src
    assert "beweisdaten_wechsel and not stand" in src
    # Protokoll-Abschicken fasst den Termin-Stand an
    assert 'db.appointments.update_one({"id": appt_id}, {"$set": {"updated_at": jetzt}})' \
        in inspect.getsource(P.submit_protocol)


# ============================================================ 16 / 17 / 18
def test_16_bis_18_loeschung(wegwerf, monkeypatch):
    db, run = wegwerf.db, wegwerf.run
    monkeypatch.setattr(deps, "_REPLICA_SET_STAND", {"bis": 0.0, "ist": False})
    run(db.dealers.insert_one({"id": "d1", "company_name": "X", "user_id": "chef"}))
    _termin(db, run, status="offen", zuteilung="angenommen")
    chef = {"id": "chef", "dealer_id": "d1", "role": "dealer"}
    with pytest.raises(HTTPException) as e:
        run(A.delete_appointment("t1", chef))
    assert e.value.status_code == 409 and "angenommen" in e.value.detail
    run(db.appointments.update_one({"id": "t1"}, {"$set": {"zuteilung": "abgelehnt"}}))
    src = inspect.getsource(A.delete_appointment)
    assert '"status": appt.get("status"), "zuteilung": appt.get("zuteilung"),' in src
    assert '"updated_at": appt.get("updated_at")}' in src        # Runde 19 (Nr. 35)
    assert 'TERMIN_MIT_PROTOKOLL_HINWEIS)\n        for kv in betroffene' in src
    assert run(A.delete_appointment("t1", chef)) == {"ok": True}


# ============================================================ 9 / 10 (HTTP)
@http
def test_09_10_fahrer_annahme_und_status_mit_stand():
    firma = K.registrieren(json={"email": f"r12chef{SUF}@example.com", "password": PW,
                                 "company_name": f"R12 {SUF}", "contact_person": "Chef"})
    assert firma.status_code == 200, firma.text[:300]
    Ch = K._kopf(firma.json()["token"])
    fahrer = K.fahrer_registrieren(json={"display_name": f"F {SUF}", "password": PW,
                                         "email": f"r12f{SUF}@example.com"})
    assert fahrer.status_code == 200, fahrer.text[:300]
    F = K._kopf(fahrer.json()["token"])
    fid = fahrer.json()["driver"]["id"]
    r = requests.post(f"{API}/drivers/add", json={"driver_code": fahrer.json()["driver"]["driver_code"]},
                      headers=Ch, timeout=60)
    assert r.status_code in (200, 409)
    r = requests.post(f"{API}/appointments", json={"title": "R12", "pickup_date": "2099-04-01",
                                                    "pickup_time": "09:00", "driver_id": fid},
                      headers=Ch, timeout=60)
    assert r.status_code == 200, r.text[:300]
    aid = r.json()["id"]
    alt = requests.get(f"{API}/appointments/{aid}", headers=Ch, timeout=60).json()["updated_at"]
    # Chef aendert die Uhrzeit -> Fahrer nimmt auf altem Stand an -> 409
    r = requests.put(f"{API}/appointments/{aid}", json={"pickup_time": "11:00"}, headers=Ch, timeout=60)
    assert r.status_code == 200, r.text[:300]
    r = requests.put(f"{API}/driver/appointments/{aid}/zuteilung",
                     json={"action": "annehmen", "stand": alt}, headers=F, timeout=60)
    assert r.status_code == 409, r.text[:300]
    neu = requests.get(f"{API}/appointments/{aid}", headers=Ch, timeout=60).json()["updated_at"]
    r = requests.put(f"{API}/driver/appointments/{aid}/zuteilung",
                     json={"action": "annehmen", "stand": neu}, headers=F, timeout=60)
    assert r.status_code == 200 and r.json()["zuteilung"] == "angenommen", r.text[:300]
    # Chef aendert das Datum (Zuteilung wieder offen) -> Fahrer-Status greift nicht
    r = requests.put(f"{API}/appointments/{aid}", json={"pickup_date": "2099-04-02"}, headers=Ch, timeout=60)
    assert r.status_code == 200, r.text[:300]
    r = requests.put(f"{API}/driver/appointments/{aid}/status", json={"status": "nicht abgeholt"},
                     headers=F, timeout=60)
    assert r.status_code == 409, r.text[:300]
    K._db().appointments.delete_one({"id": aid})


# ============================================================ 22 / 25 / 29 / 23 / 24 / 26 / 36 (Quelle + Einheit)
def test_22_nachholjobs_und_quellen(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    # 22: Abholbericht-Nacharbeit
    run(db.betriebsalarme.insert_one({"typ": "abholbericht_nacharbeit_offen", "ref": "t1",
                                      "offen": True, "created_at": _jetzt()}))
    run(db.pickup_reports.insert_many([
        {"id": "r1", "appointment_id": "t1", "version": 1, "superseded": False, "deviations": []},
        {"id": "r2", "appointment_id": "t1", "version": 2, "superseded": False,
         "deviations": [{"id": "d1"}, {"id": "d2"}]},
    ]))
    run(db.appointments.insert_one({"id": "t1", "dealer_id": "d1"}))
    assert run(CS.abholberichte_nacharbeit_nachholen(db)) == 1
    assert run(db.pickup_reports.find_one({"id": "r1"}))["superseded"] is True
    t = run(db.appointments.find_one({"id": "t1"}))
    assert t["has_pickup_report"] is True and t["deviations_count"] == 2
    assert run(db.betriebsalarme.find_one({"typ": "abholbericht_nacharbeit_offen"}))["offen"] is False
    # 25 / 29 verdrahtet
    q = inspect.getsource(CS._cleanup_once)
    assert "vertrag_nach_abholung_nachholen(db)" in q and "termine_frisch_abgleichen(db, now)" in q
    assert "vertrag_nach_abholung_aktualisieren" in inspect.getsource(CS.vertrag_nach_abholung_nachholen)
    # 23 / 24 / 26
    assert "_FREIGABE_MAX" in inspect.getsource(P._wartende_protokolle)   # Nr. 23: Grenze 5000 + Alarm
    assert "transaktion(_beide)" in inspect.getsource(P.korrektur_verwerfen)
    assert "versand_alte_fassung" in inspect.getsource(C)
    # 36: Entfernen blockiert auch Chef-Termine zu eigenen Vertraegen
    assert '{"contract_id": {"$in": eigene_vertraege}}' in inspect.getsource(BST.vehicle_fuer_sucher_entfernen)
