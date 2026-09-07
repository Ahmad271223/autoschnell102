"""Manuelle Suche: Marke + Modell + Filter → mobile.de + Autoscout24 URLs.

Runde 10 (09/2026), Pruefbericht "Firmenchef + Suchfunktion": Eingaben
haben Grenzen, unbekannte Marken/Modelle/Kraftstoffe/Getriebe werden mit
400 abgelehnt statt still zu einer viel breiteren Suche zu fuehren, kW
und PS muessen zueinander passen, die Suche ist je Konto begrenzt und
wird protokolliert, und die Antwort sagt, wie die Portale die Auswahl
aufgeloest haben. Das interne Regelpaket wird nicht mehr mitgeliefert.
"""
import unicodedata
from functools import lru_cache
import re
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator, model_validator

from autoscout_service import _autoscout_fuel as _as_fuel
from autoscout_service import _find_make as _as_find_make
from autoscout_service import _find_model as _as_find_model
from autoscout_service import _load_data as _load_autoscout
from autoscout_service import build_search_url as build_autoscout_url
from deps import current_firma, current_user, db, log_activity, require_active_sub
from mobile_service import DEFAULT_RULES
from mobile_service import _resolve_make as _mo_resolve_make
from mobile_service import _resolve_model as _mo_resolve_model
from mobile_service import build_search_url as build_mobile_url
from rate_limiter import SlidingWindowRateLimiter

router = APIRouter()

# Je Konto: 60 Suchen je Minute reichen fuer jeden Menschen und stoppen
# ein Skript, das die Suche im Sekundentakt abfeuert. Mongo-gestuetzt,
# gilt also ueber alle Worker und Server (rate_limiter._check_mongo).
suche_limiter = SlidingWindowRateLimiter(max_attempts=60, window_seconds=60, name="suche")

_JAHR_MIN = 1960


def _norm(s: str) -> str:
    nfd = unicodedata.normalize("NFD", s)
    no_acc = "".join(c for c in nfd if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]", "", no_acc.lower())


# Exakte Zuordnung (kein Teilstring-Raten): normalisiertes Label -> mobile.de-Code
_FUEL_TO_MOBILE = {
    "benzin": "PETROL", "petrol": "PETROL", "super": "PETROL",
    "diesel": "DIESEL",
    "elektro": "ELECTRICITY", "electric": "ELECTRICITY", "strom": "ELECTRICITY",
    "hybrid": "HYBRID", "hybridbenzin": "HYBRID", "hybriddiesel": "HYBRID",
    "pluginhybrid": "HYBRID", "plugin": "HYBRID",
    "lpg": "LPG", "autogas": "LPG",
    "cng": "CNG", "erdgas": "CNG",
    "wasserstoff": "HYDROGENIUM", "hydrogen": "HYDROGENIUM",
}
_GEAR_TO_MOBILE = {
    "automatik": "AUTOMATIC_GEAR", "automatic": "AUTOMATIC_GEAR", "automatikgetriebe": "AUTOMATIC_GEAR",
    "manuell": "MANUAL_GEAR", "manual": "MANUAL_GEAR",
    "schaltgetriebe": "MANUAL_GEAR", "schaltung": "MANUAL_GEAR",
}


def _fuel_code(label: str) -> str:
    """Label -> mobile.de-Code, exakt. Unbekannt -> "".
    Nachpruefung Runde 10: Die Oberflaeche schickt Doppel-Labels wie
    "LPG / Autogas" oder "CNG / Erdgas" — jeder Teil wird einzeln probiert."""
    n = _norm(label or "")
    if n in _FUEL_TO_MOBILE:
        return _FUEL_TO_MOBILE[n]
    for teil in re.split(r"[/(),]", label or ""):
        code = _FUEL_TO_MOBILE.get(_norm(teil))
        if code:
            return code
    return ""


# Standard-Label je mobile.de-Code — fuer AutoScout, wenn das Original-Label
# dort unbekannt ist (z.B. "Super", "Strom", "LPG / Autogas").
_FUEL_CANON = {"PETROL": "Benzin", "DIESEL": "Diesel", "ELECTRICITY": "Elektro",
               "HYBRID": "Hybrid", "LPG": "LPG", "CNG": "CNG", "HYDROGENIUM": "Wasserstoff"}


