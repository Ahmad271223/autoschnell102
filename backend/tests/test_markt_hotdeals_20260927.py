# -*- coding: utf-8 -*-
"""Master-Auftrag Marktanalyse (Ahmad 26.09.2026), Phase D — Hot Deals (Abschnitte 22-26, 48, 49, 58).

Je Regel ein Test: Basis zu duenn -> kein Deal; Ereignisse in richtiger Reihenfolge (NEW -> STILL_HOT ->
PRICE_DROP -> LEFT -> BECAME -> LEFT -> REMOVED); LEFT_HOT_ZONE (leerer Tag, nicht mehr im Sample); privat vs.
Haendler; keine PII; Fassungswechsel = neue Zeitreihe; POOR-Tage zaehlen nicht; Klassen/Mindest-Euro; Reihenfolge
(aelterer Tag nie nach neuerem); idempotent (zweimal auswerten, zwei Prozesse); Ablauf ueber speicher.verarbeiten;
keine Crawl-Funktion wird aufgerufen; Routen nur Super-Admin. Testdaten nur mit Praefix test-/t<hex>, Aufraeumen
am Ende. Alle Tage werden ausdruecklich uebergeben (kein Mitternachts-Effekt)."""
import asyncio
import inspect
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_befunde_runde17_termine import _module, welt  # noqa: E402,F401
from test_markt_20260926 import (  # noqa: E402
    APIFY, ENT, JOBS, K, NORM, SEG, SP, _aufraeumen as _aufraeumen_markt, _item, _modell, _pi, _segment,
)

D = _module("markt.deals")
AUS = _module("markt.auswertung")
BACKEND = Path(__file__).resolve().parent.parent
START = datetime(2026, 3, 1)
# Grundbestand: Median 20.000 EUR, guenstigstes 19.200 (4 % unter der Referenz -> kein Deal)
BASIS = (("a", 19200), ("b", 19600), ("c", 20000), ("d", 20400), ("e", 20800))


def _t(i):
    return (START + timedelta(days=i)).strftime("%Y-%m-%d")


def _aufraeumen(welt):
    _aufraeumen_markt(welt)
    muster = {"$regex": "^(test|dbg)-"}
    for coll in (K.HOTDEALS, K.HOTDEAL_EREIGNISSE, K.PRIVATE_DEALS, K.BERICHTE):
        welt.run(welt.db[coll].delete_many({"$or": [{"segment_id": muster}, {"model_id": muster}]}))


def _start(welt, **seg_extra):
    _aufraeumen(welt)
    welt.run(_module("indizes").markt_indizes(welt.db))     # Unique-Indizes wie beim Start (CAS des Zustands braucht sie)
    seg = {**_segment(welt.w), "max_items": 5, "version": 1, "definition_hash": "h1", **seg_extra}
    welt.run(welt.db[K.MODELLE].insert_one(_modell(welt.w)))
    welt.run(welt.db[K.SEGMENTE].insert_one(dict(seg)))
    return seg


def _doc(welt, seg, i, preise, *, neu=(), privat=(), dq="GOOD", dh="h1", version=1, top_n=True, offen=True, sample=None):
    """Tagesdokument wie speicher.verarbeiten es schreibt (Phase C): Preiszeilen, neue IDs, Fassung, Qualitaet."""
    s = welt.w.s
    zeilen = sorted(((f"t{s}{lid}", float(p)) for lid, p in preise), key=lambda x: x[1])
    kz = SP.kennzahlen([p for _, p in zeilen])
    ids = [lid for lid, _ in zeilen]
    tag = _t(i)
    doc = {"segment_id": seg["id"], "date": tag, "model_id": seg["model_id"], **kz,
           "listing_ids": ids, "listing_ids_alle": ids,
           "listings": [{"listing_id": lid, "rank": r, "price": p, "seller_type": "PRIVATE" if lid[11:] in privat else "DEALER"}
                        for r, (lid, p) in enumerate(zeilen, 1)],
           "new_in_sample_ids": [f"t{s}{x}" for x in neu], "data_quality": dq, "data_quality_grund": None if dq == "GOOD" else "fremdfahrzeuge",
           "market_depth": "FULL", "sample_completeness": "COMPLETE", "version": version, "definition_hash": dh,
           "top_n_bewiesen": top_n, "observed_at": f"{tag}T05:00:00+00:00", "last_run_at": f"{tag}T05:00:00+00:00",
           "last_valid_run_at": f"{tag}T05:00:00+00:00", "rows_soll": 5, "valid_runs": 1, "vergleich_vortag": _t(i - 1),
           "top5_changed": False, "disappeared_count": 0, "crawl_cost_usd": 0.01}
    if sample is not None:
        doc["sample_size"] = sample
    if offen:
        doc["hot_deals_offen"] = True
    welt.run(welt.db[K.TAGESSTATS].update_one({"segment_id": seg["id"], "date": tag}, {"$set": doc}, upsert=True))
    return doc


def _basis(welt, seg, tage=7, von=0, **kw):
    for i in range(von, von + tage):
        _doc(welt, seg, i, BASIS, offen=False, **kw)


def _auswerten(welt, seg, i):
    return welt.run(D.segment_tag_auswerten(welt.db, seg["id"], _t(i)))


def _ereignisse(welt, seg, lid):
    return welt.run(welt.db[K.HOTDEAL_EREIGNISSE].find({"segment_id": seg["id"], "listing_id": f"t{welt.w.s}{lid}"}, {"_id": 0})
                    .sort([("lauf_at", 1), ("at", 1)]).to_list(100))


def _deal(welt, seg, lid):
    return welt.run(welt.db[K.HOTDEALS].find_one({"segment_id": seg["id"], "listing_id": f"t{welt.w.s}{lid}"}, {"_id": 0}))


# ---------------------------------------------------------------- Regeln
def test_01_klassen_mindest_euro_und_liquiditaet():
    """Klassen nach Abschnitt 23 (DEAL 5-8 %, STRONG 8-12 %, EXTREME >= 12 %) UND Mindest-Euro, preisabhaengig."""
    assert D.mindest_eur(20000) == 750 and D.mindest_eur(15000) == 750 and D.mindest_eur(12000) == 600 and D.mindest_eur(6000) == 400
    assert D.klasse_bestimmen(19000, 20000) == ("DEAL", 1000.0, 5.0)
    assert D.klasse_bestimmen(18300, 20000)[0] == "STRONG" and D.klasse_bestimmen(17600, 20000)[0] == "EXTREME"
    assert D.klasse_bestimmen(19200, 20000)[0] is None, "4 % ist kein Deal"
    # 6.000-EUR-Auto: 5 % = 300 EUR < 400 EUR Mindestvorteil -> kein Deal; 7 % = 420 EUR -> Deal
    assert D.klasse_bestimmen(5700, 6000)[0] is None and D.klasse_bestimmen(5580, 6000)[0] == "DEAL"
    # 40.000-EUR-Auto: 5 % = 2.000 EUR -> Deal (750 EUR sind dort kein Hindernis)
    assert D.klasse_bestimmen(38000, 40000)[0] == "DEAL"
    assert D.MIN_BASIS_TAGE == 7 and D.MIN_BASIS_INSERATE == 5 and D.REFERENZ_FENSTER_TAGE == 30
    s = D.schwellen()
    assert s["klassen"][0] == {"klasse": "EXTREME", "ab_pct": 12.0} and s["mindest_eur_staffel"][0]["mindest_eur"] == 750
    # Liquiditaet: Umschlag und Top-5-Wechsel, UNKNOWN unter 3 Vergleichstagen
    tage = [{"sample_size": 5, "vergleich_vortag": "x", "new_in_sample_ids": ["n"], "disappeared_count": 1, "top5_changed": True,
             "listing_ids": [f"l{i}{j}" for j in range(5)]} for i in range(4)]
    assert D.liquiditaet_bewerten(tage)["stufe"] == "HIGH"
    ruhig = [{**t, "new_in_sample_ids": [], "disappeared_count": 0, "top5_changed": False, "listing_ids": ["a", "b", "c", "d", "e"]} for t in tage]
    liq = D.liquiditaet_bewerten(ruhig)
    assert liq["stufe"] == "LOW" and liq["mittlere_beobachtung_tage"] == 4.0, "fuenf immer gleiche Autos sind nicht liquide"
    assert D.liquiditaet_bewerten(tage[:2])["stufe"] == "UNKNOWN"
    assert D.liquiditaet_bewerten([{**t, "sample_size": 1} for t in tage])["stufe"] == "LOW"


