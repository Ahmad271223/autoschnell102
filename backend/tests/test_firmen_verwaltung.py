# -*- coding: utf-8 -*-
"""Firmen-Verwaltung 09/2026 — Belastungs- und Fehlerpruefung ("kann es
krachen?") fuer Kundennummer, Firmen-/Sucher-Anlage und Freischaltung.

Abgedeckt:
- Parallele Firmen-Anlage durch den Betreiber: jede Firma bekommt eine
  eigene 4-stellige Kundennummer, nie doppelt (Chef-Kontonummer = Kundennummer)
- Kontonummer (13.09.2026), Schritt 5: gleiche Kontakt-E-Mail (auch anders
  geschrieben) ist erlaubt — jede Firma eigene Nummer, kein Konto ohne
  Firmenprofil, Anmeldung nur per Nummer
- Sucher mit bereits vergebener Kontakt-E-Mail -> erlaubt, der Zusatz laeuft
  fort und wird nie doppelt vergeben
- Zaehler weg oder veraltet (z.B. Restore ohne 'counters') -> Selbst-
  heilung, naechste Nummer liegt immer ueber dem Bestand
- Backfill fuer Bestandsfirmen parallel (zwei Worker) -> jede Firma genau
  eine Nummer, keine Dublette
- Doppelklick beim Freischalten (zwei gleichzeitige Anfragen) -> genau
  EINE Zahlung und EIN Abo, zweite Anfrage 409
- gueltig_bis-Grenzfaelle (30.02., Text, leer, mit Uhrzeit, Vergangenheit)
- Geloeschte Firma: Nummer wird nie wiederverwendet
- Unique-Index auf dealers.kunden_nr existiert (DB-Backstop)

Braucht laufendes Backend (TEST_BASE_URL, mock ok) + Mongo.
Reihenfolgeabhaengig — immer die ganze Datei laufen lassen.
"""
import asyncio
import os
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
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
PW = "Firmen123!x"
MAIL = "e2etest-mail.de"


def _db():
    from pymongo import MongoClient
    return MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)[DB_NAME]


def _run(coro_factory):
    async def _inner():
        from motor.motor_asyncio import AsyncIOMotorClient
        cl = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
        try:
            return await coro_factory(cl[DB_NAME])
        finally:
            cl.close()
    return asyncio.run(_inner())


def _login(mail):
    r = konten.login_per_mail(mail, PW, "auth",
                      timeout=30)
    assert r.status_code == 200, f"Login {mail}: {r.text[:200]}"
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _max_nr():
    top = _db().dealers.find_one({"kunden_nr": {"$type": "number"}},
                                 {"kunden_nr": 1}, sort=[("kunden_nr", -1)])
    return int(top["kunden_nr"]) if top else 1000


def _n_users():
    return _db().users.count_documents({"email": {"$regex": f"_{SUF}@", "$options": "i"}})


def _n_dealers():
    return _db().dealers.count_documents({"email": {"$regex": f"_{SUF}@", "$options": "i"}})


def _dubletten():
    return list(_db().dealers.aggregate([
        {"$match": {"kunden_nr": {"$exists": True, "$ne": None}}},
        {"$group": {"_id": "$kunden_nr", "n": {"$sum": 1}}},
        {"$match": {"n": {"$gt": 1}}}]))


def _firma(welt, tag, **extra):
    r = requests.post(f"{API}/admin/users", headers=welt["A"], json={
        "email": f"fv_{tag}_{SUF}@{MAIL}", "password": PW,
        "company_name": f"Firma {tag} {SUF}", "plan_type": "none", **extra},
        timeout=60)
    if r.status_code == 200:
        welt.setdefault("dealer_ids", []).append(r.json()["dealer_id"])
    return r


@pytest.fixture(scope="module")
def welt():
    z = {"dealer_ids": []}
    yield z
    dbx = _db()
    ids = list(z["dealer_ids"]) + [d["id"] for d in dbx.dealers.find(
        {"company_name": {"$regex": SUF}}, {"id": 1})]
    for coll in ("users", "subscriptions", "manual_payments", "plan_requests",
                 "activity_logs"):
        dbx[coll].delete_many({"dealer_id": {"$in": ids}})
    dbx.dealers.delete_many({"id": {"$in": ids}})
    dbx.users.delete_many({"email": {"$regex": f"_{SUF}@"}})
    dbx.sperren.delete_many({"_id": {"$regex": "^abo:"}})


