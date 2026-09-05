# -*- coding: utf-8 -*-
"""Vorhandene Beweis-Snapshots von der lokalen Platte in den Objektspeicher
(R2/S3) verschieben — einmalig, beliebig oft wiederholbar.

Warum: Bis 09/2026 lagen Snapshot-Dateien (JPG + PDF je Inserat) nur unter
backend/local_storage/ auf dem einen Server. Ein zweiter Anwendungsserver
sieht diese Platte nicht, und bei Verlust des Servers gibt es die Dateien
nur noch in der Sicherung. Seit dem Umbau schreibt der Server neue
Snapshots direkt in den Objektspeicher; die ALTEN muessen einmal
hinterhergetragen werden. Bis dahin findet der Server sie ueber den
lokalen Rueckfall weiterhin.

Aufruf (im Container, Ordner backend):
    python scripts/snapshots_nach_r2.py            # zeigt nur, was zu tun ist
    python scripts/snapshots_nach_r2.py --wirklich # laedt hoch, prueft Groesse
    python scripts/snapshots_nach_r2.py --wirklich --loeschen
        # loescht die lokale Kopie NUR, wenn der Upload nachweislich
        # vollstaendig ist (Groesse stimmt ueberein)

Sicher gegen Wiederholung: eine Datei, die im Objektspeicher schon mit
richtiger Groesse liegt, wird uebersprungen. Exit 0 = alles wie erwartet.
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> int:
    ap = argparse.ArgumentParser(description="Snapshots von der Platte in R2/S3 verschieben")
    ap.add_argument("--wirklich", action="store_true", help="wirklich hochladen")
    ap.add_argument("--loeschen", action="store_true",
                    help="lokale Kopie nach bestaetigtem Upload loeschen")
    ap.add_argument("--ordner", default="",
                    help="Quellordner (Standard: backend/local_storage)")
    args = ap.parse_args()

    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    except ImportError:
        pass

    if not (os.environ.get("S3_ENDPOINT", "").strip() and os.environ.get("S3_BUCKET", "").strip()):
        print("FEHLER: S3_ENDPOINT / S3_BUCKET nicht gesetzt — es gibt keinen")
        print("        Objektspeicher, in den verschoben werden koennte.")
        return 1

    import storage_service as st
    speicher = st.storage
    if getattr(speicher, "name", "") != "s3":
        print(f"FEHLER: Datei-Speicher ist '{getattr(speicher, 'name', '?')}', nicht s3.")
        return 1

    quelle = Path(args.ordner) if args.ordner else Path(__file__).resolve().parents[1] / "local_storage"
    if not quelle.is_dir():
        print(f"Kein lokaler Snapshot-Ordner ({quelle}) — nichts zu tun.")
        return 0

    dateien = sorted(p for p in quelle.rglob("*") if p.is_file())
    print(f"{len(dateien)} Datei(en) unter {quelle}")
    if not dateien:
        return 0

    hoch, uebersprungen, fehler, geloescht = 0, 0, [], 0
    gesamt = 0
    for pfad in dateien:
        key = pfad.relative_to(quelle).as_posix()
        groesse = pfad.stat().st_size
        gesamt += groesse
        try:
            st._validate_key(key)
        except st.StorageError as exc:
            fehler.append(f"{key}: {exc}")
            continue
        vorhanden = False
        try:
            if speicher.exists(key):
                kopf = speicher.client.head_object(Bucket=speicher.bucket, Key=key)
                vorhanden = int(kopf.get("ContentLength", -1)) == groesse
        except Exception:                               # noqa: BLE001
            vorhanden = False
        if vorhanden:
            uebersprungen += 1
        elif not args.wirklich:
            print(f"  wuerde hochladen: {key} ({groesse} Bytes)")
            continue
        else:
            try:
                speicher.save(key, pfad.read_bytes())
                kopf = speicher.client.head_object(Bucket=speicher.bucket, Key=key)
                if int(kopf.get("ContentLength", -1)) != groesse:
                    fehler.append(f"{key}: Groesse nach Upload weicht ab")
                    continue
                hoch += 1
            except Exception as exc:                    # noqa: BLE001
                fehler.append(f"{key}: {exc}")
                continue
        if args.wirklich and args.loeschen:
            try:
                pfad.unlink()
                geloescht += 1
            except OSError as exc:
                fehler.append(f"{key}: lokal nicht loeschbar ({exc})")

    print()
    print(f"gesamt {gesamt / 1024 / 1024:.1f} MB | hochgeladen {hoch} | "
          f"schon vorhanden {uebersprungen} | lokal geloescht {geloescht} | "
          f"Fehler {len(fehler)}")
    for f in fehler[:20]:
        print("  FEHLER", f)
    if not args.wirklich:
        print("\nProbelauf. Mit --wirklich hochladen; mit --loeschen danach die")
        print("lokalen Kopien entfernen (nur bei bestaetigtem Upload).")
    return 1 if fehler else 0


if __name__ == "__main__":
    sys.exit(main())
