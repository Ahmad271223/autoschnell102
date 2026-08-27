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
import os
from datetime import datetime, timedelta, timezone
from typing import Iterable

from snapshot_service import delete_object

log = logging.getLogger("autohandel.cleanup")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

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

                # 2b) Abweichungsfotos aus Abholberichten loeschen — die
                #     Datenschutzerklaerung verspricht: Fotos aus Abholungen
                #     nach 7 Tagen (nicht abgeholt: 14 Tagen) weg. Der
                #     Berichtstext (Kilometerstand, Maengel) bleibt als
                #     Geschaeftsunterlage erhalten, nur die Bilddateien gehen.
                async for rep in db.pickup_reports.find(
                        {"appointment_id": appt["id"]},
                        {"_id": 0, "id": 1, "deviations": 1}):
                    devs = rep.get("deviations") or []
                    rep_changed = False
                    for entry in devs:
                        key = entry.get("photo_key")
                        if not key:
                            continue
                        try:
                            delete_object(key)
                        except Exception as exc:  # noqa: BLE001
                            log.warning("report photo delete failed for %s: %s",
                                        key, exc)
                        entry["photo_key"] = None
                        entry["photo_deleted_at"] = now.isoformat()
                        rep_changed = True
                    if rep_changed:
                        await db.pickup_reports.update_one(
                            {"id": rep["id"]},
                            {"$set": {"deviations": devs}})
                        stats["report_photos_deleted"] = (
                            stats.get("report_photos_deleted", 0) + 1)

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
    from job_lock import acquire, ensure_lock_index
    await ensure_lock_index(db)
    while True:
        # Bei mehreren Worker-Prozessen raeumt nur EINER pro Zyklus auf —
        # sonst loeschen acht Prozesse gleichzeitig dieselben Dateien.
        if not await acquire(db, "cleanup-cycle",
                             ttl_seconds=CLEANUP_INTERVAL_SECONDS - 60):
            await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)
            continue
        try:
            await _cleanup_once(db)
        except Exception as exc:  # noqa: BLE001
            log.exception("cleanup loop error: %s", exc)
        try:
            await _reap_stuck_snapshots(db)
        except Exception as exc:  # noqa: BLE001
            log.exception("snapshot reaper error: %s", exc)
        try:
            await _expire_old_snapshots(db)
        except Exception as exc:  # noqa: BLE001
            log.exception("snapshot expiry error: %s", exc)
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


# ---------------------------------------------------------------------------
# Snapshot-Verfall (Beschluss 08/2026): Beweis-Snapshots werden 60 Tage
# aufbewahrt, danach werden die Dateien geloescht (Speicher waechst sonst
# unbegrenzt — bei 500k neuen Inseraten/Monat ~300 GB monatlich).
# AUSNAHME: Snapshots zu Fahrzeugen, fuer die ein KAUFVERTRAG existiert,
# bleiben fuer immer — sie sind Teil des Beweis-Archivs des Vertrags.
# Die Inserats-DATEN (listings_cache) bleiben unabhaengig davon erhalten;
# ein erneuter Vergleich nach Ablauf erzeugt bei Bedarf einen frischen
# Snapshot, ohne die Quelle fuer die Daten erneut anzurufen.
# ---------------------------------------------------------------------------
SNAPSHOT_RETENTION_DAYS = int(os.environ.get("SNAPSHOT_RETENTION_DAYS", "60"))


async def _expire_old_snapshots(db) -> int:
    from storage_service import storage, StorageError
    cutoff = (datetime.now(timezone.utc)
              - timedelta(days=SNAPSHOT_RETENTION_DAYS)).isoformat()
    # Fahrzeuge mit Kaufvertrag sind geschuetzt (Beweis!).
    protected = set()
    async for c in db.generated_pdfs.find({}, {"_id": 0, "vehicle_id": 1}):
        if c.get("vehicle_id"):
            protected.add(c["vehicle_id"])
    n = 0
    cursor = db.listing_snapshots.find(
        {"status": "ready", "completed_at": {"$lt": cutoff}},
        {"_id": 0, "id": 1, "vehicle_id": 1, "pdf_path": 1, "png_path": 1})
    async for snap in cursor:
        if snap.get("vehicle_id") in protected:
            continue
        for key in (snap.get("pdf_path"), snap.get("png_path")):
            if key:
                try:
                    storage.delete(key)
                except StorageError:
                    pass
        await db.listing_snapshots.update_one(
            {"id": snap["id"]},
            {"$set": {"status": "expired", "expired_at": now_iso()},
             "$unset": {"pdf_path": "", "png_path": ""}})
        n += 1
    if n:
        log.info("[cleanup] %s Snapshots nach %s Tagen verfallen",
                 n, SNAPSHOT_RETENTION_DAYS)
    return n
