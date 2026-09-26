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
    items = [_item("t1", 9990), _item("t2", 7190), _item("t2", 7190), {"id": "t3"}, _item("t4", 12000),
             {**_item("t5", 5000), "hasDamage": True}, {**_item("t6", 5100), "isDamageCase": True},
             {**_item("t7", 5200), "condition": "Unfallfahrzeug"}]      # Wunsch Ahmad 26.09.: nie Unfallautos
    assert NORM.beschaedigt({"hasDamage": "true"}) and NORM.beschaedigt({"hasDamage": 1}) and NORM.beschaedigt({"condition": "Accident damaged"})
    assert not NORM.beschaedigt({"hasDamage": False, "condition": "Used vehicle"}) and not NORM.beschaedigt({"hasDamage": 0})
    assert not NORM.beschaedigt({"condition": "not damaged"}) and not NORM.beschaedigt({"condition": "accident free"}) and not NORM.beschaedigt({"condition": "unfallfrei"})
    # Entfernungspruefung: beschaedigt heisst NICHT "nicht mehr online"
    assert len(NORM.listings_aus_items([{**_item("t8", 5000), "hasDamage": True}], beschaedigte_verwerfen=False)) == 1
    ls = NORM.listings_aus_items(items)
    assert not any(l["listing_id"] in ("t5", "t6", "t7") for l in ls), "beschaedigte Zeilen verworfen"
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
    antworten = {"1": [], "2": [_item(f"t{s}y", 190)]}     # Nr. 50: die Antwort muss DIESE listing_id tragen

    async def _lauf(urls, max_items, zeitlimit_s=None, actor_name=None, max_items_per_query=None):
        assert max_items == 1
        key = urls[0].split("id=")[-1]
        return {"items": antworten[key], "usd": 0.007, "run_id": "r", "status": "SUCCEEDED", "dauer_ms": 5}
    monkeypatch.setattr(APIFY, "lauf", _lauf)
    kand = welt.run(ENT.kandidaten(db, 10))
    assert {k["listing_id"] for k in kand} >= {f"t{s}x", f"t{s}y"}
    lx = {"source": "mobile", "listing_id": f"t{s}x", "url": "https://suchen.mobile.de/fahrzeuge/details.html?id=1"}
    ly = {"source": "mobile", "listing_id": f"t{s}y", "url": "https://suchen.mobile.de/fahrzeuge/details.html?id=2"}
    # Review 26.09.2026 Nr. 49: EINE leere Antwort ist kein Beleg -> verification_pending, Merker gesetzt
    assert welt.run(ENT.pruefen(db, lx)) == "verification_pending"
    x = welt.run(db[K.LISTINGS].find_one({"listing_id": f"t{s}x"}, {"_id": 0}))
    assert x["active_state"] == "verification_pending" and x["leer_zaehler"] == 1 and x["verification_leer_am"] and "confirmed_removed_at" not in x
    # noch keine 12 h vergangen: nicht wieder Kandidat; nach 12 h ja (zweite Pruefung zuerst)
    assert f"t{s}x" not in {k["listing_id"] for k in welt.run(ENT.kandidaten(db, 50))}
    welt.run(db[K.LISTINGS].update_one({"listing_id": f"t{s}x"}, {"$set": {"verification_leer_am": (K.jetzt() - timedelta(hours=13)).isoformat()}}))
    kand2 = welt.run(ENT.kandidaten(db, 50))
    assert kand2 and kand2[0]["listing_id"] == f"t{s}x"
    # zweite leere Pruefung -> confirmed_removed
    assert welt.run(ENT.pruefen(db, lx)) == "confirmed_removed"
    # Nr. 1: online, aber jetzt beschaedigt -> noch online
    antworten["2"] = [{**antworten["2"][0], "hasDamage": True}]
    assert welt.run(ENT.pruefen(db, ly)) == "not_seen_in_sample"
    x = welt.run(db[K.LISTINGS].find_one({"listing_id": f"t{s}x"}, {"_id": 0}))
    y = welt.run(db[K.LISTINGS].find_one({"listing_id": f"t{s}y"}, {"_id": 0}))
    assert x["active_state"] == "confirmed_removed" and x["confirmed_removed_at"] and x["leer_zaehler"] == 2
    assert y["active_state"] == "not_seen_in_sample" and y["verified_online_at"] and y["current_price"] == 190
    assert "verkauft" not in ABF.ZUSTAND_TEXT["confirmed_removed"].lower() or "kein Beleg" in ABF.ZUSTAND_TEXT["confirmed_removed"]
    assert "NICHT verkauft" in ABF.ZUSTAND_TEXT["not_seen_in_sample"]
    b = welt.run(BUD.dokument(db, f"test-{s}"))
    assert round(b["used_usd"], 3) == 0.021 and b["runs"] == 3 and round(b["reserved_usd"], 6) == 0
    # Nr. 50: Antwort mit FREMDER listing_id -> "unklar", Zustand/Preis unveraendert
    antworten["2"] = [_item("999", 50)]
    assert welt.run(ENT.pruefen(db, ly)) == "unklar"
    y = welt.run(db[K.LISTINGS].find_one({"listing_id": f"t{s}y"}, {"_id": 0}))
    assert y["active_state"] == "not_seen_in_sample" and y["current_price"] == 190 and y["verification_error"] == "id_abweichung"
    # Nr. 49: taucht das Listing nach der ersten leeren Pruefung wieder im Sample auf -> seen, Merker weg
    antworten["2"] = []
    welt.run(db[K.LISTINGS].update_one({"listing_id": f"t{s}y"}, {"$unset": {"verification_error": ""}}))
    assert welt.run(ENT.pruefen(db, ly)) == "verification_pending"
    seg = _segment(w)
    welt.run(db[K.MODELLE].insert_one(_modell(w)))
    welt.run(db[K.SEGMENTE].insert_one(dict(seg)))
    welt.run(SP.verarbeiten(db, seg, NORM.listings_aus_items([_item(f"t{s}y", 200)])))
    y = welt.run(db[K.LISTINGS].find_one({"listing_id": f"t{s}y"}, {"_id": 0}))
    assert y["active_state"] == "seen" and "leer_zaehler" not in y and "verification_leer_am" not in y
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
    # Welle 5 Nr. 17: abgerufen werden rows + Puffer (20 -> 26), die Schaetzung rechnet damit
    assert job["status"] == "queued" and job["estimated_rows"] == 20 and job["abruf_rows"] == 26
    assert job["estimated_cost"] == K.kosten_je_lauf_usd(K.actor(), K.zeilen_mit_puffer(20))
    # beanspruchen: nur faellige (scheduled_at in der Zukunft -> nichts)
    welt.run(db[K.JOBS].update_one({"id": job["id"]}, {"$set": {"scheduled_at": K.jetzt_iso()}}))
    j = welt.run(JOBS.beanspruchen(db))
    assert j and j["id"] == job["id"] and j["status"] == "running" and j["lease_until"] and j["attempts"] == 1
    assert welt.run(JOBS.beanspruchen(db)) is None or welt.run(JOBS.beanspruchen(db))["id"] != job["id"]
    # Lease abgelaufen -> zurueck in die Warteschlange (Neustart verliert nichts)
    welt.run(db[K.JOBS].update_one({"id": job["id"]}, {"$set": {"lease_until": "2000-01-01T00:00:00+00:00"}}))
    assert welt.run(JOBS.stale_zurueck(db)) >= 1
    assert welt.run(db[K.JOBS].find_one({"id": job["id"]}, {"_id": 0}))["status"] == "queued"
    # Scraper-Lauf: max_items 20, sortierte Antwort (unsortiert -> 'data_invalid', siehe Test 17)
    gesehen = {}

    monkeypatch.setenv("MARKT_APIFY_ACTOR_ERSATZ", "")        # hier: kein Ersatz-Scraper

    async def _lauf(urls, max_items, zeitlimit_s=None, actor_name=None, max_items_per_query=None):
        gesehen["urls"], gesehen["max_items"] = urls, max_items
        return {"items": [_item(f"t{s}p", 7190), _item(f"t{s}q", 9990)], "usd": 0.05, "run_id": "r1",
                "status": "SUCCEEDED", "dauer_ms": 9}
    monkeypatch.setattr(APIFY, "lauf", _lauf)
    j = welt.run(JOBS.beanspruchen(db))
    erg = welt.run(JOBS.verarbeiten(db, j))
    assert erg["status"] == "ok" and erg["sample_size"] == 2
    assert gesehen["max_items"] == 26 and "sb=p" in gesehen["urls"][0] and "ms=3500%3B10" in gesehen["urls"][0]
    fertig = welt.run(db[K.JOBS].find_one({"id": job["id"]}, {"_id": 0}))
    assert fertig["status"] == "completed" and fertig["actual_rows"] == 2 and fertig["actual_cost"] == 0.05
    # Welle 5 Nr. 1: ohne searchPosition ist die Sortierung nur monoton — Statistik ja, Top-N nicht bewiesen
    assert fertig["sorted_confirmed"] is True and fertig["sortierung"] == "nur_monoton" and fertig["top_n_bewiesen"] is False
    ts = welt.run(db[K.TAGESSTATS].find_one({"segment_id": seg["id"]}, {"_id": 0}, sort=[("date", -1)]))
    assert ts["min_price"] == 7190 and "sorted_confirmed" not in ts
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
    """Welle 5 Nr. 29/30/31/38: Budget aus dem Monatsdokument (Admin) statt Umgebung; Restbudget /
    verbleibende Tage des Monats (abzueglich Entfernungspruefung) bestimmt die Segmente je Tag;
    Budget 0 -> keine Planung ('ohne Budget pausiert'); Zeilen mit Puffer (Nr. 17)."""
    db = welt.db
    s = welt.w.s
    monkeypatch.setenv("MARKT_CRAWL_INTERVALL_TAGE", "0")
    monkeypatch.setenv("MARKT_BUDGET_MONAT_USD", "450")
    monkeypatch.setattr(K, "monat", lambda zeit=None: f"test-{s}")
    welt.run(db[K.BUDGET].delete_many({"_id": f"test-{s}"}))
    t = welt.run(JOBS.intervall(db))
    n = t["segmente"]
    import math
    b = K.buendel_groesse()
    # wie jobs.intervall: Abrufe je Tag und Zeilen je Segment kommen aus den Segmenten (v3: crawls_per_day)
    segs = welt.run(db[K.SEGMENTE].find({"enabled": True}, {"_id": 0, "max_items": 1, "crawls_per_day": 1}).to_list(50000))
    laeufe = sum(int(s_.get("crawls_per_day") or 1) for s_ in segs)
    zeilen_abruf = sum(K.zeilen_mit_puffer(int(s_.get("max_items") or K.rows_je_segment())) * int(s_.get("crawls_per_day") or 1) for s_ in segs)
    je_tag_alle = K.kosten_buendel_usd(K.actor(), math.ceil(laeufe / b) if laeufe else 0, zeilen_abruf)
    entf = K.entfernung_kosten_je_tag_usd()
    rest_tage = K.rest_tage_im_monat()
    tagesbudget = 450 / rest_tage - entf
    if n and je_tag_alle > 0:
        je_tag_segs = max(1, min(n, int(math.floor(n * tagesbudget / je_tag_alle)))) if tagesbudget > 0 else 1
        erwartet = math.ceil(n / je_tag_segs)
    else:
        je_tag_segs, erwartet = n, 1
    assert t["intervall_tage"] == erwartet and t["segmente_je_tag"] == je_tag_segs and t["buendel"] == b
    assert t["budget_usd"] == 450 and t["restbudget_usd"] == 450 and t["rest_tage"] == rest_tage and t["ohne_budget"] is False
    assert t["entfernung_je_tag_usd"] == round(entf, 2) and t["kosten_je_tag_usd"] >= t["entfernung_je_tag_usd"]
    assert t["rows_abruf_je_tag"] >= t["rows_je_tag"]
    # Nr. 29: das Admin-Budget geht vor der Umgebung; Nr. 31: Verbrauch senkt das Restbudget und das Kontingent
    welt.run(BUD.budget_setzen(db, 90, f"test-{s}"))
    welt.run(db[K.BUDGET].update_one({"_id": f"test-{s}"}, {"$set": {"used_usd": 80.0}}))
    t2 = welt.run(JOBS.intervall(db))
    assert t2["budget_usd"] == 90 and t2["restbudget_usd"] == 10 and t2["verbraucht_usd"] == 80
    assert t2["segmente_je_tag"] <= t["segmente_je_tag"] and t2["intervall_tage"] >= t["intervall_tage"]
    # Nr. 30: Budget 0 -> keine Planung, keine Jobs
    welt.run(BUD.budget_setzen(db, 0, f"test-{s}"))
    t3 = welt.run(JOBS.intervall(db))
    assert t3["ohne_budget"] is True and t3["segmente_je_tag"] == 0 and t3["intervall_tage"] == 0 and t3["status"] == "ohne Budget pausiert"
    plan = welt.run(JOBS.tagesplan(db, "2096-01-01"))
    assert plan["neu"] == 0 and plan["status"] == "ohne Budget pausiert"
    assert welt.run(db[K.JOBS].count_documents({"tag": {"$regex": "^2096-01-01"}})) == 0
    welt.run(BUD.budget_setzen(db, 450, f"test-{s}"))
    monkeypatch.setenv("MARKT_CRAWL_INTERVALL_TAGE", "5")
    assert welt.run(JOBS.intervall(db))["intervall_tage"] == 5 and welt.run(JOBS.intervall(db))["automatisch"] is False
    welt.run(db[K.BUDGET].delete_many({"_id": f"test-{s}"}))


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
    assert any(s["id"] == f"{mid}:2019:55001-80000" and s["ez_label"] == "EZ 2019" for s in eigene), [s["id"] for s in eigene][:3]
    assert len(km) >= 4 and SEG.bucket_fuer_km(km, 70000)["min_km"] == 55001 and SEG.bucket_fuer_km(km, 999999) is None
    # v5 (Wunsch Ahmad 26.09.2026 abends): sechs Bereiche 10-190k, EZ 2018-2022, 10 Zeilen
    assert len(K.KM_BUCKETS_STANDARD) == 6 and K.KM_BUCKETS_STANDARD[0]["min_km"] == 10000 and K.KM_BUCKETS_STANDARD[-1]["max_km"] == 190000
    assert [b["year_from"] for b in K.EZ_BUCKETS_STANDARD] == [2018, 2019, 2020, 2021, 2022]
    assert SEG.ez_bucket_fuer_jahr(ez, 2020) == {"year_from": 2020, "year_to": 2020} and SEG.ez_bucket_fuer_jahr(ez, 1999) is None
    # Migration 17: unveraenderte v2-Auftraege wandern auf v3, angepasste bleiben
    MIG = _module("migrationen")
    v2 = {**_modell(w), "id": f"test-m17-{w.s}", "seed_version": 2, "km_buckets": [dict(b) for b in K.KM_BUCKETS_V2],
          "ez_years": list(K.EZ_JAHRE_V2), "rows": 20, "status": "active"}
    eigen = {**v2, "id": f"test-m17-eigen-{w.s}", "km_buckets": [{"min_km": 0, "max_km": 99000}], "ez_years": [2016], "rows": 7}
    welt.run(db[K.MODELLE].insert_many([dict(v2), dict(eigen)]))
    try:
        monkeypatch_ids = set(m["id"] for m in _module("markt.katalog").start_modelle())
        assert v2["id"] not in monkeypatch_ids  # Test-IDs sind keine Seed-IDs -> Migration prueft ueber die Seed-Liste
        alt_start = _module("markt.katalog").start_modelle
        _module("markt.katalog").start_modelle = lambda: alt_start() + [{"id": v2["id"]}, {"id": eigen["id"]}]
        try:
            erg = welt.run(MIG.m17_markt_standard_v3(db))
        finally:
            _module("markt.katalog").start_modelle = alt_start
        assert erg["aktualisiert"] >= 1
        d = welt.run(db[K.MODELLE].find_one({"id": v2["id"]}, {"_id": 0}))
        assert d["km_buckets"] == K.KM_BUCKETS_STANDARD and d["ez_years"] == [2018, 2019, 2020, 2021, 2022] and d["rows"] == 10 and d["seed_version"] == 3
        e = welt.run(db[K.MODELLE].find_one({"id": eigen["id"]}, {"_id": 0}))
        assert e["km_buckets"] == [{"min_km": 0, "max_km": 99000}] and e["ez_years"] == [2016] and e["rows"] == 7 and e["seed_version"] == 3, "eigene Werte bleiben"
    finally:
        for x_id in (v2["id"], eigen["id"]):
            welt.run(db[K.SEGMENTE].delete_many({"model_id": x_id}))
            welt.run(db[K.MODELLE].delete_many({"id": x_id}))
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
    assert b["model_id"] == "10" and b["ez_years"] == [2018, 2019, 2020, 2021, 2022] and len(b["km_buckets"]) == 6
    assert b["rows"] == 10 and b["crawls_per_day"] == 2 and b["status"] == "active" and b["gearbox"] == "AUTOMATIC_GEAR"
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
        # km passend zum jeweiligen Segment (Review 26.09. Nr. 2: unpassende Zeilen werden verworfen)
        items = [{**_item(f"t{s}a1", 9000, km=20000), "inputContext": u0}, {**_item(f"t{s}a2", 9500, km=25000), "inputContext": u0},
                 {**_item(f"t{s}b1", 12000, km=40000), "inputContext": u1},
                 {**_item(f"t{s}x9", 1), "inputContext": "https://fremd.example/x"}]     # unbekannt -> verworfen
        return {"items": items, "usd": 0.0091, "run_id": "r-b", "status": "SUCCEEDED", "dauer_ms": 3, "actor": K.actor()}
    monkeypatch.setattr(APIFY, "lauf", _lauf)
    erg = welt.run(JOBS.einmal(db))
    assert erg["erledigt"] == 3 and len(aufrufe) == 1, "drei Jobs, EIN Lauf"
    # Welle 5 Nr. 17: je Segment 20 + Puffer 6 = 26 Zeilen abgerufen
    assert len(aufrufe[0]["urls"]) == 3 and aufrufe[0]["max_items"] == 78 and aufrufe[0]["je_query"] == 26
    jobs = {j["segment_id"]: j for j in welt.run(db[K.JOBS].find({"segment_id": {"$in": [x["id"] for x in segs]}}, {"_id": 0}).to_list(10))}
    assert [jobs[x["id"]]["actual_rows"] for x in segs] == [2, 1, 0]
    assert all(j["status"] == "completed" and j["buendel"] == 3 and j["verworfen_filter"] == 0 for j in jobs.values())
    assert welt.run(db[K.LISTINGS].count_documents({"listing_id": f"t{s}x9"})) == 0, "fremder inputContext nie zugeordnet"
    assert welt.run(db[K.LISTINGS].find_one({"listing_id": f"t{s}b1"}, {"_id": 0}))["last_segment_id"] == segs[1]["id"]
    st = welt.run(db[K.SEGMENTSTATS].find_one({"_id": segs[0]["id"]}, {"_id": 0}))
    assert st["sample_size"] == 2 and st["min_price"] == 9000
    b = welt.run(BUD.dokument(db, f"test-{s}"))
    # Welle 5 Nr. 57: rows = gelieferte Rohzeilen (auch die unbekannte), runs = Actor-Starts
    assert round(b["used_usd"], 4) == 0.0091 and round(b["reserved_usd"], 6) == 0 and b["rows"] == 4 and b["runs"] == 1
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
    # Welle 5 Nr. 17: Kosten mit Puffer (26 statt 20 Zeilen je Abruf)
    assert p["rows_abruf_tag"] == 16 * 26 * 2 == 832 and p["kosten_tag_usd"] == round(4 * 0.005 + 832 * 0.0007, 2)
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
    # Welle 5 Nr. 25: Abstand = min(12 h, Restfenster bis 23:30 / 2) — beide Laeufe vor 23:30 deutscher Zeit
    erwartet = min(12 * 3600, (K.tag_ende(tag) - d0).total_seconds() / 2)
    assert abs((d1 - d0).total_seconds() - erwartet) < 60 and d1 <= K.tag_ende(tag)
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


