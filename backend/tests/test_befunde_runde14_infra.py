# -*- coding: utf-8 -*-
"""Nachpruefung Runde 14 (07.09.2026), Gruppe "infra".

  59  Stripe-Freischaltung je Kaeufer serialisiert (Job-Sperre) + CAS auf
      den gelesenen Ablauf: zwei parallele Zahlungen = +60 Tage.
  60  Unique-Index zugang_grants.session_id.
  61  Unique-Index storage_delete_retry (art, key, prefix) + Normalisierung
      des Altbestands; Teil-Unique-Indizes plan_requests (Nr. 56/57).
  10  GET /snapshots/{id}: fremde Firma sieht keine Ersteller-Metadaten.
  11  /listings/check/{id}: vor_dir zaehlt nur eigene Jobs.
  15/13/14  cleanup_service.firmenreste_bereinigen: link_jobs,
      listings_cache_client, listings_cache-Metadaten geloeschter Firmen;
      Promotion kopiert keine ingested_by_*-Felder.
  23  _protokolle_pii_entfernen leert `place`; Nachlauf fuer Altbestand.
 100  CLEANUP_RULES kennt "erledigt"; Frist ab abgeschlossen_seit.
  85  current_firma: 403 ohne dealers-Dokument.
  86  /mobile/compare zaehlt den Rueckfall erst nach dem Cache-Check.
  97  production_check warnt in Produktion bei inaktiver Vertragsloeschung;
      Trockenlauf mit Kandidaten loest einen Betriebsalarm aus.
   4  _mobile_datenblatt_job: letzter Fallback mandantengebunden.
  25  dateien.py rechnet mit auth.JWT_SECRET.

Einheitentests brauchen nur Mongo (MONGO_URL/DB_NAME). HTTP-Tests laufen
nur mit RUNDE14_HTTP=1 gegen das NEU gestartete Backend (TEST_BASE_URL).
"""
import asyncio
import inspect
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

HTTP = os.environ.get("RUNDE14_HTTP") == "1"
# Runde 21 (Pruefbefund C): klarer Skip-Grund statt "HTTP nach Neustart" —
# die CI setzt RUNDE14_HTTP=1 im Schritt "Selbsttest-Suite".
HTTP_GRUND = ("RUNDE14_HTTP=1 nicht gesetzt — HTTP-Test braucht ein laufendes "
              "Backend auf TEST_BASE_URL (CI: Schritt Selbsttest-Suite)")
BASE = (os.environ.get("TEST_BASE_URL") or "http://localhost:8001").rstrip("/")
API = f"{BASE}/api"
MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
DB_NAME = os.environ.get("DB_NAME") or "autoschnell"
SUF = uuid.uuid4().hex[:8]
PW = "RundeVierzehn123!"
MAIL = "e2etest-mail.de"
JETZT = datetime.now(timezone.utc)

# deps.py liest MONGO_URL/DB_NAME beim Import — fuer Funktionstests ohne
# laufenden Server dieselben Werte vorgeben (falls nicht gesetzt).
os.environ.setdefault("MONGO_URL", MONGO_URL)
os.environ.setdefault("DB_NAME", DB_NAME)


def _db():
    from pymongo import MongoClient
    return MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)[DB_NAME]


def _run(coro_factory):
    async def _inner():
        from motor.motor_asyncio import AsyncIOMotorClient
        cl = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
        try:
            return await coro_factory(cl[DB_NAME])
        finally:
            cl.close()
    return asyncio.run(_inner())


def _iso(dt):
    return dt.isoformat()


# =====================================================================
#                 Nr. 59 — Freischaltung rennfest (unit)
# =====================================================================
def _kaeufer(db_sync, uid, ablauf):
    db_sync.users.insert_one({
        "id": uid, "email": f"{uid}@{MAIL}", "role": "b2b_buyer", "active": True,
        "created_at": _iso(JETZT),
        "marketplace_access": {"active": True, "plan": "monthly",
                               "expires_at": ablauf}})


def _tx(db_sync, uid, sid):
    db_sync.payment_transactions.insert_one({
        "id": str(uuid.uuid4()), "session_id": sid, "user_id": uid,
        "dealer_id": None, "plan": "marktplatz", "amount": 20.0, "currency": "eur",
        "status": "paid", "payment_status": "paid",
        "created_at": _iso(JETZT), "updated_at": _iso(JETZT)})
    return db_sync.payment_transactions.find_one({"session_id": sid}, {"_id": 0})


def _race_aufraeumen(uid, sids):
    d = _db()
    d.users.delete_many({"id": uid})
    d.payment_transactions.delete_many({"session_id": {"$in": sids}})
    d.zugang_grants.delete_many({"session_id": {"$in": sids}})
    d.manual_payments.delete_many({"zahlung_ref": {"$in": sids}})
    d.betriebsalarme.delete_many({"ref": {"$in": sids}})
    d.job_locks.delete_many({"name": f"zugang_freischalten:{uid}"})


