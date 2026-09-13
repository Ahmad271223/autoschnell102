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
    c = dict(_CONTRACT, digital_vertragstext=DIGITAL_VERTRAGSTEXT_STANDARD)
    t = _text(generate_contract_pdf(dealer=_DEALER, vehicle=_VEHICLE, contract=c))
    # Druckfassung erkennbar an "Mit ihrer Unterschrift" (die Linie
    # "Ort, Datum" entfiel 11.09.2026 zugunsten von "Datum und Ort").
    assert "Mit ihrer Unterschrift" in _flach(t)
    assert "Ort, Datum" not in t
    assert "digitale Ausfertigung" not in t
    # Beschluss 10.09.2026: die Vertragsbedingungen stehen auch im Druck —
    # als eigener Abschnitt, nicht unter "Unterschriften".
    f = _flach(t)
    assert "Allgemeine Vertragsbedingungen" in f and "Absagen sind nach Vertragsbestätigung" in f


def test_01b_druckfassung_nennt_die_elektronische_uebermittlung():
    """Wunsch Ahmad (12.09.2026): Der Satz unter den Unterschriftslinien
    deckt auch den elektronischen Versand ab (Kfz-Kaufvertrag ist formfrei).
    Die digitale Fassung bleibt unveraendert ("ohne Unterschrift gueltig")."""
    c = dict(_CONTRACT, digital_vertragstext=DIGITAL_VERTRAGSTEXT_STANDARD)
    druck = _flach(_text(generate_contract_pdf(dealer=_DEALER, vehicle=_VEHICLE, contract=c)))
    digital = _flach(_text(generate_contract_pdf(dealer=_DEALER, vehicle=_VEHICLE,
                                                 contract=c, digital=True)))
    assert "Mit ihrer Unterschrift bestätigen beide Parteien die Richtigkeit aller Angaben" in druck
    assert "Wird dieser Vertrag elektronisch übermittelt" in druck
    assert "Bestätigung der Vertragsinhalte in Textform" in druck
    assert "eigenhändige Unterschrift ist dann nicht erforderlich" in druck
    assert "elektronisch übermittelt" not in digital
    assert "Dieser Vertrag ist ohne Unterschrift gültig." in digital


def test_02_digitale_fassung_text_statt_linien():
    c = dict(_CONTRACT, digital_vertragstext=DIGITAL_VERTRAGSTEXT_STANDARD)
    t = _text(generate_contract_pdf(dealer=_DEALER, vehicle=_VEHICLE,
                                    contract=c, digital=True))
    assert "Mit ihrer Unterschrift" not in _flach(t)
    assert "UNTERSCHRIFTEN" in t.upper()          # Kopf bleibt, Linien nicht
    assert "digitale Ausfertigung" in t
    f = _flach(t)
    # Beschluss 10.09.2026: die vier Klauseln unter "Allgemeine
    # Vertragsbedingungen"; unter "Unterschriften" nur der eine Satz.
    assert "Allgemeine Vertragsbedingungen" in f
    assert "keine Garantie oder Gewährleistung" in f
    assert "Absagen sind nach Vertragsbestätigung" in f
    assert "ihrer Richtigkeit entsprechen" in f
    assert "Dieser Vertrag ist ohne Unterschrift gültig." in f
    assert "Max Muster" in f and "Digi Autohaus GmbH" in f


def test_02b_ohne_gespeicherten_text_keine_bedingungen():
    """Kein Text im Vertrag -> kein Abschnitt (nie ein heutiger Standard aus
    der Luft); digital steht trotzdem der eine Satz."""
    f = _flach(_text(generate_contract_pdf(dealer=_DEALER, vehicle=_VEHICLE,
                                           contract=_CONTRACT, digital=True)))
    assert "Allgemeine Vertragsbedingungen" not in f
    assert "Absagen sind nach Vertragsbestätigung" not in f
    assert "Dieser Vertrag ist ohne Unterschrift gültig." in f


