# -*- coding: utf-8 -*-
"""„Was ist neu" in der App (Wunsch Ahmad 24.11.2026).

Die Eintraege stehen in backend/neuigkeiten.json (eine Datei im Repo — der
Server kennt so alle gueltigen IDs). Gelesen-Stand und „heute schon einen
Hinweis gezeigt" liegen je Konto in der Sammlung neuigkeiten_stand:

  {"konto": "u:<user-id>" | "f:<fahrer-id>", "gelesen": [ids], "hinweis_tag": "JJJJ-MM-TT"}

Chef und Sucher (current_firma):
  GET  /api/neuigkeiten                 Eintraege der Rolle + gelesen
  GET  /api/neuigkeiten/stand           Zahl der ungelesenen (Menue-Zaehler)
  POST /api/neuigkeiten/{id}/gelesen    404 bei unbekannter oder fremder ID
  POST /api/neuigkeiten/alle-gelesen
  POST /api/neuigkeiten/hinweis         hoechstens EIN Hinweis je Tag (Europe/Berlin), ueber alle
                                        Geraete: der Server vergibt den Tag atomar
Fahrer (current_driver): dieselben Wege unter /api/driver/neuigkeiten — nur Eintraege mit
"fuer": ["fahrer"].
"""
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pymongo.errors import DuplicateKeyError

import deps
from deps import current_firma
from routes.drivers import current_driver

log = logging.getLogger("autohandel")
router = APIRouter()

DATEI = Path(__file__).resolve().parents[1] / "neuigkeiten.json"
ZEITZONE = ZoneInfo("Europe/Berlin")
ROLLEN = ("chef", "sucher", "fahrer")
ARTEN = ("neu", "verbessert", "behoben")
SAMMLUNG = "neuigkeiten_stand"


# ------------------------------------------------------------ Eintraege
def eintraege_pruefen(roh: Any) -> List[Dict[str, Any]]:
    """Datei lesen und streng pruefen — ein Tippfehler faellt beim Start auf,
    nicht erst beim Nutzer. Reihenfolge: neueste zuerst (Datum absteigend,
    bei gleichem Datum wie in der Datei)."""
    liste = (roh or {}).get("eintraege") if isinstance(roh, dict) else roh
    if not isinstance(liste, list):
        raise ValueError("neuigkeiten.json: 'eintraege' fehlt")
    raus, ids = [], set()
    for i, e in enumerate(liste):
        if not isinstance(e, dict):
            raise ValueError(f"neuigkeiten.json: Eintrag {i} ist kein Objekt")
        eid = str(e.get("id") or "").strip()
        if not eid or eid in ids or len(eid) > 80:
            raise ValueError(f"neuigkeiten.json: ID fehlt/doppelt/zu lang bei Eintrag {i}")
        ids.add(eid)
        datetime.strptime(str(e.get("datum") or ""), "%Y-%m-%d")
        if e.get("art") not in ARTEN:
            raise ValueError(f"neuigkeiten.json: Art '{e.get('art')}' bei {eid}")
        fuer = e.get("fuer") or ["chef", "sucher"]
        if not isinstance(fuer, list) or not fuer or any(r not in ROLLEN for r in fuer):
            raise ValueError(f"neuigkeiten.json: 'fuer' ungueltig bei {eid}")
        link = e.get("link")
        if link is not None and not (isinstance(link, str) and link.startswith(("/app/", "/fahrer"))):
            raise ValueError(f"neuigkeiten.json: Link muss in die App fuehren ({eid})")
        punkte = e.get("punkte") or []
        if not isinstance(punkte, list) or not all(isinstance(p, str) for p in punkte):
            raise ValueError(f"neuigkeiten.json: 'punkte' ungueltig bei {eid}")
        if not str(e.get("titel") or "").strip() or not str(e.get("text") or "").strip():
            raise ValueError(f"neuigkeiten.json: Titel/Text fehlt bei {eid}")
        raus.append({"id": eid, "datum": e["datum"], "titel": str(e["titel"]).strip(),
                     "bereich": str(e.get("bereich") or "").strip(), "art": e["art"],
                     "text": str(e["text"]).strip(), "punkte": punkte, "link": link,
                     "wichtig": bool(e.get("wichtig")), "fuer": list(fuer), "_pos": i})
    raus.sort(key=lambda x: (x["datum"], -x["_pos"]), reverse=True)
    for x in raus:
        x.pop("_pos")
    return raus