def test_59_zwei_parallele_zahlungen_ergeben_60_tage():
    uid = f"r14_buyer_{SUF}"
    sa, sb = f"cs_r14a_{SUF}", f"cs_r14b_{SUF}"
    ablauf = (JETZT + timedelta(days=24)).replace(microsecond=0)
    d = _db()
    _kaeufer(d, uid, _iso(ablauf))
    txa, txb = _tx(d, uid, sa), _tx(d, uid, sb)

    async def lauf(db):
        import job_lock
        import routes.payments as p
        alt = p.db
        p.db = db
        try:
            await job_lock.ensure_lock_index(db)
            erg = await asyncio.gather(p._activate_paid_transaction(txa, sa),
                                       p._activate_paid_transaction(txb, sb))
            assert erg == [True, True], erg
            u = await db.users.find_one({"id": uid}, {"_id": 0})
            acc = u["marketplace_access"]
            erwartet = (ablauf + timedelta(days=60)).isoformat()
            assert acc["expires_at"] == erwartet, (acc, erwartet)
            grants = await db.zugang_grants.find(
                {"session_id": {"$in": [sa, sb]}}, {"_id": 0}).to_list(10)
            assert len(grants) == 2
            assert len({g["basis"] for g in grants}) == 2, \
                "beide Grants mit derselben Basis = nur einmal verlaengert"
            assert sorted(g["expires_at"] for g in grants) == \
                [(ablauf + timedelta(days=30)).isoformat(), erwartet]
            txs = await db.payment_transactions.find(
                {"session_id": {"$in": [sa, sb]}}, {"_id": 0}).to_list(10)
            assert {t["status"] for t in txs} == {"active"}, txs
            # Wiederholungslauf derselben Session verlaengert NICHT erneut
            assert await p._zugang_freischalten(txa, sa) == erwartet
            u2 = await db.users.find_one({"id": uid}, {"_id": 0})
            assert u2["marketplace_access"]["expires_at"] == erwartet
            assert await db.zugang_grants.count_documents(
                {"session_id": {"$in": [sa, sb]}}) == 2
            # Sperre ist wieder frei
            assert await job_lock.acquire(db, f"zugang_freischalten:{uid}", 5)
            await job_lock.release(db, f"zugang_freischalten:{uid}")
        finally:
            p.db = alt

    try:
        _run(lauf)
    finally:
        _race_aufraeumen(uid, [sa, sb])


def test_59_cas_backstop_verwirft_veralteten_stand_und_eigenen_grant():
    """Faellt die Sperre aus, faengt der Compare-and-Swap den veralteten
    Lesestand ab: nichts geschrieben, frischer Grant wieder entfernt, damit
    der Reparaturlauf die Basis neu berechnet (statt den alten Grant-Wert
    zu schreiben und Laufzeit zu verlieren)."""
    uid = f"r14_cas_{SUF}"
    sid = f"cs_r14cas_{SUF}"
    echt = (JETZT + timedelta(days=40)).replace(microsecond=0)
    d = _db()
    _kaeufer(d, uid, _iso(echt))
    tx = _tx(d, uid, sid)

    class _UsersVeraltet:
        """users.find_one liefert einen aelteren Ablauf als in der DB."""
        def __init__(self, coll):
            self._c = coll

        async def find_one(self, *a, **k):
            doc = await self._c.find_one(*a, **k)
            if doc and doc.get("marketplace_access"):
                doc["marketplace_access"]["expires_at"] = \
                    (echt - timedelta(days=10)).isoformat()
            return doc

        def __getattr__(self, name):
            return getattr(self._c, name)

    class _DbVeraltet:
        def __init__(self, db):
            self._db = db
            self.users = _UsersVeraltet(db.users)

        def __getattr__(self, name):
            return getattr(self._db, name)

    async def lauf(db):
        import job_lock
        import routes.payments as p
        alt = p.db
        p.db = _DbVeraltet(db)
        try:
            await job_lock.ensure_lock_index(db)
            with pytest.raises(RuntimeError):
                await p._zugang_freischalten(tx, sid)
            u = await db.users.find_one({"id": uid}, {"_id": 0})
            assert u["marketplace_access"]["expires_at"] == _iso(echt), \
                "veralteter Lesestand darf nicht geschrieben werden"
            assert await db.zugang_grants.count_documents({"session_id": sid}) == 0, \
                "eigener Grant mit veralteter Basis muss wieder weg sein"
            # Sperre freigegeben (finally)
            assert await job_lock.acquire(db, f"zugang_freischalten:{uid}", 5)
            await job_lock.release(db, f"zugang_freischalten:{uid}")
        finally:
            p.db = alt

    try:
        _run(lauf)
    finally:
        _race_aufraeumen(uid, [sid])


# =====================================================================
#            Nr. 60 / 61 — Unique-Indizes (unit ueber server.py)
# =====================================================================
def _index_namen(sync_coll):
    return set(sync_coll.index_information().keys())


def test_60_61_indizes_werden_idempotent_angelegt():
    async def lauf(db):
        import server
        alt = server.db
        server.db = db
        try:
            # Altbestand wie aus admin.py (ohne art/key) + Dublette desselben Ziels
            praefix = f"resale/d_r14_{SUF}/"
            await db.storage_delete_retry.insert_many([
                {"id": str(uuid.uuid4()), "prefix": praefix, "dealer_id": "x",
                 "created_at": "2026-01-01T00:00:00+00:00"},
                {"id": str(uuid.uuid4()), "art": "prefix", "key": None,
                 "prefix": praefix, "versuche": 2,
                 "created_at": "2026-01-02T00:00:00+00:00"},
            ])
            for _ in range(2):                       # idempotent
                await server._storage_retry_unique_index()
                await server._plan_requests_unique_indizes()
                await db.zugang_grants.create_index("session_id", unique=True,
                                                    name="grant_je_session")
            reste = await db.storage_delete_retry.find(
                {"prefix": praefix}, {"_id": 0}).to_list(10)
            assert len(reste) == 1 and reste[0]["art"] == "prefix" \
                and reste[0]["key"] is None, reste
            assert reste[0]["created_at"].startswith("2026-01-01"), \
                "aelteste Zeile bleibt"
        finally:
            server.db = alt
            await db.storage_delete_retry.delete_many({"prefix": praefix})

    _run(lauf)
    d = _db()
    assert "retry_je_ziel" in _index_namen(d.storage_delete_retry)
    assert "grant_je_session" in _index_namen(d.zugang_grants)
    pr = d.plan_requests.index_information()
    for name, typ in (("uniq_offene_sucher_abo_anfrage", "sucher_abo"),
                      ("uniq_offene_verkaufspaket_anfrage", "verkaufspaket")):
        assert name in pr, (name, pr.keys())
        assert pr[name].get("unique") is True
        assert pr[name].get("partialFilterExpression") == {"type": typ, "status": "offen"}


