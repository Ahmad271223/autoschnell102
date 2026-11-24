# -*- coding: utf-8 -*-
"""Chat Buero <-> Fahrer je Abholtermin (Wunsch Ahmad 24.11.2026). Regeln und Speicher: auftrag_chat.py.

Wer darf:
  * Buero: Chef der Firma und Sucher, in deren Bereich der Termin liegt (deps.termin_bereich — dieselbe
    Regel wie Terminliste/Termin-Details). Schreiben nur, solange ein Fahrer zugeteilt ist.
  * Fahrer: nur der ZUGETEILTE Fahrer, nur solange er bei der Firma in der Fahrerliste steht
    (drivers._zugriff_pruefen, gesperrte Firma = kein Zugriff), die Fahrt nicht abgelehnt hat und die
    Fahrt in seiner App steht (Sichtfrist drivers.unterlagen_zugriff_oder_404: storniert nie,
    abgeschlossen 14/30 Tage). Er sieht nur sein eigenes Gespraech (fahrer_id), nie das eines
    frueheren Fahrers.
Spam-Schutz: chat_limiter je Konto (nicht je IP — so greift er auch hinter dem eigenen Proxy).
"""
import asyncio
import base64
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

import auftrag_chat as AC
from deps import current_firma, db, ist_sucher, log_activity_sicher, termin_bereich
from rate_limiter import SlidingWindowRateLimiter
from routes.drivers import current_driver

router = APIRouter()

chat_limiter = SlidingWindowRateLimiter(max_attempts=20, window_seconds=60, name="auftrag_chat")
ZU_VIELE = "Zu viele Nachrichten in kurzer Zeit — bitte einen Moment warten."
KEIN_FAHRER = "Diesem Termin ist noch kein Fahrer zugeteilt — erst dann gibt es den Chat."
FAHRER_SIEHT_NICHT = "Der Fahrer sieht diese Fahrt nicht mehr in seiner App — der Chat ist geschlossen."


class ChatIn(BaseModel):
    text: str = Field(default="", max_length=AC.TEXT_MAX + 200)   # genaue Grenze: AC.text_pruefen
    schnell: bool = False
    foto_b64: Optional[str] = Field(default=None, max_length=AC.FOTO_B64_MAX)


def _text_oder_422(body: ChatIn) -> str:
    try:
        return AC.text_pruefen(body.text, mit_foto=bool(body.foto_b64))
    except ValueError as exc:
        raise HTTPException(422, str(exc))


async def _foto_speichern(foto_b64: Optional[str], dealer_id: str) -> Optional[str]:
    if not foto_b64:
        return None
    from storage_service import StorageError, bild_verkleinern, make_key, save_async, validate_image_bytes
    try:
        raw = base64.b64decode(foto_b64.split(",")[-1], validate=False)
        validate_image_bytes(raw, wo="Chatfoto")
        raw = await asyncio.to_thread(bild_verkleinern, raw, "Chatfoto")   # EXIF/GPS raus
    except (StorageError, ValueError) as exc:
        raise HTTPException(400, f"Foto konnte nicht gelesen werden: {exc}")
    key = make_key("protocol", dealer_id or "x", "chat.jpg")
    try:
        await save_async(key, raw)
    except StorageError as exc:
        raise HTTPException(500, f"Foto konnte nicht gespeichert werden: {exc}")
    return key


async def _speichern(doc: dict) -> None:
    """Nachricht ablegen; Obergrenze je Termin. Scheitert das Speichern, geht das Foto mit."""
    try:
        if await db.auftrag_chat.count_documents({"appointment_id": doc["appointment_id"]},
                                                 limit=AC.NACHRICHTEN_JE_TERMIN_MAX) \
                >= AC.NACHRICHTEN_JE_TERMIN_MAX:
            raise HTTPException(409, "Dieser Chat ist voll — bitte anrufen.")
        await db.auftrag_chat.insert_one(dict(doc))
    except BaseException:
        if doc.get("foto_key"):
            from storage_service import loeschen_oder_vormerken
            await loeschen_oder_vormerken(db, key=doc["foto_key"], grund="chatfoto_nicht_gespeichert",
                                          dealer_id=doc.get("dealer_id") or "")
        raise


