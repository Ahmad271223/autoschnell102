# -*- coding: utf-8 -*-
"""Runde 23 (11.09.2026): Fahrzeugpool loescht keine gemeinsamen Fahrzeuge.

Befund: Die Begrenzung auf 30 Vergleiche zaehlt je Hauptbearbeiter
(owner_user_id), loeschte beim Aufraeumen aber das ganze gemeinsame
Fahrzeug — hatte Sucher B denselben Link verglichen (mitbearbeiter_ids),
verschwand es auch fuer B. Jetzt wird es an den ersten aktiven
Mitbearbeiter uebergeben; geloescht wird nur ohne (aktiven) Mitbearbeiter.

In-Prozess: fahrzeugpool_trimmen bekommt die Datenbank als Parameter —
eigene Wegwerf-Datenbank je Test, danach gedroppt (nur Mongo noetig).
"""
import asyncio
import os
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"


def _zeit(i: int) -> str:
    """Aufsteigende Zeitstempel: kleines i = aelterer Vergleich."""
    return f"2026-01-01T00:{i // 60:02d}:{i % 60:02d}+00:00"


class _Ctx:
    def __init__(self):
        from motor.motor_asyncio import AsyncIOMotorClient
        self.s = uuid.uuid4().hex[:10]
        self.db_name = f"autoschnell_r23_fahrzeugpool_{self.s}"
        self.dealer_id = f"d_r23p_{self.s}"
        self.a = f"sa_r23p_{self.s}"
        self.b = f"sb_r23p_{self.s}"
        self.c = f"sc_r23p_{self.s}"          # deaktiviert
        self.chef = f"chef_r23p_{self.s}"
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
        self.db = self.client[self.db_name]

    def run(self, coro):
        return self.loop.run_until_complete(coro)

    def fahrzeug(self, vid, owner, i, **extra):
        doc = {"id": vid, "dealer_id": self.dealer_id, "lifecycle": "verglichen",
               "status": "verglichen", "owner_user_id": owner,
               "data": {"make_label": "BMW"}, "created_at": _zeit(i), "updated_at": _zeit(i)}
        doc.update(extra)
        return doc


@pytest.fixture
def welt():
    ctx = _Ctx()
    ctx.run(ctx.db.users.insert_many([
        {"id": ctx.chef, "dealer_id": ctx.dealer_id, "role": "dealer", "active": True},
        {"id": ctx.a, "dealer_id": ctx.dealer_id, "role": "sucher", "active": True},
        {"id": ctx.b, "dealer_id": ctx.dealer_id, "role": "sucher", "active": True},
        {"id": ctx.c, "dealer_id": ctx.dealer_id, "role": "sucher", "active": False},
    ]))
    yield ctx
    try:
        ctx.run(ctx.client.drop_database(ctx.db_name))
    finally:
        ctx.client.close()
        ctx.loop.close()


def _pool():
    import importlib
    return importlib.import_module("fahrzeugpool")


class _DbMitEinschub:
    """Datenbank-Stellvertreter: fuehrt beim ersten Zugriff auf eine
    Schutz-Collection (also NACH dem Lesen der Kandidaten, VOR dem Loeschen)
    einen synchronen Schreibvorgang aus — simuliert einen parallelen
    Vergleich/Besitzerwechsel auf einem anderen Worker."""

    def __init__(self, db, einschub):
        self._db = db
        self._einschub = einschub
        self._erledigt = False

    def __getattr__(self, name):
        return getattr(self._db, name)

    def __getitem__(self, name):
        if name == "generated_pdfs" and not self._erledigt:
            self._erledigt = True
            self._einschub()
        return self._db[name]


def _sync_vehicles(w):
    from pymongo import MongoClient
    return MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)[w.db_name].vehicles


