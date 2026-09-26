# -*- coding: utf-8 -*-
"""Pruefbericht 20.09.2026 — Inserate auslesen (Fahrzeugdaten bis in den Vertrag).

S-01..S-06  "Unfallschaden: Nein" / "Fahrbereit: Ja" wurden aus FEHLENDEN
            Angaben erfunden und standen als Zusicherung im Kaufvertrag.
S-07/S-08   Sitzzahl und HU kamen aus Elementen, die es nicht gibt.
S-10        Der Nettopreis wurde als Listenpreis uebernommen.
S-11/S-12   Zahlen: "12.500,50 km" wurde 1.250.050 km, "€ 2.000,50" 200.050 €.
S-15        Zustandsaussagen ("Unfallfrei", "TÜV neu") wurden Ausstattung.
S-16        Platzhalter "Händler"/"Privatverkäufer" fuellten das Namens-Pflichtfeld.
S-17        Telefon: erstes ZEICHEN einer Zeichenkette, oder die Faxnummer.
S-18        Englische statt deutscher Bezeichnungen im deutschen Vertrag.
A-01        Quadratische Dublettenpruefung (Rechenzeit-Angriff).
A-02        Browser-HTML wurde auch angenommen, wenn der Server selbst abruft.
"""
import ast
import sys
import time
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import mobile_service as M  # noqa: E402


# ------------------------------------------------------------------ S-11/S-12
@pytest.mark.parametrize("text, erwartet", [
    ("111,016 km", 111016),
    ("111.016 km", 111016),
    ("1,984 ccm", 1984),
    ("12.500,50 km", 12500),
    ("€ 2.000,50", 2000),
    ("€ 12.345,-", 12345),
    ("5", 5),
    (5, 5),
    (85120.0, 85120),
    ("10.000 - 20.000 km", None),
    ("10.000 bis 20.000 km", None),
    ("", None),
    (None, None),
    (True, None),
    ("ohne Angabe", None),
])
def test_s11_zahlen_werden_richtig_gelesen(text, erwartet):
    assert M._apify_zahl(text) == erwartet


# ------------------------------------------------------------------ S-03..S-06
@pytest.mark.parametrize("fall, text, erwartet", [
    (True, "", True),
    (None, "gebrauchtfahrzeug, beschädigt", True),
    (None, "beschädigtes fahrzeug", True),
    (None, "unbeschädigtes fahrzeug", False),
    (None, "gebrauchtfahrzeug, unfallfrei", False),
    (None, "unfallwagen", True),
    (False, "", False),
    (None, "", None),            # keine Angabe bleibt keine Angabe
    (None, "gebrauchtfahrzeug", None),
])
def test_s03_unfall_nur_aus_echter_angabe(fall, text, erwartet):
    assert M.zustand_unfall(fall, text) is erwartet


@pytest.mark.parametrize("bereit, text, erwartet", [
    (True, "", True),
    (False, "", False),
    (None, "gebrauchtfahrzeug, nicht fahrtauglich", False),
    (True, "nicht fahrbereit", False),
    (None, "", None),
])
def test_s04_fahrbereit_nur_aus_echter_angabe(bereit, text, erwartet):
    assert M.zustand_fahrbereit(bereit, text) is erwartet


def test_s01_xml_ohne_angabe_bleibt_offen():
    """19 von 20 Beispielinseraten haben kein ad:accident-damaged — daraus
    wurde "kein Unfallschaden"."""
    assert M._xml_unfall({}) is None
    assert M._xml_bool(None) is None
    assert M._xml_unfall({"ad:damage-and-unrepaired": {"@value": "true"}}) is True
    # "nicht unrepariert" heisst NICHT unfallfrei
    assert M._xml_unfall({"ad:damage-and-unrepaired": {"@value": "false"}}) is None
    assert M._xml_unfall({"ad:accident-damaged": {"@value": "false"}}) is False
    assert M._xml_unfall({"ad:accident-damaged": {"@value": "true"}}) is True


def test_s07_s08_s18_beispieldaten():
    """Am echten Beispielbestand (sandbox_data.xml): Sitze und HU sind jetzt
    gefuellt, Unfall/Fahrbereit nur wo angegeben."""
    import xmltodict
    roh = xmltodict.parse((BACKEND / "sandbox_data.xml").read_text(encoding="utf-8"))

    def ads(knoten):
        if isinstance(knoten, dict):
            for k, v in knoten.items():
                if k == "ad:ad":
                    yield from (v if isinstance(v, list) else [v])
                else:
                    yield from ads(v)
        elif isinstance(knoten, list):
            for x in knoten:
                yield from ads(x)

    fahrzeuge = [M._parse_ad_xml(a) for a in ads(roh)]
    assert len(fahrzeuge) >= 20
    assert sum(1 for f in fahrzeuge if f["seats"]) >= 18, "Sitzzahl bleibt leer"
    assert sum(1 for f in fahrzeuge if f["hu"]) >= 10, "HU bleibt leer"
    assert all(f["hu"] is None or "/" in f["hu"] for f in fahrzeuge), "HU nicht MM/JJJJ"
    unbekannt = sum(1 for f in fahrzeuge if f["accident_damaged"] is None)
    assert unbekannt >= 15, "fehlende Unfallangabe wurde wieder erfunden"
    assert all(f["seller_name"] not in ("Händler", "Privatverkäufer") for f in fahrzeuge)


