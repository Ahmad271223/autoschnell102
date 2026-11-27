# -*- coding: utf-8 -*-
"""Schadenspruefung aus dem Inserattext (Wunsch Ahmad 27.11.2026) — rein, ohne Server.

Die beiden langen Texte sind echte Lesungen vom 27.11.2026: der BMW X5 (Kleinanzeigen, Portalfeld "Unbeschädigtes
Fahrzeug", im Text Steuerkette/Bastlerfahrzeug) und der BMW X1 (mobile.de, gepflegt, nur Ausstattung) — der zweite
mit den Lesefehlern der Texterkennung, wie sie aus AutoPointer kommen.
"""
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

import schaden_pruefung as S  # noqa: E402

X5 = """Bitte erst die ganze Anzeige richtig lesen und dann Nachricht schreiben
Zum Verkauf steht ein gepflegter BMW X5 F15 xDrive30d (258 PS) aus dem Baujahr 2014.
Keine Luftfahrwerk
Der Motor springt sofort an und das Fahrzeug ist bedingt fahrbereit . Allerdings macht die Steuerkette deutliche
Geräusche und muss erneuert werden. Aus Sicherheitsgründen wird das Fahrzeug ausdrücklich als Export/ Bastlerfahrzeug
/ Reparaturbedürftig verkauft
Das Auto befindet sich für die Laufleistung in einem ordentlichen optischen Zustand mit normalen Gebrauchsspuren.
Getriebe schaltet sauber.
Rumänische Papiere.
TÜV 03.2027
Privatverkauf. Das Fahrzeug wird aufgrund des bekannten Mangels (Steuerkette) ausdrücklich als Bastlerfahrzeug unter
Ausschluss jeglicher Garantie, Gewährleistung und Sachmängelhaftung verkauft. Keine Rücknahme."""

X1 = """BMW Xl 23d X Drive.Alradantrieb.Das Auto wurde komplett beim BMW Händler Scheckheft gepflegt. Der letzte
Service war in 05.2025 bei 199000km.Die Bremsen vorne und hinten wurden komplett erneuert (Beläge und Scheiben).
Guter Zustand. 1. Hand.ABes funktbniert einwandfrei. Das Auto fährt sich BMW typisch sehr schön.
• Airbag Fahrer-/Befahrerseite• Anti-Bbckier-System (ABS)• Antriebsart: Alradantrieb permanent
• Getriebe Automatik - mit Steptronic (6-Stufen)• Motor 2,0 Ltr. - 150 kW Turbodiesel KAT• Rußpartikefiter
• Park-Distance-ControI (PDC) vorn und hinten• Refenpannen-Anzeige• Wegfahrsperre• Zentralverriegelung"""


def test_x5_steuerkette_bastler_ist_schwer_trotz_portalfeld_unbeschaedigt():
    e = S.pruefen(X5, zustand="Unbeschädigtes Fahrzeug", kategorie="SUV/Geländewagen/Pickup")
    assert e["stufe"] == S.SCHWER
    assert any("Steuerkette" in g or "Bastler" in g or "fahrbereit" in g for g in e["gruende"])
    assert len(e["gruende"]) <= S.MAX_GRUENDE
    assert S.meldung(e).startswith("Schaden im Inserat: ")


def test_x1_gepflegt_nur_ausstattung_ist_kein_schaden():
    e = S.pruefen(X1, zustand="Gebraucht", kategorie="SUV/Geländewagen/Pickup")
    assert e == {"stufe": S.KEIN, "gruende": [], "text_laenge": len(X1.strip())}
    assert S.meldung(e) is None


