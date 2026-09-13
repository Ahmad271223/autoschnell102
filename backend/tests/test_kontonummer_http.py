# -*- coding: utf-8 -*-
"""Kontonummer (13.09.2026), Schritt 1 — Anmeldung per Nummer ueber HTTP.

  - /auth/login mit Feld kontonummer und per Alias email
  - Sucher-Nummer in Varianten ('10023 2', '10023/2', ' 10023-02 ')
  - /buyer/login und /driver/login per Nummer (driver: email als str, kein 422)
  - Nummer aus einem anderen Bereich -> 401
  - Super-Admin per Benutzername; username eines Nicht-Super-Admins -> 401
  - jedes neu angelegte Konto hat kontonummer + kontonummer_basis,
    Teil-Unique-Index kontonummer_eindeutig in users und driver_accounts

Braucht ein laufendes Backend auf TEST_BASE_URL (RUNDE14_HTTP=1, SELF_SIGNUP=true
fuer /buyer/register und /driver/register) und dieselbe DB (DB_NAME).  (ALTWEG – Schritt 5)
Die Sperre des Konto-Limiters wird in-process geprueft (test_kontonummer.py) —
ueber 127.0.0.1 gilt die Loopback-Ausnahme.
"""
import os
import uuid
from datetime import datetime, timezone

import pytest
import requests

HTTP = os.environ.get("RUNDE14_HTTP") == "1"
pytestmark = pytest.mark.skipif(
    not HTTP, reason="RUNDE14_HTTP=1 nicht gesetzt — HTTP-Test braucht ein laufendes "
                     "Backend auf TEST_BASE_URL (CI: Schritt Selbsttest-Suite)")

BASE = (os.environ.get("TEST_BASE_URL") or "http://localhost:8001").rstrip("/")
API = f"{BASE}/api"
MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
DB_NAME = os.environ.get("DB_NAME") or "autoschnell"
SUF = uuid.uuid4().hex[:8]
PW = "KontoNummer13!x"
MAIL = "e2etest-mail.de"


def _db():
    from pymongo import MongoClient
    return MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)[DB_NAME]


def _hash(pw):
    import bcrypt
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()


def _post(pfad, json, headers=None):
    return requests.post(f"{API}{pfad}", json=json, headers=headers, timeout=60)


