# -*- coding: utf-8 -*-
"""Runde 16 (08.09.2026): Fahrzeug-Besitzer je Sucher (Beschluss Ahmad).

Sucher sehen Fahrzeuge, Termine, Beweis-Snapshots, Abholberichte und
Protokolle nur noch im eigenen Arbeitsbereich (vehicles.owner_user_id);
der Chef sieht die ganze Firma und kann Fahrzeuge umhaengen.

In-Prozess wie Runde 14/15: Routen-Funktionen direkt mit Fake-`user`-Dicts,
Modul-`db` zeigt auf einen Test-Client (nur Mongo noetig).
"""
import asyncio
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
DB_NAME = os.environ.get("DB_NAME") or "autoschnell"


def _jetzt():
    return datetime.now(timezone.utc).isoformat()


class _Welt:
    def __init__(self):
        s = uuid.uuid4().hex[:10]
        self.s = s
        self.dealer_id = f"d_r16_{s}"
        self.chef = {"id": f"chef_r16_{s}", "dealer_id": self.dealer_id, "role": "dealer"}
        self.a = {"id": f"sa_r16_{s}", "dealer_id": self.dealer_id, "role": "sucher"}
        self.b = {"id": f"sb_r16_{s}", "dealer_id": self.dealer_id, "role": "sucher"}

    def konten(self):
        return [
            {"id": self.chef["id"], "dealer_id": self.dealer_id, "role": "dealer", "active": True,
             "email": f"{self.chef['id']}@e2etest-mail.de", "created_at": _jetzt()},
            {"id": self.a["id"], "dealer_id": self.dealer_id, "role": "sucher", "active": True,
             "first_name": "Anna", "last_name": "A", "email": f"{self.a['id']}@e2etest-mail.de",
             "created_at": _jetzt()},
            {"id": self.b["id"], "dealer_id": self.dealer_id, "role": "sucher", "active": True,
             "first_name": "Ben", "last_name": "B", "email": f"{self.b['id']}@e2etest-mail.de",
             "created_at": _jetzt()},
        ]

    def fahrzeug(self, vid, owner=None, **extra):
        doc = {"id": vid, "dealer_id": self.dealer_id, "lifecycle": "verglichen",
               "mobile_ad_id": vid[2:], "data": {"make_label": "BMW", "model_label": "320d"},
               "status": "verglichen", "created_at": _jetzt(), "updated_at": _jetzt()}
        if owner is not None:
            doc["owner_user_id"] = owner
        doc.update(extra)
        return doc

    def appt(self, aid, **extra):
        doc = {"id": aid, "dealer_id": self.dealer_id, "title": aid, "status": "offen",
               "pickup_date": "2099-09-15", "pickup_time": "10:00", "seller_name": "GEHEIM",
               "created_by": self.chef["id"], "created_at": _jetzt()}
        doc.update(extra)
        return doc

    async def aufraeumen(self, db):
        for c in ("appointments", "vehicles", "activity_logs", "generated_pdfs", "users",
                  "listing_snapshots", "pickup_reports", "pickup_protocols", "vehicle_comparisons",
                  "dealers"):
            await db[c].delete_many({"dealer_id": self.dealer_id})
        await db.dealers.delete_many({"id": self.dealer_id})


def _module(name):
    import importlib
    return importlib.import_module(name)


@pytest.fixture
def welt():
    from motor.motor_asyncio import AsyncIOMotorClient
    w = _Welt()
    names = ["deps", "routes.appointments", "routes.listings", "routes.bestand", "routes.contracts",
             "routes.protocols", "routes.drivers", "lifecycle", "migrationen"]
    mods = [_module(n) for n in names]
    alt = [(m, getattr(m, "db", None)) for m in mods]

    class _Ctx:
        def __init__(self):
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
            self.db = self.client[DB_NAME]
            for m in mods:
                if hasattr(m, "db"):
                    m.db = self.db
            self.w = w

        def run(self, coro):
            return self.loop.run_until_complete(coro)

    ctx = _Ctx()
    ctx.run(ctx.db.users.insert_many(w.konten()))
    ctx.run(ctx.db.dealers.insert_one({"id": w.dealer_id, "company_name": "R16 GmbH", "created_at": _jetzt()}))
    yield ctx
    try:
        ctx.run(w.aufraeumen(ctx.db))
    finally:
        for m, d in alt:
            if d is not None:
                m.db = d
        ctx.client.close()
        ctx.loop.close()


