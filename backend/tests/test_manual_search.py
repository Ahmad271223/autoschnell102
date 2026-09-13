# -*- coding: utf-8 -*-
"""Manuelle Suche des Firmenchefs (Runde 10, 09/2026).

Vorher: keine Eingabegrenzen, unbekannte Marke/Modell/Kraftstoff fuehrten
still zu einer viel breiteren Suche, kW/PS konnten sich widersprechen,
Toleranz 0 wurde zu 10, kein Limit je Konto, kein Protokoll, und das
komplette interne Regelpaket wurde mitgeliefert.

HTTP-Teile brauchen das Backend auf TEST_BASE_URL.
"""
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BASE = (os.environ.get("TEST_BASE_URL") or "http://localhost:8001").rstrip("/")
API = f"{BASE}/api"
MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
DB_NAME = os.environ.get("DB_NAME") or "autoschnell"
SUF = uuid.uuid4().hex[:8]
PW = "SucheTest123!"


def _db():
    from pymongo import MongoClient
    return MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)[DB_NAME]


@pytest.fixture(scope="module")
def chef():
    r = requests.post(f"{API}/auth/register", json={
        "email": f"suche_{SUF}@e2etest-mail.de", "password": PW,
        "company_name": "Suche GmbH", "contact_person": "S U", "phone": "0511 7"}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    h = {"Authorization": f"Bearer {r.json()['token']}"}
    me = requests.get(f"{API}/auth/me", headers=h, timeout=30).json()["user"]
    _db().subscriptions.insert_one({
        "id": str(uuid.uuid4()), "dealer_id": me["dealer_id"], "subject_user_id": me["id"],
        "plan": "monthly", "status": "active",
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat()})
    yield {"h": h, "me": me}
    dbx = _db()
    for c in ("subscriptions", "activity_logs"):
        dbx[c].delete_many({"dealer_id": me["dealer_id"]})
    dbx.users.delete_many({"id": me["id"]})
    dbx.dealers.delete_many({"id": me["dealer_id"]})


def _suche(chef, **js):
    return requests.post(f"{API}/manual/search", headers=chef["h"], json=js, timeout=30)


def _q(url):
    return parse_qs(urlparse(url).query)


# ---------------------------------------------------------------- Einheitentests
def test_kraftstoff_und_getriebe_exakt_statt_teilstring():
    import routes.manual_search as m
    assert m._fuel_code("Diesel") == "DIESEL"
    assert m._fuel_code("Plug-in-Hybrid") == "HYBRID"
    assert m._fuel_code("Dieselll") == ""
    assert m._fuel_code("xdieselx") == ""
    assert m._gear_code("Automatik") == "AUTOMATIC_GEAR"
    assert m._gear_code("Halbautomatik") == ""


def test_autoscout_kennt_plug_in_hybrid():
    import autoscout_service as a
    assert a._autoscout_fuel("Plug-in-Hybrid") == "2"
    assert a._autoscout_fuel("Diesel") == "D"


# ---------------------------------------------------------------- HTTP: Eingabegrenzen
@pytest.mark.parametrize("js,stichwort", [
    ({"make": ""}, "make"),
    ({"make": "BMW", "km_min": -5}, "km_min"),
    ({"make": "BMW", "km_min": 90000, "km_max": 10000}, "min"),
    ({"make": "BMW", "ez_from": 2020, "ez_to": 2015}, "von"),
    ({"make": "BMW", "ez_from": 1500}, "ez_from"),
    ({"make": "BMW", "ez_to": 9999}, "ez_to"),
    ({"make": "BMW", "kw": 999999999}, "kw"),
    ({"make": "BMW", "kw_tolerance": -50}, "kw_tolerance"),
    ({"make": "BMW", "kw": 100, "ps": 500}, "passen nicht"),
    ({"make": "BMW", "fuel": "Dieselll"}, "Kraftstoff unbekannt"),
    ({"make": "BMW", "gearbox": "Halbautomatik"}, "Getriebe unbekannt"),
])
def test_unplausible_eingaben_werden_abgelehnt(chef, js, stichwort):
    r = _suche(chef, **js)
    assert r.status_code == 422, r.text[:200]
    assert stichwort.lower() in r.text.lower(), r.text[:300]


def test_unbekannte_marke_und_modell_sind_fehler_keine_breitensuche(chef):
    r = _suche(chef, make="Gibtsnicht")
    assert r.status_code == 400 and "Marke unbekannt" in r.text
    r = _suche(chef, make="BMW", model="M999XYZ")
    assert r.status_code == 400 and "Modell unbekannt" in r.text


# ---------------------------------------------------------------- HTTP: Ergebnis
def test_gute_suche_liefert_beide_portale_und_aufloesung(chef):
    r = _suche(chef, make="BMW", model="M3", fuel="Diesel", gearbox="Automatik",
               ez_from=2018, ez_to=2022, km_max=80000, kw=250)
    assert r.status_code == 200, r.text[:300]
    d = r.json()
    assert d["mobile_url"].startswith("https://") and d["autoscout_url"].startswith("https://")
    assert "rules_applied" not in d, "internes Regelpaket wird weiterhin mitgeliefert"
    assert d["aufgeloest"]["autoscout"] == {"make": "BMW", "model": "M3"}
    assert d["aufgeloest"]["mobile"]["make"] is True
    assert set(d["haendlerregeln_ersetzt"]) >= {"first_registration", "mileage", "power", "fuel", "gearbox"}
    q = _q(d["autoscout_url"])
    assert q.get("fuel") == ["D"], q.get("fuel")
    assert q.get("gear") == ["A"], q.get("gear")


def test_plug_in_hybrid_bekommt_bei_autoscout_einen_kraftstoff(chef):
    r = _suche(chef, make="BMW", fuel="Plug-in-Hybrid")
    assert r.status_code == 200, r.text[:300]
    q = _q(r.json()["autoscout_url"])
    assert q.get("fuel") == ["2"], q


def test_toleranz_null_bleibt_null(chef):
    r0 = _suche(chef, make="BMW", kw=100, kw_tolerance=0)
    r10 = _suche(chef, make="BMW", kw=100, kw_tolerance=10)
    assert r0.status_code == 200 and r10.status_code == 200
    assert r0.json()["mobile_url"] != r10.json()["mobile_url"], "Toleranz 0 wurde still zu 10"


def test_suche_wird_protokolliert(chef):
    vorher = _db().activity_logs.count_documents({"dealer_id": chef["me"]["dealer_id"], "action": "suche.manuell"})
    assert _suche(chef, make="Audi", model="A4").status_code == 200
    nachher = _db().activity_logs.count_documents({"dealer_id": chef["me"]["dealer_id"], "action": "suche.manuell"})
    assert nachher == vorher + 1


def test_katalog_fuer_firmen_und_zwischenhaendler_nicht_fuer_admins(chef):
    """Ohne Anmeldung 401; Firma 200; Zwischenhaendler 200 (Marktplatz-Filter,
    Nachpruefung Runde 10); Admin 403."""
    assert requests.get(f"{API}/manual/makes", timeout=30).status_code == 401
    assert requests.get(f"{API}/manual/makes", headers=chef["h"], timeout=30).status_code == 200
    from auth import create_token
    dbx = _db()
    konten = []
    try:
        for rolle, erwartet in (("b2b_buyer", 200), ("admin", 403)):
            uid = f"katalog_{rolle}_{SUF}"
            dbx.users.insert_one({"id": uid, "email": f"{uid}@e2etest-mail.de", "role": rolle,
                                  "active": True, "current_session_id": f"s-{uid}",
                                  "password_hash": "x", "gewerblich_bestaetigt": True,
                                  "created_at": datetime.now(timezone.utc).isoformat()})
            konten.append(uid)
            h = {"Authorization": f"Bearer {create_token(uid, f's-{uid}')}"}
            r = requests.get(f"{API}/manual/makes", headers=h, timeout=30)
            assert r.status_code == erwartet, (rolle, r.status_code, r.text[:200])
    finally:
        dbx.users.delete_many({"id": {"$in": konten}})


def test_doppel_labels_der_oberflaeche_werden_verstanden(chef):
    """Nachpruefung Runde 10: "LPG / Autogas" und "CNG / Erdgas" (Oberflaeche)
    wurden mit 422 abgelehnt; "Super"/"Strom" liefen bei AutoScout still ohne
    Kraftstoff-Filter."""
    import routes.manual_search as m
    assert m._fuel_code("LPG / Autogas") == "LPG" and m._fuel_code("CNG / Erdgas") == "CNG"
    for label, as_code in (("LPG / Autogas", "L"), ("CNG / Erdgas", "C"), ("Super", "B"),
                           ("Strom", "E"), ("Elektro", "E"), ("Wasserstoff", "H")):
        r = _suche(chef, make="BMW", fuel=label)
        assert r.status_code == 200, (label, r.text[:200])
        assert _q(r.json()["autoscout_url"]).get("fuel") == [as_code], (label, r.json()["autoscout_url"])
        assert not [h for h in r.json()["hinweise"] if "Kraftstoff" in h], r.json()["hinweise"]


def test_suche_ist_je_konto_begrenzt():
    import routes.manual_search as m
    assert m.suche_limiter.max_attempts == 60 and m.suche_limiter.window_seconds == 60
    import inspect
    assert "suche_limiter.check" in inspect.getsource(m.manual_search)


# ---------------------------------------------------------------- Nachpruefung: km=0, Zugang, nur Marke, Katalog
def test_km_max_null_bleibt_filter_mobile_und_autoscout():
    from mobile_service import build_search_url as mo, DEFAULT_RULES
    from autoscout_service import build_search_url as ac
    veh = {"make": "BMW", "make_label": "BMW", "model": "", "model_label": "",
           "mileage": 0, "first_registration": None, "power_kw": None, "fuel": "", "gearbox": ""}
    rules = {**DEFAULT_RULES, "mileage": {"mode": "custom", "min": 0, "max": 0}}
    assert _q(mo(veh, rules)).get("ml") == ["0:0"]
    qa = _q(ac(veh, rules))
    assert qa.get("kmfrom") == ["0"] and qa.get("kmto") == ["0"]
    rules = {**DEFAULT_RULES, "mileage": {"mode": "custom", "min": 10000, "max": 20000}}
    veh["mileage"] = None
    assert _q(mo(veh, rules)).get("ml") == ["10000:20000"], "fester Bereich haengt nicht am Fahrzeug-km"


def test_http_km_max_null_erzeugt_kilometerfilter(chef):
    r = _suche(chef, make="BMW", km_max=0)
    assert r.status_code == 200, r.text[:200]
    d = r.json()
    assert _q(d["mobile_url"]).get("ml") == ["0:0"], d["mobile_url"]
    assert _q(d["autoscout_url"]).get("kmto") == ["0"], d["autoscout_url"]
    r = _suche(chef, make="BMW", km_min=0, km_max=50000)
    assert _q(r.json()["mobile_url"]).get("ml") == ["0:50000"]


def test_suche_ohne_abo_402_katalog_bleibt_offen(chef):
    """require_active_sub: ohne aktives Abo 402; der Katalog haengt nur an
    der Rolle, nicht am Abo."""
    subs = _db().subscriptions
    abo = subs.find_one_and_delete({"dealer_id": chef["me"]["dealer_id"],
                                    "subject_user_id": chef["me"]["id"]})
    assert abo, "Fixture-Abo fehlt"
    try:
        r = _suche(chef, make="BMW")
        assert r.status_code == 402, r.text[:200]
        assert requests.get(f"{API}/manual/makes", headers=chef["h"], timeout=30).status_code == 200
    finally:
        subs.insert_one(abo)
    assert _suche(chef, make="BMW").status_code == 200


def test_suche_nur_fuer_chef_und_sucher_auch_mit_abo(chef):
    """Rollenpruefung steht VOR der Abo-Pruefung: Zwischenhaendler und Admins
    bekommen 403, selbst mit Abo-Dokument."""
    from auth import create_token
    dbx = _db()
    konten = []
    try:
        for rolle in ("b2b_buyer", "admin"):
            uid = f"suche_{rolle}_{SUF}"
            dbx.users.insert_one({"id": uid, "email": f"{uid}@e2etest-mail.de", "role": rolle,
                                  "active": True, "current_session_id": f"s-{uid}",
                                  "password_hash": "x", "gewerblich_bestaetigt": True,
                                  "is_super_admin": rolle == "admin",
                                  "created_at": datetime.now(timezone.utc).isoformat()})
            konten.append(uid)
            h = {"Authorization": f"Bearer {create_token(uid, f's-{uid}')}"}
            r = requests.post(f"{API}/manual/search", headers=h, json={"make": "BMW"}, timeout=30)
            assert r.status_code == 403, (rolle, r.status_code, r.text[:200])
    finally:
        dbx.users.delete_many({"id": {"$in": konten}})


def test_verwaister_dealer_403_statt_500(chef):
    dealers = _db().dealers
    doc = dealers.find_one_and_delete({"id": chef["me"]["dealer_id"]})
    assert doc, "Haendlerdokument der Fixture fehlt"
    try:
        r = _suche(chef, make="BMW")
        assert r.status_code == 403 and "Haendlerprofil" in r.text, r.text[:200]
    finally:
        dealers.insert_one(doc)


def _regeln_setzen(chef, regeln):
    dealers = _db().dealers
    vorher = dealers.find_one({"id": chef["me"]["dealer_id"]}, {"_id": 0, "comparison_rules": 1})
    dealers.update_one({"id": chef["me"]["dealer_id"]}, {"$set": {"comparison_rules": regeln}})

    def zurueck():
        if (vorher or {}).get("comparison_rules") is not None:
            dealers.update_one({"id": chef["me"]["dealer_id"]},
                               {"$set": {"comparison_rules": vorher["comparison_rules"]}})
        else:
            dealers.update_one({"id": chef["me"]["dealer_id"]}, {"$unset": {"comparison_rules": ""}})
    return zurueck


def test_laender_und_unfallregeln_landen_in_beiden_links(chef):
    zurueck = _regeln_setzen(chef, {"country": {"mode": "exact", "codes": ["DE", "AT", "CH"]},
                                    "damage": {"mode": "no_accident"}})
    try:
        d = _suche(chef, make="BMW", model="M3").json()
        qm, qa = _q(d["mobile_url"]), _q(d["autoscout_url"])
        assert sorted(qm.get("cn") or []) == ["AT", "CH", "DE"], qm
        assert qm.get("dam") == ["0"], qm
        assert set(",".join(qa.get("cy") or []).split(",")) == {"D", "A"}, qa
        assert qa.get("damaged_listing") == ["exclude"], qa
        assert any("CH" in h for h in d["hinweise"]), d["hinweise"]
    finally:
        zurueck()


def test_alte_regeln_als_string_fuehren_nicht_zu_500(chef):
    """Nachpruefung Runde 10: damage als String (Altdaten) — Lesepfad heilt."""
    zurueck = _regeln_setzen(chef, {"damage": "no_accident", "mileage": None,
                                    "first_registration": "exact"})
    try:
        r = _suche(chef, make="BMW", model="M3")
        assert r.status_code == 200, r.text[:300]
        assert _q(r.json()["mobile_url"]).get("dam") == ["0"], "Standard no_accident greift"
    finally:
        zurueck()


def test_nur_marke_liefert_hinweis_auf_ganze_marke(chef):
    d = _suche(chef, make="BMW").json()
    assert d["aufgeloest"]["autoscout"]["model"] is None
    assert any("ganze Marke BMW" in h for h in d["hinweise"]), d["hinweise"]
    assert any("ohne weitere Filter" in h for h in d["hinweise"]), d["hinweise"]
    d = _suche(chef, make="BMW", ez_from=2020, fuel="Diesel").json()
    assert any("ganze Marke BMW" in h for h in d["hinweise"])
    assert not any("ohne weitere Filter" in h for h in d["hinweise"])
    d = _suche(chef, make="BMW", model="M3").json()
    assert not any("ganze Marke" in h for h in d["hinweise"]), d["hinweise"]


def test_katalog_ist_browser_cachebar_und_wird_einmal_gebaut(chef):
    r = requests.get(f"{API}/manual/makes", headers=chef["h"], timeout=30)
    assert r.status_code == 200
    cc = r.headers.get("Cache-Control", "")
    assert cc.startswith("private") and "max-age=" in cc, cc
    import routes.manual_search as m
    m._makes_payload.cache_clear()
    a = m._makes_payload(); b = m._makes_payload()
    assert a is b and m._makes_payload.cache_info().hits == 1
