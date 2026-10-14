# -*- coding: utf-8 -*-
"""Wunsch Ahmad 28.09.2026: "ein Testlauf, wo alle Autos jetzt einmalig gecrawlt werden; ab morgen wieder zu den
normalen Uhrzeiten" -> scripts/markt_alle_jetzt.py: heutigen Tagesplan sicherstellen, wartende erste Laeufe auf
jetzt vorziehen, Rest per Sofort-Lauf (markt.jobs.job_sofort); Vorschau ohne --ja, kein Doppel-Lauf, zweite
Laeufe (heute#2) und last_planned_tag unberuehrt, Abbruch bei Crawler AUS / ohne Budget.
In-process gegen eine Wegwerf-Datenbank (echtes Mongo), keine HTTP-Aufrufe, kein Apify."""
import asyncio
import os
import sys
import uuid
from datetime import timedelta
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
    return {"ohne_budget": False, "segmente_je_tag": 99, "segmente": 3, "intervall_tage": 1}


async def _takt_leer(db):
    return {"ohne_budget": True, "segmente_je_tag": 0, "segmente": 3, "intervall_tage": 0}


def _job(seg, tag, status, scheduled_at, job_type="daily"):
    return {"id": uuid.uuid4().hex, "segment_id": seg, "model_id": "m1", "tag": tag, "status": status,
            "job_type": job_type, "scheduled_at": scheduled_at, "max_items": 20, "attempts": 0}


def _bestand(db, run):
    heute = konfig.heute_tag()
    morgen = (konfig.jetzt() + timedelta(days=1)).isoformat()
    # wie in Produktion (indizes.markt_indizes): ein Job je Segment und Tag-Schluessel
    run(db[konfig.JOBS].create_index([("segment_id", 1), ("tag", 1)], unique=True, name="markt_job_je_tag"))
    run(db[konfig.MODELLE].insert_many([
        {"id": "m1", "label": "VW Golf VII", "enabled": True, "status": "active"},
        {"id": "m2", "label": "Opel Astra K", "enabled": False, "status": "paused"},
    ]))
    run(db[konfig.SEGMENTE].insert_many([
        # s1: heute schon gelaufen (Plan erledigt) -> kein wartender Job -> Sofort-Lauf
        {"id": "s1", "model_id": "m1", "label": "VW Golf VII", "enabled": True, "max_items": 20, "crawls_per_day": 1,
         "last_planned_tag": heute},
        # s2: 2x taeglich, beide Laeufe von heute warten noch -> erster wird vorgezogen, zweiter bleibt
        {"id": "s2", "model_id": "m1", "label": "VW Golf VII", "enabled": True, "max_items": 20, "crawls_per_day": 2,
         "last_planned_tag": heute},
        # s5: noch nie geplant -> der Tagesplan legt den heutigen Job an, der dann ab jetzt faellig ist
        {"id": "s5", "model_id": "m1", "label": "VW Golf VII", "enabled": True, "max_items": 20, "crawls_per_day": 1},
        {"id": "s3", "model_id": "m1", "label": "VW Golf VII", "enabled": False, "max_items": 20},      # entfernt
        # Auftrag pausiert: segmente.synchronisieren schaltet dessen Segmente ab (enabled False) — so auch hier
        {"id": "s4", "model_id": "m2", "label": "Opel Astra K", "enabled": False, "max_items": 20},
    ]))
    run(db[konfig.JOBS].insert_many([
        _job("s1", heute, "completed", konfig.jetzt_iso()),
        _job("s2", heute, "queued", morgen),
        _job("s2", f"{heute}#2", "queued", morgen),
    ]))


