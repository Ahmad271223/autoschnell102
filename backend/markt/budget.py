# -*- coding: utf-8 -*-
"""Monatsbudget des Market-Crawlers (eigen, getrennt vom KI-Budget):
market_crawler_budget je Monat mit budget_usd, reserved_usd, used_usd,
rows, runs. Vor einem Lauf wird geschaetzt reserviert (atomar, $expr),
nach dem Lauf mit den echten Kosten abgerechnet. Voll = keine weiteren
automatischen Crawls; alles andere in AutoSchnell laeuft weiter.

Review 26.09.2026 Nr. 47: jede Reservierung ist ein Eintrag
{id, usd, expires_at} im Array 'reservierungen' des Monatsdokuments;
reserved_usd ist die Summe der offenen Eintraege ($inc beim Anlegen und
Loesen). Stirbt der Worker zwischen Reservieren und Abrechnen, gibt
verfallene_freigeben() den Eintrag nach Ablauf (Lease + 60 s) wieder frei —
Aufruf vor jedem Claim (jobs.einmal) und im Aufraeumlauf."""
from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any, Dict, List, Optional

from pymongo import ReturnDocument

from markt import konfig


async def dokument(db, monat: Optional[str] = None) -> Dict[str, Any]:
    m = monat or konfig.monat()
    await db[konfig.BUDGET].update_one(
        {"_id": m},
        {"$setOnInsert": {"budget_usd": konfig.budget_monat_usd(), "reserved_usd": 0.0, "used_usd": 0.0,
                          "rows": 0, "runs": 0, "reservierungen": [], "angelegt": konfig.jetzt_iso()}},
        upsert=True)
    d = await db[konfig.BUDGET].find_one({"_id": m}) or {}
    d["frei_usd"] = round(float(d.get("budget_usd") or 0) - float(d.get("used_usd") or 0) - float(d.get("reserved_usd") or 0), 4)
    d["reservierungen_offen"] = len(d.get("reservierungen") or [])
    return d


async def budget_setzen(db, budget_usd: float, monat: Optional[str] = None) -> None:
    await dokument(db, monat)
    await db[konfig.BUDGET].update_one({"_id": monat or konfig.monat()}, {"$set": {"budget_usd": float(budget_usd)}})


async def reservieren(db, est_usd: float, *, ablauf_s: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """{"monat", "est_usd", "id", "expires_at"} oder None (Budget voll). Atomar: nur wenn
    used + reserved + est <= budget. ablauf_s: Verfall der Reservierung (Standard
    konfig.reservierung_ablauf_s = Lease + 60 s)."""
    m = konfig.monat()
    await dokument(db, m)
    est = round(float(est_usd), 4)
    rid = uuid.uuid4().hex
    jetzt = konfig.jetzt()
    ablauf = (jetzt + timedelta(seconds=int(ablauf_s if ablauf_s is not None else konfig.reservierung_ablauf_s()))).isoformat()
    eintrag = {"id": rid, "usd": est, "angelegt": jetzt.isoformat(), "expires_at": ablauf}
    doc = await db[konfig.BUDGET].find_one_and_update(
        {"_id": m, "$expr": {"$lte": [{"$add": [{"$ifNull": ["$used_usd", 0]}, {"$ifNull": ["$reserved_usd", 0]}, est]},
                                      {"$ifNull": ["$budget_usd", 0]}]}},
        {"$inc": {"reserved_usd": est}, "$set": {"stand": jetzt.isoformat()}, "$push": {"reservierungen": eintrag}},
        return_document=ReturnDocument.AFTER)
    if doc is None:
        return None
    return {"monat": m, "est_usd": est, "id": rid, "expires_at": ablauf}


async def abrechnen(db, reservierung: Optional[Dict[str, Any]], tatsaechlich_usd: Optional[float],
                    rows: int = 0, gelaufen: bool = True, runs: Optional[int] = None) -> None:
    """Reservierung durch echte Kosten ersetzen (None = Schaetzung behalten).
    Loest genau DIESE Reservierung (Nr. 47). Hat der Reaper sie inzwischen freigegeben,
    wird reserved_usd nicht ein zweites Mal gesenkt — die echten Kosten zaehlen trotzdem.
    Reparaturwelle 5 Nr. 56/57: `runs` = Zahl der ECHTEN Actor-Starts (Standard + Ersatz je URL),
    `rows` = gelieferte Datensatz-Zeilen (roh, vor Dedupe/Filter) — so, wie Apify sie berechnet."""
    if not reservierung:
        return
    est = float(reservierung.get("est_usd") or 0)
    real = round(float(tatsaechlich_usd if tatsaechlich_usd is not None else (est if gelaufen else 0.0)), 4)
    starts = int(runs) if runs is not None else (1 if gelaufen else 0)
    verbrauch = {"used_usd": real, "rows": int(rows), "runs": max(0, starts)}
    rid = reservierung.get("id")
    if rid:
        r = await db[konfig.BUDGET].update_one(
            {"_id": reservierung["monat"], "reservierungen.id": rid},
            {"$inc": {"reserved_usd": -est, **verbrauch}, "$pull": {"reservierungen": {"id": rid}}})
        if r.matched_count == 1:
            return
        # Reservierung schon verfallen/freigegeben: nur den Verbrauch buchen
        await db[konfig.BUDGET].update_one({"_id": reservierung["monat"]}, {"$inc": verbrauch})
        return
    # alte Reservierung ohne id (vor Nr. 47): wie bisher
    await db[konfig.BUDGET].update_one({"_id": reservierung["monat"]}, {"$inc": {"reserved_usd": -est, **verbrauch}})


async def verfallene_freigeben(db) -> Dict[str, Any]:
    """Reaper (Nr. 47): abgelaufene Reservierungen loesen (je Eintrag atomar: $pull + $inc),
    danach reserved_usd = Summe der offenen Eintraege — falls beide Zahlen auseinanderlaufen
    (nur wenn sich das Array zwischenzeitlich nicht geaendert hat, CAS auf das Array)."""
    jetzt = konfig.jetzt_iso()
    freigegeben: List[Dict[str, Any]] = []
    korrigiert = 0
    async for d in db[konfig.BUDGET].find({"reservierungen.0": {"$exists": True}}, {"reservierungen": 1, "reserved_usd": 1}):
        for e in list(d.get("reservierungen") or []):
            if str(e.get("expires_at") or "") >= jetzt:
                continue
            r = await db[konfig.BUDGET].update_one(
                {"_id": d["_id"], "reservierungen.id": e.get("id")},
                {"$pull": {"reservierungen": {"id": e.get("id")}}, "$inc": {"reserved_usd": -float(e.get("usd") or 0)},
                 "$set": {"letzter_verfall": jetzt}})
            if r.matched_count == 1:
                freigegeben.append({"monat": d["_id"], "id": e.get("id"), "usd": float(e.get("usd") or 0)})
    # reserved_usd gegen die offenen Eintraege abgleichen (alle Monatsdokumente)
    async for d in db[konfig.BUDGET].find({}, {"reservierungen": 1, "reserved_usd": 1}):
        offen = list(d.get("reservierungen") or [])
        summe = round(sum(float(e.get("usd") or 0) for e in offen), 4)
        if abs(float(d.get("reserved_usd") or 0) - summe) < 0.00005:
            continue
        r = await db[konfig.BUDGET].update_one({"_id": d["_id"], "reservierungen": offen}, {"$set": {"reserved_usd": summe}})
        korrigiert += int(r.modified_count)
    return {"freigegeben": len(freigegeben), "usd": round(sum(f["usd"] for f in freigegeben), 4), "korrigiert": korrigiert}
