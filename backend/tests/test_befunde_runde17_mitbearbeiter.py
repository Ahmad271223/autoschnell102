# -*- coding: utf-8 -*-
"""Wunsch Ahmad 09.09.2026: Vergleichen zwei Sucher derselben Firma dasselbe
Auto, duerfen BEIDE einen Kaufvertrag anlegen. Umsetzung: Mitbearbeiter am
Fahrzeug (vehicles.mitbearbeiter_ids) — das Fahrzeug liegt im Bereich beider,
der Abholtermin bleibt einmalig je Fahrzeug und wird vom Kollegen nicht
uebernommen. Der Hauptbearbeiter (owner_user_id) bleibt, wer zuerst verglich.
"""
import asyncio
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException, Response

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
DB_NAME = os.environ.get("DB_NAME") or "autoschnell"


def _jetzt():
    return datetime.now(timezone.utc).isoformat()


def _module(name):
    import importlib
    return importlib.import_module(name)


@pytest.fixture
def welt():
    from motor.motor_asyncio import AsyncIOMotorClient
    s = uuid.uuid4().hex[:10]
    names = ["deps", "routes.listings", "routes.contracts", "routes.appointments", "routes.bestand",
             "routes.protocols", "lifecycle"]
    mods = [_module(n) for n in names]
    alt = [(m, getattr(m, "db", None)) for m in mods]

    class _Ctx:
        def __init__(self):
            self.s = s
            self.dealer_id = f"d_r17m_{s}"
            self.chef = {"id": f"chef_r17m_{s}", "dealer_id": self.dealer_id, "role": "dealer"}
            self.a = {"id": f"sa_r17m_{s}", "dealer_id": self.dealer_id, "role": "sucher"}
            self.b = {"id": f"sb_r17m_{s}", "dealer_id": self.dealer_id, "role": "sucher"}
            self.c = {"id": f"sc_r17m_{s}", "dealer_id": self.dealer_id, "role": "sucher"}
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
            self.db = self.client[DB_NAME]
            for m in mods:
                if hasattr(m, "db"):
                    m.db = self.db

        def run(self, coro):
            return self.loop.run_until_complete(coro)

    ctx = _Ctx()
    ctx.run(ctx.db.users.insert_many([
        {"id": ctx.chef["id"], "dealer_id": ctx.dealer_id, "role": "dealer", "active": True,
         "email": f"{ctx.chef['id']}@e2etest-mail.de", "created_at": _jetzt()},
        {"id": ctx.a["id"], "dealer_id": ctx.dealer_id, "role": "sucher", "active": True,
         "first_name": "Anna", "last_name": "A", "email": f"{ctx.a['id']}@e2etest-mail.de", "created_at": _jetzt()},
        {"id": ctx.b["id"], "dealer_id": ctx.dealer_id, "role": "sucher", "active": True,
         "first_name": "Ben", "last_name": "B", "email": f"{ctx.b['id']}@e2etest-mail.de", "created_at": _jetzt()},
        {"id": ctx.c["id"], "dealer_id": ctx.dealer_id, "role": "sucher", "active": True,
         "first_name": "Cem", "last_name": "C", "email": f"{ctx.c['id']}@e2etest-mail.de", "created_at": _jetzt()}]))
    ctx.run(ctx.db.dealers.insert_one({"id": ctx.dealer_id, "user_id": ctx.chef["id"],
                                       "company_name": "R17m GmbH", "created_at": _jetzt()}))
    yield ctx
    try:
        for c in ("vehicles", "appointments", "generated_pdfs", "activity_logs", "listing_snapshots",
                  "users", "dealers"):
            ctx.run(ctx.db[c].delete_many({"dealer_id": ctx.dealer_id}))
        ctx.run(ctx.db.dealers.delete_many({"id": ctx.dealer_id}))
    finally:
        for m, d in alt:
            if d is not None:
                m.db = d
        ctx.client.close()
        ctx.loop.close()


