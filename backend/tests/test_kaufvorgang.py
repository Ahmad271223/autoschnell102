# -*- coding: utf-8 -*-
"""Umbau Kaufvorgaenge (09.09.2026, Wunsch Ahmad): Inserat gemeinsam,
Kaufvorgang je Sucher. Pflichtliste des Pruefers:

  * Sucher A und B vergleichen dasselbe Auto -> gleiche Kleinanzeigen-
    Snapshot-ID, beide koennen den Snapshot laden
  * beide erstellen je einen eigenen Vertrag; Vertrag A fuer B unsichtbar
  * beide koennen einen eigenen offenen Termin haben; Termin A aendert B nicht
  * Preis A ueberschreibt Preis B nicht (Kaufpreis am Vorgang)
  * nicht_abgeholt bei A veraendert den Status von B nicht
  * Chef sieht beide Kaufvorgaenge
  * mobile.de/AutoScout erzeugen weiterhin keinen Snapshot
  * Migration legt fuer Altvertraege Vorgaenge an, m6 bessert Besitzer nach
"""
import asyncio
import inspect
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
    names = ["deps", "kaufvorgang", "lifecycle", "routes.listings", "routes.contracts",
             "routes.appointments", "routes.bestand", "routes.protocols", "routes.drivers",
             "routes.resale", "migrationen"]
    mods = [_module(n) for n in names]
    alt = [(m, getattr(m, "db", None)) for m in mods]

    class _Ctx:
        def __init__(self):
            self.s = s
            self.dealer_id = f"d_kv_{s}"
            self.chef = {"id": f"chef_kv_{s}", "dealer_id": self.dealer_id, "role": "dealer"}
            self.a = {"id": f"sa_kv_{s}", "dealer_id": self.dealer_id, "role": "sucher"}
            self.b = {"id": f"sb_kv_{s}", "dealer_id": self.dealer_id, "role": "sucher"}
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
            self.db = self.client[DB_NAME]
            for m in mods:
                if hasattr(m, "db"):
                    m.db = self.db

        def run(self, coro):
            return self.loop.run_until_complete(coro)

        def fahrzeug(self, vid, **extra):
            d = {"id": vid, "dealer_id": self.dealer_id, "lifecycle": "verglichen",
                 "mobile_ad_id": vid[2:], "owner_user_id": self.a["id"],
                 "mitbearbeiter_ids": [self.b["id"]],
                 "data": {"make_label": "BMW", "model_label": "320d"},
                 "created_at": _jetzt(), "updated_at": _jetzt()}
            d.update(extra)
            return d

    ctx = _Ctx()
    ctx.run(ctx.db.users.insert_many([
        {"id": ctx.chef["id"], "dealer_id": ctx.dealer_id, "role": "dealer", "active": True,
         "email": f"{ctx.chef['id']}@e2etest-mail.de", "created_at": _jetzt()},
        {"id": ctx.a["id"], "dealer_id": ctx.dealer_id, "role": "sucher", "active": True,
         "first_name": "Anna", "last_name": "A", "email": f"{ctx.a['id']}@e2etest-mail.de", "created_at": _jetzt()},
        {"id": ctx.b["id"], "dealer_id": ctx.dealer_id, "role": "sucher", "active": True,
         "first_name": "Ben", "last_name": "B", "email": f"{ctx.b['id']}@e2etest-mail.de", "created_at": _jetzt()}]))
    ctx.run(ctx.db.dealers.insert_one({"id": ctx.dealer_id, "user_id": ctx.chef["id"],
                                       "company_name": "KV GmbH", "created_at": _jetzt()}))
    ctx.run(ctx.db.kaufvorgaenge.create_index("contract_id", unique=True))
    yield ctx
    try:
        for c in ("vehicles", "appointments", "generated_pdfs", "kaufvorgaenge", "activity_logs",
                  "listing_snapshots", "pickup_reports", "pickup_protocols", "users", "dealers",
                  "resale_listings", "vehicle_comparisons"):
            ctx.run(ctx.db[c].delete_many({"dealer_id": ctx.dealer_id}))
        ctx.run(ctx.db.dealers.delete_many({"id": ctx.dealer_id}))
    finally:
        for m, d in alt:
            if d is not None:
                m.db = d
        ctx.client.close()
        ctx.loop.close()


