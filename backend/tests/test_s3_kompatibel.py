# -*- coding: utf-8 -*-
"""S3-kompatible Speicher mit Eigenheiten (Cloudflare R2).

Zwei Dinge brechen sonst still:
  * Seit botocore 1.36 schickt boto3 Pruefsummen-Kopfzeilen mit, die R2
    ablehnt — Hochladen schlaegt fehl.
  * `ServerSideEncryption: AES256` ist bei AWS ueblich, R2 weist die
    Kopfzeile zurueck (R2 verschluesselt selbst).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import s3_kompatibel as k  # noqa: E402

R2 = "https://1234567890abcdef.r2.cloudflarestorage.com"
AWS = "https://s3.eu-central-1.amazonaws.com"
MINIO = "http://minio.intern:9000"


def test_r2_wird_erkannt():
    assert k.ist_r2(R2)
    assert not k.ist_r2(AWS)
    assert not k.ist_r2("")
    assert not k.ist_r2(None)


def test_keine_verschluesselungs_kopfzeile_fuer_r2(monkeypatch):
    monkeypatch.delenv("S3_SSE", raising=False)
    assert k.sse_optionen(R2) == {}
    assert k.sse_optionen(AWS) == {"ServerSideEncryption": "AES256"}
    assert k.sse_optionen(MINIO) == {"ServerSideEncryption": "AES256"}


def test_verschluesselung_laesst_sich_erzwingen_und_abschalten(monkeypatch):
    monkeypatch.setenv("S3_SSE", "aes256")
    assert k.sse_optionen(R2) == {"ServerSideEncryption": "AES256"}
    monkeypatch.setenv("S3_SSE", "aus")
    assert k.sse_optionen(AWS) == {}


def test_pruefsummen_werden_fuer_r2_gebremst(monkeypatch):
    monkeypatch.delenv("S3_PRUEFSUMMEN", raising=False)
    cfg = k.client_konfiguration(R2)
    assert cfg is not None
    assert getattr(cfg, "request_checksum_calculation", None) == "when_required"
    assert getattr(cfg, "signature_version", None) == "s3v4"
    # AWS braucht die Sonderbehandlung nicht
    assert k.client_konfiguration(AWS) is None


def test_pruefsummen_schalter(monkeypatch):
    monkeypatch.setenv("S3_PRUEFSUMMEN", "immer")
    assert k.client_konfiguration(R2) is None
    monkeypatch.setenv("S3_PRUEFSUMMEN", "nur_noetig")
    assert k.client_konfiguration(AWS) is not None


def test_client_laesst_sich_bauen(monkeypatch):
    monkeypatch.delenv("S3_PRUEFSUMMEN", raising=False)
    c = k.s3_client(endpoint=R2, access_key="schluessel", secret_key="geheim",
                    region="auto")
    assert c.meta.endpoint_url == R2
    assert c.meta.region_name == "auto"


def test_client_nimmt_werte_aus_der_umgebung(monkeypatch):
    monkeypatch.setenv("S3_ENDPOINT", R2)
    monkeypatch.setenv("S3_ACCESS_KEY", "a")
    monkeypatch.setenv("S3_SECRET_KEY", "b")
    monkeypatch.setenv("S3_REGION", "auto")
    c = k.s3_client()
    assert c.meta.endpoint_url == R2


def test_speicher_nutzt_den_gemeinsamen_zugang(monkeypatch):
    """storage_service.S3Storage muss ueber s3_kompatibel gehen, sonst
    fehlen die R2-Eigenheiten beim Hochladen von Fotos."""
    monkeypatch.setenv("S3_ENDPOINT", R2)
    monkeypatch.setenv("S3_BUCKET", "autoschnell")
    monkeypatch.setenv("S3_ACCESS_KEY", "a")
    monkeypatch.setenv("S3_SECRET_KEY", "b")
    import storage_service
    speicher = storage_service.S3Storage()
    assert speicher.client.meta.endpoint_url == R2
    quelle = Path(storage_service.__file__).read_text(encoding="utf-8")
    assert "s3_kompatibel" in quelle, "S3Storage baut den Client selbst"


def test_sicherung_schickt_r2_keine_verschluesselung():
    quelle = (Path(__file__).resolve().parents[1] / "scripts" / "backup_mongo.py"
              ).read_text(encoding="utf-8")
    assert "sse_optionen" in quelle
    assert '"ServerSideEncryption": "AES256"' not in quelle


def test_produktion_weicht_nicht_still_auf_die_platte_aus(monkeypatch):
    """Ist S3/R2 konfiguriert, aber unbrauchbar, darf der Datei-Speicher in
    Produktion NICHT stillschweigend die lokale Platte nehmen — sonst lägen
    Fotos auf einem Server, den der zweite nie sieht, und die Sicherung
    erfasste sie nicht."""
    import storage_service
    monkeypatch.setenv("S3_ENDPOINT", R2)
    monkeypatch.setenv("S3_BUCKET", "autoschnell")
    monkeypatch.setenv("APP_ENV", "production")

    def kaputt():
        raise ImportError("No module named 's3_kompatibel'")
    monkeypatch.setattr(storage_service, "S3Storage", kaputt)
    with pytest.raises(RuntimeError) as e:
        storage_service._build_storage()
    assert "nicht nutzbar" in str(e.value)

    # Ausserhalb der Produktion bleibt der Rueckfall erlaubt (lokale Arbeit)
    monkeypatch.setenv("APP_ENV", "development")
    speicher = storage_service._build_storage()
    assert speicher.name == "local"


def test_skripte_finden_ihre_module_beim_direkten_start():
    """Als Skript gestartet kennt Python nur den scripts-Ordner. Fehlt die
    Einrichtung des Suchpfads, scheitert der Offsite-Upload erst mitten im
    naechtlichen Lauf mit "No module named" — genau so auf dem Server
    passiert. Geprueft wird der Start aus BEIDEN Verzeichnissen."""
    import subprocess
    import sys as _sys
    backend = Path(__file__).resolve().parents[1]
    for wo in (backend, backend / 'scripts'):
        for skript in ('backup_mongo.py', 'offsite_pruefen.py',
                       'verbindung_pruefen.py',
                       'bilder_verkleinern_nachtraeglich.py',
                       'anbieter_probe.py'):
            pfad = 'scripts/' + skript if wo == backend else skript
            e = subprocess.run([_sys.executable, pfad, '--help'], cwd=str(wo),
                               capture_output=True, text=True, timeout=120)
            ausgabe = (e.stdout or '') + (e.stderr or '')
            hinweis = skript + ' aus ' + wo.name + ': ' + ausgabe[-300:]
            assert 'No module named' not in ausgabe, hinweis
            assert e.returncode == 0, hinweis