def test_60_parallele_grant_upserts_ergeben_genau_einen():
    sid = f"cs_r14_par_{SUF}"

    async def lauf(db):
        from pymongo import ReturnDocument
        await db.zugang_grants.create_index("session_id", unique=True,
                                            name="grant_je_session")

        async def upsert(i):
            return await db.zugang_grants.find_one_and_update(
                {"session_id": sid},
                {"$setOnInsert": {"id": str(uuid.uuid4()), "session_id": sid,
                                  "user_id": "u", "basis": str(i)}},
                upsert=True, return_document=ReturnDocument.AFTER)
        docs = await asyncio.gather(*[upsert(i) for i in range(40)])
        assert len({d["id"] for d in docs}) == 1, "alle sehen denselben Grant"
        assert await db.zugang_grants.count_documents({"session_id": sid}) == 1

    try:
        _run(lauf)
    finally:
        _db().zugang_grants.delete_many({"session_id": sid})


def test_61_parallele_vormerkungen_ergeben_einen_eintrag(monkeypatch):
    key = f"resale/d_r14_{SUF}/kaputt.jpg"

    async def lauf(db):
        import server
        import storage_service as ss
        alt = server.db
        server.db = db
        try:
            await server._storage_retry_unique_index()
        finally:
            server.db = alt

        async def kaputt(_key):
            raise RuntimeError("Storage weg")
        monkeypatch.setattr(ss, "delete_async", kaputt)
        erg = await asyncio.gather(*[
            ss.loeschen_oder_vormerken(db, key=key, grund="r14-test", dealer_id="d")
            for _ in range(40)])
        assert erg == [False] * 40
        assert await db.storage_delete_retry.count_documents({"key": key}) == 1

    try:
        _run(lauf)
    finally:
        _db().storage_delete_retry.delete_many({"key": key})


# =====================================================================
#      Nr. 15/13/14 — Firmenreste + Promotion ohne Herkunft (unit)
# =====================================================================
def test_15_13_14_firmenreste_bereinigen():
    weg = f"d_r14_weg_{SUF}"           # existiert nicht in dealers
    da = f"d_r14_da_{SUF}"             # existiert
    ck = f"kleinanzeigen:r14{SUF}"

    async def lauf(db):
        import cleanup_service as cs
        await db.dealers.insert_one({"id": da, "company_name": "Da GmbH",
                                     "created_at": _iso(JETZT)})
        await db.link_jobs.insert_one({
            "id": f"job_r14_{SUF}", "cache_key": ck, "status": "completed",
            "active": False, "requested_by_dealer": weg, "dealer_ids": [weg, da],
            "created_at": JETZT, "finished_at": JETZT})
        await db.listings_cache_client.insert_many([
            {"cache_key": ck, "dealer_id": weg, "data": {"ingested_by_dealer": weg},
             "expires_at": JETZT + timedelta(hours=1)},
            {"cache_key": ck, "dealer_id": da, "data": {},
             "expires_at": JETZT + timedelta(hours=1)}])
        await db.listings_cache.insert_one({
            "cache_key": ck, "source": "kleinanzeigen", "item_id": f"r14{SUF}",
            "data": {"title": "T", "ingested_by_user": "u", "ingested_by_dealer": weg},
            "confirmed_by": [weg, da], "expires_at": JETZT + timedelta(hours=1)})
        try:
            n = await cs.firmenreste_bereinigen(db)
            assert n >= 4, n
            job = await db.link_jobs.find_one({"id": f"job_r14_{SUF}"}, {"_id": 0})
            assert job["dealer_ids"] == [da] and job["requested_by_dealer"] == ""
            assert await db.listings_cache_client.count_documents({"dealer_id": weg}) == 0
            assert await db.listings_cache_client.count_documents({"dealer_id": da}) == 1
            lc = await db.listings_cache.find_one({"cache_key": ck}, {"_id": 0})
            assert lc["confirmed_by"] == [da]
            assert "ingested_by_dealer" not in lc["data"] \
                and "ingested_by_user" not in lc["data"], lc["data"]
            assert lc["data"]["title"] == "T"
            # zweiter Lauf: nichts mehr zu tun
            assert await cs.firmenreste_bereinigen(db) == 0
        finally:
            await db.dealers.delete_many({"id": da})
            await db.link_jobs.delete_many({"cache_key": ck})
            await db.listings_cache_client.delete_many({"cache_key": ck})
            await db.listings_cache.delete_many({"cache_key": ck})

    _run(lauf)