@pytest.fixture(scope="module")
def welt():
    dbx = _db()
    jetzt = datetime.now(timezone.utc).isoformat()
    z = {"user_ids": [], "dealer_ids": [], "driver_ids": []}
    sa_name = f"t-sa-{SUF}"
    sa_id = f"t_sa_{SUF}"
    # E-Mail bleibt bis Schritt 2 gesetzt (users.email_1 ist noch ein voller
    # Unique-Index — mehrere Konten ohne Adresse kollidieren auf null).
    dbx.users.insert_one({"id": sa_id, "username": sa_name, "role": "admin",
                          "email": f"t_sa_{SUF}@{MAIL}",
                          "is_super_admin": True, "active": True, "dealer_id": None,
                          "password_hash": _hash(PW), "current_session_id": None,
                          "created_at": jetzt})
    z["user_ids"].append(sa_id)
    z["sa_name"] = sa_name
    try:
        r = _post("/auth/login", {"kontonummer": sa_name, "password": PW})
        assert r.status_code == 200, f"Super-Admin per Benutzername: {r.text[:200]}"
        kopf = {"Authorization": f"Bearer {r.json()['token']}"}
        # Firma (Chef) ueber den Betreiber
        r = _post("/admin/users", {"email": f"kn_chef_{SUF}@{MAIL}", "password": PW,
                                   "company_name": f"KN Firma {SUF}", "plan_type": "none"},
                  kopf)
        assert r.status_code == 200, r.text[:200]
        z["firma"] = r.json()
        z["user_ids"].append(z["firma"]["user_id"])
        z["dealer_ids"].append(z["firma"]["dealer_id"])
        # Sucher ueber den Betreiber
        r = _post(f"/admin/dealers/{z['firma']['dealer_id']}/sucher",
                  {"email": f"kn_such_{SUF}@{MAIL}", "password": PW,
                   "first_name": "Kn", "last_name": "Sucher"}, kopf)
        assert r.status_code == 200, r.text[:200]
        z["sucher"] = r.json()
        z["user_ids"].append(z["sucher"]["sucher_id"])
        # Kaeufer und Fahrer (bis Schritt 5 per Selbstregistrierung, SELF_SIGNUP=true)
        r = _post("/buyer/register", {"company_name": f"KN Kaeufer {SUF}",  # ALTWEG – Schritt 5
                                      "contact_name": "K M", "email": f"kn_kauf_{SUF}@{MAIL}",
                                      "password": PW, "gewerblich_bestaetigt": True})
        assert r.status_code == 200, r.text[:200]
        z["kaeufer"] = r.json()["user"]
        z["user_ids"].append(z["kaeufer"]["id"])
        r = _post("/driver/register", {"email": f"kn_fahr_{SUF}@{MAIL}", "password": PW,  # ALTWEG – Schritt 5
                                       "display_name": "KN Fahrer"})
        assert r.status_code == 200, r.text[:200]
        z["fahrer"] = r.json()["driver"]
        z["driver_ids"].append(z["fahrer"]["id"])
        yield z
    finally:
        dbx.users.delete_many({"$or": [{"id": {"$in": z["user_ids"]}},
                                       {"email": {"$regex": f"_{SUF}@"}},
                                       {"username": {"$regex": SUF}}]})
        dbx.dealers.delete_many({"id": {"$in": z["dealer_ids"]}})
        dbx.driver_accounts.delete_many({"$or": [{"id": {"$in": z["driver_ids"]}},
                                                 {"email": {"$regex": f"_{SUF}@"}}]})
        dbx.subscriptions.delete_many({"dealer_id": {"$in": z["dealer_ids"]}})
        dbx.activity_logs.delete_many({"$or": [{"user_id": {"$in": z["user_ids"]}},
                                               {"ref": {"$in": z["user_ids"]}}]})


def test_01_anlage_liefert_nummern_und_speichert_sie(welt):
    dbx = _db()
    firma, sucher = welt["firma"], welt["sucher"]
    assert firma["kontonummer"] == str(firma["kunden_nr"])
    assert sucher["kontonummer"] == f"{firma['kunden_nr']}-1"
    chef = dbx.users.find_one({"id": firma["user_id"]})
    assert chef["kontonummer"] == firma["kontonummer"]
    assert chef["kontonummer_basis"] == firma["kunden_nr"]
    assert dbx.dealers.find_one({"id": firma["dealer_id"]})["kunden_nr"] == firma["kunden_nr"]
    s = dbx.users.find_one({"id": sucher["sucher_id"]})
    assert s["kontonummer_basis"] == firma["kunden_nr"] and s["role"] == "sucher"
    k = dbx.users.find_one({"id": welt["kaeufer"]["id"]})
    f = dbx.driver_accounts.find_one({"id": welt["fahrer"]["id"]})
    for doc, antwort in ((k, welt["kaeufer"]), (f, welt["fahrer"])):
        assert isinstance(doc["kontonummer"], str) and doc["kontonummer"] == antwort["kontonummer"]
        assert doc["kontonummer_basis"] == int(doc["kontonummer"])
    # gemeinsame Reihe: keine Nummer doppelt ueber die Kontoarten
    assert len({firma["kontonummer"], k["kontonummer"], f["kontonummer"]}) == 3
    for coll in (dbx.users, dbx.driver_accounts):
        idx = coll.index_information()["kontonummer_eindeutig"]
        assert idx.get("unique") and idx["partialFilterExpression"] == {
            "kontonummer": {"$type": "string"}}, idx


def test_02_chef_per_nummer_und_alias_email(welt):
    nr = welt["firma"]["kontonummer"]
    r = _post("/auth/login", {"kontonummer": nr, "password": PW})
    assert r.status_code == 200, r.text[:200]
    assert r.json()["user"]["kontonummer"] == nr
    r = _post("/auth/login", {"email": nr, "password": PW})
    assert r.status_code == 200, r.text[:200]
    r = _post("/auth/login", {"kontonummer": nr, "password": "Falsch-falsch1"})
    assert r.status_code == 401


