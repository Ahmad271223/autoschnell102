# -*- coding: utf-8 -*-
"""Audit 13.09.2026, Bereich "termine": Protokoll-/Termin-/Fahrerlisten und
die Nacharbeit nach dem Anlegen eines Termins.

  #1   Sucher: Grenze 20 griff VOR dem Filter auf eigene Termine — fremde
       Versionen verdraengten das eigene Protokoll still
  #2   Chef: nach Versionsnummer gekappt — ein neues v1 fiel hinter alte
       Korrekturen; jetzt nach Abschlusszeit, Grenze mit Kopf + Warnung
  #5   create_appointment: Fehler NACH dem Insert gab 500 (Audit, Kaufvorgang);
       jetzt log_activity_sicher bzw. Merker nacharbeit_offen, den das
       naechste Speichern nachholt
  #6   /appointments: ab 2000 fielen die KOMMENDEN Termine weg
  #12  /driver/appointments: ab 500 fielen die NEUEN Fahrten weg
  #13  /driver/me und _verknuepfte_dealer_ids: harte Grenze 500 Firmen
  #60  PUT /driver/me liefert dieselbe Firmenliste wie #13

In-Prozess gegen eine Wegwerf-DB je Test (kein Server).
"""
import asyncio
import importlib
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import Response

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
    namen = ["deps", "routes.protocols", "routes.bestand", "routes.drivers",
             "routes.appointments", "routes.contracts", "kaufvorgang", "lifecycle",
             "auftraggeber"]
    mods = [_modul(n) for n in namen]
    alt = [(m, getattr(m, "db", None)) for m in mods]

    class _W:
        pass

    w = _W()
    w.s = s
    w.dealer_id = f"d_a13_{s}"
    w.chef = {"id": f"chef_a13_{s}", "dealer_id": w.dealer_id, "role": "dealer"}
    w.a = {"id": f"sa_a13_{s}", "dealer_id": w.dealer_id, "role": "sucher"}
    w.b = {"id": f"sb_a13_{s}", "dealer_id": w.dealer_id, "role": "sucher"}
    w.driver = {"id": f"f_a13_{s}", "display_name": "Fahrer A13",
                "email": f"f{s}@t.invalid", "driver_code": "A13" + s[:6].upper()}
    w.loop = asyncio.new_event_loop()
    asyncio.set_event_loop(w.loop)
    w.client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    w.db_name = f"autoschnell_wt_termine_{s}"
    w.db = w.client[w.db_name]
    for m in mods:
        if hasattr(m, "db"):
            m.db = w.db
    w.run = lambda coro: w.loop.run_until_complete(coro)

    w.run(w.db.users.insert_many([
        {**w.chef, "active": True, "email": f"chef{s}@t.invalid", "created_at": "2026-01-01T00:00:00+00:00"},
        {**w.a, "active": True, "email": f"a{s}@t.invalid", "created_at": _jetzt()},
        {**w.b, "active": True, "email": f"b{s}@t.invalid", "created_at": _jetzt()}]))
    w.run(w.db.dealers.insert_one({"id": w.dealer_id, "user_id": w.chef["id"],
                                   "company_name": "Firma A13", "created_at": _jetzt()}))
    yield w
    try:
        w.run(w.client.drop_database(w.db_name))
    finally:
        for m, d in alt:
            if d is not None:
                m.db = d
        w.client.close()
        w.loop.close()


def _protokoll(w, pid, termin, vid, version, minuten, superseded=False):
    return {"id": pid, "dealer_id": w.dealer_id, "vehicle_id": vid, "appointment_id": termin,
            "status": "final", "version": version, "superseded": superseded,
            "finalized_at": (datetime(2026, 9, 1, tzinfo=timezone.utc)
                             + timedelta(minutes=minuten)).isoformat(),
            "pdf_path": "x.pdf", "created_at": _jetzt()}


