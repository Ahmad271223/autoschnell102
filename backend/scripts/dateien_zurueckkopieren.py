# -*- coding: utf-8 -*-
"""Dateien aus dem Sicherungs-Bucket in den Datei-Speicher zurueckkopieren.

Gegenstueck zu BACKUP_DATEIEN=bucket (backup_mongo.py, 19.09.2026): Die
naechtliche Sicherung kopiert jede Datei Speicher-zu-Speicher in den
Sicherungs-Bucket (Praefix BACKUP_DATEIEN_PREFIX, Standard "dateien/").
Fehlt im Datei-Speicher etwas — ganzer Bucket weg, versehentlich geloescht,
neuer Anbieter — holt dieses Skript es von dort zurueck. Ohne Platte: die
Daten fliessen durch den Arbeitsspeicher.

Standard: nur Dateien, die im Datei-Speicher FEHLEN (nichts wird
ueberschrieben). Dateien im Papierkorb (Metadatum geloescht-am) bleiben
aussen vor, ausser mit --auch-geloeschte.

Stand eines bestimmten Backups (Nachpruefung 20.09.2026, Nr. 70): Der
Sicherungs-Bucket wird von jedem Lauf fortgeschrieben, er ist also KEIN
Zeitpunkt. Deshalb legt jedes Backup eine Objektliste an
(dateien-liste.json.gz im Backup-Ordner): welche Dateien in welcher
Fassung zu genau diesem Datenbankstand gehoerten. Mit --liste <Backup-
Ordner> vergleicht dieses Skript gegen diese Liste und meldet, was fehlt,
was sich seither geaendert hat und was zusaetzlich da ist.

Aufruf:
  python -X utf8 dateien_zurueckkopieren.py --dry-run          # nur zaehlen
  python -X utf8 dateien_zurueckkopieren.py --yes              # zurueckholen
  python -X utf8 dateien_zurueckkopieren.py --yes --praefix protocol/   # nur ein Ordner
  python -X utf8 dateien_zurueckkopieren.py --dry-run          --liste /backups/autoschnell-2026-09-20_0300      # gegen einen Stand pruefen
"""
import argparse
import gzip
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import backup_mongo as B  # noqa: E402
from s3_kompatibel import sse_optionen  # noqa: E402


