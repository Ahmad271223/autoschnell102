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
import os
import uuid
from datetime import datetime, timedelta, timezone

from konfig import zahl_env

log = logging.getLogger("autohandel.betrieb")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# Pruefbericht 20.09.2026 (AL-04/U5): Die Alarmsammlung hatte keine Grenze —
# ein Angriff auf 50.000 Kontonummern erzeugte 50.000 dauerhaft offene Alarme.
# Je Typ hoechstens so viele offene Einzelalarme; darueber zaehlt ein
# Sammelalarm (ref "*weitere*") weiter. Geschlossene Alarme verfallen nach
# ALARM_AUFBEWAHRUNG_TAGE (TTL-Index auf loeschen_ab, siehe server.py).
ALARM_JE_TYP_MAX = zahl_env("ALARM_JE_TYP_MAX", 100, unten=10)
ALARM_AUFBEWAHRUNG_TAGE = zahl_env("ALARM_AUFBEWAHRUNG_TAGE", 90, unten=7)
SAMMEL_REF = "*weitere*"


def _loeschen_ab() -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=ALARM_AUFBEWAHRUNG_TAGE)


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
            if (ref or "") != SAMMEL_REF:
                offen = await db.betriebsalarme.count_documents(
                    {"typ": typ, "offen": True, "ref": {"$ne": SAMMEL_REF}},
                    limit=ALARM_JE_TYP_MAX + 1)
                if offen > ALARM_JE_TYP_MAX:
                    # Ueber der Grenze: diesen Einzelalarm zurueck in den
                    # Sammelalarm falten (zaehlt weiter, verdraengt nichts).
                    await db.betriebsalarme.delete_one({"_id": r.upserted_id})
                    await db.betriebsalarme.update_one(
                        {"typ": typ, "ref": SAMMEL_REF, "offen": True},
                        {"$inc": {"anzahl": 1},
                         "$set": {"details": {**clean, "letzter_ref": (ref or "")[:80]},
                                  "zuletzt": _now()},
                         "$setOnInsert": {"id": str(uuid.uuid4()), "typ": typ,
                                          "ref": SAMMEL_REF, "offen": True,
                                          "created_at": _now()}},
                        upsert=True)
    except Exception:
        log.exception("Betriebsalarm konnte nicht gespeichert werden: %s %s", typ, ref)


async def alarm_schliessen(db, typ: str, ref: str = "") -> int:
    """Offenen Alarm (typ, ref) schliessen, wenn die Ursache behoben ist
    (z.B. Index inzwischen angelegt). Wirft nie."""
    try:
        r = await db.betriebsalarme.update_many(
            {"typ": typ, "ref": ref or "", "offen": True},
            {"$set": {"offen": False, "quittiert_am": _now(),
                      "quittiert_von": "system:behoben", "loeschen_ab": _loeschen_ab()}})
        return r.modified_count
    except Exception:
        log.exception("Betriebsalarm konnte nicht geschlossen werden: %s %s", typ, ref)
        return 0


async def offene_alarme(db, limit: int = 200) -> list:
    """Offene Alarme, zuletzt aufgetretene zuerst.

    AL-02/U4: sortiert wurde nach dem ERSTEN Auftreten — ein Sicherungsfehler,
    der seit drei Tagen jede Nacht wiederkommt, rutschte hinter hunderte
    frische Anmelde-Alarme und fiel aus der Liste."""
    return await db.betriebsalarme.find({"offen": True}, {"_id": 0}) \
        .sort([("zuletzt", -1), ("created_at", -1)]).to_list(limit)


async def alarm_uebersicht(db) -> dict:
    """Alle offenen Alarme je Typ (Anzahl Eintraege, Summe der Wiederholungen,
    zuletzt) — vollstaendig, auch wenn die Liste gekuerzt ist."""
    je_typ = []
    gesamt = 0
    async for g in db.betriebsalarme.aggregate([
            {"$match": {"offen": True}},
            {"$group": {"_id": "$typ", "eintraege": {"$sum": 1},
                        "vorkommen": {"$sum": {"$ifNull": ["$anzahl", 1]}},
                        "zuletzt": {"$max": {"$ifNull": ["$zuletzt", "$created_at"]}}}},
            {"$sort": {"zuletzt": -1}}]):
        gesamt += g["eintraege"]
        je_typ.append({"typ": g["_id"], "eintraege": g["eintraege"],
                       "vorkommen": g["vorkommen"], "zuletzt": g["zuletzt"]})
    return {"gesamt": gesamt, "je_typ": je_typ}


async def quittieren(db, alarm_id: str, von: str) -> bool:
    r = await db.betriebsalarme.update_one(
        {"id": alarm_id, "offen": True},
        {"$set": {"offen": False, "quittiert_am": _now(), "quittiert_von": von,
                  "loeschen_ab": _loeschen_ab()}})
    return r.modified_count == 1
