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
# Wunsch Ahmad 26.09.2026 abends (v3): EZ 2018-2022 einzeln, sechs km-Bereiche, 10 Zeilen.
EZ_BUCKETS_STANDARD = [{"year_from": j, "year_to": j} for j in (2018, 2019, 2020, 2021, 2022)]
# Befund 26.09.2026 (erster Live-Tag): 33 Segmente ohne Treffer — Autos von 2019-2022
# stehen 2026 mit 50-200k km im Markt, 10-30k km war fast leer (Probe: 320d EZ 2019
# 0-50k = 3 Treffer, 50-100k/100-150k/150-250k je 20+). Deshalb vier breitere Bereiche.
KM_BUCKETS_STANDARD = [
    {"min_km": 10000, "max_km": 30000},
    {"min_km": 30001, "max_km": 55000},
    {"min_km": 55001, "max_km": 80000},
    {"min_km": 80001, "max_km": 110000},
    {"min_km": 110001, "max_km": 140000},
    {"min_km": 140001, "max_km": 190000},
]
# Vorgaenger (v2, 26.09. nachmittags) — die Migration erkennt daran unveraenderte Auftraege
KM_BUCKETS_V2 = [
    {"min_km": 0, "max_km": 50000},
    {"min_km": 50001, "max_km": 100000},
    {"min_km": 100001, "max_km": 150000},
    {"min_km": 150001, "max_km": 250000},
]
EZ_JAHRE_V2 = [2019, 2020, 2021, 2022]
SCHALTER_DOK = "crawler"      # market_config/_id=crawler: {"aktiv": bool} — Knopf im Admin
# Reparaturwelle 5 (Review 26.09.2026 abends, Nr. 24/26/42): Merker in market_config —
# der Tagesplan/die Entfernungspruefung sind je Tag erledigt (kurze Sperren statt 20 h),
# und welche kritischen Unique-Indizes fehlen (dann crawlt der Worker nicht).
TAGESPLAN_DOK = "tagesplan"
ENTFERNUNG_DOK = "entfernung"
INDIZES_DOK = "indizes"
# Reparaturwelle 6 Nr. 94: das im Admin gesetzte Monatsbudget gilt auch fuer KOMMENDE Monate
# (market_config/budget {monthly_budget_usd}); die Umgebung ist nur die Vorbelegung, wenn nichts gesetzt ist
BUDGET_DOK = "budget"
# Nr. 17: nach dem Zeilenfilter fehlen Zeilen ("Top 10" waere sonst Top 7) — deshalb je Segment
# rows + Puffer abrufen (+30 %, hoechstens +10) und danach auf rows kuerzen. Konstante, keine
# Umgebungsvariable; Reservierung, Prognose und Taktung rechnen mit dem Puffer.
PUFFER_FAKTOR = 0.3
PUFFER_MAX = 10
# Nr. 25: alle Laeufe eines Tages liegen vor 23:30 Uhr deutscher Zeit
TAGESENDE_STUNDE, TAGESENDE_MINUTE = 23, 30


def zeilen_mit_puffer(rows: int) -> int:
    """Abgerufene Zeilen je Segment (Nr. 17): rows + min(10, ceil(rows x 0,3))."""
    import math
    r = max(1, int(rows or 1))
    return r + min(PUFFER_MAX, int(math.ceil(r * PUFFER_FAKTOR)))


def aktiv() -> bool:
    """Crawler laut Umgebung (MARKT_AKTIV). Der Knopf im Admin (crawler_aktiv) geht vor."""
    return schalter_env("MARKT_AKTIV", False)


async def crawler_aktiv(db) -> bool:
    """Crawler an? (Planer + Worker.) Wunsch Ahmad 26.09.2026: ein Knopf im Admin statt
    Umgebungsvariable — der gespeicherte Schalter (market_config/crawler) geht vor
    MARKT_AKTIV; fehlt er, gilt die Umgebung. Lesewege haengen nicht daran.
    Reparaturwelle 6 Nr. 96: kann der Schalter nicht gelesen werden (Datenbank-Stoerung),
    gilt AUS — vorher fiel er still auf die Umgebung zurueck (fail-open)."""
    try:
        doc = await db[KONFIG].find_one({"_id": SCHALTER_DOK}, {"_id": 0, "aktiv": 1})
    except Exception:  # noqa: BLE001
        return False
    if doc and "aktiv" in doc:
        return bool(doc["aktiv"])
    return aktiv()


