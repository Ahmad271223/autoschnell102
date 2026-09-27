# -*- coding: utf-8 -*-
"""Master-Auftrag Ahmad 26.09.2026, Phase B (reduziert) + Phase C — Kostenfelder/Architektur,
Tagesbasis, Datenqualitaet / Markttiefe / Vollstaendigkeit.

Phase B (ALL_KM am 26.09. abends gestrichen): Kostenprognose in getrennten Feldern (crawl_cost_exact_km,
reporting_cost = 0 — Auswertungen kosten nie Scraper-Geld); Architekturtest: Auswertungsmodule (abfrage,
speicher) rufen nie apify.lauf / jobs.job_sofort / jobs.tagesplan / segmente.synchronisieren; kein ALL_KM-Rest.
Phase C: je Regel ein Test — GOOD+THIN bei 2 von 5 mit gueltigem Lauf; POOR bei > 50 % Fremdfahrzeuge;
sample_completeness UNKNOWN ohne verlaessliche Gesamtzahl; invalid_runs im Tagesdokument; aus der Stichprobe
gefallen / Preiserhoehungen / Top-3-Wechsel; Actor-Key-Protokoll ohne Werte; Migration 19 fuer Altdaten;
datenlage-Alias; Frische (48 h) zum Lesezeitpunkt. Testdaten nur mit Praefix test-/dbg-, Aufraeumen am Ende."""
import inspect
import re
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_befunde_runde17_termine import _module, welt  # noqa: E402,F401
from test_markt_20260926 import (_aufraeumen, _antwort, _eigenen_beanspruchen, _item, _item_pos, _modell,  # noqa: E402
                                 _segment, _tag, _vorbereiten)

K = _module("markt.konfig")
SP = _module("markt.speicher")
NORM = _module("markt.normalisieren")
JOBS = _module("markt.jobs")
APIFY = _module("markt.apify")
ABF = _module("markt.abfrage")
A = _module("markt.auftraege")
MIG = _module("migrationen")
BACKEND = Path(__file__).resolve().parent.parent
FRONTEND = BACKEND.parent / "frontend" / "src"


# ---------------------------------------------------------------- Phase B (reduziert)
def test_b01_kostenfelder_getrennt_reporting_null(monkeypatch):
    monkeypatch.setenv("MARKT_APIFY_ACTOR", "scrapesmith~mobile-de-scraper")
    monkeypatch.delenv("MARKT_ROW_USD", raising=False)
    monkeypatch.delenv("MARKT_START_USD", raising=False)
    m = {"ez_years": [2021, 2022, 2023, 2024], "km_buckets": [{"min_km": 20000 + i * 10, "max_km": 20005 + i * 10} for i in range(6)],
         "rows": 5, "crawls_per_day": 1}
    p = A.prognose_modell(m)
    assert p["segmente"] == 24, "4 EZ x 6 km — kein ALL_KM-Referenzsegment"
    assert p["crawl_cost_exact_km_usd"] == p["kosten_tag_usd"] and p["crawl_cost_exact_km_monat_usd"] == p["kosten_monat_usd"]
    assert p["reporting_cost_usd"] == 0.0 and p["reporting_cost_monat_usd"] == 0.0
    q = inspect.getsource(A.prognose)
    assert "reporting_cost_usd" in q and "crawl_cost_exact_km_usd" in q and "crawl_cost_entfernung_usd" in q
    q2 = inspect.getsource(JOBS.intervall)
    assert "reporting_cost_usd" in q2 and "crawl_cost_exact_km_usd" in q2


