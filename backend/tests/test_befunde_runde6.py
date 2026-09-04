# -*- coding: utf-8 -*-
"""Pruefbericht Runde 6 (09/2026) — Regressionstests zu den gemeldeten Befunden.

  1  Termin-Teilaenderung darf keine Daten loeschen (PUT mit nur einem Feld)
  6  Fahrer-Annahme/Ablehnung ist race-sicher (nur die erste Antwort zaehlt)
  7  Wesentliche Terminaenderung nach Zusage verlangt neue Bestaetigung
  8  Kaeufer nimmt Gegenangebot an -> verbindlicher Preis (agreed_price)
  2  Stripe-Freischaltung verlaengert je Zahlung genau einmal
  3  Super-Admin kann die eigene Zwei-Faktor-Anmeldung nicht zuruecksetzen
 12  Laufzeitaenderung ohne Zahlung braucht eine Begruendung

Braucht ein laufendes Backend (TEST_BASE_URL, Standard :8001) und Zugriff
auf dieselbe MongoDB. Keine echten Anbieter- oder Stripe-Anfragen.
"""
import asyncio
import os
import sys
import uuid
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BASE_URL = (os.environ.get("TEST_BASE_URL") or "http://localhost:8001").rstrip("/")
API = f"{BASE_URL}/api"
MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
DB_NAME = os.environ.get("DB_NAME") or "autoschnell"
SUF = uuid.uuid4().hex[:8]
MAIL = "e2etest-mail.de"
PW = "RundeSechs123!x"


def _db():
    from pymongo import MongoClient
    return MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)[DB_NAME]


def _hdr(t):
    return {"Authorization": f"Bearer {t}"}


@pytest.fixture(scope="module")
def welt():
    """Firma + Fahrer + Termin, am Ende alles wieder weg."""
    dbx = _db()
    firma_mail = f"r6_firma_{SUF}@{MAIL}"
    r = requests.post(f"{API}/auth/register", json={
        "email": firma_mail, "password": PW, "company_name": f"Runde6 GmbH {SUF}",
        "contact_person": "Rita Runde", "phone": "+4915100000"}, timeout=30)
    assert r.status_code == 200, r.text[:300]
    firma = r.json()
    fahrer_mail = f"r6_fahrer_{SUF}@{MAIL}"
    r = requests.post(f"{API}/driver/register", json={
        "email": fahrer_mail, "password": PW, "display_name": "Frank Fahrer",
        "phone": "+4915200000"}, timeout=30)
    assert r.status_code == 200, r.text[:300]
    fahrer = r.json()
    code = fahrer.get("driver_code") or (fahrer.get("driver") or {}).get("driver_code")
    r = requests.post(f"{API}/drivers/add", json={"driver_code": code},
                      headers=_hdr(firma["token"]), timeout=30)
    assert r.status_code == 200, r.text[:300]
    fahrer_id = (r.json().get("driver") or r.json()).get("id") or fahrer["driver"]["id"]
    daten = {"firma_token": firma["token"], "firma_mail": firma_mail,
             "fahrer_token": fahrer["token"], "fahrer_mail": fahrer_mail,
             "fahrer_id": fahrer_id, "dealer_id": firma["user"].get("dealer_id")}
    yield daten
    dbx.users.delete_many({"email": {"$in": [firma_mail]}})
    dbx.driver_accounts.delete_many({"email": fahrer_mail})
    dbx.appointments.delete_many({"dealer_id": daten["dealer_id"]})