class _Body:
    def __init__(self, vid, preis, datum="2099-10-10"):
        self.vehicle_id = vid
        self.seller_name = "Verkaeufer"; self.seller_phone = "1"; self.seller_email = "v@e2etest-mail.de"
        self.seller_address = "Weg 1"; self.seller_zip = "30159"; self.seller_city = "Hannover"
        self.pickup_date = datum; self.pickup_time = "11:00"
        self.purchase_price = preis


async def _vertrag(w, user, vid, preis, cid):
    """Vertrag + Kaufvorgang so anlegen, wie create_contract es tut
    (ohne ReportLab): Vertrag mit kaufvorgang_id, Vorgang, Aggregation."""
    KV = _module("kaufvorgang")
    kv_id = str(uuid.uuid4())
    await w.db.generated_pdfs.insert_one({
        "id": cid, "dealer_id": w.dealer_id, "user_id": user["id"], "vehicle_id": vid,
        "purchase_price": preis, "status": "erstellt", "appointment_id": None,
        "kaufvorgang_id": kv_id, "created_at": _jetzt(),
        "contract_data": {"seller_name": "Verkaeufer"}})
    await KV.anlegen(dealer_id=w.dealer_id, user_id=user["id"], vehicle_id=vid, contract_id=cid,
                     purchase_price=preis, kaufvorgang_id=kv_id)
    await KV.fahrzeug_status_aggregieren(vid, w.dealer_id, user=user)
    return kv_id


# ================================================= zwei Sucher, zwei Vertraege, zwei Termine
def test_01_beide_sucher_vertrag_und_eigener_termin(welt):
    C = _module("routes.contracts")
    A_ = _module("routes.appointments")
    w = welt
    vid = f"v_{w.s}"

    async def lauf():
        await w.db.vehicles.insert_one(w.fahrzeug(vid))
        # create_contract laedt das Fahrzeug firmenweit (nicht mehr je Besitzer)
        q = inspect.getsource(C.create_contract)
        assert '"dealer_id": user["dealer_id"]' in q[:900] and "fahrzeug_bereich(user)" not in q[:900]
        kv_a = await _vertrag(w, w.a, vid, 18000, f"ca_{w.s}")
        kv_b = await _vertrag(w, w.b, vid, 19500, f"cb_{w.s}")
        ta, ha = await C._abholtermin_fuer_vertrag(w.a, _Body(vid, 18000), {"make_label": "BMW"},
                                                  f"ca_{w.s}", kaufvorgang_id=kv_a)
        tb, hb = await C._abholtermin_fuer_vertrag(w.b, _Body(vid, 19500, "2099-10-12"),
                                                  {"make_label": "BMW"}, f"cb_{w.s}", kaufvorgang_id=kv_b)
        # Wiederholung fuer A: derselbe Termin (idempotent), kein zweiter
        ta2, _ = await C._abholtermin_fuer_vertrag(w.a, _Body(vid, 18000), {"make_label": "BMW"},
                                                  f"ca_{w.s}", kaufvorgang_id=kv_a)
        termine = {t["id"]: t for t in await w.db.appointments.find({"dealer_id": w.dealer_id}, {"_id": 0}).to_list(10)}
        sicht_a = {t["id"] for t in await A_.list_appointments(Response(), w.a)}
        sicht_b = {t["id"] for t in await A_.list_appointments(Response(), w.b)}
        sicht_chef = {t["id"] for t in await A_.list_appointments(Response(), w.chef)}
        v = await w.db.vehicles.find_one({"id": vid}, {"_id": 0, "purchase_price": 1, "lifecycle": 1})
        kvs = {k["user_id"]: k for k in await w.db.kaufvorgaenge.find({"vehicle_id": vid}, {"_id": 0}).to_list(10)}
        # Vertragsarchiv: A sieht B nicht
        vertraege_b = await w.db.generated_pdfs.find({"vehicle_id": vid, **C._vertrag_bereich(w.b)},
                                                     {"_id": 0, "id": 1}).to_list(10)
        return ta, tb, ta2, ha, hb, termine, sicht_a, sicht_b, sicht_chef, v, kvs, vertraege_b

    ta, tb, ta2, ha, hb, termine, sicht_a, sicht_b, sicht_chef, v, kvs, vertraege_b = w.run(lauf())
    assert ta and tb and ta != tb and ha is None and hb is None, "je Vertrag ein eigener Termin"
    assert ta2 == ta, "Wiederholung nutzt den Termin desselben Vertrags"
    assert len(termine) == 2
    assert termine[ta]["contract_id"] == f"ca_{w.s}" and termine[tb]["contract_id"] == f"cb_{w.s}"
    assert termine[ta]["pickup_date"] == "2099-10-10" and termine[tb]["pickup_date"] == "2099-10-12", \
        "Termin A wurde durch B nicht ueberschrieben"
    assert sicht_a == {ta} and sicht_b == {tb} and sicht_chef == {ta, tb}
    assert v.get("purchase_price") is None, "Kaufpreis liegt am Vorgang, nicht am gemeinsamen Fahrzeug"
    assert kvs[w.a["id"]]["purchase_price"] == 18000 and kvs[w.b["id"]]["purchase_price"] == 19500
    assert kvs[w.a["id"]]["status"] == "abholung_geplant" and kvs[w.a["id"]]["appointment_id"] == ta
    assert v["lifecycle"] == "abholung_geplant"
    assert [c["id"] for c in vertraege_b] == [f"cb_{w.s}"]