@pytest.mark.parametrize("text", [
    "Verkaufe meinen Golf wegen Motorschaden, läuft noch aber klopft.",
    "Getriebe defekt, Auto steht seit einem Jahr.",
    "Das Auto hatte einen Unfallschaden vorne links, wurde repariert.",
    "Fahrzeug springt nicht mehr an, muss abgeschleppt werden.",
    "Nur für Export oder Bastler, kein TÜV.",
    "Zylinderkopfdichtung muss gemacht werden.",
    "Auto ist im Notlauf, Turbo defekt.",
    "Wirtschaftlicher Totalschaden laut Gutachten.",
    "Hagelschaden auf Dach und Motorhaube.",
    "Leider nicht fahrbereit, Kupplung ist durch.",
    "Der Wagen ist ein Unfalwagen, Front beschädigt.",          # Lesefehler: ein l fehlt
    "Die Steuerkette rasselt beim Kaltstart.",
    "Steuerkette müsste demnächst gemacht werden.",
    "Keine Kratzer, aber Motor defekt.",                        # die Verneinung gilt nur fuer die Kratzer
])
def test_schwere_schaeden(text):
    assert S.pruefen(text)["stufe"] == S.SCHWER


@pytest.mark.parametrize("text", [
    "Gepflegter Wagen mit ein paar Kratzern an der Stoßstange.",
    "Kleine Delle in der Beifahrertür, sonst top.",
    "Steinschlag in der Frontscheibe, TÜV neu.",
    "Altersbedingte Gebrauchsspuren vorhanden, technisch einwandfrei.",
    "Fensterheber hinten rechts ist defekt, alles andere funktioniert.",
    "Klimaanlage funktioniert nicht, muss befüllt werden.",
    "Hat einen kleinen Schaden am Kotflügel, siehe Bilder.",
    "Leichter Rost am Radlauf hinten.",
])
def test_leichte_schaeden(text):
    assert S.pruefen(text)["stufe"] == S.LEICHT


@pytest.mark.parametrize("text", [
    "Das Auto hat einen Schaden hinten, bitte Bilder ansehen.",
    "Einige Mängel sind vorhanden, Liste auf Anfrage.",
])
def test_schaden_ohne_groesse_ist_unklar(text):
    assert S.pruefen(text)["stufe"] == S.UNKLAR


@pytest.mark.parametrize("text", [
    "Unfallfrei, scheckheftgepflegt, Nichtraucher, keine Kratzer, keine Dellen.",
    "Kein Motorschaden, kein Getriebeschaden, Auto läuft einwandfrei.",
    "Fahrzeug ist mängelfrei und rostfrei, TÜV ohne Mängel bestanden.",
    "Frei von Kratzern und Beulen, top gepflegt aus erster Hand.",
    "Technisch und optisch in sehr gutem Zustand, fahrbereit und angemeldet.",
    "Privatverkauf unter Ausschluss jeglicher Gewährleistung und Sachmängelhaftung.",
    "Steuerkette wurde 2024 bei BMW erneuert, Rechnung vorhanden.",
    "Zahnriemen neu, Inspektion neu, Bremsen neu.",
    "Keine Kratzer, Dellen oder Beulen, Lack wie neu.",
])
def test_verneint_oder_nichts_ist_kein_schaden(text):
    e = S.pruefen(text)
    assert e["stufe"] == S.KEIN, e


@pytest.mark.parametrize("text", ["", None, "VB", "siehe Bilder"])
def test_ohne_beschreibung_ist_nicht_beurteilbar(text):
    assert S.pruefen(text)["stufe"] == S.OHNE_TEXT


def test_portalangabe_unfallwagen_reicht_auch_ohne_text():
    e = S.pruefen("", zustand="Unfallwagen", kategorie="Limousine")
    assert e["stufe"] == S.SCHWER
    assert e["gruende"][0].startswith("Angabe im Inserat: ")


def test_titel_zaehlt_mit():
    assert S.pruefen("Bei Fragen einfach anrufen, bin meist erreichbar.", titel="Polo 9N Unfallfahrzeug")["stufe"] == S.SCHWER


def test_schwerste_stufe_gewinnt_und_steht_vorn():
    e = S.pruefen("Kleine Kratzer am Heck. Außerdem Getriebeschaden, schaltet nicht mehr in den dritten Gang.")
    assert e["stufe"] == S.SCHWER
    assert "Getriebeschaden" in e["gruende"][0]
    assert any("Kratzer" in g for g in e["gruende"])
