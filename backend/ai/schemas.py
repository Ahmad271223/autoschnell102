# -*- coding: utf-8 -*-
"""JSON-Schema der KI-Antwort und die Nachpruefung im Backend.

Fassung 3 (Umbau 26.09.2026, Wunsch Ahmad): keine Rueckfragen mehr (alle
Angaben kommen vorher aus dem Formular), keine Prozent-"Sicherheit" (die
Datenlage hoch/mittel/niedrig rechnet das Backend), vier klare Geldwerte
je Position und insgesamt:

  minimum_justified_eur  darunter ist der Nachteil nicht ausgeglichen
  fair_discount_eur      der sachlich am besten begruendbare Zielwert
  best_realistic_eur     sehr gutes, noch vertretbares Ergebnis
  negotiation_start_eur  sinnvolle erste Forderung (ueber best, nicht absurd)

dazu deal_risk normal | high | reconsider_purchase (kein 30-%-Deckel mehr:
ein nicht erwaehnter Motorschaden am 2.000-EUR-Auto darf den Kauf in Frage
stellen). Die KI bekommt das Schema ueber output_config.format; `bereinigen`
prueft trotzdem jede Zahl (Reihenfolge min <= fair <= best <= start, nie
ueber dem Preis, manuell = 0)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

PROMPT_VERSION = "abholung_v3"
PROMPT_VERSION_VERTRAG = "vertrag_v2"

KATEGORIEN = ["damage", "damage_worse", "mileage", "keys", "previous_owners",
              "equipment_missing", "equipment_defect", "tires", "documents", "hu",
              "accident_history", "warning_light", "technical", "other"]
PRIORITAETEN = ["rot", "orange", "gelb"]
DEAL_RISK = ["normal", "high", "reconsider_purchase"]
DATENLAGE = ["hoch", "mittel", "niedrig"]

_GELD = {"type": "number"}

_POSITION = {
    "type": "object",
    "properties": {
        "source_id": {"type": "string", "description": "id der Abweichung/des Schadens aus der Eingabe"},
        "category": {"type": "string", "enum": KATEGORIEN},
        "title": {"type": "string", "description": "kurz, deutsch, hoechstens 8 Woerter"},
        "price_relevant": {"type": "boolean"},
        "repair_method": {"type": "string", "description": "Reparaturweg, deutsch, hoechstens 6 Woerter; leer wenn keiner"},
        "repair_estimate_eur": {"type": "number", "description": "geschaetzte Reparaturkosten aus der Referenz, 0 wenn nicht anwendbar"},
        "minimum_justified_eur": _GELD,
        "fair_discount_eur": _GELD,
        "best_realistic_eur": _GELD,
        "negotiation_start_eur": _GELD,
        "manual_review_required": {"type": "boolean",
                                   "description": "true bei Unfallfreiheit, Warnleuchte, Durchrostung tragender Teile u. ae. — dann alle Betraege 0"},
        "reason": {"type": "string", "description": "ein Satz, deutsch, hoechstens 14 Woerter; bei 'unbekannt' die getroffene Annahme nennen"},
    },
    "required": ["source_id", "category", "title", "price_relevant", "repair_method", "repair_estimate_eur",
                 "minimum_justified_eur", "fair_discount_eur", "best_realistic_eur", "negotiation_start_eur",
                 "manual_review_required", "reason"],
    "additionalProperties": False,
}

ANTWORT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "items": {"type": "array", "items": _POSITION},
        "combined": {
            "type": "object",
            "properties": {
                "sum_fair_eur": {"type": "number", "description": "Summe der fair_discount_eur aller Positionen"},
                "overlap_adjustment_eur": {"type": "number",
                                           "description": "Abzug fuer ueberlappende Arbeiten (>= 0), z. B. zwei Schaeden am selben Bauteil"},
                "minimum_justified_eur": _GELD,
                "fair_discount_eur": _GELD,
                "best_realistic_eur": _GELD,
                "negotiation_start_eur": _GELD,
                "deal_risk": {"type": "string", "enum": DEAL_RISK,
                              "description": "reconsider_purchase, wenn die Maengel den Kauf wirtschaftlich in Frage stellen"},
                "manual_review_required": {"type": "boolean"},
            },
            "required": ["sum_fair_eur", "overlap_adjustment_eur", "minimum_justified_eur", "fair_discount_eur",
                         "best_realistic_eur", "negotiation_start_eur", "deal_risk", "manual_review_required"],
            "additionalProperties": False,
        },
        "arguments": {"type": "array", "items": {"type": "string"},
                      "description": "hoechstens 3 kurze Verhandlungsargumente fuer den Verkaeufer, deutsch"},
    },
    "required": ["items", "combined", "arguments"],
    "additionalProperties": False,
}


def _zahl(w: Any, unten: float = 0.0, oben: Optional[float] = None) -> float:
    try:
        z = float(w)
    except (TypeError, ValueError):
        z = 0.0
    if z != z or z in (float("inf"), float("-inf")):
        z = 0.0
    z = max(unten, z)
    if oben is not None:
        z = min(oben, z)
    return round(z, 2)


def _vier(roh: Dict[str, Any], deckel: Optional[float]) -> Dict[str, float]:
    """min <= fair <= best <= start, alle >= 0, keiner ueber dem Deckel
    (Preis). Start hoechstens 40 % ueber best, best hoechstens 60 % ueber fair."""
    mn = _zahl(roh.get("minimum_justified_eur"), 0.0, deckel)
    fair = _zahl(roh.get("fair_discount_eur"), 0.0, deckel)
    best = _zahl(roh.get("best_realistic_eur"), 0.0, deckel)
    start = _zahl(roh.get("negotiation_start_eur"), 0.0, deckel)
    if fair <= 0:
        return {"minimum_justified_eur": 0.0, "fair_discount_eur": 0.0, "best_realistic_eur": 0.0,
                "negotiation_start_eur": 0.0}
    mn = min(mn, fair)
    best = max(best, fair)
    best = min(best, round(fair * 1.6, 2))
    start = max(start, best)
    start = min(start, round(best * 1.4, 2))
    if deckel is not None:
        best, start = min(best, deckel), min(start, deckel)
    return {"minimum_justified_eur": mn, "fair_discount_eur": fair, "best_realistic_eur": best,
            "negotiation_start_eur": start}


def bereinigen(daten: Dict[str, Any], *, kaufpreis: Optional[float]) -> Dict[str, Any]:
    """Zahlen absichern, Reihenfolge erzwingen, Summen plausibel halten.
    Liefert eine neue Struktur (das Original bleibt fuer die Ablage)."""
    kp = _zahl(kaufpreis) if kaufpreis else 0.0
    deckel = kp if kp > 0 else None      # nie mehr als der Preis selbst
    items: List[Dict[str, Any]] = []
    for roh in daten.get("items") or []:
        if not isinstance(roh, dict):
            continue
        manuell = bool(roh.get("manual_review_required"))
        vier = _vier({} if manuell else roh, deckel)
        items.append({
            "source_id": str(roh.get("source_id") or ""),
            "category": roh.get("category") if roh.get("category") in KATEGORIEN else "other",
            "title": str(roh.get("title") or "")[:120],
            "price_relevant": bool(roh.get("price_relevant")) and (vier["fair_discount_eur"] > 0 or manuell),
            "repair_method": str(roh.get("repair_method") or "")[:80],
            "repair_estimate_eur": _zahl(roh.get("repair_estimate_eur"), 0.0, None),
            **vier,
            "manual_review_required": manuell,
            "reason": str(roh.get("reason") or "")[:300],
        })
    c = daten.get("combined") or {}
    summe = round(sum(i["fair_discount_eur"] for i in items), 2)
    ueberlappung = _zahl(c.get("overlap_adjustment_eur"), 0.0, summe)
    vier = _vier(c, deckel)
    # Der faire Gesamtwert liegt zwischen Summe minus Ueberlappung und Summe —
    # sonst widerspricht sich die Karte; die anderen drei folgen der Reihenfolge.
    if summe > 0:
        fair = min(max(vier["fair_discount_eur"], round(summe - ueberlappung, 2)), summe)
        if fair != vier["fair_discount_eur"]:
            faktor = fair / vier["fair_discount_eur"] if vier["fair_discount_eur"] > 0 else 1.0
            vier = _vier({"minimum_justified_eur": vier["minimum_justified_eur"] * faktor,
                          "fair_discount_eur": fair,
                          "best_realistic_eur": vier["best_realistic_eur"] * faktor,
                          "negotiation_start_eur": vier["negotiation_start_eur"] * faktor}, deckel)
    else:
        vier = _vier({}, deckel)
    manuell_gesamt = bool(c.get("manual_review_required")) or any(i["manual_review_required"] for i in items)
    risiko = c.get("deal_risk") if c.get("deal_risk") in DEAL_RISK else "normal"
    if kp > 0 and vier["fair_discount_eur"] >= 0.5 * kp and risiko == "normal":
        risiko = "high"
    combined = {
        "sum_fair_eur": summe,
        "overlap_adjustment_eur": ueberlappung,
        **vier,
        "deal_risk": risiko,
        "manual_review_required": manuell_gesamt,
        "recommended_purchase_price_eur": round(kp - vier["fair_discount_eur"], 2)
        if kp > 0 and vier["fair_discount_eur"] > 0 else None,
    }
    argumente = [str(a)[:200] for a in (daten.get("arguments") or [])[:3] if str(a or "").strip()]
    return {"items": items, "combined": combined, "arguments": argumente}
