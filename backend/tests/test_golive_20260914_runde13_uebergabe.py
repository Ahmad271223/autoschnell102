# -*- coding: utf-8 -*-
"""Go-Live 15.09.2026, Runde 13 — zweite Nachpruefung Sucher/Chef/Fahrer
(Reviewer-Listen auf 3fbd6da: 20 + 15 Punkte):

  L3 1-8, 20 / L4 1-2  Uebergabe = ganzer Vorgang (Kaufvorgaenge, Vertraege,
                        Termine folgen dem Fahrzeug); Sucher-Loeschung uebergibt
                        den Vorgang an den Chef
  L3 17, 19            Zielkonto nicht in Loeschung; keine Uebergabe waehrend
                        Freigabe/Abschluss
  L3 9-13              Revision Pflicht, Submit mit Revisions-CAS, Insert faengt
                        nur Dubletten, Vertragsanker im Entwurf
  L3 14/15             Kaufvorgang-Status mit Ausgangsstatus-CAS, Frischabgleich
                        der Fahrzeuge
  L4 3                 persoenliches Abo an die Firma gebunden
  L4 4-6               kein zweiter geschlossener Termin je Vertrag
  L4 7/8               fehlende Zuteilung = nicht angenommen
  L4 9/10/13           Fahrer-Trennung nachholen, Verknuepfungs-Invariante
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
import kaufvorgang as KV  # noqa: E402
import lifecycle as LC  # noqa: E402
import cleanup_service as CS  # noqa: E402
import routes.admin as ADMIN  # noqa: E402
import routes.appointments as A  # noqa: E402
import routes.bestand as BST  # noqa: E402
import routes.drivers as DRV  # noqa: E402
import routes.protocols as P  # noqa: E402

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"


def _jetzt(delta_s: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=delta_s)).isoformat()


@pytest.fixture
def wegwerf(monkeypatch):
    from motor.motor_asyncio import AsyncIOMotorClient
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    name = f"autoschnell_r13_{uuid.uuid4().hex[:10]}"
    db = client[name]
    for mod in (deps, KV, LC, A, ADMIN, BST, DRV, P):
        monkeypatch.setattr(mod, "db", db)
    try:
        yield SimpleNamespace(db=db, run=loop.run_until_complete)
    finally:
        try:
            loop.run_until_complete(client.drop_database(name))
        finally:
            client.close()
            loop.close()


def _welt(db, run):
    run(db.dealers.insert_one({"id": "d1", "company_name": "X", "user_id": "chef"}))
    run(db.users.insert_many([
        {"id": "chef", "dealer_id": "d1", "role": "dealer", "active": True, "created_at": "2026-01-01"},
        {"id": "sa", "dealer_id": "d1", "role": "sucher", "active": True, "created_at": "2026-01-02"},
        {"id": "sb", "dealer_id": "d1", "role": "sucher", "active": True, "created_at": "2026-01-03"},
    ]))
    run(db.vehicles.insert_one({"id": "v1", "dealer_id": "d1", "owner_user_id": "sa",
                                "lifecycle": "abholung_geplant"}))
    run(db.generated_pdfs.insert_one({"id": "c1", "dealer_id": "d1", "vehicle_id": "v1", "user_id": "sa",
                                      "appointment_id": "t1"}))
    run(db.kaufvorgaenge.insert_one({"id": "k1", "dealer_id": "d1", "vehicle_id": "v1", "user_id": "sa",
                                     "contract_id": "c1", "status": "abholung_geplant",
                                     "appointment_id": "t1", "created_at": _jetzt(), "updated_at": _jetzt()}))
    run(db.appointments.insert_one({"id": "t1", "dealer_id": "d1", "vehicle_id": "v1", "contract_id": "c1",
                                    "kaufvorgang_id": "k1", "created_by": "sa", "status": "offen",
                                    "seller_name": "Verkaeufer", "created_at": _jetzt(), "updated_at": _jetzt()}))


# ============================================================ Uebergabe
def test_uebergabe_nimmt_den_ganzen_vorgang_mit(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    _welt(db, run)
    chef = {"id": "chef", "dealer_id": "d1", "role": "dealer"}
    sa = {"id": "sa", "dealer_id": "d1", "role": "sucher"}
    sb = {"id": "sb", "dealer_id": "d1", "role": "sucher"}
    assert run(deps.termin_im_bereich(sa, run(db.appointments.find_one({"id": "t1"})))) is True
    assert run(deps.termin_im_bereich(sb, run(db.appointments.find_one({"id": "t1"})))) is False
    erg = run(BST.set_vehicle_owner("v1", BST.BesitzerIn(owner_user_id="sb"), chef))
    assert erg["ok"] and erg["uebergabe"] == {"kaufvorgaenge": 1, "vertraege": 1, "termine": 1}
    t1 = run(db.appointments.find_one({"id": "t1"}))
    assert t1["created_by"] == "sb" and t1["uebergeben_von"] == "sa"
    assert run(db.kaufvorgaenge.find_one({"id": "k1"}))["user_id"] == "sb"
    assert run(db.generated_pdfs.find_one({"id": "c1"}))["user_id"] == "sb"
    # Bereich folgt: A sieht den Termin nicht mehr, B schon
    assert run(deps.termin_im_bereich(sa, t1)) is False
    assert run(deps.termin_im_bereich(sb, t1)) is True
    # Nr. 19: waehrend einer laufenden Freigabe keine Uebergabe
    run(db.pickup_protocols.insert_one({"id": "p1", "appointment_id": "t1", "vehicle_id": "v1",
                                        "status": "zur_freigabe", "superseded": False, "version": 1}))
    with pytest.raises(HTTPException) as e:
        run(BST.set_vehicle_owner("v1", BST.BesitzerIn(owner_user_id="sa"), chef))
    assert e.value.status_code == 409 and "Freigabe" in e.value.detail
    # Nr. 17: Zielkonto in Loeschung -> 404
    run(db.pickup_protocols.delete_many({}))
    run(db.users.update_one({"id": "sa"}, {"$set": {"loeschung": {"status": "laeuft"}}}))
    with pytest.raises(HTTPException) as e:
        run(BST.set_vehicle_owner("v1", BST.BesitzerIn(owner_user_id="sa"), chef))
    assert e.value.status_code == 404
    assert "vorgang_uebergeben(" in inspect.getsource(ADMIN.admin_delete_user), "Sucher-Loeschung uebergibt"


def test_sucher_loeschung_uebergibt_an_den_chef(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    _welt(db, run)
    run(db.users.update_one({"id": "sa"}, {"$set": {"kontonummer": "10023-1"}}))
    admin = {"id": "root", "dealer_id": "", "role": "admin", "is_super_admin": True}
    erg = run(ADMIN.admin_delete_user("sa", False, admin))
    assert erg["ok"]
    assert run(db.users.find_one({"id": "sa"})) is None
    assert run(db.vehicles.find_one({"id": "v1"}))["owner_user_id"] == "chef"
    assert run(db.kaufvorgaenge.find_one({"id": "k1"}))["user_id"] == "chef"
    assert run(db.generated_pdfs.find_one({"id": "c1"}))["user_id"] == "chef"
    assert run(db.appointments.find_one({"id": "t1"}))["created_by"] == "chef"


# ============================================================ Abo an Firma
def test_persoenliches_abo_gehoert_zur_firma(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    run(db.subscriptions.insert_many([
        {"id": "s1", "dealer_id": "d1", "subject_user_id": "u1", "status": "active",
         "expires_at": _jetzt(86400), "plan": "monthly"},
    ]))
    q = inspect.getsource(deps.get_subscription_status)
    assert '"$or": [{"dealer_id": dealer_id}, {"dealer_id": {"$exists": False}}' in q
    a = run(deps.get_subscription_status("d1", subject_user_id="u1"))
    b = run(deps.get_subscription_status("d2", subject_user_id="u1"))
    assert a.get("active") is True and not b.get("active"), (a, b)


# ============================================================ geschlossener Zweittermin
def test_kein_zweiter_geschlossener_termin_je_vertrag(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    _welt(db, run)
    chef = {"id": "chef", "dealer_id": "d1", "role": "dealer"}
    with pytest.raises(HTTPException) as e:
        run(A.create_appointment(A.AppointmentIn(title="zwei", contract_id="c1", status="storniert"), chef))
    assert e.value.status_code == 409 and "bereits einen Termin" in e.value.detail
    assert run(db.generated_pdfs.find_one({"id": "c1"}))["appointment_id"] == "t1"
    src = inspect.getsource(A.create_appointment)
    assert 'if body.contract_id and doc.get("status") not in ABGESCHLOSSEN:' in src


# ============================================================ Zuteilung strikt
def test_fehlende_zuteilung_ist_nicht_angenommen(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    run(db.dealer_drivers.insert_one({"dealer_id": "d1", "driver_account_id": "f1"}))
    run(db.appointments.insert_one({"id": "t1", "dealer_id": "d1", "driver_id": "f1", "status": "offen",
                                    "created_at": _jetzt(), "updated_at": _jetzt()}))
    fahrer = {"id": "f1", "name": "F"}
    # Invariante: fehlende Zuteilung wird im Aufraeumlauf auf "offen" gesetzt —
    # danach muss der Fahrer annehmen, bevor er die Fahrt abschliessen darf.
    assert run(CS.fahrer_verknuepfung_abgleichen(db)) >= 1
    assert run(db.appointments.find_one({"id": "t1"}))["zuteilung"] == "offen"
    with pytest.raises(HTTPException) as e:
        run(DRV.driver_set_status("t1", DRV.DriverStatusIn(status="nicht abgeholt"), fahrer))
    assert e.value.status_code == 409 and "annehmen" in e.value.detail
    with pytest.raises(HTTPException):
        P.zuteilung_offen_oder_409({"id": "t1", "driver_id": "f1", "zuteilung": "offen"})
    assert '"$nin": ["offen", "abgelehnt"]' in inspect.getsource(DRV.driver_set_status)


# ============================================================ Fahrer-Trennung nachholen / Invariante
def test_fahrer_trennung_und_verknuepfung(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    run(db.appointments.insert_many([
        {"id": "t1", "dealer_id": "d1", "driver_id": "f1", "status": "offen", "zuteilung": "angenommen",
         "created_at": _jetzt(), "updated_at": _jetzt()},
        {"id": "t2", "dealer_id": "d1", "driver_id": "f1", "status": "abgeholt",
         "created_at": _jetzt(), "updated_at": _jetzt()},
    ]))
    run(db.betriebsalarme.insert_one({"typ": "fahrer_bereinigung_fehlgeschlagen", "ref": "f1",
                                      "offen": True, "details": {"dealer_id": "d1"},
                                      "created_at": _jetzt()}))
    assert run(CS.fahrer_trennung_nachholen(db)) == 1
    t1 = run(db.appointments.find_one({"id": "t1"}))
    t2 = run(db.appointments.find_one({"id": "t2"}))
    assert "driver_id" not in t1 and t1["zuteilung"] is None
    assert "driver_id" not in t2 and t2["driver_id_hist"] == "f1"
    assert run(db.betriebsalarme.find_one({"typ": "fahrer_bereinigung_fehlgeschlagen"}))["offen"] is False
    # Invariante ohne Alarm: offener Termin mit Fahrer ohne Verknuepfung
    run(db.appointments.insert_one({"id": "t3", "dealer_id": "d1", "driver_id": "f9", "status": "offen",
                                    "zuteilung": "angenommen", "created_at": _jetzt(), "updated_at": _jetzt()}))
    assert run(CS.fahrer_verknuepfung_abgleichen(db)) >= 1
    assert "driver_id" not in run(db.appointments.find_one({"id": "t3"}))


# ============================================================ Kaufvorgang-CAS + Protokoll
def test_kaufvorgang_cas_und_protokoll_quellen(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    run(db.vehicles.insert_one({"id": "v1", "dealer_id": "d1", "lifecycle": "abholung_geplant"}))
    run(db.kaufvorgaenge.insert_one({"id": "k1", "dealer_id": "d1", "vehicle_id": "v1", "user_id": "u",
                                     "contract_id": "c1", "status": "abholung_geplant",
                                     "created_at": _jetzt(), "updated_at": _jetzt()}))
    assert run(KV.status_setzen("k1", "gesendet", von="vertrag_erstellt")) is None, "Ausgangsstatus passt nicht"
    assert run(db.kaufvorgaenge.find_one({"id": "k1"}))["status"] == "abholung_geplant"
    assert run(KV.status_setzen("k1", "nicht_abgeholt", von="abholung_geplant"))["status"] == "nicht_abgeholt"
    assert 'von=kv.get("status")' in inspect.getsource(KV.termin_status_uebernehmen)
    q = inspect.getsource(P.save_protocol)
    assert "der Entwurf braucht den" in q and "except DuplicateKeyError:" in q
    assert '"contract_id": appt.get("contract_id")' in q
    q2 = inspect.getsource(P.submit_protocol)
    assert 'submit_filt["revision"] = doc["revision"]' in q2 and "in einem anderen Tab" in q2
    q3 = inspect.getsource(CS.termine_frisch_abgleichen)
    assert "fahrzeug_status_aggregieren(kv" in q3
