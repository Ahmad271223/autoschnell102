# -*- coding: utf-8 -*-
"""Schneller Rauchtest — Sicherheitsnetz fuer automatische Korrekturen.

Bewusst eigenstaendig: legt seine Testdaten selbst an und raeumt sie wieder
weg, braucht kein Demo-Konto und keine externen Seiten. Laeuft in ~20 s.

Wird von ai_fixer.py als Pruefung benutzt: schlaegt hier etwas fehl, wird
eine automatische Korrektur NICHT uebernommen, sondern zurueckgerollt.
"""
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
import requests

BASE = (os.environ.get("TEST_BASE_URL") or "http://localhost:8001").rstrip("/")
# Dieselbe Datenbank wie das laufende Backend (CI: autoschnell_ci) —
# sonst landet das Test-Abo in einer anderen DB und die API sagt 402.
MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
DB_NAME = os.environ.get("DB_NAME") or "autoschnell"
API = f"{BASE}/api"
BACKEND_DIR = Path(__file__).resolve().parent.parent
SUFFIX = uuid.uuid4().hex[:8]
MAIL = f"smoke_{SUFFIX}@e2etest-mail.de"
PW = "SmokeTest123!"


def _cleanup():
    try:
        from pymongo import MongoClient
        db = MongoClient(MONGO_URL, serverSelectionTimeoutMS=3000)[DB_NAME]
        uids = [u["id"] for u in db.users.find({"email": {"$regex": "^smoke_"}}, {"id": 1})]
        dids = [d["id"] for d in db.dealers.find({"user_id": {"$in": uids}}, {"id": 1})]
        for c in ("vehicles", "generated_pdfs", "appointments", "activity_logs",
                  "subscriptions", "resale_listings"):
            db[c].delete_many({"dealer_id": {"$in": dids}})
        db.subscriptions.delete_many({"subject_user_id": {"$in": uids}})
        db.dealers.delete_many({"id": {"$in": dids}})
        db.users.delete_many({"id": {"$in": uids}})
    except Exception:
        pass


@pytest.fixture(scope="module")
def chef():
    """Frischer Haendler mit aktivem Abo (fuer Vertrags-Test)."""
    _cleanup()
    r = requests.post(f"{API}/auth/register", json={
        "email": MAIL, "password": PW, "company_name": "Smoke Autohaus",
        "contact_person": "S T", "phone": "0511 000"}, timeout=30)
    assert r.status_code == 200, f"Registrierung fehlgeschlagen: {r.status_code} {r.text[:200]}"
    tok = r.json()["token"]
    me = requests.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {tok}"},
                      timeout=30).json()["user"]
    # Abo direkt setzen (Vertragserstellung verlangt ein aktives Abo)
    from datetime import datetime, timedelta, timezone
    from pymongo import MongoClient
    MongoClient(MONGO_URL)[DB_NAME].subscriptions.insert_one({
        "id": str(uuid.uuid4()), "dealer_id": me["dealer_id"], "subject_user_id": me["id"],
        "plan": "monthly", "status": "active",
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat()})
    tok = requests.post(f"{API}/auth/login", json={"email": MAIL, "password": PW},
                        timeout=30).json()["token"]
    yield {"token": tok, "user": me}
    _cleanup()


