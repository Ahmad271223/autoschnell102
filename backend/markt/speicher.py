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

Master-Auftrag 26.09.2026, Phase C (Tagesbasis):
  * Datenqualitaet (Lauf technisch verlaesslich?) getrennt von der Markttiefe (wie viele Angebote?)
    und der Vollstaendigkeit (UNKNOWN ohne verlaessliche Gesamttrefferzahl — nie raten)
  * Tagesdokument je Segment/Tag: Fassung (version, definition_hash), gueltige/ungueltige/leere Laeufe,
    Zeilen des Hauptlaufs (listing_id, Rang, Preis, Verkaeuferart), aus der Stichprobe gefallene Inserate,
    Preiserhoehungen (gleiche listing_id), Top-3/Top-5-Wechsel zum Vortag, Privat/Haendler, Crawl-Kosten
  * Trend-, Chancen- und Private-Deals-Basis nur Tage mit Datenqualitaet GOOD/MEDIUM
  * Dieses Modul liest und schreibt nur Tageswerte — es loest NIE einen Abruf aus (Architekturtest)

Master-Auftrag Phase D (27.09.2026): stellt ein Lauf die Hauptwerte des Tages, bekommt das Tagesdokument
hot_deals_offen=True — der Auswertungs-Worker (markt.deals / markt.auswertung) prueft die Hot Deals danach im
Hintergrund aus den gespeicherten Tageswerten.
"""
from __future__ import annotations

import statistics
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from markt import konfig
from markt.konfig import CHANCEN, JOBS, LISTINGS, PRIVATE_DEALS, SEGMENTE, SEGMENTSTATS, SNAPSHOTS, TAGESSTATS

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


# ---------------------------------------------------------------- Datenqualitaet / Markttiefe / Vollstaendigkeit (Phase C)
DATENQUALITAET = ("GOOD", "MEDIUM", "POOR", "UNKNOWN")
MARKTTIEFE = ("FULL", "NORMAL", "THIN", "EMPTY", "UNKNOWN")
VOLLSTAENDIGKEIT = ("COMPLETE", "INCOMPLETE", "UNKNOWN")
BASIS_QUALITAET = ("GOOD", "MEDIUM")          # nur solche Tage sind Trend-, Chancen- und Private-Deals-Basis
STALE_STUNDEN = 48                            # letzter gueltiger Lauf aelter -> POOR/stale ("veraltet")
TIEFE_NORMAL_ANTEIL = 0.6                     # NORMAL ab 60 % der bestellten Zeilen, darunter THIN


def _zeitpunkt(w: Any) -> Optional[datetime]:
    try:
        d = datetime.fromisoformat(str(w).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def data_quality_bewerten(*, gueltiger_lauf: bool, top_n_bewiesen: bool = True, geliefert: int = 0, verworfen_fremd: int = 0,
                          parser_fehler: int = 0, invalid_runs: int = 0, lauf_at: Any = None,
                          jetzt: Optional[datetime] = None) -> Tuple[str, str]:
    """(Stufe, Grund). GOOD = gueltiger Lauf, Top-N bewiesen, keine verworfenen Fremdfahrzeuge, keine Parserfehler,
    Lauf hoechstens 48 h alt. Fremdfahrzeuge/Parserfehler: > 0 -> MEDIUM, > 50 % der gelieferten Zeilen -> POOR;
    nur monoton sortiert -> MEDIUM; aelter als 48 h -> POOR ('stale'); kein gueltiger Lauf -> UNKNOWN, nur
    ungueltige Laeufe -> POOR ('ungueltig'). Ein leerer gueltiger Lauf ist GOOD (Markttiefe EMPTY)."""
    if not gueltiger_lauf:
        return ("POOR", "ungueltig") if int(invalid_runs or 0) > 0 else ("UNKNOWN", "kein_lauf")
    if lauf_at and jetzt is not None:
        z = _zeitpunkt(lauf_at)
        if z is not None and (jetzt - z) > timedelta(hours=STALE_STUNDEN):
            return "POOR", "stale"
    n = max(0, int(geliefert or 0))
    fremd, parser = max(0, int(verworfen_fremd or 0)), max(0, int(parser_fehler or 0))

    def _ueber_haelfte(x: int) -> bool:
        return x > 0 and (n == 0 or x * 2 > n)
    if _ueber_haelfte(fremd):
        return "POOR", "fremdfahrzeuge"
    if _ueber_haelfte(parser):
        return "POOR", "parser"
    if fremd:
        return "MEDIUM", "fremdfahrzeuge"
    if parser:
        return "MEDIUM", "parser"
    if not top_n_bewiesen:
        return "MEDIUM", "nur_monoton"
    return "GOOD", ""


def market_depth_bewerten(*, gueltiger_lauf: bool, sample_size: int, rows: int) -> str:
    """FULL (gueltige >= bestellte Zeilen), NORMAL (>= 60 %), THIN (1 .. < 60 %), EMPTY (0 bei gueltigem Lauf),
    UNKNOWN (kein gueltiger Lauf). Eine leere oder duenne Stichprobe ist eine Marktluecke, kein Fehler."""
    if not gueltiger_lauf:
        return "UNKNOWN"
    n, soll = int(sample_size or 0), max(1, int(rows or 1))
    if n <= 0:
        return "EMPTY"
    if n >= soll:
        return "FULL"
    return "NORMAL" if n >= soll * TIEFE_NORMAL_ANTEIL else "THIN"


def sample_completeness_bewerten(*, gueltiger_lauf: bool, sample_size: int, rows: int, markt_gesamt: Optional[int] = None) -> str:
    """COMPLETE: gueltige Zeilen >= bestellte (oder eine VERLAESSLICHE Gesamttrefferzahl zeigt, dass der ganze Markt
    in der Stichprobe steckt); INCOMPLETE: nur mit verlaesslicher Gesamtzahl > gelieferte; sonst UNKNOWN — ohne
    nachgewiesenes Scraper-Feld (normalisieren.MARKT_GESAMT_FELD) wird nie geraten."""
    if not gueltiger_lauf:
        return "UNKNOWN"
    n, soll = int(sample_size or 0), max(1, int(rows or 1))
    if n >= soll:
        return "COMPLETE"
    if markt_gesamt is not None:
        return "INCOMPLETE" if int(markt_gesamt) > n else "COMPLETE"
    return "UNKNOWN"


def datenlage_alias(data_quality: Optional[str], market_depth: Optional[str], basis: Optional[str], grund: str = "") -> str:
    """Die bisherige 'datenlage' bleibt als EIN Wert fuer alte Oberflaechen: technischer Fehler -> 'fehler' (rot),
    letzter gueltiger Lauf zu alt -> 'veraltet', duenner Markt -> 'duenn' (gelb), leerer Markt -> 'leer' (grau,
    Marktluecke ist kein Fehler), sonst die Einstufung nach Tagen/Abdeckung (gut/mittel/niedrig/unvollstaendig)."""
    if data_quality == "POOR":
        return "veraltet" if grund == "stale" else "fehler"
    if data_quality in (None, "", "UNKNOWN"):
        return "keine"
    if market_depth == "EMPTY":
        return "leer"
    if market_depth == "THIN":
        return "duenn"
    return basis or "keine"


def qualitaet_aus_doc(doc: Optional[Dict[str, Any]], rows: Optional[int] = None) -> Dict[str, Any]:
    """Qualitaetsfelder eines Tagesdokuments oder einer Segmentstatistik. Altdaten ohne die Felder werden aus
    sample_size / top_n_bewiesen abgeleitet (Vollstaendigkeit ohne Gesamtzahl: COMPLETE nur bei voller Stichprobe)."""
    doc = doc or {}
    soll = int(rows or doc.get("rows_soll") or doc.get("sample_limit") or 0) or konfig.rows_je_segment()
    gueltig = doc.get("sample_size") is not None
    if doc.get("data_quality"):
        dq, grund = str(doc["data_quality"]), str(doc.get("data_quality_grund") or "")
    else:
        nur_monoton = doc.get("top_n_bewiesen") is False or doc.get("sorted_confirmed") is False
        dq, grund = data_quality_bewerten(gueltiger_lauf=gueltig, top_n_bewiesen=not nur_monoton, invalid_runs=int(doc.get("invalid_runs") or 0))
    n = int(doc.get("sample_size") or 0)
    return {"data_quality": dq, "data_quality_grund": grund or None,
            "market_depth": doc.get("market_depth") or market_depth_bewerten(gueltiger_lauf=gueltig, sample_size=n, rows=soll),
            "sample_completeness": doc.get("sample_completeness") or sample_completeness_bewerten(gueltiger_lauf=gueltig, sample_size=n, rows=soll)}


def qualitaet_lesen(stat: Optional[Dict[str, Any]], jetzt: Optional[datetime] = None) -> Dict[str, Any]:
    """Lesewege (Karte, Modell, Segment): Qualitaet der Segmentstatistik MIT Frische zum Lesezeitpunkt — liegt
    der letzte gueltige Lauf ueber 48 h zurueck, ist sie POOR/stale ('veraltet'), auch wenn seitdem kein Lauf
    die Statistik neu gerechnet hat. Enthaelt die abgeleitete 'datenlage'."""
    stat = stat or {}
    if stat.get("sample_size") is None and not stat.get("data_quality"):
        return {"data_quality": "UNKNOWN", "data_quality_grund": "kein_lauf", "market_depth": "UNKNOWN",
                "sample_completeness": "UNKNOWN", "datenlage": "keine"}
    q = qualitaet_aus_doc(stat)
    lauf_at = stat.get("letzter_gueltiger_lauf_at")
    if q["data_quality"] in BASIS_QUALITAET and lauf_at:
        _, grund = data_quality_bewerten(gueltiger_lauf=True, lauf_at=lauf_at, jetzt=jetzt or konfig.jetzt())
        if grund == "stale":
            q.update({"data_quality": "POOR", "data_quality_grund": "stale"})
    basis = stat.get("datenlage_basis") or stat.get("datenlage") or "keine"
    q["datenlage"] = datenlage_alias(q["data_quality"], q["market_depth"], basis, q.get("data_quality_grund") or "")
    return q


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
    die echte Laufzeit. lauf_info (Nr. 114/143): actor_build, sample_incomplete, run_id am Lauf.
    Phase C: lauf_info zusaetzlich geliefert / verworfen_fremd / parser_fehler (Datenqualitaet), kosten_usd /
    rohe_rows (Crawl-Kosten des Tages), markt_gesamt (nur aus nachgewiesenem Feld). Ein Lauf mit Datenqualitaet
    POOR ueberschreibt die Hauptwerte eines besseren Laufs desselben Tages nicht und erzeugt weder Chancen noch
    Private Deals."""
    jetzt = beobachtet or konfig.jetzt()
    jetzt_iso = jetzt.isoformat()
    tag = tag or konfig.heute_tag(jetzt)
    lauf_schluessel = lauf_tag or tag
    info = dict(lauf_info or {})
    seg_id = segment["id"]
    model_id = segment.get("model_id")
    seg_doc = await db[SEGMENTE].find_one({"id": seg_id}, {"_id": 0, "last_run_at": 1, "max_items": 1, "version": 1,
                                                           "definition_hash": 1}) or {}
    rows_soll = int(segment.get("max_items") or seg_doc.get("max_items") or 0) or konfig.rows_je_segment()
    # Nr. 145: gab es fuer DIESES Segment schon einen neueren Lauf, bestimmt dieser 'in_letztem_lauf'
    seg_frisch = str(seg_doc.get("last_run_at") or "") <= jetzt_iso
    # Nr. 109: Vergleichsstand fuer Chancen nur ein Tag mit Treffern UND bewiesener Sortierung
    # Phase C: und nur ein Tag mit Datenqualitaet GOOD/MEDIUM (Altdaten ohne Feld zaehlen)
    vorher_stat = await db[TAGESSTATS].find_one({"segment_id": seg_id, "date": {"$lt": tag}, "sample_size": {"$gt": 0},
                                                 "top_n_bewiesen": {"$ne": False}, "data_quality": {"$nin": ["POOR", "UNKNOWN"]}},
                                                {"_id": 0}, sort=[("date", -1)])
    # Phase C: Vergleichstag fuer 'aus der Stichprobe gefallen' und Top-3/Top-5-Wechsel = letzter gueltiger Vortag
    vortag = await db[TAGESSTATS].find_one({"segment_id": seg_id, "date": {"$lt": tag}, "sample_size": {"$exists": True},
                                            "data_quality": {"$nin": ["POOR", "UNKNOWN"]}},
                                           {"_id": 0, "date": 1, "listing_ids": 1, "listing_ids_alle": 1}, sort=[("date", -1)])
    # Nr. 41/42: Tageswerte sind die VEREINIGUNG ueber alle Laeufe des Tages — die Listing-IDs
    # (neu im Sample / Preis gesenkt) stehen am Tagesaggregat und werden je Lauf ergaenzt
    heute_stat = await db[TAGESSTATS].find_one({"segment_id": seg_id, "date": tag},
                                               {"_id": 0, "new_in_sample_ids": 1, "price_reduced_ids": 1, "sample_size": 1,
                                                "price_increase_ids": 1, "listing_ids": 1, "listing_ids_alle": 1, "data_quality": 1}) or {}
    bisher_heute = int(heute_stat.get("sample_size") or 0)
    neu_ids = set(heute_stat.get("new_in_sample_ids") or [])
    reduziert_ids = set(heute_stat.get("price_reduced_ids") or [])
    erhoeht_ids = set(heute_stat.get("price_increase_ids") or [])
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
        if delta_eur is not None and delta_eur > 0:
            erhoeht_ids.add(l["listing_id"])            # Phase C: nur gleiche listing_id, gleicher Segmentverlauf
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
    # Ahmad 26.09.2026 abends: Rang unter den PRIVATangeboten dieses Laufs (1 = guenstigstes Privatangebot) —
    # Chancen gibt es nur noch fuer die PRIVATE_TOP_N guenstigsten Privatangebote (chance_erlaubt)
    privat_sortiert, _ = private_top_auswahl(heute, n=len(heute))
    privat_rang = {h["listing"]["listing_id"]: i for i, h in enumerate(privat_sortiert, 1)}
    for h in heute:
        h["privat_rang"] = privat_rang.get(h["listing"]["listing_id"])
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
    # Phase C: Datenqualitaet / Markttiefe / Vollstaendigkeit DIESES Laufs
    dq, dq_grund = data_quality_bewerten(gueltiger_lauf=True, top_n_bewiesen=bool(top_n_bewiesen),
                                         geliefert=int(info.get("geliefert") if info.get("geliefert") is not None else len(listings)),
                                         verworfen_fremd=int(info.get("verworfen_fremd") or 0), parser_fehler=int(info.get("parser_fehler") or 0))
    tiefe = market_depth_bewerten(gueltiger_lauf=True, sample_size=kz["sample_size"], rows=rows_soll)
    vollst = sample_completeness_bewerten(gueltiger_lauf=True, sample_size=kz["sample_size"], rows=rows_soll,
                                          markt_gesamt=info.get("markt_gesamt"))
    # P5: ein leerer zweiter Lauf zerstoert den guten Tageswert nicht; Phase C: ein POOR-Lauf ebenso wenig
    tageswert_behalten = bisher_heute > 0 and (leer or (dq == "POOR" and heute_stat.get("data_quality") != "POOR"))
    lauf_eintrag = {"at": jetzt_iso, "tag": lauf_schluessel, "sample_size": kz["sample_size"],
                    "min": kz["min_price"], "median": kz["median_price"], "avg": kz["avg_price"],
                    "max": kz["max_price"], "leer": leer, "top_n_bewiesen": bool(top_n_bewiesen),
                    "data_quality": dq, "data_quality_grund": dq_grund or None,
                    **{k: v for k, v in info.items() if k not in ("markt_gesamt",)}}
    # Phase C: Vereinigung der heute gesehenen Inserate, gegen den letzten gueltigen Vortag verglichen
    alle_heute = set(heute_stat.get("listing_ids_alle") or heute_stat.get("listing_ids") or []) | set(ids_heute)
    vortag_ids = set((vortag or {}).get("listing_ids_alle") or (vortag or {}).get("listing_ids") or [])
    verschwunden = sorted(vortag_ids - alle_heute) if vortag else []
    setzen: Dict[str, Any] = {"model_id": model_id, "last_run_at": jetzt_iso,
                              "version": int(segment.get("version") or seg_doc.get("version") or 1),
                              "definition_hash": segment.get("definition_hash") or seg_doc.get("definition_hash"),
                              "rows_soll": rows_soll, "listing_ids_alle": sorted(alle_heute),
                              "disappeared_ids": verschwunden[:200], "disappeared_count": len(verschwunden),
                              "vergleich_vortag": (vortag or {}).get("date"),
                              "price_increase_ids": sorted(erhoeht_ids), "price_increases_today": len(erhoeht_ids)}
    if not tageswert_behalten:
        vortag_top = list((vortag or {}).get("listing_ids") or [])
        privat_n = sum(1 for h in heute if str(h["listing"].get("seller_type") or "").upper() == "PRIVATE")
        haendler_n = sum(1 for h in heute if str(h["listing"].get("seller_type") or "").upper() == "DEALER")
        setzen.update({"data_quality": dq, "data_quality_grund": dq_grund or None, "market_depth": tiefe, "sample_completeness": vollst,
                       "listings": [{"listing_id": h["listing"]["listing_id"], "rank": h["rang"], "price": h["preis"],
                                     "seller_type": h["listing"].get("seller_type") or None} for h in heute],
                       "private_count": privat_n, "dealer_count": haendler_n,
                       "top3_changed": (set(ids_heute[:3]) != set(vortag_top[:3])) if vortag else None,
                       "top5_changed": (set(ids_heute[:5]) != set(vortag_top[:5])) if vortag else None})
        setzen.update({**kz, "observed_at": jetzt_iso, "new_in_sample_today": len(neu_ids),
                       "price_reductions_today": len(reduziert_ids),
                       "new_in_sample_ids": sorted(neu_ids), "price_reduced_ids": sorted(reduziert_ids),
                       "listing_ids": ids_heute,
                       # Nr. 1: Hauptwerte stammen aus diesem Lauf — Top-N-Nachweis mitschreiben
                       "top_n_bewiesen": bool(top_n_bewiesen), "lauf_tag": lauf_schluessel,
                       # Nr. 143/114: Vollstaendigkeit der Stichprobe und Build des Scrapers dieses Laufs
                       "sample_incomplete": info.get("sample_incomplete"), "actor_build": info.get("actor_build"),
                       # Phase D: der Tageswert hat sich geaendert -> der Auswertungs-Worker (markt.deals) prueft
                       # die Hot Deals dieses Tages im Hintergrund; der Crawl-Weg wartet nie darauf
                       "hot_deals_offen": True})
    await _einmal_wiederholen(lambda: db[TAGESSTATS].update_one(
        {"segment_id": seg_id, "date": tag},
        {"$set": setzen, "$push": {"laeufe": {"$each": [lauf_eintrag], "$slice": -LAEUFE_MAX}},
         "$inc": {"valid_runs": 1, "empty_runs": 1 if leer else 0, "crawl_cost_usd": round(float(info.get("kosten_usd") or 0), 4),
                  "crawl_rows": int(info.get("rohe_rows") or 0)},
         "$max": {"last_valid_run_at": jetzt_iso}},
        upsert=True))
    # Private Deals (Ahmad 26.09. abends): nur aus einem GUELTIGEN Lauf mit Zeilen und nur, wenn kein
    # neuerer Lauf dieses Segments bekannt ist — ein leerer Lauf laesst den alten Stand stehen (stale).
    # Phase C: nie aus einem Lauf mit Datenqualitaet POOR
    if not leer and seg_frisch and dq != "POOR":
        await private_deals_ableiten(db, segment, heute, kz, tag, jetzt_iso, lauf_schluessel)
    await segmentstatistik(db, seg_id, tag)
    # Welle 5 Nr. 51: last_success_at nur bei einem Lauf MIT Zeilen; ein leerer Lauf setzt last_empty_at
    # (der Stale-Monitor liest last_success_at — eine Marktluecke ist kein Erfolg)
    seg_setzen: Dict[str, Any] = {"last_sample_size": bisher_heute if tageswert_behalten else kz["sample_size"]}
    seg_setzen["last_empty_at" if leer else "last_success_at"] = jetzt_iso
    await db[SEGMENTE].update_one({"id": seg_id}, {"$set": seg_setzen, "$max": {"last_run_at": jetzt_iso}})
    # Phase C: Chancen nur aus Laeufen mit Datenqualitaet GOOD/MEDIUM
    zaehler["chancen"] = await chancen_ableiten(db, segment, heute, vorher_stat, tag, jetzt_iso) if dq != "POOR" else 0
    zaehler["sample_size"] = kz["sample_size"]
    zaehler["tageswert_behalten"] = tageswert_behalten
    zaehler.update({"data_quality": dq, "data_quality_grund": dq_grund or None, "market_depth": tiefe, "sample_completeness": vollst})
    return zaehler


