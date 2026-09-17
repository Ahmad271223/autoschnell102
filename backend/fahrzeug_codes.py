# -*- coding: utf-8 -*-
"""Getriebe und Kraftstoff: EINE Zuordnung fuer alle Quellen und beide Portale.

Befund 17.09.2026 (Ahmad: "ab und zu klappt Getriebe 1:1 nicht"): Der
mobile.de-Link setzte tr= und ft= mit dem gespeicherten Rohwert. Nur der
Kleinanzeigen-Parser speicherte mobile.de-Codes (AUTOMATIC_GEAR, DIESEL).
mobile.de ueber Apify lieferte "Automatic" / "Manual gearbox" (gespeichert als
AUTOMATIC / MANUAL GEARBOX), AutoScout24 "Automatik" / "Schaltgetriebe" /
"Benzin" — diese Werte kennt mobile.de nicht und filterte STILL ohne Getriebe
bzw. Kraftstoff. Halbautomatik wurde in beiden Links als Automatik gefiltert.

Jetzt gilt:
  * getriebe_code / kraftstoff_code erkennen Codes und Beschriftungen in
    Deutsch und Englisch, egal in welcher Schreibweise.
  * Beide Link-Bauer leiten den Code beim Bauen ab — auch fuer Fahrzeuge und
    Zwischenspeicher-Eintraege, die noch mit Rohwerten gespeichert sind.
  * Die Parser speichern ab sofort gleich den richtigen Code.
  * Kann eine Regel "1:1 uebernehmen" nicht umgesetzt werden (Inserat ohne
    Angabe oder unbekannter Wert), sagt filter_hinweise das dem Nutzer —
    nie wieder ein stiller Link ohne Filter.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Iterable, List, Optional

# mobile.de-Codes (Suchparameter tr= / ft=)
GETRIEBE_CODES = ("MANUAL_GEAR", "AUTOMATIC_GEAR", "SEMIAUTOMATIC_GEAR")
KRAFTSTOFF_CODES = ("PETROL", "DIESEL", "ELECTRICITY", "HYBRID", "HYBRID_DIESEL",
                    "LPG", "CNG", "HYDROGENIUM", "ETHANOL", "OTHER")

# AutoScout24-Codes (Suchparameter gear= / fuel=)
AUTOSCOUT_GETRIEBE = {"AUTOMATIC_GEAR": "A", "MANUAL_GEAR": "M", "SEMIAUTOMATIC_GEAR": "S"}
AUTOSCOUT_KRAFTSTOFF = {"PETROL": "B", "DIESEL": "D", "ELECTRICITY": "E", "HYBRID": "2",
                        "HYBRID_DIESEL": "3", "LPG": "L", "CNG": "C", "HYDROGENIUM": "H"}

GETRIEBE_TEXT = {"MANUAL_GEAR": "Schaltgetriebe", "AUTOMATIC_GEAR": "Automatik",
                 "SEMIAUTOMATIC_GEAR": "Halbautomatik"}
KRAFTSTOFF_TEXT = {"PETROL": "Benzin", "DIESEL": "Diesel", "ELECTRICITY": "Elektro",
                   "HYBRID": "Hybrid (Benzin/Elektro)", "HYBRID_DIESEL": "Hybrid (Diesel/Elektro)",
                   "LPG": "Autogas (LPG)", "CNG": "Erdgas (CNG)", "HYDROGENIUM": "Wasserstoff",
                   "ETHANOL": "Ethanol", "OTHER": "Andere"}


def _norm(wert) -> str:
    """Kleinbuchstaben ohne Akzente, nur a-z und 0-9."""
    if wert is None:
        return ""
    s = unicodedata.normalize("NFD", str(wert))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _ein_getriebe(wert) -> Optional[str]:
    n = _norm(wert)
    if not n:
        return None
    # Reihenfolge wichtig: "Halbautomatik" / "SEMIAUTOMATIC_GEAR" enthalten "auto".
    if "halbauto" in n or n.startswith("semi"):
        return "SEMIAUTOMATIC_GEAR"
    if ("auto" in n or "doppelkupplung" in n or "dsg" in n or "cvt" in n
            or "stufenlos" in n or "tiptronic" in n):
        return "AUTOMATIC_GEAR"
    if "schalt" in n or "manu" in n:
        return "MANUAL_GEAR"
    return None


def getriebe_code(*werte) -> Optional[str]:
    """mobile.de-Getriebecode aus dem ersten erkennbaren Wert (Code oder
    Beschriftung, deutsch oder englisch). None, wenn nichts erkannt wird."""
    for wert in werte:
        code = _ein_getriebe(wert)
        if code:
            return code
    return None


def _ein_kraftstoff(wert) -> Optional[str]:
    roh = str(wert or "").strip()
    if roh.upper() in KRAFTSTOFF_CODES:
        return roh.upper()
    n = _norm(roh)
    if not n:
        return None
    elektrisch = "elektr" in n or "electr" in n or "strom" in n
    verbrenner = any(t in n for t in ("benzin", "petrol", "gasoline", "diesel"))
    # Reihenfolge wichtig: "Hybrid (Diesel/Elektro)" enthaelt "diesel" und
    # "elektro", "Benzin/LPG" enthaelt "benzin".
    if "hybrid" in n or "plugin" in n or (elektrisch and verbrenner):
        return "HYBRID_DIESEL" if "diesel" in n else "HYBRID"
    if "wasserstoff" in n or "hydrogen" in n:
        return "HYDROGENIUM"
    if "lpg" in n or "autogas" in n or "flussiggas" in n:
        return "LPG"
    if "cng" in n or "erdgas" in n or "naturalgas" in n:
        return "CNG"
    if "ethanol" in n or "e85" in n:
        return "ETHANOL"
    if elektrisch:
        return "ELECTRICITY"
    if "diesel" in n:
        return "DIESEL"
    if "benzin" in n or "petrol" in n or "gasoline" in n or n.startswith("super"):
        return "PETROL"
    if n in ("andere", "sonstige", "sonstiges", "other", "others"):
        return "OTHER"
    return None


def kraftstoff_code(*werte) -> Optional[str]:
    """mobile.de-Kraftstoffcode aus dem ersten erkennbaren Wert. None, wenn
    nichts erkannt wird."""
    for wert in werte:
        code = _ein_kraftstoff(wert)
        if code:
            return code
    return None


def autoscout_getriebe(*werte) -> str:
    """AutoScout24-Getriebecode (A/M/S) oder "" (kein Filter)."""
    return AUTOSCOUT_GETRIEBE.get(getriebe_code(*werte) or "", "")


def autoscout_kraftstoff(*werte) -> str:
    """AutoScout24-Kraftstoffcode oder "" (kein Filter)."""
    return AUTOSCOUT_KRAFTSTOFF.get(kraftstoff_code(*werte) or "", "")


def _erster_text(werte: Iterable) -> str:
    for w in werte:
        if w not in (None, "") and str(w).strip():
            return str(w).strip()
    return ""


def filter_hinweise(vehicle: dict, rules: dict) -> List[str]:
    """Hinweise fuer den Nutzer, wenn "1:1 uebernehmen" fuer Getriebe oder
    Kraftstoff NICHT in die Links kommt — statt eines stillen Links ohne Filter."""
    vehicle, rules = vehicle or {}, rules or {}
    hinweise: List[str] = []
    if (rules.get("gearbox") or {}).get("mode") == "exact":
        werte = (vehicle.get("gearbox"), vehicle.get("gearbox_label"))
        if not getriebe_code(*werte):
            roh = _erster_text((vehicle.get("gearbox_label"), vehicle.get("gearbox")))
            hinweise.append(
                f"Getriebe „{roh}“ nicht erkannt — mobile.de und AutoScout24 zeigen alle Getriebearten."
                if roh else
                "Im Inserat ist kein Getriebe angegeben — mobile.de und AutoScout24 zeigen alle Getriebearten.")
    if (rules.get("fuel") or {}).get("mode") == "exact":
        werte = (vehicle.get("fuel"), vehicle.get("fuel_label"))
        code = kraftstoff_code(*werte)
        roh = _erster_text((vehicle.get("fuel_label"), vehicle.get("fuel")))
        if not code:
            hinweise.append(
                f"Kraftstoff „{roh}“ nicht erkannt — mobile.de und AutoScout24 zeigen alle Kraftstoffarten."
                if roh else
                "Im Inserat ist kein Kraftstoff angegeben — mobile.de und AutoScout24 zeigen alle Kraftstoffarten.")
        elif code not in AUTOSCOUT_KRAFTSTOFF:
            hinweise.append(
                f"Kraftstoff „{roh or KRAFTSTOFF_TEXT.get(code, code)}“ filtert nur mobile.de — "
                "der AutoScout-Link zeigt alle Kraftstoffarten.")
    return hinweise