# ---------------------------------------------------------------- Reparaturwelle Review 26.09.2026 (Nr. 2-40)
def _eigenen_beanspruchen(welt, job_id):
    """Genau DIESEN Job als laufend markieren (wie jobs.beanspruchen, aber ohne fremde
    Demo-Jobs der Dev-DB anzufassen)."""
    from pymongo import ReturnDocument
    jetzt = K.jetzt()
    return welt.run(welt.db[K.JOBS].find_one_and_update(
        {"id": job_id, "status": "queued"},
        {"$set": {"status": "running", "claimed_at": jetzt.isoformat(), "worker": JOBS.WORKER,
                  "lease_until": (jetzt + timedelta(seconds=JOBS.lease_sekunden())).isoformat()}, "$inc": {"attempts": 1}},
        projection={"_id": 0}, return_document=ReturnDocument.AFTER))


def test_16_zeilen_gegen_segment_pruefen_und_filter_alarm(welt, monkeypatch):
    """Nr. 2: Zeilen mit falschem EZ/km/kW/Getriebe/Kraftstoff werden verworfen, passende
    bleiben; > 50 % verworfen (ab 3 Zeilen) -> Betriebsalarm markt_filter_ignoriert."""
    w, db = welt.w, welt.db
    _aufraeumen(welt)
    s = w.s
    seg = _segment(w)
    modell = {**_modell(w), "gearbox": "AUTOMATIC_GEAR"}
    # Einzelpruefung
    ok = NORM.listings_aus_items([_item("t1", 100)])[0]
    assert NORM.passt_zum_segment(ok, seg, modell) == (True, "")
    assert NORM.passt_zum_segment({**ok, "first_registration": "05/2017"}, seg, modell)[1].startswith("ez")
    assert NORM.passt_zum_segment({**ok, "first_registration": "05/2022"}, seg, modell)[1].startswith("ez")
    assert NORM.passt_zum_segment({**ok, "mileage_km": 100000}, seg, modell)[1].startswith("km")
    assert NORM.passt_zum_segment({**ok, "mileage_km": 50000}, seg, modell)[1].startswith("km")
    assert NORM.passt_zum_segment({**ok, "power_kw": 90}, seg, modell)[1].startswith("kw")
    assert NORM.passt_zum_segment({**ok, "power_kw": 148}, seg, modell)[0], "kW-Toleranz +-3"
    assert NORM.passt_zum_segment({**ok, "power_kw": 149}, seg, modell)[1].startswith("kw")
    assert NORM.passt_zum_segment({**ok, "gearbox": "Schaltgetriebe"}, seg, modell)[1].startswith("getriebe")
    assert NORM.passt_zum_segment({**ok, "gearbox": "Halbautomatik"}, seg, modell)[0], "DSG passt zur Automatik"
    assert NORM.passt_zum_segment({**ok, "fuel": "Benzin"}, seg, modell)[1].startswith("kraftstoff")
    # Review 26.09. abends P2: fehlende Pflichtfelder werden verworfen ('fehlend: <feld>'), siehe Test 33;
    # verlangt der Auftrag ein Merkmal NICHT, wird es nicht geprueft (nie faelschlich verwerfen)
    assert NORM.passt_zum_segment({**ok, "first_registration": "", "mileage_km": None, "power_kw": None, "fuel": "", "gearbox": ""}, seg, modell) == (False, "fehlend: ez")
    assert NORM.passt_zum_segment({**ok, "gearbox": "Schaltgetriebe", "fuel": "Benzin"}, seg, {**modell, "gearbox": None, "fuel": None})[0]
    assert NORM.passt_zum_segment({**ok, "power_kw": 30}, seg, {**modell, "power_kw_min": None, "power_kw_max": None})[0]
    # Buendel-Lauf: 6 Zeilen, 5 unpassend -> 1 bleibt, Alarm
    welt.run(db[K.MODELLE].insert_one(dict(modell)))
    welt.run(db[K.SEGMENTE].insert_one(dict(seg)))
    monkeypatch.setenv("MARKT_BUDGET_MONAT_USD", "100")
    monkeypatch.setenv("MARKT_APIFY_ACTOR_ERSATZ", "")
    monkeypatch.setattr(K, "monat", lambda zeit=None: f"test-{s}")
    welt.run(db[K.BUDGET].delete_many({"_id": f"test-{s}"}))
    antwort = {"items": [_item(f"t{s}ok", 9000), _item(f"t{s}ez", 9100, ez="03/2017"), _item(f"t{s}km", 9200, km=120000),
                         {**_item(f"t{s}kw", 9300), "power": "90\xa0kW\xa0(122\xa0PS)"},
                         {**_item(f"t{s}tr", 9400), "gearbox": "Schaltgetriebe"}, {**_item(f"t{s}ft", 9500), "fuel": "Benzin"}]}

    async def _lauf(urls, max_items, zeitlimit_s=None, actor_name=None, max_items_per_query=None):
        return {**antwort, "usd": 0.02, "run_id": "r-f", "status": "SUCCEEDED", "dauer_ms": 3, "actor": K.actor()}
    monkeypatch.setattr(APIFY, "lauf", _lauf)
    job = welt.run(JOBS.job_sofort(db, seg["id"]))
    erg = welt.run(JOBS.verarbeiten_buendel(db, [_eigenen_beanspruchen(welt, job["id"])]))
    assert erg["status"] == "ok" and erg["rows"] == 6, "Kosten rechnen mit den gelieferten Zeilen"
    j = welt.run(db[K.JOBS].find_one({"id": job["id"]}, {"_id": 0}))
    assert j["status"] == "completed" and j["actual_rows"] == 1 and j["verworfen_filter"] == 5 and j["gelieferte_rows"] == 6
    assert welt.run(db[K.LISTINGS].count_documents({"listing_id": {"$regex": f"^t{s}"}})) == 1
    assert welt.run(db[K.LISTINGS].find_one({"listing_id": f"t{s}ok"}, {"_id": 0}))["segment_ids"] == [seg["id"]]
    al = welt.run(db.betriebsalarme.find_one({"typ": "markt_filter_ignoriert", "ref": seg["id"], "offen": True}, {"_id": 0}))
    assert al and al["details"]["verworfen"] == 5 and "ez" in al["details"]["gruende"]
    b = welt.run(BUD.dokument(db, f"test-{s}"))
    assert b["rows"] == 6 and round(b["reserved_usd"], 6) == 0
    # naechster Lauf: 2 von 3 passen -> kein Alarm mehr noetig? Erst wenn nichts verworfen wird, schliesst er sich
    antwort["items"] = [_item(f"t{s}ok", 9000), _item(f"t{s}ok2", 9050), _item(f"t{s}ez", 9100, ez="03/2017")]
    job2 = welt.run(JOBS.job_sofort(db, seg["id"]))
    welt.run(JOBS.verarbeiten_buendel(db, [_eigenen_beanspruchen(welt, job2["id"])]))
    assert welt.run(db[K.JOBS].find_one({"id": job2["id"]}, {"_id": 0}))["verworfen_filter"] == 1
    assert welt.run(db.betriebsalarme.find_one({"typ": "markt_filter_ignoriert", "ref": seg["id"], "offen": True}))["anzahl"] == 1, "1 von 3: kein neuer Alarm"
    antwort["items"] = [_item(f"t{s}ok", 9000), _item(f"t{s}ok2", 9050)]
    job3 = welt.run(JOBS.job_sofort(db, seg["id"]))
    welt.run(JOBS.verarbeiten_buendel(db, [_eigenen_beanspruchen(welt, job3["id"])]))
    assert welt.run(db.betriebsalarme.find_one({"typ": "markt_filter_ignoriert", "ref": seg["id"], "offen": True})) is None, "sauberer Lauf schliesst den Alarm"
    welt.run(db.betriebsalarme.delete_many({"typ": "markt_filter_ignoriert", "ref": seg["id"]}))
    welt.run(db[K.BUDGET].delete_many({"_id": f"test-{s}"}))
    _aufraeumen(welt)


def test_17_unsortierter_lauf_wird_data_invalid(welt, monkeypatch):
    """Review 26.09.2026 abends P1: unsortierte Actor-Ergebnisse werden NICHT lokal sortiert und
    gespeichert — der Job endet 'data_invalid' (Grund 'Sortierung unsicher'), Kosten sind gebucht
    (Budget + actual_cost), Alarm markt_sortierung_unsicher offen; keine Snapshots, keine Tages-/
    Segmentstatistik, keine Chancen, kein last_success_at (last_attempt_at ja). Monitoring zaehlt
    'ungueltig' getrennt; ein sauberer Lauf danach schliesst den Alarm."""
    w, db = welt.w, welt.db
    _aufraeumen(welt)
    s = w.s
    seg = _segment(w)
    welt.run(db[K.MODELLE].insert_one(_modell(w)))
    welt.run(db[K.SEGMENTE].insert_one(dict(seg)))
    monkeypatch.setenv("MARKT_BUDGET_MONAT_USD", "100")
    monkeypatch.setenv("MARKT_APIFY_ACTOR_ERSATZ", "")
    monkeypatch.setattr(K, "monat", lambda zeit=None: f"test-{s}")
    welt.run(db[K.BUDGET].delete_many({"_id": f"test-{s}"}))
    antwort = {"items": [_item(f"t{s}c", 15000), _item(f"t{s}a", 12900), _item(f"t{s}b", 19900)]}     # 15000 vor 12900: unsortiert

    async def _lauf(urls, max_items, zeitlimit_s=None, actor_name=None, max_items_per_query=None):
        return {**antwort, "usd": 0.02, "run_id": "r-u", "status": "SUCCEEDED", "dauer_ms": 3, "actor": K.actor()}
    monkeypatch.setattr(APIFY, "lauf", _lauf)
    q = inspect.getsource(SP.verarbeiten)
    assert "sortiert_bestaetigt" not in q, "der alte Pfad 'unsortiert speichern' ist weg"
    job = welt.run(JOBS.job_sofort(db, seg["id"]))
    erg = welt.run(JOBS.verarbeiten_buendel(db, [_eigenen_beanspruchen(welt, job["id"])]))
    assert erg["status"] == "ok" and erg["ergebnisse"] == [{"status": "data_invalid", "grund": "Sortierung unsicher", "sample_size": 0}]
    j = welt.run(db[K.JOBS].find_one({"id": job["id"]}, {"_id": 0}))
    assert j["status"] == "data_invalid" and j["error"] == "Sortierung unsicher" and j["actual_rows"] == 0 and j["gelieferte_rows"] == 3
    assert j["actual_cost"] == 0.02 and j["sorted_confirmed"] is False and "lease_until" not in j
    for coll, filt in ((K.SNAPSHOTS, {"segment_id": seg["id"]}), (K.TAGESSTATS, {"segment_id": seg["id"]}),
                       (K.SEGMENTSTATS, {"_id": seg["id"]}), (K.CHANCEN, {"segment_id": seg["id"]}),
                       (K.LISTINGS, {"listing_id": {"$regex": f"^t{s}"}})):
        assert welt.run(db[coll].count_documents(filt)) == 0, f"{coll}: nichts gespeichert"
    sg = welt.run(db[K.SEGMENTE].find_one({"id": seg["id"]}, {"_id": 0}))
    assert sg.get("last_attempt_at") and not sg.get("last_success_at")
    assert welt.run(db.betriebsalarme.find_one({"typ": "markt_sortierung_unsicher", "ref": seg["id"], "offen": True}))
    b = welt.run(BUD.dokument(db, f"test-{s}"))
    assert round(b["used_usd"], 4) == 0.02 and round(b["reserved_usd"], 6) == 0 and b["rows"] == 3, "Kosten gebucht"
    # Monitoring/Uebersicht: getrennt gezaehlt, nicht als Fehler, nicht als Erfolg
    assert welt.run(JOBS.uebersicht(db))["data_invalid"] >= 1
    mon = welt.run(ABF.monitoring(db))
    assert mon["ungueltig"] >= 1 and any(a["typ"] == "sortierung" and "ungültigen Daten" in a["text"] for a in mon["alarme"])
    assert next(u for u in welt.run(ABF.modelle_uebersicht(db)) if u["id"] == seg["model_id"])["crawl_status"] == "ungueltig"
    detail = welt.run(ABF.modell_detail(db, seg["model_id"]))
    assert next(x for x in detail["segmente"] if x["id"] == seg["id"])["letzter_job"]["status"] == "data_invalid"
    # sauberer Lauf danach: gespeichert, Alarm geschlossen, Chancen wieder moeglich
    antwort["items"] = [_item(f"t{s}a", 12900), _item(f"t{s}c", 15000)]
    job2 = welt.run(JOBS.job_sofort(db, seg["id"]))
    welt.run(JOBS.verarbeiten_buendel(db, [_eigenen_beanspruchen(welt, job2["id"])]))
    assert welt.run(db[K.JOBS].find_one({"id": job2["id"]}, {"_id": 0}))["status"] == "completed"
    assert welt.run(db[K.SEGMENTSTATS].find_one({"_id": seg["id"]}, {"_id": 0}))["sample_size"] == 2
    assert welt.run(db.betriebsalarme.find_one({"typ": "markt_sortierung_unsicher", "ref": seg["id"], "offen": True})) is None
    assert welt.run(db[K.SEGMENTE].find_one({"id": seg["id"]}, {"_id": 0}))["last_success_at"]
    welt.run(db.betriebsalarme.delete_many({"typ": "markt_sortierung_unsicher", "ref": seg["id"]}))
    welt.run(db[K.BUDGET].delete_many({"_id": f"test-{s}"}))
    _aufraeumen(welt)


def test_18_budget_reservierung_mit_ersatz_scraper(welt, monkeypatch):
    """Nr. 10/11: Reservierung = Maximum aus Standard- und Ersatzkosten (Ersatz je URL einzeln);
    reicht nur das Standardbudget, scheitert die Reservierung. intervall() zeigt die Ersatzkosten."""
    w, db = welt.w, welt.db
    _aufraeumen(welt)
    s = w.s
    monkeypatch.setenv("MARKT_APIFY_ACTOR", "scrapesmith~mobile-de-scraper")
    monkeypatch.setenv("MARKT_APIFY_ACTOR_ERSATZ", "sourabhbgp~mobile-de-scraper")
    monkeypatch.delenv("MARKT_ROW_USD", raising=False)
    monkeypatch.delenv("MARKT_START_USD", raising=False)
    # Welle 5 Nr. 13: Standard UND Ersatz koennen anfallen (leerer Primaerlauf, dann Ersatz) -> Summe, nicht Maximum
    assert JOBS.reservierung_usd(1, 20) == round(0.019 + 0.064, 4), "Standard 0,019 + Ersatz 0,064"
    assert JOBS.reservierung_usd(3, 60) == round((0.005 + 60 * 0.0007) + (3 * 0.004 + 60 * 0.003), 4), "3 Segmente = 3 Ersatz-Starts"
    monkeypatch.setenv("MARKT_APIFY_ACTOR_ERSATZ", "")
    assert JOBS.reservierung_usd(3, 60) == round(0.005 + 60 * 0.0007, 4), "ohne Ersatz: Standard"
    monkeypatch.setenv("MARKT_APIFY_ACTOR_ERSATZ", "sourabhbgp~mobile-de-scraper")
    t = welt.run(JOBS.intervall(db))
    assert t["ersatz_actor"] == "sourabhbgp~mobile-de-scraper" and t["ersatz_kosten_je_tag_usd"] >= t["kosten_je_tag_usd"]
    seg = _segment(w)
    welt.run(db[K.MODELLE].insert_one(_modell(w)))
    welt.run(db[K.SEGMENTE].insert_one(dict(seg)))
    monkeypatch.setattr(K, "monat", lambda zeit=None: f"test-{s}")
    welt.run(db[K.BUDGET].delete_many({"_id": f"test-{s}"}))
    welt.run(BUD.budget_setzen(db, 0.03, f"test-{s}"))        # reicht fuer Standard (0,019), nicht fuer Ersatz (0,064)
    aufrufe = []

    async def _lauf(urls, max_items, zeitlimit_s=None, actor_name=None, max_items_per_query=None):
        aufrufe.append(actor_name or K.actor())
        return {"items": [_item(f"t{s}r", 9000)], "usd": 0.019, "run_id": "r-e", "status": "SUCCEEDED", "dauer_ms": 2, "actor": K.actor()}
    monkeypatch.setattr(APIFY, "lauf", _lauf)
    job = welt.run(JOBS.job_sofort(db, seg["id"]))
    assert welt.run(JOBS.verarbeiten_buendel(db, [_eigenen_beanspruchen(welt, job["id"])]))["status"] == "budget"
    assert aufrufe == [] and welt.run(db[K.JOBS].find_one({"id": job["id"]}, {"_id": 0}))["status"] == "failed"
    b = welt.run(BUD.dokument(db, f"test-{s}"))
    assert round(b["reserved_usd"], 6) == 0 and round(b["used_usd"], 6) == 0
    # ohne Ersatz reicht das Budget
    monkeypatch.setenv("MARKT_APIFY_ACTOR_ERSATZ", "")
    job2 = welt.run(JOBS.job_sofort(db, seg["id"]))
    assert welt.run(JOBS.verarbeiten_buendel(db, [_eigenen_beanspruchen(welt, job2["id"])]))["status"] == "ok"
    assert len(aufrufe) == 1
    welt.run(db.betriebsalarme.delete_many({"typ": {"$in": ["markt_budget_voll", "markt_crawl_fehlgeschlagen"]}, "ref": {"$in": [f"test-{s}", seg["id"]]}}))
    welt.run(db[K.BUDGET].delete_many({"_id": f"test-{s}"}))
    _aufraeumen(welt)


def test_19_lease_deckt_buendel_und_fremder_worker_schreibt_nicht(welt, monkeypatch):
    """Nr. 12/13: Lease nach dem Claim deckt (1 + Buendel) x Zeitlimit + 120 s; Heartbeat
    verlaengert nur eigene Jobs; _fertig/_scheitern schreiben nicht, wenn ein anderer
    Worker den Job inzwischen haelt."""
    w, db = welt.w, welt.db
    _aufraeumen(welt)
    seg = _segment(w)
    welt.run(db[K.MODELLE].insert_one(_modell(w)))
    welt.run(db[K.SEGMENTE].insert_one(dict(seg)))
    monkeypatch.setenv("MARKT_JOB_LEASE_SEKUNDEN", "900")
    monkeypatch.setenv("MARKT_LAUF_ZEITLIMIT_SEKUNDEN", "300")
    monkeypatch.setenv("MARKT_BUENDEL_GROESSE", "10")
    assert JOBS.lease_sekunden() == (1 + 10) * 300 + 120 == 3420
    assert JOBS.lease_sekunden(2) == 3 * 300 + 120
    monkeypatch.setenv("MARKT_JOB_LEASE_SEKUNDEN", "7200")
    assert JOBS.lease_sekunden() == 7200, "feste Lease bleibt, wenn sie laenger ist"
    monkeypatch.setenv("MARKT_JOB_LEASE_SEKUNDEN", "900")
    # Claim: lease_until - claimed_at >= 3420 s
    job = welt.run(JOBS.job_sofort(db, seg["id"]))
    welt.run(db[K.JOBS].update_one({"id": job["id"]}, {"$set": {"scheduled_at": "2000-01-01T00:00:00+00:00"}}))
    q = inspect.getsource(JOBS.beanspruchen)
    assert "_lease_bis()" in q
    j = _eigenen_beanspruchen(welt, job["id"])
    dauer = datetime.fromisoformat(j["lease_until"]) - datetime.fromisoformat(j["claimed_at"])
    assert dauer.total_seconds() >= 3420
    # Heartbeat: eigene Jobs ja, fremde nein
    welt.run(db[K.JOBS].update_one({"id": job["id"]}, {"$set": {"lease_until": "2000-01-01T00:00:00+00:00"}}))
    assert welt.run(JOBS.lease_verlaengern(db, [job["id"]], 3)) == 1
    assert welt.run(db[K.JOBS].find_one({"id": job["id"]}, {"_id": 0}))["lease_until"] > K.jetzt_iso()
    welt.run(db[K.JOBS].update_one({"id": job["id"]}, {"$set": {"worker": "markt-anderer"}}))
    assert welt.run(JOBS.lease_verlaengern(db, [job["id"]], 3)) == 0
    # Ergebnis eines anderen Workers wird nicht ueberschrieben
    assert welt.run(JOBS._fertig(db, j, actual_rows=99)) is False
    assert welt.run(JOBS._scheitern(db, j, "x", endgueltig=True)) is False
    assert welt.run(JOBS._scheitern(db, j, "x", endgueltig=False)) is False
    d = welt.run(db[K.JOBS].find_one({"id": job["id"]}, {"_id": 0}))
    assert d["status"] == "running" and d["worker"] == "markt-anderer" and d.get("actual_rows") is None and d.get("error") is None
    assert welt.run(db.betriebsalarme.find_one({"typ": "markt_crawl_fehlgeschlagen", "ref": seg["id"], "offen": True})) is None
    # eigener Job: schreibt
    welt.run(db[K.JOBS].update_one({"id": job["id"]}, {"$set": {"worker": JOBS.WORKER}}))
    assert welt.run(JOBS._fertig(db, j, actual_rows=1)) is True
    assert welt.run(db[K.JOBS].find_one({"id": job["id"]}, {"_id": 0}))["status"] == "completed"
    # Buendel-Verarbeitung verlaengert die Lease vor dem Lauf (Heartbeat) und schreibt nur eigene
    q = inspect.getsource(JOBS._buendel_lauf)        # P6: der Actor-Lauf je Buendel gleicher Zeilenzahl
    assert "await lease_verlaengern(db, [p[\"job\"][\"id\"] for p in plan], len(plan))" in q
    assert q.index("lease_verlaengern") < q.index("apify.lauf_mit_ersatz")
    assert '"worker": WORKER' in inspect.getsource(JOBS._meiner)
    _aufraeumen(welt)