def _autoscout_label(label: str, code: str) -> str:
    """AutoScout bekommt ein Label, das es kennt: das Original, wenn es
    passt, sonst das Standard-Label zum mobile.de-Code."""
    if label and _as_fuel(label):
        return label
    return _FUEL_CANON.get(code, label or "")


def _gear_code(label: str) -> str:
    return _GEAR_TO_MOBILE.get(_norm(label or ""), "")


# ---------- Models ----------
class ManualSearchIn(BaseModel):
    make: str = Field(min_length=1, max_length=80)
    model: Optional[str] = Field(default=None, max_length=120)
    ez_from: Optional[int] = Field(default=None, ge=_JAHR_MIN, le=2100)
    ez_to: Optional[int] = Field(default=None, ge=_JAHR_MIN, le=2100)
    km_max: Optional[int] = Field(default=None, ge=0, le=2_000_000)
    km_min: Optional[int] = Field(default=None, ge=0, le=2_000_000)
    kw: Optional[int] = Field(default=None, ge=1, le=1500)
    ps: Optional[int] = Field(default=None, ge=1, le=2100)
    kw_tolerance: Optional[int] = Field(
        default=10, ge=0, le=200,
        description="±kW-Toleranz fuer die Suche (Standard 10, 0 = exakt)")
    fuel: Optional[str] = Field(default=None, max_length=40)
    gearbox: Optional[str] = Field(default=None, max_length=40)

    @field_validator("make", "model", "fuel", "gearbox")
    @classmethod
    def _trim(cls, v):
        return v.strip() if isinstance(v, str) else v

    @model_validator(mode="after")
    def _plausibel(self):
        jahr = datetime.now(timezone.utc).year + 1
        for name in ("ez_from", "ez_to"):
            wert = getattr(self, name)
            if wert is not None and wert > jahr:
                raise ValueError(f"{name}: Jahr liegt in der Zukunft")
        if self.ez_from and self.ez_to and self.ez_from > self.ez_to:
            raise ValueError("Erstzulassung: 'von' liegt nach 'bis'")
        if self.km_min is not None and self.km_max is not None and self.km_min > self.km_max:
            raise ValueError("Kilometer: 'min' liegt ueber 'max'")
        if self.kw and self.ps:
            erwartet_ps = self.kw * 1.359621617
            if abs(erwartet_ps - self.ps) > max(3.0, erwartet_ps * 0.03):
                raise ValueError(
                    f"kW und PS passen nicht zusammen ({self.kw} kW sind etwa "
                    f"{round(erwartet_ps)} PS, angegeben {self.ps}) — bitte nur einen Wert schicken")
        if self.fuel and not _fuel_code(self.fuel):
            raise ValueError(f"Kraftstoff unbekannt: {self.fuel!r} (erlaubt: Benzin, Diesel, "
                             "Elektro, Hybrid, Plug-in-Hybrid, LPG, CNG, Wasserstoff)")
        if self.gearbox and not _gear_code(self.gearbox):
            raise ValueError(f"Getriebe unbekannt: {self.gearbox!r} (erlaubt: Automatik, Manuell)")
        return self


# ---------- Endpoints ----------
async def _katalog_berechtigt(user=Depends(current_user)):
    """Nachpruefung Runde 10: Der Katalog gehoert Firmen-Konten UND
    Zwischenhaendlern — der Marktplatz filtert damit nach Marke/Modell.
    Runde 10 hatte ihn auf Firmen beschraenkt und den Kaeufern die Filter
    genommen. Admins brauchen ihn weiterhin nicht."""
    if user.get("role") == "b2b_buyer":
        return user
    return await current_firma(user)


@lru_cache(maxsize=1)
def _makes_payload() -> list:
    """Katalog einmal je Prozess bauen — die Quelle aendert sich nur mit
    einem Deploy (Nachpruefung Runde 10: vorher bei jedem Aufruf neu)."""
    data = _load_autoscout()
    out = []
    for m in data:
        models = m.get("models") or []
        models_sorted = sorted(
            [{"id": mm["modelId"], "name": mm["modelName"]} for mm in models],
            key=lambda x: x["name"].lower(),
        )
        out.append({"id": m["makeId"], "name": m["makeName"], "models": models_sorted})
    out.sort(key=lambda x: x["name"].lower())
    return out