# ================================================= Kernbefund
def test_01_fahrzeug_mit_mitbearbeiter_geht_an_ihn_statt_geloescht(welt):
    F, w = _pool(), welt
    geteilt = f"v_geteilt_{w.s}"

    async def lauf():
        docs = [w.fahrzeug(geteilt, w.a, 0, mitbearbeiter_ids=[w.b])]      # aeltestes, B hat mitverglichen
        docs.append(w.fahrzeug(f"v_allein_{w.s}", w.a, 1))                  # zweitaeltestes, nur A
        docs += [w.fahrzeug(f"v_{i}_{w.s}", w.a, i) for i in range(2, 32)]  # 30 neuere
        await w.db.vehicles.insert_many(docs)
        vorher = await w.db.vehicles.find_one({"id": geteilt}, {"_id": 0, "updated_at": 1})
        n = await F.fahrzeugpool_trimmen(w.db, w.dealer_id, limit=30, owner_user_id=w.a)
        v = await w.db.vehicles.find_one({"id": geteilt}, {"_id": 0})
        allein = await w.db.vehicles.find_one({"id": f"v_allein_{w.s}"})
        pool_a = await w.db.vehicles.count_documents(
            {"dealer_id": w.dealer_id, "lifecycle": "verglichen", "owner_user_id": w.a})
        nochmal = await F.fahrzeugpool_trimmen(w.db, w.dealer_id, limit=30, owner_user_id=w.a)
        # zaehlt jetzt gegen B: B vergleicht ein neueres Auto, B-Limit 1 -> das alte faellt bei B
        await w.db.vehicles.insert_one(w.fahrzeug(f"v_b_neu_{w.s}", w.b, 99))
        n_b = await F.fahrzeugpool_trimmen(w.db, w.dealer_id, limit=1, owner_user_id=w.b)
        weg_bei_b = await w.db.vehicles.find_one({"id": geteilt})
        return vorher, n, v, allein, pool_a, nochmal, n_b, weg_bei_b

    vorher, n, v, allein, pool_a, nochmal, n_b, weg_bei_b = welt.run(lauf())
    assert n == 1, "nur das Fahrzeug ohne Mitbearbeiter wird geloescht"
    assert v is not None, "gemeinsames Fahrzeug darf fuer B nicht verschwinden"
    assert v["owner_user_id"] == w.b and v["mitbearbeiter_ids"] == []
    assert v["lifecycle"] == "verglichen" and v["updated_at"] == vorher["updated_at"], \
        "updated_at bleibt unveraendert"
    assert allein is None, "Fahrzeug ohne Mitbearbeiter wird wie bisher entfernt"
    assert pool_a == 30 and nochmal == 0
    assert n_b == 1 and weg_bei_b is None, "das uebergebene Fahrzeug zaehlt jetzt gegen B"


