# -*- coding: utf-8 -*-
"""Runde 17 (09/2026): bestaetigte Audit-Befunde Bestand / Weiterverkauf /
Fahrzeugpool / Snapshots / Aufraeumer (Nummern = Zeilen im Pruefprotokoll).

  261/263/287  Entscheidung: Statuswechsel + Zusatzfelder in EINEM Write (CAS)
  283          Fahrzeug loeschen beendet aktive Inserate
  275          update_bestand: Dotted-Paths, CAS auf Lifecycle -> 409
  285          Bestandsliste ignoriert ?lifecycle=geloescht; Akte fuer den Chef mit Historie
  337/336      Kosten-/Maengellisten gedeckelt
  289          create_draft: Lifecycle-Pfad vor dem Insert, set_lifecycle danach (Rollback)
  290          Rollback im Statuswechsel stellt reserved_for wieder her
  398/399      Audit nach dem Commit bricht nicht ab; Statuswechsel idempotent
  288          Fahrzeugpool: Schutz nur durch eigene Vertraege/Termine/Inserate
  382/383      Fahrzeug-ID quellen-eindeutig + Legacy-Rueckfall
  292          /listings/ingest gedeckelt (429)
  324          GET /snapshots mit Cursor und X-Truncated
  310/313/316  Snapshot-Job: Upload-Reste vormerken, CAS beim ready-Write, Heartbeat/Reaper
  307          failed/expired-Snapshotzeilen nach 7 Tagen weg
  400          marktplatz_rotieren in 1000er-Paketen
  396          Dubletten-Skript prueft vehicles (dealer_id, id)
  287          Selbstheilung: Bestand ohne Frist bekommt eine

In-Prozess wie Runde 14-16: Routen-Funktionen direkt mit Fake-`user`-Dicts,
Modul-`db` zeigt auf einen Test-Client (nur Mongo noetig).
"""
import asyncio
import inspect
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException, Response
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
DB_NAME = os.environ.get("DB_NAME") or "autoschnell"
KA_URL = "https://www.kleinanzeigen.de/s-anzeige/test-auto/1234567890-216-1"


def _jetzt():
    return datetime.now(timezone.utc).isoformat()


def _vor(**delta):
    return (datetime.now(timezone.utc) - timedelta(**delta)).isoformat()


class _Welt:
    def __init__(self):
        s = uuid.uuid4().hex[:10]
        self.s = s
        self.dealer_id = f"d_r17_{s}"
        self.fremd = f"d_r17f_{s}"
        self.chef = {"id": f"chef_r17_{s}", "dealer_id": self.dealer_id, "role": "dealer"}
        self.a = {"id": f"sa_r17_{s}", "dealer_id": self.dealer_id, "role": "sucher"}

    def konten(self):
        return [
            {"id": self.chef["id"], "dealer_id": self.dealer_id, "role": "dealer", "active": True,
             "email": f"{self.chef['id']}@e2etest-mail.de", "created_at": _jetzt()},
            {"id": self.a["id"], "dealer_id": self.dealer_id, "role": "sucher", "active": True,
             "first_name": "Anna", "last_name": "A", "email": f"{self.a['id']}@e2etest-mail.de",
             "created_at": _jetzt()},
        ]

    def fahrzeug(self, vid, lifecycle="abgeholt", owner=None, **extra):
        doc = {"id": vid, "dealer_id": self.dealer_id, "lifecycle": lifecycle,
               "lifecycle_changed_at": _jetzt(), "mobile_ad_id": vid[2:],
               "data": {"make_label": "BMW", "model_label": "320d",
                        "images": ["https://x/1.jpg"], "image_urls": ["https://x/2.jpg"]},
               "status": "verglichen", "created_at": _jetzt(), "updated_at": _jetzt()}
        doc["owner_user_id"] = owner or self.chef["id"]
        doc.update(extra)
        return doc

    def inserat(self, lid, vid, status="entwurf", **extra):
        doc = {"id": lid, "dealer_id": self.dealer_id, "vehicle_id": vid, "status": status,
               "title": "Golf", "description": "", "known_defects": [], "data": {},
               "prices": {"public": 9900.0, "b2b": None, "network": None},
               "photos": {"mode": "einkauf", "einkauf_urls": [], "uploaded_keys": []},
               "costs": [], "counted_periods": [], "created_by": self.chef["id"],
               "created_at": _jetzt(), "updated_at": _jetzt()}
        doc.update(extra)
        return doc

    def snap(self, sid, **extra):
        d = {"id": sid, "dealer_id": self.dealer_id, "user_id": self.chef["id"],
             "vehicle_id": f"v_{self.s}", "status": "ready", "created_at": _jetzt(),
             "completed_at": _jetzt(), "png_path": None, "pdf_path": None,
             "source_url": KA_URL}
        d.update(extra)
        return d

    async def aufraeumen(self, db):
        for c in ("vehicles", "resale_listings", "listing_interest", "activity_logs",
                  "listing_snapshots", "storage_delete_retry", "generated_pdfs",
                  "appointments", "users", "dealers", "betriebsalarme"):
            await db[c].delete_many({"dealer_id": {"$in": [self.dealer_id, self.fremd]}})
        await db.dealers.delete_many({"id": {"$in": [self.dealer_id, self.fremd]}})
        await db.listing_interest.delete_many({"id": {"$regex": f"^li_{self.s}_"}})
        await db.rate_limits.delete_many({"_id": {"$regex": f"^ingest:{self.chef['id']}:"}})
        await db.listing_snapshots.delete_many({"id": {"$regex": f"_{self.s}$"}})


