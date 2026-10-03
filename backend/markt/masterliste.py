# -*- coding: utf-8 -*-
"""Fahrzeug-Masterliste (Master-Auftrag Ahmad 26.09.2026, Phase A): 170 Zeilen —
119 "neu" (EZ 2018-2024) und 51 "alt" (EZ 2012-2017). Jede Zeile ist EIN Suchauftrag
(market_models): Marke, Katalog-Modell (mobile.de), Variante (nur Beschriftung),
Kraftstoff, Getriebe (nie "alle"), kW-Bereich, EZ-Jahre, km-Profil, 5 Zeilen, 1 Abruf/Tag.

Regeln (nicht verhandelbar):
  * keine Zeile still entfernen oder ergaenzen — Korrekturen nur als Vorschlag im Bericht
  * DSG/S tronic/DCT/EAT8/EDC/Powershift/S-CVT/XTronic/9G-TRONIC = AUTOMATIC_GEAR
  * Benziner nie mit Diesel, Schalter nie mit Automatik
  * kW-Bereiche eng um die Motorisierung; wo unsicher: breiter UND Hinweis (needs_review)
  * Katalog-Aufloesung ueber katalog.modell_ids; nicht aufloesbar -> needs_review mit Grund
  * Import (Migration 18 / Admin-Knopf) ist idempotent: bestehende Seeds (seed_version <= 3)
    werden klassifiziert (UNCHANGED / CHANGED / NEW / DEPRECATED), Historie bleibt,
    nichts wird automatisch aktiv (status 'paused' — Aktivieren erst nach Testlauf)."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Dict, List, Optional, Tuple

from markt import katalog, konfig

log = logging.getLogger(__name__)

SEED_VERSION = 4
MASTER_DOK = "masterliste"          # market_config/masterliste {stand, zaehler}
ROWS = 5
CRAWLS_PER_DAY = 1
A, M = "AUTOMATIC_GEAR", "MANUAL_GEAR"
P, D, H, HD, E = "PETROL", "DIESEL", "HYBRID", "HYBRID_DIESEL", "ELECTRICITY"
MASTER_STATUS = ("UNCHANGED", "CHANGED", "NEW", "DEPRECATED")

# km-Profile (Obergrenzen in km; erster Bereich ab 20.000, jeder weitere ab Vorgaenger + 1)
KM_PROFILE: Dict[str, List[int]] = {
    "K1": [35000, 50000, 70000, 95000, 125000, 165000],
    "K2": [40000, 60000, 85000, 115000, 150000, 195000],
    "D1": [45000, 70000, 100000, 135000, 175000, 225000],
    "D2": [55000, 90000, 125000, 165000, 210000, 270000],
    "D3": [60000, 100000, 145000, 195000, 250000, 320000],
    "E": [35000, 55000, 80000, 105000, 135000, 175000],
}
KM_MIN = 20000


def km_buckets_fuer_profil(profil: str) -> List[Dict[str, int]]:
    """Materialisierte km-Bereiche eines Profils (z. B. K2: 20-40k, 40-60k, ... 150-195k)."""
    grenzen = KM_PROFILE[profil]
    raus, von = [], KM_MIN
    for bis in grenzen:
        raus.append({"min_km": von, "max_km": bis})
        von = bis + 1
    return raus


def _ez(text: str) -> List[int]:
    """'2021-24' -> [2021, 2022, 2023, 2024]."""
    von, bis = text.split("-")
    v = int(von)
    b = int(bis) if len(bis) == 4 else int(str(v)[:2] + bis)
    return list(range(v, b + 1))


def _z(nr, make, model, variant, fuel, gearbox, kw, ez, profil, gruppe, body=None, hinweis="", review=False):
    return {"master_row": nr, "make": make, "model": model, "variant": variant, "fuel": fuel, "gearbox": gearbox,
            "power_kw_min": (kw[0] if kw else None), "power_kw_max": (kw[1] if kw else None), "ez_years": _ez(ez),
            "km_profile": profil, "alter_gruppe": gruppe, "body": body, "hinweis": hinweis, "needs_review": bool(review)}


# Zeilen exakt wie im Master-Auftrag (Nr. | Modell/Variante | Kraftstoff | Getriebe | EZ | Profil).
# kW aus Fachwissen, eng um die Motorisierung; Mercedes-Bezeichnungen ohne kW (None), ausser Vito
# (114/116 CDI sind sonst derselbe Auftrag -> Zwilling). Kombi-Zeilen: Grundmodell + body EstateCar.
MASTERLISTE: List[Dict[str, Any]] = [
    # ---------------------------------------------------------------- NEU (119)
    _z(1, "Volkswagen", "Golf", "1.5 TSI", P, A, (96, 110), "2021-24", "K2", "neu"),
    _z(2, "Volkswagen", "Golf", "1.5 TSI", P, M, (96, 110), "2021-24", "K2", "neu"),
    _z(3, "Volkswagen", "Golf", "2.0 TDI", D, A, (110, 125), "2021-24", "D1", "neu", hinweis="150 PS; 2.0 TDI 115 PS (85 kW) und GTD (147 kW) ausgeschlossen"),
    _z(4, "Volkswagen", "Golf", "2.0 TDI", D, M, (110, 125), "2021-24", "D1", "neu", hinweis="150 PS; 2.0 TDI 115 PS (85 kW) und GTD (147 kW) ausgeschlossen"),
    _z(5, "Volkswagen", "Golf", "GTI 2.0 TSI", P, A, (175, 195), "2021-24", "K2", "neu", hinweis="GTI 245 PS; Clubsport (221 kW) ausgeschlossen"),
    _z(6, "Volkswagen", "Golf", "Variant 2.0 TDI", D, A, (110, 125), "2021-24", "D2", "neu", body="EstateCar", hinweis="Katalog: Golf + Karosserie Kombi"),
    _z(7, "Volkswagen", "Polo", "1.0 TSI", P, M, (66, 85), "2020-23", "K1", "neu"),
    _z(8, "Volkswagen", "Polo", "1.0 TSI", P, A, (66, 85), "2020-23", "K1", "neu"),
    _z(9, "Volkswagen", "T-Roc", "1.5 TSI", P, A, (105, 115), "2021-24", "K2", "neu"),
    _z(10, "Volkswagen", "Tiguan", "1.5 TSI", P, A, (96, 110), "2018-21", "K2", "neu"),
    _z(11, "Volkswagen", "Tiguan", "2.0 TDI", D, A, (110, 125), "2019-22", "D2", "neu", hinweis="150 PS; 190/200 PS (140/147 kW) ausgeschlossen"),
    _z(12, "Volkswagen", "Touran", "2.0 TDI", D, A, (110, 125), "2018-21", "D2", "neu", hinweis="150 PS; 1.6 TDI (85 kW) und 2.0 TDI 122 PS (90 kW) ausgeschlossen"),
    _z(13, "Volkswagen", "Passat Variant", "2.0 TDI", D, A, (110, 125), "2019-22", "D2", "neu", body="EstateCar", hinweis="150 PS; 190/240 PS ausgeschlossen (Katalog 'Passat Variant')"),
    _z(14, "Volkswagen", "Caddy", "2.0 TDI", D, A, (70, 95), "2021-24", "D3", "neu", hinweis="Caddy 5: 102/122 PS (75/90 kW)"),
    _z(15, "Volkswagen", "Caddy", "2.0 TDI", D, M, (70, 95), "2021-24", "D3", "neu", hinweis="Caddy 5: 102/122 PS (75/90 kW)"),
    _z(16, "Audi", "A3", "35 TFSI", P, A, (105, 115), "2021-24", "K2", "neu"),
    _z(17, "Audi", "A3", "35 TDI", D, A, (105, 115), "2021-24", "D1", "neu"),
    _z(18, "Audi", "A4", "35 TDI", D, A, (115, 125), "2021-24", "D2", "neu", hinweis="163 PS (120 kW)"),
    _z(19, "Audi", "A4", "40 TDI", D, A, (140, 155), "2021-24", "D2", "neu", hinweis="204 PS (150 kW); fruehere 190 PS (140 kW) eingeschlossen"),
    _z(20, "Audi", "A5", "40 TDI", D, A, (140, 155), "2021-24", "D2", "neu"),
    _z(21, "Audi", "A6", "40 TDI", D, A, (140, 155), "2020-23", "D3", "neu"),
    _z(22, "Audi", "A6", "45 TDI", D, A, (165, 185), "2020-23", "D3", "neu", hinweis="231/245 PS (170/180 kW) je Baujahr"),
    _z(23, "Audi", "Q2", "35 TFSI", P, A, (105, 115), "2020-23", "K2", "neu"),
    _z(24, "Audi", "Q3", "35 TFSI", P, A, (105, 115), "2021-24", "K2", "neu"),
    _z(25, "Audi", "Q5", "40 TDI", D, A, (140, 155), "2020-23", "D2", "neu"),
    _z(26, "BMW", "118", "118i", P, A, (98, 105), "2021-24", "K2", "neu"),
    _z(27, "BMW", "120", "120i", P, A, (125, 135), "2021-24", "K2", "neu"),
    _z(28, "BMW", "120", "120d", D, A, (135, 145), "2021-24", "D1", "neu"),
    _z(29, "BMW", "218 Gran Coupé", "218i", P, A, (98, 105), "2021-24", "K2", "neu", hinweis="218i Gran Coupe: Katalog '218 Gran Coupé' (ID 343) statt generisch '218' (ID 90)"),
    _z(30, "BMW", "320", "320i", P, A, (130, 140), "2021-24", "K2", "neu"),
    _z(31, "BMW", "320", "320d", D, A, (135, 150), "2021-24", "D2", "neu"),
    _z(32, "BMW", "330", "330e", H, A, (200, 220), "2021-24", "E", "neu", hinweis="Plug-in-Hybrid, Systemleistung 292 PS"),
    _z(33, "BMW", "420", "420d", D, A, (135, 150), "2021-24", "D2", "neu"),
    _z(34, "BMW", "520", "520d", D, A, (135, 150), "2019-22", "D3", "neu"),
    _z(35, "BMW", "X1", "18i", P, A, (98, 105), "2018-21", "K2", "neu"),
    _z(36, "BMW", "X1", "18d", D, A, (105, 115), "2018-21", "D2", "neu"),
    _z(37, "BMW", "X3", "20d", D, A, (135, 145), "2019-22", "D2", "neu"),
    _z(38, "BMW", "X3", "30d", D, A, (190, 215), "2019-22", "D2", "neu", hinweis="265/286 PS je Baujahr"),
    _z(39, "Mercedes-Benz", "A 180", "A 180", P, A, None, "2021-24", "K2", "neu"),
    _z(40, "Mercedes-Benz", "A 200", "A 200", P, A, None, "2021-24", "K2", "neu"),
    _z(41, "Mercedes-Benz", "A 200", "A 200 d", D, A, None, "2021-24", "D1", "neu", hinweis="Katalog 'A 200' + Kraftstoff Diesel"),
    _z(42, "Mercedes-Benz", "B 180", "B 180", P, A, None, "2020-23", "K2", "neu"),
    _z(43, "Mercedes-Benz", "CLA 200", "CLA 200", P, A, None, "2021-24", "K2", "neu"),
    _z(44, "Mercedes-Benz", "CLA 220", "CLA 220 d", D, A, None, "2021-24", "D2", "neu", hinweis="Katalog 'CLA 220' + Diesel"),
    _z(45, "Mercedes-Benz", "C 180", "C 180", P, A, None, "2021-24", "K2", "neu"),
    _z(46, "Mercedes-Benz", "C 200", "C 200", P, A, None, "2021-24", "K2", "neu"),
    _z(47, "Mercedes-Benz", "C 220", "C 220 d", D, A, None, "2021-24", "D2", "neu", hinweis="Katalog 'C 220' + Diesel"),
    _z(48, "Mercedes-Benz", "E 200", "E 200", P, A, None, "2019-22", "D2", "neu"),
    _z(49, "Mercedes-Benz", "E 220", "E 220 d", D, A, None, "2019-22", "D3", "neu", hinweis="Katalog 'E 220' + Diesel"),
    _z(50, "Mercedes-Benz", "GLA 200", "GLA 200", P, A, None, "2021-24", "K2", "neu"),
    _z(51, "Mercedes-Benz", "GLA 200", "GLA 200 d", D, A, None, "2021-24", "D2", "neu", hinweis="Katalog 'GLA 200' + Diesel"),
    _z(52, "Mercedes-Benz", "GLB 200", "GLB 200", P, A, None, "2021-24", "K2", "neu"),
    _z(53, "Mercedes-Benz", "GLC 220", "GLC 220 d", D, A, None, "2019-22", "D2", "neu", hinweis="Katalog 'GLC 220' + Diesel"),
    _z(54, "Mercedes-Benz", "Vito", "114 CDI", D, A, (95, 105), "2020-23", "D3", "neu", hinweis="136 PS (100 kW); kW noetig, sonst Zwilling mit 116 CDI"),
    _z(55, "Mercedes-Benz", "Vito", "116 CDI", D, A, (115, 125), "2020-23", "D3", "neu", hinweis="163 PS (120 kW); kW noetig, sonst Zwilling mit 114 CDI"),
    _z(56, "Skoda", "Fabia", "1.0 TSI", P, M, (66, 85), "2021-24", "K1", "neu"),
    _z(57, "Skoda", "Scala", "1.0 TSI", P, M, (66, 85), "2021-24", "K1", "neu"),
    _z(58, "Skoda", "Octavia", "1.5 TSI", P, A, (105, 115), "2021-24", "K2", "neu"),
    _z(59, "Skoda", "Octavia", "2.0 TDI", D, A, (110, 125), "2021-24", "D2", "neu", hinweis="150 PS; 115 PS (85 kW) und RS (147 kW) ausgeschlossen"),
    _z(60, "Skoda", "Superb", "2.0 TDI", D, A, (110, 125), "2019-22", "D3", "neu", hinweis="150 PS; 190 PS (140 kW) ausgeschlossen"),
    _z(61, "Skoda", "Karoq", "1.5 TSI", P, A, (105, 115), "2019-22", "K2", "neu"),
    _z(62, "Skoda", "Kodiaq", "2.0 TDI", D, A, (110, 125), "2019-22", "D2", "neu", hinweis="150 PS; 190/200 PS ausgeschlossen"),
    _z(63, "Skoda", "Enyaq", "80", E, A, (145, 155), "2021-24", "E", "neu", hinweis="Enyaq iV 80 = 150 kW; Katalog 'Enyaq' (60/85 ueber kW ausgeschlossen)"),
    _z(64, "Seat", "Ibiza", "1.0 TSI", P, M, (66, 85), "2021-24", "K1", "neu"),
    _z(65, "Seat", "Leon", "1.5 TSI", P, A, (105, 115), "2021-24", "K2", "neu", hinweis="Automatik nur als 1.5 eTSI (Mildhybrid) — Haendler listen teils 'Hybrid'", review=True),
    _z(66, "Seat", "Leon", "2.0 TDI", D, A, (110, 125), "2021-24", "D2", "neu"),
    _z(67, "Seat", "Ateca", "1.5 TSI", P, A, (105, 115), "2019-22", "K2", "neu"),
    _z(68, "Seat", "Ateca", "2.0 TDI", D, A, (110, 125), "2019-22", "D2", "neu", hinweis="150 PS; 190 PS ausgeschlossen"),
    _z(69, "Cupra", "Formentor", "1.5 TSI", P, A, (105, 115), "2021-24", "K2", "neu"),
    _z(70, "Cupra", "Formentor", "VZ 2.0 TSI", P, A, (175, 235), "2021-24", "K2", "neu", hinweis="VZ 245 PS (180 kW) und VZ 310 PS (228 kW) gemischt", review=True),
    _z(71, "Opel", "Corsa", "1.2 Turbo", P, M, (70, 100), "2021-24", "K1", "neu", hinweis="100/130 PS"),
    _z(72, "Opel", "Corsa", "1.2 Turbo", P, A, (70, 100), "2021-24", "K1", "neu", hinweis="100/130 PS"),
    _z(73, "Opel", "Astra", "1.2 Turbo", P, M, (75, 100), "2020-23", "K2", "neu", hinweis="110/130 PS"),
    _z(74, "Opel", "Astra", "1.5 Diesel", D, A, (70, 95), "2020-23", "D2", "neu", hinweis="105/122 PS"),
    _z(75, "Opel", "Mokka", "1.2 Turbo", P, A, (70, 100), "2021-24", "K2", "neu", hinweis="100/130 PS"),
    _z(76, "Opel", "Grandland (X)", "1.2 Turbo", P, A, (90, 100), "2019-22", "K2", "neu", hinweis="Katalog 'Grandland (X)'"),
    _z(77, "Opel", "Insignia", "2.0 Diesel", D, A, (120, 130), "2018-21", "D3", "neu", hinweis="170/174 PS; 2.0 BiTurbo (154 kW) ausgeschlossen"),
    _z(78, "Ford", "Fiesta", "1.0 EcoBoost", P, M, (70, 95), "2018-21", "K1", "neu", hinweis="95-125 PS"),
    _z(79, "Ford", "Focus", "1.0 EcoBoost", P, M, (70, 95), "2019-22", "K2", "neu", hinweis="100/125 PS"),
    _z(80, "Ford", "Focus", "1.5 EcoBlue", D, A, (85, 95), "2019-22", "D2", "neu", hinweis="120 PS (88 kW)"),
    _z(81, "Ford", "Kuga", "1.5 EcoBoost", P, A, (105, 115), "2020-23", "K2", "neu", hinweis="Kuga ab 2020: 1.5 EcoBoost 150 PS nur mit Schaltgetriebe — Automatik-Zeile bleibt womoeglich leer", review=True),
    _z(82, "Ford", "Kuga", "2.0 EcoBlue", D, A, (105, 145), "2020-23", "D2", "neu", hinweis="150 PS (110 kW) und 190 PS AWD (140 kW) gemischt", review=True),
    _z(83, "Ford", "Puma", "1.0 EcoBoost", P, A, (88, 96), "2021-24", "K2", "neu", hinweis="125 PS mHEV DCT"),
    _z(84, "Toyota", "Yaris", "1.5 Hybrid", H, A, (80, 95), "2021-24", "E", "neu", hinweis="116 PS Systemleistung (85 kW)"),
    _z(85, "Toyota", "Corolla", "1.8 Hybrid", H, A, (85, 105), "2021-24", "E", "neu", hinweis="122 PS; ab 2023 140 PS (103 kW)"),
    _z(86, "Toyota", "Corolla", "Touring Sports 2.0 Hybrid", H, A, (125, 150), "2021-24", "E", "neu", body="EstateCar", hinweis="Katalog 'Corolla' + Kombi; 180 PS, ab 2023 196 PS"),
    _z(87, "Toyota", "C-HR", "1.8 Hybrid", H, A, (85, 95), "2019-22", "E", "neu"),
    _z(88, "Toyota", "RAV 4", "2.5 Hybrid", H, A, (155, 170), "2019-22", "E", "neu", hinweis="Katalog 'RAV 4'; 218/222 PS"),
    _z(89, "Toyota", "Aygo (X)", "1.0", P, M, (50, 60), "2022-24", "K1", "neu", hinweis="Katalog 'Aygo (X)' — Aygo und Aygo X teilen die Modell-ID; EZ 2022+ = Aygo X"),
    _z(90, "Toyota", "Aygo (X)", "1.0", P, A, (50, 60), "2022-24", "K1", "neu", hinweis="Katalog 'Aygo (X)'; S-CVT = Automatik"),
    _z(91, "Hyundai", "i20", "1.0 T-GDI", P, M, (70, 90), "2021-24", "K1", "neu", hinweis="100/120 PS"),
    _z(92, "Hyundai", "i30", "1.5 T-GDI", P, A, (110, 120), "2021-24", "K2", "neu", hinweis="160 PS (117 kW) DCT"),
    _z(93, "Hyundai", "TUCSON", "1.6 T-GDI Hybrid", H, A, (160, 175), "2021-24", "E", "neu", hinweis="Katalog 'TUCSON'; 230 PS Systemleistung"),
    _z(94, "Hyundai", "KONA", "Hybrid", H, A, (100, 110), "2020-23", "E", "neu", hinweis="Katalog 'KONA'; 141 PS Systemleistung"),
    _z(95, "Hyundai", "SANTA FE", "2.2 CRDi", D, A, (140, 155), "2019-22", "D3", "neu", hinweis="Katalog 'SANTA FE'; 200/202 PS"),
    _z(96, "Kia", "cee'd / Ceed", "1.5 T-GDI", P, A, (110, 125), "2021-24", "K2", "neu", hinweis="Katalog 'cee'd / Ceed' (ID 26); 160 PS DCT"),
    _z(97, "Kia", "XCeed", "1.5 T-GDI", P, A, (110, 125), "2021-24", "K2", "neu", hinweis="160 PS DCT"),
    _z(98, "Kia", "Sportage", "1.6 T-GDI", P, A, (105, 135), "2019-22", "K2", "neu", hinweis="QL 177 PS (130 kW); NQ5 ab 2022 150/180 PS — Leistungsstufen gemischt", review=True),
    _z(99, "Kia", "Niro", "Hybrid", H, A, (100, 110), "2019-22", "E", "neu", hinweis="141 PS Systemleistung; Plug-in ueber Kraftstoff nicht trennbar (beide HYBRID)", review=True),
    _z(100, "Kia", "Sorento", "2.2 CRDi", D, A, (140, 155), "2019-22", "D3", "neu", hinweis="200/202 PS"),
    _z(101, "Peugeot", "208", "PureTech 100", P, M, (70, 78), "2020-23", "K1", "neu"),
    _z(102, "Peugeot", "208", "PureTech 100", P, A, (70, 78), "2020-23", "K1", "neu", hinweis="EAT8 = Automatik"),
    _z(103, "Peugeot", "308", "PureTech 130", P, A, (90, 100), "2021-24", "K2", "neu"),
    _z(104, "Peugeot", "3008", "PureTech 130", P, A, (90, 100), "2019-22", "K2", "neu"),
    _z(105, "Peugeot", "3008", "BlueHDi 130", D, A, (90, 100), "2019-22", "D2", "neu"),
    _z(106, "Peugeot", "5008", "BlueHDi 130", D, A, (90, 100), "2019-22", "D2", "neu"),
    _z(107, "Nissan", "Qashqai", "1.3 DIG-T", P, M, (100, 120), "2019-22", "K2", "neu", hinweis="140/158 PS"),
    _z(108, "Nissan", "Qashqai", "1.3 DIG-T", P, A, (100, 120), "2021-24", "K2", "neu", hinweis="158 PS XTronic = Automatik"),
    _z(109, "Nissan", "Qashqai", "1.5 dCi", D, M, (80, 90), "2018-21", "D2", "neu", hinweis="115 PS (85 kW)"),
    _z(110, "Nissan", "Juke", "1.0 DIG-T", P, M, (80, 90), "2020-23", "K1", "neu", hinweis="114/117 PS"),
    _z(111, "Nissan", "Micra", "IG-T 92", P, M, (65, 72), "2019-22", "K1", "neu"),
    _z(112, "Renault", "Clio", "TCe 90", P, M, (63, 70), "2021-24", "K1", "neu"),
    _z(113, "Renault", "Captur", "TCe 140", P, A, (98, 108), "2021-24", "K2", "neu", hinweis="EDC = Automatik"),
    _z(114, "Renault", "Megane", "TCe 140", P, A, (98, 108), "2019-22", "K2", "neu", hinweis="EDC = Automatik"),
    _z(115, "Volvo", "XC40", "B3/B4", P, A, (115, 150), "2021-24", "K2", "neu", hinweis="Mildhybrid -> PETROL laut Auftrag; B3 163 PS und B4 197 PS gemischt; Haendler listen Mildhybrid teils als Hybrid", review=True),
    _z(116, "Volvo", "XC60", "B4 Diesel", D, A, (140, 150), "2021-24", "D2", "neu", hinweis="197 PS Mildhybrid-Diesel; Haendler listen teils 'Hybrid (Diesel)'"),
    _z(117, "Volvo", "V60", "D4", D, A, (135, 145), "2018-21", "D2", "neu", hinweis="190 PS"),
    _z(118, "Tesla", "Model 3", "Long Range", E, A, (250, 370), "2021-24", "E", "neu", hinweis="Leistungsangabe je Baujahr 258/324/366 kW; Standard Range (208-239 kW) ausgeschlossen, Performance (377 kW) ausgeschlossen", review=True),
    _z(119, "Mazda", "CX-5", "2.2 Skyactiv-D", D, A, (105, 140), "2018-21", "D2", "neu", hinweis="150 PS (110 kW) und 184 PS (135 kW) gemischt", review=True),
    # ---------------------------------------------------------------- ALT (51)
    _z(120, "Volkswagen", "Golf", "1.4 TSI", P, M, (85, 115), "2013-16", "K2", "alt", hinweis="122/140/150 PS"),
    _z(121, "Volkswagen", "Golf", "1.4 TSI", P, A, (85, 115), "2013-16", "K2", "alt", hinweis="122/140/150 PS"),
    _z(122, "Volkswagen", "Golf", "1.6 TDI", D, M, (75, 85), "2013-16", "D2", "alt", hinweis="105/110 PS"),
    _z(123, "Volkswagen", "Golf", "2.0 TDI", D, A, (110, 125), "2013-16", "D2", "alt", hinweis="150 PS; GTD (135 kW) ausgeschlossen"),
    _z(124, "Volkswagen", "Polo", "1.2 TSI", P, M, (63, 85), "2013-16", "K1", "alt", hinweis="90/110 PS"),
    _z(125, "Volkswagen", "Tiguan", "2.0 TDI", D, A, (100, 135), "2012-15", "D2", "alt", hinweis="140/177 PS gemischt (110 PS ausgeschlossen)", review=True),
    _z(126, "Volkswagen", "Touran", "2.0 TDI", D, A, (100, 135), "2012-15", "D2", "alt", hinweis="140/177 PS gemischt", review=True),
    _z(127, "Volkswagen", "Caddy", "1.6 TDI", D, M, (55, 80), "2012-15", "D3", "alt", hinweis="75/102 PS"),
    _z(128, "Audi", "A3", "1.4 TFSI", P, A, (85, 115), "2013-16", "K2", "alt", hinweis="122/125/150 PS"),
    _z(129, "Audi", "A3", "2.0 TDI", D, A, (105, 140), "2013-16", "D2", "alt", hinweis="150/184 PS gemischt", review=True),
    _z(130, "Audi", "A4", "2.0 TDI", D, A, (100, 145), "2012-15", "D3", "alt", hinweis="136-190 PS, viele Leistungsstufen", review=True),
    _z(131, "Audi", "A5", "2.0 TDI", D, A, (100, 145), "2012-15", "D2", "alt", hinweis="143-190 PS gemischt", review=True),
    _z(132, "Audi", "A6", "2.0 TDI", D, A, (125, 145), "2012-15", "D3", "alt", hinweis="177/190 PS"),
    _z(133, "Audi", "Q5", "2.0 TDI", D, A, (100, 145), "2012-15", "D3", "alt", hinweis="143-190 PS gemischt", review=True),
    _z(134, "BMW", "116", "116i", P, A, (98, 105), "2012-15", "K2", "alt", hinweis="136 PS"),
    _z(135, "BMW", "118", "118i", P, A, (120, 130), "2012-15", "K2", "alt", hinweis="170 PS (125 kW) — anders als ab 2021 (100 kW)"),
    _z(136, "BMW", "118", "118d", D, A, (100, 115), "2012-15", "D2", "alt", hinweis="143/150 PS"),
    _z(137, "BMW", "320", "320i", P, A, (130, 140), "2012-15", "K2", "alt", hinweis="184 PS"),
    _z(138, "BMW", "320", "320d", D, A, (130, 145), "2012-15", "D3", "alt", hinweis="184/190 PS"),
    _z(139, "BMW", "520", "520d", D, A, (130, 145), "2012-15", "D3", "alt", hinweis="184/190 PS"),
    _z(140, "BMW", "X1", "18d", D, A, (100, 115), "2012-15", "D2", "alt", hinweis="143/150 PS"),
    _z(141, "BMW", "X3", "20d", D, A, (130, 145), "2012-15", "D3", "alt", hinweis="184/190 PS"),
    _z(142, "Mercedes-Benz", "A 180", "A 180", P, A, None, "2013-16", "K2", "alt"),
    _z(143, "Mercedes-Benz", "A 200", "A 200", P, A, None, "2013-16", "K2", "alt"),
    _z(144, "Mercedes-Benz", "CLA 200", "CLA 200", P, A, None, "2013-16", "K2", "alt"),
    _z(145, "Mercedes-Benz", "C 180", "C 180", P, A, None, "2014-17", "K2", "alt"),
    _z(146, "Mercedes-Benz", "C 200", "C 200", P, A, None, "2014-17", "K2", "alt"),
    _z(147, "Mercedes-Benz", "C 220", "C 220 d", D, A, None, "2014-17", "D3", "alt", hinweis="Katalog 'C 220' + Diesel (auch C 220 BlueTEC)"),
    _z(148, "Mercedes-Benz", "E 220", "E 220 CDI/BlueTEC", D, A, None, "2012-15", "D3", "alt", hinweis="Katalog 'E 220' + Diesel"),
    _z(149, "Mercedes-Benz", "GLA 200", "GLA 200", P, A, None, "2014-17", "K2", "alt"),
    _z(150, "Skoda", "Octavia", "1.4 TSI", P, A, (100, 115), "2013-16", "K2", "alt", hinweis="140/150 PS"),
    _z(151, "Skoda", "Octavia", "2.0 TDI", D, A, (110, 125), "2013-16", "D3", "alt", hinweis="150 PS; RS (135 kW) ausgeschlossen"),
    _z(152, "Skoda", "Superb", "2.0 TDI", D, A, (100, 145), "2012-15", "D3", "alt", hinweis="140-190 PS gemischt", review=True),
    _z(153, "Skoda", "Yeti", "2.0 TDI", D, A, (100, 130), "2012-15", "D2", "alt", hinweis="140/170 PS gemischt (110 PS ausgeschlossen)"),
    _z(154, "Skoda", "Rapid", "1.2 TSI", P, M, (60, 85), "2013-16", "K1", "alt", hinweis="86/105/110 PS"),
    _z(155, "Seat", "Leon", "1.4 TSI", P, M, (85, 115), "2013-16", "K2", "alt", hinweis="122/140/150 PS"),
    _z(156, "Seat", "Leon", "2.0 TDI", D, A, (110, 125), "2013-16", "D3", "alt", hinweis="150 PS; FR 184 PS (135 kW) ausgeschlossen"),
    _z(157, "Seat", "Ibiza", "1.2 TSI", P, M, (60, 85), "2012-15", "K1", "alt", hinweis="86/105/110 PS"),
    _z(158, "Opel", "Astra", "1.4 Turbo", P, M, (85, 105), "2012-15", "K2", "alt", hinweis="120/140 PS"),
    _z(159, "Opel", "Insignia", "2.0 CDTI", D, A, (95, 145), "2012-15", "D3", "alt", hinweis="130-195 PS, viele Leistungsstufen", review=True),
    _z(160, "Opel", "Mokka", "1.4 Turbo", P, A, (100, 105), "2013-16", "K2", "alt", hinweis="140 PS"),
    _z(161, "Ford", "Fiesta", "1.0 EcoBoost", P, M, (70, 95), "2013-16", "K1", "alt", hinweis="100/125 PS"),
    _z(162, "Ford", "Focus", "1.0 EcoBoost", P, M, (70, 95), "2013-16", "K2", "alt", hinweis="100/125 PS"),
    _z(163, "Ford", "Kuga", "2.0 TDCi", D, A, (100, 135), "2013-16", "D2", "alt", hinweis="140-180 PS gemischt", review=True),
    _z(164, "Toyota", "Yaris", "1.5 Hybrid", H, A, (70, 78), "2013-16", "E", "alt", hinweis="100 PS Systemleistung (74 kW)"),
    _z(165, "Toyota", "Auris", "1.8 Hybrid", H, A, (95, 105), "2013-16", "E", "alt", hinweis="136 PS Systemleistung"),
    _z(166, "Nissan", "Qashqai", "1.5 dCi", D, M, (78, 85), "2014-17", "D2", "alt", hinweis="110 PS"),
    _z(167, "Nissan", "Qashqai", "1.6 dCi", D, M, (92, 100), "2014-17", "D2", "alt", hinweis="130 PS"),
    _z(168, "Nissan", "Juke", "1.5 dCi", D, M, (78, 85), "2013-16", "D1", "alt", hinweis="110 PS"),
    _z(169, "Peugeot", "208", "PureTech 82", P, M, (57, 63), "2013-16", "K1", "alt"),
    _z(170, "Peugeot", "308", "BlueHDi 120", D, M, (85, 92), "2014-17", "D2", "alt"),
]

GETRIEBE_KURZ = {A: "auto", M: "schalt"}


def master_id(z: Dict[str, Any]) -> str:
    """Eindeutige Kennung: slug aus Marke, Modell+Variante (ohne Wiederholung), Getriebe, Altersgruppe —
    z. B. 'bmw-320d-auto-neu', 'volkswagen-golf-2-0-tdi-schalt-alt', 'mercedes-benz-a-200-d-auto-neu'."""
    from markt.auftraege import slug
    modell, variante = str(z["model"]), str(z["variant"])
    name = variante if katalog._norm(variante).startswith(katalog._norm(modell)) else f"{modell} {variante}"
    return slug(z["make"], name, GETRIEBE_KURZ.get(z["gearbox"], z["gearbox"]), z["alter_gruppe"])


def master_modelle(kat: Optional[List[dict]] = None) -> List[Dict[str, Any]]:
    """Alle 170 Zeilen als market_models-Dokumente (ohne Zeitstempel): Katalog-IDs aufgeloest,
    km-Bereiche materialisiert, seed_version 4, status 'paused' (nie automatisch aktiv).
    Nicht aufloesbar -> needs_review=True mit Grund (bleibt in der Liste, wird nie still entfernt)."""
    kat = kat if kat is not None else katalog._katalog()
    raus = []
    for z in MASTERLISTE:
        ids = katalog.modell_ids(z["make"], z["model"], kat)
        review, grund = bool(z["needs_review"]), (z["hinweis"] if z["needs_review"] else "")
        if not ids:
            review, grund = True, f"Modell '{z['model']}' der Marke '{z['make']}' im mobile.de-Katalog nicht gefunden"
        raus.append({"id": master_id(z), "master_row": int(z["master_row"]), "make": z["make"], "model": z["model"], "variant": z["variant"],
                     "label": katalog.seed_label(z["make"], z["model"], z["variant"], z["gearbox"]),
                     "fuel": z["fuel"], "gearbox": z["gearbox"], "body": z["body"],
                     "power_kw_min": z["power_kw_min"], "power_kw_max": z["power_kw_max"],
                     "ez_years": list(z["ez_years"]), "km_profile": z["km_profile"], "km_buckets": km_buckets_fuer_profil(z["km_profile"]),
                     "rows": ROWS, "crawls_per_day": CRAWLS_PER_DAY, "alter_gruppe": z["alter_gruppe"],
                     "priority": 1 if z["alter_gruppe"] == "neu" else 2, "country": "DE", "seller_type": None, "zip": None, "radius_km": None,
                     "seed_version": SEED_VERSION, "status": "paused", "enabled": False,
                     "make_id": (ids or {}).get("make_id"), "model_id": (ids or {}).get("model_id"),
                     "needs_review": review, "review_grund": grund or None, "hinweis": z["hinweis"] or None,
                     "grund": "" if ids else grund})
    return raus


# ---------------------------------------------------------------- Klassifikation bestehender Seeds
def _schluessel(m: Dict[str, Any]) -> Tuple[str, str, str, str, str, str]:
    """Fachlicher Schluessel eines Auftrags: Marke/Modell (Katalog-IDs), Kraftstoff, Getriebe, Karosserie,
    Variante (normalisiert). Gleicher Schluessel = derselbe Auftrag, evtl. mit anderen EZ/km/kW."""
    return (str(m.get("make_id") or ""), str(m.get("model_id") or ""), str(m.get("fuel") or "").upper(),
            str(m.get("gearbox") or "").upper(), str(m.get("body") or ""), katalog._norm(m.get("variant") or ""))


def _ez_ueberlappung(a: List[int], b: List[int]) -> int:
    return len(set(int(x) for x in a or []) & set(int(x) for x in b or []))


def _km_gleich(a: List[Dict[str, Any]], b: List[Dict[str, Any]]) -> bool:
    return sorted((int(x["min_km"]), int(x["max_km"])) for x in a or []) == sorted((int(x["min_km"]), int(x["max_km"])) for x in b or [])


def klassifizieren(bestehende: List[Dict[str, Any]], master: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Reine Rechnung (testbar ohne Datenbank): jedes bestehende Seed-Dokument bekommt UNCHANGED,
    CHANGED oder DEPRECATED und (ausser DEPRECATED) seine Masterzeile; Masterzeilen ohne Gegenstueck
    sind NEW. Bei zwei Masterzeilen mit gleichem Schluessel (neu/alt) gewinnt die groessere
    EZ-Ueberlappung, bei Gleichstand die Gruppe 'neu'."""
    from markt import auftraege
    frei = {m["master_row"]: m for m in master}
    zuordnung: Dict[str, Dict[str, Any]] = {}
    for alt in sorted(bestehende, key=lambda d: str(d.get("id") or "")):
        k = _schluessel(alt)
        kandidaten = [m for m in frei.values() if _schluessel(m) == k]
        if not kandidaten:
            zuordnung[alt["id"]] = {"status": "DEPRECATED", "master_row": None}
            continue
        kandidaten.sort(key=lambda m: (-_ez_ueberlappung(alt.get("ez_years") or [], m["ez_years"]), 0 if m["alter_gruppe"] == "neu" else 1, m["master_row"]))
        m = kandidaten[0]
        frei.pop(m["master_row"])
        gleich = (auftraege.definition_hash({**alt, "rows": alt.get("rows")}) == auftraege.definition_hash(m)
                  and sorted(int(j) for j in (alt.get("ez_years") or [])) == sorted(m["ez_years"])
                  and _km_gleich(alt.get("km_buckets") or [], m["km_buckets"])
                  and int(alt.get("crawls_per_day") or 1) == int(m["crawls_per_day"]))
        zuordnung[alt["id"]] = {"status": "UNCHANGED" if gleich else "CHANGED", "master_row": m["master_row"]}
    neu = sorted(frei)
    return {"zuordnung": zuordnung, "neu": neu}


