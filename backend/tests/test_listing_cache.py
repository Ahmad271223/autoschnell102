"""Regressionstest: viele VERSCHIEDENE neue Links gleichzeitig.

Vor dem Lease-Fix legten alle neuen Links ein Dokument mit
source=null/item_id=null an; der Unique-Index uniq_source_item liess nur
eines davon zu — die uebrigen warteten ~70 s und schlugen dann fehl.
Dieser Test stellt sicher, dass das nie wieder passiert: N verschiedene
Links muessen parallel in wenigen Sekunden durchlaufen, mit genau EINEM
Fetch pro Link. Braucht eine laufende MongoDB (MONGO_URL, wie in CI).
"""
import asyncio
import os
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# WICHTIG: KEIN os.environ.setdefault("DB_NAME", ...) — pytest importiert
# alle Testdateien vor dem ersten Lauf, und ein hier gesetztes DB_NAME
# gaelte auch fuer die anderen Suiten. Die schrieben ihre Testdaten dann in
# eine andere Datenbank als das laufende Backend (Fehlerbild: 401 beim
# Anmelden). Dieser Test benutzt deshalb eigene lokale Variablen.
MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
DB_PREFIX = "autoschnell_cache_test"

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

from listing_identity import (  # noqa: E402
    ensure_cache_indexes, get_or_fetch_listing,
)

N_LINKS = 30
WAITERS_PER_LINK = 3          # zusaetzlich: mehrere Nutzer je Link
# Die Regression zeigte sich als ~70 s Blockade mit anschliessendem Fehler.
# Gemessen laeuft der Durchlauf in ~15-20 s (30 Abrufe, je 0,3 s, durch die
# Provider-Begrenzung auf 3 gleichzeitige gedrosselt, Warte-Takt 1,5 s).
# 45 s Budget: deutlich unter der Regressionsschwelle, aber unempfindlich
# gegen einen ausgelasteten Rechner.
TIME_BUDGET_SECONDS = 45


def test_many_distinct_new_links_do_not_block_each_other():
    async def run():
        client = AsyncIOMotorClient(MONGO_URL,
                                    serverSelectionTimeoutMS=5000)
        db = client[DB_PREFIX + "_" + uuid.uuid4().hex[:8]]
        try:
            await ensure_cache_indexes(db)
            fetch_calls = {}
            live = {"now": 0, "max": 0}

            async def fetcher(source, item_id, url):
                fetch_calls[item_id] = fetch_calls.get(item_id, 0) + 1
                live["now"] += 1
                live["max"] = max(live["max"], live["now"])
                await asyncio.sleep(0.3)   # simulierter Provider-Abruf
                live["now"] -= 1
                return {"mobile_ad_id": item_id, "title": f"Auto {item_id}",
                        "list_price": 10000}

            urls = [
                f"https://www.kleinanzeigen.de/s-anzeige/test-auto/{7000000000 + i}-216-1"
                for i in range(N_LINKS)
            ]
            tasks = [get_or_fetch_listing(db, u, fetcher, ttl_hours=1)
                     for u in urls for _ in range(WAITERS_PER_LINK)]
            start = time.monotonic()
            results = await asyncio.gather(*tasks)
            elapsed = time.monotonic() - start

            assert elapsed < TIME_BUDGET_SECONDS, (
                f"{N_LINKS} verschiedene Links brauchten {elapsed:.1f}s — "
                "sie blockieren sich gegenseitig (Lease-Regression!)")
            # Jeder Link genau einmal extern geholt (Single-Flight intakt)
            assert len(fetch_calls) == N_LINKS
            assert all(v == 1 for v in fetch_calls.values()), (
                f"Mehrfach-Abrufe: { {k: v for k, v in fetch_calls.items() if v > 1} }")
            # Und jeder Aufrufer hat brauchbare Daten bekommen
            assert all(r[0].get("list_price") == 10000 for r in results)
            # Zentrale Provider-Begrenzung: NIE mehr gleichzeitige externe
            # Abrufe als das Kleinanzeigen-Limit erlaubt (90 Aufrufer
            # duerfen eben NICHT 90 externe Requests ausloesen).
            from provider_limiter import PROVIDER_MAX_CONCURRENT
            limit = PROVIDER_MAX_CONCURRENT["kleinanzeigen"]
            assert live["max"] <= limit, (
                f"{live['max']} gleichzeitige Provider-Abrufe — erlaubt: {limit}")
        finally:
            await client.drop_database(db.name)
            client.close()

    asyncio.run(run())
