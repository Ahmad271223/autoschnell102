# -*- coding: utf-8 -*-
"""WhatsApp-Versand (Wunsch Ahmad, 09.09.2026).

Am Handy uebergibt das Frontend das PDF ueber das Teilen-Menue an WhatsApp
(eigene Nummer des Suchers) und vermerkt das mit methode="teilen". Am PC
oeffnet sich der Chat wie bisher — aber die Nachricht enthaelt einen
zeitlich begrenzten, anmeldefreien Download-Link auf die digitale Fassung
(methode="link", Standard).

Braucht ein laufendes Backend mit MOCK_PROVIDER_FETCH=true (wie in CI).
"""
import io
import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, unquote_plus, urlparse

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pypdf import PdfReader  # noqa: E402

BASE = (os.environ.get("TEST_BASE_URL") or "http://localhost:8001").rstrip("/")
API = f"{BASE}/api"
MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
DB_NAME = os.environ.get("DB_NAME") or "autoschnell"
SUF = uuid.uuid4().hex[:8]
PW = "WaTeilenTest123!"
KA_ID = f"98{uuid.uuid4().int % 10**8:08d}"
KA_URL = f"https://www.kleinanzeigen.de/s-anzeige/wa/{KA_ID}-216-1"


def _text(pdf_bytes: bytes) -> str:
    return "\n".join((p.extract_text() or "")
                     for p in PdfReader(io.BytesIO(pdf_bytes)).pages)


def _db():
    from pymongo import MongoClient
    return MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)[DB_NAME]


def _seed_sub(dealer_id, user_id):
    _db().subscriptions.insert_one({
        "id": str(uuid.uuid4()), "dealer_id": dealer_id,
        "subject_user_id": user_id, "plan": "monthly", "status": "active",
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat()})


def _vergleich(headers):
    r = requests.post(f"{API}/listings/check", json={"url": KA_URL},
                      headers=headers, timeout=30)
    assert r.status_code == 200, r.text[:200]
    body = r.json()
    if body["status"] != "completed":
        ende = time.monotonic() + 60
        while time.monotonic() < ende:
            st = requests.get(f"{API}/listings/check/{body['job_id']}",
                              headers=headers, timeout=30).json()
            if st["status"] in ("completed", "failed"):
                assert st["status"] == "completed", st
                break
            time.sleep(1)
        else:
            pytest.fail("Linkpruefungs-Job wurde nicht fertig")
    r = requests.post(f"{API}/mobile/compare", json={"url": KA_URL},
                      headers=headers, timeout=60)
    assert r.status_code == 200, r.text[:200]
    if not (r.json().get("vehicle") or {}).get("_mock"):
        pytest.skip("Backend ohne MOCK_PROVIDER_FETCH — Test braucht den Mock")
    return r.json()["vehicle_id"]


def _lokal(link: str) -> str:
    """Der Link traegt die oeffentliche Adresse (FRONTEND_URL, z.B.
    localhost:3000 oder app.auto-schnellkauf.de). Im Test rufen wir denselben
    Pfad direkt am Test-Backend ab."""
    u = urlparse(link)
    return f"{BASE}{u.path}"


def _link_aus_wa_url(wa_url: str) -> tuple[str, str]:
    """(Nachrichtentext, Download-Link) aus dem wa.me-Link."""
    q = parse_qs(urlparse(wa_url).query)
    text = unquote_plus(q["text"][0]) if q.get("text") else ""
    links = [w for w in text.split() if "/api/public/vertrag/" in w]
    return text, (links[0] if links else "")


