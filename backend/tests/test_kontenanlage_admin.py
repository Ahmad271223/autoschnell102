# -*- coding: utf-8 -*-
"""Kontonummer (13.09.2026), Schritt 2 — Kontenanlage nur durch den Super-Admin.

HTTP (RUNDE14_HTTP=1, laufendes Backend auf TEST_BASE_URL, dieselbe DB):
  - Firma zweimal OHNE E-Mail (beide 200, kontonummer = str(kunden_nr));
    kein Unique-Index auf users.email (Schritt 5); zwei Firmen mit derselben
    Kontakt-E-Mail -> beide 200 mit verschiedenen Nummern
  - Sucher ohne E-Mail: -1, -2, nach Loeschen -3; 404/409 fuer Firma
  - Kaeufer ueber POST /admin/buyers (B2B-Nachweis Pflicht, USt-IdNr. geprueft),
    Login /buyer/login; Anfrage art=kaeufer wird beim Anlegen geschlossen
  - Fahrer ueber POST /admin/drivers mit driver_code, ohne Token, Login
  - 403 fuer Chef, Sucher, Kaeufer, normalen Admin (Token direkt); ein
    Fahrer-Token ist fuer /admin gar kein Nutzer-Token (401)
  - GET /drivers, /appointments (Liste, Detail), /dealer/network/members und
    /dealer/interessen enthalten nirgends eine Kontonummer; buyer_name ohne
    E-Mail-Ersatz ('Zwischenhändler')
  - Buchhaltungsfeld recorded_by = Benutzername; /admin/betrieb zaehlt
    konten_ohne_nummer

In-process (immer): Passwort-Setzen durch den Betreiber hebt die Sperre des
Konto-Limiters auf (Chef/Sucher ueber POST und PUT, Fahrer).

Kein Import von server.py. routes.admin und routes.drivers werden NUR hier am
Modulanfang importiert (nie erstmals innerhalb einer Test-Schleife).
"""
import asyncio
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import deps  # noqa: E402  (laedt backend/.env)
import rate_limiter as RL  # noqa: E402
import routes.admin as ADMIN  # noqa: E402  (am Modulanfang, siehe oben)
import routes.drivers  # noqa: E402,F401  (admin_driver_set_password importiert es)

HTTP = os.environ.get("RUNDE14_HTTP") == "1"
http = pytest.mark.skipif(
    not HTTP, reason="RUNDE14_HTTP=1 nicht gesetzt — HTTP-Test braucht ein laufendes "
                     "Backend auf TEST_BASE_URL (CI: Schritt Selbsttest-Suite)")

BASE = (os.environ.get("TEST_BASE_URL") or "http://localhost:8001").rstrip("/")
API = f"{BASE}/api"
MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
DB_NAME = os.environ.get("DB_NAME") or "autoschnell"
SUF = uuid.uuid4().hex[:8]
PW = "Kq4Lm9Xw2-Sicher13!x"   # Runde 14: kein Kontakt-Name ("Anlage") im Passwort
MAIL = "e2etest-mail.de"


def _jetzt():
    return datetime.now(timezone.utc).isoformat()


def _db():
    from pymongo import MongoClient
    return MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)[DB_NAME]


def _hash(pw):
    import bcrypt
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()


def _post(pfad, json, headers=None):
    return requests.post(f"{API}{pfad}", json=json, headers=headers, timeout=60)


def _kopf(token):
    return {"Authorization": f"Bearer {token}"}