def _termin(w, tid, status="offen", pickup_date="2099-01-01", **extra):
    doc = {"id": tid, "dealer_id": w.dealer_id, "title": f"Fahrt {tid}", "status": status,
           "pickup_date": pickup_date, "created_by": w.chef["id"], "created_at": _jetzt()}
    doc.update(extra)
    return doc


def _tag(i):
    return (date(2020, 1, 1) + timedelta(days=i)).isoformat()


# ================================================= #1
def test_01_sucher_sieht_eigenes_protokoll_trotz_vieler_fremder_versionen(welt):
    P = _modul("routes.protocols")
    w = welt
    vid = f"v_{w.s}"

    async def lauf():
        await w.db.vehicles.insert_one({"id": vid, "dealer_id": w.dealer_id, "lifecycle": "abgeholt",
                                        "owner_user_id": w.a["id"], "mitbearbeiter_ids": [w.b["id"]],
                                        "data": {}, "created_at": _jetzt()})
        await w.db.appointments.insert_many([
            _termin(w, f"tA_{w.s}", "abgeholt", created_by=w.a["id"], vehicle_id=vid),
            _termin(w, f"tB_{w.s}", "abgeholt", created_by=w.b["id"], vehicle_id=vid)])
        # 21 Versionen am Termin von B (juenger), 1 eigenes Protokoll von A (aelter)
        await w.db.pickup_protocols.insert_many(
            [_protokoll(w, f"pB{i}_{w.s}", f"tB_{w.s}", vid, i, 1000 + i, superseded=i < 21)
             for i in range(1, 22)])
        await w.db.pickup_protocols.insert_one(_protokoll(w, f"pA_{w.s}", f"tA_{w.s}", vid, 1, 0))
        return (await P.dealer_list_protocols(vid, w.a),
                await P.dealer_list_protocols(vid, w.b))

    liste_a, liste_b = w.run(lauf())
    assert [p["id"] for p in liste_a] == [f"pA_{w.s}"], "eigenes Protokoll darf nicht verschwinden"
    assert "appointment_id" not in liste_a[0]
    ids_b = [p["id"] for p in liste_b]
    assert f"pA_{w.s}" not in ids_b and len(ids_b) == 21


# ================================================= #2
def test_02_chef_sieht_alle_versionen_juengstes_zuerst(welt, monkeypatch, caplog):
    P = _modul("routes.protocols")
    w = welt
    vid = f"v_{w.s}"

    async def lauf():
        await w.db.appointments.insert_many([_termin(w, f"t1_{w.s}", "abgeholt"),
                                             _termin(w, f"t2_{w.s}", "abgeholt")])
        await w.db.pickup_protocols.insert_many(
            [_protokoll(w, f"p1v{i}_{w.s}", f"t1_{w.s}", vid, i, i, superseded=i < 21)
             for i in range(1, 22)])
        await w.db.pickup_protocols.insert_one(_protokoll(w, f"p2_{w.s}", f"t2_{w.s}", vid, 1, 20000))
        voll = await P.dealer_list_protocols(vid, w.chef, Response())
        monkeypatch.setattr(P, "_PROTOKOLLE_JE_FAHRZEUG", 2)
        antwort = Response()
        gekappt = await P.dealer_list_protocols(vid, w.chef, antwort)
        return voll, gekappt, antwort

    voll, gekappt, antwort = w.run(lauf())
    assert len(voll) == 22
    assert voll[0]["id"] == f"p2_{w.s}", "juengstes Protokoll (neuer Termin, v1) zuerst"
    assert len(gekappt) == 2 and antwort.headers.get("X-Truncated") == "1"
    assert any("Obergrenze" in r.getMessage() for r in caplog.records)


