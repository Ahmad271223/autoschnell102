# -*- coding: utf-8 -*-
"""Private Deals (Wunsch Ahmad 26.09.2026 abends): die 3 guenstigsten PRIVATangebote je
Marktsegment — ausschliesslich aus dem Sample, das die Marktanalyse ohnehin je Lauf abruft.
Kein eigener Crawl, keine neuen Segmente/Jobs, keine Zusatzkosten; nur der Super-Admin sieht sie.

Alle Tests laufen ohne Apify (Fixtures aus test_markt_20260926, Praefix test-/dbg-).
"""
import inspect
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_befunde_runde17_termine import _module, welt  # noqa: E402,F401
from test_markt_20260926 import (  # noqa: E402
    ABF, APIFY, BUD, JOBS, K, NORM, SP, _aufraeumen as _aufraeumen_markt, _eigenen_beanspruchen, _item, _modell, _segment, _tag,
)


def _privat(lid, preis, plz="30159", ort="Hannover", **kw):
    """Privatangebot — mit Name/Telefon/Koordinaten im Rohdatensatz, die NIE im Deal landen duerfen."""
    return {**_item(lid, preis, **kw), "seller": {"name": "Vera Privat", "type": "PRIVATE", "phone": "0171 111111", "country": "DE"},
            "sellerId": 99, "zip": plz, "location": ort, "latitude": 52.37123, "longitude": 9.73456}


def _aufraeumen(welt):
    _aufraeumen_markt(welt)
    welt.run(welt.db[K.PRIVATE_DEALS].delete_many({"$or": [{"segment_id": {"$regex": "^(test|dbg)-"}}, {"model_id": {"$regex": "^(test|dbg)-"}}]}))


def _lauf(welt, seg, items, **kw):
    return welt.run(SP.verarbeiten(welt.db, seg, NORM.listings_aus_items(items), **kw))


def _deals(welt, seg, **filt):
    return welt.run(welt.db[K.PRIVATE_DEALS].find({"segment_id": seg["id"], **filt}, {"_id": 0}).sort([("current_rank_private", 1)]).to_list(50))


def _start(welt):
    _aufraeumen(welt)
    seg = _segment(welt.w)
    welt.run(welt.db[K.MODELLE].insert_one(_modell(welt.w)))
    welt.run(welt.db[K.SEGMENTE].insert_one(dict(seg)))
    return seg


# ---------------------------------------------------------------- Ableitung
def test_01_nur_private_angebote(welt):
    """(1) Nur seller_type PRIVATE kommt in die Privat-Top-3; Haendler im selben Sample nicht."""
    w, db = welt.w, welt.db
    seg, s = _start(welt), w.s
    _lauf(welt, seg, [_item(f"t{s}h1", 6000), _privat(f"t{s}p1", 7000), _item(f"t{s}h2", 7500), _privat(f"t{s}p2", 9000), _item(f"t{s}h3", 9900)])
    d = _deals(welt, seg)
    assert [x["listing_id"] for x in d] == [f"t{s}p1", f"t{s}p2"] and [x["current_rank_private"] for x in d] == [1, 2]
    assert all(x["currently_top3"] is True for x in d)
    # Rang im Gesamtsample (unter allen Verkaeufern) bleibt sichtbar: p1 ist Nr. 2, p2 Nr. 4
    assert [x["rank_in_sample"] for x in d] == [2, 4]
    st = welt.run(db[K.SEGMENTSTATS].find_one({"_id": seg["id"]}, {"_id": 0}))
    assert st["private_anzahl_im_sample"] == 2 and [t["listing_id"] for t in st["private_top3"]] == [f"t{s}p1", f"t{s}p2"]
    # Segment-Median des Laufs (7500) und Abstand
    assert d[0]["segment_median"] == 7500 and d[0]["difference_to_segment_median_eur"] == -500 and round(d[0]["difference_to_segment_median_pct"], 2) == -6.67
    _aufraeumen(welt)


def test_02_haendler_nie_enthalten(welt):
    """(2) Ein Sample nur aus Haendlern liefert KEINEN Deal — die Statistik sagt 'keine Privatangebote'."""
    w, db = welt.w, welt.db
    seg, s = _start(welt), w.s
    _lauf(welt, seg, [_item(f"t{s}h1", 6000), _item(f"t{s}h2", 7000), _item(f"t{s}h3", 8000), _item(f"t{s}h4", 9000)])
    assert _deals(welt, seg) == []
    st = welt.run(db[K.SEGMENTSTATS].find_one({"_id": seg["id"]}, {"_id": 0}))
    assert st["private_top3"] == [] and st["private_anzahl_im_sample"] == 0 and st["private_stand_at"]
    block = welt.run(ABF.segment_private_deals(db, seg["id"]))
    assert block["top3"] == [] and block["keine_privaten"] is True and block["stale"] is False and block["sample_size"] == 4
    _aufraeumen(welt)


