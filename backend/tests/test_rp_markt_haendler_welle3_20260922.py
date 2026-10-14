# -*- coding: utf-8 -*-
"""Rollenpruefung 22.09.2026, Welle 3 — Uebergaben an Team markt_haendler
(routes/resale.py).

In-Prozess gegen eine Wegwerf-Datenbank (Fixture aus
test_rp_markt_haendler_20260922.py, die DB wird am Ende verworfen).

  RP-517    Laufzeit ab max(published_at, wieder_veroeffentlicht_am) — Editor,
            Liste und die 409-Pruefung beim erneuten Veroeffentlichen rechnen
            wie der Aufraeumlauf (cleanup_service)
  RP-057 b  Einkaufspreis "mehrdeutig" (Doppel-Vertraege verschiedener
            Konten mit verschiedenen Preisen): kein geratener Preis, keine
            Marge, Stand beim Verkauf festgehalten
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import Response  # noqa: E402

from test_rp_markt_haendler_20260922 import (  # noqa: E402,F401
    _fehler, _vor, welt)


# ============================================================ RP-517
def test_rp517_laufzeit_anker():
    from routes import resale as R
    erst, spaeter = _vor(days=30), _vor(days=2)
    assert R._laufzeit_anker({"published_at": erst}) == erst
    assert R._laufzeit_anker({"published_at": erst, "wieder_veroeffentlicht_am": spaeter}) == spaeter
    # aeltere Freigabe verschiebt nichts nach vorn
    assert R._laufzeit_anker({"published_at": spaeter, "wieder_veroeffentlicht_am": erst}) == spaeter
    for leer in (None, ""):
        assert R._laufzeit_anker({"published_at": erst, "wieder_veroeffentlicht_am": leer}) == erst
    # nie veroeffentlicht -> keine Laufzeit, auch nicht mit Freigabe-Stempel
    assert R._laufzeit_anker({"published_at": None, "wieder_veroeffentlicht_am": spaeter}) is None


def test_rp517_editor_und_liste_ab_freigabe(welt):
    R, db = welt.R, welt.db
    vid, lid = f"v1_{welt.s}", f"l1_{welt.s}"
    erst = datetime.now(timezone.utc) - timedelta(days=30)
    frei = datetime.now(timezone.utc) - timedelta(days=2)
    welt.run(db.vehicles.insert_one(welt.fahrzeug(vid, "veroeffentlicht")))
    welt.run(db.resale_listings.insert_one(welt.inserat(
        lid, vid, "veroeffentlicht", published_at=erst.isoformat(),
        wieder_veroeffentlicht_am=frei.isoformat())))
    erwartet = (frei + timedelta(days=R.INSERAT_LAUFZEIT_TAGE)).isoformat()
    l = welt.run(R.get_listing(lid, welt.chef))
    assert l["laeuft_ab_am"] == erwartet == l["laufzeit_bis"]
    liste = welt.run(R.list_listings(Response(), welt.chef))
    assert next(x for x in liste if x["id"] == lid)["laeuft_ab_am"] == erwartet
    # ohne Freigabe weiter ab der ersten Veroeffentlichung
    welt.run(db.resale_listings.update_one({"id": lid},
                                           {"$unset": {"wieder_veroeffentlicht_am": ""}}))
    assert welt.run(R.get_listing(lid, welt.chef))["laeuft_ab_am"] == \
        (erst + timedelta(days=R.INSERAT_LAUFZEIT_TAGE)).isoformat()


def test_rp517_erneut_veroeffentlichen_nach_freigabe(welt):
    R, db = welt.R, welt.db
    vid, lid = f"v2_{welt.s}", f"l2_{welt.s}"
    welt.run(db.vehicles.insert_one(welt.fahrzeug(vid, "verkaufsbereit")))
    welt.run(db.resale_listings.insert_one(welt.inserat(
        lid, vid, "zurueckgezogen", published_at=_vor(days=R.INSERAT_LAUFZEIT_TAGE + 9),
        wieder_veroeffentlicht_am=_vor(days=R.INSERAT_LAUFZEIT_TAGE + 1),
        counted_periods=["2026-08"])))
    # beide Zeitpunkte aelter als die Laufzeit -> weiter 409, nichts gezaehlt
    e = _fehler(welt, R.publish_listing(lid, R.PublishIn(visibility="public"), welt.chef))
    assert e.status_code == 409 and "Laufzeit" in e.detail
    d = welt.run(db.resale_listings.find_one({"id": lid}))
    assert d["status"] == "zurueckgezogen" and d["counted_periods"] == ["2026-08"]
    # Freigabe vor 3 Tagen: der Aufraeumlauf behielte es noch -> veroeffentlichen geht
    welt.run(db.resale_listings.update_one(
        {"id": lid}, {"$set": {"wieder_veroeffentlicht_am": _vor(days=3)}}))
    r = welt.run(R.publish_listing(lid, R.PublishIn(visibility="public"), welt.chef))
    assert r["status"] == "veroeffentlicht"
    d = welt.run(db.resale_listings.find_one({"id": lid}))
    assert d["status"] == "veroeffentlicht"
    assert d["published_at"] < _vor(days=R.INSERAT_LAUFZEIT_TAGE), "erste Veroeffentlichung bleibt"


# ============================================================ RP-057 b
def _doppelvertraege(welt, vid, preise=(20000.0, 18500.0)):
    for n, preis in enumerate(preise):
        welt.run(welt.db.kaufvorgaenge.insert_one(
            {"id": f"kv{n}_{welt.s}_{vid}", "vehicle_id": vid, "dealer_id": welt.dealer_id,
             "user_id": f"sucher{n}_{welt.s}", "status": "gesendet",
             "purchase_price": preis, "updated_at": _vor(minutes=n + 1)}))


def test_rp057b_mehrdeutig_ohne_geratenen_preis_und_marge(welt):
    R, db = welt.R, welt.db
    vid, lid = f"v3_{welt.s}", f"l3_{welt.s}"
    welt.run(db.vehicles.insert_one(welt.fahrzeug(vid, "verkaufsbereit")))
    # beim Anlegen kopierte (zufaellige) Vertragszahl
    welt.run(db.resale_listings.insert_one(welt.inserat(
        lid, vid, "verkaufsbereit", purchase_price=20000.0, purchase_price_quelle="vertrag")))
    _doppelvertraege(welt, vid)
    m = welt.run(R.get_listing(lid, welt.chef))["margin"]
    assert m["purchase_price"] is None and m["purchase_price_quelle"] == "mehrdeutig"
    assert m["total_cost"] is None and m["expected_margin"] is None
    liste = welt.run(R.list_listings(Response(), welt.chef))
    assert next(x for x in liste if x["id"] == lid)["margin"]["purchase_price_quelle"] == "mehrdeutig"
    # von Hand am Inserat eingetragener Preis (ohne Quelle) bleibt stehen
    welt.run(db.resale_listings.update_one(
        {"id": lid}, {"$set": {"purchase_price": 19000.0},
                      "$unset": {"purchase_price_quelle": ""}}))
    m = welt.run(R.get_listing(lid, welt.chef))["margin"]
    assert m["purchase_price"] == 19000.0 and m["purchase_price_quelle"] == "inserat"
    assert m["expected_margin"] == round(9900.0 - 19000.0, 2)
    # nach der Abholung steht der Preis fest
    welt.run(db.resale_listings.update_one(
        {"id": lid}, {"$set": {"purchase_price_quelle": "vertrag"}}))
    welt.run(db.kaufvorgaenge.update_one({"id": f"kv1_{welt.s}_{vid}"},
                                         {"$set": {"status": "abgeholt"}}))
    m = welt.run(R.get_listing(lid, welt.chef))["margin"]
    assert m["purchase_price"] == 18500.0 and m["purchase_price_quelle"] == "abgeholt"


def test_rp057b_verkauf_haelt_offenen_einkauf_fest(welt):
    R, db = welt.R, welt.db
    vid, lid = f"v4_{welt.s}", f"l4_{welt.s}"
    welt.run(db.vehicles.insert_one(welt.fahrzeug(vid, "verkaufsbereit")))
    welt.run(db.resale_listings.insert_one(welt.inserat(
        lid, vid, "verkaufsbereit", purchase_price=20000.0, purchase_price_quelle="vertrag")))
    _doppelvertraege(welt, vid)
    welt.run(R.set_listing_status(lid, R.ListingStatusIn(status="verkauft", sold_price=21000),
                                  welt.chef))
    d = welt.run(db.resale_listings.find_one({"id": lid}))
    assert d["status"] == "verkauft"
    assert d["purchase_price"] is None and d["purchase_price_quelle"] == "mehrdeutig"
    assert welt.run(R.get_listing(lid, welt.chef))["margin"]["expected_margin"] is None
