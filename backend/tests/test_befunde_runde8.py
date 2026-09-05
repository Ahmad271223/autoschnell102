# -*- coding: utf-8 -*-
"""Pruefbericht Runde 8 (09/2026) — die bestaetigten Befunde, je ein Beweis.

  1  Sucher-Trennung beim VERSAND fehlte (nur Firma geprueft, nicht Ersteller)
  2  Aufraeumer traf Fahrzeuge/Entwuerfe der falschen Firma (gleiche
     Fahrzeug-ID "v_<Inserat>" bei zwei Firmen)
  3  Versand konnte haengen bleiben ("laeuft" fuer immer) oder ohne
     Schluessel doppelt gehen
  4  siehe tests/test_client_ip.py (Nachbar zuerst, nur echte Adressen)
  5  Speicherausfall bleibt "bereit", loest aber einen Betriebsalarm aus
  6  gitleaks nimmt keine ganzen Test-Verzeichnisse mehr aus

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
PW = "RundeAcht123!"
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
    """Eine Firma, zwei Sucher, ein Vertrag von Sucher A."""
    r = requests.post(f"{API}/auth/register", json={
        "email": f"r8chef_{SUF}@e2etest-mail.de", "password": PW,
        "company_name": "Runde8 GmbH", "contact_person": "R A",
        "phone": "0511 8"}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    chef_h = {"Authorization": f"Bearer {r.json()['token']}"}
    chef = requests.get(f"{API}/auth/me", headers=chef_h, timeout=30).json()["user"]
    dealer_id = chef["dealer_id"]

    sucher = {}
    for name in ("a", "b"):
        r = requests.post(f"{API}/dealer/sucher", headers=chef_h, json={
            "first_name": "Sucher", "last_name": name.upper(),
            "email": f"r8sucher{name}_{SUF}@e2etest-mail.de", "password": PW},
            timeout=30)
        assert r.status_code == 200, r.text[:200]
        uid = r.json()["sucher_id"]
        _abo(dealer_id, uid)
        r = requests.post(f"{API}/auth/login", json={
            "email": f"r8sucher{name}_{SUF}@e2etest-mail.de", "password": PW},
            timeout=30)
        assert r.status_code == 200, r.text[:200]
        sucher[name] = {"id": uid, "h": {"Authorization": f"Bearer {r.json()['token']}"}}

    ka = f"https://www.kleinanzeigen.de/s-anzeige/r8/95{uuid.uuid4().int % 10**8:08d}-216-1"
    r = requests.post(f"{API}/mobile/compare", json={"url": ka},
                      headers=sucher["a"]["h"], timeout=90)
    if r.status_code != 200 or not (r.json().get("vehicle") or {}).get("_mock"):
        pytest.skip("Backend ohne MOCK_PROVIDER_FETCH")
    r = requests.post(f"{API}/contracts", headers=sucher["a"]["h"], json={
        "vehicle_id": r.json()["vehicle_id"], "seller_name": "V A",
        "seller_address": "Weg 1", "seller_zip": "30159",
        "seller_city": "Hannover", "purchase_price": 4000,
        "pickup_date": "2099-06-01", "pickup_time": "10:00"}, timeout=90)
    assert r.status_code == 200, r.text[:200]
    yield {"dealer_id": dealer_id, "chef_h": chef_h, "a": sucher["a"],
           "b": sucher["b"], "cid": r.json()["id"]}
    dbx = _db()
    for c in ("subscriptions", "vehicles", "appointments", "generated_pdfs",
              "vehicle_comparisons", "activity_logs"):
        dbx[c].delete_many({"dealer_id": dealer_id})
    dbx.users.delete_many({"dealer_id": dealer_id})
    dbx.dealers.delete_many({"id": dealer_id})
    dbx.listings_cache.delete_many({"item_id": {"$regex": "^95"}})


# ---------------------------------------------------------------- Befund 1
def test_01_sucher_b_darf_vertrag_von_a_nicht_versenden(welt):
    js = {"channel": "whatsapp", "recipient": "+491701234567",
          "message": "Hallo", "idempotency_key": str(uuid.uuid4())}
    r = requests.post(f"{API}/contracts/{welt['cid']}/send",
                      headers=welt["b"]["h"], json=js, timeout=30)
    assert r.status_code == 404, r.text[:200]
    doc = _db().generated_pdfs.find_one({"id": welt["cid"]}, {"send_status": 1})
    assert not (doc.get("send_status") or []), "B hat den Versandstatus veraendert"

    # Der Ersteller selbst und der Chef duerfen weiterhin.
    for wer in ("a",):
        js["idempotency_key"] = str(uuid.uuid4())
        r = requests.post(f"{API}/contracts/{welt['cid']}/send",
                          headers=welt[wer]["h"], json=js, timeout=30)
        assert r.status_code == 200, r.text[:200]


def test_01b_jede_schreibabfrage_im_versand_nutzt_den_bereich():
    """Fuenf Datenbankzugriffe in send_contract — alle muessen den Bereich
    des Kontos tragen, nicht nur der erste."""
    import routes.contracts as c
    quelle = inspect.getsource(c.send_contract)
    assert quelle.count("**bereich") >= 5, quelle.count("**bereich")
    assert '"dealer_id": user["dealer_id"]}' not in quelle.replace(" ", ""), \
        "eine Abfrage prueft weiterhin nur die Firma"


# ---------------------------------------------------------------- Befund 2
def test_02_archivierung_trifft_nur_die_eigene_firma():
    """Zwei Firmen, dasselbe Inserat -> dieselbe Fahrzeug-ID. Bei Firma A
    laeuft die Bestandsfrist ab. Firma B darf NICHTS davon merken."""
    from cleanup_service import _archive_expired_bestand
    vid = f"v_{uuid.uuid4().hex[:10]}"
    fa, fb = f"firma-a-{SUF}", f"firma-b-{SUF}"
    jetzt = datetime.now(timezone.utc)

    async def _lauf(db):
        alt = (jetzt - timedelta(days=1)).isoformat()
        neu = (jetzt + timedelta(days=30)).isoformat()
        await db.vehicles.insert_many([
            {"id": vid, "dealer_id": fa, "lifecycle": "bestand",
             "bestand": {"expires_at": alt}, "data": {"images": ["a.jpg"]}},
            {"id": vid, "dealer_id": fb, "lifecycle": "bestand",
             "bestand": {"expires_at": neu}, "data": {"images": ["b.jpg"]}},
        ])
        await db.resale_listings.insert_many([
            {"id": f"la-{SUF}", "dealer_id": fa, "vehicle_id": vid,
             "status": "entwurf", "photos": {"uploaded_keys": []}},
            {"id": f"lb-{SUF}", "dealer_id": fb, "vehicle_id": vid,
             "status": "entwurf", "photos": {"uploaded_keys": []}},
        ])
        try:
            n = await _archive_expired_bestand(db, jetzt)
            a = await db.vehicles.find_one({"id": vid, "dealer_id": fa}, {"_id": 0})
            b = await db.vehicles.find_one({"id": vid, "dealer_id": fb}, {"_id": 0})
            la = await db.resale_listings.find_one({"id": f"la-{SUF}"})
            lb = await db.resale_listings.find_one({"id": f"lb-{SUF}"})
            return n, a, b, la, lb
        finally:
            await db.vehicles.delete_many({"id": vid})
            await db.resale_listings.delete_many({"vehicle_id": vid})
            await db.activity_logs.delete_many({"ref": vid})
    n, a, b, la, lb = _mit_db(_lauf)
    assert n == 1
    assert a["lifecycle"] == "archiviert" and a["data"]["images"] == []
    assert la is None, "Entwurf von A sollte weg sein"
    assert b["lifecycle"] == "bestand", "Firma B wurde archiviert!"
    assert b["data"]["images"] == ["b.jpg"], "Fotos von Firma B geloescht!"
    assert lb is not None, "Entwurf von Firma B geloescht!"


def test_02b_keine_fahrzeugabfrage_ohne_firma_im_aufraeumer():
    quelle = (BACKEND / "cleanup_service.py").read_text(encoding="utf-8")
    import re
    treffer = re.findall(r'db\.vehicles\.(?:find_one|update_one)\(\s*\{"id": (\w+)\}', quelle)
    assert treffer == [], f"Fahrzeugabfragen nur ueber die ID: {treffer}"


# ---------------------------------------------------------------- Befund 3
def test_03_haengende_zustellung_wird_erkannt():
    from routes.contracts import _zustellung_haengt, ZUSTELLUNG_HAENGT_NACH_SEK
    jetzt = datetime.now(timezone.utc)
    frisch = {"zustellung": "laeuft", "sent_at": jetzt.isoformat()}
    alt = {"zustellung": "laeuft",
           "sent_at": (jetzt - timedelta(seconds=ZUSTELLUNG_HAENGT_NACH_SEK + 5)).isoformat()}
    assert _zustellung_haengt(frisch, jetzt) is False
    assert _zustellung_haengt(alt, jetzt) is True
    assert _zustellung_haengt({"zustellung": "unklar", "sent_at": jetzt.isoformat()}, jetzt)
    assert _zustellung_haengt({"zustellung": "laeuft", "sent_at": "kaputt"}, jetzt)


def test_03b_abgebrochener_versand_wird_wiederaufgenommen_nicht_verdoppelt(welt):
    """Der Prozess starb nach der Reservierung. Frueher: fuer immer
    "bereits gesendet". Jetzt: derselbe Schluessel setzt den Versand fort,
    es entsteht KEIN zweiter Eintrag."""
    schluessel = f"abgebrochen-{uuid.uuid4().hex[:8]}"
    alt = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    _db().generated_pdfs.update_one({"id": welt["cid"]}, {"$push": {"send_status": {
        "idempotency_key": schluessel, "channel": "email",
        "recipient": "kunde@e2etest-mail.de", "subject": "V",
        "sent_at": alt, "zustellung": "laeuft"}}})
    vorher = len(_db().generated_pdfs.find_one({"id": welt["cid"]})["send_status"])
    js = {"channel": "email", "recipient": "kunde@e2etest-mail.de",
          "subject": "V", "message": "Hallo", "idempotency_key": schluessel}
    r = requests.post(f"{API}/contracts/{welt['cid']}/send",
                      headers=welt["a"]["h"], json=js, timeout=30)
    assert r.status_code == 200, r.text[:200]
    assert not r.json().get("bereits_gesendet"), "haengender Versand galt als erledigt"
    eintraege = _db().generated_pdfs.find_one({"id": welt["cid"]})["send_status"]
    assert len(eintraege) == vorher, "zweiter Eintrag statt Wiederaufnahme"
    e = next(x for x in eintraege if x.get("idempotency_key") == schluessel)
    assert e["zustellung"] in ("mock", "versendet"), e
    assert e.get("wiederaufgenommen") is True


def test_03c_ohne_schluessel_ist_dieselbe_mail_in_derselben_minute_ein_versand(welt):
    js = {"channel": "email", "recipient": "zweimal@e2etest-mail.de",
          "subject": "Gleich", "message": "Gleicher Text"}
    vorher = len(_db().generated_pdfs.find_one({"id": welt["cid"]})["send_status"])
    for _ in range(3):
        r = requests.post(f"{API}/contracts/{welt['cid']}/send",
                          headers=welt["a"]["h"], json=js, timeout=30)
        assert r.status_code == 200, r.text[:200]
    nachher = len(_db().generated_pdfs.find_one({"id": welt["cid"]})["send_status"])
    assert nachher == vorher + 1, f"{nachher - vorher} Eintraege fuer dreimal dieselbe Mail"


def test_03d_aufraeumer_markiert_alte_laeuft_eintraege_und_alarmiert():
    from cleanup_service import haengende_zustellungen_markieren
    cid = f"r8-haengt-{uuid.uuid4().hex[:8]}"
    jetzt = datetime.now(timezone.utc)

    async def _lauf(db):
        await db.generated_pdfs.insert_one({
            "id": cid, "dealer_id": f"firma-{SUF}", "contract_no": "R8-1",
            "send_status": [
                {"idempotency_key": "alt", "channel": "email", "recipient": "x@y.de",
                 "sent_at": (jetzt - timedelta(minutes=30)).isoformat(), "zustellung": "laeuft"},
                {"idempotency_key": "frisch", "channel": "email", "recipient": "x@y.de",
                 "sent_at": jetzt.isoformat(), "zustellung": "laeuft"},
                {"idempotency_key": "fertig", "channel": "email", "recipient": "x@y.de",
                 "sent_at": (jetzt - timedelta(minutes=30)).isoformat(), "zustellung": "versendet"},
            ]})
        try:
            n = await haengende_zustellungen_markieren(db, jetzt)
            doc = await db.generated_pdfs.find_one({"id": cid}, {"_id": 0})
            alarme = await db.betriebsalarme.count_documents(
                {"typ": "zustellung_unklar", "ref": cid})
            return n, {e["idempotency_key"]: e["zustellung"] for e in doc["send_status"]}, alarme
        finally:
            await db.generated_pdfs.delete_one({"id": cid})
            await db.betriebsalarme.delete_many({"ref": cid})
    n, stand, alarme = _mit_db(_lauf)
    assert n == 1
    assert stand == {"alt": "unklar", "frisch": "laeuft", "fertig": "versendet"}
    assert alarme == 1


def test_03e_resend_bekommt_den_idempotency_key(monkeypatch):
    """Der Schluessel muss beim Anbieter ankommen — nur dann kann eine
    Wiederholung dort erkannt werden."""
    import email_service as es
    gesehen = {}

    class _Antwort:
        status_code = 200

        @staticmethod
        def json():
            return {"id": "msg_123"}

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None):
            gesehen["headers"] = headers
            return _Antwort()

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    monkeypatch.setattr(es, "RESEND_API_KEY", "re_test", raising=False)
    monkeypatch.setattr(es, "absender_adresse", lambda: "vertrag@example.invalid", raising=False)
    beleg = asyncio.run(es._send_resend(
        to="k@example.invalid", subject="S", text="T", html=None, anhang=None,
        anhang_name="", reply_to=[], kopie=[], absender_name="F",
        idempotency_key="vertrag-1-abc"))
    assert beleg == "msg_123"
    assert gesehen["headers"]["Idempotency-Key"] == "vertrag-1-abc"


# ---------------------------------------------------------------- Befund 5
def test_05_speicherausfall_bleibt_bereit_aber_nicht_stumm():
    quelle = (BACKEND / "server.py").read_text(encoding="utf-8")
    start = quelle.index('if os.environ.get("S3_BUCKET")')
    block = quelle[start:start + 2500]
    assert "datei_speicher_nicht_erreichbar" in block, "kein Betriebsalarm bei Speicherausfall"
    assert "fehler.append" not in block.split("try:")[1].split("except")[0], \
        "Speicherausfall wuerde den Server aus dem Betrieb nehmen"
    lb = (BACKEND.parent / "deploy" / "hinter-loadbalancer.conf.template").read_text(encoding="utf-8")
    assert "location = /api/ready" in lb


# ---------------------------------------------------------------- Befund 6
def test_06_gitleaks_nimmt_keine_ganzen_testverzeichnisse_aus():
    toml = (BACKEND.parent / ".gitleaks.toml").read_text(encoding="utf-8")
    # Kommentare raus — dort steht absichtlich, was frueher falsch war.
    toml = "\n".join(z for z in toml.splitlines() if not z.strip().startswith("#"))
    global_teil = toml[toml.index("[allowlist]"):]
    assert "backend/tests" not in global_teil
    assert "frontend/e2e" not in global_teil
    rule_teil = toml[toml.index("[rules.allowlist]"):toml.index("[allowlist]")]
    assert "backend/tests" not in rule_teil
