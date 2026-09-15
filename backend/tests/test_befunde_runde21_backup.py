# -*- coding: utf-8 -*-
"""Runde 21 (Pruefbefund Backup).

A  Scheitert der Datenbank-Snapshot, fiel das Backup auf nacheinander
   gelesene Collections zurueck und zaehlte trotzdem als vollstaendiges,
   gutes Backup (Status, Nachholung, Rotation, Restore). Jetzt: Snapshot
   wird wiederholt; bleibt es beim Rueckfall, ist das Backup INKONSISTENT
   (Exit 3, Alarm backup_inkonsistent), zaehlt nie als letzter guter Stand,
   verdraengt in der Rotation kein gutes Backup und wird beim Restore nur
   mit --notfall-inkonsistent-akzeptieren eingespielt.
B  Fehler beim Lesen/Schreiben der Index-Metadaten wurden verschluckt; beim
   Restore hiess eine fehlende Datei "keine Indexe erwartet", und die
   Pruefung verglich nur Namen. Jetzt: metadata.json ist Pflicht (Backup
   Exit 1, Restore Abbruch; Alt-Backups nur mit --alt-backup-ohne-
   indexdaten), und unique/expireAfterSeconds/partialFilterExpression
   werden verglichen.

Nur eigene Datenbanken autoschnell_r21_<suffix>_* (am Ende entfernt) und
Attrappen; die Entwicklungsdatenbank "autoschnell" wird nicht beruehrt.
Braucht nur Mongo (kein Backend).
"""
import asyncio
import contextlib
import hashlib
import json
import os
import shutil
import sys
import types
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
SCRIPTS = BACKEND / "scripts"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(SCRIPTS))
MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
SUF = uuid.uuid4().hex[:8]
PRAEFIX = f"autoschnell_r21_{SUF}"
assert PRAEFIX != "autoschnell"

RUECKFALL = "best-effort (snapshot fehlgeschlagen)"


# ------------------------------------------------------------------ Hilfen
@pytest.fixture(scope="module")
def mongo():
    from pymongo import MongoClient
    c = MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    yield c
    for name in c.list_database_names():
        if name.startswith(PRAEFIX):
            c.drop_database(name)
    assert not [n for n in c.list_database_names() if n.startswith(PRAEFIX)]


@pytest.fixture
def umgebung(monkeypatch, tmp_path):
    # deps (ueber backup_service) laedt .env — deshalb ZUERST importieren,
    # danach die Umgebung fuer die Tests festlegen.
    if not os.environ.get("MONGO_URL"):
        monkeypatch.setenv("MONGO_URL", MONGO_URL)
    if not os.environ.get("DB_NAME"):
        monkeypatch.setenv("DB_NAME", f"{PRAEFIX}_deps")
    import backup_service  # noqa: F401
    import backup_mongo as bm
    up, ls = tmp_path / "uploads", tmp_path / "local_storage"
    up.mkdir()
    ls.mkdir()
    (up / "a.txt").write_text("a", encoding="utf-8")
    monkeypatch.setattr(bm, "UPLOADS_DIR", up)
    monkeypatch.setattr(bm, "LOCAL_STORAGE_DIR", ls)
    for var in ("EMERGENT_LLM_KEY", "S3_BUCKET", "S3_ENDPOINT", "BACKUP_S3_BUCKET",
                "BACKUP_S3_ENDPOINT"):
        monkeypatch.setenv(var, "")
    monkeypatch.setenv("BACKUP_SNAPSHOT_PAUSE_S", "0")
    monkeypatch.delenv("BACKUP_SNAPSHOT_PFLICHT", raising=False)
    monkeypatch.delenv("BACKUP_SNAPSHOT_VERSUCHE", raising=False)
    return {"bm": bm, "tmp": tmp_path}


def _quelle(c, kurz: str) -> str:
    """Quell-Datenbank mit Unique-, TTL- und Partial-Unique-Index."""
    name = f"{PRAEFIX}_{kurz}"
    q = c[name]
    q.users.insert_many([{"id": f"u{i}", "email": f"u{i}@x.de"} for i in range(3)])
    q.users.create_index("email", unique=True, name="email_1")
    q.sessions.insert_one({"id": "s1",
                           "expires_at": datetime.now(timezone.utc) + timedelta(days=2)})
    q.sessions.create_index("expires_at", expireAfterSeconds=0, name="ttl_expires")
    q.appointments.insert_one({"id": "a1", "dealer_id": "d", "contract_id": "k"})
    q.appointments.create_index([("dealer_id", 1), ("contract_id", 1)], unique=True,
                                name="ein_termin_je_vertrag",
                                partialFilterExpression={"contract_id": {"$gt": ""}})
    q.contracts.insert_one({"id": "k1"})
    q.termine.insert_one({"id": "t1", "contract_id": "k1"})
    return name


def _ordner(base: Path) -> list:
    if not base.is_dir():
        return []
    return sorted(p for p in base.iterdir() if p.is_dir() and p.name.startswith("autoschnell-"))


def _manifest(p: Path) -> dict:
    return json.loads((p / "manifest.json").read_text(encoding="utf-8"))


