# -*- coding: utf-8 -*-
"""Beweis (Auftrag Punkt 11): Ein Retry mit demselben Idempotency-Key
erzeugt KEINEN zweiten Versandeintrag — auch nicht bei 10 parallelen
Wiederholungen. Ohne Key bleibt das alte Verhalten (jeder Aufruf ein
Eintrag). Braucht Backend mit MOCK_PROVIDER_FETCH=true (wie CI)."""
import concurrent.futures
import os
import sys
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
PW = "IdemTest123!"


def _db():
    from pymongo import MongoClient
    return MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)[DB_NAME]


@pytest.fixture(scope="module")
def vertrag():
    r = requests.post(f"{API}/auth/register", json={
        "email": f"idem_{SUF}@e2etest-mail.de", "password": PW,
        "company_name": "Idem GmbH", "contact_person": "I T",
        "phone": "0511 4"}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    tok = r.json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    me = requests.get(f"{API}/auth/me", headers=h, timeout=30).json()["user"]
    _db().subscriptions.insert_one({
        "id": str(uuid.uuid4()), "dealer_id": me["dealer_id"],
        "subject_user_id": me["id"], "plan": "monthly", "status": "active",
        "expires_at": (datetime.now(timezone.utc)
                       + timedelta(days=1)).isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat()})
    ka = f"https://www.kleinanzeigen.de/s-anzeige/idem/94{uuid.uuid4().int % 10**8:08d}-216-1"
    r = requests.post(f"{API}/mobile/compare", json={"url": ka},
                      headers=h, timeout=90)
    if r.status_code != 200 or not (r.json().get("vehicle") or {}).get("_mock"):
        pytest.skip("Backend ohne MOCK_PROVIDER_FETCH")
    r = requests.post(f"{API}/contracts", headers=h, json={
        "vehicle_id": r.json()["vehicle_id"], "seller_name": "I V",
        "seller_address": "Weg 1", "seller_zip": "30159",
        "seller_city": "Hannover", "purchase_price": 5000,
        "pickup_date": "2099-06-01", "pickup_time": "10:00"}, timeout=90)
    assert r.status_code == 200, r.text[:200]
    yield {"h": h, "cid": r.json()["id"], "me": me}
    dbx = _db()
    for c in ("subscriptions", "vehicles", "appointments", "generated_pdfs"):
        dbx[c].delete_many({"dealer_id": me["dealer_id"]})
    dbx.dealers.delete_many({"id": me["dealer_id"]})
    dbx.users.delete_many({"id": me["id"]})
    dbx.listings_cache.delete_many({"item_id": {"$regex": "^94"}})


def _eintraege(cid):
    doc = _db().generated_pdfs.find_one({"id": cid}, {"send_status": 1}) or {}
    return doc.get("send_status") or []


def test_retry_mit_gleichem_key_versendet_nicht_doppelt(vertrag):
    key = f"klick-{uuid.uuid4().hex}"
    js = {"channel": "email", "recipient": "idem@e2etest-mail.de",
          "subject": "V", "message": "Hallo", "idempotency_key": key}
    r1 = requests.post(f"{API}/contracts/{vertrag['cid']}/send",
                       headers=vertrag["h"], json=js, timeout=30)
    r2 = requests.post(f"{API}/contracts/{vertrag['cid']}/send",
                       headers=vertrag["h"], json=js, timeout=30)
    assert r1.status_code == 200 and r2.status_code == 200
    assert r2.json().get("bereits_gesendet") is True
    treffer = [e for e in _eintraege(vertrag["cid"])
               if e.get("idempotency_key") == key]
    assert len(treffer) == 1, f"{len(treffer)} Eintraege statt 1"


def test_zehn_parallele_retries_ein_eintrag(vertrag):
    key = f"parallel-{uuid.uuid4().hex}"
    js = {"channel": "whatsapp", "recipient": "+491700000001",
          "subject": "V", "message": "Hallo", "idempotency_key": key}

    def senden(_):
        return requests.post(f"{API}/contracts/{vertrag['cid']}/send",
                             headers=vertrag["h"], json=js, timeout=30).status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        codes = list(ex.map(senden, range(10)))
    assert all(c == 200 for c in codes), codes
    treffer = [e for e in _eintraege(vertrag["cid"])
               if e.get("idempotency_key") == key]
    assert len(treffer) == 1, f"{len(treffer)} Eintraege statt 1"


def test_ohne_key_schuetzt_der_server_selbst(vertrag):
    """Pruefbericht Runde 8, Befund 3: Ohne Schluessel gab es KEINEN Schutz —
    zwei identische Aufrufe stellten zweimal zu. Jetzt leitet der Server den
    Schluessel aus dem Inhalt ab: dieselbe Mail an denselben Empfaenger in
    derselben Minute ist EIN Versand. Eine andere Mail bleibt ein eigener."""
    js = {"channel": "email", "recipient": "idem2@e2etest-mail.de",
          "subject": "V", "message": "Hallo"}
    vorher = len(_eintraege(vertrag["cid"]))
    for _ in range(2):
        r = requests.post(f"{API}/contracts/{vertrag['cid']}/send",
                          headers=vertrag["h"], json=js, timeout=30)
        assert r.status_code == 200
    assert len(_eintraege(vertrag["cid"])) == vorher + 1
    js["message"] = "Hallo, anderer Text"
    r = requests.post(f"{API}/contracts/{vertrag['cid']}/send",
                      headers=vertrag["h"], json=js, timeout=30)
    assert r.status_code == 200
    assert len(_eintraege(vertrag["cid"])) == vorher + 2
