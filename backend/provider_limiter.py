"""Zentrale Begrenzung gleichzeitiger Provider-Abrufe (Kleinanzeigen/mobile.de).

Problem: 300 Sucher, die gleichzeitig NEUE Links vergleichen, wuerden ohne
Begrenzung 300 gleichzeitige externe Abrufe ausloesen — Kleinanzeigen wuerde
den Server sperren, und die mobile.de-API hat Vertragslimits.

Loesung: ein ATOMARER Zaehler je Quelle in MongoDB (find_one_and_update mit
Bedingung active < limit) — wirkt ueber ALLE Worker-Prozesse und Server
hinweg und laesst auch unter hunderten gleichzeitigen Anfragen exakt
`limit` Abrufe durch. Zusaetzlich je Abruf ein Slot-Dokument mit
Ablaufdatum: stirbt ein Prozess mitten im Abruf, erkennt die Selbstheilung
den veralteten Slot und korrigiert den Zaehler.

Audit 13.09.2026 (#33): Invariante "es gibt mindestens so viele Slot-
Dokumente wie der Zaehler angibt". Belegen legt ZUERST den Slot an und zaehlt
DANN hoch, Freigeben zaehlt ZUERST herunter und loescht DANN den Slot. Die
Selbstheilung korrigiert nur gegen einen unveraenderten Zaehlerstand (Feld
`rev`, das jede Zaehleraenderung hochzaehlt). Vorher konnte die Selbstheilung
den Zaehler zwischen "$inc" und "insert" eines Belegens auf die (noch zu
kleine) Slot-Zahl setzen — dann liefen kurzzeitig mehr Abrufe als erlaubt.
"""
import logging
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

log = logging.getLogger("autohandel")

# Gleichzeitige externe Abrufe JE QUELLE, ueber alle Prozesse/Server gesamt.
PROVIDER_MAX_CONCURRENT = {
    "kleinanzeigen": int(os.environ.get("MAX_CONCURRENT_KLEINANZEIGEN", "3")),
    # Runde 29 (12.09.2026): Der Weg ueber die API ist ein bezahlter Dienst,
    # kein Abgreifen der Webseite — er darf deutlich mehr gleichzeitig. Die
    # API selbst erlaubt 600 Anfragen je Minute (gemessen), 8 gleichzeitige
    # Anfragen beantwortet sie in 0,56 s. Der eigene Abruf bleibt streng
    # begrenzt, damit wir Kleinanzeigen nicht belasten.
    "kleinanzeigen_api": int(os.environ.get("MAX_CONCURRENT_KLEINANZEIGEN_API", "8")),
    "mobile": int(os.environ.get("MAX_CONCURRENT_MOBILE", "10")),
    "autoscout24": int(os.environ.get("MAX_CONCURRENT_AUTOSCOUT", "3")),
}
# Nach so vielen Sekunden gilt ein Slot als verwaist (Prozess abgestuerzt).
SLOT_TTL_SECONDS = int(os.environ.get("PROVIDER_SLOT_TTL", "120"))
# Mindestabstand zwischen zwei Reparaturlaeufen je Quelle.
HEAL_INTERVAL_SECONDS = int(os.environ.get("PROVIDER_HEAL_INTERVAL", "30"))


# Der eindeutige Index auf provider_limits.provider ist die einzige
# Absicherung dagegen, dass zwei gleichzeitige Erst-Anfragen ZWEI
# Zaehler-Dokumente derselben Quelle anlegen — dann wuerde das Limit
# doppelt gelten. Deshalb legt der Limiter ihn selbst an, statt sich auf
# die Startroutine zu verlassen.
#
# Runde 31 (12.09.2026): Der Merker galt frueher je PROZESS (ein bool).
# Sobald irgendwer ihn gesetzt hatte, uebersprang jede spaetere Datenbank
# das Anlegen — und ohne den eindeutigen Index entstanden bei
# gleichzeitigen Erst-Anfragen mehrere Zaehler je Quelle, das Limit galt
# dann doppelt. Sichtbar wurde das in der Suite: 14 gleichzeitige Abrufe
# bei erlaubten 8. Der Merker haengt jetzt am Datenbanknamen.
_indexes_ready: set = set()
# Audit 13.09.2026 (#34): Scheiterte der Aufbau, wurde er bei JEDEM Belegen
# erneut versucht — ohne ein einziges Protokoll. Jetzt: Warnung plus
# Betriebsalarm, Neuversuch je Datenbank hoechstens alle
# INDEX_NEUVERSUCH_SEKUNDEN. Nur nach einem FEHLSCHLAG gedrosselt — eine
# neue Datenbank versucht es sofort (sonst kaeme der Runde-31-Fehler zurueck).
_fehlversuch: dict = {}
INDEX_NEUVERSUCH_SEKUNDEN = 60


def _db_marke(db) -> str:
    try:
        return f"{id(getattr(db, 'client', None))}:{db.name}"
    except Exception:  # noqa: BLE001 — im Zweifel immer neu anlegen
        return ""


