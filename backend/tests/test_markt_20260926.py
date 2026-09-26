# -*- coding: utf-8 -*-
"""Market Intelligence (Auftrag Ahmad 25./26.09.2026) — Phase 1.

Alle Tests laufen ohne Apify (Scraper-Lauf wird ersetzt). Gepruefte Punkte
aus dem Auftrag (Nr. 35): Preis-Sortierung, max 20, Segmentzuordnung,
Listing-/Snapshot-Dedupe, Preisaenderung, not_seen_in_sample ist KEIN
Verkauf, Entfernung nur nach Pruefung, Budget parallel, Job-Lease/-Dedupe/
-Retry, Hauptweg unabhaengig, Lesewege nur lesend, Stichprobengroesse.
"""
import asyncio
import inspect
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_befunde_runde17_termine import _module, welt  # noqa: E402,F401

K = _module("markt.konfig")
SEG = _module("markt.segmente")
URL = _module("markt.url")
NORM = _module("markt.normalisieren")
SP = _module("markt.speicher")
BUD = _module("markt.budget")
JOBS = _module("markt.jobs")
ENT = _module("markt.entfernung")
ABF = _module("markt.abfrage")
APIFY = _module("markt.apify")


def _item(lid, preis, km=70000, ez="03/2020", created="2026-09-05T11:46:02.000Z", rating="GOOD_PRICE"):
    return {"id": lid, "url": f"https://suchen.mobile.de/auto-inserat/bmw-320d/{lid}.html", "make": "BMW", "model": "320",
            "variant": "d Touring", "title": "BMW 320d Touring", "category": "EstateCar", "firstRegistration": ez,
            "mileageKm": km, "power": "140\xa0kW\xa0(190\xa0PS)", "fuel": "Diesel", "gearbox": "Automatik", "hu": "08/2027",
            "priceGross": preis, "priceNet": None, "vat": None,
            "priceRating": {"rating": rating, "ratingLabel": "Guter Preis", "thresholdLabels": ["10.600\xa0€", "13.800\xa0€"]},
            "seller": {"name": "GEHEIM Automobile", "type": "DEALER", "phone": "0170 000000", "country": "DE"},
            "sellerId": 4711, "zip": "30159", "location": "Hannover", "latitude": 52.37, "longitude": 9.73,
            "createdAt": created, "modifiedAt": "2026-09-22T14:08:07.000Z", "renewedAt": created, "country": "DE"}


def _tag(offset=0):
    return K.heute_tag(K.jetzt() + timedelta(days=offset))


def _modell(w):
    return {"id": f"test-320d-{w.s}", "make": "BMW", "model": "320", "variant": "320d", "label": "BMW 320d (Test)",
            "fuel": "DIESEL", "power_kw_min": 120, "power_kw_max": 145, "priority": 1, "enabled": True,
            "make_id": "3500", "model_id": "10"}


def _segment(w):
    return {"id": f"test-320d-{w.s}:2019-2021:55001-85000", "model_id": f"test-320d-{w.s}", "label": "BMW 320d (Test)",
            "min_km": 55001, "max_km": 85000, "km_label": "55–85k km", "year_from": 2019, "year_to": 2021,
            "ez_label": "EZ 2019–2021", "max_items": 20, "sort": "price_asc", "enabled": True, "priority": 1}


def _aufraeumen(welt):
    """Alle Test-Reste (auch aus abgebrochenen frueheren Laeufen: Praefix test-/dbg-)."""
    db = welt.db
    muster = "^(test|dbg)-"
    for coll in (K.MODELLE, K.SEGMENTE, K.JOBS, K.SEGMENTSTATS, K.TAGESSTATS, K.CHANCEN):
        welt.run(db[coll].delete_many({"$or": [{"id": {"$regex": muster}}, {"model_id": {"$regex": muster}},
                                               {"segment_id": {"$regex": muster}}, {"_id": {"$regex": muster}}]}))
    welt.run(db[K.LISTINGS].delete_many({"$or": [{"listing_id": {"$regex": "^t[0-9a-f]{10}"}}, {"model_id": {"$regex": muster}}]}))
    welt.run(db[K.SNAPSHOTS].delete_many({"$or": [{"listing_id": {"$regex": "^t[0-9a-f]{10}"}}, {"segment_id": {"$regex": muster}}]}))
    welt.run(db[K.BUDGET].delete_many({"_id": {"$regex": "^test-"}}))
    welt.run(db.job_locks.delete_many({"name": {"$regex": "^markt-"}}))


# ---------------------------------------------------------------- URL + Normalisierung
def test_01_such_url_preis_aufsteigend_km_ez_kraftstoff(welt):
    w = welt.w
    u = URL.such_url(_segment(w), _modell(w))
    assert u.startswith("https://suchen.mobile.de/fahrzeuge/search.html?")
    for teil in ("ms=3500%3B10%3B%3B%3B", "ft=DIESEL", "pw=120%3A145", "ml=55001%3A85000", "fr=2019%3A2021",
                 "dam=0", "sb=p", "od=up"):
        assert teil in u, teil
    # ohne EZ und ohne Leistung: Parameter fehlen, Sortierung bleibt
    u2 = URL.such_url({"min_km": 0, "max_km": 30000}, {"make_id": "3500", "model_id": "10", "fuel": "PETROL"})
    assert "fr=" not in u2 and "pw=" not in u2 and "sb=p" in u2 and "od=up" in u2 and "ml=0%3A30000" in u2


def test_02_normalisierung_ohne_verkaeuferdaten_und_sortierpruefung():
    items = [_item("t1", 9990), _item("t2", 7190), _item("t2", 7190), {"id": "t3"}, _item("t4", 12000)]
    ls = NORM.listings_aus_items(items)
    assert [l["listing_id"] for l in ls] == ["t1", "t2", "t4"], "ohne Preis raus, Dublette raus, Reihenfolge bleibt"
    l = ls[0]
    assert l["price_gross"] == 9990 and l["power_kw"] == 140 and l["power_ps"] == 190 and l["mileage_km"] == 70000
    assert l["price_rating"] == {"rating": "GOOD_PRICE", "label": "Guter Preis", "thresholds": [10600, 13800]}
    assert l["seller_type"] == "DEALER" and l["seller_id"] == "4711" and l["postal_code"] == "30159" and l["city"] == "Hannover"
    assert "GEHEIM" not in str(l) and "0170" not in str(l), "kein Name, keine Telefonnummer"
    assert l["mobile_created_at"] == "2026-09-05T11:46:02.000Z" and l["mobile_renewed_at"]
    assert NORM.preise_aufsteigend(ls) is False
    assert NORM.preise_aufsteigend(sorted(ls, key=lambda x: x["price_gross"])) is True


