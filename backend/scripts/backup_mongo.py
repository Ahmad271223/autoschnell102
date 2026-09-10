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
Runde 21 (Pruefbefund Backup A): Scheitert das Snapshot-Lesen, wird es bis
zu BACKUP_SNAPSHOT_VERSUCHE mal wiederholt. Bleibt es beim Rueckfall auf
Collection fuer Collection, ist das Backup INKONSISTENT (Manifest-Feld
"inkonsistent", Exit 3): es zaehlt nie als letzter guter Stand, schuetzt
sich nicht in der Rotation und wird beim Restore abgelehnt. Nennt
MONGO_URL ein Replica Set (replicaSet=...) oder ist BACKUP_SNAPSHOT_PFLICHT
gesetzt, wird immer der Snapshot versucht; ein Einzelserver-Lauf ist dann
ebenfalls INKONSISTENT.

Index-Metadaten (Runde 21, Befund B): <name>.metadata.json ist fuer JEDE
Collection Pflicht. Lassen sich die Indexe nicht lesen oder die Datei
nicht vollstaendig schreiben (atomar geschrieben und zurueckgelesen),
scheitert das Backup (Exit 1) — ohne Index-Metadaten fehlen nach einem
Restore Unique- und TTL-Indexe unbemerkt.

Offsite-Kopie: Ist BACKUP_S3_BUCKET gesetzt, wird das fertige Backup als
tar.gz (serverseitig verschlüsselt, optional mit Object Lock)
hochgeladen und im Manifest unter "offsite" vermerkt. Schlägt der Upload
fehl, gilt das Backup als UNVOLLSTAENDIG.

Ergebnis-Status ("BACKUP OK" nur, wenn wirklich alles gesichert wurde):
  BACKUP OK               Exit 0  — Datenbank (stimmig bzw. zugelassener
                                    Einzelserver-Lauf), alle Datei-Speicher
                                    und (falls konfiguriert) die Offsite-Kopie
  BACKUP UNVOLLSTAENDIG   Exit 2  — Datenbank gesichert, aber mindestens
                                    ein Datei-Speicher oder die Offsite-
                                    Kopie fehlt (siehe manifest.unvollstaendig)
  BACKUP INKONSISTENT     Exit 3  — Daten gesichert, aber NICHT auf einen
                                    gemeinsamen Zeitpunkt (siehe
                                    manifest.inkonsistent); Vorrang vor Exit 2
  FEHLER                  Exit 1  — Datenbank oder Index-Metadaten nicht
                                    gesichert

Aufbewahrung: lokal die letzten 14 Backups, dazu immer das juengste GUTE
(auch wenn es aelter ist); offsite die letzten BACKUP_S3_KEEP (Standard
30) Archive, best effort — rotiert wird offsite nur nach einem guten Lauf.

Umgebung:
  MONGO_URL, DB_NAME, BACKUP_DIR
  BACKUP_SNAPSHOT_VERSUCHE    Snapshot-Versuche vor dem Rueckfall (3)
  BACKUP_SNAPSHOT_PAUSE_S     Pause vor Versuch n: n-1 mal so viele Sekunden (5)
  BACKUP_SNAPSHOT_PFLICHT     true: Snapshot ist Pflicht (siehe oben)
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
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Als Skript gestartet kennt Python nur den scripts-Ordner. Damit
# Module aus dem Projektordner (s3_kompatibel) importierbar sind, muss
# er VOR dem ersten solchen Import im Suchpfad stehen.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bson
from bson import json_util
from s3_kompatibel import s3_client, sse_optionen
from backup_bewertung import (KONSISTENZ_RUECKFALL, KONSISTENZ_SCHREIBPAUSE,
                              KONSISTENZ_SNAPSHOT, KONSISTENZ_STANDALONE,
                              ist_gut, metadaten_mangel, snapshot_pflicht)
from pymongo import MongoClient

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://127.0.0.1:27017")
DB_NAME = os.environ.get("DB_NAME", "autoschnell")
KEEP = 14
# Runde 21 (Nebenbefund Schreibpause): die WartungsmodusMiddleware im
# Backend liest das Flag nur alle 5 s neu. Erst nach dieser Frist sind
# Schreibzugriffe sicher pausiert.
WARTUNG_WARTEN_S = 6
DEFAULT_DIR = Path(os.environ.get("BACKUP_DIR") or r"C:\AutoSchnell-Backups")
BACKEND = Path(__file__).resolve().parent.parent
UPLOADS_DIR = Path(os.environ.get("BACKUP_UPLOADS_DIR") or BACKEND / "uploads")
LOCAL_STORAGE_DIR = Path(os.environ.get("BACKUP_LOCAL_STORAGE_DIR")
                         or BACKEND / "local_storage")
