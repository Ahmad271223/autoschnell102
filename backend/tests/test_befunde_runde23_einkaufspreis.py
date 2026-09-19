# -*- coding: utf-8 -*-
"""Runde 23 (11.09.2026): Befunde des externen Pruefers, Strang Einkaufspreis.

Befund A: Die Fahrzeugakte eines Suchers rechnete den Einkaufspreis
firmenweit (kaufvorgang.einkaufspreis_vorschlag) — Sucher B sah den
Vertragspreis von Sucher A. Ausserdem gingen vehicles.purchase_price /
abgeholt_kaufvorgang_id ueber /bestand, /vehicles, /vehicles/{id} und die
Akte an jeden Sucher des gemeinsamen Fahrzeugs.
Regel: Sucher sehen nur ihren eigenen Einkaufspreis; der Chef sieht alles.

Befund B: Parallele Abholabschluesse verschiedener Vorgaenge lasen beide
"kein massgeblicher Vorgang festgelegt"; der letzte Write gewann und
ueberschrieb den bereits festgehaltenen Vorgang samt Preis.

In-Prozess gegen eine eigene Wegwerf-Datenbank (wird am Ende geloescht).
"""
import asyncio
import contextvars
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"

PREIS_A = 20000.0
PREIS_B = 25000.0


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
             "routes.appointments", "routes.bestand", "routes.protocols"]
    mods = [_module(n) for n in names]
    alt = [(m, getattr(m, "db", None)) for m in mods]
    db_name = f"autoschnell_r23_einkauf_{s}"

    class _Ctx:
        def __init__(self):
            self.s = s
            self.dealer_id = f"d_r23e_{s}"
            self.chef = {"id": f"chef_r23e_{s}", "dealer_id": self.dealer_id, "role": "dealer"}
            self.a = {"id": f"sa_r23e_{s}", "dealer_id": self.dealer_id, "role": "sucher"}
            self.b = {"id": f"sb_r23e_{s}", "dealer_id": self.dealer_id, "role": "sucher"}
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
            self.db = self.client[db_name]
            for m in mods:
                if hasattr(m, "db"):
                    m.db = self.db

        def run(self, coro):
            return self.loop.run_until_complete(coro)

        def fahrzeug(self, vid, **extra):
            # Gemeinsames Inserat: A Hauptbearbeiter, B Mitbearbeiter.
            d = {"id": vid, "dealer_id": self.dealer_id, "lifecycle": "gekauft",
                 "mobile_ad_id": vid[2:], "owner_user_id": self.a["id"],
                 "mitbearbeiter_ids": [self.b["id"]],
                 "data": {"make_label": "BMW", "model_label": "320d"},
                 "created_at": _jetzt(), "updated_at": _jetzt(),
                 "lifecycle_changed_at": _jetzt()}
            d.update(extra)
            return d

    ctx = _Ctx()
    ctx.run(ctx.db.users.insert_many([
        {"id": ctx.chef["id"], "dealer_id": ctx.dealer_id, "role": "dealer", "active": True,
         "email": f"{ctx.chef['id']}@e2etest-mail.de", "created_at": _jetzt()},
        {"id": ctx.a["id"], "dealer_id": ctx.dealer_id, "role": "sucher", "active": True,
         "first_name": "Anna", "last_name": "A", "email": f"{ctx.a['id']}@e2etest-mail.de",
         "created_at": _jetzt()},
        {"id": ctx.b["id"], "dealer_id": ctx.dealer_id, "role": "sucher", "active": True,
         "first_name": "Ben", "last_name": "B", "email": f"{ctx.b['id']}@e2etest-mail.de",
         "created_at": _jetzt()}]))
    ctx.run(ctx.db.dealers.insert_one({"id": ctx.dealer_id, "user_id": ctx.chef["id"],
                                       "company_name": "R23 GmbH", "created_at": _jetzt()}))
    yield ctx
    try:
        ctx.run(ctx.client.drop_database(db_name))
    finally:
        for m, d in alt:
            if d is not None:
                m.db = d
        ctx.client.close()
        ctx.loop.close()


