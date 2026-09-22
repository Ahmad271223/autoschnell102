# -*- coding: utf-8 -*-
"""Rollenprüfung 22.09.2026 (Review, Welle 4) — Team Sucher/Vergleich.

Review-Befund zu RP-439: Der Marken-Rueckfall der Kleinanzeigen-API las jedes
Wort aus Titel UND Beschreibung gegen den ganzen mobile.de-Markenkatalog —
"Man kann ihn besichtigen" wurde MAN, "andere Extras" wurde Andere, und das
sogar ueber "Weitere Automarken" aus der Tabelle hinweg. Der HTML-Weg (auch
fuer die Browser-Erweiterung) suchte Teilzeichenketten im Seitentext
("Kein Anruf" -> Ruf).

Jetzt: nur der Titel, wortweise, mehrdeutige Marken nur mit Modell dahinter
bzw. als Kuerzel, "Andere" nie, eine konkrete Tabellenmarke bleibt, und der
Vergleich sagt, wenn die Marke aus dem Titel stammt. Reine Einheitentests,
keine Datenbank.
"""
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import kleinanzeigen_api as KAPI  # noqa: E402
import kleinanzeigen_service as KS  # noqa: E402
import routes.listings as L  # noqa: E402

_KA_URL = "https://www.kleinanzeigen.de/s-anzeige/sprinter/3012345678-276-1234"


def _ad(**aenderung):
    ad = {"ad_id": "3012345678", "title": "Sprinter 316 CDI Kasten",
          "description": "Verkaufe hier meinen Sprinter. Man kann ihn jederzeit "
                         "besichtigen. Klima, AHK und andere Extras. Smart Key, Ruf mich an.",
          "status": "ACTIVE", "price": {"amount": 13900, "currency_code": "EUR"},
          "seller": {"name": "vnightx", "type": "PRIVATE"},
          "location": {"zip": "30179", "city": "Hannover", "state": "Niedersachsen"},
          "details": {"Kilometerstand": "222.245", "Art": "Transporter"}}
    ad.update(aenderung)
    return ad


def _marke(titel):
    e = KS._marke_aus_titel(titel)
    return e["raw_name"] if e else None


# ------------------------------------------------------------ Hilfsfunktion
@pytest.mark.parametrize("titel", [
    "Man kann nicht meckern - Transporter",
    "Smart Key Kastenwagen 2.0",
    "Ruf an - Kastenwagen",
    "Mini Bagger mit Anhänger",
    "Transporter mit AC und Navi",
    "Kastenwagen A/C Klima",
    "Andere Transporter Kasten",
    "Minimal genutzter Kastenwagen",
    "Kein Anruf, nur Mail",
    "SPRINTER KASTEN MAN KANN BESICHTIGEN",
    "", None,
])
def test_gewoehnliche_woerter_sind_keine_marke(titel):
    assert _marke(titel) is None, titel


@pytest.mark.parametrize("titel,erwartet", [
    ("Mercedes-Benz Sprinter 316 CDI Kasten", "Mercedes-Benz"),
    ("Mercedes Sprinter 316", "Mercedes-Benz"),
    ("VW Crafter 35 Kasten", "Volkswagen"),
    ("Land Rover Defender 110", "Land Rover"),
    ("Range Rover Sport", "Land Rover"),
    ("Citroën Jumper L2H2", "Citroën"),
    ("Man kann nicht meckern: Ford Transit", "Ford"),   # erste ECHTE Marke
    ("MAN TGE 3.180 Kasten", "MAN"),                  # Kuerzel + Modell
    ("MAN TGX 18.510 Sattelzugmaschine", "MAN"),      # Kuerzel, Lkw-Modell unbekannt
    ("Verkaufe MAN Lkw Pritsche", "MAN"),             # Kuerzel im normalen Titel
    ("man tge kasten", "MAN"),                        # kleingeschrieben nur mit Modell
    ("smart fortwo coupé", "Smart"),
    ("Mini Cooper S Cabrio", "MINI"),
    ("MG ZS EV Luxury", "MG"),
    ("ORA Funky Cat", "ORA"),
    ("BYD Atto 3 Design", "BYD"),
])
def test_echte_marken_im_titel(titel, erwartet):
    assert _marke(titel) == erwartet, titel


def test_marke_fehlt_nur_bei_leer_oder_sammelposten():
    assert KS._marke_fehlt(None) and KS._marke_fehlt("") and KS._marke_fehlt("  ")
    assert KS._marke_fehlt("Weitere Automarken") and KS._marke_fehlt("Andere")
    assert not KS._marke_fehlt("Dongfeng") and not KS._marke_fehlt("Mercedes-Benz")