@pytest.fixture(scope="module")
def welt():
    try:
        if requests.get(f"{API}/health", timeout=5).status_code != 200:
            pytest.skip("Backend nicht erreichbar")
    except requests.RequestException:
        pytest.skip("Backend nicht erreichbar")
    z = {}
    r = requests.post(f"{API}/auth/register", json={
        "email": f"wa_chef_{SUF}@watest-mail.de", "password": PW,
        "company_name": f"WA Autohaus {SUF}", "contact_person": "W Chef",
        "phone": "0511 5"}, timeout=30)
    if r.status_code != 200:
        pytest.skip(f"Registrierung nicht moeglich ({r.status_code}) — SELF_SIGNUP aus?")
    z["H"] = {"Authorization": f"Bearer {r.json()['token']}"}
    me = requests.get(f"{API}/auth/me", headers=z["H"], timeout=30).json()
    z["chef"] = me["user"]
    z["dealer_id"] = me["user"]["dealer_id"]
    _seed_sub(z["dealer_id"], z["chef"]["id"])
    z["vehicle_id"] = _vergleich(z["H"])
    r = requests.post(f"{API}/contracts", headers=z["H"], json={
        "vehicle_id": z["vehicle_id"], "seller_name": "WA Verkaeufer",
        "seller_phone": "+49 170 1234567", "purchase_price": 7000}, timeout=90)
    assert r.status_code == 200, r.text[:300]
    z["contract_id"] = r.json()["id"]
    yield z
    dbx = _db()
    for coll in ("users", "subscriptions", "vehicles", "appointments",
                 "generated_pdfs", "generated_pdf_versions", "kaufvorgaenge",
                 "activity_logs", "admin_vehicle_data"):
        if z.get("dealer_id"):
            dbx[coll].delete_many({"dealer_id": z["dealer_id"]})
    dbx.users.delete_many({"email": {"$regex": f"_{SUF}@"}})
    dbx.dealers.delete_many({"id": z.get("dealer_id", "___")})
    dbx.listings_cache.delete_many({"item_id": KA_ID})
    dbx.link_jobs.delete_many({"item_id": KA_ID})


def _senden(welt, **extra):
    body = {"channel": "whatsapp", "recipient": "+49 170 1234567",
            "message": "Hallo, hier ist der Kaufvertrag.",
            "idempotency_key": str(uuid.uuid4()), **extra}
    return requests.post(f"{API}/contracts/{welt['contract_id']}/send",
                         headers=welt["H"], json=body, timeout=60)


def test_01_pc_weg_nachricht_enthaelt_download_link(welt):
    r = _senden(welt)
    assert r.status_code == 200, r.text[:300]
    d = r.json()
    assert d["zustellung"] == "chat_geoeffnet"
    assert d["wa_url"].startswith("https://wa.me/491701234567?text=")
    basis = (os.environ.get("FRONTEND_URL") or "http://localhost:3000").rstrip("/")
    assert d["download_link"].startswith("http") and "/api/public/vertrag/" in d["download_link"]
    if os.environ.get("FRONTEND_URL"):
        assert d["download_link"].startswith(basis)
    token = d["download_link"].rsplit("/", 1)[1]
    assert len(token) >= 32 and token.replace("-", "").replace("_", "").isalnum()
    text, link = _link_aus_wa_url(d["wa_url"])
    assert text.startswith("Hallo, hier ist der Kaufvertrag.")
    assert "Link gültig bis" in text and link == d["download_link"]
    # ~14 Tage gueltig (Standard)
    bis = datetime.fromisoformat(d["link_gueltig_bis"])
    assert timedelta(days=13) < bis - datetime.now(timezone.utc) < timedelta(days=15)
    welt["link"] = d["download_link"]
    welt["token"] = token
    doc = _db().generated_pdfs.find_one({"id": welt["contract_id"]})
    assert doc["freigabe"]["token"] == token and doc["freigabe"]["abrufe"] == 0
    assert doc["status"] == "versand_vorbereitet"
    eintrag = doc["send_status"][-1]
    assert eintrag["methode"] == "link" if "methode" in eintrag else True
    assert eintrag["download_link"] == d["download_link"]


def test_02_link_liefert_digitale_fassung_ohne_anmeldung(welt):
    r = requests.get(_lokal(welt["link"]), timeout=60)
    assert r.status_code == 200, r.text[:200]
    assert r.headers["content-type"].startswith("application/pdf")
    assert r.content[:4] == b"%PDF"
    assert "no-store" in r.headers.get("cache-control", "")
    t = _text(r.content)
    assert "Ort, Datum" not in t                  # digitale Fassung
    assert "digitale Ausfertigung" in t
    doc = _db().generated_pdfs.find_one({"id": welt["contract_id"]})
    assert doc["freigabe"]["abrufe"] == 1 and doc["freigabe"].get("zuletzt_abgerufen")
    assert _db().activity_logs.find_one({"action": "vertrag.link.abgerufen",
                                         "ref": welt["contract_id"]})


