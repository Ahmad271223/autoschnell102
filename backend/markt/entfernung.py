# -*- coding: utf-8 -*-
"""Nicht gesehen ist NICHT verkauft. Ein Listing, das seit N Tagen nicht
mehr im Top-20-Sample auftaucht, wird gezielt einzeln nachgeprueft (ein
Scraper-Lauf mit der Detail-URL, maxItems=1, ~0,007 $). Nur wenn das Inserat
wirklich nicht mehr erreichbar ist: confirmed_removed. Die Oberflaeche sagt
dann "Inserat nicht mehr online" — nie "verkauft".

Review 26.09.2026 Nr. 49/50 — zweistufig und ID-geprueft:
  * liefert der Detail-Scrape eine Zeile mit einer ANDEREN listing_id
    (Weiterleitung, Aehnliche-Angebote-Seite), gilt die Pruefung als
    "unklar" und nichts wird aktualisiert
  * eine leere Antwort ist noch kein Beleg (Sperre, Zeitueberschreitung,
    Wartung): erste leere Pruefung -> verification_pending mit
    verification_leer_am und leer_zaehler=1; erst eine ZWEITE leere Pruefung
    mindestens ZWEITE_PRUEFUNG_NACH_H spaeter (naechster Tageslauf) setzt
    confirmed_removed. Taucht das Listing dazwischen in einem Sample auf,
    setzt speicher.verarbeiten es auf 'seen' und loescht die Merker."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Dict

from markt import apify, budget, konfig, normalisieren
from markt.konfig import LISTINGS

log = logging.getLogger(__name__)
ZWEITE_PRUEFUNG_NACH_H = 12     # Nr. 49: Abstand zwischen erster und zweiter leerer Pruefung
PENDING_VERWAIST_NACH_H = 24    # haengende 'verification_pending' ohne Ergebnis (Prozess weg) erneut einplanen


def _schluessel(listing: Dict[str, Any]) -> Dict[str, Any]:
    return {"source": listing["source"], "listing_id": listing["listing_id"]}


async def pruefen(db, listing: Dict[str, Any]) -> str:
    """Zustand nach der Pruefung: confirmed_removed | verification_pending (erste leere
    Pruefung) | not_seen_in_sample (noch online) | unklar."""
    jetzt = konfig.jetzt_iso()
    url = listing.get("url") or f"https://suchen.mobile.de/fahrzeuge/details.html?id={listing['listing_id']}"
    res = await budget.reservieren(db, konfig.kosten_je_lauf_usd(konfig.actor(), 1))
    if res is None:
        return "unklar"
    aktuell = await db[LISTINGS].find_one(_schluessel(listing), {"_id": 0, "leer_zaehler": 1, "verification_leer_am": 1,
                                                                "active_state": 1}) or {}
    leer_vorher = int(aktuell.get("leer_zaehler") or 0)
    await db[LISTINGS].update_one(_schluessel(listing),
                                  {"$set": {"active_state": "verification_pending", "verification_started_at": jetzt}})
    # Zustand, in den ein technisch unklares Ergebnis zurueckfaellt: eine begonnene
    # zweistufige Pruefung bleibt 'verification_pending' (Merker bleiben), sonst not_seen_in_sample
    rueckfall = "verification_pending" if leer_vorher else "not_seen_in_sample"
    try:
        r = await apify.lauf([url], 1, zeitlimit_s=120)
    except apify.ApifyFehler as e:
        await budget.abrechnen(db, res, None if e.art in ("zeit", "ausfall") else 0.0, 0, gelaufen=e.art in ("zeit", "ausfall"))
        await db[LISTINGS].update_one(_schluessel(listing), {"$set": {"active_state": rueckfall, "verification_error": e.art}})
        return "unklar"
    # Nur die Frage "noch online?" — ein inzwischen als beschaedigt markiertes Inserat ist
    # trotzdem online (Review 26.09.2026 Nr. 1: sonst faelschlich confirmed_removed).
    items = normalisieren.listings_aus_items(r["items"], beschaedigte_verwerfen=False)
    await budget.abrechnen(db, res, r.get("usd"), len(items))
    if not items:
        if leer_vorher >= 1:
            # zweite leere Pruefung (Kandidatenauswahl stellt den Abstand sicher): jetzt bestaetigt
            await db[LISTINGS].update_one(_schluessel(listing),
                                          {"$set": {"active_state": "confirmed_removed", "confirmed_removed_at": jetzt,
                                                    "verified_at": jetzt, "leer_zaehler": leer_vorher + 1}})
            return "confirmed_removed"
        await db[LISTINGS].update_one(_schluessel(listing),
                                      {"$set": {"active_state": "verification_pending", "verification_leer_am": jetzt,
                                                "leer_zaehler": 1, "verified_at": jetzt}})
        return "verification_pending"
    l = items[0]
    # Nr. 50: die gelieferte Zeile MUSS das angefragte Inserat sein — sonst "unklar", nichts aendern
    if str(l.get("listing_id") or "") != str(listing["listing_id"]):
        await db[LISTINGS].update_one(_schluessel(listing),
                                      {"$set": {"active_state": rueckfall, "verification_error": "id_abweichung",
                                                "verification_fremde_id": str(l.get("listing_id") or "")[:40]}})
        return "unklar"
    setzen = {"active_state": "not_seen_in_sample", "verified_online_at": jetzt, "verified_at": jetzt,
              "current_price": float(l["price_gross"])}
    await db[LISTINGS].update_one(_schluessel(listing),
                                  {"$set": setzen, "$unset": {"verification_leer_am": "", "leer_zaehler": "",
                                                              "verification_error": "", "verification_fremde_id": ""}})
    return "not_seen_in_sample"


async def kandidaten(db, limit: int):
    """Erste Pruefung: seit N Tagen nicht im Sample (und nicht kuerzlich geprueft).
    Zweite Pruefung (Nr. 49): erste war leer und liegt >= 12 h zurueck.
    Verwaist: verification_pending ohne Ergebnis seit > 24 h (Prozess weg)."""
    jetzt = konfig.jetzt()
    grenze = (jetzt - timedelta(days=konfig.entfernung_nach_tagen())).isoformat()
    zweite = (jetzt - timedelta(hours=ZWEITE_PRUEFUNG_NACH_H)).isoformat()
    verwaist = (jetzt - timedelta(hours=PENDING_VERWAIST_NACH_H)).isoformat()
    filt = {"$or": [
        {"active_state": "not_seen_in_sample", "not_seen_since": {"$lte": grenze},
         "$or": [{"verified_at": {"$exists": False}}, {"verified_at": {"$lt": grenze}}]},
        {"active_state": "verification_pending", "leer_zaehler": {"$gte": 1}, "verification_leer_am": {"$lte": zweite}},
        {"active_state": "verification_pending", "leer_zaehler": {"$exists": False},
         "verification_started_at": {"$lte": verwaist}},
    ]}
    return await db[LISTINGS].find(filt, {"_id": 0, "source": 1, "listing_id": 1, "url": 1, "leer_zaehler": 1})\
        .sort([("leer_zaehler", -1), ("not_seen_since", 1)]).to_list(limit)


async def taeglich(db) -> Dict[str, Any]:
    """Einmal je Tag (Sperre) bis zu MARKT_ENTFERNUNG_MAX_JE_TAG Listings nachpruefen."""
    if not konfig.entfernung_pruefen() or konfig.entfernung_max_je_tag() <= 0:
        return {"geprueft": 0}
    tag = konfig.heute_tag()
    from job_lock import acquire
    token = await acquire(db, f"markt-entfernung-{tag}", ttl_seconds=20 * 3600)
    if not token:
        return {"geprueft": 0}
    zaehler: Dict[str, int] = {"geprueft": 0, "confirmed_removed": 0, "verification_pending": 0,
                               "not_seen_in_sample": 0, "unklar": 0}
    for l in await kandidaten(db, konfig.entfernung_max_je_tag()):
        try:
            erg = await pruefen(db, l)
        except Exception:  # noqa: BLE001
            log.exception("Entfernungs-Pruefung %s gescheitert", l.get("listing_id"))
            erg = "unklar"
        zaehler["geprueft"] += 1
        zaehler[erg] = zaehler.get(erg, 0) + 1
    return zaehler
