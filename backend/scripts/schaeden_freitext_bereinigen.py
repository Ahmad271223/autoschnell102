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

Trockenlauf: zaehlt Datensaetze mit Freitext und zeigt die nicht erkannten
Fragmente gruppiert (haeufigste zuerst) — so faellt auf, wenn eine
Bezeichnung nur in der Liste fehlt. --anwenden: behaelt nur die bekannten
Bezeichnungen (normalisiert "Schadensart: Bauteil, ..."), setzt
`damages_bereinigt_am` und `damages_freitext_entfernt` (Anzahl entfernter
Fragmente). Datensaetze mit `damages_redacted` sind bereits leer.

Pruefbericht 20.09.2026 (SK-14): beim Anwenden werden die entfernten
Fragmente je Datensatz in `schaeden_freitext_entfernt` gesichert (TTL-Index,
SICHERUNG_TAGE Tage — danach sind sie endgueltig weg) und ein
activity_logs-Eintrag (Skript, Host, Anzahl) haelt fest, wer wann bereinigt
hat.
"""
import argparse
import os
import re
import socket
import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import List, Optional, Tuple

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://127.0.0.1:27017")
DB_NAME = os.environ.get("DB_NAME", "autoschnell")
COLLECTION = "admin_vehicle_data"
SICHERUNG_COLLECTION = "schaeden_freitext_entfernt"     # SK-14
SICHERUNG_TAGE = 90                                       # SK-14: TTL

# Schadensarten der Skizze (frontend/src/components/DamageSelector.jsx, DAMAGE_TYPES)
SCHADENSARTEN = (
    "Unfallschaden repariert", "Unfallschaden NICHT repariert", "Hagelschaden",
    "Steinschlag", "Delle", "Kratzer", "Rost", "Beleuchtung defekt",
    "Technischer Mangel",                      # 25.09.2026 abends, ohne Skizze
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
    # Bereiche des Technischen Mangels (kiSchaden.js TECHNIK_BEREICHE)
    "Motor", "Getriebe/Kupplung", "Fahrwerk/Bremsen/Lenkung", "Elektrik/Elektronik", "Klima/Heizung",
    "Fensterheber/Verriegelung/Sitze", "Auspuff/Abgas", "Batterie/Start", "Innenraum",
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


def eintrag_zerlegen(text) -> Tuple[List[str], List[str]]:
    """(behaltene Fragmente, entfernte Freitext-Fragmente im Wortlaut) — SK-14."""
    behalten: List[str] = []
    entfernt: List[str] = []
    for frag in re.split(r"[•\n;]+", str(text or "")):
        r = fragment_pruefen(frag)
        if r is None:
            entfernt.append(_norm(frag.strip(" -•;:")))
        elif r and r not in behalten:
            behalten.append(r)
    return behalten, entfernt


def eintrag_bereinigen(text) -> Tuple[List[str], int]:
    """(behaltene Fragmente, Anzahl entfernter Freitext-Fragmente)."""
    behalten, entfernt = eintrag_zerlegen(text)
    return behalten, len(entfernt)


def datensatz_zerlegen(damages) -> Tuple[List[str], List[str]]:
    """Alle Eintraege eines Datensatzes; Rueckgabe wie eintrag_zerlegen."""
    neu: List[str] = []
    entfernt: List[str] = []
    for eintrag in damages or []:
        behalten, weg = eintrag_zerlegen(eintrag)
        entfernt.extend(weg)
        for b in behalten:
            if b not in neu:
                neu.append(b)
    return neu, entfernt


def datensatz_bereinigen(damages) -> Tuple[List[str], int]:
    """Alle Eintraege eines Datensatzes; Rueckgabe wie eintrag_bereinigen."""
    neu, entfernt = datensatz_zerlegen(damages)
    return neu, len(entfernt)


def _sicherung_vorbereiten(db) -> None:
    """SK-14: TTL-Index auf der Sicherungs-Sammlung (idempotent)."""
    db[SICHERUNG_COLLECTION].create_index(
        "entfernt_am", expireAfterSeconds=SICHERUNG_TAGE * 86400, name="sicherung_ttl")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--anwenden", action="store_true",
                    help="Freitext wirklich entfernen (Standard: nur zaehlen)")
    ap.add_argument("--zeige", type=int, default=30, metavar="N",
                    help="im Trockenlauf die N haeufigsten unbekannten Fragmente zeigen")
    args = ap.parse_args()
    from pymongo import MongoClient
    db = MongoClient(MONGO_URL, serverSelectionTimeoutMS=10000)[DB_NAME]
    jetzt = datetime.now(timezone.utc)
    host = socket.gethostname()
    if args.anwenden:
        _sicherung_vorbereiten(db)
    gesamt = betroffen = fragmente = bereinigt = 0
    unbekannt: Counter = Counter()
    for d in db[COLLECTION].find(
            {"damages": {"$exists": True, "$ne": []},
             "damages_redacted": {"$ne": True}},
            {"_id": 0, "id": 1, "damages": 1}):
        gesamt += 1
        neu, weg = datensatz_zerlegen(d.get("damages"))
        if not weg and neu == list(d.get("damages") or []):
            continue
        betroffen += 1
        fragmente += len(weg)
        unbekannt.update(weg)
        if args.anwenden:
            # SK-14: Wortlaut je Datensatz sichern (TTL), dann erst ersetzen
            if weg:
                db[SICHERUNG_COLLECTION].insert_one({
                    "id": str(uuid.uuid4()), "datensatz_id": d["id"],
                    "fragmente": weg, "entfernt_am": jetzt, "host": host,
                    "skript": "scripts/schaeden_freitext_bereinigen.py"})
            db[COLLECTION].update_one(
                {"id": d["id"]},
                {"$set": {"damages": neu,
                          "damages_bereinigt_am": jetzt.isoformat(),
                          "damages_freitext_entfernt": len(weg)}})
            bereinigt += 1
    print(f"Datenbank: {DB_NAME}   Datensaetze mit Schaeden: {gesamt}")
    print(f"Datensaetze mit Freitext/nicht normalisierten Eintraegen: {betroffen} "
          f"({fragmente} Freitext-Fragmente)")
    if unbekannt and not args.anwenden and args.zeige > 0:
        # SK-14: gruppiert zeigen — eine fehlende Bezeichnung faellt so auf
        print(f"\nNicht erkannte Fragmente (haeufigste {min(args.zeige, len(unbekannt))} "
              f"von {len(unbekannt)}):")
        for frag, n in unbekannt.most_common(args.zeige):
            print(f"  {n:>5} x  {frag[:100]}")
    if args.anwenden:
        if bereinigt:
            # SK-14: Audit-Eintrag (wer/wann/wie viele), ohne die Fragmente selbst
            db.activity_logs.insert_one({
                "id": str(uuid.uuid4()), "dealer_id": "", "user_id": "system",
                "action": "auto_daten.schaeden_freitext.bereinigt", "ref": "",
                "meta": {"quelle": "scripts/schaeden_freitext_bereinigen.py", "host": host,
                         "datensaetze": bereinigt, "fragmente": fragmente,
                         "sicherung": SICHERUNG_COLLECTION, "sicherung_tage": SICHERUNG_TAGE},
                "created_at": jetzt.isoformat(),
            })
        print(f"Bereinigt: {bereinigt} Datensaetze (damages_bereinigt_am gesetzt); "
              f"entfernte Fragmente {SICHERUNG_TAGE} Tage in '{SICHERUNG_COLLECTION}'.")
    elif betroffen:
        print("Trockenlauf — nichts geaendert. Bereinigen mit --anwenden.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