def _foto_antwort(daten: bytes, key: str) -> Response:
    from storage_service import guess_media_type
    return Response(content=daten, media_type=guess_media_type(key),
                    headers={"Cache-Control": "private, no-store"})


async def _foto_laden(m: Optional[dict]) -> Response:
    from storage_service import StorageError, load_async
    if not m or not m.get("foto_key"):
        raise HTTPException(404, "Foto nicht gefunden")
    try:
        daten = await load_async(m["foto_key"])
    except StorageError:
        raise HTTPException(404, "Foto nicht gefunden")
    return _foto_antwort(daten, m["foto_key"])


# =========================================================================== Buero (Chef/Sucher)
async def _buero_termin(appt_id: str, user: dict) -> dict:
    appt = await db.appointments.find_one({"id": appt_id, **await termin_bereich(user)}, {"_id": 0})
    if appt is None:
        raise HTTPException(404, "Termin nicht gefunden")
    return appt


def _buero_name(user: dict) -> str:
    name = f"{user.get('first_name') or ''} {user.get('last_name') or ''}".strip()
    return name or ("Chef" if user.get("role") == "dealer" else "Büro")


async def _fahrer_sieht(appt: dict) -> bool:
    """Steht die Fahrt gerade in der App des zugeteilten Fahrers? (ohne Fahrer: nein)"""
    if not appt.get("driver_id") or (appt.get("zuteilung") or "") == "abgelehnt":
        return False
    from routes.drivers import unterlagen_zugriff_oder_404
    try:
        unterlagen_zugriff_oder_404(appt)
    except HTTPException:
        return False
    link = await db.dealer_drivers.find_one(
        {"dealer_id": appt.get("dealer_id"), "driver_account_id": appt["driver_id"]}, {"_id": 1})
    return link is not None


@router.get("/chat/ungelesen")
async def buero_ungelesen(user=Depends(current_firma)):
    """{termine: {appointment_id: Anzahl ungelesener Fahrer-Nachrichten}, gesamt} — fuer die Zaehler."""
    roh = await AC.ungelesen_je_termin(db, {"dealer_id": user["dealer_id"], "von": "fahrer"})
    if roh and ist_sucher(user):
        sichtbar = set(await db.appointments.distinct(
            "id", {"id": {"$in": sorted(roh)}, **await termin_bereich(user)}))
        roh = {k: v for k, v in roh.items() if k in sichtbar}
    return {"termine": roh, "gesamt": sum(roh.values())}


@router.get("/appointments/{appt_id}/chat")
async def buero_chat(appt_id: str, lesen: bool = True, user=Depends(current_firma)):
    """Verlauf (aelteste zuerst). lesen=true markiert die Fahrer-Nachrichten als gelesen (Chat offen)
    und die passenden Chat-Meldungen dieses Kontos ebenso."""
    appt = await _buero_termin(appt_id, user)
    basis = {"appointment_id": appt_id, "dealer_id": user["dealer_id"]}
    if lesen:
        await AC.als_gelesen(db, {**basis, "von": "fahrer"})
        await db.meldungen.update_many(
            {"dealer_id": user["dealer_id"], "empfaenger": user["id"], "typ": AC.MELDUNG_TYP,
             "ref": appt_id, f"gelesen.{user['id']}": {"$exists": False}},
            {"$set": {f"gelesen.{user['id']}": AC.jetzt_iso()}})
    nachrichten = await AC.verlauf(db, basis, "buero")
    fahrer = None
    if appt.get("driver_id"):
        d = await db.driver_accounts.find_one({"id": appt["driver_id"]},
                                              {"_id": 0, "display_name": 1, "phone": 1})
        fahrer = {"name": (d or {}).get("display_name") or "Fahrer", "phone": (d or {}).get("phone") or None}
    offen = await _fahrer_sieht(appt)
    ungelesen = sum(1 for m in nachrichten if not m["eigene"] and not m["gelesen_am"])
    return {"nachrichten": nachrichten, "fahrer": fahrer, "kann_schreiben": offen,
            "hinweis": None if offen else (KEIN_FAHRER if not appt.get("driver_id") else FAHRER_SIEHT_NICHT),
            "schnellantworten": list(AC.SCHNELL_BUERO), "ungelesen": ungelesen,
            "text_max": AC.TEXT_MAX}


