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

# Umbau 26.09.2026: global erst ab 20 Faellen (ein aggressiv verhandelnder
# Haendler soll nicht alle verschieben), firmeneigen ab 5.
MIN_FAELLE = zahl_env("KI_KALIBRIERUNG_MIN_FAELLE", 20, unten=1, oben=10000)
MIN_FIRMA = zahl_env("KI_KALIBRIERUNG_MIN_FIRMA", 5, unten=1, oben=10000)
CACHE_SEKUNDEN = 600
LERN_SAMMLUNG = "ki_lernfaelle"
BEWERTUNGEN = "ki_bewertungen"
# Richtwerte je Million Tokens (USD, Listenpreise 09/2026) — nur fuer die
# Kostenschaetzung auf der Betriebsseite, nicht fuer die Abrechnung.
PREIS_JE_MIO = {"claude-opus-5": (15.0, 75.0), "claude-sonnet-5": (3.0, 15.0),
                "claude-haiku-4-5-20251001": (1.0, 5.0)}

_cache: Dict[str, Any] = {"bis": 0.0, "werte": None}
NUR_ENDGUELTIG = {"vorlaeufig": {"$ne": True}, "verworfen": {"$ne": True}, "ersetzt": {"$ne": True},
                  "abgleich_unsicher": {"$ne": True}}


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


async def erfahrungswerte(*, frisch: bool = False, dealer_id: Optional[str] = None) -> Dict[str, Any]:
    """Gesamt und je Kategorie (nur Faelle mit genau EINER Position, damit
    das Verhaeltnis der Kategorie zuzuordnen ist). dealer_id: nur die Faelle
    dieser Firma (Cache je Firma). Wirft nie."""
    cache_key = dealer_id or ""
    if not frisch and _cache.get(cache_key) is not None and time.time() < _cache.get("bis:" + cache_key, 0):
        return _cache[cache_key]
    werte: Dict[str, Any] = {"gesamt": {"n": 0}, "je_kategorie": {}, "je_art": {}}
    try:
        faktoren: List[float] = []
        je_kat: Dict[str, List[float]] = {}
        je_art: Dict[str, List[float]] = {}
        je_art_kat: Dict[str, Dict[str, List[float]]] = {}
        # Review 26.09.2026 (Nr. 76-78, 100, 117, 118): nur ENDGUELTIGE Faelle —
        # keine vorlaeufigen (Freigabe ohne Abschluss), verworfenen (Abholung
        # gescheitert), ersetzten (Korrekturversion) oder unsicheren (Abgleich
        # "moeglich"). Aeltere Lernfaelle ohne die Felder zaehlen wie bisher.
        filt: Dict[str, Any] = {**NUR_ENDGUELTIG, **({"dealer_id": dealer_id} if dealer_id else {})}
        cursor = db[LERN_SAMMLUNG].find(filt, {"_id": 0, "ki_nachlass": 1, "tatsaechlicher_nachlass": 1,
                                              "chef_nachlass": 1, "items": 1, "art": 1}
                                        ).sort("created_at", -1).limit(3000)
        async for d in cursor:
            f = _faktor(d)
            if f is None:
                continue
            art = d.get("art") or "abholung"
            faktoren.append(f)
            je_art.setdefault(art, []).append(f)
            items = [i for i in (d.get("items") or []) if (i.get("fair_discount_eur") or i.get("recommended_discount_eur") or 0) > 0]
            if len(items) == 1:
                kat = str(items[0].get("category") or "other")
                je_kat.setdefault(kat, []).append(f)
                je_art_kat.setdefault(art, {}).setdefault(kat, []).append(f)
        if faktoren:
            werte["gesamt"] = {"n": len(faktoren), "faktor_median": round(statistics.median(faktoren), 2)}
        werte["je_kategorie"] = {k: {"n": len(v), "faktor_median": round(statistics.median(v), 2)}
                                 for k, v in je_kat.items()}
        # Review 26.09.2026 (Nr. 36-38): der Prompt-Zusatz nimmt den Faktor JE ART
        # (Abholung und Vertrag verhandeln verschieden), samt Kategorien je Art.
        werte["je_art"] = {k: {"n": len(v), "faktor_median": round(statistics.median(v), 2),
                               "je_kategorie": {kk: {"n": len(vv), "faktor_median": round(statistics.median(vv), 2)}
                                                for kk, vv in (je_art_kat.get(k) or {}).items()}}
                           for k, v in je_art.items()}
    except Exception:  # noqa: BLE001
        log.exception("Erfahrungswerte nicht berechenbar")
    _cache[cache_key] = werte
    _cache["bis:" + cache_key] = time.time() + CACHE_SEKUNDEN
    return werte


# Review 26.09.2026 (Nr. 37): Der Faktor im Prompt bleibt in einem festen
# Band. Ein Haendler, der wenig verhandelt, wuerde die KI sonst mit jedem
# Fall weiter herunterziehen (Rueckkopplung) — und ein Verhandlungskuenstler
# sie nach oben treiben. Die Reparaturkosten bleiben die Grundlage.
FAKTOR_UNTEN, FAKTOR_OBEN = 0.6, 1.2
ART_TITEL = {"abholung": "Abholung", "vertrag": "Vertrag"}


def faktor_begrenzt(f) -> float:
    try:
        return round(min(FAKTOR_OBEN, max(FAKTOR_UNTEN, float(f))), 2)
    except (TypeError, ValueError):
        return 1.0


