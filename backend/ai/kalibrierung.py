# -*- coding: utf-8 -*-
"""Stufe 4 (Wunsch Ahmad 25.09.2026): Die KI lernt aus den eigenen Faellen.

Jede Freigabe (Abholung) und jeder Vertrag mit vorheriger KI-Bewertung legt
einen anonymisierten Lernfall in ki_lernfaelle ab: KI-Empfehlung, tatsaechlich
erzielter Nachlass, Kategorien. Hier werden daraus ERFAHRUNGSWERTE gebildet
(Median des Verhaeltnisses "tatsaechlich / KI-Empfehlung", gesamt und je
Kategorie) und als kurzer Zusatz in den System-Prompt gegeben — erst ab
KI_KALIBRIERUNG_MIN_FAELLE Faellen, damit ein einzelner Ausreisser nichts
verschiebt. Zusaetzlich die Betriebszahlen fuer /admin/ki.
"""
from __future__ import annotations

import logging
import statistics
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from deps import db
from konfig import zahl_env

log = logging.getLogger("autohandel.ki")

MIN_FAELLE = zahl_env("KI_KALIBRIERUNG_MIN_FAELLE", 5, unten=1, oben=10000)
CACHE_SEKUNDEN = 600
LERN_SAMMLUNG = "ki_lernfaelle"
BEWERTUNGEN = "ki_bewertungen"
# Richtwerte je Million Tokens (USD, Listenpreise 09/2026) — nur fuer die
# Kostenschaetzung auf der Betriebsseite, nicht fuer die Abrechnung.
PREIS_JE_MIO = {"claude-opus-5": (15.0, 75.0), "claude-sonnet-5": (3.0, 15.0)}

_cache: Dict[str, Any] = {"bis": 0.0, "werte": None}


def _faktor(doc: dict) -> Optional[float]:
    ki = doc.get("ki_nachlass")
    ist = doc.get("tatsaechlicher_nachlass")
    if ist is None:
        ist = doc.get("chef_nachlass")
    try:
        ki, ist = float(ki), float(ist)
    except (TypeError, ValueError):
        return None
    if ki <= 0 or ist < 0:
        return None
    return min(ist / ki, 3.0)      # Deckel: ein Chef, der 5x so viel holt, ist kein Massstab


async def erfahrungswerte(*, frisch: bool = False) -> Dict[str, Any]:
    """Gesamt und je Kategorie (nur Faelle mit genau EINER Position, damit
    das Verhaeltnis der Kategorie zuzuordnen ist). Wirft nie."""
    if not frisch and _cache["werte"] is not None and time.time() < _cache["bis"]:
        return _cache["werte"]
    werte: Dict[str, Any] = {"gesamt": {"n": 0}, "je_kategorie": {}, "je_art": {}}
    try:
        faktoren: List[float] = []
        je_kat: Dict[str, List[float]] = {}
        je_art: Dict[str, List[float]] = {}
        cursor = db[LERN_SAMMLUNG].find({}, {"_id": 0, "ki_nachlass": 1, "tatsaechlicher_nachlass": 1,
                                            "chef_nachlass": 1, "items": 1, "art": 1}
                                        ).sort("created_at", -1).limit(3000)
        async for d in cursor:
            f = _faktor(d)
            if f is None:
                continue
            faktoren.append(f)
            je_art.setdefault(d.get("art") or "abholung", []).append(f)
            items = [i for i in (d.get("items") or []) if (i.get("recommended_discount_eur") or 0) > 0]
            if len(items) == 1:
                je_kat.setdefault(str(items[0].get("category") or "other"), []).append(f)
        if faktoren:
            werte["gesamt"] = {"n": len(faktoren), "faktor_median": round(statistics.median(faktoren), 2)}
        werte["je_kategorie"] = {k: {"n": len(v), "faktor_median": round(statistics.median(v), 2)}
                                 for k, v in je_kat.items()}
        werte["je_art"] = {k: {"n": len(v), "faktor_median": round(statistics.median(v), 2)}
                           for k, v in je_art.items()}
    except Exception:  # noqa: BLE001
        log.exception("Erfahrungswerte nicht berechenbar")
    _cache.update(bis=time.time() + CACHE_SEKUNDEN, werte=werte)
    return werte


def als_text(werte: Dict[str, Any]) -> str:
    """Kurzer Prompt-Zusatz — leer, solange zu wenige Faelle vorliegen."""
    g = (werte or {}).get("gesamt") or {}
    if (g.get("n") or 0) < MIN_FAELLE:
        return ""
    zeilen = [f"Erfahrungswerte aus {g['n']} abgeschlossenen AutoSchnell-Faellen: der tatsaechlich "
              f"erzielte Nachlass lag im Median bei {round(g['faktor_median'] * 100)} % der KI-Empfehlung."]
    kat = [(k, w) for k, w in ((werte or {}).get("je_kategorie") or {}).items() if (w.get("n") or 0) >= MIN_FAELLE]
    if kat:
        zeilen.append("Je Kategorie: " + "; ".join(f"{k} {round(w['faktor_median'] * 100)} % (n={w['n']})"
                                                   for k, w in sorted(kat)))
    zeilen.append("Beruecksichtige das: liegt der Wert deutlich unter 100 %, waren fruehere Empfehlungen "
                  "eher zu hoch, ueber 100 % eher zu niedrig — kalibriere den Hauptwert entsprechend, "
                  "ohne die Bereiche zu verbreitern.")
    return "\n".join(zeilen)