def test_02_schutzbedingungen_bleiben(welt):
    """Vertrag, Termin, Weiterverkaufs-Inserat und Lebenszyklus schuetzen wie
    bisher — auch ein geschuetztes gemeinsames Fahrzeug bleibt beim
    Hauptbearbeiter (keine Uebergabe). Fremde Firma schuetzt nicht."""
    F, w = _pool(), welt

    async def lauf():
        await w.db.vehicles.insert_many([
            w.fahrzeug(f"v_vertrag_{w.s}", w.a, 0),
            w.fahrzeug(f"v_termin_{w.s}", w.a, 1),
            w.fahrzeug(f"v_inserat_{w.s}", w.a, 2),
            w.fahrzeug(f"v_vertrag_mb_{w.s}", w.a, 3, mitbearbeiter_ids=[w.b]),
            w.fahrzeug(f"v_bestand_{w.s}", w.a, 4, lifecycle="bestand", mitbearbeiter_ids=[w.b]),
            w.fahrzeug(f"v_fremdvertrag_{w.s}", w.a, 5),
            w.fahrzeug(f"v_neu1_{w.s}", w.a, 10),
            w.fahrzeug(f"v_neu2_{w.s}", w.a, 11),
        ])
        await w.db.generated_pdfs.insert_many([
            {"id": f"c1_{w.s}", "dealer_id": w.dealer_id, "vehicle_id": f"v_vertrag_{w.s}", "user_id": w.a},
            {"id": f"c2_{w.s}", "dealer_id": w.dealer_id, "vehicle_id": f"v_vertrag_mb_{w.s}", "user_id": w.b},
            {"id": f"c3_{w.s}", "dealer_id": "andere_firma", "vehicle_id": f"v_fremdvertrag_{w.s}",
             "user_id": "x"},
        ])
        await w.db.appointments.insert_one({"id": f"t_{w.s}", "dealer_id": w.dealer_id,
                                            "vehicle_id": f"v_termin_{w.s}"})
        await w.db.resale_listings.insert_one({"id": f"l_{w.s}", "dealer_id": w.dealer_id,
                                               "vehicle_id": f"v_inserat_{w.s}"})
        n = await F.fahrzeugpool_trimmen(w.db, w.dealer_id, limit=2, owner_user_id=w.a)
        rest = {v["id"]: v async for v in w.db.vehicles.find({"dealer_id": w.dealer_id}, {"_id": 0})}
        return n, rest

    n, rest = welt.run(lauf())
    assert n == 1 and f"v_fremdvertrag_{w.s}" not in rest, "Vertrag einer fremden Firma schuetzt nicht"
    for k in ("vertrag", "termin", "inserat", "vertrag_mb", "bestand", "neu1", "neu2"):
        assert f"v_{k}_{w.s}" in rest, k
    mb = rest[f"v_vertrag_mb_{w.s}"]
    assert mb["owner_user_id"] == w.a and mb["mitbearbeiter_ids"] == [w.b], "geschuetzt: keine Uebergabe"
    assert rest[f"v_bestand_{w.s}"]["owner_user_id"] == w.a, "Bestand wird nicht angefasst"


def test_03_nur_aktive_mitbearbeiter_uebernehmen(welt):
    F, w = _pool(), welt

    async def lauf():
        await w.db.vehicles.insert_many([
            w.fahrzeug(f"v_cb_{w.s}", w.a, 0, mitbearbeiter_ids=[w.c, w.b]),      # C deaktiviert -> B
            w.fahrzeug(f"v_nur_c_{w.s}", w.a, 1, mitbearbeiter_ids=[w.c]),        # nur deaktiviert -> weg
            w.fahrzeug(f"v_leer_{w.s}", w.a, 2, mitbearbeiter_ids=[]),            # leere Liste -> weg
            w.fahrzeug(f"v_fremd_{w.s}", w.a, 3, mitbearbeiter_ids=["konto_fremde_firma"]),  # -> weg
            w.fahrzeug(f"v_neu_{w.s}", w.a, 10),
        ])
        await w.db.users.insert_one({"id": "konto_fremde_firma", "dealer_id": "andere_firma",
                                     "role": "sucher", "active": True})
        n = await F.fahrzeugpool_trimmen(w.db, w.dealer_id, limit=1, owner_user_id=w.a)
        rest = {v["id"]: v async for v in w.db.vehicles.find({"dealer_id": w.dealer_id}, {"_id": 0})}
        return n, rest

    n, rest = welt.run(lauf())
    assert n == 3
    assert set(rest) == {f"v_cb_{w.s}", f"v_neu_{w.s}"}
    v = rest[f"v_cb_{w.s}"]
    assert v["owner_user_id"] == w.b and v["mitbearbeiter_ids"] == [w.c], \
        "erster AKTIVER Mitbearbeiter uebernimmt, der deaktivierte bleibt eingetragen"