async def _zwei_vorgaenge(w, vid):
    """A und B legen je einen eigenen Vorgang an; B ist der juengere."""
    KV = _module("kaufvorgang")
    await w.db.vehicles.insert_one(w.fahrzeug(vid))
    # Runde 32: Der Bestand zaehlt gespeicherte Vertraege — zu jedem Vorgang
    # gehoert (wie in echt) das Vertragsdokument.
    await w.db.generated_pdfs.insert_many([
        {"id": f"ca_{vid}", "dealer_id": w.dealer_id, "user_id": w.a["id"],
         "vehicle_id": vid, "contract_no": "R23-A", "created_at": _jetzt()},
        {"id": f"cb_{vid}", "dealer_id": w.dealer_id, "user_id": w.b["id"],
         "vehicle_id": vid, "contract_no": "R23-B", "created_at": _jetzt()}])
    kv_a = await KV.anlegen(dealer_id=w.dealer_id, user_id=w.a["id"], vehicle_id=vid,
                            contract_id=f"ca_{vid}", purchase_price=PREIS_A)
    kv_b = await KV.anlegen(dealer_id=w.dealer_id, user_id=w.b["id"], vehicle_id=vid,
                            contract_id=f"cb_{vid}", purchase_price=PREIS_B)
    await w.db.kaufvorgaenge.update_one({"id": kv_a["id"]},
                                        {"$set": {"updated_at": "2026-09-11T10:00:00+00:00"}})
    await w.db.kaufvorgaenge.update_one({"id": kv_b["id"]},
                                        {"$set": {"updated_at": "2026-09-11T11:00:00+00:00"}})
    return kv_a["id"], kv_b["id"]


def _ohne_einkauf(doc):
    return "purchase_price" not in doc and "abgeholt_kaufvorgang_id" not in doc


# ============================================================ Befund A
def test_01_vor_abholung_sieht_jeder_sucher_nur_seinen_vertragspreis(welt):
    KV = _module("kaufvorgang")
    Bst = _module("routes.bestand")
    w = welt
    vid = f"v_vor_{w.s}"

    async def lauf():
        kv_a, kv_b = await _zwei_vorgaenge(w, vid)
        return (kv_a, kv_b,
                await Bst.vehicle_akte(vid, w.a), await Bst.vehicle_akte(vid, w.b),
                await Bst.vehicle_akte(vid, w.chef),
                await KV.einkaufspreis_vorschlag(vid, w.dealer_id))

    kv_a, kv_b, akte_a, akte_b, akte_chef, firmenweit = w.run(lauf())
    assert akte_a["einkaufspreis"] == {"preis": PREIS_A, "quelle": "vertrag", "kaufvorgang_id": kv_a}
    assert akte_b["einkaufspreis"] == {"preis": PREIS_B, "quelle": "vertrag", "kaufvorgang_id": kv_b}
    assert [k["id"] for k in akte_b["kaufvorgaenge"]] == [kv_b]
    # Chef unveraendert: firmenweit (juengster offener Vertrag) — wie bisher
    assert akte_chef["einkaufspreis"] == firmenweit
    assert firmenweit == {"preis": PREIS_B, "quelle": "vertrag", "kaufvorgang_id": kv_b}
    assert {k["id"] for k in akte_chef["kaufvorgaenge"]} == {kv_a, kv_b}