def _module(name):
    import importlib
    return importlib.import_module(name)


@pytest.fixture
def welt():
    from motor.motor_asyncio import AsyncIOMotorClient
    w = _Welt()
    # kaufvorgang: create_draft fragt seit Runde 19 den Einkaufspreis-Vorschlag
    # ab — ohne Tausch nutzte der Test den globalen Client eines fremden,
    # schon geschlossenen Test-Loops ("Event loop is closed", reihenfolgeabhaengig).
    names = ["deps", "lifecycle", "kaufvorgang", "routes.bestand", "routes.resale",
             "routes.listings", "routes.marketplace", "routes.team", "routes.contracts"]
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
    ctx.run(ctx.db.dealers.insert_one({"id": w.dealer_id, "user_id": w.chef["id"],
                                       "company_name": "R17 GmbH", "created_at": _jetzt()}))
    yield ctx
    try:
        ctx.run(w.aufraeumen(ctx.db))
    finally:
        for m, d in alt:
            if d is not None:
                m.db = d
        ctx.client.close()
        ctx.loop.close()


def _status(exc_info):
    return exc_info.value.status_code


# ================================================= Nr. 261/263/287/283: Entscheidung
def test_01_loeschen_ein_write_fotos_weg_inserate_zu(welt):
    B = _module("routes.bestand")
    w, db = welt.w, welt.db
    vid = f"v_{w.s}"

    async def lauf():
        await db.vehicles.insert_one(w.fahrzeug(vid, known_defects=["Kratzer"]))
        await db.resale_listings.insert_many([
            w.inserat(f"l_e_{w.s}", vid, "entwurf"),
            w.inserat(f"l_z_{w.s}", vid, "zurueckgezogen"),
            w.inserat(f"l_v_{w.s}", vid, "verkauft"),
        ])
        await db.listing_interest.insert_one(
            {"id": f"li_{w.s}_1", "listing_id": f"l_e_{w.s}", "status": "offen",
             "history": [], "dealer_id": w.dealer_id})
        r = await B.vehicle_decision(vid, B.DecisionIn(decision="loeschen"), w.chef)
        v = await db.vehicles.find_one({"id": vid, "dealer_id": w.dealer_id}, {"_id": 0})
        ins = {l["id"]: l async for l in db.resale_listings.find({"vehicle_id": vid}, {"_id": 0})}
        it = await db.listing_interest.find_one({"id": f"li_{w.s}_1"}, {"_id": 0})
        logs = [l["action"] async for l in db.activity_logs.find({"dealer_id": w.dealer_id})]
        with pytest.raises(HTTPException) as e:      # geloescht -> nichts mehr erlaubt
            await B.vehicle_decision(vid, B.DecisionIn(decision="bestand"), w.chef)
        return r, v, ins, it, logs, _status(e)

    r, v, ins, it, logs, status = welt.run(lauf())
    assert r["lifecycle"] == "geloescht" and set(r["inserate_geloescht"]) == {f"l_e_{w.s}", f"l_z_{w.s}"}
    assert v["lifecycle"] == "geloescht" and v["deleted_at"]
    assert v["data"]["images"] == [] and v["data"]["image_urls"] == []
    assert v["data"]["make_label"] == "BMW" and v["known_defects"] == ["Kratzer"], \
        "nur Fotos + deleted_at, Rest der Akte bleibt"
    assert ins[f"l_e_{w.s}"]["status"] == "geloescht" and ins[f"l_e_{w.s}"]["geloescht_grund"] == "fahrzeug_geloescht"
    assert ins[f"l_z_{w.s}"]["status"] == "geloescht" and ins[f"l_v_{w.s}"]["status"] == "verkauft"
    assert it["status"] == "abgelehnt" and it["beendet_grund"] == "fahrzeug_geloescht"
    assert "fahrzeug.entscheidung.geloescht" in logs and logs.count("inserat.geloescht") == 2
    assert status == 409