def test_00_admin(welt):
    import bcrypt
    mail = f"fv_admin_{SUF}@{MAIL}"
    _db().users.insert_one({
        "id": f"fvadm_{SUF}", "email": mail, "role": "admin", "active": True,
        "dealer_id": None, "is_super_admin": True,
        "password_hash": bcrypt.hashpw(PW.encode(), bcrypt.gensalt()).decode(),
        "created_at": "2026-01-01T00:00:00+00:00"})
    welt["A"] = _login(mail)
    # Pruefbericht 20.09.2026 (T-10): der Aufbau-Test sichert etwas zu — das
    # Konto ist als Super-Admin angemeldet (vorher nur der Statuscode im Helfer).
    me = requests.get(f"{API}/auth/me", headers=welt["A"], timeout=30)
    assert me.status_code == 200, me.text[:200]
    konto = me.json()["user"]
    assert konto["role"] == "admin" and konto.get("is_super_admin") is True, konto
    assert konto["id"] == f"fvadm_{SUF}"
    welt["max_vor_probe"] = _max_nr()      # hoechste Nummer VOR allen Testfirmen


# ---------- parallele Anlage ----------
def test_01_parallel_anlegen_eindeutige_nummern(welt):
    vorher_max = welt["max_vor_probe"]

    def admin_anlegen(i):
        return _firma(welt, f"par{i}")

    with ThreadPoolExecutor(max_workers=16) as ex:
        a = list(ex.map(admin_anlegen, range(16)))
    assert all(r.status_code == 200 for r in a), [r.text[:100] for r in a if r.status_code != 200]
    firmen = list(_db().dealers.find({"company_name": {"$regex": SUF}},
                                     {"kunden_nr": 1, "company_name": 1}))
    nummern = [f.get("kunden_nr") for f in firmen]
    assert len(firmen) == 16
    assert all(isinstance(n, int) and 1001 <= n <= 9999 for n in nummern), nummern
    assert len(set(nummern)) == len(nummern), f"Dublette: {nummern}"
    assert min(nummern) > vorher_max
    assert _dubletten() == []
    # Kontonummer (13.09.2026): Chef-Nummer = Kundennummer, alle verschieden
    konten_nr = [r.json()["kontonummer"] for r in a]
    assert konten_nr == [str(r.json()["kunden_nr"]) for r in a]
    assert len(set(konten_nr)) == 16


# ---------- gleiche Kontakt-E-Mail ----------
def test_02_gleiche_kontakt_email_eigene_nummer(welt):
    dbx = _db()
    r = _firma(welt, "dup")
    assert r.status_code == 200, r.text[:200]
    dealer_id = r.json()["dealer_id"]
    nummern = [r.json()["kontonummer"]]
    users_vorher = _n_users()
    dealers_vorher = _n_dealers()
    # Kontonummer (13.09.2026), Schritt 5: exakt gleich und nur anders
    # geschrieben -> je eine neue Firma mit eigener Nummer (E-Mail nur Kontakt)
    for mail, name in ((f"fv_dup_{SUF}@{MAIL}", "Nochmal"),
                       (f"FV_DUP_{SUF}@{MAIL.upper()}", "Nochmal gross")):
        r = requests.post(f"{API}/admin/users", headers=welt["A"], json={
            "email": mail, "password": PW, "company_name": f"{name} {SUF}",
            "plan_type": "none"}, timeout=60)
        assert r.status_code == 200, r.text[:200]
        welt["dealer_ids"].append(r.json()["dealer_id"])
        nummern.append(r.json()["kontonummer"])
    assert len(set(nummern)) == 3, nummern
    assert _n_users() == users_vorher + 2
    assert _n_dealers() == dealers_vorher + 2
    # gespeichert klein geschrieben; Anmeldung nur per Nummer, nie per Adresse
    gleich = list(dbx.users.find({"email": {"$regex": f"^fv_dup_{SUF}@", "$options": "i"}}))
    assert len(gleich) == 3 and all(u["email"] == f"fv_dup_{SUF}@{MAIL}" for u in gleich)
    assert konten.anmelden(nummern[0], PW).status_code == 200
    assert konten.anmelden(f"FV_DUP_{SUF}@{MAIL}", PW).status_code == 401
    welt["dup_dealer"] = dealer_id
    welt["dup_chef"] = dbx.users.find_one({"dealer_id": dealer_id})["id"]
    # kein Konto ohne Firmenprofil zurueckgeblieben
    for u in dbx.users.find({"email": {"$regex": f"_{SUF}@"}, "role": "dealer"}):
        assert dbx.dealers.count_documents({"id": u["dealer_id"]}) == 1, u["email"]