async def crawler_quelle(db) -> str:
    """'admin' (Knopf gesetzt) oder 'env' (nur MARKT_AKTIV)."""
    doc = await db[KONFIG].find_one({"_id": SCHALTER_DOK}, {"_id": 0, "aktiv": 1})
    return "admin" if doc and "aktiv" in doc else "env"


async def crawler_schalten(db, an: bool, wer: str = "") -> bool:
    """Nur der Schalter. Nr. 23: Stornieren wartender Jobs (aus) und frischer Tagesplan (an)
    liegen in jobs.crawler_schalten — die Admin-Route ruft DEN."""
    await db[KONFIG].update_one({"_id": SCHALTER_DOK},
                                {"$set": {"aktiv": bool(an), "updated_at": jetzt_iso(), "von": str(wer or "")}}, upsert=True)
    return bool(an)


async def merker_lesen(db, dok: str) -> dict:
    try:
        return await db[KONFIG].find_one({"_id": dok}, {"_id": 0}) or {}
    except Exception:  # noqa: BLE001
        return {}


async def merker_setzen(db, dok: str, **werte) -> None:
    await db[KONFIG].update_one({"_id": dok}, {"$set": {**werte, "updated_at": jetzt_iso()}}, upsert=True)


async def indizes_fehlen(db) -> list:
    """Nr. 26: fehlende KRITISCHE Unique-Indizes (von indizes.markt_indizes gemerkt) — der
    Worker crawlt dann nicht, die Hauptapp laeuft weiter."""
    return list((await merker_lesen(db, INDIZES_DOK)).get("fehlen") or [])


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


def actor_und_build(actor_name: str) -> tuple:
    """Reparaturwelle 6 Nr. 113: der Scraper darf gepinnt werden — 'name@1.2.3' oder
    'name@latest' (Build-Tag/-Nummer, Apify ?build=). Liefert (Actor-Pfad, Build oder '')."""
    s = str(actor_name or "").strip()
    if "@" in s:
        name, build = s.split("@", 1)
        return name.strip(), build.strip()
    return s, ""


def actor_ersatz() -> str:
    """Leer = kein Ersatz. Standard: der andere bekannte Scraper."""
    w = os.environ.get("MARKT_APIFY_ACTOR_ERSATZ")
    if w is None:
        return ACTOR_ERSATZ if actor_und_build(actor())[0] != ACTOR_ERSATZ else ""
    return w.strip()


def start_urls_form(actor_name: str) -> str:
    """'objekt' ({"url": ...}) oder 'text' (nackte URL) — je Scraper verschieden.
    MARKT_APIFY_URL_FORM erzwingt eine Form fuer den Standard-Scraper."""
    erzwungen = (os.environ.get("MARKT_APIFY_URL_FORM") or "").strip().lower()
    if erzwungen in ("objekt", "text") and actor_und_build(actor_name)[0] == actor_und_build(actor())[0]:
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
    return zahl_env("MARKT_ROWS_JE_SEGMENT", 10, unten=1, oben=200)     # Ahmad 26.09. abends: 10


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
    """Beginn des Crawl-Fensters (Stunde, deutsche Zeit). Review 26.09.2026 Nr. 18:
    Das Fenster gilt nur fuer den ERSTEN Abruf eines Tages. Bei crawls_per_day=2 liegt
    der zweite Abruf ~12 h nach dem ersten (also ~15-21 Uhr) und damit bewusst
    AUSSERHALB des Fensters — gewollt, damit die beiden Stichproben eines Tages
    moeglichst weit auseinanderliegen (siehe jobs.tagesplan)."""
    return zahl_env("MARKT_CRAWL_FENSTER_VON", 3, unten=0, oben=23)


def fenster_bis() -> int:
    """Ende des Crawl-Fensters fuer den ERSTEN Abruf je Tag; weitere Abrufe
    (crawls_per_day > 1) verteilen sich im 24/k-Stunden-Takt ueber den Tag —
    gewollt, keine Fensterverletzung (Review 26.09.2026 Nr. 18)."""
    return zahl_env("MARKT_CRAWL_FENSTER_BIS", 9, unten=1, oben=24)


def jobs_parallel() -> int:
    """Nr. 34: so viele Buendel verarbeitet ein Worker-Takt GLEICHZEITIG (asyncio.gather);
    jedes Buendel reserviert sein Budget atomar. Zaehlt bei Apify als parallele Laeufe —
    zusammen mit Vergleich (mobile/autoscout) unter APIFY_MAX_PARALLEL bleiben."""
    return zahl_env("MARKT_JOBS_PARALLEL", 2, unten=1, oben=16)


