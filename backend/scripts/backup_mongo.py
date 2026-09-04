# -*- coding: utf-8 -*-
"""Tägliches Backup für AutoSchnell: MongoDB + Datei-Speicher (+ Offsite-Kopie).

Schreibt jede Collection als <name>.bson.gz (mongodump-kompatibles Roh-BSON,
gzip) plus <name>.metadata.json (Indexe), spiegelt die Datei-Speicher
(uploads/, local_storage/ und — falls konfiguriert — den S3-Bucket) und
legt eine manifest.json mit SHA-256-Prüfsummen und Dokumentzahlen an.
restore_mongo.py verweigert die Wiederherstellung, wenn eine Prüfsumme
nicht stimmt.

Konsistenz (Go-Live-Audit): Läuft MongoDB als Replica Set, werden ALLE
Collections in EINER Snapshot-Session gelesen — ein gemeinsamer Zeitpunkt
für die ganze Datenbank (Manifest "konsistenz": "snapshot"). Ein
Standalone-Server kann das nicht; dort wird Collection für Collection
gelesen ("konsistenz": "best-effort (standalone)").

Offsite-Kopie: Ist BACKUP_S3_BUCKET gesetzt, wird das fertige Backup als
tar.gz (serverseitig AES256-verschlüsselt, optional mit Object Lock)
hochgeladen und im Manifest unter "offsite" vermerkt. Schlägt der Upload
fehl, gilt das Backup als UNVOLLSTAENDIG.

Ergebnis-Status ("BACKUP OK" nur, wenn wirklich alles gesichert wurde):
  BACKUP OK               Exit 0  — Datenbank, alle Datei-Speicher und
                                    (falls konfiguriert) die Offsite-Kopie
  BACKUP UNVOLLSTAENDIG   Exit 2  — Datenbank gesichert, aber mindestens
                                    ein Datei-Speicher oder die Offsite-
                                    Kopie fehlt (siehe manifest.unvollstaendig)
  FEHLER                  Exit 1  — Datenbank nicht gesichert

Aufbewahrung: lokal die letzten 14 Backups; offsite die letzten
BACKUP_S3_KEEP (Standard 30) Archive, best effort.

Umgebung:
  MONGO_URL, DB_NAME, BACKUP_DIR
  S3_ENDPOINT, S3_BUCKET, S3_ACCESS_KEY, S3_SECRET_KEY, S3_REGION
      Datei-Speicher der App — wird IN das Backup gespiegelt.
  BACKUP_S3_BUCKET            Offsite-Ziel (EIGENER Bucket, nicht S3_BUCKET);
                              Client mit denselben S3_*-Zugangsdaten.
  BACKUP_S3_PREFIX            Schlüssel-Präfix, Standard "autoschnell-backups/"
  BACKUP_S3_OBJECT_LOCK_DAYS  > 0: ObjectLockMode COMPLIANCE bis +N Tage.
                              Der Bucket muss MIT Object Lock angelegt sein.
  BACKUP_S3_KEEP              Offsite-Aufbewahrung in Archiven (30)
  BACKUP_UPLOADS_DIR, BACKUP_LOCAL_STORAGE_DIR   nur für Tests

Aufruf:  python -X utf8 backup_mongo.py [--dir <Zielordner>]
"""
import argparse
import gzip
import hashlib
import json
import os
import re
import shutil
import sys
import tarfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import bson
from bson import json_util
from pymongo import MongoClient

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://127.0.0.1:27017")
DB_NAME = os.environ.get("DB_NAME", "autoschnell")
KEEP = 14
DEFAULT_DIR = Path(os.environ.get("BACKUP_DIR") or r"C:\AutoSchnell-Backups")
BACKEND = Path(__file__).resolve().parent.parent
UPLOADS_DIR = Path(os.environ.get("BACKUP_UPLOADS_DIR") or BACKEND / "uploads")
LOCAL_STORAGE_DIR = Path(os.environ.get("BACKUP_LOCAL_STORAGE_DIR")
                         or BACKEND / "local_storage")
MANIFEST_VERSION = 3
OFFSITE_PREFIX_DEFAULT = "autoschnell-backups/"
OFFSITE_KEEP_DEFAULT = 30
_OFFSITE_ARCHIV = re.compile(r"autoschnell-\d{4}-\d{2}-\d{2}_\d{4}\.tar\.gz$")


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


# ---------------------------------------------------------------- Datenbank
def ist_replica_set(client) -> bool:
    """True, wenn der Server Mitglied eines Replica Sets ist — nur dann sind
    Snapshot-Reads (readConcern snapshot) moeglich."""
    for cmd in ("hello", "isMaster"):
        try:
            return bool(client.admin.command(cmd).get("setName"))
        except Exception:  # noqa: BLE001
            continue
    return False