def _master_felder(m: Dict[str, Any]) -> Dict[str, Any]:
    """Werte der Masterzeile, die ein bestehendes Dokument uebernimmt (Kennung und Label bleiben)."""
    return {k: m[k] for k in ("master_row", "km_profile", "km_buckets", "ez_years", "rows", "crawls_per_day", "power_kw_min", "power_kw_max",
                              "alter_gruppe", "needs_review", "review_grund", "hinweis", "seed_version", "make_id", "model_id")}


async def importieren(db, *, synchronisieren: bool = True, master: Optional[List[Dict[str, Any]]] = None,
                      bestehende_filter: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Masterliste einspielen (Migration 18 und Admin-Knopf) — idempotent und lock-frei (nur Upserts
    auf eindeutige Kennungen; ein zweiter Lauf findet alles schon eingetragen):
      * bestehende Seeds (seed_version <= 3, noch ohne master_row) -> UNCHANGED (nur Zuordnung),
        CHANGED (neue EZ/km/kW/Zeilen = neue Fassung ueber die vorhandene Versionierung; ein AKTIVER
        Auftrag wird pausiert, weil die neue Fassung erst einen Testlauf braucht — nie stillschweigend
        mit ungeprueften Filtern weitercrawlen), DEPRECATED (status 'archived', wartende Jobs storniert,
        Historie bleibt)
      * Masterzeilen ohne Gegenstueck -> NEW (status 'paused', nie automatisch aktiv)
      * eigene Auftraege des Admins (ohne seed_version) werden nie angefasst
    Ergebnis im Log und in market_config/masterliste {stand, zaehler}.
    `master`/`bestehende_filter` sind nur Testhaken (eigene Zeilen mit Praefix test-, Altbestand eingegrenzt)."""
    from markt import auftraege, segmente
    from markt.konfig import JOBS, KONFIG, MODELLE
    jetzt = konfig.jetzt_iso()
    test = master is not None
    master = master if master is not None else master_modelle()
    je_zeile = {m["master_row"]: m for m in master}
    z = {"unchanged": 0, "changed": 0, "new": 0, "deprecated": 0, "schon": 0, "needs_review": sum(1 for m in master if m["needs_review"]),
         "jobs_storniert": 0, "pausiert": 0, "zeilen": len(master)}
    zeilen = sorted(je_zeile)
    schon_zugeordnet = {int(d["master_row"]) async for d in db[MODELLE].find({"master_row": {"$in": zeilen}, **(bestehende_filter or {})},
                                                                              {"_id": 0, "master_row": 1})}
    z["schon"] = len(schon_zugeordnet)
    offen = [m for m in master if m["master_row"] not in schon_zugeordnet]
    bestehende = await db[MODELLE].find({"seed_version": {"$lte": 3}, "master_row": {"$exists": False}, "master_status": {"$exists": False},
                                         **(bestehende_filter or {})}, {"_id": 0}).to_list(5000)
    erg = klassifizieren(bestehende, offen)
    for alt in bestehende:
        e = erg["zuordnung"][alt["id"]]
        if e["status"] == "DEPRECATED":
            r = await db[MODELLE].update_one({"id": alt["id"], "master_status": {"$exists": False}},
                                             {"$set": {"master_status": "DEPRECATED", "status": "archived", "enabled": False, "archived_at": jetzt,
                                                       "review_grund": "Masterliste 26.09.2026: kein Gegenstueck — archiviert, Historie bleibt",
                                                       "updated_at": jetzt}})
            if r.modified_count:
                z["deprecated"] += 1
                j = await db[JOBS].update_many({"model_id": alt["id"], "status": "queued"},
                                               {"$set": {"status": "cancelled", "error": "Suchauftrag archiviert (Masterliste)", "finished_at": jetzt}})
                z["jobs_storniert"] += int(j.modified_count)
            continue
        m = je_zeile[e["master_row"]]
        setzen: Dict[str, Any] = {**_master_felder(m), "master_status": e["status"], "updated_at": jetzt}
        if e["status"] == "CHANGED":
            neu_doc = {**alt, **setzen}
            neu_hash = auftraege.definition_hash(neu_doc)
            alt_hash = (alt.get("definition_hash") if int(alt.get("hash_fassung") or 1) >= auftraege.HASH_FASSUNG else None) or auftraege.definition_hash(alt)
            version = segmente.modell_version(alt)
            if neu_hash != alt_hash:
                version += 1
            setzen.update({"version": version, "definition_hash": neu_hash, "filter_hash": auftraege.filter_hash(neu_doc),
                           "hash_fassung": auftraege.HASH_FASSUNG})
            if alt.get("status") == "active" or alt.get("enabled"):
                setzen.update({"status": "paused", "enabled": False,
                               "review_grund": (m.get("review_grund") or "Masterliste 26.09.2026: EZ/km/kW/Zeilen geaendert — erst Testlauf, dann aktivieren")})
                z["pausiert"] += 1
        r = await db[MODELLE].update_one({"id": alt["id"], "master_row": {"$exists": False}}, {"$set": setzen})
        if r.modified_count:
            z["unchanged" if e["status"] == "UNCHANGED" else "changed"] += 1
            if setzen.get("status") == "paused":
                j = await db[JOBS].update_many({"model_id": alt["id"], "status": "queued"},
                                               {"$set": {"status": "cancelled", "error": "Suchauftrag pausiert (Masterliste, Testlauf noetig)", "finished_at": jetzt}})
                z["jobs_storniert"] += int(j.modified_count)
    from pymongo.errors import DuplicateKeyError
    # eigene Auftraege des Admins (ohne seed_version) mit gleichem fachlichem Schluessel und ueberlappenden EZ-Jahren:
    # die neue Masterzeile wird NICHT still doppelt — sie kommt als "zu pruefen" mit Hinweis auf den eigenen Auftrag
    eigene = await db[MODELLE].find({"seed_version": {"$exists": False}, "status": {"$ne": "archived"}, **(bestehende_filter or {})},
                                    {"_id": 0}).to_list(5000)
    for nr in erg["neu"]:
        m = je_zeile[nr]
        zwilling = next((e for e in eigene if _schluessel(e) == _schluessel(m) and _ez_ueberlappung(e.get("ez_years") or [], m["ez_years"])), None)
        if zwilling:
            m = {**m, "needs_review": True,
                 "review_grund": f"Eigener Suchauftrag mit gleicher Definition vorhanden: {zwilling.get('label') or zwilling['id']} — doppelt beobachten?"}
            z["needs_review"] += 0 if je_zeile[nr]["needs_review"] else 1
        doc = {**m, "master_status": "NEW", "version": 1, "definition_hash": auftraege.definition_hash(m), "filter_hash": auftraege.filter_hash(m),
               "hash_fassung": auftraege.HASH_FASSUNG, "created_at": jetzt, "updated_at": jetzt}
        try:
            # bestehende_filter grenzt nur im Test ein (dort gibt es die echte Masterliste schon aus Migration 18)
            if await db[MODELLE].find_one({"$or": [{"id": m["id"]}, {"master_row": nr, **(bestehende_filter or {})}]}, {"_id": 1}):
                z["schon"] += 1
                continue
            await db[MODELLE].insert_one(dict(doc))
            z["new"] += 1
        except DuplicateKeyError:
            z["schon"] += 1          # zweiter Server war schneller — idempotent
    if synchronisieren:
        z["segmente"] = (await segmente.synchronisieren(db, nachplanen=False)).get("segmente", 0)
    if not test:
        await db[KONFIG].update_one({"_id": MASTER_DOK}, {"$set": {"stand": jetzt, "seed_version": SEED_VERSION, "zaehler": z, "updated_at": jetzt}},
                                    upsert=True)
        log.info("Masterliste v%d importiert: %s", SEED_VERSION, z)
    return z


# ---------------------------------------------------------------- Sammel-Testlauf (Aktivieren nur nach Testlauf)
# Nach dem Import ist jede Masterzeile pausiert, und Aktivieren geht nur mit einem bestandenen Testlauf ueber
# ALLE Segmente. 170 Testlaeufe einzeln zu klicken ist praktisch unmoeglich — der Sammel-Testlauf prueft alle
# pausierten Masterlisten-Auftraege nacheinander (hoechstens TESTLAUF_ALLE_PARALLEL gleichzeitig), vermerkt das
# Ergebnis am Auftrag und aktiviert auf Wunsch NUR die bestandenen. Zeilen "zu pruefen" (needs_review) nur, wenn
# ausdruecklich gewuenscht. Zwei Server: der Lauf-Merker (market_config/testlauf_alle) wird atomar beansprucht und
# per Lease gehalten; ein abgebrochener Lauf (Neustart) gilt nach Ablauf der Lease als beendet und kann neu starten.
TESTLAUF_ALLE_DOK = "testlauf_alle"
TESTLAUF_ALLE_PARALLEL = 2
TESTLAUF_ALLE_LEASE_S = 900
TESTLAUF_ALLE_FEHLER_MAX = 200


class LaeuftSchon(RuntimeError):
    """Ein Sammel-Testlauf laeuft bereits (auf diesem oder dem anderen Server)."""


def _kandidaten_filter(mit_review: bool, zusatz: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    f: Dict[str, Any] = {"status": "paused", "master_row": {"$exists": True, "$ne": None}, "model_id": {"$nin": [None, ""]}}
    if not mit_review:
        f["needs_review"] = {"$ne": True}
    return {**f, **(zusatz or {})}


def testlauf_kosten_usd(m: Dict[str, Any]) -> float:
    """Geschaetzte Kosten EINES Testlaufs: ein Buendel-Lauf, je Segment TESTLAUF_JE_SEGMENT Zeilen."""
    from markt import auftraege
    segs = len(m.get("ez_years") or []) * len(m.get("km_buckets") or [])
    zeilen = auftraege.TESTLAUF_JE_SEGMENT * max(1, segs)
    return konfig.kosten_je_lauf_usd(konfig.actor(), zeilen)


async def testlauf_alle_status(db, *, zusatz: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Stand des Sammel-Testlaufs + wie viele Auftraege ein neuer Lauf pruefen wuerde (mit Kostenschaetzung)."""
    from markt.konfig import KONFIG, MODELLE
    doc = await db[KONFIG].find_one({"_id": TESTLAUF_ALLE_DOK}, {"_id": 0}) or {}
    jetzt = konfig.jetzt_iso()
    laeuft = bool(doc.get("laeuft")) and str(doc.get("lease_until") or "") > jetzt
    kandidaten = await db[MODELLE].find(_kandidaten_filter(False, zusatz), {"_id": 0, "ez_years": 1, "km_buckets": 1}).to_list(5000)
    review = await db[MODELLE].count_documents({**_kandidaten_filter(True, zusatz), "needs_review": True})
    return {**doc, "laeuft": laeuft, "abgebrochen_lease": bool(doc.get("laeuft")) and not laeuft,
            "kandidaten": len(kandidaten), "kandidaten_review": review,
            "kosten_schaetzung_usd": round(sum(testlauf_kosten_usd(m) for m in kandidaten), 2)}


async def testlauf_alle_beanspruchen(db, *, aktivieren: bool, mit_review: bool, wer: str = "") -> str:
    """Lauf-Merker atomar setzen (upsert). Laeuft schon einer mit gueltiger Lease -> LaeuftSchon."""
    import uuid
    from pymongo.errors import DuplicateKeyError
    from markt.konfig import KONFIG
    jetzt = konfig.jetzt()
    lauf_id = uuid.uuid4().hex
    try:
        await db[KONFIG].update_one(
            {"_id": TESTLAUF_ALLE_DOK, "$or": [{"laeuft": {"$ne": True}}, {"lease_until": {"$lt": jetzt.isoformat()}}]},
            {"$set": {"laeuft": True, "lauf_id": lauf_id, "lease_until": (jetzt + timedelta(seconds=TESTLAUF_ALLE_LEASE_S)).isoformat(),
                      "gestartet_at": jetzt.isoformat(), "gestartet_von": wer, "aktivieren": bool(aktivieren), "mit_review": bool(mit_review),
                      "gesamt": 0, "fertig": 0, "bestanden": 0, "nicht_bestanden": 0, "aktiviert": 0, "fehler": [], "kosten_usd": 0.0,
                      "abbruch": None, "beendet_at": None}},
            upsert=True)
    except DuplicateKeyError:
        raise LaeuftSchon("Ein Sammel-Testlauf läuft bereits — bitte abwarten")
    return lauf_id


async def testlauf_alle_wiederaufnehmen(db) -> Optional[str]:
    """Pruefliste 01.10.2026 (Markt Nr. 5): der Sammel-Testlauf lebte nur im RAM EINES Web-Prozesses — nach
    Neustart/Rollout dieses Prozesses stand der Merker auf "laeuft", bis die Lease (15 min) ablief, und der
    Lauf musste von Hand neu gestartet werden. Jetzt prueft jeder Prozess beim Start: Merker "laeuft" mit
    abgelaufener Lease -> diesen Lauf wieder aufnehmen (atomar, nur ein Server gewinnt). Schon bestandene
    Auftraege kosten keinen zweiten Lauf (testlauf_ok_hash). Ohne APIFY_TOKEN passiert nichts."""
    from markt.konfig import KONFIG
    if not konfig.token():
        return None
    doc = await db[KONFIG].find_one({"_id": TESTLAUF_ALLE_DOK}, {"_id": 0}) or {}
    if not doc.get("laeuft") or str(doc.get("lease_until") or "") > konfig.jetzt_iso():
        return None
    aktivieren, mit_review = bool(doc.get("aktivieren")), bool(doc.get("mit_review"))
    try:
        lauf_id = await testlauf_alle_beanspruchen(db, aktivieren=aktivieren, mit_review=mit_review,
                                                  wer=f"wiederaufnahme:{doc.get('gestartet_von') or ''}")
    except LaeuftSchon:
        return None
    await db[KONFIG].update_one({"_id": TESTLAUF_ALLE_DOK, "lauf_id": lauf_id},
                                {"$set": {"wiederaufgenommen_von": doc.get("lauf_id"), "wiederaufgenommen_at": konfig.jetzt_iso()}})
    log.info("Sammel-Testlauf %s nach Neustart wieder aufgenommen (vorher %s)", lauf_id, doc.get("lauf_id"))
    await testlauf_alle_ausfuehren(db, lauf_id, aktivieren=aktivieren, mit_review=mit_review)
    return lauf_id


def _kurz(erg: Dict[str, Any]) -> Dict[str, Any]:
    """Ergebnis eines Testlaufs fuer den Auftrag (nur Zahlen und Gruende, keine Inseratsdaten)."""
    gruende: List[str] = []
    for s in erg.get("segmente") or []:
        for g in s.get("gruende") or []:
            if g not in gruende:
                gruende.append(g)
    grund = ""
    if not erg.get("bestanden"):
        if int(erg.get("verworfen_gesamt") or 0):
            grund = f"{erg['verworfen_gesamt']} Zeile(n) verworfen: {'; '.join(gruende[:3])}"
        elif int(erg.get("sortierung_ungueltig") or 0):
            grund = f"Sortierung in {erg['sortierung_ungueltig']} Segment(en) ungültig"
        elif int(erg.get("nicht_zuordenbar") or 0):
            grund = f"{erg['nicht_zuordenbar']} Zeile(n) keinem Segment zuzuordnen"
        elif int(erg.get("segmente_ungueltig") or 0):
            grund = f"{erg['segmente_ungueltig']} Segment(e) mit Zeilen ohne Fahrzeugdaten"
        elif int(erg.get("segmente_geprueft") or 0) != int(erg.get("segmente_gesamt") or 0):
            grund = "Testlauf deckt nicht alle Segmente ab"
        else:
            grund = "kein einziger gültiger Treffer"
    return {"at": konfig.jetzt_iso(), "bestanden": bool(erg.get("bestanden")), "grund": grund or None,
            "segmente": int(erg.get("segmente_gesamt") or 0), "leer": int(erg.get("leer") or 0),
            "gueltig": int(erg.get("gueltig_gesamt") or 0), "verworfen": int(erg.get("verworfen_gesamt") or 0),
            "usd": erg.get("usd"), "sammel": True}


async def testlauf_alle_ausfuehren(db, lauf_id: str, *, aktivieren: bool, mit_review: bool,
                                   zusatz: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Alle pausierten Masterlisten-Auftraege pruefen (je Auftrag EIN Testlauf ueber alle Segmente, gegen das
    Marktbudget) und — mit aktivieren=True — die bestandenen aktivieren (einmal synchronisieren am Ende).
    Budget aufgebraucht -> Abbruch mit Grund. `zusatz` ist nur ein Testhaken (Praefix test-)."""
    import asyncio
    from markt import auftraege, segmente
    from markt.konfig import KONFIG, MODELLE
    kandidaten = await db[MODELLE].find(_kandidaten_filter(mit_review, zusatz), {"_id": 0}).sort("master_row", 1).to_list(5000)
    await db[KONFIG].update_one({"_id": TESTLAUF_ALLE_DOK, "lauf_id": lauf_id}, {"$set": {"gesamt": len(kandidaten)}})
    stopp: Dict[str, Optional[str]] = {"grund": None}
    zu_aktivieren: List[str] = []
    sem = asyncio.Semaphore(max(1, TESTLAUF_ALLE_PARALLEL))

    async def _vermerken(inc: Dict[str, Any], fehler: Optional[Dict[str, Any]] = None) -> None:
        aenderung: Dict[str, Any] = {"$set": {"lease_until": (konfig.jetzt() + timedelta(seconds=TESTLAUF_ALLE_LEASE_S)).isoformat()}}
        if inc:
            aenderung["$inc"] = inc
        if fehler:
            aenderung["$push"] = {"fehler": {"$each": [fehler], "$slice": -TESTLAUF_ALLE_FEHLER_MAX}}
        r = await db[KONFIG].update_one({"_id": TESTLAUF_ALLE_DOK, "lauf_id": lauf_id, "laeuft": True}, aenderung)
        if not r.matched_count and not stopp["grund"]:
            stopp["grund"] = "Lauf beendet oder von einem anderen Lauf übernommen"

    async def _eins(m: Dict[str, Any]) -> None:
        async with sem:
            if stopp["grund"]:
                return
            h = auftraege.filter_hash(m)
            if m.get("testlauf_ok_hash") == h and m.get("testlauf_ok_at"):
                # schon bestanden (z. B. frueherer, abgebrochener Sammellauf) — kein zweiter bezahlter Lauf
                if aktivieren:
                    zu_aktivieren.append(m["id"])
                await _vermerken({"fertig": 1, "bestanden": 1})
                return
            try:
                erg = await auftraege.testlauf(m, db=db)
            except auftraege.Ungueltig as ex:
                if "Monatsbudget" in str(ex):
                    stopp["grund"] = str(ex)
                await db[MODELLE].update_one({"id": m["id"]}, {"$set": {"testlauf_letzter": {"at": konfig.jetzt_iso(), "bestanden": False,
                                                                                               "grund": str(ex)[:200], "sammel": True}}})
                await _vermerken({"fertig": 1, "nicht_bestanden": 1}, {"id": m["id"], "label": m.get("label"), "grund": str(ex)[:200]})
                return
            except Exception as ex:  # noqa: BLE001
                log.warning("Sammel-Testlauf %s gescheitert: %s", m["id"], ex)
                await _vermerken({"fertig": 1, "nicht_bestanden": 1}, {"id": m["id"], "label": m.get("label"), "grund": f"Fehler: {str(ex)[:180]}"})
                return
            kurz = _kurz(erg)
            setzen: Dict[str, Any] = {"testlauf_letzter": kurz}
            ok = bool(erg.get("bestanden")) and erg.get("filter_hash") == h
            if ok:
                setzen.update({"testlauf_ok_at": erg["testlauf_ok_at"], "testlauf_ok_hash": erg["testlauf_ok_hash"]})
            # nur, wenn der Auftrag inzwischen nicht geaendert wurde (noch pausiert, gleicher Bearbeitungsstand)
            r = await db[MODELLE].update_one({"id": m["id"], "status": "paused", "updated_at": m.get("updated_at")}, {"$set": setzen})
            if ok and r.modified_count and aktivieren:
                zu_aktivieren.append(m["id"])
            inc = {"fertig": 1, ("bestanden" if ok else "nicht_bestanden"): 1, "kosten_usd": float(erg.get("usd") or 0)}
            await _vermerken(inc, None if ok else {"id": m["id"], "label": m.get("label"), "grund": kurz.get("grund") or "nicht bestanden"})

    try:
        await asyncio.gather(*(_eins(m) for m in kandidaten))
        aktiviert = 0
        for mid in zu_aktivieren:
            try:
                await auftraege.status_setzen(db, mid, "active", sync=False)
                aktiviert += 1
            except auftraege.Ungueltig as ex:
                await _vermerken({}, {"id": mid, "grund": f"nicht aktiviert: {str(ex)[:180]}"})
        if aktiviert:
            await segmente.synchronisieren(db)
        await db[KONFIG].update_one({"_id": TESTLAUF_ALLE_DOK, "lauf_id": lauf_id}, {"$set": {"aktiviert": aktiviert}})
    finally:
        await db[KONFIG].update_one({"_id": TESTLAUF_ALLE_DOK, "lauf_id": lauf_id},
                                    {"$set": {"laeuft": False, "beendet_at": konfig.jetzt_iso(), "abbruch": stopp["grund"]}})
    doc = await db[KONFIG].find_one({"_id": TESTLAUF_ALLE_DOK}, {"_id": 0}) or {}
    log.info("Sammel-Testlauf %s: %s", lauf_id, {k: doc.get(k) for k in ("gesamt", "bestanden", "nicht_bestanden", "aktiviert", "abbruch")})
    return doc