def test_01_vorschau_start_und_schutzregeln(wegwerf, monkeypatch):
    db, run = wegwerf.db, wegwerf.run
    _bestand(db, run)
    heute = konfig.heute_tag()
    monkeypatch.setattr(MAJ.jobs, "intervall", _takt_ok)
    # Vorschau: zaehlt nur aktive Segmente aktiver Auftraege, legt nichts an, zieht nichts vor
    e = run(MAJ.ausfuehren(db, ja=False))
    assert e["segmente"] == 3 and e["laeufe"] == 1 and e["kosten_usd"] > 0
    assert e["vorziehbar"] == 1 and e["ohne_job"] == 2                # s2 wartet; s1 und s5 ohne Job
    assert e["angelegt"] is False and e["grund"] is None and e["crawler_an"] is False
    assert run(db[konfig.JOBS].count_documents({})) == 3
    # Crawler AUS: nichts anlegen (die Jobs wuerden nur warten)
    e = run(MAJ.ausfuehren(db, ja=True))
    assert e["angelegt"] is False and "Crawler ist AUS" in e["grund"]
    assert run(db[konfig.JOBS].count_documents({})) == 3
    # Crawler an: Tagesplan ergaenzt s5, erste Laeufe vorgezogen, s1 bekommt einen Sofort-Lauf
    run(konfig.crawler_schalten(db, True, wer="test"))
    e = run(MAJ.ausfuehren(db, ja=True))
    assert e["angelegt"] is True and e["plan_neu"] == 1 and e["fehler"] == 0
    assert e["vorgezogen"] in (1, 2)          # s2 sicher; s5 nur, wenn sein Fensterplatz noch in der Zukunft lag
    assert (e["neu"], e["uebersprungen"]) == (1, 2)
    jetzt = konfig.jetzt_iso()
    # alle ersten Laeufe von heute sind jetzt faellig, der zweite Lauf von s2 bleibt in der Zukunft
    for j in run(db[konfig.JOBS].find({"status": "queued", "job_type": "daily", "tag": heute}, {"_id": 0}).to_list(None)):
        assert j["scheduled_at"] <= jetzt, j
    zweiter = run(db[konfig.JOBS].find_one({"segment_id": "s2", "tag": f"{heute}#2"}, {"_id": 0}))
    assert zweiter["status"] == "queued" and zweiter["scheduled_at"] > jetzt
    man = run(db[konfig.JOBS].find({"job_type": "manual"}, {"_id": 0}).to_list(None))
    assert len(man) == 1 and man[0]["segment_id"] == "s1" and man[0]["status"] == "queued"
    assert man[0]["tag"].startswith(heute + "#") and man[0]["scheduled_at"] <= jetzt
    # zweiter Aufruf direkt danach: alles wartet schon -> nichts Neues, nichts doppelt
    e = run(MAJ.ausfuehren(db, ja=True))
    assert (e["plan_neu"], e["vorgezogen"], e["neu"], e["uebersprungen"]) == (0, 0, 0, 3)
    assert run(db[konfig.JOBS].count_documents({"job_type": "manual"})) == 1
    # morgen laeuft der normale Plan: last_planned_tag ist hoechstens heute, nie in der Zukunft
    for s in run(db[konfig.SEGMENTE].find({"enabled": True, "model_id": "m1"}, {"_id": 0}).to_list(None)):
        assert (s.get("last_planned_tag") or "") <= heute
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
    assert "Aktive Segmente (aktive Auftraege): 3" in aus and "Vorschau — nichts gestartet" in aus
    assert "Wartende Plan-Jobs von heute, die vorgezogen wuerden: 1   Segmente ohne wartenden Job (bekaemen einen Sofort-Lauf): 2" in aus
    assert MAJ.main(["--ja"]) == 1
    assert "NICHT gestartet: Crawler ist AUS" in capsys.readouterr().out
    run(konfig.crawler_schalten(db, True, wer="test"))
    assert MAJ.main(["--ja"]) == 0
    aus = capsys.readouterr().out
    assert "Tagesplan ergaenzt: 1 Jobs   vorgezogen (erster Lauf von heute ab jetzt): " in aus
    assert "zusaetzliche Sofort-Laeufe: 1   uebersprungen (wartet/laeuft schon): 2   Fehler: 0" in aus
    assert "bleiben um 18 Uhr" in aus and 'markt_fehlversuche.py --seit "' in aus
    assert run(db[konfig.JOBS].count_documents({"job_type": "manual"})) == 1
