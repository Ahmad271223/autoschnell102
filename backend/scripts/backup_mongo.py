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
14) Archive, best effort — rotiert wird offsite nur nach einem guten Lauf.

Umgebung:
  MONGO_URL, DB_NAME, BACKUP_DIR
  BACKUP_SNAPSHOT_VERSUCHE    Snapshot-Versuche vor dem Rueckfall (3)
  BACKUP_SNAPSHOT_PAUSE_S     Pause vor Versuch n: n-1 mal so viele Sekunden (5)
  BACKUP_SNAPSHOT_PFLICHT     true: Snapshot ist Pflicht (siehe oben)
  S3_ENDPOINT, S3_BUCKET, S3_ACCESS_KEY, S3_SECRET_KEY, S3_REGION
      Datei-Speicher der App.
  BACKUP_DATEIEN              Wie der Datei-Speicher gesichert wird (19.09.2026):
      bucket   (Standard, sobald ein Sicherungs-Bucket bekannt ist) — jede
               Datei wird Speicher-zu-Speicher in den Sicherungs-Bucket
               kopiert, OHNE Umweg ueber die Platte. Nur neue/geaenderte
               Dateien werden uebertragen; im Datei-Speicher geloeschte
               bleiben BACKUP_DATEIEN_AUFBEWAHRUNG_TAGE (30) als Papierkorb
               erhalten und verschwinden dann auch dort.
      spiegel  (Standard ohne Sicherungs-Bucket) — wie frueher: der ganze
               Bucket wird in jedes lokale Backup heruntergeladen. Bei
               grossen Datei-Speichern fuellt das die Platte (14 Staende x
               Bucket-Groesse) — deshalb nur fuer kleine Installationen.
      aus      Dateien werden nicht gesichert (nur mit Versionierung am
               Datei-Speicher vertretbar); steht so im Manifest.
  BACKUP_DATEIEN_BUCKET       Ziel der Dateikopie; leer = BACKUP_S3_BUCKET.
  BACKUP_DATEIEN_PREFIX       Schluessel-Praefix dort, Standard "dateien/".
  BACKUP_DATEIEN_AUFBEWAHRUNG_TAGE  Papierkorb-Frist geloeschter Dateien (30).
  BACKUP_S3_BUCKET            Offsite-Ziel (EIGENER Bucket, nicht S3_BUCKET);
                              Client mit denselben S3_*-Zugangsdaten.
  BACKUP_S3_PREFIX            Schlüssel-Präfix, Standard "autoschnell-backups/"
  BACKUP_S3_OBJECT_LOCK_DAYS  > 0: ObjectLockMode COMPLIANCE bis +N Tage.
                              Der Bucket muss MIT Object Lock angelegt sein.
  BACKUP_S3_KEEP              Offsite-Aufbewahrung in Archiven (14)
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
import threading
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

import wartung
# In `backup_once` heisst ein PARAMETER ebenfalls `wartung` (der Schalter
# --wartung) und verdeckt dort das Modul. Fuer Zugriffe in diesem Namensraum
# deshalb ein eigener Name.
_wartung_modul = wartung

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://127.0.0.1:27017")
DB_NAME = os.environ.get("DB_NAME", "autoschnell")
KEEP = 14


DEFAULT_DIR = Path(os.environ.get("BACKUP_DIR") or r"C:\AutoSchnell-Backups")
BACKEND = Path(__file__).resolve().parent.parent
UPLOADS_DIR = Path(os.environ.get("BACKUP_UPLOADS_DIR") or BACKEND / "uploads")
LOCAL_STORAGE_DIR = Path(os.environ.get("BACKUP_LOCAL_STORAGE_DIR")
                         or BACKEND / "local_storage")
MANIFEST_VERSION = 5          # 20.09.2026: dateien_kopie.liste_datei (Nr. 70)
DATEIEN_LISTE = "dateien-liste.json.gz"   # Objektliste dieses Laufs (Nr. 70)
OFFSITE_PREFIX_DEFAULT = "autoschnell-backups/"
OFFSITE_KEEP_DEFAULT = 14
_OFFSITE_ARCHIV = re.compile(r"autoschnell-\d{4}-\d{2}-\d{2}_\d{4}\.tar\.gz$")


def _auslaufen_max_s() -> float:
    """Wie lange hoechstens auf ein echtes Auslaufen gewartet wird (N6).
    Danach laeuft die Sicherung trotzdem — nur eben ohne die Zusage
    "stichtagsgenau"."""
    try:
        return max(5.0, float(os.environ.get("BACKUP_AUSLAUFEN_MAX_S", "").strip()
                              or 120))
    except ValueError:
        return 120.0


def _wartung_warten_s() -> float:
    """Wie lange nach dem Einschalten gewartet wird (Nr. 65).

    Runde 21: die Middleware im Backend liest den Merker nur alle 5 s neu —
    erst danach sind neue Schreibzugriffe sicher pausiert.
    Nachpruefung 20.09.2026: dazu kommt eine Auslaufzeit fuer Anfragen, die
    vorher durchkamen und noch laufen. Standard 30 s."""
    try:
        return max(6.0, float(os.environ.get("BACKUP_WARTUNG_WARTEN_S", "").strip()
                              or 30))
    except ValueError:
        return 30.0


