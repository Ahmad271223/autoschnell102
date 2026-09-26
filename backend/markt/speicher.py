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
"""
from __future__ import annotations

import statistics
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from pymongo import ReturnDocument

from markt import konfig
from markt.konfig import CHANCEN, LISTINGS, SEGMENTE, SEGMENTSTATS, SNAPSHOTS, TAGESSTATS

ZUSTAENDE = ("seen", "not_seen_in_sample", "verification_pending", "confirmed_removed")
LISTING_FELDER = ("url", "make", "model", "variant", "title", "category", "first_registration", "mileage_km",
                  "power_kw", "power_ps", "fuel", "gearbox", "hu", "color", "doors", "seats", "condition",
                  "price_net", "vat", "price_rating", "seller_type", "seller_id", "postal_code", "city",
                  "country", "latitude", "longitude", "mobile_created_at", "mobile_modified_at",
                  "mobile_renewed_at", "mobile_scraped_at", "image", "num_images", "make_id", "model_id", "hsn", "tsn",
                  "previous_owners")


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


def datenlage(tage: int, mittlere_groesse: float) -> str:
    """<7 Tage niedrig; 7-29 mittel; >=30 Tage und regelmaessig 15-20 Listings gut."""
    if tage < 7:
        return "niedrig"
    if tage >= 30 and mittlere_groesse >= 15:
        return "gut"
    return "mittel"


# ---------------------------------------------------------------- Verarbeitung
LAEUFE_MAX = 4      # Nr. 16/17: hoechstens 4 Laeufe je Tag am Snapshot / Tagesaggregat (crawls_per_day <= 4)


async def verarbeiten(db, segment: Dict[str, Any], listings: List[Dict[str, Any]], *,
                      beobachtet: Optional[datetime] = None, sortiert_bestaetigt: bool = True,
                      lauf_tag: Optional[str] = None) -> Dict[str, Any]:
    """Ein Tages-Sample (Preis aufsteigend) eines Segments einarbeiten.

    sortiert_bestaetigt=False (Review 26.09.2026 Nr. 3): die Zeilen sind dann nicht
    sicher "die guenstigsten" — Tagesaggregat und Segmentstatistik werden als unsicher
    markiert, Chancen werden NICHT abgeleitet.
    lauf_tag (Nr. 16/17): Schluessel des Laufs (z. B. '2026-09-26#2') — bei mehreren
    Abrufen je Tag bleibt jeder Lauf in 'laeufe' erhalten, die Hauptfelder zeigen den letzten."""
    jetzt = beobachtet or konfig.jetzt()
    jetzt_iso = jetzt.isoformat()
    tag = konfig.heute_tag(jetzt)
    lauf_schluessel = lauf_tag or tag
    seg_id = segment["id"]
    model_id = segment.get("model_id")
    vorher_stat = await db[TAGESSTATS].find_one({"segment_id": seg_id, "date": {"$lt": tag}}, {"_id": 0},
                                                sort=[("date", -1)])
    zaehler = {"neu_gesamt": 0, "neu_im_sample": 0, "preis_gesunken": 0, "preis_gestiegen": 0, "chancen": 0}
    heute: List[Dict[str, Any]] = []
    for rang, l in enumerate(listings, 1):
        preis = float(l["price_gross"])
        felder = {k: l.get(k) for k in LISTING_FELDER}
        vorher = await db[LISTINGS].find_one_and_update(
            {"source": l["source"], "listing_id": l["listing_id"]},
            {"$setOnInsert": {"first_seen_at": jetzt_iso, "first_price": preis, "first_segment_id": seg_id,
                              "price_changes": 0, "price_reductions": 0,
                              "price_history": [{"at": jetzt_iso, "price": preis}], "created_at": jetzt_iso},
             "$set": {**felder, "last_seen_at": jetzt_iso, "last_seen_tag": tag, "current_price": preis,
                      "active_state": "seen", "last_segment_id": seg_id, "model_id": model_id, "last_rank": rang,
                      "updated_at": jetzt_iso},
             # Nr. 14/15: ein Inserat kann in mehreren Segmenten stehen (Automatik-Auftrag
             # und ueberlappender eigener Auftrag) — alle Segmente/Modelle merken
             "$addToSet": {"segment_ids": seg_id, **({"model_ids": model_id} if model_id else {})},
             "$unset": {"not_seen_since": ""}},
            upsert=True, return_document=ReturnDocument.BEFORE)
        delta_eur: Optional[float] = None
        delta_pct: Optional[float] = None
        if vorher is None:
            zaehler["neu_gesamt"] += 1
        else:
            alt = float(vorher.get("current_price") or 0)
            if alt and alt != preis:
                delta_eur = round(preis - alt, 2)
                delta_pct = round(delta_eur / alt * 100, 2)
                await db[LISTINGS].update_one(
                    {"source": l["source"], "listing_id": l["listing_id"]},
                    {"$push": {"price_history": {"$each": [{"at": jetzt_iso, "price": preis}], "$slice": -120}},
                     "$inc": {"price_changes": 1, "price_reductions": 1 if delta_eur < 0 else 0},
                     "$set": {"last_price_change_at": jetzt_iso}})
                zaehler["preis_gesunken" if delta_eur < 0 else "preis_gestiegen"] += 1
        letzter_snap = await db[SNAPSHOTS].find_one({"listing_id": l["listing_id"], "segment_id": seg_id,
                                                     "date": {"$lt": tag}}, {"_id": 0, "rank_in_sample": 1, "date": 1},
                                                    sort=[("date", -1)])
        neu_im_sample = letzter_snap is None
        if neu_im_sample:
            zaehler["neu_im_sample"] += 1
        await db[SNAPSHOTS].update_one(
            {"listing_id": l["listing_id"], "segment_id": seg_id, "date": tag},
            {"$set": {"observed_at": jetzt_iso, "price": preis,
                      "price_rating": (l.get("price_rating") or {}).get("rating"),
                      "mobile_modified_at": l.get("mobile_modified_at"), "mobile_renewed_at": l.get("mobile_renewed_at"),
                      "rank_in_sample": rang, "price_change_eur": delta_eur, "price_change_pct": delta_pct,
                      "new_in_sample": neu_im_sample, "rank_yesterday": (letzter_snap or {}).get("rank_in_sample"),
                      "model_id": model_id, "source": l["source"]},
             # Nr. 16: jeder Lauf des Tages bleibt erhalten (Hauptfelder = letzter Lauf)
             "$push": {"laeufe": {"$each": [{"at": jetzt_iso, "price": preis, "rank_in_sample": rang, "tag": lauf_schluessel}],
                                  "$slice": -LAEUFE_MAX}}},
            upsert=True)
        heute.append({"listing": l, "preis": preis, "rang": rang, "neu_im_sample": neu_im_sample,
                      "rang_vorher": (letzter_snap or {}).get("rank_in_sample"), "delta_eur": delta_eur,
                      "delta_pct": delta_pct, "neu_gesamt": vorher is None,
                      "first_price": (vorher or {}).get("first_price", preis)})
    # nicht mehr im Sample dieses Segments: KEIN Verkauf. Nr. 15: NUR Listings, die heute
    # nirgendwo gesehen wurden (last_seen_tag < heute) — ein Inserat, das heute in einem
    # anderen, ueberlappenden Segment auftauchte, bleibt "seen".
    await db[LISTINGS].update_many(
        {"$or": [{"last_segment_id": seg_id}, {"segment_ids": seg_id}],
         "active_state": "seen", "last_seen_tag": {"$lt": tag}},
        {"$set": {"active_state": "not_seen_in_sample", "not_seen_since": jetzt_iso, "updated_at": jetzt_iso}})
    # Tagesaggregat (Nr. 17: jeder Lauf des Tages in 'laeufe', Hauptfelder = letzter Lauf)
    kz = kennzahlen([h["preis"] for h in heute])
    reduktionen = sum(1 for h in heute if (h["delta_eur"] or 0) < 0)
    await db[TAGESSTATS].update_one(
        {"segment_id": seg_id, "date": tag},
        {"$set": {**kz, "model_id": model_id, "observed_at": jetzt_iso, "new_in_sample_today": zaehler["neu_im_sample"],
                  "price_reductions_today": reduktionen, "sorted_confirmed": bool(sortiert_bestaetigt),
                  "listing_ids": [h["listing"]["listing_id"] for h in heute]},
         "$push": {"laeufe": {"$each": [{"at": jetzt_iso, "tag": lauf_schluessel, "sample_size": kz["sample_size"],
                                         "min": kz["min_price"], "median": kz["median_price"], "avg": kz["avg_price"],
                                         "max": kz["max_price"], "sorted_confirmed": bool(sortiert_bestaetigt)}],
                              "$slice": -LAEUFE_MAX}}},
        upsert=True)
    await segmentstatistik(db, seg_id, tag)
    await db[SEGMENTE].update_one({"id": seg_id}, {"$set": {"last_success_at": jetzt_iso, "last_sample_size": kz["sample_size"]}})
    # Nr. 3: aus einem unsicher sortierten Sample werden KEINE Chancen abgeleitet
    zaehler["chancen"] = (await chancen_ableiten(db, segment, heute, vorher_stat, tag, jetzt_iso)
                          if sortiert_bestaetigt else 0)
    zaehler["sample_size"] = kz["sample_size"]
    zaehler["sortierung_unsicher"] = not sortiert_bestaetigt
    return zaehler


# ---------------------------------------------------------------- Segmentstatistik
async def _tagesstat_vor(db, seg_id: str, tag: str, tage: int) -> Optional[Dict[str, Any]]:
    ziel = _tag_minus(tag, tage)
    return await db[TAGESSTATS].find_one({"segment_id": seg_id, "date": {"$lte": ziel}, "sample_size": {"$gt": 0}},
                                         {"_id": 0}, sort=[("date", -1)])


def _trend(aktuell: Optional[float], alt: Optional[float]) -> Tuple[Optional[float], Optional[float]]:
    if aktuell is None or alt is None or not alt:
        return None, None
    d = round(float(aktuell) - float(alt), 2)
    return d, round(d / float(alt) * 100, 2)


async def segmentstatistik(db, seg_id: str, tag: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Aktuellen Stand + Trends aus den Tagesaggregaten (nie aus Snapshots) neu rechnen."""
    heute = await db[TAGESSTATS].find_one({"segment_id": seg_id, **({"date": {"$lte": tag}} if tag else {})},
                                          {"_id": 0}, sort=[("date", -1)])
    if not heute:
        return None
    t = heute["date"]
    tage_docs = await db[TAGESSTATS].find({"segment_id": seg_id, "date": {"$lte": t}, "sample_size": {"$gt": 0}},
                                          {"_id": 0, "date": 1, "sample_size": 1, "new_in_sample_today": 1,
                                           "price_reductions_today": 1}).to_list(2000)
    vor7, vor30 = await _tagesstat_vor(db, seg_id, t, 7), await _tagesstat_vor(db, seg_id, t, 30)
    t7_eur, t7_pct = _trend(heute.get("median_price"), (vor7 or {}).get("median_price"))
    t30_eur, t30_pct = _trend(heute.get("median_price"), (vor30 or {}).get("median_price"))
    letzte7 = [d for d in tage_docs if d["date"] > _tag_minus(t, 7)]
    mittel = (sum(d["sample_size"] for d in tage_docs) / len(tage_docs)) if tage_docs else 0
    stat = {"segment_id": seg_id, "date": t, "model_id": heute.get("model_id"),
            **{k: heute.get(k) for k in ("sample_size", "min_price", "median_price", "avg_price", "max_price",
                                         "p25_price", "p75_price")},
            "median_top20_price": heute.get("median_price"),
            "trend_7d_eur": t7_eur, "trend_7d_pct": t7_pct, "trend_30d_eur": t30_eur, "trend_30d_pct": t30_pct,
            "new_in_sample_today": heute.get("new_in_sample_today"), "price_reductions_today": heute.get("price_reductions_today"),
            "new_listings_7d": sum(int(d.get("new_in_sample_today") or 0) for d in letzte7),
            "price_reductions_7d": sum(int(d.get("price_reductions_today") or 0) for d in letzte7),
            "beobachtete_tage": len(tage_docs), "mittlere_sample_groesse": round(mittel, 1),
            "datenlage": datenlage(len(tage_docs), mittel),
            # Nr. 3: letzter Lauf nicht sicher preis-aufsteigend -> Lesewege zeigen es an
            "sortierung_unsicher": heute.get("sorted_confirmed") is False,
            "updated_at": konfig.jetzt_iso()}
    await db[SEGMENTSTATS].update_one({"_id": seg_id}, {"$set": stat}, upsert=True)
    return stat


