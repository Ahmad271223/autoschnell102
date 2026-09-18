# -*- coding: utf-8 -*-
"""Beweisdokument nur noch auf Knopfdruck (Wunsch Ahmad 18.09.2026).

Vorher entstand zu JEDEM abgerufenen Inserat automatisch ein Beweis-PDF —
bei 30 Suchern x 150 Vergleichen waeren das rund 3,5 GB am Tag, von denen
fast nichts gebraucht wird. Jetzt gilt:

  1  Der reine Abruf/Vergleich legt nichts mehr an (Schalter
     BEWEIS_AUTOMATISCH=true holt das alte Verhalten zurueck).
  2  POST /beweise/anfordern legt es an — idempotent, nie ein zweites
     "erstes" Dokument je Inserat.
  3  Nur wer das Fahrzeug im eigenen Bereich hat (oder das Inserat selbst
     verglichen hat) darf anfordern.
  4  Die Oberflaeche fragt nach dem Vertragsversand und bietet den Knopf
     in der Beweis-Karte an.
"""
import inspect
import random
import sys
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_beweis_service import _jetzt, welt  # noqa: E402,F401

SRC = Path(__file__).resolve().parents[2] / "frontend" / "src"


def _nutzer(w, rolle="dealer"):
    return {"id": f"u_{w.s}", "dealer_id": w.dealer_id, "role": rolle,
            "is_super_admin": False}


def _fahrzeug(w, n, cache_key=None):
    vid = f"v_{w.s}_{n}"
    w.run(w.db.vehicles.insert_one({
        "id": vid, "dealer_id": w.dealer_id, "lifecycle": "verglichen",
        "quelle": "mobile", "mobile_ad_id": f"bew{w.s}{n}",
        "inserat_schluessel": cache_key or w.ck(n),
        "data": {"make_label": "VW", "model_label": "Golf",
                 "detail_url": f"https://suchen.mobile.de/fahrzeuge/details.html?id=bew{w.s}{n}"}}))
    return vid


# ------------------------------------------------------------------ 1
def test_01_abruf_legt_ohne_schalter_kein_dokument_an(welt, monkeypatch):
    """Der Portalabruf merkt nichts mehr vor — erst mit BEWEIS_AUTOMATISCH."""
    from listing_identity import ensure_cache_indexes, get_or_fetch_listing
    welt.run(ensure_cache_indexes(welt.db))

    async def fetcher(source, item_id, u):
        return {"make_label": "VW", "model_label": "Polo", "list_price": 7000,
                "description": "Abruf", "seller_type": "haendler",
                "images": ["https://img.classistatic.de/a"], "_mock": True}

    def _einmal(nr):
        url = f"https://www.kleinanzeigen.de/s-anzeige/test-auto/{nr}-216-1"
        welt.run(get_or_fetch_listing(welt.db, url, fetcher, ttl_hours=1))
        cache = welt.run(welt.db.listings_cache.find_one({"item_id": str(nr)}))
        return welt.run(welt.db.inserat_beweise.find_one({"cache_key": cache["cache_key"]}))

    nr1 = random.randint(7_100_000_000, 7_199_999_999)
    nr2 = nr1 + 1
    try:
        monkeypatch.delenv("BEWEIS_AUTOMATISCH", raising=False)
        assert _einmal(nr1) is None, "Abruf hat wieder automatisch vorgemerkt"
        monkeypatch.setenv("BEWEIS_AUTOMATISCH", "true")
        zeile = _einmal(nr2)
        assert zeile and zeile["anlass"] == "abruf"
    finally:
        for nr in (nr1, nr2):
            cache = welt.run(welt.db.listings_cache.find_one({"item_id": str(nr)}))
            if cache:
                welt.run(welt.db.inserat_beweise.delete_many({"cache_key": cache["cache_key"]}))
                welt.run(welt.db.listings_cache.delete_many({"cache_key": cache["cache_key"]}))


