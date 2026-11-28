# -*- coding: utf-8 -*-
"""Aufbewahrung der KI-Daten (Review 26.09.2026, Nr. 140/143/147).

ki_bewertungen speichert je Lauf das komplette Eingabepaket (Fahrernotizen,
Antworten auf Rueckfragen, Schaeden, FIN, Recherchetext, Roh-Antwort) —
bisher ohne Frist. Zwei Stufen im stuendlichen Aufraeumlauf
("ki_bewertungen_retention", cleanup_service):

  1. aelter als KI_BEWERTUNG_ROHDATEN_TAGE (Standard 90): Rohdaten weg
     (eingabe, roh, recherche.text/quellen, abgleich) — Ergebnis (Geldwerte,
     Status, Datenlage), Kosten, Hashes und Fahrzeugbezug bleiben fuer
     Statistik, Budget-Abgleich und Kalibrierung.
  2. aelter als KI_BEWERTUNG_TAGE (Standard 730): Bewertung geloescht —
     ausser ein Lernfall (ki_lernfaelle) haengt noch daran (Abholung ueber
     protocol_id+input_hash, Vertrag ueber ki_bewertung_id).

Dazu die Pseudonymisierung der Nutzerkennung beim Loeschen eines Kontos
(Sucher-Loeschung, routes.admin) — wie zugang_grants und Snapshots.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from deps import db as _db
from konfig import zahl_env

log = logging.getLogger("autohandel.ki")

SAMMLUNG = "ki_bewertungen"
LERN_SAMMLUNG = "ki_lernfaelle"

#: Felder, die nach der Rohdaten-Frist verschwinden (auf None gesetzt, damit
#: ein spaeterer Leser "Rohdaten entfernt" von "nie vorhanden" unterscheidet).
ROHDATEN_FELDER = ("eingabe", "roh", "abgleich", "vorschau")
#: ... und innerhalb der Recherche nur Text/Quellen — Status, Zahl der Suchen
#: und "gelernt" bleiben (Statistik). Nur setzbar, wenn recherche ein Objekt ist.
RECHERCHE_FELDER = ("recherche.text", "recherche.quellen", "recherche.verworfen")


def rohdaten_tage() -> int:
    return zahl_env("KI_BEWERTUNG_ROHDATEN_TAGE", 90, unten=1, oben=3650)


def aufbewahrung_tage() -> int:
    return zahl_env("KI_BEWERTUNG_TAGE", 730, unten=30, oben=3650)


async def rohdaten_entfernen(db, filt: Dict[str, Any], jetzt: Optional[str] = None) -> int:
    """Rohdaten aller Bewertungen, die `filt` trifft und die noch welche
    tragen, entfernen. Liefert die Zahl geaenderter Dokumente."""
    jetzt = jetzt or datetime.now(timezone.utc).isoformat()
    offen = {**filt, "rohdaten_entfernt_am": {"$in": [None, ""]}}
    leer = {f: None for f in ROHDATEN_FELDER}
    # Mongo kann "recherche.text" nicht setzen, wenn recherche null ist —
    # deshalb zwei Wege: Recherche-Objekt (Text/Quellen raus) und Rest.
    r1 = await db[SAMMLUNG].update_many(
        {**offen, "recherche": {"$type": "object"}},
        {"$set": {**leer, **{f: None for f in RECHERCHE_FELDER}, "rohdaten_entfernt_am": jetzt}})
    r2 = await db[SAMMLUNG].update_many(
        {**offen, "recherche": {"$not": {"$type": "object"}}},
        {"$set": {**leer, "rohdaten_entfernt_am": jetzt}})
    return int(r1.modified_count) + int(r2.modified_count)


async def _lernfall_haengt_daran(db, bew: dict) -> bool:
    if bew.get("id") and await db[LERN_SAMMLUNG].count_documents({"ki_bewertung_id": bew["id"]}, limit=1):
        return True
    if bew.get("protocol_id") and bew.get("input_hash") and await db[LERN_SAMMLUNG].count_documents(
            {"protocol_id": bew["protocol_id"], "input_hash": bew["input_hash"]}, limit=1):
        return True
    return False


async def ki_bewertungen_retention(db=None, now: Optional[datetime] = None, limit: int = 2000) -> Dict[str, int]:
    """Schritt des Aufraeumlaufs. Wirft nie (Fehler -> Log, Teilergebnis)."""
    db = db if db is not None else _db
    now = now or datetime.now(timezone.utc)
    erg = {"rohdaten_entfernt": 0, "geloescht": 0, "behalten_lernfall": 0}
    try:
        grenze_roh = (now - timedelta(days=rohdaten_tage())).isoformat()
        # Ein laufender Lauf (status laeuft) behaelt sein Paket — er ist
        # ohnehin nur Minuten alt; nach der Frist ist er ein Leichenrest.
        erg["rohdaten_entfernt"] = await rohdaten_entfernen(db, {"created_at": {"$lt": grenze_roh}}, now.isoformat())
    except Exception:  # noqa: BLE001
        log.exception("KI-Bewertungen: Rohdaten nicht entfernt")
    try:
        grenze = (now - timedelta(days=aufbewahrung_tage())).isoformat()
        weg = []
        async for bew in db[SAMMLUNG].find({"created_at": {"$lt": grenze}},
                                           {"_id": 0, "id": 1, "protocol_id": 1, "input_hash": 1}).limit(limit):
            if await _lernfall_haengt_daran(db, bew):
                erg["behalten_lernfall"] += 1
                continue
            if bew.get("id"):
                weg.append(bew["id"])
        for i in range(0, len(weg), 500):
            r = await db[SAMMLUNG].delete_many({"id": {"$in": weg[i:i + 500]}})
            erg["geloescht"] += int(r.deleted_count)
    except Exception:  # noqa: BLE001
        log.exception("KI-Bewertungen: Frist-Loeschung gescheitert")
    if erg["rohdaten_entfernt"] or erg["geloescht"]:
        log.info("ki_bewertungen_retention: %s", erg)
    return erg


#: Nr. 147: Lernfaelle werden nicht geloescht — deshalb duerfen sie keinen
#: Klartext tragen: keine Freitext-Notiz des Fahrers (note/text) und keine
#: Fahrzeugidentitaet aus Abschnitt 1 (FIN). Die Kalibrierung liest davon
#: nur Nachlaesse, Kategorien und Positionen (ai.kalibrierung).
_LERNFALL_FREITEXT = ("note", "text", "manual_hint")
_LERNFALL_IDENTITAET = ("vin",)


def lernfall_ohne_klartext(liste) -> list:
    """Schaeden/Abweichungen fuer einen Lernfall ohne Freitext und FIN."""
    raus = []
    for d in (liste or []):
        if not isinstance(d, dict):
            continue
        d = {k: v for k, v in d.items() if k not in _LERNFALL_FREITEXT}
        if str(d.get("field") or "") in _LERNFALL_IDENTITAET:
            d = {k: v for k, v in d.items() if k not in ("expected", "actual")}
        raus.append(d)
    return raus


async def nutzer_pseudonymisieren(db, user_id: str, pseudonym: str) -> int:
    """Review 26.09.2026 (Nr. 143): Nutzerkennung in Bewertungen und
    Lernfaellen auf das Pseudonym (deterministisch, wie zugang_grants)."""
    if not user_id:
        return 0
    n = 0
    jetzt = datetime.now(timezone.utc).isoformat()
    for coll in (SAMMLUNG, LERN_SAMMLUNG):
        r = await db[coll].update_many({"user_id": user_id},
                                       {"$set": {"user_id": pseudonym, "pseudonymisiert_at": jetzt}})
        n += int(r.modified_count)
    return n
