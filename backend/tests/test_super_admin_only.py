# -*- coding: utf-8 -*-
"""Abo-Audit 09/2026: Betreiber-Funktionen sind Super-Admin-exklusiv.

Ein NORMALER Admin (role=admin, is_super_admin=false) darf NICHT:
Firmen/Sucher anlegen, Abos freischalten/aufheben, Laufzeiten aendern,
Zahlungen eintragen/einsehen, Marktplatz-Zugang vergeben, Nutzer/Firmen
sperren, Anfragen schliessen. Lesen (Nutzerliste, Detail, Anfragen) bleibt.

Ausserdem: fail-closed Abo-Aufloesung (naive/kaputte Daten, Lifetime
widerrufbar, unbekannter Plan), Firmensperre widerruft Sucher-Sitzungen,
Freischaltung als idempotenter Vorgang (Wiederholung = keine zweite
Zahlung), Historie bleibt erhalten, Zahlungen unveraenderlich.

Braucht laufendes Backend (TEST_BASE_URL, mock ok) + Mongo.
Reihenfolgeabhaengig — immer die ganze Datei laufen lassen.
"""
import asyncio
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
PW = "SuperAdmin123!x"
MAIL = "e2etest-mail.de"


def _db():
    from pymongo import MongoClient
    return MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)[DB_NAME]


def _login(mail):
    r = requests.post(f"{API}/auth/login", json={"email": mail, "password": PW}, timeout=30)
    assert r.status_code == 200, f"Login {mail}: {r.text[:200]}"
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _admin_anlegen(mail, super_admin: bool):
    import bcrypt
    _db().users.insert_one({
        "id": f"sa_{uuid.uuid4().hex[:10]}", "email": mail, "role": "admin", "active": True,
        "dealer_id": None, "is_super_admin": super_admin,
        "password_hash": bcrypt.hashpw(PW.encode(), bcrypt.gensalt()).decode(),
        "created_at": "2026-01-01T00:00:00+00:00"})
    return _login(mail)


@pytest.fixture(scope="module")
def welt():
    z = {}
    yield z
    dbx = _db()
    if z.get("dealer_id"):
        for coll in ("users", "subscriptions", "manual_payments", "plan_requests",
                     "activity_logs", "abo_vorgaenge", "zugangs_aenderungen"):
            dbx[coll].delete_many({"dealer_id": z["dealer_id"]})
        dbx.dealers.delete_many({"id": z["dealer_id"]})
    dbx.users.delete_many({"email": {"$regex": f"_{SUF}@"}})
    dbx.subscriptions.delete_many({"subject_user_id": {"$regex": f"^sa_test_{SUF}"}})


def test_00_aufbau(welt):
    welt["S"] = _admin_anlegen(f"sa_super_{SUF}@{MAIL}", True)      # Super-Admin
    welt["N"] = _admin_anlegen(f"sa_normal_{SUF}@{MAIL}", False)    # normaler Admin
    r = requests.post(f"{API}/admin/users", headers=welt["S"], json={
        "email": f"sa_chef_{SUF}@{MAIL}", "password": PW,
        "company_name": f"SuperAdmin Test {SUF}", "plan_type": "none"}, timeout=30)
    assert r.status_code == 200, r.text[:300]
    welt["dealer_id"], welt["chef_id"] = r.json()["dealer_id"], r.json()["user_id"]
    r = requests.post(f"{API}/admin/dealers/{welt['dealer_id']}/sucher", headers=welt["S"],
                      json={"email": f"sa_sucher_{SUF}@{MAIL}", "password": PW,
                            "first_name": "Sina", "last_name": "Test"}, timeout=30)
    assert r.status_code == 200, r.text[:300]
    welt["sucher_id"] = r.json()["sucher_id"]


