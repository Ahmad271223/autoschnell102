# -*- coding: utf-8 -*-
"""Wunsch Ahmad 28.09.2026: "ein Testlauf, wo alle Autos jetzt einmalig gecrawlt werden; ab morgen wieder zu den
normalen Uhrzeiten" -> scripts/markt_alle_jetzt.py: je aktivem Segment ein Sofort-Lauf (markt.jobs.job_sofort),
Vorschau ohne --ja, kein Doppel-Lauf, Tagesplan unberuehrt, Abbruch bei Crawler AUS / ohne Budget.
In-process gegen eine Wegwerf-Datenbank (echtes Mongo), keine HTTP-Aufrufe, kein Apify."""
import asyncio
import os
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import markt_alle_jetzt as MAJ  # noqa: E402
from markt import konfig  # noqa: E402

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"


@pytest.fixture
def wegwerf():
    from motor.motor_asyncio import AsyncIOMotorClient
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    name = f"autoschnell_aj_{uuid.uuid4().hex[:10]}"
    client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    try:
        yield SimpleNamespace(db=client[name], name=name, run=loop.run_until_complete)
    finally:
        try:
            loop.run_until_complete(client.drop_database(name))
        finally:
            client.close()
            loop.close()


async def _takt_ok(db):
    return {"ohne_budget": False, "segmente_je_tag": 99, "segmente": 2, "intervall_tage": 1}


async def _takt_leer(db):
    return {"ohne_budget": True, "segmente_je_tag": 0, "segmente": 2, "intervall_tage": 0}


def _bestand(db, run):
    run(db[konfig.MODELLE].insert_many([
        {"id": "m1", "label": "VW Golf VII", "enabled": True, "status": "active"},
        {"id": "m2", "label": "Opel Astra K", "enabled": False, "status": "paused"},
    ]))
    run(db[konfig.SEGMENTE].insert_many([
        {"id": "s1", "model_id": "m1", "label": "VW Golf VII", "enabled": True, "max_items": 20, "crawls_per_day": 1},
        {"id": "s2", "model_id": "m1", "label": "VW Golf VII", "enabled": True, "max_items": 20, "crawls_per_day": 2},
        {"id": "s3", "model_id": "m1", "label": "VW Golf VII", "enabled": False, "max_items": 20},      # entfernt
        {"id": "s4", "model_id": "m2", "label": "Opel Astra K", "enabled": True, "max_items": 20},      # Auftrag pausiert
    ]))
    # s2 hat schon einen wartenden Tagesplan-Job -> kein zweiter Lauf
    run(db[konfig.JOBS].insert_one({"id": "j-s2", "segment_id": "s2", "model_id": "m1", "tag": konfig.heute_tag(),
                                    "status": "queued", "job_type": "daily", "scheduled_at": konfig.jetzt_iso()}))


def test_01_vorschau_anlage_und_schutzregeln(wegwerf, monkeypatch):
    db, run = wegwerf.db, wegwerf.run
    _bestand(db, run)
    monkeypatch.setattr(MAJ.jobs, "intervall", _takt_ok)
    # Vorschau: zaehlt nur aktive Segmente aktiver Auftraege, legt nichts an
    e = run(MAJ.ausfuehren(db, ja=False))
    assert e["segmente"] == 2 and e["laeufe"] == 1 and e["kosten_usd"] > 0
    assert e["angelegt"] is False and e["grund"] is None and e["crawler_an"] is False
    assert run(db[konfig.JOBS].count_documents({"job_type": "manual"})) == 0
    # Crawler AUS: nichts anlegen (die Jobs wuerden nur warten)
    e = run(MAJ.ausfuehren(db, ja=True))
    assert e["angelegt"] is False and "Crawler ist AUS" in e["grund"]
    assert run(db[konfig.JOBS].count_documents({"job_type": "manual"})) == 0
    # Crawler an: s1 bekommt einen Sofort-Lauf, s2 wartet schon (uebersprungen), s3/s4 sind keine Kandidaten
    run(konfig.crawler_schalten(db, True, wer="test"))
    e = run(MAJ.ausfuehren(db, ja=True))
    assert e["angelegt"] is True and (e["neu"], e["uebersprungen"], e["fehler"]) == (1, 1, 0)
    man = run(db[konfig.JOBS].find_one({"job_type": "manual"}, {"_id": 0}))
    assert man["segment_id"] == "s1" and man["status"] == "queued"
    assert man["tag"].startswith(konfig.heute_tag() + "#") and man["scheduled_at"] <= konfig.jetzt_iso()
    # zweiter Aufruf direkt danach: s1 wartet jetzt selbst -> nichts Neues, kein Doppel-Lauf
    e = run(MAJ.ausfuehren(db, ja=True))
    assert (e["neu"], e["uebersprungen"]) == (0, 2)
    assert run(db[konfig.JOBS].count_documents({"job_type": "manual"})) == 1
    # Tagesplan unberuehrt: last_planned_tag bleibt leer, der normale Plan von morgen sieht nichts davon
    assert run(db[konfig.SEGMENTE].find_one({"id": "s1"}, {"_id": 0})).get("last_planned_tag") is None
    # ohne Budget: Abbruch mit Klartext
    monkeypatch.setattr(MAJ.jobs, "intervall", _takt_leer)
    e = run(MAJ.ausfuehren(db, ja=True))
    assert e["angelegt"] is False and "Monatsbudget" in e["grund"]


def test_02_main_ausgabe(wegwerf, monkeypatch, capsys):
    db, run = wegwerf.db, wegwerf.run
    _bestand(db, run)
    monkeypatch.setattr(MAJ.jobs, "intervall", _takt_ok)
    monkeypatch.setattr(MAJ, "DB_NAME", wegwerf.name)
    monkeypatch.setattr(MAJ, "MONGO_URL", MONGO_URL)
    monkeypatch.setattr(MAJ.konfig, "token", lambda: "")
    assert MAJ.main([]) == 2 and "APIFY_TOKEN fehlt" in capsys.readouterr().out
    monkeypatch.setattr(MAJ.konfig, "token", lambda: "test-token")
    assert MAJ.main(["--kaputt"]) == 2
    capsys.readouterr()
    assert MAJ.main([]) == 0
    aus = capsys.readouterr().out
    assert "Aktive Segmente (aktive Auftraege): 2" in aus and "Vorschau — nichts angelegt" in aus
    assert MAJ.main(["--ja"]) == 1
    assert "NICHT angelegt: Crawler ist AUS" in capsys.readouterr().out
    run(konfig.crawler_schalten(db, True, wer="test"))
    assert MAJ.main(["--ja"]) == 0
    aus = capsys.readouterr().out
    assert "Angelegt: 1 Sofort-Laeufe   uebersprungen (wartet/laeuft schon oder < 5 Min.): 1   Fehler: 0" in aus
    assert 'markt_fehlversuche.py --seit "' in aus
    assert run(db[konfig.JOBS].count_documents({"job_type": "manual"})) == 1
