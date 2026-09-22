# -*- coding: utf-8 -*-
"""Wiederherstellung eines AutoSchnell-Backups (Gegenstück zu backup_mongo.py).

    python -X utf8 restore_mongo.py <Backup-Ordner> [--db autoschnell] [--yes]
    python -X utf8 restore_mongo.py <Backup-Ordner> --dry-run     # nur prüfen

Grundsatz (Go-Live-Audit): Nach einem Restore ist die Zieldatenbank
ENTWEDER vollständig auf dem alten ODER vollständig auf dem Backup-Stand —
nie gemischt.

Nachprüfung 20.09.2026 (Nr. 75): Dieser Satz stimmte bisher NICHT für den
dokumentierten Standardaufruf. Collections, die es nur live gibt (weil sie
nach dem Backup entstanden sind), blieben unverändert stehen — neben dem
alten Stand aus dem Backup. Nur das nirgends dokumentierte `--exakt`
verschob sie weg. Deshalb ist dieses Verhalten jetzt der STANDARD; wer den
gemischten Stand wirklich will, sagt es mit `--zusaetzliche-behalten`
ausdrücklich und bekommt eine laute Warnung.

Dafür:

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
      S3-Objekte zurückspielen. Nachprüfung 20.09.2026 (Nr. 77): Vom
      bisherigen Stand JEDES überschriebenen Objekts wird vorher eine Kopie
      unter `restore-vorher/<stamp>/` im selben Eimer angelegt (Server zu
      Server, ohne Herunterladen). Vorher war dieser Schritt nicht
      rückgängig zu machen: scheiterte danach das Umschalten, wurden
      Datenbank und lokale Ordner zurückgedreht, die schon überschriebenen
      S3-Objekte aber nicht — die alte Datenbank zeigte dann auf
      zurückgespielte Dateien. Jetzt wird auch S3 zurückgedreht; die Kopien
      verschwinden erst nach einem gelungenen Restore.
  5/6 UMSCHALTEN: Ordner per Rename (live -> <live>.vorher-<stamp>,
      Staging -> live), dann je Collection renameCollection (bisheriger
      Stand -> <db>__vorher_<stamp>). Jeder Fehler: ALLE bereits
      umgeschalteten Collections, Ordner UND S3-Objekte werden
      zurückgedreht.
  6/6 KONTROLLE: Dokumentzahlen und Indexe der Live-Datenbank erneut gegen
      das Manifest. Nur wenn alles passt: "RESTORE OK", Wartungsmodus aus.
      Sonst Rollback, Exit 1.

Die Collection system_flags (Betriebs-Flags, u. a. der Wartungsmodus) wird
nie aus dem Backup zurückgespielt. Alte Backups ohne Manifest (Version < 2)
werden nur mit --allow-no-manifest akzeptiert (dann ohne Prüfsummen, aber
weiterhin mit vollständigem Einlesen vor dem Umschalten). --nur-datenbank
lässt die Datei-Speicher unangetastet (Restore-Probe in eine Testdatenbank).

Runde 21 (Pruefbefund Backup):
  A  Ein als INKONSISTENT markiertes Backup (Snapshot gescheitert,
     Collections nacheinander gelesen; Manifest "inkonsistent" bzw. bei
     Alt-Manifesten konsistenz "best-effort (snapshot fehlgeschlagen)")
     wird abgelehnt — auch im --dry-run. Nur --notfall-inkonsistent-
     akzeptieren spielt es ein, mit lauter Warnung.
  B  Zu JEDER .bson.gz muss eine lesbare <name>.metadata.json mit den
     Indexen vorliegen (und zum Manifest-Feld "indexe" passen). Fehlt sie
     oder ist sie unlesbar: Abbruch, nichts veraendert. Nur fuer Alt-Backups
     ohne Indexdaten gibt es --alt-backup-ohne-indexdaten (dann werden fuer
     diese Collections keine Indexe angelegt oder geprueft). Die Pruefung
     vergleicht je Index Schluessel, unique, sparse, expireAfterSeconds und
     partialFilterExpression — nicht nur den Namen.
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

# Als Skript gestartet kennt Python nur den scripts-Ordner; die gemeinsame
# Bewertung (backup_bewertung) liegt im Backend-Ordner darueber.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bson
from bson import json_util
from backup_bewertung import inkonsistenz, ist_stichtagsgenau, metadaten_mangel
from pymongo import MongoClient

import wartung

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://127.0.0.1:27017")
BACKEND = Path(__file__).resolve().parent.parent
FLAG_COLLECTION = wartung.FLAG_COLLECTION
FLAG_ID = wartung.FLAG_ID
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


class BsonDatei:
    """Rollenprüfung 22.09.2026 (RP-545): die Dokumente EINER .bson.gz —
    gezaehlt, aber nicht im Speicher.

    Vorher las pruefe_backup jede Collection per list(...) komplett ein und
    hielt ALLE gleichzeitig (das Laden brauchte sie danach noch einmal). Als
    Python-Objekte ist das das 1,7- bis 6-fache der Datenmenge — im
    Backend-Container (mem_limit 4g, neben den laufenden Workern) drohte ab
    etwa 0,5-1,5 GB Daten der OOM-Kill mitten im Notfall-Restore.

    Jetzt steht hier nur Pfad und Anzahl; `for doc in datei` liest die Datei
    erneut Dokument fuer Dokument. `len()` liefert die Anzahl — alle Stellen,
    die bisher `len(docs)` nutzten, bleiben unveraendert."""

    def __init__(self, pfad: Path, anzahl: int):
        self.pfad = Path(pfad)
        self.anzahl = int(anzahl)

    def __len__(self) -> int:
        return self.anzahl

    def __iter__(self):
        with gzip.open(self.pfad, "rb") as fh:
            yield from read_bson_stream(fh)

    def __repr__(self) -> str:
        return f"BsonDatei({self.pfad.name}, {self.anzahl} Dokumente)"


#: RP-545: so viele Dokumente je insert_many (statt der ganzen Collection).
LADE_PAKET = 1000


def dokumente_laden(coll, docs) -> int:
    """Dokumente paketweise einfuegen (RP-545) — `docs` darf eine Liste oder
    eine BsonDatei sein. Liefert die Anzahl der eingefuegten Dokumente."""
    n, paket = 0, []
    for doc in docs:
        paket.append(doc)
        if len(paket) >= LADE_PAKET:
            coll.insert_many(paket, ordered=False)
            n += len(paket)
            paket = []
    if paket:
        coll.insert_many(paket, ordered=False)
        n += len(paket)
    return n


def sha256_datei(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def index_metadaten_mangel(meta_path: Path, soll_namen=None) -> str:
    """Runde 21 (Befund B): Mangel der metadata.json einer Collection
    ("" = in Ordnung). soll_namen: Indexnamen laut Manifest-Feld "indexe"
    (None = Manifest ohne diese Angabe)."""
    if not meta_path.is_file():
        return "fehlen (metadata.json nicht vorhanden)"
    try:
        meta = json_util.loads(meta_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return f"unlesbar ({exc})"
    grund = metadaten_mangel(meta)
    if grund:
        return f"ungueltig ({grund})"
    if soll_namen is not None:
        ist = sorted(str(i.get("name")) for i in meta["indexes"])
        if sorted(str(n) for n in soll_namen) != ist:
            return (f"passen nicht zum Manifest (Manifest: {sorted(soll_namen)}, "
                    f"Datei: {ist})")
    return ""


def pruefe_backup(root: Path, allow_no_manifest: bool,
                  alt_ohne_indexdaten: bool = False):
    """Liefert (dumps: {name: (docs, metadata-Pfad)}, manifest|None, db_dir).
    Wirft bei jedem Fehler. `docs` ist seit RP-545 eine BsonDatei (Anzahl
    per len(), Dokumente per Iteration aus der Datei) statt einer Liste.

    Runde 21 (Befund B): metadata.json ist fuer jede Collection Pflicht.
    Nur mit alt_ohne_indexdaten (--alt-backup-ohne-indexdaten) wird eine
    fehlende/unlesbare Datei hingenommen; der metadata-Pfad ist dann None
    (= fuer diese Collection keine Indexe anlegen oder pruefen)."""
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

    manifest_indexe = (manifest or {}).get("indexe")
    if not isinstance(manifest_indexe, dict):
        manifest_indexe = None
    dumps, index_maengel = {}, []
    for f in sorted(db_dir.glob("*.bson.gz")):
        name = f.name[:-len(".bson.gz")]
        # RP-545: vollstaendig einlesen (jedes Dokument wird dekodiert, eine
        # abgeschnittene Datei faellt weiter auf) — aber nur ZAEHLEN, nichts
        # behalten.
        with gzip.open(f, "rb") as fh:
            anzahl = sum(1 for _ in read_bson_stream(fh))
        docs = BsonDatei(f, anzahl)
        erwartet = (manifest or {}).get("collections", {}).get(name)
        if erwartet is not None and erwartet != len(docs):
            raise ValueError(f"{name}: {len(docs)} Dokumente gelesen, Manifest "
                             f"erwartet {erwartet}")
        meta_path = db_dir / f"{name}.metadata.json"
        if manifest_indexe is not None and name not in manifest_indexe:
            grund = "im Manifest (Feld 'indexe') nicht verzeichnet"
        else:
            grund = index_metadaten_mangel(
                meta_path, manifest_indexe.get(name) if manifest_indexe else None)
        if grund:
            if not alt_ohne_indexdaten:
                index_maengel.append(f"{name}: Index-Metadaten {grund}")
            else:
                print(f"  !!! WARNUNG: {name}: Index-Metadaten {grund} — wegen "
                      f"--alt-backup-ohne-indexdaten werden fuer {name} KEINE Indexe "
                      f"angelegt oder geprueft (Unique-/TTL-Indexe fehlen, bis das "
                      f"Backend sie beim Start neu anlegt)")
                meta_path = None
        dumps[name] = (docs, meta_path)
        print(f"  {name}: {len(docs)} Dokumente gelesen")
    if index_maengel:
        raise ValueError(
            "Index-Metadaten fehlen oder sind unlesbar — ohne sie wuerden Unique- "
            "und TTL-Indexe nach dem Restore unbemerkt fehlen: "
            + "; ".join(index_maengel[:10])
            + ". Nur fuer Alt-Backups ohne Indexdaten: --alt-backup-ohne-indexdaten")
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
_INDEX_OPTIONEN = ("unique", "sparse", "expireAfterSeconds", "partialFilterExpression")


def erwartete_indexe(meta_path) -> dict:
    """{indexname: (keys, optionen)} aus einer metadata.json (ohne _id_).

    Runde 21 (Befund B): Eine fehlende oder unlesbare Datei wirft. Bisher
    hiess das still "keine Indexe erwartet", und Unique-/TTL-Indexe fehlten
    unbemerkt. meta_path None = ausdruecklich ohne Indexdaten
    (--alt-backup-ohne-indexdaten). Gelesen wird mit json_util (Datumswerte
    u. ae. in partialFilterExpression bleiben erhalten)."""
    if meta_path is None:
        return {}
    meta = json_util.loads(Path(meta_path).read_text(encoding="utf-8"))
    grund = metadaten_mangel(meta)
    if grund:
        raise ValueError(f"{Path(meta_path).name} ungueltig ({grund})")
    out = {}
    for idx in meta.get("indexes") or []:
        name = idx.get("name")
        if not name or name == "_id_":
            continue
        keys = [(k, v) for k, v in (idx.get("key") or {}).items()]
        if not keys:
            continue
        opts = {k: v for k, v in idx.items() if k in _INDEX_OPTIONEN}
        out[name] = (keys, opts)
    return out


def _ohne_ttl(soll: dict) -> dict:
    """RP-544: dieselben Indexe, aber ohne Ablaufzeit (expireAfterSeconds)."""
    return {name: (keys, {k: v for k, v in opts.items() if k != "expireAfterSeconds"})
            for name, (keys, opts) in soll.items()}


def indexe_anlegen(coll, meta_path, ttl_spaeter: bool = False) -> list:
    """Indexe laut metadata.json anlegen; liefert die Liste der Fehler.

    Rollenprüfung 22.09.2026 (RP-544): `ttl_spaeter=True` legt TTL-Indexe
    zunaechst OHNE Ablaufzeit an. Sonst loeschte der TTL-Waechter der
    Datenbank (alle 60 s) in der temporaeren Datenbank sofort alle Dokumente,
    die seit der Sicherung abgelaufen sind (vehicle_cache, rate_limits,
    link_jobs, mail_idempotenz, betriebsalarme ...). Die Kontrolle verlangt
    aber exakt die Zahl aus dem Manifest — der Restore brach in Schritt 3 ab
    bzw. rollte nach Schritt 6 zurueck, je aelter die Sicherung, desto
    sicherer. Die Ablaufzeit setzt ttl_aktivieren() erst nach der Kontrolle."""
    fehler = []
    try:
        soll = erwartete_indexe(meta_path)
    except Exception as exc:  # noqa: BLE001
        return [f"{coll.name}: Index-Metadaten nicht lesbar ({exc})"]
    if ttl_spaeter:
        soll = _ohne_ttl(soll)
    for name, (keys, opts) in soll.items():
        try:
            coll.create_index(keys, name=name, **opts)
        except Exception as exc:  # noqa: BLE001
            fehler.append(f"Index {coll.name}.{name} nicht angelegt: {exc}")
    return fehler


def ttl_indexe(dumps: dict) -> list:
    """RP-544: [(collection, indexname, expireAfterSeconds)] aller TTL-Indexe
    laut Sicherung."""
    out = []
    for name, (_docs, meta_path) in dumps.items():
        try:
            soll = erwartete_indexe(meta_path)
        except Exception:  # noqa: BLE001 — meldet pruefe_datenbank
            continue
        for idx_name, (_keys, opts) in soll.items():
            if opts.get("expireAfterSeconds") is not None:
                out.append((name, idx_name, opts["expireAfterSeconds"]))
    return out


def ttl_aktivieren(db, dumps: dict) -> list:
    """RP-544: die zunaechst ohne Ablaufzeit angelegten TTL-Indexe per collMod
    scharf schalten (MongoDB >= 5.1 wandelt so einen normalen Einzelfeld-Index
    in einen TTL-Index um). Liefert die Liste der Fehler."""
    fehler = []
    for coll_name, idx_name, sekunden in ttl_indexe(dumps):
        try:
            db.command("collMod", coll_name,
                       index={"name": idx_name, "expireAfterSeconds": int(sekunden)})
        except Exception as exc:  # noqa: BLE001
            fehler.append(f"TTL-Index {coll_name}.{idx_name} nicht scharf geschaltet: {exc}")
    return fehler


def erwartete_anzahl(manifest, name: str, gelesen: int) -> int:
    e = (manifest or {}).get("collections", {}).get(name)
    return gelesen if e is None else int(e)


def _norm(wert):
    """Zahlen gleich behandeln (1 == 1.0, Int64 == int), Dicts/Listen
    rekursiv; bool bleibt bool."""
    if isinstance(wert, bool) or wert is None:
        return wert
    if isinstance(wert, (int, float)):
        return float(wert)
    if isinstance(wert, dict):
        return {str(k): _norm(v) for k, v in wert.items()}
    if isinstance(wert, (list, tuple)):
        return [_norm(v) for v in wert]
    return wert


def _anzeige(wert) -> str:
    if wert is None or wert is False:
        return "fehlt"
    if isinstance(wert, float) and wert.is_integer():
        return str(int(wert))
    if isinstance(wert, dict):
        return "{" + ", ".join(f"{k}: {_anzeige(v)}" for k, v in wert.items()) + "}"
    if isinstance(wert, list):
        return "[" + ", ".join(_anzeige(v) for v in wert) + "]"
    return str(wert)


def _index_eigenschaften(keys, opts: dict) -> dict:
    """Vergleichbare Form eines Index (Soll aus metadata.json, Ist aus
    index_information()). unique/sparse False gleich fehlt."""
    ttl = opts.get("expireAfterSeconds")
    pfe = opts.get("partialFilterExpression")
    return {
        "key": [[str(k), _norm(v)] for k, v in (keys or [])],
        "unique": bool(opts.get("unique")),
        "sparse": bool(opts.get("sparse")),
        "expireAfterSeconds": None if ttl is None else _norm(ttl),
        "partialFilterExpression": None if pfe is None else _norm(pfe),
    }


def index_abweichungen(coll_name: str, soll: dict, ist_info: dict) -> list:
    """Runde 21 (Befund B): je gleichnamigem Index Schluessel (mit
    Reihenfolge), unique, sparse, expireAfterSeconds und
    partialFilterExpression vergleichen. Ein Index gleichen Namens, der
    nicht mehr eindeutig ist oder keine Ablaufzeit mehr hat, faellt so auf."""
    out = []
    for idx_name, (keys, opts) in soll.items():
        info = ist_info.get(idx_name)
        if info is None:
            continue  # fehlende Indexe meldet pruefe_datenbank gesondert
        s = _index_eigenschaften(keys, opts)
        i = _index_eigenschaften(info.get("key") or [], info)
        for feld in ("key", "unique", "sparse", "expireAfterSeconds",
                     "partialFilterExpression"):
            if s[feld] != i[feld]:
                out.append(f"{coll_name}.{idx_name}: {feld} soll {_anzeige(s[feld])}, "
                           f"ist {_anzeige(i[feld])}")
    return out


def pruefe_datenbank(db, dumps: dict, manifest, ttl_ausstehend: bool = False,
                     nur_indexe: bool = False) -> list:
    """Dokumentzahlen (gegen Manifest bzw. gelesene Dokumente) und Indexe
    jeder Collection in db pruefen. Liefert die Liste der Abweichungen.
    Runde 21: Indexe nach Name UND Eigenschaften (index_abweichungen).

    Rollenprüfung 22.09.2026 (RP-544): `ttl_ausstehend=True` erwartet die
    TTL-Indexe noch OHNE Ablaufzeit (sie werden erst nach der Kontrolle
    scharf geschaltet); `nur_indexe=True` prueft nach dem Scharfschalten nur
    noch die Indexe — die Dokumentzahl darf dann durch den TTL-Waechter
    sinken, genau wie im laufenden Betrieb."""
    probleme = []
    for name, (docs, meta_path) in dumps.items():
        if not nur_indexe:
            soll = erwartete_anzahl(manifest, name, len(docs))
            ist = db[name].count_documents({})
            if ist != soll:
                probleme.append(f"{name}: {ist} Dokumente, erwartet {soll}")
        try:
            soll_idx = erwartete_indexe(meta_path)
        except Exception as exc:  # noqa: BLE001
            probleme.append(f"{name}: Index-Metadaten nicht lesbar ({exc})")
            continue
        if ttl_ausstehend:
            soll_idx = _ohne_ttl(soll_idx)
        try:
            ist_info = db[name].index_information()
        except Exception as exc:  # noqa: BLE001
            probleme.append(f"{name}: Indexe nicht lesbar ({exc})")
            continue
        fehlend = sorted(set(soll_idx) - set(ist_info))
        if fehlend:
            probleme.append(f"{name}: Index(e) fehlen: {', '.join(fehlend)}")
        probleme += index_abweichungen(name, soll_idx, ist_info)
    return probleme


# ----------------------------------------------------------- Datei-Speicher
#: Pruefbericht 20.09.2026 (SK-01): Im Container sind /app/uploads und
#: /app/local_storage eingehaengte Volumes. Einen Einhaengepunkt kann man
#: nicht umbenennen (EBUSY) — der Restore rollte deshalb bei JEDEM Backup mit
#: Dateien in Schritt 5 zurueck. Bei einem Einhaengepunkt liegen Staging und
#: Vorher-Stand deshalb als Unterordner IM Volume, und umgeschaltet wird
#: Eintrag fuer Eintrag (dasselbe Dateisystem, also schnelle Renames).
INNEN_STAGING = ".restore-"
INNEN_VORHER = ".vorher-"


def _ist_einhaengepunkt(pfad: Path) -> bool:
    """In Tests austauschbar."""
    try:
        return os.path.ismount(pfad)
    except OSError:
        return False


def _staging_ziel(live_dir: Path, stamp: str) -> Path:
    if live_dir.exists() and _ist_einhaengepunkt(live_dir):
        return live_dir / f"{INNEN_STAGING}{stamp}"
    return live_dir.parent / f"{live_dir.name}.restore-{stamp}"


def _innen(live_dir: Path, staging_dir: Path) -> bool:
    return Path(staging_dir).parent == Path(live_dir)


def _eintraege(ordner: Path) -> list:
    """Inhalt eines Datei-Speichers OHNE die Hilfsordner des Restores."""
    if not ordner.is_dir():
        return []
    return [e for e in ordner.iterdir()
            if not e.name.startswith((INNEN_STAGING, INNEN_VORHER))]


def _dateien_zaehlen(ordner: Path) -> int:
    n = 0
    for e in _eintraege(ordner):
        if e.is_file():
            n += 1
        elif e.is_dir():
            n += sum(1 for f in e.rglob("*") if f.is_file())
    return n


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
        ziel = _staging_ziel(live_dir, stamp)
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


def _innen_umschalten(live_dir: Path, stg: Path, stamp: str) -> Path:
    """SK-01: Umschalten INNERHALB eines eingehaengten Volumes. Liefert den
    Vorher-Ordner; bei einem Fehler ist alles Bewegte zurueckgelegt."""
    vorher = live_dir / f"{INNEN_VORHER}{stamp}"
    n = 1
    while vorher.exists():
        n += 1
        vorher = live_dir / f"{INNEN_VORHER}{stamp}-{n}"
    alt_bewegt, neu_bewegt = [], []
    try:
        vorher.mkdir()
        for e in _eintraege(live_dir):
            _verzeichnis_umbenennen(e, vorher / e.name)
            alt_bewegt.append(e.name)
        for e in list(stg.iterdir()):
            _verzeichnis_umbenennen(e, live_dir / e.name)
            neu_bewegt.append(e.name)
        stg.rmdir()
    except BaseException:
        # zuruecklegen, was schon bewegt wurde (best effort)
        stg.mkdir(exist_ok=True)
        for nm in reversed(neu_bewegt):
            try:
                _verzeichnis_umbenennen(live_dir / nm, stg / nm)
            except Exception:  # noqa: BLE001
                pass
        for nm in reversed(alt_bewegt):
            try:
                _verzeichnis_umbenennen(vorher / nm, live_dir / nm)
            except Exception:  # noqa: BLE001
                pass
        try:
            vorher.rmdir()
        except OSError:
            pass
        raise
    return vorher


def _innen_zuruecknehmen(e: dict) -> None:
    """SK-01: Gegenstueck zu _innen_umschalten (Backup-Stand raus, Vorher-Stand
    zurueck ins Volume)."""
    live_dir, stg, vorher = e["live"], e["staging"], e["vorher"]
    stg.mkdir(exist_ok=True)
    for x in _eintraege(live_dir):
        _verzeichnis_umbenennen(x, stg / x.name)
    for x in list(vorher.iterdir()):
        _verzeichnis_umbenennen(x, live_dir / x.name)
    vorher.rmdir()
    shutil.rmtree(stg, ignore_errors=True)


def verzeichnisse_umschalten(staging: dict, live: dict, stamp: str, geschaltet=None):
    """Je Datei-Speicher: live -> <live>.vorher-<stamp>, Staging -> live.
    Liefert ([umgeschaltete Eintraege], fehler|None). Ein Eintrag:
    {name, live, vorher (None wenn es keinen Live-Ordner gab), staging}.
    SK-01: liegt der Staging-Ordner IM Live-Ordner (Einhaengepunkt), wird
    innerhalb des Volumes umgeschaltet (Eintrag "innen": True).
    SK-03: `geschaltet` darf eine Liste des Aufrufers sein — sie traegt den
    Fortschritt auch bei einem harten Abbruch (Strg+C)."""
    geschaltet = geschaltet if geschaltet is not None else []
    for name, stg in staging.items():
        live_dir = live[name]
        if _innen(live_dir, stg):
            try:
                vorher = _innen_umschalten(live_dir, stg, stamp)
            except Exception as exc:  # noqa: BLE001
                return geschaltet, f"Datei-Speicher {name}: {exc}"
            geschaltet.append({"name": name, "live": live_dir, "vorher": vorher,
                               "staging": stg, "innen": True})
            print(f"  {name}: umgeschaltet (im Volume, bisher -> {vorher.name})")
            continue
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
            if e.get("innen"):
                _innen_zuruecknehmen(e)
                continue
            _verzeichnis_umbenennen(e["live"], e["staging"])
            if e["vorher"] is not None:
                _verzeichnis_umbenennen(e["vorher"], e["live"])
            shutil.rmtree(e["staging"], ignore_errors=True)
        except Exception as exc:  # noqa: BLE001
            fehler.append(f"Datei-Speicher {e['name']}: Rueckgaengig fehlgeschlagen — "
                          f"{exc} (Backup-Stand: {e['staging']}, bisheriger "
                          f"Stand: {e['vorher']})")
    return fehler


#: Praefix, unter dem der bisherige Stand eines ueberschriebenen S3-Objekts
#: liegt, solange der Restore laeuft (Nachpruefung 20.09.2026, Nr. 77).
S3_RUECKNAHME_PREFIX = "restore-vorher/"


def _s3_client():
    import boto3
    return boto3.client("s3", endpoint_url=os.environ["S3_ENDPOINT"],
                        aws_access_key_id=os.environ["S3_ACCESS_KEY"],
                        aws_secret_access_key=os.environ["S3_SECRET_KEY"],
                        region_name=os.environ.get("S3_REGION", "auto"))


def s3_zurueckspielen(s3_dir: Path, objekte: list, stamp: str = "",
                      gesichert: list = None) -> int:
    """Objekte aus dem Backup in den Datei-Speicher zurueckspielen.

    Nachpruefung 20.09.2026, Nr. 77: Vorher wurde hier DIREKT ueber den
    Live-Stand geschrieben — vor dem Umschalten von Datenbank und lokalen
    Dateien. Scheiterte danach das Umschalten, wurden Datenbank und Ordner
    zurueckgedreht, die schon ueberschriebenen S3-Objekte aber NICHT. Die
    weiterlaufende (alte) Datenbank zeigte dann auf zurueckgespielte, also
    aeltere oder halb ersetzte Dateien. Der Kopf dieser Datei versprach
    ausdruecklich das Gegenteil.

    Jetzt wird der bisherige Stand jedes Objekts VOR dem Ueberschreiben im
    selben Eimer unter `restore-vorher/<stamp>/` gesichert — Server zu
    Server, ohne Herunterladen. `gesichert` sammelt, was zurueckgenommen
    werden kann; `s3_zuruecknehmen()` macht es rueckgaengig."""
    c = _s3_client()
    bucket = os.environ["S3_BUCKET"]
    sicherung = f"{S3_RUECKNAHME_PREFIX}{stamp}/" if stamp else ""
    n = 0
    try:
        for f in objekte:
            key = str(f.relative_to(s3_dir)).replace("\\", "/")
            if gesichert is not None:
                gab_es = True
                try:
                    c.head_object(Bucket=bucket, Key=key)
                except Exception:  # noqa: BLE001 — Objekt existiert noch nicht
                    gab_es = False
                if gab_es and sicherung:
                    c.copy_object(Bucket=bucket, Key=sicherung + key,
                                  CopySource={"Bucket": bucket, "Key": key})
                gesichert.append((key, gab_es))
            c.upload_file(str(f), bucket, key)
            n += 1
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"nach {n} von {len(objekte)} Objekten: {exc}") from exc
    return n


def s3_zuruecknehmen(gesichert: list, stamp: str) -> list:
    """Den S3-Stand von vor dem Restore wiederherstellen (Nr. 77).

    Objekte, die es vorher gab, kommen aus `restore-vorher/<stamp>/`
    zurueck; Objekte, die der Restore neu angelegt hat, werden entfernt.
    Liefert die Liste der Schluessel, bei denen das NICHT geklappt hat —
    der Aufrufer meldet sie laut, statt sie zu verschlucken."""
    if not gesichert:
        return []
    c = _s3_client()
    bucket = os.environ["S3_BUCKET"]
    sicherung = f"{S3_RUECKNAHME_PREFIX}{stamp}/"
    fehler = []
    for key, gab_es in gesichert:
        try:
            if gab_es:
                c.copy_object(Bucket=bucket, Key=key,
                              CopySource={"Bucket": bucket, "Key": sicherung + key})
            else:
                c.delete_object(Bucket=bucket, Key=key)
        except Exception as exc:  # noqa: BLE001
            fehler.append(f"s3://{bucket}/{key}: {exc}")
    return fehler


def s3_sicherung_aufraeumen(gesichert: list, stamp: str) -> None:
    """Die Ruecknahme-Kopien entfernen — erst NACH einem gelungenen Restore.
    Fehler sind hier folgenlos (es bleibt hoechstens Speicher liegen)."""
    if not gesichert:
        return
    try:
        c = _s3_client()
        bucket = os.environ["S3_BUCKET"]
        sicherung = f"{S3_RUECKNAHME_PREFIX}{stamp}/"
        for key, gab_es in gesichert:
            if gab_es:
                try:
                    c.delete_object(Bucket=bucket, Key=sicherung + key)
                except Exception:  # noqa: BLE001
                    pass
    except Exception as exc:  # noqa: BLE001
        print(f"  Hinweis: Ruecknahme-Kopien unter {S3_RUECKNAHME_PREFIX}{stamp}/ "
              f"konnten nicht entfernt werden ({exc}) — sie stoeren nicht, "
              f"belegen aber Speicher.")


# ------------------------------------------------------------- Collections
def _rename_collection(client, von: str, nach: str) -> None:
    """Ein renameCollection-Schritt (in Tests austauschbar)."""
    client.admin.command("renameCollection", von, to=nach, dropTarget=True)


def collections_umschalten(client, ziel_name: str, tmp_name: str, alt_name: str,
                           namen: list, vorhandene: set, stand=None):
    """Je Collection: ziel -> alt (falls vorhanden), tmp -> ziel.
    Liefert (umgeschaltet, halb, fehler). 'halb' ist die Collection, deren
    bisheriger Stand schon nach alt verschoben war, als der zweite Schritt
    scheiterte (None, wenn nichts halb ist).
    SK-03: `stand` (dict) traegt den Fortschritt laufend mit
    ({"umgeschaltet": [...], "halb": name|None}) — auch wenn der Lauf hart
    abbricht (Strg+C, Verbindung weg), weiss der Aufrufer, was zurueck muss."""
    stand = stand if stand is not None else {}
    umgeschaltet = stand.setdefault("umgeschaltet", [])
    stand.setdefault("halb", None)
    for name in namen:
        alt_da = False
        try:
            if name in vorhandene:
                _rename_collection(client, f"{ziel_name}.{name}", f"{alt_name}.{name}")
                alt_da = True
                stand["halb"] = name
            _rename_collection(client, f"{tmp_name}.{name}", f"{ziel_name}.{name}")
            stand["halb"] = None
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
def schema_version_setzen(ziel_db, flags_dump) -> None:
    """Phase 3 (3.5, E5): system_flags.schema aus dem Backup uebernehmen."""
    from datetime import datetime, timezone
    docs = (flags_dump or (None, None))[0] or []
    schema = next((d for d in docs if isinstance(d, dict) and d.get("_id") == "schema"), None)
    if schema is None:
        ziel_db[FLAG_COLLECTION].delete_one({"_id": "schema"})
        print("  Schema-Version: im Backup nicht vorhanden — Eintrag entfernt, die "
              "Migrationen laufen beim naechsten Start von Anfang an")
        return
    version = int(schema.get("version") or 0)
    ziel_db[FLAG_COLLECTION].update_one(
        {"_id": "schema"},
        {"$set": {"version": version, "updated_at": datetime.now(timezone.utc).isoformat(),
                  "restore_hinweis": "aus Backup uebernommen"}},
        upsert=True)
    print(f"  Schema-Version {version} aus dem Backup uebernommen — fehlende Migrationen "
          "laufen beim naechsten Start")


def wartungsmodus(ziel_db, aktiv: bool, grund: str = "Restore") -> None:
    """system_flags.wartungsmodus setzen/aufheben — die API antwortet bei
    aktiv=True mit 503 (Middleware im Backend).

    Nachpruefung 20.09.2026: Umfang "alles" — waehrend einer
    Wiederherstellung darf auch NICHT GELESEN werden, denn die Datenbank
    ist zwischendurch halb alt und halb neu. Bewusst OHNE Ablaufzeit: ein
    abgebrochener Restore muss die Plattform gesperrt lassen, bis jemand
    nachgesehen hat. /api/ready meldet ihn als Fehler, der Lastverteiler
    nimmt den Server damit aus dem Verkehr, und der Waechter beim
    Serverstart raeumt nur Merker MIT abgelaufener Frist weg."""
    coll = ziel_db[FLAG_COLLECTION]
    if aktiv:
        # Rollenprüfung 22.09.2026 (RP-246/RP-397): Der Restore ist der
        # STAERKERE Merker und darf eine laufende Schreibpause der Sicherung
        # bewusst ersetzen. Umgekehrt nicht mehr: wartung.setzen() (Sicherung)
        # laesst diesen Merker jetzt stehen, und die Sicherung startet gar
        # nicht erst, solange er gilt.
        coll.replace_one(
            {"_id": FLAG_ID},
            {"_id": FLAG_ID, "aktiv": True, "grund": grund,
             "umfang": wartung.UMFANG_ALLES, "besitzer": "restore",
             "seit": datetime.now(timezone.utc).isoformat()},
            upsert=True)
    else:
        wartung.aufheben(coll, "restore", zwang=True)


def _zahl_env(name: str, standard: float) -> float:
    try:
        return max(0.0, float(os.environ.get(name, "").strip() or standard))
    except ValueError:
        return standard


def _utc(wert):
    if isinstance(wert, datetime):
        return wert if wert.tzinfo else wert.replace(tzinfo=timezone.utc)
    return None


def _backend_laeuft(ziel_db) -> bool:
    """RP-245: Arbeitet ueberhaupt ein Backend gegen diese Datenbank? Jeder
    Backend-Prozess nimmt stuendlich die Sperre des Aufraeumlaufs; wurde sie
    in den letzten zwei Stunden genommen, laeuft eins. (Ohne Backend — Probe
    in eine Testdatenbank — gibt es nichts abzuwarten.)"""
    try:
        doc = ziel_db.job_locks.find_one({"name": "cleanup-cycle"},
                                         {"_id": 0, "acquired_at": 1, "expires_at": 1})
    except Exception:  # noqa: BLE001
        return True                     # im Zweifel lieber warten
    if not doc:
        return False
    from datetime import timedelta as _td
    genommen = _utc(doc.get("acquired_at")) or _utc(doc.get("expires_at"))
    return bool(genommen) and genommen > datetime.now(timezone.utc) - _td(hours=2)


def hintergrund_abwarten(ziel_db) -> bool:
    """Rollenprüfung 22.09.2026 (RP-245/RP-396): Nach dem Setzen des
    Wartungsmodus begann der Restore SOFORT mit S3-Upload und Umschalten —
    ohne zu warten, ob ein Aufraeumlauf gerade loescht oder eine Anfrage noch
    schreibt. Der Runbook-Weg (docker compose exec backend) heisst aber: das
    Backend LAEUFT dabei.

    Jetzt: RESTORE_AUSLAUF_MIN_S (Standard 6 s) warten, bis alle Prozesse den
    Merker gelesen haben, dann bis RESTORE_AUSLAUF_MAX_S (Standard 180 s)
    darauf, dass der Aufraeumlauf steht (lauf_aktiv an seiner Sperre) und
    alle Prozesse null offene Schreibzugriffe melden (wartung_schreiber, wie
    bei der Sicherung). True = ruhig. Nach Ablauf wird mit Warnung
    weitergemacht — ein Notfall-Restore darf nicht an einem haengenden
    Prozess scheitern; der Aufraeumlauf haelt vor seinem naechsten Schritt
    ohnehin selbst an."""
    import time as _time
    if not _backend_laeuft(ziel_db):
        print("  kein laufendes Backend erkannt — nichts abzuwarten")
        return True
    mindestens = _zahl_env("RESTORE_AUSLAUF_MIN_S", 6)
    hoechstens = _zahl_env("RESTORE_AUSLAUF_MAX_S", 180)
    print(f"  warte mindestens {mindestens:.0f} s, bis alle Backend-Prozesse den "
          f"Wartungsmodus sehen, dann bis {hoechstens:.0f} s auf laufende Arbeiten ...")
    _time.sleep(mindestens)
    ende = _time.monotonic() + hoechstens
    while True:
        try:
            aufraeumen = ziel_db.job_locks.count_documents(
                {"name": "cleanup-cycle", "lauf_aktiv": True,
                 "expires_at": {"$gt": datetime.now(timezone.utc)}}, limit=1)
            ruhig, offen, prozesse = wartung.schreiber_stand(
                ziel_db[wartung.SCHREIBER_COLLECTION])
        except Exception as exc:  # noqa: BLE001
            print(f"  WARNUNG: Stand der laufenden Arbeiten nicht lesbar ({exc}) — "
                  f"es wird trotzdem fortgefahren")
            return False
        if not aufraeumen and (ruhig or prozesse == 0):
            print("  keine laufenden Schreibzugriffe mehr"
                  + (f" ({prozesse} Prozesse melden 0)" if prozesse else ""))
            return True
        if _time.monotonic() >= ende:
            print(f"  !!! WARNUNG: nach {hoechstens:.0f} s "
                  + ("laeuft der Aufraeumlauf noch" if aufraeumen else
                     f"melden {prozesse} Prozesse noch {offen} offene Schreibzugriffe")
                  + " — der Restore faehrt trotzdem fort (der Aufraeumlauf haelt "
                    "vor seinem naechsten Schritt selbst an).")
            return False
        _time.sleep(1)


def _wartungsmodus_befehl(ziel_name: str) -> str:
    # Pruefbericht 20.09.2026 (SK-04): vorher stand hier ein mongosh-Aufruf —
    # im Backend-Container gibt es kein mongosh, und die Datenbank verlangt
    # eine Anmeldung. Der Befehl lief also nie. Jetzt ein Skript im Image.
    return (f"docker compose exec backend python scripts/wartung_aufheben.py "
            f"--db {ziel_name} --ja")


# ------------------------------------------------------------------ Ablauf
def _rollback(client, ziel_name, tmp_name, alt_name, umgeschaltet, halb,
              vorhandene, geschaltet, grund, s3_gesichert=None, stamp="") -> int:
    print(f"FEHLER: {grund}")
    print(f"ROLLBACK: {len(umgeschaltet) + (1 if halb else 0)} Collection(s), "
          f"{len(geschaltet)} Datei-Speicher"
          + (f" und {len(s3_gesichert)} S3-Objekte" if s3_gesichert else "")
          + " werden zurueckgedreht ...")
    fehler = collections_zuruecknehmen(client, ziel_name, tmp_name, alt_name,
                                       umgeschaltet, halb, vorhandene)
    fehler += verzeichnisse_zuruecknehmen(geschaltet)
    # Nr. 77: Der Datei-Speicher gehoert mit zurueckgedreht — sonst zeigt die
    # wiederhergestellte alte Datenbank auf zurueckgespielte Dateien.
    if s3_gesichert:
        fehler += s3_zuruecknehmen(s3_gesichert, stamp)
    if fehler:
        print("!!! ROLLBACK UNVOLLSTAENDIG — Zieldatenbank/Datei-Speicher sind "
              "GEMISCHT. Manuell pruefen:")
        for f in fehler:
            print(f"!!!   - {f}")
        print(f"!!! Backup-Stand liegt in '{tmp_name}', bisheriger Stand in "
              f"'{alt_name}'. Der Wartungsmodus bleibt AKTIV; nach der "
              f"Bereinigung aufheben mit:\n    {_wartungsmodus_befehl(ziel_name)}")
        return 1
    try:
        client.drop_database(tmp_name)
        client.drop_database(alt_name)
    except Exception as exc:  # noqa: BLE001
        print(f"  Hinweis: temporaere Datenbanken nicht entfernt ({exc}) — "
              f"'{tmp_name}' und '{alt_name}' von Hand loeschen.")
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
        dumps, manifest, db_dir = pruefe_backup(
            root, args.allow_no_manifest,
            alt_ohne_indexdaten=getattr(args, "alt_backup_ohne_indexdaten", False))
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
    # Runde 21 (Pruefbefund Backup A): ein Rueckfall nach gescheitertem
    # Snapshot ist kein stimmiger Stand (z. B. Termine ohne Vertrag).
    grund_inkonsistent = inkonsistenz(manifest)
    if grund_inkonsistent:
        if not getattr(args, "notfall_inkonsistent_akzeptieren", False):
            print("FEHLER: Dieses Backup ist als INKONSISTENT markiert — die "
                  "Collections zeigen unterschiedliche Zeitstaende:")
            print(f"  - {grund_inkonsistent}")
            print("Besser ein gutes (stimmiges) Backup waehlen. Einspielen nur im "
                  "Notfall mit --notfall-inkonsistent-akzeptieren (danach koennen "
                  "z. B. Termine ohne Vertrag vorliegen; Datenbestand pruefen).")
            print("Es wurde NICHTS veraendert.")
            return 1
        print("!!! WARNUNG: INKONSISTENTES Backup wird auf ausdruecklichen Wunsch "
              "(Notfall) eingespielt. Die Collections zeigen unterschiedliche "
              "Zeitstaende; nach dem Restore Datenbestand pruefen:")
        print(f"!!!   - {grund_inkonsistent}")
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
    flags_dump = None
    if FLAG_COLLECTION in dumps:
        flags_dump = dumps.pop(FLAG_COLLECTION)
        print(f"  Hinweis: {FLAG_COLLECTION} (Betriebs-Flags) wird nicht zurueckgespielt — "
              "nur die Schema-Version daraus wird uebernommen")
    if args.dry_run and not args.nur_datenbank:
        # SK-01: wie wird umgeschaltet, und ist das Ziel schreibbar?
        nicht_schreibbar = []
        for name, live_dir in live_verzeichnisse().items():
            if not (root / name).is_dir():
                continue
            ziel_probe = _staging_ziel(live_dir, "probe")
            basis = ziel_probe.parent
            while not basis.exists() and basis != basis.parent:
                basis = basis.parent
            art = ("im Volume (Einhaengepunkt)" if _innen(live_dir, ziel_probe)
                   else "per Umbenennen des Ordners")
            ok = os.access(basis, os.W_OK)
            print(f"  {name}: Umschalten {art} — {'schreibbar' if ok else 'NICHT schreibbar'}")
            if not ok:
                nicht_schreibbar.append(f"{name} ({basis})")
        if nicht_schreibbar:
            print("FEHLER: kein Schreibrecht fuer " + ", ".join(nicht_schreibbar)
                  + " — der Restore wuerde in Schritt 3 scheitern. Es wurde NICHTS veraendert.")
            return 1
    if args.dry_run:
        # Runde 21: "konsistent" nur sagen, wenn es stimmt.
        if grund_inkonsistent:
            print("DRY-RUN OK (NOTFALL): Pruefsummen und Inhalte in Ordnung, aber "
                  "das Backup ist INKONSISTENT; nichts veraendert.")
        elif ist_stichtagsgenau(manifest):
            print("DRY-RUN OK: Backup ist konsistent (stichtagsgenau); nichts veraendert.")
        else:
            print(f"DRY-RUN OK: Pruefsummen und Inhalte in Ordnung (Konsistenz: "
                  f"{(manifest or {}).get('konsistenz', 'unbekannt')}, nicht "
                  f"stichtagsgenau); nichts veraendert.")
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
            # RP-545: paketweise aus der Datei, nie die ganze Collection im
            # Speicher. RP-544: TTL-Indexe erst nach der Kontrolle scharf.
            if len(docs):
                dokumente_laden(tmp[name], docs)
            else:
                tmp.create_collection(name)
            index_fehler += indexe_anlegen(tmp[name], meta_path, ttl_spaeter=True)
            total += len(docs)
    except Exception as exc:  # noqa: BLE001
        print(f"FEHLER beim Laden: {exc} — temporaere Datenbank wird entfernt, "
              f"Zieldatenbank '{args.db}' ist unveraendert.")
        client.drop_database(tmp_name)
        return 1

    print("3/6 Pruefung VOR dem Umschalten (Dokumentzahlen, Indexe, Datei-Pruefsummen) ...")
    probleme = index_fehler + pruefe_datenbank(tmp, dumps, manifest, ttl_ausstehend=True)
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
    # RP-245: laufenden Aufraeumlauf und offene Schreibzugriffe abwarten,
    # bevor Dateien und Collections umgeschaltet werden.
    hintergrund_abwarten(ziel)
    n_s3 = 0
    s3_gesichert = []
    if s3_aktiv:
        print(f"  s3: {len(s3_objekte)} Objekte hochladen "
              f"(bisheriger Stand wird vorher unter "
              f"{S3_RUECKNAHME_PREFIX}{stamp}/ gesichert) ...")
        try:
            n_s3 = s3_zurueckspielen(root / "s3", s3_objekte, stamp, s3_gesichert)
        except BaseException as exc:  # noqa: BLE001  (SK-03: auch Strg+C)
            print(f"FEHLER beim Zurueckspielen nach S3: {exc}")
            # Nr. 77: auch hier die schon ueberschriebenen Objekte zurueckholen.
            s3_fehler = s3_zuruecknehmen(s3_gesichert, stamp)
            staging_entfernen(staging)
            client.drop_database(tmp_name)
            wartungsmodus(ziel, False)
            if s3_fehler:
                print("!!! ACHTUNG: diese S3-Objekte konnten NICHT zurueckgeholt "
                      "werden — der Datei-Speicher ist gemischt:")
                for f in s3_fehler[:20]:
                    print(f"!!!   - {f}")
                print(f"!!! Der Stand von vorher liegt unter "
                      f"{S3_RUECKNAHME_PREFIX}{stamp}/ im selben Eimer.")
                return 1
            print(f"Datenbank '{args.db}', lokale Datei-Speicher UND der "
                  f"S3-Datei-Speicher sind unveraendert. Wartungsmodus beendet.")
            return 1

    print(f"5/6 Umschalten (bisheriger Stand -> {alt_name} bzw. *.vorher-{stamp}) ...")
    # Pruefbericht 20.09.2026 (SK-03): Schritte 5 und 6 standen in keinem
    # try. Ein Verbindungsabbruch (list_collection_names, pruefe_datenbank)
    # oder Strg+C uebersprang Rueckbau UND Aufheben — die Plattform blieb im
    # Wartungsmodus ohne Ablaufzeit stehen. Jetzt fuehrt JEDER Abbruch in den
    # Rueckbau; der Fortschritt steht laufend in `stand`/`geschaltet`.
    vorhandene: set = set()
    geschaltet: list = []
    stand = {"umgeschaltet": [], "halb": None}
    fehler = None
    try:
        vorhandene = set(ziel.list_collection_names())
        _, fehler = verzeichnisse_umschalten(staging, live, stamp, geschaltet=geschaltet)
        if fehler is None:
            _, _, fehler = collections_umschalten(
                client, args.db, tmp_name, alt_name, list(dumps), vorhandene, stand=stand)
        if fehler is None:
            print(f"  {len(stand['umgeschaltet'])} Collections umgeschaltet")
            print("6/6 Kontrolle nach dem Umschalten ...")
            abweichungen = pruefe_datenbank(ziel, dumps, manifest, ttl_ausstehend=True)
            if abweichungen:
                fehler = ("Kontrolle nach dem Umschalten: "
                          + "; ".join(abweichungen[:10]))
        if fehler is None:
            # RP-544: erst jetzt — Zahlen stimmen — die Ablaufzeiten setzen,
            # danach die Indexe mit Ablaufzeit gegen die Sicherung pruefen.
            ttl = ttl_indexe(dumps)
            fehler_ttl = ttl_aktivieren(ziel, dumps)
            if not fehler_ttl:
                fehler_ttl = pruefe_datenbank(ziel, dumps, manifest, nur_indexe=True)
            if fehler_ttl:
                fehler = "TTL-Indexe: " + "; ".join(fehler_ttl[:10])
            elif ttl:
                print(f"  {len(ttl)} TTL-Index(e) scharf geschaltet — abgelaufene "
                      f"Eintraege raeumt die Datenbank ab jetzt selbst")
    except BaseException as exc:  # noqa: BLE001  (auch KeyboardInterrupt)
        fehler = f"Abbruch waehrend des Umschaltens ({type(exc).__name__}: {exc})"
    if fehler is not None:
        return _rollback(client, args.db, tmp_name, alt_name, stand["umgeschaltet"],
                         stand["halb"], vorhandene, geschaltet, fehler, s3_gesichert, stamp)

    client.drop_database(tmp_name)
    # Nr. 75: exakt ist der Standard — nur --zusaetzliche-behalten schaltet
    # es ab (dann ist der Stand bewusst gemischt).
    exakt = not getattr(args, "zusaetzliche_behalten", False)
    # Phase 3 (3.5, E5): Schema-Version des BACKUPS setzen — sonst bliebe die
    # neuere Live-Version stehen und Migrationen zwischen Backup- und Live-
    # Stand liefen beim naechsten Start nicht mehr.
    try:
        schema_version_setzen(ziel, flags_dump)
    except Exception as exc:  # noqa: BLE001
        # SK-03: Die Daten SIND eingespielt. Ohne die Schema-Version des
        # Backups liefen fehlende Migrationen beim Start nicht — deshalb
        # bleibt der Wartungsmodus bewusst an, bis das geklaert ist.
        print(f"!!! Schema-Version aus dem Backup konnte nicht gesetzt werden ({exc}).")
        print(f"!!! Die Daten sind eingespielt; der Wartungsmodus bleibt AKTIV. Nach "
              f"der Klaerung aufheben mit:\n    {_wartungsmodus_befehl(args.db)}")
        return 1
    exakt_fehler = []
    if exakt:
        # Phase 3 (3.5, E6): Collections, die es live gibt, im Backup aber
        # nicht, wandern in die Vorher-Datenbank — der Live-Stand entspricht
        # danach exakt dem Backup.
        #
        # Nachpruefung 20.09.2026, Nr. 76: Ein gescheitertes Verschieben
        # wurde nur GEDRUCKT. Danach setzte der Code `extra = []`, und der
        # Lauf meldete Exit 0 und "RESTORE OK" — obwohl der ausdruecklich
        # verlangte exakte Stand gar nicht erreicht war. Jetzt zaehlt jeder
        # Fehlschlag und der Lauf endet mit Exit 1.
        for name in sorted(set(ziel.list_collection_names()) - set(dumps) - {FLAG_COLLECTION}):
            try:
                _rename_collection(client, f"{args.db}.{name}", f"{alt_name}.{name}")
                print(f"  exakt: {name} nicht im Backup -> nach {alt_name} verschoben")
            except Exception as exc:  # noqa: BLE001
                exakt_fehler.append(f"{name}: {exc}")
                print(f"  exakt: {name} konnte NICHT verschoben werden: {exc}")
    try:
        wartungsmodus(ziel, False)
    except Exception as exc:  # noqa: BLE001
        print(f"!!! Wartungsmodus konnte nicht aufgehoben werden ({exc}) — manuell:\n"
              f"    {_wartungsmodus_befehl(args.db)}")
        return 1
    extra = sorted(vorhandene - set(dumps) - {FLAG_COLLECTION})
    if exakt:
        # Nr. 76: NUR das, was wirklich verschoben wurde, verschwindet aus
        # der Liste. Was haengenblieb, steht weiter drin UND unten im Fehler.
        nicht_bewegt = {z.split(":", 1)[0] for z in exakt_fehler}
        extra = [n for n in extra if n in nicht_bewegt]
    if extra and not exakt:
        # Nr. 75: Das ist jetzt ein ausdruecklich gewaehlter Mischstand —
        # und muss auch so benannt werden, nicht als beilaeufiger "Hinweis".
        print(f"!!! ACHTUNG: --zusaetzliche-behalten war gesetzt. Diese "
              f"Collections sind NICHT aus dem Backup und stehen jetzt neben "
              f"dem Backup-Stand: {', '.join(extra)}")
        print("!!! Der Datenbestand ist damit GEMISCHT (alte Daten aus dem "
              "Backup neben neueren Collections). Bitte pruefen.")
    elif extra:
        print(f"  Hinweis: nicht im Backup enthalten und daher unveraendert "
              f"belassen: {', '.join(extra)}")
    n_files = sum(_dateien_zaehlen(e["live"]) for e in geschaltet)
    if exakt_fehler:
        print(f"!!! RESTORE UNVOLLSTAENDIG: {len(exakt_fehler)} Collection(s) "
              f"liegen weiterhin live, obwohl sie nicht im Backup sind — der "
              f"verlangte exakte Stand ist NICHT erreicht:")
        for z in exakt_fehler[:20]:
            print(f"!!!   - {z}")
        print(f"!!! Die Daten selbst sind eingespielt ({len(dumps)} Collections, "
              f"{total} Dokumente). Die genannten Collections von Hand nach "
              f"'{alt_name}' verschieben oder pruefen, ob sie bleiben duerfen.")
        print(f"Der vorherige Datenbestand liegt in '{alt_name}'.")
        return 1
    # Nr. 77: Die Ruecknahme-Kopien werden erst jetzt entfernt — vorher
    # haetten sie bei einem spaeten Fehler noch gebraucht werden koennen.
    s3_sicherung_aufraeumen(s3_gesichert, stamp)
    print(f"RESTORE OK: {len(dumps)} Collections, {total} Dokumente, "
          f"{n_files} Dateien, {n_s3} S3-Objekte -> {args.db}; Wartungsmodus beendet.")
    if grund_inkonsistent:
        print(f"!!! WARNUNG: eingespielt wurde ein INKONSISTENTES Backup "
              f"({grund_inkonsistent}) — Datenbestand pruefen.")
    print(f"Der vorherige Datenbestand liegt in '{alt_name}'"
          + ("".join(f", Ordner {e['vorher']}" for e in geschaltet if e["vorher"]))
          + ". Wenn alles passt, entfernen mit:  mongosh --eval "
          f"\"db.getSiblingDB('{alt_name}').dropDatabase()\"")
    # Aeltere Sicherungskopien entfernen — sonst sammeln sich mit jedem
    # Restore vollstaendige Kopien personenbezogener Daten an.
    try:
        weg = alte_sicherungen_aufraeumen(
            client, args.db, args.vorher_aufbewahrung,
            live_ordner=[e["live"] for e in geschaltet])
        if weg:
            print(f"Aufgeraeumt (aelter als {args.vorher_aufbewahrung} Tage): "
                  + ", ".join(weg))
    except Exception as exc:  # noqa: BLE001
        print(f"  Hinweis: Aufraeumen alter Sicherungskopien fehlgeschlagen ({exc}) "
              f"— sie liegen weiterhin bereit und koennen von Hand entfernt werden.")
    print("HINWEIS: Eindeutigkeits-Indizes prueft das Backend beim naechsten "
          "Start (ensure_indexes) — nach dem Restore einmal neu starten.")
    return 0


def alte_sicherungen_aufraeumen(client, db_name: str, tage: int,
                                behalte: int = 1, live_ordner=None) -> list:
    """Alte `<db>__vorher_<stamp>`-Datenbanken und `<live>.vorher-<stamp>`-
    Ordner entfernen (Audit 09/2026).

    Jeder Restore legt den BISHERIGEN Stand vollstaendig zur Seite — inklusive
    Kundendaten, Vertraegen und Fotos. Bisher blieb das fuer immer liegen:
    unnoetige Speicherung personenbezogener Daten und wachsender Platzbedarf.
    Aufgeraeumt wird nur, was aelter als `tage` ist; die juengsten `behalte`
    Staende bleiben immer erhalten (Sicherheitsnetz direkt nach dem Restore).
    `tage <= 0` schaltet das Aufraeumen ab. Liefert die entfernten Namen."""
    from datetime import datetime, timedelta
    entfernt = []
    if tage <= 0:
        return entfernt
    grenze = datetime.now() - timedelta(days=tage)

    def _stempel(text: str):
        try:
            return datetime.strptime(text, "%Y%m%d_%H%M%S")
        except ValueError:
            return None

    # --- Datenbanken
    kandidaten = []
    for name in client.list_database_names():
        if name.startswith(f"{db_name}__vorher_"):
            st = _stempel(name[len(f"{db_name}__vorher_"):])
            if st:
                kandidaten.append((st, name))
    kandidaten.sort(reverse=True)                      # neueste zuerst
    for st, name in kandidaten[behalte:]:
        if st < grenze:
            client.drop_database(name)
            entfernt.append(name)

    # --- Datei-Ordner (<live>.vorher-<stamp>)
    ordner = []
    for basis in (live_ordner or []):
        elternteil = Path(basis).parent
        if elternteil.is_dir():
            for eintrag in elternteil.iterdir():
                if eintrag.is_dir() and f"{Path(basis).name}.vorher-" in eintrag.name:
                    st = _stempel(eintrag.name.split(".vorher-")[-1].split("-")[0])
                    if st:
                        ordner.append((st, eintrag))
        # SK-01: Vorher-Staende IM eingehaengten Volume
        if Path(basis).is_dir():
            for eintrag in Path(basis).iterdir():
                if eintrag.is_dir() and eintrag.name.startswith(INNEN_VORHER):
                    st = _stempel(eintrag.name[len(INNEN_VORHER):].split("-")[0])
                    if st:
                        ordner.append((st, eintrag))
    ordner.sort(reverse=True)
    for st, eintrag in ordner[behalte:]:
        if st < grenze:
            shutil.rmtree(eintrag, ignore_errors=True)
            entfernt.append(str(eintrag))
    return entfernt


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
    ap.add_argument("--vorher-aufbewahrung", type=int, default=30,
                    metavar="TAGE",
                    help="nach erfolgreichem Restore aeltere Sicherungskopien "
                         "(<db>__vorher_<stamp> und <live>.vorher-<stamp>) "
                         "loeschen; der juengste Stand bleibt immer erhalten. "
                         "0 = nie loeschen (Standard: 30 Tage)")
    ap.add_argument("--notfall-unvollstaendig-akzeptieren", action="store_true",
                    help="ein als UNVOLLSTAENDIG markiertes Backup trotzdem "
                         "einspielen (nur im Notfall)")
    ap.add_argument("--notfall-inkonsistent-akzeptieren", action="store_true",
                    help="ein als INKONSISTENT markiertes Backup (Snapshot "
                         "gescheitert, Collections nacheinander gelesen) trotzdem "
                         "einspielen (nur im Notfall)")
    ap.add_argument("--alt-backup-ohne-indexdaten", action="store_true",
                    help="Alt-Backup ohne (lesbare) metadata.json einspielen; fuer "
                         "diese Collections werden dann KEINE Indexe angelegt oder "
                         "geprueft (Standard: Abbruch)")
    ap.add_argument("--ohne-s3", action="store_true",
                    help="S3-Objekte im Backup bewusst NICHT zurueckspielen")
    ap.add_argument("--nur-datenbank", action="store_true",
                    help="nur die Datenbank; Datei-Speicher (uploads, "
                         "local_storage, S3) unangetastet lassen")
    # Nachpruefung 20.09.2026, Nr. 75: Das war ein SCHALTER und stand in
    # keinem dokumentierten Befehl. Ohne ihn blieben Collections, die es nur
    # live gibt (weil sie nach dem Backup entstanden sind), unveraendert
    # stehen — neben dem alten Stand aus dem Backup. Genau der Mischstand,
    # den Doku und Dateikopf ausdruecklich ausschliessen ("nie gemischt").
    # Jetzt ist exakt der STANDARD; wer den Mischstand wirklich will, sagt
    # es ausdruecklich.
    ap.add_argument("--exakt", action="store_true",
                    help="(Standard, nur noch aus Gewohnheit erlaubt) Collections, "
                         "die im Backup fehlen, in die Vorher-Datenbank verschieben")
    ap.add_argument("--zusaetzliche-behalten", action="store_true",
                    help="Collections, die es nur live gibt, STEHEN LASSEN. Achtung: "
                         "dann ist der Stand gemischt — alte Daten aus dem Backup "
                         "neben neueren Collections. Nur mit gutem Grund.")
    args = ap.parse_args(argv)
    return wiederherstellen(args)


if __name__ == "__main__":
    sys.exit(main())