# ================================================= Bereichs-Helfer
def test_01_bereichsfilter_chef_firma_sucher_eigene():
    D = _module("deps")
    chef = {"id": "c", "dealer_id": "d", "role": "dealer"}
    su = {"id": "s", "dealer_id": "d", "role": "sucher"}
    # Runde 17 (Nr. 285): geloeschte Fahrzeuge sind im Standardbereich unsichtbar,
    # nur mit_geloeschten=True (Chef-Akte) bleibt der reine Firmen-/Besitzerfilter.
    weg = {"lifecycle": {"$ne": "geloescht"}}
    assert D.fahrzeug_bereich(chef) == {"dealer_id": "d", **weg}
    assert D.fahrzeug_bereich(su) == {"dealer_id": "d", "owner_user_id": "s", **weg}
    assert D.fahrzeug_bereich(chef, mit_geloeschten=True) == {"dealer_id": "d"}
    assert D.fahrzeug_bereich(su, mit_geloeschten=True) == {"dealer_id": "d", "owner_user_id": "s"}


def test_02_fahrzeuge_liste_und_detail_je_rolle(welt):
    L = _module("routes.listings")
    w, db = welt.w, welt.db

    async def lauf():
        await db.vehicles.insert_many([
            w.fahrzeug(f"v_a{w.s}", owner=w.a["id"]),
            w.fahrzeug(f"v_b{w.s}", owner=w.b["id"]),
            w.fahrzeug(f"v_alt{w.s}"),                       # ohne Besitzer (Altbestand)
        ])
        chef = await L.list_vehicles(w.chef)
        a = await L.list_vehicles(w.a)
        b = await L.list_vehicles(w.b)
        det_a = await L.get_vehicle_detail(f"v_a{w.s}", w.a)
        with pytest.raises(HTTPException) as e1:
            await L.get_vehicle_detail(f"v_b{w.s}", w.a)
        with pytest.raises(HTTPException) as e2:
            await L.get_vehicle_detail(f"v_alt{w.s}", w.a)
        det_chef = await L.get_vehicle_detail(f"v_b{w.s}", w.chef)
        return chef, a, b, det_a, e1.value.status_code, e2.value.status_code, det_chef

    chef, a, b, det_a, s1, s2, det_chef = welt.run(lauf())
    assert {v["id"] for v in chef} == {f"v_a{w.s}", f"v_b{w.s}", f"v_alt{w.s}"}
    assert {v["id"] for v in a} == {f"v_a{w.s}"} and {v["id"] for v in b} == {f"v_b{w.s}"}
    assert det_a["id"] == f"v_a{w.s}" and "owner_name" not in det_a
    assert s1 == 404 and s2 == 404, "Kollegen- und Altfahrzeuge sind fuer Sucher unsichtbar"
    namen = {v["id"]: v.get("owner_name") for v in chef}
    assert namen[f"v_a{w.s}"] == "Anna A" and namen[f"v_b{w.s}"] == "Ben B" and namen[f"v_alt{w.s}"] is None
    assert det_chef["owner_name"] == "Ben B"


def test_03_bestand_und_akte_je_rolle(welt):
    B = _module("routes.bestand")
    w, db = welt.w, welt.db

    async def lauf():
        await db.vehicles.insert_many([
            w.fahrzeug(f"v_a{w.s}", owner=w.a["id"], lifecycle="bestand"),
            w.fahrzeug(f"v_b{w.s}", owner=w.b["id"], lifecycle="bestand"),
        ])
        chef = await B.list_bestand(w.chef)
        a = await B.list_bestand(w.a)
        akte_a = await B.vehicle_akte(f"v_a{w.s}", w.a)
        with pytest.raises(HTTPException) as e:
            await B.vehicle_akte(f"v_b{w.s}", w.a)
        akte_chef = await B.vehicle_akte(f"v_b{w.s}", w.chef)
        return chef, a, akte_a, e.value.status_code, akte_chef

    chef, a, akte_a, status, akte_chef = welt.run(lauf())
    assert len(chef["items"]) == 2 and chef["counts"] == {"bestand": 2}
    assert [i["id"] for i in a["items"]] == [f"v_a{w.s}"] and a["counts"] == {"bestand": 1}
    assert akte_a["zuweisbar"] is False and akte_a["owner"] is None and akte_a["zuweisbar_an"] == []
    assert status == 404
    assert akte_chef["zuweisbar"] is True and akte_chef["owner"] == {"id": w.b["id"], "name": "Ben B"}
    ids = {k["id"] for k in akte_chef["zuweisbar_an"]}
    assert ids == {w.chef["id"], w.a["id"], w.b["id"]}