def _wartung_frist_min() -> int:
    """Ablaufzeit des Merkers (Nr. 66) — er wird waehrend des Laufs
    verlaengert, nach einem Absturz laeuft er von selbst ab."""
    try:
        return max(2, int(os.environ.get("BACKUP_WARTUNG_FRIST_MIN", "").strip()
                          or 15))
    except ValueError:
        return 15


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


class Schreibpause:
    """Schreibpause fuer die Dauer der Sicherung (Audit 09/2026).

    Ohne Replica Set gibt es keine Snapshot-Sicht. Mit --wartung pausiert die
    Plattform ihre Schreibzugriffe, sodass die einzelnen Collections
    zusammenpassen. Die Nachpruefung vom 20.09.2026 hat drei Luecken
    gezeigt, die hier geschlossen sind:

      Nr. 66  Der Merker bekommt eine ABLAUFZEIT und eine Besitzer-Kennung.
              Wird dieses Skript hart beendet (Zeitlimit -> kill), laeuft
              sein Aufraeumen nicht — dann laeuft der Merker von selbst ab
              und der naechste Serverstart raeumt ihn weg. Solange der Lauf
              arbeitet, verlaengert ein Faden im Hintergrund die Frist.
      Nr. 68  Der Umfang ist "schreiben": Lesen, Downloads und Marktplatz
              laufen weiter, nur veraendernde Anfragen bekommen 503.
      Nr. 65  Nach dem Einschalten wird nicht mehr stur 6 s gewartet,
              sondern WARTUNG_WARTEN_S (Standard 30 s, einstellbar ueber
              BACKUP_WARTUNG_WARTEN_S): 5 s, bis alle Backend-Prozesse den
              Merker gelesen haben, plus Auslaufzeit fuer Anfragen, die
              vorher durchkamen. Laenger laufende Anfragen koennen trotzdem
              noch schreiben — das steht jetzt im Log und im Manifest.
    """

    def __init__(self, db, logfile: Path):
        self.db = db
        self.logfile = logfile
        self.kennung = None
        #: RP-246: True, wenn ein fremder gueltiger Merker das Setzen verhindert hat
        self.fremder_merker = False
        self._stop = threading.Event()
        self._faden = None

    @property
    def aktiv(self) -> bool:
        return self.kennung is not None

    def einschalten(self) -> bool:
        try:
            self.kennung = wartung.setzen(
                self.db[wartung.FLAG_COLLECTION], "Datensicherung laeuft",
                umfang=wartung.UMFANG_SCHREIBEN, frist_min=_wartung_frist_min())
        except Exception as exc:  # noqa: BLE001
            log(f"  WARNUNG: Schreibpause konnte nicht gesetzt werden: {exc}",
                self.logfile)
            return False
        if self.kennung is None:
            # Rollenprüfung 22.09.2026 (RP-246/RP-397): ein FREMDER, noch
            # gueltiger Merker steht (z. B. ein laufender Restore). Frueher
            # wurde er ueberschrieben und am Ende sogar aufgehoben.
            self.fremder_merker = True
            log("  WARNUNG: Es steht bereits ein fremder Wartungsmerker (Restore "
                "oder zweite Sicherung) — die Schreibpause wird NICHT gesetzt "
                "und der fremde Merker bleibt unangetastet.", self.logfile)
            return False
        log(f"  Schreibpause AN (Lesen bleibt moeglich, laengstens "
            f"{_wartung_frist_min()} min)", self.logfile)
        self._faden = threading.Thread(target=self._verlaengern, daemon=True)
        self._faden.start()
        return True

    def auslaufen_lassen(self) -> bool:
        """Nachpruefung 20.09.2026 (N6): Warten, bis WIRKLICH niemand mehr
        schreibt — statt blind `_wartung_warten_s()` Sekunden zu schlafen.

        Ablauf: erst die Mindestzeit (die Middleware liest den Merker nur alle
        5 s neu — vorher kommen noch neue Schreibzugriffe durch), dann fragen,
        was die Backend-Prozesse melden. Jeder meldet waehrend einer Pause
        einmal je Sekunde seinen Stand nach `wartung_schreiber`.

        Rueckgabe: True, wenn alle meldenden Prozesse null offene
        Schreibzugriffe hatten. False heisst: die Frist lief ab oder niemand
        meldete (alter Stand ohne den Melder) — dann steht das im Log und im
        Manifest, und die Sicherung nennt sich NICHT stichtagsgenau.
        """
        mindestens = _wartung_warten_s()
        log(f"  warte mindestens {mindestens:.0f} s, bis alle Backend-Prozesse "
            f"die Schreibpause sehen ...", self.logfile)
        time.sleep(mindestens)
        coll = self.db[wartung.SCHREIBER_COLLECTION]
        frist = _auslaufen_max_s()
        ende = time.monotonic() + frist
        zuletzt = (None, 0)
        while time.monotonic() < ende:
            try:
                ruhig, offen, prozesse = wartung.schreiber_stand(coll)
            except Exception as exc:  # noqa: BLE001
                log(f"  WARNUNG: Stand der Schreibzugriffe nicht lesbar: {exc}",
                    self.logfile)
                return False
            zuletzt = (offen, prozesse)
            if ruhig:
                log(f"  alle {prozesse} Backend-Prozesse melden 0 offene "
                    f"Schreibzugriffe — Sicherung kann starten", self.logfile)
                return True
            time.sleep(1)
        offen, prozesse = zuletzt
        if not prozesse:
            log(f"  WARNUNG: kein Backend-Prozess meldet seinen Stand "
                f"(alte Fassung ohne Melder?). Nach {frist:.0f} s wird trotzdem "
                f"gesichert — ob dabei noch geschrieben wurde, ist ungewiss.",
                self.logfile)
        else:
            log(f"  WARNUNG: nach {frist:.0f} s melden {prozesse} Prozesse noch "
                f"{offen} offene Schreibzugriffe. Die Sicherung startet "
                f"trotzdem, gilt aber NICHT als stichtagsgenau.", self.logfile)
        return False

    def _verlaengern(self) -> None:
        # Alle 60 s die Frist erneuern, solange der Lauf arbeitet.
        while not self._stop.wait(60):
            try:
                wartung.verlaengern(self.db[wartung.FLAG_COLLECTION],
                                    self.kennung, _wartung_frist_min())
            except Exception:  # noqa: BLE001
                pass

    def ausschalten(self) -> None:
        if not self.aktiv:
            return
        self._stop.set()
        try:
            wartung.aufheben(self.db[wartung.FLAG_COLLECTION], self.kennung)
            log("  Schreibpause AUS", self.logfile)
        except Exception as exc:  # noqa: BLE001
            # Nicht schlimm: die Frist laeuft ohnehin ab, und der naechste
            # Serverstart raeumt den Merker weg (Nr. 66).
            log(f"  WARNUNG: Schreibpause nicht aufgehoben ({exc}) — sie laeuft "
                f"spaetestens in {_wartung_frist_min()} min von selbst ab",
                self.logfile)
        self.kennung = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.ausschalten()
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


