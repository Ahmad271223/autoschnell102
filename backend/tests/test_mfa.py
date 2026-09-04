# -*- coding: utf-8 -*-
"""Zwei-Faktor-Anmeldung (TOTP) fuer Admin-/Super-Admin-Konten — Abo-Audit
09/2026 ("MFA fuer Super-Admins").

- Einrichten -> Aktivieren mit gueltigem Code -> 8 Wiederherstellungscodes
- Login liefert danach nur ein Zwischen-Token (kein Sitzungs-Token);
  Zwischen-Token ist als Bearer wertlos
- falscher Code 401, richtiger Code -> Sitzung; derselbe Code kein 2. Mal
- Wiederherstellungscode funktioniert genau einmal
- 5 Fehlversuche -> 15 Minuten Sperre
- Abschalten nur mit Code; Super-Admin kann 2FA eines Admins zuruecksetzen;
  Geheimnisse tauchen in keiner API-Antwort auf

Braucht laufendes Backend (TEST_BASE_URL) + Mongo.
"""
import os
import sys
import time
import uuid
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BASE = (os.environ.get("TEST_BASE_URL") or "http://localhost:8001").rstrip("/")
API = f"{BASE}/api"
MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
DB_NAME = os.environ.get("DB_NAME") or "autoschnell"
SUF = uuid.uuid4().hex[:8]
PW = "MfaTest123!x"
MAIL = "e2etest-mail.de"


def _db():
    from pymongo import MongoClient
    return MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)[DB_NAME]


def _admin_anlegen(mail, super_admin):
    import bcrypt
    _db().users.insert_one({
        "id": f"mfa_{uuid.uuid4().hex[:10]}", "email": mail, "role": "admin", "active": True,
        "dealer_id": None, "is_super_admin": super_admin,
        "password_hash": bcrypt.hashpw(PW.encode(), bcrypt.gensalt()).decode(),
        "created_at": "2026-01-01T00:00:00+00:00"})


