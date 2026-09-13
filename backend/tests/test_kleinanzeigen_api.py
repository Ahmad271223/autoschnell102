# -*- coding: utf-8 -*-
"""Kleinanzeigen ueber die API von kleinanzeigen-agent.de (Wunsch Ahmad,
12.09.2026).

Der eigene Abruf bleibt die Notloesung. Geprueft wird deshalb vor allem:
  * die Umsetzung der API-Antwort in die interne Fahrzeugform (identische
    Felder wie der eigene Abruf — sonst brechen PDF, Vertrag und Cache),
  * dass JEDES API-Problem still auf den eigenen Abruf zurueckfaellt,
  * dass eine nachweislich beendete Anzeige als solche gemeldet wird.

Kein Netz: die Antworten sind aufgezeichnet (echte Struktur vom 12.09.2026).
"""
import asyncio
import importlib
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

WURZEL = Path(__file__).resolve().parents[1]


def _modul(name):
    return importlib.import_module(name)


# Aufgezeichnete Antwort (gekuerzt, Struktur 1:1 wie am 12.09.2026).
ANTWORT = {
    "success": True,
    "message": "Kleinanzeigen ad fetched",
    "data": {
        "source": "live",
        "ad": {
            "ad_id": "3458821471",
            "title": "Volkswagen Caddy Maxi Trendline BMT /Klima/ 4Motion",
            "description": "Fahrzeugnummer: 265 1. Hand / Klima / Allrad",
            "price": {"amount": 13900, "currency_code": "EUR",
                      "price_type": "SPECIFIED_AMOUNT", "negotiable": False},
            "images": ["https://img.kleinanzeigen.de/a.jpg",
                       "https://img.kleinanzeigen.de/b.jpg",
                       "https://img.kleinanzeigen.de/a.jpg"],   # Dublette
            "ad_url": "https://www.kleinanzeigen.de/s-anzeige/caddy/3458821471-216-3166",
            "status": "ACTIVE",
            "deleted": False,
            "seller": {"seller_id": "35992798", "name": "Davidoff GmbH",
                       "type": "COMMERCIAL"},
            "location": {"id": "3166", "name": "30179 Nord", "city": "Nord",
                         "state": "Niedersachsen", "zip": "30179"},
            "category": {"id": "216", "name": "Autos"},
            "details": {
                "Marke": "Volkswagen",
                "Modell": "Caddy",
                "Kilometerstand": "222.245",
                "Fahrzeugzustand": "Unbeschädigtes Fahrzeug",
                "Erstzulassung": "März 2019",
                "Kraftstoffart": "Diesel",
                "Leistung": "122",
                "Getriebe": "Manuell",
                "Fahrzeugtyp": "Kombi",
                "Anzahl Türen": "4/5",
                "Außenfarbe": "Grau",
                "Anhängerkupplung": "true",
                "Klimaanlage": "true",
                "Sitzheizung": "true",
            },
        },
    },
}


def _ad(**aenderungen):
    import copy
    ad = copy.deepcopy(ANTWORT["data"]["ad"])
    ad.update(aenderungen)
    return ad


URL = "https://www.kleinanzeigen.de/s-anzeige/caddy/3458821471-216-3166"


# ------------------------------------------------------------- Umsetzung
def test_01_api_antwort_wird_zur_internen_fahrzeugform():
    A = _modul("kleinanzeigen_api")
    v = A.fahrzeug_aus_api(_ad(), URL, "3458821471")
    assert v["mobile_ad_id"] == "3458821471" and v["kleinanzeigen_id"] == "3458821471"
    assert v["make"] == "Volkswagen" and v["model"] == "Caddy"
    assert v["mileage"] == 222245, "Punkte im Kilometerstand muessen weg"
    assert v["first_registration"] == "03/2019"
    assert v["fuel_label"] == "Diesel"
    assert v["list_price"] == 13900.0 and v["currency"] == "EUR"
    assert v["color"] == "Grau" and v["doors"] == "4/5"
    assert v["accident_damaged"] is False
    assert v["_source"] == "kleinanzeigen" and v["_abrufweg"] == "api"


