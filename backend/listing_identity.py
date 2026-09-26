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
* get_or_fetch_listing(db, url, fetcher, ttl_hours=24, dealer_id="", frist_sekunden=None)
                                   -> Tuple[dict, bool]   (vehicle, was_cached)

Bonus (am Ende der Datei):
* SQLAlchemy-Referenzmodell (für Projekte mit SQL-DB)
* Optional integrierbarer FastAPI-Router (`router`) mit POST /extract
"""

from __future__ import annotations

import logging
import os
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Optional, Tuple
from urllib.parse import urlparse, parse_qs

from konfig import zahl_env

_log = logging.getLogger("autohandel")

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


URL_MAX_LAENGE = 2048


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


# Rollenprüfung 22.09.2026 (RP-409): "Teilen" aus der Kleinanzeigen- bzw.
# mobile.de-App liefert Text MIT Link ("Schau mal: https://…"). Der ganze Text
# ging als Adresse durch — urlparse fand kein Schema, jeder solche Link endete
# mit 400. Aus einem Text wird deshalb die erste unterstuetzte Inserats-Adresse
# gezogen; Satzzeichen am Ende gehoeren nicht zur Adresse.
_RE_URL_IM_TEXT = re.compile(r"https?://[^\s<>\"'“”„«»]+", re.IGNORECASE)
_URL_ENDE_WEG = ".,;:!?)]}>'\"“”„«»"


def inserats_url_aus_text(text):
    """Reine Adresse -> unveraendert (nur getrimmt). Text mit Adresse -> die
    erste Adresse einer unterstuetzten Quelle (ohne Satzzeichen am Ende).
    Nichts gefunden -> der getrimmte Text (die Pruefung meldet dann den Fehler)."""
    if not isinstance(text, str):
        return text
    t = text.strip()
    if not t or re.fullmatch(r"(?i)https?://\S+", t):
        return t
    for m in _RE_URL_IM_TEXT.finditer(t):
        kandidat = m.group(0).rstrip(_URL_ENDE_WEG)
        if detect_source(kandidat):
            return kandidat
    return t


def get_listing_identity(url: str) -> dict:
    """
    Gibt {"source", "item_id", "cache_key"} zurück.
    Wirft ListingIdentityError, wenn nichts erkannt werden kann.
    """
    # Runde 15 (Nr. 6): fail-fast fuer alle Aufrufer (Routen, Link-Jobs,
    # Erweiterungs-Ingest) — auch die Fehlermeldung unten zitiert die URL.
    if not isinstance(url, str) or len(url) > URL_MAX_LAENGE:
        raise ListingIdentityError(
            f"Adresse zu lang (max. {URL_MAX_LAENGE} Zeichen)")
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
    # Rollenprüfung 22.09.2026 (RP-205/RP-356): Angenommen wurden alle
    # AutoScout-Laender (.com/.it/.fr/.nl ...), der Abruf-Dienst liest aber nur
    # Inserate unter "/angebote/" (autoscout_service.detail_looks_like_
    # autoscout_listing). Alles andere lief in drei Job-Versuche und endete als
    # "Technischer Fehler". Jetzt sofort eine klare Meldung (HTTP 400).
    if source == "autoscout24" and "/angebote/" not in (urlparse(url).path or "").lower():
        raise ListingIdentityError(
            "Dieser AutoScout24-Link kann nicht gelesen werden: Bitte den Link eines "
            "einzelnen Inserats von autoscout24.de oder autoscout24.at verwenden "
            "(Adresse mit „/angebote/“). Auslandsseiten (/offers/, /annunci/ …) "
            "werden nicht unterstützt.")

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


# Pruefbericht 20.09.2026 (A-17): EINE Gesamtfrist je Abruf — Lease-Warten,
# Anbieter-Platz, Apify-Topf und der Abruf selbst zaehlen dagegen. Vorher
# addierten sich die Phasen (bis zu 30 + 30 + 30 + 180 s), das Frontend hatte
# laengst aufgegeben und die Plaetze blieben belegt. Standard 90 s: unter dem
# Cloudflare-Abbruch (~100 s) und dem Frontend-Limit (95 s, DP-04).
ABRUF_GESAMT_SEKUNDEN = zahl_env("ABRUF_GESAMT_SEKUNDEN", 90, unten=10)
# A-06: Hoechstdauer des Lease-Herzschlags (Sicherheitsnetz hinter der Frist;
# wie link_jobs.HERZSCHLAG_MAX_SEKUNDEN). Danach laufen Lease und Plaetze aus,
# die Selbstheilung greift.
LEASE_HERZSCHLAG_MAX_SEKUNDEN = zahl_env("LEASE_HERZSCHLAG_MAX", 300, unten=60)
# Hoechstens so lange auf die Lease bzw. auf Anbieter-Plaetze warten (wie
# bisher ~30 s) — und nie ueber die Gesamtfrist hinaus.
LEASE_WARTEN_SEKUNDEN = 30
SLOT_WARTEN_SEKUNDEN = 30
# Bleibt weniger Frist als das, wird kein Abruf mehr gestartet (der Anbieter
# braucht selbst Sekunden; ein abgebrochener Apify-Lauf kostet trotzdem).
ABRUF_MINDESTREST_SEKUNDEN = 15
ABRUF_ZU_LANGE_TEXT = "Abruf dauert zu lange — bitte in einer Minute erneut versuchen."


class AbrufDauertZuLange(ListingBusy):
    """Zeitueberschreitung (DP-04: Apify-Lauf; A-17: Gesamtfrist) — dieselbe
    freundliche 503-Antwort wie ListingBusy.

    hintergrund=True: der Abruf laeuft im Hintergrund zu Ende und landet im
    Speicher — ein Link-Job darf einfach spaeter nachsehen (zaehlt nicht als
    Fehlversuch). hintergrund=False: der Anbieter selbst war zu langsam — im
    Job zaehlt das als Versuch (jeder Lauf kostet)."""

    def __init__(self, text: str = ABRUF_ZU_LANGE_TEXT, hintergrund: bool = False):
        super().__init__(text)
        self.hintergrund = hintergrund


# Abrufe, die nach Ablauf der Gesamtfrist im Hintergrund zu Ende laufen —
# Referenz halten, sonst raeumt asyncio den Task mitten im Abruf weg.
_hintergrund_abrufe: set = set()


async def ensure_cache_indexes(db) -> None:
    """Idempotent: legt die nötigen Indizes auf der listings_cache Collection an."""
    # Altlasten raus: fruehere Versionen legten Lease-Dokumente OHNE
    # source/item_id an. So ein null/null-Relikt blockiert wegen des
    # Unique-Index jeden weiteren neuen Link — vor der Index-Anlage loeschen.
    # NUR Lease-Reste ohne Nutzdaten: fruehere Versionen legten beim
    # Reservieren Dokumente ohne source/item_id an, die den Unique-Index
    # blockierten. Eintraege MIT data bleiben unangetastet — sie zu
    # loeschen wuerde den gesamten Cache-Bestand verwerfen.
    await db.listings_cache.delete_many(
        {"data": {"$exists": False},
         "$or": [{"source": None}, {"source": {"$exists": False}},
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


def _ohne_herkunft(daten):
    """Runde 29 (12.09.2026): Wer ein Inserat eingereicht hat, steht als
    ingested_by_user/-dealer im Datensatz. Das ist Buchhaltung fuer das
    Aufraeumen, keine Information fuer andere Konten — beim Lesen raus.
    (Die Felder bleiben in der Datenbank, das Aufraeumen braucht sie.)"""
    if not isinstance(daten, dict):
        return daten
    return {k: v for k, v in daten.items()
            if not str(k).startswith("ingested_by_")}

async def peek_cached_listing(db, url: str,
                              dealer_id: Optional[str] = None
                              ) -> Optional[Tuple[dict, str]]:
    """Schaut NUR im Cache nach (kein Abruf, kein Lease). Liefert
    (data, herkunft) bei gültigem Treffer, sonst None.

    herkunft: "speicher" (gemeinsamer Zwischenspeicher, Server-Abruf),
    "browser" (Speicher, aber aus bestaetigten Browser-Einreichungen) oder
    "quarantaene" (eigene Browser-Einreichung). Pruefbericht 20.09.2026
    (A-20/U-11): vorher stand hier eine snapshot_id, die seit dem
    Beweisdokument immer None war; die Herkunft braucht der Vergleich, weil
    es fuer Browserdaten kein Beweisdokument gibt.

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
        # Altbestand im geteilten Cache kann die Herkunft noch tragen.
        return (_ohne_herkunft(cached["data"]),
                "browser" if cached.get("client_confirmed") else "speicher")
    if dealer_id:
        own = await db.listings_cache_client.find_one(
            {"cache_key": cache_key, "dealer_id": dealer_id}, {"_id": 0})
        if _is_fresh(own):
            # Runde 29 (12.09.2026, Pruefbefund): Die Quarantaene liegt je
            # FIRMA. Der Datensatz traegt aber, wer ihn eingereicht hat
            # (ingested_by_user/-dealer) — damit haette ein Sucher gesehen,
            # dass ein Kollege dasselbe Inserat schon geholt hat. Die
            # Inseratsdaten selbst duerfen firmenweit geteilt werden, die
            # Herkunft nicht. Zum Aufraeumen bleibt sie in der Datenbank.
            return _ohne_herkunft(own["data"]), "quarantaene"
    return None