def test_02_basis_zu_duenn_kein_deal(welt):
    """Unter 7 gueltigen Basistagen oder unter 5 verschiedenen Inseraten: kein Deal, kein Ereignis — der Tag
    wird trotzdem als ausgewertet markiert (Grund steht am Tagesdokument)."""
    seg = _start(welt)
    try:
        _basis(welt, seg, tage=6)
        _doc(welt, seg, 6, BASIS[1:] + (("x", 15000),), neu=("x",))
        r = _auswerten(welt, seg, 6)
        assert r["status"] == "ohne_basis" and r["grund"] == "zu_wenig_tage" and r["basis_tage"] == 6
        assert _deal(welt, seg, "x") is None and _ereignisse(welt, seg, "x") == []
        td = welt.run(welt.db[K.TAGESSTATS].find_one({"segment_id": seg["id"], "date": _t(6)}, {"_id": 0}))
        assert "hot_deals_offen" not in td and td["hot_deals"]["grund"] == "zu_wenig_tage" and td["hot_deals"]["basis_ok"] is False
        # 7 Tage, aber immer nur dieselben 3 Autos -> zu wenig verschiedene Inserate
        seg2 = {**seg, "id": f"{seg['id']}-b"}
        welt.run(welt.db[K.SEGMENTE].insert_one(dict(seg2)))
        for i in range(7):
            _doc(welt, seg2, i, BASIS[:3], offen=False)
        _doc(welt, seg2, 7, BASIS[:3] + (("y", 15000),), neu=("y",))
        r2 = _auswerten(welt, seg2, 7)
        assert r2["grund"] == "zu_wenig_inserate" and r2["basis_inserate"] == 3 and _deal(welt, seg2, "y") is None
        # mit dem 7. Basistag im ersten Segment reicht es
        _doc(welt, seg, 6, BASIS, offen=False)
        _doc(welt, seg, 7, BASIS[1:] + (("x", 15000),), neu=("x",))
        r3 = _auswerten(welt, seg, 7)
        assert r3["status"] == "ausgewertet" and r3["referenz_eur"] == 20000 and r3["basis_tage"] == 7
        assert _deal(welt, seg, "x")["klasse"] == "EXTREME"
    finally:
        _aufraeumen(welt)


def test_03_ereignisse_in_richtiger_reihenfolge(welt):
    """NEW -> STILL_HOT -> PRICE_DROP -> LEFT (ueber Schwelle) -> BECAME -> LEFT (nicht mehr im Sample) -> REMOVED.
    Ein Zustand je Inserat (nie jeden Tag ein neuer Deal), Historie nur eingefuegt, zweite Auswertung ohne Doppel."""
    seg = _start(welt)
    s, db = welt.w.s, welt.db
    try:
        _basis(welt, seg, tage=12)
        ohne_e = BASIS[:4]
        _doc(welt, seg, 12, ohne_e + (("x", 17500),), neu=("x",))
        assert _auswerten(welt, seg, 12)["ereignisse"] == {"NEW_HOT_DEAL": 1}
        erst = _deal(welt, seg, "x")
        assert erst["status"] == "ACTIVE" and erst["klasse"] == "EXTREME" and erst["detected_price"] == 17500 and erst["reference_price"] == 20000
        assert erst["deal_first_detected_tag"] == _t(12) and erst["diff_eur"] == 2500 and erst["diff_pct"] == 12.5 and erst["rank"] == 1
        _doc(welt, seg, 13, ohne_e + (("x", 17500),))
        assert _auswerten(welt, seg, 13)["ereignisse"] == {"STILL_HOT": 1}
        _doc(welt, seg, 14, ohne_e + (("x", 17000),))
        _auswerten(welt, seg, 14)
        d = _deal(welt, seg, "x")
        assert d["last_event"] == "PRICE_DROP_HOT_DEAL" and d["price_change_since_detection_eur"] == -500 and d["current_price"] == 17000
        _doc(welt, seg, 15, ohne_e + (("x", 19300),))        # 3,5 % -> nicht mehr heiss, aber noch im Sample
        _auswerten(welt, seg, 15)
        d = _deal(welt, seg, "x")
        assert d["status"] == "LEFT" and d["left_grund"] == "ueber_schwelle" and d["current_price"] == 19300
        _doc(welt, seg, 16, ohne_e + (("x", 18300),))        # 8,5 % -> wieder heiss (STRONG)
        _auswerten(welt, seg, 16)
        d = _deal(welt, seg, "x")
        assert d["status"] == "ACTIVE" and d["klasse"] == "STRONG" and d["hot_seit_tag"] == _t(16) and "left_grund" not in d
        _doc(welt, seg, 17, BASIS)                            # x nicht mehr unter den guenstigsten
        _auswerten(welt, seg, 17)
        assert _deal(welt, seg, "x")["left_grund"] == "nicht_mehr_im_sample"
        welt.run(db[K.LISTINGS].insert_one({"source": K.QUELLE, "listing_id": f"t{s}x", "active_state": "confirmed_removed"}))
        _doc(welt, seg, 18, BASIS)
        _auswerten(welt, seg, 18)
        ev = _ereignisse(welt, seg, "x")
        assert [e["typ"] for e in ev] == ["NEW_HOT_DEAL", "STILL_HOT", "PRICE_DROP_HOT_DEAL", "LEFT_HOT_ZONE", "BECAME_HOT_DEAL",
                                          "LEFT_HOT_ZONE", "REMOVED"]
        assert [e["tag"] for e in ev] == [_t(i) for i in range(12, 19)]
        assert ev[2]["price_vorher"] == 17500 and ev[2]["price"] == 17000 and ev[4]["klasse"] == "STRONG" and ev[4]["klasse_vorher"] == "EXTREME"
        d = _deal(welt, seg, "x")
        assert d["status"] == "REMOVED" and d["hot_phasen"] == 2 and d["tage_hot"] == 4 and d["deal_first_detected_tag"] == _t(12)
        assert welt.run(db[K.HOTDEALS].count_documents({"segment_id": seg["id"], "listing_id": f"t{s}x"})) == 1
        # zweite Auswertung desselben Tageswerts (Merker erneut gesetzt) und ein verspaeteter Lauf fuer einen
        # aelteren Tag (neuer Tageswert an Tag 14, nachdem Tag 18 schon ausgewertet ist): nichts doppelt, nichts zurueck
        ids_vorher = [e["id"] for e in ev]
        welt.run(db[K.TAGESSTATS].update_one({"segment_id": seg["id"], "date": _t(18)}, {"$set": {"hot_deals_offen": True}}))
        welt.run(db[K.TAGESSTATS].update_one({"segment_id": seg["id"], "date": _t(14)},
                                             {"$set": {"hot_deals_offen": True, "observed_at": f"{_t(15)}T00:10:00+00:00"}}))
        z = welt.run(D.auswerten_faellige(db, segment_ids=[seg["id"]]))
        assert z["offen"] == 2 and z["fehler"] == 0 and z["ereignisse"] == 0
        assert [e["id"] for e in _ereignisse(welt, seg, "x")] == ids_vorher, "Historie unveraendert"
        assert welt.run(db[K.TAGESSTATS].find_one({"segment_id": seg["id"], "date": _t(14)}))["hot_deals"]["grund"] == "neuerer_tag_ausgewertet"
        t18 = welt.run(db[K.TAGESSTATS].find_one({"segment_id": seg["id"], "date": _t(18)}))
        assert "hot_deals_offen" not in t18 and t18["hot_deals"]["ereignisse"] == {"REMOVED": 1}, "Zusammenfassung bleibt"
        assert _deal(welt, seg, "x")["status"] == "REMOVED"
        # die Tagesvereinigungen fuer die Berichte: heiss an den Tagen 12-14 und 16
        heiss_tage = [d["date"] for d in welt.run(db[K.TAGESSTATS].find({"segment_id": seg["id"], "hot_deal_ids_tag": f"t{s}x"}).to_list(50))]
        assert sorted(heiss_tage) == [_t(12), _t(13), _t(14), _t(16)]
        neu_tage = [d["date"] for d in welt.run(db[K.TAGESSTATS].find({"segment_id": seg["id"], "hot_deal_neu_ids": f"t{s}x"}).to_list(50))]
        assert sorted(neu_tage) == [_t(12), _t(16)]
    finally:
        _aufraeumen(welt)


