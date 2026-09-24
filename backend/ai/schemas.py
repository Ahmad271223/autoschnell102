# -*- coding: utf-8 -*-
"""JSON-Schema der KI-Antwort und die Nachpruefung im Backend.

Die KI bekommt das Schema ueber output_config.format — die Antwort IST damit
gueltiges JSON dieser Form. Trotzdem prueft `bereinigen` jede Zahl: keine
absurden Spannen (Wunsch Ahmad: kein "100-600 EUR"), kein Nachlass ueber dem
Kaufpreis, Sicherheit 0..1. Fehlt der KI eine Angabe, meldet sie
needs_information statt einer aufgeblasenen Spanne."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

PROMPT_VERSION = "abholung_v1"

# Ein Nachlassbereich darf hoechstens +-SPANNE_MAX um den Hauptwert liegen.
SPANNE_MAX = 0.20
SICHERHEIT_MIN_FUER_ZAHL = 0.35

KATEGORIEN = ["damage", "damage_worse", "mileage", "keys", "previous_owners",
              "equipment_missing", "equipment_defect", "tires", "documents", "hu",
              "accident_history", "warning_light", "technical", "other"]
PRIORITAETEN = ["rot", "orange", "gelb"]

_POSITION = {
    "type": "object",
    "properties": {
        "source_id": {"type": "string",
                      "description": "id der Abweichung/des Schadens aus der Eingabe"},
        "category": {"type": "string", "enum": KATEGORIEN},
        "title": {"type": "string", "description": "kurz, deutsch, z. B. 'Delle Kotflügel vorne rechts'"},
        "price_relevant": {"type": "boolean"},
        "priority": {"type": "string", "enum": PRIORITAETEN},
        "repair_method": {"type": "string",
                          "description": "vermutetes Reparaturverfahren, deutsch, kurz; leer wenn keins"},
        "repair_estimate_eur": {"type": "number", "description": "geschaetzte Reparaturkosten, 0 wenn nicht anwendbar"},
        "recommended_discount_eur": {"type": "number"},
        "discount_min_eur": {"type": "number"},
        "discount_max_eur": {"type": "number"},
        "confidence": {"type": "number", "description": "0..1"},
        "manual_review_required": {"type": "boolean",
                                   "description": "true bei Unfallfreiheit, Warnleuchten ohne Diagnose u. ae. — dann keine Zahl"},
        "reason": {"type": "string", "description": "ein Satz, deutsch"},
    },
    "required": ["source_id", "category", "title", "price_relevant", "priority", "repair_method",
                 "repair_estimate_eur", "recommended_discount_eur", "discount_min_eur",
                 "discount_max_eur", "confidence", "manual_review_required", "reason"],
    "additionalProperties": False,
}

_FRAGE = {
    "type": "object",
    "properties": {
        "source_id": {"type": "string"},
        "question": {"type": "string", "description": "eine konkrete Frage an den Fahrer, deutsch"},
        "options": {"type": "array", "items": {"type": "string"}, "description": "2-4 kurze Antwortmoeglichkeiten"},
    },
    "required": ["source_id", "question", "options"],
    "additionalProperties": False,
}

ANTWORT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "items": {"type": "array", "items": _POSITION},
        "combined": {
            "type": "object",
            "properties": {
                "sum_of_items_eur": {"type": "number"},
                "overlap_adjustment_eur": {"type": "number",
                                           "description": "Abzug fuer ueberlappende Reparaturen (>= 0)"},
                "recommended_discount_eur": {"type": "number"},
                "discount_min_eur": {"type": "number"},
                "discount_max_eur": {"type": "number"},
                "negotiation_start_eur": {"type": "number",
                                          "description": "Nachlass, mit dem der Chef das Gespraech beginnt (etwas ueber dem empfohlenen)"},
                "confidence": {"type": "number"},
                "manual_review_required": {"type": "boolean"},
            },
            "required": ["sum_of_items_eur", "overlap_adjustment_eur", "recommended_discount_eur",
                         "discount_min_eur", "discount_max_eur", "negotiation_start_eur",
                         "confidence", "manual_review_required"],
            "additionalProperties": False,
        },
        "needs_information": {"type": "array", "items": _FRAGE},
        "arguments": {"type": "array", "items": {"type": "string"},
                      "description": "hoechstens 4 kurze Verhandlungsargumente fuer den Verkaeufer, deutsch"},
    },
    "required": ["items", "combined", "needs_information", "arguments"],
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


def _spanne(haupt: float, unten: float, oben: float) -> tuple:
    """Enge Spanne um den Hauptwert erzwingen: hoechstens +-SPANNE_MAX,
    nie unter 0, nie unter/ueber dem Hauptwert verdreht."""
    if haupt <= 0:
        return 0.0, 0.0
    lo = max(0.0, min(unten, haupt))
    hi = max(oben, haupt)
    lo = max(lo, round(haupt * (1 - SPANNE_MAX)))
    hi = min(hi, round(haupt * (1 + SPANNE_MAX)))
    if hi < haupt:
        hi = haupt
    if lo > haupt:
        lo = haupt
    return float(lo), float(hi)


def bereinigen(daten: Dict[str, Any], *, kaufpreis: Optional[float]) -> Dict[str, Any]:
    """Zahlen absichern, Spannen eng ziehen, Summen plausibel halten.
    Liefert eine neue Struktur (das Original bleibt fuer die Ablage)."""
    kp = _zahl(kaufpreis) if kaufpreis else 0.0
    deckel = kp * 0.9 if kp > 0 else None    # nie mehr als 90 % des Kaufpreises
    items: List[Dict[str, Any]] = []
    for roh in daten.get("items") or []:
        if not isinstance(roh, dict):
            continue
        manuell = bool(roh.get("manual_review_required"))
        sicher = _zahl(roh.get("confidence"), 0.0, 1.0)
        haupt = 0.0 if manuell else _zahl(roh.get("recommended_discount_eur"), 0.0, deckel)
        if sicher < SICHERHEIT_MIN_FUER_ZAHL and not manuell:
            # Zu unsicher fuer eine Zahl: als Frage/Hinweis, nicht als Betrag.
            manuell = True
            haupt = 0.0
        lo, hi = _spanne(haupt, _zahl(roh.get("discount_min_eur")), _zahl(roh.get("discount_max_eur")))
        prio = roh.get("priority") if roh.get("priority") in PRIORITAETEN else "gelb"
        if manuell and prio == "gelb":
            prio = "rot" if roh.get("category") in ("accident_history", "warning_light", "technical") else "orange"
        items.append({
            "source_id": str(roh.get("source_id") or ""),
            "category": roh.get("category") if roh.get("category") in KATEGORIEN else "other",
            "title": str(roh.get("title") or "")[:120],
            "price_relevant": bool(roh.get("price_relevant")) and (haupt > 0 or manuell),
            "priority": prio,
            "repair_method": str(roh.get("repair_method") or "")[:80],
            "repair_estimate_eur": _zahl(roh.get("repair_estimate_eur"), 0.0, deckel),
            "recommended_discount_eur": haupt,
            "discount_min_eur": lo,
            "discount_max_eur": hi,
            "confidence": sicher,
            "manual_review_required": manuell,
            "reason": str(roh.get("reason") or "")[:300],
        })
    c = daten.get("combined") or {}
    summe = round(sum(i["recommended_discount_eur"] for i in items), 2)
    ueberlappung = _zahl(c.get("overlap_adjustment_eur"), 0.0, summe)
    gesamt = _zahl(c.get("recommended_discount_eur"), 0.0, deckel)
    # Die Gesamtempfehlung darf die Einzelsumme nicht uebersteigen und nicht
    # unter Summe minus Ueberlappung fallen — sonst widerspricht sich die Karte.
    if summe > 0:
        gesamt = min(max(gesamt, round(summe - ueberlappung, 2)), summe)
    else:
        gesamt = 0.0
    lo, hi = _spanne(gesamt, _zahl(c.get("discount_min_eur")), _zahl(c.get("discount_max_eur")))
    einstieg = _zahl(c.get("negotiation_start_eur"), gesamt, deckel)
    if gesamt > 0:
        einstieg = max(einstieg, gesamt)
        einstieg = min(einstieg, round(gesamt * 1.35))
    else:
        einstieg = 0.0
    manuell_gesamt = bool(c.get("manual_review_required")) or any(i["manual_review_required"] for i in items)
    combined = {
        "sum_of_items_eur": summe,
        "overlap_adjustment_eur": ueberlappung,
        "recommended_discount_eur": gesamt,
        "discount_min_eur": lo,
        "discount_max_eur": hi,
        "negotiation_start_eur": einstieg,
        "confidence": _zahl(c.get("confidence"), 0.0, 1.0),
        "manual_review_required": manuell_gesamt,
        "recommended_purchase_price_eur": round(kp - gesamt, 2) if kp > 0 and gesamt > 0 else None,
    }
    fragen = []
    for f in (daten.get("needs_information") or [])[:4]:
        if isinstance(f, dict) and str(f.get("question") or "").strip():
            fragen.append({"source_id": str(f.get("source_id") or ""),
                           "question": str(f["question"])[:200],
                           "options": [str(o)[:40] for o in (f.get("options") or [])[:4]]})
    argumente = [str(a)[:200] for a in (daten.get("arguments") or [])[:4] if str(a or "").strip()]
    return {"items": items, "combined": combined, "needs_information": fragen,
            "arguments": argumente}
