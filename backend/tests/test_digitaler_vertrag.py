# -*- coding: utf-8 -*-
"""Digitale Vertragsausfertigung (Wunsch Ahmad, 09.09.2026).

Wird der Kaufvertrag per E-Mail oder WhatsApp verschickt, hat das PDF
keine Unterschriftslinien mehr — unter "Unterschriften" steht der
digitale Vertragstext (Standard: die vier Klauseln; Firma und Sucher
koennen in den Einstellungen dauerhaft einen eigenen Text hinterlegen).

Teil 1 (ohne Server): pdf_service direkt.
Teil 2 (HTTP gegen ein laufendes Backend mit MOCK_PROVIDER_FETCH=true,
wie in CI): Einstellungen Chef/Sucher, Vertrag anlegen, beide Fassungen,
Altvertrag-Nachholen, Archiv-Versionen.
"""
import base64
import io
import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pypdf import PdfReader  # noqa: E402

from pdf_service import (  # noqa: E402
    DIGITAL_VERTRAGSTEXT_STANDARD, digitaler_vertragstext, generate_contract_pdf)

BASE = (os.environ.get("TEST_BASE_URL") or "http://localhost:8001").rstrip("/")
API = f"{BASE}/api"
MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
DB_NAME = os.environ.get("DB_NAME") or "autoschnell"
SUF = uuid.uuid4().hex[:8]
PW = "DigiTest123!"
KA_ID = f"97{uuid.uuid4().int % 10**8:08d}"
KA_URL = f"https://www.kleinanzeigen.de/s-anzeige/digi/{KA_ID}-216-1"

FIRMA_TEXT = f"FIRMENTEXT-{SUF}: Der Vertrag gilt digital.\n\nZweiter Absatz der Firma."
SUCHER_TEXT = f"SUCHERTEXT-{SUF}: Mein eigener Absatz.\n\nNoch ein Absatz."


def _text(pdf_bytes: bytes) -> str:
    return "\n".join((p.extract_text() or "")
                     for p in PdfReader(io.BytesIO(pdf_bytes)).pages)


def _flach(s: str) -> str:
    """pypdf bricht Zeilen — fuer Textvergleiche Whitespace normalisieren."""
    return " ".join((s or "").split())


# ---------------------------------------------------------------------------
# Teil 1: pdf_service ohne Server
# ---------------------------------------------------------------------------
_DEALER = {"company_name": "Digi Autohaus GmbH"}
_VEHICLE = {"make_label": "VW", "model_label": "Golf"}
_CONTRACT = {"seller_name": "Max Muster", "purchase_price": 1000,
             "agb_text": "AGB Absatz eins.", "contract_no": "KV-DIGI"}


def test_01_druckfassung_hat_unterschriftslinien():
    t = _text(generate_contract_pdf(dealer=_DEALER, vehicle=_VEHICLE, contract=_CONTRACT))
    assert "Ort, Datum" in t
    assert "Mit ihrer Unterschrift" in _flach(t)
    assert "digitale Ausfertigung" not in t


def test_02_digitale_fassung_text_statt_linien():
    t = _text(generate_contract_pdf(dealer=_DEALER, vehicle=_VEHICLE,
                                    contract=_CONTRACT, digital=True))
    assert "Ort, Datum" not in t
    assert "Mit ihrer Unterschrift" not in _flach(t)
    assert "UNTERSCHRIFTEN" in t.upper()          # Kopf bleibt, Linien nicht
    assert "digitale Ausfertigung" in t
    f = _flach(t)
    # die vier Standard-Klauseln
    assert "keine Garantie oder Gewährleistung" in f
    assert "Absagen sind nach Vertragsbestätigung" in f
    assert "ihrer Richtigkeit entsprechen" in f
    assert "auch ohne Unterschrift gültig" in f
    # Parteien werden benannt
    assert "Max Muster" in f and "Digi Autohaus GmbH" in f


def test_03_eigener_text_ersetzt_standard():
    c = dict(_CONTRACT, digital_vertragstext="Mein eigener Text.\n\nZweiter Absatz.")
    f = _flach(_text(generate_contract_pdf(dealer=_DEALER, vehicle=_VEHICLE,
                                           contract=c, digital=True)))
    assert "Mein eigener Text." in f and "Zweiter Absatz." in f
    assert "Absagen sind nach Vertragsbestätigung" not in f
    assert "Ort, Datum" not in f