@router.post("/appointments/{appt_id}/chat")
async def buero_schreiben(appt_id: str, body: ChatIn, user=Depends(current_firma)):
    text = _text_oder_422(body)
    appt = await _buero_termin(appt_id, user)
    if not appt.get("driver_id"):
        raise HTTPException(409, KEIN_FAHRER)
    if not await _fahrer_sieht(appt):
        raise HTTPException(409, FAHRER_SIEHT_NICHT)
    if not await chat_limiter.check(f"buero:{user['id']}"):
        raise HTTPException(429, ZU_VIELE)
    key = await _foto_speichern(body.foto_b64, user["dealer_id"])
    doc = AC.nachricht_neu(appointment_id=appt_id, dealer_id=user["dealer_id"],
                           fahrer_id=appt["driver_id"], von="buero", von_id=user["id"],
                           von_name=_buero_name(user), text=text,
                           schnell=bool(body.schnell and text in AC.SCHNELL_BUERO), foto_key=key)
    await _speichern(doc)
    await log_activity_sicher(user["dealer_id"], user["id"], "chat.buero.geschrieben", ref=appt_id,
                              meta={"foto": bool(key)})
    return AC.fuer_anzeige(doc, "buero")


@router.get("/appointments/{appt_id}/chat/fotos/{nachricht_id}")
async def buero_foto(appt_id: str, nachricht_id: str, user=Depends(current_firma)):
    await _buero_termin(appt_id, user)
    m = await db.auftrag_chat.find_one({"id": nachricht_id, "appointment_id": appt_id,
                                        "dealer_id": user["dealer_id"]}, {"_id": 0, "foto_key": 1})
    return await _foto_laden(m)


@router.get("/appointments/{appt_id}/chat.pdf")
async def buero_chat_pdf(appt_id: str, user=Depends(current_firma)):
    """Verlauf als kurzes PDF "Nachrichten Büro/Fahrer" fuer die Akte (ohne Fotos, nur Vermerk)."""
    appt = await _buero_termin(appt_id, user)
    nachrichten = await AC.verlauf(db, {"appointment_id": appt_id, "dealer_id": user["dealer_id"]}, "buero")
    firma = await db.dealers.find_one({"id": user["dealer_id"]}, {"_id": 0, "company_name": 1})
    pdf = await asyncio.to_thread(AC.chat_pdf, appt, nachrichten, (firma or {}).get("company_name") or "")
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Cache-Control": "private, no-store",
                             "Content-Disposition": 'inline; filename="Nachrichten_Buero_Fahrer.pdf"'})


# =========================================================================== Fahrer-App
async def _fahrer_termin(appt_id: str, driver: dict) -> dict:
    from routes.drivers import _zugriff_pruefen, unterlagen_zugriff_oder_404
    appt = await db.appointments.find_one({"id": appt_id, "driver_id": driver["id"]}, {"_id": 0})
    if appt is None:
        raise HTTPException(404, "Termin nicht gefunden")
    await _zugriff_pruefen(appt, driver)                 # Fahrerliste der Firma + Firmensperre
    if (appt.get("zuteilung") or "") == "abgelehnt":
        raise HTTPException(404, "Termin nicht gefunden")
    unterlagen_zugriff_oder_404(appt)                    # Sichtfrist (storniert nie, 14/30 Tage)
    return appt


def _fahrer_filter(appt: dict, driver: dict) -> dict:
    return {"appointment_id": appt["id"], "dealer_id": appt.get("dealer_id"), "fahrer_id": driver["id"]}