# ---------------------------------------------------------------- Speicher: Dedupe, Snapshots, Preisaenderung, Zustaende
def test_03_listing_dedupe_snapshots_preisaenderung_not_seen(welt):
    w, db = welt.w, welt.db
    _aufraeumen(welt)
    seg = _segment(w)
    welt.run(db[K.MODELLE].insert_one(_modell(w)))
    welt.run(db[K.SEGMENTE].insert_one(dict(seg)))
    s = w.s
    tag1 = K.jetzt() - timedelta(days=1)
    items1 = NORM.listings_aus_items([_item(f"t{s}a", 18900), _item(f"t{s}b", 19900), _item(f"t{s}c", 21700)])
    erg1 = welt.run(SP.verarbeiten(db, seg, items1, beobachtet=tag1))
    assert erg1["sample_size"] == 3 and erg1["neu_gesamt"] == 3 and erg1["neu_im_sample"] == 3
    # derselbe Tag noch einmal: keine zweiten Snapshots, keine Preisaenderung
    welt.run(SP.verarbeiten(db, seg, items1, beobachtet=tag1 + timedelta(minutes=5)))
    assert welt.run(db[K.SNAPSHOTS].count_documents({"listing_id": {"$regex": f"^t{s}"}})) == 3
    assert welt.run(db[K.LISTINGS].count_documents({"listing_id": {"$regex": f"^t{s}"}})) == 3
    # Tag 2: a guenstiger (-500), b weg (nicht im Sample!), d neu unter dem bisherigen Minimum
    items2 = NORM.listings_aus_items([_item(f"t{s}d", 17500), _item(f"t{s}a", 18400), _item(f"t{s}c", 21700)])
    erg2 = welt.run(SP.verarbeiten(db, seg, items2, beobachtet=K.jetzt()))
    a = welt.run(db[K.LISTINGS].find_one({"listing_id": f"t{s}a"}, {"_id": 0}))
    assert a["current_price"] == 18400 and a["first_price"] == 18900 and a["price_changes"] == 1 and a["price_reductions"] == 1
    assert [p["price"] for p in a["price_history"]] == [18900, 18400] and a["active_state"] == "seen"
    snap_a = welt.run(db[K.SNAPSHOTS].find_one({"listing_id": f"t{s}a", "date": _tag(0)}, {"_id": 0}))
    assert snap_a["price_change_eur"] == -500 and round(snap_a["price_change_pct"], 2) == -2.65 and snap_a["rank_in_sample"] == 2
    assert snap_a["rank_yesterday"] == 1 and snap_a["new_in_sample"] is False
    b = welt.run(db[K.LISTINGS].find_one({"listing_id": f"t{s}b"}, {"_id": 0}))
    assert b["active_state"] == "not_seen_in_sample" and b.get("not_seen_since") and "confirmed_removed_at" not in b
    assert erg2["preis_gesunken"] == 1 and erg2["neu_gesamt"] == 1
    # Tagesaggregat + Segmentstatistik (Top-20-Begriffe, kein Marktmedian)
    st = welt.run(db[K.SEGMENTSTATS].find_one({"_id": seg["id"]}, {"_id": 0}))
    assert st["sample_size"] == 3 and st["min_price"] == 17500 and st["median_top20_price"] == 18400 and st["max_price"] == 21700
    assert st["new_in_sample_today"] == 1 and st["price_reductions_today"] == 1 and st["beobachtete_tage"] == 2
    assert st["datenlage"] == "niedrig" and "market_median" not in str(st)
    ts = welt.run(db[K.TAGESSTATS].find({"segment_id": seg["id"]}, {"_id": 0}).sort("date", 1).to_list(10))
    assert [t["median_price"] for t in ts] == [19900, 18400] and ts[1]["p25_price"] == 17950
    # Chancen: d unter bisherigem Minimum, a stark reduziert? (-500 EUR >= 500 EUR -> ja)
    ch = welt.run(db[K.CHANCEN].find({"listing_id": {"$regex": f"^t{s}"}}, {"_id": 0}).to_list(20))
    typen = sorted((c["listing_id"][-1], c["typ"]) for c in ch)
    assert ("d", "neues_minimum") in typen and ("a", "stark_reduziert") in typen
    _aufraeumen(welt)


def test_04_entfernung_nur_nach_pruefung(welt, monkeypatch):
    w, db = welt.w, welt.db
    _aufraeumen(welt)
    s = w.s
    alt = (K.jetzt() - timedelta(days=5)).isoformat()
    welt.run(db[K.LISTINGS].insert_many([
        {"source": "mobile", "listing_id": f"t{s}x", "url": "https://suchen.mobile.de/fahrzeuge/details.html?id=1",
         "active_state": "not_seen_in_sample", "not_seen_since": alt, "current_price": 100.0},
        {"source": "mobile", "listing_id": f"t{s}y", "url": "https://suchen.mobile.de/fahrzeuge/details.html?id=2",
         "active_state": "not_seen_in_sample", "not_seen_since": alt, "current_price": 200.0}]))
    monkeypatch.setenv("MARKT_BUDGET_MONAT_USD", "10")
    monkeypatch.setattr(K, "monat", lambda zeit=None: f"test-{s}")
    antworten = {"1": [], "2": [_item("2", 190)]}

    async def _lauf(urls, max_items, zeitlimit_s=None, actor_name=None, max_items_per_query=None):
        assert max_items == 1
        key = urls[0].split("id=")[-1]
        return {"items": antworten[key], "usd": 0.007, "run_id": "r", "status": "SUCCEEDED", "dauer_ms": 5}
    monkeypatch.setattr(APIFY, "lauf", _lauf)
    kand = welt.run(ENT.kandidaten(db, 10))
    assert {k["listing_id"] for k in kand} >= {f"t{s}x", f"t{s}y"}
    assert welt.run(ENT.pruefen(db, {"source": "mobile", "listing_id": f"t{s}x", "url": "https://suchen.mobile.de/fahrzeuge/details.html?id=1"})) == "confirmed_removed"
    assert welt.run(ENT.pruefen(db, {"source": "mobile", "listing_id": f"t{s}y", "url": "https://suchen.mobile.de/fahrzeuge/details.html?id=2"})) == "not_seen_in_sample"
    x = welt.run(db[K.LISTINGS].find_one({"listing_id": f"t{s}x"}, {"_id": 0}))
    y = welt.run(db[K.LISTINGS].find_one({"listing_id": f"t{s}y"}, {"_id": 0}))
    assert x["active_state"] == "confirmed_removed" and x["confirmed_removed_at"]
    assert y["active_state"] == "not_seen_in_sample" and y["verified_online_at"] and y["current_price"] == 190
    assert "verkauft" not in ABF.ZUSTAND_TEXT["confirmed_removed"].lower() or "kein Beleg" in ABF.ZUSTAND_TEXT["confirmed_removed"]
    assert "NICHT verkauft" in ABF.ZUSTAND_TEXT["not_seen_in_sample"]
    b = welt.run(BUD.dokument(db, f"test-{s}"))
    assert round(b["used_usd"], 3) == 0.014 and b["runs"] == 2 and round(b["reserved_usd"], 6) == 0
    _aufraeumen(welt)


# ---------------------------------------------------------------- Budget, Jobs
def test_05_budget_atomar_und_abrechnung(welt, monkeypatch):
    w, db = welt.w, welt.db
    s = w.s
    monkeypatch.setenv("MARKT_BUDGET_MONAT_USD", "0.20")
    monkeypatch.setattr(K, "monat", lambda zeit=None: f"test-{s}")
    welt.run(db[K.BUDGET].delete_many({"_id": f"test-{s}"}))
    r1 = welt.run(BUD.reservieren(db, 0.064))
    r2 = welt.run(BUD.reservieren(db, 0.064))
    r3 = welt.run(BUD.reservieren(db, 0.064))
    assert r1 and r2 and r3
    assert welt.run(BUD.reservieren(db, 0.064)) is None, "0,256 > 0,20 — vierte Reservierung scheitert"
    welt.run(BUD.abrechnen(db, r1, 0.050, rows=20))
    welt.run(BUD.abrechnen(db, r2, None, rows=0, gelaufen=False))     # nichts gelaufen: freigeben
    d = welt.run(BUD.dokument(db, f"test-{s}"))
    assert round(d["used_usd"], 3) == 0.05 and round(d["reserved_usd"], 3) == 0.064 and d["rows"] == 20 and d["runs"] == 1
    assert welt.run(BUD.reservieren(db, 0.064)) is not None
    welt.run(db[K.BUDGET].delete_many({"_id": f"test-{s}"}))


