# -*- coding: utf-8 -*-
"""Tägliches MongoDB-Backup für AutoSchnell.

Schreibt jede Collection als <name>.bson.gz (mongodump-kompatibles Roh-BSON,
gzip-komprimiert) plus <name>.metadata.json (Indexe). Wiederherstellen geht
mit dem beiliegenden restore_mongo.py — oder mit mongorestore --gzip.

Aufbewahrung: die letzten 14 Backups bleiben, ältere werden gelöscht.

Aufruf (macht der Windows-Taskplaner täglich 03:00):
    python -X utf8 backup_mongo.py
Optional:  --dir <Zielordner>   (Standard: C:\\AutoSchnell-Backups)
"""
import argparse
import gzip
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

import bson
from pymongo import MongoClient

# Im Container laeuft Mongo unter dem Dienstnamen "mongo", lokal unter
# 127.0.0.1 — beides kommt aus der Umgebung, damit das Backup in BEIDEN
# Faellen die richtige Datenbank erreicht.
MONGO_URL = os.environ.get("MONGO_URL", "mongodb://127.0.0.1:27017")
DB_NAME = os.environ.get("DB_NAME", "autoschnell")
KEEP = 14          # so viele Backups bleiben erhalten
DEFAULT_DIR = Path(r"C:\AutoSchnell-Backups")
# Datei-Speicher (Fotos, Vertrags-PDFs, Snapshots, Unterschriften) — liegt
# NICHT in MongoDB und muss deshalb mitgesichert werden.
UPLOADS_DIR = Path(__file__).resolve().parent.parent / "uploads"
# Beweis-Snapshots (Mobile Rebuild, Kleinanzeigen-Fotos) liegen NICHT in
# uploads/, sondern hier — ohne diese Sicherung waere "wiederherstellbar"
# eine Falschaussage (PR-Review 09/2026).
LOCAL_STORAGE_DIR = Path(__file__).resolve().parent.parent / "local_storage"


def log(msg: str, logfile: Path) -> None:
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line)
    try:
        with open(logfile, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def dump_collection(coll, out_dir: Path) -> int:
    """Alle Dokumente als Roh-BSON (gzip) schreiben — exakt das Format,
    das mongodump erzeugt (BSON-Dokumente einfach hintereinander)."""
    n = 0
    with gzip.open(out_dir / f"{coll.name}.bson.gz", "wb") as fh:
        for doc in coll.find({}):
            fh.write(bson.encode(doc))
            n += 1
    # Indexe im mongodump-Metadata-Format (fuer mongorestore / Doku).
    try:
        indexes = list(coll.list_indexes())
        meta = {"options": {}, "collectionName": coll.name,
                "indexes": [json.loads(bson.json_util.dumps(i)) for i in indexes]}
        with open(out_dir / f"{coll.name}.metadata.json", "w", encoding="utf-8") as fh:
            json.dump(meta, fh, ensure_ascii=False)
    except Exception:
        pass
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
    base.mkdir(parents=True, exist_ok=True)
    logfile = base / "backup.log"

    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    # ATOMAR (PR-Review 09/2026): erst in einen .tmp-Ordner schreiben und
    # NUR bei vollem Erfolg auf den endgueltigen Namen umbenennen. Vorher
    # blieb bei einem Teilfehler ein halber "autoschnell-…"-Ordner stehen,
    # den die Ueberwachung als gueltiges letztes Backup wertete — weitere
    # Backups unterblieben dann 24 h lang.
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

    total_docs = 0
    for name in names:
        try:
            n = dump_collection(db[name], target)
            total_docs += n
            log(f"  {name}: {n} Dokumente", logfile)
        except Exception as exc:
            log(f"FEHLER bei {name}: {exc}", logfile)
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return 1

    # Datei-Speicher spiegeln (nur neue/geänderte Dateien kopieren).
    n_files = 0
    for quelle, name in ((UPLOADS_DIR, "uploads"),
                         (LOCAL_STORAGE_DIR, "local_storage")):
        if not quelle.is_dir():
            continue
        files_target = target.parent / name
        for src in quelle.rglob("*"):
            if not src.is_file():
                continue
            rel = src.relative_to(quelle)
            dst = files_target / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            n_files += 1
    log(f"  Datei-Speicher: {n_files} Dateien gesichert "
        f"(uploads + local_storage)", logfile)

    size_mb = sum(f.stat().st_size for f in target.parent.rglob("*") if f.is_file()) / 1e6
    tmp_dir.rename(final_dir)          # atomarer Abschluss
    log(f"BACKUP OK: {len(names)} Collections, {total_docs} Dokumente, "
        f"{n_files} Dateien, {size_mb:.1f} MB -> {final_dir.name}", logfile)
    rotate(base, logfile)
    return 0


if __name__ == "__main__":
    sys.exit(main())
