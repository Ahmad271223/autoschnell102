# -*- coding: utf-8 -*-
"""Nicht gesehen ist NICHT verkauft. Ein Listing, das seit N Tagen nicht
mehr im Top-20-Sample auftaucht, wird gezielt einzeln nachgeprueft (ein
Scraper-Lauf mit der Detail-URL, maxItems=1, ~0,007 $). Nur wenn das Inserat
wirklich nicht mehr erreichbar ist: confirmed_removed. Die Oberflaeche sagt
dann "Inserat nicht mehr online" — nie "verkauft"."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Dict

from markt import apify, budget, konfig, normalisieren
from markt.konfig import LISTINGS

log = logging.getLogger(__name__)


async def pruefen(db, listing: Dict[str, Any]) -> str:
    """Zustand nach der Pruefung: confirmed_removed | not_seen_in_sample (noch online) | unklar."""
    jetzt = konfig.jetzt_iso()
    url = listing.get("url") or f"https://suchen.mobile.de/fahrzeuge/details.html?id={listing['listing_id']}"
    res = await budget.reservieren(db, konfig.kosten_je_lauf_usd(konfig.actor(), 1))
    if res is None:
        return "unklar"
    await db[LISTINGS].update_one({"source": listing["source"], "listing_id": listing["listing_id"]},
                                  {"$set": {"active_state": "verification_pending", "verification_started_at": jetzt}})
    try:
        r = await apify.lauf([url], 1, zeitlimit_s=120)
    except apify.ApifyFehler as e:
        await budget.abrechnen(db, res, None if e.art in ("zeit", "ausfall") else 0.0, 0, gelaufen=e.art in ("zeit", "ausfall"))
        await db[LISTINGS].update_one({"source": listing["source"], "listing_id": listing["listing_id"]},
                                      {"$set": {"active_state": "not_seen_in_sample", "verification_error": e.art}})
        return "unklar"
    items = normalisieren.listings_aus_items(r["items"])
    await budget.abrechnen(db, res, r.get("usd"), len(items))
    if not items:
        await db[LISTINGS].update_one({"source": listing["source"], "listing_id": listing["listing_id"]},
                                      {"$set": {"active_state": "confirmed_removed", "confirmed_removed_at": jetzt,
                                                "verified_at": jetzt}})
        return "confirmed_removed"
    l = items[0]
    setzen = {"active_state": "not_seen_in_sample", "verified_online_at": jetzt, "verified_at": jetzt,
              "current_price": float(l["price_gross"])}
    await db[LISTINGS].update_one({"source": listing["source"], "listing_id": listing["listing_id"]}, {"$set": setzen})
    return "not_seen_in_sample"


async def kandidaten(db, limit: int):
    grenze = (konfig.jetzt() - timedelta(days=konfig.entfernung_nach_tagen())).isoformat()
    return await db[LISTINGS].find({"active_state": "not_seen_in_sample", "not_seen_since": {"$lte": grenze},
                                    "$or": [{"verified_at": {"$exists": False}}, {"verified_at": {"$lt": grenze}}]},
                                   {"_id": 0, "source": 1, "listing_id": 1, "url": 1}).sort("not_seen_since", 1).to_list(limit)


async def taeglich(db) -> Dict[str, Any]:
    """Einmal je Tag (Sperre) bis zu MARKT_ENTFERNUNG_MAX_JE_TAG Listings nachpruefen."""
    if not konfig.entfernung_pruefen() or konfig.entfernung_max_je_tag() <= 0:
        return {"geprueft": 0}
    tag = konfig.heute_tag()
    from job_lock import acquire
    token = await acquire(db, f"markt-entfernung-{tag}", ttl_seconds=20 * 3600)
    if not token:
        return {"geprueft": 0}
    zaehler: Dict[str, int] = {"geprueft": 0, "confirmed_removed": 0, "not_seen_in_sample": 0, "unklar": 0}
    for l in await kandidaten(db, konfig.entfernung_max_je_tag()):
        try:
            erg = await pruefen(db, l)
        except Exception:  # noqa: BLE001
            log.exception("Entfernungs-Pruefung %s gescheitert", l.get("listing_id"))
            erg = "unklar"
        zaehler["geprueft"] += 1
        zaehler[erg] = zaehler.get(erg, 0) + 1
    return zaehler
