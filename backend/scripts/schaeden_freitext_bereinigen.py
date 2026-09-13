# -*- coding: utf-8 -*-
"""Freitext-Schaeden aus den dauerhaften Auto-Daten entfernen
(Go-Live-Audit 09/2026: AUTO_DATEN_SCHAEDEN_FREITEXT ist jetzt standardmaessig
aus; Altbestand kann noch Freitext mit Personenbezug tragen).

    python -X utf8 scripts/schaeden_freitext_bereinigen.py            # Trockenlauf
    python -X utf8 scripts/schaeden_freitext_bereinigen.py --anwenden # bereinigen

`admin_vehicle_data.damages` ist eine Liste von Strings. Aus der Schaden-
Skizze (frontend DamageSelector) entstehen Zeilen der Form
"• <Schadensart>: <Bauteil>, <Bauteil>", die auto_daten.schaeden_bereinigen
zu EINEM whitespace-normalisierten String zusammenzieht. Alles, was nicht
ausschliesslich aus den bekannten Schadensarten und Bauteil-Bezeichnungen
(bzw. der festen Vorbelegung des Hinweisfelds) besteht, gilt als Freitext.

Trockenlauf: zaehlt Datensaetze mit Freitext. --anwenden: behaelt nur die
bekannten Bezeichnungen (normalisiert "Schadensart: Bauteil, ..."), setzt
`damages_bereinigt_am` und `damages_freitext_entfernt` (Anzahl entfernter
Fragmente). Datensaetze mit `damages_redacted` sind bereits leer.
"""
import argparse
import os
import re
from datetime import datetime, timezone
from typing import List, Optional, Tuple

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://127.0.0.1:27017")
DB_NAME = os.environ.get("DB_NAME", "autoschnell")
COLLECTION = "admin_vehicle_data"

# Schadensarten der Skizze (frontend/src/components/DamageSelector.jsx, DAMAGE_TYPES)
SCHADENSARTEN = (
    "Unfallschaden repariert", "Unfallschaden NICHT repariert", "Hagelschaden",
    "Steinschlag", "Delle", "Kratzer", "Rost", "Beleuchtung defekt",
)
# Klickpunkte aller fuenf Ansichten (DamageSelector.jsx, DOTS)
BAUTEILE = (
    "A-Säule links", "A-Säule rechts", "Auspuff links", "Auspuff rechts",
    "B-Säule links", "B-Säule rechts", "C-Säule links", "C-Säule rechts",
    "Dach", "Heckklappe", "Heckscheibe",
    "Hinterrad / Felge links", "Hinterrad / Felge rechts",
    "Kennzeichen hinten", "Kennzeichenhalterung",
    "Kotflügel hinten links", "Kotflügel hinten rechts",
    "Kotflügel vorne links", "Kotflügel vorne rechts", "Kühlergrill",
    "Linker Außenspiegel", "Linker Hauptscheinwerfer", "Linker Nebelscheinwerfer",
    "Linkes Hinterrad / Felge", "Linkes Rücklicht", "Linkes Vorderrad / Reifen",
    "Lufteinlass links", "Lufteinlass rechts", "Marken-Emblem", "Motorhaube",
    "Rechter Außenspiegel", "Rechter Hauptscheinwerfer", "Rechter Nebelscheinwerfer",
    "Rechtes Hinterrad / Felge", "Rechtes Rücklicht", "Rechtes Vorderrad / Reifen",
    "Schweller links", "Schweller rechts",
    "Seitenscheibe hinten links", "Seitenscheibe hinten rechts",
    "Stoßstange hinten", "Stoßstange vorne",
    "Tür hinten links", "Tür hinten rechts", "Tür vorne links", "Tür vorne rechts",
    "Vorderrad / Felge links", "Vorderrad / Felge rechts", "Windschutzscheibe",
)
# Feste Vorbelegung des Hinweisfelds im Vertragsdialog (kein Nutzer-Freitext)
BEKANNTE_HINWEISE = ("Motorschaden / Unfallschaden vorhanden",)

_BEKANNT = set(SCHADENSARTEN) | set(BAUTEILE) | set(BEKANNTE_HINWEISE)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def fragment_pruefen(frag: str) -> Optional[str]:
    """Normalisiertes Fragment, wenn es NUR aus bekannten Bezeichnungen
    besteht; "" fuer leere Fragmente; None = Freitext."""
    frag = _norm(frag.strip(" -•;:"))
    if not frag:
        return ""
    if frag in _BEKANNT:
        return frag
    art, sep, rest = frag.partition(":")
    art = _norm(art)
    if sep and art in SCHADENSARTEN:
        teile = [t for t in (_norm(t) for t in rest.split(",")) if t]
        if teile and all(t in BAUTEILE for t in teile):
            return f"{art}: {', '.join(teile)}"
    return None


def eintrag_bereinigen(text) -> Tuple[List[str], int]:
    """(behaltene Fragmente, Anzahl entfernter Freitext-Fragmente)."""
    behalten: List[str] = []
    entfernt = 0
    for frag in re.split(r"[•\n;]+", str(text or "")):
        r = fragment_pruefen(frag)
        if r is None:
            entfernt += 1
        elif r and r not in behalten:
            behalten.append(r)
    return behalten, entfernt


def datensatz_bereinigen(damages) -> Tuple[List[str], int]:
    """Alle Eintraege eines Datensatzes; Rueckgabe wie eintrag_bereinigen."""
    neu: List[str] = []
    entfernt = 0
    for eintrag in damages or []:
        behalten, n = eintrag_bereinigen(eintrag)
        entfernt += n
        for b in behalten:
            if b not in neu:
                neu.append(b)
    return neu, entfernt


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--anwenden", action="store_true",
                    help="Freitext wirklich entfernen (Standard: nur zaehlen)")
    args = ap.parse_args()
    from pymongo import MongoClient
    db = MongoClient(MONGO_URL, serverSelectionTimeoutMS=10000)[DB_NAME]
    gesamt = betroffen = fragmente = bereinigt = 0
    for d in db[COLLECTION].find(
            {"damages": {"$exists": True, "$ne": []},
             "damages_redacted": {"$ne": True}},
            {"_id": 0, "id": 1, "damages": 1}):
        gesamt += 1
        neu, entfernt = datensatz_bereinigen(d.get("damages"))
        if not entfernt and neu == list(d.get("damages") or []):
            continue
        betroffen += 1
        fragmente += entfernt
        if args.anwenden:
            db[COLLECTION].update_one(
                {"id": d["id"]},
                {"$set": {"damages": neu,
                          "damages_bereinigt_am": datetime.now(timezone.utc).isoformat(),
                          "damages_freitext_entfernt": entfernt}})
            bereinigt += 1
    print(f"Datenbank: {DB_NAME}   Datensaetze mit Schaeden: {gesamt}")
    print(f"Datensaetze mit Freitext/nicht normalisierten Eintraegen: {betroffen} "
          f"({fragmente} Freitext-Fragmente)")
    if args.anwenden:
        print(f"Bereinigt: {bereinigt} Datensaetze (damages_bereinigt_am gesetzt).")
    elif betroffen:
        print("Trockenlauf — nichts geaendert. Bereinigen mit --anwenden.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