def test_02_bestand_und_verkaufsentwurf_per_dotted_path(welt):
    B = _module("routes.bestand")
    w, db = welt.w, welt.db
    vid = f"v_{w.s}"

    async def lauf():
        await db.vehicles.insert_one(w.fahrzeug(vid, bestand={"notes": "bleibt", "costs": []}))
        r1 = await B.vehicle_decision(vid, B.DecisionIn(decision="bestand"), w.chef)
        v1 = await db.vehicles.find_one({"id": vid, "dealer_id": w.dealer_id}, {"_id": 0})
        r2 = await B.vehicle_decision(vid, B.DecisionIn(decision="verkaufsentwurf"), w.chef)
        v2 = await db.vehicles.find_one({"id": vid, "dealer_id": w.dealer_id}, {"_id": 0})
        return r1, v1, r2, v2

    r1, v1, r2, v2 = welt.run(lauf())
    assert v1["lifecycle"] == "bestand" and v1["bestand"]["expires_at"] == r1["expires_at"]
    assert v1["bestand"]["notes"] == "bleibt" and v1["bestand"]["saved_at"]
    assert v2["lifecycle"] == "verkaufsentwurf" and v2["bestand"]["expires_at"] is None
    assert r2["expires_at"] is None
    assert v2["bestand"]["saved_at"] == v1["bestand"]["saved_at"] and v2["bestand"]["notes"] == "bleibt"


def test_03_entscheidung_bei_rennen_409(welt, monkeypatch):
    B = _module("routes.bestand")
    LC = _module("lifecycle")
    w, db = welt.w, welt.db
    vid = f"v_{w.s}"

    async def _rennen(*a, **k):
        raise LC.LifecycleError("Fahrzeugstatus wurde zwischenzeitlich geändert")
    monkeypatch.setattr(B, "set_lifecycle", _rennen)

    async def lauf():
        await db.vehicles.insert_one(w.fahrzeug(vid))
        with pytest.raises(HTTPException) as e:
            await B.vehicle_decision(vid, B.DecisionIn(decision="loeschen"), w.chef)
        v = await db.vehicles.find_one({"id": vid, "dealer_id": w.dealer_id}, {"_id": 0})
        return _status(e), v

    status, v = welt.run(lauf())
    assert status == 409 and v["lifecycle"] == "abgeholt" and v["data"]["images"], \
        "kein zweiter Write ohne Statuswechsel"


# ================================================= Nr. 275: update_bestand
def test_04_update_bestand_nur_gesendete_felder_und_cas(welt, monkeypatch):
    B = _module("routes.bestand")
    w, db = welt.w, welt.db
    vid = f"v_{w.s}"

    async def lauf():
        await db.vehicles.insert_one(w.fahrzeug(
            vid, lifecycle="bestand",
            bestand={"notes": "alt", "location": "Halle 1", "costs": [{"label": "x", "amount": 5}]}))
        r = await B.update_bestand(vid, B.BestandUpdateIn(location="Halle 2"), w.chef)
        v = await db.vehicles.find_one({"id": vid, "dealer_id": w.dealer_id}, {"_id": 0})
        # Vorpruefung greift (409), kein Write
        await db.vehicles.update_one({"id": vid, "dealer_id": w.dealer_id},
                                     {"$set": {"lifecycle": "verkauft"}})
        with pytest.raises(HTTPException) as e1:
            await B.update_bestand(vid, B.BestandUpdateIn(notes="neu"), w.chef)
        # Rennen: Vorpruefung sieht noch "bestand", der Write trifft "verkauft" -> 409
        monkeypatch.setattr(B, "_abgeschlossen_sperren", lambda v, m: None)
        with pytest.raises(HTTPException) as e2:
            await B.update_bestand(vid, B.BestandUpdateIn(notes="neu"), w.chef)
        v2 = await db.vehicles.find_one({"id": vid, "dealer_id": w.dealer_id}, {"_id": 0})
        return r, v, _status(e1), _status(e2), v2

    r, v, s1, s2, v2 = welt.run(lauf())
    assert r["bestand"]["location"] == "Halle 2" and r["bestand"]["notes"] == "alt"
    assert v["bestand"] == {"notes": "alt", "location": "Halle 2", "costs": [{"label": "x", "amount": 5}]}
    assert s1 == 409 and s2 == 409 and v2["bestand"]["notes"] == "alt"
    q = inspect.getsource(B.update_bestand)
    assert '"bestand.location"' in q and '"$set": {"bestand": b' not in q


