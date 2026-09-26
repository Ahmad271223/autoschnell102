# -*- coding: utf-8 -*-
"""Abgleich "neuer Schaden vor Ort" gegen "bekannter Schaden" — je Schadensart
(Review 26.09.2026, Nr. 68-75 und 84/85).

Vorher gab es EINE Stufentabelle fuer alle Arten (Umfang von Rost, Hagel und
Unfall in einer Liste, Beleuchtung als lineare Reihe, Steinschlag "Riss"
fehlte, ein alter Schaden ohne Auspraegung galt als "bekannt"). Jetzt:

  STUFEN_JE_ART   Art -> Feld -> geordnete Liste (links harmlos, rechts schlimm),
                  Werte GENAU wie in frontend/src/lib/kiSchaden.js
                  (ein Test liest die Datei und prueft jede Option)
  MERKMALE_JE_ART Art -> Feld -> ungeordnete Werte (Technik des Lichts, Ort
                  des Steinschlags ...): ein Unterschied ist keine
                  Verschlechterung, aber ein Hinweis auf einen ANDEREN Schaden
  SICHERHEIT      Felder, bei denen "unbekannt" vor Ort nie als "bekannt"
                  durchgeht (fahrbereit, airbag, Rahmen im Umfang, tragendes Teil)

Ergebnis je neuem Schaden: neu | bekannt | schlimmer (+ Feld) | moeglich.
  bekannt   gleiche Art, gleiches Bauteil/Position/Seite, keine Auspraegung hoeher
  schlimmer dito, mindestens ein Feld hoeher — die Verschlechterung wird bewertet
  moeglich  gleiche Art und gleiches Bauteil, aber Position/Seite unklar; ODER
            alter Schaden ohne Auspraegung; ODER "unbekannt" in einem
            Sicherheitsfeld; ODER ein ungeordnetes Merkmal weicht ab
            -> manuelle Pruefung, nie "bekannt"
  neu       kein passender bekannter Schaden (anderes Bauteil, andere
            Position/Seite oder andere Art)
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from ai.bekannte_schaeden import ascii_norm, zone_merkmale, zonen_gleich

STUFEN_JE_ART: Dict[str, Dict[str, List[str]]] = {
    "delle": {
        "groesse": ["bis 2 cm", "2–5 cm", "5–10 cm", "über 10 cm"],
        "lack": ["nein", "ja"],
        "lage": ["Fläche", "Kante/Sicke"],
    },
    "kratzer": {
        "laenge": ["bis 5 cm", "5–15 cm", "15–30 cm", "über 30 cm"],
        "tiefe": ["oberflächlich", "bis Grundierung", "bis Blech"],
        "anzahl": ["einzeln", "mehrere"],
    },
    "rost": {
        "umfang": ["oberflächlich", "Blasen", "durchgerostet"],
        "groesse": ["bis 5 cm", "5–15 cm", "über 15 cm"],
        "stelle": ["Fläche", "Kante/Falz", "tragendes Teil"],
    },
    "hagelschaden": {
        "umfang": ["wenige (unter 10)", "viele (10–30)", "sehr viele (über 30)", "ganzes Fahrzeug"],
        "dellengroesse": ["klein (bis 1 cm)", "mittel (1–3 cm)", "groß"],
        "lack": ["nein", "ja"],
    },
    "steinschlag": {
        "umfang": ["einzeln", "mehrere", "Riss/flächig"],
        "tiefe": ["nur Deckschicht", "bis Grundierung/Blech"],
    },
    "beleuchtung": {
        # "Gehäuse beschädigt" ist KEINE Stufe der Funktion, sondern ein eigenes
        # Merkmal (gehaeuse); die Umsetzung macht `_sd_normalisieren`.
        "funktion": ["eingeschränkt", "komplett ausgefallen"],
        "gehaeuse": ["nein", "ja"],
    },
    "unfall_repariert": {
        "nachweis": ["Rechnung vorhanden", "kein Beleg"],
        "umfang": ["Blech", "Blech + Rahmen"],
        "qualitaet": ["fachgerecht", "sichtbare Mängel"],
    },
    "unfall_nicht_repariert": {
        "umfang": ["Blech", "Blech + Rahmen"],
        "fahrbereit": ["ja", "eingeschränkt", "nein"],
        "airbag": ["nicht ausgelöst", "ausgelöst"],
    },
    "technik": {
        "fahrbereit": ["ja", "eingeschränkt", "nein"],
        "warnleuchte": ["keine", "leuchtet"],
        "umfang": ["Kleinteil/Einstellung", "Bauteil tauschen", "Instandsetzung/Überholung", "Austauschaggregat"],
    },
}

# Ungeordnete Merkmale: None = freier Wert (Bereich, Betrag)
MERKMALE_JE_ART: Dict[str, Dict[str, Optional[List[str]]]] = {
    "steinschlag": {"wo": ["Lack", "Windschutzscheibe", "andere Scheibe"]},
    "beleuchtung": {"welches": ["Scheinwerfer", "Rückleuchte", "Blinker/Nebel", "andere"],
                    "technik": ["Halogen", "Xenon", "LED"]},
    # status/kva: eine bestaetigte Diagnose ist derselbe Mangel, nur genauer — kein Hinweis auf einen anderen
    "technik": {"bereich": None, "status": None, "kva": None, "kva_eur": None},
}

# Werte, die als Stufe eines ANDEREN Feldes gelten (Frontend-Option -> Serverfeld/-wert)
_UMGESETZT = {("beleuchtung", "funktion", "Gehäuse beschädigt"): ("gehaeuse", "ja")}

# Sicherheitsrelevante Felder je Art: "unbekannt" vor Ort -> "moeglich"
SICHERHEIT: Dict[str, set] = {
    "unfall_nicht_repariert": {"umfang", "fahrbereit", "airbag"},   # umfang traegt den Rahmen
    "unfall_repariert": {"umfang"},
    "technik": {"fahrbereit"},
    "rost": {"stelle"},                                            # tragendes Teil
}
UNBEKANNT = "unbekannt"


def _norm(w: Any) -> str:
    return ascii_norm(str(w or "").replace("–", "-").replace("—", "-"))


def stufe(art: str, feld: str, wert: Any) -> Optional[int]:
    """Index in der geordneten Liste; None bei leer/unbekannt/unbekannter Wert."""
    w = _norm(wert)
    if not w or w == UNBEKANNT:
        return None
    for i, s in enumerate((STUFEN_JE_ART.get(art) or {}).get(feld) or []):
        if _norm(s) == w:
            return i
    return None


def option_bekannt(art: str, feld: str, option: Any) -> bool:
    """Kennt der Server diese Frontend-Option? (Test gegen kiSchaden.js)"""
    if _norm(option) == UNBEKANNT:
        return True
    if (art, feld, str(option)) in _UMGESETZT:
        return True
    if stufe(art, feld, option) is not None:
        return True
    m = (MERKMALE_JE_ART.get(art) or {})
    if feld in m:
        return m[feld] is None or any(_norm(x) == _norm(option) for x in m[feld])
    return False


def _sd_normalisieren(art: str, sd: Optional[dict]) -> Dict[str, Any]:
    """Frontend-Werte in Serverfelder: 'Gehäuse beschädigt' -> gehaeuse=ja,
    funktion bleibt offen; eine Funktionsangabe setzt gehaeuse=nein."""
    raus: Dict[str, Any] = {str(k): v for k, v in (sd or {}).items()} if isinstance(sd, dict) else {}
    for (a, feld, wert), (ziel, zielwert) in _UMGESETZT.items():
        if a != art or feld not in raus:
            continue
        if _norm(raus.get(feld)) == _norm(wert):
            raus[ziel] = zielwert
            raus[feld] = ""
        elif stufe(art, feld, raus.get(feld)) is not None:
            raus.setdefault(ziel, "nein")
    return raus


def stufen_vergleich(art: str, alt_sd: Optional[dict], neu_sd: Optional[dict]) -> Tuple[str, Optional[str]]:
    """('bekannt' | 'schlimmer' | 'moeglich', Feld der Verschlechterung).
    Alter Schaden ohne Auspraegung + neuer mit Daten -> moeglich (nie bekannt)."""
    alt = _sd_normalisieren(art, alt_sd)
    neu = _sd_normalisieren(art, neu_sd)
    alt_gefuellt = {k for k, v in alt.items() if str(v or "").strip()}
    neu_gefuellt = {k for k, v in neu.items() if str(v or "").strip()}
    if not alt_gefuellt and neu_gefuellt:
        return "moeglich", None
    moeglich = False
    stufen = STUFEN_JE_ART.get(art) or {}
    for feld in stufen:
        a, n = stufe(art, feld, alt.get(feld)), stufe(art, feld, neu.get(feld))
        if n is not None and a is not None and n > a:
            return "schlimmer", feld
    for feld in stufen:
        a, n = stufe(art, feld, alt.get(feld)), stufe(art, feld, neu.get(feld))
        n_roh = _norm(neu.get(feld))
        if n is None:
            if n_roh == UNBEKANNT and feld in (SICHERHEIT.get(art) or set()):
                moeglich = True
            continue
        if a is None and n > 0:
            moeglich = True              # alt unbekannt/leer, vor Ort mehr als die harmloseste Stufe
    for feld, werte in (MERKMALE_JE_ART.get(art) or {}).items():
        a, n = _norm(alt.get(feld)), _norm(neu.get(feld))
        if a and n and a != UNBEKANNT and n != UNBEKANNT and a != n and werte is not None:
            moeglich = True              # z. B. Steinschlag Lack vs. Scheibe: anderer Schaden?
    return ("moeglich" if moeglich else "bekannt"), None


def _technik_bereich(d: dict) -> str:
    from ai import preisbasis
    sd = d.get("severity_data") if isinstance(d.get("severity_data"), dict) else {}
    return preisbasis._bereich_schluessel(str(sd.get("bereich") or "")) \
        or preisbasis._bereich_schluessel(str(d.get("zone") or d.get("note") or ""))


def abgleich(bekannt: List[dict], neu: dict) -> Tuple[str, Optional[dict], Optional[str]]:
    """(status, bekannter Schaden, Feld der Verschlechterung) fuer einen neuen
    Schaden. Reihenfolge: exakte Stelle (bekannt/schlimmer/moeglich) vor
    unklarer Stelle (moeglich); andere Position/Seite ist NICHT moeglich."""
    art = str(neu.get("type_key") or neu.get("type") or "").strip().lower()
    zn = zone_merkmale(neu.get("zone") or neu.get("part_label") or "")
    kandidat: Optional[dict] = None
    for b in bekannt or []:
        if str(b.get("type_key") or b.get("type") or "").strip().lower() != art:
            continue
        zb = zone_merkmale(b.get("zone") or b.get("part_label") or "")
        gleich = zonen_gleich(zn, zb)
        if art == "technik":
            # Technik hat kein Bauteil, sondern einen Bereich (Motor, Klima ...):
            # gleicher Bereich = dieselbe Stelle — auch bei Freitext
            # ("Klimaanlage kuehlt nicht" ~ Bereich Klima/Heizung).
            ka, kb = _technik_bereich(neu), _technik_bereich(b)
            if ka and kb:
                gleich = ka == kb
        if gleich is False:
            continue
        if gleich is None:
            kandidat = kandidat or b
            continue
        status, feld = stufen_vergleich(art, b.get("severity_data"), neu.get("severity_data"))
        return status, b, feld
    if kandidat is not None:
        return "moeglich", kandidat, None
    return "neu", None, None