def test_03_exakt_die_guenstigsten_drei(welt):
    """(3) Fuenf Privatangebote -> genau die drei guenstigsten, in Preisreihenfolge #1-#3."""
    w = welt.w
    seg, s = _start(welt), w.s
    _lauf(welt, seg, [_privat(f"t{s}a", 7000), _privat(f"t{s}b", 8000), _item(f"t{s}h", 8500), _privat(f"t{s}c", 9000),
                      _privat(f"t{s}d", 10000), _privat(f"t{s}e", 12000)])
    d = _deals(welt, seg)
    assert [(x["listing_id"], x["current_rank_private"], x["current_price"]) for x in d] == \
        [(f"t{s}a", 1, 7000), (f"t{s}b", 2, 8000), (f"t{s}c", 3, 9000)]
    assert welt.run(welt.db[K.PRIVATE_DEALS].count_documents({"segment_id": seg["id"]})) == 3, "d und e sind keine Deals"
    assert all(x["best_rank_private"] == x["current_rank_private"] and x["first_price_top3"] == x["current_price"] == x["lowest_price_seen"] for x in d)
    assert all(x["first_entered_top3_tag"] == _tag(0) and x["run_tag"] == _tag(0) for x in d)
    _aufraeumen(welt)


def test_04_nur_zwei_vorhanden(welt):
    """(4) Zwei Privatangebote -> zwei Deals (#1, #2); der Segmentblock sagt '2 von 3'."""
    w = welt.w
    seg, s = _start(welt), w.s
    _lauf(welt, seg, [_item(f"t{s}h1", 6000), _privat(f"t{s}a", 7000), _item(f"t{s}h2", 7900), _privat(f"t{s}b", 8000)])
    d = _deals(welt, seg)
    assert [x["current_rank_private"] for x in d] == [1, 2]
    block = welt.run(ABF.segment_private_deals(welt.db, seg["id"]))
    assert len(block["top3"]) == 2 and block["anzahl_im_sample"] == 2 and block["keine_privaten"] is False
    _aufraeumen(welt)


def test_05_keine_zeilen_leerer_lauf_laesst_alten_stand(welt):
    """(5) Leerer Lauf auf frischem Segment: kein Stand, nichts angelegt. Leerer Lauf NACH einem gueltigen:
    der alte Stand bleibt, aber 'stale' (letzter gueltiger Lauf ...)."""
    w, db = welt.w, welt.db
    seg, s = _start(welt), w.s
    gestern = K.jetzt() - timedelta(days=1)
    _lauf(welt, seg, [], beobachtet=gestern)
    block = welt.run(ABF.segment_private_deals(db, seg["id"]))
    assert block["top3"] == [] and block["stand_at"] is None and block["stale"] is False and block["keine_privaten"] is False
    assert welt.run(db[K.PRIVATE_DEALS].count_documents({"segment_id": seg["id"]})) == 0
    # gueltiger Lauf, dann leerer Lauf (Marktluecke): Deals bleiben, Stand wird stale
    _lauf(welt, seg, [_privat(f"t{s}a", 7000), _item(f"t{s}h", 8000)], beobachtet=gestern + timedelta(hours=1))
    stand1 = welt.run(db[K.SEGMENTSTATS].find_one({"_id": seg["id"]}, {"_id": 0}))["private_stand_at"]
    _lauf(welt, seg, [])
    d = _deals(welt, seg)
    assert len(d) == 1 and d[0]["currently_top3"] is True and d[0]["observed_at"] == stand1
    st = welt.run(db[K.SEGMENTSTATS].find_one({"_id": seg["id"]}, {"_id": 0}))
    assert st["private_stand_at"] == stand1 and st["private_stand_stale"] is True
    block = welt.run(ABF.segment_private_deals(db, seg["id"]))
    assert block["stale"] is True and block["stand_at"] == stand1 and len(block["top3"]) == 1 and block["top3"][0]["stale"] is True
    _aufraeumen(welt)


