"""
listing_identity.py
===================

Robuste Erkennung von Inserats-IDs für Kleinanzeigen, mobile.de und AutoScout24
plus Cache-Logik (MongoDB), damit jedes Inserat nur einmal von der jeweiligen
Plattform abgerufen wird.

Kernidee:
---------
Niemals die komplette URL als Cache-Schlüssel verwenden – URLs enthalten
Tracking-Parameter, Suchparameter, Ref-IDs etc. Stattdessen:

    cache_key = f"{source}:{item_id}"

Dadurch wird derselbe Inserat unabhängig von der konkreten URL nur einmal
geladen.

Öffentliche API
---------------
* detect_source(url)               -> "kleinanzeigen" | "mobile" | "autoscout24" | None
* extract_kleinanzeigen_id(url)    -> str | None
* extract_mobile_id(url)           -> str | None
* extract_autoscout_id(url)        -> str | None
* get_listing_identity(url)        -> {"source", "item_id", "cache_key"}
* get_or_fetch_listing(db, url, fetcher, ttl_hours=24)
                                   -> Tuple[dict, bool]   (vehicle, was_cached)

Bonus (am Ende der Datei):
* SQLAlchemy-Referenzmodell (für Projekte mit SQL-DB)
* Optional integrierbarer FastAPI-Router (`router`) mit POST /extract
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Optional, Tuple
from urllib.parse import urlparse, parse_qs

# -----------------------------------------------------------------------------
# 1. Regex-Patterns
# -----------------------------------------------------------------------------

# Kleinanzeigen: /s-anzeige/<itemId> oder /s-anzeige/<slug>/<itemId>(-<categoryId>-<userId>)?
# Matches both:
#   /s-anzeige/3400731605
#   /s-anzeige/iphone-13-pro/3400731605-173-3405
_RE_KA = re.compile(r"/s-anzeige/(?:[^/]+/)?(\d{6,})(?:-\d+-\d+)?")

# mobile.de Variante 2 (Pretty-URL): /auto-inserat/<slug>/<itemId>.html
_RE_MOBILE_HTML = re.compile(r"/(\d{6,})\.html")

# AutoScout24: UUID irgendwo im Pfad
_RE_AS24_UUID = re.compile(
    r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
)


class ListingIdentityError(ValueError):
    """Wird geworfen, wenn keine Inserats-ID extrahiert werden konnte."""


# -----------------------------------------------------------------------------
# 2. Detection
# -----------------------------------------------------------------------------

# Erlaubte Hosts pro Quelle. SSRF-Schutz: Es wird AUSSCHLIESSLICH exakt diese
# Domain oder eine echte Subdomain davon akzeptiert. Ein Substring-Check
# ("kleinanzeigen.de" in host) waere unsicher, weil ein Angreifer eine Domain
# wie "kleinanzeigen.de.attacker.com" registrieren und den Server so dazu
# bringen koennte, eine beliebige (auch interne) Adresse server-seitig
# abzurufen (-> Cloud-Metadata 169.254.169.254, 127.0.0.1, internes Netz).
_KLEINANZEIGEN_DOMAINS = ("kleinanzeigen.de",)
_MOBILE_DOMAINS = ("mobile.de",)
_AUTOSCOUT_DOMAINS = (
    "autoscout24.de", "autoscout24.at", "autoscout24.ch", "autoscout24.com",
    "autoscout24.it", "autoscout24.fr", "autoscout24.nl", "autoscout24.be",
    "autoscout24.es", "autoscout24.lu", "autoscout24.pl",
)


def _host_matches(host: str, domains: tuple) -> bool:
    """True, wenn host exakt einer Domain entspricht ODER eine echte
    Subdomain davon ist (z.B. www./suchen./m.). Verhindert das Umgehen
    per Suffix-Trick (kleinanzeigen.de.attacker.com)."""
    return any(host == d or host.endswith("." + d) for d in domains)


def detect_source(url: str) -> Optional[str]:
    """Bestimmt die Quelle anhand des Hostnamens. None, falls nicht unterstützt.

    Strikte Host-Pruefung gegen eine Allowlist (kein Substring-Match) als
    SSRF-Schutz — siehe Kommentar an den *_DOMAINS-Konstanten.
    """
    if not url or not isinstance(url, str):
        return None
    try:
        parsed = urlparse(url)
    except Exception:
        return None
    # Nur http/https zulassen — blockt file:, gopher:, ftp: usw.
    if (parsed.scheme or "").lower() not in ("http", "https"):
        return None
    host = (parsed.hostname or "").lower()
    if not host:
        return None

    if _host_matches(host, _KLEINANZEIGEN_DOMAINS):
        return "kleinanzeigen"
    if _host_matches(host, _MOBILE_DOMAINS):
        return "mobile"
    if _host_matches(host, _AUTOSCOUT_DOMAINS):
        return "autoscout24"
    return None


# -----------------------------------------------------------------------------
# 3. Per-Source-Extraktoren
# -----------------------------------------------------------------------------

def extract_kleinanzeigen_id(url: str) -> Optional[str]:
    """
    Beispiel:
      https://www.kleinanzeigen.de/s-anzeige/mazda-cx-5/3395964748-216-7219
      -> 3395964748
    """
    if not url:
        return None
    path = urlparse(url).path or url
    m = _RE_KA.search(path)
    return m.group(1) if m else None


def extract_mobile_id(url: str) -> Optional[str]:
    """
    Zwei Varianten:
      a) https://suchen.mobile.de/fahrzeuge/details.html?id=454337945&...
         -> 454337945
      b) https://suchen.mobile.de/auto-inserat/<slug>/448651862.html
         -> 448651862
    """
    if not url:
        return None
    parsed = urlparse(url)

    # Variante a: ?id=
    qs_id = parse_qs(parsed.query).get("id", [None])[0]
    if qs_id and qs_id.isdigit():
        return qs_id

    # Variante b: /<digits>.html
    m = _RE_MOBILE_HTML.search(parsed.path or "")
    return m.group(1) if m else None


def extract_autoscout_id(url: str) -> Optional[str]:
    """
    Beispiel:
      https://www.autoscout24.de/angebote/mercedes-benz-c-180-...-d4dd34a4-1795-4bd8-a7d8-064f3b73d8f5?...
      -> d4dd34a4-1795-4bd8-a7d8-064f3b73d8f5
    """
    if not url:
        return None
    path = urlparse(url).path or ""
    m = _RE_AS24_UUID.search(path)
    return m.group(1).lower() if m else None


# -----------------------------------------------------------------------------
# 4. Vereinheitlichte Identität
# -----------------------------------------------------------------------------

_EXTRACTORS = {
    "kleinanzeigen": extract_kleinanzeigen_id,
    "mobile": extract_mobile_id,
    "autoscout24": extract_autoscout_id,
}


def get_listing_identity(url: str) -> dict:
    """
    Gibt {"source", "item_id", "cache_key"} zurück.
    Wirft ListingIdentityError, wenn nichts erkannt werden kann.
    """
    source = detect_source(url)
    if not source:
        raise ListingIdentityError(
            f"Quelle nicht erkannt: nur kleinanzeigen.de, mobile.de und "
            f"autoscout24 werden unterstützt (URL={url!r})."
        )

    item_id = _EXTRACTORS[source](url)
    if not item_id:
        raise ListingIdentityError(
            f"Konnte keine Inserats-ID aus {source}-URL extrahieren: {url!r}"
        )

    return {
        "source": source,
        "item_id": item_id,
        "cache_key": f"{source}:{item_id}",
    }


# -----------------------------------------------------------------------------
# 5. Cache-Logik (MongoDB – passt zu diesem Projekt)
# -----------------------------------------------------------------------------

# Collection-Schema (logisch):
#   listings_cache: {
#     cache_key:    "kleinanzeigen:3395964748",   # unique
#     source:       "kleinanzeigen",
#     item_id:      "3395964748",
#     url:          "<letzte gesehene URL>",
#     data:         { ... extrahierte Fahrzeugdaten ... },
#     fetched_at:   <datetime utc>,
#     expires_at:   <datetime utc>,
#     last_used_at: <datetime utc>,
#     use_count:    <int>,
#   }
#
# Empfohlene Indizes (einmalig anlegen):
#   await db.listings_cache.create_index("cache_key", unique=True)
#   await db.listings_cache.create_index([("source", 1), ("item_id", 1)],
#                                        unique=True)


class ListingBusy(RuntimeError):
    """Das Inserat wird gerade von einer anderen Anfrage geladen —
    der Aufrufer soll kurz warten und erneut anfragen (HTTP 503)."""


async def ensure_cache_indexes(db) -> None:
    """Idempotent: legt die nötigen Indizes auf der listings_cache Collection an."""
    # Altlasten raus: fruehere Versionen legten Lease-Dokumente OHNE
    # source/item_id an. So ein null/null-Relikt blockiert wegen des
    # Unique-Index jeden weiteren neuen Link — vor der Index-Anlage loeschen.
    await db.listings_cache.delete_many(
        {"$or": [{"source": None}, {"source": {"$exists": False}},
                 {"item_id": None}, {"item_id": {"$exists": False}}]})
    await db.listings_cache.create_index("cache_key", unique=True)
    await db.listings_cache.create_index(
        [("source", 1), ("item_id", 1)], unique=True, name="uniq_source_item"
    )
    # Quarantaene fuer Client-Einreichungen: EIN Eintrag je Inserat+Haendler.
    # Mongo-TTL-Index raeumt abgelaufene Eintraege selbststaendig weg.
    await db.listings_cache_client.create_index(
        [("cache_key", 1), ("dealer_id", 1)], unique=True,
        name="uniq_key_dealer")
    await db.listings_cache_client.create_index(
        "expires_at", expireAfterSeconds=0, name="ttl_expires")


def _is_fresh(doc: Optional[dict]) -> bool:
    if not doc or not doc.get("data"):
        return False
    exp = doc.get("expires_at")
    if not isinstance(exp, datetime):
        return False
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    return exp > datetime.now(timezone.utc)


async def peek_cached_listing(db, url: str,
                              dealer_id: Optional[str] = None
                              ) -> Optional[Tuple[dict, Optional[str]]]:
    """Schaut NUR im Cache nach (kein Abruf, kein Lease). Liefert
    (data, snapshot_id) bei gültigem Treffer, sonst None.

    Mit dealer_id wird zusaetzlich die QUARANTAENE des Haendlers geprueft:
    Client-Einreichungen sind zunaechst nur fuer den einreichenden Haendler
    sichtbar (globale Freigabe erst nach unabhaengiger Bestaetigung —
    siehe store_client_listing). So kann niemand mit gefaelschtem HTML
    die Daten ALLER Haendler vergiften."""
    identity = get_listing_identity(url)
    cache_key = identity["cache_key"]
    cached = await db.listings_cache.find_one({"cache_key": cache_key}, {"_id": 0})
    if _is_fresh(cached):
        await db.listings_cache.update_one(
            {"cache_key": cache_key},
            {"$inc": {"use_count": 1},
             "$set": {"last_used_at": datetime.now(timezone.utc), "url": url}})
        return cached["data"], cached.get("snapshot_id")
    if dealer_id:
        own = await db.listings_cache_client.find_one(
            {"cache_key": cache_key, "dealer_id": dealer_id}, {"_id": 0})
        if _is_fresh(own):
            return own["data"], None
    return None


def _client_core_match(a: dict, b: dict) -> bool:
    """Stimmen zwei unabhaengige Einreichungen im Kern ueberein?
    (Preis auf 1 % genau, Titel-Anfang identisch.)"""
    try:
        pa, pb = float(a.get("list_price") or 0), float(b.get("list_price") or 0)
    except (TypeError, ValueError):
        return False
    if not pa or not pb or abs(pa - pb) > 0.01 * max(pa, pb):
        return False
    ta = (a.get("title") or "").strip().lower()[:40]
    tb = (b.get("title") or "").strip().lower()[:40]
    return bool(ta) and ta == tb


async def store_client_listing(db, url: str, data: dict, dealer_id: str,
                               ttl_hours: int = 24,
                               confirmed_ttl_hours: int = 168) -> str:
    """Client-Einreichung speichern — ZUERST in Quarantaene (nur fuer den
    einreichenden Haendler sichtbar, kurze TTL). Global freigegeben wird
    ein Inserat erst, wenn ein ZWEITER, unabhaengiger Haendler dieselben
    Kerndaten einreicht (zwei fremde Browser luegen selten identisch).

    Rueckgabe: "quarantined" oder "promoted"."""
    identity = get_listing_identity(url)
    cache_key = identity["cache_key"]
    now = datetime.now(timezone.utc)
    await db.listings_cache_client.update_one(
        {"cache_key": cache_key, "dealer_id": dealer_id},
        {"$set": {"source": identity["source"], "item_id": identity["item_id"],
                  "url": url, "data": data,
                  "expires_at": now + timedelta(hours=ttl_hours)},
         "$setOnInsert": {"cache_key": cache_key, "dealer_id": dealer_id,
                          "created_at": now}},
        upsert=True)
    # Unabhaengige Bestaetigung durch einen ANDEREN Haendler?
    async for other in db.listings_cache_client.find(
            {"cache_key": cache_key, "dealer_id": {"$ne": dealer_id},
             "expires_at": {"$gt": now}}, {"_id": 0}):
        if _client_core_match(other.get("data") or {}, data):
            await db.listings_cache.update_one(
                {"cache_key": cache_key},
                {"$set": {"cache_key": cache_key,
                          "source": identity["source"],
                          "item_id": identity["item_id"],
                          "url": url, "data": data,
                          "fetched_at": now,
                          # Kuerzere TTL als Server-Abrufe: Client-Daten
                          # sind Momentaufnahmen zweier Browser, keine
                          # API-Antwort.
                          "expires_at": now + timedelta(hours=confirmed_ttl_hours),
                          "last_used_at": now,
                          "client_confirmed": True,
                          "confirmed_by": [other.get("dealer_id"), dealer_id]},
                 "$setOnInsert": {"created_at": now}},
                upsert=True)
            return "promoted"
    return "quarantined"


async def get_or_fetch_listing(
    db,
    url: str,
    fetcher: Callable[[str, str, str], Awaitable[dict]],
    ttl_hours: int = 6,
) -> Tuple[dict, bool, Optional[str]]:
    """
    Liefert (vehicle_data, was_cached, cached_snapshot_id).

    Ablauf:
      1. ID erkennen (sonst ListingIdentityError).
      2. Cache lesen. Wenn vorhanden & nicht abgelaufen -> aus DB liefern,
         use_count++ und last_used_at aktualisieren. Liefert auch die
         zuletzt gespeicherte snapshot_id zurück, damit der Caller den
         vorhandenen Beweis wiederverwenden kann.
      3. Sonst fetcher(source, item_id, url) aufrufen und Ergebnis speichern.

    `fetcher` ist eine async-Funktion (source, item_id, url) -> dict.
    """
    identity = get_listing_identity(url)
    source = identity["source"]
    item_id = identity["item_id"]
    cache_key = identity["cache_key"]
    now = datetime.now(timezone.utc)

    cached = await db.listings_cache.find_one({"cache_key": cache_key}, {"_id": 0})
    if cached:
        expires_at = cached.get("expires_at")
        # In Mongo kommt expires_at als datetime zurück (sofern als datetime gespeichert).
        if isinstance(expires_at, datetime):
            # Ensure timezone-aware comparison
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at > now:
                await db.listings_cache.update_one(
                    {"cache_key": cache_key},
                    {
                        "$inc": {"use_count": 1},
                        "$set": {"last_used_at": now, "url": url},
                    },
                )
                return cached["data"], True, cached.get("snapshot_id")

    # MISS oder abgelaufen -> Single-Flight: nur EINE Anfrage ruft wirklich
    # ab; gleichzeitige Anfragen derselben URL warten auf deren Ergebnis.
    # Verhindert Doppel-Scrapes (Bot-Block-Risiko) und Doppel-Snapshots,
    # wenn z.B. 5 Sucher zeitgleich dasselbe Inserat vergleichen.
    import asyncio as _aio
    from pymongo.errors import DuplicateKeyError

    async def _fresh_cached():
        c = await db.listings_cache.find_one({"cache_key": cache_key}, {"_id": 0})
        if not c or not c.get("data"):
            return None
        exp = c.get("expires_at")
        if isinstance(exp, datetime):
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if exp > datetime.now(timezone.utc):
                return c
        return None

    got_lease = False
    for _wait in range(20):                    # max. ~30 s warten, dann klare Meldung
        lease_now = datetime.now(timezone.utc)
        try:
            await db.listings_cache.update_one(
                {"cache_key": cache_key,
                 "$or": [{"fetching_until": {"$exists": False}},
                         {"fetching_until": None},
                         {"fetching_until": {"$lt": lease_now}}]},
                {"$set": {"fetching_until": lease_now + timedelta(seconds=90)},
                 # WICHTIG: source/item_id MUESSEN schon beim Lease gesetzt
                 # werden. Ohne sie legt der Upsert ein Dokument mit
                 # source=null/item_id=null an — und der Unique-Index
                 # uniq_source_item laesst nur EIN null/null-Paar zu. Folge
                 # (vor diesem Fix): 100 VERSCHIEDENE neue Links blockierten
                 # sich gegenseitig ~70 s und endeten im Fehler.
                 "$setOnInsert": {"cache_key": cache_key, "source": source,
                                  "item_id": item_id, "url": url,
                                  "created_at": lease_now}},
                upsert=True,
            )
            got_lease = True
        except DuplicateKeyError:
            got_lease = False                  # jemand anderes laedt gerade
        if got_lease:
            break
        await _aio.sleep(1.5)
        c = await _fresh_cached()
        if c:                                  # der Erste ist fertig - uebernehmen
            await db.listings_cache.update_one(
                {"cache_key": cache_key},
                {"$inc": {"use_count": 1},
                 "$set": {"last_used_at": datetime.now(timezone.utc)}})
            return c["data"], True, c.get("snapshot_id")
    if not got_lease:
        raise ListingBusy(
            "Das Inserat wird gerade von einer anderen Anfrage geladen - "
            "bitte in ein paar Sekunden erneut versuchen.")

    # ZENTRALE PROVIDER-BEGRENZUNG: bevor wirklich extern abgerufen wird,
    # einen Slot belegen (MongoDB — wirkt ueber ALLE Worker/Server). So
    # loesen 300 gleichzeitige Nutzer nicht 300 externe Abrufe aus,
    # sondern hoechstens MAX_CONCURRENT_<QUELLE> — der Rest wartet kurz
    # oder bekommt eine freundliche "bitte gleich nochmal"-Antwort.
    from provider_limiter import acquire_slot, extend_slot, release_slot
    slot_id = None
    for _try in range(20):                     # max. ~30 s auf einen Slot warten
        slot_id = await acquire_slot(db, source)
        if slot_id:
            break
        await _aio.sleep(1.5)
    if not slot_id:
        await db.listings_cache.update_one(
            {"cache_key": cache_key}, {"$set": {"fetching_until": None}})
        raise ListingBusy(
            "Gerade werden viele Inserate gleichzeitig geladen - "
            "bitte in ein paar Sekunden erneut versuchen.")

    async def _extend_lease_forever():
        # Herzschlag: solange der Provider-Abruf laeuft, bleiben Lease UND
        # Provider-Slot gueltig — kein zweiter Prozess uebernimmt mittendrin.
        while True:
            await _aio.sleep(30)
            await db.listings_cache.update_one(
                {"cache_key": cache_key},
                {"$set": {"fetching_until":
                          datetime.now(timezone.utc) + timedelta(seconds=90)}})
            await extend_slot(db, slot_id)

    _heartbeat = _aio.create_task(_extend_lease_forever())
    # Buchfuehrung: wie viele ECHTE externe Abrufe je Quelle und Tag —
    # damit laesst sich providerfreundliches Verhalten jederzeit belegen.
    try:
        await db.provider_stats.update_one(
            {"provider": source,
             "date": datetime.now(timezone.utc).strftime("%Y-%m-%d")},
            {"$inc": {"calls": 1}}, upsert=True)
    except Exception:
        pass
    try:
        data = await fetcher(source, item_id, url)
    except Exception:
        # Lease freigeben, damit der naechste Versuch nicht 90 s warten muss.
        _heartbeat.cancel()
        await db.listings_cache.update_one(
            {"cache_key": cache_key}, {"$set": {"fetching_until": None}})
        raise
    finally:
        _heartbeat.cancel()
        await release_slot(db, slot_id, provider=source)
    if not isinstance(data, dict):
        await db.listings_cache.update_one(
            {"cache_key": cache_key}, {"$set": {"fetching_until": None}})
        raise RuntimeError(
            f"fetcher für {source}:{item_id} hat kein dict zurückgegeben."
        )

    expires_at = now + timedelta(hours=ttl_hours)
    await db.listings_cache.update_one(
        {"cache_key": cache_key},
        {
            "$set": {
                "cache_key": cache_key,
                "source": source,
                "item_id": item_id,
                "url": url,
                "data": data,
                "fetched_at": now,
                "expires_at": expires_at,
                "last_used_at": now,
                # Beim Re-Fetch (TTL abgelaufen) muss ein alter Snapshot
                # als ungültig gelten — der Caller erzeugt direkt einen
                # neuen. Vorher löschen verhindert, dass nach dem
                # Re-Fetch versehentlich noch auf den alten Schnappschuss
                # verwiesen wird, falls der Caller das snapshot_id-
                # Speichern (set_cache_snapshot) auslassen sollte.
                "snapshot_id": None,
                "fetching_until": None,
            },
            "$inc": {"use_count": 1},
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )
    return data, False, None


async def set_cache_snapshot(db, url: str, snapshot_id: str) -> None:
    """Verknüpft einen frisch erzeugten Snapshot mit dem Cache-Eintrag, so
    dass Folge-Aufrufe innerhalb der TTL den bestehenden Beweis
    wiederverwenden können. Fehlerhaft/unerkannte URLs werden stumm
    ignoriert (kein harter Fehler im Hot-Path)."""
    try:
        identity = get_listing_identity(url)
    except ListingIdentityError:
        return
    await db.listings_cache.update_one(
        {"cache_key": identity["cache_key"]},
        {"$set": {"snapshot_id": snapshot_id}},
    )


# -----------------------------------------------------------------------------
# 6. SQLAlchemy-Referenzmodell (nur zur Dokumentation – dieses Projekt nutzt Mongo)
# -----------------------------------------------------------------------------
#
# from sqlalchemy import (
#     Column, String, DateTime, Integer, JSON, UniqueConstraint, Index, func,
# )
# from sqlalchemy.orm import declarative_base
#
# Base = declarative_base()
#
# class ListingCache(Base):
#     __tablename__ = "listings_cache"
#
#     id           = Column(Integer, primary_key=True, autoincrement=True)
#     source       = Column(String(32),  nullable=False)
#     item_id      = Column(String(64),  nullable=False)
#     cache_key    = Column(String(128), nullable=False, unique=True)
#     url          = Column(String(1024), nullable=False)
#     data         = Column(JSON,        nullable=False)
#
#     fetched_at   = Column(DateTime(timezone=True), server_default=func.now())
#     expires_at   = Column(DateTime(timezone=True), nullable=False)
#     last_used_at = Column(DateTime(timezone=True), server_default=func.now())
#     use_count    = Column(Integer, nullable=False, default=0)
#
#     __table_args__ = (
#         UniqueConstraint("source", "item_id", name="uq_listing_source_item"),
#         Index("ix_listing_cache_key", "cache_key"),
#     )
#
# -----------------------------------------------------------------------------
# 7. Optionaler FastAPI-Router – kann in server.py eingebunden werden
# -----------------------------------------------------------------------------

try:
    from fastapi import APIRouter, HTTPException
    from pydantic import BaseModel, Field

    class ExtractIn(BaseModel):
        url: str = Field(..., description="Kleinanzeigen-, mobile.de- oder AutoScout24-URL")

    router = APIRouter(prefix="/listings", tags=["listings"])

    @router.post("/extract")
    async def extract_endpoint(body: ExtractIn):
        """Liefert nur die Identität (source / item_id / cache_key) – ohne Fetch."""
        try:
            return get_listing_identity(body.url)
        except ListingIdentityError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

except Exception:  # FastAPI nicht verfügbar -> nur Funktionen exportieren
    router = None  # type: ignore


__all__ = [
    "ListingIdentityError",
    "detect_source",
    "extract_kleinanzeigen_id",
    "extract_mobile_id",
    "extract_autoscout_id",
    "get_listing_identity",
    "get_or_fetch_listing",
    "set_cache_snapshot",
    "ensure_cache_indexes",
    "ListingBusy",
    "store_client_listing",
    "router",
]
