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

import wartung
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
from konfig import zahl_env  # Pruefung 14.09.2026: keine Abstuerze durch .env-Tippfehler
# Lasttest 16.09.2026: Standard 32 je Prozess — die echte Grenze setzen die
# Anbieter-Slots (provider_limiter, global); ein wartender Job kostet nichts.
JOB_CONCURRENCY = zahl_env("LINK_JOB_CONCURRENCY", 32, unten=1)
# Nach so vielen Sekunden gilt ein 'processing'-Job als verwaist.
PROCESSING_TTL_SECONDS = zahl_env("LINK_JOB_PROCESSING_TTL", 240, unten=30)
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
# Nachpruefung 20.09.2026 (gemessen mit scripts/lasttest_apify_grenze.py):
# Antwortete der Anbieter mit einem Tempolimit (429), ging der Job SOFORT
# zurueck in die Schlange und der Worker holte ihn im 0,3-s-Takt gleich
# wieder. Alle drei Versuche waren damit in gut einer Sekunde verbraucht —
# und weil die Ueberlastung laenger dauert, liefen alle drei in denselben
# 429. Der Sucher sah einen Fehler, obwohl ein paar Sekunden gereicht
# haetten. Jetzt bekommt NUR das Tempolimit einen Abstand; alle anderen
# Fehler (Inserat weg, Netz, Zeitueberschreitung) laufen wie bisher sofort
# wieder an.
TEMPOLIMIT_WARTEN = zahl_env("LINK_JOB_TEMPOLIMIT_WARTEN", 5, unten=0, oben=120)


def _reif() -> dict:
    """Filter-Baustein: Jobs, deren Wartezeit abgelaufen ist (oder die gar
    keine haben — der Normalfall)."""
    return {"$or": [{"fruehestens": {"$exists": False}},
                    {"fruehestens": None},
                    {"fruehestens": {"$lte": _now()}}]}


def _ist_tempolimit(exc: BaseException) -> bool:
    """War das eine Tempolimit-Antwort des Anbieters (429)?"""
    try:
        from anbieter_fehler import AnbieterFehler, ART_LIMIT
        if isinstance(exc, AnbieterFehler):
            return exc.art == ART_LIMIT
    except Exception:  # noqa: BLE001
        pass
    return False
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


#: Pruefbericht 20.09.2026 (A-07): So lange darf ein Job wegen voller
#: Anbieter-Plaetze (ListingBusy) warten; danach endet er mit klarer Meldung.
BUSY_FRIST_S = 600
BUSY_FEHLER = ("Der Anbieter ist gerade ausgelastet — der Link konnte 10 Minuten lang "
               "nicht abgerufen werden. Bitte später erneut prüfen.")


def _als_utc(wert) -> Optional[datetime]:
    """Mongo liefert Zeitpunkte ohne Zeitzone zurueck (naiv = UTC)."""
    if not isinstance(wert, datetime):
        return None
    return wert if wert.tzinfo else wert.replace(tzinfo=timezone.utc)


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


class KeinBerechtigterWartender(RuntimeError):
    """Befunde 146-149 (19.09.2026): Zwischen Einreihen und Abruf koennen
    Minuten liegen. Ist in dieser Zeit das Konto gesperrt, das Abo abgelaufen
    oder die Firma in Loeschung gegangen, darf der Abruf NICHT mehr laufen —
    sonst holt der Worker extern nach, was der Nutzer selbst nicht mehr
    duerfte (und verbraucht dabei fremdes Anbieter-Kontingent)."""