def test_06_data_invalid_aendert_top3_nicht_und_ist_stale(welt, monkeypatch):
    """(6) Ein ungueltiger Lauf (data_invalid, Sortierung unsicher) ruehrt die Privat-Top-3 nicht an —
    der Stand bleibt, gilt aber als 'stale' (letzter Versuch nach dem letzten gueltigen Lauf); ein
    sauberer Lauf danach macht ihn wieder frisch."""
    w, db = welt.w, welt.db
    seg, s = _start(welt), w.s
    _lauf(welt, seg, [_privat(f"t{s}a", 7000), _privat(f"t{s}b", 8000), _item(f"t{s}h", 8500)], beobachtet=K.jetzt() - timedelta(hours=2))
    vorher = _deals(welt, seg)
    stand1 = welt.run(db[K.SEGMENTSTATS].find_one({"_id": seg["id"]}, {"_id": 0}))["private_stand_at"]
    monkeypatch.setenv("MARKT_BUDGET_MONAT_USD", "100")
    monkeypatch.setenv("MARKT_APIFY_ACTOR_ERSATZ", "")
    monkeypatch.setattr(K, "monat", lambda zeit=None: f"test-{s}")
    welt.run(db[K.BUDGET].delete_many({"_id": f"test-{s}"}))
    antwort = {"items": [_privat(f"t{s}z", 5000), _privat(f"t{s}a", 7000), _privat(f"t{s}y", 5500)]}     # 7000 vor 5500: unsortiert

    async def _actor(urls, max_items, zeitlimit_s=None, actor_name=None, max_items_per_query=None):
        return {**antwort, "usd": 0.02, "run_id": "r-pd", "status": "SUCCEEDED", "dauer_ms": 3, "actor": K.actor()}
    monkeypatch.setattr(APIFY, "lauf", _actor)
    job = welt.run(JOBS.job_sofort(db, seg["id"]))
    erg = welt.run(JOBS.verarbeiten_buendel(db, [_eigenen_beanspruchen(welt, job["id"])]))
    assert erg["ergebnisse"][0]["status"] == "data_invalid"
    assert _deals(welt, seg) == vorher, "ungueltiger Lauf: Deals unveraendert"
    assert welt.run(db[K.PRIVATE_DEALS].count_documents({"segment_id": seg["id"], "listing_id": f"t{s}z"})) == 0
    st = welt.run(db[K.SEGMENTSTATS].find_one({"_id": seg["id"]}, {"_id": 0}))
    assert st["private_stand_at"] == stand1
    block = welt.run(ABF.segment_private_deals(db, seg["id"]))
    assert block["stale"] is True and block["stand_at"] == stand1
    liste = welt.run(ABF.private_deals(db, model_id=seg["model_id"]))
    assert liste["deals"] and all(x["stale"] is True for x in liste["deals"])
    # sauberer Lauf danach: neuer Stand, nicht mehr stale, z rueckt auf #1
    antwort["items"] = [_privat(f"t{s}z", 5000), _privat(f"t{s}a", 7000), _privat(f"t{s}b", 8000)]
    job2 = welt.run(JOBS.job_sofort(db, seg["id"]))
    welt.run(JOBS.verarbeiten_buendel(db, [_eigenen_beanspruchen(welt, job2["id"])]))
    assert welt.run(db[K.JOBS].find_one({"id": job2["id"]}, {"_id": 0}))["status"] == "completed"
    block = welt.run(ABF.segment_private_deals(db, seg["id"]))
    assert block["stale"] is False and block["stand_at"] > stand1 and [x["listing_id"] for x in block["top3"]] == [f"t{s}z", f"t{s}a", f"t{s}b"]
    welt.run(db.betriebsalarme.delete_many({"typ": {"$regex": "^markt_"}, "ref": seg["id"]}))
    welt.run(db[K.BUDGET].delete_many({"_id": f"test-{s}"}))
    _aufraeumen(welt)


def test_07_seller_type_unbekannt_zaehlt_nicht(welt):
    """(7) Leere/unbekannte Verkaeuferart ist KEIN Privatangebot (fail-closed)."""
    w = welt.w
    seg, s = _start(welt), w.s
    ohne = {**_item(f"t{s}u1", 6000), "seller": {"type": ""}}
    komisch = {**_item(f"t{s}u2", 6500), "seller": {"type": "Gewerblich"}}
    fehlt = {k: v for k, v in _item(f"t{s}u3", 6800).items() if k != "seller"}
    _lauf(welt, seg, [ohne, komisch, fehlt, _privat(f"t{s}p", 9000)])
    d = _deals(welt, seg)
    assert [x["listing_id"] for x in d] == [f"t{s}p"] and d[0]["current_rank_private"] == 1
    # auch die reine Auswahl: nur 'PRIVATE' (Gross-/Kleinschreibung egal), nie None/DEALER
    top, n = SP.private_top_auswahl([{"listing": {"seller_type": None}, "preis": 1, "rang": 1}, {"listing": {"seller_type": "DEALER"}, "preis": 2, "rang": 2},
                                     {"listing": {"seller_type": "private"}, "preis": 3, "rang": 3}])
    assert n == 1 and top[0]["preis"] == 3
    _aufraeumen(welt)