# ================================================= Nr. 285: Listen/Akte ohne geloeschte
def test_05_geloeschte_nur_in_der_chef_akte(welt):
    B = _module("routes.bestand")
    L = _module("routes.listings")
    w, db = welt.w, welt.db
    weg, da = f"v_weg{w.s}", f"v_da{w.s}"

    async def lauf():
        f_weg = w.fahrzeug(weg, lifecycle="geloescht", owner=w.a["id"])
        f_da = w.fahrzeug(da, lifecycle="bestand", owner=w.a["id"])
        await db.vehicles.insert_many([f_weg, f_da])
        # Runde 32: Sucher sehen im Bestand nur eigene Vertraege oder eigene
        # Abholungen — beide sind abgeholt; das geloeschte bleibt trotzdem draussen.
        await db.kaufvorgaenge.insert_many([
            {"id": f"k_{v['id']}", "dealer_id": v["dealer_id"], "user_id": w.a["id"],
             "vehicle_id": v["id"], "contract_id": f"c_{v['id']}", "status": "abgeholt"}
            for v in (f_weg, f_da)])
        liste = await B.list_bestand(w.chef, lifecycle="geloescht")
        liste_a = await B.list_bestand(w.a)
        fahrzeuge = await L.list_vehicles(w.chef)
        akte_chef = await B.vehicle_akte(weg, w.chef)
        with pytest.raises(HTTPException) as e1:
            await B.vehicle_akte(weg, w.a)
        with pytest.raises(HTTPException) as e2:
            await L.get_vehicle_detail(weg, w.chef)
        return liste, liste_a, fahrzeuge, akte_chef, _status(e1), _status(e2)

    liste, liste_a, fahrzeuge, akte_chef, s1, s2 = welt.run(lauf())
    assert [i["id"] for i in liste["items"]] == [da] and liste["counts"] == {"bestand": 1}
    assert [i["id"] for i in liste_a["items"]] == [da]
    assert {v["id"] for v in fahrzeuge} == {da}
    assert akte_chef["vehicle"]["id"] == weg and s1 == 404 and s2 == 404


# ================================================= Nr. 337/336: Eingabedeckel
def test_06_kosten_und_maengel_gedeckelt():
    B = _module("routes.bestand")
    R = _module("routes.resale")
    kosten = [{"label": "x", "amount": 1}]
    assert len(B.BestandUpdateIn(costs=kosten * 30).costs) == 30
    with pytest.raises(ValidationError):
        B.BestandUpdateIn(costs=kosten * 31)
    with pytest.raises(ValidationError):
        R.ListingUpdateIn(costs=kosten * 31)
    with pytest.raises(ValidationError):
        R.ListingUpdateIn(known_defects=["x" * 301])
    with pytest.raises(ValidationError):
        R.ListingUpdateIn(known_defects=["m"] * 51)
    assert R.ListingUpdateIn(known_defects=["x" * 300] * 50).known_defects[0] == "x" * 300


# ================================================= Nr. 289: create_draft
def test_07_create_draft_setzt_lifecycle_oder_rollt_zurueck(welt, monkeypatch):
    R = _module("routes.resale")
    LC = _module("lifecycle")
    w, db = welt.w, welt.db
    vid = f"v_{w.s}"

    async def lauf():
        await db.vehicles.insert_one(w.fahrzeug(vid, lifecycle="verglichen"))
        with pytest.raises(HTTPException) as e0:          # nicht verkaufsfaehig
            await R.create_draft(vid, w.chef)
        await db.vehicles.update_one({"id": vid, "dealer_id": w.dealer_id},
                                     {"$set": {"lifecycle": "abgeholt"}})
        r = await R.create_draft(vid, w.chef)
        v = await db.vehicles.find_one({"id": vid, "dealer_id": w.dealer_id}, {"_id": 0})
        logs = [l["action"] async for l in db.activity_logs.find({"dealer_id": w.dealer_id})]
        # Rennen am Fahrzeug NACH dem Insert: Entwurf wird zurueckgenommen
        await db.resale_listings.delete_many({"dealer_id": w.dealer_id})
        await db.vehicles.update_one({"id": vid, "dealer_id": w.dealer_id},
                                     {"$set": {"lifecycle": "abgeholt"}})

        async def _rennen(*a, **k):
            raise LC.LifecycleError("zwischenzeitlich geändert")
        monkeypatch.setattr(R, "set_lifecycle", _rennen)
        with pytest.raises(HTTPException) as e1:
            await R.create_draft(vid, w.chef)
        n = await db.resale_listings.count_documents({"dealer_id": w.dealer_id})
        return _status(e0), r, v, logs, _status(e1), n

    s0, r, v, logs, s1, n = welt.run(lauf())
    assert s0 == 400
    assert r["status"] == "entwurf" and v["lifecycle"] == "verkaufsentwurf"
    assert "fahrzeug.status.verkaufsentwurf" in logs and "inserat.entwurf" in logs
    assert s1 == 409 and n == 0, "Entwurf ohne passenden Fahrzeugstatus bleibt nicht liegen"
    q = inspect.getsource(R.create_draft)
    assert "await try_set_lifecycle(" not in q and "_lifecycle_pfad_oder_409" in q


