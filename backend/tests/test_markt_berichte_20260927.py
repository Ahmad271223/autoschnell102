# -*- coding: utf-8 -*-
"""Master-Auftrag Marktanalyse (Ahmad 26.09.2026), Phase E — Berichte 5 Tage / 15 Tage / Monat
(Abschnitte 12-21, 34-39, 46, 50-55).

Je Regel ein Test: Periodengrenzen (26.-28./29./30./31., 15-Tage-Haelften, Monat, Europe/Berlin inkl. Sommerzeit);
Kennzahlen des Monatsberichts (Start/Ende gleicher Segmentkorb, Fallen/Steigen/Stabil, Summen, Serien,
Volatilitaet, Preissenkungen derselben Inserate, Hot Deals, Kosten, Luecken ohne Interpolation, 5-Tage-Bloecke);
gewichtete Modellaggregation ohne Mix-Effekt; POOR-Tage zaehlen nicht; Fassungswechsel = neue Zeitreihe; Bericht
erst nach Karenz final; laufende Periode nur vorlaeufig (nie gespeichert); zweiter Lauf aendert nichts
(idempotent, zwei Server); finaler Bericht bleibt unveraendert, auch wenn danach Tagesdaten geaendert werden;
Hot-Deal-Auswertung offen -> Bericht wartet; keine Crawl-Funktion wird aufgerufen; Uebersicht aller Modelle und
Routen. Alle Tage und "jetzt" werden ausdruecklich uebergeben (kein Mitternachts-Effekt); Testdaten nur test-/t<hex>.
"""
import asyncio
import inspect
import random
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_befunde_runde17_termine import _module, welt  # noqa: E402,F401
from test_markt_20260926 import APIFY, ENT, JOBS, K, SEG, SP, _modell  # noqa: E402
from test_markt_hotdeals_20260927 import _aufraeumen  # noqa: E402

B = _module("markt.berichte")
D = _module("markt.deals")
AUS = _module("markt.auswertung")
A = _module("markt.auftraege")
BACKEND = Path(__file__).resolve().parent.parent


def _t(tag, monat=2, jahr=2028):
    return f"{jahr:04d}-{monat:02d}-{tag:02d}"


def _seg_daten(welt, name="20000-40000", *, version=1, dh="h1", enabled=True, praefix=""):
    """Segment wie in market_segments — nur das Dokument (reine Rechnung ohne Datenbank)."""
    s = welt.w.s
    lo, hi = name.split("-")
    return {"id": f"test-320d-{s}:{praefix}2020:{name}", "model_id": f"test-320d-{s}", "label": "BMW 320d (Test)", "min_km": int(lo),
            "max_km": int(hi), "km_label": f"{int(lo) // 1000}–{int(hi) // 1000}k km", "year_from": 2020, "year_to": 2020, "ez_label": "EZ 2020",
            "max_items": 5, "enabled": enabled, "version": version, "definition_hash": dh}


def _seg(welt, name="20000-40000", **kw):
    seg = _seg_daten(welt, name, **kw)
    welt.run(welt.db[K.SEGMENTE].insert_one(dict(seg)))
    return seg


def _start(welt):
    _aufraeumen(welt)
    welt.run(_module("indizes").markt_indizes(welt.db))
    welt.run(welt.db[K.MODELLE].insert_one({**_modell(welt.w), "gearbox": "AUTOMATIC_GEAR"}))
    return f"test-320d-{welt.w.s}"


def _doc(welt, seg, tag, median, **kw):
    doc = _doc_daten(welt, seg, tag, median, **kw)
    welt.run(welt.db[K.TAGESSTATS].update_one({"segment_id": seg["id"], "date": tag}, {"$set": doc}, upsert=True))
    return doc


def _doc_daten(welt, seg, tag, median, *, n=5, dq="GOOD", neu=(), weg=(), red=(), inc=(), top3=False, top5=False, hot=(), hot_privat=(),
               hot_neu=(), kosten=0.01, version=None, dh=None, vergleich=True, offen=False):
    """Tagesdokument wie speicher.verarbeiten (Phase C/D): Stichprobe symmetrisch um den Median (Inserate a, b, c ...);
    n=0 ist ein gueltiger leerer Lauf (EMPTY)."""
    s = welt.w.s
    schritt = 200
    versatz = [(i - (n - 1) / 2) * schritt for i in range(n)]
    zeilen = [(f"t{s}{chr(97 + i)}", float(median + v)) for i, v in enumerate(versatz)]
    kz = SP.kennzahlen([p for _, p in zeilen])
    ids = [lid for lid, _ in zeilen]
    doc = {"segment_id": seg["id"], "date": tag, "model_id": seg["model_id"], **kz, "listing_ids": ids, "listing_ids_alle": ids,
           "listings": [{"listing_id": lid, "rank": r, "price": p, "seller_type": "DEALER"} for r, (lid, p) in enumerate(zeilen, 1)],
           "new_in_sample_ids": [f"t{s}{x}" for x in neu], "disappeared_ids": [f"t{s}{x}" for x in weg], "disappeared_count": len(weg),
           "price_reduced_ids": [f"t{s}{x}" for x in red], "price_reductions_today": len(red),
           "price_increase_ids": [f"t{s}{x}" for x in inc], "price_increases_today": len(inc),
           "top3_changed": top3, "top5_changed": top5, "data_quality": dq, "market_depth": "FULL" if n >= 5 else "THIN" if n else "EMPTY",
           "sample_completeness": "COMPLETE", "version": version or seg["version"], "definition_hash": dh or seg["definition_hash"],
           "observed_at": f"{tag}T05:00:00+00:00", "rows_soll": 5, "valid_runs": 1, "crawl_cost_usd": kosten,
           "vergleich_vortag": "x" if vergleich else None, "top_n_bewiesen": True,
           "hot_deal_ids_tag": [f"t{s}{x}" for x in hot], "hot_deal_privat_ids_tag": [f"t{s}{x}" for x in hot_privat],
           "hot_deal_neu_ids": [f"t{s}{x}" for x in hot_neu]}
    if offen:
        doc["hot_deals_offen"] = True
    return doc


def _ungueltig_daten(seg, tag, kosten=0.02):
    return {"segment_id": seg["id"], "date": tag, "model_id": seg["model_id"], "invalid_runs": 1, "valid_runs": 0, "data_quality": "POOR",
            "data_quality_grund": "ungueltig", "market_depth": "UNKNOWN", "version": seg["version"], "definition_hash": seg["definition_hash"],
            "crawl_cost_usd": kosten}


def _ungueltig(welt, seg, tag, kosten=0.02):
    welt.run(welt.db[K.TAGESSTATS].insert_one(_ungueltig_daten(seg, tag, kosten)))


def _nach(bis, stunden=1):
    """Ein Zeitpunkt nach Periodenende + Karenz (bzw. davor bei negativen Stunden)."""
    return B.faellig_ab(bis) + timedelta(hours=stunden)


def _bericht(welt, mid, typ, von, bis):
    return welt.run(welt.db[K.BERICHTE].find_one({"model_id": mid, "typ": typ, "periode_von": von, "periode_bis": bis}, {"_id": 0}))


# ---------------------------------------------------------------- Perioden
def test_01_perioden_grenzen_und_faelligkeit():
    """5-Tage-Bloecke fest (26.-Monatsende: 28/29/30/31), 15-Tage 01.-15. und 16.-Monatsende, Monat; faellig am
    Folgetag 06:00 deutscher Zeit (Sommer- und Winterzeit)."""
    feb28 = B.perioden_im_monat(2028, 2)
    fuenf = [(v, b) for t, v, b in feb28 if t == "FIVE_DAY"]
    assert fuenf == [(_t(1), _t(5)), (_t(6), _t(10)), (_t(11), _t(15)), (_t(16), _t(20)), (_t(21), _t(25)), (_t(26), _t(29))], "Schaltjahr 29"
    assert [(v, b) for t, v, b in feb28 if t == "FIFTEEN_DAY"] == [(_t(1), _t(15)), (_t(16), _t(29))]
    assert [(v, b) for t, v, b in feb28 if t == "MONTHLY"] == [(_t(1), _t(29))]
    assert ("FIVE_DAY", "2027-02-26", "2027-02-28") in B.perioden_im_monat(2027, 2)
    assert ("FIVE_DAY", "2026-04-26", "2026-04-30") in B.perioden_im_monat(2026, 4)
    assert ("FIVE_DAY", "2026-01-26", "2026-01-31") in B.perioden_im_monat(2026, 1)
    assert ("FIFTEEN_DAY", "2026-01-16", "2026-01-31") in B.perioden_im_monat(2026, 1) and ("MONTHLY", "2026-01-01", "2026-01-31") in B.perioden_im_monat(2026, 1)
    assert len(B.tage_zwischen("2027-02-01", "2027-02-28")) == 28 and len(B.tage_zwischen("2026-01-01", "2026-01-31")) == 31
    assert B.periode_gueltig("FIVE_DAY", _t(26), _t(29)) and not B.periode_gueltig("FIVE_DAY", _t(26), _t(28))
    assert not B.periode_gueltig("FIVE_DAY", _t(2), _t(6)), "keine rollenden Fenster"
    assert not B.periode_gueltig("WEEKLY", _t(1), _t(7)) and not B.periode_gueltig("FIFTEEN_DAY", _t(11), _t(25))
    # Karenz 6 h nach Mitternacht (deutsche Zeit): Sommer UTC+2 -> 04:00 UTC, Winter UTC+1 -> 05:00 UTC
    assert B.faellig_ab("2026-07-05") == datetime(2026, 7, 6, 4, 0, tzinfo=timezone.utc)
    assert B.faellig_ab("2026-01-05") == datetime(2026, 1, 6, 5, 0, tzinfo=timezone.utc)
    assert B.faellig_ab("2026-10-25") == datetime(2026, 10, 26, 5, 0, tzinfo=timezone.utc), "Tag der Zeitumstellung"
    assert B.BERICHT_KARENZ_STUNDEN == 6 and B.STABIL_PCT == 0.5
    jetzt = datetime(2028, 3, 1, 5, 30, tzinfo=timezone.utc)                  # 01.03. 06:30 deutscher Zeit
    faellig = B.faellige_perioden(jetzt)
    assert ("FIVE_DAY", _t(26), _t(29)) in faellig and ("MONTHLY", _t(1), _t(29)) in faellig and ("FIFTEEN_DAY", _t(16), _t(29)) in faellig
    assert ("FIVE_DAY", "2028-03-01", "2028-03-05") not in faellig
    frueh = datetime(2028, 3, 1, 4, 30, tzinfo=timezone.utc)                  # 05:30 deutscher Zeit: noch Karenz
    assert ("MONTHLY", _t(1), _t(29)) not in B.faellige_perioden(frueh)
    assert B.richtung(-0.51) == "FALLING" and B.richtung(0.5) == "STABLE" and B.richtung(-0.5) == "STABLE" and B.richtung(0.6) == "RISING"
    assert B.richtung(None) == "UNKNOWN"


# ---------------------------------------------------------------- Kennzahlen
def _monatsverlauf():
    """Tag -> Median: 5 fallende Tage, 1 stabil, 3 steigende, Luecke am 11., stabil bis 28., am 29. fallend."""
    m = {1: 20000}
    for d in range(2, 7):
        m[d] = m[d - 1] - 200
    m[7] = 19000
    for d in range(8, 11):
        m[d] = m[d - 1] + 300
    for d in range(12, 29):
        m[d] = 19900
    m[20], m[21] = 19950, 19900
    m[29] = 19800
    return m


