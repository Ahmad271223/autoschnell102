# -*- coding: utf-8 -*-
"""Audit 13.09.2026 — Link-Jobs / Warteschlange (Befunde #28 bis #32).

  #28 Grenzen je Konto/Firma: count_documents und insert_one waren nicht
      atomar — parallel eingereichte Links rutschten ueber die Grenze.
  #29 Kandidatenfenster der Reihum-Auswahl war fest 50 Konten.
  #30 Fairness gruppierte nach dem Einreicher statt nach allen wartenden
      Konten — Sucher B erbte die Warteposition von Sucher A.
  #31 Einem schon aktiven Job beizutreten scheiterte an der Grenzpruefung.
  #32 processing_until wurde nie verlaengert — lange Abrufe wurden
      zurueckgestellt, und ein ueberholter Task ueberschrieb fremde Claims.

In-Prozess gegen eine Wegwerf-DB; kein Server.
"""
import asyncio
import importlib
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MONGO_URL = "mongodb://127.0.0.1:27017"


@pytest.fixture
def welt():
    from motor.motor_asyncio import AsyncIOMotorClient
    s = uuid.uuid4().hex[:10]
    # routes.listings muss geladen sein: _process importiert daraus zur Laufzeit.
    mods = [importlib.import_module(n) for n in ("deps", "routes.listings", "link_jobs")]
    alt = [(m, getattr(m, "db", None)) for m in mods]

    class _W:
        pass

    w = _W()
    w.dealer_id = f"d_wtlj_{s}"
    w.a = {"id": f"sa_wtlj_{s}", "dealer_id": w.dealer_id, "role": "sucher"}
    w.b = {"id": f"sb_wtlj_{s}", "dealer_id": w.dealer_id, "role": "sucher"}
    w.c = {"id": f"sc_wtlj_{s}", "dealer_id": f"d2_wtlj_{s}", "role": "sucher"}
    w.loop = asyncio.new_event_loop()
    asyncio.set_event_loop(w.loop)
    w.client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    w.db_name = f"autoschnell_wt_linkjobs_{s}"
    w.db = w.client[w.db_name]
    for m in mods:
        if hasattr(m, "db"):
            m.db = w.db
    w.run = lambda coro: w.loop.run_until_complete(coro)
    yield w
    try:
        w.run(w.client.drop_database(w.db_name))
    finally:
        for m, d in alt:
            if d is not None:
                m.db = d
        w.client.close()
        w.loop.close()


def _neue_url():
    return f"https://www.kleinanzeigen.de/s-anzeige/x/93{uuid.uuid4().int % 10**8:08d}-216-1"


def _job(cache_key, user, dealer_id, minuten=0, status="queued", user_ids=None):
    return {"id": f"job_{uuid.uuid4().hex[:8]}", "cache_key": cache_key,
            "source": "kleinanzeigen", "item_id": cache_key.split(":")[-1],
            "url": f"https://www.kleinanzeigen.de/s-anzeige/x/{cache_key.split(':')[-1]}-216-1",
            "status": status, "active": status in ("queued", "processing"),
            "attempts": 0, "error": None,
            "requested_by_dealer": dealer_id, "requested_by_user": user["id"],
            "dealer_ids": [dealer_id],
            "user_ids": [user["id"]] if user_ids is None else user_ids,
            "created_at": datetime.now(timezone.utc) - timedelta(minutes=minuten),
            "updated_at": datetime.now(timezone.utc)}


