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
"""
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from pymongo import ReturnDocument

# Gleichzeitige externe Abrufe JE QUELLE, ueber alle Prozesse/Server gesamt.
PROVIDER_MAX_CONCURRENT = {
    "kleinanzeigen": int(os.environ.get("MAX_CONCURRENT_KLEINANZEIGEN", "3")),
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
# doppelt gelten. Deshalb legt der Limiter ihn selbst an (einmal je
# Prozess), statt sich auf die Startroutine zu verlassen.
_indexes_ready = False


async def ensure_slot_indexes(db) -> None:
    global _indexes_ready
    await db.provider_slots.create_index("expires_at", expireAfterSeconds=0,
                                         name="ttl_expires")
    await db.provider_slots.create_index("provider", name="by_provider")
    await db.provider_limits.create_index("provider", unique=True,
                                          name="uniq_provider")
    for provider in PROVIDER_MAX_CONCURRENT:
        await db.provider_limits.update_one(
            {"provider": provider},
            {"$setOnInsert": {"provider": provider, "active": 0}},
            upsert=True)
    _indexes_ready = True


async def _heal_stale(db, provider: str) -> None:
    """Zaehler mit der Zahl der tatsaechlich frischen Slots abgleichen —
    repariert Slots von abgestuerzten Prozessen.

    Gedrosselt: hoechstens alle HEAL_INTERVAL_SECONDS je Quelle. Ohne diese
    Bremse liefe die Reparatur bei jedem einzelnen fehlgeschlagenen
    Belegungsversuch — unter Last hunderte Male pro Sekunde."""
    now = datetime.now(timezone.utc)
    darf = await db.provider_limits.find_one_and_update(
        {"provider": provider,
         "$or": [{"heal_bis": {"$exists": False}}, {"heal_bis": None},
                 {"heal_bis": {"$lt": now}}]},
        {"$set": {"heal_bis": now + timedelta(seconds=HEAL_INTERVAL_SECONDS)}})
    if not darf:
        return
    await db.provider_slots.delete_many(
        {"provider": provider, "expires_at": {"$lt": now}})
    fresh = await db.provider_slots.count_documents({"provider": provider})
    doc = await db.provider_limits.find_one({"provider": provider})
    stale_active = (doc or {}).get("active", 0)
    if stale_active > fresh:
        # Nur korrigieren, wenn der Zaehler seit dem Lesen unveraendert ist
        # (kein anderer Prozess dazwischenfunkt) — sonst naechster Versuch.
        await db.provider_limits.update_one(
            {"provider": provider, "active": stale_active},
            {"$set": {"active": fresh}})


async def acquire_slot(db, provider: str) -> Optional[str]:
    """Versucht, einen Abruf-Slot zu belegen. Liefert die Slot-ID oder None
    (Limit erreicht). Kein Warten — das macht der Aufrufer."""
    limit = PROVIDER_MAX_CONCURRENT.get(provider, 3)
    if not _indexes_ready:
        # Selbstversorgung: ohne den eindeutigen Index koennten gleichzeitige
        # Erst-Anfragen mehrere Zaehler je Quelle anlegen und das Limit
        # vervielfachen.
        try:
            await ensure_slot_indexes(db)
        except Exception:
            pass
    now = datetime.now(timezone.utc)
    res = await db.provider_limits.find_one_and_update(
        {"provider": provider, "active": {"$lt": limit}},
        {"$inc": {"active": 1}},
        return_document=ReturnDocument.AFTER)
    if not res:
        # Voll — oder Zaehler haengt wegen eines abgestuerzten Prozesses.
        seeded = await db.provider_limits.find_one({"provider": provider})
        if seeded is None:
            await db.provider_limits.update_one(
                {"provider": provider},
                {"$setOnInsert": {"provider": provider, "active": 0}},
                upsert=True)
        else:
            await _heal_stale(db, provider)
        return None
    slot_id = uuid.uuid4().hex
    try:
        await db.provider_slots.insert_one({
            "id": slot_id, "provider": provider,
            "created_at": now,
            "expires_at": now + timedelta(seconds=SLOT_TTL_SECONDS)})
    except Exception:
        # Zaehler war schon erhoeht — ohne Rueckgabe wuerde er dauerhaft
        # eine Einheit Kapazitaet verlieren.
        await db.provider_limits.update_one(
            {"provider": provider, "active": {"$gt": 0}},
            {"$inc": {"active": -1}})
        raise
    return slot_id


async def release_slot(db, slot_id: Optional[str],
                       provider: Optional[str] = None) -> None:
    """Slot freigeben. Der Provider steht im Slot-Dokument — er muss NICHT
    uebergeben werden (ein vergessenes Argument haette den Zaehler still
    leckgeschlagen). `provider` dient nur als Rueckfallwert, falls die
    TTL-Bereinigung das Dokument bereits entfernt hat."""
    if not slot_id:
        return
    try:
        doc = await db.provider_slots.find_one_and_delete({"id": slot_id})
        name = (doc or {}).get("provider") or provider
        if name:
            # Auch ohne Dokument dekrementieren: hat die TTL den Slot
            # entfernt, waehrend der Abruf noch lief, bliebe der Zaehler
            # sonst dauerhaft zu hoch (verlorene Kapazitaet).
            await db.provider_limits.update_one(
                {"provider": name, "active": {"$gt": 0}},
                {"$inc": {"active": -1}})
    except Exception:
        pass  # Selbstheilung in acquire_slot korrigiert notfalls


async def extend_slot(db, slot_id: str) -> None:
    """Slot verlaengern, solange der Abruf noch laeuft (Herzschlag)."""
    await db.provider_slots.update_one(
        {"id": slot_id},
        {"$set": {"expires_at": datetime.now(timezone.utc)
                  + timedelta(seconds=SLOT_TTL_SECONDS)}})
