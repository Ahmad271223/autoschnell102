# -*- coding: utf-8 -*-
"""Runde 17 (08.09.2026): bestaetigte Befunde Marktplatz / Team / Haendler-
Einstellungen (Pruefprotokoll Nr. 375-401).

  375  Chef-Abo-Anfrage: dealer_id im Upsert-Schluessel, 409 bei Altanfrage
       einer frueheren Firma
  376  geaenderter Wunsch an einer offenen Anfrage bekommt ein Audit
  377  Logowechsel schreibt Audit (vorher/nachher/persoenlich)
  381  B2B-Preis nur fuer angemeldete Zwischenhaendler (Anzeige, Filter,
       Sortierung, Haendlerseite)
  384/385  Freitext-Filter begrenzt, Seitennummer gedeckelt
  386  Netzwerkliste meldet Abschnitt (X-Truncated) und blaettert (before)
  387  unlesbares valid_until macht das Verkaufspaket ungueltig (fail-closed)
  397  inf/nan als Angebot/Gegenangebot -> 422
  401  Haendlerliste meldet Abschnitt (X-Truncated)

In-Prozess wie Runde 15: Routen-Funktionen direkt mit Fake-`user`-Dicts,
Modul-`db` zeigt auf einen Test-Client (nur Mongo noetig). Query-Defaults
greifen bei direktem Aufruf nicht — die Tests uebergeben alle Filter
explizit und pruefen die Grenzen ueber die Signatur.
"""
import asyncio
import base64
import inspect
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Optional

import pytest
from fastapi import HTTPException, Response
from pydantic import TypeAdapter, ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
DB_NAME = os.environ.get("DB_NAME") or "autoschnell"
MAIL = "e2etest-mail.de"


def _jetzt(delta=None):
    t = datetime.now(timezone.utc)
    if delta is not None:
        t = t + delta
    return t.isoformat()


def _module(name):
    import importlib
    return importlib.import_module(name)


class _Welt:
    def __init__(self):
        s = uuid.uuid4().hex[:10]
        self.s = s
        self.dealer_id = f"d_r17_{s}"
        self.slug = f"r17-{s}"
        self.chef = {"id": f"chef_r17_{s}", "dealer_id": self.dealer_id, "role": "dealer",
                     "email": f"chef_r17_{s}@{MAIL}"}
        self.sucher = {"id": f"su_r17_{s}", "dealer_id": self.dealer_id, "role": "sucher",
                       "first_name": "Su", "last_name": "Cher", "email": f"su_r17_{s}@{MAIL}"}
        # Zwischenhaendler (b2b_buyer): keine eigene Firma
        self.kaeufer = {"id": f"k_r17_{s}", "dealer_id": None, "role": "b2b_buyer",
                        "company_name": "K GmbH", "email": f"k_r17_{s}@{MAIL}"}

    async def firma_anlegen(self, db, public=True):
        """Oeffentliche Firma mit aktivem Chef-Konto (sonst gilt sie als gesperrt).
        dealers.user_id traegt einen Unique-Index — immer setzen."""
        await db.dealers.insert_one({"id": self.dealer_id, "user_id": self.chef["id"],
                                     "company_name": f"R17 Autohaus {self.s}",
                                     "marketplace": {"public": public, "slug": self.slug},
                                     "created_at": _jetzt()})
        await db.users.insert_one({"id": self.chef["id"], "dealer_id": self.dealer_id, "role": "dealer",
                                   "active": True, "email": self.chef["email"], "password_hash": "x",
                                   "created_at": _jetzt()})

    async def sucher_anlegen(self, db):
        await db.users.insert_one({"id": self.sucher["id"], "dealer_id": self.dealer_id, "role": "sucher",
                                   "active": True, "email": self.sucher["email"], "first_name": "Su",
                                   "last_name": "Cher", "password_hash": "x", "created_at": _jetzt()})

    def inserat(self, lid, **prices):
        return {"id": lid, "dealer_id": self.dealer_id, "vehicle_id": f"v_{lid}",
                "title": f"R17 Golf {self.s}", "status": "veroeffentlicht", "visibility": "public",
                "prices": prices, "data": {"make_label": "VW", "model_label": "Golf", "mileage": 1000},
                "photos": {}, "published_at": _jetzt(), "created_at": _jetzt()}

    async def aufraeumen(self, db):
        for c in ("dealers", "users", "resale_listings", "network_members", "buyer_favorites",
                  "activity_logs", "plan_requests", "listing_interest", "subscriptions"):
            await db[c].delete_many({"dealer_id": self.dealer_id})
        await db.dealers.delete_many({"id": {"$regex": f"^{self.dealer_id}"}})
        ids = [self.chef["id"], self.sucher["id"], self.kaeufer["id"]]
        await db.users.delete_many({"id": {"$in": ids}})
        await db.plan_requests.delete_many({"subject_user_id": {"$in": ids}})
        await db.activity_logs.delete_many({"user_id": {"$in": ids}})
        await db.network_members.delete_many({"buyer_user_id": self.kaeufer["id"]})