# ------------------------------------------------------------------ S-10
def test_s10_netto_ist_kein_listenpreis():
    item = {"price": {"nettoAmount": {"amount": 20042.0}}, "id": "1"}
    assert M._parse_apify_item(item, "1")["list_price"] is None
    item = {"price": {"grs": {"amount": 23850.0}, "nettoAmount": {"amount": 20042.0}}, "id": "1"}
    assert M._parse_apify_item(item, "1")["list_price"] == 23850.0


# ------------------------------------------------------------------ S-16/S-17
def test_s16_kein_platzhalter_als_name():
    f = M._parse_apify_item({"id": "1", "contact": {"enumType": "DEALER"}}, "1")
    assert f["seller_name"] is None
    import autoscout_service as A
    f = A.parse_autoscout_item({"seller": "Händler"}, "1")
    assert f["seller_name"] is None


@pytest.mark.parametrize("roh, erwartet", [
    ("0171 1234567", "0171 1234567"),
    ([{"number": "0211 999", "type": "FAX"}, {"number": "0211 111", "type": "PHONE"}], "0211 111"),
    ([{"number": "0211 111", "type": "PHONE"}, {"number": "0171 222", "type": "MOBILE"}], "0171 222"),
    ([{"number": "0211 999", "type": "FAX"}], ""),
    (["0211 555"], "0211 555"),
    (None, ""),
])
def test_s17_telefon(roh, erwartet):
    assert M.telefon_aus(roh) == erwartet


def test_s17_autoscout_zeichenkette_ist_eine_nummer():
    import autoscout_service as A
    f = A.parse_autoscout_item({"phones": "0171 1234567"}, "1")
    assert f["seller_phone"] == "0171 1234567"


# ------------------------------------------------------------------ S-18
def test_s18_deutsch_vor_englisch():
    knoten = {"resource:local-description": [
        {"@xml-lang": "en", "#text": "Alloy wheels"},
        {"@xml-lang": "de", "#text": "Leichtmetallfelgen"}]}
    assert M._desc(knoten) == "Leichtmetallfelgen"
    knoten = {"resource:local-description": [{"@xml-lang": "en", "#text": "Car"}]}
    assert M._desc(knoten) == "Car"


# ------------------------------------------------------------------ S-15
def test_s15_zustandsaussagen_sind_keine_ausstattung():
    from kleinanzeigen_service import _is_equipment_like
    for rauschen in ("Unfallfrei", "TÜV neu", "2. Hand", "kein Raucherauto",
                     "Verkaufe meinen Golf", "Top Zustand", "Kratzer hinten"):
        assert not _is_equipment_like(rauschen), rauschen
    for merkmal in ("Klimaautomatik", "Sitzheizung", "Navigationssystem", "Anhängerkupplung",
                    "Scheckheftgepflegt", "Nichtraucherfahrzeug"):
        assert _is_equipment_like(merkmal), merkmal


# ------------------------------------------------------------------ A-01
def test_a01_dublettenpruefung_nicht_quadratisch():
    from kleinanzeigen_service import MAX_AUSSTATTUNG, _parse_equipment
    teile = ",".join(f"Merkmal {i}" for i in range(60000))
    text = "\nAusstattung\n" + teile + "\nAnbieter\n"
    start = time.monotonic()
    erg = _parse_equipment(text)
    dauer = time.monotonic() - start
    assert len(erg) <= MAX_AUSSTATTUNG
    assert dauer < 3.0, f"{dauer:.1f} s — Dublettenpruefung ist wieder quadratisch"


# ------------------------------------------------------------------ A-02
def test_a02_ingest_nur_im_erweiterungsmodus():
    code = (BACKEND / "routes" / "listings.py").read_text(encoding="utf-8")
    baum = ast.parse(code)
    for k in ast.walk(baum):
        if isinstance(k, ast.AsyncFunctionDef) and k.name == "ingest_client_html":
            quelle = ast.unparse(k)
            assert "_erweiterung_noetig()" in quelle
            assert quelle.index("_erweiterung_noetig()") < quelle.index("looks_like_kleinanzeigen_listing")
            break
    else:
        pytest.fail("ingest_client_html nicht gefunden")


# ------------------------------------------------------------------ PDF
def test_pdf_druckt_nur_echte_angaben():
    from pdf_service import _ja_nein_oder_nichts
    assert _ja_nein_oder_nichts(True) == "Ja"
    assert _ja_nein_oder_nichts(False) == "Nein"
    assert _ja_nein_oder_nichts(None) == "—"
    assert _ja_nein_oder_nichts("unbekannt") == "—"
