# -*- coding: utf-8 -*-
"""Runde 23 (11.09.2026): Erst- und Neuvergleich atomar
(routes/listings.py::_fahrzeug_uebernehmen).

Befund 1: Zwei Sucher vergleichen denselben NEUEN Link gleichzeitig — beide
lesen "gibt es noch nicht"; nur einer wurde Hauptbearbeiter, der andere aber
NICHT Mitbearbeiter. Zudem stand "id"/"dealer_id" im $set des Upserts (kann
die automatische DuplicateKey-Wiederholung von MongoDB verhindern -> 500).

Befund 2: Zwischen Lesen des Lebenszyklus und Schreiben der Inseratsdaten
fehlte eine erneute Pruefung — ein zwischenzeitlicher Statuswechsel
(Vertrag durch Kollegen, Chef uebernimmt) konnte den eingefrorenen Stand
mit neueren Inseratsdaten ueberschreiben.

In-Prozess gegen eine eigene Wegwerf-Datenbank (wird am Ende gedroppt).
"""
import asyncio
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pymongo.errors import DuplicateKeyError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"


def _jetzt():
    return datetime.now(timezone.utc).isoformat()


def _module(name):
    import importlib
    return importlib.import_module(name)


class _Fahrzeuge:
    """Stellvertreter fuer db.vehicles: simuliert Nebenlaeufigkeit an genau
    einer Stelle, alles andere geht an die echte Sammlung."""

    def __init__(self, echt):
        self.echt = echt
        self.lesen = 0
        self.erstes_lesen_leer = False      # 1. find_one liefert None (Kollege war schneller)
        self.nach_erstem_lesen = None       # async Haken nach dem 1. find_one
        self.duplicate_key_fehler = 0       # so oft wirft find_one_and_update E11000
        self.upserts = []                   # mitgeschnittene Update-Dokumente

    def __getattr__(self, name):
        return getattr(self.echt, name)

    async def find_one(self, *a, **k):
        self.lesen += 1
        erg = await self.echt.find_one(*a, **k)
        if self.lesen == 1:
            if self.nach_erstem_lesen is not None:
                await self.nach_erstem_lesen()
            if self.erstes_lesen_leer:
                return None
        return erg

    async def find_one_and_update(self, filt, update, *a, **k):
        self.upserts.append(update)
        if self.duplicate_key_fehler > 0:
            self.duplicate_key_fehler -= 1
            raise DuplicateKeyError("E11000 duplicate key error (simuliert)", 11000)
        return await self.echt.find_one_and_update(filt, update, *a, **k)


class _DbProxy:
    def __init__(self, echt, fahrzeuge):
        self._echt = echt
        self._fahrzeuge = fahrzeuge

    def __getattr__(self, name):
        return self._fahrzeuge if name == "vehicles" else getattr(self._echt, name)

    def __getitem__(self, name):
        return self._fahrzeuge if name == "vehicles" else self._echt[name]


@pytest.fixture
def welt():
    from motor.motor_asyncio import AsyncIOMotorClient
    s = uuid.uuid4().hex[:10]
    names = ["deps", "routes.listings"]
    mods = [_module(n) for n in names]
    alt = [(m, getattr(m, "db", None)) for m in mods]

    class _Ctx:
        def __init__(self):
            self.s = s
            self.dealer_id = f"d_r23a_{s}"
            self.chef = {"id": f"chef_r23a_{s}", "dealer_id": self.dealer_id, "role": "dealer"}
            self.a = {"id": f"sa_r23a_{s}", "dealer_id": self.dealer_id, "role": "sucher"}
            self.b = {"id": f"sb_r23a_{s}", "dealer_id": self.dealer_id, "role": "sucher"}
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
            self.db_name = f"autoschnell_r23_atomar_{s}"
            self.db = self.client[self.db_name]
            for m in mods:
                if hasattr(m, "db"):
                    m.db = self.db

        def run(self, coro):
            return self.loop.run_until_complete(coro)

        def proxy(self):
            """Stellvertreter in routes.listings einsetzen (Fixture stellt zurueck)."""
            fz = _Fahrzeuge(self.db.vehicles)
            _module("routes.listings").db = _DbProxy(self.db, fz)
            return fz

        def fahrzeug(self, vid, **extra):
            doc = {"id": vid, "dealer_id": self.dealer_id, "lifecycle": "verglichen",
                   "status": "verglichen", "owner_user_id": self.a["id"],
                   "data": {"make_label": "BMW", "mileage": 1},
                   "created_at": "2026-09-01T10:00:00+00:00",
                   "updated_at": "2026-09-01T10:00:00+00:00"}
            doc.update(extra)
            return {k: v for k, v in doc.items() if v is not None}

    ctx = _Ctx()
    # Wie server.py (Runde 17): EIN Fahrzeugdokument je (Firma, Fahrzeug-ID).
    ctx.run(ctx.db.vehicles.create_index([("dealer_id", 1), ("id", 1)], unique=True))
    ctx.run(ctx.db.users.insert_many([
        {"id": ctx.chef["id"], "dealer_id": ctx.dealer_id, "role": "dealer", "active": True,
         "email": f"{ctx.chef['id']}@e2etest-mail.de", "created_at": _jetzt()},
        {"id": ctx.a["id"], "dealer_id": ctx.dealer_id, "role": "sucher", "active": True,
         "first_name": "Anna", "last_name": "A", "email": f"{ctx.a['id']}@e2etest-mail.de",
         "created_at": _jetzt()},
        {"id": ctx.b["id"], "dealer_id": ctx.dealer_id, "role": "sucher", "active": True,
         "first_name": "Ben", "last_name": "B", "email": f"{ctx.b['id']}@e2etest-mail.de",
         "created_at": _jetzt()}]))
    yield ctx
    try:
        ctx.run(ctx.client.drop_database(ctx.db_name))
    finally:
        for m, d in alt:
            if d is not None:
                m.db = d
        ctx.client.close()
        ctx.loop.close()