@pytest.fixture
def welt():
    """Modul-db auf einen frischen Motor-Client des laufenden Loops legen."""
    from motor.motor_asyncio import AsyncIOMotorClient
    w = _Welt()
    module_names = ["deps", "routes.bestand", "routes.dealer", "routes.marketplace", "routes.team"]
    mods = [_module(n) for n in module_names]
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
    yield ctx
    try:
        ctx.run(w.aufraeumen(ctx.db))
    finally:
        for m, d in alt:
            if d is not None:
                m.db = d
        ctx.client.close()
        ctx.loop.close()


async def _index_sucher_abo(db):
    """Teil-Unique-Index wie server.py (_plan_requests_unique_indizes) — die
    CI-Datenbank ist frisch."""
    await db.plan_requests.create_index(
        [("type", 1), ("subject_user_id", 1)], unique=True,
        name="uniq_offene_sucher_abo_anfrage",
        partialFilterExpression={"type": "sucher_abo", "status": "offen"})


async def _index_verkaufspaket(db):
    await db.plan_requests.create_index(
        [("type", 1), ("dealer_id", 1)], unique=True,
        name="uniq_offene_verkaufspaket_anfrage",
        partialFilterExpression={"type": "verkaufspaket", "status": "offen"})


async def _listings(M, user, **k):
    """browse_listings mit allen Filtern explizit (Query-Defaults greifen
    bei direktem Aufruf nicht)."""
    args = dict(q=None, make=None, model=None, fuel=None, price_min=None, price_max=None,
                km_min=None, km_max=None, ps_min=None, ps_max=None, sort=None, dealer=None,
                nur_favoriten=0, page=1, limit=300)
    args.update(k)
    return await M.browse_listings(user=user, **args)


# ================================================= Nr. 381: B2B-Preis nur angemeldet
def test_381_anonym_sieht_oeffentlichen_preis_zwischenhaendler_b2b(welt):
    M = _module("routes.marketplace")
    w, db = welt.w, welt.db
    lid = f"l_{w.s}"

    async def lauf():
        await w.firma_anlegen(db)
        await db.resale_listings.insert_one(w.inserat(lid, public=9900, b2b=9000, network=8000))
        anon = await _listings(M, None, dealer=w.dealer_id)
        kaeufer = await _listings(M, w.kaeufer, dealer=w.dealer_id)
        seite_anon = await M.dealer_page(w.slug, None)
        seite_kaeufer = await M.dealer_page(w.slug, w.kaeufer)
        # Netzwerkmitglied: Netzwerkpreis wie bisher
        await db.network_members.insert_one({"dealer_id": w.dealer_id, "buyer_user_id": w.kaeufer["id"],
                                             "created_at": _jetzt()})
        mitglied = await _listings(M, w.kaeufer, dealer=w.dealer_id)
        return anon, kaeufer, seite_anon, seite_kaeufer, mitglied

    anon, kaeufer, seite_anon, seite_kaeufer, mitglied = welt.run(lauf())
    assert [(l["price"], l["price_level"]) for l in anon if l["id"] == lid] == [(9900, "oeffentlich")]
    assert [(l["price"], l["price_level"]) for l in kaeufer if l["id"] == lid] == [(9000, "b2b")]
    assert [(l["price"], l["price_level"]) for l in seite_anon["listings"]] == [(9900, "oeffentlich")]
    assert [(l["price"], l["price_level"]) for l in seite_kaeufer["listings"]] == [(9000, "b2b")]
    assert [(l["price"], l["price_level"]) for l in mitglied if l["id"] == lid] == [(8000, "netzwerk")]
    for v in (anon, kaeufer, mitglied):
        assert all("prices" not in l for l in v), "rohe Preisstufen duerfen nie nach aussen"
    assert "is_trade = True" not in inspect.getsource(M.browse_listings)
    assert "is_trade=True" not in inspect.getsource(M.dealer_page)


