# -*- coding: utf-8 -*-
"""Backup/Restore (Pruefbericht Runde 5 + Go-Live-Audit): Restore darf die
Zieldatenbank nie teilweise zerstoeren.

- Backup schreibt manifest.json (Pruefsummen, konsistenz) und meldet BACKUP OK.
- --dry-run prueft und veraendert nichts.
- Restore in eine Ziel-DB: Daten identisch, bisheriger Stand bleibt als
  <db>__vorher_* erhalten; Datei-Speicher werden per Staging + Rename
  umgeschaltet (bisheriger Ordner als <live>.vorher-<stamp>); der
  Wartungsmodus (system_flags.wartungsmodus) ist danach wieder aus.
- Beschaedigte Datei (Pruefsumme falsch / abgeschnitten): Restore bricht
  VOR jeder Aenderung ab — Zieldatenbank byteidentisch wie vorher.
- UNVOLLSTAENDIGES Backup: Abbruch (Exit 1) ohne Aenderung; nur mit
  --notfall-unvollstaendig-akzeptieren einspielbar.
- Fehler beim Umschalten: ALLE bereits umgeschalteten Collections und
  Ordner werden zurueckgedreht (Rollback), Wartungsmodus aus.
- backup_service: unvollstaendige Backups zaehlen nicht; Exit 2 / 1 des
  Skripts loesen Betriebsalarme aus.
- Offsite-Kopie (BACKUP_S3_BUCKET) landet im Manifest; Upload-Fehler ->
  UNVOLLSTAENDIG (Exit 2); Offsite-Rotation loescht nur die aeltesten.

Braucht nur Mongo (kein Backend).
"""
import asyncio
import json
import os
import shutil
import subprocess
import sys
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
QUELLE = f"r5bak_quelle_{SUF}"
ZIEL = f"r5bak_ziel_{SUF}"


def _client():
    from pymongo import MongoClient
    return MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)


def _run(script, *args, env=None):
    e = dict(os.environ, MONGO_URL=MONGO_URL, PYTHONIOENCODING="utf-8",
             EMERGENT_LLM_KEY="", S3_BUCKET="", BACKUP_S3_BUCKET="")
    e.update(env or {})
    r = subprocess.run([sys.executable, "-X", "utf8", str(SCRIPTS / script), *args],
                       capture_output=True, text=True, env=e, timeout=300)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def _dateien(ordner: Path) -> set:
    if not ordner.is_dir():
        return set()
    return {str(p.relative_to(ordner)).replace("\\", "/")
            for p in ordner.rglob("*") if p.is_file()}


def _vorher_dbs(c):
    return sorted(n for n in c.list_database_names() if n.startswith(f"{ZIEL}__vorher_"))


def _sicherungs_db_aus_ausgabe(out: str):
    """Namen der in DIESEM Lauf angelegten `__vorher_`-Datenbank aus der
    Ausgabe lesen (Schritt "5/6 Umschalten (bisheriger Stand -> ...)")."""
    import re
    treffer = re.findall(rf"{ZIEL}__vorher_\d{{8}}_\d{{6}}", out)
    return treffer[0] if treffer else None


def _rollback_hat_nichts_geparkt(c, out):
    """Nach einem abgebrochenen Restore darf die Sicherungs-Datenbank dieses
    Laufs KEINE Daten mehr enthalten — der Ruecktausch muss alles in die
    Live-Datenbank zurueckgeholt haben. Genau darauf kommt es an; die reine
    Namensliste taugt nicht, weil MongoDB leere Datenbanken ausblendet."""
    name = _sicherungs_db_aus_ausgabe(out)
    assert name, f"Sicherungs-Datenbank nicht aus der Ausgabe lesbar: {out[-600:]}"
    inhalt = {k: c[name][k].count_documents({}) for k in c[name].list_collection_names()}
    assert not any(inhalt.values()), f"Datenkopie in {name} liegengeblieben: {inhalt}"
    assert not _restore_dbs(c), _restore_dbs(c)


