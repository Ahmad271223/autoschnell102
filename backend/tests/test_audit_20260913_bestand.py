# -*- coding: utf-8 -*-
"""Audit 13.09.2026, Bereich "bestand" — routes/bestand.py und die
50-Tage-Archivierung in cleanup_service.py.

   7  apply-deviations: Write mit Lifecycle-CAS, nur geaenderte data-Felder
   8  PUT /vehicles/manual/{id}: Write mit Lifecycle-CAS
   9  PUT /vehicles/{id}/besitzer: CAS auf den gelesenen Besitzer
  10  Akte: kaufvorgaenge_gesamt (die Liste endet bei 50)
  48  Archivierung: Nebenaufraeumen mit Nachhol-Marker, wirft nicht
  49  Archivierung: Statuswechsel als CAS VOR dem Aufraeumen
  50  Archivierung: nur Fotofelder schreiben, nicht das ganze data-Objekt
  53  Akte: history_gekuerzt (die Historie endet bei 100)
  54  Akte: zuweisbar_an ohne 1000er-Grenze

In-Prozess gegen eine Wegwerf-DB; kein Server.
"""
import asyncio
import importlib
import sys
import types
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException

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
    namen = ["deps", "routes.bestand", "routes.contracts", "kaufvorgang", "lifecycle"]
    mods = [_modul(n) for n in namen]
    alt = [(m, getattr(m, "db", None)) for m in mods]

    w = types.SimpleNamespace()
    w.s = s
    w.dealer_id = f"d_a0913_{s}"
    w.chef = {"id": f"chef_a0913_{s}", "dealer_id": w.dealer_id, "role": "dealer"}
    w.sucher = {"id": f"su_a0913_{s}", "dealer_id": w.dealer_id, "role": "sucher"}
    w.kollege = {"id": f"ko_a0913_{s}", "dealer_id": w.dealer_id, "role": "sucher"}
    w.loop = asyncio.new_event_loop()
    asyncio.set_event_loop(w.loop)
    w.client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    w.db_name = f"autoschnell_wt_bestand_{s}"
    w.db = w.client[w.db_name]
    for m in mods:
        if hasattr(m, "db"):
            m.db = w.db
    w.run = lambda coro: w.loop.run_until_complete(coro)
    w.run(w.db.users.insert_many([
        {**w.chef, "active": True, "first_name": "Chef", "last_name": "A",
         "email": f"chef{s}@t.invalid", "created_at": "2026-01-01T00:00:00+00:00"},
        {**w.sucher, "active": True, "first_name": "Sam", "last_name": "Sucher",
         "email": f"su{s}@t.invalid", "created_at": "2026-01-02T00:00:00+00:00"},
        {**w.kollege, "active": True, "first_name": "Kai", "last_name": "Kollege",
         "email": f"ko{s}@t.invalid", "created_at": "2026-01-03T00:00:00+00:00"}]))
    yield w
    try:
        w.run(w.client.drop_database(w.db_name))
    finally:
        # Direkt zuruecksetzen (nicht per monkeypatch): Tests biegen routes.bestand.db
        # auf einen Proxy um, danach muss das Original wieder stehen.
        for m, d in alt:
            if d is not None:
                m.db = d
        w.client.close()
        w.loop.close()


class _VehEingriff:
    """Duenner Proxy fuer db.vehicles: Ein paralleler Schreiber (Chef im
    zweiten Tab, Stundenjob) schlaegt genau einmal zu — nach dem echten
    find_one bzw. bevor ein Cursor das Zielfahrzeug ausliefert. Alles andere
    geht an die echte Collection."""

    def __init__(self, echt, ziel_id, eingriff):
        self._echt, self._ziel, self._eingriff = echt, ziel_id, eingriff
        self.gefeuert = 0

    async def _feuern(self, doc, vid=None):
        if doc and (vid or doc.get("id")) == self._ziel and not self.gefeuert:
            self.gefeuert += 1
            await self._eingriff(self._echt)

    async def find_one(self, *a, **k):
        doc = await self._echt.find_one(*a, **k)
        # Die Projektion enthaelt nicht immer "id" — deshalb ueber den Filter.
        filt = a[0] if a else k.get("filter") or {}
        await self._feuern(doc, vid=filt.get("id"))
        return doc

    def find(self, *a, **k):
        cur = self._echt.find(*a, **k)

        async def _gen():
            async for d in cur:
                await self._feuern(d)
                yield d
        return _gen()

    def __getattr__(self, name):
        return getattr(self._echt, name)