def test_04_left_hot_zone_leerer_tag_und_ungueltiger_tag(welt):
    """Ein gueltiger leerer Tag (Marktluecke) laesst einen aktiven Deal die Zone verlassen; ein Tag mit nur
    ungueltigen Laeufen (ohne sample_size) wird nie ausgewertet und aendert nichts."""
    seg = _start(welt)
    db = welt.db
    try:
        _basis(welt, seg, tage=8)
        _doc(welt, seg, 8, BASIS[:4] + (("x", 17800),))
        _auswerten(welt, seg, 8)
        assert _deal(welt, seg, "x")["last_event"] == "BECAME_HOT_DEAL", "nicht heute neu im Sample -> BECAME"
        # Tag nur mit ungueltigen Laeufen: kein Tageswert, kein Merker
        welt.run(db[K.TAGESSTATS].insert_one({"segment_id": seg["id"], "date": _t(9), "invalid_runs": 1, "data_quality": "POOR",
                                              "data_quality_grund": "ungueltig", "model_id": seg["model_id"]}))
        assert _auswerten(welt, seg, 9) == {"status": "kein_tageswert"}
        assert _deal(welt, seg, "x")["status"] == "ACTIVE"
        # gueltiger leerer Tag
        welt.run(db[K.TAGESSTATS].update_one({"segment_id": seg["id"], "date": _t(10)}, {"$set": {
            "model_id": seg["model_id"], "sample_size": 0, "median_price": None, "listings": [], "listing_ids": [], "data_quality": "GOOD",
            "market_depth": "EMPTY", "version": 1, "definition_hash": "h1", "observed_at": f"{_t(10)}T05:00:00+00:00", "hot_deals_offen": True}},
            upsert=True))
        r = _auswerten(welt, seg, 10)
        assert r["status"] == "ausgewertet" and r["ereignisse"] == {"LEFT_HOT_ZONE": 1} and r["ids"] == []
        d = _deal(welt, seg, "x")
        assert d["status"] == "LEFT" and d["left_grund"] == "nicht_mehr_im_sample" and d["left_tag"] == _t(10)
        ev = _ereignisse(welt, seg, "x")
        assert ev[-1]["typ"] == "LEFT_HOT_ZONE" and ev[-1]["grund"] == "nicht_mehr_im_sample" and ev[-1]["price"] is None
    finally:
        _aufraeumen(welt)


def test_05_privat_und_haendler(welt, monkeypatch):
    """Privatangebote werden markiert und sind getrennt filterbar; die Zusammenfassung zaehlt 'davon privat'."""
    seg = _start(welt)
    heute = _t(9)
    monkeypatch.setattr(K, "heute_tag", lambda zeit=None: heute)
    try:
        _basis(welt, seg, tage=9)
        _doc(welt, seg, 9, BASIS[:3] + (("p", 17000), ("h", 17500)), neu=("p", "h"), privat=("p",))
        r = _auswerten(welt, seg, 9)
        assert sorted(r["ereignisse"].items()) == [("NEW_HOT_DEAL", 2)] and r["privat_ids"] == [f"t{welt.w.s}p"]
        p, h = _deal(welt, seg, "p"), _deal(welt, seg, "h")
        assert p["privat"] is True and p["seller_type"] == "PRIVATE" and h["privat"] is False and h["seller_type"] == "DEALER"
        ids = lambda x: [d["listing_id"][11:] for d in x["deals"]]  # noqa: E731
        m = seg["model_id"]
        assert ids(welt.run(D.liste(welt.db, model_id=m))) == ["p", "h"]
        assert ids(welt.run(D.liste(welt.db, model_id=m, privat=True))) == ["p"]
        assert ids(welt.run(D.liste(welt.db, model_id=m, privat=False))) == ["h"]
        z = welt.run(D.zusammenfassung(welt.db))
        assert z["tag"] == heute and z["davon_privat"] >= 1 and z["neue_privat_heute"] >= 1 and z["extreme"] >= 2
        assert z["modelle_geprueft"] >= 1 and z["modelle_gueltig"] >= 1
        ev = _ereignisse(welt, seg, "p")
        assert ev[0]["privat"] is True and ev[0]["seller_type"] == "PRIVATE"
    finally:
        _aufraeumen(welt)


def test_06_keine_pii(welt):
    """Zustand, Ereignisse und Liste tragen nur Fahrzeugfelder aus der Whitelist (PLZ/Ort) — keine Namen,
    Telefonnummern, seller_id oder Koordinaten, auch wenn das Inserat sie im Rohdatensatz hat."""
    seg = _start(welt)
    s, db = welt.w.s, welt.db
    try:
        welt.run(db[K.LISTINGS].insert_one({"source": K.QUELLE, "listing_id": f"t{s}p", "title": "BMW 320d Privat", "make": "BMW",
                                            "first_registration": "03/2020", "mileage_km": 71000, "postal_code": "80331", "city": "München",
                                            "url": "https://suchen.mobile.de/auto-inserat/p.html", "seller_type": "PRIVATE",
                                            "seller_id": 987654321, "seller_name": "Vera Privat", "phone": "0171 111111",
                                            "latitude": 48.13712, "longitude": 11.57541, "active_state": "seen",
                                            "segmente": {seg["id"]: {"first_seen_at": f"{_t(8)}T05:00:00+00:00"}}}))
        _basis(welt, seg, tage=9)
        _doc(welt, seg, 9, BASIS[:4] + (("p", 17000),), privat=("p",))
        _auswerten(welt, seg, 9)
        d = _deal(welt, seg, "p")
        assert d["city"] == "München" and d["postal_code"] == "80331" and d["ez_year"] == 2020 and d["mileage_km"] == 71000
        assert d["segment_first_seen_at"].startswith(_t(8))
        for x in (d, *_ereignisse(welt, seg, "p"), *welt.run(D.liste(db, model_id=seg["model_id"]))["deals"]):
            text = str(x)
            assert not [k for k in x if k.startswith("seller_") and k != "seller_type"], x
            assert "latitude" not in x and "longitude" not in x and "phone" not in x
            assert "Vera" not in text and "0171 111111" not in text and "987654321" not in text and "48.137" not in text
        q = inspect.getsource(D)
        assert "latitude" not in q and "seller_name" not in q and "phone" not in q and "seller_id" not in q
        assert "PRIVAT_FAHRZEUGFELDER" in inspect.getsource(D.segment_tag_auswerten)
    finally:
        _aufraeumen(welt)