def test_14_promotion_kopiert_keine_herkunftsfelder(monkeypatch):
    monkeypatch.setenv("CLIENT_INGEST_PROMOTE_MIN_DEALERS", "3")
    url = f"https://www.kleinanzeigen.de/s-anzeige/r14-promo/98{uuid.uuid4().int % 10**8:08d}-216-1"
    firmen = [f"d_r14_p{i}_{SUF}" for i in range(3)]

    async def lauf(db):
        from listing_identity import get_listing_identity, store_client_listing
        ck = get_listing_identity(url)["cache_key"]
        try:
            stati = []
            for i, f in enumerate(firmen):
                daten = {"title": "Golf", "list_price": 9990, "mileage": 100000,
                         "first_registration": "2018", "make_label": "VW",
                         "ingested_by_user": f"u{i}", "ingested_by_dealer": f}
                stati.append(await store_client_listing(db, url, daten, f))
            assert stati[-1] == "promoted", stati
            lc = await db.listings_cache.find_one({"cache_key": ck}, {"_id": 0})
            assert lc and lc.get("client_confirmed") is True
            assert not any(k.startswith("ingested_by_") for k in lc["data"]), lc["data"]
            assert lc["data"]["title"] == "Golf"
            # Quarantaene behaelt die Herkunft (Missbrauchs-Nachvollziehbarkeit)
            q = await db.listings_cache_client.find_one(
                {"cache_key": ck, "dealer_id": firmen[0]}, {"_id": 0})
            assert q["data"]["ingested_by_dealer"] == firmen[0]
        finally:
            await db.listings_cache.delete_many({"cache_key": ck})
            await db.listings_cache_client.delete_many({"cache_key": ck})

    _run(lauf)


# =====================================================================
#                 Nr. 23 / 100 — Protokoll-Ort, erledigt (unit)
# =====================================================================
def test_23_protokoll_ort_wird_geleert_und_altbestand_nachgezogen():
    termin = f"a_r14_{SUF}"
    dealer = f"d_r14_{SUF}"

    async def lauf(db):
        import cleanup_service as cs
        await db.pickup_protocols.insert_many([
            {"id": f"p_r14_neu_{SUF}", "appointment_id": termin, "dealer_id": dealer,
             "seller_name": "Max Muster", "place": "Wohnadresse Verkaeufer, Hannover",
             "superseded": True, "version": 1},
            # Altbestand: schon "bereinigt", Ort blieb stehen
            {"id": f"p_r14_alt_{SUF}", "appointment_id": f"{termin}_alt",
             "dealer_id": dealer, "seller_name": "", "place": "Hannover",
             "pii_geloescht_at": "2026-08-01T00:00:00+00:00",
             "superseded": True, "version": 1},
        ])
        try:
            await cs._protokolle_pii_entfernen(db, [termin], dealer, _iso(JETZT))
            p = await db.pickup_protocols.find_one({"id": f"p_r14_neu_{SUF}"}, {"_id": 0})
            assert p["place"] == "" and p["seller_name"] == "" and p["pii_geloescht_at"]
            assert await cs.protokoll_orte_nachziehen(db) >= 1
            alt = await db.pickup_protocols.find_one({"id": f"p_r14_alt_{SUF}"}, {"_id": 0})
            assert alt["place"] == ""
        finally:
            await db.pickup_protocols.delete_many({"dealer_id": dealer})

    _run(lauf)


def test_100_cleanup_regeln_kennen_erledigt_und_abschlusszeit():
    import cleanup_service as cs
    regeln = dict(cs.CLEANUP_RULES)
    assert regeln.get("erledigt") == 7, regeln
    assert regeln.get("abgeholt") == 7 and regeln.get("nicht abgeholt") == 14
    src = inspect.getsource(cs._cleanup_once)
    assert "abgeschlossen_seit" in src and "status_changed_at" in src


def test_100_kandidatenabfrage_findet_erledigt_ueber_beide_zeitfelder():
    """Dieselbe Abfrage wie in _cleanup_once, gegen echte Dokumente."""
    dealer = f"d_r14_100_{SUF}"
    alt = _iso(JETZT - timedelta(days=15))
    frisch = _iso(JETZT - timedelta(days=1))

    async def lauf(db):
        import cleanup_service as cs
        docs = [
            {"id": f"a_r14_e1_{SUF}", "dealer_id": dealer, "status": "erledigt",
             "status_changed_at": alt},                                     # treffen
            {"id": f"a_r14_e2_{SUF}", "dealer_id": dealer, "status": "erledigt",
             "abgeschlossen_seit": alt, "status_changed_at": frisch},       # treffen
            {"id": f"a_r14_e3_{SUF}", "dealer_id": dealer, "status": "erledigt",
             "abgeschlossen_seit": frisch, "status_changed_at": alt},       # nicht
            {"id": f"a_r14_e4_{SUF}", "dealer_id": dealer, "status": "erledigt",
             "status_changed_at": alt, "assets_cleaned_at": frisch},        # nicht
        ]
        await db.appointments.insert_many(docs)
        try:
            tage = dict(cs.CLEANUP_RULES)["erledigt"]
            cutoff = _iso(JETZT - timedelta(days=tage))
            treffer = {a["id"] async for a in db.appointments.find({
                "status": "erledigt", "dealer_id": dealer,
                "assets_cleaned_at": {"$in": [None, ""]},
                "$or": [{"abgeschlossen_seit": {"$lte": cutoff}},
                        {"abgeschlossen_seit": {"$in": [None, ""]},
                         "status_changed_at": {"$lte": cutoff}}]},
                {"_id": 0, "id": 1})}
            assert treffer == {f"a_r14_e1_{SUF}", f"a_r14_e2_{SUF}"}, treffer
        finally:
            await db.appointments.delete_many({"dealer_id": dealer})

    _run(lauf)