def test_02_nach_abholung_von_a_sieht_b_den_preis_von_a_nicht(welt):
    KV = _module("kaufvorgang")
    Bst = _module("routes.bestand")
    L = _module("routes.listings")
    w = welt
    vid = f"v_nach_{w.s}"

    async def lauf():
        kv_a, kv_b = await _zwei_vorgaenge(w, vid)
        await KV.status_setzen(kv_a, "abgeholt", user=w.a)
        r = {"kv_a": kv_a, "kv_b": kv_b}
        for name, u in (("a", w.a), ("b", w.b), ("chef", w.chef)):
            r[f"akte_{name}"] = await Bst.vehicle_akte(vid, u)
            r[f"bestand_{name}"] = next(x for x in (await Bst.list_bestand(u))["items"]
                                        if x["id"] == vid)
            r[f"liste_{name}"] = next(x for x in await L.list_vehicles(u) if x["id"] == vid)
            r[f"detail_{name}"] = await L.get_vehicle_detail(vid, u)
        r["v_db"] = await w.db.vehicles.find_one({"id": vid}, {"_id": 0})
        # Danach schliesst auch B ab — der von A bleibt massgeblich (Runde 18).
        await KV.status_setzen(kv_b, "abgeholt", user=w.b)
        r["akte_b2"] = await Bst.vehicle_akte(vid, w.b)
        r["akte_a2"] = await Bst.vehicle_akte(vid, w.a)
        r["v_db2"] = await w.db.vehicles.find_one({"id": vid}, {"_id": 0})
        return r

    r = w.run(lauf())
    kv_a, kv_b = r["kv_a"], r["kv_b"]
    # Fahrzeug haelt den realisierten Vorgang von A (unveraendert in der DB)
    assert r["v_db"]["purchase_price"] == PREIS_A and r["v_db"]["abgeholt_kaufvorgang_id"] == kv_a

    # B: nirgends der Preis/Vorgang von A
    for k in ("bestand_b", "liste_b", "detail_b"):
        assert _ohne_einkauf(r[k]), k
    assert _ohne_einkauf(r["akte_b"]["vehicle"])
    assert r["akte_b"]["einkaufspreis"] == {"preis": PREIS_B, "quelle": "vertrag",
                                            "kaufvorgang_id": kv_b}
    assert [k["id"] for k in r["akte_b"]["kaufvorgaenge"]] == [kv_b]

    # A: sein realisierter Preis
    for k in ("bestand_a", "liste_a", "detail_a"):
        assert r[k]["purchase_price"] == PREIS_A and r[k]["abgeholt_kaufvorgang_id"] == kv_a, k
    assert r["akte_a"]["vehicle"]["purchase_price"] == PREIS_A
    assert r["akte_a"]["einkaufspreis"] == {"preis": PREIS_A, "quelle": "fahrzeug",
                                            "kaufvorgang_id": kv_a}

    # Chef: der realisierte Preis, ueberall
    for k in ("bestand_chef", "liste_chef", "detail_chef"):
        assert r[k]["purchase_price"] == PREIS_A and r[k]["abgeholt_kaufvorgang_id"] == kv_a, k
    assert r["akte_chef"]["vehicle"]["purchase_price"] == PREIS_A
    assert r["akte_chef"]["einkaufspreis"] == {"preis": PREIS_A, "quelle": "fahrzeug",
                                               "kaufvorgang_id": kv_a}

    # B ebenfalls abgeholt: Fahrzeug bleibt bei A; B sieht seinen eigenen
    # abgeholten Preis, aber weiterhin nicht das Fahrzeugfeld von A.
    assert r["v_db2"]["abgeholt_kaufvorgang_id"] == kv_a and r["v_db2"]["purchase_price"] == PREIS_A
    assert _ohne_einkauf(r["akte_b2"]["vehicle"])
    assert r["akte_b2"]["einkaufspreis"] == {"preis": PREIS_B, "quelle": "abgeholt",
                                             "kaufvorgang_id": kv_b}
    assert r["akte_a2"]["vehicle"]["purchase_price"] == PREIS_A


