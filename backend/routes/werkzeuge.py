# -*- coding: utf-8 -*-
"""Werkzeuge zum Herunterladen (03.10.2026) — siehe backend/werkzeuge.py.

GET /api/werkzeuge                    Liste der Werkzeuge DIESER Firma (sonst leer)
GET /api/werkzeuge/{id}/download      Programmdatei — 404 fuer jede nicht
                                      freigegebene Firma (gar nicht sichtbar)
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

import werkzeuge as wz
from deps import current_firma, current_user, db, log_activity_sicher
from storage_service import StorageError, bloecke_async, groesse_async

log = logging.getLogger("autohandel")
router = APIRouter()

NICHT_GEFUNDEN = "Nicht gefunden"


async def _kunden_nr(user: dict):
    if user.get("role") not in ("dealer", "sucher") or not user.get("dealer_id"):
        return None
    firma = await db.dealers.find_one({"id": user["dealer_id"]}, {"_id": 0, "id": 1, "kunden_nr": 1})
    return (firma or {}).get("kunden_nr")


@router.get("/werkzeuge")
async def werkzeuge_liste(user=Depends(current_user)):
    """Fuer jede Rolle aufrufbar; nur Chef und Sucher einer freigegebenen
    Firma bekommen Eintraege — alle anderen eine leere Liste."""
    kunden_nr = await _kunden_nr(user)
    ids = wz.freigegebene_werkzeuge(kunden_nr) if kunden_nr is not None else []
    liste = []
    for wid in ids:
        meta = await db.werkzeuge.find_one({"id": wid}, {"_id": 0})
        liste.append(wz.oeffentlich(meta, wid))
    return {"werkzeuge": liste}


@router.get("/werkzeuge/{werkzeug_id}/download")
async def werkzeug_download(werkzeug_id: str, user=Depends(current_firma)):
    kunden_nr = await _kunden_nr(user)
    if werkzeug_id not in wz.WERKZEUGE or not wz.ist_freigegeben(werkzeug_id, kunden_nr):
        raise HTTPException(404, NICHT_GEFUNDEN)
    meta = await db.werkzeuge.find_one({"id": werkzeug_id}, {"_id": 0})
    if not meta or not meta.get("schluessel"):
        raise HTTPException(404, "Das Programm wurde noch nicht hochgeladen — bitte später erneut versuchen.")
    try:
        groesse = await groesse_async(meta["schluessel"])
    except StorageError:
        log.error("Werkzeug %s: Eintrag vorhanden, Datei %s fehlt im Speicher", werkzeug_id, meta["schluessel"])
        raise HTTPException(404, "Das Programm wurde noch nicht hochgeladen — bitte später erneut versuchen.")
    await log_activity_sicher(user["dealer_id"], user["id"], "werkzeug_download",
                              ref=werkzeug_id, meta={"version": meta.get("version")})
    dateiname = meta.get("dateiname") or wz.WERKZEUGE[werkzeug_id]["dateiname"]
    return StreamingResponse(
        bloecke_async(meta["schluessel"], 0, groesse - 1),
        media_type="application/vnd.microsoft.portable-executable",
        headers={
            "Content-Disposition": f'attachment; filename="{dateiname}"',
            "Content-Length": str(groesse),
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