def test_381_preisfilter_und_sortierung_rechnen_anonym_mit_oeffentlichem_preis(welt):
    M = _module("routes.marketplace")
    w, db = welt.w, welt.db
    lid = f"l_{w.s}"

    async def lauf():
        await w.firma_anlegen(db)
        # oeffentlich 9900, B2B 9000: price_max 9500 trifft anonym NICHT
        await db.resale_listings.insert_one(w.inserat(lid, public=9900, b2b=9000))
        anon = await _listings(M, None, dealer=w.dealer_id, price_max=9500, sort="preis_auf")
        kaeufer = await _listings(M, w.kaeufer, dealer=w.dealer_id, price_max=9500, sort="preis_auf")
        anon_min = await _listings(M, None, dealer=w.dealer_id, price_min=9500, sort="preis_ab")
        return anon, kaeufer, anon_min

    anon, kaeufer, anon_min = welt.run(lauf())
    assert [l["id"] for l in anon] == [], "anonym darf der B2B-Preis den Filter nicht treffen"
    assert [l["id"] for l in kaeufer] == [lid]
    assert [l["id"] for l in anon_min] == [lid]


def test_381_b2b_zweig_nur_bei_angemeldetem_nutzer_in_der_pipeline(welt, monkeypatch):
    M = _module("routes.marketplace")
    from motor.motor_asyncio import AsyncIOMotorCollection as K
    w, db = welt.w, welt.db
    pipelines = []
    echt = K.aggregate

    def spion(self, pipeline, *a, **k):
        if self.name == "resale_listings":
            pipelines.append(pipeline)
        return echt(self, pipeline, *a, **k)
    monkeypatch.setattr(K, "aggregate", spion)

    async def lauf():
        await w.firma_anlegen(db)
        await _listings(M, None, dealer=w.dealer_id, sort="preis_auf")
        await _listings(M, w.kaeufer, dealer=w.dealer_id, sort="preis_auf")

    welt.run(lauf())
    assert len(pipelines) == 2

    def zweige(p):
        for st in p:
            if "$addFields" in st and "_eff_price" in st["$addFields"]:
                return st["$addFields"]["_eff_price"]["$switch"]["branches"]
        raise AssertionError("kein _eff_price in der Pipeline")
    assert "$prices.b2b" not in str(zweige(pipelines[0]))
    assert "$prices.b2b" in str(zweige(pipelines[1]))


# ================================================= Nr. 384/385: Filterlaenge, Seitendeckel
def _grenze(param, typ):
    """Grenzwerte (max_length/ge/le) aus dem Query-Default eines Parameters."""
    import annotated_types as at
    klasse = {"max_length": at.MaxLen, "ge": at.Ge, "le": at.Le}[typ]
    return [getattr(m, typ) for m in param.default.metadata if isinstance(m, klasse)]


