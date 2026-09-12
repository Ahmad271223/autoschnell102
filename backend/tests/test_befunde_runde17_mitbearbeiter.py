# -*- coding: utf-8 -*-
"""Wunsch Ahmad 09.09.2026: Vergleichen zwei Sucher derselben Firma dasselbe
Auto, duerfen BEIDE einen Kaufvertrag anlegen. Umsetzung: Mitbearbeiter am
Fahrzeug (vehicles.mitbearbeiter_ids) — das Fahrzeug liegt im Bereich beider.
Der Hauptbearbeiter (owner_user_id) bleibt, wer zuerst verglich.

Umbau Kaufvorgaenge (09.09.2026): Besitzer/Mitbearbeiter regeln nur die
SICHTBARKEIT des gemeinsamen Inserats. Jeder Sucher der Firma darf einen
eigenen Vertrag (= eigenen Kaufvorgang) anlegen; je Vertrag gibt es EINEN
eigenen Abholtermin, den der Kollege weder uebernimmt noch sieht.
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


def _pdf_stub(monkeypatch):
    """ReportLab durch einen Stub ersetzen (create_contract in-Prozess)."""
    C = _module("routes.contracts")
    monkeypatch.setattr(C, "generate_contract_pdf",
                        lambda *, dealer, vehicle, contract, digital=False: b"%PDF-1.4 test")


def _auto_daten_stub(monkeypatch):
    """Anonyme Auto-Daten haetten keinen Aufraeum-Schluessel — Stub."""
    AD = _module("auto_daten")

    async def _anlegen(db, contract_dict, vehicle, gekauft_am=None):
        return f"ad_stub_{uuid.uuid4().hex[:8]}"

    async def _nichts(*a, **k):
        return None
    monkeypatch.setattr(AD, "anlegen", _anlegen)
    monkeypatch.setattr(AD, "zurueckrollen", _nichts)


@pytest.fixture
def welt():
    from motor.motor_asyncio import AsyncIOMotorClient
    s = uuid.uuid4().hex[:10]
    # Umbau Kaufvorgaenge 09.09.2026: auch `kaufvorgang` umbiegen.
    names = ["deps", "routes.listings", "routes.contracts", "routes.appointments", "routes.bestand",
             "routes.protocols", "lifecycle", "kaufvorgang"]
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
                  "users", "dealers", "kaufvorgaenge"):
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
    # Runde 29 (12.09.2026, Regel Ahmad): Der Sucher sieht NICHT, wer sonst
    # an dem Auto arbeitet — nur der Chef bekommt die Namen.
    assert liste_b[0]["mitbearbeiter_namen"] == []
    assert chef[0]["owner_name"] == "Anna A" and chef[0]["mitbearbeiter_namen"] == ["Ben B"]


def test_02_jeder_sucher_der_firma_darf_einen_eigenen_vertrag_anlegen(welt, monkeypatch):
    """Umbau Kaufvorgaenge 09.09.2026: das Fahrzeug ist das gemeinsame Inserat
    der Firma. Nicht nur Haupt- und Mitbearbeiter, sondern JEDER Sucher der
    Firma (auch C, der es nie verglichen hat) legt einen eigenen Vertrag =
    eigenen Kaufvorgang an und wird dadurch Mitbearbeiter. Die Sichtbarkeit
    des Fahrzeugs (fahrzeug_bereich) bleibt organisatorisch: VOR seinem
    Vertrag sieht C es nicht, danach schon. Der Kaufpreis liegt je Vorgang."""
    D = _module("deps")
    C = _module("routes.contracts")
    w = welt
    vid = f"v_{w.s}"
    _pdf_stub(monkeypatch)
    _auto_daten_stub(monkeypatch)

    def body(preis):
        return C.ContractIn(vehicle_id=vid, seller_name="Verkaeufer", purchase_price=preis)

    async def lauf():
        await w.db.vehicles.insert_one({"id": vid, "dealer_id": w.dealer_id, "lifecycle": "verglichen",
                                        "owner_user_id": w.a["id"], "mitbearbeiter_ids": [w.b["id"]],
                                        "data": {"make_label": "BMW"}, "created_at": _jetzt(),
                                        "updated_at": _jetzt()})
        sicht_vorher = {n: await w.db.vehicles.count_documents({"id": vid, **D.fahrzeug_bereich(u)})
                        for n, u in (("a", w.a), ("b", w.b), ("c", w.c))}
        outs = {}
        for n, u, preis in (("a", w.a, 1000), ("b", w.b, 2000), ("c", w.c, 3000)):
            outs[n] = await C.create_contract(body(preis), u)
        sicht_nachher = {n: await w.db.vehicles.count_documents({"id": vid, **D.fahrzeug_bereich(u)})
                         for n, u in (("a", w.a), ("b", w.b), ("c", w.c))}
        v = await w.db.vehicles.find_one({"id": vid}, {"_id": 0})
        kvs = await w.db.kaufvorgaenge.find({"vehicle_id": vid, "dealer_id": w.dealer_id}, {"_id": 0}).to_list(10)
        return sicht_vorher, outs, sicht_nachher, v, kvs

    sicht_vorher, outs, sicht_nachher, v, kvs = w.run(lauf())
    assert sicht_vorher == {"a": 1, "b": 1, "c": 0}, "Sichtbarkeit: Haupt- und Mitbearbeiter, ein Dritter nicht"
    assert {n: o["user_id"] for n, o in outs.items()} == {"a": w.a["id"], "b": w.b["id"], "c": w.c["id"]}
    assert len({o["id"] for o in outs.values()}) == 3, "drei eigene Vertraege zum selben Inserat"
    assert sicht_nachher == {"a": 1, "b": 1, "c": 1}, "C wird durch seinen Vertrag Mitbearbeiter"
    assert v["owner_user_id"] == w.a["id"] and v["mitbearbeiter_ids"] == [w.b["id"], w.c["id"]]
    assert v.get("purchase_price") is None, "Kaufpreis liegt am Vorgang, nicht am Fahrzeug"
    assert v["lifecycle"] == "gekauft"
    assert {(k["user_id"], k["contract_id"], k["purchase_price"], k["status"]) for k in kvs} == {
        (w.a["id"], outs["a"]["id"], 1000.0, "vertrag_erstellt"),
        (w.b["id"], outs["b"]["id"], 2000.0, "vertrag_erstellt"),
        (w.c["id"], outs["c"]["id"], 3000.0, "vertrag_erstellt")}
    assert {k["id"] for k in kvs} == {o["kaufvorgang_id"] for o in outs.values()}


def test_03_jeder_vertrag_bekommt_eigenen_abholtermin_kollege_sieht_ihn_nicht(welt):
    """Umbau Kaufvorgaenge 09.09.2026: EIN offener Abholtermin je VERTRAG.
    Haupt- und Mitbearbeiter bekommen je einen eigenen Termin zum selben
    Fahrzeug; keiner uebernimmt oder sieht den Termin des Kollegen. Ein
    zweiter eigener Vertrag bekommt ebenfalls einen eigenen Termin (kein
    Umhaengen mehr); die Wiederholung fuer denselben Vertrag nutzt denselben."""
    C = _module("routes.contracts")
    A_ = _module("routes.appointments")
    w = welt
    vid, ca, cb, ca2 = f"v_{w.s}", f"ca_{w.s}", f"cb_{w.s}", f"ca2_{w.s}"

    class Body:
        vehicle_id = vid
        seller_name = "Neu"; seller_phone = "1"; seller_email = "n@e2etest-mail.de"
        seller_address = ""; seller_zip = ""; seller_city = ""
        pickup_date = "2099-10-10"; pickup_time = "11:00"

    def vertrag(cid, user_id):
        return {"id": cid, "dealer_id": w.dealer_id, "user_id": user_id, "vehicle_id": vid,
                "appointment_id": None, "created_at": _jetzt()}

    async def lauf():
        await w.db.vehicles.insert_one({"id": vid, "dealer_id": w.dealer_id, "lifecycle": "verglichen",
                                        "owner_user_id": w.a["id"], "mitbearbeiter_ids": [w.b["id"]],
                                        "data": {"make_label": "BMW"}, "created_at": _jetzt(),
                                        "updated_at": _jetzt()})
        await w.db.generated_pdfs.insert_many([vertrag(ca, w.a["id"]), vertrag(cb, w.b["id"])])
        a_id, a_hint = await C._abholtermin_fuer_vertrag(w.a, Body(), {"make_label": "BMW"}, ca)
        b_id, b_hint = await C._abholtermin_fuer_vertrag(w.b, Body(), {"make_label": "BMW"}, cb)
        termine = {t["id"]: t for t in await w.db.appointments.find({"dealer_id": w.dealer_id}, {"_id": 0}).to_list(10)}
        sichtbar_a = [t["id"] for t in await A_.list_appointments(Response(), w.a)]
        sichtbar_b = [t["id"] for t in await A_.list_appointments(Response(), w.b)]
        darf_b_fremd = await C._termin_gehoert_mir(w.b, termine[a_id])
        darf_a_fremd = await C._termin_gehoert_mir(w.a, termine[b_id])
        # zweiter eigener Vertrag von A: eigener Termin, der erste bleibt unangetastet
        await w.db.generated_pdfs.insert_one(vertrag(ca2, w.a["id"]))
        a2_id, a2_hint = await C._abholtermin_fuer_vertrag(w.a, Body(), {"make_label": "BMW"}, ca2)
        a_wieder, _ = await C._abholtermin_fuer_vertrag(w.a, Body(), {"make_label": "BMW"}, ca)  # Wiederholung
        zeiger = {c: (await w.db.generated_pdfs.find_one({"id": c}, {"_id": 0}))["appointment_id"]
                  for c in (ca, cb, ca2)}
        n = await w.db.appointments.count_documents({"dealer_id": w.dealer_id, "status": "offen"})
        return a_id, a_hint, b_id, b_hint, termine, sichtbar_a, sichtbar_b, darf_b_fremd, darf_a_fremd, \
            a2_id, a2_hint, a_wieder, zeiger, n

    (a_id, a_hint, b_id, b_hint, termine, sichtbar_a, sichtbar_b, darf_b_fremd, darf_a_fremd,
     a2_id, a2_hint, a_wieder, zeiger, n) = w.run(lauf())
    assert a_id and b_id and a_id != b_id and a_hint is None and b_hint is None, "je Vertrag ein eigener Termin"
    assert len(termine) == 2
    assert {t["contract_id"]: t["created_by"] for t in termine.values()} == {ca: w.a["id"], cb: w.b["id"]}
    assert sichtbar_a == [a_id] and sichtbar_b == [b_id], "Kollegen-Termine sind unsichtbar"
    assert darf_b_fremd is False and darf_a_fremd is False
    assert a2_id and a2_hint is None and a2_id not in (a_id, b_id), "zweiter eigener Vertrag bekommt eigenen Termin"
    assert a_wieder == a_id, "Wiederholung fuer denselben Vertrag nutzt denselben Termin"
    assert zeiger == {ca: a_id, cb: b_id, ca2: a2_id}, "kein Vertrag verliert seinen Termin"
    assert n == 3


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
    # Runde 29 (12.09.2026, Regel Ahmad): Der Sucher sieht seine Akte, aber
    # NICHT, wer sonst an dem Auto arbeitet — nur der Chef bekommt die Namen.
    assert akte_b["vehicle"]["id"] == vid and akte_b["mitbearbeiter"] == []