def test_20_listing_in_zwei_segmenten_bleibt_seen(welt):
    """Nr. 14/15: ein Inserat in zwei ueberlappenden Segmenten — in A nicht mehr gesehen,
    in B gesehen -> bleibt 'seen'; segment_ids/model_ids sammeln alle Segmente."""
    w, db = welt.w, welt.db
    _aufraeumen(welt)
    s = w.s
    seg_a = _segment(w)
    m2 = {**_modell(w), "id": f"test-320d-{s}-eigen"}
    seg_b = {**_segment(w), "id": f"test-320d-{s}-eigen:2019-2021:0-100000", "model_id": m2["id"], "min_km": 0, "max_km": 100000}
    welt.run(db[K.MODELLE].insert_many([_modell(w), dict(m2)]))
    welt.run(db[K.SEGMENTE].insert_many([dict(seg_a), dict(seg_b)]))
    x, y = f"t{s}x", f"t{s}y"
    gestern = K.jetzt() - timedelta(days=1)
    welt.run(SP.verarbeiten(db, seg_a, NORM.listings_aus_items([_item(x, 18000), _item(y, 19000)]), beobachtet=gestern))
    welt.run(SP.verarbeiten(db, seg_b, NORM.listings_aus_items([_item(x, 18000)]), beobachtet=gestern))
    lx = welt.run(db[K.LISTINGS].find_one({"listing_id": x}, {"_id": 0}))
    assert lx["segment_ids"] == [seg_a["id"], seg_b["id"]] and lx["model_ids"] == [seg_a["model_id"], m2["id"]]
    assert lx["last_segment_id"] == seg_b["id"]
    # heute: A ohne x (nur y), danach B mit x -> x bleibt seen; y in A gesehen
    welt.run(SP.verarbeiten(db, seg_a, NORM.listings_aus_items([_item(y, 19000)])))
    welt.run(SP.verarbeiten(db, seg_b, NORM.listings_aus_items([_item(x, 18000)])))
    lx = welt.run(db[K.LISTINGS].find_one({"listing_id": x}, {"_id": 0}))
    assert lx["active_state"] == "seen" and "not_seen_since" not in lx and lx["last_seen_tag"] == _tag(0)
    # umgekehrte Reihenfolge am naechsten Tag: B (mit x) zuerst, dann A (ohne x) -> x bleibt seen
    morgen = K.jetzt() + timedelta(days=1)
    welt.run(SP.verarbeiten(db, seg_b, NORM.listings_aus_items([_item(x, 18000)]), beobachtet=morgen))
    welt.run(SP.verarbeiten(db, seg_a, NORM.listings_aus_items([_item(y, 19000)]), beobachtet=morgen))
    lx = welt.run(db[K.LISTINGS].find_one({"listing_id": x}, {"_id": 0}))
    assert lx["active_state"] == "seen" and "not_seen_since" not in lx
    # nirgendwo mehr gesehen -> not_seen_in_sample (kein Verkauf)
    uebermorgen = K.jetzt() + timedelta(days=2)
    welt.run(SP.verarbeiten(db, seg_b, NORM.listings_aus_items([_item(y, 19000)]), beobachtet=uebermorgen))
    lx = welt.run(db[K.LISTINGS].find_one({"listing_id": x}, {"_id": 0}))
    assert lx["active_state"] == "not_seen_in_sample" and lx["not_seen_since"]
    v = welt.run(ABF.listing_verlauf(db, x))
    assert v["segment_ids"] == [seg_a["id"], seg_b["id"]]
    _aufraeumen(welt)


def test_21_zwei_laeufe_je_tag_bleiben_erhalten(welt):
    """Nr. 16/17: zweimal verarbeiten am selben Tag -> 'laeufe' hat 2 Eintraege, Hauptwerte =
    zweiter Lauf; segment_verlauf gibt die Laeufe je Tag mit."""
    w, db = welt.w, welt.db
    _aufraeumen(welt)
    s = w.s
    seg = _segment(w)
    welt.run(db[K.MODELLE].insert_one(_modell(w)))
    welt.run(db[K.SEGMENTE].insert_one(dict(seg)))
    t = _tag(0)
    a, b = f"t{s}a", f"t{s}b"
    welt.run(SP.verarbeiten(db, seg, NORM.listings_aus_items([_item(a, 18000), _item(b, 19000)]), lauf_tag=t))
    welt.run(SP.verarbeiten(db, seg, NORM.listings_aus_items([_item(b, 18500), _item(a, 18600)]), lauf_tag=f"{t}#2"))
    snap = welt.run(db[K.SNAPSHOTS].find_one({"listing_id": a, "segment_id": seg["id"], "date": t}, {"_id": 0}))
    assert snap["price"] == 18600 and snap["rank_in_sample"] == 2, "Hauptfelder = letzter Lauf"
    assert [(x["price"], x["rank_in_sample"], x["tag"]) for x in snap["laeufe"]] == [(18000, 1, t), (18600, 2, f"{t}#2")]
    assert welt.run(db[K.SNAPSHOTS].count_documents({"listing_id": a, "segment_id": seg["id"]})) == 1, "kein zweites Snapshot-Dokument"
    ts = welt.run(db[K.TAGESSTATS].find_one({"segment_id": seg["id"], "date": t}, {"_id": 0}))
    assert ts["min_price"] == 18500 and ts["median_price"] == 18550 and len(ts["laeufe"]) == 2
    assert [(x["tag"], x["min"], x["median"], x["sample_size"]) for x in ts["laeufe"]] == [(t, 18000, 18500, 2), (f"{t}#2", 18500, 18550, 2)]
    assert welt.run(db[K.TAGESSTATS].count_documents({"segment_id": seg["id"], "date": t})) == 1
    # hoechstens 4 Laeufe je Tag
    for i in range(3, 7):
        welt.run(SP.verarbeiten(db, seg, NORM.listings_aus_items([_item(a, 18000 + i)]), lauf_tag=f"{t}#{i}"))
    ts = welt.run(db[K.TAGESSTATS].find_one({"segment_id": seg["id"], "date": t}, {"_id": 0}))
    assert len(ts["laeufe"]) == 4 and ts["laeufe"][-1]["tag"] == f"{t}#6" and ts["min_price"] == 18006
    verlauf = welt.run(ABF.segment_verlauf(db, seg["id"], "7d"))
    assert verlauf["reihe"][-1]["date"] == t and len(verlauf["reihe"][-1]["laeufe"]) == 4 and verlauf["reihe"][-1]["median"] == 18006
    _aufraeumen(welt)


def test_22_testlauf_ueber_alle_segmente(welt, monkeypatch):
    """Nr. 39: der Admin-Testlauf prueft ALLE Segmente des Entwurfs (max. 20) in EINEM
    Buendel-Lauf mit je 2 Treffern; Rueckgabe je Segment (anzahl, ez_ok, km_ok) und 'leer'."""
    A = _module("markt.auftraege")
    monkeypatch.setenv("MARKT_APIFY_ACTOR", "scrapesmith~mobile-de-scraper")
    aufrufe = []

    async def _lauf(urls, max_items, zeitlimit_s=None, actor_name=None, max_items_per_query=None):
        aufrufe.append({"urls": list(urls), "max_items": max_items, "je_query": max_items_per_query})
        items = [{**_item("q1", 9000, km=20000, ez="03/2019"), "inputContext": urls[0]},
                 {**_item("q2", 9500, km=25000, ez="03/2019"), "inputContext": urls[0]},
                 {**_item("q3", 9900, km=40000, ez="03/2021"), "inputContext": urls[1]}]     # EZ falsch fuer Segment 2 (2019)
        return {"items": items, "usd": 0.0092, "run_id": "r-t", "status": "SUCCEEDED", "dauer_ms": 4, "actor": K.actor()}
    monkeypatch.setattr(APIFY, "lauf", _lauf)
    e = {"make": "BMW", "model": "320", "variant": "320d", "fuel": "DIESEL", "ez_years": [2019, 2020],
         "km_buckets": [{"min_km": 10000, "max_km": 30000}, {"min_km": 30001, "max_km": 50000}, {"min_km": 50001, "max_km": 85000}], "rows": 20}
    erg = welt.run(A.testlauf(e, n=1))
    assert len(aufrufe) == 1 and len(aufrufe[0]["urls"]) == 6 and aufrufe[0]["max_items"] == 12 and aufrufe[0]["je_query"] == 2
    assert "fr=2019%3A2019" in aufrufe[0]["urls"][0] and "ml=10000%3A30000" in aufrufe[0]["urls"][0]
    assert erg["segmente_geprueft"] == 6 and erg["segmente_gesamt"] == 6 and erg["leer"] == 4
    assert [x["anzahl"] for x in erg["segmente"]] == [2, 1, 0, 0, 0, 0]
    assert erg["segmente"][0] == {"label": "EZ 2019 · 10–30k km", "anzahl": 2, "ez_ok": True, "km_ok": True}
    assert erg["segmente"][1]["ez_ok"] is False and erg["segmente"][1]["km_ok"] is True
    assert erg["segmente"][2]["ez_ok"] is None
    assert erg["anzahl"] == 1 and erg["zeilen"][0]["title"] and erg["segment"] == "EZ 2019 · 10–30k km" and erg["sortiert"] is True
    assert erg["alle_ez_ok"] is True and erg["alle_km_ok"] is True and erg["usd"] == 0.0092
    # mehr als 20 Segmente: nur die ersten 20 (1 Start + hoechstens 40 Zeilen)
    e2 = {**e, "ez_years": [2017, 2018, 2019, 2020], "km_buckets": e["km_buckets"] + [{"min_km": 85001, "max_km": 100000},
                                                                                   {"min_km": 100001, "max_km": 130000}, {"min_km": 130001, "max_km": 160000}]}
    erg2 = welt.run(A.testlauf(e2, n=5))
    assert len(aufrufe[1]["urls"]) == 20 and aufrufe[1]["max_items"] == 40 and erg2["segmente_geprueft"] == 20 and erg2["segmente_gesamt"] == 24
    assert len(erg2["segmente"]) == 20


def test_23_km_bereiche_zentral_ohne_ueberschneidung(welt):
    """Nr. 40: segmente.km_buckets_setzen prueft Ueberschneidungen wie auftraege.km_bereiche_pruefen."""
    db = welt.db
    with pytest.raises(ValueError) as ex:
        welt.run(SEG.km_buckets_setzen(db, [{"min_km": 10000, "max_km": 30000}, {"min_km": 25000, "max_km": 50000}]))
    assert "überschneiden" in str(ex.value)
    with pytest.raises(ValueError):
        welt.run(SEG.km_buckets_setzen(db, [{"min_km": 0, "max_km": 30000}, {"min_km": 30000, "max_km": 50000}]))     # Grenze doppelt
    try:
        sauber = welt.run(SEG.km_buckets_setzen(db, [{"min_km": 30001, "max_km": 50000}, {"min_km": 0, "max_km": 30000}]))
        assert sauber == [{"min_km": 0, "max_km": 30000}, {"min_km": 30001, "max_km": 50000}], "sortiert gespeichert"
        assert welt.run(SEG.km_buckets(db)) == sauber
    finally:
        welt.run(db[K.KONFIG].delete_many({"_id": "km_buckets"}))     # zurueck auf Standard



# ---------------------------------------------------------------- Reparaturwelle 3 (Review 26.09.2026 Nr. 41-56)
def test_24_zweiter_lauf_am_tag_neu_und_reduktion_vereinigt(welt):
    """Nr. 41: ein morgens erstmals gesehenes Auto ist abends NICHT wieder 'neu im Sample';
    Nr. 42: Tageswerte (neu / Preis gesenkt) sind die Vereinigung ueber alle Laeufe des Tages."""
    w, db = welt.w, welt.db
    _aufraeumen(welt)
    s = w.s
    seg = _segment(w)
    welt.run(db[K.MODELLE].insert_one(_modell(w)))
    welt.run(db[K.SEGMENTE].insert_one(dict(seg)))
    t = _tag(0)
    a, b, c = f"t{s}a", f"t{s}b", f"t{s}c"
    e1 = welt.run(SP.verarbeiten(db, seg, NORM.listings_aus_items([_item(a, 18000), _item(b, 19000)]), lauf_tag=t))
    assert e1["neu_im_sample"] == 2
    # zweiter Lauf: a guenstiger, c neu — a und b sind NICHT mehr neu
    e2 = welt.run(SP.verarbeiten(db, seg, NORM.listings_aus_items([_item(a, 17500), _item(b, 19000), _item(c, 20000)]), lauf_tag=f"{t}#2"))
    assert e2["neu_im_sample"] == 1 and e2["preis_gesunken"] == 1
    snap_a = welt.run(db[K.SNAPSHOTS].find_one({"listing_id": a, "segment_id": seg["id"], "date": t}, {"_id": 0}))
    assert snap_a["new_in_sample"] is True, "morgens neu bleibt fuer den Tag neu"
    assert snap_a["price_reduced_today"] is True and snap_a["price_change_eur"] == -500
    ts = welt.run(db[K.TAGESSTATS].find_one({"segment_id": seg["id"], "date": t}, {"_id": 0}))
    assert ts["new_in_sample_today"] == 3 and sorted(ts["new_in_sample_ids"]) == sorted([a, b, c])
    assert ts["price_reductions_today"] == 1 and ts["price_reduced_ids"] == [a]
    # dritter Lauf: b gesenkt, c nicht mehr dabei — Tageswert bleibt die Vereinigung (a und b)
    welt.run(SP.verarbeiten(db, seg, NORM.listings_aus_items([_item(a, 17500), _item(b, 18000)]), lauf_tag=f"{t}#3"))
    ts = welt.run(db[K.TAGESSTATS].find_one({"segment_id": seg["id"], "date": t}, {"_id": 0}))
    assert ts["price_reductions_today"] == 2 and sorted(ts["price_reduced_ids"]) == [a, b]
    assert ts["new_in_sample_today"] == 3, "c bleibt 'heute neu', auch wenn es im dritten Lauf fehlt"
    st = welt.run(db[K.SEGMENTSTATS].find_one({"_id": seg["id"]}, {"_id": 0}))
    assert st["new_in_sample_today"] == 3 and st["price_reductions_today"] == 2
    # keine Chance "neu unter Minimum" fuer a am selben Tag — a ist im 2. Lauf nicht "neu"
    assert welt.run(db[K.CHANCEN].count_documents({"listing_id": a, "typ": {"$in": ["neues_minimum", "neu_guenstig"]}})) == 0
    # naechster Tag: rank_yesterday kommt aus dem Tagesdokument, a ist nicht neu
    morgen = K.jetzt() + timedelta(days=1)
    welt.run(SP.verarbeiten(db, seg, NORM.listings_aus_items([_item(b, 18000), _item(a, 17500)]), beobachtet=morgen))
    snap = welt.run(db[K.SNAPSHOTS].find_one({"listing_id": a, "segment_id": seg["id"], "date": _tag(1)}, {"_id": 0}))
    assert snap["new_in_sample"] is False and snap["rank_yesterday"] == 1 and snap["rank_in_sample"] == 2
    _aufraeumen(welt)


def _tagesstat(seg_id, tag, median, ids, sample=None):
    return {"segment_id": seg_id, "date": tag, "sample_size": sample if sample is not None else len(ids),
            "min_price": median - 1000, "median_price": median, "avg_price": median, "max_price": median + 1000,
            "p25_price": median - 500, "p75_price": median + 500, "listing_ids": ids, "new_in_sample_today": 0,
            "price_reductions_today": 0, "sorted_confirmed": True}


def test_25_trend_nur_in_toleranz_und_bestandstrend(welt):
    """Nr. 43/44: 7-Tage-Basis nur zwischen t-10 und t-5 (30 Tage: t-37..t-23), sonst None +
    Basisdatum; Nr. 55: Bestandstrend = mittlere Preisaenderung der Autos, die an beiden Tagen
    im Sample waren."""
    w, db = welt.w, welt.db
    _aufraeumen(welt)
    s = w.s
    seg = _segment(w)
    sid = seg["id"]
    welt.run(db[K.MODELLE].insert_one(_modell(w)))
    welt.run(db[K.SEGMENTE].insert_one(dict(seg)))
    a, b, c, x = f"t{s}a", f"t{s}b", f"t{s}c", f"t{s}x"
    t = _tag(0)
    welt.run(db[K.TAGESSTATS].insert_many([_tagesstat(sid, t, 20000, [a, b, c]), _tagesstat(sid, _tag(-7), 21000, [a, b, x]),
                                           _tagesstat(sid, _tag(-25), 23000, [a])]))
    welt.run(db[K.SNAPSHOTS].insert_many([
        {"listing_id": a, "segment_id": sid, "date": t, "price": 19000.0}, {"listing_id": a, "segment_id": sid, "date": _tag(-7), "price": 19500.0},
        {"listing_id": a, "segment_id": sid, "date": _tag(-25), "price": 21000.0},
        {"listing_id": b, "segment_id": sid, "date": t, "price": 20000.0}, {"listing_id": b, "segment_id": sid, "date": _tag(-7), "price": 20500.0},
        {"listing_id": c, "segment_id": sid, "date": t, "price": 21000.0}, {"listing_id": x, "segment_id": sid, "date": _tag(-7), "price": 23000.0}]))
    st = welt.run(SP.segmentstatistik(db, sid, t))
    assert st["trend_7d_eur"] == -1000 and st["trend_7d_basis_date"] == _tag(-7)
    assert st["trend_30d_eur"] == -3000 and st["trend_30d_basis_date"] == _tag(-25), "t-25 liegt in der 30-Tage-Toleranz"
    assert st["trend_7d_bestand_eur"] == -500 and st["trend_7d_bestand_pct"] == -2.5 and st["anzahl_gemeinsam"] == 2
    assert st["trend_30d_bestand_eur"] == -2000 and st["anzahl_gemeinsam_30d"] == 1
    # Basis t-7 weg, stattdessen t-12: ausserhalb der Toleranz -> kein 7-Tage-Trend (statt 12 Tage alt)
    welt.run(db[K.TAGESSTATS].delete_one({"segment_id": sid, "date": _tag(-7)}))
    welt.run(db[K.TAGESSTATS].insert_one(_tagesstat(sid, _tag(-12), 21000, [a, b])))
    st = welt.run(SP.segmentstatistik(db, sid, t))
    assert st["trend_7d_eur"] is None and st["trend_7d_pct"] is None and st["trend_7d_basis_date"] is None
    assert st["trend_7d_bestand_eur"] is None and st["anzahl_gemeinsam"] == 0
    assert st["trend_30d_eur"] == -3000, "30-Tage-Trend unabhaengig davon"
    # zwei Kandidaten in der Toleranz: der dem Zieltag naechste gewinnt (t-6 vor t-9); ohne gemeinsame Autos: None
    welt.run(db[K.TAGESSTATS].insert_many([_tagesstat(sid, _tag(-6), 20800, [x]), _tagesstat(sid, _tag(-9), 21500, [a])]))
    st = welt.run(SP.segmentstatistik(db, sid, t))
    assert st["trend_7d_basis_date"] == _tag(-6) and st["trend_7d_eur"] == -800
    assert st["trend_7d_bestand_eur"] is None and st["anzahl_gemeinsam"] == 0
    # 30 Tage: t-40 liegt ausserhalb (t-37..t-23)
    welt.run(db[K.TAGESSTATS].delete_one({"segment_id": sid, "date": _tag(-25)}))
    welt.run(db[K.TAGESSTATS].insert_one(_tagesstat(sid, _tag(-40), 24000, [a])))
    st = welt.run(SP.segmentstatistik(db, sid, t))
    assert st["trend_30d_eur"] is None and st["trend_30d_basis_date"] is None
    # Karte liefert die Felder mit
    karte_felder = inspect.getsource(ABF.karte)
    for f in ("trend_7d_basis_date", "trend_7d_bestand_eur", "anzahl_gemeinsam", "abdeckung_pct"):
        assert f in karte_felder
    _aufraeumen(welt)