def _frischer_code(secret, user_id):
    """Naechster noch nicht benutzter TOTP-Code. Der Replay-Schutz lehnt jeden
    Zaehler <= letzter_zaehler ab — die Tests laufen schneller als ein
    30-s-Fenster, deshalb notfalls aufs naechste Fenster warten."""
    import mfa
    doc = _db().users.find_one({"id": user_id}, {"mfa.letzter_zaehler": 1}) or {}
    letzter = int((doc.get("mfa") or {}).get("letzter_zaehler", -1))
    z = int(time.time() // 30) + 1          # Toleranz +1: naechster Zaehler gilt schon
    if z <= letzter:
        time.sleep(max(0.0, letzter * 30 - time.time()) + 0.3)
        z = letzter + 1
    return mfa.totp(secret, z)


def _login(mail):
    return requests.post(f"{API}/auth/login", json={"email": mail, "password": PW}, timeout=30)


def _hdr(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def welt():
    z = {}
    yield z
    _db().users.delete_many({"email": {"$regex": f"_{SUF}@"}})


def test_00_aufbau(welt):
    _admin_anlegen(f"mfa_super_{SUF}@{MAIL}", True)
    _admin_anlegen(f"mfa_admin_{SUF}@{MAIL}", False)
    r = _login(f"mfa_super_{SUF}@{MAIL}")
    assert r.status_code == 200 and r.json().get("token"), r.text[:200]
    welt["S"] = _hdr(r.json()["token"])
    welt["super_id"] = r.json()["user"]["id"]
    r = _login(f"mfa_admin_{SUF}@{MAIL}")
    welt["A"] = _hdr(r.json()["token"])
    welt["admin_id"] = r.json()["user"]["id"]


def test_01_einrichten_und_aktivieren(welt):
    import mfa
    S = welt["S"]
    st = requests.get(f"{API}/admin/me/mfa", headers=S, timeout=30).json()
    assert st["aktiv"] is False
    # Aktivieren ohne Einrichten -> 400
    assert requests.post(f"{API}/admin/me/mfa/aktivieren", headers=S, json={"code": "000000"}, timeout=30).status_code == 400
    r = requests.post(f"{API}/admin/me/mfa/einrichten", headers=S, timeout=30)
    assert r.status_code == 200, r.text[:200]
    secret = r.json()["secret"]
    assert r.json()["otpauth_uri"].startswith("otpauth://totp/") and secret in r.json()["otpauth_uri"]
    welt["secret"] = secret
    # falscher Code aktiviert nicht
    assert requests.post(f"{API}/admin/me/mfa/aktivieren", headers=S, json={"code": "000000"}, timeout=30).status_code == 400
    r = requests.post(f"{API}/admin/me/mfa/aktivieren", headers=S, json={"code": mfa.totp(secret)}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    codes = r.json()["wiederherstellungscodes"]
    assert len(codes) == 8 and all("-" in c for c in codes)
    welt["codes"] = codes
    st = requests.get(f"{API}/admin/me/mfa", headers=S, timeout=30).json()
    assert st["aktiv"] is True and st["wiederherstellungscodes_uebrig"] == 8
    # Geheimnis liegt nur verschluesselt in der DB
    doc = _db().users.find_one({"id": welt["super_id"]})["mfa"]
    assert doc["secret"] != secret and secret not in doc["secret"]
    # ... und in keiner API-Antwort
    me = requests.get(f"{API}/auth/me", headers=S, timeout=30).json()
    assert "secret" not in str(me.get("user", {}).get("mfa", "")) and secret not in me.__str__()
    liste = requests.get(f"{API}/admin/users", headers=S, timeout=30).json()
    ich = next(u for u in liste if u["id"] == welt["super_id"])
    assert ich["mfa_aktiv"] is True and "mfa" not in ich


def test_02_login_verlangt_zweiten_faktor(welt):
    import mfa
    r = _login(f"mfa_super_{SUF}@{MAIL}")
    assert r.status_code == 200, r.text[:200]
    assert r.json().get("mfa_erforderlich") is True and "token" not in r.json()
    zt = r.json()["mfa_token"]
    # Zwischen-Token ist als Bearer wertlos
    assert requests.get(f"{API}/auth/me", headers=_hdr(zt), timeout=30).status_code == 401
    assert requests.get(f"{API}/admin/users", headers=_hdr(zt), timeout=30).status_code == 401
    # falscher Code
    r = requests.post(f"{API}/auth/login/mfa", json={"mfa_token": zt, "code": "000000"}, timeout=30)
    assert r.status_code == 401, r.text[:200]
    # richtiger Code -> Sitzung
    # Aktivierung (Test 01) und Login liegen oft im selben 30-s-Fenster:
    # derselbe Code darf nicht zweimal gelten -> naechster Zaehler.
    code = _frischer_code(welt["secret"], welt["super_id"])
    r = requests.post(f"{API}/auth/login/mfa", json={"mfa_token": zt, "code": code}, timeout=30)
    assert r.status_code == 200 and r.json().get("token"), r.text[:200]
    welt["S"] = _hdr(r.json()["token"])
    assert r.json()["user"]["mfa_aktiv"] is True and "mfa" not in r.json()["user"]
    assert requests.get(f"{API}/admin/users", headers=welt["S"], timeout=30).status_code == 200
    # derselbe Code kein zweites Mal (Replay-Schutz)
    zt2 = _login(f"mfa_super_{SUF}@{MAIL}").json()["mfa_token"]
    r = requests.post(f"{API}/auth/login/mfa", json={"mfa_token": zt2, "code": code}, timeout=30)
    assert r.status_code == 401, r.text[:200]
    # kaputtes / fremdes Zwischen-Token
    r = requests.post(f"{API}/auth/login/mfa", json={"mfa_token": welt["S"]["Authorization"][7:], "code": code}, timeout=30)
    assert r.status_code == 401


def test_03_wiederherstellungscode_einmalig(welt):
    zt = _login(f"mfa_super_{SUF}@{MAIL}").json()["mfa_token"]
    code = welt["codes"][0]
    r = requests.post(f"{API}/auth/login/mfa", json={"mfa_token": zt, "code": code}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    welt["S"] = _hdr(r.json()["token"])
    st = requests.get(f"{API}/admin/me/mfa", headers=welt["S"], timeout=30).json()
    assert st["wiederherstellungscodes_uebrig"] == 7
    zt = _login(f"mfa_super_{SUF}@{MAIL}").json()["mfa_token"]
    r = requests.post(f"{API}/auth/login/mfa", json={"mfa_token": zt, "code": code}, timeout=30)
    assert r.status_code == 401                      # verbraucht


def test_04_fehlversuche_sperren(welt):
    import mfa
    dbx = _db()
    zt = _login(f"mfa_super_{SUF}@{MAIL}").json()["mfa_token"]
    for _ in range(5):
        requests.post(f"{API}/auth/login/mfa", json={"mfa_token": zt, "code": "111111"}, timeout=30)
    r = requests.post(f"{API}/auth/login/mfa", json={"mfa_token": zt, "code": _frischer_code(welt["secret"], welt["super_id"])}, timeout=30)
    assert r.status_code == 429, r.text[:200]
    # Sperre aufheben (Test), danach geht es wieder
    dbx.users.update_one({"id": welt["super_id"]}, {"$unset": {"mfa.gesperrt_bis": ""}})
    r = requests.post(f"{API}/auth/login/mfa", json={"mfa_token": zt, "code": _frischer_code(welt["secret"], welt["super_id"])}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    welt["S"] = _hdr(r.json()["token"])


def test_05_abschalten_nur_mit_code_und_zuruecksetzen(welt):
    import mfa
    S = welt["S"]
    assert requests.post(f"{API}/admin/me/mfa/deaktivieren", headers=S, json={"code": "000000"}, timeout=30).status_code == 401
    # Normaler Admin richtet 2FA ein, Super-Admin setzt sie zurueck (Aussperrung)
    A = welt["A"]
    sec = requests.post(f"{API}/admin/me/mfa/einrichten", headers=A, timeout=30).json()["secret"]
    assert requests.post(f"{API}/admin/me/mfa/aktivieren", headers=A, json={"code": mfa.totp(sec)}, timeout=30).status_code == 200
    assert _login(f"mfa_admin_{SUF}@{MAIL}").json().get("mfa_erforderlich") is True
    # normaler Admin darf NICHT zuruecksetzen
    assert requests.post(f"{API}/admin/users/{welt['super_id']}/mfa-zuruecksetzen", headers=A, timeout=30).status_code in (401, 403)
    r = requests.post(f"{API}/admin/users/{welt['admin_id']}/mfa-zuruecksetzen", headers=S, timeout=30)
    assert r.status_code == 200, r.text[:200]
    r = _login(f"mfa_admin_{SUF}@{MAIL}")
    assert r.status_code == 200 and r.json().get("token"), "nach Zuruecksetzen normal anmeldbar"
    # Super-Admin schaltet mit gueltigem Code ab
    r = requests.post(f"{API}/admin/me/mfa/deaktivieren", headers=S,
                      json={"code": _frischer_code(welt["secret"], welt["super_id"])}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    assert _login(f"mfa_super_{SUF}@{MAIL}").json().get("token")
    # Betrieb-Uebersicht nennt Super-Admins ohne 2FA
    b = requests.get(f"{API}/admin/betrieb", headers=_hdr(_login(f"mfa_super_{SUF}@{MAIL}").json()["token"]), timeout=30).json()
    assert f"mfa_super_{SUF}@{MAIL}" in b["super_admins_ohne_mfa"]
