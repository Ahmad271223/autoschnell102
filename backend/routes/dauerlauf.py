# -*- coding: utf-8 -*-
"""Dauerlauf (Wunsch Ahmad 27.11.2026): Trefferliste — Annahme vom Windows-Programm, Anzeige in der App.

* ``POST /werkzeuge/{id}/dauerlauf/treffer`` — das Programm schickt ein ganz gelesenes Auto (Tabelle + ganze
  Beschreibung). Hier laeuft KEIN Vergleich und KEIN Abruf bei einem Portal: nur die Erkennung von Marke/Modell, die
  Schadenspruefung und die Preislage aus den schon gespeicherten Marktdaten. Darum zaehlt es auch zu keinem Tageslimit
  (gemessen: bis 1,3 neue Autos je Sekunde in AutoPointer).
* ``GET /dauerlauf/treffer`` — die Liste fuer die App (eigene Treffer; der Chef sieht die der Firma).
* ``POST /dauerlauf/treffer/{id}/erledigt`` — aus der Liste nehmen.

Ein Treffer bleibt ``dauerlauf_treffer.TREFFER_STUNDEN`` (48) liegen, dann loescht die Datenbank ihn samt Beschreibung.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Annotated, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field

import dauerlauf_treffer as dt
import erkennung_lernen
import schaden_pruefung
import werkzeuge as wz
from deps import current_firma, db, now_iso
from rate_limiter import SlidingWindowRateLimiter
from routes.werkzeuge import (NICHT_GEFUNDEN, FahrzeugIn, _erkennen_im_pool, _ist_browser, _kunden_nr, _programm,
                              _programm_version)

log = logging.getLogger("autohandel")
router = APIRouter()

#: je Konto: AutoPointer bringt bis ~80 neue Autos je Minute, nach der Vorauswahl deutlich weniger
_treffer_limiter = SlidingWindowRateLimiter(max_attempts=180, window_seconds=60, name="dauerlauf_treffer")


class ListeIn(BaseModel):
    """Was in der Listenzeile von AutoPointer stand (das Programm hat die Detailansicht dagegen geprueft)."""
    zeit: str = Field("", max_length=30)
    privat: Optional[bool] = None
    portal: str = Field("", max_length=40)
    #: die drei Textzeilen der Zeile, wie sie dastehen ("PKW, Gebraucht, …" / "EZ: …, … Km, …" / Ausstattung)
    art: str = Field("", max_length=200)
    daten: str = Field("", max_length=200)
    ausstattung: str = Field("", max_length=200)
    #: Spalte "Anbieter": Name, Telefonnummern, Entfernung, Ort — fuer den Telefon-Knopf in der Trefferliste
    anbieter: str = Field("", max_length=80)
    telefone: List[Annotated[str, Field(max_length=40)]] = Field(default_factory=list, max_length=3)
    entfernung_km: Optional[int] = Field(None, ge=0, le=5000)
    ort: str = Field("", max_length=80)
    email: str = Field("", max_length=120)


class TrefferIn(BaseModel):
    fahrzeug: FahrzeugIn
    liste: Optional[ListeIn] = None
    #: alle Zeilen der Tabelle "Technische Daten" als [Bezeichnung, Wert] und die Eintraege unter "Ausstattung" —
    #: vom Programm exakt gelesen, fuer den vorausgefuellten Kaufvertrag und die Detailansicht
    merkmale: List[Annotated[List[Annotated[str, Field(max_length=160)]], Field(min_length=2, max_length=2)]] = Field(
        default_factory=list, max_length=60)
    ausstattung: List[Annotated[str, Field(max_length=80)]] = Field(default_factory=list, max_length=120)


async def _preislage(f: dict) -> Optional[dict]:
    """Preislage nur aus gespeicherten Marktdaten — ein Fehler dort haelt den Treffer nie auf."""
    try:
        from markt import abfrage
        return dt.preislage(f.get("preis"), await abfrage.karte(db, wz.fahrzeug_zu_vehicle(f)))
    except Exception:  # noqa: BLE001
        log.exception("Dauerlauf: Preislage nicht bestimmbar")
        return None


@router.post("/werkzeuge/{werkzeug_id}/dauerlauf/treffer")
async def dauerlauf_treffer_melden(werkzeug_id: str, body: TrefferIn,
                                   schluessel: Optional[str] = Header(None, alias=wz.TOKEN_KOPF),
                                   user_agent: Optional[str] = Header(None, alias="User-Agent")):
    if _ist_browser(werkzeug_id):
        raise HTTPException(404, NICHT_GEFUNDEN)
    user, v, _firma_doc, _abo = await _programm(werkzeug_id, schluessel, _programm_version(user_agent))
    if not await _treffer_limiter.check(f"konto:{user['id']}"):
        raise HTTPException(429, "Zu viele Treffer in kurzer Zeit – bitte kurz warten.")
    f = body.fahrzeug.model_dump()
    alternativen = f.pop("alternativen", None) or {}
    voll, exakt = f.pop("beschreibung_voll", "") or "", bool(f.pop("beschreibung_exakt", False))
    text = voll or f.get("beschreibung") or ""
    schaden = schaden_pruefung.pruefen(text, f.get("zustand"), f.get("kategorie"), f.get("titel"))
    schaden["text_exakt"] = bool(voll) and exakt
    erkannt = await _erkennen_im_pool(f, await erkennung_lernen.tabelle(db, user.get("dealer_id")), alternativen)
    if erkannt.get("erkannt"):
        f["marke"], f["modell"] = erkannt.get("marke") or f.get("marke"), erkannt.get("modell") or f.get("modell")
    f["inserat_id"] = wz.inserat_nummer(f.get("inserat_id"))
    f["inserat_url"] = wz.inserat_url(f.get("quelle"), f.get("inserat_id"), f.get("hash_id"))
    jetzt = datetime.now(timezone.utc)
    doc = dt.dokument(user, v, f, body.liste.model_dump() if body.liste else None, schaden, await _preislage(f),
                      text, now_iso(), jetzt, body.merkmale, body.ausstattung)
    # dasselbe Inserat noch einmal gelesen: der Eintrag wird ersetzt, „erledigt“ und die erste Zeit bleiben
    fest = {k: doc.pop(k) for k in ("id", "erstellt_am", "erledigt")}
    alt = await db[dt.SAMMLUNG].find_one_and_update(
        {"user_id": user["id"], "kennung": doc["kennung"]}, {"$set": doc, "$setOnInsert": fest},
        upsert=True, projection={"_id": 0, "id": 1})
    return {"id": (alt or fest)["id"], "neu": alt is None, "urteil": doc["urteil"], "schaden": schaden,
            "preislage": doc["preislage"], "inserat_url": f["inserat_url"]}


async def _freigegeben(user: dict) -> None:
    if not wz.ist_freigegeben(wz.AUTOPOINTER, await _kunden_nr(user)):
        raise HTTPException(404, NICHT_GEFUNDEN)


def _eigene(user: dict) -> dict:
    """Der Chef sieht die Treffer der ganzen Firma, ein Sucher seine eigenen."""
    return {"dealer_id": user.get("dealer_id")} if user.get("role") == "dealer" else {"user_id": user["id"]}


@router.get("/dauerlauf/treffer")
async def dauerlauf_treffer_liste(alle: bool = False, limit: int = Query(100, ge=1, le=300),
                                  user=Depends(current_firma)):
    """Neueste zuerst. Ohne ``alle``: nur was sich lohnt (gut / leichter Schaden / pruefen) und nicht erledigt ist."""
    await _freigegeben(user)
    filt = _eigene(user)
    gesamt = await db[dt.SAMMLUNG].count_documents(filt)
    if not alle:
        filt = {**filt, "urteil": {"$in": ["gut", "leicht", "pruefen"]}, "erledigt": {"$ne": True}}
    docs = [d async for d in db[dt.SAMMLUNG].find(filt, {"_id": 0}).sort("erstellt_am", -1).limit(limit)]
    return {"treffer": [dt.antwort(d) for d in docs], "gesamt": gesamt, "stunden": dt.TREFFER_STUNDEN,
            "stand": now_iso()}


@router.post("/dauerlauf/treffer/{treffer_id}/vertrag")
async def dauerlauf_treffer_vertrag(treffer_id: str, user=Depends(current_firma)):
    """Wunsch Ahmad 27.11.2026: „Kaufvertrag“ in der Trefferliste oeffnet den Vertrag SOFORT, vorausgefuellt mit allem,
    was das Programm gelesen hat (Daten, Merkmale, Ausstattung, Beschreibung, Verkaeufer) — ohne den Link noch einmal
    einzugeben und ohne Abruf beim Portal. Legt dafuer das Fahrzeug im Pool des Kontos an (wie ein Vergleich es tut)
    und sagt, welche Angaben das Inserat nicht hergab (``fehlt``: der Sucher traegt sie im Vertrag nach)."""
    await _freigegeben(user)
    doc = await db[dt.SAMMLUNG].find_one({**_eigene(user), "id": treffer_id}, {"_id": 0})
    if doc is None:
        raise HTTPException(404, NICHT_GEFUNDEN)
    from routes.listings import _fahrzeug_id, _fahrzeug_uebernehmen
    f = doc.get("fahrzeug") or {}
    vehicle = dt.fahrzeug_fuer_vertrag(doc, wz.fahrzeug_zu_vehicle(f))
    quelle, ad_id, schluessel = "dauerlauf", str(f.get("inserat_id") or doc["id"]), None
    if f.get("inserat_url"):
        from listing_identity import ListingIdentityError, get_listing_identity
        try:
            ident = get_listing_identity(f["inserat_url"])
            quelle, ad_id, schluessel = ident["source"], str(ident["item_id"]), ident.get("cache_key")
        except (ListingIdentityError, KeyError):
            pass
    vid = await _fahrzeug_id(quelle, ad_id, user["dealer_id"]) if quelle != "dauerlauf" else f"v_dauerlauf_{doc['id'].replace('-', '')[:16]}"
    zustand: dict = {}
    kollege = await _fahrzeug_uebernehmen(user, vid, ad_id, vehicle, quelle, schluessel, zustand)
    if zustand.get("lifecycle") in ("geloescht", "verkauft", "archiviert"):
        raise HTTPException(409, "Dieses Fahrzeug ist im Bestand bereits abgeschlossen – bitte dort nachsehen.")
    # was wirklich am Fahrzeug steht (ein Kollege oder ein frueherer Vergleich kann schon Daten hinterlegt haben)
    gespeichert = await db.vehicles.find_one({"id": vid, "dealer_id": user["dealer_id"]}, {"_id": 0, "data": 1})
    await db[dt.SAMMLUNG].update_one({"id": doc["id"]}, {"$set": {"vehicle_id": vid}})
    return {"vehicle_id": vid, "vehicle": (gespeichert or {}).get("data") or vehicle, "fehlt": dt.fehlt(doc), "kollege": kollege}


@router.post("/dauerlauf/treffer/{treffer_id}/erledigt")
async def dauerlauf_treffer_erledigt(treffer_id: str, zurueck: bool = False, user=Depends(current_firma)):
    await _freigegeben(user)
    r = await db[dt.SAMMLUNG].update_one({**_eigene(user), "id": treffer_id}, {"$set": {"erledigt": not zurueck}})
    if not r.matched_count:
        raise HTTPException(404, NICHT_GEFUNDEN)
    return {"ok": True, "erledigt": not zurueck}
