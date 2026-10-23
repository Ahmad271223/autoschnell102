# -*- coding: utf-8 -*-
"""Wunsch Ahmad 04.10.2026 (Kleinanzeigen 3530655110): Modell "Weitere Mercedes Benz", Titel "Mercedes Benz
c300e", Beschreibung "... meinen Mercedes C 300 e ...". Vorher wurde das Modell "c300e" (kennt kein Portal ->
Vergleich ueber die ganze Marke). Jetzt: im Titel zaehlen Leerzeichen nicht und an einer Modellnummer darf ein
kurzer Zusatz haengen; aus der Beschreibung nur mit der Marke direkt davor und eindeutig — mit Pruef-Hinweis.
"Guenstiger als jeder Golf!" bleibt nur ein Vorschlag (Pruefbericht B-10)."""
import inspect
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mobile_service as M  # noqa: E402

BESCHREIBUNG = ("Verkaufe auf diesem Weg meinen Mercedes C 300 e, da ich ein Firmenfahrzeug erhalte und das "
                "Fahrzeug nicht mehr benötige.")


def _erkennen(marke, titel, text="", modell=None):
    v = {"make_label": marke, "model_label": modell or f"Weitere {marke}", "model_description": titel,
         "description": text}
    return M._enhance_generic_model(v)


def test_das_inserat_von_ahmad():
    v = _erkennen("Mercedes Benz", "Mercedes Benz c300e", BESCHREIBUNG)
    assert v["model_label"] == "C 300" and v["model"] == "C 300"
    assert not v.get("_modell_aus_beschreibung"), "kam aus dem Titel"


@pytest.mark.parametrize("titel,modell", [
    ("Mercedes Benz C300", "C 300"),
    ("Mercedes-Benz C220d AMG Line", "C 220"),
    ("Mercedes E220CDI Avantgarde", "E 220"),
    ("BMW 320d Touring", "320"),
])
def test_titel_leerzeichen_egal_zusatz_an_der_nummer(titel, modell):
    marke = titel.split()[0] if not titel.startswith("Mercedes") else "Mercedes Benz"
    assert _erkennen(marke, titel)["model_label"] == modell


def test_kein_falscher_zusatz():
    # an der Nummer haengt eine weitere ZIFFER -> anderes Modell, kein Treffer "320"
    assert _erkennen("BMW", "BMW 3200")["model_label"] != "320"


def test_aus_der_beschreibung_nur_mit_marke_davor_und_mit_hinweis():
    v = _erkennen("Mercedes Benz", "Mercedes Benz", BESCHREIBUNG)
    assert v["model_label"] == "C 300" and v["_modell_aus_beschreibung"] is True
    v = _erkennen("Volkswagen", "Volkswagen", "Verkaufe meinen VW Golf 7, TÜV neu")
    assert v["model_label"] == "Golf" and v["_modell_aus_beschreibung"] is True


@pytest.mark.parametrize("text", [
    "Günstiger als jeder Golf!",                       # B-10: keine Marke davor
    "Tausche meinen VW Golf gegen VW Polo",            # zwei verschiedene -> nicht raten
])
def test_beschreibung_ohne_eindeutigen_treffer_bleibt_offen(text):
    v = _erkennen("Volkswagen", "Weitere Volkswagen", text)
    assert v["model_label"] == "Weitere Volkswagen" and not v.get("_modell_aus_beschreibung")


def test_titel_schlaegt_beschreibung():
    v = _erkennen("Volkswagen", "VW Polo 1.2", "Mein alter VW Golf war auch toll")
    assert v["model_label"] == "Polo" and not v.get("_modell_aus_beschreibung")


def test_vergleich_sagt_bitte_pruefen():
    import routes.listings as L
    v = _erkennen("Mercedes Benz", "Mercedes Benz", BESCHREIBUNG)
    v["make"] = "MERCEDES_BENZ"
    hinweise, mobile_link, autoscout_link = L._katalog_pruefen(v)
    assert any("aus der Beschreibung übernommen — bitte kurz prüfen" in h and "C 300" in h for h in hinweise), hinweise
    assert mobile_link, "mobile.de kennt C 300 -> Link mit Modell"
    # aus dem Titel erkannt: kein Hinweis
    t = _erkennen("Mercedes Benz", "Mercedes Benz c300e", BESCHREIBUNG)
    assert not any("Beschreibung" in h for h in L._katalog_pruefen(t)[0])
