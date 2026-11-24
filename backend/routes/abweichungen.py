# -*- coding: utf-8 -*-
"""Nummerierte Abweichungen eines Abholauftrags (Wunsch Ahmad 24.11.2026).

  GET /api/appointments/{appt_id}/abweichungen   Chef und Sucher (eigener Bereich)

Die Terminliste zeigt die Zahl (appointments.protokoll_abweichungen); ein
Klick darauf oeffnet diese Ansicht. Rechnung und Nummern: abweichung_nummern.py
— dieselben wie in Freigaben, Fahrer-App und Protokoll-PDF.
"""
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException

import abweichung_nummern as AN
from deps import current_firma, termin_im_bereich
from routes import protocols as P

log = logging.getLogger("autohandel")
router = APIRouter()

LEER: Dict[str, Any] = {"anzahl": 0, "eintraege": [], "gruppen": []}


async def nummeriert(appt: dict, doc: Optional[dict], vehicle: Optional[dict] = None,
                     contract: Optional[dict] = None, *, sichern: bool = True) -> Dict[str, Any]:
    """Nummerierte Abweichungen eines Protokolls; haelt neue Nummern fest.

    Faellt nie: ein Fehler beim Rechnen ergibt die leere Liste (die Seiten
    zeigen das Protokoll trotzdem), ein Fehler beim Speichern nur ein Log."""
    if not doc:
        return dict(LEER)
    try:
        if vehicle is None or contract is None:
            fz, vt = await P._fahrzeug_und_vertrag(appt)
            vehicle = fz if vehicle is None else vehicle
            contract = vt if contract is None else contract
        erg = AN.berechnen(doc, vehicle, contract, felder=P.VEHICLE_CHECK_FIELDS,
                           condition_fields=P.CONDITION_FIELDS, document_items=P.DOCUMENT_ITEMS)
    except Exception:  # noqa: BLE001
        log.exception("Abweichungen: Protokoll %s liess sich nicht nummerieren", doc.get("id"))
        return dict(LEER)
    if sichern:
        try:
            await AN.sichern(P.db, doc, erg)
        except Exception:  # noqa: BLE001
            log.exception("Abweichungen: Nummern fuer Protokoll %s nicht gespeichert", doc.get("id"))
    return AN.fuer_antwort(erg)


async def nach_aenderung(appt_id: str) -> None:
    """Nach dem Abschicken: Nummern und Zahl am Termin nachziehen (best effort)."""
    try:
        appt = await P.db.appointments.find_one({"id": appt_id}, {"_id": 0})
        doc = await P._current(appt_id) if appt else None
        if appt and doc:
            await nummeriert(appt, doc)
    except Exception:  # noqa: BLE001
        log.exception("Abweichungen: Nachziehen fuer Termin %s gescheitert", appt_id)


@router.get("/appointments/{appt_id}/abweichungen")
async def termin_abweichungen(appt_id: str, user=Depends(current_firma)):
    appt = await P.db.appointments.find_one({"id": appt_id, "dealer_id": user["dealer_id"]}, {"_id": 0})
    # wie der Abholbericht: nur im eigenen Bereich (Sucher: eigene Termine)
    if not appt or not await termin_im_bereich(user, appt):
        raise HTTPException(404, "Termin nicht gefunden")
    doc = await P._current(appt_id)
    erg = await nummeriert(appt, doc)
    return {**erg, "appointment_id": appt_id, "title": appt.get("title") or "",
            "protocol_id": (doc or {}).get("id"), "status": (doc or {}).get("status")}