# Massstaebe fuer die Freigabe aus der Quarantaene — bewusst benannt, denn
# diese Zahlen definieren die Sicherheitsschwelle gegen gefaelschtes HTML.
CLIENT_MATCH_PRICE_TOLERANCE = 0.01   # Preis auf 1 % genau
CLIENT_MATCH_TITLE_PREFIX = 40        # verglichene Titel-Laenge


def _client_core_match(a: dict, b: dict) -> bool:
    """Stimmen zwei unabhaengige Einreichungen im Kern ueberein?

    Preis und Titel allein waeren zu wenig: die stehen oeffentlich in der
    Suchergebnis-Liste — ein Angreifer koennte sie abschreiben und den REST
    faelschen (km-Stand, Erstzulassung). Deshalb muessen auch km-Stand,
    Erstzulassung und Marke uebereinstimmen, sofern beide Seiten sie
    liefern."""
    try:
        pa, pb = float(a.get("list_price") or 0), float(b.get("list_price") or 0)
    except (TypeError, ValueError):
        return False
    if not pa or not pb or abs(pa - pb) > CLIENT_MATCH_PRICE_TOLERANCE * max(pa, pb):
        return False
    ta = (a.get("title") or "").strip().lower()[:CLIENT_MATCH_TITLE_PREFIX]
    tb = (b.get("title") or "").strip().lower()[:CLIENT_MATCH_TITLE_PREFIX]
    if not ta or ta != tb:
        return False
    try:
        ka, kb = a.get("mileage"), b.get("mileage")
        if ka is not None and kb is not None:
            ka, kb = float(ka), float(kb)
            if abs(ka - kb) > 0.01 * max(ka, kb, 1):
                return False
    except (TypeError, ValueError):
        return False
    ea = str(a.get("first_registration") or "").strip()
    eb = str(b.get("first_registration") or "").strip()
    if ea and eb and ea != eb:
        return False
    ma = str(a.get("make_label") or "").strip().lower()
    mb = str(b.get("make_label") or "").strip().lower()
    if ma and mb and ma != mb:
        return False
    return True


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
    # Rollenprüfung 22.09.2026 (RP-442): Ein ABGELAUFENER eigener Eintrag,
    # den der TTL-Monitor (laeuft etwa minuetlich) noch nicht entfernt hat,
    # blockierte das $setOnInsert unten — die neue Einreichung wurde still
    # verworfen, und der Vergleich verlangte erneut die Erweiterung. Nur der
    # abgelaufene Eintrag wird ersetzt; ein gueltiger bleibt (first-wins).
    await db.listings_cache_client.update_one(
        {"cache_key": cache_key, "dealer_id": dealer_id, "expires_at": {"$lte": now}},
        {"$set": {"data": data, "url": url, "zuletzt_gesehen": now,
                  "expires_at": now + timedelta(hours=ttl_hours),
                  "created_at": now}})
    # Runde 19 (Nr. 22): first-wins ATOMAR — die Daten stehen nur im
    # $setOnInsert. Zwei gleichzeitige erste Einreichungen liessen sonst die
    # spaetere gewinnen (Vorpruefung und Upsert waren zwei Schritte).
    await db.listings_cache_client.update_one(
        {"cache_key": cache_key, "dealer_id": dealer_id},
        {"$set": {"zuletzt_gesehen": now},
         "$setOnInsert": {"cache_key": cache_key, "dealer_id": dealer_id,
                          "source": identity["source"], "item_id": identity["item_id"],
                          "url": url, "data": data,
                          "expires_at": now + timedelta(hours=ttl_hours),
                          "created_at": now}},
        upsert=True)
    # Globale Freigabe ("Promotion") ist standardmaessig AUS (Pruefbericht
    # Runde 4): Wer zwei Haendlerkonten kontrolliert, konnte sich sein
    # gefaelschtes HTML selbst "bestaetigen" und damit ALLEN Haendlern
    # falsche Preise/Fahrzeugdaten unterschieben. Ohne Freigabe bleiben
    # Client-Daten strikt beim einreichenden Haendler; jeder holt das
    # Inserat einmal selbst aus seinem Browser (kostenlos, kein Server-
    # Abruf). Wer die Freigabe bewusst will, setzt die Mindestzahl
    # unabhaengiger Haendler per CLIENT_INGEST_PROMOTE_MIN_DEALERS (>= 3).
    min_dealers = int(os.environ.get("CLIENT_INGEST_PROMOTE_MIN_DEALERS", "0") or 0)
    if min_dealers < 3:
        return "quarantined"
    bestaetiger = []
    # Pruefbericht 20.09.2026 (A-21): AELTESTE Einreichung zuerst — ohne
    # Sortierung bestimmte die Reihenfolge der Datenbank, wessen Stand
    # veroeffentlicht wird.
    async for other in db.listings_cache_client.find(
            {"cache_key": cache_key, "dealer_id": {"$ne": dealer_id},
             "expires_at": {"$gt": now}}, {"_id": 0}).sort("created_at", 1):
        if _client_core_match(other.get("data") or {}, data):
            bestaetiger.append(other)
    if len(bestaetiger) + 1 >= min_dealers:
        other = bestaetiger[0]
        # Nachpruefung Runde 14 (Nr. 14): die Herkunftsfelder
        # ingested_by_user/ingested_by_dealer (routes/listings.py) gehoeren
        # in die Quarantaene des Einreichers — nicht in den geteilten Cache,
        # den peek_cached_listing ALLEN Firmen liefert.
        freigabe = {k: v for k, v in (other.get("data") or data).items()
                    if not str(k).startswith("ingested_by_")}
        await db.listings_cache.update_one(
            {"cache_key": cache_key},
            {"$set": {"cache_key": cache_key,
                      "source": identity["source"],
                      "item_id": identity["item_id"],
                      # Die AELTERE Einreichung wird veroeffentlicht:
                      # wer als Zweiter bestaetigt, bestimmt nicht den
                      # Inhalt, den alle Haendler sehen.
                      "url": url, "data": freigabe,
                      "fetched_at": now,
                      # Kuerzere TTL als Server-Abrufe: Client-Daten
                      # sind Momentaufnahmen zweier Browser, keine
                      # API-Antwort.
                      "expires_at": now + timedelta(hours=confirmed_ttl_hours),
                      "last_used_at": now,
                      "client_confirmed": True,
                      "confirmed_by": [b.get("dealer_id") for b in bestaetiger]
                      + [dealer_id]},
             "$setOnInsert": {"created_at": now}},
            upsert=True)
        return "promoted"
    return "quarantined"


