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
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "autoschnell_cache_test")

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

from listing_identity import (  # noqa: E402
    ensure_cache_indexes, get_or_fetch_listing,
)

N_LINKS = 30
WAITERS_PER_LINK = 3          # zusaetzlich: mehrere Nutzer je Link
TIME_BUDGET_SECONDS = 20      # vor dem Fix: ~70 s + Fehler


def test_many_distinct_new_links_do_not_block_each_other():
    async def run():
        client = AsyncIOMotorClient(os.environ["MONGO_URL"],
                                    serverSelectionTimeoutMS=5000)
        db = client[os.environ["DB_NAME"] + "_" + uuid.uuid4().hex[:8]]
        try:
            await ensure_cache_indexes(db)
            fetch_calls = {}

            async def fetcher(source, item_id, url):
                fetch_calls[item_id] = fetch_calls.get(item_id, 0) + 1
                await asyncio.sleep(0.3)   # simulierter Provider-Abruf
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
        finally:
            await client.drop_database(db.name)
            client.close()

    asyncio.run(run())
