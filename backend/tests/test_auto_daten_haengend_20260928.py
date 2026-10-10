# -*- coding: utf-8 -*-
"""Bestandspruefung 28.09.2026 (Scharfschalten der Fristloeschung auf prod2):
16 von 17 Live-Vertraegen zeigten auf Auto-Datensaetze, die es nicht mehr gab
(kein Vermerk auto_daten_entfernt_am) — das Pruefskript meldete "NICHT bereit",
der stuendliche Reparaturlauf liess solche Vertraege aber liegen.

- cleanup_service.auto_daten_reparieren legt den Datensatz aus der
  Vertragsfassung neu an; aeltere Vertraege desselben Autos teilen ihn
  (ein Auto = ein Datensatz); bewusst entfernte Datensaetze, laufende
  Loeschungen und Vertraege ohne Vertragsfassung bleiben unangetastet.
- scripts/vertraege_bestand_pruefen.py zaehlt den Vermerk nicht als Hindernis
  und fuehrt den Reparaturlauf per --reparieren sofort aus.

In-process gegen eine Wegwerf-Datenbank (echtes Mongo), keine HTTP-Aufrufe."""
import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from pymongo import MongoClient

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import cleanup_service as CS  # noqa: E402
import vertraege_bestand_pruefen as VBP  # noqa: E402

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"


def _zeit(tage_zurueck: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=tage_zurueck)).isoformat()


def _vertrag(cid, avd, vehicle="v1", tage=1, preis=5100.0, **extra):
    doc = {"id": cid, "dealer_id": "d1", "vehicle_id": vehicle, "make": "VW", "model": "Golf",
           "admin_vehicle_data_id": avd, "created_at": _zeit(tage),
           "contract_data": {"purchase_price": preis, "vehicle_mileage": "85000",
                             "vehicle_first_registration": "05/2019", "vehicle_fuel": "Diesel",
                             "vehicle_power_ps": 150, "vehicle_power_kw": 110}}
    doc.update(extra)
    return doc


def _bestand(db):
    """Der Live-Befund im Kleinen: ein gueltiger Verweis, zwei Verweise ins Leere
    auf dasselbe Auto, ein bewusst entfernter Datensatz, ein Grabstein, ein
    Vertrag ohne Vertragsfassung."""
    # wie in Produktion (job_lock legt den Unique-Index beim Start an): ohne ihn legt
    # acquire() eine zweite Sperre gleichen Namens an, statt "besetzt" zu melden
    db.job_locks.create_index("name", unique=True)
    db.admin_vehicle_data.insert_one({"id": "avd-ok", "purchase_date": "2026-09-01",
                                      "purchase_price_cents": 100})
    db.generated_pdfs.insert_many([
        _vertrag("c-ok", "avd-ok", vehicle="v2"),
        _vertrag("c-neu", "weg-1", vehicle="v1", tage=1, preis=5100.0),
        _vertrag("c-alt", "weg-2", vehicle="v1", tage=5, preis=4900.0),
        _vertrag("c-entfernt", "weg-3", vehicle="v3", auto_daten_entfernt_am=_zeit(0)),
        _vertrag("c-laeuft", "weg-4", vehicle="v4", loeschung={"status": "laeuft"}),
        _vertrag("c-leer", "weg-5", vehicle="v5", contract_data={}),
    ])


def _sperre_halten(db_name: str, name: str) -> None:
    """Eine job_lock-Sperre so setzen, wie sie ein laufender Prozess haelt
    (nicht freigegeben, TTL 10 Minuten)."""
    from motor.motor_asyncio import AsyncIOMotorClient
    import job_lock

    async def _lauf():
        client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
        try:
            assert await job_lock.acquire(client[db_name], name, ttl_seconds=600)
        finally:
            client.close()
    asyncio.run(_lauf())


@pytest.fixture
def wegwerf():
    from motor.motor_asyncio import AsyncIOMotorClient
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    name = f"autoschnell_hg_{uuid.uuid4().hex[:10]}"
    client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    try:
        yield SimpleNamespace(db=client[name], run=loop.run_until_complete)
    finally:
        try:
            loop.run_until_complete(client.drop_database(name))
        finally:
            client.close()
            loop.close()


@pytest.fixture
def sync_db():
    name = f"autoschnell_hg_{uuid.uuid4().hex[:10]}"
    client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    try:
        yield client[name]
    finally:
        client.drop_database(name)
        client.close()


