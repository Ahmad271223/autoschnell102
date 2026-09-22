# -*- coding: utf-8 -*-
"""Rollenpruefung 22.09.2026, Welle 4 — Review-Befunde fuer Team
markt_haendler (routes/resale.py).

In-Prozess gegen eine Wegwerf-Datenbank (Fixture aus
test_rp_markt_haendler_20260922.py, die DB wird am Ende verworfen).

  RP-492/Nr. 399  Doppelklick nach erfolgreichem Statuswechsel: der zweite
                  Aufruf traegt den alten angezeigten Status (von_status) —
                  "bereits" statt 409 "… von einem Käufer reserviert", solange
                  der Zielstatus nur von der eigenen Firma stammen kann; eine
                  Kaeufer-Reservierung bleibt 409
  RP-088          Scheitert "Verkauft" am Fahrzeug (Rennen), nimmt der
                  Rollback auch den festgeschriebenen Einkaufspreis-/Kosten-
                  Stand zurueck; "inserat" gilt als "von Hand"
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_rp_markt_haendler_20260922 import (  # noqa: E402,F401
    _fehler, welt)


# ============================================================ RP-492 / Nr. 399
def test_doppelklick_nach_wechsel_ist_bereits_kein_409(welt):
    R, db = welt.R, welt.db
    faelle = (
        # (Inserat vorher, Fahrzeug vorher, Ziel)
        ("veroeffentlicht", "veroeffentlicht", "zurueckgezogen"),   # "Vom Marktplatz nehmen"
        ("verkaufsbereit", "verkaufsbereit", "entwurf"),             # "Zurück zu Entwurf"
        ("veroeffentlicht", "veroeffentlicht", "reserviert"),        # "Reservieren" (von Hand)
    )
    for i, (vorher, lifecycle, ziel) in enumerate(faelle):
        vid, lid = f"vd{i}_{welt.s}", f"ld{i}_{welt.s}"
        welt.run(db.vehicles.insert_one(welt.fahrzeug(vid, lifecycle)))
        welt.run(db.resale_listings.insert_one(welt.inserat(lid, vid, vorher)))
        body = R.ListingStatusIn(status=ziel, von_status=vorher)
        erst = welt.run(R.set_listing_status(lid, body, welt.chef))
        assert erst == {"ok": True, "status": ziel}, (ziel, erst)
        # zweiter Klick mit demselben (veralteten) von_status
        zweit = welt.run(R.set_listing_status(lid, body, welt.chef))
        assert zweit.get("bereits") is True and zweit["status"] == ziel, (ziel, zweit)
        assert welt.run(db.resale_listings.find_one({"id": lid}))["status"] == ziel


def test_kaeufer_reservierung_bleibt_409_trotz_gleichem_ziel(welt):
    R, db = welt.R, welt.db
    vid, lid = f"vk_{welt.s}", f"lk_{welt.s}"
    welt.run(db.vehicles.insert_one(welt.fahrzeug(vid, "reserviert")))
    welt.run(db.resale_listings.insert_one(welt.inserat(lid, vid, "reserviert",
                                                        reserved_for="k1")))
    e = _fehler(welt, R.set_listing_status(
        lid, R.ListingStatusIn(status="reserviert", von_status="veroeffentlicht"), welt.chef))
    assert e.status_code == 409 and "Reserviert" in e.detail
    # ein anderes Ziel aus veraltetem Tab bleibt ebenfalls 409 (RP-492)
    welt.run(db.resale_listings.update_one({"id": lid}, {"$set": {"status": "zurueckgezogen"},
                                                         "$unset": {"reserved_for": ""}}))
    e = _fehler(welt, R.set_listing_status(
        lid, R.ListingStatusIn(status="entwurf", von_status="veroeffentlicht"), welt.chef))
    assert e.status_code == 409
    assert welt.run(db.resale_listings.find_one({"id": lid}))["status"] == "zurueckgezogen"


# ============================================================ RP-088 Rollback
def _lifecycle_rennen(R, monkeypatch):
    async def _rennen(*_a, **_k):
        raise R.LifecycleError("Fahrzeugstatus wurde zwischenzeitlich geändert")
    monkeypatch.setattr(R, "_lifecycle_anwenden", _rennen)


def test_verkauf_rollback_nimmt_festgeschriebenen_stand_zurueck(welt, monkeypatch):
    R, db = welt.R, welt.db
    vid, lid = f"vr1_{welt.s}", f"lr1_{welt.s}"
    welt.run(db.vehicles.insert_one(welt.fahrzeug(
        vid, "verkaufsbereit", purchase_price=12000,
        bestand={"costs": [{"label": "Reifen", "amount": 400}]})))
    # Altinserat: Einkaufspreis von Hand, ohne Quelle
    welt.run(db.resale_listings.insert_one(welt.inserat(
        lid, vid, "verkaufsbereit", purchase_price=7000,
        costs=[{"label": "Alt", "amount": 50}])))
    _lifecycle_rennen(R, monkeypatch)
    e = _fehler(welt, R.set_listing_status(
        lid, R.ListingStatusIn(status="verkauft", sold_price=15000,
                               von_status="verkaufsbereit"), welt.chef))
    assert e.status_code == 409
    d = welt.run(db.resale_listings.find_one({"id": lid}))
    assert d["status"] == "verkaufsbereit"
    assert d["purchase_price"] == 7000
    assert "purchase_price_quelle" not in d, d.get("purchase_price_quelle")
    assert d["costs"] == [{"label": "Alt", "amount": 50}]
    for feld in ("sold_at", "sold_price", "sold_to_user_id"):
        assert feld not in d
    # die Anzeige behaelt den Hand-Preis (vorher: Vertrags-/Fahrzeugpreis)
    m = welt.run(R.get_listing(lid, welt.chef))["margin"]
    assert m["purchase_price"] == 7000 and m["purchase_price_quelle"] == "inserat"


def test_verkauf_rollback_entfernt_vorher_fehlende_felder(welt, monkeypatch):
    R, db = welt.R, welt.db
    vid, lid = f"vr2_{welt.s}", f"lr2_{welt.s}"
    welt.run(db.vehicles.insert_one(welt.fahrzeug(
        vid, "verkaufsbereit", purchase_price=12000,
        bestand={"costs": [{"label": "Reifen", "amount": 400}]})))
    doc = welt.inserat(lid, vid, "verkaufsbereit")
    doc.pop("costs")
    welt.run(db.resale_listings.insert_one(doc))
    _lifecycle_rennen(R, monkeypatch)
    e = _fehler(welt, R.set_listing_status(
        lid, R.ListingStatusIn(status="verkauft", sold_price=15000), welt.chef))
    assert e.status_code == 409
    d = welt.run(db.resale_listings.find_one({"id": lid}))
    assert d["status"] == "verkaufsbereit"
    for feld in ("purchase_price", "purchase_price_quelle", "costs", "sold_price"):
        assert feld not in d, (feld, d.get(feld))


def test_quelle_inserat_gilt_als_von_hand(welt):
    """Schon beschaedigter Altbestand (Quelle "inserat" an einem aktiven
    Inserat) behaelt seinen Preis statt ihn durch den Fahrzeugpreis zu
    ersetzen."""
    R, db = welt.R, welt.db
    vid, lid = f"vq_{welt.s}", f"lq_{welt.s}"
    welt.run(db.vehicles.insert_one(welt.fahrzeug(vid, "verkaufsbereit", purchase_price=12000)))
    welt.run(db.resale_listings.insert_one(welt.inserat(
        lid, vid, "verkaufsbereit", purchase_price=7000, purchase_price_quelle="inserat")))
    m = welt.run(R.get_listing(lid, welt.chef))["margin"]
    assert m["purchase_price"] == 7000 and m["purchase_price_quelle"] == "inserat"
    # eine echte Quelle (Vertrag) folgt weiter dem Live-Stand
    welt.run(db.resale_listings.update_one({"id": lid},
                                           {"$set": {"purchase_price_quelle": "vertrag"}}))
    m = welt.run(R.get_listing(lid, welt.chef))["margin"]
    assert m["purchase_price"] == 12000 and m["purchase_price_quelle"] == "fahrzeug"
