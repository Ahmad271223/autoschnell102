# -*- coding: utf-8 -*-
"""Kontextpaket VOR dem KI-Aufruf (Umbau 26.09.2026, Wunsch Ahmad: die KI
soll nichts mehr ermitteln, nur noch einen sauber vorbereiteten Fall
wirtschaftlich bewerten).

Alle Teile kommen aus eigenen Daten, parallel und mit hartem Zeitlimit
(KONTEXT_ZEITLIMIT_S): fehlt einer, rechnet die KI mit dem Rest und die
Datenlage sinkt.

  * marktvergleich    — vergleichbare Fahrzeuge aus unserem Fahrzeugpool und
                        Inserats-Zwischenspeicher (nur Statistik, kein Inserat)
  * reparaturreferenz — je Schaden Reparaturweg + low/median/high: eigene
                        Preisdatenbank (ki_reparaturpreise) vor monatlicher
                        Markttabelle (ai.marktdaten) vor Startwerten (preisbasis)
  * historie          — aehnliche eigene Faelle (ki_lernfaelle)
  * vorberechnet      — Summe der Referenz-Mediane, Anteil am Preis
  * datenlage         — hoch / mittel / niedrig, deterministisch
"""
from __future__ import annotations

import asyncio
import logging
import re
import statistics
import time
from typing import Any, Dict, List, Optional

from deps import db as _db
from konfig import kommazahl_env

from ai import preisbasis

log = logging.getLogger("autohandel.ki")

KONTEXT_ZEITLIMIT_S = kommazahl_env("KI_KONTEXT_ZEITLIMIT_SEKUNDEN", 2.0, unten=0.5, oben=10.0)
MARKT_CACHE_S = 6 * 3600
# Review 26.09.2026 (Nr. 32): nur Vergleichsdaten, die hoechstens so alt sind
MARKT_MAX_TAGE = 120
# Leistung +-15 % kW (nur wenn beide bekannt), Kilometer +-40 %, EZ +-2 Jahre
MARKT_KW_TOLERANZ = 0.15
_markt_cache: Dict[str, Any] = {}


def _zahl(w) -> Optional[float]:
    try:
        z = float(str(w).replace(".", "").replace(",", ".")) if isinstance(w, str) and "," in str(w) else float(w)
    except (TypeError, ValueError):
        return None
    if z != z or z in (float("inf"), float("-inf")):
        return None
    return z


def _jahr(ez) -> Optional[int]:
    m = re.search(r"(\d{4})", str(ez or ""))
    return int(m.group(1)) if m else None


def _norm(s) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()


# ------------------------------------------------ Marktvergleich
def _getriebe(v: dict) -> Optional[str]:
    """mobile.de-Getriebecode; Halbautomatik zaehlt wie Automatik (DSG &
    Co. stehen mal so, mal so)."""
    try:
        from fahrzeug_codes import getriebe_code
        code = getriebe_code(v.get("gearbox"), v.get("gearbox_label"), v.get("transmission"))
    except Exception:  # noqa: BLE001
        return None
    return "AUTOMATIC_GEAR" if code == "SEMIAUTOMATIC_GEAR" else code


def _modell_text(v: dict) -> str:
    return _norm(v.get("model_label") or v.get("model"))