def test_02_leistung_ohne_einheit_wird_als_ps_gelesen():
    """Die API liefert die blanke Zahl ("122"), die Webseite "122 PS"."""
    A = _modul("kleinanzeigen_api")
    v = A.fahrzeug_aus_api(_ad(), URL, "3458821471")
    assert v["power_ps"] == 122, v["power_ps"]
    assert v["power_kw"] == 90, v["power_kw"]
    # Steht doch eine Einheit dabei, wird sie respektiert.
    ad = _ad()
    ad["details"]["Leistung"] = "150 kW"
    v2 = A.fahrzeug_aus_api(ad, URL, "1")
    assert v2["power_kw"] == 150 and v2["power_ps"] and v2["power_ps"] > 150


def test_03_ausstattung_kommt_aus_den_ja_werten():
    A = _modul("kleinanzeigen_api")
    v = A.fahrzeug_aus_api(_ad(), URL, "3458821471")
    assert set(v["features"]) == {"Anhängerkupplung", "Klimaanlage", "Sitzheizung"}
    assert "Marke" not in v["features"], "reine Angaben sind keine Ausstattung"


def test_04_bilder_ohne_dubletten():
    A = _modul("kleinanzeigen_api")
    v = A.fahrzeug_aus_api(_ad(), URL, "3458821471")
    assert v["images"] == ["https://img.kleinanzeigen.de/a.jpg",
                           "https://img.kleinanzeigen.de/b.jpg"]
    assert v["image_count"] == 2


def test_05_verkaeufername_kommt_mit():
    """Der eigene Abruf liefert den Namen gar nicht — ueber die API schon."""
    A = _modul("kleinanzeigen_api")
    v = A.fahrzeug_aus_api(_ad(), URL, "3458821471")
    assert v["seller_name"] == "Davidoff GmbH"


def test_06_stadtstaat_bekommt_die_stadt_statt_des_stadtteils():
    """Kleinanzeigen nennt in Grossstaedten den Stadtteil. Fuer Berlin und
    Hamburg steht die Stadt im Bundesland — der Kaufvertrag braucht sie."""
    A = _modul("kleinanzeigen_api")
    ad = _ad(location={"name": "10365 Lichtenberg", "city": "Lichtenberg",
                       "state": "Berlin", "zip": "10365"})
    v = A.fahrzeug_aus_api(ad, URL, "1")
    assert v["seller_city"] == "Berlin", v["seller_city"]
    assert v["seller_zip"] == "10365"
    assert v["location"] == "10365 Berlin - Lichtenberg", v["location"]
    # Ausserhalb der Stadtstaaten bleibt es bei der Angabe der API.
    v2 = A.fahrzeug_aus_api(_ad(), URL, "1")
    assert v2["seller_city"] == "Nord" and v2["location"] == "30179 Nord"


def test_07_gleiche_felder_wie_der_eigene_abruf():
    """Beide Wege muessen dieselbe Form liefern — sonst brechen PDF,
    Kaufvertrag und Zwischenspeicher, je nachdem welcher Weg lief."""
    A = _modul("kleinanzeigen_api")
    v = A.fahrzeug_aus_api(_ad(), URL, "3458821471")
    quelle = (WURZEL / "kleinanzeigen_service.py").read_text(encoding="utf-8")
    start = quelle.index("    result = {")
    block = quelle[start:quelle.index("\n    }", start)]
    erwartet = set(re.findall(r'^\s{8}"([^"]+)":', block, re.M))
    assert erwartet, "Feldliste des eigenen Abrufs nicht gefunden"
    fehlt = erwartet - set(v)
    assert not fehlt, f"Diese Felder fehlen im API-Weg: {sorted(fehlt)}"


# ----------------------------------------------------------- Rueckfall
class _Antwort:
    def __init__(self, code, daten=None):
        self.status_code = code
        self._daten = daten if daten is not None else {}

    def json(self):
        return self._daten


class _Klient:
    def __init__(self, antwort):
        self._antwort = antwort

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, *a, **k):
        if isinstance(self._antwort, Exception):
            raise self._antwort
        return self._antwort


