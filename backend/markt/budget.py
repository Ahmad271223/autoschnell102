# -*- coding: utf-8 -*-
"""Monatsbudget des Market-Crawlers (eigen, getrennt vom KI-Budget):
market_crawler_budget je Monat mit budget_usd, reserved_usd, used_usd,
rows, runs. Vor einem Lauf wird geschaetzt reserviert (atomar, $expr),
nach dem Lauf mit den echten Kosten abgerechnet. Voll = keine weiteren
automatischen Crawls; alles andere in AutoSchnell laeuft weiter."""
from __future__ import annotations

from typing import Any, Dict, Optional

from pymongo import ReturnDocument

from markt import konfig


async def dokument(db, monat: Optional[str] = None) -> Dict[str, Any]:
    m = monat or konfig.monat()
    await db[konfig.BUDGET].update_one(
        {"_id": m},
        {"$setOnInsert": {"budget_usd": konfig.budget_monat_usd(), "reserved_usd": 0.0, "used_usd": 0.0,
                          "rows": 0, "runs": 0, "angelegt": konfig.jetzt_iso()}},
        upsert=True)
    d = await db[konfig.BUDGET].find_one({"_id": m}) or {}
    d["frei_usd"] = round(float(d.get("budget_usd") or 0) - float(d.get("used_usd") or 0) - float(d.get("reserved_usd") or 0), 4)
    return d


async def budget_setzen(db, budget_usd: float, monat: Optional[str] = None) -> None:
    await dokument(db, monat)
    await db[konfig.BUDGET].update_one({"_id": monat or konfig.monat()}, {"$set": {"budget_usd": float(budget_usd)}})


async def reservieren(db, est_usd: float) -> Optional[Dict[str, Any]]:
    """{"monat", "est_usd"} oder None (Budget voll). Atomar: nur wenn
    used + reserved + est <= budget."""
    m = konfig.monat()
    await dokument(db, m)
    est = round(float(est_usd), 4)
    doc = await db[konfig.BUDGET].find_one_and_update(
        {"_id": m, "$expr": {"$lte": [{"$add": [{"$ifNull": ["$used_usd", 0]}, {"$ifNull": ["$reserved_usd", 0]}, est]},
                                      {"$ifNull": ["$budget_usd", 0]}]}},
        {"$inc": {"reserved_usd": est}, "$set": {"stand": konfig.jetzt_iso()}},
        return_document=ReturnDocument.AFTER)
    if doc is None:
        return None
    return {"monat": m, "est_usd": est}


async def abrechnen(db, reservierung: Optional[Dict[str, Any]], tatsaechlich_usd: Optional[float],
                    rows: int = 0, gelaufen: bool = True) -> None:
    """Reservierung durch echte Kosten ersetzen (None = Schaetzung behalten)."""
    if not reservierung:
        return
    est = float(reservierung.get("est_usd") or 0)
    real = round(float(tatsaechlich_usd if tatsaechlich_usd is not None else (est if gelaufen else 0.0)), 4)
    await db[konfig.BUDGET].update_one(
        {"_id": reservierung["monat"]},
        {"$inc": {"reserved_usd": -est, "used_usd": real, "rows": int(rows), "runs": 1 if gelaufen else 0}})
