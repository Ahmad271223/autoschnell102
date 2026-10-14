# -*- coding: utf-8 -*-
"""Konto-Maskierung ueber HTTP (Pruefbericht 20.09.2026, T-22).

Regel (Runde 29/30, Ahmad): Ein Sucher sieht NICHT, welche Konten der Firma
an einem Fahrzeug haengen — weder Namen noch Kennungen. Bisher wurde das nur
prozessintern geprueft (L.list_vehicles / B.vehicle_akte direkt aufgerufen);
die Serialisierung und die Antwortmodelle der echten Routen blieben aussen
vor. Hier laeuft alles per requests gegen das Backend auf TEST_BASE_URL:

  * Sucher A legt per Vergleich (Anbieter-Mock) ein Fahrzeug, einen Termin
    und einen Kaufvertrag an.
  * Sucher A: /vehicles, /vehicles/{id}, /vehicles/{id}/akte, /bestand,
    /appointments, /appointments/{id} — kein Konto-Feld im JSON (tief).
  * Chef: dieselben Antworten tragen owner_user_id (= Sucher A).
  * Sucher B derselben Firma sieht davon NICHTS (leer bzw. 404).

Braucht ein laufendes Backend (TEST_BASE_URL) mit MOCK_PROVIDER_FETCH=true
und Mongo-Zugriff (Aufraeumen).
"""
import os
import sys
import uuid
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
PW = "Kq4Lm9Xw2-Maske!x"

# Spiegel von deps.KONTO_FELDER (test_04 gleicht beide Seiten ab).
KONTO_FELDER = frozenset({"owner_user_id", "mitbearbeiter_ids", "uebernommen_von",
                          "besitzer_migriert_von", "besitzer_vorher",
                          "entfernt_von_sucher"})


def _backend_da() -> bool:
    try:
        requests.get(f"{API}/health", timeout=5)
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _backend_da(), reason=f"Backend fehlt (TEST_BASE_URL={BASE} nicht erreichbar)")


def _db():
    from pymongo import MongoClient
    return MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)[DB_NAME]


def _kopf(token):
    return {"Authorization": f"Bearer {token}"}


def _get(pfad, kopf):
    return requests.get(f"{API}{pfad}", headers=kopf, timeout=60)


def _schluessel(obj, acc=None) -> set:
    """Alle Dict-Schluessel einer JSON-Antwort, beliebig tief."""
    acc = set() if acc is None else acc
    if isinstance(obj, dict):
        acc.update(obj.keys())
        for v in obj.values():
            _schluessel(v, acc)
    elif isinstance(obj, list):
        for v in obj:
            _schluessel(v, acc)
    return acc


def _eintraege(antwort):
    d = antwort.json()
    if isinstance(d, list):
        return d
    return d.get("items") or d.get("vehicles") or d.get("appointments") or d.get("contracts") or []


@pytest.fixture(scope="module")
def welt():
    z = {}
    r = konten.registrieren(json={
        "email": f"mh_chef_{SUF}@{MAIL}", "password": PW,
        "company_name": f"Masken Autohaus {SUF}", "contact_person": "M Chef",
        "phone": "0511 7"}, timeout=30)
    assert r.status_code == 200, r.text[:300]
    z["chef"] = _kopf(r.json()["token"])
    z["dealer_id"] = r.json()["user"]["dealer_id"]
    for name in ("a", "b"):
        r = konten.sucher_als_chef_anlegen(z["chef"], json={
            "email": f"mh_sucher_{name}_{SUF}@{MAIL}", "password": PW,
            "first_name": "Erika", "last_name": f"Sucher {name.upper()}"}, timeout=30)
        assert r.status_code == 200, r.text[:300]
        sid, nr = r.json()["sucher_id"], r.json()["kontonummer"]
        abo = konten._super("POST", f"/admin/sucher/{sid}/abo", {"plan": "monthly"})
        assert abo.status_code == 200, abo.text[:300]
        login = konten.anmelden(nr, PW, "auth")
        assert login.status_code == 200, login.text[:300]
        z[name] = {"id": sid, "kopf": _kopf(login.json()["token"]), "nr": nr}
    yield z
    dbx = _db()
    for coll in ("users", "subscriptions", "manual_payments", "vehicles", "appointments",
                 "generated_pdfs", "kaufvorgaenge", "activity_logs", "abo_vorgaenge"):
        dbx[coll].delete_many({"dealer_id": z["dealer_id"]})
    dbx.dealers.delete_many({"id": z["dealer_id"]})
    dbx.users.delete_many({"email": {"$regex": f"_{SUF}@"}})