def test_01_zweiter_sucher_wird_mitbearbeiter_und_sieht_das_fahrzeug(welt):
    L = _module("routes.listings")
    D = _module("deps")
    w = welt
    vid = f"v_{w.s}"

    async def lauf():
        k1 = await L._fahrzeug_uebernehmen(w.a, vid, w.s, {"make_label": "BMW", "mileage": 1})
        k2 = await L._fahrzeug_uebernehmen(w.b, vid, w.s, {"make_label": "BMW", "mileage": 2})
        k2b = await L._fahrzeug_uebernehmen(w.b, vid, w.s, {"make_label": "BMW", "mileage": 3})   # nochmal
        v = await w.db.vehicles.find_one({"id": vid}, {"_id": 0})
        liste_a = [x["id"] for x in await L.list_vehicles(w.a)]
        liste_b = await L.list_vehicles(w.b)
        liste_c = [x["id"] for x in await L.list_vehicles(w.c)]
        det_b = await L.get_vehicle_detail(vid, w.b)
        ids_b = await D.eigene_fahrzeug_ids(w.b)
        chef = await L.list_vehicles(w.chef)
        return k1, k2, k2b, v, liste_a, liste_b, liste_c, det_b, ids_b, chef

    k1, k2, k2b, v, liste_a, liste_b, liste_c, det_b, ids_b, chef = w.run(lauf())
    assert k1 is None
    assert k2["mitbearbeiter"] is True and k2["name"] == "Anna A" and k2b["mitbearbeiter"] is True
    assert v["owner_user_id"] == w.a["id"] and v["mitbearbeiter_ids"] == [w.b["id"]], "kein Doppeleintrag"
    assert liste_a == [vid] and [x["id"] for x in liste_b] == [vid] and liste_c == []
    assert det_b["id"] == vid and ids_b == [vid]
    assert liste_b[0]["mitbearbeiter_namen"] == ["Ben B"]
    assert chef[0]["owner_name"] == "Anna A" and chef[0]["mitbearbeiter_namen"] == ["Ben B"]


def test_02_beide_duerfen_vertrag_anlegen_fahrzeug_im_bereich(welt):
    D = _module("deps")
    C = _module("routes.contracts")
    w = welt
    vid = f"v_{w.s}"

    async def lauf():
        await w.db.vehicles.insert_one({"id": vid, "dealer_id": w.dealer_id, "lifecycle": "verglichen",
                                        "owner_user_id": w.a["id"], "mitbearbeiter_ids": [w.b["id"]],
                                        "data": {"make_label": "BMW"}, "created_at": _jetzt(),
                                        "updated_at": _jetzt()})
        # genau die Abfrage, die create_contract / preview_contract nutzen
        va = await w.db.vehicles.find_one({"id": vid, **D.fahrzeug_bereich(w.a)}, {"_id": 0, "id": 1})
        vb = await w.db.vehicles.find_one({"id": vid, **D.fahrzeug_bereich(w.b)}, {"_id": 0, "id": 1})
        vc = await w.db.vehicles.find_one({"id": vid, **D.fahrzeug_bereich(w.c)}, {"_id": 0, "id": 1})
        return va, vb, vc

    va, vb, vc = w.run(lauf())
    assert va and vb, "Haupt- und Mitbearbeiter finden das Fahrzeug"
    assert vc is None, "ein Dritter nicht"
    import inspect
    assert "**fahrzeug_bereich(user)" in inspect.getsource(C.create_contract)[:700]


