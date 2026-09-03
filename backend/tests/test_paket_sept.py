# -*- coding: utf-8 -*-
"""Paket 09/2026 (Wuensche Betreiber): Snapshots nur Kleinanzeigen,
Fahrzeugpool max. 30 Vergleiche, Termin-Status verschoben/erledigt,
Sucher fragt Abo-Verlaengerung selbst an (Betreiber gibt frei),
Fahrer nimmt zugeteilte Fahrt an / lehnt ab, Marktplatz-Verhandlung in
beide Richtungen (Kaeufer-Gegenangebot, Haendler antwortet jederzeit).

Braucht laufendes Backend MIT MOCK_PROVIDER_FETCH=true (TEST_BASE_URL)
+ Mongo. Reihenfolgeabhaengig — immer die ganze Datei laufen lassen.
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
NUM = int(SUF[:6], 16) % 900000            # eindeutiger Zahlenblock fuer Inserat-IDs
PW = "Paket123!x"
MAIL = "e2etest-mail.de"


def _db():
    from pymongo import MongoClient
    return MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)[DB_NAME]


def _login(mail):
    r = requests.post(f"{API}/auth/login", json={"email": mail, "password": PW},
                      timeout=30)
    assert r.status_code == 200, f"Login {mail}: {r.text[:200]}"
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _ka_url(n):
    return f"https://www.kleinanzeigen.de/s-anzeige/paket-test/97{NUM:06d}{n:03d}-216-1"


def _compare(H, url):
    return requests.post(f"{API}/mobile/compare", headers=H, json={"url": url}, timeout=90)


@pytest.fixture(scope="module")
def welt():
    z = {}
    yield z
    dbx = _db()
    did = z.get("dealer_id")
    if did:
        for coll in ("users", "subscriptions", "manual_payments", "plan_requests",
                     "activity_logs", "vehicles", "vehicle_comparisons",
                     "appointments", "resale_listings", "listing_interest",
                     "dealer_drivers", "network_members", "listing_snapshots",
                     "sale_plans"):
            dbx[coll].delete_many({"dealer_id": did})
        dbx.dealers.delete_many({"id": did})
    dbx.users.delete_many({"email": {"$regex": f"_{SUF}@"}})
    dbx.driver_accounts.delete_many({"email": {"$regex": f"_{SUF}@"}})
    dbx.listings_cache.delete_many({"cache_key": {"$regex": f"97{NUM:06d}"}})


def test_00_aufbau(welt):
    import bcrypt
    dbx = _db()
    mail = f"ps_admin_{SUF}@{MAIL}"
    dbx.users.insert_one({
        "id": f"psadm_{SUF}", "email": mail, "role": "admin", "active": True,
        "dealer_id": None, "is_super_admin": True,
        "password_hash": bcrypt.hashpw(PW.encode(), bcrypt.gensalt()).decode(),
        "created_at": "2026-01-01T00:00:00+00:00"})
    welt["A"] = _login(mail)
    r = requests.post(f"{API}/admin/users", headers=welt["A"], json={
        "email": f"ps_chef_{SUF}@{MAIL}", "password": PW,
        "company_name": f"Paket Autohaus {SUF}", "plan_type": "none"}, timeout=30)
    assert r.status_code == 200, r.text[:300]
    welt["dealer_id"], welt["chef_id"] = r.json()["dealer_id"], r.json()["user_id"]
    welt["H"] = _login(f"ps_chef_{SUF}@{MAIL}")
    r = requests.post(f"{API}/admin/dealers/{welt['dealer_id']}/sucher", headers=welt["A"],
                      json={"email": f"ps_sucher_{SUF}@{MAIL}", "password": PW,
                            "first_name": "Sina", "last_name": "Sucher"}, timeout=30)
    assert r.status_code == 200, r.text[:300]
    welt["sucher_id"] = r.json()["sucher_id"]
    welt["S"] = _login(f"ps_sucher_{SUF}@{MAIL}")
    # Chef + Sucher freischalten (Vergleiche brauchen ein aktives Abo)
    for uid in (welt["chef_id"], welt["sucher_id"]):
        assert requests.post(f"{API}/admin/sucher/{uid}/abo", headers=welt["A"],
                             json={"plan": "monthly"}, timeout=30).status_code == 200


# ---------- Snapshots nur fuer Kleinanzeigen ----------
def test_01_snapshots_nur_kleinanzeigen(welt):
    r = _compare(welt["S"], _ka_url(1))
    assert r.status_code == 200, r.text[:300]
    assert r.json().get("snapshot_id"), "Kleinanzeigen: Snapshot erwartet"
    r = _compare(welt["S"], f"https://suchen.mobile.de/fahrzeuge/details.html?id=3{NUM:06d}1")
    if r.status_code == 400 and "freigeschaltet" in r.text:
        pytest.skip("mobile.de in dieser Umgebung nicht verfuegbar")
    assert r.status_code == 200, r.text[:300]
    assert r.json().get("snapshot_id") is None, "mobile.de: KEIN Snapshot mehr"
    vid = f"v_{r.json().get('ad_id')}"
    assert _db().listing_snapshots.count_documents(
        {"vehicle_id": vid, "dealer_id": welt["dealer_id"]}) == 0


# ---------- Fahrzeugpool: max. 30 Vergleiche ----------
def test_02_fahrzeugpool_maximal_30(welt):
    dbx = _db()
    ids = []
    for n in range(2, 32):                    # 30 weitere Vergleiche (Nr. 2..31)
        r = _compare(welt["S"], _ka_url(n))
        assert r.status_code == 200, (n, r.text[:200])
        ids.append(f"v_{r.json()['ad_id']}")
    pool = requests.get(f"{API}/vehicles", headers=welt["S"], timeout=30).json()
    verglichen = [v for v in pool if v.get("lifecycle") == "verglichen"]
    assert len(verglichen) == 30, len(verglichen)          # Nr.1 (aeltester) ist raus
    # Schutz: Fahrzeug mit Abholtermin bleibt, auch wenn es das aelteste ist
    aeltestes = ids[0]                                      # Nr. 2 ist jetzt das aelteste
    r = requests.post(f"{API}/appointments", headers=welt["H"], json={
        "vehicle_id": aeltestes, "pickup_date": "2099-01-01", "pickup_time": "10:00",
        "status": "offen"}, timeout=30)
    assert r.status_code == 200, r.text[:300]
    welt["termin_id"] = r.json().get("id") or r.json().get("appointment", {}).get("id")
    for n in range(32, 35):                   # drei weitere: Nr. 2 bleibt (geschuetzt),
        assert _compare(welt["S"], _ka_url(n)).status_code == 200   # Nr. 3 + 4 fallen raus
    pool = {v["id"]: v for v in requests.get(f"{API}/vehicles", headers=welt["S"], timeout=30).json()}
    verglichen = [v for v in pool.values() if v.get("lifecycle") == "verglichen"]
    assert aeltestes in pool, "Fahrzeug mit Termin wurde geloescht"
    for weg in ids[1:3]:
        assert weg not in pool, f"{weg} sollte aus dem Pool gefallen sein"
    assert ids[3] in pool                    # Nr. 5 gehoert noch zu den 30 neuesten
    assert len(verglichen) == 31             # 30 neueste + das geschuetzte
    assert dbx.vehicles.count_documents({"dealer_id": welt["dealer_id"], "lifecycle": "verglichen"}) == 31


# ---------- Termin-Status verschoben / erledigt ----------
def test_03_termin_status_verschoben_und_erledigt(welt):
    r = requests.post(f"{API}/appointments", headers=welt["H"], json={
        "title": "Test verschoben", "pickup_date": "2099-02-01", "status": "verschoben"},
        timeout=30)
    assert r.status_code == 200, r.text[:300]
    aid = r.json().get("id") or r.json().get("appointment", {}).get("id")
    assert aid
    r = requests.put(f"{API}/appointments/{aid}", headers=welt["H"],
                     json={"status": "erledigt"}, timeout=30)
    assert r.status_code == 200, r.text[:300]
    items = requests.get(f"{API}/appointments", headers=welt["H"], timeout=30).json()
    assert next(a for a in items if a["id"] == aid)["status"] == "erledigt"


# ---------- Sucher fragt Abo-Verlaengerung selbst an ----------
def test_04_sucher_abo_anfrage_und_freigabe(welt):
    dbx = _db()
    r = requests.post(f"{API}/dealer/abo-anfrage-selbst", headers=welt["S"],
                      json={"plan": "yearly"}, timeout=30)
    assert r.status_code == 200, r.text[:300]
    rid = r.json()["request_id"]
    req = dbx.plan_requests.find_one({"id": rid})
    assert req["type"] == "sucher_abo" and req["subject_user_id"] == welt["sucher_id"]
    assert req["subject_role"] == "sucher" and req["sucher_name"] == "Sina Sucher"
    assert req["company_name"] == f"Paket Autohaus {SUF}" and req["wanted_plan"] == "yearly"
    assert isinstance(req.get("kunden_nr"), int)
    # zweiter Klick: keine zweite Anfrage
    r = requests.post(f"{API}/dealer/abo-anfrage-selbst", headers=welt["S"],
                      json={"plan": "monthly"}, timeout=30)
    assert r.status_code == 200 and r.json().get("bereits_offen") is True
    assert dbx.plan_requests.count_documents(
        {"subject_user_id": welt["sucher_id"], "status": "offen"}) == 1
    # Abo-Ansicht des Suchers: Anfrage offen, kein Kuendigen
    sub = requests.get(f"{API}/dealer/subscription", headers=welt["S"], timeout=30).json()
    assert sub["anfrage_offen"] is True and sub["can_cancel"] is False
    # Betreiber sieht sie in den offenen Anfragen ...
    offen = requests.get(f"{API}/admin/plan-requests?status=offen", headers=welt["A"],
                         timeout=30).json()
    assert any(x["id"] == rid for x in offen)
    # ... und schaltet frei ("Ja") -> Anfrage geschlossen, Abo verlaengert
    r = requests.post(f"{API}/admin/sucher/{welt['sucher_id']}/abo", headers=welt["A"],
                      json={"plan": "monthly"}, timeout=30)
    assert r.status_code == 200, r.text[:300]
    assert dbx.plan_requests.find_one({"id": rid})["status"] != "offen"
    sub = requests.get(f"{API}/dealer/subscription", headers=welt["S"], timeout=30).json()
    assert sub["anfrage_offen"] is False and sub["active"] is True
    # Chef kann weiterhin selbst anfragen (unveraendert)
    r = requests.post(f"{API}/dealer/abo-anfrage-selbst", headers=welt["H"],
                      json={"plan": "monthly"}, timeout=30)
    assert r.status_code == 200, r.text[:300]
    assert dbx.plan_requests.find_one({"id": r.json()["request_id"]})["subject_role"] == "dealer"


# ---------- Fahrer: Zuteilung annehmen / ablehnen ----------
def test_05_fahrer_nimmt_fahrt_an_oder_lehnt_ab(welt):
    dbx = _db()
    r = requests.post(f"{API}/driver/register", json={
        "email": f"ps_fahrer_{SUF}@{MAIL}", "password": PW, "display_name": "Paket Fahrer"},
        timeout=30)
    assert r.status_code == 200, r.text[:300]
    D = {"Authorization": f"Bearer {r.json()['token']}"}
    driver_id, code = r.json()["driver"]["id"], r.json()["driver"]["driver_code"]
    assert requests.post(f"{API}/drivers/add", headers=welt["H"],
                         json={"driver_code": code}, timeout=30).status_code == 200
    r = requests.post(f"{API}/appointments", headers=welt["H"], json={
        "title": "Zuteilung Test", "pickup_date": "2099-03-01", "pickup_time": "09:00",
        "driver_id": driver_id, "status": "offen"}, timeout=30)
    assert r.status_code == 200, r.text[:300]
    aid = r.json().get("id") or r.json().get("appointment", {}).get("id")
    assert dbx.appointments.find_one({"id": aid})["zuteilung"] == "offen"
    # Fahrer sieht die Fahrt als "zugeteilt, bitte antworten"
    mine = requests.get(f"{API}/driver/appointments", headers=D, timeout=30).json()
    assert next(a for a in mine if a["id"] == aid)["zuteilung"] == "offen"
    # Ohne Annahme kein Abschluss
    r = requests.put(f"{API}/driver/appointments/{aid}/status", headers=D,
                     json={"status": "nicht abgeholt"}, timeout=30)
    assert r.status_code == 409 and "annehmen" in r.text, r.text[:200]
    # Ablehnen -> Termin geht an den Chef zurueck
    r = requests.put(f"{API}/driver/appointments/{aid}/zuteilung", headers=D,
                     json={"action": "ablehnen", "grund": "Bin im Urlaub"}, timeout=30)
    assert r.status_code == 200 and r.json()["zuteilung"] == "abgelehnt", r.text[:200]
    doc = dbx.appointments.find_one({"id": aid})
    assert "driver_id" not in doc and doc["zuteilung"] == "abgelehnt"
    assert "Fahrt abgelehnt: Bin im Urlaub" in doc.get("notes", "")
    chef_sicht = next(a for a in requests.get(f"{API}/appointments", headers=welt["H"],
                                              timeout=30).json() if a["id"] == aid)
    assert chef_sicht["zuteilung"] == "abgelehnt" and not chef_sicht.get("driver")
    assert all(a["id"] != aid for a in requests.get(f"{API}/driver/appointments",
                                                   headers=D, timeout=30).json())
    # Chef teilt erneut zu -> wieder "offen"; Fahrer nimmt an -> Abschluss moeglich
    r = requests.put(f"{API}/appointments/{aid}", headers=welt["H"],
                     json={"driver_id": driver_id}, timeout=30)
    assert r.status_code == 200, r.text[:300]
    assert dbx.appointments.find_one({"id": aid})["zuteilung"] == "offen"
    r = requests.put(f"{API}/driver/appointments/{aid}/zuteilung", headers=D,
                     json={"action": "annehmen"}, timeout=30)
    assert r.status_code == 200 and r.json()["zuteilung"] == "angenommen"
    chef_sicht = next(a for a in requests.get(f"{API}/appointments", headers=welt["H"],
                                              timeout=30).json() if a["id"] == aid)
    assert chef_sicht["zuteilung"] == "angenommen" and chef_sicht["driver"]["name"] == "Paket Fahrer"
    r = requests.put(f"{API}/driver/appointments/{aid}/status", headers=D,
                     json={"status": "nicht abgeholt"}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    # Alt-Termine ohne Feld gelten als angenommen (kein Blockieren)
    dbx.appointments.insert_one({
        "id": f"ps_alt_{SUF}", "dealer_id": welt["dealer_id"], "driver_id": driver_id,
        "status": "offen", "pickup_date": "2099-04-01", "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00"})
    alt = next(a for a in requests.get(f"{API}/driver/appointments", headers=D,
                                       timeout=30).json() if a["id"] == f"ps_alt_{SUF}")
    assert alt["zuteilung"] == "angenommen"


# ---------- Marktplatz: Verhandlung in beide Richtungen ----------
def test_06_marktplatz_verhandlung_beide_seiten(welt):
    dbx = _db()
    now = datetime.now(timezone.utc).isoformat()
    # Verkaufspaket + oeffentliches Profil, damit veroeffentlicht werden kann
    r = requests.put(f"{API}/admin/dealers/{welt['dealer_id']}/sale-plan", headers=welt["A"],
                     json={"tier": "s5"}, timeout=30)
    assert r.status_code == 200, r.text[:300]
    r = requests.put(f"{API}/dealer/marketplace-profile", headers=welt["H"],
                     json={"public": True, "description": "Paket-Test"}, timeout=30)
    assert r.status_code == 200, r.text[:300]
    vid = str(uuid.uuid4())
    dbx.vehicles.insert_one({
        "id": vid, "dealer_id": welt["dealer_id"], "lifecycle": "bestand",
        "status": "Bestand", "purchase_price": 5000,
        "data": {"make_label": "VW", "model_label": "Golf Paket", "mileage": 90000,
                 "first_registration": "01/2020", "fuel_label": "Benzin",
                 "power_ps": 110, "images": []},
        "created_at": now, "updated_at": now})
    r = requests.post(f"{API}/resale/draft/{vid}", headers=welt["H"], timeout=30)
    assert r.status_code == 200, r.text[:300]
    lid = r.json()["id"]
    assert requests.put(f"{API}/resale/{lid}", headers=welt["H"],
                        json={"price_public": 9900, "price_b2b": 9000}, timeout=30).status_code == 200
    assert requests.post(f"{API}/resale/{lid}/status", headers=welt["H"],
                         json={"status": "verkaufsbereit"}, timeout=30).status_code == 200
    r = requests.post(f"{API}/resale/{lid}/publish", headers=welt["H"],
                      json={"visibility": "public"}, timeout=30)
    assert r.status_code == 200, r.text[:300]
    # Kaeufer mit Zugang
    r = requests.post(f"{API}/buyer/register", json={
        "company_name": "Paket Kaeufer", "contact_name": "K P",
        "email": f"ps_kaeufer_{SUF}@{MAIL}", "password": PW, "phone": "0511 8"}, timeout=30)
    assert r.status_code == 200, r.text[:300]
    K = {"Authorization": f"Bearer {r.json()['token']}"}
    kid = requests.get(f"{API}/buyer/me", headers=K, timeout=30).json()["id"]
    dbx.users.update_one({"id": kid}, {"$set": {"marketplace_access": {
        "active": True, "plan": "monthly",
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()}}})
    # 1) Kaeufer fragt an (Angebot 8.000)
    r = requests.post(f"{API}/marktplatz/listings/{lid}/interesse", headers=K,
                      json={"offer": 8000, "message": "Interesse"}, timeout=30)
    assert r.status_code == 200, r.text[:300]
    iid = r.json()["interest_id"]

    def h_antwort(action, **extra):
        return requests.post(f"{API}/interessen/{iid}/antwort", headers=welt["H"],
                             json={"action": action, "message": "", **extra}, timeout=30)

    def k_antwort(action, **extra):
        return requests.post(f"{API}/interessen/{iid}/kaeufer-antwort", headers=K,
                             json={"action": action, "message": "", **extra}, timeout=30)

    def status():
        return dbx.listing_interest.find_one({"id": iid})

    # 2) Haendler: Gegenangebot 9.500
    assert h_antwort("gegenangebot", counter_offer=9500).status_code == 200
    assert status()["status"] == "gegenangebot"
    # 3) Kaeufer: eigenes Gegenangebot 8.800 (NEU)
    r = k_antwort("gegenangebot", counter_offer=8800)
    assert r.status_code == 200, r.text[:300]
    st = status(); assert st["status"] == "gegenangebot_kaeufer" and st["buyer_counter_offer"] == 8800
    # Kaeufer kann jetzt nicht "annehmen" (es liegt kein Haendler-Angebot vor)
    assert k_antwort("annehmen").status_code == 400
    # Kaeufer-Sicht zeigt den Zustand
    mine = requests.get(f"{API}/buyer/interessen", headers=K, timeout=30).json()
    assert next(x for x in mine if x["id"] == iid)["status"] == "gegenangebot_kaeufer"
    # 4) Haendler darf JEDERZEIT erneut ein Angebot schreiben (vorher: keine Buttons)
    assert h_antwort("gegenangebot", counter_offer=9200).status_code == 200
    assert status()["status"] == "gegenangebot"
    # 5) Kaeufer nochmal 9.000, Haendler akzeptiert -> Preis 9.000, Fahrzeug reserviert
    assert k_antwort("gegenangebot", counter_offer=9000).status_code == 200
    r = h_antwort("akzeptieren")
    assert r.status_code == 200, r.text[:300]
    st = status()
    assert st["status"] == "akzeptiert" and st["agreed_price"] == 9000
    l = dbx.resale_listings.find_one({"id": lid})
    assert l["status"] == "reserviert" and l["reserved_for"] == kid
    # abgeschlossen: keine weiteren Antworten
    assert h_antwort("gegenangebot", counter_offer=1).status_code == 400
    assert k_antwort("gegenangebot", counter_offer=1).status_code == 400
    # Haendler-Liste enthaelt den Verlauf beider Seiten
    li = next(x for x in requests.get(f"{API}/dealer/interessen", headers=welt["H"],
                                      timeout=30).json() if x["id"] == iid)
    von = [h["von"] for h in li["history"]]
    assert von.count("kaeufer") >= 3 and von.count("haendler") >= 3
    # Veroeffentlichtes Inserat loeschen geht (Wunsch: loeschen koennen)
    r = requests.delete(f"{API}/resale/{lid}", headers=welt["H"], timeout=30)
    assert r.status_code == 200, r.text[:300]
    assert dbx.resale_listings.find_one({"id": lid})["status"] == "geloescht"