def test_02_nicht_abgeholt_bei_a_laesst_b_unberuehrt_abgeholt_setzt_preis(welt):
    C = _module("routes.contracts")
    A_ = _module("routes.appointments")
    KV = _module("kaufvorgang")
    w = welt
    vid = f"v_{w.s}"

    async def lauf():
        await w.db.vehicles.insert_one(w.fahrzeug(vid))
        kv_a = await _vertrag(w, w.a, vid, 18000, f"ca_{w.s}")
        kv_b = await _vertrag(w, w.b, vid, 19500, f"cb_{w.s}")
        ta, _ = await C._abholtermin_fuer_vertrag(w.a, _Body(vid, 18000), {}, f"ca_{w.s}", kaufvorgang_id=kv_a)
        tb, _ = await C._abholtermin_fuer_vertrag(w.b, _Body(vid, 19500), {}, f"cb_{w.s}", kaufvorgang_id=kv_b)
        # A: nicht abgeholt (kein Fahrer -> manuell erlaubt)
        await A_.update_appointment(ta, A_.AppointmentIn(status="nicht abgeholt"), w.a)
        v1 = await w.db.vehicles.find_one({"id": vid}, {"_id": 0, "lifecycle": 1, "purchase_price": 1})
        kv_b_doc = await w.db.kaufvorgaenge.find_one({"id": kv_b}, {"_id": 0})
        kv_a_doc = await w.db.kaufvorgaenge.find_one({"id": kv_a}, {"_id": 0})
        # B: abgeholt -> Fahrzeug abgeholt, realisierter Preis = B
        await A_.update_appointment(tb, A_.AppointmentIn(status="abgeholt"), w.b)
        v2 = await w.db.vehicles.find_one({"id": vid}, {"_id": 0, "lifecycle": 1, "purchase_price": 1})
        kv_b2 = await w.db.kaufvorgaenge.find_one({"id": kv_b}, {"_id": 0, "status": 1})
        return v1, kv_a_doc, kv_b_doc, v2, kv_b2

    v1, kv_a_doc, kv_b_doc, v2, kv_b2 = w.run(lauf())
    assert kv_a_doc["status"] == "nicht_abgeholt"
    assert kv_b_doc["status"] == "abholung_geplant", "Vorgang B unveraendert"
    assert v1["lifecycle"] == "abholung_geplant", "Fahrzeug bleibt geplant, solange B offen ist"
    assert v1.get("purchase_price") is None
    assert kv_b2["status"] == "abgeholt" and v2["lifecycle"] == "abgeholt"
    assert v2["purchase_price"] == 19500, "realisierter Kaufpreis aus dem abgeholten Vorgang"


