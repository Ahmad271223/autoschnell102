# -*- coding: utf-8 -*-
"""Eigene Ausgangswerte fuer Schaeden und Abweichungen (Wunsch Ahmad
25.09.2026): Die KI entscheidet Kategorie, Verfahren und Schwere — die
Ausgangsbasis kommt von uns, damit derselbe Fall nicht heute 250 und morgen
650 EUR ergibt. Die Werte hier sind STARTWERTE (Marktschaetzung Deutschland,
Stand 09/2026, Werkstatt/Smart-Repair inkl. MwSt.); sie werden spaeter aus den
echten AutoSchnell-Faellen (ki_lernfaelle) nachjustiert.

Zusaetzlich die deterministische Vorsortierung (Prioritaet rot/orange/gelb),
die nicht von der KI abhaengt."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

# (Schadensart, Auspraegung) -> (Reparaturweg, Basisspanne EUR)
BASIS: List[Dict[str, Any]] = [
    {"typ": "delle", "auspraegung": "klein, Lack intakt (bis 5 cm)",
     "verfahren": "Smart-Repair / Ausbeulen ohne Lackieren", "min": 100, "max": 250},
    {"typ": "delle", "auspraegung": "mittel, Lack intakt (5-10 cm)",
     "verfahren": "Ausbeulen ohne Lackieren", "min": 180, "max": 350},
    {"typ": "delle", "auspraegung": "Lack beschaedigt oder > 10 cm",
     "verfahren": "Ausbeulen + Lackierung des Bauteils", "min": 300, "max": 650},
    {"typ": "kratzer", "auspraegung": "oberflaechlich, polierbar (bis 15 cm)",
     "verfahren": "Polieren / Spot-Repair", "min": 50, "max": 150},
    {"typ": "kratzer", "auspraegung": "tief oder > 15 cm",
     "verfahren": "Lackierung des Bauteils", "min": 250, "max": 550},
    {"typ": "kratzer", "auspraegung": "Stossfaenger, Lackierung noetig",
     "verfahren": "Stossfaenger lackieren", "min": 300, "max": 600},
    {"typ": "steinschlag", "auspraegung": "Lack, einzeln",
     "verfahren": "Spot-Repair", "min": 40, "max": 120},
    {"typ": "steinschlag", "auspraegung": "Windschutzscheibe, reparabel",
     "verfahren": "Scheibenreparatur", "min": 60, "max": 120},
    {"typ": "steinschlag", "auspraegung": "Windschutzscheibe, Tausch noetig",
     "verfahren": "Scheibentausch (ohne Sensorik)", "min": 350, "max": 900},
    {"typ": "rost", "auspraegung": "oberflaechlich, klein",
     "verfahren": "Anschleifen + Lackieren", "min": 150, "max": 400},
    {"typ": "rost", "auspraegung": "durchgerostet / tragend",
     "verfahren": "Blecharbeit — nur mit Werkstattdiagnose",
     "min": 500, "max": 1500, "manuell": True},
    {"typ": "hagelschaden", "auspraegung": "wenige Dellen",
     "verfahren": "Ausbeulen ohne Lackieren", "min": 300, "max": 900},
    {"typ": "hagelschaden", "auspraegung": "grossflaechig",
     "verfahren": "PDR ganzes Fahrzeug", "min": 900, "max": 3000},
    {"typ": "beleuchtung", "auspraegung": "Leuchtmittel / eingeschraenkt",
     "verfahren": "Leuchtmittel tauschen", "min": 30, "max": 120},
    {"typ": "beleuchtung", "auspraegung": "komplett ausgefallen / Gehaeuse",
     "verfahren": "Scheinwerfer/Leuchte tauschen (Halogen bis LED stark unterschiedlich)",
     "min": 150, "max": 900},
    {"typ": "felge", "auspraegung": "Bordsteinschaden, kosmetisch",
     "verfahren": "Felgenaufbereitung", "min": 80, "max": 180},
    {"typ": "unfall_nicht_repariert", "auspraegung": "jeder",
     "verfahren": "Karosserie — nur mit Werkstattdiagnose",
     "min": 500, "max": 5000, "manuell": True},
    {"typ": "unfall_repariert", "auspraegung": "dokumentiert",
     "verfahren": "kein Reparaturbedarf, Wertminderung", "min": 0, "max": 0},
    # Abweichungen ohne Schaden
    {"typ": "keys", "auspraegung": "ein Schluessel fehlt (Funkschluessel)",
     "verfahren": "Ersatz + Anlernen (Marke entscheidet)", "min": 150, "max": 450},
    {"typ": "tires", "auspraegung": "ein Satz deutlich schlechter / abgefahren",
     "verfahren": "Reifensatz (Klasse entscheidet)", "min": 250, "max": 900},
    {"typ": "tires", "auspraegung": "zweiter Radsatz fehlt",
     "verfahren": "Ersatz Radsatz", "min": 300, "max": 1500},
    {"typ": "documents", "auspraegung": "Zulassungsbescheinigung Teil II fehlt",
     "verfahren": "Ersatz beim Amt + Risiko", "min": 100, "max": 300, "manuell": True},
    {"typ": "documents", "auspraegung": "Servicebuch / HU-Bericht fehlt",
     "verfahren": "Nachweislücke, Wertminderung", "min": 50, "max": 300},
    {"typ": "documents", "auspraegung": "Bedienungsanleitung / Zubehoer fehlt",
     "verfahren": "organisatorisch", "min": 0, "max": 60},
    {"typ": "hu", "auspraegung": "HU abgelaufen oder frueher faellig als vereinbart",
     "verfahren": "HU + moegliche Maengel", "min": 120, "max": 400},
    {"typ": "mileage", "auspraegung": "je 1.000 km ueber Vertrag (Richtwert, fahrzeugabhaengig)",
     "verfahren": "Wertminderung", "min": 20, "max": 60},
    {"typ": "previous_owners", "auspraegung": "je zusaetzlicher Halter",
     "verfahren": "Wertminderung (jung/hochpreisig staerker)", "min": 80, "max": 300},
    {"typ": "equipment_missing", "auspraegung": "Komfort/Assistenz fehlt (z. B. Kamera, Navi, AHK)",
     "verfahren": "Nachruestung oder Wertminderung", "min": 150, "max": 800},
    {"typ": "equipment_defect", "auspraegung": "vorhanden, aber defekt",
     "verfahren": "Reparatur (bauteilabhaengig)", "min": 100, "max": 900},
    {"typ": "warning_light", "auspraegung": "Motor/Getriebe/Airbag/ABS",
     "verfahren": "nur mit Diagnose — hohes Preisrisiko", "min": 0, "max": 0, "manuell": True},
    {"typ": "accident_history", "auspraegung": "Unfallfreiheit weicht ab",
     "verfahren": "wesentliche Vertragsabweichung — manuelle Entscheidung",
     "min": 0, "max": 0, "manuell": True},
]


def basis_als_text() -> str:
    """Fuer den System-Prompt: kompakte Tabelle unserer Ausgangswerte."""
    zeilen = ["Ausgangswerte AutoSchnell (EUR inkl. MwSt., Deutschland 2026; als Orientierung, "
              "immer auf Fahrzeugwert/Alter/Klasse anpassen):"]
    for b in BASIS:
        preis = ("manuelle Entscheidung, keine Zahl" if b.get("manuell") and b["max"] == 0
                 else f"{b['min']}-{b['max']}")
        zeilen.append(f"- {b['typ']} / {b['auspraegung']}: {b['verfahren']}: {preis}"
                      + (" (manual_review_required=true)" if b.get("manuell") else ""))
    return "\n".join(zeilen)


# ------------------------------------------------ deterministische Prioritaet
ROT = {"accident_history", "warning_light", "technical", "damage_worse"}
ORANGE = {"damage", "keys", "tires", "equipment_missing", "equipment_defect", "hu", "documents"}


def prioritaet(kategorie: str, *, betrag: Optional[float] = None,
               kaufpreis: Optional[float] = None, manuell: bool = False) -> str:
    """rot = Handlungsbedarf, orange = preisrelevant, gelb = geringer Einfluss.
    Ein grosser Betrag (>= 5 % des Kaufpreises oder >= 500 EUR) hebt auf rot."""
    if manuell or kategorie in ROT:
        return "rot"
    if betrag is not None and betrag > 0:
        if betrag >= 500 or (kaufpreis and kaufpreis > 0 and betrag >= 0.05 * kaufpreis):
            return "rot"
        if betrag < 100:
            return "gelb"
    if kategorie in ORANGE:
        return "orange"
    if kategorie == "mileage":
        return "gelb" if (betrag or 0) < 150 else "orange"
    return "gelb"


def prioritaet_reihenfolge(p: str) -> int:
    return {"rot": 0, "orange": 1, "gelb": 2}.get(p, 3)