class _DbEingriff:
    def __init__(self, echt, vehicles):
        self._echt = echt
        self.vehicles = vehicles

    def __getattr__(self, name):
        return getattr(self._echt, name)


def _bericht_attrappe(monkeypatch, eingriff):
    """abholbericht.massgeblicher_bericht: fuehrt waehrend des Berichtladens
    einen parallelen Schreiber aus und liefert eine km-Abweichung."""
    mod = types.ModuleType("abholbericht")

    async def massgeblicher_bericht(db, vehicle_id, dealer_id, nur_termine=None):
        if eingriff:
            await eingriff(db, vehicle_id, dealer_id)
        return {"id": "r1", "appointment_id": "t1", "mileage_at_pickup": 999999,
                "deviations": [{"id": "d1", "field": "mileage", "label": "km"}]}
    mod.massgeblicher_bericht = massgeblicher_bericht
    monkeypatch.setitem(sys.modules, "abholbericht", mod)


async def _logs(w, ref, action):
    return await w.db.activity_logs.count_documents({"ref": ref, "action": action})


# ================================================= Nr. 7
def test_07_abweichungen_nach_zwischenzeitlichem_loeschen_409(welt, monkeypatch):
    B = _modul("routes.bestand")
    vid = f"v7a_{welt.s}"
    welt.run(welt.db.vehicles.insert_one(
        {"id": vid, "dealer_id": welt.dealer_id, "lifecycle": "abgeholt",
         "data": {"mileage": 100, "images": ["a.jpg"]}}))

    async def loeschen(db, v, d):
        await db.vehicles.update_one({"id": v, "dealer_id": d},
                                     {"$set": {"lifecycle": "geloescht", "data.images": []}})
    _bericht_attrappe(monkeypatch, loeschen)
    with pytest.raises(HTTPException) as exc:
        welt.run(B.apply_deviations(vid, B.ApplyDeviationsIn(deviation_ids=["d1"]),
                                    user=welt.chef))
    assert exc.value.status_code == 409
    v = welt.run(welt.db.vehicles.find_one({"id": vid}, {"_id": 0}))
    assert v["data"] == {"mileage": 100, "images": []}, "geloeschte Akte veraendert"
    assert welt.run(_logs(welt, vid, "fahrzeug.abweichungen.uebernommen")) == 0


def test_07_abweichungen_ohne_lost_update_der_fotofelder(welt, monkeypatch):
    B = _modul("routes.bestand")
    vid = f"v7b_{welt.s}"
    welt.run(welt.db.vehicles.insert_one(
        {"id": vid, "dealer_id": welt.dealer_id, "lifecycle": "abgeholt",
         "data": {"mileage": 100, "images": ["a.jpg"]}}))

    async def fotos_raeumen(db, v, d):
        await db.vehicles.update_one({"id": v, "dealer_id": d}, {"$set": {"data.images": []}})
    _bericht_attrappe(monkeypatch, fotos_raeumen)
    out = welt.run(B.apply_deviations(vid, B.ApplyDeviationsIn(deviation_ids=["d1"]),
                                      user=welt.chef))
    assert out["ok"] is True
    v = welt.run(welt.db.vehicles.find_one({"id": vid}, {"_id": 0}))
    assert v["data"]["mileage"] == 999999
    assert v["data"]["images"] == [], "Fotofelder aus dem Lesestand zurueckgeschrieben"
    assert welt.run(_logs(welt, vid, "fahrzeug.abweichungen.uebernommen")) == 1


