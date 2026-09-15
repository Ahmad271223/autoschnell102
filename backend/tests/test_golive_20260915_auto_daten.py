# -*- coding: utf-8 -*-
"""Wunsch Ahmad 15.09.2026, Auto-Daten des Betreibers:
- ein neuer Vertrag zum selben Fahrzeug fuehrt den Datensatz nach (bestehenden_datensatz)
- Nachverhandlung / neuer Preis (Neuerzeugung des Vertrags) aendert den Preis im Datensatz
- der Super-Admin loescht einen Datensatz (entfernen): Vermerk am Vertrag; Fristloeschung
  und Reparatur respektieren ihn
Wegwerf-Datenbank je Test (echtes Mongo), keine HTTP-Aufrufe.
"""
import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import auto_daten  # noqa: E402
import cleanup_service as CS  # noqa: E402
import deps  # noqa: E402
import routes.contracts as C  # noqa: E402

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
CHEF = {"id": "chef", "dealer_id": "d1", "role": "dealer", "active": True}


def _jetzt(delta_s: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=delta_s)).isoformat()


@pytest.fixture
def wegwerf(monkeypatch):
    from motor.motor_asyncio import AsyncIOMotorClient
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    name = f"autoschnell_ad_{uuid.uuid4().hex[:10]}"
    db = client[name]
    for mod in (deps, C):
        monkeypatch.setattr(mod, "db", db)
    try:
        yield SimpleNamespace(db=db, run=loop.run_until_complete)
    finally:
        try:
            loop.run_until_complete(client.drop_database(name))
        finally:
            client.close()
            loop.close()


def _datensatz(did, cents):
    return {"id": did, "brand": "VW", "model": "Golf", "purchase_price_cents": cents,
            "currency": "EUR", "damages": [], "schema_version": 2, "purchase_date": "2026-09-01"}


def _vertrag(cid, did, **extra):
    return {"id": cid, "dealer_id": "d1", "user_id": "chef", "version": 1, "status": "erstellt",
            "contract_no": cid, "vehicle_id": "v1", "mobile_ad_id": "m1",
            "admin_vehicle_data_id": did,
            "contract_data": {"seller_name": "V", "purchase_price": 5000.0,
                              "vehicle_make": "VW", "vehicle_model": "Golf",
                              "pickup_date": "2099-01-01"},
            "send_status": [], "created_at": _jetzt(), **extra}