def _mehrheit_rueckstand_max_s() -> float:
    """RP-552: ab so vielen Sekunden Rueckstand des Mehrheits-Commitpunkts gilt
    ein Snapshot als veraltet (Standard 600 s = 10 min)."""
    try:
        return max(30.0, float(os.environ.get("BACKUP_MEHRHEIT_RUECKSTAND_MAX_S", "")
                               .strip() or 600))
    except ValueError:
        return 600.0


def mehrheit_rueckstand_s(client):
    """Rollenprüfung 22.09.2026 (RP-552): Abstand in Sekunden zwischen dem
    zuletzt angewendeten und dem von der Mehrheit bestaetigten Stand
    (replSetGetStatus). None = nicht ermittelbar (Einzelserver, fehlendes
    Recht clusterMonitor, Stoerung) — dann bleibt alles wie bisher."""
    try:
        st = client.admin.command("replSetGetStatus")
        o = st.get("optimes") or {}
        angewendet, bestaetigt = o.get("lastAppliedWallTime"), o.get("lastCommittedWallTime")
        if isinstance(angewendet, datetime) and isinstance(bestaetigt, datetime):
            return max(0.0, (angewendet - bestaetigt).total_seconds())
        a = (o.get("appliedOpTime") or {}).get("ts")
        c = (o.get("lastCommittedOpTime") or {}).get("ts")
        if a is not None and c is not None:
            return max(0.0, float(a.time - c.time))
    except Exception:  # noqa: BLE001
        return None
    return None


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
    # Rollenprüfung 22.09.2026 (RP-552): Im Aufbau Primary + Secondary +
    # Arbiter bleibt der Mehrheits-Commitpunkt stehen, sobald prod2 ausfaellt.
    # Die Snapshot-Sitzung liest genau diesen Punkt — jede Nacht kam deshalb
    # still der Stand vom Ausfallzeitpunkt heraus, und das Manifest nannte ihn
    # "snapshot". Jetzt: Rueckstand messen; ist er groesser als
    # BACKUP_MEHRHEIT_RUECKSTAND_MAX_S, wird vom Primary nacheinander gelesen
    # (aktuell, aber nicht stichtagsgenau) und der Lauf als INKONSISTENT
    # gemeldet — backup_service legt daraus den Betriebsalarm an.
    rueckstand = mehrheit_rueckstand_s(client) if (rs or rs is None or pflicht) else None
    zu_alt = rueckstand is not None and rueckstand > _mehrheit_rueckstand_max_s()
    if zu_alt:
        konsistenz = KONSISTENZ_RUECKFALL
        inkonsistent = (f"Mehrheits-Commitpunkt {rueckstand / 60:.0f} min hinter dem "
                        f"Primary (Replikat-Mitglied ausgefallen?) — ein Snapshot "
                        f"zeigte den alten Stand; Collections vom Primary nacheinander "
                        f"gelesen, Zeitstaende koennen abweichen")
        log(f"  WARNUNG: {inkonsistent}. Abhilfe: DEPLOYMENT.md, 'prod2 laenger "
            f"weg' (ausgefallenes Mitglied votes:0/priority:0).", logfile)
    elif rs or rs is None or pflicht:
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
        rel = src.relative_to(quelle)
        # Pruefbericht 20.09.2026 (SK-01): Hilfsordner einer Wiederherstellung
        # (.restore-*, .vorher-*) liegen IM Volume — sie gehoeren nicht in die
        # Sicherung, sonst waechst jede Sicherung um den ganzen alten Stand.
        if rel.parts and rel.parts[0].startswith((".restore-", ".vorher-")):
            continue
        dst = ziel / rel
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


