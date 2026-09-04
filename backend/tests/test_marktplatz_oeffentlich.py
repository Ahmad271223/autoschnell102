# -*- coding: utf-8 -*-
"""Marktplatz kostenlos und oeffentlich (Wunsch 09/2026).

Geprueft wird vor allem die Sichtbarkeit, denn hier darf nichts durchrutschen:

  * OHNE Anmeldung sichtbar: oeffentlich veroeffentlichte Fahrzeuge
    oeffentlicher Haendler.
  * NIE ohne Netzwerk sichtbar: Inserate mit "Nur Netzwerk (privat)" und
    Haendler, die ihr Profil nicht oeffentlich gestellt haben — weder fuer
    Besucher ohne Anmeldung noch fuer angemeldete, aber nicht eingeladene
    Zwischenhaendler.
  * Kein Zugangs-Abo mehr: ein frisch registrierter Kaeufer sieht sofort
    alles Oeffentliche (frueher 402 "Kein aktiver Marktplatz-Zugang").

Braucht ein laufendes Backend (TEST_BASE_URL) und Mongo-Zugriff.
"""
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BASE = (os.environ.get("TEST_BASE_URL") or "http://localhost:8001").rstrip("/")
API = f"{BASE}/api"
MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
DB_NAME = os.environ.get("DB_NAME") or "autoschnell"
SUF = uuid.uuid4().hex[:8]
MAIL = "e2etest-mail.de"
PW = "MarktOeffentlich123!x"


def _db():
    from pymongo import MongoClient
    return MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)[DB_NAME]


def _kopf(token):
    return {"Authorization": f"Bearer {token}"}