def test_03_chef_sieht_beide_vorgaenge_sucher_nur_eigene_in_der_akte(welt):
    Bst = _module("routes.bestand")
    C = _module("routes.contracts")
    w = welt
    vid = f"v_{w.s}"

    async def lauf():
        await w.db.vehicles.insert_one(w.fahrzeug(vid, lifecycle="gekauft"))
        kv_a = await _vertrag(w, w.a, vid, 18000, f"ca_{w.s}")
        kv_b = await _vertrag(w, w.b, vid, 19500, f"cb_{w.s}")
        ta, _ = await C._abholtermin_fuer_vertrag(w.a, _Body(vid, 18000), {}, f"ca_{w.s}", kaufvorgang_id=kv_a)
        await w.db.pickup_reports.insert_one({"id": f"r_{w.s}", "dealer_id": w.dealer_id, "appointment_id": ta,
                                              "vehicle_id": vid, "version": 1, "created_at": _jetzt()})
        akte_chef = await Bst.vehicle_akte(vid, w.chef)
        akte_a = await Bst.vehicle_akte(vid, w.a)
        akte_b = await Bst.vehicle_akte(vid, w.b)
        return akte_chef, akte_a, akte_b, ta

    akte_chef, akte_a, akte_b, ta = w.run(lauf())
    assert {k["user_name"] for k in akte_chef["kaufvorgaenge"]} == {"Anna A", "Ben B"}
    assert [k["purchase_price"] for k in akte_a["kaufvorgaenge"]] == [18000]
    assert [k["purchase_price"] for k in akte_b["kaufvorgaenge"]] == [19500]
    assert [a["id"] for a in akte_a["appointments"]] == [ta] and akte_b["appointments"] == []
    assert akte_a["pickup_report"] and akte_b["pickup_report"] is None, "Bericht nur zum eigenen Termin"
    assert [c["id"] for c in akte_b["contracts"]] == [f"cb_{w.s}"]


def test_04_snapshot_gemeinsam_beide_duerfen_laden_und_kein_snapshot_fuer_mobile(welt):
    L = _module("routes.listings")
    w = welt
    vid = f"v_{w.s}"
    sid = f"s_{w.s}"

    async def lauf():
        await w.db.vehicles.insert_one(w.fahrzeug(vid))
        await w.db.listing_snapshots.insert_one({"id": sid, "dealer_id": w.dealer_id, "user_id": w.a["id"],
                                                 "vehicle_id": vid, "status": "ready", "png_path": "x",
                                                 "pdf_path": "y", "created_at": _jetzt()})
        ok_a = await L._load_snapshot_or_404(sid, w.a)
        ok_b = await L._load_snapshot_or_404(sid, w.b)          # Mitbearbeiter (hat verglichen)
        # Dritter Sucher mit eigenem Vertrag, ohne Vergleich: ueber den Vertrag
        c = {"id": f"sc_kv_{w.s}", "dealer_id": w.dealer_id, "role": "sucher"}
        await w.db.generated_pdfs.insert_one({"id": f"cc_{w.s}", "dealer_id": w.dealer_id, "user_id": c["id"],
                                              "vehicle_id": vid, "created_at": _jetzt()})
        ok_c = await L._load_snapshot_or_404(sid, c)
        d = {"id": f"sd_kv_{w.s}", "dealer_id": w.dealer_id, "role": "sucher"}
        with pytest.raises(HTTPException) as e:
            await L._load_snapshot_or_404(sid, d)
        liste_b = {s["id"] for s in await L.list_snapshots(None, w.b)}
        return ok_a["id"], ok_b["id"], ok_c["id"], e.value.status_code, liste_b

    a, b, c, status_d, liste_b = w.run(lauf())
    assert a == b == c == sid and status_d == 404 and liste_b == {sid}
    # Seit 10.09.2026: keine neuen Snapshots mehr, stattdessen EIN
    # Beweisdokument je Inserat fuer alle Portale (alte Snapshots bleiben lesbar).
    q = inspect.getsource(L.compare)
    assert "beweis_vormerken(" in q and "create_snapshot" not in q


def test_05_termin_nur_mit_eigenem_vertrag_je_vertrag_ein_offener(welt):
    A_ = _module("routes.appointments")
    w = welt
    vid = f"v_{w.s}"

    async def lauf():
        await w.db.vehicles.insert_one(w.fahrzeug(vid))
        kv_a = await _vertrag(w, w.a, vid, 18000, f"ca_{w.s}")
        # Der Teil-Unique-Index termin_offen_je_vertrag legt das Backend beim
        # Start an; die Route prueft vorab (409) auch ohne Index.
        if True:
            # B darf keinen Termin an A's Vertrag haengen (fremder Vertrag)
            with pytest.raises(HTTPException) as e1:
                await A_.create_appointment(A_.AppointmentIn(vehicle_id=vid, contract_id=f"ca_{w.s}",
                                                             pickup_date="2099-01-01"), w.b)
            # A: eigener Termin zum eigenen Vertrag
            t1 = await A_.create_appointment(A_.AppointmentIn(vehicle_id=vid, contract_id=f"ca_{w.s}",
                                                              pickup_date="2099-01-01"), w.a)
            # A: zweiter offener Termin zum SELBEN Vertrag -> 409
            with pytest.raises(HTTPException) as e2:
                await A_.create_appointment(A_.AppointmentIn(vehicle_id=vid, contract_id=f"ca_{w.s}",
                                                             pickup_date="2099-01-02"), w.a)
            # B: eigener Termin zum selben FAHRZEUG (ohne Vertrag) ist erlaubt
            t2 = await A_.create_appointment(A_.AppointmentIn(vehicle_id=vid, pickup_date="2099-01-03"), w.b)
            kv = await w.db.kaufvorgaenge.find_one({"id": kv_a}, {"_id": 0})
            return e1.value.status_code, t1, e2.value.status_code, t2, kv

    s1, t1, s2, t2, kv = w.run(lauf())
    assert s1 == 404 and s2 == 409
    assert t1["kaufvorgang_id"] == kv["id"] and kv["appointment_id"] == t1["id"] and kv["status"] == "abholung_geplant"
    assert t2["vehicle_id"] == vid and "kaufvorgang_id" not in t2