def _mit_antwort(monkeypatch, antwort):
    A = _modul("kleinanzeigen_api")
    monkeypatch.setattr(A, "API_KEY", "klaz_test")
    monkeypatch.setattr(A.httpx, "AsyncClient", lambda **k: _Klient(antwort))
    return A


@pytest.mark.parametrize("antwort,grund", [
    (_Antwort(401, {"error_code": "API_KEY_INVALID"}), "Schluessel abgelehnt"),
    (_Antwort(429, {}), "Limit"),
    (_Antwort(503, {}), "Stoerung"),
    (_Antwort(404, {"success": False, "error_code": "UPSTREAM_NOT_FOUND"}), "kein Treffer"),
    (_Antwort(200, {"success": True, "data": {"ad": {}}}), "ohne Anzeigendaten"),
    (RuntimeError("Netz weg"), "nicht erreichbar"),
])
def test_08_jedes_api_problem_faellt_auf_den_eigenen_abruf_zurueck(monkeypatch, antwort, grund):
    A = _mit_antwort(monkeypatch, antwort)
    with pytest.raises(A.ApiNichtNutzbar) as e:
        asyncio.run(A.hole_inserat("123", URL))
    assert grund.lower() in str(e.value).lower(), str(e.value)


def test_09_beendete_anzeige_wird_als_beendet_gemeldet(monkeypatch):
    """Die API sagt ausdruecklich DELETED — das ist verlaesslicher als die
    Texterkennung des eigenen Abrufs und darf NICHT im Rueckfall landen."""
    from kleinanzeigen_service import ListingGone
    A = _mit_antwort(monkeypatch, _Antwort(200, {
        "success": True,
        "data": {"ad": _ad(status="DELETED", deleted=True)}}))
    with pytest.raises(ListingGone):
        asyncio.run(A.hole_inserat("3458821471", URL))


def test_10_erfolgreicher_abruf_liefert_das_fahrzeug(monkeypatch):
    A = _mit_antwort(monkeypatch, _Antwort(200, ANTWORT))
    v = asyncio.run(A.hole_inserat("3458821471", URL))
    assert v["make"] == "Volkswagen" and v["_abrufweg"] == "api"


def test_11_ohne_schluessel_kein_api_weg(monkeypatch):
    A = _modul("kleinanzeigen_api")
    monkeypatch.setattr(A, "API_KEY", "")
    assert A.api_verfuegbar() is False
    with pytest.raises(A.ApiNichtNutzbar):
        asyncio.run(A.hole_inserat("123", URL))


def test_12_abrufweg_ist_verdrahtet_und_begrenzt_getrennt():
    """Quellpruefung: die API wird zuerst gefragt, der eigene Abruf bleibt
    die Notloesung, und beide Wege haben getrennte Obergrenzen."""
    pf = (WURZEL / "provider_fetch.py").read_text(encoding="utf-8")
    block = pf[pf.index('async def _abrufen('):]
    block = block[:block.index('if source == "mobile":')]
    assert block.index("hole_inserat") < block.index("_mit_scrape_bremse"), \
        "die API muss VOR dem eigenen Abruf drankommen"
    assert "ApiNichtNutzbar" in block and "api_verfuegbar()" in block
    pl = (WURZEL / "provider_limiter.py").read_text(encoding="utf-8")
    assert '"kleinanzeigen_api"' in pl, "eigene Obergrenze fuer den API-Weg fehlt"
    li = (WURZEL / "listing_identity.py").read_text(encoding="utf-8")
    assert 'begrenzung = "kleinanzeigen_api"' in li
    assert "acquire_slot(db, begrenzung)" in li


def test_17_notloesung_laeuft_unter_der_strengen_bremse():
    """Gegenpruefung 12.09.2026 (schwerer Befund): Der Slot wird oben nach
    "API-Schluessel vorhanden?" gewaehlt (8 gleichzeitig). Faellt die API
    aus, lief das Abgreifen der Webseite unter genau dieser lockeren Grenze
    — also vierfach, ausgerechnet wenn NUR noch gescrapt wird."""
    pf = (WURZEL / "provider_fetch.py").read_text(encoding="utf-8")
    bremse = pf[pf.index("async def _mit_scrape_bremse("):pf.index("async def _abrufen(")]
    assert 'acquire_slot(db, "kleinanzeigen")' in bremse, \
        "der eigene Abruf braucht einen Slot aus dem STRENGEN Topf"
    assert "release_slot" in bremse, "Slot muss wieder freigegeben werden"
    assert "finally:" in bremse, "auch bei einem Fehler freigeben"
    # Ohne API-Schluessel liegt der aeussere Slot schon im strengen Topf —
    # ein zweites Belegen waere eine Selbstblockade.
    assert "if not ueber_api" in bremse
    assert "ListingBusy" in bremse, "voll = freundliche Meldung statt Stau"