# =====================================================================
#                 Nr. 86 — Rueckfall erst nach Cache-Check (unit)
# =====================================================================
def test_86_cache_treffer_verbraucht_keinen_rueckfall():
    url = f"https://www.kleinanzeigen.de/s-anzeige/r14-rueckfall/97{uuid.uuid4().int % 10**8:08d}-216-1"
    user = {"id": f"u_r14_{SUF}", "dealer_id": f"d_r14_rf_{SUF}", "role": "sucher"}
    tag = JETZT.strftime("%Y-%m-%d")
    budget_id = f"{tag}:rueckfall:{user['dealer_id']}"

    async def lauf(db):
        import routes.listings as L
        from fastapi import BackgroundTasks
        from listing_identity import get_listing_identity
        ck = get_listing_identity(url)["cache_key"]
        alt = (L.CLIENT_FETCH_KLEINANZEIGEN, L.db, L.RUECKFALL_TAGESLIMIT)
        await db.listings_cache.insert_one({
            "cache_key": ck, "source": "kleinanzeigen", "item_id": ck.split(":")[1],
            "url": url, "fetched_at": JETZT, "expires_at": JETZT + timedelta(hours=1),
            "data": {"title": "R14 Cache", "list_price": 1000, "mobile_ad_id": ck.split(":")[1],
                     "make_label": "VW", "model_label": "Golf"}})
        try:
            L.CLIENT_FETCH_KLEINANZEIGEN = True
            L.db = db
            for _ in range(3):
                r = await L.compare(L.CompareIn(url=url, ohne_erweiterung=True),
                                    BackgroundTasks(), user)
                assert not (isinstance(r, dict) and r.get("needs_client_fetch")), r
            b = await db.provider_budget.find_one({"_id": budget_id})
            assert b is None or b.get("n", 0) == 0, \
                f"Cache-Treffer haben den Rueckfall gezaehlt: {b}"
            # Cache-MISS ohne Rueckfall-Kontingent -> Browser gefragt, nichts gezaehlt
            L.RUECKFALL_TAGESLIMIT = 0
            r = await L.compare(L.CompareIn(url=url.replace("-216-1", "-216-2")
                                            .replace("r14-rueckfall/97", "r14-rueckfall/96"),
                                            ohne_erweiterung=True),
                                BackgroundTasks(), user)
            assert isinstance(r, dict) and r.get("needs_client_fetch") is True, r
        finally:
            (L.CLIENT_FETCH_KLEINANZEIGEN, L.db, L.RUECKFALL_TAGESLIMIT) = alt
            await db.listings_cache.delete_many({"cache_key": ck})
            await db.provider_budget.delete_many({"_id": budget_id})
            await db.vehicle_comparisons.delete_many({"dealer_id": user["dealer_id"]})
            await db.vehicles.delete_many({"dealer_id": user["dealer_id"]})
            await db.listing_snapshots.delete_many({"dealer_id": user["dealer_id"]})
            await db.activity_logs.delete_many({"dealer_id": user["dealer_id"]})

    _run(lauf)


# =====================================================================
#                 Nr. 97 / 4 / 25 — Warnung, Fallback, Geheimnis (unit)
# =====================================================================
class _Protokoll:
    def __init__(self):
        self.eintraege = []

    def _log(self, stufe):
        def _f(msg, *args, **_kw):
            self.eintraege.append((stufe, (msg % args) if args else str(msg)))
        return _f

    def __getattr__(self, name):
        if name in ("error", "warning", "info", "debug", "critical", "exception"):
            return self._log(name)
        raise AttributeError(name)

    def texte(self, stufe):
        return [t for s, t in self.eintraege if s == stufe]


_PROD_UMGEBUNG = {
    "APP_ENV": "production",
    "JWT_SECRET": uuid.uuid4().hex + uuid.uuid4().hex,
    "ADMIN_PASSWORD": "Runde14-Betreiber-Kennwort!",
    "SUPER_ADMIN_PASSWORD": "",
    "FRONTEND_URL": "https://app.example.de",
    "CORS_ORIGINS": "https://app.example.de",
    "MONGO_URL": "mongodb://u:p@db:27017/autoschnell?authSource=admin&maxPoolSize=20",
    "MOCK_PROVIDER_FETCH": "false",
    "VERTRAG_AUFBEWAHRUNG_TAGE": "90",
    "SNAPSHOT_RETENTION_DAYS": "60",
    "RESEND_API_KEY": "re_r14_test",
    "MAIL_FROM": "AutoSchnell <vertrag@example.de>",
    "WEB_CONCURRENCY": "1",
    "SELF_SIGNUP": "false",
    "AUTO_DATEN_SCHAEDEN_FREITEXT": "false",
}
_PROD_LOESCHEN = ("S3_ENDPOINT", "S3_BUCKET", "S3_ACCESS_KEY", "S3_SECRET_KEY",
                  "STRIPE_API_KEY", "STRIPE_WEBHOOK_SECRET", "DATEI_SIGNATUR_PFLICHT",
                  "SMTP_HOST", "SMTP_USER", "SMTP_PASS", "SMTP_FROM",
                  "VERTRAG_LOESCHUNG_AKTIV")