EINTRAEGE: List[Dict[str, Any]] = eintraege_pruefen(json.loads(DATEI.read_text(encoding="utf-8")))


def rolle_von(user: dict) -> str:
    return "sucher" if user.get("role") == "sucher" else "chef"


def fuer_rolle(rolle: str, eintraege: Optional[List[dict]] = None) -> List[dict]:
    return [e for e in (EINTRAEGE if eintraege is None else eintraege) if rolle in e["fuer"]]


def hinweis_waehlen(sichtbar: List[dict], gelesen: set) -> Optional[dict]:
    """Neuester ungelesener „wichtig"-Eintrag, sonst der neueste ungelesene."""
    offen = [e for e in sichtbar if e["id"] not in gelesen]
    wichtig = [e for e in offen if e.get("wichtig")]
    return (wichtig or offen or [None])[0]


def heute_berlin(jetzt: Optional[datetime] = None) -> str:
    return (jetzt or datetime.now(timezone.utc)).astimezone(ZEITZONE).date().isoformat()


# ------------------------------------------------------------ Stand je Konto
_INDEX_OK: set = set()      # je Datenbank einmal (Tests nutzen Wegwerf-DBs)


async def _sammlung():
    coll = deps.db[SAMMLUNG]
    if deps.db.name not in _INDEX_OK:
        await coll.create_index("konto", unique=True, name="neuigkeiten_konto")
        _INDEX_OK.add(deps.db.name)
    return coll


async def _stand(konto: str) -> Dict[str, Any]:
    coll = await _sammlung()
    return await coll.find_one({"konto": konto}, {"_id": 0}) or {}


def _ausgabe(e: dict, gelesen: set) -> dict:
    return {k: v for k, v in e.items() if k != "fuer"} | {"gelesen": e["id"] in gelesen}


async def _liste(konto: str, rolle: str) -> Dict[str, Any]:
    gelesen = set((await _stand(konto)).get("gelesen") or [])
    sichtbar = fuer_rolle(rolle)
    return {"eintraege": [_ausgabe(e, gelesen) for e in sichtbar],
            "ungelesen": sum(1 for e in sichtbar if e["id"] not in gelesen)}


async def _zaehler(konto: str, rolle: str) -> Dict[str, Any]:
    gelesen = set((await _stand(konto)).get("gelesen") or [])
    offen = [e["id"] for e in fuer_rolle(rolle) if e["id"] not in gelesen]
    return {"ungelesen": len(offen), "ids": offen}


async def _gelesen(konto: str, rolle: str, eid: str) -> Dict[str, Any]:
    if eid not in {e["id"] for e in fuer_rolle(rolle)}:
        raise HTTPException(404, "Diesen Eintrag gibt es nicht")
    coll = await _sammlung()
    try:
        await coll.update_one({"konto": konto}, {"$addToSet": {"gelesen": eid}}, upsert=True)
    except DuplicateKeyError:      # gleichzeitiger erster Eintrag — noch einmal ohne Anlage
        await coll.update_one({"konto": konto}, {"$addToSet": {"gelesen": eid}})
    return await _zaehler(konto, rolle)


async def _alle_gelesen(konto: str, rolle: str) -> Dict[str, Any]:
    ids = [e["id"] for e in fuer_rolle(rolle)]
    coll = await _sammlung()
    if ids:
        try:
            await coll.update_one({"konto": konto}, {"$addToSet": {"gelesen": {"$each": ids}}}, upsert=True)
        except DuplicateKeyError:
            await coll.update_one({"konto": konto}, {"$addToSet": {"gelesen": {"$each": ids}}})
    return await _zaehler(konto, rolle)