def test_06_jobs_plan_dedupe_lease_retry_und_scraperlauf(welt, monkeypatch):
    w, db = welt.w, welt.db
    _aufraeumen(welt)
    s = w.s
    seg = _segment(w)
    welt.run(db[K.MODELLE].insert_one(_modell(w)))
    welt.run(db[K.SEGMENTE].insert_one(dict(seg)))
    monkeypatch.setenv("MARKT_BUDGET_MONAT_USD", "100")
    monkeypatch.setenv("MARKT_CRAWL_INTERVALL_TAGE", "1")
    monkeypatch.setattr(K, "monat", lambda zeit=None: f"test-{s}")
    tag = f"2099-01-{int(s[:1], 16) % 28 + 1:02d}"
    # Tagesplan idempotent (Dedupe segment_id+tag) — nur unser Segment zaehlen
    p1 = welt.run(JOBS.tagesplan(db, tag))
    p2 = welt.run(JOBS.tagesplan(db, tag))
    assert welt.run(db[K.JOBS].count_documents({"segment_id": seg["id"], "tag": tag})) == 1 and p2["neu"] == 0
    assert p1["intervall_tage"] == 1
    job = welt.run(db[K.JOBS].find_one({"segment_id": seg["id"], "tag": tag}, {"_id": 0}))
    assert job["status"] == "queued" and job["estimated_rows"] == 20 and job["estimated_cost"] == K.kosten_je_lauf_usd(K.actor(), 20)
    # beanspruchen: nur faellige (scheduled_at in der Zukunft -> nichts)
    welt.run(db[K.JOBS].update_one({"id": job["id"]}, {"$set": {"scheduled_at": K.jetzt_iso()}}))
    j = welt.run(JOBS.beanspruchen(db))
    assert j and j["id"] == job["id"] and j["status"] == "running" and j["lease_until"] and j["attempts"] == 1
    assert welt.run(JOBS.beanspruchen(db)) is None or welt.run(JOBS.beanspruchen(db))["id"] != job["id"]
    # Lease abgelaufen -> zurueck in die Warteschlange (Neustart verliert nichts)
    welt.run(db[K.JOBS].update_one({"id": job["id"]}, {"$set": {"lease_until": "2000-01-01T00:00:00+00:00"}}))
    assert welt.run(JOBS.stale_zurueck(db)) >= 1
    assert welt.run(db[K.JOBS].find_one({"id": job["id"]}, {"_id": 0}))["status"] == "queued"
    # Scraper-Lauf: max_items 20, unsortierte Antwort -> lokal sortiert + gekennzeichnet
    gesehen = {}

    monkeypatch.setenv("MARKT_APIFY_ACTOR_ERSATZ", "")        # hier: kein Ersatz-Scraper

    async def _lauf(urls, max_items, zeitlimit_s=None, actor_name=None, max_items_per_query=None):
        gesehen["urls"], gesehen["max_items"] = urls, max_items
        return {"items": [_item(f"t{s}q", 9990), _item(f"t{s}p", 7190)], "usd": 0.05, "run_id": "r1",
                "status": "SUCCEEDED", "dauer_ms": 9}
    monkeypatch.setattr(APIFY, "lauf", _lauf)
    j = welt.run(JOBS.beanspruchen(db))
    erg = welt.run(JOBS.verarbeiten(db, j))
    assert erg["status"] == "ok" and erg["sample_size"] == 2
    assert gesehen["max_items"] == 20 and "sb=p" in gesehen["urls"][0] and "ms=3500%3B10" in gesehen["urls"][0]
    fertig = welt.run(db[K.JOBS].find_one({"id": job["id"]}, {"_id": 0}))
    assert fertig["status"] == "completed" and fertig["actual_rows"] == 2 and fertig["actual_cost"] == 0.05
    assert fertig["sorted_confirmed"] is False, "unsortiert erkannt — nicht als 'die guenstigsten' behauptet"
    ts = welt.run(db[K.TAGESSTATS].find_one({"segment_id": seg["id"]}, {"_id": 0}, sort=[("date", -1)]))
    assert ts["sorted_confirmed"] is False and ts["min_price"] == 7190
    listing = welt.run(db[K.LISTINGS].find_one({"listing_id": f"t{s}p"}, {"_id": 0}))
    assert listing["last_segment_id"] == seg["id"] and listing["model_id"] == seg["model_id"], "Segmentzuordnung"
    # Retry: technischer Fehler -> queued mit Wartezeit; Token-Fehler -> failed
    welt.run(db[K.JOBS].update_one({"id": job["id"]}, {"$set": {"status": "queued", "scheduled_at": K.jetzt_iso(), "attempts": 0}}))

    async def _kaputt(urls, max_items, zeitlimit_s=None, actor_name=None, max_items_per_query=None):
        raise APIFY.ApifyFehler("ausfall", "HTTP 500")
    monkeypatch.setattr(APIFY, "lauf", _kaputt)
    j = welt.run(JOBS.beanspruchen(db))
    assert welt.run(JOBS.verarbeiten(db, j))["status"] == "fehler"
    wieder = welt.run(db[K.JOBS].find_one({"id": job["id"]}, {"_id": 0}))
    assert wieder["status"] == "queued" and wieder["scheduled_at"] > K.jetzt_iso() and "ausfall" in wieder["error"]

    async def _token(urls, max_items, zeitlimit_s=None, actor_name=None, max_items_per_query=None):
        raise APIFY.ApifyFehler("token", "401")
    monkeypatch.setattr(APIFY, "lauf", _token)
    welt.run(db[K.JOBS].update_one({"id": job["id"]}, {"$set": {"scheduled_at": K.jetzt_iso()}}))
    j = welt.run(JOBS.beanspruchen(db))
    welt.run(JOBS.verarbeiten(db, j))
    assert welt.run(db[K.JOBS].find_one({"id": job["id"]}, {"_id": 0}))["status"] == "failed"
    # Budget voll -> failed ohne Lauf (Budget des Monats wird im Dokument gefuehrt, nicht aus der Umgebung)
    welt.run(BUD.budget_setzen(db, 0.01, f"test-{s}"))
    welt.run(db[K.JOBS].update_one({"id": job["id"]}, {"$set": {"status": "queued", "scheduled_at": K.jetzt_iso(), "attempts": 0}}))
    j = welt.run(JOBS.beanspruchen(db))
    assert welt.run(JOBS.verarbeiten(db, j))["status"] == "budget"
    b = welt.run(BUD.dokument(db, f"test-{s}"))
    assert round(b["reserved_usd"], 6) == 0, "keine haengende Reservierung"
    welt.run(db[K.BUDGET].delete_many({"_id": f"test-{s}"}))
    _aufraeumen(welt)


def test_07_taktung_nach_budget(welt, monkeypatch):
    db = welt.db
    monkeypatch.setenv("MARKT_CRAWL_INTERVALL_TAGE", "0")
    monkeypatch.setenv("MARKT_BUDGET_MONAT_USD", "450")
    t = welt.run(JOBS.intervall(db))
    n = t["segmente"]
    import math
    b = K.buendel_groesse()
    # wie jobs.intervall: Abrufe je Tag und Zeilen je Segment kommen aus den Segmenten (v3: crawls_per_day)
    segs = welt.run(db[K.SEGMENTE].find({"enabled": True}, {"_id": 0, "max_items": 1, "crawls_per_day": 1}).to_list(50000))
    laeufe = sum(int(s.get("crawls_per_day") or 1) for s in segs)
    zeilen = sum(int(s.get("max_items") or K.rows_je_segment()) * int(s.get("crawls_per_day") or 1) for s in segs)
    je_tag = K.kosten_buendel_usd(K.actor(), math.ceil(laeufe / b) if laeufe else 0, zeilen)
    erwartet = max(1, math.ceil(je_tag * 30.4 / 450)) if n else 1
    assert t["intervall_tage"] == erwartet and t["buendel"] == b
    assert t["kosten_je_monat_usd"] <= 450 + je_tag * 30.4 / max(1, erwartet) + 1
    monkeypatch.setenv("MARKT_CRAWL_INTERVALL_TAGE", "5")
    assert welt.run(JOBS.intervall(db))["intervall_tage"] == 5 and welt.run(JOBS.intervall(db))["automatisch"] is False