def test_07_abweichungen_bei_data_null(welt, monkeypatch):
    B = _modul("routes.bestand")
    vid = f"v7c_{welt.s}"
    welt.run(welt.db.vehicles.insert_one(
        {"id": vid, "dealer_id": welt.dealer_id, "lifecycle": "abgeholt", "data": None}))
    _bericht_attrappe(monkeypatch, None)
    welt.run(B.apply_deviations(vid, B.ApplyDeviationsIn(deviation_ids=["d1"]), user=welt.chef))
    v = welt.run(welt.db.vehicles.find_one({"id": vid}, {"_id": 0}))
    assert v["data"] == {"mileage": 999999}


# ================================================= Nr. 8
def test_08_manuelles_fahrzeug_nach_zwischenzeitlicher_archivierung_409(welt):
    B = _modul("routes.bestand")
    vid = f"m8_{welt.s}"
    welt.run(welt.db.vehicles.insert_one(
        {"id": vid, "dealer_id": welt.dealer_id, "source": "manuell",
         "owner_user_id": welt.chef["id"], "lifecycle": "bestand",
         "purchase_price": 20000, "data": {"make_label": "VW", "model_label": "Golf"}}))

    async def archivieren(vehicles):
        await vehicles.update_one({"id": vid, "dealer_id": welt.dealer_id},
                                  {"$set": {"lifecycle": "archiviert"}})
    B.db = _DbEingriff(welt.db, _VehEingriff(welt.db.vehicles, vid, archivieren))
    body = B.ManualVehicleIn(make_label="VW", model_label="Polo", mileage=5,
                             purchase_price=15000)
    with pytest.raises(HTTPException) as exc:
        welt.run(B.update_manual_vehicle(vid, body, user=welt.chef))
    assert exc.value.status_code == 409
    v = welt.run(welt.db.vehicles.find_one({"id": vid}, {"_id": 0}))
    assert v["purchase_price"] == 20000 and v["data"]["model_label"] == "Golf"
    assert welt.run(_logs(welt, vid, "fahrzeug.manuell.geaendert")) == 0

    # Ohne Rennen weiter wie bisher
    B.db = welt.db
    welt.run(welt.db.vehicles.update_one({"id": vid}, {"$set": {"lifecycle": "bestand"}}))
    assert welt.run(B.update_manual_vehicle(vid, body, user=welt.chef)) == {"ok": True}
    v = welt.run(welt.db.vehicles.find_one({"id": vid}, {"_id": 0}))
    assert v["purchase_price"] == 15000 and v["data"]["model_label"] == "Polo"
    assert welt.run(_logs(welt, vid, "fahrzeug.manuell.geaendert")) == 1


# ================================================= Nr. 9
def test_09_umhaengen_mit_cas_auf_den_gelesenen_besitzer(welt):
    B = _modul("routes.bestand")
    vid = f"v9_{welt.s}"
    welt.run(welt.db.vehicles.insert_one(
        {"id": vid, "dealer_id": welt.dealer_id, "lifecycle": "bestand",
         "owner_user_id": welt.chef["id"]}))

    async def anderer_tab(vehicles):
        await vehicles.update_one({"id": vid, "dealer_id": welt.dealer_id},
                                  {"$set": {"owner_user_id": welt.kollege["id"]}})
    B.db = _DbEingriff(welt.db, _VehEingriff(welt.db.vehicles, vid, anderer_tab))
    with pytest.raises(HTTPException) as exc:
        welt.run(B.set_vehicle_owner(vid, B.BesitzerIn(owner_user_id=welt.sucher["id"]),
                                     welt.chef))
    assert exc.value.status_code == 409
    v = welt.run(welt.db.vehicles.find_one({"id": vid}, {"_id": 0}))
    assert v["owner_user_id"] == welt.kollege["id"]
    assert welt.run(_logs(welt, vid, "fahrzeug.zugewiesen")) == 0

    # Fahrzeug ganz ohne owner_user_id (Altbestand): None trifft das fehlende Feld
    B.db = welt.db
    vid2 = f"v9b_{welt.s}"
    welt.run(welt.db.vehicles.insert_one(
        {"id": vid2, "dealer_id": welt.dealer_id, "lifecycle": "bestand"}))
    r = welt.run(B.set_vehicle_owner(vid2, B.BesitzerIn(owner_user_id=welt.sucher["id"]),
                                     welt.chef))
    assert r["ok"] is True and r["owner_user_id"] == welt.sucher["id"]
    v2 = welt.run(welt.db.vehicles.find_one({"id": vid2}, {"_id": 0}))
    assert v2["owner_user_id"] == welt.sucher["id"]