def test_b02_architektur_auswertung_loest_nie_crawls_aus():
    """Nur der Crawler (jobs/apify) darf externe Marktanfragen ausloesen. Auswertungen (abfrage, Tages-/
    Segmentstatistik) lesen nur — sie importieren/rufen nie apify.lauf, jobs.job_sofort, jobs.tagesplan,
    jobs.einmal, segmente.synchronisieren. Kein ALL_KM-Rest im Code (Ahmad 26.09.2026: gestrichen)."""
    verboten = ("apify.lauf", "lauf_mit_ersatz", "job_sofort", "tagesplan(", "verarbeiten_buendel", "jobs.einmal", "synchronisieren(",
                "httpx", "entfernung.taeglich", "entfernung.pruefen")
    abfrage_q = (BACKEND / "markt" / "abfrage.py").read_text(encoding="utf-8")
    for v in verboten:
        assert v not in abfrage_q, f"abfrage.py ruft {v}"
    assert "from markt import apify" not in abfrage_q and "import apify" not in abfrage_q
    speicher_q = (BACKEND / "markt" / "speicher.py").read_text(encoding="utf-8")
    for v in verboten:
        assert v not in speicher_q, f"speicher.py ruft {v}"
    assert "apify" not in speicher_q and "from markt import jobs" not in speicher_q
    for fn in (SP.segmentstatistik, SP.verarbeiten, SP.ungueltig_vermerken, ABF.modelle_uebersicht, ABF.modell_detail,
               ABF.segment_verlauf, ABF.karte, ABF.chancen, ABF.private_deals):
        q = inspect.getsource(fn)
        assert "apify" not in q and "job_sofort" not in q and "tagesplan" not in q and "synchronisieren" not in q, fn.__name__
    # Master-Auftrag Phase D/E (27.09.2026): Hot Deals, Berichte und der Auswertungs-Worker lesen nur gespeicherte
    # Tageswerte — kein Import und kein Aufruf von Crawl-Funktionen (apify, jobs, entfernung, segmente.synchronisieren, httpx)
    # Phase F/G: Segment-Health und Optimierung ebenso (Laufzeittest: test_markt_health_20260927.py::test_12)
    for datei in ("deals.py", "auswertung.py", "berichte.py", "health.py", "optimierung.py"):
        q = (BACKEND / "markt" / datei).read_text(encoding="utf-8")
        for v in verboten:
            assert v not in q, f"{datei} ruft {v}"
        for imp in ("apify", "from markt import jobs", "import jobs", "jobs.", "entfernung", "import segmente", "segmente import",
                    "httpx", "requests", "urllib"):
            assert imp not in q, f"{datei} importiert/nutzt {imp}"
    # Firmen-Lesewege (routes/markt.py) rufen keine Crawl-Funktionen
    r = (BACKEND / "routes" / "markt.py").read_text(encoding="utf-8")
    assert "apify" not in r and "job_sofort" not in r and "synchronisieren" not in r
    treffer = []
    for p in list((BACKEND / "markt").glob("*.py")) + [BACKEND / "routes" / "markt_admin.py", BACKEND / "routes" / "markt.py",
                                                       BACKEND / "indizes.py", BACKEND / "migrationen.py"]:
        if re.search(r"ALL_KM|segment_scope", p.read_text(encoding="utf-8")):
            treffer.append(p.name)
    for p in list((FRONTEND / "pages" / "admin_v2").glob("Markt*.jsx")) + [FRONTEND / "lib" / "markt.js"]:
        if re.search(r"ALL_KM|segment_scope", p.read_text(encoding="utf-8")):
            treffer.append(p.name)
    assert treffer == [], treffer


