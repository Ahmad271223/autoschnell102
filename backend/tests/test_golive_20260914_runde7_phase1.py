# -*- coding: utf-8 -*-
"""Go-Live 14.09.2026, Runde 7 — Phase 1 des Befundplans + Entscheidungen Ahmad:

  1.2  Login-Namensraum: Super-Admin-Benutzername vor Kaeufer-Code
  1.3  Zahlen-Parser der Auto-Daten (110.5 -> 111 statt 1105)
  1.4  Betraege: kein 0-Euro-Preis/-Angebot, keine negativen Kosten,
       bool/Freitext sind keine Fahrzeugzahlen
  1.5  AGB-Bestaetigung fuer jede Zugangsanfrage (HTTP)
  1.6  Super-Admin Pflicht in Produktion, /ready meldet 0 Super-Admins
  1.7  Zahlen aus der .env: Tippfehler -> Standard + Meldung statt Absturz
  1.8  Sucher loeschen ohne Chef -> 409
  1.9  Abo aufheben unter der Abo-Sperre (Quelle)
  1.10 Protokoll-Entwurf: Ausweichpfad setzt Fahrer/Fahrzeug (Quelle)
  1.11 Vertragsloeschung auch bei Entwurf blockiert (Quelle)
  1.12 Fehlerarchiv-Deckel loescht erst erledigte
  1.13 Gegenangebote nur bei verfuegbarem Inserat
  1.15 Vertrags-Idempotenz an den Inhalt gebunden (HTTP)
  Stripe entfernt: keine Zahlungsrouten mehr (HTTP)
"""
import asyncio
import inspect
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests
from fastapi import HTTPException
from pydantic import ValidationError

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tests"))

import deps  # noqa: E402
import konten as K  # noqa: E402
import routes.admin as ADMIN  # noqa: E402
import routes.auth as AUTH  # noqa: E402
import routes.marketplace as M  # noqa: E402
import cleanup_service as CS  # noqa: E402

HTTP = os.environ.get("RUNDE14_HTTP") == "1"
http = pytest.mark.skipif(not HTTP, reason="RUNDE14_HTTP=1 nicht gesetzt")
MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
API = K.API
SUF = uuid.uuid4().hex[:8]
PW = f"Rz7{SUF}Kq4Lm9Xw2"


def _jetzt(delta_s: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=delta_s)).isoformat()


@pytest.fixture
def wegwerf(monkeypatch):
    from motor.motor_asyncio import AsyncIOMotorClient
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    name = f"autoschnell_r7_{uuid.uuid4().hex[:10]}"
    db = client[name]
    for mod in (deps, ADMIN, AUTH, M):
        monkeypatch.setattr(mod, "db", db)
    try:
        yield SimpleNamespace(db=db, run=loop.run_until_complete)
    finally:
        try:
            loop.run_until_complete(client.drop_database(name))
        finally:
            client.close()
            loop.close()


