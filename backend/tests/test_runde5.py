# -*- coding: utf-8 -*-
"""Pruefbericht Runde 5 — Negativ- und Regressionstests.

- Admin-Konten: normaler Admin darf andere Admins weder sperren noch
  entsperren (nur der Super-Admin).
- Fahrer-Logout widerruft den Token serverseitig.
- Snapshots: nur die eigene Firma (oder Admin) sieht Status/Datei.
- Client-Fehlerberichte: Query/Fragment (Reset-Token, Stripe-Session)
  landen nie im Fehlerarchiv.
- Seeds reaktivieren gesperrte Admin-Konten nicht (Funktionsaufruf).

Braucht laufendes Backend (TEST_BASE_URL) + Mongo-Zugriff.
"""
import asyncio
import os
import sys
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
PW = "Runde5Test123!"


def _db():
    from pymongo import MongoClient
    return MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)[DB_NAME]


def _login(mail):
    r = requests.post(f"{API}/auth/login", json={"email": mail, "password": PW},
                      timeout=30)
    assert r.status_code == 200, f"Login {mail}: {r.text[:200]}"
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _admin(name, **extra):
    import bcrypt
    mail = f"r5_{name}_{SUF}@e2etest-mail.de"
    _db().users.insert_one({
        "id": f"r5{name}_{SUF}", "email": mail, "role": "admin", "active": True,
        "dealer_id": None,
        "password_hash": bcrypt.hashpw(PW.encode(), bcrypt.gensalt()).decode(),
        "created_at": "2026-01-01T00:00:00+00:00", **extra})
    return mail


@pytest.fixture(scope="module")
def welt():
    z = {}
    yield z
    dbx = _db()
    for did in (z.get("dealer_a"), z.get("dealer_b")):
        if did:
            for coll in ("users", "vehicles", "listing_snapshots", "subscriptions", "dealers"):
                dbx[coll].delete_many({"dealer_id": did} if coll != "dealers" else {"id": did})
    dbx.users.delete_many({"email": {"$regex": f"_{SUF}@"}})
    dbx.driver_accounts.delete_many({"email": {"$regex": f"_{SUF}@"}})
    dbx.listing_snapshots.delete_many({"id": {"$regex": f"^r5snap_{SUF}"}})
    dbx.error_logs.delete_many({"message": {"$regex": f"r5fehler_{SUF}"}})


def test_00_aufbau(welt):
    welt["sa_mail"] = _admin("sa", is_super_admin=True)
    welt["a1_mail"] = _admin("a1")
    welt["a2_mail"] = _admin("a2")
    welt["SA"], welt["A1"] = _login(welt["sa_mail"]), _login(welt["a1_mail"])
    welt["a2_id"] = f"r5a2_{SUF}"
    for k in ("a", "b"):
        r = requests.post(f"{API}/auth/register", json={
            "email": f"r5_chef{k}_{SUF}@e2etest-mail.de", "password": PW,
            "company_name": f"R5 Autohaus {k.upper()}", "contact_person": "R5",
            "phone": "0511 5"}, timeout=30)
        assert r.status_code == 200, r.text[:200]
        welt[f"H{k.upper()}"] = {"Authorization": f"Bearer {r.json()['token']}"}
        me = requests.get(f"{API}/auth/me", headers=welt[f"H{k.upper()}"],
                          timeout=30).json()["user"]
        welt[f"dealer_{k}"] = me["dealer_id"]


