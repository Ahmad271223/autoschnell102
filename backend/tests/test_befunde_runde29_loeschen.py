# -*- coding: utf-8 -*-
"""Runde 29 (12.09.2026, Pruefbefunde) — Loeschen sauber zu Ende fuehren:

  * Wurde ein Kaufvertrag endgueltig geloescht, blieb sein Kaufvorgang
    unberuehrt. Ein noch OFFENER Vorgang liess das Fahrzeug dauerhaft auf
    "gekauft" stehen, obwohl es den Vertrag nicht mehr gab.
  * Beim Loeschen eines Suchers blieben seine Fahrzeuge mit einem Besitzer
    stehen, den es nicht mehr gibt, und sein Konto stand weiter in den
    Mitbearbeitern.

In-Prozess gegen eine Wegwerf-DB; kein Server.
"""
import asyncio
import importlib
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MONGO_URL = "mongodb://127.0.0.1:27017"


def _jetzt():
    return datetime.now(timezone.utc).isoformat()


def _modul(name):
    return importlib.import_module(name)


@pytest.fixture
def welt():
    from motor.motor_asyncio import AsyncIOMotorClient
    s = uuid.uuid4().hex[:10]
    namen = ["deps", "routes.team", "routes.bestand", "kaufvorgang",
             "lifecycle", "cleanup_service"]
    mods = [_modul(n) for n in namen]
    alt = [(m, getattr(m, "db", None)) for m in mods]

    class _W:
        pass

    w = _W()
    w.s = s
    w.dealer_id = f"d_r29l_{s}"
    w.chef = {"id": f"chef_r29l_{s}", "dealer_id": w.dealer_id, "role": "dealer"}
    w.a = {"id": f"sa_r29l_{s}", "dealer_id": w.dealer_id, "role": "sucher"}
    w.b = {"id": f"sb_r29l_{s}", "dealer_id": w.dealer_id, "role": "sucher"}
    w.loop = asyncio.new_event_loop()
    asyncio.set_event_loop(w.loop)
    w.client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    w.db_name = f"autoschnell_r29l_{s}"
    w.db = w.client[w.db_name]
    for m in mods:
        if hasattr(m, "db"):
            m.db = w.db
    w.run = lambda coro: w.loop.run_until_complete(coro)
    w.run(w.db.users.insert_many([
        {**w.chef, "active": True, "created_at": _jetzt()},
        {**w.a, "active": True, "email": f"a{s}@test.invalid", "created_at": _jetzt()},
        {**w.b, "active": True, "email": f"b{s}@test.invalid", "created_at": _jetzt()}]))
    w.run(w.db.dealers.insert_one({"id": w.dealer_id, "user_id": w.chef["id"],
                                   "company_name": "Firma R29L", "created_at": _jetzt()}))
    yield w
    try:
        w.run(w.client.drop_database(w.db_name))
    finally:
        for m, d in alt:
            if d is not None:
                m.db = d
        w.client.close()
        w.loop.close()


def _vertrag(w, cid, vid, user_id):
    return {"id": cid, "dealer_id": w.dealer_id, "user_id": user_id,
            "vehicle_id": vid, "contract_no": "R29-1", "created_at": _jetzt()}


def _vorgang(w, kid, cid, vid, user_id, status):
    return {"id": kid, "dealer_id": w.dealer_id, "user_id": user_id,
            "vehicle_id": vid, "contract_id": cid, "status": status,
            "purchase_price": 12000, "created_at": _jetzt(),
            "updated_at": _jetzt()}


# ------------------------------------------------ Vertrag endgueltig weg
def test_01_offener_kaufvorgang_wird_beim_vertragsloeschen_geschlossen(welt):
    w = welt
    CS = _modul("cleanup_service")
    cid, vid, kid = f"c_{w.s}", f"v_{w.s}", f"k_{w.s}"

    async def lauf():
        await w.db.vehicles.insert_one(
            {"id": vid, "dealer_id": w.dealer_id, "owner_user_id": w.a["id"],
             "lifecycle": "gekauft", "created_at": _jetzt()})
        await w.db.generated_pdfs.insert_one(_vertrag(w, cid, vid, w.a["id"]))
        await w.db.kaufvorgaenge.insert_one(
            _vorgang(w, kid, cid, vid, w.a["id"], "vertrag_erstellt"))
        weg = await CS.vertrag_endgueltig_loeschen(
            w.db, cid, scrub_pii=False, grund="test")
        kv = await w.db.kaufvorgaenge.find_one({"id": kid}, {"_id": 0})
        v = await w.db.vehicles.find_one({"id": vid}, {"_id": 0})
        # Zweiter Lauf darf nichts kaputt machen (idempotent).
        nochmal = await CS.vertrag_endgueltig_loeschen(
            w.db, cid, scrub_pii=False, grund="test")
        kv2 = await w.db.kaufvorgaenge.find_one({"id": kid}, {"_id": 0})
        return weg, kv, v, nochmal, kv2

    weg, kv, v, nochmal, kv2 = w.run(lauf())
    assert weg is True and nochmal is False
    assert kv["status"] == "storniert", kv
    assert kv["storno_grund"] == "vertrag_geloescht", kv
    assert kv["vertrag_geloescht_am"], kv
    # Das Fahrzeug haengt nicht mehr auf "gekauft".
    assert v["lifecycle"] != "gekauft", v["lifecycle"]
    assert kv2["vertrag_geloescht_am"] == kv["vertrag_geloescht_am"], "zweiter Lauf aendert nichts"


