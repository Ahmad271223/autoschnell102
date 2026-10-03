# -*- coding: utf-8 -*-
"""Getriebe und Kraftstoff "1:1 uebernehmen" in BEIDEN Vergleichs-Links.

Befund 17.09.2026 (Ahmad: "ab und zu klappt Getriebe 1:1 nicht"): Der
mobile.de-Link bekam tr= nur bei Kleinanzeigen-Inseraten richtig. Bei
mobile.de-Links (Apify: "Automatic" -> AUTOMATIC) und AutoScout24-Links
("Schaltgetriebe" -> SCHALTGETRIEBE) stand ein Wert im Link, den mobile.de
nicht kennt — gefiltert wurde still ohne Getriebe. Halbautomatik lief in
beiden Links als Automatik. Beim Kraftstoff derselbe Fehler (BENZIN, ELECTRIC).

Geprueft wird gegen die ECHTEN aufgezeichneten Actor-Datensaetze
(tests/fixtures) und gegen Rohwerte, wie sie noch im Zwischenspeicher liegen.
"""
import copy
import inspect
import json
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

import pytest

import autoscout_service as AS
import kleinanzeigen_service as KS
import mobile_service as M
from fahrzeug_codes import (
    autoscout_getriebe, autoscout_kraftstoff, filter_hinweise, getriebe_code,
    hat_navigation, kraftstoff_code,
)

FIXTURES = Path(__file__).parent / "fixtures"
REGELN = copy.deepcopy(M.DEFAULT_RULES)
EXPORT = copy.deepcopy(M.DEFAULT_EXPORT_RULES)


def _mobile(vehicle, regeln=REGELN):
    q = dict(parse_qsl(urlparse(M.build_search_url(vehicle, regeln)).query))
    return q.get("tr"), q.get("ft")


def _autoscout(vehicle, regeln=REGELN):
    q = dict(parse_qsl(urlparse(AS.build_search_url(vehicle, regeln)).query))
    return q.get("gear"), q.get("fuel")


def _fahrzeug(**extra):
    v = {"make": "VW", "make_label": "VW", "model": "Golf", "model_label": "Golf",
         "first_registration": "06/2019", "mileage": 100000, "power_kw": 110, "power_ps": 150}
    v.update(extra)
    return v


# ------------------------------------------------------------ Zuordnung
@pytest.mark.parametrize("wert, code", [
    ("AUTOMATIC_GEAR", "AUTOMATIC_GEAR"), ("MANUAL_GEAR", "MANUAL_GEAR"),
    ("SEMIAUTOMATIC_GEAR", "SEMIAUTOMATIC_GEAR"),
    ("Automatik", "AUTOMATIC_GEAR"), ("AUTOMATIK", "AUTOMATIC_GEAR"),
    ("Automatic", "AUTOMATIC_GEAR"), ("AUTOMATIC", "AUTOMATIC_GEAR"),
    ("Automatikgetriebe", "AUTOMATIC_GEAR"), ("Doppelkupplungsgetriebe (DSG)", "AUTOMATIC_GEAR"),
    ("Schaltgetriebe", "MANUAL_GEAR"), ("SCHALTGETRIEBE", "MANUAL_GEAR"),
    ("Manuell", "MANUAL_GEAR"), ("Manual gearbox", "MANUAL_GEAR"), ("MANUAL GEARBOX", "MANUAL_GEAR"),
    ("Halbautomatik", "SEMIAUTOMATIC_GEAR"), ("HALBAUTOMATIK", "SEMIAUTOMATIC_GEAR"),
    ("Semi-automatic", "SEMIAUTOMATIC_GEAR"), ("SEMI-AUTOMATIC", "SEMIAUTOMATIC_GEAR"),
    (" automatik ", "AUTOMATIC_GEAR"),
    ("", None), (None, None), ("unbekannt", None), ("—", None),
])
def test_getriebe_code(wert, code):
    assert getriebe_code(wert) == code


def test_getriebe_code_nimmt_den_ersten_erkennbaren_wert():
    assert getriebe_code(None, "Halbautomatik") == "SEMIAUTOMATIC_GEAR"
    assert getriebe_code("", "Schaltgetriebe") == "MANUAL_GEAR"
    assert getriebe_code("???", "Automatik") == "AUTOMATIC_GEAR"
    assert autoscout_getriebe("Halbautomatik") == "S"
    assert autoscout_getriebe("Automatik") == "A" and autoscout_getriebe("Schaltgetriebe") == "M"
    assert autoscout_getriebe("unbekannt") == ""