# ---------------------------------------------------------------- Lesewege
def test_08_karte_zum_fahrzeug_mit_ez_und_km_und_nur_lesen(welt):
    w, db = welt.w, welt.db
    _aufraeumen(welt)
    s = w.s
    seg = _segment(w)
    # echte Modelle mit derselben mobile.de-ID (lokale Demo) fuer die Dauer des Tests pausieren
    fremde = [m["id"] for m in welt.run(db[K.MODELLE].find({"make_id": "3500", "model_id": "10", "enabled": True}, {"_id": 0, "id": 1}).to_list(50))]
    welt.run(db[K.MODELLE].update_many({"id": {"$in": fremde}}, {"$set": {"enabled": False}}))
    welt.run(db[K.MODELLE].insert_one(_modell(w)))
    welt.run(db[K.SEGMENTE].insert_one(dict(seg)))
    items = NORM.listings_aus_items([_item(f"t{s}{i}", 18000 + i * 500) for i in range(20)])
    welt.run(SP.verarbeiten(db, seg, items))
    fz = {"make_label": "BMW", "model_label": "320", "model_description": "320d Touring", "mileage": 70000,
          "first_registration": "05/2020", "fuel_label": "Diesel", "power_kw": 140, "list_price": 19400,
          "seller_name": "Vera"}
    welt.run(SEG.km_buckets_setzen(db, [{"min_km": 55001, "max_km": 85000}]))
    welt.run(SEG.ez_buckets_setzen(db, [{"year_from": 2019, "year_to": 2021}]))
    karte = welt.run(ABF.karte(db, fz, f"t{s}3"))
    assert karte and karte["sample_size"] == 20 and karte["min_price"] == 18000 and karte["median_top20_price"] == 22750
    assert karte["segment_id"] == seg["id"] and karte["ez_label"] == "EZ 2019–2021" and karte["km_label"] == "55–85k km"
    assert karte["listing"]["rank_today"] == 4 and karte["listing"]["first_price"] == 19500
    assert karte["preis_vs_median_eur"] == -3350 and "kein Marktmedian" in karte["hinweis"]
    assert "market_median" not in str(karte) and "Vera" not in str(karte)
    # EZ ausserhalb der Bereiche oder km ausserhalb: keine Karte (kein falsches Segment)
    assert welt.run(ABF.karte(db, {**fz, "first_registration": "01/2005"}, None)) is None
    assert welt.run(ABF.karte(db, {**fz, "mileage": 200000}, None)) is None
    assert welt.run(ABF.karte(db, {**fz, "fuel_label": "Benzin"}, None)) is None
    # Chancen und Admin-Lesewege liefern, ohne zu schreiben
    vorher = welt.run(db[K.SNAPSHOTS].count_documents({})), welt.run(db[K.LISTINGS].count_documents({}))
    welt.run(ABF.chancen(db, model_id=seg["model_id"], tage=7))
    welt.run(ABF.modelle_uebersicht(db))
    welt.run(ABF.modell_detail(db, seg["model_id"]))
    welt.run(ABF.segment_zusammenfassung(db, seg["id"]))
    verlauf = welt.run(ABF.segment_verlauf(db, seg["id"], "30d"))
    assert verlauf["reihe"][-1]["sample_size"] == 20 and verlauf["auswertung"]["tage"] >= 1
    liste = welt.run(ABF.segment_listings(db, seg["id"]))
    assert len(liste["listings"]) == 20 and liste["listings"][0]["rank_today"] == 1
    assert welt.run(ABF.listing_verlauf(db, f"t{s}3"))["snapshots"]
    assert (welt.run(db[K.SNAPSHOTS].count_documents({})), welt.run(db[K.LISTINGS].count_documents({}))) == vorher
    welt.run(db[K.KONFIG].delete_many({"_id": {"$in": ["km_buckets", "ez_buckets"]}}))     # zurueck auf Standard
    welt.run(db[K.MODELLE].update_many({"id": {"$in": fremde}}, {"$set": {"enabled": True}}))
    _aufraeumen(welt)


def test_09_hauptweg_unabhaengig_und_routen_nur_lesend():
    """Vergleich, Vertraege, Versand und Fahrer importieren das Markt-Modul
    nicht; die Markt-Lesewege schreiben nicht; die Karte liefert 404 statt 500."""
    root = Path(__file__).resolve().parent.parent
    for datei in ("routes/listings.py", "routes/contracts.py", "listing_identity.py", "link_jobs.py",
                  "mobile_service.py", "routes/protocols.py", "pdf_service.py", "email_service.py"):
        p = root / datei
        if p.exists():
            q = p.read_text(encoding="utf-8")
            import re as _re
            assert not _re.search(r"^\s*(from\s+markt(\.|\s)|import\s+markt(\.|\s|$))", q, _re.M), f"{datei} haengt am Markt-Modul"
    for datei in ("routes/markt.py", "markt/abfrage.py"):
        q = (root / datei).read_text(encoding="utf-8")
        for verboten in ("insert_one", "insert_many", "update_one", "update_many", "delete_one", "delete_many",
                         "find_one_and_update", "replace_one"):
            assert verboten not in q, f"{datei} schreibt ({verboten})"
    r = (root / "routes/markt.py").read_text(encoding="utf-8")
    assert "HTTPException(404" in r and "except Exception" in r


def test_10_karte_route_faengt_fehler(welt, monkeypatch):
    from fastapi import HTTPException
    w, db = welt.w, welt.db
    R = _module("routes.markt")
    vid = f"v_markt_{w.s}"
    welt.run(db.vehicles.insert_one(w.fahrzeug(vid, data={"make_label": "BMW", "model_label": "320"})))

    async def _kaputt(*a, **k):
        raise RuntimeError("Scheduler tot")
    monkeypatch.setattr(ABF, "karte", _kaputt)
    with pytest.raises(HTTPException) as ex:
        welt.run(R.markt_karte(vid, user=w.chef))
    assert ex.value.status_code == 404
    with pytest.raises(HTTPException) as ex:
        welt.run(R.markt_karte(f"fremd_{w.s}", user=w.chef))
    assert ex.value.status_code == 404
    monkeypatch.setenv("MARKT_CHANCEN_AKTIV", "false")
    with pytest.raises(HTTPException) as ex:
        welt.run(R.markt_chancen(user=w.chef))
    assert ex.value.status_code == 404
    welt.run(db.vehicles.delete_many({"id": vid}))


def test_11_segmente_sync_und_ez_bereiche(welt):
    w, db = welt.w, welt.db
    _aufraeumen(welt)
    mid = f"test-sync-{w.s}"
    welt.run(db[K.MODELLE].insert_one({**_modell(w), "id": mid}))
    erg = welt.run(SEG.synchronisieren(db))
    km, ez = welt.run(SEG.km_buckets(db)), welt.run(SEG.ez_buckets(db))
    eigene = welt.run(db[K.SEGMENTE].find({"model_id": mid}, {"_id": 0}).to_list(100))
    assert len(eigene) == len(km) * max(1, len(ez)) and all(s["enabled"] for s in eigene)
    assert any(s["id"] == f"{mid}:2019:50001-100000" and s["ez_label"] == "EZ 2019" for s in eigene), [s["id"] for s in eigene][:3]
    assert len(km) >= 4 and SEG.bucket_fuer_km(km, 70000)["min_km"] == 50001 and SEG.bucket_fuer_km(km, 999999) is None
    # v4 (Befund 26.09.2026): 0-250k km, weil Autos von 2019-2022 im Jahr 2026 bei 50-200k km stehen
    assert len(K.KM_BUCKETS_STANDARD) == 4 and K.KM_BUCKETS_STANDARD[0]["min_km"] == 0 and K.KM_BUCKETS_STANDARD[-1]["max_km"] == 250000
    assert SEG.ez_bucket_fuer_jahr(ez, 2020) == {"year_from": 2020, "year_to": 2020} and SEG.ez_bucket_fuer_jahr(ez, 1999) is None
    # Modell-eigene EZ-Jahre gehen vor
    welt.run(db[K.MODELLE].update_one({"id": mid}, {"$set": {"ez_years": [2015, 2016]}}))
    welt.run(SEG.synchronisieren(db))
    eigene2 = welt.run(db[K.SEGMENTE].find({"model_id": mid, "enabled": True}, {"_id": 0, "id": 1}).to_list(100))
    assert len(eigene2) == len(km) * 2 and all(":2015:" in s["id"] or ":2016:" in s["id"] for s in eigene2)
    welt.run(db[K.MODELLE].update_one({"id": mid}, {"$set": {"enabled": False}}))
    welt.run(SEG.synchronisieren(db))
    assert welt.run(db[K.SEGMENTE].count_documents({"model_id": mid, "enabled": True})) == 0, "deaktiviert, nicht geloescht"
    assert welt.run(db[K.SEGMENTE].count_documents({"model_id": mid})) == len(eigene) + len(eigene2)
    with pytest.raises(ValueError):
        welt.run(SEG.km_buckets_setzen(db, [{"min_km": 5, "max_km": 1}]))
    welt.run(db[K.SEGMENTE].delete_many({"model_id": mid}))
    welt.run(db[K.MODELLE].delete_many({"id": mid}))
    KAT = _module("markt.katalog")
    ms = KAT.start_modelle()
    # v4 (26.09.2026 abends): 72 Eintraege — JEDER mit Getriebe (nie "alle"), Schalt-/Automatik-Doppel wo ueblich
    assert len(ms) == 72 and sum(1 for m in ms if m["enabled"]) == 72, [m["id"] for m in ms if not m["enabled"]]
    assert len({m["id"] for m in ms}) == 72 and all(m["gearbox"] in ("AUTOMATIC_GEAR", "MANUAL_GEAR") for m in ms)
    assert all(m["seed_version"] == KAT.SEED_VERSION and m["km_buckets"] == K.KM_BUCKETS_STANDARD for m in ms)
    b = next(m for m in ms if m["id"] == "bmw-320d")
    assert b["model_id"] == "10" and b["ez_years"] == [2019, 2020, 2021, 2022] and len(b["km_buckets"]) == 4
    assert b["rows"] == 20 and b["crawls_per_day"] == 2 and b["status"] == "active" and b["gearbox"] == "AUTOMATIC_GEAR"
    assert b["label"] == "BMW 320d Automatik"
    touran = {m["id"]: m for m in ms if m["id"].startswith("vw-touran")}
    assert set(touran) == {"vw-touran-20tdi", "vw-touran-20tdi-schalt"}
    assert touran["vw-touran-20tdi-schalt"]["gearbox"] == "MANUAL_GEAR" and touran["vw-touran-20tdi-schalt"]["label"].endswith("Schaltung")
    assert (touran["vw-touran-20tdi"]["power_kw_min"], touran["vw-touran-20tdi"]["power_kw_max"]) == (110, 150), "1.6 TDI (85 kW) draussen"
    assert next(m for m in ms if m["id"] == "skoda-octavia-20tdi")["power_kw_max"] == 140, "RS TDI (147 kW) draussen"
    assert next(m for m in ms if m["id"] == "toyota-yaris-hybrid")["power_kw_min"] == 70, "EZ 2019 = 74 kW"
    assert next(m for m in ms if m["id"] == "toyota-aygo-x")["model_id"] and next(m for m in ms if m["id"] == "bmw-330e")["fuel"] == "HYBRID"



