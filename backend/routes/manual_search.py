"""Manuelle Suche: Marke + Modell + Filter → mobile.de + Autoscout24 URLs."""
import unicodedata
import re
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from autoscout_service import _load_data as _load_autoscout
from autoscout_service import build_search_url as build_autoscout_url
from deps import current_user, db, require_active_sub
from mobile_service import DEFAULT_RULES
from mobile_service import build_search_url as build_mobile_url

router = APIRouter()


def _norm(s: str) -> str:
    nfd = unicodedata.normalize("NFD", s)
    no_acc = "".join(c for c in nfd if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]", "", no_acc.lower())


# German label → mobile.de API code
_FUEL_TO_MOBILE = {
    "benzin": "PETROL",
    "diesel": "DIESEL",
    "elektro": "ELECTRICITY",
    "electric": "ELECTRICITY",
    "hybrid": "HYBRID",
    "pluginhybrid": "HYBRID",
    "lpg": "LPG",
    "autogas": "LPG",
    "cng": "CNG",
    "erdgas": "CNG",
    "wasserstoff": "HYDROGENIUM",
}

_GEAR_TO_MOBILE = {
    "automatik": "AUTOMATIC_GEAR",
    "automatic": "AUTOMATIC_GEAR",
    "manuell": "MANUAL_GEAR",
    "manual": "MANUAL_GEAR",
    "schaltgetriebe": "MANUAL_GEAR",
}


def _fuel_code(label: str) -> str:
    """German/English label → mobile.de fuel code."""
    if not label:
        return ""
    n = _norm(label)
    for key, code in _FUEL_TO_MOBILE.items():
        if key in n:
            return code
    return ""


def _gear_code(label: str) -> str:
    """German/English label → mobile.de gearbox code."""
    if not label:
        return ""
    n = _norm(label)
    for key, code in _GEAR_TO_MOBILE.items():
        if key in n:
            return code
    return ""


# ---------- Models ----------
class ManualSearchIn(BaseModel):
    make: str
    model: Optional[str] = None
    # Filter-Eingaben:
    ez_from: Optional[int] = None   # Jahr (z.B. 2018)
    ez_to: Optional[int] = None
    km_max: Optional[int] = None
    km_min: Optional[int] = None
    kw: Optional[int] = None
    ps: Optional[int] = None
    kw_tolerance: Optional[int] = Field(
        default=10,
        description="±kW-Toleranz für die Suche (Default: 10)",
    )
    fuel: Optional[str] = None      # z.B. "Benzin", "Diesel", "Elektro", "Hybrid"
    gearbox: Optional[str] = None   # "Automatik" / "Manuell"


# ---------- Endpoints ----------
@router.get("/manual/makes")
async def list_makes(_=Depends(current_user)):
    """Komplette Marken+Modelle-Liste (Quelle: autoscout_makes.json, 288 Marken).
    Wird vom Frontend für die Make/Model-Picker geladen — einmal pro Session
    cached der Browser das Resultat."""
    data = _load_autoscout()
    out = []
    for m in data:
        models = m.get("models") or []
        # Modelle nach Name sortieren — angenehmer beim Suchen
        models_sorted = sorted(
            [{"id": mm["modelId"], "name": mm["modelName"]} for mm in models],
            key=lambda x: x["name"].lower(),
        )
        out.append({
            "id": m["makeId"],
            "name": m["makeName"],
            "models": models_sorted,
        })
    out.sort(key=lambda x: x["name"].lower())
    return out


@router.post("/manual/search")
async def manual_search(body: ManualSearchIn, user=Depends(require_active_sub)):
    # require_active_sub prueft Rolle UND aktives Abo — vorher genuegte die
    # Rolle, wodurch die Bezahlschranke per direktem API-Aufruf umgangen
    # werden konnte.
    """Wandelt die manuelle Eingabe in ein „virtuelles Fahrzeug" um und nutzt
    die existierenden mobile_service / autoscout_service Url-Builder.
    Übernimmt damit automatisch die Vergleichs-Regeln des Händlers (KM-Toleranz,
    Leistungs-Toleranz, Schaden-Ausschluss, …).
    """
    from deps import effective_dealer
    dealer = await effective_dealer(user) or {}
    # Dasselbe Regelpaket wie der Vergleich (PR-Review 09/2026): vorher
    # nutzte die manuelle Suche IMMER die Inland-Regeln — das aktive
    # Export-Profil samt export_rules wurde komplett ignoriert.
    if (dealer.get("active_profile") or "inland") == "export":
        from mobile_service import DEFAULT_EXPORT_RULES
        base_rules = dealer.get("export_rules") or DEFAULT_EXPORT_RULES
    else:
        base_rules = dealer.get("comparison_rules") or DEFAULT_RULES

    # ---- Regeln aus dem manuellen Formular überschreiben ----
    rules = {**base_rules}

    # Erstzulassung: explizite Von/Bis-Range
    if body.ez_from or body.ez_to:
        rules["first_registration"] = {
            "mode": "year_range",
            "from": body.ez_from or None,
            "to": body.ez_to or None,
        }
    else:
        rules["first_registration"] = {"mode": "ignore"}

    # KM
    if body.km_min and body.km_max:
        rules["mileage"] = {"mode": "custom", "min": body.km_min, "max": body.km_max}
    elif body.km_max:
        rules["mileage"] = {"mode": "custom", "min": 0, "max": body.km_max}
    elif body.km_min:
        rules["mileage"] = {"mode": "custom", "min": body.km_min, "max": None}
    else:
        rules["mileage"] = {"mode": "ignore"}

    # Leistung
    if body.kw or body.ps:
        rules["power"] = {
            "mode": "tolerance_kw",
            "value": int(body.kw_tolerance or 10),
        }
    else:
        rules["power"] = {"mode": "ignore"}

    # Kraftstoff & Getriebe — mobile.de-Codes für den URL-Builder
    fuel_code = _fuel_code(body.fuel or "")
    gear_code = _gear_code(body.gearbox or "")
    rules["fuel"] = {"mode": "exact" if fuel_code else "ignore"}
    rules["gearbox"] = {"mode": "exact" if gear_code else "ignore"}

    # Schaden raus (Standard): unfallfrei
    rules.setdefault("damage", {"mode": "no_accident"})

    # ---- „Virtuelles Fahrzeug" zusammenbauen, damit die Builder ihn füttern können
    kw = body.kw
    ps = body.ps
    if not kw and ps:
        kw = int(round(int(ps) / 1.359621617))
    if not ps and kw:
        ps = int(round(int(kw) * 1.359621617))

    # Erstzulassung: für _parse_first_registration reicht ein beliebiges Jahr
    ez_year = body.ez_from or body.ez_to
    first_reg = f"01/{ez_year}" if ez_year else None

    vehicle = {
        "make": body.make,
        "make_label": body.make,
        "model": body.model or "",
        "model_label": body.model or "",
        "first_registration": first_reg,
        "mileage": body.km_max or body.km_min or 0,
        "power_kw": kw,
        "power_ps": ps,
        # mobile.de erwartet API-Codes (PETROL, DIESEL, AUTOMATIC_GEAR, …);
        # autoscout24-Builder liest fuel_label und mappt selbst.
        "fuel": fuel_code,
        "fuel_label": body.fuel or "",
        "gearbox": gear_code,
        "gearbox_label": body.gearbox or "",
    }

    mobile_url = build_mobile_url(vehicle, rules)
    autoscout_url = build_autoscout_url(vehicle, rules)

    return {
        "mobile_url": mobile_url,
        "autoscout_url": autoscout_url,
        "rules_applied": rules,
    }