def test_384_385_freitextfilter_und_seite_in_der_signatur_begrenzt():
    M = _module("routes.marketplace")
    from fastapi.params import Query as QueryParam
    sig = inspect.signature(M.browse_listings)
    for name in ("q", "make", "model", "fuel"):
        p = sig.parameters[name]
        assert isinstance(p.default, QueryParam) and p.default.default is None, name
        assert _grenze(p, "max_length") == [100], name
        # funktional: der Query-Default validiert wie FastAPI es taete
        ta = TypeAdapter(Annotated[Optional[str], p.default])
        assert ta.validate_python("x" * 100) == "x" * 100
        with pytest.raises(ValidationError):
            ta.validate_python("x" * 101)
    seite = sig.parameters["page"]
    assert isinstance(seite.default, QueryParam) and seite.default.default == 1
    assert _grenze(seite, "ge") == [1] and _grenze(seite, "le") == [1000]
    ta = TypeAdapter(Annotated[int, seite.default])
    assert ta.validate_python(1000) == 1000
    for falsch in (0, 1001):
        with pytest.raises(ValidationError):
            ta.validate_python(falsch)
    hq = inspect.signature(M.browse_dealers).parameters["q"]
    assert isinstance(hq.default, QueryParam) and _grenze(hq, "max_length") == [100]
    with pytest.raises(ValidationError):
        TypeAdapter(Annotated[Optional[str], hq.default]).validate_python("x" * 101)


def test_385_seite_wird_auch_bei_direktem_aufruf_gedeckelt(welt, monkeypatch):
    M = _module("routes.marketplace")
    from motor.motor_asyncio import AsyncIOMotorCollection as K
    w, db = welt.w, welt.db
    pipelines = []
    echt = K.aggregate

    def spion(self, pipeline, *a, **k):
        if self.name == "resale_listings":
            pipelines.append(pipeline)
        return echt(self, pipeline, *a, **k)
    monkeypatch.setattr(K, "aggregate", spion)

    async def lauf():
        await w.firma_anlegen(db)
        return await _listings(M, None, dealer=w.dealer_id, page=10 ** 9, limit=300)

    assert welt.run(lauf()) == []
    skips = [st["$skip"] for st in pipelines[-1] if "$skip" in st]
    assert skips == [(1000 - 1) * 300], skips


# ================================================= Nr. 386: Netzwerkliste mit Abschnitt + Cursor
def test_386_netzwerkliste_meldet_abschnitt_und_blaettert(welt):
    M = _module("routes.marketplace")
    w, db = welt.w, welt.db

    async def lauf():
        basis = datetime(2026, 1, 1, tzinfo=timezone.utc)
        await db.network_members.insert_many([
            {"dealer_id": w.dealer_id, "buyer_user_id": f"k_{w.s}_{i}",
             "created_at": (basis + timedelta(seconds=i)).isoformat()}
            for i in range(2001)])
        r1 = Response()
        seite1 = await M.list_network_members(r1, w.chef)
        r2 = Response()
        seite2 = await M.list_network_members(r2, w.chef, before=seite1[-1]["joined_at"])
        await db.network_members.delete_many({"dealer_id": w.dealer_id,
                                              "buyer_user_id": {"$ne": f"k_{w.s}_0"}})
        r3 = Response()
        klein = await M.list_network_members(r3, w.chef)
        return (r1.headers.get("X-Truncated"), seite1, r2.headers.get("X-Truncated"), seite2,
                r3.headers.get("X-Truncated"), klein)

    t1, s1, t2, s2, t3, klein = welt.run(lauf())
    assert t1 == "1" and len(s1) == 2000
    assert s1[0]["buyer_user_id"] == f"k_{w.s}_2000", "neueste zuerst"
    assert s1[-1]["buyer_user_id"] == f"k_{w.s}_1"
    assert t2 == "0" and [m["buyer_user_id"] for m in s2] == [f"k_{w.s}_0"], "Cursor liefert den Rest"
    assert t3 == "0" and len(klein) == 1
    quelle = inspect.getsource(M.list_network_members)
    assert "to_list(2001)" in quelle and "to_list(2000)" not in quelle
    assert inspect.signature(M.list_network_members).parameters["response"].annotation is Response


# ================================================= Nr. 397: inf/nan im Angebot
@pytest.mark.parametrize("wert", [float("inf"), float("-inf"), float("nan"), "Infinity", "nan", "1e999"])
def test_397_angebote_lehnen_inf_nan_ab(wert):
    M = _module("routes.marketplace")
    with pytest.raises(ValidationError):
        M.InterestIn(offer=wert)
    with pytest.raises(ValidationError):
        M.InterestAnswerIn(action="gegenangebot", counter_offer=wert)
    with pytest.raises(ValidationError):
        M.BuyerInterestAnswerIn(action="gegenangebot", counter_offer=wert)


