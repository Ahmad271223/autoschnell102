# -*- coding: utf-8 -*-
"""Negative Rollentests (Review-Runde 3, Punkt 9) + Regressionen der
Rollentrennung:

- Admin -> Super-Admin: kein Passwortreset, keine Rollenvergabe
- Sucher -> Fahrerverwaltung, fremde Vertraege, fremde Termine
- Fahrer nach Abschluss: Status/Bericht/Protokoll gesperrt
- zwei Haendler mit identischer Fahrzeug-ID: Fahrer sieht das richtige
- Fahrer-Passwortwechsel + Reset-Bestaetigung ueber driver_accounts
- Netzwerk-Mitglieder: Liste (Chef-only), Widerruf, ehrliches network_joined
- Marktplatz nur fuer Zwischenhaendler
- Abo-Freischaltung erhaelt Restlaufzeit + schliesst den Antrag
- Regel-Schema (400 statt spaeterem 500), Sucher-Overrides frieren nicht ein
- Inland/Export: manuelle Suche + AutoScout-URL folgen dem aktiven Profil

Braucht ein laufendes Backend MIT MOCK_PROVIDER_FETCH=true und Mongo-Zugriff.
Reihenfolgeabhaengig — immer die ganze Datei laufen lassen.
"""
import hashlib
import json
import os
import secrets
import sys
import time
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
PW = "RollenTest123!"
KA_ID = f"98{uuid.uuid4().int % 10**8:08d}"
KA_URL = f"https://www.kleinanzeigen.de/s-anzeige/rollen/{KA_ID}-216-1"


def _db():
    from pymongo import MongoClient
    return MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)[DB_NAME]


def _seed_sub(dealer_id, user_id):
    _db().subscriptions.insert_one({
        "id": str(uuid.uuid4()), "dealer_id": dealer_id,
        "subject_user_id": user_id, "plan": "monthly", "status": "active",
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat()})


def _login(mail, pw=PW):
    return requests.post(f"{API}/auth/login", json={"email": mail, "password": pw},
                         timeout=30)


def _hdr(tok):
    return {"Authorization": f"Bearer {tok}"}


def _admin(is_super):
    import bcrypt
    mail = f"rt_{'super' if is_super else 'admin'}_{SUF}@e2etest-mail.de"
    uid = f"rt{'sa' if is_super else 'ad'}_{SUF}"
    doc = {"id": uid, "email": mail, "role": "admin", "active": True,
           "dealer_id": None,
           "password_hash": bcrypt.hashpw(PW.encode(), bcrypt.gensalt()).decode(),
           "created_at": "2026-01-01T00:00:00+00:00"}
    if is_super:
        doc["is_super_admin"] = True
    _db().users.insert_one(doc)
    r = _login(mail)
    assert r.status_code == 200, r.text[:200]
    return uid, _hdr(r.json()["token"])


