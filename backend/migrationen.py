# -*- coding: utf-8 -*-
"""Versionierte Datenbank-Migrationen mit Sperre (Audit 09/2026, Punkt 18).

Vorher fuehrten ALLE Worker-Prozesse Index-Anlage, Seeds und Backfills
gleichzeitig aus (bis zu 8x), Fehler wurden nur protokolliert. Jetzt:
- genau EIN Prozess uebernimmt (Mongo-Sperre "migration"), die anderen
  warten, bis `system_flags._id="schema"` die Zielversion traegt;
- jede Datenmigration ist nummeriert, idempotent und wird in
  `schema_migrations` festgehalten;
- in Produktion bricht ein Fehler den Start ab (fail-closed), lokal wird
  gewarnt.

Aufruf im Container VOR den Web-Workern: `python migrationen.py`
(Dockerfile CMD) — und zusaetzlich beim App-Start als Absicherung.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import datetime, timezone

log = logging.getLogger("autohandel.migrationen")

ZIEL_VERSION = 3
_SPERRE = "migration"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ist_prod() -> bool:
    return os.environ.get("APP_ENV", "").strip().lower() == "production"


# ---------------------------------------------------------------------
# Datenmigrationen (nummeriert, idempotent)
# ---------------------------------------------------------------------
async def m1_abos_normalisieren(db) -> dict:
    """Abo-Audit: naive Ablaufdaten -> UTC, unbekannte Plaene sperren,
    alte firmenweite Abos dem Chef persoenlich zuordnen (eine zentrale
    Aufloesung fuer Anzeige, Zugriff und Abrechnung)."""
    from deps import ABO_PLAENE_ERLAUBT
    stats = {"tz_ergaenzt": 0, "plan_ungueltig": 0, "firmenweit_zugeordnet": 0}
    async for sub in db.subscriptions.find(
            {"expires_at": {"$type": "string"}}, {"_id": 0, "id": 1, "expires_at": 1}):
        ea = sub.get("expires_at") or ""
        try:
            dt = datetime.fromisoformat(ea.replace("Z", "+00:00"))
        except ValueError:
            continue
        if dt.tzinfo is None:
            neu = dt.replace(tzinfo=timezone.utc).isoformat()
            await db.subscriptions.update_one({"id": sub["id"]},
                                              {"$set": {"expires_at": neu}})
            stats["tz_ergaenzt"] += 1
    r = await db.subscriptions.update_many(
        {"plan": {"$nin": sorted(ABO_PLAENE_ERLAUBT)}, "status": {"$in": ["active", "cancelled"]}},
        {"$set": {"status": "ungueltig", "migration_hinweis": "unbekannter Plan (m1)"}})
    stats["plan_ungueltig"] = r.modified_count
    async for sub in db.subscriptions.find(
            {"$or": [{"subject_user_id": {"$exists": False}}, {"subject_user_id": None}],
             "status": {"$in": ["active", "cancelled"]}},
            {"_id": 0, "id": 1, "dealer_id": 1}):
        chef = await db.users.find_one({"dealer_id": sub.get("dealer_id"), "role": "dealer"},
                                       {"_id": 0, "id": 1})
        if not chef:
            continue
        await db.subscriptions.update_one(
            {"id": sub["id"]},
            {"$set": {"subject_user_id": chef["id"], "migriert_von": "firmenweit",
                      "updated_at": _now()}})
        stats["firmenweit_zugeordnet"] += 1
    return stats


async def m2_lifecycle(db) -> dict:
    from lifecycle import migrate_missing_lifecycles
    n = await migrate_missing_lifecycles()
    return {"fahrzeuge": n}


async def m3_kundennummern(db) -> dict:
    from deps import kunden_nummern_nachziehen
    return {"nummern": await kunden_nummern_nachziehen()}


MIGRATIONEN = [
    (1, "abos_normalisieren", m1_abos_normalisieren),
    (2, "lifecycle_nachziehen", m2_lifecycle),
    (3, "kundennummern", m3_kundennummern),
]


# ---------------------------------------------------------------------
# Ablauf
# ---------------------------------------------------------------------
async def aktuelle_version(db) -> int:
    doc = await db.system_flags.find_one({"_id": "schema"})
    return int((doc or {}).get("version") or 0)


async def _sperre_holen(db) -> bool:
    from job_lock import acquire
    return await acquire(db, _SPERRE, ttl_seconds=600)


async def _sperre_loesen(db) -> None:
    from job_lock import release
    await release(db, _SPERRE)


async def ausfuehren(db, indexe=None, seeds=()) -> dict:
    """Als Leader: Indizes, Seeds, Datenmigrationen — in dieser Reihenfolge."""
    if indexe is not None:
        await indexe()
    for seed in seeds:
        await seed()
    stand = await aktuelle_version(db)
    erledigt = {}
    for nr, name, fn in MIGRATIONEN:
        if nr <= stand:
            continue
        log.info("Migration %d (%s) laeuft ...", nr, name)
        stats = await fn(db)
        await db.schema_migrations.update_one(
            {"version": nr},
            {"$set": {"version": nr, "name": name, "applied_at": _now(),
                      "stats": stats}}, upsert=True)
        await db.system_flags.update_one(
            {"_id": "schema"}, {"$set": {"version": nr, "updated_at": _now()}},
            upsert=True)
        erledigt[name] = stats
        log.info("Migration %d (%s) fertig: %s", nr, name, stats)
    await db.system_flags.update_one(
        {"_id": "schema"},
        {"$set": {"version": max(stand, ZIEL_VERSION), "updated_at": _now(),
                  "letzter_start": _now()}}, upsert=True)
    return erledigt


async def ausfuehren_oder_warten(db, indexe=None, seeds=(), warte_sekunden: int = 180) -> str:
    """Genau ein Prozess migriert; die anderen warten auf die Zielversion.
    Rueckgabe: "leader" | "gewartet" | "timeout"."""
    if await _sperre_holen(db):
        try:
            await ausfuehren(db, indexe=indexe, seeds=seeds)
            return "leader"
        except Exception:
            log.exception("Migration fehlgeschlagen")
            if _ist_prod():
                log.error("Start ABGEBROCHEN: Migration fehlgeschlagen (fail-closed)")
                raise SystemExit(78)
            return "fehler"
        finally:
            await _sperre_loesen(db)
    # Kein Leader: warten, bis die Zielversion erreicht ist
    for _ in range(max(1, warte_sekunden)):
        if await aktuelle_version(db) >= ZIEL_VERSION:
            # Indizes sind idempotent — zur Sicherheit auch hier anlegen
            # (z.B. wenn der Leader ein aelterer Prozess war).
            if indexe is not None:
                try:
                    await indexe()
                except Exception as exc:
                    log.warning("Index-Anlage im Wartenden fehlgeschlagen: %s", exc)
            return "gewartet"
        await asyncio.sleep(1)
    log.error("Migration nicht innerhalb von %ds abgeschlossen", warte_sekunden)
    if _ist_prod():
        raise SystemExit(78)
    return "timeout"


def _main() -> int:
    """CLI: `python migrationen.py` — laeuft VOR den Web-Workern."""
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s - %(levelname)s - %(message)s")

    # Audit 09/2026: Die strenge Produktionspruefung MUSS vor jeder
    # Datenbankaenderung laufen. Das Image startet erst migrationen.py
    # (Indizes + Seeds) und danach uvicorn — eine unsichere Konfiguration
    # haette also bereits Migrationen und Seeds ausgefuehrt, bevor der
    # Serverstart abbricht.
    from production_check import pruefe_produktion
    pruefe_produktion(log)

    async def lauf():
        import server  # registriert ensure_indexes/seeds
        from deps import db
        ergebnis = await ausfuehren_oder_warten(
            db, indexe=server.ensure_indexes,
            seeds=(server.seed_admin, server.seed_super_admin), warte_sekunden=300)
        log.info("Migration: %s (Version %d)", ergebnis, await aktuelle_version(db))
        return 0 if ergebnis in ("leader", "gewartet") else 1

    return asyncio.run(lauf())


if __name__ == "__main__":
    sys.exit(_main())
