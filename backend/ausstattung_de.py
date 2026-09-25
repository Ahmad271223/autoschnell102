# -*- coding: utf-8 -*-
"""Ausstattungsbezeichnungen auf Deutsch (Befund Ahmad 26.09.2026).

Der Apify-Scraper memo23 liefert die mobile.de-Ausstattung auf ENGLISCH
("Alloy wheels", "Central locking", "Heated seats"), obwohl das Inserat
deutsch ist — so landete sie im Kaufvertrag, im Abholprotokoll und in der
Fahrer-App. Die mobile.de-Such-API tut dasselbe, wenn keine deutsche
Beschreibung mitkommt (Schluessel wie ALLOY_WHEELS -> "Alloy Wheels").

Hier die Tabelle englisch -> deutsch (Bezeichnungen wie auf mobile.de).
`uebersetzen` laesst Deutsches und Unbekanntes unveraendert, ist also
idempotent — es wird beim Auslesen, im PDF und in der Migration 15 fuer den
Bestand angewandt.
"""
from __future__ import annotations

import re
from typing import Iterable, List

_TABELLE = {
    # Sicherheit / Assistenz
    "abs": "ABS", "anti-lock braking system": "ABS", "esp": "ESP",
    "electronic stability program": "ESP", "electronic stability control": "ESP",
    "traction control": "Traktionskontrolle", "airbag": "Airbag", "alarm system": "Alarmanlage",
    "immobilizer": "Elektr. Wegfahrsperre", "immobiliser": "Elektr. Wegfahrsperre",
    "electronic immobilizer": "Elektr. Wegfahrsperre",
    "central locking": "Zentralverriegelung", "central locking system": "Zentralverriegelung",
    "keyless central locking": "Schlüssellose Zentralverriegelung",
    "keyless entry": "Schlüssellose Zentralverriegelung", "keyless go": "Keyless Go",
    "adaptive cruise control": "Abstandstempomat", "cruise control": "Tempomat",
    "distance warning system": "Abstandswarner", "distance control": "Abstandswarner",
    "speed limit control system": "Geschwindigkeitsbegrenzer", "speed limiter": "Geschwindigkeitsbegrenzer",
    "emergency brake assist": "Notbremsassistent", "emergency brake assistant": "Notbremsassistent",
    "emergency call system": "Notrufsystem", "emergency call": "Notrufsystem", "ecall": "Notrufsystem",
    "blind spot assist": "Totwinkel-Assistent", "blind spot monitor": "Totwinkel-Assistent",
    "lane change assist": "Spurwechselassistent", "lane departure warning system": "Spurhalteassistent",
    "lane keeping assist": "Spurhalteassistent", "lane assist": "Spurhalteassistent",
    "fatigue warning system": "Müdigkeitswarner", "attention assist": "Müdigkeitswarner",
    "traffic sign recognition": "Verkehrszeichenerkennung",
    "hill-start assist": "Berganfahrassistent", "hill start assist": "Berganfahrassistent",
    "high beam assist": "Fernlichtassistent", "glare-free high beam headlights": "Blendfreies Fernlicht",
    "rear traffic alert": "Querverkehrsassistent hinten", "cross traffic alert": "Querverkehrsassistent",
    "night vision assist": "Nachtsicht-Assistent", "night vision": "Nachtsicht-Assistent",
    "tyre pressure monitoring": "Reifendruckkontrolle", "tire pressure monitoring": "Reifendruckkontrolle",
    "tire pressure monitoring system": "Reifendruckkontrolle",
    "collision avoidance system": "Kollisionswarner", "isofix": "Isofix",
    "isofix child seat": "Isofix", "isofix passenger seat": "Isofix Beifahrersitz",
    "parking sensors": "Einparkhilfe", "parking assist": "Einparkhilfe", "park assist": "Einparkhilfe",
    "park distance control": "Einparkhilfe", "parking sensors front": "Einparkhilfe vorn",
    "parking sensors rear": "Einparkhilfe hinten", "parking sensors front and rear": "Einparkhilfe vorn und hinten",
    "self-steering systems": "Einparkhilfe selbstlenkend", "park assist system self-steering": "Einparkhilfe selbstlenkend",
    "rear view camera": "Rückfahrkamera", "reversing camera": "Rückfahrkamera", "camera": "Rückfahrkamera",
    "360° camera": "360°-Kamera", "360 camera": "360°-Kamera",
    # Licht
    "led headlights": "LED-Scheinwerfer", "full led": "LED-Scheinwerfer", "led running lights": "LED-Tagfahrlicht",
    "daytime running lights": "Tagfahrlicht", "xenon headlights": "Xenonscheinwerfer",
    "bi-xenon headlights": "Bi-Xenon-Scheinwerfer", "laser headlights": "Laserlicht",
    "halogen headlights": "Halogen-Scheinwerfer", "adaptive cornering lights": "Kurvenlicht",
    "cornering lights": "Kurvenlicht", "adaptive lighting": "Adaptives Kurvenlicht",
    "adaptive headlights": "Adaptives Kurvenlicht", "fog lamps": "Nebelscheinwerfer",
    "front fog lights": "Nebelscheinwerfer", "fog lights": "Nebelscheinwerfer",
    "light sensor": "Lichtsensor", "rain sensor": "Regensensor", "rain sensing wipers": "Regensensor",
    "headlight washer system": "Scheinwerferreinigung", "headlight cleaning": "Scheinwerferreinigung",
    "ambient lighting": "Ambiente-Beleuchtung", "ambient light": "Ambiente-Beleuchtung",
    "autom. dimming interior mirror": "Innenspiegel autom. abblendend",
    "automatically dimming interior mirror": "Innenspiegel autom. abblendend",
    # Komfort / Innen
    "air conditioning": "Klimaanlage", "manual climatisation": "Klimaanlage",
    "automatic climatisation": "Klimaautomatik", "automatic air conditioning": "Klimaautomatik",
    "automatic climate control": "Klimaautomatik", "no climatisation": "Keine Klimaanlage",
    "auxiliary heating": "Standheizung", "heated seats": "Sitzheizung", "electric heated seats": "Sitzheizung",
    "seat heating": "Sitzheizung", "heated rear seats": "Sitzheizung hinten", "rear seat heating": "Sitzheizung hinten",
    "seat ventilation": "Sitzbelüftung", "ventilated seats": "Sitzbelüftung", "massage seats": "Massagesitze",
    "electric seat adjustment": "Elektr. Sitzeinstellung", "electric seats": "Elektr. Sitzeinstellung",
    "electrically adjustable seats": "Elektr. Sitzeinstellung",
    "electric seat adjustment with memory function": "Elektr. Sitzeinstellung mit Memory-Funktion",
    "eletric seat adjustment with memory function": "Elektr. Sitzeinstellung mit Memory-Funktion",
    "memory seats": "Elektr. Sitzeinstellung mit Memory-Funktion",
    "lumbar support": "Lordosenstütze", "sport seats": "Sportsitze", "sports seats": "Sportsitze",
    "arm rest": "Armlehne", "armrest": "Armlehne", "fold flat passenger seat": "Umklappbarer Beifahrersitz",
    "leather steering wheel": "Lederlenkrad", "multifunction steering wheel": "Multifunktionslenkrad",
    "multifunctional steering wheel": "Multifunktionslenkrad", "heated steering wheel": "Beheizbares Lenkrad",
    "steering wheel heating": "Beheizbares Lenkrad", "paddle shifters": "Schaltwippen",
    "power assisted steering": "Servolenkung", "power steering": "Servolenkung",
    "electric windows": "Elektr. Fensterheber", "power windows": "Elektr. Fensterheber",
    "electric side mirror": "Elektr. Seitenspiegel", "electric exterior mirrors": "Elektr. Seitenspiegel",
    "folding exterior mirrors": "Elektr. anklappbare Außenspiegel",
    "electric tailgate": "Elektr. Heckklappe", "power tailgate": "Elektr. Heckklappe",
    "heated windshield": "Beheizbare Frontscheibe", "heated windscreen": "Beheizbare Frontscheibe",
    "tinted windows": "Getönte Scheiben", "sunroof": "Schiebedach", "sliding roof": "Schiebedach",
    "panoramic roof": "Panorama-Dach", "panoramic sunroof": "Panorama-Dach", "panoramic glass roof": "Panorama-Dach",
    "glass roof": "Glasdach", "sliding door": "Schiebetür", "sliding door left": "Schiebetür links",
    "sliding door right": "Schiebetür rechts", "sliding door both sides": "Schiebetür beidseitig",
    "dual sliding doors": "Schiebetür beidseitig", "ski bag": "Skisack", "cargo barrier": "Gepäckraumabtrennung",
    "partition wall": "Trennwand", "roof rails": "Dachreling", "roof rack": "Dachreling",
    "trailer coupling": "Anhängerkupplung", "trailer hitch": "Anhängerkupplung", "towbar": "Anhängerkupplung",
    "trailer coupling fixed": "Anhängerkupplung fest", "trailer coupling detachable": "Anhängerkupplung abnehmbar",
    "trailer coupling swivelling": "Anhängerkupplung schwenkbar",
    "digital cockpit": "Volldigitales Kombiinstrument", "on-board computer": "Bordcomputer",
    "onboard computer": "Bordcomputer", "head-up display": "Head-up Display", "head up display": "Head-up Display",
    "touchscreen": "Touchscreen", "voice control": "Sprachsteuerung",
    "winter package": "Winterpaket", "sports package": "Sportpaket", "sport package": "Sportpaket",
    "sports suspension": "Sportfahrwerk", "sport suspension": "Sportfahrwerk", "air suspension": "Luftfederung",
    "dynamic chassis control": "Adaptives Fahrwerk", "adaptive suspension": "Adaptives Fahrwerk",
    "smoker package": "Raucherpaket", "non-smoker vehicle": "Nichtraucher-Fahrzeug", "non-smoker": "Nichtraucher-Fahrzeug",
    "disabled accessible": "Behindertengerecht", "handicapped enabled": "Behindertengerecht",
    "right hand drive": "Rechtslenker", "taxi": "Taxi", "taxi or rental car": "Taxi/Mietwagen",
    # Multimedia
    "bluetooth": "Bluetooth", "hands-free kit": "Freisprecheinrichtung", "bluetooth hands-free": "Freisprecheinrichtung",
    "navigation system": "Navigationssystem", "navigation": "Navigationssystem",
    "android auto": "Android Auto", "apple carplay": "Apple CarPlay", "dab radio": "DAB-Radio",
    "tuner/radio": "Tuner/Radio", "radio": "Radio", "cd player": "CD-Spieler", "cd multichanger": "CD-Wechsler",
    "sound system": "Soundsystem", "usb port": "USB", "usb": "USB", "tv": "TV",
    "music streaming integrated": "Musikstreaming integriert",
    "induction charging for smartphones": "Induktionsladen für Smartphones", "wireless charging": "Induktionsladen für Smartphones",
    "wlan / wi-fi hotspot": "WLAN / WiFi Hotspot", "wifi hotspot": "WLAN / WiFi Hotspot", "wlan / wifi hotspot": "WLAN / WiFi Hotspot",
    # Antrieb / Reifen / Sonstiges
    "four-wheel drive": "Allradantrieb", "four wheel drive": "Allradantrieb", "4wd": "Allradantrieb", "awd": "Allradantrieb",
    "all-wheel drive": "Allradantrieb", "start-stop system": "Start/Stopp-Automatik", "start/stop": "Start/Stopp-Automatik",
    "start stop system": "Start/Stopp-Automatik", "particulate filter": "Partikelfilter", "particle filter": "Partikelfilter",
    "catalytic converter": "Katalysator", "catalyst": "Katalysator", "e10-enabled": "E10-geeignet",
    "biodiesel suitable": "Biodiesel-geeignet", "biodiesel conversion": "Biodiesel-Umrüstung",
    "vegetable oil suitable": "Pflanzenöl-geeignet", "alloy wheels": "Leichtmetallfelgen", "alloy rims": "Leichtmetallfelgen",
    "steel wheels": "Stahlfelgen", "all season tyres": "Allwetterreifen", "all-season tires": "Allwetterreifen",
    "all season tires": "Allwetterreifen", "winter tyres": "Winterreifen", "winter tires": "Winterreifen",
    "summer tyres": "Sommerreifen", "summer tires": "Sommerreifen", "spare wheel": "Ersatzrad",
    "space saver spare wheel": "Notrad", "emergency tyre repair kit": "Pannenkit", "tyre repair kit": "Pannenkit",
    "spoiler": "Spoiler", "metallic": "Metallic", "warranty": "Garantie", "full service history": "Scheckheftgepflegt",
    "new hu/au": "HU/AU neu", "new inspection": "HU/AU neu", "kickstarter": "Kickstarter", "e-starter": "E-Starter",
}