# ================================================= Befund 1
def test_01_erstvergleich_setzt_id_und_firma_nur_ueber_filter(welt):
    """Neues Fahrzeug: "id"/"dealer_id" stehen NICHT im $set (sonst kann
    MongoDB den DuplicateKey des Upserts nicht selbst wiederholen), das
    Dokument hat sie trotzdem (aus dem Gleichheitsfilter)."""
    L = _module("routes.listings")
    w = welt
    vid = f"v_{w.s}"
    fz = w.proxy()

    async def lauf():
        k = await L._fahrzeug_uebernehmen(w.a, vid, w.s, {"make_label": "BMW", "mileage": 7},
                                          quelle="kleinanzeigen")
        v = await w.db.vehicles.find_one({"id": vid}, {"_id": 0})
        return k, v

    k, v = w.run(lauf())
    assert k is None
    assert len(fz.upserts) == 1
    # Runde 27: Beim Erstvergleich steht nichts mehr im $set (nur noch
    # $setOnInsert) — die Zusage 'id/dealer_id kommen aus dem Filter' gilt
    # damit erst recht.
    gesetzt = {**fz.upserts[0].get("$set", {}), **fz.upserts[0].get("$setOnInsert", {})}
    assert "id" not in gesetzt and "dealer_id" not in gesetzt
    assert fz.upserts[0]["$setOnInsert"]["owner_user_id"] == w.a["id"]
    assert v["id"] == vid and v["dealer_id"] == w.dealer_id
    assert v["owner_user_id"] == w.a["id"] and v["lifecycle"] == "verglichen"
    assert v["data"]["mileage"] == 7 and v["quelle"] == "kleinanzeigen" and v["mobile_ad_id"] == w.s
    assert "mitbearbeiter_ids" not in v


def test_02_verpasster_erstvergleich_macht_zweiten_sucher_zum_mitbearbeiter(welt):
    """B liest "gibt es noch nicht" (simuliert), A hat das Fahrzeug aber
    inzwischen angelegt -> B wird Mitbearbeiter, bekommt den Kollegen-Hinweis
    ("seit" = Stand VOR B's Schreiben, wie im Zweig "vorhanden"), ein Dokument."""
    L = _module("routes.listings")
    w = welt
    vid = f"v_{w.s}"

    async def lauf():
        await w.db.vehicles.insert_one(w.fahrzeug(vid))
        fz = w.proxy()
        fz.erstes_lesen_leer = True
        k = await L._fahrzeug_uebernehmen(w.b, vid, w.s, {"make_label": "BMW", "mileage": 2})
        n = await w.db.vehicles.count_documents({"id": vid, "dealer_id": w.dealer_id})
        v = await w.db.vehicles.find_one({"id": vid}, {"_id": 0})
        return fz, k, n, v

    fz, k, n, v = w.run(lauf())
    assert len(fz.upserts) == 1, "Upsert-Zweig wurde durchlaufen"
    assert k == {"user_id": w.a["id"], "name": "Anna A",
                 "seit": "2026-09-01T10:00:00+00:00", "mitbearbeiter": True}
    assert n == 1
    assert v["owner_user_id"] == w.a["id"] and v["mitbearbeiter_ids"] == [w.b["id"]]
    assert v["data"]["mileage"] == 2, "noch 'verglichen': Inseratsdaten wie bei jedem Vergleich"
    assert v["created_at"] == "2026-09-01T10:00:00+00:00", "$setOnInsert greift nicht erneut"


