# -*- coding: utf-8 -*-
"""Frage Ahmad 29.09.2026 ("was fehlt da"): die Kachel Taktung nannte die Vorbelegung 2x (alle Segmente laufen 1x),
die Kopfzeile 10 Zeilen (bestellt 5), "Naechster Lauf" stand nach dem letzten Lauf des Tages auf "—".
Jetzt liefert markt.abfrage.status: abrufe_je_tag (Segmente je Abrufe/Tag), zeilen_je_segment (min/max der
bestellten Zeilen) und naechster_plan_at (Fensterbeginn heute, sonst morgen)."""
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_befunde_runde17_termine import _module, welt  # noqa: E402,F401
from test_markt_20260926 import K, _aufraeumen  # noqa: E402

ABF = _module("markt.abfrage")


def test_01_segment_profil_und_naechster_plan(welt):
    w, db = welt.w, welt.db
    _aufraeumen(welt)
    s = w.s
    segs = [{"id": f"test-prof-{s}:{i}", "model_id": f"test-prof-{s}", "enabled": True, "max_items": 5, "crawls_per_day": 1}
            for i in range(3)]
    segs += [{"id": f"test-prof-{s}:z", "model_id": f"test-prof-{s}", "enabled": True, "max_items": 10, "crawls_per_day": 2},
             {"id": f"test-prof-{s}:aus", "model_id": f"test-prof-{s}", "enabled": False, "max_items": 99, "crawls_per_day": 4},
             {"id": f"test-prof-{s}:ohne", "model_id": f"test-prof-{s}", "enabled": True, "max_items": 5}]     # ohne Angabe = 1x
    welt.run(db[K.SEGMENTE].insert_many(segs))
    try:
        vorher = welt.run(ABF.segment_profil(db))
        # eigene Segmente: 4x 1/Tag (3 + ohne Angabe), 1x 2/Tag; das abgeschaltete zaehlt nicht (sonst max 99)
        assert vorher["abrufe_je_tag"].get("1", 0) >= 4 and vorher["abrufe_je_tag"].get("2", 0) >= 1
        z = vorher["zeilen_je_segment"]
        assert z["min"] is not None and z["min"] <= 5 and 10 <= z["max"] < 99
        st = welt.run(ABF.status(db))
        assert st["abrufe_je_tag"] == vorher["abrufe_je_tag"] and st["zeilen_je_segment"] == z
        # naechster Tagesplan: Fensterbeginn (deutsche Zeit) in der Zukunft, hoechstens ~24 h entfernt
        naechster = datetime.fromisoformat(st["naechster_plan_at"])
        jetzt = K.jetzt()
        assert jetzt < naechster <= jetzt + timedelta(hours=24, minutes=1)
        assert naechster == K.fenster_start(K.heute_tag(naechster))
    finally:
        welt.run(db[K.SEGMENTE].delete_many({"model_id": f"test-prof-{s}"}))
        _aufraeumen(welt)


def test_02_leeres_profil():
    """Ohne aktive Segmente: leere Verteilung, keine Zeilen-Spanne — die Oberflaeche faellt auf die Vorbelegung zurueck."""
    import asyncio
    from motor.motor_asyncio import AsyncIOMotorClient
    import os
    client = AsyncIOMotorClient(os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017", serverSelectionTimeoutMS=5000)
    name = f"autoschnell_prof_{uuid.uuid4().hex[:8]}"
    loop = asyncio.new_event_loop()
    try:
        p = loop.run_until_complete(ABF.segment_profil(client[name]))
        assert p == {"abrufe_je_tag": {}, "zeilen_je_segment": {"min": None, "max": None}}
    finally:
        loop.run_until_complete(client.drop_database(name))
        client.close()
        loop.close()