def test_03_eigener_text_ersetzt_standard():
    c = dict(_CONTRACT, digital_vertragstext="Mein eigener Text.\n\nZweiter Absatz.")
    f = _flach(_text(generate_contract_pdf(dealer=_DEALER, vehicle=_VEHICLE,
                                           contract=c, digital=True)))
    assert "Mein eigener Text." in f and "Zweiter Absatz." in f
    assert "Absagen sind nach Vertragsbestätigung" not in f
    assert "Mit ihrer Unterschrift" not in f


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
    """Runde 26 (Zusammenlegung der zwei Textfelder): Neue Firmen starten
    mit dem Startertext (vier Klauseln + AGB-Punkte) im EINEN Feld. Wer das
    Feld leert, bekommt weiterhin den Standardtext."""
    for h in (welt["H"], welt["S"]):
        s = requests.get(f"{API}/dealer/settings", headers=h, timeout=30).json()
        assert s.get("digital_vertragstext_standard") == DIGITAL_VERTRAGSTEXT_STANDARD
        assert DIGITAL_VERTRAGSTEXT_STANDARD in (s.get("digital_vertragstext") or "")
        assert "Gerichtsstand" in (s.get("digital_vertragstext") or ""), "AGB-Punkte dabei"
        me = requests.get(f"{API}/auth/me", headers=h, timeout=30).json()
        assert me["dealer"].get("digital_vertragstext_standard") == DIGITAL_VERTRAGSTEXT_STANDARD
    # Feld leeren -> wieder der Standardtext
    r = requests.put(f"{API}/dealer/settings", headers=welt["H"],
                     json={"digital_vertragstext": ""}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    s = requests.get(f"{API}/dealer/settings", headers=welt["H"], timeout=30).json()
    assert not s.get("digital_vertragstext")


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
        "pickup_date": "2099-04-01", "pickup_time": "10:00",
        # Runde 22: Empfangsdatum wie im Formular = Abholdatum (test_25)
        "empfang_datum": "2099-04-01", "empfang_ort_verkaeufer": "Hannover"}, timeout=90)
    assert r.status_code == 200, r.text[:300]
    body = r.json()
    welt["contract_chef"] = body["id"]
    assert "pdf_digital_b64" not in body       # nicht doppelt uebertragen
    # Druckfassung (Standard) — mit Unterschriftslinien
    r = requests.get(f"{API}/contracts/{body['id']}/pdf", headers=welt["H"], timeout=60)
    assert r.status_code == 200 and r.content[:4] == b"%PDF"
    assert "Mit ihrer Unterschrift" in _flach(_text(r.content))
    # Digitale Fassung — Firmentext statt Linien
    r = requests.get(f"{API}/contracts/{body['id']}/pdf",
                     params={"variante": "digital"}, headers=welt["H"], timeout=60)
    assert r.status_code == 200 and r.content[:4] == b"%PDF"
    f = _flach(_text(r.content))
    assert "Mit ihrer Unterschrift" not in f
    assert f"FIRMENTEXT-{SUF}" in f and "Zweiter Absatz der Firma." in f
    assert "Absagen sind nach Vertragsbestätigung" not in f
    doc = _db().generated_pdfs.find_one({"id": body["id"]})
    assert doc.get("pdf_digital_b64") and doc.get("pdf_b64")
    assert doc["contract_data"]["digital_vertragstext"] == FIRMA_TEXT
    assert "Mit ihrer Unterschrift" not in _flach(_text(base64.b64decode(doc["pdf_digital_b64"])))


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
    assert "Mit ihrer Unterschrift" not in f


def test_23_altvertrag_behaelt_seinen_gespeicherten_text(welt):
    """Fehlt nur die digitale PDF-Fassung, wird sie aus dem im Vertrag
    GESPEICHERTEN Text erzeugt — unabhaengig davon, wer abruft und was
    heute in den Einstellungen steht."""
    dbx = _db()
    dbx.generated_pdfs.update_one({"id": welt["contract_sucher"]},
                                  {"$unset": {"pdf_digital_b64": ""}})
    # Der Sucher aendert HEUTE seinen Text — der Altvertrag darf ihn nicht bekommen.
    r = requests.put(f"{API}/dealer/settings", headers=welt["S"],
                     json={"digital_vertragstext": f"NEUERTEXT-{SUF}: heute geaendert."},
                     timeout=30)
    assert r.status_code == 200
    try:
        r = requests.get(f"{API}/contracts/{welt['contract_sucher']}/pdf",
                         params={"variante": "digital"}, headers=welt["H"], timeout=60)
        assert r.status_code == 200 and r.content[:4] == b"%PDF"
        f = _flach(_text(r.content))
        assert "Mit ihrer Unterschrift" not in f
        assert f"SUCHERTEXT-{SUF}" in f            # Stand der Vertragserstellung
        assert f"NEUERTEXT-{SUF}" not in f         # NICHT der heutige Text
        doc = dbx.generated_pdfs.find_one({"id": welt["contract_sucher"]})
        assert doc.get("pdf_digital_b64")
        assert doc["contract_data"]["digital_vertragstext"] == SUCHER_TEXT
        assert not doc.get("pdf_digital_nachtraeglich")
    finally:
        requests.put(f"{API}/dealer/settings", headers=welt["S"],
                     json={"digital_vertragstext": SUCHER_TEXT}, timeout=30)
    r = requests.get(f"{API}/contracts/{welt['contract_sucher']}/pdf",
                     headers=welt["H"], timeout=60)
    assert "Mit ihrer Unterschrift" in _flach(_text(r.content))        # Druckfassung unveraendert