def _haendler(nr: int, oeffentlich: bool):
    r = requests.post(f"{API}/auth/register", json={
        "email": f"mo_chef{nr}_{SUF}@{MAIL}", "password": PW,
        "company_name": f"Markt Autohaus {nr} {SUF}",
        "contact_person": f"Chef {nr}", "phone": "0511 1"}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    kopf = _kopf(r.json()["token"])
    dealer_id = r.json()["user"]["dealer_id"]
    r = requests.put(f"{API}/dealer/marketplace-profile", headers=kopf,
                     json={"public": oeffentlich, "description": f"Test {nr}"}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    return {"kopf": kopf, "dealer_id": dealer_id,
            "mail": f"mo_chef{nr}_{SUF}@{MAIL}"}


def _inserat(haendler, name: str, sichtbarkeit: str):
    dbx = _db()
    vid = str(uuid.uuid4())
    jetzt = datetime.now(timezone.utc).isoformat()
    dbx.vehicles.insert_one({
        "id": vid, "dealer_id": haendler["dealer_id"], "lifecycle": "bestand",
        "status": "Bestand", "purchase_price": 5000,
        "data": {"make_label": "VW", "model_label": name, "mileage": 90000,
                 "first_registration": "01/2020", "fuel_label": "Benzin",
                 "power_ps": 110, "images": []},
        "created_at": jetzt, "updated_at": jetzt})
    r = requests.post(f"{API}/resale/draft/{vid}", headers=haendler["kopf"], timeout=30)
    assert r.status_code == 200, r.text[:300]
    lid = r.json()["id"]
    assert requests.put(f"{API}/resale/{lid}", headers=haendler["kopf"],
                        json={"price_public": 9900, "price_b2b": 9000},
                        timeout=30).status_code == 200
    assert requests.post(f"{API}/resale/{lid}/status", headers=haendler["kopf"],
                         json={"status": "verkaufsbereit"}, timeout=30).status_code == 200
    r = requests.post(f"{API}/resale/{lid}/publish", headers=haendler["kopf"],
                      json={"visibility": sichtbarkeit}, timeout=30)
    assert r.status_code == 200, r.text[:300]
    return lid


@pytest.fixture(scope="module")
def welt():
    dbx = _db()
    daten = {}
    # Haendler 1: oeffentliches Profil, ein oeffentliches und ein privates Inserat
    h1 = _haendler(1, True)
    daten["h1"] = h1
    daten["oeffentlich"] = _inserat(h1, f"Golf offen {SUF}", "public")
    daten["privat"] = _inserat(h1, f"Golf privat {SUF}", "private")
    # Haendler 2: NICHT oeffentlich, ein oeffentlich markiertes Inserat
    h2 = _haendler(2, False)
    daten["h2"] = h2
    daten["h2_inserat"] = _inserat(h2, f"Golf geheim {SUF}", "public")
    # Kaeufer OHNE Netzwerk und ohne jedes Zugangs-Abo
    r = requests.post(f"{API}/buyer/register", json={
        "gewerblich_bestaetigt": True, "company_name": f"MO Kaeufer {SUF}",
        "contact_name": "K M", "email": f"mo_kaeufer_{SUF}@{MAIL}",
        "password": PW, "phone": "0511 2"}, timeout=30)
    assert r.status_code == 200, r.text[:300]
    daten["kaeufer"] = _kopf(r.json()["token"])
    daten["kaeufer_id"] = requests.get(f"{API}/buyer/me", headers=daten["kaeufer"],
                                       timeout=30).json()["id"]
    # ausdruecklich KEIN marketplace_access setzen
    yield daten
    for mail in (h1["mail"], h2["mail"], f"mo_kaeufer_{SUF}@{MAIL}"):
        dbx.users.delete_many({"email": mail})
    for d in (h1["dealer_id"], h2["dealer_id"]):
        dbx.dealers.delete_many({"id": d})
        dbx.resale_listings.delete_many({"dealer_id": d})
        dbx.vehicles.delete_many({"dealer_id": d})
        dbx.network_members.delete_many({"dealer_id": d})
    dbx.buyer_favorites.delete_many({"buyer_user_id": daten["kaeufer_id"]})


def _ids(antwort):
    d = antwort.json()
    eintraege = d if isinstance(d, list) else (d.get("listings") or d.get("items") or [])
    return {e.get("id") for e in eintraege}


# ------------------------------------------------- ohne Anmeldung
def test_01_oeffentliche_fahrzeuge_ohne_anmeldung_sichtbar(welt):
    r = requests.get(f"{API}/marktplatz/listings", timeout=30)
    assert r.status_code == 200, r.text[:200]
    ids = _ids(r)
    assert welt["oeffentlich"] in ids, "oeffentliches Inserat fehlt"


def test_02_private_inserate_bleiben_ohne_anmeldung_verborgen(welt):
    ids = _ids(requests.get(f"{API}/marktplatz/listings", timeout=30))
    assert welt["privat"] not in ids, "PRIVATES Inserat oeffentlich sichtbar"
    assert welt["h2_inserat"] not in ids, "Inserat eines NICHT oeffentlichen Haendlers sichtbar"


def test_03_haendlerliste_ohne_anmeldung(welt):
    r = requests.get(f"{API}/marktplatz/haendler", timeout=30)
    assert r.status_code == 200, r.text[:200]
    ids = {d.get("dealer_id") for d in r.json()}
    assert welt["h1"]["dealer_id"] in ids
    assert welt["h2"]["dealer_id"] not in ids, "nicht oeffentlicher Haendler gelistet"


def test_04_haendlerseite_ohne_anmeldung(welt):
    r = requests.get(f"{API}/marktplatz/haendler/{welt['h1']['dealer_id']}", timeout=30)
    assert r.status_code == 200, r.text[:200]
    ids = {l.get("id") for l in (r.json().get("listings") or [])}
    assert welt["oeffentlich"] in ids
    assert welt["privat"] not in ids, "privates Inserat auf der Haendlerseite sichtbar"
    # Nicht oeffentlicher Haendler bleibt gesperrt
    r = requests.get(f"{API}/marktplatz/haendler/{welt['h2']['dealer_id']}", timeout=30)
    assert r.status_code == 403, r.text[:200]


def test_05_merken_und_anfragen_brauchen_weiterhin_anmeldung(welt):
    r = requests.post(f"{API}/marktplatz/favoriten/{welt['oeffentlich']}", timeout=30)
    assert r.status_code in (401, 403), r.text[:200]
    r = requests.post(f"{API}/marktplatz/listings/{welt['oeffentlich']}/interesse",
                      json={"message": "Test"}, timeout=30)
    assert r.status_code in (401, 403), r.text[:200]


def test_06_kaputtes_token_gilt_als_nicht_angemeldet(welt):
    r = requests.get(f"{API}/marktplatz/listings",
                     headers={"Authorization": "Bearer unsinn"}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    assert welt["privat"] not in _ids(r)


# ------------------------------------------- angemeldet, ohne Netzwerk
def test_07_kaeufer_ohne_zugangsabo_sieht_sofort_alles_oeffentliche(welt):
    """Frueher: 402 "Kein aktiver Marktplatz-Zugang". Jetzt kostenlos."""
    r = requests.get(f"{API}/marktplatz/listings", headers=welt["kaeufer"], timeout=30)
    assert r.status_code == 200, r.text[:200]
    assert welt["oeffentlich"] in _ids(r)
    z = requests.get(f"{API}/marktplatz/zugang", headers=welt["kaeufer"], timeout=30)
    assert z.status_code == 200 and z.json().get("active") is True, z.text[:200]


def test_08_kaeufer_ohne_netzwerk_sieht_nichts_privates(welt):
    ids = _ids(requests.get(f"{API}/marktplatz/listings", headers=welt["kaeufer"], timeout=30))
    assert welt["privat"] not in ids
    assert welt["h2_inserat"] not in ids
    r = requests.get(f"{API}/marktplatz/haendler/{welt['h2']['dealer_id']}",
                     headers=welt["kaeufer"], timeout=30)
    assert r.status_code == 403


def test_09_privates_inserat_nicht_ueber_die_id_erreichbar(welt):
    """Auch mit bekannter ID darf ohne Netzwerk nichts gehen."""
    r = requests.post(f"{API}/marktplatz/favoriten/{welt['privat']}",
                      headers=welt["kaeufer"], timeout=30)
    assert r.status_code in (403, 404), r.text[:200]
    r = requests.post(f"{API}/marktplatz/listings/{welt['privat']}/interesse",
                      headers=welt["kaeufer"], json={"message": "Test"}, timeout=30)
    assert r.status_code in (403, 404), r.text[:200]


# ------------------------------------------- angemeldet, MIT Netzwerk
def test_10_nach_einladung_sieht_der_kaeufer_die_privaten(welt):
    r = requests.post(f"{API}/dealer/invites", headers=welt["h1"]["kopf"],
                      json={"note": "Test"}, timeout=30)
    assert r.status_code == 200, r.text[:300]
    token = r.json().get("token") or r.json().get("invite", {}).get("token")
    assert token, r.text[:200]
    r = requests.post(f"{API}/invites/{token}/redeem", headers=welt["kaeufer"], timeout=30)
    assert r.status_code == 200, r.text[:300]
    ids = _ids(requests.get(f"{API}/marktplatz/listings", headers=welt["kaeufer"], timeout=30))
    assert welt["privat"] in ids, "eingeladener Kaeufer sieht das private Inserat nicht"
    assert welt["oeffentlich"] in ids
    # ... und ein Besucher OHNE Anmeldung weiterhin nicht
    assert welt["privat"] not in _ids(requests.get(f"{API}/marktplatz/listings", timeout=30))


def test_11_einladung_gilt_nur_fuer_diesen_haendler(welt):
    """Netzwerk bei Haendler 1 oeffnet NICHT den Bestand von Haendler 2."""
    ids = _ids(requests.get(f"{API}/marktplatz/listings", headers=welt["kaeufer"], timeout=30))
    assert welt["h2_inserat"] not in ids
    r = requests.get(f"{API}/marktplatz/haendler/{welt['h2']['dealer_id']}",
                     headers=welt["kaeufer"], timeout=30)
    assert r.status_code == 403
