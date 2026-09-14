# -*- coding: utf-8 -*-
"""Kontonummer (13.09.2026), Schritt 7 — scripts/live_reset.py.

  - Probelauf (Standard) aendert nichts und listet Sammlungen und Dateizahlen
  - Ausfuehren nur mit --bestaetige <DB> und 'LOESCHEN'; unbekannte Sammlung
    blockiert
  - danach bleiben nur Super-Admin, seine Firma, sein Abo, Systemdaten, Caches;
    alle Indizes bleiben (nur delete_many)
  - Dateien: Praefixe weg ausser logo/<sa_dealer_id>/, Snapshot-Keys gehen an
    den Loescher, bevor listing_snapshots geleert wird
  - --nummern-ab hebt nur an; Super-Admin fehlt/doppelt -> Exit 2
  - Abbruch mitten im Lauf -> erneuter Aufruf setzt fort
  - storage_service.zaehle_prefix (LocalDiskStorage mit tmp_path, S3 paginiert)

In-process, synchrones pymongo gegen die Wegwerf-DB autoschnell_reset_<hex>.
Kein Import von server.py, deps oder routes (keine Event-Loop-Falle).
"""
import importlib.util
import json
import os
import sys
import uuid
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
SCRIPTS = BACKEND / "scripts"
for _p in (str(BACKEND), str(SCRIPTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
SA = "t-reset-admin"


def _skript():
    spec = importlib.util.spec_from_file_location("live_reset_s7", SCRIPTS / "live_reset.py")
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


LR = _skript()


class FalscherSpeicher:
    name = "falsch"

    def __init__(self, keys):
        self.keys = set(keys)
        self.geloescht = []
        self.gezaehlt = []

    def zaehle_prefix(self, prefix):
        self.gezaehlt.append(prefix)
        return sum(1 for k in self.keys if k.startswith(prefix))

    def delete_prefix(self, prefix):
        self.geloescht.append(prefix)
        weg = {k for k in self.keys if k.startswith(prefix)}
        self.keys -= weg
        return len(weg)


class FalscherSnapshotLoescher:
    def __init__(self, scheitert=()):
        self.keys = []
        self.scheitert = set(scheitert)

    def __call__(self, key):
        self.keys.append(key)
        return key not in self.scheitert


class _Abbruch(BaseException):
    """Simulierter harter Abbruch (Strom weg, Strg+C) — kein Exception-Fang."""


DATEIEN = {
    "protocol/d_1/unterschrift.png", "protocol/d_2/abholprotokoll.pdf",
    "pickup/d_1/foto.jpg", "resale/d_2/foto.jpg", "beweise/mobile/b1.pdf",
    "logo/d_sa/logo.png", "logo/d_1/logo.png", "logo/d_2/logo.png",
    "test/bleibt.jpg",
}


@pytest.fixture
def db():
    from pymongo import MongoClient
    client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    name = f"autoschnell_reset_{uuid.uuid4().hex[:10]}"
    try:
        yield client[name]
    finally:
        client.drop_database(name)
        client.close()


def _welt(db):
    db.users.insert_many([
        {"id": "sa", "username": SA, "role": "admin", "is_super_admin": True,
         "dealer_id": "d_sa", "current_session_id": "sid-alt", "mfa_aktiv": True,
         "login_ips_bekannt": ["abcdef0123456789"]},
        {"id": "sa2", "role": "admin", "is_super_admin": True, "email": "alt@x.test"},
        {"id": "adm", "role": "admin", "email": "normal@x.test"},
        {"id": "c1", "role": "dealer", "dealer_id": "d_1", "kontonummer": "1002"},
        {"id": "s1", "role": "sucher", "dealer_id": "d_1", "kontonummer": "1002-1"},
        {"id": "c2", "role": "dealer", "dealer_id": "d_2", "kontonummer": "1003"},
        {"id": "s2", "role": "sucher", "dealer_id": "d_2", "kontonummer": "1003-1"},
        {"id": "k1", "role": "b2b_buyer", "kontonummer": "1004"},
    ])
    db.dealers.insert_many([
        {"id": "d_sa", "kunden_nr": 1001, "user_id": "sa"},
        {"id": "d_1", "kunden_nr": 1002}, {"id": "d_2", "kunden_nr": 1003}])
    db.subscriptions.insert_many([
        {"id": "sub_sa", "dealer_id": "d_sa", "plan": "lifetime"},
        {"id": "sub_1", "dealer_id": "d_1"}, {"id": "sub_s1", "dealer_id": "d_1", "user_id": "s1"},
        {"id": "sub_2", "dealer_id": "d_2"}, {"id": "sub_ohne"}])
    db.driver_accounts.insert_one({"id": "f1", "kontonummer": "1005", "driver_code": "FD-X"})
    for name in LR.GANZ_LEEREN:
        if name == "listing_snapshots":
            db[name].insert_many([
                {"id": "sn1", "png_path": "autohandel/snapshots/v1/a.jpg",
                 "pdf_path": "autohandel/snapshots/v1/a.pdf"},
                {"id": "sn2", "png_path": "autohandel/snapshots/v2/b.jpg", "pdf_path": ""}])
        elif name == "payment_transactions":
            db[name].insert_one({"id": "pt1", "payment_status": "paid"})
        elif name == "pickup_protocols":
            db[name].insert_one({"id": "pp1", "pdf_path": "protocol/d_2/abholprotokoll.pdf",
                                 "signature_driver_key": "protocol/d_1/unterschrift.png"})
        elif db[name].count_documents({}) == 0:
            db[name].insert_one({"id": f"x_{name}", "dealer_id": "d_1"})
    db.system_flags.insert_many([{"_id": "schema", "version": 7},
                                 {"_id": "wartungsmodus", "aktiv": False}])
    db.schema_migrations.insert_one({"_id": "m7"})
    db.counters.insert_one({"_id": "kunden_nr", "seq": 5})
    for name in ("job_locks", "sperren", "provider_limits", "provider_budget",
                 "provider_slots", "provider_stats"):
        db[name].insert_one({"_id": f"k_{name}"})
    for name in LR.CACHES:
        db[name].insert_one({"_id": f"c_{name}"})
    db.zz_unbekannt.insert_one({"_id": 1})
    # Indizes wie in echt: Teil-Index, eindeutige Nummer, TTL
    db.users.create_index("kontonummer", name="kontonummer_eindeutig", unique=True,
                          partialFilterExpression={"kontonummer": {"$type": "string"}})
    db.dealers.create_index("kunden_nr", name="kunden_nr_unique", unique=True, sparse=True)
    db.activity_logs.create_index("created_at")
    db.rate_limits.create_index("expires_at", expireAfterSeconds=0)
    db.listings_cache.create_index("fetched_at")


def _zaehlstaende(db):
    return {n: db[n].count_documents({}) for n in sorted(db.list_collection_names())}


def _indizes(db):
    return {n: db[n].index_information() for n in sorted(db.list_collection_names())}


# ------------------------------------------------------------------ Probelauf
def test_01_probelauf_aendert_nichts_und_listet(db, capsys, tmp_path):
    _welt(db)
    vorher = _zaehlstaende(db)
    sp, sl = FalscherSpeicher(DATEIEN), FalscherSnapshotLoescher()
    bericht = tmp_path / "probe.json"
    rc = LR.main(["--super-admin-username", SA, "--bericht", str(bericht)],
                 db=db, speicher=sp, snapshot_loescher=sl)
    assert rc == 0
    out = capsys.readouterr().out
    assert _zaehlstaende(db) == vorher
    assert db.system_flags.find_one({"_id": "live_reset"}) is None
    assert sp.keys == DATEIEN and not sp.geloescht and not sl.keys
    assert "PROBELAUF" in out and "UNBEKANNT" in out and "zz_unbekannt" in out
    assert "activity_logs" in out and "counters" in out and "WARNUNG" in out
    for p in ("protocol/", "pickup/", "resale/", "beweise/"):
        assert p in out
    b = json.loads(bericht.read_text(encoding="utf-8"))
    assert b["modus"] == "probelauf" and b["unbekannt"] == ["zz_unbekannt"]
    assert b["dateien"]["praefixe"] == {"protocol/": 2, "pickup/": 1, "resale/": 1, "beweise/": 1}
    assert b["dateien"]["logo"] == {"firmen": 2, "dateien": 2}
    assert b["dateien"]["snapshot_keys"] == 3
    zeilen = {z["name"]: z for z in b["sammlungen"]}
    assert zeilen["users"]["loeschen"] == 7 and zeilen["users"]["bleibt"] == 1
    assert zeilen["subscriptions"]["loeschen"] == 4 and zeilen["subscriptions"]["bleibt"] == 1
    assert zeilen["counters"]["loeschen"] == 0 and zeilen["listings_cache"]["loeschen"] == 0
    assert b["zahlungen"]["payment_transactions_paid"] == 1


# ------------------------------------------------------- Bestaetigung / Sperren
def test_02_ohne_bestaetigung_und_mit_unbekannter_sammlung_abbruch(db, monkeypatch):
    _welt(db)
    sp, sl = FalscherSpeicher(DATEIEN), FalscherSnapshotLoescher()
    vorher = _zaehlstaende(db)
    basis = ["--super-admin-username", SA, "--ausfuehren"]
    # unbekannte Sammlung blockiert, auch mit Bestaetigung
    assert LR.main(basis + ["--bestaetige", db.name, "--ja"],
                   db=db, speicher=sp, snapshot_loescher=sl) == 3
    db.zz_unbekannt.drop()
    vorher.pop("zz_unbekannt")
    # ohne / mit falschem DB-Namen
    assert LR.main(basis, db=db, speicher=sp, snapshot_loescher=sl) == 4
    assert LR.main(basis + ["--bestaetige", "autoschnell"], db=db, speicher=sp,
                   snapshot_loescher=sl) == 4
    # richtiger Name, aber Eingabe falsch bzw. keine Eingabe moeglich
    monkeypatch.setattr("builtins.input", lambda *_a: "loeschen bitte")
    assert LR.main(basis + ["--bestaetige", db.name], db=db, speicher=sp,
                   snapshot_loescher=sl) == 4

    def _eof(*_a):
        raise EOFError
    monkeypatch.setattr("builtins.input", _eof)
    assert LR.main(basis + ["--bestaetige", db.name], db=db, speicher=sp,
                   snapshot_loescher=sl) == 4
    assert _zaehlstaende(db) == vorher
    assert db.system_flags.find_one({"_id": "live_reset"}) is None
    assert sp.keys == DATEIEN and not sp.geloescht and not sl.keys


# ---------------------------------------------------------------- Ausfuehren
def test_03_ausfuehren_laesst_nur_super_admin_und_systemdaten(db, monkeypatch, tmp_path):
    _welt(db)
    db.zz_unbekannt.drop()
    indizes_vorher = _indizes(db)
    vorher = _zaehlstaende(db)
    sp = FalscherSpeicher(DATEIEN)
    sl = FalscherSnapshotLoescher()
    # Reihenfolge: Snapshot-Keys muessen geloescht sein, solange die Dokumente
    # noch existieren
    snaps_beim_loeschen = []
    orig = sl.__call__

    def loescher(key):
        snaps_beim_loeschen.append(db.listing_snapshots.count_documents({}))
        return orig(key)
    monkeypatch.setattr("builtins.input", lambda *_a: "LOESCHEN")
    bericht = tmp_path / "reset.json"
    rc = LR.main(["--super-admin-username", SA, "--ausfuehren", "--bestaetige", db.name,
                  "--bericht", str(bericht)], db=db, speicher=sp, snapshot_loescher=loescher)
    assert rc == 0

    # Konten und Firmen
    assert [u["id"] for u in db.users.find()] == ["sa"]
    assert [d["id"] for d in db.dealers.find()] == ["d_sa"]
    assert [s["id"] for s in db.subscriptions.find()] == ["sub_sa"]
    sa = db.users.find_one({"id": "sa"})
    assert sa["current_session_id"] is None
    assert sa["mfa_aktiv"] is True and sa["login_ips_bekannt"] == ["abcdef0123456789"]
    # ganz geleert (activity_logs: nur der Abschluss-Eintrag)
    for name in LR.GANZ_LEEREN:
        if name == "activity_logs":
            logs = list(db.activity_logs.find())
            assert len(logs) == 1 and logs[0]["action"] == "system.live_reset"
            assert logs[0]["meta"]["geloescht"]["users"] == 7
        else:
            assert db[name].count_documents({}) == 0, name
    # behalten
    for name in LR.BEHALTEN + LR.CACHES:
        erwartet = vorher[name] + (1 if name == "system_flags" else 0)
        assert db[name].count_documents({}) == erwartet, name
    assert db.counters.find_one({"_id": "kunden_nr"})["seq"] == 5
    flag = db.system_flags.find_one({"_id": "live_reset"})
    assert flag["status"] == "fertig" and flag["dateien_fertig"] is True
    assert flag["stats"]["dateien"]["snapshots"] == 3
    # keine Sammlung und kein Index verschwunden
    assert _indizes(db) == indizes_vorher
    # Dateien
    assert sp.keys == {"logo/d_sa/logo.png", "test/bleibt.jpg"}
    assert "logo/d_sa/" not in sp.geloescht
    assert set(sp.geloescht) == {"protocol/", "pickup/", "resale/", "beweise/",
                                 "logo/d_1/", "logo/d_2/"}
    assert sorted(sl.keys) == ["autohandel/snapshots/v1/a.jpg", "autohandel/snapshots/v1/a.pdf",
                               "autohandel/snapshots/v2/b.jpg"]
    assert snaps_beim_loeschen and all(n == 2 for n in snaps_beim_loeschen)
    b = json.loads(bericht.read_text(encoding="utf-8"))
    assert b["modus"] == "ausfuehren" and b["ergebnis"]["datei_fehler"] == []


def test_04_nummern_ab_hebt_nur_an_und_datei_fehler_im_bericht(db, capsys):
    _welt(db)
    db.zz_unbekannt.drop()
    sp = FalscherSpeicher(DATEIEN)
    sl = FalscherSnapshotLoescher(scheitert={"autohandel/snapshots/v2/b.jpg"})
    basis = ["--super-admin-username", SA, "--ausfuehren", "--bestaetige", db.name, "--ja"]
    rc = LR.main(basis + ["--nummern-ab", "10001", "--caches-leeren"],
                 db=db, speicher=sp, snapshot_loescher=sl)
    assert rc == 5, "nicht geloeschte Datei darf nicht als sauber fertig gelten"
    assert db.counters.find_one({"_id": "kunden_nr"})["seq"] == 9000   # naechste 10001
    for name in LR.CACHES:
        assert db[name].count_documents({}) == 0
    flag = db.system_flags.find_one({"_id": "live_reset"})
    assert flag["status"] == "fertig"
    assert [f["ziel"] for f in flag["datei_fehler"]] == ["autohandel/snapshots/v2/b.jpg"]
    capsys.readouterr()
    # zweiter Lauf mit kleinerem Wert senkt nicht
    assert LR.main(basis + ["--nummern-ab", "2000"], db=db, speicher=sp,
                   snapshot_loescher=FalscherSnapshotLoescher()) == 0
    assert db.counters.find_one({"_id": "kunden_nr"})["seq"] == 9000
    with pytest.raises(SystemExit):
        LR.main(basis + ["--nummern-ab", "999"], db=db, speicher=sp)


# ------------------------------------------------------------- Super-Admin
def test_05_super_admin_fehlt_oder_doppelt_exit_2(db):
    _welt(db)
    sp, sl = FalscherSpeicher(DATEIEN), FalscherSnapshotLoescher()
    vorher = _zaehlstaende(db)
    assert LR.main(["--super-admin-username", "gibt-es-nicht"], db=db, speicher=sp,
                   snapshot_loescher=sl) == 2
    # gleicher Benutzername, aber kein Super-Admin -> zaehlt nicht
    db.users.update_one({"id": "adm"}, {"$set": {"username": "normal-admin"}})
    assert LR.main(["--super-admin-username", "normal-admin", "--ausfuehren",
                    "--bestaetige", db.name, "--ja"], db=db, speicher=sp,
                   snapshot_loescher=sl) == 2
    db.users.insert_one({"id": "sa_dup", "username": SA, "role": "admin",
                         "is_super_admin": True})
    assert LR.main(["--super-admin-username", SA, "--ausfuehren", "--bestaetige", db.name,
                    "--ja"], db=db, speicher=sp, snapshot_loescher=sl) == 2
    vorher["users"] += 1
    assert _zaehlstaende(db) == vorher
    assert not sp.geloescht and not sl.keys


# ------------------------------------------------------------- Fortsetzung
def test_06_abbruch_mitten_im_lauf_wird_fortgesetzt(db, monkeypatch, capsys):
    _welt(db)
    db.zz_unbekannt.drop()
    sp, sl = FalscherSpeicher(DATEIEN), FalscherSnapshotLoescher()
    basis = ["--super-admin-username", SA, "--ausfuehren", "--bestaetige", db.name, "--ja"]
    echt = LR._leeren
    aufrufe = []

    def bricht_ab(d, name, filt):
        aufrufe.append(name)
        if len(aufrufe) == 10:
            raise _Abbruch()
        return echt(d, name, filt)
    monkeypatch.setattr(LR, "_leeren", bricht_ab)
    with pytest.raises(_Abbruch):
        LR.main(basis, db=db, speicher=sp, snapshot_loescher=sl)
    flag = db.system_flags.find_one({"_id": "live_reset"})
    assert flag["status"] == "laeuft" and flag["dateien_fertig"] is True
    assert db.users.count_documents({}) == 8 and db.dealers.count_documents({}) == 3
    assert db.driver_accounts.count_documents({}) == 0          # schon geleert
    geloescht_vorher, snaps_vorher = list(sp.geloescht), list(sl.keys)
    capsys.readouterr()

    monkeypatch.setattr(LR, "_leeren", echt)
    assert LR.main(basis, db=db, speicher=sp, snapshot_loescher=sl) == 0
    out = capsys.readouterr().out
    assert "Fortsetzung" in out
    # Dateien nicht doppelt angefasst
    assert sp.geloescht == geloescht_vorher and sl.keys == snaps_vorher
    assert [u["id"] for u in db.users.find()] == ["sa"]
    assert [d["id"] for d in db.dealers.find()] == ["d_sa"]
    flag = db.system_flags.find_one({"_id": "live_reset"})
    assert flag["status"] == "fertig" and flag["laeufe"] == 2
    assert flag["stats"]["geloescht"]["driver_accounts"] == 1


# ---------------------------------------------------------- zaehle_prefix
def test_07_zaehle_prefix_lokal(tmp_path):
    from storage_service import LocalDiskStorage, StorageError
    s = LocalDiskStorage(root=tmp_path)
    for k in ("resale/d1/a.jpg", "resale/d2/b.jpg", "logo/d1/logo.png", "logo/d10/logo.png"):
        s.save(k, b"x")
    assert s.zaehle_prefix("resale/") == 2
    assert s.zaehle_prefix("logo/d1/") == 1
    assert s.zaehle_prefix("beweise/") == 0
    assert s.exists("resale/d1/a.jpg"), "zaehlen darf nichts loeschen"
    with pytest.raises(StorageError):
        s.zaehle_prefix("../")
    with pytest.raises(StorageError):
        s.zaehle_prefix("resale/../../x/")


def test_08_zaehle_prefix_s3_paginiert_ohne_loeschen():
    from storage_service import S3Storage

    class Client:
        def __init__(self):
            self.aufrufe = []

        def list_objects_v2(self, **kw):
            self.aufrufe.append(kw)
            if "ContinuationToken" not in kw:
                return {"Contents": [{"Key": "a"}, {"Key": "b"}], "IsTruncated": True,
                        "NextContinuationToken": "t2"}
            return {"Contents": [{"Key": "c"}], "IsTruncated": False}

        def delete_objects(self, **kw):
            raise AssertionError("zaehlen darf nichts loeschen")

    s3 = S3Storage.__new__(S3Storage)
    s3.bucket, s3.client = "eimer", Client()
    assert s3.zaehle_prefix("protocol/") == 3
    assert s3.client.aufrufe[0] == {"Bucket": "eimer", "Prefix": "protocol/"}
    assert s3.client.aufrufe[1]["ContinuationToken"] == "t2"