# ---------------------------------------------------------------- Phase C: Regeln (reine Rechnung)
def test_c01_regeln_datenqualitaet_tiefe_vollstaendigkeit_alias():
    b = SP.data_quality_bewerten
    assert b(gueltiger_lauf=False) == ("UNKNOWN", "kein_lauf")
    assert b(gueltiger_lauf=False, invalid_runs=1) == ("POOR", "ungueltig")
    assert b(gueltiger_lauf=True, top_n_bewiesen=True, geliefert=5) == ("GOOD", "")
    assert b(gueltiger_lauf=True, top_n_bewiesen=True, geliefert=0) == ("GOOD", ""), "leerer gueltiger Lauf ist GOOD (Tiefe EMPTY)"
    assert b(gueltiger_lauf=True, top_n_bewiesen=False, geliefert=5) == ("MEDIUM", "nur_monoton")
    assert b(gueltiger_lauf=True, geliefert=5, verworfen_fremd=1) == ("MEDIUM", "fremdfahrzeuge")
    assert b(gueltiger_lauf=True, geliefert=5, verworfen_fremd=3) == ("POOR", "fremdfahrzeuge")
    assert b(gueltiger_lauf=True, geliefert=4, verworfen_fremd=2) == ("MEDIUM", "fremdfahrzeuge"), "genau 50 % ist nicht > 50 %"
    assert b(gueltiger_lauf=True, geliefert=5, parser_fehler=1) == ("MEDIUM", "parser")
    assert b(gueltiger_lauf=True, geliefert=5, parser_fehler=4) == ("POOR", "parser")
    jetzt = K.jetzt()
    assert b(gueltiger_lauf=True, geliefert=5, lauf_at=(jetzt - timedelta(hours=47)).isoformat(), jetzt=jetzt) == ("GOOD", "")
    assert b(gueltiger_lauf=True, geliefert=5, lauf_at=(jetzt - timedelta(hours=49)).isoformat(), jetzt=jetzt) == ("POOR", "stale")
    d = SP.market_depth_bewerten
    assert d(gueltiger_lauf=False, sample_size=0, rows=5) == "UNKNOWN" and d(gueltiger_lauf=True, sample_size=0, rows=5) == "EMPTY"
    assert d(gueltiger_lauf=True, sample_size=5, rows=5) == "FULL" and d(gueltiger_lauf=True, sample_size=6, rows=5) == "FULL"
    assert d(gueltiger_lauf=True, sample_size=3, rows=5) == "NORMAL" and d(gueltiger_lauf=True, sample_size=2, rows=5) == "THIN"
    v = SP.sample_completeness_bewerten
    assert v(gueltiger_lauf=False, sample_size=0, rows=5) == "UNKNOWN"
    assert v(gueltiger_lauf=True, sample_size=5, rows=5) == "COMPLETE"
    assert v(gueltiger_lauf=True, sample_size=2, rows=5) == "UNKNOWN", "ohne verlaessliche Gesamtzahl nie raten"
    assert v(gueltiger_lauf=True, sample_size=2, rows=5, markt_gesamt=50) == "INCOMPLETE"
    assert v(gueltiger_lauf=True, sample_size=2, rows=5, markt_gesamt=2) == "COMPLETE", "ganzer Markt in der Stichprobe"
    a = SP.datenlage_alias
    assert a("POOR", "FULL", "gut", grund="fremdfahrzeuge") == "fehler" and a("POOR", "FULL", "gut", grund="stale") == "veraltet"
    assert a("GOOD", "THIN", "gut") == "duenn" and a("GOOD", "EMPTY", "niedrig") == "leer"
    assert a("GOOD", "FULL", "mittel") == "mittel" and a("MEDIUM", "NORMAL", "gut") == "gut" and a("UNKNOWN", "UNKNOWN", "niedrig") == "keine"
    # Fremdfahrzeug = erkannter falscher Typ; Parserfehler = fehlend/unlesbar/unplausibel; EZ/km/kW-Rand = keines von beiden
    assert NORM.ist_fremdfahrzeug("fremdes Modell") and NORM.ist_fremdfahrzeug("kraftstoff PETROL != DIESEL")
    assert NORM.ist_fremdfahrzeug("getriebe MANUAL_GEAR != AUTOMATIC_GEAR") and NORM.ist_fremdfahrzeug("karosserie Van != EstateCar")
    assert NORM.ist_parserfehler("unbekannt: fuel") and NORM.ist_parserfehler("fehlend: km") and NORM.ist_parserfehler(NORM.GRUND_PREIS_UNPLAUSIBEL)
    assert not NORM.ist_fremdfahrzeug("ez 2021 > 2019") and not NORM.ist_parserfehler("km 5 < 10") and not NORM.ist_fremdfahrzeug("kw 150 > 125")
    assert NORM.MARKT_GESAMT_FELD is None and NORM.markt_gesamt([{"totalResults": "57"}]) is None, "keine geratenen Feldnamen"
    # Altdaten ohne Felder: aus sample_size / Top-N abgeleitet
    q = SP.qualitaet_aus_doc({"sample_size": 4, "top_n_bewiesen": False}, rows=10)
    assert q == {"data_quality": "MEDIUM", "data_quality_grund": "nur_monoton", "market_depth": "THIN", "sample_completeness": "UNKNOWN"}
    assert SP.qualitaet_lesen(None)["datenlage"] == "keine"