def _snapshot_scheitert(monkeypatch, bm, versuche=None):
    """Snapshot-Zweig erzwingen und JEDES Snapshot-Lesen scheitern lassen
    (wie SnapshotTooOld). Deterministisch auf Einzelserver und Replica Set."""
    from pymongo.errors import OperationFailure
    monkeypatch.setattr(bm, "ist_replica_set", lambda client: True)
    orig = bm.dump_collection

    def dump(coll, out_dir, session=None):
        if session is not None:
            if versuche is not None:
                versuche.append(coll.name)
            raise OperationFailure("simuliert: SnapshotTooOld", code=239)
        return orig(coll, out_dir, session=session)
    monkeypatch.setattr(bm, "dump_collection", dump)


class _SnapClient:
    """Echter Client, dessen Snapshot-Sitzung None liefert: so laeuft auch
    auf dem lokalen Einzelserver der Snapshot-Zweig mit echten Lesezugriffen."""

    def __init__(self, echt):
        self.echt = echt
        self.sitzungen = 0

    def __getitem__(self, name):
        return self.echt[name]

    @property
    def admin(self):
        return self.echt.admin

    @contextlib.contextmanager
    def start_session(self, snapshot=False):
        assert snapshot is True
        self.sitzungen += 1
        yield None


class _Flags:
    def __init__(self, docs=None):
        self.calls = []
        self.docs = dict(docs or {})

    async def update_one(self, filt, upd, upsert=False):
        self.calls.append((filt, upd))
        return types.SimpleNamespace(upserted_id="x")

    async def find_one(self, filt, projektion=None):
        doc = self.docs.get(filt.get("_id"))
        return dict(doc) if doc else None


class _FakeDb:
    def __init__(self, flags=None):
        self.betriebsalarme = _Flags()
        self.system_flags = _Flags(flags)


def _synth(base: Path, name: str, alter_h: float, **felder) -> Path:
    p = base / name
    (p / "db").mkdir(parents=True)
    m = {"version": 4, "db": "db", "konsistenz": "snapshot", "inkonsistent": "",
         "created_at": (datetime.now(timezone.utc) - timedelta(hours=alter_h)).isoformat(),
         "collections": {}, "files": {}, "unvollstaendig": []}
    for k, v in felder.items():
        if v is None:
            m.pop(k, None)
        else:
            m[k] = v
    (p / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    return p


def _restore(argv, capsys):
    import restore_mongo
    rc = restore_mongo.main(argv)
    return rc, capsys.readouterr().out


# =================================================================== A
def test_a1_rueckfall_nach_snapshotfehler_ist_inkonsistent(umgebung, mongo, monkeypatch):
    bm = umgebung["bm"]
    q = _quelle(mongo, "a1")
    versuche = []
    _snapshot_scheitert(monkeypatch, bm, versuche)
    gescheitert = bm.dump_collection

    def mit_gleichzeitigem_schreiben(coll, out_dir, session=None):
        n = gescheitert(coll, out_dir, session=session)
        # Waehrend des Rueckfalls legt die App Vertrag + Termin an.
        if coll.name == "contracts" and session is None:
            mongo[q].contracts.insert_one({"id": "k2"})
            mongo[q].termine.insert_one({"id": "t2", "contract_id": "k2"})
        return n
    monkeypatch.setattr(bm, "dump_collection", mit_gleichzeitigem_schreiben)

    base = umgebung["tmp"] / "a1"
    rc = bm.backup_erstellen(base, db_name=q, mongo_url=MONGO_URL)
    log = (base / "backup.log").read_text(encoding="utf-8")
    assert rc == 3, log[-1000:]
    [ordner] = _ordner(base)
    m = _manifest(ordner)
    assert m["version"] >= 4
    assert m["konsistenz"] == RUECKFALL
    assert "Snapshot nach 3 Versuch" in m["inkonsistent"], m
    assert m["unvollstaendig"] == []
    assert len(versuche) == 3, versuche           # erneut versucht, nicht sofort Rueckfall
    assert "BACKUP INKONSISTENT" in log and "BACKUP OK" not in log
    assert ordner.name in log.strip().splitlines()[-1]
    from backup_bewertung import ist_gut
    assert ist_gut(m) is False


def test_a2_snapshot_wird_wiederholt_und_teilreste_entfernt(umgebung, mongo, monkeypatch):
    from pymongo import MongoClient
    from pymongo.errors import OperationFailure
    bm = umgebung["bm"]
    q = _quelle(mongo, "a2")
    monkeypatch.setattr(bm, "ist_replica_set", lambda client: True)
    client = _SnapClient(MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000))
    orig = bm.dump_collection

    def erster_versuch_scheitert(coll, out_dir, session=None):
        if client.sitzungen == 1 and coll.name == "sessions":
            raise OperationFailure("simuliert: SnapshotTooOld (1. Versuch)", code=239)
        return orig(coll, out_dir, session=session)
    monkeypatch.setattr(bm, "dump_collection", erster_versuch_scheitert)

    # Direkter Aufruf: Reste eines abgebrochenen Versuchs duerfen nicht bleiben
    target = umgebung["tmp"] / "a2_target"
    target.mkdir()
    (target / "geist.bson.gz").write_bytes(b"alt")
    (target / "geist.metadata.json").write_text("{", encoding="utf-8")
    logfile = umgebung["tmp"] / "a2.log"
    names = sorted(mongo[q].list_collection_names())
    counts, konsistenz, inkonsistent = bm.dump_datenbank(
        client, mongo[q], names, target, logfile)
    assert konsistenz == "snapshot" and inkonsistent == ""
    assert client.sitzungen == 2
    assert counts == {"appointments": 1, "contracts": 1, "sessions": 1, "termine": 1, "users": 3}
    assert not (target / "geist.bson.gz").exists() and not (target / "geist.metadata.json").exists()
    assert {p.name for p in target.iterdir()} == \
        {f"{n}.bson.gz" for n in names} | {f"{n}.metadata.json" for n in names}
    assert "Versuch 1/3" in logfile.read_text(encoding="utf-8")

    # Ende-zu-Ende: Exit 0, Manifest "snapshot"
    client2 = _SnapClient(MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000))
    client = client2
    monkeypatch.setattr(bm, "MongoClient", lambda *a, **kw: client2)
    base = umgebung["tmp"] / "a2"
    rc = bm.backup_erstellen(base, db_name=q, mongo_url=MONGO_URL)
    assert rc == 0, (base / "backup.log").read_text(encoding="utf-8")[-800:]
    m = _manifest(_ordner(base)[0])
    assert m["konsistenz"] == "snapshot" and m["inkonsistent"] == ""
    assert client2.sitzungen == 2