def test_01_admin_sperrt_keine_anderen_admins(welt):
    url = f"{API}/admin/users/{welt['a2_id']}/active"
    r = requests.post(url, headers=welt["A1"], json={"active": False}, timeout=30)
    assert r.status_code == 403, r.text[:200]
    r = requests.post(url, headers=welt["A1"], json={"active": True}, timeout=30)
    assert r.status_code == 403, r.text[:200]
    assert _db().users.find_one({"id": welt["a2_id"]})["active"] is True
    # Super-Admin darf; danach wieder entsperren
    r = requests.post(url, headers=welt["SA"], json={"active": False}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    r = requests.post(url, headers=welt["SA"], json={"active": True}, timeout=30)
    assert r.status_code == 200, r.text[:200]


def test_02_fahrer_logout_widerruft_token(welt):
    r = requests.post(f"{API}/driver/register", json={
        "email": f"r5_fahrer_{SUF}@e2etest-mail.de", "password": PW,
        "display_name": "R5 Fahrer"}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    D = {"Authorization": f"Bearer {r.json()['token']}"}
    assert requests.get(f"{API}/driver/me", headers=D, timeout=30).status_code == 200
    assert requests.post(f"{API}/driver/logout", headers=D, timeout=30).status_code == 200
    r = requests.get(f"{API}/driver/me", headers=D, timeout=30)
    assert r.status_code == 401, f"kopierter Token nach Logout noch gueltig: {r.status_code}"


def test_03_snapshot_nur_eigene_firma(welt):
    dbx = _db()
    vid = f"v_r5_{SUF}"
    dbx.vehicles.insert_one({"id": vid, "dealer_id": welt["dealer_a"],
                             "lifecycle": "bestand", "data": {"make_label": "VW"},
                             "created_at": "2026-09-01T00:00:00+00:00"})
    sid = f"r5snap_{SUF}"
    dbx.listing_snapshots.insert_one({
        "id": sid, "dealer_id": welt["dealer_a"], "vehicle_id": vid,
        "status": "ready", "png_path": "x/y.png", "pdf_path": "x/y.pdf",
        "created_at": "2026-09-01T00:00:00+00:00"})
    assert requests.get(f"{API}/snapshots/{sid}", headers=welt["HA"], timeout=30).status_code == 200
    r = requests.get(f"{API}/snapshots/{sid}", headers=welt["HB"], timeout=30)
    assert r.status_code == 404, f"fremde Firma sieht Snapshot: {r.status_code}"
    r = requests.get(f"{API}/snapshots/{sid}/pdf", headers=welt["HB"], timeout=30)
    assert r.status_code == 404, r.status_code
    assert requests.get(f"{API}/snapshots/{sid}", headers=welt["SA"], timeout=30).status_code == 200


def test_04_client_errors_ohne_query(welt):
    marker = f"r5fehler_{SUF}"
    r = requests.post(f"{API}/client-errors", json={
        "message": marker, "url": "https://app.example/passwort-reset?token=GEHEIM123#frag"},
        timeout=30)
    assert r.status_code == 200
    doc = _db().error_logs.find_one({"message": marker})
    assert doc is not None
    assert "GEHEIM123" not in (doc.get("path") or "") and "?" not in (doc.get("path") or "")
    assert doc["path"] == "https://app.example/passwort-reset"


def test_05_seed_reaktiviert_gesperrten_admin_nicht(welt):
    """seed_admin()/seed_super_admin() setzen 'active' nicht mehr auf True."""
    import server  # noqa: F401  (Modul laedt .env; Funktionen direkt aufrufen)
    dbx = _db()
    mail = os.environ.get("ADMIN_EMAIL", "")
    if not mail:
        pytest.skip("ADMIN_EMAIL nicht gesetzt")
    u = dbx.users.find_one({"email": mail})
    if not u:
        pytest.skip("Bootstrap-Admin nicht vorhanden")
    vorher = u.get("active", True)
    dbx.users.update_one({"email": mail}, {"$set": {"active": False}})
    try:
        asyncio.run(server.seed_admin())
        assert dbx.users.find_one({"email": mail})["active"] is False
    finally:
        dbx.users.update_one({"email": mail}, {"$set": {"active": vorher}})


def test_06_chef_sperre_kaskadiert_auf_sucher(welt):
    """Review 09/2026: gesperrter Haendler-Hauptaccount = gesperrte Firma."""
    dbx = _db()
    r = requests.post(f"{API}/dealer/sucher", headers=welt["HA"], json={
        "email": f"r5_sucher_{SUF}@e2etest-mail.de", "password": PW,
        "first_name": "R5", "last_name": "Sucher"}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    S = _login(f"r5_sucher_{SUF}@e2etest-mail.de")
    assert requests.get(f"{API}/auth/me", headers=S, timeout=30).status_code == 200
    chef = dbx.users.find_one({"dealer_id": welt["dealer_a"], "role": "dealer"})
    r = requests.post(f"{API}/admin/users/{chef['id']}/active", headers=welt["SA"],
                      json={"active": False}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    r = requests.get(f"{API}/auth/me", headers=S, timeout=30)
    assert r.status_code == 403, f"Sucher trotz gesperrter Firma aktiv: {r.status_code}"
    r = requests.post(f"{API}/admin/users/{chef['id']}/active", headers=welt["SA"],
                      json={"active": True}, timeout=30)
    assert r.status_code == 200
    welt["HA"] = _login(f"r5_chefa_{SUF}@e2etest-mail.de")   # Chef-Session wurde beim Sperren beendet
    S = _login(f"r5_sucher_{SUF}@e2etest-mail.de")
    assert requests.get(f"{API}/auth/me", headers=S, timeout=30).status_code == 200
    welt["S"] = S


def test_07_abo_anzeige_zeigt_eigenes_abo(welt):
    """Review 09/2026: /dealer/subscription zeigte das NEUESTE Abo der Firma
    (auch Sucher-Abos) — jetzt das eigene."""
    from datetime import datetime, timedelta, timezone
    import uuid as _uuid
    dbx = _db()
    sucher = dbx.users.find_one({"email": f"r5_sucher_{SUF}@e2etest-mail.de"})
    chef = dbx.users.find_one({"dealer_id": welt["dealer_a"], "role": "dealer"})
    now = datetime.now(timezone.utc)
    dbx.subscriptions.insert_one({
        "id": str(_uuid.uuid4()), "dealer_id": welt["dealer_a"],
        "subject_user_id": chef["id"], "plan": "yearly", "status": "active",
        "expires_at": (now + timedelta(days=300)).isoformat(),
        "created_at": (now - timedelta(days=2)).isoformat()})
    dbx.subscriptions.insert_one({
        "id": str(_uuid.uuid4()), "dealer_id": welt["dealer_a"],
        "subject_user_id": sucher["id"], "plan": "monthly", "status": "active",
        "expires_at": (now + timedelta(days=10)).isoformat(),
        "created_at": now.isoformat()})          # neuer als das Chef-Abo
    r = requests.get(f"{API}/dealer/subscription", headers=welt["HA"], timeout=30)
    assert r.status_code == 200, r.text[:200]
    assert r.json()["plan"] == "yearly" and r.json()["days_remaining"] > 200, r.json()
    r = requests.get(f"{API}/dealer/subscription", headers=welt["S"], timeout=30)
    assert r.status_code == 200 and r.json()["plan"] == "monthly", r.text[:200]
    # Admin-Nutzerliste: Sucher-Zeile zeigt das persoenliche Abo
    r = requests.get(f"{API}/admin/users", headers=welt["SA"], params={"limit": 200}, timeout=30)
    assert r.status_code == 200
    zeile = next((u for u in r.json() if u.get("id") == sucher["id"]), None)
    assert zeile and (zeile.get("subscription") or {}).get("plan") == "monthly", zeile