def test_03_mitbearbeiter_uebernimmt_den_abholtermin_des_kollegen_nicht(welt):
    C = _module("routes.contracts")
    w = welt
    vid = f"v_{w.s}"

    class Body:
        vehicle_id = vid
        seller_name = "Neu"; seller_phone = "1"; seller_email = "n@e2etest-mail.de"
        seller_address = ""; seller_zip = ""; seller_city = ""
        pickup_date = "2099-10-10"; pickup_time = "11:00"

    async def lauf():
        await w.db.vehicles.insert_one({"id": vid, "dealer_id": w.dealer_id, "lifecycle": "verglichen",
                                        "owner_user_id": w.a["id"], "mitbearbeiter_ids": [w.b["id"]],
                                        "data": {"make_label": "BMW"}, "created_at": _jetzt(),
                                        "updated_at": _jetzt()})
        await w.db.generated_pdfs.insert_many([
            {"id": f"ca_{w.s}", "dealer_id": w.dealer_id, "user_id": w.a["id"], "vehicle_id": vid,
             "appointment_id": None, "created_at": _jetzt()},
            {"id": f"cb_{w.s}", "dealer_id": w.dealer_id, "user_id": w.b["id"], "vehicle_id": vid,
             "appointment_id": None, "created_at": _jetzt()}])
        a_id, a_hint = await C._abholtermin_fuer_vertrag(w.a, Body(), {"make_label": "BMW"}, f"ca_{w.s}")
        b_id, b_hint = await C._abholtermin_fuer_vertrag(w.b, Body(), {"make_label": "BMW"}, f"cb_{w.s}")
        termine = await w.db.appointments.find({"dealer_id": w.dealer_id}, {"_id": 0}).to_list(10)
        # B sieht den Termin (Fahrzeug-Anker), darf ihn aber nicht aendern
        A_ = _module("routes.appointments")
        sichtbar_b = [t["id"] for t in await A_.list_appointments(Response(), w.b)]
        darf_b = await C._termin_gehoert_mir(w.b, termine[0])
        # nochmal A: eigener Termin wird umgehaengt (zweiter eigener Vertrag)
        await w.db.generated_pdfs.insert_one({"id": f"ca2_{w.s}", "dealer_id": w.dealer_id,
                                              "user_id": w.a["id"], "vehicle_id": vid,
                                              "appointment_id": None, "created_at": _jetzt()})
        a2_id, a2_hint = await C._abholtermin_fuer_vertrag(w.a, Body(), {"make_label": "BMW"}, f"ca2_{w.s}")
        return a_id, a_hint, b_id, b_hint, termine, sichtbar_b, darf_b, a2_id

    a_id, a_hint, b_id, b_hint, termine, sichtbar_b, darf_b, a2_id = w.run(lauf())
    assert a_id and a_hint is None
    assert b_id is None and "Kollegen" in b_hint
    assert len(termine) == 1 and termine[0]["contract_id"] == f"ca_{w.s}"
    assert sichtbar_b == [a_id] and darf_b is False
    assert a2_id == a_id, "eigener zweiter Vertrag haengt den eigenen Termin um"


def test_04_uebergabe_neuer_hauptbearbeiter_verliert_mitbearbeiter_status_alter_verliert_zugriff(welt):
    Bst = _module("routes.bestand")
    D = _module("deps")
    w = welt
    vid = f"v_{w.s}"

    async def lauf():
        await w.db.vehicles.insert_one({"id": vid, "dealer_id": w.dealer_id, "lifecycle": "verglichen",
                                        "owner_user_id": w.a["id"],
                                        "mitbearbeiter_ids": [w.b["id"], w.c["id"]],
                                        "data": {"make_label": "BMW"}, "created_at": _jetzt(),
                                        "updated_at": _jetzt()})
        await Bst.set_vehicle_owner(vid, Bst.BesitzerIn(owner_user_id=w.b["id"]), w.chef)
        v = await w.db.vehicles.find_one({"id": vid}, {"_id": 0, "owner_user_id": 1, "mitbearbeiter_ids": 1})
        return v, await D.fahrzeug_im_bereich(w.a, vid), await D.fahrzeug_im_bereich(w.b, vid), \
            await D.fahrzeug_im_bereich(w.c, vid)

    v, a_ok, b_ok, c_ok = w.run(lauf())
    assert v["owner_user_id"] == w.b["id"] and v["mitbearbeiter_ids"] == [w.c["id"]]
    assert a_ok is False, "bisheriger Hauptbearbeiter verliert den Zugriff"
    assert b_ok is True and c_ok is True, "Mitbearbeiter C hat selbst verglichen und bleibt"


def test_05_akte_zeigt_mitbearbeiter(welt):
    Bst = _module("routes.bestand")
    w = welt
    vid = f"v_{w.s}"

    async def lauf():
        await w.db.vehicles.insert_one({"id": vid, "dealer_id": w.dealer_id, "lifecycle": "verglichen",
                                        "owner_user_id": w.a["id"], "mitbearbeiter_ids": [w.b["id"]],
                                        "data": {"make_label": "BMW"}, "created_at": _jetzt(),
                                        "updated_at": _jetzt()})
        akte_chef = await Bst.vehicle_akte(vid, w.chef)
        akte_b = await Bst.vehicle_akte(vid, w.b)
        return akte_chef, akte_b

    akte_chef, akte_b = w.run(lauf())
    assert akte_chef["owner"]["name"] == "Anna A"
    assert akte_chef["mitbearbeiter"] == [{"id": w.b["id"], "name": "Ben B"}]
    assert akte_b["vehicle"]["id"] == vid and akte_b["mitbearbeiter"][0]["name"] == "Ben B"
