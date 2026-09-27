# -*- coding: utf-8 -*-
"""Master-Auftrag Marktanalyse (Ahmad 26.09.2026), Phase F — Segment-Health (Abschnitte 27-30, 39, 42, 47, 49, 50, 53).

Je Regel ein Test: EMPTY erst ab 14 gueltigen Laeufen (Confidence hoeher ab 30 Tagen); THIN (Datenqualitaet GOOD,
Health THIN — nie vermischt); STALE; UNSTABLE (ungueltige Laeufe / stark schwankende Werte); HOT/HEALTHY/NORMAL;
Activity Score und konfigurierbare Frequenzzuordnung; Fassungswechsel = eigene Zeitreihe; nur GOOD/MEDIUM-Laeufe
zaehlen; Historie nur bei Wechseln; taeglicher Lauf einmal je Tag nach dem Crawl-Fenster; zwei Server; Lesefunktionen
fuer Berichte/Modellseite; Routen; keine Crawl-Funktion wird aufgerufen. Alle Tage und der Stichtag werden
ausdruecklich uebergeben (kein Mitternachts-Effekt). Testdaten nur test-/t<hex>, Aufraeumen am Ende.
"""
import asyncio
import inspect
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_befunde_runde17_termine import _module, welt  # noqa: E402,F401
from test_markt_20260926 import APIFY, ENT, JOBS, K, SEG, SP, _aufraeumen as _aufraeumen_markt, _modell  # noqa: E402

H = _module("markt.health")
OPT = _module("markt.optimierung")
AUS = _module("markt.auswertung")
D = _module("markt.deals")
B = _module("markt.berichte")
BACKEND = Path(__file__).resolve().parent.parent
STICHTAG = "2031-03-30"
MUSTER = {"$regex": "^(test|dbg)-"}


def _t(i):
    """i Tage vor dem Stichtag (0 = Stichtag)."""
    return (datetime.strptime(STICHTAG, "%Y-%m-%d") - timedelta(days=i)).strftime("%Y-%m-%d")


def _aufraeumen(welt):
    _aufraeumen_markt(welt)
    for coll in (K.HEALTH, K.HEALTH_HISTORIE, K.MODELL_HEALTH, K.VORSCHLAEGE, K.AENDERUNGEN, K.BERICHTE, K.HOTDEALS, K.HOTDEAL_EREIGNISSE,
                 K.PRIVATE_DEALS):
        welt.run(welt.db[coll].delete_many({"$or": [{"segment_id": MUSTER}, {"model_id": MUSTER}]}))


def _mid(welt, zusatz=""):
    return f"test-320d-{welt.w.s}{zusatz}"


def _modell_anlegen(welt, *, km=((20000, 40000),), ez=(2020,), rows=5, version=1, dh="h1", status="active", zusatz="", **extra):
    doc = {**_modell(welt.w), "id": _mid(welt, zusatz), "status": status, "enabled": status == "active", "version": version, "definition_hash": dh,
           "km_buckets": [{"min_km": a, "max_km": b} for a, b in km], "ez_years": list(ez), "rows": rows, "crawls_per_day": 1,
           "gearbox": "AUTOMATIC_GEAR", **extra}
    welt.run(welt.db[K.MODELLE].insert_one(dict(doc)))
    return doc


def _seg(welt, km=(20000, 40000), ez=2020, *, version=1, dh="h1", rows=5, cpd=1, enabled=True, zusatz=""):
    mid = _mid(welt, zusatz)
    fass = f"v{version}:" if version >= 2 else ""
    seg = {"id": f"{mid}:{fass}{ez}:{km[0]}-{km[1]}", "model_id": mid, "label": "BMW 320d (Test)", "min_km": km[0], "max_km": km[1],
           "km_label": f"{round(km[0] / 1000)}–{round(km[1] / 1000)}k km", "year_from": ez, "year_to": ez, "ez_label": f"EZ {ez}",
           "max_items": rows, "crawls_per_day": cpd, "enabled": enabled, "version": version, "definition_hash": dh, "priority": 5,
           "sort": "price_asc"}
    welt.run(welt.db[K.SEGMENTE].insert_one(dict(seg)))
    return seg