def test_06_protokoll_und_foto_nur_ueber_eigenen_termin(welt):
    P = _module("routes.protocols")
    w = welt
    vid = f"v_{w.s}"

    async def lauf():
        await w.db.vehicles.insert_one(w.fahrzeug(vid))
        kv_a = await _vertrag(w, w.a, vid, 18000, f"ca_{w.s}")
        await w.db.appointments.insert_one({"id": f"ta_{w.s}", "dealer_id": w.dealer_id, "vehicle_id": vid,
                                            "contract_id": f"ca_{w.s}", "kaufvorgang_id": kv_a,
                                            "created_by": w.a["id"], "status": "abgeholt",
                                            "pickup_date": "2099-01-01", "created_at": _jetzt()})
        await w.db.pickup_protocols.insert_one({"id": f"p_{w.s}", "dealer_id": w.dealer_id, "vehicle_id": vid,
                                                "appointment_id": f"ta_{w.s}", "status": "final", "version": 1,
                                                "pdf_path": "x.pdf", "created_at": _jetzt()})
        doc = {"vehicle_id": vid, "appointment_id": f"ta_{w.s}"}
        a_ok = await P._protokoll_im_bereich(w.a, doc)
        b_ok = await P._protokoll_im_bereich(w.b, doc)      # Mitbearbeiter, aber fremder Termin
        liste_a = await P.dealer_list_protocols(vid, w.a)
        liste_b = await P.dealer_list_protocols(vid, w.b)
        liste_chef = await P.dealer_list_protocols(vid, w.chef)
        return a_ok, b_ok, liste_a, liste_b, liste_chef

    a_ok, b_ok, liste_a, liste_b, liste_chef = w.run(lauf())
    assert a_ok is True and b_ok is False
    assert [p["id"] for p in liste_a] == [f"p_{w.s}"] and liste_b == [] and len(liste_chef) == 1


def test_07_termin_loeschen_setzt_vorgang_zurueck(welt):
    A_ = _module("routes.appointments")
    C = _module("routes.contracts")
    w = welt
    vid = f"v_{w.s}"

    async def lauf():
        await w.db.vehicles.insert_one(w.fahrzeug(vid))
        kv_a = await _vertrag(w, w.a, vid, 18000, f"ca_{w.s}")
        ta, _ = await C._abholtermin_fuer_vertrag(w.a, _Body(vid, 18000), {}, f"ca_{w.s}", kaufvorgang_id=kv_a)
        await A_.delete_appointment(ta, w.a)
        kv = await w.db.kaufvorgaenge.find_one({"id": kv_a}, {"_id": 0})
        c = await w.db.generated_pdfs.find_one({"id": f"ca_{w.s}"}, {"_id": 0, "appointment_id": 1})
        return kv, c

    kv, c = w.run(lauf())
    assert kv["status"] == "vertrag_erstellt" and kv["appointment_id"] is None
    assert c["appointment_id"] is None