# ---------- Sucher mit vergebener Kontakt-E-Mail ----------
def test_03_sucher_mit_vergebener_email(welt):
    dbx = _db()
    url = f"{API}/admin/dealers/{welt['dup_dealer']}/sucher"
    r = requests.post(url, headers=welt["A"], json={
        "email": f"fv_such_{SUF}@{MAIL}", "password": PW,
        "first_name": "S", "last_name": "Eins"}, timeout=60)
    assert r.status_code == 200, r.text[:200]
    welt["sucher_id"] = r.json()["sucher_id"]
    nummern = [r.json()["kontonummer"]]
    n = _n_users()
    kunden_nr = dbx.dealers.find_one({"id": welt["dup_dealer"]})["kunden_nr"]
    # Kontonummer (13.09.2026), Schritt 5: E-Mail des Chefs, des Suchers (anders
    # geschrieben) oder einer fremden Firma ist erlaubt — der Zusatz laeuft fort
    for mail in (f"fv_dup_{SUF}@{MAIL}", f"FV_SUCH_{SUF}@{MAIL}", f"fv_par0_{SUF}@{MAIL}"):
        r = requests.post(url, headers=welt["A"], json={
            "email": mail, "password": PW, "first_name": "X", "last_name": "Y"},
            timeout=60)
        assert r.status_code == 200, (mail, r.text[:200])
        nummern.append(r.json()["kontonummer"])
    assert nummern == [f"{kunden_nr}-{i}" for i in range(1, 5)], nummern
    assert _n_users() == n + 3
    # unbekannte Firma -> 404, kein Konto
    r = requests.post(f"{API}/admin/dealers/gibtesnicht/sucher", headers=welt["A"],
                      json={"password": PW, "email": f"fv_nix_{SUF}@{MAIL}",
                            "first_name": "X", "last_name": "Y"}, timeout=60)
    assert r.status_code == 404
    assert _n_users() == n + 3


# ---------- Zaehler weg / veraltet ----------
def test_04_zaehler_selbstheilung(welt):
    dbx = _db()
    original = dbx.counters.find_one({"_id": "kunden_nr"})
    assert original, "Zaehler fehlt komplett — Backend nie mit Firmen gestartet?"
    max_vorher = _max_nr()
    try:
        # 1) Zaehler geloescht (Restore ohne 'counters')
        dbx.counters.delete_one({"_id": "kunden_nr"})
        r = _firma(welt, "heal1")
        assert r.status_code == 200, r.text[:200]
        nr1 = dbx.dealers.find_one({"id": r.json()["dealer_id"]})["kunden_nr"]
        assert nr1 > max_vorher, (nr1, max_vorher)
        # 2) Zaehler veraltet (steht weit hinter dem Bestand)
        dbx.counters.update_one({"_id": "kunden_nr"}, {"$set": {"seq": 1}})
        r = _firma(welt, "heal2")
        assert r.status_code == 200, r.text[:200]
        nr2 = dbx.dealers.find_one({"id": r.json()["dealer_id"]})["kunden_nr"]
        assert nr2 > nr1, (nr2, nr1)
        assert _dubletten() == []
    finally:
        # Zaehler nie unter den Bestand fallen lassen
        dbx.counters.update_one({"_id": "kunden_nr"},
                                {"$max": {"seq": _max_nr() - 1000}}, upsert=True)