def test_bestehender_datensatz_wird_gefunden(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    run(db.admin_vehicle_data.insert_one(_datensatz("avd1", 500000)))
    run(db.generated_pdfs.insert_one(_vertrag("c1", "avd1", created_at=_jetzt(-100))))
    assert run(auto_daten.bestehenden_datensatz(db, "d1", "v1")) == "avd1"
    assert run(auto_daten.bestehenden_datensatz(db, "d1", None, "m1")) == "avd1"
    assert run(auto_daten.bestehenden_datensatz(db, "d2", "v1")) is None      # andere Firma
    assert run(auto_daten.bestehenden_datensatz(db, "d1", "v9")) is None      # anderes Auto
    assert run(auto_daten.bestehenden_datensatz(db, "d1", None, None)) is None
    # Vertrag in Loeschung zaehlt nicht
    run(db.generated_pdfs.update_one({"id": "c1"}, {"$set": {"loeschung": {"status": "laeuft"}}}))
    assert run(auto_daten.bestehenden_datensatz(db, "d1", "v1")) is None
    run(db.generated_pdfs.update_one({"id": "c1"}, {"$unset": {"loeschung": ""}}))
    # der juengste Vertrag gewinnt
    run(db.admin_vehicle_data.insert_one(_datensatz("avd2", 450000)))
    run(db.generated_pdfs.insert_one(_vertrag("c2", "avd2")))
    assert run(auto_daten.bestehenden_datensatz(db, "d1", "v1")) == "avd2"
    # fehlt der Datensatz des juengsten Vertrags, gibt es keinen (kein Rueckfall auf alte)
    run(db.admin_vehicle_data.delete_one({"id": "avd2"}))
    assert run(auto_daten.bestehenden_datensatz(db, "d1", "v1")) is None
    run(db.admin_vehicle_data.insert_one(_datensatz("avd2", 450000)))
    # vom Betreiber entfernt: Vermerk am Vertrag, der aeltere Datensatz zaehlt wieder
    assert run(auto_daten.entfernen(db, "avd2")) is True
    assert run(auto_daten.entfernen(db, "avd2")) is False
    assert run(db.generated_pdfs.find_one({"id": "c2"}))["auto_daten_entfernt_am"]
    assert "auto_daten_entfernt_am" not in run(db.generated_pdfs.find_one({"id": "c1"}))
    assert run(auto_daten.bestehenden_datensatz(db, "d1", "v1")) == "avd1"


def test_nachverhandlung_eigene_spalte_einkaufspreis_bleibt(wegwerf):
    """Nachfrage Ahmad 15.09.2026: der Preis, fuer den man zum Auto gefahren ist,
    bleibt stehen; der vor Ort nachverhandelte Preis steht in der eigenen Spalte."""
    db, run = wegwerf.db, wegwerf.run
    run(db.dealers.insert_one({"id": "d1", "company_name": "Firma", "created_at": _jetzt()}))
    run(db.admin_vehicle_data.insert_one(_datensatz("avd1", 500000)))
    run(db.generated_pdfs.insert_one(_vertrag("c1", "avd1")))
    ok = run(C.regenerate_contract_for_pickup(
        contract_id="c1", dealer_id="d1", user=CHEF, neuer_preis=4500.0,
        grund="abholung_abgeschlossen", protokoll_id="p1"))
    assert ok is True
    d = run(db.admin_vehicle_data.find_one({"id": "avd1"}))
    assert d["purchase_price_cents"] == 500000          # Einkaufspreis des Vertrags bleibt
    assert d["preis_vor_ort_cents"] == 450000           # eigene Spalte
    assert d["purchase_date"] == "2026-09-01"           # Kaufdatum bleibt (Korrektur)
    assert run(db.admin_vehicle_data.count_documents({})) == 1
    c = run(db.generated_pdfs.find_one({"id": "c1"}))
    assert c["contract_data"]["purchase_price"] == 4500.0 and int(c["version"]) == 2
    assert c["contract_data"]["preis_vor_abholung"] == 5000.0
    # Terminverschiebung danach: die Spalten bleiben, wie sie sind
    ok = run(C.regenerate_contract_for_pickup(
        contract_id="c1", dealer_id="d1", user=CHEF, pickup_date="2099-02-02"))
    assert ok is True
    d = run(db.admin_vehicle_data.find_one({"id": "avd1"}))
    assert d["purchase_price_cents"] == 500000 and d["preis_vor_ort_cents"] == 450000


def test_vor_ort_nachtragen_preis_und_maengel(wegwerf):
    """Nachfrage Ahmad 15.09.2026: die vom Fahrer vor Ort festgehaltenen Maengel und
    der nachverhandelte Preis landen im Datensatz — gefiltert wie die Vertragsschaeden."""
    import inspect
    import routes.protocols as P
    db, run = wegwerf.db, wegwerf.run
    run(db.admin_vehicle_data.insert_one(_datensatz("avd1", 500000)))
    run(db.generated_pdfs.insert_one(_vertrag("c1", "avd1")))
    ok = run(auto_daten.vor_ort_nachtragen(
        db, "c1", "d1", preis=4200.0,
        maengel=[{"type_label": "Kratzer", "zone": "Tür vorne links"},
                 "Delle Heckklappe", "Rueckruf unter 0176 12345678"]))
    assert ok is True
    d = run(db.admin_vehicle_data.find_one({"id": "avd1"}))
    assert d["purchase_price_cents"] == 500000 and d["preis_vor_ort_cents"] == 420000
    assert d["maengel_vor_ort"] == ["Kratzer: Tür vorne links", "Delle Heckklappe"]
    # ohne Preis nur die Maengel; nichts -> nichts geschrieben
    assert run(auto_daten.vor_ort_nachtragen(db, "c1", "d1", maengel=[])) is True
    d = run(db.admin_vehicle_data.find_one({"id": "avd1"}))
    assert d["maengel_vor_ort"] == [] and d["preis_vor_ort_cents"] == 420000
    assert run(auto_daten.vor_ort_nachtragen(db, "c1", "d1")) is False
    assert run(auto_daten.vor_ort_nachtragen(db, "c9", "d1", preis=1.0)) is False
    assert run(auto_daten.vor_ort_nachtragen(db, "c1", "d2", preis=1.0)) is False   # andere Firma
    # vom Betreiber entfernter Datensatz: nichts mehr nachtragen
    assert run(auto_daten.entfernen(db, "avd1")) is True
    assert run(auto_daten.vor_ort_nachtragen(db, "c1", "d1", preis=1.0)) is False
    # Abschluss und Selbstheilung des Abholprotokolls rufen den Helfer auf
    q = inspect.getsource(P)
    assert "auto_daten_vor_ort_nachtragen(appt, filled, _preis_final)" in q
    assert 'auto_daten_vor_ort_nachtragen(appt, doc, doc.get("neuer_preis"))' in q


def test_fristloeschung_und_reparatur_respektieren_den_vermerk(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    alt = (datetime.now(timezone.utc) - timedelta(days=91)).isoformat()
    run(db.admin_vehicle_data.insert_one(_datensatz("avd1", 500000)))
    run(db.generated_pdfs.insert_many([
        _vertrag("c_entfernt", "avd1", created_at=alt),
        _vertrag("c_fehlt", "avd_weg", created_at=alt),
    ]))
    assert run(auto_daten.entfernen(db, "avd1")) is True
    # Reparatur legt fuer den bewusst entfernten Datensatz nichts Neues an
    assert run(CS.auto_daten_reparieren(db)) == 0
    assert run(db.admin_vehicle_data.count_documents({})) == 0
    n = run(CS.vertraege_nach_frist_loeschen(db, datetime.now(timezone.utc), aktiv=True))
    assert n == 1
    assert run(db.generated_pdfs.find_one({"id": "c_entfernt"})) is None
    assert run(db.generated_pdfs.find_one({"id": "c_fehlt"})) is not None   # ohne Vermerk: Alarm, bleibt
    assert run(db.betriebsalarme.count_documents({"ref": "c_fehlt"})) == 1
    assert run(db.betriebsalarme.count_documents({"ref": "c_entfernt"})) == 0