# ------------------------------------------------ Dateien: Speicher zu Speicher
# 19.09.2026 (Entscheidung Ahmad): Der Datei-Speicher wird nicht mehr auf
# die Serverplatte gespiegelt. Bei 36 Nutzern x 150 Inseraten am Tag liegen
# dauerhaft ~20 GB Dateien im Bucket — 14 Staende plus Archiv waeren 300 GB
# auf einer 160-GB-Platte. Stattdessen wandert jede Datei einmal in den
# Sicherungs-Bucket (Papierkorb-Frist fuer Geloeschtes), die Platte traegt
# nur noch die Datenbank-Sicherung.
DATEIEN_MODI = ("bucket", "spiegel", "aus")
DATEIEN_PREFIX_DEFAULT = "dateien/"
DATEIEN_AUFBEWAHRUNG_DEFAULT = 30
_GELOESCHT_AM = "geloescht-am"


def dateien_modus() -> str:
    """bucket | spiegel | aus — Standard: bucket, sobald ein Sicherungs-
    Bucket bekannt ist, sonst der alte Spiegel."""
    wahl = os.environ.get("BACKUP_DATEIEN", "").strip().lower()
    if wahl in DATEIEN_MODI:
        return wahl
    ziel = (os.environ.get("BACKUP_DATEIEN_BUCKET", "").strip()
            or os.environ.get("BACKUP_S3_BUCKET", "").strip())
    return "bucket" if ziel else "spiegel"


def dateien_ziel() -> tuple:
    """(Bucket, Praefix) der Dateikopie."""
    bucket = (os.environ.get("BACKUP_DATEIEN_BUCKET", "").strip()
              or os.environ.get("BACKUP_S3_BUCKET", "").strip())
    praefix = os.environ.get("BACKUP_DATEIEN_PREFIX", "").strip() or DATEIEN_PREFIX_DEFAULT
    if praefix and not praefix.endswith("/"):
        praefix += "/"
    return bucket, praefix


def dateien_aufbewahrung_tage() -> int:
    raw = os.environ.get("BACKUP_DATEIEN_AUFBEWAHRUNG_TAGE", "").strip()
    try:
        return max(1, int(raw)) if raw else DATEIEN_AUFBEWAHRUNG_DEFAULT
    except ValueError:
        return DATEIEN_AUFBEWAHRUNG_DEFAULT


def _objekte_auflisten(client, bucket: str, praefix: str = "",
                       etags: dict | None = None) -> dict:
    """Schluessel -> (Groesse, LastModified) unter einem Praefix.

    Nachpruefung 20.09.2026, Nr. 70: `etags` sammelt zusaetzlich die
    Pruefsumme je Objekt. Damit haelt jedes Backup fest, WELCHE Dateien in
    welcher Fassung zu seinem Datenbankstand gehoeren — vorher standen im
    Manifest nur Zaehler, und ein bestimmter Stand liess sich nicht
    beweisbar wiederherstellen."""
    aus = {}
    for seite in client.get_paginator("list_objects_v2").paginate(
            Bucket=bucket, Prefix=praefix):
        for obj in seite.get("Contents") or []:
            aus[obj["Key"]] = (int(obj.get("Size") or 0), obj.get("LastModified"))
            if etags is not None:
                etags[obj["Key"]] = (obj.get("ETag") or "").strip('"')
    return aus


