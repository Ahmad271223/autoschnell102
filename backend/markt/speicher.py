# -*- coding: utf-8 -*-
"""Verarbeitung eines Tages-Samples je Segment — deterministisch, ohne KI:

  * market_listings: ein Hauptdatensatz je (source, listing_id); erstes/
    letztes Sehen, aktueller Preis, Preisverlauf, Zustand
  * market_listing_snapshots: eine Zeile je Listing, Segment und Tag
    (Preis, Bewertung, Rang, Preisaenderung)
  * Zustaende: seen | not_seen_in_sample | verification_pending |
    confirmed_removed — "nicht im Sample" ist NIE "verkauft"
  * market_segment_daily_stats: Tagesaggregat (min/median/avg/max/p25/p75)
  * market_segment_stats: aktueller Stand + 7/30-Tage-Trend + Datenlage
  * market_opportunities: regelbasierte Chancen

Reparaturwelle 6 (Review 26.09.2026 abends):
  * Nr. 103: observation_at am Listing — ein langsamer alter Lauf ueberschreibt
    keinen neueren Preis (Upsert nur, wenn Beobachtung >= gespeicherte; sonst
    nur Segmentzuordnung), Snapshots ebenso je (Segment, Tag)
  * Nr. 104: `tag` = Tag des JOBS (nicht der Ausfuehrung) — ein verspaeteter
    Lauf nach Mitternacht schreibt unter seinem Job-Tag
  * Nr. 132: Preisaenderung JE SEGMENT (gegen den letzten Snapshot dieses
    Listings in diesem Segment); der globale Verlauf (price_history) bleibt
  * Nr. 139/145: je Segment ein Zustand am Listing (segmente.<id>: first_seen_at,
    last_seen_at, first_rank, last_rank, in_letztem_lauf) — in_letztem_lauf
    wird sofort je Lauf gesetzt; das globale active_state bleibt TAGESBASIERT
    (ein Inserat, das heute in irgendeinem Segment gesehen wurde, ist 'seen')
  * Nr. 83: taucht ein Inserat wieder auf, sind alle Merker einer Entfernungs-
    pruefung hinfaellig (confirmed_removed_at, verification_*, verified_at ->
    last_verification_at)
  * Nr. 101/102: DuplicateKeyError bei den Upserts (Rennen zweier Laeufe) wird
    einmal wiederholt — nie ein neuer Actor-Lauf
  * Nr. 108/109: Trend- und Chancenbasis nur Tage mit bewiesener Top-N-Sortierung
  * Nr. 82: Datenlage 'gut'/'mittel' relativ zur Zeilenzahl des Auftrags
  * Nr. 106/107: erwartete Laeufe = tatsaechlich geplante Jobs (Fallback Konfiguration)
  * Nr. 143: sample_incomplete (Markt groesser als Bestellung, aber weniger geliefert)
"""
from __future__ import annotations

import statistics
import uuid
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from markt import konfig
from markt.konfig import CHANCEN, JOBS, LISTINGS, SEGMENTE, SEGMENTSTATS, SNAPSHOTS, TAGESSTATS

ZUSTAENDE = ("seen", "not_seen_in_sample", "verification_pending", "confirmed_removed")
LISTING_FELDER = ("url", "make", "model", "variant", "title", "category", "first_registration", "mileage_km",
                  "power_kw", "power_ps", "fuel", "gearbox", "hu", "color", "doors", "seats", "condition",
                  "price_net", "vat", "price_rating", "seller_type", "seller_id", "postal_code", "city",
                  "country", "latitude", "longitude", "mobile_created_at", "mobile_modified_at",
                  "mobile_renewed_at", "mobile_scraped_at", "image", "num_images", "make_id", "model_id", "hsn", "tsn",
                  "previous_owners")
# Nr. 83: Merker einer Entfernungspruefung, die beim Wiederauftauchen weg muessen
PRUEFUNGS_MERKER = ("not_seen_since", "verification_leer_am", "leer_zaehler", "confirmed_removed_at",
                    "verification_started_at", "verification_error", "verification_fremde_id",
                    "unbestaetigt_einzelquelle", "verified_actor")


# ---------------------------------------------------------------- Statistik
def _perzentil(sortiert: List[float], p: float) -> float:
    if not sortiert:
        return 0.0
    if len(sortiert) == 1:
        return float(sortiert[0])
    k = (len(sortiert) - 1) * p
    f, c = int(k), min(int(k) + 1, len(sortiert) - 1)
    return round(sortiert[f] + (sortiert[c] - sortiert[f]) * (k - f), 2)


def kennzahlen(preise: List[float]) -> Dict[str, Any]:
    s = sorted(float(p) for p in preise if p is not None)
    if not s:
        return {"sample_size": 0, "min_price": None, "median_price": None, "avg_price": None, "max_price": None,
                "p25_price": None, "p75_price": None}
    return {"sample_size": len(s), "min_price": s[0], "median_price": round(statistics.median(s), 2),
            "avg_price": round(sum(s) / len(s), 2), "max_price": s[-1],
            "p25_price": _perzentil(s, 0.25), "p75_price": _perzentil(s, 0.75)}


def _tag_minus(tag: str, tage: int) -> str:
    return (datetime.strptime(tag, "%Y-%m-%d") - timedelta(days=tage)).strftime("%Y-%m-%d")