def test_397_normale_angebote_bleiben_und_ge0_gilt_weiter():
    M = _module("routes.marketplace")
    assert M.InterestIn(offer=12000.5).offer == 12000.5
    assert M.InterestIn().offer is None
    assert M.InterestAnswerIn(action="ablehnen").counter_offer is None
    assert M.BuyerInterestAnswerIn(action="gegenangebot", counter_offer=1).counter_offer == 1.0
    for modell, kw in ((M.InterestIn, {}), (M.InterestAnswerIn, {"action": "gegenangebot"}),
                       (M.BuyerInterestAnswerIn, {"action": "gegenangebot"})):
        feld = "offer" if modell is M.InterestIn else "counter_offer"
        with pytest.raises(ValidationError):
            modell(**kw, **{feld: -1})


# ================================================= Nr. 401: Haendlerliste mit Abschnitt
def test_401_haendlerliste_meldet_abschnitt(welt):
    M = _module("routes.marketplace")
    w, db = welt.w, welt.db
    name = f"R17 Markt {w.s}"

    async def lauf():
        await db.dealers.insert_many([
            {"id": f"{w.dealer_id}_m{i}", "user_id": f"u_{w.dealer_id}_m{i}",
             "company_name": f"{name} Nr {i}",
             "marketplace": {"public": True}, "created_at": _jetzt()}
            for i in range(1001)])
        r1 = Response()
        voll = await M.browse_dealers(r1, q=name, user=None)
        await db.dealers.delete_many({"id": {"$in": [f"{w.dealer_id}_m{i}" for i in range(3, 1001)]}})
        r2 = Response()
        klein = await M.browse_dealers(r2, q=name, user=None)
        return r1.headers.get("X-Truncated"), voll, r2.headers.get("X-Truncated"), klein

    t1, voll, t2, klein = welt.run(lauf())
    assert t1 == "1" and len(voll) == 1000
    assert t2 == "0" and len(klein) == 3
    assert all(d["company_name"].startswith(name) for d in voll + klein)
    quelle = inspect.getsource(M.browse_dealers)
    assert "to_list(1001)" in quelle and "to_list(1000)" not in quelle


# ================================================= Nr. 375: Chef-Abo-Anfrage nur eigene Firma
def test_375_chef_abo_anfrage_aendert_keine_altanfrage_anderer_firma(welt):
    T = _module("routes.team")
    w, db = welt.w, welt.db

    async def lauf():
        await _index_sucher_abo(db)
        await w.firma_anlegen(db)
        await w.sucher_anlegen(db)
        # Altanfrage unter einer ANDEREN Firma (nur per DB-Eingriff erreichbar)
        await db.plan_requests.insert_one(
            {"id": f"alt_{w.s}", "type": "sucher_abo", "subject_user_id": w.sucher["id"],
             "dealer_id": f"alte_firma_{w.s}", "status": "offen", "wanted_plan": "monthly",
             "price": 1, "created_at": _jetzt()})
        with pytest.raises(HTTPException) as e:
            await T.sucher_abo_request(w.sucher["id"], {"plan": "yearly"}, w.chef)
        alt = await db.plan_requests.find_one({"id": f"alt_{w.s}"}, {"_id": 0})
        n = await db.plan_requests.count_documents({"subject_user_id": w.sucher["id"]})
        logs = await db.activity_logs.count_documents({"dealer_id": w.dealer_id})
        return e.value, alt, n, logs

    exc, alt, n, logs = welt.run(lauf())
    assert exc.status_code == 409 and "Betreiber" in exc.detail
    assert n == 1 and logs == 0
    assert alt["wanted_plan"] == "monthly" and alt["dealer_id"] == f"alte_firma_{w.s}", \
        "Altanfrage der anderen Firma darf nicht veraendert werden"
    q = inspect.getsource(T.sucher_abo_request)
    i = q.index('"type": "sucher_abo", "subject_user_id": sucher_id')
    assert '"dealer_id": user["dealer_id"], "status": "offen"' in q[i:i + 160], "dealer_id im Schluessel"
    assert "DuplicateKeyError" in q


