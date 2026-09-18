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
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

import beweis_service as BS
from deps import (current_firma, current_user, db, fahrzeug_im_bereich,
                  ist_sucher, log_activity_sicher, termin_bereich)

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


async def _firmenstatus_pruefen(user: dict) -> None:
    """Runde 16 (15.09.2026): die direkten Beweis-Routen haengen an current_user
    — Chef und Sucher muessen trotzdem durch dieselbe Firmenpruefung wie alle
    Firmenrouten (Loeschung laeuft -> 409, gesperrt -> 403)."""
    if user.get("role") in ("dealer", "sucher"):
        await current_firma(user)


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
    # Pruefung 14.09.2026 (B10): Die Pruefsumme im Kopf war bisher nur der
    # gespeicherte Wert — eine veraenderte oder vertauschte Datei im Speicher
    # waere mit "passender" Pruefsumme ausgeliefert worden. Jetzt wird die
    # Datei selbst gehasht; weicht sie ab, gibt es kein Dokument, sondern
    # einen Betriebsalarm.
    import hashlib
    ist = hashlib.sha256(daten).hexdigest()
    soll = str(doc.get("pdf_sha256") or "")
    if soll and ist != soll:
        log.error("Beweisdokument %s: Pruefsumme weicht ab (gespeichert %s, Datei %s)",
                  doc.get("id"), soll[:12], ist[:12])
        import betrieb as _betrieb
        await _betrieb.alarm(db, "beweis_pruefsumme_abweichend", ref=str(doc.get("id") or ""),
                             pdf_key=str(doc.get("pdf_key") or ""), soll=soll, ist=ist)
        raise HTTPException(409, "Die gespeicherte Datei stimmt nicht mit der Prüfsumme "
                                 "des Beweisdokuments überein — bitte den Betreiber "
                                 "informieren.")
    return Response(
        content=daten, media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{_dateiname(doc)}"',
                 "Cache-Control": "no-store",
                 "X-Beweis-SHA256": ist})


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


class AnforderungIn(BaseModel):
    vehicle_id: Optional[str] = None
    cache_key: Optional[str] = None


@router.post("/beweise/anfordern")
async def beweis_anfordern(body: AnforderungIn, user=Depends(current_firma)):
    """Beweisdokument auf Knopfdruck erzeugen lassen (Wunsch Ahmad 18.09.2026).

    Frueher entstand zu JEDEM abgerufenen Inserat automatisch eines. Jetzt
    fragt die App nach dem Versand ("Beweisdokument erstellen lassen?") und
    in der Akte steht der Knopf — hier wird das Dokument vorgemerkt, erzeugt
    wird es wie bisher vom Hintergrund-Worker.

    Idempotent: Gibt es zum Inserat schon ein Dokument (auch ein laufendes),
    kommt genau dieses zurueck — nie ein zweites "erstes" Dokument."""
    schluessel = (body.cache_key or "").strip()
    fahrzeug = None
    if body.vehicle_id:
        if not await _fahrzeug_erlaubt(user, body.vehicle_id):
            raise HTTPException(404, "Fahrzeug nicht gefunden")
        fahrzeug = await db.vehicles.find_one(
            {"id": body.vehicle_id, "dealer_id": user["dealer_id"]},
            {"_id": 0, "id": 1, "inserat_schluessel": 1, "quelle": 1,
             "mobile_ad_id": 1, "data": 1})
        schluessel = BS.inserat_schluessel(fahrzeug) or schluessel
        if schluessel and fahrzeug is not None and not fahrzeug.get("inserat_schluessel"):
            await db.vehicles.update_one(
                {"id": body.vehicle_id, "dealer_id": user["dealer_id"]},
                {"$set": {"inserat_schluessel": schluessel}})
    elif not schluessel:
        raise HTTPException(400, "Bitte ein Fahrzeug oder ein Inserat angeben.")
    if not schluessel:
        raise HTTPException(400, "Zu diesem Fahrzeug ist kein Inserats-Link hinterlegt — "
                                 "ein Beweisdokument gibt es nur zu einem Inserat.")
    if not fahrzeug and not await _darf_sehen(user, {"cache_key": schluessel}):
        # Ohne Fahrzeug nur, wer das Inserat selbst verglichen hat.
        raise HTTPException(404, _NICHT_GEFUNDEN)

    vorhanden = await BS.beweis_fuer_schluessel(db, schluessel)
    if vorhanden and vorhanden.get("status") in ("offen", "in_arbeit", "fertig"):
        return {"beweis": BS.oeffentlich(vorhanden)}

    # Datenstand einfrieren: was der Sucher gesehen hat. Erst der
    # Inseratsspeicher (frischester gepruefter Stand), sonst die beim
    # Vergleich am Fahrzeug gespeicherten Daten (Speicher laeuft nach 90
    # Tagen ab). Die Fotos holt der Worker beim Erzeugen vom Portal.
    eintrag = await db.listings_cache.find_one(
        {"cache_key": schluessel},
        {"_id": 0, "source": 1, "item_id": 1, "url": 1, "data": 1, "fetched_at": 1})
    daten = (eintrag or {}).get("data") or (fahrzeug or {}).get("data") or None
    if not eintrag and not daten:
        raise HTTPException(404, "Zu diesem Inserat liegen keine Daten mehr vor — "
                                 "bitte den Link noch einmal vergleichen.")
    quelle = ((eintrag or {}).get("source") or (fahrzeug or {}).get("quelle")
              or schluessel.split(":")[0])
    item_id = ((eintrag or {}).get("item_id") or (fahrzeug or {}).get("mobile_ad_id") or "")
    url = ((eintrag or {}).get("url") or (daten or {}).get("detail_url")
           or (daten or {}).get("kleinanzeigen_url") or "")
    doc = await BS.beweis_vormerken(
        db, cache_key=schluessel, quelle=quelle, item_id=item_id, url=url,
        anlass="angefordert", daten=daten, abgerufen_am=(eintrag or {}).get("fetched_at"))
    if not doc:
        raise HTTPException(503, "Das Beweisdokument konnte gerade nicht angefordert "
                                 "werden — bitte in ein paar Minuten noch einmal.")
    await log_activity_sicher(user["dealer_id"], user["id"], "beweis.angefordert",
                              ref=str(item_id or schluessel))
    return {"beweis": BS.oeffentlich(doc)}


@router.get("/beweise/{beweis_id}")
async def beweis_status(beweis_id: str, user=Depends(current_user)):
    await _firmenstatus_pruefen(user)
    doc = await db.inserat_beweise.find_one({"id": beweis_id}, {"_id": 0})
    if not doc or not await _darf_sehen(user, doc):
        raise HTTPException(404, _NICHT_GEFUNDEN)
    return BS.oeffentlich(doc)


@router.get("/beweise/{beweis_id}/pdf")
async def beweis_pdf(beweis_id: str, user=Depends(current_user)):
    await _firmenstatus_pruefen(user)
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
    # Pruefung 14.09.2026 (C22/C23): nur ueber einen ANGENOMMENEN, nicht
    # stornierten Termin.
    if not paare or not await db.appointments.count_documents(
            {"driver_id": driver["id"], "$or": paare,
             "status": {"$ne": "storniert"},
             "zuteilung": {"$nin": ["offen", "abgelehnt"]}}, limit=1):
        raise HTTPException(404, _NICHT_GEFUNDEN)
    return await _pdf_antwort(doc)