async def ungueltig_vermerken(db, job: Dict[str, Any], grund: str, *, kosten: Optional[float] = None,
                              rohe_rows: Optional[int] = None) -> None:
    """Phase C: ein 'data_invalid'-Lauf (bezahlt, aber keine gueltige Statistik) zaehlt im Tagesdokument
    (invalid_runs, letzter Grund, Kosten/Zeilen) — die Hauptwerte eines gueltigen Laufs desselben Tages bleiben;
    gab es an dem Tag nur ungueltige Laeufe, steht das Tagesdokument ohne sample_size da (Datenqualitaet POOR,
    Markttiefe UNKNOWN) — das ist KEINE Marktluecke."""
    seg_id = job.get("segment_id")
    if not seg_id:
        return
    seg = await db[SEGMENTE].find_one({"id": seg_id}, {"_id": 0, "version": 1, "definition_hash": 1, "model_id": 1, "max_items": 1}) or {}
    tag = str(job.get("tag") or konfig.heute_tag()).split("#")[0]
    jetzt_iso = konfig.jetzt_iso()
    text = str(grund or "")[:200]
    await _einmal_wiederholen(lambda: db[TAGESSTATS].update_one(
        {"segment_id": seg_id, "date": tag},
        {"$inc": {"invalid_runs": 1, "crawl_cost_usd": round(float(kosten or 0), 4), "crawl_rows": int(rohe_rows or 0)},
         "$set": {"last_invalid_at": jetzt_iso, "last_invalid_grund": text},
         "$setOnInsert": {"model_id": seg.get("model_id") or job.get("model_id"), "version": int(seg.get("version") or 1),
                          "definition_hash": seg.get("definition_hash"), "rows_soll": int(seg.get("max_items") or 0) or None,
                          "valid_runs": 0, "empty_runs": 0, "data_quality": "POOR", "data_quality_grund": "ungueltig",
                          "market_depth": "UNKNOWN", "sample_completeness": "UNKNOWN"},
         "$push": {"laeufe": {"$each": [{"at": jetzt_iso, "tag": job.get("tag"), "ungueltig": True, "grund": text,
                                         "sample_size": 0, "leer": False}], "$slice": -LAEUFE_MAX}}},
        upsert=True))
    await segmentstatistik(db, seg_id)


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
    # Phase C: Trendbasis nur Tage mit Datenqualitaet GOOD/MEDIUM
    docs = await db[TAGESSTATS].find({"segment_id": seg_id, "date": {"$gte": von, "$lte": bis}, "sample_size": {"$gt": 0},
                                      "top_n_bewiesen": {"$ne": False}, "data_quality": {"$nin": ["POOR", "UNKNOWN"]}},
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
    datum_f = {"date": {"$lte": tag}} if tag else {}
    # Phase C: Hauptwerte vom letzten Tag MIT gueltigem Lauf (sample_size vorhanden) — ein Tag mit nur
    # ungueltigen Laeufen verdraengt die Werte nicht
    heute = await db[TAGESSTATS].find_one({"segment_id": seg_id, "sample_size": {"$exists": True}, **datum_f},
                                          {"_id": 0}, sort=[("date", -1)])
    letzter = await db[TAGESSTATS].find_one({"segment_id": seg_id, **datum_f}, {"_id": 0, "date": 1, "model_id": 1, "last_invalid_at": 1,
                                                                               "last_invalid_grund": 1, "sample_size": 1},
                                            sort=[("date", -1)])
    if not heute:
        if not letzter:
            return None
        # bisher nur ungueltige Laeufe: Qualitaet POOR, keine Kennzahlen (keine Karte), keine Marktluecke
        stat_u = {"segment_id": seg_id, "date": letzter["date"], "model_id": letzter.get("model_id"), "sample_size": None,
                  "data_quality": "POOR", "data_quality_grund": "ungueltig", "market_depth": "UNKNOWN",
                  "sample_completeness": "UNKNOWN", "datenlage": "fehler", "datenlage_basis": "keine", "beobachtete_tage": 0,
                  "letzter_ungueltiger_lauf_at": letzter.get("last_invalid_at"), "letzter_ungueltiger_grund": letzter.get("last_invalid_grund"),
                  "updated_at": konfig.jetzt_iso()}
        await db[SEGMENTSTATS].update_one({"_id": seg_id}, {"$set": stat_u}, upsert=True)
        return stat_u
    t = heute["date"]
    alle_docs = await db[TAGESSTATS].find({"segment_id": seg_id, "date": {"$lte": t}, "sample_size": {"$exists": True}},
                                          {"_id": 0, "date": 1, "sample_size": 1, "new_in_sample_today": 1,
                                           "price_reductions_today": 1, "top_n_bewiesen": 1, "laeufe": 1}).to_list(2000)
    nur_ungueltig = await db[TAGESSTATS].count_documents({"segment_id": seg_id, "sample_size": {"$exists": False}, **datum_f})
    tage_docs = [d for d in alle_docs if int(d.get("sample_size") or 0) > 0]
    # Welle 5 Nr. 49: beobachtet = nur Tage mit GUELTIGEM Lauf (Treffer und bewiesene Top-N-Sortierung);
    # leere oder nur monoton sortierte Tage zaehlen nicht (Nr. 54 aus Welle 3 damit zurueckgenommen)
    gueltige_docs = [d for d in tage_docs if d.get("top_n_bewiesen", True) is not False]
    seg_doc = await db[SEGMENTE].find_one({"id": seg_id}, {"_id": 0, "crawls_per_day": 1, "max_items": 1, "last_attempt_at": 1}) or {}
    rows_soll = int(seg_doc.get("max_items") or heute.get("rows_soll") or 0) or konfig.rows_je_segment()
    # Phase C: Qualitaet des Tages + Frische (letzter gueltiger Lauf > 48 h -> POOR/stale)
    q = qualitaet_aus_doc(heute, rows_soll)
    lauf_at = heute.get("last_valid_run_at") or heute.get("observed_at") or heute.get("last_run_at")
    if q["data_quality"] in BASIS_QUALITAET and lauf_at:
        _, g = data_quality_bewerten(gueltiger_lauf=True, lauf_at=lauf_at, jetzt=konfig.jetzt())
        if g == "stale":
            q.update({"data_quality": "POOR", "data_quality_grund": "stale"})
    # ein technisch schlechter Tag (Fremdfahrzeuge/Parser) ist keine Trendbasis — auch nicht als "heute"
    trend_ok = not (q["data_quality"] == "POOR" and q.get("data_quality_grund") != "stale")
    vor7, vor30 = (await _tagesstat_vor(db, seg_id, t, 7), await _tagesstat_vor(db, seg_id, t, 30)) if trend_ok else (None, None)
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
    k = max(1, min(4, int(seg_doc.get("crawls_per_day") or 1)))
    gueltige_laeufe = sum(_gueltige_laeufe(d) for d in gueltige_docs)
    geplant = await geplante_laeufe(db, seg_id, t)
    erwartete_laeufe = geplant if geplant > 0 else kalendertage * k
    abdeckung = round(min(100.0, gueltige_laeufe / erwartete_laeufe * 100), 1) if erwartete_laeufe else 0.0
    # Phase C: 'unvollstaendig' nur noch aus einer VERLAESSLICHEN Gesamtzahl (sample_completeness INCOMPLETE) —
    # das alte sample_incomplete beruhte auf geratenen Feldnamen und zaehlt nicht mehr
    unvollstaendig = q["sample_completeness"] == "INCOMPLETE"
    basis = datenlage(len(gueltige_docs), mittel, abdeckung, rows=seg_doc.get("max_items"), sample_incomplete=unvollstaendig)
    # Private Deals: der Stand der Privat-Top-3 kommt aus dem letzten GUELTIGEN Lauf mit Zeilen; gab es danach
    # einen Lauf ohne Zeilen (oder einen Versuch, der nichts speicherte), ist der Stand 'stale'
    alt_stat = await db[SEGMENTSTATS].find_one({"_id": seg_id}, {"_id": 0, "private_stand_at": 1}) or {}
    stale = private_stand_stale(alt_stat.get("private_stand_at"), heute.get("last_run_at"), seg_doc.get("last_attempt_at"))
    stat = {"segment_id": seg_id, "date": t, "model_id": heute.get("model_id"),
            "private_stand_stale": stale,
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
            # Phase C: Datenqualitaet, Markttiefe, Vollstaendigkeit getrennt; 'datenlage' bleibt als Alias daraus
            **q, "datenlage_basis": basis,
            "datenlage": datenlage_alias(q["data_quality"], q["market_depth"], basis, q.get("data_quality_grund") or ""),
            "letzter_gueltiger_lauf_at": lauf_at, "rows_soll": rows_soll, "tage_nur_ungueltig": nur_ungueltig,
            "letzter_ungueltiger_lauf_at": (letzter or {}).get("last_invalid_at"),
            "letzter_ungueltiger_grund": (letzter or {}).get("last_invalid_grund"),
            "valid_runs_heute": int(heute.get("valid_runs") or 0), "invalid_runs_heute": int(heute.get("invalid_runs") or 0),
            "disappeared_count": heute.get("disappeared_count"), "price_increases_today": heute.get("price_increases_today"),
            "top3_changed": heute.get("top3_changed"), "top5_changed": heute.get("top5_changed"),
            "private_count": heute.get("private_count"), "dealer_count": heute.get("dealer_count"),
            "crawl_cost_usd_heute": heute.get("crawl_cost_usd"),
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
def chance_erlaubt(h: Dict[str, Any]) -> bool:
    """Ahmad 26.09.2026 abends: Chancen NUR fuer Privatangebote, die in diesem Lauf zu den PRIVATE_TOP_N (3)
    guenstigsten Privatangeboten ihres Segments gehoeren — reduzierte, aber immer noch teure Haendlerautos
    (z. B. Platz 4/5 der guenstigsten) sind keine Chance. Dieselbe Auswahl wie bei den Private Deals."""
    l = h.get("listing") or {}
    rang = h.get("privat_rang")
    return (str(l.get("seller_type") or "").strip().upper() == "PRIVATE" and rang is not None
            and 1 <= int(rang) <= konfig.PRIVATE_TOP_N)


async def chancen_ableiten(db, segment: Dict[str, Any], heute: List[Dict[str, Any]],
                           vorher_stat: Optional[Dict[str, Any]], tag: str, jetzt_iso: str) -> int:
    """Regeln (ohne KI): neues Listing unter bisherigem Minimum / unter p25;
    starke Reduktion (>= Prozent oder >= Betrag); neu in die Top-N gefallen.
    Welle 6 Nr. 136: jede Chance traegt detected_price/detected_advantage (Stand beim Erkennen);
    der Leseweg rechnet current_price/current_advantage/still_valid dazu.
    Ahmad 26.09.2026 abends: nur Privatangebote unter den 3 guenstigsten Privatangeboten des Laufs
    (chance_erlaubt); der Privat-Rang steht an der Chance (privat_rang)."""
    n = 0
    top_n = konfig.chance_top_n()
    red_pct, red_eur = konfig.chance_reduktion_pct(), konfig.chance_reduktion_eur()
    # Nr. 46: "unter bisherigem Minimum/p25" nur gegen einen hoechstens 3 Tage alten Stand —
    # ein Wochen alter Vergleichstag macht jedes neue Auto zur Schein-Chance
    vergleichbar = bool(vorher_stat and vorher_stat.get("sample_size")
                        and str(vorher_stat.get("date") or "") >= _tag_minus(tag, CHANCE_VERGLEICH_TAGE))
    for h in heute:
        if not chance_erlaubt(h):
            continue
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
                      "seller_type": l.get("seller_type"), "privat_rang": h.get("privat_rang"),
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


# ---------------------------------------------------------------- Private Deals (Ahmad 26.09.2026 abends)
# Die 3 guenstigsten PRIVATangebote je Segment — ausschliesslich aus den Zeilen, die die Marktanalyse
# ohnehin je Lauf abruft (die N guenstigsten). Kein eigener Crawl, keine neuen Segmente/Jobs/Kosten.
# Nur der Super-Admin liest sie (routes/markt_admin); nichts davon landet in den Chancen der Firmen
# oder in der Fahrzeugkarte. KEINE PII: keine seller_*-Felder, keine Koordinaten — nur Ort/PLZ.
PRIVAT_FAHRZEUGFELDER = ("title", "make", "model", "variant", "first_registration", "mileage_km", "power_kw", "fuel",
                         "gearbox", "category", "city", "postal_code", "url", "mobile_created_at", "mobile_modified_at",
                         "mobile_renewed_at", "price_rating")


def _ez_jahr(wert: Any) -> Optional[int]:
    import re
    m = re.search(r"(\d{4})", str(wert or ""))
    return int(m.group(1)) if m else None


def private_stand_stale(stand_at: Optional[str], letzter_lauf_at: Optional[str], letzter_versuch_at: Optional[str]) -> bool:
    """Der Privat-Stand ist veraltet, wenn nach dem letzten gueltigen Lauf (stand_at) noch ein Lauf ohne
    Zeilen (letzter_lauf_at, Tagesstatistik) oder ein Versuch ohne Speicherung (letzter_versuch_at am
    Segment, z. B. data_invalid) lag. Ohne Stand: nicht 'stale', sondern schlicht kein Stand."""
    if not stand_at:
        return False
    s = str(stand_at)
    return any(str(x) > s for x in (letzter_lauf_at, letzter_versuch_at) if x)


def _privat_fahrzeug(l: Dict[str, Any]) -> Dict[str, Any]:
    raus = {k: l.get(k) for k in PRIVAT_FAHRZEUGFELDER}
    pr = raus.get("price_rating")
    raus["price_rating"] = pr.get("rating") if isinstance(pr, dict) else pr
    raus["ez_year"] = _ez_jahr(raus.get("first_registration"))
    return raus


def private_top_auswahl(heute: List[Dict[str, Any]], n: int = konfig.PRIVATE_TOP_N) -> Tuple[List[Dict[str, Any]], int]:
    """(die n guenstigsten Privatangebote des Laufs in Preisreihenfolge, Anzahl Privatangebote im Sample).
    seller_type muss ausdruecklich 'PRIVATE' sein — unbekannt/leer/DEALER zaehlt nie."""
    privat = [h for h in heute if str((h.get("listing") or {}).get("seller_type") or "").strip().upper() == "PRIVATE"]
    privat.sort(key=lambda h: (float(h["preis"]), int(h.get("rang") or 0)))
    return privat[:max(0, int(n))], len(privat)


async def private_deals_ableiten(db, segment: Dict[str, Any], heute: List[Dict[str, Any]], kz: Dict[str, Any],
                                 tag: str, jetzt_iso: str, lauf_schluessel: str) -> Dict[str, Any]:
    """Nach einem gueltigen Lauf: Privat-Top-3 des Segments fortschreiben (ein Dokument je Segment+Listing).
    Wer herausfaellt, bleibt als Historie (currently_top3=False, left_top3_at); eine Preisaenderung eines
    Top-3-Autos landet in price_history_top3 (hoechstens PRIVATE_PREISVERLAUF_MAX Eintraege).
    Die Segmentstatistik bekommt private_top3 / private_anzahl_im_sample / private_stand_at."""
    seg_id = segment["id"]
    top, anzahl = private_top_auswahl(heute)
    median = kz.get("median_price")
    ids = [h["listing"]["listing_id"] for h in top]
    vorher: Dict[str, Dict[str, Any]] = {}
    if ids:
        async for d in db[PRIVATE_DEALS].find({"segment_id": seg_id, "listing_id": {"$in": ids}}, {"_id": 0}):
            vorher[str(d["listing_id"])] = d
    zusammenfassung = []
    for i, h in enumerate(top, 1):
        l, preis = h["listing"], float(h["preis"])
        alt = vorher.get(l["listing_id"])
        alt_preis = float(alt.get("current_price") or 0) if alt else 0.0
        diff = round(preis - float(median), 2) if median else None
        pct = round(diff / float(median) * 100, 2) if (diff is not None and median) else None
        erster = float(alt.get("first_price_top3")) if alt and alt.get("first_price_top3") is not None else preis
        setzen: Dict[str, Any] = {**_privat_fahrzeug(l), "model_id": segment.get("model_id"), "version": int(segment.get("version") or 1),
                                  "source": l["source"], "current_rank_private": i, "current_price": preis,
                                  "last_seen_top3_at": jetzt_iso, "currently_top3": True, "segment_median": median,
                                  "difference_to_segment_median_eur": diff, "difference_to_segment_median_pct": pct,
                                  "rank_in_sample": int(h.get("rang") or 0), "observed_at": jetzt_iso, "run_tag": lauf_schluessel,
                                  "price_change_since_first_eur": round(preis - erster, 2), "updated_at": jetzt_iso,
                                  "segment_label": segment.get("label"), "km_label": segment.get("km_label"), "ez_label": segment.get("ez_label")}
        aenderung: Dict[str, Any] = {
            "$setOnInsert": {"id": uuid.uuid4().hex, "first_price_top3": preis, "first_entered_top3_at": jetzt_iso,
                             "first_entered_top3_tag": tag, "created_at": jetzt_iso, "price_history_top3": [{"at": jetzt_iso, "price": preis}]},
            "$set": setzen, "$min": {"best_rank_private": i, "lowest_price_seen": preis},
            "$unset": {"left_top3_at": ""}}
        if alt and alt_preis and alt_preis != preis:
            # $setOnInsert und $push auf demselben Pfad kollidieren (auch ohne Insert) — Verlauf nur anhaengen
            aenderung["$setOnInsert"].pop("price_history_top3", None)
            aenderung["$push"] = {"price_history_top3": {"$each": [{"at": jetzt_iso, "price": preis}], "$slice": -konfig.PRIVATE_PREISVERLAUF_MAX}}
            setzen["last_price_change_at"] = jetzt_iso
            setzen["last_price_change_eur"] = round(preis - alt_preis, 2)
            if preis < alt_preis:
                setzen["last_reduced_at"], setzen["last_reduced_tag"] = jetzt_iso, tag
        if alt and alt.get("currently_top3") is False:
            setzen["reentered_top3_at"] = jetzt_iso
        schl = {"segment_id": seg_id, "listing_id": l["listing_id"]}
        await _einmal_wiederholen(lambda: db[PRIVATE_DEALS].update_one(schl, aenderung, upsert=True))
        zusammenfassung.append({"listing_id": l["listing_id"], "price": preis, "rank_private": i})
    # herausgefallen: bleibt als Historie erhalten
    await db[PRIVATE_DEALS].update_many({"segment_id": seg_id, "currently_top3": True, "listing_id": {"$nin": ids}},
                                        {"$set": {"currently_top3": False, "current_rank_private": None, "left_top3_at": jetzt_iso,
                                                  "updated_at": jetzt_iso}})
    await db[SEGMENTSTATS].update_one({"_id": seg_id}, {"$set": {"segment_id": seg_id, "private_top3": zusammenfassung,
                                                                 "private_anzahl_im_sample": anzahl, "private_stand_at": jetzt_iso,
                                                                 "private_stand_lauf": lauf_schluessel, "private_stand_tag": tag,
                                                                 "private_stand_sample_size": int(kz.get("sample_size") or 0)}}, upsert=True)
    return {"top3": zusammenfassung, "anzahl": anzahl}
