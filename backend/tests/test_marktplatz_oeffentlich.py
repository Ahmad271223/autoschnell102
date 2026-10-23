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

import konten  # noqa: E402  Kontonummer (13.09.2026): zentrale Konto-Helfer
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BASE = (os.environ.get("TEST_BASE_URL") or "http://localhost:8001").rstrip("/")
API = f"{BASE}/api"
MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
DB_NAME = os.environ.get("DB_NAME") or "autoschnell"
SUF = uuid.uuid4().hex[:8]
MAIL = "e2etest-mail.de"
PW = "Kq4Lm9Xw2-Sicher!x"   # Runde 14: kein Firmenname ("Markt") im Passwort


def _db():
    from pymongo import MongoClient
    return MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)[DB_NAME]


def _kopf(token):
    return {"Authorization": f"Bearer {token}"}


def _haendler(nr: int, oeffentlich: bool):
    r = konten.registrieren(json={
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
    # Regel 20.09.2026: veroeffentlichen nur mit eigenen Fotos.
    konten.eigene_fotos_hinterlegen(_db(), lid)
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
    r = konten.kaeufer_registrieren(json={
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
    dbx.listing_interest.delete_many({"buyer_user_id": daten["kaeufer_id"]})


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
    # Nicht oeffentlicher Haendler bleibt gesperrt — Rollenprüfung 22.09.2026
    # (RP-098 Nr. 11): 404 wie "unbekannt", damit sich nicht pruefen laesst,
    # ob es die Firma gibt (vorher 403 "privat").
    r = requests.get(f"{API}/marktplatz/haendler/{welt['h2']['dealer_id']}", timeout=30)
    assert r.status_code == 404, r.text[:200]


def test_05_merken_und_anfragen_brauchen_weiterhin_anmeldung(welt):
    r = requests.post(f"{API}/marktplatz/favoriten/{welt['oeffentlich']}", timeout=30)
    assert r.status_code in (401, 403), r.text[:200]
    r = requests.post(f"{API}/marktplatz/listings/{welt['oeffentlich']}/interesse",
                      json={"message": "Test"}, timeout=30)
    assert r.status_code in (401, 403), r.text[:200]


def test_06_kaputtes_token_wird_gemeldet_ohne_token_oeffentlich(welt):
    """Rollenprüfung 22.09.2026 (RP-530): Ein mitgeschicktes, aber ungueltiges
    Token (Sitzung verdraengt/abgemeldet) galt vorher still als "nicht
    angemeldet" — die Liste lud ohne Netzwerk-Inserate, der Kaeufer merkte
    nichts. Jetzt 401 (die Kaeufer-App meldet ab und nennt den Grund); ohne
    Token bleibt der Marktplatz oeffentlich."""
    r = requests.get(f"{API}/marktplatz/listings",
                     headers={"Authorization": "Bearer unsinn"}, timeout=30)
    assert r.status_code == 401, r.text[:200]
    r = requests.get(f"{API}/marktplatz/listings", timeout=30)
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
    assert r.status_code == 404     # RP-098 Nr. 11: privat wie unbekannt


def test_09_privates_inserat_nicht_ueber_die_id_erreichbar(welt):
    """Auch mit bekannter ID darf ohne Netzwerk nichts gehen.

    Pruefbericht 20.09.2026 (T-09): vorher 'in (403, 404)' — eine entfernte
    Route (404) haette den Berechtigungstest bestanden. Deshalb zuerst die
    Positivprobe am OEFFENTLICHEN Inserat (200), dann am privaten genau die
    404 'Inserat nicht gefunden' (kein Hinweis, dass es das Inserat gibt)."""
    # Positivprobe: dieselben Routen funktionieren fuer das oeffentliche Inserat
    r = requests.post(f"{API}/marktplatz/favoriten/{welt['oeffentlich']}",
                      headers=welt["kaeufer"], timeout=30)
    assert r.status_code == 200 and r.json().get("favorit") is True, r.text[:200]
    r = requests.post(f"{API}/marktplatz/favoriten/{welt['oeffentlich']}?aktiv=false",
                      headers=welt["kaeufer"], timeout=30)
    assert r.status_code == 200 and r.json().get("favorit") is False, r.text[:200]
    r = requests.post(f"{API}/marktplatz/listings/{welt['oeffentlich']}/interesse",
                      headers=welt["kaeufer"], json={"message": "Test"}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    # Privates Inserat ohne Netzwerk: genau 404, nichts gemerkt, nichts angefragt
    r = requests.post(f"{API}/marktplatz/favoriten/{welt['privat']}",
                      headers=welt["kaeufer"], timeout=30)
    assert r.status_code == 404, r.text[:200]
    r = requests.post(f"{API}/marktplatz/listings/{welt['privat']}/interesse",
                      headers=welt["kaeufer"], json={"message": "Test"}, timeout=30)
    assert r.status_code == 404, r.text[:200]
    dbx = _db()
    assert dbx.buyer_favorites.count_documents(
        {"buyer_user_id": welt["kaeufer_id"], "listing_id": welt["privat"]}) == 0
    assert dbx.listing_interest.count_documents(
        {"buyer_user_id": welt["kaeufer_id"], "listing_id": welt["privat"]}) == 0


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
    assert r.status_code == 404     # RP-098 Nr. 11: privat wie unbekannt

def test_13_werbetexte_nennen_keinen_festen_preis():
    """Regressionsschutz im Frontend: der Preis darf nicht wieder fest im
    Text stehen, sonst wirbt die Seite beim naechsten Umschalten falsch."""
    basis = Path(__file__).resolve().parents[2] / "frontend" / "src"
    # Kontonummer (13.09.2026): BuyerRegister.jsx ist entfallen — Zwischen-
    # haendler fragen ueber Anfrage.jsx (?art=kaeufer) an.
    for datei in ("pages/Landing.jsx", "pages/Anfrage.jsx"):
        quelle = (basis / datei).read_text(encoding="utf-8")
        # 14.09.2026: kein Zahlungsdienst mehr (Stripe entfernt) — nur noch die
        # Regel "kein fester Preis im Werbetext" bleibt.
        assert "20 €" not in quelle, f"{datei} nennt weiter einen festen Preis"


def test_14_anfrage_bestaetigung_fuer_alle_arten():
    """Kontonummer (13.09.2026, Gegenpruefung): AGB §1 stuetzt sich auf die
    Pflicht-Checkbox der Zugangs-Anfrage — sie muss fuer Firma, Kaeufer und
    Fahrer gelten und immer mitgesendet werden, nicht nur bei art=kaeufer."""
    basis = Path(__file__).resolve().parents[2] / "frontend" / "src"
    quelle = (basis / "pages" / "Anfrage.jsx").read_text(encoding="utf-8")
    assert "if (!gewerblich) {" in quelle
    assert 'art === "kaeufer" && !gewerblich' not in quelle
    assert "gewerblich_bestaetigt: gewerblich," in quelle
    # Checkbox steht ausserhalb des Kaeufer-Blocks (nach dessen Ende)
    kaeufer_ende = quelle.index('data-testid="anfrage-ust-id"')
    assert quelle.index('data-testid="anfrage-b2b"') > quelle.index(")}", kaeufer_ende)