_MUSTER = [
    (re.compile(r"^automatic climatisation,?\s*(\d)\s*zones?$"), lambda m: f"{m.group(1)}-Zonen-Klimaautomatik"),
    (re.compile(r"^automatic climate control,?\s*(\d)\s*zones?$"), lambda m: f"{m.group(1)}-Zonen-Klimaautomatik"),
    (re.compile(r"^trailer coupling\s*\(?(fixed|detachable|swivelling|swiveling)\)?$"),
     lambda m: "Anhängerkupplung " + {"fixed": "fest", "detachable": "abnehmbar"}.get(m.group(1), "schwenkbar")),
    (re.compile(r"^sliding door\s*\(?(left|right|both sides|both)\)?$"),
     lambda m: "Schiebetür " + {"left": "links", "right": "rechts"}.get(m.group(1), "beidseitig")),
]


def _norm(text: str) -> str:
    t = str(text or "").strip().lower().replace("_", " ")
    t = re.sub(r"\s+", " ", t).strip(" .;")
    return t


def uebersetzen(text) -> str:
    """Eine Bezeichnung: englisch -> deutsch, sonst unveraendert (getrimmt)."""
    s = str(text or "").strip()
    if not s:
        return ""
    n = _norm(s)
    if n in _TABELLE:
        return _TABELLE[n]
    for muster, ersatz in _MUSTER:
        m = muster.match(n)
        if m:
            return ersatz(m)
    return s


def liste_uebersetzen(eintraege: Iterable) -> List[str]:
    """Liste uebersetzen, leere Eintraege und Dubletten (nach Uebersetzung) weg,
    Reihenfolge bleibt."""
    raus: List[str] = []
    gesehen = set()
    for e in eintraege or []:
        w = uebersetzen(e)
        if not w or w.casefold() in gesehen:
            continue
        gesehen.add(w.casefold())
        raus.append(w)
    return raus


def ist_englisch(text) -> bool:
    """Nur fuer Tests/Migration: steht die Bezeichnung in der Tabelle (also
    ist sie eine bekannte englische Form)?"""
    n = _norm(text)
    return n in _TABELLE and _TABELLE[n].casefold() != n or any(m.match(n) for m, _ in _MUSTER)