async def wartender_darf_abrufen(db, user_id: str, job: dict) -> Optional[str]:
    """Darf dieses wartende Konto den Anbieter-Abruf ausloesen?

    Liefert die Firma, auf die der Abruf gebucht wird ("" = interner Abruf
    ohne Konto), oder None, wenn das Konto nicht mehr in Frage kommt.
    """
    erlaubte_firmen = set(job.get("dealer_ids") or [])
    if job.get("requested_by_dealer"):
        erlaubte_firmen.add(job["requested_by_dealer"])
    firma = job.get("requested_by_dealer") or ""
    if user_id:
        u = await db.users.find_one(
            {"id": user_id}, {"_id": 0, "id": 1, "role": 1, "dealer_id": 1, "active": 1})
        if not u or u.get("active") is False:          # Befund 146: gesperrt
            return None
        if u.get("role") not in ("dealer", "sucher"):
            return None
        firma = u.get("dealer_id") or ""
        # Befund 149: Der Job wurde unter EINER Firma eingereiht. Wechselt ein
        # Wartender inzwischen die Firma, darf sein Abruf nicht der neuen
        # Firma angelastet werden — sie hat ihn nie eingereiht.
        if erlaubte_firmen and firma not in erlaubte_firmen:
            return None
        from deps import subscription_for
        try:
            if not (await subscription_for(u)).get("active"):
                return None                            # Befund 147: Abo vorbei
        except Exception:  # noqa: BLE001 — im Zweifel nicht abrufen
            log.warning("link_jobs: Abo von %s nicht pruefbar", user_id)
            return None
    if firma:
        d = await db.dealers.find_one({"id": firma}, {"_id": 0, "id": 1, "loeschung": 1})
        # Befund 148: Firma geloescht oder mitten in der Loeschkaskade
        if not d or (d.get("loeschung") or {}).get("status") == "laeuft":
            return None
        # Pruefbericht 20.09.2026 (Nr. 9): ... und nicht GESPERRT. Wird nur
        # der Hauptchef deaktiviert, bleiben die Sucher im Konto weiter
        # "active" — in der normalen API sperrt sie `firma_gesperrt`, hier
        # aber wurde nur nach der LOESCHUNG gefragt. Ein schon eingereihter
        # Job konnte danach weiter bei Apify/Kleinanzeigen abrufen und
        # Kontingent und Geld verbrauchen, obwohl die Firma stillsteht.
        try:
            from deps import firma_gesperrt
            if await firma_gesperrt(firma):
                return None
        except Exception:  # noqa: BLE001 — im Zweifel nicht abrufen
            log.warning("link_jobs: Firmensperre von %s nicht pruefbar", firma)
            return None
    return firma


class JobRace(WarteschlangeVoll):
    """Runde 19 (Nr. 32): dreimal hintereinander verschwand der aktive Job
    zwischen Einfuegen und Beitritt, und im Cache liegt nichts — der Client
    soll kurz spaeter neu einreihen (503 statt geratenem 'completed')."""


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
    job: dict = {}
    for _versuch in range(3):
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
            # Der aktive Job ist zwischen Einfuegen und Beitritt verschwunden.
            # Nachpruefung 13.09.2026 (#28): Das kann ein FERTIGER Job sein
            # (Ergebnis liegt im Cache) — oder einer, den ein anderer Tab an
            # der Grenze zurueckgerollt hat (_rang_pruefen). Ein completed-Stub
            # meldete dann ein Ergebnis fuer einen Link, der nie abgerufen
            # wurde, und das Frontend lud den Vergleich am Limit vorbei.
            # Deshalb nur einen wirklich fertigen Job liefern, sonst neu
            # versuchen (Grenze pruefen, einfuegen).
            fertig = await db.link_jobs.find_one(
                {"cache_key": identity["cache_key"], "status": "completed"},
                {"_id": 0}, sort=[("updated_at", -1)])
            if fertig:
                return fertig
            continue
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
    # Dreimal hintereinander verschwand der aktive Job zwischen Einfuegen und
    # Beitritt — praktisch ausgeschlossen. Dann wie bisher: das Ergebnis
    # liegt (wahrscheinlich) im Cache.
    # Runde 19 (Nr. 32): nicht raten — liegt das Ergebnis wirklich im Cache,
    # ist der Job fertig; sonst soll der Client kurz spaeter neu einreihen.
    log.warning("link_jobs: %s nach drei Einfuegeversuchen ohne aktiven Job",
                identity["cache_key"])
    try:
        from listing_identity import peek_cached_listing
        treffer = await peek_cached_listing(db, url, dealer_id=dealer_id or None)
    except Exception:  # noqa: BLE001
        treffer = None
    if treffer is not None:
        return {**job, "status": "completed", "active": False}
    raise JobRace("Der Link wird gerade eingereiht — bitte in ein paar Sekunden erneut "
                  "versuchen.", 0, 0)


async def get_job(db, job_id: str) -> Optional[dict]:
    return await db.link_jobs.find_one({"id": job_id}, {"_id": 0})


