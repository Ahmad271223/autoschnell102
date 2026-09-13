# -*- coding: utf-8 -*-
"""Beweisdokumente je Inserat (ersetzt /snapshots, 10.09.2026).

Ein Beweisdokument gehoert zum INSERAT, nicht zu einer Firma: es entsteht
beim ersten Gebrauch des Links (egal von wem) und wird von allen Firmen
geteilt, die das Inserat verwenden. Sehen darf es, wer

  * das Inserat selbst verglichen hat (vehicle_comparisons, 14 Tage) oder
  * ein Fahrzeug zu diesem Inserat im eigenen Bereich hat
    (Chef: Firma; Sucher: eigenes/mitbearbeitetes Fahrzeug, eigener
    Vertrag oder eigener Termin dazu),
  * als Fahrer einen eigenen Termin zu so einem Fahrzeug hat.

Das Dokument enthaelt keine Angaben zur Firma, die es ausgeloest hat.
"""
import logging
import re
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Response

import beweis_service as BS
from deps import (current_firma, current_user, db, fahrzeug_im_bereich,
                  ist_sucher, termin_bereich)

log = logging.getLogger("autohandel")
router = APIRouter()

_NICHT_GEFUNDEN = "Beweisdokument nicht gefunden"


async def _fahrzeug_erlaubt(user: dict, vehicle_id: str) -> bool:
    """Fahrzeug der eigenen Firma, fuer Sucher zusaetzlich im eigenen
    Bereich (Fahrzeug, eigener Vertrag oder eigener Termin dazu)."""
    dealer_id = user.get("dealer_id")
    if not dealer_id or not vehicle_id:
        return False
    if not await db.vehicles.count_documents({"id": vehicle_id, "dealer_id": dealer_id},
                                             limit=1):
        return False
    if not ist_sucher(user):
        return True
    if await fahrzeug_im_bereich(user, vehicle_id):
        return True
    if await db.generated_pdfs.count_documents(
            {"vehicle_id": vehicle_id, "dealer_id": dealer_id, "user_id": user["id"]},
            limit=1):
        return True
    return await db.appointments.count_documents(
        {"vehicle_id": vehicle_id, **await termin_bereich(user)}, limit=1) > 0


async def _darf_sehen(user: dict, doc: dict) -> bool:
    if user.get("role") == "admin":
        # Nur der Super-Admin (Runde 12: keine normalen Admins mehr).
        return bool(user.get("is_super_admin"))
    dealer_id = user.get("dealer_id")
    if not dealer_id:
        return False
    ck = doc.get("cache_key")
    q: Dict[str, Any] = {"cache_key": ck, "dealer_id": dealer_id}
    if ist_sucher(user):
        q["user_id"] = user["id"]
    if await db.vehicle_comparisons.count_documents(q, limit=1):
        return True
    vids: List[str] = await db.vehicles.distinct(
        "id", {"dealer_id": dealer_id, "inserat_schluessel": ck})
    for vid in vids[:50]:
        if await _fahrzeug_erlaubt(user, vid):
            return True
    return False


def _dateiname(doc: dict) -> str:
    q = re.sub(r"[^a-z0-9]", "", str(doc.get("quelle") or "").lower()) or "inserat"
    kennung = re.sub(r"[^A-Za-z0-9_-]", "", str(doc.get("item_id") or ""))[:40] or "dokument"
    return f"Beweis_{q}_{kennung}.pdf"


async def _pdf_antwort(doc: dict) -> Response:
    if doc.get("status") != "fertig" or not doc.get("pdf_key"):
        if doc.get("status") == "geloescht":
            raise HTTPException(410, "Das Beweisdokument wurde nach Ablauf der "
                                     "Aufbewahrungsfrist gelöscht.")
        raise HTTPException(409, "Das Beweisdokument ist noch nicht fertig.")
    from storage_service import load_async
    try:
        daten = await load_async(doc["pdf_key"])
    except (FileNotFoundError, KeyError):
        raise HTTPException(404, "Die Datei des Beweisdokuments fehlt im Speicher.")
    except Exception:  # noqa: BLE001
        log.exception("Beweisdokument %s nicht ladbar", doc.get("id"))
        raise HTTPException(502, "Datei-Speicher gerade nicht erreichbar.")
    return Response(
        content=daten, media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{_dateiname(doc)}"',
                 "Cache-Control": "no-store",
                 "X-Beweis-SHA256": str(doc.get("pdf_sha256") or "")})