# ================================================= Nr. 10
def test_10_akte_meldet_gesamtzahl_der_kaufvorgaenge(welt, monkeypatch):
    B = _modul("routes.bestand")
    _bericht_attrappe(monkeypatch, None)
    vid = f"v10_{welt.s}"
    welt.run(welt.db.vehicles.insert_one(
        {"id": vid, "dealer_id": welt.dealer_id, "lifecycle": "bestand",
         "owner_user_id": welt.sucher["id"], "created_at": _jetzt(), "data": {}}))
    basis = datetime(2026, 1, 1, tzinfo=timezone.utc)
    docs = [{"id": f"k{i}_{welt.s}", "dealer_id": welt.dealer_id, "vehicle_id": vid,
             "contract_id": f"c{i}_{welt.s}", "status": "storniert",
             "user_id": (welt.sucher if i < 2 else welt.kollege)["id"],
             "created_at": (basis + timedelta(minutes=i)).isoformat()} for i in range(51)]
    welt.run(welt.db.kaufvorgaenge.insert_many(docs))
    akte = welt.run(B.vehicle_akte(vid, user=welt.chef))
    assert len(akte["kaufvorgaenge"]) == 50
    assert akte["kaufvorgaenge_gesamt"] == 51
    akte_s = welt.run(B.vehicle_akte(vid, user=welt.sucher))
    assert len(akte_s["kaufvorgaenge"]) == 2 and akte_s["kaufvorgaenge_gesamt"] == 2


# ================================================= Nr. 53
def test_53_akte_meldet_gekuerzte_historie(welt, monkeypatch):
    B = _modul("routes.bestand")
    _bericht_attrappe(monkeypatch, None)
    vid, vid100 = f"v53_{welt.s}", f"v53h_{welt.s}"
    for x in (vid, vid100):
        welt.run(welt.db.vehicles.insert_one(
            {"id": x, "dealer_id": welt.dealer_id, "lifecycle": "bestand",
             "owner_user_id": welt.sucher["id"], "created_at": _jetzt(), "data": {}}))
    basis = datetime(2026, 9, 1, tzinfo=timezone.utc)

    def _log(ref, i, user_id):
        return {"id": uuid.uuid4().hex, "dealer_id": welt.dealer_id, "user_id": user_id,
                "action": "bestand.geaendert", "ref": ref, "meta": {},
                "created_at": (basis + timedelta(minutes=i)).isoformat()}
    welt.run(welt.db.activity_logs.insert_many(
        [_log(vid, i, welt.kollege["id"]) for i in range(101)]
        + [_log(vid, 500 + i, welt.sucher["id"]) for i in range(3)]
        + [_log(vid100, i, welt.kollege["id"]) for i in range(100)]))
    akte = welt.run(B.vehicle_akte(vid, user=welt.chef))
    assert len(akte["history"]) == 100
    assert akte["history_gekuerzt"] is True
    # Genau 100 Eintraege: nichts abgeschnitten
    akte100 = welt.run(B.vehicle_akte(vid100, user=welt.chef))
    assert len(akte100["history"]) == 100 and akte100["history_gekuerzt"] is False
    # Sucher: Eintraege des Kollegen zaehlen nicht mit
    akte_s = welt.run(B.vehicle_akte(vid, user=welt.sucher))
    assert len(akte_s["history"]) == 3 and akte_s["history_gekuerzt"] is False


