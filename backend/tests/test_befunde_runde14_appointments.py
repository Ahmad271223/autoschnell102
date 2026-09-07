# -*- coding: utf-8 -*-
"""Nachpruefung Runde 14, Gruppe "appointments" (09/2026).

Befunde 19, 20, 82, 83, 84, 99/98, 113, 114, 74, 77, 101 zu
routes/appointments.py.

Zwei Sorten Tests:
* unit: In-Prozess — die Routen-Funktionen werden direkt mit Fake-`user`-
  Dicts aufgerufen, das Modul-`db` (appointments/deps/lifecycle/contracts)
  zeigt solange auf einen Test-Client (nur Mongo noetig, kein Backend).
  Der Vertrags-Neuaufbau (ReportLab) wird durch einen Rekorder ersetzt.
* http: gegen TEST_BASE_URL, laufen nur mit RUNDE14_HTTP=1 (nach dem
  Neustart des Backends mit dem neuen Code).
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
from fastapi import HTTPException
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

HTTP = os.environ.get("RUNDE14_HTTP") == "1"
BASE = (os.environ.get("TEST_BASE_URL") or "http://localhost:8001").rstrip("/")
API = f"{BASE}/api"
MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
DB_NAME = os.environ.get("DB_NAME") or "autoschnell"
SUF = uuid.uuid4().hex[:8]
PW = "Runde14Test!"
JETZT = datetime.now(timezone.utc)


def _db():
    from pymongo import MongoClient
    return MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)[DB_NAME]


def _iso(delta_days=0):
    return (JETZT + timedelta(days=delta_days)).isoformat()


# ============================================================ In-Prozess-Harness
class _Welt:
    """IDs eines Testlaufs (uuid-Suffix) + Aufraeumen."""

    def __init__(self):
        s = uuid.uuid4().hex[:10]
        self.s = s
        self.dealer_id = f"d_r14a_{s}"
        self.chef = {"id": f"chef_r14a_{s}", "dealer_id": self.dealer_id, "role": "dealer"}
        self.sucher_a = {"id": f"sa_r14a_{s}", "dealer_id": self.dealer_id, "role": "sucher"}
        self.sucher_b = {"id": f"sb_r14a_{s}", "dealer_id": self.dealer_id, "role": "sucher"}
        self.driver_id = f"f_r14a_{s}"

    def appt(self, appt_id, **extra):
        doc = {"id": appt_id, "dealer_id": self.dealer_id, "title": f"Fahrt {appt_id}",
               "status": "offen", "pickup_date": "2099-09-15", "pickup_time": "10:00",
               "pickup_address": "Teststr. 1", "seller_name": "Verkaeufer",
               "created_by": self.chef["id"], "created_at": _iso()}
        doc.update(extra)
        return doc

    def vertrag(self, cid, user_id=None, **extra):
        doc = {"id": cid, "dealer_id": self.dealer_id, "user_id": user_id or self.chef["id"],
               "pickup_date": "2099-09-10", "pickup_time": "09:00", "version": 1,
               "contract_data": {"seller_name": "GEHEIM", "seller_phone": "0170 1234567"},
               "seller_phone": "0170 1234567", "created_at": _iso()}
        doc.update(extra)
        return doc

    def fahrzeug(self, vid, lifecycle):
        return {"id": vid, "dealer_id": self.dealer_id, "lifecycle": lifecycle,
                "data": {"make_label": "BMW", "model_label": "320d"}, "created_at": _iso()}

    async def fahrer_anlegen(self, db):
        await db.driver_accounts.insert_one({
            "id": self.driver_id, "email": f"fahrer-{self.s}@e2etest-mail.de",
            "display_name": "Fahrer R14", "active": True, "password_hash": "x",
            "driver_code": f"R14{self.s[:5].upper()}", "created_at": _iso()})
        await db.dealer_drivers.insert_one({
            "id": str(uuid.uuid4()), "dealer_id": self.dealer_id,
            "driver_account_id": self.driver_id, "added_at": _iso()})

    async def aufraeumen(self, db):
        for c in ("appointments", "vehicles", "generated_pdfs", "generated_pdf_versions",
                  "activity_logs", "pickup_protocols", "dealer_drivers"):
            await db[c].delete_many({"dealer_id": self.dealer_id})
        await db.driver_accounts.delete_many({"id": self.driver_id})


def _run(fn):
    """Test-Coroutine mit frischem Motor-Client; Modul-`db` von appointments,
    contracts, deps und lifecycle zeigt solange darauf (Motor bindet sich an
    die erste Event-Loop — je Test eine neue)."""
    async def _inner():
        from motor.motor_asyncio import AsyncIOMotorClient
        import deps
        import lifecycle
        import routes.appointments as A
        import routes.contracts as C
        cl = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
        db = cl[DB_NAME]
        try:
            await db.command("ping")
        except Exception as exc:  # pragma: no cover
            pytest.skip(f"Mongo nicht erreichbar: {exc}")
        alt = (A.db, C.db, deps.db, lifecycle.db)
        w = _Welt()
        A.db = C.db = deps.db = lifecycle.db = db
        try:
            return await fn(db, A, C, w)
        finally:
            A.db, C.db, deps.db, lifecycle.db = alt
            await w.aufraeumen(db)
            cl.close()
    return asyncio.run(_inner())


async def _erwarte(status: int, coro):
    with pytest.raises(HTTPException) as e:
        await coro
    assert e.value.status_code == status, (e.value.status_code, e.value.detail)


class _Rekorder:
    """Ersetzt regenerate_contract_for_pickup: merkt sich die Aufrufe."""

    def __init__(self):
        self.aufrufe = []

    async def __call__(self, **kw):
        self.aufrufe.append(kw)
        return True


def _put(A, appt_id, user, **felder):
    return A.update_appointment(appt_id, A.AppointmentIn(**felder), user)


# ============================================================ Nr. 101 (unit)
@pytest.mark.parametrize("wert", ["zzzzzzzzzz", "01.09.2026", "2026-13-01", "2026-09-31",
                                  "20260910", "2026-9-1"])
def test_101_pickup_date_nur_iso(wert):
    from routes.appointments import AppointmentIn
    with pytest.raises(ValidationError):
        AppointmentIn(pickup_date=wert)


@pytest.mark.parametrize("wert", ["25:00", "9:00", "09:60", "0900", "09:00:00", "abc"])
def test_101_pickup_time_nur_hhmm(wert):
    from routes.appointments import AppointmentIn
    with pytest.raises(ValidationError):
        AppointmentIn(pickup_time=wert)


def test_101_leer_und_gueltig_bleiben_erlaubt():
    from routes.appointments import AppointmentIn
    a = AppointmentIn(pickup_date="", pickup_time="")
    assert a.pickup_date == "" and a.pickup_time == ""
    a = AppointmentIn(pickup_date=None, pickup_time=None)
    assert a.pickup_date is None and a.pickup_time is None
    a = AppointmentIn(pickup_date=" 2026-09-10 ", pickup_time="23:59")
    assert a.pickup_date == "2026-09-10" and a.pickup_time == "23:59"
    assert AppointmentIn().pickup_date == ""


# ============================================================ Nr. 74 (unit)
def test_74_listenlimit_2000_und_antwort_bleibt_liste():
    import routes.appointments as A
    src = inspect.getsource(A.list_appointments)
    assert ".to_list(2000)" in src and ".to_list(500)" not in src
    assert "return items" in src, "Antwortform (Liste) darf sich nicht aendern"


# ============================================================ Nr. 19 (unit)
def test_19_lebenszyklus_folgt_dem_neuen_fahrzeug():
    async def lauf(db, A, C, w):
        va, vb, aid = f"va_{w.s}", f"vb_{w.s}", f"a19_{w.s}"
        await db.vehicles.insert_many([w.fahrzeug(va, "abholung_geplant"),
                                       w.fahrzeug(vb, "gekauft")])
        await db.appointments.insert_one(w.appt(aid, vehicle_id=va))
        r = await _put(A, aid, w.chef, vehicle_id=vb, status="nicht abgeholt")
        assert r["ok"]
        lc = {v["id"]: v.get("lifecycle") async for v in db.vehicles.find({"dealer_id": w.dealer_id})}
        assert lc[va] == "abholung_geplant", "abgehaengtes Auto darf keinen Status bekommen"
        assert lc[vb] == "nicht_abgeholt", "neues Auto: abholung_geplant -> nicht_abgeholt"
        appt = await db.appointments.find_one({"id": aid})
        assert appt["vehicle_id"] == vb and appt["status"] == "nicht abgeholt"
    _run(lauf)


def test_19_neues_fahrzeug_ohne_statuswechsel_bekommt_abholung_geplant():
    async def lauf(db, A, C, w):
        va, vb, aid = f"va_{w.s}", f"vb_{w.s}", f"a19b_{w.s}"
        await db.vehicles.insert_many([w.fahrzeug(va, "abholung_geplant"),
                                       w.fahrzeug(vb, "vertrag_erstellt")])
        await db.appointments.insert_one(w.appt(aid, vehicle_id=va))
        await _put(A, aid, w.chef, vehicle_id=vb)
        assert (await db.vehicles.find_one({"id": vb}))["lifecycle"] == "abholung_geplant"
        assert (await db.vehicles.find_one({"id": va}))["lifecycle"] == "abholung_geplant"
    _run(lauf)


# ============================================================ Nr. 82 (unit)
def test_82_fahrer_und_abgeholt_in_einem_aufruf_verlangt_protokoll():
    async def lauf(db, A, C, w):
        await w.fahrer_anlegen(db)
        aid = f"a82_{w.s}"
        await db.appointments.insert_one(w.appt(aid))
        await _erwarte(409, _put(A, aid, w.chef, driver_id=w.driver_id, status="abgeholt"))
        appt = await db.appointments.find_one({"id": aid})
        assert appt["status"] == "offen" and not appt.get("driver_id")
        # ohne Fahrer bleibt der manuelle Abschluss moeglich
        r = await _put(A, aid, w.chef, status="abgeholt")
        assert r["ok"]
        assert (await db.appointments.find_one({"id": aid}))["status"] == "abgeholt"
    _run(lauf)


# ============================================================ Nr. 83 / 84 / 20 / 113 (unit)
def test_83_erstmaliges_datum_erreicht_den_vertrag(monkeypatch):
    async def lauf(db, A, C, w):
        rek = _Rekorder()
        monkeypatch.setattr(C, "regenerate_contract_for_pickup", rek)
        ca, aid = f"ca_{w.s}", f"a83_{w.s}"
        await db.generated_pdfs.insert_one(w.vertrag(ca, appointment_id=aid))
        await db.appointments.insert_one(w.appt(aid, pickup_date="", pickup_time="",
                                                contract_id=ca))
        r = await _put(A, aid, w.chef, pickup_date="2099-09-15")
        assert r["pickup_date_changed"] is True and r["contract_updated"] is True
        assert len(rek.aufrufe) == 1
        assert rek.aufrufe[0]["contract_id"] == ca
        assert rek.aufrufe[0]["pickup_date"] == "2099-09-15"
        # unveraendertes Datum: keine Aenderung, kein Neuaufbau
        r = await _put(A, aid, w.chef, pickup_date="2099-09-15")
        assert r["pickup_date_changed"] is False and len(rek.aufrufe) == 1
    _run(lauf)


def test_84_leeren_des_datums_gilt_als_aenderung(monkeypatch):
    async def lauf(db, A, C, w):
        rek = _Rekorder()
        monkeypatch.setattr(C, "regenerate_contract_for_pickup", rek)
        ca, aid = f"ca_{w.s}", f"a84_{w.s}"
        await db.generated_pdfs.insert_one(w.vertrag(ca, appointment_id=aid))
        await db.appointments.insert_one(w.appt(aid, contract_id=ca))
        r = await _put(A, aid, w.chef, pickup_date="")
        assert r["pickup_date_changed"] is True
        assert rek.aufrufe and rek.aufrufe[-1]["pickup_date"] == ""
        assert (await db.appointments.find_one({"id": aid}))["pickup_date"] == ""
        # ohne pickup_date im Aufruf: keine Aenderung
        r = await _put(A, aid, w.chef, notes="nur Notiz")
        assert r["pickup_date_changed"] is False and len(rek.aufrufe) == 1
    _run(lauf)


def test_20_113_vertragswechsel_trifft_den_neuen_vertrag(monkeypatch):
    async def lauf(db, A, C, w):
        rek = _Rekorder()
        monkeypatch.setattr(C, "regenerate_contract_for_pickup", rek)
        ca, cb, aid = f"ca_{w.s}", f"cb_{w.s}", f"a20_{w.s}"
        await db.generated_pdfs.insert_many([w.vertrag(ca, appointment_id=aid),
                                             w.vertrag(cb)])
        await db.appointments.insert_one(w.appt(aid, contract_id=ca, pickup_date="2099-09-10"))
        r = await _put(A, aid, w.chef, contract_id=cb, pickup_date="2099-09-20")
        assert r["ok"] and r["contract_updated"] is True
        assert [k["contract_id"] for k in rek.aufrufe] == [cb], "nur der NEUE Vertrag"
        assert rek.aufrufe[0]["pickup_date"] == "2099-09-20"
        # Nr. 113: Verweise synchron
        assert (await db.generated_pdfs.find_one({"id": ca})).get("appointment_id") is None
        neu = await db.generated_pdfs.find_one({"id": cb})
        assert neu["appointment_id"] == aid and neu["status"] == "Termin erstellt"
        assert (await db.appointments.find_one({"id": aid}))["contract_id"] == cb
    _run(lauf)


def test_20_vertragswechsel_ohne_datum_prueft_neuen_vertrag_mit_wirksamem_termin(monkeypatch):
    async def lauf(db, A, C, w):
        rek = _Rekorder()
        monkeypatch.setattr(C, "regenerate_contract_for_pickup", rek)
        ca, cb, aid = f"ca_{w.s}", f"cb_{w.s}", f"a20b_{w.s}"
        await db.generated_pdfs.insert_many([w.vertrag(ca, appointment_id=aid), w.vertrag(cb)])
        await db.appointments.insert_one(w.appt(aid, contract_id=ca,
                                                pickup_date="2099-09-12", pickup_time="14:30"))
        await _put(A, aid, w.chef, contract_id=cb)
        assert len(rek.aufrufe) == 1 and rek.aufrufe[0]["contract_id"] == cb
        assert rek.aufrufe[0]["pickup_date"] == "2099-09-12"
        assert rek.aufrufe[0]["pickup_time"] == "14:30"
    _run(lauf)


def test_113_erster_vertrag_wird_verknuepft():
    async def lauf(db, A, C, w):
        cb, aid = f"cb_{w.s}", f"a113_{w.s}"
        await db.generated_pdfs.insert_one(w.vertrag(cb))
        await db.appointments.insert_one(w.appt(aid, pickup_date="", pickup_time=""))
        await _put(A, aid, w.chef, contract_id=cb)
        assert (await db.generated_pdfs.find_one({"id": cb}))["appointment_id"] == aid
    _run(lauf)


def test_20_sucher_kann_nicht_auf_fremden_vertrag_wechseln():
    async def lauf(db, A, C, w):
        ca, cb, aid = f"ca_{w.s}", f"cb_{w.s}", f"a20s_{w.s}"
        await db.generated_pdfs.insert_many([w.vertrag(ca, user_id=w.sucher_a["id"], appointment_id=aid),
                                             w.vertrag(cb, user_id=w.sucher_b["id"])])
        await db.appointments.insert_one(w.appt(aid, contract_id=ca, created_by=w.sucher_a["id"]))
        await _erwarte(404, _put(A, aid, w.sucher_a, contract_id=cb))
        assert (await db.generated_pdfs.find_one({"id": ca}))["appointment_id"] == aid
    _run(lauf)


# ============================================================ Nr. 99 / 98 (unit)
def test_99_98_abgeschlossen_nur_chef_aendert_beweisfelder_und_oeffnet():
    async def lauf(db, A, C, w):
        await w.fahrer_anlegen(db)
        aid = f"a99_{w.s}"
        await db.appointments.insert_one(w.appt(
            aid, status="abgeholt", created_by=w.sucher_a["id"],
            status_changed_at=_iso(-10), abgeschlossen_seit=_iso(-10)))
        # Sucher: Beweisfelder gesperrt
        await _erwarte(403, _put(A, aid, w.sucher_a, driver_id=w.driver_id))
        await _erwarte(403, _put(A, aid, w.sucher_a, seller_name="Neu"))
        await _erwarte(403, _put(A, aid, w.sucher_a, seller_phone="0171 1"))
        # Sucher: Rueckweg aus dem Endstatus gesperrt
        await _erwarte(403, _put(A, aid, w.sucher_a, status="offen"))
        # Sucher: Ganzes Objekt mit UNVERAENDERTEN Feldern (Oberflaeche) + Notiz geht
        r = await _put(A, aid, w.sucher_a, seller_name="Verkaeufer", pickup_address="Teststr. 1",
                       status="abgeholt", notes="Nachtrag")
        assert r["ok"]
        appt = await db.appointments.find_one({"id": aid})
        assert appt["status"] == "abgeholt" and appt["notes"] == "Nachtrag"
        assert appt["seller_name"] == "Verkaeufer" and not appt.get("driver_id")
        # Chef darf beides — mit von/nach im Log
        r = await _put(A, aid, w.chef, status="offen", seller_name="Neu")
        assert r["ok"]
        appt = await db.appointments.find_one({"id": aid})
        assert appt["status"] == "offen" and appt["seller_name"] == "Neu"
        log = await db.activity_logs.find_one(
            {"dealer_id": w.dealer_id, "action": "termin.aktualisiert", "ref": aid,
             "meta.status_von": "abgeholt"})
        assert log and log["meta"]["status_nach"] == "offen"
        # Sucher darf den wieder offenen Termin normal bearbeiten
        r = await _put(A, aid, w.sucher_a, seller_name="Wieder")
        assert r["ok"]
    _run(lauf)


def test_98_wechsel_zwischen_endstatus_bleibt_dem_sucher_erlaubt():
    async def lauf(db, A, C, w):
        aid = f"a98_{w.s}"
        await db.appointments.insert_one(w.appt(aid, status="nicht abgeholt",
                                                created_by=w.sucher_a["id"]))
        r = await _put(A, aid, w.sucher_a, status="storniert")
        assert r["ok"]
        log = await db.activity_logs.find_one(
            {"dealer_id": w.dealer_id, "action": "termin.aktualisiert", "ref": aid})
        assert log["meta"]["status_von"] == "nicht abgeholt"
        assert log["meta"]["status_nach"] == "storniert"
    _run(lauf)


# ============================================================ Nr. 114 (unit)
def test_114_abgeschlossen_seit_bleibt_und_assets_cleaned_at_wird_nicht_geloescht():
    async def lauf(db, A, C, w):
        aid = f"a114_{w.s}"
        await db.appointments.insert_one(w.appt(aid))
        await _put(A, aid, w.chef, status="abgeholt")
        a1 = await db.appointments.find_one({"id": aid})
        erst = a1.get("abgeschlossen_seit")
        assert erst and erst == a1["status_changed_at"]
        # Aufraeumer war schon da
        await db.appointments.update_one({"id": aid}, {"$set": {"assets_cleaned_at": _iso(-1)}})
        await _put(A, aid, w.chef, status="offen")
        a2 = await db.appointments.find_one({"id": aid})
        assert a2["abgeschlossen_seit"] == erst, "Wieder-Oeffnen loescht die Erst-Frist nicht"
        assert a2.get("assets_cleaned_at"), "bereits bereinigt bleibt bereinigt"
        await _put(A, aid, w.chef, status="abgeholt")
        a3 = await db.appointments.find_one({"id": aid})
        assert a3["abgeschlossen_seit"] == erst, "erneuter Abschluss startet die Frist nicht neu"
        assert a3["status_changed_at"] > erst
        assert a3.get("assets_cleaned_at")
    _run(lauf)


def test_114_erledigt_und_storniert_zaehlen_als_abschluss():
    async def lauf(db, A, C, w):
        for st in ("erledigt", "storniert", "nicht abgeholt"):
            aid = f"a114_{st[:4]}_{w.s}"
            await db.appointments.insert_one(w.appt(aid))
            await _put(A, aid, w.chef, status=st)
            assert (await db.appointments.find_one({"id": aid})).get("abgeschlossen_seit")
        aid = f"a114_off_{w.s}"
        await db.appointments.insert_one(w.appt(aid))
        await _put(A, aid, w.chef, status="bestätigt")
        assert not (await db.appointments.find_one({"id": aid})).get("abgeschlossen_seit")
    _run(lauf)


# ============================================================ Nr. 77 (unit)
def test_77_abholauftrag_pdf_fremder_sucher_404():
    async def lauf(db, A, C, w):
        ca, aid = f"ca_{w.s}", f"a77_{w.s}"
        await db.generated_pdfs.insert_one(w.vertrag(ca, user_id=w.sucher_a["id"], appointment_id=aid))
        await db.appointments.insert_one(w.appt(aid, contract_id=ca, created_by=w.sucher_a["id"]))
        await _erwarte(404, A.get_pickup_order_pdf(aid, 0, w.sucher_b))
        # Termin ohne created_by, aber eigener Vertrag: Sucher A darf (kein 404)
        aid2 = f"a77b_{w.s}"
        await db.appointments.insert_one(w.appt(aid2, contract_id=ca, created_by=None))
        await _erwarte(404, A.get_pickup_order_pdf(aid2, 0, w.sucher_b))
        assert await A._sucher_darf(w.sucher_a, await db.appointments.find_one({"id": aid2}))
        assert not (await db.activity_logs.find_one(
            {"dealer_id": w.dealer_id, "action": "abholauftrag.erzeugt",
             "user_id": w.sucher_b["id"]}))
    _run(lauf)


# ============================================================ HTTP (nach Neustart)
@pytest.fixture(scope="module")
def firma():
    if not HTTP:
        pytest.skip("HTTP nach Neustart")
    from auth import create_token
    r = requests.post(f"{API}/auth/register", json={
        "email": f"r14appt_{SUF}@e2etest-mail.de", "password": PW,
        "company_name": "R14 Termine GmbH", "contact_person": "R T", "phone": "0511 14"},
        timeout=30)
    assert r.status_code == 200, r.text[:200]
    h = {"Authorization": f"Bearer {r.json()['token']}"}
    me = requests.get(f"{API}/auth/me", headers=h, timeout=30).json()["user"]
    dbx = _db()
    sucher = {}
    for k in ("a", "b"):
        uid = f"r14s{k}_{SUF}"
        dbx.users.insert_one({"id": uid, "email": f"{uid}@e2etest-mail.de", "role": "sucher",
                              "dealer_id": me["dealer_id"], "active": True,
                              "current_session_id": f"s-{uid}", "password_hash": "x",
                              "first_name": "S", "last_name": k.upper(),
                              "created_at": JETZT.isoformat()})
        sucher[k] = {"id": uid, "h": {"Authorization": f"Bearer {create_token(uid, f's-{uid}')}"}}
    yield {"h": h, "me": me, "dealer_id": me["dealer_id"], "sucher": sucher}
    for c in ("appointments", "vehicles", "generated_pdfs", "generated_pdf_versions",
              "activity_logs", "dealer_drivers", "subscriptions"):
        dbx[c].delete_many({"dealer_id": me["dealer_id"]})
    dbx.users.delete_many({"id": {"$in": [me["id"]] + [s["id"] for s in sucher.values()]}})
    dbx.dealers.delete_many({"id": me["dealer_id"]})


def test_http_77_abholauftrag_pdf_nur_im_eigenen_bereich(firma):
    if not HTTP:
        pytest.skip("HTTP nach Neustart")
    dbx = _db()
    sa, sb = firma["sucher"]["a"], firma["sucher"]["b"]
    aid, cid = f"h77_{SUF}", f"h77c_{SUF}"
    dbx.generated_pdfs.insert_one({
        "id": cid, "dealer_id": firma["dealer_id"], "user_id": sa["id"], "appointment_id": aid,
        "pickup_date": "2099-09-10", "pickup_time": "09:00", "version": 1,
        "contract_data": {"seller_name": "GEHEIM", "seller_phone": "0170 1234567"},
        "seller_phone": "0170 1234567", "created_at": JETZT.isoformat()})
    dbx.appointments.insert_one({
        "id": aid, "dealer_id": firma["dealer_id"], "title": "H77", "status": "offen",
        "contract_id": cid, "created_by": sa["id"], "pickup_date": "2099-09-10",
        "seller_name": "GEHEIM", "created_at": JETZT.isoformat()})
    url = f"{API}/appointments/{aid}/pickup-order.pdf"
    assert requests.get(url, headers=sb["h"], timeout=60).status_code == 404
    for kopf in (sa["h"], firma["h"]):
        r = requests.get(url, headers=kopf, timeout=60)
        assert r.status_code == 200, r.text[:200]
        assert r.headers.get("content-type", "").startswith("application/pdf")
    # Termin ohne created_by, aber eigener Vertrag -> Sucher A 200
    aid2 = f"h77b_{SUF}"
    dbx.appointments.insert_one({
        "id": aid2, "dealer_id": firma["dealer_id"], "title": "H77b", "status": "offen",
        "contract_id": cid, "created_at": JETZT.isoformat()})
    assert requests.get(f"{API}/appointments/{aid2}/pickup-order.pdf",
                        headers=sa["h"], timeout=60).status_code == 200
    assert requests.get(f"{API}/appointments/{aid2}/pickup-order.pdf",
                        headers=sb["h"], timeout=60).status_code == 404


def test_http_101_datum_und_zeit_format(firma):
    if not HTTP:
        pytest.skip("HTTP nach Neustart")
    for body in ({"pickup_date": "zzz"}, {"pickup_date": "01.09.2026"}, {"pickup_time": "9 Uhr"}):
        r = requests.post(f"{API}/appointments", headers=firma["h"],
                          json={"title": "T", **body}, timeout=30)
        assert r.status_code == 422, (body, r.status_code, r.text[:200])
    r = requests.post(f"{API}/appointments", headers=firma["h"],
                      json={"title": "T", "pickup_date": "2099-09-10", "pickup_time": "09:30"},
                      timeout=30)
    assert r.status_code == 200, r.text[:200]
    aid = r.json()["id"]
    r = requests.put(f"{API}/appointments/{aid}", headers=firma["h"],
                     json={"pickup_date": "2099-09-31"}, timeout=30)
    assert r.status_code == 422
    r = requests.put(f"{API}/appointments/{aid}", headers=firma["h"],
                     json={"pickup_date": ""}, timeout=30)
    assert r.status_code == 200 and r.json()["pickup_date_changed"] is True


def test_http_99_sucher_403_chef_200_auf_abgeschlossenem_termin(firma):
    if not HTTP:
        pytest.skip("HTTP nach Neustart")
    sa = firma["sucher"]["a"]
    aid = f"h99_{SUF}"
    _db().appointments.insert_one({
        "id": aid, "dealer_id": firma["dealer_id"], "title": "H99", "status": "abgeholt",
        "created_by": sa["id"], "seller_name": "V", "status_changed_at": JETZT.isoformat(),
        "created_at": JETZT.isoformat()})
    url = f"{API}/appointments/{aid}"
    assert requests.put(url, headers=sa["h"], json={"seller_name": "Neu"}, timeout=30).status_code == 403
    assert requests.put(url, headers=sa["h"], json={"status": "offen"}, timeout=30).status_code == 403
    assert requests.put(url, headers=sa["h"], json={"notes": "ok"}, timeout=30).status_code == 200
    r = requests.put(url, headers=firma["h"], json={"status": "offen"}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    log = _db().activity_logs.find_one({"dealer_id": firma["dealer_id"], "ref": aid,
                                        "meta.status_von": "abgeholt"})
    assert log and log["meta"]["status_nach"] == "offen"


def test_http_74_kommende_termine_fallen_nicht_weg(firma):
    if not HTTP:
        pytest.skip("HTTP nach Neustart")
    dbx = _db()
    start = datetime(2030, 1, 1)
    docs = [{"id": f"h74_{i}_{SUF}", "dealer_id": firma["dealer_id"], "title": f"T{i}",
             "status": "offen", "pickup_date": (start + timedelta(days=i)).strftime("%Y-%m-%d"),
             "created_at": JETZT.isoformat()} for i in range(601)]
    dbx.appointments.insert_many(docs)
    try:
        r = requests.get(f"{API}/appointments", headers=firma["h"], timeout=60)
        assert r.status_code == 200 and isinstance(r.json(), list)
        ids = {a["id"] for a in r.json()}
        assert f"h74_600_{SUF}" in ids, "der spaeteste Termin muss in der Liste stehen"
        assert "X-Truncated" not in r.headers
    finally:
        dbx.appointments.delete_many({"id": {"$regex": f"^h74_.*_{SUF}$"}})