# ================================================= #5
def _kauf_welt(w):
    vid, cid, kid = f"v_{w.s}", f"c_{w.s}", f"k_{w.s}"

    async def anlegen():
        await w.db.vehicles.insert_one({"id": vid, "dealer_id": w.dealer_id, "lifecycle": "vertrag_erstellt",
                                        "owner_user_id": w.chef["id"],
                                        "data": {"make_label": "BMW", "model_label": "320d"},
                                        "created_at": _jetzt()})
        await w.db.generated_pdfs.insert_one({"id": cid, "dealer_id": w.dealer_id, "user_id": w.chef["id"],
                                              "vehicle_id": vid, "kaufvorgang_id": kid, "status": "erstellt",
                                              "contract_data": {"seller_name": "Vera"}, "created_at": _jetzt()})
        await w.db.kaufvorgaenge.insert_one({"id": kid, "dealer_id": w.dealer_id, "user_id": w.chef["id"],
                                             "vehicle_id": vid, "contract_id": cid, "status": "vertrag_erstellt",
                                             "created_at": _jetzt(), "updated_at": _jetzt()})
    w.run(anlegen())
    return vid, cid, kid


async def _kaputt(*a, **k):
    raise RuntimeError("DB-Aussetzer (Test)")


def test_05a_audit_fehler_nach_dem_insert_gibt_kein_500(welt, monkeypatch):
    A = _modul("routes.appointments")
    deps = _modul("deps")
    w = welt
    vid, cid, _kid = _kauf_welt(w)
    monkeypatch.setattr(A, "log_activity", _kaputt)
    monkeypatch.setattr(deps, "log_activity", _kaputt)

    async def lauf():
        r = await A.create_appointment(
            A.AppointmentIn(contract_id=cid, vehicle_id=vid, pickup_date="2099-01-01"), w.chef)
        return r, await w.db.appointments.count_documents({"contract_id": cid})

    r, anzahl = w.run(lauf())
    assert r["id"] and anzahl == 1
    assert "hinweis" not in r, "ein fehlendes Audit allein braucht keinen Nutzerhinweis"


def test_05b_nacharbeit_scheitert_merker_und_naechstes_speichern_holt_nach(welt, monkeypatch):
    A = _modul("routes.appointments")
    KV = _modul("kaufvorgang")
    w = welt
    vid, cid, kid = _kauf_welt(w)
    echt = KV.termin_status_uebernehmen
    monkeypatch.setattr(KV, "termin_status_uebernehmen", _kaputt)

    async def anlegen():
        r = await A.create_appointment(
            A.AppointmentIn(contract_id=cid, vehicle_id=vid, pickup_date="2099-01-01"), w.chef)
        t = await w.db.appointments.find_one({"id": r["id"]}, {"_id": 0})
        kv = await w.db.kaufvorgaenge.find_one({"id": kid}, {"_id": 0})
        return r, t, kv

    r, termin, kv = w.run(anlegen())
    assert r["hinweis"] == A.NACHARBEIT_HINWEIS
    assert termin.get("nacharbeit_offen") is True
    assert kv["status"] == "vertrag_erstellt"

    monkeypatch.setattr(KV, "termin_status_uebernehmen", echt)

    async def speichern():
        out = await A.update_appointment(r["id"], A.AppointmentIn(notes="x"), w.chef)
        t = await w.db.appointments.find_one({"id": r["id"]}, {"_id": 0})
        kv = await w.db.kaufvorgaenge.find_one({"id": kid}, {"_id": 0})
        c = await w.db.generated_pdfs.find_one({"id": cid}, {"_id": 0})
        v = await w.db.vehicles.find_one({"id": vid}, {"_id": 0})
        return out, t, kv, c, v

    out, termin, kv, vertrag, fahrzeug = w.run(speichern())
    assert out["ok"] is True
    assert "nacharbeit_offen" not in termin
    assert kv["status"] == "abholung_geplant" and kv.get("appointment_id") == r["id"]
    assert vertrag["appointment_id"] == r["id"]
    assert fahrzeug["lifecycle"] == "abholung_geplant"


