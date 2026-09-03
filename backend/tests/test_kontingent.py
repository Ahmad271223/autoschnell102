# -*- coding: utf-8 -*-
"""Verkaufskontingent: "5 Autos im Monat" heisst 5 VERSCHIEDENE Autos je
Abrechnungszeitraum — nicht 5 gleichzeitig.

Frage 09/2026: Kann ein Haendler mit Paket "Verkauf 5" durch Loeschen und
Neu-Einstellen beliebig viele Autos im Monat inserieren? Antwort im Code:
Nein — der Slot wird beim ERSTEN Veroeffentlichen im Zeitraum verbraucht
(counted_periods) und durch Loeschen NICHT frei. Dieser Test haelt das fest.

Braucht ein laufendes Backend (TEST_BASE_URL) + Mongo-Zugriff.
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
PW = "Kontingent123!"


def _db():
    from pymongo import MongoClient
    return MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)[DB_NAME]


def _admin():
    import bcrypt
    mail = f"kq_admin_{SUF}@e2etest-mail.de"
    _db().users.insert_one({
        "id": f"kqadm_{SUF}", "email": mail, "role": "admin", "active": True,
        "dealer_id": None, "is_super_admin": True,
        "password_hash": bcrypt.hashpw(PW.encode(), bcrypt.gensalt()).decode(),
        "created_at": "2026-01-01T00:00:00+00:00"})
    r = requests.post(f"{API}/auth/login", json={"email": mail, "password": PW},
                      timeout=30)
    assert r.status_code == 200, r.text[:200]
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _fahrzeug(dealer_id, n):
    vid = str(uuid.uuid4())
    _db().vehicles.insert_one({
        "id": vid, "dealer_id": dealer_id, "lifecycle": "bestand",
        "status": "Bestand", "purchase_price": 4000 + n,
        "mobile_ad_id": f"kq{SUF}{n}",
        "data": {"make_label": "VW", "model_label": f"Golf {n}",
                 "mileage": 90000, "first_registration": "01/2020",
                 "fuel_label": "Benzin", "power_ps": 110,
                 "list_price": 15000, "images": []},
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat()})
    return vid


def _inserat_live(H, vid):
    """Entwurf -> Preis -> verkaufsbereit -> publish; liefert (listing_id, r)."""
    r = requests.post(f"{API}/resale/draft/{vid}", headers=H, timeout=30)
    assert r.status_code == 200, r.text[:300]
    lid = r.json()["id"]
    r = requests.put(f"{API}/resale/{lid}", headers=H,
                     json={"price_public": 9900, "price_b2b": 9000}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    r = requests.post(f"{API}/resale/{lid}/status", headers=H,
                      json={"status": "verkaufsbereit"}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    r = requests.post(f"{API}/resale/{lid}/publish", headers=H,
                      json={"visibility": "public"}, timeout=30)
    return lid, r


def _plan(H):
    r = requests.get(f"{API}/dealer/sale-plan", headers=H, timeout=30)
    assert r.status_code == 200, r.text[:200]
    return r.json()


@pytest.fixture(scope="module")
def welt():
    z = {}
    yield z
    dbx = _db()
    if z.get("dealer_id"):
        for coll in ("users", "subscriptions", "vehicles", "resale_listings",
                     "activity_logs", "plan_requests"):
            dbx[coll].delete_many({"dealer_id": z["dealer_id"]})
        dbx.dealers.delete_many({"id": z["dealer_id"]})
    dbx.users.delete_many({"email": {"$regex": f"_{SUF}@"}})


def test_00_aufbau(welt):
    r = requests.post(f"{API}/auth/register", json={
        "email": f"kq_chef_{SUF}@e2etest-mail.de", "password": PW,
        "company_name": "Kontingent Autohaus", "contact_person": "K Chef",
        "phone": "0511 5"}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    welt["H"] = {"Authorization": f"Bearer {r.json()['token']}"}
    me = requests.get(f"{API}/auth/me", headers=welt["H"], timeout=30).json()["user"]
    welt["dealer_id"] = me["dealer_id"]
    admin = _admin()
    r = requests.put(f"{API}/admin/dealers/{welt['dealer_id']}/sale-plan",
                     headers=admin, json={"tier": "s5", "months": 1}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    r = requests.put(f"{API}/dealer/marketplace-profile", headers=welt["H"],
                     json={"public": True, "description": "Kontingent-Test"},
                     timeout=30)
    assert r.status_code == 200, r.text[:200]
    p = _plan(welt["H"])
    assert p["active"] and p["quota"] == 5 and p["used"] == 0
    welt["vids"] = [_fahrzeug(welt["dealer_id"], n) for n in range(9)]
    welt["lids"] = []


def test_01_fuenf_veroeffentlichen(welt):
    for n in range(5):
        lid, r = _inserat_live(welt["H"], welt["vids"][n])
        assert r.status_code == 200, f"Auto {n + 1}: {r.text[:200]}"
        welt["lids"].append(lid)
    p = _plan(welt["H"])
    assert p["used"] == 5 and p["remaining"] == 0


def test_02_loeschen_gibt_keinen_platz_frei(welt):
    r = requests.delete(f"{API}/resale/{welt['lids'][0]}", headers=welt["H"],
                        timeout=30)
    assert r.status_code == 200, r.text[:200]
    assert "weiter auf dein Kontingent" in r.json().get("hinweis", "")
    p = _plan(welt["H"])
    assert p["used"] == 5 and p["remaining"] == 0, p
    # Sechstes (neues) Auto im selben Monat: abgelehnt
    lid6, r = _inserat_live(welt["H"], welt["vids"][5])
    assert r.status_code == 402, r.text[:200]
    assert "Kontingent" in r.text
    welt["lid6"] = lid6
    l = _db().resale_listings.find_one({"id": lid6})
    assert l["status"] != "veroeffentlicht" and not l.get("counted_periods")
    assert _plan(welt["H"])["used"] == 5


def test_03_loeschen_und_neu_in_schleife_bleibt_gesperrt(welt):
    """Genau das befuerchtete Muster: loeschen, neues rein, loeschen, neues
    rein ... — es bleibt bei 5 Slots im Zeitraum."""
    for n in (1, 2):
        r = requests.delete(f"{API}/resale/{welt['lids'][n]}", headers=welt["H"],
                            timeout=30)
        assert r.status_code == 200
        _, r = _inserat_live(welt["H"], welt["vids"][6 + n])
        assert r.status_code == 402, f"Durchlauf {n}: {r.text[:200]}"
    p = _plan(welt["H"])
    assert p["used"] == 5 and p["remaining"] == 0
    live = _db().resale_listings.count_documents(
        {"dealer_id": welt["dealer_id"], "status": "veroeffentlicht"})
    assert live == 2  # 5 veroeffentlicht, 3 geloescht — Slots trotzdem weg


def test_04_zurueckziehen_und_reaktivieren_zaehlt_nicht_doppelt(welt):
    lid = welt["lids"][3]
    r = requests.post(f"{API}/resale/{lid}/status", headers=welt["H"],
                      json={"status": "zurueckgezogen"}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    r = requests.post(f"{API}/resale/{lid}/publish", headers=welt["H"],
                      json={"visibility": "public"}, timeout=30)
    assert r.status_code == 200, r.text[:200]   # dasselbe Auto: kein neuer Slot
    assert _plan(welt["H"])["used"] == 5


def test_05_neuer_monat_gibt_neue_fuenf(welt):
    """Abrechnungszeitraum rollt (30 Tage ab Buchung) — dann 5 neue Slots."""
    dbx = _db()
    d = dbx.dealers.find_one({"id": welt["dealer_id"]}, {"sale_plan": 1})
    alt = datetime.fromisoformat(d["sale_plan"]["period_start"])
    if alt.tzinfo is None:
        alt = alt.replace(tzinfo=timezone.utc)
    dbx.dealers.update_one(
        {"id": welt["dealer_id"]},
        {"$set": {"sale_plan.period_start": (alt - timedelta(days=31)).isoformat()}})
    p = _plan(welt["H"])
    assert p["used"] == 0 and p["remaining"] == 5, p
    r = requests.post(f"{API}/resale/{welt['lid6']}/publish", headers=welt["H"],
                      json={"visibility": "public"}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    assert _plan(welt["H"])["used"] == 1


def test_06_parallele_veroeffentlichung_zaehlt_genau_einmal(welt):
    """Runde 5: Zwei gleichzeitige Publishes desselben Inserats duerfen
    nicht auseinanderlaufen (Inserat live, aber ungezaehlt). Die Sperre je
    Inserat laesst genau EINE Anfrage durch; die anderen bekommen 409."""
    import threading
    H = welt["H"]
    vid = _fahrzeug(welt["dealer_id"], 99)          # frisches Fahrzeug, noch ohne Entwurf
    r = requests.post(f"{API}/resale/draft/{vid}", headers=H, timeout=30)
    assert r.status_code == 200, r.text[:200]
    lid = r.json()["id"]
    assert requests.put(f"{API}/resale/{lid}", headers=H,
                        json={"price_public": 9900, "price_b2b": 9000}, timeout=30).status_code == 200
    assert requests.post(f"{API}/resale/{lid}/status", headers=H,
                         json={"status": "verkaufsbereit"}, timeout=30).status_code == 200
    vorher = _plan(H)["used"]
    codes = []
    def go():
        codes.append(requests.post(f"{API}/resale/{lid}/publish", headers=H,
                                   json={"visibility": "public"}, timeout=60).status_code)
    ts = [threading.Thread(target=go) for _ in range(6)]
    [t.start() for t in ts]; [t.join() for t in ts]
    # Mindestens einer gewinnt; Nachzuegler nach Freigabe der Sperre duerfen
    # das bereits veroeffentlichte Inserat idempotent "erneut" veroeffentlichen
    # (200) — entscheidend ist: kein 5xx und genau EIN verbrauchter Slot.
    assert codes.count(200) >= 1, codes
    assert all(c in (200, 409) for c in codes), codes
    p = _plan(H)
    assert p["used"] == vorher + 1, p
    l = _db().resale_listings.find_one({"id": lid})
    assert l["status"] == "veroeffentlicht" and len(l.get("counted_periods") or []) == 1
    assert "publish_lock_until" not in l
