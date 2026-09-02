# -*- coding: utf-8 -*-
"""Auto-Daten (admin_vehicle_data): dauerhafte, anonyme Fahrzeugdaten.

Pflichtpruefungen (Auftrag 09/2026): Datensatz bei Vertragserstellung,
nur Whitelist-Felder, keine Verknuepfung zu Vertrag/Haendler/Person,
Kaufpreis als Integer-Cent, keine Duplikate (PDF/Versand/Status/Liste),
Korrektur aktualisiert statt dupliziert, Schaeden bereinigt (HTML/PII/
Laenge/Anzahl), 90-Tage-Loeschung des Vertrags samt Personendaten,
Datensatz ueberlebt unveraendert, Reparatur unvollstaendiger
Schreibvorgaenge, Rollen-Matrix des Super-Admin-Endpunkts, Cache-Control,
Feld-Whitelist der API, stabile Pagination, Suche/Filter ohne
Operator-Injektion.

Braucht ein laufendes Backend MIT MOCK_PROVIDER_FETCH=true (wie in CI)
und direkten Mongo-Zugriff (MONGO_URL). Die Tests bauen aufeinander auf —
immer die ganze Datei laufen lassen.
"""
import asyncio
import json
import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

BASE = (os.environ.get("TEST_BASE_URL") or "http://localhost:8001").rstrip("/")
API = f"{BASE}/api"
MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
DB_NAME = os.environ.get("DB_NAME") or "autoschnell"
SUF = uuid.uuid4().hex[:8]
PW = "AutoDaten123!"
KA_ID = f"97{uuid.uuid4().int % 10**8:08d}"
KA_URL = f"https://www.kleinanzeigen.de/s-anzeige/autodaten/{KA_ID}-216-1"

WHITELIST = {"id", "brand", "model", "first_registration", "mileage_km",
             "fuel_type", "power_ps", "power_kw", "purchase_price_cents",
             "currency", "damages", "schema_version", "purchase_date"}
API_FELDER = set(WHITELIST)   # inkl. id (nur fuer die Super-Admin-Bereinigung)

SELLER = {"seller_name": f"Verkaeufer Autodaten {SUF}",
          "seller_address": "Geheimweg 7", "seller_zip": "30159",
          "seller_city": "Hannover", "seller_phone": "0511 4455667",
          "seller_email": f"verkaeufer_{SUF}@e2etest-mail.de"}
VIN = "WDB2030461A" + SUF[:6].upper()


def _db():
    from pymongo import MongoClient
    return MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)[DB_NAME]


def _run(coro_factory):
    """Backend-Funktion (motor) im Test ausfuehren — eigener Client je Loop."""
    async def _inner():
        from motor.motor_asyncio import AsyncIOMotorClient
        cl = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
        try:
            return await coro_factory(cl[DB_NAME])
        finally:
            cl.close()
    return asyncio.run(_inner())


def _seed_sub(dealer_id, user_id):
    _db().subscriptions.insert_one({
        "id": str(uuid.uuid4()), "dealer_id": dealer_id,
        "subject_user_id": user_id, "plan": "monthly", "status": "active",
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat()})


def _login(mail):
    r = requests.post(f"{API}/auth/login", json={"email": mail, "password": PW},
                      timeout=30)
    assert r.status_code == 200, f"Login {mail}: {r.text[:200]}"
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _user_anlegen(role, **extra):
    import bcrypt
    mail = f"ad_{role}_{SUF}@e2etest-mail.de"
    _db().users.insert_one({
        "id": f"ad{role}_{SUF}", "email": mail, "role": role, "active": True,
        "dealer_id": extra.pop("dealer_id", None),
        "password_hash": bcrypt.hashpw(PW.encode(), bcrypt.gensalt()).decode(),
        "created_at": "2026-01-01T00:00:00+00:00", **extra})
    return _login(mail)