ABDECKUNG_GUT_PCT = 70.0      # Nr. 45: Anteil beobachteter Tage an den Kalendertagen seit Erstbeobachtung
ABDECKUNG_MITTEL_PCT = 40.0
TREND_TOLERANZ = {7: (3, 2), 30: (7, 7)}     # Nr. 43/44: 7-Tage-Basis zwischen t-10 und t-5, 30-Tage zwischen t-37 und t-23
CHANCE_VERGLEICH_TAGE = 3                   # Nr. 46: neues_minimum/neu_guenstig/neu_topN nur gegen einen hoechstens 3 Tage alten Stand
WIEDERKEHRER_TAGE = 14                      # Welle 5 Nr. 47: nach 14 Tagen ohne Schnappschuss wieder "neu im Sample"
# Welle 6 Nr. 82: Schwellen der mittleren Stichprobe RELATIV zur Zeilenzahl des Auftrags (10 Zeilen: gut ab 8, mittel ab 5)
STICHPROBE_GUT = 0.8
STICHPROBE_MITTEL = 0.5


def datenlage(tage: int, mittlere_groesse: float, abdeckung_pct: float = 100.0, rows: Optional[int] = None,
              sample_incomplete: bool = False) -> str:
    """<7 Tage niedrig; >=30 Tage, mittlere Stichprobe >= 80 % der bestellten Zeilen UND Abdeckung >= 70 % gut;
    mittel ab 50 % der Zeilen und Abdeckung >= 40 %; sonst niedrig. Nr. 143: 'unvollstaendig', wenn der
    letzte gueltige Lauf weniger lieferte, als der Markt hergibt."""
    if sample_incomplete:
        return "unvollstaendig"
    if tage < 7:
        return "niedrig"
    soll = max(1, int(rows or konfig.rows_je_segment()))
    if tage >= 30 and mittlere_groesse >= soll * STICHPROBE_GUT and abdeckung_pct >= ABDECKUNG_GUT_PCT:
        return "gut"
    if mittlere_groesse >= soll * STICHPROBE_MITTEL and abdeckung_pct >= ABDECKUNG_MITTEL_PCT:
        return "mittel"
    return "niedrig"


# ---------------------------------------------------------------- Verarbeitung
LAEUFE_MAX = 12     # Nr. 16/17 + Welle 6 Nr. 88: hoechstens 12 Laeufe je Tag am Snapshot / Tagesaggregat (4 planmaessig + manuelle)


async def _einmal_wiederholen(fn: Callable[[], Awaitable[Any]]) -> Any:
    """Nr. 101/102: ein Upsert-Rennen zweier Laeufe (DuplicateKeyError am Unique-Index) wird genau
    einmal wiederholt — beim zweiten Mal trifft der Filter das inzwischen angelegte Dokument."""
    try:
        return await fn()
    except DuplicateKeyError:
        return await fn()


async def preis_aktualisieren(db, schluessel: Dict[str, Any], alt: Optional[float], neu: float, jetzt_iso: str) -> Optional[float]:
    """Reparaturwelle 5 Nr. 40: EINE Stelle fuer eine beobachtete Preisaenderung (Sample-Lauf
    UND Entfernungspruefung): price_history anhaengen (hoechstens 120 Eintraege), price_changes /
    price_reductions hochzaehlen, last_price_change_at setzen. Liefert die Differenz (neu - alt)
    oder None, wenn sich nichts geaendert hat (oder kein alter Preis bekannt war)."""
    try:
        alt_f = float(alt or 0)
    except (TypeError, ValueError):
        alt_f = 0.0
    if not alt_f or alt_f == float(neu):
        return None
    delta = round(float(neu) - alt_f, 2)
    await db[LISTINGS].update_one(
        schluessel,
        {"$push": {"price_history": {"$each": [{"at": jetzt_iso, "price": float(neu)}], "$slice": -120}},
         "$inc": {"price_changes": 1, "price_reductions": 1 if delta < 0 else 0},
         "$set": {"last_price_change_at": jetzt_iso, "current_price": float(neu)}})
    return delta


def _beobachtung_frisch(jetzt_iso: str, feld: str = "observation_at") -> Dict[str, Any]:
    """Nr. 103: Filterteil 'gespeicherte Beobachtung ist nicht neuer als diese'."""
    return {"$or": [{feld: {"$exists": False}}, {feld: {"$lte": jetzt_iso}}]}