def test_03_sucher_nummer_in_varianten(welt):
    kunden_nr = welt["firma"]["kunden_nr"]
    for variante in (f"{kunden_nr} 1", f"{kunden_nr}/1", f" {kunden_nr}-01 ",
                     f"{kunden_nr}–1"):
        r = _post("/auth/login", {"kontonummer": variante, "password": PW})
        assert r.status_code == 200, (variante, r.text[:200])
        assert r.json()["user"]["kontonummer"] == welt["sucher"]["kontonummer"]


def test_04_kaeufer_per_nummer(welt):
    nr = welt["kaeufer"]["kontonummer"]
    r = _post("/buyer/login", {"kontonummer": nr, "password": PW})
    assert r.status_code == 200, r.text[:200]
    assert r.json()["user"]["kontonummer"] == nr
    r = _post("/buyer/login", {"email": nr, "password": PW})
    assert r.status_code == 200, r.text[:200]
    me = requests.get(f"{API}/buyer/me", timeout=30,
                      headers={"Authorization": f"Bearer {r.json()['token']}"})
    assert me.status_code == 200 and me.json()["kontonummer"] == nr


def test_05_fahrer_per_nummer(welt):
    nr = welt["fahrer"]["kontonummer"]
    r = _post("/driver/login", {"kontonummer": nr, "password": PW})
    assert r.status_code == 200, r.text[:200]
    assert r.json()["driver"]["kontonummer"] == nr
    # Alias email mit einer Nummer: str statt EmailStr -> kein 422
    r = _post("/driver/login", {"email": nr, "password": PW})
    assert r.status_code == 200, r.text[:200]
    me = requests.get(f"{API}/driver/me", timeout=30,
                      headers={"Authorization": f"Bearer {r.json()['token']}"})
    assert me.status_code == 200 and me.json()["kontonummer"] == nr


def test_06_nummer_aus_falschem_bereich_401(welt):
    chef = welt["firma"]["kontonummer"]
    sucher = welt["sucher"]["kontonummer"]
    fahrer = welt["fahrer"]["kontonummer"]
    for pfad, kennung in (("/auth/login", fahrer), ("/driver/login", chef),
                          ("/driver/login", welt["kaeufer"]["kontonummer"]),
                          ("/buyer/login", chef), ("/buyer/login", sucher),
                          ("/auth/login", "999999999"), ("/driver/login", "999999999")):
        r = _post(pfad, {"kontonummer": kennung, "password": PW})
        assert r.status_code == 401, (pfad, kennung, r.status_code, r.text[:200])


def test_07_benutzername_nur_fuer_den_super_admin(welt):
    dbx = _db()
    jetzt = datetime.now(timezone.utc).isoformat()
    konten = [
        {"id": f"t_nd_{SUF}", "username": f"t-nd-{SUF}", "role": "dealer",
         "email": f"t_nd_{SUF}@{MAIL}", "dealer_id": welt["firma"]["dealer_id"]},
        {"id": f"t_na_{SUF}", "username": f"t-na-{SUF}", "role": "admin",
         "email": f"t_na_{SUF}@{MAIL}", "is_super_admin": False, "dealer_id": None},
    ]
    for k in konten:
        dbx.users.insert_one({**k, "active": True, "password_hash": _hash(PW),
                              "current_session_id": None, "created_at": jetzt})
        welt["user_ids"].append(k["id"])
    for k in konten:
        r = _post("/auth/login", {"kontonummer": k["username"], "password": PW})
        assert r.status_code == 401, (k["role"], r.text[:200])


def test_08_super_admin_per_benutzername_im_alias_feld(welt):
    r = _post("/auth/login", {"email": welt["sa_name"], "password": PW})
    assert r.status_code == 200, r.text[:200]
    assert r.json()["user"].get("is_super_admin") is True
