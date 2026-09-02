# -*- coding: utf-8 -*-
"""Schema fuer Vergleichsregeln (Inland-/Export-Profil).

Vorher wurden comparison_rules/export_rules als beliebige Dictionaries
gespeichert. Ein falscher Wert (z.B. "years": "zwei") liess spaeter den
Vergleich oder die manuelle Suche mit int(...) und HTTP 500 abstuerzen —
fuer den Sucher persoenlich oder gleich firmenweit (PR-Review 09/2026).

Hier wird jedes Regelpaket beim Speichern normalisiert: bekannte Schluessel,
erlaubte Modi, Zahlen als int, Laendercodes als Grossbuchstaben. Unbekannte
Schluessel werden verworfen, ungueltige Werte mit einer klaren Fehlermeldung
abgelehnt. Die URL-Bauer (mobile_service/autoscout_service) bekommen damit
garantiert typsichere Werte.
"""
import re
from typing import Any, Dict

_MODE_RE = re.compile(r"^[a-z_]{1,32}$")

# Regelschluessel -> (erlaubte Modi oder None fuer frei, Zahlenfelder)
_REGELN: Dict[str, Dict[str, Any]] = {
    "first_registration": {"modi": {"ignore", "any", "exact", "older_exact",
                                    "year_range"},
                           "zahlen": ("years", "from", "to")},
    "mileage": {"modi": {"ignore", "exact", "plus", "range", "custom"},
                "zahlen": ("value", "min", "max")},
    "power": {"modi": {"ignore", "exact", "tolerance_ps", "tolerance_kw"},
              "zahlen": ("value",)},
    "fuel": {"modi": {"ignore", "exact"}, "zahlen": ()},
    "gearbox": {"modi": {"ignore", "exact"}, "zahlen": ()},
    "category": {"modi": {"ignore", "exact"}, "zahlen": ()},
    "doors": {"modi": {"ignore", "exact"}, "zahlen": ()},
    "displacement": {"modi": {"ignore", "exact", "tolerance"}, "zahlen": ("value",)},
    "damage": {"modi": {"ignore", "any", "no_accident", "include"}, "zahlen": ()},
    "seller": {"modi": {"all", "dealer", "private"}, "zahlen": ()},
    "country": {"modi": {"all", "any", "exact"}, "zahlen": ()},
    "radius": {"modi": {"country", "km"}, "zahlen": ("value", "km")},
    "climatisation": {"modi": {"ignore", "always"}, "zahlen": ()},
}
_SORT = {"price_asc", "price_desc", "mileage_asc", "mileage_desc",
         "first_registration_desc", "first_registration_asc", "relevance"}
_FEATURE_MODI = {"ignore", "always"}


class RegelFehler(ValueError):
    pass


def _int_oder_none(wert, feld: str, regel: str):
    if wert in (None, ""):
        return None
    if isinstance(wert, bool):
        raise RegelFehler(f"{regel}.{feld}: Zahl erwartet")
    if isinstance(wert, (int, float)):
        return int(wert)
    if isinstance(wert, str) and re.fullmatch(r"\s*-?\d{1,9}\s*", wert):
        return int(wert)
    raise RegelFehler(f"{regel}.{feld}: Zahl erwartet, bekommen {wert!r}")


def regeln_validieren(rohe: Any) -> Dict[str, Any]:
    """Normalisiert ein Regelpaket. Loest RegelFehler bei ungueltigen Werten."""
    if rohe is None:
        return {}
    if not isinstance(rohe, dict):
        raise RegelFehler("Regeln muessen ein Objekt sein")
    sauber: Dict[str, Any] = {}
    for regel, spec in _REGELN.items():
        eintrag = rohe.get(regel)
        if eintrag is None:
            continue
        if not isinstance(eintrag, dict):
            raise RegelFehler(f"{regel}: Objekt mit 'mode' erwartet")
        mode = eintrag.get("mode")
        if mode is not None:
            if not isinstance(mode, str) or not _MODE_RE.match(mode) \
                    or mode not in spec["modi"]:
                raise RegelFehler(f"{regel}.mode: unbekannter Wert {mode!r}")
        neu: Dict[str, Any] = {}
        if mode is not None:
            neu["mode"] = mode
        for feld in spec["zahlen"]:
            if feld in eintrag:
                v = _int_oder_none(eintrag[feld], feld, regel)
                if v is not None:
                    if v < 0 or v > 10_000_000:
                        raise RegelFehler(f"{regel}.{feld}: ausserhalb des Bereichs")
                    neu[feld] = v
                else:
                    neu[feld] = None
        if regel == "country" and "codes" in eintrag:
            codes = eintrag.get("codes") or []
            if not isinstance(codes, list):
                raise RegelFehler("country.codes: Liste erwartet")
            saubere_codes = []
            for c in codes[:40]:
                if not isinstance(c, str) or not re.fullmatch(r"[A-Za-z]{2}", c):
                    raise RegelFehler(f"country.codes: ungueltiger Code {c!r}")
                saubere_codes.append(c.upper())
            neu["codes"] = saubere_codes
        if regel == "climatisation" and isinstance(eintrag.get("value"), str):
            if re.fullmatch(r"[A-Z_]{1,40}", eintrag["value"]):
                neu["value"] = eintrag["value"]
        sauber[regel] = neu
    if "sort" in rohe:
        if rohe["sort"] not in _SORT:
            raise RegelFehler(f"sort: unbekannter Wert {rohe['sort']!r}")
        sauber["sort"] = rohe["sort"]
    if "result_count" in rohe:
        n = _int_oder_none(rohe["result_count"], "result_count", "regeln")
        if n is not None:
            sauber["result_count"] = max(1, min(20, n))
    feats = rohe.get("features")
    if feats is not None:
        if not isinstance(feats, dict):
            raise RegelFehler("features: Objekt erwartet")
        saubere_feats = {}
        for name, f in list(feats.items())[:30]:
            if not isinstance(name, str) or not re.fullmatch(r"[a-z_]{1,32}", name):
                raise RegelFehler(f"features: ungueltiger Name {name!r}")
            mode = (f or {}).get("mode") if isinstance(f, dict) else None
            if mode not in _FEATURE_MODI:
                raise RegelFehler(f"features.{name}.mode: unbekannter Wert {mode!r}")
            saubere_feats[name] = {"mode": mode}
        sauber["features"] = saubere_feats
    return sauber