def test_26_datenlage_abdeckung_und_leere_tage(welt):
    """Nr. 45: 'gut' nur mit Abdeckung >= 70 % (mittel >= 40 %); Nr. 54: ein Lauf mit 0 Treffern
    schreibt ein Tagesaggregat (sample_size 0) und zaehlt als beobachtet — getrennt von Tagen mit Treffern."""
    w, db = welt.w, welt.db
    assert SP.datenlage(31, 20, 100) == "gut" and SP.datenlage(31, 20, 69) == "mittel" and SP.datenlage(31, 20, 39) == "niedrig"
    assert SP.datenlage(10, 20, 100) == "mittel" and SP.datenlage(3, 20, 100) == "niedrig"
    _aufraeumen(welt)
    s = w.s
    seg = _segment(w)
    sid = seg["id"]
    welt.run(db[K.MODELLE].insert_one(_modell(w)))
    welt.run(db[K.SEGMENTE].insert_one(dict(seg)))
    a = f"t{s}a"
    # 10 Kalendertage: Tag -9 mit Treffer, Tag -8 leer (0 Treffer), Tage -7..-1 fehlen, heute mit Treffer
    welt.run(SP.verarbeiten(db, seg, NORM.listings_aus_items([_item(a, 18000)]), beobachtet=K.jetzt() - timedelta(days=9)))
    erg = welt.run(SP.verarbeiten(db, seg, [], beobachtet=K.jetzt() - timedelta(days=8)))
    assert erg["sample_size"] == 0
    leer = welt.run(db[K.TAGESSTATS].find_one({"segment_id": sid, "date": _tag(-8)}, {"_id": 0}))
    assert leer and leer["sample_size"] == 0 and leer["median_price"] is None and leer["listing_ids"] == []
    welt.run(SP.verarbeiten(db, seg, NORM.listings_aus_items([_item(a, 18000)])))
    st = welt.run(db[K.SEGMENTSTATS].find_one({"_id": sid}, {"_id": 0}))
    assert st["beobachtete_tage"] == 3 and st["tage_mit_treffern"] == 2 and st["kalendertage"] == 10
    assert st["abdeckung_pct"] == 30.0 and st["erste_beobachtung"] == _tag(-9)
    q = welt.run(ABF.segment_zusammenfassung(db, sid))["qualitaet"]
    assert q["tage_beobachtet"] == 3 and q["tage_mit_treffern"] == 2 and q["abdeckung_pct"] == 30.0 and q["daten_seit"] == _tag(-9)
    # 30 Tage mit je 20 Treffern, aber nur jeder zweite Tag beobachtet -> Abdeckung ~50 % -> mittel, nicht gut
    _aufraeumen(welt)
    welt.run(db[K.MODELLE].insert_one(_modell(w)))
    welt.run(db[K.SEGMENTE].insert_one(dict(seg)))
    docs = [_tagesstat(sid, _tag(-d), 20000, [f"t{s}{i:02d}" for i in range(20)]) for d in range(0, 60, 2)]
    welt.run(db[K.TAGESSTATS].insert_many(docs))
    st = welt.run(SP.segmentstatistik(db, sid, _tag(0)))
    assert st["beobachtete_tage"] == 30 and st["kalendertage"] == 59 and 50 <= st["abdeckung_pct"] <= 51
    assert st["mittlere_sample_groesse"] == 20 and st["datenlage"] == "mittel"
    # jeden Tag beobachtet -> gut
    welt.run(db[K.TAGESSTATS].insert_many([_tagesstat(sid, _tag(-d), 20000, [f"t{s}{i:02d}" for i in range(20)]) for d in range(1, 60, 2)]))
    st = welt.run(SP.segmentstatistik(db, sid, _tag(0)))
    assert st["abdeckung_pct"] == 100.0 and st["datenlage"] == "gut"
    _aufraeumen(welt)


def test_27_chance_neues_minimum_nur_gegen_frischen_stand(welt):
    """Nr. 46: neues_minimum/neu_guenstig nur, wenn der Vergleichsstand hoechstens 3 Tage alt ist."""
    w, db = welt.w, welt.db
    _aufraeumen(welt)
    s = w.s
    seg = _segment(w)
    welt.run(db[K.MODELLE].insert_one(_modell(w)))
    welt.run(db[K.SEGMENTE].insert_one(dict(seg)))
    a, b, d = f"t{s}a", f"t{s}b", f"t{s}d"
    # Stand von vor 5 Tagen, dann heute ein neues Auto unter dem damaligen Minimum -> KEINE Chance
    welt.run(SP.verarbeiten(db, seg, NORM.listings_aus_items([_item(a, 18900), _item(b, 19900)]), beobachtet=K.jetzt() - timedelta(days=5)))
    welt.run(SP.verarbeiten(db, seg, NORM.listings_aus_items([_item(d, 17500), _item(a, 18900), _item(b, 19900)])))
    assert welt.run(db[K.CHANCEN].count_documents({"listing_id": d, "typ": {"$in": ["neues_minimum", "neu_guenstig"]}})) == 0
    # Stand von vor 2 Tagen -> Chance
    _aufraeumen(welt)
    welt.run(db[K.MODELLE].insert_one(_modell(w)))
    welt.run(db[K.SEGMENTE].insert_one(dict(seg)))
    welt.run(SP.verarbeiten(db, seg, NORM.listings_aus_items([_item(a, 18900), _item(b, 19900)]), beobachtet=K.jetzt() - timedelta(days=2)))
    welt.run(SP.verarbeiten(db, seg, NORM.listings_aus_items([_item(d, 17500), _item(a, 18900), _item(b, 19900)])))
    assert welt.run(db[K.CHANCEN].count_documents({"listing_id": d, "typ": "neues_minimum"})) == 1
    _aufraeumen(welt)


def test_28_budget_reaper_verwaiste_reservierung(welt, monkeypatch):
    """Nr. 47: Reservierungen als Eintraege {id, usd, expires_at}; eine verwaiste (Worker weg)
    wird nach Ablauf freigegeben; eine spaete Abrechnung senkt reserved_usd nicht doppelt."""
    w, db = welt.w, welt.db
    s = w.s
    monkeypatch.setenv("MARKT_BUDGET_MONAT_USD", "1")
    monkeypatch.setattr(K, "monat", lambda zeit=None: f"test-{s}")
    welt.run(db[K.BUDGET].delete_many({"_id": f"test-{s}"}))
    assert K.reservierung_ablauf_s(1) == JOBS.lease_sekunden(1) + 60
    r_alt = welt.run(BUD.reservieren(db, 0.30, ablauf_s=-1))        # schon abgelaufen (Worker gestorben)
    r_neu = welt.run(BUD.reservieren(db, 0.20))
    d = welt.run(BUD.dokument(db, f"test-{s}"))
    assert round(d["reserved_usd"], 4) == 0.5 and d["reservierungen_offen"] == 2 and r_alt["id"] != r_neu["id"]
    assert all(e["expires_at"] for e in d["reservierungen"])
    assert welt.run(BUD.reservieren(db, 0.60)) is None, "0,5 + 0,6 > 1"
    erg = welt.run(BUD.verfallene_freigeben(db))
    assert erg["freigegeben"] >= 1 and erg["usd"] >= 0.3
    d = welt.run(BUD.dokument(db, f"test-{s}"))
    assert round(d["reserved_usd"], 4) == 0.2 and [e["id"] for e in d["reservierungen"]] == [r_neu["id"]]
    assert welt.run(BUD.reservieren(db, 0.60)) is not None, "nach dem Reaper wieder Platz"
    # spaete Abrechnung der verfallenen Reservierung: nur Verbrauch, reserved_usd unveraendert
    welt.run(BUD.abrechnen(db, r_alt, 0.05, rows=3))
    d = welt.run(BUD.dokument(db, f"test-{s}"))
    assert round(d["reserved_usd"], 4) == 0.8 and round(d["used_usd"], 4) == 0.05 and d["rows"] == 3 and d["runs"] == 1
    # normale Abrechnung loest genau ihre Reservierung
    welt.run(BUD.abrechnen(db, r_neu, 0.10, rows=10))
    d = welt.run(BUD.dokument(db, f"test-{s}"))
    assert round(d["reserved_usd"], 4) == 0.6 and round(d["used_usd"], 4) == 0.15 and d["reservierungen_offen"] == 1
    # reserved_usd gegen die offenen Eintraege abgleichen (kaputter Zaehler wird korrigiert)
    welt.run(db[K.BUDGET].update_one({"_id": f"test-{s}"}, {"$set": {"reserved_usd": 0.9}}))
    assert welt.run(BUD.verfallene_freigeben(db))["korrigiert"] >= 1
    assert round(welt.run(BUD.dokument(db, f"test-{s}"))["reserved_usd"], 4) == 0.6
    # Reaper laeuft vor jedem Claim und im Aufraeumlauf
    q = inspect.getsource(JOBS.einmal)
    assert "verfallene_freigeben" in q and q.index("verfallene_freigeben") < q.index("beanspruchen")
    assert "verfallene_freigeben" in (Path(__file__).resolve().parent.parent / "cleanup_service.py").read_text(encoding="utf-8")
    welt.run(db[K.BUDGET].delete_many({"_id": f"test-{s}"}))


def test_29_pausieren_waehrend_des_laufs_speichert_nichts(welt, monkeypatch):
    """Nr. 48/51: wird der Suchauftrag WAEHREND des Actor-Laufs pausiert, setzt status_setzen
    cancel_requested am laufenden Job; der Worker speichert keine Zeilen, schliesst den Job als
    'cancelled' mit Grund ab und verbucht die Kosten trotzdem."""
    A = _module("markt.auftraege")
    w, db = welt.w, welt.db
    _aufraeumen(welt)
    s = w.s
    seg = _segment(w)
    m = {**_modell(w), "status": "active"}
    welt.run(db[K.MODELLE].insert_one(dict(m)))
    welt.run(db[K.SEGMENTE].insert_one(dict(seg)))
    monkeypatch.setenv("MARKT_BUDGET_MONAT_USD", "10")
    monkeypatch.setenv("MARKT_APIFY_ACTOR_ERSATZ", "")
    monkeypatch.setattr(K, "monat", lambda zeit=None: f"test-{s}")
    welt.run(db[K.BUDGET].delete_many({"_id": f"test-{s}"}))
    job = welt.run(JOBS.job_sofort(db, seg["id"]))
    j = _eigenen_beanspruchen(welt, job["id"])

    async def _lauf(urls, max_items, zeitlimit_s=None, actor_name=None, max_items_per_query=None):
        await A.status_setzen(db, m["id"], "paused")        # Betreiber pausiert waehrend des Laufs
        return {"items": [_item(f"t{s}p", 7190), _item(f"t{s}q", 9990)], "usd": 0.05, "run_id": "r-p", "status": "SUCCEEDED", "dauer_ms": 3}
    monkeypatch.setattr(APIFY, "lauf", _lauf)
    erg = welt.run(JOBS.verarbeiten(db, j))
    assert erg["status"] == "cancelled" and "pausiert" in erg["grund"]
    d = welt.run(db[K.JOBS].find_one({"id": job["id"]}, {"_id": 0}))
    assert d["status"] == "cancelled" and d["cancel_requested"] is True and "pausiert" in d["error"]
    assert d["actual_rows"] == 0 and d["gelieferte_rows"] == 2 and d["actual_cost"] == 0.05 and "lease_until" not in d
    assert welt.run(db[K.SNAPSHOTS].count_documents({"segment_id": seg["id"]})) == 0, "keine Zeilen gespeichert"
    assert welt.run(db[K.LISTINGS].count_documents({"listing_id": {"$in": [f"t{s}p", f"t{s}q"]}})) == 0
    assert welt.run(db[K.TAGESSTATS].count_documents({"segment_id": seg["id"]})) == 0
    b = welt.run(BUD.dokument(db, f"test-{s}"))
    assert round(b["used_usd"], 4) == 0.05 and round(b["reserved_usd"], 6) == 0, "Kosten verbucht, Reservierung geloest"
    # Segment deaktiviert (ohne Flag) wirkt genauso
    _aufraeumen(welt)
    welt.run(db[K.MODELLE].insert_one(dict(m)))
    welt.run(db[K.SEGMENTE].insert_one(dict(seg)))
    job2 = welt.run(JOBS.job_sofort(db, seg["id"]))
    j2 = _eigenen_beanspruchen(welt, job2["id"])

    async def _lauf2(urls, max_items, zeitlimit_s=None, actor_name=None, max_items_per_query=None):
        await db[K.SEGMENTE].update_one({"id": seg["id"]}, {"$set": {"enabled": False}})
        return {"items": [_item(f"t{s}p", 7190)], "usd": 0.02, "run_id": "r-q", "status": "SUCCEEDED", "dauer_ms": 3}
    monkeypatch.setattr(APIFY, "lauf", _lauf2)
    assert welt.run(JOBS.verarbeiten(db, j2))["status"] == "cancelled"
    assert welt.run(db[K.JOBS].find_one({"id": job2["id"]}, {"_id": 0}))["error"] == "Segment während des Laufs deaktiviert"
    assert welt.run(db[K.SNAPSHOTS].count_documents({"segment_id": seg["id"]})) == 0
    welt.run(db[K.BUDGET].delete_many({"_id": f"test-{s}"}))
    _aufraeumen(welt)


def test_30_kein_sofort_job_fuer_inaktives_segment(welt):
    """Nr. 52: job_sofort lehnt deaktivierte Segmente / pausierte Suchauftraege ab; Route -> 400 Klartext."""
    w, db = welt.w, welt.db
    _aufraeumen(welt)
    seg = _segment(w)
    welt.run(db[K.MODELLE].insert_one(_modell(w)))
    welt.run(db[K.SEGMENTE].insert_one({**seg, "enabled": False}))
    with pytest.raises(ValueError) as ex:
        welt.run(JOBS.job_sofort(db, seg["id"]))
    assert "Segment inaktiv" in str(ex.value)
    assert welt.run(db[K.JOBS].count_documents({"segment_id": seg["id"]})) == 0
    welt.run(db[K.SEGMENTE].update_one({"id": seg["id"]}, {"$set": {"enabled": True}}))
    welt.run(db[K.MODELLE].update_one({"id": seg["model_id"]}, {"$set": {"enabled": False, "status": "paused"}}))
    with pytest.raises(ValueError) as ex:
        welt.run(JOBS.job_sofort(db, seg["id"]))
    assert "inaktiv" in str(ex.value)
    welt.run(db[K.MODELLE].update_one({"id": seg["model_id"]}, {"$set": {"enabled": True, "status": "active"}}))
    assert welt.run(JOBS.job_sofort(db, seg["id"]))["status"] == "queued"
    route = (Path(__file__).resolve().parent.parent / "routes" / "markt_admin.py").read_text(encoding="utf-8")
    i = route.index("crawl-now")
    assert 'HTTPException(404 if "nicht gefunden" in str(e) else 400, str(e))' in route[i:i + 800]
    _aufraeumen(welt)


def test_31_prognose_mit_ersatzkosten(monkeypatch):
    """Nr. 53: die Prognose liefert zusaetzlich die Obergrenze, wenn alles ueber den Ersatz-Scraper liefe."""
    A = _module("markt.auftraege")
    monkeypatch.setenv("MARKT_APIFY_ACTOR", "scrapesmith~mobile-de-scraper")
    monkeypatch.setenv("MARKT_APIFY_ACTOR_ERSATZ", "sourabhbgp~mobile-de-scraper")
    monkeypatch.delenv("MARKT_ROW_USD", raising=False)
    monkeypatch.delenv("MARKT_START_USD", raising=False)
    m = {"ez_years": [2019, 2020], "km_buckets": [{"min_km": 0, "max_km": 50000}, {"min_km": 50001, "max_km": 100000}], "rows": 20, "crawls_per_day": 2}
    p = A.prognose_modell(m)
    assert p["segmente"] == 4 and p["rows_tag"] == 160 and p["rows_abruf_tag"] == 208     # Welle 5 Nr. 17: 26 statt 20 je Abruf
    assert p["kosten_tag_ersatz_usd"] == round(8 * 0.004 + 208 * 0.003, 2) and p["kosten_monat_ersatz_usd"] == round((8 * 0.004 + 208 * 0.003) * 30.4, 2)
    assert p["kosten_monat_ersatz_usd"] > p["kosten_monat_usd"] and p["ersatz_actor"] == "sourabhbgp~mobile-de-scraper"
    monkeypatch.setenv("MARKT_APIFY_ACTOR_ERSATZ", "")
    p2 = A.prognose_modell(m)
    assert p2["kosten_monat_ersatz_usd"] == 0.0 and p2["ersatz_actor"] is None
    q = inspect.getsource(A.prognose)
    assert "kosten_monat_ersatz_usd" in q and "ersatz_ueberschritten" in q


def test_32_sync_storniert_wartende_jobs_deaktivierter_segmente(welt):
    """Nr. 56: aendert sich ein aktiver Suchauftrag, werden die wartenden Jobs der weggefallenen
    Segmente storniert (cancelled, 'Segment deaktiviert'); laufende bleiben (Nr. 48 prueft nach dem Lauf)."""
    w, db = welt.w, welt.db
    _aufraeumen(welt)
    s = w.s
    mid = f"test-sync-{s}"
    m = {"id": mid, "make": "BMW", "model": "320", "variant": "320d", "label": "Sync (Test)", "make_id": "3500", "model_id": "10",
         "status": "active", "enabled": True, "priority": 1, "ez_years": [2020],
         "km_buckets": [{"min_km": 0, "max_km": 50000}, {"min_km": 50001, "max_km": 100000}], "rows": 20, "crawls_per_day": 1}
    welt.run(db[K.MODELLE].insert_one(dict(m)))
    welt.run(SEG.synchronisieren(db))
    weg_id = f"{mid}:2020:50001-100000"
    bleibt_id = f"{mid}:2020:0-50000"
    assert welt.run(db[K.SEGMENTE].count_documents({"model_id": mid, "enabled": True})) == 2
    j_weg = welt.run(JOBS.job_sofort(db, weg_id))
    j_bleibt = welt.run(JOBS.job_sofort(db, bleibt_id))
    welt.run(db[K.JOBS].insert_one({**JOBS._job_doc({"id": weg_id, "model_id": mid, "max_items": 20}, f"test-{s}-run", K.jetzt_iso(), "daily"),
                                    "status": "running", "worker": "markt-x"}))
    welt.run(db[K.MODELLE].update_one({"id": mid}, {"$set": {"km_buckets": [{"min_km": 0, "max_km": 50000}]}}))
    erg = welt.run(SEG.synchronisieren(db))
    assert erg["jobs_storniert"] >= 1
    assert welt.run(db[K.SEGMENTE].find_one({"id": weg_id}, {"_id": 0}))["enabled"] is False
    d = welt.run(db[K.JOBS].find_one({"id": j_weg["id"]}, {"_id": 0}))
    assert d["status"] == "cancelled" and d["error"] == "Segment deaktiviert" and d["finished_at"]
    assert welt.run(db[K.JOBS].find_one({"id": j_bleibt["id"]}, {"_id": 0}))["status"] == "queued"
    assert welt.run(db[K.JOBS].find_one({"segment_id": weg_id, "status": "running"}, {"_id": 0})) is not None
    _aufraeumen(welt)


