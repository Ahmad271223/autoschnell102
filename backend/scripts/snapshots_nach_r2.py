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
richtiger Groesse UND gleichem Inhalt liegt (Pruefbericht 20.09.2026, SK-11:
SHA-256 im Metadatum `sha256`, ersatzweise ETag = MD5 bei Einteil-Upload),
wird uebersprungen; nur dann wird mit --loeschen die lokale Kopie entfernt.
Exit 0 = alles wie erwartet.
"""
import argparse
import hashlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _sha256(pfad: Path) -> str:
    h = hashlib.sha256()
    with open(pfad, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _md5(pfad: Path) -> str:
    h = hashlib.md5()  # noqa: S324 — nur Vergleich mit dem S3-ETag, kein Schutzzweck
    with open(pfad, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def inhalt_gleich(kopf: dict, pfad: Path, groesse: int, digest: str) -> bool:
    """SK-11: Ist das Objekt nachweislich dieselbe Datei? Groesse gleich UND
    (Metadatum sha256 gleich ODER — ohne Metadatum — ETag gleich dem MD5 der
    Datei; ein Mehrteil-ETag mit '-' laesst sich so nicht pruefen -> False)."""
    if int((kopf or {}).get("ContentLength", -1)) != groesse:
        return False
    meta_sha = ((kopf or {}).get("Metadata") or {}).get("sha256")
    if meta_sha:
        return meta_sha == digest
    etag = str((kopf or {}).get("ETag") or "").strip('"')
    if etag and "-" not in etag:
        return etag.lower() == _md5(pfad)
    return False


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
        digest = _sha256(pfad)
        vorhanden = False
        try:
            if speicher.exists(key):
                kopf = speicher.client.head_object(Bucket=speicher.bucket, Key=key)
                # SK-11: Groesse allein genuegt nicht — Inhalt vergleichen
                vorhanden = inhalt_gleich(kopf, pfad, groesse, digest)
        except Exception:                               # noqa: BLE001
            vorhanden = False
        if vorhanden:
            uebersprungen += 1
        elif not args.wirklich:
            print(f"  wuerde hochladen: {key} ({groesse} Bytes)")
            continue
        else:
            try:
                # SK-11: mit sha256-Metadatum (wie backup_mongo.offsite_hochladen),
                # damit sich jeder spaetere Lauf am Inhalt orientieren kann.
                speicher.client.put_object(Bucket=speicher.bucket, Key=key,
                                           Body=pfad.read_bytes(),
                                           Metadata={"sha256": digest})
                kopf = speicher.client.head_object(Bucket=speicher.bucket, Key=key)
                if not inhalt_gleich(kopf, pfad, groesse, digest):
                    fehler.append(f"{key}: Inhalt nach Upload nicht bestaetigt "
                                  f"(Groesse/Pruefsumme weicht ab)")
                    continue
                hoch += 1
            except Exception as exc:                    # noqa: BLE001
                fehler.append(f"{key}: {exc}")
                continue
        # Hierher kommt nur, was nachweislich mit gleichem Inhalt im
        # Objektspeicher liegt — erst dann darf die lokale Kopie weg (SK-11).
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