async def _listing_schreiben(db, l: Dict[str, Any], seg_id: str, model_id: Optional[str], rang: int, preis: float,
                             tag: str, jetzt_iso: str, seg_frisch: bool) -> Tuple[Optional[Dict[str, Any]], bool]:
    """Hauptdatensatz des Inserats: (Stand VOR dem Lauf, veraltet). veraltet=True heisst: es gibt schon
    eine NEUERE Beobachtung (Nr. 103) — dann wurden nur Segmentzuordnung und Segmentzustand ergaenzt."""
    schl = {"source": l["source"], "listing_id": l["listing_id"]}
    felder = {k: l.get(k) for k in LISTING_FELDER}
    pfad = f"segmente.{seg_id}"
    seg_stand: Dict[str, Any] = {f"{pfad}.last_seen_at": jetzt_iso, f"{pfad}.last_seen_tag": tag, f"{pfad}.last_rank": rang,
                                 f"{pfad}.last_price": preis}
    if seg_frisch:
        seg_stand[f"{pfad}.in_letztem_lauf"] = True
    aenderung = {
        "$setOnInsert": {"first_seen_at": jetzt_iso, "first_price": preis, "first_segment_id": seg_id,
                         "price_changes": 0, "price_reductions": 0,
                         "price_history": [{"at": jetzt_iso, "price": preis}], "created_at": jetzt_iso},
        "$set": {**felder, "last_seen_at": jetzt_iso, "last_seen_tag": tag, "current_price": preis,
                 "active_state": "seen", "last_segment_id": seg_id, "model_id": model_id, "last_rank": rang,
                 "updated_at": jetzt_iso, "observation_at": jetzt_iso, **seg_stand},
        "$min": {f"{pfad}.first_seen_at": jetzt_iso},
        # Nr. 14/15: ein Inserat kann in mehreren Segmenten stehen (Automatik-Auftrag
        # und ueberlappender eigener Auftrag) — alle Segmente/Modelle merken
        "$addToSet": {"segment_ids": seg_id, **({"model_ids": model_id} if model_id else {})},
        # Nr. 49 + Welle 6 Nr. 83: wieder im Sample -> jede angefangene/abgeschlossene Entfernungs-
        # pruefung ist hinfaellig; verified_at wird zu last_verification_at
        "$unset": {k: "" for k in PRUEFUNGS_MERKER},
        "$rename": {"verified_at": "last_verification_at"},
    }
    try:
        vorher = await db[LISTINGS].find_one_and_update({**schl, **_beobachtung_frisch(jetzt_iso)}, aenderung,
                                                        upsert=True, return_document=ReturnDocument.BEFORE)
        veraltet = False
    except DuplicateKeyError:
        # entweder ein Rennen zweier Laeufe (Nr. 102: einmal wiederholen) oder eine NEUERE Beobachtung
        # (Nr. 103: nur Zuordnung ergaenzen, Preis/Zustand nicht anfassen)
        vorher = await db[LISTINGS].find_one(schl)
        if vorher is not None and str(vorher.get("observation_at") or "") > jetzt_iso:
            veraltet = True
            await db[LISTINGS].update_one(schl, {"$addToSet": aenderung["$addToSet"], "$min": aenderung["$min"],
                                                 "$max": {f"{pfad}.last_seen_at": jetzt_iso, "last_seen_tag": tag}})
        else:
            vorher = await db[LISTINGS].find_one_and_update({**schl, **_beobachtung_frisch(jetzt_iso)}, aenderung,
                                                            upsert=True, return_document=ReturnDocument.BEFORE)
            veraltet = False
    # Nr. 139: erster Rang in DIESEM Segment — nur beim ersten Sehen im Segment
    if not ((vorher or {}).get("segmente") or {}).get(seg_id):
        await db[LISTINGS].update_one({**schl, f"{pfad}.first_rank": {"$exists": False}},
                                      {"$set": {f"{pfad}.first_rank": rang, f"{pfad}.first_price": preis}})
    return vorher, veraltet


async def _snapshot_schreiben(db, l: Dict[str, Any], seg_id: str, tag: str, jetzt_iso: str,
                              setzen: Dict[str, Any], lauf_eintrag: Dict[str, Any]) -> None:
    """Snapshot je (Listing, Segment, Tag): Hauptfelder nur, wenn keine neuere Beobachtung gespeichert ist
    (Nr. 103); der Lauf-Eintrag ('laeufe') bleibt in jedem Fall erhalten."""
    schl = {"listing_id": l["listing_id"], "segment_id": seg_id, "date": tag}
    push = {"$push": {"laeufe": {"$each": [lauf_eintrag], "$slice": -LAEUFE_MAX}}}

    async def _voll():
        await db[SNAPSHOTS].update_one({**schl, **_beobachtung_frisch(jetzt_iso, "observed_at")},
                                       {"$set": setzen, **push}, upsert=True)
    try:
        await _voll()
    except DuplicateKeyError:
        snap = await db[SNAPSHOTS].find_one(schl, {"_id": 0, "observed_at": 1})
        if snap and str(snap.get("observed_at") or "") > jetzt_iso:
            await db[SNAPSHOTS].update_one(schl, push)          # alter Lauf: nur protokollieren
        else:
            await _voll()                                       # Rennen: einmal wiederholen


