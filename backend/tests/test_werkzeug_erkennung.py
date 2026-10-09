"""Marke/Modell-Erkennung fuer das Windows-Programm — seit 03.10.2026 auf dem Server (werkzeug_erkennung.py).

Wunsch Ahmad: "Erkennung auf den Server verlegen — wenn es das Programm nicht schlechter macht". Deshalb:
  * alle Faelle der bisherigen Programm-Tests (KatalogLinkTests.cs) hier noch einmal,
  * Soll-Werte aus dem Programm 1.3.5 (C#) fuer 1.676 Faelle (fixtures/werkzeug_erkennung_programm_1_3_5.json,
    Stichprobe aus 16.636 Faellen, die am 03.10.2026 ohne eine Abweichung abgeglichen wurden).
Aendert sich ein Soll-Wert absichtlich (Verbesserung), die Fixture mit Begruendung nachziehen.
"""
import json
from pathlib import Path

import pytest

import werkzeug_erkennung as we

FIXTURE = Path(__file__).parent / "fixtures" / "werkzeug_erkennung_programm_1_3_5.json"
PASSAT_TITEL = "VW Passat B6 - Bastlerfahrzeug"
BENTLEY_TITEL = "Bentley Bentayga V8 Diesel, 1. Hand, Mulliner, Nai..."


def test_00_soll_werte_aus_dem_programm():
    faelle = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert len(faelle) > 1500
    abweichungen = []
    for fall in faelle:
        ist = we.zuordnen(fall["text"], fall["titel"])
        soll = fall["erwartet"]
        if any(ist[k] != soll[k] for k in ("marke_text", "modell_text", "marke", "modell", "erkannt")):
            abweichungen.append((fall["text"], fall["titel"], soll, ist))
    assert not abweichungen, abweichungen[:5]


@pytest.mark.parametrize("text,marke,modell", [
    ("VW Passat Variant", "VW", "Passat Variant"),
    ("Bentley Bentayga", "Bentley", "Bentayga"),
    ("Land Rover Range Rover Evoque", "Land Rover", "Range Rover Evoque"),
    ("Mercedes-Benz C 200", "Mercedes-Benz", "C 200"),
    ("Alfa Romeo Giulia", "Alfa Romeo", "Giulia"),
    ("Rolls-Royce Ghost", "Rolls-Royce", "Ghost"),
    ("BMW 320", "BMW", "320"),
    ("Bentlev Bentayga", "Bentley", "Bentayga"),          # Lesefehler in der Marke
])
def test_01_marke_und_modell_trennen(text, marke, modell):
    assert we.katalog().teile(text) == (marke, modell)


def test_02_unbekannte_marke():
    assert we.katalog().teile("Quatschmarke X1") is None


@pytest.mark.parametrize("marke,modell,marke_id,modell_id", [
    ("Bentley", "Bentayga", "3100", "16"), ("VW", "Passat Variant", "25200", "63"), ("Mercedes-Benz", "C 200", "17200", "18"),
])
def test_03_mobile_ids_wie_im_backend(marke, modell, marke_id, modell_id):
    k = we.katalog()
    m = k.mobile_marke(marke)
    assert m.id == marke_id
    t = k.mobile_modell(m, modell, None)
    assert t.id == modell_id and not t.unscharf


def test_04_lesefehler_unscharf_und_titel():
    k = we.katalog()
    t = k.mobile_modell(k.mobile_marke("Bentley"), "Bentavga", None)
    assert t.id == "16" and t.unscharf
    assert k.mobile_modell(k.mobile_marke("VW"), "Andere", "VW Golf 1.4 TSI Highline").name == "Golf"
    bmw = k.autoscout_marke("BMW")
    t = k.autoscout_modell(bmw, "M340i", None)
    assert t is None or t.name != "M3"                    # RP-435


def test_05_wortgrenzen_wie_im_backend():
    assert we.wortgrenzen("E 220 d") == {1, 4, 5}
    assert we.wortgrenzen("M340i") == {1, 4, 5}
    assert we.wortgrenzen("CLA Shooting Brake") == {3, 11, 16}