def test_03_sucher_ohne_eigenen_vorgang_und_fremd_eingetragener_preis(welt):
    """B ohne eigenen Vorgang sieht keinen Preis (auch nicht den abgeholten
    von A); ein am Fahrzeug eingetragener Preis ohne eigenen Vorgang (z. B.
    manuell vom Chef, danach einem Sucher zugewiesen) geht nicht an Sucher."""
    KV = _module("kaufvorgang")
    Bst = _module("routes.bestand")
    L = _module("routes.listings")
    w = welt
    vid = f"v_nur_a_{w.s}"
    vman = f"m_{w.s}"

    async def lauf():
        await w.db.vehicles.insert_one(w.fahrzeug(vid))
        kv_a = await KV.anlegen(dealer_id=w.dealer_id, user_id=w.a["id"], vehicle_id=vid,
                                contract_id=f"ca_{vid}", purchase_price=PREIS_A)
        vor = await Bst.vehicle_akte(vid, w.b)
        await KV.status_setzen(kv_a["id"], "abgeholt", user=w.a)
        nach = await Bst.vehicle_akte(vid, w.b)
        liste_b = next(x for x in await L.list_vehicles(w.b) if x["id"] == vid)
        await w.db.vehicles.insert_one(w.fahrzeug(vman, source="manuell", lifecycle="bestand",
                                                  purchase_price=9999.0))
        man_a = await L.get_vehicle_detail(vman, w.a)
        man_akte_a = await Bst.vehicle_akte(vman, w.a)
        man_bestand_a = next(x for x in (await Bst.list_bestand(w.a))["items"] if x["id"] == vman)
        man_chef = await L.get_vehicle_detail(vman, w.chef)
        return vor, nach, liste_b, man_a, man_akte_a, man_bestand_a, man_chef

    vor, nach, liste_b, man_a, man_akte_a, man_bestand_a, man_chef = w.run(lauf())
    keiner = {"preis": None, "quelle": "keiner", "kaufvorgang_id": None}
    assert vor["einkaufspreis"] == keiner
    assert nach["einkaufspreis"] == keiner and _ohne_einkauf(nach["vehicle"])
    assert nach["kaufvorgaenge"] == []
    assert _ohne_einkauf(liste_b)
    assert _ohne_einkauf(man_a) and _ohne_einkauf(man_bestand_a)
    assert _ohne_einkauf(man_akte_a["vehicle"]) and man_akte_a["einkaufspreis"] == keiner
    assert man_chef["purchase_price"] == 9999.0


def test_04_einkaufspreis_vorschlag_mit_user_id(welt):
    KV = _module("kaufvorgang")
    w = welt
    vid = f"v_vs_{w.s}"

    async def lauf():
        kv_a, kv_b = await _zwei_vorgaenge(w, vid)
        await KV.status_setzen(kv_a, "abgeholt", user=w.a)
        return (kv_a, kv_b,
                await KV.einkaufspreis_vorschlag(vid, w.dealer_id),
                await KV.einkaufspreis_vorschlag(vid, w.dealer_id, user_id=w.a["id"]),
                await KV.einkaufspreis_vorschlag(vid, w.dealer_id, user_id=w.b["id"]),
                await KV.einkaufspreis_vorschlag(vid, w.dealer_id, user_id="fremd"))

    kv_a, kv_b, chef, a, b, fremd = w.run(lauf())
    assert chef == {"preis": PREIS_A, "quelle": "fahrzeug", "kaufvorgang_id": kv_a}
    assert a == {"preis": PREIS_A, "quelle": "fahrzeug", "kaufvorgang_id": kv_a}
    assert b == {"preis": PREIS_B, "quelle": "vertrag", "kaufvorgang_id": kv_b}
    assert fremd == {"preis": None, "quelle": "keiner", "kaufvorgang_id": None}


# ============================================================ Befund B
_rolle = contextvars.ContextVar("r23_rolle", default=None)


class _Steuerung:
    def __init__(self):
        self.a_hat_gelesen = asyncio.Event()
        self.b_hat_geschrieben = asyncio.Event()
        self.a_pausiert = False
        self.writes = []            # (rolle, abgeholt_kaufvorgang_id, matched)


