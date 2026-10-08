# -*- coding: utf-8 -*-
"""Werkzeuge zum Herunterladen (03.10.2026) — siehe backend/werkzeuge.py.

App (angemeldet):
  GET    /api/werkzeuge                          Liste der Werkzeuge DIESER Firma (sonst leer)
  GET    /api/werkzeuge/{id}/download            Programmdatei — 404 fuer nicht freigegebene Firmen
  POST   /api/werkzeuge/{id}/code                6-stelliger Code zum Verbinden (aktives Abo noetig)
  DELETE /api/werkzeuge/{id}/verbindung          eigenen PC trennen
  GET    /api/werkzeuge/{id}/firma               Chef: Verbindungen + Vergleiche seiner Sucher
  DELETE /api/werkzeuge/{id}/verbindungen/{uid}  Chef: PC eines Kontos der Firma trennen
  POST   /api/werkzeuge/app-start/{start}        App meldet: Auto aus dem Programm uebernommen (Nr. 12)
Programm (Kopfzeile X-Werkzeug-Schluessel):
  POST   /api/werkzeuge/{id}/verbinden           Code -> Schluessel (ohne Anmeldung, gedrosselt)
  GET    /api/werkzeuge/{id}/status              Lizenz pruefen (Abo, Freigabe, Sperren) + angebotene Version
  POST   /api/werkzeuge/{id}/vergleich           Abo pruefen, Links mit Firmenregeln, protokollieren
  GET    /api/werkzeuge/{id}/app-start/{start}   hat die App das Auto uebernommen? (Nr. 12)
Browser-Helfer (04.10.2026, nur Werkzeuge mit art "browser", Kopfzeile X-Werkzeug-Schluessel):
  POST   /api/werkzeuge/{id}/inserat             Inseratsseite aus dem Browser -> Links, Vertragsdaten, Hinweise
  POST   /api/werkzeuge/{id}/marktlage           Vergleichsseite aus dem Browser -> Platz + Ampel
  POST   /api/werkzeuge/{id}/programm-suche      gehoert diese Vergleichsseite zu einem Vergleich des Programms?
Betreiber:
  GET    /api/admin/werkzeug-vergleiche          wer hat wann welches Auto verglichen
  DELETE /api/admin/werkzeug-verbindungen/{uid}  PC eines Kontos trennen
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

import werkzeuge as wz
import werkzeug_erkennung
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
#: Pruefung Browser-Helfer 05.10.2026 (Nr. 24): der Helfer laeuft im Browser — keine Texte von "PC" und "Programm"
NICHT_VERBUNDEN_BROWSER = ("AutoSchnell Analyse und Vertragsabwicklung ist nicht (mehr) verbunden – vielleicht wurde dein Konto in einem "
                           "anderen Browser verbunden. Bitte auf das AutoSchnell-Symbol klicken und mit einem neuen "
                           "Code aus AutoSchnell verbinden.")
KEIN_ABO_BROWSER = "Kein aktives AutoSchnell-Abo – die Erweiterung ist gesperrt."


def _ist_browser(werkzeug_id: str) -> bool:
    return wz.art(werkzeug_id) == "browser"


_verbinden_limiter_ip = SlidingWindowRateLimiter(max_attempts=10, window_seconds=600,
                                                 name="werkzeug_verbinden_ip", fail_closed=True)
#: Pruefung 05.10.2026 (Nr. 23): auch ueber ALLE Adressen hoechstens 200 falsche Codes in 10 Minuten — ein Raten mit
#: vielen Adressen kommt so nicht weit (6 Stellen, 10 min gueltig, einmal nutzbar). Trifft nur NEUE Verbindungen.
_verbinden_limiter_gesamt = SlidingWindowRateLimiter(max_attempts=200, window_seconds=600,
                                                     name="werkzeug_verbinden_gesamt", fail_closed=True)
_code_limiter_konto = SlidingWindowRateLimiter(max_attempts=20, window_seconds=600,
                                               name="werkzeug_code_konto")
_vergleich_limiter = SlidingWindowRateLimiter(max_attempts=120, window_seconds=60,
                                              name="werkzeug_vergleich")

_USER_FELDER = {"_id": 0, "password_hash": 0, "mfa": 0, "settings": 0}

#: Nr. 12: Kennung eines App-Starts (das Programm erzeugt 32 Hex-Zeichen)
_START_KENNUNG = re.compile(r"^[0-9a-f]{32}$")
_PROGRAMM_VERSION = re.compile(r"AutoSchnell-Vergleich/(\d+(?:\.\d+){1,3})")


def _programm_version(user_agent: Optional[str], kopf: Optional[str] = None) -> Optional[str]:
    """Nr. 4: Version des Programms aus der Kopfzeile User-Agent ("AutoSchnell-Vergleich/1.5.0").
    Eine Browser-Erweiterung darf den User-Agent nicht setzen — sie schickt X-Werkzeug-Version."""
    if kopf and re.fullmatch(r"\d+(?:\.\d+){1,3}", kopf.strip()):
        return kopf.strip()
    m = _PROGRAMM_VERSION.search(user_agent or "")
    return m.group(1) if m else None


def _berlin(iso: Optional[str]) -> str:
    try:
        from zoneinfo import ZoneInfo
        return datetime.fromisoformat(str(iso)).astimezone(ZoneInfo("Europe/Berlin")).strftime("%d.%m.%Y um %H:%M")
    except Exception:  # noqa: BLE001
        return ""


async def _getrennt_merken(werkzeug_id: str, token_hashes, grund: str, pc_name: str = "") -> None:
    """Nr. 16: festhalten, WARUM ein Schluessel nicht mehr gilt — das Programm sagt es dem Sucher dann genau
    (vorher immer nur "vielleicht anderer PC"). 30 Tage, danach gilt wieder der allgemeine Text."""
    jetzt = datetime.now(timezone.utc)
    for h in {h for h in token_hashes if h}:
        try:
            await db[wz.SAMMLUNG_GETRENNT].update_one(
                {"werkzeug": werkzeug_id, "token_hash": h},
                {"$set": {"grund": grund, "pc_name": pc_name, "am": jetzt.isoformat(),
                          "ablauf": jetzt + timedelta(days=30)}},
                upsert=True)
        except Exception:  # noqa: BLE001 — nur fuer die Meldung, nie den Vorgang aufhalten
            log.exception("Werkzeug: Trenn-Grund nicht gespeichert")


async def alle_trennen(user_id: str, grund: str = "passwort") -> int:
    """Entscheidung Ahmad 06.10.2026: ein neues Passwort trennt Programm UND Browser-Helfer des Kontos — ein
    gestohlenes Konto behielt sonst den Zugang ueber die Werkzeuge. Der naechste Aufruf bekommt 401 mit dem Grund,
    verbinden geht dann mit einem neuen Code. Rueckgabe: Zahl der getrennten Verbindungen."""
    getrennt = 0
    for werkzeug_id in wz.WERKZEUGE:
        filt = {"werkzeug": werkzeug_id, "user_id": user_id}
        alte = [x.get("token_hash") async for x in db[wz.SAMMLUNG_VERBINDUNGEN].find(filt, {"_id": 0, "token_hash": 1})]
        if not alte:
            continue
        r = await db[wz.SAMMLUNG_VERBINDUNGEN].delete_many(filt)
        if r.deleted_count:
            await _getrennt_merken(werkzeug_id, alte, grund)
            getrennt += r.deleted_count
    return getrennt


async def _nicht_verbunden_text(werkzeug_id: str, schluessel: str) -> str:
    g = await db[wz.SAMMLUNG_GETRENNT].find_one(
        {"werkzeug": werkzeug_id, "token_hash": wz.streuwert(schluessel)}, {"_id": 0})
    browser = _ist_browser(werkzeug_id)
    if not g:
        return NICHT_VERBUNDEN_BROWSER if browser else NICHT_VERBUNDEN
    wann = _berlin(g.get("am"))
    am = f" am {wann}" if wann else ""
    if browser:
        if g.get("grund") == "anderer_pc":
            wo = f" („{g['pc_name']}“)" if g.get("pc_name") else ""
            return (f"Dein Konto wurde{am} in einem anderen Browser{wo} verbunden – ein Konto kann nur in einem "
                    "Browser verbunden sein. Zum Zurückwechseln auf das AutoSchnell-Symbol klicken und mit einem "
                    "neuen Code verbinden.")
        wer = {"chef": "Dein Chef hat", "betreiber": "AutoSchnell hat"}.get(g.get("grund"))
        if wer:
            return f"{wer} diesen Browser{am} getrennt. Bitte auf das AutoSchnell-Symbol klicken und neu verbinden."
        if g.get("grund") == "app":
            return f"Die Erweiterung wurde{am} in der AutoSchnell-App getrennt. Bitte auf das AutoSchnell-Symbol klicken."
        if g.get("grund") == "passwort":
            return (f"Das Passwort deines Kontos wurde{am} geändert – zur Sicherheit wurde die Erweiterung getrennt. "
                    "Bitte auf das AutoSchnell-Symbol klicken und mit einem neuen Code verbinden.")
        return NICHT_VERBUNDEN_BROWSER
    if g.get("grund") == "anderer_pc":
        pc = f" („{g['pc_name']}“)" if g.get("pc_name") else ""
        return (f"Dein Konto wurde{am} auf einem anderen PC{pc} verbunden – ein Konto kann nur auf einem PC "
                "verbunden sein, dieser ist deshalb getrennt. Zum Zurückwechseln hier mit einem neuen Code aus "
                "AutoSchnell verbinden.")
    if g.get("grund") == "chef":
        return f"Dein Chef hat diesen PC{am} von AutoSchnell getrennt. Bitte mit einem neuen Code verbinden."
    if g.get("grund") == "betreiber":
        return f"AutoSchnell hat diesen PC{am} getrennt. Bitte mit einem neuen Code verbinden."
    if g.get("grund") == "app":
        return f"Diese Verbindung wurde{am} in der AutoSchnell-App getrennt. Bitte mit einem neuen Code verbinden."
    if g.get("grund") == "passwort":
        return (f"Das Passwort deines Kontos wurde{am} geändert – zur Sicherheit wurde dieser PC getrennt. "
                "Bitte mit einem neuen Code aus AutoSchnell verbinden.")
    return NICHT_VERBUNDEN


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
        media_type=("application/zip" if wz.art(werkzeug_id) == "browser"
                    else "application/vnd.microsoft.portable-executable"),
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
    filt = {"werkzeug": werkzeug_id, "user_id": user["id"]}
    alte = [x.get("token_hash") async for x in db[wz.SAMMLUNG_VERBINDUNGEN].find(filt, {"_id": 0, "token_hash": 1})]
    r = await db[wz.SAMMLUNG_VERBINDUNGEN].delete_many(filt)
    if r.deleted_count:
        await _getrennt_merken(werkzeug_id, alte, "app")
        await log_activity_sicher(user["dealer_id"], user["id"], "werkzeug.getrennt", ref=werkzeug_id)
    return {"ok": True, "getrennt": r.deleted_count > 0}


# ---------------------------------------------------------------- Programm
class VerbindenIn(BaseModel):
    code: str = Field(..., max_length=20)
    pc_name: str = Field("", max_length=80)
    pc_kennung: str = Field("", max_length=128)


async def _konto_pruefen(user: Optional[dict], werkzeug_id: str):
    """Darf dieses Konto das Werkzeug JETZT benutzen? (firma, abo) oder HTTP-Fehler."""
    if not user:
        raise HTTPException(401, "Konto nicht mehr vorhanden – bitte neu verbinden.")
    if user.get("active") is not True:
        # Paket 2 (Pruefung 05.10.2026): 403 statt 401 — bei 401 warf das Programm seinen Schluessel weg und
        # brauchte nach dem Reaktivieren einen neuen Code
        raise HTTPException(403, "Konto deaktiviert – bitte den Administrator kontaktieren.")
    if user.get("role") not in ("dealer", "sucher") or not user.get("dealer_id"):
        raise HTTPException(403, "Nur für Händler- und Sucher-Konten.")
    # Lasttest 07.10.2026 ("keiner soll warten"): Firma, Sperre und Abo haengen nur vom Konto ab — gleichzeitig
    # abfragen statt nacheinander (drei Datenbank-Rundreisen weniger in der Kette); geprueft wird in der alten Reihenfolge
    import asyncio
    firma, gesperrt, abo = await asyncio.gather(
        _firma(user["dealer_id"]), firma_gesperrt(user["dealer_id"]), subscription_for(user))
    if not firma or (firma.get("loeschung") or {}).get("status") == "laeuft":
        raise HTTPException(403, "Kein Händlerprofil – bitte den Administrator kontaktieren.")
    if gesperrt:
        raise HTTPException(403, FIRMA_GESPERRT_TEXT)
    if not wz.ist_freigegeben(werkzeug_id, firma.get("kunden_nr")):
        raise HTTPException(403, "Dieses Programm ist für dein Konto nicht freigeschaltet.")
    if not abo.get("active"):
        raise HTTPException(402, KEIN_ABO_BROWSER if _ist_browser(werkzeug_id) else KEIN_ABO)
    return firma, abo


@router.post("/werkzeuge/{werkzeug_id}/verbinden")
async def werkzeug_verbinden(werkzeug_id: str, body: VerbindenIn, request: Request):
    _wid_pruefen(werkzeug_id)
    ip = client_ip(request)
    if not await _verbinden_limiter_ip.check(ip):
        raise HTTPException(429, "Zu viele falsche Codes – bitte in 10 Minuten erneut.")
    if not await _verbinden_limiter_gesamt.check("alle"):
        await _verbinden_limiter_ip.erstatten(ip)
        log.warning("Werkzeug: Grenze fuer falsche Codes insgesamt erreicht (Raten ueber viele Adressen?)")
        raise HTTPException(429, "Gerade zu viele Verbindungsversuche – bitte in ein paar Minuten erneut.")
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
    alt = await db[wz.SAMMLUNG_VERBINDUNGEN].find_one(
        {"werkzeug": werkzeug_id, "user_id": user["id"]}, {"_id": 0, "token_hash": 1, "pc_kennung": 1})
    if alt and alt.get("token_hash") != wz.streuwert(schluessel) \
            and (alt.get("pc_kennung") or "") != wz._text(body.pc_kennung, 128):
        await _getrennt_merken(werkzeug_id, [alt.get("token_hash")], "anderer_pc", pc_name)
    # Ein PC je Konto: die neue Verbindung ersetzt die alte (deren Schluessel gilt ab sofort nicht mehr).
    await db[wz.SAMMLUNG_VERBINDUNGEN].update_one(
        {"werkzeug": werkzeug_id, "user_id": user["id"]},
        {"$set": {"id": str(uuid.uuid4()), "token_hash": wz.streuwert(schluessel), "dealer_id": user["dealer_id"],
                  "pc_name": pc_name, "pc_kennung": wz._text(body.pc_kennung, 128),
                  "verbunden_am": jetzt, "zuletzt_am": jetzt}},
        upsert=True)
    await _verbinden_limiter_ip.erstatten(ip)
    await _verbinden_limiter_gesamt.erstatten("alle")
    await log_activity_sicher(user["dealer_id"], user["id"], "werkzeug.verbunden", ref=werkzeug_id,
                              meta={"pc_name": pc_name})
    konto = _konto_text(user)
    return {"schluessel": schluessel, "konto": konto["konto"], "name": konto["name"],
            "firma": firma.get("company_name") or "", "werkzeug": werkzeug_id}


async def _programm(werkzeug_id: str, schluessel: Optional[str], version: Optional[str] = None):
    _wid_pruefen(werkzeug_id)
    if not schluessel:
        raise HTTPException(401, NICHT_VERBUNDEN_BROWSER if _ist_browser(werkzeug_id) else NICHT_VERBUNDEN)
    v = await db[wz.SAMMLUNG_VERBINDUNGEN].find_one(
        {"werkzeug": werkzeug_id, "token_hash": wz.streuwert(schluessel)}, {"_id": 0})
    if not v:
        # Nr. 16: genau sagen, warum (anderer PC, Chef, Betreiber, App) — sonst der allgemeine Text
        raise HTTPException(401, await _nicht_verbunden_text(werkzeug_id, schluessel))
    user = await db.users.find_one({"id": v["user_id"]}, _USER_FELDER)
    firma, abo = await _konto_pruefen(user, werkzeug_id)
    # Paket 3 (Pruefung 05.10.2026): "zuletzt aktiv" hoechstens einmal je Minute schreiben, die Version nur,
    # wenn sie sich aendert — vorher ein Schreibzugriff je Anfrage (Helfer: mehrere je Inserat)
    setzen = {}
    zuletzt = str(v.get("zuletzt_am") or "")
    if zuletzt < (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat():
        setzen["zuletzt_am"] = now_iso()
    if version and version != v.get("programm_version"):
        setzen["programm_version"] = version          # Nr. 4: welche Version laeuft auf welchem PC
    if setzen:
        await db[wz.SAMMLUNG_VERBINDUNGEN].update_one({"id": v["id"]}, {"$set": setzen})
    return user, v, firma, abo


@router.get("/werkzeuge/{werkzeug_id}/status")
async def werkzeug_status(werkzeug_id: str,
                          schluessel: Optional[str] = Header(None, alias=wz.TOKEN_KOPF),
                          user_agent: Optional[str] = Header(None, alias="User-Agent"),
                          version: Optional[str] = Header(None, alias="X-Werkzeug-Version")):
    user, v, firma, abo = await _programm(werkzeug_id, schluessel, _programm_version(user_agent, version))
    konto = _konto_text(user)
    dealer = await effective_dealer(user)
    # Nr. 4: die Version, die AutoSchnell gerade zum Herunterladen anbietet — ist sie neuer, sagt es das Programm
    meta = await db.werkzeuge.find_one({"id": werkzeug_id}, {"_id": 0, "version": 1}) or {}
    antwort = {"ok": True, "konto": konto["konto"], "name": konto["name"], "firma": firma.get("company_name") or "",
               "pc_name": v.get("pc_name") or "", "abo_bis": abo.get("expires_at"),
               "profil": (dealer or {}).get("active_profile", "inland"),
               "aktuelle_version": meta.get("version"), "programm_name": wz.WERKZEUGE[werkzeug_id]["name"],
               # 08.10.2026: Portalwahl kommt aus AutoSchnell (Programm ab 1.5.8, Erweiterung ab 2.7.2 zeigen sie)
               "portale": wz.portale_von(user)}
    if _ist_browser(werkzeug_id):
        # Pruefung 05.10.2026 (Nr. 15): der Helfer fragt /programm-suche nur, wenn das Konto das Windows-Programm hat
        antwort["programm_verbunden"] = await db[wz.SAMMLUNG_VERBINDUNGEN].count_documents(
            {"werkzeug": wz.AUTOPOINTER, "user_id": user["id"]}, limit=1) > 0
    return antwort


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
    #: AutoScout24: Kennung fuer den Inserat-Link (nur wenn vollstaendig gelesen)
    hash_id: str = Field("", max_length=60)
    #: Seit Programm 1.4.0 (Wunsch Ahmad 03.10.2026): Marke/Modell sind nur Rohtext (erstes Wort / Rest) —
    #: der SERVER erkennt sie aus marke_modell_text + titel (werkzeug_erkennung). Aeltere Programme schicken
    #: schon erkannte Werte (roh=False) und werden wie bisher behandelt.
    roh: bool = False
    #: Seit Programm 1.4.1: Anfang der Beschreibung (vom Bildschirm gelesen) — nur, falls Feld und Ueberschrift
    #: kein Modell hergeben (Befund 04.10.2026: "meinen Mercedes C 300 e"). Wird nicht gespeichert.
    beschreibung: str = Field("", max_length=2000)


class VergleichIn(BaseModel):
    fahrzeug: FahrzeugIn
    probelauf: bool = False


@router.post("/werkzeuge/{werkzeug_id}/vergleich")
async def werkzeug_vergleich(werkzeug_id: str, body: VergleichIn,
                             schluessel: Optional[str] = Header(None, alias=wz.TOKEN_KOPF),
                             user_agent: Optional[str] = Header(None, alias="User-Agent")):
    """Abo pruefen, Links mit den Vergleichsregeln der Firma bauen (wie der
    Vergleich in der App: aktives Profil Inland/Export, Sucher-Overrides),
    protokollieren."""
    programm_version = _programm_version(user_agent)
    user, v, _firma_doc, _abo = await _programm(werkzeug_id, schluessel, programm_version)
    if not await _vergleich_limiter.check(f"konto:{user['id']}"):      # je Konto (Paket 2): Neu-Verbinden hilft nicht
        raise HTTPException(429, "Zu viele Vergleiche in kurzer Zeit – bitte kurz warten.")
    # Entscheidung Ahmad 06.10.2026: hoechstens 600 Vergleiche je Konto und Tag ueber das Programm
    # (PROGRAMM_TAGESLIMIT_JE_KONTO); der Probelauf zaehlt nicht
    from provider_fetch import TageslimitErreicht, programm_tageslimit
    import asyncio
    f = body.fahrzeug.model_dump()

    async def tageslimit():
        if body.probelauf:
            return None
        try:
            return (await programm_tageslimit(db, user["id"]))[2]
        except TageslimitErreicht as exc:
            raise HTTPException(429, str(exc))

    # Lasttest 07.10.2026 ("keiner soll warten"): Tageslimit, Firmenregeln, Helfer-Frage und die Erkennung (Pruefung
    # 05.10., Paket 1: im Thread, bis 0,2 s) haengen nicht voneinander ab — gleichzeitig statt nacheinander.
    # Faellt das Tageslimit (429), sind die anderen Ergebnisse ohne Folgen (nur Lesen).
    verbleibend, (profil, regeln), helfer, erkannt = await asyncio.gather(
        tageslimit(), _firmenregeln(user),
        db[wz.SAMMLUNG_VERBINDUNGEN].find_one({"werkzeug": wz.BROWSER_HELFER, "user_id": user["id"]},
                                              {"_id": 0, "programm_version": 1}),
        asyncio.to_thread(_erkennen, f))
    # 08.10.2026 (Durchsicht vor dem Rollout): neue Wege nur, wenn Programm UND Erweiterung sie kennen —
    # aeltere Versionen arbeiten wie bisher (Vorab-Abruf ueber Apify, Programm oeffnet die Vergleiche selbst)
    helfer_version = (helfer or {}).get("programm_version")
    inserat_tab_bekannt = (wz.version_mindestens(programm_version, wz.INSERAT_TAB_PROGRAMM)
                           and wz.version_mindestens(helfer_version, wz.INSERAT_TAB_HELFER))
    vorgang_bekannt = (wz.version_mindestens(programm_version, wz.VORGANG_PROGRAMM)
                       and wz.version_mindestens(helfer_version, wz.VORGANG_HELFER))
    f["inserat_url"] = wz.inserat_url(f.get("quelle"), f.get("inserat_id"), f.get("hash_id"))
    vehicle = wz.fahrzeug_zu_vehicle(f)
    # Befund 04.10.2026: unplausible EZ/km sagen — die Filter bleiben wie eingestellt (wz.plausibel)
    melden = wz.plausibel(f)
    links, hinweise = wz.vergleichs_links(vehicle, regeln)      # Navi + Beschaedigte wie eingestellt (04.10.)
    # 08.10.2026: nur die Portale, die das Konto in AutoSchnell gewaehlt hat (eine Wahl fuer App/Programm/Helfer)
    links, hinweise = wz.nach_portalen(links, hinweise, wz.portale_von(user))
    if erkannt.pop("aus_beschreibung", False):
        melden.insert(0, f"Modell aus der Beschreibung übernommen: {erkannt['modell']} – bitte kurz prüfen.")
    if verbleibend in (50, 20, 5, 1):
        melden.append(f"Noch {verbleibend} Vergleich{'e' if verbleibend != 1 else ''} heute über das Programm "
                      "(Tageslimit), dann wieder ab 0 Uhr.")
    # "melden": was das Programm (ab 1.5.3) dem Sucher sofort zeigt; aeltere Programme protokollieren die Hinweise
    hinweise = melden + hinweise
    # Wunsch Ahmad 03.10.2026: das Inserat schon jetzt im Hintergrund auslesen (Daten + Fotos), damit
    # der Kaufvertrag ohne Link-Einfuegen geht — derselbe Weg wie das Einfuegen in der App.
    # Wunsch Ahmad 07.10.2026: hat das Konto den Browser-Helfer, liest der das Inserat im Browser — das Programm
    # oeffnet das Inserat als Tab mit, der Helfer schickt die Seite (werkzeug_inserate, 24 h), der Kaufvertrag nimmt
    # sie. Kein Apify, kein Tageslimit, keine 32/128 Plaetze. Ohne Helfer wie bisher: Vorab-Abruf ueber Apify.
    # ohne Link (abgewaehltes oder unbekanntes Portal) oeffnet das Programm auch keinen Inserat-Tab -> Vorab wie bisher
    im_browser = (not body.probelauf and bool(f["inserat_url"]) and wz.inserat_im_browser_an() and helfer is not None
                  and bool(links) and inserat_tab_bekannt)
    # Wunsch Ahmad 08.10.2026 (Vorgangsnummer): hat das Konto die Erweiterung, oeffnet das Programm (ab 1.5.8) nur
    # /app/vorgang/<id> — die Erweiterung holt sich den Vorgang und oeffnet Vergleiche + Inserat selbst. So oeffnet
    # genau EINER die Tabs, und die Erweiterung kennt sie (keine Programm-Suche, kein 30-Minuten-Raten).
    ueber_helfer = not body.probelauf and helfer is not None and bool(links) and vorgang_bekannt
    if body.probelauf:
        vorab = {"status": "probelauf", "hinweis": ""}
    elif im_browser:
        vorab = {"status": "browser", "hinweis": ""}
    else:
        vorab = await _vorab_abrufen(user, f["inserat_url"])
    eintrag = {
        "id": str(uuid.uuid4()), "werkzeug": werkzeug_id, "dealer_id": user["dealer_id"], "user_id": user["id"],
        "verbindung_id": v["id"], "pc_name": v.get("pc_name") or "", "erstellt_am": now_iso(),
        "fahrzeug": f, "links": links, "hinweise": hinweise, "profil": profil, "probelauf": body.probelauf,
        "vorab": vorab["status"], "ablauf": wz.vergleich_ablauf(), "ueber_helfer": ueber_helfer,
    }
    if body.probelauf:
        await db[wz.SAMMLUNG_VERGLEICHE].insert_one(eintrag)
    else:
        # Vorab-Abruf tauschen (im Browser-Fall: einen noch wartenden Apify-Abruf des vorigen Autos zurueckziehen)
        # und den Vergleich protokollieren — unabhaengig voneinander, also gleichzeitig
        await asyncio.gather(_vorab_ersetzen(user, v, vorab.get("job_id")),
                             db[wz.SAMMLUNG_VERGLEICHE].insert_one(eintrag))
    return {"links": links, "hinweise": hinweise, "profil": profil, "inserat_url": f["inserat_url"],
            "vorab": {"status": vorab["status"], "hinweis": vorab.get("hinweis", "")}, "fahrzeug": erkannt,
            "melden": melden,
            # Programm ab 1.5.7: das Inserat als Tab mit oeffnen, der Browser-Helfer liest es dort
            "inserat_im_browser": im_browser,
            # Programm ab 1.5.8: Vorgangsnummer; ueber_helfer -> nur /app/vorgang/<id> oeffnen (s. o.)
            "vorgang_id": eintrag["id"], "ueber_helfer": ueber_helfer}


def _erkennen(f: dict) -> dict:
    """Programm ab 1.4.0 (roh=True): Marke/Modell auf dem Server erkennen und in f eintragen — so, wie es das
    Programm bis 1.3.5 selbst tat (1:1 uebertragen, am 03.10.2026 ueber 16.636 Faelle abgeglichen).
    Rueckgabe fuer die Anzeige im Programm: Katalognamen und ob die Marke bekannt ist."""
    if not f.get("roh"):
        f.pop("beschreibung", None)
        return {"marke": f.get("marke") or "", "modell": f.get("modell") or "", "erkannt": True}
    text = (f.get("marke_modell_text") or f"{f.get('marke') or ''} {f.get('modell') or ''}").strip()
    z = werkzeug_erkennung.zuordnen(text, f.get("titel"), beschreibung=f.pop("beschreibung", "") or None)
    f["marke"], f["modell"] = z["marke_text"], z["modell_text"]
    return {"marke": z["marke"] or text, "modell": z["modell"] or "", "erkannt": z["erkannt"],
            "aus_beschreibung": z["aus_beschreibung"]}


async def _vorab_ersetzen(user: dict, v: dict, neuer_job: Optional[str]) -> None:
    """Wunsch Ahmad 03.10.2026: neues Auto angeklickt -> den alten, noch wartenden Vorab-Abruf dieses Kontos
    zurueckziehen und den neuen merken. Fehler hier duerfen den Vergleich nie aufhalten."""
    try:
        # Paket 2 (Pruefung 05.10.2026): in EINEM Zug tauschen — zwei schnelle Klicks lasen vorher beide den alten
        # Job, einer der neuen wurde nie zurueckgezogen (ein Apify-Abruf und Tageskontingent umsonst)
        from pymongo import ReturnDocument
        vorher = await db[wz.SAMMLUNG_VERBINDUNGEN].find_one_and_update(
            {"id": v["id"]}, {"$set": {"vorab_job_id": neuer_job}},
            projection={"_id": 0, "vorab_job_id": 1}, return_document=ReturnDocument.BEFORE)
        alter_job = (vorher or {}).get("vorab_job_id")
        if alter_job and alter_job != neuer_job:
            from link_jobs import vorab_zurueckziehen
            await vorab_zurueckziehen(db, alter_job, user.get("dealer_id") or "", user.get("id") or "")
    except Exception:  # noqa: BLE001
        log.exception("Werkzeug: alter Vorab-Abruf nicht zurueckgezogen")


async def _vorab_abrufen(user: dict, url: Optional[str]) -> dict:
    """Inserat fuer den Kaufvertrag vorab auslesen — ueber POST /listings/check (dieselben Regeln wie
    beim Einfuegen in der App: Quellen-Freigabe, Speicher-Treffer kostenlos, Kleinanzeigen-Erweiterung,
    Warteschlange, Tageslimit je Konto). Der Sucher oeffnet danach /app/vergleich?url=…, der Vergleich
    steht sofort mit Fotos da und uebernimmt das Fahrzeug fuer den Vertrag."""
    if not url:
        return {"status": "kein_link", "hinweis": "Inserat-Adresse unbekannt – für den Kaufvertrag bitte die "
                                                  "Adresse kopieren und in AutoSchnell einfügen."}
    if not wz.vorab_abruf_an():
        return {"status": "aus", "hinweis": ""}
    from routes.listings import ListingURLIn, listing_pruefen
    try:
        r = await listing_pruefen(ListingURLIn(url=url), user, vorab_warten_s=wz.vorab_warten_s())
    except HTTPException as exc:
        status = "limit" if exc.status_code == 429 else "fehler"
        return {"status": status, "hinweis": str(exc.detail)[:200]}
    except Exception:  # noqa: BLE001 — der Vergleich darf am Vorab-Abruf nie scheitern
        log.exception("Werkzeug: Vorab-Abruf %s gescheitert", url)
        return {"status": "fehler", "hinweis": ""}
    if r.get("status") == "completed":
        return {"status": "fertig", "hinweis": ""}
    if r.get("status") == "needs_client_fetch":
        return {"status": "in_app", "hinweis": "Dieses Inserat liest AutoSchnell beim Öffnen in der App aus."}
    return {"status": "laeuft", "hinweis": "", "job_id": r.get("job_id")}


# ---------------------------------------------------------------- Browser-Helfer (04.10.2026)
# Wunsch Ahmad 04.10.2026: eigene Erweiterung fuer Chrome/Edge. Beim Oeffnen eines Inserats schickt sie die
# Seite (gzip, base64) — der Server baut die Vergleichslinks mit den Firmenregeln, merkt die Daten fuer den
# Kaufvertrag (browser_helfer.inserat_merken; seit 04.10. abends fuer alle Konten) und gibt Hinweise zurueck. Die
# Vergleichsseite, die die Erweiterung danach oeffnet, kommt ueber /marktlage zurueck: Platz + Ampel.
# Kein Apify-Abruf, kein Tageslimit, keine KI.
_inserat_limiter = SlidingWindowRateLimiter(max_attempts=120, window_seconds=60, name="werkzeug_inserat")
_marktlage_limiter = SlidingWindowRateLimiter(max_attempts=240, window_seconds=60, name="werkzeug_marktlage")
#: base64(gzip(HTML)): ~4/3 der gepackten Groesse (browser_helfer.MAX_GEPACKT)
_SEITE_MAX = 4 * 1024 * 1024 + 16
#: Pruefung 05.10.2026 (Nr. 11): Entpacken + Auswerten laeuft in Threads — je Prozess hoechstens so viele
#: gleichzeitig, damit eine Welle grosser Seiten nicht alle Threads (und damit jede andere Anfrage) belegt
_AUSWERTEN_GLEICHZEITIG = 4
_auswerten_sperre = None


_AUSWERTEN_WARTEN_S = 10


async def _auswerten(fn, *args):
    """Seite im Thread auswerten — hoechstens _AUSWERTEN_GLEICHZEITIG je Prozess. Paket 2 (05.10.2026): wer
    laenger als _AUSWERTEN_WARTEN_S auf einen Platz wartet, bekommt 503 mit Retry-After statt endlos zu warten
    (Cloudflare bricht nach ~100 s ohnehin ab, die Arbeit liefe dann umsonst weiter)."""
    import asyncio
    global _auswerten_sperre
    if _auswerten_sperre is None:
        _auswerten_sperre = asyncio.Semaphore(_AUSWERTEN_GLEICHZEITIG)
    try:
        await asyncio.wait_for(_auswerten_sperre.acquire(), _AUSWERTEN_WARTEN_S)
    except asyncio.TimeoutError:
        raise HTTPException(503, "Gerade werden viele Seiten ausgewertet – bitte gleich noch einmal.",
                            headers={"Retry-After": "5"})
    try:
        return await asyncio.to_thread(fn, *args)
    finally:
        _auswerten_sperre.release()


#: Wunsch Ahmad 04.10.2026: Programm und Helfer arbeiten zusammen — was das Programm in dieser Zeit verglichen
#: hat, oeffnet der Helfer nicht noch einmal von selbst (nur auf Knopfdruck); die Vergleichsseiten, die das
#: Programm geoeffnet hat, wertet der Helfer aus (Ampel).
ZUSAMMEN_MINUTEN = 30


def _seit(minuten: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minuten)).isoformat()


async def _programm_vergleich(user: dict, inserat_url: str) -> Optional[dict]:
    """Letzter Vergleich DIESES Kontos im Windows-Programm zu diesem Inserat (ZUSAMMEN_MINUTEN), sonst None."""
    if not inserat_url:
        return None
    return await db[wz.SAMMLUNG_VERGLEICHE].find_one(
        {"werkzeug": wz.AUTOPOINTER, "user_id": user["id"], "probelauf": {"$ne": True},
         "fahrzeug.inserat_url": inserat_url, "erstellt_am": {"$gte": _seit(ZUSAMMEN_MINUTEN)}},
        {"_id": 0, "id": 1, "erstellt_am": 1}, sort=[("erstellt_am", -1)])


def _browser_werkzeug(werkzeug_id: str) -> None:
    _wid_pruefen(werkzeug_id)
    if wz.art(werkzeug_id) != "browser":
        raise HTTPException(404, NICHT_GEFUNDEN)


async def _firmenregeln(user: dict):
    from mobile_service import DEFAULT_EXPORT_RULES, DEFAULT_RULES
    from regeln import regeln_lesen
    dealer = await effective_dealer(user)
    profil = (dealer or {}).get("active_profile", "inland")
    regeln = (regeln_lesen((dealer or {}).get("export_rules"), DEFAULT_EXPORT_RULES) if profil == "export"
              else regeln_lesen((dealer or {}).get("comparison_rules"), DEFAULT_RULES))
    return profil, regeln


class InseratIn(BaseModel):
    url: str = Field(..., max_length=2048)
    #: base64(gzip(document.documentElement.outerHTML))
    seite: str = Field(..., min_length=20, max_length=_SEITE_MAX)
    #: 08.10.2026: das Inserat hat die Erweiterung zu diesem Vorgang des Programms geoeffnet (statt 30-Minuten-Suche)
    vorgang_id: Optional[str] = Field(None, max_length=64)


@router.post("/werkzeuge/{werkzeug_id}/inserat")
async def werkzeug_inserat(werkzeug_id: str, body: InseratIn,
                           schluessel: Optional[str] = Header(None, alias=wz.TOKEN_KOPF),
                           version: Optional[str] = Header(None, alias="X-Werkzeug-Version")):
    import browser_helfer as bh
    from anbieter_fehler import ListingGone
    from listing_identity import ListingIdentityError, get_listing_identity
    _browser_werkzeug(werkzeug_id)
    user, v, _firma_doc, _abo = await _programm(werkzeug_id, schluessel, _programm_version(None, version))
    if not await _inserat_limiter.check(f"konto:{user['id']}"):
        raise HTTPException(429, "Zu viele Inserate in kurzer Zeit – bitte kurz warten.")
    try:
        identity = get_listing_identity(body.url.strip())
    except ListingIdentityError as exc:
        raise HTTPException(400, str(exc) or "Kein Inserat von mobile.de, AutoScout24 oder Kleinanzeigen.")
    try:
        # Entpacken + Auslesen in EINEM Thread-Aufruf (Paket 3): das entpackte HTML (bis 4 MB) liegt nie
        # zwischen zwei Aufrufen ausserhalb der Sperre herum
        fahrzeug, bewertung = await _auswerten(
            lambda: bh.inserat_auslesen(identity, body.url.strip(), bh.seite_entpacken(body.seite)))
    except bh.SeiteUngueltig as exc:
        raise HTTPException(422, str(exc))
    except ListingGone as exc:
        raise HTTPException(404, str(exc))
    except Exception:  # noqa: BLE001 — eine unbekannte Seite ist ein 422, kein 500
        log.exception("Browser-Helfer: Inserat %s nicht auswertbar", identity["cache_key"])
        raise HTTPException(422, "Die Seite konnte nicht ausgewertet werden.")
    inserat_url = wz.inserat_url(identity["source"], identity["item_id"], identity["item_id"]) or body.url.strip()
    profil, regeln = await _firmenregeln(user)
    # Wunsch Ahmad 04.10.2026 (abends): immer an die AutoSchnell-Einstellungen halten — Beschaedigte und Navi wie
    # eingestellt (vorher hier immer "ohne Beschaedigte"). Was trotzdem durchrutscht (Unfall, Export, Neuwagen,
    # Lockangebote …), sortiert /marktlage fuer die Ampel weiter aus.
    try:
        links, hinweise = wz.vergleichs_links(fahrzeug, regeln)
        links, hinweise = wz.nach_portalen(links, hinweise, wz.portale_von(user))      # 08.10.2026
        kurz = bh.fahrzeug_kurz(fahrzeug, identity, inserat_url)
        # wie im Programm: unplausible EZ/km sagen (Filter bleiben wie eingestellt)
        melden = wz.plausibel(kurz)
        verhandlung = bh.verhandlung_hinweise(fahrzeug)
    except Exception:  # noqa: BLE001 — Werte aus der Seite, mit denen sich nicht rechnen laesst: 422, kein 500
        log.exception("Browser-Helfer: Inserat %s nicht verwertbar", identity["cache_key"])
        raise HTTPException(422, "Die Seite konnte nicht ausgewertet werden.")
    hinweise = melden + hinweise
    # Pruefung 05.10.2026 (Paket 1): ERST nach allen Rechenschritten merken — die Lesung gilt 24 h fuer alle Konten;
    # vorher wurde gespeichert und danach scheiterte die Anfrage (500), der kaputte Wert blieb liegen.
    await bh.inserat_merken(db, identity, inserat_url, fahrzeug, user)
    # 08.10.2026: mit Vorgangsnummer genau dieser Vergleich des Programms; ohne (aeltere Erweiterung) wie bisher
    programm = (await _vorgang_doc(body.vorgang_id, user["id"], {"_id": 0, "id": 1, "erstellt_am": 1})
                if body.vorgang_id else None) or await _programm_vergleich(user, inserat_url)
    vergleich_id = str(uuid.uuid4())
    await db[wz.SAMMLUNG_VERGLEICHE].insert_one({
        "id": vergleich_id, "werkzeug": werkzeug_id, "dealer_id": user["dealer_id"], "user_id": user["id"],
        "verbindung_id": v["id"], "pc_name": v.get("pc_name") or "", "erstellt_am": now_iso(),
        "fahrzeug": kurz, "links": links, "hinweise": hinweise, "profil": profil, "probelauf": False,
        "vorab": "fertig", "portal_bewertung": bewertung, "verhandlung": verhandlung, "marktlage": {},
        "ablauf": wz.vergleich_ablauf(), **({"vorgang_id": programm["id"]} if programm else {}),
    })
    return {"vergleich_id": vergleich_id, "links": links, "hinweise": hinweise, "profil": profil,
            "fahrzeug": kurz, "inserat_url": inserat_url,
            "app_pfad": "/app/vergleich?url=" + quote(inserat_url, safe=""),
            "portal_bewertung": bewertung, "verhandlung": verhandlung, "melden": melden,
            # das Programm hat dieses Auto gerade verglichen -> der Helfer oeffnet nichts von selbst
            "programm_verglichen": programm["erstellt_am"] if programm else None,
            "verkaeufer": {"name": fahrzeug.get("seller_name") or "", "art": fahrzeug.get("seller_type") or ""}}


class MarktlageIn(BaseModel):
    vergleich_id: str = Field(..., min_length=1, max_length=64)
    url: str = Field(..., max_length=4096)
    seite: str = Field(..., min_length=20, max_length=_SEITE_MAX)


@router.post("/werkzeuge/{werkzeug_id}/marktlage")
async def werkzeug_marktlage(werkzeug_id: str, body: MarktlageIn,
                             schluessel: Optional[str] = Header(None, alias=wz.TOKEN_KOPF),
                             version: Optional[str] = Header(None, alias="X-Werkzeug-Version")):
    """Die Vergleichsseite, die die Erweiterung zu einem Inserat geoeffnet hat: wo liegt der Preis?"""
    import browser_helfer as bh
    _browser_werkzeug(werkzeug_id)
    user, v, _firma_doc, _abo = await _programm(werkzeug_id, schluessel, _programm_version(None, version))
    if not await _marktlage_limiter.check(f"konto:{user['id']}"):
        raise HTTPException(429, "Zu viele Vergleichsseiten in kurzer Zeit – bitte kurz warten.")
    art = bh.ist_vergleichsseite(body.url.strip())
    if not art:
        raise HTTPException(400, "Das ist keine Vergleichsseite von mobile.de oder AutoScout24.")
    # eigener Vergleich oder einer des Windows-Programms DESSELBEN Kontos (programm-suche, 04.10.2026)
    doc = await db[wz.SAMMLUNG_VERGLEICHE].find_one(
        {"id": body.vergleich_id, "werkzeug": {"$in": [werkzeug_id, wz.AUTOPOINTER]}, "user_id": user["id"]},
        {"_id": 0, "id": 1, "fahrzeug": 1, "links": 1})
    if doc is None:
        raise HTTPException(404, "Vergleich nicht gefunden.")
    portal = "mobile.de" if art == "mobile" else "AutoScout24"
    eigene = [l.get("url") or "" for l in doc.get("links") or [] if l.get("portal") == portal]
    if not eigene:
        raise HTTPException(400, "Zu diesem Inserat wurde keine solche Vergleichsseite geöffnet.")
    # Pruefung 05.10.2026 (Paket 1): nur die Suche DIESES Vergleichs. Wer schnell zum naechsten Auto klickt, dessen
    # Vergleichs-Tab laedt noch die alte Suche — sie bekam die Ampel des neuen Autos. 409: die Erweiterung wartet
    # dann auf die richtige Seite. (Lieber keine Ampel als die eines anderen Autos, wie bei /programm-suche.)
    if not any(bh.gleiche_suche(u, body.url.strip()) for u in eigene):
        log.info("Browser-Helfer: Vergleichsseite passt nicht zum Vergleich %s (%s) — Seite: %s | Link: %s",
                 doc["id"], portal, body.url.strip()[:400], eigene[0][:400])
        raise HTTPException(409, "Diese Vergleichsseite gehört zu einer anderen Suche.")
    try:
        liste = await _auswerten(lambda: bh.treffer_auslesen(body.url.strip(), bh.seite_entpacken(body.seite)))
        fz = doc.get("fahrzeug") or {}
        # Wunsch Ahmad 04.10.2026: aussortieren (Unfall, Export, Neuwagen …), auf km/EZ des eigenen Autos umrechnen
        lage = bh.marktlage(fz.get("preis"), fz.get("inserat_id") or "", liste, eigen=fz)
    except bh.SeiteUngueltig as exc:
        raise HTTPException(422, str(exc))
    except Exception:  # noqa: BLE001
        log.exception("Browser-Helfer: Vergleichsseite nicht auswertbar")
        raise HTTPException(422, "Die Vergleichsseite konnte nicht ausgewertet werden.")
    lage["am"] = now_iso()
    await db[wz.SAMMLUNG_VERGLEICHE].update_one(
        {"id": doc["id"]}, {"$set": {f"marktlage.{'mobile' if art == 'mobile' else 'autoscout'}": lage}})
    return lage


class ProgrammSucheIn(BaseModel):
    url: str = Field(..., max_length=4096)


@router.post("/werkzeuge/{werkzeug_id}/programm-suche")
async def werkzeug_programm_suche(werkzeug_id: str, body: ProgrammSucheIn,
                                  schluessel: Optional[str] = Header(None, alias=wz.TOKEN_KOPF),
                                  version: Optional[str] = Header(None, alias="X-Werkzeug-Version")):
    """Wunsch Ahmad 04.10.2026: eine Vergleichsseite, die der Helfer NICHT selbst geoeffnet hat — stammt sie aus
    einem Vergleich des Windows-Programms DIESES Kontos (letzte 30 min, dieselbe Suche)? Dann bekommt die
    Erweiterung die Vergleichs-ID fuer /marktlage und das Auto fuer die Box. Nur die Adresse kommt her, die
    Seite erst bei einem Treffer."""
    import browser_helfer as bh
    _browser_werkzeug(werkzeug_id)
    user, v, _firma_doc, _abo = await _programm(werkzeug_id, schluessel, _programm_version(None, version))
    if not await _marktlage_limiter.check(f"konto:{user['id']}"):
        raise HTTPException(429, "Zu viele Vergleichsseiten in kurzer Zeit – bitte kurz warten.")
    url = body.url.strip()
    art = bh.ist_vergleichsseite(url)
    if not art:
        raise HTTPException(400, "Das ist keine Vergleichsseite von mobile.de oder AutoScout24.")
    portal = "mobile.de" if art == "mobile" else "AutoScout24"
    async for doc in db[wz.SAMMLUNG_VERGLEICHE].find(
            {"werkzeug": wz.AUTOPOINTER, "user_id": user["id"], "probelauf": {"$ne": True},
             "erstellt_am": {"$gte": _seit(ZUSAMMEN_MINUTEN)}},
            {"_id": 0, "id": 1, "fahrzeug": 1, "links": 1, "erstellt_am": 1}).sort("erstellt_am", -1).limit(50):
        if any(l.get("portal") == portal and bh.gleiche_suche(l.get("url") or "", url) for l in doc.get("links") or []):
            f = doc.get("fahrzeug") or {}
            return {"vergleich_id": doc["id"], "portal": portal,
                    "fahrzeug": {k: f.get(k) for k in ("marke", "modell", "titel", "ez_monat", "ez_jahr", "kilometer",
                                                       "ps", "preis", "inserat_url")}}
    raise HTTPException(404, "Kein passender Vergleich des Programms.")


# ---------------------------------------------------------------- App-Start (Nr. 12)
# ---------------------------------------------------------------- Vorgangsnummer (08.10.2026)
# Wunsch Ahmad 08.10.2026 (externe Pruefung "drei Wege fuer denselben Ablauf"): jeder Programm-Vergleich IST ein
# Vorgang (werkzeug_vergleiche.id). Hat das Konto die Erweiterung, oeffnet das Programm nur /app/vorgang/<id>; die
# Erweiterung uebernimmt ihn (POST …/vorgang/<id>/uebernehmen) und oeffnet Vergleiche + Inserat selbst. Das Programm
# fragt kurz nach (GET …/vorgang/<id>) und oeffnet nur, wenn niemand uebernommen hat, selbst (Erweiterung nicht in
# diesem Browser). Ohne Erweiterung bleibt alles wie bisher.
VORGANG_MINUTEN = 10
_VORGANG_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_VORGANG_FAHRZEUG = ("marke", "modell", "titel", "ez_monat", "ez_jahr", "kilometer", "ps", "preis", "quelle",
                     "inserat_id", "inserat_url")


async def _vorgang_doc(vorgang_id: Optional[str], user_id: str, felder: Optional[dict] = None,
                       minuten: Optional[int] = VORGANG_MINUTEN) -> Optional[dict]:
    """Ein Vergleich des Windows-Programms DIESES Kontos (kein Probelauf), hoechstens ``minuten`` alt."""
    if not vorgang_id or not _VORGANG_ID.match(str(vorgang_id)):
        return None
    filter_ = {"id": vorgang_id, "werkzeug": wz.AUTOPOINTER, "user_id": user_id, "probelauf": {"$ne": True}}
    if minuten:
        filter_["erstellt_am"] = {"$gte": _seit(minuten)}
    return await db[wz.SAMMLUNG_VERGLEICHE].find_one(filter_, felder or {"_id": 0})


def _vorgang_antwort(doc: dict) -> dict:
    from listing_identity import ListingIdentityError, get_listing_identity
    f = doc.get("fahrzeug") or {}
    url = f.get("inserat_url") or None
    kennung = None
    if url:
        try:
            kennung = get_listing_identity(url)["cache_key"]      # = inseratKennung der Erweiterung (gemeinsam.js)
        except ListingIdentityError:
            url = None
    return {"vorgang_id": doc["id"], "links": doc.get("links") or [], "inserat_url": url, "kennung": kennung,
            "fahrzeug": {k: f.get(k) for k in _VORGANG_FAHRZEUG}}


@router.post("/werkzeuge/{werkzeug_id}/vorgang/{vorgang_id}/uebernehmen")
async def werkzeug_vorgang_uebernehmen(werkzeug_id: str, vorgang_id: str,
                                       schluessel: Optional[str] = Header(None, alias=wz.TOKEN_KOPF),
                                       version: Optional[str] = Header(None, alias="X-Werkzeug-Version")):
    """Die Erweiterung uebernimmt einen Vorgang des Programms (genau einmal — ein Neuladen der Seite oeffnet nichts
    doppelt: schon_uebernommen)."""
    from pymongo import ReturnDocument
    _browser_werkzeug(werkzeug_id)
    user, _v, _firma_doc, _abo = await _programm(werkzeug_id, schluessel, _programm_version(None, version))
    if not _VORGANG_ID.match(vorgang_id):
        raise HTTPException(404, "Vorgang nicht gefunden.")
    doc = await db[wz.SAMMLUNG_VERGLEICHE].find_one_and_update(
        {"id": vorgang_id, "werkzeug": wz.AUTOPOINTER, "user_id": user["id"], "probelauf": {"$ne": True},
         "erstellt_am": {"$gte": _seit(VORGANG_MINUTEN)}, "helfer_am": {"$exists": False}},
        {"$set": {"helfer_am": now_iso()}}, projection={"_id": 0}, return_document=ReturnDocument.AFTER)
    if doc is not None:
        return {**_vorgang_antwort(doc), "schon_uebernommen": False}
    vorhanden = await _vorgang_doc(vorgang_id, user["id"])
    if vorhanden is None:
        raise HTTPException(404, "Vorgang nicht gefunden oder abgelaufen.")
    return {**_vorgang_antwort(vorhanden), "schon_uebernommen": True}


@router.get("/werkzeuge/{werkzeug_id}/vorgang/{vorgang_id}")
async def werkzeug_vorgang_stand(werkzeug_id: str, vorgang_id: str,
                                 schluessel: Optional[str] = Header(None, alias=wz.TOKEN_KOPF),
                                 user_agent: Optional[str] = Header(None, alias="User-Agent")):
    """Das Programm fragt nach: hat die Erweiterung den Vorgang uebernommen? Sonst oeffnet es selbst."""
    if _ist_browser(werkzeug_id):
        raise HTTPException(404, NICHT_GEFUNDEN)
    user, _v, _firma_doc, _abo = await _programm(werkzeug_id, schluessel, _programm_version(user_agent))
    doc = await _vorgang_doc(vorgang_id, user["id"], {"_id": 0, "id": 1, "helfer_am": 1})
    if doc is None:
        raise HTTPException(404, "Vorgang nicht gefunden oder abgelaufen.")
    return {"uebernommen": bool(doc.get("helfer_am"))}


@router.get("/werkzeuge/vorgang/{vorgang_id}")
async def app_vorgang(vorgang_id: str, user=Depends(current_user)):
    """Die Seite /app/vorgang/<id> in AutoSchnell — nur sichtbar, wenn die Erweiterung in DIESEM Browser fehlt
    (sonst uebernimmt sie, bevor die Seite steht): zeigt das Auto und die Links zum selbst Oeffnen."""
    doc = await _vorgang_doc(vorgang_id, user["id"], minuten=None)
    if doc is None:
        raise HTTPException(404, "Vorgang nicht gefunden.")
    return {**_vorgang_antwort(doc), "uebernommen": bool(doc.get("helfer_am"))}


@router.post("/werkzeuge/app-start/{start}")
async def werkzeug_app_start_melden(start: str, user=Depends(current_firma)):
    """Pruefbericht 03.10.2026 (Nr. 12): die AutoSchnell-App hat ein Auto aus dem Programm uebernommen (Kennung im
    Link "start"). Das Programm fragt danach — ohne Meldung oeffnet es den Kaufvertrag im Browser. 10 Minuten."""
    if not _START_KENNUNG.match(start or ""):
        raise HTTPException(400, "Ungültige Kennung")
    jetzt = datetime.now(timezone.utc)
    from pymongo.errors import DuplicateKeyError
    try:
        await db[wz.SAMMLUNG_APP_STARTS].update_one(
            {"start": start},
            {"$setOnInsert": {"start": start, "user_id": user["id"], "dealer_id": user.get("dealer_id"),
                              "am": jetzt.isoformat(), "ablauf": jetzt + timedelta(minutes=10)}},
            upsert=True)
    except DuplicateKeyError:
        pass                                   # zwei Meldungen gleichzeitig (Fenster + Ereignis): eine reicht
    return {"ok": True}


@router.get("/werkzeuge/{werkzeug_id}/app-start/{start}")
async def werkzeug_app_start_pruefen(werkzeug_id: str, start: str,
                                     schluessel: Optional[str] = Header(None, alias=wz.TOKEN_KOPF)):
    """Nur fuer Starts derselben Firma — ein fremdes Programm erfaehrt nichts ueber andere Konten."""
    user, _v, _firma_doc, _abo = await _programm(werkzeug_id, schluessel)
    if not _START_KENNUNG.match(start or ""):
        return {"bestaetigt": False}
    d = await db[wz.SAMMLUNG_APP_STARTS].find_one({"start": start}, {"_id": 0, "dealer_id": 1})
    return {"bestaetigt": bool(d) and bool(user.get("dealer_id")) and d.get("dealer_id") == user.get("dealer_id")}


@router.get("/werkzeuge/{werkzeug_id}/meine")
async def werkzeug_meine(werkzeug_id: str, limit: int = Query(30, ge=1, le=100), user=Depends(current_firma)):
    """Die eigenen zuletzt im Programm angeklickten Autos — mit Inserat-Link fuer den Kaufvertrag."""
    _wid_pruefen(werkzeug_id)
    if not wz.ist_freigegeben(werkzeug_id, await _kunden_nr(user)):
        raise HTTPException(404, NICHT_GEFUNDEN)
    liste = [x async for x in db[wz.SAMMLUNG_VERGLEICHE].find(
        {"werkzeug": werkzeug_id, "user_id": user["id"], "probelauf": {"$ne": True}},
        {"_id": 0, "id": 1, "erstellt_am": 1, "fahrzeug": 1, "links": 1, "vorab": 1})
        .sort("erstellt_am", -1).limit(limit)]
    return {"vergleiche": liste}


# ---------------------------------------------------------------- Chef
async def _uebersicht(filter_: dict, limit: int, ueberspringen: int = 0) -> dict:
    verbindungen = [v async for v in db[wz.SAMMLUNG_VERBINDUNGEN].find(
        filter_, {"_id": 0, "token_hash": 0, "pc_kennung": 0}).sort("zuletzt_am", -1).limit(500)]
    gesamt = await db[wz.SAMMLUNG_VERGLEICHE].count_documents(filter_)
    # Paket 3: ohne die aussortierten Angebote je Portal (bis 25 je Vergleich) — die Uebersicht zeigt sie nicht
    vergleiche = [x async for x in db[wz.SAMMLUNG_VERGLEICHE].find(
        filter_, {"_id": 0, "marktlage.mobile.aussortiert": 0, "marktlage.autoscout.aussortiert": 0, "ablauf": 0})
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
    filt = {"werkzeug": werkzeug_id, "user_id": konto_id, "dealer_id": user["dealer_id"]}
    alte = [x.get("token_hash") async for x in db[wz.SAMMLUNG_VERBINDUNGEN].find(filt, {"_id": 0, "token_hash": 1})]
    r = await db[wz.SAMMLUNG_VERBINDUNGEN].delete_many(filt)
    if not r.deleted_count:
        raise HTTPException(404, "Keine Verbindung für dieses Konto.")
    await _getrennt_merken(werkzeug_id, alte, "chef")
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


@router.get("/admin/werkzeug-downloads")
async def admin_werkzeug_downloads(limit: int = Query(300, ge=1, le=1000), _=Depends(current_super_admin)):
    """Wunsch Ahmad 06.10.2026: wer hat wann welches Programm heruntergeladen — aus activity_logs
    (werkzeug_download, geschrieben von /werkzeuge/{id}/download), neueste zuerst, mit Konto und Firma."""
    filt = {"action": "werkzeug_download"}
    roh = [x async for x in db.activity_logs.find(
        filt, {"_id": 0, "id": 1, "dealer_id": 1, "user_id": 1, "ref": 1, "meta": 1, "created_at": 1})
        .sort("created_at", -1).limit(limit)]
    namen = await _konten([x.get("user_id") for x in roh])
    firmen = {}
    firmen_ids = list({x.get("dealer_id") for x in roh if x.get("dealer_id")})
    if firmen_ids:
        async for d in db.dealers.find({"id": {"$in": firmen_ids}}, {"_id": 0, "id": 1, "company_name": 1, "kunden_nr": 1}):
            firmen[d["id"]] = {"firma": d.get("company_name") or "", "kunden_nr": d.get("kunden_nr")}
    leer = {"konto": "", "name": "gelöschtes Konto", "rolle": ""}
    downloads = []
    for x in roh:
        werkzeug = str(x.get("ref") or "")
        eintrag = {"id": x.get("id"), "am": x.get("created_at"), "werkzeug": werkzeug,
                   "programm": (wz.WERKZEUGE.get(werkzeug) or {}).get("name", werkzeug),
                   "version": (x.get("meta") or {}).get("version"), "user_id": x.get("user_id"),
                   "dealer_id": x.get("dealer_id")}
        eintrag.update(namen.get(x.get("user_id"), leer))
        eintrag.update(firmen.get(x.get("dealer_id"), {"firma": "", "kunden_nr": None}))
        downloads.append(eintrag)
    return {"downloads": downloads, "gesamt": await db.activity_logs.count_documents(filt)}


@router.delete("/admin/werkzeug-verbindungen/{konto_id}")
async def admin_werkzeug_trennen(konto_id: str, werkzeug: str = wz.AUTOPOINTER,
                                 admin=Depends(current_super_admin)):
    filt = {"werkzeug": werkzeug, "user_id": konto_id}
    alte = [x.get("token_hash") async for x in db[wz.SAMMLUNG_VERBINDUNGEN].find(filt, {"_id": 0, "token_hash": 1})]
    r = await db[wz.SAMMLUNG_VERBINDUNGEN].delete_many(filt)
    await _getrennt_merken(werkzeug, alte, "betreiber")
    await log_activity_sicher(admin.get("dealer_id", ""), admin["id"], "admin.werkzeug.getrennt", ref=konto_id)
    return {"ok": True, "getrennt": r.deleted_count}