async def verarbeiten(db, segment: Dict[str, Any], listings: List[Dict[str, Any]], *,
                      beobachtet: Optional[datetime] = None, lauf_tag: Optional[str] = None,
                      top_n_bewiesen: bool = True, tag: Optional[str] = None,
                      lauf_info: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Ein Tages-Sample (Preis aufsteigend, vom Worker bestaetigt) eines Segments einarbeiten.

    Review 26.09.2026 abends P1: ein Lauf mit unsicherer Sortierung kommt hier NICHT mehr an —
    der Worker schliesst ihn als 'data_invalid' ab, ohne Snapshots, Tagesstatistik, Chancen.
    lauf_tag (Nr. 16/17): Schluessel des Laufs (z. B. '2026-09-26#2') — bei mehreren
    Abrufen je Tag bleibt jeder Lauf in 'laeufe' erhalten.
    P5: die Hauptfelder der Tagesstatistik zeigen den letzten GUELTIGEN Lauf des Tages mit
    Treffern; ein leerer zweiter Lauf wird nur in 'laeufe' protokolliert (leer=True) und
    zerstoert den guten Tageswert nicht. War der Tag bisher leer, ueberschreibt ein Lauf mit
    Zeilen. Ein Tag, an dem ALLE Laeufe leer waren, bleibt sample_size 0 (echte Marktluecke).
    Reparaturwelle 5 Nr. 1: top_n_bewiesen=False (Scraper ohne Positionsnummer, nur monoton
    sortiert) wird am Lauf und — wenn der Lauf die Hauptwerte stellt — am Tagesaggregat
    vermerkt; die Datenqualitaet sagt dann 'Top-N nicht bewiesen'.
    Welle 6 Nr. 104: `tag` = Tag des Jobs (Statistik-Zuordnung); der Beobachtungszeitpunkt bleibt
    die echte Laufzeit. lauf_info (Nr. 114/143): actor_build, sample_incomplete, run_id am Lauf."""
    jetzt = beobachtet or konfig.jetzt()
    jetzt_iso = jetzt.isoformat()
    tag = tag or konfig.heute_tag(jetzt)
    lauf_schluessel = lauf_tag or tag
    info = dict(lauf_info or {})
    seg_id = segment["id"]
    model_id = segment.get("model_id")
    seg_doc = await db[SEGMENTE].find_one({"id": seg_id}, {"_id": 0, "last_run_at": 1}) or {}
    # Nr. 145: gab es fuer DIESES Segment schon einen neueren Lauf, bestimmt dieser 'in_letztem_lauf'
    seg_frisch = str(seg_doc.get("last_run_at") or "") <= jetzt_iso
    # Nr. 109: Vergleichsstand fuer Chancen nur ein Tag mit Treffern UND bewiesener Sortierung
    vorher_stat = await db[TAGESSTATS].find_one({"segment_id": seg_id, "date": {"$lt": tag}, "sample_size": {"$gt": 0},
                                                 "top_n_bewiesen": {"$ne": False}},
                                                {"_id": 0}, sort=[("date", -1)])
    # Nr. 41/42: Tageswerte sind die VEREINIGUNG ueber alle Laeufe des Tages — die Listing-IDs
    # (neu im Sample / Preis gesenkt) stehen am Tagesaggregat und werden je Lauf ergaenzt
    heute_stat = await db[TAGESSTATS].find_one({"segment_id": seg_id, "date": tag},
                                               {"_id": 0, "new_in_sample_ids": 1, "price_reduced_ids": 1, "sample_size": 1}) or {}
    bisher_heute = int(heute_stat.get("sample_size") or 0)
    neu_ids = set(heute_stat.get("new_in_sample_ids") or [])
    reduziert_ids = set(heute_stat.get("price_reduced_ids") or [])
    zaehler = {"neu_gesamt": 0, "neu_im_sample": 0, "preis_gesunken": 0, "preis_gestiegen": 0, "chancen": 0, "veraltet": 0}
    heute: List[Dict[str, Any]] = []
    for rang, l in enumerate(listings, 1):
        preis = float(l["price_gross"])
        vorher, veraltet = await _listing_schreiben(db, l, seg_id, model_id, rang, preis, tag, jetzt_iso, seg_frisch)
        if veraltet:
            zaehler["veraltet"] += 1
        delta_global: Optional[float] = None
        if vorher is None:
            zaehler["neu_gesamt"] += 1
        elif not veraltet:
            alt = float(vorher.get("current_price") or 0)
            delta_global = await preis_aktualisieren(db, {"source": l["source"], "listing_id": l["listing_id"]}, alt, preis, jetzt_iso)
        # Nr. 41: "neu im Sample" = VOR diesem Lauf kein Snapshot in diesem Segment — weder an
        # einem Vortag noch heute frueher (zweiter Lauf des Tages findet das Tagesdokument)
        letzter_snap = await db[SNAPSHOTS].find_one({"listing_id": l["listing_id"], "segment_id": seg_id,
                                                     "date": {"$lte": tag}},
                                                    {"_id": 0, "rank_in_sample": 1, "date": 1, "rank_yesterday": 1, "new_in_sample": 1,
                                                     "rank_vergleich": 1, "wiederkehrer": 1, "price": 1},
                                                    sort=[("date", -1)])
        # Nr. 132: Preisaenderung JE SEGMENT — gegen den letzten Snapshot dieses Inserats in diesem Segment
        delta_eur: Optional[float] = None
        delta_pct: Optional[float] = None
        if letzter_snap is not None and letzter_snap.get("price") is not None and not veraltet:
            alt_seg = float(letzter_snap["price"])
            if alt_seg and alt_seg != preis:
                delta_eur = round(preis - alt_seg, 2)
                delta_pct = round(delta_eur / alt_seg * 100, 2)
                zaehler["preis_gesunken" if delta_eur < 0 else "preis_gestiegen"] += 1
        neu_im_sample = letzter_snap is None
        wiederkehrer = False
        rang_vergleich: Optional[int] = None
        if letzter_snap and letzter_snap.get("date") == tag:
            # heute schon gesehen: Rang von gestern bleibt, "neu heute" bleibt, wie es der erste Lauf setzte
            rang_vorher = letzter_snap.get("rank_yesterday")
            rang_vergleich = letzter_snap.get("rank_vergleich")
            neu_heute = bool(letzter_snap.get("new_in_sample"))
            wiederkehrer = bool(letzter_snap.get("wiederkehrer"))
        else:
            snap_datum = str((letzter_snap or {}).get("date") or "")
            # Welle 5 Nr. 46: rank_yesterday nur, wenn der Vergleichsschnappschuss vom VORTAG ist;
            # fuer "neu in den Top-N" gilt ein hoechstens 3 Tage alter Stand (rank_vergleich)
            rang_vorher = letzter_snap.get("rank_in_sample") if (letzter_snap and snap_datum == _tag_minus(tag, 1)) else None
            rang_vergleich = letzter_snap.get("rank_in_sample") if (letzter_snap and snap_datum >= _tag_minus(tag, CHANCE_VERGLEICH_TAGE)) else None
            # Welle 5 Nr. 47: nach >= 14 Tagen ohne Schnappschuss ist das Inserat wieder "neu im Sample"
            if letzter_snap and snap_datum <= _tag_minus(tag, WIEDERKEHRER_TAGE):
                neu_im_sample, wiederkehrer = True, True
            neu_heute = neu_im_sample
        if neu_im_sample:
            zaehler["neu_im_sample"] += 1
        if neu_heute:
            neu_ids.add(l["listing_id"])
        if delta_eur is not None and delta_eur < 0:
            reduziert_ids.add(l["listing_id"])
        setzen_snap = {"observed_at": jetzt_iso, "price": preis,
                       "price_rating": (l.get("price_rating") or {}).get("rating"),
                       "mobile_modified_at": l.get("mobile_modified_at"), "mobile_renewed_at": l.get("mobile_renewed_at"),
                       "rank_in_sample": rang, "price_change_eur": delta_eur, "price_change_pct": delta_pct,
                       "price_change_global_eur": delta_global,
                       "new_in_sample": neu_heute, "rank_yesterday": rang_vorher, "rank_vergleich": rang_vergleich,
                       "wiederkehrer": wiederkehrer, "model_id": model_id, "source": l["source"],
                       # Nr. 42: einmal am Tag gesenkt bleibt fuer den Tag gesenkt
                       **({"price_reduced_today": True} if l["listing_id"] in reduziert_ids else {})}
        # Nr. 16: jeder Lauf des Tages bleibt erhalten (Hauptfelder = letzter Lauf)
        await _snapshot_schreiben(db, l, seg_id, tag, jetzt_iso, setzen_snap,
                                  {"at": jetzt_iso, "price": preis, "rank_in_sample": rang, "tag": lauf_schluessel})
        heute.append({"listing": l, "preis": preis, "rang": rang, "neu_im_sample": neu_im_sample, "wiederkehrer": wiederkehrer,
                      "rang_vorher": rang_vorher, "rang_vergleich": rang_vergleich, "delta_eur": delta_eur,
                      "delta_pct": delta_pct, "neu_gesamt": vorher is None,
                      "first_price": (vorher or {}).get("first_price", preis)})
    ids_heute = [h["listing"]["listing_id"] for h in heute]
    # nicht mehr im Sample dieses Segments: KEIN Verkauf. Nr. 15: NUR Listings, die heute
    # nirgendwo gesehen wurden (last_seen_tag < heute) — ein Inserat, das heute in einem
    # anderen, ueberlappenden Segment auftauchte, bleibt "seen". (Global tagesbasiert — Nr. 145.)
    await db[LISTINGS].update_many(
        {"$or": [{"last_segment_id": seg_id}, {"segment_ids": seg_id}],
         "active_state": "seen", "last_seen_tag": {"$lt": tag}},
        {"$set": {"active_state": "not_seen_in_sample", "not_seen_since": jetzt_iso, "updated_at": jetzt_iso}})
    if seg_frisch:
        # Nr. 145: je Segment sofort — wer in DIESEM Lauf nicht dabei war, ist im Segment 'nicht im letzten Lauf'
        await db[LISTINGS].update_many({f"segmente.{seg_id}.in_letztem_lauf": True, "listing_id": {"$nin": ids_heute}},
                                       {"$set": {f"segmente.{seg_id}.in_letztem_lauf": False,
                                                 f"segmente.{seg_id}.not_in_run_since": jetzt_iso}})
    # Tagesaggregat (Nr. 17: jeder Lauf des Tages in 'laeufe'; P5: Hauptfelder = letzter
    # gueltiger Lauf des Tages MIT Treffern). Nr. 54: auch ein Lauf mit 0 Treffern schreibt das
    # Tagesaggregat (sample_size 0, ohne Preise), wenn der Tag noch keinen Treffer hatte —
    # der Tag zaehlt als beobachtet. Nr. 41/42: Tageswerte = Vereinigung ueber alle Laeufe des Tages.
    kz = kennzahlen([h["preis"] for h in heute])
    leer = kz["sample_size"] == 0
    tageswert_behalten = leer and bisher_heute > 0
    lauf_eintrag = {"at": jetzt_iso, "tag": lauf_schluessel, "sample_size": kz["sample_size"],
                    "min": kz["min_price"], "median": kz["median_price"], "avg": kz["avg_price"],
                    "max": kz["max_price"], "leer": leer, "top_n_bewiesen": bool(top_n_bewiesen), **info}
    setzen: Dict[str, Any] = {"model_id": model_id, "last_run_at": jetzt_iso}
    if not tageswert_behalten:
        setzen.update({**kz, "observed_at": jetzt_iso, "new_in_sample_today": len(neu_ids),
                       "price_reductions_today": len(reduziert_ids),
                       "new_in_sample_ids": sorted(neu_ids), "price_reduced_ids": sorted(reduziert_ids),
                       "listing_ids": ids_heute,
                       # Nr. 1: Hauptwerte stammen aus diesem Lauf — Top-N-Nachweis mitschreiben
                       "top_n_bewiesen": bool(top_n_bewiesen), "lauf_tag": lauf_schluessel,
                       # Nr. 143/114: Vollstaendigkeit der Stichprobe und Build des Scrapers dieses Laufs
                       "sample_incomplete": info.get("sample_incomplete"), "actor_build": info.get("actor_build")})
    await _einmal_wiederholen(lambda: db[TAGESSTATS].update_one(
        {"segment_id": seg_id, "date": tag},
        {"$set": setzen, "$push": {"laeufe": {"$each": [lauf_eintrag], "$slice": -LAEUFE_MAX}}},
        upsert=True))
    await segmentstatistik(db, seg_id, tag)
    # Welle 5 Nr. 51: last_success_at nur bei einem Lauf MIT Zeilen; ein leerer Lauf setzt last_empty_at
    # (der Stale-Monitor liest last_success_at — eine Marktluecke ist kein Erfolg)
    seg_setzen: Dict[str, Any] = {"last_sample_size": bisher_heute if tageswert_behalten else kz["sample_size"]}
    seg_setzen["last_empty_at" if leer else "last_success_at"] = jetzt_iso
    await db[SEGMENTE].update_one({"id": seg_id}, {"$set": seg_setzen, "$max": {"last_run_at": jetzt_iso}})
    zaehler["chancen"] = await chancen_ableiten(db, segment, heute, vorher_stat, tag, jetzt_iso)
    zaehler["sample_size"] = kz["sample_size"]
    zaehler["tageswert_behalten"] = tageswert_behalten
    return zaehler


# ---------------------------------------------------------------- Segmentstatistik
async def _tagesstat_vor(db, seg_id: str, tag: str, tage: int) -> Optional[Dict[str, Any]]:
    """Vergleichsbasis fuer den Trend (Nr. 43/44): der Datensatz mit Treffern, der dem Zieltag
    (tag - tage) am naechsten liegt — aber nur innerhalb der Toleranz (7 Tage: t-10..t-5,
    30 Tage: t-37..t-23). Liegt dort keiner, gibt es keinen Trend (None), statt einen
    25 Tage alten Stand als "7-Tage-Trend" auszugeben. Welle 6 Nr. 108: nur Tage mit
    bewiesener Top-N-Sortierung (ein 'nur monoton' sortierter Tag ist keine Trendbasis)."""
    vor, nach = TREND_TOLERANZ.get(tage, (max(1, tage // 3), max(1, tage // 3)))
    ziel = _tag_minus(tag, tage)
    von, bis = _tag_minus(tag, tage + vor), _tag_minus(tag, tage - nach)
    docs = await db[TAGESSTATS].find({"segment_id": seg_id, "date": {"$gte": von, "$lte": bis}, "sample_size": {"$gt": 0},
                                      "top_n_bewiesen": {"$ne": False}},
                                     {"_id": 0}).to_list(100)
    if not docs:
        return None
    ziel_d = datetime.strptime(ziel, "%Y-%m-%d")
    docs.sort(key=lambda d: (abs((datetime.strptime(d["date"], "%Y-%m-%d") - ziel_d).days), d["date"]))
    return docs[0]


def _gueltige_laeufe(tagesdoc: Dict[str, Any]) -> int:
    """Welle 5 Nr. 48/50: gueltige Laeufe eines Tages = Eintraege in 'laeufe' mit Treffern und
    bewiesener Sortierung; aeltere Dokumente ohne 'laeufe' zaehlen als ein Lauf."""
    laeufe = tagesdoc.get("laeufe")
    if not isinstance(laeufe, list) or not laeufe:
        return 1 if int(tagesdoc.get("sample_size") or 0) > 0 else 0
    return sum(1 for x in laeufe if int(x.get("sample_size") or 0) > 0 and x.get("top_n_bewiesen", True) is not False)


def _trend(aktuell: Optional[float], alt: Optional[float]) -> Tuple[Optional[float], Optional[float]]:
    if aktuell is None or alt is None or not alt:
        return None, None
    d = round(float(aktuell) - float(alt), 2)
    return d, round(d / float(alt) * 100, 2)


async def _bestandstrend(db, seg_id: str, heute: Dict[str, Any], alt: Optional[Dict[str, Any]]) -> Tuple[Optional[float], Optional[float], int]:
    """Nr. 55: mittlere Preisaenderung der Listings, die an BEIDEN Vergleichstagen im Sample
    waren (Preise aus den Tages-Snapshots) — unabhaengig von der Sample-Fluktuation.
    (eur, pct, anzahl_gemeinsam); ohne gemeinsame Autos (None, None, 0)."""
    if not alt:
        return None, None, 0
    gemeinsam = set(heute.get("listing_ids") or []) & set(alt.get("listing_ids") or [])
    if not gemeinsam:
        return None, None, 0
    preise: Dict[str, Dict[str, float]] = {heute["date"]: {}, alt["date"]: {}}
    async for s in db[SNAPSHOTS].find({"segment_id": seg_id, "date": {"$in": [heute["date"], alt["date"]]},
                                       "listing_id": {"$in": sorted(gemeinsam)}},
                                      {"_id": 0, "listing_id": 1, "date": 1, "price": 1}):
        if s.get("price") is not None:
            preise[s["date"]][s["listing_id"]] = float(s["price"])
    paare = [(preise[heute["date"]][lid], preise[alt["date"]][lid]) for lid in gemeinsam
             if lid in preise[heute["date"]] and lid in preise[alt["date"]]]
    if not paare:
        return None, None, 0
    delta = sum(h - a for h, a in paare)
    alt_summe = sum(a for _, a in paare)
    return round(delta / len(paare), 2), (round(delta / alt_summe * 100, 2) if alt_summe else None), len(paare)


async def geplante_laeufe(db, seg_id: str, bis_tag: str) -> int:
    """Welle 6 Nr. 106/107: wie viele planmaessige Jobs (job_type daily) gab es fuer dieses Segment
    bis einschliesslich `bis_tag` — die Erwartung kommt aus dem PLAN, nicht aus der heutigen
    Konfiguration (crawls_per_day kann sich geaendert haben)."""
    try:
        naechster = (datetime.strptime(bis_tag, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        return int(await db[JOBS].count_documents({"segment_id": seg_id, "job_type": "daily", "tag": {"$lt": naechster}}))
    except Exception:  # noqa: BLE001
        return 0


async def segmentstatistik(db, seg_id: str, tag: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Aktuellen Stand + Trends aus den Tagesaggregaten (nie aus Snapshots — Ausnahme Nr. 55:
    der Bestandstrend braucht die Preise gleicher Autos) neu rechnen."""
    heute = await db[TAGESSTATS].find_one({"segment_id": seg_id, **({"date": {"$lte": tag}} if tag else {})},
                                          {"_id": 0}, sort=[("date", -1)])
    if not heute:
        return None
    t = heute["date"]
    alle_docs = await db[TAGESSTATS].find({"segment_id": seg_id, "date": {"$lte": t}},
                                          {"_id": 0, "date": 1, "sample_size": 1, "new_in_sample_today": 1,
                                           "price_reductions_today": 1, "top_n_bewiesen": 1, "laeufe": 1}).to_list(2000)
    tage_docs = [d for d in alle_docs if int(d.get("sample_size") or 0) > 0]
    # Welle 5 Nr. 49: beobachtet = nur Tage mit GUELTIGEM Lauf (Treffer und bewiesene Top-N-Sortierung);
    # leere oder nur monoton sortierte Tage zaehlen nicht (Nr. 54 aus Welle 3 damit zurueckgenommen)
    gueltige_docs = [d for d in tage_docs if d.get("top_n_bewiesen", True) is not False]
    vor7, vor30 = await _tagesstat_vor(db, seg_id, t, 7), await _tagesstat_vor(db, seg_id, t, 30)
    t7_eur, t7_pct = _trend(heute.get("median_price"), (vor7 or {}).get("median_price"))
    t30_eur, t30_pct = _trend(heute.get("median_price"), (vor30 or {}).get("median_price"))
    b7_eur, b7_pct, b7_n = await _bestandstrend(db, seg_id, heute, vor7)
    b30_eur, b30_pct, b30_n = await _bestandstrend(db, seg_id, heute, vor30)
    letzte7 = [d for d in alle_docs if d["date"] > _tag_minus(t, 7)]
    mittel = (sum(int(d.get("sample_size") or 0) for d in gueltige_docs) / len(gueltige_docs)) if gueltige_docs else 0
    # Nr. 45 + Welle 5 Nr. 50: Abdeckung = gueltige Laeufe / erwartete Laeufe. Welle 6 Nr. 106/107: erwartet =
    # tatsaechlich geplante Jobs des Segments; nur ohne Plan (Altdaten, Tests) Kalendertage x Abrufe je Tag
    erster = min(d["date"] for d in alle_docs) if alle_docs else t
    kalendertage = max(1, (datetime.strptime(t, "%Y-%m-%d") - datetime.strptime(erster, "%Y-%m-%d")).days + 1)
    seg_doc = await db[SEGMENTE].find_one({"id": seg_id}, {"_id": 0, "crawls_per_day": 1, "max_items": 1}) or {}
    k = max(1, min(4, int(seg_doc.get("crawls_per_day") or 1)))
    gueltige_laeufe = sum(_gueltige_laeufe(d) for d in gueltige_docs)
    geplant = await geplante_laeufe(db, seg_id, t)
    erwartete_laeufe = geplant if geplant > 0 else kalendertage * k
    abdeckung = round(min(100.0, gueltige_laeufe / erwartete_laeufe * 100), 1) if erwartete_laeufe else 0.0
    unvollstaendig = heute.get("sample_incomplete") is True
    stat = {"segment_id": seg_id, "date": t, "model_id": heute.get("model_id"),
            **{k_: heute.get(k_) for k_ in ("sample_size", "min_price", "median_price", "avg_price", "max_price",
                                            "p25_price", "p75_price")},
            # Welle 6 Nr. 137: neutrale Namen (die Stichprobe hat N Zeilen, nicht 20); alte Namen bleiben parallel
            "median_top20_price": heute.get("median_price"), "median_sample_price": heute.get("median_price"),
            "avg_sample_price": heute.get("avg_price"), "max_sample_price": heute.get("max_price"),
            "sample_limit": int(seg_doc.get("max_items") or 0) or None,
            "trend_7d_eur": t7_eur, "trend_7d_pct": t7_pct, "trend_30d_eur": t30_eur, "trend_30d_pct": t30_pct,
            # Nr. 43/44: welcher Tag die Basis war (None = kein Datensatz in der Toleranz -> kein Trend)
            "trend_7d_basis_date": (vor7 or {}).get("date"), "trend_30d_basis_date": (vor30 or {}).get("date"),
            # Nr. 55: Bestandstrend (gleiche Autos an beiden Tagen)
            "trend_7d_bestand_eur": b7_eur, "trend_7d_bestand_pct": b7_pct, "anzahl_gemeinsam": b7_n,
            "trend_30d_bestand_eur": b30_eur, "trend_30d_bestand_pct": b30_pct, "anzahl_gemeinsam_30d": b30_n,
            "new_in_sample_today": heute.get("new_in_sample_today"), "price_reductions_today": heute.get("price_reductions_today"),
            "new_listings_7d": sum(int(d.get("new_in_sample_today") or 0) for d in letzte7),
            "price_reductions_7d": sum(int(d.get("price_reductions_today") or 0) for d in letzte7),
            "beobachtete_tage": len(gueltige_docs), "tage_mit_treffern": len(tage_docs), "tage_mit_lauf": len(alle_docs),
            "erste_beobachtung": erster, "kalendertage": kalendertage, "abdeckung_pct": abdeckung,
            "gueltige_laeufe": gueltige_laeufe, "erwartete_laeufe": erwartete_laeufe, "crawls_per_day": k,
            "erwartete_quelle": "plan" if geplant > 0 else "konfiguration",
            "mittlere_sample_groesse": round(mittel, 1),
            "datenlage": datenlage(len(gueltige_docs), mittel, abdeckung, rows=seg_doc.get("max_items"),
                                   sample_incomplete=unvollstaendig),
            "sample_incomplete": heute.get("sample_incomplete"),
            # Nr. 3 (nur noch Altdaten vor P1): Tage, die damals unsortiert gespeichert wurden —
            # seit P1 kommt ein unsortierter Lauf nie mehr in die Tagesstatistik (Job 'data_invalid')
            "sortierung_unsicher": heute.get("sorted_confirmed") is False,
            # Reparaturwelle 5 Nr. 1: der Lauf hinter den Hauptwerten hatte keine Positionsnummern
            "top_n_bewiesen": heute.get("top_n_bewiesen", True) is not False,
            "updated_at": konfig.jetzt_iso()}
    await db[SEGMENTSTATS].update_one({"_id": seg_id}, {"$set": stat}, upsert=True)
    return stat


# ---------------------------------------------------------------- Chancen
async def chancen_ableiten(db, segment: Dict[str, Any], heute: List[Dict[str, Any]],
                           vorher_stat: Optional[Dict[str, Any]], tag: str, jetzt_iso: str) -> int:
    """Regeln (ohne KI): neues Listing unter bisherigem Minimum / unter p25;
    starke Reduktion (>= Prozent oder >= Betrag); neu in die Top-N gefallen.
    Welle 6 Nr. 136: jede Chance traegt detected_price/detected_advantage (Stand beim Erkennen);
    der Leseweg rechnet current_price/current_advantage/still_valid dazu."""
    n = 0
    top_n = konfig.chance_top_n()
    red_pct, red_eur = konfig.chance_reduktion_pct(), konfig.chance_reduktion_eur()
    # Nr. 46: "unter bisherigem Minimum/p25" nur gegen einen hoechstens 3 Tage alten Stand —
    # ein Wochen alter Vergleichstag macht jedes neue Auto zur Schein-Chance
    vergleichbar = bool(vorher_stat and vorher_stat.get("sample_size")
                        and str(vorher_stat.get("date") or "") >= _tag_minus(tag, CHANCE_VERGLEICH_TAGE))
    for h in heute:
        l, preis = h["listing"], h["preis"]
        treffer: List[Tuple[str, Optional[float], str]] = []
        if h["neu_im_sample"] and vergleichbar:
            if vorher_stat.get("min_price") is not None and preis < float(vorher_stat["min_price"]):
                treffer.append(("neues_minimum", float(vorher_stat["min_price"]), "unter dem bisher günstigsten Angebot"))
            elif vorher_stat.get("p25_price") is not None and preis < float(vorher_stat["p25_price"]):
                treffer.append(("neu_guenstig", float(vorher_stat["p25_price"]), "neu und unter dem unteren Viertel (p25)"))
        d_eur, d_pct = h.get("delta_eur"), h.get("delta_pct")
        if d_eur is not None and d_eur < 0 and (abs(d_eur) >= red_eur or abs(d_pct or 0) >= red_pct):
            treffer.append(("stark_reduziert", preis - d_eur, "deutliche Preisreduzierung"))
        # Welle 5 Nr. 46: "neu in den Top-N" nur gegen einen hoechstens 3 Tage alten Rang (rang_vergleich)
        rang_alt = h.get("rang_vergleich") if "rang_vergleich" in h else h.get("rang_vorher")
        if h["rang"] <= top_n and rang_alt is not None and rang_alt > top_n:
            treffer.append((f"neu_top{top_n}", None, f"neu unter den {top_n} günstigsten"))
        for typ, referenz, text in treffer:
            diff = round(preis - referenz, 2) if referenz is not None else None
            # Welle 5 Nr. 45: Staerke der Chance (Betrag) — eine staerkere Chance desselben Tages
            # ueberschreibt die gespeicherte (z. B. zweite Reduktion am Abend)
            staerke = round(abs(diff), 2) if diff is not None else round(abs(float(d_eur or 0)), 2)
            felder = {"segment_id": segment["id"], "model_id": segment.get("model_id"), "label": segment.get("label"),
                      "km_label": segment.get("km_label"), "source": l["source"], "url": l.get("url"),
                      "title": l.get("title"), "price": preis, "referenz_eur": referenz, "differenz_eur": diff,
                      "differenz_pct": round(diff / referenz * 100, 2) if diff is not None and referenz else None,
                      # Nr. 136: Stand beim Erkennen — Vorteil = Referenz minus Preis (positiv = guenstiger)
                      "detected_price": preis, "detected_advantage": round(-diff, 2) if diff is not None else None,
                      "delta_eur": d_eur, "delta_pct": d_pct, "rang": h["rang"], "rang_vorher": h.get("rang_vorher"),
                      "rang_vergleich": h.get("rang_vergleich"), "wiederkehrer": bool(h.get("wiederkehrer")),
                      "mileage_km": l.get("mileage_km"), "first_registration": l.get("first_registration"),
                      "power_kw": l.get("power_kw"), "gearbox": l.get("gearbox"), "fuel": l.get("fuel"),
                      "city": l.get("city"), "postal_code": l.get("postal_code"),
                      "seller_type": l.get("seller_type"),
                      "price_rating": (l.get("price_rating") or {}).get("rating"),
                      "mobile_created_at": l.get("mobile_created_at"), "first_price": h.get("first_price"),
                      "text": text, "staerke_eur": staerke}
            # Welle 5 Nr. 44: Dedupe je Listing, Typ, Tag UND Segment (ueberlappende Auftraege)
            schluessel = {"listing_id": l["listing_id"], "typ": typ, "date": tag, "segment_id": segment["id"]}
            r = await _einmal_wiederholen(lambda: db[CHANCEN].update_one(
                schluessel, {"$setOnInsert": {"id": uuid.uuid4().hex, "created_at": jetzt_iso, **felder}}, upsert=True))
            if r.upserted_id is not None:
                n += 1
            else:
                await db[CHANCEN].update_one({**schluessel, "staerke_eur": {"$lt": staerke}},
                                             {"$set": {**felder, "updated_at": jetzt_iso}})
    return n