# ------------------------------------------------------------------ #28
def test_28_parallele_links_halten_die_kontogrenze_genau_ein(welt, monkeypatch):
    import link_jobs as LJ
    w = welt
    w.run(LJ.ensure_job_indexes(w.db))
    monkeypatch.setattr(LJ, "MAX_OFFEN_JE_KONTO", 2)
    monkeypatch.setattr(LJ, "MAX_OFFEN_JE_FIRMA", 999)

    async def viele():
        return await asyncio.gather(*[
            LJ.enqueue_job(w.db, _neue_url(), dealer_id=w.dealer_id, user_id=w.a["id"])
            for _ in range(10)], return_exceptions=True)

    res = w.run(viele())
    offen = w.run(w.db.link_jobs.count_documents(
        {"status": {"$in": ["queued", "processing"]}, "user_ids": w.a["id"]}))
    # GENAU 2 — nicht <= 2, sonst faellt eine Unterzulassung nicht auf.
    assert offen == 2, res
    assert sum(isinstance(r, dict) for r in res) == 2, res
    voll = [r for r in res if isinstance(r, LJ.WarteschlangeVoll)]
    assert len(voll) == 8, res
    assert all("Warteschlange" in v.text and v.grenze == 2 for v in voll)
    # Die behaltenen Jobs sind die, die der Aufrufer auch zurueckbekam.
    ids = {r["id"] for r in res if isinstance(r, dict)}
    assert w.run(w.db.link_jobs.count_documents({"id": {"$in": list(ids)}})) == 2


def test_28_parallele_links_halten_die_firmengrenze_genau_ein(welt, monkeypatch):
    import link_jobs as LJ
    w = welt
    w.run(LJ.ensure_job_indexes(w.db))
    monkeypatch.setattr(LJ, "MAX_OFFEN_JE_KONTO", 99)
    monkeypatch.setattr(LJ, "MAX_OFFEN_JE_FIRMA", 3)

    async def viele():
        return await asyncio.gather(*[
            LJ.enqueue_job(w.db, _neue_url(), dealer_id=w.dealer_id,
                           user_id=(w.a if i % 2 else w.b)["id"])
            for i in range(10)], return_exceptions=True)

    res = w.run(viele())
    offen = w.run(w.db.link_jobs.count_documents(
        {"status": {"$in": ["queued", "processing"]}, "dealer_ids": w.dealer_id}))
    assert offen == 3, res
    assert sum(isinstance(r, dict) for r in res) == 3, res
    assert sum(isinstance(r, LJ.WarteschlangeVoll) for r in res) == 7, res


def test_28_rueckrollen_trifft_keinen_job_mit_beigetretenem_konto(welt, monkeypatch):
    """Ist jemand dem Job schon beigetreten (oder er ist beansprucht), bleibt
    er — lieber minimal ueber der Grenze als ein Wartender ohne Job."""
    import link_jobs as LJ
    w = welt
    monkeypatch.setattr(LJ, "MAX_OFFEN_JE_KONTO", 1)
    monkeypatch.setattr(LJ, "MAX_OFFEN_JE_FIRMA", 999)
    w.run(w.db.link_jobs.insert_one(_job("kleinanzeigen:alt1", w.a, w.dealer_id, minuten=5)))
    geteilt = _job("kleinanzeigen:neu1", w.a, w.dealer_id, user_ids=[w.a["id"], w.b["id"]])
    w.run(w.db.link_jobs.insert_one(dict(geteilt)))
    w.run(LJ._rang_pruefen(w.db, geteilt, w.dealer_id, w.a["id"]))   # keine Ausnahme
    assert w.run(w.db.link_jobs.count_documents({"id": geteilt["id"]})) == 1
    eigen = _job("kleinanzeigen:neu2", w.a, w.dealer_id)
    w.run(w.db.link_jobs.insert_one(dict(eigen)))
    with pytest.raises(LJ.WarteschlangeVoll):
        w.run(LJ._rang_pruefen(w.db, eigen, w.dealer_id, w.a["id"]))
    assert w.run(w.db.link_jobs.count_documents({"id": eigen["id"]})) == 0


