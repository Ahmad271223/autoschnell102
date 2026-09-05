# -*- coding: utf-8 -*-
"""Betriebsalarme (Pruefbericht 09/2026): Vorgaenge, die NICHT still
scheitern duerfen (bezahlt ohne Zugang, Datei nicht loeschbar, Vertrag ohne
dauerhaften Datensatz, Backup unvollstaendig, Migration fehlgeschlagen ...)
landen als offener Alarm in `betriebsalarme` und werden im Admin-Bereich
(/api/admin/betrieb) sowie in der Readiness-Pruefung sichtbar.

Schema: {id, typ, ref, details, created_at, offen: bool, quittiert_am,
         quittiert_von, anzahl (bei Wiederholung derselben typ+ref)}
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

log = logging.getLogger("autohandel.betrieb")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def alarm(db, typ: str, ref: str = "", **details) -> None:
    """Offenen Alarm anlegen; derselbe (typ, ref) wird nicht dupliziert,
    sondern hochgezaehlt. Darf selbst NIE eine Exception nach aussen
    werfen — der Alarm ist Beiwerk des eigentlichen Vorgangs."""
    try:
        clean = {k: (v if isinstance(v, (str, int, float, bool)) or v is None
                     else str(v)[:500]) for k, v in details.items()}
        r = await db.betriebsalarme.update_one(
            {"typ": typ, "ref": ref or "", "offen": True},
            {"$inc": {"anzahl": 1},
             "$set": {"details": clean, "zuletzt": _now()},
             "$setOnInsert": {"id": str(uuid.uuid4()), "typ": typ,
                              "ref": ref or "", "offen": True,
                              "created_at": _now()}},
            upsert=True)
        if r.upserted_id is not None:
            log.error("BETRIEBSALARM %s (%s): %s", typ, ref, clean)
    except Exception:
        log.exception("Betriebsalarm konnte nicht gespeichert werden: %s %s", typ, ref)


async def offene_alarme(db, limit: int = 200) -> list:
    return await db.betriebsalarme.find({"offen": True}, {"_id": 0}) \
        .sort("created_at", -1).to_list(limit)


async def quittieren(db, alarm_id: str, von: str) -> bool:
    r = await db.betriebsalarme.update_one(
        {"id": alarm_id, "offen": True},
        {"$set": {"offen": False, "quittiert_am": _now(), "quittiert_von": von}})
    return r.modified_count == 1