def test_08_ehemaliges_nummer_drei_bleibt_historisch(welt):
    """(8) Faellt ein Auto aus der Top-3, bleibt sein Dokument (currently_top3 False, left_top3_at) — Historie."""
    w, db = welt.w, welt.db
    seg, s = _start(welt), w.s
    _lauf(welt, seg, [_privat(f"t{s}a", 7000), _privat(f"t{s}b", 8000), _privat(f"t{s}c", 9000)], lauf_tag=f"{_tag(0)}#1")
    _lauf(welt, seg, [_privat(f"t{s}a", 7000), _privat(f"t{s}b", 8000), _privat(f"t{s}d", 8500)], lauf_tag=f"{_tag(0)}#2")
    c = welt.run(db[K.PRIVATE_DEALS].find_one({"segment_id": seg["id"], "listing_id": f"t{s}c"}, {"_id": 0}))
    assert c["currently_top3"] is False and c["current_rank_private"] is None and c["left_top3_at"] and c["best_rank_private"] == 3
    assert c["last_seen_top3_at"] < c["left_top3_at"]
    d = _deals(welt, seg, currently_top3=True)
    assert [(x["listing_id"], x["current_rank_private"]) for x in d] == [(f"t{s}a", 1), (f"t{s}b", 2), (f"t{s}d", 3)]
    block = welt.run(ABF.segment_private_deals(db, seg["id"]))
    assert [x["listing_id"] for x in block["historie"]] == [f"t{s}c"] and len(block["top3"]) == 3
    # Liste: Standard nur aktuelle; historisch schaltet die herausgefallenen dazu
    ids_aktuell = {x["listing_id"] for x in welt.run(ABF.private_deals(db, model_id=seg["model_id"]))["deals"]}
    ids_alle = {x["listing_id"] for x in welt.run(ABF.private_deals(db, model_id=seg["model_id"], nur_aktuell=False))["deals"]}
    assert f"t{s}c" not in ids_aktuell and f"t{s}c" in ids_alle and ids_alle - ids_aktuell == {f"t{s}c"}
    # kommt c zurueck, wird es wieder aktuell — mit reentered_top3_at, first_entered_top3_at bleibt
    erst = c["first_entered_top3_at"]
    _lauf(welt, seg, [_privat(f"t{s}a", 7000), _privat(f"t{s}c", 7500), _privat(f"t{s}b", 8000)], lauf_tag=f"{_tag(0)}#3")
    c2 = welt.run(db[K.PRIVATE_DEALS].find_one({"segment_id": seg["id"], "listing_id": f"t{s}c"}, {"_id": 0}))
    assert c2["currently_top3"] is True and c2["current_rank_private"] == 2 and c2["reentered_top3_at"] and "left_top3_at" not in c2
    assert c2["first_entered_top3_at"] == erst and c2["best_rank_private"] == 2 and c2["lowest_price_seen"] == 7500
    _aufraeumen(welt)


def test_09_neues_auto_rueckt_auf_platz_eins(welt):
    """(9) Ein neues, guenstigeres Privatangebot wird #1; die anderen ruecken nach, best_rank bleibt."""
    w, db = welt.w, welt.db
    seg, s = _start(welt), w.s
    _lauf(welt, seg, [_privat(f"t{s}a", 7000), _privat(f"t{s}b", 8000), _privat(f"t{s}c", 9000)], lauf_tag=f"{_tag(0)}#1")
    _lauf(welt, seg, [_privat(f"t{s}n", 6500), _privat(f"t{s}a", 7000), _privat(f"t{s}b", 8000), _privat(f"t{s}c", 9000)], lauf_tag=f"{_tag(0)}#2")
    d = _deals(welt, seg, currently_top3=True)
    assert [(x["listing_id"], x["current_rank_private"], x["best_rank_private"]) for x in d] == \
        [(f"t{s}n", 1, 1), (f"t{s}a", 2, 1), (f"t{s}b", 3, 2)]
    assert d[0]["first_entered_top3_tag"] == _tag(0) and d[0]["first_price_top3"] == 6500
    st = welt.run(db[K.SEGMENTSTATS].find_one({"_id": seg["id"]}, {"_id": 0}))
    assert [t["rank_private"] for t in st["private_top3"]] == [1, 2, 3] and st["private_top3"][0]["listing_id"] == f"t{s}n"
    _aufraeumen(welt)