# ================================================= Nebenlaeufigkeit
def test_04_zwei_gleichzeitige_aufraeumlaeufe(welt):
    F, w = _pool(), welt

    async def lauf():
        docs = [w.fahrzeug(f"v_{i}_{w.s}", w.a, i,
                           **({"mitbearbeiter_ids": [w.b]} if i in (0, 3) else {}))
                for i in range(35)]
        await w.db.vehicles.insert_many(docs)
        ergebnisse = await asyncio.gather(
            F.fahrzeugpool_trimmen(w.db, w.dealer_id, limit=30, owner_user_id=w.a),
            F.fahrzeugpool_trimmen(w.db, w.dealer_id, limit=30, owner_user_id=w.a),
            F.fahrzeugpool_trimmen(w.db, w.dealer_id, limit=30, owner_user_id=w.a),
            return_exceptions=True)
        rest = {v["id"]: v async for v in w.db.vehicles.find({"dealer_id": w.dealer_id}, {"_id": 0})}
        return ergebnisse, rest

    ergebnisse, rest = welt.run(lauf())
    assert not any(isinstance(e, BaseException) for e in ergebnisse), ergebnisse
    assert sum(ergebnisse) == 3, ergebnisse
    assert len(rest) == 32
    for i in (0, 3):
        v = rest[f"v_{i}_{w.s}"]
        assert v["owner_user_id"] == w.b and v["mitbearbeiter_ids"] == [], i
    assert sum(1 for v in rest.values() if v["owner_user_id"] == w.a) == 30


def test_05_kollege_vergleicht_zwischen_lesen_und_loeschen(welt):
    """Vergleicht B denselben Link, nachdem der Aufraeumlauf die Kandidaten
    gelesen hat ($addToSet mitbearbeiter_ids), bleibt das Fahrzeug stehen —
    der naechste Lauf uebergibt es an B."""
    F, w = _pool(), welt
    vid = f"v_alt_{w.s}"

    async def lauf():
        await w.db.vehicles.insert_many([w.fahrzeug(vid, w.a, 0), w.fahrzeug(f"v_neu_{w.s}", w.a, 1)])
        sync = _sync_vehicles(w)
        proxy = _DbMitEinschub(w.db, lambda: sync.update_one(
            {"id": vid, "dealer_id": w.dealer_id}, {"$addToSet": {"mitbearbeiter_ids": w.b}}))
        n1 = await F.fahrzeugpool_trimmen(proxy, w.dealer_id, limit=1, owner_user_id=w.a)
        v1 = await w.db.vehicles.find_one({"id": vid}, {"_id": 0})
        n2 = await F.fahrzeugpool_trimmen(w.db, w.dealer_id, limit=1, owner_user_id=w.a)
        v2 = await w.db.vehicles.find_one({"id": vid}, {"_id": 0})
        return n1, v1, n2, v2

    n1, v1, n2, v2 = welt.run(lauf())
    assert n1 == 0 and v1 is not None and v1["mitbearbeiter_ids"] == [w.b]
    assert n2 == 0 and v2["owner_user_id"] == w.b and v2["mitbearbeiter_ids"] == []


def test_06_chef_haengt_parallel_um(welt):
    """Haengt der Chef ein Kandidaten-Fahrzeug zwischen Lesen und Loeschen um,
    loescht der Lauf fuer A es nicht mehr (gehoert nicht mehr zu A's Pool)."""
    F, w = _pool(), welt
    vid = f"v_alt_{w.s}"

    async def lauf():
        await w.db.vehicles.insert_many([w.fahrzeug(vid, w.a, 0), w.fahrzeug(f"v_neu_{w.s}", w.a, 1)])
        sync = _sync_vehicles(w)
        proxy = _DbMitEinschub(w.db, lambda: sync.update_one(
            {"id": vid, "dealer_id": w.dealer_id}, {"$set": {"owner_user_id": w.b}}))
        n = await F.fahrzeugpool_trimmen(proxy, w.dealer_id, limit=1, owner_user_id=w.a)
        return n, await w.db.vehicles.find_one({"id": vid}, {"_id": 0})

    n, v = welt.run(lauf())
    assert n == 0 and v is not None and v["owner_user_id"] == w.b