def _item2(lid, preis, pos=1):
    """Zeile des zweiten Scrapers (scrapesmith), wie im Probelauf 26.09.2026."""
    return {"id": lid, "url": f"https://suchen.mobile.de/auto-inserat/bmw-320d/{lid}.html", "title": "BMW 320d Automatik touring",
            "shortTitle": "BMW 320", "subTitle": "d Automatik touring", "make": "BMW", "makeId": "3500", "model": "320", "modelId": "10",
            "category": "Estate car", "price": preis, "priceCurrency": "EUR", "priceType": "FIXED", "priceRating": "VERY_GOOD_PRICE",
            "priceRatingLabel": "Very good price", "priceRatingVehicleOffset": 29, "condition": "Used vehicle", "hasDamage": False,
            "mileageKm": 74000, "firstRegistration": "12/2019", "firstRegistrationYear": 2019, "powerKw": 140, "powerHp": 190,
            "fuelType": "Diesel", "transmission": "Automatic", "numSeats": 5, "doorCount": "4/5", "numberOfPreviousOwners": 1,
            "inspectionStatus": "11/2027", "color": "Black", "images": ["https://img/1"], "numImages": 11,
            "sellerType": "Dealer", "sellerId": "17536211", "sellerName": "GEHEIM GmbH", "sellerPhone": "+49 170 0", "sellerWhatsapp": "+49 170 0",
            "sellerAddress": "Hamburger Straße 273, 22083 Hamburg", "sellerCountry": "DE", "sellerLatitude": 53.58, "sellerLongitude": 10.03,
            "createdAt": "2026-09-25T13:10:04.000Z", "modifiedAt": "2026-09-25T13:10:04.000Z", "renewedAt": "2026-09-25T13:10:04.000Z",
            "searchPosition": pos, "vinHsn": "0005", "vinTsn": "CCT", "isNew": True}


def test_12_normalisierung_scraper2_und_ersatz(welt, monkeypatch):
    # mit Detailseiten kommen die Zeilen in Abrufreihenfolge — die Positionsnummer stellt die Suchreihenfolge her
    ls = NORM.listings_aus_items([_item2("s2", 19500, 2), _item2("s1", 17980, 1)])
    assert [l["price_gross"] for l in ls] == [17980, 19500] and NORM.preise_aufsteigend(ls)
    l = ls[0]
    assert l["power_kw"] == 140 and l["power_ps"] == 190 and l["fuel"] == "Diesel" and l["gearbox"] == "Automatic"
    assert l["seller_type"] == "DEALER" and l["seller_id"] == "17536211" and l["postal_code"] == "22083" and l["city"] == "Hamburg"
    assert l["price_rating"] == {"rating": "VERY_GOOD_PRICE", "label": "Very good price", "thresholds": None, "offset": 29}
    assert l["hsn"] == "0005" and l["tsn"] == "CCT" and l["search_position"] == 1 and l["previous_owners"] == 1
    assert l["make_id"] == "3500" and l["model_id"] == "10" and l["hu"] == "11/2027" and l["mobile_created_at"]
    assert "GEHEIM" not in str(l) and "170" not in str(l), "kein Name, kein Telefon, kein WhatsApp"
    # Eingabeform je Scraper
    monkeypatch.setenv("MARKT_APIFY_DETAILS", "false")
    e2 = APIFY.eingabe("scrapesmith~mobile-de-scraper", ["https://x"], 20)
    assert e2 == {"startUrls": [{"url": "https://x"}], "maxItems": 20, "includeFullDetails": False}
    monkeypatch.setenv("MARKT_APIFY_DETAILS", "true")
    assert APIFY.eingabe("scrapesmith~mobile-de-scraper", ["https://x"], 20)["includeFullDetails"] is True
    e1 = APIFY.eingabe("sourabhbgp~mobile-de-scraper", ["https://x"], 20)
    assert e1 == {"startUrls": ["https://x"], "maxItems": 20}
    assert K.kosten_je_lauf_usd("sourabhbgp~mobile-de-scraper", 20) == 0.064
    monkeypatch.delenv("MARKT_ROW_USD", raising=False)
    monkeypatch.delenv("MARKT_START_USD", raising=False)
    assert K.kosten_je_lauf_usd("scrapesmith~mobile-de-scraper", 20) == 0.019      # 0,005 Start + 20 x 0,0007
    assert K.kosten_buendel_usd("scrapesmith~mobile-de-scraper", 1, 200) == 0.145   # 10 Segmente in einem Lauf
    # Kosten aus den Ereigniszaehlern (Apify bucht Zeilen erst nach dem Lauf nach)
    assert APIFY.kosten_aus_lauf("scrapesmith~mobile-de-scraper", {"usageTotalUsd": 0.005,
                                 "chargedEventCounts": {"apify-actor-start": 1, "apify-default-dataset-item": 80}}, 80) == 0.061
    assert APIFY.kosten_aus_lauf("scrapesmith~mobile-de-scraper", {"usageTotalUsd": 0.09}, 80) == 0.09
    # Ersatz: Standard scheitert (Ausfall) -> Ersatz laeuft; Token-Fehler -> kein Ersatz
    aufrufe = []

    async def _lauf(urls, max_items, zeitlimit_s=None, actor_name=None, max_items_per_query=None):
        actor_name = actor_name or K.actor()
        aufrufe.append(actor_name)
        if actor_name.startswith("scrapesmith"):
            raise APIFY.ApifyFehler("ausfall", "HTTP 500")
        return {"items": [_item("e1", 100)], "usd": 0.007, "run_id": "r", "status": "SUCCEEDED", "dauer_ms": 1, "actor": actor_name}
    monkeypatch.setattr(APIFY, "lauf", _lauf)
    monkeypatch.setenv("MARKT_APIFY_ACTOR", "scrapesmith~mobile-de-scraper")
    monkeypatch.delenv("MARKT_APIFY_ACTOR_ERSATZ", raising=False)
    r = welt.run(APIFY.lauf_mit_ersatz(["https://x"], 20))
    assert r["actor"] == "sourabhbgp~mobile-de-scraper" and "ausfall" in r["ersatz_grund"] and aufrufe == ["scrapesmith~mobile-de-scraper", "sourabhbgp~mobile-de-scraper"]
    aufrufe.clear()

    async def _token(urls, max_items, zeitlimit_s=None, actor_name=None, max_items_per_query=None):
        aufrufe.append(actor_name or K.actor())
        raise APIFY.ApifyFehler("token", "401")
    monkeypatch.setattr(APIFY, "lauf", _token)
    with pytest.raises(APIFY.ApifyFehler):
        welt.run(APIFY.lauf_mit_ersatz(["https://x"], 20))
    assert aufrufe == ["scrapesmith~mobile-de-scraper"], "Token-Fehler: kein zweiter Versuch"
    monkeypatch.setenv("MARKT_APIFY_ACTOR_ERSATZ", "")
    assert K.actor_ersatz() == ""