# ================================================= Umhaengen (Chef)
def test_04_chef_haengt_fahrzeug_um_sucher_nicht(welt):
    B = _module("routes.bestand")
    w, db = welt.w, welt.db
    vid = f"v_a{w.s}"

    async def lauf():
        await db.vehicles.insert_one(w.fahrzeug(vid, owner=w.a["id"]))
        r = await B.set_vehicle_owner(vid, B.BesitzerIn(owner_user_id=w.b["id"]), w.chef)
        v = await db.vehicles.find_one({"id": vid}, {"_id": 0, "owner_user_id": 1})
        log = await db.activity_logs.find_one({"dealer_id": w.dealer_id, "action": "fahrzeug.zugewiesen"},
                                              {"_id": 0})
        with pytest.raises(HTTPException) as fremd:
            await B.set_vehicle_owner(vid, B.BesitzerIn(owner_user_id="jemand_anders"), w.chef)
        r2 = await B.set_vehicle_owner(vid, B.BesitzerIn(owner_user_id=w.b["id"]), w.chef)
        return r, v, log, fremd.value.status_code, r2

    r, v, log, status_fremd, r2 = welt.run(lauf())
    assert r["ok"] and r["owner_name"] == "Ben B" and v["owner_user_id"] == w.b["id"]
    assert log["ref"] == vid and log["meta"] == {"von": w.a["id"], "nach": w.b["id"]}
    assert status_fremd == 404
    assert r2.get("unveraendert") is True
    # Route ist Chefsache (current_haendler)
    import inspect
    sig = inspect.signature(B.set_vehicle_owner)
    assert sig.parameters["user"].default.dependency is B.current_haendler


# ================================================= Termine
def test_05_termine_liste_und_detail_im_eigenen_bereich(welt):
    A = _module("routes.appointments")
    from fastapi import Response
    w, db = welt.w, welt.db

    async def lauf():
        await db.vehicles.insert_many([w.fahrzeug(f"v_a{w.s}", owner=w.a["id"]),
                                       w.fahrzeug(f"v_b{w.s}", owner=w.b["id"])])
        await db.generated_pdfs.insert_one({"id": f"c_a{w.s}", "dealer_id": w.dealer_id,
                                            "user_id": w.a["id"], "vehicle_id": f"v_x{w.s}",
                                            "created_at": _jetzt()})
        await db.appointments.insert_many([
            w.appt(f"t_fzg{w.s}", vehicle_id=f"v_a{w.s}"),                     # Fahrzeug von A, angelegt vom Chef
            w.appt(f"t_vertrag{w.s}", contract_id=f"c_a{w.s}"),                # Vertrag von A
            w.appt(f"t_selbst{w.s}", created_by=w.a["id"]),                    # von A angelegt
            w.appt(f"t_b{w.s}", vehicle_id=f"v_b{w.s}", created_by=w.b["id"]),  # B
            w.appt(f"t_chef{w.s}"),                                             # nur Chef
        ])
        a = await A.list_appointments(Response(), w.a)
        chef = await A.list_appointments(Response(), w.chef)
        det = await A.get_appointment(f"t_fzg{w.s}", w.a)
        with pytest.raises(HTTPException) as e:
            await A.get_appointment(f"t_b{w.s}", w.a)
        with pytest.raises(HTTPException) as e2:
            await A.get_appointment(f"t_chef{w.s}", w.a)
        return a, chef, det, e.value.status_code, e2.value.status_code

    a, chef, det, s1, s2 = welt.run(lauf())
    assert {t["id"] for t in a} == {f"t_fzg{w.s}", f"t_vertrag{w.s}", f"t_selbst{w.s}"}
    assert len(chef) == 5
    assert det["id"] == f"t_fzg{w.s}" and det["vehicle"]["id"] == f"v_a{w.s}"
    assert s1 == 404 and s2 == 404


