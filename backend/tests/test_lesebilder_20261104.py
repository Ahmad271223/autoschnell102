# -*- coding: utf-8 -*-
"""Wunsch Ahmad 04.11.2026: zwoelf Lesebilder nicht erkannter Anzeigen ausgewertet. Server-Seite: Kleinanzeigen-
Platzhalter "BMW Weitere BMW" mit Titel "BMW 540d xDrive/H&amp;..." (vorher kein Modell), HTML-Zeichen, das Portal-Logo
in der Titelzeile, "540d" in der Ueberschrift, ein falsch gelesener Kraftstoff ("Benzhi")."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fahrzeug_codes as fc  # noqa: E402
import werkzeug_erkennung as we  # noqa: E402


def _z(mm, titel):
    z = we.zuordnen(mm, titel)
    return z["marke"], z["modell"], z["modell_gefunden"]


def test_01_platzhalter_mit_titel_derselben_marke():
    assert _z("BMW Weitere BMW", "BMW 540d xDrive/H&amp;...") == ("BMW", "540", True)
    assert _z("BMW Weitere BMW", "BMW 540d xDrive") == ("BMW", "540", True)
    assert _z("VW Weitere VW", "VW Golf VII 1.5 TSI") == ("Volkswagen", "Golf", True)
    # Titel einer ANDEREN Marke hilft nicht (lieber kein Modell als ein fremdes)
    m, _modell, gefunden = _z("BMW Weitere BMW", "Audi A4 Avant")
    assert m == "BMW" and not gefunden
    # die zwoelf Lesebilder: alle Marken/Modelle, die auf dem Bildschirm standen
    for mm, titel, marke, modell in (
            ("Mercedes-Benz GLE 350", "Mercedes-Benz GLE ... Auto Scout24", "Mercedes-Benz", "GLE 350"),
            ("Kia Stonic", "Kia Stonic", "Kia", "Stonic"),
            ("Hyundai i30", "Hyundai i30 1...amobile.de", "Hyundai", "i30"),
            ("BMW 530d - F10", "BMW 530d - F10", "BMW", "530"),           # Programm 1.5.18: Titel als Marke/Modell
            ("VW", "Volkswagen Vw Touran", "Volkswagen", "Touran"),
            ("Ford Ka", "Ford Ka Trend", "Ford", "Ka/Ka+"),
            ("VW Passat Variant", "VW Passat Variant 1.5 TSI DSG eHybrid Business Navi", "Volkswagen", "Passat Variant"),
            ("Audi Q6 e-tron", "Audi Q6 e-tron performance S line business Neupreis 8", "Audi", "Q6 e-tron"),
            ("VW Polo", "VW Polo 1.0 TSI HIGHLINE APPLE/AN...", "Volkswagen", "Polo")):
        assert _z(mm, titel) == (marke, modell, True), (mm, titel)


def test_02_html_zeichen_und_logo_im_titel():
    assert we.text_saeubern("BMW 540d xDrive/H&amp;...") == "BMW 540d xDrive/H&..."
    assert we.text_saeubern("Hyundai i30 1...amobile.de") == "Hyundai i30 1..."
    assert we.text_saeubern("Mercedes-Benz GLE ... Auto Scout24") == "Mercedes-Benz GLE ..."
    assert we.text_saeubern("Ford Ka Trend kleinanzeigen") == "Ford Ka Trend"
    assert we.text_saeubern("Skoda Octavia Scout") == "Skoda Octavia Scout"
    assert we.text_saeubern(None) == ""
    # das Logo darf kein Modell liefern: "Auto Scout24" im Titel neben "Andere"
    assert _z("Skoda Andere", "Skoda Octavia Combi Auto Scout24")[1] != "Scout"


def test_03_modellzahl_mit_motorbuchstabe_in_der_ueberschrift():
    k = we.katalog()
    mm = k.mobile_marke("BMW")
    assert we.aus_titel(mm.roh, "BMW 540d xDrive Touring") == "540"
    assert we.aus_titel(mm.roh, "BMW 320i Limousine") == "320"
    assert _z("", "Mercedes-Benz C200d T-Modell")[2] is True


def test_04_falsch_gelesener_kraftstoff():
    assert fc.kraftstoff_code("Benzhi") == "PETROL"
    assert fc.kraftstoff_code("Benzii") == "PETROL"
    assert fc.kraftstoff_code("Dieel") == "DIESEL"
    assert fc.kraftstoff_code("Elektra") == "ELECTRICITY"
    # zu weit weg oder zu kurz: nichts geraten
    assert fc.kraftstoff_code("Allrad") is None and fc.kraftstoff_code("Bxyz") is None
    assert fc.kraftstoff_code("Klimaanlage") is None
