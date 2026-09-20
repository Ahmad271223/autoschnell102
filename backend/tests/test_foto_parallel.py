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

import konten  # noqa: E402  Kontonummer (13.09.2026): zentrale Konto-Helfer
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
    r = konten.registrieren(json={
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


# Regel vom 20.09.2026 (Wunsch Ahmad): hoechstens 10 Fotos je Inserat, vorher
# waren es 40. Diese Tests pruefen die GLEICHZEITIGKEIT (kein Lost Update),
# nicht die Hoehe der Grenze — sie richten sich deshalb nach der Einstellung
# statt nach einer fest eingetippten Zahl.
GRENZE = max(1, int(os.environ.get("INSERAT_FOTOS_MAX", "10") or 10))


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

    with concurrent.futures.ThreadPoolExecutor(max_workers=GRENZE) as ex:
        codes = list(ex.map(up, range(GRENZE)))
    assert all(c == 200 for c in codes), codes
    keys = _keys(inserat["lid"])
    assert len(keys) == GRENZE, f"{len(keys)} statt {GRENZE} Referenzen (Lost Update!)"
    # Jede Referenz zeigt auf eine echte Datei
    root = Path(__file__).resolve().parents[1] / "uploads"
    fehlend = [k for k in keys if not (root / k).exists()]
    assert not fehlend, f"DB-Eintraege ohne Datei: {fehlend[:3]}"


def test_parallele_loeschungen_konsistent(inserat):
    """Die Haelfte gleichzeitig loeschen — genau die andere Haelfte bleibt."""
    vorhanden = _keys(inserat["lid"])
    weg = vorhanden[:len(vorhanden) // 2]
    bleibt = len(vorhanden) - len(weg)

    def rm(k):
        return requests.post(
            f"{API}/resale/{inserat['lid']}/photos/remove",
            headers=inserat["h"], json={"key": k}, timeout=60).status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(weg))) as ex:
        codes = list(ex.map(rm, weg))
    assert all(c == 200 for c in codes), codes
    rest = _keys(inserat["lid"])
    assert len(rest) == bleibt, f"{len(rest)} statt {bleibt} uebrig"
    assert not (set(weg) & set(rest)), "geloeschte Keys noch referenziert"


def test_ein_foto_ueber_der_grenze_atomar_abgelehnt(inserat):
    """Bis zur Grenze auffuellen, dann muss das naechste sauber abprallen —
    ohne die schon vorhandenen anzuruehren."""
    fehlen = GRENZE - len(_keys(inserat["lid"]))
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
    assert r.status_code == 400, (
        f"Foto Nr. {GRENZE + 1} durchgerutscht: {r.status_code}")
    assert str(GRENZE) in r.text, "die Meldung nennt die Grenze nicht"
    assert len(_keys(inserat["lid"])) == GRENZE