def _bs(monkeypatch):
    if not os.environ.get("MONGO_URL"):
        monkeypatch.setenv("MONGO_URL", MONGO_URL)
    if not os.environ.get("DB_NAME"):
        monkeypatch.setenv("DB_NAME", f"{PRAEFIX}_deps")
    import backup_service
    return backup_service


def test_a3_backup_stand_zaehlt_inkonsistentes_backup_nicht(tmp_path, monkeypatch):
    bs = _bs(monkeypatch)
    monkeypatch.setattr(bs, "BACKUP_DIR", tmp_path)
    _synth(tmp_path, "autoschnell-2026-09-01_0300", 30)
    neu = _synth(tmp_path, "autoschnell-2026-09-02_0300", 2, konsistenz=RUECKFALL,
                 inkonsistent="Snapshot nach 3 Versuch(en) fehlgeschlagen (x)")
    # Nachholung/Alter: das junge inkonsistente zaehlt nicht
    assert 29.5 < bs._last_backup_age_hours() < 30.5
    info = bs.letztes_backup_info()
    assert info["pfad"] == str(neu)
    assert info["vollstaendig"] is False and info["konsistent"] is False
    assert info["konsistenz"] == RUECKFALL and "Snapshot nach 3" in info["inkonsistent"]
    assert info["hinweis"].startswith("INKONSISTENT"), info["hinweis"]
    assert "autoschnell-2026-09-01_0300" in info["hinweis"]
    # stand_speichern: nur der Versuch, NICHT der letzte gute Stand
    db = _FakeDb()
    asyncio.run(bs.stand_speichern(db))
    ids = [f["_id"] for f, _ in db.system_flags.calls]
    assert ids == [bs._STAND_ID], ids
    gesetzt = db.system_flags.calls[0][1]["$set"]
    assert gesetzt["vollstaendig"] is False and gesetzt["konsistent"] is False

    # Alt-Manifest (Version 3) ohne Feld "inkonsistent": der Text reicht
    shutil.rmtree(neu)
    alt = _synth(tmp_path, "autoschnell-2026-09-02_0300", 2, version=3,
                 konsistenz=RUECKFALL, inkonsistent=None)
    info = bs.letztes_backup_info()
    assert info["vollstaendig"] is False and "INKONSISTENT" in info["hinweis"]
    assert 29.5 < bs._last_backup_age_hours() < 30.5

    # Gegenprobe: Einzelserver-Lauf bleibt zugelassen (aber nicht stichtagsgenau)
    shutil.rmtree(alt)
    _synth(tmp_path, "autoschnell-2026-09-02_0300", 2, konsistenz="best-effort (standalone)")
    info = bs.letztes_backup_info()
    assert info["vollstaendig"] is True and info["konsistent"] is True
    assert info["stichtagsgenau"] is False and info["hinweis"] == "ok"
    db = _FakeDb()
    asyncio.run(bs.stand_speichern(db))
    assert [f["_id"] for f, _ in db.system_flags.calls] == [bs._STAND_ID, bs._STAND_VOLL_ID]