def _plan_indizes(plan):
    """Alle Indexnamen aus einem explain()-Gewinnerplan (rekursiv)."""
    namen = set()
    if isinstance(plan, dict):
        if plan.get("indexName"):
            namen.add(plan["indexName"])
        for v in plan.values():
            namen |= _plan_indizes(v)
    elif isinstance(plan, list):
        for v in plan:
            namen |= _plan_indizes(v)
    return namen


def test_53_nachbesserung_akte_historie_hat_index(welt):
    """Nachbesserung #53/#48: server._bestand_lese_indizes legt die Lese-Indizes
    an (idempotent, ohne Alarm), und die Aktenabfrage nutzt den neuen Index
    statt den created_at-Index ueber alle Firmen."""
    import server
    alt = server.db
    server.db = welt.db
    try:
        for _ in range(2):
            welt.run(server._bestand_lese_indizes())
    finally:
        server.db = alt
    assert "akte_historie" in welt.run(welt.db.activity_logs.index_information())
    v_idx = welt.run(welt.db.vehicles.index_information())
    assert v_idx["archiv_aufraeumen_offen"]["partialFilterExpression"] == \
        {"archiv_aufraeumen_offen": True}
    assert welt.run(welt.db.betriebsalarme.count_documents({"typ": "index_fehlt"})) == 0
    # Wie in ensure_indexes: der alte created_at-Index steht daneben
    welt.run(welt.db.activity_logs.create_index([("created_at", -1)]))

    vid, tid = f"v53i_{welt.s}", f"t53i_{welt.s}"
    basis = datetime(2026, 9, 1, tzinfo=timezone.utc)
    welt.run(welt.db.activity_logs.insert_many(
        [{"dealer_id": f"fremd_{i % 7}", "ref": f"x{i}", "action": "a",
          "created_at": (basis + timedelta(minutes=i)).isoformat()} for i in range(2000)]
        + [{"dealer_id": welt.dealer_id, "ref": vid, "user_id": welt.sucher["id"],
            "action": "a", "created_at": basis.isoformat()}]))
    filter_chef = {"ref": {"$in": [vid, tid]}, "dealer_id": welt.dealer_id}
    filter_sucher = {"dealer_id": welt.dealer_id, "$or": [
        {"ref": vid, "user_id": welt.sucher["id"]}, {"ref": {"$in": [tid]}}]}
    for f in (filter_chef, filter_sucher):
        exp = welt.run(welt.db.activity_logs.find(f).sort("created_at", -1).limit(101).explain())
        gewinner = exp["queryPlanner"]["winningPlan"]
        assert _plan_indizes(gewinner) == {"akte_historie"}, gewinner

    # Nachhol-Abfrage der Archivierung (cleanup_service) nutzt den partiellen Index
    exp = welt.run(welt.db.vehicles.find(
        {"lifecycle": "archiviert", "archiv_aufraeumen_offen": True}).explain())
    assert "archiv_aufraeumen_offen" in _plan_indizes(exp["queryPlanner"]["winningPlan"])


# ================================================= Nr. 54
def test_54_zuweisbar_an_ohne_1000er_grenze(welt, monkeypatch):
    B = _modul("routes.bestand")
    _bericht_attrappe(monkeypatch, None)
    vid = f"v54_{welt.s}"
    welt.run(welt.db.vehicles.insert_one(
        {"id": vid, "dealer_id": welt.dealer_id, "lifecycle": "bestand",
         "owner_user_id": welt.chef["id"], "created_at": _jetzt(), "data": {}}))
    welt.run(welt.db.users.insert_many([
        {"id": f"viele{i}_{welt.s}", "dealer_id": welt.dealer_id, "role": "sucher",
         "active": True, "email": f"v{i}{welt.s}@t.invalid",
         "created_at": f"2026-02-01T00:00:{i % 60:02d}+00:00"} for i in range(1001)]))
    akte = welt.run(B.vehicle_akte(vid, user=welt.chef))
    assert len(akte["zuweisbar_an"]) == 1001 + 3