def test_07_fassungswechsel_neue_zeitreihe(welt):
    """Tage einer anderen Fassung (version/definition_hash) sind keine Referenz — nach einem Fassungswechsel
    beginnt die Basis neu (hier: 10 Tage alte Fassung -> Basis 0 Tage -> kein Deal)."""
    seg = _start(welt, version=2, definition_hash="neu")
    try:
        _basis(welt, seg, tage=10, dh="alt", version=1)
        _doc(welt, seg, 10, BASIS[:4] + (("x", 15000),), neu=("x",), dh="neu", version=2)
        r = _auswerten(welt, seg, 10)
        assert r["grund"] == "zu_wenig_tage" and r["basis_tage"] == 0 and r["version"] == 2 and r["definition_hash"] == "neu"
        assert _deal(welt, seg, "x") is None
        ref = welt.run(D.referenz_berechnen(welt.db, seg, _t(10), (2, "neu")))
        assert ref["andere_fassung_tage"] == 10
        # dieselbe Fassung: Deal
        ref_alt = welt.run(D.referenz_berechnen(welt.db, seg, _t(10), (1, "alt")))
        assert ref_alt["basis_ok"] is True and ref_alt["referenz_eur"] == 20000
    finally:
        _aufraeumen(welt)


def test_08_poor_tage_zaehlen_nicht(welt):
    """Basis nur GOOD/MEDIUM: POOR-Tage (und nur monoton sortierte) zaehlen nicht zur Referenz; ein POOR-Tag
    selbst erzeugt kein Ereignis und laesst aktive Deals unveraendert."""
    seg = _start(welt)
    try:
        _basis(welt, seg, tage=6)
        _doc(welt, seg, 6, BASIS, dq="POOR", offen=False)
        _doc(welt, seg, 7, BASIS, top_n=False, offen=False)
        _doc(welt, seg, 8, BASIS[:4] + (("x", 15000),))
        r = _auswerten(welt, seg, 8)
        assert r["grund"] == "zu_wenig_tage" and r["basis_tage"] == 6, "POOR und 'nur monoton' zaehlen nicht"
        _doc(welt, seg, 8, BASIS, offen=False)
        _doc(welt, seg, 9, BASIS[:4] + (("x", 15000),))
        assert _auswerten(welt, seg, 9)["ereignisse"] == {"BECAME_HOT_DEAL": 1}
        # POOR-Tag: x fehlt, trotzdem kein LEFT — die Daten taugen nicht fuer ein Urteil
        _doc(welt, seg, 10, BASIS, dq="POOR")
        r = _auswerten(welt, seg, 10)
        assert r["status"] == "ohne_basis" and r["grund"] == "datenqualitaet" and _deal(welt, seg, "x")["status"] == "ACTIVE"
        # MEDIUM zaehlt: Fremdfahrzeug verworfen, aber brauchbar
        _doc(welt, seg, 11, BASIS[:4] + (("x", 15000),), dq="MEDIUM")
        assert _auswerten(welt, seg, 11)["ereignisse"] == {"STILL_HOT": 1}
    finally:
        _aufraeumen(welt)


def test_09_ablauf_ueber_speicher_und_merker(welt):
    """Echter Weg: speicher.verarbeiten setzt hot_deals_offen am Tagesdokument (Statistik-Tag ausdruecklich),
    die Auswertung arbeitet die Tage der Reihe nach ab und loescht den Merker; ein zweiter Durchlauf findet
    nichts mehr."""
    seg = _start(welt)
    s, db = welt.w.s, welt.db
    try:
        for i in range(8):
            items = [_item(f"t{s}{x}", p) for x, p in BASIS]
            if i == 7:
                items = [_pi(f"t{s}n", 16500)] + items[:4]
            welt.run(SP.verarbeiten(db, seg, NORM.listings_aus_items(items), beobachtet=(START + timedelta(days=i, hours=5)).replace(tzinfo=timezone.utc),
                                    tag=_t(i)))
        offen = welt.run(db[K.TAGESSTATS].count_documents({"segment_id": seg["id"], "hot_deals_offen": True}))
        assert offen == 8
        z = welt.run(D.auswerten_faellige(db, segment_ids=[seg["id"]]))
        assert z == {"offen": 8, "ausgewertet": 1, "ohne_basis": 7, "fehler": 0, "ereignisse": 1}
        n = _deal(welt, seg, "n")
        assert n["last_event"] == "NEW_HOT_DEAL" and n["privat"] is True and n["reference_price"] == 20000 and n["current_price"] == 16500
        assert welt.run(db[K.TAGESSTATS].count_documents({"segment_id": seg["id"], "hot_deals_offen": True})) == 0
        assert welt.run(D.auswerten_faellige(db, segment_ids=[seg["id"]]))["offen"] == 0
        # ein zweiter Lauf am selben Tag mit hoeherem Preis: Merker wieder gesetzt -> LEFT_HOT_ZONE
        items = [_pi(f"t{s}n", 19500)] + [_item(f"t{s}{x}", p) for x, p in BASIS[:4]]
        welt.run(SP.verarbeiten(db, seg, NORM.listings_aus_items(items), beobachtet=(START + timedelta(days=7, hours=17)).replace(tzinfo=timezone.utc),
                                tag=_t(7), lauf_tag=f"{_t(7)}#2"))
        z = welt.run(D.auswerten_faellige(db, segment_ids=[seg["id"]]))
        assert z["ausgewertet"] == 1 and _deal(welt, seg, "n")["status"] == "LEFT"
        assert [e["typ"] for e in _ereignisse(welt, seg, "n")] == ["NEW_HOT_DEAL", "LEFT_HOT_ZONE"]
        td = welt.run(db[K.TAGESSTATS].find_one({"segment_id": seg["id"], "date": _t(7)}))
        assert td["hot_deal_ids_tag"] == [f"t{s}n"] and td["hot_deals"]["ids"] == [], "am Tag heiss gewesen, im letzten Lauf nicht mehr"
    finally:
        _aufraeumen(welt)