def als_text(werte: Dict[str, Any], *, art: str = "abholung", minimum: Optional[int] = None,
             titel: str = "AutoSchnell-Faellen") -> str:
    """Kurzer Prompt-Zusatz aus den Faellen DIESER Art — leer, solange zu
    wenige vorliegen. Faktor auf 60-120 % begrenzt und ausdruecklich als
    Orientierung formuliert, nicht als Ersatz fuer die Reparaturkosten."""
    mindest = minimum if minimum is not None else MIN_FAELLE
    a = ((werte or {}).get("je_art") or {}).get(art) or {}
    if (a.get("n") or 0) < mindest:
        return ""
    zeilen = [f"Erfahrungswerte aus {a['n']} abgeschlossenen {titel} ({ART_TITEL.get(art, art)}): der tatsaechlich "
              f"erzielte Nachlass lag im Median bei etwa {round(faktor_begrenzt(a['faktor_median']) * 100)} % "
              "der KI-Empfehlung (fair)."]
    kat = [(k, w) for k, w in (a.get("je_kategorie") or {}).items() if (w.get("n") or 0) >= mindest]
    if kat:
        zeilen.append("Je Kategorie: " + "; ".join(f"{k} etwa {round(faktor_begrenzt(w['faktor_median']) * 100)} % (n={w['n']})"
                                                   for k, w in sorted(kat)))
    zeilen.append("Das ist eine Orientierung fuer die Verhandlungswerte, kein Ersatz fuer die Reparaturkosten: "
                  "fair_discount_eur bleibt an repair_reference und Wertminderung gebunden; passe hoechstens "
                  "best_realistic_eur und negotiation_start_eur in diese Richtung an.")
    return "\n".join(zeilen)


async def prompt_zusatz(dealer_id: Optional[str] = None, art: str = "abholung") -> str:
    """Faktor je Art: die eigene Firma (ab MIN_FIRMA Faellen dieser Art)
    geht vor; sonst global (ab MIN_FAELLE). Nie beides — die KI bekommt
    EINEN Wert. Wirft nie."""
    try:
        if dealer_id:
            firma = als_text(await erfahrungswerte(dealer_id=dealer_id), art=art, minimum=MIN_FIRMA,
                             titel="Faellen dieser Firma")
            if firma:
                return firma
        return als_text(await erfahrungswerte(), art=art)
    except Exception:  # noqa: BLE001
        return ""


def zuruecksetzen() -> None:
    _cache.clear()


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
    kosten_liste: List[float] = []
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
                k_usd = _kosten_usd(d.get("modell") or "", u)
                kosten += k_usd
                if st == "ok":
                    kosten_liste.append(round(k_usd * 100, 2))
            if st in ("fehler", "zeitlimit", "ueberlastet", "schluessel", "abgelehnt") and len(fehler) < 10:
                fehler.append({"status": st, "grund": (d.get("grund") or "")[:160], "am": d.get("created_at"),
                               "art": art, "ref": d.get("protocol_id") or d.get("vehicle_id") or ""})
        lern_n = await db[LERN_SAMMLUNG].count_documents({})
        lern_mit_ergebnis = await db[LERN_SAMMLUNG].count_documents(
            {**NUR_ENDGUELTIG, "$or": [{"tatsaechlicher_nachlass": {"$ne": None}}, {"chef_nachlass": {"$ne": None}}]})
        lern_vorlaeufig = await db[LERN_SAMMLUNG].count_documents({"vorlaeufig": True, "ersetzt": {"$ne": True},
                                                                  "verworfen": {"$ne": True}})
    except Exception:  # noqa: BLE001
        log.exception("KI-Statistik nicht berechenbar")
        lern_n = lern_mit_ergebnis = lern_vorlaeufig = 0
    dauern.sort()
    p95 = dauern[int(len(dauern) * 0.95) - 1] if len(dauern) >= 2 else (dauern[0] if dauern else None)
    return {
        "zeitraum_tage": tage, "bewertungen": n, "je_status": je_status, "je_art": je_art,
        "dauer_median_ms": int(statistics.median(dauern)) if dauern else None,
        "dauer_p95_ms": p95, "tokens": tokens, "kosten_usd_geschaetzt": round(kosten, 2),
        "letzte_fehler": fehler,
        "lernfaelle": {"gesamt": lern_n, "mit_ergebnis": lern_mit_ergebnis, "vorlaeufig": lern_vorlaeufig,
                       "min_fuer_kalibrierung": MIN_FAELLE, "min_firma": MIN_FIRMA},
        "erfahrungswerte": await erfahrungswerte(),
        "marktdaten": await _marktdaten_kurz(),
        "eigene_preise": await _eigene_kurz(),
        "budget": {"monat_eur": _budget().budget_monat_eur(), "lauf_max_ct": _budget().kosten_max_ct()},
        "kosten_median_ct": _median_kosten(kosten_liste),
    }


def _budget():
    from ai import budget
    return budget


def _median_kosten(werte: List[float]) -> Optional[float]:
    return round(statistics.median(werte), 2) if werte else None


async def _eigene_kurz() -> Dict[str, Any]:
    try:
        from ai import marktdaten
        return await marktdaten.statistik_eigene()
    except Exception:  # noqa: BLE001
        return {}


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