# ================================================= Nr. 290: reserved_for-Rollback
def test_08_rollback_stellt_reserved_for_wieder_her(welt, monkeypatch):
    R = _module("routes.resale")
    LC = _module("lifecycle")
    w, db = welt.w, welt.db
    vid, lid = f"v_{w.s}", f"l_{w.s}"

    async def _rennen(*a, **k):
        raise LC.LifecycleError("zwischenzeitlich geändert")
    monkeypatch.setattr(R, "set_lifecycle", _rennen)

    async def lauf():
        await db.vehicles.insert_one(w.fahrzeug(vid, lifecycle="reserviert"))
        await db.resale_listings.insert_one(w.inserat(lid, vid, "reserviert", reserved_for="kaeufer-1"))
        with pytest.raises(HTTPException) as e:
            await R.set_listing_status(lid, R.ListingStatusIn(status="verkauft", sold_price=8000), w.chef)
        l = await db.resale_listings.find_one({"id": lid}, {"_id": 0})
        return _status(e), l

    status, l = welt.run(lauf())
    assert status == 409
    assert l["status"] == "reserviert" and l["reserved_for"] == "kaeufer-1"
    assert "sold_at" not in l and "sold_price" not in l and "sold_to_user_id" not in l


# ================================================= Nr. 398/399: Audit nach Commit, Idempotenz
def test_09_statuswechsel_idempotent_und_folgeschritte_brechen_nicht_ab(welt, monkeypatch):
    R = _module("routes.resale")
    w, db = welt.w, welt.db
    vid, lid = f"v_{w.s}", f"l_{w.s}"

    async def lauf():
        await db.vehicles.insert_one(w.fahrzeug(vid, lifecycle="verkaufsbereit"))
        await db.resale_listings.insert_one(w.inserat(lid, vid, "verkaufsbereit"))
        await db.listing_interest.insert_many([
            {"id": f"li_{w.s}_a", "listing_id": lid, "status": "offen", "history": []},
            {"id": f"li_{w.s}_b", "listing_id": lid, "status": "gegenangebot", "history": []}])
        # 1) Anfragen-Schliessen scheitert -> Antwort bleibt ok, Zustand geschrieben
        orig = R._anfragen_schliessen

        async def _kaputt(*a, **k):
            raise RuntimeError("Marktplatz nicht erreichbar")
        monkeypatch.setattr(R, "_anfragen_schliessen", _kaputt)
        r1 = await R.set_listing_status(lid, R.ListingStatusIn(status="verkauft", sold_price=7000), w.chef)
        l1 = await db.resale_listings.find_one({"id": lid}, {"_id": 0})
        v1 = await db.vehicles.find_one({"id": vid, "dealer_id": w.dealer_id}, {"_id": 0})
        monkeypatch.setattr(R, "_anfragen_schliessen", orig)
        # 2) Wiederholung: idempotent, Anfragen werden nachgezogen
        r2 = await R.set_listing_status(lid, R.ListingStatusIn(status="verkauft", sold_price=7000), w.chef)
        l2 = await db.resale_listings.find_one({"id": lid}, {"_id": 0})
        stati = {i["id"]: i["status"] async for i in db.listing_interest.find({"listing_id": lid})}
        return r1, l1, v1, r2, l2, stati

    r1, l1, v1, r2, l2, stati = welt.run(lauf())
    assert r1 == {"ok": True, "status": "verkauft"}
    assert l1["status"] == "verkauft" and l1["sold_price"] == 7000 and v1["lifecycle"] == "verkauft"
    assert r2 == {"ok": True, "status": "verkauft", "bereits": True}
    assert l2["sold_at"] == l1["sold_at"], "Wiederholung schreibt nichts erneut"
    assert set(stati.values()) == {"abgelehnt"}


def test_10_publish_audit_fehler_bricht_nicht_ab(welt, monkeypatch):
    R = _module("routes.resale")
    D = _module("deps")
    T = _module("routes.team")
    w, db = welt.w, welt.db
    vid, lid = f"v_{w.s}", f"l_{w.s}"

    async def _plan(dealer_id):
        return {"active": True, "quota": None, "used": 0, "period_key": "2026-09"}
    monkeypatch.setattr(T, "get_sale_plan_status", _plan)

    async def _audit_kaputt(*a, **k):
        raise RuntimeError("activity_logs nicht schreibbar")
    monkeypatch.setattr(D, "log_activity", _audit_kaputt)

    async def lauf():
        await db.vehicles.insert_one(w.fahrzeug(vid, lifecycle="verkaufsbereit"))
        await db.resale_listings.insert_one(w.inserat(lid, vid, "verkaufsbereit"))
        r = await R.publish_listing(lid, R.PublishIn(), w.chef)
        l = await db.resale_listings.find_one({"id": lid}, {"_id": 0})
        return r, l

    r, l = welt.run(lauf())
    assert r["ok"] is True and r["status"] == "veroeffentlicht"
    assert l["status"] == "veroeffentlicht" and "publish_lock_until" not in l
    assert "log_activity_sicher(" in inspect.getsource(R.publish_listing)