def dump_collection(coll, out_dir: Path, session=None) -> int:
    n = 0
    with gzip.open(out_dir / f"{coll.name}.bson.gz", "wb") as fh:
        for doc in coll.find({}, session=session):
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


def wartung_setzen(db, an: bool, logfile: Path) -> bool:
    """Wartungsmodus schalten (Audit 09/2026, Befund "Backup nicht stimmig").

    Ohne Replica Set gibt es keine Snapshot-Sicht. Mit --wartung pausiert das
    Backend fuer die Dauer des Laufs alle Schreibzugriffe (die Middleware
    antwortet mit 503), sodass die einzelnen Collections zusammenpassen.
    Liefert True, wenn geschaltet werden konnte."""
    try:
        db.system_flags.update_one(
            {"_id": "wartungsmodus"},
            {"$set": {"aktiv": bool(an), "grund": "Datensicherung laeuft",
                      "gesetzt_am": datetime.now(timezone.utc).isoformat()}},
            upsert=True)
        log(f"  Wartungsmodus {'AN' if an else 'AUS'} (Schreibpause)", logfile)
        return True
    except Exception as exc:  # noqa: BLE001
        log(f"  WARNUNG: Wartungsmodus konnte nicht geschaltet werden: {exc}", logfile)
        return False


def dump_datenbank(client, db, names, target: Path, logfile: Path):
    """Alle Collections nach target schreiben. Liefert (counts, konsistenz).

    Replica Set: EINE Snapshot-Session fuer alle Collections, d. h. alle
    Dateien zeigen denselben Zeitpunkt. Standalone: Collection fuer
    Collection (Aenderungen waehrend des Laufs koennen dazwischen liegen).
    Schlaegt das Snapshot-Lesen fehl (z. B. SnapshotTooOld bei sehr grossen
    Datenbanken), wird auf das Collection-fuer-Collection-Verfahren
    zurueckgefallen statt gar kein Backup zu haben."""
    if ist_replica_set(client):
        counts = {}
        try:
            with client.start_session(snapshot=True) as s:
                for name in names:
                    counts[name] = dump_collection(db[name], target, session=s)
                    log(f"  {name}: {counts[name]} Dokumente (Snapshot)", logfile)
            return counts, "snapshot"
        except Exception as exc:  # noqa: BLE001
            log(f"  WARNUNG: Snapshot-Lesen fehlgeschlagen ({exc}) — Fallback: "
                f"Collection fuer Collection", logfile)
            konsistenz = "best-effort (snapshot fehlgeschlagen)"
    else:
        konsistenz = "best-effort (standalone)"
        log("  WARNUNG: MongoDB laeuft OHNE Replica Set — die Sicherung wird "
            "Collection fuer Collection gelesen und ist damit nicht auf eine "
            "Sekunde genau in sich stimmig. Abhilfe: Replica Set einrichten "
            "(mongod --replSet rs0) ODER die Sicherung mit --wartung starten "
            "(pausiert Schreibzugriffe fuer die Dauer des Laufs).", logfile)
    counts = {}
    for name in names:
        counts[name] = dump_collection(db[name], target)
        log(f"  {name}: {counts[name]} Dokumente", logfile)
    return counts, konsistenz


# ------------------------------------------------------------ Datei-Speicher
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


def _s3_client(endpoint_pflicht: bool = True):
    import boto3
    endpoint = os.environ.get("S3_ENDPOINT", "").strip() or None
    if endpoint_pflicht and not endpoint:
        raise KeyError("S3_ENDPOINT")
    return boto3.client(
        "s3", endpoint_url=endpoint,
        aws_access_key_id=os.environ["S3_ACCESS_KEY"],
        aws_secret_access_key=os.environ["S3_SECRET_KEY"],
        region_name=os.environ.get("S3_REGION") or ("auto" if endpoint else None))


def spiegle_s3(ziel: Path, logfile: Path) -> int:
    """Alle Objekte des konfigurierten S3-Buckets herunterladen (Runde 5:
    vorher wurden bei S3-Betrieb NUR lokale Ordner gesichert)."""
    client = _s3_client()
    bucket = os.environ["S3_BUCKET"]
    # Liegt die Offsite-Kopie (entgegen der Empfehlung) im selben Bucket, die
    # eigenen Backup-Archive nicht mitsichern — sonst enthielte jedes Backup
    # alle vorherigen.
    skip_prefix = offsite_prefix() if \
        os.environ.get("BACKUP_S3_BUCKET", "").strip() == bucket else None
    n = 0
    for seite in client.get_paginator("list_objects_v2").paginate(Bucket=bucket):
        for obj in seite.get("Contents") or []:
            key = obj["Key"]
            if skip_prefix and key.startswith(skip_prefix):
                continue
            dst = ziel / key
            dst.parent.mkdir(parents=True, exist_ok=True)
            client.download_file(bucket, key, str(dst))
            n += 1
    log(f"  S3-Bucket {bucket}: {n} Objekte gesichert", logfile)
    return n