# ================================================= Nr. 48 / 49 / 50
def _abgelaufen(w, vid, **extra):
    doc = {"id": vid, "dealer_id": w.dealer_id, "lifecycle": "bestand",
           "owner_user_id": w.chef["id"],
           "bestand": {"expires_at": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
                       "notes": "Platz 4"},
           "data": {"mileage": 100, "images": ["a.jpg"]}}
    doc.update(extra)
    return doc


def _entwurf(w, vid, lid):
    return {"id": lid, "dealer_id": w.dealer_id, "vehicle_id": vid, "status": "entwurf",
            "photos": {"uploaded_keys": []}, "created_at": _jetzt()}


def test_48_inseratsfehler_wird_beim_naechsten_lauf_nachgeholt(welt, monkeypatch):
    CS = _modul("cleanup_service")
    vid, lid = f"v48_{welt.s}", f"l48_{welt.s}"
    welt.run(welt.db.vehicles.insert_one(_abgelaufen(welt, vid)))
    welt.run(welt.db.resale_listings.insert_one(_entwurf(welt, vid, lid)))
    echt = CS._inserat_mit_fotos_loeschen
    aufrufe = {"n": 0}

    async def einmal_kaputt(*a, **k):
        aufrufe["n"] += 1
        if aufrufe["n"] == 1:
            raise RuntimeError("Primarywechsel")
        return await echt(*a, **k)
    monkeypatch.setattr(CS, "_inserat_mit_fotos_loeschen", einmal_kaputt)

    n1 = welt.run(CS._archive_expired_bestand(welt.db, datetime.now(timezone.utc)))
    v1 = welt.run(welt.db.vehicles.find_one({"id": vid}, {"_id": 0}))
    assert n1 == 1 and v1["lifecycle"] == "archiviert"
    assert v1.get("archiv_aufraeumen_offen") is True, "kein Nachhol-Marker"
    assert welt.run(welt.db.resale_listings.find_one({"id": lid})) is not None

    n2 = welt.run(CS._archive_expired_bestand(welt.db, datetime.now(timezone.utc)))
    v2 = welt.run(welt.db.vehicles.find_one({"id": vid}, {"_id": 0}))
    assert n2 == 0
    assert welt.run(welt.db.resale_listings.find_one({"id": lid})) is None, \
        "Entwurf zum archivierten Fahrzeug bleibt fuer immer liegen"
    assert "archiv_aufraeumen_offen" not in v2
    assert welt.run(_logs(welt, vid, "fahrzeug.archiviert.50tage")) == 1


def test_48_snapshotfehler_bricht_den_lauf_nicht_ab(welt, monkeypatch):
    CS = _modul("cleanup_service")
    vid = f"v48b_{welt.s}"
    welt.run(welt.db.vehicles.insert_one(_abgelaufen(welt, vid)))

    async def kaputt(*a, **k):
        raise RuntimeError("Mongo weg")
    monkeypatch.setattr(CS, "_delete_snapshots_for_vehicle", kaputt)
    n = welt.run(CS._archive_expired_bestand(welt.db, datetime.now(timezone.utc)))
    v = welt.run(welt.db.vehicles.find_one({"id": vid}, {"_id": 0}))
    assert n == 1 and v["lifecycle"] == "archiviert"
    assert v.get("archiv_aufraeumen_offen") is True

    # Nachbesserung: Lauf 2 — Fehler bleibt, Nachholung scheitert: offener Alarm
    ref = f"{welt.dealer_id}/{vid}"
    alarm_filter = {"typ": "bestand_archiv_aufraeumen_offen", "ref": ref}
    welt.run(CS._archive_expired_bestand(welt.db, datetime.now(timezone.utc)))
    alarme = welt.run(welt.db.betriebsalarme.find(alarm_filter, {"_id": 0}).to_list(10))
    assert len(alarme) == 1 and alarme[0]["offen"] is True and alarme[0]["anzahl"] == 1, alarme

    # Lauf 3 — Fehler behoben: Marker weg, derselbe Alarm (typ/ref) geschlossen
    monkeypatch.undo()
    welt.run(CS._archive_expired_bestand(welt.db, datetime.now(timezone.utc)))
    v3 = welt.run(welt.db.vehicles.find_one({"id": vid}, {"_id": 0}))
    assert "archiv_aufraeumen_offen" not in v3
    alarme = welt.run(welt.db.betriebsalarme.find(alarm_filter, {"_id": 0}).to_list(10))
    assert len(alarme) == 1 and alarme[0]["offen"] is False, "Alarm bleibt fuer immer offen"