class _Durchreichen:
    def __init__(self, echt, st):
        self._echt, self._st = echt, st

    def __getattr__(self, name):
        return getattr(self._echt, name)


class _Fahrzeuge(_Durchreichen):
    async def update_one(self, filt, upd, *a, **k):
        res = await self._echt.update_one(filt, upd, *a, **k)
        neu = (upd.get("$set") or {}).get("abgeholt_kaufvorgang_id")
        if neu is not None:
            self._st.writes.append((_rolle.get(), neu, res.matched_count))
            if _rolle.get() == "B":
                self._st.b_hat_geschrieben.set()
        return res


class _Cursor:
    def __init__(self, echt, st, filt):
        self._echt, self._st, self._filt = echt, st, filt

    # Runde 29: die Zusammenfassung fragt jetzt gezielt (sortiert, begrenzt)
    # statt 500 Vorgaenge zu laden — die Attrappe reicht beides durch.
    def sort(self, *a, **k):
        self._echt = self._echt.sort(*a, **k)
        return self

    def limit(self, *a, **k):
        self._echt = self._echt.limit(*a, **k)
        return self

    async def to_list(self, *a, **k):
        docs = await self._echt.to_list(*a, **k)
        if (_rolle.get() == "A" and not self._st.a_pausiert
                and "vehicle_id" in self._filt and "id" not in self._filt):
            # A hat Fahrzeug UND Vorgaenge gelesen (Zusammenfassung) und wartet,
            # bis B geschrieben hat -> A schreibt mit altem Stand.
            self._st.a_pausiert = True
            self._st.a_hat_gelesen.set()
            await asyncio.wait_for(self._st.b_hat_geschrieben.wait(), 10)
        return docs


class _Vorgaenge(_Durchreichen):
    def find(self, filt=None, *a, **k):
        return _Cursor(self._echt.find(filt, *a, **k), self._st, filt or {})

    async def find_one_and_update(self, *a, **k):
        if _rolle.get() == "B":
            # B schliesst erst ab, nachdem A seine Vorgaenge gelesen hat
            # (A kennt nur den eigenen als abgeholt).
            await asyncio.wait_for(self._st.a_hat_gelesen.wait(), 10)
        return await self._echt.find_one_and_update(*a, **k)


class _Db(_Durchreichen):
    def __init__(self, echt, st):
        super().__init__(echt, st)
        self.vehicles = _Fahrzeuge(echt.vehicles, st)
        self.kaufvorgaenge = _Vorgaenge(echt.kaufvorgaenge, st)


async def _als(rolle, coro):
    _rolle.set(rolle)
    return await coro


def test_05_paralleler_abholabschluss_ueberschreibt_den_festgehaltenen_nicht(welt):
    """Gesteuerter Wettlauf ueber asyncio.gather: A liest den Fahrzeugstand
    (nichts festgelegt), B schliesst dazwischen ab und haelt seinen Vorgang
    fest, dann schreibt A mit dem alten Stand. Vorher gewann A (letzter
    Write) — jetzt scheitert A am CAS, liest neu und laesst B stehen."""
    KV = _module("kaufvorgang")
    w = welt
    vid = f"v_race_{w.s}"
    st = _Steuerung()

    async def lauf():
        kv_a, kv_b = await _zwei_vorgaenge(w, vid)
        KV.db = _Db(w.db, st)
        try:
            await asyncio.gather(
                _als("A", KV.status_setzen(kv_a, "abgeholt", user=w.a)),
                _als("B", KV.status_setzen(kv_b, "abgeholt", user=w.b)))
        finally:
            KV.db = w.db
        v = await w.db.vehicles.find_one({"id": vid}, {"_id": 0})
        return kv_a, kv_b, v

    kv_a, kv_b, v = w.run(lauf())
    gewonnen = {kv for _r, kv, matched in st.writes if matched}
    assert gewonnen == {kv_b}, f"nur EIN Vorgang darf festgehalten werden: {st.writes}"
    assert ("A", kv_a, 0) in st.writes, "der veraltete Write von A muss am CAS scheitern"
    assert v["abgeholt_kaufvorgang_id"] == kv_b and v["purchase_price"] == PREIS_B
    assert v["lifecycle"] == "abgeholt"


