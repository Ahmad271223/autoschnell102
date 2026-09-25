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

PROMPT_VERSION = "abholung_v4"
PROMPT_VERSION_VERTRAG = "vertrag_v3"

KATEGORIEN = ["damage", "damage_worse", "mileage", "keys", "previous_owners",
              "equipment_missing", "equipment_defect", "tires", "documents", "hu",
              "accident_history", "warning_light", "technical", "other"]
PRIORITAETEN = ["rot", "orange", "gelb"]
DEAL_RISK = ["normal", "high", "reconsider_purchase"]
DATENLAGE = ["hoch", "mittel", "niedrig"]
# Drei Ergebnisarten (Wunsch Ahmad 25.09.2026 abends, nach dem Schadenkatalog):
#   repair_estimate        Schaden sichtbar, Reparaturweg klar -> vier Geldwerte
#   diagnosis_required     nur ein Symptom (Warnleuchte, Geraeusch, Ruckeln ...):
#                          Diagnosekosten + drei Szenarien, ausdruecklich Szenarien
#   expert_check_required  Sachverstaendiger/Fachbetrieb noetig -> keine Zahl
ARTEN = ["repair_estimate", "diagnosis_required", "expert_check_required"]

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
                                   "description": "true bei expert_check_required (Unfallfreiheit, Durchrostung tragender Teile u. ae.) — dann alle Betraege 0"},
        "assessment_kind": {"type": "string", "enum": ARTEN,
                            "description": "repair_estimate = Reparaturpreis geschaetzt; diagnosis_required = nur Symptom, Diagnose noetig (Szenarien); expert_check_required = Fachpruefung, keine Zahl"},
        "diagnosis_cost_eur": {"type": "number", "description": "nur bei diagnosis_required: Kosten der Werkstattdiagnose, sonst 0"},
        "scenario_low_eur": {"type": "number", "description": "nur bei diagnosis_required: guenstiger Reparaturfall, sonst 0"},
        "scenario_mid_eur": {"type": "number", "description": "nur bei diagnosis_required: mittlerer Reparaturfall, sonst 0"},
        "scenario_high_eur": {"type": "number", "description": "nur bei diagnosis_required: aufwendiger Reparaturfall, sonst 0"},
        "reason": {"type": "string", "description": "ein Satz, deutsch, hoechstens 14 Woerter; bei 'unbekannt' die getroffene Annahme nennen"},
    },
    "required": ["source_id", "category", "title", "price_relevant", "repair_method", "repair_estimate_eur",
                 "minimum_justified_eur", "fair_discount_eur", "best_realistic_eur", "negotiation_start_eur",
                 "manual_review_required", "assessment_kind", "diagnosis_cost_eur", "scenario_low_eur",
                 "scenario_mid_eur", "scenario_high_eur", "reason"],
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


def _szenarien(roh: Dict[str, Any], deckel: Optional[float]) -> Dict[str, float]:
    """Diagnosekosten und drei Reparaturszenarien, sortiert und gedeckelt."""
    diag = _zahl(roh.get("diagnosis_cost_eur"), 0.0, deckel)
    lo, mid, hi = sorted(_zahl(roh.get(k), 0.0, deckel) for k in
                         ("scenario_low_eur", "scenario_mid_eur", "scenario_high_eur"))
    return {"diagnosis_cost_eur": diag, "scenario_low_eur": lo, "scenario_mid_eur": mid, "scenario_high_eur": hi}


def _vier_aus_szenarien(sz: Dict[str, float]) -> Dict[str, float]:
    """Bei 'Diagnose erforderlich' folgen die vier Werte den Szenarien:
    mindestens = Diagnosekosten (sonst guenstiger Fall), fair = guenstiger Fall,
    sehr gut = mittlerer Fall, Start = aufwendiger Fall. Keine 60/40-Deckel —
    die Spanne IST die Aussage."""
    lo, mid, hi = sz["scenario_low_eur"], sz["scenario_mid_eur"], sz["scenario_high_eur"]
    mn = sz["diagnosis_cost_eur"] if sz["diagnosis_cost_eur"] > 0 else lo
    return {"minimum_justified_eur": min(mn, lo) if lo > 0 else mn, "fair_discount_eur": lo,
            "best_realistic_eur": max(mid, lo), "negotiation_start_eur": max(hi, mid, lo)}


# Deterministische Risiko-Stufen (Review 25.09.2026 abends) — die KI kann
# das Risiko nur ERHOEHEN, nie senken:
#   high                 fairer Nachlass >= 50 % des Preises
#                        ODER aufwendige Diagnose-Szenarien zusammen >= 25 %
#                        ODER mindestens eine Fachpruefungs-Position (Betrag unbekannt)
#   reconsider_purchase  fairer Nachlass >= 60 % ODER Szenarien zusammen >= 60 %
#                        ODER fair + Szenarien >= 75 % des Preises
RISIKO_HIGH_FAIR, RISIKO_HIGH_SZENARIEN = 0.50, 0.25
RISIKO_RECONSIDER_FAIR, RISIKO_RECONSIDER_SZENARIEN, RISIKO_RECONSIDER_SUMME = 0.60, 0.60, 0.75