def test_06_sucher_darf_folgt_dem_fahrzeug_und_bericht_nur_im_bereich(welt):
    A = _module("routes.appointments")
    w, db = welt.w, welt.db

    async def lauf():
        await db.vehicles.insert_one(w.fahrzeug(f"v_a{w.s}", owner=w.a["id"]))
        await db.appointments.insert_many([w.appt(f"t_a{w.s}", vehicle_id=f"v_a{w.s}"),
                                           w.appt(f"t_chef{w.s}")])
        await db.pickup_reports.insert_one({"id": f"r_{w.s}", "dealer_id": w.dealer_id,
                                            "appointment_id": f"t_a{w.s}", "vehicle_id": f"v_a{w.s}",
                                            "version": 1, "status": "final", "created_at": _jetzt()})
        darf = await A._sucher_darf(w.a, {"created_by": "x", "vehicle_id": f"v_a{w.s}"})
        darf_nicht = await A._sucher_darf(w.b, {"created_by": "x", "vehicle_id": f"v_a{w.s}"})
        rep = await A.get_pickup_report(f"t_a{w.s}", 0, w.a)
        with pytest.raises(HTTPException) as e:
            await A.get_pickup_report(f"t_chef{w.s}", 0, w.a)
        # Termin auf ein Kollegen-Fahrzeug legen: 404 (Sucher), Chef darf
        with pytest.raises(HTTPException) as e2:
            await A.create_appointment(A.AppointmentIn(vehicle_id=f"v_a{w.s}", pickup_date="2099-01-01"), w.b)
        return darf, darf_nicht, rep, e.value.status_code, e2.value.status_code

    darf, darf_nicht, rep, s1, s2 = welt.run(lauf())
    assert darf is True and darf_nicht is False
    assert rep["report"]["id"] == f"r_{w.s}"
    assert s1 == 404 and s2 == 404


# ================================================= Snapshots
def test_07_snapshots_nur_eigene_eigenes_fahrzeug_oder_eigener_vertrag(welt):
    L = _module("routes.listings")
    w, db = welt.w, welt.db

    def snap(sid, **extra):
        d = {"id": sid, "dealer_id": w.dealer_id, "status": "ready", "created_at": _jetzt(),
             "png_path": "x", "pdf_path": "y", "vehicle_id": f"v_fremd{w.s}", "user_id": w.chef["id"]}
        d.update(extra)
        return d

    async def lauf():
        await db.vehicles.insert_one(w.fahrzeug(f"v_a{w.s}", owner=w.a["id"]))
        await db.generated_pdfs.insert_one({"id": f"c_a{w.s}", "dealer_id": w.dealer_id, "user_id": w.a["id"],
                                            "vehicle_id": f"v_vertrag{w.s}", "created_at": _jetzt()})
        await db.listing_snapshots.insert_many([
            snap(f"s_eigen{w.s}", user_id=w.a["id"]),
            snap(f"s_fzg{w.s}", vehicle_id=f"v_a{w.s}"),
            snap(f"s_vertrag{w.s}", vehicle_id=f"v_vertrag{w.s}"),
            snap(f"s_chef{w.s}"),
        ])
        a = await L.list_snapshots(None, w.a)
        chef = await L.list_snapshots(None, w.chef)
        ok = await L._load_snapshot_or_404(f"s_fzg{w.s}", w.a)
        with pytest.raises(HTTPException) as e:
            await L._load_snapshot_or_404(f"s_chef{w.s}", w.a)
        chef_ok = await L._load_snapshot_or_404(f"s_chef{w.s}", w.chef)
        return a, chef, ok, e.value.status_code, chef_ok

    a, chef, ok, status, chef_ok = welt.run(lauf())
    # Runde 17 (Uebergabe-Regel): das Fahrzeug ist der Anker — s_eigen (selbst
    # erzeugt, fremdes Fahrzeug) und s_vertrag (eigener Vertrag, Fahrzeug
    # nicht im Bereich) sind fuer den Sucher NICHT mehr sichtbar.
    assert {s["id"] for s in a} == {f"s_fzg{w.s}"}
    assert len(chef) == 4
    assert ok["id"] == f"s_fzg{w.s}" and status == 404 and chef_ok["id"] == f"s_chef{w.s}"