def test_05b_veralteter_lauf_schreibt_alten_preis_desselben_vorgangs_nicht_zurueck(welt):
    """Gegenpruefung Runde 23: Der CAS pruefte nur die Vorgangs-ID. Lauf A
    (Zusammenfassung nach Fahrerabschluss) liest Vorgang X mit 20000; der
    Chef traegt dazwischen den Vor-Ort-Preis 18000 nach (appointments.py
    final_price -> status_setzen extra), Lauf B schreibt X/18000. Vorher
    schrieb A danach X/20000 zurueck — Fahrzeug und Vorgang passten nicht
    mehr zusammen. Jetzt scheitert A am Preis im CAS, liest neu, schreibt 18000."""
    KV = _module("kaufvorgang")
    w = welt
    vid = f"v_preis_{w.s}"
    st = _Steuerung()
    VOR_ORT = 18000.0

    async def lauf():
        await w.db.vehicles.insert_one(w.fahrzeug(vid))
        kv = await KV.anlegen(dealer_id=w.dealer_id, user_id=w.a["id"], vehicle_id=vid,
                              contract_id=f"ca_{vid}", purchase_price=PREIS_A)
        await KV.status_setzen(kv["id"], "abgeholt", user=w.a)
        v0 = await w.db.vehicles.find_one({"id": vid}, {"_id": 0})
        KV.db = _Db(w.db, st)
        try:
            await asyncio.gather(
                _als("A", KV.fahrzeug_status_aggregieren(vid, w.dealer_id, user=w.a)),
                _als("B", KV.status_setzen(kv["id"], "abgeholt", user=w.chef,
                                           extra={"purchase_price": VOR_ORT,
                                                  "preis_quelle": "vor_ort"})))
        finally:
            KV.db = w.db
        v = await w.db.vehicles.find_one({"id": vid}, {"_id": 0})
        kv_db = await w.db.kaufvorgaenge.find_one({"id": kv["id"]}, {"_id": 0})
        return kv["id"], v0, v, kv_db

    kv_id, v0, v, kv_db = w.run(lauf())
    assert v0["abgeholt_kaufvorgang_id"] == kv_id and v0["purchase_price"] == PREIS_A
    assert st.a_pausiert, "Steuerung hat nicht gegriffen"
    assert ("A", kv_id, 0) in st.writes, f"veralteter Write von A muss scheitern: {st.writes}"
    assert kv_db["purchase_price"] == VOR_ORT
    assert v["abgeholt_kaufvorgang_id"] == kv_id and v["purchase_price"] == VOR_ORT


def test_06_gather_ohne_steuerung_bleibt_konsistent(welt):
    KV = _module("kaufvorgang")
    w = welt

    async def lauf():
        out = []
        for i in range(4):
            vid = f"v_gather{i}_{w.s}"
            kv_a, kv_b = await _zwei_vorgaenge(w, vid)
            await asyncio.gather(KV.status_setzen(kv_a, "abgeholt", user=w.a),
                                 KV.status_setzen(kv_b, "abgeholt", user=w.b))
            v = await w.db.vehicles.find_one({"id": vid}, {"_id": 0})
            out.append(({kv_a: PREIS_A, kv_b: PREIS_B}, v))
        return out

    for preise, v in w.run(lauf()):
        assert v["abgeholt_kaufvorgang_id"] in preise
        assert v["purchase_price"] == preise[v["abgeholt_kaufvorgang_id"]]
        assert v["lifecycle"] == "abgeholt"


