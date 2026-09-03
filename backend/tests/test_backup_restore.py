# -*- coding: utf-8 -*-
"""Backup/Restore (Pruefbericht Runde 5): Restore darf die Zieldatenbank
nie teilweise zerstoeren.

- Backup schreibt manifest.json mit Pruefsummen und meldet BACKUP OK.
- --dry-run prueft und veraendert nichts.
- Restore in eine Ziel-DB: Daten identisch, bisheriger Stand bleibt als
  <db>__vorher_* erhalten.
- Beschaedigte Datei (Pruefsumme falsch / abgeschnitten): Restore bricht
  VOR jeder Aenderung ab — Zieldatenbank byteidentisch wie vorher.

Braucht nur Mongo (kein Backend).
"""
import gzip
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
SCRIPTS = BACKEND / "scripts"
MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
SUF = uuid.uuid4().hex[:8]
QUELLE = f"r5bak_quelle_{SUF}"
ZIEL = f"r5bak_ziel_{SUF}"


def _client():
    from pymongo import MongoClient
    return MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)


def _run(script, *args, env=None):
    e = dict(os.environ, MONGO_URL=MONGO_URL, PYTHONIOENCODING="utf-8")
    e.update(env or {})
    r = subprocess.run([sys.executable, "-X", "utf8", str(SCRIPTS / script), *args],
                       capture_output=True, text=True, env=e, timeout=300)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


@pytest.fixture(scope="module")
def welt(tmp_path_factory):
    z = {"dir": tmp_path_factory.mktemp("bak")}
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
                   env={"DB_NAME": QUELLE})
    assert rc in (0, 2), out[-800:]      # 2 = Datei-Speicher fehlt lokal (ok im Test)
    ordner = [p for p in welt["dir"].iterdir() if p.is_dir() and p.name.startswith("autoschnell-")]
    assert len(ordner) == 1, out[-500:]
    welt["backup"] = ordner[0]
    assert (welt["backup"] / "manifest.json").is_file()
    assert (welt["backup"] / QUELLE / "users.bson.gz").is_file()
    assert "BACKUP" in out


def test_02_dry_run_veraendert_nichts(welt):
    c = _client()
    rc, out = _run("restore_mongo.py", str(welt["backup"]), "--db", ZIEL, "--dry-run")
    assert rc == 0 and "DRY-RUN OK" in out, out[-800:]
    assert ZIEL not in c.list_database_names()


def test_03_restore_identisch_und_alter_stand_bleibt(welt):
    c = _client()
    # Ziel hat vorher "alte" Daten, die erhalten bleiben muessen (als __vorher)
    c[ZIEL].users.insert_one({"id": "alt", "email": "alt@x.de"})
    rc, out = _run("restore_mongo.py", str(welt["backup"]), "--db", ZIEL, "--yes")
    assert rc == 0 and "RESTORE OK" in out, out[-800:]
    assert c[ZIEL].users.count_documents({}) == 50
    assert c[ZIEL].vehicles.count_documents({}) == 30
    assert c[ZIEL].users.find_one({"id": "u7"})["n"] == 7
    assert any(i.get("unique") for i in c[ZIEL].users.list_indexes())
    vorher = [n for n in c.list_database_names() if n.startswith(f"{ZIEL}__vorher_")]
    assert len(vorher) == 1
    assert c[vorher[0]].users.find_one({"id": "alt"}) is not None
    assert not [n for n in c.list_database_names() if n.startswith(f"{ZIEL}__restore_")]


def test_04_beschaedigtes_backup_aendert_nichts(welt):
    c = _client()
    import shutil
    kaputt = welt["dir"] / "kaputt"
    shutil.copytree(welt["backup"], kaputt)
    f = kaputt / QUELLE / "vehicles.bson.gz"
    daten = f.read_bytes()
    f.write_bytes(daten[: len(daten) // 2])          # abgeschnitten
    snapshot_vorher = {n: c[ZIEL][n].count_documents({}) for n in c[ZIEL].list_collection_names()}
    rc, out = _run("restore_mongo.py", str(kaputt), "--db", ZIEL, "--yes")
    assert rc == 1 and "NICHTS veraendert" in out, out[-800:]
    assert "beschaedigt" in out.lower() or "pruefsumme" in out.lower()
    assert {n: c[ZIEL][n].count_documents({}) for n in c[ZIEL].list_collection_names()} == snapshot_vorher
    assert c[ZIEL].users.find_one({"id": "u7"})["n"] == 7
    # Ohne Manifest (altes Backup) ohne Freigabe abgelehnt
    (kaputt / "manifest.json").unlink()
    rc, out = _run("restore_mongo.py", str(kaputt), "--db", ZIEL, "--yes")
    assert rc == 1 and "manifest" in out.lower()