# ---------------------------------------------------------------- Phase C: Tagesbasis ueber den Worker
def test_c02_good_thin_bei_2_von_5_und_actor_protokoll(welt, monkeypatch):
    s, seg = _vorbereiten(welt, monkeypatch, seg={**_segment(welt.w), "max_items": 5, "version": 2, "definition_hash": "def-x"})
    db = welt.db
    items = [_item_pos(f"t{s}{i}", 9000 + i * 10, i + 1) for i in range(2)]
    monkeypatch.setattr(APIFY, "lauf", _antwort(items, run_id=f"r-{s}", build_number=f"9.9.{s[:4]}"))
    build = f"9.9.{s[:4]}"
    meta_id = f"actor_meta_{K.actor_und_build(K.actor())[0]}"
    try:
        job = welt.run(JOBS.job_sofort(db, seg["id"]))
        welt.run(JOBS.verarbeiten_buendel(db, [_eigenen_beanspruchen(welt, job["id"])]))
        ts = welt.run(db[K.TAGESSTATS].find_one({"segment_id": seg["id"]}, {"_id": 0}))
        assert ts["sample_size"] == 2 and ts["data_quality"] == "GOOD" and ts["market_depth"] == "THIN" and ts["sample_completeness"] == "UNKNOWN"
        assert ts["valid_runs"] == 1 and ts.get("invalid_runs", 0) == 0 and ts["empty_runs"] == 0 and ts["last_valid_run_at"]
        assert ts["version"] == 2 and ts["definition_hash"] == "def-x" and ts["rows_soll"] == 5
        assert [x["listing_id"] for x in ts["listings"]] == [f"t{s}0", f"t{s}1"]
        assert ts["listings"][0] == {"listing_id": f"t{s}0", "rank": 1, "price": 9000.0, "seller_type": "DEALER"}
        assert ts["dealer_count"] == 2 and ts["private_count"] == 0 and ts["crawl_rows"] == 2 and ts["crawl_cost_usd"] > 0
        assert ts["disappeared_count"] == 0 and ts["price_increases_today"] == 0 and ts["top3_changed"] is None, "ohne Vortag kein Vergleich"
        st = welt.run(db[K.SEGMENTSTATS].find_one({"_id": seg["id"]}, {"_id": 0}))
        assert st["data_quality"] == "GOOD" and st["market_depth"] == "THIN" and st["sample_completeness"] == "UNKNOWN" and st["datenlage"] == "duenn"
        assert st["datenlage_basis"] == "niedrig" and st["letzter_gueltiger_lauf_at"]
        zus = welt.run(ABF.segment_zusammenfassung(db, seg["id"]))
        assert zus["qualitaet"]["data_quality"] == "GOOD" and zus["qualitaet"]["market_depth"] == "THIN" and zus["stats"]["datenlage"] == "duenn"
        j = welt.run(db[K.JOBS].find_one({"id": job["id"]}, {"_id": 0}))
        assert j["sample_incomplete"] is None and j["markt_gesamt"] is None and j["verworfen_fremd"] == 0 and j["parser_fehler"] == 0
        # Actor-Key-Protokoll: einmal je Build nur Feldnamen + Typen, keine Werte, keine PII
        meta = welt.run(db[K.KONFIG].find_one({"_id": meta_id}, {"_id": 0}))
        eintrag = next(b for b in meta["builds"] if b["build"] == build)
        assert eintrag["item_keys"]["priceGross"] == "int" and eintrag["item_keys"]["seller"].startswith("dict[")
        assert eintrag["item_keys"]["searchPosition"] == "int" and "id" in eintrag["item_keys"]
        text = str(eintrag)
        assert "GEHEIM" not in text and "0170" not in text and "Hannover" not in text and "suchen.mobile.de" not in text, "keine Werte, keine PII"
        n_vorher = len(meta["builds"])
        job2 = welt.run(JOBS.job_sofort(db, seg["id"]))
        welt.run(JOBS.verarbeiten_buendel(db, [_eigenen_beanspruchen(welt, job2["id"])]))
        assert len(welt.run(db[K.KONFIG].find_one({"_id": meta_id}))["builds"]) == n_vorher, "je Build nur einmal"
        assert welt.run(db[K.TAGESSTATS].find_one({"segment_id": seg["id"]}, {"_id": 0}))["valid_runs"] == 2
        # Admin-Leseweg fuer das Protokoll (nur Admin, nur lesen)
        r = (BACKEND / "routes" / "markt_admin.py").read_text(encoding="utf-8")
        i = r.index('"/admin/market/actor-meta"')
        assert "Depends(current_admin)" in r[i:i + 200] and "markt_gesamt_feld" in r[i:i + 900]
    finally:
        welt.run(db[K.KONFIG].update_one({"_id": meta_id}, {"$pull": {"builds": {"build": build}}}))
        _aufraeumen(welt)


