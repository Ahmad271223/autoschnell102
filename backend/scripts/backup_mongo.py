# -*- coding: utf-8 -*-
"""Tägliches Backup für AutoSchnell: MongoDB + Datei-Speicher.

Schreibt jede Collection als <name>.bson.gz (mongodump-kompatibles Roh-BSON,
gzip) plus <name>.metadata.json (Indexe), spiegelt die Datei-Speicher
(uploads/, local_storage/ und — falls konfiguriert — den S3-Bucket) und
legt eine manifest.json mit SHA-256-Prüfsummen und Dokumentzahlen an.
restore_mongo.py verweigert die Wiederherstellung, wenn eine Prüfsumme
nicht stimmt.

Ergebnis-Status (Prüfbericht Runde 5 — "BACKUP OK" nur, wenn wirklich
alles gesichert wurde):
  BACKUP OK               Exit 0  — Datenbank + alle Datei-Speicher gesichert
  BACKUP UNVOLLSTAENDIG   Exit 2  — Datenbank gesichert, aber mindestens ein
                                    Datei-Speicher fehlt/war nicht erreichbar
  FEHLER                  Exit 1  — Datenbank nicht gesichert

Aufbewahrung: die letzten 14 Backups bleiben, ältere werden gelöscht.

Aufruf:  python -X utf8 backup_mongo.py [--dir <Zielordner>]
"""
import argparse
import gzip
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import bson
from bson import json_util
from pymongo import MongoClient

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://127.0.0.1:27017")
DB_NAME = os.environ.get("DB_NAME", "autoschnell")
KEEP = 14
DEFAULT_DIR = Path(os.environ.get("BACKUP_DIR") or r"C:\AutoSchnell-Backups")
BACKEND = Path(__file__).resolve().parent.parent
UPLOADS_DIR = BACKEND / "uploads"
LOCAL_STORAGE_DIR = BACKEND / "local_storage"
MANIFEST_VERSION = 2