# ---------- Backfill parallel ----------
def test_05_backfill_parallel_ohne_dubletten(welt):
    dbx = _db()
    ids = [f"fvbf_{SUF}_{i}" for i in range(3)]
    for i, did in enumerate(ids):
        dbx.dealers.insert_one({"id": did, "user_id": f"fvbfu_{SUF}_{i}",
                                "company_name": f"Bestand {i} {SUF}",
                                "created_at": f"2025-01-0{i + 1}T00:00:00+00:00"})
    welt["dealer_ids"].extend(ids)

    async def zwei_worker(db):
        import deps
        alt = deps.db
        deps.db = db
        try:
            return await asyncio.gather(deps.kunden_nummern_nachziehen(),
                                        deps.kunden_nummern_nachziehen())
        finally:
            deps.db = alt          # Modulzustand fuer nachfolgende Tests zuruecksetzen

    ergebnis = _run(zwei_worker)
    assert sum(ergebnis) == 3, ergebnis          # jede Firma genau einmal
    nummern = [dbx.dealers.find_one({"id": d})["kunden_nr"] for d in ids]
    assert all(isinstance(n, int) for n in nummern) and len(set(nummern)) == 3, nummern
    assert _dubletten() == []
    # zweiter Lauf: nichts mehr zu tun
    assert _run(lambda db: _nachziehen(db)) == 0


async def _nachziehen(db):
    import deps
    alt = deps.db
    deps.db = db
    try:
        return await deps.kunden_nummern_nachziehen()
    finally:
        deps.db = alt


# ---------- Doppelklick beim Freischalten ----------
def test_06_doppelklick_freischalten_genau_eine_zahlung(welt):
    dbx = _db()
    sid = welt["sucher_id"]
    url = f"{API}/admin/sucher/{sid}/abo"
    # 1) Deterministisch: waehrend eine Freischaltung laeuft (Sperre gesetzt),
    #    wird die zweite Anfrage mit 409 abgewiesen — nichts gebucht.
    dbx.sperren.insert_one({"_id": f"abo:{sid}",
                            "seit": datetime.now(timezone.utc).isoformat()})
    r = requests.post(url, headers=welt["A"], json={"plan": "monthly"}, timeout=60)
    assert r.status_code == 409 and "Doppelklick" in r.text, r.text[:200]
    assert dbx.manual_payments.count_documents({"subject_user_id": sid}) == 0
    # 2) Verwaiste Sperre (z.B. Absturz mitten im Vorgang) blockiert nicht ewig
    dbx.sperren.update_one({"_id": f"abo:{sid}"}, {"$set": {"seit": (
        datetime.now(timezone.utc) - timedelta(seconds=90)).isoformat()}})
    r = requests.post(url, headers=welt["A"], json={"plan": "monthly"}, timeout=60)
    assert r.status_code == 200, r.text[:200]
    assert dbx.manual_payments.count_documents({"subject_user_id": sid}) == 1
    assert dbx.sperren.count_documents({"_id": f"abo:{sid}"}) == 0   # wieder frei
    # 3) Salve gleichzeitiger Anfragen: Buchungen == erfolgreiche Antworten,
    #    nie mehr (kein doppeltes Buchen), nie ein zweites Abo, Sperre frei.
    vorher = dbx.manual_payments.count_documents({"subject_user_id": sid})

    def frei(_):
        return requests.post(url, headers=welt["A"], json={"plan": "monthly"}, timeout=60)

    with ThreadPoolExecutor(max_workers=6) as ex:
        codes = [r.status_code for r in ex.map(frei, range(6))]
    assert set(codes) <= {200, 409}, codes
    assert dbx.manual_payments.count_documents({"subject_user_id": sid}) == vorher + codes.count(200)
    # Historie bleibt (Audit): genau EIN aktives Abo, alte Zeilen als "ersetzt"
    assert dbx.subscriptions.count_documents({"subject_user_id": sid, "status": "active"}) == 1
    assert dbx.subscriptions.count_documents({"subject_user_id": sid, "status": "active"}) == 1
    assert dbx.sperren.count_documents({"_id": f"abo:{sid}"}) == 0
    # Sperre wieder frei: naechste (gewollte) Freischaltung geht durch
    r = requests.post(url, headers=welt["A"], json={"plan": "yearly"}, timeout=60)
    assert r.status_code == 200, r.text[:200]
    # Historie bleibt (Audit): genau EIN aktives Abo, alte Zeilen als "ersetzt"
    assert dbx.subscriptions.count_documents({"subject_user_id": sid, "status": "active"}) == 1