@pytest.mark.parametrize("wert, code", [
    ("PETROL", "PETROL"), ("Petrol", "PETROL"), ("Benzin", "PETROL"), ("BENZIN", "PETROL"),
    ("Super", "PETROL"), ("Diesel", "DIESEL"),
    ("Electric", "ELECTRICITY"), ("ELECTRIC", "ELECTRICITY"), ("Elektro", "ELECTRICITY"),
    ("ELECTRICITY", "ELECTRICITY"),
    ("Hybrid (petrol/electric)", "HYBRID"), ("Hybrid (Benzin/Elektro)", "HYBRID"),
    ("Elektro/Benzin", "HYBRID"), ("Plug-in-Hybrid", "HYBRID"), ("HYBRID", "HYBRID"),
    ("Hybrid (diesel/electric)", "HYBRID_DIESEL"), ("Elektro/Diesel", "HYBRID_DIESEL"),
    ("HYBRID_DIESEL", "HYBRID_DIESEL"),
    ("Autogas (LPG)", "LPG"), ("LPG", "LPG"), ("Benzin/LPG", "LPG"),
    ("Erdgas (CNG)", "CNG"), ("Natural Gas", "CNG"),
    ("Wasserstoff", "HYDROGENIUM"), ("Hydrogen", "HYDROGENIUM"),
    ("Ethanol (FFV, E85)", "ETHANOL"), ("Andere", "OTHER"),
    ("", None), (None, None), ("Holzvergaser", None),
])
def test_kraftstoff_code(wert, code):
    assert kraftstoff_code(wert) == code


def test_autoscout_kraftstoff_codes():
    assert autoscout_kraftstoff("Benzin") == "B" and autoscout_kraftstoff("Diesel") == "D"
    assert autoscout_kraftstoff("Elektro/Benzin") == "2" and autoscout_kraftstoff("Elektro/Diesel") == "3"
    assert autoscout_kraftstoff("Autogas (LPG)") == "L" and autoscout_kraftstoff("Erdgas (CNG)") == "C"
    assert autoscout_kraftstoff("Ethanol") == "", "kein AutoScout-Code -> kein Filter (Hinweis)"
    # Alte Einzelwert-Aufrufe (manuelle Suche) bleiben gueltig
    assert AS._autoscout_fuel("Plug-in-Hybrid") == "2" and AS._autoscout_fuel("Diesel") == "D"


# ------------------------------------------------------------ mobile.de-Inserate (Apify)
@pytest.mark.parametrize("roh, tr, gear", [
    ("Automatic", "AUTOMATIC_GEAR", "A"),
    ("Manual gearbox", "MANUAL_GEAR", "M"),
    ("Semi-automatic", "SEMIAUTOMATIC_GEAR", "S"),
])
def test_mobile_inserat_getriebe_in_beiden_links(roh, tr, gear):
    item = json.loads((FIXTURES / "apify_mobile_item.json").read_text(encoding="utf-8"))
    item = item[0] if isinstance(item, list) else item
    for a in item["attributes"]:
        if a.get("tag") == "transmission":
            a["value"] = roh
    v = M._parse_apify_item(item, "1")
    assert v["gearbox"] == tr, "der Parser speichert den mobile.de-Code"
    assert _mobile(v) == (tr, "PETROL")
    assert _autoscout(v) == (gear, "B")
    assert filter_hinweise(v, REGELN) == []


# ------------------------------------------------------------ AutoScout24-Inserate (Apify)
def test_autoscout_inserat_getriebe_und_kraftstoff_in_beiden_links():
    item = json.loads((FIXTURES / "apify_autoscout_item.json").read_text(encoding="utf-8"))[0]
    assert item["gearbox"] == "Schaltgetriebe" and item["fuelType"] == "Benzin"   # echter Datensatz
    v = AS.parse_autoscout_item(item, "719b9573-d82f-4d29-85e0-54b5708d9aa6")
    assert (v["gearbox"], v["fuel"]) == ("MANUAL_GEAR", "PETROL")
    assert (v["gearbox_label"], v["fuel_label"]) == ("Schaltgetriebe", "Benzin")
    assert _mobile(v) == ("MANUAL_GEAR", "PETROL")
    assert _autoscout(v) == ("M", "B")
    for roh, tr, gear in (("Automatik", "AUTOMATIC_GEAR", "A"), ("Halbautomatik", "SEMIAUTOMATIC_GEAR", "S")):
        v2 = AS.parse_autoscout_item(dict(item, gearbox=roh, fuelType="Elektro/Benzin"), "x")
        assert _mobile(v2) == (tr, "HYBRID") and _autoscout(v2) == (gear, "2")


