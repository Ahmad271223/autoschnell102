# -*- coding: utf-8 -*-
"""Nachpruefung Runde 10, Teil 2 (09/2026): Re-Vergleich nach Seitenausgang.
Gehoert zu routes/listings.py (Gruppe 2, gemeinsam mit Runde 11 committen).

HTTP-Teile brauchen das Backend auf TEST_BASE_URL (Mock-Anbieter wie in CI).
"""
import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
try:
    from dotenv import load_dotenv
    load_dotenv(BACKEND / ".env")
except Exception:                                   # noqa: BLE001
    pass

BASE = (os.environ.get("TEST_BASE_URL") or "http://localhost:8001").rstrip("/")
API = f"{BASE}/api"
MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
DB_NAME = os.environ.get("DB_NAME") or "autoschnell"
SUF = uuid.uuid4().hex[:8]
PW = "NachTest123!"
JETZT = datetime.now(timezone.utc)


def _db():
    from pymongo import MongoClient
    return MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)[DB_NAME]


def _mit_db(fn):
    async def _run():
        from motor.motor_asyncio import AsyncIOMotorClient
        cl = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
        try:
            return await fn(cl[DB_NAME])
        finally:
            cl.close()
    return asyncio.run(_run())


# =============================================================== Vertragsversand
@pytest.fixture(scope="module")
def vertrag():
    r = requests.post(f"{API}/auth/register", json={
        "email": f"nachlisting_{SUF}@e2etest-mail.de", "password": PW,
        "company_name": "Nach GmbH", "contact_person": "N T", "phone": "0511 9"}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    h = {"Authorization": f"Bearer {r.json()['token']}"}
    me = requests.get(f"{API}/auth/me", headers=h, timeout=30).json()["user"]
    _db().subscriptions.insert_one({
        "id": str(uuid.uuid4()), "dealer_id": me["dealer_id"], "subject_user_id": me["id"],
        "plan": "monthly", "status": "active",
        "expires_at": (JETZT + timedelta(days=1)).isoformat(), "created_at": JETZT.isoformat()})
    ka = f"https://www.kleinanzeigen.de/s-anzeige/nach/94{uuid.uuid4().int % 10**8:08d}-216-1"
    r = requests.post(f"{API}/mobile/compare", json={"url": ka}, headers=h, timeout=90)
    if r.status_code != 200 or not (r.json().get("vehicle") or {}).get("_mock"):
        pytest.skip("Backend ohne MOCK_PROVIDER_FETCH")
    vid = r.json()["vehicle_id"]
    r = requests.post(f"{API}/contracts", headers=h, json={
        "vehicle_id": vid, "seller_name": "N V", "seller_address": "Weg 1",
        "seller_zip": "30159", "seller_city": "Hannover", "purchase_price": 5000,
        "pickup_date": "2099-06-01", "pickup_time": "10:00"}, timeout=90)
    assert r.status_code == 200, r.text[:200]
    yield {"h": h, "cid": r.json()["id"], "me": me, "ka": ka, "vid": vid}
    dbx = _db()
    for c in ("subscriptions", "vehicles", "appointments", "generated_pdfs", "activity_logs"):
        dbx[c].delete_many({"dealer_id": me["dealer_id"]})
    dbx.users.delete_many({"id": me["id"]})
    dbx.dealers.delete_many({"id": me["dealer_id"]})


# =============================================================== Re-Vergleich
def test_re_vergleich_nach_seitenausgang_erneuert_daten(vertrag):
    """Nach 'storniert' ist ein erneuter Vergleich ein Neuanfang (Daten frisch);
    im 'bestand' bleiben Haendler-Korrekturen erhalten (Runde 10)."""
    dbx = _db()
    vid = vertrag["vid"]
    for lifecycle, frisch_erwartet in (("storniert", True), ("bestand", False)):
        dbx.vehicles.update_one({"id": vid}, {"$set": {"lifecycle": lifecycle, "data.mileage": 1},
                                              "$unset": {"inserat_aktuell": ""}})
        r = requests.post(f"{API}/mobile/compare", json={"url": vertrag["ka"]},
                          headers=vertrag["h"], timeout=90)
        assert r.status_code == 200, r.text[:200]
        v = dbx.vehicles.find_one({"id": vid}, {"_id": 0})
        if frisch_erwartet:
            assert v["data"].get("mileage") != 1, "Daten nach Storno nicht erneuert"
        else:
            assert v["data"].get("mileage") == 1 and v.get("inserat_aktuell"), \
                "Bestandsdaten wurden ueberschrieben"