# ================================================= Nr. 288: Fahrzeugpool
def test_11_pool_schutz_nur_durch_eigene_firma(welt):
    F = _module("fahrzeugpool")
    w, db = welt.w, welt.db

    async def lauf():
        docs = [w.fahrzeug(f"v_{i}_{w.s}", lifecycle="verglichen", owner=w.a["id"],
                           updated_at=f"2026-01-0{i + 1}T00:00:00") for i in range(5)]
        await db.vehicles.insert_many(docs)
        await db.generated_pdfs.insert_many([
            # Fremdfirma hat einen Vertrag auf dieselbe Fahrzeug-ID: schuetzt NICHT
            {"id": f"c_fremd_{w.s}", "dealer_id": w.fremd, "vehicle_id": f"v_0_{w.s}",
             "user_id": "x", "created_at": _jetzt()},
            # eigener Vertrag: schuetzt
            {"id": f"c_eigen_{w.s}", "dealer_id": w.dealer_id, "vehicle_id": f"v_1_{w.s}",
             "user_id": w.a["id"], "created_at": _jetzt()},
        ])
        n = await F.fahrzeugpool_trimmen(db, w.dealer_id, limit=3, owner_user_id=w.a["id"])
        rest = set(await db.vehicles.distinct("id", {"dealer_id": w.dealer_id}))
        return n, rest

    n, rest = welt.run(lauf())
    assert n == 1 and f"v_0_{w.s}" not in rest, "Fremdvertrag schuetzt nicht"
    assert f"v_1_{w.s}" in rest, "eigener Vertrag schuetzt"


# ================================================= Nr. 382/383: Fahrzeug-ID je Quelle
def test_12_fahrzeug_id_quellen_eindeutig_mit_legacy_rueckfall(welt):
    L = _module("routes.listings")
    w, db = welt.w, welt.db
    ad = f"9{w.s[:6]}"

    async def lauf():
        ka = await L._fahrzeug_id("kleinanzeigen", ad, w.dealer_id)
        neu = await L._fahrzeug_id("mobile", ad, w.dealer_id)
        # Legacy: altes Dokument v_<id> von mobile.de ohne quelle -> weiterverwenden
        await db.vehicles.insert_one(w.fahrzeug(f"v_{ad}", lifecycle="gekauft", mobile_ad_id=ad))
        legacy = await L._fahrzeug_id("mobile", ad, w.dealer_id)
        # ... aber nicht, wenn es als Kleinanzeige markiert ist
        await db.vehicles.update_one({"id": f"v_{ad}", "dealer_id": w.dealer_id},
                                     {"$set": {"quelle": "kleinanzeigen"}})
        getrennt = await L._fahrzeug_id("mobile", ad, w.dealer_id)
        # neues Dokument vorhanden -> immer das neue
        k = await L._fahrzeug_uebernehmen(w.chef, f"v_mobile_{ad}", ad, {"make_label": "VW"},
                                          quelle="mobile")
        v_neu = await db.vehicles.find_one({"id": f"v_mobile_{ad}", "dealer_id": w.dealer_id}, {"_id": 0})
        wieder = await L._fahrzeug_id("mobile", ad, w.dealer_id)
        # Fremdfirma: eigenes Dokument, kein Rueckfall auf fremde Altdaten
        fremd = await L._fahrzeug_id("mobile", ad, w.fremd)
        return ka, neu, legacy, getrennt, k, v_neu, wieder, fremd

    ka, neu, legacy, getrennt, k, v_neu, wieder, fremd = welt.run(lauf())
    assert ka == f"v_{ad}" and neu == f"v_mobile_{ad}"
    assert legacy == f"v_{ad}" and getrennt == f"v_mobile_{ad}"
    assert k is None and v_neu["quelle"] == "mobile" and v_neu["mobile_ad_id"] == ad
    assert wieder == f"v_mobile_{ad}" and fremd == f"v_mobile_{ad}"
    q = inspect.getsource(L.compare)
    assert "await _fahrzeug_id(source, ad_id" in q and "quelle=source" in q
    assert 'vid = f"v_{ad_id}"' not in q


# ================================================= Nr. 292: Ingest-Limiter
def test_13_ingest_gedeckelt_429(welt, monkeypatch):
    L = _module("routes.listings")
    RL = _module("rate_limiter")
    w = welt.w
    monkeypatch.setattr(RL, "_RATE_LIMIT_ENABLED", True)
    assert L._ingest_limiter.max_attempts == 20 and L._ingest_limiter.window_seconds == 60

    async def lauf():
        for _ in range(20):
            assert await L._ingest_limiter.check(w.chef["id"])
        with pytest.raises(HTTPException) as e:
            await L.ingest_client_html(L.IngestIn(url=KA_URL, html="x" * 500), w.chef)
        return _status(e)

    assert welt.run(lauf()) == 429
    q = inspect.getsource(L.ingest_client_html)
    assert q.index("_ingest_limiter.check") < q.index("peek_cached_listing")