def test_23b_echter_altvertrag_wird_als_nachtraeglich_gekennzeichnet(welt):
    """Blocker-Befund 09.09.2026: Ein Vertrag von VOR der digitalen
    Ausfertigung hat keinen gespeicherten Text. Dann darf KEIN heute
    eingestellter Text eingesetzt werden — die Fassung nennt sich
    ausdruecklich nachtraeglich erzeugt, und der Vertragsinhalt
    (contract_data) bleibt unveraendert."""
    dbx = _db()
    dbx.generated_pdfs.update_one(
        {"id": welt["contract_sucher"]},
        {"$unset": {"pdf_digital_b64": "", "contract_data.digital_vertragstext": ""}})
    r = requests.get(f"{API}/contracts/{welt['contract_sucher']}/pdf",
                     params={"variante": "digital"}, headers=welt["H"], timeout=60)
    assert r.status_code == 200
    f = _flach(_text(r.content))
    assert "nachträglich erzeugt" in f
    assert f"SUCHERTEXT-{SUF}" not in f and f"FIRMENTEXT-{SUF}" not in f
    assert "Absagen sind nach Vertragsbestätigung" not in f   # auch nicht der Standard
    doc = dbx.generated_pdfs.find_one({"id": welt["contract_sucher"]})
    assert "digital_vertragstext" not in (doc.get("contract_data") or {}),         "historischer Vertragsinhalt darf nicht nachtraeglich ergaenzt werden"
    assert doc.get("pdf_digital_nachtraeglich") is True


def test_23c_abruf_ist_unabhaengig_vom_abrufenden_und_stabil(welt):
    """Auch wenn das Ersteller-Konto fehlt, haengt die nacherzeugte Fassung
    nicht am Abrufenden: Chef und Sucher bekommen dasselbe Dokument, und
    zwei Abrufe liefern denselben Inhalt (frueher konnten parallele Abrufe
    verschiedene PDFs zurueckgeben)."""
    dbx = _db()
    dbx.generated_pdfs.update_one({"id": welt["contract_sucher"]},
                                  {"$unset": {"pdf_digital_b64": ""}})
    dbx.generated_pdfs.update_one({"id": welt["contract_sucher"]},
                                  {"$set": {"user_id": f"geloescht_{SUF}"}})
    try:
        chef = requests.get(f"{API}/contracts/{welt['contract_sucher']}/pdf",
                            params={"variante": "digital"}, headers=welt["H"], timeout=60)
        sucher = requests.get(f"{API}/contracts/{welt['contract_sucher']}/pdf",
                              params={"variante": "digital"}, headers=welt["H"], timeout=60)
        assert chef.status_code == sucher.status_code == 200
        assert _flach(_text(chef.content)) == _flach(_text(sucher.content))
        f = _flach(_text(chef.content))
        assert "nachträglich erzeugt" in f or f"SUCHERTEXT-{SUF}" in f
        assert f"FIRMENTEXT-{SUF}" not in f, "Text des Abrufenden darf nie einfliessen"
    finally:
        dbx.generated_pdfs.update_one(
            {"id": welt["contract_sucher"]},
            {"$set": {"user_id": welt["sucher_id"],
                      "contract_data.digital_vertragstext": SUCHER_TEXT},
             "$unset": {"pdf_digital_b64": "", "pdf_digital_nachtraeglich": ""}})


def test_24_vorschau_kennt_beide_fassungen(welt):
    payload = {"vehicle_id": welt["vehicle_id"], "seller_name": "Vorschau V",
               "purchase_price": 100}
    r = requests.post(f"{API}/contracts/preview", headers=welt["H"], json=payload, timeout=60)
    assert r.status_code == 200 and "Mit ihrer Unterschrift" in _flach(_text(r.content))
    r = requests.post(f"{API}/contracts/preview", params={"variante": "digital"},
                      headers=welt["H"], json=payload, timeout=60)
    assert r.status_code == 200
    f = _flach(_text(r.content))
    assert "Mit ihrer Unterschrift" not in f and f"FIRMENTEXT-{SUF}" in f


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
    assert "05.04.2099" in neu and "Mit ihrer Unterschrift" not in neu
    # Runde 22: das Empfangsdatum stand auf dem alten Abholtag und wandert mit
    assert doc["contract_data"]["empfang_datum"] == "2099-04-05"
    assert "Datum und Ort: 05.04.2099, Hannover" in neu
    versionen = requests.get(f"{API}/contracts/{welt['contract_chef']}/versions",
                             headers=welt["H"], timeout=30).json()
    assert versionen and versionen[0]["version"] == 1
    assert "pdf_digital_b64" not in versionen[0] and "pdf_b64" not in versionen[0]
    r = requests.get(f"{API}/contracts/{welt['contract_chef']}/versions/1/pdf",
                     params={"variante": "digital"}, headers=welt["H"], timeout=60)
    assert r.status_code == 200
    alt = _flach(_text(r.content))
    assert "01.04.2099" in alt and "Mit ihrer Unterschrift" not in alt
    assert "Datum und Ort: 01.04.2099, Hannover" in alt
    r = requests.get(f"{API}/contracts/{welt['contract_chef']}/versions/1/pdf",
                     headers=welt["H"], timeout=60)
    assert "Mit ihrer Unterschrift" in _flach(_text(r.content))


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


