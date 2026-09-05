# -*- coding: utf-8 -*-
"""End-to-End-Test ueber die komplette Plattform (Priorität 3).

Registrierung -> Firma -> Sucher -> Abo -> Linkpruefung (Job) ->
Vergleich -> Kaufvertrag -> Fahrer -> Abholprotokoll (Pflichtfelder +
Unterschriften) -> PDFs -> Inserat -> Marktplatz (Kaeufer) ->
Admin-Loeschung (Sucher einzeln, dann komplette Firma mit Vorschau).

Braucht ein laufendes Backend MIT MOCK_PROVIDER_FETCH=true (wie in CI);
ohne Mock ueberspringt sich der Test mit Begruendung.
"""
import base64
import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BASE = (os.environ.get("TEST_BASE_URL") or "http://localhost:8001").rstrip("/")
API = f"{BASE}/api"
MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
DB_NAME = os.environ.get("DB_NAME") or "autoschnell"
SUF = uuid.uuid4().hex[:8]
PW = "E2eTest123!"
KA_ID = f"96{uuid.uuid4().int % 10**8:08d}"
KA_URL = f"https://www.kleinanzeigen.de/s-anzeige/e2e/{KA_ID}-216-1"

# 1x1-PNG als Unterschrift (>=20 Zeichen b64)
SIG = "data:image/png;base64," + base64.b64encode(
    base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
        "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")).decode()


def _db():
    from pymongo import MongoClient
    return MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)[DB_NAME]


def _seed_sub(dealer_id, user_id):
    _db().subscriptions.insert_one({
        "id": str(uuid.uuid4()), "dealer_id": dealer_id,
        "subject_user_id": user_id, "plan": "monthly", "status": "active",
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat()})


def _make_admin():
    import bcrypt
    mail = f"e2e_admin_{SUF}@e2etest-mail.de"
    _db().users.insert_one({
        "id": f"e2eadm_{SUF}", "email": mail, "role": "admin", "active": True,
        "dealer_id": None, "is_super_admin": True,
        "password_hash": bcrypt.hashpw(PW.encode(), bcrypt.gensalt()).decode(),
        "created_at": "2026-01-01T00:00:00+00:00"})
    r = requests.post(f"{API}/auth/login", json={"email": mail, "password": PW},
                      timeout=30)
    assert r.status_code == 200, f"Admin-Login: {r.text[:200]}"
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture(scope="module")
def welt():
    """Baut die komplette Welt auf und raeumt am Ende via Firmenloeschung ab."""
    z = {}
    yield z
    # Notfall-Aufraeumen, falls der Test vor der Admin-Loeschung abbrach.
    dbx = _db()
    for coll in ("users", "subscriptions", "vehicles", "appointments",
                 "generated_pdfs", "resale_listings", "pickup_protocols",
                 "pickup_reports", "driver_accounts", "dealer_drivers"):
        if z.get("dealer_id"):
            dbx[coll].delete_many({"dealer_id": z["dealer_id"]})
    dbx.users.delete_many({"email": {"$regex": f"_{SUF}@"}})
    dbx.dealers.delete_many({"id": z.get("dealer_id", "___")})
    dbx.listings_cache.delete_many({"item_id": KA_ID})
    dbx.link_jobs.delete_many({"item_id": KA_ID})


