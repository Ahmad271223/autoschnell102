# -*- coding: utf-8 -*-
"""Eigene Ausgangswerte fuer Schaeden und Abweichungen (Wunsch Ahmad
25./26.09.2026): Die Formularangaben (Groesse, Lack, Umfang, Technik ...)
bestimmen deterministisch die Auspraegung; die Ausgangsbasis dafuer kommt
von uns, damit derselbe Fall nicht heute 250 und morgen 650 EUR ergibt.
Die Werte sind STARTWERTE (Marktschaetzung Deutschland, Stand 09/2026,
Werkstatt/Smart-Repair inkl. MwSt.); die monatliche Markttabelle
(ai.marktdaten) und die eigenen Faelle (ki_lernfaelle) legen sich darueber.

Fassung 26.09.2026: feinere Kategorien (Rostblasen nach Groesse, Hagel je
Bauteil, mehrere Steinschlaege, Beleuchtung je Technik) und `zuordnen`, das
aus type_key + severity_data die passende Zeile findet — "unbekannt" nimmt
die vorsichtige (teurere) Annahme und meldet das.

Zusaetzlich die deterministische Vorsortierung (Prioritaet rot/orange/gelb).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

# (Schadensart, Auspraegung) -> (Reparaturweg, Basisspanne EUR); "schluessel"
# ist der feste Bezeichner fuer die Zuordnung aus dem Formular.
BASIS: List[Dict[str, Any]] = [
    {"typ": "delle", "schluessel": "delle_klein", "auspraegung": "klein, Lack intakt (bis 5 cm)",
     "verfahren": "Smart-Repair / Ausbeulen ohne Lackieren", "min": 80, "max": 220},
    {"typ": "delle", "schluessel": "delle_mittel", "auspraegung": "mittel, Lack intakt (5-10 cm)",
     "verfahren": "Ausbeulen ohne Lackieren", "min": 150, "max": 350},
    {"typ": "delle", "schluessel": "delle_lack", "auspraegung": "Lack beschaedigt oder > 10 cm",
     "verfahren": "Ausbeulen + Lackierung des Bauteils", "min": 300, "max": 650},
    {"typ": "delle", "schluessel": "delle_kante", "auspraegung": "an Kante/Sicke, Lack beschaedigt",
     "verfahren": "Ausbeulen + Lackierung, aufwendig", "min": 400, "max": 800},
    {"typ": "kratzer", "schluessel": "kratzer_polierbar", "auspraegung": "oberflaechlich, polierbar (bis 15 cm)",
     "verfahren": "Polieren / Spot-Repair", "min": 50, "max": 150},
    {"typ": "kratzer", "schluessel": "kratzer_grundierung", "auspraegung": "bis Grundierung oder 15-30 cm",
     "verfahren": "Spot-Repair / Teillackierung", "min": 150, "max": 350},
    {"typ": "kratzer", "schluessel": "kratzer_tief", "auspraegung": "bis Blech oder > 30 cm",
     "verfahren": "Lackierung des Bauteils", "min": 300, "max": 600},
    {"typ": "kratzer", "schluessel": "kratzer_stossfaenger", "auspraegung": "Stossfaenger, Lackierung noetig",
     "verfahren": "Stossfaenger lackieren", "min": 300, "max": 600},
    {"typ": "kratzer", "schluessel": "kratzer_mehrere", "auspraegung": "mehrere Bauteile betroffen",
     "verfahren": "mehrere Bauteile lackieren", "min": 600, "max": 1400},
    {"typ": "steinschlag", "schluessel": "steinschlag_lack_einzeln", "auspraegung": "Lack, einzeln",
     "verfahren": "Spot-Repair", "min": 40, "max": 120},
    {"typ": "steinschlag", "schluessel": "steinschlag_lack_mehrere", "auspraegung": "Lack, mehrere (Front/Haube)",
     "verfahren": "Spot-Repair mehrere Stellen / Teillackierung", "min": 120, "max": 350},
    {"typ": "steinschlag", "schluessel": "steinschlag_haube_flaechig", "auspraegung": "Motorhaube flaechig",
     "verfahren": "Haube lackieren", "min": 350, "max": 700},
    {"typ": "steinschlag", "schluessel": "steinschlag_scheibe_reparabel", "auspraegung": "Windschutzscheibe, reparabel (bis 2 cm, ausserhalb Sichtfeld)",
     "verfahren": "Scheibenreparatur", "min": 60, "max": 150},
    {"typ": "steinschlag", "schluessel": "steinschlag_scheibe_tausch", "auspraegung": "Windschutzscheibe, Riss / Tausch noetig",
     "verfahren": "Scheibentausch (mit Sensorik teurer)", "min": 350, "max": 1000},
    {"typ": "steinschlag", "schluessel": "steinschlag_scheibe_andere", "auspraegung": "andere Scheibe beschaedigt",
     "verfahren": "Scheibentausch Seite/Heck", "min": 200, "max": 600},
    {"typ": "rost", "schluessel": "rost_oberflaechlich", "auspraegung": "oberflaechlich, klein",
     "verfahren": "Anschleifen + Lackieren", "min": 120, "max": 350},
    {"typ": "rost", "schluessel": "rost_blasen_klein", "auspraegung": "Blasen bis 5 cm",
     "verfahren": "Rost entfernen + Lackaufbau", "min": 250, "max": 500},
    {"typ": "rost", "schluessel": "rost_blasen_mittel", "auspraegung": "Blasen 5-15 cm",
     "verfahren": "Rost entfernen + Bauteil lackieren", "min": 400, "max": 800},
    {"typ": "rost", "schluessel": "rost_blasen_gross", "auspraegung": "Blasen > 15 cm",
     "verfahren": "Blecharbeit + Lackierung", "min": 700, "max": 1500},
    {"typ": "rost", "schluessel": "rost_durch", "auspraegung": "durchgerostet",
     "verfahren": "Blecharbeit — nur mit Werkstattdiagnose", "min": 500, "max": 2000, "manuell": True},
    {"typ": "rost", "schluessel": "rost_tragend", "auspraegung": "tragendes Teil (Schweller, Traeger, Achse) betroffen",
     "verfahren": "sicherheitsrelevant — Werkstattdiagnose", "min": 0, "max": 0, "manuell": True},
    {"typ": "hagelschaden", "schluessel": "hagel_wenige", "auspraegung": "wenige Dellen, einzelnes Bauteil",
     "verfahren": "Ausbeulen ohne Lackieren (PDR)", "min": 200, "max": 600},
    {"typ": "hagelschaden", "schluessel": "hagel_viele_bauteil", "auspraegung": "viele Dellen, ein Bauteil (Dach/Haube)",
     "verfahren": "PDR ein Bauteil", "min": 500, "max": 1200},
    {"typ": "hagelschaden", "schluessel": "hagel_mehrere_bauteile", "auspraegung": "sehr viele Dellen, mehrere Bauteile",
     "verfahren": "PDR mehrere Bauteile", "min": 1000, "max": 2500},
    {"typ": "hagelschaden", "schluessel": "hagel_ganz", "auspraegung": "ganzes Fahrzeug",
     "verfahren": "PDR ganzes Fahrzeug", "min": 1500, "max": 5000},
    {"typ": "hagelschaden", "schluessel": "hagel_lack", "auspraegung": "mit Lackschaeden",
     "verfahren": "Ausbeulen + Lackierung", "min": 800, "max": 3000},
    {"typ": "beleuchtung", "schluessel": "licht_leuchtmittel", "auspraegung": "Leuchtmittel / eingeschraenkt (Halogen)",
     "verfahren": "Leuchtmittel tauschen", "min": 20, "max": 80},
    {"typ": "beleuchtung", "schluessel": "licht_halogen", "auspraegung": "komplett ausgefallen / Gehaeuse, Halogen",
     "verfahren": "Scheinwerfer/Leuchte tauschen (Halogen)", "min": 120, "max": 350},
    {"typ": "beleuchtung", "schluessel": "licht_xenon", "auspraegung": "komplett ausgefallen / Gehaeuse, Xenon",
     "verfahren": "Xenon-Scheinwerfer tauschen", "min": 300, "max": 900},
    {"typ": "beleuchtung", "schluessel": "licht_led", "auspraegung": "komplett ausgefallen / Gehaeuse, LED/Matrix",
     "verfahren": "LED-Scheinwerfer tauschen", "min": 500, "max": 1800},
    {"typ": "beleuchtung", "schluessel": "licht_rueck", "auspraegung": "Rueckleuchte / Blinker / Nebelleuchte",
     "verfahren": "Leuchte tauschen", "min": 60, "max": 300},
    {"typ": "felge", "schluessel": "felge_kosmetisch", "auspraegung": "Bordsteinschaden, kosmetisch",
     "verfahren": "Felgenaufbereitung", "min": 80, "max": 180},
    {"typ": "unfall_nicht_repariert", "schluessel": "unfall_offen", "auspraegung": "nicht repariert, jeder Umfang",
     "verfahren": "Karosserie — nur mit Werkstattdiagnose/Gutachten", "min": 500, "max": 5000, "manuell": True},
    {"typ": "unfall_repariert", "schluessel": "unfall_repariert_beleg", "auspraegung": "fachgerecht repariert, Beleg vorhanden",
     "verfahren": "kein Reparaturbedarf, merkantile Wertminderung", "min": 150, "max": 800},
    {"typ": "unfall_repariert", "schluessel": "unfall_repariert_ohne", "auspraegung": "repariert ohne Beleg / sichtbare Maengel",
     "verfahren": "Wertminderung + Nacharbeit moeglich", "min": 300, "max": 1500},
    # Abweichungen ohne Schaden
    {"typ": "keys", "schluessel": "keys_fehlt", "auspraegung": "ein Schluessel fehlt (Funkschluessel)",
     "verfahren": "Ersatz + Anlernen (Marke entscheidet)", "min": 150, "max": 450},
    {"typ": "tires", "schluessel": "tires_schlecht", "auspraegung": "ein Satz deutlich schlechter / abgefahren",
     "verfahren": "Reifensatz (Klasse entscheidet)", "min": 250, "max": 900},
    {"typ": "tires", "schluessel": "tires_zweitsatz", "auspraegung": "zweiter Radsatz fehlt",
     "verfahren": "Ersatz Radsatz", "min": 300, "max": 1500},
    {"typ": "documents", "schluessel": "doc_brief", "auspraegung": "Zulassungsbescheinigung Teil II fehlt",
     "verfahren": "Ersatz beim Amt + Risiko", "min": 100, "max": 300, "manuell": True},
    {"typ": "documents", "schluessel": "doc_service", "auspraegung": "Servicebuch / HU-Bericht fehlt (vereinbart)",
     "verfahren": "Nachweisluecke, Wertminderung", "min": 50, "max": 300},
    {"typ": "documents", "schluessel": "doc_organisatorisch", "auspraegung": "Bedienungsanleitung / Zubehoer fehlt",
     "verfahren": "organisatorisch", "min": 0, "max": 40},
    {"typ": "hu", "schluessel": "hu_faellig", "auspraegung": "HU abgelaufen oder frueher faellig als vereinbart",
     "verfahren": "HU + moegliche Maengel", "min": 120, "max": 400},
    {"typ": "mileage", "schluessel": "km", "auspraegung": "je 1.000 km ueber Vertrag (Richtwert, fahrzeugabhaengig)",
     "verfahren": "Wertminderung", "min": 20, "max": 60},
    {"typ": "previous_owners", "schluessel": "halter", "auspraegung": "je zusaetzlicher Halter",
     "verfahren": "Wertminderung (jung/hochpreisig staerker)", "min": 80, "max": 300},
    {"typ": "equipment_missing", "schluessel": "ausstattung_fehlt", "auspraegung": "Komfort/Assistenz fehlt (z. B. Kamera, Navi, AHK)",
     "verfahren": "Nachruestung oder Wertminderung", "min": 150, "max": 800},
    {"typ": "equipment_defect", "schluessel": "ausstattung_defekt", "auspraegung": "vorhanden, aber defekt",
     "verfahren": "Reparatur (bauteilabhaengig)", "min": 100, "max": 900},
    {"typ": "warning_light", "schluessel": "warnleuchte", "auspraegung": "Motor/Getriebe/Airbag/ABS",
     "verfahren": "nur mit Diagnose — hohes Preisrisiko", "min": 0, "max": 0, "manuell": True},
    {"typ": "accident_history", "schluessel": "unfallfrei_abweichung", "auspraegung": "Unfallfreiheit weicht ab",
     "verfahren": "wesentliche Vertragsabweichung — manuelle Entscheidung", "min": 0, "max": 0, "manuell": True},
]

_JE_SCHLUESSEL = {b["schluessel"]: b for b in BASIS}


def zeile(schluessel: str) -> Optional[Dict[str, Any]]:
    return _JE_SCHLUESSEL.get(schluessel)


def basis_als_text() -> str:
    """Fuer den System-Prompt: kompakte Tabelle unserer Ausgangswerte."""
    zeilen = ["Ausgangswerte AutoSchnell (EUR inkl. MwSt., Deutschland 2026; gelten nur, wenn keine "
              "repair_reference/Marktpreise mitgeliefert sind; immer auf Fahrzeugwert/Alter/Klasse anpassen):"]
    for b in BASIS:
        preis = ("manuelle Entscheidung, keine Zahl" if b.get("manuell") and b["max"] == 0
                 else f"{b['min']}-{b['max']}")
        zeilen.append(f"- {b['typ']} / {b['auspraegung']}: {b['verfahren']}: {preis}"
                      + (" (manual_review_required=true)" if b.get("manuell") else ""))
    return "\n".join(zeilen)


# ------------------------------------------------ Zuordnung aus dem Formular
def _t(sd: Dict[str, Any], k: str) -> str:
    return str((sd or {}).get(k) or "").strip().lower()


def _hat(text: str, *woerter: str) -> bool:
    return any(w in text for w in woerter)


def zuordnen(type_key: str, severity_data: Optional[Dict[str, Any]], zone: str = "") -> Tuple[Optional[Dict[str, Any]], bool]:
    """(Zeile, annahme) — annahme=True, wenn eine Angabe fehlt/"unbekannt"
    war und die vorsichtige (teurere) Auspraegung gewaehlt wurde."""
    sd = severity_data or {}
    typ = str(type_key or "").lower()
    z = str(zone or "").lower()
    annahme = False

    def unbek(k: str) -> bool:
        return _t(sd, k) in ("", "unbekannt")

    if typ == "delle":
        groesse, lack, lage = _t(sd, "groesse"), _t(sd, "lack"), _t(sd, "lage")
        if unbek("lack") or unbek("groesse"):
            annahme = True
        if lack == "ja" or _hat(groesse, "über 10", "ueber 10"):
            return zeile("delle_kante" if _hat(lage, "kante", "sicke") else "delle_lack"), annahme
        if unbek("lack"):
            return zeile("delle_lack"), True
        if _hat(groesse, "5–10", "5-10"):
            return zeile("delle_mittel"), annahme
        if unbek("groesse"):
            return zeile("delle_mittel"), True
        return zeile("delle_klein"), annahme
    if typ == "kratzer":
        laenge, tiefe, anzahl = _t(sd, "laenge"), _t(sd, "tiefe"), _t(sd, "anzahl")
        if unbek("tiefe") or unbek("laenge"):
            annahme = True
        if _hat(anzahl, "mehrere"):
            return zeile("kratzer_mehrere"), annahme
        stoss = _hat(z, "stoß", "stoss")
        if _hat(tiefe, "blech") or _hat(laenge, "über 30", "ueber 30"):
            return zeile("kratzer_stossfaenger" if stoss else "kratzer_tief"), annahme
        if _hat(tiefe, "grundierung", "tief") or _hat(laenge, "15–30", "15-30") or unbek("tiefe"):
            return zeile("kratzer_stossfaenger" if stoss else "kratzer_grundierung"), annahme
        return zeile("kratzer_polierbar"), annahme
    if typ == "rost":
        umfang, groesse, stelle = _t(sd, "umfang"), _t(sd, "groesse"), _t(sd, "stelle")
        if _hat(stelle, "tragend") or _hat(z, "schweller", "träger", "traeger", "achse", "rahmen", "längsträger"):
            return zeile("rost_tragend"), unbek("umfang")
        if _hat(umfang, "durch"):
            return zeile("rost_durch"), False
        if unbek("umfang"):
            return zeile("rost_blasen_mittel"), True
        if _hat(umfang, "blasen"):
            if _hat(groesse, "über 15", "ueber 15"):
                return zeile("rost_blasen_gross"), False
            if _hat(groesse, "5–15", "5-15"):
                return zeile("rost_blasen_mittel"), False
            return zeile("rost_blasen_klein"), unbek("groesse")
        return zeile("rost_oberflaechlich"), unbek("groesse")
    if typ == "hagelschaden":
        umfang, lack = _t(sd, "umfang"), _t(sd, "lack")
        if lack == "ja":
            return zeile("hagel_lack"), False
        if _hat(umfang, "ganz"):
            return zeile("hagel_ganz"), False
        if _hat(umfang, "sehr viele", "über 30", "ueber 30", "mehrere bauteile"):
            return zeile("hagel_mehrere_bauteile"), unbek("lack")
        if _hat(umfang, "viele", "10-30", "10–30"):
            return zeile("hagel_viele_bauteil"), unbek("lack")
        if unbek("umfang"):
            return zeile("hagel_viele_bauteil"), True
        return zeile("hagel_wenige"), unbek("lack")
    if typ == "steinschlag":
        wo, umfang, tiefe = _t(sd, "wo"), _t(sd, "umfang"), _t(sd, "tiefe")
        if _hat(wo, "windschutz") or (not wo and _hat(z, "scheibe")):
            if _hat(umfang, "riss", "flächig", "flaechig") or _hat(tiefe, "über 2", "ueber 2", "sichtfeld"):
                return zeile("steinschlag_scheibe_tausch"), False
            return zeile("steinschlag_scheibe_reparabel"), unbek("umfang")
        if _hat(wo, "andere scheibe"):
            return zeile("steinschlag_scheibe_andere"), False
        if _hat(umfang, "flächig", "flaechig") or (_hat(umfang, "mehrere") and _hat(tiefe, "grundierung", "blech")):
            return zeile("steinschlag_haube_flaechig"), False
        if _hat(umfang, "mehrere"):
            return zeile("steinschlag_lack_mehrere"), unbek("tiefe")
        if unbek("umfang"):
            return zeile("steinschlag_lack_mehrere"), True
        return zeile("steinschlag_lack_einzeln"), unbek("tiefe")
    if typ == "beleuchtung":
        welches, funktion, technik = _t(sd, "welches"), _t(sd, "funktion"), _t(sd, "technik")
        if _hat(welches, "rück", "rueck", "blinker", "nebel", "andere"):
            return zeile("licht_rueck"), unbek("funktion")
        if _hat(funktion, "eingeschränkt", "eingeschraenkt", "leuchtmittel"):
            return zeile("licht_leuchtmittel"), unbek("technik")
        if _hat(technik, "led", "matrix", "laser"):
            return zeile("licht_led"), unbek("funktion")
        if _hat(technik, "xenon"):
            return zeile("licht_xenon"), unbek("funktion")
        if _hat(technik, "halogen"):
            return zeile("licht_halogen"), unbek("funktion")
        return zeile("licht_xenon"), True          # Technik unbekannt: vorsichtig
    if typ == "unfall_nicht_repariert":
        return zeile("unfall_offen"), False
    if typ == "unfall_repariert":
        nachweis, qualitaet = _t(sd, "nachweis"), _t(sd, "qualitaet")
        if _hat(nachweis, "rechnung", "beleg vorhanden") and not _hat(qualitaet, "mängel", "maengel"):
            return zeile("unfall_repariert_beleg"), False
        return zeile("unfall_repariert_ohne"), unbek("nachweis")
    return None, False


def referenz(type_key: str, severity_data: Optional[Dict[str, Any]], zone: str = "") -> Optional[Dict[str, Any]]:
    """Reparaturreferenz fuer das KI-Paket aus der Startwert-Tabelle."""
    z, annahme = zuordnen(type_key, severity_data, zone)
    if not z:
        return None
    return {"key": z["schluessel"], "method": z["verfahren"], "low": z["min"],
            "median": round((z["min"] + z["max"]) / 2), "high": z["max"],
            "manual_review": bool(z.get("manuell")), "source": "AutoSchnell-Startwerte",
            "assumption_made": annahme}


# ------------------------------------------------ deterministische Prioritaet
ROT = {"accident_history", "warning_light", "technical", "damage_worse"}
ORANGE = {"damage", "keys", "tires", "equipment_missing", "equipment_defect", "hu", "documents"}


def prioritaet(kategorie: str, *, betrag: Optional[float] = None,
               kaufpreis: Optional[float] = None, manuell: bool = False) -> str:
    """rot = Handlungsbedarf, orange = preisrelevant, gelb = geringer Einfluss.
    Ein grosser Betrag (>= 5 % des Kaufpreises oder >= 500 EUR) hebt auf rot."""
    if manuell or kategorie in ROT:
        return "rot"
    if betrag is not None and betrag <= 0:
        return "gelb"                # 0 EUR (z. B. nicht vereinbarte Unterlage) ist nie "preisrelevant"
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
