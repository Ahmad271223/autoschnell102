# -*- coding: utf-8 -*-
"""Runde 17 (08.09.2026) — gemeinsame Grundlagen der Fixes:
set_lifecycle mit extra_set/CAS, fahrzeug_bereich ohne geloeschte,
Datum/Uhrzeit-Helfer, log_activity_sicher, Unique-Index mit Feldliste
(nur Alarm bei Altdubletten), Termin-Index-Alarm, production_check-Pflicht
fuer VERTRAG_LOESCHUNG_AKTIV, Betriebsseite zeigt Index-Zustand.
"""
import asyncio
import inspect
import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
DB_NAME = os.environ.get("DB_NAME") or "autoschnell"
WURZEL = Path(__file__).resolve().parents[2]


def _jetzt():
    return datetime.now(timezone.utc).isoformat()


def _module(name):
    import importlib
    return importlib.import_module(name)


@pytest.fixture
def welt():
    from motor.motor_asyncio import AsyncIOMotorClient
    s = uuid.uuid4().hex[:10]
    mods = [_module(n) for n in ("deps", "lifecycle", "betrieb")]
    alt = [(m, getattr(m, "db", None)) for m in mods]

    class _Ctx:
        def __init__(self):
            self.s = s
            self.dealer_id = f"d_r17g_{s}"
            self.chef = {"id": f"chef_r17g_{s}", "dealer_id": self.dealer_id, "role": "dealer"}
            self.sucher = {"id": f"su_r17g_{s}", "dealer_id": self.dealer_id, "role": "sucher"}
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
                 "owner_user_id": self.sucher["id"], "data": {"make_label": "BMW"},
                 "created_at": _jetzt(), "updated_at": _jetzt()}
            d.update(extra)
            return d

    ctx = _Ctx()
    yield ctx
    try:
        ctx.run(ctx.db.vehicles.delete_many({"dealer_id": ctx.dealer_id}))
        ctx.run(ctx.db.activity_logs.delete_many({"dealer_id": ctx.dealer_id}))
        ctx.run(ctx.db.betriebsalarme.delete_many({"ref": {"$regex": f"r17g_{s}"}}))
        ctx.run(ctx.db[f"r17g_{s}"].drop())
    finally:
        for m, d in alt:
            if d is not None:
                m.db = d
        ctx.client.close()
        ctx.loop.close()


# ================================================= set_lifecycle: extra_set + CAS
def test_01_set_lifecycle_schreibt_zusatzfelder_im_selben_write(welt):
    L = _module("lifecycle")
    vid = f"v_{welt.s}"

    async def lauf():
        await welt.db.vehicles.insert_one(welt.fahrzeug(vid, lifecycle="abgeholt",
                                                        data={"images": ["a.jpg"], "make_label": "BMW"}))
        await L.set_lifecycle(vid, welt.dealer_id, "geloescht", user=welt.chef,
                              extra_set={"data.images": [], "deleted_at": "2026-09-08"},
                              extra_unset={"inserat_aktuell": ""})
        v = await welt.db.vehicles.find_one({"id": vid}, {"_id": 0})
        # gleicher Zustand: nur Zusatzfelder, kein zweites Audit
        await L.set_lifecycle(vid, welt.dealer_id, "geloescht", user=welt.chef,
                              extra_set={"deleted_at": "2026-09-09"})
        v2 = await welt.db.vehicles.find_one({"id": vid}, {"_id": 0})
        n_audit = await welt.db.activity_logs.count_documents(
            {"dealer_id": welt.dealer_id, "action": "fahrzeug.status.geloescht"})
        return v, v2, n_audit

    v, v2, n_audit = welt.run(lauf())
    assert v["lifecycle"] == "geloescht" and v["data"]["images"] == [] and v["deleted_at"] == "2026-09-08"
    assert v2["deleted_at"] == "2026-09-09" and n_audit == 1