def test_02_monatsbericht_kennzahlen_bewegung_und_luecken(welt):
    mid = _start(welt)
    seg = _seg(welt)
    s = welt.w.s
    try:
        verlauf = _monatsverlauf()
        for d, med in verlauf.items():
            _doc(welt, seg, _t(d), med, red=("a",) if 2 <= d <= 6 else (), inc=("a",) if 8 <= d <= 10 else (),
                 neu=("n1",) if d == 5 else (), weg=("x1",) if d == 7 else (), top3=d == 5, top5=d in (5, 7),
                 hot=("h1",) if d == 3 else ("h1", "h2") if d == 4 else (), hot_privat=("h2",) if d == 4 else (),
                 hot_neu=("h1",) if d == 3 else ("h2",) if d == 4 else (), vergleich=d > 1)
        for e in ({"listing_id": f"t{s}h1", "tag": _t(3), "typ": "NEW_HOT_DEAL", "diff_pct": 9.0, "klasse": "STRONG", "privat": False},
                  {"listing_id": f"t{s}h1", "tag": _t(6), "typ": "PRICE_DROP_HOT_DEAL", "diff_pct": 12.0, "klasse": "EXTREME", "privat": False},
                  {"listing_id": f"t{s}h2", "tag": _t(4), "typ": "NEW_HOT_DEAL", "diff_pct": 6.0, "klasse": "DEAL", "privat": True}):
            welt.run(welt.db[K.HOTDEAL_EREIGNISSE].insert_one({**e, "segment_id": seg["id"], "model_id": mid, "lauf_key": e["tag"], "price": 17000.0,
                                                               "reference_price": 19500.0, "diff_eur": 1500.0, "rank": 1,
                                                               "seller_name": "darf nicht in den Bericht"}))
        assert welt.run(B.finalisieren(welt.db, mid, "MONTHLY", _t(1), _t(29), jetzt=_nach(_t(29)))) == "erstellt"
        b = _bericht(welt, mid, "MONTHLY", _t(1), _t(29))
        k = b["kennzahlen"]
        assert b["status"] == "FINAL" and b["revision"] == 1 and b["stabil_zone_pct"] == 0.5 and b["modell"]["gearbox"] == "AUTOMATIC_GEAR"
        # Start/Ende/Differenz ueber den Segmentkorb; Richtung aus der Stabilitaetszone
        assert (k["startwert"], k["endwert"], k["delta_eur"], k["delta_pct"], k["richtung"]) == (20000, 19800, -200, -1.0, "FALLING")
        assert k["same_listing_price_change_eur"] == -200 and k["same_listing_anzahl"] == 5 and k["sample_market_change_eur"] == -200
        werte = list(verlauf.values())
        assert k["median_periode"] == statistics.median(werte) and k["mittelwert_periode"] == round(sum(werte) / len(werte), 2)
        assert k["minimum"] == {"date": _t(6), "wert": 19000} and k["maximum"] == {"date": _t(1), "wert": 20000}
        assert k["guenstigstes_angebot"] == {"date": _t(6), "preis": 18600}
        # Luecke am 11.: nicht interpoliert, Abdeckung 28/29, Vergleich 12. zum 11. faellt weg
        assert (k["coverage_days"], k["expected_days"]) == (28, 29)
        tag11 = next(r for r in b["tage"] if r["date"] == _t(11))
        assert tag11["median"] is None and tag11["gueltig"] is False and tag11["delta_vortag_eur"] is None
        assert next(r for r in b["tage"] if r["date"] == _t(12))["delta_vortag_eur"] is None
        assert len(b["tage"]) == 29 and b["tage"][1]["delta_vortag_eur"] == -200 and b["tage"][1]["richtung"] == "FALLING"
        # Fallen / Steigen / Stabil (Abschnitt 19/20): nur direkt vergleichbare Tage innerhalb des Monats
        bw = b["bewegung"]
        assert (bw["vergleiche"], bw["fallend"], bw["steigend"], bw["stabil"]) == (26, 6, 3, 17)
        assert bw["fallend_pct"] == round(6 / 26 * 100, 1) and bw["stabil_pct"] == round(17 / 26 * 100, 1)
        assert (bw["summe_negativ_eur"], bw["summe_positiv_eur"], bw["netto_eur"]) == (-1150, 950, -200)
        assert bw["staerkster_rueckgang_eur"]["date"] == _t(2) and bw["staerkster_rueckgang_pct"]["date"] == _t(6)
        assert bw["staerkster_anstieg_eur"]["eur"] == 300 and bw["staerkster_anstieg_pct"]["date"] == _t(8)
        assert (bw["laengste_fallserie"], bw["laengste_steigeserie"]) == (5, 3) and bw["volatilitaet_pct"] > 0
        # Marktaktivitaet: nur dieselbe listing_id zaehlt als Preisaenderung
        assert (k["preissenkungen"], k["reduzierte_listings"], k["mittlere_senkung_eur"]) == (5, 1, -200)
        assert (k["preiserhoehungen"], k["erhoehte_listings"], k["mittlere_erhoehung_eur"]) == (3, 1, 300)
        assert (k["neue_listings"], k["verschwundene_listings"], k["unterschiedliche_listings"]) == (1, 1, 5)
        assert (k["top3_wechsel"], k["top5_wechsel"]) == (1, 2)
        assert (k["hot_deals"], k["private_hot_deals"], k["hot_deals_neu"]) == (2, 1, 2)
        # Kosten (aus crawl_cost_usd der Tagesdokumente) und Kosteneffizienz (Abschnitt 38)
        assert k["kosten_usd"] == 0.28 and k["cost_per_valid_observation"] == 0.01 and k["cost_per_unique_listing"] == 0.056 and k["cost_per_hot_deal"] == 0.14
        assert k["data_quality"] == "GOOD" and k["market_depth"] == "FULL" and k["confidence"] == "MEDIUM"
        assert "wenige verschiedene Inserate" in k["confidence_gruende"] and (k["segmente_mit_daten"], k["segmente_gesamt"]) == (1, 1)
        # Segmentdetail (Abschnitt 21) und 5-Tage-Bloecke inkl. 26.-29.
        sd = b["segmente"][0]
        assert (sd["aktueller_median"], sd["delta_eur"], sd["gueltige_tage"], sd["erwartete_tage"], sd["listings"], sd["hot_deals"]) == (19800, -200, 28, 29, 5, 2)
        assert sd["health"] is None and sd["kosten_usd"] == 0.28
        assert [(x["von"], x["bis"]) for x in b["bloecke"]] == [(_t(1), _t(5)), (_t(6), _t(10)), (_t(11), _t(15)), (_t(16), _t(20)), (_t(21), _t(25)), (_t(26), _t(29))]
        assert (b["bloecke"][0]["delta_eur"], b["bloecke"][0]["richtung"]) == (-800, "FALLING") and b["bloecke"][1]["richtung"] == "RISING"
        assert b["bloecke"][2]["coverage_days"] == 4 and b["bloecke"][5]["expected_days"] == 4
        # Hot Deals des Zeitraums kompakt (IDs/Preise/Raenge, keine PII), bester Stand je Inserat
        assert [(h["listing_id"][11:], h["diff_pct"]) for h in b["hot_deals_top"]] == [("h1", 12.0), ("h2", 6.0)]
        assert "seller_name" not in str(b) and "darf nicht" not in str(b)
        # 5-Tage-Bericht fuer den letzten Block 26.-29. Februar (4 Kalendertage)
        assert welt.run(B.finalisieren(welt.db, mid, "FIVE_DAY", _t(26), _t(29), jetzt=_nach(_t(29)))) == "erstellt"
        f = _bericht(welt, mid, "FIVE_DAY", _t(26), _t(29))
        assert [r["date"] for r in f["tage"]] == [_t(26), _t(27), _t(28), _t(29)] and "bloecke" not in f
        assert f["kennzahlen"]["expected_days"] == 4 and f["bewegung"]["vergleiche"] == 3
    finally:
        _aufraeumen(welt)


def test_03_modellaggregation_gewichtet_ohne_mix_effekt(welt):
    """Abschnitte 12/54: Tagesniveau gewichtet nach Stichprobe; ein neu hinzukommendes (billigeres) Segment senkt das
    Niveau (sample_market_change), ist aber keine Preisbewegung (Tagesbewegung nur ueber gemeinsame Segmente)."""
    mid = _start(welt)
    a, b2 = _seg(welt, "20000-40000"), _seg(welt, "40001-60000")
    try:
        for d in range(1, 11):
            _doc(welt, a, _t(d), 20000)
            if d >= 4:
                _doc(welt, b2, _t(d), 15000, n=10)
        ber = welt.run(B.bericht_berechnen(welt.db, mid, "FIVE_DAY", _t(1), _t(5)))
        tage = {r["date"]: r for r in ber["tage"]}
        assert tage[_t(3)]["median"] == 20000 and tage[_t(4)]["median"] == round((20000 * 5 + 15000 * 10) / 15, 2)
        assert tage[_t(4)]["delta_vortag_eur"] == 0 and tage[_t(4)]["richtung"] == "STABLE" and tage[_t(4)]["vergleich_segmente"] == 1
        assert tage[_t(4)]["segmente"] == 2 and tage[_t(4)]["listings"] == 15
        assert tage[_t(5)]["p25"] is not None and tage[_t(5)]["min"] == 15000 - 900
        k = ber["kennzahlen"]
        assert k["delta_eur"] == 0 and k["richtung"] == "STABLE" and k["korb_segmente"] == 2
        assert k["sample_market_change_eur"] == round((20000 * 5 + 15000 * 10) / 15 - 20000, 2), "Mix-Effekt getrennt ausgewiesen"
        assert ber["bewegung"]["stabil"] == 4 and ber["bewegung"]["fallend"] == 0
        assert [x["segment_id"] for x in ber["segmente"]] == [a["id"], b2["id"]]
    finally:
        _aufraeumen(welt)


def test_04_poor_tage_zaehlen_nicht(welt):
    """Basis nur GOOD/MEDIUM: ein POOR-Tag (Ausreisser 10.000) ist kein Tageswert; ein Tag mit nur ungueltigen
    Laeufen ist keine Marktluecke — beide zaehlen nur bei Datenqualitaet (und Kosten)."""
    mid = _start(welt)
    seg = _seg(welt)
    try:
        for d in (1, 2, 5):
            _doc(welt, seg, _t(d), 20000)
        _doc(welt, seg, _t(3), 10000, dq="POOR")
        _ungueltig(welt, seg, _t(4))
        ber = welt.run(B.bericht_berechnen(welt.db, mid, "FIVE_DAY", _t(1), _t(5)))
        tage = {r["date"]: r for r in ber["tage"]}
        assert tage[_t(3)]["median"] is None and tage[_t(3)]["gueltig"] is False and tage[_t(3)]["data_quality"] == "POOR"
        assert tage[_t(4)]["nur_ungueltig"] is True and tage[_t(4)]["leer"] is False and tage[_t(4)]["kosten_usd"] == 0.02
        k = ber["kennzahlen"]
        assert k["minimum"]["wert"] == 20000 and k["coverage_days"] == 3 and k["data_quality_zaehler"] == {"GOOD": 3, "POOR": 2}
        assert k["data_quality"] == "POOR" and k["kosten_usd"] == 0.06
        assert tage[_t(5)]["delta_vortag_eur"] is None, "kein Vergleich ueber die Luecke"
        assert ber["bewegung"]["vergleiche"] == 1
    finally:
        _aufraeumen(welt)