def test_13_buendel_zuordnung_ueber_inputcontext(welt, monkeypatch):
    """Auftrag v2 Nr. 45: mehrere Segmente in einem Lauf — jede Zeile nur ueber
    inputContext (= Start-URL) zugeordnet, unbekannte Zeilen verworfen, ein
    Segment mit 0 Treffern bekommt einen Alarm, Kosten anteilig."""
    w, db = welt.w, welt.db
    _aufraeumen(welt)
    s = w.s
    welt.run(db[K.MODELLE].insert_one(_modell(w)))
    segs = []
    for i, (mn, mx) in enumerate(((10000, 30000), (30001, 50000), (50001, 85000))):
        seg = {**_segment(w), "id": f"test-320d-{s}:2019-2021:{mn}-{mx}", "min_km": mn, "max_km": mx}
        welt.run(db[K.SEGMENTE].insert_one(dict(seg)))
        segs.append(seg)
    monkeypatch.setenv("MARKT_BUDGET_MONAT_USD", "100")
    monkeypatch.setenv("MARKT_BUENDEL_GROESSE", "10")
    monkeypatch.setenv("MARKT_APIFY_ACTOR_ERSATZ", "")
    monkeypatch.setattr(K, "monat", lambda zeit=None: f"test-{s}")
    welt.run(db[K.BUDGET].delete_many({"_id": f"test-{s}"}))
    for seg in segs:
        welt.run(JOBS.job_sofort(db, seg["id"]))
    aufrufe = []

    async def _lauf(urls, max_items, zeitlimit_s=None, actor_name=None, max_items_per_query=None):
        aufrufe.append({"urls": urls, "max_items": max_items, "je_query": max_items_per_query})
        u0, u1 = urls[0], urls[1]
        items = [{**_item(f"t{s}a1", 9000), "inputContext": u0}, {**_item(f"t{s}a2", 9500), "inputContext": u0},
                 {**_item(f"t{s}b1", 12000), "inputContext": u1},
                 {**_item(f"t{s}x9", 1), "inputContext": "https://fremd.example/x"}]     # unbekannt -> verworfen
        return {"items": items, "usd": 0.0091, "run_id": "r-b", "status": "SUCCEEDED", "dauer_ms": 3, "actor": K.actor()}
    monkeypatch.setattr(APIFY, "lauf", _lauf)
    erg = welt.run(JOBS.einmal(db))
    assert erg["erledigt"] == 3 and len(aufrufe) == 1, "drei Jobs, EIN Lauf"
    assert len(aufrufe[0]["urls"]) == 3 and aufrufe[0]["max_items"] == 60 and aufrufe[0]["je_query"] == 20
    jobs = {j["segment_id"]: j for j in welt.run(db[K.JOBS].find({"segment_id": {"$in": [x["id"] for x in segs]}}, {"_id": 0}).to_list(10))}
    assert [jobs[x["id"]]["actual_rows"] for x in segs] == [2, 1, 0]
    assert all(j["status"] == "completed" and j["buendel"] == 3 for j in jobs.values())
    assert welt.run(db[K.LISTINGS].count_documents({"listing_id": f"t{s}x9"})) == 0, "fremder inputContext nie zugeordnet"
    assert welt.run(db[K.LISTINGS].find_one({"listing_id": f"t{s}b1"}, {"_id": 0}))["last_segment_id"] == segs[1]["id"]
    st = welt.run(db[K.SEGMENTSTATS].find_one({"_id": segs[0]["id"]}, {"_id": 0}))
    assert st["sample_size"] == 2 and st["min_price"] == 9000
    b = welt.run(BUD.dokument(db, f"test-{s}"))
    assert round(b["used_usd"], 4) == 0.0091 and round(b["reserved_usd"], 6) == 0 and b["rows"] == 3 and b["runs"] == 1
    # v4 (Befund 26.09.2026): Kosten je Job summieren sich auf den Monatszaehler (vorher 4,17 $ heute > 3,41 $ Monat)
    assert abs(sum(float(j["actual_cost"]) for j in jobs.values()) - 0.0091) < 0.0005, "Rundung je Job auf 4 Stellen"
    alarme = welt.run(db.betriebsalarme.find({"typ": {"$in": ["markt_keine_treffer", "markt_zuordnung_unklar", "markt_lauf_leer"]}, "offen": True}, {"_id": 0, "typ": 1, "ref": 1}).to_list(20))
    # v4: ein leeres Segment ist eine Marktluecke, KEIN Betriebsalarm mehr (32 Alarme am ersten Tag)
    assert not any(a["typ"] == "markt_keine_treffer" and a["ref"] == segs[2]["id"] for a in alarme)
    assert not any(a["typ"] == "markt_lauf_leer" for a in alarme)
    assert any(a["typ"] == "markt_zuordnung_unklar" for a in alarme)
    seg2 = welt.run(db[K.SEGMENTE].find_one({"id": segs[2]["id"]}, {"_id": 0}))
    assert seg2["leer_in_folge"] == 1 and seg2["last_rows"] == 0
    assert welt.run(db[K.SEGMENTE].find_one({"id": segs[0]["id"]}, {"_id": 0}))["last_rows"] == 2
    # Ein ganzer Buendel-Lauf ohne eine Zeile -> Alarm markt_lauf_leer (Scraper/Sperre), Kosten = Start
    for seg in segs[:2]:
        welt.run(JOBS.job_sofort(db, seg["id"]))

    async def _leer(urls, max_items, zeitlimit_s=None, actor_name=None, max_items_per_query=None):
        return {"items": [], "usd": 0.005, "run_id": "r-leer", "status": "SUCCEEDED", "dauer_ms": 2, "actor": K.actor()}
    monkeypatch.setattr(APIFY, "lauf", _leer)
    erg2 = welt.run(JOBS.einmal(db))
    assert erg2["erledigt"] == 2
    assert welt.run(db.betriebsalarme.find_one({"typ": "markt_lauf_leer", "ref": "r-leer", "offen": True}))
    assert welt.run(db[K.SEGMENTE].find_one({"id": segs[0]["id"]}, {"_id": 0}))["leer_in_folge"] == 1
    b = welt.run(BUD.dokument(db, f"test-{s}"))
    assert round(b["used_usd"], 4) == round(0.0091 + 0.005, 4) and b["runs"] == 2
    welt.run(db.betriebsalarme.delete_many({"typ": {"$in": ["markt_keine_treffer", "markt_zuordnung_unklar"]}, "ref": {"$regex": s}}))
    welt.run(db.betriebsalarme.delete_many({"typ": {"$in": ["markt_zuordnung_unklar", "markt_lauf_leer"]}, "ref": {"$in": ["r-b", "r-leer"]}}))
    welt.run(db[K.BUDGET].delete_many({"_id": f"test-{s}"}))
    # Monitoring liefert die technische Sicht ohne Fehler
    mon = welt.run(ABF.monitoring(db))
    assert "alarme" in mon and "rows_monat" in mon and "budget_uebrig_usd" in mon
    _aufraeumen(welt)