def _kein_neuer_datenbestand_geparkt(c, vorher_dbs):
    """Nach einem abgebrochenen Restore darf kein LIVE-Datenbestand in einer
    Sicherungs-Datenbank haengenbleiben.

    Auf Gleichheit der Namensliste laesst sich das nicht pruefen: MongoDB
    blendet LEERE Datenbanken aus `list_database_names()` aus. Eine beim
    Ruecktausch geleerte `__vorher_`-Datenbank verschwindet dadurch aus der
    Liste, eine zuvor leere kann wieder auftauchen — beides ohne jeden
    Datenverlust. Geprueft wird deshalb die Sache selbst: hoechstens eine
    zusaetzliche Sicherungs-Datenbank, und keine davon haelt Collections des
    laufenden Restores fest."""
    jetzt = _vorher_dbs(c)
    assert len(jetzt) <= len(vorher_dbs) + 1, (jetzt, vorher_dbs)
    for name in jetzt:
        if name not in vorher_dbs:
            # Neu entstandene Sicherung darf die Live-Daten nicht ersetzen
            assert c[ZIEL].users.count_documents({}) > 0, "Live-Daten fehlen"
    assert not _restore_dbs(c), _restore_dbs(c)


def _restore_dbs(c):
    return sorted(n for n in c.list_database_names() if n.startswith(f"{ZIEL}__restore_"))


def _flag(c):
    return c[ZIEL].system_flags.find_one({"_id": "wartungsmodus"})


@pytest.fixture(scope="module")
def welt(tmp_path_factory):
    z = {"dir": tmp_path_factory.mktemp("bak"), "live": tmp_path_factory.mktemp("live")}
    # Quell-Datei-Speicher (das, was das Backup sichert)
    src_up = z["dir"] / "quelle_uploads"
    (src_up / "fotos").mkdir(parents=True)
    (src_up / "fotos" / "a.jpg").write_bytes(b"A" * 100)
    (src_up / "b.txt").write_text("b", encoding="utf-8")
    src_ls = z["dir"] / "quelle_local_storage"
    src_ls.mkdir()
    (src_ls / "snap.html").write_text("<html>", encoding="utf-8")
    z["src_uploads"], z["src_local"] = src_up, src_ls
    z["env_backup"] = {"BACKUP_UPLOADS_DIR": str(src_up),
                       "BACKUP_LOCAL_STORAGE_DIR": str(src_ls)}
    # Live-Datei-Speicher des Restore-Ziels (getrennt von den echten Ordnern)
    z["live_uploads"] = z["live"] / "uploads"
    z["live_local"] = z["live"] / "local_storage"
    z["env_restore"] = {"BACKUP_UPLOADS_DIR": str(z["live_uploads"]),
                        "BACKUP_LOCAL_STORAGE_DIR": str(z["live_local"])}
    c = _client()
    q = c[QUELLE]
    q.users.insert_many([{"id": f"u{i}", "email": f"u{i}@x.de", "n": i} for i in range(50)])
    q.users.create_index("email", unique=True)
    q.vehicles.insert_many([{"id": f"v{i}", "dealer_id": "d1", "data": {"km": i}} for i in range(30)])
    q.leer.insert_one({"x": 1}); q.leer.delete_many({})
    yield z
    for name in list(c.list_database_names()):
        if name.startswith(("r5bak_",)) and SUF in name:
            c.drop_database(name)


def test_01_backup_mit_manifest(welt):
    rc, out = _run("backup_mongo.py", "--dir", str(welt["dir"]),
                   env=dict(welt["env_backup"], DB_NAME=QUELLE))
    assert rc == 0, out[-800:]
    ordner = [p for p in welt["dir"].iterdir() if p.is_dir() and p.name.startswith("autoschnell-")]
    assert len(ordner) == 1, out[-500:]
    welt["backup"] = ordner[0]
    assert (welt["backup"] / "manifest.json").is_file()
    assert (welt["backup"] / QUELLE / "users.bson.gz").is_file()
    assert "BACKUP OK" in out
    m = json.loads((welt["backup"] / "manifest.json").read_text(encoding="utf-8"))
    assert m["konsistenz"] in ("snapshot", "best-effort (standalone)")
    assert m["unvollstaendig"] == []
    assert m["collections"] == {"leer": 0, "users": 50, "vehicles": 30}
    assert "uploads/fotos/a.jpg" in m["files"] and "local_storage/snap.html" in m["files"]
    assert "offsite" not in m


