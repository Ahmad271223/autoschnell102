# -*- coding: utf-8 -*-
"""Runde 13, Gruppe 2 (06.09.2026) — bestaetigte Befunde.

  B4  Schutz fuer Weiterverkaufsdateien konnte per ENV fail-open geschaltet
      werden: DATEI_SIGNATUR_PFLICHT=false deaktivierte die Signaturpruefung
      fuer resale/-Fotos in server.serve_file vollstaendig — dann reichte der
      zufaellige Storage-Key, um nichtoeffentliche Fahrzeugfotos ohne Login
      abzurufen. Der Schalter wurde nirgends geprueft. Jetzt: in Produktion
      (APP_ENV=production) ist der Schalter ein Startfehler (Exit 78 ueber
      production_check.pruefe_produktion, das VOR allem anderen laeuft),
      ausserhalb von Produktion eine Warnung.

Einheitentests laufen ohne Server. HTTP-Teile brauchen das Backend auf
TEST_BASE_URL (Standardkonfiguration DATEI_SIGNATUR_PFLICHT=true) mit
SELF_SIGNUP=true; der Ende-zu-Ende-Test laedt ein echtes Foto hoch und
belegt, dass der Schluessel allein NICHT genuegt.
"""
import base64
import inspect
import io
import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import production_check  # noqa: E402

BASE = (os.environ.get("TEST_BASE_URL") or "http://localhost:8001").rstrip("/")
API = f"{BASE}/api"
MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
DB_NAME = os.environ.get("DB_NAME") or "autoschnell"
SUF = uuid.uuid4().hex[:8]
PW = "RundeDreizehn123!"
MAIL = "e2etest-mail.de"
BACKEND = Path(__file__).resolve().parents[1]


def _db():
    from pymongo import MongoClient
    return MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)[DB_NAME]


def _q(url):
    return parse_qs(urlparse(url).query)


def _abo(dealer_id, user_id, **extra):
    doc = {
        "id": str(uuid.uuid4()), "dealer_id": dealer_id,
        "subject_user_id": user_id, "plan": "monthly", "status": "active",
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat()}
    doc.update(extra)
    _db().subscriptions.insert_one(doc)
    return doc


# =====================================================================
#                          Einheitentests
# =====================================================================
class _Protokoll:
    """Ersatz fuer den Logger: sammelt (stufe, text) statt zu schreiben."""

    def __init__(self):
        self.eintraege = []

    def _log(self, stufe):
        def _f(msg, *args, **_kw):
            self.eintraege.append((stufe, (msg % args) if args else str(msg)))
        return _f

    def __getattr__(self, name):
        if name in ("error", "warning", "info", "debug", "critical", "exception"):
            return self._log(name)
        raise AttributeError(name)

    def texte(self, stufe):
        return [t for s, t in self.eintraege if s == stufe]


# Eine sonst GUELTIGE Produktionsumgebung — jeder Eintrag entspricht einer
# Pflichtpruefung in pruefe_produktion. Nur DATEI_SIGNATUR_PFLICHT wird je
# Test variiert; so faellt ein Abbruch eindeutig auf B4 zurueck.
_PROD_UMGEBUNG = {
    "APP_ENV": "production",
    "JWT_SECRET": uuid.uuid4().hex + uuid.uuid4().hex,       # 64 Hex-Zeichen
    "ADMIN_PASSWORD": "Runde13-Betreiber-Kennwort!",
    "SUPER_ADMIN_PASSWORD": "",
    "FRONTEND_URL": "https://app.example.de",
    "CORS_ORIGINS": "https://app.example.de",
    "MONGO_URL": "mongodb://u:p@db:27017/autoschnell?authSource=admin&maxPoolSize=20",
    "MOCK_PROVIDER_FETCH": "false",
    "VERTRAG_AUFBEWAHRUNG_TAGE": "90",
    "SNAPSHOT_RETENTION_DAYS": "60",
    "RESEND_API_KEY": "re_r13_test",
    "MAIL_FROM": "AutoSchnell <vertrag@example.de>",
    "WEB_CONCURRENCY": "1",
    "SNAPSHOT_CONCURRENCY": "1",
    "SELF_SIGNUP": "false",
    "AUTO_DATEN_SCHAEDEN_FREITEXT": "false",
    "VERTRAG_LOESCHUNG_AKTIV": "false",
}
# Was NICHT gesetzt sein darf (S3 nur ganz oder gar nicht, Stripe beide
# oder keins, SMTP wuerde Resend ueberlagern).
_PROD_LOESCHEN = ("S3_ENDPOINT", "S3_BUCKET", "S3_ACCESS_KEY", "S3_SECRET_KEY",
                  "STRIPE_API_KEY", "STRIPE_WEBHOOK_SECRET", "DATEI_SIGNATUR_PFLICHT",
                  "SMTP_HOST", "SMTP_USER", "SMTP_PASS", "SMTP_FROM")