def test_10_keine_crawl_funktion_wird_aufgerufen(welt, monkeypatch):
    """Hot Deals lesen nur gespeicherte Tageswerte: jede Crawl-Funktion schlaegt hier fehl — Auswertung,
    Liste, Zusammenfassung, Ereignisse und der Auswertungs-Durchlauf laufen trotzdem."""
    async def _verboten(*a, **k):
        raise AssertionError("Crawl-Funktion aufgerufen")
    for mod, name in ((APIFY, "lauf"), (JOBS, "job_sofort"), (JOBS, "tagesplan"), (JOBS, "einmal"), (JOBS, "verarbeiten_buendel"),
                      (SEG, "synchronisieren"), (ENT, "taeglich")):
        monkeypatch.setattr(mod, name, _verboten)
    seg = _start(welt)
    merker_vorher = welt.run(welt.db[K.KONFIG].find_one({"_id": K.AUSWERTUNG_DOK}))
    try:
        _basis(welt, seg, tage=7)
        _doc(welt, seg, 7, BASIS[:4] + (("x", 15000),), neu=("x",))
        echt = D.auswerten_faellige

        async def _nur_test(db, **kw):
            return await echt(db, segment_ids=[seg["id"]], **{k: v for k, v in kw.items() if k != "segment_ids"})
        monkeypatch.setattr(D, "auswerten_faellige", _nur_test)
        # Phase E: der Durchlauf friert danach die faelligen Berichte ein — im Test nur fuer das Testmodell
        BER = _module("markt.berichte")
        echt_ber = BER.faellige_finalisieren

        async def _nur_test_ber(db, **kw):
            return await echt_ber(db, model_ids=[seg["model_id"]], **{k: v for k, v in kw.items() if k != "model_ids"})
        monkeypatch.setattr(BER, "faellige_finalisieren", _nur_test_ber)
        erg = welt.run(AUS.durchlauf(welt.db))
        assert erg["hot_deals"]["ausgewertet"] == 1 and AUS.fehler_anzahl(erg) == 0
        assert welt.run(D.liste(welt.db, model_id=seg["model_id"]))["anzahl"] == 1
        welt.run(D.zusammenfassung(welt.db))
        assert welt.run(D.ereignisse(welt.db, seg["id"], f"t{welt.w.s}x"))["ereignisse"][0]["typ"] == "NEW_HOT_DEAL"
        assert welt.run(K.merker_lesen(welt.db, K.AUSWERTUNG_DOK))["letzter_lauf_at"]
    finally:
        # der Stand-Merker ist Betriebszustand, kein Testdatum — alten Stand wiederherstellen
        if merker_vorher:
            welt.run(welt.db[K.KONFIG].replace_one({"_id": K.AUSWERTUNG_DOK}, merker_vorher, upsert=True))
        else:
            welt.run(welt.db[K.KONFIG].delete_one({"_id": K.AUSWERTUNG_DOK}))
        _aufraeumen(welt)


def test_11_zwei_prozesse_idempotent(welt):
    """Zwei gleichzeitige Auswertungen desselben Tages legen kein Ereignis doppelt an (Unique-Index +
    Stand je Inserat); haelt ein anderer Prozess die Sperre, wertet der Durchlauf nicht aus."""
    IDX = _module("indizes")
    welt.run(IDX.markt_indizes(welt.db))
    info = welt.run(welt.db[K.HOTDEAL_EREIGNISSE].index_information())
    assert info["markt_hotdeal_ereignis"]["key"] == [("segment_id", 1), ("listing_id", 1), ("lauf_key", 1), ("typ", 1)]
    assert info["markt_hotdeal_ereignis"].get("unique")
    assert welt.run(welt.db[K.HOTDEALS].index_information())["markt_hotdeal_je_segment"].get("unique")
    ts_info = welt.run(welt.db[K.TAGESSTATS].index_information())
    assert ts_info["markt_tagesstat_hotdeal_offen"]["partialFilterExpression"] == {"hot_deals_offen": True}
    assert "market_hot_deals" not in " ".join(IDX.MARKT_UNIQUE_KRITISCH)
    seg = _start(welt)
    db = welt.db
    try:
        _basis(welt, seg, tage=7)
        _doc(welt, seg, 7, BASIS[:4] + (("x", 15000),), neu=("x",))

        async def _zwei():
            return await asyncio.gather(D.segment_tag_auswerten(db, seg["id"], _t(7)), D.segment_tag_auswerten(db, seg["id"], _t(7)))
        welt.run(_zwei())
        assert len(_ereignisse(welt, seg, "x")) == 1 and welt.run(db[K.HOTDEALS].count_documents({"segment_id": seg["id"]})) == 1
        # Sperre von einem "anderen Server" gehalten -> kein Durchlauf
        from job_lock import acquire, release
        token = welt.run(acquire(db, AUS.SPERRE, ttl_seconds=60))
        try:
            assert welt.run(AUS.durchlauf(db)) == {"gesperrt": True}
        finally:
            welt.run(release(db, AUS.SPERRE, token))
    finally:
        _aufraeumen(welt)


def test_12_liste_sortierung_und_filter(welt, monkeypatch):
    """Abschnitt 26: Sortierung nach Vorteil %/EUR, neueste, privat, Modell, EZ, km, Liquiditaet, Klasse; Filter
    Klasse und Status (aktuell = ACTIVE in aktiven Segmenten, alle = Historie)."""
    seg = _start(welt)
    heute = _t(9)
    monkeypatch.setattr(K, "heute_tag", lambda zeit=None: heute)
    s, db = welt.w.s, welt.db
    try:
        for lid, km, ez in (("p", 90000, "05/2019"), ("q", 60000, "03/2021")):
            welt.run(db[K.LISTINGS].insert_one({"source": K.QUELLE, "listing_id": f"t{s}{lid}", "mileage_km": km, "first_registration": ez,
                                                "make": "BMW", "active_state": "seen"}))
        _basis(welt, seg, tage=9)
        _doc(welt, seg, 9, BASIS[:3] + (("p", 17000), ("q", 18300)), neu=("q",), privat=("q",))
        _auswerten(welt, seg, 9)
        ids = lambda **kw: [d["listing_id"][11:] for d in welt.run(D.liste(db, model_id=seg["model_id"], **kw))["deals"]]  # noqa: E731
        assert ids() == ["p", "q"] and ids(sort="vorteil_eur") == ["p", "q"] and ids(sort="klasse") == ["p", "q"]
        assert ids(sort="privat") == ["q", "p"] and ids(sort="km") == ["q", "p"] and ids(sort="ez") == ["q", "p"]
        assert set(ids(sort="neueste")) == {"p", "q"} and set(ids(sort="liquiditaet")) == {"p", "q"} and set(ids(sort="modell")) == {"p", "q"}
        assert ids(klasse="STRONG") == ["q"] and ids(klasse="EXTREME") == ["p"] and ids(make="bmw") == ["p", "q"] and ids(make="Audi") == []
        assert ids(ez=2021) == ["q"] and ids(km_min=70000) == ["p"] and ids(heute_neu=True) == ["p", "q"]
        liste = welt.run(D.liste(db, model_id=seg["model_id"]))
        assert liste["deals"][0]["heute_neu"] is True and liste["deals"][0]["stand_alter_tage"] == 0 and "unfallfrei" in liste["hinweis"]
        # Segment inaktiv (z. B. fruehere Fassung): nicht mehr 'aktuell', aber in der Historie
        welt.run(db[K.SEGMENTE].update_one({"id": seg["id"]}, {"$set": {"enabled": False}}))
        assert ids() == [] and ids(status="alle") == ["p", "q"]
        assert set(D.SORTIERUNGEN) == {"vorteil_pct", "vorteil_eur", "neueste", "privat", "modell", "ez", "km", "liquiditaet", "klasse"}
    finally:
        _aufraeumen(welt)