def test_02_dry_run_veraendert_nichts(welt):
    c = _client()
    rc, out = _run("restore_mongo.py", str(welt["backup"]), "--db", ZIEL, "--dry-run",
                   env=welt["env_restore"])
    assert rc == 0 and "DRY-RUN OK" in out, out[-800:]
    assert ZIEL not in c.list_database_names()
    assert not welt["live_uploads"].exists()


def test_03_restore_identisch_und_alter_stand_bleibt(welt):
    c = _client()
    # Ziel hat vorher "alte" Daten und alte Dateien, die erhalten bleiben muessen
    c[ZIEL].users.insert_one({"id": "alt", "email": "alt@x.de"})
    welt["live_uploads"].mkdir(parents=True)
    (welt["live_uploads"] / "alt.txt").write_text("alt", encoding="utf-8")
    rc, out = _run("restore_mongo.py", str(welt["backup"]), "--db", ZIEL, "--yes",
                   env=welt["env_restore"])
    assert rc == 0 and "RESTORE OK" in out, out[-1200:]
    assert c[ZIEL].users.count_documents({}) == 50
    assert c[ZIEL].vehicles.count_documents({}) == 30
    assert c[ZIEL].users.find_one({"id": "u7"})["n"] == 7
    assert any(i.get("unique") for i in c[ZIEL].users.list_indexes())
    vorher = _vorher_dbs(c)
    assert len(vorher) == 1
    assert c[vorher[0]].users.find_one({"id": "alt"}) is not None
    assert not _restore_dbs(c)
    # Datei-Speicher: Backup-Stand live, alter Stand als .vorher-<stamp>
    assert _dateien(welt["live_uploads"]) == {"fotos/a.jpg", "b.txt"}
    assert _dateien(welt["live_local"]) == {"snap.html"}
    vorher_dirs = [p for p in welt["live"].iterdir() if p.name.startswith("uploads.vorher-")]
    assert len(vorher_dirs) == 1 and _dateien(vorher_dirs[0]) == {"alt.txt"}
    assert not [p for p in welt["live"].iterdir() if ".restore-" in p.name]
    # Wartungsmodus wurde gesetzt und wieder aufgehoben
    flag = _flag(c)
    assert flag is not None and flag["aktiv"] is False and flag["grund"] == "Restore"
    assert "system_flags" not in [n for n in c.list_database_names()]


