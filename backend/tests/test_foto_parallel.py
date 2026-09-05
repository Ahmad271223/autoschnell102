# -*- coding: utf-8 -*-
"""Beweis fuer den Lasttest-Befund (finaler T3): parallele Foto-Uploads
aufs SELBE Inserat duerfen keine Referenzen verlieren (frueher: Lesen-
Aendern-Schreiben -> Lost Update -> Dateien ohne DB-Eintrag).
Braucht Backend mit MOCK_PROVIDER_FETCH=true (wie CI)."""
import base64
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
PW = "FotoTest123!"
FOTO = "data:image/jpeg;base64," + base64.b64encode(
    b"\xff\xd8\xff\xe0" + os.urandom(4096) + b"\xff\xd9").decode()


def _db():
    from pymongo import MongoClient
    return MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)[DB_NAME]


@pytest.fixture(scope="module")
def inserat():
    r = requests.post(f"{API}/auth/register", json={
        "email": f"foto_{SUF}@e2etest-mail.de", "password": PW,
        "company_name": "Foto GmbH", "contact_person": "F T",
        "phone": "0511 6"}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    h = {"Authorization": f"Bearer {r.json()['token']}"}
    me = requests.get(f"{API}/auth/me", headers=h, timeout=30).json()["user"]
    _db().subscriptions.insert_one({
        "id": str(uuid.uuid4()), "dealer_id": me["dealer_id"],
        "subject_user_id": me["id"], "plan": "monthly", "status": "active",
        "expires_at": (datetime.now(timezone.utc)
                       + timedelta(days=1)).isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat()})
    ka = f"https://www.kleinanzeigen.de/s-anzeige/foto/93{uuid.uuid4().int % 10**8:08d}-216-1"
    r = requests.post(f"{API}/mobile/compare", json={"url": ka}, headers=h,
                      timeout=90)
    if r.status_code != 200 or not (r.json().get("vehicle") or {}).get("_mock"):
        pytest.skip("Backend ohne MOCK_PROVIDER_FETCH")
    vid = r.json()["vehicle_id"]
    # Lifecycle: Inserats-Entwurf braucht ein verkaufsfaehiges Fahrzeug —
    # wie im echten Ablauf zuerst den Kaufvertrag anlegen.
    r = requests.post(f"{API}/contracts", headers=h, json={
        "vehicle_id": vid, "seller_name": "F V", "seller_address": "W 1",
        "seller_zip": "30159", "seller_city": "Hannover",
        "purchase_price": 8000, "pickup_date": "2099-08-01",
        "pickup_time": "10:00"}, timeout=90)
    assert r.status_code == 200, r.text[:200]
    r = requests.post(f"{API}/resale/draft/{vid}", headers=h, timeout=30)
    assert r.status_code == 200, r.text[:200]
    yield {"h": h, "lid": r.json()["id"], "me": me}
    dbx = _db()
    for c in ("subscriptions", "vehicles", "resale_listings",
              "generated_pdfs", "appointments"):
        dbx[c].delete_many({"dealer_id": me["dealer_id"]})
    dbx.dealers.delete_many({"id": me["dealer_id"]})
    dbx.users.delete_many({"id": me["id"]})
    dbx.listings_cache.delete_many({"item_id": {"$regex": "^93"}})


def _keys(lid):
    doc = _db().resale_listings.find_one({"id": lid},
                                         {"photos.uploaded_keys": 1}) or {}
    return (doc.get("photos") or {}).get("uploaded_keys") or []


def test_20_parallele_uploads_verlieren_nichts(inserat):
    def up(_):
        return requests.post(
            f"{API}/resale/{inserat['lid']}/photos",
            headers=inserat["h"], json={"photos_b64": [FOTO]},
            timeout=60).status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as ex:
        codes = list(ex.map(up, range(20)))
    assert all(c == 200 for c in codes), codes
    keys = _keys(inserat["lid"])
    assert len(keys) == 20, f"{len(keys)} statt 20 Referenzen (Lost Update!)"
    # Jede Referenz zeigt auf eine echte Datei
    root = Path(__file__).resolve().parents[1] / "uploads"
    fehlend = [k for k in keys if not (root / k).exists()]
    assert not fehlend, f"DB-Eintraege ohne Datei: {fehlend[:3]}"


def test_parallele_loeschungen_konsistent(inserat):
    keys = _keys(inserat["lid"])[:10]

    def rm(k):
        return requests.post(
            f"{API}/resale/{inserat['lid']}/photos/remove",
            headers=inserat["h"], json={"key": k}, timeout=60).status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        codes = list(ex.map(rm, keys))
    assert all(c == 200 for c in codes), codes
    rest = _keys(inserat["lid"])
    assert len(rest) == 10, f"{len(rest)} statt 10 uebrig"
    assert not (set(keys) & set(rest)), "geloeschte Keys noch referenziert"


def test_41tes_foto_atomar_abgelehnt(inserat):
    # auf 40 auffuellen
    aktuell = len(_keys(inserat["lid"]))
    fehlen = 40 - aktuell
    while fehlen > 0:                      # max. 20 Fotos je Request
        batch = min(20, fehlen)
        r = requests.post(f"{API}/resale/{inserat['lid']}/photos",
                          headers=inserat["h"],
                          json={"photos_b64": [FOTO] * batch}, timeout=120)
        assert r.status_code == 200, r.text[:200]
        fehlen -= batch
    r = requests.post(f"{API}/resale/{inserat['lid']}/photos",
                      headers=inserat["h"], json={"photos_b64": [FOTO]},
                      timeout=60)
    assert r.status_code == 400, f"41. Foto durchgerutscht: {r.status_code}"
    assert len(_keys(inserat["lid"])) == 40