@pytest.fixture
def prod_umgebung(monkeypatch, tmp_path):
    for k, v in _PROD_UMGEBUNG.items():
        monkeypatch.setenv(k, v)
    for k in _PROD_LOESCHEN:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path / "backups"))
    return monkeypatch


@pytest.mark.parametrize("wert", ["false", "0", "no", " False "])
def test_b4_produktion_bricht_bei_abgeschalteter_signaturpflicht_ab(prod_umgebung, wert):
    prod_umgebung.setenv("DATEI_SIGNATUR_PFLICHT", wert)
    log = _Protokoll()
    with pytest.raises(SystemExit) as exc:
        production_check.pruefe_produktion(log)
    assert exc.value.code == 78, "Konfigurationsfehler muss mit EX_CONFIG (78) enden"
    fehler = log.texte("error")
    assert any("DATEI_SIGNATUR_PFLICHT" in t for t in fehler), fehler
    assert any("resale/" in t for t in fehler if "DATEI_SIGNATUR_PFLICHT" in t), \
        "die Meldung soll sagen, WAS offen laege (resale/-Fotos)"
    # Der Abbruch geht auf B4 zurueck, nicht auf eine andere Pflichtpruefung.
    assert all("DATEI_SIGNATUR_PFLICHT" in t or t.startswith("Start ABGEBROCHEN") for t in fehler), fehler


@pytest.mark.parametrize("wert", ["true", None, "1", "yes"])
def test_b4_produktion_startet_mit_signaturpflicht(prod_umgebung, wert):
    if wert is None:
        prod_umgebung.delenv("DATEI_SIGNATUR_PFLICHT", raising=False)
    else:
        prod_umgebung.setenv("DATEI_SIGNATUR_PFLICHT", wert)
    log = _Protokoll()
    production_check.pruefe_produktion(log)          # kein SystemExit
    assert log.texte("error") == [], log.texte("error")
    assert not any("DATEI_SIGNATUR_PFLICHT" in t for t in log.texte("warning")), \
        log.texte("warning")
    assert any("alle Pflichtwerte" in t for t in log.texte("info")), log.eintraege


@pytest.mark.parametrize("app_env", [None, "development", "test"])
def test_b4_ausserhalb_produktion_nur_warnung(prod_umgebung, app_env):
    if app_env is None:
        prod_umgebung.delenv("APP_ENV", raising=False)
    else:
        prod_umgebung.setenv("APP_ENV", app_env)
    prod_umgebung.setenv("DATEI_SIGNATUR_PFLICHT", "false")
    log = _Protokoll()
    production_check.pruefe_produktion(log)          # kein SystemExit
    assert log.texte("error") == [], log.texte("error")
    assert any("DATEI_SIGNATUR_PFLICHT" in t for t in log.texte("warning")), \
        "ausserhalb von Produktion muss der Schalter wenigstens gemeldet werden"


def test_b4_quelle_pruefung_und_laufzeit_lesen_den_schalter_gleich():
    """Der Produktions-Check muss GENAU die Werte ablehnen, die server.py
    als 'aus' versteht — sonst laesst sich der Check mit einer Schreibweise
    umgehen, die die Laufzeit trotzdem abschaltet."""
    quelle = inspect.getsource(production_check.pruefe_produktion)
    assert "DATEI_SIGNATUR_PFLICHT" in quelle
    assert 'os.environ.get("DATEI_SIGNATUR_PFLICHT", "true").strip().lower() in ("0", "false", "no")' \
        in quelle
    server = (BACKEND / "server.py").read_text(encoding="utf-8")
    if "_DATEI_SIGNATUR_PFLICHT" in server:
        start = server.index("_DATEI_SIGNATUR_PFLICHT =")
        zuweisung = server[start:start + 300]
        assert 'not in ("0", "false", "no")' in zuweisung, \
            "server.py wertet den Schalter anders aus als production_check"
    # Der Fix liegt VOR den Betriebsvoraussetzungen, also im Block der
    # harten Sicherheitswerte (JWT, Admin-Passwort, CORS, Mongo, Mock).
    assert quelle.index("DATEI_SIGNATUR_PFLICHT") < quelle.index("Runde 5: Betriebsvoraussetzungen")


