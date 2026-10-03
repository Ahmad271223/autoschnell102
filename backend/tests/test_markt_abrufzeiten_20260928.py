# -*- coding: utf-8 -*-
"""Entscheidung Ahmad 28.09.2026: Segmente mit 2 Abrufen je Tag laufen zu festen Uhrzeiten (12 und 18 Uhr
deutscher Zeit, MARKT_ABRUF2_UHRZEITEN); 1x-Segmente weiter im Crawl-Fenster (bei Ahmad 9-18 Uhr); das
Nachplanen nach einer Aktivierung (sofort=True) behaelt die alte Abstandsregel."""
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_befunde_runde17_termine import _module, welt  # noqa: E402,F401
from test_markt_20260926 import JOBS, K, _aufraeumen, _modell, _segment  # noqa: E402


def test_01_abruf2_uhrzeiten_aus_umgebung(monkeypatch):
    monkeypatch.delenv("MARKT_ABRUF2_UHRZEITEN", raising=False)
    assert K.abruf2_uhrzeiten() == (12, 18)
    monkeypatch.setenv("MARKT_ABRUF2_UHRZEITEN", "16, 10")
    assert K.abruf2_uhrzeiten() == (10, 16)
    for kaputt in ("abc", "12", "12,18,20", "12,25", "-1,5", ""):
        monkeypatch.setenv("MARKT_ABRUF2_UHRZEITEN", kaputt)
        assert K.abruf2_uhrzeiten() == (12, 18), kaputt
    # deutsche Zeit -> UTC (Sommerzeit: 12 Uhr = 10 Uhr UTC; Winterzeit: 11 Uhr UTC)
    assert K.uhrzeit("2027-07-01", 12).strftime("%H:%M%z") == "10:00+0000"
    assert K.uhrzeit("2027-01-15", 12).strftime("%H:%M%z") == "11:00+0000"


def test_02_tagesplan_zwei_abrufe_um_12_und_18_uhr(welt, monkeypatch):
    w, db = welt.w, welt.db
    _aufraeumen(welt)
    monkeypatch.setenv("MARKT_BUDGET_MONAT_USD", "100")
    monkeypatch.setenv("MARKT_CRAWL_INTERVALL_TAGE", "1")
    monkeypatch.setenv("MARKT_CRAWL_FENSTER_VON", "9")
    monkeypatch.setenv("MARKT_CRAWL_FENSTER_BIS", "18")
    monkeypatch.delenv("MARKT_ABRUF2_UHRZEITEN", raising=False)
    monkeypatch.setattr(K, "monat", lambda zeit=None: f"test-{w.s}")
    seg1 = {**_segment(w), "crawls_per_day": 1}
    seg2 = {**_segment(w), "id": f"test-320d-{w.s}:2019-2021:85001-115000", "min_km": 85001, "max_km": 115000,
            "crawls_per_day": 2}
    welt.run(db[K.MODELLE].insert_one({**_modell(w), "crawls_per_day": 2}))
    welt.run(db[K.SEGMENTE].insert_many([dict(seg1), dict(seg2)]))
    tag = f"2098-03-{int(w.s[:1], 16) % 28 + 1:02d}"
    try:
        welt.run(JOBS.tagesplan(db, tag))
        j1 = welt.run(db[K.JOBS].find({"segment_id": seg1["id"]}, {"_id": 0}).to_list(5))
        j2 = welt.run(db[K.JOBS].find({"segment_id": seg2["id"]}, {"_id": 0}).sort("scheduled_at", 1).to_list(5))
        assert len(j1) == 1 and len(j2) == 2 and j2[0]["tag"] == tag and j2[1]["tag"] == f"{tag}#2"
        # 1x taeglich: im Fenster 9-18 Uhr deutscher Zeit
        e1 = datetime.fromisoformat(j1[0]["scheduled_at"])
        assert K.uhrzeit(tag, 9) <= e1 <= K.uhrzeit(tag, 18)
        # 2x taeglich: 12 und 18 Uhr (Buendel bis 30 Minuten gestaffelt), nicht "~12 h nach dem ersten"
        d0, d1 = datetime.fromisoformat(j2[0]["scheduled_at"]), datetime.fromisoformat(j2[1]["scheduled_at"])
        assert timedelta(0) <= d0 - K.uhrzeit(tag, 12) < timedelta(minutes=30)
        assert timedelta(0) <= d1 - K.uhrzeit(tag, 18) < timedelta(minutes=30)
        assert d1 - d0 == timedelta(hours=6)
        # eigene Uhrzeiten aus der Umgebung
        welt.run(db[K.JOBS].delete_many({"segment_id": seg2["id"]}))
        welt.run(db[K.SEGMENTE].update_one({"id": seg2["id"]}, {"$unset": {"last_planned_tag": ""}}))
        monkeypatch.setenv("MARKT_ABRUF2_UHRZEITEN", "10,16")
        welt.run(JOBS.tagesplan(db, tag))
        j2 = welt.run(db[K.JOBS].find({"segment_id": seg2["id"]}, {"_id": 0}).sort("scheduled_at", 1).to_list(5))
        d0, d1 = datetime.fromisoformat(j2[0]["scheduled_at"]), datetime.fromisoformat(j2[1]["scheduled_at"])
        assert timedelta(0) <= d0 - K.uhrzeit(tag, 10) < timedelta(minutes=30) and d1 - d0 == timedelta(hours=6)
        # Nachplanen nach Aktivierung (sofort=True): ab jetzt, zweiter Lauf nach der Abstandsregel, vor 23:30
        welt.run(db[K.JOBS].delete_many({"segment_id": seg2["id"]}))
        welt.run(db[K.SEGMENTE].update_one({"id": seg2["id"]}, {"$unset": {"last_planned_tag": ""}}))
        heute = K.heute_tag()
        vorher = K.jetzt()
        welt.run(JOBS.tagesplan(db, heute, sofort=True))
        j2 = welt.run(db[K.JOBS].find({"segment_id": seg2["id"]}, {"_id": 0}).sort("scheduled_at", 1).to_list(5))
        assert len(j2) == 2
        d0, d1 = datetime.fromisoformat(j2[0]["scheduled_at"]), datetime.fromisoformat(j2[1]["scheduled_at"])
        assert abs((d0 - vorher).total_seconds()) < 120 and d1 <= K.tag_ende(heute) and d1 > d0
    finally:
        _aufraeumen(welt)