def test_03_verpasster_erstvergleich_chef_wird_nicht_mitbearbeiter(welt):
    L = _module("routes.listings")
    w = welt
    vid = f"v_{w.s}"

    async def lauf():
        await w.db.vehicles.insert_one(w.fahrzeug(vid))
        fz = w.proxy()
        fz.erstes_lesen_leer = True
        k = await L._fahrzeug_uebernehmen(w.chef, vid, w.s, {"make_label": "BMW", "mileage": 3})
        return k, await w.db.vehicles.find_one({"id": vid}, {"_id": 0})

    k, v = w.run(lauf())
    assert k is None and v["owner_user_id"] == w.a["id"] and "mitbearbeiter_ids" not in v


def test_04_duplicate_key_wird_einmal_wiederholt(welt):
    """Meldet der Upsert trotzdem E11000, laeuft die Funktion EINMAL erneut ab
    dem Lesen — dann im Zweig "vorhanden" (mit CAS), kein HTTP 500."""
    L = _module("routes.listings")
    w = welt
    vid = f"v_{w.s}"

    async def lauf():
        await w.db.vehicles.insert_one(w.fahrzeug(vid))
        fz = w.proxy()
        fz.erstes_lesen_leer = True
        fz.duplicate_key_fehler = 1
        k = await L._fahrzeug_uebernehmen(w.b, vid, w.s, {"make_label": "BMW", "mileage": 4})
        n = await w.db.vehicles.count_documents({"id": vid, "dealer_id": w.dealer_id})
        return fz, k, n, await w.db.vehicles.find_one({"id": vid}, {"_id": 0})

    fz, k, n, v = w.run(lauf())
    assert fz.lesen == 2 and len(fz.upserts) == 1
    assert k["user_id"] == w.a["id"] and k["mitbearbeiter"] is True
    assert n == 1 and v["owner_user_id"] == w.a["id"] and v["mitbearbeiter_ids"] == [w.b["id"]]
    assert v["data"]["mileage"] == 4


def test_05_gleichzeitige_erstvergleiche_immer_ein_besitzer_ein_mitbearbeiter(welt):
    """asyncio.gather zweier Erstvergleiche auf denselben neuen Link, 20x."""
    L = _module("routes.listings")
    w = welt

    async def einmal(i):
        vid = f"v_{w.s}_{i}"
        ka, kb = await asyncio.gather(
            L._fahrzeug_uebernehmen(w.a, vid, f"{w.s}{i}", {"make_label": "BMW", "mileage": 1}),
            L._fahrzeug_uebernehmen(w.b, vid, f"{w.s}{i}", {"make_label": "BMW", "mileage": 2}))
        docs = await w.db.vehicles.find({"id": vid, "dealer_id": w.dealer_id}, {"_id": 0}).to_list(5)
        return ka, kb, docs

    async def lauf():
        return [await einmal(i) for i in range(20)]

    for i, (ka, kb, docs) in enumerate(w.run(lauf())):
        assert len(docs) == 1, f"Lauf {i}: genau ein Dokument"
        v = docs[0]
        besitzer = v["owner_user_id"]
        assert besitzer in (w.a["id"], w.b["id"]), f"Lauf {i}"
        anderer = w.b["id"] if besitzer == w.a["id"] else w.a["id"]
        assert v.get("mitbearbeiter_ids") == [anderer], f"Lauf {i}: Verlierer ist Mitbearbeiter"
        k_gewinner, k_verlierer = (ka, kb) if besitzer == w.a["id"] else (kb, ka)
        assert k_gewinner is None, f"Lauf {i}"
        assert k_verlierer and k_verlierer["user_id"] == besitzer \
            and k_verlierer["mitbearbeiter"] is True, f"Lauf {i}: Kollegen-Hinweis"
        assert v["lifecycle"] == "verglichen"