def dateien_in_bucket_sichern(logfile: Path) -> dict:
    """Datei-Speicher in den Sicherungs-Bucket kopieren — ohne Platte.

    Gelesen wird mit den S3_*-Zugangsdaten (Datei-Speicher), geschrieben mit
    den BACKUP_S3_*-Zugangsdaten (Sicherungs-Bucket); die Daten fliessen
    durch den Arbeitsspeicher, nie auf die Platte. So funktioniert es auch
    mit einem Sicherungs-Schluessel, der den Datei-Speicher nicht lesen darf.

    Liefert die Angaben fuers Manifest. Einzelne Fehler brechen den Lauf
    nicht ab, zaehlen aber als 'fehler' — das Backup gilt dann als
    UNVOLLSTAENDIG (Exit 2), wie bei jedem anderen fehlenden Teil."""
    quelle_bucket = os.environ["S3_BUCKET"].strip()
    ziel_bucket, praefix = dateien_ziel()
    if not ziel_bucket:
        raise RuntimeError("BACKUP_DATEIEN=bucket, aber kein Sicherungs-Bucket "
                           "(BACKUP_DATEIEN_BUCKET oder BACKUP_S3_BUCKET) gesetzt")
    if ziel_bucket == quelle_bucket:
        raise RuntimeError("Der Sicherungs-Bucket darf nicht der Datei-Speicher "
                           f"selbst sein ({quelle_bucket})")
    lesen = _s3_client()
    schreiben = _backup_s3_client()
    jetzt = datetime.now(timezone.utc)
    frist = timedelta(days=dateien_aufbewahrung_tage())
    sse = sse_optionen(backup_endpoint())

    quell_etags: dict = {}
    quelle = _objekte_auflisten(lesen, quelle_bucket, etags=quell_etags)
    # Liegt die Offsite-Kopie (entgegen der Empfehlung) im Datei-Speicher,
    # die Archive nicht mitkopieren (wie spiegle_s3).
    if os.environ.get("BACKUP_S3_BUCKET", "").strip() == quelle_bucket:
        op = offsite_prefix()
        quelle = {k: v for k, v in quelle.items() if not k.startswith(op)}
    ziel = _objekte_auflisten(schreiben, ziel_bucket, praefix)

    stand = {"modus": "bucket", "bucket": ziel_bucket, "prefix": praefix,
             "geprueft": len(quelle), "kopiert": 0, "unveraendert": 0,
             "bytes_kopiert": 0, "vorgemerkt": 0, "entfernt": 0, "fehler": 0,
             "aufbewahrung_tage": dateien_aufbewahrung_tage()}
    fehler_beispiel = ""

    for key, (groesse, geaendert) in quelle.items():
        ziel_key = praefix + key
        dort = ziel.get(ziel_key)
        if dort is not None and dort[0] == groesse and (
                geaendert is None or dort[1] is None or dort[1] >= geaendert):
            stand["unveraendert"] += 1
            continue
        try:
            body = lesen.get_object(Bucket=quelle_bucket, Key=key)["Body"]
            schreiben.upload_fileobj(body, ziel_bucket, ziel_key, ExtraArgs=dict(sse))
            stand["kopiert"] += 1
            stand["bytes_kopiert"] += groesse
        except Exception as exc:  # noqa: BLE001
            stand["fehler"] += 1
            fehler_beispiel = fehler_beispiel or f"{key}: {exc}"
            log(f"  WARNUNG: Datei nicht kopiert — {key}: {exc}", logfile)

    # Papierkorb: im Datei-Speicher geloeschte Dateien bleiben die Frist lang
    # erhalten (Schutz vor Versehen), danach verschwinden sie auch hier.
    for ziel_key in ziel:
        key = ziel_key[len(praefix):]
        if key in quelle:
            continue
        try:
            kopf = schreiben.head_object(Bucket=ziel_bucket, Key=ziel_key)
            meta = dict(kopf.get("Metadata") or {})
            markiert = meta.get(_GELOESCHT_AM)
            if not markiert:
                meta[_GELOESCHT_AM] = jetzt.isoformat()
                schreiben.copy_object(
                    Bucket=ziel_bucket, Key=ziel_key,
                    CopySource={"Bucket": ziel_bucket, "Key": ziel_key},
                    Metadata=meta, MetadataDirective="REPLACE", **sse)
                stand["vorgemerkt"] += 1
                continue
            seit = datetime.fromisoformat(markiert)
            if seit.tzinfo is None:
                seit = seit.replace(tzinfo=timezone.utc)
            if jetzt - seit >= frist:
                schreiben.delete_object(Bucket=ziel_bucket, Key=ziel_key)
                stand["entfernt"] += 1
        except Exception as exc:  # noqa: BLE001
            # Nie loeschen, was sich nicht sicher einordnen laesst.
            # Nachpruefung 20.09.2026, Nr. 71: frueher wurde das NUR geloggt,
            # `fehler` blieb 0 und der Lauf meldete "BACKUP OK" — obwohl die
            # Loeschfrist nicht umgesetzt wurde und geloeschte personen-
            # bezogene Dateien unbegrenzt liegenblieben. Jetzt zaehlt es als
            # Fehler und das Backup gilt als UNVOLLSTAENDIG (Exit 2).
            stand["fehler"] += 1
            stand["papierkorb_fehler"] = stand.get("papierkorb_fehler", 0) + 1
            fehler_beispiel = fehler_beispiel or f"{ziel_key}: {exc}"
            log(f"  WARNUNG: Papierkorb-Eintrag {ziel_key} nicht bearbeitet — {exc}",
                logfile)

    # Nr. 70: WELCHE Dateien in welcher Fassung zu genau diesem Backup
    # gehoeren. Ohne diese Liste aktualisieren alle Laeufe dasselbe
    # dateien/-Praefix, und ein bestimmter Datenbankstand liess sich nicht
    # mit seinem damaligen Dateistand wiederherstellen. Der Restore
    # vergleicht gegen diese Liste (siehe dateien_zurueckkopieren.py).
    stand["objekte"] = len(quelle)
    stand["liste"] = {k: {"bytes": v[0], "etag": quell_etags.get(k, "")}
                      for k, v in sorted(quelle.items())}

    log(f"  Dateien -> s3://{ziel_bucket}/{praefix}: {stand['kopiert']} kopiert "
        f"({stand['bytes_kopiert'] / 1e6:.1f} MB), {stand['unveraendert']} unveraendert, "
        f"{stand['vorgemerkt']} in den Papierkorb, {stand['entfernt']} endgueltig entfernt"
        + (f", {stand['fehler']} FEHLER (z. B. {fehler_beispiel})" if stand["fehler"] else ""),
        logfile)
    return stand


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
    """S3-Client fuer die Offsite-Kopie. Phase 3 (3.5, E3): eigene Zugangs-
    daten BACKUP_S3_ACCESS_KEY / BACKUP_S3_SECRET_KEY (und BACKUP_S3_REGION),
    damit ein kompromittierter Datei-Speicher-Schluessel nicht auch an die
    Sicherungen kommt; ohne diese Werte wie bisher die S3_*-Zugangsdaten.
    Rollenprüfung 22.09.2026 (Review): mit den Zeitlimits der Sicherung
    (sicherung=True, 5 Versuche/120 s) statt der knappen des Anfragewegs
    (S3_VERSUCHE=2/30 s) — sonst brach ein kurzer 5xx an EINEM Teil den
    ganzen mehrteiligen Offsite-Upload ab."""
    return s3_client(endpoint=backup_endpoint(),
                     access_key=os.environ.get("BACKUP_S3_ACCESS_KEY", "").strip() or None,
                     secret_key=os.environ.get("BACKUP_S3_SECRET_KEY", "").strip() or None,
                     region=os.environ.get("BACKUP_S3_REGION", "").strip() or None,
                     sicherung=True)


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