def test_a4_run_backup_alarmiert_bei_inkonsistenz(tmp_path, monkeypatch):
    bs = _bs(monkeypatch)
    monkeypatch.setattr(bs, "BACKUP_DIR", tmp_path)

    def _skript(code, zeile, name):
        p = tmp_path / f"fake_{name}.py"
        p.write_text(f"import sys\nprint('zeile 1')\nprint({zeile!r})\nsys.exit({code})\n",
                     encoding="utf-8")
        return p

    monkeypatch.setattr(bs, "_SCRIPT", _skript(
        3, "BACKUP INKONSISTENT: 3 Collections -> autoschnell-2026-09-03_0300; "
           "Grund: Snapshot nach 3 Versuch(en) fehlgeschlagen", "i"))
    db = _FakeDb()
    asyncio.run(bs._run_backup(db))
    typen = [f["typ"] for f, _ in db.betriebsalarme.calls]
    assert typen == ["backup_inkonsistent"], typen
    assert db.betriebsalarme.calls[0][0]["ref"] == "autoschnell-2026-09-03_0300"

    # Inkonsistent UND unvollstaendig: beide Alarme
    monkeypatch.setattr(bs, "_SCRIPT", _skript(
        3, "BACKUP INKONSISTENT: -> autoschnell-2026-09-03_0301; Grund: x; zudem "
           "UNVOLLSTAENDIG, NICHT gesichert: offsite: kaputt", "iu"))
    db = _FakeDb()
    asyncio.run(bs._run_backup(db))
    assert sorted(f["typ"] for f, _ in db.betriebsalarme.calls) == \
        ["backup_inkonsistent", "backup_unvollstaendig"]

    # Exit 0, aber das Manifest des Ordners ist inkonsistent: trotzdem Alarm
    _synth(tmp_path, "autoschnell-2026-09-03_0302", 0, konsistenz=RUECKFALL,
           inkonsistent="Snapshot nach 3 Versuch(en) fehlgeschlagen (y)")
    monkeypatch.setattr(bs, "_SCRIPT", _skript(
        0, "BACKUP OK: alles -> autoschnell-2026-09-03_0302", "ok"))
    db = _FakeDb()
    asyncio.run(bs._run_backup(db))
    assert [f["typ"] for f, _ in db.betriebsalarme.calls] == ["backup_inkonsistent"]

    # Gutes Manifest, Exit 0: kein Alarm
    _synth(tmp_path, "autoschnell-2026-09-03_0303", 0)
    monkeypatch.setattr(bs, "_SCRIPT", _skript(
        0, "BACKUP OK: alles -> autoschnell-2026-09-03_0303", "ok2"))
    db = _FakeDb()
    asyncio.run(bs._run_backup(db))
    assert db.betriebsalarme.calls == []


def test_a5_rotation_behaelt_das_letzte_gute_backup(umgebung):
    bm = umgebung["bm"]
    base = umgebung["tmp"] / "rot"
    base.mkdir()
    gut = _synth(base, "autoschnell-2026-08-01_0300", 24 * 40)
    schlecht = [_synth(base, f"autoschnell-2026-08-{t:02d}_0300", 24, konsistenz=RUECKFALL,
                       inkonsistent="Snapshot fehlgeschlagen") for t in range(2, 17)]
    logfile = base / "backup.log"
    bm.rotate(base, logfile)
    rest = _ordner(base)
    assert gut.exists(), "letztes gutes Backup wurde verdraengt"
    assert rest == [gut] + schlecht[-bm.KEEP:]
    assert not schlecht[0].exists()
    assert "juengstes gutes Backup" in logfile.read_text(encoding="utf-8")


class _FakeS3:
    def __init__(self, vorhandene=()):
        self.uploads, self.geloescht, self.groessen = [], [], {}
        self.vorhandene = list(vorhandene)

    def upload_file(self, Filename, Bucket, Key, ExtraArgs=None):
        self.uploads.append(Key)
        self.groessen[Key] = os.path.getsize(Filename)

    def head_object(self, Bucket, Key):
        return {"ContentLength": self.groessen[Key]}

    def get_paginator(self, name):
        fake = self

        class P:
            def paginate(self, Bucket, Prefix=""):
                keys = [k for k in fake.vorhandene + fake.uploads if k.startswith(Prefix)]
                return [{"Contents": [{"Key": k} for k in keys]}]
        return P()

    def delete_object(self, Bucket, Key):
        self.geloescht.append(Key)


def test_a6_offsite_rotation_nicht_nach_inkonsistentem_lauf(umgebung, mongo, monkeypatch):
    bm = umgebung["bm"]
    q = _quelle(mongo, "a6")
    monkeypatch.setenv("BACKUP_S3_BUCKET", "offsite-test")
    monkeypatch.setenv("BACKUP_S3_OBJECT_LOCK_DAYS", "")
    monkeypatch.setenv("BACKUP_S3_KEEP", "2")
    s3 = _FakeS3(vorhandene=[f"autoschnell-backups/autoschnell-2026-08-{t:02d}_0300.tar.gz"
                             for t in range(1, 5)])
    monkeypatch.setattr(bm, "_backup_s3_client", lambda: s3)
    _snapshot_scheitert(monkeypatch, bm)
    base = umgebung["tmp"] / "a6"
    rc = bm.backup_erstellen(base, db_name=q, mongo_url=MONGO_URL)
    assert rc == 3
    m = _manifest(_ordner(base)[0])
    assert m.get("offsite") and m["inkonsistent"]
    assert len(s3.uploads) == 1                     # die Kopie wird trotzdem abgelegt
    assert s3.geloescht == [], "schlechter Lauf darf keine Offsite-Archive loeschen"
    assert "Offsite-Rotation ausgesetzt" in (base / "backup.log").read_text(encoding="utf-8")