# ---------- normaler Admin: alles Schreibende ist zu ----------
def test_01_normaler_admin_darf_nicht_verwalten(welt):
    N, d, s, c = welt["N"], welt["dealer_id"], welt["sucher_id"], welt["chef_id"]
    versuche = [
        ("POST", f"/admin/users", {"email": f"sa_neu_{SUF}@{MAIL}", "password": PW,
                                   "company_name": "X", "plan_type": "none"}),
        ("POST", f"/admin/dealers/{d}/sucher", {"email": f"sa_neu2_{SUF}@{MAIL}", "password": PW,
                                                "first_name": "A", "last_name": "B"}),
        ("POST", f"/admin/sucher/{s}/abo", {"plan": "monthly"}),
        ("POST", f"/admin/sucher/{s}/abo", {"plan": None}),
        ("PATCH", f"/admin/sucher/{s}/abo-gueltig-bis", {"gueltig_bis": "2030-01-01"}),
        ("GET", f"/admin/dealers/{d}/zahlungen", None),
        ("POST", f"/admin/dealers/{d}/zahlungen", {"amount": 10}),
        ("POST", f"/admin/users/{s}/active", {"active": False}),
        ("POST", f"/admin/users/{c}/active", {"active": False}),
        ("POST", f"/admin/users/{s}/password", {"new_password": PW}),
        ("DELETE", f"/admin/users/{s}", None),
        ("PUT", f"/admin/dealers/{d}/sale-plan", {"tier": "s5"}),
        ("PUT", f"/admin/plan-requests/gibtesnicht", {"status": "abgelehnt"}),
        ("POST", f"/admin/buyers/gibtesnicht/access", {"plan": "monthly"}),
        ("POST", f"/admin/cleanup/run", None),
        ("GET", f"/admin/betrieb", None),
    ]
    for methode, pfad, body in versuche:
        r = requests.request(methode, f"{API}{pfad}", headers=N, json=body, timeout=30)
        assert r.status_code == 403, (methode, pfad, r.status_code, r.text[:120])
    # Nichts ist passiert
    dbx = _db()
    assert dbx.users.count_documents({"email": {"$regex": f"^sa_neu"}}) == 0
    assert dbx.subscriptions.count_documents({"subject_user_id": s}) == 0
    assert dbx.users.find_one({"id": s})["active"] is True
    # Lesen bleibt erlaubt
    assert requests.get(f"{API}/admin/users", headers=N, timeout=30).status_code == 200
    assert requests.get(f"{API}/admin/users/{c}/contracts", headers=N, timeout=30).status_code == 200
    assert requests.get(f"{API}/admin/plan-requests", headers=N, timeout=30).status_code == 200
    assert requests.get(f"{API}/admin/dealers/{d}/sucher", headers=N, timeout=30).status_code == 200
    # Super-Admin darf
    assert requests.get(f"{API}/admin/betrieb", headers=welt["S"], timeout=30).status_code == 200


# ---------- Abo-Aufloesung fail-closed ----------
def test_02_abo_aufloesung_fail_closed(welt):
    from deps import sub_status_from_doc
    morgen = (datetime.now(timezone.utc) + timedelta(days=1))
    gestern = (datetime.now(timezone.utc) - timedelta(days=1))
    # naives Datum (ohne Zeitzone) in der Vergangenheit -> abgelaufen (vorher: ewig aktiv)
    assert sub_status_from_doc({"plan": "monthly", "status": "active",
                                "expires_at": gestern.replace(tzinfo=None).isoformat()})["active"] is False
    assert sub_status_from_doc({"plan": "monthly", "status": "active",
                                "expires_at": morgen.replace(tzinfo=None).isoformat()})["active"] is True
    # kaputtes Datum -> inaktiv
    assert sub_status_from_doc({"plan": "monthly", "status": "active", "expires_at": "irgendwann"})["status"] == "ungueltig"
    # befristeter Plan ohne Ablauf -> inaktiv
    assert sub_status_from_doc({"plan": "monthly", "status": "active"})["active"] is False
    # unbekannter Plan mit active ohne Ende -> inaktiv
    assert sub_status_from_doc({"plan": "gold", "status": "active"})["active"] is False
    # Lifetime: aktiv ohne Ende, aber widerrufbar (cancelled + expires_at jetzt)
    assert sub_status_from_doc({"plan": "lifetime", "status": "active"})["active"] is True
    assert sub_status_from_doc({"plan": "lifetime", "status": "cancelled",
                                "expires_at": gestern.isoformat()})["active"] is False
    assert sub_status_from_doc({"plan": "lifetime", "status": "suspended"})["active"] is False
    # ersetzte Historie zaehlt nie
    assert sub_status_from_doc({"plan": "yearly", "status": "ersetzt",
                                "expires_at": morgen.isoformat()})["active"] is False