# ---------------------------------------------------------------- Chancen
async def chancen_ableiten(db, segment: Dict[str, Any], heute: List[Dict[str, Any]],
                           vorher_stat: Optional[Dict[str, Any]], tag: str, jetzt_iso: str) -> int:
    """Regeln (ohne KI): neues Listing unter bisherigem Minimum / unter p25;
    starke Reduktion (>= Prozent oder >= Betrag); neu in die Top-N gefallen."""
    n = 0
    top_n = konfig.chance_top_n()
    red_pct, red_eur = konfig.chance_reduktion_pct(), konfig.chance_reduktion_eur()
    for h in heute:
        l, preis = h["listing"], h["preis"]
        treffer: List[Tuple[str, Optional[float], str]] = []
        if h["neu_im_sample"] and vorher_stat and vorher_stat.get("sample_size"):
            if vorher_stat.get("min_price") is not None and preis < float(vorher_stat["min_price"]):
                treffer.append(("neues_minimum", float(vorher_stat["min_price"]), "unter dem bisher günstigsten Angebot"))
            elif vorher_stat.get("p25_price") is not None and preis < float(vorher_stat["p25_price"]):
                treffer.append(("neu_guenstig", float(vorher_stat["p25_price"]), "neu und unter dem unteren Viertel (p25)"))
        d_eur, d_pct = h.get("delta_eur"), h.get("delta_pct")
        if d_eur is not None and d_eur < 0 and (abs(d_eur) >= red_eur or abs(d_pct or 0) >= red_pct):
            treffer.append(("stark_reduziert", preis - d_eur, "deutliche Preisreduzierung"))
        if h["rang"] <= top_n and h.get("rang_vorher") is not None and h["rang_vorher"] > top_n:
            treffer.append((f"neu_top{top_n}", None, f"neu unter den {top_n} günstigsten"))
        for typ, referenz, text in treffer:
            diff = round(preis - referenz, 2) if referenz is not None else None
            r = await db[CHANCEN].update_one(
                {"listing_id": l["listing_id"], "typ": typ, "date": tag},
                {"$setOnInsert": {"id": uuid.uuid4().hex, "created_at": jetzt_iso, "segment_id": segment["id"],
                                  "model_id": segment.get("model_id"), "label": segment.get("label"),
                                  "km_label": segment.get("km_label"), "source": l["source"], "url": l.get("url"),
                                  "title": l.get("title"), "price": preis, "referenz_eur": referenz,
                                  "differenz_eur": diff,
                                  "differenz_pct": round(diff / referenz * 100, 2) if diff is not None and referenz else None,
                                  "delta_eur": d_eur, "delta_pct": d_pct, "rang": h["rang"], "rang_vorher": h.get("rang_vorher"),
                                  "mileage_km": l.get("mileage_km"), "first_registration": l.get("first_registration"),
                                  "power_kw": l.get("power_kw"), "gearbox": l.get("gearbox"), "fuel": l.get("fuel"),
                                  "city": l.get("city"), "postal_code": l.get("postal_code"),
                                  "seller_type": l.get("seller_type"),
                                  "price_rating": (l.get("price_rating") or {}).get("rating"),
                                  "mobile_created_at": l.get("mobile_created_at"), "first_price": h.get("first_price"),
                                  "text": text}},
                upsert=True)
            if r.upserted_id is not None:
                n += 1
    return n
