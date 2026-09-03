# -*- coding: utf-8 -*-
"""Produktluecken 09/2026: Fahrer-Verwaltung im Admin + Interessenten-Flow
(Kaeufer-Antwort auf Gegenangebote, listing_id-Filter).

Braucht laufendes Backend (TEST_BASE_URL, mock ok) + Mongo-Zugriff.
Die Tests bauen aufeinander auf — immer die ganze Datei laufen lassen.
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
PW = "Produkt123!x"


def _db():
    from pymongo import MongoClient
    return MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)[DB_NAME]


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
    for did in (z.get("dealer_id"),):
        if did:
            for coll in ("users", "vehicles", "resale_listings", "listing_interest",
                         "dealer_drivers", "appointments", "subscriptions"):
                dbx[coll].delete_many({"dealer_id": did})
            dbx.dealers.delete_many({"id": did})
    dbx.users.delete_many({"email": {"$regex": f"_{SUF}@"}})
    dbx.driver_accounts.delete_many({"email": {"$regex": f"_{SUF}@"}})
    dbx.listing_interest.delete_many({"buyer_email": {"$regex": f"_{SUF}@"}})


def test_00_aufbau(welt):
    import bcrypt
    dbx = _db()
    # Admin
    mail = f"pk_admin_{SUF}@e2etest-mail.de"
    dbx.users.insert_one({
        "id": f"pkadm_{SUF}", "email": mail, "role": "admin", "active": True,
        "dealer_id": None, "is_super_admin": True,
        "password_hash": bcrypt.hashpw(PW.encode(), bcrypt.gensalt()).decode(),
        "created_at": "2026-01-01T00:00:00+00:00"})
    welt["A"] = _login(mail)
    # Haendler
    r = requests.post(f"{API}/auth/register", json={
        "email": f"pk_chef_{SUF}@e2etest-mail.de", "password": PW,
        "company_name": "Produkt Autohaus", "contact_person": "P Chef",
        "phone": "0511 7"}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    welt["H"] = {"Authorization": f"Bearer {r.json()['token']}"}
    me = requests.get(f"{API}/auth/me", headers=welt["H"], timeout=30).json()["user"]
    welt["dealer_id"] = me["dealer_id"]
    # Verkaufspaket + oeffentliches Profil (fuer Interessen-Flow)
    r = requests.put(f"{API}/admin/dealers/{welt['dealer_id']}/sale-plan",
                     headers=welt["A"], json={"tier": "s5", "months": 1}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    r = requests.put(f"{API}/dealer/marketplace-profile", headers=welt["H"],
                     json={"public": True, "description": "Produkt-Test"}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    # Kaeufer mit aktivem Zugang
    r = requests.post(f"{API}/buyer/register", json={"gewerblich_bestaetigt": True, 
        "company_name": "PK Kaeufer", "contact_name": "K P",
        "email": f"pk_kaeufer_{SUF}@e2etest-mail.de", "password": PW,
        "phone": "0511 8"}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    welt["K"] = {"Authorization": f"Bearer {r.json()['token']}"}
    kid = requests.get(f"{API}/buyer/me", headers=welt["K"], timeout=30).json()["id"]
    welt["buyer_id"] = kid
    dbx.users.update_one({"id": kid}, {"$set": {"marketplace_access": {
        "active": True, "plan": "monthly",
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()}}})
    # Fahrer
    r = requests.post(f"{API}/driver/register", json={
        "email": f"pk_fahrer_{SUF}@e2etest-mail.de", "password": PW,
        "display_name": "PK Fahrer"}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    welt["D"] = {"Authorization": f"Bearer {r.json()['token']}"}
    welt["driver_id"] = r.json()["driver"]["id"]
    welt["driver_code"] = r.json()["driver"]["driver_code"]
    # Fahrer mit Haendler verknuepfen + ein offener und ein erledigter Termin
    r = requests.post(f"{API}/drivers/add", headers=welt["H"],
                      json={"driver_code": welt["driver_code"]}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    now = datetime.now(timezone.utc).isoformat()
    for aid, status in ((f"pk_offen_{SUF}", "offen"), (f"pk_fertig_{SUF}", "abgeholt")):
        dbx.appointments.insert_one({
            "id": aid, "dealer_id": welt["dealer_id"], "driver_id": welt["driver_id"],
            "vehicle_id": None, "status": status, "pickup_date": "2099-01-01",
            "status_changed_at": now, "created_at": now, "updated_at": now})
    # Zwei Fahrzeuge -> zwei veroeffentlichte Inserate
    welt["lids"] = []
    for n in range(2):
        vid = str(uuid.uuid4())
        dbx.vehicles.insert_one({
            "id": vid, "dealer_id": welt["dealer_id"], "lifecycle": "bestand",
            "status": "Bestand", "purchase_price": 5000,
            "data": {"make_label": "VW", "model_label": f"Golf P{n}",
                     "mileage": 90000, "first_registration": "01/2020",
                     "fuel_label": "Benzin", "power_ps": 110, "images": []},
            "created_at": now, "updated_at": now})
        r = requests.post(f"{API}/resale/draft/{vid}", headers=welt["H"], timeout=30)
        assert r.status_code == 200, r.text[:300]
        lid = r.json()["id"]
        assert requests.put(f"{API}/resale/{lid}", headers=welt["H"],
                            json={"price_public": 9900, "price_b2b": 9000},
                            timeout=30).status_code == 200
        assert requests.post(f"{API}/resale/{lid}/status", headers=welt["H"],
                             json={"status": "verkaufsbereit"}, timeout=30).status_code == 200
        r = requests.post(f"{API}/resale/{lid}/publish", headers=welt["H"],
                          json={"visibility": "public"}, timeout=30)
        assert r.status_code == 200, r.text[:300]
        welt["lids"].append(lid)


# ================= Fahrer-Verwaltung im Admin =================
def test_01_admin_fahrerliste_mit_zahlen(welt):
    r = requests.get(f"{API}/admin/drivers", headers=welt["A"], timeout=30)
    assert r.status_code == 200, r.text[:200]
    zeile = next((x for x in r.json() if x["id"] == welt["driver_id"]), None)
    assert zeile, "Fahrer fehlt in der Admin-Liste"
    assert "password_hash" not in zeile and "current_session_id" not in zeile
    assert zeile["verknuepfungen"] == 1
    assert zeile["termine"] == 2 and zeile["termine_offen"] == 1
    assert any("Produkt Autohaus" in f for f in zeile["firmen"])
    # Nur Admins: Haendler und Fahrer bekommen 403/401
    assert requests.get(f"{API}/admin/drivers", headers=welt["H"], timeout=30).status_code == 403
    assert requests.get(f"{API}/admin/drivers", headers=welt["D"], timeout=30).status_code in (401, 403)


def test_02_admin_sperrt_fahrer_und_beendet_sitzung(welt):
    r = requests.post(f"{API}/admin/drivers/{welt['driver_id']}/active",
                      headers=welt["A"], json={"active": False}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    r = requests.get(f"{API}/driver/me", headers=welt["D"], timeout=30)
    assert r.status_code == 401, f"gesperrter Fahrer noch aktiv: {r.status_code}"
    # Login gesperrt
    r = requests.post(f"{API}/driver/login", json={
        "email": f"pk_fahrer_{SUF}@e2etest-mail.de", "password": PW}, timeout=30)
    assert r.status_code in (401, 403)
    # Entsperren + neu anmelden
    assert requests.post(f"{API}/admin/drivers/{welt['driver_id']}/active",
                         headers=welt["A"], json={"active": True}, timeout=30).status_code == 200
    r = requests.post(f"{API}/driver/login", json={
        "email": f"pk_fahrer_{SUF}@e2etest-mail.de", "password": PW}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    welt["D"] = {"Authorization": f"Bearer {r.json()['token']}"}


def test_03_admin_setzt_fahrer_passwort(welt):
    # Zu schwach -> 400
    r = requests.post(f"{API}/admin/drivers/{welt['driver_id']}/password",
                      headers=welt["A"], json={"new_password": "nurbuchstaben"}, timeout=30)
    assert r.status_code in (400, 422), r.text[:200]
    neu = "NeuesPw123!x"
    r = requests.post(f"{API}/admin/drivers/{welt['driver_id']}/password",
                      headers=welt["A"], json={"new_password": neu}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    # Alte Sitzung beendet, altes Passwort ungueltig, neues funktioniert
    assert requests.get(f"{API}/driver/me", headers=welt["D"], timeout=30).status_code == 401
    assert requests.post(f"{API}/driver/login", json={
        "email": f"pk_fahrer_{SUF}@e2etest-mail.de", "password": PW},
        timeout=30).status_code in (401, 403)
    r = requests.post(f"{API}/driver/login", json={
        "email": f"pk_fahrer_{SUF}@e2etest-mail.de", "password": neu}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    welt["D"] = {"Authorization": f"Bearer {r.json()['token']}"}
    # Nicht-Admin darf nicht
    assert requests.post(f"{API}/admin/drivers/{welt['driver_id']}/password",
                         headers=welt["H"], json={"new_password": neu},
                         timeout=30).status_code == 403


def test_04_admin_loescht_fahrer_mit_entkopplung(welt):
    dbx = _db()
    dbx.password_resets.insert_one({
        "id": str(uuid.uuid4()), "user_id": welt["driver_id"],
        "account_type": "driver", "token_hash": "x", "used": False,
        "expires_at": datetime.now(timezone.utc).isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat()})
    r = requests.delete(f"{API}/admin/drivers/{welt['driver_id']}",
                        headers=welt["A"], timeout=30)
    assert r.status_code == 200, r.text[:200]
    assert r.json()["verknuepfungen_entfernt"] == 1
    assert r.json()["offene_termine_getrennt"] >= 1
    assert dbx.driver_accounts.find_one({"id": welt["driver_id"]}) is None
    assert dbx.dealer_drivers.count_documents({"driver_account_id": welt["driver_id"]}) == 0
    assert dbx.password_resets.count_documents({"user_id": welt["driver_id"]}) == 0
    offen = dbx.appointments.find_one({"id": f"pk_offen_{SUF}"})
    assert "driver_id" not in offen or not offen.get("driver_id")
    fertig = dbx.appointments.find_one({"id": f"pk_fertig_{SUF}"})
    # Audit 09/2026: Fahrer wird pseudonymisiert — Historie bleibt als Pseudonym erhalten
    assert not fertig.get("driver_id") and str(fertig.get("driver_id_hist", "")).startswith("geloescht:"), fertig
    assert requests.delete(f"{API}/admin/drivers/{welt['driver_id']}",
                           headers=welt["A"], timeout=30).status_code == 404


# ================= Interessenten-Flow =================
def test_05_kaeufer_sendet_interesse(welt):
    for n, lid in enumerate(welt["lids"]):
        r = requests.post(f"{API}/marktplatz/listings/{lid}/interesse",
                          headers=welt["K"],
                          json={"offer": 8000 + n * 100, "message": f"Anfrage {n}"},
                          timeout=30)
        assert r.status_code == 200, r.text[:300]
    r = requests.get(f"{API}/buyer/interessen", headers=welt["K"], timeout=30)
    assert r.status_code == 200 and len(r.json()) == 2


def test_06_haendler_filtert_nach_inserat(welt):
    r = requests.get(f"{API}/dealer/interessen", headers=welt["H"], timeout=30)
    assert r.status_code == 200 and len(r.json()) == 2, r.text[:200]
    r = requests.get(f"{API}/dealer/interessen", headers=welt["H"],
                     params={"listing_id": welt["lids"][0]}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    assert len(r.json()) == 1 and r.json()[0]["listing_id"] == welt["lids"][0]
    welt["interest_a"] = r.json()[0]["id"]
    r = requests.get(f"{API}/dealer/interessen", headers=welt["H"],
                     params={"listing_id": welt["lids"][1]}, timeout=30)
    welt["interest_b"] = r.json()[0]["id"]


def test_07_gegenangebot_und_kaeufer_nimmt_an(welt):
    dbx = _db()
    r = requests.post(f"{API}/interessen/{welt['interest_a']}/antwort",
                      headers=welt["H"],
                      json={"action": "gegenangebot", "counter_offer": 9500,
                            "message": "Dafuer geht er weg"}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    # Kaeufer sieht das Gegenangebot
    r = requests.get(f"{API}/buyer/interessen", headers=welt["K"], timeout=30)
    it = next(x for x in r.json() if x["id"] == welt["interest_a"])
    assert it["status"] == "gegenangebot" and it["counter_offer"] == 9500
    # Fremder Kaeufer darf nicht antworten (404 — nicht seine Anfrage)
    r = requests.post(f"{API}/buyer/register", json={"gewerblich_bestaetigt": True, 
        "company_name": "PK Fremd", "contact_name": "F P",
        "email": f"pk_fremd_{SUF}@e2etest-mail.de", "password": PW}, timeout=30)
    fremd = {"Authorization": f"Bearer {r.json()['token']}"}
    assert requests.post(f"{API}/interessen/{welt['interest_a']}/kaeufer-antwort",
                         headers=fremd, json={"action": "annehmen"},
                         timeout=30).status_code == 404
    # Haendler darf den Kaeufer-Endpunkt nicht nutzen (403 — falsche Rolle)
    assert requests.post(f"{API}/interessen/{welt['interest_a']}/kaeufer-antwort",
                         headers=welt["H"], json={"action": "annehmen"},
                         timeout=30).status_code == 403
    # Kaeufer nimmt an -> Anfrage akzeptiert + Inserat reserviert
    r = requests.post(f"{API}/interessen/{welt['interest_a']}/kaeufer-antwort",
                      headers=welt["K"], json={"action": "annehmen"}, timeout=30)
    assert r.status_code == 200 and r.json()["status"] == "akzeptiert", r.text[:200]
    l = dbx.resale_listings.find_one({"id": welt["lids"][0]})
    assert l["status"] == "reserviert" and l["reserved_for"] == welt["buyer_id"]
    it = dbx.listing_interest.find_one({"id": welt["interest_a"]})
    assert it["status"] == "akzeptiert"
    assert any(h.get("von") == "kaeufer" and h.get("aktion") == "annehmen"
               for h in it.get("history") or [])
    # Zweite Antwort auf dieselbe Anfrage: 400 (kein Gegenangebot mehr offen)
    assert requests.post(f"{API}/interessen/{welt['interest_a']}/kaeufer-antwort",
                         headers=welt["K"], json={"action": "annehmen"},
                         timeout=30).status_code == 400


def test_08_kaeufer_lehnt_gegenangebot_ab(welt):
    dbx = _db()
    # Antwort auf OFFENE Anfrage (kein Gegenangebot): 400
    assert requests.post(f"{API}/interessen/{welt['interest_b']}/kaeufer-antwort",
                         headers=welt["K"], json={"action": "ablehnen"},
                         timeout=30).status_code == 400
    assert requests.post(f"{API}/interessen/{welt['interest_b']}/antwort",
                         headers=welt["H"],
                         json={"action": "gegenangebot", "counter_offer": 9700,
                               "message": ""}, timeout=30).status_code == 200
    r = requests.post(f"{API}/interessen/{welt['interest_b']}/kaeufer-antwort",
                      headers=welt["K"], json={"action": "ablehnen"}, timeout=30)
    assert r.status_code == 200 and r.json()["status"] == "abgelehnt"
    # Inserat bleibt veroeffentlicht (nichts reserviert)
    l = dbx.resale_listings.find_one({"id": welt["lids"][1]})
    assert l["status"] == "veroeffentlicht" and not l.get("reserved_for")
    # Haendler-Antwort auf abgeschlossene Anfrage: 400
    assert requests.post(f"{API}/interessen/{welt['interest_b']}/antwort",
                         headers=welt["H"], json={"action": "akzeptieren"},
                         timeout=30).status_code == 400


def test_09_kaeufer_annahme_rollt_zurueck_wenn_fahrzeug_weg(welt):
    """Review-Workflow 09/2026: 409-Pfad der Kaeufer-Annahme — ist das
    Fahrzeug nicht mehr veroeffentlicht, bleibt die Anfrage unveraendert."""
    dbx = _db()
    r = requests.post(f"{API}/marktplatz/listings/{welt['lids'][1]}/interesse",
                      headers=welt["K"], json={"offer": 8500, "message": "nochmal"},
                      timeout=30)
    assert r.status_code == 200, r.text[:300]
    iid = r.json()["interest_id"]
    assert requests.post(f"{API}/interessen/{iid}/antwort", headers=welt["H"],
                         json={"action": "gegenangebot", "counter_offer": 9100,
                               "message": ""}, timeout=30).status_code == 200
    # Fahrzeug parallel "weg" (fuer jemand anderen reserviert)
    dbx.resale_listings.update_one({"id": welt["lids"][1]},
                                   {"$set": {"status": "reserviert",
                                             "reserved_for": "jemand_anderes"}})
    r = requests.post(f"{API}/interessen/{iid}/kaeufer-antwort",
                      headers=welt["K"], json={"action": "annehmen"}, timeout=30)
    assert r.status_code == 409, r.text[:200]
    it = dbx.listing_interest.find_one({"id": iid})
    assert it["status"] == "gegenangebot", "Anfrage darf bei 409 nicht kippen"
    l = dbx.resale_listings.find_one({"id": welt["lids"][1]})
    assert l["reserved_for"] == "jemand_anderes", "fremde Reservierung angefasst"
    # Aufraeumen: zuruecksetzen und ablehnen
    dbx.resale_listings.update_one({"id": welt["lids"][1]},
                                   {"$set": {"status": "veroeffentlicht"},
                                    "$unset": {"reserved_for": ""}})
    assert requests.post(f"{API}/interessen/{iid}/kaeufer-antwort",
                         headers=welt["K"], json={"action": "ablehnen"},
                         timeout=30).status_code == 200


def test_10_haendler_antwort_verliert_nicht_gegen_kaeufer(welt):
    """Review-Workflow 09/2026 (hoch): Haendler-Write mit veraltetem Stand
    darf eine bereits akzeptierte Anfrage nicht ueberschreiben (409)."""
    dbx = _db()
    r = requests.post(f"{API}/marktplatz/listings/{welt['lids'][1]}/interesse",
                      headers=welt["K"], json={"offer": 8600, "message": "race"},
                      timeout=30)
    iid = r.json()["interest_id"]
    assert requests.post(f"{API}/interessen/{iid}/antwort", headers=welt["H"],
                         json={"action": "gegenangebot", "counter_offer": 9200,
                               "message": ""}, timeout=30).status_code == 200
    # Kaeufer nimmt an (Anfrage -> akzeptiert, Inserat reserviert)
    assert requests.post(f"{API}/interessen/{iid}/kaeufer-antwort",
                         headers=welt["K"], json={"action": "annehmen"},
                         timeout=30).status_code == 200
    # Haendler versucht (mit veraltetem Stand) abzulehnen -> 400/409, Status bleibt
    r = requests.post(f"{API}/interessen/{iid}/antwort", headers=welt["H"],
                      json={"action": "ablehnen"}, timeout=30)
    assert r.status_code in (400, 409), r.text[:200]
    it = dbx.listing_interest.find_one({"id": iid})
    assert it["status"] == "akzeptiert", "Kaeufer-Annahme wurde ueberschrieben!"
    l = dbx.resale_listings.find_one({"id": welt["lids"][1]})
    assert l["status"] == "reserviert" and l["reserved_for"] == welt["buyer_id"]
