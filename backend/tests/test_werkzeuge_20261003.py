# -*- coding: utf-8 -*-
"""Werkzeuge (03.10.2026, Wunsch Ahmad): AutoPointer-Vergleich nur fuer Kunde 10002.

"Alle anderen bekommen das nicht, die sollen das gar nicht sehen":
  * Chef und Sucher der Firma 10002 sehen den Eintrag und laden die Datei.
  * Jede andere Firma: leere Liste, Download 404 (wie nicht vorhanden).
  * Betreiber/Fahrer/Kaeufer: leere Liste, Download 403 (keine Firmenroute).

Teil 1 laeuft ohne Server (Freigabe-Logik, Pruefung, Skript), Teil 2 braucht ein
laufendes Backend (wie die anderen HTTP-Tests). Fuer Teil 2 bekommt eine eigene
Testfirma die Kundennummer 10002; haelt eine andere Firma sie, wird deren Nummer
fuer die Dauer des Tests getauscht und danach zurueckgesetzt.
"""
import os
import secrets
import sys
import uuid
from pathlib import Path

import pytest
import requests

import konten  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import werkzeuge as wz  # noqa: E402

API = konten.API
WID = wz.AUTOPOINTER


# ------------------------------------------------------------ Teil 1: ohne Server
def test_01_standard_nur_10002(monkeypatch):
    monkeypatch.delenv("AUTOPOINTER_VERGLEICH_KUNDEN", raising=False)
    assert wz.freigegebene_kunden(WID) == frozenset({"10002"})
    assert wz.ist_freigegeben(WID, 10002)
    assert wz.ist_freigegeben(WID, "10002")
    assert wz.ist_freigegeben(WID, " 010002 ")
    for andere in (10001, 10003, "10002-1", None, "", True):
        assert not wz.ist_freigegeben(WID, andere), andere
    assert wz.freigegebene_werkzeuge(10002) == [WID]
    assert wz.freigegebene_werkzeuge(10023) == []


def test_02_liste_per_umgebung(monkeypatch):
    monkeypatch.setenv("AUTOPOINTER_VERGLEICH_KUNDEN", "10002, 10017;10023")
    assert wz.freigegebene_kunden(WID) == frozenset({"10002", "10017", "10023"})
    assert wz.ist_freigegeben(WID, 10017)
    monkeypatch.setenv("AUTOPOINTER_VERGLEICH_KUNDEN", "")
    assert wz.freigegebene_kunden(WID) == frozenset()
    assert not wz.ist_freigegeben(WID, 10002)
    assert not wz.ist_freigegeben("gibts-nicht", 10002)


def test_03_nur_echte_exe():
    with pytest.raises(ValueError):
        wz.exe_pruefen(b"")
    with pytest.raises(ValueError):
        wz.exe_pruefen(b"PK" + b"\0" * 5000)        # ZIP, kein Programm
    with pytest.raises(ValueError):
        wz.exe_pruefen(b"MZ")                       # zu klein
    wz.exe_pruefen(b"MZ" + b"\0" * 5000)


class _Speicher:
    def __init__(self):
        self.dateien = {}

    def save(self, key, data, max_mb=None):
        assert max_mb == wz.MAX_MB
        self.dateien[key] = data
        return key


class _Sammlung:
    def __init__(self):
        self.docs = {}

    def replace_one(self, filt, doc, upsert=False):
        assert upsert
        self.docs[filt["id"]] = doc


class _Db:
    def __init__(self):
        self.werkzeuge = _Sammlung()


def test_04_hochladen_schreibt_datei_und_eintrag():
    db, sp = _Db(), _Speicher()
    daten = b"MZ" + secrets.token_bytes(4096)
    meta = wz.hochladen(db, WID, daten, "1.0.0", storage=sp)
    assert sp.dateien[meta["schluessel"]] == daten
    assert meta["schluessel"] == "werkzeuge/autopointer-vergleich/AutoSchnell-Vergleich.exe"
    assert db.werkzeuge.docs[WID]["groesse"] == len(daten)
    assert db.werkzeuge.docs[WID]["version"] == "1.0.0"
    assert len(meta["sha256"]) == 64
    with pytest.raises(ValueError):
        wz.hochladen(db, "gibts-nicht", daten, storage=sp)


def test_05_skript(tmp_path):
    from scripts.werkzeug_hochladen import main
    db, sp = _Db(), _Speicher()
    exe = tmp_path / "AutoSchnell-Vergleich.exe"
    exe.write_bytes(b"MZ" + b"\1" * 3000)
    assert main([str(exe), "--version", "1.2.3"], db=db, storage=sp) == 0
    assert db.werkzeuge.docs[WID]["version"] == "1.2.3"
    keine = tmp_path / "bild.png"
    keine.write_bytes(b"\x89PNG" + b"\0" * 3000)
    assert main([str(keine)], db=db, storage=sp) == 2
    assert main([str(tmp_path / "fehlt.exe")], db=db, storage=sp) == 2