def test_03_lifetime_aufheben_sperrt_wirklich(welt):
    dbx = _db()
    sid = welt["sucher_id"]
    dbx.subscriptions.insert_one({
        "id": f"sa_test_{SUF}_lt", "dealer_id": welt["dealer_id"], "subject_user_id": sid,
        "plan": "lifetime", "status": "active", "expires_at": None,
        "created_at": datetime.now(timezone.utc).isoformat()})
    S = _login(f"sa_sucher_{SUF}@{MAIL}")
    r = requests.post(f"{API}/mobile/compare", headers=S,
                      json={"url": "https://www.kleinanzeigen.de/s-anzeige/x/9600000077-216-1"}, timeout=60)
    assert r.status_code != 402, "Lifetime sollte aktiv sein"
    r = requests.post(f"{API}/admin/sucher/{sid}/abo", headers=welt["S"], json={"plan": None}, timeout=30)
    assert r.status_code == 200
    r = requests.post(f"{API}/mobile/compare", headers=S,
                      json={"url": "https://www.kleinanzeigen.de/s-anzeige/x/9600000078-216-1"}, timeout=60)
    assert r.status_code == 402, "Aufgehobenes Lifetime-Abo muss sperren"
    dbx.subscriptions.delete_many({"id": f"sa_test_{SUF}_lt"})


# ---------- Plantyp fest, Zahlung validiert ----------
def test_04_plantyp_und_zahlung_validiert(welt):
    S, sid = welt["S"], welt["sucher_id"]
    r = requests.post(f"{API}/admin/users", headers=S, json={
        "email": f"sa_lt_{SUF}@{MAIL}", "password": PW, "company_name": "LT",
        "plan_type": "lifetime"}, timeout=30)
    assert r.status_code == 422, r.text[:200]          # lifetime nicht mehr anlegbar
    r = requests.post(f"{API}/admin/users", headers=S, json={
        "email": f"sa_lt_{SUF}@{MAIL}", "password": PW, "company_name": "LT",
        "plan_type": "gold"}, timeout=30)
    assert r.status_code == 422
    # 0 EUR / negativ / unbekannter Plan / Kulanz ohne Grund / falsches Datum
    for body in ({"plan": "monthly", "betrag": 0}, {"plan": "monthly", "betrag": -5},
                 {"plan": "gold"}, {"plan": "monthly", "zahlungsart": "kulanz"},
                 {"plan": "monthly", "gezahlt_am": "01.09.2026"}):
        r = requests.post(f"{API}/admin/sucher/{sid}/abo", headers=S, json=body, timeout=30)
        assert r.status_code in (400, 422), (body, r.status_code, r.text[:120])
    assert _db().manual_payments.count_documents({"subject_user_id": sid}) == 0
    # schwaches Passwort beim Anlegen -> 422 (zentrale Regel)
    r = requests.post(f"{API}/admin/users", headers=S, json={
        "email": f"sa_pw_{SUF}@{MAIL}", "password": "passwort1", "company_name": "PW",
        "plan_type": "none"}, timeout=30)
    assert r.status_code == 422, r.text[:200]