# ---------------------------------------------------------------- Reparaturwelle 4 (Review 26.09.2026 abends, P1-P7)
def test_33_pflichtfelder_je_zeile(welt, monkeypatch):
    """P2: listing_id, price_gross, EZ-Jahr, km sind Pflicht; Kraftstoff/Getriebe/kW nur, wenn der
    Auftrag sie verlangt. Fehlt eines -> 'fehlend: <feld>' (zaehlt zu verworfen_filter, 50-%-Alarm bleibt)."""
    w, db = welt.w, welt.db
    _aufraeumen(welt)
    s = w.s
    seg = _segment(w)
    modell = {**_modell(w), "gearbox": "AUTOMATIC_GEAR"}
    ok = NORM.listings_aus_items([_item("t1", 100)])[0]
    assert NORM.passt_zum_segment(ok, seg, modell) == (True, "")
    assert NORM.passt_zum_segment({**ok, "listing_id": ""}, seg, modell) == (False, "fehlend: listing_id")
    assert NORM.passt_zum_segment({**ok, "price_gross": None}, seg, modell) == (False, "fehlend: price_gross")
    assert NORM.passt_zum_segment({**ok, "first_registration": ""}, seg, modell) == (False, "fehlend: ez")
    assert NORM.passt_zum_segment({**ok, "mileage_km": None}, seg, modell) == (False, "fehlend: km")
    assert NORM.passt_zum_segment({**ok, "power_kw": None}, seg, modell) == (False, "fehlend: power_kw")
    assert NORM.passt_zum_segment({**ok, "fuel": ""}, seg, modell) == (False, "fehlend: fuel")
    assert NORM.passt_zum_segment({**ok, "gearbox": ""}, seg, modell) == (False, "fehlend: gearbox")
    # verlangt der Auftrag es nicht, ist es keine Pflicht
    frei = {**modell, "fuel": None, "gearbox": None, "power_kw_min": None, "power_kw_max": None}
    assert NORM.passt_zum_segment({**ok, "power_kw": None, "fuel": "", "gearbox": ""}, seg, frei) == (True, "")
    assert NORM.passt_zum_segment({**ok, "power_kw": None}, seg, {**modell, "power_kw_min": None, "power_kw_max": 145})[1] == "fehlend: power_kw"
    # Buendel: 3 Zeilen, 2 ohne EZ -> 1 bleibt, verworfen_filter 2, Alarm mit 'fehlend: ez'
    welt.run(db[K.MODELLE].insert_one(dict(modell)))
    welt.run(db[K.SEGMENTE].insert_one(dict(seg)))
    monkeypatch.setenv("MARKT_BUDGET_MONAT_USD", "100")
    monkeypatch.setenv("MARKT_APIFY_ACTOR_ERSATZ", "")
    monkeypatch.setattr(K, "monat", lambda zeit=None: f"test-{s}")
    welt.run(db[K.BUDGET].delete_many({"_id": f"test-{s}"}))

    async def _lauf(urls, max_items, zeitlimit_s=None, actor_name=None, max_items_per_query=None):
        return {"items": [_item(f"t{s}ok", 9000), _item(f"t{s}e1", 9100, ez=""), _item(f"t{s}e2", 9200, ez=None)],
                "usd": 0.02, "run_id": "r-pf", "status": "SUCCEEDED", "dauer_ms": 3, "actor": K.actor()}
    monkeypatch.setattr(APIFY, "lauf", _lauf)
    job = welt.run(JOBS.job_sofort(db, seg["id"]))
    welt.run(JOBS.verarbeiten_buendel(db, [_eigenen_beanspruchen(welt, job["id"])]))
    j = welt.run(db[K.JOBS].find_one({"id": job["id"]}, {"_id": 0}))
    assert j["status"] == "completed" and j["actual_rows"] == 1 and j["verworfen_filter"] == 2 and j["gelieferte_rows"] == 3
    al = welt.run(db.betriebsalarme.find_one({"typ": "markt_filter_ignoriert", "ref": seg["id"], "offen": True}, {"_id": 0}))
    assert al and "fehlend: ez" in al["details"]["gruende"]
    welt.run(db.betriebsalarme.delete_many({"typ": "markt_filter_ignoriert", "ref": seg["id"]}))
    welt.run(db[K.BUDGET].delete_many({"_id": f"test-{s}"}))
    _aufraeumen(welt)


def test_34_marke_modell_hart_pruefen(welt):
    """P3: make_id/model_id der Zeile (scrapesmith makeId/modelId werden durchgereicht) muessen dem
    Auftrag gleichen; ohne IDs strenger Namensvergleich (Marke gleich, Modellname beginnt mit dem
    Katalognamen); sonst 'fremdes Modell'. Ohne jede Angabe: tolerant."""
    w = welt.w
    seg, modell = _segment(w), _modell(w)          # make_id 3500 / model_id 10, BMW 320
    mit_id = NORM.listings_aus_items([_item2("s1", 17980, 1)])[0]
    assert mit_id["make_id"] == "3500" and mit_id["model_id"] == "10"
    assert NORM.passt_zum_segment(mit_id, seg, modell) == (True, "")
    assert NORM.passt_zum_segment({**mit_id, "model_id": "17"}, seg, modell) == (False, "fremdes Modell")
    assert NORM.passt_zum_segment({**mit_id, "make_id": "1900"}, seg, modell) == (False, "fremdes Modell")
    # IDs gleich, Namen weichen ab (Anzeigename) -> IDs entscheiden
    assert NORM.passt_zum_segment({**mit_id, "model": "3er"}, seg, modell)[0]
    ohne_id = NORM.listings_aus_items([_item("t1", 100)])[0]        # make BMW, model 320, keine IDs
    assert ohne_id["make_id"] is None
    assert NORM.passt_zum_segment(ohne_id, seg, modell) == (True, "")
    assert NORM.passt_zum_segment({**ohne_id, "model": "320d"}, seg, modell)[0], "beginnt mit dem Katalognamen"
    assert NORM.passt_zum_segment({**ohne_id, "model": "520"}, seg, modell) == (False, "fremdes Modell")
    assert NORM.passt_zum_segment({**ohne_id, "make": "Audi"}, seg, modell) == (False, "fremdes Modell")
    assert NORM.passt_zum_segment({**ohne_id, "make": "vw", "model": "Golf"}, seg, {**modell, "make": "Volkswagen", "model": "Golf"})[0], "Alias VW"
    assert NORM.passt_zum_segment({**ohne_id, "make": "Mercedes", "model": "C 220 d"}, seg, {**modell, "make": "Mercedes-Benz", "model": "C 220"})[0]
    assert NORM.passt_zum_segment({**ohne_id, "make": "", "model": ""}, seg, modell)[0], "ohne Angabe tolerant"


def test_35_auftrag_fassung_bei_materieller_aenderung(welt):
    """P4: 320d -> 320i (Kraftstoff) erhoeht die Fassung; neue Segmente tragen ':v2:', die alten
    bleiben deaktiviert mit Daten; rows-Aenderung behaelt die Fassung; die Fahrzeugzuordnung
    nimmt die aktuelle Fassung; Duplikat startet mit Fassung 1."""
    A = _module("markt.auftraege")
    w, db = welt.w, welt.db
    _aufraeumen(welt)
    s = w.s
    e = {"make": "BMW", "model": "320", "variant": f"Test {s} 320d", "fuel": "DIESEL", "gearbox": "AUTOMATIC_GEAR",
         "power_kw_min": 120, "power_kw_max": 145, "ez_years": [2019], "km_buckets": [{"min_km": 0, "max_km": 100000}],
         "rows": 10, "crawls_per_day": 1, "status": "active"}
    h1 = A.definition_hash(A.entwurf_pruefen(e))
    assert h1 == A.definition_hash(A.entwurf_pruefen({**e, "rows": 50, "ez_years": [2020, 2021], "label": "x", "priority": 3})), "nicht materiell"
    assert h1 != A.definition_hash(A.entwurf_pruefen({**e, "fuel": "PETROL"})) and h1 != A.definition_hash(A.entwurf_pruefen({**e, "body": "Kombi"}))
    doc = welt.run(A.anlegen(db, e))
    mid, dup_id = doc["id"], None
    # echte Demo-Auftraege mit derselben mobile.de-ID fuer die Fahrzeugzuordnung pausieren (wie Test 15)
    welt.run(db[K.MODELLE].update_many({"make_id": "3500", "model_id": "10", "id": {"$ne": mid}, "enabled": True},
                                       {"$set": {"enabled": False, "_test_pausiert": s}}))
    try:
        assert doc["version"] == 1 and doc["definition_hash"] == h1
        v1 = f"{mid}:2019:0-100000"
        seg1 = welt.run(db[K.SEGMENTE].find_one({"id": v1}, {"_id": 0}))
        assert seg1 and seg1["enabled"] and seg1["version"] == 1
        welt.run(SP.verarbeiten(db, seg1, NORM.listings_aus_items([_item(f"t{s}a", 18000)])))
        # nicht materiell: rows -> Fassung bleibt, Segment-ID bleibt
        welt.run(A.aendern(db, mid, {"rows": 25}))
        m = welt.run(db[K.MODELLE].find_one({"id": mid}, {"_id": 0}))
        assert m["version"] == 1 and m["rows"] == 25 and welt.run(db[K.SEGMENTE].find_one({"id": v1}, {"_id": 0}))["enabled"]
        # materiell: 320d -> 320i
        welt.run(A.aendern(db, mid, {"fuel": "PETROL", "variant": f"Test {s} 320i", "power_kw_min": 130, "power_kw_max": 140}))
        m = welt.run(db[K.MODELLE].find_one({"id": mid}, {"_id": 0}))
        assert m["version"] == 2 and m["definition_hash"] != h1
        v2 = f"{mid}:v2:2019:0-100000"
        assert SEG.segment_id(mid, {"min_km": 0, "max_km": 100000}, {"year_from": 2019, "year_to": 2019}, 2) == v2
        seg2 = welt.run(db[K.SEGMENTE].find_one({"id": v2}, {"_id": 0}))
        assert seg2 and seg2["enabled"] and seg2["version"] == 2
        alt = welt.run(db[K.SEGMENTE].find_one({"id": v1}, {"_id": 0}))
        assert alt["enabled"] is False, "alte Fassung deaktiviert, nicht geloescht"
        assert welt.run(db[K.TAGESSTATS].count_documents({"segment_id": v1})) == 1 and welt.run(db[K.SEGMENTSTATS].count_documents({"_id": v1})) == 1
        assert welt.run(db[K.SNAPSHOTS].count_documents({"segment_id": v1})) == 1, "Historie der alten Fassung bleibt"
        detail = welt.run(ABF.modell_detail(db, mid))
        assert detail["version"] == 2 and {(x["id"], x["version"], x["enabled"]) for x in detail["segmente"]} == {(v1, 1, False), (v2, 2, True)}
        # Fahrzeugzuordnung: aktuelle Fassung (v2), nie das alte v1-Segment
        fz = {"make": "BMW", "model": "320i", "fuel": "Benzin", "power_kw": 135, "mileage": 50000, "first_registration": "05/2019", "gearbox": "AUTOMATIC_GEAR"}
        found = welt.run(ABF.segment_fuer_fahrzeug(db, fz))
        assert found and found["id"] == v2
        # noch einmal materiell (Karosserie) -> v3; rows danach -> bleibt v3
        welt.run(A.aendern(db, mid, {"body": "EstateCar"}))
        assert welt.run(db[K.MODELLE].find_one({"id": mid}, {"_id": 0}))["version"] == 3
        welt.run(A.aendern(db, mid, {"crawls_per_day": 2}))
        assert welt.run(db[K.MODELLE].find_one({"id": mid}, {"_id": 0}))["version"] == 3
        assert welt.run(db[K.SEGMENTE].count_documents({"model_id": mid, "enabled": True})) == 1
        assert welt.run(db[K.SEGMENTE].find_one({"model_id": mid, "enabled": True}, {"_id": 0}))["id"] == f"{mid}:v3:2019:0-100000"
        # Duplikat: Fassung 1, eigener Hash
        dup = welt.run(A.duplizieren(db, mid, {"variant": f"Test {s} Kopie"}))
        dup_id = dup["id"]
        assert dup["version"] == 1 and dup["definition_hash"] == welt.run(db[K.MODELLE].find_one({"id": mid}, {"_id": 0}))["definition_hash"]
    finally:
        welt.run(db[K.MODELLE].update_many({"_test_pausiert": s}, {"$set": {"enabled": True}, "$unset": {"_test_pausiert": ""}}))
        for x_id in (mid, dup_id):
            if not x_id:
                continue
            for coll in (K.SEGMENTE, K.JOBS, K.TAGESSTATS, K.SNAPSHOTS):
                welt.run(db[coll].delete_many({"model_id": x_id}))
            welt.run(db[K.SEGMENTSTATS].delete_many({"_id": {"$regex": f"^{x_id}:"}}))
            welt.run(db[K.MODELLE].delete_many({"id": x_id}))
        _aufraeumen(welt)


def test_36_tageswert_bleibt_bei_leerem_zweiten_lauf(welt):
    """P5: Lauf 1 mit 10 Zeilen, Lauf 2 mit 0 -> Median bleibt, der leere Lauf steht nur in 'laeufe'
    (leer=True); ein spaeterer Lauf mit Zeilen ueberschreibt; ein Tag, an dem ALLE Laeufe leer
    waren, bleibt sample_size 0 (Marktluecke) und wird vom ersten Lauf mit Zeilen ueberschrieben."""
    w, db = welt.w, welt.db
    _aufraeumen(welt)
    s = w.s
    seg = _segment(w)
    welt.run(db[K.MODELLE].insert_one(_modell(w)))
    welt.run(db[K.SEGMENTE].insert_one(dict(seg)))
    t = _tag(0)
    zehn = NORM.listings_aus_items([_item(f"t{s}{i}", 18000 + i * 100) for i in range(10)])
    e1 = welt.run(SP.verarbeiten(db, seg, zehn, lauf_tag=t))
    assert e1["sample_size"] == 10 and e1["tageswert_behalten"] is False
    e2 = welt.run(SP.verarbeiten(db, seg, [], lauf_tag=f"{t}#2"))
    assert e2["sample_size"] == 0 and e2["tageswert_behalten"] is True
    ts = welt.run(db[K.TAGESSTATS].find_one({"segment_id": seg["id"], "date": t}, {"_id": 0}))
    assert ts["sample_size"] == 10 and ts["median_price"] == 18450 and ts["min_price"] == 18000 and len(ts["listing_ids"]) == 10
    assert [(x["tag"], x["sample_size"], x["leer"]) for x in ts["laeufe"]] == [(t, 10, False), (f"{t}#2", 0, True)]
    st = welt.run(db[K.SEGMENTSTATS].find_one({"_id": seg["id"]}, {"_id": 0}))
    assert st["sample_size"] == 10 and st["median_price"] == 18450
    assert welt.run(db[K.SEGMENTE].find_one({"id": seg["id"]}, {"_id": 0}))["last_sample_size"] == 10
    assert welt.run(db[K.SNAPSHOTS].count_documents({"segment_id": seg["id"], "date": t})) == 10
    # dritter Lauf mit 3 Zeilen: Hauptfelder = dieser Lauf
    welt.run(SP.verarbeiten(db, seg, NORM.listings_aus_items([_item(f"t{s}0", 17000), _item(f"t{s}1", 17500), _item(f"t{s}2", 18000)]), lauf_tag=f"{t}#3"))
    ts = welt.run(db[K.TAGESSTATS].find_one({"segment_id": seg["id"], "date": t}, {"_id": 0}))
    assert ts["sample_size"] == 3 and ts["median_price"] == 17500 and len(ts["laeufe"]) == 3
    # Folgetag: erst leer, dann Zeilen -> ueberschrieben; ganz leerer Tag bleibt 0
    morgen = K.jetzt() + timedelta(days=1)
    welt.run(SP.verarbeiten(db, seg, [], beobachtet=morgen, lauf_tag=_tag(1)))
    ts = welt.run(db[K.TAGESSTATS].find_one({"segment_id": seg["id"], "date": _tag(1)}, {"_id": 0}))
    assert ts["sample_size"] == 0 and ts["laeufe"][0]["leer"] is True
    welt.run(SP.verarbeiten(db, seg, NORM.listings_aus_items([_item(f"t{s}0", 17000)]), beobachtet=morgen, lauf_tag=f"{_tag(1)}#2"))
    ts = welt.run(db[K.TAGESSTATS].find_one({"segment_id": seg["id"], "date": _tag(1)}, {"_id": 0}))
    assert ts["sample_size"] == 1 and ts["median_price"] == 17000
    uebermorgen = K.jetzt() + timedelta(days=2)
    welt.run(SP.verarbeiten(db, seg, [], beobachtet=uebermorgen, lauf_tag=_tag(2)))
    welt.run(SP.verarbeiten(db, seg, [], beobachtet=uebermorgen, lauf_tag=f"{_tag(2)}#2"))
    ts = welt.run(db[K.TAGESSTATS].find_one({"segment_id": seg["id"], "date": _tag(2)}, {"_id": 0}))
    assert ts["sample_size"] == 0 and len(ts["laeufe"]) == 2, "echte Marktluecke"
    _aufraeumen(welt)