def _werte(obj):
    """Alle Schluessel und Werte einer JSON-Antwort (rekursiv)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _werte(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _werte(v)
    else:
        yield obj


@pytest.fixture(scope="module")
def welt():
    if not HTTP:
        pytest.skip("RUNDE14_HTTP=1 nicht gesetzt")
    dbx = _db()
    z = {"user_ids": [], "dealer_ids": [], "driver_ids": [], "anfrage_mails": [],
         "listing_ids": [], "termin_ids": []}
    sa_name, sa_id = f"t-ka-sa-{SUF}", f"t_ka_sa_{SUF}"
    # Ohne E-Mail: seit Schritt 2 ist users.email nur noch ein Teil-Index.
    dbx.users.insert_one({"id": sa_id, "username": sa_name, "role": "admin",
                          "is_super_admin": True, "active": True, "dealer_id": None,
                          "password_hash": _hash(PW), "current_session_id": None,
                          "created_at": _jetzt()})
    z["user_ids"].append(sa_id)
    z["sa_name"] = sa_name
    try:
        r = _post("/auth/login", {"kontonummer": sa_name, "password": PW})
        assert r.status_code == 200, f"Super-Admin per Benutzername: {r.text[:200]}"
        z["S"] = _kopf(r.json()["token"])
        yield z
    finally:
        ids = z["user_ids"] + z["dealer_ids"] + z["driver_ids"]
        dbx.users.delete_many({"$or": [{"id": {"$in": z["user_ids"]}},
                                       {"dealer_id": {"$in": z["dealer_ids"]}},
                                       {"email": {"$regex": f"_{SUF}@"}},
                                       {"company_name": {"$regex": SUF}}]})
        dbx.dealers.delete_many({"id": {"$in": z["dealer_ids"]}})
        dbx.driver_accounts.delete_many({"$or": [{"id": {"$in": z["driver_ids"]}},
                                                 {"display_name": {"$regex": SUF}}]})
        dbx.plan_requests.delete_many({"contact_email": {"$in": z["anfrage_mails"]}})
        for coll in ("subscriptions", "manual_payments", "dealer_drivers",
                     "network_members"):
            dbx[coll].delete_many({"dealer_id": {"$in": z["dealer_ids"]}})
        dbx.appointments.delete_many({"id": {"$in": z["termin_ids"]}})
        dbx.resale_listings.delete_many({"id": {"$in": z["listing_ids"]}})
        dbx.listing_interest.delete_many({"listing_id": {"$in": z["listing_ids"]}})
        dbx.activity_logs.delete_many({"$or": [{"user_id": {"$in": ids}},
                                               {"ref": {"$in": ids}}]})


# ============================================================ Firmen
@http
def test_01_firma_ohne_email_zweimal_und_index(welt):
    dbx = _db()
    firmen = []
    for i in (1, 2):
        r = _post("/admin/users", {"email": "", "password": PW,
                                   "company_name": f"KA Firma {i} {SUF}",
                                   "contact_person": "Kai Anlage", "phone": "0511 22",
                                   "plan_type": "none"}, welt["S"])
        assert r.status_code == 200, r.text[:300]
        d = r.json()
        welt["user_ids"].append(d["user_id"])
        welt["dealer_ids"].append(d["dealer_id"])
        assert d["kontonummer"] == str(d["kunden_nr"])
        chef = dbx.users.find_one({"id": d["user_id"]})
        assert "email" not in chef, "ohne Angabe fehlt das Feld (nie '')"
        assert chef["kontonummer"] == d["kontonummer"] and chef["current_session_id"] is None
        firma = dbx.dealers.find_one({"id": d["dealer_id"]})
        assert firma["email"] == "" and firma["contact_person"] == "Kai Anlage"
        assert firma["phone"] == "0511 22"
        firmen.append(d)
    assert firmen[0]["kontonummer"] != firmen[1]["kontonummer"]
    welt["firma"], welt["firma2"] = firmen
    for coll in (dbx.users, dbx.driver_accounts):
        info = coll.index_information()
        assert "email_1" not in info and "email_alt_eindeutig" not in info, (
            coll.name, sorted(info))
    # Anmeldung per Nummer (einmal je Konto — Single-Session)
    r = _post("/auth/login", {"kontonummer": firmen[0]["kontonummer"], "password": PW})
    assert r.status_code == 200, r.text[:200]
    welt["C"] = _kopf(r.json()["token"])
    # Zwei Firmen mit derselben Kontakt-E-Mail: Kontonummer (13.09.2026),
    # Schritt 5 — beide 200 mit verschiedenen Nummern (E-Mail nur Kontakt)
    mail = f"ka_kontakt_{SUF}@{MAIL}"
    nummern = []
    for adresse, name in ((mail, f"KA Kontakt {SUF}"), (mail.upper(), f"KA Kontakt 2 {SUF}")):
        r = _post("/admin/users", {"email": adresse, "password": PW,
                                   "company_name": name}, welt["S"])
        assert r.status_code == 200, r.text[:200]
        welt["user_ids"].append(r.json()["user_id"])
        welt["dealer_ids"].append(r.json()["dealer_id"])
        nummern.append(r.json()["kontonummer"])
    assert nummern[0] != nummern[1]
    assert dbx.users.count_documents({"email": mail}) == 2


# ============================================================ Sucher
@http
def test_02_sucher_ohne_email_zusatz_ohne_wiedervergabe(welt):
    dbx = _db()
    S, did, kn = welt["S"], welt["firma"]["dealer_id"], welt["firma"]["kunden_nr"]
    ids, nummern = [], []
    for vorname in ("Anna", "Ben"):
        r = _post(f"/admin/dealers/{did}/sucher",
                  {"email": "", "password": PW, "first_name": vorname, "last_name": "Such"}, S)
        assert r.status_code == 200, r.text[:200]
        ids.append(r.json()["sucher_id"])
        nummern.append(r.json()["kontonummer"])
    welt["user_ids"].extend(ids)
    assert nummern == [f"{kn}-1", f"{kn}-2"]
    assert "email" not in dbx.users.find_one({"id": ids[0]})
    r = requests.delete(f"{API}/admin/users/{ids[1]}", headers=S, timeout=30)
    assert r.status_code == 200, r.text[:200]
    r = _post(f"/admin/dealers/{did}/sucher", {"password": PW, "first_name": "Cem"}, S)
    assert r.status_code == 200, r.text[:200]
    welt["user_ids"].append(r.json()["sucher_id"])
    assert r.json()["kontonummer"] == f"{kn}-3", "Zusatz eines geloeschten Suchers nie neu"
    # Anmeldung per Nummer (Variante mit Leerzeichen)
    r = _post("/auth/login", {"kontonummer": f"{kn} 1", "password": PW})
    assert r.status_code == 200, r.text[:200]
    welt["SU"] = _kopf(r.json()["token"])
    # unbekannte Firma 404, Firma in Loeschung 409 — ohne Nummer zu ziehen
    assert _post("/admin/dealers/gibtesnicht/sucher", {"password": PW}, S).status_code == 404
    d2 = welt["firma2"]["dealer_id"]
    dbx.dealers.update_one({"id": d2}, {"$set": {"loeschung": {"status": "laeuft"}}})
    try:
        r = _post(f"/admin/dealers/{d2}/sucher", {"password": PW}, S)
        assert r.status_code == 409, r.text[:200]
        assert dbx.dealers.find_one({"id": d2}).get("sucher_seq") is None
    finally:
        dbx.dealers.update_one({"id": d2}, {"$unset": {"loeschung": ""}})


# ============================================================ Kaeufer
@http
def test_03_kaeufer_anlage_und_anfrage_geschlossen(welt):
    dbx = _db()
    S = welt["S"]
    basis = {"company_name": f"KA Kaeufer {SUF}", "contact_name": "Kim Kauf", "password": PW}
    assert _post("/admin/buyers", basis, S).status_code == 400, "B2B-Nachweis ist Pflicht"
    r = _post("/admin/buyers", {**basis, "b2b_nachweis": True, "ust_id": "DE0123"}, S)
    assert r.status_code == 422, r.text[:200]
    r = _post("/admin/buyers", {**basis, "b2b_nachweis": True, "ust_id": "de 123456789"}, S)
    assert r.status_code == 200, r.text[:300]
    k = r.json()
    welt["user_ids"].append(k["user_id"])
    assert "token" not in k and k["kontonummer"]
    doc = dbx.users.find_one({"id": k["user_id"]})
    assert doc["role"] == "b2b_buyer" and doc["dealer_id"] is None
    assert "email" not in doc and doc["current_session_id"] is None
    assert doc["ust_id"] == "DE123456789" and doc["gewerblich_bestaetigt_durch"] == "betreiber"
    # Kaeufer-Code (14.09.2026): kein Wert aus der Reihe; Login auch klein geschrieben
    from kontonummer import KAEUFER_MUSTER
    assert KAEUFER_MUSTER.match(k["kontonummer"]) and "kontonummer_basis" not in doc
    r = _post("/buyer/login", {"kontonummer": k["kontonummer"].lower(), "password": PW})
    assert r.status_code == 200, r.text[:200]
    welt["K"] = _kopf(r.json()["token"])
    welt["kaeufer"] = k

    # Zugangs-Anfrage art=kaeufer
    mail = f"ka_anfrage_{SUF}@{MAIL}"
    welt["anfrage_mails"].append(mail)
    anfrage = {"art": "kaeufer", "company_name": f"KA Anfrage {SUF}",
               "contact_person": "Ada Kauf", "email": mail, "ust_id": "DE123456789"}
    assert _post("/zugang-anfrage", anfrage).status_code == 400, "gewerblich_bestaetigt Pflicht"
    r = _post("/zugang-anfrage", {**anfrage, "gewerblich_bestaetigt": True, "ust_id": "DE0123"})
    assert r.status_code == 422, r.text[:200]
    r = _post("/zugang-anfrage", {**anfrage, "gewerblich_bestaetigt": True})
    assert r.status_code == 200, r.text[:200]
    req = dbx.plan_requests.find_one({"contact_email": mail})
    assert req["type"] == "zugang" and req["art"] == "kaeufer" and req["status"] == "offen"
    assert req["ust_id"] == "DE123456789" and req.get("gewerblich_bestaetigt_am")
    # Pruefung 14.09.2026 (E10): Die Bestaetigung (Unternehmer, AGB) ist fuer
    # JEDE Art Pflicht — ohne sie entsteht keine Anfrage.
    for art, bestaetigt in (("fahrer", True), ("firma", True), ("firma", False)):
        m = f"ka_agb_{art}_{int(bestaetigt)}_{SUF}@{MAIL}"
        welt["anfrage_mails"].append(m)
        r = _post("/zugang-anfrage", {"art": art, "company_name": f"KA AGB {SUF}",
                                      "contact_person": "Ada Agb", "email": m,
                                      "gewerblich_bestaetigt": bestaetigt})
        if not bestaetigt:
            assert r.status_code == 400, r.text[:200]
            assert dbx.plan_requests.find_one({"contact_email": m}) is None
            continue
        assert r.status_code == 200, r.text[:200]
        d = dbx.plan_requests.find_one({"contact_email": m})
        assert d["art"] == art and "ust_id" not in d
        assert d.get("gewerblich_bestaetigt_am"), (art, bestaetigt)
    # Anfrage passt nicht zur Kontoart / unbekannte Anfrage
    r = _post("/admin/drivers", {"display_name": f"KA Falsch {SUF}", "password": PW,
                                 "anfrage_id": req["id"]}, S)
    assert r.status_code == 400, r.text[:200]
    r = _post("/admin/buyers", {**basis, "b2b_nachweis": True, "anfrage_id": "gibtesnicht"}, S)
    assert r.status_code == 404, r.text[:200]
    neu = {"company_name": f"KA Anfrage {SUF}", "contact_name": "Ada Kauf",
           "email": mail, "password": PW, "b2b_nachweis": True, "anfrage_id": req["id"]}
    r = _post("/admin/buyers", neu, S)
    assert r.status_code == 200, r.text[:300]
    k2 = r.json()
    welt["user_ids"].append(k2["user_id"])
    req2 = dbx.plan_requests.find_one({"id": req["id"]})
    assert req2["status"] == "erledigt" and req2["angelegt_konto_id"] == k2["user_id"]
    assert req2["kontonummer"] == k2["kontonummer"] and "anlage_marke" not in req2
    u2 = dbx.users.find_one({"id": k2["user_id"]})
    assert u2["gewerblich_bestaetigt_durch"] == "anfrage"
    assert u2["gewerblich_bestaetigt_am"] == req["gewerblich_bestaetigt_am"]
    assert u2["ust_id"] == "DE123456789" and u2["email"] == mail
    # dieselbe Anfrage ein zweites Mal -> 409 (Anfrage ist schon erledigt)
    r = _post("/admin/buyers", {**neu, "email": ""}, S)
    assert r.status_code == 409, r.text[:200]
    assert dbx.users.count_documents({"company_name": f"KA Anfrage {SUF}"}) == 1


# ============================================================ Fahrer
@http
def test_04_fahrer_anlage_ohne_token_und_login(welt):
    dbx = _db()
    S = welt["S"]
    r = _post("/admin/drivers", {"display_name": f"KA Fahrer {SUF}", "password": PW,
                                 "email": ""}, S)
    assert r.status_code == 200, r.text[:300]
    f = r.json()
    welt["driver_ids"].append(f["driver_id"])
    assert "token" not in f and f["driver_code"].startswith("FD-") and f["kontonummer"]
    doc = dbx.driver_accounts.find_one({"id": f["driver_id"]})
    assert "email" not in doc and doc["current_session_id"] is None
    assert doc["kontonummer"] == f["kontonummer"] and doc["driver_code"] == f["driver_code"]
    assert _post("/admin/drivers", {"display_name": f"KA Schwach {SUF}",
                                    "password": "passwort1"}, S).status_code == 422
    r = _post("/driver/login", {"kontonummer": f["kontonummer"], "password": PW})
    assert r.status_code == 200, r.text[:200]
    welt["F"] = _kopf(r.json()["token"])
    welt["fahrer"] = f
    # gemeinsame Reihe: Fahrer-, Kaeufer- und Firmennummern verschieden
    assert len({f["kontonummer"], welt["kaeufer"]["kontonummer"],
                welt["firma"]["kontonummer"], welt["firma2"]["kontonummer"]}) == 4


# ============================================================ Rechte
@http
def test_05_anlage_nur_super_admin(welt):
    dbx = _db()
    from auth import create_token
    na_id, sid = f"t_ka_na_{SUF}", f"s-{SUF}"
    dbx.users.insert_one({"id": na_id, "role": "admin", "is_super_admin": False,
                          "active": True, "dealer_id": None, "password_hash": "x",
                          "current_session_id": sid, "created_at": _jetzt()})
    welt["user_ids"].append(na_id)
    N = _kopf(create_token(na_id, sid))
    did = welt["firma"]["dealer_id"]
    verboten = f"KA verboten {SUF}"
    versuche = [
        ("/admin/users", {"password": PW, "company_name": verboten}),
        (f"/admin/dealers/{did}/sucher", {"password": PW, "first_name": verboten}),
        ("/admin/buyers", {"company_name": verboten, "contact_name": "X Y",
                           "password": PW, "b2b_nachweis": True}),
        ("/admin/drivers", {"display_name": verboten, "password": PW}),
    ]
    for rolle, kopf in (("chef", welt["C"]), ("sucher", welt["SU"]), ("kaeufer", welt["K"]),
                        ("normaler_admin", N), ("fahrer", welt["F"])):
        for pfad, body in versuche:
            r = _post(pfad, body, kopf)
            # Ein Fahrer-Token ist kein Nutzer-Token: current_user kennt die
            # Konto-ID nicht -> 401 statt 403. Beides verweigert.
            erlaubt = (401, 403) if rolle == "fahrer" else (403,)
            assert r.status_code in erlaubt, (rolle, pfad, r.status_code, r.text[:120])
    assert dbx.users.count_documents({"$or": [{"company_name": verboten},
                                              {"first_name": verboten}]}) == 0
    assert dbx.driver_accounts.count_documents({"display_name": verboten}) == 0


# ============================================================ keine Nummern an Firmen
@http
def test_06_keine_fahrer_oder_kaeufernummer_an_firmen(welt):
    dbx = _db()
    C, did = welt["C"], welt["firma"]["dealer_id"]
    fahrer, kaeufer = welt["fahrer"], welt["kaeufer"]
    r = _post("/drivers/add", {"driver_code": fahrer["driver_code"]}, C)
    assert r.status_code == 200, r.text[:200]
    termin = f"ka_termin_{SUF}"
    welt["termin_ids"].append(termin)
    dbx.appointments.insert_one({"id": termin, "dealer_id": did,
                                 "driver_id": fahrer["driver_id"], "status": "offen",
                                 "pickup_date": "2099-01-01", "pickup_time": "10:00",
                                 "pickup_address": "Teststr. 1", "title": "KA Termin",
                                 "created_by": welt["firma"]["user_id"],
                                 "created_at": _jetzt(), "updated_at": _jetzt()})
    # Netzwerk: Kaeufer 1 mit Firmenname, Kaeufer 2 ganz ohne Namen
    r = _post("/admin/buyers", {"company_name": f"KA Namenlos {SUF}", "contact_name": "N N",
                                "email": f"ka_namenlos_{SUF}@{MAIL}", "password": PW,
                                "b2b_nachweis": True}, welt["S"])
    assert r.status_code == 200, r.text[:200]
    k2 = r.json()
    welt["user_ids"].append(k2["user_id"])
    dbx.users.update_one({"id": k2["user_id"]},
                         {"$unset": {"company_name": "", "contact_name": ""}})
    r = _post("/buyer/login", {"kontonummer": k2["kontonummer"], "password": PW})
    assert r.status_code == 200, r.text[:200]
    K2 = _kopf(r.json()["token"])
    listing = f"ka_listing_{SUF}"
    welt["listing_ids"].append(listing)
    dbx.resale_listings.insert_one({"id": listing, "dealer_id": did, "status": "veroeffentlicht",
                                    "visibility": "private", "title": "KA Golf",
                                    "price": 5000, "created_at": _jetzt(),
                                    "updated_at": _jetzt()})
    for kid in (kaeufer["user_id"], k2["user_id"]):
        dbx.network_members.insert_one({"dealer_id": did, "buyer_user_id": kid,
                                        "via_invite_id": f"ka_{SUF}", "created_at": _jetzt()})
    for kopf in (welt["K"], K2):
        r = _post(f"/marktplatz/listings/{listing}/interesse", {"offer": 4500, "message": "KA"},
                  kopf)
        assert r.status_code == 200, r.text[:200]

    # Fahrer-ID (14.09.2026): die Kontonummer des Fahrers IST der FD-Code, den
    # die Firma zum Verknuepfen kennt — er darf (und muss) in /drivers stehen.
    # Kaeufer-Codes bleiben tabu.
    nummern = {kaeufer["kontonummer"], k2["kontonummer"]}
    if fahrer["kontonummer"] != fahrer["driver_code"]:
        nummern.add(fahrer["kontonummer"])
    antworten = {}
    for pfad in ("/drivers", "/appointments", f"/appointments/{termin}",
                 "/dealer/network/members", "/dealer/interessen"):
        r = requests.get(f"{API}{pfad}", headers=C, timeout=60)
        assert r.status_code == 200, (pfad, r.text[:200])
        assert "kontonummer" not in r.text, pfad
        assert not nummern & {w for w in _werte(r.json()) if isinstance(w, str)}, pfad
        antworten[pfad] = r.json()
    assert any((e.get("driver_code") == fahrer["driver_code"]) for e in antworten["/drivers"])
    assert antworten[f"/appointments/{termin}"]["driver"]["driver_code"] == fahrer["driver_code"]
    mitglieder = {m["buyer_user_id"]: m for m in antworten["/dealer/network/members"]}
    assert mitglieder[kaeufer["user_id"]]["company_name"] == f"KA Kaeufer {SUF}"
    namen = {i["buyer_user_id"]: i["buyer_name"] for i in antworten["/dealer/interessen"]
             if i.get("listing_id") == listing}
    assert namen == {kaeufer["user_id"]: f"KA Kaeufer {SUF}",
                     k2["user_id"]: "Zwischenhändler"}, namen


# ============================================================ Handelnder, Betrieb
@http
def test_07_handelnder_ist_benutzername_und_betrieb_zaehlt(welt):
    S, did = welt["S"], welt["firma"]["dealer_id"]
    r = _post(f"/admin/dealers/{did}/zahlungen", {"amount": 10, "note": f"KA {SUF}"}, S)
    assert r.status_code == 200, r.text[:200]
    assert r.json()["zahlung"]["recorded_by"] == welt["sa_name"]
    b = requests.get(f"{API}/admin/betrieb", headers=S, timeout=60)
    assert b.status_code == 200, b.text[:200]
    ohne = b.json()["konten_ohne_nummer"]
    assert set(ohne) == {"users", "driver_accounts"}
    assert all(isinstance(v, int) for v in ohne.values())


# ============================================ Nachbesserung: Anfrage-Abschluss
def _anfrage_einfuegen(dbx, welt, art, name):
    """Offene Zugangs-Anfrage direkt in der DB (ohne Formular-Limiter)."""
    mail = f"ka_{name}_{SUF}@{MAIL}"
    welt["anfrage_mails"].append(mail)
    aid = f"ka_anfrage_{name}_{SUF}"
    dbx.plan_requests.insert_one({"id": aid, "type": "zugang", "art": art,
                                  "company_name": f"KA {name} {SUF}",
                                  "contact_person": "Ada Anfrage", "contact_email": mail,
                                  "status": "offen", "created_at": _jetzt()})
    return aid


def _erledigt(dbx, aid, konto_id, kontonummer):
    req = dbx.plan_requests.find_one({"id": aid})
    assert req["status"] == "erledigt", req
    assert req["angelegt_konto_id"] == konto_id and req["kontonummer"] == kontonummer, req
    assert "anlage_marke" not in req and "anlage_seit" not in req, req


@http
def test_08_anfrage_abschluss_firma_mit_und_ohne_abo_und_fahrer(welt):
    dbx = _db()
    S = welt["S"]
    # (a) Firma plan_type none und Firma mit Abo (trial)
    for plan in ("none", "trial"):
        aid = _anfrage_einfuegen(dbx, welt, "firma", f"firma_{plan}")
        # Runde 14: der Firmenname weicht bewusst von der Anfrage ab -> daten_geaendert
        r = _post("/admin/users", {"password": PW, "company_name": f"KA AnfrageFirma {plan} {SUF}",
                                   "plan_type": plan, "anfrage_id": aid, "daten_geaendert": True}, S)
        assert r.status_code == 200, (plan, r.text[:300])
        d = r.json()
        welt["user_ids"].append(d["user_id"])
        welt["dealer_ids"].append(d["dealer_id"])
        _erledigt(dbx, aid, d["user_id"], d["kontonummer"])
        abos = dbx.subscriptions.count_documents({"dealer_id": d["dealer_id"]})
        assert abos == (0 if plan == "none" else 1), (plan, abos)
        # zweiter Aufruf mit derselben Anfrage: 409, keine zweite Firma
        # Runde 14: der Firmenname weicht bewusst von der Anfrage ab -> daten_geaendert
        r = _post("/admin/users", {"password": PW, "company_name": f"KA AnfrageFirma {plan} {SUF}",
                                   "plan_type": plan, "anfrage_id": aid, "daten_geaendert": True}, S)
        assert r.status_code == 409, r.text[:200]
        assert dbx.dealers.count_documents(
            {"company_name": f"KA AnfrageFirma {plan} {SUF}"}) == 1
    # Firmen-Anfrage passt nicht zur Kaeufer-Anlage
    aid = _anfrage_einfuegen(dbx, welt, "firma", "firma_falsch")
    r = _post("/admin/buyers", {"company_name": f"KA Falsch {SUF}", "contact_name": "F F",
                                "password": PW, "b2b_nachweis": True, "anfrage_id": aid}, S)
    assert r.status_code == 400, r.text[:200]
    assert "anlage_marke" not in dbx.plan_requests.find_one({"id": aid})
    # (b) Fahrer
    aid = _anfrage_einfuegen(dbx, welt, "fahrer", "fahrer")
    r = _post("/admin/drivers", {"display_name": f"KA AnfrageFahrer {SUF}", "password": PW,
                                 "anfrage_id": aid}, S)
    assert r.status_code == 200, r.text[:300]
    f = r.json()
    welt["driver_ids"].append(f["driver_id"])
    _erledigt(dbx, aid, f["driver_id"], f["kontonummer"])


@http
def test_09_gleichzeitige_anlage_mit_derselben_anfrage(welt):
    """Doppelklick: drei gleichzeitige POST /admin/buyers mit derselben
    anfrage_id -> genau einmal 200, sonst 409, genau ein Konto."""
    import threading
    from concurrent.futures import ThreadPoolExecutor
    dbx = _db()
    aid = _anfrage_einfuegen(dbx, welt, "kaeufer", "parallel")
    name = f"KA Parallel {SUF}"
    body = {"company_name": name, "contact_name": "P P", "password": PW,
            "b2b_nachweis": True, "anfrage_id": aid}
    start = threading.Barrier(3)

    def anlegen(_):
        start.wait()
        return _post("/admin/buyers", body, welt["S"])

    with ThreadPoolExecutor(max_workers=3) as pool:
        antworten = list(pool.map(anlegen, range(3)))
    codes = sorted(r.status_code for r in antworten)
    ok = [r.json() for r in antworten if r.status_code == 200]
    welt["user_ids"].extend(k["user_id"] for k in ok)
    assert codes == [200, 409, 409], [(r.status_code, r.text[:120]) for r in antworten]
    assert dbx.users.count_documents({"company_name": name}) == 1
    _erledigt(dbx, aid, ok[0]["user_id"], ok[0]["kontonummer"])


class _DbMitKaputtemAbo:
    """Wie test_befunde_runde14_admin::test_50_49: nur der Abo-Insert scheitert."""

    class _KaputtesAbo:
        async def insert_one(self, *args, **kwargs):
            raise RuntimeError("simulierter DB-Ausfall")

    def __init__(self, echt):
        self._echt = echt

    @property
    def subscriptions(self):
        return self._KaputtesAbo()

    def __getattr__(self, name):
        return getattr(self._echt, name)

    def __getitem__(self, name):
        return self._echt[name]


@pytest.fixture
def wegwerf_db(monkeypatch):
    from motor.motor_asyncio import AsyncIOMotorClient
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    name = f"autoschnell_ka_{uuid.uuid4().hex[:10]}"
    db = client[name]
    monkeypatch.setattr(deps, "db", db)
    monkeypatch.setattr(ADMIN, "db", db)
    try:
        yield SimpleNamespace(db=db, run=loop.run_until_complete)
    finally:
        try:
            loop.run_until_complete(client.drop_database(name))
        finally:
            client.close()
            loop.close()


def test_abo_fehler_gibt_anfrage_frei(wegwerf_db, monkeypatch):
    """Scheitert der Abo-Insert, ist die Firma zurueckgerollt UND die
    Reservierung der Anfrage entfernt; ein zweiter Versuch schliesst sie."""
    from fastapi import HTTPException
    w = wegwerf_db
    db = w.db
    sa = {"id": "t_ka_sa", "role": "admin", "is_super_admin": True,
          "username": "t-ka-sa", "dealer_id": ""}

    def body():
        return ADMIN.AdminUserIn(password=PW, company_name="KA Abofehler",
                                 plan_type="monthly", anfrage_id="anf-abo")

    async def lauf():
        z = {}
        await db.plan_requests.insert_one({"id": "anf-abo", "type": "zugang", "art": "firma",
                                           "status": "offen", "created_at": _jetzt()})
        monkeypatch.setattr(ADMIN, "db", _DbMitKaputtemAbo(db))
        try:
            await ADMIN.admin_create_user(body(), admin=sa)
        except HTTPException as e:
            z["code"] = e.status_code
        finally:
            monkeypatch.setattr(ADMIN, "db", db)
        z["nach_fehler"] = await db.plan_requests.find_one({"id": "anf-abo"}, {"_id": 0})
        z["konten"] = await db.users.count_documents({})
        z["firmen"] = await db.dealers.count_documents({})
        z["erg"] = await ADMIN.admin_create_user(body(), admin=sa)
        z["nach_erfolg"] = await db.plan_requests.find_one({"id": "anf-abo"}, {"_id": 0})
        z["abos"] = await db.subscriptions.count_documents({"dealer_id": z["erg"]["dealer_id"]})
        return z

    z = w.run(lauf())
    assert z["code"] == 500
    assert z["konten"] == 0 and z["firmen"] == 0, "Rollback von Konto und Firma"
    assert z["nach_fehler"]["status"] == "offen", z["nach_fehler"]
    assert "anlage_marke" not in z["nach_fehler"] and "anlage_seit" not in z["nach_fehler"]
    req = z["nach_erfolg"]
    assert req["status"] == "erledigt" and req["angelegt_konto_id"] == z["erg"]["user_id"]
    assert req["kontonummer"] == z["erg"]["kontonummer"] and "anlage_marke" not in req
    assert z["abos"] == 1


# ============================================ in-process: Passwort hebt Sperre auf
def _fenster_abwarten(sekunden: int, puffer: float = 8.0) -> None:
    rest = sekunden - (time.time() % sekunden)
    if rest < puffer:
        time.sleep(rest + 0.2)


@pytest.fixture
def wegwerf_limiter(monkeypatch):
    from motor.motor_asyncio import AsyncIOMotorClient
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    s = uuid.uuid4().hex[:10]
    name = f"autoschnell_ka_{s}"
    db = client[name]
    monkeypatch.setattr(deps, "db", db)
    monkeypatch.setattr(ADMIN, "db", db)
    monkeypatch.setattr(RL, "_RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(RL, "_EXEMPT_LOOPBACK", False)
    monkeypatch.setattr(RL, "_LOGIN_KONTO_LIMIT", 30)
    monkeypatch.setattr(RL, "_KONTO_ALARM_SCHWELLE", 30)
    monkeypatch.setattr(RL.login_konto_limiter, "name", f"login-konto-ka{s}")
    _fenster_abwarten(RL.login_konto_limiter.window_seconds)
    try:
        yield SimpleNamespace(db=db, run=loop.run_until_complete, s=s)
    finally:
        try:
            loop.run_until_complete(client.drop_database(name))
        finally:
            client.close()
            loop.close()


def test_passwort_setzen_hebt_konto_sperre_auf(wegwerf_limiter):
    w = wegwerf_limiter
    db = w.db
    sa = {"id": "t_ka_sa", "role": "admin", "is_super_admin": True,
          "username": "t-ka-sa", "dealer_id": ""}
    fremd, andere = "198.51.100.41", "198.51.100.42"
    nummern = ("10091", "10091-1", "10092")

    async def lauf():
        z = {}
        basis = {"active": True, "password_hash": "x", "current_session_id": "alt",
                 "created_at": _jetzt()}
        await db.users.insert_many([
            {**basis, "id": "ku", "role": "dealer", "dealer_id": "dka",
             "kontonummer": "10091", "email": "chef@konto.test"},
            {**basis, "id": "ks", "role": "sucher", "dealer_id": "dka",
             "kontonummer": "10091-1"}])
        await db.driver_accounts.insert_one({**basis, "id": "kf", "kontonummer": "10092"})
        for nr in nummern:
            for _ in range(30):
                await RL.konto_fehlversuch(nr, fremd)
        z["vor"] = [await RL.konto_gesperrt(nr, andere, None) for nr in nummern]
        await ADMIN.admin_user_set_password(
            "ku", ADMIN.AdminUserPasswordIn(new_password=PW), admin=sa)
        # Runde 14: Passwoerter nur noch ueber den eigenen Endpunkt (PUT lehnt ab)
        await ADMIN.admin_user_set_password(
            "ks", ADMIN.AdminUserPasswordIn(new_password=PW), admin=sa)
        await ADMIN.admin_driver_set_password(
            "kf", ADMIN.AdminUserPasswordIn(new_password=PW), admin=sa)
        z["nach"] = [await RL.konto_gesperrt(nr, andere, None) for nr in nummern]
        z["sitzungen"] = ([(await db.users.find_one({"id": i}))["current_session_id"]
                           for i in ("ku", "ks")]
                          + [(await db.driver_accounts.find_one({"id": "kf"}))
                             ["current_session_id"]])
        z["audit"] = await db.activity_logs.find_one(
            {"action": "admin.passwort.zurueckgesetzt", "ref": "ku"}, {"_id": 0})
        z["audit_fahrer"] = await db.activity_logs.find_one(
            {"action": "admin.fahrer.passwort.zurueckgesetzt", "ref": "kf"}, {"_id": 0})
        return z

    z = w.run(lauf())
    assert z["vor"] == [True, True, True]
    assert z["nach"] == [False, False, False], "Passwort-Setzen muss die Sperre aufheben"
    assert z["sitzungen"] == [None, None, None]
    # 14.09.2026: das Audit haelt fest, ob wirklich eine Sperre bestand (hier ja)
    assert z["audit"]["meta"] == {"kontonummer": "10091", "sperre_aufgehoben": True}, z["audit"]
    assert z["audit_fahrer"]["meta"] == {"kontonummer": "10092",
                                         "sperre_aufgehoben": True}, z["audit_fahrer"]
