# -*- coding: utf-8 -*-
"""mobile.de-Such-URL je Segment — kompaktes Format wie im Vergleich
(mobile_service.build_search_url), plus Preis aufsteigend (sb=p&od=up).
Probelauf 25.09.2026: der Scraper respektiert diese Sortierung ueber die
Such-API (20 Treffer, 7.190 -> 17.999 EUR, alle im km-Bereich)."""
from __future__ import annotations

from typing import Any, Dict, List, Tuple
from urllib.parse import urlencode

from markt.normalisieren import KAROSSERIE_CODES

BASIS = "https://suchen.mobile.de/fahrzeuge/search.html"
# Nr. 84: Uebersetzung der internen Verkaeuferart (DEALER/PRIVATE) in den mobile.de-Parameter st=
VERKAEUFER_URL = {"DEALER": "DEALER", "PRIVATE": "FSBO", "FSBO": "FSBO"}


def parameter(segment: Dict[str, Any], modell: Dict[str, Any]) -> List[Tuple[str, str]]:
    p: List[Tuple[str, str]] = [("isSearchRequest", "true"), ("s", "Car"), ("vc", "Car")]
    make_id, model_id = modell.get("make_id"), modell.get("model_id")
    if make_id and model_id:
        p.append(("ms", f"{make_id};{model_id};;;"))
    elif make_id:
        p.append(("ms", f"{make_id};;;;"))
    if modell.get("fuel"):
        p.append(("ft", str(modell["fuel"])))
    kw_von, kw_bis = modell.get("power_kw_min"), modell.get("power_kw_max")
    if kw_von or kw_bis:
        p.append(("pw", f"{kw_von or ''}:{kw_bis or ''}"))
    if modell.get("gearbox"):
        p.append(("tr", str(modell["gearbox"])))
    # Review 26.09.2026 abends P7: Karosserie (c=) — dieselben Codes wie der Vergleich
    # (Limousine, EstateCar, OffRoad, Cabrio, SportsCar, SmallCar, Van); nur bekannte Codes
    if modell.get("body") and str(modell["body"]) in KAROSSERIE_CODES:
        p.append(("c", str(modell["body"])))
    if modell.get("seller_type"):
        # Reparaturwelle 6 Nr. 84: intern heisst es DEALER | PRIVATE — nur die mobile.de-URL sagt FSBO
        p.append(("st", VERKAEUFER_URL.get(str(modell["seller_type"]).upper(), str(modell["seller_type"]))))
    if modell.get("zip") and modell.get("radius_km"):
        p.append(("zipr", f"{modell['zip']}:{int(modell['radius_km'])}"))
    if modell.get("country") and str(modell["country"]).upper() != "DE":
        p.append(("cn", str(modell["country"]).upper()))
    mn, mx = segment.get("min_km"), segment.get("max_km")
    if mn is not None or mx is not None:
        p.append(("ml", f"{'' if mn is None else int(mn)}:{'' if mx is None else int(mx)}"))
    # spaeter: EZ-Buckets je Segment (year_from/year_to) — Datenmodell ist vorbereitet
    y_von, y_bis = segment.get("year_from"), segment.get("year_to")
    if y_von or y_bis:
        p.append(("fr", f"{y_von or ''}:{y_bis or ''}"))
    p.append(("dam", "0"))            # keine Unfallfahrzeuge unter den "guenstigsten"
    p.append(("sb", "p"))
    p.append(("od", "up"))
    return p


def such_url(segment: Dict[str, Any], modell: Dict[str, Any]) -> str:
    return BASIS + "?" + urlencode(parameter(segment, modell), safe="")
