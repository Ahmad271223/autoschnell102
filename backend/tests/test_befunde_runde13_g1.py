# -*- coding: utf-8 -*-
"""Runde 13 (07.09.2026), Gruppe 1 — Marktplatz, Zahlung, Sperren, Namensraum.

  A2  Marktplatz-Zugang bei fehlendem/unlesbarem Ablaufdatum fail-closed
  A3  /payments/status: nur Eigentuemer oder Super-Admin
  A4  Betreiber-Abruf des Zahlungsstatus loest KEINE Freischaltung aus
  A5  Stripe-Freischaltung verlangt weiterhin die Rolle b2b_buyer
  A6  Stripe-Freischaltung schreibt nicht auf ein gesperrtes Konto
  A7  Haendleruebersicht zaehlt private Inserate nur fuer Netzwerkmitglieder
  A8  gesperrte Firma verschwindet aus dem oeffentlichen Marktplatz
  A9  Einladungen einer gesperrten Firma sind nicht einloesbar
  A10 Netzwerk-Widerruf beendet auch laufende Verhandlungen
  B1  Abholfoto nur ueber einen Abholbericht der eigenen Firma
  B3  Admin-Vergleichsansicht mischt keine Firmenkorrekturen
  B5  Login-E-Mail plattformweit eindeutig (users + driver_accounts)
  B6  Passwort-Reset bedient beide Kontotypen
  B8  Beweis-Snapshots werden bei Loeschung pseudonymisiert
  C1  Verhandlungsantwort braucht aktiven Marktplatz-Zugang
  C2  Favorit setzen braucht aktiven Marktplatz-Zugang
  C4  Merkliste zeigt nur noch sichtbare Inserate; Widerruf raeumt auf
  C6  Betreiber-Sperre gilt auch im Kostenlos-Modus

HTTP-Teile brauchen das Backend auf TEST_BASE_URL mit SELF_SIGNUP=true.
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

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "autoschnell")

BASE = (os.environ.get("TEST_BASE_URL") or "http://localhost:8001").rstrip("/")
API = f"{BASE}/api"
MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]
SUF = uuid.uuid4().hex[:8]
PW = "RundeDreizehn13!"
MAIL = "e2etest-mail.de"


def _db():
    from pymongo import MongoClient
    return MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)[DB_NAME]


def _kopf(token):
    return {"Authorization": f"Bearer {token}"}


def _login(mail):
    r = requests.post(f"{API}/auth/login", json={"email": mail, "password": PW}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    return _kopf(r.json()["token"])


def _buyer_login(mail):
    r = requests.post(f"{API}/buyer/login", json={"email": mail, "password": PW}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    return _kopf(r.json()["token"])


def _ids(antwort):
    d = antwort.json()
    eintraege = d if isinstance(d, list) else (d.get("listings") or d.get("items") or [])
    return {e.get("id") for e in eintraege}


def _jetzt():
    return datetime.now(timezone.utc).isoformat()


# =====================================================================
#                          Einheitentests
# =====================================================================
def _buyer(acc):
    return {"id": "u1", "role": "b2b_buyer", "active": True, "marketplace_access": acc}


def test_a2_c6_zugangsstatus_fail_closed_und_sperre(monkeypatch):
    import routes.marketplace as m
    monkeypatch.setattr(m, "MARKTPLATZ_KOSTENLOS", False)
    zukunft = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    assert m._access_status(_buyer({"active": True, "expires_at": zukunft}))["active"] is True
    for kaputt in ("kaputt", 12345, None, "", datetime(2020, 1, 1)):
        st = m._access_status(_buyer({"active": True, "expires_at": kaputt}))
        assert st["active"] is False, f"expires_at={kaputt!r} darf keinen Zugang geben"
    assert m._access_status(_buyer({"active": True, "expires_at": "2020-01-01T00:00:00+00:00"}))["active"] is False
    assert m._access_status(_buyer(None))["active"] is False
    # Sperre gewinnt immer — auch im Kostenlos-Modus; Altbestand active=False gilt als Sperre
    for kostenlos in (False, True):
        monkeypatch.setattr(m, "MARKTPLATZ_KOSTENLOS", kostenlos)
        st = m._access_status(_buyer({"active": True, "expires_at": zukunft, "gesperrt": True}))
        assert st["active"] is False and st["gesperrt"] is True, (kostenlos, st)
        st = m._access_status(_buyer({"active": False, "expires_at": zukunft}))
        assert st["active"] is False and st["gesperrt"] is True, (kostenlos, st)
    monkeypatch.setattr(m, "MARKTPLATZ_KOSTENLOS", True)
    assert m._access_status(_buyer(None)) == {**m._access_status(_buyer(None)), "active": True, "gesperrt": False}


def test_c1_c2_zugang_erzwingen_und_abhaengigkeiten(monkeypatch):
    import routes.marketplace as m
    from fastapi import HTTPException
    monkeypatch.setattr(m, "MARKTPLATZ_KOSTENLOS", False)
    with pytest.raises(HTTPException) as e:
        m._zugang_erzwingen(_buyer({"active": True, "expires_at": "2020-01-01T00:00:00+00:00"}))
    assert e.value.status_code == 402
    with pytest.raises(HTTPException) as e:
        m._zugang_erzwingen(_buyer({"active": True, "gesperrt": True}))
    assert e.value.status_code == 403
    # C1: Kaeufer-Antwort haengt am Zugang; C2: Favorit setzen prueft den Zugang
    sig = inspect.signature(m.buyer_answer_interest)
    assert "require_marketplace_access" in str(sig.parameters["user"].default.dependency.__name__)
    assert "_zugang_erzwingen(user)" in inspect.getsource(m.toggle_favorit)


def test_a3_a4_zahlungsstatus_quelle():
    import routes.payments as p
    src = inspect.getsource(p.payment_status)
    assert "is_super_admin" in src, "Rolle admin allein darf fremde Zahlungen nicht lesen"
    # Der Betreiber liest nur: return VOR der Freischaltung
    assert src.index("return tx") < src.index("_activate_paid_transaction")


def test_a5_a6_stripe_freischaltung_prueft_rolle_und_sperre():
    import routes.payments as p
    from deps import db
    dbx = _db()
    uid_s, uid_b, uid_g = (f"r13pay_{k}_{SUF}" for k in ("sucher", "buyer", "gesperrt"))
    dbx.users.insert_many([
        {"id": uid_s, "email": f"r13pay_s_{SUF}@{MAIL}", "role": "sucher", "active": True,
         "dealer_id": "x", "created_at": _jetzt()},
        {"id": uid_b, "email": f"r13pay_b_{SUF}@{MAIL}", "role": "b2b_buyer", "active": False,
         "created_at": _jetzt()},
        {"id": uid_g, "email": f"r13pay_g_{SUF}@{MAIL}", "role": "b2b_buyer", "active": True,
         "marketplace_access": {"active": False, "gesperrt": True, "gesperrt_am": _jetzt(),
                                "gesperrt_von": "test"},
         "created_at": _jetzt()},
    ])
    try:
        for uid, wort in ((uid_s, "kein Zwischenhaendler"), (uid_b, "gesperrt")):
            with pytest.raises(RuntimeError, match=wort):
                asyncio.run(p._zugang_freischalten({"user_id": uid, "plan": "marktplatz"},
                                                   f"cs_test_{SUF}_{uid}"))
        # gesperrter, aber aktiver Kaeufer: Laufzeit wird gebucht, Sperre bleibt
        asyncio.run(p._zugang_freischalten({"user_id": uid_g, "plan": "marktplatz"}, f"cs_test_{SUF}_g"))
        acc = dbx.users.find_one({"id": uid_g})["marketplace_access"]
        assert acc["active"] is True and acc.get("gesperrt") is True and acc.get("gesperrt_von") == "test"
        import routes.marketplace as m
        assert m._access_status({"role": "b2b_buyer", "marketplace_access": acc})["gesperrt"] is True
    finally:
        dbx.users.delete_many({"id": {"$in": [uid_s, uid_b, uid_g]}})
        dbx.manual_payments.delete_many({"subject_user_id": {"$in": [uid_s, uid_b, uid_g]}})


def test_b5_email_namensraum_und_b6_reset_quelle():
    from deps import email_vergeben
    import routes.auth as a
    dbx = _db()
    mail = f"r13ns_{SUF}@{MAIL}"
    dbx.driver_accounts.insert_one({"id": f"r13drv_{SUF}", "email": mail, "active": True,
                                    "password_hash": "x", "created_at": _jetzt()})
    try:
        assert asyncio.run(email_vergeben(mail)) == "driver"
        assert asyncio.run(email_vergeben(mail.upper())) == "driver"
        assert asyncio.run(email_vergeben(f"frei_{SUF}@{MAIL}")) is None
    finally:
        dbx.driver_accounts.delete_many({"id": f"r13drv_{SUF}"})
    src = inspect.getsource(a.password_reset_request)
    assert "kandidaten" in src and "driver_accounts" in src
    assert "if not u:" not in src, "Fahrerkonto darf nicht mehr hinter dem users-Treffer verschwinden"


def test_b8_snapshots_pseudonymisieren():
    from snapshot_service import snapshots_pseudonymisieren, snapshot_pseudonym
    from deps import db
    dbx = _db()
    d1, d2, u1, u2 = (f"r13snap_{k}_{SUF}" for k in ("d1", "d2", "u1", "u2"))
    dbx.listing_snapshots.insert_many([
        {"id": f"s1_{SUF}", "dealer_id": d1, "user_id": u1, "mobile_ad_id": "1", "created_at": _jetzt()},
        {"id": f"s2_{SUF}", "dealer_id": d1, "user_id": u2, "mobile_ad_id": "2", "created_at": _jetzt()},
        {"id": f"s3_{SUF}", "dealer_id": d2, "user_id": u1, "mobile_ad_id": "3", "created_at": _jetzt()},
    ])
    try:
        assert asyncio.run(snapshots_pseudonymisieren(db, dealer_id=d1)) == 2
        s1 = dbx.listing_snapshots.find_one({"id": f"s1_{SUF}"})
        assert s1["dealer_id"] == snapshot_pseudonym(d1) and s1["user_id"] == snapshot_pseudonym(u1)
        assert s1["dealer_id"].startswith("geloescht-") and ":" not in s1["dealer_id"]
        assert dbx.listing_snapshots.find_one({"id": f"s3_{SUF}"})["dealer_id"] == d2, "fremde Firma unangetastet"
        assert asyncio.run(snapshots_pseudonymisieren(db, user_id=u1)) == 1
        assert dbx.listing_snapshots.find_one({"id": f"s3_{SUF}"})["user_id"] == snapshot_pseudonym(u1)
        assert asyncio.run(snapshots_pseudonymisieren(db)) == 0
    finally:
        dbx.listing_snapshots.delete_many({"id": {"$in": [f"s{i}_{SUF}" for i in (1, 2, 3)]}})


# =====================================================================
#                          HTTP-Tests
# =====================================================================
def _haendler(nr, oeffentlich):
    r = requests.post(f"{API}/auth/register", json={
        "email": f"r13_chef{nr}_{SUF}@{MAIL}", "password": PW,
        "company_name": f"R13 Autohaus {nr} {SUF}", "contact_person": f"Chef {nr}",
        "phone": "0511 1"}, timeout=30)
    assert r.status_code == 200, f"Backend braucht SELF_SIGNUP=true: {r.text[:200]}"
    kopf = _kopf(r.json()["token"])
    r2 = requests.put(f"{API}/dealer/marketplace-profile", headers=kopf,
                      json={"public": oeffentlich, "description": f"R13 {nr}"}, timeout=30)
    assert r2.status_code == 200, r2.text[:200]
    return {"kopf": kopf, "dealer_id": r.json()["user"]["dealer_id"],
            "user_id": r.json()["user"]["id"], "mail": f"r13_chef{nr}_{SUF}@{MAIL}"}


def _inserat(h, name, sichtbarkeit):
    dbx = _db()
    vid = str(uuid.uuid4())
    dbx.vehicles.insert_one({
        "id": vid, "dealer_id": h["dealer_id"], "lifecycle": "bestand", "status": "Bestand",
        "purchase_price": 5000,
        "data": {"make_label": "VW", "model_label": name, "mileage": 90000,
                 "first_registration": "01/2020", "fuel_label": "Benzin", "power_ps": 110,
                 "images": []},
        "created_at": _jetzt(), "updated_at": _jetzt()})
    r = requests.post(f"{API}/resale/draft/{vid}", headers=h["kopf"], timeout=30)
    assert r.status_code == 200, r.text[:300]
    lid = r.json()["id"]
    assert requests.put(f"{API}/resale/{lid}", headers=h["kopf"],
                        json={"price_public": 9900, "price_b2b": 9000}, timeout=30).status_code == 200
    assert requests.post(f"{API}/resale/{lid}/status", headers=h["kopf"],
                         json={"status": "verkaufsbereit"}, timeout=30).status_code == 200
    r = requests.post(f"{API}/resale/{lid}/publish", headers=h["kopf"],
                      json={"visibility": sichtbarkeit}, timeout=30)
    assert r.status_code == 200, r.text[:300]
    return lid


def _kaeufer(nr):
    mail = f"r13_kaeufer{nr}_{SUF}@{MAIL}"
    r = requests.post(f"{API}/buyer/register", json={
        "gewerblich_bestaetigt": True, "company_name": f"R13 Kaeufer {nr} {SUF}",
        "contact_name": "K M", "email": mail, "password": PW, "phone": "0511 2"}, timeout=30)
    assert r.status_code == 200, r.text[:300]
    kopf = _kopf(r.json()["token"])
    uid = requests.get(f"{API}/buyer/me", headers=kopf, timeout=30).json()["id"]
    return {"kopf": kopf, "id": uid, "mail": mail}


@pytest.fixture(scope="module")
def welt():
    import bcrypt
    dbx = _db()
    z = {}
    admin_mail = f"r13_admin_{SUF}@{MAIL}"
    dbx.users.insert_one({
        "id": f"r13adm_{SUF}", "email": admin_mail, "role": "admin", "active": True,
        "dealer_id": None, "is_super_admin": True,
        "password_hash": bcrypt.hashpw(PW.encode(), bcrypt.gensalt()).decode(),
        "created_at": "2026-01-01T00:00:00+00:00"})
    z["A"] = _login(admin_mail)
    z["h1"] = _haendler(1, True)
    z["oeffentlich"] = _inserat(z["h1"], f"Golf offen {SUF}", "public")
    z["privat"] = _inserat(z["h1"], f"Golf privat {SUF}", "private")
    z["k1"] = _kaeufer(1)
    z["k2"] = _kaeufer(2)
    yield z
    for mail in (admin_mail, z["h1"]["mail"], z["k1"]["mail"], z["k2"]["mail"]):
        dbx.users.delete_many({"email": mail})
    dbx.users.delete_many({"email": {"$regex": f"_{SUF}@"}})
    d = z["h1"]["dealer_id"]
    for coll in ("resale_listings", "vehicles", "network_members", "dealer_invites",
                 "listing_interest", "activity_logs", "subscriptions", "pickup_reports",
                 "vehicle_comparisons"):
        dbx[coll].delete_many({"dealer_id": d})
    dbx.buyer_favorites.delete_many({"buyer_user_id": {"$in": [z["k1"]["id"], z["k2"]["id"]]}})
    dbx.payment_transactions.delete_many({"session_id": {"$regex": f"_{SUF}$"}})
    dbx.driver_accounts.delete_many({"email": {"$regex": f"_{SUF}@"}})
    dbx.listing_snapshots.delete_many({"mobile_ad_id": {"$regex": f"r13_{SUF}"}})
    dbx.dealers.delete_many({"id": d})


def _einladung(h):
    r = requests.post(f"{API}/dealer/invites", headers=h["kopf"], json={"max_uses": 5}, timeout=30)
    assert r.status_code == 200, r.text[:300]
    token = r.json().get("token") or (r.json().get("invite") or {}).get("token")
    assert token, r.text[:200]
    return token


def test_a7_haendleruebersicht_zaehlt_private_nur_im_netzwerk(welt):
    h1 = welt["h1"]

    def zahl(kopf=None):
        r = requests.get(f"{API}/marktplatz/haendler", headers=kopf, timeout=30)
        assert r.status_code == 200, r.text[:200]
        eintraege = r.json() if isinstance(r.json(), list) else r.json().get("items") or r.json().get("dealers") or []
        me = next((d for d in eintraege if (d.get("dealer_id") or d.get("id")) == h1["dealer_id"]), None)
        assert me is not None, "oeffentlicher Haendler fehlt in der Uebersicht"
        return me.get("vehicle_count")

    assert zahl() == 1, "Besucher darf das private Inserat nicht mitzaehlen"
    assert zahl(welt["k1"]["kopf"]) == 1
    token = _einladung(h1)
    r = requests.post(f"{API}/invites/{token}/redeem", headers=welt["k1"]["kopf"], timeout=30)
    assert r.status_code == 200, r.text[:300]
    assert zahl(welt["k1"]["kopf"]) == 2, "Netzwerkmitglied sieht beide"
    assert zahl() == 1


def test_c4_a10_netzwerk_widerruf_beendet_verhandlung_und_merkliste(welt):
    h1, k1 = welt["h1"], welt["k1"]
    # k1 ist im Netzwerk (voriger Test): privates Inserat merken und anfragen
    r = requests.post(f"{API}/marktplatz/favoriten/{welt['privat']}", headers=k1["kopf"], timeout=30)
    assert r.status_code == 200 and r.json().get("favorit") is True, r.text[:200]
    assert welt["privat"] in set(requests.get(f"{API}/marktplatz/favoriten", headers=k1["kopf"],
                                              timeout=30).json()["listing_ids"])
    r = requests.post(f"{API}/marktplatz/listings/{welt['privat']}/interesse", headers=k1["kopf"],
                      json={"offer": 8500, "message": "Interesse"}, timeout=30)
    assert r.status_code == 200, r.text[:300]
    interest_id = r.json().get("id") or r.json().get("interest_id")
    if not interest_id:
        interest_id = _db().listing_interest.find_one(
            {"listing_id": welt["privat"], "buyer_user_id": k1["id"]}, sort=[("created_at", -1)])["id"]
    r = requests.post(f"{API}/interessen/{interest_id}/antwort", headers=h1["kopf"],
                      json={"action": "gegenangebot", "counter_offer": 8900}, timeout=30)
    assert r.status_code == 200, r.text[:300]
    # Chef entfernt k1 aus dem Netzwerk
    r = requests.delete(f"{API}/dealer/network/members/{k1['id']}", headers=h1["kopf"], timeout=30)
    assert r.status_code == 200, r.text[:300]
    # A10: laufende Verhandlung kann nicht mehr angenommen werden, nichts reserviert
    r = requests.post(f"{API}/interessen/{interest_id}/kaeufer-antwort", headers=k1["kopf"],
                      json={"action": "annehmen"}, timeout=30)
    assert r.status_code == 403, r.text[:300]
    assert _db().resale_listings.find_one({"id": welt["privat"]})["status"] == "veroeffentlicht"
    # C4: privates Inserat ist aus der Merkliste verschwunden (DB und Antwort)
    assert welt["privat"] not in set(requests.get(f"{API}/marktplatz/favoriten", headers=k1["kopf"],
                                                  timeout=30).json()["listing_ids"])
    assert _db().buyer_favorites.count_documents({"buyer_user_id": k1["id"], "listing_id": welt["privat"]}) == 0


def test_c6_betreiber_sperre_gilt_im_kostenlos_modus(welt):
    k2 = welt["k2"]
    r = requests.post(f"{API}/admin/buyers/{k2['id']}/access", headers=welt["A"], json={"plan": None}, timeout=30)
    assert r.status_code == 200 and r.json().get("gesperrt") is True, r.text[:200]
    z = requests.get(f"{API}/marktplatz/zugang", headers=k2["kopf"], timeout=30)
    assert z.status_code in (200, 403), z.text[:200]
    if z.status_code == 200:
        assert z.json().get("active") is False and z.json().get("gesperrt") is True, z.json()
    assert requests.get(f"{API}/marktplatz/listings", headers=k2["kopf"], timeout=30).status_code == 403
    assert requests.post(f"{API}/marktplatz/favoriten/{welt['oeffentlich']}", headers=k2["kopf"],
                         timeout=30).status_code == 403
    assert requests.get(f"{API}/buyer/interessen", headers=k2["kopf"], timeout=30).status_code == 403
    # ohne Token bleibt die oeffentliche Liste oeffentlich
    assert requests.get(f"{API}/marktplatz/listings", timeout=30).status_code == 200
    # Betreiber hebt die Sperre auf
    r = requests.post(f"{API}/admin/buyers/{k2['id']}/access", headers=welt["A"],
                      json={"plan": "monthly", "zahlungsart": "kulanz", "grund": "Test R13"}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    acc = _db().users.find_one({"id": k2["id"]})["marketplace_access"]
    assert acc["active"] is True and not acc.get("gesperrt")
    assert requests.get(f"{API}/marktplatz/listings", headers=k2["kopf"], timeout=30).status_code == 200


def test_a3_a4_zahlungsstatus_http(welt):
    sid = f"cs_r13_{SUF}"
    dbx = _db()
    dbx.payment_transactions.insert_one({
        "session_id": sid, "user_id": welt["k2"]["id"], "plan": "marktplatz",
        "status": "activation_failed", "payment_status": "paid", "amount": 2000,
        "currency": "eur", "created_at": _jetzt(), "updated_at": _jetzt()})
    vorher = dbx.users.find_one({"id": welt["k2"]["id"]}).get("marketplace_access")
    # fremder Firmenchef: 403
    assert requests.get(f"{API}/payments/status/{sid}", headers=welt["h1"]["kopf"], timeout=30).status_code == 403
    # Betreiber: lesen ja, Freischaltung nein
    r = requests.get(f"{API}/payments/status/{sid}", headers=welt["A"], timeout=30)
    assert r.status_code == 200, r.text[:200]
    assert dbx.payment_transactions.find_one({"session_id": sid})["status"] == "activation_failed"
    assert dbx.users.find_one({"id": welt["k2"]["id"]}).get("marketplace_access") == vorher


def test_b5_doppelkonto_ueber_kontotypen_abgelehnt(welt):
    dbx = _db()
    mail = f"r13_fahrer_{SUF}@{MAIL}"
    dbx.driver_accounts.insert_one({"id": f"r13drv2_{SUF}", "email": mail, "active": True,
                                    "password_hash": "x", "created_at": _jetzt()})
    r = requests.post(f"{API}/auth/register", json={
        "email": mail, "password": PW, "company_name": f"Dup {SUF}", "contact_person": "D",
        "phone": "0511 1"}, timeout=30)
    assert r.status_code == 409, r.text[:200]
    r = requests.post(f"{API}/buyer/register", json={
        "gewerblich_bestaetigt": True, "company_name": f"Dup K {SUF}", "contact_name": "D K",
        "email": mail, "password": PW, "phone": "0511 2"}, timeout=30)
    assert r.status_code == 409, r.text[:200]
    r = requests.post(f"{API}/dealer/sucher", headers=welt["h1"]["kopf"], json={
        "email": mail, "password": PW, "first_name": "D", "last_name": "S"}, timeout=30)
    assert r.status_code == 409, r.text[:200]
    r = requests.post(f"{API}/admin/users", headers=welt["A"], json={
        "email": mail, "password": PW, "company_name": f"Dup A {SUF}", "plan_type": "none"}, timeout=60)
    assert r.status_code == 409, r.text[:200]
    assert dbx.users.count_documents({"email": mail}) == 0


def test_b8_sucher_loeschen_pseudonymisiert_snapshots(welt):
    from snapshot_service import snapshot_pseudonym
    dbx = _db()
    r = requests.post(f"{API}/dealer/sucher", headers=welt["h1"]["kopf"], json={
        "email": f"r13_sucher_{SUF}@{MAIL}", "password": PW, "first_name": "S", "last_name": "N"}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    sid = r.json()["sucher_id"]
    dbx.listing_snapshots.insert_one({"id": f"snapx_{SUF}", "dealer_id": welt["h1"]["dealer_id"],
                                      "user_id": sid, "mobile_ad_id": f"r13_{SUF}", "created_at": _jetzt()})
    assert requests.delete(f"{API}/dealer/sucher/{sid}", headers=welt["h1"]["kopf"], timeout=30).status_code == 200
    snap = dbx.listing_snapshots.find_one({"id": f"snapx_{SUF}"})
    assert snap["user_id"] == snapshot_pseudonym(sid) and snap["dealer_id"] == welt["h1"]["dealer_id"]


def test_b3_admin_vergleiche_ohne_firmenvermischung(welt):
    dbx = _db()
    ad = f"r13ad{SUF}"
    fremd = f"r13_fremd_{SUF}"
    dbx.vehicles.insert_many([
        # Firma h1: gekauft, mit Haendlerkorrektur (Kilometer nach Abholung), reine Daten in inserat_aktuell
        {"id": f"v_{ad}", "dealer_id": welt["h1"]["dealer_id"], "mobile_ad_id": ad, "lifecycle": "gekauft",
         "data": {"make_label": "BMW", "model": "M3", "mileage": 123456, "price": 1},
         "inserat_aktuell": {"make_label": "BMW", "model": "M3", "mileage": 50000, "price": 30000},
         "inserat_aktuell_am": "2026-09-01T00:00:00+00:00", "updated_at": "2026-09-01T00:00:00+00:00",
         "created_at": _jetzt()},
        # fremde Firma: nur verglichen, reine Inseratsdaten, juenger
        {"id": f"v_{ad}", "dealer_id": fremd, "mobile_ad_id": ad, "lifecycle": "verglichen",
         "data": {"make_label": "BMW", "model": "M3", "mileage": 51000, "price": 29500},
         "updated_at": "2026-09-05T00:00:00+00:00", "created_at": _jetzt()},
    ])
    dbx.vehicle_comparisons.insert_many([
        {"id": str(uuid.uuid4()), "mobile_ad_id": ad, "source": "mobile", "dealer_id": welt["h1"]["dealer_id"],
         "user_id": welt["h1"]["user_id"], "created_at": _jetzt()},
        {"id": str(uuid.uuid4()), "mobile_ad_id": ad, "source": "mobile", "dealer_id": fremd,
         "user_id": f"r13_fremduser_{SUF}", "created_at": _jetzt()},
    ])
    try:
        r = requests.get(f"{API}/admin/comparisons?limit=1000", headers=welt["A"], timeout=60)
        assert r.status_code == 200, r.text[:200]
        zeile = next((i for i in r.json()["items"] if i.get("ad_id") == ad or i.get("mobile_ad_id") == ad), None)
        assert zeile is not None, "Vergleichszeile fehlt"
        assert zeile["vehicle"]["mileage"] in (50000, 51000), zeile["vehicle"]
        assert zeile["vehicle"]["mileage"] != 123456, "Haendlerkorrektur darf nicht angezeigt werden"
        assert zeile["vehicle"]["korrigiert"] is False and zeile.get("firmen") == 2, zeile
    finally:
        dbx.vehicles.delete_many({"mobile_ad_id": ad})
        dbx.vehicle_comparisons.delete_many({"mobile_ad_id": ad})


def test_b1_abholfoto_nur_ueber_bericht(welt):
    from storage_service import save_async, storage
    dbx = _db()
    key = f"pickup/{welt['h1']['dealer_id']}/r13_{SUF}/foto.jpg"
    asyncio.run(save_async(key, b"\xff\xd8\xff\xe0testbild"))
    try:
        r = requests.get(f"{API}/pickup-fotos/{key}", headers=welt["h1"]["kopf"], timeout=30)
        assert r.status_code == 404, "ohne Abholbericht der Firma kein Foto"
        dbx.pickup_reports.insert_one({
            "id": f"r13rep_{SUF}", "dealer_id": welt["h1"]["dealer_id"], "appointment_id": "x",
            "deviations": [{"id": "d1", "field": "damage", "photo_key": key}], "created_at": _jetzt()})
        r = requests.get(f"{API}/pickup-fotos/{key}", headers=welt["h1"]["kopf"], timeout=30)
        assert r.status_code == 200, r.text[:200]
    finally:
        dbx.pickup_reports.delete_many({"id": f"r13rep_{SUF}"})
        try:
            storage.delete(key)
        except Exception:
            pass


def test_a8_a9_gesperrte_firma_verschwindet_und_einladung_gilt_nicht(welt):
    h1, k2 = welt["h1"], welt["k2"]
    token = _einladung(h1)
    sichtbar = requests.get(f"{API}/marktplatz/haendler", timeout=30)
    assert h1["dealer_id"] in {(d.get("dealer_id") or d.get("id")) for d in (sichtbar.json() if isinstance(sichtbar.json(), list)
                                                       else sichtbar.json().get("items") or sichtbar.json().get("dealers") or [])}
    # Betreiber sperrt den Chef
    r = requests.post(f"{API}/admin/users/{h1['user_id']}/active", headers=welt["A"],
                      json={"active": False}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    try:
        eintraege = requests.get(f"{API}/marktplatz/haendler", timeout=30).json()
        eintraege = eintraege if isinstance(eintraege, list) else eintraege.get("items") or eintraege.get("dealers") or []
        assert h1["dealer_id"] not in {(d.get("dealer_id") or d.get("id")) for d in eintraege}, "gesperrte Firma weiter in der Uebersicht"
        assert requests.get(f"{API}/marktplatz/haendler/{h1['dealer_id']}", timeout=30).status_code == 404
        assert welt["oeffentlich"] not in _ids(requests.get(f"{API}/marktplatz/listings", timeout=30))
        # A9: Einladung waehrend der Sperre nicht einloesbar
        r = requests.post(f"{API}/invites/{token}/redeem", headers=k2["kopf"], timeout=30)
        assert r.status_code != 200, r.text[:200]
        assert _db().network_members.count_documents({"dealer_id": h1["dealer_id"], "buyer_user_id": k2["id"]}) == 0
    finally:
        r = requests.post(f"{API}/admin/users/{h1['user_id']}/active", headers=welt["A"],
                          json={"active": True}, timeout=30)
        assert r.status_code == 200, r.text[:200]
    # nach dem Entsperren: sichtbar und Einladung wieder gueltig (Nutzung wurde nicht verbraucht)
    assert welt["oeffentlich"] in _ids(requests.get(f"{API}/marktplatz/listings", timeout=30))
    r = requests.post(f"{API}/invites/{token}/redeem", headers=k2["kopf"], timeout=30)
    assert r.status_code == 200, r.text[:300]
