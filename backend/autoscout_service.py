"""AutoScout24 search-URL builder.

Generates a search URL on autoscout24.de that matches a given vehicle using
the dealer's comparison rules. Reuses the same rule-shape as the mobile.de
builder so a single set of rules works for both portals.

URL format example:
    https://www.autoscout24.de/lst/mercedes-benz?atype=C
        &cat=ma47mo18486
        &cy=D
        &damaged_listing=exclude
        &fregfrom=2015
        &kmto=180000
        &ocs_listing=include
        &powertype=kw
        &sort=price
        &desc=0
        &ustate=N%2CU
"""
from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import urlencode

_DATA_PATH = Path(__file__).parent / "autoscout_makes.json"


# ---------- Loaders ----------
@lru_cache(maxsize=1)
def _load_data() -> list:
    """Loads the autoscout-makes-with-models JSON exactly once."""
    if not _DATA_PATH.exists():
        return []
    try:
        with _DATA_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _norm(s: str) -> str:
    """Normalised, lowercase, no diacritics, only [a-z0-9] — used for fuzzy match."""
    if not s:
        return ""
    nfd = unicodedata.normalize("NFD", s)
    no_acc = "".join(c for c in nfd if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]", "", no_acc.lower())


def _slug(s: str) -> str:
    """URL-Slug für `/lst/<slug>`: lowercase, Bindestriche, keine Diakritika."""
    if not s:
        return ""
    nfd = unicodedata.normalize("NFD", s)
    no_acc = "".join(c for c in nfd if unicodedata.category(c) != "Mn")
    out = re.sub(r"[^a-z0-9]+", "-", no_acc.lower()).strip("-")
    return out


# ---------- Lookups ----------
def _find_make(make_name: str) -> Optional[dict]:
    """Sucht eine Marke per Name (fuzzy, case-/diakritik-insensitiv)."""
    if not make_name:
        return None
    target = _norm(make_name)
    if not target:
        return None
    data = _load_data()
    # 1) Exakter normalisierter Match
    for m in data:
        if _norm(m.get("makeName", "")) == target:
            return m
    # 2) Marke fängt mit Suchbegriff an (z.B. "VW" → "Volkswagen" geht NICHT,
    #    aber "Mercedes" → "Mercedes-Benz" geht). Bekannte Aliase mappen wir
    #    unten via _MAKE_ALIASES.
    alias = _MAKE_ALIASES.get(target)
    if alias:
        for m in data:
            if _norm(m.get("makeName", "")) == _norm(alias):
                return m
    for m in data:
        n = _norm(m.get("makeName", ""))
        if n.startswith(target):
            return m
    return None


_MAKE_ALIASES = {
    # User-/mobile.de-Schreibweisen → Autoscout-Name
    "vw": "Volkswagen",
    "merc": "Mercedes-Benz",
    "mercedesbenz": "Mercedes-Benz",
    "mercedes": "Mercedes-Benz",
    "rangerover": "Land Rover",
    "rolls": "Rolls-Royce",
    "rollsroyce": "Rolls-Royce",
    "astonmartin": "Aston Martin",
    "alfaromeo": "Alfa Romeo",
    "lambo": "Lamborghini",
}


def _find_model(make: dict, model_name: str) -> Optional[dict]:
    """Sucht ein Modell innerhalb einer Marke. Wir versuchen erst exakte
    Treffer, dann „startswith" (z.B. „E 220 d" → Modell „E 220" / „E-Klasse"
    je nach Autoscout-Schreibweise), dann den ersten enthaltenden Eintrag."""
    if not model_name:
        return None
    target = _norm(model_name)
    if not target:
        return None
    models = make.get("models") or []
    # 1) Exakt
    for m in models:
        if _norm(m.get("modelName", "")) == target:
            return m
    # 2) Autoscout-Eintrag fängt mit unserem Modell an (z.B. "C 220" ∈ "C")
    candidates = []
    for m in models:
        n = _norm(m.get("modelName", ""))
        if n and (n.startswith(target) or target.startswith(n)):
            candidates.append((m, n))
    if candidates:
        # Den spezifischsten (längsten Namen) nehmen
        candidates.sort(key=lambda kv: len(kv[1]), reverse=True)
        return candidates[0][0]
    # 3) substring match (vorsichtig — nur wenn unique-ish)
    contains = [m for m in models if target in _norm(m.get("modelName", ""))]
    if len(contains) == 1:
        return contains[0]
    return None