def test_14_suchauftraege_pruefung_prognose_und_verwaltung(welt, monkeypatch):
    """Auftrag v3: Super-Admin legt Marktanalysen selbst an — Pruefung (EZ, km ohne
    Ueberschneidung, Zeilen, Frequenz), Segmente je Modell aus eigener Konfiguration,
    Kostenprognose deterministisch, Duplizieren, Pausieren/Archivieren ohne Datenverlust,
    Tagesplan mit 2 Abrufen ~12 h auseinander."""
    A = _module("markt.auftraege")
    w, db = welt.w, welt.db
    _aufraeumen(welt)
    s = w.s
    monkeypatch.setenv("MARKT_APIFY_ACTOR", "scrapesmith~mobile-de-scraper")
    monkeypatch.delenv("MARKT_ROW_USD", raising=False)
    monkeypatch.delenv("MARKT_START_USD", raising=False)
    monkeypatch.setenv("MARKT_BUENDEL_GROESSE", "10")
    # --- Pruefung
    e = {"make": "BMW", "model": "320", "variant": f"320d Test {s}", "fuel": "diesel", "power_kw_min": 120, "power_kw_max": 145,
         "ez_years": [2019, 2020, 2021, 2022], "km_buckets": [{"min_km": 10000, "max_km": 30000}, {"min_km": 30001, "max_km": 50000},
                                                              {"min_km": 50001, "max_km": 85000}, {"min_km": 85001, "max_km": 115000}],
         "rows": 20, "crawls_per_day": 2, "status": "active"}
    m = A.entwurf_pruefen(e)
    assert m["make_id"] == "3500" and m["model_id"] == "10" and m["fuel"] == "DIESEL" and m["label"] == f"BMW 320d Test {s}"
    assert m["ez_years"] == [2019, 2020, 2021, 2022] and len(m["km_buckets"]) == 4 and m["enabled"] is True
    for kaputt, text in ((({**e, "km_buckets": [{"min_km": 10000, "max_km": 30000}, {"min_km": 25000, "max_km": 50000}]}), "überschneiden"),
                         (({**e, "km_buckets": [{"min_km": 30000, "max_km": 10000}]}), "kleiner"),
                         (({**e, "km_buckets": [{"min_km": -5, "max_km": 10}]}), "außerhalb"),
                         (({**e, "ez_years": []}), "EZ-Jahr"),
                         (({**e, "rows": 500}), "Zeilen"),
                         (({**e, "crawls_per_day": 9}), "Abrufe"),
                         (({**e, "model": "Gibtsnicht"}), "Katalog"),
                         (({**e, "fuel": "WASSER"}), "Kraftstoff")):
        with pytest.raises(A.Ungueltig) as ex:
            A.entwurf_pruefen(kaputt)
        assert text in str(ex.value), (text, str(ex.value))
    von_bis = A.entwurf_pruefen({**e, "ez_years": None, "ez_from": 2016, "ez_to": 2018})
    assert von_bis["ez_years"] == [2016, 2017, 2018]
    # --- Prognose je Modell: 4 x 4 = 16 Segmente, 640 Zeilen/Tag, 19.456/Monat
    p = A.prognose_modell(m)
    assert p["segmente"] == 16 and p["rows_tag"] == 640 and p["rows_monat"] == 19456 and p["laeufe_tag"] == 4
    assert p["kosten_tag_usd"] == round(4 * 0.005 + 640 * 0.0007, 2)
    # --- Anlegen -> Segmente aus eigener Konfiguration
    doc = welt.run(A.anlegen(db, e))
    mid = doc["id"]
    assert mid.startswith("bmw-320d-test")
    eigene = welt.run(db[K.SEGMENTE].find({"model_id": mid, "enabled": True}, {"_id": 0}).to_list(100))
    assert len(eigene) == 16 and all(sg["max_items"] == 20 and sg["crawls_per_day"] == 2 for sg in eigene)
    assert any(sg["id"] == f"{mid}:2020:50001-85000" for sg in eigene)
    # --- Gesamtprognose (mit Entwurf, Budget-Vergleich)
    ges = welt.run(A.prognose(db, {**e, "variant": "Entwurf"}))
    assert ges["segmente"] >= 32 and ges["rows_tag"] >= 1280 and "ueberschritten" in ges and ges["entwurf"]["segmente"] == 16
    # --- Tagesplan: 2 Abrufe je Tag, ~12 h auseinander, Dedupe
    monkeypatch.setenv("MARKT_CRAWL_INTERVALL_TAGE", "1")
    tag = f"2098-01-{int(s[:1], 16) % 28 + 1:02d}"
    welt.run(JOBS.tagesplan(db, tag))
    welt.run(JOBS.tagesplan(db, tag))
    jobs = welt.run(db[K.JOBS].find({"segment_id": eigene[0]["id"]}, {"_id": 0}).sort("scheduled_at", 1).to_list(10))
    assert len(jobs) == 2 and jobs[0]["tag"] == tag and jobs[1]["tag"] == f"{tag}#2"
    d0, d1 = datetime.fromisoformat(jobs[0]["scheduled_at"]), datetime.fromisoformat(jobs[1]["scheduled_at"])
    assert abs((d1 - d0).total_seconds() - 12 * 3600) < 60
    # --- Aendern (km-Bereiche auf 2) -> Segmente 8 aktiv, alte deaktiviert (nicht geloescht)
    welt.run(A.aendern(db, mid, {"km_buckets": [{"min_km": 0, "max_km": 50000}, {"min_km": 50001, "max_km": 120000}]}))
    assert welt.run(db[K.SEGMENTE].count_documents({"model_id": mid, "enabled": True})) == 8
    assert welt.run(db[K.SEGMENTE].count_documents({"model_id": mid})) == 8 + 16
    # --- Duplizieren: gleiche Konfiguration, pausiert, neue ID
    dup = welt.run(A.duplizieren(db, mid, {"model": "520", "variant": f"520d Test {s}"}))
    assert dup["id"] != mid and dup["model_id"] == "17" and dup["status"] == "paused" and dup["ez_years"] == [2019, 2020, 2021, 2022]
    assert welt.run(db[K.SEGMENTE].count_documents({"model_id": dup["id"], "enabled": True})) == 0, "pausiert: keine aktiven Segmente"
    welt.run(A.status_setzen(db, dup["id"], "active"))
    assert welt.run(db[K.SEGMENTE].count_documents({"model_id": dup["id"], "enabled": True})) == 8
    # --- Archivieren: wartende Jobs abgebrochen, Historie bleibt
    welt.run(A.status_setzen(db, mid, "archived"))
    assert welt.run(db[K.SEGMENTE].count_documents({"model_id": mid, "enabled": True})) == 0
    assert welt.run(db[K.JOBS].count_documents({"model_id": mid, "status": "queued"})) == 0
    assert welt.run(db[K.JOBS].count_documents({"model_id": mid, "status": "cancelled"})) >= 2
    assert welt.run(db[K.SEGMENTE].count_documents({"model_id": mid})) == 24, "nichts geloescht"
    uebersicht = welt.run(ABF.modelle_uebersicht(db))
    assert not any(u["id"] == mid for u in uebersicht) and any(u["id"] == dup["id"] for u in uebersicht)
    assert any(u["id"] == mid for u in welt.run(ABF.modelle_uebersicht(db, mit_archiv=True)))
    for x_id in (mid, dup["id"]):
        welt.run(db[K.SEGMENTE].delete_many({"model_id": x_id})); welt.run(db[K.JOBS].delete_many({"model_id": x_id}))
        welt.run(db[K.MODELLE].delete_many({"id": x_id}))
    _aufraeumen(welt)


