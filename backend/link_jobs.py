# -*- coding: utf-8 -*-
"""Hintergrundjobs fuer die Linkpruefung.

Warum: Ein unbekannter Link bedeutet einen externen Anbieter-Abruf, und der
kann unter Last (zentrale Begrenzung!) Minuten warten. Statt die HTTP-
Anfrage des Nutzers so lange offen zu halten, legt /listings/check einen
JOB an und antwortet sofort mit einer Job-ID. Das Frontend fragt den
Status ab; sobald der Job fertig ist, liegt das Inserat im Cache und der
normale /mobile/compare liefert es augenblicklich — die bestehende
Vergleichs- und Kontingentlogik bleibt unveraendert.

Garantien:
- IDEMPOTENT: fuer dasselbe Inserat existiert hoechstens EIN aktiver Job
  (Unique-Index auf cache_key, solange active=True). Alle Wartenden
  bekommen dieselbe Job-ID und damit dasselbe Ergebnis.
- MEHRERE WORKER: jeder Uvicorn-Worker betreibt eine Job-Schleife; ein Job
  wird per atomarem Statuswechsel (queued -> processing) beansprucht —
  genau EIN Worker gewinnt. Der eigentliche Abruf laeuft zusaetzlich durch
  Lease + Provider-Begrenzung aus listing_identity/provider_limiter.
- SELBSTHEILEND: haengt ein Job laenger als processing_until (Worker tot),
  stellt die Aufraeumroutine ihn zurueck auf queued; nach zu vielen
  Versuchen wird er failed. Fertige Jobs raeumt ein TTL-Index nach 1 h weg.

Statuswerte: queued | processing | completed | failed
"""
import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from deps import log

# Wie viele Jobs EIN Worker-Prozess gleichzeitig bearbeitet. Die Zahl der
# echten Anbieter-Abrufe deckelt ohnehin provider_limiter.
JOB_CONCURRENCY = int(os.environ.get("LINK_JOB_CONCURRENCY", "4"))
# Nach so vielen Sekunden gilt ein 'processing'-Job als verwaist.
PROCESSING_TTL_SECONDS = int(os.environ.get("LINK_JOB_PROCESSING_TTL", "240"))
# Maximale Wiederanlaeufe, bevor ein Job endgueltig failed wird.
MAX_ATTEMPTS = int(os.environ.get("LINK_JOB_MAX_ATTEMPTS", "3"))
# Fertige/gescheiterte Jobs verschwinden nach dieser Zeit automatisch.
FINISHED_TTL_SECONDS = int(os.environ.get("LINK_JOB_FINISHED_TTL", "3600"))

_WORKER = f"{os.getpid()}-{uuid.uuid4().hex[:6]}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def ensure_job_indexes(db) -> None:
    # Genau EIN aktiver Job je Inserat (queued/processing tragen active=True).
    await db.link_jobs.create_index(
        "cache_key", unique=True, name="uniq_active_cache_key",
        partialFilterExpression={"active": True})
    await db.link_jobs.create_index("id", unique=True, name="uniq_job_id")
    await db.link_jobs.create_index("status", name="by_status")
    # TTL: fertige Jobs raeumen sich selbst weg. Aendert sich die TTL per
    # Umgebung, lehnt Mongo create_index mit "IndexOptionsConflict" ab —
    # vorher fiel damit der Job-Worker in ALLEN Prozessen aus (Review
    # 09/2026). Jetzt: alten Index verwerfen und neu anlegen.
    await _ttl_index_sicher(db, "finished_at", FINISHED_TTL_SECONDS, "ttl_finished")
    # Notbremse: auch nie fertig gewordene Jobs verschwinden nach 24 h.
    await _ttl_index_sicher(db, "created_at", 24 * 3600, "ttl_created")


async def _ttl_index_sicher(db, feld: str, sekunden: int, name: str) -> None:
    from pymongo.errors import OperationFailure
    try:
        await db.link_jobs.create_index(feld, expireAfterSeconds=sekunden, name=name)
    except OperationFailure as exc:
        if exc.code not in (85, 86):        # IndexOptionsConflict / IndexKeySpecsConflict
            raise
        log.warning("link_jobs: TTL-Index %s hat andere Optionen — wird neu angelegt", name)
        try:
            await db.link_jobs.drop_index(name)
        except OperationFailure:
            pass
        await db.link_jobs.create_index(feld, expireAfterSeconds=sekunden, name=name)