def liste_lesen(backup_ordner: str) -> dict:
    """Objektliste eines Backups lesen (Nr. 70). {} wenn keine da ist."""
    pfad = Path(backup_ordner) / B.DATEIEN_LISTE
    if not pfad.is_file():
        raise SystemExit(f"Keine Objektliste in {backup_ordner} ({B.DATEIEN_LISTE}) — "
                         f"das Backup ist aelter als Manifest-Version 5 oder "
                         f"wurde ohne Datei-Sicherung erstellt.")
    with gzip.open(pfad, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def gegen_liste_pruefen(liste: dict, gesichert: dict, praefix: str) -> dict:
    """Stimmt der Sicherungs-Bucket noch mit dem Stand dieses Backups ueberein?

    Nr. 70: ohne diesen Abgleich laesst sich nicht belegen, dass die
    Dateien zum Datenbankstand passen — spaetere Laeufe schreiben dasselbe
    Praefix fort und der Papierkorb raeumt nach der Frist endgueltig auf."""
    fehlend, abweichend = [], []
    for key, soll in sorted(liste.items()):
        ist = gesichert.get(praefix + key)
        if ist is None:
            fehlend.append(key)
        elif int(soll.get("bytes") or 0) != int(ist[0] or 0):
            abweichend.append(key)
    zusaetzlich = sum(1 for k in gesichert if k[len(praefix):] not in liste)
    return {"soll": len(liste), "fehlend": fehlend, "abweichend": abweichend,
            "zusaetzlich": zusaetzlich}


def zurueckkopieren(praefix_filter: str = "", auch_geloeschte: bool = False,
                    dry_run: bool = True, liste_ordner: str = "") -> dict:
    quelle_bucket = os.environ["S3_BUCKET"].strip()
    sicherung_bucket, praefix = B.dateien_ziel()
    if not sicherung_bucket:
        raise SystemExit("Kein Sicherungs-Bucket gesetzt (BACKUP_DATEIEN_BUCKET/BACKUP_S3_BUCKET)")
    lesen = B._backup_s3_client()          # liest die Sicherung
    schreiben = B._s3_client()             # schreibt in den Datei-Speicher
    sse = sse_optionen(os.environ.get("S3_ENDPOINT", ""))

    vorhanden = B._objekte_auflisten(schreiben, quelle_bucket, praefix_filter)
    gesichert = B._objekte_auflisten(lesen, sicherung_bucket, praefix + praefix_filter)
    stand = {"gesichert": len(gesichert), "fehlt": 0, "zurueck": 0,
             "papierkorb_uebersprungen": 0, "fehler": 0, "dry_run": dry_run}
    if liste_ordner:
        stand["abgleich"] = gegen_liste_pruefen(
            liste_lesen(liste_ordner), gesichert, praefix)
    for sich_key in sorted(gesichert):
        key = sich_key[len(praefix):]
        if key in vorhanden:
            continue
        try:
            kopf = lesen.head_object(Bucket=sicherung_bucket, Key=sich_key)
            if (kopf.get("Metadata") or {}).get(B._GELOESCHT_AM) and not auch_geloeschte:
                stand["papierkorb_uebersprungen"] += 1
                continue
            stand["fehlt"] += 1
            if dry_run:
                continue
            body = lesen.get_object(Bucket=sicherung_bucket, Key=sich_key)["Body"]
            schreiben.upload_fileobj(body, quelle_bucket, key, ExtraArgs=dict(sse))
            stand["zurueck"] += 1
        except Exception as exc:  # noqa: BLE001
            stand["fehler"] += 1
            print(f"  FEHLER {key}: {exc}")
    return stand


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Dateien aus dem Sicherungs-Bucket zurueckholen")
    ap.add_argument("--praefix", default="", help="nur Schluessel mit diesem Anfang (z. B. protocol/)")
    ap.add_argument("--auch-geloeschte", action="store_true",
                    help="auch Dateien aus dem Papierkorb (im Datei-Speicher bewusst geloescht)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true", help="nur zaehlen, nichts schreiben")
    g.add_argument("--yes", action="store_true", help="wirklich zurueckkopieren")
    ap.add_argument("--liste", default="",
                    help="Backup-Ordner, gegen dessen Objektliste geprueft wird "
                         "(dateien-liste.json.gz; Nachpruefung 20.09.2026, Nr. 70)")
    args = ap.parse_args(argv)
    stand = zurueckkopieren(args.praefix, args.auch_geloeschte,
                            dry_run=not args.yes, liste_ordner=args.liste)
    print(f"Sicherung: {stand['gesichert']} Dateien; im Datei-Speicher fehlen {stand['fehlt']}; "
          f"zurueckkopiert {stand['zurueck']}; Papierkorb uebersprungen "
          f"{stand['papierkorb_uebersprungen']}; Fehler {stand['fehler']}"
          + (" (Probelauf, nichts geschrieben)" if stand["dry_run"] else ""))
    ab = stand.get("abgleich")
    if ab:
        print(f"Abgleich mit dem Backup-Stand: {ab['soll']} Dateien gehoerten dazu; "
              f"davon fehlen jetzt {len(ab['fehlend'])}, {len(ab['abweichend'])} haben "
              f"eine andere Groesse; {ab['zusaetzlich']} Dateien kamen spaeter dazu.")
        for key in (ab["fehlend"] + ab["abweichend"])[:20]:
            print(f"  - {key}")
        if ab["fehlend"] or ab["abweichend"]:
            print("  ACHTUNG: Der Sicherungs-Bucket zeigt NICHT mehr genau den Stand "
                  "dieses Backups. Fuer einen beweisbaren Stand muss die Datei-Sicherung "
                  "versioniert werden (Objektversionierung am Bucket).")
            return 2
    return 1 if stand["fehler"] else 0


if __name__ == "__main__":
    sys.exit(main())
