# -*- coding: utf-8 -*-
"""Schalter und Grenzen des Market-Crawlers — alles per Umgebung, nichts im
Code verteilt. Standard: Crawler AUS (MARKT_AKTIV=false); die Lesewege
(Karte im Vergleich, Chancen, Admin) funktionieren trotzdem mit dem, was da ist."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from konfig import kommazahl_env, schalter_env, zahl_env

ZEITZONE = ZoneInfo("Europe/Berlin")

# Sammlungen (alle neu — nichts in listings_cache / vehicle_comparisons)
MODELLE = "market_models"
KONFIG = "market_config"
SEGMENTE = "market_segments"
JOBS = "market_crawl_jobs"
LISTINGS = "market_listings"
SNAPSHOTS = "market_listing_snapshots"
TAGESSTATS = "market_segment_daily_stats"
SEGMENTSTATS = "market_segment_stats"
CHANCEN = "market_opportunities"
BUDGET = "market_crawler_budget"

QUELLE = "mobile"
# Wunsch Ahmad 26.09.2026: die Erstzulassung MUSS gleich sein — ein 320d von
# 2016 fuer 4.000 EUR und einer von 2019 fuer 9.600 EUR sind kein Segment.
# Auftrag v2: EINZELNE EZ-Jahre (5 je Modell, im Modell ueberschreibbar) und 5 km-Bereiche.
# Auftrag v3 (26.09.2026): Standard 4 EZ-Jahre x 4 km-Bereiche, 20 Zeilen, 2 Abrufe je Tag —
# nur Vorbelegung; jede Marktanalyse traegt ihre eigenen Werte (market_models).
EZ_BUCKETS_STANDARD = [{"year_from": j, "year_to": j} for j in (2019, 2020, 2021, 2022)]
KM_BUCKETS_STANDARD = [
    {"min_km": 10000, "max_km": 30000},
    {"min_km": 30001, "max_km": 50000},
    {"min_km": 50001, "max_km": 85000},
    {"min_km": 85001, "max_km": 115000},
]


def aktiv() -> bool:
    """Crawler an? (Planer + Worker). Lesewege haengen nicht daran."""
    return schalter_env("MARKT_AKTIV", False)


def chancen_aktiv() -> bool:
    """Menuepunkt Markt -> Chancen fuer die Firmen freigeschaltet?"""
    return schalter_env("MARKT_CHANCEN_AKTIV", False)


# Probelaeufe 26.09.2026: scrapesmith liefert dieselben 20 Treffer (sortiert,
# gefiltert, mit createdAt/modifiedAt/renewedAt) in 9 s fuer 0,005 $ je Lauf;
# sourabhbgp braucht 17-30 s fuer 0,064 $ (0,004 + 20 x 0,003). scrapesmith ist
# jung (Build 0.0.1) — deshalb der erste als automatischer Ersatz.
ACTOR_STANDARD = "scrapesmith~mobile-de-scraper"
ACTOR_ERSATZ = "sourabhbgp~mobile-de-scraper"


def actor() -> str:
    return (os.environ.get("MARKT_APIFY_ACTOR") or "").strip() or ACTOR_STANDARD


def actor_ersatz() -> str:
    """Leer = kein Ersatz. Standard: der andere bekannte Scraper."""
    w = os.environ.get("MARKT_APIFY_ACTOR_ERSATZ")
    if w is None:
        return ACTOR_ERSATZ if actor() != ACTOR_ERSATZ else ""
    return w.strip()


def start_urls_form(actor_name: str) -> str:
    """'objekt' ({"url": ...}) oder 'text' (nackte URL) — je Scraper verschieden.
    MARKT_APIFY_URL_FORM erzwingt eine Form fuer den Standard-Scraper."""
    erzwungen = (os.environ.get("MARKT_APIFY_URL_FORM") or "").strip().lower()
    if erzwungen in ("objekt", "text") and actor_name == actor():
        return erzwungen
    return "text" if actor_name.startswith("sourabhbgp") else "objekt"


def apify_details() -> bool:
    """scrapesmith: Detailseiten je Treffer mitladen — Standard AUS (Auftrag v2 Nr. 13):
    taeglich reichen ID, Preis, km, EZ, Motor, Verkaeuferart, Ort, Bewertung, Zeitstempel."""
    return schalter_env("MARKT_APIFY_DETAILS", False)


def kosten_je_lauf_usd(actor_name: str, rows: int) -> float:
    """Schaetzung je Scraper fuer EINEN Lauf mit `rows` Zeilen."""
    return kosten_buendel_usd(actor_name, 1, rows)


def preise_je_actor(actor_name: str) -> tuple:
    """(Start-USD, Zeilen-USD) je Scraper."""
    if actor_name.startswith("sourabhbgp"):
        return 0.004, 0.003
    return start_usd(), row_usd()


def kosten_buendel_usd(actor_name: str, laeufe: int, rows: int) -> float:
    start, row = preise_je_actor(actor_name)
    return round(laeufe * start + rows * row, 4)


def token() -> str:
    return (os.environ.get("APIFY_TOKEN") or "").strip()


def budget_monat_usd() -> float:
    return kommazahl_env("MARKT_BUDGET_MONAT_USD", 700.0, unten=0.0, oben=1000000.0)


def crawls_je_tag_standard() -> int:
    """Vorbelegung 'Abrufe je Tag' fuer neue Marktanalysen (1-4)."""
    return zahl_env("MARKT_CRAWLS_JE_TAG", 2, unten=1, oben=4)


def buendel_groesse() -> int:
    """Segmente je Actor-Lauf (Probe 26.09.2026: 4 URLs, je 20 Treffer, eindeutig ueber inputContext)."""
    return zahl_env("MARKT_BUENDEL_GROESSE", 10, unten=1, oben=50)


def crawl_intervall_tage() -> int:
    """0 = automatisch aus Segmentanzahl, Kosten und Monatsbudget (siehe jobs.intervall)."""
    return zahl_env("MARKT_CRAWL_INTERVALL_TAGE", 0, unten=0, oben=30)


def rows_je_segment() -> int:
    return zahl_env("MARKT_ROWS_JE_SEGMENT", 20, unten=1, oben=200)


def row_usd() -> float:
    """Zeilenpreis des Standard-Scrapers (scrapesmith Starter: 0,70 $ je 1.000 = 0,0007;
    Apify bucht die Zeilen erst kurz NACH dem Lauf — deshalb rechnen wir aus den Ereigniszaehlern)."""
    return kommazahl_env("MARKT_ROW_USD", 0.0007, unten=0.0, oben=10.0)


def start_usd() -> float:
    """Actor-Start des Standard-Scrapers (scrapesmith: 0,005 $ je GB, 1 GB)."""
    return kommazahl_env("MARKT_START_USD", 0.005, unten=0.0, oben=10.0)


def kosten_schaetzung_usd(rows: int) -> float:
    return round(start_usd() + rows * row_usd(), 4)


def fenster_von() -> int:
    return zahl_env("MARKT_CRAWL_FENSTER_VON", 3, unten=0, oben=23)


def fenster_bis() -> int:
    return zahl_env("MARKT_CRAWL_FENSTER_BIS", 9, unten=1, oben=24)


def jobs_parallel() -> int:
    return zahl_env("MARKT_JOBS_PARALLEL", 2, unten=1, oben=16)


def lauf_zeitlimit_s() -> int:
    return zahl_env("MARKT_LAUF_ZEITLIMIT_SEKUNDEN", 300, unten=30, oben=3600)


def job_versuche() -> int:
    return zahl_env("MARKT_JOB_VERSUCHE", 3, unten=1, oben=10)


def job_lease_s() -> int:
    return zahl_env("MARKT_JOB_LEASE_SEKUNDEN", 900, unten=60, oben=7200)


def entfernung_pruefen() -> bool:
    return schalter_env("MARKT_ENTFERNUNG_PRUEFEN", True)


def entfernung_nach_tagen() -> int:
    return zahl_env("MARKT_ENTFERNUNG_NACH_TAGEN", 2, unten=1, oben=60)


def entfernung_max_je_tag() -> int:
    return zahl_env("MARKT_ENTFERNUNG_MAX_JE_TAG", 50, unten=0, oben=5000)


def chance_reduktion_pct() -> float:
    return kommazahl_env("MARKT_CHANCE_REDUKTION_PROZENT", 5.0, unten=0.1, oben=90.0)


def chance_reduktion_eur() -> float:
    return kommazahl_env("MARKT_CHANCE_REDUKTION_EUR", 500.0, unten=1.0, oben=100000.0)


def chance_top_n() -> int:
    return zahl_env("MARKT_CHANCE_TOP_N", 5, unten=1, oben=20)


# ---------------------------------------------------------------- Zeit
def jetzt() -> datetime:
    return datetime.now(timezone.utc)


def jetzt_iso() -> str:
    return jetzt().isoformat()


def heute_tag(zeit: datetime | None = None) -> str:
    """Kalendertag in deutscher Zeit (YYYY-MM-DD) — ein Crawl-Tag."""
    return (zeit or jetzt()).astimezone(ZEITZONE).strftime("%Y-%m-%d")


def monat(zeit: datetime | None = None) -> str:
    return (zeit or jetzt()).astimezone(ZEITZONE).strftime("%Y-%m")


def fenster_start(tag: str) -> datetime:
    """Beginn des Crawl-Fensters (deutsche Zeit) als UTC-Zeitpunkt."""
    d = datetime.strptime(tag, "%Y-%m-%d").replace(tzinfo=ZEITZONE, hour=fenster_von())
    return d.astimezone(timezone.utc)


def fenster_dauer() -> timedelta:
    stunden = max(1, fenster_bis() - fenster_von())
    return timedelta(hours=stunden)