def test_backend_importierbar():
    """Faengt Syntax-/Namensfehler ab, bevor sie irgendwen erreichen."""
    r = subprocess.run([sys.executable, "-c", "import server"], cwd=str(BACKEND_DIR),
                       capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, f"Backend laesst sich nicht laden:\n{r.stderr[-1500:]}"


def test_health():
    r = requests.get(f"{API}/health", timeout=15)
    assert r.status_code == 200, f"Health-Endpunkt antwortet {r.status_code}"


def test_registrierung_und_login(chef):
    assert chef["token"], "Kein Token nach Login"
    assert chef["user"]["role"] == "dealer"


def test_falsches_passwort_abgelehnt():
    r = requests.post(f"{API}/auth/login", json={"email": MAIL, "password": "FALSCH!"},
                      timeout=30)
    assert r.status_code == 401, f"Falsches Passwort ergab {r.status_code} statt 401"


def test_ohne_token_gesperrt():
    assert requests.get(f"{API}/auth/me", timeout=15).status_code in (401, 403)


def test_fahrzeug_und_kaufvertrag(chef):
    """Kernkette: Fahrzeug anlegen -> Vertrag -> PDF."""
    h = {"Authorization": f"Bearer {chef['token']}"}
    r = requests.post(f"{API}/vehicles/manual", headers=h, json={
        "make_label": "VW", "model_label": "Golf", "model_description": "Golf Test",
        "first_registration": "01/2021", "mileage": 50000, "fuel_label": "Benzin",
        "gearbox_label": "Schaltgetriebe", "power_ps": 110, "color": "Blau",
        "vin": "", "previous_owners": "1", "features": [], "description": "",
        "purchase_price": 9000}, timeout=60)
    assert r.status_code == 200, f"Fahrzeug anlegen: {r.status_code} {r.text[:200]}"
    vid = r.json()["id"]

    r = requests.post(f"{API}/contracts", headers=h, json={
        "vehicle_id": vid, "seller_name": "Test Verkaeufer", "seller_address": "Weg 1",
        "seller_zip": "30159", "seller_city": "Hannover", "purchase_price": 9000,
        "pickup_date": "2026-12-01", "pickup_time": "10:00"}, timeout=120)
    assert r.status_code == 200, f"Vertrag: {r.status_code} {r.text[:200]}"
    cid = r.json()["id"]

    r = requests.get(f"{API}/contracts/{cid}/pdf", headers=h, timeout=60)
    assert r.status_code == 200 and r.content[:4] == b"%PDF", "Vertrags-PDF fehlerhaft"


def test_rollentrennung(chef):
    """Kaeufer darf nicht in den Haendler-Bestand."""
    mail = f"smoke_b_{SUFFIX}@e2etest-mail.de"
    r = requests.post(f"{API}/buyer/register", json={"gewerblich_bestaetigt": True, 
        "company_name": "Smoke Kaeufer", "contact_name": "S K", "email": mail,
        "password": PW, "phone": "0511 1"}, timeout=30)
    if r.status_code != 200:
        r = requests.post(f"{API}/buyer/login", json={"email": mail, "password": PW}, timeout=30)
    btok = r.json()["token"]
    r = requests.get(f"{API}/bestand", headers={"Authorization": f"Bearer {btok}"}, timeout=30)
    assert r.status_code == 403, f"Kaeufer kam in den Bestand ({r.status_code})"


def test_fremde_zahlung_unsichtbar(chef):
    """Mandantentest: bezahlte Transaktion eines ANDEREN Nutzers darf
    ueber /payments/status nicht einsehbar sein (PR-Review Blocker)."""
    from pymongo import MongoClient
    session_id = f"cs_smoke_{SUFFIX}"
    MongoClient(MONGO_URL)[DB_NAME].payment_transactions.insert_one({
        "session_id": session_id, "user_id": "jemand_anderes",
        "dealer_id": "fremde_firma", "plan": "monthly",
        "payment_status": "paid", "status": "complete",
        "amount": 160.0, "created_at": "2026-01-01T00:00:00+00:00"})
    try:
        r = requests.get(f"{API}/payments/status/{session_id}",
                         headers={"Authorization": f"Bearer {chef['token']}"},
                         timeout=30)
        assert r.status_code == 403, (
            f"Fremde Zahlung wurde ausgeliefert ({r.status_code}): {r.text[:200]}")
    finally:
        MongoClient(MONGO_URL)[DB_NAME].payment_transactions.delete_one(
            {"session_id": session_id})


def test_sucher_loeschen_zerstoert_firma_nicht(chef):
    """Mandantentest: loescht der Admin einen SUCHER, muessen Firma und
    Haendler-Abo bestehen bleiben (PR-Review Blocker: Admin-Loeschung)."""
    from pymongo import MongoClient
    dbx = MongoClient(MONGO_URL)[DB_NAME]
    h = {"Authorization": f"Bearer {chef['token']}"}
    r = requests.post(f"{API}/dealer/sucher", headers=h, json={
        "email": f"smoke_s_{SUFFIX}@e2etest-mail.de", "password": PW,
        "first_name": "Smoke", "last_name": "Sucher"}, timeout=30)
    assert r.status_code == 200, f"Sucher anlegen: {r.status_code} {r.text[:200]}"
    sucher_id = r.json().get("sucher_id") or r.json().get("id")
    assert sucher_id, f"Keine Sucher-ID in Antwort: {r.text[:200]}"

    # Direkt in der DB loeschen wie der Admin-Endpunkt es tut? Nein — wir
    # rufen den ECHTEN Endpunkt als Plattform-Admin auf. Dazu einen
    # Wegwerf-Admin direkt in der DB anlegen (der Seed-Admin der CI-DB
    # hat kein bekanntes Passwort).
    import bcrypt
    admin_mail = f"smoke_admin_{SUFFIX}@e2etest-mail.de"
    dbx.users.insert_one({
        "id": f"adm_{SUFFIX}", "email": admin_mail, "role": "admin",
        "active": True, "dealer_id": None, "is_super_admin": True,
        "password_hash": bcrypt.hashpw(PW.encode(), bcrypt.gensalt()).decode(),
        "created_at": "2026-01-01T00:00:00+00:00"})
    try:
        r = requests.post(f"{API}/auth/login",
                          json={"email": admin_mail, "password": PW}, timeout=30)
        assert r.status_code == 200, f"Admin-Login: {r.status_code} {r.text[:200]}"
        atok = r.json()["token"]
        r = requests.delete(f"{API}/admin/users/{sucher_id}",
                            headers={"Authorization": f"Bearer {atok}"}, timeout=30)
        assert r.status_code == 200, f"Sucher loeschen: {r.status_code} {r.text[:200]}"
        dealer_id = chef["user"]["dealer_id"]
        assert dbx.dealers.find_one({"id": dealer_id}), \
            "Firmendatensatz wurde beim Sucher-Loeschen mit entfernt!"
        assert dbx.subscriptions.find_one({"dealer_id": dealer_id}), \
            "Haendler-Abo wurde beim Sucher-Loeschen mit entfernt!"
        assert dbx.users.find_one({"id": sucher_id}) is None, \
            "Sucher wurde nicht geloescht"
    finally:
        dbx.users.delete_many({"email": admin_mail})
