# -*- coding: utf-8 -*-
"""Routen: Ueberweisung mit GiroCode im Abholauftrag (Wunsch Ahmad 24.11.2026).

Fahrer:  GET/PUT /driver/appointments/{id}/ueberweisung   (Kontodaten erfassen, Stand sehen)
Buero:   GET     /appointments/{id}/ueberweisung           (Karte mit GiroCode)
         POST    /appointments/{id}/ueberweisung/ueberwiesen   (atomar, 409 beim zweiten Mal)
         DELETE  /appointments/{id}/ueberweisung/ueberwiesen   (nur Hauptchef)

Rechte: Chef alle Termine der Firma, Sucher nur eigene Vorgaenge
(deps.termin_im_bereich — wie beim Einkaufspreis), Fahrer nur seine
angenommene Fahrt (protocols._appt_or_404) und nur Erfassen + Stand.
Der Betrag kommt immer vom Server (ueberweisung.betrag_aus).
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import ueberweisung as U
from deps import (current_firma, db, ist_haupt_chef, log_activity_sicher, now_iso,
                  termin_im_bereich)
from routes.drivers import current_driver

router = APIRouter()


class KontodatenIn(BaseModel):
    iban: str = Field(max_length=60)
    kontoinhaber: str = Field(max_length=200)


class UeberwiesenIn(BaseModel):
    # Der Stand, den das Buero angezeigt hat (Betrag + Kontodaten). Das
    # Frontend schickt NIE einen Betrag.
    stand: str = Field(min_length=8, max_length=64)


async def _termin_buero(appt_id: str, user: dict) -> dict:
    appt = await db.appointments.find_one({"id": appt_id, "dealer_id": user["dealer_id"]}, {"_id": 0})
    if appt is None or not await termin_im_bereich(user, appt):
        raise HTTPException(404, "Termin nicht gefunden")
    return appt


async def _termin_fahrer(appt_id: str, driver: dict) -> dict:
    from routes.protocols import _appt_or_404
    return await _appt_or_404(appt_id, driver)


# ------------------------------------------------------------------ Fahrer
@router.get("/driver/appointments/{appt_id}/ueberweisung")
async def fahrer_ueberweisung(appt_id: str, driver=Depends(current_driver)):
    appt = await _termin_fahrer(appt_id, driver)
    return U.fahrer_sicht(await U.lage(appt))


@router.put("/driver/appointments/{appt_id}/ueberweisung")
async def fahrer_kontodaten(appt_id: str, body: KontodatenIn, driver=Depends(current_driver)):
    appt = await _termin_fahrer(appt_id, driver)
    try:
        iban = U.iban_pruefen(body.iban)
        inhaber = U.kontoinhaber_pruefen(body.kontoinhaber)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    l = await U.lage(appt)
    if not l["zahlungsart"]["ueberweisung"]:
        raise HTTPException(409, "Laut Kaufvertrag wird bar bezahlt — Kontodaten werden nicht gebraucht.")
    if (l["doc"] or {}).get("ueberwiesen"):
        raise HTTPException(409, "Schon überwiesen — die Kontodaten lassen sich nicht mehr ändern.")
    coll = getattr(db, U.SAMMLUNG)
    jetzt = now_iso()
    try:
        r = await coll.update_one(
            {"appointment_id": appt["id"], "dealer_id": appt["dealer_id"], "ueberwiesen": None},
            {"$set": {"iban": iban, "kontoinhaber": inhaber, "erfasst_am": jetzt,
                      "erfasst_von": driver["id"], "contract_id": appt.get("contract_id")},
             "$setOnInsert": {"id": uuid.uuid4().hex, "dealer_id": appt["dealer_id"],
                              "appointment_id": appt["id"], "angelegt_am": jetzt,
                              "meldung_kontodaten_am": None, "meldung_betrag_am": None}},
            upsert=True)
    except Exception as exc:  # DuplicateKey: Eintrag existiert und ist schon ueberwiesen
        if "duplicate" not in str(exc).lower() and getattr(exc, "code", None) != 11000:
            raise
        r = None
    if r is None or (not r.matched_count and not r.upserted_id):
        raise HTTPException(409, "Schon überwiesen — die Kontodaten lassen sich nicht mehr ändern.")
    # Personendaten nie in Audit-Zeilen: nur das Land der IBAN
    await log_activity_sicher(appt["dealer_id"], "", "ueberweisung.kontodaten", ref=appt["id"],
                              meta={"fahrer": driver["id"], "land": iban[:2]})
    await U.meldungen_pruefen(appt)
    return U.fahrer_sicht(await U.lage(appt))


# ------------------------------------------------------------------ Buero
@router.get("/appointments/{appt_id}/ueberweisung")
async def buero_ueberweisung(appt_id: str, user=Depends(current_firma)):
    appt = await _termin_buero(appt_id, user)
    # Fallback, falls ein Haken (Freigabe/Termin) die Meldung verpasst hat — einmalig.
    await U.meldungen_pruefen(appt)
    return U.buero_sicht(await U.lage(appt), darf_zuruecknehmen=await ist_haupt_chef(user))


def _name(user: dict) -> str:
    n = f"{user.get('first_name') or ''} {user.get('last_name') or ''}".strip()
    return n or user.get("kontonummer") or ("Chef" if user.get("role") == "dealer" else "Sucher")


@router.post("/appointments/{appt_id}/ueberweisung/ueberwiesen")
async def als_ueberwiesen_markieren(appt_id: str, body: UeberwiesenIn, user=Depends(current_firma)):
    appt = await _termin_buero(appt_id, user)
    l = await U.lage(appt)
    doc = l["doc"] or {}
    if doc.get("ueberwiesen"):
        raise HTTPException(409, "Schon als überwiesen markiert (am "
                                 f"{str(doc['ueberwiesen'].get('am') or '')[:10]}).")
    if not (doc.get("iban") and doc.get("kontoinhaber")):
        raise HTTPException(409, "Noch keine Kontodaten — der Fahrer trägt sie vor Ort ein.")
    b = l["betrag"]
    if not b["fest"]:
        raise HTTPException(409, "Der Betrag ist noch offen — der Preis vor Ort ist noch nicht bestätigt.")
    if body.stand != U._stand(b, doc):
        raise HTTPException(409, "Betrag oder Kontodaten haben sich gerade geändert — bitte neu laden "
                                 "und den angezeigten Betrag prüfen.")
    vertrag = l["vertrag"] or {}
    eintrag = {"am": now_iso(), "von": user["id"], "von_name": _name(user),
               "betrag_cents": b["betrag_cents"], "quelle": b["quelle"], "betrag_stand": b["stand"],
               "contract_id": vertrag.get("id"), "contract_version": int(vertrag.get("version") or 1)
               if vertrag else None, "protokoll_id": (l["protokoll"] or {}).get("id")}
    r = await getattr(db, U.SAMMLUNG).update_one(
        {"id": doc["id"], "dealer_id": appt["dealer_id"], "ueberwiesen": None,
         "iban": doc["iban"], "kontoinhaber": doc["kontoinhaber"]},
        {"$set": {"ueberwiesen": eintrag},
         "$push": {"verlauf": {"art": "markiert", "am": eintrag["am"], "von": user["id"],
                               "betrag_cents": b["betrag_cents"]}}})
    if not r.modified_count:
        raise HTTPException(409, "Schon als überwiesen markiert oder Kontodaten gerade geändert — bitte neu laden.")
    await log_activity_sicher(user["dealer_id"], user["id"], "ueberweisung.markiert", ref=appt["id"],
                              meta={"betrag_cents": b["betrag_cents"],
                                    "vertrag_version": eintrag["contract_version"]})
    return U.buero_sicht(await U.lage(appt), darf_zuruecknehmen=await ist_haupt_chef(user))


@router.delete("/appointments/{appt_id}/ueberweisung/ueberwiesen")
async def ueberwiesen_zuruecknehmen(appt_id: str, user=Depends(current_firma)):
    appt = await _termin_buero(appt_id, user)
    if not await ist_haupt_chef(user):
        raise HTTPException(403, "Rückgängig machen darf nur der Hauptaccount.")
    coll = getattr(db, U.SAMMLUNG)
    doc = await U.lesen(appt["id"], appt["dealer_id"])
    if not doc or not doc.get("ueberwiesen"):
        raise HTTPException(409, "Nicht als überwiesen markiert.")
    r = await coll.update_one(
        {"id": doc["id"], "dealer_id": appt["dealer_id"], "ueberwiesen.am": doc["ueberwiesen"].get("am")},
        {"$set": {"ueberwiesen": None},
         "$push": {"verlauf": {"art": "zurueckgenommen", "am": now_iso(), "von": user["id"],
                               "betrag_cents": doc["ueberwiesen"].get("betrag_cents")}}})
    if not r.modified_count:
        raise HTTPException(409, "Der Stand hat sich gerade geändert — bitte neu laden.")
    await log_activity_sicher(user["dealer_id"], user["id"], "ueberweisung.zurueckgenommen", ref=appt["id"],
                              meta={"betrag_cents": doc["ueberwiesen"].get("betrag_cents")})
    return U.buero_sicht(await U.lage(appt), darf_zuruecknehmen=True)