def test_07_nicht_mehr_abgeholter_vorgang_wird_abgeloest(welt):
    """Der CAS darf den festgehaltenen Vorgang ersetzen, wenn dieser nicht
    mehr abgeholt ist (z. B. korrigiert auf nicht abgeholt)."""
    KV = _module("kaufvorgang")
    w = welt
    vid = f"v_abl_{w.s}"

    async def lauf():
        kv_a, kv_b = await _zwei_vorgaenge(w, vid)
        await KV.status_setzen(kv_a, "abgeholt", user=w.a)
        await KV.status_setzen(kv_b, "abgeholt", user=w.b)
        v1 = await w.db.vehicles.find_one({"id": vid}, {"_id": 0})
        await KV.status_setzen(kv_a, "nicht_abgeholt", user=w.a)
        v2 = await w.db.vehicles.find_one({"id": vid}, {"_id": 0})
        return kv_a, kv_b, v1, v2

    kv_a, kv_b, v1, v2 = w.run(lauf())
    assert v1["abgeholt_kaufvorgang_id"] == kv_a and v1["purchase_price"] == PREIS_A
    assert v2["abgeholt_kaufvorgang_id"] == kv_b and v2["purchase_price"] == PREIS_B


def test_08_akte_eines_fremden_fahrzeugs_bleibt_404(welt):
    """Gegenprobe: die Maskierung aendert nichts an der Sichtbarkeit."""
    Bst = _module("routes.bestand")
    w = welt
    vid = f"v_fremd_{w.s}"

    async def lauf():
        await w.db.vehicles.insert_one(w.fahrzeug(vid, owner_user_id=w.chef["id"],
                                                  mitbearbeiter_ids=[]))
        with pytest.raises(HTTPException) as e:
            await Bst.vehicle_akte(vid, w.b)
        return e.value.status_code

    assert w.run(lauf()) == 404


async def _termin_von_b_nach_abholung_von_a(w, vid):
    """B hat einen eigenen Termin zum gemeinsamen Fahrzeug; A hat abgeholt."""
    KV = _module("kaufvorgang")
    Ap = _module("routes.appointments")
    kv_a, kv_b = await _zwei_vorgaenge(w, vid)
    await KV.status_setzen(kv_a, "abgeholt", user=w.a)
    appt_id = f"t_b_{vid}"
    await w.db.appointments.insert_one(
        {"id": appt_id, "dealer_id": w.dealer_id, "vehicle_id": vid,
         "created_by": w.b["id"], "kaufvorgang_id": kv_b, "status": "geplant",
         "created_at": _jetzt()})
    return await Ap.get_appointment(appt_id, user=w.b)


def test_09_termin_detail_mit_zentraler_maskierung_ist_sauber(welt):
    """Gegenpruefung Runde 23: GET /appointments/{id} haengt das volle
    Fahrzeug an (appointments.py get_appointment, ausserhalb dieses Strangs).
    Belegt, dass der vorgeschlagene Einzeiler (einkauf_fuer_sucher_maskieren
    vor a["vehicle"] = v) das Leck schliesst, ohne den Rest zu veraendern."""
    KV = _module("kaufvorgang")
    w = welt
    vid = f"v_termin_{w.s}"

    async def lauf():
        a = await _termin_von_b_nach_abholung_von_a(w, vid)
        v = dict(a["vehicle"])
        await KV.einkauf_fuer_sucher_maskieren(w.b, v)
        return v

    v = w.run(lauf())
    assert _ohne_einkauf(v)
    assert v["id"] == vid and v["data"]["make_label"] == "BMW"


def test_10_termin_detail_liefert_sucher_b_keinen_einkauf_von_a(welt):
    """Gegenpruefung Runde 23: GET /appointments/{id} maskiert das Fahrzeug
    fuer Sucher ebenfalls (routes/appointments.py get_appointment)."""
    w = welt
    vid = f"v_termin2_{w.s}"
    a = w.run(_termin_von_b_nach_abholung_von_a(w, vid))
    assert _ohne_einkauf(a["vehicle"])