async def _aussteiger_aufraeumen(db, job: dict, dealer_id: str, user_id: str) -> dict:
    """Nach dem Aussteigen aufraeumen, was sonst stehen bleibt.

    Befund 150 (19.09.2026): `requested_by_user` blieb der Aussteiger — und
    genau dieses Konto probiert der Worker beim Abruf ZUERST. Wer abbricht,
    waehrend ein Kollege weiterwartet, wurde also trotzdem mit dem
    Anbieter-Abruf und seinem Tageskontingent belastet.
    Befund 151: `dealer_ids` blieb ebenfalls stehen — der Job zaehlte weiter
    gegen die Warteschlangen-Grenze der Firma, und ihr Chef sah ihn weiter
    als eigenen Auftrag, obwohl dort niemand mehr wartet.
    """
    rest = list(job.get("user_ids") or [])
    firmen_rest = set()
    if rest:
        async for u in db.users.find({"id": {"$in": rest}}, {"_id": 0, "dealer_id": 1}):
            if u.get("dealer_id"):
                firmen_rest.add(u["dealer_id"])
    setzen: dict = {}
    if (job.get("requested_by_user") or "") == user_id:
        nachfolger = rest[0] if rest else ""
        setzen["requested_by_user"] = nachfolger
        if nachfolger:
            u = await db.users.find_one({"id": nachfolger}, {"_id": 0, "dealer_id": 1})
            setzen["requested_by_dealer"] = (u or {}).get("dealer_id") or ""
        else:
            setzen["requested_by_dealer"] = ""
    aenderung: dict = {}
    if setzen:
        aenderung["$set"] = {**setzen, "updated_at": _now()}
    if dealer_id and dealer_id not in firmen_rest:
        aenderung["$pull"] = {"dealer_ids": dealer_id}
    if not aenderung:
        return job
    neu = await db.link_jobs.find_one_and_update(
        {"id": job["id"], "status": {"$in": list(OFFEN)}}, aenderung,
        projection={"_id": 0}, return_document=ReturnDocument.AFTER)
    return neu or job


async def firma_austragen(db, dealer_id: str) -> int:
    """Alle offenen Link-Auftraege einer Firma beenden (Befund 148/165).

    Wird die Firma geloescht, darf kein Worker ihre Auftraege spaeter noch
    extern abrufen — und der Sucher kann sie selbst nicht mehr abbrechen
    (die Firma ist ja gesperrt). Auftraege, auf die nur diese Firma wartet,
    verschwinden; geteilte Auftraege verlieren nur ihre Wartenden.
    Liefert die Zahl der beendeten Auftraege.
    """
    if not dealer_id:
        return 0
    eigene = [u["id"] async for u in db.users.find(
        {"dealer_id": dealer_id}, {"_id": 0, "id": 1})]
    weg = 0
    async for job in db.link_jobs.find(
            {"status": {"$in": list(OFFEN)}, "dealer_ids": dealer_id}, {"_id": 0}):
        rest = [u for u in (job.get("user_ids") or []) if u not in eigene]
        if rest:
            await db.link_jobs.update_one(
                {"id": job["id"], "status": {"$in": list(OFFEN)}},
                {"$pull": {"dealer_ids": dealer_id, "user_ids": {"$in": eigene}},
                 "$set": {"updated_at": _now()}})
            if (job.get("requested_by_user") or "") in eigene:
                u = await db.users.find_one({"id": rest[0]}, {"_id": 0, "dealer_id": 1})
                await db.link_jobs.update_one(
                    {"id": job["id"]},
                    {"$set": {"requested_by_user": rest[0],
                              "requested_by_dealer": (u or {}).get("dealer_id") or ""}})
            continue
        entfernt = await db.link_jobs.delete_one({"id": job["id"], "status": "queued"})
        if entfernt.deleted_count:
            weg += 1
        else:
            # Schon beim Worker: als beendet markieren, damit er nach dem
            # Abruf nichts mehr nachtraegt und niemand mehr wartet.
            await db.link_jobs.update_one(
                {"id": job["id"], "status": {"$in": list(OFFEN)}},
                {"$set": {"user_ids": [], "dealer_ids": [], "updated_at": _now()}})
    if weg:
        log.info("link_jobs: %d offene Auftraege der geloeschten Firma %s entfernt",
                 weg, dealer_id)
    return weg