# ============================================================ 1.2
def test_12_super_admin_vor_kaeufer_code(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    # Benutzername, der zufaellig wie ein Kaeufer-Code aussieht
    run(db.users.insert_many([
        {"id": "sa", "username": "ADMN7K", "role": "admin", "is_super_admin": True,
         "active": True, "password_hash": "x"},
        {"id": "kb", "kontonummer": "ADMN7K", "role": "b2b_buyer", "active": True,
         "password_hash": "y"},
        {"id": "kc", "kontonummer": "6FE7K2M", "role": "b2b_buyer", "active": True,
         "password_hash": "z"}]))
    assert run(AUTH._konto_fuer_login("ADMN7K"))["id"] == "sa"
    assert run(AUTH._konto_fuer_login("6fe7k2m"))["id"] == "kc"
    assert run(AUTH._konto_fuer_login("gibtsnicht"))is None
    # Seed und Produktionspruefung lehnen solche Benutzernamen ab
    from kontonummer import kennung_normalisieren
    for schlecht in ("ADMN7K", "10023", "FD-7K2M9QX4", "6fe7k2m"):
        assert kennung_normalisieren(schlecht), schlecht
    assert kennung_normalisieren("ci-superadmin") is None


# ============================================================ 1.3
def test_13_zahlen_parser_dezimal_und_tausender():
    from auto_daten import _zahl
    faelle = {"242.000 km": 242000, "111,016 km": 111016, "1.984 ccm": 1984, "110": 110,
              "110.5": 111, "12,5": 13, "150 PS": 150, "1.000": 1000, " 75 kW ": 75,
              110.0: 110, 110.4: 110, 12.5: 13,
              "1e400": None, "abc": None, "": None, None: None, True: None,
              float("inf"): None}
    for roh, erwartet in faelle.items():
        assert _zahl(roh) == erwartet, (roh, _zahl(roh), erwartet)


# ============================================================ 1.4
def test_14_betraege_null_und_negativ():
    import routes.protocols as P
    import routes.appointments as A
    import routes.bestand as Bst
    import routes.resale as R
    with pytest.raises(ValidationError) as e:
        P.FreigabeIn(neuer_preis=0, zurueck=False, stand="x")
    assert any(err["loc"] == ("neuer_preis",) for err in e.value.errors())
    for modell in (M.InterestIn, M.InterestAnswerIn, M.BuyerInterestAnswerIn):
        for feld in ("offer", "counter_offer"):
            if feld in modell.model_fields:
                with pytest.raises(ValidationError) as e:
                    modell(**{feld: 0})
                assert any(err["loc"] == (feld,) for err in e.value.errors()), feld
    with pytest.raises(ValidationError) as e:
        A.AppointmentIn(final_price=0)
    assert any(err["loc"] == ("final_price",) for err in e.value.errors())
    with pytest.raises(HTTPException) as e:
        Bst._clean_costs([{"label": "Transport", "amount": -5000}])
    assert e.value.status_code == 422 and "negativ" in e.value.detail
    assert Bst._clean_costs([{"label": "Transport", "amount": 250}]) == [{"label": "Transport", "amount": 250.0}]
    assert R._zahl_ok(2000) is True
    f = R._fahrzeugwert_bereinigen
    assert f("mileage", "viel")[0] is False
    assert f("mileage", "keine Ahnung")[0] is False
    assert f("mileage", "150.000 km") == (True, 150000)
    assert f("power_ps", "110,5") == (True, 111)
    assert f("mileage", "") == (True, "")
    assert f("mileage", 2000) == (True, 2000)
    assert f("mileage", True)[0] is False
    assert f("mileage", "1e400")[0] is False


# ============================================================ 1.6 + 1.7
def test_16_17_produktionspruefung(monkeypatch):
    import production_check as PC
    import konfig
    quelle = inspect.getsource(PC)
    assert "SUPER_ADMIN_USERNAME und SUPER_ADMIN_PASSWORD muessen in Produktion" in quelle
    assert "APIFY_TOKEN fehlt" in quelle and "BACKUP_S3_BUCKET fehlt" in quelle
    assert "BEWEIS_PRIVATDATEN=true ist in Produktion nicht erlaubt" in quelle
    # Zahlen: Tippfehler wird gemeldet statt Absturz
    monkeypatch.setenv("PROBE_TAGE", "14d")
    assert konfig.zahl_env("PROBE_TAGE", 60) == 60
    assert konfig.FEHLERHAFT.get("PROBE_TAGE") == "14d"
    monkeypatch.setenv("PROBE_TAGE", "90")
    assert konfig.zahl_env("PROBE_TAGE", 60, oben=60) == 60
    assert konfig.zahl_pruefen("PROBE_TAGE") is None
    monkeypatch.setenv("PROBE_TAGE", "x")
    assert konfig.zahl_pruefen("PROBE_TAGE") == "x"
    # /ready: Super-Admin-Zaehlung und MFA-Pflicht sind im Code
    import server
    q = inspect.getsource(server.readiness_check)
    assert "kein aktives Super-Admin-Konto" in q and "MFA_PFLICHT" in q
    assert "_PROZESS_START" in q


# ============================================================ 1.8
def test_18_sucher_loeschen_ohne_chef(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    run(db.users.insert_many([
        {"id": "s1", "role": "sucher", "dealer_id": "d1", "active": True, "password_hash": "x"},
        {"id": "sa", "role": "admin", "is_super_admin": True, "active": True, "dealer_id": None}]))
    run(db.vehicles.insert_one({"id": "v1", "dealer_id": "d1", "owner_user_id": "s1"}))
    sa = {"id": "sa", "is_super_admin": True, "dealer_id": None, "role": "admin"}
    with pytest.raises(HTTPException) as e:
        run(ADMIN.admin_delete_user("s1", firma_loeschen=False, admin=sa))
    assert e.value.status_code == 409 and "Hauptaccount" in e.value.detail
    assert run(db.users.find_one({"id": "s1"}))["active"] is True
    assert run(db.vehicles.find_one({"id": "v1"}))["owner_user_id"] == "s1"


# ============================================================ 1.9 / 1.10 / 1.11 (Quelle)
def test_19_10_11_quellen():
    import routes.protocols as P
    import routes.contracts as C
    q = inspect.getsource(ADMIN.admin_set_sucher_abo)
    assert 'async with _sperre(f"abo:{sucher_id}"' in q
    assert 'async with _sperre(f"abo:{user_id}"' in inspect.getsource(ADMIN.admin_update_user)
    q = inspect.getsource(P.save_protocol)
    assert q.count('"driver_name": driver.get("display_name", "")') >= 2
    q = inspect.getsource(C.delete_contract)
    assert '"entwurf", "zur_freigabe"' in q


# ============================================================ 1.12
def test_112_fehlerarchiv_deckel_erst_erledigte(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    docs = []
    for i in range(6):
        docs.append({"_id": f"e{i}", "status": "resolved" if i < 3 else "open",
                     "created_at": _jetzt(-3600 * (10 - i))})
    run(db.error_logs.insert_many(docs))
    n = run(CS.fehlerlogs_begrenzen(db, datetime.now(timezone.utc), offen_tage=365, maximum=4))
    assert n == 2
    uebrig = {d["_id"] for d in run(db.error_logs.find({}, {"_id": 1}).to_list(None))}
    assert uebrig == {"e2", "e3", "e4", "e5"}, uebrig       # zwei erledigte weg, alle offenen da
    n = run(CS.fehlerlogs_begrenzen(db, datetime.now(timezone.utc), offen_tage=365, maximum=2))
    assert n == 2
    uebrig = {d["_id"] for d in run(db.error_logs.find({}, {"_id": 1}).to_list(None))}
    assert uebrig == {"e4", "e5"}


# ============================================================ 1.13
def test_113_gegenangebot_nur_bei_verfuegbarem_inserat(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    assert M._inserat_verhandelbar({"status": "veroeffentlicht"}, "k1") is True
    assert M._inserat_verhandelbar({"status": "reserviert", "reserved_for": "k1"}, "k1") is True
    assert M._inserat_verhandelbar({"status": "reserviert", "reserved_for": "k2"}, "k1") is False
    for status in ("verkauft", "zurueckgezogen", "geloescht", "entwurf", "verkaufsbereit"):
        assert M._inserat_verhandelbar({"status": status}, "k1") is False, status
    run(db.users.insert_one({"id": "k1", "role": "b2b_buyer", "active": True}))
    run(db.resale_listings.insert_one({"id": "L1", "dealer_id": "d1", "status": "verkauft",
                                       "visibility": "public"}))
    with pytest.raises(HTTPException) as e:
        run(M._kaeufer_darf_noch({"buyer_user_id": "k1", "listing_id": "L1"}))
    assert e.value.status_code == 409 and "nicht mehr verfügbar" in e.value.detail


# ============================================================ HTTP: 1.5, 1.15, Stripe weg
@pytest.fixture(scope="module")
def welt_http():
    if not HTTP:
        pytest.skip("RUNDE14_HTTP=1 nicht gesetzt")
    dbx = K._db()
    z = {"user_ids": [], "dealer_ids": [], "mails": [], "contract_ids": [], "vehicle_ids": []}
    try:
        r = K.registrieren(json={"company_name": f"R7 Firma {SUF}", "password": PW,
                                 "contact_person": "Chef", "email": ""})
        assert r.status_code == 200, r.text[:300]
        z["C"] = {"Authorization": f"Bearer {r.json()['token']}"}
        me = requests.get(f"{API}/auth/me", headers=z["C"], timeout=60).json()["user"]
        z["chef_id"], z["dealer_id"] = me["id"], me["dealer_id"]
        z["user_ids"].append(me["id"])
        z["dealer_ids"].append(me["dealer_id"])
        yield z
    finally:
        dbx.users.delete_many({"$or": [{"id": {"$in": z["user_ids"]}},
                                       {"company_name": {"$regex": SUF}}]})
        dbx.dealers.delete_many({"id": {"$in": z["dealer_ids"]}})
        dbx.plan_requests.delete_many({"contact_email": {"$in": z["mails"]}})
        dbx.generated_pdfs.delete_many({"dealer_id": {"$in": z["dealer_ids"]}})
        dbx.kaufvorgaenge.delete_many({"dealer_id": {"$in": z["dealer_ids"]}})
        dbx.vehicles.delete_many({"dealer_id": {"$in": z["dealer_ids"]}})
        for coll in ("subscriptions", "manual_payments", "admin_vehicle_data"):
            dbx[coll].delete_many({"dealer_id": {"$in": z["dealer_ids"]}})
        dbx.activity_logs.delete_many({"user_id": {"$in": z["user_ids"]}})


@http
def test_h15_agb_fuer_jede_anfrageart(welt_http):
    for art in ("firma", "kaeufer", "fahrer"):
        mail = f"r7_{art}_{uuid.uuid4().hex[:6]}@e2etest-mail.de"
        welt_http["mails"].append(mail)
        body = {"art": art, "company_name": f"R7 {art} {SUF}", "contact_person": "Anna Agb",
                "email": mail, "gewerblich_bestaetigt": False}
        if art == "kaeufer":
            body["ust_id"] = "DE123456789"
        r = requests.post(f"{API}/zugang-anfrage", json=body, timeout=60)
        assert r.status_code == 400, (art, r.status_code, r.text[:200])
        assert K._db().plan_requests.find_one({"contact_email": mail}) is None
        r = requests.post(f"{API}/zugang-anfrage", json={**body, "gewerblich_bestaetigt": True},
                          timeout=60)
        assert r.status_code == 200, (art, r.status_code, r.text[:200])


@http
def test_h_stripe_ist_weg(welt_http):
    for pfad in ("/payments/config", "/payments/checkout", "/payments/status/x"):
        r = requests.get(f"{API}{pfad}", headers=welt_http["C"], timeout=60)
        assert r.status_code in (404, 405), (pfad, r.status_code)
    r = requests.post(f"{API}/webhook/stripe", json={}, timeout=60)
    assert r.status_code in (404, 405), r.status_code


@http
def test_h115_vertrags_idempotenz_haengt_am_inhalt(welt_http):
    dbx = K._db()
    z = welt_http
    dbx.subscriptions.insert_one({"id": f"r7sub_{SUF}", "dealer_id": z["dealer_id"], "plan": "lifetime",
                                  "status": "active", "expires_at": None, "created_at": _jetzt()})
    vid = f"r7v_{SUF}"
    dbx.vehicles.insert_one({"id": vid, "dealer_id": z["dealer_id"], "owner_user_id": z["chef_id"],
                             "source": "manuell", "lifecycle": "verglichen",
                             "data": {"make": "VW", "model": "Golf", "price": 9000,
                                      "first_registration": "2019-05", "mileage": 80000},
                             "created_at": _jetzt(), "updated_at": _jetzt()})
    z["vehicle_ids"].append(vid)
    basis = {"vehicle_id": vid, "purchase_price": 8500, "seller_name": "Max Muster",
             "seller_address": "Weg 1, 30159 Hannover", "seller_phone": "0511 1",
             "idempotency_key": f"r7key{SUF}"}
    r1 = requests.post(f"{API}/contracts", json=basis, headers=z["C"], timeout=120)
    assert r1.status_code == 200, r1.text[:300]
    z["contract_ids"].append(r1.json()["id"])
    # gleicher Schluessel, gleicher Inhalt -> derselbe Vertrag
    r2 = requests.post(f"{API}/contracts", json=basis, headers=z["C"], timeout=120)
    assert r2.status_code == 200 and r2.json()["id"] == r1.json()["id"] and r2.json().get("bereits_vorhanden")
    # gleicher Schluessel, anderer Preis -> 409, kein stiller Rueckgriff
    r3 = requests.post(f"{API}/contracts", json={**basis, "purchase_price": 7000},
                       headers=z["C"], timeout=120)
    assert r3.status_code == 409, (r3.status_code, r3.text[:200])
    assert dbx.generated_pdfs.count_documents({"dealer_id": z["dealer_id"]}) == 1