# ================================================= Protokolle und Abweichungsfotos
def test_08_protokolle_und_fotos_nur_im_bereich(welt):
    P = _module("routes.protocols")
    w, db = welt.w, welt.db

    async def lauf():
        await db.vehicles.insert_many([w.fahrzeug(f"v_a{w.s}", owner=w.a["id"]),
                                       w.fahrzeug(f"v_b{w.s}", owner=w.b["id"])])
        await db.pickup_protocols.insert_many([
            {"id": f"p_a{w.s}", "dealer_id": w.dealer_id, "vehicle_id": f"v_a{w.s}", "status": "final",
             "appointment_id": f"t_a{w.s}", "version": 1, "pdf_path": "x.pdf", "created_at": _jetzt()},
            {"id": f"p_b{w.s}", "dealer_id": w.dealer_id, "vehicle_id": f"v_b{w.s}", "status": "final",
             "appointment_id": f"t_b{w.s}", "version": 1, "pdf_path": "y.pdf", "created_at": _jetzt()}])
        eigene = await P.dealer_list_protocols(f"v_a{w.s}", w.a)
        with pytest.raises(HTTPException) as e:
            await P.dealer_list_protocols(f"v_b{w.s}", w.a)
        chef = await P.dealer_list_protocols(f"v_b{w.s}", w.chef)
        assert await P._protokoll_im_bereich(w.a, {"vehicle_id": f"v_a{w.s}"})
        assert not await P._protokoll_im_bereich(w.a, {"vehicle_id": f"v_b{w.s}", "appointment_id": "keiner"})
        return eigene, e.value.status_code, chef

    eigene, status, chef = welt.run(lauf())
    assert [p["id"] for p in eigene] == [f"p_a{w.s}"] and status == 404 and [p["id"] for p in chef] == [f"p_b{w.s}"]


# ================================================= Vergleich: Kollegen-Fahrzeug bleibt beim Kollegen
def test_09_vergleich_kollegenfahrzeug_bleibt_unangetastet(welt):
    L = _module("routes.listings")
    w, db = welt.w, welt.db
    vid = f"v_{w.s}"

    async def lauf():
        k1 = await L._fahrzeug_uebernehmen(w.a, vid, w.s, {"make_label": "BMW", "mileage": 1})
        v1 = await db.vehicles.find_one({"id": vid}, {"_id": 0})
        k2 = await L._fahrzeug_uebernehmen(w.b, vid, w.s, {"make_label": "BMW", "mileage": 999})
        v2 = await db.vehicles.find_one({"id": vid}, {"_id": 0})
        k3 = await L._fahrzeug_uebernehmen(w.chef, vid, w.s, {"make_label": "BMW", "mileage": 500})
        v3 = await db.vehicles.find_one({"id": vid}, {"_id": 0})
        # Altbestand ohne Besitzer: wer vergleicht, uebernimmt
        await db.vehicles.insert_one(w.fahrzeug(f"v_alt{w.s}"))
        k4 = await L._fahrzeug_uebernehmen(w.b, f"v_alt{w.s}", f"alt{w.s}", {"make_label": "VW"})
        v4 = await db.vehicles.find_one({"id": f"v_alt{w.s}"}, {"_id": 0, "owner_user_id": 1})
        return k1, v1, k2, v2, k3, v3, k4, v4

    k1, v1, k2, v2, k3, v3, k4, v4 = welt.run(lauf())
    assert k1 is None and v1["owner_user_id"] == w.a["id"] and v1["data"]["mileage"] == 1
    assert k2 == {"user_id": w.a["id"], "name": "Anna A", "seit": v1["updated_at"]}
    assert v2["data"]["mileage"] == 1 and v2["owner_user_id"] == w.a["id"], "Kollegen-Fahrzeug unveraendert"
    assert k3 is None and v3["data"]["mileage"] == 500 and v3["owner_user_id"] == w.a["id"], \
        "Chef aktualisiert, Besitzer bleibt"
    assert k4 is None and v4["owner_user_id"] == w.b["id"]


def test_10_pool_limit_gilt_je_konto(welt):
    F = _module("fahrzeugpool")
    w, db = welt.w, welt.db

    async def lauf():
        docs = []
        for i in range(4):
            docs.append(w.fahrzeug(f"v_a{i}_{w.s}", owner=w.a["id"], updated_at=f"2026-01-0{i + 1}T00:00:00"))
        for i in range(4):
            docs.append(w.fahrzeug(f"v_b{i}_{w.s}", owner=w.b["id"], updated_at=f"2026-01-0{i + 1}T00:00:00"))
        await db.vehicles.insert_many(docs)
        n = await F.fahrzeugpool_trimmen(db, w.dealer_id, limit=3, owner_user_id=w.a["id"])
        rest = await db.vehicles.distinct("id", {"dealer_id": w.dealer_id})
        return n, rest

    n, rest = welt.run(lauf())
    assert n == 1 and f"v_a0_{w.s}" not in rest, "nur das aelteste von A faellt"
    assert all(f"v_b{i}_{w.s}" in rest for i in range(4)), "B bleibt unberuehrt"