def test_10_preisreduzierung_aktualisiert_preis_und_verlauf(welt):
    """(10) Preissenkung eines Top-3-Autos: current_price, lowest_price_seen, price_history_top3 (max. 30),
    last_reduced_tag; eine spaetere Erhoehung hebt lowest_price_seen nicht an."""
    w, db = welt.w, welt.db
    seg, s = _start(welt), w.s
    _lauf(welt, seg, [_privat(f"t{s}a", 7000), _item(f"t{s}h", 7500)], lauf_tag=f"{_tag(0)}#1")
    _lauf(welt, seg, [_privat(f"t{s}a", 6500), _item(f"t{s}h", 7500)], lauf_tag=f"{_tag(0)}#2")
    a = welt.run(db[K.PRIVATE_DEALS].find_one({"segment_id": seg["id"], "listing_id": f"t{s}a"}, {"_id": 0}))
    assert a["current_price"] == 6500 and a["lowest_price_seen"] == 6500 and a["first_price_top3"] == 7000
    assert [p["price"] for p in a["price_history_top3"]] == [7000, 6500] and a["last_reduced_tag"] == _tag(0)
    assert a["price_change_since_first_eur"] == -500 and a["last_price_change_eur"] == -500
    _lauf(welt, seg, [_privat(f"t{s}a", 6800), _item(f"t{s}h", 7500)], lauf_tag=f"{_tag(0)}#3")
    a = welt.run(db[K.PRIVATE_DEALS].find_one({"segment_id": seg["id"], "listing_id": f"t{s}a"}, {"_id": 0}))
    assert a["current_price"] == 6800 and a["lowest_price_seen"] == 6500 and [p["price"] for p in a["price_history_top3"]] == [7000, 6500, 6800]
    assert a["price_change_since_first_eur"] == -200 and a["last_price_change_eur"] == 300
    # unveraenderter Preis: kein neuer Verlaufseintrag
    _lauf(welt, seg, [_privat(f"t{s}a", 6800), _item(f"t{s}h", 7500)], lauf_tag=f"{_tag(0)}#4")
    a = welt.run(db[K.PRIVATE_DEALS].find_one({"segment_id": seg["id"], "listing_id": f"t{s}a"}, {"_id": 0}))
    assert len(a["price_history_top3"]) == 3
    assert K.PRIVATE_PREISVERLAUF_MAX == 30 and "$slice" in inspect.getsource(SP.private_deals_ableiten)
    liste = welt.run(ABF.private_deals(db, model_id=seg["model_id"], preis_reduziert=True))
    assert [x["listing_id"] for x in liste["deals"]] == [f"t{s}a"] and liste["deals"][0]["preis_reduziert"] is True
    _aufraeumen(welt)


def test_11_dieselbe_listing_id_in_zwei_laeufen_ein_dokument(welt):
    """(11) Zwei Laeufe (auch an zwei Tagen) mit demselben Auto -> EIN Dokument je (segment_id, listing_id)."""
    w, db = welt.w, welt.db
    seg, s = _start(welt), w.s
    _lauf(welt, seg, [_privat(f"t{s}a", 7000)], beobachtet=K.jetzt() - timedelta(days=1))
    erst = welt.run(db[K.PRIVATE_DEALS].find_one({"segment_id": seg["id"], "listing_id": f"t{s}a"}, {"_id": 0}))
    _lauf(welt, seg, [_privat(f"t{s}a", 7000)])
    assert welt.run(db[K.PRIVATE_DEALS].count_documents({"segment_id": seg["id"], "listing_id": f"t{s}a"})) == 1
    zweit = welt.run(db[K.PRIVATE_DEALS].find_one({"segment_id": seg["id"], "listing_id": f"t{s}a"}, {"_id": 0}))
    assert zweit["first_entered_top3_at"] == erst["first_entered_top3_at"] and zweit["id"] == erst["id"]
    assert zweit["last_seen_top3_at"] > erst["last_seen_top3_at"] and zweit["first_entered_top3_tag"] == _tag(-1)
    liste = welt.run(ABF.private_deals(db, model_id=seg["model_id"]))
    assert len(liste["deals"]) == 1 and liste["deals"][0]["heute_neu"] is False
    # Unique-Index steht (idempotent, nicht kritisch) und der Upsert wiederholt einen Konflikt einmal
    IDX = _module("indizes")
    erg = welt.run(IDX.markt_indizes(db))
    assert "market_private_deals.markt_privat_je_segment" not in erg["fehler"]
    info = welt.run(db[K.PRIVATE_DEALS].index_information())
    assert info["markt_privat_je_segment"]["key"] == [("segment_id", 1), ("listing_id", 1)] and info["markt_privat_je_segment"].get("unique")
    assert info["markt_privat_aktuell_abstand"]["key"] == [("currently_top3", 1), ("difference_to_segment_median_pct", 1)]
    assert info["markt_privat_modell"]["key"] == [("model_id", 1), ("currently_top3", 1)]
    assert "market_private_deals" not in " ".join(IDX.MARKT_UNIQUE_KRITISCH)
    assert "_einmal_wiederholen(lambda: db[PRIVATE_DEALS]" in inspect.getsource(SP.private_deals_ableiten)
    _aufraeumen(welt)