@router.get("/beweise")
async def beweis_zum_fahrzeug(vehicle_id: str, user=Depends(current_firma)):
    """Beweisdokument zum Inserat eines Fahrzeugs (Akte, Termine, Archiv).
    Antwort {"beweis": null}, wenn es (noch) keines gibt."""
    if not await _fahrzeug_erlaubt(user, vehicle_id):
        raise HTTPException(404, "Fahrzeug nicht gefunden")
    v = await db.vehicles.find_one(
        {"id": vehicle_id, "dealer_id": user["dealer_id"]},
        {"_id": 0, "inserat_schluessel": 1, "quelle": 1, "mobile_ad_id": 1,
         "data.detail_url": 1, "data.kleinanzeigen_url": 1})
    ck = BS.inserat_schluessel(v)
    if ck and v is not None and not v.get("inserat_schluessel"):
        # Altbestand (vor 10.09.2026 verglichen): Zuordnung einmal festhalten,
        # damit Download und Fahrer-App das Dokument ebenfalls finden.
        await db.vehicles.update_one(
            {"id": vehicle_id, "dealer_id": user["dealer_id"]},
            {"$set": {"inserat_schluessel": ck}})
    return {"beweis": BS.oeffentlich(await BS.beweis_fuer_schluessel(db, ck))}


@router.get("/beweise/{beweis_id}")
async def beweis_status(beweis_id: str, user=Depends(current_user)):
    doc = await db.inserat_beweise.find_one({"id": beweis_id}, {"_id": 0})
    if not doc or not await _darf_sehen(user, doc):
        raise HTTPException(404, _NICHT_GEFUNDEN)
    return BS.oeffentlich(doc)


@router.get("/beweise/{beweis_id}/pdf")
async def beweis_pdf(beweis_id: str, user=Depends(current_user)):
    """Nur mit Authorization-Header (kein ?auth= — Token gehoert nicht in
    Verlauf und Logs); das Frontend laedt per fetch und zeigt eine Blob-URL."""
    doc = await db.inserat_beweise.find_one({"id": beweis_id}, {"_id": 0})
    if not doc or not await _darf_sehen(user, doc):
        raise HTTPException(404, _NICHT_GEFUNDEN)
    return await _pdf_antwort(doc)


from routes.drivers import _verknuepfte_dealer_ids, current_driver  # noqa: E402


@router.get("/driver/beweise/{beweis_id}/pdf")
async def driver_beweis_pdf(beweis_id: str, driver=Depends(current_driver)):
    """Fahrer: nur ueber einen EIGENEN Termin zu einem Fahrzeug dieses
    Inserats, bei einer Firma, in deren Fahrerliste er aktuell steht."""
    doc = await db.inserat_beweise.find_one({"id": beweis_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, _NICHT_GEFUNDEN)
    firmen = await _verknuepfte_dealer_ids(driver["id"])
    paare = []
    if firmen:
        async for v in db.vehicles.find(
                {"dealer_id": {"$in": firmen}, "inserat_schluessel": doc.get("cache_key")},
                {"_id": 0, "id": 1, "dealer_id": 1}).limit(50):
            paare.append({"vehicle_id": v["id"], "dealer_id": v["dealer_id"]})
    if not paare or not await db.appointments.count_documents(
            {"driver_id": driver["id"], "$or": paare}, limit=1):
        raise HTTPException(404, _NICHT_GEFUNDEN)
    return await _pdf_antwort(doc)
