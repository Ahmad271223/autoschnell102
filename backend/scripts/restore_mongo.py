# -*- coding: utf-8 -*-
"""Wiederherstellung eines AutoSchnell-Backups (Gegenstück zu backup_mongo.py).

    python -X utf8 restore_mongo.py <Backup-Ordner> [--db autoschnell] [--yes]
    python -X utf8 restore_mongo.py <Backup-Ordner> --dry-run     # nur prüfen

Grundsatz (Go-Live-Audit): Nach einem Restore ist die Zieldatenbank
ENTWEDER vollständig auf dem alten ODER vollständig auf dem Backup-Stand —
nie gemischt. Dafür:

  1/6 VORABPRÜFUNG: manifest.json lesen, SHA-256 JEDER Datei prüfen, jede
      .bson.gz vollständig einlesen, Dokumentzahl gegen das Manifest.
      Ein als UNVOLLSTAENDIG markiertes Backup wird abgelehnt (Exit 1,
      nichts verändert) — nur --notfall-unvollstaendig-akzeptieren spielt
      es trotzdem ein (mit lauter Warnung, was fehlt). Enthält das Backup
      S3-Objekte, muss S3 konfiguriert sein — sonst Abbruch, außer --ohne-s3.
  2/6 Laden in eine TEMPORÄRE Datenbank (<db>__restore_<stamp>) inkl.
      Indexe aus den metadata.json.
  3/6 PRÜFUNG VOR DEM UMSCHALTEN: Dokumentzahlen der temporären Datenbank
      gegen das Manifest, alle Indexe vorhanden, Datei-Speicher in
      Staging-Ordner (<live>.restore-<stamp>) kopiert und die Prüfsummen
      DORT erneut geprüft. Schlägt etwas fehl: Staging + temporäre DB weg,
      Zieldatenbank und Live-Ordner unverändert.
  4/6 WARTUNGSMODUS: system_flags {_id: "wartungsmodus", aktiv: true} in
      der Zieldatenbank — die API antwortet solange mit 503. Danach ggf.
      S3-Objekte zurückspielen (nicht rückgängig machbar, deshalb VOR dem
      Umschalten; bei Fehler bleibt Datenbank/Datei-Speicher unverändert).
  5/6 UMSCHALTEN: Ordner per Rename (live -> <live>.vorher-<stamp>,
      Staging -> live), dann je Collection renameCollection (bisheriger
      Stand -> <db>__vorher_<stamp>). Jeder Fehler: ALLE bereits
      umgeschalteten Collections und Ordner werden zurückgedreht.
  6/6 KONTROLLE: Dokumentzahlen und Indexe der Live-Datenbank erneut gegen
      das Manifest. Nur wenn alles passt: "RESTORE OK", Wartungsmodus aus.
      Sonst Rollback, Exit 1.

Die Collection system_flags (Betriebs-Flags, u. a. der Wartungsmodus) wird
nie aus dem Backup zurückgespielt. Alte Backups ohne Manifest (Version < 2)
werden nur mit --allow-no-manifest akzeptiert (dann ohne Prüfsummen, aber
weiterhin mit vollständigem Einlesen vor dem Umschalten). --nur-datenbank
lässt die Datei-Speicher unangetastet (Restore-Probe in eine Testdatenbank).
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
from pymongo import MongoClient

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://127.0.0.1:27017")
BACKEND = Path(__file__).resolve().parent.parent
FLAG_COLLECTION = "system_flags"
FLAG_ID = "wartungsmodus"
S3_VARS = ("S3_ENDPOINT", "S3_BUCKET", "S3_ACCESS_KEY", "S3_SECRET_KEY")


# ------------------------------------------------------------ Vorabpruefung
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
    """Liefert (dumps: {name: (docs, metadata-Pfad)}, manifest|None, db_dir).
    Wirft bei jedem Fehler."""
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


def s3_konfiguriert() -> bool:
    return all(os.environ.get(v, "").strip() for v in S3_VARS)


def s3_objekte_im_backup(root: Path) -> list:
    s3_dir = root / "s3"
    if not s3_dir.is_dir():
        return []
    return sorted(f for f in s3_dir.rglob("*") if f.is_file())


def live_verzeichnisse() -> dict:
    """Live-Ordner der Datei-Speicher (BACKUP_*_DIR nur fuer Tests)."""
    return {
        "uploads": Path(os.environ.get("BACKUP_UPLOADS_DIR") or BACKEND / "uploads"),
        "local_storage": Path(os.environ.get("BACKUP_LOCAL_STORAGE_DIR")
                              or BACKEND / "local_storage"),
    }


# ------------------------------------------------------------------ Indexe
def erwartete_indexe(meta_path: Path) -> dict:
    """{indexname: (keys, optionen)} aus einer metadata.json (ohne _id_)."""
    if not meta_path.is_file():
        return {}
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    out = {}
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
        out[name] = (keys, opts)
    return out


def indexe_anlegen(coll, meta_path: Path) -> list:
    """Indexe laut metadata.json anlegen; liefert die Liste der Fehler."""
    fehler = []
    for name, (keys, opts) in erwartete_indexe(meta_path).items():
        try:
            coll.create_index(keys, name=name, **opts)
        except Exception as exc:  # noqa: BLE001
            fehler.append(f"Index {coll.name}.{name} nicht angelegt: {exc}")
    return fehler


def erwartete_anzahl(manifest, name: str, gelesen: int) -> int:
    e = (manifest or {}).get("collections", {}).get(name)
    return gelesen if e is None else int(e)


def pruefe_datenbank(db, dumps: dict, manifest) -> list:
    """Dokumentzahlen (gegen Manifest bzw. gelesene Dokumente) und Indexnamen
    jeder Collection in db pruefen. Liefert die Liste der Abweichungen."""
    probleme = []
    for name, (docs, meta_path) in dumps.items():
        soll = erwartete_anzahl(manifest, name, len(docs))
        ist = db[name].count_documents({})
        if ist != soll:
            probleme.append(f"{name}: {ist} Dokumente, erwartet {soll}")
        soll_idx = set(erwartete_indexe(meta_path))
        try:
            ist_idx = set(db[name].index_information())
        except Exception as exc:  # noqa: BLE001
            probleme.append(f"{name}: Indexe nicht lesbar ({exc})")
            continue
        fehlend = sorted(soll_idx - ist_idx)
        if fehlend:
            probleme.append(f"{name}: Index(e) fehlen: {', '.join(fehlend)}")
    return probleme


# ----------------------------------------------------------- Datei-Speicher
def dateien_bereitstellen(root: Path, live: dict, stamp: str, manifest):
    """uploads/ und local_storage/ aus dem Backup in Staging-Ordner NEBEN den
    Live-Ordnern kopieren (<live>.restore-<stamp>) und die Pruefsummen der
    kopierten Dateien gegen das Manifest pruefen.
    Liefert ({name: staging_pfad}, [fehler])."""
    files = (manifest or {}).get("files") or {}
    staging, fehler = {}, []
    for name, live_dir in live.items():
        quelle = root / name
        if not quelle.is_dir():
            continue
        ziel = live_dir.parent / f"{live_dir.name}.restore-{stamp}"
        try:
            if ziel.exists():
                shutil.rmtree(ziel)
            shutil.copytree(quelle, ziel)
        except OSError as exc:
            fehler.append(f"{name}: Staging nach {ziel} fehlgeschlagen — {exc}")
            continue
        staging[name] = ziel
        geprueft = 0
        for rel, info in files.items():
            if not rel.startswith(name + "/"):
                continue
            f = ziel / rel[len(name) + 1:]
            geprueft += 1
            if not f.is_file():
                fehler.append(f"{rel}: fehlt im Staging-Ordner")
            elif sha256_datei(f) != info.get("sha256"):
                fehler.append(f"{rel}: Pruefsumme nach dem Kopieren falsch")
        kopiert = sum(1 for f in ziel.rglob("*") if f.is_file())
        print(f"  {name}: {kopiert} Dateien nach {ziel.name} kopiert"
              + (f", {geprueft} Pruefsummen geprueft" if files else ""))
    return staging, fehler


def staging_entfernen(staging: dict) -> None:
    for p in staging.values():
        shutil.rmtree(p, ignore_errors=True)


def _verzeichnis_umbenennen(von: Path, nach: Path) -> None:
    """Ein Rename-Schritt (in Tests austauschbar)."""
    os.rename(von, nach)


def verzeichnisse_umschalten(staging: dict, live: dict, stamp: str):
    """Je Datei-Speicher: live -> <live>.vorher-<stamp>, Staging -> live.
    Liefert ([umgeschaltete Eintraege], fehler|None). Ein Eintrag:
    {name, live, vorher (None wenn es keinen Live-Ordner gab), staging}."""
    geschaltet = []
    for name, stg in staging.items():
        live_dir = live[name]
        vorher = (live_dir.parent / f"{live_dir.name}.vorher-{stamp}"
                  if live_dir.exists() else None)
        # Kollision vermeiden (CI 09/2026): gleicher Zeitstempel innerhalb
        # einer Sekunde -> Rename auf einen vorhandenen, nicht leeren Ordner
        # scheitert unter Linux (ENOTEMPTY). Eindeutigen Namen waehlen.
        n = 1
        while vorher is not None and vorher.exists():
            n += 1
            vorher = live_dir.parent / f"{live_dir.name}.vorher-{stamp}-{n}"
        try:
            live_dir.parent.mkdir(parents=True, exist_ok=True)
            if vorher is not None:
                _verzeichnis_umbenennen(live_dir, vorher)
            try:
                _verzeichnis_umbenennen(stg, live_dir)
            except Exception:
                if vorher is not None and not live_dir.exists():
                    _verzeichnis_umbenennen(vorher, live_dir)
                raise
        except Exception as exc:  # noqa: BLE001
            return geschaltet, f"Datei-Speicher {name}: {exc}"
        geschaltet.append({"name": name, "live": live_dir, "vorher": vorher,
                           "staging": stg})
        print(f"  {name}: umgeschaltet"
              + (f" (bisher -> {vorher.name})" if vorher else ""))
    return geschaltet, None


def verzeichnisse_zuruecknehmen(geschaltet: list) -> list:
    """Umschaltung der Ordner rueckgaengig machen (Live -> Staging zurueck,
    .vorher -> Live). Liefert die Liste der Fehler."""
    fehler = []
    for e in reversed(geschaltet):
        try:
            _verzeichnis_umbenennen(e["live"], e["staging"])
            if e["vorher"] is not None:
                _verzeichnis_umbenennen(e["vorher"], e["live"])
            shutil.rmtree(e["staging"], ignore_errors=True)
        except Exception as exc:  # noqa: BLE001
            fehler.append(f"Datei-Speicher {e['name']}: Rueckgaengig fehlgeschlagen — "
                          f"{exc} (Backup-Stand: {e['staging']}, bisheriger "
                          f"Stand: {e['vorher']})")
    return fehler


def s3_zurueckspielen(s3_dir: Path, objekte: list) -> int:
    import boto3
    c = boto3.client("s3", endpoint_url=os.environ["S3_ENDPOINT"],
                     aws_access_key_id=os.environ["S3_ACCESS_KEY"],
                     aws_secret_access_key=os.environ["S3_SECRET_KEY"],
                     region_name=os.environ.get("S3_REGION", "auto"))
    bucket = os.environ["S3_BUCKET"]
    n = 0
    try:
        for f in objekte:
            c.upload_file(str(f), bucket, str(f.relative_to(s3_dir)).replace("\\", "/"))
            n += 1
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"nach {n} von {len(objekte)} Objekten: {exc}") from exc
    return n


# ------------------------------------------------------------- Collections
def _rename_collection(client, von: str, nach: str) -> None:
    """Ein renameCollection-Schritt (in Tests austauschbar)."""
    client.admin.command("renameCollection", von, to=nach, dropTarget=True)


def collections_umschalten(client, ziel_name: str, tmp_name: str, alt_name: str,
                           namen: list, vorhandene: set):
    """Je Collection: ziel -> alt (falls vorhanden), tmp -> ziel.
    Liefert (umgeschaltet, halb, fehler). 'halb' ist die Collection, deren
    bisheriger Stand schon nach alt verschoben war, als der zweite Schritt
    scheiterte (None, wenn nichts halb ist)."""
    umgeschaltet = []
    for name in namen:
        alt_da = False
        try:
            if name in vorhandene:
                _rename_collection(client, f"{ziel_name}.{name}", f"{alt_name}.{name}")
                alt_da = True
            _rename_collection(client, f"{tmp_name}.{name}", f"{ziel_name}.{name}")
        except Exception as exc:  # noqa: BLE001
            return umgeschaltet, (name if alt_da else None), f"Collection {name}: {exc}"
        umgeschaltet.append(name)
    return umgeschaltet, None, None


def collections_zuruecknehmen(client, ziel_name: str, tmp_name: str, alt_name: str,
                              umgeschaltet: list, halb, vorhandene: set) -> list:
    """Alle umgeschalteten Collections zurueckdrehen: ziel -> tmp, alt -> ziel.
    Liefert die Liste der Fehler (leer = Zieldatenbank wieder vollstaendig
    auf dem bisherigen Stand)."""
    fehler = []
    for name in reversed(umgeschaltet):
        try:
            _rename_collection(client, f"{ziel_name}.{name}", f"{tmp_name}.{name}")
            if name in vorhandene:
                _rename_collection(client, f"{alt_name}.{name}", f"{ziel_name}.{name}")
        except Exception as exc:  # noqa: BLE001
            fehler.append(f"Collection {name}: {exc}")
    if halb:
        try:
            if halb in client[ziel_name].list_collection_names():
                _rename_collection(client, f"{ziel_name}.{halb}", f"{tmp_name}.{halb}")
            _rename_collection(client, f"{alt_name}.{halb}", f"{ziel_name}.{halb}")
        except Exception as exc:  # noqa: BLE001
            fehler.append(f"Collection {halb}: {exc}")
    return fehler


# ------------------------------------------------------------ Wartungsmodus
def wartungsmodus(ziel_db, aktiv: bool, grund: str = "Restore") -> None:
    """system_flags.wartungsmodus setzen/aufheben — die API antwortet bei
    aktiv=True mit 503 (Middleware im Backend)."""
    jetzt = datetime.now(timezone.utc).isoformat()
    coll = ziel_db[FLAG_COLLECTION]
    if aktiv:
        coll.replace_one({"_id": FLAG_ID},
                         {"_id": FLAG_ID, "aktiv": True, "grund": grund, "seit": jetzt},
                         upsert=True)
    else:
        coll.update_one({"_id": FLAG_ID}, {"$set": {"aktiv": False, "beendet": jetzt}})


def _wartungsmodus_befehl(ziel_name: str) -> str:
    return (f"mongosh --eval \"db.getSiblingDB('{ziel_name}').{FLAG_COLLECTION}"
            f".updateOne({{_id:'{FLAG_ID}'}},{{$set:{{aktiv:false}}}})\"")


# ------------------------------------------------------------------ Ablauf
def _rollback(client, ziel_name, tmp_name, alt_name, umgeschaltet, halb,
              vorhandene, geschaltet, grund) -> int:
    print(f"FEHLER: {grund}")
    print(f"ROLLBACK: {len(umgeschaltet) + (1 if halb else 0)} Collection(s) und "
          f"{len(geschaltet)} Datei-Speicher werden zurueckgedreht ...")
    fehler = collections_zuruecknehmen(client, ziel_name, tmp_name, alt_name,
                                       umgeschaltet, halb, vorhandene)
    fehler += verzeichnisse_zuruecknehmen(geschaltet)
    if fehler:
        print("!!! ROLLBACK UNVOLLSTAENDIG — Zieldatenbank/Datei-Speicher sind "
              "GEMISCHT. Manuell pruefen:")
        for f in fehler:
            print(f"!!!   - {f}")
        print(f"!!! Backup-Stand liegt in '{tmp_name}', bisheriger Stand in "
              f"'{alt_name}'. Der Wartungsmodus bleibt AKTIV; nach der "
              f"Bereinigung aufheben mit:\n    {_wartungsmodus_befehl(ziel_name)}")
        return 1
    client.drop_database(tmp_name)
    client.drop_database(alt_name)
    try:
        wartungsmodus(client[ziel_name], False)
    except Exception as exc:  # noqa: BLE001
        print(f"!!! Wartungsmodus konnte nicht aufgehoben werden ({exc}) — manuell:\n"
              f"    {_wartungsmodus_befehl(ziel_name)}")
    print(f"ROLLBACK OK: Zieldatenbank '{ziel_name}' und Datei-Speicher sind "
          f"vollstaendig auf dem bisherigen Stand; temporaere Datenbanken "
          f"entfernt, Wartungsmodus beendet.")
    return 1


def wiederherstellen(args) -> int:
    root = Path(args.backup_dir)
    if not root.is_dir():
        print(f"FEHLER: {root} ist kein Verzeichnis")
        return 1
    print(f"1/6 Vorabpruefung von {root} ...")
    try:
        dumps, manifest, db_dir = pruefe_backup(root, args.allow_no_manifest)
    except Exception as exc:  # noqa: BLE001
        print(f"FEHLER: {exc}")
        print("Es wurde NICHTS veraendert.")
        return 1
    fehlend = [str(x) for x in ((manifest or {}).get("unvollstaendig") or [])]
    if fehlend:
        if not args.notfall_unvollstaendig_akzeptieren:
            print("FEHLER: Dieses Backup ist als UNVOLLSTAENDIG markiert. Es fehlt:")
            for f in fehlend:
                print(f"  - {f}")
            print("Einspielen nur im Notfall mit --notfall-unvollstaendig-akzeptieren "
                  "(diese Daten fehlen dann nach dem Restore).")
            print("Es wurde NICHTS veraendert.")
            return 1
        print("!!! WARNUNG: UNVOLLSTAENDIGES Backup wird auf ausdruecklichen Wunsch "
              "(Notfall) eingespielt. Folgendes FEHLT und ist nach dem Restore "
              "NICHT vorhanden:")
        for f in fehlend:
            print(f"!!!   - {f}")
    if manifest:
        print(f"  Konsistenz laut Manifest: {manifest.get('konsistenz', 'unbekannt')}")
    s3_objekte = [] if args.nur_datenbank else s3_objekte_im_backup(root)
    s3_aktiv = False
    if s3_objekte:
        if s3_konfiguriert():
            s3_aktiv = True
            print(f"  s3: {len(s3_objekte)} Objekte werden nach Bucket "
                  f"{os.environ['S3_BUCKET']} zurueckgespielt")
        elif args.ohne_s3:
            print(f"  WARNUNG: {len(s3_objekte)} S3-Objekte im Backup werden wegen "
                  f"--ohne-s3 NICHT zurueckgespielt")
        else:
            print(f"FEHLER: Backup enthaelt {len(s3_objekte)} S3-Objekte, aber S3 ist "
                  f"hier nicht konfiguriert ({', '.join(S3_VARS)}). Entweder S3 "
                  f"konfigurieren oder mit --ohne-s3 bewusst ohne S3-Objekte "
                  f"wiederherstellen.")
            print("Es wurde NICHTS veraendert.")
            return 1
    if FLAG_COLLECTION in dumps:
        dumps.pop(FLAG_COLLECTION)
        print(f"  Hinweis: {FLAG_COLLECTION} (Betriebs-Flags) wird nicht zurueckgespielt")
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
    tmp, ziel = client[tmp_name], client[args.db]
    live = live_verzeichnisse()
    staging = {}

    print(f"2/6 Laden in temporaere Datenbank {tmp_name} ...")
    index_fehler, total = [], 0
    try:
        for name, (docs, meta_path) in dumps.items():
            if docs:
                tmp[name].insert_many(docs, ordered=False)
            else:
                tmp.create_collection(name)
            index_fehler += indexe_anlegen(tmp[name], meta_path)
            total += len(docs)
    except Exception as exc:  # noqa: BLE001
        print(f"FEHLER beim Laden: {exc} — temporaere Datenbank wird entfernt, "
              f"Zieldatenbank '{args.db}' ist unveraendert.")
        client.drop_database(tmp_name)
        return 1

    print("3/6 Pruefung VOR dem Umschalten (Dokumentzahlen, Indexe, Datei-Pruefsummen) ...")
    probleme = index_fehler + pruefe_datenbank(tmp, dumps, manifest)
    if not args.nur_datenbank:
        staging, f = dateien_bereitstellen(root, live, stamp, manifest)
        probleme += f
    if probleme:
        print("FEHLER: Pruefung vor dem Umschalten fehlgeschlagen:")
        for p in probleme[:20]:
            print(f"  - {p}")
        staging_entfernen(staging)
        client.drop_database(tmp_name)
        print(f"Temporaere Datenbank und Staging-Ordner entfernt; Zieldatenbank "
              f"'{args.db}' und Datei-Speicher sind unveraendert.")
        return 1
    print(f"  OK: {len(dumps)} Collections, {total} Dokumente, Indexe vollstaendig"
          + (f", {len(staging)} Datei-Speicher bereitgestellt" if staging else ""))

    print(f"4/6 Wartungsmodus fuer '{args.db}' setzen ...")
    wartungsmodus(ziel, True)
    print(f"  {FLAG_COLLECTION}.{FLAG_ID} aktiv — die API antwortet jetzt mit 503")
    n_s3 = 0
    if s3_aktiv:
        print(f"  s3: {len(s3_objekte)} Objekte hochladen ...")
        try:
            n_s3 = s3_zurueckspielen(root / "s3", s3_objekte)
        except Exception as exc:  # noqa: BLE001
            print(f"FEHLER beim Zurueckspielen nach S3: {exc}")
            staging_entfernen(staging)
            client.drop_database(tmp_name)
            wartungsmodus(ziel, False)
            print(f"Datenbank '{args.db}' und lokale Datei-Speicher sind unveraendert "
                  f"(bereits hochgeladene S3-Objekte bleiben im Bucket). "
                  f"Wartungsmodus beendet.")
            return 1

    print(f"5/6 Umschalten (bisheriger Stand -> {alt_name} bzw. *.vorher-{stamp}) ...")
    vorhandene = set(ziel.list_collection_names())
    geschaltet, fehler = verzeichnisse_umschalten(staging, live, stamp)
    umgeschaltet, halb = [], None
    if fehler is None:
        umgeschaltet, halb, fehler = collections_umschalten(
            client, args.db, tmp_name, alt_name, list(dumps), vorhandene)
    if fehler is None:
        print(f"  {len(umgeschaltet)} Collections umgeschaltet")
        print("6/6 Kontrolle nach dem Umschalten ...")
        abweichungen = pruefe_datenbank(ziel, dumps, manifest)
        if abweichungen:
            fehler = ("Kontrolle nach dem Umschalten: "
                      + "; ".join(abweichungen[:10]))
    if fehler is not None:
        return _rollback(client, args.db, tmp_name, alt_name, umgeschaltet, halb,
                         vorhandene, geschaltet, fehler)

    client.drop_database(tmp_name)
    try:
        wartungsmodus(ziel, False)
    except Exception as exc:  # noqa: BLE001
        print(f"!!! Wartungsmodus konnte nicht aufgehoben werden ({exc}) — manuell:\n"
              f"    {_wartungsmodus_befehl(args.db)}")
        return 1
    extra = sorted(vorhandene - set(dumps) - {FLAG_COLLECTION})
    if extra:
        print(f"  Hinweis: nicht im Backup enthalten und daher unveraendert "
              f"belassen: {', '.join(extra)}")
    n_files = sum(1 for e in geschaltet for f in e["live"].rglob("*") if f.is_file())
    print(f"RESTORE OK: {len(dumps)} Collections, {total} Dokumente, "
          f"{n_files} Dateien, {n_s3} S3-Objekte -> {args.db}; Wartungsmodus beendet.")
    print(f"Der vorherige Datenbestand liegt in '{alt_name}'"
          + ("".join(f", Ordner {e['vorher']}" for e in geschaltet if e["vorher"]))
          + ". Wenn alles passt, entfernen mit:  mongosh --eval "
          f"\"db.getSiblingDB('{alt_name}').dropDatabase()\"")
    print("HINWEIS: Eindeutigkeits-Indizes prueft das Backend beim naechsten "
          "Start (ensure_indexes) — nach dem Restore einmal neu starten.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="AutoSchnell-Backup wiederherstellen")
    ap.add_argument("backup_dir")
    ap.add_argument("--db", default=os.environ.get("DB_NAME", "autoschnell"))
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--dry-run", action="store_true",
                    help="nur pruefen, nichts veraendern")
    ap.add_argument("--allow-no-manifest", action="store_true")
    ap.add_argument("--keep-old", action="store_true", default=True,
                    help="bisherige Collections als <db>__vorher_<stamp> behalten (Standard)")
    ap.add_argument("--notfall-unvollstaendig-akzeptieren", action="store_true",
                    help="ein als UNVOLLSTAENDIG markiertes Backup trotzdem "
                         "einspielen (nur im Notfall)")
    ap.add_argument("--ohne-s3", action="store_true",
                    help="S3-Objekte im Backup bewusst NICHT zurueckspielen")
    ap.add_argument("--nur-datenbank", action="store_true",
                    help="nur die Datenbank; Datei-Speicher (uploads, "
                         "local_storage, S3) unangetastet lassen")
    args = ap.parse_args(argv)
    return wiederherstellen(args)


if __name__ == "__main__":
    sys.exit(main())
