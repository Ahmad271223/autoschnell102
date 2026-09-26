# -*- coding: utf-8 -*-
"""Bekannte Schaeden — EINE Wahrheit fuer Fahrer-App und KI (Review 26.09.2026,
Nr. 81/82/83/111/116).

Vorher galt `contract.damages OR vehicle.damages`: sobald der Vertrag einen
Schaden kannte, fiel das Inserat ganz weg; Freitext-Schaeden (Strings) und
die bekannten Maengel des Inserats (known_defects) kamen im deterministischen
Abgleich nie an. Hier werden alle vier Quellen zusammengefuehrt:

  Vertrag (damages, dict)            quelle "vertrag"
  Vertrag Freitext (damages-Strings, damages_text, vehicle_damage_note)
                                     quelle "vertrag_freitext"
  Inserat (vehicle.damages, dict)    quelle "inserat"
  Inserat Freitext (Strings)         quelle "inserat_freitext"
  Inserat known_defects              quelle "inserat" (technik oder sonstiges)

Freitext wird in {type_key, zone, note} umgesetzt (Art per Stichwort, sonst
"sonstiges"); Doppelte (gleiche Art + gleiches normalisiertes Bauteil/
Position/Seite) fallen weg — der Vertrag geht vor.

Dazu die Zonen-Normalisierung (Nr. 84/85): "rechter vorderer Kotfluegel",
"Kotfluegel vorne rechts" und "Kotfluegel VR" meinen dasselbe Bauteil
(kotfluegel / vorne / rechts) — der Abgleich vergleicht Bauteil, Position und
Seite statt Woerter zu zaehlen.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

# Bauteil-Schluessel -> Erkennungsmuster (auf ASCII-normalisierten Woertern;
# ein Wort passt, wenn es mit dem Muster BEGINNT oder es ENTHAELT).
BAUTEILE: Dict[str, tuple] = {
    "kotfluegel": ("kotfluegel", "fender", "radlauf"),
    "stossfaenger": ("stossfaenger", "stossstange", "frontschuerze", "heckschuerze", "schuerze"),
    "tuer": ("tuer", "fahrertuer", "beifahrertuer"),
    "motorhaube": ("motorhaube", "fronthaube", "haube"),
    "heckklappe": ("heckklappe", "heckdeckel", "kofferraumdeckel", "kofferraumklappe", "kofferraum"),
    "dach": ("dach", "panoramadach", "schiebedach"),
    "schweller": ("schweller",),
    "spiegel": ("aussenspiegel", "spiegel"),
    "nebelscheinwerfer": ("nebelscheinwerfer", "nebellicht", "nebelleuchte"),
    "scheinwerfer": ("hauptscheinwerfer", "scheinwerfer", "frontlicht"),
    "ruecklicht": ("ruecklicht", "rueckleuchte", "heckleuchte", "hecklicht"),
    "windschutzscheibe": ("windschutzscheibe", "frontscheibe"),
    "heckscheibe": ("heckscheibe",),
    "seitenscheibe": ("seitenscheibe", "seitenfenster"),
    "rad": ("hinterrad", "vorderrad", "felge", "alufelge", "reifen", "rad"),
    "seitenwand": ("seitenwand", "seitenteil"),
    "saeule": ("saeule",),
    "kuehlergrill": ("kuehlergrill", "grill"),
    "auspuff": ("auspuff", "endrohr"),
    "kennzeichen": ("kennzeichenhalterung", "kennzeichen"),
    "emblem": ("emblem",),
    "lufteinlass": ("lufteinlass",),
    "unterboden": ("unterboden",),
    "innenraum": ("innenraum", "sitz", "polster", "armaturenbrett", "himmel"),
}
_POSITION = {"vorne": "vorne", "vorn": "vorne", "vorder": "vorne", "vorderer": "vorne", "vordere": "vorne",
             "vorderes": "vorne", "vorderrad": "vorne", "front": "vorne",
             "hinten": "hinten", "hinter": "hinten", "hinterer": "hinten", "hintere": "hinten", "hinteres": "hinten",
             "hinterrad": "hinten", "heck": "hinten"}
_SEITE = {"links": "links", "linke": "links", "linker": "links", "linken": "links", "linkes": "links", "li": "links",
          "fahrerseite": "links",
          "rechts": "rechts", "rechte": "rechts", "rechter": "rechts", "rechten": "rechts", "rechtes": "rechts",
          "re": "rechts", "beifahrerseite": "rechts"}
_KURZ = {"vl": ("vorne", "links"), "vr": ("vorne", "rechts"), "hl": ("hinten", "links"), "hr": ("hinten", "rechts")}

# Bauteile, bei denen die Lage im Namen steckt (kein Positions-/Seitenwort noetig)
_BAUTEIL_POSITION = {"motorhaube": "vorne", "heckklappe": "hinten", "windschutzscheibe": "vorne",
                     "heckscheibe": "hinten", "scheinwerfer": "vorne", "nebelscheinwerfer": "vorne",
                     "ruecklicht": "hinten", "kuehlergrill": "vorne"}

# Schadensart aus Freitext (Reihenfolge = Vorrang)
_ART_STICHWORT = (
    ("unfall_repariert", ("unfall repariert", "unfallschaden repariert", "instandgesetzt", "unfall behoben")),
    ("unfall_nicht_repariert", ("unfall", "crash", "aufprall", "kollision")),
    ("hagelschaden", ("hagel",)),
    ("steinschlag", ("steinschlag",)),
    ("rost", ("rost", "korrosion", "durchgerostet")),
    ("beleuchtung", ("scheinwerfer", "ruecklicht", "rueckleuchte", "blinker", "leuchte", "licht defekt")),
    ("delle", ("delle", "beule", "eingedrueckt", "eingedellt")),
    ("kratzer", ("kratzer", "schramme", "lackschaden", "lack ab", "verkratzt", "abgeschuerft", "schuerf")),
    ("technik", ("motor", "getriebe", "kupplung", "turbo", "klima", "bremse", "elektr", "batterie", "warnleuchte",
                 "fehlermeldung", "geraeusch", "ruckel", "oel", "undicht", "fensterheber", "defekt", "funktioniert nicht",
                 "abgas", "auspuff", "lenkung", "stossdaempfer", "fahrwerk", "sensor", "display", "navi")),
)
BEKANNTE_ARTEN = ("delle", "kratzer", "rost", "hagelschaden", "steinschlag", "beleuchtung", "unfall_repariert",
                  "unfall_nicht_repariert", "technik")

_STEUERZEICHEN = re.compile(r"[\x00-\x1f\x7f]+")


def ascii_norm(text: Any) -> str:
    """Kleinbuchstaben, Umlaute als ae/oe/ue/ss, alles andere als Leerzeichen."""
    s = str(text or "").lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        s = s.replace(a, b)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def freitext(text: Any, n: int = 300) -> str:
    """Nr. 130-132: Freitext fuer Paket und Prompt — Zeilenumbrueche und
    Steuerzeichen raus, Leerraum zusammen, auf n Zeichen gekuerzt."""
    s = _STEUERZEICHEN.sub(" ", str(text or ""))
    s = re.sub(r"\s+", " ", s).strip()
    return s[:n]


def _bauteil(wort: str) -> Optional[str]:
    for key, muster in BAUTEILE.items():
        for m in muster:
            if wort == m or wort.startswith(m) or (len(m) >= 5 and m in wort):
                return key
    return None


def zone_merkmale(zone: Any) -> Dict[str, Optional[str]]:
    """'rechter vorderer Kotfluegel' -> {bauteil: kotfluegel, position: vorne,
    seite: rechts, text: 'rechter vorderer kotfluegel'}. Unbekanntes Bauteil:
    bauteil None (dann zaehlt nur der normalisierte Text)."""
    text = ascii_norm(zone)
    bauteil = position = seite = None
    for w in text.split():
        if w in _KURZ:
            position, seite = _KURZ[w]
            continue
        if w in _POSITION:
            position = position or _POSITION[w]
            # "vorderrad"/"hinterrad" sind zugleich das Bauteil
        if w in _SEITE:
            seite = seite or _SEITE[w]
            continue
        if bauteil is None:
            b = _bauteil(w)
            if b:
                bauteil = b
                if w in ("fahrertuer",):
                    seite = seite or "links"
                if w in ("beifahrertuer",):
                    seite = seite or "rechts"
    if bauteil and position is None:
        position = _BAUTEIL_POSITION.get(bauteil)
    return {"bauteil": bauteil, "position": position, "seite": seite, "text": text}


def zonen_gleich(a: Dict[str, Optional[str]], b: Dict[str, Optional[str]]) -> Optional[bool]:
    """True = dasselbe Bauteil an derselben Stelle; False = verschiedene
    Stelle (Position ODER Seite widersprechen sich, oder anderes Bauteil);
    None = gleiches Bauteil, aber Position/Seite auf einer Seite unklar."""
    if not a.get("bauteil") or not b.get("bauteil"):
        return True if (a.get("text") and a.get("text") == b.get("text")) else False
    if a["bauteil"] != b["bauteil"]:
        return False
    unklar = False
    for k in ("position", "seite"):
        x, y = a.get(k), b.get(k)
        if x and y and x != y:
            return False
        if bool(x) != bool(y):
            unklar = True
    return None if unklar else True


def art_erkennen(text: Any) -> str:
    """Schadensart aus Freitext per Stichwort; sonst 'sonstiges'."""
    t = ascii_norm(text)
    if not t:
        return "sonstiges"
    for art, woerter in _ART_STICHWORT:
        for w in woerter:
            if ascii_norm(w) in t:
                return art
    return "sonstiges"


def _aus_freitext(text: Any, quelle: str, art_fest: Optional[str] = None) -> List[dict]:
    """Ein Freitext (ggf. mehrzeilig, mit ; oder , getrennt) -> Schaeden."""
    raus = []
    roh = str(text or "")
    teile = [t.strip(" -•*\t") for t in re.split(r"[\n;]+", roh)]
    for t in teile:
        t = freitext(t, 200)
        if len(t) < 3:
            continue
        art = art_fest or art_erkennen(t)
        # "Delle Tuer vorne links" -> Bauteil "Tuer vorne links" (die Fahrer-App
        # zeigt Art und Bauteil getrennt; das Wort der Art nicht doppelt)
        zone = t
        for wort in (_label(art), art):
            if len(t) > len(wort) + 3 and ascii_norm(t).startswith(ascii_norm(wort) + " "):
                zone = t[len(wort):].strip(" :-–,")
                break
        raus.append({"id": "", "type_key": art, "type_label": _label(art), "zone": zone or t, "note": t,
                     "severity_data": {}, "quelle": quelle})
    return raus


def _label(art: str) -> str:
    return {"delle": "Delle", "kratzer": "Kratzer", "rost": "Rost", "hagelschaden": "Hagelschaden",
            "steinschlag": "Steinschlag", "beleuchtung": "Beleuchtung", "unfall_repariert": "Unfall (repariert)",
            "unfall_nicht_repariert": "Unfall (nicht repariert)", "technik": "Technischer Mangel",
            "sonstiges": "Sonstiges"}.get(art, art)


def _aus_dict(d: dict, quelle: str) -> dict:
    sd = d.get("severity_data") if isinstance(d.get("severity_data"), dict) else {}
    art = str(d.get("type_key") or d.get("type") or "").strip().lower() or "sonstiges"
    return {"id": str(d.get("id") or ""), "type_key": art,
            "type_label": d.get("type_label") or d.get("label") or _label(art),
            "zone": freitext(d.get("zone") or d.get("part_label") or d.get("part") or "", 120),
            "view": d.get("view") or "", "note": freitext(d.get("note") or d.get("text") or "", 200),
            "severity_data": {str(k)[:40]: (str(v)[:60] if not isinstance(v, (int, float)) or isinstance(v, bool)
                                            else v) for k, v in sd.items()},
            "quelle": quelle}


def _schluessel(d: dict) -> tuple:
    m = zone_merkmale(d.get("zone"))
    if m["bauteil"]:
        return (d.get("type_key"), m["bauteil"], m["position"], m["seite"])
    return (d.get("type_key"), m["text"], None, None)


def zusammenfuehren(contract: Optional[dict], vehicle: Optional[dict]) -> List[dict]:
    """Alle bekannten Schaeden aus Vertrag und Inserat, strukturiert und
    dedupliziert (gleiche Art + gleiches Bauteil/Position/Seite; der erste —
    also der Vertrag — bleibt). Reihenfolge: Vertrag, Vertrag-Freitext,
    Inserat, Inserat-Freitext, bekannte Maengel des Inserats."""
    c = contract if isinstance(contract, dict) else {}
    v = vehicle if isinstance(vehicle, dict) else {}
    roh: List[dict] = []
    for d in (c.get("damages") or []):
        if isinstance(d, dict):
            roh.append(_aus_dict(d, "vertrag"))
        elif isinstance(d, str):
            roh.extend(_aus_freitext(d, "vertrag_freitext"))
    for feld in ("damages_text", "vehicle_damage_note"):
        if c.get(feld):
            roh.extend(_aus_freitext(c.get(feld), "vertrag_freitext"))
    for d in (v.get("damages") or []):
        if isinstance(d, dict):
            roh.append(_aus_dict(d, "inserat"))
        elif isinstance(d, str):
            roh.extend(_aus_freitext(d, "inserat_freitext"))
    for m in (v.get("known_defects") or [])[:40]:
        if str(m or "").strip():
            roh.extend(_aus_freitext(m, "inserat"))     # Art per Stichwort (meist technik), sonst sonstiges
    gesehen = set()
    raus: List[dict] = []
    for i, d in enumerate(roh):
        k = _schluessel(d)
        if k in gesehen:
            continue
        gesehen.add(k)
        if not d.get("id"):
            d["id"] = f"bekannt:{i + 1}"
        raus.append(d)
    return raus[:80]
