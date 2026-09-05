# -*- coding: utf-8 -*-
"""Apify-Scraper fuer mobile.de (memo23/mobile-de-scraper).

Prueft die Feld-Zuordnung gegen einen ECHTEN Datensatz des Actors
(tests/fixtures/apify_mobile_item.json, Lauf vom 31.08.2026) sowie die
Schutzlogik: nur Inserats-URLs duerfen an den Actor gehen (eine Such-URL
wuerde hunderte Ergebnisse abrufen und unnoetig Geld kosten).
"""
import json
from pathlib import Path

import pytest

from mobile_service import (
    AD_ID_RE, _apify_bild_url, _apify_leistung, _apify_zahl,
    _parse_apify_item, detail_looks_like_listing,
)

FIXTURE = Path(__file__).parent / "fixtures" / "apify_mobile_item.json"


@pytest.fixture()
def item():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))[0]


def test_parse_echter_datensatz(item):
    v = _parse_apify_item(item, "42196329136896")
    assert v["mobile_ad_id"] == "42196329136896"
    assert v["make_label"] == "Volkswagen"
    assert v["model_label"] == "Golf"
    assert v["model_description"].startswith("VII 2.0 GTI TCR")
    assert v["list_price"] == 23850.0
    assert v["mileage"] == 111016
    assert v["first_registration"] == "06/2019"
    assert v["power_kw"] == 213 and v["power_ps"] == 290
    assert v["fuel_label"] == "Benzin"          # "Petrol" -> deutsch
    assert v["gearbox_label"] == "Automatik"    # "Automatic" -> deutsch
    assert v["category_label"] == "Limousine"
    assert v["displacement"] == 1984
    assert v["seats"] == 5 and v["doors"] == "4/5"
    assert v["color"] == "Weiß"                 # "White" -> deutsch
    assert v["previous_owners"] == "2"
    assert v["accident_damaged"] is False
    assert v["roadworthy"] is True
    assert v["hu"] == "Neu"
    # Haendler + Adresse aus contact/address2 ("DE-97078 Würzburg")
    assert v["seller_name"] == "AUTO-MAGER.DE"
    assert v["seller_address"] == "Versbacher Str. 6"
    assert v["seller_zip"] == "97078"
    assert v["seller_city"] == "Würzburg"
    assert v["seller_phone"].startswith("+49")
    # Beschreibung: HTML entfernt, deutscher Text erhalten
    assert "Auto Mager" in v["description"]
    assert "<" not in v["description"]
    # Bilder: alle 46, vollstaendige URLs mit Groessen-Regel
    assert v["image_count"] == 46
    assert v["images"] == v["image_urls"]
    assert all(u.startswith("https://img.classistatic.de/") for u in v["images"])
    assert all(u.endswith("?rule=mo-1024.jpg") for u in v["images"])
    assert v["detail_url"].startswith("https://suchen.mobile.de/auto-inserat/")
    assert len(v["features"]) == 69


def test_parse_deutsche_lokalisierung(item):
    """Der Actor liefert je nach Proxy-Land deutsche Werte — die muessen
    genauso funktionieren wie die englischen."""
    uebersetzung = {"mileage": "111.016 km", "power": "213 kW (290 PS)",
                    "fuel": "Benzin", "transmission": "Automatik",
                    "color": "Weiß"}
    for a in item["attributes"]:
        if a["tag"] in uebersetzung:
            a["value"] = uebersetzung[a["tag"]]
    v = _parse_apify_item(item, "42196329136896")
    assert v["mileage"] == 111016
    assert v["power_ps"] == 290
    assert v["fuel_label"] == "Benzin"
    assert v["gearbox_label"] == "Automatik"
    assert v["color"] == "Weiß"


def test_preis_varianten(item):
    item["price"] = {"gross": {"amount": 9990, "currency": "EUR"}}
    assert _parse_apify_item(item, "1")["list_price"] == 9990.0
    item["price"] = 12345
    assert _parse_apify_item(item, "1")["list_price"] == 12345.0
    item["price"] = None
    assert _parse_apify_item(item, "1")["list_price"] is None


def test_bild_url_varianten():
    assert _apify_bild_url({"uri": "img.classistatic.de/api/v1/mo-prod/images/ab/xyz"}) \
        == "https://img.classistatic.de/api/v1/mo-prod/images/ab/xyz?rule=mo-1024.jpg"
    # Bereits vollstaendige URLs bleiben unangetastet
    assert _apify_bild_url("https://example.com/foto.jpg") == "https://example.com/foto.jpg"
    assert _apify_bild_url({"uri": "//host.de/bild.png"}) == "https://host.de/bild.png"
    assert _apify_bild_url({}) is None
    assert _apify_bild_url(None) is None


def test_hilfsparser():
    assert _apify_zahl("111,016 km") == 111016
    assert _apify_zahl("1.984 ccm") == 1984
    assert _apify_zahl(None) is None
    assert _apify_leistung("213 kW (290 hp)") == (213, 290)
    assert _apify_leistung("213 kW (290 PS)") == (213, 290)
    # Nur kW angegeben -> PS wird umgerechnet
    kw, ps = _apify_leistung("100 kW")
    assert kw == 100 and ps == 136


def test_nur_inserats_urls_an_den_actor():
    """Schutz vor Kosten-Explosion: Such-URLs duerfen NIE direkt an den
    Actor gehen (der wuerde die komplette Ergebnisliste abrufen)."""
    assert detail_looks_like_listing(
        "https://suchen.mobile.de/auto-inserat/vw-golf/42196329136896.html")
    assert detail_looks_like_listing(
        "https://suchen.mobile.de/fahrzeuge/details.html?id=42196329136896")
    assert not detail_looks_like_listing(
        "https://suchen.mobile.de/fahrzeuge/search.html?dam=false&s=Car")
    assert not detail_looks_like_listing("https://www.kleinanzeigen.de/s-anzeige/x/123")
    assert not detail_looks_like_listing("")


def test_lange_mobile_ids_erkannt():
    """Neue mobile.de-IDs haben 14 Stellen — der alte Regex (max. 12)
    haette sie verstuemmelt."""
    m = AD_ID_RE.search("https://suchen.mobile.de/fahrzeuge/details.html?id=42196329136896")
    assert m and m.group(1) == "42196329136896"
