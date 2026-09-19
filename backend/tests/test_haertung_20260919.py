# -*- coding: utf-8 -*-
"""Haertung 19.09.2026: Anmelde-Geheimnis und Sicherungs-Zugangsdaten.

Zwei Punkte aus der Sicherheits-Durchsicht, die der Code bisher nicht selbst
gepruefta hat:

  * `JWT_SECRET` unterschreibt JEDE Anmeldung. Geprueft wurde nur, DASS es
    gesetzt ist — "geheim123" war erlaubt und liesse sich durchprobieren.
  * Nutzt die Sicherung dieselben S3-Zugangsdaten wie der Datei-Speicher,
    kommt ein gestohlener Schluessel an die Daten UND an ihre Sicherungen.
"""
import inspect
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))


def _start(secret, app_env="production"):
    """Backend-Modul mit diesem Geheimnis laden — in EIGENEM Prozess, damit
    das echte auth-Modul dieses Tests unberuehrt bleibt."""
    # Die .env des Backends darf hier nicht mitreden — JWT_SECRET und APP_ENV
    # setzen wir ausdruecklich (gesetzte Umgebungsvariablen gewinnen gegen
    # load_dotenv). Die uebrige Umgebung wird kopiert, sonst findet Python
    # unter Windows seine eigenen Pfade nicht.
    code = ("import os, sys;"
            "sys.path.insert(0, r'%s');"
            "import warnings; warnings.simplefilter('error');"
            "import auth; print('START OK', len(auth.JWT_SECRET))" % BACKEND)
    import os as _os
    env = dict(_os.environ, JWT_SECRET=secret, APP_ENV=app_env,
               PYTHONIOENCODING="utf-8")
    r = subprocess.run([sys.executable, "-X", "utf8", "-c", code],
                       capture_output=True, text=True, env=env, timeout=120)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def test_01_kurzes_geheimnis_startet_in_produktion_nicht():
    rc, aus = _start("viel-zu-kurz")
    assert rc != 0, aus[-400:]
    assert "JWT_SECRET ist zu kurz" in aus and "openssl rand -hex 32" in aus


def test_02_langes_geheimnis_startet():
    rc, aus = _start("a" * 64)
    assert rc == 0 and "START OK 64" in aus, aus[-400:]
    # Genau an der Grenze
    rc, aus = _start("b" * 32)
    assert rc == 0, aus[-400:]


def test_03_entwicklung_warnt_nur():
    """Lokal soll niemand ausgesperrt werden — dort genuegt die Warnung."""
    rc, aus = _start("kurz", app_env="development")
    assert rc != 0 and "RuntimeWarning" in aus, \
        "mit simplefilter('error') muss die Warnung sichtbar werden"
    assert "JWT_SECRET ist zu kurz" in aus


def test_04_grenze_steht_im_code():
    import auth
    assert auth.JWT_SECRET_MIN_ZEICHEN == 32


def test_05_bereitschaft_meldet_gemeinsame_sicherungs_schluessel():
    import server
    q = inspect.getsource(server)
    stelle = q.split("offsite_noetig and not os.environ.get")[1][:400]
    assert "BACKUP_S3_ACCESS_KEY" in q
    assert "warnungen.append" in stelle, "Hinweis, kein Startverbot"
    assert "backup_eigene_zugangsdaten" in q, "auch als Feld in /ready"


def test_06_mfa_pflicht_gilt_schon_bei_der_anmeldung():
    """Gegenprobe zur Sicherheitsliste: Der zweite Faktor ist in Produktion
    nicht nur eine Empfehlung — die Anmeldung selbst weist ohne ihn ab."""
    import routes.auth as A
    q = inspect.getsource(A)
    assert "mfa_pflicht_aktiv()" in q
    anmeldung = q.split("mfa_pflicht_aktiv() and not gnadenfrist")
    assert len(anmeldung) > 1, "die Pflicht haengt nicht mehr an der Anmeldung"
    regel = inspect.getsource(A.mfa_pflicht_aktiv)
    assert 'APP_ENV' in regel and 'MFA_PFLICHT' in regel