def test_05c_ohne_vorgang_holt_das_speichern_den_fahrzeugstatus_nach(welt, monkeypatch):
    A = _modul("routes.appointments")
    w = welt
    vid = f"v_{w.s}"
    w.run(w.db.vehicles.insert_one({"id": vid, "dealer_id": w.dealer_id, "lifecycle": "vertrag_erstellt",
                                    "owner_user_id": w.chef["id"], "data": {}, "created_at": _jetzt()}))
    echt = A.try_set_lifecycle
    monkeypatch.setattr(A, "try_set_lifecycle", _kaputt)
    r = w.run(A.create_appointment(A.AppointmentIn(vehicle_id=vid, pickup_date="2099-01-01"), w.chef))
    assert r["hinweis"] == A.NACHARBEIT_HINWEIS
    monkeypatch.setattr(A, "try_set_lifecycle", echt)
    w.run(A.update_appointment(r["id"], A.AppointmentIn(notes="x"), w.chef))
    fahrzeug = w.run(w.db.vehicles.find_one({"id": vid}, {"_id": 0}))
    termin = w.run(w.db.appointments.find_one({"id": r["id"]}, {"_id": 0}))
    assert fahrzeug["lifecycle"] == "abholung_geplant"
    assert "nacharbeit_offen" not in termin


def test_05d_ohne_vertrag_bleiben_doppelte_termine_moeglich(welt):
    """Fall B bewusst NICHT behoben: ohne Idempotenzschluessel des Clients legt
    ein wiederholter Klick einen zweiten Termin an (dokumentiert)."""
    A = _modul("routes.appointments")
    w = welt
    w.run(A.create_appointment(A.AppointmentIn(title="x", pickup_date="2099-01-01"), w.chef))
    w.run(A.create_appointment(A.AppointmentIn(title="x", pickup_date="2099-01-01"), w.chef))
    assert w.run(w.db.appointments.count_documents({"dealer_id": w.dealer_id})) == 2


# ================================================= #6
def test_06_terminliste_behaelt_kommende_termine_bei_ueberlauf(welt):
    A = _modul("routes.appointments")
    w = welt

    async def lauf():
        await w.db.appointments.insert_many(
            [_termin(w, f"alt{i}_{w.s}", "abgeholt", _tag(i)) for i in range(2000)])
        await w.db.appointments.insert_many([
            _termin(w, f"kommend_{w.s}", "offen", "2099-01-01"),
            _termin(w, f"ohne_datum_{w.s}", "offen", ""),
            {k: v for k, v in _termin(w, f"ohne_status_{w.s}", pickup_date="2098-01-01").items()
             if k != "status"}])
        antwort = Response()
        ohne_filter = await A.list_appointments(antwort, w.chef)
        antwort2 = Response()
        abgeholt = await A.list_appointments(antwort2, w.chef, status="abgeholt")
        return ohne_filter, antwort, abgeholt, antwort2

    items, antwort, abgeholt, antwort2 = w.run(lauf())
    ids = {a["id"] for a in items}
    assert {f"kommend_{w.s}", f"ohne_datum_{w.s}", f"ohne_status_{w.s}"} <= ids
    assert len(items) == 2000 and antwort.headers.get("X-Truncated") == "1"
    assert f"alt1999_{w.s}" in ids and f"alt0_{w.s}" not in ids
    daten = [a.get("pickup_date") or "" for a in items]
    assert daten == sorted(daten), "Antwort bleibt aufsteigend (Termine.jsx 'Kommend')"

    ids2 = [a["id"] for a in abgeholt]
    # genau 2000 Treffer = Grenze erreicht -> Kopf (wie bisher ">= 2000")
    assert len(ids2) == 2000 and antwort2.headers.get("X-Truncated") == "1"
    daten2 = [a.get("pickup_date") or "" for a in abgeholt]
    assert daten2 == sorted(daten2) and ids2[-1] == f"alt1999_{w.s}"


