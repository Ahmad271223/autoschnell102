"""Hintergrund-Cleanup für Fahrzeug-Assets nach Abholung.

Regel (vom Nutzer definiert):
- Abholung erfolgreich (`status == "abgeholt"`): nach 7 Tagen Inserat-Fotos,
  Snapshots (Beweis-Archiv PDF + PNG) und den Vehicle-Cache-Eintrag löschen.
- Nicht abgeholt (`status == "nicht abgeholt"`): nach 14 Tagen dasselbe.

Der Termin-Eintrag selbst bleibt bestehen (Historie), wird aber mit
`assets_cleaned_at` markiert, damit der Cleanup-Job nicht zweimal läuft.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Iterable

from snapshot_service import delete_object

log = logging.getLogger("autohandel.cleanup")

# (status, Tage bis Löschung)
CLEANUP_RULES = (
    ("abgeholt", 7),
    ("nicht abgeholt", 14),
)

# Intervall zwischen Durchläufen
CLEANUP_INTERVAL_SECONDS = 60 * 60  # 1×/Stunde


def _iter_photo_keys() -> Iterable[str]:
    """Die Felder im `vehicle.data`, die wir ausräumen, wenn gecleant wird."""
    return ("image_urls", "images", "photos", "pictures")


async def _delete_snapshots_for_vehicle(db, vehicle_id: str) -> int:
    """Löscht alle listing_snapshots zum Fahrzeug inkl. der dahinter
    liegenden Object-Storage-Blobs. Liefert Anzahl gelöschter Einträge."""
    if not vehicle_id:
        return 0
    count = 0
    async for snap in db.listing_snapshots.find({"vehicle_id": vehicle_id}, {"_id": 0}):
        for key in ("png_path", "pdf_path"):
            path = snap.get(key)
            if path:
                try:
                    delete_object(path)
                except Exception as exc:  # noqa: BLE001
                    log.warning("storage delete failed for %s: %s", path, exc)
        await db.listing_snapshots.delete_one({"id": snap["id"]})
        count += 1
    return count


async def _cleanup_once(db) -> dict:
    """Ein Durchlauf. Liefert Metriken."""
    now = datetime.now(timezone.utc)
    stats = {"checked": 0, "cleaned": 0, "snapshots_deleted": 0, "photos_cleared": 0}

    for status_name, days in CLEANUP_RULES:
        cutoff_iso = (now - timedelta(days=days)).isoformat()
        # Kandidaten: Termin hat den Status, status_changed_at ist älter
        # als cutoff, und assets wurden noch nicht bereits gecleant.
        cursor = db.appointments.find(
            {
                "status": status_name,
                "status_changed_at": {"$lte": cutoff_iso},
                "assets_cleaned_at": {"$in": [None, ""]},
            },
            {"_id": 0, "id": 1, "vehicle_id": 1, "dealer_id": 1,
             "status_changed_at": 1},
        )
        async for appt in cursor:
            stats["checked"] += 1
            vehicle_id = appt.get("vehicle_id")

            # 1) Snapshots + Storage-Objekte wegwerfen
            if vehicle_id:
                deleted = await _delete_snapshots_for_vehicle(db, vehicle_id)
                stats["snapshots_deleted"] += deleted

                # 2) Fotos aus dem Vehicle-Cache räumen
                v = await db.vehicles.find_one({"id": vehicle_id}, {"_id": 0, "data": 1})
                if v and isinstance(v.get("data"), dict):
                    data = v["data"]
                    changed = False
                    for key in _iter_photo_keys():
                        if data.get(key):
                            data[key] = []
                            changed = True
                    if changed:
                        await db.vehicles.update_one(
                            {"id": vehicle_id},
                            {"$set": {"data": data, "assets_cleaned_at": now.isoformat()}},
                        )
                        stats["photos_cleared"] += 1

                # 3) Listings-Cache-Eintrag entfernen, damit ein neuer
                #    Vergleich wieder frisch zieht (sonst käme der leere
                #    Cache-Hit).
                await db.listings_cache.delete_many({"source_url": {"$regex": vehicle_id}})

            # 4) Termin markieren, damit wir ihn nicht nochmal anfassen
            await db.appointments.update_one(
                {"id": appt["id"]},
                {"$set": {"assets_cleaned_at": now.isoformat()}},
            )
            stats["cleaned"] += 1

    if stats["cleaned"] or stats["snapshots_deleted"]:
        log.info("cleanup run: %s", stats)
    return stats


async def run_cleanup_forever(db):
    """Endlosschleife; wird beim FastAPI-Startup als Task gestartet."""
    # kurze Verzögerung beim Start, damit andere Init-Jobs fertig werden
    await asyncio.sleep(30)
    while True:
        try:
            await _cleanup_once(db)
        except Exception as exc:  # noqa: BLE001
            log.exception("cleanup loop error: %s", exc)
        await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)
