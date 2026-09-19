# -*- coding: utf-8 -*-
"""AutoScout24-Scraper (ivanvs/autoscout-scraper via Apify).

Prueft die Feld-Zuordnung gegen einen ECHTEN Datensatz des Actors
(tests/fixtures/apify_autoscout_item.json, Lauf vom 01.09.2026) sowie
den Kostenschutz: nur Inserats-URLs (/angebote/) duerfen an den Actor.
"""
import json
from pathlib import Path

import pytest

from autoscout_service import (
    detail_looks_like_autoscout_listing, parse_autoscout_item,
)

FIXTURE = Path(__file__).parent / "fixtures" / "apify_autoscout_item.json"
UUID = "719b9573-d82f-4d29-85e0-54b5708d9aa6"


@pytest.fixture()
def item():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))[0]


def test_parse_echter_datensatz(item):
    v = parse_autoscout_item(item, UUID)
    assert v["mobile_ad_id"] == UUID
    assert v["make_label"] == "Mercedes-Benz"
    assert v["model_label"] == "B 160"
    assert v["model_description"] == "BlueEFFICIENCY SPORT EDITION"
    assert v["list_price"] == 2000.0
    assert v["mileage"] == 242000
    assert v["first_registration"] == "03/2010"
    assert v["power_kw"] == 70 and v["power_ps"] == 95
    assert v["fuel_label"] == "Benzin"
    assert v["gearbox_label"] == "Schaltgetriebe"
    assert v["category_label"] == "Kombi"
    assert v["displacement"] == 1498
    assert v["doors"] == "5" and v["seats"] == 5
    assert v["seller_name"] == "Privatverkäufer"       # seller: "Privat"
    assert v["seller_zip"] == "47059"
    assert "Duisburg" in v["seller_city"]
    # Keine Unfall-Angabe im Actor-Datensatz -> ehrlich leer, nicht erfunden
    assert v["accident_damaged"] is None
    # Beschreibung als Klartext, Fotos als fertige URLs
    assert "Mercedes-Benz B 160 zu verkaufen" in v["description"]
    assert v["image_count"] == 20
    assert all(u.startswith("https://prod.pictures.autoscout24.net")
               for u in v["images"])
    # Tracking-Parameter aus der Detail-URL entfernt
    assert v["detail_url"].startswith("https://www.autoscout24.de/angebote/")
    assert "?" not in v["detail_url"]


def test_preis_fallback_aus_text(item):
    del item["rawPrice"]
    assert parse_autoscout_item(item, UUID)["list_price"] == 2000.0


def test_haendler_statt_privat(item):
    item["seller"] = "Händler"
    item["contactName"] = ""
    assert parse_autoscout_item(item, UUID)["seller_name"] == "Händler"
    item["contactName"] = "Autohaus Muster"
    assert parse_autoscout_item(item, UUID)["seller_name"] == "Autohaus Muster"


def test_features_aus_listen_oder_text(item):
    item["comfort"] = ["Klimaanlage", "Sitzheizung"]
    item["safety"] = "ABS, ESP"
    v = parse_autoscout_item(item, UUID)
    assert "Klimaanlage" in v["features"] and "ESP" in v["features"]
    assert len(v["features"]) == 4


def test_nur_inserats_urls_an_den_actor():
    assert detail_looks_like_autoscout_listing(
        "https://www.autoscout24.de/angebote/mercedes-x-" + UUID)
    assert not detail_looks_like_autoscout_listing(
        "https://www.autoscout24.de/lst/mercedes-benz?atype=C")
    assert not detail_looks_like_autoscout_listing(
        "https://suchen.mobile.de/auto-inserat/x/123.html")
    assert not detail_looks_like_autoscout_listing("")
