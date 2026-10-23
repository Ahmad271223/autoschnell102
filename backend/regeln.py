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

Runde 11 (09/2026): Nichts wird mehr still zurechtgestutzt. Zu viele
Laender oder Ausstattungen, ein Land, das kein Portal kennt, oder eine
Ausstattung, die kein Portal filtert — alles ist ein Fehler mit Hinweis
statt einer Konfiguration, die anders gespeichert wird, als der Firmenchef
sie abgeschickt hat. "radius" ist kein Regelschluessel mehr: kein
Portal-Bauer hat ihn je ausgewertet, ein gespeicherter Radius war also eine
leere Zusage.

Pruefbericht 20.09.2026 (B-08): "result_count" ebenso — validiert und
gespeichert, aber weder von einem Link-Bauer noch von der Oberflaeche
gelesen. Der Schluessel wird wie jeder unbekannte still verworfen.
"""
import copy
import re
from typing import Any, Dict, List

_MODE_RE = re.compile(r"^[a-z_]{1,32}$")

# Laender, die mobile.de im Standort-Filter anbietet (Spiegel von
# frontend/src/components/CountryPicker.jsx, dort aus mobile.de's
# <select id="location-filter-country">). Zentrale Liste — Oberflaeche,
# Schema und URL-Bauer meinen dasselbe.
LAENDER_MOBILE = (
    "DE AT CH BE DK FR IT LU NL PL CZ ES PT SE NO FI GB IE HU SK SI HR BG RO "
    "GR EE LV LT MT CY AD AL BA BR CA EG ET FO IL IS JO JP KR KW LB LI MA MC "
    "MD ME MK MX NG NZ OM RS RU SA SM TN TR TW UA US AE BY ZA").split()
# AutoScout24.de filtert nur diese Laender (cy=...). Alles andere kann die
# AutoScout-Suche NICHT abbilden — der Aufrufer bekommt dann einen Hinweis
# (laender_ohne_autoscout), statt dass der Link still breiter sucht.
LAENDER_AUTOSCOUT = {"DE", "AT", "BE", "ES", "FR", "IT", "LU", "NL"}
MAX_LAENDER = 40

# Ausstattungen, die mindestens ein Portal-Bauer wirklich filtert. Andere
# Namen liessen sich vorher speichern ("panorama", "leder") und bewirkten
# nichts.
# Runde 24 (11.09.2026): Der Navigationsfilter ist entfallen (Wunsch Ahmad —
# nur mobile.de setzte ihn um, AutoScout24 nie). Damit filtert derzeit KEINE
# Ausstattung mehr. "navigation" aus gespeicherten Alt-Regeln wird still
# verworfen (ENTFERNTE_FEATURES); jeder andere Name bleibt ein Fehler wie
# seit Runde 11.
FEATURES_BEKANNT: set = set()
ENTFERNTE_FEATURES = {"navigation"}
_FEATURE_MODI = {"ignore", "always", "exact"}

# Runde 24 (11.09.2026): Kategorie und Klimatisierung sind keine Filter mehr
# — fuer BEIDE Portale. AutoScout24 kann die Kategorie nicht sauber abbilden
# ("keine passende Kategorie fuer 'Kombi'"), die Klimatisierung filterte nur
# mobile.de. Gespeicherte Firmen-Regeln, Sucher-Overrides und Admin-
# Standards koennen die Schluessel noch enthalten: Beim Speichern werden sie
# wie jeder unbekannte Schluessel still verworfen (kein RegelFehler), beim
# Lesen ignoriert. Die Fahrzeug-Kategorie als DATEN (Inserat, Vertrag, PDF,
# Beweisdokument) bleibt davon unberuehrt.
ENTFERNTE_REGELN = {"category", "climatisation"}

# Regelschluessel -> (erlaubte Modi, Zahlenfelder)
_REGELN: Dict[str, Dict[str, Any]] = {
    "first_registration": {"modi": {"ignore", "any", "exact", "older_exact",
                                    "year_range"},
                           "zahlen": ("years", "from", "to")},
    "mileage": {"modi": {"ignore", "exact", "plus", "range", "custom"},
                "zahlen": ("value", "min", "max")},
    # min_ps (15.09.2026, Wunsch Ahmad): nur nach unten begrenzen — "-X PS
    # und aufwaerts", nach oben keine Grenze.
    "power": {"modi": {"ignore", "exact", "tolerance_ps", "tolerance_kw", "min_ps"},
              "zahlen": ("value",)},
    "fuel": {"modi": {"ignore", "exact"}, "zahlen": ()},
    "gearbox": {"modi": {"ignore", "exact"}, "zahlen": ()},
    # Navi (18.09.2026, Wunsch Ahmad): "wenn_vorhanden" haengt den
    # mobile.de-Filter fe=NAVIGATION_SYSTEM an, sobald das Inserat ein Navi
    # nennt; "ignore" filtert nie danach. Fehlt der Schluessel in einem
    # gespeicherten Regelpaket, gilt "wenn_vorhanden" (so wie seit heute
    # gebaut). NICHT zu verwechseln mit dem alten features.navigation aus
    # Runde 24 — das setzte ein wirkungsloses f= und ist entfallen.
    "navi": {"modi": {"ignore", "wenn_vorhanden"}, "zahlen": ()},
    "doors": {"modi": {"ignore", "exact"}, "zahlen": ()},
    "displacement": {"modi": {"ignore", "exact", "tolerance"}, "zahlen": ("value",)},
    "damage": {"modi": {"ignore", "any", "no_accident", "include"}, "zahlen": ()},
    "seller": {"modi": {"all", "dealer", "private"}, "zahlen": ()},
    "country": {"modi": {"all", "any", "exact"}, "zahlen": ()},
}
# Sortierungen, die BEIDE Portal-Bauer umsetzen (Runde 11: vorher wurde
# die Sortierung gespeichert und von beiden Bauern ignoriert).
SORTIERUNGEN = {"price_asc", "price_desc", "mileage_asc", "mileage_desc",
                "first_registration_desc", "first_registration_asc", "relevance"}
_SORT = SORTIERUNGEN


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
                elif feld in ("from", "to", "min", "max"):
                    # Offene Grenze (Bereich ohne Unter- oder Obergrenze).
                    neu[feld] = None
                # 16.09.2026: ein leeres "value"/"years" (Feld im Formular
                # geleert, null per Schnittstelle) wird NICHT als None
                # gespeichert — die Portal-Bauer rechnen int(None) und jeder
                # Vergleich brach mit 500 ab. Ohne den Schluessel gilt der
                # Standardwert des Bauers (z.B. 5 PS, 30.000 km, 1 Jahr).
        # Pruefbericht 20.09.2026 (B-02): jedes Zahlenfeld wurde nur einzeln
        # geprueft — "min 200000 / max 1000" oder "von 2024 / bis 2001" wurde
        # gespeichert, und jede Suche fand danach schlicht nichts.
        for unten, oben in (("min", "max"), ("from", "to")):
            if neu.get(unten) is not None and neu.get(oben) is not None \
                    and neu[unten] > neu[oben]:
                raise RegelFehler(f"{regel}: '{unten}' ({neu[unten]}) ist größer als "
                                  f"'{oben}' ({neu[oben]}) — bitte die Grenzen tauschen")
        if regel == "country" and "codes" in eintrag:
            codes = eintrag.get("codes") or []
            if not isinstance(codes, list):
                raise RegelFehler("country.codes: Liste erwartet")
            if len(codes) > MAX_LAENDER:
                raise RegelFehler(f"country.codes: hoechstens {MAX_LAENDER} Laender, "
                                  f"bekommen {len(codes)}")
            saubere_codes: List[str] = []
            for c in codes:
                if not isinstance(c, str) or not re.fullmatch(r"[A-Za-z]{2}", c):
                    raise RegelFehler(f"country.codes: ungueltiger Code {c!r}")
                code = c.upper()
                if code not in LAENDER_MOBILE:
                    raise RegelFehler(f"country.codes: Land {code!r} bietet kein "
                                      "Portal als Filter an")
                if code not in saubere_codes:
                    saubere_codes.append(code)
            if mode == "exact" and not saubere_codes:
                raise RegelFehler("country: Modus 'exact' braucht mindestens ein Land")
            neu["codes"] = saubere_codes
        sauber[regel] = neu
    if "sort" in rohe:
        if rohe["sort"] not in _SORT:
            raise RegelFehler(f"sort: unbekannter Wert {rohe['sort']!r}")
        sauber["sort"] = rohe["sort"]
    feats = rohe.get("features")
    if feats is not None:
        if not isinstance(feats, dict):
            raise RegelFehler("features: Objekt erwartet")
        saubere_feats = {}
        for name, f in feats.items():
            if name in ENTFERNTE_FEATURES:
                # Runde 24: entfallener Filter aus Alt-Regeln -> still weg
                # (auch mit kaputtem Modus), nie ein Fehler beim Speichern.
                continue
            if not isinstance(name, str) or name not in FEATURES_BEKANNT:
                bekannt = ", ".join(sorted(FEATURES_BEKANNT)) or "derzeit keine"
                raise RegelFehler(f"features: {name!r} filtert kein Portal "
                                  f"(bekannt: {bekannt})")
            mode = (f or {}).get("mode") if isinstance(f, dict) else None
            if mode not in _FEATURE_MODI:
                raise RegelFehler(f"features.{name}.mode: unbekannter Wert {mode!r}")
            saubere_feats[name] = {"mode": mode}
        # Runde 24: ein leeres "features" (bereinigte Alt-Regeln) wird nicht
        # mehr gespeichert — es gibt derzeit keinen Ausstattungsfilter.
        if saubere_feats:
            sauber["features"] = saubere_feats
    return sauber


def regeln_lesen(rohe: Any, standard: Dict[str, Any]) -> Dict[str, Any]:
    """Lesepfad (Nachpruefung Runde 10): Alt- oder Fremddokumente in die
    Schema-Form bringen, ohne je einen 500 zu werfen. Ein ungueltiger
    Eintrag (z.B. damage als String aus alten Daten) faellt auf den
    Standard zurueck; gueltige Eintraege bleiben, fehlende werden aus dem
    Standard ergaenzt. Der Schreibpfad (regeln_validieren) bleibt streng."""
    if not isinstance(rohe, dict):
        return copy.deepcopy(standard)
    sauber: Dict[str, Any] = {}
    for k, v in rohe.items():
        try:
            teil = regeln_validieren({k: v})
        except RegelFehler:
            teil = {}
        if k in teil:
            basis = standard.get(k)
            if isinstance(basis, dict) and isinstance(teil[k], dict):
                # 16.09.2026: fehlende Teile einer Regel (z.B. "mode" oder
                # "value", wenn nur ein Feld gespeichert wurde) kommen aus dem
                # Standard — vorher blieb eine Regel ohne Modus wirkungslos,
                # obwohl das Formular einen Modus anzeigte.
                sauber[k] = {**basis, **teil[k]}
            else:
                sauber[k] = teil[k]
        elif k in standard:
            # Pruefbericht 20.09.2026 (B-12): Kopie statt des Standard-Objekts
            # selbst — sonst teilte das gelesene Paket dict-Werte mit
            # DEFAULT_RULES, und eine Aenderung am Ergebnis aenderte den Standard.
            sauber[k] = copy.deepcopy(standard[k])
    for k, v in standard.items():
        sauber.setdefault(k, copy.deepcopy(v))
    return sauber


def laender_ohne_autoscout(rules: dict) -> List[str]:
    """Laender des Regelpakets, die AutoScout24 nicht filtern kann
    (leer, wenn das Paket nicht nach Land filtert)."""
    country = (rules or {}).get("country") or {}
    if country.get("mode", "exact") != "exact":
        return []
    codes = country.get("codes") or ([country["value"]] if country.get("value") else ["DE"])
    return [str(c).upper() for c in codes if str(c).upper() not in LAENDER_AUTOSCOUT]