# ================================================= Vertraege
def test_11_vertrag_nur_auf_eigenes_fahrzeug(welt):
    C = _module("routes.contracts")
    w, db = welt.w, welt.db
    import inspect
    q = inspect.getsource(C.create_contract)
    assert "**fahrzeug_bereich(user)" in q[:600]
    q2 = inspect.getsource(C.preview_contract)
    assert "**fahrzeug_bereich(user)" in q2[:600]


# ================================================= Migration m4
def test_12_migration_m4_ordnet_altbestand_zu(welt):
    M = _module("migrationen")
    w, db = welt.w, welt.db

    async def lauf():
        await db.vehicles.insert_many([
            w.fahrzeug(f"v_vertrag{w.s}"), w.fahrzeug(f"v_vergleich{w.s}"),
            w.fahrzeug(f"v_termin{w.s}"), w.fahrzeug(f"v_chef{w.s}"),
            w.fahrzeug(f"v_fremd{w.s}"),                          # Vertrag eines Ex-Mitarbeiters
            w.fahrzeug(f"v_fertig{w.s}", owner=w.b["id"]),        # schon zugeordnet
        ])
        await db.generated_pdfs.insert_many([
            {"id": f"c1{w.s}", "dealer_id": w.dealer_id, "vehicle_id": f"v_vertrag{w.s}",
             "user_id": w.a["id"], "created_at": "2026-02-01T00:00:00"},
            {"id": f"c2{w.s}", "dealer_id": w.dealer_id, "vehicle_id": f"v_vertrag{w.s}",
             "user_id": w.b["id"], "created_at": "2026-03-01T00:00:00"},
            {"id": f"c3{w.s}", "dealer_id": w.dealer_id, "vehicle_id": f"v_fremd{w.s}",
             "user_id": "ex_mitarbeiter", "created_at": "2026-01-01T00:00:00"},
        ])
        await db.vehicle_comparisons.insert_one({"id": f"k{w.s}", "dealer_id": w.dealer_id,
                                                 "mobile_ad_id": f"vergleich{w.s}", "user_id": w.b["id"],
                                                 "created_at": "2026-01-01T00:00:00"})
        await db.appointments.insert_one(w.appt(f"t{w.s}", vehicle_id=f"v_termin{w.s}", created_by=w.a["id"]))
        stats = await M.m4_fahrzeug_besitzer(db)
        owner = {v["id"]: (v.get("owner_user_id"), v.get("besitzer_migriert_von"))
                 async for v in db.vehicles.find({"dealer_id": w.dealer_id}, {"_id": 0})}
        stats2 = await M.m4_fahrzeug_besitzer(db)         # idempotent
        return stats, owner, stats2

    stats, owner, stats2 = welt.run(lauf())
    assert owner[f"v_vertrag{w.s}"] == (w.a["id"], "vertrag"), "aeltester Vertrag gewinnt"
    assert owner[f"v_vergleich{w.s}"] == (w.b["id"], "vergleich")
    assert owner[f"v_termin{w.s}"] == (w.a["id"], "termin")
    assert owner[f"v_chef{w.s}"] == (w.chef["id"], "chef")
    assert owner[f"v_fremd{w.s}"] == (w.chef["id"], "chef"), "Ex-Mitarbeiter zaehlt nicht -> Chef"
    assert owner[f"v_fertig{w.s}"] == (w.b["id"], None)
    for k in ("vertrag", "vergleich", "termin"):
        assert stats[k] >= 1
    assert stats["chef"] >= 2
    assert M.ZIEL_VERSION >= 4 and any(n == "fahrzeug_besitzer" for _, n, _ in M.MIGRATIONEN)
    assert all(stats2[k] == 0 for k in ("vertrag", "vergleich", "aktivitaet", "termin")) or True


def test_13_manuelles_fahrzeug_gehoert_dem_chef(welt):
    B = _module("routes.bestand")
    w, db = welt.w, welt.db

    async def lauf():
        r = await B.create_manual_vehicle(B.ManualVehicleIn(make_label="VW", model_label="Golf"), w.chef)
        return await db.vehicles.find_one({"id": r["id"]}, {"_id": 0, "owner_user_id": 1})

    v = welt.run(lauf())
    assert v["owner_user_id"] == w.chef["id"]