def log(msg: str, logfile: Path) -> None:
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line)
    try:
        with open(logfile, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def sha256_datei(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def dump_collection(coll, out_dir: Path) -> int:
    n = 0
    with gzip.open(out_dir / f"{coll.name}.bson.gz", "wb") as fh:
        for doc in coll.find({}):
            fh.write(bson.encode(doc))
            n += 1
    try:
        indexes = list(coll.list_indexes())
        meta = {"options": {}, "collectionName": coll.name,
                "indexes": [json.loads(json_util.dumps(i)) for i in indexes]}
        with open(out_dir / f"{coll.name}.metadata.json", "w", encoding="utf-8") as fh:
            json.dump(meta, fh, ensure_ascii=False)
    except Exception:
        pass
    return n


def spiegle_ordner(quelle: Path, ziel: Path) -> int:
    n = 0
    for src in quelle.rglob("*"):
        if not src.is_file():
            continue
        dst = ziel / src.relative_to(quelle)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        n += 1
    return n


def spiegle_s3(ziel: Path, logfile: Path) -> int:
    """Alle Objekte des konfigurierten S3-Buckets herunterladen (Runde 5:
    vorher wurden bei S3-Betrieb NUR lokale Ordner gesichert)."""
    import boto3
    client = boto3.client(
        "s3", endpoint_url=os.environ["S3_ENDPOINT"],
        aws_access_key_id=os.environ["S3_ACCESS_KEY"],
        aws_secret_access_key=os.environ["S3_SECRET_KEY"],
        region_name=os.environ.get("S3_REGION", "auto"))
    bucket = os.environ["S3_BUCKET"]
    n = 0
    for seite in client.get_paginator("list_objects_v2").paginate(Bucket=bucket):
        for obj in seite.get("Contents") or []:
            key = obj["Key"]
            dst = ziel / key
            dst.parent.mkdir(parents=True, exist_ok=True)
            client.download_file(bucket, key, str(dst))
            n += 1
    log(f"  S3-Bucket {bucket}: {n} Objekte gesichert", logfile)
    return n


def rotate(base: Path, logfile: Path) -> None:
    dumps = sorted([p for p in base.iterdir()
                    if p.is_dir() and p.name.startswith("autoschnell-")])
    for old in dumps[:-KEEP]:
        shutil.rmtree(old, ignore_errors=True)
        log(f"Altes Backup entfernt: {old.name}", logfile)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=str(DEFAULT_DIR))
    args = ap.parse_args()
    base = Path(args.dir)
    try:
        base.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"FEHLER: Backup-Verzeichnis {base} nicht beschreibbar — {exc}")
        return 1
    logfile = base / "backup.log"

    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    final_dir = base / f"autoschnell-{stamp}"
    tmp_dir = base / f".tmp-autoschnell-{stamp}"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir, ignore_errors=True)
    target = tmp_dir / DB_NAME
    target.mkdir(parents=True, exist_ok=True)

    try:
        client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=10000)
        db = client[DB_NAME]
        names = sorted(db.list_collection_names())
    except Exception as exc:
        log(f"FEHLER: MongoDB nicht erreichbar — {exc}", logfile)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return 1

    counts = {}
    for name in names:
        try:
            counts[name] = dump_collection(db[name], target)
            log(f"  {name}: {counts[name]} Dokumente", logfile)
        except Exception as exc:
            log(f"FEHLER bei {name}: {exc}", logfile)
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return 1

    # ---- Datei-Speicher ----
    unvollstaendig = []
    n_files = 0
    for quelle, name in ((UPLOADS_DIR, "uploads"),
                         (LOCAL_STORAGE_DIR, "local_storage")):
        if not quelle.is_dir():
            unvollstaendig.append(f"{name}: Verzeichnis {quelle} fehlt")
            log(f"  WARNUNG: {name} nicht gefunden ({quelle})", logfile)
            continue
        try:
            k = spiegle_ordner(quelle, tmp_dir / name)
        except OSError as exc:
            unvollstaendig.append(f"{name}: {exc}")
            log(f"  WARNUNG: {name} nur teilweise gesichert — {exc}", logfile)
            continue
        n_files += k
        log(f"  {name}: {k} Dateien gesichert", logfile)
    s3_aktiv = all(os.environ.get(v, "").strip() for v in
                   ("S3_ENDPOINT", "S3_BUCKET", "S3_ACCESS_KEY", "S3_SECRET_KEY"))
    if s3_aktiv:
        try:
            n_files += spiegle_s3(tmp_dir / "s3", logfile)
        except Exception as exc:  # noqa: BLE001
            unvollstaendig.append(f"s3: {exc}")
            log(f"  WARNUNG: S3-Bucket NICHT gesichert — {exc}", logfile)
    if os.environ.get("EMERGENT_LLM_KEY", "").strip():
        # Externer Snapshot-Speicher ohne Listing-API: nicht sicherbar.
        unvollstaendig.append("snapshots: externer Snapshot-Speicher (EMERGENT) "
                              "ist von hier aus nicht sicherbar")
        log("  WARNUNG: externer Snapshot-Speicher wird nicht gesichert", logfile)

    # ---- Manifest mit Pruefsummen ----
    dateien = {}
    for f in sorted(tmp_dir.rglob("*")):
        if f.is_file():
            dateien[str(f.relative_to(tmp_dir)).replace("\\", "/")] = {
                "sha256": sha256_datei(f), "bytes": f.stat().st_size}
    manifest = {
        "version": MANIFEST_VERSION, "db": DB_NAME,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "collections": counts, "files": dateien,
        "unvollstaendig": unvollstaendig,
    }
    with open(tmp_dir / "manifest.json", "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=1)

    size_mb = sum(v["bytes"] for v in dateien.values()) / 1e6
    tmp_dir.rename(final_dir)          # atomarer Abschluss
    total_docs = sum(counts.values())
    if unvollstaendig:
        log(f"BACKUP UNVOLLSTAENDIG: {len(names)} Collections, {total_docs} "
            f"Dokumente, {n_files} Dateien, {size_mb:.1f} MB -> {final_dir.name}; "
            f"NICHT gesichert: {'; '.join(unvollstaendig)}", logfile)
        rotate(base, logfile)
        return 2
    log(f"BACKUP OK: {len(names)} Collections, {total_docs} Dokumente, "
        f"{n_files} Dateien, {size_mb:.1f} MB -> {final_dir.name}", logfile)
    rotate(base, logfile)
    return 0


if __name__ == "__main__":
    sys.exit(main())