def test_02_set_lifecycle_cas_erkennt_parallelen_wechsel(welt, monkeypatch):
    L = _module("lifecycle")
    vid = f"v_{welt.s}"

    async def lauf():
        await welt.db.vehicles.insert_one(welt.fahrzeug(vid, lifecycle="abgeholt"))
        echt = welt.db.vehicles.find_one

        async def lesen_dann_aendern(*a, **k):
            doc = await echt(*a, **k)
            # zwischen Lesen und Schreiben setzt ein anderer Request "verkauft"
            await welt.db.vehicles.update_one({"id": vid}, {"$set": {"lifecycle": "verkauft"}})
            return doc
        from motor.motor_asyncio import AsyncIOMotorCollection as K
        monkeypatch.setattr(K, "find_one", lambda self, *a, **k: (
            lesen_dann_aendern(*a, **k) if self.name == "vehicles" else echt.__func__(self, *a, **k)))
        with pytest.raises(L.LifecycleError) as e:
            await L.set_lifecycle(vid, welt.dealer_id, "bestand", user=welt.chef)
        monkeypatch.undo()
        return str(e.value), await welt.db.vehicles.find_one({"id": vid}, {"_id": 0, "lifecycle": 1})

    msg, v = welt.run(lauf())
    assert "zwischenzeitlich" in msg and v["lifecycle"] == "verkauft"


def test_03_try_set_lifecycle_loggt_uebersprungene_uebergaenge(welt, caplog):
    L = _module("lifecycle")
    vid = f"v_{welt.s}"

    async def lauf():
        await welt.db.vehicles.insert_one(welt.fahrzeug(vid, lifecycle="verkauft"))
        with caplog.at_level(logging.INFO, logger="autohandel"):
            await L.try_set_lifecycle(vid, welt.dealer_id, "vertrag_erstellt", user=welt.chef)
        return await welt.db.vehicles.find_one({"id": vid}, {"_id": 0, "lifecycle": 1})

    v = welt.run(lauf())
    assert v["lifecycle"] == "verkauft"
    assert any("uebersprungen" in r.getMessage() for r in caplog.records)


# ================================================= fahrzeug_bereich ohne geloeschte
def test_04_fahrzeug_bereich_blendet_geloeschte_aus():
    D = _module("deps")
    chef = {"id": "c", "dealer_id": "d", "role": "dealer"}
    su = {"id": "s", "dealer_id": "d", "role": "sucher"}
    assert D.fahrzeug_bereich(chef) == {"dealer_id": "d", "lifecycle": {"$ne": "geloescht"}}
    assert D.fahrzeug_bereich(su, mit_geloeschten=True) == {
        "dealer_id": "d", "$or": [{"owner_user_id": "s"}, {"mitbearbeiter_ids": "s"}]}
    sig = inspect.signature(D.fahrzeug_im_bereich)
    assert sig.parameters["mit_geloeschten"].default is True, \
        "Termine/Beweise zu geloeschten Fahrzeugen bleiben im Bereich"


def test_05_geloeschtes_fahrzeug_fuer_liste_unsichtbar_akte_chef_sichtbar(welt):
    D = _module("deps")
    vid = f"v_{welt.s}"

    async def lauf():
        await welt.db.vehicles.insert_one(welt.fahrzeug(vid, lifecycle="geloescht"))
        n_normal = await welt.db.vehicles.count_documents({"id": vid, **D.fahrzeug_bereich(welt.chef)})
        n_akte = await welt.db.vehicles.count_documents(
            {"id": vid, **D.fahrzeug_bereich(welt.chef, mit_geloeschten=True)})
        im_bereich = await D.fahrzeug_im_bereich(welt.sucher, vid)
        return n_normal, n_akte, im_bereich

    n_normal, n_akte, im_bereich = welt.run(lauf())
    assert n_normal == 0 and n_akte == 1 and im_bereich is True


# ================================================= Datum/Uhrzeit-Helfer
@pytest.mark.parametrize("wert,ok", [("2026-09-15", True), ("", True), (None, True), ("2026-99-99", False),
                                     ("15.09.2026", False), ("blabla", False), ("2026-02-30", False)])
def test_06_datum_iso_pruefen(wert, ok):
    D = _module("deps")
    if ok:
        assert D.datum_iso_pruefen(wert) in (wert, "", None)
    else:
        with pytest.raises(ValueError):
            D.datum_iso_pruefen(wert)


@pytest.mark.parametrize("wert,ok", [("09:30", True), ("23:59", True), ("", True), ("24:00", False),
                                     ("9:30", False), ("09:60", False)])
def test_07_uhrzeit_hhmm_pruefen(wert, ok):
    D = _module("deps")
    if ok:
        assert D.uhrzeit_hhmm_pruefen(wert) == wert
    else:
        with pytest.raises(ValueError):
            D.uhrzeit_hhmm_pruefen(wert)


