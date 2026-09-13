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
import weakref
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
# Audit 13.09.2026 (#32): Solange ein Job WIRKLICH laeuft, verlaengert ein
# Herzschlag seine Frist — vorher stellte _requeue_stale jeden Abruf ueber
# 240 s zurueck, obwohl der erste Task noch arbeitete. max(1, ...): bei
# kleiner TTL wuerde sonst ohne Pause geschrieben.
HERZSCHLAG_SEKUNDEN = max(1, PROCESSING_TTL_SECONDS // 3)
# Ein wirklich haengender Abruf soll nicht ewig 'processing' bleiben: nach
# dieser Laufzeit endet der Herzschlag, die Selbstheilung greift wieder.
HERZSCHLAG_MAX_SEKUNDEN = int(os.environ.get("LINK_JOB_HERZSCHLAG_MAX", "900") or 900)
# Maximale Wiederanlaeufe, bevor ein Job endgueltig failed wird.
MAX_ATTEMPTS = int(os.environ.get("LINK_JOB_MAX_ATTEMPTS", "3"))
# Fertige/gescheiterte Jobs verschwinden nach dieser Zeit automatisch.
FINISHED_TTL_SECONDS = int(os.environ.get("LINK_JOB_FINISHED_TTL", "3600"))

# Runde 28 (12.09.2026, Pruefbefund Fairness): Ohne Obergrenze konnte EIN
# Sucher hunderte Links einreihen; die Warteschlange war reines FIFO und
# alle anderen warteten dahinter. Jetzt: begrenzte offene Jobs je Konto
# und je Firma, und der Worker bedient die Konten reihum.
MAX_OFFEN_JE_KONTO = int(os.environ.get("LINK_JOB_MAX_OFFEN_JE_KONTO", "20") or 20)
MAX_OFFEN_JE_FIRMA = int(os.environ.get("LINK_JOB_MAX_OFFEN_JE_FIRMA", "100") or 100)
# Audit 13.09.2026 (#29): Wie viele Konten (mit dem aeltesten Warten) die
# Reihum-Auswahl betrachtet. Vorher fest 50 — bei mehr gleichzeitig
# wartenden Konten wurde ein Konto ohne laufende Jobs nicht vorgezogen.
KANDIDATEN_FENSTER = int(os.environ.get("LINK_JOB_KANDIDATEN", "200") or 200)


class WarteschlangeVoll(Exception):
    """Zu viele offene Link-Jobs dieses Kontos bzw. dieser Firma."""

    def __init__(self, text: str, offen: int, grenze: int):
        super().__init__(text)
        self.text = text
        self.offen = offen
        self.grenze = grenze


_WORKER = f"{os.getpid()}-{uuid.uuid4().hex[:6]}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


_letzter_eingang = datetime.min.replace(tzinfo=timezone.utc)


def _eingangszeit() -> datetime:
    """created_at eines neuen Jobs — je Prozess streng steigend, auf volle
    Millisekunden (so speichert Mongo). Audit 13.09.2026 (#28): Die
    Rang-Pruefung ordnet nach created_at; bei gleichen Millisekunden
    entschied sonst die zufaellige Job-ID statt der Eingangsreihenfolge."""
    global _letzter_eingang
    t = _now()
    t = t.replace(microsecond=t.microsecond // 1000 * 1000)
    if t <= _letzter_eingang:
        t = _letzter_eingang + timedelta(milliseconds=1)
    _letzter_eingang = t
    return t


# Audit 13.09.2026 (#28): Eingangszeit vergeben und Einfuegen laufen je
# Prozess nacheinander. Motor fuehrt Schreibzugriffe in einem Thread-Pool
# aus; ohne Sperre konnte ein juengerer Job sichtbar werden und seinen Rang
# zaehlen, bevor ein aelterer eingefuegt war (Grenze +1). Die Sperre haelt
# nur das eine insert_one — Einreichen ist selten, der Durchsatz reicht.
# Je Event-Loop eine Sperre: asyncio.Lock bindet sich an die erste Schleife.
_einfuege_sperren: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def _einfuege_sperre() -> asyncio.Lock:
    schleife = asyncio.get_running_loop()
    sperre = _einfuege_sperren.get(schleife)
    if sperre is None:
        sperre = _einfuege_sperren[schleife] = asyncio.Lock()
    return sperre


async def ensure_job_indexes(db) -> None:
    # Genau EIN aktiver Job je Inserat (queued/processing tragen active=True).
    await db.link_jobs.create_index(
        "cache_key", unique=True, name="uniq_active_cache_key",
        partialFilterExpression={"active": True})
    await db.link_jobs.create_index("id", unique=True, name="uniq_job_id")
    await db.link_jobs.create_index("status", name="by_status")
    # Runde 28: offene Jobs je Konto/Firma zaehlen und reihum auswaehlen.
    await db.link_jobs.create_index([("status", 1), ("user_ids", 1)],
                                    name="by_status_user")
    await db.link_jobs.create_index([("status", 1), ("dealer_ids", 1)],
                                    name="by_status_dealer")
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


OFFEN = ("queued", "processing")


async def _grenzen_pruefen(db, dealer_id: str, user_id: str) -> None:
    """Zu viele offene Jobs? Dann NICHT einreihen (Runde 28).

    Ohne diese Grenze konnte ein einzelnes Konto die Warteschlange fuellen
    und alle anderen Sucher warten lassen. Die Grenzen zaehlen nur OFFENE
    Jobs — fertige verschwinden ohnehin von selbst."""
    if user_id and MAX_OFFEN_JE_KONTO > 0:
        offen = await db.link_jobs.count_documents(
            {"status": {"$in": list(OFFEN)}, "user_ids": user_id})
        if offen >= MAX_OFFEN_JE_KONTO:
            raise _voll_konto(offen)
    if dealer_id and MAX_OFFEN_JE_FIRMA > 0:
        offen = await db.link_jobs.count_documents(
            {"status": {"$in": list(OFFEN)}, "dealer_ids": dealer_id})
        if offen >= MAX_OFFEN_JE_FIRMA:
            raise _voll_firma(offen)


def _voll_konto(offen: int) -> WarteschlangeVoll:
    return WarteschlangeVoll(
        f"Du hast {offen} Links in der Warteschlange (Grenze {MAX_OFFEN_JE_KONTO}). "
        "Bitte warte, bis die ersten fertig sind.",
        offen, MAX_OFFEN_JE_KONTO)


def _voll_firma(offen: int) -> WarteschlangeVoll:
    return WarteschlangeVoll(
        f"Deine Firma hat {offen} Links in der Warteschlange (Grenze {MAX_OFFEN_JE_FIRMA}). "
        "Bitte kurz warten.",
        offen, MAX_OFFEN_JE_FIRMA)


async def _rang_pruefen(db, job: dict, dealer_id: str, user_id: str) -> None:
    """Nach dem Einfuegen: liegt der EIGENE Job ueber der Grenze? Dann den
    noch unberuehrten Job zurueckrollen und WarteschlangeVoll werfen.

    Audit 13.09.2026 (#28): count+insert ist nicht atomar — parallel
    eingereichte Links rutschten ueber die Grenze (auch ueber beide
    App-Server hinweg). Nur die Jobs VOR dem eigenen zaehlen (Rang), damit
    ein neuerer Job nie einen aelteren verdraengt; die Gesamtzahl
    nachzuzaehlen haette alle parallelen Anfragen abgelehnt. pymongo kuerzt
    datetime beim Speichern und im Filter gleich auf Millisekunden, der
    Gleichheitsvergleich passt also."""
    vor_mir = {"$or": [{"created_at": {"$lt": job["created_at"]}},
                       {"created_at": job["created_at"], "id": {"$lte": job["id"]}}]}
    grund = None
    if user_id and MAX_OFFEN_JE_KONTO > 0:
        rang = await db.link_jobs.count_documents(
            {"status": {"$in": list(OFFEN)}, "user_ids": user_id, **vor_mir})
        if rang > MAX_OFFEN_JE_KONTO:
            grund = _voll_konto(rang - 1)
    if grund is None and dealer_id and MAX_OFFEN_JE_FIRMA > 0:
        rang = await db.link_jobs.count_documents(
            {"status": {"$in": list(OFFEN)}, "dealer_ids": dealer_id, **vor_mir})
        if rang > MAX_OFFEN_JE_FIRMA:
            grund = _voll_firma(rang - 1)
    if grund is None:
        return
    # Nur zurueckrollen, solange niemand beigetreten ist und kein Worker
    # den Job beansprucht hat — sonst behalten (minimale Ueberschreitung).
    # Nachbesserung: tritt DASSELBE Konto bei (zweiter Tab), bleiben
    # user_ids/dealer_ids gleich — deshalb zaehlt "beitritte" jeden Beitritt.
    # Server der alten Fassung setzen das Feld nicht (Rollout): dort gilt
    # das bisherige Verhalten.
    r = await db.link_jobs.delete_one({
        "id": job["id"], "status": "queued",
        "user_ids": [user_id] if user_id else [],
        "dealer_ids": [dealer_id] if dealer_id else [],
        "beitritte": {"$exists": False}})
    if r.deleted_count == 1:
        raise grund
    log.warning("link_jobs: Job %s liegt ueber der Grenze, ist aber schon "
                "beansprucht/geteilt — bleibt", job["id"])


def _beitritt(dealer_id: str, user_id: str) -> dict:
    """$addToSet-Teil, mit dem ein Konto/eine Firma einem Job beitritt."""
    dazu = {}
    if dealer_id:
        dazu["dealer_ids"] = dealer_id
    if user_id:
        dazu["user_ids"] = user_id
    return dazu


async def _aktivem_job_beitreten(db, cache_key: str, dealer_id: str,
                                 user_id: str) -> Optional[dict]:
    aktiv = {"cache_key": cache_key, "active": True}
    dazu = _beitritt(dealer_id, user_id)
    if not dazu:
        return await db.link_jobs.find_one(aktiv, {"_id": 0})
    # "beitritte" macht den Beitritt am Dokument sichtbar, auch wenn $addToSet
    # nichts aendert (siehe _rang_pruefen).
    return await db.link_jobs.find_one_and_update(
        aktiv, {"$addToSet": dazu, "$inc": {"beitritte": 1}},
        projection={"_id": 0}, return_document=ReturnDocument.AFTER)


async def enqueue_job(db, url: str, dealer_id: str = "",
                      user_id: str = "") -> dict:
    """Job fuer diesen Link anlegen — oder den bereits AKTIVEN Job dieses
    Inserats zurueckgeben (idempotent, race-fest ueber den Unique-Index)."""
    from listing_identity import get_listing_identity
    identity = get_listing_identity(url)
    # Audit 13.09.2026 (#31): Einem schon aktiven Job beizutreten kostet
    # keinen Abruf — deshalb vor der Grenzpruefung. Vorher bekam ein Sucher
    # am Limit 429, obwohl ein anderer denselben Link gerade holte.
    bestehend = await _aktivem_job_beitreten(
        db, identity["cache_key"], dealer_id, user_id)
    if bestehend:
        return bestehend
    await _grenzen_pruefen(db, dealer_id, user_id)
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
        "requested_by_user": user_id,
        # Runde 28: das KONTO, das wartet — fuer Fairness und Statusabfrage.
        "user_ids": [user_id] if user_id else [],
        # Audit 09/2026 (Punkt 33): alle Firmen, die auf diesen Job warten —
        # nur sie duerfen den Status abfragen.
        "dealer_ids": [dealer_id] if dealer_id else [],
        "updated_at": _now(),
    }
    try:
        async with _einfuege_sperre():
            job["created_at"] = _eingangszeit()
            await db.link_jobs.insert_one(dict(job))
    except DuplicateKeyError:
        # Rennen zwischen Beitritt oben und Einfuegen: jetzt beitreten.
        existing = await _aktivem_job_beitreten(
            db, identity["cache_key"], dealer_id, user_id)
        if existing:
            return existing
        # Seltenes Rennen: der aktive Job wurde JETZT gerade fertig —
        # dann liegt das Ergebnis im Cache; ein frischer completed-Stub
        # reicht dem Aufrufer.
        return {**job, "status": "completed", "active": False}
    job.pop("_id", None)
    # Audit 13.09.2026 (#28): Rang-Pruefung NUR fuer den selbst eingefuegten
    # Job — nie im Beitrittsweg; _grenzen_pruefen oben bleibt der Schnellweg.
    try:
        await _rang_pruefen(db, job, dealer_id, user_id)
    except WarteschlangeVoll:
        raise
    except Exception as exc:  # noqa: BLE001
        # Der Job ist gespeichert und wird abgearbeitet — lieber minimal
        # ueber der Grenze als ein 500 fuer einen vorhandenen Job.
        log.warning("link_jobs: Rang-Pruefung fuer Job %s fehlgeschlagen: %s",
                    job["id"], exc)
    return job


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
            # Audit 13.09.2026 (#32): claim_id entfernen — beansprucht ein
            # Server der alten Fassung (setzt keine claim_id) den Job neu,
            # darf kein ueberholter Task mehr auf die alte Kennung passen.
            # Schuetzt nur gegen Rueckstellungen der neuen Fassung: der
            # _requeue_stale der alten Fassung laesst claim_id stehen.
            await db.link_jobs.update_one(
                {"id": j["id"], "status": "processing"},
                {"$set": {"status": "queued", "updated_at": _now()},
                 "$unset": {"claim_id": ""}})


async def _beanspruchen(db, filter_zusatz: dict) -> Optional[dict]:
    """Einen wartenden Job in Bearbeitung nehmen (aeltester zuerst)."""
    return await db.link_jobs.find_one_and_update(
        {"status": "queued", "active": True, **filter_zusatz},
        {"$set": {"status": "processing", "worker": _WORKER,
                  # Audit 13.09.2026 (#32): Kennung DIESES Claims — Herzschlag
                  # und Rueckstellung beruehren nur den eigenen Claim.
                  "claim_id": uuid.uuid4().hex,
                  "processing_until": _now() + timedelta(
                      seconds=PROCESSING_TTL_SECONDS),
                  "updated_at": _now()},
         "$inc": {"attempts": 1}},
        sort=[("created_at", 1)],
        return_document=ReturnDocument.AFTER)


async def _frist_verlaengern(db, job_id: str, claim_id: str) -> None:
    """Herzschlag eines laufenden Jobs (Audit 13.09.2026, #32): haelt
    processing_until frisch, solange der eigene Claim gilt. Stirbt der
    Worker, endet der Herzschlag und _requeue_stale heilt wie bisher.
    Fehler werden geschluckt (wie listing_identity._extend_lease_forever),
    sonst liefe die Frist nach einem kurzen DB-Schluckauf mitten im Abruf ab."""
    beginn = _now()
    while True:
        try:
            await asyncio.sleep(HERZSCHLAG_SEKUNDEN)
            if (_now() - beginn).total_seconds() > HERZSCHLAG_MAX_SEKUNDEN:
                log.warning("link_jobs: Job %s laeuft laenger als %s s — "
                            "Frist wird nicht mehr verlaengert", job_id,
                            HERZSCHLAG_MAX_SEKUNDEN)
                return
            await db.link_jobs.update_one(
                {"id": job_id, "status": "processing", "claim_id": claim_id},
                {"$set": {"processing_until": _now() + timedelta(
                    seconds=PROCESSING_TTL_SECONDS), "updated_at": _now()}})
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            continue


async def _claim_one(db) -> Optional[dict]:
    """Naechsten Job holen — reihum je Konto (Runde 28).

    Vorher galt reines FIFO: Wer 500 Links einwarf, schob alle anderen
    dahinter. Jetzt wird je Konto der aelteste wartende Job betrachtet;
    zuerst kommt das Konto, das gerade am wenigsten in Arbeit hat, dann
    das mit dem laengsten Warten."""
    # Audit 13.09.2026 (#30): ein geteilter Link gehoert ALLEN wartenden
    # Konten (user_ids) — sonst erbte Sucher B die Warteposition von A, der
    # den Link zuerst eingereicht hatte. Altjobs ohne user_ids fallen auf
    # requested_by_user zurueck. Steht derselbe Job in mehreren Gruppen,
    # trifft der zweite Beanspruchen-Versuch nichts und die Schleife geht weiter.
    je_konto = [{"$unwind": {"path": "$user_ids", "preserveNullAndEmptyArrays": True}}]
    konto = {"$ifNull": ["$user_ids", "$requested_by_user"]}
    laufend: dict = {}
    async for reihe in db.link_jobs.aggregate([
        {"$match": {"status": "processing"}},
        *je_konto,
        {"$group": {"_id": konto, "n": {"$sum": 1}}},
    ]):
        laufend[reihe["_id"] or ""] = reihe["n"]
    kandidaten = [reihe async for reihe in db.link_jobs.aggregate([
        {"$match": {"status": "queued", "active": True}},
        *je_konto,
        {"$sort": {"created_at": 1}},
        {"$group": {"_id": konto,
                    "job_id": {"$first": "$id"},
                    "created_at": {"$first": "$created_at"}}},
        # Aeltestes Warten zuerst betrachten: so rutscht kein Konto
        # dauerhaft aus der Auswahl, auch wenn sehr viele warten.
        {"$sort": {"created_at": 1}},
        {"$limit": KANDIDATEN_FENSTER},
    ])]
    if not kandidaten:
        return None
    kandidaten.sort(key=lambda k: (laufend.get(k["_id"] or "", 0),
                                   k["created_at"]))
    for k in kandidaten:
        job = await _beanspruchen(db, {"id": k["job_id"]})
        if job:
            return job
    # Alle Kandidaten waren inzwischen weg (anderer Worker) — einmal
    # regulaer nachfassen, damit kein Job liegen bleibt.
    return await _beanspruchen(db, {})


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

    # Audit 13.09.2026 (#32): Herzschlag fuer die Job-Frist, und die
    # Rueckstellung trifft nur den EIGENEN Claim — ein ueberholter Task
    # ueberschrieb vorher den Claim seines Nachfolgers mit 'queued'.
    # Claims ohne claim_id (alte Fassung beim Rollout): Verhalten wie bisher.
    claim_id = job.get("claim_id")
    eigener_claim = {"id": job["id"], "status": "processing"}
    herz = None
    if claim_id:
        eigener_claim["claim_id"] = claim_id
        herz = asyncio.create_task(_frist_verlaengern(db, job["id"], claim_id))
    try:
        try:
            await get_or_fetch_listing(db, job["url"], _fetcher,
                                       ttl_hours=LISTING_CACHE_TTL_HOURS)
        except ListingBusy:
            # Anbieter gerade voll ausgelastet — zurueck in die Schlange,
            # zaehlt nicht als Fehlversuch.
            await db.link_jobs.update_one(
                eigener_claim,
                {"$set": {"status": "queued", "updated_at": _now()},
                 "$unset": {"claim_id": ""},
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
                    eigener_claim,
                    {"$set": {"status": "queued",
                              "error": str(exc)[:300], "updated_at": _now()},
                     "$unset": {"claim_id": ""}})
            return
        await db.link_jobs.update_one(
            {"id": job["id"]},
            {"$set": {"status": "completed", "active": False, "error": None,
                      "finished_at": _now(), "updated_at": _now()}})
    finally:
        if herz is not None:
            herz.cancel()


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