def test_04_helfer_digitaler_vertragstext():
    assert digitaler_vertragstext({}) == DIGITAL_VERTRAGSTEXT_STANDARD
    assert digitaler_vertragstext(None) == DIGITAL_VERTRAGSTEXT_STANDARD
    assert digitaler_vertragstext({"digital_vertragstext": "   \n "}) == DIGITAL_VERTRAGSTEXT_STANDARD
    assert digitaler_vertragstext({"digital_vertragstext": "Eigen"}) == "Eigen"


def test_05_sonderzeichen_im_text_brechen_pdf_nicht():
    c = dict(_CONTRACT, digital_vertragstext="<b>kein HTML</b> & Co. \"Zitat\" 100 % < 200")
    f = _flach(_text(generate_contract_pdf(dealer=_DEALER, vehicle=_VEHICLE,
                                           contract=c, digital=True)))
    assert "kein HTML" in f and "& Co." in f


# ---------------------------------------------------------------------------
# Teil 2: HTTP
# ---------------------------------------------------------------------------
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


@pytest.fixture(scope="module")
def welt():
    try:
        if requests.get(f"{API}/health", timeout=5).status_code != 200:
            pytest.skip("Backend nicht erreichbar")
    except requests.RequestException:
        pytest.skip("Backend nicht erreichbar")
    z = {}
    r = requests.post(f"{API}/auth/register", json={
        "email": f"digi_chef_{SUF}@digitest-mail.de", "password": PW,
        "company_name": f"Digi Autohaus {SUF}", "contact_person": "D Chef",
        "phone": "0511 7"}, timeout=30)
    if r.status_code != 200:
        pytest.skip(f"Registrierung nicht moeglich ({r.status_code}) — SELF_SIGNUP aus?")
    z["H"] = {"Authorization": f"Bearer {r.json()['token']}"}
    me = requests.get(f"{API}/auth/me", headers=z["H"], timeout=30).json()
    z["chef"] = me["user"]
    z["dealer_id"] = me["user"]["dealer_id"]
    r = requests.post(f"{API}/dealer/sucher", headers=z["H"], json={
        "email": f"digi_sucher_{SUF}@digitest-mail.de", "password": PW,
        "first_name": "Digi", "last_name": "Sucher"}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    z["sucher_id"] = r.json()["sucher_id"]
    _seed_sub(z["dealer_id"], z["chef"]["id"])
    _seed_sub(z["dealer_id"], z["sucher_id"])
    r = requests.post(f"{API}/auth/login", json={
        "email": f"digi_sucher_{SUF}@digitest-mail.de", "password": PW}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    z["S"] = {"Authorization": f"Bearer {r.json()['token']}"}
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


def test_10_einstellungen_liefern_standardtext(welt):
    for h in (welt["H"], welt["S"]):
        s = requests.get(f"{API}/dealer/settings", headers=h, timeout=30).json()
        assert s.get("digital_vertragstext_standard") == DIGITAL_VERTRAGSTEXT_STANDARD
        assert not s.get("digital_vertragstext")
        me = requests.get(f"{API}/auth/me", headers=h, timeout=30).json()
        assert me["dealer"].get("digital_vertragstext_standard") == DIGITAL_VERTRAGSTEXT_STANDARD


def test_11_chef_speichert_firmentext(welt):
    r = requests.put(f"{API}/dealer/settings", headers=welt["H"],
                     json={"digital_vertragstext": FIRMA_TEXT}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    s = requests.get(f"{API}/dealer/settings", headers=welt["H"], timeout=30).json()
    assert s["digital_vertragstext"] == FIRMA_TEXT
    # Sucher erbt die Firmenvorgabe
    s = requests.get(f"{API}/dealer/settings", headers=welt["S"], timeout=30).json()
    assert s["digital_vertragstext"] == FIRMA_TEXT
    assert _db().dealers.find_one({"id": welt["dealer_id"]})["digital_vertragstext"] == FIRMA_TEXT


def test_12_sucher_eigener_text_als_override(welt):
    r = requests.put(f"{API}/dealer/settings", headers=welt["S"],
                     json={"digital_vertragstext": SUCHER_TEXT}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    assert r.json()["digital_vertragstext"] == SUCHER_TEXT
    s = requests.get(f"{API}/dealer/settings", headers=welt["S"], timeout=30).json()
    assert s["digital_vertragstext"] == SUCHER_TEXT
    # Chef-Vorgabe unveraendert, Override liegt beim Nutzer
    s = requests.get(f"{API}/dealer/settings", headers=welt["H"], timeout=30).json()
    assert s["digital_vertragstext"] == FIRMA_TEXT
    u = _db().users.find_one({"id": welt["sucher_id"]})
    assert (u.get("settings_override") or {}).get("digital_vertragstext") == SUCHER_TEXT


def test_13_zu_langer_text_wird_abgelehnt(welt):
    r = requests.put(f"{API}/dealer/settings", headers=welt["H"],
                     json={"digital_vertragstext": "x" * 20001}, timeout=30)
    assert r.status_code == 422, r.status_code


def test_20_vertrag_chef_beide_fassungen(welt):
    welt["vehicle_id"] = _vergleich(welt["H"])
    r = requests.post(f"{API}/contracts", headers=welt["H"], json={
        "vehicle_id": welt["vehicle_id"], "seller_name": "Digi Verkaeufer",
        "seller_address": "Weg 3", "seller_zip": "30159",
        "seller_city": "Hannover", "purchase_price": 9000,
        "pickup_date": "2099-04-01", "pickup_time": "10:00"}, timeout=90)
    assert r.status_code == 200, r.text[:300]
    body = r.json()
    welt["contract_chef"] = body["id"]
    assert "pdf_digital_b64" not in body       # nicht doppelt uebertragen
    # Druckfassung (Standard) — mit Unterschriftslinien
    r = requests.get(f"{API}/contracts/{body['id']}/pdf", headers=welt["H"], timeout=60)
    assert r.status_code == 200 and r.content[:4] == b"%PDF"
    assert "Ort, Datum" in _text(r.content)
    # Digitale Fassung — Firmentext statt Linien
    r = requests.get(f"{API}/contracts/{body['id']}/pdf",
                     params={"variante": "digital"}, headers=welt["H"], timeout=60)
    assert r.status_code == 200 and r.content[:4] == b"%PDF"
    f = _flach(_text(r.content))
    assert "Ort, Datum" not in f
    assert f"FIRMENTEXT-{SUF}" in f and "Zweiter Absatz der Firma." in f
    assert "Absagen sind nach Vertragsbestätigung" not in f
    doc = _db().generated_pdfs.find_one({"id": body["id"]})
    assert doc.get("pdf_digital_b64") and doc.get("pdf_b64")
    assert doc["contract_data"]["digital_vertragstext"] == FIRMA_TEXT
    assert _text(base64.b64decode(doc["pdf_digital_b64"])).find("Ort, Datum") < 0


def test_21_vertragsliste_ohne_pdf_inhalte(welt):
    items = requests.get(f"{API}/contracts", headers=welt["H"], timeout=30).json()
    mine = [i for i in items if i["id"] == welt["contract_chef"]]
    assert mine and "pdf_b64" not in mine[0] and "pdf_digital_b64" not in mine[0]


def test_22_vertrag_sucher_nutzt_eigenen_text(welt):
    # Mitbearbeiter-Regel: der Sucher darf fuers gleiche Auto einen eigenen
    # Vertrag anlegen — mit SEINEM digitalen Text.
    r = requests.post(f"{API}/contracts", headers=welt["S"], json={
        "vehicle_id": welt["vehicle_id"], "seller_name": "Digi Verkaeufer",
        "seller_address": "Weg 3", "seller_zip": "30159",
        "seller_city": "Hannover", "purchase_price": 8500,
        "pickup_date": "2099-04-02", "pickup_time": "11:00"}, timeout=90)
    assert r.status_code == 200, r.text[:300]
    welt["contract_sucher"] = r.json()["id"]
    r = requests.get(f"{API}/contracts/{welt['contract_sucher']}/pdf",
                     params={"variante": "digital"}, headers=welt["S"], timeout=60)
    assert r.status_code == 200
    f = _flach(_text(r.content))
    assert f"SUCHERTEXT-{SUF}" in f and f"FIRMENTEXT-{SUF}" not in f
    assert "Ort, Datum" not in f


def test_23_altvertrag_wird_nachgeholt(welt):
    """Vertraege von vor dem Umbau haben keine digitale Fassung: beim ersten
    Abruf (und damit beim Versand) wird sie aus den Vertragsdaten erzeugt
    und gespeichert — mit dem Text des ERSTELLERS, auch wenn der Chef abruft."""
    dbx = _db()
    dbx.generated_pdfs.update_one(
        {"id": welt["contract_sucher"]},
        {"$unset": {"pdf_digital_b64": "", "contract_data.digital_vertragstext": ""}})
    assert "pdf_digital_b64" not in dbx.generated_pdfs.find_one({"id": welt["contract_sucher"]})
    r = requests.get(f"{API}/contracts/{welt['contract_sucher']}/pdf",
                     params={"variante": "digital"}, headers=welt["H"], timeout=60)
    assert r.status_code == 200 and r.content[:4] == b"%PDF"
    f = _flach(_text(r.content))
    assert "Ort, Datum" not in f
    assert f"SUCHERTEXT-{SUF}" in f          # Text des Erstellers (Sucher)
    doc = dbx.generated_pdfs.find_one({"id": welt["contract_sucher"]})
    assert doc.get("pdf_digital_b64")       # einmalig nachgetragen
    assert doc["contract_data"]["digital_vertragstext"] == SUCHER_TEXT
    # Druckfassung unveraendert
    r = requests.get(f"{API}/contracts/{welt['contract_sucher']}/pdf",
                     headers=welt["H"], timeout=60)
    assert "Ort, Datum" in _text(r.content)


def test_24_vorschau_kennt_beide_fassungen(welt):
    payload = {"vehicle_id": welt["vehicle_id"], "seller_name": "Vorschau V",
               "purchase_price": 100}
    r = requests.post(f"{API}/contracts/preview", headers=welt["H"], json=payload, timeout=60)
    assert r.status_code == 200 and "Ort, Datum" in _text(r.content)
    r = requests.post(f"{API}/contracts/preview", params={"variante": "digital"},
                      headers=welt["H"], json=payload, timeout=60)
    assert r.status_code == 200
    f = _flach(_text(r.content))
    assert "Ort, Datum" not in f and f"FIRMENTEXT-{SUF}" in f


def test_25_termin_verschieben_archiviert_beide_fassungen(welt):
    appts = requests.get(f"{API}/appointments", headers=welt["H"], timeout=30).json()
    appt = next(a for a in appts if a.get("contract_id") == welt["contract_chef"])
    r = requests.put(f"{API}/appointments/{appt['id']}", headers=welt["H"],
                     json={"pickup_date": "2099-04-05", "pickup_time": "12:00"}, timeout=90)
    assert r.status_code == 200, r.text[:300]
    doc = _db().generated_pdfs.find_one({"id": welt["contract_chef"]})
    assert int(doc.get("version") or 1) == 2
    assert doc.get("pdf_digital_b64")
    neu = _flach(_text(base64.b64decode(doc["pdf_digital_b64"])))
    assert "05.04.2099" in neu and "Ort, Datum" not in neu
    versionen = requests.get(f"{API}/contracts/{welt['contract_chef']}/versions",
                             headers=welt["H"], timeout=30).json()
    assert versionen and versionen[0]["version"] == 1
    assert "pdf_digital_b64" not in versionen[0] and "pdf_b64" not in versionen[0]
    r = requests.get(f"{API}/contracts/{welt['contract_chef']}/versions/1/pdf",
                     params={"variante": "digital"}, headers=welt["H"], timeout=60)
    assert r.status_code == 200
    alt = _flach(_text(r.content))
    assert "01.04.2099" in alt and "Ort, Datum" not in alt
    r = requests.get(f"{API}/contracts/{welt['contract_chef']}/versions/1/pdf",
                     headers=welt["H"], timeout=60)
    assert "Ort, Datum" in _text(r.content)


def test_26_leerer_text_bedeutet_standard(welt):
    r = requests.put(f"{API}/dealer/settings", headers=welt["H"],
                     json={"digital_vertragstext": ""}, timeout=30)
    assert r.status_code == 200
    assert not requests.get(f"{API}/dealer/settings", headers=welt["H"],
                            timeout=30).json().get("digital_vertragstext")
    payload = {"vehicle_id": welt["vehicle_id"], "seller_name": "Standard V",
               "purchase_price": 100}
    r = requests.post(f"{API}/contracts/preview", params={"variante": "digital"},
                      headers=welt["H"], json=payload, timeout=60)
    f = _flach(_text(r.content))
    assert "Absagen sind nach Vertragsbestätigung" in f
    assert f"FIRMENTEXT-{SUF}" not in f