# ------------------------------------------------------------ API-Weg
def test_api_beschreibung_macht_keine_marke():
    """Der Befund: Titel ohne Marke, Beschreibung mit "Man"/"andere"/"Smart"."""
    v = KAPI.fahrzeug_aus_api(_ad(), _KA_URL, "3012345678")
    assert not v["make_label"] and not v["make"], v["make_label"]
    assert v["_resolved_make_id"] is None and v["_marke_aus_titel"] is False


def test_api_weitere_automarken_wird_nicht_ueberschrieben_durch_worte():
    v = KAPI.fahrzeug_aus_api(_ad(details={"Marke": "Weitere Automarken"}), _KA_URL, "1")
    assert v["make_label"] == "Weitere Automarken", v["make_label"]
    assert v["_resolved_make_id"] is None and v["_marke_aus_titel"] is False


def test_api_weitere_automarken_mit_marke_im_titel():
    v = KAPI.fahrzeug_aus_api(_ad(title="BYD Atto 3 Design",
                                  details={"Marke": "Weitere Automarken"}), _KA_URL, "1")
    assert v["make_label"] == "BYD" and v["_resolved_make_id"]
    assert v["_marke_aus_titel"] is True


def test_api_konkrete_unbekannte_marke_bleibt():
    v = KAPI.fahrzeug_aus_api(_ad(title="Mini Cooper Optik", details={"Marke": "Dongfeng"}),
                              _KA_URL, "1")
    assert v["make_label"] == "Dongfeng" and v["_marke_aus_titel"] is False


def test_api_nutzfahrzeug_marke_aus_titel_wird_markiert():
    v = KAPI.fahrzeug_aus_api(_ad(title="Mercedes-Benz Sprinter 316 CDI Kasten"), _KA_URL, "1")
    assert v["make_label"] == "Mercedes-Benz" and v["_marke_aus_titel"] is True
    # Marke aus der Tabelle -> kein Hinweis-Merker
    v = KAPI.fahrzeug_aus_api(_ad(details={"Marke": "Mercedes-Benz", "Modell": "Sprinter"}),
                              _KA_URL, "1")
    assert v["make_label"] == "Mercedes-Benz" and v["_marke_aus_titel"] is False


# ------------------------------------------------------------ HTML-Weg
def _seite(titel, marke_zeile="", text="Kein Anruf, nur Mail. Man kann ihn besichtigen."):
    return f"""
<html><head><title>x</title></head><body>
<h1 id="viewad-title">{titel}</h1>
<h2 id="viewad-price">13.900 €</h2>
<span id="viewad-locality" itemprop="addressLocality">30179 Niedersachsen - Hannover</span>
<div id="viewad-details">
{marke_zeile}
<div>Kilometerstand</div><div>222.245 km</div>
</div>
<div>Beschreibung</div>
<div>{text}</div>
</body></html>
"""


def test_html_seitentext_macht_keine_marke():
    v = KS.parse_kleinanzeigen_html(_KA_URL, _seite("Sprinter 316 CDI Kasten"))
    assert not v["make_label"], v["make_label"]          # vorher: "Ruf" aus "Anruf"
    assert v["_resolved_make_id"] is None and v["_marke_aus_titel"] is False


def test_html_marke_aus_titel_und_konkrete_tabellenmarke():
    v = KS.parse_kleinanzeigen_html(_KA_URL, _seite("Mercedes-Benz Sprinter 316 CDI"))
    assert v["make_label"] == "Mercedes-Benz" and v["_marke_aus_titel"] is True
    v = KS.parse_kleinanzeigen_html(
        _KA_URL, _seite("Mini Cooper Optik", "<div>Marke</div><div>Dongfeng</div>"))
    assert v["make_label"] == "Dongfeng" and v["_marke_aus_titel"] is False


# ------------------------------------------------------------ Vergleich
def test_vergleich_hinweis_bei_marke_aus_dem_titel():
    h, mobile, autoscout = L._katalog_pruefen(
        {"make_label": "Mercedes-Benz", "model_label": "Sprinter", "_marke_aus_titel": True})
    assert mobile and autoscout
    assert any("aus dem Titel übernommen" in x for x in h), h
    h, _, _ = L._katalog_pruefen({"make_label": "Mercedes-Benz", "model_label": "Sprinter"})
    assert not any("aus dem Titel" in x for x in h), h


@pytest.mark.parametrize("platzhalter", ["Weitere Automarken", "Andere", "Sonstige"])
def test_vergleich_sammelposten_ist_keine_marke(platzhalter):
    """Vorher: AutoScout-Link /lst/weitere-automarken, bei "Andere" ein
    mobile.de-Link ueber die Sammelmarke."""
    h, mobile, autoscout = L._katalog_pruefen({"make_label": platzhalter, "model_label": None})
    assert not mobile and not autoscout
    assert len(h) == 1 and "keine Marke" in h[0] and platzhalter in h[0], h