@pytest.fixture
def prod_umgebung(monkeypatch, tmp_path):
    for k, v in _PROD_UMGEBUNG.items():
        monkeypatch.setenv(k, v)
    for k in _PROD_LOESCHEN:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path / "backups"))
    return monkeypatch


@pytest.mark.parametrize("wert", ["false", "0"])
def test_97_produktion_warnt_bei_inaktiver_vertragsloeschung(prod_umgebung, wert):
    import production_check
    prod_umgebung.setenv("VERTRAG_LOESCHUNG_AKTIV", wert)
    log = _Protokoll()
    production_check.pruefe_produktion(log)           # kein SystemExit
    hinweise = [t for t in log.texte("warning") if "VERTRAG_LOESCHUNG_AKTIV" in t]
    assert hinweise and "Vorschau" in hinweise[0], log.eintraege


def test_97_produktion_bricht_ab_wenn_die_variable_fehlt(prod_umgebung):
    """Runde 17: FEHLT die Variable ganz, ist das kein bewusster Trockenlauf,
    sondern vergessen — Produktion startet dann nicht (ausdruecklich false
    bleibt eine Warnung, siehe oben)."""
    import production_check
    prod_umgebung.delenv("VERTRAG_LOESCHUNG_AKTIV", raising=False)
    log = _Protokoll()
    with pytest.raises(SystemExit) as e:
        production_check.pruefe_produktion(log)
    assert e.value.code == 78
    assert any("VERTRAG_LOESCHUNG_AKTIV" in t for t in log.texte("error")), log.eintraege


def test_97_aktiv_warnt_scharf_und_dev_schweigt(prod_umgebung):
    import production_check
    prod_umgebung.setenv("VERTRAG_LOESCHUNG_AKTIV", "true")
    log = _Protokoll()
    production_check.pruefe_produktion(log)
    t = [x for x in log.texte("warning") if "VERTRAG_LOESCHUNG_AKTIV" in x]
    assert t and "scharf" in t[0] and "Vorschau" not in t[0]
    prod_umgebung.setenv("APP_ENV", "development")
    prod_umgebung.delenv("VERTRAG_LOESCHUNG_AKTIV", raising=False)
    log = _Protokoll()
    production_check.pruefe_produktion(log)
    assert not [x for x in log.texte("warning") if "VERTRAG_LOESCHUNG_AKTIV" in x]


def test_97_trockenlauf_mit_kandidaten_alarmiert(monkeypatch):
    monkeypatch.delenv("VERTRAG_LOESCHUNG_AKTIV", raising=False)
    dealer = f"d_r14_dry_{SUF}"
    alt = datetime(2000, 3, 1, tzinfo=timezone.utc).isoformat()
    now = datetime(2001, 1, 1, tzinfo=timezone.utc)

    async def lauf(db):
        import cleanup_service as cs
        await db.betriebsalarme.delete_many({"typ": "vertrag_loeschung_trockenlauf"})
        await db.generated_pdfs.insert_one({
            "id": f"c_r14_dry_{SUF}", "dealer_id": dealer, "contract_no": "T",
            "created_at": alt, "admin_vehicle_data_id": "avd_x"})
        try:
            assert await cs.vertraege_nach_frist_loeschen(db, now, aktiv=False) == 0
            assert await db.generated_pdfs.count_documents({"id": f"c_r14_dry_{SUF}"}) == 1
            a = await db.betriebsalarme.find_one(
                {"typ": "vertrag_loeschung_trockenlauf", "offen": True}, {"_id": 0})
            assert a and a["details"]["anzahl"] >= 1, a
            # zweiter Lauf: hochgezaehlt, nicht dupliziert
            await cs.vertraege_nach_frist_loeschen(db, now, aktiv=False)
            assert await db.betriebsalarme.count_documents(
                {"typ": "vertrag_loeschung_trockenlauf", "offen": True}) == 1
        finally:
            await db.generated_pdfs.delete_many({"dealer_id": dealer})
            await db.betriebsalarme.delete_many({"typ": "vertrag_loeschung_trockenlauf"})

    _run(lauf)


def test_25_dateien_rechnet_mit_auth_geheimnis(monkeypatch):
    import auth
    import dateien
    assert dateien._geheimnis() == str(auth.JWT_SECRET).encode("utf-8")
    monkeypatch.delenv("JWT_SECRET", raising=False)     # os.environ ist egal
    assert dateien._geheimnis() == str(auth.JWT_SECRET).encode("utf-8")
    key = f"resale/d_r14_{SUF}/{uuid.uuid4().hex}.jpg"
    url = dateien.signierte_datei_url(key)
    from urllib.parse import parse_qs, urlparse
    q = parse_qs(urlparse(url).query)
    exp, sig = q["exp"][0], q["sig"][0]
    assert dateien.signatur_gueltig(key, exp, sig) is True
    monkeypatch.setattr(auth, "JWT_SECRET", "r14-anderes-" + uuid.uuid4().hex)
    assert dateien.signatur_gueltig(key, exp, sig) is False
    monkeypatch.setenv("JWT_SECRET", "dev-secret")     # darf nichts aendern
    assert dateien._geheimnis() != b"dev-secret"