def test_07_firmenweiter_altaufruf_uebergibt_ebenfalls(welt):
    """Ohne owner_user_id (Altaufrufer, Limit je Firma) gilt dieselbe Regel;
    die CAS-Bedingung nimmt den gelesenen Besitzer."""
    F, w = _pool(), welt

    async def lauf():
        await w.db.vehicles.insert_many([
            w.fahrzeug(f"v_mb_{w.s}", w.a, 0, mitbearbeiter_ids=[w.b]),
            w.fahrzeug(f"v_ohne_besitzer_{w.s}", None, 1),
            w.fahrzeug(f"v_neu_{w.s}", w.b, 2),
        ])
        await w.db.vehicles.update_one({"id": f"v_ohne_besitzer_{w.s}"}, {"$unset": {"owner_user_id": ""}})
        n = await F.fahrzeugpool_trimmen(w.db, w.dealer_id, limit=1)
        rest = {v["id"]: v async for v in w.db.vehicles.find({"dealer_id": w.dealer_id}, {"_id": 0})}
        return n, rest

    n, rest = welt.run(lauf())
    assert n == 1 and f"v_ohne_besitzer_{w.s}" not in rest
    assert rest[f"v_mb_{w.s}"]["owner_user_id"] == w.b and rest[f"v_mb_{w.s}"]["mitbearbeiter_ids"] == []


def _neuvergleich_durch_a(w, vid):
    """Einschub: A vergleicht dasselbe alte Fahrzeug erneut (updated_at neu)."""
    sync = _sync_vehicles(w)
    return lambda: sync.update_one(
        {"id": vid, "dealer_id": w.dealer_id},
        {"$set": {"updated_at": "2026-12-31T00:00:00+00:00", "data.mileage": 1}})


def test_08_besitzer_vergleicht_zwischen_lesen_und_loeschen(welt):
    """Nachpruefung Runde 23: Vergleicht A das aelteste Fahrzeug erneut,
    nachdem der Aufraeumlauf die Kandidaten gelesen hat, ist es nicht mehr
    das aelteste — es darf NICHT geloescht werden (updated_at in der CAS)."""
    F, w = _pool(), welt
    vid = f"v_alt_{w.s}"

    async def lauf():
        await w.db.vehicles.insert_many([w.fahrzeug(vid, w.a, 0), w.fahrzeug(f"v_neu_{w.s}", w.a, 1)])
        proxy = _DbMitEinschub(w.db, _neuvergleich_durch_a(w, vid))
        n = await F.fahrzeugpool_trimmen(proxy, w.dealer_id, limit=1, owner_user_id=w.a)
        return n, await w.db.vehicles.find_one({"id": vid}, {"_id": 0})

    n, v = welt.run(lauf())
    assert n == 0 and v is not None and v["owner_user_id"] == w.a


def test_09_besitzer_vergleicht_zwischen_lesen_und_uebergeben(welt):
    """Dito mit Mitbearbeiter: das eben neu verglichene Fahrzeug bleibt bei
    A (keine Uebergabe an B, A verliert es nicht aus seinem Bereich)."""
    F, w = _pool(), welt
    vid = f"v_alt_{w.s}"

    async def lauf():
        await w.db.vehicles.insert_many([w.fahrzeug(vid, w.a, 0, mitbearbeiter_ids=[w.b]),
                                         w.fahrzeug(f"v_neu_{w.s}", w.a, 1)])
        proxy = _DbMitEinschub(w.db, _neuvergleich_durch_a(w, vid))
        n = await F.fahrzeugpool_trimmen(proxy, w.dealer_id, limit=1, owner_user_id=w.a)
        return n, await w.db.vehicles.find_one({"id": vid}, {"_id": 0})

    n, v = welt.run(lauf())
    assert n == 0
    assert v["owner_user_id"] == w.a and v["mitbearbeiter_ids"] == [w.b]