# ================================================= Nr. 376: Audit bei geaenderter Anfrage
def test_375_376_chef_abo_anfrage_neu_dann_geaendert_mit_audit(welt):
    T = _module("routes.team")
    w, db = welt.w, welt.db

    async def lauf():
        await _index_sucher_abo(db)
        await w.firma_anlegen(db)
        await w.sucher_anlegen(db)
        r1 = await T.sucher_abo_request(w.sucher["id"], {"plan": "monthly"}, w.chef)
        r2 = await T.sucher_abo_request(w.sucher["id"], {"plan": "yearly"}, w.chef)
        doc = await db.plan_requests.find_one({"id": r1["request_id"]}, {"_id": 0})
        logs = await db.activity_logs.find({"dealer_id": w.dealer_id}, {"_id": 0}).to_list(10)
        return r1, r2, doc, logs

    r1, r2, doc, logs = welt.run(lauf())
    assert not r1.get("bereits_offen") and r2["bereits_offen"] is True
    assert r2["request_id"] == r1["request_id"]
    assert doc["dealer_id"] == w.dealer_id and doc["wanted_plan"] == "yearly"
    je = {l["action"]: l for l in logs}
    assert set(je) == {"sucher.abo.anfrage", "sucher.abo.anfrage.geaendert"}
    g = je["sucher.abo.anfrage.geaendert"]
    assert g["ref"] == r1["request_id"] and g["user_id"] == w.chef["id"]
    assert g["meta"] == {"sucher": w.sucher["id"], "plan": "yearly"}


def test_376_eigene_abo_anfrage_geaendert_mit_audit(welt):
    T = _module("routes.team")
    w, db = welt.w, welt.db

    async def lauf():
        await _index_sucher_abo(db)
        await w.firma_anlegen(db)
        r1 = await T.eigenes_abo_anfrage({"plan": "monthly"}, w.chef)
        r2 = await T.eigenes_abo_anfrage({"plan": "yearly"}, w.chef)
        log = await db.activity_logs.find_one(
            {"dealer_id": w.dealer_id, "action": "abo.anfrage.selbst.geaendert"}, {"_id": 0})
        doc = await db.plan_requests.find_one({"id": r1["request_id"]}, {"_id": 0})
        return r1, r2, log, doc

    r1, r2, log, doc = welt.run(lauf())
    assert r2["bereits_offen"] is True and r2["request_id"] == r1["request_id"]
    assert doc["wanted_plan"] == "yearly"
    assert log and log["ref"] == r1["request_id"] and log["user_id"] == w.chef["id"]
    assert log["meta"] == {"plan": "yearly"}


def test_376_verkaufspaket_anfrage_geaendert_mit_audit(welt, monkeypatch):
    T = _module("routes.team")
    w, db = welt.w, welt.db
    monkeypatch.setattr(T, "VERKAUF_KOSTENLOS", False)

    async def lauf():
        await _index_verkaufspaket(db)
        await w.firma_anlegen(db)
        r1 = await T.sale_plan_upgrade_request(T.UpgradeRequestIn(wanted_tier="s10"), w.chef)
        r2 = await T.sale_plan_upgrade_request(
            T.UpgradeRequestIn(wanted_tier="s20", message="mehr"), w.chef)
        log = await db.activity_logs.find_one(
            {"dealer_id": w.dealer_id, "action": "verkaufsplan.anfrage.geaendert"}, {"_id": 0})
        doc = await db.plan_requests.find_one({"id": r1["request_id"]}, {"_id": 0})
        return r1, r2, log, doc

    r1, r2, log, doc = welt.run(lauf())
    assert r2["bereits_offen"] is True and r2["request_id"] == r1["request_id"]
    assert doc["wanted_tier"] == "s20" and doc["message"] == "mehr"
    assert log and log["ref"] == r1["request_id"] and log["meta"] == {"wunsch": "s20"}


