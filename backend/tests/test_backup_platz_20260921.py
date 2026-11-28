# -*- coding: utf-8 -*-
"""Pruefbericht 20.09.2026 — Sicherung bei knappem Platz (AL-10, SK-08).

  AL-10  Vor dem Dump wird der Platz geprueft (1,5 x juengste Sicherung). Reicht
         er nicht, wird vorab eine Sicherung weniger behalten; reicht er dann
         immer noch nicht, endet der Lauf mit klarer Meldung — statt mitten im
         Dump an voller Platte zu scheitern, ohne je wieder aufzuraeumen.
  SK-08  Reste abgebrochener Laeufe (.tmp-autoschnell-*, .tmp-*.tar.gz) werden
         zu Beginn entfernt, frische (laufender Lauf) bleiben.
Braucht kein Mongo.
"""
import json
import os
import sys
import time
from collections import namedtuple
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))


def _bm(monkeypatch):
    if not os.environ.get("MONGO_URL"):
        monkeypatch.setenv("MONGO_URL", "mongodb://127.0.0.1:27017")
    if not os.environ.get("DB_NAME"):
        monkeypatch.setenv("DB_NAME", "autoschnell_platz_test")
    import backup_service  # noqa: F401
    import backup_mongo as bm
    return bm


def _sicherung(base: Path, name: str, groesse: int, gut: bool = True) -> Path:
    p = base / name
    (p / "db").mkdir(parents=True)
    (p / "db" / "users.bson.gz").write_bytes(b"x" * groesse)
    m = {"version": 4, "db": "db", "konsistenz": "snapshot", "inkonsistent": "",
         "created_at": "2026-09-01T03:00:00+00:00", "collections": {"users": 1},
         "files": {}, "unvollstaendig": [] if gut else ["s3: kaputt"]}
    (p / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    return p


Nutzung = namedtuple("Nutzung", "total used free")


def test_sk08_reste_abgebrochener_laeufe(tmp_path, monkeypatch):
    bm = _bm(monkeypatch)
    alt_ordner = tmp_path / ".tmp-autoschnell-2026-09-01_0300"
    alt_ordner.mkdir()
    (alt_ordner / "halb.bson.gz").write_bytes(b"x")
    alt_archiv = tmp_path / ".tmp-autoschnell-2026-09-01_0300.tar.gz"
    alt_archiv.write_bytes(b"x")
    frisch = tmp_path / ".tmp-autoschnell-2026-09-21_0300"
    frisch.mkdir()
    vor_5h = time.time() - 5 * 3600
    for p in (alt_ordner, alt_archiv):
        os.utime(p, (vor_5h, vor_5h))
    weg = bm.reste_aufraeumen(tmp_path, tmp_path / "backup.log")
    assert sorted(weg) == sorted([alt_ordner.name, alt_archiv.name])
    assert not alt_ordner.exists() and not alt_archiv.exists()
    assert frisch.exists(), "ein gerade laufender Lauf bleibt unberuehrt"


def test_al10_platz_reicht(tmp_path, monkeypatch):
    bm = _bm(monkeypatch)
    _sicherung(tmp_path, "autoschnell-2026-09-20_0300", 1000)
    monkeypatch.setattr(bm.shutil, "disk_usage", lambda p: Nutzung(10**9, 0, 10**9))
    assert bm.platz_pruefen(tmp_path, tmp_path / "backup.log") is True


def test_al10_zu_wenig_platz_rotiert_vorab_und_bricht_klar_ab(tmp_path, monkeypatch):
    bm = _bm(monkeypatch)
    monkeypatch.setattr(bm, "KEEP", 3)
    staende = [_sicherung(tmp_path, f"autoschnell-2026-09-{t:02d}_0300", 1000) for t in range(10, 14)]
    monkeypatch.setattr(bm.shutil, "disk_usage", lambda p: Nutzung(10**9, 0, 10))
    assert bm.platz_pruefen(tmp_path, tmp_path / "backup.log") is False
    rest = sorted(p.name for p in tmp_path.iterdir() if p.name.startswith("autoschnell-"))
    assert rest == [p.name for p in staende[-2:]], "vorab eine Sicherung weniger (KEEP-1)"
    log = (tmp_path / "backup.log").read_text(encoding="utf-8")
    assert "zu wenig Speicher" in log and "Speicher voll" in log


def test_al10_lauf_endet_vor_dem_dump(tmp_path, monkeypatch):
    bm = _bm(monkeypatch)
    _sicherung(tmp_path, "autoschnell-2026-09-20_0300", 1000)
    monkeypatch.setattr(bm.shutil, "disk_usage", lambda p: Nutzung(10**9, 0, 10))

    def kein_dump(*a, **k):
        raise AssertionError("der Dump darf gar nicht erst beginnen")
    monkeypatch.setattr(bm, "MongoClient", kein_dump)
    assert bm.backup_erstellen(tmp_path, db_name="egal", mongo_url="mongodb://x") == 1
    assert not list(tmp_path.glob(".tmp-*")), "kein halber Arbeitsordner"
