# -*- coding: utf-8 -*-
"""Live-faehig (19.09.2026, Entscheidung Ahmad "mach das Ding live-faehig").

  A  Sicherung ohne Platten-Spiegel: Dateien wandern Speicher-zu-Speicher in
     den Sicherungs-Bucket (Papierkorb 30 Tage). Vorher: 15 x Bucket-Groesse
     auf einer 160-GB-Platte.
  B  Die fuenf sichtbaren Maengel aus der Video-Pruefung vom 17.09.:
     Marke, Platzhalter im Versand, Abo-Text, Abhol-Check des Fahrers.
"""
import inspect
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import backup_mongo as B  # noqa: E402

FRONT = BACKEND.parent / "frontend" / "src"


def _lies(*teile):
    return (FRONT.joinpath(*teile)).read_text(encoding="utf-8")


# ------------------------------------------------------------------ A
def test_a1_standard_ist_bucket_sobald_ein_sicherungs_bucket_da_ist(monkeypatch):
    monkeypatch.delenv("BACKUP_DATEIEN", raising=False)
    monkeypatch.setenv("BACKUP_DATEIEN_BUCKET", "")
    monkeypatch.setenv("BACKUP_S3_BUCKET", "")
    assert B.dateien_modus() == "spiegel"
    monkeypatch.setenv("BACKUP_S3_BUCKET", "sicherung")
    assert B.dateien_modus() == "bucket"
    monkeypatch.setenv("BACKUP_DATEIEN", "aus")
    assert B.dateien_modus() == "aus"
    monkeypatch.setenv("BACKUP_DATEIEN", "quatsch")
    assert B.dateien_modus() == "bucket", "unbekannter Wert faellt auf den Standard"
    assert B.dateien_ziel() == ("sicherung", "dateien/")
    monkeypatch.setenv("BACKUP_DATEIEN_PREFIX", "kopie")
    assert B.dateien_ziel()[1] == "kopie/"


class _Body:
    def __init__(self, daten):
        self.daten = daten

    def read(self, n=-1):
        d, self.daten = self.daten, b""
        return d


class _FakeBucket:
    """Ein S3-Client fuer genau einen Bucket: list/get/upload/head/copy/delete."""

    def __init__(self, objekte=None, meta=None):
        self.objekte = dict(objekte or {})       # key -> bytes
        self.meta = dict(meta or {})             # key -> Metadata
        self.zeiten = {}
        self.geloescht, self.kopiert = [], []

    def get_paginator(self, _name):
        fake = self

        class P:
            def paginate(self, Bucket, Prefix=""):
                inhalt = [{"Key": k, "Size": len(v),
                           "LastModified": fake.zeiten.get(k, datetime(2026, 9, 1, tzinfo=timezone.utc))}
                          for k, v in sorted(fake.objekte.items()) if k.startswith(Prefix)]
                return [{"Contents": inhalt}]
        return P()

    def get_object(self, Bucket, Key):
        return {"Body": _Body(self.objekte[Key])}

    def upload_fileobj(self, body, Bucket, Key, ExtraArgs=None):
        self.objekte[Key] = body.read()
        self.zeiten[Key] = datetime.now(timezone.utc)
        self.kopiert.append(Key)

    def head_object(self, Bucket, Key):
        return {"ContentLength": len(self.objekte[Key]), "Metadata": dict(self.meta.get(Key, {}))}

    def copy_object(self, Bucket, Key, CopySource, Metadata=None, MetadataDirective=None, **_):
        self.meta[Key] = dict(Metadata or {})

    def delete_object(self, Bucket, Key):
        self.objekte.pop(Key, None)
        self.geloescht.append(Key)


@pytest.fixture
def buckets(monkeypatch, tmp_path):
    quelle = _FakeBucket({"protocol/a.pdf": b"A" * 10, "pickup/b.jpg": b"B" * 20})
    ziel = _FakeBucket()
    monkeypatch.setenv("S3_BUCKET", "dateien")
    monkeypatch.setenv("S3_ENDPOINT", "https://x.r2.cloudflarestorage.com")
    monkeypatch.setenv("BACKUP_S3_BUCKET", "sicherung")
    monkeypatch.delenv("BACKUP_DATEIEN_BUCKET", raising=False)
    monkeypatch.delenv("BACKUP_DATEIEN_PREFIX", raising=False)
    monkeypatch.setenv("BACKUP_DATEIEN_AUFBEWAHRUNG_TAGE", "30")
    monkeypatch.setattr(B, "_s3_client", lambda *a, **k: quelle)
    monkeypatch.setattr(B, "_backup_s3_client", lambda: ziel)
    return quelle, ziel, tmp_path / "backup.log"


def test_a2_dateien_wandern_in_den_sicherungs_bucket_ohne_platte(buckets, tmp_path):
    quelle, ziel, log = buckets
    stand = B.dateien_in_bucket_sichern(log)
    assert stand["modus"] == "bucket" and stand["bucket"] == "sicherung"
    assert stand["kopiert"] == 2 and stand["bytes_kopiert"] == 30 and stand["fehler"] == 0
    assert ziel.objekte == {"dateien/protocol/a.pdf": b"A" * 10, "dateien/pickup/b.jpg": b"B" * 20}
    # Nichts davon auf der Platte
    assert not list(tmp_path.rglob("*.pdf")) and not list(tmp_path.rglob("*.jpg"))
    # Zweiter Lauf: alles unveraendert, nichts kopiert
    stand2 = B.dateien_in_bucket_sichern(log)
    assert stand2["kopiert"] == 0 and stand2["unveraendert"] == 2


