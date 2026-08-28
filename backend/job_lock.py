"""Verteilte Job-Sperre über MongoDB.

In Produktion laeuft das Backend mit mehreren Worker-Prozessen
(WEB_CONCURRENCY=8). Ohne Sperre wuerde JEDER Worker seinen eigenen
Backup- und Aufraeum-Lauf starten — achtfache Last, achtfache Backups
und konkurrierende Loeschvorgaenge.

Mit dieser Sperre erledigt immer nur EIN Worker den Job; die anderen
ueberspringen ihn. Faellt der Gewinner aus, laeuft die Sperre nach
`ttl_seconds` ab und ein anderer Worker uebernimmt beim naechsten Mal.
"""
import os
import uuid
from datetime import datetime, timedelta, timezone

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

# Eindeutig je Prozess — so erkennt ein Worker seine eigene Sperre wieder.
OWNER = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"


async def ensure_lock_index(db) -> None:
    """Eindeutiger Index auf `name` — ohne ihn koennten zwei Worker
    gleichzeitig eine Sperre desselben Namens anlegen."""
    try:
        # Alt-Duplikate (aus der Zeit vor dem Index) wuerden die Anlage
        # dauerhaft scheitern lassen — nur das juengste je Name behalten.
        async for row in db.job_locks.aggregate([
                {"$sort": {"acquired_at": -1}},
                {"$group": {"_id": "$name", "keep": {"$first": "$_id"},
                            "n": {"$sum": 1}}}]):
            if row.get("n", 1) > 1:
                await db.job_locks.delete_many(
                    {"name": row["_id"], "_id": {"$ne": row["keep"]}})
        await db.job_locks.create_index("name", unique=True)
    except Exception as exc:
        # Ohne den Index schuetzt die Sperre nicht zuverlaessig — laut sein.
        import logging
        logging.getLogger("autohandel").error(
            "job_locks-Index konnte nicht angelegt werden: %s", exc)


async def acquire(db, name: str, ttl_seconds: int = 3600) -> bool:
    """True, wenn dieser Prozess den Job ausfuehren darf."""
    now = datetime.now(timezone.utc)
    until = now + timedelta(seconds=ttl_seconds)
    try:
        doc = await db.job_locks.find_one_and_update(
            {"name": name,
             "$or": [{"expires_at": {"$lt": now}}, {"expires_at": None}]},
            {"$set": {"name": name, "owner": OWNER,
                      "acquired_at": now, "expires_at": until}},
            upsert=True, return_document=ReturnDocument.AFTER,
        )
        return bool(doc) and doc.get("owner") == OWNER
    except DuplicateKeyError:
        # Ein anderer Worker haelt die (noch gueltige) Sperre.
        return False
    except Exception:
        # Im Zweifel NICHT ausfuehren — lieber ein Lauf zu wenig als
        # mehrere gleichzeitig.
        return False


async def release(db, name: str) -> None:
    """Sperre freigeben (nur die eigene)."""
    try:
        await db.job_locks.update_one(
            {"name": name, "owner": OWNER},
            {"$set": {"expires_at": datetime.now(timezone.utc)}})
    except Exception:
        pass