def test_12_keine_pii_im_dokument(welt):
    """(12) Kein Schluessel beginnt mit seller_, keine Koordinaten, kein Name/keine Telefonnummer —
    nur Ort/PLZ. Weder die Ableitung noch der Leseweg fassen latitude/longitude an."""
    w, db = welt.w, welt.db
    seg, s = _start(welt), w.s
    _lauf(welt, seg, [_privat(f"t{s}a", 7000)])
    a = welt.run(db[K.PRIVATE_DEALS].find_one({"segment_id": seg["id"], "listing_id": f"t{s}a"}, {"_id": 0}))
    assert not [k for k in a if k.startswith("seller_")] and "latitude" not in a and "longitude" not in a
    assert "Vera" not in str(a) and "0171" not in str(a) and "99" != str(a.get("seller_id"))
    assert a["city"] == "Hannover" and a["postal_code"] == "30159" and a["url"].startswith("https://suchen.mobile.de/")
    assert not any(f.startswith("seller") or f in ("latitude", "longitude") for f in SP.PRIVAT_FAHRZEUGFELDER)
    for fn in (SP.private_deals_ableiten, SP._privat_fahrzeug, ABF.private_deals, ABF.segment_private_deals, ABF._deal_anreichern, ABF._private_kontext):
        q = inspect.getsource(fn)
        assert "latitude" not in q and "seller_name" not in q and "phone" not in q, fn.__name__
    # Leseweg reicht nichts nach: die Listing-Projektion holt nur Zustand und Preis
    assert '{"_id": 0, "listing_id": 1, "active_state": 1, "current_price": 1}' in inspect.getsource(ABF._private_kontext)
    liste = welt.run(ABF.private_deals(db, model_id=seg["model_id"]))
    d = liste["deals"][0]
    assert not [k for k in d if k.startswith("seller_")] and "latitude" not in d and "Vera" not in str(d)
    _aufraeumen(welt)