def test_b4_env_vorlage_nennt_die_pflicht():
    vorlage = (BACKEND.parent / ".env.example").read_text(encoding="utf-8")
    assert "DATEI_SIGNATUR_PFLICHT=true" in vorlage
    i = vorlage.index("DATEI_SIGNATUR_PFLICHT=true")
    kommentar = vorlage[max(0, i - 600):i]
    assert "bricht den Start ab" in kommentar, \
        ".env.example soll sagen, dass false in Produktion den Start abbricht"


def test_b4_dateien_signatur_regeln(monkeypatch):
    """Gegenprobe der Signaturlogik, die der Schalter aushebelte."""
    monkeypatch.setenv("JWT_SECRET", "r13-" + uuid.uuid4().hex + uuid.uuid4().hex)
    import dateien
    key = f"resale/d_{SUF}/{uuid.uuid4().hex}.jpg"
    assert dateien.signatur_noetig(key) is True
    assert dateien.signatur_noetig("logo/firma.png") is False
    assert dateien.signatur_noetig("protocol/x.pdf") is False
    assert dateien.signatur_noetig("pickup/x/y.jpg") is False
    url = dateien.signierte_datei_url(key)
    q = _q(url)
    assert urlparse(url).path == f"/api/files/{key}"
    assert "exp" in q and "sig" in q, url
    exp, sig = q["exp"][0], q["sig"][0]
    assert dateien.signatur_gueltig(key, exp, sig) is True
    assert dateien.signatur_gueltig(key, None, None) is False
    assert dateien.signatur_gueltig(key, "abc", sig) is False
    assert dateien.signatur_gueltig(key, exp, "0" * len(sig)) is False
    assert dateien.signatur_gueltig(key, int(time.time()) - 5, sig) is False
    assert dateien.signatur_gueltig(key, int(exp) + 999999, sig) is False, \
        "Ablauf verlaengern ohne neue Signatur darf nicht gehen"
    assert dateien.signatur_gueltig(key.replace(".jpg", "-2.jpg"), exp, sig) is False, \
        "Signatur gilt nur fuer genau diesen Schluessel"
    assert dateien.signierte_datei_url("logo/firma.png") == "/api/files/logo/firma.png"
    # anderes Geheimnis -> Signatur wertlos (Load Balancer: gleiches JWT_SECRET!)
    monkeypatch.setenv("JWT_SECRET", "r13-anderes-" + uuid.uuid4().hex + uuid.uuid4().hex)
    assert dateien.signatur_gueltig(key, exp, sig) is False


# =====================================================================
#                          HTTP-Tests
# =====================================================================
def test_b4_http_resale_ohne_signatur_403():
    """Standardkonfiguration (Pflicht=true): der Schluessel allein genuegt
    nicht — 403 kommt VOR dem Storage-Zugriff, unabhaengig davon, ob die
    Datei existiert."""
    key = f"resale/r13_{SUF}/{uuid.uuid4().hex}.jpg"
    r = requests.get(f"{API}/files/{key}", timeout=30)
    assert r.status_code == 403, f"{r.status_code}: {r.text[:200]}"
    assert "abgelaufen" in r.text
    r = requests.get(f"{API}/files/{key}",
                     params={"exp": int(time.time()) - 60, "sig": "0" * 40}, timeout=30)
    assert r.status_code == 403, f"abgelaufene Signatur: {r.status_code}"
    r = requests.get(f"{API}/files/{key}", params={"exp": "abc", "sig": "x"}, timeout=30)
    assert r.status_code == 403, f"unsinnige Signatur: {r.status_code}"


def test_b4_http_logo_oeffentlich_protokolle_gesperrt():
    # Logos brauchen keine Signatur: fehlende Datei -> 404, NIE 403
    r = requests.get(f"{API}/files/logo/r13_{SUF}.png", timeout=30)
    assert r.status_code == 404, f"{r.status_code}: {r.text[:200]}"
    # protocol/ und pickup/ bleiben auch mit "Signatur" gesperrt (404)
    for pfad in (f"protocol/r13_{SUF}.pdf", f"pickup/r13_{SUF}/foto.jpg"):
        r = requests.get(f"{API}/files/{pfad}",
                         params={"exp": int(time.time()) + 600, "sig": "0" * 40}, timeout=30)
        assert r.status_code == 404, (pfad, r.status_code, r.text[:200])


def _jpeg_b64() -> str:
    """Kleines echtes JPEG (Magic Bytes + Verkleinerung wollen ein Bild)."""
    from PIL import Image
    puffer = io.BytesIO()
    Image.new("RGB", (64, 48), (200, 30, 30)).save(puffer, format="JPEG", quality=80)
    return "data:image/jpeg;base64," + base64.b64encode(puffer.getvalue()).decode("ascii")