def test_37_buendel_je_zeilenzahl(welt, monkeypatch):
    """P6: Segmente mit verschiedener Zeilenzahl laufen in getrennten Buendeln — je Buendel
    max_items = Segmente x Zeilen, maxItemsPerQuery = Zeilen, Reservierung exakt je Buendel."""
    w, db = welt.w, welt.db
    _aufraeumen(welt)
    s = w.s
    welt.run(db[K.MODELLE].insert_one(_modell(w)))
    segs = []
    for mn, mx, rows in ((10000, 30000, 10), (30001, 50000, 20), (50001, 85000, 10)):
        seg = {**_segment(w), "id": f"test-320d-{s}:2019-2021:{mn}-{mx}", "min_km": mn, "max_km": mx, "max_items": rows}
        welt.run(db[K.SEGMENTE].insert_one(dict(seg)))
        segs.append(seg)
    monkeypatch.setenv("MARKT_BUDGET_MONAT_USD", "100")
    monkeypatch.setenv("MARKT_BUENDEL_GROESSE", "10")
    monkeypatch.setenv("MARKT_APIFY_ACTOR_ERSATZ", "")
    monkeypatch.setenv("MARKT_APIFY_ACTOR", "scrapesmith~mobile-de-scraper")
    monkeypatch.delenv("MARKT_ROW_USD", raising=False)
    monkeypatch.delenv("MARKT_START_USD", raising=False)
    monkeypatch.setattr(K, "monat", lambda zeit=None: f"test-{s}")
    welt.run(db[K.BUDGET].delete_many({"_id": f"test-{s}"}))
    assert JOBS.buendel_schluessel(10) == (K.actor(), 10) and JOBS.buendel_schluessel(10) != JOBS.buendel_schluessel(20)
    reserviert = []
    alt_res = BUD.reservieren

    async def _res(db_, usd, **kw):
        reserviert.append(round(float(usd), 4))
        return await alt_res(db_, usd, **kw)
    monkeypatch.setattr(BUD, "reservieren", _res)
    aufrufe = []

    async def _lauf(urls, max_items, zeitlimit_s=None, actor_name=None, max_items_per_query=None):
        aufrufe.append({"urls": list(urls), "max_items": max_items, "je_query": max_items_per_query})
        return {"items": [], "usd": 0.005, "run_id": f"r-{len(aufrufe)}", "status": "SUCCEEDED", "dauer_ms": 2, "actor": K.actor()}
    monkeypatch.setattr(APIFY, "lauf", _lauf)
    jobs = [welt.run(JOBS.job_sofort(db, sg["id"])) for sg in segs]
    assert [j["max_items"] for j in jobs] == [10, 20, 10]
    # verarbeiten_buendel selbst trennt gemischte Zeilenzahlen (auch bei direktem Aufruf)
    erg = welt.run(JOBS.verarbeiten_buendel(db, [_eigenen_beanspruchen(welt, j["id"]) for j in jobs]))
    assert erg["status"] == "ok" and erg["jobs"] == 3 and erg["buendel"] == 2 and len(erg["ergebnisse"]) == 3
    # Welle 5 Nr. 17: 10 -> 13, 20 -> 26 Zeilen je Abruf (Puffer)
    assert sorted((len(a["urls"]), a["max_items"], a["je_query"]) for a in aufrufe) == [(1, 26, None), (2, 26, 13)]
    assert sorted(reserviert) == sorted([round(0.005 + 26 * 0.0007, 4), round(0.005 + 26 * 0.0007, 4)]), "je Buendel exakt"
    assert all(welt.run(db[K.JOBS].find_one({"id": j["id"]}, {"_id": 0}))["status"] == "completed" for j in jobs)
    assert welt.run(BUD.dokument(db, f"test-{s}"))["runs"] == 2
    # Worker-Takt: der erste faellige Job bestimmt die Zeilenzahl des Buendels
    aufrufe.clear()
    jobs2 = [welt.run(JOBS.job_sofort(db, sg["id"])) for sg in segs]
    welt.run(db[K.JOBS].update_many({"id": {"$in": [j["id"] for j in jobs2]}}, {"$set": {"scheduled_at": "2000-01-01T00:00:00+00:00"}}))
    assert welt.run(JOBS.beanspruchen(db, max_items=99)) is None
    erg2 = welt.run(JOBS.einmal(db))
    assert erg2["erledigt"] == 3 and len(aufrufe) == 2
    assert all(a["max_items"] == len(a["urls"]) * (a["je_query"] or a["max_items"]) for a in aufrufe), "nie gemischt"
    welt.run(db.betriebsalarme.delete_many({"typ": "markt_lauf_leer", "ref": {"$regex": "^r-"}}))
    welt.run(db[K.BUDGET].delete_many({"_id": f"test-{s}"}))
    _aufraeumen(welt)


def test_38_karosserie_in_url_validator_und_auftrag():
    """P7: body als mobile.de-Code (c=…, Codes wie im Vergleich), Zeile.category gegen den
    Auftrag (unbekannt tolerant), Formularwerte werden zugeordnet, Startliste: nur 'Passat Variant' = Kombi."""
    A = _module("markt.auftraege")
    KAT = _module("markt.katalog")
    assert NORM.KAROSSERIE_CODES == ("Limousine", "EstateCar", "OffRoad", "Cabrio", "SportsCar", "SmallCar", "Van")
    for text, code in (("Estate car", "EstateCar"), ("EstateCar", "EstateCar"), ("Kombi", "EstateCar"), ("Saloon", "Limousine"),
                       ("Limousine", "Limousine"), ("SUV/Off-road", "OffRoad"), ("SUV / Geländewagen", "OffRoad"), ("Cabrio", "Cabrio"),
                       ("Cabriolet / Roadster", "Cabrio"), ("Sports car", "SportsCar"), ("Coupé", "SportsCar"), ("Small car", "SmallCar"),
                       ("Kleinwagen", "SmallCar"), ("Van", "Van"), ("Van / Kleinbus", "Van"), ("Other", None), ("", None)):
        assert NORM.karosserie_code(text) == code, text
    modell = {"id": "test-body", "make": "BMW", "model": "320", "make_id": "3500", "model_id": "10", "fuel": "DIESEL",
              "power_kw_min": 120, "power_kw_max": 145, "body": "EstateCar"}
    seg = {"min_km": 0, "max_km": 100000, "year_from": 2019, "year_to": 2021}
    u = URL.such_url(seg, modell)
    assert "&c=EstateCar&" in u and "&c=" not in URL.such_url(seg, {**modell, "body": None}) and "&c=" not in URL.such_url(seg, {**modell, "body": "Rakete"})
    ok = NORM.listings_aus_items([_item("t1", 100)])[0]           # category EstateCar
    assert NORM.passt_zum_segment(ok, seg, modell) == (True, "")
    assert NORM.passt_zum_segment({**ok, "category": "Saloon"}, seg, modell) == (False, "karosserie Limousine != EstateCar")
    assert NORM.passt_zum_segment({**ok, "category": "Other"}, seg, modell)[0] and NORM.passt_zum_segment({**ok, "category": ""}, seg, modell)[0], "unbekannt tolerant"
    assert NORM.passt_zum_segment({**ok, "category": "Saloon"}, seg, {**modell, "body": None})[0]
    e = {"make": "BMW", "model": "320", "variant": "320d", "ez_years": [2019], "km_buckets": [{"min_km": 0, "max_km": 100000}]}
    assert A.entwurf_pruefen(e)["body"] is None
    assert A.entwurf_pruefen({**e, "body": "EstateCar"})["body"] == "EstateCar"
    assert A.entwurf_pruefen({**e, "body": "Kombi"})["body"] == "EstateCar" and A.entwurf_pruefen({**e, "body": "SUV"})["body"] == "OffRoad"
    with pytest.raises(A.Ungueltig) as ex:
        A.entwurf_pruefen({**e, "body": "Rakete"})
    assert "Karosserie" in str(ex.value)
    assert list(A.KAROSSERIE) == [""] + list(NORM.KAROSSERIE_CODES)
    ms = {m["id"]: m for m in KAT.start_modelle()}
    assert ms["vw-passat-20tdi"]["body"] == "EstateCar" and ms["vw-passat-20tdi-schalt"]["body"] == "EstateCar"
    assert all(m["body"] is None for k, m in ms.items() if not k.startswith("vw-passat"))
    r = (Path(__file__).resolve().parent.parent / "routes" / "markt_admin.py").read_text(encoding="utf-8")
    assert '"karosserie": list(auftraege.KAROSSERIE)' in r


# ---------------------------------------------------------------- Reparaturwelle 5A (Review 26.09.2026 abends, Kosten/Planung/Besitz/Entfernung)
def _item_pos(lid, preis, pos, **kw):
    """Zeile MIT Positionsnummer (scrapesmith) fuer den Top-N-Nachweis."""
    return {**_item(lid, preis, **kw), "searchPosition": pos}


def test_39_top_n_nachweis_ueber_searchposition(welt, monkeypatch):
    """Nr. 1: 'sortiert' hiess nur monoton. Jetzt: searchPosition 1..n lueckenlos = bewiesen;
    Luecken = data_invalid; keine Positionsnummer (Ersatz-Scraper) = nur monoton -> Statistik ja,
    aber Kennzeichen am Job, Tagesaggregat und in der Segmentstatistik."""
    assert NORM.top_n_nachweis([]) == ("bewiesen", "")
    assert NORM.top_n_nachweis([_item_pos("a", 1, 1), _item_pos("b", 2, 2), _item_pos("c", 3, 3)]) == ("bewiesen", "")
    assert NORM.top_n_nachweis([_item_pos("c", 3, 3), _item_pos("a", 1, 1), _item_pos("b", 2, 2)])[0] == "bewiesen", "Reihenfolge egal"
    assert NORM.top_n_nachweis([_item("a", 1), _item("b", 2)])[0] == "nur_monoton"
    st, grund = NORM.top_n_nachweis([_item_pos("a", 1, 1), _item_pos("c", 3, 3)])
    assert st == "ungueltig" and "Luecken" in grund and "2" in grund
    assert NORM.top_n_nachweis([_item_pos("a", 1, 1), _item("b", 2)])[0] == "ungueltig", "teilweise ohne Nummer"
    assert NORM.top_n_nachweis([_item_pos("a", 1, 4), _item_pos("b", 2, 5)])[0] == "nur_monoton", "beginnt nicht bei 1: nicht bewiesen, aber gueltig"
    # Luecke durch den Zeilenfilter (Unfallwagen auf Platz 2) ist KEINE Luecke: Pruefung vor dem Filter
    w, db = welt.w, welt.db
    _aufraeumen(welt)
    s = w.s
    seg = _segment(w)
    welt.run(db[K.MODELLE].insert_one(_modell(w)))
    welt.run(db[K.SEGMENTE].insert_one(dict(seg)))
    monkeypatch.setenv("MARKT_BUDGET_MONAT_USD", "100")
    monkeypatch.setenv("MARKT_APIFY_ACTOR_ERSATZ", "")
    monkeypatch.setattr(K, "monat", lambda zeit=None: f"test-{s}")
    welt.run(db[K.BUDGET].delete_many({"_id": f"test-{s}"}))
    antwort = {"items": [_item_pos(f"t{s}a", 9000, 1), {**_item_pos(f"t{s}u", 9100, 2), "hasDamage": True}, _item_pos(f"t{s}c", 9200, 3)]}

    async def _lauf(urls, max_items, zeitlimit_s=None, actor_name=None, max_items_per_query=None):
        return {**antwort, "usd": 0.02, "run_id": "r-pos", "status": "SUCCEEDED", "dauer_ms": 3, "actor": K.actor()}
    monkeypatch.setattr(APIFY, "lauf", _lauf)
    job = welt.run(JOBS.job_sofort(db, seg["id"]))
    welt.run(JOBS.verarbeiten_buendel(db, [_eigenen_beanspruchen(welt, job["id"])]))
    j = welt.run(db[K.JOBS].find_one({"id": job["id"]}, {"_id": 0}))
    assert j["status"] == "completed" and j["actual_rows"] == 2 and j["sortierung"] == "bewiesen" and j["top_n_bewiesen"] is True
    ts = welt.run(db[K.TAGESSTATS].find_one({"segment_id": seg["id"]}, {"_id": 0}))
    assert ts["top_n_bewiesen"] is True and ts["laeufe"][-1]["top_n_bewiesen"] is True
    # Luecke in den Rohzeilen (Platz 2 fehlt ganz) -> data_invalid, nichts gespeichert, Kosten gebucht
    antwort["items"] = [_item_pos(f"t{s}a", 9000, 1), _item_pos(f"t{s}c", 9200, 3)]
    job2 = welt.run(JOBS.job_sofort(db, seg["id"]))
    erg = welt.run(JOBS.verarbeiten_buendel(db, [_eigenen_beanspruchen(welt, job2["id"])]))
    assert erg["ergebnisse"][0]["status"] == "data_invalid" and "Luecken" in erg["ergebnisse"][0]["grund"]
    j2 = welt.run(db[K.JOBS].find_one({"id": job2["id"]}, {"_id": 0}))
    assert j2["status"] == "data_invalid" and j2["sortierung"] == "ungueltig" and j2["actual_cost"] == 0.02
    assert welt.run(db[K.SNAPSHOTS].count_documents({"segment_id": seg["id"]})) == 2, "nichts Neues gespeichert"
    # ohne Positionsnummer: nur monoton -> completed, aber Kennzeichen bis in die Segmentstatistik
    antwort["items"] = [_item(f"t{s}a", 9000), _item(f"t{s}c", 9200), _item(f"t{s}d", 9300)]
    job3 = welt.run(JOBS.job_sofort(db, seg["id"]))
    welt.run(JOBS.verarbeiten_buendel(db, [_eigenen_beanspruchen(welt, job3["id"])]))
    j3 = welt.run(db[K.JOBS].find_one({"id": job3["id"]}, {"_id": 0}))
    assert j3["status"] == "completed" and j3["sortierung"] == "nur_monoton" and j3["top_n_bewiesen"] is False
    ts = welt.run(db[K.TAGESSTATS].find_one({"segment_id": seg["id"]}, {"_id": 0}))
    assert ts["top_n_bewiesen"] is False and ts["sample_size"] == 3
    assert welt.run(db[K.SEGMENTSTATS].find_one({"_id": seg["id"]}, {"_id": 0}))["top_n_bewiesen"] is False
    welt.run(db.betriebsalarme.delete_many({"typ": "markt_sortierung_unsicher", "ref": seg["id"]}))
    welt.run(db[K.BUDGET].delete_many({"_id": f"test-{s}"}))
    _aufraeumen(welt)


def test_40_auftrag_geaendert_waehrend_des_laufs(welt, monkeypatch):
    """Nr. 9: der Job traegt definition_hash/version beim Claim; aendert sich der Auftrag
    materiell waehrend des Actor-Laufs, wird nichts gespeichert (cancelled 'Auftrag geaendert')."""
    A = _module("markt.auftraege")
    w, db = welt.w, welt.db
    _aufraeumen(welt)
    s = w.s
    e = {"make": "BMW", "model": "320", "variant": f"Test {s} hash", "fuel": "DIESEL", "gearbox": "AUTOMATIC_GEAR",
         "power_kw_min": 120, "power_kw_max": 145, "ez_years": [2019], "km_buckets": [{"min_km": 0, "max_km": 100000}],
         "rows": 10, "crawls_per_day": 1, "status": "active"}
    doc = welt.run(A.anlegen(db, e))
    mid = doc["id"]
    monkeypatch.setenv("MARKT_BUDGET_MONAT_USD", "10")
    monkeypatch.setenv("MARKT_APIFY_ACTOR_ERSATZ", "")
    monkeypatch.setattr(K, "monat", lambda zeit=None: f"test-{s}")
    welt.run(db[K.BUDGET].delete_many({"_id": f"test-{s}"}))
    try:
        seg_id = f"{mid}:2019:0-100000"
        job = welt.run(JOBS.job_sofort(db, seg_id))
        j = _eigenen_beanspruchen(welt, job["id"])

        async def _lauf(urls, max_items, zeitlimit_s=None, actor_name=None, max_items_per_query=None):
            # Betreiber aendert den Kraftstoff waehrend des Laufs -> Fassung 2 (nicht materiell: rows -> bleibt)
            await A.aendern(db, mid, {"rows": 12})
            await A.aendern(db, mid, {"fuel": "PETROL"})
            return {"items": [_item(f"t{s}p", 7190, ez="03/2019")], "usd": 0.02, "run_id": "r-h", "status": "SUCCEEDED", "dauer_ms": 3}
        monkeypatch.setattr(APIFY, "lauf", _lauf)
        erg = welt.run(JOBS.verarbeiten(db, j))
        assert erg["status"] == "cancelled" and "Auftrag geändert" in erg["grund"] and "v1" in erg["grund"] and "v2" in erg["grund"]
        d = welt.run(db[K.JOBS].find_one({"id": job["id"]}, {"_id": 0}))
        assert d["status"] == "cancelled" and d["auftrag_version"] == 1 and d["auftrag_hash"] == doc["definition_hash"] and d["actual_cost"] == 0.02
        assert welt.run(db[K.LISTINGS].count_documents({"listing_id": f"t{s}p"})) == 0
        assert welt.run(db[K.TAGESSTATS].count_documents({"segment_id": seg_id})) == 0
    finally:
        welt.run(db[K.BUDGET].delete_many({"_id": f"test-{s}"}))
        for coll in (K.SEGMENTE, K.JOBS):
            welt.run(db[coll].delete_many({"model_id": mid}))
        welt.run(db[K.MODELLE].delete_many({"id": mid}))
        _aufraeumen(welt)


def test_41_ersatz_kosten_zeilen_und_marktluecke(welt, monkeypatch):
    """Nr. 14: Kosten des leeren/abgebrochenen Primaerlaufs kommen zum Ersatzergebnis (usd, run_ids, laeufe);
    Nr. 15: Ersatz je URL mit der Zeilenzahl DIESES Segments; Nr. 16: legitim leeres Buendel loest keinen
    Ersatz aus — nur, wenn ein Segment in den letzten 7 Tagen Treffer hatte; Nr. 56: budget.runs = Actor-Starts."""
    w, db = welt.w, welt.db
    monkeypatch.setenv("MARKT_APIFY_ACTOR", "scrapesmith~mobile-de-scraper")
    monkeypatch.setenv("MARKT_APIFY_ACTOR_ERSATZ", "sourabhbgp~mobile-de-scraper")
    aufrufe = []

    async def _lauf(urls, max_items, zeitlimit_s=None, actor_name=None, max_items_per_query=None):
        actor_name = actor_name or K.actor()
        aufrufe.append((actor_name, list(urls), max_items))
        if actor_name.startswith("scrapesmith"):
            return {"items": [], "usd": 0.005, "run_id": "p1", "status": "SUCCEEDED", "dauer_ms": 1, "actor": actor_name, "laeufe": 1}
        return {"items": [_item("e" + urls[0][-1], 100)], "usd": 0.01, "run_id": "e" + urls[0][-1], "status": "SUCCEEDED", "dauer_ms": 1, "actor": actor_name, "laeufe": 1}
    monkeypatch.setattr(APIFY, "lauf", _lauf)
    r = welt.run(APIFY.lauf_mit_ersatz(["https://x/1", "https://x/2"], 52, max_items_per_query=26, rows_je_url=[13, 26]))
    assert r["actor"] == "sourabhbgp~mobile-de-scraper" and r["usd"] == round(0.005 + 0.02, 4) and r["laeufe"] == 3
    assert r["run_id"] == "p1,e1,e2" and r["primaer_usd"] == 0.005 and r["primaer_run_id"] == "p1"
    assert [a[2] for a in aufrufe[1:]] == [13, 26], "Ersatz je URL mit der Zeilenzahl des Segments"
    assert all(it["inputContext"] in ("https://x/1", "https://x/2") for it in r["items"])
    # Nr. 16: Aufrufer sagt 'Marktluecke moeglich' -> kein Ersatz, leeres Primaerergebnis bleibt
    aufrufe.clear()
    r2 = welt.run(APIFY.lauf_mit_ersatz(["https://x/1"], 13, ersatz_bei_leer=False))
    assert r2["items"] == [] and r2["actor"] == "scrapesmith~mobile-de-scraper" and len(aufrufe) == 1
    # Nr. 14 bei Fehler: ein abgebrochener Primaerlauf traegt seine Startkosten und Lauf-ID in den Fehler
    aufrufe.clear()

    async def _kaputt(urls, max_items, zeitlimit_s=None, actor_name=None, max_items_per_query=None):
        actor_name = actor_name or K.actor()
        aufrufe.append(actor_name)
        if actor_name.startswith("scrapesmith"):
            raise APIFY.ApifyFehler("zeit", "nicht fertig", usd=0.005, run_id="p-zeit")
        return {"items": [_item("z", 100)], "usd": 0.01, "run_id": "e-z", "status": "SUCCEEDED", "dauer_ms": 1, "actor": actor_name, "laeufe": 1}
    monkeypatch.setattr(APIFY, "lauf", _kaputt)
    r3 = welt.run(APIFY.lauf_mit_ersatz(["https://x/1"], 13))
    assert r3["usd"] == 0.015 and r3["run_id"] == "p-zeit,e-z" and r3["laeufe"] == 2 and "zeit" in r3["ersatz_grund"]

    # Nr. 68: Rate-Limit (429) -> KEIN Ersatz
    async def _limit(urls, max_items, zeitlimit_s=None, actor_name=None, max_items_per_query=None):
        aufrufe.append(actor_name or K.actor())
        raise APIFY.ApifyFehler("limit", "429")
    monkeypatch.setattr(APIFY, "lauf", _limit)
    aufrufe.clear()
    with pytest.raises(APIFY.ApifyFehler) as ex:
        welt.run(APIFY.lauf_mit_ersatz(["https://x/1"], 13))
    assert ex.value.art == "limit" and aufrufe == ["scrapesmith~mobile-de-scraper"]
    # Im Worker: frisches Segment ohne Treffer in 7 Tagen -> leerer Lauf = Marktluecke, kein Ersatz;
    # nach einem Tag mit Treffern -> Ersatz; budget.runs zaehlt beide Starts, rows die Rohzeilen
    _aufraeumen(welt)
    s = w.s
    seg = _segment(w)
    welt.run(db[K.MODELLE].insert_one(_modell(w)))
    welt.run(db[K.SEGMENTE].insert_one(dict(seg)))
    monkeypatch.setenv("MARKT_BUDGET_MONAT_USD", "100")
    monkeypatch.setattr(K, "monat", lambda zeit=None: f"test-{s}")
    welt.run(db[K.BUDGET].delete_many({"_id": f"test-{s}"}))
    aufrufe.clear()

    async def _lauf2(urls, max_items, zeitlimit_s=None, actor_name=None, max_items_per_query=None):
        actor_name = actor_name or K.actor()
        aufrufe.append(actor_name)
        if actor_name.startswith("scrapesmith"):
            return {"items": [], "usd": 0.005, "run_id": "p2", "status": "SUCCEEDED", "dauer_ms": 1, "actor": actor_name, "laeufe": 1}
        return {"items": [_item(f"t{s}e", 9000), _item(f"t{s}e", 9000)], "usd": 0.01, "run_id": "e2", "status": "SUCCEEDED", "dauer_ms": 1, "actor": actor_name, "laeufe": 1}
    monkeypatch.setattr(APIFY, "lauf", _lauf2)
    job = welt.run(JOBS.job_sofort(db, seg["id"]))
    welt.run(JOBS.verarbeiten_buendel(db, [_eigenen_beanspruchen(welt, job["id"])]))
    assert aufrufe == ["scrapesmith~mobile-de-scraper"], "Marktluecke: kein Ersatz"
    assert welt.run(db[K.JOBS].find_one({"id": job["id"]}, {"_id": 0}))["status"] == "completed"
    welt.run(db[K.TAGESSTATS].insert_one(_tagesstat(seg["id"], _tag(-3), 9000, [f"t{s}alt"])))
    aufrufe.clear()
    job2 = welt.run(JOBS.job_sofort(db, seg["id"]))
    welt.run(JOBS.verarbeiten_buendel(db, [_eigenen_beanspruchen(welt, job2["id"])]))
    assert aufrufe == ["scrapesmith~mobile-de-scraper", "sourabhbgp~mobile-de-scraper"], "Treffer vor 3 Tagen: Ersatz"
    j2 = welt.run(db[K.JOBS].find_one({"id": job2["id"]}, {"_id": 0}))
    assert j2["status"] == "completed" and j2["actual_rows"] == 1 and j2["rohe_rows"] == 2 and j2["actual_cost"] == 0.015
    assert j2["run_id"] == "p2,e2" and j2["sortierung"] == "nur_monoton"
    b = welt.run(BUD.dokument(db, f"test-{s}"))
    assert b["runs"] == 3 and b["rows"] == 2 and round(b["used_usd"], 4) == 0.02, "Starts 1 + 2, Rohzeilen 0 + 2"
    welt.run(db[K.BUDGET].delete_many({"_id": f"test-{s}"}))
    _aufraeumen(welt)