def rotate(base: Path, logfile: Path, keep: int = None) -> None:
    """Lokal die letzten KEEP Backups behalten (bzw. `keep`, siehe platz_pruefen).

    Runde 21 (Nebenbefund Rotation): das juengste GUTE Backup (vollstaendig
    und stimmig) bleibt immer erhalten, auch ausserhalb der letzten KEEP —
    sonst verdraengen 14 schlechte Laeufe in Folge den letzten brauchbaren
    Stand."""
    dumps = sorted([p for p in base.iterdir()
                    if p.is_dir() and p.name.startswith("autoschnell-")])
    gute = [p for p in dumps if ist_gut(_manifest_lesen(p))]
    schutz = gute[-1] if gute else None
    for old in dumps[:-(KEEP if keep is None else max(1, keep))]:
        if old == schutz:
            log(f"Backup {old.name} bleibt erhalten: juengstes gutes Backup", logfile)
            continue
        shutil.rmtree(old, ignore_errors=True)
        log(f"Altes Backup entfernt: {old.name}", logfile)


#: Pruefbericht 20.09.2026 (SK-08): Reste abgebrochener Laeufe, die aelter als
#: so viele Stunden sind, werden zu Beginn jedes Laufs entfernt (ein Lauf wird
#: nach 3 h beendet, siehe backup_service).
RESTE_ALTER_H = 4.0


def reste_aufraeumen(base: Path, logfile: Path, alter_h: float = RESTE_ALTER_H) -> list:
    """SK-08: Arbeitsordner (.tmp-autoschnell-*) und Offsite-Archive
    (.tmp-*.tar.gz) abgebrochener Laeufe entfernen. rotate() erfasst nur
    fertige Staende — scheiterte ein Lauf nach dem Dump (Pruefsummen,
    Manifest) oder wurde er nach 3 h beendet, blieb der Rest fuer immer
    liegen. Liefert die entfernten Namen."""
    grenze = datetime.now(timezone.utc).timestamp() - alter_h * 3600
    weg = []
    for p in sorted(base.glob(".tmp-*")):
        try:
            if p.stat().st_mtime > grenze:
                continue
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            else:
                p.unlink()
            weg.append(p.name)
            log(f"  Rest eines abgebrochenen Laufs entfernt: {p.name}", logfile)
        except OSError:
            continue
    return weg


def _groesse(ordner: Path) -> int:
    try:
        return sum(f.stat().st_size for f in ordner.rglob("*") if f.is_file())
    except OSError:
        return 0