def test_06_an_den_server_geht_was_autopointer_zeigt():
    z = we.zuordnen("VW Passat Variant", PASSAT_TITEL)
    assert z["erkannt"] and (z["marke_text"], z["modell_text"]) == ("VW", "Passat Variant")
    assert (z["marke"], z["modell"]) == ("Volkswagen", "Passat Variant")
    assert we.zuordnen("Bentley Bentavga", BENTLEY_TITEL)["modell_text"] == "Bentayga"


@pytest.mark.parametrize("text,titel,modell", [
    ("VW weitere VW", "VW Beetle Cabrio 1.2 TSI", "Beetle"),
    ("VW Andere", "VW Beetle Cabrio 1.2 TSI", "Beetle"),
    ("VW weitere VW", "Schoenes Cabrio", "weitere VW"),
    ("VW VW-Busse", "T5 Bulli multivan", "T5 Multivan"),
    ("VW VW-Busse", "Suche einen 7 sitzer", "VW-Busse"),
    ("VW VW-Busse", "VW T 5 Multivan", "T5 Multivan"),
])
def test_07_platzhalter_modell_aus_dem_titel(text, titel, modell):
    z = we.zuordnen(text, titel)
    assert (z["marke_text"], z["modell_text"]) == ("VW", modell)


@pytest.mark.parametrize("feld,titel,marke,modell", [
    ("Andere", "Ford Mondeo Turnier 2.0 TDCi Diesel, ...", "Ford", "Mondeo"),
    ("Andere", "Ford Mondeo Turnier 2.0 TDCi Diesel, …", "Ford", "Mondeo"),
    ("Sonstige", "BMW 320d Touring", "BMW", "320"),
    ("Andere", "VW Weitere VW, Golf 4 1.9TDI Aussch...", "VW", "Golf"),
])
def test_08_ohne_marke_alles_aus_dem_titel(feld, titel, marke, modell):
    z = we.zuordnen(feld, titel)
    assert z["erkannt"] and z["marke_text"] == marke and z["modell_text"].startswith(modell)


@pytest.mark.parametrize("feld", ["Hyundai ilO", "Hyundai il0", "Hyundai i1O", "Hyundai 110", "Hyundai i10"])
def test_09_lesefehler_i_l_1_und_o_0(feld):
    k = we.katalog()
    z = we.zuordnen(feld, "Hyundai ilO Classic")
    assert (z["marke_text"], z["modell_text"]) == ("Hyundai", "i10")
    assert k.mobile_modell(k.mobile_marke("Hyundai"), feld.split(" ")[1], "Hyundai ilO Classic") is not None
    assert k.autoscout_modell(k.autoscout_marke("Hyundai"), feld.split(" ")[1], "Hyundai ilO Classic") is not None


def test_10_volvo_xc_60_und_byd():
    assert we.zuordnen("volvo xcoo", "XC 60 D3 2017. mit Turboschad")["modell_text"].replace(" ", "") == "XC60"
    z = we.zuordnen("BYD DOLPHIN", "BYD Dolphin G DM-i Comfort")
    assert z["marke_text"] == "BYD" and z["erkannt"]


def test_11_verwechslung_und_titelabgleich():
    assert we.verwechslung(["i10", "i20", "i30"], "ilO") == "i10"
    assert we.verwechslung(["i10", "110"], "ilO") is None
    assert we.verwechslung(["Golf", "Polo"], "Go1f") is None
    assert we.aus_titel(["i10", "i20", "Atos"], "Hyundai ilO Classic") == "i10"
    assert we.aus_titel(["T5 (Alle)", "T5 andere", "Multivan", "T5 Multivan"], "VW T5 Bulli Multivan") == "T5 Multivan"
    assert we.aus_titel(["T5 (Alle)", "T5 andere"], "T5 andere Ausstattung alle") is None
    assert we.aus_titel(["Beetle", "New Beetle"], "VW Beetle Cabrio 1.2 TSI") == "Beetle"
    assert we.aus_titel(["Beetle", "New Beetle"], "VW New Beetle 1.6") == "New Beetle"


def test_12_unbekannte_marke():
    assert not we.zuordnen("Quatschmarke X1", "Quatschmarke X1 Sport")["erkannt"]
    z = we.zuordnen("Quatschmarke X1", BENTLEY_TITEL)            # Marke aus der Ueberschrift
    assert z["erkannt"] and z["marke_text"] == "Bentley"
    leer = we.zuordnen("", "")
    assert not leer["erkannt"] and leer["marke"] is None