def test_08_migration_m5_legt_vorgaenge_fuer_altvertraege_an(welt):
    M = _module("migrationen")
    w = welt
    vid = f"v_{w.s}"

    async def lauf():
        await w.db.vehicles.insert_one(w.fahrzeug(vid, mitbearbeiter_ids=[]))
        await w.db.generated_pdfs.insert_many([
            {"id": f"c1_{w.s}", "dealer_id": w.dealer_id, "user_id": w.b["id"], "vehicle_id": vid,
             "purchase_price": 17000, "status": "versendet", "appointment_id": f"t1_{w.s}",
             "created_at": "2026-05-01T00:00:00"},
            {"id": f"c2_{w.s}", "dealer_id": w.dealer_id, "user_id": w.a["id"], "vehicle_id": vid,
             "purchase_price": 16000, "status": "erstellt", "appointment_id": None,
             "created_at": "2026-06-01T00:00:00"}])
        await w.db.appointments.insert_one({"id": f"t1_{w.s}", "dealer_id": w.dealer_id, "vehicle_id": vid,
                                            "contract_id": f"c1_{w.s}", "status": "abgeholt",
                                            "created_by": w.b["id"], "created_at": _jetzt()})
        stats = await M.m5_kaufvorgaenge(w.db)
        stats2 = await M.m5_kaufvorgaenge(w.db)      # idempotent
        kvs = {k["contract_id"]: k for k in await w.db.kaufvorgaenge.find({"dealer_id": w.dealer_id}, {"_id": 0}).to_list(10)}
        c1 = await w.db.generated_pdfs.find_one({"id": f"c1_{w.s}"}, {"_id": 0, "kaufvorgang_id": 1})
        t1 = await w.db.appointments.find_one({"id": f"t1_{w.s}"}, {"_id": 0, "kaufvorgang_id": 1})
        v = await w.db.vehicles.find_one({"id": vid}, {"_id": 0, "mitbearbeiter_ids": 1})
        return stats, stats2, kvs, c1, t1, v

    stats, stats2, kvs, c1, t1, v = w.run(lauf())
    assert stats["vorgaenge"] >= 2 and stats["termine_verknuepft"] >= 1
    assert kvs[f"c1_{w.s}"]["status"] == "abgeholt" and kvs[f"c1_{w.s}"]["purchase_price"] == 17000
    assert kvs[f"c2_{w.s}"]["status"] == "vertrag_erstellt" and kvs[f"c2_{w.s}"]["user_id"] == w.a["id"]
    assert c1["kaufvorgang_id"] == kvs[f"c1_{w.s}"]["id"] and t1["kaufvorgang_id"] == kvs[f"c1_{w.s}"]["id"]
    assert v["mitbearbeiter_ids"] == [w.b["id"]], "Vertragsersteller B wird Mitbearbeiter, Besitzer A nicht"
    assert stats2["vorgaenge"] == 0 or all(
        k["contract_id"] in (f"c1_{w.s}", f"c2_{w.s}") for k in kvs.values())


def test_09_migration_m4_und_m6_nehmen_nur_aktive_konten_und_gehen_kandidaten_durch(welt):
    M = _module("migrationen")
    w = welt
    vid = f"v_{w.s}"
    vid2 = f"v_2{w.s}"

    async def lauf():
        ex = f"ex_kv_{w.s}"      # ausgeschiedener Mitarbeiter (inaktiv)
        await w.db.users.insert_one({"id": ex, "dealer_id": w.dealer_id, "role": "sucher", "active": False,
                                     "email": f"{ex}@e2etest-mail.de"})
        await w.db.vehicles.insert_many([
            {"id": vid, "dealer_id": w.dealer_id, "lifecycle": "verglichen", "mobile_ad_id": w.s,
             "data": {}, "created_at": _jetzt()},                                   # ohne Besitzer
            {"id": vid2, "dealer_id": w.dealer_id, "lifecycle": "verglichen", "mobile_ad_id": "2" + w.s,
             "owner_user_id": ex, "data": {}, "created_at": _jetzt()}])             # Besitzer inaktiv
        await w.db.generated_pdfs.insert_many([
            {"id": f"alt_{w.s}", "dealer_id": w.dealer_id, "user_id": ex, "vehicle_id": vid,
             "created_at": "2026-01-01T00:00:00"},
            {"id": f"neu_{w.s}", "dealer_id": w.dealer_id, "user_id": w.b["id"], "vehicle_id": vid,
             "created_at": "2026-02-01T00:00:00"}])
        await w.db.vehicle_comparisons.insert_one({"id": f"k_{w.s}", "dealer_id": w.dealer_id,
                                                   "mobile_ad_id": "2" + w.s, "user_id": w.a["id"],
                                                   "created_at": "2026-03-01T00:00:00"})
        s4 = await M.m4_fahrzeug_besitzer(w.db)
        s6 = await M.m6_besitzer_nachbessern(w.db)
        v1 = await w.db.vehicles.find_one({"id": vid}, {"_id": 0, "owner_user_id": 1, "besitzer_migriert_von": 1})
        v2 = await w.db.vehicles.find_one({"id": vid2}, {"_id": 0, "owner_user_id": 1, "besitzer_migriert_von": 1})
        await w.db.users.delete_one({"id": ex})
        return s4, s6, v1, v2

    s4, s6, v1, v2 = w.run(lauf())
    assert v1["owner_user_id"] == w.b["id"] and v1["besitzer_migriert_von"] == "vertrag", \
        "aeltester Vertrag von Ex-Mitarbeiter wird uebersprungen, naechster gueltiger zaehlt"
    assert v2["owner_user_id"] == w.a["id"] and v2["besitzer_migriert_von"].endswith("vergleich")
    assert s6["nachgebessert"] >= 1


