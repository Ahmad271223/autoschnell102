# -*- coding: utf-8 -*-
"""Market Intelligence — Lesewege fuer die Firmen (Auftrag Ahmad 25.09.2026).

Rein lesend und best effort: liefert fertige Daten oder 404. Der Vergleich
ist zu diesem Zeitpunkt schon fertig; die Karte laedt danach getrennt.
Kein Weg hier wartet auf Apify oder einen Crawl."""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from deps import current_firma, db, fahrzeug_bereich
from markt import abfrage, konfig, segmente

log = logging.getLogger(__name__)
router = APIRouter()


@router.get("/market-intelligence/vehicle/{vehicle_id}")
async def markt_karte(vehicle_id: str, user=Depends(current_firma)):
    """Karte 'AutoSchnell Marktdaten' zum Fahrzeug des Vergleichs (nur lesen).
    Review 26.09.2026 (Nr. 9): Fahrzeug nur im Bereich des Kontos (Chef =
    Firma, Sucher = eigene/mitbearbeitete) — wie bei Vertrag und Termin."""
    try:
        v = await db.vehicles.find_one({"id": vehicle_id, **fahrzeug_bereich(user)}, {"_id": 0, "data": 1, "mobile_ad_id": 1})
        if not v:
            raise HTTPException(404, "Fahrzeug nicht gefunden")
        daten = dict(v.get("data") or {})
        listing_id = daten.get("mobile_ad_id") or v.get("mobile_ad_id")
        karte = await abfrage.karte(db, daten, str(listing_id) if listing_id else None)
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001 — Marktdaten sind Beiwerk, nie ein 500 im Vergleich
        log.exception("Marktdaten-Karte %s gescheitert", vehicle_id)
        raise HTTPException(404, "Keine Marktdaten")
    if not karte:
        raise HTTPException(404, "Keine Marktdaten zu diesem Fahrzeug")
    return karte


@router.get("/markt/chancen")
async def markt_chancen(typ: Optional[str] = None, model_id: Optional[str] = None, segment_id: Optional[str] = None,
                        km_min: Optional[int] = None, km_max: Optional[int] = None, tage: int = 7, limit: int = 100,
                        user=Depends(current_firma)):
    if not konfig.chancen_aktiv():
        raise HTTPException(404, "Demnächst verfügbar")
    try:
        return {"chancen": await abfrage.chancen(db, typ=typ, model_id=model_id, segment_id=segment_id, km_min=km_min,
                                                 km_max=km_max, tage=tage, limit=limit),
                "hinweis": abfrage.HINWEIS}
    except Exception:  # noqa: BLE001
        log.exception("Chancen gescheitert")
        return {"chancen": [], "hinweis": abfrage.HINWEIS}


@router.get("/markt/modelle")
async def markt_modelle(user=Depends(current_firma)):
    """Filterlisten fuer Chancen: aktive Modelle, km- und EZ-Bereiche."""
    if not konfig.chancen_aktiv():
        raise HTTPException(404, "Demnächst verfügbar")
    modelle = await db[konfig.MODELLE].find({"enabled": True}, {"_id": 0, "id": 1, "label": 1, "make": 1, "model": 1,
                                                               "variant": 1}).sort("label", 1).to_list(2000)
    return {"modelle": modelle, "km_buckets": await segmente.km_buckets(db), "ez_buckets": await segmente.ez_buckets(db)}