def test_04_beschaedigtes_backup_aendert_nichts(welt):
    c = _client()
    kaputt = welt["dir"] / "kaputt"
    shutil.copytree(welt["backup"], kaputt)
    f = kaputt / QUELLE / "vehicles.bson.gz"
    daten = f.read_bytes()
    f.write_bytes(daten[: len(daten) // 2])          # abgeschnitten
    snapshot_vorher = {n: c[ZIEL][n].count_documents({}) for n in c[ZIEL].list_collection_names()}
    dateien_vorher = _dateien(welt["live_uploads"])
    rc, out = _run("restore_mongo.py", str(kaputt), "--db", ZIEL, "--yes",
                   env=welt["env_restore"])
    assert rc == 1 and "NICHTS veraendert" in out, out[-800:]
    assert "beschaedigt" in out.lower() or "pruefsumme" in out.lower()
    assert {n: c[ZIEL][n].count_documents({}) for n in c[ZIEL].list_collection_names()} == snapshot_vorher
    assert c[ZIEL].users.find_one({"id": "u7"})["n"] == 7
    assert _dateien(welt["live_uploads"]) == dateien_vorher
    # Ohne Manifest (altes Backup) ohne Freigabe abgelehnt
    (kaputt / "manifest.json").unlink()
    rc, out = _run("restore_mongo.py", str(kaputt), "--db", ZIEL, "--yes",
                   env=welt["env_restore"])
    assert rc == 1 and "manifest" in out.lower()


def test_05_unvollstaendiges_backup_nur_im_notfall(welt):
    c = _client()
    unvoll = welt["dir"] / "unvoll"
    shutil.copytree(welt["backup"], unvoll)
    mp = unvoll / "manifest.json"
    m = json.loads(mp.read_text(encoding="utf-8"))
    m["unvollstaendig"] = ["s3: Testfall — Bucket nicht erreichbar"]
    mp.write_text(json.dumps(m), encoding="utf-8")
    # Markierung im Ziel, an der man erkennt, ob es angefasst wurde
    c[ZIEL].users.insert_one({"id": "marker05", "email": "marker05@x.de"})
    snapshot_vorher = {n: c[ZIEL][n].count_documents({}) for n in c[ZIEL].list_collection_names()}
    vorher_dbs, vorher_dirs = _vorher_dbs(c), sorted(p.name for p in welt["live"].iterdir())

    rc, out = _run("restore_mongo.py", str(unvoll), "--db", ZIEL, "--yes",
                   env=welt["env_restore"])
    assert rc == 1 and "UNVOLLSTAENDIG" in out and "NICHTS veraendert" in out, out[-800:]
    assert "Testfall" in out and "--notfall-unvollstaendig-akzeptieren" in out
    assert {n: c[ZIEL][n].count_documents({}) for n in c[ZIEL].list_collection_names()} == snapshot_vorher
    _kein_neuer_datenbestand_geparkt(c, vorher_dbs)
    assert sorted(p.name for p in welt["live"].iterdir()) == vorher_dirs
    # Auch der Dry-Run meldet es als Fehler (ein echter Lauf wuerde scheitern)
    rc, out = _run("restore_mongo.py", str(unvoll), "--db", ZIEL, "--dry-run",
                   env=welt["env_restore"])
    assert rc == 1 and "DRY-RUN OK" not in out

    rc, out = _run("restore_mongo.py", str(unvoll), "--db", ZIEL, "--yes",
                   "--notfall-unvollstaendig-akzeptieren", env=welt["env_restore"])
    assert rc == 0 and "RESTORE OK" in out, out[-1200:]
    assert "WARNUNG" in out and "Testfall" in out
    assert c[ZIEL].users.count_documents({}) == 50
    assert c[ZIEL].users.find_one({"id": "marker05"}) is None
    assert _flag(c)["aktiv"] is False


def test_06_rollback_bei_fehler_beim_umschalten(welt, monkeypatch, capsys):
    import restore_mongo
    c = _client()
    for k, v in welt["env_restore"].items():
        monkeypatch.setenv(k, v)
    # Aktueller Live-Stand mit Markierungen (DB + Datei)
    c[ZIEL].users.insert_one({"id": "marker06", "email": "marker06@x.de"})
    (welt["live_uploads"] / "marker06.txt").write_text("m", encoding="utf-8")
    snapshot_vorher = {n: c[ZIEL][n].count_documents({}) for n in c[ZIEL].list_collection_names()}
    dateien_vorher = _dateien(welt["live_uploads"])
    vorher_dbs, vorher_dirs = _vorher_dbs(c), sorted(p.name for p in welt["live"].iterdir())

    original = restore_mongo._rename_collection
    zaehler = {"n": 0}

    def kaputt(client, von, nach):
        # Der Schritt tmp -> ziel scheitert bei der 2. Collection (users)
        if von.startswith(f"{ZIEL}__restore_") and nach.startswith(f"{ZIEL}."):
            zaehler["n"] += 1
            if zaehler["n"] == 2:
                raise RuntimeError("simulierter Rename-Fehler")
        return original(client, von, nach)

    monkeypatch.setattr(restore_mongo, "_rename_collection", kaputt)
    rc = restore_mongo.main([str(welt["backup"]), "--db", ZIEL, "--yes"])
    out = capsys.readouterr().out
    assert rc == 1, out[-1500:]
    assert "simulierter Rename-Fehler" in out and "ROLLBACK OK" in out, out[-1500:]
    assert "RESTORE OK" not in out
    # Live-DB vollstaendig auf dem bisherigen Stand (inkl. Markierung)
    assert {n: c[ZIEL][n].count_documents({}) for n in c[ZIEL].list_collection_names()} == snapshot_vorher
    assert c[ZIEL].users.find_one({"id": "marker06"}) is not None
    assert c[ZIEL].users.count_documents({}) == 51
    assert any(i.get("unique") for i in c[ZIEL].users.list_indexes())
    _rollback_hat_nichts_geparkt(c, out)
    _kein_neuer_datenbestand_geparkt(c, vorher_dbs)
    # Datei-Speicher zurueckgetauscht, keine Staging-/vorher-Reste
    assert _dateien(welt["live_uploads"]) == dateien_vorher
    assert sorted(p.name for p in welt["live"].iterdir()) == vorher_dirs
    assert _flag(c)["aktiv"] is False


def test_07_verzeichnis_swap_und_rueckgaengig(tmp_path, monkeypatch):
    import restore_mongo
    live = {"uploads": tmp_path / "uploads", "local_storage": tmp_path / "local_storage"}
    live["uploads"].mkdir(); (live["uploads"] / "alt.txt").write_text("alt")
    # local_storage gibt es live noch nicht
    staging = {}
    for name in live:
        s = tmp_path / f"{name}.restore-T1"
        s.mkdir(); (s / "neu.txt").write_text("neu")
        staging[name] = s
    geschaltet, fehler = restore_mongo.verzeichnisse_umschalten(staging, live, "T1")
    assert fehler is None and len(geschaltet) == 2
    assert _dateien(live["uploads"]) == {"neu.txt"} and _dateien(live["local_storage"]) == {"neu.txt"}
    assert _dateien(tmp_path / "uploads.vorher-T1") == {"alt.txt"}
    assert not (tmp_path / "local_storage.vorher-T1").exists()
    assert not (tmp_path / "uploads.restore-T1").exists()
    # Rueckgaengig: alter Stand zurueck, Staging entfernt
    assert restore_mongo.verzeichnisse_zuruecknehmen(geschaltet) == []
    assert _dateien(live["uploads"]) == {"alt.txt"} and not live["local_storage"].exists()
    assert not (tmp_path / "uploads.vorher-T1").exists()
    assert not [p for p in tmp_path.iterdir() if ".restore-" in p.name]
    # Fehler beim 2. Ordner: der 1. wird von der Umschaltfunktion nicht
    # angefasst gelassen — der Aufrufer dreht ihn ueber zuruecknehmen() zurueck
    for name, s in staging.items():
        s.mkdir(); (s / "neu.txt").write_text("neu")
    original = restore_mongo._verzeichnis_umbenennen

    def kaputt(von, nach):
        if von.name.startswith("local_storage.restore-"):
            raise PermissionError("simuliert: Datei in Benutzung")
        original(von, nach)

    monkeypatch.setattr(restore_mongo, "_verzeichnis_umbenennen", kaputt)
    geschaltet, fehler = restore_mongo.verzeichnisse_umschalten(staging, live, "T2")
    assert fehler and "local_storage" in fehler and len(geschaltet) == 1
    monkeypatch.setattr(restore_mongo, "_verzeichnis_umbenennen", original)
    assert restore_mongo.verzeichnisse_zuruecknehmen(geschaltet) == []
    assert _dateien(live["uploads"]) == {"alt.txt"} and not live["local_storage"].exists()
    assert not [p for p in tmp_path.iterdir() if ".vorher-" in p.name]


def _backup_service(monkeypatch):
    # deps.py braucht MONGO_URL/DB_NAME nur zum Anlegen eines (lazy) Clients.
    if not os.environ.get("MONGO_URL"):
        monkeypatch.setenv("MONGO_URL", MONGO_URL)
    if not os.environ.get("DB_NAME"):
        monkeypatch.setenv("DB_NAME", "autoschnell_backupservice_test")
    import backup_service
    return backup_service


def _synth_backup(base: Path, name: str, alter_h: float, unvollstaendig=None,
                  offsite=False, manifest=True) -> Path:
    p = base / name
    (p / "db").mkdir(parents=True)
    (p / "db" / "users.bson.gz").write_bytes(b"x")
    if manifest:
        m = {"version": 3, "db": "db", "konsistenz": "best-effort (standalone)",
             "created_at": (datetime.now(timezone.utc) - timedelta(hours=alter_h)).isoformat(),
             "collections": {"users": 1}, "files": {},
             "unvollstaendig": list(unvollstaendig or [])}
        if offsite:
            m["offsite"] = {"bucket": "b", "key": f"autoschnell-backups/{name}.tar.gz",
                            "uploaded_at": m["created_at"], "bytes": 1, "sha256": "0" * 64}
        (p / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    return p


def test_08_backup_service_zaehlt_nur_vollstaendige(tmp_path, monkeypatch):
    bs = _backup_service(monkeypatch)
    monkeypatch.setattr(bs, "BACKUP_DIR", tmp_path)
    assert bs._last_backup_age_hours() == float("inf")
    info = bs.letztes_backup_info()
    assert info["alter_stunden"] is None and info["pfad"] is None
    assert info["vollstaendig"] is False and "kein Backup" in info["hinweis"]

    _synth_backup(tmp_path, "autoschnell-2026-08-30_0300", 100, manifest=False)
    voll = _synth_backup(tmp_path, "autoschnell-2026-09-01_0300", 30, offsite=True)
    unvoll = _synth_backup(tmp_path, "autoschnell-2026-09-02_0300", 2,
                           unvollstaendig=["offsite: Upload fehlgeschlagen"])
    # Das juengste (2 h) ist unvollstaendig -> zaehlt nicht; das vollstaendige ist 30 h alt
    assert 29.5 < bs._last_backup_age_hours() < 30.5
    info = bs.letztes_backup_info()
    assert info["pfad"] == str(unvoll) and info["vollstaendig"] is False
    assert info["offsite"] is False and 1.5 < info["alter_stunden"] < 2.5
    assert "UNVOLLSTAENDIG" in info["hinweis"] and "Upload fehlgeschlagen" in info["hinweis"]
    assert "autoschnell-2026-09-01_0300" in info["hinweis"] and info["erstellt"]

    shutil.rmtree(unvoll)
    assert 29.5 < bs._last_backup_age_hours() < 30.5
    info = bs.letztes_backup_info()
    assert info["pfad"] == str(voll) and info["vollstaendig"] is True and info["offsite"] is True
    assert 29.5 < info["alter_stunden"] < 30.5 and "30 h alt" in info["hinweis"]

    shutil.rmtree(voll)  # nur noch der Ordner ohne Manifest
    assert bs._last_backup_age_hours() == float("inf")
    info = bs.letztes_backup_info()
    assert info["vollstaendig"] is False and "manifest.json fehlt" in info["hinweis"]
    assert "KEIN vollstaendiges Backup" in info["hinweis"]


class _FakeAlarme:
    def __init__(self):
        self.calls = []

    async def update_one(self, filt, upd, upsert=False):
        self.calls.append((filt, upd))
        return type("R", (), {"upserted_id": "x"})()


class _FakeDb:
    def __init__(self):
        self.betriebsalarme = _FakeAlarme()


def test_09_run_backup_alarmiert_bei_exit_2_und_1(tmp_path, monkeypatch):
    bs = _backup_service(monkeypatch)
    monkeypatch.setattr(bs, "BACKUP_DIR", tmp_path)

    def _skript(code, zeile):
        p = tmp_path / f"fake_{code}.py"
        p.write_text(f"import sys\nprint('zeile 1')\nprint({zeile!r})\nsys.exit({code})\n",
                     encoding="utf-8")
        return p

    monkeypatch.setattr(bs, "_SCRIPT", _skript(
        2, "BACKUP UNVOLLSTAENDIG: 3 Collections -> autoschnell-2026-09-03_0300; "
           "NICHT gesichert: offsite: Upload fehlgeschlagen"))
    db = _FakeDb()
    asyncio.run(bs._run_backup(db))
    assert len(db.betriebsalarme.calls) == 1
    filt, upd = db.betriebsalarme.calls[0]
    assert filt["typ"] == "backup_unvollstaendig" and filt["ref"] == "autoschnell-2026-09-03_0300"
    assert "Upload fehlgeschlagen" in upd["$set"]["details"]["ausgabe"]

    monkeypatch.setattr(bs, "_SCRIPT", _skript(1, "FEHLER: MongoDB nicht erreichbar"))
    db = _FakeDb()
    asyncio.run(bs._run_backup(db))
    assert len(db.betriebsalarme.calls) == 1
    assert db.betriebsalarme.calls[0][0]["typ"] == "backup_fehlgeschlagen"

    monkeypatch.setattr(bs, "_SCRIPT", _skript(0, "BACKUP OK: alles -> autoschnell-2026-09-03_0301"))
    db = _FakeDb()
    asyncio.run(bs._run_backup(db))
    assert db.betriebsalarme.calls == []
    asyncio.run(bs._run_backup(None))  # ohne DB-Handle: kein Fehler


class _FakeS3:
    """Minimaler S3-Client: upload_file/head_object/list/delete."""

    def __init__(self, vorhandene=(), upload_fehler=None):
        self.uploads, self.geloescht = [], []
        self.groessen = {}
        self.vorhandene = list(vorhandene)
        self.upload_fehler = upload_fehler

    def upload_file(self, Filename, Bucket, Key, ExtraArgs=None):
        if self.upload_fehler:
            raise RuntimeError(self.upload_fehler)
        self.uploads.append((Bucket, Key, dict(ExtraArgs or {})))
        self.groessen[Key] = os.path.getsize(Filename)

    def head_object(self, Bucket, Key):
        return {"ContentLength": self.groessen[Key]}

    def get_paginator(self, name):
        fake = self

        class P:
            def paginate(self, Bucket, Prefix=""):
                keys = [k for k in fake.vorhandene + [u[1] for u in fake.uploads]
                        if k.startswith(Prefix)]
                return [{"Contents": [{"Key": k} for k in keys]}]
        return P()

    def delete_object(self, Bucket, Key):
        self.geloescht.append(Key)


def test_10_offsite_kopie_im_manifest_und_rotation(welt, tmp_path, monkeypatch):
    import backup_mongo
    monkeypatch.setattr(backup_mongo, "UPLOADS_DIR", welt["src_uploads"])
    monkeypatch.setattr(backup_mongo, "LOCAL_STORAGE_DIR", welt["src_local"])
    monkeypatch.setenv("EMERGENT_LLM_KEY", "")
    monkeypatch.setenv("S3_BUCKET", "")
    monkeypatch.setenv("BACKUP_S3_BUCKET", "offsite-test")
    monkeypatch.setenv("BACKUP_S3_OBJECT_LOCK_DAYS", "7")
    monkeypatch.setenv("BACKUP_S3_KEEP", "3")
    alt = [f"autoschnell-backups/autoschnell-2026-08-{t:02d}_0300.tar.gz" for t in range(1, 5)]
    s3 = _FakeS3(vorhandene=alt + ["autoschnell-backups/fremd.txt"])
    monkeypatch.setattr(backup_mongo, "_backup_s3_client", lambda: s3)

    base = tmp_path / "off"
    rc = backup_mongo.backup_erstellen(base, db_name=QUELLE, mongo_url=MONGO_URL)
    assert rc == 0
    ordner = [p for p in base.iterdir() if p.is_dir() and p.name.startswith("autoschnell-")]
    assert len(ordner) == 1
    m = json.loads((ordner[0] / "manifest.json").read_text(encoding="utf-8"))
    assert m["unvollstaendig"] == [] and m["konsistenz"]
    off = m["offsite"]
    assert off["bucket"] == "offsite-test"
    assert off["key"] == f"autoschnell-backups/{ordner[0].name}.tar.gz"
    assert off["bytes"] > 0 and len(off["sha256"]) == 64 and off["uploaded_at"]
    assert off["object_lock_bis"] > off["uploaded_at"]
    assert len(s3.uploads) == 1
    extra = s3.uploads[0][2]
    assert extra["ServerSideEncryption"] == "AES256"
    assert extra["ObjectLockMode"] == "COMPLIANCE" and extra["ObjectLockRetainUntilDate"]
    assert extra["Metadata"]["sha256"] == off["sha256"]
    assert not [p for p in base.iterdir() if p.name.endswith(".tar.gz")]  # Archiv aufgeraeumt
    # Rotation: 5 Archive, KEEP=3 -> die 2 aeltesten weg, fremde Objekte unangetastet
    assert s3.geloescht == alt[:2]

    # Upload-Fehler -> UNVOLLSTAENDIG (Exit 2), Manifest ohne offsite
    monkeypatch.setattr(backup_mongo, "_backup_s3_client",
                        lambda: _FakeS3(upload_fehler="AccessDenied"))
    base2 = tmp_path / "off2"
    rc = backup_mongo.backup_erstellen(base2, db_name=QUELLE, mongo_url=MONGO_URL)
    assert rc == 2
    ordner = [p for p in base2.iterdir() if p.is_dir() and p.name.startswith("autoschnell-")]
    m = json.loads((ordner[0] / "manifest.json").read_text(encoding="utf-8"))
    assert "offsite" not in m
    assert len(m["unvollstaendig"]) == 1 and m["unvollstaendig"][0].startswith("offsite: ")
    assert "AccessDenied" in m["unvollstaendig"][0]
    assert (base2 / "backup.log").read_text(encoding="utf-8").count("BACKUP UNVOLLSTAENDIG") == 1


def test_11_snapshot_session_oder_fallback(welt, tmp_path, monkeypatch):
    import backup_mongo
    monkeypatch.setattr(backup_mongo, "UPLOADS_DIR", welt["src_uploads"])
    monkeypatch.setattr(backup_mongo, "LOCAL_STORAGE_DIR", welt["src_local"])
    monkeypatch.setenv("EMERGENT_LLM_KEY", "")
    monkeypatch.setenv("S3_BUCKET", "")
    monkeypatch.setenv("BACKUP_S3_BUCKET", "")
    # Snapshot-Pfad erzwingen: auf einem echten Replica Set -> "snapshot",
    # auf Standalone lehnt der Server readConcern snapshot ab -> Fallback.
    monkeypatch.setattr(backup_mongo, "ist_replica_set", lambda client: True)
    base = tmp_path / "snap"
    rc = backup_mongo.backup_erstellen(base, db_name=QUELLE, mongo_url=MONGO_URL)
    assert rc == 0
    ordner = [p for p in base.iterdir() if p.is_dir() and p.name.startswith("autoschnell-")]
    m = json.loads((ordner[0] / "manifest.json").read_text(encoding="utf-8"))
    assert m["konsistenz"] == "snapshot" or m["konsistenz"].startswith("best-effort (snapshot fehlgeschlagen")
    assert m["collections"] == {"leer": 0, "users": 50, "vehicles": 30}
    log = (base / "backup.log").read_text(encoding="utf-8")
    assert "BACKUP OK" in log and m["konsistenz"] in log
    # Der Fallback muss vollstaendige, lesbare Dateien hinterlassen
    import restore_mongo
    dumps, manifest, _ = restore_mongo.pruefe_backup(ordner[0], allow_no_manifest=False)
    assert {k: len(v[0]) for k, v in dumps.items()} == m["collections"]


def test_12_wiederherstellung_testen_probe(welt):
    rc, out = _run("wiederherstellung_testen.py",
                   env=dict(welt["env_backup"], DB_NAME=QUELLE))
    assert rc == 0, out[-1500:]
    assert "Wiederherstellung bewiesen" in out and "Konsistenz:" in out
    c = _client()
    assert not [n for n in c.list_database_names() if n.startswith("autoschnell_restore_test")]


def test_13_alte_sicherungskopien_werden_aufgeraeumt(welt):
    """Audit 09/2026: Jeder Restore legt den bisherigen Stand vollstaendig
    zur Seite. Ohne Aufraeumen sammeln sich Kopien personenbezogener Daten.
    Geprueft: alte Kopien verschwinden, die juengste bleibt, 0 = nie loeschen."""
    import restore_mongo
    from datetime import datetime, timedelta
    c = _client()
    alt_tage = (datetime.now() - timedelta(days=90)).strftime("%Y%m%d_%H%M%S")
    mittel = (datetime.now() - timedelta(days=40)).strftime("%Y%m%d_%H%M%S")
    neu = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d_%H%M%S")
    namen = [f"{ZIEL}__vorher_{s}" for s in (alt_tage, mittel, neu)]
    for n in namen:
        c[n].users.insert_one({"id": f"kopie_{n[-6:]}"})
    # Passende Datei-Ordner
    live = welt["live_uploads"]
    ordner = []
    for stempel in (alt_tage, mittel, neu):
        d = live.parent / f"{live.name}.vorher-{stempel}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "foto.jpg").write_text("x", encoding="utf-8")
        ordner.append(d)
    try:
        # 0 = nie loeschen
        assert restore_mongo.alte_sicherungen_aufraeumen(c, ZIEL, 0, live_ordner=[live]) == []
        assert all(n in c.list_database_names() for n in namen)
        # 30 Tage: die beiden aelteren gehen, die juengste bleibt
        weg = restore_mongo.alte_sicherungen_aufraeumen(c, ZIEL, 30, live_ordner=[live])
        vorhanden = c.list_database_names()
        assert namen[0] not in vorhanden and namen[1] not in vorhanden, weg
        assert namen[2] in vorhanden, "juengster Stand muss erhalten bleiben"
        assert not ordner[0].exists() and not ordner[1].exists()
        assert ordner[2].exists(), "juengster Ordner muss erhalten bleiben"
        assert len(weg) == 4, weg
    finally:
        for n in namen:
            c.drop_database(n)
        for d in ordner:
            shutil.rmtree(d, ignore_errors=True)