def test_10_index_und_betrieb_kennen_den_vertragsindex():
    WURZEL = Path(__file__).resolve().parents[2]
    q = (WURZEL / "backend" / "indizes.py").read_text(encoding="utf-8")
    assert "termin_offen_je_vertrag" in q and '("dealer_id", 1), ("contract_id", 1)' in q
    assert 'drop_index("termin_offen_je_fahrzeug")' in q
    a = (WURZEL / "backend" / "routes" / "admin.py").read_text(encoding="utf-8")
    assert '"termin_offen_je_vertrag" in await db.appointments.index_information()' in a
    s = (WURZEL / "backend" / "server.py").read_text(encoding="utf-8")
    assert "_unique_index_sicher(" in s and 'db.kaufvorgaenge, "contract_id"' in s
    # Runde 29 (12.09.2026): Fehlt der Index, darf die Instanz nicht in die
    # Rotation — /ready meldet dann einen Fehler (503) statt nur zu warnen.
    assert 'BETRIEBSBEREIT["index_kaufvorgaenge"]' in s
    # Gegenpruefung 12.09.2026: geprueft wird LIVE (nicht ueber den Merker
    # vom Start) — sonst bliebe /ready nach dem Bereinigen der Dubletten
    # dauerhaft auf 503 und der Server waere nicht mehr freizugeben.
    assert '"kaufvorgaenge": ("contract_id",)' in s
    assert "index_information()" in s


def test_11_migration_meldet_fahrzeuge_ohne_besitzer_als_alarm(welt):
    """Runde 17 (Migrations-Befund 5): laesst sich kein Besitzer ermitteln,
    bleibt das nicht still — offener Betriebsalarm fahrzeuge_ohne_besitzer;
    sobald alles zugeordnet ist, wird er geschlossen."""
    M = _module("migrationen")
    B = _module("betrieb")
    B.db = welt.db
    w = welt
    fremd = f"d_kv_leer_{w.s}"      # Firma ohne Konten -> kein Kandidat

    async def lauf():
        await w.db.vehicles.insert_one({"id": f"v_ohne_{w.s}", "dealer_id": fremd, "lifecycle": "verglichen",
                                        "data": {}, "created_at": _jetzt()})
        s = await M.m4_fahrzeug_besitzer(w.db)
        alarm = await w.db.betriebsalarme.find_one({"typ": "fahrzeuge_ohne_besitzer", "offen": True}, {"_id": 0})
        await w.db.vehicles.delete_many({"dealer_id": fremd})
        # Runde 18: Der Melder zaehlt die besitzerlosen Fahrzeuge SELBST aus der
        # Datenbank (vorher schloss m6 den Alarm von m4 wieder). Er schliesst
        # also nur, wenn wirklich keines mehr da ist — die geteilte
        # Entwicklungs-DB haelt fremde Altlasten, deshalb der Abgleich.
        await M._offene_besitzer_melden(w.db, 0)
        rest = await w.db.vehicles.count_documents(M.OHNE_BESITZER)
        alarm2 = await w.db.betriebsalarme.find_one({"typ": "fahrzeuge_ohne_besitzer", "offen": True}, {"_id": 0})
        return s, alarm, alarm2, rest

    s, alarm, alarm2, rest = w.run(lauf())
    assert s["offen"] >= 1 and alarm and alarm["details"]["anzahl"] >= 1
    assert (alarm2 is None) == (rest == 0),         "der Alarm folgt genau der Zahl besitzerloser Fahrzeuge"