def test_15_getriebe_crawler_schalter_und_seed_v2(welt, monkeypatch):
    """v4 (Befund Ahmad 26.09.2026): (a) Getriebe des Suchauftrags steht in der URL (tr=)
    und ein Fahrzeug wird nur dem passenden Getriebe zugeordnet (DSG als Halbautomatik
    passt zur Automatik); (b) Segment zum Fahrzeug ueber die Bereiche DES Auftrags, nicht
    die zentralen; (c) Crawler-Schalter im Admin geht vor MARKT_AKTIV; (d) Zeilen werden
    bei den Kosten nie unterschaetzt; (e) Migration 16 hebt alte Seed-Modelle auf v2 und
    ist idempotent."""
    w, db = welt.w, welt.db
    _aufraeumen(welt)
    s = w.s
    URL = _module("markt.url")
    # (a) tr= in der URL
    m_auto = {**_modell(w), "id": f"test-getriebe-{s}", "gearbox": "AUTOMATIC_GEAR", "priority": 1,
              "km_buckets": [{"min_km": 0, "max_km": 50000}, {"min_km": 50001, "max_km": 100000}], "ez_years": [2019, 2020]}
    m_schalt = {**m_auto, "id": f"test-getriebe-{s}-schalt", "gearbox": "MANUAL_GEAR", "priority": 2}
    assert "tr=AUTOMATIC_GEAR" in URL.such_url({"min_km": 0, "max_km": 50000}, m_auto)
    assert "tr=MANUAL_GEAR" in URL.such_url({"min_km": 0, "max_km": 50000}, m_schalt)
    welt.run(db[K.MODELLE].insert_many([dict(m_auto), dict(m_schalt)]))
    welt.run(SEG.synchronisieren(db))
    assert welt.run(db[K.SEGMENTE].count_documents({"model_id": m_auto["id"], "enabled": True})) == 4, "eigene Bereiche des Auftrags"
    fremde = welt.run(db[K.MODELLE].update_many({"make_id": "3500", "model_id": "10", "id": {"$not": {"$regex": f"test-getriebe-{s}"}}, "enabled": True},
                                                {"$set": {"enabled": False, "_test_pausiert": s}}))
    try:
        fz = {"make": "BMW", "model": "320d", "fuel": "Diesel", "power_kw": 140, "mileage": 74000, "first_registration": "05/2019"}
        assert ABF.getriebe_passt("AUTOMATIC_GEAR", "SEMIAUTOMATIC_GEAR") and not ABF.getriebe_passt("MANUAL_GEAR", "AUTOMATIC_GEAR")
        ms = welt.run(ABF.modelle_fuer_fahrzeug(db, {**fz, "gearbox": "MANUAL_GEAR"}))
        assert [m["id"] for m in ms] == [m_schalt["id"]], "Schalter nur zum Schalt-Auftrag"
        ms = welt.run(ABF.modelle_fuer_fahrzeug(db, {**fz, "gearbox": "Halbautomatik"}))
        assert [m["id"] for m in ms] == [m_auto["id"]], "DSG als Halbautomatik -> Automatik"
        ms = welt.run(ABF.modelle_fuer_fahrzeug(db, fz))
        assert {m["id"] for m in ms} == {m_auto["id"], m_schalt["id"]}, "ohne Getriebeangabe beide"
        # (b) Segment ueber die Bereiche des Auftrags (zentral steht etwas anderes)
        welt.run(SEG.km_buckets_setzen(db, [{"min_km": 200000, "max_km": 300000}]))
        seg = welt.run(ABF.segment_fuer_fahrzeug(db, {**fz, "gearbox": "AUTOMATIC_GEAR"}))
        assert seg and seg["id"] == f"{m_auto['id']}:2019:50001-100000"
        assert welt.run(ABF.segment_fuer_fahrzeug(db, {**fz, "gearbox": "AUTOMATIC_GEAR", "mileage": 120000})) is None
    finally:
        welt.run(db[K.KONFIG].delete_many({"_id": {"$in": ["km_buckets", "ez_buckets"]}}))
        welt.run(db[K.MODELLE].update_many({"_test_pausiert": s}, {"$set": {"enabled": True}, "$unset": {"_test_pausiert": ""}}))
    # (c) Crawler-Schalter: Umgebung aus, Knopf an -> an; Knopf aus -> aus trotz Umgebung an
    welt.run(db[K.KONFIG].delete_many({"_id": K.SCHALTER_DOK}))
    try:
        monkeypatch.setenv("MARKT_AKTIV", "false")
        assert welt.run(K.crawler_aktiv(db)) is False and welt.run(K.crawler_quelle(db)) == "env"
        assert welt.run(K.crawler_schalten(db, True, wer="test")) is True
        assert welt.run(K.crawler_aktiv(db)) is True and welt.run(K.crawler_quelle(db)) == "admin"
        monkeypatch.setenv("MARKT_AKTIV", "true")
        welt.run(K.crawler_schalten(db, False))
        assert welt.run(K.crawler_aktiv(db)) is False, "Knopf geht vor Umgebung"
        st = welt.run(ABF.status(db))
        assert st["aktiv"] is False and st["aktiv_quelle"] == "admin" and st["aktiv_env"] is True
        assert welt.run(JOBS.uebersicht(db))["aktiv"] is False
    finally:
        welt.run(db[K.KONFIG].delete_many({"_id": K.SCHALTER_DOK}))
    # Worker-Schleife wartet ohne Schalter/Token, Route nur fuer den Super-Admin
    q = inspect.getsource(JOBS.worker_forever)
    assert "await konfig.crawler_aktiv(db)" in q and "konfig.token()" in q
    r = inspect.getsource(_module("routes.markt_admin"))
    assert '"/admin/market/crawler"' in r and "current_super_admin" in r.split('"/admin/market/crawler"')[1][:400]
    sv = (Path(__file__).resolve().parent.parent / "server.py").read_text(encoding="utf-8")
    assert "if markt_konfig.aktiv():" not in sv, "Worker startet immer, der Schalter entscheidet"
    # (d) Kosten: Zeilen nie unter den gelieferten
    assert APIFY.kosten_aus_lauf("scrapesmith~mobile-de-scraper", {"usageTotalUsd": 0.005, "chargedEventCounts": {"apify-actor-start": 1, "apify-default-dataset-item": 40}}, 133) == round(0.005 + 133 * 0.0007, 4)
    # (e) Migration 16: altes Seed-Modell (v1) -> Getriebe, km 0-250k, kW eng, Label; Fremdes bleibt; idempotent
    MIG = _module("migrationen")
    KAT = _module("markt.katalog")
    alt_seed = next(m for m in KAT.start_modelle() if m["id"] == "vw-touran-20tdi")
    sicherung = welt.run(db[K.MODELLE].find_one({"id": "vw-touran-20tdi"}))
    welt.run(db[K.MODELLE].delete_many({"id": "vw-touran-20tdi"}))
    welt.run(db[K.MODELLE].insert_one({**{k: v for k, v in alt_seed.items() if k not in ("gearbox", "seed_version")},
                                       "label": "Volkswagen Touran 2.0 TDI", "power_kw_min": 85, "power_kw_max": 150,
                                       "km_buckets": [{"min_km": 10000, "max_km": 30000}, {"min_km": 30001, "max_km": 50000},
                                                      {"min_km": 50001, "max_km": 85000}, {"min_km": 85001, "max_km": 115000}],
                                       "created_at": "x", "updated_at": "x"}))
    welt.run(db.betriebsalarme.insert_one({"id": f"al-{s}", "typ": "markt_keine_treffer", "ref": f"test-{s}:2019:10000-30000", "offen": True, "anzahl": 1}))
    try:
        erg = welt.run(MIG.m16_markt_startliste_v2(db))
        assert erg["aktualisiert"] >= 1 and erg["alarme_geschlossen"] >= 1 and erg["segmente"] > 0
        d = welt.run(db[K.MODELLE].find_one({"id": "vw-touran-20tdi"}, {"_id": 0}))
        assert d["gearbox"] == "AUTOMATIC_GEAR" and d["seed_version"] == KAT.SEED_VERSION and d["label"] == "Volkswagen Touran 2.0 TDI Automatik"
        assert (d["power_kw_min"], d["power_kw_max"]) == (110, 150) and d["km_buckets"] == K.KM_BUCKETS_STANDARD
        assert welt.run(db[K.MODELLE].find_one({"id": "vw-touran-20tdi-schalt"}, {"_id": 0}))["gearbox"] == "MANUAL_GEAR"
        assert welt.run(db.betriebsalarme.find_one({"id": f"al-{s}"}))["offen"] is False
        erg2 = welt.run(MIG.m16_markt_startliste_v2(db))
        assert erg2["aktualisiert"] == 0 and erg2["neu"] == 0, "idempotent"
        assert welt.run(db[K.MODELLE].find_one({"id": m_auto["id"]}, {"_id": 0})).get("seed_version") is None, "eigene Auftraege unangetastet"
    finally:
        welt.run(db.betriebsalarme.delete_many({"id": f"al-{s}"}))
        if sicherung:
            welt.run(db[K.MODELLE].delete_many({"id": "vw-touran-20tdi"}))
            welt.run(db[K.MODELLE].insert_one(sicherung))
        for mid in (m_auto["id"], m_schalt["id"]):
            welt.run(db[K.SEGMENTE].delete_many({"model_id": mid}))
            welt.run(db[K.MODELLE].delete_many({"id": mid}))
        _aufraeumen(welt)

