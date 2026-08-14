"""Hintergrund-Cleanup für Fahrzeug-Assets nach Abholung.

Regeln (Stand B2B-Modul 08/2026):
- Abholung erfolgreich (`status == "abgeholt"`): nach 7 Tagen Inserat-Fotos,
  Snapshots und Cache löschen — ABER NUR, solange der Händler noch keine
  Bestands-Entscheidung getroffen hat. Ab „Speichern"/„Weiterverkaufen"
  regelt der Fahrzeug-Lebenszyklus die Aufbewahrung.
- Nicht abgeholt (`status == "nicht abgeholt"`): nach 14 Tagen dasselbe.
- Bestand abgelaufen (`lifecycle == "bestand"` und `bestand.expires_at`
  überschritten, Standard 50 Tage): NUR Fotos + temporäre Verkaufsdaten
  (Inserats-Entwürfe) werden gelöscht; Kaufvertrag, Abholbericht,
  Einkaufspreis, Historie und Audit-Logs bleiben erhalten. Das Fahrzeug
  erhält den Status `archiviert`.

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

# Lebenszyklus-Status, in denen der Händler bereits entschieden hat —
# die 7-Tage-Regel greift dann NICHT mehr.
_DECIDED_STATES = {
    "bestand", "verkaufsentwurf", "verkaufsbereit",
    "veroeffentlicht", "reserviert", "verkauft", "archiviert",
}

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

            # Händler hat bereits über das Fahrzeug entschieden? Dann regelt
            # der Lebenszyklus die Aufbewahrung — 7-Tage-Regel entfällt.
            if vehicle_id:
                v_state = await db.vehicles.find_one(
                    {"id": vehicle_id}, {"_id": 0, "lifecycle": 1})
                if v_state and v_state.get("lifecycle") in _DECIDED_STATES:
                    await db.appointments.update_one(
                        {"id": appt["id"]},
                        {"$set": {"assets_cleaned_at": now.isoformat(),
                                  "cleanup_skipped": "haendler_entscheidung"}},
                    )
                    continue

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

    # ---- 50-Tage-Regel: abgelaufene Bestandsfahrzeuge archivieren ----
    stats["archived"] = await _archive_expired_bestand(db, now)

    if stats["cleaned"] or stats["snapshots_deleted"] or stats["archived"]:
        log.info("cleanup run: %s", stats)
    return stats


async def _archive_expired_bestand(db, now: datetime) -> int:
    """Bestand > 50 Tage: löscht NUR Fotos + Inserats-Entwürfe. Vertrag,
    Abholbericht, Einkaufspreis und Historie bleiben — Fahrzeug wird
    `archiviert` (geschäftliche Nachvollziehbarkeit)."""
    archived = 0
    cursor = db.vehicles.find(
        {"lifecycle": "bestand",
         "bestand.expires_at": {"$lte": now.isoformat(), "$ne": None}},
        {"_id": 0, "id": 1, "dealer_id": 1, "data": 1},
    )
    async for v in cursor:
        vid = v["id"]
        # Fotos aus den Fahrzeugdaten räumen
        data = v.get("data") or {}
        for key in _iter_photo_keys():
            if data.get(key):
                data[key] = []
        # Hochgeladene Dateien (Storage) + Inserats-Entwürfe entfernen
        try:
            from storage_service import storage
            async for listing in db.resale_listings.find(
                {"vehicle_id": vid, "status": {"$in": ["entwurf", "verkaufsbereit"]}},
                {"_id": 0, "id": 1, "photos": 1},
            ):
                for k in (listing.get("photos") or {}).get("uploaded_keys", []):
                    try:
                        storage.delete(k)
                    except Exception:
                        pass
                await db.resale_listings.delete_one({"id": listing["id"]})
        except Exception as exc:  # noqa: BLE001
            log.warning("bestand archive: listing cleanup failed for %s: %s", vid, exc)
        await _delete_snapshots_for_vehicle(db, vid)
        await db.vehicles.update_one(
            {"id": vid},
            {"$set": {"data": data, "lifecycle": "archiviert",
                      "lifecycle_changed_at": now.isoformat(),
                      "archived_at": now.isoformat()}},
        )
        await db.activity_logs.insert_one({
            "id": __import__("uuid").uuid4().hex,
            "dealer_id": v.get("dealer_id"), "user_id": "",
            "action": "fahrzeug.archiviert.50tage", "ref": vid,
            "meta": {}, "created_at": now.isoformat(),
        })
        archived += 1
    return archived


async def run_cleanup_forever(db):
    """Endlosschleife; wird beim FastAPI-Startup als Task gestartet."""
    # kurze Verzögerung beim Start, damit andere Init-Jobs fertig werden
    await asyncio.sleep(30)
    while True:
        try:
            await _cleanup_once(db)
        except Exception as exc:  # noqa: BLE001
            log.exception("cleanup loop error: %s", exc)
        try:
            await _reap_stuck_snapshots(db)
        except Exception as exc:  # noqa: BLE001
            log.exception("snapshot reaper error: %s", exc)
        await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)


async def _reap_stuck_snapshots(db) -> None:
    """Haengengebliebene Snapshot-Jobs heilen: alles, was seit >15 min in
    pending/running/retrying steckt (z.B. weil das Backend mittendrin neu
    gestartet wurde), wird als failed markiert — das Frontend hoert auf zu
    pollen und der naechste Vergleich erzeugt einen frischen Job."""
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
    r = await db.listing_snapshots.update_many(
        {"status": {"$in": ["pending", "running", "retrying"]},
         "created_at": {"$lt": cutoff}},
        {"$set": {"status": "failed",
                  "error": "Zeitueberschreitung — automatisch abgebrochen "
                           "(Backend-Neustart oder haengender Job).",
                  "completed_at": datetime.now(timezone.utc).isoformat()}})
    if r.modified_count:
        log.info("snapshot reaper: %d haengende Jobs bereinigt", r.modified_count)