def test_13_routen_nur_super_admin_und_getrennt_vom_hauptweg():
    """Alle Hot-Deal-Routen haengen an current_super_admin; die Firmen-Routen (routes/markt.py), die Karte und
    die Chancen wissen nichts von Hot Deals; der Auswertungs-Worker startet in server.py ohne Takt-Pflicht."""
    r = (BACKEND / "routes" / "markt_admin.py").read_text(encoding="utf-8")
    for pfad in ('"/admin/market/hot-deals"', '"/admin/market/hot-deals/ereignisse"', '"/admin/market/hot-deals/auswerten"'):
        assert pfad in r
        kopf = r.split(pfad)[1].split("\n\n")[0]
        assert "current_super_admin" in kopf and "Depends(current_admin)" not in kopf, pfad
    firmen = (BACKEND / "routes" / "markt.py").read_text(encoding="utf-8").lower()
    assert "hot" not in firmen and "deals" not in firmen
    ABF = _module("markt.abfrage")
    for fn in (ABF.karte, ABF.chancen, ABF.segment_listings, SP.chancen_ableiten):
        q = inspect.getsource(fn)
        assert "HOTDEAL" not in q and "hot_deal" not in q, fn.__name__
    for datei in ("routes/listings.py", "routes/contracts.py", "mobile_service.py", "pdf_service.py"):
        p = BACKEND / datei
        if p.exists():
            assert "hot_deal" not in p.read_text(encoding="utf-8").lower(), datei
    server = (BACKEND / "server.py").read_text(encoding="utf-8")
    assert '_worker_starten("markt_auswertung"' in server and 'WORKER_TAKT_S["markt_auswertung"]' not in server
    assert "wartung.aktiv_async(db)" in inspect.getsource(AUS.worker_forever)
    assert "markt_auswertung_fehler" == AUS.ALARM and "_alarm(db, ALARM" in inspect.getsource(AUS.worker_forever)
    # Lesewege schreiben nicht
    for fn in (D.liste, D.zusammenfassung, D.ereignisse):
        q = inspect.getsource(fn)
        assert not any(v in q for v in ("insert_one", "update_one", "update_many", "delete_one", "delete_many")), fn.__name__
    assert K.HOTDEALS == "market_hot_deals" and K.HOTDEAL_EREIGNISSE == "market_hot_deal_events"
    # Ereignisse werden nur eingefuegt ($setOnInsert), nie geaendert
    assert '"$setOnInsert"' in inspect.getsource(D._ereignis) and "$set\"" not in inspect.getsource(D._ereignis)


# ---------------------------------------------------------------- Pruefbefunde Phase D/E (27.09.2026)
def _tagesdoc(welt, seg, i):
    return welt.run(welt.db[K.TAGESSTATS].find_one({"segment_id": seg["id"], "date": _t(i)}, {"_id": 0, "laeufe": 0}))


def test_14_b0_leerer_erstlauf_des_laufenden_tages_schliesst_keine_deals(welt):
    """Pruefbefund B0: ein leerer ERSTER Lauf des laufenden Tages stellt nur vorlaeufig den Tageswert (ein spaeterer
    Lauf mit Treffern ersetzt ihn, P5). Er schliesst keine aktiven Deals — kein dauerhaftes LEFT/BECAME-Paar, keine
    zweite heisse Phase, kein Schein-'heute neu'. Nach Tagesende gilt ein leer gebliebener Tag weiter als Marktluecke
    (LEFT_HOT_ZONE, wie test_04). Echter Weg ueber speicher.verarbeiten, Tage und Uhrzeiten ausdruecklich."""
    seg = _start(welt)
    s, db = welt.w.s, welt.db
    try:
        _basis(welt, seg, tage=12)
        _doc(welt, seg, 12, BASIS[:4] + (("x", 17500),), neu=("x",))
        assert _auswerten(welt, seg, 12)["ereignisse"] == {"NEW_HOT_DEAL": 1}
        # Tag 13 (2026-03-14), 08:00 deutscher Zeit: gueltiger, aber leerer erster Lauf (Aussetzer der Buendel-URL)
        acht = datetime(2026, 3, 14, 7, 0, tzinfo=timezone.utc)
        assert K.heute_tag(acht) == _t(13)
        welt.run(SP.verarbeiten(db, seg, [], beobachtet=acht, tag=_t(13)))
        td = _tagesdoc(welt, seg, 13)
        assert td["sample_size"] == 0 and td["hot_deals_offen"] is True and td["data_quality"] == "GOOD"
        z = welt.run(D.auswerten_faellige(db, segment_ids=[seg["id"]], jetzt=acht + timedelta(minutes=5)))
        assert z["offen"] == 0 and z["ereignisse"] == 0, "leerer Tageswert des laufenden Tages wartet"
        assert welt.run(D.segment_tag_auswerten(db, seg["id"], _t(13), jetzt=acht + timedelta(minutes=5))) == {"status": "leer_vorlaeufig"}
        assert _deal(welt, seg, "x")["status"] == "ACTIVE" and _tagesdoc(welt, seg, 13)["hot_deals_offen"] is True
        assert "hot_deals" not in _tagesdoc(welt, seg, 13)
        # 10:00: zweiter Lauf desselben Tages mit Treffern stellt jetzt den Tageswert
        zehn = acht + timedelta(hours=2)
        items = [_item(f"t{s}{x}", p) for x, p in BASIS[:4]] + [_item(f"t{s}x", 17500)]
        welt.run(SP.verarbeiten(db, seg, NORM.listings_aus_items(sorted(items, key=lambda it: it["priceGross"])), beobachtet=zehn,
                                tag=_t(13), lauf_tag=f"{_t(13)}#2"))
        z = welt.run(D.auswerten_faellige(db, segment_ids=[seg["id"]], jetzt=zehn + timedelta(minutes=5)))
        assert z["ausgewertet"] == 1 and z["ereignisse"] == 1
        assert [e["typ"] for e in _ereignisse(welt, seg, "x")] == ["NEW_HOT_DEAL", "STILL_HOT"], "kein LEFT/BECAME-Paar"
        d = _deal(welt, seg, "x")
        assert d["status"] == "ACTIVE" and d["hot_phasen"] == 1 and d["hot_seit_tag"] == _t(12) and d["last_event"] == "STILL_HOT"
        td = _tagesdoc(welt, seg, 13)
        assert "hot_deals_offen" not in td and f"t{s}x" in td["hot_deal_ids_tag"] and f"t{s}x" not in (td.get("hot_deal_neu_ids") or [])
        # Tag 14 bleibt leer: waehrend des Tages vorlaeufig, nach Mitternacht Marktluecke -> LEFT (nicht mehr im Sample)
        tag14 = datetime(2026, 3, 15, 7, 0, tzinfo=timezone.utc)
        welt.run(SP.verarbeiten(db, seg, [], beobachtet=tag14, tag=_t(14)))
        assert welt.run(D.auswerten_faellige(db, segment_ids=[seg["id"]], jetzt=tag14 + timedelta(hours=8)))["offen"] == 0
        assert _deal(welt, seg, "x")["status"] == "ACTIVE"
        nach_mitternacht = datetime(2026, 3, 15, 23, 10, tzinfo=timezone.utc)      # 00:10 am 16.03. deutscher Zeit
        z = welt.run(D.auswerten_faellige(db, segment_ids=[seg["id"]], jetzt=nach_mitternacht))
        assert z["offen"] == 1 and z["ereignisse"] == 1
        d = _deal(welt, seg, "x")
        assert d["status"] == "LEFT" and d["left_grund"] == "nicht_mehr_im_sample" and d["left_tag"] == _t(14)
        assert [e["typ"] for e in _ereignisse(welt, seg, "x")] == ["NEW_HOT_DEAL", "STILL_HOT", "LEFT_HOT_ZONE"]
    finally:
        _aufraeumen(welt)