def test_00_sucher_a_legt_fahrzeug_termin_und_vertrag_an(welt):
    ka_url = ("https://www.kleinanzeigen.de/s-anzeige/maske-http/"
              f"97{uuid.uuid4().int % 10**8:08d}-216-1")
    r = requests.post(f"{API}/mobile/compare", headers=welt["a"]["kopf"],
                      json={"url": ka_url}, timeout=90)
    if r.status_code != 200 or not (r.json().get("vehicle") or {}).get("_mock"):
        pytest.skip("Backend ohne MOCK_PROVIDER_FETCH — Test wuerde einen echten "
                    f"Kleinanzeigen-Abruf ausloesen ({r.status_code} {r.text[:120]})")
    vid = r.json().get("vehicle_id") or (r.json().get("vehicle") or {}).get("id")
    assert vid, str(r.json())[:200]
    welt["vid"] = vid
    r = requests.post(f"{API}/appointments", headers=welt["a"]["kopf"], json={
        "title": f"Abholung Maske {SUF}", "status": "offen", "pickup_date": "2099-06-15",
        "pickup_time": "10:00", "vehicle_id": vid, "seller_name": "Verkaeufer Maske"},
        timeout=30)
    assert r.status_code == 200, r.text[:300]
    welt["appt"] = r.json()["id"]
    cr = requests.post(f"{API}/contracts", headers=welt["a"]["kopf"], json={
        "vehicle_id": vid, "seller_name": "Verkäufer Maske GmbH",
        "seller_address": "Str 2", "seller_zip": "10115", "seller_city": "Berlin",
        "seller_phone": "+490", "seller_email": "v@e.de", "purchase_price": 19999,
        "pickup_date": "2099-06-16", "pickup_time": "10:00"}, timeout=60)
    assert cr.status_code in (200, 201), cr.text[:300]
    welt["cid"] = cr.json().get("id")
    assert welt["cid"]


def _sucher_pfade(welt):
    return ["/vehicles", f"/vehicles/{welt['vid']}", f"/vehicles/{welt['vid']}/akte",
            "/bestand", "/appointments", f"/appointments/{welt['appt']}",
            "/contracts", f"/contracts/{welt['cid']}"]


def test_01_sucher_sieht_keine_konto_kennungen(welt):
    if "vid" not in welt:
        pytest.skip("Aufbau uebersprungen")
    for pfad in _sucher_pfade(welt):
        r = _get(pfad, welt["a"]["kopf"])
        assert r.status_code == 200, f"{pfad}: {r.status_code} {r.text[:200]}"
        gefunden = _schluessel(r.json()) & KONTO_FELDER
        assert not gefunden, f"{pfad}: Konto-Kennungen in der Sucher-Antwort: {sorted(gefunden)}"
    # ... und die Antworten sind nicht etwa leer (sonst waere der Test wertlos)
    assert welt["vid"] in {v.get("id") for v in _eintraege(_get("/vehicles", welt["a"]["kopf"]))}
    assert _get(f"/vehicles/{welt['vid']}", welt["a"]["kopf"]).json()["id"] == welt["vid"]
    assert welt["appt"] in {a.get("id") for a in _eintraege(_get("/appointments", welt["a"]["kopf"]))}
    a = _get(f"/appointments/{welt['appt']}", welt["a"]["kopf"]).json()
    assert a["id"] == welt["appt"] and (a.get("vehicle") or {}).get("id") == welt["vid"]


def test_02_chef_sieht_die_kennungen(welt):
    if "vid" not in welt:
        pytest.skip("Aufbau uebersprungen")
    liste = _eintraege(_get("/vehicles", welt["chef"]))
    eintrag = next((v for v in liste if v.get("id") == welt["vid"]), None)
    assert eintrag and eintrag.get("owner_user_id") == welt["a"]["id"], eintrag
    detail = _get(f"/vehicles/{welt['vid']}", welt["chef"]).json()
    assert detail.get("owner_user_id") == welt["a"]["id"], detail.keys()
    akte = _get(f"/vehicles/{welt['vid']}/akte", welt["chef"])
    assert akte.status_code == 200 and "owner_user_id" in _schluessel(akte.json())
    termin = _get(f"/appointments/{welt['appt']}", welt["chef"]).json()
    assert (termin.get("vehicle") or {}).get("owner_user_id") == welt["a"]["id"], termin.get("vehicle")


def test_03_zweiter_sucher_derselben_firma_sieht_nichts(welt):
    if "vid" not in welt:
        pytest.skip("Aufbau uebersprungen")
    kopf = welt["b"]["kopf"]
    assert welt["vid"] not in {v.get("id") for v in _eintraege(_get("/vehicles", kopf))}
    assert welt["vid"] not in {v.get("id") for v in _eintraege(_get("/bestand", kopf))}
    assert welt["appt"] not in {a.get("id") for a in _eintraege(_get("/appointments", kopf))}
    assert welt["cid"] not in {c.get("id") for c in _eintraege(_get("/contracts", kopf))}
    for pfad in (f"/vehicles/{welt['vid']}", f"/vehicles/{welt['vid']}/akte",
                 f"/appointments/{welt['appt']}", f"/contracts/{welt['cid']}",
                 f"/contracts/{welt['cid']}/pdf"):
        r = _get(pfad, kopf)
        assert r.status_code == 404, f"{pfad}: Sucher B bekam {r.status_code} {r.text[:120]}"


def test_04_felderliste_stimmt_mit_deps_ueberein():
    """Kommt in deps.KONTO_FELDER ein Feld dazu, muss dieser Test es mitpruefen."""
    os.environ.setdefault("MONGO_URL", MONGO_URL)
    os.environ.setdefault("DB_NAME", DB_NAME)
    deps = pytest.importorskip("deps")
    assert set(deps.KONTO_FELDER) == set(KONTO_FELDER), \
        f"deps.KONTO_FELDER = {sorted(deps.KONTO_FELDER)} — Liste im Test nachziehen"