def test_05_fassungswechsel_neue_zeitreihe(welt):
    """Abschnitte 34/46: Fassung 1 (Tage 1-10) und Fassung 2 (ab Tag 11) werden nie zusammengerechnet — der Bericht
    rechnet die aktuelle Fassung, die fruehere steht getrennt; erwartet werden nur die Tage der neuen Fassung."""
    mid = _start(welt)
    alt = _seg(welt, enabled=False)
    neu = _seg(welt, version=2, dh="h2", praefix="v2:")
    try:
        for d in range(1, 11):
            _doc(welt, alt, _t(d), 20000)
        for d in range(11, 30):
            _doc(welt, neu, _t(d), 25000 + (d - 11) * 10)
        ber = welt.run(B.bericht_berechnen(welt.db, mid, "MONTHLY", _t(1), _t(29)))
        k = ber["kennzahlen"]
        assert ber["fassung"] == {"version": 2, "definition_hash": "h2", "ab": _t(11)}
        assert k["startwert"] == 25000 and k["endwert"] == 25180 and (k["coverage_days"], k["expected_days"]) == (19, 19)
        assert ber["fruehere_fassungen"] == [{"version": 1, "definition_hash": "h1", "von": _t(1), "bis": _t(10), "tage": 10,
                                              "startwert": 20000, "endwert": 20000, "delta_eur": 0, "delta_pct": 0}]
        assert any("Fassungswechsel" in h for h in ber["hinweise"])
        tage = {r["date"]: r for r in ber["tage"]}
        assert tage[_t(10)]["median"] is None and tage[_t(10)]["andere_fassung"] is True and tage[_t(11)]["andere_fassung"] is False
        assert tage[_t(11)]["delta_vortag_eur"] is None, "kein Vergleich ueber den Fassungswechsel"
        assert k["kosten_usd"] == 0.29, "Kosten zaehlen fuer alle Fassungen (reale Ausgaben)"
        assert [x["segment_id"] for x in ber["segmente"]] == [neu["id"]] and (k["segmente_mit_daten"], k["segmente_gesamt"]) == (1, 1)
    finally:
        _aufraeumen(welt)


# ---------------------------------------------------------------- Einfrieren
def test_06_erst_nach_karenz_final(welt):
    mid = _start(welt)
    seg = _seg(welt)
    try:
        for d in range(1, 6):
            _doc(welt, seg, _t(d), 20000)
        assert welt.run(B.finalisieren(welt.db, mid, "FIVE_DAY", _t(1), _t(5), jetzt=_nach(_t(5), -0.02))) == "nicht_faellig"
        assert _bericht(welt, mid, "FIVE_DAY", _t(1), _t(5)) is None
        assert welt.run(B.finalisieren(welt.db, mid, "FIVE_DAY", _t(1), _t(5), jetzt=_nach(_t(5), 0.02))) == "erstellt"
        b = _bericht(welt, mid, "FIVE_DAY", _t(1), _t(5))
        assert b["status"] == "FINAL" and b["erstellt_at"] >= B.faellig_ab(_t(5)).isoformat()
        try:
            welt.run(B.finalisieren(welt.db, mid, "FIVE_DAY", _t(2), _t(6), jetzt=_nach(_t(6))))
            assert False, "ungueltige Periode"
        except ValueError:
            pass
    finally:
        _aufraeumen(welt)