def deal_risk_stufe(ki_wert: Any, *, kaufpreis: float, fair: float, szenarien_hoch: float, experten: int) -> str:
    stufe = ki_wert if ki_wert in DEAL_RISK else "normal"
    rang = {"normal": 0, "high": 1, "reconsider_purchase": 2}
    eigen = "normal"
    if kaufpreis > 0:
        if (fair >= RISIKO_RECONSIDER_FAIR * kaufpreis or szenarien_hoch >= RISIKO_RECONSIDER_SZENARIEN * kaufpreis
                or fair + szenarien_hoch >= RISIKO_RECONSIDER_SUMME * kaufpreis):
            eigen = "reconsider_purchase"
        elif fair >= RISIKO_HIGH_FAIR * kaufpreis or szenarien_hoch >= RISIKO_HIGH_SZENARIEN * kaufpreis:
            eigen = "high"
    if experten > 0 and rang[eigen] < 1:
        eigen = "high"
    return eigen if rang[eigen] > rang[stufe] else stufe


def datenlage_anpassen(ergebnis: Dict[str, Any], lage: str) -> str:
    """Eine Diagnose- oder Fachpruefungsposition drueckt 'hoch' auf 'mittel':
    die Zahlen sind dann Szenarien, keine Messung."""
    arten = {i.get("assessment_kind") for i in ergebnis.get("items") or []}
    if lage == "hoch" and (arten & {"diagnosis_required", "expert_check_required"}):
        return "mittel"
    return lage


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
        art = roh.get("assessment_kind") if roh.get("assessment_kind") in ARTEN else (
            "expert_check_required" if manuell else "repair_estimate")
        if art == "expert_check_required":
            manuell = True
        szen = {"diagnosis_cost_eur": 0.0, "scenario_low_eur": 0.0, "scenario_mid_eur": 0.0, "scenario_high_eur": 0.0}
        if art == "diagnosis_required" and not manuell:
            szen = _szenarien(roh, deckel)
            vier = _vier_aus_szenarien(szen) if szen["scenario_high_eur"] > 0 else _vier(roh, deckel)
        else:
            vier = _vier({} if manuell else roh, deckel)
        items.append({
            "assessment_kind": art,
            **szen,
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
    # Die Gesamtwerte duerfen nicht unter der Summe der Einzelwerte (abzueglich
    # Ueberschneidung) liegen — sonst frisst der 60/40-Deckel die Szenarien
    # einer Diagnose-Position (Einzel-Start 3.500, Gesamt-Start 1.000).
    if summe > 0:
        for k in ("best_realistic_eur", "negotiation_start_eur"):
            boden = round(max(0.0, sum(i[k] for i in items) - ueberlappung), 2)
            vier[k] = max(vier[k], boden)
        vier["negotiation_start_eur"] = max(vier["negotiation_start_eur"], vier["best_realistic_eur"])
        if deckel is not None:
            vier["best_realistic_eur"] = min(vier["best_realistic_eur"], deckel)
            vier["negotiation_start_eur"] = min(vier["negotiation_start_eur"], deckel)
    manuell_gesamt = bool(c.get("manual_review_required")) or any(i["manual_review_required"] for i in items)
    # Diagnose-Positionen: die Spanne zwischen guenstigem und aufwendigem Fall
    # ist das Unsichere an dieser Empfehlung — wird gesondert ausgewiesen.
    diag = [i for i in items if i["assessment_kind"] == "diagnosis_required"]
    unsicher = round(sum(max(0.0, i["scenario_high_eur"] - i["scenario_low_eur"]) for i in diag), 2)
    experten = sum(1 for i in items if i["assessment_kind"] == "expert_check_required")
    risiko = deal_risk_stufe(c.get("deal_risk"), kaufpreis=kp, fair=vier["fair_discount_eur"],
                             szenarien_hoch=sum(i["scenario_high_eur"] for i in diag), experten=experten)
    combined = {
        "sum_fair_eur": summe,
        "overlap_adjustment_eur": ueberlappung,
        **vier,
        "deal_risk": risiko,
        "manual_review_required": manuell_gesamt,
        "diagnosis_items": len(diag),
        "expert_items": experten,
        "uncertain_eur": unsicher,
        "recommended_purchase_price_eur": round(kp - vier["fair_discount_eur"], 2)
        if kp > 0 and vier["fair_discount_eur"] > 0 else None,
    }
    argumente = [str(a)[:200] for a in (daten.get("arguments") or [])[:3] if str(a or "").strip()]
    return {"items": items, "combined": combined, "arguments": argumente}