def test_01_registrierung_firma(welt):
    r = requests.post(f"{API}/auth/register", json={
        "email": f"e2e_chef_{SUF}@e2etest-mail.de", "password": PW,
        "company_name": "E2E Autohaus", "contact_person": "E Chef",
        "phone": "0511 9"}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    welt["chef_tok"] = r.json()["token"]
    me = requests.get(f"{API}/auth/me",
                      headers={"Authorization": f"Bearer {welt['chef_tok']}"},
                      timeout=30).json()["user"]
    welt["chef"] = me
    welt["dealer_id"] = me["dealer_id"]
    welt["H"] = {"Authorization": f"Bearer {welt['chef_tok']}"}


def test_02_sucher_und_abo(welt):
    r = requests.post(f"{API}/dealer/sucher", headers=welt["H"], json={
        "email": f"e2e_sucher_{SUF}@e2etest-mail.de", "password": PW,
        "first_name": "E2E", "last_name": "Sucher"}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    welt["sucher_id"] = r.json()["sucher_id"]
    # Abos (Chef + Sucher) — Zahlungsweg ist separat getestet.
    _seed_sub(welt["dealer_id"], welt["chef"]["id"])
    _seed_sub(welt["dealer_id"], welt["sucher_id"])
    r = requests.post(f"{API}/auth/login", json={
        "email": f"e2e_sucher_{SUF}@e2etest-mail.de", "password": PW},
        timeout=30)
    assert r.status_code == 200
    welt["S"] = {"Authorization": f"Bearer {r.json()['token']}"}


def test_03_linkpruefung_als_job_und_vergleich(welt):
    r = requests.post(f"{API}/listings/check", json={"url": KA_URL},
                      headers=welt["S"], timeout=30)
    assert r.status_code == 200, r.text[:200]
    body = r.json()
    if body["status"] != "completed":
        ende = time.monotonic() + 60
        while time.monotonic() < ende:
            st = requests.get(f"{API}/listings/check/{body['job_id']}",
                              headers=welt["S"], timeout=30).json()
            if st["status"] in ("completed", "failed"):
                assert st["status"] == "completed", st
                break
            time.sleep(1)
        else:
            pytest.fail("Linkpruefungs-Job wurde nicht fertig")

    r = requests.post(f"{API}/mobile/compare", json={"url": KA_URL},
                      headers=welt["S"], timeout=60)
    assert r.status_code == 200, r.text[:200]
    if not (r.json().get("vehicle") or {}).get("_mock"):
        pytest.skip("Backend ohne MOCK_PROVIDER_FETCH — E2E braucht den Mock")
    assert r.json().get("cached") is True, "Vergleich traf nicht den Job-Cache"
    welt["vehicle_id"] = r.json()["vehicle_id"]


def test_04_kaufvertrag_und_termin(welt):
    r = requests.post(f"{API}/contracts", headers=welt["H"], json={
        "vehicle_id": welt["vehicle_id"], "seller_name": "E2E Verkaeufer",
        "seller_address": "Weg 3", "seller_zip": "30159",
        "seller_city": "Hannover", "purchase_price": 12000,
        "pickup_date": "2099-03-01", "pickup_time": "09:00"}, timeout=90)
    assert r.status_code == 200, r.text[:200]
    welt["contract_id"] = r.json()["id"]
    r = requests.get(f"{API}/contracts/{welt['contract_id']}/pdf",
                     headers=welt["H"], timeout=60)
    assert r.status_code == 200 and r.content[:4] == b"%PDF"
    appts = requests.get(f"{API}/appointments", headers=welt["H"],
                         timeout=30).json()
    appt = next(a for a in appts if a.get("contract_id") == welt["contract_id"])
    welt["appt_id"] = appt["id"]


def test_05_fahrer_zuweisen(welt):
    r = requests.post(f"{API}/driver/register", json={
        "email": f"e2e_fahrer_{SUF}@e2etest-mail.de", "password": PW,
        "display_name": "E2E Fahrer"}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    welt["drv_tok"] = r.json()["token"]
    welt["drv_id"] = r.json()["driver"]["id"]
    code = r.json()["driver"]["driver_code"]
    r = requests.post(f"{API}/drivers/add", headers=welt["H"],
                      json={"driver_code": code}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    r = requests.put(f"{API}/appointments/{welt['appt_id']}", headers=welt["H"],
                     json={"driver_id": welt["drv_id"]}, timeout=60)
    assert r.status_code == 200, r.text[:200]
    welt["D"] = {"Authorization": f"Bearer {welt['drv_tok']}"}


def test_06_abholung_ohne_protokoll_verboten(welt):
    """Der alte Schnellweg 'abgeholt' MUSS ohne Protokoll scheitern."""
    r = requests.put(f"{API}/driver/appointments/{welt['appt_id']}/status",
                     headers=welt["D"], json={"status": "abgeholt"}, timeout=30)
    assert r.status_code == 409, f"abgeholt ohne Protokoll: {r.status_code}"


def test_07_abholprotokoll_und_pdfs(welt):
    tpl = requests.get(f"{API}/driver/appointments/{welt['appt_id']}/protocol",
                       headers=welt["D"], timeout=60).json()
    felder = [f[0] if isinstance(f, (list, tuple)) else f.get("key")
              for f in tpl["template"]["vehicle_check_fields"]]
    assert len(felder) == 12, f"12 Fahrzeugdaten-Zeilen erwartet: {felder}"

    # Unvollstaendiger Abschluss muss abgelehnt werden (422)
    r = requests.post(
        f"{API}/driver/appointments/{welt['appt_id']}/protocol/finalize",
        headers=welt["D"], json={"signature_driver_b64": SIG,
                                 "signature_seller_b64": SIG,
                                 "seller_name": "E2E Verkaeufer",
                                 "place": "Hannover"}, timeout=60)
    assert r.status_code in (400, 422), f"leeres Protokoll durchgerutscht: {r.status_code}"

    # Alle Pflichtabschnitte fuellen
    r = requests.put(f"{API}/driver/appointments/{welt['appt_id']}/protocol",
                     headers=welt["D"], json={
        "vehicle_check": {k: {"status": "stimmt"} for k in felder},
        "documents": {"Fahrzeugschein": True},
        "keys_count": "2", "keys_expected": "2",
        "condition": {"mileage": "90000", "fuel_level": "1/2"},
        "damages_confirmed": True,
        "notes": "E2E-Testlauf"}, timeout=30)
    assert r.status_code == 200, r.text[:200]

    r = requests.post(
        f"{API}/driver/appointments/{welt['appt_id']}/protocol/finalize",
        headers=welt["D"], json={"signature_driver_b64": SIG,
                                 "signature_seller_b64": SIG,
                                 "seller_name": "E2E Verkaeufer",
                                 "place": "Hannover"}, timeout=120)
    assert r.status_code == 200, f"Abschluss: {r.status_code} {r.text[:300]}"

    # PDFs: Abholauftrag + ausgefuelltes Protokoll
    for pfad in (f"/driver/appointments/{welt['appt_id']}/pickup-order.pdf",
                 f"/driver/appointments/{welt['appt_id']}/protocol.pdf"):
        r = requests.get(f"{API}{pfad}", headers=welt["D"], timeout=90)
        assert r.status_code == 200 and r.content[:4] == b"%PDF", pfad

    # Termin steht jetzt auf abgeholt
    appt = next(a for a in requests.get(f"{API}/appointments",
                                        headers=welt["H"], timeout=30).json()
                if a["id"] == welt["appt_id"])
    assert appt["status"] == "abgeholt"


def test_08_inserat_und_marktplatz(welt):
    # Verkaufspaket durch den Admin + oeffentliches Profil
    admin = _make_admin()
    welt["ADMIN"] = admin
    r = requests.put(f"{API}/admin/dealers/{welt['dealer_id']}/sale-plan",
                     headers=admin, json={"tier": "s5", "months": 1}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    r = requests.put(f"{API}/dealer/marketplace-profile", headers=welt["H"],
                     json={"public": True, "description": "E2E"}, timeout=30)
    assert r.status_code == 200, r.text[:200]

    # Entwurf -> verkaufsbereit -> veroeffentlicht
    r = requests.post(f"{API}/resale/draft/{welt['vehicle_id']}",
                      headers=welt["H"], timeout=30)
    assert r.status_code == 200, r.text[:300]
    listing_id = r.json()["id"]
    r = requests.put(f"{API}/resale/{listing_id}", headers=welt["H"],
                     json={"price_public": 14500, "price_b2b": 13900},
                     timeout=30)
    assert r.status_code == 200, r.text[:200]
    r = requests.post(f"{API}/resale/{listing_id}/status", headers=welt["H"],
                      json={"status": "verkaufsbereit"}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    r = requests.post(f"{API}/resale/{listing_id}/publish", headers=welt["H"],
                      json={"visibility": "public"}, timeout=30)
    assert r.status_code == 200, r.text[:300]
    welt["listing_id"] = listing_id

    # Kaeufer registrieren, Zugang freischalten, Inserat finden
    r = requests.post(f"{API}/buyer/register", json={"gewerblich_bestaetigt": True, 
        "company_name": "E2E Kaeufer", "contact_name": "K B",
        "email": f"e2e_kaeufer_{SUF}@e2etest-mail.de", "password": PW,
        "phone": "0511 8"}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    kaeufer_tok = r.json()["token"]
    kid = requests.get(f"{API}/buyer/me",
                       headers={"Authorization": f"Bearer {kaeufer_tok}"},
                       timeout=30).json()["id"]
    _db().users.update_one({"id": kid}, {"$set": {"marketplace_access": {
        "active": True, "plan": "monthly",
        "expires_at": (datetime.now(timezone.utc)
                       + timedelta(days=1)).isoformat()}}})
    r = requests.get(f"{API}/marktplatz/listings",
                     headers={"Authorization": f"Bearer {kaeufer_tok}"},
                     timeout=30)
    assert r.status_code == 200, r.text[:200]
    ids = [l["id"] for l in r.json()]
    assert welt["listing_id"] in ids, "Inserat nicht im Marktplatz sichtbar"


def test_09_admin_loeschung(welt):
    admin = welt["ADMIN"]
    # 1) Sucher einzeln: Firma bleibt unversehrt
    r = requests.delete(f"{API}/admin/users/{welt['sucher_id']}",
                        headers=admin, timeout=30)
    assert r.status_code == 200, r.text[:200]
    assert _db().dealers.find_one({"id": welt["dealer_id"]}) is not None

    # 2) Haupt-Account ohne Bestaetigung: 409 + Vorschau verfuegbar
    r = requests.delete(f"{API}/admin/users/{welt['chef']['id']}",
                        headers=admin, timeout=30)
    assert r.status_code == 409
    r = requests.get(f"{API}/admin/dealers/{welt['dealer_id']}/loeschvorschau",
                     headers=admin, timeout=30)
    assert r.status_code == 200
    assert r.json()["wuerde_loeschen"]["vehicles"] >= 1

    # 3) Mit Bestaetigung: Firma vollstaendig weg
    r = requests.delete(
        f"{API}/admin/users/{welt['chef']['id']}?firma_loeschen=true",
        headers=admin, timeout=60)
    assert r.status_code == 200, r.text[:300]
    dbx = _db()
    assert dbx.dealers.find_one({"id": welt["dealer_id"]}) is None
    assert dbx.vehicles.count_documents({"dealer_id": welt["dealer_id"]}) == 0
    assert dbx.generated_pdfs.count_documents(
        {"dealer_id": welt["dealer_id"]}) == 0
    assert dbx.resale_listings.count_documents(
        {"dealer_id": welt["dealer_id"]}) == 0
