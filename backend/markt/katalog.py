# -*- coding: utf-8 -*-
"""Startbestand der Marktanalysen (Auftrag v3, 26.09.2026: 52 Modelle) — NUR
ein Seed. Die Wahrheit ist market_models; der Super-Admin legt im Admin
(Marktanalyse -> Suchauftraege) beliebige weitere Marktanalysen an, mit
eigenen EZ-Jahren, km-Bereichen, Zeilen und Frequenz. Nichts davon ist
im Code verdrahtet.

Probelaeufe 26.09.2026: mobile.de filtert Freitext-Varianten ("320d") NICHT
zuverlaessig — eine Variante wird ueber Modell-ID + Kraftstoff (+ kW-Bereich)
beschrieben; die IDs kommen aus mobile_makes_models.json (2.721 Modelle) und
werden beim Einspielen aufgeloest. Unaufloesbare Eintraege bleiben pausiert."""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional

# mobile.de-Kraftstoffcodes (ft=): PETROL, DIESEL, HYBRID (Benzin/Elektro), HYBRID_DIESEL, ELECTRICITY, LPG, CNG
# Getriebe (tr=): AUTOMATIC_GEAR (auch DSG/S tronic — so listen die Haendler), MANUAL_GEAR.
# Befund Ahmad 26.09.2026 (erster Live-Tag): Schalt- und Automatikpreise vermischten sich,
# und der kW-Bereich zog Fremdmotoren mit (1.6 TDI 85 kW im "2.0 TDI", Octavia RS TDI 147 kW).
# Deshalb: JEDER Eintrag hat ein Getriebe (nie "alle"); wo beide Getriebe im Gebrauchtmarkt
# haeufig sind, gibt es zwei Eintraege (…-schalt / …-auto); kW-Bereiche eng um die Variante.
# (id, Marke, Modellname im Katalog, Anzeige-Variante, Kraftstoff, kW von, kW bis, Prioritaet, Getriebe)
A, M = "AUTOMATIC_GEAR", "MANUAL_GEAR"
STARTLISTE: List[tuple] = [
    ("vw-golf-15tsi", "Volkswagen", "Golf", "1.5 TSI", "PETROL", 90, 115, 1, A),
    ("vw-golf-15tsi-schalt", "Volkswagen", "Golf", "1.5 TSI", "PETROL", 90, 115, 1, M),
    ("vw-golf-20tdi", "Volkswagen", "Golf", "2.0 TDI", "DIESEL", 110, 125, 1, A),          # 150 PS; 1.6 TDI (85) und GTD (147) draussen
    ("vw-golf-20tdi-schalt", "Volkswagen", "Golf", "2.0 TDI", "DIESEL", 110, 125, 1, M),
    ("vw-golf-gti", "Volkswagen", "Golf", "GTI 2.0 TSI", "PETROL", 169, 195, 2, A),
    ("vw-golf-gti-schalt", "Volkswagen", "Golf", "GTI 2.0 TSI", "PETROL", 169, 195, 2, M),
    ("vw-polo-10tsi", "Volkswagen", "Polo", "1.0 TSI", "PETROL", 66, 85, 2, M),
    ("vw-polo-10tsi-auto", "Volkswagen", "Polo", "1.0 TSI", "PETROL", 66, 85, 2, A),
    ("vw-passat-20tdi", "Volkswagen", "Passat Variant", "2.0 TDI", "DIESEL", 110, 150, 1, A),
    ("vw-passat-20tdi-schalt", "Volkswagen", "Passat Variant", "2.0 TDI", "DIESEL", 110, 150, 1, M),
    ("vw-tiguan-20tdi", "Volkswagen", "Tiguan", "2.0 TDI", "DIESEL", 110, 150, 1, A),
    ("vw-tiguan-20tdi-schalt", "Volkswagen", "Tiguan", "2.0 TDI", "DIESEL", 110, 150, 1, M),
    ("vw-t-roc-15tsi", "Volkswagen", "T-Roc", "1.5 TSI", "PETROL", 90, 115, 2, A),
    ("vw-t-roc-15tsi-schalt", "Volkswagen", "T-Roc", "1.5 TSI", "PETROL", 90, 115, 2, M),
    ("vw-touran-20tdi", "Volkswagen", "Touran", "2.0 TDI", "DIESEL", 110, 150, 2, A),      # 1.6 TDI (85 kW) draussen
    ("vw-touran-20tdi-schalt", "Volkswagen", "Touran", "2.0 TDI", "DIESEL", 110, 150, 2, M),
    ("audi-a3-35tfsi", "Audi", "A3", "35 TFSI", "PETROL", 100, 115, 2, A),
    ("audi-a3-35tfsi-schalt", "Audi", "A3", "35 TFSI", "PETROL", 100, 115, 2, M),
    ("audi-a3-35tdi", "Audi", "A3", "35 TDI", "DIESEL", 100, 115, 2, A),
    ("audi-a3-35tdi-schalt", "Audi", "A3", "35 TDI", "DIESEL", 100, 115, 2, M),
    ("audi-a4-35tdi", "Audi", "A4", "35 TDI", "DIESEL", 100, 125, 2, A),
    ("audi-a4-40tdi", "Audi", "A4", "40 TDI", "DIESEL", 135, 155, 1, A),
    ("audi-a5-40tdi", "Audi", "A5", "40 TDI", "DIESEL", 135, 155, 2, A),
    ("audi-a6-40tdi", "Audi", "A6", "40 TDI", "DIESEL", 135, 155, 1, A),
    ("audi-q3-35tfsi", "Audi", "Q3", "35 TFSI", "PETROL", 100, 115, 2, A),
    ("audi-q5-40tdi", "Audi", "Q5", "40 TDI", "DIESEL", 135, 155, 2, A),
    ("bmw-118i", "BMW", "118", "118i", "PETROL", 90, 105, 2, A),
    ("bmw-118i-schalt", "BMW", "118", "118i", "PETROL", 90, 105, 2, M),
    ("bmw-120d", "BMW", "120", "120d", "DIESEL", 120, 145, 2, A),
    ("bmw-320i", "BMW", "320", "320i", "PETROL", 130, 140, 2, A),
    ("bmw-320d", "BMW", "320", "320d", "DIESEL", 120, 145, 1, A),
    ("bmw-330e", "BMW", "330", "330e", "HYBRID", 130, 215, 2, A),
    ("bmw-520d", "BMW", "520", "520d", "DIESEL", 120, 145, 1, A),
    ("bmw-530d", "BMW", "530", "530d", "DIESEL", 180, 225, 2, A),
    ("bmw-x1-18d", "BMW", "X1", "18d", "DIESEL", 100, 115, 2, A),
    ("bmw-x1-18d-schalt", "BMW", "X1", "18d", "DIESEL", 100, 115, 2, M),
    ("bmw-x3-20d", "BMW", "X3", "20d", "DIESEL", 120, 145, 2, A),
    ("mercedes-a-180", "Mercedes-Benz", "A 180", "A 180", "PETROL", None, None, 2, A),
    ("mercedes-a-200", "Mercedes-Benz", "A 200", "A 200", "PETROL", None, None, 2, A),
    ("mercedes-a-200d", "Mercedes-Benz", "A 200", "A 200 d", "DIESEL", None, None, 2, A),
    ("mercedes-c-200", "Mercedes-Benz", "C 200", "C 200", "PETROL", None, None, 2, A),
    ("mercedes-c-220d", "Mercedes-Benz", "C 220", "C 220 d", "DIESEL", None, None, 1, A),
    ("mercedes-e-220d", "Mercedes-Benz", "E 220", "E 220 d", "DIESEL", None, None, 1, A),
    ("mercedes-e-300d", "Mercedes-Benz", "E 300", "E 300 d", "DIESEL", None, None, 2, A),
    ("mercedes-cla-200", "Mercedes-Benz", "CLA 200", "CLA 200", "PETROL", None, None, 2, A),
    ("mercedes-cla-220d", "Mercedes-Benz", "CLA 220", "CLA 220 d", "DIESEL", None, None, 2, A),
    ("mercedes-gla-200d", "Mercedes-Benz", "GLA 200", "GLA 200 d", "DIESEL", None, None, 2, A),
    ("mercedes-glc-220d", "Mercedes-Benz", "GLC 220", "GLC 220 d", "DIESEL", None, None, 1, A),
    ("mercedes-glc-300d", "Mercedes-Benz", "GLC 300", "GLC 300 d", "DIESEL", None, None, 2, A),
    ("skoda-fabia-10tsi", "Skoda", "Fabia", "1.0 TSI", "PETROL", 66, 85, 2, M),
    ("skoda-fabia-10tsi-auto", "Skoda", "Fabia", "1.0 TSI", "PETROL", 66, 85, 2, A),
    ("skoda-octavia-15tsi", "Skoda", "Octavia", "1.5 TSI", "PETROL", 100, 115, 2, A),
    ("skoda-octavia-15tsi-schalt", "Skoda", "Octavia", "1.5 TSI", "PETROL", 100, 115, 2, M),
    ("skoda-octavia-20tdi", "Skoda", "Octavia", "2.0 TDI", "DIESEL", 110, 140, 1, A),      # 1.6 TDI (85) und RS TDI (147) draussen
    ("skoda-octavia-20tdi-schalt", "Skoda", "Octavia", "2.0 TDI", "DIESEL", 110, 140, 1, M),
    ("skoda-superb-20tdi", "Skoda", "Superb", "2.0 TDI", "DIESEL", 110, 150, 2, A),
    ("skoda-karoq-15tsi", "Skoda", "Karoq", "1.5 TSI", "PETROL", 100, 115, 2, A),
    ("skoda-karoq-15tsi-schalt", "Skoda", "Karoq", "1.5 TSI", "PETROL", 100, 115, 2, M),
    ("opel-corsa-12turbo", "Opel", "Corsa", "1.2 Turbo", "PETROL", 70, 100, 2, M),
    ("opel-corsa-12turbo-auto", "Opel", "Corsa", "1.2 Turbo", "PETROL", 70, 100, 2, A),
    ("opel-astra-12turbo", "Opel", "Astra", "1.2 Turbo", "PETROL", 75, 100, 2, M),
    ("opel-astra-12turbo-auto", "Opel", "Astra", "1.2 Turbo", "PETROL", 75, 100, 2, A),
    ("opel-astra-15diesel", "Opel", "Astra", "1.5 Diesel", "DIESEL", 70, 95, 2, M),
    ("ford-focus-10ecoboost", "Ford", "Focus", "1.0 EcoBoost", "PETROL", 70, 115, 2, M),
    ("ford-focus-10ecoboost-auto", "Ford", "Focus", "1.0 EcoBoost", "PETROL", 70, 115, 2, A),
    ("ford-focus-15ecoblue", "Ford", "Focus", "1.5 EcoBlue", "DIESEL", 70, 90, 2, M),
    ("ford-mondeo-20tdci", "Ford", "Mondeo", "2.0 TDCi / EcoBlue", "DIESEL", 85, 145, 2, A),   # ab 2019 heisst er EcoBlue (88/110/140 kW)
    ("ford-mondeo-20tdci-schalt", "Ford", "Mondeo", "2.0 TDCi / EcoBlue", "DIESEL", 85, 145, 2, M),
    ("ford-kuga-15ecoboost", "Ford", "Kuga", "1.5 EcoBoost", "PETROL", 88, 135, 2, M),
    ("toyota-aygo-x", "Toyota", "Aygo (X)", "1.0", "PETROL", 50, 60, 2, M),                # Aygo (bis 2022) und Aygo X (ab 2022) teilen die Modell-ID
    ("toyota-yaris-hybrid", "Toyota", "Yaris", "1.5 Hybrid", "HYBRID", 70, 100, 2, A),      # EZ 2019 = alter 1.5 Hybrid mit 74 kW
    ("toyota-corolla-hybrid", "Toyota", "Corolla", "1.8 Hybrid", "HYBRID", 85, 105, 2, A),
]
GETRIEBE_TEXT = {"AUTOMATIC_GEAR": "Automatik", "MANUAL_GEAR": "Schaltung", "SEMIAUTOMATIC_GEAR": "Halbautomatik"}

