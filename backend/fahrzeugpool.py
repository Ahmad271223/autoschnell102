# -*- coding: utf-8 -*-
"""Fahrzeugpool-Begrenzung (Wunsch 09/2026): je Firma bleiben nur die
neuesten N verglichenen Fahrzeuge (Default 30) — kommt ein neuer Vergleich,
faellt der aelteste raus. Geschuetzt bleiben Fahrzeuge, die schon in einen
Vertrag, einen Abholtermin oder ein Weiterverkaufs-Inserat uebernommen
wurden, sowie alles, was nicht mehr nur "verglichen" ist (Bestand etc.).
Snapshots werden NICHT angefasst (firmenuebergreifend geteilt; die
Aufbewahrung regelt cleanup_service)."""
import os

POOL_MAX = int(os.environ.get("FAHRZEUGPOOL_MAX_VERGLEICHE", "30") or 30)


async def fahrzeugpool_trimmen(db, dealer_id: str, limit=None) -> int:
    limit = POOL_MAX if limit is None else int(limit)
    if limit <= 0 or not dealer_id:
        return 0
    cur = db.vehicles.find(
        {"dealer_id": dealer_id, "lifecycle": "verglichen"},
        {"_id": 0, "id": 1},
    ).sort([("updated_at", -1), ("created_at", -1)]).skip(limit)
    kandidaten = [v["id"] async for v in cur]
    if not kandidaten:
        return 0
    geschuetzt = set()
    for coll in ("generated_pdfs", "appointments", "resale_listings"):
        async for d in db[coll].find({"vehicle_id": {"$in": kandidaten}},
                                     {"_id": 0, "vehicle_id": 1}):
            geschuetzt.add(d.get("vehicle_id"))
    loeschen = [v for v in kandidaten if v not in geschuetzt]
    if not loeschen:
        return 0
    r = await db.vehicles.delete_many(
        {"dealer_id": dealer_id, "id": {"$in": loeschen},
         "lifecycle": "verglichen"})
    return r.deleted_count