def _markt_schluessel(v: dict) -> str:
    """Review 26.09.2026 (Nr. 29): exaktes Jahr, km je 10.000, vollstaendiges
    Modell, Getriebe und Leistung je 10 kW — vorher teilten sich ein 2019er
    und ein 2021er, ein Schalter und eine Automatik denselben Eintrag."""
    jahr = _jahr(v.get("first_registration") or v.get("ezl")) or 0
    km = _zahl(v.get("mileage") or v.get("km")) or 0
    kw = _zahl(v.get("power_kw")) or 0
    return "|".join([_norm(v.get("make_label") or v.get("make")), _modell_text(v), str(jahr),
                     str(int(km // 10000)), _norm(v.get("fuel_label") or v.get("fuel")),
                     _getriebe(v) or "", str(int(kw // 10))])


def _wort_praefix(lang: str, kurz: str) -> bool:
    return bool(kurz) and (lang == kurz or lang.startswith(kurz + " "))


def modell_passt(kandidat: dict, modell: str) -> bool:
    """Review 26.09.2026 (Nr. 30): das GANZE normalisierte Modell zaehlt, als
    Wort-Praefix in beide Richtungen — "c 220 d" trifft "C 220 d 4MATIC" und
    "C 220", nicht "C 180" und nicht "C 2200"."""
    for feld in ("model_label", "model", "model_description"):
        n = _norm(kandidat.get(feld))
        if n and (_wort_praefix(n, modell) or _wort_praefix(modell, n)):
            return True
    return False


def _frische_filter(sammlung: str) -> Dict[str, Any]:
    """Nur Daten der letzten MARKT_MAX_TAGE (Nr. 32): vehicles tragen
    created_at/updated_at als ISO-Text, listings_cache fetched_at/created_at
    als datetime — beide Formen werden geprueft."""
    from datetime import datetime, timedelta, timezone
    seit_dt = datetime.now(timezone.utc) - timedelta(days=MARKT_MAX_TAGE)
    seit = seit_dt.isoformat()
    if sammlung == "vehicles":
        return {"$or": [{"updated_at": {"$gte": seit}}, {"created_at": {"$gte": seit}},
                        {"updated_at": {"$gte": seit_dt}}, {"created_at": {"$gte": seit_dt}}]}
    return {"$or": [{"fetched_at": {"$gte": seit_dt}}, {"created_at": {"$gte": seit_dt}},
                    {"fetched_at": {"$gte": seit}}, {"created_at": {"$gte": seit}}]}


async def _marktbeobachtung(vehicle: dict, db) -> Optional[Dict[str, Any]]:
    """Review 26.09.2026 (Nr. 33): Liegt zum Fahrzeug ein beobachtetes
    Marktsegment (Market Intelligence, mobile.de-Top-20) mit Daten vor, hat
    es Vorrang vor dem groben Vergleich aus eigenen Fahrzeugen. Nur lesen,
    kurzes Zeitlimit, nie eine Ausnahme."""
    try:
        from markt import abfrage
        # halbes Kontext-Zeitlimit: bleibt die Beobachtung aus, hat der grobe
        # Vergleich noch Zeit, bevor `sammeln` den ganzen Marktteil aufgibt
        karte = await asyncio.wait_for(abfrage.karte(db, vehicle), timeout=KONTEXT_ZEITLIMIT_S / 2)
    except Exception as exc:  # noqa: BLE001
        log.info("Marktbeobachtung nicht verfuegbar: %s", exc)
        return None
    if not karte or not karte.get("sample_size") or not karte.get("median_top20_price"):
        return None
    return {"source": "marktbeobachtung", "comparable_count": int(karte["sample_size"]),
            "median_price_eur": round(float(karte["median_top20_price"])),
            "min_price_eur": round(float(karte["min_price"])) if karte.get("min_price") else None,
            "p25_price_eur": round(float(karte["p25_price"])) if karte.get("p25_price") else None,
            "p75_price_eur": round(float(karte["p75_price"])) if karte.get("p75_price") else None,
            "datenstand": karte.get("datenstand") or karte.get("datum"),
            "segment": karte.get("label"),
            "hint": "guenstigstes Segment (Top 20 auf mobile.de), kein Marktmedian"}


async def marktvergleich(vehicle: dict, *, eigene_id: str = "", db=None) -> Optional[Dict[str, Any]]:
    """EINE Marktzahl fuer die KI: zuerst die Marktbeobachtung (quelle
    "marktbeobachtung"), sonst der grobe Vergleich (quelle "grob") aus
    vehicles und listings_cache aller Firmen — Marke, ganzes Modell, EZ +-2
    Jahre, km +-40 %, gleicher Kraftstoff, gleiches Getriebe, Leistung
    +-15 % (wenn beide bekannt), Daten hoechstens MARKT_MAX_TAGE alt. Nur
    Zahlen, keine Inserate. None, wenn zu wenige."""
    db = db if db is not None else _db
    v = vehicle or {}
    marke, modell = _norm(v.get("make_label") or v.get("make")), _modell_text(v)
    if not marke or not modell:
        return None
    key = _markt_schluessel(v)
    treffer = _markt_cache.get(key)
    if treffer and time.time() - treffer["t"] < MARKT_CACHE_S:
        return treffer["wert"]
    wert = await _marktbeobachtung(v, db)
    if wert is None:
        wert = await _grober_vergleich(v, marke, modell, eigene_id=eigene_id, db=db)
    _markt_cache[key] = {"t": time.time(), "wert": wert}
    return wert


async def _grober_vergleich(v: dict, marke: str, modell: str, *, eigene_id: str, db) -> Optional[Dict[str, Any]]:
    jahr = _jahr(v.get("first_registration") or v.get("ezl"))
    km = _zahl(v.get("mileage") or v.get("km"))
    kw = _zahl(v.get("power_kw"))
    fuel = _norm(v.get("fuel_label") or v.get("fuel"))
    getriebe = _getriebe(v)
    preise: List[float] = []
    muster_marke = re.compile("^" + re.escape(marke.split(" ")[0]), re.I)
    muster_modell = re.compile(re.escape(modell.split(" ")[0]), re.I)
    for sammlung, feld in (("vehicles", "data"), ("listings_cache", "data")):
        cursor = db[sammlung].find(
            {"$and": [
                {"$or": [{f"{feld}.make_label": muster_marke}, {f"{feld}.make": muster_marke}]},
                {"$or": [{f"{feld}.model_label": muster_modell}, {f"{feld}.model": muster_modell},
                         {f"{feld}.model_description": muster_modell}]},
                _frische_filter(sammlung),
            ]},
            {"_id": 0, "id": 1, f"{feld}.price": 1, f"{feld}.list_price": 1, f"{feld}.first_registration": 1,
             f"{feld}.mileage": 1, f"{feld}.fuel_label": 1, f"{feld}.fuel": 1, f"{feld}.model_label": 1,
             f"{feld}.model": 1, f"{feld}.model_description": 1, f"{feld}.gearbox": 1, f"{feld}.gearbox_label": 1,
             f"{feld}.transmission": 1, f"{feld}.power_kw": 1}
        ).limit(400)
        async for d in cursor:
            if eigene_id and d.get("id") == eigene_id:
                continue
            dd = d.get(feld) or {}
            p = _zahl(dd.get("price")) or _zahl(dd.get("list_price"))
            if not p or p < 300:
                continue
            if not modell_passt(dd, modell):
                continue
            j = _jahr(dd.get("first_registration"))
            if jahr and j and abs(j - jahr) > 2:
                continue
            k = _zahl(dd.get("mileage"))
            if km and k and (k < km * 0.6 or k > km * 1.4):
                continue
            f = _norm(dd.get("fuel_label") or dd.get("fuel"))
            if fuel and f and f != fuel:
                continue
            g = _getriebe(dd)
            if getriebe and g and g != getriebe:
                continue
            k_kw = _zahl(dd.get("power_kw"))
            if kw and k_kw and abs(k_kw - kw) > kw * MARKT_KW_TOLERANZ:
                continue
            preise.append(p)
    # Nr. 28: KEIN set() — zwei Inserate zum selben Preis sind zwei Inserate
    preise = sorted(round(p) for p in preise)
    if len(preise) < 3:
        return None
    q = statistics.quantiles(preise, n=4) if len(preise) >= 4 else [preise[0], statistics.median(preise), preise[-1]]
    return {"source": "grob", "comparable_count": len(preise), "median_price_eur": round(statistics.median(preise)),
            "p25_price_eur": round(q[0]), "p75_price_eur": round(q[-1])}


def marktposition(markt: Optional[dict], *, listing: Optional[float], agreed: Optional[float]) -> Optional[dict]:
    if not markt or not markt.get("median_price_eur"):
        return None
    med = float(markt["median_price_eur"])
    raus = dict(markt)
    if listing:
        raus["listing_vs_median_percent"] = round((listing - med) / med * 100, 1)
    if agreed:
        raus["agreed_vs_median_percent"] = round((agreed - med) / med * 100, 1)
    return raus


# ------------------------------------------------ Reparaturreferenz
def _markt_zeile(marktdoc: Optional[dict], typ: str, auspraegung: str) -> Optional[dict]:
    """Zeile der Markttabelle, die zur Startwert-Zeile passt (typ gleich,
    Auspraegung aehnlich)."""
    if not marktdoc or marktdoc.get("status") != "ok":
        return None
    kandidaten = [p for p in marktdoc.get("positionen") or [] if p.get("typ") == typ]
    if not kandidaten:
        return None
    woerter = set(_norm(auspraegung).split())
    beste, punkte = None, 0
    for p in kandidaten:
        w = set(_norm(p.get("auspraegung")).split())
        gemeinsam = len(woerter & w)
        if gemeinsam > punkte:
            beste, punkte = p, gemeinsam
    if beste is None or punkte < 2:
        return None
    return beste


def reparaturreferenz(damage: dict, marktdoc: Optional[dict]) -> Optional[Dict[str, Any]]:
    """Referenz je Schaden: Markttabelle (mit Quelle) vor Startwerten. Die
    eigene Preisdatenbank legt sich danach ueber `eigene_anwenden` darueber."""
    ref = preisbasis.referenz(damage.get("type_key") or damage.get("type") or "", damage.get("severity_data"),
                              damage.get("zone") or "")
    if not ref:
        return None
    z = preisbasis.zeile(ref["key"]) or {}
    m = _markt_zeile(marktdoc, z.get("typ", ""), z.get("auspraegung", ""))
    if m and not ref.get("manual_review"):
        ref.update(low=m["min_eur"], median=m["typisch_eur"], high=m["max_eur"],
                   source=f"Marktdaten {str(marktdoc.get('stand') or '')[:10]} ({m.get('quelle') or 'o. Q.'})")
    return ref


def abweichungsreferenz(typ: str, *, betrag: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """Referenz fuer Abweichungen ohne Skizze (Schluessel, Reifen, km ...)."""
    key = {"keys": "keys_fehlt", "tires": "tires_schlecht", "hu": "hu_faellig", "mileage": "km",
           "previous_owners": "halter", "equipment_missing": "ausstattung_fehlt",
           "equipment_defect": "ausstattung_defekt", "warning_light": "warnleuchte",
           "accident_history": "unfallfrei_abweichung", "technical": "warnleuchte"}.get(typ)
    z = preisbasis.zeile(key) if key else None
    if not z:
        return None
    faktor = 1.0
    if typ == "mileage" and betrag:
        faktor = max(1.0, float(betrag) / 1000.0)
    if typ == "previous_owners" and betrag:
        faktor = max(1.0, float(betrag))
    ref = preisbasis.referenz_aus_zeile(z, faktor=faktor)
    return {**ref,
            "manual_review": bool(z.get("manuell")), "source": "AutoSchnell-Startwerte", "assumption_made": False}


def eigene_anwenden(paket: Dict[str, Any], eigene: Dict[str, Dict[str, Any]]) -> int:
    """Eigene Preisdatenbank (ki_reparaturpreise) ueber die Referenzen legen —
    sie hat Vorrang vor Markttabelle und Startwerten. Liefert die Anzahl."""
    if not eigene:
        return 0
    n = 0
    for liste in (paket.get("damages") or [], paket.get("new_damages") or [], paket.get("deviations") or []):
        for p in liste:
            ref = p.get("repair_reference")
            if not ref or ref.get("manual_review"):
                continue
            e = eigene.get(ref.get("key") or "")
            if not e:
                continue
            ref.update(low=e["low"], median=e["median"], high=e["high"], source=e["source"], own_data_n=e["n"])
            n += 1
    if n:
        paket["precomputed"] = vorberechnet(paket)
    return n


# ------------------------------------------------ Historie
async def historie(vehicle: dict, art: str, *, db=None) -> Optional[Dict[str, Any]]:
    """Aehnliche eigene Faelle: gleiche Marke, gleiche Art. Nur Statistik."""
    db = db if db is not None else _db
    marke = _norm((vehicle or {}).get("make_label") or (vehicle or {}).get("make")).split(" ")[0]
    if not marke:
        return None
    ki, ist = [], []
    async for d in db.ki_lernfaelle.find({"art": art, "fahrzeug.make": re.compile("^" + re.escape(marke), re.I)},
                                         {"_id": 0, "ki_nachlass": 1, "tatsaechlicher_nachlass": 1,
                                          "chef_nachlass": 1}).limit(500):
        k = _zahl(d.get("ki_nachlass"))
        t = _zahl(d.get("tatsaechlicher_nachlass") if d.get("tatsaechlicher_nachlass") is not None else d.get("chef_nachlass"))
        if k:
            ki.append(k)
        if t is not None and t >= 0:
            ist.append(t)
    if len(ki) < 3:
        return None
    return {"similar_cases": len(ki), "median_ai_fair_eur": round(statistics.median(ki)),
            "median_actual_discount_eur": round(statistics.median(ist)) if len(ist) >= 3 else None}


# ------------------------------------------------ Datenlage / Vorberechnung
def _positionen(paket: Dict[str, Any]) -> List[dict]:
    return list(paket.get("damages") or []) \
        + [d for d in (paket.get("new_damages") or []) if not d.get("already_known")] \
        + list(paket.get("deviations") or [])


def datenlage(paket: Dict[str, Any]) -> str:
    """hoch: alle Angaben da, Preis bekannt, Referenzen und Markt da.
    mittel: eine wichtige Quelle fehlt oder eine Annahme. niedrig: mehrere."""
    minus = 0
    pos = _positionen(paket)
    if any((p.get("repair_reference") or {}).get("assumption_made") for p in pos):
        minus += 1
    if any(not p.get("repair_reference") and p.get("type") not in ("other", "technical") for p in pos):
        minus += 1
    preise = paket.get("prices") or {}
    if not (preise.get("contract_price_eur") or preise.get("agreed_price_eur") or preise.get("listing_price_eur")):
        minus += 2
    if not paket.get("market"):
        minus += 1
    if minus == 0:
        return "hoch"
    return "mittel" if minus == 1 else "niedrig"


def vorberechnet(paket: Dict[str, Any]) -> Dict[str, Any]:
    refs = [(p.get("repair_reference") or {}) for p in _positionen(paket)]
    summe = round(sum(float(r.get("median") or 0) for r in refs if r and not r.get("manual_review")))
    preise = paket.get("prices") or {}
    basis = preise.get("agreed_price_eur") or preise.get("contract_price_eur") or preise.get("listing_price_eur")
    anteil = round(summe / float(basis) * 100, 1) if basis else None
    return {"repair_reference_total_eur": summe, "repair_percent_of_price": anteil,
            "manual_review_positions": sum(1 for r in refs if r and r.get("manual_review"))}


async def sammeln(vehicle: dict, art: str, *, eigene_id: str = "", db=None) -> Dict[str, Any]:
    """Marktvergleich, Historie und Markttabelle parallel, jeweils mit
    Zeitlimit — fehlt eins, laeuft die Bewertung mit dem Rest weiter."""
    from ai import marktdaten

    async def _sicher(coro, name):
        try:
            return await asyncio.wait_for(coro, timeout=KONTEXT_ZEITLIMIT_S)
        except Exception as exc:  # noqa: BLE001
            log.info("Kontext %s nicht verfuegbar: %s", name, exc)
            return None
    markt, hist, tabelle = await asyncio.gather(
        _sicher(marktvergleich(vehicle, eigene_id=eigene_id, db=db), "markt"),
        _sicher(historie(vehicle, art, db=db), "historie"),
        _sicher(marktdaten.aktuell(db), "marktdaten"),
    )
    return {"markt": markt, "historie": hist, "marktdoc": tabelle}


def vorschau(paket: Dict[str, Any], kaufpreis: Optional[float]) -> Dict[str, Any]:
    """Deterministische Sofort-Schaetzung aus den Referenzen (Median), damit
    niemand auf die KI warten muss: fair = Summe der Mediane, minimum =
    Summe low, best = Summe high, start = best * 1.15."""
    pos = _positionen(paket)
    refs = [(p.get("repair_reference") or {}) for p in pos]
    manuell = any(r.get("manual_review") for r in refs if r)
    refs = [r for r in refs if r and not r.get("manual_review")]
    lo = round(sum(float(r.get("low") or 0) for r in refs))
    fair = round(sum(float(r.get("median") or 0) for r in refs))
    hi = round(sum(float(r.get("high") or 0) for r in refs))
    kp = float(kaufpreis or 0)
    if kp > 0:
        lo, fair, hi = min(lo, kp), min(fair, kp), min(hi, kp)
    return {"minimum_justified_eur": lo, "fair_discount_eur": fair, "best_realistic_eur": hi,
            "negotiation_start_eur": round(min(hi * 1.15, kp) if kp > 0 else hi * 1.15),
            "manual_review_required": manuell, "vorlaeufig": True,
            "recommended_purchase_price_eur": round(kp - fair) if kp > 0 and fair > 0 else None}