# ------------------------------------------------------------- Offsite-Kopie
def offsite_konfiguriert() -> bool:
    return bool(os.environ.get("BACKUP_S3_BUCKET", "").strip())


def offsite_prefix() -> str:
    return os.environ.get("BACKUP_S3_PREFIX", OFFSITE_PREFIX_DEFAULT)


def backup_endpoint() -> str:
    """Adresse fuer die Offsite-Kopie. Faellt auf S3_ENDPOINT zurueck.

    Eigene Angabe noetig, wenn der Sicherungs-Bucket in einer anderen
    Zone liegt: bei Cloudflare R2 hat ein Bucket mit EU-Zone die Adresse
    <konto>.eu.r2.cloudflarestorage.com und ist ueber die Standard-
    Adresse NICHT erreichbar (404)."""
    return (os.environ.get("BACKUP_S3_ENDPOINT", "").strip()
            or os.environ.get("S3_ENDPOINT", "").strip())


def _backup_s3_client():
    """S3-Client fuer die Offsite-Kopie (gleiche Zugangsdaten wie der
    Datei-Speicher, ggf. eigene Adresse; in Tests austauschbar)."""
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))
    from s3_kompatibel import s3_client
    return s3_client(endpoint=backup_endpoint())


def _object_lock_tage() -> int:
    raw = os.environ.get("BACKUP_S3_OBJECT_LOCK_DAYS", "").strip()
    try:
        return max(0, int(raw)) if raw else 0
    except ValueError:
        return 0


def _offsite_keep() -> int:
    raw = os.environ.get("BACKUP_S3_KEEP", "").strip()
    try:
        n = int(raw) if raw else OFFSITE_KEEP_DEFAULT
    except ValueError:
        n = OFFSITE_KEEP_DEFAULT
    return max(1, n)


def offsite_hochladen(final_dir: Path, logfile: Path) -> dict:
    """Backup-Ordner als tar.gz packen und in den Offsite-Bucket laden.
    Liefert die Angaben fuers Manifest; wirft bei jedem Fehler."""
    bucket = os.environ["BACKUP_S3_BUCKET"].strip()
    key = f"{offsite_prefix()}{final_dir.name}.tar.gz"
    archiv = final_dir.parent / f".tmp-{final_dir.name}.tar.gz"
    try:
        with tarfile.open(archiv, "w:gz") as tar:
            tar.add(final_dir, arcname=final_dir.name)
        groesse = archiv.stat().st_size
        digest = sha256_datei(archiv)
        # AES256 nur, wo der Anbieter die Kopfzeile akzeptiert (R2 nicht —
        # R2 verschluesselt ohnehin selbst).
        from s3_kompatibel import sse_optionen
        extra = {"Metadata": {"sha256": digest}}
        extra.update(sse_optionen(backup_endpoint()))
        bis = None
        lock_tage = _object_lock_tage()
        if lock_tage:
            bis = datetime.now(timezone.utc) + timedelta(days=lock_tage)
            extra["ObjectLockMode"] = "COMPLIANCE"
            extra["ObjectLockRetainUntilDate"] = bis
        client = _backup_s3_client()
        client.upload_file(str(archiv), bucket, key, ExtraArgs=extra)
        kopf = client.head_object(Bucket=bucket, Key=key)
        if int(kopf.get("ContentLength", -1)) != groesse:
            raise RuntimeError(f"Objekt {key} hat {kopf.get('ContentLength')} Bytes, "
                               f"erwartet {groesse}")
        info = {"bucket": bucket, "key": key,
                "uploaded_at": datetime.now(timezone.utc).isoformat(),
                "bytes": groesse, "sha256": digest}
        if bis is not None:
            info["object_lock_bis"] = bis.isoformat()
        log(f"  Offsite: s3://{bucket}/{key} ({groesse / 1e6:.1f} MB, AES256"
            + (f", Object Lock bis {bis:%Y-%m-%d}" if bis else "") + ")", logfile)
        return info
    finally:
        archiv.unlink(missing_ok=True)


def offsite_rotieren(logfile: Path) -> None:
    """Offsite-Archive bis auf die letzten BACKUP_S3_KEEP loeschen — best
    effort (Object-Lock-gesperrte Objekte bleiben ohnehin bis zum Ablauf)."""
    keep = _offsite_keep()
    bucket = os.environ["BACKUP_S3_BUCKET"].strip()
    try:
        client = _backup_s3_client()
        keys = []
        for seite in client.get_paginator("list_objects_v2").paginate(
                Bucket=bucket, Prefix=offsite_prefix()):
            for obj in seite.get("Contents") or []:
                if _OFFSITE_ARCHIV.search(obj["Key"]):
                    keys.append(obj["Key"])
        for key in sorted(keys)[:-keep]:
            client.delete_object(Bucket=bucket, Key=key)
            log(f"Altes Offsite-Backup entfernt: {key}", logfile)
    except Exception as exc:  # noqa: BLE001
        log(f"  Hinweis: Offsite-Rotation nicht moeglich — {exc}", logfile)


