# -*- coding: utf-8 -*-
"""mobile.de-Such-URL je Segment — kompaktes Format wie im Vergleich
(mobile_service.build_search_url), plus Preis aufsteigend (sb=p&od=up).
Probelauf 25.09.2026: der Scraper respektiert diese Sortierung ueber die
Such-API (20 Treffer, 7.190 -> 17.999 EUR, alle im km-Bereich)."""
from __future__ import annotations

from typing import Any, Dict, List, Tuple
from urllib.parse import urlencode

BASIS = "https://suchen.mobile.de/fahrzeuge/search.html"


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
    if modell.get("seller_type"):
        p.append(("st", str(modell["seller_type"])))          # DEALER | FSBO (privat)
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