def test_376_alle_drei_upserts_loggen_im_bereits_offen_zweig():
    T = _module("routes.team")
    for fn, aktion in ((T.sale_plan_upgrade_request, "verkaufsplan.anfrage.geaendert"),
                       (T.eigenes_abo_anfrage, "abo.anfrage.selbst.geaendert"),
                       (T.sucher_abo_request, "sucher.abo.anfrage.geaendert")):
        q = inspect.getsource(fn)
        i = q.index("if not neu:")
        block = q[i:q.index("return {", i)]
        assert aktion in block and "log_activity" in block, fn.__name__


# ================================================= Nr. 387: valid_until fail-closed
def test_387_unlesbares_valid_until_macht_paket_ungueltig(welt, monkeypatch):
    T = _module("routes.team")
    w, db = welt.w, welt.db
    monkeypatch.setattr(T, "VERKAUF_KOSTENLOS", False)
    meldungen = []
    monkeypatch.setattr(T.log, "error", lambda msg, *a, **k: meldungen.append(msg % a))

    async def lauf():
        await db.dealers.insert_one({"id": w.dealer_id, "user_id": w.chef["id"], "company_name": "R17",
                                     "sale_plan": {"tier": "s5", "period_start": _jetzt(),
                                                   "valid_until": "kaputt"},
                                     "quota_usage": {}})
        kaputt = await T.get_sale_plan_status(w.dealer_id)
        await db.dealers.update_one({"id": w.dealer_id}, {"$set": {"sale_plan.valid_until": 12345}})
        zahl = await T.get_sale_plan_status(w.dealer_id)
        await db.dealers.update_one({"id": w.dealer_id},
                                    {"$set": {"sale_plan.valid_until": _jetzt(timedelta(days=10))}})
        gesund = await T.get_sale_plan_status(w.dealer_id)
        await db.dealers.update_one({"id": w.dealer_id},
                                    {"$set": {"sale_plan.valid_until": _jetzt(timedelta(days=-1))}})
        abgelaufen = await T.get_sale_plan_status(w.dealer_id)
        return kaputt, zahl, gesund, abgelaufen

    kaputt, zahl, gesund, abgelaufen = welt.run(lauf())
    for st in (kaputt, zahl):
        assert st["active"] is False and st["tier"] == "s5"
        assert st["quota"] == 0 and st["remaining"] == 0 and st["used"] == 0
        assert "ungueltig" in st["fehler"] and "Betreiber" in st["fehler"]
        assert st.get("expired") is not True
    assert kaputt["valid_until"] == "kaputt" and zahl["valid_until"] == 12345
    assert len(meldungen) == 2 and all("valid_until" in m and w.dealer_id in m for m in meldungen)
    # gesunder und abgelaufener Datensatz wie bisher
    assert gesund["active"] is True and gesund["quota"] == 5 and "fehler" not in gesund
    assert abgelaufen["active"] is False and abgelaufen["expired"] is True
    q = inspect.getsource(T.get_sale_plan_status)
    i = q.index("vu = datetime.fromisoformat(valid_until)")
    block = q[i:q.index("if vu.tzinfo", i)]
    assert "except (TypeError, ValueError)" in block and "log.error" in block
    assert not any(z.strip() == "pass" for z in block.splitlines()), "except: pass ist weg"
    # Admin-Verlaengerungspfad rechnet mit eigenem Parser, nicht ueber den Status
    A = _module("routes.admin")
    assert "get_sale_plan_status" not in inspect.getsource(A)


# ================================================= Nr. 377: Logo-Audit
@pytest.fixture
def storage_attrappe(monkeypatch):
    import storage_service as ss
    protokoll = {"gespeichert": [], "geloescht": []}

    async def save_async(key, data):
        protokoll["gespeichert"].append(key)
        return key

    async def loeschen(db, *, key=None, prefix=None, grund, dealer_id="", ref=None, art="storage"):
        protokoll["geloescht"].append((key, grund))
        return True

    monkeypatch.setattr(ss, "save_async", save_async)
    monkeypatch.setattr(ss, "loeschen_oder_vormerken", loeschen)
    monkeypatch.setattr(ss, "validate_image_bytes", lambda raw, wo="": None)
    monkeypatch.setattr(ss, "bild_verkleinern", lambda raw, wo, fmt: raw)
    return protokoll