def lauf_zeitlimit_s() -> int:
    return zahl_env("MARKT_LAUF_ZEITLIMIT_SEKUNDEN", 300, unten=30, oben=3600)


def job_versuche() -> int:
    return zahl_env("MARKT_JOB_VERSUCHE", 3, unten=1, oben=10)


def job_lease_s() -> int:
    return zahl_env("MARKT_JOB_LEASE_SEKUNDEN", 900, unten=60, oben=7200)


def lease_sekunden(buendelgroesse: int | None = None) -> int:
    """Review 26.09.2026 Nr. 12: ein Buendel kann laenger dauern als die feste Lease
    (900 s) — Standardlauf plus Ersatzweg je URL einzeln, jeder bis lauf_zeitlimit_s.
    Lease = max(MARKT_JOB_LEASE_SEKUNDEN, (1 + Buendelgroesse) x Zeitlimit + 120 s).
    Liegt hier (nicht in jobs), weil auch budget.py den Wert braucht (Nr. 47)."""
    n = int(buendelgroesse if buendelgroesse is not None else buendel_groesse())
    return max(job_lease_s(), (1 + max(1, n)) * lauf_zeitlimit_s() + 120)


def reservierung_ablauf_s(buendelgroesse: int | None = None) -> int:
    """Nr. 47: eine Budgetreservierung verfaellt, wenn der Worker zwischen Reservieren und
    Abrechnen stirbt — Ablauf = Lease-Dauer + 60 s (keine eigene Umgebungsvariable)."""
    return lease_sekunden(buendelgroesse) + 60


def entfernung_pruefen() -> bool:
    return schalter_env("MARKT_ENTFERNUNG_PRUEFEN", True)


def entfernung_nach_tagen() -> int:
    return zahl_env("MARKT_ENTFERNUNG_NACH_TAGEN", 2, unten=1, oben=60)


def entfernung_max_je_tag() -> int:
    return zahl_env("MARKT_ENTFERNUNG_MAX_JE_TAG", 50, unten=0, oben=5000)


def entfernung_kosten_je_tag_usd() -> float:
    """Nr. 38: Obergrenze der Entfernungspruefung je Tag — max. Pruefungen x (Start + 1 Zeile)
    des Standard-Scrapers; Prognose und Taktung rechnen sie mit ein."""
    if not entfernung_pruefen() or entfernung_max_je_tag() <= 0:
        return 0.0
    return round(entfernung_max_je_tag() * kosten_je_lauf_usd(actor(), 1), 4)


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


def monat_aus_tag(tag: str) -> str:
    """Reparaturwelle 6 Nr. 105: der Budget-Monat eines Jobs kommt aus seinem Tag (YYYY-MM-DD),
    nicht aus der Ausfuehrungszeit — ein Lauf nach Mitternacht am Monatsende bucht im alten Monat."""
    try:
        d = datetime.strptime(str(tag)[:10], "%Y-%m-%d").replace(tzinfo=ZEITZONE, hour=12)
    except (TypeError, ValueError):
        return monat()
    return monat(d)


def fenster_start(tag: str) -> datetime:
    """Beginn des Crawl-Fensters (deutsche Zeit) als UTC-Zeitpunkt."""
    d = datetime.strptime(tag, "%Y-%m-%d").replace(tzinfo=ZEITZONE, hour=fenster_von())
    return d.astimezone(timezone.utc)


def fenster_dauer() -> timedelta:
    stunden = max(1, fenster_bis() - fenster_von())
    return timedelta(hours=stunden)


def tag_ende(tag: str) -> datetime:
    """Nr. 25: spaetester Zeitpunkt eines Laufs an diesem Tag (23:30 deutsche Zeit) als UTC."""
    d = datetime.strptime(tag, "%Y-%m-%d").replace(tzinfo=ZEITZONE, hour=TAGESENDE_STUNDE, minute=TAGESENDE_MINUTE)
    return d.astimezone(timezone.utc)


def rest_tage_im_monat(zeit: datetime | None = None) -> int:
    """Nr. 31: verbleibende Kalendertage des Monats (deutsche Zeit), heute mitgezaehlt (>= 1)."""
    import calendar
    d = (zeit or jetzt()).astimezone(ZEITZONE)
    return max(1, calendar.monthrange(d.year, d.month)[1] - d.day + 1)