def test_42_puffer_und_kuerzen(welt, monkeypatch):
    """Nr. 17: rows + Puffer abrufen (+30 %, hoechstens +10), nach dem Filter auf rows kuerzen."""
    assert K.zeilen_mit_puffer(10) == 13 and K.zeilen_mit_puffer(20) == 26 and K.zeilen_mit_puffer(100) == 110 and K.zeilen_mit_puffer(1) == 2
    assert K.zeilen_mit_puffer(40) == 50 and K.zeilen_mit_puffer(33) == 43
    w, db = welt.w, welt.db
    _aufraeumen(welt)
    s = w.s
    seg = {**_segment(w), "max_items": 10}
    welt.run(db[K.MODELLE].insert_one(_modell(w)))
    welt.run(db[K.SEGMENTE].insert_one(dict(seg)))
    monkeypatch.setenv("MARKT_BUDGET_MONAT_USD", "100")
    monkeypatch.setenv("MARKT_APIFY_ACTOR_ERSATZ", "")
    monkeypatch.setattr(K, "monat", lambda zeit=None: f"test-{s}")
    welt.run(db[K.BUDGET].delete_many({"_id": f"test-{s}"}))
    gesehen = {}

    async def _lauf(urls, max_items, zeitlimit_s=None, actor_name=None, max_items_per_query=None):
        gesehen["max_items"] = max_items
        # 13 geliefert, davon 2 mit falscher EZ -> 11 passen -> auf 10 gekuerzt
        items = [_item_pos(f"t{s}{i:02d}", 9000 + i * 10, i + 1, ez=("03/2017" if i in (1, 5) else "03/2020")) for i in range(13)]
        return {"items": items, "usd": 0.02, "run_id": "r-puffer", "status": "SUCCEEDED", "dauer_ms": 3, "actor": K.actor()}
    monkeypatch.setattr(APIFY, "lauf", _lauf)
    job = welt.run(JOBS.job_sofort(db, seg["id"]))
    assert job["max_items"] == 10 and job["abruf_rows"] == 13
    welt.run(JOBS.verarbeiten_buendel(db, [_eigenen_beanspruchen(welt, job["id"])]))
    assert gesehen["max_items"] == 13
    j = welt.run(db[K.JOBS].find_one({"id": job["id"]}, {"_id": 0}))
    assert j["status"] == "completed" and j["actual_rows"] == 10 and j["gelieferte_rows"] == 13 and j["verworfen_filter"] == 2 and j["sortierung"] == "bewiesen"
    assert welt.run(db[K.TAGESSTATS].find_one({"segment_id": seg["id"]}, {"_id": 0}))["sample_size"] == 10
    welt.run(db[K.BUDGET].delete_many({"_id": f"test-{s}"}))
    _aufraeumen(welt)


def test_43_tagesplan_buendel_slots_kontingent_und_nachzuegler(welt, monkeypatch):
    """Nr. 21/22: 25 Segmente, Buendel 10 -> 3 Slots (gleiche scheduled_at je Slot) -> 3 Actor-Laeufe;
    Nr. 24/32: zweiter Aufruf plant nichts doppelt, sofort=True nur das Tageskontingent (Rest wartet,
    Hinweis); Nr. 25: k Abrufe bleiben vor 23:30; Nr. 62: Segmente per Cursor ohne Obergrenze."""
    w, db = welt.w, welt.db
    _aufraeumen(welt)
    s = w.s
    welt.run(db[K.MODELLE].insert_one(_modell(w)))
    segs = []
    for i in range(25):
        seg = {**_segment(w), "id": f"test-320d-{s}:2019-2021:{i * 1000}-{i * 1000 + 999}", "min_km": i * 1000, "max_km": i * 1000 + 999, "max_items": 20}
        welt.run(db[K.SEGMENTE].insert_one(dict(seg)))
        segs.append(seg)
    ids = [x["id"] for x in segs]
    monkeypatch.setenv("MARKT_BUDGET_MONAT_USD", "1000")
    monkeypatch.setenv("MARKT_BUENDEL_GROESSE", "10")
    monkeypatch.setenv("MARKT_CRAWL_INTERVALL_TAGE", "1")
    monkeypatch.setenv("MARKT_APIFY_ACTOR_ERSATZ", "")
    monkeypatch.setattr(K, "monat", lambda zeit=None: f"test-{s}")
    welt.run(db[K.BUDGET].delete_many({"_id": f"test-{s}"}))
    tag = f"2097-03-{int(s[:1], 16) % 28 + 1:02d}"
    p1 = welt.run(JOBS.tagesplan(db, tag))
    assert p1["neu"] >= 25 and p1["slots"] >= 3
    jobs = welt.run(db[K.JOBS].find({"segment_id": {"$in": ids}, "tag": tag}, {"_id": 0}).to_list(100))
    assert len(jobs) == 25
    zeiten = sorted({j["scheduled_at"] for j in jobs})
    assert len(zeiten) == 3, "3 Slots mit derselben scheduled_at je Slot"
    assert sorted(sum(1 for j in jobs if j["scheduled_at"] == z) for z in zeiten) == [5, 10, 10]
    assert all(K.fenster_start(tag) <= datetime.fromisoformat(z) < K.fenster_start(tag) + K.fenster_dauer() for z in zeiten)
    assert (welt.run(K.merker_lesen(db, K.TAGESPLAN_DOK))).get("tag") == tag, "Merker 'geplant fuer Tag'"
    p2 = welt.run(JOBS.tagesplan(db, tag))
    assert p2["neu"] == 0 and p2["schon_geplant"] >= 25, "idempotent"
    # Worker: alle 25 faellig -> genau 3 Actor-Laeufe (10 + 10 + 5), Nachzuegler-Wartezeit greift nicht
    welt.run(db[K.JOBS].update_many({"segment_id": {"$in": ids}, "tag": tag}, {"$set": {"scheduled_at": "2000-01-01T00:00:00+00:00"}}))
    aufrufe = []

    async def _lauf(urls, max_items, zeitlimit_s=None, actor_name=None, max_items_per_query=None):
        aufrufe.append(len(urls))
        return {"items": [], "usd": 0.005, "run_id": f"r-{len(aufrufe)}", "status": "SUCCEEDED", "dauer_ms": 1, "actor": K.actor()}
    monkeypatch.setattr(APIFY, "lauf", _lauf)
    monkeypatch.setenv("MARKT_JOBS_PARALLEL", "1")
    erg = welt.run(JOBS.einmal(db, nachzuegler_s=0))
    assert erg["erledigt"] >= 25 and sorted(a for a in aufrufe if a in (5, 10)) == [5, 10, 10] and erg["buendel"] >= 3
    # Nr. 22: ein Nachzuegler, der in 2 s faellig wird, kommt noch ins Buendel (ein Lauf statt zwei)
    aufrufe.clear()
    welt.run(JOBS.job_sofort(db, ids[0]))
    j2 = welt.run(JOBS.job_sofort(db, ids[1]))
    welt.run(db[K.JOBS].update_one({"id": j2["id"]}, {"$set": {"scheduled_at": (K.jetzt() + timedelta(seconds=2)).isoformat()}}))
    erg2 = welt.run(JOBS.einmal(db, nachzuegler_s=10))
    assert aufrufe == [2] and erg2["erledigt"] >= 2, "gewartet, ein Buendel"
    # Nr. 32: sofort=True plant hoechstens das Tageskontingent — Rest wartet, mit Hinweis
    welt.run(db[K.SEGMENTE].update_many({"id": {"$in": ids}}, {"$unset": {"last_planned_tag": ""}}))
    alt_intervall = JOBS.intervall

    async def _klein(db_):
        t = await alt_intervall(db_)
        return {**t, "segmente_je_tag": 7, "intervall_tage": 4}
    monkeypatch.setattr(JOBS, "intervall", _klein)
    tag2 = f"2097-04-{int(s[1:2], 16) % 28 + 1:02d}"
    p3 = welt.run(JOBS.tagesplan(db, tag2, sofort=True))
    assert p3["segmente"] == 7 and p3["wartend"] >= 18 and "warten" in p3["hinweis"]
    assert welt.run(db[K.JOBS].count_documents({"segment_id": {"$in": ids}, "tag": tag2})) == 7
    p4 = welt.run(JOBS.tagesplan(db, tag2, sofort=True))
    assert p4["segmente"] == 0 and p4["neu"] == 0, "Kontingent ausgeschoepft — nicht das doppelte"
    monkeypatch.setattr(JOBS, "intervall", alt_intervall)
    # Nr. 25: 4 Abrufe je Tag ab 20:00 Uhr -> alle vor 23:30 (Abstand = Restfenster / 4)
    seg4 = {**_segment(w), "id": f"test-320d-{s}:2019-2021:900000-900999", "min_km": 900000, "max_km": 900999, "crawls_per_day": 4}
    welt.run(db[K.SEGMENTE].insert_one(dict(seg4)))
    abends = K.tag_ende("2097-05-01") - timedelta(hours=3, minutes=30)      # 20:00 deutscher Zeit
    monkeypatch.setattr(K, "jetzt", lambda: abends)
    welt.run(JOBS.tagesplan(db, "2097-05-01", sofort=True))
    vier = welt.run(db[K.JOBS].find({"segment_id": seg4["id"], "tag": {"$regex": "^2097-05-01"}}, {"_id": 0, "scheduled_at": 1}).sort("scheduled_at", 1).to_list(10))
    assert len(vier) == 4 and all(datetime.fromisoformat(v["scheduled_at"]) < K.tag_ende("2097-05-01") for v in vier)
    assert abs((datetime.fromisoformat(vier[1]["scheduled_at"]) - datetime.fromisoformat(vier[0]["scheduled_at"])).total_seconds() - 3.5 * 3600 / 4) < 60
    # Nr. 62: der Planer laedt Segmente per Cursor, ohne to_list-Obergrenze
    q = inspect.getsource(JOBS.tagesplan)
    assert "async for s in db[SEGMENTE].find" in q and "to_list(20000)" not in q
    welt.run(db.betriebsalarme.delete_many({"typ": "markt_lauf_leer", "ref": {"$regex": "^r-"}}))
    welt.run(db[K.BUDGET].delete_many({"_id": f"test-{s}"}))
    welt.run(db[K.KONFIG].delete_many({"_id": K.TAGESPLAN_DOK, "tag": {"$regex": "^2097"}}))
    _aufraeumen(welt)


def test_44_crawler_schalter_storniert_und_alte_jobs(welt, monkeypatch):
    """Nr. 23: Crawler aus storniert wartende Jobs; an reiht die heute stornierten wieder ein und plant
    frisch; Jobs vergangener Tage werden im Worker vor dem Claim storniert (nie nachtraeglich).
    Nr. 33: die Worker-Schleife bricht bei ausgeschaltetem Crawler vor dem naechsten Buendel ab;
    die Admin-Route verarbeitet hoechstens ein Buendel je Aufruf."""
    w, db = welt.w, welt.db
    _aufraeumen(welt)
    s = w.s
    seg = _segment(w)
    welt.run(db[K.MODELLE].insert_one(_modell(w)))
    welt.run(db[K.SEGMENTE].insert_one(dict(seg)))
    welt.run(db[K.KONFIG].delete_many({"_id": K.SCHALTER_DOK}))
    geplant = []

    async def _plan(db_, tag=None, *, sofort=False):
        geplant.append((tag, sofort))
        return {"segmente": 0, "neu": 0, "tag": tag, "status": "ok"}
    monkeypatch.setattr(JOBS, "tagesplan", _plan)
    try:
        job = welt.run(JOBS.job_sofort(db, seg["id"]))
        aus = welt.run(JOBS.crawler_schalten(db, False, wer="test"))
        assert aus["aktiv"] is False and aus["storniert"] >= 1
        d = welt.run(db[K.JOBS].find_one({"id": job["id"]}, {"_id": 0}))
        assert d["status"] == "cancelled" and d["error"] == JOBS.STORNO_AUS and d["finished_at"]
        an = welt.run(JOBS.crawler_schalten(db, True, wer="test"))
        assert an["aktiv"] is True and an["wieder_eingereiht"] >= 1 and geplant == [(K.heute_tag(), True)]
        d = welt.run(db[K.JOBS].find_one({"id": job["id"]}, {"_id": 0}))
        assert d["status"] == "queued" and d["error"] is None and d["scheduled_at"] >= job["scheduled_at"]
        # alter Job (gestern) wird vor dem Claim storniert, nie verarbeitet
        alt = JOBS._job_doc(seg, "2000-01-01", "2000-01-01T00:00:00+00:00", "daily")
        welt.run(db[K.JOBS].insert_one(dict(alt)))
        aufrufe = []

        async def _lauf(urls, max_items, zeitlimit_s=None, actor_name=None, max_items_per_query=None):
            aufrufe.append(urls)
            return {"items": [], "usd": 0.005, "run_id": "r-s", "status": "SUCCEEDED", "dauer_ms": 1, "actor": K.actor()}
        monkeypatch.setattr(APIFY, "lauf", _lauf)
        monkeypatch.setenv("MARKT_APIFY_ACTOR_ERSATZ", "")
        monkeypatch.setenv("MARKT_BUDGET_MONAT_USD", "10")
        monkeypatch.setattr(K, "monat", lambda zeit=None: f"test-{s}")
        # Nr. 33: Schalter aus + schalter_pruefen (Worker-Schleife) -> nichts verarbeitet
        welt.run(K.crawler_schalten(db, False))
        erg = welt.run(JOBS.einmal(db, schalter_pruefen=True, nachzuegler_s=0))
        assert erg["erledigt"] == 0 and erg["abgebrochen"] == "crawler_aus"
        assert welt.run(db[K.JOBS].find_one({"id": alt["id"]}, {"_id": 0}))["status"] == "cancelled"
        assert welt.run(db[K.JOBS].find_one({"id": alt["id"]}, {"_id": 0}))["error"] == JOBS.STORNO_ALT
        assert aufrufe == []
        # Admin-Route: ein Buendel je Aufruf (auch bei Schalter aus), 'wartend' zaehlt den Rest
        welt.run(db[K.JOBS].update_one({"id": job["id"]}, {"$set": {"scheduled_at": "2000-01-01T00:00:00+00:00"}}))
        seg2 = {**_segment(w), "id": f"test-320d-{s}:2019-2021:1-2", "min_km": 1, "max_km": 2, "max_items": 7}
        welt.run(db[K.SEGMENTE].insert_one(dict(seg2)))
        welt.run(JOBS.job_sofort(db, seg2["id"]))
        erg = welt.run(JOBS.einmal(db, max_buendel=1, nachzuegler_s=0))
        assert erg["buendel"] == 1 and erg["erledigt"] == 1 and erg["wartend"] >= 1 and len(aufrufe) == 1
        r = (Path(__file__).resolve().parent.parent / "routes" / "markt_admin.py").read_text(encoding="utf-8")
        assert "jobs.einmal(db, max_buendel=1, nachzuegler_s=0)" in r and "jobs.crawler_schalten(db, body.aktiv" in r
    finally:
        welt.run(db[K.KONFIG].delete_many({"_id": K.SCHALTER_DOK}))
        welt.run(db.betriebsalarme.delete_many({"typ": "markt_lauf_leer", "ref": "r-s"}))
        welt.run(db[K.BUDGET].delete_many({"_id": f"test-{s}"}))
        _aufraeumen(welt)