def test_15_b0_neue_deals_heute_zaehlt_deals_nicht_ereignisse(welt, monkeypatch):
    """Pruefbefund B0 (Zusatz): 'Neue Deals heute' zaehlt verschiedene Deals (Segment, Inserat) — eine zweite heisse
    Phase desselben Inserats am selben Tag ist kein zweiter neuer Deal."""
    seg = _start(welt)
    heute = _t(9)
    monkeypatch.setattr(K, "heute_tag", lambda zeit=None: heute)
    s, db = welt.w.s, welt.db
    try:
        vorher = welt.run(D.zusammenfassung(db))
        basis = {"segment_id": seg["id"], "model_id": seg["model_id"], "tag": heute, "privat": True}
        welt.run(db[K.HOTDEAL_EREIGNISSE].insert_many([
            {**basis, "id": "e1", "listing_id": f"t{s}x", "typ": "BECAME_HOT_DEAL", "lauf_key": f"{heute}T05:00:00+00:00"},
            {**basis, "id": "e2", "listing_id": f"t{s}x", "typ": "LEFT_HOT_ZONE", "lauf_key": f"{heute}T09:00:00+00:00"},
            {**basis, "id": "e3", "listing_id": f"t{s}x", "typ": "BECAME_HOT_DEAL", "lauf_key": f"{heute}T15:00:00+00:00"},
            {**basis, "id": "e4", "listing_id": f"t{s}y", "typ": "NEW_HOT_DEAL", "lauf_key": f"{heute}T05:00:00+00:00", "privat": False}]))
        nachher = welt.run(D.zusammenfassung(db))
        assert nachher["neue_deals_heute"] - vorher["neue_deals_heute"] == 2, "x zweimal heiss geworden = ein Deal, dazu y"
        assert nachher["neue_privat_heute"] - vorher["neue_privat_heute"] == 1
    finally:
        _aufraeumen(welt)


def test_16_b1_liquiditaet_nur_aus_gueltigen_tagen(welt):
    """Pruefbefund B1: die Liquiditaet am Deal (und damit die Sortierung 'Liquiditaet') rechnet nur mit gueltigen
    Tagen (GOOD/MEDIUM) wie die Berichte — POOR-Tage mit Mini-Stichprobe und vielen 'verschwundenen' Inseraten
    taeuschen sonst Umschlag vor (hier LOW statt HIGH)."""
    seg = _start(welt)
    db = welt.db
    try:
        _basis(welt, seg, tage=7)                                  # immer dieselben 5 Autos: nicht liquide
        for i in (7, 8, 9):
            _doc(welt, seg, i, ((f"p{i}", 19000),), neu=(f"p{i}",), dq="POOR", offen=False)
            welt.run(db[K.TAGESSTATS].update_one({"segment_id": seg["id"], "date": _t(i)},
                                                 {"$set": {"disappeared_count": 4, "top5_changed": True}}))
        ref = welt.run(D.referenz_berechnen(db, seg, _t(10), (1, "h1")))
        assert ref["basis_tage"] == 7 and ref["basis_ok"] is True
        assert ref["liquiditaet"]["stufe"] == "LOW" and ref["liquiditaet"]["tage"] == 7, ref["liquiditaet"]
        assert D.liquiditaet_bewerten([_tagesdoc(welt, seg, i) for i in range(10)])["stufe"] == "HIGH", "mit POOR-Tagen waere es HIGH"
        _doc(welt, seg, 10, BASIS[:4] + (("x", 17000),), neu=("x",))
        _auswerten(welt, seg, 10)
        d = _deal(welt, seg, "x")
        assert d["liquiditaet"] == "LOW" and d["liquiditaet_rang"] == D.LIQ_RANG["LOW"]
    finally:
        _aufraeumen(welt)


def test_17_b2_fehler_eines_tages_stellt_neuere_tage_desselben_segments_zurueck(welt, monkeypatch):
    """Pruefbefund B2: scheitert (S, D) kurz (z. B. Wahl im Replikat-Set), bleibt (S, D+1) im selben Durchlauf offen
    und kommt im naechsten Durchlauf NACH D dran — D wird nicht als 'neuerer_tag_ausgewertet' ohne Ereignisse
    geschlossen, NEW bleibt NEW am richtigen Tag."""
    seg = _start(welt)
    s = welt.w.s
    try:
        _basis(welt, seg, tage=7)
        _doc(welt, seg, 7, BASIS[:4] + (("x", 17000),), neu=("x",))
        _doc(welt, seg, 8, BASIS[:4] + (("x", 17000),))
        echt = D.segment_tag_auswerten
        aufrufe = []

        async def _einmal_gestoert(db, seg_id, tag, **kw):
            aufrufe.append(tag)
            if tag == _t(7) and aufrufe.count(_t(7)) == 1:
                raise RuntimeError("NotPrimary (Test)")
            return await echt(db, seg_id, tag, **kw)
        monkeypatch.setattr(D, "segment_tag_auswerten", _einmal_gestoert)
        z = welt.run(D.auswerten_faellige(welt.db, segment_ids=[seg["id"]]))
        assert z["fehler"] == 1 and z.get("zurueckgestellt") == 1 and z["ausgewertet"] == 0 and aufrufe == [_t(7)]
        t8 = _tagesdoc(welt, seg, 8)
        assert t8["hot_deals_offen"] is True and "hot_deals" not in t8
        z = welt.run(D.auswerten_faellige(welt.db, segment_ids=[seg["id"]]))
        assert z["ausgewertet"] == 2 and z["fehler"] == 0 and "zurueckgestellt" not in z
        assert [e["typ"] for e in _ereignisse(welt, seg, "x")] == ["NEW_HOT_DEAL", "STILL_HOT"]
        assert _deal(welt, seg, "x")["deal_first_detected_tag"] == _t(7)
        t7 = _tagesdoc(welt, seg, 7)
        assert t7["hot_deals"]["grund"] == "" and t7["hot_deal_ids_tag"] == [f"t{s}x"] and t7["hot_deal_neu_ids"] == [f"t{s}x"]
    finally:
        _aufraeumen(welt)