async def warten_beenden(db, job_id: str, dealer_id: str = "",
                         user_id: str = "") -> dict:
    """Ein Wartender steigt aus (Wunsch Ahmad 18.09.2026: Knopf "X" im
    Vergleich — "dann soll es gestoppt werden ... man soll direkt einen neuen
    Link eingeben koennen").

    EIN Job gehoert allen, die auf dasselbe Inserat warten. Deshalb:
      * Der Aussteiger wird aus der Warteliste genommen — seine Grenze
        (Links je Konto) ist damit sofort wieder frei.
      * Wartet NIEMAND mehr und hat noch kein Worker den Job beansprucht
        (status "queued"), wird er geloescht: der Anbieter-Abruf findet gar
        nicht erst statt (spart Apify-Lauf und Tageskontingent).
      * Laeuft der Abruf schon ("processing"), bleibt er stehen. Er ist
        ohnehin unterwegs, sein Ergebnis landet im Zwischenspeicher — der
        naechste, der den Link einfuegt, hat es sofort.

    Rueckgabe: {"status": "abgebrochen" | "laeuft_weiter" | "weg"}.
    """
    if user_id:
        job = await db.link_jobs.find_one_and_update(
            {"id": job_id, "status": {"$in": list(OFFEN)}},
            {"$pull": {"user_ids": user_id}},
            projection={"_id": 0}, return_document=ReturnDocument.AFTER)
        if job:
            job = await _aussteiger_aufraeumen(db, job, dealer_id, user_id)
    else:
        job = await db.link_jobs.find_one({"id": job_id}, {"_id": 0})
    if not job:
        return {"status": "weg"}
    if job.get("status") == "queued" and not (job.get("user_ids") or []):
        # Nur solange er wirklich noch wartet: beansprucht ihn in derselben
        # Sekunde ein Worker (status -> processing), greift der Filter nicht.
        entfernt = await db.link_jobs.delete_one(
            {"id": job_id, "status": "queued", "user_ids": []})
        if entfernt.deleted_count == 1:
            log.info("link_jobs: Job %s vom Nutzer abgebrochen (niemand wartet mehr)",
                     job_id)
            return {"status": "abgebrochen"}
    return {"status": "laeuft_weiter", "job_status": job.get("status")}


# Audit 09/2026 (Punkt 17): Sofort-Anstoesse sind je Prozess begrenzt und
# dedupliziert — viele parallele Link-Einreichungen erzeugen keine
# unbegrenzten Tasks mehr; der Dauer-Worker holt den Rest im 0,3-s-Takt.
SOFORT_MAX = int(os.environ.get("LINK_JOB_SOFORT_MAX", "4") or 4)
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
            {"_id": 0, "id": 1, "attempts": 1, "processing_until": 1, "claim_id": 1}):
        # Befund 119 (16.09.2026): nur den GELESENEN Stand zurueckstellen —
        # hat der Herzschlag die Frist inzwischen verlaengert oder ein anderer
        # Worker den Job neu beansprucht (neue claim_id, neue Frist), trifft
        # der Filter nicht mehr. Vorher konnte eine alte Rueckstellung den
        # frischen Claim wieder auf 'queued' setzen.
        stand = {"id": j["id"], "status": "processing",
                 "processing_until": j.get("processing_until"),
                 "claim_id": j["claim_id"] if j.get("claim_id") else {"$exists": False}}
        if j.get("attempts", 0) >= MAX_ATTEMPTS:
            await db.link_jobs.update_one(
                stand,
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
                stand,
                {"$set": {"status": "queued", "updated_at": _now()},
                 "$unset": {"claim_id": ""}})