# ------------------------------------------------- Ausstattung vollstaendig
# Gemessen an echten Anzeigen (12.09.2026): Kleinanzeigens Haken decken nur
# rund 30 Standardmerkmale ab. Haendler fuegen die komplette Werksausstattung
# als Komma-Liste in den Anzeigentext ein — der eigene Abruf liest sie von der
# Webseite, also muss der API-Weg sie aus dem Text holen. Sonst stuenden im
# Kaufvertrag 15 Merkmale statt 55.
def test_13_werksausstattung_aus_dem_anzeigentext():
    A = _modul("kleinanzeigen_api")
    ad = _ad(description=(
        "Schoenes Auto aus erster Hand, scheckheftgepflegt.\n"
        "Sonderausstattung: Dachreling, Nebelscheinwerfer, Regensensor, "
        "Lichtsensor, Lederlenkrad, Multifunktionslenkrad, Isofix, "
        "Reifendruckkontrolle, Spurhalteassistent\n"
        "Bei Fragen melden Sie sich gerne."))
    v = A.fahrzeug_aus_api(ad, URL, "1")
    for erwartet in ("Dachreling", "Nebelscheinwerfer", "Regensensor",
                     "Lederlenkrad", "Isofix", "Spurhalteassistent"):
        assert erwartet in v["features"], (erwartet, v["features"])
    # Die angehakten Merkmale bleiben selbstverstaendlich erhalten.
    assert "Klimaanlage" in v["features"]


def test_14_kommas_in_klammern_trennen_nicht():
    """"Audiosystem Composition Colour (Touchscreen, MP3, Radio/CD-Player)"
    ist EIN Merkmal — naives Trennen machte daraus drei Bruchstuecke."""
    A = _modul("kleinanzeigen_api")
    ad = _ad(description=(
        "Sonderausstattung: Audiosystem Composition Colour (Touchscreen, MP3, "
        "Radio/CD-Player), Lautsprecher (6), Dachreling, Nebelscheinwerfer, "
        "Regensensor, Isofix"))
    v = A.fahrzeug_aus_api(ad, URL, "1")
    assert "Audiosystem Composition Colour (Touchscreen, MP3, Radio/CD-Player)" in v["features"], \
        v["features"]
    assert "MP3" not in v["features"], "Bruchstueck aus der Klammer"
    assert "Lautsprecher (6)" in v["features"]


def test_15_normale_saetze_werden_nicht_zu_ausstattung():
    """Ein Fliesstext mit Kommas darf keine Merkmale erzeugen."""
    A = _modul("kleinanzeigen_api")
    ad = _ad(description=(
        "Das Fahrzeug wurde stets gepflegt, war nie verunfallt, steht in der "
        "Garage, hat einen neuen TUEV, und der Preis ist Verhandlungsbasis."))
    v = A.fahrzeug_aus_api(ad, URL, "1")
    # Nur die angehakten Merkmale der Anzeige.
    assert set(v["features"]) == {"Anhängerkupplung", "Klimaanlage", "Sitzheizung"}, \
        v["features"]


def test_16_ausstattung_ist_gedeckelt():
    A = _modul("kleinanzeigen_api")
    lang = ", ".join(f"Merkmal {i}" for i in range(400))
    v = A.fahrzeug_aus_api(_ad(description="Sonderausstattung: " + lang), URL, "1")
    assert len(v["features"]) <= A.MAX_MERKMALE, len(v["features"])
    assert A.MAX_MERKMALE >= 125, "echte Haendleranzeigen haben bis zu ~125 Merkmale"