# ---------------------------------------------------------------- API
def test_13_api_filter_und_sortierung(welt):
    """(13) Filter (model_id, ez, km, Preis, Abstand, PLZ, heute_neu, historisch), Sortierungen, Limit,
    Zusammenfassung — Listings/Segmente je mit EINEM $in (kein N+1)."""
    w, db = welt.w, welt.db
    seg_a, s = _start(welt), w.s
    seg_b = {**seg_a, "id": f"test-320d-{s}:2019-2021:10000-30000", "min_km": 10000, "max_km": 30000, "km_label": "10–30k km"}
    welt.run(db[K.SEGMENTE].insert_one(dict(seg_b)))
    gestern = K.jetzt() - timedelta(days=1)
    # Segment A: Median 8000 -> a -25 %, b -12,5 %, c +12,5 %
    _lauf(welt, seg_a, [_privat(f"t{s}a", 6000, plz="80331", ort="München", ez="05/2019", created="2026-09-01T10:00:00.000Z"),
                        _privat(f"t{s}b", 7000, plz="30159", ez="03/2020", created="2026-09-20T10:00:00.000Z"),
                        _item(f"t{s}h1", 8000), _privat(f"t{s}c", 9000, plz="30161", ez="07/2021", created="2026-09-10T10:00:00.000Z"),
                        _item(f"t{s}h2", 12000)], beobachtet=gestern)
    # Segment B: ein Privatangebot mit wenig km, heute neu
    _lauf(welt, seg_b, [_privat(f"t{s}d", 15000, km=20000, ez="01/2021"), _item(f"t{s}h3", 15500, km=25000)])
    # c faellt heute aus der Top-3 (historisch), b wird guenstiger (reduziert)
    _lauf(welt, seg_a, [_privat(f"t{s}a", 6000, plz="80331", ort="München", ez="05/2019", created="2026-09-01T10:00:00.000Z"),
                        _privat(f"t{s}b", 6600, plz="30159", ez="03/2020", created="2026-09-20T10:00:00.000Z"),
                        _privat(f"t{s}e", 7200, plz="30159", ez="03/2020"), _item(f"t{s}h1", 8000), _privat(f"t{s}c", 9000, plz="30161", ez="07/2021"),
                        _item(f"t{s}h2", 12000)])
    m = seg_a["model_id"]
    ids = lambda r: [x["listing_id"] for x in r["deals"]]  # noqa: E731
    alle = welt.run(ABF.private_deals(db, model_id=m))
    assert ids(alle) == [f"t{s}a", f"t{s}b", f"t{s}e", f"t{s}d"], "Standard: groesste negative Abweichung zuerst, nur aktuelle"
    z = alle["zusammenfassung"]
    assert z["aktuelle_top3"] >= 4 and z["segmente_mit_deals"] >= 2 and z["heute_neu"] >= 2 and z["heute_reduziert"] >= 1 and z["segmente_aktiv"] >= 2
    assert all(x["segment_label"] and x["km_label"] for x in alle["deals"]) and next(x for x in alle["deals"] if x["listing_id"] == f"t{s}d")["km_label"] == "10–30k km"
    # historisch: c (herausgefallen, zuletzt +12,5 % ueber dem Median) haengt hinten dran
    assert ids(welt.run(ABF.private_deals(db, model_id=m, nur_aktuell=False))) == [f"t{s}a", f"t{s}b", f"t{s}e", f"t{s}d", f"t{s}c"]
    assert ids(welt.run(ABF.private_deals(db, model_id=m, ez=2020))) == [f"t{s}b", f"t{s}e"]
    assert ids(welt.run(ABF.private_deals(db, model_id=m, km_min=0, km_max=30000))) == [f"t{s}d"]
    assert ids(welt.run(ABF.private_deals(db, model_id=m, km_min=60000))) == [f"t{s}a", f"t{s}b", f"t{s}e"]
    assert ids(welt.run(ABF.private_deals(db, model_id=m, preis_von=6500, preis_bis=7500))) == [f"t{s}b", f"t{s}e"]
    assert ids(welt.run(ABF.private_deals(db, model_id=m, abstand_pct_max=-5))) == [f"t{s}a", f"t{s}b", f"t{s}e"]
    assert ids(welt.run(ABF.private_deals(db, model_id=m, abstand_pct_max=-20))) == [f"t{s}a"]
    assert ids(welt.run(ABF.private_deals(db, model_id=m, plz="80"))) == [f"t{s}a"]
    assert ids(welt.run(ABF.private_deals(db, model_id=m, plz="301"))) == [f"t{s}b", f"t{s}e", f"t{s}d"]     # d: Standard-PLZ 30159
    assert ids(welt.run(ABF.private_deals(db, model_id=m, make="bmw"))) == ids(alle) and ids(welt.run(ABF.private_deals(db, model_id=m, make="Audi"))) == []
    assert set(ids(welt.run(ABF.private_deals(db, model_id=m, heute_neu=True)))) == {f"t{s}e", f"t{s}d"}
    assert ids(welt.run(ABF.private_deals(db, model_id=m, preis_reduziert=True))) == [f"t{s}b"]
    assert ids(welt.run(ABF.private_deals(db, model_id=m, sort="preis"))) == [f"t{s}a", f"t{s}b", f"t{s}e", f"t{s}d"]
    assert ids(welt.run(ABF.private_deals(db, model_id=m, sort="reduzierung")))[0] == f"t{s}b"
    standzeit = ids(welt.run(ABF.private_deals(db, model_id=m, sort="standzeit")))
    assert standzeit[0] == f"t{s}a" and standzeit[-1] == f"t{s}b", "am laengsten bei mobile.de zuerst"
    neueste = ids(welt.run(ABF.private_deals(db, model_id=m, sort="neueste")))
    assert set(neueste[:2]) == {f"t{s}e", f"t{s}d"} and neueste[-1] in (f"t{s}a", f"t{s}b")
    begrenzt = welt.run(ABF.private_deals(db, model_id=m, limit=2))
    assert len(begrenzt["deals"]) == 2 and begrenzt["gekuerzt"] is True and welt.run(ABF.private_deals(db, model_id=m, limit=9999))["anzahl"] == 4
    assert ABF.PRIVATE_LIMIT_MAX == 500
    b = next(x for x in alle["deals"] if x["listing_id"] == f"t{s}b")
    assert b["preis_reduziert"] is True and b["heute_reduziert"] is True and b["heute_neu"] is False and b["active_state"] == "seen" and b["stale"] is False
    # kein N+1: Listings, Segmente, Statistiken je mit einem $in
    q = inspect.getsource(ABF._private_kontext)
    assert '"listing_id": {"$in": ids}' in q and '"id": {"$in": seg_ids}' in q and '"_id": {"$in": seg_ids}' in q
    assert "find_one" not in inspect.getsource(ABF.private_deals)
    _aufraeumen(welt)


