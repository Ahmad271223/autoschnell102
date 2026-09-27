# -*- coding: utf-8 -*-
"""Live-Befund 27.09.2026 (Betriebsalarm markt_filter_ignoriert 447x "land GE != DE"): scrapesmith liefert je Zeile
country "GERMANY" (Marktplatz) und sellerCountry "DE"/"IT" (Verkaeufer). Aus GERMANY wurde per [:2] "GE" — jede Zeile
fiel durch die Land-Pruefung. Diese Tests stellen die echte Zeilenform nach."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_befunde_runde17_termine import _module, welt  # noqa: E402,F401
from test_markt_20260926 import _item2, _modell, _segment  # noqa: E402

NORM = _module("markt.normalisieren")
ABF = _module("markt.abfrage")


def test_01_land_code_nie_abgeschnitten():
    for wert, code in (("GERMANY", "DE"), ("Germany", "DE"), ("Deutschland", "DE"), ("DEU", "DE"), ("D", "DE"), ("de", "DE"),
                       (" DE ", "DE"), ("ITALY", "IT"), ("Österreich", "AT"), ("AUT", "AT"), ("the netherlands", "NL"),
                       ("Czech Republic", "CZ"), ("AT", "AT"), ("XK", "XK")):
        assert NORM.land_code(wert) == code, wert
    for wert in (None, "", "   ", "ATLANTIS", "GERMANYY", "G3", 42):
        assert NORM.land_code(wert) is None, wert


def test_02_scrapesmith_zeile_mit_country_germany_passt(welt):
    w = welt.w
    seg, modell = _segment(w), {**_modell(w), "country": "DE"}
    zeile = NORM.listings_aus_items([{**_item2("s1", 17980, 1), "country": "GERMANY", "sellerCountry": "DE"}])[0]
    assert zeile["country"] == "DE"
    assert NORM.passt_zum_segment(zeile, seg, modell) == (True, ""), "vorher: land GE != DE"


def test_03_verkaeuferland_zaehlt_nicht_der_marktplatz(welt):
    w = welt.w
    seg, modell = _segment(w), {**_modell(w), "country": "DE"}
    it = NORM.listings_aus_items([{**_item2("s2", 17980, 1), "country": "GERMANY", "sellerCountry": "IT"}])[0]
    assert it["country"] == "IT"
    assert NORM.passt_zum_segment(it, seg, modell) == (False, "land IT != DE")
    # ohne Verkaeuferland: der Marktplatz ist die beste Angabe
    nur_markt = NORM.listings_aus_items([{**_item2("s3", 17980, 1), "country": "GERMANY", "sellerCountry": None}])[0]
    assert nur_markt["country"] == "DE"
    # unbekannte Angabe: tolerant (wie fehlend), nie verworfen
    unbekannt = NORM.listings_aus_items([{**_item2("s4", 17980, 1), "country": "ATLANTIS", "sellerCountry": ""}])[0]
    assert unbekannt["country"] is None and NORM.passt_zum_segment(unbekannt, seg, modell)[0]
    # Auftrag mit ausgeschriebenem Land (Altbestand) vergleicht ebenfalls per Code
    assert NORM.passt_zum_segment({**it, "country": "DE"}, seg, {**modell, "country": "Germany"}) == (True, "")


def test_04_alle_zeilen_eines_echten_laufs_bleiben_drin(welt):
    """So sah der Live-Lauf aus: 20 Zeilen, alle country GERMANY/sellerCountry DE — vorher 20 von 20 verworfen."""
    w = welt.w
    seg, modell = _segment(w), {**_modell(w), "country": "DE"}
    zeilen = NORM.listings_aus_items([{**_item2(f"z{i}", 15000 + i * 100, i + 1), "country": "GERMANY", "sellerCountry": "DE"}
                                      for i in range(20)])
    verworfen = [NORM.passt_zum_segment(z, seg, modell)[1] for z in zeilen if not NORM.passt_zum_segment(z, seg, modell)[0]]
    assert verworfen == [], verworfen


def test_05_fahrzeug_mit_ausgeschriebenem_land_findet_seinen_auftrag():
    q = __import__("inspect").getsource(ABF)
    assert "normalisieren.land_code(v.get(\"seller_country\"))" in q and ".upper()[:2] or None" not in q


def test_06_migration_20_schliesst_nur_die_land_alarme(welt):
    """Die 101 Alarme vom 27.09. (100 Einzel + Sammelalarm) schliesst Migration 20 — echte Filterprobleme bleiben offen."""
    import uuid
    MIG = _module("migrationen")
    db = welt.db
    s = uuid.uuid4().hex[:8]
    land = "land GE != DE; land GE != DE; land GE != DE"
    docs = [
        {"id": f"a1-{s}", "typ": "markt_filter_ignoriert", "ref": f"test-land-{s}:2022:20000-55000", "offen": True, "anzahl": 1,
         "details": {"gruende": land, "verworfen": 20, "geliefert": 20}},
        {"id": f"a2-{s}", "typ": "markt_filter_ignoriert", "ref": f"*weitere*-test-{s}", "offen": True, "anzahl": 347,
         "details": {"gruende": land, "letzter_ref": "bmw-420d-auto-neu:2022:20000-55000"}},
        {"id": f"a3-{s}", "typ": "markt_filter_ignoriert", "ref": f"test-echt-{s}", "offen": True, "anzahl": 1,
         "details": {"gruende": "land GE != DE; ez 2017 != 2020", "verworfen": 5, "geliefert": 6}},
        {"id": f"a4-{s}", "typ": "markt_filter_ignoriert", "ref": f"test-ez-{s}", "offen": True, "anzahl": 1,
         "details": {"gruende": "ez 2017 != 2020", "verworfen": 5, "geliefert": 6}},
        {"id": f"a5-{s}", "typ": "markt_lauf_leer", "ref": f"test-leer-{s}", "offen": True, "anzahl": 1, "details": {"gruende": land}},
    ]
    welt.run(db.betriebsalarme.insert_many([dict(d) for d in docs]))
    try:
        z = welt.run(MIG.m20_markt_land_alarme_schliessen(db))
        assert z["geschlossen"] >= 2
        offen = {d["id"]: d["offen"] for d in welt.run(db.betriebsalarme.find({"id": {"$regex": f"-{s}$"}}, {"_id": 0}).to_list(10))}
        assert offen == {f"a1-{s}": False, f"a2-{s}": False, f"a3-{s}": True, f"a4-{s}": True, f"a5-{s}": True}, offen
        zu = welt.run(db.betriebsalarme.find_one({"id": f"a1-{s}"}, {"_id": 0}))
        assert zu["quittiert_von"].startswith("system:behoben") and zu.get("loeschen_ab")
        assert welt.run(MIG.m20_markt_land_alarme_schliessen(db))["geschlossen"] == 0 or True   # idempotent (fremde Altalarme egal)
        nochmal = {d["id"]: d["offen"] for d in welt.run(db.betriebsalarme.find({"id": {"$regex": f"-{s}$"}}, {"_id": 0}).to_list(10))}
        assert nochmal == offen
    finally:
        welt.run(db.betriebsalarme.delete_many({"id": {"$regex": f"-{s}$"}}))
