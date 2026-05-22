"""Offline integration test for the Kleinanzeigen service.

Mocks the HTTP fetch with a representative kleinanzeigen.de detail page
HTML and verifies that all key fields are extracted plus the correct
mobile.de search URL is built (using the JSON catalogue).
"""
import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import kleinanzeigen_service as ka  # noqa: E402
from mobile_service import build_search_url, DEFAULT_RULES  # noqa: E402


SAMPLE_HTML = """
<html><head>
<title>VW Golf VII 2.0 TDI · Kleinanzeigen</title>
<meta property="og:title" content="VW Golf VII 2.0 TDI Highline">
<meta property="og:description" content="Top gepflegter Golf, Scheckheft.">
</head>
<body>
<h1>VW Golf VII 2.0 TDI Highline</h1>
<div>Preis: 18.500 €</div>
<div>10115 Berlin</div>
<div>Bayern</div>
<div>Eigenschaften:</div>
<div>Marke</div><div>Volkswagen</div>
<div>Modell</div><div>Golf</div>
<div>Kilometerstand</div><div>123.456 km</div>
<div>Erstzulassung</div><div>06/2018</div>
<div>Kraftstoffart</div><div>Diesel</div>
<div>Leistung</div><div>110 kW (150 PS)</div>
<div>Getriebe</div><div>Automatik</div>
<div>Anzahl Türen</div><div>4/5</div>
<div>HU bis</div><div>07/2025</div>
<div>Außenfarbe</div><div>Schwarz</div>
<div>Anzahl der Fahrzeughalter</div><div>2</div>
<div>Beschreibung</div>
<div>Top gepflegter Golf, Scheckheft, Klimaautomatik, Tempomat.</div>
<div>Ausstattung</div>
<div>Klimaautomatik, Tempomat, Bluetooth, Navigationssystem</div>
<img src="https://img.kleinanzeigen.de/api/v1/prod-ads/images/abc/123-1024x768.jpg">
<img src="https://img.kleinanzeigen.de/api/v1/prod-ads/images/abc/124-1024x768.jpg">
<div>Ähnliche Anzeigen</div>
<img src="https://img.kleinanzeigen.de/api/v1/prod-ads/images/zzz/should-be-cut.jpg">
</body></html>
"""


async def _patched(url):  # noqa: D401
    return SAMPLE_HTML


def test_url_detection():
    assert ka.is_kleinanzeigen_url("https://www.kleinanzeigen.de/s-anzeige/abc-1234567890-345-2289.html")
    assert ka.is_kleinanzeigen_url("https://www.ebay-kleinanzeigen.de/x")
    assert not ka.is_kleinanzeigen_url("https://suchen.mobile.de/auto-inserat/411111165.html")


def test_full_kleinanzeigen_extract(monkeypatch):
    monkeypatch.setattr(ka, "_fetch_html", _patched)

    url = "https://www.kleinanzeigen.de/s-anzeige/golf-1234567890-345-2289.html"
    v = asyncio.run(ka.fetch_kleinanzeigen_vehicle(url))

    assert v["make_label"] == "Volkswagen"
    assert v["model_label"] == "Golf"
    assert v["mileage"] == 123456
    assert v["first_registration"] == "06/2018"
    assert v["power_kw"] == 110
    assert v["power_ps"] == 150
    assert v["fuel"] == "DIESEL"
    assert v["gearbox"] == "AUTOMATIC_GEAR"
    assert v["hu"] == "07/2025"
    assert v["color"] == "Schwarz"
    assert v["previous_owners"] == 2
    assert v["list_price"] == 18500.0
    assert v["title"].startswith("VW Golf VII")
    assert v["_resolved_make_id"] == "25200"  # Volkswagen
    assert v["_resolved_model_id"] == "14"    # Golf
    assert v["_source"] == "kleinanzeigen"

    # Description extracted, Ausstattung captured
    assert "Klimaautomatik" in (v["description"] or "")
    assert "Tempomat" in v["features"]

    # Images: first two kept, the 3rd one (after stop marker) cut
    assert len(v["images"]) == 2
    assert all("?rule=$_59.AUTO" in img for img in v["images"])


def test_build_search_url_from_kleinanzeigen(monkeypatch):
    monkeypatch.setattr(ka, "_fetch_html", _patched)
    v = asyncio.run(ka.fetch_kleinanzeigen_vehicle(
        "https://www.kleinanzeigen.de/s-anzeige/x-1234567890-345-2289.html"
    ))
    url = build_search_url(v, DEFAULT_RULES)
    assert "ms=25200" in url.replace("%3B", ";"), f"missing make ms in {url}"
    assert "14;;" in url.replace("%3B", ";")  # Golf model id
    assert "ft=DIESEL" in url
    assert "tr=AUTOMATIC_GEAR" in url
    assert "fr=" in url   # registration year filter applied
    assert "ml=" in url   # mileage filter applied
    assert "pw=" in url   # power filter applied
    assert "cn=DE" in url  # default country