MANIFEST_VERSION = 4          # Runde 21: Feld "inkonsistent", Pflicht-Indexdaten
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
def ist_replica_set(client):
    """True, wenn der Server Mitglied eines Replica Sets ist — nur dann sind
    Snapshot-Reads (readConcern snapshot) moeglich. False: Einzelserver.

    Runde 21 (Nebenbefund): None, wenn weder hello noch isMaster antworten.
    Bisher hiess das still "Standalone", und ein Replica Set wurde ohne
    Snapshot als gutes Backup gesichert. Unbekannt fuehrt jetzt zum
    Snapshot-Versuch; scheitert er, ist das Backup INKONSISTENT."""
    for cmd in ("hello", "isMaster"):
        try:
            return bool(client.admin.command(cmd).get("setName"))
        except Exception:  # noqa: BLE001
            continue
    return None


class IndexMetadatenFehler(RuntimeError):
    """Runde 21 (Pruefbefund Backup B): Index-Metadaten einer Collection
    nicht lesbar oder nicht vollstaendig geschrieben. Fuehrt immer zu
    FEHLER (Exit 1) — auch im Snapshot-Zweig, nie zum Rueckfall auf
    Collection fuer Collection."""


def _metadaten_lesen(pfad: Path) -> dict:
    """metadata.json laden und pruefen; wirft IndexMetadatenFehler."""
    try:
        meta = json_util.loads(pfad.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise IndexMetadatenFehler(f"{pfad.name} fehlt oder ist unlesbar ({exc})") from exc
    grund = metadaten_mangel(meta)
    if grund:
        raise IndexMetadatenFehler(f"{pfad.name} ungueltig ({grund})")
    return meta


def indexe_sichern(coll, out_dir: Path) -> list:
    """<name>.metadata.json atomar schreiben und zuruecklesen.

    Runde 21 (Befund B): Fehler wurden hier bisher verschluckt — dann fehlte
    die Datei (Restore erwartete "keine Indexe") oder sie war halb
    geschrieben und wanderte mit Pruefsumme ins Manifest. Jetzt: eine
    Wiederholung bei einem Lesefehler, danach IndexMetadatenFehler.
    Liefert die Indexnamen."""
    letzter = None
    for _ in range(2):
        try:
            indexes = list(coll.list_indexes())
            break
        except Exception as exc:  # noqa: BLE001
            letzter = exc
    else:
        raise IndexMetadatenFehler(f"{coll.name}: Indexe nicht lesbar ({letzter})")
    meta = {"options": {}, "collectionName": coll.name,
            "indexes": [json.loads(json_util.dumps(i)) for i in indexes]}
    ziel = out_dir / f"{coll.name}.metadata.json"
    tmp = out_dir / f"{coll.name}.metadata.json.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(meta, fh, ensure_ascii=False)
        os.replace(tmp, ziel)
    except Exception as exc:  # noqa: BLE001
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise IndexMetadatenFehler(
            f"{coll.name}: metadata.json nicht vollstaendig geschrieben ({exc})") from exc
    zurueck = _metadaten_lesen(ziel)
    namen = [i.get("name") for i in zurueck["indexes"]]
    if namen != [i.get("name") for i in meta["indexes"]]:
        raise IndexMetadatenFehler(f"{coll.name}: zurueckgelesene Indexe weichen ab "
                                   f"({namen})")
    return namen


def dump_collection(coll, out_dir: Path, session=None) -> int:
    n = 0
    with gzip.open(out_dir / f"{coll.name}.bson.gz", "wb") as fh:
        for doc in coll.find({}, session=session):
            fh.write(bson.encode(doc))
            n += 1
    indexe_sichern(coll, out_dir)
    return n


def indexe_gegenpruefen(target: Path, namen) -> dict:
    """Nach dem Dump: zu JEDER Collection eine gueltige metadata.json.
    Liefert {collection: [indexnamen]} fuers Manifest; wirft
    IndexMetadatenFehler."""
    out = {}
    for name in namen:
        meta = _metadaten_lesen(target / f"{name}.metadata.json")
        out[name] = [i.get("name") for i in meta["indexes"]]
    return out


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


def _snapshot_versuche() -> int:
    try:
        return max(1, int(os.environ.get("BACKUP_SNAPSHOT_VERSUCHE", "").strip() or 3))
    except ValueError:
        return 3


def _snapshot_pause_s() -> float:
    try:
        return max(0.0, float(os.environ.get("BACKUP_SNAPSHOT_PAUSE_S", "").strip() or 5))
    except ValueError:
        return 5.0


def _teildateien_entfernen(target: Path) -> None:
    """Reste eines abgebrochenen Versuchs entfernen, damit kein Teil eines
    frueheren Zeitstands im Backup landet."""
    for muster in ("*.bson.gz", "*.metadata.json", "*.tmp"):
        for f in target.glob(muster):
            f.unlink(missing_ok=True)


def dump_datenbank(client, db, names, target: Path, logfile: Path,
                   pflicht: bool = False):
    """Alle Collections nach target schreiben.
    Liefert (counts, konsistenz, inkonsistent); inkonsistent ist der Grund
    als Text ("" = stimmig bzw. zugelassener Einzelserver-Lauf).

    Replica Set: EINE Snapshot-Session fuer alle Collections, d. h. alle
    Dateien zeigen denselben Zeitpunkt. Standalone: Collection fuer
    Collection (Aenderungen waehrend des Laufs koennen dazwischen liegen).

    Runde 21 (Pruefbefund Backup A): Schlaegt das Snapshot-Lesen fehl (z. B.
    SnapshotTooOld bei sehr grossen Datenbanken), wird es bis zu
    BACKUP_SNAPSHOT_VERSUCHE mal wiederholt. Erst danach wird Collection
    fuer Collection gelesen — lieber ein Backup als keins —, aber das
    Ergebnis ist ausdruecklich INKONSISTENT und zaehlt nicht als gutes
    Backup. pflicht=True (MONGO_URL mit replicaSet= oder
    BACKUP_SNAPSHOT_PFLICHT) erzwingt den Snapshot-Versuch auch dann, wenn
    die Erkennung "kein Replica Set" meldet. Fehler bei den Index-Metadaten
    fallen NICHT in den Rueckfall, sondern brechen das Backup ab."""
    rs = ist_replica_set(client)
    if rs is None:
        log("  WARNUNG: Replica-Set-Erkennung (hello/isMaster) ohne Antwort — "
            "Snapshot wird versucht", logfile)
    if rs or rs is None or pflicht:
        versuche = _snapshot_versuche()
        letzter = None
        for versuch in range(1, versuche + 1):
            if versuch > 1:
                pause = _snapshot_pause_s() * (versuch - 1)
                log(f"  Snapshot-Versuch {versuch}/{versuche} in {pause:.0f} s ...", logfile)
                time.sleep(pause)
            _teildateien_entfernen(target)
            counts = {}
            try:
                with client.start_session(snapshot=True) as s:
                    for name in names:
                        counts[name] = dump_collection(db[name], target, session=s)
                        log(f"  {name}: {counts[name]} Dokumente (Snapshot)", logfile)
                return counts, KONSISTENZ_SNAPSHOT, ""
            except IndexMetadatenFehler:
                raise
            except Exception as exc:  # noqa: BLE001
                letzter = exc
                log(f"  WARNUNG: Snapshot-Lesen fehlgeschlagen (Versuch {versuch}/"
                    f"{versuche}): {exc}", logfile)
        konsistenz = KONSISTENZ_RUECKFALL
        inkonsistent = (f"Snapshot nach {versuche} Versuch(en) fehlgeschlagen "
                        f"({str(letzter)[:300]}); Collections nacheinander gelesen, "
                        f"Zeitstaende koennen abweichen")
        log(f"  WARNUNG: {inkonsistent} — Backup wird als INKONSISTENT markiert "
            f"und zaehlt NICHT als gutes Backup", logfile)
    else:
        konsistenz = KONSISTENZ_STANDALONE
        inkonsistent = ""
        log("  WARNUNG: MongoDB laeuft OHNE Replica Set — die Sicherung wird "
            "Collection fuer Collection gelesen und ist damit nicht auf eine "
            "Sekunde genau in sich stimmig. Abhilfe: Replica Set einrichten "
            "(mongod --replSet rs0) ODER die Sicherung mit --wartung starten "
            "(pausiert Schreibzugriffe fuer die Dauer des Laufs).", logfile)
    _teildateien_entfernen(target)
    counts = {}
    for name in names:
        counts[name] = dump_collection(db[name], target)
        log(f"  {name}: {counts[name]} Dokumente", logfile)
    return counts, konsistenz, inkonsistent


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
        # Ehrlich benennen, WIE verschluesselt wurde: mit eigener Kopfzeile
        # (AES256) oder durch den Anbieter selbst (R2 verschluesselt immer,
        # nimmt die Kopfzeile aber nicht an).
        verschluesselung = ("AES256" if extra.get("ServerSideEncryption")
                            else "vom Anbieter verschluesselt")
        log(f"  Offsite: s3://{bucket}/{key} ({groesse / 1e6:.1f} MB, {verschluesselung}"
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


def _manifest_lesen(ordner: Path):
    try:
        m = json.loads((ordner / "manifest.json").read_text(encoding="utf-8"))
        return m if isinstance(m, dict) else None
    except (OSError, ValueError):
        return None


def rotate(base: Path, logfile: Path) -> None:
    """Lokal die letzten KEEP Backups behalten.

    Runde 21 (Nebenbefund Rotation): das juengste GUTE Backup (vollstaendig
    und stimmig) bleibt immer erhalten, auch ausserhalb der letzten KEEP —
    sonst verdraengen 14 schlechte Laeufe in Folge den letzten brauchbaren
    Stand."""
    dumps = sorted([p for p in base.iterdir()
                    if p.is_dir() and p.name.startswith("autoschnell-")])
    gute = [p for p in dumps if ist_gut(_manifest_lesen(p))]
    schutz = gute[-1] if gute else None
    for old in dumps[:-KEEP]:
        if old == schutz:
            log(f"Backup {old.name} bleibt erhalten: juengstes gutes Backup", logfile)
            continue
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
        if pause:
            # Runde 21: die Middleware cacht das Flag 5 s je Prozess — erst
            # danach sind Schreibzugriffe ueber die API sicher pausiert.
            log(f"  warte {WARTUNG_WARTEN_S} s, bis alle Backend-Prozesse die "
                f"Schreibpause sehen ...", logfile)
            time.sleep(WARTUNG_WARTEN_S)
        counts, konsistenz, inkonsistent = dump_datenbank(
            client, db, names, target, logfile, pflicht=snapshot_pflicht(mongo_url))
        indexe = indexe_gegenpruefen(target, counts)
    except IndexMetadatenFehler as exc:
        log(f"FEHLER: Index-Metadaten nicht gesichert — {exc}. Ohne sie fehlen "
            f"nach einem Restore Unique- und TTL-Indexe; kein Backup angelegt.", logfile)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return 1
    except Exception as exc:  # noqa: BLE001
        log(f"FEHLER beim Sichern der Datenbank: {exc}", logfile)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return 1
    finally:
        if pause:
            wartung_setzen(db, False, logfile)
    if pause:
        # Bei pausierten Schreibzugriffen passen auch nacheinander gelesene
        # Collections zusammen (auch nach einem gescheiterten Snapshot).
        konsistenz, inkonsistent = KONSISTENZ_SCHREIBPAUSE, ""
    log(f"  Konsistenz: {konsistenz}" + (f" — INKONSISTENT: {inkonsistent}"
                                         if inkonsistent else ""), logfile)

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
        # Runde 21: Grund einer Inkonsistenz ("" = kein Mangel) und die
        # Indexnamen je Collection als Gegenprobe fuer den Restore.
        "inkonsistent": inkonsistent,
        "indexe": indexe,
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
        if manifest.get("offsite") and ist_gut(manifest):
            offsite_rotieren(logfile)
        elif manifest.get("offsite"):
            # Runde 21: nach einem schlechten Lauf nichts offsite loeschen —
            # sonst verdraengen schlechte Archive die guten.
            log("  Offsite-Rotation ausgesetzt: dieser Lauf ist kein gutes Backup",
                logfile)

    total_docs = sum(counts.values())
    rotate(base, logfile)
    if inkonsistent:
        log(f"BACKUP INKONSISTENT: {len(names)} Collections, {total_docs} "
            f"Dokumente, {n_files} Dateien, {size_mb:.1f} MB -> {final_dir.name}; "
            f"Grund: {inkonsistent}; zaehlt NICHT als gutes Backup"
            + (f"; zudem UNVOLLSTAENDIG, NICHT gesichert: {'; '.join(unvollstaendig)}"
               if unvollstaendig else ""), logfile)
        return 3
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