# ------------------------------------------------------------ Kleinanzeigen
@pytest.mark.parametrize("roh, tr, gear", [
    ("Automatik", "AUTOMATIC_GEAR", "A"), ("Manuell", "MANUAL_GEAR", "M"),
    ("Halbautomatik", "SEMIAUTOMATIC_GEAR", "S"),
])
def test_kleinanzeigen_getriebe(roh, tr, gear):
    code, text = KS._parse_gearbox(roh)
    assert code == tr and text
    v = _fahrzeug(gearbox=code, gearbox_label=text, fuel="DIESEL", fuel_label="Diesel")
    assert _mobile(v) == (tr, "DIESEL") and _autoscout(v) == (gear, "D")
    assert KS._parse_fuel("Hybrid (Diesel/Elektro)")[0] == "HYBRID_DIESEL"


# ------------------------------------------------------------ Altdaten im Zwischenspeicher
@pytest.mark.parametrize("gearbox, label, fuel, fuel_label, erwartet", [
    ("AUTOMATIC", "Automatik", "PETROL", "Benzin", ("AUTOMATIC_GEAR", "PETROL", "A", "B")),
    ("MANUAL GEARBOX", "Schaltgetriebe", "ELECTRIC", "Elektro", ("MANUAL_GEAR", "ELECTRICITY", "M", "E")),
    ("SCHALTGETRIEBE", "Schaltgetriebe", "BENZIN", "Benzin", ("MANUAL_GEAR", "PETROL", "M", "B")),
    ("HALBAUTOMATIK", "Halbautomatik", "ELEKTRO/BENZIN", "Elektro/Benzin", ("SEMIAUTOMATIC_GEAR", "HYBRID", "S", "2")),
    ("SEMI-AUTOMATIC", "Halbautomatik", "HYBRID (PETROL/ELECTRIC)", "Hybrid (Benzin/Elektro)",
     ("SEMIAUTOMATIC_GEAR", "HYBRID", "S", "2")),
    ("", "Automatik", "", "Diesel", ("AUTOMATIC_GEAR", "DIESEL", "A", "D")),
])
def test_altdaten_mit_rohwerten_filtern_trotzdem(gearbox, label, fuel, fuel_label, erwartet):
    v = _fahrzeug(gearbox=gearbox, gearbox_label=label, fuel=fuel, fuel_label=fuel_label)
    tr, ft, gear, as_fuel = erwartet
    for regeln in (REGELN, EXPORT):
        assert _mobile(v, regeln) == (tr, ft), regeln
        assert _autoscout(v, regeln) == (gear, as_fuel), regeln


def test_regel_ignorieren_setzt_keinen_filter():
    v = _fahrzeug(gearbox="AUTOMATIC_GEAR", gearbox_label="Automatik", fuel="DIESEL", fuel_label="Diesel")
    ohne = {**REGELN, "gearbox": {"mode": "ignore"}, "fuel": {"mode": "ignore"}}
    assert _mobile(v, ohne) == (None, None)
    assert _autoscout(v, ohne) == (None, None)
    assert filter_hinweise(_fahrzeug(), ohne) == []


# ------------------------------------------------------------ Hinweise statt stiller Links
def test_hinweis_wenn_getriebe_fehlt_oder_unbekannt():
    ohne = _fahrzeug(fuel="DIESEL", fuel_label="Diesel")
    h = AS.regeln_nicht_abgebildet(ohne, REGELN)
    assert any("kein Getriebe angegeben" in t for t in h), h
    assert _mobile(ohne) == (None, "DIESEL")
    komisch = _fahrzeug(gearbox="XYZ", gearbox_label="Sonderbauform", fuel="DIESEL", fuel_label="Diesel")
    h2 = AS.regeln_nicht_abgebildet(komisch, REGELN)
    assert any("Getriebe „Sonderbauform“ nicht erkannt" in t for t in h2), h2
    gut = _fahrzeug(gearbox="AUTOMATIC_GEAR", gearbox_label="Automatik", fuel="DIESEL", fuel_label="Diesel")
    assert AS.regeln_nicht_abgebildet(gut, REGELN) == []