# ================================================= Befund 2
@pytest.mark.parametrize("gelesen,neu", [
    ("verglichen", "vertrag_erstellt"),
    (None, "gekauft"),                   # Altdokument ohne lifecycle-Feld
    ("storniert", "verglichen"),         # Neuanfang durch Kollegen -> dessen Stand bleibt
])
def test_06_statuswechsel_zwischen_lesen_und_schreiben_laesst_data_stehen(welt, gelesen, neu):
    L = _module("routes.listings")
    w = welt
    vid = f"v_{w.s}"
    frisch = {"make_label": "BMW", "mileage": 999}

    async def lauf():
        await w.db.vehicles.insert_one(w.fahrzeug(vid, lifecycle=gelesen))
        fz = w.proxy()

        async def kollege_macht_vertrag():
            await w.db.vehicles.update_one({"id": vid, "dealer_id": w.dealer_id},
                                           {"$set": {"lifecycle": neu}})
        fz.nach_erstem_lesen = kollege_macht_vertrag
        k = await L._fahrzeug_uebernehmen(w.a, vid, w.s, frisch)
        return k, await w.db.vehicles.find_one({"id": vid}, {"_id": 0})

    k, v = w.run(lauf())
    assert k is None
    assert v["lifecycle"] == neu
    assert v["data"] == {"make_label": "BMW", "mileage": 1}, "eingefrorener Stand bleibt"
    assert v["inserat_aktuell"] == frisch and v.get("inserat_aktuell_am")


def test_07_ohne_statuswechsel_bleibt_das_bisherige_verhalten(welt):
    """Neuvergleich ohne Zwischenfall: noch "verglichen" bzw. Altdokument
    ohne lifecycle -> data wird aktualisiert; fortgeschritten -> nur
    inserat_aktuell; Altbestand ohne Besitzer uebernimmt, wer vergleicht."""
    L = _module("routes.listings")
    w = welt
    v1, v2, v3 = f"v1_{w.s}", f"v2_{w.s}", f"v3_{w.s}"

    async def lauf():
        await w.db.vehicles.insert_many([
            w.fahrzeug(v1),
            w.fahrzeug(v2, lifecycle=None, owner_user_id=None),
            w.fahrzeug(v3, lifecycle="bestand")])
        k1 = await L._fahrzeug_uebernehmen(w.a, v1, "1", {"mileage": 11})
        k2 = await L._fahrzeug_uebernehmen(w.b, v2, "2", {"mileage": 22})
        k3 = await L._fahrzeug_uebernehmen(w.b, v3, "3", {"mileage": 33})
        docs = {d["id"]: d async for d in w.db.vehicles.find({"dealer_id": w.dealer_id}, {"_id": 0})}
        return k1, k2, k3, docs

    k1, k2, k3, docs = w.run(lauf())
    assert k1 is None and docs[v1]["data"] == {"mileage": 11} and "inserat_aktuell" not in docs[v1]
    assert k2 is None and docs[v2]["data"] == {"mileage": 22} and docs[v2]["owner_user_id"] == w.b["id"]
    assert "lifecycle" not in docs[v2], "Altdokument bekommt keinen Lebenszyklus untergeschoben"
    assert k3["user_id"] == w.a["id"] and docs[v3]["mitbearbeiter_ids"] == [w.b["id"]]
    assert docs[v3]["data"] == {"make_label": "BMW", "mileage": 1}
    assert docs[v3]["inserat_aktuell"] == {"mileage": 33}


def test_08_zwischendurch_entferntes_fahrzeug_wird_neu_angelegt(welt):
    """Verschwindet das Fahrzeug zwischen Lesen und Schreiben (z. B.
    Pool-Begrenzung), wird es wie beim Erstvergleich neu angelegt — der
    Vergleichende ist dann Hauptbearbeiter, kein Kollegen-Hinweis."""
    L = _module("routes.listings")
    w = welt
    vid = f"v_{w.s}"

    async def lauf():
        await w.db.vehicles.insert_one(w.fahrzeug(vid))
        fz = w.proxy()

        async def weg():
            await w.db.vehicles.delete_one({"id": vid, "dealer_id": w.dealer_id})
        fz.nach_erstem_lesen = weg
        k = await L._fahrzeug_uebernehmen(w.b, vid, w.s, {"make_label": "BMW", "mileage": 5})
        docs = await w.db.vehicles.find({"id": vid, "dealer_id": w.dealer_id}, {"_id": 0}).to_list(5)
        return k, docs

    k, docs = w.run(lauf())
    assert k is None and len(docs) == 1
    assert docs[0]["owner_user_id"] == w.b["id"] and docs[0]["lifecycle"] == "verglichen"
    assert docs[0]["data"]["mileage"] == 5 and "mitbearbeiter_ids" not in docs[0]