def test_a7_restore_lehnt_inkonsistentes_backup_ab(umgebung, mongo, monkeypatch, capsys):
    bm = umgebung["bm"]
    q = _quelle(mongo, "a7")
    _snapshot_scheitert(monkeypatch, bm)
    base = umgebung["tmp"] / "a7"
    assert bm.backup_erstellen(base, db_name=q, mongo_url=MONGO_URL) == 3
    [ordner] = _ordner(base)
    ziel = f"{PRAEFIX}_a7_ziel"
    capsys.readouterr()

    rc, out = _restore([str(ordner), "--db", ziel, "--dry-run", "--nur-datenbank"], capsys)
    assert rc == 1 and "INKONSISTENT" in out and "NICHTS veraendert" in out, out[-800:]
    assert "DRY-RUN OK" not in out and "--notfall-inkonsistent-akzeptieren" in out
    rc, out = _restore([str(ordner), "--db", ziel, "--yes", "--nur-datenbank"], capsys)
    assert rc == 1 and "NICHTS veraendert" in out, out[-800:]
    assert ziel not in mongo.list_database_names()

    # Alt-Manifest ohne Feld "inkonsistent" (nur der konsistenz-Text): ebenso
    alt = umgebung["tmp"] / "a7_alt"
    shutil.copytree(ordner, alt)
    m = _manifest(alt)
    m.pop("inkonsistent")
    m["version"] = 3
    (alt / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    rc, out = _restore([str(alt), "--db", ziel, "--dry-run", "--nur-datenbank"], capsys)
    assert rc == 1 and "INKONSISTENT" in out, out[-800:]

    # Notfall-Schalter: eingespielt, mit lauter Warnung
    rc, out = _restore([str(ordner), "--db", ziel, "--yes", "--nur-datenbank",
                        "--notfall-inkonsistent-akzeptieren"], capsys)
    assert rc == 0 and "RESTORE OK" in out, out[-1500:]
    assert "WARNUNG" in out and "INKONSISTENT" in out
    assert mongo[ziel].users.count_documents({}) == 3


def test_a7b_dry_run_nennt_nur_stimmiges_backup_konsistent(umgebung, mongo, capsys):
    bm = umgebung["bm"]
    q = _quelle(mongo, "a7b")
    base = umgebung["tmp"] / "a7b"
    rc = bm.backup_erstellen(base, db_name=q, mongo_url=MONGO_URL)
    assert rc == 0
    [ordner] = _ordner(base)
    m = _manifest(ordner)
    rc, out = _restore([str(ordner), "--db", f"{PRAEFIX}_a7b_ziel", "--dry-run",
                        "--nur-datenbank"], capsys)
    assert rc == 0 and "DRY-RUN OK" in out
    if m["konsistenz"] == "best-effort (standalone)":
        assert "nicht stichtagsgenau" in out and "Backup ist konsistent" not in out
    else:
        assert "Backup ist konsistent" in out


def test_a8_datenbank_stand_mit_konsistent_false_gilt_nicht(tmp_path, monkeypatch):
    bs = _bs(monkeypatch)
    monkeypatch.setattr(bs, "BACKUP_DIR", tmp_path)      # lokal: nichts
    vor_1h = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    db = _FakeDb({
        bs._STAND_ID: {"erstellt": vor_1h, "vollstaendig": True, "konsistent": False,
                       "offsite": True, "hinweis": "ok", "server": "prod1-test"},
        bs._STAND_VOLL_ID: {"erstellt": vor_1h, "vollstaendig": True, "konsistent": False},
    })
    info = asyncio.run(bs.letztes_backup_info_global(db))
    assert info["vollstaendig"] is False and "INKONSISTENT" in info["hinweis"]
    assert "prod1-test" in info["quelle"]
    assert asyncio.run(bs.letztes_vollstaendiges_alter_global(db)) == float("inf")

    # Grund als Text bzw. Rueckfall-Konsistenz im Eintrag: ebenso
    db = _FakeDb({
        bs._STAND_ID: {"erstellt": vor_1h, "vollstaendig": True, "konsistenz": RUECKFALL},
        bs._STAND_VOLL_ID: {"erstellt": vor_1h, "vollstaendig": True,
                            "inkonsistent": "Snapshot fehlgeschlagen"},
    })
    info = asyncio.run(bs.letztes_backup_info_global(db))
    assert info["vollstaendig"] is False
    assert asyncio.run(bs.letztes_vollstaendiges_alter_global(db)) == float("inf")

    # Gegenprobe: guter Eintrag bleibt gut
    db = _FakeDb({
        bs._STAND_ID: {"erstellt": vor_1h, "vollstaendig": True, "konsistent": True,
                       "offsite": True, "hinweis": "ok"},
        bs._STAND_VOLL_ID: {"erstellt": vor_1h, "vollstaendig": True, "konsistent": True},
    })
    info = asyncio.run(bs.letztes_backup_info_global(db))
    assert info["vollstaendig"] is True and info["hinweis"] == "ok"
    assert 0.9 < asyncio.run(bs.letztes_vollstaendiges_alter_global(db)) < 1.2


def test_a8b_fehlendes_manifest_heisst_auf_dem_anderen_server_nicht_inkonsistent(
        tmp_path, monkeypatch):
    """Runde 21 (Gegenpruefung): ein Ordner ohne manifest.json ist kein
    gutes Backup, aber auch nicht "INKONSISTENT" — der Betreiber soll auf
    beiden Servern denselben, richtigen Grund lesen."""
    bs = _bs(monkeypatch)
    lokal = tmp_path / "server1"
    (lokal / "autoschnell-2026-09-10_0300").mkdir(parents=True)
    monkeypatch.setattr(bs, "BACKUP_DIR", lokal)
    info = bs.letztes_backup_info()
    assert info["vollstaendig"] is False and info["konsistent"] is None
    db = _FakeDb()
    asyncio.run(bs.stand_speichern(db))
    assert [f["_id"] for f, _ in db.system_flags.calls] == [bs._STAND_ID]
    eintrag = db.system_flags.calls[0][1]["$set"]
    # Der ANDERE Server (lokal keine Sicherung) liest den Datenbank-Eintrag
    anderer = tmp_path / "server2"
    anderer.mkdir()
    monkeypatch.setattr(bs, "BACKUP_DIR", anderer)
    info = asyncio.run(bs.letztes_backup_info_global(_FakeDb({bs._STAND_ID: eintrag})))
    assert info["vollstaendig"] is False
    assert "manifest.json fehlt" in info["hinweis"], info["hinweis"]
    assert "INKONSISTENT" not in info["hinweis"], info["hinweis"]


def test_a9_replica_set_laut_adresse_oder_unbekannt_verlangt_snapshot(umgebung, mongo, monkeypatch):
    from backup_bewertung import snapshot_pflicht
    bm = umgebung["bm"]
    assert snapshot_pflicht("mongodb://10.0.0.2,10.0.0.3/?replicaSet=rs0") is True
    assert snapshot_pflicht("mongodb://127.0.0.1:27017") is False
    monkeypatch.setenv("BACKUP_SNAPSHOT_PFLICHT", "true")
    assert snapshot_pflicht("mongodb://127.0.0.1:27017") is True
    monkeypatch.delenv("BACKUP_SNAPSHOT_PFLICHT")

    # hello/isMaster ohne Antwort: "unbekannt" statt still "Standalone"
    class _Stumm:
        class admin:
            @staticmethod
            def command(cmd):
                raise RuntimeError("keine Antwort")
    assert bm.ist_replica_set(_Stumm()) is None

    q = _quelle(mongo, "a9")
    names = sorted(mongo[q].list_collection_names())
    _snapshot_scheitert(monkeypatch, bm)
    # Erkennung meldet "kein Replica Set", die Adresse nennt aber eines
    monkeypatch.setattr(bm, "ist_replica_set", lambda client: False)
    t1 = umgebung["tmp"] / "a9_1"
    t1.mkdir()
    _, konsistenz, inkonsistent = bm.dump_datenbank(
        mongo, mongo[q], names, t1, umgebung["tmp"] / "a9.log", pflicht=True)
    assert konsistenz == RUECKFALL and inkonsistent
    # Ohne Pflicht bleibt der Einzelserver-Lauf zugelassen
    t2 = umgebung["tmp"] / "a9_2"
    t2.mkdir()
    _, konsistenz, inkonsistent = bm.dump_datenbank(
        mongo, mongo[q], names, t2, umgebung["tmp"] / "a9.log", pflicht=False)
    assert konsistenz == "best-effort (standalone)" and inkonsistent == ""
    # Erkennung unbekannt: Snapshot wird versucht, Rueckfall ist inkonsistent
    monkeypatch.setattr(bm, "ist_replica_set", lambda client: None)
    t3 = umgebung["tmp"] / "a9_3"
    t3.mkdir()
    _, konsistenz, inkonsistent = bm.dump_datenbank(
        mongo, mongo[q], names, t3, umgebung["tmp"] / "a9.log")
    assert konsistenz == RUECKFALL and inkonsistent


def test_a10_wartung_wartet_auf_den_middleware_cache(umgebung, mongo, monkeypatch):
    bm = umgebung["bm"]
    q = _quelle(mongo, "a10")
    monkeypatch.setattr(bm, "ist_replica_set", lambda client: False)
    ereignisse = []
    monkeypatch.setattr(bm, "time", types.SimpleNamespace(
        sleep=lambda s: ereignisse.append(("warten", s))))
    orig = bm.dump_collection

    def dump(coll, out_dir, session=None):
        flag = mongo[q].system_flags.find_one({"_id": "wartungsmodus"}) or {}
        ereignisse.append(("dump", coll.name, bool(flag.get("aktiv"))))
        return orig(coll, out_dir, session=session)
    monkeypatch.setattr(bm, "dump_collection", dump)
    base = umgebung["tmp"] / "a10"
    rc = bm.backup_erstellen(base, db_name=q, mongo_url=MONGO_URL, wartung=True)
    assert rc == 0, (base / "backup.log").read_text(encoding="utf-8")[-800:]
    assert ereignisse[0] == ("warten", bm.WARTUNG_WARTEN_S) and bm.WARTUNG_WARTEN_S > 5
    assert all(e[2] for e in ereignisse if e[0] == "dump"), ereignisse
    assert _manifest(_ordner(base)[0])["konsistenz"] == "stimmig (Schreibpause)"
    assert mongo[q].system_flags.find_one({"_id": "wartungsmodus"})["aktiv"] is False


# =================================================================== B
def test_b1_list_indexes_fehler_laesst_backup_scheitern(umgebung, mongo, monkeypatch):
    from pymongo.collection import Collection
    bm = umgebung["bm"]
    q = _quelle(mongo, "b1")
    orig = Collection.list_indexes
    aufrufe = []

    def kaputt(self, *a, **kw):
        if self.database.name == q and self.name == "users":
            aufrufe.append(1)
            raise RuntimeError("simuliert: listIndexes nicht erlaubt")
        return orig(self, *a, **kw)
    monkeypatch.setattr(Collection, "list_indexes", kaputt)
    base = umgebung["tmp"] / "b1"
    rc = bm.backup_erstellen(base, db_name=q, mongo_url=MONGO_URL)
    log = (base / "backup.log").read_text(encoding="utf-8")
    assert rc == 1, log[-800:]
    assert "Index-Metadaten" in log and "users" in log and "BACKUP OK" not in log
    assert len(aufrufe) == 2                       # eine Wiederholung
    assert _ordner(base) == []
    assert not [p for p in base.iterdir() if p.name.startswith(".tmp-")]


class _JsonHalb:
    """json-Ersatz fuer backup_mongo: schreibt die sessions-Metadaten nur halb."""

    def __init__(self, mit_fehler: bool):
        self.mit_fehler = mit_fehler
        self.loads, self.dumps = json.loads, json.dumps

    def dump(self, obj, fh, **kw):
        if isinstance(obj, dict) and obj.get("collectionName") == "sessions":
            fh.write('{"options": {}, "indexes": [{"v": 2, "key"')
            if self.mit_fehler:
                raise OSError("simuliert: Datentraeger voll")
            return None
        return json.dump(obj, fh, **kw)


@pytest.mark.parametrize("mit_fehler", [True, False], ids=["oserror", "still_abgeschnitten"])
def test_b2_halb_geschriebene_metadaten_lassen_backup_scheitern(umgebung, mongo, monkeypatch,
                                                                mit_fehler):
    bm = umgebung["bm"]
    q = _quelle(mongo, f"b2{int(mit_fehler)}")
    monkeypatch.setattr(bm, "json", _JsonHalb(mit_fehler))
    base = umgebung["tmp"] / f"b2{int(mit_fehler)}"
    rc = bm.backup_erstellen(base, db_name=q, mongo_url=MONGO_URL)
    log = (base / "backup.log").read_text(encoding="utf-8")
    assert rc == 1, log[-800:]
    assert "sessions" in log and "BACKUP OK" not in log
    assert _ordner(base) == []


def test_b3_indexfehler_im_snapshot_zweig_ohne_rueckfall(umgebung, mongo, monkeypatch):
    from pymongo import MongoClient
    from pymongo.collection import Collection
    bm = umgebung["bm"]
    q = _quelle(mongo, "b3")
    monkeypatch.setattr(bm, "ist_replica_set", lambda client: True)
    client = _SnapClient(MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000))
    monkeypatch.setattr(bm, "MongoClient", lambda *a, **kw: client)
    orig = Collection.list_indexes

    def kaputt(self, *a, **kw):
        if self.database.name == q and self.name == "users":
            raise RuntimeError("simuliert: listIndexes nicht erlaubt")
        return orig(self, *a, **kw)
    monkeypatch.setattr(Collection, "list_indexes", kaputt)
    base = umgebung["tmp"] / "b3"
    rc = bm.backup_erstellen(base, db_name=q, mongo_url=MONGO_URL)
    log = (base / "backup.log").read_text(encoding="utf-8")
    assert rc == 1, log[-800:]
    assert client.sitzungen == 1, "Indexfehler darf keinen neuen Snapshot-Versuch ausloesen"
    assert "Snapshot-Lesen fehlgeschlagen" not in log and "best-effort" not in log
    assert _ordner(base) == []