def test_01_reparaturlauf_legt_datensatz_fuer_verweis_ins_leere_neu_an(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    with MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000) as sync:
        _bestand(sync[db.name])
    assert run(CS.auto_daten_reparieren(db)) >= 2
    neu = run(db.generated_pdfs.find_one({"id": "c-neu"}))
    alt = run(db.generated_pdfs.find_one({"id": "c-alt"}))
    # juengster Vertrag bekommt einen frischen Datensatz aus seiner Vertragsfassung ...
    assert isinstance(neu["admin_vehicle_data_id"], str) and neu["admin_vehicle_data_id"] != "weg-1"
    d = run(db.admin_vehicle_data.find_one({"id": neu["admin_vehicle_data_id"]}))
    assert d["purchase_price_cents"] == 510000 and d["mileage_km"] == 85000
    assert d["purchase_date"] == neu["created_at"][:10]
    # ... der aeltere Vertrag desselben Autos teilt ihn (kein zweites Auto in den Auto-Daten)
    assert alt["admin_vehicle_data_id"] == neu["admin_vehicle_data_id"]
    # unangetastet: gueltiger Verweis, bewusst entfernt, Grabstein, ohne Vertragsfassung
    assert run(db.generated_pdfs.find_one({"id": "c-ok"}))["admin_vehicle_data_id"] == "avd-ok"
    e = run(db.generated_pdfs.find_one({"id": "c-entfernt"}))
    assert e["admin_vehicle_data_id"] == "weg-3" and e["auto_daten_entfernt_am"]
    assert run(db.generated_pdfs.find_one({"id": "c-laeuft"}))["admin_vehicle_data_id"] == "weg-4"
    assert run(db.generated_pdfs.find_one({"id": "c-leer"}))["admin_vehicle_data_id"] == "weg-5"
    assert run(db.admin_vehicle_data.count_documents({})) == 2
    # zweiter Lauf: nichts mehr zu tun
    assert run(CS.auto_daten_reparieren(db)) == 0
    assert run(db.admin_vehicle_data.count_documents({})) == 2


def test_02_pruefskript_vermerk_kein_hindernis_und_reparieren(sync_db, monkeypatch, capsys):
    _bestand(sync_db)
    e = VBP.pruefen(sync_db)
    assert e["vertraege_gesamt"] == 6 and e["auto_datensaetze"] == 1
    assert sorted(e["haengende_verweise"]) == ["weg-1", "weg-2", "weg-5"]
    assert e["datensatz_bewusst_entfernt"] == 1 and e["loeschung_laeuft"] == 1
    assert e["doppelte_verweise"] == {} and e["ohne_verweis"] == 0
    monkeypatch.setattr(VBP, "DB_NAME", sync_db.name)
    monkeypatch.setattr(VBP, "MONGO_URL", MONGO_URL)
    assert VBP.main([]) == 1
    aus = capsys.readouterr().out
    assert "NICHT bereit: 3 Vertraege" in aus and "--reparieren" in aus
    assert "Datensatz vom Betreiber entfernt (Vermerk, kein Hindernis): 1" in aus
    # --reparieren fuehrt den Reparaturlauf sofort aus — auch waehrend der
    # stuendliche Aufraeumlauf seine Stundensperre haelt (prod2 28.09.: unter
    # derselben Sperre kam das Skript nie dran); der Vertrag ohne
    # Vertragsfassung bleibt das einzige Hindernis
    _sperre_halten(sync_db.name, "cleanup-cycle")
    assert VBP.main(["--reparieren"]) == 1
    aus = capsys.readouterr().out
    assert "Reparaturlauf: 2 Vertraege repariert" in aus
    assert "NICHT bereit: 1 Vertraege" in aus
    assert VBP.pruefen(sync_db)["haengende_verweise"] == ["weg-5"]
    # laeuft schon eine Reparatur (eigene Sperre), wartet das Skript — kein zweiter Lauf
    _sperre_halten(sync_db.name, VBP.SPERRE)
    assert VBP.main(["--reparieren"]) == 3
    assert "Eine Reparatur laeuft gerade" in capsys.readouterr().out
    sync_db.generated_pdfs.delete_one({"id": "c-leer"})
    assert VBP.main([]) == 0
    assert "Bereit:" in capsys.readouterr().out
    # unbekannter Parameter: Hilfe statt Aktion
    assert VBP.main(["--loeschen"]) == 2