def test_28_beitritt_desselben_kontos_verhindert_rueckrollen(welt, monkeypatch):
    """Nachbesserung: Tritt DASSELBE Konto (zweiter Tab) dem Job bei, aendert
    $addToSet user_ids/dealer_ids nicht — der Beitritt muss trotzdem das
    Zurueckrollen verhindern, sonst hat der zweite Aufruf eine tote Job-ID."""
    import link_jobs as LJ
    w = welt
    w.run(LJ.ensure_job_indexes(w.db))
    monkeypatch.setattr(LJ, "MAX_OFFEN_JE_KONTO", 1)
    monkeypatch.setattr(LJ, "MAX_OFFEN_JE_FIRMA", 999)
    w.run(w.db.link_jobs.insert_one(_job("kleinanzeigen:alt2", w.a, w.dealer_id, minuten=5)))
    eigen = _job("kleinanzeigen:neu3", w.a, w.dealer_id)
    w.run(w.db.link_jobs.insert_one(dict(eigen)))
    beigetreten = w.run(LJ._aktivem_job_beitreten(
        w.db, eigen["cache_key"], w.dealer_id, w.a["id"]))
    assert beigetreten["id"] == eigen["id"]
    assert beigetreten["user_ids"] == [w.a["id"]]
    w.run(LJ._rang_pruefen(w.db, eigen, w.dealer_id, w.a["id"]))   # keine Ausnahme
    assert w.run(w.db.link_jobs.count_documents({"id": eigen["id"]})) == 1


def test_28_zwei_tabs_an_der_grenze_keine_tote_job_id(welt, monkeypatch):
    """Grenze 1: ein anderer neuer Link plus zweimal derselbe neue Link
    desselben Kontos parallel. Jede zurueckgegebene Job-ID muss existieren
    (oder ein completed-Stub sein)."""
    import link_jobs as LJ
    w = welt
    w.run(LJ.ensure_job_indexes(w.db))
    monkeypatch.setattr(LJ, "MAX_OFFEN_JE_KONTO", 1)
    monkeypatch.setattr(LJ, "MAX_OFFEN_JE_FIRMA", 999)

    async def runde():
        await w.db.link_jobs.delete_many({})
        gleich = _neue_url()
        res = await asyncio.gather(
            LJ.enqueue_job(w.db, _neue_url(), dealer_id=w.dealer_id, user_id=w.a["id"]),
            LJ.enqueue_job(w.db, gleich, dealer_id=w.dealer_id, user_id=w.a["id"]),
            LJ.enqueue_job(w.db, gleich, dealer_id=w.dealer_id, user_id=w.a["id"]),
            return_exceptions=True)
        tot = []
        for r in res:
            if isinstance(r, dict) and r.get("status") != "completed":
                if not await w.db.link_jobs.count_documents({"id": r["id"]}):
                    tot.append(r["id"])
        return res, tot

    for _ in range(20):
        res, tot = w.run(runde())
        assert not tot, res
        assert not [r for r in res if isinstance(r, Exception)
                    and not isinstance(r, LJ.WarteschlangeVoll)], res


def test_28_db_fehler_in_rang_pruefung_liefert_trotzdem_den_job(welt, monkeypatch):
    """Der Job ist gespeichert und wird abgearbeitet — ein voruebergehender
    DB-Fehler in der Rang-Pruefung darf kein 500 daraus machen."""
    import link_jobs as LJ
    w = welt
    w.run(LJ.ensure_job_indexes(w.db))

    async def kaputt(*a, **k):
        raise RuntimeError("Primary-Wechsel")

    monkeypatch.setattr(LJ, "_rang_pruefen", kaputt)
    job = w.run(LJ.enqueue_job(w.db, _neue_url(), dealer_id=w.dealer_id, user_id=w.a["id"]))
    assert job["status"] == "queued"
    assert w.run(w.db.link_jobs.count_documents({"id": job["id"]})) == 1


# ------------------------------------------------------------------ #29
def test_29_kandidatenfenster_reicht_ueber_50_konten(welt):
    """51 Konten warten; die 50 aeltesten haben schon einen laufenden Job.
    Konto Z (juengster Job, nichts in Arbeit) muss vorgezogen werden."""
    import link_jobs as LJ
    w = welt
    konten = [{"id": f"k{i:02d}_{w.dealer_id}"} for i in range(51)]
    docs = []
    for i, k in enumerate(konten):
        docs.append(_job(f"kleinanzeigen:q{i}", k, w.dealer_id, minuten=200 - i))
        if i < 50:
            docs.append(_job(f"kleinanzeigen:p{i}", k, w.dealer_id,
                             minuten=300, status="processing"))
    w.run(w.db.link_jobs.insert_many(docs))
    job = w.run(LJ._claim_one(w.db))
    assert job["requested_by_user"] == konten[50]["id"]


