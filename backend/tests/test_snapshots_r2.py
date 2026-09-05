# -*- coding: utf-8 -*-
"""Beweis-Snapshots liegen im Objektspeicher (Umbau fuer zwei Server, 09/2026).

Bis dahin lagen sie nur auf der Platte des einen Servers. Ein zweiter
Anwendungsserver haette sie nicht gesehen, und bei Verlust des Servers gab
es sie nur in der Sicherung. Jetzt gilt: sobald S3_ENDPOINT und S3_BUCKET
gesetzt sind, gehen neue Snapshots in denselben Eimer wie die Fotos; alte,
noch lokale Dateien werden beim Lesen weiterhin gefunden.
"""
import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class _FakeSpeicher:
    """Nachbau von storage_service.storage: merkt sich, was gespeichert wurde."""
    name = "s3"

    def __init__(self):
        self.daten = {}

    def save(self, key, data):
        self.daten[key] = data
        return key

    def load(self, key):
        if key not in self.daten:
            raise KeyError(key)
        return self.daten[key]

    def delete(self, key):
        self.daten.pop(key, None)
        return True


def _modul(monkeypatch, *, s3: bool, tmp_path):
    if s3:
        monkeypatch.setenv("S3_ENDPOINT", "https://x.r2.cloudflarestorage.com")
        monkeypatch.setenv("S3_BUCKET", "eimer")
    else:
        monkeypatch.delenv("S3_ENDPOINT", raising=False)
        monkeypatch.delenv("S3_BUCKET", raising=False)
    monkeypatch.delenv("EMERGENT_LLM_KEY", raising=False)
    import snapshot_service
    sn = importlib.reload(snapshot_service)
    monkeypatch.setattr(sn, "_LOCAL_STORAGE", tmp_path)
    return sn


@pytest.fixture(autouse=True)
def _zuruecksetzen():
    yield
    import snapshot_service
    importlib.reload(snapshot_service)


def test_ohne_s3_bleibt_es_lokal(monkeypatch, tmp_path):
    sn = _modul(monkeypatch, s3=False, tmp_path=tmp_path)
    assert sn.speicherort() == "lokal"
    sn._put_object("autohandel/snapshots/a/b.jpg", b"jpg", "image/jpeg")
    assert (tmp_path / "autohandel" / "snapshots" / "a" / "b.jpg").read_bytes() == b"jpg"


def test_mit_s3_geht_alles_in_den_objektspeicher(monkeypatch, tmp_path):
    sn = _modul(monkeypatch, s3=True, tmp_path=tmp_path)
    assert sn.speicherort() == "s3"
    fake = _FakeSpeicher()
    monkeypatch.setattr(sn, "_s3_speicher", lambda: fake)

    sn._put_object("autohandel/snapshots/a/b.jpg", b"jpg", "image/jpeg")
    sn._put_object("autohandel/snapshots/a/b.pdf", b"%PDF", "application/pdf")
    assert set(fake.daten) == {"autohandel/snapshots/a/b.jpg", "autohandel/snapshots/a/b.pdf"}
    assert not list(tmp_path.rglob("*")), "es wurde trotzdem lokal geschrieben"

    daten, ct = sn.get_object("autohandel/snapshots/a/b.pdf")
    assert daten == b"%PDF" and ct == "application/pdf"
    daten, ct = sn.get_object("autohandel/snapshots/a/b.jpg")
    assert daten == b"jpg" and ct == "image/jpeg"

    assert sn.delete_object("autohandel/snapshots/a/b.jpg") is True
    assert "autohandel/snapshots/a/b.jpg" not in fake.daten


def test_alte_lokale_datei_wird_weiterhin_gefunden(monkeypatch, tmp_path):
    """Uebergang: Dateien von vor dem Umbau liegen noch auf der Platte."""
    sn = _modul(monkeypatch, s3=True, tmp_path=tmp_path)
    fake = _FakeSpeicher()
    monkeypatch.setattr(sn, "_s3_speicher", lambda: fake)
    alt = tmp_path / "autohandel" / "snapshots" / "alt" / "x.jpg"
    alt.parent.mkdir(parents=True)
    alt.write_bytes(b"alt")
    daten, ct = sn.get_object("autohandel/snapshots/alt/x.jpg")
    assert daten == b"alt" and ct == "image/jpeg"
    # Loeschen raeumt auch die lokale Altkopie weg.
    assert sn.delete_object("autohandel/snapshots/alt/x.jpg") is True
    assert not alt.exists()


def test_fehlende_datei_ist_ein_klarer_fehler(monkeypatch, tmp_path):
    sn = _modul(monkeypatch, s3=True, tmp_path=tmp_path)
    monkeypatch.setattr(sn, "_s3_speicher", lambda: _FakeSpeicher())
    with pytest.raises(FileNotFoundError):
        sn.get_object("autohandel/snapshots/nix/da.jpg")


def test_ausbruch_aus_dem_ordner_bleibt_verboten(monkeypatch, tmp_path):
    sn = _modul(monkeypatch, s3=True, tmp_path=tmp_path)
    fake = _FakeSpeicher()
    monkeypatch.setattr(sn, "_s3_speicher", lambda: fake)
    with pytest.raises(ValueError):
        sn.get_object("../../etc/passwd")


def test_snapshot_pfade_passen_zu_den_schluesselregeln():
    """Der Objektspeicher laesst nur bestimmte Schluessel zu. Ein echter
    Snapshot-Pfad muss durchkommen, sonst scheitert jeder Upload."""
    import storage_service as st
    st._validate_key("autohandel/snapshots/0601cc0c-6c24-414d-a072-b6624712262e/"
                     "c65ac34a-a51c-40f7-b626-1566d0b547db-20260803T201633Z.pdf")


def test_r2_loeschung_meldet_fehlschlag_ehrlich(monkeypatch, tmp_path):
    """Sonst gilt eine Datei als geloescht, die noch da ist — und die
    Nachholung greift nie."""
    sn = _modul(monkeypatch, s3=True, tmp_path=tmp_path)

    class _Kaputt(_FakeSpeicher):
        def delete(self, key):
            raise RuntimeError("R2 weg")
    monkeypatch.setattr(sn, "_s3_speicher", lambda: _Kaputt())
    assert sn.delete_object("autohandel/snapshots/a/b.jpg") is False
