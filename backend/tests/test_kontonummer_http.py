# -*- coding: utf-8 -*-
"""Kontonummer (13.09.2026), Schritt 1/5 — Anmeldung per Nummer ueber HTTP.

  - /auth/login mit Feld kontonummer und per Alias email
  - Sucher-Nummer in Varianten ('10023 2', '10023/2', ' 10023-02 ')
  - /buyer/login und /driver/login per Nummer (driver: email als str, kein 422)
  - Nummer aus einem anderen Bereich -> 401
  - Super-Admin per Benutzername; username eines Nicht-Super-Admins -> 401
  - jedes neu angelegte Konto hat kontonummer + kontonummer_basis,
    Teil-Unique-Index kontonummer_eindeutig in users und driver_accounts
  Schritt 5 (alte Wege entfernt):
  - 410 fuer /auth/register, /buyer/register, /driver/register und beide
    Passwort-Reset-Routen (auch ohne Body); feste 403 fuer POST/PUT/DELETE
    /dealer/sucher
  - Anmeldung mit einer E-Mail-Adresse (Feld email oder kontonummer) -> 401
    mit 'Kontonummer oder Passwort falsch' in allen drei Masken
  - normaler Admin (is_super_admin False) -> 401 per E-Mail und per Benutzername
  - zwei Firmen (und Sucher, Fahrer, Kaeufer) mit derselben Kontakt-E-Mail -> 200
  - nach dem Start kein Unique-Index auf users.email bzw. driver_accounts.email

Braucht ein laufendes Backend auf TEST_BASE_URL (RUNDE14_HTTP=1) und dieselbe
DB (DB_NAME). Konten legt der Super-Admin an (POST /admin/users, /sucher,
/admin/buyers, /admin/drivers).
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
FALSCH = "Kontonummer oder Passwort falsch"


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


@pytest.fixture(scope="module")
def welt():
    dbx = _db()
    jetzt = datetime.now(timezone.utc).isoformat()
    z = {"user_ids": [], "dealer_ids": [], "driver_ids": []}
    sa_name = f"t-sa-{SUF}"
    sa_id = f"t_sa_{SUF}"
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
        kopf = _kopf(r.json()["token"])
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
        # Kaeufer und Fahrer ueber den Betreiber (Schritt 5: keine Selbstregistrierung)
        r = _post("/admin/buyers", {"company_name": f"KN Kaeufer {SUF}",
                                    "contact_name": "K M", "email": f"kn_kauf_{SUF}@{MAIL}",
                                    "password": PW, "b2b_nachweis": True}, kopf)
        assert r.status_code == 200, r.text[:200]
        z["kaeufer"] = {"id": r.json()["user_id"], "kontonummer": r.json()["kontonummer"]}
        z["user_ids"].append(z["kaeufer"]["id"])
        r = _post("/admin/drivers", {"email": f"kn_fahr_{SUF}@{MAIL}", "password": PW,
                                     "display_name": "KN Fahrer"}, kopf)
        assert r.status_code == 200, r.text[:200]
        z["fahrer"] = {"id": r.json()["driver_id"], "kontonummer": r.json()["kontonummer"]}
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


def _sa_kopf(welt):
    """Frische Super-Admin-Sitzung (Single-Session: fruehere Tests melden ihn
    ueber den Alias erneut an)."""
    r = _post("/auth/login", {"kontonummer": welt["sa_name"], "password": PW})
    assert r.status_code == 200, r.text[:200]
    return _kopf(r.json()["token"])


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
    assert f["kontonummer_basis"] == int(f["kontonummer"])
    from kontonummer import KAEUFER_MUSTER
    assert KAEUFER_MUSTER.match(k["kontonummer"]) and "kontonummer_basis" not in k
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
    assert r.status_code == 401 and r.json()["detail"] == FALSCH


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
                      headers=_kopf(r.json()["token"]))
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
                      headers=_kopf(r.json()["token"]))
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


def test_07_benutzername_und_email_nur_fuer_den_super_admin(welt):
    """Schritt 5: ein normaler Admin (is_super_admin False) kommt weder per
    Benutzername noch per E-Mail hinein — es gibt nur den Super-Admin."""
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
        for body in ({"kontonummer": k["username"]}, {"email": k["email"]},
                     {"kontonummer": k["email"]}):
            r = _post("/auth/login", {**body, "password": PW})
            assert r.status_code == 401, (k["role"], body, r.text[:200])
            assert r.json()["detail"] == FALSCH


def test_08_super_admin_per_benutzername_im_alias_feld(welt):
    r = _post("/auth/login", {"email": welt["sa_name"], "password": PW})
    assert r.status_code == 200, r.text[:200]
    assert r.json()["user"].get("is_super_admin") is True


# ==================================================== Schritt 5: alte Wege
def test_09_alte_anlagewege_410_und_chef_sucheranlage_403(welt):
    dbx = _db()
    mails = {k: f"kn_alt{k}_{SUF}@{MAIL}" for k in ("f", "k", "d")}
    faelle = (
        ("/auth/register", {"email": mails["f"], "password": PW, "company_name": "Alt F"}),
        ("/buyer/register", {"email": mails["k"], "password": PW, "company_name": "Alt K",
                             "contact_name": "A K", "gewerblich_bestaetigt": True}),
        ("/driver/register", {"email": mails["d"], "password": PW, "display_name": "Alt D"}),
        ("/auth/password-reset/request", {"email": f"kn_chef_{SUF}@{MAIL}"}),
        ("/auth/password-reset/confirm", {"token": "x" * 40, "new_password": PW}),
    )
    for pfad, body in faelle:
        for inhalt in (body, {}):
            r = _post(pfad, inhalt)
            assert r.status_code == 410, (pfad, inhalt, r.status_code, r.text[:200])
            assert "Betreiber" in r.json()["detail"], r.text[:200]
    assert dbx.users.count_documents({"email": {"$in": [mails["f"], mails["k"]]}}) == 0
    assert dbx.driver_accounts.count_documents({"email": mails["d"]}) == 0
    # Chef: Sucher anlegen, aendern, loeschen -> feste 403, Sucher bleibt
    r = _post("/auth/login", {"kontonummer": welt["firma"]["kontonummer"], "password": PW})
    assert r.status_code == 200, r.text[:200]
    chef = _kopf(r.json()["token"])
    sid = welt["sucher"]["sucher_id"]
    for methode, pfad, body in (
            ("POST", "/dealer/sucher", {"email": f"kn_neu_{SUF}@{MAIL}", "password": PW,
                                        "first_name": "N", "last_name": "S"}),
            ("PUT", f"/dealer/sucher/{sid}", {"active": False, "password": "Neu-Passwort-13x"}),
            ("DELETE", f"/dealer/sucher/{sid}", None)):
        r = requests.request(methode, f"{API}{pfad}", json=body, headers=chef, timeout=30)
        assert r.status_code == 403 and "Betreiber" in r.text, (methode, r.text[:200])
    s = dbx.users.find_one({"id": sid})
    assert s and s["active"] is True
    assert dbx.users.count_documents({"email": f"kn_neu_{SUF}@{MAIL}"}) == 0
    # Liste bleibt erreichbar
    r = requests.get(f"{API}/dealer/sucher", headers=chef, timeout=30)
    assert r.status_code == 200 and any(x["id"] == sid for x in r.json())


def test_10_anmeldung_per_email_401_in_allen_masken(welt):
    for pfad, mail in (("/auth/login", f"kn_chef_{SUF}@{MAIL}"),
                       ("/auth/login", f"kn_such_{SUF}@{MAIL}"),
                       ("/auth/login", f"t_sa_{SUF}@{MAIL}"),
                       ("/buyer/login", f"kn_kauf_{SUF}@{MAIL}"),
                       ("/driver/login", f"kn_fahr_{SUF}@{MAIL}")):
        for feld in ("email", "kontonummer"):
            r = _post(pfad, {feld: mail, "password": PW})
            assert r.status_code == 401, (pfad, feld, r.status_code, r.text[:200])
            assert r.json()["detail"] == FALSCH, r.text[:200]


def test_11_gleiche_kontakt_email_mehrfach_erlaubt(welt):
    dbx = _db()
    kopf = _sa_kopf(welt)
    mail = f"kn_gleich_{SUF}@{MAIL}"
    nummern = []
    for i, adresse in enumerate((mail, mail.upper())):
        r = _post("/admin/users", {"email": adresse, "password": PW, "plan_type": "none",
                                   "company_name": f"KN Gleich {i} {SUF}"}, kopf)
        assert r.status_code == 200, r.text[:200]
        welt["user_ids"].append(r.json()["user_id"])
        welt["dealer_ids"].append(r.json()["dealer_id"])
        nummern.append(r.json()["kontonummer"])
    r = _post(f"/admin/dealers/{welt['firma']['dealer_id']}/sucher",
              {"email": mail, "password": PW, "first_name": "G", "last_name": "S"}, kopf)
    assert r.status_code == 200, r.text[:200]
    welt["user_ids"].append(r.json()["sucher_id"])
    nummern.append(r.json()["kontonummer"])
    r = _post("/admin/drivers", {"email": mail, "password": PW, "display_name": "KN Gleich"},
              kopf)
    assert r.status_code == 200, r.text[:200]
    welt["driver_ids"].append(r.json()["driver_id"])
    nummern.append(r.json()["kontonummer"])
    r = _post("/admin/buyers", {"email": mail, "password": PW, "company_name": f"KN G {SUF}",
                                "contact_name": "G K", "b2b_nachweis": True}, kopf)
    assert r.status_code == 200, r.text[:200]
    welt["user_ids"].append(r.json()["user_id"])
    nummern.append(r.json()["kontonummer"])
    assert len(set(nummern)) == 5, nummern
    assert dbx.users.count_documents({"email": mail}) == 4
    assert dbx.driver_accounts.count_documents({"email": mail}) == 1
    # angemeldet wird nur per Nummer
    r = _post("/auth/login", {"kontonummer": nummern[1], "password": PW})
    assert r.status_code == 200, r.text[:200]
    r = _post("/auth/login", {"email": mail, "password": PW})
    assert r.status_code == 401


def test_12_kein_unique_index_auf_email(welt):
    dbx = _db()
    for coll in (dbx.users, dbx.driver_accounts):
        info = coll.index_information()
        assert "email_alt_eindeutig" not in info and "email_1" not in info, (coll.name, sorted(info))
        for name, idx in info.items():
            felder = [f for f, _ in idx["key"]]
            assert not (idx.get("unique") and felder == ["email"]), (coll.name, name, idx)