def test_02_abgeschlossener_vorgang_bleibt_als_beleg_stehen(welt):
    """Ein abgeholter Kauf ist Geschichte — er wird nur markiert, nicht
    nachtraeglich storniert."""
    w = welt
    CS = _modul("cleanup_service")
    cid, vid, kid = f"c2_{w.s}", f"v2_{w.s}", f"k2_{w.s}"

    async def lauf():
        await w.db.vehicles.insert_one(
            {"id": vid, "dealer_id": w.dealer_id, "owner_user_id": w.a["id"],
             "lifecycle": "abgeholt", "created_at": _jetzt()})
        await w.db.generated_pdfs.insert_one(_vertrag(w, cid, vid, w.a["id"]))
        await w.db.kaufvorgaenge.insert_one(
            _vorgang(w, kid, cid, vid, w.a["id"], "abgeholt"))
        await CS.vertrag_endgueltig_loeschen(
            w.db, cid, scrub_pii=False, grund="frist")
        return await w.db.kaufvorgaenge.find_one({"id": kid}, {"_id": 0})

    kv = w.run(lauf())
    assert kv["status"] == "abgeholt", kv
    assert "storno_grund" not in kv, kv
    assert kv["vertrag_geloescht_am"], kv


# ------------------------------------------------------- Sucher loeschen
def test_03_geloeschter_sucher_hinterlaesst_keine_toten_verweise(welt, monkeypatch):
    w = welt
    T = _modul("routes.team")
    monkeypatch.setattr(T, "_chef_verwaltung_erlaubt", lambda: True)
    v_eigen, v_fremd = f"ve_{w.s}", f"vf_{w.s}"

    async def lauf():
        await w.db.vehicles.insert_many([
            {"id": v_eigen, "dealer_id": w.dealer_id, "owner_user_id": w.a["id"],
             "mitbearbeiter_ids": [w.b["id"]], "lifecycle": "verglichen",
             "created_at": _jetzt()},
            {"id": v_fremd, "dealer_id": w.dealer_id, "owner_user_id": w.b["id"],
             "mitbearbeiter_ids": [w.a["id"]], "lifecycle": "verglichen",
             "created_at": _jetzt()}])
        ok = await T.delete_sucher(w.a["id"], w.chef)
        eigen = await w.db.vehicles.find_one({"id": v_eigen}, {"_id": 0})
        fremd = await w.db.vehicles.find_one({"id": v_fremd}, {"_id": 0})
        weg = await w.db.users.find_one({"id": w.a["id"]})
        log = await w.db.activity_logs.find_one({"action": "sucher.geloescht"},
                                                {"_id": 0})
        return ok, eigen, fremd, weg, log

    ok, eigen, fremd, weg, log = w.run(lauf())
    assert ok == {"ok": True} and weg is None
    # Das Fahrzeug des geloeschten Suchers gehoert jetzt dem Firmenaccount.
    assert eigen["owner_user_id"] == w.chef["id"], eigen
    assert eigen["uebernommen_von"] == w.a["id"], eigen
    # Bei fremden Fahrzeugen verschwindet er aus den Mitbearbeitern.
    assert fremd["owner_user_id"] == w.b["id"], fremd
    assert fremd["mitbearbeiter_ids"] == [], fremd
    assert log["meta"]["fahrzeuge_uebernommen"] == 1, log


def test_04_wiederaufnahme_nach_abbruch_zieht_den_fahrzeugstatus_nach(welt):
    """Gegenpruefung 12.09.2026: Bricht der Lauf mitten in Schritt 4b ab
    (Vorgang schon markiert, Fahrzeugstatus noch nicht abgeleitet), darf die
    Wiederaufnahme nicht daran vorbeilaufen. Vorher filterte die Auswahl nach
    der Markierung — das Fahrzeug waere fuer immer auf "gekauft" stehen
    geblieben, weil danach kein Statuswechsel mehr kommt."""
    w = welt
    CS = _modul("cleanup_service")
    cid, vid, kid = f"c4_{w.s}", f"v4_{w.s}", f"k4_{w.s}"

    async def lauf():
        await w.db.vehicles.insert_one(
            {"id": vid, "dealer_id": w.dealer_id, "owner_user_id": w.a["id"],
             "lifecycle": "gekauft", "created_at": _jetzt()})
        # Vertrag mit Grabstein, wie ihn ein abgebrochener Lauf hinterlaesst
        await w.db.generated_pdfs.insert_one({
            **_vertrag(w, cid, vid, w.a["id"]),
            "loeschung": {"status": "laeuft", "gestartet": _jetzt(),
                          "grund": "frist", "scrub_pii": False}})
        # Vorgang: bereits storniert UND markiert (Abbruch danach)
        await w.db.kaufvorgaenge.insert_one({
            **_vorgang(w, kid, cid, vid, w.a["id"], "storniert"),
            "storno_grund": "vertrag_geloescht",
            "vertrag_geloescht_am": _jetzt()})
        weg = await CS.vertrag_endgueltig_loeschen(
            w.db, cid, scrub_pii=False, grund="frist")
        v = await w.db.vehicles.find_one({"id": vid}, {"_id": 0})
        return weg, v

    weg, v = w.run(lauf())
    assert weg is True
    assert v["lifecycle"] != "gekauft", (
        f"Fahrzeug haengt auf '{v['lifecycle']}' — die Wiederaufnahme hat den "
        "Status nicht nachgezogen")
