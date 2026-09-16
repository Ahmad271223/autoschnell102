# -*- coding: utf-8 -*-
"""Go-Live 14.09.2026, Runde 6 (Ahmad: "teste komplett — alle Logins, alle
Anfragen, alle Super-Admin-Erstellungen der Passwoerter, ob das dann immer
klappt").

Durchgespielt wird der komplette Weg fuer jede Kontoart:
  Zugangs-Anfrage (Startseite) -> Super-Admin legt das Konto AUS der Anfrage
  an (20-Zeichen-Passwort) -> Anmeldung -> Passwort ueber JEDEN Weg neu setzen
  (POST /admin/users/{id}/password, PUT /admin/users/{id}, POST
  /admin/drivers/{id}/password, PUT /driver/password) -> Anmeldung mit dem
  neuen, Ablehnung mit dem alten -> Diagnose "Konto pruefen".
Dazu die Passwortregeln (zu kurz, zu lang, Allerweltswort) an jeder Stelle mit
klarer 4xx statt stillem Scheitern.

HTTP (RUNDE14_HTTP=1, laufendes Backend).
"""
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
import requests

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tests"))

import konten as K  # noqa: E402

HTTP = os.environ.get("RUNDE14_HTTP") == "1"
http = pytest.mark.skipif(not HTTP, reason="RUNDE14_HTTP=1 nicht gesetzt")
API = K.API
SUF = "f" + uuid.uuid4().hex[:7]   # nie nur Ziffern: SUF steckt im Passwort, ein reiner Zahlen-Suffix als Nachname/Firma haette es abgelehnt (CI 17.09.2026)
MAIL = "e2etest-mail.de"
PW = {k: f"{p}{SUF}Kq4Lm9Xw2"[:20] for k, p in
      (("a", "Aa7"), ("b", "Bb8"), ("c", "Cc9"), ("d", "Dd6"), ("e", "Ee5"), ("f", "Ff4"))}
assert all(len(v) == 20 for v in PW.values())


def _jetzt():
    return datetime.now(timezone.utc).isoformat()


def _kopf(token):
    return {"Authorization": f"Bearer {token}"}


def _get(pfad, headers=None, **kw):
    return requests.get(f"{API}{pfad}", headers=headers, timeout=60, **kw)


def _post(pfad, json=None, headers=None):
    return requests.post(f"{API}{pfad}", json=json, headers=headers, timeout=60)


def _put(pfad, json=None, headers=None):
    return requests.put(f"{API}{pfad}", json=json, headers=headers, timeout=60)


@pytest.fixture(scope="module")
def welt():
    if not HTTP:
        pytest.skip("RUNDE14_HTTP=1 nicht gesetzt")
    dbx = K._db()
    z = {"mails": [], "user_ids": [], "dealer_ids": [], "driver_ids": []}
    try:
        yield z
    finally:
        dbx.users.delete_many({"$or": [{"id": {"$in": z["user_ids"]}},
                                       {"dealer_id": {"$in": z["dealer_ids"]}},
                                       {"company_name": {"$regex": SUF}}]})
        dbx.dealers.delete_many({"$or": [{"id": {"$in": z["dealer_ids"]}},
                                         {"company_name": {"$regex": SUF}}]})
        dbx.driver_accounts.delete_many({"$or": [{"id": {"$in": z["driver_ids"]}},
                                                 {"display_name": {"$regex": SUF}}]})
        dbx.plan_requests.delete_many({"contact_email": {"$in": z["mails"]}})
        for coll in ("subscriptions", "manual_payments", "dealer_drivers", "network_members"):
            dbx[coll].delete_many({"dealer_id": {"$in": z["dealer_ids"]}})
        ids = z["user_ids"] + z["driver_ids"]
        dbx.activity_logs.delete_many({"$or": [{"user_id": {"$in": ids}}, {"ref": {"$in": ids}}]})


def _anfrage(welt, art, **extra):
    mail = f"r6_{art}_{uuid.uuid4().hex[:6]}@{MAIL}"
    welt["mails"].append(mail)
    body = {"art": art, "company_name": f"R6 {art} {SUF}", "contact_person": "Anna Anfrage",
            "email": mail, "phone": "0511 123", "gewerblich_bestaetigt": True, **extra}
    r = _post("/zugang-anfrage", body)
    assert r.status_code == 200, (art, r.status_code, r.text[:300])
    req = K._db().plan_requests.find_one({"contact_email": mail})
    assert req and req["status"] == "offen" and req["art"] == art, req
    return req