# ---------- Helpers ----------
def kw_to_ps(kw: int) -> int:
    return int(round(int(kw) * 1.359621617))


def ps_to_kw(ps: int) -> int:
    return int(round(int(ps) / 1.359621617))


def _parse_first_registration(raw) -> Optional[int]:
    """Extracts the YEAR from various inputs: '03/2021', '2021', 2021, ..."""
    if raw is None:
        return None
    s = str(raw).strip()
    m = re.search(r"(\d{4})", s)
    if not m:
        return None
    y = int(m.group(1))
    return y if 1900 <= y <= 2100 else None


# ---------- Public: URL builder ----------
def build_search_url(vehicle: dict, rules: dict) -> str:
    """Erzeugt eine AutoScout24-Suchurl, die zum übergebenen Fahrzeug passt.

    Akzeptiert die gleiche Rules-Struktur wie `mobile_service.build_search_url`,
    damit der Händler EIN Regelwerk pflegt und beide Portale gleich gefiltert
    werden. Unbekannte Marken/Modelle werden weggelassen — der User landet
    dann auf einer breiteren Suche statt einer „404"-leeren.
    """
    rules = rules or {}

    # ---- Marke + Modell -------------------------------------------------
    make_name = (
        vehicle.get("make_label") or vehicle.get("make")
        or vehicle.get("brand") or ""
    )
    model_name = (
        vehicle.get("model_label") or vehicle.get("model_description")
        or vehicle.get("model") or ""
    )

    make = _find_make(make_name)
    model = _find_model(make, model_name) if make else None

    # Slug für die Pfad-Komponente:
    if make:
        slug = _slug(make["makeName"])
    elif make_name:
        slug = _slug(make_name)
    else:
        slug = ""  # generische Suche

    base = "https://www.autoscout24.de"
    path = f"/lst/{slug}" if slug else "/lst"

    # ---- Query-Parameter ------------------------------------------------
    params: list = [
        ("atype", "C"),       # Car
        ("cy", "D"),          # Country: Germany
    ]

    # Marke+Modell-Kombi (mawXmoY) — nur wenn beide bekannt
    if make and model:
        params.append(("cat", f"ma{make['makeId']}mo{model['modelId']}"))
    elif make:
        params.append(("cat", f"ma{make['makeId']}"))

    # Erstzulassung (fregfrom / fregto)
    fr_year = _parse_first_registration(vehicle.get("first_registration"))
    fr_rule = rules.get("first_registration", {"mode": "older_exact", "years": 1})
    if fr_rule.get("mode") == "year_range":
        from_y = fr_rule.get("from")
        to_y = fr_rule.get("to")
        if from_y:
            params.append(("fregfrom", str(from_y)))
        if to_y:
            params.append(("fregto", str(to_y)))
    elif fr_year and fr_rule.get("mode") != "ignore":
        mode = fr_rule.get("mode")
        if mode == "exact":
            params.append(("fregfrom", str(fr_year)))
            params.append(("fregto", str(fr_year)))
        elif mode == "older_exact":
            x = int(fr_rule.get("years", 1))
            params.append(("fregfrom", str(fr_year - x)))

    # Kilometer (kmfrom / kmto)
    km = vehicle.get("mileage")
    if isinstance(km, str):
        # akzeptiert "180.000" oder "180000"
        try:
            km = int(re.sub(r"\D", "", km))
        except Exception:
            km = None
    km_rule = rules.get("mileage", {"mode": "plus", "value": 30000})
    if km and km_rule.get("mode") != "ignore":
        mode = km_rule.get("mode")
        v = int(km_rule.get("value", 30000))
        if mode == "exact":
            params.append(("kmto", str(km)))
        elif mode == "plus":
            params.append(("kmto", str(km + v)))
        elif mode == "range":
            params.append(("kmfrom", str(max(0, km - v))))
            params.append(("kmto", str(km + v)))
        elif mode == "custom":
            if km_rule.get("min") is not None:
                params.append(("kmfrom", str(int(km_rule["min"]))))
            if km_rule.get("max") is not None:
                params.append(("kmto", str(int(km_rule["max"]))))

    # Leistung in kW (powerfrom / powerto)
    kw = vehicle.get("power_kw")
    try:
        kw = int(kw) if kw not in (None, "") else None
    except Exception:
        kw = None
    pwr_rule = rules.get("power", {"mode": "tolerance_ps", "value": 5})
    if kw and pwr_rule.get("mode") != "ignore":
        mode = pwr_rule.get("mode")
        if mode == "exact":
            params.append(("powerfrom", str(kw)))
            params.append(("powerto", str(kw)))
            params.append(("powertype", "kw"))
        elif mode == "tolerance_kw":
            v = int(pwr_rule.get("value", 5))
            params.append(("powerfrom", str(max(1, kw - v))))
            params.append(("powerto", str(kw + v)))
            params.append(("powertype", "kw"))
        elif mode == "tolerance_ps":
            v_ps = int(pwr_rule.get("value", 5))
            cur_ps = vehicle.get("power_ps") or kw_to_ps(kw)
            mn = ps_to_kw(max(1, int(cur_ps) - v_ps))
            mx = ps_to_kw(int(cur_ps) + v_ps)
            params.append(("powerfrom", str(mn)))
            params.append(("powerto", str(mx)))
            params.append(("powertype", "kw"))

    # Kraftstoff
    fuel_rule = rules.get("fuel", {}).get("mode")
    if fuel_rule == "exact":
        fuel = vehicle.get("fuel_label") or vehicle.get("fuel")
        if fuel:
            params.append(("fuel", _autoscout_fuel(fuel)))

    # Getriebe
    gear_rule = rules.get("gearbox", {}).get("mode")
    if gear_rule == "exact":
        gb = vehicle.get("gearbox_label") or vehicle.get("gearbox")
        gb_code = _autoscout_gearbox(gb)
        if gb_code:
            params.append(("gear", gb_code))

    # Schaden
    if rules.get("damage", {}).get("mode") == "no_accident":
        params.append(("damaged_listing", "exclude"))
    else:
        params.append(("damaged_listing", "exclude"))  # default: keine Unfaller

    # Default-Polish: passt zum vom Nutzer geschickten Beispiel
    params.append(("ocs_listing", "include"))
    params.append(("sort", "price"))
    params.append(("desc", "0"))
    params.append(("ustate", "N,U"))

    return f"{base}{path}?{urlencode(params, safe=',')}"


