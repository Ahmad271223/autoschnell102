# -*- coding: utf-8 -*-
"""Frage Ahmad 28.09.2026: "wie viele Segmente sind mehrfach fehlgeschlagen — welche Marke, Modell, Segmente
haben bislang nicht geklappt" -> scripts/markt_fehlversuche.py (nur lesen, keine Abrufe).
In-process gegen eine Wegwerf-Datenbank (echtes Mongo, pymongo), keine HTTP-Aufrufe."""
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pymongo import MongoClient

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "scripts"))

import markt_fehlversuche as MF  # noqa: E402

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"


def T(tage_zurueck: int) -> str:
    """Job-Tag relativ zu heute (UTC), damit --tage unabhaengig vom Kalender pruefbar ist."""
    return (datetime.now(timezone.utc) - timedelta(days=tage_zurueck)).strftime("%Y-%m-%d")


@pytest.fixture
def sync_db():
    name = f"autoschnell_mf_{uuid.uuid4().hex[:10]}"
    client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    try:
        yield client[name]
    finally:
        client.drop_database(name)
        client.close()


def _job(seg, model, status, tag, error=None, stunde="10"):
    return {"id": uuid.uuid4().hex, "segment_id": seg, "model_id": model, "tag": tag, "status": status,
            "error": error, "finished_at": f"{tag[:10]}T{stunde}:00:00+00:00", "job_type": "daily"}


def _bestand(db):
    db.market_models.insert_many([{"id": "m-golf", "label": "VW Golf VII", "status": "active"},
                                  {"id": "m-astra", "label": "Opel Astra K", "status": "paused"}])
    db.market_segments.insert_many([
        {"id": "s-a", "model_id": "m-golf", "label": "VW Golf VII", "ez_label": "EZ 2015–2017",
         "km_label": "100.000–150.000 km", "enabled": True},
        {"id": "s-b", "model_id": "m-golf", "label": "VW Golf VII", "ez_label": "EZ 2018", "km_label": "bis 50.000 km", "enabled": True},
        {"id": "s-c", "model_id": "m-golf", "label": "VW Golf VII", "ez_label": "EZ 2019", "km_label": "bis 50.000 km", "enabled": True},
        {"id": "s-d", "model_id": "m-astra", "label": "Opel Astra K", "ez_label": "EZ 2016", "km_label": "ab 150.000 km", "enabled": False},
    ])
    db.market_crawl_jobs.insert_many([
        # s-a: erst Fehler, dann ungueltig, zuletzt erfolgreich -> 2 Fehlversuche, inzwischen ok
        _job("s-a", "m-golf", "failed", T(8), "Lease abgelaufen (Prozess weg?)"),
        _job("s-a", "m-golf", "data_invalid", T(7), "Sortierung unsicher: Luecke bei 3"),
        _job("s-a", "m-golf", "completed", T(6)),
        # s-b: dreimal ungueltig, juengster Lauf kaputt -> zuletzt gescheitert; ein Storno zaehlt nicht
        _job("s-b", "m-golf", "data_invalid", T(2), "Sortierung unsicher: Luecke bei 1"),
        _job("s-b", "m-golf", "data_invalid", T(1), "Sortierung unsicher"),
        _job("s-b", "m-golf", "data_invalid", T(0), "Sortierung unsicher: Luecke bei 2"),
        _job("s-b", "m-golf", "cancelled", f"{T(0)}#2", "Crawler aus", stunde="12"),
        # s-c: nur Erfolge -> taucht nicht auf
        _job("s-c", "m-golf", "completed", T(0)),
        # s-d (anderer Auftrag, Segment inaktiv): ein einzelner technischer Fehler, lange her
        _job("s-d", "m-astra", "failed", T(18), "Apify: 502"),
    ])


