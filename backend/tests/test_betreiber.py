# -*- coding: utf-8 -*-
"""Betreiber-Modell 09/2026 (Commit 'feat(betreiber-modell)') — Tests.

Abgedeckt:
- Oeffentliche Zugangs-Anfrage (landet als plan_request type=zugang)
- Firma anlegen mit plan_type "none" (Hauptaccount ohne Abo)
- Sucher-Konten legt der Betreiber an (anlegen, Duplikat 409, Login,
  Liste mit Abo-Status und naechster Zahlung)
- Freischalten erfasst die Zahlung (manual_payments) und schliesst die
  offene Anfrage; funktioniert auch fuer den Chef (dealer)
- Zahlungen je Firma einsehen/nachtragen
- Chef-Abo-Anfrage (POST /dealer/abo-anfrage-selbst)
- Stripe nur fuer Marktplatz-Kaeufer: Firmen-Checkout 403, unbekannter
  Plan 400; Aktivierung 'marktplatz' verlaengert um 30 Tage ab Ablauf,
  Alt-Plan 'monthly' erzeugt weiterhin ein Abo (Funktionstest)
- Kaeufer-Zugangspreis 20 EUR

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
PW = "Betreiber123!x"


def _db():
    from pymongo import MongoClient
    return MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)[DB_NAME]


def _run(coro_factory):
    async def _inner():
        from motor.motor_asyncio import AsyncIOMotorClient
        cl = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
        try:
            return await coro_factory(cl[DB_NAME])
        finally:
            cl.close()
    return asyncio.run(_inner())


def _login(mail):
    r = requests.post(f"{API}/auth/login", json={"email": mail, "password": PW},
                      timeout=30)
    assert r.status_code == 200, f"Login {mail}: {r.text[:200]}"
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture(scope="module")
def welt():
    z = {}
    yield z
    dbx = _db()
    if z.get("dealer_id"):
        for coll in ("users", "subscriptions", "manual_payments", "plan_requests",
                     "activity_logs"):
            dbx[coll].delete_many({"dealer_id": z["dealer_id"]})
        dbx.dealers.delete_many({"id": z["dealer_id"]})
    dbx.users.delete_many({"email": {"$regex": f"_{SUF}@"}})
    dbx.plan_requests.delete_many({"contact_email": {"$regex": f"_{SUF}@"}})
    dbx.payment_transactions.delete_many({"session_id": {"$regex": f"^bt_{SUF}"}})
    dbx.subscriptions.delete_many({"session_id": {"$regex": f"^bt_{SUF}"}})


def test_00_admin(welt):
    import bcrypt
    mail = f"bt_admin_{SUF}@e2etest-mail.de"
    _db().users.insert_one({
        "id": f"btadm_{SUF}", "email": mail, "role": "admin", "active": True,
        "dealer_id": None, "is_super_admin": True,
        "password_hash": bcrypt.hashpw(PW.encode(), bcrypt.gensalt()).decode(),
        "created_at": "2026-01-01T00:00:00+00:00"})
    welt["A"] = _login(mail)


# ---------- Zugangs-Anfrage (oeffentlich) ----------
def test_01_zugangs_anfrage(welt):
    r = requests.post(f"{API}/zugang-anfrage", json={
        "company_name": f"Betreiber Autohaus {SUF}",
        "contact_person": "B Chef",
        "email": f"bt_chef_{SUF}@e2etest-mail.de",
        "phone": "0511 9", "message": "Bitte freischalten",
        "sucher_anzahl": 2}, timeout=30)
    assert r.status_code == 200, r.text[:300]
    req = _db().plan_requests.find_one({"contact_email": f"bt_chef_{SUF}@e2etest-mail.de"})
    assert req and req["type"] == "zugang" and req["status"] == "offen"
    assert "2 Sucher" in req["wanted"]
    welt["zugang_req"] = req["id"]
    # Validierung: kaputte E-Mail -> 422
    assert requests.post(f"{API}/zugang-anfrage", json={
        "company_name": "X Y", "contact_person": "A B", "email": "keinemail"},
        timeout=30).status_code == 422


# ---------- Firma ohne Abo anlegen (plan_type none) ----------
def test_02_firma_anlegen_plan_none(welt):
    r = requests.post(f"{API}/admin/users", headers=welt["A"], json={
        "email": f"bt_chef_{SUF}@e2etest-mail.de", "password": PW,
        "company_name": f"Betreiber Autohaus {SUF}", "plan_type": "none"},
        timeout=30)
    assert r.status_code == 200, r.text[:300]
    welt["dealer_id"] = r.json()["dealer_id"]
    welt["chef_id"] = r.json()["user_id"]
    # KEIN Abo angelegt
    assert _db().subscriptions.count_documents({"dealer_id": welt["dealer_id"]}) == 0
    welt["H"] = _login(f"bt_chef_{SUF}@e2etest-mail.de")
    # Chef kann verwalten (Bestand, kostenlos) ...
    assert requests.get(f"{API}/contracts", headers=welt["H"], timeout=30).status_code == 200
    # ... aber nicht suchen/vergleichen (402 ohne Abo)
    r = requests.post(f"{API}/mobile/compare", headers=welt["H"],
                      json={"url": "https://www.kleinanzeigen.de/s-anzeige/x/9600000001-216-1"},
                      timeout=30)
    assert r.status_code == 402, r.text[:200]


# ---------- Sucher legt der Betreiber an ----------
def test_03_betreiber_legt_sucher_an(welt):
    url = f"{API}/admin/dealers/{welt['dealer_id']}/sucher"
    r = requests.post(url, headers=welt["A"], json={
        "email": f"bt_sucher_{SUF}@e2etest-mail.de", "password": PW,
        "first_name": "B", "last_name": "Sucher"}, timeout=30)
    assert r.status_code == 200, r.text[:300]
    welt["sucher_id"] = r.json()["sucher_id"]
    assert "150" in r.json()["hinweis"] and "1.500" in r.json()["hinweis"]
    # Duplikat -> 409; fremde Firma -> 404; Nicht-Admin -> 403
    assert requests.post(url, headers=welt["A"], json={
        "email": f"bt_sucher_{SUF}@e2etest-mail.de", "password": PW},
        timeout=30).status_code == 409
    assert requests.post(f"{API}/admin/dealers/gibtesnicht/sucher", headers=welt["A"],
                         json={"email": f"bt_x_{SUF}@e2etest-mail.de", "password": PW},
                         timeout=30).status_code == 404
    assert requests.post(url, headers=welt["H"], json={
        "email": f"bt_y_{SUF}@e2etest-mail.de", "password": PW},
        timeout=30).status_code == 403
    # Sucher kann sich anmelden, hat aber noch kein Abo
    welt["S"] = _login(f"bt_sucher_{SUF}@e2etest-mail.de")
    r = requests.post(f"{API}/mobile/compare", headers=welt["S"],
                      json={"url": "https://www.kleinanzeigen.de/s-anzeige/x/9600000002-216-1"},
                      timeout=30)
    assert r.status_code == 402


def test_04_sucherliste_mit_abo_status(welt):
    r = requests.get(f"{API}/admin/dealers/{welt['dealer_id']}/sucher",
                     headers=welt["A"], timeout=30)
    assert r.status_code == 200, r.text[:200]
    zeile = next(x for x in r.json() if x["id"] == welt["sucher_id"])
    assert "password_hash" not in zeile
    assert zeile["subscription"]["active"] is False
    assert zeile["letzte_zahlung"] is None


# ---------- Freischalten erfasst die Zahlung ----------
def test_05_freischalten_erfasst_zahlung_und_schliesst_anfrage(welt):
    dbx = _db()
    # Offene Abo-Anfrage des Suchers simulieren (wie vom Team-Formular)
    dbx.plan_requests.insert_one({
        "id": f"btreq_{SUF}", "type": "sucher_abo",
        "dealer_id": welt["dealer_id"], "subject_user_id": welt["sucher_id"],
        "status": "offen", "created_at": datetime.now(timezone.utc).isoformat()})
    r = requests.post(f"{API}/admin/sucher/{welt['sucher_id']}/abo",
                      headers=welt["A"], json={"plan": "monthly"}, timeout=30)
    assert r.status_code == 200, r.text[:300]
    # Abo aktiv
    zeile = next(x for x in requests.get(
        f"{API}/admin/dealers/{welt['dealer_id']}/sucher", headers=welt["A"],
        timeout=30).json() if x["id"] == welt["sucher_id"])
    assert zeile["subscription"]["active"] is True
    assert zeile["naechste_zahlung_am"]
    # Zahlung automatisch erfasst: 150 EUR (neuer Listenpreis)
    z = dbx.manual_payments.find_one({"subject_user_id": welt["sucher_id"]})
    assert z and z["amount"] == 150.00 and z["plan"] == "monthly"
    assert z["period_until"] == zeile["subscription"]["expires_at"]
    # Anfrage im selben Vorgang geschlossen
    assert dbx.plan_requests.find_one({"id": f"btreq_{SUF}"})["status"] != "offen"
    # Sucher darf jetzt vergleichen (kein 402 mehr; Mock liefert 200)
    r = requests.post(f"{API}/mobile/compare", headers=welt["S"],
                      json={"url": f"https://www.kleinanzeigen.de/s-anzeige/x/96{uuid.uuid4().int % 10**8:08d}-216-1"},
                      timeout=60)
    assert r.status_code != 402, r.text[:200]


def test_06_chef_abo_anfrage_und_freischaltung_fuer_dealer(welt):
    r = requests.post(f"{API}/dealer/abo-anfrage-selbst", headers=welt["H"],
                      json={"plan": "yearly"}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    req = _db().plan_requests.find_one({"id": r.json()["request_id"]})
    assert req["subject_user_id"] == welt["chef_id"] and req["price"] == 1500.00
    # Freischaltung akzeptiert auch die Rolle dealer (Chef als eigener Sucher)
    r = requests.post(f"{API}/admin/sucher/{welt['chef_id']}/abo",
                      headers=welt["A"], json={"plan": "yearly", "betrag": 1400,
                                               "gezahlt_am": "2026-09-01"},
                      timeout=30)
    assert r.status_code == 200, r.text[:300]
    z = _db().manual_payments.find_one({"subject_user_id": welt["chef_id"]})
    assert z["amount"] == 1400.00 and z["paid_at"] == "2026-09-01"
    r = requests.post(f"{API}/mobile/compare", headers=welt["H"],
                      json={"url": f"https://www.kleinanzeigen.de/s-anzeige/x/96{uuid.uuid4().int % 10**8:08d}-216-1"},
                      timeout=60)
    assert r.status_code != 402


def test_07_zahlungen_einsehen_und_nachtragen(welt):
    url = f"{API}/admin/dealers/{welt['dealer_id']}/zahlungen"
    r = requests.get(url, headers=welt["A"], timeout=30)
    assert r.status_code == 200 and len(r.json()) == 2   # Sucher + Chef
    r = requests.post(url, headers=welt["A"], json={
        "amount": 300, "paid_at": "2026-09-02", "note": "Nachzahlung Rechnung 42",
        "subject_user_id": welt["sucher_id"]}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    r = requests.get(url, headers=welt["A"], timeout=30)
    assert len(r.json()) == 3
    assert requests.get(url, headers=welt["H"], timeout=30).status_code == 403
    assert requests.post(f"{API}/admin/dealers/gibtesnicht/zahlungen",
                         headers=welt["A"], json={"amount": 1},
                         timeout=30).status_code == 404


# ---------- Stripe nur fuer den Marktplatz ----------
def test_08_firmen_checkout_geschlossen(welt):
    origin = os.environ.get("CORS_ORIGINS", "http://localhost:3000").split(",")[0].strip()
    for H in (welt["H"], welt["S"]):
        r = requests.post(f"{API}/payments/checkout", headers=H,
                          json={"plan": "monthly", "origin_url": origin}, timeout=30)
        assert r.status_code == 403, r.text[:200]
        assert "Rechnung" in r.text
    # Kaeufer mit unbekanntem Plan -> 400
    r = requests.post(f"{API}/buyer/register", json={"gewerblich_bestaetigt": True, 
        "company_name": "BT Kaeufer", "contact_name": "K B",
        "email": f"bt_kaeufer_{SUF}@e2etest-mail.de", "password": PW}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    welt["K"] = {"Authorization": f"Bearer {r.json()['token']}"}
    welt["kaeufer_id"] = requests.get(f"{API}/buyer/me", headers=welt["K"],
                                      timeout=30).json()["id"]
    r = requests.post(f"{API}/payments/checkout", headers=welt["K"],
                      json={"plan": "monthly", "origin_url": origin}, timeout=30)
    assert r.status_code == 400, r.text[:200]


def test_09_marktplatz_aktivierung_verlaengert_ab_ablauf(welt):
    """Funktionstest der Aktivierung (ohne echten Stripe-Aufruf):
    'marktplatz' verlaengert um 30 Tage ab bisherigem Ablauf; der atomare
    paid-Uebergang aktiviert genau einmal."""
    from routes.payments import _activate_paid_transaction
    dbx = _db()
    kid = welt["kaeufer_id"]
    kuenftig = datetime.now(timezone.utc) + timedelta(days=10)
    dbx.users.update_one({"id": kid}, {"$set": {"marketplace_access": {
        "active": True, "plan": "monthly",
        "expires_at": kuenftig.isoformat()}}})
    assert _activate_paid_transaction is not None
    sid = f"bt_{SUF}_1"
    tx = {"user_id": kid, "dealer_id": None, "plan": "marktplatz"}
    _run(lambda mdb: _aktivieren(mdb, tx, sid))
    u = dbx.users.find_one({"id": kid})
    ablauf = datetime.fromisoformat(u["marketplace_access"]["expires_at"])
    erwartet = kuenftig + timedelta(days=30)
    assert abs((ablauf - erwartet).total_seconds()) < 120, (ablauf, erwartet)
    assert u["marketplace_access"]["active"] is True
    assert u["marketplace_access"]["price"] == 20.00
    # Alt-Plan monthly (Bestands-Transaktion) erzeugt weiterhin ein Abo
    sid2 = f"bt_{SUF}_2"
    tx2 = {"user_id": welt["chef_id"], "dealer_id": welt["dealer_id"],
           "plan": "monthly"}
    _run(lambda mdb: _aktivieren(mdb, tx2, sid2))
    sub = dbx.subscriptions.find_one({"session_id": sid2})
    assert sub and sub["plan"] == "monthly" and sub["status"] == "active"
    # Idempotent per session_id: zweiter Lauf erzeugt kein zweites Abo
    _run(lambda mdb: _aktivieren(mdb, tx2, sid2))
    assert dbx.subscriptions.count_documents({"session_id": sid2}) == 1


async def _aktivieren(mdb, tx, sid):
    """_activate_paid_transaction gegen eine eigene Motor-DB ausfuehren
    (die Route nutzt das globale db-Objekt; patchen + awaiten muessen im
    SELBEN async-Kontext passieren, sonst laeuft die Coroutine schon
    wieder gegen das Original-db)."""
    import routes.payments as p
    alt = p.db
    p.db = mdb
    try:
        return await p._activate_paid_transaction(tx, sid)
    finally:
        p.db = alt


def test_10_kaeufer_zugangspreis_20(welt):
    r = requests.get(f"{API}/marktplatz/zugang", headers=welt["K"], timeout=30)
    assert r.status_code == 200, r.text[:200]
    assert float(r.json().get("price") or 0) == 20.00


# ---------- Produktionsverhalten: SELF_SIGNUP=false ----------
PROD_BASE = os.environ.get("BETREIBER_PROD_URL", "").rstrip("/")


@pytest.mark.skipif(not PROD_BASE, reason="BETREIBER_PROD_URL nicht gesetzt "
                    "(eigener Backend-Prozess mit SELF_SIGNUP=false)")
def test_11_self_signup_aus(welt):
    api = f"{PROD_BASE}/api"
    r = requests.post(f"{api}/auth/register", json={
        "email": f"bt_prod_{SUF}@e2etest-mail.de", "password": PW,
        "company_name": "Prod Test", "contact_person": "P T",
        "phone": "0511 1"}, timeout=30)
    assert r.status_code == 403, r.text[:200]
    assert "Zugangs-Anfrage" in r.text
    # Zugangs-Anfrage selbst bleibt offen (oeffentlich erlaubt)
    r = requests.post(f"{api}/zugang-anfrage", json={
        "company_name": f"Prod Firma {SUF}", "contact_person": "P T",
        "email": f"bt_prod_{SUF}@e2etest-mail.de"}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    _db().plan_requests.delete_many({"contact_email": f"bt_prod_{SUF}@e2etest-mail.de"})
    # Chef-Sucher-Verwaltung ist zu (403 mit Betreiber-Hinweis)
    r = requests.post(f"{api}/auth/login", json={
        "email": f"bt_chef_{SUF}@e2etest-mail.de", "password": PW}, timeout=30)
    assert r.status_code == 200
    H = {"Authorization": f"Bearer {r.json()['token']}"}
    r = requests.post(f"{api}/dealer/sucher", headers=H, json={
        "email": f"bt_neu_{SUF}@e2etest-mail.de", "password": PW,
        "first_name": "N", "last_name": "S"}, timeout=30)
    assert r.status_code == 403 and "Betreiber" in r.text, r.text[:200]


# ---------- Firmen-Verwaltung 09/2026: Kundennummer, Chef-Zeile, Gueltig-bis ----------
def test_12_kundennummer_automatisch_vierstellig(welt):
    """Jede Firma bekommt automatisch eine fortlaufende 4-stellige
    Kundennummer (ab 1001) — nichts anzugeben, sichtbar in Liste + Detail."""
    d = _db().dealers.find_one({"id": welt["dealer_id"]}, {"kunden_nr": 1})
    assert d and isinstance(d.get("kunden_nr"), int), d
    assert 1001 <= d["kunden_nr"] <= 9999
    welt["kunden_nr"] = d["kunden_nr"]
    # zweite Firma -> hoehere Nummer, nie dieselbe
    r = requests.post(f"{API}/admin/users", headers=welt["A"], json={
        "email": f"bt_chef2_{SUF}@e2etest-mail.de", "password": PW,
        "company_name": f"Zweite Firma {SUF}", "plan_type": "none"}, timeout=30)
    assert r.status_code == 200, r.text[:300]
    welt["dealer2_id"] = r.json()["dealer_id"]
    d2 = _db().dealers.find_one({"id": welt["dealer2_id"]}, {"kunden_nr": 1})
    assert d2["kunden_nr"] > welt["kunden_nr"]
    # Nutzerliste + Detail liefern die Nummer (Suche im Admin nach #Nummer)
    liste = requests.get(f"{API}/admin/users", headers=welt["A"], timeout=30).json()
    chef = next(x for x in liste if x["id"] == welt["chef_id"])
    assert chef["kunden_nr"] == welt["kunden_nr"]
    assert chef["company_name"] == f"Betreiber Autohaus {SUF}"
    det = requests.get(f"{API}/admin/users/{welt['chef_id']}/contracts",
                       headers=welt["A"], timeout=30).json()["user"]
    assert det["kunden_nr"] == welt["kunden_nr"]
    assert det["company_name"] == f"Betreiber Autohaus {SUF}"
    # Aufraeumen der zweiten Firma
    dbx = _db()
    dbx.users.delete_many({"dealer_id": welt["dealer2_id"]})
    dbx.dealers.delete_many({"id": welt["dealer2_id"]})


def test_13_chef_steht_in_der_firmenliste(welt):
    """Die Firmen-Karte zeigt den Chef zuerst (ist_chef) mit seinem
    Sucher-Funktion-Status, danach die Sucher."""
    r = requests.get(f"{API}/admin/dealers/{welt['dealer_id']}/sucher",
                     headers=welt["A"], timeout=30)
    assert r.status_code == 200, r.text[:200]
    rows = r.json()
    assert rows[0]["id"] == welt["chef_id"] and rows[0]["ist_chef"] is True
    assert "password_hash" not in rows[0]
    assert rows[0]["subscription"]["active"] is True          # aus test_06
    sucher = next(x for x in rows if x["id"] == welt["sucher_id"])
    assert sucher["ist_chef"] is False


def test_14_gueltig_bis_frei_waehlbar_und_automatische_sperre(welt):
    """Freischalten mit 'gueltig_bis' setzt genau dieses Ablaufdatum; das
    Datum ist spaeter ohne neue Zahlung aenderbar; nach Ablauf sperrt die
    Sucher-Funktion automatisch (402)."""
    dbx = _db()
    heute = datetime.now(timezone.utc).date()
    bis = (heute + timedelta(days=45)).isoformat()
    vorher = dbx.manual_payments.count_documents({"subject_user_id": welt["sucher_id"]})
    r = requests.post(f"{API}/admin/sucher/{welt['sucher_id']}/abo",
                      headers=welt["A"], json={"plan": "monthly", "gueltig_bis": bis},
                      timeout=30)
    assert r.status_code == 200, r.text[:300]
    assert r.json()["expires_at"].startswith(bis)
    sub = dbx.subscriptions.find_one({"subject_user_id": welt["sucher_id"], "status": "active"})
    assert sub["expires_at"].startswith(f"{bis}T23:59:59")   # Tagesende (Berlin)
    # Freischalten erfasst weiterhin eine Zahlung, bezahlt bis = gueltig_bis
    assert dbx.manual_payments.count_documents({"subject_user_id": welt["sucher_id"]}) == vorher + 1
    assert dbx.manual_payments.find_one({"subject_user_id": welt["sucher_id"]},
                                        sort=[("created_at", -1)])["period_until"].startswith(bis)
    # Ungueltiges Datum -> 400
    r = requests.patch(f"{API}/admin/sucher/{welt['sucher_id']}/abo-gueltig-bis",
                       headers=welt["A"], json={"gueltig_bis": "31.12.2026", "grund": "Test: Laufzeit angepasst"}, timeout=30)
    assert r.status_code == 400, r.text[:200]
    # Nur Datum aendern: KEINE neue Zahlung, Liste zeigt neues Datum
    neu = (heute + timedelta(days=10)).isoformat()
    r = requests.patch(f"{API}/admin/sucher/{welt['sucher_id']}/abo-gueltig-bis",
                       headers=welt["A"], json={"gueltig_bis": neu, "grund": "Test: Laufzeit angepasst"}, timeout=30)
    assert r.status_code == 200, r.text[:300]
    assert dbx.manual_payments.count_documents({"subject_user_id": welt["sucher_id"]}) == vorher + 1
    zeile = next(x for x in requests.get(
        f"{API}/admin/dealers/{welt['dealer_id']}/sucher", headers=welt["A"],
        timeout=30).json() if x["id"] == welt["sucher_id"])
    assert zeile["naechste_zahlung_am"].startswith(neu)
    assert zeile["subscription"]["active"] is True
    # Datum in der Vergangenheit -> Sucher-Funktion automatisch gesperrt
    gestern = (heute - timedelta(days=1)).isoformat()
    r = requests.patch(f"{API}/admin/sucher/{welt['sucher_id']}/abo-gueltig-bis",
                       headers=welt["A"], json={"gueltig_bis": gestern, "grund": "Test: Laufzeit angepasst"}, timeout=30)
    assert r.status_code == 200, r.text[:300]
    zeile = next(x for x in requests.get(
        f"{API}/admin/dealers/{welt['dealer_id']}/sucher", headers=welt["A"],
        timeout=30).json() if x["id"] == welt["sucher_id"])
    assert zeile["subscription"]["active"] is False
    r = requests.post(f"{API}/mobile/compare", headers=welt["S"],
                      json={"url": f"https://www.kleinanzeigen.de/s-anzeige/x/96{uuid.uuid4().int % 10**8:08d}-216-1"},
                      timeout=60)
    assert r.status_code == 402, r.text[:200]
    # Abgelaufen per Datum = weiterhin per neuem Datum verlaengerbar (ohne
    # neue Zahlung) — der Betreiber steuert die Gueltigkeit frei.
    r = requests.patch(f"{API}/admin/sucher/{welt['sucher_id']}/abo-gueltig-bis",
                       headers=welt["A"], json={"gueltig_bis": bis, "grund": "Test: Laufzeit angepasst"}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    assert dbx.manual_payments.count_documents({"subject_user_id": welt["sucher_id"]}) == vorher + 1
    # Aufgehobenes Abo (plan=null): kein Datum mehr setzbar (404)
    r = requests.post(f"{API}/admin/sucher/{welt['sucher_id']}/abo",
                      headers=welt["A"], json={"plan": None}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    r = requests.patch(f"{API}/admin/sucher/{welt['sucher_id']}/abo-gueltig-bis",
                       headers=welt["A"], json={"gueltig_bis": bis, "grund": "Test: Laufzeit angepasst"}, timeout=30)
    assert r.status_code == 404, r.text[:200]
