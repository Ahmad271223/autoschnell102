# -*- coding: utf-8 -*-
"""Wiederherstellung eines AutoSchnell-Backups (Gegenstück zu backup_mongo.py).

    python -X utf8 restore_mongo.py <Backup-Ordner> [--db autoschnell] [--yes]
    python -X utf8 restore_mongo.py <Backup-Ordner> --dry-run     # nur prüfen

Ablauf (Prüfbericht Runde 5 — ein Restore darf die Zieldatenbank nie halb
zerstören):
  1. VORABPRÜFUNG: manifest.json lesen, SHA-256 JEDER Datei prüfen, jede
     .bson.gz vollständig einlesen und die Dokumentzahl mit dem Manifest
     vergleichen. Schlägt irgendetwas fehl, wird NICHTS verändert.
  2. Restore in eine TEMPORÄRE Datenbank (<db>__restore_<stamp>) inkl.
     Indexe aus den metadata.json.
  3. UMSCHALTEN je Collection per renameCollection: die bisherige Collection
     wandert nach <db>__vorher_<stamp> (bleibt als Rückfalllinie erhalten),
     dann die wiederhergestellte an ihren Platz. Jeder Schritt ist für sich
     atomar; die Zieldatenbank enthält zu keinem Zeitpunkt halb geladene
     Collections.
  4. Datei-Speicher (uploads/, local_storage/, s3/) zurückspielen.
Alte Backups ohne Manifest (Version < 2) werden nur mit --allow-no-manifest
akzeptiert (dann ohne Prüfsummen, aber weiterhin mit vollständigem Einlesen
vor dem Umschalten).
"""
import argparse
import gzip
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

import bson
from pymongo import MongoClient

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://127.0.0.1:27017")


def read_bson_stream(fh):
    while True:
        head = fh.read(4)
        if len(head) < 4:
            return
        length = int.from_bytes(head, "little")
        body = head + fh.read(length - 4)
        if len(body) != length:
            raise ValueError("BSON-Datei ist abgeschnitten")
        yield bson.decode(body)