# ------------------------------------------------------------------ 2
def test_02_anfordern_legt_an_und_bleibt_bei_einem_dokument(welt):
    import routes.beweise as RB
    ck = welt.cache("k1")
    vid = _fahrzeug(welt, "k1", ck)
    user = _nutzer(welt)

    erst = welt.run(RB.beweis_anfordern(RB.AnforderungIn(vehicle_id=vid), user))["beweis"]
    assert erst["status"] == "offen"
    zeile = welt.run(welt.db.inserat_beweise.find_one({"cache_key": ck}))
    assert zeile["anlass"] == "angefordert"
    # Der Stand, den der Sucher gesehen hat, ist eingefroren (nicht erst der
    # Stand von irgendwann spaeter).
    assert zeile["quelle_daten"]["model_label"] == "Golf"

    # Zweiter Klick (oder zweite Firma): dasselbe Dokument, kein zweites.
    nochmal = welt.run(RB.beweis_anfordern(RB.AnforderungIn(vehicle_id=vid), user))["beweis"]
    assert nochmal["id"] == erst["id"]
    assert welt.run(welt.db.inserat_beweise.count_documents({"cache_key": ck})) == 1
    # Und es steht im Protokoll, wer es verlangt hat.
    assert welt.run(welt.db.activity_logs.count_documents(
        {"dealer_id": welt.dealer_id, "action": "beweis.angefordert"})) >= 1
    welt.run(welt.db.activity_logs.delete_many({"dealer_id": welt.dealer_id}))


# ------------------------------------------------------------------ 3
def test_03_nur_der_eigene_bereich_darf_anfordern(welt):
    import routes.beweise as RB
    ck = welt.cache("k2")
    vid = _fahrzeug(welt, "k2", ck)
    fremd = {"id": f"u_fremd_{welt.s}", "dealer_id": welt.anderer, "role": "dealer"}
    with pytest.raises(HTTPException) as e:
        welt.run(RB.beweis_anfordern(RB.AnforderungIn(vehicle_id=vid), fremd))
    assert e.value.status_code == 404
    # Ohne Fahrzeug zaehlt der eigene Vergleich: ohne ihn nichts.
    with pytest.raises(HTTPException) as e2:
        welt.run(RB.beweis_anfordern(RB.AnforderungIn(cache_key=ck), fremd))
    assert e2.value.status_code == 404
    assert welt.run(welt.db.inserat_beweise.count_documents({"cache_key": ck})) == 0

    # Wer das Inserat selbst verglichen hat, darf — auch ohne Fahrzeug.
    welt.run(welt.db.vehicle_comparisons.insert_one({
        "id": str(uuid.uuid4()), "cache_key": ck, "dealer_id": welt.anderer,
        "user_id": fremd["id"], "created_at": _jetzt().isoformat()}))
    doc = welt.run(RB.beweis_anfordern(RB.AnforderungIn(cache_key=ck), fremd))["beweis"]
    assert doc["status"] == "offen"
    welt.run(welt.db.vehicle_comparisons.delete_many({"cache_key": ck}))


# ------------------------------------------------------------------ 4
def test_04_ohne_inseratsdaten_sagt_die_antwort_warum(welt):
    """Der Inseratsspeicher laeuft nach 90 Tagen ab. Dann gibt es nichts
    einzufrieren — die Meldung sagt, was zu tun ist."""
    import routes.beweise as RB
    vid = f"v_leer_{welt.s}"
    welt.run(welt.db.vehicles.insert_one({
        "id": vid, "dealer_id": welt.dealer_id, "lifecycle": "verglichen",
        "inserat_schluessel": welt.ck("k3")}))
    with pytest.raises(HTTPException) as e:
        welt.run(RB.beweis_anfordern(RB.AnforderungIn(vehicle_id=vid), _nutzer(welt)))
    assert e.value.status_code == 404 and "vergleichen" in e.value.detail


# ------------------------------------------------------------------ 5
def test_05_vergleich_und_oberflaeche_fragen_nach(welt):
    import routes.listings as L
    import listing_identity as LI
    q = inspect.getsource(L.compare)
    assert "automatisch_aktiv()" in q and "beweis_fuer_schluessel" in q, (
        "Der Vergleich darf nicht mehr ungefragt vormerken")
    assert "automatisch_aktiv()" in inspect.getsource(LI.get_or_fetch_listing)

    versand = (SRC / "components" / "SendDialog.jsx").read_text(encoding="utf-8")
    assert "/beweise/anfordern" in versand and "beweis-frage" in versand
    assert "Beweisdokument erstellen lassen?" in versand
    assert "beweis-ja-btn" in versand and "beweis-nein-btn" in versand

    karte = (SRC / "components" / "BeweisCard.jsx").read_text(encoding="utf-8")
    assert "/beweise/anfordern" in karte and "beweis-erstellen-btn" in karte
    vergleich = (SRC / "pages" / "app" / "Vergleich.jsx").read_text(encoding="utf-8")
    assert "cacheKey={result.cache_key}" in vergleich
