# -*- coding: utf-8 -*-
"""Wiederherstellung eines AutoSchnell-Backups (Gegenstück zu backup_mongo.py).

    python -X utf8 restore_mongo.py "C:\\AutoSchnell-Backups\\autoschnell-2026-08-16_0300"

Standard: stellt in die Datenbank 'autoschnell' her, bestehende Collections
werden vorher geleert (--db für Test-Restore in eine andere DB).
Fragt vor dem Überschreiben zur Sicherheit nach (--yes überspringt das).
"""
import argparse
import gzip
import sys
from pathlib import Path

import bson
from pymongo import MongoClient

import os
# Aus der Umgebung wie beim Backup — im Container heisst der Host 'mongo'.
MONGO_URL = os.environ.get("MONGO_URL", "mongodb://127.0.0.1:27017")


def read_bson_stream(fh):
    """BSON-Dokumente aus einem Stream lesen (Länge-prefixed, wie mongodump)."""
    while True:
        head = fh.read(4)
        if len(head) < 4:
            return
        length = int.from_bytes(head, "little")
        body = head + fh.read(length - 4)
        yield bson.decode(body)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("backup_dir")
    ap.add_argument("--db", default="autoschnell")
    ap.add_argument("--yes", action="store_true")
    args = ap.parse_args()

    src = Path(args.backup_dir)
    # Backup-Ordner enthaelt einen 'autoschnell'-Unterordner
    inner = src / "autoschnell"
    if inner.is_dir():
        src = inner
    files = sorted(src.glob("*.bson.gz"))
    if not files:
        print(f"FEHLER: keine .bson.gz-Dateien in {src}")
        return 1

    if not args.yes:
        answer = input(f"{len(files)} Collections nach '{args.db}' wiederherstellen? "
                       f"Bestehende Daten dort werden ERSETZT. [ja/nein] ")
        if answer.strip().lower() not in ("ja", "j", "yes", "y"):
            print("Abgebrochen.")
            return 1

    db = MongoClient(MONGO_URL)[args.db]
    total = 0
    for f in files:
        name = f.name[:-len(".bson.gz")]
        docs = []
        with gzip.open(f, "rb") as fh:
            docs = list(read_bson_stream(fh))
        db[name].drop()
        if docs:
            db[name].insert_many(docs, ordered=False)
        total += len(docs)
        print(f"  {name}: {len(docs)} Dokumente")
    print(f"RESTORE OK: {len(files)} Collections, {total} Dokumente -> {args.db}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