_KATALOG_DATEI = Path(__file__).resolve().parent.parent / "mobile_makes_models.json"
_MARKEN_ALIAS = {"mini": "mini", "vw": "volkswagen", "mercedes": "mercedesbenz", "mercedesbenz": "mercedesbenz"}


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _katalog() -> List[dict]:
    try:
        return json.loads(_KATALOG_DATEI.read_text(encoding="utf-8")).get("marken") or []
    except Exception:  # noqa: BLE001
        return []


def marken() -> List[Dict[str, Any]]:
    """Alle Marken des mobile.de-Katalogs (fuer das Formular)."""
    return [{"name": m.get("name"), "make_id": str(m.get("id"))} for m in _katalog() if m.get("id")]


def modelle_der_marke(marke: str) -> List[Dict[str, Any]]:
    mn = _MARKEN_ALIAS.get(_norm(marke), _norm(marke))
    for m in _katalog():
        if _norm(m.get("name")) == mn or str(m.get("id")) == str(marke):
            return [{"name": x.get("name"), "model_id": str(x.get("id"))}
                    for x in m.get("modelle") or [] if x.get("id") and not str(x.get("id")).startswith("g")]
    return []


def modell_ids(marke: str, modell: str, katalog: Optional[List[dict]] = None) -> Optional[Dict[str, Any]]:
    """{"make_id", "model_id", "make_name", "model_name"} oder None."""
    kat = katalog if katalog is not None else _katalog()
    mn = _MARKEN_ALIAS.get(_norm(marke), _norm(marke))
    for m in kat:
        if _norm(m.get("name")) != mn and str(m.get("id")) != str(marke):
            continue
        ziel = _norm(modell)
        for x in m.get("modelle") or []:
            if (_norm(x.get("name")) == ziel or str(x.get("id")) == str(modell)) and not str(x.get("id")).startswith("g"):
                return {"make_id": str(m.get("id")), "model_id": str(x.get("id")),
                        "make_name": m.get("name"), "model_name": x.get("name")}
        return None
    return None