# ================================================= Nr. 324: Snapshot-Liste mit Cursor
def test_14_snapshots_liste_cursor_und_truncated(welt):
    L = _module("routes.listings")
    w, db = welt.w, welt.db

    async def lauf():
        basis = datetime(2026, 9, 1, tzinfo=timezone.utc)
        await db.listing_snapshots.insert_many([
            w.snap(f"s{i}_{w.s}", created_at=(basis + timedelta(minutes=i)).isoformat())
            for i in range(5)])
        r1 = Response()
        seite1 = await L.list_snapshots(None, w.chef, limit=2, before=None, response=r1)
        r2 = Response()
        seite2 = await L.list_snapshots(None, w.chef, limit=2, before=r1.headers.get("X-Next-Before"),
                                        response=r2)
        r3 = Response()
        seite3 = await L.list_snapshots(None, w.chef, limit=2, before=r2.headers.get("X-Next-Before"),
                                        response=r3)
        alle = await L.list_snapshots(None, w.chef)
        return r1, seite1, r2, seite2, r3, seite3, alle

    r1, s1, r2, s2, r3, s3, alle = welt.run(lauf())
    assert [s["id"] for s in s1] == [f"s4_{w.s}", f"s3_{w.s}"] and r1.headers["X-Truncated"] == "1"
    assert r1.headers["X-Next-Before"] == s1[-1]["created_at"]
    assert [s["id"] for s in s2] == [f"s2_{w.s}", f"s1_{w.s}"] and r2.headers["X-Truncated"] == "1"
    assert [s["id"] for s in s3] == [f"s0_{w.s}"] and "X-Truncated" not in r3.headers
    assert len(alle) == 5 and all("png_path" not in s for s in alle)


# ================================================= Nr. 313: Reaper fuer Alt-Snapshots
# (Snapshot-Erzeugung seit 10.09.2026 entfernt — Nr. 310/316 entfallen mit ihr;
# der Reaper bleibt, bis keine Altzeilen mehr haengen koennen.)
def test_17_reaper_misst_am_heartbeat(welt):
    CS = _module("cleanup_service")
    w, db = welt.w, welt.db

    async def lauf():
        await db.listing_snapshots.insert_many([
            w.snap(f"lebt_{w.s}", status="running", started_at=_vor(minutes=40),
                   heartbeat_at=_vor(seconds=20), created_at=_vor(minutes=45), completed_at=None),
            w.snap(f"tot_{w.s}", status="running", started_at=_vor(minutes=40),
                   heartbeat_at=_vor(minutes=20), created_at=_vor(minutes=45), completed_at=None),
            w.snap(f"alt_{w.s}", status="running", started_at=_vor(minutes=20),
                   created_at=_vor(minutes=25), completed_at=None),
            w.snap(f"frisch_{w.s}", status="running", started_at=_vor(minutes=5),
                   created_at=_vor(minutes=6), completed_at=None),
            w.snap(f"wartet_{w.s}", status="queued", created_at=_vor(minutes=30), completed_at=None),
        ])
        await CS._reap_stuck_snapshots(db)
        return {s["id"]: s["status"] async for s in db.listing_snapshots.find(
            {"id": {"$regex": f"_{w.s}$"}}, {"_id": 0, "id": 1, "status": 1})}

    stati = welt.run(lauf())
    assert stati[f"lebt_{w.s}"] == "running", "Lebenszeichen schlaegt started_at"
    assert stati[f"tot_{w.s}"] == "failed"
    assert stati[f"alt_{w.s}"] == "failed", "ohne heartbeat gilt started_at"
    assert stati[f"frisch_{w.s}"] == "running" and stati[f"wartet_{w.s}"] == "queued"