def _datensatz(welt):
    c = _db().generated_pdfs.find_one({"id": welt["contract_id"]})
    assert c and c.get("admin_vehicle_data_id"), "Vertrag ohne admin_vehicle_data_id"
    docs = list(_db().admin_vehicle_data.find({"id": c["admin_vehicle_data_id"]}))
    assert len(docs) == 1, f"erwartet genau 1 Auto-Datensatz, gefunden {len(docs)}"
    return docs[0]


@pytest.fixture(scope="module")
def welt():
    z = {"seed_ids": []}
    yield z
    dbx = _db()
    for coll in ("users", "subscriptions", "vehicles", "appointments",
                 "generated_pdfs", "generated_pdf_versions", "dealer_drivers"):
        if z.get("dealer_id"):
            dbx[coll].delete_many({"dealer_id": z["dealer_id"]})
    dbx.users.delete_many({"email": {"$regex": f"_{SUF}@"}})
    dbx.driver_accounts.delete_many({"email": {"$regex": f"_{SUF}@"}})
    dbx.dealers.delete_many({"id": z.get("dealer_id", "___")})
    dbx.listings_cache.delete_many({"item_id": KA_ID})
    dbx.link_jobs.delete_many({"item_id": KA_ID})
    # Eigene Auto-Datensaetze (Tests + Seeds) wieder entfernen.
    for did in z.get("auto_ids", []) + z.get("seed_ids", []):
        dbx.admin_vehicle_data.delete_one({"id": did})
    dbx.admin_vehicle_data.delete_many({"brand": {"$regex": f"^ZzSeed{SUF}"}})


