# -*- coding: utf-8 -*-
"""Pruefbericht 20.09.2026 — Wiederherstellung im Container (SK-01/03/04).

  SK-01  /app/uploads und /app/local_storage sind eingehaengte Volumes. Ein
         Einhaengepunkt laesst sich nicht umbenennen (EBUSY) — der Restore
         rollte bei JEDEM Backup mit Dateien zurueck. Jetzt wird bei einem
         Einhaengepunkt IM Volume umgeschaltet (.restore-/.vorher-Unterordner),
         die Sicherung laesst diese Hilfsordner aus, das Aufraeumen findet sie.
  SK-03  Ein harter Abbruch (Strg+C, Verbindung weg) in Schritt 5/6 fuehrt in
         den Rueckbau statt den Wartungsmodus fuer immer stehen zu lassen.
  SK-04  Der genannte Befehl zum Aufheben funktioniert (Skript im Image statt
         mongosh ohne Anmeldung).

Echte Einhaengepunkte gibt es im Test nicht: die Pruefung wird ersetzt, und
das Umbenennen eines "eingehaengten" Ordners scheitert wie im Container.
Braucht nur Mongo.
"""
import json
import os
import sys
import uuid
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
SCRIPTS = BACKEND / "scripts"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(SCRIPTS))
MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
SUF = uuid.uuid4().hex[:8]
QUELLE = f"rvol_quelle_{SUF}"
ZIEL = f"rvol_ziel_{SUF}"


def _client():
    from pymongo import MongoClient
    return MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)


@pytest.fixture(scope="module")
def mongo():
    c = _client()
    yield c
    for name in list(c.list_database_names()):
        if SUF in name:
            c.drop_database(name)
    c.close()


def _dateien(ordner: Path) -> set:
    """Nutzdateien eines Datei-Speichers — OHNE die Hilfsordner des Restores."""
    if not ordner.is_dir():
        return set()
    out = set()
    for p in ordner.rglob("*"):
        rel = p.relative_to(ordner)
        if p.is_file() and not rel.parts[0].startswith((".restore-", ".vorher-")):
            out.add(str(rel).replace("\\", "/"))
    return out


@pytest.fixture
def sicherung(tmp_path, monkeypatch, mongo):
    if not os.environ.get("MONGO_URL"):
        monkeypatch.setenv("MONGO_URL", MONGO_URL)
    if not os.environ.get("DB_NAME"):
        monkeypatch.setenv("DB_NAME", f"{QUELLE}_deps")
    import backup_service  # noqa: F401  (laedt .env — danach die Umgebung setzen)
    import backup_mongo as bm
    src_up, src_ls = tmp_path / "q_up", tmp_path / "q_ls"
    (src_up / "fotos").mkdir(parents=True)
    (src_up / "fotos" / "a.jpg").write_bytes(b"A" * 10)
    src_ls.mkdir()
    (src_ls / "s.html").write_text("<html>", encoding="utf-8")
    # Rest einer frueheren Wiederherstellung IM Volume — gehoert nicht in die Sicherung
    (src_up / ".vorher-20200101_000000").mkdir()
    (src_up / ".vorher-20200101_000000" / "uralt.jpg").write_bytes(b"x")
    monkeypatch.setattr(bm, "UPLOADS_DIR", src_up)
    monkeypatch.setattr(bm, "LOCAL_STORAGE_DIR", src_ls)
    for var in ("EMERGENT_LLM_KEY", "S3_BUCKET", "S3_ENDPOINT", "S3_ACCESS_KEY",
                "S3_SECRET_KEY", "BACKUP_S3_BUCKET", "BACKUP_S3_ENDPOINT"):
        monkeypatch.setenv(var, "")
    monkeypatch.setenv("BACKUP_SNAPSHOT_PAUSE_S", "0")
    mongo[QUELLE].users.delete_many({})
    mongo[QUELLE].vehicles.delete_many({})
    mongo[QUELLE].users.insert_many([{"id": f"u{i}"} for i in range(5)])
    mongo[QUELLE].vehicles.insert_many([{"id": f"v{i}"} for i in range(3)])
    base = tmp_path / "b"
    assert bm.backup_erstellen(base, db_name=QUELLE, mongo_url=MONGO_URL) == 0
    [ordner] = [p for p in base.iterdir() if p.is_dir() and p.name.startswith("autoschnell-")]
    return ordner


@pytest.fixture
def volumes(tmp_path, monkeypatch):
    """Live-Ordner, die sich wie Docker-Volumes verhalten."""
    import restore_mongo as RM
    live_up, live_ls = tmp_path / "live" / "uploads", tmp_path / "live" / "local_storage"
    live_up.mkdir(parents=True)
    live_ls.mkdir(parents=True)
    (live_up / "bisher.jpg").write_bytes(b"b")
    monkeypatch.setenv("BACKUP_UPLOADS_DIR", str(live_up))
    monkeypatch.setenv("BACKUP_LOCAL_STORAGE_DIR", str(live_ls))
    eingehaengt = {live_up.resolve(), live_ls.resolve()}
    monkeypatch.setattr(RM, "_ist_einhaengepunkt", lambda p: Path(p).resolve() in eingehaengt)
    original = RM._verzeichnis_umbenennen

    def umbenennen(von, nach):
        if Path(von).resolve() in eingehaengt or Path(nach).resolve() in eingehaengt:
            raise OSError(16, "Device or resource busy")          # wie im Container
        return original(von, nach)
    monkeypatch.setattr(RM, "_verzeichnis_umbenennen", umbenennen)
    return {"up": live_up, "ls": live_ls, "basis": tmp_path / "live"}