@pytest.fixture(scope="module")
def welt():
    """Firma mit Chef (Abo) und einem Fahrzeug im Bestand — genug fuer einen
    Inserats-Entwurf mit Foto-Upload."""
    dbx = _db()
    r = requests.post(f"{API}/auth/register", json={
        "email": f"r13g2_chef_{SUF}@{MAIL}", "password": PW,
        "company_name": f"Runde13g2 {SUF}", "contact_person": "R E", "phone": "0511 13"}, timeout=30)
    assert r.status_code == 200, f"Backend braucht SELF_SIGNUP=true: {r.text[:200]}"
    C = {"Authorization": f"Bearer {r.json()['token']}"}
    chef = requests.get(f"{API}/auth/me", headers=C, timeout=30).json()["user"]
    _abo(chef["dealer_id"], chef["id"])
    vid = f"v_r13g2_{SUF}"
    dbx.vehicles.insert_one({
        "id": vid, "dealer_id": chef["dealer_id"], "mobile_ad_id": f"r13g2{SUF}",
        "data": {"make_label": "BMW", "model_label": "M3", "mileage": 50000},
        "lifecycle": "bestand", "status": "bestand",
        "created_at": datetime.now(timezone.utc).isoformat()})
    z = {"C": C, "chef": chef, "dealer_id": chef["dealer_id"], "vid": vid}
    yield z
    for coll in ("subscriptions", "activity_logs", "vehicles", "resale_listings",
                 "storage_delete_retry", "plan_requests", "password_resets"):
        dbx[coll].delete_many({"dealer_id": z["dealer_id"]})
    dbx.users.delete_many({"email": {"$regex": f"_{SUF}@"}})
    dbx.dealers.delete_many({"id": z["dealer_id"]})


def test_b4_http_echter_foto_link_nur_mit_signatur(welt):
    """Ende zu Ende: das Backend erzeugt beim Upload einen signierten Link,
    der funktioniert; derselbe Schluessel ohne/mit manipulierter Signatur
    wird abgewiesen. Genau das hebelte DATEI_SIGNATUR_PFLICHT=false aus."""
    C = welt["C"]
    r = requests.post(f"{API}/resale/draft/{welt['vid']}", headers=C, timeout=30)
    assert r.status_code == 200, r.text[:300]
    lid = r.json()["id"]
    r = requests.post(f"{API}/resale/{lid}/photos", headers=C,
                      json={"photos_b64": [_jpeg_b64()]}, timeout=60)
    assert r.status_code == 200, r.text[:300]
    url = r.json()["uploaded"][0]
    q = _q(url)
    pfad = urlparse(url).path
    assert pfad.startswith("/api/files/resale/"), url
    assert "exp" in q and "sig" in q, f"Upload liefert unsignierten Link: {url}"
    key = pfad[len("/api/files/"):]

    r = requests.get(f"{BASE}{url}", timeout=30)
    assert r.status_code == 200, f"signierter Link: {r.status_code} {r.text[:200]}"
    assert r.content[:3] == b"\xff\xd8\xff", "JPEG erwartet"
    assert r.headers.get("Cache-Control", "").startswith("private"), r.headers.get("Cache-Control")

    # Der zufaellige Schluessel allein reicht NICHT (Kern von B4)
    r = requests.get(f"{BASE}{pfad}", timeout=30)
    assert r.status_code == 403, f"ohne Signatur: {r.status_code} {r.text[:200]}"
    sig = q["sig"][0]
    kaputt = sig[:-1] + ("0" if sig[-1] != "0" else "1")
    r = requests.get(f"{BASE}{pfad}", params={"exp": q["exp"][0], "sig": kaputt}, timeout=30)
    assert r.status_code == 403, f"manipulierte Signatur: {r.status_code}"
    r = requests.get(f"{BASE}{pfad}", params={"exp": int(q["exp"][0]) + 999999, "sig": sig}, timeout=30)
    assert r.status_code == 403, f"verlaengerter Ablauf: {r.status_code}"

    # Aufraeumen ueber die API (loescht die Datei im Storage)
    r = requests.post(f"{API}/resale/{lid}/photos/remove", headers=C, json={"key": key}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    assert key not in r.json()["uploaded_keys"]
    r = requests.get(f"{BASE}{url}", timeout=30)
    assert r.status_code == 404, f"nach dem Entfernen: {r.status_code}"