# ================================================= log_activity_sicher
def test_08_log_activity_sicher_wirft_nie(welt, monkeypatch):
    D = _module("deps")

    async def lauf():
        ok = await D.log_activity_sicher(welt.dealer_id, welt.chef["id"], "r17.test", ref="x")

        async def kaputt(*a, **k):
            raise RuntimeError("DB weg")
        monkeypatch.setattr(D, "log_activity", kaputt)
        ok2 = await D.log_activity_sicher(welt.dealer_id, welt.chef["id"], "r17.test2")
        return ok, ok2

    ok, ok2 = welt.run(lauf())
    assert ok is True and ok2 is False


# ================================================= Unique-Index mit Feldliste
def test_09_unique_index_sicher_feldliste_alarm_statt_abbruch(welt, monkeypatch):
    S = _module("indizes")
    B = _module("betrieb")
    monkeypatch.setattr(S, "db", welt.db)
    coll = welt.db[f"r17g_{welt.s}"]

    async def lauf():
        await coll.insert_many([{"dealer_id": "d", "id": "v1"}, {"dealer_id": "d", "id": "v1"},
                                {"dealer_id": "d", "id": "v2"}])
        monkeypatch.setenv("APP_ENV", "production")
        ok = await S._unique_index_sicher(coll, ["dealer_id", "id"], abbruch_in_produktion=False)
        alarm = await welt.db.betriebsalarme.find_one({"typ": "unique_index_fehlt", "offen": True,
                                                       "ref": f"{coll.name}.dealer_id.id"}, {"_id": 0})
        # Dublette bereinigen -> Index kommt, Alarm schliesst sich
        await coll.delete_one({"dealer_id": "d", "id": "v1"})
        ok2 = await S._unique_index_sicher(coll, ["dealer_id", "id"], abbruch_in_produktion=False)
        info = await coll.index_information()
        alarm2 = await welt.db.betriebsalarme.find_one({"typ": "unique_index_fehlt", "offen": True,
                                                        "ref": f"{coll.name}.dealer_id.id"})
        return ok, alarm, ok2, info, alarm2

    ok, alarm, ok2, info, alarm2 = welt.run(lauf())
    assert ok is False and alarm and "v1" in alarm["details"]["beispiele"]
    assert ok2 is True and any(i.get("unique") and i["key"] == [("dealer_id", 1), ("id", 1)] for i in info.values())
    assert alarm2 is None
    quelle = (WURZEL / "backend" / "server.py").read_text(encoding="utf-8")
    assert 'db.vehicles, ["dealer_id", "id"], abbruch_in_produktion=False' in quelle
    # Runde 29 (12.09.2026): Der Start bricht weiterhin NICHT ab — aber die
    # Instanz meldet sich ueber /ready als nicht bereit, damit der Load
    # Balancer sie nicht in die Rotation nimmt.
    assert 'BETRIEBSBEREIT["index_vehicles"]' in quelle


def test_10_termin_unique_index_alarmiert_bei_dubletten_und_filtert_leere_ids(welt, monkeypatch):
    S = _module("indizes")
    monkeypatch.setattr(S, "db", welt.db)
    q = inspect.getsource(S._termin_unique_index)
    assert '"$gt": ""' in q and 'alarm(db, "termin_index_fehlt"' in q and "drop_index" in q

    async def lauf():
        # zwei offene Termine mit LEERER vehicle_id duerfen nie als Dublette zaehlen
        aids = [f"a_{welt.s}_{i}" for i in range(2)]
        await welt.db.appointments.insert_many([
            {"id": a, "dealer_id": welt.dealer_id, "vehicle_id": "", "status": "offen", "created_at": _jetzt()}
            for a in aids])
        try:
            n = await welt.db.appointments.aggregate([
                {"$match": {"vehicle_id": {"$type": "string", "$gt": ""}, "status": "offen",
                            "dealer_id": welt.dealer_id}},
                {"$group": {"_id": "$vehicle_id", "n": {"$sum": 1}}}]).to_list(10)
        finally:
            await welt.db.appointments.delete_many({"id": {"$in": aids}})
        return n

    assert welt.run(lauf()) == []