async def _hinweis(konto: str, rolle: str, jetzt: Optional[datetime] = None) -> Dict[str, Any]:
    """Hoechstens EIN Hinweis je Konto und Tag (Europe/Berlin) — Handy und PC
    zusammen. Der Tag wird atomar vergeben: wer ihn nicht bekommt, bekommt
    keinen Hinweis. Ohne ungelesenen Eintrag wird der Tag nicht verbraucht.

    NEUIGKEITEN_HINWEIS=false schaltet das Fenster ab (25.11.2026: die E2E-Suite
    klickt sonst gegen das Fenster, das sich ueber die Seite legt). Die Seite
    „Was ist neu“ und der Zaehler bleiben."""
    if os.environ.get("NEUIGKEITEN_HINWEIS", "true").strip().lower() in ("false", "0", "nein", "aus"):
        return {"hinweis": None}
    stand = await _stand(konto)
    eintrag = hinweis_waehlen(fuer_rolle(rolle), set(stand.get("gelesen") or []))
    heute = heute_berlin(jetzt)
    if eintrag is None or stand.get("hinweis_tag") == heute:
        return {"hinweis": None}
    coll = await _sammlung()
    try:
        r = await coll.update_one({"konto": konto, "hinweis_tag": {"$ne": heute}},
                                  {"$set": {"hinweis_tag": heute}}, upsert=True)
    except DuplicateKeyError:      # ein anderes Geraet war schneller
        return {"hinweis": None}
    if not (r.modified_count or r.upserted_id is not None):
        return {"hinweis": None}
    return {"hinweis": _ausgabe(eintrag, set())}


def _konto_firma(user: dict) -> str:
    return "u:" + str(user["id"])


def _konto_fahrer(driver: dict) -> str:
    return "f:" + str(driver["id"])


# ------------------------------------------------------------ Chef und Sucher
@router.get("/neuigkeiten")
async def neuigkeiten_liste(user=Depends(current_firma)):
    return await _liste(_konto_firma(user), rolle_von(user))


@router.get("/neuigkeiten/stand")
async def neuigkeiten_stand(user=Depends(current_firma)):
    return await _zaehler(_konto_firma(user), rolle_von(user))


@router.post("/neuigkeiten/alle-gelesen")
async def neuigkeiten_alle_gelesen(user=Depends(current_firma)):
    return await _alle_gelesen(_konto_firma(user), rolle_von(user))


@router.post("/neuigkeiten/hinweis")
async def neuigkeiten_hinweis(user=Depends(current_firma)):
    return await _hinweis(_konto_firma(user), rolle_von(user))


@router.post("/neuigkeiten/{eintrag_id}/gelesen")
async def neuigkeiten_gelesen(eintrag_id: str, user=Depends(current_firma)):
    return await _gelesen(_konto_firma(user), rolle_von(user), eintrag_id)


# ------------------------------------------------------------ Fahrer
@router.get("/driver/neuigkeiten")
async def fahrer_neuigkeiten_liste(driver=Depends(current_driver)):
    return await _liste(_konto_fahrer(driver), "fahrer")


@router.get("/driver/neuigkeiten/stand")
async def fahrer_neuigkeiten_stand(driver=Depends(current_driver)):
    return await _zaehler(_konto_fahrer(driver), "fahrer")


@router.post("/driver/neuigkeiten/alle-gelesen")
async def fahrer_neuigkeiten_alle_gelesen(driver=Depends(current_driver)):
    return await _alle_gelesen(_konto_fahrer(driver), "fahrer")


@router.post("/driver/neuigkeiten/hinweis")
async def fahrer_neuigkeiten_hinweis(driver=Depends(current_driver)):
    return await _hinweis(_konto_fahrer(driver), "fahrer")


@router.post("/driver/neuigkeiten/{eintrag_id}/gelesen")
async def fahrer_neuigkeiten_gelesen(eintrag_id: str, driver=Depends(current_driver)):
    return await _gelesen(_konto_fahrer(driver), "fahrer", eintrag_id)