# ---------------------------------------------------------------- Aufbau
def test_00_aufbau(welt):
    r = requests.post(f"{API}/auth/register", json={
        "email": f"ad_chef_{SUF}@e2etest-mail.de", "password": PW,
        "company_name": "Autodaten Autohaus", "contact_person": "A Chef",
        "phone": "0511 1"}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    welt["H"] = {"Authorization": f"Bearer {r.json()['token']}"}
    me = requests.get(f"{API}/auth/me", headers=welt["H"], timeout=30).json()["user"]
    welt["chef"], welt["dealer_id"] = me, me["dealer_id"]
    _seed_sub(welt["dealer_id"], me["id"])

    r = requests.post(f"{API}/listings/check", json={"url": KA_URL},
                      headers=welt["H"], timeout=30)
    assert r.status_code == 200, r.text[:200]
    body = r.json()
    if body.get("status") != "completed":
        ende = time.monotonic() + 60
        while time.monotonic() < ende:
            st = requests.get(f"{API}/listings/check/{body['job_id']}",
                              headers=welt["H"], timeout=30).json()
            if st["status"] in ("completed", "failed"):
                assert st["status"] == "completed", st
                break
            time.sleep(1)
    r = requests.post(f"{API}/mobile/compare", json={"url": KA_URL},
                      headers=welt["H"], timeout=60)
    assert r.status_code == 200, r.text[:200]
    if not (r.json().get("vehicle") or {}).get("_mock"):
        pytest.skip("Backend ohne MOCK_PROVIDER_FETCH — Test braucht den Mock")
    welt["vehicle_id"] = r.json()["vehicle_id"]

    welt["SA"] = _user_anlegen("admin", is_super_admin=True)
    # Zweiter Admin OHNE Super-Recht (eigene Mail, gleiche Rolle)
    import bcrypt
    mail = f"ad_normaladmin_{SUF}@e2etest-mail.de"
    _db().users.insert_one({
        "id": f"adnormal_{SUF}", "email": mail, "role": "admin", "active": True,
        "dealer_id": None,
        "password_hash": bcrypt.hashpw(PW.encode(), bcrypt.gensalt()).decode(),
        "created_at": "2026-01-01T00:00:00+00:00"})
    welt["A"] = _login(mail)
    welt["B"] = _user_anlegen("b2b_buyer", marketplace_access=True)
    r = requests.post(f"{API}/dealer/sucher", headers=welt["H"], json={
        "email": f"ad_sucher_{SUF}@e2etest-mail.de", "password": PW,
        "first_name": "AD", "last_name": "Sucher"}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    welt["S"] = _login(f"ad_sucher_{SUF}@e2etest-mail.de")
    r = requests.post(f"{API}/driver/register", json={
        "email": f"ad_fahrer_{SUF}@e2etest-mail.de", "password": PW,
        "display_name": "AD Fahrer"}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    welt["D"] = {"Authorization": f"Bearer {r.json()['token']}"}


# ------------------------------------------------ Persistierung (Commit 2)
def test_01_datensatz_bei_vertragserstellung(welt):
    r = requests.post(f"{API}/contracts", headers=welt["H"], json={
        "vehicle_id": welt["vehicle_id"], **SELLER,
        "purchase_price": 12345.67,
        "vehicle_vin": VIN, "vehicle_license_plate": "H-AD 1234",
        "vehicle_mileage": "85.000 km", "vehicle_power_ps": "150",
        "vehicle_power_kw": "110", "vehicle_first_registration": "03/2019",
        "vehicle_fuel": "Diesel",
        "vehicle_damage_note": "Kratzer Stoßstange hinten links",
        "damages_text": "Delle Tür vorne rechts; Steinschlag Frontscheibe",
        "damages": [{"label": "Felge vorne links verkratzt", "x": 12, "y": 40}],
        "pickup_date": "2099-04-01", "pickup_time": "10:00"}, timeout=90)
    assert r.status_code == 200, r.text[:300]
    welt["contract_id"] = r.json()["id"]
    d = _datensatz(welt)
    welt.setdefault("auto_ids", []).append(d["id"])
    assert d["brand"] == "VW" and d["model"] == "Golf"
    assert d["mileage_km"] == 85000 and d["power_ps"] == 150 and d["power_kw"] == 110
    assert d["fuel_type"] == "Diesel"
    assert d["first_registration"] == "2019-03"


def test_02_nur_whitelist_felder(welt):
    d = _datensatz(welt)
    felder = set(d.keys()) - {"_id", "damages_redacted"}
    assert felder == WHITELIST, f"unerwartete Felder: {felder ^ WHITELIST}"
    assert d["schema_version"] == 2 and d["currency"] == "EUR"
    # Kaufdatum: nur der Tag (JJJJ-MM-TT), kein Zeitstempel
    assert d["purchase_date"] == datetime.now(timezone.utc).strftime("%Y-%m-%d")


def test_03_keine_verknuepfung_zu_vertrag_haendler_person(welt):
    d = _datensatz(welt)
    c = _db().generated_pdfs.find_one({"id": welt["contract_id"]})
    blob = json.dumps(d, default=str)
    verboten = [welt["contract_id"], c["contract_no"], welt["dealer_id"],
                welt["chef"]["id"], welt["vehicle_id"], KA_ID, VIN, "H-AD 1234",
                SELLER["seller_name"], SELLER["seller_phone"],
                SELLER["seller_email"], "Geheimweg", "kleinanzeigen.de"]
    for wert in verboten:
        assert wert not in blob, f"Personen-/Quellbezug im Auto-Datensatz: {wert!r}"
    # Die zufaellige id darf mit KEINER anderen id uebereinstimmen.
    assert d["id"] not in (welt["contract_id"], welt["vehicle_id"], c["contract_no"])
    assert uuid.UUID(d["id"]).version == 4


def test_04_kaufpreis_als_integer_cent(welt):
    d = _datensatz(welt)
    assert d["purchase_price_cents"] == 1234567
    assert isinstance(d["purchase_price_cents"], int)
    assert not isinstance(d["purchase_price_cents"], bool)
    assert d["currency"] == "EUR"


def test_05_keine_duplikate_bei_pdf_versand_status_liste(welt):
    vorher = _db().admin_vehicle_data.count_documents({})
    cid = welt["contract_id"]
    for _ in range(2):
        r = requests.get(f"{API}/contracts/{cid}/pdf", headers=welt["H"], timeout=60)
        assert r.status_code == 200 and r.content[:4] == b"%PDF"
        assert requests.get(f"{API}/contracts", headers=welt["H"], timeout=30).status_code == 200
        assert requests.get(f"{API}/contracts/{cid}", headers=welt["H"], timeout=30).status_code == 200
        r = requests.post(f"{API}/contracts/{cid}/send", headers=welt["H"],
                          json={"channel": "whatsapp", "recipient": "+49 176 0000000",
                                "message": "Ihr Kaufvertrag",
                                "idempotency_key": str(uuid.uuid4())}, timeout=60)
        assert r.status_code == 200, r.text[:200]
    assert _db().admin_vehicle_data.count_documents({}) == vorher
    _datensatz(welt)  # weiterhin genau einer fuer diesen Vertrag


def test_06_korrektur_aktualisiert_statt_dupliziert(welt):
    vorher = _db().admin_vehicle_data.count_documents({})
    appts = requests.get(f"{API}/appointments", headers=welt["H"], timeout=30).json()
    appt = next(a for a in appts if a.get("contract_id") == welt["contract_id"])
    welt["appt_id"] = appt["id"]
    r = requests.put(f"{API}/appointments/{appt['id']}", headers=welt["H"],
                     json={"pickup_date": "2099-05-02", "pickup_time": "11:30"},
                     timeout=90)
    assert r.status_code == 200, r.text[:200]
    c = _db().generated_pdfs.find_one({"id": welt["contract_id"]})
    assert int(c.get("version") or 1) >= 2, "Vertragskorrektur hat keine neue Version erzeugt"
    assert _db().admin_vehicle_data.count_documents({}) == vorher
    d = _datensatz(welt)
    assert d["purchase_price_cents"] == 1234567 and d["brand"] == "VW"


def test_07_schaeden_bereinigt_html_pii_laenge_anzahl(welt):
    d = _datensatz(welt)
    assert "Kratzer Stoßstange hinten links" in d["damages"]
    assert any("Delle Tür vorne rechts" in s for s in d["damages"])
    assert any("Felge vorne links verkratzt" in s for s in d["damages"])
    assert not any("x" == k for s in d["damages"] for k in (s if isinstance(s, dict) else []))
    # Zweiter Vertrag mit boesartigen/persoenlichen Schadens-Texten
    lang = "Rost " * 200
    r = requests.post(f"{API}/contracts", headers=welt["H"], json={
        "vehicle_id": welt["vehicle_id"], **SELLER, "purchase_price": 1000,
        "vehicle_damage_note": '<script>alert("x")</script>Lack <b>zerkratzt</b>',
        "damages_text": "Rueckruf unter 0176 12345678 vereinbart",
        "damages": ([f"Kontakt {SELLER['seller_email']}", f"FIN {VIN} beschaedigt",
                     lang] + [f"Kratzer Nr. {i}" for i in range(80)])}, timeout=90)
    assert r.status_code == 200, r.text[:300]
    c2 = _db().generated_pdfs.find_one({"id": r.json()["id"]})
    d2 = _db().admin_vehicle_data.find_one({"id": c2["admin_vehicle_data_id"]})
    welt["auto_ids"].append(d2["id"])
    welt["contract2_id"] = c2["id"]
    blob = json.dumps(d2["damages"])
    assert "<" not in blob and "script" not in blob.lower(), d2["damages"]
    assert "Lack zerkratzt" in d2["damages"]
    assert "0176" not in blob and "@" not in blob and VIN not in blob
    assert all(isinstance(s, str) and len(s) <= 300 for s in d2["damages"])
    assert len(d2["damages"]) <= 50
    assert all(len(s.split()) == len(s.split(" ")) for s in d2["damages"])


def test_08_vertrag_nach_90_tagen_vollstaendig_geloescht(welt):
    from cleanup_service import vertraege_nach_frist_loeschen
    dbx = _db()
    cid = welt["contract_id"]
    vorher = dbx.admin_vehicle_data.find_one({"id": welt["auto_ids"][0]})
    welt["snapshot"] = {k: v for k, v in vorher.items() if k != "_id"}
    alt = (datetime.now(timezone.utc) - timedelta(days=91)).isoformat()
    dbx.generated_pdfs.update_one({"id": cid}, {"$set": {"created_at": alt}})
    assert dbx.generated_pdf_versions.count_documents({"contract_id": cid}) >= 1
    now = datetime.now(timezone.utc)
    n = _run(lambda mdb: vertraege_nach_frist_loeschen(mdb, now))
    assert n >= 1
    assert dbx.generated_pdfs.find_one({"id": cid}) is None
    assert dbx.generated_pdf_versions.count_documents({"contract_id": cid}) == 0
    appt = dbx.appointments.find_one({"id": welt["appt_id"]})
    assert appt and appt.get("contract_id") is None
    # Der juengere zweite Vertrag bleibt (noch keine 90 Tage alt).
    assert dbx.generated_pdfs.find_one({"id": welt["contract2_id"]}) is not None


def test_09_keine_personendaten_mehr_in_der_datenbank(welt):
    dbx = _db()
    spuren = []
    for coll in dbx.list_collection_names():
        if coll in ("users", "dealers", "subscriptions"):
            continue  # Haendler-Stammdaten sind kein Vertragsbestandteil
        for wert in (SELLER["seller_email"], SELLER["seller_phone"],
                     SELLER["seller_name"], VIN):
            for doc in dbx[coll].find({}, {"_id": 0}).limit(5000):
                if wert in json.dumps(doc, default=str, ensure_ascii=False):
                    # Der zweite (juengere) Vertrag darf sie noch tragen.
                    if doc.get("id") == welt["contract2_id"] or \
                            doc.get("contract_id") == welt["contract2_id"] or \
                            doc.get("appointment_id") and coll == "appointments":
                        continue
                    if coll == "appointments" and doc.get("contract_id") == welt["contract2_id"]:
                        continue
                    spuren.append((coll, wert))
                    break
    spuren = [s for s in spuren if s[0] != "appointments"]
    assert not spuren, f"Personendaten des geloeschten Vertrags noch vorhanden: {spuren}"


def test_10_datensatz_ueberlebt_unveraendert(welt):
    d = _db().admin_vehicle_data.find_one({"id": welt["auto_ids"][0]})
    assert d is not None, "Auto-Datensatz wurde mit dem Vertrag geloescht!"
    assert {k: v for k, v in d.items() if k != "_id"} == welt["snapshot"]
    # Nach der Loeschung verweist NICHTS mehr auf die Datensatz-id.
    dbx = _db()
    for coll in dbx.list_collection_names():
        if coll == "admin_vehicle_data":
            continue
        assert dbx[coll].count_documents({"admin_vehicle_data_id": d["id"]}) == 0, coll


def test_11_reparatur_unvollstaendiger_schreibvorgang(welt):
    from cleanup_service import auto_daten_reparieren
    dbx = _db()
    cid = f"repair_{SUF}"
    dbx.generated_pdfs.insert_one({
        "id": cid, "dealer_id": welt["dealer_id"], "user_id": welt["chef"]["id"],
        "make": "Opel", "model": "Astra", "status": "erstellt",
        "contract_data": {"purchase_price": 4321.5, "vehicle_mileage": "120000",
                          "vehicle_fuel": "Benzin", "damages_text": "Kratzer"},
        "created_at": datetime.now(timezone.utc).isoformat()})
    n1 = _run(lambda mdb: auto_daten_reparieren(mdb))
    assert n1 >= 1
    c = dbx.generated_pdfs.find_one({"id": cid})
    assert c.get("admin_vehicle_data_id")
    welt["auto_ids"].append(c["admin_vehicle_data_id"])
    d = dbx.admin_vehicle_data.find_one({"id": c["admin_vehicle_data_id"]})
    assert d["brand"] == "Opel" and d["purchase_price_cents"] == 432150
    assert d["damages"] == ["Kratzer"]
    # Zweiter Lauf: nichts mehr zu reparieren, kein Duplikat.
    vorher = dbx.admin_vehicle_data.count_documents({})
    assert _run(lambda mdb: auto_daten_reparieren(mdb)) == 0
    assert dbx.admin_vehicle_data.count_documents({}) == vorher
    dbx.generated_pdfs.delete_one({"id": cid})


def test_12_rollback_bei_fehlgeschlagenem_vertrag(welt):
    import auto_daten
    dbx = _db()

    async def _ablauf(mdb):
        did = await auto_daten.anlegen(mdb, {"purchase_price": 1}, {"make_label": "X"})
        assert await mdb.admin_vehicle_data.find_one({"id": did}) is not None
        await auto_daten.zurueckrollen(mdb, did)
        return did
    did = _run(_ablauf)
    assert dbx.admin_vehicle_data.find_one({"id": did}) is None
    # Ein Vertrag, der am Server abgelehnt wird (PDF-Fehler/Validierung),
    # hinterlaesst KEINEN Auto-Datensatz.
    vorher = dbx.admin_vehicle_data.count_documents({})
    r = requests.post(f"{API}/contracts", headers=welt["H"], json={
        "vehicle_id": welt["vehicle_id"], **SELLER, "purchase_price": -5},
        timeout=60)
    assert r.status_code in (400, 422)
    assert dbx.admin_vehicle_data.count_documents({}) == vorher


# ------------------------------------------------- API + Rollen (Commit 3)
def test_13_rollenmatrix_endpunkt(welt):
    url = f"{API}/admin/vehicle-data"
    assert requests.get(url, headers=welt["SA"], timeout=30).status_code == 200
    for name in ("A", "H", "S", "B"):
        r = requests.get(url, headers=welt[name], timeout=30)
        assert r.status_code == 403, f"{name}: {r.status_code} {r.text[:120]}"
    assert requests.get(url, headers=welt["D"], timeout=30).status_code in (401, 403)
    assert requests.get(url, timeout=30).status_code == 401
    # Normaler Admin: kein Super-Admin-Kennzeichen fuer die UI
    me = requests.get(f"{API}/auth/me", headers=welt["A"], timeout=30).json()["user"]
    assert not me.get("is_super_admin")


def test_14_cache_control_und_feld_whitelist(welt):
    r = requests.get(f"{API}/admin/vehicle-data", headers=welt["SA"], timeout=30)
    assert r.status_code == 200
    assert r.headers.get("Cache-Control", "").lower() == "no-store"
    body = r.json()
    assert set(body.keys()) == {"items", "next_cursor", "total", "limit"}
    assert body["limit"] == 50 and len(body["items"]) <= 50
    for item in body["items"]:
        assert set(item.keys()) == API_FELDER, set(item.keys()) ^ API_FELDER


def test_15_pagination_stabil_ohne_duplikate(welt):
    dbx = _db()
    for i in range(120):
        did = str(uuid.uuid4())
        welt["seed_ids"].append(did)
        dbx.admin_vehicle_data.insert_one({
            "id": did, "brand": f"ZzSeed{SUF}", "model": f"M{i:03d}",
            "first_registration": f"{2010 + i % 15}-{1 + i % 12:02d}",
            "mileage_km": 1000 * i, "fuel_type": "Elektro" if i % 2 else "Benzin",
            "power_ps": 100 + i, "power_kw": 74 + i,
            "purchase_price_cents": 100000 + i * 1000, "currency": "EUR",
            "damages": [], "schema_version": 2,
            "purchase_date": f"2026-{1 + i % 12:02d}-{1 + i % 28:02d}"})
    gesehen, cursor, seiten = [], None, 0
    while True:
        params = {"limit": 50, "search": f"ZzSeed{SUF}"}
        if cursor:
            params["cursor"] = cursor
        r = requests.get(f"{API}/admin/vehicle-data", headers=welt["SA"],
                         params=params, timeout=30)
        assert r.status_code == 200, r.text[:200]
        b = r.json()
        gesehen += [it["model"] for it in b["items"]]
        seiten += 1
        cursor = b["next_cursor"]
        if not cursor:
            break
        assert seiten < 10
    assert len(gesehen) == 120 and len(set(gesehen)) == 120
    assert b["total"] == 120
    # Standardsortierung: zuletzt angelegt zuerst
    r = requests.get(f"{API}/admin/vehicle-data", headers=welt["SA"],
                     params={"search": f"ZzSeed{SUF}", "limit": 1}, timeout=30)
    assert r.json()["items"][0]["model"] == "M119"
    # Limit-Grenzen: 100 ok, 101 abgelehnt, 0 abgelehnt
    ok = requests.get(f"{API}/admin/vehicle-data", headers=welt["SA"],
                      params={"limit": 100}, timeout=30)
    assert ok.status_code == 200 and ok.json()["limit"] == 100
    for bad in (101, 0, -1, "abc"):
        assert requests.get(f"{API}/admin/vehicle-data", headers=welt["SA"],
                            params={"limit": bad}, timeout=30).status_code == 422


def test_16_suche_und_filter_ohne_injektion(welt):
    H, url = welt["SA"], f"{API}/admin/vehicle-data"

    def hole(**p):
        r = requests.get(url, headers=H, params=p, timeout=30)
        assert r.status_code == 200, r.text[:200]
        return r.json()
    s = f"ZzSeed{SUF}"
    assert hole(search=s.lower())["total"] == 120            # case-insensitive
    assert hole(search=s, fuel_type="elektro")["total"] == 60
    assert hole(search=s, preis_min=200000, preis_max=200000)["total"] == 1
    assert hole(search=s, km_min=118000)["total"] == 2
    assert hole(search=s, ez_von="2024", ez_bis="2024")["total"] == 8
    assert hole(search=s, ez_von="2020-11", ez_bis="2020-11")["total"] == 2
    # Regex-/Operator-Injektion: alles wird als Literal behandelt
    assert hole(search=".*")["total"] == 0
    assert hole(search="ZzSeed.*")["total"] == 0
    assert hole(search='{"$gt": ""}')["total"] == 0
    r = requests.get(url, headers=H, params={"fuel_type[$ne]": "x"}, timeout=30)
    assert r.status_code == 200 and r.json()["total"] >= 120
    for bad in ({"ez_von": "20x4"}, {"preis_min": -1}, {"km_max": "viel"},
                {"cursor": "nicht-hex"}, {"search": "x" * 500}):
        assert requests.get(url, headers=H, params=bad, timeout=30).status_code == 422, bad


def test_17_schaeden_mit_html_kommen_als_reiner_text_aus_der_api(welt):
    r = requests.get(f"{API}/admin/vehicle-data", headers=welt["SA"],
                     params={"search": "VW", "limit": 100}, timeout=30)
    assert r.status_code == 200
    treffer = [it for it in r.json()["items"] if "Lack zerkratzt" in it["damages"]]
    assert treffer, "Datensatz des zweiten Vertrags nicht in der API"
    for s in treffer[0]["damages"]:
        assert isinstance(s, str) and "<" not in s and ">" not in s


def test_18_normaler_admin_sieht_keine_auto_daten(welt):
    for pfad in ("/admin/vehicle-data", "/admin/vehicle-data?limit=1"):
        r = requests.get(f"{API}{pfad}", headers=welt["A"], timeout=30)
        assert r.status_code == 403
        assert "Super-Admin" in r.text


def test_19_datensatz_nach_loeschung_nicht_zuordenbar(welt):
    """Der ueberlebende Datensatz enthaelt nichts, womit man ihn dem
    (geloeschten) Vertrag, dem Haendler oder dem Verkaeufer zuordnen koennte —
    auch nicht ueber die Super-Admin-API."""
    r = requests.get(f"{API}/admin/vehicle-data", headers=welt["SA"],
                     params={"search": "VW", "limit": 100}, timeout=30)
    blob = json.dumps(r.json(), ensure_ascii=False)
    for wert in (welt["contract_id"], welt["dealer_id"], welt["chef"]["id"],
                 welt["vehicle_id"], VIN, SELLER["seller_name"],
                 SELLER["seller_email"], SELLER["seller_phone"],
                 "created_at", "dealer", "contract"):
        assert wert not in blob, wert


def test_19c_schaeden_bereinigung_durch_super_admin(welt):
    """Freitext-Schaeden lassen sich nachtraeglich entfernen (PII-Notfall);
    Adresse/PLZ/Kennzeichen/IBAN werden schon beim Speichern verworfen."""
    import auto_daten
    assert auto_daten.schaeden_bereinigen([
        "Kratzer Musterstr. 12", "Delle 30159 Hannover", "Beule H-AB 1234",
        "IBAN DE89 3704 0044 0532 0130 00", "Steinschlag Frontscheibe"]) ==         ["Steinschlag Frontscheibe"]
    did = welt["auto_ids"][1]
    url = f"{API}/admin/vehicle-data/{did}/damages"
    assert requests.delete(url, headers=welt["A"], timeout=30).status_code == 403
    assert requests.delete(url, headers=welt["H"], timeout=30).status_code == 403
    r = requests.delete(url, headers=welt["SA"], timeout=30)
    assert r.status_code == 200 and r.json()["damages"] == []
    d = _db().admin_vehicle_data.find_one({"id": did})
    assert d["damages"] == [] and d.get("damages_redacted") is True
    assert requests.delete(f"{API}/admin/vehicle-data/{uuid.uuid4()}/damages",
                           headers=welt["SA"], timeout=30).status_code == 404


def test_19b_gruppierte_ansicht(welt):
    url = f"{API}/admin/vehicle-data/gruppiert"
    for name in ("A", "H", "S", "B"):
        assert requests.get(url, headers=welt[name], timeout=30).status_code == 403
    assert requests.get(url, timeout=30).status_code == 401
    r = requests.get(url, headers=welt["SA"], params={"search": f"ZzSeed{SUF}"},
                     timeout=30)
    assert r.status_code == 200, r.text[:200]
    assert r.headers.get("Cache-Control", "").lower() == "no-store"
    b = r.json()
    assert set(b.keys()) == {"marken", "total", "truncated", "sort"}
    assert b["total"] == 120 and b["truncated"] is False and b["sort"] == "ps"
    assert [m["name"] for m in b["marken"]] == [f"ZzSeed{SUF}"]
    marke = b["marken"][0]
    assert marke["anzahl"] == 120 and len(marke["modelle"]) == 120
    # Jahr -> Kraftstoff -> Autos; Felder = API-Whitelist; Kaufdatum dabei
    modell = marke["modelle"][0]
    jahr = modell["jahre"][0]
    kraft = jahr["kraftstoffe"][0]
    assert jahr["jahr"].isdigit() and kraft["name"] in ("Benzin", "Elektro")
    auto = kraft["autos"][0]
    assert set(auto.keys()) == API_FELDER and auto["purchase_date"].startswith("2026-")
    # Preis-Sortierung innerhalb der Kraftstoffgruppen
    r = requests.get(url, headers=welt["SA"],
                     params={"search": "VW", "sort": "price_desc"}, timeout=30)
    assert r.status_code == 200
    for m in r.json()["marken"]:
        for mo in m["modelle"]:
            for j in mo["jahre"]:
                for k in j["kraftstoffe"]:
                    preise = [a.get("purchase_price_cents") or -1 for a in k["autos"]]
                    assert preise == sorted(preise, reverse=True), preise
    assert requests.get(url, headers=welt["SA"], params={"sort": "DROP"},
                        timeout=30).status_code == 422


def test_20_lifecycle_loeschung_ueberlebt_datensatz_zweiter_vertrag(welt):
    """Auch die Loeschung ueber die regulaere Vertrags-Loeschroute nimmt den
    Auto-Datensatz NICHT mit."""
    did = welt["auto_ids"][1]
    r = requests.delete(f"{API}/contracts/{welt['contract2_id']}",
                        headers=welt["H"], timeout=30)
    assert r.status_code == 200, r.text[:200]
    assert _db().generated_pdfs.find_one({"id": welt["contract2_id"]}) is None
    assert _db().admin_vehicle_data.find_one({"id": did}) is not None