def test_06_speicher_grenze_nur_fuer_betreiberdateien():
    from storage_service import MAX_FILE_MB, LocalDiskStorage, StorageError
    sp = LocalDiskStorage(root=Path(os.environ.get("TMP", "/tmp")) / f"wz_{uuid.uuid4().hex[:8]}")
    gross = b"MZ" + b"\0" * (MAX_FILE_MB * 1024 * 1024 + 10)
    with pytest.raises(StorageError):
        sp.save("werkzeuge/test/a.exe", gross)              # Nutzer-Uploads: weiter 25 MB
    sp.save("werkzeuge/test/a.exe", gross, max_mb=wz.MAX_MB)  # Betreiberdatei: erlaubt
    assert sp.groesse("werkzeuge/test/a.exe") == len(gross)
    sp.delete("werkzeuge/test/a.exe")


# ------------------------------------------------------------ Teil 2: ueber HTTP
def _neue_firma():
    s = uuid.uuid4().hex[:8]
    pw = "Wz-Test-" + secrets.token_hex(6) + "!"
    r = konten.registrieren({"email": f"wz_chef_{s}@wztest-mail.de", "password": pw,
                             "company_name": f"WZ Testfirma {s}", "contact_person": "Test"})
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    me = requests.get(f"{API}/auth/me", headers=konten._kopf(token), timeout=30).json()["user"]
    return {"token": token, "dealer_id": me["dealer_id"], "user_id": me["id"]}


@pytest.fixture(scope="module")
def welt():
    db = konten._db()
    firma = _neue_firma()
    andere = _neue_firma()
    getauscht = None
    halter = db.dealers.find_one({"kunden_nr": {"$in": [10002, "10002"]}}, {"_id": 0, "id": 1, "kunden_nr": 1})
    if halter and halter["id"] != firma["dealer_id"]:
        ersatz = 9_000_000 + secrets.randbelow(900_000)
        db.dealers.update_one({"id": halter["id"]}, {"$set": {"kunden_nr": ersatz}})
        getauscht = (halter["id"], halter["kunden_nr"])
    alte_nr = db.dealers.find_one({"id": firma["dealer_id"]}, {"_id": 0, "kunden_nr": 1})["kunden_nr"]
    db.dealers.update_one({"id": firma["dealer_id"]}, {"$set": {"kunden_nr": 10002}})
    s = konten.sucher_als_chef_anlegen(firma["token"], json={"password": "Wz-Sucher-" + secrets.token_hex(6) + "!"})
    assert s.status_code == 200, s.text
    sucher_token = konten.token_direkt(s.json()["sucher_id"])
    meta_vorher = db.werkzeuge.find_one({"id": WID}, {"_id": 0})
    try:
        yield {"db": db, "chef": konten._kopf(firma["token"]), "sucher": konten._kopf(sucher_token),
               "andere": konten._kopf(andere["token"]), "firma": firma}
    finally:
        db.dealers.update_one({"id": firma["dealer_id"]}, {"$set": {"kunden_nr": alte_nr}})
        if getauscht:
            db.dealers.update_one({"id": getauscht[0]}, {"$set": {"kunden_nr": getauscht[1]}})
        if meta_vorher:
            db.werkzeuge.replace_one({"id": WID}, meta_vorher, upsert=True)


def _liste(kopf):
    r = requests.get(f"{API}/werkzeuge", headers=kopf, timeout=30)
    assert r.status_code == 200, r.text
    return r.json()["werkzeuge"]


def _download(kopf):
    return requests.get(f"{API}/werkzeuge/{WID}/download", headers=kopf, timeout=60)


def test_10_ohne_datei_sichtbar_aber_noch_nicht_ladbar(welt):
    welt["db"].werkzeuge.delete_one({"id": WID})
    liste = _liste(welt["chef"])
    assert [w["id"] for w in liste] == [WID]
    assert liste[0]["vorhanden"] is False
    r = _download(welt["chef"])
    assert r.status_code == 404
    assert "noch nicht hochgeladen" in r.json()["detail"]


def test_11_chef_und_sucher_von_10002_laden(welt):
    from storage_service import storage
    daten = b"MZ" + secrets.token_bytes(64 * 1024)
    meta = wz.hochladen(welt["db"], WID, daten, "test-" + uuid.uuid4().hex[:6], storage=storage)
    for wer in ("chef", "sucher"):
        liste = _liste(welt[wer])
        assert liste and liste[0]["vorhanden"] is True and liste[0]["version"] == meta["version"], wer
        assert liste[0]["groesse"] == len(daten)
        r = _download(welt[wer])
        assert r.status_code == 200, (wer, r.text[:200])
        assert r.content == daten
        assert 'filename="AutoSchnell-Vergleich.exe"' in r.headers["content-disposition"]
        assert r.headers["cache-control"] == "no-store"
    log = welt["db"].activity_logs.find_one({"dealer_id": welt["firma"]["dealer_id"], "action": "werkzeug_download"})
    assert log and log["ref"] == WID


def test_12_andere_firma_sieht_nichts(welt):
    assert _liste(welt["andere"]) == []
    r = _download(welt["andere"])
    assert r.status_code == 404
    assert r.json()["detail"] == "Nicht gefunden"
    r = requests.get(f"{API}/werkzeuge/gibts-nicht/download", headers=welt["chef"], timeout=30)
    assert r.status_code == 404


def test_13_betreiber_und_ohne_anmeldung(welt):
    sa = konten.super_kopf()
    assert _liste(sa) == []
    assert _download(sa).status_code == 403
    assert requests.get(f"{API}/werkzeuge", timeout=30).status_code == 401
    assert requests.get(f"{API}/werkzeuge/{WID}/download", timeout=30).status_code == 401