# ---------- gueltig_bis-Grenzfaelle ----------
def test_07_gueltig_bis_grenzfaelle(welt):
    dbx = _db()
    sid = welt["sucher_id"]
    url = f"{API}/admin/sucher/{sid}/abo"
    zahlungen = dbx.manual_payments.count_documents({"subject_user_id": sid})
    for kaputt in ("2026-02-30", "abc", "31.12.2026", "2026-13-01"):
        r = requests.post(url, headers=welt["A"],
                          json={"plan": "monthly", "gueltig_bis": kaputt}, timeout=60)
        assert r.status_code == 400, (kaputt, r.text[:200])
        r = requests.patch(f"{API}/admin/sucher/{sid}/abo-gueltig-bis", headers=welt["A"],
                           json={"gueltig_bis": kaputt, "grund": "Test: Laufzeit angepasst"}, timeout=60)
        assert r.status_code == 400, (kaputt, r.text[:200])
    # nichts gebucht, Abo unveraendert
    assert dbx.manual_payments.count_documents({"subject_user_id": sid}) == zahlungen
    assert dbx.sperren.count_documents({"_id": f"abo:{sid}"}) == 0
    # leer -> normale Laufzeit ab bisherigem Ablauf (kein Absturz)
    r = requests.post(url, headers=welt["A"], json={"plan": "monthly", "gueltig_bis": ""}, timeout=60)
    assert r.status_code == 200, r.text[:200]
    # mit Uhrzeit -> nur der Tag zaehlt
    r = requests.post(url, headers=welt["A"],
                      json={"plan": "monthly", "gueltig_bis": "2030-01-01T10:00:00"}, timeout=60)
    assert r.status_code == 200 and r.json()["expires_at"].startswith("2030-01-01T23:59:59"), r.text[:200]
    # Vergangenheit beim Freischalten: erlaubt, aber sofort gesperrt
    r = requests.post(url, headers=welt["A"],
                      json={"plan": "monthly", "gueltig_bis": "2020-01-01"}, timeout=60)
    assert r.status_code == 200
    zeile = next(x for x in requests.get(
        f"{API}/admin/dealers/{welt['dup_dealer']}/sucher", headers=welt["A"],
        timeout=30).json() if x["id"] == sid)
    assert zeile["subscription"]["active"] is False
    assert zeile["subscription"]["status"] == "expired"
    # unbekanntes Konto
    assert requests.patch(f"{API}/admin/sucher/gibtesnicht/abo-gueltig-bis", headers=welt["A"],
                          json={"gueltig_bis": "2030-01-01", "grund": "Test: Laufzeit angepasst"}, timeout=60).status_code == 404
    assert requests.post(f"{API}/admin/sucher/gibtesnicht/abo", headers=welt["A"],
                         json={"plan": "monthly"}, timeout=60).status_code == 404


# ---------- geloeschte Firma: Nummer nie wieder ----------
def test_08_nummer_nach_loeschen_nicht_wiederverwendet(welt):
    dbx = _db()
    r = _firma(welt, "weg")
    assert r.status_code == 200, r.text[:200]
    dealer_id, chef_id = r.json()["dealer_id"], r.json()["user_id"]
    nr = dbx.dealers.find_one({"id": dealer_id})["kunden_nr"]
    r = requests.delete(f"{API}/admin/users/{chef_id}?firma_loeschen=true", headers=welt["A"], timeout=60)
    assert r.status_code == 200, r.text[:200]
    assert dbx.dealers.count_documents({"id": dealer_id}) == 0
    r = _firma(welt, "danach")
    assert r.status_code == 200, r.text[:200]
    nr2 = dbx.dealers.find_one({"id": r.json()["dealer_id"]})["kunden_nr"]
    assert nr2 > nr and dbx.dealers.count_documents({"kunden_nr": nr}) == 0


# ---------- DB-Backstop ----------
def test_09_unique_index_auf_kundennummer(welt):
    idx = {i["name"]: i for i in _db().dealers.list_indexes()}
    u = [i for i in idx.values() if i.get("unique") and list(i["key"].keys()) == ["kunden_nr"]]
    assert u, f"kein Unique-Index auf kunden_nr: {list(idx)}"
    assert _dubletten() == []