@http
def test_01_anfrage_bis_anmeldung_alle_kontoarten(welt):
    """Anfrage -> Konto aus der Anfrage -> Anmeldung: Firma, Zwischenhaendler, Fahrer."""
    S = K.super_kopf()
    dbx = K._db()
    from kontonummer import FAHRER_MUSTER, KAEUFER_MUSTER
    # Firma (Chef)
    req = _anfrage(welt, "firma")
    r = _post("/admin/users", {"company_name": f"R6 Firma {SUF}", "contact_person": "Chef",
                               "email": req["contact_email"], "password": PW["a"],
                               "phone": "", "plan_type": "none", "anfrage_id": req["id"]}, S)
    assert r.status_code == 200, r.text[:300]
    welt["chef_id"], welt["chef_nr"] = r.json()["user_id"], r.json()["kontonummer"]
    welt["user_ids"].append(welt["chef_id"])
    assert welt["chef_nr"].isdigit()
    assert dbx.plan_requests.find_one({"id": req["id"]})["status"] == "erledigt"
    r = K.anmelden(welt["chef_nr"], PW["a"], "auth")
    assert r.status_code == 200, r.text[:300]
    welt["C"] = _kopf(r.json()["token"])
    me = _get("/auth/me", welt["C"]).json()["user"]
    welt["dealer_id"] = me["dealer_id"]
    welt["dealer_ids"].append(me["dealer_id"])
    # dieselbe Anfrage kein zweites Mal
    r = _post("/admin/users", {"company_name": f"R6 Firma2 {SUF}", "contact_person": "Chef",
                               "email": "", "password": PW["a"], "phone": "",
                               "plan_type": "none", "anfrage_id": req["id"]}, S)
    assert r.status_code == 409, r.text[:200]

    # Sucher der Firma (kein Anfrage-Weg — legt der Chef ueber den Betreiber an)
    r = _post(f"/admin/dealers/{welt['dealer_id']}/sucher",
              {"password": PW["b"], "first_name": "Su", "last_name": SUF, "email": "", "phone": ""}, S)
    assert r.status_code == 200, r.text[:300]
    welt["sucher_id"], welt["sucher_nr"] = r.json()["sucher_id"], r.json()["kontonummer"]
    welt["user_ids"].append(welt["sucher_id"])
    assert welt["sucher_nr"] == f"{welt['chef_nr']}-1"
    assert K.anmelden(welt["sucher_nr"], PW["b"], "auth").status_code == 200

    # Zwischenhaendler aus Anfrage (B2B-Nachweis)
    req = _anfrage(welt, "kaeufer", ust_id="DE123456789")
    r = _post("/admin/buyers", {"company_name": f"R6 Kaeufer {SUF}", "contact_name": "Kai",
                                "phone": "", "email": req["contact_email"], "ust_id": "",
                                "password": PW["c"], "b2b_nachweis": True,
                                "anfrage_id": req["id"]}, S)
    assert r.status_code == 200, r.text[:300]
    welt["kaeufer_id"], welt["kaeufer_nr"] = r.json()["user_id"], r.json()["kontonummer"]
    welt["user_ids"].append(welt["kaeufer_id"])
    assert KAEUFER_MUSTER.match(welt["kaeufer_nr"]), welt["kaeufer_nr"]
    assert dbx.plan_requests.find_one({"id": req["id"]})["status"] == "erledigt"
    assert K.anmelden(welt["kaeufer_nr"], PW["c"], "buyer").status_code == 200
    assert K.anmelden(welt["kaeufer_nr"].lower(), PW["c"], "auth").status_code == 200

    # Fahrer aus Anfrage
    req = _anfrage(welt, "fahrer")
    r = _post("/admin/drivers", {"display_name": f"R6 Fahrer {SUF}", "password": PW["d"],
                                 "email": req["contact_email"], "phone": "",
                                 "anfrage_id": req["id"]}, S)
    assert r.status_code == 200, r.text[:300]
    welt["fahrer_id"], welt["fahrer_nr"] = r.json()["driver_id"], r.json()["kontonummer"]
    welt["driver_ids"].append(welt["fahrer_id"])
    assert FAHRER_MUSTER.match(welt["fahrer_nr"]) and r.json()["driver_code"] == welt["fahrer_nr"]
    assert dbx.plan_requests.find_one({"id": req["id"]})["status"] == "erledigt"
    r = K.anmelden(welt["fahrer_nr"], PW["d"], "driver")
    assert r.status_code == 200, r.text[:300]
    welt["F"] = _kopf(r.json()["token"])
    # Anfrage der falschen Kontoart -> 400, unbekannt -> 404
    req2 = _anfrage(welt, "fahrer")
    r = _post("/admin/buyers", {"company_name": f"R6 Falsch {SUF}", "contact_name": "Kai",
                                "phone": "", "email": "", "ust_id": "", "password": PW["c"],
                                "b2b_nachweis": True, "anfrage_id": req2["id"]}, S)
    assert r.status_code == 400, r.text[:200]
    r = _post("/admin/drivers", {"display_name": f"R6 Nix {SUF}", "password": PW["d"],
                                 "email": "", "phone": "", "anfrage_id": "gibtsnicht"}, S)
    assert r.status_code == 404, r.text[:200]