async def _zaehler_dubletten_bereinigen(db) -> int:
    """Audit 13.09.2026 (#34): Mehrere Zaehler-Dokumente derselben Quelle
    (Altbestand aus einer Zeit ohne Index) verhindern den eindeutigen Index —
    und jedes Dokument vergaebe sein eigenes Limit. Die Zaehler sind reine
    Laufzeit-Buchhaltung: das aelteste Dokument bleibt, sein Stand wird auf
    die Zahl der Slots gesetzt, die uebrigen werden geloescht. Idempotent;
    liefert die Zahl der entfernten Dokumente."""
    entfernt = 0
    async for gruppe in db.provider_limits.aggregate([
            {"$group": {"_id": "$provider", "ids": {"$push": "$_id"},
                        "n": {"$sum": 1}}},
            {"$match": {"n": {"$gt": 1}}}]):
        ids = sorted(gruppe["ids"], key=str)
        behalten, weg = ids[0], ids[1:]
        r = await db.provider_limits.delete_many({"_id": {"$in": weg}})
        slots = await db.provider_slots.count_documents({"provider": gruppe["_id"]})
        await db.provider_limits.update_one(
            {"_id": behalten}, {"$set": {"active": slots}, "$inc": {"rev": 1}})
        entfernt += r.deleted_count
        log.warning("provider_limiter: %d doppelte Zaehler fuer %r entfernt "
                    "(Stand auf %d Slots gesetzt)", r.deleted_count,
                    gruppe["_id"], slots)
    return entfernt


async def ensure_slot_indexes(db) -> None:
    await db.provider_slots.create_index("expires_at", expireAfterSeconds=0,
                                         name="ttl_expires")
    await db.provider_slots.create_index("provider", name="by_provider")
    try:
        await db.provider_limits.create_index("provider", unique=True,
                                              name="uniq_provider")
    except Exception:
        # Audit 13.09.2026 (#34): Dubletten automatisch zusammenlegen, dann
        # genau einmal erneut — auch wenn ein paralleler Aufruf sie schon
        # entfernt hat. Jeder andere Fehler (z.B. Namenskonflikt) scheitert
        # dabei erneut und geht nach oben.
        await _zaehler_dubletten_bereinigen(db)
        await db.provider_limits.create_index("provider", unique=True,
                                              name="uniq_provider")
    for provider in PROVIDER_MAX_CONCURRENT:
        await db.provider_limits.update_one(
            {"provider": provider},
            {"$setOnInsert": {"provider": provider, "active": 0}},
            upsert=True)
    marke = _db_marke(db)
    if marke:
        _indexes_ready.add(marke)
        _fehlversuch.pop(marke, None)
    from betrieb import alarm_schliessen
    await alarm_schliessen(db, "anbieter_grenze_index_fehlt", ref="provider_limits")


async def _indizes_sicherstellen(db) -> None:
    """Selbstversorgung beim Belegen: ohne den eindeutigen Index koennten
    gleichzeitige Erst-Anfragen mehrere Zaehler je Quelle anlegen und das
    Limit vervielfachen. Fail-open: ein Fehler bremst keinen Abruf, wird aber
    gemeldet und gedrosselt wiederholt (Audit 13.09.2026, #34)."""
    marke = _db_marke(db)
    if marke in _indexes_ready:
        return
    zuletzt = _fehlversuch.get(marke)
    if zuletzt is not None and time.monotonic() - zuletzt < INDEX_NEUVERSUCH_SEKUNDEN:
        return
    try:
        await ensure_slot_indexes(db)
    except Exception as exc:  # noqa: BLE001
        if marke and marke in _indexes_ready:
            return  # ein paralleler Aufruf war erfolgreich
        _fehlversuch[marke] = time.monotonic()
        log.warning("provider_limiter: Indexaufbau fehlgeschlagen (%s) — "
                    "Begrenzung laeuft ohne Absicherung weiter, naechster "
                    "Versuch in %d s", exc, INDEX_NEUVERSUCH_SEKUNDEN)
        from betrieb import alarm
        await alarm(db, "anbieter_grenze_index_fehlt", ref="provider_limits",
                    fehler=str(exc)[:300],
                    hinweis="Eindeutiger Index provider_limits.provider fehlt — "
                            "das Abruf-Limit kann mehrfach gelten.")