# =====================================================================
#                          HTTP-Tests (nach Neustart)
# =====================================================================
def _register(name):
    r = requests.post(f"{API}/auth/register", json={
        "email": f"r14infra_{name}_{SUF}@{MAIL}", "password": PW,
        "company_name": f"R14 {name} GmbH", "contact_person": "N T",
        "phone": "0511 9"}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    h = {"Authorization": f"Bearer {r.json()['token']}"}
    me = requests.get(f"{API}/auth/me", headers=h, timeout=30).json()["user"]
    _db().subscriptions.insert_one({
        "id": str(uuid.uuid4()), "dealer_id": me["dealer_id"],
        "subject_user_id": me["id"], "plan": "monthly", "status": "active",
        "expires_at": _iso(JETZT + timedelta(days=1)), "created_at": _iso(JETZT)})
    return {"h": h, "me": me, "dealer_id": me["dealer_id"]}


def _firma_loeschen(f):
    d = _db()
    for c in ("subscriptions", "vehicles", "appointments", "generated_pdfs",
              "activity_logs", "listing_snapshots", "vehicle_comparisons"):
        d[c].delete_many({"dealer_id": f["dealer_id"]})
    d.users.delete_many({"id": f["me"]["id"]})
    d.dealers.delete_many({"id": f["dealer_id"]})


@pytest.fixture(scope="module")
def firmen():
    if not HTTP:
        yield None
        return
    a, b = _register("a"), _register("b")
    yield a, b
    _firma_loeschen(a)
    _firma_loeschen(b)


def test_10_http_fremde_firma_sieht_keine_ersteller_metadaten(firmen):
    if not HTTP:
        pytest.skip(HTTP_GRUND)
    a, b = firmen
    d = _db()
    vid = f"v_r14_{SUF}"
    sid = f"snap_r14_{SUF}"
    # Fahrzeug bei BEIDEN Firmen (gleiche Anzeige), Snapshot von A erzeugt
    d.vehicles.insert_many([
        {"id": vid, "dealer_id": a["dealer_id"], "mobile_ad_id": f"r14{SUF}",
         "data": {}, "created_at": _iso(JETZT), "updated_at": _iso(JETZT)},
        {"id": vid, "dealer_id": b["dealer_id"], "mobile_ad_id": f"r14{SUF}",
         "data": {}, "created_at": _iso(JETZT), "updated_at": _iso(JETZT)}])
    d.listing_snapshots.insert_one({
        "id": sid, "dealer_id": a["dealer_id"], "user_id": a["me"]["id"],
        "vehicle_id": vid, "mobile_ad_id": f"r14{SUF}",
        "source_url": "https://www.kleinanzeigen.de/s-anzeige/x/1-216-1",
        "status": "ready", "pdf_path": "snap/x.pdf", "png_path": "snap/x.png",
        "error": None, "created_at": _iso(JETZT), "completed_at": _iso(JETZT)})
    try:
        rb = requests.get(f"{API}/snapshots/{sid}", headers=b["h"], timeout=30)
        assert rb.status_code == 200, rb.text[:200]
        jb = rb.json()
        assert "dealer_id" not in jb and "user_id" not in jb, jb
        assert "png_path" not in jb and "pdf_path" not in jb
        assert jb["status"] == "ready" and jb["completed_at"] and jb["id"] == sid
        ra = requests.get(f"{API}/snapshots/{sid}", headers=a["h"], timeout=30)
        assert ra.status_code == 200
        ja = ra.json()
        assert ja["dealer_id"] == a["dealer_id"] and ja["user_id"] == a["me"]["id"]
        assert "png_path" not in ja
    finally:
        d.vehicles.delete_many({"id": vid})
        d.listing_snapshots.delete_many({"id": sid})


def test_11_http_vor_dir_zaehlt_nur_eigene_jobs(firmen):
    if not HTTP:
        pytest.skip(HTTP_GRUND)
    a, b = firmen
    d = _db()
    frueher = JETZT - timedelta(minutes=5)
    fremd = [f"job_r14_x{i}_{SUF}" for i in range(3)]
    eigen_alt = f"job_r14_a0_{SUF}"
    eigen = f"job_r14_a1_{SUF}"
    # active=False: der Job-Worker beansprucht sie nicht, Status bleibt queued
    docs = [{"id": j, "cache_key": f"kleinanzeigen:r14x{i}{SUF}", "source": "kleinanzeigen",
             "item_id": f"r14x{i}{SUF}", "url": "https://www.kleinanzeigen.de/s-anzeige/x/1-216-1",
             "status": "queued", "active": False, "attempts": 0, "error": None,
             "requested_by_dealer": b["dealer_id"], "dealer_ids": [b["dealer_id"]],
             "created_at": frueher, "updated_at": frueher}
            for i, j in enumerate(fremd)]
    for j, dt in ((eigen_alt, frueher + timedelta(seconds=1)), (eigen, JETZT)):
        docs.append({"id": j, "cache_key": f"kleinanzeigen:{j}", "source": "kleinanzeigen",
                     "item_id": j, "url": "https://www.kleinanzeigen.de/s-anzeige/x/2-216-1",
                     "status": "queued", "active": False, "attempts": 0, "error": None,
                     "requested_by_dealer": a["dealer_id"], "dealer_ids": [a["dealer_id"]],
                     "created_at": dt, "updated_at": dt})
    d.link_jobs.insert_many(docs)
    try:
        r = requests.get(f"{API}/listings/check/{eigen}", headers=a["h"], timeout=30)
        assert r.status_code == 200, r.text[:200]
        j = r.json()
        assert j["status"] == "queued"
        assert j.get("vor_dir") == 1, j          # nur der eigene aeltere Job
        # fremder Job bleibt unsichtbar
        assert requests.get(f"{API}/listings/check/{fremd[0]}", headers=a["h"],
                            timeout=30).status_code == 404
    finally:
        d.link_jobs.delete_many({"id": {"$in": fremd + [eigen_alt, eigen]}})


def test_85_http_ohne_haendlerprofil_403(firmen):
    if not HTTP:
        pytest.skip(HTTP_GRUND)
    a, _b = firmen
    d = _db()
    dealer = d.dealers.find_one({"id": a["dealer_id"]})
    assert dealer
    assert requests.get(f"{API}/appointments", headers=a["h"], timeout=30).status_code == 200
    d.dealers.delete_one({"id": a["dealer_id"]})
    try:
        for pfad in ("/appointments", "/contracts", "/dealer/settings"):
            r = requests.get(f"{API}{pfad}", headers=a["h"], timeout=30)
            assert r.status_code == 403, (pfad, r.status_code, r.text[:200])
            assert "profil" in r.text.lower(), r.text[:200]
    finally:
        dealer.pop("_id", None)
        d.dealers.insert_one(dealer)
    assert requests.get(f"{API}/appointments", headers=a["h"], timeout=30).status_code == 200


def test_60_61_http_indizes_nach_start_vorhanden():
    if not HTTP:
        pytest.skip(HTTP_GRUND)
    assert requests.get(f"{API}/health", timeout=30).status_code == 200
    d = _db()
    assert "grant_je_session" in _index_namen(d.zugang_grants)
    assert "retry_je_ziel" in _index_namen(d.storage_delete_retry)
    assert "uniq_offene_sucher_abo_anfrage" in _index_namen(d.plan_requests)
    assert "uniq_offene_verkaufspaket_anfrage" in _index_namen(d.plan_requests)
    assert d.storage_delete_retry.count_documents({"art": {"$exists": False}}) == 0


# =============================================================== CI (Runde 21)
# Runde 21 (Pruefbefund C): die HTTP-Teile aller Runde-14-Dateien liefen in
# der CI nie — RUNDE14_HTTP fehlte im Workflow, rund 60 Tests wurden still
# uebersprungen (Grund "HTTP nach Neustart"). Diese Tests halten fest, dass
# der Schalter im Schritt mit dem laufenden Backend gesetzt bleibt und jeder
# Skip einen verstaendlichen Grund traegt.
WURZEL = Path(__file__).resolve().parents[2]
CI_YML = WURZEL / ".github" / "workflows" / "ci.yml"


def _ci_schritt(ci, name):
    start = ci.index(f"- name: {name}")
    ende = ci.find("\n      - ", start + 1)
    return ci[start:] if ende == -1 else ci[start:ende]


def test_r21_ci_schaltet_runde14_http_tests_ein():
    import re
    ci = CI_YML.read_text(encoding="utf-8")
    job = ci[ci.index("\n  backend:"):ci.index("\n  frontend:")]
    schritt = _ci_schritt(job, "Selbsttest-Suite")
    # Schalter als echte YAML-Zeile (kein Kommentar), im Schritt oder im
    # Job-env — beides wirkt fuer pytest; 1 mit oder ohne Anfuehrungszeichen.
    schalter = r'^\s+RUNDE14_HTTP:\s*["\']?1["\']?\s*$'
    job_env = job[:job.index("\n    steps:")]
    assert (re.search(schalter, schritt, re.M)
            or re.search(schalter, job_env, re.M)), schritt
    # Runde 21 (Gegenpruefung): "-rs" steht auch im Kommentar ueber dem
    # Schritt — geprueft wird deshalb der pytest-Aufruf selbst (-rs, -ra, -rfs).
    assert re.search(r'^\s*python -m pytest\b[^\n#]*\s-r[a-zA-Z]*[sa]\b',
                     schritt, re.M), "Skip-Gruende muessen im CI-Log stehen (pytest -rs)"
    # Das Backend fuer die HTTP-Tests laeuft im selben Job VOR der Suite auf
    # dem Port aus TEST_BASE_URL (sonst liefen die Tests ins Leere).
    assert "TEST_BASE_URL: http://127.0.0.1:8001" in job
    assert "- name: Backend starten" in job, "Schritt 'Backend starten' fehlt im Job backend"
    start = job.index("- name: Backend starten")
    assert "--port 8001" in _ci_schritt(job, "Backend starten")
    assert start < job.index("- name: Selbsttest-Suite")
    # Uebersicht der verbleibenden Skips liegt im Repo
    doku = WURZEL / "docs" / "tests" / "UEBERSPRUNGENE_TESTS.md"
    assert doku.is_file(), doku
    assert "RUNDE14_HTTP" in doku.read_text(encoding="utf-8")


def test_r21_runde14_skips_haben_klaren_grund():
    alt = 'pytest.skip("HTTP nach ' + 'Neustart")'
    dateien = sorted(Path(__file__).resolve().parent.glob("test_befunde_runde14_*.py"))
    assert len(dateien) >= 9, dateien
    for datei in dateien:
        text = datei.read_text(encoding="utf-8")
        assert alt not in text, f"{datei.name}: Skip ohne klaren Grund"
        if 'os.environ.get("RUNDE14_HTTP")' in text:
            assert "HTTP_GRUND = (" in text and "RUNDE14_HTTP=1" in text, datei.name
            assert "pytest.skip(HTTP_GRUND)" in text, datei.name