def _compose_umgebung(dienst="backend"):
    """Die Namen, die dieser Dienst wirklich als Umgebung bekommt.

    Bewusst ueber den YAML-Baum statt per Textsuche: ein Name in einem
    Kommentar oder bei einem ANDEREN Dienst zaehlt nicht."""
    import yaml
    daten = yaml.safe_load((BACKEND.parent / "docker-compose.yml").read_text(encoding="utf-8"))
    umgebung = ((daten.get("services") or {}).get(dienst) or {}).get("environment") or []
    if isinstance(umgebung, dict):
        return set(umgebung)
    return {str(z).split("=", 1)[0].strip() for z in umgebung}


def test_07_alle_gelesenen_umgebungsvariablen_erreichen_den_container():
    """Befund 19.09.2026 (live): BACKUP_S3_ACCESS_KEY/-SECRET_KEY/-REGION
    standen in der .env, fehlten aber in docker-compose.yml — der eigene
    Sicherungs-Schluessel erreichte den Container nie, /api/ready meldete
    weiter "false". Dasselbe Muster gab es frueher bei RESEND_API_KEY.

    Nachpruefung 20.09.2026 (zwei berechtigte Einwaende): Die erste Fassung
    dieses Tests las NUR backup_mongo.py — BACKUP_SNAPSHOT_PFLICHT wird aber
    im importierten backup_bewertung.py gelesen und entging ihm deshalb
    (die Variable fehlte tatsaechlich weiter). Und sie fragte nur, ob der
    Name IRGENDWO in der Datei vorkommt: ein blosser Kommentar haette
    gereicht. Jetzt: die ganze Sicherungs-Kette als Quelle, und geprueft
    wird der `environment:`-Block des backend-Dienstes aus dem YAML-Baum."""
    import re
    dateien = ["backup_bewertung.py", "backup_service.py",
               "scripts/backup_mongo.py", "scripts/restore_mongo.py",
               "scripts/offsite_pruefen.py"]
    gelesen = set()
    for datei in dateien:
        quelle = (BACKEND / datei).read_text(encoding="utf-8")
        gelesen |= set(re.findall(r'os\.environ(?:\.get)?[\(\[]"([A-Z0-9_]+)"', quelle))
    # BACKUP_DIR/-HOUR setzt die Compose-Datei selbst; die beiden *_DIR sind
    # laut backup_mongo.py ausdruecklich "nur fuer Tests".
    pflicht = ({v for v in gelesen if v.startswith("BACKUP_")}
               - {"BACKUP_DIR", "BACKUP_HOUR",
                  "BACKUP_UPLOADS_DIR", "BACKUP_LOCAL_STORAGE_DIR"})
    assert "BACKUP_SNAPSHOT_PFLICHT" in pflicht, (
        "die Quelle mit dem Schalter wird nicht mehr gelesen — Dateiliste pruefen")
    fehlend = sorted(pflicht - _compose_umgebung())
    assert not fehlend, ("diese Variablen erreichen den Container nicht: %s" % fehlend)


def test_08_kommentar_allein_zaehlt_nicht():
    """Gegenprobe zum Einwand: Der Name muss im environment-Block stehen,
    nicht irgendwo in der Datei."""
    umgebung = _compose_umgebung()
    text = (BACKEND.parent / "docker-compose.yml").read_text(encoding="utf-8")
    # Im Kommentar erwaehnt, aber NICHT durchgereicht (Beispiel aus der Datei)
    assert "MONGO_EXTRA_ARGS" in text and "MONGO_EXTRA_ARGS" not in umgebung, (
        "Beispiel veraltet — ein nur kommentierter Name muss durchfallen")
    assert "BACKUP_SNAPSHOT_PFLICHT" in umgebung
    assert "JWT_SECRET" in umgebung and len(umgebung) > 40