def test_07_laufende_periode_nur_vorlaeufig(welt, monkeypatch):
    """Die laufende Periode wird live gerechnet ('VORLAEUFIG') und nie gespeichert; faellige_finalisieren friert nur
    die faelligen Perioden ein."""
    mid = _start(welt)
    seg = _seg(welt)
    jetzt = datetime(2028, 2, 10, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(K, "jetzt", lambda: jetzt)
    try:
        for d in range(1, 11):
            _doc(welt, seg, _t(d), 20000 - d * 100)
        vorl = welt.run(B.modell_bericht(welt.db, mid, "FIVE_DAY", _t(6), _t(10)))
        assert vorl["status"] == "VORLAEUFIG" and vorl["kennzahlen"]["delta_eur"] == -400 and any("vorläufig" in h for h in vorl["hinweise"])
        assert welt.run(welt.db[K.BERICHTE].count_documents({"model_id": mid})) == 0, "vorlaeufig wird nie gespeichert"
        z = welt.run(B.faellige_finalisieren(welt.db, model_ids=[mid]))
        assert z["erstellt"] == 1 and z["fehler"] == 0
        arten = welt.run(welt.db[K.BERICHTE].find({"model_id": mid}, {"_id": 0, "typ": 1, "periode_von": 1, "periode_bis": 1}).to_list(10))
        assert arten == [{"typ": "FIVE_DAY", "periode_von": _t(1), "periode_bis": _t(5)}]
        laufend = {(p["typ"], p["von"], p["bis"]) for p in B.laufende_perioden()}
        assert {("FIVE_DAY", _t(6), _t(10)), ("FIFTEEN_DAY", _t(1), _t(15)), ("MONTHLY", _t(1), _t(29))} <= laufend
        assert ("FIVE_DAY", _t(1), _t(5)) not in laufend and ("FIVE_DAY", _t(11), _t(15)) not in laufend, "noch nicht begonnen"
        liste = welt.run(B.modell_berichte(welt.db, mid))
        assert [(x["typ"], x["periode_von"]) for x in liste["final"]] == [("FIVE_DAY", _t(1))]
        assert ("FIVE_DAY", _t(6), _t(10)) in {(p["typ"], p["von"], p["bis"]) for p in liste["laufend"]}
        # der finale Bericht wird unveraendert ausgeliefert (kein Neurechnen)
        assert welt.run(B.modell_bericht(welt.db, mid, "FIVE_DAY", _t(1), _t(5)))["status"] == "FINAL"
    finally:
        _aufraeumen(welt)


def test_08_zweiter_lauf_idempotent_und_zwei_server(welt):
    mid = _start(welt)
    seg = _seg(welt)
    db = welt.db
    try:
        info = welt.run(db[K.BERICHTE].index_information())
        assert info["markt_bericht_je_periode"]["key"] == [("model_id", 1), ("typ", 1), ("periode_von", 1), ("periode_bis", 1)]
        assert info["markt_bericht_je_periode"].get("unique")
        for d in range(1, 16):
            _doc(welt, seg, _t(d), 20000)
        jetzt = _nach(_t(15))

        async def _zwei():
            return await asyncio.gather(B.finalisieren(db, mid, "FIFTEEN_DAY", _t(1), _t(15), jetzt=jetzt),
                                        B.finalisieren(db, mid, "FIFTEEN_DAY", _t(1), _t(15), jetzt=jetzt))
        assert sorted(welt.run(_zwei())) == ["erstellt", "vorhanden"]
        assert welt.run(db[K.BERICHTE].count_documents({"model_id": mid, "typ": "FIFTEEN_DAY"})) == 1
        vorher = welt.run(db[K.BERICHTE].count_documents({"model_id": mid}))
        z1 = welt.run(B.faellige_finalisieren(db, jetzt=jetzt, model_ids=[mid]))
        n1 = welt.run(db[K.BERICHTE].count_documents({"model_id": mid}))
        z2 = welt.run(B.faellige_finalisieren(db, jetzt=jetzt, model_ids=[mid]))
        assert z2["erstellt"] == 0 and welt.run(db[K.BERICHTE].count_documents({"model_id": mid})) == n1 == vorher + z1["erstellt"]
        assert welt.run(B.finalisieren(db, mid, "FIFTEEN_DAY", _t(1), _t(15), jetzt=jetzt)) == "vorhanden"
        f = _bericht(welt, mid, "FIFTEEN_DAY", _t(1), _t(15))
        assert [(x["von"], x["bis"]) for x in f["bloecke"]] == [(_t(1), _t(5)), (_t(6), _t(10)), (_t(11), _t(15))]
    finally:
        _aufraeumen(welt)


def test_09_finaler_bericht_bleibt_unveraendert(welt):
    """Abschnitt 36: ein Oktober-Bericht zeigt im Maerz dieselben Werte — spaetere Aenderungen an Tagesdaten
    (Korrektur, neuer Tag, Hot Deals) aendern den eingefrorenen Bericht nicht."""
    mid = _start(welt)
    seg = _seg(welt)
    db = welt.db
    try:
        for d in range(1, 30):
            _doc(welt, seg, _t(d), 20000 - d * 10)
        jetzt = _nach(_t(29))
        assert welt.run(B.finalisieren(db, mid, "MONTHLY", _t(1), _t(29), jetzt=jetzt)) == "erstellt"
        vorher = _bericht(welt, mid, "MONTHLY", _t(1), _t(29))
        welt.run(db[K.TAGESSTATS].update_one({"segment_id": seg["id"], "date": _t(5)}, {"$set": {"median_price": 5000.0, "hot_deal_ids_tag": [f"t{welt.w.s}z"]}}))
        welt.run(db[K.TAGESSTATS].delete_one({"segment_id": seg["id"], "date": _t(29)}))
        spaeter = jetzt + timedelta(days=150)
        assert welt.run(B.finalisieren(db, mid, "MONTHLY", _t(1), _t(29), jetzt=spaeter)) == "vorhanden"
        welt.run(B.faellige_finalisieren(db, jetzt=spaeter, model_ids=[mid]))
        assert _bericht(welt, mid, "MONTHLY", _t(1), _t(29)) == vorher
        gelesen = welt.run(B.modell_bericht(db, mid, "MONTHLY", _t(1), _t(29)))
        assert gelesen == vorher and gelesen["kennzahlen"]["endwert"] == 19710 and gelesen["kennzahlen"]["hot_deals"] == 0
        # zum Vergleich: live gerechnet saehe der Monat jetzt anders aus
        live = welt.run(B.bericht_berechnen(db, mid, "MONTHLY", _t(1), _t(29)))
        assert live["kennzahlen"]["endwert"] != 19710 and live["kennzahlen"]["hot_deals"] == 1
        # kein Codepfad aktualisiert einen Bericht
        q = inspect.getsource(B)
        for verboten in ("db[BERICHTE].update", "db[BERICHTE].replace", "db[BERICHTE].delete", "db[BERICHTE].find_one_and"):
            assert verboten not in q, verboten
    finally:
        _aufraeumen(welt)


def test_10_offene_hot_deal_auswertung_haelt_den_bericht_an(welt):
    mid = _start(welt)
    seg = _seg(welt)
    try:
        for d in range(1, 6):
            _doc(welt, seg, _t(d), 20000, offen=d == 3)
        assert welt.run(B.finalisieren(welt.db, mid, "FIVE_DAY", _t(1), _t(5), jetzt=_nach(_t(5)))) == "wartet_auf_hot_deals"
        z = welt.run(B.faellige_finalisieren(welt.db, jetzt=_nach(_t(5)), model_ids=[mid]))
        assert z["wartet"] >= 1 and _bericht(welt, mid, "FIVE_DAY", _t(1), _t(5)) is None
        assert welt.run(B.finalisieren(welt.db, mid, "FIVE_DAY", _t(1), _t(5), jetzt=_nach(_t(5), 25))) == "erstellt"
        assert any("ohne Hot-Deal-Auswertung" in h for h in _bericht(welt, mid, "FIVE_DAY", _t(1), _t(5))["hinweise"])
    finally:
        _aufraeumen(welt)


def test_11_keine_crawl_funktion_wird_aufgerufen(welt, monkeypatch):
    async def _verboten(*a, **k):
        raise AssertionError("Crawl-Funktion aufgerufen")
    for mod, name in ((APIFY, "lauf"), (JOBS, "job_sofort"), (JOBS, "tagesplan"), (JOBS, "einmal"), (JOBS, "verarbeiten_buendel"),
                      (SEG, "synchronisieren"), (ENT, "taeglich")):
        monkeypatch.setattr(mod, name, _verboten)
    mid = _start(welt)
    seg = _seg(welt)
    jetzt = datetime(2028, 2, 12, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(K, "jetzt", lambda: jetzt)
    merker_vorher = welt.run(welt.db[K.KONFIG].find_one({"_id": K.AUSWERTUNG_DOK}))
    try:
        for d in range(1, 12):
            _doc(welt, seg, _t(d), 20000)
        echt_d, echt_b = D.auswerten_faellige, B.faellige_finalisieren

        async def _d(db, **kw):
            return await echt_d(db, segment_ids=[seg["id"]], **{k: v for k, v in kw.items() if k != "segment_ids"})

        async def _b(db, **kw):
            return await echt_b(db, model_ids=[mid], **{k: v for k, v in kw.items() if k != "model_ids"})
        monkeypatch.setattr(D, "auswerten_faellige", _d)
        monkeypatch.setattr(B, "faellige_finalisieren", _b)
        erg = welt.run(AUS.durchlauf(welt.db))
        assert erg["berichte"]["erstellt"] == 2 and AUS.fehler_anzahl(erg) == 0          # 01.-05. und 06.-10.
        assert welt.run(B.modell_bericht(welt.db, mid, "FIVE_DAY", _t(11), _t(15)))["status"] == "VORLAEUFIG"
        assert welt.run(B.uebersicht(welt.db, "FIVE_DAY", _t(1), _t(5)))["anzahl"] >= 1
        welt.run(B.perioden_liste(welt.db, "FIVE_DAY"))
        assert len(welt.run(B.modell_berichte(welt.db, mid))["final"]) == 2
    finally:
        if merker_vorher:
            welt.run(welt.db[K.KONFIG].replace_one({"_id": K.AUSWERTUNG_DOK}, merker_vorher, upsert=True))
        else:
            welt.run(welt.db[K.KONFIG].delete_one({"_id": K.AUSWERTUNG_DOK}))
        _aufraeumen(welt)


def test_12_uebersicht_aller_modelle_perioden_und_routen(welt):
    """Abschnitt 37: Uebersicht aller Modelle einer Periode aus den eingefrorenen Berichten; Perioden-Liste; Routen
    lesen mit current_admin, einfrieren nur current_super_admin; Berichte kosten nie Abrufgeld."""
    mid = _start(welt)
    seg = _seg(welt)
    db = welt.db
    mid2 = f"{mid}-b"
    welt.run(db[K.MODELLE].insert_one({**_modell(welt.w), "id": mid2, "label": "BMW 320d B (Test)", "gearbox": "MANUAL_GEAR"}))
    seg2 = {**seg, "id": f"{mid2}:2020:20000-40000", "model_id": mid2}
    welt.run(db[K.SEGMENTE].insert_one(dict(seg2)))
    try:
        for d in range(1, 6):
            _doc(welt, seg, _t(d), 20000 - d * 100)
            _doc(welt, seg2, _t(d), 18000 + d * 100, hot=("h9",) if d == 2 else ())
        jetzt = _nach(_t(5))
        for m in (mid, mid2):
            assert welt.run(B.finalisieren(db, m, "FIVE_DAY", _t(1), _t(5), jetzt=jetzt)) == "erstellt"
        u = welt.run(B.uebersicht(db, "FIVE_DAY", _t(1), _t(5)))
        zeilen = {z["model_id"]: z for z in u["zeilen"]}
        assert zeilen[mid]["richtung"] == "FALLING" and zeilen[mid]["gearbox"] == "AUTOMATIC_GEAR" and zeilen[mid]["delta_eur"] == -400
        assert zeilen[mid2]["richtung"] == "RISING" and zeilen[mid2]["hot_deals"] == 1 and zeilen[mid2]["kosten_usd"] == 0.05
        assert set(zeilen[mid]) >= {"label", "fuel", "gearbox", "richtung", "delta_eur", "delta_pct", "listings", "preissenkungen", "preiserhoehungen",
                                    "hot_deals", "private_hot_deals", "liquiditaet", "data_quality", "health", "kosten_usd", "empty_segmente"}
        assert "tage" not in str(u["zeilen"][0].keys())
        pl = welt.run(B.perioden_liste(db, "FIVE_DAY"))
        treffer = [p for p in pl["final"] if (p["von"], p["bis"]) == (_t(1), _t(5))]
        assert treffer and treffer[0]["anzahl"] >= 2 and pl["karenz_stunden"] == 6
        r = (BACKEND / "routes" / "markt_admin.py").read_text(encoding="utf-8")
        for pfad in ('"/admin/market/reports/periods"', '"/admin/market/reports"', '"/admin/market/reports/model/{model_id}"',
                     '"/admin/market/reports/model/{model_id}/list"'):
            kopf = r.split(f"@router.get({pfad})")[1].split("\n\n")[0]
            assert "Depends(current_admin)" in kopf, pfad
        kopf = r.split('@router.post("/admin/market/reports/finalize")')[1].split("\n\n")[0]
        assert "current_super_admin" in kopf and "auswertung.durchlauf" in kopf
        assert "report" not in (BACKEND / "routes" / "markt.py").read_text(encoding="utf-8").lower()
        p = A.prognose_modell({"ez_years": [2021], "km_buckets": [{"min_km": 20000, "max_km": 40000}], "rows": 5, "crawls_per_day": 1})
        assert p["reporting_cost_usd"] == 0.0 and p["reporting_cost_monat_usd"] == 0.0
        assert K.BERICHTE == "market_model_reports" and "berichte.faellige_finalisieren" in inspect.getsource(AUS.durchlauf)
    finally:
        welt.run(db[K.MODELLE].delete_many({"id": mid2}))
        _aufraeumen(welt)


# ---------------------------------------------------------------- Pruefbefunde Phase E (27.09.2026)
def _rechnen(welt, segs, docs, typ="FIVE_DAY", von=None, bis=None, **kw):
    """Reine Rechnung ohne Datenbank (bericht_rechnen) mit Segment-Dokumenten aus _seg_daten."""
    modell = {"id": f"test-320d-{welt.w.s}", "label": "BMW 320d (Test)"}
    return B.bericht_rechnen(modell, {s["id"]: s for s in segs}, docs, typ, von or _t(1), bis or _t(5), **kw)


def test_13_technischer_ausfall_ist_teilabdeckung_kein_markteinbruch(welt):
    """Befund B3: EZ 2018 (12.000 EUR) und EZ 2023 (35.000 EUR), je 5 Inserate, alle Preise stabil. Faellt ein Segment
    an einem Tag technisch aus (ungueltiger Lauf oder gar kein Tagesdokument), ist der Tageswert ein anderer
    Segment-Mix — Teilabdeckung, kein Einbruch um 49 %: Minimum/Maximum/Median/Mittel der Periode und die
    Stichproben-Aenderung kommen nur aus Tagen mit vollem Korb. EMPTY (gueltig leer), neue und abgeschaltete Segmente
    bleiben Markt- bzw. Mix-Information."""
    a, b = _seg_daten(welt, "20000-40000"), _seg_daten(welt, "40001-60000")
    voll = 23500.0

    def reihe(tage_b, extra=()):
        docs = [_doc_daten(welt, a, _t(d), 12000) for d in range(1, 6)]
        return docs + [_doc_daten(welt, b, _t(d), 35000) for d in tage_b] + list(extra)
    for ausfall, docs in (("ungueltig", reihe((1, 2, 4, 5), [_ungueltig_daten(b, _t(3))])), ("kein Dokument", reihe((1, 2, 4, 5)))):
        ber = _rechnen(welt, [a, b], docs)
        tage = {r["date"]: r for r in ber["tage"]}
        assert tage[_t(3)]["teilabdeckung"] is True and tage[_t(3)]["fehlende_segmente"] == 1, ausfall
        assert tage[_t(3)]["median"] == 12000 and tage[_t(2)]["teilabdeckung"] is False and tage[_t(3)]["delta_vortag_eur"] == 0
        k = ber["kennzahlen"]
        assert k["minimum"] == {"date": _t(1), "wert": voll} and k["maximum"]["wert"] == voll, ausfall
        assert (k["median_periode"], k["mittelwert_periode"], k["sample_market_change_eur"], k["delta_eur"]) == (voll, voll, 0, 0), ausfall
        assert k["teilabgedeckte_tage"] == 1 and any("Teilabdeckung" in h for h in ber["hinweise"]), ausfall
        assert k["guenstigstes_angebot"] == {"date": _t(1), "preis": 12000 - 400}, "echtes Angebot zaehlt weiter"
    # Ausfall am letzten Tag: keine Stichproben-Aenderung, die es im Markt nicht gab
    k = _rechnen(welt, [a, b], reihe((1, 2, 3, 4)))["kennzahlen"]
    assert (k["sample_market_change_eur"], k["sample_market_change_pct"], k["minimum"]["wert"], k["teilabgedeckte_tage"]) == (0, 0, voll, 1)
    # EMPTY ist Marktinformation (gueltig leer) — kein technischer Ausfall
    k = _rechnen(welt, [a, b], reihe((1, 2, 4, 5), [_doc_daten(welt, b, _t(3), 0, n=0)]))["kennzahlen"]
    assert k["teilabgedeckte_tage"] == 0 and k["minimum"] == {"date": _t(3), "wert": 12000}
    # abgeschaltetes Segment (danach kein Lauf mehr) und neues Segment (vorher nie gueltig): Mix, keine Luecke
    k = _rechnen(welt, [a, {**b, "enabled": False}], reihe((1, 2, 3)))["kennzahlen"]
    assert k["teilabgedeckte_tage"] == 0 and k["sample_market_change_eur"] == 12000 - voll
    k = _rechnen(welt, [a, b], reihe((3, 4, 5)))["kennzahlen"]
    assert k["teilabgedeckte_tage"] == 0 and k["sample_market_change_eur"] == voll - 12000


def test_14_tagesbewegung_eur_und_prozent_aus_einem_aggregat():
    """Befund B4: A 10.000 -> 10.500 (+5 %), B 40.000 -> 39.000 (-2,5 %), je 5 Inserate. Getrennt gewichtet waere das
    -250 EUR bei +1,25 % ('steigend', aber in den Abwaertsbewegungen und als 'staerkster Rueckgang +1,25 %'). Aus
    demselben Aggregat: -250 EUR / -1 %, fallend, kein Anstieg."""
    gestern = {"A": {"median_price": 10000.0, "sample_size": 5}, "B": {"median_price": 40000.0, "sample_size": 5}}
    heute = {"A": {"median_price": 10500.0, "sample_size": 5}, "B": {"median_price": 39000.0, "sample_size": 5}}
    b = B.tagesbewegung(heute, gestern)
    assert b == {"eur": -250.0, "pct": -1.0, "segmente": 2}
    bw = B.bewegung_statistik(["t2"], {"t2": b})
    assert (bw["fallend"], bw["steigend"], bw["summe_negativ_eur"], bw["summe_positiv_eur"]) == (1, 0, -250.0, 0)
    assert bw["staerkster_rueckgang_pct"] == {"date": "t2", "eur": -250.0, "pct": -1.0} and bw["staerkster_anstieg_pct"] is None
    # Extreme in % nur aus Tagen mit passendem %-Vorzeichen
    bw = B.bewegung_statistik(["t2"], {"t2": {"eur": -250.0, "pct": 1.25, "segmente": 2}})
    assert bw["staerkster_rueckgang_pct"] is None and bw["staerkster_anstieg_pct"]["pct"] == 1.25


def test_15_confidence_beruecksichtigt_die_segmentabdeckung(welt):
    """Befund B5 (Abschnitte 52/53): 24 Segmente, nur eines liefert taeglich (GOOD, FULL, 12 Inserate), die uebrigen
    23 wurden nie abgerufen — das ist kein 'hoch' abgesicherter Modellwert: 5/120 Segment-Tage -> LOW mit Grund und
    Hinweis. Liefern alle 24, bleibt es HIGH."""
    segs = [_seg_daten(welt, f"{i * 10000}-{i * 10000 + 9999}") for i in range(24)]
    ber = _rechnen(welt, segs, [_doc_daten(welt, segs[0], _t(d), 20000, n=12) for d in range(1, 6)])
    k = ber["kennzahlen"]
    assert (k["coverage_days"], k["expected_days"], k["segmente_mit_daten"], k["segmente_gesamt"]) == (5, 5, 1, 24)
    assert (k["segment_tage_gueltig"], k["segment_tage_erwartet"]) == (5, 120)
    assert k["confidence"] == "LOW" and "Segmentabdeckung 5/120 Segment-Tage" in k["confidence_gruende"]
    assert any("5 / 120 gültige Segment-Tage" in h for h in ber["hinweise"])
    ber = _rechnen(welt, segs, [_doc_daten(welt, s, _t(d), 20000, n=12) for s in segs for d in range(1, 6)])
    k = ber["kennzahlen"]
    assert (k["segment_tage_gueltig"], k["segment_tage_erwartet"], k["confidence"], k["confidence_gruende"]) == (120, 120, "HIGH", [])
    assert not any("Segment-Tage" in h for h in ber["hinweise"])


def test_16_p25_p75_ueber_dieselbe_segmentmenge_wie_der_median():
    """Befund B6: Segment mit 3 Angeboten (12.000) und Segment mit 5 (35.000, P25 34.000) — ueber verschiedene Mengen
    laege die 'untere Preiszone' bei 34.000 ueber dem Median 26.375. Dann keine Zonen (Luecke)."""
    duenn = {"median_price": 12000.0, "p25_price": 11800.0, "p75_price": 12200.0, "min_price": 11800.0, "sample_size": 3}
    voll = {"median_price": 35000.0, "p25_price": 34000.0, "p75_price": 36000.0, "min_price": 33500.0, "sample_size": 5}
    nv = B.niveau([duenn, voll])
    assert (nv["median"], nv["p25"], nv["p75"], nv["min"]) == (26375.0, None, None, 11800.0)
    nv = B.niveau([{**duenn, "sample_size": 4}, voll])
    assert nv["p25"] is not None and nv["p25"] <= nv["median"] <= nv["p75"]


def test_17_kosteneffizienz_zaehler_und_nenner_auf_derselben_basis(welt):
    """Befund B7 (Abschnitt 38): Fassung 1 an den Tagen 1-25, Fassung 2 an 26-29, je 0,10 $. kosten_usd bleibt die
    reale Ausgabe (2,90 $), die Kennzahlen je Einheit rechnen mit den Kosten der gerechneten Fassung (0,40 $ / 4
    Beobachtungen = 0,10 $ statt 0,725 $). Gueltige EMPTY-Tage sind gueltige Beobachtungen (deals.ist_gueltig)."""
    alt, neu = _seg_daten(welt, enabled=False), _seg_daten(welt, version=2, dh="h2", praefix="v2:")
    docs = [_doc_daten(welt, alt, _t(d), 20000, kosten=0.10) for d in range(1, 26)]
    docs += [_doc_daten(welt, neu, _t(d), 25000, kosten=0.10) for d in range(26, 30)]
    k = _rechnen(welt, [alt, neu], docs, "MONTHLY", _t(1), _t(29))["kennzahlen"]
    assert (k["kosten_usd"], k["kosten_fassung_usd"], k["gueltige_beobachtungen"]) == (2.9, 0.4, 4)
    assert (k["cost_per_valid_observation"], k["cost_per_unique_listing"]) == (0.1, 0.08)
    a, leer = _seg_daten(welt, "20000-40000"), _seg_daten(welt, "40001-60000")
    docs = [_doc_daten(welt, a, _t(d), 20000, kosten=0.10) for d in range(1, 6)]
    docs += [_doc_daten(welt, leer, _t(d), 0, n=0, kosten=0.10) for d in range(1, 6)]
    k = _rechnen(welt, [a, leer], docs)["kennzahlen"]
    assert (k["gueltige_beobachtungen"], k["kosten_usd"], k["cost_per_valid_observation"]) == (10, 1.0, 0.1)
    k = _rechnen(welt, [leer], [_doc_daten(welt, leer, _t(d), 0, n=0, kosten=0.10) for d in range(1, 6)])["kennzahlen"]
    assert (k["gueltige_beobachtungen"], k["cost_per_valid_observation"]) == (5, 0.1), "nur leere Tage: trotzdem Kosten je Beobachtung"


def test_18_berichtsrechnung_blockiert_den_event_loop_nicht(welt, monkeypatch):
    """Befund B9 (Abschnitt 49): ein Monatsbericht mit 180 Segmenten (15 EZ x 12 km, Grenze der Suchauftraege) braucht
    ueber eine Sekunde CPU. Die reine Rechnung laeuft im Hilfsthread — der Event-Loop des Web-Prozesses (Vergleich,
    Vertrag, Versand) steht dabei nie laenger als einen Bruchteil davon."""
    import time
    segs = [_seg_daten(welt, f"{i * 1000}-{i * 1000 + 999}") for i in range(180)]
    von, bis = _t(1), _t(29)
    # Pruefung Runde 3: Budget-Rotation wie im Betrieb (jeder 4. Segment-Tag nicht geplant, ~75 % je Tag) — die Rechnung
    # mit getragenen Werten der nicht geplanten Segmente laeuft ebenfalls im Hilfsthread
    geplant = {s["id"]: {B._plus(von, d) for d in range(-B.VORLAUF_TAGE, 29) if (i + d) % 4} for i, s in enumerate(segs)}
    docs = [_doc_daten(welt, s, B._plus(von, d), 20000 + (d % 7) * 50, red=("a",) if d % 3 == 0 else ())
            for i, s in enumerate(segs) for d in range(-B.VORLAUF_TAGE, 29) if (i + d) % 4]
    modell = {"id": f"test-320d-{welt.w.s}"}

    async def _laden(db, model_id, v, b):
        return modell, {s["id"]: s for s in segs}, docs, []

    async def _geplant_laden(db, seg_ids, v, b):
        assert sorted(seg_ids) == sorted(geplant) and (v, b) == (von, bis)
        return geplant
    monkeypatch.setattr(B, "_laden", _laden)
    monkeypatch.setattr(B, "_geplant_laden", _geplant_laden)
    im_loop = []
    echt = B.bericht_rechnen

    def _pruefen(*a, **kw):
        try:
            asyncio.get_running_loop()
            im_loop.append(True)
        except RuntimeError:
            im_loop.append(False)
        return echt(*a, **kw)
    monkeypatch.setattr(B, "bericht_rechnen", _pruefen)

    async def _messen():
        luecken, fertig = [0.0], asyncio.Event()

        async def puls():
            letzte = time.perf_counter()
            while not fertig.is_set():
                await asyncio.sleep(0.001)
                jetzt = time.perf_counter()
                luecken.append(jetzt - letzte)
                letzte = jetzt
        p = asyncio.ensure_future(puls())
        await asyncio.sleep(0.02)
        t0 = time.perf_counter()
        ber = await B.bericht_berechnen(None, modell["id"], "MONTHLY", von, bis)
        dauer = time.perf_counter() - t0
        fertig.set()
        await p
        return ber, dauer, max(luecken)
    ber, dauer, luecke = welt.run(_messen())
    assert ber and ber["kennzahlen"]["segmente_gesamt"] == 180 and len(ber["tage"]) == 29
    k = ber["kennzahlen"]
    assert k["planung"] == "jobs" and k["getragene_segment_tage"] == 29 * 45 and k["niveau_tage"] == 29 and k["teilabgedeckte_tage"] == 0
    assert im_loop == [False], "die reine Rechnung laeuft im Hilfsthread, nie im Event-Loop"
    assert luecke < 0.25, f"Event-Loop {luecke:.3f} s am Stueck blockiert (Rechnung {dauer:.3f} s)"


def test_19_periodenliste_liest_keine_berichtsdokumente(welt):
    """Befunde B12/B13: eingefrorene Berichte werden nie geloescht; die Periodenliste (jedes Oeffnen der Seite
    'Berichte', jeder Typwechsel) gruppiert deshalb als abgedeckter Index-Scan ueber markt_bericht_periode — mit und
    ohne Typ wird kein einziges Berichtsdokument gelesen (Profiler: docsExamined 0)."""
    mid = _start(welt)
    seg = _seg(welt)
    db = welt.db
    try:
        for d in range(1, 6):
            _doc(welt, seg, _t(d), 20000)
        assert welt.run(B.finalisieren(db, mid, "FIVE_DAY", _t(1), _t(5), jetzt=_nach(_t(5)))) == "erstellt"
        welt.run(db.command("profile", 0))
        welt.run(db["system.profile"].drop())
        welt.run(db.command("profile", 2))
        try:
            mit_typ = welt.run(B.perioden_liste(db, "FIVE_DAY"))
            ohne_typ = welt.run(B.perioden_liste(db))
        finally:
            welt.run(db.command("profile", 0))
        ops = welt.run(db["system.profile"].find({"ns": f"{db.name}.{K.BERICHTE}", "command.aggregate": K.BERICHTE}).to_list(None))
        welt.run(db["system.profile"].drop())
        assert len(ops) == 2
        for op in ops:
            assert op.get("docsExamined") == 0 and op.get("keysExamined", 0) >= 1, op.get("planSummary")
        for pl in (mit_typ, ohne_typ):
            treffer = [p for p in pl["final"] if (p["typ"], p["von"], p["bis"]) == ("FIVE_DAY", _t(1), _t(5))]
            assert treffer and treffer[0]["anzahl"] >= 1
    finally:
        _aufraeumen(welt)


def test_20_vorlaeufiger_bericht_zaehlt_kuenftige_tage_nicht_als_luecke(welt, monkeypatch):
    """Befund B17 (Abschnitte 52/53): am 03.02. zeigt der vorlaeufige Monatsbericht 3/3 statt 3/29 Tage — kuenftige
    Tage und Bloecke sind 'offen', keine Datenluecke, und senken die Confidence nicht. Heute ohne Lauf ist ebenfalls
    offen; ein echter fehlender Tag vor heute bleibt eine Luecke; finale Berichte zaehlen jeden Kalendertag."""
    seg = _seg_daten(welt)
    docs = [_doc_daten(welt, seg, _t(d), 20000 - d * 10) for d in range(1, 4)]
    ber = _rechnen(welt, [seg], docs, "MONTHLY", _t(1), _t(29), heute=_t(3))
    k = ber["kennzahlen"]
    assert (k["coverage_days"], k["expected_days"]) == (3, 3) and not any("Abdeckung" in g for g in k["confidence_gruende"])
    assert ber["offen_ab"] == _t(4) and not any("gültige Tage" in h for h in ber["hinweise"])
    tage = {r["date"]: r for r in ber["tage"]}
    assert tage[_t(3)]["offen"] is False and tage[_t(4)]["offen"] is True and tage[_t(29)]["offen"] is True
    assert [x["offen"] for x in ber["bloecke"]] == [False, True, True, True, True, True]
    assert ber["bloecke"][0]["expected_days"] == 3 and ber["segmente"][0]["erwartete_tage"] == 3
    k = _rechnen(welt, [seg], docs, "MONTHLY", _t(1), _t(29), heute=_t(4))["kennzahlen"]
    assert (k["coverage_days"], k["expected_days"]) == (3, 3), "heute noch ohne Lauf"
    k = _rechnen(welt, [seg], docs[:1] + docs[2:], "MONTHLY", _t(1), _t(29), heute=_t(3))["kennzahlen"]
    assert (k["coverage_days"], k["expected_days"]) == (2, 3), "echte Luecke vor heute"
    final = _rechnen(welt, [seg], docs, "MONTHLY", _t(1), _t(29))
    assert final["kennzahlen"]["expected_days"] == 29 and "offen_ab" not in final and not any(r["offen"] for r in final["tage"])
    # ueber modell_bericht: laufende Periode mit dem deutschen Kalendertag
    mid = _start(welt)
    welt.run(welt.db[K.SEGMENTE].insert_one(dict(seg)))
    try:
        for d in range(1, 4):
            _doc(welt, seg, _t(d), 20000 - d * 10)
        monkeypatch.setattr(K, "jetzt", lambda: datetime(2028, 2, 3, 22, 30, tzinfo=timezone.utc))      # 23:30 deutscher Zeit
        vorl = welt.run(B.modell_bericht(welt.db, mid, "MONTHLY", _t(1), _t(29)))
        assert vorl["status"] == "VORLAEUFIG" and vorl["offen_ab"] == _t(4)
        assert (vorl["kennzahlen"]["coverage_days"], vorl["kennzahlen"]["expected_days"]) == (3, 3)
        assert welt.run(welt.db[K.BERICHTE].count_documents({"model_id": mid})) == 0
    finally:
        _aufraeumen(welt)


# ---------------------------------------------------------------- Pruefung Runde 2 (27.09.2026)
def _korb_monat(welt, anzahl, seed):
    """anzahl Segmente mit sehr verschiedenem Preisniveau (12.000-35.000 EUR, je 5 Inserate). Alle Preise fallen
    gleichmaessig um 0,1 % je Tag: das wahre Niveau faellt streng, Maximum am 01., Minimum am 29. Je Segment-Tag 2 %
    technische Ausfaelle (Haelfte ohne Tagesdokument, Haelfte nur ungueltig), auch im Vorlauf (drei Tage wie im
    Betrieb) — deterministisch per Seed."""
    rnd = random.Random(seed)
    segs = [_seg_daten(welt, f"{i * 1000}-{i * 1000 + 999}") for i in range(anzahl)]
    preis = {s["id"]: 12000 + 23000 * i / (anzahl - 1) for i, s in enumerate(segs)}
    docs = []
    for d in range(-3, 29):
        tag, faktor = B._plus(_t(1), d), 1 - 0.001 * d
        for s in segs:
            if rnd.random() < 0.02:
                if rnd.random() < 0.5:
                    docs.append(_ungueltig_daten(s, tag))
                continue
            docs.append(_doc_daten(welt, s, tag, preis[s["id"]] * faktor))
    mittel = sum(preis.values()) / anzahl
    return segs, docs, {_t(d): mittel * (1 - 0.001 * (d - 1)) for d in range(1, 30)}


def test_21_teilabdeckung_verwirft_keine_tage_und_bringt_keinen_mix_effekt(welt):
    """Pruefung Runde 2 #0 (Befund B3): 'Teilabdeckung zaehlt nicht' skaliert nicht — bei 2 % Ausfall je Segment-Tag
    sind bei 24 Segmenten ~38 %, bei 180 Segmenten ~97 % der Tage teilabgedeckt; Minimum/Maximum/Stichproben-Aenderung
    kaemen aus 0-2 Tagen, die Diagrammlinie waere fast nur Luecke (und eingefroren bleibt das so). Der verkettete
    Korbwert nutzt jeden Tag mit Daten, ohne den Mix-Effekt zurueckzuholen: Minimum/Maximum/Median/Mittel treffen das
    wahre Niveau (24 Segmente auf 1 EUR; 180 Segmente — ggf. ohne vollstaendigen Tag als Anker — auf 0,5 %) und die
    richtigen Tage, die Korbwerte fallen Tag fuer Tag wie der Markt."""
    for anzahl, toleranz in ((24, 1.0), (180, None)):
        segs, docs, wahr = _korb_monat(welt, anzahl, 20260927)
        ber = _rechnen(welt, segs, docs, "MONTHLY", _t(1), _t(29))
        k, tage = ber["kennzahlen"], ber["tage"]
        assert k["teilabgedeckte_tage"] >= (8 if anzahl == 24 else 25), (anzahl, k["teilabgedeckte_tage"])
        assert (k["maximum"] or {}).get("date") == _t(1) and (k["minimum"] or {}).get("date") == _t(29), (anzahl, k["maximum"], k["minimum"])
        grenze = toleranz or wahr[_t(15)] * 0.005
        for ist, soll in ((k["maximum"]["wert"], wahr[_t(1)]), (k["minimum"]["wert"], wahr[_t(29)]),
                          (k["median_periode"], wahr[_t(15)]), (k["mittelwert_periode"], statistics.mean(wahr.values()))):
            assert abs(ist - soll) <= grenze, (anzahl, ist, soll)
        assert abs(k["sample_market_change_pct"] - (-2.8)) <= 0.01, (anzahl, k["sample_market_change_pct"])
        assert k["niveau_tage"] == 29 and all(r["median_korb"] is not None for r in tage), anzahl
        korb = [r["median_korb"] for r in tage]
        assert all(b < a for a, b in zip(korb, korb[1:])), (anzahl, "kein Scheineinbruch/-anstieg an Ausfalltagen")
        assert any(r["teilabdeckung"] and abs(r["median"] - r["median_korb"]) > 5 for r in tage), "roher Tageswert bleibt daneben sichtbar"
        assert any("verkettet" in h for h in ber["hinweise"]), anzahl


def test_22_vorlaeufig_heute_zaehlen_nur_gelaufene_segmente(welt):
    """Pruefung Runde 2 #1 (Befunde B5/B17): die Laeufe verteilen sich ueber den Tag. Am 03. um 08:00 sind erst 3 von
    24 Segmenten gelaufen — die 21 ausstehenden sind weder fehlende Segment-Tage (51/51 statt 51/72, Confidence
    bleibt HIGH, kein Hinweis) noch Teilabdeckung, und der Korbwert von heute ist nicht der Mix der drei guenstigsten
    Segmente. Ein heute schon gescheiterter Lauf zaehlt dagegen als fehlend. Am 01. um 08:00 (3 von 24) gibt es
    3/3 Segment-Tage und noch keinen Periodenwert aus dem Mix der ersten drei."""
    segs = [_seg_daten(welt, f"{i * 10000}-{i * 10000 + 9999}") for i in range(24)]
    preis = {s["id"]: 12000.0 + 1000 * i for i, s in enumerate(segs)}
    voll = sum(preis.values()) / 24                                            # 23.500
    vormonat = [_doc_daten(welt, s, "2028-01-31", preis[s["id"]], n=12) for s in segs]
    docs = vormonat + [_doc_daten(welt, s, _t(d), preis[s["id"]], n=12) for s in segs for d in (1, 2)]
    frueh = [_doc_daten(welt, s, _t(3), preis[s["id"]], n=12) for s in segs[:3]]
    ber = _rechnen(welt, segs, docs + frueh, "MONTHLY", _t(1), _t(29), heute=_t(3))
    k = ber["kennzahlen"]
    assert (k["segment_tage_gueltig"], k["segment_tage_erwartet"]) == (51, 51) and k["segment_luecken"] == 0
    assert (k["confidence"], k["confidence_gruende"], k["teilabgedeckte_tage"]) == ("HIGH", [], 0)
    assert not any("Segment-Tage" in h or "Teilabdeckung" in h for h in ber["hinweise"]), ber["hinweise"]
    tag3 = next(r for r in ber["tage"] if r["date"] == _t(3))
    assert (tag3["teilabdeckung"], tag3["fehlende_segmente"], tag3["ausstehende_segmente"]) == (False, 0, 21)
    assert tag3["median"] == 13000 and tag3["median_korb"] == voll, "roher Tageswert = Mix der ersten drei, Korbwert nicht"
    assert (k["minimum"]["wert"], k["maximum"]["wert"], k["sample_market_change_eur"], k["niveau_tage"]) == (voll, voll, 0, 3)
    assert ber["segmente"][0]["erwartete_tage"] == 3 and ber["segmente"][5]["erwartete_tage"] == 2
    # ein heute schon gescheiterter Lauf ist ein echter Ausfall (Teilabdeckung, fehlender Segment-Tag)
    k = _rechnen(welt, segs, docs + frueh + [_ungueltig_daten(segs[5], _t(3))], "MONTHLY", _t(1), _t(29), heute=_t(3))["kennzahlen"]
    assert (k["segment_tage_gueltig"], k["segment_tage_erwartet"], k["teilabgedeckte_tage"], k["segment_luecken"]) == (51, 52, 1, 1)
    # 01. um 08:00: Monat und neuer 5-Tage-Block
    erst = [_doc_daten(welt, s, _t(1), preis[s["id"]], n=12) for s in segs[:3]]
    for typ, bis in (("MONTHLY", _t(29)), ("FIVE_DAY", _t(5))):
        ber = _rechnen(welt, segs, vormonat + erst, typ, _t(1), bis, heute=_t(1))
        k = ber["kennzahlen"]
        assert (k["segment_tage_gueltig"], k["segment_tage_erwartet"], k["confidence"]) == (3, 3, "HIGH"), typ
        assert (k["minimum"], k["median_periode"], k["niveau_tage"]) == (None, None, 0), typ
        assert ber["tage"][0]["median_korb"] is None and ber["tage"][0]["ausstehende_segmente"] == 21, typ
        assert not any("Segment-Tage" in h for h in ber["hinweise"]), typ


def test_23_zu_und_abschalten_ist_keine_datenluecke(welt):
    """Pruefung Runde 2 #2 (Befund B5): April (30 Tage), 24 Segmente liefen schon im Maerz. Am 10. werden 12 davon
    abgeschaltet (letzter Lauf am 09.), die uebrigen liefern jeden Tag — eine Einstellung, keine Datenluecke: 468/468
    Segment-Tage statt 468/720 (65 % -> MEDIUM 'Segmentabdeckung'), Confidence HIGH, kein Hinweis. Zugeschaltet:
    ein im Zeitraum angelegtes Segment (created_at 15.04. 01:30 deutscher Zeit; die ersten Laeufe scheitern ohne
    Dokument, erster Lauf 18.04.) wird ab Anlage erwartet — die drei gescheiterten Tage bleiben Luecke; ein wieder
    eingeschaltetes (alt angelegt, ohne Vorlauf) ab seinem ersten Lauf; ein angelegtes, nie gelaufenes ab Anlage."""
    def _a(d):
        return _t(d, 4)
    segs = [_seg_daten(welt, f"{i * 10000}-{i * 10000 + 9999}") for i in range(24)]
    for s in segs[12:]:
        s["enabled"] = False
    docs = [_doc_daten(welt, s, "2028-03-31", 20000, n=12) for s in segs]
    docs += [_doc_daten(welt, s, _a(d), 20000, n=12) for s in segs for d in range(1, 31) if s["enabled"] or d < 10]
    ber = _rechnen(welt, segs, docs, "MONTHLY", _a(1), _a(30))
    k = ber["kennzahlen"]
    assert (k["segment_tage_gueltig"], k["segment_tage_erwartet"]) == (468, 468) and k["segment_luecken"] == 0
    assert (k["confidence"], k["confidence_gruende"], k["segmente_gesamt"], k["teilabgedeckte_tage"]) == ("HIGH", [], 24, 0)
    assert not any("Segment-Tage" in h for h in ber["hinweise"])
    detail = {x["segment_id"]: x for x in ber["segmente"]}
    assert (detail[segs[12]["id"]]["gueltige_tage"], detail[segs[12]["id"]]["erwartete_tage"]) == (9, 9)
    assert (detail[segs[0]["id"]]["gueltige_tage"], detail[segs[0]["id"]]["erwartete_tage"]) == (30, 30)
    # zugeschaltet
    neu = {**_seg_daten(welt, "900000-909999"), "created_at": "2028-04-14T23:30:00+00:00"}
    wieder = {**_seg_daten(welt, "910000-919999"), "created_at": "2027-06-01T08:00:00+00:00"}
    zusatz = [_doc_daten(welt, neu, _a(d), 20000, n=12) for d in range(18, 31)]
    zusatz += [_doc_daten(welt, wieder, _a(d), 20000, n=12) for d in range(20, 31)]
    ber = _rechnen(welt, segs + [neu, wieder], docs + zusatz, "MONTHLY", _a(1), _a(30))
    k = ber["kennzahlen"]
    assert (k["segment_tage_gueltig"], k["segment_tage_erwartet"]) == (468 + 13 + 11, 468 + 16 + 11) and k["segment_luecken"] == 3
    assert k["confidence"] == "HIGH" and any(f"{468 + 13 + 11} / {468 + 16 + 11} gültige Segment-Tage" in h for h in ber["hinweise"])
    detail = {x["segment_id"]: x for x in ber["segmente"]}
    assert (detail[neu["id"]]["erwartete_tage"], detail[wieder["id"]]["erwartete_tage"]) == (16, 11)
    nie = {**_seg_daten(welt, "920000-929999"), "created_at": "2028-04-26T08:00:00+00:00"}
    k = _rechnen(welt, segs + [nie], docs, "MONTHLY", _a(1), _a(30))["kennzahlen"]
    assert (k["segment_tage_gueltig"], k["segment_tage_erwartet"], k["segmente_gesamt"]) == (468, 468 + 5, 25)
    assert B._tag_aus_zeit("2028-04-14T23:30:00+00:00") == _a(15) and B._tag_aus_zeit("kaputt") is None


def test_24_schema_im_bericht_und_in_den_listen(welt):
    """Pruefung Runde 2 #3 / Runde 3: die Bedeutung mehrerer gespeicherter Kennzahlen hat sich geaendert (B3-B7, Runde 2;
    Abdeckung und Korbwert in Runde 3) — SCHEMA 3 steht in jedem neu eingefrorenen Bericht, in der Uebersichtszeile und
    in der Berichtsliste des Modells; frueher eingefrorene Berichte behalten Schema 1 bzw. 2 (nie veraendert) und sind
    so unterscheidbar."""
    mid = _start(welt)
    seg = _seg(welt)
    db = welt.db
    try:
        for d in range(1, 16):
            _doc(welt, seg, _t(d), 20000)
        assert B.SCHEMA == 3
        assert welt.run(B.finalisieren(db, mid, "FIVE_DAY", _t(1), _t(5), jetzt=_nach(_t(5)))) == "erstellt"
        neu = _bericht(welt, mid, "FIVE_DAY", _t(1), _t(5))
        assert neu["schema"] == 3
        for von, bis, schema in ((_t(6), _t(10), 1), (_t(11), _t(15), 2)):
            welt.run(db[K.BERICHTE].insert_one({**neu, "id": f"test-alt-{schema}", "periode_von": von, "periode_bis": bis, "schema": schema}))
            assert welt.run(B.finalisieren(db, mid, "FIVE_DAY", von, bis, jetzt=_nach(bis))) == "vorhanden"
        for von, bis, schema in ((_t(1), _t(5), 3), (_t(6), _t(10), 1), (_t(11), _t(15), 2)):
            zeile = next(z for z in welt.run(B.uebersicht(db, "FIVE_DAY", von, bis))["zeilen"] if z["model_id"] == mid)
            assert zeile["schema"] == schema
            assert welt.run(B.modell_bericht(db, mid, "FIVE_DAY", von, bis))["schema"] == schema
        liste = welt.run(B.modell_berichte(db, mid))["final"]
        assert sorted((x["periode_von"], x["schema"]) for x in liste) == [(_t(1), 3), (_t(6), 1), (_t(11), 2)]
    finally:
        _aufraeumen(welt)


# ---------------------------------------------------------------- Pruefung Runde 3 (27.09.2026)
def _rotation_korb(welt):
    """24 Segmente, 12.000-35.000 EUR (wahres Niveau 23.500), je 5 Inserate — wie im Befund nachgerechnet."""
    segs = [_seg_daten(welt, f"{i * 10000}-{i * 10000 + 9999}") for i in range(24)]
    preis = {s["id"]: 12000 + 23000 * i / 23 for i, s in enumerate(segs)}
    return segs, preis


def _rotation(welt, segs, preis, geplant_fn, faktor=lambda k: 1.0):
    """Tage k = -3..28 (01. = k 0, drei Vorlauftage): Tagesdokument nur, wenn geplant_fn(i, k) — sonst kein Lauf.
    Liefert (docs, Jobs wie die Rotation sie plant: Segment -> Tage)."""
    docs, geplant = [], {s["id"]: set() for s in segs}
    for k in range(-3, 29):
        tag = B._plus(_t(1), k)
        for i, s in enumerate(segs):
            if geplant_fn(i, k):
                geplant[s["id"]].add(tag)
                docs.append(_doc_daten(welt, s, tag, preis[s["id"]] * faktor(k)))
    return docs, geplant


def test_25_runde3_rotation_ist_keine_luecke_und_kein_teilkorb_niveau(welt):
    """Pruefung Runde 3 #0 (mittel): bei knappem Budget plant der Tagesplan nur einen Teil der Segmente je Tag
    (rotierend) — ein Segment ohne Abruf-Job ist NICHT GEPLANT, weder Luecke noch technischer Ausfall. Beide
    Rechenbeispiele des Befunds (24 Segmente, wahres Niveau 23.500 EUR):
      (a) die teure und die guenstige Haelfte laufen abwechselnd (keine gemeinsamen Segmente an Nachbartagen) — vorher
          Median = Mittel = Min = Max = 29.500 EUR allein aus dem 01.
      (b) 12 Segmente taeglich, 6 an geraden und 6 an ungeraden Tagen ausgelassen, Preise -0,1 % je Tag — vorher alle
          Periodenwerte ~20.500 statt 23.500 EUR (-12,8 %).
    Mit den Jobs der Rotation: plausible Werte nahe dem wahren Niveau (nicht geplante Segmente mit ihrem letzten
    geplanten Wert), keine Teilabdeckung, alle Segment-Tage abgedeckt. Ohne Jobs bzw. mit Jobs fuer jedes Segment an
    jedem Tag (dann sind die fehlenden technische Ausfaelle): kein Anker mit Mindestabdeckung -> ehrliche Luecke."""
    segs, preis = _rotation_korb(welt)
    wahr = sum(preis.values()) / 24
    alle = {s["id"]: {B._plus(_t(1), k) for k in range(-3, 29)} for s in segs}
    # (a) abwechselnde Haelften: ungerader Kalendertag (k gerade) die teure Haelfte, gerader die guenstige
    docs, geplant = _rotation(welt, segs, preis, lambda i, k: (i >= 12) == (k % 2 == 0))
    ber = _rechnen(welt, segs, docs, "MONTHLY", _t(1), _t(29), geplant=geplant)
    k = ber["kennzahlen"]
    for wert in (k["median_periode"], k["mittelwert_periode"], k["minimum"]["wert"], k["maximum"]["wert"]):
        assert abs(wert - wahr) <= 1, (wert, wahr)
    assert abs(k["sample_market_change_eur"]) <= 1 and k["niveau_tage"] == 29 and k["anker_tage"] == 29
    assert (k["teilabgedeckte_tage"], k["segment_luecken"], k["segment_tage_gueltig"], k["segment_tage_erwartet"]) == (0, 0, 29 * 12, 29 * 12)
    assert (k["expected_days"], k["coverage_days"], k["nicht_geplante_segment_tage"], k["getragene_segment_tage"]) == (29, 29, 29 * 12, 29 * 12)
    assert not any("Segmentabdeckung" in g or "Abdeckung" in g for g in k["confidence_gruende"]), k["confidence_gruende"]
    assert k["planung"] == "jobs" and any("Budget-Rotation" in h for h in ber["hinweise"]), ber["hinweise"]
    tag1 = ber["tage"][0]
    assert tag1["median"] == 29500 and abs(tag1["median_korb"] - wahr) <= 1, "roher Tageswert bleibt die gelaufene Haelfte"
    assert (tag1["nicht_geplante_segmente"], tag1["teilabdeckung"], tag1["korb_abdeckung_pct"], tag1["anker"]) == (12, False, 100.0, True)
    for plan, name in ((None, "ohne Jobs"), (alle, "Jobs fuer alle: technischer Ausfall")):
        ber = _rechnen(welt, segs, docs, "MONTHLY", _t(1), _t(29), geplant=plan)
        k = ber["kennzahlen"]
        assert (k["median_periode"], k["mittelwert_periode"], k["minimum"], k["maximum"]) == (None, None, None, None), name
        assert (k["sample_market_change_eur"], k["niveau_tage"], k["anker_tage"]) == (None, 0, 0), name
        assert k["teilabgedeckte_tage"] == 29 and all(r["median_korb"] is None for r in ber["tage"]), name
        assert any("Kein Tag mit ausreichender Korbabdeckung" in h for h in ber["hinweise"]), name
        assert k["guenstigstes_angebot"] is not None and k["startwert"] is not None, "echte Einzelwerte bleiben"
    # (b) 12 taeglich, 6 an geraden, 6 an ungeraden Kalendertagen ausgelassen; Preise -0,1 % je Tag
    def plan_b(i, k):
        return i < 12 or (12 <= i < 18 and k % 2 == 0) or (i >= 18 and k % 2 == 1)
    docs, geplant = _rotation(welt, segs, preis, plan_b, faktor=lambda k: 1 - 0.001 * k)
    soll = {_t(d): wahr * (1 - 0.001 * (d - 1)) for d in range(1, 30)}
    ber = _rechnen(welt, segs, docs, "MONTHLY", _t(1), _t(29), geplant=geplant)
    k = ber["kennzahlen"]
    grenze = wahr * 0.001                        # getragene Werte sind hoechstens einen Tag alt: < 0,1 %
    assert k["maximum"]["date"] == _t(1) and k["minimum"]["date"] == _t(29), (k["maximum"], k["minimum"])
    for ist, s in ((k["maximum"]["wert"], soll[_t(1)]), (k["minimum"]["wert"], soll[_t(29)]), (k["median_periode"], soll[_t(15)]),
                   (k["mittelwert_periode"], statistics.mean(soll.values()))):
        assert abs(ist - s) <= grenze, (ist, s)
    korb = [r["median_korb"] for r in ber["tage"]]
    assert all(b < a for a, b in zip(korb, korb[1:])), "faellt Tag fuer Tag wie der Markt"
    assert (k["teilabgedeckte_tage"], k["segment_luecken"], k["niveau_tage"]) == (0, 0, 29)
    for plan, name in ((None, "ohne Jobs"), (alle, "Jobs fuer alle: technischer Ausfall")):
        k = _rechnen(welt, segs, docs, "MONTHLY", _t(1), _t(29), geplant=plan)["kennzahlen"]
        assert (k["median_periode"], k["minimum"], k["maximum"], k["niveau_tage"]) == (None, None, None, 0), name
        assert k["teilabgedeckte_tage"] == 29, name


def test_26_runde3_anker_nur_mit_mindestabdeckung(welt):
    """Pruefung Runde 3 #0: gibt es keinen vollstaendigen Tag, wird der am besten abgedeckte Tag nur Anker, wenn ihm
    hoechstens 5 % des Korbgewichts technisch fehlen (ANKER_MIN_ABDECKUNG). 40 Segmente: fehlt je Tag eines (2,5 %),
    stehen Periodenwerte (Fehler hoechstens der Mix des einen Segments); fehlen je Tag drei (7,5 %), bleiben sie leer
    — vorher wurde der Tag mit den wenigsten fehlenden Segmenten Anker, egal wie viele fehlten."""
    assert B.ANKER_MIN_ABDECKUNG == 0.95 and B.TRAGEN_MAX_TAGE == B.VORLAUF_TAGE
    segs = [_seg_daten(welt, f"{i * 10000}-{i * 10000 + 9999}") for i in range(40)]
    preis = {s["id"]: 15000 + 250 * i for i, s in enumerate(segs)}
    wahr = sum(preis.values()) / 40

    def docs_mit(ausfall):
        docs = [_doc_daten(welt, s, "2028-01-31", preis[s["id"]]) for s in segs]
        for d in range(1, 6):
            weg = {(d * 7 + j * 13) % 40 for j in range(ausfall)}
            docs += [_ungueltig_daten(s, _t(d)) if i in weg else _doc_daten(welt, s, _t(d), preis[s["id"]]) for i, s in enumerate(segs)]
        return docs
    ber = _rechnen(welt, segs, docs_mit(1))
    k = ber["kennzahlen"]
    assert (k["anker_tage"], k["anker_abdeckung_pct"], k["niveau_tage"], k["teilabgedeckte_tage"]) == (1, 97.5, 5, 5)
    assert abs(k["median_periode"] - wahr) <= wahr * 0.025 * 0.5 and not any("Korbabdeckung" in h for h in ber["hinweise"])
    ber = _rechnen(welt, segs, docs_mit(3))
    k = ber["kennzahlen"]
    assert (k["anker_tage"], k["anker_abdeckung_pct"], k["niveau_tage"], k["median_periode"], k["minimum"]) == (0, None, 0, None, None)
    assert [r["korb_abdeckung_pct"] for r in ber["tage"]] == [92.5] * 5 and not any(r["anker"] for r in ber["tage"])
    assert any("mindestens 95 % des Korbgewichts" in h for h in ber["hinweise"]), ber["hinweise"]


def _job_db(welt, seg, tag, status, error=None):
    welt.run(welt.db[K.JOBS].insert_one({"id": f"test-job-{seg['id']}-{tag}", "segment_id": seg["id"], "model_id": seg["model_id"], "tag": tag,
                                         "status": status, "error": error, "job_type": "daily", "attempts": 1}))


def test_27_runde3_technisch_fehlend_nur_mit_abruf_job(welt):
    """Pruefung Runde 3 #0 ueber die Datenbank: bericht_berechnen liest die Abruf-Jobs (market_crawl_jobs, nur lesend,
    gebuendelt je Modell und Zeitraum ueber den Unique-Index (segment_id, tag)). Technisch fehlend nur mit Job fuer
    (Segment, Tag): failed, data_invalid, 'Tagesplan veraltet' storniert (nie gelaufen). Kein Job, 'Crawler
    ausgeschaltet', 'doppelt faellig' = nicht geplant; war der letzte geplante Tag ein Ausfall, wird nichts getragen.
    Gibt es fuer das Modell gar keine Jobs (Altdaten), rechnet der Bericht wie bisher."""
    JOBS_MOD = JOBS
    assert JOBS_MOD.STORNO_ALT.startswith(B.STORNO_TECHNISCH), "Text im Crawl-Modul geaendert — STORNO_TECHNISCH nachziehen"
    assert not JOBS_MOD.STORNO_AUS.startswith(B.STORNO_TECHNISCH) and not JOBS_MOD.STORNO_DOPPELT.startswith(B.STORNO_TECHNISCH)
    mid = _start(welt)
    a, b, c = _seg(welt, "20000-40000"), _seg(welt, "40001-60000"), _seg(welt, "60001-80000")
    db = welt.db
    try:
        for s, preis in ((a, 12000), (b, 35000), (c, 23500)):
            _doc(welt, s, "2028-01-31", preis)
        for d in range(1, 6):
            _doc(welt, a, _t(d), 12000)
            _job_db(welt, a, _t(d), "completed")
        for d in (1, 2, 5):
            _doc(welt, b, _t(d), 35000)
            _job_db(welt, b, _t(d), "completed")
        _job_db(welt, b, _t(3), "failed", "Apify-Lauf gescheitert")          # 03.: Ausfall; 04.: kein Job (Rotation)
        for d in (1, 2, 3):
            _doc(welt, c, _t(d), 23500)
            _job_db(welt, c, _t(d), "completed")
        _job_db(welt, c, _t(4), "cancelled", JOBS_MOD.STORNO_ALT)            # geplant, nie gelaufen: technisch
        _job_db(welt, c, _t(5), "cancelled", JOBS_MOD.STORNO_AUS)            # Crawler aus: nicht geplant
        _job_db(welt, c, _t(5) + "#2", "cancelled", JOBS_MOD.STORNO_DOPPELT)
        geplant = welt.run(B._geplant_laden(db, sorted([a["id"], b["id"], c["id"]]), _t(1), _t(5)))
        assert geplant[b["id"]] == {_t(1), _t(2), _t(3), _t(5)} and geplant[c["id"]] == {_t(1), _t(2), _t(3), _t(4)}
        ber = welt.run(B.bericht_berechnen(db, mid, "FIVE_DAY", _t(1), _t(5)))
        tage = {r["date"]: r for r in ber["tage"]}
        k = ber["kennzahlen"]
        assert k["planung"] == "jobs"
        assert [(tage[_t(d)]["fehlende_segmente"], tage[_t(d)]["nicht_geplante_segmente"]) for d in range(1, 6)] == [(0, 0), (0, 0), (1, 0), (1, 1), (0, 1)]
        assert [tage[_t(d)]["anker"] for d in range(1, 6)] == [True, True, False, False, False]
        # b am 04. (nicht geplant, letzter geplanter Tag 03. = Ausfall) und c am 05. (nicht geplant nach Ausfall 04.): nichts getragen
        assert k["getragene_segment_tage"] == 0 and k["nicht_geplante_segment_tage"] == 2
        assert (k["segment_tage_gueltig"], k["segment_tage_erwartet"], k["segment_luecken"], k["teilabgedeckte_tage"]) == (11, 13, 2, 2)
        detail = {x["segment_id"]: x for x in ber["segmente"]}
        assert [detail[s["id"]]["erwartete_tage"] for s in (a, b, c)] == [5, 4, 4]
        wahr = (12000 + 35000 + 23500) / 3
        assert all(abs(r["median_korb"] - wahr) <= 1 for r in ber["tage"]), [r["median_korb"] for r in ber["tage"]]
        # Abfrage ueber den Unique-Index (segment_id, tag), nie ein Sammlungs-Scan
        plan = welt.run(db.command({"explain": {"aggregate": K.JOBS, "pipeline": B._jobs_pipeline([a["id"], b["id"]], _t(1), _t(5)),
                                                "cursor": {}, "hint": B.JOB_INDEX}, "verbosity": "queryPlanner"}))
        assert B.JOB_INDEX in str(plan) and "COLLSCAN" not in str(plan)
        # ohne jeden Job (Altdaten): wie bisher — jeder fehlende Segment-Tag eines eingeschalteten Segments ist ein Ausfall
        welt.run(db[K.JOBS].delete_many({"model_id": mid}))
        assert welt.run(B._geplant_laden(db, [a["id"], b["id"], c["id"]], _t(1), _t(5))) is None
        k = welt.run(B.bericht_berechnen(db, mid, "FIVE_DAY", _t(1), _t(5)))["kennzahlen"]
        assert (k["planung"], k["segment_tage_erwartet"], k["segment_luecken"], k["nicht_geplante_segment_tage"]) == ("unbekannt", 15, 4, 0)
    finally:
        _aufraeumen(welt)


def test_28_runde3_tage_ohne_erwartetes_segment_zaehlen_nicht(welt):
    """Pruefung Runde 3 #1 (niedrig): expected_days/Abdeckung zaehlt keine Kalendertage, an denen kein Segment erwartet
    war. April: ein am 10. pausierter Suchauftrag (alle 24 Segmente enabled=False, letzter Lauf 09.) hat 216/216
    Segment-Tage — vorher 'Abdeckung 9/30 Tage' (LOW); ein am 15. neu angelegter 384/384 — vorher 16/30 (MEDIUM). Mit
    bekannter Planung zaehlt ein Tag ohne jeden Job fuer das Modell ebenfalls nicht (Budget-Rotation ueber Modelle,
    Crawler aus)."""
    def _a(d):
        return _t(d, 4)
    segs = [{**_seg_daten(welt, f"{i * 10000}-{i * 10000 + 9999}"), "enabled": False} for i in range(24)]
    docs = [_doc_daten(welt, s, "2028-03-31", 20000, n=12) for s in segs]
    docs += [_doc_daten(welt, s, _a(d), 20000, n=12) for s in segs for d in range(1, 10)]
    ber = _rechnen(welt, segs, docs, "MONTHLY", _a(1), _a(30))
    k = ber["kennzahlen"]
    assert (k["coverage_days"], k["expected_days"], k["tage_ohne_plan"]) == (9, 9, 21)
    assert (k["segment_tage_gueltig"], k["segment_tage_erwartet"]) == (216, 216) and not any("Abdeckung" in g for g in k["confidence_gruende"])
    assert k["confidence"] == "HIGH" and not any("gültige Tage" in h for h in ber["hinweise"])
    assert any("21 Kalendertag(e) ohne geplanten Abruf" in h for h in ber["hinweise"]), ber["hinweise"]
    neu = [{**_seg_daten(welt, f"{i * 10000}-{i * 10000 + 9999}"), "created_at": "2028-04-14T23:30:00+00:00"} for i in range(24)]
    ber = _rechnen(welt, neu, [_doc_daten(welt, s, _a(d), 20000, n=12) for s in neu for d in range(15, 31)], "MONTHLY", _a(1), _a(30))
    k = ber["kennzahlen"]
    assert (k["coverage_days"], k["expected_days"], k["segment_tage_gueltig"], k["segment_tage_erwartet"]) == (16, 16, 384, 384)
    assert k["confidence"] == "HIGH" and k["tage_ohne_plan"] == 14
    # bekannte Planung: am 03. hatte das Modell keinen einzigen Job (kein Tagesdokument) — kein erwarteter Tag
    seg = _seg_daten(welt)
    docs = [_doc_daten(welt, seg, _t(d), 20000) for d in (1, 2, 4, 5)]
    geplant = {seg["id"]: {_t(d) for d in (1, 2, 4, 5)}}
    k = _rechnen(welt, [seg], docs, geplant=geplant)["kennzahlen"]
    assert (k["coverage_days"], k["expected_days"], k["tage_ohne_plan"], k["segment_tage_erwartet"]) == (4, 4, 1, 4)
    # derselbe Tag mit gescheitertem Job ist eine echte Luecke
    k = _rechnen(welt, [seg], docs, geplant={seg["id"]: {_t(d) for d in range(1, 6)}})["kennzahlen"]
    assert (k["coverage_days"], k["expected_days"], k["tage_ohne_plan"]) == (4, 5, 0)


def test_29_runde3_aufgegebener_hot_deal_tag_steht_im_finalen_bericht(welt):
    """Pruefung Runde 3 hotdeals#0 (mittel): ein Tag, dessen Hot-Deal-Auswertung nach wiederholten Fehlern aufgegeben
    wurde (hot_deals.grund 'auswertung_fehler', kein hot_deals_offen mehr), fror vorher still in den finalen Bericht
    ein — die Hot Deals des Tages fehlen dort unbemerkt, und ein eingefrorener Bericht wird nie mehr geaendert. Jetzt:
    Hinweis im Bericht (und Kennzahl), der Bericht wartet deshalb nicht."""
    mid = _start(welt)
    seg = _seg(welt)
    try:
        for d in range(1, 6):
            _doc(welt, seg, _t(d), 20000)
        welt.run(welt.db[K.TAGESSTATS].update_one({"segment_id": seg["id"], "date": _t(3)}, {"$set": {"hot_deals": {
            "ausgewertet_at": "2028-02-04T00:10:00+00:00", "grund": D.GRUND_AUSWERTUNG_FEHLER, "basis_ok": False, "fehlversuche": 6,
            "fehler": "ValueError"}}}))
        assert welt.run(B._hot_deals_offen(welt.db, [seg["id"]], _t(1), _t(5))) == 0
        assert welt.run(B.finalisieren(welt.db, mid, "FIVE_DAY", _t(1), _t(5), jetzt=_nach(_t(5)))) == "erstellt"
        b = _bericht(welt, mid, "FIVE_DAY", _t(1), _t(5))
        assert (b["kennzahlen"]["hot_deal_tage_aufgegeben"], b["kennzahlen"]["hot_deal_tage_offen"]) == (1, 0)
        assert any("1 Tageswert(e) ohne Hot-Deal-Auswertung" in h and "aufgegeben" in h for h in b["hinweise"]), b["hinweise"]
        vorl = welt.run(B.bericht_berechnen(welt.db, mid, "FIVE_DAY", _t(1), _t(5), heute=_t(5)))
        assert any("ohne Hot-Deal-Auswertung" in h for h in vorl["hinweise"]), "auch vorlaeufig sichtbar"
    finally:
        _aufraeumen(welt)