# ------------------------------------------------------------------ #30
def test_30_geteilter_link_gehoert_allen_wartenden_konten(welt):
    import link_jobs as LJ
    w = welt
    w.run(w.db.link_jobs.insert_many(
        [_job(f"kleinanzeigen:a{i}", w.a, w.dealer_id, minuten=10 - i) for i in range(3)]
        + [_job("kleinanzeigen:s1", w.a, w.dealer_id, minuten=5,
                user_ids=[w.a["id"], w.b["id"]]),
           _job("kleinanzeigen:c1", w.c, w.c["dealer_id"], minuten=4)]))
    erster = w.run(LJ._claim_one(w.db))
    zweiter = w.run(LJ._claim_one(w.db))
    assert erster["cache_key"] == "kleinanzeigen:a0"
    # B hat sonst nichts in der Schlange: sein geteilter Link kommt als
    # Zweites dran, nicht erst hinter allen Links von A.
    assert zweiter["cache_key"] == "kleinanzeigen:s1"


def test_30_altjob_ohne_konten_wird_weiter_bedient(welt):
    import link_jobs as LJ
    w = welt
    alt = _job("kleinanzeigen:alt", w.a, w.dealer_id, minuten=3, user_ids=[])
    alt.pop("user_ids")
    w.run(w.db.link_jobs.insert_one(alt))
    job = w.run(LJ._claim_one(w.db))
    assert job and job["cache_key"] == "kleinanzeigen:alt"


# ------------------------------------------------------------------ #31
def test_31_beitritt_zu_aktivem_job_trotz_grenze(welt, monkeypatch):
    import link_jobs as LJ
    w = welt
    w.run(LJ.ensure_job_indexes(w.db))
    monkeypatch.setattr(LJ, "MAX_OFFEN_JE_KONTO", 1)
    monkeypatch.setattr(LJ, "MAX_OFFEN_JE_FIRMA", 999)
    url = _neue_url()
    eins = w.run(LJ.enqueue_job(w.db, url, dealer_id=w.dealer_id, user_id=w.a["id"]))
    w.run(w.db.link_jobs.insert_one(_job("kleinanzeigen:bvoll", w.b, w.dealer_id, minuten=2)))
    zwei = w.run(LJ.enqueue_job(w.db, url, dealer_id=w.dealer_id, user_id=w.b["id"]))
    assert zwei["id"] == eins["id"]
    assert w.b["id"] in zwei["user_ids"]
    doc = w.run(w.db.link_jobs.find_one({"id": eins["id"]}))
    assert set(doc["user_ids"]) == {w.a["id"], w.b["id"]}
    # Ein NEUER Link bleibt fuer B weiterhin gesperrt.
    with pytest.raises(LJ.WarteschlangeVoll):
        w.run(LJ.enqueue_job(w.db, _neue_url(), dealer_id=w.dealer_id, user_id=w.b["id"]))


# ------------------------------------------------------------------ #32
def _schnelle_frist(monkeypatch, LJ):
    monkeypatch.setattr(LJ, "PROCESSING_TTL_SECONDS", 1)
    monkeypatch.setattr(LJ, "HERZSCHLAG_SEKUNDEN", 0.2)