def test_27_terminverschiebung_gibt_altvertrag_keine_heutigen_bedingungen(welt):
    """Beschluss Ahmad 09.09.2026: Einstellungs-Texte gelten NUR fuer neue
    Vertraege. Auch die Neuerzeugung bei Terminverschiebung darf einem
    Altvertrag ohne gespeicherten Text keinen heutigen Text unterschieben."""
    dbx = _db()
    r = requests.post(f"{API}/contracts", headers=welt["H"], json={
        "vehicle_id": welt["vehicle_id"], "seller_name": "Alt V",
        "purchase_price": 100, "pickup_date": "2099-06-01", "pickup_time": "10:00",
        # Runde 22: von Hand anders gesetztes Empfangsdatum bleibt stehen
        "empfang_datum": "2099-05-31"},
        timeout=90)
    assert r.status_code == 200, r.text[:300]
    cid = r.json()["id"]
    # Altvertrag simulieren: kein gespeicherter Text, keine digitale Fassung
    dbx.generated_pdfs.update_one(
        {"id": cid}, {"$unset": {"pdf_digital_b64": "", "contract_data.digital_vertragstext": ""}})
    # Chef hat HEUTE einen Firmentext gesetzt
    r = requests.put(f"{API}/dealer/settings", headers=welt["H"],
                     json={"digital_vertragstext": f"HEUTE-{SUF}: neuer Firmentext."}, timeout=30)
    assert r.status_code == 200
    try:
        appts = requests.get(f"{API}/appointments", headers=welt["H"], timeout=30).json()
        appt = next(a for a in appts if a.get("contract_id") == cid)
        r = requests.put(f"{API}/appointments/{appt['id']}", headers=welt["H"],
                         json={"pickup_date": "2099-06-03", "pickup_time": "11:00"}, timeout=90)
        assert r.status_code == 200, r.text[:300]
        doc = dbx.generated_pdfs.find_one({"id": cid})
        assert int(doc.get("version") or 1) == 2
        assert "digital_vertragstext" not in (doc.get("contract_data") or {}), \
            "der Vertragsinhalt bekommt keinen nachtraeglichen Text"
        assert doc.get("pdf_digital_nachtraeglich") is True
        assert doc["contract_data"]["empfang_datum"] == "2099-05-31"
        f = _flach(_text(base64.b64decode(doc["pdf_digital_b64"])))
        assert "nachträglich erzeugt" in f and "03.06.2099" in f
        assert f"HEUTE-{SUF}" not in f and "Absagen sind nach Vertragsbestätigung" not in f
    finally:
        requests.put(f"{API}/dealer/settings", headers=welt["H"],
                     json={"digital_vertragstext": ""}, timeout=30)


def test_28_scheitert_die_digitale_fassung_kommt_keine_druckfassung(welt):
    """Pruefbefund: Scheiterte die Nacherzeugung, lieferte der Code still die
    Druckfassung MIT Unterschriftslinien — obwohl 'digital' zugesagt war.
    Jetzt: klarer Fehler (503), kein stiller Ersatz."""
    dbx = _db()
    cid = welt["contract_chef"]
    sicherung = dbx.generated_pdfs.find_one({"id": cid}, {"contract_data": 1, "pdf_digital_b64": 1})
    # Erzeugung zum Scheitern bringen: contract_data kaputt, keine Fassung gespeichert
    dbx.generated_pdfs.update_one({"id": cid}, {"$set": {"contract_data": "kaputt"},
                                                "$unset": {"pdf_digital_b64": ""}})
    try:
        r = requests.get(f"{API}/contracts/{cid}/pdf", params={"variante": "digital"},
                         headers=welt["H"], timeout=60)
        assert r.status_code == 503, r.status_code
        assert "digitale Vertragsfassung" in r.json()["detail"]
        # Druckfassung selbst bleibt abrufbar
        r = requests.get(f"{API}/contracts/{cid}/pdf", headers=welt["H"], timeout=60)
        assert r.status_code == 200 and "Mit ihrer Unterschrift" in _flach(_text(r.content))
    finally:
        setzen = {"contract_data": sicherung.get("contract_data")}
        if sicherung.get("pdf_digital_b64"):
            setzen["pdf_digital_b64"] = sicherung["pdf_digital_b64"]
        dbx.generated_pdfs.update_one({"id": cid}, {"$set": setzen})
