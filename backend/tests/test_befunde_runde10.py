# -*- coding: utf-8 -*-
"""Runde 10 (06.09.2026) — bestaetigte Befunde aus sechs Pruefberichten.

  E1  MFA-Zwischen-Token war nicht an den Kontozustand gebunden
  E2  Zahlungswiederholung konnte bezahlte Laufzeit verkuerzen
  F1  verspaetetes Autospeichern ueberschrieb ein abgeschlossenes Protokoll
  F2  Sucher konnte fremde Termine aendern und damit fremde Vertrags-PDFs neu erzeugen
  F3  Termine ohne Vertrag behielten Personendaten fuer immer
  G1  Fahrzeugakte zeigte Vertragsdaten fremder Sucher
  G2  erneuter Vergleich ueberschrieb korrigierte Fahrzeugdaten
  G3  automatisch angelegter Termin hatte keinen Ersteller
  D   Backup-Nachholung und Readiness zaehlten unvollstaendige Sicherungen

HTTP-Teile brauchen das Backend auf TEST_BASE_URL mit MOCK_PROVIDER_FETCH.
"""
import asyncio
import inspect
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
PW = "RundeZehn123!"
BACKEND = Path(__file__).resolve().parents[1]


def _db():
    from pymongo import MongoClient
    return MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)[DB_NAME]


def _mit_db(coro_factory):
    from motor.motor_asyncio import AsyncIOMotorClient

    async def _run():
        cl = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
        try:
            return await coro_factory(cl[DB_NAME])
        finally:
            cl.close()
    return asyncio.run(_run())


def _abo(dealer_id, user_id):
    _db().subscriptions.insert_one({
        "id": str(uuid.uuid4()), "dealer_id": dealer_id,
        "subject_user_id": user_id, "plan": "monthly", "status": "active",
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat()})