async def enqueue_job(db, url: str, dealer_id: str = "") -> dict:
    """Job fuer diesen Link anlegen — oder den bereits AKTIVEN Job dieses
    Inserats zurueckgeben (idempotent, race-fest ueber den Unique-Index)."""
    from listing_identity import get_listing_identity
    identity = get_listing_identity(url)
    job = {
        "id": str(uuid.uuid4()),
        "cache_key": identity["cache_key"],
        "source": identity["source"],
        "item_id": identity["item_id"],
        "url": url,
        "status": "queued",
        "active": True,
        "attempts": 0,
        "error": None,
        "requested_by_dealer": dealer_id,
        # Audit 09/2026 (Punkt 33): alle Firmen, die auf diesen Job warten —
        # nur sie duerfen den Status abfragen.
        "dealer_ids": [dealer_id] if dealer_id else [],
        "created_at": _now(),
        "updated_at": _now(),
    }
    try:
        await db.link_jobs.insert_one(dict(job))
        job.pop("_id", None)
        return job
    except DuplicateKeyError:
        if dealer_id:
            await db.link_jobs.update_one(
                {"cache_key": identity["cache_key"], "active": True},
                {"$addToSet": {"dealer_ids": dealer_id}})
        existing = await db.link_jobs.find_one(
            {"cache_key": identity["cache_key"], "active": True}, {"_id": 0})
        if existing:
            return existing
        # Seltenes Rennen: der aktive Job wurde JETZT gerade fertig —
        # dann liegt das Ergebnis im Cache; ein frischer completed-Stub
        # reicht dem Aufrufer.
        return {**job, "status": "completed", "active": False}


async def get_job(db, job_id: str) -> Optional[dict]:
    return await db.link_jobs.find_one({"id": job_id}, {"_id": 0})


# Audit 09/2026 (Punkt 17): Sofort-Anstoesse sind je Prozess begrenzt und
# dedupliziert — viele parallele Link-Einreichungen erzeugen keine
# unbegrenzten Tasks mehr; der Dauer-Worker holt den Rest im 0,3-s-Takt.
SOFORT_MAX = int(os.environ.get("LINK_JOB_SOFORT_MAX", "2") or 2)
_sofort_laufend: set = set()


def anstossen(db) -> bool:
    """Einen wartenden Job sofort bearbeiten lassen — hoechstens SOFORT_MAX
    gleichzeitig; sonst False (Worker-Schleife uebernimmt)."""
    _sofort_laufend.difference_update({t for t in _sofort_laufend if t.done()})
    if len(_sofort_laufend) >= SOFORT_MAX:
        return False
    try:
        task = asyncio.get_running_loop().create_task(process_one_now(db))
    except RuntimeError:
        return False
    _sofort_laufend.add(task)
    task.add_done_callback(_sofort_laufend.discard)
    return True


async def process_one_now(db) -> None:
    """Einen wartenden Job SOFORT bearbeiten — Anstoss direkt nach dem
    Einreihen, statt auf den 0,3-s-Takt des Dauer-Workers zu warten
    (Wunsch 09/2026: Auslesen soll schneller reagieren). Der Claim ist
    atomar: laeuft der Dauer-Worker zeitgleich, gewinnt genau einer."""
    try:
        job = await _claim_one(db)
        if job:
            await _process(db, job)
    except Exception:
        import logging
        logging.getLogger("link_jobs").exception("Sofort-Anstoss fehlgeschlagen")


async def _requeue_stale(db) -> None:
    """Verwaiste processing-Jobs (Worker abgestuerzt) zurueckstellen bzw.
    nach zu vielen Versuchen beenden."""
    cutoff = _now()
    async for j in db.link_jobs.find(
            {"status": "processing", "processing_until": {"$lt": cutoff}},
            {"_id": 0, "id": 1, "attempts": 1}):
        if j.get("attempts", 0) >= MAX_ATTEMPTS:
            await db.link_jobs.update_one(
                {"id": j["id"], "status": "processing"},
                {"$set": {"status": "failed", "active": False,
                          "error": "Abgebrochen: Bearbeiter mehrfach "
                                   "ausgefallen", "finished_at": _now(),
                          "updated_at": _now()}})
        else:
            await db.link_jobs.update_one(
                {"id": j["id"], "status": "processing"},
                {"$set": {"status": "queued", "updated_at": _now()}})