def test_03_zweiter_versand_nutzt_denselben_link(welt):
    r = _senden(welt, message="Zweite Nachricht.")
    assert r.status_code == 200, r.text[:300]
    assert r.json()["download_link"] == welt["link"]


def test_04_unbekannter_und_kaputter_token(welt):
    basis = _lokal(welt["link"]).rsplit("/", 1)[0]
    assert requests.get(f"{basis}/{uuid.uuid4().hex}", timeout=30).status_code == 404
    assert requests.get(f"{basis}/../contracts", timeout=30).status_code in (404, 405, 422)
    assert requests.get(f"{basis}/{'x' * 200}", timeout=30).status_code in (404, 414)


def test_05_abgelaufener_link_410_und_neuer_link_beim_naechsten_versand(welt):
    dbx = _db()
    dbx.generated_pdfs.update_one(
        {"id": welt["contract_id"]},
        {"$set": {"freigabe.laeuft_ab": (datetime.now(timezone.utc)
                                         - timedelta(minutes=1)).isoformat()}})
    r = requests.get(_lokal(welt["link"]), timeout=30)
    assert r.status_code == 410, r.status_code
    r = _senden(welt, message="Dritte Nachricht.")
    assert r.status_code == 200, r.text[:300]
    neu = r.json()["download_link"]
    assert neu != welt["link"]
    assert requests.get(_lokal(welt["link"]), timeout=30).status_code == 404   # alter Token weg
    assert requests.get(_lokal(neu), timeout=60).status_code == 200
    welt["link"] = neu


def test_06_handy_weg_teilen_nur_vermerk(welt):
    r = _senden(welt, methode="teilen", recipient="")
    assert r.status_code == 200, r.text[:300]
    d = r.json()
    assert d["zustellung"] == "geteilt" and d["status"] == "ok"
    assert "wa_url" not in d and "download_link" not in d
    doc = _db().generated_pdfs.find_one({"id": welt["contract_id"]})
    assert doc["status"] == "versand_vorbereitet"
    eintrag = doc["send_status"][-1]
    assert eintrag["channel"] == "whatsapp" and eintrag["methode"] == "teilen"
    assert eintrag["zustellung"] == "geteilt" and "download_link" not in eintrag


def test_07_unbekannte_methode_422(welt):
    r = _senden(welt, methode="fax")
    assert r.status_code == 422


def test_08_vertrag_in_loeschung_link_tot(welt):
    dbx = _db()
    dbx.generated_pdfs.update_one({"id": welt["contract_id"]},
                                  {"$set": {"loeschung.status": "laeuft"}})
    try:
        assert requests.get(_lokal(welt["link"]), timeout=30).status_code == 404
    finally:
        dbx.generated_pdfs.update_one({"id": welt["contract_id"]},
                                      {"$unset": {"loeschung": ""}})
    assert requests.get(_lokal(welt["link"]), timeout=60).status_code == 200


def test_09_link_ist_je_ip_gedrosselt():
    """Die Drossel greift je Client-IP (60/min). Ueber HTTP ist sie hier
    nicht sichtbar, weil localhost standardmaessig ausgenommen ist
    (RATE_LIMIT_EXEMPT_LOOPBACK) — deshalb direkt am Limiter des
    Endpunkts mit einer fremden Adresse."""
    import asyncio
    import importlib
    C = importlib.import_module("routes.contracts")
    assert C._link_limiter.max_attempts == 60 and C._link_limiter.window_seconds == 60
    ip = f"203.0.113.{uuid.uuid4().int % 250 + 1}"

    async def lauf():
        return [await C._link_limiter.check(ip) for _ in range(61)]
    erg = asyncio.run(lauf())
    assert all(erg[:60]) and erg[60] is False, "61. Abruf derselben IP muss gedrosselt sein"