def _neu_anmelden(mail):
    r = requests.post(f"{API}/auth/login", json={"email": mail, "password": PW}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    return r.json()["token"]


def _fahrer_anmelden(mail):
    r = requests.post(f"{API}/driver/login", json={"email": mail, "password": PW}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    return r.json()["token"]


def _termin_anlegen(token, fahrer_id=None):
    body = {"title": "Abholung Golf", "seller_name": "Sabine Verkauf",
            "seller_phone": "+4917600000", "seller_email": "sabine@example.com",
            "pickup_address": "Musterweg 5, 30159 Hannover",
            "pickup_date": "2026-10-01", "pickup_time": "09:30",
            "notes": "Papiere liegen beim Nachbarn", "status": "bestätigt"}
    if fahrer_id:
        body["driver_id"] = fahrer_id
    r = requests.post(f"{API}/appointments", json=body, headers=_hdr(token), timeout=30)
    assert r.status_code == 200, r.text[:300]
    return r.json()["id"] if "id" in r.json() else r.json()["appointment"]["id"]


def _termin(token, appt_id):
    r = requests.get(f"{API}/appointments/{appt_id}", headers=_hdr(token), timeout=30)
    assert r.status_code == 200, r.text[:200]
    return r.json()


# --------------------------------------------------------------- Befund 1
def test_01_teilaenderung_loescht_keine_daten(welt):
    t = _neu_anmelden(welt["firma_mail"])
    appt_id = _termin_anlegen(t)
    vorher = _termin(t, appt_id)
    # Nur den Fahrer zuweisen — alles andere darf sich NICHT aendern.
    r = requests.put(f"{API}/appointments/{appt_id}",
                     json={"driver_id": welt["fahrer_id"]}, headers=_hdr(t), timeout=30)
    assert r.status_code == 200, r.text[:300]
    nachher = _termin(t, appt_id)
    for feld in ("seller_name", "seller_phone", "seller_email", "pickup_address",
                 "pickup_date", "pickup_time", "notes", "title"):
        assert nachher.get(feld) == vorher.get(feld), f"{feld} wurde ueberschrieben"
    assert nachher.get("status") == vorher.get("status") == "bestätigt"
    assert nachher.get("driver_id") == welt["fahrer_id"]
    assert nachher.get("zuteilung") == "offen"          # neuer Fahrer -> anfragen
    welt["appt_zugewiesen"] = appt_id


# --------------------------------------------------------------- Befund 6
def test_02_fahrer_antwort_nur_einmal(welt):
    appt_id = welt["appt_zugewiesen"]
    ft = _fahrer_anmelden(welt["fahrer_mail"])
    r1 = requests.put(f"{API}/driver/appointments/{appt_id}/zuteilung",
                      json={"action": "annehmen"}, headers=_hdr(ft), timeout=30)
    assert r1.status_code == 200 and r1.json()["zuteilung"] == "angenommen", r1.text[:200]
    # Zweite (spaete) Antwort darf den Zustand nicht mehr umdrehen
    r2 = requests.put(f"{API}/driver/appointments/{appt_id}/zuteilung",
                      json={"action": "ablehnen", "grund": "doch keine Zeit"},
                      headers=_hdr(ft), timeout=30)
    assert r2.status_code == 200, r2.text[:200]
    assert r2.json().get("unveraendert") is True
    assert r2.json()["zuteilung"] == "angenommen"
    t = _neu_anmelden(welt["firma_mail"])
    a = _termin(t, appt_id)
    assert a["zuteilung"] == "angenommen"
    assert a.get("driver_id") == welt["fahrer_id"], "Fahrer darf nicht entfernt sein"


# --------------------------------------------------------------- Befund 7
def test_03_aenderung_nach_zusage_verlangt_neue_bestaetigung(welt):
    appt_id = welt["appt_zugewiesen"]
    t = _neu_anmelden(welt["firma_mail"])
    assert _termin(t, appt_id)["zuteilung"] == "angenommen"
    r = requests.put(f"{API}/appointments/{appt_id}",
                     json={"pickup_time": "17:45"}, headers=_hdr(t), timeout=30)
    assert r.status_code == 200, r.text[:300]
    a = _termin(t, appt_id)
    assert a["pickup_time"] == "17:45"
    assert a["zuteilung"] == "offen", "geaenderte Fahrt muss neu bestaetigt werden"
    assert a.get("zuteilung_neu_wegen_aenderung") is True
    # Unwesentliche Aenderung (Notiz) hebt eine Zusage NICHT auf
    ft = _fahrer_anmelden(welt["fahrer_mail"])
    requests.put(f"{API}/driver/appointments/{appt_id}/zuteilung",
                 json={"action": "annehmen"}, headers=_hdr(ft), timeout=30)
    t = _neu_anmelden(welt["firma_mail"])
    requests.put(f"{API}/appointments/{appt_id}", json={"notes": "Schlüssel beim Nachbarn"},
                 headers=_hdr(t), timeout=30)
    assert _termin(t, appt_id)["zuteilung"] == "angenommen"


# --------------------------------------------------------------- Befund 3
def test_04_super_admin_kann_eigene_mfa_nicht_zuruecksetzen():
    import bcrypt
    dbx = _db()
    mail = f"r6_super_{SUF}@{MAIL}"
    dbx.users.delete_many({"email": mail})
    uid = f"r6_super_{uuid.uuid4().hex[:8]}"
    dbx.users.insert_one({
        "id": uid, "email": mail, "role": "admin", "active": True, "dealer_id": None,
        "is_super_admin": True,
        "password_hash": bcrypt.hashpw(PW.encode(), bcrypt.gensalt()).decode(),
        "created_at": "2026-01-01T00:00:00+00:00"})
    try:
        tok = _neu_anmelden(mail)
        r = requests.post(f"{API}/admin/users/{uid}/mfa-zuruecksetzen",
                          json={}, headers=_hdr(tok), timeout=30)
        assert r.status_code == 400, r.text[:300]
        assert "eigene" in r.text.lower()
        # Fremdes Super-Admin-Konto: ohne Passwort/Grund abgelehnt
        mail2 = f"r6_super2_{SUF}@{MAIL}"
        uid2 = f"r6_super2_{uuid.uuid4().hex[:8]}"
        dbx.users.delete_many({"email": mail2})
        dbx.users.insert_one({
            "id": uid2, "email": mail2, "role": "admin", "active": True,
            "dealer_id": None, "is_super_admin": True,
            "password_hash": bcrypt.hashpw(PW.encode(), bcrypt.gensalt()).decode(),
            "mfa": {"aktiv": True, "secret": "x", "letzter_zaehler": -1},
            "created_at": "2026-01-01T00:00:00+00:00"})
        r = requests.post(f"{API}/admin/users/{uid2}/mfa-zuruecksetzen",
                          json={}, headers=_hdr(tok), timeout=30)
        assert r.status_code == 401, r.text[:200]
        r = requests.post(f"{API}/admin/users/{uid2}/mfa-zuruecksetzen",
                          json={"passwort": PW}, headers=_hdr(tok), timeout=30)
        assert r.status_code == 400 and "Grund" in r.text, r.text[:200]
        r = requests.post(f"{API}/admin/users/{uid2}/mfa-zuruecksetzen",
                          json={"passwort": PW, "grund": "Handy verloren"},
                          headers=_hdr(tok), timeout=30)
        assert r.status_code == 200, r.text[:300]
        assert not (dbx.users.find_one({"id": uid2}) or {}).get("mfa")
        eintrag = dbx.zugangs_aenderungen.find_one({"subject_user_id": uid2,
                                                    "art": "mfa_zurueckgesetzt"})
        assert eintrag and eintrag["grund"] == "Handy verloren"
        assert dbx.betriebsalarme.find_one({"typ": "mfa_zurueckgesetzt", "ref": mail2})
    finally:
        dbx.users.delete_many({"email": {"$in": [mail, f"r6_super2_{SUF}@{MAIL}"]}})
        dbx.zugangs_aenderungen.delete_many({"admin_id": uid})
        dbx.betriebsalarme.delete_many({"typ": "mfa_zurueckgesetzt",
                                        "ref": f"r6_super2_{SUF}@{MAIL}"})


# --------------------------------------------------------------- Befund 2
def test_05_stripe_freischaltung_verlaengert_nur_einmal():
    """_zugang_freischalten zweimal mit derselben Session -> gleiches Datum.

    Laeuft direkt gegen die Funktion (kein echter Stripe-Aufruf); die
    Datenbank-Bruecke aus deps.py erzeugt fuer diese Schleife eine eigene
    Verbindung, deshalb wird deps NICHT ersetzt."""
    dbx = _db()
    sid = f"cs_test_r6_{uuid.uuid4().hex[:12]}"
    uid = f"r6_kaeufer_{uuid.uuid4().hex[:8]}"
    dbx.users.insert_one({"id": uid, "email": f"{uid}@{MAIL}", "role": "b2b_buyer",
                          "active": True, "created_at": "2026-01-01T00:00:00+00:00"})
    tx = {"user_id": uid, "dealer_id": None, "plan": "marktplatz",
          "amount": 20.0, "currency": "eur"}

    async def lauf():
        import routes.payments as pay
        from deps import db as db_async
        eins = await pay._zugang_freischalten(tx, sid)
        zwei = await pay._zugang_freischalten(tx, sid)          # Wiederholung
        gespeichert = (await db_async.users.find_one(
            {"id": uid}, {"_id": 0, "marketplace_access": 1})
        )["marketplace_access"]["expires_at"]
        # Eine ANDERE Zahlung muss dagegen weiter verlaengern
        drei = await pay._zugang_freischalten(tx, sid + "_zweite")
        return eins, zwei, gespeichert, drei
    try:
        eins, zwei, gespeichert, drei = asyncio.run(lauf())
        assert eins == zwei == gespeichert, "Wiederholung darf NICHT erneut verlaengern"
        assert drei > eins, "eine zweite Zahlung muss weiter verlaengern"
        assert dbx.zugang_grants.count_documents({"session_id": sid}) == 1
    finally:
        dbx.users.delete_many({"id": uid})
        dbx.zugang_grants.delete_many({"session_id": {"$regex": f"^{sid}"}})


def test_06_stripe_freischaltung_ohne_konto_meldet_fehler():
    """Kein Konto zur Zahlung -> klarer Fehler (statt stiller Nicht-Freischaltung)."""
    async def lauf():
        import routes.payments as pay
        with pytest.raises(RuntimeError) as e:
            await pay._zugang_freischalten(
                {"user_id": f"gibt-es-nicht-{uuid.uuid4().hex[:8]}", "plan": "marktplatz"},
                f"cs_test_r6_{uuid.uuid4().hex[:12]}")
        return str(e.value)
    assert "Konto" in asyncio.run(lauf())


# -------------------------------------------------------------- Befund 12
def test_07_laufzeitaenderung_braucht_grund(welt):
    import bcrypt
    dbx = _db()
    mail = f"r6_sa_{SUF}@{MAIL}"
    dbx.users.delete_many({"email": mail})
    uid = f"r6_sa_{uuid.uuid4().hex[:8]}"
    dbx.users.insert_one({
        "id": uid, "email": mail, "role": "admin", "active": True, "dealer_id": None,
        "is_super_admin": True,
        "password_hash": bcrypt.hashpw(PW.encode(), bcrypt.gensalt()).decode(),
        "created_at": "2026-01-01T00:00:00+00:00"})
    sucher_id = f"r6_sucher_{uuid.uuid4().hex[:8]}"
    dbx.users.insert_one({
        "id": sucher_id, "email": f"{sucher_id}@{MAIL}", "role": "user",
        "active": True, "dealer_id": welt["dealer_id"], "sucher_funktion": True,
        "password_hash": bcrypt.hashpw(PW.encode(), bcrypt.gensalt()).decode(),
        "created_at": "2026-01-01T00:00:00+00:00"})
    dbx.subscriptions.insert_one({
        "id": f"r6_abo_{uuid.uuid4().hex[:8]}", "subject_user_id": sucher_id,
        "dealer_id": welt["dealer_id"], "plan": "monthly", "status": "active",
        "expires_at": "2026-12-01T00:00:00+00:00", "created_at": "2026-09-01T00:00:00+00:00"})
    try:
        tok = _neu_anmelden(mail)
        r = requests.patch(f"{API}/admin/sucher/{sucher_id}/abo-gueltig-bis",
                           json={"gueltig_bis": "2027-01-31"}, headers=_hdr(tok), timeout=30)
        assert r.status_code == 400 and "Grund" in r.text, r.text[:200]
        r = requests.patch(f"{API}/admin/sucher/{sucher_id}/abo-gueltig-bis",
                           json={"gueltig_bis": "2027-01-31", "grund": "Kulanz nach Ausfall"},
                           headers=_hdr(tok), timeout=30)
        assert r.status_code == 200, r.text[:300]
        eintrag = dbx.zugangs_aenderungen.find_one({"subject_user_id": sucher_id})
        assert eintrag["grund"] == "Kulanz nach Ausfall"
        assert eintrag["alt"].startswith("2026-12-01") and eintrag["neu"].startswith("2027-01-31")
    finally:
        dbx.users.delete_many({"email": mail})
        dbx.users.delete_many({"id": sucher_id})
        dbx.subscriptions.delete_many({"subject_user_id": sucher_id})
        dbx.zugangs_aenderungen.delete_many({"subject_user_id": sucher_id})