# ================================================= production_check
def test_11_production_check_verlangt_bewusste_entscheidung_zur_loeschung():
    q = (WURZEL / "backend" / "production_check.py").read_text(encoding="utf-8")
    i = q.index('elif ist_prod and not os.environ.get("VERTRAG_LOESCHUNG_AKTIV", "").strip():')
    assert "fehler.append" in q[i:i + 400], "fehlende Variable ist in Produktion ein Konfigurationsfehler"
    j = q.index("elif ist_prod:", i + 10)
    assert "warnungen.append" in q[j:j + 500], "ausdruecklich false bleibt Warnung (Trockenlauf)"


# ================================================= Betriebsseite
def test_12_betriebsseite_zeigt_index_zustand_und_nachholen_legt_indizes_an():
    q = (WURZEL / "backend" / "routes" / "admin.py").read_text(encoding="utf-8")
    assert '"termin_index_aktiv"' in q and '"fahrzeug_index_aktiv"' in q
    i = q.index("async def admin_betrieb_nachholen")
    assert "_termin_unique_index()" in q[i:i + 900] and "_unique_index_sicher(" in q[i:i + 900]


def test_13_alarm_schliessen(welt):
    B = _module("betrieb")
    ref = f"r17g_{welt.s}"

    async def lauf():
        await B.alarm(welt.db, "r17_test", ref=ref, info="x")
        n = await B.alarm_schliessen(welt.db, "r17_test", ref=ref)
        offen = await welt.db.betriebsalarme.count_documents({"typ": "r17_test", "ref": ref, "offen": True})
        return n, offen

    n, offen = welt.run(lauf())
    assert n == 1 and offen == 0