def test_01_bericht_zaehlt_segmente_und_ordnet_je_modell(sync_db):
    _bestand(sync_db)
    b = MF.bericht(sync_db)
    assert b["laeufe"] == 6 and b["laeufe_failed"] == 2 and b["laeufe_invalid"] == 4
    assert b["segmente_aktiv"] == 3 and b["segmente_mit_fehler"] == 3
    assert b["mehrfach"] == 2 and b["dreifach"] == 1
    # s-b (juengster Lauf ungueltig) und s-d (einziger Lauf ein Fehler) sind "zuletzt gescheitert", s-a hat sich erholt
    assert b["zuletzt_gescheitert"] == 2 and b["inzwischen_ok"] == 1
    assert b["nur_ungueltig"] == 1 and b["nur_fehler"] == 1
    assert b["gruende"][0] == ("Sortierung unsicher", 4)
    assert [z["label"] for z in b["je_modell"]] == ["VW Golf VII", "Opel Astra K"]
    golf = b["je_modell"][0]
    assert golf["n"] == 5 and golf["zuletzt_gescheitert"] == 1 and golf["status"] == "active"
    # zuletzt gescheiterte Segmente zuerst, dann nach Zahl der Fehlversuche
    assert [e["segment_id"] for e in golf["segmente"]] == ["s-b", "s-a"]
    sb = golf["segmente"][0]
    assert sb["n"] == 3 and sb["invalid"] == 3 and sb["failed"] == 0 and sb["zuletzt_gescheitert"] is True
    assert sb["letzter_tag"] == T(0) and sb["letzter_grund"] == "Sortierung unsicher: Luecke bei 2"
    sa = golf["segmente"][1]
    assert sa["n"] == 2 and sa["zuletzt_gescheitert"] is False and sa["letzter_lauf_tag"] == T(6)
    astra = b["je_modell"][1]
    assert astra["segmente"][0]["enabled"] is False and astra["status"] == "paused"
    # --tage grenzt ueber den Job-Tag ein: letzte 3 Tage -> nur s-b (3 Laeufe)
    b3 = MF.bericht(sync_db, tage=3)
    assert b3["ab_tag"] == T(3) and b3["segmente_mit_fehler"] == 1 and b3["laeufe"] == 3
    assert MF.bericht(sync_db, tage=10)["segmente_mit_fehler"] == 2        # s-a (Tag 7/8) dazu, s-d (Tag 18) nicht


def test_02_ausgabe_und_parameter(sync_db, monkeypatch, capsys):
    _bestand(sync_db)
    monkeypatch.setattr(MF, "DB_NAME", sync_db.name)
    monkeypatch.setattr(MF, "MONGO_URL", MONGO_URL)
    assert MF.main([]) == 0
    aus = capsys.readouterr().out
    assert "Fehlversuche: 6 Laeufe in 3 Segmenten (von 3 aktiven)" in aus
    assert "mehrfach (>= 2): 2 Segmente, >= 3: 1" in aus
    assert "zuletzt gescheitert (juengster Lauf kaputt): 2   inzwischen ok: 1" in aus
    assert "4 x Sortierung unsicher" in aus
    assert "VW Golf VII (active) — 5 Fehlversuche in 2 Segmenten, 1 zuletzt gescheitert" in aus
    assert f"EZ 2018 · bis 50.000 km: 3 Fehlversuch(e) (3 ungueltig), letzter {T(0)} — ZULETZT GESCHEITERT" in aus
    assert (f"EZ 2015–2017 · 100.000–150.000 km: 2 Fehlversuch(e) (1 ungueltig, 1 Fehler), letzter {T(7)} — "
            f"inzwischen ok (letzter Lauf {T(6)} erfolgreich)") in aus
    assert "Opel Astra K" not in aus            # nur ein Fehlversuch: erst mit --alle
    assert MF.main(["--alle"]) == 0
    aus = capsys.readouterr().out
    assert "Opel Astra K (paused) — 1 Fehlversuche in 1 Segmenten, 1 zuletzt gescheitert" in aus
    assert f"EZ 2016 · ab 150.000 km [Segment inaktiv]: 1 Fehlversuch(e) (1 Fehler), letzter {T(18)} — ZULETZT GESCHEITERT" in aus
    assert MF.main(["--tage", "3", "--alle"]) == 0
    aus = capsys.readouterr().out
    assert "letzte 3 Tage" in aus and "Fehlversuche: 3 Laeufe in 1 Segmenten" in aus
    assert MF.main(["--kaputt"]) == 2
    assert MF.grund_kurz("Sortierung unsicher: Luecke") == "Sortierung unsicher"
    assert MF.grund_kurz("Lease abgelaufen — erneut") == "Lease abgelaufen"
    assert MF.grund_kurz("") == "(ohne Grund)"
