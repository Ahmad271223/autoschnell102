"""Verteilte Job-Sperre über MongoDB.

In Produktion laeuft das Backend mit mehreren Worker-Prozessen
(WEB_CONCURRENCY=8). Ohne Sperre wuerde JEDER Worker seinen eigenen
Backup- und Aufraeum-Lauf starten — achtfache Last, achtfache Backups
und konkurrierende Loeschvorgaenge.

Mit dieser Sperre erledigt immer nur EIN Worker den Job; die anderen
ueberspringen ihn. Faellt der Gewinner aus, laeuft die Sperre nach
`ttl_seconds` ab und ein anderer Worker uebernimmt beim naechsten Mal.
"""
import asyncio
import contextlib
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

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
        # Pruefung 14.09.2026 (Liste 5, Nr. 7): und den Fehler WEITERGEBEN —
        # vorher glaubte server.py, der Index stehe (in Produktion Startabbruch).
        import logging
        logging.getLogger("autohandel").error(
            "job_locks-Index konnte nicht angelegt werden: %s", exc)
        raise


async def acquire(db, name: str, ttl_seconds: int = 3600,
                  fehler_melden: bool = False) -> Optional[str]:
    """Besitzer-Token (wahr), wenn dieser Prozess den Job ausfuehren darf,
    sonst None. Phase 3 (15.09.2026, 3.1 / A11 B14 B15): jede Sperre traegt ein
    eigenes Token — ein alter Lauf desselben Prozesses kann eine spaeter neu
    genommene Sperre nicht mehr freigeben oder verlaengern."""
    now = datetime.now(timezone.utc)
    until = now + timedelta(seconds=ttl_seconds)
    token = uuid.uuid4().hex
    try:
        doc = await db.job_locks.find_one_and_update(
            {"name": name,
             "$or": [{"expires_at": {"$lt": now}}, {"expires_at": None}]},
            {"$set": {"name": name, "owner": OWNER, "token": token,
                      "acquired_at": now, "expires_at": until}},
            upsert=True, return_document=ReturnDocument.AFTER,
        )
        if doc and doc.get("owner") == OWNER and doc.get("token") == token:
            return token
        return None
    except DuplicateKeyError:
        # Ein anderer Worker haelt die (noch gueltige) Sperre.
        return None
    except Exception:
        # Im Zweifel NICHT ausfuehren — lieber ein Lauf zu wenig als
        # mehrere gleichzeitig.
        #
        # Nachpruefung 20.09.2026: Der Aufrufer konnte "ein anderer Worker
        # hat die Sperre" (normal) nicht von "die Datenbank antwortet nicht"
        # (Stoerung) unterscheiden — beides war None. Die Sicherung wertete
        # das als "nichts zu tun" und startete deshalb KEINEN Wiederholungs-
        # versuch nach einer Stunde. Mit fehler_melden=True bekommt der
        # Aufrufer die Stoerung zu sehen; ohne den Schalter bleibt alles wie
        # bisher.
        if fehler_melden:
            raise
        return None


def _eigene(name: str, token: Optional[str]) -> dict:
    filt = {"name": name, "owner": OWNER}
    if token:
        filt["token"] = token
    return filt


async def verlaengern(db, name: str, ttl_seconds: int = 3600,
                      token: Optional[str] = None) -> bool:
    """Eigene Sperre verlaengern (Heartbeat eines laufenden Jobs). False, wenn
    sie inzwischen abgelaufen und von einem anderen Prozess uebernommen wurde
    (oder die Datenbank nicht antwortet) — dann darf der Lauf nicht weiter
    davon ausgehen, allein zu sein. Mit `token` nur die eigene Instanz."""
    try:
        r = await db.job_locks.update_one(
            _eigene(name, token),
            {"$set": {"expires_at": datetime.now(timezone.utc)
                      + timedelta(seconds=ttl_seconds)}})
        return bool(r.matched_count)
    except Exception:
        return False


@contextlib.asynccontextmanager
async def heartbeat(db, name: str, token: Optional[str], ttl_seconds: int = 3600,
                    intervall: int = 30):
    """Phase 3 (3.1): haelt die Sperre waehrend eines langen Laufs am Leben —
    alle `intervall` Sekunden wird sie um `ttl_seconds` verlaengert. Geht sie
    verloren, steht das laut im Log (der Lauf selbst wird nicht abgebrochen,
    der zweite Prozess sieht denselben Stand in der Datenbank)."""
    stop = asyncio.Event()

    async def _puls():
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=intervall)
                return
            except asyncio.TimeoutError:
                pass
            if not await verlaengern(db, name, ttl_seconds, token=token):
                logging.getLogger("autohandel").error(
                    "Sperre %s konnte nicht verlaengert werden — ein zweiter Prozess "
                    "koennte denselben Job starten", name)

    aufgabe = asyncio.ensure_future(_puls())
    try:
        yield
    finally:
        stop.set()
        try:
            await aufgabe
        except Exception:  # noqa: BLE001
            pass


async def gehalten(db, name: str) -> bool:
    """True, wenn irgendein Prozess die Sperre gerade gueltig haelt."""
    try:
        return bool(await db.job_locks.find_one(
            {"name": name, "expires_at": {"$gt": datetime.now(timezone.utc)}},
            {"_id": 1}))
    except Exception:
        return False


async def release(db, name: str, token: Optional[str] = None) -> None:
    """Sperre freigeben (nur die eigene; mit `token` nur genau diese Instanz)."""
    try:
        await db.job_locks.update_one(
            _eigene(name, token),
            {"$set": {"expires_at": datetime.now(timezone.utc)}})
    except Exception:
        pass