def test_c03_poor_bei_fremdfahrzeugen_und_ungueltige_laeufe(welt, monkeypatch):
    s, seg = _vorbereiten(welt, monkeypatch, seg={**_segment(welt.w), "max_items": 5})
    db = welt.db
    # 5 geliefert, 3 davon fremdes Modell (> 50 %) -> Lauf gueltig (2 Zeilen), Qualitaet POOR, keine Chancen/Private Deals
    items = [_item_pos(f"t{s}{i}", 9000 + i * 10, i + 1) for i in range(2)] + \
            [{**_item_pos(f"t{s}f{i}", 9500 + i * 10, i + 3), "make": "Audi", "model": "A4"} for i in range(3)]
    monkeypatch.setattr(APIFY, "lauf", _antwort(items))
    job = welt.run(JOBS.job_sofort(db, seg["id"]))
    welt.run(JOBS.verarbeiten_buendel(db, [_eigenen_beanspruchen(welt, job["id"])]))
    ts = welt.run(db[K.TAGESSTATS].find_one({"segment_id": seg["id"]}, {"_id": 0}))
    assert ts["sample_size"] == 2 and ts["data_quality"] == "POOR" and ts["data_quality_grund"] == "fremdfahrzeuge" and ts["market_depth"] == "THIN"
    assert welt.run(db[K.JOBS].find_one({"id": job["id"]}, {"_id": 0}))["verworfen_fremd"] == 3
    st = welt.run(db[K.SEGMENTSTATS].find_one({"_id": seg["id"]}, {"_id": 0}))
    assert st["datenlage"] == "fehler" and st["data_quality"] == "POOR" and st["trend_7d_basis_date"] is None
    assert "private_stand_at" not in st, "kein Private-Deals-Stand aus einem POOR-Lauf"
    # ein Fremdfahrzeug von 5 -> MEDIUM; ueberschreibt den POOR-Tageswert (besser)
    items2 = [_item_pos(f"t{s}{i}", 9000 + i * 10, i + 1) for i in range(4)] + [{**_item_pos(f"t{s}f9", 9900, 5), "make": "Audi", "model": "A4"}]
    monkeypatch.setattr(APIFY, "lauf", _antwort(items2, run_id="r-7"))
    job2 = welt.run(JOBS.job_sofort(db, seg["id"]))
    welt.run(JOBS.verarbeiten_buendel(db, [_eigenen_beanspruchen(welt, job2["id"])]))
    ts = welt.run(db[K.TAGESSTATS].find_one({"segment_id": seg["id"]}, {"_id": 0}))
    assert ts["sample_size"] == 4 and ts["data_quality"] == "MEDIUM" and ts["market_depth"] == "NORMAL" and ts["valid_runs"] == 2
    # wieder ein POOR-Lauf am selben Tag -> der MEDIUM-Tageswert bleibt (Lauf nur in 'laeufe')
    monkeypatch.setattr(APIFY, "lauf", _antwort(items, run_id="r-7b"))
    job2b = welt.run(JOBS.job_sofort(db, seg["id"]))
    welt.run(JOBS.verarbeiten_buendel(db, [_eigenen_beanspruchen(welt, job2b["id"])]))
    ts = welt.run(db[K.TAGESSTATS].find_one({"segment_id": seg["id"]}, {"_id": 0}))
    assert ts["sample_size"] == 4 and ts["data_quality"] == "MEDIUM" and ts["valid_runs"] == 3 and ts["laeufe"][-1]["data_quality"] == "POOR"
    # ungueltiger Lauf (Positionsnummern mit Luecken) -> invalid_runs im Tagesdokument, Hauptwerte bleiben
    items3 = [_item_pos(f"t{s}{i}", 9000 + i * 10, (i + 1) * 2) for i in range(3)]
    monkeypatch.setattr(APIFY, "lauf", _antwort(items3, run_id="r-8"))
    job3 = welt.run(JOBS.job_sofort(db, seg["id"]))
    welt.run(JOBS.verarbeiten_buendel(db, [_eigenen_beanspruchen(welt, job3["id"])]))
    assert welt.run(db[K.JOBS].find_one({"id": job3["id"]}, {"_id": 0}))["status"] == "data_invalid"
    ts = welt.run(db[K.TAGESSTATS].find_one({"segment_id": seg["id"]}, {"_id": 0}))
    assert ts["invalid_runs"] == 1 and ts["valid_runs"] == 3 and ts["sample_size"] == 4 and ts["data_quality"] == "MEDIUM", "Hauptwerte des gueltigen Laufs bleiben"
    assert ts["last_invalid_at"] and "Sortierung" in ts["last_invalid_grund"] and ts["laeufe"][-1]["ungueltig"] is True
    st = welt.run(db[K.SEGMENTSTATS].find_one({"_id": seg["id"]}, {"_id": 0}))
    assert st["invalid_runs_heute"] == 1 and st["sample_size"] == 4 and st["letzter_ungueltiger_lauf_at"]
    # nur ungueltige Laeufe an einem Tag in einem Segment -> Tagesdokument ohne sample_size, POOR/ungueltig, KEINE Marktluecke
    seg2 = {**_segment(welt.w), "id": f"test-320d-{s}:2019-2021:1-2", "min_km": 1, "max_km": 2, "max_items": 5}
    welt.run(db[K.SEGMENTE].insert_one(dict(seg2)))
    monkeypatch.setattr(APIFY, "lauf", _antwort([{**_item_pos(f"t{s}x{i}", 9000 + i, (i + 1) * 2), "mileageKm": 1} for i in range(3)], run_id="r-9"))
    job4 = welt.run(JOBS.job_sofort(db, seg2["id"]))
    welt.run(JOBS.verarbeiten_buendel(db, [_eigenen_beanspruchen(welt, job4["id"])]))
    ts2 = welt.run(db[K.TAGESSTATS].find_one({"segment_id": seg2["id"]}, {"_id": 0}))
    assert ts2 and ts2["invalid_runs"] == 1 and ts2["valid_runs"] == 0 and ts2["data_quality"] == "POOR" and ts2["data_quality_grund"] == "ungueltig"
    assert ts2["market_depth"] == "UNKNOWN" and ts2["sample_completeness"] == "UNKNOWN" and "sample_size" not in ts2 and ts2["crawl_cost_usd"] > 0
    st2 = welt.run(db[K.SEGMENTSTATS].find_one({"_id": seg2["id"]}, {"_id": 0}))
    assert st2["sample_size"] is None and st2["datenlage"] == "fehler" and st2["data_quality"] == "POOR"
    verlauf = welt.run(ABF.segment_verlauf(db, seg2["id"], "7d"))
    assert verlauf["reihe"][-1]["nur_ungueltig"] is True and verlauf["reihe"][-1]["kein_angebot"] is False, "ungueltig ist keine Marktluecke"
    assert verlauf["auswertung"]["tage_nur_ungueltig"] == 1 and verlauf["auswertung"]["tage_ohne_angebot"] == 0
    assert welt.run(ABF.segment_listings(db, seg2["id"]))["listings"] == []
    # Trend-/Chancenbasis: nur Tage mit GOOD/MEDIUM
    assert '"data_quality": {"$nin": ["POOR", "UNKNOWN"]}' in inspect.getsource(SP._tagesstat_vor)
    assert '"data_quality": {"$nin": ["POOR", "UNKNOWN"]}' in inspect.getsource(SP.verarbeiten)
    _aufraeumen(welt)


