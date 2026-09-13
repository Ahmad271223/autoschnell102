# -*- coding: utf-8 -*-
"""Runde 19 (Befunde Ahmad 10.09.2026):

  * WhatsApp: Verkaeufer-Nummer "0170 …" ergab wa.me/0170… (ungueltig, kein
    Chat) -> Laendervorwahl wird ergaenzt
  * Fahrer-Protokoll/Abholauftrag ohne Schadensskizzen in Produktion (Skizzen
    lagen nur im Frontend-Ordner, nicht im Backend-Image) -> backend/assets
  * Inseratsfotos laden oft nicht -> Bild-Proxy mit signierten, kleinen
    Vorschaubildern (kein offener Proxy)
  * Inserat zeigte "Einkaufspreis 0 €" trotz Vertrag ueber 23.000 € -> der
    Vertragspreis des Suchers gilt bis Abholung/Handeingabe; Preis vor Ort
    (final_price am Termin) ueberschreibt ihn am Kaufvorgang
"""
import io
import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BASE = (os.environ.get("TEST_BASE_URL") or "http://localhost:8001").rstrip("/")
API = f"{BASE}/api"
MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
DB_NAME = os.environ.get("DB_NAME") or "autoschnell"
SUF = uuid.uuid4().hex[:8]
PW = "Runde19Test123!x"
KA_ID = f"95{uuid.uuid4().int % 10**8:08d}"
KA_URL = f"https://www.kleinanzeigen.de/s-anzeige/r19/{KA_ID}-216-1"


# ---------------------------------------------------------------- ohne Server
def test_01_wa_nummer_bekommt_laendervorwahl():
    from routes.contracts import wa_nummer
    assert wa_nummer("+49 170 1234567") == "491701234567"
    assert wa_nummer("0170 1234567") == "491701234567"
    assert wa_nummer("0170-123 45 67") == "491701234567"
    assert wa_nummer("0049 170 1234567") == "491701234567"
    assert wa_nummer("+41 79 123 45 67") == "41791234567"
    assert wa_nummer("") == "" and wa_nummer(None) == ""


def test_02_bild_proxy_signatur_und_allowliste():
    import bild_proxy as bp
    ka = "https://img.kleinanzeigen.de/api/v1/prod-ads/images/ab/abc?rule=$_59.AUTO"
    assert bp.erlaubt(ka) and bp.erlaubt("https://img.classistatic.de/x.jpg") \
        and bp.erlaubt("https://prod.pictures.autoscout24.net/x.jpg")
    assert not bp.erlaubt("http://img.kleinanzeigen.de/x.jpg")       # kein http
    assert not bp.erlaubt("https://evil.example.com/x.jpg")
    assert not bp.erlaubt("https://img.kleinanzeigen.de.evil.com/x.jpg")
    url = bp.bild_url(ka)
    assert url.startswith("/api/bild?u=")
    q = parse_qs(urlparse(url).query)
    assert q["u"][0] == ka and int(q["exp"][0]) > time.time() + 6 * 24 * 3600
    assert bp.gueltig(ka, q["exp"][0], q["sig"][0])
    assert not bp.gueltig(ka, q["exp"][0], "falsch")
    assert not bp.gueltig(ka + "x", q["exp"][0], q["sig"][0])         # andere Adresse
    assert not bp.gueltig(ka, str(int(time.time()) - 10), q["sig"][0])  # abgelaufen
    # Unbekannte/eigene Adressen bleiben unveraendert
    assert bp.bild_url("/api/files/foo.jpg") == "/api/files/foo.jpg"
    assert bp.bild_url("https://evil.example.com/x.jpg") == "https://evil.example.com/x.jpg"
    assert bp.thumbs([ka, "", None, 5]) == [url] or bp.thumbs([ka])[0].startswith("/api/bild?u=")


def test_03_bild_proxy_verkleinert_zu_jpeg():
    import bild_proxy as bp
    from PIL import Image
    im = Image.new("RGBA", (1600, 1200), (200, 30, 30, 255))
    buf = io.BytesIO(); im.save(buf, "PNG")
    klein = bp._verkleinern(buf.getvalue())
    out = Image.open(io.BytesIO(klein))
    assert out.format == "JPEG" and max(out.size) <= bp.THUMB_KANTE
    assert len(klein) < len(buf.getvalue())