@http
def test_02_passwort_neu_setzen_alle_wege(welt):
    S = K.super_kopf()
    # Chef: POST /admin/users/{id}/password
    r = _post(f"/admin/users/{welt['chef_id']}/password", {"new_password": PW["e"]}, S)
    assert r.status_code == 200 and r.json()["sperre_aufgehoben"] is False, r.text[:200]
    assert K.anmelden(welt["chef_nr"], PW["a"], "auth").status_code == 401
    r = K.anmelden(welt["chef_nr"], PW["e"], "auth")
    assert r.status_code == 200, r.text[:200]
    welt["C"] = _kopf(r.json()["token"])
    # Runde 14: PUT setzt keine Passwoerter mehr — zweiter Wechsel ebenfalls per POST
    r = _put(f"/admin/users/{welt['chef_id']}", {"password": PW["f"]}, S)
    assert r.status_code == 400, r.text[:200]
    r = _post(f"/admin/users/{welt['chef_id']}/password", {"new_password": PW["f"]}, S)
    assert r.status_code == 200, r.text[:200]
    assert K.anmelden(welt["chef_nr"], PW["e"], "auth").status_code == 401
    r = K.anmelden(welt["chef_nr"], PW["f"], "auth")
    assert r.status_code == 200, r.text[:200]
    welt["C"] = _kopf(r.json()["token"])
    # Sucher: POST + PUT
    r = _post(f"/admin/users/{welt['sucher_id']}/password", {"new_password": PW["e"]}, S)
    assert r.status_code == 200, r.text[:200]
    assert K.anmelden(welt["sucher_nr"], PW["e"], "auth").status_code == 200
    r = _post(f"/admin/users/{welt['sucher_id']}/password", {"new_password": PW["f"]}, S)
    assert r.status_code == 200, r.text[:200]
    assert K.anmelden(welt["sucher_nr"], PW["e"], "auth").status_code == 401
    assert K.anmelden(f"{welt['sucher_nr'].replace('-', ' ')}", PW["f"], "auth").status_code == 200
    # Zwischenhaendler: POST -> Marktplatz UND /login
    r = _post(f"/admin/users/{welt['kaeufer_id']}/password", {"new_password": PW["e"]}, S)
    assert r.status_code == 200, r.text[:200]
    assert K.anmelden(welt["kaeufer_nr"], PW["c"], "buyer").status_code == 401
    assert K.anmelden(welt["kaeufer_nr"].lower(), PW["e"], "buyer").status_code == 200
    assert K.anmelden(welt["kaeufer_nr"], PW["e"], "auth").status_code == 200
    # Fahrer: Betreiber setzt neu, danach wechselt der Fahrer selbst
    r = _post(f"/admin/drivers/{welt['fahrer_id']}/password", {"new_password": PW["e"]}, S)
    assert r.status_code == 200 and r.json()["sperre_aufgehoben"] is False, r.text[:200]
    assert K.anmelden(welt["fahrer_nr"], PW["d"], "driver").status_code == 401
    r = K.anmelden(welt["fahrer_nr"].lower(), PW["e"], "driver")
    assert r.status_code == 200, r.text[:200]
    F = _kopf(r.json()["token"])
    r = _put("/driver/password", {"current_password": PW["e"], "new_password": PW["f"]}, F)
    assert r.status_code == 200, r.text[:200]
    assert K.anmelden(welt["fahrer_nr"], PW["e"], "driver").status_code == 401
    r = K.anmelden(welt["fahrer_nr"], PW["f"], "driver")
    assert r.status_code == 200, r.text[:200]
    welt["F"] = _kopf(r.json()["token"])
    welt["pw_aktuell"] = PW["f"]