SEED_VERSION = 3      # v5 (26.09.2026 abends, Ahmad): EZ 2018-2022, sechs km-Bereiche 10-190k, 10 Zeilen


def seed_label(marke: str, modell: str, variante: str, getriebe: Optional[str] = None) -> str:
    basis = f"{marke} {variante}" if (modell in variante or variante.split()[0] == modell) else f"{marke} {modell} {variante}"
    text = GETRIEBE_TEXT.get(getriebe or "")
    return (basis.strip() + (f" {text}" if text else "")).strip()


def start_modelle() -> List[Dict[str, Any]]:
    """Die Startliste mit aufgeloesten IDs — fertig fuer market_models.
    EZ-Jahre, km-Bereiche, Zeilen und Frequenz kommen aus den zentralen
    Standardwerten (markt.konfig) und sind je Modell im Admin aenderbar."""
    from markt import konfig
    kat = _katalog()
    raus = []
    for (mid, marke, modell, variante, fuel, kw_von, kw_bis, prio, getriebe) in STARTLISTE:
        ids = modell_ids(marke, modell, kat)
        label = seed_label(marke, modell, variante, getriebe)
        # Review 26.09.2026 abends P7: Karosserie leer (alle), ausser die Variante sagt es
        # ("Passat Variant" -> Kombi). Codes wie im Vergleich (mobile_service.CATEGORY_LABELS).
        body = "EstateCar" if "variant" in modell.lower() else None
        raus.append({"id": mid, "make": marke, "model": modell, "variant": variante, "fuel": fuel, "gearbox": getriebe, "body": body,
                     "power_kw_min": kw_von, "power_kw_max": kw_bis, "priority": prio, "label": label, "seed_version": SEED_VERSION,
                     "enabled": bool(ids), "status": "active" if ids else "paused",
                     "make_id": (ids or {}).get("make_id"), "model_id": (ids or {}).get("model_id"),
                     "ez_years": [b["year_from"] for b in konfig.EZ_BUCKETS_STANDARD],
                     "km_buckets": [dict(b) for b in konfig.KM_BUCKETS_STANDARD],
                     "rows": konfig.rows_je_segment(), "crawls_per_day": konfig.crawls_je_tag_standard(),
                     "grund": "" if ids else "Modell im mobile.de-Katalog nicht gefunden"})
    return raus