def test_c04_verschwunden_preiserhoehung_top3(welt):
    w, db = welt.w, welt.db
    _aufraeumen(welt)
    s = w.s
    seg = {**_segment(w), "max_items": 5}
    welt.run(db[K.MODELLE].insert_one(_modell(w)))
    welt.run(db[K.SEGMENTE].insert_one(dict(seg)))
    gestern = K.jetzt() - timedelta(days=1)
    tag1 = NORM.listings_aus_items([_item(f"t{s}a", 18000), _item(f"t{s}b", 18500), _item(f"t{s}c", 19000), _item(f"t{s}d", 19500)])
    welt.run(SP.verarbeiten(db, seg, tag1, beobachtet=gestern))
    # heute: b weg, a teurer, e neu ganz vorn -> Top 3 anders
    tag2 = NORM.listings_aus_items([_item(f"t{s}e", 17000), _item(f"t{s}a", 18200), _item(f"t{s}c", 19000), _item(f"t{s}d", 19500)])
    welt.run(SP.verarbeiten(db, seg, tag2))
    ts = welt.run(db[K.TAGESSTATS].find_one({"segment_id": seg["id"], "date": _tag(0)}, {"_id": 0}))
    assert ts["disappeared_ids"] == [f"t{s}b"] and ts["disappeared_count"] == 1 and ts["vergleich_vortag"] == _tag(-1)
    assert ts["price_increase_ids"] == [f"t{s}a"] and ts["price_increases_today"] == 1 and ts["price_reductions_today"] == 0
    assert ts["top3_changed"] is True and ts["top5_changed"] is True
    assert ts["listings"][0]["listing_id"] == f"t{s}e" and ts["listings"][1]["price"] == 18200.0
    assert ts["data_quality"] == "GOOD" and ts["market_depth"] == "NORMAL" and ts["sample_completeness"] == "UNKNOWN"
    # zweiter Lauf am selben Tag mit gleichen Autos: Vergleich weiter zum VORTAG, Preiserhoehung bleibt vermerkt (Vereinigung)
    welt.run(SP.verarbeiten(db, seg, tag2, lauf_tag=f"{_tag(0)}#2"))
    ts = welt.run(db[K.TAGESSTATS].find_one({"segment_id": seg["id"], "date": _tag(0)}, {"_id": 0}))
    assert ts["price_increase_ids"] == [f"t{s}a"] and ts["valid_runs"] == 2 and ts["top3_changed"] is True and ts["disappeared_count"] == 1
    # gleicher Bestand wie gestern (andere Reihenfolge) -> nichts verschwunden, Top-3 gleich
    seg3 = {**seg, "id": f"test-320d-{s}:2019-2021:1-2", "min_km": 1, "max_km": 2}
    welt.run(db[K.SEGMENTE].insert_one(dict(seg3)))
    welt.run(SP.verarbeiten(db, seg3, tag1, beobachtet=gestern))
    welt.run(SP.verarbeiten(db, seg3, NORM.listings_aus_items([_item(f"t{s}b", 18000), _item(f"t{s}a", 18500), _item(f"t{s}c", 19000),
                                                                _item(f"t{s}d", 19500)])))
    ts3 = welt.run(db[K.TAGESSTATS].find_one({"segment_id": seg3["id"], "date": _tag(0)}, {"_id": 0}))
    assert ts3["disappeared_count"] == 0 and ts3["top3_changed"] is False and ts3["top5_changed"] is False
    assert ts3["price_increase_ids"] == [f"t{s}a"] and ts3["price_reduced_ids"] == [f"t{s}b"], "a teurer, b guenstiger (je Segment)"
    _aufraeumen(welt)


