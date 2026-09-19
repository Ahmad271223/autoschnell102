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

Aufruf:
  python -X utf8 dateien_zurueckkopieren.py --dry-run          # nur zaehlen
  python -X utf8 dateien_zurueckkopieren.py --yes              # zurueckholen
  python -X utf8 dateien_zurueckkopieren.py --yes --praefix protocol/   # nur ein Ordner
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import backup_mongo as B  # noqa: E402
from s3_kompatibel import sse_optionen  # noqa: E402


def zurueckkopieren(praefix_filter: str = "", auch_geloeschte: bool = False,
                    dry_run: bool = True) -> dict:
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
    args = ap.parse_args(argv)
    stand = zurueckkopieren(args.praefix, args.auch_geloeschte, dry_run=not args.yes)
    print(f"Sicherung: {stand['gesichert']} Dateien; im Datei-Speicher fehlen {stand['fehlt']}; "
          f"zurueckkopiert {stand['zurueck']}; Papierkorb uebersprungen "
          f"{stand['papierkorb_uebersprungen']}; Fehler {stand['fehler']}"
          + (" (Probelauf, nichts geschrieben)" if stand["dry_run"] else ""))
    return 1 if stand["fehler"] else 0


if __name__ == "__main__":
    sys.exit(main())