@http
def test_03_passwortregeln_ueberall_klar(welt):
    """Zu kurz (9), zu lang (>72 Bytes), Allerweltswort: ueberall 4xx mit Text,
    nie 200 mit einem Passwort, das spaeter nicht funktioniert."""
    S = K.super_kopf()
    schlecht = ("Kurz9!abc", "x" * 73 + "1", "autoschnell2026!", "aaaaaaaaaa1")
    ziele = (
        ("POST", "/admin/users", {"company_name": f"R6 Regel {SUF}", "contact_person": "R",
                                  "email": "", "phone": "", "plan_type": "none"}, "password"),
        ("POST", f"/admin/dealers/{welt['dealer_id']}/sucher",
         {"first_name": "R", "last_name": "Regel", "email": "", "phone": ""}, "password"),
        ("POST", "/admin/buyers", {"company_name": f"R6 Regel K {SUF}", "contact_name": "R",
                                   "phone": "", "email": "", "ust_id": "", "b2b_nachweis": True},
         "password"),
        ("POST", "/admin/drivers", {"display_name": f"R6 Regel F {SUF}", "email": "", "phone": ""},
         "password"),
        ("POST", f"/admin/users/{welt['chef_id']}/password", {}, "new_password"),
        ("POST", f"/admin/drivers/{welt['fahrer_id']}/password", {}, "new_password"),
    )
    # Runde 14: PUT /admin/users/{id} setzt keine Passwoerter mehr (400, eigener Endpunkt)
    r = _put(f"/admin/users/{welt['chef_id']}", {"password": PW["a"]}, S)
    assert r.status_code == 400 and "/admin/me/password" in r.text, r.text[:200]
    for methode, pfad, basis, feld in ziele:
        for pw in schlecht:
            body = {**basis, feld: pw}
            r = (_post if methode == "POST" else _put)(pfad, body, S)
            assert 400 <= r.status_code < 500, (pfad, pw, r.status_code, r.text[:200])
            assert "Passwort" in r.text or "passwort" in r.text.lower(), (pfad, pw, r.text[:200])
    # Fahrer selbst: falsches aktuelles Passwort -> 4xx, Konto bleibt anmeldbar
    r = _put("/driver/password", {"current_password": "Falsch12345!", "new_password": PW["a"]},
             welt["F"])
    assert 400 <= r.status_code < 500, r.text[:200]
    r = K.anmelden(welt["fahrer_nr"], welt["pw_aktuell"], "driver")
    assert r.status_code == 200, r.text[:200]
    welt["F"] = _kopf(r.json()["token"])
    # Ein Passwort mit Leerzeichen am Ende ist erlaubt und muss dann GENAU so
    # eingegeben werden (Server schneidet nichts ab).
    mit_leer = f"Ll3{SUF}Kq4Lm9X "
    r = _post(f"/admin/users/{welt['sucher_id']}/password", {"new_password": mit_leer}, S)
    assert r.status_code == 200, r.text[:200]
    assert K.anmelden(welt["sucher_nr"], mit_leer, "auth").status_code == 200
    assert K.anmelden(welt["sucher_nr"], mit_leer.strip(), "auth").status_code == 401


@http
def test_04_diagnose_und_falsche_tuer(welt):
    S = K.super_kopf()
    erwartet = {welt["chef_nr"]: "firma", welt["sucher_nr"]: "sucher",
                welt["kaeufer_nr"].lower(): "kaeufer", welt["fahrer_nr"].lower(): "fahrer"}
    for kennung, art in erwartet.items():
        d = _get("/admin/konten/pruefen", S, params={"kennung": kennung}).json()
        assert d["gefunden"] and d["art"] == art and d["aktiv"] and d["passwort_gesetzt"], (kennung, d)
        assert d["anmeldesperre"] is False
    d = _get("/admin/konten/pruefen", S, params={"kennung": "FD-ABCDEFGH"}).json()
    assert d["gefunden"] is False and "Kein Konto" in d["hinweis"]
    # Jede Kennung nur an ihrer Tuer — sonst dieselbe 401 wie ein Tippfehler
    pw = welt["pw_aktuell"]
    for kennung, weg in ((welt["fahrer_nr"], "auth"), (welt["fahrer_nr"], "buyer"),
                         (welt["kaeufer_nr"], "driver"), (welt["chef_nr"], "driver"),
                         (welt["chef_nr"], "buyer"), (welt["sucher_nr"], "driver")):
        r = K.anmelden(kennung, pw, weg)
        assert r.status_code == 401, (kennung, weg, r.status_code, r.text[:200])