def test_377_logo_wechsel_schreibt_audit_fuer_chef_und_sucher(welt, storage_attrappe):
    Dl = _module("routes.dealer")
    w, db = welt.w, welt.db
    alt_url = f"/api/files/logo/{w.dealer_id}/alt.png"
    body = Dl.LogoUploadIn(logo_b64="data:image/png;base64,"
                           + base64.b64encode(b"\x89PNG-fake").decode())

    async def lauf():
        await db.dealers.insert_one({"id": w.dealer_id, "user_id": w.chef["id"], "company_name": "R17",
                                     "logo_url": alt_url})
        await w.sucher_anlegen(db)
        r_chef = await Dl.upload_logo(body, w.chef)
        log_chef = await db.activity_logs.find_one(
            {"dealer_id": w.dealer_id, "user_id": w.chef["id"],
             "action": "einstellungen.logo.geaendert"}, {"_id": 0})
        sucher = {**w.sucher, "settings_override": {}}
        r_sucher = await Dl.upload_logo(body, sucher)
        log_sucher = await db.activity_logs.find_one(
            {"dealer_id": w.dealer_id, "user_id": w.sucher["id"],
             "action": "einstellungen.logo.geaendert"}, {"_id": 0})
        firma = await db.dealers.find_one({"id": w.dealer_id}, {"_id": 0, "logo_url": 1})
        konto = await db.users.find_one({"id": w.sucher["id"]}, {"_id": 0, "settings_override": 1})
        return r_chef, log_chef, r_sucher, log_sucher, firma, konto

    r_chef, log_chef, r_sucher, log_sucher, firma, konto = welt.run(lauf())
    assert r_chef["ok"] and r_chef["logo_url"].startswith(f"/api/files/logo/{w.dealer_id}/")
    assert log_chef and log_chef["meta"] == {"vorher": alt_url, "nachher": r_chef["logo_url"],
                                             "persoenlich": False}
    assert firma["logo_url"] == r_chef["logo_url"]
    assert log_sucher and log_sucher["meta"] == {"vorher": None, "nachher": r_sucher["logo_url"],
                                                 "persoenlich": True}
    assert konto["settings_override"]["logo_url"] == r_sucher["logo_url"]
    assert r_sucher["logo_url"] != r_chef["logo_url"]
    # Verhalten von Runde 14 (Nr. 96) unveraendert: altes Logo weggeraeumt
    assert storage_attrappe["geloescht"] == [(alt_url[len("/api/files/"):], "logo_ersetzt")]


def test_377_kein_audit_wenn_der_logo_upload_scheitert(welt, storage_attrappe, monkeypatch):
    Dl = _module("routes.dealer")
    from motor.motor_asyncio import AsyncIOMotorCollection as K
    w, db = welt.w, welt.db
    body = Dl.LogoUploadIn(logo_b64="data:image/png;base64,"
                           + base64.b64encode(b"\x89PNG-fake").decode())
    echt = K.update_one

    async def kaputt(self, *a, **k):
        if self.name == "dealers":
            raise RuntimeError("mongo weg")
        return await echt(self, *a, **k)
    monkeypatch.setattr(K, "update_one", kaputt)

    async def lauf():
        await db.dealers.insert_one({"id": w.dealer_id, "user_id": w.chef["id"], "company_name": "R17",
                                     "logo_url": None})
        with pytest.raises(HTTPException) as e:
            await Dl.upload_logo(body, w.chef)
        n = await db.activity_logs.count_documents({"dealer_id": w.dealer_id})
        return e.value.status_code, n

    status, n = welt.run(lauf())
    assert status == 500 and n == 0
    assert [g for _, g in storage_attrappe["geloescht"]] == ["logo_upload_abbruch"]
