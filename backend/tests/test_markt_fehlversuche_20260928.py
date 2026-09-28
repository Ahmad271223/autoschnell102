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


def Z(tage_zurueck: int, stunde: str = "10") -> str:
    """finished_at (UTC-ISO) eines Laufs an Tag T(n) um <stunde> Uhr UTC."""
    return f"{T(tage_zurueck)}T{stunde}:00:00+00:00"


@pytest.fixture
def sync_db():
    name = f"autoschnell_mf_{uuid.uuid4().hex[:10]}"
    client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    try:
        yield client[name]
    finally:
        client.drop_database(name)
        client.close()


def _job(seg, model, status, tage, error=None, stunde="10", tag_zusatz=""):
    return {"id": uuid.uuid4().hex, "segment_id": seg, "model_id": model, "tag": T(tage) + tag_zusatz,
            "status": status, "error": error, "finished_at": Z(tage, stunde), "job_type": "daily"}


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
        _job("s-a", "m-golf", "failed", 8, "Lease abgelaufen (Prozess weg?)"),
        _job("s-a", "m-golf", "data_invalid", 7, "Sortierung unsicher: Luecke bei 3"),
        _job("s-a", "m-golf", "completed", 6),
        # s-b: dreimal ungueltig (zweimal Laenderfehler), juengster Lauf kaputt -> zuletzt gescheitert; Storno zaehlt nicht
        _job("s-b", "m-golf", "data_invalid", 2, "alle Zeilen verworfen (20 von 20: land IT != DE; land IT != DE; land IT != DE)"),
        _job("s-b", "m-golf", "data_invalid", 1, "alle Zeilen verworfen (1 von 1: land NL != DE)"),
        _job("s-b", "m-golf", "data_invalid", 0, "Sortierung unsicher: Luecke bei 2"),
        _job("s-b", "m-golf", "cancelled", 0, "Crawler aus", stunde="12", tag_zusatz="#2"),
        # s-c: nur Erfolge -> taucht nicht auf
        _job("s-c", "m-golf", "completed", 0),
        # s-d (anderer Auftrag, Segment inaktiv): ein einzelner technischer Fehler, lange her
        _job("s-d", "m-astra", "failed", 18, "Apify: 502"),
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
    # Gruende auf ihre Familie gekuerzt (Zahlen und Laender-Liste weg), Laender je Lauf einmal
    assert dict(b["gruende"]) == {"Sortierung unsicher": 2, "alle Zeilen verworfen": 2, "Lease abgelaufen": 1, "Apify": 1}
    assert b["laender"] == [("IT", 1), ("NL", 1)]
    assert len(b["je_tag"]) == 6 and b["je_tag"][-1] == (T(0), 1)
    assert [z["label"] for z in b["je_modell"]] == ["VW Golf VII", "Opel Astra K"]
    golf = b["je_modell"][0]
    assert golf["n"] == 5 and golf["zuletzt_gescheitert"] == 1 and golf["status"] == "active"
    # zuletzt gescheiterte Segmente zuerst, dann nach Zahl der Fehlversuche
    assert [e["segment_id"] for e in golf["segmente"]] == ["s-b", "s-a"]
    sb = golf["segmente"][0]
    assert sb["n"] == 3 and sb["invalid"] == 3 and sb["failed"] == 0 and sb["zuletzt_gescheitert"] is True
    assert sb["letzter_tag"] == T(0) and sb["letzter_grund"] == "Sortierung unsicher: Luecke bei 2"
    assert sb["letzte_zeit"] == MF.lokal(Z(0)) and sb["letzte_zeit"].startswith(T(0))
    sa = golf["segmente"][1]
    assert sa["n"] == 2 and sa["zuletzt_gescheitert"] is False and sa["letzter_lauf_tag"] == T(6)
    assert sa["letzter_lauf_zeit"] == MF.lokal(Z(6))
    astra = b["je_modell"][1]
    assert astra["segmente"][0]["enabled"] is False and astra["status"] == "paused"
    # --tage grenzt ueber den Job-Tag ein: letzte 3 Tage -> nur s-b (3 Laeufe)
    b3 = MF.bericht(sync_db, tage=3)
    assert b3["ab_tag"] == T(3) and b3["segmente_mit_fehler"] == 1 and b3["laeufe"] == 3
    assert MF.bericht(sync_db, tage=10)["segmente_mit_fehler"] == 2        # s-a (Tag 7/8) dazu, s-d (Tag 18) nicht
    # --seit grenzt ueber das Laufende (deutsche Zeit) ein: ab gestern 00:00 -> die zwei juengsten s-b-Laeufe
    bs = MF.bericht(sync_db, seit=f"{T(1)} 00:00")
    assert bs["laeufe"] == 2 and bs["segmente_mit_fehler"] == 1 and bs["seit"] == f"{T(1)} 00:00"
    assert MF.bericht(sync_db, seit=f"{T(0)} 23:59")["laeufe"] == 0