def test_32_langer_abruf_behaelt_seinen_claim(welt, monkeypatch):
    import link_jobs as LJ
    import listing_identity
    w = welt
    _schnelle_frist(monkeypatch, LJ)

    async def langsam(*a, **k):
        await asyncio.sleep(3)
        return ({}, False, None)

    monkeypatch.setattr(listing_identity, "get_or_fetch_listing", langsam)

    async def lauf():
        await w.db.link_jobs.insert_one(_job("kleinanzeigen:lang", w.a, w.dealer_id))
        job = await LJ._claim_one(w.db)
        t = asyncio.create_task(LJ._process(w.db, job))
        await asyncio.sleep(2)
        await LJ._requeue_stale(w.db)
        mitte = await w.db.link_jobs.find_one({"id": job["id"]})
        await t
        ende = await w.db.link_jobs.find_one({"id": job["id"]})
        return mitte["status"], ende["status"]

    mitte, ende = w.run(lauf())
    assert mitte == "processing", "Frist wurde nicht verlaengert"
    assert ende == "completed"


def test_32_toter_worker_wird_weiter_zurueckgestellt(welt, monkeypatch):
    import link_jobs as LJ
    import listing_identity
    w = welt
    _schnelle_frist(monkeypatch, LJ)

    async def haengt(*a, **k):
        await asyncio.sleep(30)

    monkeypatch.setattr(listing_identity, "get_or_fetch_listing", haengt)

    async def lauf():
        await w.db.link_jobs.insert_one(_job("kleinanzeigen:tot", w.a, w.dealer_id))
        job = await LJ._claim_one(w.db)
        t = asyncio.create_task(LJ._process(w.db, job))
        await asyncio.sleep(0.3)
        t.cancel()                      # Worker stirbt
        try:
            await t
        except asyncio.CancelledError:
            pass
        await asyncio.sleep(1.5)
        await LJ._requeue_stale(w.db)
        return await w.db.link_jobs.find_one({"id": job["id"]})

    doc = w.run(lauf())
    assert doc["status"] == "queued"
    assert "claim_id" not in doc


def test_32_herzschlag_endet_nach_hoechstlaufzeit(welt, monkeypatch):
    import link_jobs as LJ
    import listing_identity
    w = welt
    _schnelle_frist(monkeypatch, LJ)
    monkeypatch.setattr(LJ, "HERZSCHLAG_MAX_SEKUNDEN", 0.5)

    async def haengt(*a, **k):
        await asyncio.sleep(3)
        return ({}, False, None)

    monkeypatch.setattr(listing_identity, "get_or_fetch_listing", haengt)

    async def lauf():
        await w.db.link_jobs.insert_one(_job("kleinanzeigen:haenger", w.a, w.dealer_id))
        job = await LJ._claim_one(w.db)
        t = asyncio.create_task(LJ._process(w.db, job))
        await asyncio.sleep(2.2)
        await LJ._requeue_stale(w.db)
        doc = await w.db.link_jobs.find_one({"id": job["id"]})
        await t
        return doc

    assert w.run(lauf())["status"] == "queued"


@pytest.mark.parametrize("fehler", ["busy", "kaputt"])
def test_32_ueberholter_task_ueberschreibt_keinen_fremden_claim(welt, monkeypatch, fehler):
    import link_jobs as LJ
    import listing_identity
    w = welt
    doc = _job("kleinanzeigen:ueberholt", w.a, w.dealer_id)

    async def ueberholt(*a, **k):
        # Waehrenddessen hat ein anderer Worker den Job neu beansprucht.
        await w.db.link_jobs.update_one(
            {"id": doc["id"]},
            {"$set": {"claim_id": "fremd", "processing_until":
                      datetime.now(timezone.utc) + timedelta(minutes=10)}})
        if fehler == "busy":
            raise listing_identity.ListingBusy("belegt")
        raise RuntimeError("Anbieter antwortet nicht")

    monkeypatch.setattr(listing_identity, "get_or_fetch_listing", ueberholt)

    async def lauf():
        await w.db.link_jobs.insert_one(dict(doc))
        job = await LJ._claim_one(w.db)
        await LJ._process(w.db, job)
        return await w.db.link_jobs.find_one({"id": doc["id"]})

    danach = w.run(lauf())
    assert danach["status"] == "processing", danach
    assert danach["claim_id"] == "fremd"
    assert danach["attempts"] == 1