# ------------------------------------------------------------------ Ablauf
def schreibe_manifest(ordner: Path, manifest: dict) -> None:
    tmp = ordner / "manifest.json.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, ordner / "manifest.json")


def rotate(base: Path, logfile: Path) -> None:
    dumps = sorted([p for p in base.iterdir()
                    if p.is_dir() and p.name.startswith("autoschnell-")])
    for old in dumps[:-KEEP]:
        shutil.rmtree(old, ignore_errors=True)
        log(f"Altes Backup entfernt: {old.name}", logfile)


def backup_erstellen(base: Path, db_name: str = None, mongo_url: str = None,
                     wartung: bool = False) -> int:
    """Ein komplettes Backup nach base/autoschnell-<stamp>. Exit-Code wie main()."""
    db_name = db_name or DB_NAME
    mongo_url = mongo_url or MONGO_URL
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
    target = tmp_dir / db_name
    target.mkdir(parents=True, exist_ok=True)

    try:
        client = MongoClient(mongo_url, serverSelectionTimeoutMS=10000)
        db = client[db_name]
        names = sorted(db.list_collection_names())
    except Exception as exc:
        log(f"FEHLER: MongoDB nicht erreichbar — {exc}", logfile)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return 1

    # Schreibpause nur, wenn ausdruecklich gewuenscht (Standalone-Mongo):
    # dann pausiert das Backend Schreibzugriffe, damit die Collections
    # zusammenpassen (Audit 09/2026).
    pause = wartung and wartung_setzen(db, True, logfile)
    try:
        counts, konsistenz = dump_datenbank(client, db, names, target, logfile)
    except Exception as exc:  # noqa: BLE001
        log(f"FEHLER beim Sichern der Datenbank: {exc}", logfile)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return 1
    finally:
        if pause:
            wartung_setzen(db, False, logfile)
    if pause:
        konsistenz = "stimmig (Schreibpause)"
    log(f"  Konsistenz: {konsistenz}", logfile)

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
        "version": MANIFEST_VERSION, "db": db_name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "konsistenz": konsistenz,
        "collections": counts, "files": dateien,
        "unvollstaendig": unvollstaendig,
    }
    schreibe_manifest(tmp_dir, manifest)
    size_mb = sum(v["bytes"] for v in dateien.values()) / 1e6
    tmp_dir.rename(final_dir)          # atomarer Abschluss des lokalen Backups

    # ---- Offsite-Kopie (nach dem lokalen Abschluss; Manifest wird danach
    #      um "offsite" bzw. den Fehler ergaenzt) ----
    if offsite_konfiguriert():
        try:
            manifest["offsite"] = offsite_hochladen(final_dir, logfile)
        except Exception as exc:  # noqa: BLE001
            unvollstaendig.append(f"offsite: {exc}")
            log(f"  WARNUNG: Offsite-Kopie NICHT hochgeladen — {exc}", logfile)
        manifest["unvollstaendig"] = unvollstaendig
        schreibe_manifest(final_dir, manifest)
        if manifest.get("offsite"):
            offsite_rotieren(logfile)

    total_docs = sum(counts.values())
    rotate(base, logfile)
    if unvollstaendig:
        log(f"BACKUP UNVOLLSTAENDIG: {len(names)} Collections, {total_docs} "
            f"Dokumente, {n_files} Dateien, {size_mb:.1f} MB -> {final_dir.name}; "
            f"NICHT gesichert: {'; '.join(unvollstaendig)}", logfile)
        return 2
    log(f"BACKUP OK: {len(names)} Collections, {total_docs} Dokumente, "
        f"{n_files} Dateien, {size_mb:.1f} MB ({konsistenz}"
        + (", offsite" if manifest.get("offsite") else "") + f") -> {final_dir.name}",
        logfile)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="AutoSchnell-Backup: MongoDB + Datei-Speicher")
    ap.add_argument("--dir", default=str(DEFAULT_DIR))
    ap.add_argument("--wartung", action="store_true",
                    help="Schreibzugriffe waehrend der Sicherung pausieren "
                         "(noetig fuer eine stimmige Sicherung ohne Replica Set)")
    args = ap.parse_args(argv)
    return backup_erstellen(Path(args.dir), wartung=args.wartung)


if __name__ == "__main__":
    sys.exit(main())