async def prompt_zusatz() -> str:
    try:
        return als_text(await erfahrungswerte())
    except Exception:  # noqa: BLE001
        return ""


def zuruecksetzen() -> None:
    _cache.update(bis=0.0, werte=None)


# ------------------------------------------------ Betriebszahlen (/admin/ki)
def _kosten_usd(modell: str, usage: Dict[str, Any]) -> float:
    ein, aus = PREIS_JE_MIO.get(modell or "", PREIS_JE_MIO["claude-sonnet-5"])
    e = float(usage.get("input_tokens") or 0) + float(usage.get("cache_creation_input_tokens") or 0) * 1.25 \
        + float(usage.get("cache_read_input_tokens") or 0) * 0.1
    a = float(usage.get("output_tokens") or 0)
    # Websuche (26.09.2026): 10 USD je 1.000 Suchen
    suchen = float(usage.get("web_search_requests") or 0)
    return e / 1e6 * ein + a / 1e6 * aus + suchen * 0.01


async def statistik(tage: int = 30) -> Dict[str, Any]:
    """Zahlen fuer die Betriebsseite: Aufrufe je Status/Art, Dauer, Tokens,
    Kostenschaetzung, letzte Fehler, Lernfaelle und Erfahrungswerte."""
    seit = (datetime.now(timezone.utc) - timedelta(days=tage)).isoformat()
    je_status: Dict[str, int] = {}
    je_art: Dict[str, int] = {}
    dauern: List[int] = []
    tokens = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
              "web_search_requests": 0}
    kosten = 0.0
    fehler: List[Dict[str, Any]] = []
    n = 0
    try:
        cursor = db[BEWERTUNGEN].find({"created_at": {"$gte": seit}},
                                      {"_id": 0, "status": 1, "art": 1, "dauer_ms": 1, "usage": 1, "modell": 1,
                                       "grund": 1, "created_at": 1, "protocol_id": 1, "vehicle_id": 1}
                                      ).sort("created_at", -1).limit(5000)
        async for d in cursor:
            n += 1
            st = d.get("status") or "?"
            je_status[st] = je_status.get(st, 0) + 1
            art = d.get("art") or "abholung"
            je_art[art] = je_art.get(art, 0) + 1
            if st == "ok" and isinstance(d.get("dauer_ms"), int):
                dauern.append(d["dauer_ms"])
            u = d.get("usage") or {}
            for k in tokens:
                tokens[k] += int(u.get(k) or 0)
            if u:
                kosten += _kosten_usd(d.get("modell") or "", u)
            if st in ("fehler", "zeitlimit", "ueberlastet", "schluessel", "abgelehnt") and len(fehler) < 10:
                fehler.append({"status": st, "grund": (d.get("grund") or "")[:160], "am": d.get("created_at"),
                               "art": art, "ref": d.get("protocol_id") or d.get("vehicle_id") or ""})
        lern_n = await db[LERN_SAMMLUNG].count_documents({})
        lern_mit_ergebnis = await db[LERN_SAMMLUNG].count_documents(
            {"$or": [{"tatsaechlicher_nachlass": {"$ne": None}}, {"chef_nachlass": {"$ne": None}}]})
    except Exception:  # noqa: BLE001
        log.exception("KI-Statistik nicht berechenbar")
        lern_n = lern_mit_ergebnis = 0
    dauern.sort()
    p95 = dauern[int(len(dauern) * 0.95) - 1] if len(dauern) >= 2 else (dauern[0] if dauern else None)
    return {
        "zeitraum_tage": tage, "bewertungen": n, "je_status": je_status, "je_art": je_art,
        "dauer_median_ms": int(statistics.median(dauern)) if dauern else None,
        "dauer_p95_ms": p95, "tokens": tokens, "kosten_usd_geschaetzt": round(kosten, 2),
        "letzte_fehler": fehler,
        "lernfaelle": {"gesamt": lern_n, "mit_ergebnis": lern_mit_ergebnis, "min_fuer_kalibrierung": MIN_FAELLE},
        "erfahrungswerte": await erfahrungswerte(),
        "marktdaten": await _marktdaten_kurz(),
    }


async def _marktdaten_kurz() -> Dict[str, Any]:
    """Stand der Markttabelle (Stufe 5) fuer die Betriebsseite."""
    try:
        from ai import marktdaten
        doc = await marktdaten.aktuell()
        return {"aktiv": marktdaten.aktiv(), "je_fall_abholung": marktdaten.je_fall("abholung"),
                "je_fall_vertrag": marktdaten.je_fall("vertrag"), "tage": marktdaten.tage(),
                "stand": (doc or {}).get("stand"), "status": (doc or {}).get("status"),
                "grund": (doc or {}).get("grund") or "", "alter_tage": marktdaten.alter_tage(doc),
                "positionen": len((doc or {}).get("positionen") or []),
                "quellen": [q.get("titel") or q.get("url") for q in ((doc or {}).get("quellen") or [])[:8]],
                "zusammenfassung": (doc or {}).get("zusammenfassung") or ""}
    except Exception:  # noqa: BLE001
        return {"aktiv": False, "status": "unbekannt"}