async def _heal_stale(db, provider: str) -> None:
    """Zaehler mit der Zahl der tatsaechlich frischen Slots abgleichen —
    repariert Slots von abgestuerzten Prozessen.

    Gedrosselt: hoechstens alle HEAL_INTERVAL_SECONDS je Quelle. Ohne diese
    Bremse liefe die Reparatur bei jedem einzelnen fehlgeschlagenen
    Belegungsversuch — unter Last hunderte Male pro Sekunde."""
    now = datetime.now(timezone.utc)
    # Audit 13.09.2026 (#33): Der Zaehlerstand (active, rev) wird ZUERST
    # gelesen — hier gleich mit dem Drossel-Merker, der das Dokument vor der
    # Aenderung liefert — und erst DANACH werden die Slots gezaehlt. Vorher
    # umgekehrt: ein vollstaendiges Belegen zwischen beiden Lesevorgaengen
    # ergab active > Slots, und die Korrektur senkte den Zaehler zu tief.
    darf = await db.provider_limits.find_one_and_update(
        {"provider": provider,
         "$or": [{"heal_bis": {"$exists": False}}, {"heal_bis": None},
                 {"heal_bis": {"$lt": now}}]},
        {"$set": {"heal_bis": now + timedelta(seconds=HEAL_INTERVAL_SECONDS)}})
    if not darf:
        return
    stale_active = darf.get("active", 0)
    # Slots, deren Freigabe gerade laeuft (freigegeben, Zaehler noch nicht
    # heruntergezaehlt), NICHT wegraeumen — sonst zaehlte die Freigabe
    # anschliessend noch einmal ab.
    await db.provider_slots.delete_many(
        {"provider": provider, "expires_at": {"$lt": now},
         "freigegeben": {"$ne": True}})
    fresh = await db.provider_slots.count_documents({"provider": provider})
    if stale_active > fresh:
        # Nur korrigieren, wenn der Zaehler seit dem Lesen unveraendert ist
        # (kein anderer Prozess dazwischenfunkt) — sonst naechster Versuch.
        # rev None trifft auch Altdokumente ohne das Feld.
        await db.provider_limits.update_one(
            {"provider": provider, "active": stale_active, "rev": darf.get("rev")},
            {"$set": {"active": fresh}, "$inc": {"rev": 1}})


async def _slot_verwerfen(db, slot_id: str) -> None:
    try:
        await db.provider_slots.delete_one({"id": slot_id})
    except Exception:  # noqa: BLE001 — die TTL raeumt ihn spaeter weg
        pass


async def acquire_slot(db, provider: str) -> Optional[str]:
    """Versucht, einen Abruf-Slot zu belegen. Liefert die Slot-ID oder None
    (Limit erreicht). Kein Warten — das macht der Aufrufer."""
    limit = PROVIDER_MAX_CONCURRENT.get(provider, 3)
    await _indizes_sicherstellen(db)
    now = datetime.now(timezone.utc)
    # Audit 13.09.2026 (#33): billiger Lesetest zuerst — sonst kostete jeder
    # der bis zu hundert Wartenden im 0,3-s-Takt ein insert plus delete.
    stand = await db.provider_limits.find_one(
        {"provider": provider}, {"_id": 0, "active": 1})
    if stand is None:
        await db.provider_limits.update_one(
            {"provider": provider},
            {"$setOnInsert": {"provider": provider, "active": 0}},
            upsert=True)
        return None
    if (stand.get("active") or 0) >= limit:
        # Voll — oder Zaehler haengt wegen eines abgestuerzten Prozesses.
        await _heal_stale(db, provider)
        return None
    # ZUERST der Slot, DANN der Zaehler (Invariante siehe Modulkopf).
    slot_id = uuid.uuid4().hex
    await db.provider_slots.insert_one({
        "id": slot_id, "provider": provider,
        "created_at": now,
        "expires_at": now + timedelta(seconds=SLOT_TTL_SECONDS)})
    try:
        res = await db.provider_limits.find_one_and_update(
            {"provider": provider, "active": {"$lt": limit}},
            {"$inc": {"active": 1, "rev": 1}})
    except Exception:
        await _slot_verwerfen(db, slot_id)
        raise
    if not res:
        # Zwischen Lesetest und Hochzaehlen voll geworden: Slot zurueck.
        await _slot_verwerfen(db, slot_id)
        return None
    return slot_id


async def release_slot(db, slot_id: Optional[str],
                       provider: Optional[str] = None) -> None:
    """Slot freigeben. Der Provider steht im Slot-Dokument — er muss NICHT
    uebergeben werden. `provider` bleibt nur fuer bestehende Aufrufer stehen.

    Audit 13.09.2026 (#33): Heruntergezaehlt wird nur noch, wenn DIESER Aufruf
    den Slot als erster freigibt. Ist das Dokument schon weg (TTL bzw.
    Selbstheilung), hat die Selbstheilung den Zaehler bereits gegen die
    Slot-Zahl korrigiert — ein zweiter Abzug senkte ihn zu tief."""
    if not slot_id:
        return
    try:
        doc = await db.provider_slots.find_one_and_update(
            {"id": slot_id, "freigegeben": {"$ne": True}},
            {"$set": {"freigegeben": True}})
        if not doc:
            return
        name = doc.get("provider") or provider
        if name:
            await db.provider_limits.update_one(
                {"provider": name, "active": {"$gt": 0}},
                {"$inc": {"active": -1, "rev": 1}})
        await db.provider_slots.delete_one({"id": slot_id})
    except Exception:
        pass  # Selbstheilung in acquire_slot korrigiert notfalls


async def extend_slot(db, slot_id: str) -> None:
    """Slot verlaengern, solange der Abruf noch laeuft (Herzschlag)."""
    await db.provider_slots.update_one(
        {"id": slot_id},
        {"$set": {"expires_at": datetime.now(timezone.utc)
                  + timedelta(seconds=SLOT_TTL_SECONDS)}})
