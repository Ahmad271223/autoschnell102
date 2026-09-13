# -*- coding: utf-8 -*-
"""Mandantentrennung (Priorität 3): Firma B darf NICHTS von Firma A sehen.

Geprueft werden Fahrzeug, Vertrag, Vertrags-PDF, Termin, Inserat,
Fahrer-Zugriffe (fremder Termin/Protokoll/PDF) und die Sucherverwaltung.
Zahlungs-Trennung deckt test_smoke.py::test_fremde_zahlung_unsichtbar ab.

Braucht ein laufendes Backend MIT MOCK_PROVIDER_FETCH=true (wie in CI).
"""
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
PW = "Tenant123!"
KA_ID = f"95{uuid.uuid4().int % 10**8:08d}"
KA_URL = f"https://www.kleinanzeigen.de/s-anzeige/tenant/{KA_ID}-216-1"


def _db():
    from pymongo import MongoClient
    return MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)[DB_NAME]


def _firma(name):
    r = requests.post(f"{API}/auth/register", json={
        "email": f"tenant_{name}_{SUF}@e2etest-mail.de", "password": PW,
        "company_name": f"Tenant {name}", "contact_person": name,
        "phone": "0511 7"}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    tok = r.json()["token"]
    me = requests.get(f"{API}/auth/me",
                      headers={"Authorization": f"Bearer {tok}"},
                      timeout=30).json()["user"]
    _db().subscriptions.insert_one({
        "id": str(uuid.uuid4()), "dealer_id": me["dealer_id"],
        "subject_user_id": me["id"], "plan": "monthly", "status": "active",
        "expires_at": (datetime.now(timezone.utc)
                       + timedelta(days=1)).isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat()})
    return {"me": me, "h": {"Authorization": f"Bearer {tok}"}}


@pytest.fixture(scope="module")
def welt():
    a, b = _firma("A"), _firma("B")
    h = a["h"]
    # Firma A: Fahrzeug + Vertrag + Termin + Fahrer + Inserat aufbauen
    r = requests.post(f"{API}/mobile/compare", json={"url": KA_URL},
                      headers=h, timeout=90)
    if r.status_code != 200 or not (r.json().get("vehicle") or {}).get("_mock"):
        pytest.skip("Backend ohne MOCK_PROVIDER_FETCH")
    vehicle_id = r.json()["vehicle_id"]
    r = requests.post(f"{API}/contracts", headers=h, json={
        "vehicle_id": vehicle_id, "seller_name": "T Verkaeufer",
        "seller_address": "Weg 1", "seller_zip": "30159",
        "seller_city": "Hannover", "purchase_price": 9000,
        "pickup_date": "2099-05-01", "pickup_time": "10:00"}, timeout=90)
    assert r.status_code == 200, r.text[:200]
    contract_id = r.json()["id"]
    appt = next(x for x in requests.get(f"{API}/appointments", headers=h,
                                        timeout=30).json()
                if x.get("contract_id") == contract_id)

    r = requests.post(f"{API}/driver/register", json={
        "email": f"tenant_drvA_{SUF}@e2etest-mail.de", "password": PW,
        "display_name": "Fahrer A"}, timeout=30).json()
    requests.post(f"{API}/drivers/add", headers=h,
                  json={"driver_code": r["driver"]["driver_code"]}, timeout=30)
    requests.put(f"{API}/appointments/{appt['id']}", headers=h,
                 json={"driver_id": r["driver"]["id"]}, timeout=60)

    # Fremder Fahrer (nirgends zugeteilt)
    fremd = requests.post(f"{API}/driver/register", json={
        "email": f"tenant_drvB_{SUF}@e2etest-mail.de", "password": PW,
        "display_name": "Fahrer B"}, timeout=30).json()

    z = {"a": a, "b": b, "vehicle_id": vehicle_id,
         "contract_id": contract_id, "appt_id": appt["id"],
         "drv_fremd": {"Authorization": f"Bearer {fremd['token']}"}}
    yield z
    dbx = _db()
    for f in (a, b):
        did = f["me"]["dealer_id"]
        for coll in ("subscriptions", "vehicles", "appointments",
                     "generated_pdfs", "resale_listings", "pickup_protocols",
                     "dealer_drivers"):
            dbx[coll].delete_many({"dealer_id": did})
        dbx.dealers.delete_many({"id": did})
    dbx.users.delete_many({"email": {"$regex": f"_{SUF}@"}})
    dbx.driver_accounts.delete_many({"email": {"$regex": f"_{SUF}@"}})
    dbx.listings_cache.delete_many({"item_id": KA_ID})


def test_fremdes_fahrzeug_unsichtbar(welt):
    hb = welt["b"]["h"]
    r = requests.get(f"{API}/bestand", headers=hb, timeout=30)
    assert r.status_code == 200
    body = r.json()
    fahrzeuge = body.get("items") or body.get("vehicles") or []
    assert welt["vehicle_id"] not in [
        v.get("id") for v in fahrzeuge if isinstance(v, dict)]


def test_fremder_vertrag_gesperrt(welt):
    hb = welt["b"]["h"]
    for pfad in (f"/contracts/{welt['contract_id']}",
                 f"/contracts/{welt['contract_id']}/pdf",
                 f"/contracts/{welt['contract_id']}/versions"):
        r = requests.get(f"{API}{pfad}", headers=hb, timeout=30)
        assert r.status_code == 404, f"{pfad}: {r.status_code}"


def test_fremder_termin_gesperrt(welt):
    hb = welt["b"]["h"]
    appts = requests.get(f"{API}/appointments", headers=hb, timeout=30).json()
    assert welt["appt_id"] not in [a["id"] for a in appts]
    # Termin von B darf kein Fahrzeug/Vertrag von A referenzieren
    r = requests.post(f"{API}/appointments", headers=hb, json={
        "vehicle_id": welt["vehicle_id"], "pickup_date": "2099-05-02",
        "seller_name": "X"}, timeout=30)
    assert r.status_code in (404, 422), f"Fremdreferenz erlaubt: {r.status_code}"


def test_fremder_fahrer_ohne_zugriff(welt):
    d = welt["drv_fremd"]
    r = requests.get(f"{API}/driver/appointments", headers=d, timeout=30)
    assert r.status_code == 200 and welt["appt_id"] not in [
        a["id"] for a in r.json()]
    for pfad in (f"/driver/appointments/{welt['appt_id']}/pickup-order.pdf",
                 f"/driver/appointments/{welt['appt_id']}/protocol",
                 f"/driver/contracts/{welt['contract_id']}/pdf"):
        r = requests.get(f"{API}{pfad}", headers=d, timeout=30)
        assert r.status_code == 404, f"{pfad}: {r.status_code}"


def test_fremdes_inserat_nicht_bearbeitbar(welt):
    ha, hb = welt["a"]["h"], welt["b"]["h"]
    r = requests.post(f"{API}/resale/draft/{welt['vehicle_id']}",
                      headers=ha, timeout=30)
    assert r.status_code == 200, r.text[:200]
    listing_id = r.json()["id"]
    r = requests.put(f"{API}/resale/{listing_id}", headers=hb,
                     json={"price_public": 1}, timeout=30)
    assert r.status_code == 404, f"Fremd-Inserat bearbeitbar: {r.status_code}"
    r = requests.post(f"{API}/resale/{listing_id}/publish", headers=hb,
                      json={"visibility": "public"}, timeout=30)
    assert r.status_code == 404, f"Fremd-Inserat publizierbar: {r.status_code}"


def test_fremde_sucher_unsichtbar(welt):
    ha, hb = welt["a"]["h"], welt["b"]["h"]
    r = requests.post(f"{API}/dealer/sucher", headers=ha, json={
        "email": f"tenant_sucher_{SUF}@e2etest-mail.de", "password": PW,
        "first_name": "T", "last_name": "S"}, timeout=30)
    assert r.status_code == 200
    sid = r.json()["sucher_id"]
    liste_b = requests.get(f"{API}/dealer/sucher", headers=hb,
                           timeout=30).json()
    assert sid not in [s["id"] for s in liste_b]
    r = requests.delete(f"{API}/dealer/sucher/{sid}", headers=hb, timeout=30)
    assert r.status_code == 404, f"Fremden Sucher geloescht: {r.status_code}"