def _gutes_backup(umgebung, mongo, kurz):
    bm = umgebung["bm"]
    q = _quelle(mongo, kurz)
    base = umgebung["tmp"] / kurz
    assert bm.backup_erstellen(base, db_name=q, mongo_url=MONGO_URL) == 0
    [ordner] = _ordner(base)
    return q, ordner


def test_b4_fehlende_metadaten_lassen_restore_scheitern(umgebung, mongo, capsys):
    q, ordner = _gutes_backup(umgebung, mongo, "b4")
    m = _manifest(ordner)
    # Backup: jede Collection hat ihre metadata.json, Indexnamen im Manifest
    for name in m["collections"]:
        assert (ordner / q / f"{name}.metadata.json").is_file(), name
    assert "email_1" in m["indexe"]["users"] and "ttl_expires" in m["indexe"]["sessions"]

    # So sah ein Backup mit verschlucktem Indexfehler aus: Datei fehlt und
    # steht in keiner Pruefliste.
    kaputt = umgebung["tmp"] / "b4_ohne_meta"
    shutil.copytree(ordner, kaputt)
    (kaputt / q / "users.metadata.json").unlink()
    m.pop("indexe")
    m["files"].pop(f"{q}/users.metadata.json")
    (kaputt / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    ziel = f"{PRAEFIX}_b4_ziel"
    capsys.readouterr()
    rc, out = _restore([str(kaputt), "--db", ziel, "--yes", "--nur-datenbank"], capsys)
    assert rc == 1 and "NICHTS veraendert" in out, out[-1000:]
    assert "users" in out and "Index-Metadaten" in out and "--alt-backup-ohne-indexdaten" in out
    assert ziel not in mongo.list_database_names()
    rc, out = _restore([str(kaputt), "--db", ziel, "--dry-run", "--nur-datenbank"], capsys)
    assert rc == 1 and "DRY-RUN OK" not in out

    # Ausdruecklicher Schalter fuer Alt-Backups: eingespielt, laut gewarnt
    rc, out = _restore([str(kaputt), "--db", ziel, "--yes", "--nur-datenbank",
                        "--alt-backup-ohne-indexdaten"], capsys)
    assert rc == 0 and "RESTORE OK" in out, out[-1500:]
    assert "WARNUNG" in out and "users" in out
    assert sorted(mongo[ziel].users.index_information()) == ["_id_"]
    assert mongo[ziel].sessions.index_information()["ttl_expires"]["expireAfterSeconds"] == 0


def test_b5_unlesbare_metadaten_mit_passender_pruefsumme(umgebung, mongo, capsys):
    q, ordner = _gutes_backup(umgebung, mongo, "b5")
    kaputt = umgebung["tmp"] / "b5_unlesbar"
    shutil.copytree(ordner, kaputt)
    f = kaputt / q / "sessions.metadata.json"
    f.write_text('{"options": {}, "indexes": [{"v": 2, "key"', encoding="utf-8")
    m = _manifest(kaputt)
    m["files"][f"{q}/sessions.metadata.json"] = {
        "sha256": hashlib.sha256(f.read_bytes()).hexdigest(), "bytes": f.stat().st_size}
    (kaputt / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    ziel = f"{PRAEFIX}_b5_ziel"
    capsys.readouterr()
    rc, out = _restore([str(kaputt), "--db", ziel, "--yes", "--nur-datenbank"], capsys)
    assert rc == 1 and "NICHTS veraendert" in out, out[-1000:]
    assert "sessions" in out and "unlesbar" in out
    assert ziel not in mongo.list_database_names()


def test_b6_pruefung_vergleicht_index_eigenschaften(umgebung, mongo, tmp_path):
    import restore_mongo
    from bson import json_util
    q = _quelle(mongo, "b6")
    meta_dir = tmp_path / "meta"
    meta_dir.mkdir()
    for coll in ("users", "sessions", "appointments"):
        idx = [json.loads(json_util.dumps(i)) for i in mongo[q][coll].list_indexes()]
        (meta_dir / f"{coll}.metadata.json").write_text(
            json.dumps({"indexes": idx}), encoding="utf-8")

    # Gegenprobe ohne Fehlalarm: die Quelle selbst
    dumps_q = {"users": ([{}] * 3, meta_dir / "users.metadata.json"),
               "sessions": ([{}], meta_dir / "sessions.metadata.json"),
               "appointments": ([{}], meta_dir / "appointments.metadata.json")}
    assert restore_mongo.pruefe_datenbank(mongo[q], dumps_q, None) == []

    # Gleiche Namen, falsche Eigenschaften
    z = mongo[f"{PRAEFIX}_b6_ziel"]
    z.users.insert_many([{"email": "x@x.de"}, {"email": "x@x.de"}])
    z.users.create_index("email", name="email_1")                          # nicht unique
    z.sessions.insert_one({"expires_at": None})
    z.sessions.create_index("expires_at", name="ttl_expires")              # ohne TTL
    z.appointments.insert_one({"dealer_id": "d"})
    z.appointments.create_index([("dealer_id", 1), ("contract_id", 1)], unique=True,
                                name="ein_termin_je_vertrag",
                                partialFilterExpression={"contract_id": {"$exists": True}})
    dumps = {"users": ([{}, {}], meta_dir / "users.metadata.json"),
             "sessions": ([{}], meta_dir / "sessions.metadata.json"),
             "appointments": ([{}], meta_dir / "appointments.metadata.json")}
    probleme = restore_mongo.pruefe_datenbank(z, dumps, None)
    text = " | ".join(probleme)
    assert "users.email_1: unique soll True, ist fehlt" in text, probleme
    assert "sessions.ttl_expires: expireAfterSeconds soll 0, ist fehlt" in text, probleme
    assert "appointments.ein_termin_je_vertrag: partialFilterExpression" in text, probleme
    assert len(probleme) == 3, probleme

    # Fehlende Datei heisst NICHT "keine Indexe erwartet"
    with pytest.raises(Exception):
        restore_mongo.erwartete_indexe(meta_dir / "gibt_es_nicht.metadata.json")
    probleme = restore_mongo.pruefe_datenbank(
        z, {"users": ([{}, {}], meta_dir / "gibt_es_nicht.metadata.json")}, None)
    assert probleme and "Index-Metadaten nicht lesbar" in probleme[0]


def test_b7_rundlauf_behaelt_unique_ttl_und_partial(umgebung, mongo, capsys):
    q, ordner = _gutes_backup(umgebung, mongo, "b7")
    ziel = f"{PRAEFIX}_b7_ziel"
    capsys.readouterr()
    rc, out = _restore([str(ordner), "--db", ziel, "--yes", "--nur-datenbank"], capsys)
    assert rc == 0 and "RESTORE OK" in out, out[-1500:]
    info = mongo[ziel].users.index_information()
    assert info["email_1"].get("unique") is True
    assert mongo[ziel].sessions.index_information()["ttl_expires"]["expireAfterSeconds"] == 0
    termin = mongo[ziel].appointments.index_information()["ein_termin_je_vertrag"]
    assert termin.get("unique") is True
    assert termin["partialFilterExpression"] == {"contract_id": {"$gt": ""}}