def test_49_chef_entscheidung_waehrend_des_laufs_gewinnt(welt):
    CS = _modul("cleanup_service")
    vid, lid = f"v49_{welt.s}", f"l49_{welt.s}"
    welt.run(welt.db.vehicles.insert_one(_abgelaufen(welt, vid)))

    async def weiterverkaufen(vehicles):
        # wie resale.create_draft: Entwurf anlegen, dann bestand -> verkaufsentwurf
        await welt.db.resale_listings.insert_one(_entwurf(welt, vid, lid))
        await vehicles.update_one({"id": vid, "dealer_id": welt.dealer_id, "lifecycle": "bestand"},
                                  {"$set": {"lifecycle": "verkaufsentwurf"}})
    proxy = _DbEingriff(welt.db, _VehEingriff(welt.db.vehicles, vid, weiterverkaufen))
    n = welt.run(CS._archive_expired_bestand(proxy, datetime.now(timezone.utc)))
    v = welt.run(welt.db.vehicles.find_one({"id": vid}, {"_id": 0}))
    assert n == 0 and v["lifecycle"] == "verkaufsentwurf", "Chef-Entscheidung ueberschrieben"
    assert v["data"]["images"] == ["a.jpg"]
    assert welt.run(welt.db.resale_listings.find_one({"id": lid})) is not None, \
        "Entwurf des Chefs geloescht"
    assert welt.run(_logs(welt, vid, "fahrzeug.archiviert.50tage")) == 0


def test_49_frist_waehrend_des_laufs_verlaengert(welt):
    CS = _modul("cleanup_service")
    vid = f"v49b_{welt.s}"
    welt.run(welt.db.vehicles.insert_one(_abgelaufen(welt, vid)))

    async def verlaengern(vehicles):
        neu = (datetime.now(timezone.utc) + timedelta(days=50)).isoformat()
        await vehicles.update_one({"id": vid, "dealer_id": welt.dealer_id},
                                  {"$set": {"bestand.expires_at": neu}})
    proxy = _DbEingriff(welt.db, _VehEingriff(welt.db.vehicles, vid, verlaengern))
    n = welt.run(CS._archive_expired_bestand(proxy, datetime.now(timezone.utc)))
    v = welt.run(welt.db.vehicles.find_one({"id": vid}, {"_id": 0}))
    assert n == 0 and v["lifecycle"] == "bestand" and v["data"]["images"] == ["a.jpg"]


def test_50_archivierung_schreibt_nur_die_fotofelder(welt):
    CS = _modul("cleanup_service")
    vid, vnull = f"v50_{welt.s}", f"v50n_{welt.s}"
    welt.run(welt.db.vehicles.insert_many([
        _abgelaufen(welt, vid), _abgelaufen(welt, vnull, data=None)]))

    async def km_korrigieren(vehicles):
        await vehicles.update_one({"id": vid, "dealer_id": welt.dealer_id},
                                  {"$set": {"data.mileage": 155000}})
    proxy = _DbEingriff(welt.db, _VehEingriff(welt.db.vehicles, vid, km_korrigieren))
    n = welt.run(CS._archive_expired_bestand(proxy, datetime.now(timezone.utc)))
    v = welt.run(welt.db.vehicles.find_one({"id": vid}, {"_id": 0}))
    vn = welt.run(welt.db.vehicles.find_one({"id": vnull}, {"_id": 0}))
    assert n == 2
    assert v["lifecycle"] == "archiviert" and v["data"]["images"] == []
    assert v["data"]["mileage"] == 155000, "Kilometer-Korrektur still verloren"
    assert v["bestand"]["notes"] == "Platz 4"
    assert vn["lifecycle"] == "archiviert" and vn["data"] is None