# ---------- Freischaltung = idempotenter Vorgang, Historie bleibt ----------
def test_05_vorgang_idempotent_und_historie(welt):
    dbx = _db()
    S, sid = welt["S"], welt["sucher_id"]
    r = requests.post(f"{API}/admin/sucher/{sid}/abo", headers=S,
                      json={"plan": "monthly", "betrag": 150}, timeout=30)
    assert r.status_code == 200, r.text[:300]
    vid = r.json()["vorgang_id"]
    v = dbx.abo_vorgaenge.find_one({"id": vid})
    assert v["status"] == "fertig"
    assert dbx.manual_payments.count_documents({"vorgang_id": vid}) == 1
    assert dbx.subscriptions.find_one({"id": vid})["status"] == "active"
    # Wiederholung desselben Vorgangs (Reparaturlauf) -> nichts doppelt
    from routes import admin as A
    import deps

    async def wiederholen():
        from motor.motor_asyncio import AsyncIOMotorClient
        cl = AsyncIOMotorClient(MONGO_URL)
        alt = (A.db, deps.db)
        A.db = deps.db = cl[DB_NAME]
        try:
            v2 = await A.db.abo_vorgaenge.find_one({"id": vid}, {"_id": 0})
            await A._abo_vorgang_ausfuehren(v2)
            await A._abo_vorgang_ausfuehren(v2)
        finally:
            A.db, deps.db = alt
            cl.close()
    asyncio.run(wiederholen())
    assert dbx.manual_payments.count_documents({"vorgang_id": vid}) == 1
    assert dbx.subscriptions.count_documents({"id": vid}) == 1
    # Zweite Freischaltung: alte Zeile bleibt als Historie ("ersetzt"), nicht geloescht
    r = requests.post(f"{API}/admin/sucher/{sid}/abo", headers=S, json={"plan": "yearly"}, timeout=30)
    assert r.status_code == 200
    assert dbx.subscriptions.count_documents({"subject_user_id": sid}) == 2
    assert dbx.subscriptions.find_one({"id": vid})["status"] == "ersetzt"
    # Kulanz mit Grund: Zahlung 0 EUR, kostenlos markiert
    r = requests.post(f"{API}/admin/sucher/{sid}/abo", headers=S,
                      json={"plan": "monthly", "zahlungsart": "kulanz", "grund": "Testphase"}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    z = dbx.manual_payments.find_one({"vorgang_id": r.json()["vorgang_id"]})
    assert z["amount"] == 0.0 and z["kostenlos"] is True and z["grund"] == "Testphase"


def test_06_gueltig_bis_aendert_keine_zahlung(welt):
    dbx = _db()
    S, sid = welt["S"], welt["sucher_id"]
    vorher = [z["period_until"] for z in dbx.manual_payments.find({"subject_user_id": sid})]
    r = requests.patch(f"{API}/admin/sucher/{sid}/abo-gueltig-bis", headers=S,
                       json={"gueltig_bis": "2027-06-30", "grund": "Kulanzverlaengerung"}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    nachher = [z["period_until"] for z in dbx.manual_payments.find({"subject_user_id": sid})]
    assert vorher == nachher, "Zahlungshistorie darf nicht veraendert werden"
    ae = dbx.zugangs_aenderungen.find_one({"subject_user_id": sid}, sort=[("created_at", -1)])
    assert ae and ae["neu"].startswith("2027-06-30") and ae["grund"] == "Kulanzverlaengerung"
    assert ae["alt"] and ae["admin_email"]


# ---------- Firmensperre widerruft Sucher-Sitzungen ----------
def test_07_firmensperre_widerruft_sucher_sitzungen(welt):
    dbx = _db()
    S = welt["S"]
    su = _login(f"sa_sucher_{SUF}@{MAIL}")
    assert requests.get(f"{API}/contracts", headers=su, timeout=30).status_code == 200
    r = requests.post(f"{API}/admin/users/{welt['chef_id']}/active", headers=S,
                      json={"active": False}, timeout=30)
    assert r.status_code == 200 and r.json()["sucher_abgemeldet"] >= 1, r.text[:200]
    assert dbx.users.find_one({"id": welt["sucher_id"]})["current_session_id"] is None
    assert requests.get(f"{API}/contracts", headers=su, timeout=30).status_code in (401, 403)
    # Entsperren: altes Token bleibt ungueltig (Sitzung wurde widerrufen)
    requests.post(f"{API}/admin/users/{welt['chef_id']}/active", headers=S, json={"active": True}, timeout=30)
    assert requests.get(f"{API}/contracts", headers=su, timeout=30).status_code == 401