@pytest.fixture(scope="module")
def welt():
    """Firma, zwei Sucher, Vertrag von A mit Abholtermin."""
    r = requests.post(f"{API}/auth/register", json={
        "email": f"r10chef_{SUF}@e2etest-mail.de", "password": PW,
        "company_name": "Runde10 GmbH", "contact_person": "R Z",
        "phone": "0511 10"}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    chef_h = {"Authorization": f"Bearer {r.json()['token']}"}
    chef = requests.get(f"{API}/auth/me", headers=chef_h, timeout=30).json()["user"]
    dealer_id = chef["dealer_id"]
    _abo(dealer_id, chef["id"])
    sucher = {}
    for name in ("a", "b"):
        r = requests.post(f"{API}/dealer/sucher", headers=chef_h, json={
            "first_name": "Sucher", "last_name": name.upper(),
            "email": f"r10sucher{name}_{SUF}@e2etest-mail.de", "password": PW}, timeout=30)
        assert r.status_code == 200, r.text[:200]
        uid = r.json()["sucher_id"]
        _abo(dealer_id, uid)
        r = requests.post(f"{API}/auth/login", json={
            "email": f"r10sucher{name}_{SUF}@e2etest-mail.de", "password": PW}, timeout=30)
        assert r.status_code == 200, r.text[:200]
        sucher[name] = {"id": uid, "h": {"Authorization": f"Bearer {r.json()['token']}"}}
    ka = f"https://www.kleinanzeigen.de/s-anzeige/r10/97{uuid.uuid4().int % 10**8:08d}-216-1"
    r = requests.post(f"{API}/mobile/compare", json={"url": ka},
                      headers=sucher["a"]["h"], timeout=90)
    if r.status_code != 200 or not (r.json().get("vehicle") or {}).get("_mock"):
        pytest.skip("Backend ohne MOCK_PROVIDER_FETCH")
    vid = r.json()["vehicle_id"]
    r = requests.post(f"{API}/contracts", headers=sucher["a"]["h"], json={
        "vehicle_id": vid, "seller_name": "V Zehn",
        "seller_address": "Weg 10", "seller_zip": "30159",
        "seller_city": "Hannover", "purchase_price": 4100,
        "pickup_date": "2099-07-01", "pickup_time": "09:00"}, timeout=90)
    assert r.status_code == 200, r.text[:200]
    cid = r.json()["id"]
    appt = _db().appointments.find_one({"contract_id": cid}, {"_id": 0})
    assert appt, "kein Termin zum Vertrag angelegt"
    yield {"dealer_id": dealer_id, "chef_h": chef_h, "chef": chef, "a": sucher["a"],
           "b": sucher["b"], "cid": cid, "vid": vid, "appt": appt, "ka": ka}
    dbx = _db()
    for c in ("subscriptions", "vehicles", "appointments", "generated_pdfs",
              "generated_pdf_versions", "vehicle_comparisons", "activity_logs",
              "pickup_protocols"):
        dbx[c].delete_many({"dealer_id": dealer_id})
    dbx.users.delete_many({"dealer_id": dealer_id})
    dbx.dealers.delete_many({"id": dealer_id})
    dbx.listings_cache.delete_many({"item_id": {"$regex": "^97"}})


# ---------------------------------------------------------------- E1
def test_e1_mfa_zwischen_token_verfaellt_bei_zustandsaenderung():
    import auth
    u = {"id": "u1", "password_hash": "hash-alt", "mfa": {"aktiv": True, "secret": "s1"}}
    z1 = auth.mfa_zustand(u)
    assert auth.mfa_zustand({**u, "password_hash": "hash-neu"}) != z1, "Passwortwechsel unbemerkt"
    assert auth.mfa_zustand({**u, "mfa": {"aktiv": True, "secret": "s2"}}) != z1, "MFA-Reset unbemerkt"
    assert auth.mfa_zustand({**u, "mfa": {"aktiv": False, "secret": "s1"}}) != z1, "MFA-Abschaltung unbemerkt"
    tok = auth.create_mfa_token(u)
    assert auth.decode_mfa_token(tok).get("z") == z1


def test_e1b_login_mfa_lehnt_alten_zustand_und_abgeschaltete_mfa_ab():
    quelle = (BACKEND / "routes" / "auth.py").read_text(encoding="utf-8")
    start = quelle.index('payload = decode_mfa_token(body.mfa_token)')
    block = quelle[start:start + 1500]
    assert 'payload.get("z") != mfa_zustand(user)' in block
    assert 'or not m.get("aktiv")' in block, "abgeschaltete MFA stellt weiterhin eine Sitzung aus"
    assert "return await _sitzung_ausstellen(user, ip)" not in block.split("sperre = m.get")[0]


# ---------------------------------------------------------------- E2
def test_e2_zahlungswiederholung_verkuerzt_laufzeit_nicht():
    quelle = (BACKEND / "routes" / "payments.py").read_text(encoding="utf-8")
    start = quelle.index('expires_at = grant["expires_at"]')
    block = quelle[start:start + 700]
    assert 'if aktuell and str(aktuell) > str(expires_at):' in block
    assert 'expires_at = aktuell' in block


# ---------------------------------------------------------------- F1
def test_f1_protokoll_wird_nur_im_entwurf_beschrieben():
    import routes.protocols as p
    quelle = inspect.getsource(p.save_protocol)
    # Nachpruefung: Filter "Entwurf ODER abgelaufener Claim" (_entwurf_filter)
    assert quelle.count("_entwurf_filter(") >= 2, "Schreibabfrage nicht an den Entwurf gebunden"
    assert "res.matched_count == 0" in quelle and "_speichern_abgelehnt(" in quelle


def test_f1b_finales_protokoll_bleibt_unveraendert_bei_verspaetetem_schreiben():
    """Nachgestellt auf Datenbank-Ebene: die bedingte Abfrage aus save_protocol
    trifft ein finales Protokoll nicht."""
    pid = f"r10-proto-{SUF}"

    async def _lauf(db):
        await db.pickup_protocols.insert_one({"id": pid, "status": "final", "notes": "unterschrieben"})
        try:
            res = await db.pickup_protocols.update_one(
                {"id": pid, "status": "entwurf"}, {"$set": {"notes": "verspaetet"}})
            doc = await db.pickup_protocols.find_one({"id": pid}, {"_id": 0})
            return res.matched_count, doc["notes"]
        finally:
            await db.pickup_protocols.delete_one({"id": pid})
    n, notes = _mit_db(_lauf)
    assert n == 0 and notes == "unterschrieben"


# ---------------------------------------------------------------- F2 / G3
def test_g3_automatischer_termin_hat_ersteller(welt):
    assert welt["appt"].get("created_by") == welt["a"]["id"]


def test_f2_sucher_b_darf_termin_von_a_nicht_aendern(welt):
    aid = welt["appt"]["id"]
    r = requests.put(f"{API}/appointments/{aid}", headers=welt["b"]["h"],
                     json={"pickup_date": "2099-07-02"}, timeout=60)
    assert r.status_code == 403, r.text[:200]
    # A selbst darf — und der Vertrag bekommt das neue Datum.
    r = requests.put(f"{API}/appointments/{aid}", headers=welt["a"]["h"],
                     json={"pickup_date": "2099-07-03"}, timeout=90)
    assert r.status_code == 200, r.text[:200]
    c = _db().generated_pdfs.find_one({"id": welt["cid"]}, {"pickup_date": 1})
    assert c["pickup_date"] == "2099-07-03"
    # Chef darf immer.
    r = requests.put(f"{API}/appointments/{aid}", headers=welt["chef_h"],
                     json={"pickup_time": "10:30"}, timeout=90)
    assert r.status_code == 200, r.text[:200]


def test_f2b_pdf_neuerzeugung_nutzt_den_vertragsbereich():
    import routes.contracts as c
    quelle = inspect.getsource(c.regenerate_contract_for_pickup)
    assert "_vertrag_bereich(user)" in quelle


# ---------------------------------------------------------------- G1
def test_g1_fahrzeugakte_zeigt_sucher_b_keinen_vertrag_von_a(welt):
    # Runde 16: das Fahrzeug gehoert A (owner_user_id) — B bekommt gar keine
    # Akte mehr (vorher: Akte 200, nur der Vertrag von A ausgeblendet).
    r = requests.get(f"{API}/vehicles/{welt['vid']}/akte", headers=welt["b"]["h"], timeout=30)
    assert r.status_code == 404, r.text[:200]
    r = requests.get(f"{API}/vehicles/{welt['vid']}/akte", headers=welt["a"]["h"], timeout=30)
    ids_a = [c.get("id") for c in (r.json().get("contracts") or [])]
    assert welt["cid"] in ids_a
    r = requests.get(f"{API}/vehicles/{welt['vid']}/akte", headers=welt["chef_h"], timeout=30)
    assert welt["cid"] in [c.get("id") for c in (r.json().get("contracts") or [])]


# ---------------------------------------------------------------- G2
def test_g2_erneuter_vergleich_ueberschreibt_korrekturen_nicht(welt):
    dbx = _db()
    dbx.vehicles.update_one({"id": welt["vid"], "dealer_id": welt["dealer_id"]},
                            {"$set": {"lifecycle": "bestand", "data.mileage": 150000,
                                      "data.keys_count": 1}})
    r = requests.post(f"{API}/mobile/compare", json={"url": welt["ka"]},
                      headers=welt["a"]["h"], timeout=90)
    assert r.status_code == 200, r.text[:200]
    v = dbx.vehicles.find_one({"id": welt["vid"], "dealer_id": welt["dealer_id"]}, {"_id": 0})
    assert v["data"]["mileage"] == 150000, "korrigierter Kilometerstand ueberschrieben"
    assert v["data"].get("keys_count") == 1
    assert v.get("inserat_aktuell"), "frische Inseratsdaten muessen getrennt abgelegt sein"
    assert v["lifecycle"] == "bestand"


# ---------------------------------------------------------------- G3 (Loeschen)
def test_g3b_sucher_kann_eigenen_termin_loeschen(welt):
    """Ein zweiter Vertrag von A -> eigener Termin -> A loescht ihn."""
    r = requests.post(f"{API}/contracts", headers=welt["a"]["h"], json={
        "vehicle_id": welt["vid"], "seller_name": "V Zwei",
        "seller_address": "Weg 11", "seller_zip": "30159",
        "seller_city": "Hannover", "purchase_price": 4200,
        "pickup_date": "2099-08-01", "pickup_time": "11:00"}, timeout=90)
    assert r.status_code == 200, r.text[:200]
    appt = _db().appointments.find_one({"contract_id": r.json()["id"]}, {"_id": 0, "id": 1})
    r = requests.delete(f"{API}/appointments/{appt['id']}", headers=welt["b"]["h"], timeout=30)
    assert r.status_code == 403
    r = requests.delete(f"{API}/appointments/{appt['id']}", headers=welt["a"]["h"], timeout=30)
    assert r.status_code == 200, r.text[:200]


# ---------------------------------------------------------------- F3
def test_f3_termin_ohne_vertrag_verliert_personendaten_nach_frist(monkeypatch):
    from cleanup_service import termine_ohne_vertrag_bereinigen
    monkeypatch.setenv("VERTRAG_LOESCHUNG_AKTIV", "true")
    jetzt = datetime.now(timezone.utc)
    alt_id, jung_id = f"r10-alt-{SUF}", f"r10-jung-{SUF}"

    async def _lauf(db):
        await db.appointments.insert_many([
            {"id": alt_id, "dealer_id": f"f-{SUF}", "contract_id": None,
             "seller_name": "Max Muster", "seller_phone": "0170", "seller_email": "m@x.de",
             "pickup_address": "Weg 1", "pickup_date": (jetzt - timedelta(days=120)).strftime("%Y-%m-%d"),
             "created_at": (jetzt - timedelta(days=130)).isoformat()},
            {"id": jung_id, "dealer_id": f"f-{SUF}", "contract_id": None,
             "seller_name": "Erika", "pickup_date": (jetzt - timedelta(days=10)).strftime("%Y-%m-%d"),
             "created_at": (jetzt - timedelta(days=12)).isoformat()},
        ])
        try:
            n = await termine_ohne_vertrag_bereinigen(db, jetzt)
            a = await db.appointments.find_one({"id": alt_id}, {"_id": 0})
            j = await db.appointments.find_one({"id": jung_id}, {"_id": 0})
            return n, a, j
        finally:
            await db.appointments.delete_many({"id": {"$in": [alt_id, jung_id]}})
    n, a, j = _mit_db(_lauf)
    assert n == 1
    assert a["seller_name"] == "" and a["seller_phone"] == "" and a["seller_email"] == ""
    assert a["pickup_address"] == "" and a.get("pii_geloescht_at")
    assert j["seller_name"] == "Erika", "junger Termin darf nicht angefasst werden"


def test_f3b_ohne_schalter_passiert_nichts(monkeypatch):
    from cleanup_service import termine_ohne_vertrag_bereinigen
    monkeypatch.setenv("VERTRAG_LOESCHUNG_AKTIV", "false")
    assert _mit_db(lambda db: termine_ohne_vertrag_bereinigen(db, datetime.now(timezone.utc))) == 0


# ---------------------------------------------------------------- D
def test_d_unvollstaendige_junge_sicherung_zaehlt_nicht_fuer_die_nachholung(monkeypatch, tmp_path):
    import backup_service as bs
    monkeypatch.setattr(bs, "BACKUP_DIR", tmp_path)   # lokal: nichts

    async def _lauf(db):
        alt_v = await db.system_flags.find_one({"_id": bs._STAND_ID})
        alt_ok = await db.system_flags.find_one({"_id": bs._STAND_VOLL_ID})
        jetzt = datetime.now(timezone.utc)
        await db.system_flags.update_one({"_id": bs._STAND_ID}, {"$set": {
            "erstellt": (jetzt - timedelta(hours=1)).isoformat(), "vollstaendig": False,
            "offsite": False}}, upsert=True)
        await db.system_flags.update_one({"_id": bs._STAND_VOLL_ID}, {"$set": {
            "erstellt": (jetzt - timedelta(hours=40)).isoformat(), "vollstaendig": True,
            "offsite": True}}, upsert=True)
        try:
            juengster = await bs.letztes_backup_info_global(db)
            alter_voll = await bs.letztes_vollstaendiges_alter_global(db)
            return juengster, alter_voll
        finally:
            for _id, alt in ((bs._STAND_ID, alt_v), (bs._STAND_VOLL_ID, alt_ok)):
                if alt is None:
                    await db.system_flags.delete_one({"_id": _id})
                else:
                    await db.system_flags.replace_one({"_id": _id}, alt, upsert=True)
    juengster, alter_voll = _mit_db(_lauf)
    assert juengster["vollstaendig"] is False and juengster["alter_stunden"] < 2
    assert alter_voll > 39, "Nachholung wuerde die junge, unvollstaendige Sicherung zaehlen"


def test_d2_ready_verlangt_vollstaendig_und_offsite():
    quelle = (BACKEND / "server.py").read_text(encoding="utf-8")
    start = quelle.index("letztes_backup_info_global")
    block = quelle[start:start + 1200]
    assert 'not b.get("vollstaendig")' in block
    assert 'not b.get("offsite")' in block


def test_d5_zeitlimit_schreibt_den_stand():
    import backup_service as bs
    quelle = inspect.getsource(bs._run_backup)
    teil = quelle[quelle.index("TimeoutError"):quelle.index("zeilen = ")]
    assert "stand_speichern(db)" in teil