def test_a3_papierkorb_erst_vormerken_dann_nach_der_frist_entfernen(buckets):
    quelle, ziel, log = buckets
    B.dateien_in_bucket_sichern(log)
    del quelle.objekte["pickup/b.jpg"]           # im Datei-Speicher geloescht
    stand = B.dateien_in_bucket_sichern(log)
    assert stand["vorgemerkt"] == 1 and stand["entfernt"] == 0
    assert "dateien/pickup/b.jpg" in ziel.objekte, "Papierkorb: bleibt erst einmal"
    assert ziel.meta["dateien/pickup/b.jpg"]["geloescht-am"]
    # Frist noch nicht um -> bleibt
    stand = B.dateien_in_bucket_sichern(log)
    assert stand["entfernt"] == 0 and "dateien/pickup/b.jpg" in ziel.objekte
    # Frist um -> weg
    alt = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
    ziel.meta["dateien/pickup/b.jpg"]["geloescht-am"] = alt
    stand = B.dateien_in_bucket_sichern(log)
    assert stand["entfernt"] == 1 and "dateien/pickup/b.jpg" not in ziel.objekte
    assert "dateien/protocol/a.pdf" in ziel.objekte


def test_a4_sicherungs_bucket_darf_nicht_der_datei_speicher_sein(buckets, monkeypatch):
    _q, _z, log = buckets
    monkeypatch.setenv("BACKUP_S3_BUCKET", "dateien")
    with pytest.raises(RuntimeError):
        B.dateien_in_bucket_sichern(log)
    monkeypatch.setenv("BACKUP_S3_BUCKET", "")
    with pytest.raises(RuntimeError):
        B.dateien_in_bucket_sichern(log)


def test_a5_einzelner_fehler_macht_das_backup_unvollstaendig_nicht_kaputt(buckets):
    quelle, ziel, log = buckets

    def kaputt(Bucket, Key):
        if Key.endswith("b.jpg"):
            raise RuntimeError("AccessDenied")
        return {"Body": _Body(quelle.objekte[Key])}
    quelle.get_object = kaputt
    stand = B.dateien_in_bucket_sichern(log)
    assert stand["kopiert"] == 1 and stand["fehler"] == 1
    assert "AccessDenied" in log.read_text(encoding="utf-8")


def test_a6_backup_lauf_nutzt_den_neuen_weg_und_schreibt_es_ins_manifest():
    q = inspect.getsource(B.backup_erstellen)
    assert 'modus == "spiegel"' in q and 'modus == "bucket"' in q
    assert 'manifest["dateien_kopie"] = dateien_kopie' in q
    # Der alte Spiegel laeuft NUR noch im Modus spiegel
    assert q.count("spiegle_s3(") == 1
    assert (BACKEND / "scripts" / "dateien_zurueckkopieren.py").is_file()


def test_a7_konfiguration_ueberall_bekannt():
    wurzel = BACKEND.parent
    for datei in (".env.example", "docker-compose.yml", "DEPLOYMENT.md"):
        assert "BACKUP_DATEIEN" in (wurzel / datei).read_text(encoding="utf-8"), datei


# ------------------------------------------------------------------ B
def test_b1_marke_heisst_ueberall_autoschnell():
    for p in FRONT.rglob("*.jsx"):
        text = p.read_text(encoding="utf-8")
        assert "AUTOHANDEL" not in text and "Autohandel SaaS" not in text, p


def test_b2_versand_fuellt_alle_beworbenen_platzhalter():
    beworben = ["{kunde_name}", "{fahrzeug}", "{marke}", "{modell}",
                "{abholdatum}", "{händler_name}", "{telefon}", "{email}"]
    einstellungen = _lies("pages", "app", "Einstellungen.jsx")
    versand = _lies("components", "SendDialog.jsx")
    for ph in beworben:
        assert ph in einstellungen, f"{ph} nicht mehr beworben?"
        assert f'"{ph}":' in versand, f"{ph} wird im Versand nicht ersetzt"


def test_b3_abo_seite_verspricht_keine_kostenlosen_vertraege_mehr():
    seite = _lies("pages", "Subscription.jsx")
    assert "Kaufverträge, Versand, Terminplaner, Bestand und" not in seite
    assert "Kaufverträge" in seite and "Sucher-Zugang" in seite


def test_b4_fahrer_kann_den_abhol_check_nach_dem_protokoll_nachtragen():
    import routes.drivers as D
    q = inspect.getsource(D.driver_appointments)
    assert '"bericht_vorhanden"' in q and '"status_changed_at"' in q
    app = _lies("pages", "driver", "DriverDashboard.jsx")
    assert "abholcheck-nachtragen-" in app and "abholCheckNoch(a)" in app
    assert "24 * 60 * 60 * 1000" in app, "dieselbe 24-h-Frist wie der Server"