def test_18_b10_schreibpause_haelt_auswertung_und_berichte_an(welt, monkeypatch):
    """Pruefbefund B10: beginnt eine Schreibpause (Sicherung/Restore) waehrend des Durchlaufs, haelt die Auswertung
    vor dem naechsten Tagesdokument an (Rest bleibt offen), friert keine Berichte ein und schreibt keinen Stand-Merker."""
    WART = _module("wartung")
    BER = _module("markt.berichte")
    seg = _start(welt)
    merker_vorher = welt.run(welt.db[K.KONFIG].find_one({"_id": K.AUSWERTUNG_DOK}))
    try:
        _basis(welt, seg, tage=7)
        _doc(welt, seg, 7, BASIS[:4] + (("x", 17000),), neu=("x",))
        _doc(welt, seg, 8, BASIS[:4] + (("x", 17000),))
        pruefungen = []

        async def _pause_ab_zweiter_pruefung(db, methode="POST"):
            pruefungen.append(methode)
            return len(pruefungen) >= 2
        monkeypatch.setattr(WART, "aktiv_async", _pause_ab_zweiter_pruefung)
        z = welt.run(D.auswerten_faellige(welt.db, segment_ids=[seg["id"]]))
        assert z.get("wartung") is True and z["ausgewertet"] == 1 and len(pruefungen) == 2
        assert _tagesdoc(welt, seg, 8)["hot_deals_offen"] is True and "hot_deals" not in _tagesdoc(welt, seg, 8)
        # Durchlauf (Worker/Admin-Knopf): Pause -> keine Berichte, kein Merker
        echt = D.auswerten_faellige

        async def _nur_test(db, **kw):
            return await echt(db, segment_ids=[seg["id"]], **{k: v for k, v in kw.items() if k != "segment_ids"})
        monkeypatch.setattr(D, "auswerten_faellige", _nur_test)
        berichte_aufrufe = []

        async def _berichte(db, **kw):
            berichte_aufrufe.append(kw)
            return {"perioden": 0, "fehler": 0}
        monkeypatch.setattr(BER, "faellige_finalisieren", _berichte)

        async def _immer_pause(db, methode="POST"):
            return True
        monkeypatch.setattr(WART, "aktiv_async", _immer_pause)
        erg = welt.run(AUS.durchlauf(welt.db))
        assert erg.get("wartung") is True and "berichte" not in erg and berichte_aufrufe == []
        assert erg["hot_deals"]["ausgewertet"] == 0 and _tagesdoc(welt, seg, 8)["hot_deals_offen"] is True
        assert welt.run(welt.db[K.KONFIG].find_one({"_id": K.AUSWERTUNG_DOK})) == merker_vorher, "kein Stand-Merker in der Pause"
        # nach der Pause macht der naechste Durchlauf weiter
        monkeypatch.setattr(WART, "aktiv_async", lambda db, methode="POST": _nie_pause())
        erg = welt.run(AUS.durchlauf(welt.db))
        assert erg["hot_deals"]["ausgewertet"] == 1 and len(berichte_aufrufe) == 1 and "wartung" not in erg
    finally:
        if merker_vorher:
            welt.run(welt.db[K.KONFIG].replace_one({"_id": K.AUSWERTUNG_DOK}, merker_vorher, upsert=True))
        else:
            welt.run(welt.db[K.KONFIG].delete_one({"_id": K.AUSWERTUNG_DOK}))
        _aufraeumen(welt)


async def _nie_pause():
    return False


def test_19_b11_fehlerfreier_durchlauf_schliesst_alarm_prozessunabhaengig(welt, monkeypatch):
    """Pruefbefund B11: den Alarm 'markt_auswertung_fehler' schliesst jeder fehlerfreie Durchlauf — auch in einem
    anderen Prozess oder nach einem Neustart (vorher nur der Prozess mit dem lokalen Merker). Ein Durchlauf mit
    Fehlern schliesst ihn nicht."""
    BET = _module("betrieb")
    BER = _module("markt.berichte")
    seg = _start(welt)
    db = welt.db
    alt = welt.run(db.betriebsalarme.find({"typ": AUS.ALARM, "ref": "auswertung"}).to_list(100))
    merker_vorher = welt.run(db[K.KONFIG].find_one({"_id": K.AUSWERTUNG_DOK}))
    offen = lambda: welt.run(db.betriebsalarme.count_documents({"typ": AUS.ALARM, "ref": "auswertung", "offen": True}))  # noqa: E731
    try:
        welt.run(db.betriebsalarme.delete_many({"typ": AUS.ALARM, "ref": "auswertung"}))
        welt.run(BET.alarm(db, AUS.ALARM, ref="auswertung", fehler="Test: frueherer Prozess vor dem Neustart"))
        assert offen() == 1
        echt = D.auswerten_faellige

        async def _nur_test(db, **kw):
            return await echt(db, segment_ids=[seg["id"]], **{k: v for k, v in kw.items() if k != "segment_ids"})
        monkeypatch.setattr(D, "auswerten_faellige", _nur_test)
        ber_fehler = {"n": 1}

        async def _berichte(db, **kw):
            return {"perioden": 1, "fehler": ber_fehler["n"]}
        monkeypatch.setattr(BER, "faellige_finalisieren", _berichte)
        erg = welt.run(AUS.durchlauf(db))                          # mit Fehler: Alarm bleibt offen
        assert AUS.fehler_anzahl(erg) == 1 and offen() == 1
        ber_fehler["n"] = 0
        erg = welt.run(AUS.durchlauf(db))                          # fehlerfrei (dieser "Prozess" hat keinen Merker)
        assert AUS.fehler_anzahl(erg) == 0 and offen() == 0
        assert "alarm_offen" not in inspect.getsource(AUS.worker_forever)
    finally:
        welt.run(db.betriebsalarme.delete_many({"typ": AUS.ALARM, "ref": "auswertung"}))
        if alt:
            welt.run(db.betriebsalarme.insert_many(alt))
        if merker_vorher:
            welt.run(db[K.KONFIG].replace_one({"_id": K.AUSWERTUNG_DOK}, merker_vorher, upsert=True))
        else:
            welt.run(db[K.KONFIG].delete_one({"_id": K.AUSWERTUNG_DOK}))
        _aufraeumen(welt)


def test_20_b14_liste_alle_nur_im_zeitfenster_mit_index(welt, monkeypatch):
    """Pruefbefund B14: 'alle (auch verlassene)' liest nur Zustaende mit letztem Ereignis in den letzten
    ALLE_FENSTER_TAGE Tagen — ueber den Index markt_hotdeal_letztes_ereignis statt Vollscan der nie bereinigten
    Historie. 'aktuell' bleibt unveraendert."""
    seg = _start(welt)
    heute = "2026-09-27"
    monkeypatch.setattr(K, "heute_tag", lambda zeit=None: heute)
    s, db = welt.w.s, welt.db
    try:
        info = welt.run(db[K.HOTDEALS].index_information())
        assert info["markt_hotdeal_letztes_ereignis"]["key"] == [("last_event_tag", -1)]
        gemeinsam = {"segment_id": seg["id"], "model_id": seg["model_id"], "diff_pct": 10.0, "diff_eur": 2000.0}
        welt.run(db[K.HOTDEALS].insert_many([
            {**gemeinsam, "listing_id": f"t{s}alt", "status": "LEFT", "last_event_tag": "2026-03-01"},
            {**gemeinsam, "listing_id": f"t{s}neu", "status": "LEFT", "last_event_tag": "2026-09-20"},
            {**gemeinsam, "listing_id": f"t{s}akt", "status": "ACTIVE", "last_event_tag": "2026-09-27", "diff_pct": 12.0}]))
        r = welt.run(D.liste(db, status="alle", model_id=seg["model_id"]))
        assert [d["listing_id"][11:] for d in r["deals"]] == ["akt", "neu"] and r["fenster_von"] == "2026-06-29"
        assert [d["listing_id"][11:] for d in welt.run(D.liste(db, status="aktuell", model_id=seg["model_id"]))["deals"]] == ["akt"]
        assert welt.run(D.liste(db, status="aktuell"))["fenster_von"] is None
        # ohne Modell/Segment: Indexbereich statt Vollscan (Standardsortierung Vorteil %)
        plan = welt.run(db.command("explain", {"find": K.HOTDEALS, "filter": {"last_event_tag": {"$gte": "2026-06-29"}},
                                               "sort": {"diff_pct": -1, "diff_eur": -1}, "limit": 300}, verbosity="queryPlanner"))
        assert "COLLSCAN" not in str(plan["queryPlanner"]["winningPlan"]) and "markt_hotdeal_letztes_ereignis" in str(plan)
    finally:
        _aufraeumen(welt)