def test_sk01_sicherung_laesst_die_hilfsordner_aus(sicherung):
    m = json.loads((sicherung / "manifest.json").read_text(encoding="utf-8"))
    assert "uploads/fotos/a.jpg" in m["files"]
    assert not [k for k in m["files"] if ".vorher-" in k or ".restore-" in k], m["files"]


def test_sk01_restore_in_eingehaengte_ordner(sicherung, volumes, mongo, capsys):
    import restore_mongo as RM
    mongo[ZIEL].users.insert_one({"id": "alt"})
    assert RM.main([str(sicherung), "--db", ZIEL, "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "im Volume (Einhaengepunkt)" in out and "schreibbar" in out, out[-800:]

    rc = RM.main([str(sicherung), "--db", ZIEL, "--yes"])
    out = capsys.readouterr().out
    assert rc == 0 and "RESTORE OK" in out, out[-1500:]
    assert _dateien(volumes["up"]) == {"fotos/a.jpg"}
    assert _dateien(volumes["ls"]) == {"s.html"}
    vorher = [p for p in volumes["up"].iterdir() if p.name.startswith(".vorher-")]
    assert len(vorher) == 1 and (vorher[0] / "bisher.jpg").is_file()
    assert not [p for p in volumes["up"].iterdir() if p.name.startswith(".restore-")]
    # nichts NEBEN den Volumes (dort koennte der Container gar nicht schreiben)
    assert sorted(p.name for p in volumes["basis"].iterdir()) == ["local_storage", "uploads"]
    assert mongo[ZIEL].users.count_documents({}) == 5
    assert mongo[ZIEL].system_flags.find_one({"_id": "wartungsmodus"})["aktiv"] is False


def test_sk01_rollback_im_volume(sicherung, volumes, mongo, monkeypatch, capsys):
    import restore_mongo as RM
    mongo[ZIEL].drop_collection("users")
    mongo[ZIEL].users.insert_one({"id": "marker"})
    original = RM._rename_collection
    zaehler = {"n": 0}

    def kaputt(client, von, nach):
        if "__restore_" in von and nach.startswith(f"{ZIEL}."):
            zaehler["n"] += 1
            if zaehler["n"] == 2:
                raise RuntimeError("simulierter Fehler beim Umschalten")
        return original(client, von, nach)
    monkeypatch.setattr(RM, "_rename_collection", kaputt)
    rc = RM.main([str(sicherung), "--db", ZIEL, "--yes"])
    out = capsys.readouterr().out
    assert rc == 1 and "ROLLBACK OK" in out, out[-1500:]
    assert _dateien(volumes["up"]) == {"bisher.jpg"}, "bisheriger Stand wieder live"
    assert not [p for p in volumes["up"].iterdir() if p.name.startswith((".vorher-", ".restore-"))]
    assert mongo[ZIEL].users.find_one({"id": "marker"}) is not None
    assert mongo[ZIEL].system_flags.find_one({"_id": "wartungsmodus"})["aktiv"] is False


def test_sk03_strg_c_beim_umschalten_fuehrt_in_den_rueckbau(sicherung, volumes, mongo,
                                                           monkeypatch, capsys):
    import restore_mongo as RM
    mongo[ZIEL].drop_collection("users")
    mongo[ZIEL].users.insert_one({"id": "marker03"})
    original = RM._rename_collection
    zaehler = {"n": 0}

    def unterbrochen(client, von, nach):
        if "__restore_" in von and nach.startswith(f"{ZIEL}."):
            zaehler["n"] += 1
            if zaehler["n"] == 2:
                raise KeyboardInterrupt()
        return original(client, von, nach)
    monkeypatch.setattr(RM, "_rename_collection", unterbrochen)
    rc = RM.main([str(sicherung), "--db", ZIEL, "--yes"])
    out = capsys.readouterr().out
    assert rc == 1 and "KeyboardInterrupt" in out and "ROLLBACK OK" in out, out[-1500:]
    assert mongo[ZIEL].users.find_one({"id": "marker03"}) is not None
    assert _dateien(volumes["up"]) == {"bisher.jpg"}
    assert mongo[ZIEL].system_flags.find_one({"_id": "wartungsmodus"})["aktiv"] is False


def test_sk01_aufraeumen_findet_die_vorher_ordner_im_volume(tmp_path, mongo):
    import restore_mongo as RM
    up = tmp_path / "uploads"
    for st in ("20200101_000000", "20200102_000000", "20200102_000000-2"):
        (up / f".vorher-{st}").mkdir(parents=True)
    weg = RM.alte_sicherungen_aufraeumen(mongo, f"nicht_da_{SUF}", tage=30, behalte=1,
                                         live_ordner=[up])
    rest = sorted(p.name for p in up.iterdir())
    assert len(weg) == 2 and len(rest) == 1, (weg, rest)


def test_sk04_befehl_zum_aufheben_funktioniert(mongo, monkeypatch):
    import restore_mongo as RM
    import wartung_aufheben as WA
    befehl = RM._wartungsmodus_befehl("autoschnell")
    assert "mongosh" not in befehl and "scripts/wartung_aufheben.py --db autoschnell --ja" in befehl
    name = f"rvol_flag_{SUF}"
    monkeypatch.setenv("MONGO_URL", MONGO_URL)
    RM.wartungsmodus(mongo[name], True)
    assert WA.main(["--db", name]) == 1, "ohne --ja nur anzeigen"
    assert mongo[name].system_flags.find_one({"_id": "wartungsmodus"})["aktiv"] is True
    assert WA.main(["--db", name, "--ja"]) == 0
    assert mongo[name].system_flags.find_one({"_id": "wartungsmodus"})["aktiv"] is False
    assert WA.main(["--db", name]) == 0, "nichts mehr aktiv"