def _register_dealer(name):
    r = requests.post(f"{API}/auth/register", json={
        "email": f"rt_{name}_{SUF}@e2etest-mail.de", "password": PW,
        "company_name": f"Rollen {name}", "contact_person": "R T",
        "phone": "0511 1"}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    h = _hdr(r.json()["token"])
    me = requests.get(f"{API}/auth/me", headers=h, timeout=30).json()["user"]
    _seed_sub(me["dealer_id"], me["id"])
    return h, me


def _vehicle_for(h):
    r = requests.post(f"{API}/listings/check", json={"url": KA_URL}, headers=h,
                      timeout=30)
    assert r.status_code == 200, r.text[:200]
    body = r.json()
    if body.get("status") != "completed":
        ende = time.monotonic() + 60
        while time.monotonic() < ende:
            st = requests.get(f"{API}/listings/check/{body['job_id']}", headers=h,
                              timeout=30).json()
            if st["status"] in ("completed", "failed"):
                assert st["status"] == "completed", st
                break
            time.sleep(1)
    r = requests.post(f"{API}/mobile/compare", json={"url": KA_URL}, headers=h,
                      timeout=60)
    assert r.status_code == 200, r.text[:200]
    if not (r.json().get("vehicle") or {}).get("_mock"):
        pytest.skip("Backend ohne MOCK_PROVIDER_FETCH")
    return r.json()["vehicle_id"]


@pytest.fixture(scope="module")
def welt():
    z = {}
    yield z
    dbx = _db()
    for did in (z.get("dealer_a"), z.get("dealer_b")):
        if not did:
            continue
        for c in dbx.generated_pdfs.find({"dealer_id": did}, {"admin_vehicle_data_id": 1}):
            if c.get("admin_vehicle_data_id"):
                dbx.admin_vehicle_data.delete_one({"id": c["admin_vehicle_data_id"]})
        for coll in ("users", "subscriptions", "vehicles", "appointments",
                     "generated_pdfs", "generated_pdf_versions", "dealer_drivers",
                     "network_members", "dealer_invites", "plan_requests",
                     "pickup_reports", "pickup_protocols", "activity_logs"):
            dbx[coll].delete_many({"dealer_id": did})
        dbx.dealers.delete_one({"id": did})
    dbx.users.delete_many({"email": {"$regex": f"_{SUF}@"}})
    dbx.driver_accounts.delete_many({"email": {"$regex": f"_{SUF}@"}})
    dbx.password_resets.delete_many({"user_id": z.get("driver_id", "___")})
    dbx.plan_requests.delete_many({"subject_user_id": z.get("sucher_id", "___")})
    dbx.subscriptions.delete_many({"subject_user_id": z.get("sucher_id", "___")})
    dbx.listings_cache.delete_many({"item_id": KA_ID})
    dbx.link_jobs.delete_many({"item_id": KA_ID})


def test_00_aufbau(welt):
    welt["HA"], chef = _register_dealer("chefa")
    welt["chef_a"], welt["dealer_a"] = chef, chef["dealer_id"]
    r = requests.post(f"{API}/dealer/sucher", headers=welt["HA"], json={
        "email": f"rt_sucher_{SUF}@e2etest-mail.de", "password": PW,
        "first_name": "R", "last_name": "Sucher"}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    welt["sucher_id"] = r.json()["sucher_id"]
    _seed_sub(welt["dealer_a"], welt["sucher_id"])
    r = _login(f"rt_sucher_{SUF}@e2etest-mail.de")
    assert r.status_code == 200
    welt["HS"] = _hdr(r.json()["token"])
    welt["vehicle_id"] = _vehicle_for(welt["HA"])

    welt["HB"], chef_b = _register_dealer("chefb")
    welt["dealer_b"] = chef_b["dealer_id"]
    welt["vehicle_id_b"] = _vehicle_for(welt["HB"])

    welt["sa_id"], welt["SA"] = _admin(True)
    welt["ad_id"], welt["A"] = _admin(False)

    r = requests.post(f"{API}/driver/register", json={
        "email": f"rt_fahrer_{SUF}@e2etest-mail.de", "password": PW,
        "display_name": "Rollen Fahrer"}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    welt["D"] = _hdr(r.json()["token"])
    welt["driver_id"] = r.json()["driver"]["id"]
    welt["driver_code"] = r.json()["driver"]["driver_code"]
    r = requests.post(f"{API}/drivers/add", headers=welt["HA"],
                      json={"driver_code": welt["driver_code"]}, timeout=30)
    assert r.status_code == 200, r.text[:200]


# ------------------------------------------------ Admin -> Super-Admin
def test_01_admin_kann_superadmin_nicht_uebernehmen(welt):
    r = requests.put(f"{API}/admin/users/{welt['sa_id']}", headers=welt["A"],
                     json={"password": "Uebernahme12345!"}, timeout=30)
    assert r.status_code == 403, r.text[:200]
    r = requests.put(f"{API}/admin/users/{welt['sucher_id']}", headers=welt["A"],
                     json={"role": "admin"}, timeout=30)
    assert r.status_code == 403, r.text[:200]
    assert _db().users.find_one({"id": welt["sucher_id"]})["role"] == "sucher"
    # Der Super-Admin darf — und nur auf bekannte Rollen
    r = requests.put(f"{API}/admin/users/{welt['sucher_id']}", headers=welt["SA"],
                     json={"role": "hacker"}, timeout=30)
    assert r.status_code == 400
    r = requests.put(f"{API}/admin/users/{welt['sucher_id']}", headers=welt["SA"],
                     json={"role": "admin"}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    r = requests.put(f"{API}/admin/users/{welt['sucher_id']}", headers=welt["SA"],
                     json={"role": "sucher"}, timeout=30)
    assert r.status_code == 200
    # Normaler Admin darf keinen anderen Admin sperren/loeschen
    r = requests.put(f"{API}/admin/users/{welt['sa_id']}", headers=welt["A"],
                     json={"active": False}, timeout=30)
    assert r.status_code in (400, 403)
    r = requests.delete(f"{API}/admin/users/{welt['sa_id']}", headers=welt["A"],
                        timeout=30)
    assert r.status_code in (400, 403)


# ------------------------------------------------ Sucher
def test_02_sucher_fahrerverwaltung_gesperrt(welt):
    r = requests.post(f"{API}/drivers/add", headers=welt["HS"],
                      json={"driver_code": welt["driver_code"]}, timeout=30)
    assert r.status_code == 403, r.text[:200]
    r = requests.delete(f"{API}/drivers/{welt['driver_id']}", headers=welt["HS"],
                        timeout=30)
    assert r.status_code == 403, r.text[:200]
    r = requests.get(f"{API}/drivers", headers=welt["HS"], timeout=30)
    assert r.status_code == 200 and any(d["id"] == welt["driver_id"] for d in r.json())


def test_03_sucher_loescht_keine_fremden_vertraege(welt):
    basis = {"vehicle_id": welt["vehicle_id"], "seller_name": "RT Verkaeufer",
             "seller_address": "Weg 1", "seller_zip": "30159", "seller_city": "H",
             "purchase_price": 5000}
    r = requests.post(f"{API}/contracts", headers=welt["HA"],
                      json={**basis, "pickup_date": "2099-06-01",
                            "pickup_time": "09:00"}, timeout=90)
    assert r.status_code == 200, r.text[:200]
    welt["contract_chef"] = r.json()["id"]
    r = requests.delete(f"{API}/contracts/{welt['contract_chef']}",
                        headers=welt["HS"], timeout=30)
    assert r.status_code == 403, r.text[:200]
    assert _db().generated_pdfs.find_one({"id": welt["contract_chef"]}) is not None
    r = requests.post(f"{API}/contracts", headers=welt["HS"], json=basis, timeout=90)
    assert r.status_code == 200, r.text[:200]
    eigener = r.json()["id"]
    r = requests.delete(f"{API}/contracts/{eigener}", headers=welt["HS"], timeout=30)
    assert r.status_code == 200, r.text[:200]


def test_04_sucher_loescht_keine_fremden_termine(welt):
    appts = requests.get(f"{API}/appointments", headers=welt["HA"], timeout=30).json()
    appt = next(a for a in appts if a.get("contract_id") == welt["contract_chef"])
    welt["appt_chef"] = appt["id"]
    r = requests.delete(f"{API}/appointments/{appt['id']}", headers=welt["HS"],
                        timeout=30)
    assert r.status_code == 403, r.text[:200]
    r = requests.post(f"{API}/appointments", headers=welt["HS"],
                      json={"title": "Eigener Termin", "pickup_date": "2099-06-02"},
                      timeout=30)
    assert r.status_code == 200, r.text[:200]
    eigener = r.json()["id"]
    r = requests.delete(f"{API}/appointments/{eigener}", headers=welt["HS"],
                        timeout=30)
    assert r.status_code == 200, r.text[:200]


# ------------------------------------------------ Fahrer
def test_05_fahrer_nach_abschluss_gesperrt(welt):
    a = welt["appt_chef"]
    r = requests.put(f"{API}/appointments/{a}", headers=welt["HA"],
                     json={"driver_id": welt["driver_id"]}, timeout=60)
    assert r.status_code == 200, r.text[:200]
    r = requests.post(f"{API}/driver/appointments/{a}/report", headers=welt["D"],
                      json={"notes": "vor Ort ok"}, timeout=60)
    assert r.status_code == 200, r.text[:200]
    r = requests.put(f"{API}/appointments/{a}", headers=welt["HA"],
                     json={"status": "nicht abgeholt"}, timeout=60)
    assert r.status_code == 200, r.text[:200]
    r = requests.post(f"{API}/driver/appointments/{a}/report", headers=welt["D"],
                      json={"notes": "nachtraeglich"}, timeout=60)
    assert r.status_code == 409, r.text[:200]
    r = requests.put(f"{API}/driver/appointments/{a}/status", headers=welt["D"],
                     json={"status": "abgeholt"}, timeout=30)
    assert r.status_code == 409, r.text[:200]
    r = requests.put(f"{API}/driver/appointments/{a}/protocol", headers=welt["D"],
                     json={"notes": "neu"}, timeout=30)
    assert r.status_code == 409, r.text[:200]
    r = requests.post(f"{API}/driver/appointments/{a}/protocol/correction",
                      headers=welt["D"], timeout=30)
    assert r.status_code == 409, r.text[:200]
    assert _db().pickup_reports.count_documents({"appointment_id": a}) == 1


def test_06_zwei_haendler_gleiche_fahrzeug_id(welt):
    if welt["vehicle_id_b"] != welt["vehicle_id"]:
        pytest.skip("Fahrzeug-IDs unterscheiden sich je Haendler — Fall tritt nicht auf")
    dbx = _db()
    dbx.vehicles.update_one(
        {"id": welt["vehicle_id"], "dealer_id": welt["dealer_b"]},
        {"$set": {"data.make_label": "FALSCH-B", "data.model_label": "Geheim",
                  "data.image_urls": ["https://b.example/geheimfoto.jpg"]}})
    r = requests.get(f"{API}/driver/appointments", headers=welt["D"], timeout=30)
    assert r.status_code == 200
    blob = json.dumps(r.json())
    mein = next(x for x in r.json() if x["id"] == welt["appt_chef"])
    assert mein["vehicle"]["make"] == "VW", mein["vehicle"]
    assert "FALSCH-B" not in blob and "geheimfoto" not in blob


def test_07_fahrer_passwort_wechsel(welt):
    r = requests.put(f"{API}/driver/password", headers=welt["D"],
                     json={"current_password": "falsch123!", "new_password": "NeuesPw12345!"},
                     timeout=30)
    assert r.status_code == 400
    r = requests.put(f"{API}/driver/password", headers=welt["D"],
                     json={"current_password": PW, "new_password": "NeuesPw12345!"},
                     timeout=30)
    assert r.status_code == 200, r.text[:200]
    assert requests.get(f"{API}/driver/me", headers=welt["D"], timeout=30).status_code == 401
    r = requests.post(f"{API}/driver/login", json={
        "email": f"rt_fahrer_{SUF}@e2etest-mail.de", "password": PW}, timeout=30)
    assert r.status_code == 401
    r = requests.post(f"{API}/driver/login", json={
        "email": f"rt_fahrer_{SUF}@e2etest-mail.de", "password": "NeuesPw12345!"},
        timeout=30)
    assert r.status_code == 200, r.text[:200]
    welt["D"] = _hdr(r.json()["token"])


def test_08_fahrer_passwort_reset_bestaetigung(welt):
    token = secrets.token_urlsafe(32)
    _db().password_resets.insert_one({
        "id": str(uuid.uuid4()), "user_id": welt["driver_id"],
        "account_type": "driver",
        "token_hash": hashlib.sha256(token.encode()).hexdigest(),
        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
        "used": False, "created_at": datetime.now(timezone.utc).isoformat()})
    r = requests.post(f"{API}/auth/password-reset/confirm",
                      json={"token": token, "new_password": "ResetPw12345!"}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    r = requests.post(f"{API}/driver/login", json={
        "email": f"rt_fahrer_{SUF}@e2etest-mail.de", "password": "ResetPw12345!"},
        timeout=30)
    assert r.status_code == 200, r.text[:200]
    welt["D"] = _hdr(r.json()["token"])
    # Token ist verbraucht
    r = requests.post(f"{API}/auth/password-reset/confirm",
                      json={"token": token, "new_password": "NochEinPw12345!"}, timeout=30)
    assert r.status_code == 400


# ------------------------------------------------ Marktplatz
def test_09_netzwerk_mitglieder_und_widerruf(welt):
    r = requests.post(f"{API}/dealer/invites", headers=welt["HA"],
                      json={"validity_hours": 24, "max_uses": 1}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    token = r.json()["token"]
    r = requests.post(f"{API}/buyer/register", json={
        "company_name": "RT Kaeufer", "contact_name": "K R",
        "email": f"rt_kaeufer_{SUF}@e2etest-mail.de", "password": PW,
        "invite_token": token}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    assert r.json()["network_joined"] is True
    welt["K"] = _hdr(r.json()["token"])
    me = requests.get(f"{API}/buyer/me", headers=welt["K"], timeout=30).json()
    welt["buyer_id"] = me["id"]
    assert welt["dealer_a"] in me["network_dealer_ids"]

    assert requests.get(f"{API}/dealer/network/members", headers=welt["HS"],
                        timeout=30).status_code == 403
    r = requests.get(f"{API}/dealer/network/members", headers=welt["HA"], timeout=30)
    assert r.status_code == 200
    assert any(m["buyer_user_id"] == welt["buyer_id"] for m in r.json())
    r = requests.delete(f"{API}/dealer/network/members/{welt['buyer_id']}",
                        headers=welt["HA"], timeout=30)
    assert r.status_code == 200, r.text[:200]
    me = requests.get(f"{API}/buyer/me", headers=welt["K"], timeout=30).json()
    assert welt["dealer_a"] not in me["network_dealer_ids"]
    # Der alte Einmal-Link bringt den Zugang nicht zurueck
    r = requests.post(f"{API}/invites/{token}/redeem", headers=welt["K"], timeout=30)
    assert r.status_code == 400
    # Ungueltige Einladung -> ehrlich network_joined false
    r = requests.post(f"{API}/buyer/register", json={
        "company_name": "RT Kaeufer 2", "contact_name": "K Z",
        "email": f"rt_kaeufer2_{SUF}@e2etest-mail.de", "password": PW,
        "invite_token": "gibt-es-nicht"}, timeout=30)
    assert r.status_code == 200 and r.json()["network_joined"] is False


def test_10_marktplatz_nur_fuer_zwischenhaendler(welt):
    for name in ("HA", "HS", "SA", "A"):
        r = requests.get(f"{API}/marktplatz/zugang", headers=welt[name], timeout=30)
        assert r.status_code == 403, f"{name}: {r.status_code}"
    assert requests.get(f"{API}/marktplatz/zugang", headers=welt["K"],
                        timeout=30).status_code == 200


# ------------------------------------------------ Admin-Freischaltung
def test_11_abo_restlaufzeit_und_antrag_geschlossen(welt):
    dbx = _db()
    req_id = str(uuid.uuid4())
    dbx.plan_requests.insert_one({
        "id": req_id, "type": "sucher_abo", "dealer_id": welt["dealer_a"],
        "subject_user_id": welt["sucher_id"], "status": "offen",
        "created_at": datetime.now(timezone.utc).isoformat()})
    r1 = requests.post(f"{API}/admin/sucher/{welt['sucher_id']}/abo",
                       headers=welt["A"], json={"plan": "monthly"}, timeout=30)
    assert r1.status_code == 200, r1.text[:200]
    r2 = requests.post(f"{API}/admin/sucher/{welt['sucher_id']}/abo",
                       headers=welt["A"], json={"plan": "monthly"}, timeout=30)
    assert r2.status_code == 200
    e1 = datetime.fromisoformat(r1.json()["expires_at"])
    e2 = datetime.fromisoformat(r2.json()["expires_at"])
    assert timedelta(days=29) < (e2 - e1) < timedelta(days=31), (e1, e2)
    assert dbx.plan_requests.find_one({"id": req_id})["status"] == "erledigt"
    assert dbx.subscriptions.count_documents(
        {"subject_user_id": welt["sucher_id"], "status": "active"}) == 1


# ------------------------------------------------ Regeln & Einstellungen
def test_12_regel_schema_verhindert_kaputte_werte(welt):
    r = requests.put(f"{API}/dealer/settings", headers=welt["HA"], json={
        "comparison_rules": {"first_registration": {"mode": "older_exact",
                                                    "years": "zwei"}}}, timeout=30)
    assert r.status_code == 400, r.text[:200]
    r = requests.put(f"{API}/dealer/settings", headers=welt["HA"], json={
        "comparison_rules": {"first_registration": {"mode": "older_exact", "years": "2"},
                             "mileage": {"mode": "plus", "value": 25000},
                             "country": {"mode": "exact", "codes": ["de", "AT"]},
                             "unbekannt": {"mode": "x"}}}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    regeln = _db().dealers.find_one({"id": welt["dealer_a"]})["comparison_rules"]
    assert regeln["first_registration"]["years"] == 2
    assert regeln["country"]["codes"] == ["DE", "AT"]
    assert "unbekannt" not in regeln
    r = requests.post(f"{API}/manual/search", headers=welt["HA"],
                      json={"make": "VW", "model": "Golf"}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    for bad in ({"mileage": {"mode": "plus", "value": True}},
                {"damage": {"mode": "$ne"}}, {"sort": "DROP"}):
        r = requests.put(f"{API}/dealer/settings", headers=welt["HA"],
                         json={"comparison_rules": bad}, timeout=30)
        assert r.status_code == 400, bad


def test_13_sucher_override_friert_chefwerte_nicht_ein(welt):
    r = requests.put(f"{API}/dealer/settings", headers=welt["HA"],
                     json={"profile": {"company_name": "Chef GmbH"}}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    # Sucher speichert (wie die UI) ALLE effektiven Werte zurueck — nur das
    # Telefon weicht wirklich ab.
    r = requests.put(f"{API}/dealer/settings", headers=welt["HS"],
                     json={"profile": {"company_name": "Chef GmbH", "phone": "0511 999"}},
                     timeout=30)
    assert r.status_code == 200, r.text[:200]
    ov = _db().users.find_one({"id": welt["sucher_id"]}).get("settings_override") or {}
    assert ov.get("phone") == "0511 999"
    assert "company_name" not in ov, ov
    r = requests.put(f"{API}/dealer/settings", headers=welt["HA"],
                     json={"profile": {"company_name": "Chef Neu GmbH"}}, timeout=30)
    assert r.status_code == 200
    eff = requests.get(f"{API}/dealer/settings", headers=welt["HS"], timeout=30).json()
    assert eff["company_name"] == "Chef Neu GmbH" and eff["phone"] == "0511 999"
    # Zuruecksetzen auf den Chef-Wert loescht den Override wieder
    chef_phone = _db().dealers.find_one({"id": welt["dealer_a"]}).get("phone")
    r = requests.put(f"{API}/dealer/settings", headers=welt["HS"],
                     json={"profile": {"phone": chef_phone}}, timeout=30)
    assert r.status_code == 200
    ov = _db().users.find_one({"id": welt["sucher_id"]}).get("settings_override") or {}
    assert "phone" not in ov
    assert _db().activity_logs.count_documents(
        {"dealer_id": welt["dealer_a"], "action": "sucher.einstellungen.override"}) >= 2


def test_14_inland_export_manuelle_suche_und_autoscout(welt):
    r = requests.put(f"{API}/dealer/settings", headers=welt["HA"], json={
        "export_rules": {"country": {"mode": "all"}, "damage": {"mode": "ignore"},
                         "first_registration": {"mode": "any"},
                         "mileage": {"mode": "ignore"}, "seller": {"mode": "dealer"}},
        "active_profile": "export"}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    r = requests.post(f"{API}/manual/search", headers=welt["HA"],
                      json={"make": "VW", "model": "Golf"}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    url = r.json()["autoscout_url"]
    assert "cy=" not in url and "damaged_listing" not in url and "custtype=D" in url, url
    r = requests.put(f"{API}/dealer/settings", headers=welt["HA"],
                     json={"active_profile": "inland"}, timeout=30)
    assert r.status_code == 200
    r = requests.post(f"{API}/manual/search", headers=welt["HA"],
                      json={"make": "VW", "model": "Golf"}, timeout=30)
    url = r.json()["autoscout_url"]
    assert "cy=D" in url and "damaged_listing=exclude" in url, url


def test_05b_abholbericht_direkt_nach_abholung_erlaubt(welt):
    """Pruefbericht Runde 4: Der Protokoll-Abschluss setzt 'abgeholt' selbst;
    der Abweichungsbericht kommt danach. Erlaubt ist genau EIN Bericht
    binnen 24 h nach 'abgeholt'; der Status-Aufruf ist idempotent."""
    import uuid as _uuid
    from datetime import datetime, timedelta, timezone
    dbx = _db()
    now = datetime.now(timezone.utc)

    def termin(status_seit):
        aid = f"r4appt_{_uuid.uuid4().hex[:8]}"
        dbx.appointments.insert_one({
            "id": aid, "dealer_id": welt["dealer_a"], "driver_id": welt["driver_id"],
            "vehicle_id": welt["vehicle_id"], "status": "abgeholt",
            "status_changed_at": status_seit.isoformat(),
            "pickup_date": "2099-01-01", "created_at": now.isoformat(),
            "updated_at": now.isoformat()})
        return aid
    frisch = termin(now - timedelta(minutes=5))
    r = requests.post(f"{API}/driver/appointments/{frisch}/report", headers=welt["D"],
                      json={"notes": "Abweichungen nach Protokoll"}, timeout=60)
    assert r.status_code == 200, r.text[:200]
    r = requests.post(f"{API}/driver/appointments/{frisch}/report", headers=welt["D"],
                      json={"notes": "zweite Version"}, timeout=60)
    assert r.status_code == 409, r.text[:200]
    assert dbx.pickup_reports.count_documents({"appointment_id": frisch}) == 1
    r = requests.put(f"{API}/driver/appointments/{frisch}/status", headers=welt["D"],
                     json={"status": "abgeholt"}, timeout=30)
    assert r.status_code == 200 and r.json().get("unveraendert") is True, r.text[:200]
    # Zu spaet (2 Tage nach Abholung): kein Erstbericht mehr
    alt = termin(now - timedelta(days=2))
    r = requests.post(f"{API}/driver/appointments/{alt}/report", headers=welt["D"],
                      json={"notes": "spaet"}, timeout=60)
    assert r.status_code == 409, r.text[:200]
    dbx.appointments.delete_many({"id": {"$in": [frisch, alt]}})
    dbx.pickup_reports.delete_many({"appointment_id": {"$in": [frisch, alt]}})


def test_05c_paralleler_erstbericht_genau_einmal(welt):
    """Runde 5: Zwei gleichzeitige Erstberichte nach 'abgeholt' — genau
    einer wird gespeichert, kein zweiter als 'Korrekturversion'."""
    import threading
    import uuid as _uuid
    from datetime import datetime, timezone
    dbx = _db()
    now = datetime.now(timezone.utc)
    aid = f"r5par_{_uuid.uuid4().hex[:8]}"
    dbx.appointments.insert_one({
        "id": aid, "dealer_id": welt["dealer_a"], "driver_id": welt["driver_id"],
        "vehicle_id": welt["vehicle_id"], "status": "abgeholt",
        "status_changed_at": now.isoformat(), "pickup_date": "2099-01-01",
        "created_at": now.isoformat(), "updated_at": now.isoformat()})
    codes = []
    def go(i):
        codes.append(requests.post(f"{API}/driver/appointments/{aid}/report",
                                   headers=welt["D"], json={"notes": f"parallel {i}"},
                                   timeout=60).status_code)
    ts = [threading.Thread(target=go, args=(i,)) for i in range(6)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert codes.count(200) == 1, codes
    assert dbx.pickup_reports.count_documents({"appointment_id": aid}) == 1
    dbx.appointments.delete_one({"id": aid})
    dbx.pickup_reports.delete_many({"appointment_id": aid})
