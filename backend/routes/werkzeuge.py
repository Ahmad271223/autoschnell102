# -*- coding: utf-8 -*-
"""Werkzeuge zum Herunterladen (03.10.2026) — siehe backend/werkzeuge.py.

App (angemeldet):
  GET    /api/werkzeuge                          Liste der Werkzeuge DIESER Firma (sonst leer)
  GET    /api/werkzeuge/{id}/download            Programmdatei — 404 fuer nicht freigegebene Firmen
  POST   /api/werkzeuge/{id}/code                6-stelliger Code zum Verbinden (aktives Abo noetig)
  DELETE /api/werkzeuge/{id}/verbindung          eigenen PC trennen
  GET    /api/werkzeuge/{id}/firma               Chef: Verbindungen + Vergleiche seiner Sucher
  DELETE /api/werkzeuge/{id}/verbindungen/{uid}  Chef: PC eines Kontos der Firma trennen
Programm (Kopfzeile X-Werkzeug-Schluessel):
  POST   /api/werkzeuge/{id}/verbinden           Code -> Schluessel (ohne Anmeldung, gedrosselt)
  GET    /api/werkzeuge/{id}/status              Lizenz pruefen (Abo, Freigabe, Sperren)
  POST   /api/werkzeuge/{id}/vergleich           Abo pruefen, Links mit Firmenregeln, protokollieren
Betreiber:
  GET    /api/admin/werkzeug-vergleiche          wer hat wann welches Auto verglichen
  DELETE /api/admin/werkzeug-verbindungen/{uid}  PC eines Kontos trennen
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

import werkzeuge as wz
from deps import (FIRMA_GESPERRT_TEXT, current_chef, current_firma, current_super_admin, current_user, db,
                  effective_dealer, firma_gesperrt, log_activity_sicher, now_iso, require_active_sub,
                  subscription_for)
from rate_limiter import SlidingWindowRateLimiter, client_ip
from storage_service import StorageError, bloecke_async, groesse_async

log = logging.getLogger("autohandel")
router = APIRouter()

NICHT_GEFUNDEN = "Nicht gefunden"
CODE_FALSCH = "Code ungültig oder abgelaufen – bitte in AutoSchnell einen neuen Code holen."
NICHT_VERBUNDEN = ("Dieses Programm ist nicht (mehr) verbunden – vielleicht wurde dein Konto auf einem "
                   "anderen PC verbunden. Bitte mit einem neuen Code aus AutoSchnell verbinden.")
KEIN_ABO = "Kein aktives AutoSchnell-Abo – das Programm ist gesperrt."

_verbinden_limiter_ip = SlidingWindowRateLimiter(max_attempts=10, window_seconds=600,
                                                 name="werkzeug_verbinden_ip", fail_closed=True)
_code_limiter_konto = SlidingWindowRateLimiter(max_attempts=20, window_seconds=600,
                                               name="werkzeug_code_konto")
_vergleich_limiter = SlidingWindowRateLimiter(max_attempts=120, window_seconds=60,
                                              name="werkzeug_vergleich")

_USER_FELDER = {"_id": 0, "password_hash": 0, "mfa": 0, "settings": 0}


def _wid_pruefen(werkzeug_id: str) -> None:
    if werkzeug_id not in wz.WERKZEUGE:
        raise HTTPException(404, NICHT_GEFUNDEN)


async def _firma(dealer_id: Optional[str]) -> Optional[dict]:
    if not dealer_id:
        return None
    return await db.dealers.find_one({"id": dealer_id},
                                     {"_id": 0, "id": 1, "kunden_nr": 1, "company_name": 1, "loeschung": 1})


async def _kunden_nr(user: dict):
    if user.get("role") not in ("dealer", "sucher") or not user.get("dealer_id"):
        return None
    return ((await _firma(user["dealer_id"])) or {}).get("kunden_nr")


def _konto_text(u: Optional[dict]) -> dict:
    u = u or {}
    name = " ".join(x for x in (u.get("first_name"), u.get("last_name")) if x) \
        or u.get("contact_person") or u.get("name") or ""
    return {"konto": u.get("kontonummer") or "", "name": name, "rolle": u.get("role") or ""}


async def _konten(ids) -> dict:
    ids = [i for i in set(ids) if i]
    if not ids:
        return {}
    raus = {}
    async for u in db.users.find({"id": {"$in": ids}}, {"_id": 0, "id": 1, "kontonummer": 1, "first_name": 1,
                                                         "last_name": 1, "contact_person": 1, "name": 1, "role": 1}):
        raus[u["id"]] = _konto_text(u)
    return raus


# ---------------------------------------------------------------- App: Liste + Download
@router.get("/werkzeuge")
async def werkzeuge_liste(user=Depends(current_user)):
    """Fuer jede Rolle aufrufbar; nur Chef und Sucher einer freigegebenen
    Firma bekommen Eintraege — alle anderen eine leere Liste."""
    kunden_nr = await _kunden_nr(user)
    ids = wz.freigegebene_werkzeuge(kunden_nr) if kunden_nr is not None else []
    liste = []
    for wid in ids:
        meta = await db.werkzeuge.find_one({"id": wid}, {"_id": 0})
        eintrag = wz.oeffentlich(meta, wid)
        v = await db[wz.SAMMLUNG_VERBINDUNGEN].find_one({"werkzeug": wid, "user_id": user["id"]}, {"_id": 0})
        eintrag["verbindung"] = ({"pc_name": v.get("pc_name") or "", "verbunden_am": v.get("verbunden_am"),
                                  "zuletzt_am": v.get("zuletzt_am")} if v else None)
        eintrag["chef"] = user.get("role") == "dealer"
        liste.append(eintrag)
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


# ---------------------------------------------------------------- App: Code + Trennen
@router.post("/werkzeuge/{werkzeug_id}/code")
async def werkzeug_code(werkzeug_id: str, user=Depends(require_active_sub)):
    """6-stelliger Code (10 Minuten, einmal) — nur mit aktivem Abo."""
    _wid_pruefen(werkzeug_id)
    if not wz.ist_freigegeben(werkzeug_id, await _kunden_nr(user)):
        raise HTTPException(404, NICHT_GEFUNDEN)
    if not await _code_limiter_konto.check(f"konto:{user['id']}"):
        raise HTTPException(429, "Zu viele Codes – bitte in ein paar Minuten erneut.")
    codes = db[wz.SAMMLUNG_CODES]
    await codes.delete_many({"werkzeug": werkzeug_id, "user_id": user["id"]})
    jetzt = datetime.now(timezone.utc)
    gueltig_bis = (jetzt + timedelta(minutes=wz.CODE_MINUTEN)).isoformat()
    for _ in range(8):
        code = wz.code_erzeugen()
        h = wz.streuwert(code)
        if await codes.count_documents({"code_hash": h, "benutzt": False,
                                        "gueltig_bis": {"$gt": jetzt.isoformat()}}, limit=1):
            continue
        await codes.insert_one({"id": str(uuid.uuid4()), "werkzeug": werkzeug_id, "code_hash": h,
                                "user_id": user["id"], "dealer_id": user["dealer_id"], "benutzt": False,
                                "gueltig_bis": gueltig_bis, "erstellt_am": jetzt.isoformat()})
        return {"code": code, "gueltig_bis": gueltig_bis, "minuten": wz.CODE_MINUTEN}
    raise HTTPException(503, "Bitte gleich noch einmal versuchen.")


@router.delete("/werkzeuge/{werkzeug_id}/verbindung")
async def werkzeug_trennen(werkzeug_id: str, user=Depends(current_firma)):
    _wid_pruefen(werkzeug_id)
    r = await db[wz.SAMMLUNG_VERBINDUNGEN].delete_many({"werkzeug": werkzeug_id, "user_id": user["id"]})
    if r.deleted_count:
        await log_activity_sicher(user["dealer_id"], user["id"], "werkzeug.getrennt", ref=werkzeug_id)
    return {"ok": True, "getrennt": r.deleted_count > 0}


# ---------------------------------------------------------------- Programm
class VerbindenIn(BaseModel):
    code: str = Field(..., max_length=20)
    pc_name: str = Field("", max_length=80)
    pc_kennung: str = Field("", max_length=128)


async def _konto_pruefen(user: Optional[dict], werkzeug_id: str):
    """Darf dieses Konto das Werkzeug JETZT benutzen? (firma, abo) oder HTTP-Fehler."""
    if not user or user.get("active") is not True:
        raise HTTPException(401, "Konto deaktiviert – bitte den Administrator kontaktieren.")
    if user.get("role") not in ("dealer", "sucher") or not user.get("dealer_id"):
        raise HTTPException(403, "Nur für Händler- und Sucher-Konten.")
    firma = await _firma(user["dealer_id"])
    if not firma or (firma.get("loeschung") or {}).get("status") == "laeuft":
        raise HTTPException(403, "Kein Händlerprofil – bitte den Administrator kontaktieren.")
    if await firma_gesperrt(user["dealer_id"]):
        raise HTTPException(403, FIRMA_GESPERRT_TEXT)
    if not wz.ist_freigegeben(werkzeug_id, firma.get("kunden_nr")):
        raise HTTPException(403, "Dieses Programm ist für dein Konto nicht freigeschaltet.")
    abo = await subscription_for(user)
    if not abo.get("active"):
        raise HTTPException(402, KEIN_ABO)
    return firma, abo


@router.post("/werkzeuge/{werkzeug_id}/verbinden")
async def werkzeug_verbinden(werkzeug_id: str, body: VerbindenIn, request: Request):
    _wid_pruefen(werkzeug_id)
    ip = client_ip(request)
    if not await _verbinden_limiter_ip.check(ip):
        raise HTTPException(429, "Zu viele falsche Codes – bitte in 10 Minuten erneut.")
    code = wz.code_normalisieren(body.code)
    if len(code) != wz.CODE_LAENGE:
        raise HTTPException(404, CODE_FALSCH)
    from pymongo import ReturnDocument
    code_hash = wz.streuwert(code)
    pc = wz._text(body.pc_kennung, 128)
    c = await db[wz.SAMMLUNG_CODES].find_one_and_update(
        {"werkzeug": werkzeug_id, "code_hash": code_hash, "benutzt": False, "gueltig_bis": {"$gt": now_iso()}},
        {"$set": {"benutzt": True, "benutzt_am": now_iso(), "benutzt_von": pc}},
        return_document=ReturnDocument.AFTER)
    if not c and pc:
        # Dieselbe Anfrage kam doppelt an (Netz, Doppelklick): derselbe PC bekommt kurz danach
        # denselben Schluessel noch einmal — ein anderer PC kann den Code nicht nachnutzen.
        grenze = (datetime.now(timezone.utc) - timedelta(seconds=wz.WIEDERHOLUNG_SEKUNDEN)).isoformat()
        c = await db[wz.SAMMLUNG_CODES].find_one(
            {"werkzeug": werkzeug_id, "code_hash": code_hash, "benutzt": True, "benutzt_von": pc,
             "benutzt_am": {"$gt": grenze}})
    if not c:
        raise HTTPException(404, CODE_FALSCH)
    user = await db.users.find_one({"id": c["user_id"]}, _USER_FELDER)
    firma, _abo = await _konto_pruefen(user, werkzeug_id)
    if pc:
        from auth import JWT_SECRET
        schluessel = wz.schluessel_ableiten(JWT_SECRET, c["id"], pc)
    else:
        schluessel = wz.schluessel_erzeugen()
    jetzt = now_iso()
    pc_name = wz._text(body.pc_name, 80)
    # Ein PC je Konto: die neue Verbindung ersetzt die alte (deren Schluessel gilt ab sofort nicht mehr).
    await db[wz.SAMMLUNG_VERBINDUNGEN].update_one(
        {"werkzeug": werkzeug_id, "user_id": user["id"]},
        {"$set": {"id": str(uuid.uuid4()), "token_hash": wz.streuwert(schluessel), "dealer_id": user["dealer_id"],
                  "pc_name": pc_name, "pc_kennung": wz._text(body.pc_kennung, 128),
                  "verbunden_am": jetzt, "zuletzt_am": jetzt}},
        upsert=True)
    await _verbinden_limiter_ip.erstatten(ip)
    await log_activity_sicher(user["dealer_id"], user["id"], "werkzeug.verbunden", ref=werkzeug_id,
                              meta={"pc_name": pc_name})
    konto = _konto_text(user)
    return {"schluessel": schluessel, "konto": konto["konto"], "name": konto["name"],
            "firma": firma.get("company_name") or "", "werkzeug": werkzeug_id}


async def _programm(werkzeug_id: str, schluessel: Optional[str]):
    _wid_pruefen(werkzeug_id)
    if not schluessel:
        raise HTTPException(401, NICHT_VERBUNDEN)
    v = await db[wz.SAMMLUNG_VERBINDUNGEN].find_one(
        {"werkzeug": werkzeug_id, "token_hash": wz.streuwert(schluessel)}, {"_id": 0})
    if not v:
        raise HTTPException(401, NICHT_VERBUNDEN)
    user = await db.users.find_one({"id": v["user_id"]}, _USER_FELDER)
    firma, abo = await _konto_pruefen(user, werkzeug_id)
    await db[wz.SAMMLUNG_VERBINDUNGEN].update_one({"id": v["id"]}, {"$set": {"zuletzt_am": now_iso()}})
    return user, v, firma, abo


@router.get("/werkzeuge/{werkzeug_id}/status")
async def werkzeug_status(werkzeug_id: str,
                          schluessel: Optional[str] = Header(None, alias=wz.TOKEN_KOPF)):
    user, v, firma, abo = await _programm(werkzeug_id, schluessel)
    konto = _konto_text(user)
    dealer = await effective_dealer(user)
    return {"ok": True, "konto": konto["konto"], "name": konto["name"], "firma": firma.get("company_name") or "",
            "pc_name": v.get("pc_name") or "", "abo_bis": abo.get("expires_at"),
            "profil": (dealer or {}).get("active_profile", "inland")}


@router.post("/werkzeuge/{werkzeug_id}/abmelden")
async def werkzeug_abmelden(werkzeug_id: str,
                            schluessel: Optional[str] = Header(None, alias=wz.TOKEN_KOPF)):
    """Das Programm trennt sich selbst (Menue "Verbindung trennen"). Geht auch
    ohne Abo — ein gekuendigter Sucher soll seinen PC freigeben koennen."""
    _wid_pruefen(werkzeug_id)
    if not schluessel:
        return {"ok": True, "getrennt": False}
    v = await db[wz.SAMMLUNG_VERBINDUNGEN].find_one_and_delete(
        {"werkzeug": werkzeug_id, "token_hash": wz.streuwert(schluessel)}, projection={"_id": 0})
    if v:
        await log_activity_sicher(v.get("dealer_id", ""), v.get("user_id", ""), "werkzeug.getrennt",
                                  ref=werkzeug_id, meta={"durch": "programm"})
    return {"ok": True, "getrennt": bool(v)}


class FahrzeugIn(BaseModel):
    marke: str = Field(..., min_length=1, max_length=60)
    modell: str = Field("", max_length=80)
    marke_modell_text: str = Field("", max_length=160)
    titel: str = Field("", max_length=300)
    ez_monat: Optional[int] = Field(None, ge=1, le=12)
    ez_jahr: int = Field(..., ge=1950, le=2100)
    kilometer: int = Field(..., ge=0, le=3_000_000)
    kw: Optional[int] = Field(None, ge=1, le=2000)
    ps: Optional[int] = Field(None, ge=1, le=3000)
    kraftstoff: str = Field("", max_length=60)
    getriebe: str = Field("", max_length=60)
    tueren: str = Field("", max_length=20)
    preis: Optional[int] = Field(None, ge=0, le=100_000_000)
    zustand: str = Field("", max_length=60)
    kategorie: str = Field("", max_length=80)
    quelle: str = Field("", max_length=40)
    inserat_id: str = Field("", max_length=60)


class VergleichIn(BaseModel):
    fahrzeug: FahrzeugIn
    probelauf: bool = False


@router.post("/werkzeuge/{werkzeug_id}/vergleich")
async def werkzeug_vergleich(werkzeug_id: str, body: VergleichIn,
                             schluessel: Optional[str] = Header(None, alias=wz.TOKEN_KOPF)):
    """Abo pruefen, Links mit den Vergleichsregeln der Firma bauen (wie der
    Vergleich in der App: aktives Profil Inland/Export, Sucher-Overrides),
    protokollieren."""
    user, v, _firma_doc, _abo = await _programm(werkzeug_id, schluessel)
    if not await _vergleich_limiter.check(f"verbindung:{v['id']}"):
        raise HTTPException(429, "Zu viele Vergleiche in kurzer Zeit – bitte kurz warten.")
    from mobile_service import DEFAULT_EXPORT_RULES, DEFAULT_RULES
    from regeln import regeln_lesen
    dealer = await effective_dealer(user)
    profil = (dealer or {}).get("active_profile", "inland")
    regeln = (regeln_lesen((dealer or {}).get("export_rules"), DEFAULT_EXPORT_RULES) if profil == "export"
              else regeln_lesen((dealer or {}).get("comparison_rules"), DEFAULT_RULES))
    f = body.fahrzeug.model_dump()
    links, hinweise = wz.vergleichs_links(wz.fahrzeug_zu_vehicle(f), regeln)
    await db[wz.SAMMLUNG_VERGLEICHE].insert_one({
        "id": str(uuid.uuid4()), "werkzeug": werkzeug_id, "dealer_id": user["dealer_id"], "user_id": user["id"],
        "verbindung_id": v["id"], "pc_name": v.get("pc_name") or "", "erstellt_am": now_iso(),
        "fahrzeug": f, "links": links, "hinweise": hinweise, "profil": profil, "probelauf": body.probelauf,
    })
    return {"links": links, "hinweise": hinweise, "profil": profil}


# ---------------------------------------------------------------- Chef
async def _uebersicht(filter_: dict, limit: int, ueberspringen: int = 0) -> dict:
    verbindungen = [v async for v in db[wz.SAMMLUNG_VERBINDUNGEN].find(
        filter_, {"_id": 0, "token_hash": 0, "pc_kennung": 0}).sort("zuletzt_am", -1).limit(500)]
    gesamt = await db[wz.SAMMLUNG_VERGLEICHE].count_documents(filter_)
    vergleiche = [x async for x in db[wz.SAMMLUNG_VERGLEICHE].find(filter_, {"_id": 0})
                  .sort("erstellt_am", -1).skip(ueberspringen).limit(limit)]
    namen = await _konten([x.get("user_id") for x in vergleiche + verbindungen])
    firmen_ids = list({x.get("dealer_id") for x in vergleiche + verbindungen if x.get("dealer_id")})
    firmen = {}
    async for d in db.dealers.find({"id": {"$in": firmen_ids}}, {"_id": 0, "id": 1, "company_name": 1,
                                                                 "kunden_nr": 1}):
        firmen[d["id"]] = {"firma": d.get("company_name") or "", "kunden_nr": d.get("kunden_nr")}
    leer = {"konto": "", "name": "gelöschtes Konto", "rolle": ""}
    for x in vergleiche + verbindungen:
        x.update(namen.get(x.get("user_id"), leer))
        x.update(firmen.get(x.get("dealer_id"), {"firma": "", "kunden_nr": None}))
    return {"vergleiche": vergleiche, "verbindungen": verbindungen, "gesamt": gesamt}


@router.get("/werkzeuge/{werkzeug_id}/firma")
async def werkzeug_firma(werkzeug_id: str, limit: int = Query(200, ge=1, le=500),
                         user=Depends(current_chef)):
    """Chef: wer aus der eigenen Firma hat wann welches Auto verglichen, welche PCs sind verbunden."""
    _wid_pruefen(werkzeug_id)
    if not wz.ist_freigegeben(werkzeug_id, await _kunden_nr(user)):
        raise HTTPException(404, NICHT_GEFUNDEN)
    return await _uebersicht({"werkzeug": werkzeug_id, "dealer_id": user["dealer_id"]}, limit)


@router.delete("/werkzeuge/{werkzeug_id}/verbindungen/{konto_id}")
async def werkzeug_firma_trennen(werkzeug_id: str, konto_id: str, user=Depends(current_chef)):
    _wid_pruefen(werkzeug_id)
    r = await db[wz.SAMMLUNG_VERBINDUNGEN].delete_many(
        {"werkzeug": werkzeug_id, "user_id": konto_id, "dealer_id": user["dealer_id"]})
    if not r.deleted_count:
        raise HTTPException(404, "Keine Verbindung für dieses Konto.")
    await log_activity_sicher(user["dealer_id"], user["id"], "werkzeug.getrennt_durch_chef", ref=konto_id)
    return {"ok": True}


# ---------------------------------------------------------------- Betreiber
@router.get("/admin/werkzeug-vergleiche")
async def admin_werkzeug_vergleiche(werkzeug: str = wz.AUTOPOINTER, dealer_id: Optional[str] = None,
                                    user_id: Optional[str] = None,
                                    limit: int = Query(200, ge=1, le=500), seite: int = Query(1, ge=1, le=1000),
                                    _=Depends(current_super_admin)):
    filter_: dict = {"werkzeug": werkzeug}
    if dealer_id:
        filter_["dealer_id"] = dealer_id
    if user_id:
        filter_["user_id"] = user_id
    daten = await _uebersicht(filter_, limit, (seite - 1) * limit)
    daten["name"] = (wz.WERKZEUGE.get(werkzeug) or {}).get("name", werkzeug)
    daten["freigegeben_fuer"] = sorted(wz.freigegebene_kunden(werkzeug))
    return daten


@router.delete("/admin/werkzeug-verbindungen/{konto_id}")
async def admin_werkzeug_trennen(konto_id: str, werkzeug: str = wz.AUTOPOINTER,
                                 admin=Depends(current_super_admin)):
    r = await db[wz.SAMMLUNG_VERBINDUNGEN].delete_many({"werkzeug": werkzeug, "user_id": konto_id})
    await log_activity_sicher(admin.get("dealer_id", ""), admin["id"], "admin.werkzeug.getrennt", ref=konto_id)
    return {"ok": True, "getrennt": r.deleted_count}