def test_04_skizzen_liegen_im_backend_und_abholauftrag_hat_bilder():
    import pickup_pdf_service as P
    from pypdf import PdfReader
    assert (P.SKETCH_DIR / "front.png").exists(), P.SKETCH_DIR
    assert P.SKETCH_DIR.name == "damage" and "backend" in str(P.SKETCH_DIR).replace("\\", "/"), \
        "Skizzen muessen aus backend/assets/damage kommen (im Container gibt es kein frontend/)"
    damages = [{"id": "1", "view": "front", "type_key": "delle", "type_label": "Delle",
                "abbr": "DE", "color": "#eab308", "zone": "Motorhaube", "x": 768, "y": 500}]
    pdf = P.build_pickup_pdf(
        appointment={"id": "a1", "title": "Abholen", "pickup_date": "2099-01-01",
                     "pickup_time": "10:00", "seller_name": "V", "pickup_address": "Weg 1"},
        vehicle={"make_label": "BMW", "model_label": "320d"},
        contract={"contract_data": {"damages": damages, "damages_text": "• Delle: Motorhaube"},
                  "damages": damages, "contract_no": "KV-1"},
        dealer={"company_name": "R19 GmbH"}, driver={"display_name": "Fahrer"})
    assert pdf[:4] == b"%PDF"
    bilder = 0
    for page in PdfReader(io.BytesIO(pdf)).pages:
        res = page.get("/Resources") or {}
        xo = res.get("/XObject") or {}
        bilder += sum(1 for k in xo if xo[k].get("/Subtype") == "/Image")
    assert bilder >= 1, "Abholauftrag muss die Schadensskizze als Bild enthalten"


# ---------------------------------------------------------------- HTTP
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
    r = requests.post(f"{API}/listings/check", json={"url": KA_URL}, headers=headers, timeout=30)
    assert r.status_code == 200, r.text[:200]
    body = r.json()
    if body["status"] != "completed":
        ende = time.monotonic() + 60
        while time.monotonic() < ende:
            st = requests.get(f"{API}/listings/check/{body['job_id']}", headers=headers,
                              timeout=30).json()
            if st["status"] in ("completed", "failed"):
                assert st["status"] == "completed", st
                break
            time.sleep(1)
    r = requests.post(f"{API}/mobile/compare", json={"url": KA_URL}, headers=headers, timeout=60)
    assert r.status_code == 200, r.text[:200]
    if not (r.json().get("vehicle") or {}).get("_mock"):
        pytest.skip("Backend ohne MOCK_PROVIDER_FETCH")
    return r.json()


@pytest.fixture(scope="module")
def welt():
    try:
        if requests.get(f"{API}/health", timeout=5).status_code != 200:
            pytest.skip("Backend nicht erreichbar")
    except requests.RequestException:
        pytest.skip("Backend nicht erreichbar")
    z = {}
    r = requests.post(f"{API}/auth/register", json={
        "email": f"r19_chef_{SUF}@r19test-mail.de", "password": PW,
        "company_name": f"R19 Autohaus {SUF}", "contact_person": "R Chef", "phone": "0511 3"},
        timeout=30)
    if r.status_code != 200:
        pytest.skip(f"Registrierung nicht moeglich ({r.status_code})")
    z["H"] = {"Authorization": f"Bearer {r.json()['token']}"}
    me = requests.get(f"{API}/auth/me", headers=z["H"], timeout=30).json()["user"]
    z["chef"] = me; z["dealer_id"] = me["dealer_id"]
    _seed_sub(z["dealer_id"], me["id"])
    z["compare"] = _vergleich(z["H"])
    z["vehicle_id"] = z["compare"]["vehicle_id"]
    yield z
    dbx = _db()
    for coll in ("users", "subscriptions", "vehicles", "appointments", "generated_pdfs",
                 "generated_pdf_versions", "kaufvorgaenge", "activity_logs",
                 "admin_vehicle_data", "resale_listings"):
        dbx[coll].delete_many({"dealer_id": z["dealer_id"]})
    dbx.dealers.delete_many({"id": z["dealer_id"]})
    dbx.listings_cache.delete_many({"item_id": KA_ID})
    dbx.link_jobs.delete_many({"item_id": KA_ID})


def test_10_vergleich_liefert_vorschaubilder_ueber_den_proxy(welt):
    v = welt["compare"]["vehicle"]
    assert "images_thumbs" in v and isinstance(v["images_thumbs"], list)
    # Mock-Fahrzeug hat keine Fotos; die Zuordnung ist 1:1 zu den Fotos
    assert len(v["images_thumbs"]) == len(v.get("images") or v.get("image_urls") or [])
    # Die Vorschaubild-Adressen landen NICHT im gespeicherten Fahrzeug
    doc = _db().vehicles.find_one({"id": welt["vehicle_id"], "dealer_id": welt["dealer_id"]})
    assert "images_thumbs" not in (doc.get("data") or {})