def test_06b_kleine_liste_ohne_kopf_und_aufsteigend(welt):
    A = _modul("routes.appointments")
    w = welt

    async def lauf():
        await w.db.appointments.insert_many([
            _termin(w, f"x1_{w.s}", "abgeholt", "2026-01-02"),
            _termin(w, f"x2_{w.s}", "offen", "2026-01-01"),
            _termin(w, f"x3_{w.s}", "storniert", "2026-01-03")])
        antwort = Response()
        return await A.list_appointments(antwort, w.chef), antwort

    items, antwort = w.run(lauf())
    assert [a["id"] for a in items] == [f"x2_{w.s}", f"x1_{w.s}", f"x3_{w.s}"]
    assert antwort.headers.get("X-Truncated") is None


# ================================================= #12
def _fahrer_welt(w):
    async def anlegen():
        await w.db.driver_accounts.insert_one({**w.driver, "active": True, "created_at": _jetzt()})
        await w.db.dealer_drivers.insert_one({"id": str(uuid.uuid4()), "dealer_id": w.dealer_id,
                                              "driver_account_id": w.driver["id"],
                                              "display_name": w.driver["display_name"],
                                              "added_at": _jetzt()})
    w.run(anlegen())


def test_12_fahrer_sieht_neue_fahrt_trotz_langer_historie(welt):
    D = _modul("routes.drivers")
    w = welt
    _fahrer_welt(w)

    async def lauf():
        await w.db.appointments.insert_many(
            [_termin(w, f"alt{i}_{w.s}", "abgeholt", _tag(i), driver_id=w.driver["id"])
             for i in range(500)])
        await w.db.appointments.insert_one(
            _termin(w, f"neu_{w.s}", "offen", "2099-01-01", driver_id=w.driver["id"], zuteilung="offen"))
        antwort = Response()
        return await D.driver_appointments(w.driver, antwort), antwort

    appts, antwort = w.run(lauf())
    ids = [a["id"] for a in appts]
    assert f"neu_{w.s}" in ids, "neu zugeteilte Fahrt muss erscheinen"
    assert len(ids) == 500 and antwort.headers.get("X-Truncated") == "1"
    assert f"alt499_{w.s}" in ids and f"alt0_{w.s}" not in ids
    assert ids == [a["id"] for a in sorted(appts, key=lambda a: a.get("pickup_date") or "")]


def test_12b_grenze_voll_mit_offenen_keine_unbegrenzte_rueckgabe(welt):
    D = _modul("routes.drivers")
    w = welt
    _fahrer_welt(w)

    async def lauf():
        await w.db.appointments.insert_many(
            [_termin(w, f"o{i}_{w.s}", "offen", "2099-01-01", driver_id=w.driver["id"])
             for i in range(500)])
        await w.db.appointments.insert_one(
            _termin(w, f"zu_{w.s}", "abgeholt", "2020-01-01", driver_id=w.driver["id"]))
        return await D.driver_appointments(w.driver)

    ids = [a["id"] for a in w.run(lauf())]
    assert len(ids) == 500 and f"zu_{w.s}" not in ids


# ================================================= #13 / #60
def test_13_60_fahrer_firmenliste_ohne_500er_grenze(welt):
    D = _modul("routes.drivers")
    w = welt

    async def lauf():
        await w.db.driver_accounts.insert_one({**w.driver, "active": True, "created_at": _jetzt()})
        await w.db.dealer_drivers.insert_many(
            [{"id": str(uuid.uuid4()), "dealer_id": f"d13_{i}_{w.s}", "driver_account_id": w.driver["id"],
              "display_name": w.driver["display_name"], "added_at": _jetzt()} for i in range(501)])
        me = await D.driver_me(w.driver)
        verknuepft = await D._verknuepfte_dealer_ids(w.driver["id"])
        neu = await D.driver_update_me(D.DriverProfileUpdate(display_name="Neu Name"), w.driver)
        return me, verknuepft, neu

    me, verknuepft, neu = w.run(lauf())
    assert len(me["dealers"]) == 501
    assert len(verknuepft) == 501
    assert len(neu["dealers"]) == 501 and neu["display_name"] == "Neu Name"