def test_45_lease_verloren_vor_und_nach_dem_lauf(welt, monkeypatch):
    """Nr. 35: verlaengert der Heartbeat nicht alle Jobs, fliegen die verlorenen aus dem Plan (nicht
    gestartet); Nr. 36: nach dem Lauf, vor dem Speichern, wird der Besitz erneut geprueft — sonst
    nichts speichern (Kosten gebucht). Nr. 34: bis zu MARKT_JOBS_PARALLEL Buendel gleichzeitig."""
    w, db = welt.w, welt.db
    _aufraeumen(welt)
    s = w.s
    welt.run(db[K.MODELLE].insert_one(_modell(w)))
    seg_a = _segment(w)
    seg_b = {**_segment(w), "id": f"test-320d-{s}:2019-2021:1-2", "min_km": 1, "max_km": 2}
    welt.run(db[K.SEGMENTE].insert_many([dict(seg_a), dict(seg_b)]))
    monkeypatch.setenv("MARKT_BUDGET_MONAT_USD", "10")
    monkeypatch.setenv("MARKT_APIFY_ACTOR_ERSATZ", "")
    monkeypatch.setattr(K, "monat", lambda zeit=None: f"test-{s}")
    welt.run(db[K.BUDGET].delete_many({"_id": f"test-{s}"}))
    aufrufe = []

    async def _lauf(urls, max_items, zeitlimit_s=None, actor_name=None, max_items_per_query=None):
        aufrufe.append(list(urls))
        return {"items": [_item(f"t{s}a", 9000)], "usd": 0.01, "run_id": "r-l", "status": "SUCCEEDED", "dauer_ms": 1, "actor": K.actor()}
    monkeypatch.setattr(APIFY, "lauf", _lauf)
    ja, jb = welt.run(JOBS.job_sofort(db, seg_a["id"])), welt.run(JOBS.job_sofort(db, seg_b["id"]))
    a, b = _eigenen_beanspruchen(welt, ja["id"]), _eigenen_beanspruchen(welt, jb["id"])
    welt.run(db[K.JOBS].update_one({"id": jb["id"]}, {"$set": {"worker": "markt-anderer"}}))     # b gehoert jetzt jemand anderem
    erg = welt.run(JOBS.verarbeiten_buendel(db, [a, b]))
    assert erg["status"] == "ok" and erg["jobs"] == 1 and len(aufrufe) == 1 and len(aufrufe[0]) == 1, "nur a gestartet"
    assert welt.run(db[K.JOBS].find_one({"id": jb["id"]}, {"_id": 0}))["status"] == "running", "b unangetastet"
    assert welt.run(db[K.JOBS].find_one({"id": ja["id"]}, {"_id": 0}))["status"] == "completed"
    # Nr. 36: waehrend des Laufs uebernimmt ein anderer Worker -> nichts gespeichert, Kosten gebucht
    _aufraeumen(welt)
    welt.run(db[K.MODELLE].insert_one(_modell(w)))
    welt.run(db[K.SEGMENTE].insert_one(dict(seg_a)))
    jc = welt.run(JOBS.job_sofort(db, seg_a["id"]))
    c = _eigenen_beanspruchen(welt, jc["id"])

    async def _lauf2(urls, max_items, zeitlimit_s=None, actor_name=None, max_items_per_query=None):
        await db[K.JOBS].update_one({"id": jc["id"]}, {"$set": {"lease_until": "2000-01-01T00:00:00+00:00"}})   # Lease abgelaufen
        return {"items": [_item(f"t{s}c", 9000)], "usd": 0.01, "run_id": "r-v", "status": "SUCCEEDED", "dauer_ms": 1, "actor": K.actor()}
    monkeypatch.setattr(APIFY, "lauf", _lauf2)
    erg = welt.run(JOBS.verarbeiten(db, c))
    assert erg["status"] == "verloren"
    assert welt.run(db[K.LISTINGS].count_documents({"listing_id": f"t{s}c"})) == 0 and welt.run(db[K.TAGESSTATS].count_documents({"segment_id": seg_a["id"]})) == 0
    bud = welt.run(BUD.dokument(db, f"test-{s}"))     # _aufraeumen hat das Budget-Dokument geleert: nur dieser Lauf
    assert round(bud["used_usd"], 4) == 0.01 and round(bud["reserved_usd"], 6) == 0, "Lauf bezahlt, Reservierung geloest"
    q = inspect.getsource(JOBS._buendel_lauf)
    assert q.index("_noch_meiner(db, p") < q.index("_noch_gewollt(db, p") < q.index("speicher.verarbeiten(")
    # Nr. 34: zwei Buendel (verschiedene Zeilenzahl) laufen in EINEM Takt gleichzeitig
    welt.run(db[K.JOBS].update_one({"id": jc["id"]}, {"$set": {"status": "cancelled"}}))     # sonst holt stale_zurueck ihn wieder
    monkeypatch.setenv("MARKT_JOBS_PARALLEL", "2")
    seg_c = {**_segment(w), "id": f"test-320d-{s}:2019-2021:3-4", "min_km": 3, "max_km": 4, "max_items": 5}
    welt.run(db[K.SEGMENTE].insert_one(dict(seg_c)))
    welt.run(JOBS.job_sofort(db, seg_a["id"]))
    welt.run(JOBS.job_sofort(db, seg_c["id"]))
    aufrufe.clear()
    gleichzeitig = {"n": 0, "max": 0}

    async def _lauf3(urls, max_items, zeitlimit_s=None, actor_name=None, max_items_per_query=None):
        gleichzeitig["n"] += 1
        gleichzeitig["max"] = max(gleichzeitig["max"], gleichzeitig["n"])
        await asyncio.sleep(0.05)
        gleichzeitig["n"] -= 1
        aufrufe.append(max_items)
        return {"items": [], "usd": 0.005, "run_id": f"r-p{max_items}", "status": "SUCCEEDED", "dauer_ms": 1, "actor": K.actor()}
    monkeypatch.setattr(APIFY, "lauf", _lauf3)
    erg = welt.run(JOBS.einmal(db, nachzuegler_s=0))
    assert erg["buendel"] == 2 and sorted(aufrufe) == [7, 26] and gleichzeitig["max"] == 2, "parallel"
    assert "asyncio.gather" in inspect.getsource(JOBS.einmal)
    welt.run(db.betriebsalarme.delete_many({"typ": "markt_lauf_leer", "ref": {"$regex": "^r-"}}))
    welt.run(db[K.BUDGET].delete_many({"_id": f"test-{s}"}))
    _aufraeumen(welt)


def test_46_entfernung_hintergrund_historie_zweite_quelle_rueckstand(welt, monkeypatch):
    """Nr. 37: eigene Hintergrundaufgabe, Detail-Zeitlimit 45 s; Nr. 40: Preisaenderung mit Historie;
    Nr. 41: zweite Pruefung ueber den Ersatz-Scraper (sonst Kennzeichen unbestaetigt_einzelquelle);
    Nr. 42: 5-Minuten-Sperre + Merker 'erledigt fuer Tag'; Nr. 39: Rueckstand im Monitoring."""
    w, db = welt.w, welt.db
    _aufraeumen(welt)
    s = w.s
    alt = (K.jetzt() - timedelta(days=5)).isoformat()
    welt.run(db[K.LISTINGS].insert_many([
        {"source": "mobile", "listing_id": f"t{s}x", "url": "https://suchen.mobile.de/fahrzeuge/details.html?id=1", "active_state": "not_seen_in_sample",
         "not_seen_since": alt, "current_price": 200.0, "first_price": 200.0, "price_history": [{"at": alt, "price": 200.0}], "price_changes": 0, "price_reductions": 0},
        {"source": "mobile", "listing_id": f"t{s}y", "url": "https://suchen.mobile.de/fahrzeuge/details.html?id=2", "active_state": "not_seen_in_sample",
         "not_seen_since": alt, "current_price": 100.0}]))
    monkeypatch.setenv("MARKT_BUDGET_MONAT_USD", "10")
    monkeypatch.setenv("MARKT_APIFY_ACTOR", "scrapesmith~mobile-de-scraper")
    monkeypatch.setenv("MARKT_APIFY_ACTOR_ERSATZ", "sourabhbgp~mobile-de-scraper")
    monkeypatch.setattr(K, "monat", lambda zeit=None: f"test-{s}")
    welt.run(db[K.BUDGET].delete_many({"_id": f"test-{s}"}))
    assert welt.run(ENT.rueckstand(db)) >= 2
    aufrufe = []
    antworten = {"1": [_item(f"t{s}x", 180)], "2": []}

    async def _lauf(urls, max_items, zeitlimit_s=None, actor_name=None, max_items_per_query=None):
        aufrufe.append((actor_name or K.actor(), zeitlimit_s))
        return {"items": antworten[urls[0].split("id=")[-1]], "usd": 0.007, "run_id": "r", "status": "SUCCEEDED", "dauer_ms": 5}
    monkeypatch.setattr(APIFY, "lauf", _lauf)
    lx = {"source": "mobile", "listing_id": f"t{s}x", "url": "https://suchen.mobile.de/fahrzeuge/details.html?id=1"}
    ly = {"source": "mobile", "listing_id": f"t{s}y", "url": "https://suchen.mobile.de/fahrzeuge/details.html?id=2"}
    # Nr. 40: Preis 200 -> 180 mit Historie und Zaehlern
    assert welt.run(ENT.pruefen(db, lx)) == "not_seen_in_sample"
    x = welt.run(db[K.LISTINGS].find_one({"listing_id": f"t{s}x"}, {"_id": 0}))
    assert x["current_price"] == 180 and [p["price"] for p in x["price_history"]] == [200, 180] and x["price_changes"] == 1 and x["price_reductions"] == 1
    assert aufrufe[-1] == ("scrapesmith~mobile-de-scraper", ENT.DETAIL_ZEITLIMIT_S) and ENT.DETAIL_ZEITLIMIT_S == 45
    # Nr. 41: erste leere Pruefung Standard, zweite ueber den Ersatz -> confirmed_removed ohne Einzelquellen-Kennzeichen
    assert welt.run(ENT.pruefen(db, ly)) == "verification_pending"
    assert welt.run(ENT.pruefen(db, ly)) == "confirmed_removed"
    assert [a[0] for a in aufrufe[-2:]] == ["scrapesmith~mobile-de-scraper", "sourabhbgp~mobile-de-scraper"]
    y = welt.run(db[K.LISTINGS].find_one({"listing_id": f"t{s}y"}, {"_id": 0}))
    assert y["active_state"] == "confirmed_removed" and y["verified_actor"] == "sourabhbgp~mobile-de-scraper" and "unbestaetigt_einzelquelle" not in y
    # ohne Ersatz-Scraper: wie bisher, aber gekennzeichnet
    monkeypatch.setenv("MARKT_APIFY_ACTOR_ERSATZ", "")
    welt.run(db[K.LISTINGS].update_one({"listing_id": f"t{s}y"}, {"$set": {"active_state": "verification_pending", "leer_zaehler": 1}, "$unset": {"confirmed_removed_at": "", "verified_actor": ""}}))
    assert welt.run(ENT.pruefen(db, ly)) == "confirmed_removed"
    assert welt.run(db[K.LISTINGS].find_one({"listing_id": f"t{s}y"}, {"_id": 0}))["unbestaetigt_einzelquelle"] is True
    # Nr. 42: taeglich setzt den Merker, der zweite Aufruf am selben Tag tut nichts; Sperre nur 5 Minuten
    # (Kandidaten leer gestellt — die Demo-Listings der Dev-DB sollen nicht angefasst werden)
    welt.run(db[K.KONFIG].delete_many({"_id": K.ENTFERNUNG_DOK}))
    welt.run(db.job_locks.delete_many({"name": {"$regex": "^markt-entfernung-"}}))
    monkeypatch.setenv("MARKT_ENTFERNUNG_PRUEFEN", "true")
    monkeypatch.setenv("MARKT_ENTFERNUNG_MAX_JE_TAG", "50")

    async def _keine(db_, limit):
        return []
    monkeypatch.setattr(ENT, "kandidaten", _keine)
    try:
        e1 = welt.run(ENT.taeglich(db))
        assert e1["geprueft"] >= 0 and (welt.run(K.merker_lesen(db, K.ENTFERNUNG_DOK))).get("tag") == K.heute_tag()
        e2 = welt.run(ENT.taeglich(db))
        assert e2 == {"geprueft": 0, "erledigt": True}
        assert ENT.SPERRE_S == 300 and "ttl_seconds=SPERRE_S" in inspect.getsource(ENT.taeglich) and "20 * 3600" not in inspect.getsource(ENT)
    finally:
        welt.run(db[K.KONFIG].delete_many({"_id": K.ENTFERNUNG_DOK}))
        welt.run(db.job_locks.delete_many({"name": {"$regex": "^markt-entfernung-"}}))
    # Nr. 37: Hintergrundaufgabe je Takt, hoechstens eine gleichzeitig; Nr. 38: Kosten in der Taktung
    q = inspect.getsource(JOBS.worker_forever)
    assert "_entfernung_starten(db)" in q and "await entfernung.taeglich" not in q
    assert "create_task" in inspect.getsource(JOBS._entfernung_starten)
    monkeypatch.setenv("MARKT_ENTFERNUNG_MAX_JE_TAG", "50")
    monkeypatch.setenv("MARKT_ENTFERNUNG_PRUEFEN", "true")
    monkeypatch.delenv("MARKT_ROW_USD", raising=False)
    monkeypatch.delenv("MARKT_START_USD", raising=False)
    assert K.entfernung_kosten_je_tag_usd() == round(50 * (0.005 + 0.0007), 4)
    monkeypatch.setenv("MARKT_ENTFERNUNG_PRUEFEN", "false")
    assert K.entfernung_kosten_je_tag_usd() == 0.0
    # Nr. 39: Monitoring zeigt den Rueckstand, Alarm ab 500
    mon = welt.run(ABF.monitoring(db))
    assert "rueckstand_entfernung" in mon and mon["rueckstand_entfernung"] >= 0

    async def _viel(db_):
        return 750
    monkeypatch.setattr(ENT, "rueckstand", _viel)
    mon = welt.run(ABF.monitoring(db))
    assert any(a["typ"] == "entfernung_rueckstand" and "750" in a["text"] for a in mon["alarme"])
    welt.run(db[K.BUDGET].delete_many({"_id": f"test-{s}"}))
    _aufraeumen(welt)


class _Antwort:
    def __init__(self, status, daten=None, text=""):
        self.status_code = status
        self._daten = daten
        self.text = text

    def json(self):
        return self._daten


class _FakeClient:
    """httpx.AsyncClient-Ersatz fuer apify.lauf: Start ok, Polling laut Drehbuch."""
    drehbuch: list = []
    posts: list = []

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, **k):
        _FakeClient.posts.append(url)
        if url.endswith("/abort"):
            return _Antwort(200, {})
        return _Antwort(201, {"data": {"id": "run-1", "defaultDatasetId": "ds-1", "status": "RUNNING"}})

    async def get(self, url, **k):
        if "/actor-runs/" in url:
            schritt = _FakeClient.drehbuch.pop(0)
            if isinstance(schritt, Exception):
                raise schritt
            return schritt
        return _Antwort(200, [{"id": "a", "priceGross": 100}])


def test_47_apify_poll_fehler_backoff_und_abbruch(monkeypatch, welt):
    """Nr. 67/68: voruebergehende Poll-Fehler (5xx/Netz) werden bis zu 3x wiederholt; danach wird
    der Primaerlauf per API abgebrochen (abort VOR einem Ersatz) und der Fehler traegt run_id +
    Startkosten; 429 beim Polling -> Fehlerart 'limit' (kein Ersatz); ein Poll-Aussetzer allein
    scheitert nicht."""
    import httpx as _httpx
    monkeypatch.setenv("APIFY_TOKEN", "test-token")
    monkeypatch.setenv("MARKT_APIFY_ACTOR", "scrapesmith~mobile-de-scraper")
    monkeypatch.delenv("MARKT_START_USD", raising=False)
    monkeypatch.setattr(APIFY, "httpx", type("H", (), {"AsyncClient": _FakeClient, "Timeout": lambda *a, **k: None,
                                                        "HTTPError": _httpx.HTTPError}))

    async def _kein_schlaf(*a, **k):
        return None
    import types as _types
    monkeypatch.setattr(APIFY, "asyncio", _types.SimpleNamespace(sleep=_kein_schlaf))     # nur im Modul, nicht global
    # ein Aussetzer, dann fertig -> Erfolg
    _FakeClient.drehbuch = [_Antwort(500, None, "kaputt"), _Antwort(200, {"data": {"status": "SUCCEEDED", "defaultDatasetId": "ds-1", "usageTotalUsd": 0.006}})]
    _FakeClient.posts = []
    r = welt.run(APIFY.lauf(["https://x"], 5))
    assert r["run_id"] == "run-1" and len(r["items"]) == 1 and not any(p.endswith("/abort") for p in _FakeClient.posts)
    # dreimal gescheitert -> abort, Fehlerart 'poll', Kosten = Start, run_id gesetzt
    _FakeClient.drehbuch = [_Antwort(502, None, "bad gateway"), _httpx.ConnectError("weg"), _Antwort(503, None, "down")]
    _FakeClient.posts = []
    with pytest.raises(APIFY.ApifyFehler) as ex:
        welt.run(APIFY.lauf(["https://x"], 5))
    assert ex.value.art == "poll" and ex.value.run_id == "run-1" and ex.value.usd == 0.005
    assert _FakeClient.posts[-1].endswith("/actor-runs/run-1/abort"), "Primaerlauf abgebrochen"
    # 429 beim Polling -> 'limit' (lauf_mit_ersatz startet dann KEINEN Ersatz)
    _FakeClient.drehbuch = [_Antwort(429, None, "rate"), _Antwort(429, None, "rate"), _Antwort(429, None, "rate")]
    _FakeClient.posts = []
    with pytest.raises(APIFY.ApifyFehler) as ex:
        welt.run(APIFY.lauf_mit_ersatz(["https://x"], 5))
    assert ex.value.art == "limit" and _FakeClient.posts[-1].endswith("/abort") and "limit" in APIFY.OHNE_ERSATZ
    assert APIFY.POLL_VERSUCHE == 3


def test_48_markt_indizes_einzeln_und_worker_sperre(welt, monkeypatch):
    """Nr. 26: markt_indizes legt jeden Index einzeln an (eigener Fehlerfang); fehlen kritische
    Unique-Indizes, steht das im Merker und der Worker crawlt nicht (Alarm markt_indizes_fehlen);
    Nr. 27: Multikey-Index (segment_ids, active_state, last_seen_tag)."""
    IDX = _module("indizes")
    db = welt.db
    erg = welt.run(IDX.markt_indizes(db))
    assert erg["ok"] is True and erg["kritisch"] == []
    info = welt.run(db.market_listings.index_information())
    assert info["markt_listing_segmente_zustand"]["key"] == [("segment_ids", 1), ("active_state", 1), ("last_seen_tag", 1)]
    assert welt.run(db.market_crawl_jobs.index_information())["markt_job_buendel"]["key"] == [("status", 1), ("max_items", 1), ("scheduled_at", 1)]
    assert welt.run(K.indizes_fehlen(db)) == []
    q = inspect.getsource(IDX.markt_indizes)
    assert q.count("try:") >= 3 and "unique_anlegen" in q and "MARKT_UNIQUE_KRITISCH" in q
    assert "market_crawl_jobs.markt_job_je_tag" in IDX.MARKT_UNIQUE_KRITISCH and "market_segment_daily_stats.markt_tagesstat" in IDX.MARKT_UNIQUE_KRITISCH
    # ein kritischer Unique-Index scheitert -> Merker, Worker gesperrt, Monitoring-Alarm; danach wieder frei
    alt = IDX.unique_anlegen

    async def _kaputt(coll, schluessel, *, name=None, weich=False, **opt):
        if name == "markt_job_id":
            return False
        return await alt(coll, schluessel, name=name, weich=weich, **opt)
    monkeypatch.setattr(IDX, "unique_anlegen", _kaputt)
    try:
        erg = welt.run(IDX.markt_indizes(db))
        assert erg["ok"] is False and erg["kritisch"] == ["market_crawl_jobs.markt_job_id"]
        assert welt.run(K.indizes_fehlen(db)) == ["market_crawl_jobs.markt_job_id"]
        e = welt.run(JOBS.einmal(db, nachzuegler_s=0))
        assert e["erledigt"] == 0 and e["gesperrt"] == "indizes_fehlen"
        assert welt.run(db.betriebsalarme.find_one({"typ": "markt_indizes_fehlen", "ref": "crawler", "offen": True}))
        assert any(a["typ"] == "indizes_fehlen" for a in welt.run(ABF.monitoring(db))["alarme"])
        assert welt.run(JOBS.uebersicht(db))["indizes_fehlen"] == ["market_crawl_jobs.markt_job_id"]
    finally:
        monkeypatch.setattr(IDX, "unique_anlegen", alt)
        erg = welt.run(IDX.markt_indizes(db))
        assert erg["kritisch"] == [] and welt.run(K.indizes_fehlen(db)) == []
        assert welt.run(db.betriebsalarme.find_one({"typ": "markt_indizes_fehlen", "ref": "crawler", "offen": True})) is None
        welt.run(db.betriebsalarme.delete_many({"typ": "markt_indizes_fehlen"}))