# ================================================= Uebergabe-Regel (Umbau Kaufvorgaenge: Vertrag/Vorgang ist der Anker)
def test_14_uebergabe_laesst_altem_bearbeiter_eigene_termine_berichte_und_snapshots(welt):
    """Umbau Kaufvorgaenge 09.09.2026 (Beschluss Ahmad): Sucher A hat zu
    seinem Fahrzeug einen Vertrag, einen eigenen Termin (mit Bericht und
    Protokoll) und einen Snapshot.

    Wunsch Ahmad 21.09.2026 (R1-01): der Chef kann das Fahrzeug NICHT mehr
    einem anderen Konto zuweisen (410) — A behaelt alles, Kollege B bekommt
    nichts. Frueher lief die Uebergabe hier ueber PUT /besitzer an B.

    Runde 13 (15.09.2026, Reviewer-Liste "Uebergabe"): die Uebergabe nimmt den
    ganzen Vorgang mit — Vertrag, Termin, Bericht und Protokoll zu diesem
    Fahrzeug gehen an das neue Konto; A behaelt nur, was nicht an diesem
    Fahrzeug haengt. Diese Mechanik (vorgang_uebergeben) bleibt fuer
    "Aus meiner Liste entfernen" (A selbst, Vorgang an den Chef) und die
    Konto-Loeschung; geprueft wird sie jetzt ueber das Entfernen durch A.
    A verliert danach die Fahrzeug-Sichtbarkeit (weder Besitzer noch
    Mitbearbeiter; der Vertrag wurde hier direkt eingefuegt, nicht ueber
    create_contract, das den Sucher zum Mitbearbeiter macht). Der Kollege B
    sieht zu keinem Zeitpunkt A's Termin, Bericht oder Protokoll."""
    B = _module("routes.bestand")
    L = _module("routes.listings")
    A_ = _module("routes.appointments")
    P = _module("routes.protocols")
    K = _module("kaufvorgang")
    from fastapi import HTTPException, Response
    mods = (B, L, A_, P, K)
    alt = [(m, m.db) for m in mods]
    for m in mods:
        m.db = welt.db
    a = welt.sucher
    b = {"id": f"sb_r17g_{welt.s}", "dealer_id": welt.dealer_id, "role": "sucher"}
    vid, aid, sid, cid, pid = (f"v_{welt.s}", f"t_{welt.s}", f"s_{welt.s}", f"c_{welt.s}", f"p_{welt.s}")

    chef = welt.chef

    async def lauf():
        await welt.db.users.insert_many([
            {"id": chef["id"], "dealer_id": welt.dealer_id, "role": "dealer", "active": True,
             "first_name": "Chef", "last_name": "C", "email": f"{chef['id']}@e2etest-mail.de"},
            {"id": a["id"], "dealer_id": welt.dealer_id, "role": "sucher", "active": True,
             "first_name": "Anna", "last_name": "A", "email": f"{a['id']}@e2etest-mail.de"},
            {"id": b["id"], "dealer_id": welt.dealer_id, "role": "sucher", "active": True,
             "first_name": "Ben", "last_name": "B", "email": f"{b['id']}@e2etest-mail.de"}])
        await welt.db.dealers.insert_one({"id": welt.dealer_id, "user_id": chef["id"],
                                          "company_name": "R17g GmbH", "created_at": _jetzt()})
        await welt.db.vehicles.insert_one(welt.fahrzeug(vid, owner_user_id=a["id"], lifecycle="abgeholt"))
        await welt.db.generated_pdfs.insert_one({"id": cid, "dealer_id": welt.dealer_id, "user_id": a["id"],
                                                 "vehicle_id": vid, "created_at": _jetzt()})
        # Der Termin zum Fahrzeug ist erledigt (abgeholt) — mit einem noch
        # laufenden Termin laesst "Aus meiner Liste entfernen" nicht zu (409).
        await welt.db.appointments.insert_many([
            {"id": aid, "dealer_id": welt.dealer_id, "vehicle_id": vid, "contract_id": cid,
             "created_by": a["id"], "status": "abgeholt", "pickup_date": "2099-01-01", "created_at": _jetzt()},
            {"id": f"{aid}_ohne", "dealer_id": welt.dealer_id, "created_by": a["id"], "status": "offen",
             "pickup_date": "2099-01-02", "created_at": _jetzt()}])
        await welt.db.listing_snapshots.insert_one({"id": sid, "dealer_id": welt.dealer_id, "user_id": a["id"],
                                                    "vehicle_id": vid, "status": "ready", "png_path": "x",
                                                    "pdf_path": "y", "created_at": _jetzt()})
        await welt.db.pickup_reports.insert_one({"id": f"r_{welt.s}", "dealer_id": welt.dealer_id,
                                                 "appointment_id": aid, "vehicle_id": vid, "version": 1,
                                                 "created_at": _jetzt()})
        await welt.db.pickup_protocols.insert_one({"id": pid, "dealer_id": welt.dealer_id, "appointment_id": aid,
                                                   "vehicle_id": vid, "status": "final", "version": 1,
                                                   "pdf_path": "p.pdf", "created_at": _jetzt()})
        vorher = {t["id"] for t in await A_.list_appointments(Response(), a)}
        fzg_a_vorher = (await L.get_vehicle_detail(vid, a))["id"]
        # Wunsch Ahmad 21.09.2026 (R1-01): der Chef kann nicht mehr an B umhaengen
        with pytest.raises(HTTPException) as e_chef:
            await B.fahrzeug_umhaengen_entfernt(vid, chef)
        nach_chef_a = {t["id"] for t in await A_.list_appointments(Response(), a)}
        nach_chef_b = {t["id"] for t in await A_.list_appointments(Response(), b)}
        nach_chef_fzg_a = (await L.get_vehicle_detail(vid, a))["id"]
        nach_chef_vertrag_a = await welt.db.generated_pdfs.count_documents({"id": cid, "user_id": a["id"]})
        # Die verbliebene Uebergabe: A entfernt das Fahrzeug selbst aus seiner Liste
        # -> Fahrzeug und ganzer Vorgang gehen an den Chef (vorgang_uebergeben).
        out = await B.vehicle_fuer_sucher_entfernen(vid, a)
        nachher_a = {t["id"] for t in await A_.list_appointments(Response(), a)}
        nachher_b = {t["id"] for t in await A_.list_appointments(Response(), b)}
        nachher_chef = {t["id"] for t in await A_.list_appointments(Response(), chef)}
        t_neu = await welt.db.appointments.find_one({"id": aid}, {"_id": 0})
        darf_a = await A_._sucher_darf(a, t_neu)
        darf_b = await A_._sucher_darf(b, t_neu)
        rep_chef = await A_.get_pickup_report(aid, 0, chef)
        with pytest.raises(HTTPException) as e_rep:
            await A_.get_pickup_report(aid, 0, a)
        snaps_a = {s["id"] for s in await L.list_snapshots(None, a)}
        snaps_b = {s["id"] for s in await L.list_snapshots(None, b)}
        # Befund 82 (16.09.2026): nach der Uebergabe haengt der alte Snapshot
        # am Fahrzeug — A (Ersteller) verliert ihn wie Termin, Bericht und Vertrag.
        with pytest.raises(HTTPException) as e_snap:
            await L._load_snapshot_or_404(sid, a)
        snap_a = e_snap.value.status_code
        snap_chef = (await L._load_snapshot_or_404(sid, chef))["id"]
        proto_a = await P._protokoll_im_bereich(a, {"vehicle_id": vid, "appointment_id": aid})
        proto_b = await P._protokoll_im_bereich(b, {"vehicle_id": vid, "appointment_id": aid})
        vertrag = await welt.db.generated_pdfs.find_one({"id": cid}, {"_id": 0})
        with pytest.raises(HTTPException) as e_fzg:
            await L.get_vehicle_detail(vid, a)
        v_neu = await welt.db.vehicles.find_one({"id": vid}, {"_id": 0})
        await welt.db.users.delete_many({"id": {"$in": [chef["id"], a["id"], b["id"]]}})
        await welt.db.dealers.delete_many({"id": welt.dealer_id})
        for c in ("appointments", "generated_pdfs", "listing_snapshots", "pickup_reports", "pickup_protocols",
                  "kaufvorgaenge"):
            await welt.db[c].delete_many({"dealer_id": welt.dealer_id})
        return (vorher, fzg_a_vorher, e_chef.value.status_code, nach_chef_a, nach_chef_b, nach_chef_fzg_a,
                nach_chef_vertrag_a, out, nachher_a, nachher_b, nachher_chef, t_neu, darf_a, darf_b, rep_chef,
                e_rep.value.status_code, snaps_a, snaps_b, snap_a, snap_chef, proto_a, proto_b, vertrag,
                e_fzg.value.status_code, v_neu)

    try:
        (vorher, fzg_a_vorher, s_chef, nach_chef_a, nach_chef_b, nach_chef_fzg_a, nach_chef_vertrag_a, out,
         nachher_a, nachher_b, nachher_chef, t_neu, darf_a, darf_b, rep_chef, s_rep, snaps_a, snaps_b,
         snap_a, snap_chef, proto_a, proto_b, vertrag, s_fzg, v_neu) = welt.run(lauf())
    finally:
        for m, d in alt:
            m.db = d
    assert vorher == {aid, f"{aid}_ohne"} and fzg_a_vorher == vid
    # R1-01: Chef bekommt 410, bei A bleibt alles, B bekommt nichts
    assert s_chef == 410
    assert nach_chef_a == vorher and nach_chef_fzg_a == vid and nach_chef_vertrag_a == 1
    assert nach_chef_b == set(), "Kollege B bekommt nichts"
    # Runde 13 (15.09.2026, Uebergabe = ganzer Vorgang) — jetzt ueber das
    # Entfernen durch A selbst: Termin, Vertrag und Bericht zu diesem Fahrzeug
    # gehen an den Chef; A behaelt nur den Termin ohne Fahrzeug.
    assert out["an_chef"] is True and out["uebergabe"]["termine"] == 1 and out["uebergabe"]["vertraege"] == 1
    assert v_neu["owner_user_id"] == chef["id"] and v_neu["entfernt_von_sucher"] == a["id"]
    assert nachher_a == {f"{aid}_ohne"}, "A behaelt nur Termine ohne dieses Fahrzeug"
    assert nachher_b == set(), "B bekommt auch beim Entfernen durch A nichts"
    assert aid in nachher_chef
    assert t_neu["created_by"] == chef["id"] and t_neu["uebergeben_von"] == a["id"]
    assert darf_a is False and darf_b is False
    assert rep_chef["report"]["id"] == f"r_{welt.s}" and s_rep == 404
    assert snaps_a == set() and snaps_b == set(), "Snapshot: nach der Uebergabe nur noch der Chef"
    assert snap_a == 404 and snap_chef == sid
    assert proto_a is False and proto_b is False
    assert vertrag["user_id"] == chef["id"] and vertrag["uebergeben_von"] == a["id"], \
        "der Vertrag geht mit dem Vorgang an den Chef (Runde 13)"
    assert s_fzg == 404, "A verliert die Fahrzeug-Sichtbarkeit"