def sha256_datei(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def pruefe_backup(root: Path, allow_no_manifest: bool):
    """Liefert (dumps: {name: [docs]}, manifest|None). Wirft bei jedem Fehler."""
    manifest = None
    mp = root / "manifest.json"
    if mp.is_file():
        manifest = json.loads(mp.read_text(encoding="utf-8"))
        fehler = []
        for rel, info in (manifest.get("files") or {}).items():
            f = root / rel
            if not f.is_file():
                fehler.append(f"fehlt: {rel}")
                continue
            if sha256_datei(f) != info.get("sha256"):
                fehler.append(f"Pruefsumme falsch: {rel}")
        if fehler:
            raise ValueError("Backup beschaedigt — " + "; ".join(fehler[:10]))
        print(f"  Pruefsummen OK ({len(manifest.get('files') or {})} Dateien)")
    elif not allow_no_manifest:
        raise ValueError("manifest.json fehlt (altes Backup?) — mit "
                         "--allow-no-manifest ohne Pruefsummen fortfahren")
    else:
        print("  WARNUNG: kein Manifest — keine Pruefsummenpruefung moeglich")

    db_dir = None
    for kandidat in [d for d in root.iterdir() if d.is_dir()]:
        if list(kandidat.glob("*.bson.gz")):
            db_dir = kandidat
            break
    if db_dir is None and list(root.glob("*.bson.gz")):
        db_dir = root
    if db_dir is None:
        raise ValueError(f"keine .bson.gz-Dateien unter {root}")

    dumps = {}
    for f in sorted(db_dir.glob("*.bson.gz")):
        name = f.name[:-len(".bson.gz")]
        with gzip.open(f, "rb") as fh:
            docs = list(read_bson_stream(fh))
        erwartet = (manifest or {}).get("collections", {}).get(name)
        if erwartet is not None and erwartet != len(docs):
            raise ValueError(f"{name}: {len(docs)} Dokumente gelesen, Manifest "
                             f"erwartet {erwartet}")
        dumps[name] = (docs, db_dir / f"{name}.metadata.json")
        print(f"  {name}: {len(docs)} Dokumente gelesen")
    return dumps, manifest, db_dir


def indexe_anlegen(coll, meta_path: Path) -> None:
    if not meta_path.is_file():
        return
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return
    for idx in meta.get("indexes") or []:
        name = idx.get("name")
        if not name or name == "_id_":
            continue
        keys = [(k, v) for k, v in (idx.get("key") or {}).items()]
        if not keys:
            continue
        opts = {k: v for k, v in idx.items()
                if k in ("unique", "sparse", "expireAfterSeconds",
                         "partialFilterExpression")}
        try:
            coll.create_index(keys, name=name, **opts)
        except Exception as exc:  # noqa: BLE001
            print(f"  Hinweis: Index {coll.name}.{name} nicht angelegt: {exc}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("backup_dir")
    ap.add_argument("--db", default=os.environ.get("DB_NAME", "autoschnell"))
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--dry-run", action="store_true",
                    help="nur pruefen, nichts veraendern")
    ap.add_argument("--allow-no-manifest", action="store_true")
    ap.add_argument("--keep-old", action="store_true", default=True,
                    help="bisherige Collections als <db>__vorher_<stamp> behalten (Standard)")
    args = ap.parse_args()

    root = Path(args.backup_dir)
    if not root.is_dir():
        print(f"FEHLER: {root} ist kein Verzeichnis")
        return 1
    print(f"1/4 Vorabpruefung von {root} ...")
    try:
        dumps, manifest, db_dir = pruefe_backup(root, args.allow_no_manifest)
    except Exception as exc:  # noqa: BLE001
        print(f"FEHLER: {exc}")
        print("Es wurde NICHTS veraendert.")
        return 1
    if manifest and manifest.get("unvollstaendig"):
        print("  HINWEIS: Backup war als UNVOLLSTAENDIG markiert: "
              + "; ".join(manifest["unvollstaendig"]))
    if args.dry_run:
        print("DRY-RUN OK: Backup ist konsistent; nichts veraendert.")
        return 0

    if not args.yes:
        answer = input(f"{len(dumps)} Collections nach '{args.db}' wiederherstellen? "
                       f"Bestehende Daten dort werden ERSETZT (Kopie bleibt als "
                       f"'{args.db}__vorher_...'). [ja/nein] ")
        if answer.strip().lower() not in ("ja", "j", "yes", "y"):
            print("Abgebrochen.")
            return 1

    client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=10000)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    tmp_name = f"{args.db}__restore_{stamp}"
    alt_name = f"{args.db}__vorher_{stamp}"
    tmp = client[tmp_name]

    print(f"2/4 Laden in temporaere Datenbank {tmp_name} ...")
    try:
        total = 0
        for name, (docs, meta_path) in dumps.items():
            if docs:
                tmp[name].insert_many(docs, ordered=False)
            else:
                tmp.create_collection(name)
            indexe_anlegen(tmp[name], meta_path)
            total += len(docs)
    except Exception as exc:  # noqa: BLE001
        print(f"FEHLER beim Laden: {exc} — temporaere Datenbank wird entfernt, "
              f"Zieldatenbank '{args.db}' ist unveraendert.")
        client.drop_database(tmp_name)
        return 1

    print(f"3/4 Umschalten je Collection (bisheriger Stand -> {alt_name}) ...")
    ziel = client[args.db]
    vorhandene = set(ziel.list_collection_names())
    umgeschaltet = []
    try:
        for name in dumps:
            if name in vorhandene:
                client.admin.command("renameCollection", f"{args.db}.{name}",
                                     to=f"{alt_name}.{name}", dropTarget=True)
            client.admin.command("renameCollection", f"{tmp_name}.{name}",
                                 to=f"{args.db}.{name}", dropTarget=True)
            umgeschaltet.append(name)
    except Exception as exc:  # noqa: BLE001
        print(f"FEHLER beim Umschalten nach {len(umgeschaltet)} Collections: {exc}")
        print(f"Umgeschaltet: {', '.join(umgeschaltet) or '-'}; der vorherige Stand "
              f"dieser Collections liegt unveraendert in '{alt_name}', die "
              f"restlichen Collections in '{args.db}' sind unberuehrt. Temporaere "
              f"Datenbank '{tmp_name}' bleibt zur Analyse erhalten.")
        return 1
    client.drop_database(tmp_name)
    extra = sorted(vorhandene - set(dumps))
    if extra:
        print(f"  Hinweis: nicht im Backup enthalten und daher unveraendert "
              f"belassen: {', '.join(extra)}")

    print("4/4 Datei-Speicher zurueckspielen ...")
    backend = Path(__file__).resolve().parent.parent
    n_files = 0
    for name in ("uploads", "local_storage"):
        quelle = root / name
        if not quelle.is_dir():
            continue
        for f2 in quelle.rglob("*"):
            if not f2.is_file():
                continue
            d = backend / name / f2.relative_to(quelle)
            d.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f2, d)
            n_files += 1
    s3_dir = root / "s3"
    if s3_dir.is_dir():
        if all(os.environ.get(v, "").strip() for v in
               ("S3_ENDPOINT", "S3_BUCKET", "S3_ACCESS_KEY", "S3_SECRET_KEY")):
            import boto3
            c = boto3.client("s3", endpoint_url=os.environ["S3_ENDPOINT"],
                             aws_access_key_id=os.environ["S3_ACCESS_KEY"],
                             aws_secret_access_key=os.environ["S3_SECRET_KEY"],
                             region_name=os.environ.get("S3_REGION", "auto"))
            for f2 in s3_dir.rglob("*"):
                if f2.is_file():
                    c.upload_file(str(f2), os.environ["S3_BUCKET"],
                                  str(f2.relative_to(s3_dir)).replace("\\", "/"))
                    n_files += 1
        else:
            print("  WARNUNG: Backup enthaelt S3-Objekte, aber S3 ist hier nicht "
                  "konfiguriert — nicht zurueckgespielt.")
    print(f"RESTORE OK: {len(dumps)} Collections, {total} Dokumente, "
          f"{n_files} Dateien -> {args.db}")
    print(f"Der vorherige Datenbestand liegt in '{alt_name}'. Wenn alles passt, "
          f"entfernen mit:  mongosh --eval \"db.getSiblingDB('{alt_name}').dropDatabase()\"")
    print("HINWEIS: Eindeutigkeits-Indizes prueft das Backend beim naechsten "
          "Start (ensure_indexes) — nach dem Restore einmal neu starten.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