def test_hinweis_kraftstoff_ohne_autoscout_entsprechung():
    v = _fahrzeug(gearbox="MANUAL_GEAR", gearbox_label="Schaltgetriebe", fuel="ETHANOL", fuel_label="Ethanol")
    assert _mobile(v) == ("MANUAL_GEAR", "ETHANOL")
    assert _autoscout(v)[1] is None
    assert any("filtert nur mobile.de" in t for t in AS.regeln_nicht_abgebildet(v, REGELN))


def test_quelltext_kein_rohwert_mehr_im_link():
    q = inspect.getsource(M.build_search_url)
    assert 'params.append(("tr", vehicle["gearbox"]))' not in q
    assert 'params.append(("ft", vehicle["fuel"]))' not in q
    assert "getriebe_code(" in q and "kraftstoff_code(" in q
    import routes.drivers as D
    assert '"fuel": v.get("fuel_label") or v.get("fuel")' in inspect.getsource(D)


# ------------------------------------------------ Navi (Wunsch Ahmad 18.09.2026)
@pytest.mark.parametrize("fahrzeug, erwartet, fall", [
    ({"features": ["Klimaanlage", "Navigationssystem", "Bluetooth"]}, True, "Ausstattungsliste"),
    ({"features": ["Navi"]}, True, "Kurzform in der Liste"),
    ({"equipment": "Navigationsgerät, Sitzheizung"}, True, "equipment als Text"),
    ({"features": ["Klimaanlage", "Tempomat"]}, False, "kein Navi"),
    ({"description": "Sehr gepflegt, Navi, Sitzheizung"}, True, "Navi im Fliesstext"),
    ({"title": "VW Golf Navi Business"}, True, "im Titel"),
    ({"description": "Fahrzeug ohne Navi, dafür Sitzheizung"}, False, "ohne Navi"),
    ({"description": "Kein Navigationssystem verbaut"}, False, "kein Navigationssystem"),
    ({"description": "Navi-Vorbereitung ab Werk"}, False, "nur vorbereitet"),
    ({"description": "Navigationsvorbereitung vorhanden"}, False, "Vorbereitung ausgeschrieben"),
    ({"description": "Klima, Navi. Ohne Anhängerkupplung."}, True, "anderes 'ohne' im Text"),
    ({}, False, "nichts angegeben"),
    (None, False, "kein Fahrzeug"),
])
def test_navi_erkennung(fahrzeug, erwartet, fall):
    assert hat_navigation(fahrzeug) is erwartet, fall


def _fe(vehicle, regeln=REGELN):
    q = dict(parse_qsl(urlparse(M.build_search_url(vehicle, regeln)).query))
    return q.get("fe")


def test_navi_landet_im_mobile_link():
    """Wunsch Ahmad 18.09.2026: Steht im Inserat ein Navi, filtert der
    Vergleich auch danach — mit dem Parameter aus seinem geprueften Link."""
    mit = _fahrzeug(gearbox="AUTOMATIC_GEAR", fuel="PETROL",
                    features=["Klimaanlage", "Navigationssystem"])
    assert _fe(mit) == "NAVIGATION_SYSTEM"
    # Reihenfolge wie im echten Link: ... ft= tr= fe= dam= ...
    link = M.build_search_url(mit, REGELN)
    assert link.index("tr=AUTOMATIC_GEAR") < link.index("fe=NAVIGATION_SYSTEM") < link.index("dam=")
    ohne = _fahrzeug(gearbox="AUTOMATIC_GEAR", fuel="PETROL", features=["Klimaanlage"])
    assert _fe(ohne) is None
    # "ohne Navi" im Text darf den Filter NICHT setzen.
    verneint = _fahrzeug(gearbox="AUTOMATIC_GEAR", fuel="PETROL",
                         description="Guter Zustand, leider ohne Navi")
    assert _fe(verneint) is None