async def _lease_freigeben(db, cache_key: str, claim: str) -> None:
    """Runde 19 (16.09.2026, Abrufe Nr. 30/31): nur die EIGENE Lease freigeben
    (Claim-Token). Vorher gab ein ueberholter Prozess (langer DB-Aussetzer)
    die Lease seines Nachfolgers frei — der Weg zu einem zweiten und dritten
    Abruf desselben Links."""
    await db.listings_cache.update_one(
        {"cache_key": cache_key, "fetching_claim": claim},
        {"$set": {"fetching_until": None}, "$unset": {"fetching_claim": ""}})


async def get_or_fetch_listing(
    db,
    url: str,
    fetcher: Callable[[str, str, str], Awaitable[dict]],
    ttl_hours: int = 6,
    dealer_id: str = "",
    frist_sekunden: Optional[int] = None,
) -> Tuple[dict, bool]:
    """
    Liefert (vehicle_data, was_cached).

    Ablauf:
      1. ID erkennen (sonst ListingIdentityError).
      2. Cache lesen. Wenn vorhanden & nicht abgelaufen -> aus DB liefern,
         use_count++ und last_used_at aktualisieren.
      3. Sonst fetcher(source, item_id, url) aufrufen und Ergebnis speichern.

    `fetcher` ist eine async-Funktion (source, item_id, url) -> dict.
    `dealer_id` (B-04): Firma des Abrufs fuer den Firmenanteil an den
    Anbieter-Plaetzen. `frist_sekunden` (A-17): Gesamtfrist fuer Warten und
    Abruf, Standard ABRUF_GESAMT_SEKUNDEN.
    Pruefbericht 20.09.2026 (A-20): die dritte Rueckgabe (snapshot_id) ist
    entfallen — seit dem Beweisdokument war sie immer None.
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
                return cached["data"], True

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

    claim = uuid.uuid4().hex                   # Runde 19: Besitzer-Token der Lease
    # A-17: Gesamtfrist — alle Wartezeiten und der Abruf zaehlen dagegen.
    frist_s = int(frist_sekunden) if frist_sekunden else ABRUF_GESAMT_SEKUNDEN
    frist = time.monotonic() + frist_s

    def _rest() -> float:
        return frist - time.monotonic()

    got_lease = False
    lease_bis = time.monotonic() + LEASE_WARTEN_SEKUNDEN
    while True:                                # max. ~30 s warten, dann klare Meldung
        lease_now = datetime.now(timezone.utc)
        try:
            await db.listings_cache.update_one(
                {"cache_key": cache_key,
                 "$or": [{"fetching_until": {"$exists": False}},
                         {"fetching_until": None},
                         {"fetching_until": {"$lt": lease_now}}]},
                {"$set": {"fetching_until": lease_now + timedelta(seconds=90),
                          "fetching_claim": claim},
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
        if time.monotonic() >= lease_bis or _rest() <= 1.5:
            break                              # A-17: Wartezeit bzw. Frist verbraucht
        await _aio.sleep(1.5)
        c = await _fresh_cached()
        if c:                                  # der Erste ist fertig - uebernehmen
            await db.listings_cache.update_one(
                {"cache_key": cache_key},
                {"$inc": {"use_count": 1},
                 "$set": {"last_used_at": datetime.now(timezone.utc)}})
            return c["data"], True
    if not got_lease:
        raise ListingBusy(
            "Das Inserat wird gerade von einer anderen Anfrage geladen - "
            "bitte in ein paar Sekunden erneut versuchen.")

    # Letzte Kontrolle vor dem externen Abruf: hat der vorherige Halter in
    # der Zwischenzeit fertig geschrieben, sind seine Daten jetzt da — dann
    # NICHT noch einmal abrufen (sonst zwei Abrufe derselben Anzeige).
    frisch = await _fresh_cached()
    if frisch:
        await db.listings_cache.update_one(
            {"cache_key": cache_key},
            {"$inc": {"use_count": 1},
             "$set": {"last_used_at": datetime.now(timezone.utc)}})
        await _lease_freigeben(db, cache_key, claim)
        return frisch["data"], True

    # ZENTRALE PROVIDER-BEGRENZUNG: bevor wirklich extern abgerufen wird,
    # einen Slot belegen (MongoDB — wirkt ueber ALLE Worker/Server). So
    # loesen 300 gleichzeitige Nutzer nicht 300 externe Abrufe aus,
    # sondern hoechstens MAX_CONCURRENT_<QUELLE> — der Rest wartet kurz
    # oder bekommt eine freundliche "bitte gleich nochmal"-Antwort.
    from provider_limiter import APIFY_QUELLEN, acquire_slot, extend_slot, release_slot
    # Runde 29 (12.09.2026): Kleinanzeigen wird jetzt zuerst ueber die API
    # geholt (bezahlter Dienst, 600 Anfragen/Minute) statt die Webseite
    # abzugreifen. Dafuer gilt eine eigene, deutlich hoehere Obergrenze —
    # sonst wuerde die strenge Bremse des Selbst-Abrufs (2-3 gleichzeitig)
    # 30 Sucher ausbremsen, obwohl die API muehelos mithaelt.
    begrenzung = source
    if source == "kleinanzeigen":
        try:
            from kleinanzeigen_api import api_verfuegbar
            if api_verfuegbar():
                begrenzung = "kleinanzeigen_api"
        except Exception:  # noqa: BLE001 — im Zweifel die strenge Bremse
            pass
    slot_id = None
    apify_slot = None          # N9: Platz im gemeinsamen Apify-Topf
    braucht_topf = source in APIFY_QUELLEN
    try:
        # 0,3-s-Takt statt 1,5 s: bei kurzen Abrufen (Mock 0,4 s; echte
        # Abrufe 1-3 s) verschenkte der grobe Takt bis zu 1,5 s je
        # Slot-Wechsel — das drittelte den Durchsatz der Warteschlange.
        #
        # Nachpruefung 20.09.2026 (N9): mobile.de und AutoScout24 teilen sich
        # EINEN Apify-Plan. Zusaetzlich zum Platz der Quelle braucht es also
        # einen aus dem gemeinsamen Topf — sonst duerfen beide zusammen mehr,
        # als der Plan hergibt (Apify antwortet dann mit 429).
        #
        # Reihenfolge: erst Quelle, dann Topf — ueberall gleich, deshalb kann
        # sich nichts gegenseitig blockieren. A-17: Klappt der zweite Griff
        # nicht, geht der erste SOFORT zurueck und es wird in EINER Wartezeit
        # neu angesetzt — vorher hielt der Wartende bis zu 30 s einen Platz
        # der Quelle, waehrend er auf den Topf wartete. B-04: dealer_id fuer
        # den Firmenanteil.
        slot_bis = time.monotonic() + SLOT_WARTEN_SEKUNDEN
        while True:
            slot_id = await acquire_slot(db, begrenzung, dealer_id=dealer_id)
            if slot_id and braucht_topf:
                apify_slot = await acquire_slot(db, "apify", dealer_id=dealer_id)
                if not apify_slot:
                    await release_slot(db, slot_id)
                    slot_id = None
            if slot_id:
                break
            if time.monotonic() >= slot_bis or _rest() <= ABRUF_MINDESTREST_SEKUNDEN:
                break
            await _aio.sleep(0.3)
    except BaseException:
        # Lease (und einen schon belegten Platz) nicht haengen lassen, sonst
        # warten alle anderen 90 s. BaseException: auch beim Abbruch (X).
        if slot_id:
            await _aio.shield(release_slot(db, slot_id))
        await _aio.shield(_lease_freigeben(db, cache_key, claim))
        raise
    if not slot_id:
        await _lease_freigeben(db, cache_key, claim)
        raise ListingBusy(
            "Gerade werden viele Inserate gleichzeitig geladen - "
            "bitte in ein paar Sekunden erneut versuchen.")

    async def _extend_lease_forever():
        # Herzschlag: solange der Provider-Abruf laeuft, bleiben Lease UND
        # Provider-Slot gueltig — kein zweiter Prozess uebernimmt mittendrin.
        # JEDER Fehler wird geschluckt: stuerbe die Schleife an einem
        # kurzen Datenbank-Schluckauf, liefen Lease und Slot mitten im
        # Abruf ab — genau das, was der Herzschlag verhindern soll.
        # Pruefbericht 20.09.2026 (A-06): nicht mehr endlos — nach
        # LEASE_HERZSCHLAG_MAX_SEKUNDEN endet er (wie in link_jobs), Lease und
        # Plaetze laufen dann aus und die Selbstheilung greift. Die
        # Gesamtfrist unten greift im Normalfall lange vorher.
        beginn = time.monotonic()
        while True:
            try:
                await _aio.sleep(30)
                if time.monotonic() - beginn > LEASE_HERZSCHLAG_MAX_SEKUNDEN:
                    _log.warning("listings_cache %s: Abruf laeuft laenger als %s s — "
                                 "Lease wird nicht mehr verlaengert",
                                 cache_key, LEASE_HERZSCHLAG_MAX_SEKUNDEN)
                    return
                # Runde 19: nur die eigene Lease verlaengern (Claim-Token)
                await db.listings_cache.update_one(
                    {"cache_key": cache_key, "fetching_claim": claim},
                    {"$set": {"fetching_until":
                              datetime.now(timezone.utc) + timedelta(seconds=90)}})
                await extend_slot(db, slot_id)
                if apify_slot:
                    await extend_slot(db, apify_slot)
            except _aio.CancelledError:
                raise
            except Exception:
                continue

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

    async def _abruf_und_speichern() -> Tuple[dict, bool]:
        """Abruf, Aufraeumen und Speichern als EIGENER Task (A-17): laeuft die
        Gesamtfrist ab, arbeitet er im Hintergrund zu Ende und legt das
        Ergebnis in den Speicher — der naechste Versuch trifft dann sofort."""
        try:
            data = await fetcher(source, item_id, url)
        except BaseException:
            # Lease freigeben, damit der naechste Versuch nicht 90 s warten muss.
            # BaseException (statt Exception) wegen CancelledError: Wunsch Ahmad
            # 18.09.2026 — bricht der Nutzer ab ("X") oder legt der Browser auf,
            # wird dieser Task abgebrochen. Ohne Freigabe blieb derselbe Link bis
            # zu 90 Sekunden gesperrt ("wird gerade abgerufen"). shield(): die
            # Aufraeumung laeuft zu Ende, auch wenn der Task schon abgebrochen ist.
            await _aio.shield(_lease_freigeben(db, cache_key, claim))
            raise
        finally:
            _heartbeat.cancel()
            await _aio.shield(release_slot(db, slot_id))
            if apify_slot:
                await _aio.shield(release_slot(db, apify_slot))
        if not isinstance(data, dict):
            await _lease_freigeben(db, cache_key, claim)
            raise RuntimeError(
                f"fetcher für {source}:{item_id} hat kein dict zurückgegeben."
            )

        # Befund 79 (16.09.2026): fetched_at/expires_at/last_used_at stempeln den
        # ERFOLGREICHEN Abruf — nicht den Start vor Lease- und Slot-Wartezeit
        # (sonst war der Eintrag bis zu einer Minute "aelter" und lief frueher ab).
        abruf_ende = datetime.now(timezone.utc)
        expires_at = abruf_ende + timedelta(hours=ttl_hours)
        # Runde 19: das Ergebnis nur unter der EIGENEN Lease speichern — ist sie
        # inzwischen an einen Nachfolger gegangen, schreibt der (kein Upsert mehr,
        # sonst entstuende ein zweites Dokument).
        # A-20: kein snapshot_id-Feld mehr (Snapshots gibt es seit dem
        # Beweisdokument nicht; set_cache_snapshot wurde nie aufgerufen).
        res = await db.listings_cache.update_one(
            {"cache_key": cache_key, "fetching_claim": claim},
            {
                "$set": {
                    "cache_key": cache_key,
                    "source": source,
                    "item_id": item_id,
                    "url": url,
                    "data": data,
                    "fetched_at": abruf_ende,
                    "expires_at": expires_at,
                    "last_used_at": abruf_ende,
                    "fetching_until": None,
                },
                # fetch_count: Abrufzaehler je Inserat — belegt im Lasttest
                # und im Betrieb, dass kein Inserat mehrfach extern geholt
                # wird. WICHTIG: mit use_count in EINEM $inc — ein zweiter
                # "$inc"-Schluessel im selben Dict wuerde den ersten still
                # verdraengen (Python behaelt nur den letzten).
                "$inc": {"use_count": 1, "fetch_count": 1},
                "$unset": {"fetching_claim": ""},
            },
        )
        if res.matched_count == 0:
            _log.warning("listings_cache %s: Lease waehrend des Abrufs verloren — "
                         "Ergebnis nicht gespeichert (Nachfolger schreibt)", cache_key)
            # Befund 116 (16.09.2026): der verlorene Abruf darf auch KEINEN
            # Beweisstand einfrieren — sonst dokumentierte das Dokument einen
            # Stand, der nie Cache-Stand wurde. Der Nachfolger merkt vor.
            #
            # Befund 153 (19.09.2026): Er darf seine verworfenen Daten auch nicht
            # mehr ZURUECKGEBEN. Sonst stand im gemeinsamen Speicher der Stand des
            # Gewinners und im Fahrzeug dieses Suchers ein anderer — zwei Kollegen
            # sahen dasselbe Inserat mit verschiedenen Zahlen. Stattdessen kurz auf
            # den Gewinner warten und DESSEN Stand liefern.
            for _versuch in range(4):
                gewinner = await _fresh_cached()
                if gewinner:
                    await db.listings_cache.update_one(
                        {"cache_key": cache_key},
                        {"$inc": {"use_count": 1},
                         "$set": {"last_used_at": datetime.now(timezone.utc)}})
                    return gewinner["data"], True
                await _aio.sleep(0.5)
            raise ListingBusy(
                "Das Inserat wurde gerade von einer anderen Anfrage aktualisiert - "
                "bitte in ein paar Sekunden erneut versuchen.")
        # Beweisdokument (ersetzt die Snapshots, 10.09.2026): erster Abruf eines
        # Inserats durch den Server -> genau EIN Dokument je Inserat vormerken
        # (Linkpruefung, Vergleich, resolve laufen alle durch diesen Zweig).
        # Eigener Schutzblock: ein Fehler darf den Datenabruf nie brechen.
        try:
            from beweis_service import automatisch_aktiv
            from beweis_service import beweis_vormerken
            # Runde 23 (11.09.2026): den Stand DIESES Abrufs mitgeben — er wird
            # beim Anlegen eingefroren; der Worker liest sonst spaeter den
            # veraenderlichen Cache (neuerer Stand nach Wiederholung/Neuabruf).
            # Wunsch Ahmad 18.09.2026: nur noch auf Knopfdruck (POST
            # /beweise/anfordern) — der blosse Abruf legt kein Dokument mehr an.
            if automatisch_aktiv():
                await beweis_vormerken(db, cache_key=cache_key, quelle=source,
                                       item_id=item_id, url=url, anlass="abruf",
                                       daten=data, abgerufen_am=abruf_ende)
        except Exception as exc:  # noqa: BLE001
            _log.warning("Beweis-Vormerkung %s: %s", cache_key, exc)
        return data, False

    verwaist = {"ja": False}

    def _hintergrund_ende(t) -> None:
        _hintergrund_abrufe.discard(t)
        if verwaist["ja"] and not t.cancelled() and t.exception() is not None:
            _log.info("listings_cache %s: Hintergrund-Abruf endete mit %s: %s",
                      cache_key, type(t.exception()).__name__, str(t.exception())[:200])

    lauf = _aio.ensure_future(_abruf_und_speichern())
    _hintergrund_abrufe.add(lauf)
    lauf.add_done_callback(_hintergrund_ende)
    try:
        return await _aio.wait_for(_aio.shield(lauf), timeout=max(1.0, _rest()))
    except _aio.TimeoutError:
        # A-06/A-17: Frist verbraucht. Der Abruf laeuft im Hintergrund zu Ende
        # (Lease und Plaetze bleiben bis dahin belegt, das Ergebnis landet im
        # Speicher — DP-04: "der Link wird im Hintergrund weitergeladen");
        # der Aufrufer bekommt eine klare, wiederholbare Meldung (503).
        verwaist["ja"] = True
        _log.warning("listings_cache %s: Gesamtfrist %s s verbraucht — Abruf laeuft "
                     "im Hintergrund weiter", cache_key, frist_s)
        raise AbrufDauertZuLange(ABRUF_ZU_LANGE_TEXT, hintergrund=True)
    except _aio.CancelledError:
        # Wunsch Ahmad 18.09.2026: bricht der Nutzer ab ("X") oder legt der
        # Browser auf, bricht auch der Abruf ab — das finally im Task gibt
        # Lease und Plaetze frei.
        lauf.cancel()
        raise


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
    "ensure_cache_indexes",
    "ListingBusy",
    "AbrufDauertZuLange",
    "store_client_listing",
    "router",
]