def platz_pruefen(base: Path, logfile: Path) -> bool:
    """Pruefbericht 20.09.2026 (AL-10): VOR dem Dump pruefen, ob der Platz
    reicht (Schaetzung: 1,5 x die juengste Sicherung). Vorher lief die Platte
    beim Dump voll, der Lauf endete mit Exit 1 — und weil rotate() nur nach
    einem GELUNGENEN Lauf aufraeumt, scheiterte jeder weitere Lauf genauso.
    Reicht der Platz nicht, wird vorab eine Sicherung weniger behalten; reicht
    er dann immer noch nicht, endet der Lauf mit klarer Meldung (Exit 1)."""
    dumps = sorted(p for p in base.iterdir()
                   if p.is_dir() and p.name.startswith("autoschnell-"))
    if not dumps:
        return True
    bedarf = int(_groesse(dumps[-1]) * 1.5)
    frei = shutil.disk_usage(base).free
    if frei >= bedarf:
        return True
    log(f"  WARNUNG: wenig Platz ({frei / 1e6:.0f} MB frei, gebraucht etwa "
        f"{bedarf / 1e6:.0f} MB) — die aelteste Sicherung wird schon jetzt entfernt", logfile)
    rotate(base, logfile, keep=KEEP - 1)
    frei = shutil.disk_usage(base).free
    if frei >= bedarf:
        return True
    log(f"FEHLER: zu wenig Speicher fuer die Sicherung ({frei / 1e6:.0f} MB frei, "
        f"gebraucht etwa {bedarf / 1e6:.0f} MB) — Platz schaffen, siehe DEPLOYMENT.md "
        f"\"Speicher voll\"", logfile)
    return False


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
    reste_aufraeumen(base, logfile)
    try:
        if not platz_pruefen(base, logfile):
            return 1
    except OSError as exc:
        log(f"  Hinweis: freier Platz nicht pruefbar ({exc})", logfile)

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
        names = sorted(n for n in db.list_collection_names()
                       # N6: die Melde-Sammlung der Schreibpause gehoert nicht
                       # in die Sicherung — sie wird waehrenddessen beschrieben
                       # und traegt keine Geschaeftsdaten.
                       if n != _wartung_modul.SCHREIBER_COLLECTION)
    except Exception as exc:
        log(f"FEHLER: MongoDB nicht erreichbar — {exc}", logfile)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return 1

    # Schreibpause nur, wenn ausdruecklich gewuenscht (Standalone-Mongo):
    # dann pausiert das Backend Schreibzugriffe, damit die Collections
    # zusammenpassen (Audit 09/2026).
    # Nachpruefung 20.09.2026, Nr. 69: die Pause bleibt jetzt bis NACH der
    # Dateisicherung an. Vorher endete sie direkt nach dem Datenbank-Dump —
    # eine danach geloeschte Datei war in der Datenbanksicherung noch
    # verzeichnet, in der Dateisicherung aber schon weg.
    # Rollenprüfung 22.09.2026 (RP-246/RP-397): Laeuft gerade ein Restore
    # (Wartungsmodus "alles"), ist die Datenbank halb alt, halb neu — eine
    # Sicherung davon waere wertlos, und frueher ueberschrieb die
    # Schreibpause hier sogar den Restore-Merker. Dann: keine Sicherung
    # (Exit 1, backup_service versucht es in einer Stunde erneut).
    try:
        merker = db[_wartung_modul.FLAG_COLLECTION].find_one({"_id": _wartung_modul.FLAG_ID})
    except Exception:  # noqa: BLE001 — nicht lesbar: wie bisher weiter
        merker = None
    if _wartung_modul.pausiert(merker, "GET"):
        log(f"FEHLER: Wartungsmodus 'alles' aktiv ({_wartung_modul.beschreibung(merker)}, "
            f"Besitzer {(merker or {}).get('besitzer') or 'unbekannt'}) — waehrend eines "
            f"Restores wird nicht gesichert. Kein Backup angelegt.", logfile)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return 1
    schreibpause = Schreibpause(db, logfile)
    pause = wartung and schreibpause.einschalten()
    if wartung and schreibpause.fremder_merker:
        log("FEHLER: Schreibpause nicht moeglich, weil bereits ein fremder "
            "Wartungsmerker steht — kein Backup angelegt (naechster Versuch "
            "spaeter).", logfile)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return 1
    ruhig = False          # N6: wurde das Auslaufen bestaetigt?
    try:
        if pause:
            ruhig = schreibpause.auslaufen_lassen()
        counts, konsistenz, inkonsistent = dump_datenbank(
            client, db, names, target, logfile, pflicht=snapshot_pflicht(mongo_url))
        indexe = indexe_gegenpruefen(target, counts)
    except IndexMetadatenFehler as exc:
        schreibpause.ausschalten()
        log(f"FEHLER: Index-Metadaten nicht gesichert — {exc}. Ohne sie fehlen "
            f"nach einem Restore Unique- und TTL-Indexe; kein Backup angelegt.", logfile)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return 1
    except Exception as exc:  # noqa: BLE001
        schreibpause.ausschalten()
        log(f"FEHLER beim Sichern der Datenbank: {exc}", logfile)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return 1
    if pause and ruhig:
        # Bei pausierten Schreibzugriffen passen auch nacheinander gelesene
        # Collections zusammen (auch nach einem gescheiterten Snapshot).
        #
        # Nachpruefung 20.09.2026 (N6): NUR wenn das Auslaufen wirklich
        # geklappt hat. Vorher genuegte "Pause eingeschaltet" — und danach
        # wurde eine feste Zeit geschlafen, ohne zu wissen, ob noch jemand
        # schreibt. Eine laenger laufende Anfrage konnte mitten im Dump
        # schreiben, und die Sicherung nannte sich trotzdem stichtagsgenau.
        # Jetzt gilt die Zusage nur, wenn ALLE Backend-Prozesse null offene
        # Schreibzugriffe gemeldet haben (siehe auslaufen_lassen).
        konsistenz, inkonsistent = KONSISTENZ_SCHREIBPAUSE, ""
    elif pause:
        log("  HINWEIS: Die Schreibpause lief, aber das Auslaufen konnte nicht "
            "bestaetigt werden — die Sicherung gilt deshalb NICHT als "
            "stichtagsgenau.", logfile)
    log(f"  Konsistenz: {konsistenz}" + (f" — INKONSISTENT: {inkonsistent}"
                                         if inkonsistent else ""), logfile)

    # Nr. 69: die Schreibpause umfasst auch die Dateien — sonst passen
    # Datenbankstand und Dateistand nicht zusammen. Sie wird erst
    # danach aufgehoben, auch wenn hier etwas schiefgeht.
    try:
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
        s3_namen = ("S3_ENDPOINT", "S3_BUCKET", "S3_ACCESS_KEY", "S3_SECRET_KEY")
        s3_aktiv = all(os.environ.get(v, "").strip() for v in s3_namen)
        # Pruefbericht 20.09.2026 (AL-01/D1): War EINE der vier Variablen leer
        # oder vertippt, wurde der Datei-Speicher kommentarlos uebersprungen —
        # und das Backup galt trotzdem als vollstaendig. Alle Fahrzeugfotos,
        # Protokoll-PDFs und Unterschriften fehlten, ohne dass es jemand merkte.
        s3_fehlt = [v for v in s3_namen if not os.environ.get(v, "").strip()]
        if not s3_aktiv and len(s3_fehlt) < len(s3_namen):
            unvollstaendig.append("s3: nur teilweise konfiguriert (fehlt: "
                                  + ", ".join(s3_fehlt) + ") — Datei-Speicher NICHT gesichert")
            log("  WARNUNG: S3 nur teilweise konfiguriert — Dateien NICHT gesichert "
                f"(fehlt: {', '.join(s3_fehlt)})", logfile)
        dateien_kopie = None
        modus = dateien_modus() if s3_aktiv else None
        if s3_aktiv and modus == "spiegel":
            try:
                n_files += spiegle_s3(tmp_dir / "s3", logfile)
            except Exception as exc:  # noqa: BLE001
                unvollstaendig.append(f"s3: {exc}")
                log(f"  WARNUNG: S3-Bucket NICHT gesichert — {exc}", logfile)
        elif s3_aktiv and modus == "bucket":
            # 19.09.2026: Speicher zu Speicher, nichts davon landet auf der Platte.
            try:
                dateien_kopie = dateien_in_bucket_sichern(logfile)
                # Nr. 70: die Objektliste dieses Laufs als eigene Datei im
                # Backup — sie bekommt damit eine SHA-256-Pruefsumme im
                # Manifest wie jede andere Datei. Im Manifest selbst steht
                # nur der Dateiname, sonst wuerde es bei vielen Objekten
                # riesig.
                liste = dateien_kopie.pop("liste", None)
                if liste is not None:
                    ziel_liste = tmp_dir / DATEIEN_LISTE
                    with gzip.open(ziel_liste, "wt", encoding="utf-8") as fh:
                        json.dump(liste, fh, ensure_ascii=False)
                    dateien_kopie["liste_datei"] = DATEIEN_LISTE
                if dateien_kopie.get("fehler"):
                    unvollstaendig.append(
                        f"dateien: {dateien_kopie['fehler']} Objekte nicht in den "
                        f"Sicherungs-Bucket kopiert")
            except Exception as exc:  # noqa: BLE001
                unvollstaendig.append(f"dateien: {exc}")
                log(f"  WARNUNG: Dateien NICHT in den Sicherungs-Bucket kopiert — {exc}",
                    logfile)
        elif s3_aktiv:
            dateien_kopie = {"modus": "aus"}
            log("  Hinweis: Datei-Speicher wird nicht gesichert (BACKUP_DATEIEN=aus)", logfile)
        if os.environ.get("EMERGENT_LLM_KEY", "").strip():
            # Externer Snapshot-Speicher ohne Listing-API: nicht sicherbar.
            unvollstaendig.append("snapshots: externer Snapshot-Speicher (EMERGENT) "
                                  "ist von hier aus nicht sicherbar")
            log("  WARNUNG: externer Snapshot-Speicher wird nicht gesichert", logfile)

    finally:
        schreibpause.ausschalten()

    # ---- Manifest mit Pruefsummen ----
    # SK-08: scheitert diese Phase (Platte voll, Lesefehler), bleibt kein
    # halber Arbeitsordner liegen.
    try:
        dateien = {}
        for f in sorted(tmp_dir.rglob("*")):
            if f.is_file():
                dateien[str(f.relative_to(tmp_dir)).replace("\\", "/")] = {
                    "sha256": sha256_datei(f), "bytes": f.stat().st_size}
    except OSError as exc:
        log(f"FEHLER beim Pruefsummen-Bilden: {exc} — kein Backup angelegt", logfile)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return 1
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
    if dateien_kopie is not None:
        # 19.09.2026: wo die Dateien liegen (Sicherungs-Bucket) bzw. dass sie
        # bewusst nicht gesichert werden — der Restore liest das mit.
        manifest["dateien_kopie"] = dateien_kopie
    try:
        schreibe_manifest(tmp_dir, manifest)
        size_mb = sum(v["bytes"] for v in dateien.values()) / 1e6
        tmp_dir.rename(final_dir)          # atomarer Abschluss des lokalen Backups
    except OSError as exc:
        log(f"FEHLER beim Abschliessen der Sicherung: {exc} — kein Backup angelegt", logfile)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return 1

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