def test_14_segmentstatistik_traegt_private_top3(welt):
    """(14) market_segment_stats: private_top3 [{listing_id, price, rank_private}], private_anzahl_im_sample,
    private_stand_at/-lauf — und eine Neuberechnung der Statistik loescht sie nicht."""
    w, db = welt.w, welt.db
    seg, s = _start(welt), w.s
    _lauf(welt, seg, [_privat(f"t{s}a", 7000), _item(f"t{s}h", 7500), _privat(f"t{s}b", 8000), _privat(f"t{s}c", 9000), _privat(f"t{s}d", 9500)],
          lauf_tag=f"{_tag(0)}#2")
    st = welt.run(db[K.SEGMENTSTATS].find_one({"_id": seg["id"]}, {"_id": 0}))
    assert st["private_top3"] == [{"listing_id": f"t{s}a", "price": 7000.0, "rank_private": 1}, {"listing_id": f"t{s}b", "price": 8000.0, "rank_private": 2},
                                  {"listing_id": f"t{s}c", "price": 9000.0, "rank_private": 3}]
    assert st["private_anzahl_im_sample"] == 4 and st["private_stand_lauf"] == f"{_tag(0)}#2" and st["private_stand_tag"] == _tag(0)
    assert st["private_stand_at"] and st["private_stand_stale"] is False and st["private_stand_sample_size"] == 5
    welt.run(SP.segmentstatistik(db, seg["id"]))
    st2 = welt.run(db[K.SEGMENTSTATS].find_one({"_id": seg["id"]}, {"_id": 0}))
    assert st2["private_top3"] == st["private_top3"] and st2["private_stand_at"] == st["private_stand_at"] and st2["sample_size"] == 5
    # Segment-Zusammenfassung (current_admin) traegt die Felder ueber stats mit; der Block selbst kommt ueber die Super-Admin-Route
    zs = welt.run(ABF.segment_zusammenfassung(db, seg["id"]))
    assert zs["stats"]["private_anzahl_im_sample"] == 4
    _aufraeumen(welt)


def test_15_routen_nur_super_admin_und_hauptweg_unberuehrt():
    """(15) Beide Routen haengen an current_super_admin; die Firmen-Routen (routes/markt.py), die Karte und
    die Chancen wissen nichts von Private Deals; der Hauptweg (Vergleich/Vertrag) bleibt unabhaengig."""
    root = Path(__file__).resolve().parent.parent
    r = (root / "routes" / "markt_admin.py").read_text(encoding="utf-8")
    for pfad in ('"/admin/market/private-deals"', '"/admin/market/segments/{segment_id}/private-deals"'):
        assert pfad in r
        kopf = r.split(pfad)[1][:900]
        assert "current_super_admin" in kopf.split("\n\n")[0], pfad
        assert "Depends(current_admin)" not in kopf.split("\n\n")[0], pfad
    # Registry-Meta-Test: GET unter /api/admin/... verlangt current_admin — current_super_admin haengt daran
    assert "user=Depends(current_admin)" in inspect.getsource(_module("deps").current_super_admin)
    assert "private" not in (root / "routes" / "markt.py").read_text(encoding="utf-8").lower()
    for fn in (ABF.karte, ABF.chancen, ABF.segment_listings, SP.chancen_ableiten):
        assert "PRIVATE_DEALS" not in inspect.getsource(fn) and "private_top3" not in inspect.getsource(fn), fn.__name__
    for datei in ("routes/listings.py", "routes/contracts.py", "mobile_service.py", "pdf_service.py"):
        p = root / datei
        if p.exists():
            assert "private_deals" not in p.read_text(encoding="utf-8").lower() and "PRIVATE_DEALS" not in p.read_text(encoding="utf-8"), datei
    # Lesewege schreiben nicht (wie test_09 in test_markt_20260926): auch die neuen Funktionen
    for fn in (ABF.private_deals, ABF.segment_private_deals, ABF._private_kontext):
        q = inspect.getsource(fn)
        assert not any(v in q for v in ("insert_one", "update_one", "update_many", "delete_one", "delete_many")), fn.__name__
    assert K.PRIVATE_DEALS == "market_private_deals" and K.PRIVATE_TOP_N == 3