@router.get("/driver/chat/ungelesen")
async def fahrer_ungelesen(driver=Depends(current_driver)):
    """{termine: {appointment_id: Anzahl ungelesener Buero-Nachrichten}, gesamt} — nur Fahrten, die der
    Fahrer gerade sehen darf."""
    from routes.drivers import _verknuepfte_dealer_ids, unterlagen_zugriff_oder_404
    firmen = await _verknuepfte_dealer_ids(driver["id"])
    if not firmen:
        return {"termine": {}, "gesamt": 0}
    roh = await AC.ungelesen_je_termin(db, {"fahrer_id": driver["id"], "von": "buero",
                                            "dealer_id": {"$in": firmen}})
    raus = {}
    if roh:
        async for a in db.appointments.find(
                {"id": {"$in": sorted(roh)}, "driver_id": driver["id"], "dealer_id": {"$in": firmen}},
                {"_id": 0}):
            if (a.get("zuteilung") or "") == "abgelehnt":
                continue
            try:
                unterlagen_zugriff_oder_404(a)
            except HTTPException:
                continue
            raus[a["id"]] = roh[a["id"]]
    return {"termine": raus, "gesamt": sum(raus.values())}


@router.get("/driver/appointments/{appt_id}/chat")
async def fahrer_chat(appt_id: str, lesen: bool = True, driver=Depends(current_driver)):
    appt = await _fahrer_termin(appt_id, driver)
    filt = _fahrer_filter(appt, driver)
    if lesen:
        await AC.als_gelesen(db, {**filt, "von": "buero"})
    nachrichten = await AC.verlauf(db, filt, "fahrer")
    ungelesen = sum(1 for m in nachrichten if not m["eigene"] and not m["gelesen_am"])
    return {"nachrichten": nachrichten, "kann_schreiben": True, "hinweis": None,
            "schnellantworten": list(AC.SCHNELL_FAHRER), "ungelesen": ungelesen,
            "text_max": AC.TEXT_MAX}


@router.post("/driver/appointments/{appt_id}/chat")
async def fahrer_schreiben(appt_id: str, body: ChatIn, driver=Depends(current_driver)):
    text = _text_oder_422(body)
    appt = await _fahrer_termin(appt_id, driver)
    if not await chat_limiter.check(f"fahrer:{driver['id']}"):
        raise HTTPException(429, ZU_VIELE)
    key = await _foto_speichern(body.foto_b64, appt.get("dealer_id") or "")
    doc = AC.nachricht_neu(appointment_id=appt_id, dealer_id=appt.get("dealer_id"),
                           fahrer_id=driver["id"], von="fahrer", von_id=driver["id"],
                           von_name=driver.get("display_name") or "Fahrer", text=text,
                           schnell=bool(body.schnell and text in AC.SCHNELL_FAHRER), foto_key=key)
    await _speichern(doc)
    # Meldung im Buero: hoechstens eine je Gespraech und Stunde (nie pro Nachricht).
    try:
        if await AC.meldung_beanspruchen(db, appt_id, appt.get("dealer_id")):
            from routes.kundenportal import meldung_anlegen
            empf = await AC.buero_empfaenger(db, appt)
            await meldung_anlegen(appt.get("dealer_id"), empf, AC.MELDUNG_TYP,
                                  AC.meldung_text(appt, driver.get("display_name") or ""), ref=appt_id)
    except Exception:  # noqa: BLE001 — eine Meldung darf die Nachricht nie scheitern lassen
        import logging
        logging.getLogger("autohandel").exception("Chat-Meldung zu %s nicht angelegt", appt_id)
    await log_activity_sicher(appt.get("dealer_id"), driver["id"], "chat.fahrer.geschrieben",
                              ref=appt_id, meta={"foto": bool(key)})
    return AC.fuer_anzeige(doc, "fahrer")


@router.get("/driver/appointments/{appt_id}/chat/fotos/{nachricht_id}")
async def fahrer_foto(appt_id: str, nachricht_id: str, driver=Depends(current_driver)):
    appt = await _fahrer_termin(appt_id, driver)
    m = await db.auftrag_chat.find_one({"id": nachricht_id, **_fahrer_filter(appt, driver)},
                                       {"_id": 0, "foto_key": 1})
    return await _foto_laden(m)