async def _beanspruchen(db, filter_zusatz: dict) -> Optional[dict]:
    """Einen wartenden Job in Bearbeitung nehmen (aeltester zuerst)."""
    return await db.link_jobs.find_one_and_update(
        {"status": "queued", "active": True, **_reif(), **filter_zusatz},
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
        {"$match": {"status": "queued", "active": True, **_reif()}},
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


async def _claim_many(db, n: int) -> list:
    """Bis zu n wartende Jobs auf einmal beanspruchen — reihum je Konto wie
    _claim_one, aber mit EINER Kandidatenrunde je Aufruf statt zwei
    Aggregationen je Job.

    Lasttest 16.09.2026 (180 neue Links gleichzeitig): der Dauer-Worker holte
    die Jobs einzeln, ~10 je Sekunde — allein das Einsammeln dauerte 15 s,
    obwohl 64 Arbeiter frei waren. Jetzt: Belegung je Konto und Kandidaten
    (aeltester Job je Konto) einmal lesen, dann der Reihe nach beanspruchen;
    reicht eine Runde nicht, folgt eine weitere Kandidatenrunde (Fairness
    bleibt: je Runde hoechstens ein Job je Konto)."""
    jobs: list = []
    if n <= 0:
        return jobs
    je_konto = [{"$unwind": {"path": "$user_ids", "preserveNullAndEmptyArrays": True}}]
    konto = {"$ifNull": ["$user_ids", "$requested_by_user"]}
    laufend: dict = {}
    async for reihe in db.link_jobs.aggregate([
        {"$match": {"status": "processing"}},
        *je_konto,
        {"$group": {"_id": konto, "n": {"$sum": 1}}},
    ]):
        laufend[reihe["_id"] or ""] = reihe["n"]
    for _runde in range(4):
        kandidaten = [reihe async for reihe in db.link_jobs.aggregate([
            {"$match": {"status": "queued", "active": True, **_reif()}},
            *je_konto,
            {"$sort": {"created_at": 1}},
            {"$group": {"_id": konto,
                        "job_id": {"$first": "$id"},
                        "created_at": {"$first": "$created_at"}}},
            {"$sort": {"created_at": 1}},
            {"$limit": KANDIDATEN_FENSTER},
        ])]
        if not kandidaten:
            break
        kandidaten.sort(key=lambda k: (laufend.get(k["_id"] or "", 0), k["created_at"]))
        gesehen: set = set()
        vorher = len(jobs)
        for k in kandidaten:
            if len(jobs) >= n:
                return jobs
            if k["job_id"] in gesehen:
                continue
            gesehen.add(k["job_id"])
            job = await _beanspruchen(db, {"id": k["job_id"]})
            if job:
                jobs.append(job)
                laufend[k["_id"] or ""] = laufend.get(k["_id"] or "", 0) + 1
        if len(jobs) == vorher:
            break                     # nichts mehr zu holen (anderer Worker war schneller)
    return jobs


FEHLER_TECHNISCH = ("Technischer Fehler beim Abruf des Inserats — bitte "
                    "später erneut versuchen.")


def fehlertext(exc: BaseException) -> str:
    """Befund 122 (16.09.2026): Fehlertext fuer die Statusantwort
    (/listings/check). Nur EIGENE Sachtexte gehen an den Nutzer (Anbieter-
    fehler, Inserat weg, Tageslimit, ungueltige Adresse, Anbieter voll);
    alles andere wird zu einem festen Satz — der rohe Text steht im Log."""
    eigene: tuple = ()
    try:
        from anbieter_fehler import AnbieterFehler
        from kleinanzeigen_service import ListingGone
        from listing_identity import ListingBusy, ListingIdentityError
        from provider_fetch import TageslimitErreicht
        eigene = (AnbieterFehler, ListingGone, ListingBusy, ListingIdentityError,
                  TageslimitErreicht)
    except Exception:  # noqa: BLE001 — dann gilt jeder Text als fremd
        eigene = ()
    if eigene and isinstance(exc, eigene) and str(exc).strip():
        return str(exc)[:300]
    return FEHLER_TECHNISCH


async def _process(db, job: dict) -> None:
    """Einen beanspruchten Job ausfuehren: das Inserat in den Cache holen.
    Lease, Single-Flight und Provider-Begrenzung stecken bereits in
    get_or_fetch_listing — hier faellt nur der Job-Status."""
    # Audit 13.09.2026 (#32): Herzschlag fuer die Job-Frist, und die
    # Rueckstellung trifft nur den EIGENEN Claim — ein ueberholter Task
    # ueberschrieb vorher den Claim seines Nachfolgers mit 'queued'.
    # Claims ohne claim_id (alte Fassung beim Rollout): Verhalten wie bisher.
    # Befund 120 (16.09.2026): der Claim-Filter steht VOR den Imports — auch
    # der Import-Fehlerpfad trifft nur den eigenen Claim.
    claim_id = job.get("claim_id")
    eigener_claim = {"id": job["id"], "status": "processing"}
    if claim_id:
        eigener_claim["claim_id"] = claim_id

    # Imports in einem EIGENEN Schutzblock: schluege das Laden fehl,
    # wuerde ein "except ListingBusy" darunter selbst crashen (Name
    # unbekannt) und der Job bis zum Fristablauf in 'processing' haengen.
    try:
        from listing_identity import ListingBusy, get_or_fetch_listing
        from kleinanzeigen_service import ListingGone
        from provider_fetch import TageslimitErreicht, fetch_listing
        from routes.listings import LISTING_CACHE_TTL_HOURS
    except Exception as exc:  # noqa: BLE001
        log.error("link_jobs: Import fuer Job %s fehlgeschlagen: %s", job["id"], exc)
        await db.link_jobs.update_one(
            eigener_claim,
            {"$set": {"status": "failed", "active": False,
                      "error": FEHLER_TECHNISCH,
                      "error_intern": f"Import: {exc}"[:300],
                      "finished_at": _now(), "updated_at": _now()}})
        return

    async def _fetcher(src, iid, url):
        # Runde 19 (Nr. 28): dieselbe Konto-Bremse wie /mobile/compare und
        # /listings/resolve — der Job kennt das Konto, das ihn erzeugt hat.
        # Befund 129 (16.09.2026): ein GETEILTER Job gehoert allen wartenden
        # Konten — steht der erste Einreicher am Tageslimit, bucht das
        # naechste wartende Konto mit Kontingent (die Ablehnung faellt vor
        # dem Abruf, es wird also nie doppelt geholt).
        from routes.listings import AbrufGebremst, _AbrufSlot
        konten: list = []
        # Befund 150 (19.09.2026): Der urspruengliche Einreicher zaehlt nur,
        # solange er noch wartet. Bricht er ab ("X") und ein Kollege wartet
        # weiter, darf der Abruf nicht mehr auf sein Tageskontingent gehen.
        wartende = list(job.get("user_ids") or [])
        zuerst = job.get("requested_by_user") or ""
        for uid in ([zuerst] if zuerst in wartende else []) + wartende:
            if uid and uid not in konten:
                konten.append(uid)
        if not konten:
            konten = [""] if not wartende else []
        letzte = None
        gebremst = None
        erlaubte = 0
        for uid in konten:
            firma = await wartender_darf_abrufen(db, uid, job)
            if firma is None:
                continue
            erlaubte += 1
            konto = {"id": uid or firma or "", "dealer_id": firma}
            try:
                async with _AbrufSlot(konto):
                    return await fetch_listing(db, src, iid, url,
                                               dealer_id=firma, user_id=uid)
            except TageslimitErreicht as exc:
                letzte = exc
                continue
            except AbrufGebremst as exc:
                # Befund 152: 60/min bzw. 8 gleichzeitig sind eine Bremse
                # DIESES Kontos — der naechste Wartende hat womoeglich sofort
                # Kapazitaet. Erst wenn alle gebremst sind, geht der ganze
                # Job zurueck in die Schlange.
                gebremst = exc
                continue
        if erlaubte == 0:
            raise KeinBerechtigterWartender(
                "Der Abruf wurde nicht ausgeführt: kein wartendes Konto ist "
                "noch berechtigt (gesperrt, Abo abgelaufen oder Firma gelöscht).")
        raise letzte or gebremst

    herz = None
    if claim_id:
        herz = asyncio.create_task(_frist_verlaengern(db, job["id"], claim_id))
    try:
        try:
            await get_or_fetch_listing(db, job["url"], _fetcher,
                                       ttl_hours=LISTING_CACHE_TTL_HOURS)
        except ListingBusy:
            # Anbieter gerade voll ausgelastet — zurueck in die Schlange,
            # zaehlt nicht als Fehlversuch.
            # Pruefbericht 20.09.2026 (A-07): Vorher OHNE Wartezeit (der Job
            # kreiste sofort wieder), und eine Gesamtfrist gab es nicht — erst
            # der 24-h-TTL raeumte ab. Jetzt wachsender Abstand und nach
            # BUSY_FRIST_S ein klares Ende.
            busy = int(job.get("busy_versuche") or 0) + 1
            erstellt = _als_utc(job.get("created_at"))
            if erstellt is not None and (_now() - erstellt).total_seconds() > BUSY_FRIST_S:
                await db.link_jobs.update_one(
                    eigener_claim,
                    {"$set": {"status": "failed", "active": False, "error": BUSY_FEHLER,
                              "finished_at": _now(), "updated_at": _now()}})
                return
            await db.link_jobs.update_one(
                eigener_claim,
                {"$set": {"status": "queued", "updated_at": _now(),
                          "busy_versuche": busy,
                          "fruehestens": _now() + timedelta(seconds=min(60, 2 ** min(busy, 6)))},
                 "$unset": {"claim_id": ""},
                 "$inc": {"attempts": -1}})
            return
        except KeinBerechtigterWartender as exc:
            # Befunde 146-149: niemand darf diesen Abruf (noch) ausloesen.
            # Endgueltig: ein Wiederholungsversuch aendert daran nichts.
            log.info("link_jobs: Job %s ohne berechtigtes Konto beendet", job["id"])
            await db.link_jobs.update_one(
                eigener_claim,
                {"$set": {"status": "failed", "active": False,
                          "error": str(exc), "finished_at": _now(),
                          "updated_at": _now()}})
            return
        except TageslimitErreicht as exc:
            # Tageslimit je Konto/Firma (16.09.2026): sofort endgueltig, kein
            # weiterer Versuch — jeder Versuch wuerde nur erneut abgelehnt.
            await db.link_jobs.update_one(
                eigener_claim,
                {"$set": {"status": "failed", "active": False,
                          "error": str(exc), "finished_at": _now(),
                          "updated_at": _now()}})
            return
        except ListingGone as exc:
            # Runde 19 (Nr. 31): nur den EIGENEN Claim abschliessen
            await db.link_jobs.update_one(
                eigener_claim,
                {"$set": {"status": "failed", "active": False,
                          "error": str(exc), "finished_at": _now(),
                          "updated_at": _now()}})
            return
        except Exception as exc:  # noqa: BLE001
            endgueltig = job.get("attempts", 1) >= MAX_ATTEMPTS
            # Befund 122 (16.09.2026): nach aussen nur eigene Sachtexte — der
            # rohe Ausnahmetext (Hostnamen, Anbieterantworten, Treibermeldungen)
            # bleibt im Log und in error_intern (nicht Teil der Statusantwort).
            log.warning("link_jobs: Job %s Versuch %s fehlgeschlagen: %s",
                        job["id"], job.get("attempts", 1), str(exc)[:300])
            if endgueltig:
                await db.link_jobs.update_one(
                    eigener_claim,
                    {"$set": {"status": "failed", "active": False,
                              "error": fehlertext(exc), "error_intern": str(exc)[:300],
                              "finished_at": _now(), "updated_at": _now()}})
            else:
                neu_setzen = {"status": "queued",
                              "error": fehlertext(exc),
                              "error_intern": str(exc)[:300],
                              "updated_at": _now()}
                if _ist_tempolimit(exc) and TEMPOLIMIT_WARTEN > 0:
                    neu_setzen["fruehestens"] = _now() + timedelta(
                        seconds=TEMPOLIMIT_WARTEN)
                await db.link_jobs.update_one(
                    eigener_claim,
                    {"$set": neu_setzen, "$unset": {"claim_id": ""}})
            return
        r = await db.link_jobs.update_one(
            eigener_claim,
            {"$set": {"status": "completed", "active": False, "error": None,
                      "finished_at": _now(), "updated_at": _now()}})
        if r.matched_count == 0:
            log.warning("link_jobs: Job %s wurde waehrend des Abrufs neu vergeben — "
                        "Abschluss des ueberholten Claims verworfen", job["id"])
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
            # Nachpruefung 20.09.2026, Nr. 64: waehrend einer Schreibpause
            # (Sicherung/Restore) darf dieser Worker NICHT schreiben — sonst
            # aendert sich die Datenbank mitten im Dump und die Sicherung
            # nennt sich zu Unrecht stichtagsgenau.
            if await wartung.aktiv_async(db):
                await asyncio.sleep(5)
                continue
            laufend = {t for t in laufend if not t.done()}
            await _requeue_stale(db)
            # Lasttest 16.09.2026: freie Plaetze in EINEM Paket fuellen (statt
            # einzeln mit zwei Aggregationen je Job).
            frei = JOB_CONCURRENCY - len(laufend)
            if frei > 0:
                for job in await _claim_many(db, frei):
                    laufend.add(asyncio.create_task(_process(db, job)))
        except Exception as exc:  # noqa: BLE001
            log.warning("link job loop error: %s", exc)
        await asyncio.sleep(0.3)