@router.get("/manual/makes")
async def list_makes(_=Depends(_katalog_berechtigt)):
    """Komplette Marken+Modelle-Liste (Quelle: autoscout_makes.json).
    Fuer Firmen-Konten und Zwischenhaendler (Marktplatz-Filter). Der Browser
    darf sie einen Tag behalten (private: liegt hinter dem Login)."""
    return JSONResponse(_makes_payload(),
                        headers={"Cache-Control": "private, max-age=86400"})


@router.post("/manual/search")
async def manual_search(body: ManualSearchIn, user=Depends(require_active_sub)):
    """Wandelt die manuelle Eingabe in ein „virtuelles Fahrzeug" um und nutzt
    die mobile.de-/AutoScout24-URL-Builder. Erstzulassung, Kilometer,
    Leistung, Kraftstoff und Getriebe kommen aus dem Formular; alle
    uebrigen Regeln (Land, Unfallwagen, Preisrahmen …) aus dem aktiven
    Regelpaket des Haendlers.
    """
    if not await suche_limiter.check(f"suche:{user['id']}"):
        raise HTTPException(429, "Zu viele Suchen in kurzer Zeit — bitte eine Minute warten.")

    # ---- Marke/Modell gegen den Katalog: unbekannt ist ein Fehler, keine Breitensuche ----
    as_make = _as_find_make(body.make)
    if not as_make:
        raise HTTPException(400, f"Marke unbekannt: {body.make!r}. Bitte aus der Liste waehlen.")
    as_model = None
    if body.model:
        as_model = _as_find_model(as_make, body.model)
        if not as_model:
            raise HTTPException(400, f"Modell unbekannt fuer {as_make.get('makeName', body.make)}: "
                                     f"{body.model!r}. Bitte aus der Liste waehlen.")

    from deps import effective_dealer
    dealer = await effective_dealer(user)
    if not dealer:
        raise HTTPException(403, "Kein Haendlerprofil zu diesem Konto — bitte den Betreiber ansprechen.")
    profil = "export" if (dealer.get("active_profile") or "inland") == "export" else "inland"
    from regeln import regeln_lesen
    # Nachpruefung Runde 10: Lesepfad heilt Alt-Dokumente statt 500.
    if profil == "export":
        from mobile_service import DEFAULT_EXPORT_RULES
        base_rules = regeln_lesen(dealer.get("export_rules"), DEFAULT_EXPORT_RULES)
    else:
        base_rules = regeln_lesen(dealer.get("comparison_rules"), DEFAULT_RULES)
    rules = {**base_rules}
    ersetzt = []          # welche Haendlerregeln das Formular ueberschreibt

    if body.ez_from or body.ez_to:
        rules["first_registration"] = {"mode": "year_range",
                                       "from": body.ez_from or None, "to": body.ez_to or None}
    else:
        rules["first_registration"] = {"mode": "ignore"}
    ersetzt.append("first_registration")

    if body.km_min is not None and body.km_max is not None:
        rules["mileage"] = {"mode": "custom", "min": body.km_min, "max": body.km_max}
    elif body.km_max is not None:
        rules["mileage"] = {"mode": "custom", "min": 0, "max": body.km_max}
    elif body.km_min is not None:
        rules["mileage"] = {"mode": "custom", "min": body.km_min, "max": None}
    else:
        rules["mileage"] = {"mode": "ignore"}
    ersetzt.append("mileage")

    toleranz = body.kw_tolerance if body.kw_tolerance is not None else 10
    if body.kw or body.ps:
        rules["power"] = {"mode": "tolerance_kw", "value": int(toleranz)}
    else:
        rules["power"] = {"mode": "ignore"}
    ersetzt.append("power")

    fuel_code = _fuel_code(body.fuel or "")
    gear_code = _gear_code(body.gearbox or "")
    rules["fuel"] = {"mode": "exact" if fuel_code else "ignore"}
    rules["gearbox"] = {"mode": "exact" if gear_code else "ignore"}
    ersetzt += ["fuel", "gearbox"]
    rules.setdefault("damage", {"mode": "no_accident"})

    kw = body.kw
    ps = body.ps
    if not kw and ps:
        kw = int(round(int(ps) / 1.359621617))
    if not ps and kw:
        ps = int(round(int(kw) * 1.359621617))
    ez_year = body.ez_from or body.ez_to
    vehicle = {
        "make": as_make.get("makeName") or body.make,
        "make_label": as_make.get("makeName") or body.make,
        "model": (as_model or {}).get("modelName") or body.model or "",
        "model_label": (as_model or {}).get("modelName") or body.model or "",
        "first_registration": f"01/{ez_year}" if ez_year else None,
        "mileage": body.km_max or body.km_min or 0,
        "power_kw": kw,
        "power_ps": ps,
        "fuel": fuel_code,
        "fuel_label": _autoscout_label(body.fuel or "", fuel_code),
        "gearbox": gear_code,
        "gearbox_label": body.gearbox or "",
    }

    # Wie loest mobile.de die Auswahl auf? (eigener Katalog — kann abweichen)
    mo_make_id, mo_make_entry = _mo_resolve_make(vehicle)
    mo_model_id = _mo_resolve_model(mo_make_entry, vehicle) if (mo_make_id and body.model) else None
    hinweise = []
    if not mo_make_id:
        hinweise.append("mobile.de kennt diese Marke nicht — die mobile.de-Suche laeuft OHNE Markenfilter.")
    elif body.model and not mo_model_id:
        hinweise.append("mobile.de kennt dieses Modell nicht — die mobile.de-Suche zeigt die ganze Marke.")
    if body.kw and body.ps:
        hinweise.append("kW und PS beide angegeben — fuer die Suche gilt der kW-Wert.")
    if body.fuel and not _as_fuel(vehicle["fuel_label"]):
        # Nachpruefung Runde 10: vorher lief der AutoScout-Link dann still
        # ueber ALLE Kraftstoffe.
        hinweise.append(f"AutoScout kennt den Kraftstoff {body.fuel!r} nicht — "
                        "der AutoScout-Link laeuft OHNE Kraftstoff-Filter.")

    if not body.model:
        # Nachpruefung Runde 10: Ohne Modell zeigen beide Links die ganze
        # Marke — das sagt der Server jetzt (warnen statt bremsen).
        filter_gesetzt = any([body.ez_from, body.ez_to, body.km_min is not None,
                              body.km_max is not None, body.kw, body.ps, fuel_code, gear_code])
        hinweise.append(
            f"Kein Modell gewaehlt — beide Links zeigen die ganze Marke {vehicle['make']}"
            + ("" if filter_gesetzt else
               " ohne weitere Filter (Erstzulassung, km, Leistung, Kraftstoff, Getriebe)")
            + ".")
    mobile_url = build_mobile_url(vehicle, rules)
    autoscout_url = build_autoscout_url(vehicle, rules)
    # Runde 11: Was der AutoScout-Link von den Firmenregeln NICHT umsetzt
    # (z.B. Land CH), sagt der Server — vorher sah der Link nur "erfolgreich" aus.
    from autoscout_service import regeln_nicht_abgebildet
    hinweise += regeln_nicht_abgebildet(vehicle, rules)

    await log_activity(user["dealer_id"], user["id"], "suche.manuell",
                       meta={"make": vehicle["make"], "model": vehicle["model"],
                             "fuel": fuel_code or "", "gearbox": gear_code or "",
                             "profil": profil})
    return {
        "mobile_url": mobile_url,
        "autoscout_url": autoscout_url,
        "profil": profil,          # welches Regelpaket die Links erzeugt hat
        "aufgeloest": {
            "autoscout": {"make": as_make.get("makeName"), "model": (as_model or {}).get("modelName")},
            "mobile": {"make": bool(mo_make_id), "model": bool(mo_model_id) if body.model else None},
        },
        "haendlerregeln_ersetzt": ersetzt,
        "hinweise": hinweise,
    }