# ---------- Fuel/Gearbox-Mappings ----------
def _autoscout_fuel(s: str) -> str:
    """Mappt unsere Kraftstoff-Labels auf Autoscout-Codes."""
    n = _norm(s)
    if not n:
        return ""
    mapping = {
        "benzin": "B",
        "petrol": "B",
        "diesel": "D",
        "elektro": "E",
        "electric": "E",
        "hybrid": "2",  # Autoscout: 2 = hybrid (benz/E)
        "hybriddiesel": "3",
        "plugin": "2",
        "lpg": "L",
        "autogas": "L",
        "cng": "C",
        "erdgas": "C",
        "wasserstoff": "H",
        "hydrogen": "H",
    }
    return mapping.get(n, "")


def _autoscout_gearbox(s: str) -> str:
    n = _norm(s)
    if not n:
        return ""
    if "auto" in n:
        return "A"
    if "manuell" in n or "manual" in n or "schalt" in n:
        return "M"
    if "halbauto" in n or "semi" in n:
        return "S"
    return ""


# ---------- Public: Resolver (für ggf. Debug / Tests) ----------
def resolve(make_name: str, model_name: str) -> Tuple[Optional[int], Optional[int]]:
    """Gibt das zugehörige (makeId, modelId)-Paar zurück.

    Hilfreich zum Testen, ob ein Fahrzeug korrekt in Autoscout abgebildet
    werden kann."""
    make = _find_make(make_name or "")
    if not make:
        return None, None
    model = _find_model(make, model_name or "")
    return make["makeId"], model["modelId"] if model else None