# ================================================= Nr. 307: Snapshot-Reste
def test_18_failed_und_expired_zeilen_nach_sieben_tagen_weg(welt, monkeypatch):
    CS = _module("cleanup_service")
    SN = _module("snapshot_service")
    w, db = welt.w, welt.db
    geloescht = []
    monkeypatch.setattr(SN, "delete_object", lambda p: geloescht.append(p) or True)

    async def lauf():
        await db.listing_snapshots.insert_many([
            w.snap(f"f_alt_{w.s}", status="failed", completed_at=_vor(days=10)),
            w.snap(f"f_ohne_{w.s}", status="failed", completed_at=None, created_at=_vor(days=10)),
            w.snap(f"f_pfad_{w.s}", status="failed", completed_at=_vor(days=10),
                   png_path=f"autohandel/snapshots/{w.dealer_id}/x.jpg"),
            w.snap(f"f_neu_{w.s}", status="failed", completed_at=_vor(days=2)),
            w.snap(f"e_alt_{w.s}", status="expired", completed_at=_vor(days=70)),
            w.snap(f"e_offen_{w.s}", status="expired", completed_at=_vor(days=70), loeschung_offen=True),
            w.snap(f"ready_{w.s}", status="ready", completed_at=_vor(days=10)),
        ])
        n = await CS._snapshot_reste_entfernen(db)
        rest = {s["id"] async for s in db.listing_snapshots.find(
            {"id": {"$regex": f"_{w.s}$"}}, {"_id": 0, "id": 1})}
        return n, rest

    n, rest = welt.run(lauf())
    assert n >= 4          # geteilte Test-DB: der Sweep raeumt auch Altbestand anderer Laeufe
    assert rest == {f"f_neu_{w.s}", f"e_offen_{w.s}", f"ready_{w.s}"}
    assert geloescht == [f"autohandel/snapshots/{w.dealer_id}/x.jpg"], "Datei zuerst weg, dann die Zeile"
    assert "_snapshot_reste_entfernen(db)" in inspect.getsource(CS._expire_old_snapshots)


# ================================================= Nr. 400: marktplatz_rotieren in Paketen
def test_19_marktplatz_rotieren_verarbeitet_mehr_als_tausend(welt):
    CS = _module("cleanup_service")
    w, db = welt.w, welt.db
    n = 1200

    async def lauf():
        await db.listing_interest.insert_many([
            {"id": f"li_{w.s}_{i}", "listing_id": f"weg_{w.s}_{i}", "status": "offen",
             "history": [], "created_at": _jetzt(), "updated_at": _jetzt()} for i in range(n)])
        stats = await CS.marktplatz_rotieren(db, datetime.now(timezone.utc))
        offen = await db.listing_interest.count_documents(
            {"id": {"$regex": f"^li_{w.s}_"}, "status": "offen"})
        zu = await db.listing_interest.count_documents(
            {"id": {"$regex": f"^li_{w.s}_"}, "status": "abgelehnt", "beendet_grund": "inserat_weg"})
        return stats, offen, zu

    stats, offen, zu = welt.run(lauf())
    assert offen == 0 and zu == n and stats["interessen_verwaist_geschlossen"] >= n
    assert "to_list(5000)" not in inspect.getsource(CS.marktplatz_rotieren)


# ================================================= Nr. 396: Dubletten-Skript
def test_20_dubletten_skript_meldet_fahrzeug_dubletten(welt, capsys):
    from pymongo import MongoClient
    from pymongo.errors import BulkWriteError, DuplicateKeyError
    scripts = str(Path(__file__).resolve().parents[1] / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    DP = _module("dubletten_pruefen")
    w = welt.w
    db = MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)[DB_NAME]
    vid = f"v_dup_{w.s}"
    try:
        db.vehicles.insert_many([
            {"id": vid, "dealer_id": w.dealer_id, "lifecycle": "verglichen", "created_at": "a"},
            {"id": vid, "dealer_id": w.dealer_id, "lifecycle": "bestand", "created_at": "b"}])
    except (DuplicateKeyError, BulkWriteError):
        # Unique-Index (dealer_id, id) ist schon aktiv: Dubletten unmoeglich
        assert DP.doppelte_fahrzeuge(db) == 0
        return
    n = DP.doppelte_fahrzeuge(db)
    out = capsys.readouterr().out
    assert n >= 1 and vid in out and w.dealer_id in out
    assert "doppelte_fahrzeuge(db)" in inspect.getsource(DP.main)


# ================================================= Nr. 287: Selbstheilung Bestand ohne Frist
def test_21_bestand_ohne_frist_bekommt_eine(welt):
    CS = _module("cleanup_service")
    w, db = welt.w, welt.db
    jung, alt = f"v_jung_{w.s}", f"v_alt_{w.s}"

    async def lauf():
        await db.vehicles.insert_many([
            w.fahrzeug(jung, lifecycle="bestand", lifecycle_changed_at=_vor(days=10),
                       bestand={"notes": "x"}),
            w.fahrzeug(alt, lifecycle="bestand", lifecycle_changed_at=_vor(days=60)),   # ohne bestand
        ])
        await CS._archive_expired_bestand(db, datetime.now(timezone.utc))
        return {v["id"]: v async for v in db.vehicles.find({"dealer_id": w.dealer_id}, {"_id": 0})}

    v = welt.run(lauf())
    j, a = v[jung], v[alt]
    assert j["lifecycle"] == "bestand" and j["bestand"]["notes"] == "x"
    exp = datetime.fromisoformat(j["bestand"]["expires_at"])
    assert 39 <= (exp - datetime.now(timezone.utc)).days <= 40, "lifecycle_changed_at + 50 Tage"
    assert j["bestand"]["frist_nachgetragen_at"]
    assert a["lifecycle"] == "archiviert", "nachgetragene Frist ist abgelaufen -> gleich archiviert"