async def _claim_one(db) -> Optional[dict]:
    return await db.link_jobs.find_one_and_update(
        {"status": "queued", "active": True},
        {"$set": {"status": "processing", "worker": _WORKER,
                  "processing_until": _now() + timedelta(
                      seconds=PROCESSING_TTL_SECONDS),
                  "updated_at": _now()},
         "$inc": {"attempts": 1}},
        sort=[("created_at", 1)],
        return_document=ReturnDocument.AFTER)


async def _process(db, job: dict) -> None:
    """Einen beanspruchten Job ausfuehren: das Inserat in den Cache holen.
    Lease, Single-Flight und Provider-Begrenzung stecken bereits in
    get_or_fetch_listing — hier faellt nur der Job-Status."""
    # Imports in einem EIGENEN Schutzblock: schluege das Laden fehl,
    # wuerde ein "except ListingBusy" darunter selbst crashen (Name
    # unbekannt) und der Job bis zum Fristablauf in 'processing' haengen.
    try:
        from listing_identity import ListingBusy, get_or_fetch_listing
        from kleinanzeigen_service import ListingGone
        from provider_fetch import fetch_listing
        from routes.listings import LISTING_CACHE_TTL_HOURS
    except Exception as exc:  # noqa: BLE001
        await db.link_jobs.update_one(
            {"id": job["id"]},
            {"$set": {"status": "failed", "active": False,
                      "error": f"Interner Fehler: {exc}"[:300],
                      "finished_at": _now(), "updated_at": _now()}})
        return

    async def _fetcher(src, iid, url):
        return await fetch_listing(db, src, iid, url,
                                   dealer_id=job.get("requested_by_dealer", ""))

    try:
        await get_or_fetch_listing(db, job["url"], _fetcher,
                                   ttl_hours=LISTING_CACHE_TTL_HOURS)
    except ListingBusy:
        # Anbieter gerade voll ausgelastet — zurueck in die Schlange,
        # zaehlt nicht als Fehlversuch.
        await db.link_jobs.update_one(
            {"id": job["id"], "status": "processing"},
            {"$set": {"status": "queued", "updated_at": _now()},
             "$inc": {"attempts": -1}})
        return
    except ListingGone as exc:
        await db.link_jobs.update_one(
            {"id": job["id"]},
            {"$set": {"status": "failed", "active": False,
                      "error": str(exc), "finished_at": _now(),
                      "updated_at": _now()}})
        return
    except Exception as exc:  # noqa: BLE001
        endgueltig = job.get("attempts", 1) >= MAX_ATTEMPTS
        if endgueltig:
            await db.link_jobs.update_one(
                {"id": job["id"]},
                {"$set": {"status": "failed", "active": False,
                          "error": str(exc)[:300], "finished_at": _now(),
                          "updated_at": _now()}})
        else:
            await db.link_jobs.update_one(
                {"id": job["id"], "status": "processing"},
                {"$set": {"status": "queued",
                          "error": str(exc)[:300], "updated_at": _now()}})
        return
    await db.link_jobs.update_one(
        {"id": job["id"]},
        {"$set": {"status": "completed", "active": False, "error": None,
                  "finished_at": _now(), "updated_at": _now()}})


async def run_job_worker_forever(db) -> None:
    """Job-Schleife eines Worker-Prozesses. Bearbeitet bis zu
    JOB_CONCURRENCY Jobs gleichzeitig; die Zahl echter Anbieter-Abrufe
    begrenzt weiterhin provider_limiter."""
    await asyncio.sleep(3)  # Backend erst hochfahren lassen
    laufend: set = set()
    while True:
        try:
            laufend = {t for t in laufend if not t.done()}
            await _requeue_stale(db)
            while len(laufend) < JOB_CONCURRENCY:
                job = await _claim_one(db)
                if not job:
                    break
                laufend.add(asyncio.create_task(_process(db, job)))
        except Exception as exc:  # noqa: BLE001
            log.warning("link job loop error: %s", exc)
        await asyncio.sleep(0.3)