def _doc_daten(welt, seg, tag, n, *, median=20000.0, schritt=200.0, dq="GOOD", neu=0, weg=0, red=0, inc=0, top3=False, top5=False,
               invalid=0, kosten=0.01, vergleich=True, version=None, dh=None, praefix="", laeufe=None):
    """Tagesdokument wie speicher.verarbeiten (Phase C): Stichprobe symmetrisch um den Median, n=0 = gueltiger leerer Lauf."""
    s = welt.w.s
    preise = [median + (i - (n - 1) / 2) * schritt for i in range(n)]
    ids = [f"t{s}{praefix}{i}" for i in range(n)]
    rows = int(seg.get("max_items") or 5)
    doc = {"segment_id": seg["id"], "date": tag, "model_id": seg["model_id"], **SP.kennzahlen(preise), "listing_ids": ids,
           "listing_ids_alle": ids, "new_in_sample_ids": ids[:neu], "new_in_sample_today": neu, "disappeared_count": weg,
           "price_reductions_today": red, "price_increases_today": inc,
           "top3_changed": top3 if vergleich else None, "top5_changed": top5 if vergleich else None,
           "data_quality": dq, "data_quality_grund": None if dq == "GOOD" else "fremdfahrzeuge",
           "market_depth": "EMPTY" if n == 0 else "FULL" if n >= rows else "THIN", "version": version or seg["version"],
           "definition_hash": dh or seg["definition_hash"], "rows_soll": rows, "valid_runs": 1, "empty_runs": 1 if n == 0 else 0,
           "invalid_runs": invalid, "crawl_cost_usd": round(kosten * (1 + invalid), 4), "top_n_bewiesen": True,
           "vergleich_vortag": (datetime.strptime(tag, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d") if vergleich else None,
           "observed_at": f"{tag}T05:00:00+00:00", "last_valid_run_at": f"{tag}T05:00:00+00:00"}
    if laeufe is not None:
        doc["laeufe"] = laeufe
    return doc


def _doc(welt, seg, tag, n, **kw):
    doc = _doc_daten(welt, seg, tag, n, **kw)
    welt.run(welt.db[K.TAGESSTATS].update_one({"segment_id": seg["id"], "date": tag}, {"$set": doc}, upsert=True))
    return doc


def _ungueltig(welt, seg, tag, kosten=0.02):
    welt.run(welt.db[K.TAGESSTATS].insert_one({"segment_id": seg["id"], "date": tag, "model_id": seg["model_id"], "invalid_runs": 1, "valid_runs": 0,
                                               "data_quality": "POOR", "data_quality_grund": "ungueltig", "market_depth": "UNKNOWN",
                                               "version": seg["version"], "definition_hash": seg["definition_hash"], "crawl_cost_usd": kosten}))


def _reihe(welt, seg, tage, muster, **kw):
    """tage Tagesdokumente (Stichtag zurueck), Stichprobengroesse zyklisch aus muster."""
    for i in range(tage):
        _doc(welt, seg, _t(i), muster[i % len(muster)], **kw)


def _health(welt, seg):
    return welt.run(welt.db[K.HEALTH].find_one({"segment_id": seg["id"]}, {"_id": 0}))


def _rechnen(welt, *mids, stichtag=STICHTAG):
    return welt.run(OPT.berechnen(welt.db, stichtag=stichtag, model_ids=list(mids) or [_mid(welt)],
                                  jetzt=datetime(2031, 3, 30, 12, 0, tzinfo=timezone.utc)))


def _start(welt, **kw):
    _aufraeumen(welt)
    welt.run(_module("indizes").markt_indizes(welt.db))
    return _modell_anlegen(welt, **kw)


# ---------------------------------------------------------------- reine Regeln
def test_01_empty_erst_ab_14_gueltigen_laeufen_und_confidence(welt):
    """Abschnitt 28: mindestens 14 gueltige Laeufe UND >= 80 % ohne Treffer -> EMPTY; 13 Laeufe -> noch UNKNOWN
    (EMPTY-Kandidat); nach 30 Tagen Confidence HIGH. Nicht geloescht — nur ein Status."""
    _start(welt)
    try:
        seg = _seg(welt)
        _reihe(welt, seg, 13, [0])
        _rechnen(welt)
        h = _health(welt, seg)
        assert h["health"] == "UNKNOWN" and h["health_grund"] == "empty_kandidat" and h["valid_runs"] == 13 and h["empty_rate"] == 1.0
        assert h["recommended_frequency_days"] is None and h["confidence"] == "LOW"
        _doc(welt, seg, _t(13), 0)
        _rechnen(welt)
        h = _health(welt, seg)
        assert h["health"] == "EMPTY" and h["valid_runs"] == 14 and h["confidence"] == "MEDIUM"
        assert h["recommended_pause"] is True and h["recommended_frequency_days"] == 7.0 and h["market_depth"] == "EMPTY"
        assert h["data_quality"] == "GOOD", "leerer Markt ist kein technischer Fehler"
        # 4 von 14 mit Treffern (71 % leer) -> nicht EMPTY
        seg2 = _seg(welt, km=(40001, 60000))
        _reihe(welt, seg2, 14, [0, 0, 1, 0, 0, 0, 3])
        # 30 Tage leer -> HIGH
        seg3 = _seg(welt, km=(60001, 90000))
        _reihe(welt, seg3, 30, [0])
        _rechnen(welt)
        assert _health(welt, seg2)["health"] == "THIN" and _health(welt, seg2)["empty_rate"] == pytest.approx(0.714, abs=0.001)
        h3 = _health(welt, seg3)
        assert h3["health"] == "EMPTY" and h3["confidence"] == "HIGH" and h3["window_days"] == 30 and h3["valid_days"] == 30
        assert welt.run(welt.db[K.SEGMENTE].count_documents({"id": {"$in": [seg["id"], seg3["id"]]}, "enabled": True})) == 2, "nichts geloescht/deaktiviert"
        assert H.MIN_LAEUFE_EMPTY == 14 and H.EMPTY_ANTEIL == 0.8 and H.FENSTER_TAGE == 30
    finally:
        _aufraeumen(welt)


def test_02_thin_datenqualitaet_good_nie_vermischt(welt):
    """Abschnitt 27/29: technisch korrekt, aber kaum Autos -> data_quality GOOD, health THIN. Die technische Qualitaet
    bleibt ein eigenes Feld; THIN ist nicht ueberwiegend leer und im Mittel < 2 Fahrzeuge je Lauf."""
    _start(welt)
    try:
        seg = _seg(welt)
        _reihe(welt, seg, 20, [1, 2])
        _rechnen(welt)
        h = _health(welt, seg)
        assert h["health"] == "THIN" and h["data_quality"] == "GOOD" and h["avg_valid_rows"] == 1.5 and h["empty_rate"] == 0.0
        assert h["market_depth"] == "THIN" and h["health_grund"] == "wenige_fahrzeuge"
        # THIN wird nie taeglich empfohlen (Abschnitt 29: nicht taeglich teuer crawlen)
        assert h["recommended_intervall_tage"] >= H.THIN_MIN_INTERVALL_TAGE and h["recommended_crawls_per_day"] == 1
        # im Mittel genau 2 -> nicht mehr THIN
        seg2 = _seg(welt, km=(40001, 60000))
        _reihe(welt, seg2, 20, [2])
        _rechnen(welt)
        assert _health(welt, seg2)["health"] in ("NORMAL", "HEALTHY", "HOT") and _health(welt, seg2)["health"] != "THIN"
    finally:
        _aufraeumen(welt)


def test_03_stale_letzter_gueltiger_lauf_zu_alt(welt):
    """STALE: der letzte gueltige Lauf liegt laenger zurueck als erwarteter Abstand + 2 Tage (taeglich: > 3 Tage).
    Ein spaeterer ungueltiger Lauf aendert daran nichts; ein bewusst seltener Takt (alle 4 Tage) ist nicht veraltet."""
    _start(welt)
    try:
        seg = _seg(welt)
        for i in range(4, 24):
            _doc(welt, seg, _t(i), 5)
        _ungueltig(welt, seg, _t(1))
        seg2 = _seg(welt, km=(40001, 60000))
        for i in range(3, 24):
            _doc(welt, seg2, _t(i), 5)
        seg3 = _seg(welt, km=(60001, 90000))
        for i in range(5, 29, 4):              # alle 4 Tage, letzter vor 5 Tagen -> Grenze 4 + 2 = 6 -> nicht veraltet
            _doc(welt, seg3, _t(i), 5)
        _rechnen(welt)
        h = _health(welt, seg)
        assert h["health"] == "STALE" and h["letzter_lauf_alter_tage"] == 4 and h["stale_grenze_tage"] == 3 and h["recommended_frequency_days"] is None
        assert _health(welt, seg2)["health"] != "STALE" and _health(welt, seg2)["letzter_lauf_alter_tage"] == 3
        h3 = _health(welt, seg3)
        assert h3["health"] != "STALE" and h3["erwarteter_abstand_tage"] == 4
        # keine Tageswerte mehr im Fenster, aber frueher ein gueltiger Lauf (Segmentstatistik) -> STALE
        seg4 = _seg(welt, km=(90001, 120000))
        welt.run(welt.db[K.SEGMENTSTATS].insert_one({"_id": seg4["id"], "segment_id": seg4["id"], "letzter_gueltiger_lauf_at": "2031-01-02T05:00:00+00:00"}))
        _rechnen(welt)
        assert _health(welt, seg4)["health"] == "STALE" and _health(welt, seg4)["valid_runs"] == 0
    finally:
        _aufraeumen(welt)


def test_04_unstable_ungueltige_laeufe_und_schwankende_werte(welt):
    """UNSTABLE: >= 30 % ungueltige Laeufe (mindestens 3) — oder der Tages-Low-Market-Median schwankt stark
    (Variationskoeffizient >= 20 %). Wenige Ausfaelle bleiben normal."""
    _start(welt)
    try:
        seg = _seg(welt)
        for i in range(10):
            _doc(welt, seg, _t(i), 5)
        for i in range(10, 15):
            _ungueltig(welt, seg, _t(i))
        seg2 = _seg(welt, km=(40001, 60000))
        for i in range(12):
            _doc(welt, seg2, _t(i), 5, median=15000.0 if i % 2 else 25000.0)
        seg3 = _seg(welt, km=(60001, 90000))
        for i in range(12):
            _doc(welt, seg3, _t(i), 5)
        for i in range(12, 14):
            _ungueltig(welt, seg3, _t(i))
        _rechnen(welt)
        h = _health(welt, seg)
        assert h["health"] == "UNSTABLE" and h["health_grund"] == "viele_ungueltige_laeufe" and h["invalid_runs"] == 5 and h["ungueltig_anteil"] == 0.333
        assert h["data_quality"] == "POOR" and h["recommended_frequency_days"] is None, "erst Technik pruefen, keine Frequenzempfehlung"
        h2 = _health(welt, seg2)
        assert h2["health"] == "UNSTABLE" and h2["health_grund"] == "stark_schwankend" and h2["price_volatility_pct"] == 25.0
        assert h2["data_quality"] == "GOOD", "schwankende Preise sind kein technischer Fehler"
        assert _health(welt, seg3)["health"] != "UNSTABLE" and _health(welt, seg3)["invalid_runs"] == 2
    finally:
        _aufraeumen(welt)


def test_05_hot_healthy_normal(welt):
    """HOT: Score >= 75 (viele neue Inserate, Top-N-Wechsel, Preisaenderungen, Umschlag); HEALTHY: Score >= 45 und
    Stichprobe >= 60 % gefuellt; NORMAL: ruhiges, volles Segment."""
    _start(welt)
    try:
        heiss = _seg(welt)
        for i in range(20):
            _doc(welt, heiss, _t(i), 5, neu=2, weg=2, red=1, top3=True, top5=True, praefix=f"h{i}")
        gesund = _seg(welt, km=(40001, 60000))
        for i in range(20):
            _doc(welt, gesund, _t(i), 5, neu=1, weg=1, top5=bool(i % 2), praefix=f"g{i}")
        ruhig = _seg(welt, km=(60001, 90000))
        _reihe(welt, ruhig, 20, [5])
        _rechnen(welt)
        h = _health(welt, heiss)
        assert h["health"] == "HOT" and h["activity_score"] == 100 and h["liquiditaet"] == "HIGH"
        assert h["recommended_crawls_per_day"] == 2 and h["recommended_frequency_days"] == 0.5
        assert h["top3_turnover_rate"] == 1.0 and h["price_drop_events"] == 20 and h["unique_listings"] == 100
        g = _health(welt, gesund)
        assert g["health"] == "HEALTHY" and 45 <= g["activity_score"] < 75 and g["recommended_frequency_days"] == 1.0
        r = _health(welt, ruhig)
        assert r["health"] == "NORMAL" and r["activity_score"] == 25 and r["unique_listings"] == 5 and r["liquiditaet"] == "LOW"
        assert r["recommended_intervall_tage"] == 2, "Score 25 -> Stufe 20-44 -> alle 2 Tage"
    finally:
        _aufraeumen(welt)


def test_06_activity_score_und_frequenzzuordnung_konfigurierbar(welt):
    """Abschnitt 30: Score 0-100 aus Trefferquote, neuen Listings, Top-N-Wechseln, Preisaenderungen, Liquiditaet (je
    Kalendertag). Standard-Zuordnung 75/45/20/0; die Zuordnung ist konfigurierbar (market_config) und geprueft."""
    e = H.empfehlung
    assert e("HOT", 90) == {"pausiert": False, "intervall_tage": 1, "crawls_per_day": 2, "frequency_days": 0.5, "stufe_ab": 75}
    assert e("HEALTHY", 60)["frequency_days"] == 1.0 and e("NORMAL", 30)["intervall_tage"] == 2
    assert e("NORMAL", 19)["intervall_tage"] == 3 and e("NORMAL", 0)["intervall_tage"] == 7 and e("NORMAL", 10)["intervall_tage"] == 5
    assert e("EMPTY", 0) == {"pausiert": True, "intervall_tage": 7, "crawls_per_day": 1, "frequency_days": 7.0, "stufe_ab": None}
    assert e("UNKNOWN", 80) is None and e("STALE", 80) is None and e("UNSTABLE", 80) is None
    assert e("THIN", 60)["intervall_tage"] == 2, "THIN nie taeglich"
    # Hysterese (Pruefbefund F1/F6): mit aktiver Reduktion (alle 2 Tage) erst 5 Punkte ueber der Grenze wieder haeufiger;
    # die Zielstufe selbst bleibt die Zuordnung exakt (Score 22 unter Wirkung 2 Tage bleibt 2 Tage, nie 3)
    zwei = {"intervall_tage": 2, "crawls_per_day": 1}
    assert e("NORMAL", 47)["intervall_tage"] == 1 and e("NORMAL", 47, aktuell=zwei)["intervall_tage"] == 2
    assert e("NORMAL", 50, aktuell=zwei)["intervall_tage"] == 1 and e("NORMAL", 22, aktuell=zwei)["intervall_tage"] == 2
    # Gewichte summieren sich zu 100; je Kalendertag normiert: ein Vergleich ueber 3 Tage zaehlt wie 3 Tage
    assert sum(H.GEWICHTE.values()) == 100
    m1 = {"rows_soll": 5, "valid_runs": 10, "vergleich_tage": 9, "_mittel": 5.0, "_neu_je_tag": 1.5, "_weg_je_tag": 1.5, "_top_je_tag": 0.7, "_preis_je_tag": 0.75}
    assert H.activity(m1)[0] == 100
    m2 = {**m1, "_neu_je_tag": 0.0, "_weg_je_tag": 0.0, "_top_je_tag": 0.0, "_preis_je_tag": 0.0}
    assert H.activity(m2) == (25, {"treffer": 1.0, "neu": 0.0, "top": 0.0, "preis": 0.0, "liquiditaet": 0.0}, "LOW")
    k = H.kennzahlen([{"date": "2031-01-10", "sample_size": 5, "median_price": 1, "vergleich_vortag": "2031-01-07", "new_in_sample_today": 3,
                       "data_quality": "GOOD", "valid_runs": 1}], rows_soll=5, stichtag="2031-01-10")
    assert k["vergleich_kalendertage"] == 3 and k["_neu_je_tag"] == 1.0, "3 neue in 3 Tagen = 1 je Tag"
    # Konfiguration: pruefen + speichern + wirkt auf die Empfehlung
    cfg = H.frequenz_pruefen({"stufen": [{"ab": 80, "crawls_per_day": 2, "intervall_tage": 1}, {"ab": 30, "intervall_tage": 1},
                                         {"ab": 0, "intervall_tage": 4, "intervall_tage_bis": 10}], "empty_nachpruefung_tage": 14})
    assert e("NORMAL", 29, cfg)["intervall_tage"] == 4 and e("NORMAL", 0, cfg)["intervall_tage"] == 10 and e("EMPTY", 0, cfg)["intervall_tage"] == 14
    for falsch in ({"stufen": [{"ab": 50}, {"ab": 60}, {"ab": 0}]}, {"stufen": [{"ab": 50}, {"ab": 10}]},
                   {"stufen": [{"ab": 0, "crawls_per_day": 2, "intervall_tage": 2}]}, {"stufen": [{"ab": 0, "intervall_tage": 5, "intervall_tage_bis": 3}]},
                   {"stufen": []}, {"stufen": [{"ab": 0}], "empty_nachpruefung_tage": 99}):
        with pytest.raises(H.Ungueltig):
            H.frequenz_pruefen(falsch)
    db = welt.db
    alt = welt.run(db[K.KONFIG].find_one({"_id": K.OPTIMIERUNG_DOK}))
    try:
        welt.run(OPT.frequenz_setzen(db, cfg, wer="test"))
        einst = welt.run(OPT.einstellungen(db))
        assert einst["frequenz"] == cfg and einst["modus"] in ("OBSERVE", "SAFE_AUTO") and einst["frequenz_standard"] == H.FREQUENZ_STANDARD
        # kaputter Eintrag in der Datenbank -> Standard (nie ein Absturz)
        welt.run(db[K.KONFIG].update_one({"_id": K.OPTIMIERUNG_DOK}, {"$set": {"frequenz": {"stufen": "kaputt"}}}))
        assert welt.run(OPT.einstellungen(db))["frequenz"] == H.FREQUENZ_STANDARD
    finally:
        if alt:
            welt.run(db[K.KONFIG].replace_one({"_id": K.OPTIMIERUNG_DOK}, alt, upsert=True))
        else:
            welt.run(db[K.KONFIG].delete_one({"_id": K.OPTIMIERUNG_DOK}))


def test_07_fassungswechsel_eigene_zeitreihe(welt):
    """Nur die aktuelle Fassung des Segments zaehlt (Abschnitt 34): Tagesdokumente einer anderen Fassung (Altdaten unter
    derselben Segment-ID) fliessen nicht ein; die neue Fassung beginnt ohne Urteil (UNKNOWN), bis genug Laeufe da sind."""
    _start(welt, version=2, dh="h2")
    try:
        seg = _seg(welt, version=2, dh="h2")
        for i in range(3, 25):
            _doc(welt, seg, _t(i), 5, version=1, dh="h1", praefix="alt")
        for i in range(3):
            _doc(welt, seg, _t(i), 2, praefix="neu")
        _rechnen(welt)
        h = _health(welt, seg)
        assert h["health"] == "UNKNOWN" and h["health_grund"] == "zu_wenig_laeufe" and h["valid_runs"] == 3
        assert h["version"] == 2 and h["definition_hash"] == "h2" and h["unique_listings"] == 2 and h["avg_valid_rows"] == 2.0
        assert H.fassung({"version": 1, "definition_hash": "h1"}, seg) == (1, "h1") and H.fassung({}, seg) == (2, "h2")
    finally:
        _aufraeumen(welt)


def test_08_nur_good_medium_zaehlen(welt):
    """Nur Laeufe mit Datenqualitaet GOOD/MEDIUM sind gueltige Beobachtungen: POOR-Tage (Fremdfahrzeuge) mit 5 Autos
    zaehlen als ungueltig, nicht als Treffer — das Segment bleibt EMPTY; MEDIUM zaehlt als gueltig. Laeufe eines
    Tages kommen aus 'laeufe' (ein POOR-Zweitlauf neben einem guten Lauf ist ungueltig)."""
    _start(welt)
    try:
        seg = _seg(welt)
        for i in range(14):
            _doc(welt, seg, _t(i), 0)
        for i in range(14, 16):
            _doc(welt, seg, _t(i), 0, dq="MEDIUM")
        for i in range(16, 19):
            _doc(welt, seg, _t(i), 5, dq="POOR", praefix="p")
        _rechnen(welt)
        h = _health(welt, seg)
        assert h["valid_runs"] == 16 and h["invalid_runs"] == 3 and h["poor_runs"] == 3 and h["empty_rate"] == 1.0
        assert h["health"] == "EMPTY" and h["unique_listings"] == 0 and h["avg_valid_rows"] == 0.0
        # Laeufe je Tag aus 'laeufe': ein guter Lauf mit 5 Autos, ein POOR-Lauf, ein ungueltiger -> 1 gueltig, 2 ungueltig
        laeufe = [{"sample_size": 5, "data_quality": "GOOD"}, {"sample_size": 3, "data_quality": "POOR"}, {"ungueltig": True, "sample_size": 0}]
        assert H.laeufe_aus_doc({"sample_size": 5, "laeufe": laeufe}) == [("gueltig", 5, "GOOD"), ("poor", 3, "POOR"), ("ungueltig", 0, "POOR")]
        assert H.laeufe_aus_doc({"sample_size": 4, "valid_runs": 2, "empty_runs": 1, "invalid_runs": 1, "data_quality": "GOOD"}) == [
            ("ungueltig", 0, "POOR"), ("gueltig", 0, "GOOD"), ("gueltig", 4, "GOOD")]
        assert H.laeufe_aus_doc({"invalid_runs": 2}) == [("ungueltig", 0, "POOR")] * 2
    finally:
        _aufraeumen(welt)


def test_09_historie_nur_bei_wechsel_und_modell_aggregat(welt):
    """Historie nur bei Status-WECHSEL (nicht jeden Tag ein Dokument); ein Dokument je Segment; Modell-Health als
    Aggregat (Zaehler, Status, mittlerer Score); Lesefunktionen fuer Berichte und Modellseite."""
    _start(welt, km=((20000, 40000), (40001, 60000)))
    db = welt.db
    try:
        seg = _seg(welt)
        seg2 = _seg(welt, km=(40001, 60000))
        _reihe(welt, seg, 20, [5])
        _reihe(welt, seg2, 20, [1, 2])
        _rechnen(welt)
        _rechnen(welt)
        _rechnen(welt, stichtag=_t(-1))
        assert welt.run(db[K.HEALTH].count_documents({"segment_id": seg["id"]})) == 1
        hist = welt.run(H.historie(db, seg["id"]))
        assert len(hist) == 1 and hist[0]["von"] is None and hist[0]["nach"] == "NORMAL" and hist[0]["tag"] == STICHTAG
        # Wechsel: seg wird THIN -> genau ein neuer Historieneintrag
        _reihe(welt, seg, 20, [1, 2])
        _rechnen(welt, stichtag=_t(-2))
        _rechnen(welt, stichtag=_t(-2))
        hist = welt.run(H.historie(db, seg["id"]))
        assert [(x["von"], x["nach"]) for x in hist] == [("NORMAL", "THIN"), (None, "NORMAL")]
        assert _health(welt, seg)["health_seit"] == _t(-2) and _health(welt, seg)["health_vorher"] == "NORMAL"
        mh = welt.run(H.modell_health(db, [_mid(welt)]))[_mid(welt)]
        assert mh["segmente"] == 2 and mh["zaehler"]["THIN"] == 2 and mh["health"] == "THIN" and mh["segmente_bewertet"] == 2
        je = welt.run(H.health_je_segment(db, [seg["id"], seg2["id"], "gibt-es-nicht"]))
        assert set(je) == {seg["id"], seg2["id"]} and je[seg["id"]]["health"] == "THIN" and "confidence" in je[seg["id"]]
        assert welt.run(H.health_je_segment(db, [])) == {}
        assert H.modell_status({"HOT": 1, "HEALTHY": 1, "NORMAL": 2}) == "HEALTHY" and H.modell_status({"EMPTY": 3, "HOT": 1}) == "EMPTY"
        assert H.modell_status({"UNKNOWN": 5}) == "UNKNOWN" and H.modell_status({"STALE": 2, "NORMAL": 1}) == "STALE"
        assert H.modell_status({"HOT": 2, "NORMAL": 4}) == "HOT" and H.modell_status({"THIN": 2, "EMPTY": 1, "NORMAL": 3}) == "THIN"
        ansicht = welt.run(OPT.modell_ansicht(db, _mid(welt)))
        assert ansicht["modell"]["health"] == "THIN" and [s["segment_id"] for s in ansicht["segmente"]] == [seg["id"], seg2["id"]]
        assert welt.run(OPT.modell_ansicht(db, "test-gibt-es-nicht")) is None
    finally:
        _aufraeumen(welt)


def test_10_taeglich_einmal_nach_dem_crawl_fenster(welt, monkeypatch):
    """Zeitplan: im Auswertungs-Worker einmal je Tag, erst nach dem Crawl-Fenster (dann sind die Laeufe des Tages
    gespeichert); der Merker market_config/health verhindert eine zweite Berechnung am selben Tag; Fehler des Tages
    bleiben stehen (Alarm bleibt offen); in der Schreibpause wird nichts gerechnet."""
    _start(welt)
    db = welt.db
    merker = welt.run(db[K.KONFIG].find_one({"_id": K.HEALTH_DOK}))
    aufrufe = []

    async def _berechnen(db_, **kw):
        aufrufe.append(kw)
        return {"stichtag": kw.get("stichtag"), "fehler": 0, "modelle": 1, "health": {}}
    monkeypatch.setattr(OPT, "berechnen", _berechnen)
    try:
        welt.run(db[K.KONFIG].delete_one({"_id": K.HEALTH_DOK}))
        ab = OPT.taeglich_ab_stunde()
        tag = "2031-04-02"
        frueh = datetime(2031, 4, 2, 0, 30, tzinfo=K.ZEITZONE)
        spaet = datetime(2031, 4, 2, ab, 5, tzinfo=K.ZEITZONE)
        assert welt.run(OPT.taeglich(db, jetzt=frueh))["uebersprungen"] == "vor Ende des Crawl-Fensters" and aufrufe == []
        erg = welt.run(OPT.taeglich(db, jetzt=spaet))
        assert erg["modelle"] == 1 and len(aufrufe) == 1 and aufrufe[0]["stichtag"] == tag
        assert welt.run(K.merker_lesen(db, K.HEALTH_DOK))["tag"] == tag
        assert welt.run(OPT.taeglich(db, jetzt=spaet + timedelta(hours=2)))["uebersprungen"] == "heute schon berechnet" and len(aufrufe) == 1
        # Fehler des Tages bleiben stehen (der Worker haelt den Alarm offen), ohne alle 5 Minuten neu zu rechnen
        welt.run(db[K.KONFIG].update_one({"_id": K.HEALTH_DOK}, {"$set": {"fehler": 2}}))
        assert welt.run(OPT.taeglich(db, jetzt=spaet + timedelta(hours=3)))["fehler"] == 2 and len(aufrufe) == 1
        # Schreibpause: nichts rechnen, kein Merker
        WART = _module("wartung")

        async def _pause(db_, methode="POST"):
            return True
        monkeypatch.setattr(WART, "aktiv_async", _pause)
        naechster = spaet + timedelta(days=1)
        assert welt.run(OPT.taeglich(db, jetzt=naechster)) == {"wartung": True, "fehler": 0} and len(aufrufe) == 1
        assert welt.run(K.merker_lesen(db, K.HEALTH_DOK))["tag"] == tag
        # der Auswertungs-Worker ruft genau einmal nach Hot Deals und Berichten
        q = inspect.getsource(AUS.durchlauf)
        assert q.count("optimierung.taeglich(") == 1 and q.index("berichte.faellige_finalisieren") < q.index("optimierung.taeglich(")
    finally:
        if merker:
            welt.run(db[K.KONFIG].replace_one({"_id": K.HEALTH_DOK}, merker, upsert=True))
        else:
            welt.run(db[K.KONFIG].delete_one({"_id": K.HEALTH_DOK}))
        _aufraeumen(welt)


def test_11_zwei_server_sperre_und_idempotenz(welt):
    """Zwei Server: der Admin-Knopf nimmt dieselbe Sperre wie der Auswertungs-Worker (gesperrt -> kein Lauf); von zwei
    gleichzeitigen Knopfdruecken rechnet genau einer; wiederholte Laeufe legen weder Health-Dokumente noch Historie
    noch Vorschlaege doppelt an (Unique-Indizes)."""
    _start(welt)
    db = welt.db
    merker = welt.run(db[K.KONFIG].find_one({"_id": K.HEALTH_DOK}))
    try:
        info = welt.run(db[K.HEALTH].index_information())
        assert info["markt_health_segment"].get("unique")
        assert welt.run(db[K.HEALTH_HISTORIE].index_information())["markt_health_wechsel"].get("unique")
        assert welt.run(db[K.VORSCHLAEGE].index_information())["markt_vorschlag_schluessel"].get("unique")
        assert welt.run(db[K.MODELL_HEALTH].index_information())["markt_health_modell"].get("unique")
        assert "market_segment_health" not in " ".join(_module("indizes").MARKT_UNIQUE_KRITISCH), "nicht kritisch fuer den Crawler"
        seg = _seg(welt)
        jetzt = datetime.now(timezone.utc)
        for i in range(20):
            _doc(welt, seg, K.heute_tag(jetzt - timedelta(days=i)), 0)

        async def _zwei():
            return await asyncio.gather(*[OPT.jetzt_berechnen(db, wer=f"test-{i}", jetzt=jetzt) for i in range(2)])
        erg = welt.run(_zwei())
        assert sorted(bool(e.get("gesperrt")) for e in erg) == [False, True], "genau ein Lauf"
        welt.run(OPT.jetzt_berechnen(db, wer="test", jetzt=jetzt))
        assert welt.run(db[K.HEALTH].count_documents({"segment_id": seg["id"]})) == 1
        assert _health(welt, seg)["health"] == "EMPTY"
        assert welt.run(db[K.HEALTH_HISTORIE].count_documents({"segment_id": seg["id"]})) == 1
        assert welt.run(db[K.VORSCHLAEGE].count_documents({"segment_id": seg["id"]})) == 1
        assert welt.run(K.merker_lesen(db, K.HEALTH_DOK))["quelle"] == "admin"
        from job_lock import acquire, release
        assert OPT.SPERRE == AUS.SPERRE
        token = welt.run(acquire(db, AUS.SPERRE, ttl_seconds=60))
        try:
            assert welt.run(OPT.jetzt_berechnen(db, wer="test")) == {"gesperrt": True}
        finally:
            welt.run(release(db, AUS.SPERRE, token))
    finally:
        if merker:
            welt.run(db[K.KONFIG].replace_one({"_id": K.HEALTH_DOK}, merker, upsert=True))
        else:
            welt.run(db[K.KONFIG].delete_one({"_id": K.HEALTH_DOK}))
        _aufraeumen(welt)


def test_12_keine_crawl_funktion_wird_aufgerufen(welt, monkeypatch):
    """Abschnitt 39: Health und Vorschlaege entstehen nur aus gespeicherten Tageswerten. Jede Crawl-Funktion schlaegt
    hier fehl — Berechnung (auch der taegliche Lauf im Auswertungs-Worker und der Admin-Knopf) und alle Lesewege
    laufen trotzdem."""
    async def _verboten(*a, **k):
        raise AssertionError("Crawl-Funktion aufgerufen")
    for mod, name in ((APIFY, "lauf"), (APIFY, "lauf_mit_ersatz"), (JOBS, "job_sofort"), (JOBS, "tagesplan"), (JOBS, "einmal"),
                      (JOBS, "verarbeiten_buendel"), (SEG, "synchronisieren"), (ENT, "taeglich")):
        monkeypatch.setattr(mod, name, _verboten)
    _start(welt, km=((20000, 40000), (40001, 60000)))
    db = welt.db
    merker = {k: welt.run(db[K.KONFIG].find_one({"_id": k})) for k in (K.AUSWERTUNG_DOK, K.HEALTH_DOK)}
    try:
        seg = _seg(welt)
        seg2 = _seg(welt, km=(40001, 60000))
        # Tage relativ zu HEUTE (der taegliche Lauf und der Admin-Knopf rechnen mit dem heutigen Stichtag)
        mittag = datetime.now(K.ZEITZONE).replace(hour=OPT.taeglich_ab_stunde(), minute=30)
        heute = K.heute_tag(mittag)
        tag = lambda i: (datetime.strptime(heute, "%Y-%m-%d") - timedelta(days=i)).strftime("%Y-%m-%d")  # noqa: E731
        for i in range(20):
            _doc(welt, seg, tag(i), 0)
            _doc(welt, seg2, tag(i), [1, 2][i % 2])
        erg = _rechnen(welt, stichtag=heute)
        assert erg["fehler"] == 0 and erg["segmente"] == 2 and erg["health"]["EMPTY"] == 1 and erg["health"]["THIN"] == 1
        # Auswertungs-Worker: Hot Deals/Berichte nur fuer das Testmodell, Health taeglich (Merker weg, nach dem Fenster)
        echt_d, echt_b = D.auswerten_faellige, B.faellige_finalisieren

        async def _d(db_, **kw):
            return await echt_d(db_, segment_ids=[seg["id"]], **{k: v for k, v in kw.items() if k != "segment_ids"})

        async def _b(db_, **kw):
            return await echt_b(db_, model_ids=[_mid(welt)], **{k: v for k, v in kw.items() if k != "model_ids"})
        monkeypatch.setattr(D, "auswerten_faellige", _d)
        monkeypatch.setattr(B, "faellige_finalisieren", _b)
        welt.run(db[K.KONFIG].delete_one({"_id": K.HEALTH_DOK}))
        monkeypatch.setattr(K, "jetzt", lambda: mittag.astimezone(timezone.utc))
        erg = welt.run(AUS.durchlauf(db))
        assert AUS.fehler_anzahl(erg) == 0 and erg["health"]["fehler"] == 0 and erg["health"]["modelle"] >= 1
        assert welt.run(OPT.jetzt_berechnen(db, wer="test"))["fehler"] == 0
        welt.run(H.health_je_segment(db, [seg["id"]]))
        welt.run(H.modell_health(db))
        welt.run(OPT.uebersicht(db))
        welt.run(OPT.vorschlaege_liste(db, status="alle"))
        welt.run(OPT.modell_ansicht(db, _mid(welt)))
        v = welt.run(db[K.VORSCHLAEGE].find_one({"segment_id": seg["id"]}, {"_id": 0}))
        assert welt.run(OPT.vorschlag_entscheiden(db, v["id"], "annehmen", wer="test"))["status"] == "ACCEPTED"
    finally:
        for k, alt in merker.items():
            if alt:
                welt.run(db[K.KONFIG].replace_one({"_id": k}, alt, upsert=True))
            else:
                welt.run(db[K.KONFIG].delete_one({"_id": k}))
        _aufraeumen(welt)


def test_13_architektur_und_routen():
    """Architektur: health/optimierung importieren und rufen keine Crawl-Funktion (siehe auch test_b02); Routen lesen
    mit current_admin, schreiben nur mit current_super_admin; nichts davon in den Firmen-Routen (routes/markt.py)."""
    verboten = ("apify", "lauf_mit_ersatz", "job_sofort", "tagesplan(", "verarbeiten_buendel", "jobs.", "synchronisieren(", "httpx",
                "requests", "urllib", "entfernung", "import segmente", "segmente import")
    for datei in ("health.py", "optimierung.py"):
        q = (BACKEND / "markt" / datei).read_text(encoding="utf-8")
        for v in verboten:
            assert v not in q, f"{datei}: {v}"
    r = (BACKEND / "routes" / "markt_admin.py").read_text(encoding="utf-8")
    lesen = ('@router.get("/admin/market/optimierung")', '@router.get("/admin/market/optimierung/vorschlaege")',
             '@router.get("/admin/market/health/models/{model_id}")', '@router.get("/admin/market/health/segments/{segment_id}/history")')
    for pfad in lesen:
        kopf = r.split(pfad)[1].split("\n\n")[0]
        assert "Depends(current_admin)" in kopf, pfad
    schreiben = ('@router.put("/admin/market/optimierung/frequenz")', '@router.post("/admin/market/optimierung/berechnen")',
                 '@router.post("/admin/market/optimierung/vorschlaege/{vorschlag_id}/annehmen")',
                 '@router.post("/admin/market/optimierung/vorschlaege/{vorschlag_id}/ablehnen")',
                 '@router.post("/admin/market/optimierung/vorschlaege/{vorschlag_id}/uebernehmen")')
    for pfad in schreiben:
        kopf = r.split(pfad)[1].split("\n\n")[0]
        assert "current_super_admin" in kopf and "log_activity_sicher" in kopf, pfad
    firmen = (BACKEND / "routes" / "markt.py").read_text(encoding="utf-8").lower()
    assert "health" not in firmen and "optimierung" not in firmen and "vorschlag" not in firmen
    assert K.HEALTH == "market_segment_health" and K.MODELL_HEALTH == "market_model_health" and K.VORSCHLAEGE == "market_optimization_proposals"
    # Lesewege schreiben nicht
    for fn in (H.health_je_segment, H.modell_health, H.segmente_eines_modells, H.historie, OPT.uebersicht, OPT.vorschlaege_liste, OPT.modell_ansicht):
        q = inspect.getsource(fn)
        assert not any(x in q for x in ("insert_one", "update_one", "update_many", "delete_one", "delete_many", "bulk_write")), fn.__name__


def test_14_routen_aufrufe(welt, monkeypatch):
    """Die Routen selbst (ohne HTTP): Uebersicht, Frequenz (400 bei Unsinn), Berechnen (409 bei Sperre), Modell-Health
    (404 ohne Berechnung), Historie; Entscheidungen 404/409."""
    from fastapi import HTTPException
    MA = _module("routes.markt_admin")
    monkeypatch.setattr(MA, "db", welt.db)
    _start(welt)
    db = welt.db
    alt = {k: welt.run(db[K.KONFIG].find_one({"_id": k})) for k in (K.OPTIMIERUNG_DOK, K.HEALTH_DOK)}
    admin = {"id": "test-admin"}
    try:
        seg = _seg(welt)
        _reihe(welt, seg, 20, [1, 2])
        _rechnen(welt)
        u = welt.run(MA.admin_market_optimierung(_=admin))
        assert u["modus"] in ("OBSERVE", "SAFE_AUTO") and [m["modus"] for m in u["modi"]] == ["OBSERVE", "SAFE_AUTO", "FULL_AUTO"]
        assert u["modi"][2]["gesperrt"] is True and u["schwellen"]["min_laeufe_empty"] == 14 and "Datenqualität" in u["hinweis"]
        assert any(m["model_id"] == _mid(welt) for m in u["modelle"])
        with pytest.raises(HTTPException) as ex:
            welt.run(MA.admin_market_optimierung_frequenz(MA.FrequenzIn(stufen=[{"ab": 10}, {"ab": 20}]), admin=admin))
        assert ex.value.status_code == 400
        ok = welt.run(MA.admin_market_optimierung_frequenz(MA.FrequenzIn(**H.FREQUENZ_STANDARD), admin=admin))
        assert ok["frequenz"] == H.FREQUENZ_STANDARD
        from job_lock import acquire, release
        token = welt.run(acquire(db, AUS.SPERRE, ttl_seconds=60))
        try:
            with pytest.raises(HTTPException) as ex:
                welt.run(MA.admin_market_optimierung_berechnen(admin=admin))
            assert ex.value.status_code == 409
        finally:
            welt.run(release(db, AUS.SPERRE, token))
        d = welt.run(MA.admin_market_health_modell(_mid(welt), _=admin))
        assert d["segmente"][0]["health"] == "THIN"
        with pytest.raises(HTTPException) as ex:
            welt.run(MA.admin_market_health_modell("test-gibt-es-nicht", _=admin))
        assert ex.value.status_code == 404
        assert welt.run(MA.admin_market_health_historie(seg["id"], _=admin))["wechsel"][0]["nach"] == "THIN"
        v = welt.run(MA.admin_market_vorschlaege(status="offen", typ=None, model_id=_mid(welt), limit=50, _=admin))
        assert v["anzahl"] == 1 and v["vorschlaege"][0]["typ"] == "REDUCE_FREQUENCY"
        with pytest.raises(HTTPException) as ex:
            welt.run(MA.admin_market_vorschlaege(status="quatsch", typ=None, model_id=None, limit=50, _=admin))
        assert ex.value.status_code == 400
        with pytest.raises(HTTPException) as ex:
            welt.run(MA.admin_market_vorschlag_annehmen("gibt-es-nicht", admin=admin))
        assert ex.value.status_code == 404
        vid = v["vorschlaege"][0]["id"]
        assert welt.run(MA.admin_market_vorschlag_ablehnen(vid, admin=admin))["vorschlag"]["status"] == "REJECTED"
        with pytest.raises(HTTPException) as ex:
            welt.run(MA.admin_market_vorschlag_annehmen(vid, admin=admin))
        assert ex.value.status_code == 409
        with pytest.raises(HTTPException) as ex:
            welt.run(MA.admin_market_vorschlag_uebernehmen(vid, admin=admin))
        assert ex.value.status_code == 400, "Frequenz-Vorschlaege uebernimmt man nicht (nur MERGE/SPLIT)"
    finally:
        for k, a in alt.items():
            if a:
                welt.run(db[K.KONFIG].replace_one({"_id": k}, a, upsert=True))
            else:
                welt.run(db[K.KONFIG].delete_one({"_id": k}))
        _aufraeumen(welt)