def test_02_ausgabe_und_parameter(sync_db, monkeypatch, capsys):
    _bestand(sync_db)
    monkeypatch.setattr(MF, "DB_NAME", sync_db.name)
    monkeypatch.setattr(MF, "MONGO_URL", MONGO_URL)
    assert MF.main([]) == 0
    aus = capsys.readouterr().out
    assert "Zeitraum: alle Tage" in aus
    assert "Fehlversuche: 6 Laeufe in 3 Segmenten (von 3 aktiven)" in aus
    assert "mehrfach (>= 2): 2 Segmente, >= 3: 1" in aus
    assert "zuletzt gescheitert (juengster Lauf kaputt): 2   inzwischen ok: 1" in aus
    assert f"je Tag: {T(18)}: 1, {T(8)}: 1" in aus and f"{T(0)}: 1" in aus
    assert "2 x Sortierung unsicher" in aus and "2 x alle Zeilen verworfen" in aus
    assert "Verworfene Laender (Laeufe, in denen das Land vorkam): IT 1, NL 1" in aus
    assert "VW Golf VII (active) — 5 Fehlversuche in 2 Segmenten, 1 zuletzt gescheitert" in aus
    assert f"EZ 2018 · bis 50.000 km: 3 Fehlversuch(e) (3 ungueltig), letzter {MF.lokal(Z(0))} — ZULETZT GESCHEITERT" in aus
    assert (f"EZ 2015–2017 · 100.000–150.000 km: 2 Fehlversuch(e) (1 ungueltig, 1 Fehler), letzter {MF.lokal(Z(7))} — "
            f"inzwischen ok (letzter Lauf {MF.lokal(Z(6))} erfolgreich)") in aus
    assert "Opel Astra K" not in aus            # nur ein Fehlversuch: erst mit --alle
    assert MF.main(["--alle"]) == 0
    aus = capsys.readouterr().out
    assert "Opel Astra K (paused) — 1 Fehlversuche in 1 Segmenten, 1 zuletzt gescheitert" in aus
    assert (f"EZ 2016 · ab 150.000 km [Segment inaktiv]: 1 Fehlversuch(e) (1 Fehler), letzter {MF.lokal(Z(18))} — "
            "ZULETZT GESCHEITERT") in aus
    assert MF.main(["--tage", "3", "--alle"]) == 0
    aus = capsys.readouterr().out
    assert "letzte 3 Tage" in aus and "Fehlversuche: 3 Laeufe in 1 Segmenten" in aus
    assert MF.main(["--seit", f"{T(1)} 00:00"]) == 0
    aus = capsys.readouterr().out
    assert f"Laeufe beendet seit {T(1)} 00:00 Uhr" in aus and "Fehlversuche: 2 Laeufe in 1 Segmenten" in aus
    assert MF.main(["--seit", "gestern"]) == 2
    assert "--seit erwartet" in capsys.readouterr().out
    assert MF.main(["--kaputt"]) == 2
    assert MF.grund_kurz("Sortierung unsicher: Luecke") == "Sortierung unsicher"
    assert MF.grund_kurz("alle Zeilen verworfen (20 von 20: land IT != DE; land IT != DE)") == "alle Zeilen verworfen"
    assert MF.grund_kurz("Lease abgelaufen — erneut") == "Lease abgelaufen"
    assert MF.grund_kurz("") == "(ohne Grund)"
    assert MF.laender("alle Zeilen verworfen (3 von 3: land IT != DE; land NL != DE; land IT != DE)") == ["IT", "NL"]
    assert MF.lokal("2026-09-28T14:25:00+00:00") == "2026-09-28 16:25"     # Sommerzeit
    assert MF.lokal("2026-12-01T14:25:00+00:00") == "2026-12-01 15:25"     # Winterzeit
    assert MF.lokal(None) == "" and MF.lokal("kaputt") == "kaputt"
    assert MF.seit_utc("2026-09-28 18:40") == "2026-09-28T16:40:00+00:00"