def test_11_bild_route_kein_offener_proxy(welt):
    assert requests.get(f"{API}/bild", params={"u": "https://evil.example.com/x.jpg",
                                               "exp": "9999999999", "sig": "x"}, timeout=30).status_code == 404
    assert requests.get(f"{API}/bild", params={"u": "https://img.kleinanzeigen.de/api/v1/x",
                                               "exp": "9999999999", "sig": "x"}, timeout=30).status_code == 403
    assert requests.get(f"{API}/bild", timeout=30).status_code == 404


def test_12_whatsapp_link_traegt_laendervorwahl(welt):
    r = requests.post(f"{API}/contracts", headers=welt["H"], json={
        "vehicle_id": welt["vehicle_id"], "seller_name": "R19 Verkaeufer",
        "seller_phone": "0170 1234567", "purchase_price": 23000,
        "pickup_date": "2099-07-01", "pickup_time": "10:00"}, timeout=90)
    assert r.status_code == 200, r.text[:300]
    welt["contract_id"] = r.json()["id"]
    r = requests.post(f"{API}/contracts/{welt['contract_id']}/send", headers=welt["H"], json={
        "channel": "whatsapp", "recipient": "0170 1234567", "message": "Hallo",
        "idempotency_key": str(uuid.uuid4())}, timeout=60)
    assert r.status_code == 200, r.text[:300]
    assert r.json()["wa_url"].startswith("https://wa.me/491701234567?text=")


def test_13_inserat_uebernimmt_vertragspreis_als_einkaufspreis(welt):
    r = requests.post(f"{API}/resale/draft/{welt['vehicle_id']}", headers=welt["H"], timeout=60)
    assert r.status_code == 200, r.text[:300]
    l = r.json()
    welt["listing_id"] = l["id"]
    assert l["purchase_price"] == 23000 and l.get("purchase_price_quelle") == "vertrag"
    assert l["margin"]["purchase_price"] == 23000
    assert l["margin"]["purchase_price_quelle"] == "vertrag"
    # Liste und Einzelabruf ebenso
    r = requests.get(f"{API}/resale/{l['id']}", headers=welt["H"], timeout=30)
    assert r.json()["margin"]["purchase_price"] == 23000
    assert "einkauf_thumbs" in r.json()
    r = requests.get(f"{API}/resale", headers=welt["H"], timeout=30)
    assert next(i for i in r.json() if i["id"] == l["id"])["margin"]["purchase_price"] == 23000


def test_14_fahrzeugakte_nennt_preis_und_quelle(welt):
    r = requests.get(f"{API}/vehicles/{welt['vehicle_id']}/akte", headers=welt["H"], timeout=30)
    assert r.status_code == 200, r.text[:200]
    e = r.json()["einkaufspreis"]
    assert e["preis"] == 23000 and e["quelle"] == "vertrag"


def test_15_preis_vor_ort_ueberschreibt_vertragspreis(welt):
    appts = requests.get(f"{API}/appointments", headers=welt["H"], timeout=30).json()
    appt = next(a for a in appts if a.get("contract_id") == welt["contract_id"])
    r = requests.put(f"{API}/appointments/{appt['id']}", headers=welt["H"],
                     json={"final_price": 21000}, timeout=60)
    assert r.status_code == 200, r.text[:300]
    kv = _db().kaufvorgaenge.find_one({"contract_id": welt["contract_id"]})
    assert kv["purchase_price"] == 21000 and kv.get("preis_quelle") == "vor_ort"
    r = requests.get(f"{API}/vehicles/{welt['vehicle_id']}/akte", headers=welt["H"], timeout=30)
    assert r.json()["einkaufspreis"]["preis"] == 21000
    # Abholung -> realisierter Preis am Fahrzeug = Preis vor Ort
    r = requests.put(f"{API}/appointments/{appt['id']}", headers=welt["H"],
                     json={"status": "abgeholt"}, timeout=60)
    assert r.status_code == 200, r.text[:300]
    v = _db().vehicles.find_one({"id": welt["vehicle_id"], "dealer_id": welt["dealer_id"]})
    assert v["purchase_price"] == 21000


def test_16_vertragsliste_hat_vorschaubilder_feld_nur_mit_fotos(welt):
    items = requests.get(f"{API}/contracts", headers=welt["H"], timeout=30).json()
    mine = next(i for i in items if i["id"] == welt["contract_id"])
    if mine.get("vehicle_image_urls"):
        assert len(mine["vehicle_image_urls_thumbs"]) == min(12, len(mine["vehicle_image_urls"]))
    else:
        assert "vehicle_image_urls_thumbs" not in mine
