# -*- coding: utf-8 -*-
"""Abruf-Helfer-Rueckfall 09/2026: Ist der Client-Abruf fuer Kleinanzeigen
aktiv (CLIENT_FETCH_KLEINANZEIGEN=true) und der Browser hat KEINE
Erweiterung, darf der Vergleich nicht mehr mit "Erweiterung installieren"
blockieren — mit ohne_erweiterung=true holt der Server das Inserat selbst.

In-Prozess-Test (kein laufendes Backend noetig, nur Mongo): die Routen-
Funktionen werden direkt aufgerufen, Job-Anlage/Abruf sind Stubs.
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
DB_NAME = os.environ.get("DB_NAME") or "autoschnell"
KA_URL = "https://www.kleinanzeigen.de/s-anzeige/abruf-fallback-test/9799999901-216-1"
USER = {"id": "u_abruf_test", "dealer_id": "d_abruf_test", "role": "sucher"}


class _Weiter(Exception):
    """Sentinel: der Server-Abrufpfad wurde erreicht."""


def _run(coro_factory):
    async def _inner():
        from motor.motor_asyncio import AsyncIOMotorClient
        cl = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
        try:
            return await coro_factory(cl[DB_NAME])
        finally:
            cl.close()
    return asyncio.run(_inner())


def test_check_und_compare_fallen_ohne_erweiterung_auf_server_zurueck():
    async def lauf(db):
        import link_jobs
        import routes.listings as L
        from fastapi import BackgroundTasks
        alt = (L.CLIENT_FETCH_KLEINANZEIGEN, L.db, link_jobs.enqueue_job,
               link_jobs.process_one_now, L.get_or_fetch_listing)
        aufrufe = []

        async def enqueue_stub(db_, url, dealer_id=""):
            aufrufe.append(url)
            return {"status": "completed", "id": "job_abruf_test"}

        async def process_stub(db_):
            return None

        erreicht = []

        async def fetch_stub(*a, **k):
            erreicht.append(1)          # Server-Abrufpfad wurde betreten
            raise _Weiter()

        try:
            L.CLIENT_FETCH_KLEINANZEIGEN = True      # Client-Abruf-Modus an
            L.db = db
            link_jobs.enqueue_job = enqueue_stub
            link_jobs.process_one_now = process_stub
            L.get_or_fetch_listing = fetch_stub
            # Vorab-Check ohne Flag: bittet den Browser (bisheriges Verhalten)
            r = await L.listings_check(L.ListingURLIn(url=KA_URL), USER)
            assert r["status"] == "needs_client_fetch" and aufrufe == []
            # Vorab-Check MIT Rueckfall-Flag: Server-Job statt Blockade
            r = await L.listings_check(
                L.ListingURLIn(url=KA_URL, ohne_erweiterung=True), USER)
            assert r["status"] == "completed" and aufrufe == [KA_URL], r
            # Vergleich ohne Flag: needs_client_fetch, kein Abruf
            r = await L.compare(L.CompareIn(url=KA_URL), BackgroundTasks(), USER)
            assert r.get("needs_client_fetch") is True
            # Vergleich MIT Flag: laeuft in den Server-Abruf (der Stub bricht
            # dort ab; compare wandelt das in einen Abruf-Fehler um)
            try:
                r = await L.compare(L.CompareIn(url=KA_URL, ohne_erweiterung=True),
                                    BackgroundTasks(), USER)
                assert not (isinstance(r, dict) and r.get("needs_client_fetch")), r
            except Exception as e:
                assert not isinstance(e, AssertionError), e
            assert erreicht == [1], "Server-Abruf wurde nicht erreicht"
            # Modus AUS: nie needs_client_fetch, auch ohne Flag
            L.CLIENT_FETCH_KLEINANZEIGEN = False
            r = await L.listings_check(L.ListingURLIn(url=KA_URL), USER)
            assert r["status"] == "completed" and len(aufrufe) == 2
        finally:
            (L.CLIENT_FETCH_KLEINANZEIGEN, L.db, link_jobs.enqueue_job,
             link_jobs.process_one_now, L.get_or_fetch_listing) = alt
            await db.link_jobs.delete_many({"url": KA_URL})

    _run(lauf)


def test_mobile_und_autoscout_nie_client_fetch():
    """mobile.de/AutoScout laufen immer serverseitig — auch im Client-Modus."""
    async def lauf(db):
        import routes.listings as L
        alt = (L.CLIENT_FETCH_KLEINANZEIGEN, L.db)
        try:
            L.CLIENT_FETCH_KLEINANZEIGEN = True
            L.db = db
            for url in ("https://suchen.mobile.de/fahrzeuge/details.html?id=379999901",
                        "https://www.autoscout24.de/angebote/x-abc123def456"):
                try:
                    r = await L.listings_check(L.ListingURLIn(url=url), USER)
                except Exception as e:      # Quelle evtl. nicht freigeschaltet (400)
                    assert getattr(e, "status_code", None) == 400, repr(e)
                    continue
                assert r["status"] != "needs_client_fetch", (url, r)
        finally:
            L.CLIENT_FETCH_KLEINANZEIGEN, L.db = alt

    _run(lauf)