def test_c05_migration_altdaten_und_frische(welt):
    w, db = welt.w, welt.db
    _aufraeumen(welt)
    s = w.s
    seg = {**_segment(w), "max_items": 10, "version": 3, "definition_hash": "d3"}
    welt.run(db[K.MODELLE].insert_one(_modell(w)))
    welt.run(db[K.SEGMENTE].insert_one(dict(seg)))
    jetzt = K.jetzt()
    alt = [{"segment_id": seg["id"], "date": _tag(-3), "sample_size": 10, "median_price": 20000, "listing_ids": [f"t{s}{i}" for i in range(10)],
            "model_id": seg["model_id"], "observed_at": (jetzt - timedelta(hours=72)).isoformat()},
           {"segment_id": seg["id"], "date": _tag(-2), "sample_size": 4, "median_price": 20000, "listing_ids": [f"t{s}{i}" for i in range(4)],
            "top_n_bewiesen": False, "model_id": seg["model_id"], "observed_at": (jetzt - timedelta(hours=48)).isoformat()},
           {"segment_id": seg["id"], "date": _tag(-1), "sample_size": 0, "median_price": None, "listing_ids": [],
            "laeufe": [{"leer": True, "sample_size": 0}], "model_id": seg["model_id"], "observed_at": (jetzt - timedelta(hours=20)).isoformat()}]
    welt.run(db[K.TAGESSTATS].insert_many([dict(d) for d in alt]))
    erg = welt.run(MIG.m19_markt_tagesbasis(db))
    assert erg["tagesstats"] >= 3
    docs = {d["date"]: d for d in welt.run(db[K.TAGESSTATS].find({"segment_id": seg["id"]}, {"_id": 0}).to_list(10))}
    assert docs[_tag(-3)]["data_quality"] == "GOOD" and docs[_tag(-3)]["market_depth"] == "FULL" and docs[_tag(-3)]["sample_completeness"] == "COMPLETE"
    assert docs[_tag(-2)]["data_quality"] == "MEDIUM" and docs[_tag(-2)]["market_depth"] == "THIN" and docs[_tag(-2)]["sample_completeness"] == "UNKNOWN"
    assert docs[_tag(-1)]["data_quality"] == "GOOD" and docs[_tag(-1)]["market_depth"] == "EMPTY" and docs[_tag(-1)]["empty_runs"] == 1
    assert all(d["version"] == 3 and d["definition_hash"] == "d3" and d["valid_runs"] == 1 and d["last_valid_run_at"] for d in docs.values())
    assert welt.run(MIG.m19_markt_tagesbasis(db))["tagesstats"] == 0, "idempotent"
    nummern = {n: name for n, name, _ in MIG.MIGRATIONEN}
    assert nummern[19] == "markt_tagesbasis" and MIG.ZIEL_VERSION == 19
    # Frische: der letzte gueltige Lauf (Tag -1, vor 20 h) ist frisch -> leerer Markt 'leer'; 72 h alt -> 'veraltet'
    st = welt.run(SP.segmentstatistik(db, seg["id"]))
    assert st["data_quality"] == "GOOD" and st["market_depth"] == "EMPTY" and st["datenlage"] == "leer"
    st3 = welt.run(SP.segmentstatistik(db, seg["id"], _tag(-3)))
    assert st3["data_quality"] == "POOR" and st3["data_quality_grund"] == "stale" and st3["datenlage"] == "veraltet"
    # auch ohne neuen Lauf veraltet eine gespeicherte Statistik beim Lesen (Karte, Modell, Segment)
    frisch = {**welt.run(SP.segmentstatistik(db, seg["id"])), "sample_size": 10, "market_depth": "FULL"}
    assert SP.qualitaet_lesen(frisch)["data_quality"] == "GOOD"
    spaeter = SP.qualitaet_lesen(frisch, jetzt=jetzt + timedelta(hours=40))
    assert spaeter["data_quality"] == "POOR" and spaeter["data_quality_grund"] == "stale" and spaeter["datenlage"] == "veraltet"
    _aufraeumen(welt)


def test_c06_lesewege_und_oberflaeche():
    q = inspect.getsource(ABF.karte)
    assert '"data_quality"' in q and '"market_depth"' in q and '"sample_completeness"' in q and "qualitaet_lesen" in q
    assert "qualitaet_lesen" in inspect.getsource(ABF.modell_detail) and "qualitaet_zaehler" in inspect.getsource(ABF.modelle_uebersicht)
    js = (FRONTEND / "lib" / "markt.js").read_text(encoding="utf-8")
    for t in ("DATENQUALITAET", "MARKTTIEFE", "VOLLSTAENDIGKEIT", "fehler:", "duenn:", "leer:", "veraltet:", "Low-Market-Median"):
        assert t in js, t
    for name in ("components/MarktdatenKarte.jsx", "pages/admin_v2/MarktModell.jsx", "pages/admin_v2/Markt.jsx"):
        assert "MarktQualitaet" in (FRONTEND / name).read_text(encoding="utf-8"), name
    modell = (FRONTEND / "pages" / "admin_v2" / "MarktModell.jsx").read_text(encoding="utf-8")
    assert "Median Top-" not in modell and "Durchschnitt Top-" not in modell and "lowMarketLabel" in modell