# Befund 04.10.2026: Feld "Andere", Ueberschrift "Mercedes-Benz Weitere Mercedes Be...", Modell nur in der Beschreibung
MERCEDES_TEXT = ("Verkaufe auf diesem Weg meinen Mercedes C 300 e, da ich ein Firmenfahrzeug erhalte und das Fahrzeug "
                 "nicht mehr benötige.Im Grunde hat das Fahrzeug alles, was man braucht:Gutes Soundsystem")


def test_13_modell_aus_der_beschreibung():
    z = we.zuordnen("Andere", "Mercedes-Benz Weitere Mercedes Be...", beschreibung=MERCEDES_TEXT)
    assert (z["marke_text"], z["modell_text"], z["aus_beschreibung"]) == ("Mercedes-Benz", "C 300", True)
    # ohne Beschreibung wie bisher (Soll-Werte unveraendert)
    assert we.zuordnen("Andere", "Mercedes-Benz Weitere Mercedes Be...")["aus_beschreibung"] is False


def test_14_beschreibung_nur_wenn_feld_und_ueberschrift_nichts_ergeben():
    z = we.zuordnen("VW Golf", "VW Golf 1.4", beschreibung="Tausche auch gegen einen Passat Variant")
    assert z["modell_text"] == "Golf" and z["aus_beschreibung"] is False


def test_15_beschreibung_ist_strenger_als_die_ueberschrift():
    # Woerter muessen zusammenstehen ("C 300", "C300", "320d" fuer 320) — verstreut zaehlt nicht
    assert we.aus_beschreibung(["C 300", "C 200"], "Typ C3OO, Bj 2020") == "C 300"
    assert we.aus_beschreibung(["320", "X3"], "Schoener 320d Touring, kein Unfall") == "320"
    assert we.aus_beschreibung(["C 300"], "Klasse C mit 300 PS") is None
    # sehr kurze Namen und Sammelnamen nie
    assert we.aus_beschreibung(["G", "V", "T1", "CL"], "G Klasse, V Klasse, T1, CL") is None
    assert we.aus_beschreibung(["T5 andere", "T5 (Alle)"], "T5 andere Alle") is None
    # mehr Woerter gewinnen
    assert we.aus_beschreibung(["C 300", "C 300 AMG"], "C 300 AMG Line") == "C 300 AMG"



def test_16_platzhalter_mit_zusammengeschriebenem_modell():
    """Befund Ahmad 08.10.2026 (AutoScout, Feld "Mercedes-Benz Andere", Ueberschrift "Mercedes-Benz Andere EQA300 ..."):
    "EQA300" ist EQA (vorher blieb "Andere" stehen); "EQA 300" ergab das alte Mercedes-Modell "300"."""
    z = we.zuordnen("Mercedes-Benz Andere", "Mercedes-Benz Andere EQA300 ...")
    assert (z["modell_text"], z["modell"]) == ("EQA", "EQA")
    assert we.zuordnen("Mercedes-Benz Andere", "Mercedes-Benz Andere EQA 300 4MATIC")["modell"] == "EQA"
    assert we.zuordnen("Mercedes-Benz Andere", "Mercedes-Benz Andere GLC300 4Matic")["modell"] == "GLC 300"
    # reine Zahl nur, wenn kein Name mit Buchstaben passt
    assert we.aus_titel(["300", "EQA"], "Mercedes EQA 300") == "EQA"
    assert we.aus_titel(["300", "EQA"], "Mercedes 300 SE") == "300"


def test_17_weitere_lesefehler_der_texterkennung():
    """Befund Ahmad 09.10.2026: Hyundai "i30" als "IBO" gelesen (3 -> B, 0 -> O) — nur eindeutige Treffer."""
    z = we.zuordnen("Hyundai IBO", "Hyundai IBO 1.4 Trend")
    assert (z["modell_text"], z["modell"]) == ("i30", "i30")
    assert we.verwechslung(["i30", "i10", "i20"], "IBO") == "i30"
    assert we.verwechslung(["i30", "i80"], "IBO") is None, "B kann 3 oder 8 sein -> zwei Treffer, keiner"
    assert we.verwechslung(["X5", "X3"], "XS") == "X5"
    assert we.verwechslung(["Golf"], "Go1f") is None, "nur Namen mit Ziffern"
