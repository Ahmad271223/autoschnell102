# -*- coding: utf-8 -*-
"""Rollenpruefung 22.09.2026 — Team markt_haendler (routes/resale.py).

In-Prozess gegen eine Wegwerf-Datenbank (Motor); die Routen-Funktionen
werden direkt mit Fake-`user`-Dicts aufgerufen.

  RP-036/087/525/526  Entwurfsbeschreibung kurz, ohne Portaltext, ohne Eckdaten;
                      Grenze auch bei Veroeffentlichen/Verkaufsbereit
  RP-083/182          paralleles create_draft -> genau EIN Inserat
  RP-088              Einkaufspreis/Kosten live aus dem Fahrzeug, beim Verkauf fest
  RP-090/340          Fahrerfotos: fehlende einzeln ueberspringen, freie Plaetze
  RP-093              Hand-Reservierung, letztes Foto auch bei "reserviert"
  RP-458              ausdruecklich geleerter Preis wird entfernt
  RP-460/467/468      vereinbarter Preis, Profil-Hinweis, Laufzeit
  RP-463/492          Stand-/Statuspruefung gegen veraltete Tabs
  RP-469              Reihenfolge / Titelbild
  RP-474              km und Schaeden aus dem unterschriebenen Protokoll
  RP-518              Rueckweg vor der Abholung (sobald lifecycle.py ihn kennt)
  RP-523              "150 Tkm" = 150.000 km
  RP-532              Upload schaltet Altinserate von "einkauf" auf "beide"
"""
import asyncio
import base64
import io
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"


def _jetzt():
    return datetime.now(timezone.utc).isoformat()


def _vor(**delta):
    return (datetime.now(timezone.utc) - timedelta(**delta)).isoformat()


def _mod(name):
    import importlib
    return importlib.import_module(name)


@pytest.fixture
def welt(monkeypatch):
    from motor.motor_asyncio import AsyncIOMotorClient
    # Eigene Schleife, aber NICHT als aktuelle gesetzt: sonst bleibt nach dem
    # Test eine geschlossene Schleife stehen, und spaetere Testdateien mit
    # asyncio.run()/Modul-Clients scheitern mit "Event loop is closed".
    loop = asyncio.new_event_loop()
    client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000, io_loop=loop)
    name = f"autoschnell_rpmh_{uuid.uuid4().hex[:10]}"
    db = client[name]
    # ERST alles importieren, DANN umbiegen: ein Modul, das erst nach dem
    # Umbiegen von deps.db importiert wird, bindet sich per "from deps import
    # db" dauerhaft an die Wegwerf-DB (samt geschlossener Schleife) — und
    # monkeypatch stellt dann genau diese wieder her. Folge waren
    # "Event loop is closed"-Fehler in spaeteren Testdateien.
    module = [_mod(n) for n in (
        "deps", "routes.resale", "kaufvorgang", "lifecycle", "routes.marketplace",
        "routes.team", "routes.bestand", "cleanup_service", "abholbericht",
        "protokoll_vergleich", "auto_daten", "storage_service", "bild_proxy",
        "dateien")]
    for m in module:
        if hasattr(m, "db"):
            monkeypatch.setattr(m, "db", db)
    team = _mod("routes.team")

    async def _plan(dealer_id):
        return {"active": True, "quota": None, "used": 0, "period_key": "2026-09"}
    monkeypatch.setattr(team, "get_sale_plan_status", _plan)
    s = uuid.uuid4().hex[:8]
    w = SimpleNamespace(db=db, run=loop.run_until_complete, s=s,
                        dealer_id=f"d_rpmh_{s}", R=_mod("routes.resale"))
    w.chef = {"id": f"chef_{s}", "dealer_id": w.dealer_id, "role": "dealer"}

    def fahrzeug(vid, lifecycle="abgeholt", **extra):
        d = {"id": vid, "dealer_id": w.dealer_id, "lifecycle": lifecycle,
             "data": {"make_label": "VW", "model_label": "Golf", "mileage": 90000,
                      "features": ["Navi", "LED-Scheinwerfer", "Sitzheizung"],
                      "description": "Privatverkauf, Tel. 0171 1234567, keine Garantie"},
             "created_at": _jetzt(), "updated_at": _jetzt()}
        d.update(extra)
        return d

    def inserat(lid, vid, status="entwurf", **extra):
        d = {"id": lid, "dealer_id": w.dealer_id, "vehicle_id": vid, "status": status,
             "title": "Golf", "description": "Gepflegt", "known_defects": [],
             "data": {"mileage": 90000},
             "prices": {"public": 9900.0, "b2b": 9000.0, "network": None},
             "photos": {"mode": "neu", "einkauf_urls": [],
                        "uploaded_keys": [f"resale/{w.dealer_id}/{lid}-1.jpg"]},
             "costs": [], "counted_periods": [], "visibility": "public",
             "created_at": _jetzt(), "updated_at": _jetzt()}
        d.update(extra)
        return d

    w.fahrzeug, w.inserat = fahrzeug, inserat
    try:
        yield w
    finally:
        try:
            from storage_service import delete_prefix_async
            for kat in ("pickup", "resale"):
                try:
                    loop.run_until_complete(delete_prefix_async(f"{kat}/{w.dealer_id}/"))
                except Exception:  # noqa: BLE001
                    pass
            loop.run_until_complete(client.drop_database(name))
        finally:
            client.close()
            loop.close()


def _fehler(w, coro):
    with pytest.raises(HTTPException) as e:
        w.run(coro)
    return e.value


def _jpeg_b64():
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (200, 30, 30)).save(buf, format="JPEG")
    return base64.b64encode(buf.getvalue()).decode()


def _jpeg():
    return base64.b64decode(_jpeg_b64())


# ============================================================ RP-523
def test_rp523_kilometer_mit_tausender_einheit():
    R = _mod("routes.resale")
    f = R._fahrzeugwert_bereinigen
    assert f("mileage", "150 Tkm") == (True, 150000)
    assert f("mileage", "150k") == (True, 150000)
    assert f("mileage", "150 Tsd.") == (True, 150000)
    assert f("mileage", "12,5 tausend") == (True, 12500)
    assert f("mileage", "150.000 km") == (True, 150000)
    assert f("mileage", "150000") == (True, 150000)
    # andere "Einheiten" sind kein Kilometerstand mehr (vorher still 150)
    assert f("mileage", "150 PS")[0] is False
    assert f("mileage", "150 Meilen")[0] is False
    assert f("mileage", "3000 Tkm")[0] is False, "ueber 2 Mio. km"
    # Leistung unveraendert
    assert f("power_ps", "110,5") == (True, 111)


# ============================================================ RP-036/525/526
def test_rp036_525_entwurfsbeschreibung_kurz_ohne_portaltext(welt):
    R, db = welt.R, welt.db
    vid = f"v1_{welt.s}"
    lang = [f"Ausstattungsmerkmal Nummer {i}" for i in range(60)]
    welt.run(db.vehicles.insert_one(welt.fahrzeug(vid, data={
        "make_label": "VW", "model_label": "Golf", "mileage": 90000,
        "features": lang, "description": "Privatverkauf, Tel. 0171 1234567"})))
    l = welt.run(R.create_draft(vid, welt.chef))
    beschreibung = l["description"]
    assert len(beschreibung) <= R.INSERAT_BESCHREIBUNG_MAX
    assert beschreibung.startswith("Ausstattung: Ausstattungsmerkmal Nummer 0")
    assert "0171" not in beschreibung and "Privatverkauf" not in beschreibung
    assert "Kilometerstand" not in beschreibung, "Eckdaten zeigt der Kaeufer aus data"
    # der Entwurf laesst sich unveraendert speichern (vorher 422)
    R.ListingUpdateIn(description=beschreibung)
    # ohne Ausstattung: leer statt Portaltext
    assert R._build_description({"description": "Tel. 0171 1234567"}) == ""


def test_rp087_zu_lange_beschreibung_blockiert_verkaufsbereit_und_publish(welt):
    R, db = welt.R, welt.db
    vid, lid = f"v2_{welt.s}", f"l2_{welt.s}"
    welt.run(db.vehicles.insert_one(welt.fahrzeug(vid, "verkaufsentwurf")))
    welt.run(db.resale_listings.insert_one(welt.inserat(lid, vid, "entwurf",
                                                        description="x" * 900)))
    e = _fehler(welt, R.set_listing_status(lid, R.ListingStatusIn(status="verkaufsbereit"),
                                           welt.chef))
    assert e.status_code == 400 and "900 Zeichen" in e.detail
    welt.run(db.resale_listings.update_one({"id": lid}, {"$set": {"status": "verkaufsbereit"}}))
    welt.run(db.vehicles.update_one({"id": vid}, {"$set": {"lifecycle": "verkaufsbereit"}}))
    e = _fehler(welt, R.publish_listing(lid, R.PublishIn(), welt.chef))
    assert e.status_code == 400 and "kürzen" in e.detail
    assert welt.run(db.resale_listings.find_one({"id": lid}))["status"] == "verkaufsbereit"


# ============================================================ RP-083
def test_rp083_paralleles_anlegen_ergibt_ein_inserat(welt):
    R, db = welt.R, welt.db
    vid = f"v3_{welt.s}"
    welt.run(db.vehicles.insert_one(welt.fahrzeug(vid, "abgeholt")))

    async def beide():
        return await asyncio.gather(R.create_draft(vid, welt.chef),
                                    R.create_draft(vid, welt.chef),
                                    R.create_draft(vid, welt.chef))
    ergebnisse = welt.run(beide())
    ids = {r["id"] for r in ergebnisse}
    assert len(ids) == 1, ergebnisse
    assert welt.run(db.resale_listings.count_documents({"vehicle_id": vid})) == 1
    v = welt.run(db.vehicles.find_one({"id": vid}))
    assert v["lifecycle"] == "verkaufsentwurf"
    assert "inserat_entwurf_sperre_token" not in v, "Sperre wieder frei"


def test_rp083_fremde_gueltige_sperre_gibt_409(welt, monkeypatch):
    R, db = welt.R, welt.db
    vid = f"v4_{welt.s}"
    monkeypatch.setattr(R, "_ENTWURF_SPERRE_VERSUCHE", 2)
    welt.run(db.vehicles.insert_one(welt.fahrzeug(
        vid, "abgeholt", inserat_entwurf_sperre_bis=datetime.now(timezone.utc) + timedelta(seconds=30),
        inserat_entwurf_sperre_token="fremd")))
    assert _fehler(welt, R.create_draft(vid, welt.chef)).status_code == 409
    assert welt.run(db.resale_listings.count_documents({"vehicle_id": vid})) == 0


def test_rp083_unique_index_rueckhalt_liefert_vorhandenes(welt, monkeypatch):
    """Sobald der Teil-Unique-Index (Uebergabe Betrieb) steht, faengt
    create_draft den DuplicateKeyError ab und liefert das andere Inserat."""
    R, db = welt.R, welt.db
    vid = f"v4b_{welt.s}"
    welt.run(db.resale_listings.create_index(
        [("dealer_id", 1), ("vehicle_id", 1)], unique=True, name="ein_aktives_je_fahrzeug",
        partialFilterExpression={"status": {"$in": list(R._AKTIV)}}))
    welt.run(db.vehicles.insert_one(welt.fahrzeug(vid, "abgeholt")))
    fremd = welt.inserat(f"fremd_{welt.s}", vid, "entwurf")

    async def _rennen(vehicle_id, dealer_id):
        # zwischen "gibt es schon eins?" und dem Einfuegen legt ein anderer
        # Prozess (ohne Sperre, Altstand) ein Inserat an
        await db.resale_listings.insert_one(dict(fremd))
        return {}
    monkeypatch.setattr(R, "_protokoll_befund", _rennen)
    r = welt.run(R.create_draft(vid, welt.chef))
    assert r["id"] == fremd["id"]
    assert welt.run(db.resale_listings.count_documents({"vehicle_id": vid})) == 1


def test_rp083_publish_blockiert_zweites_aktives_inserat(welt):
    R, db = welt.R, welt.db
    vid = f"v5_{welt.s}"
    welt.run(db.vehicles.insert_one(welt.fahrzeug(vid, "veroeffentlicht")))
    welt.run(db.resale_listings.insert_many([
        welt.inserat(f"alt_{welt.s}", vid, "veroeffentlicht"),
        welt.inserat(f"neu_{welt.s}", vid, "verkaufsbereit")]))
    e = _fehler(welt, R.publish_listing(f"neu_{welt.s}", R.PublishIn(), welt.chef))
    assert e.status_code == 409 and "anderes" in e.detail


# ============================================================ RP-474
def test_rp474_protokoll_km_und_schaeden_im_entwurf(welt):
    R, db = welt.R, welt.db
    vid = f"v6_{welt.s}"
    welt.run(db.vehicles.insert_one(welt.fahrzeug(vid, "abgeholt")))
    welt.run(db.appointments.insert_many([
        {"id": f"a_ok_{welt.s}", "dealer_id": welt.dealer_id, "vehicle_id": vid, "status": "abgeholt"},
        {"id": f"a_st_{welt.s}", "dealer_id": welt.dealer_id, "vehicle_id": vid, "status": "storniert"}]))
    welt.run(db.pickup_protocols.insert_many([
        {"id": f"p_ok_{welt.s}", "dealer_id": welt.dealer_id, "vehicle_id": vid,
         "appointment_id": f"a_ok_{welt.s}", "status": "final", "superseded": False,
         "finalized_at": _vor(days=1), "condition": {"mileage": "123.456 km"},
         "new_damages": [{"type_label": "Kratzer", "zone": "Tür vorne links"}]},
        # Termin storniert (V-12): Protokoll bleibt Nachweis, zaehlt aber nicht
        {"id": f"p_st_{welt.s}", "dealer_id": welt.dealer_id, "vehicle_id": vid,
         "appointment_id": f"a_st_{welt.s}", "status": "final", "superseded": False,
         "finalized_at": _jetzt(), "condition": {"mileage": "999.999"},
         "new_damages": [{"type_label": "Totalschaden", "zone": "Front"}]}]))
    l = welt.run(R.create_draft(vid, welt.chef))
    assert l["data"]["mileage"] == 123456
    assert "Kratzer: Tür vorne links" in l["known_defects"]
    assert not any("Totalschaden" in m for m in l["known_defects"])
    assert any("Abholprotokoll" in n for n in l["auto_notes"])


# ============================================================ RP-088
def test_rp088_einkaufspreis_und_kosten_live_und_beim_verkauf_fest(welt):
    R, db = welt.R, welt.db
    vid, lid = f"v7_{welt.s}", f"l7_{welt.s}"
    welt.run(db.vehicles.insert_one(welt.fahrzeug(
        vid, "verkaufsbereit", purchase_price=12000,
        bestand={"costs": [{"label": "Transport", "amount": 300}]})))
    welt.run(db.resale_listings.insert_one(welt.inserat(
        lid, vid, "verkaufsbereit", purchase_price=10000, purchase_price_quelle="vertrag",
        costs=[])))
    l = welt.run(R.get_listing(lid, welt.chef))
    assert l["margin"]["purchase_price"] == 12000 and l["margin"]["costs_total"] == 300
    assert l["margin"]["purchase_price_quelle"] == "fahrzeug"
    liste = welt.run(R.list_listings(__import__("fastapi").Response(), welt.chef))
    assert next(i for i in liste if i["id"] == lid)["margin"]["total_cost"] == 12300
    # Kosten in der Akte nachgetragen -> sofort in der Marge
    welt.run(db.vehicles.update_one({"id": vid}, {"$push": {"bestand.costs": {"label": "Politur", "amount": 200}}}))
    assert welt.run(R.get_listing(lid, welt.chef))["margin"]["costs_total"] == 500
    # Verkauf schreibt den Stand fest
    welt.run(R.set_listing_status(lid, R.ListingStatusIn(status="verkauft", sold_price=15000), welt.chef))
    d = welt.run(db.resale_listings.find_one({"id": lid}))
    assert d["purchase_price"] == 12000 and sum(c["amount"] for c in d["costs"]) == 500
    welt.run(db.vehicles.update_one({"id": vid}, {"$push": {"bestand.costs": {"label": "Spaeter", "amount": 999}}}))
    m = welt.run(R.get_listing(lid, welt.chef))["margin"]
    assert m["costs_total"] == 500, "verkaufte Inserate behalten ihren Stand"


def test_rp088_altinserat_mit_preis_ohne_quelle_bleibt(welt):
    R, db = welt.R, welt.db
    vid, lid = f"v8_{welt.s}", f"l8_{welt.s}"
    welt.run(db.vehicles.insert_one(welt.fahrzeug(
        vid, "verkaufsbereit", purchase_price=12000,
        bestand={"costs": [{"label": "Reifen", "amount": 400}]})))
    welt.run(db.resale_listings.insert_one(welt.inserat(lid, vid, "verkaufsbereit",
                                                        purchase_price=7000)))
    m = welt.run(R.get_listing(lid, welt.chef))["margin"]
    assert m["purchase_price"] == 7000 and m["purchase_price_quelle"] == "inserat"
    assert m["costs_total"] == 400, "Kosten kommen trotzdem aus der Akte"


# ============================================================ RP-458 / RP-463 / RP-523
def test_rp458_geleerter_preis_wird_entfernt(welt):
    R, db = welt.R, welt.db
    vid, lid = f"v9_{welt.s}", f"l9_{welt.s}"
    welt.run(db.resale_listings.insert_one(welt.inserat(lid, vid, "verkaufsbereit",
                                                        prices={"public": 9900.0, "b2b": 9000.0,
                                                                "network": 8500.0})))
    r = welt.run(R.update_listing(lid, R.ListingUpdateIn.model_validate(
        {"price_b2b": None, "price_network": None}), welt.chef))
    assert r["prices"] == {"public": 9900.0, "b2b": None, "network": None}
    # der oeffentliche Preis bleibt Pflicht
    e = _fehler(welt, R.update_listing(lid, R.ListingUpdateIn.model_validate(
        {"price_public": None}), welt.chef))
    assert e.status_code == 400
    # nicht gesendet = unveraendert (wie bisher)
    r = welt.run(R.update_listing(lid, R.ListingUpdateIn(title="Neu"), welt.chef))
    assert r["prices"]["public"] == 9900.0


def test_rp463_veralteter_stand_gibt_409(welt):
    R, db = welt.R, welt.db
    vid, lid = f"v10_{welt.s}", f"l10_{welt.s}"
    doc = welt.inserat(lid, vid, "veroeffentlicht")
    welt.run(db.resale_listings.insert_one(doc))
    e = _fehler(welt, R.update_listing(lid, R.ListingUpdateIn(
        price_public=1000, stand="2020-01-01T00:00:00+00:00"), welt.chef))
    assert e.status_code == 409 and "neu laden" in e.detail
    assert welt.run(db.resale_listings.find_one({"id": lid}))["prices"]["public"] == 9900.0
    r = welt.run(R.update_listing(lid, R.ListingUpdateIn(
        price_public=10500, stand=doc["updated_at"], data={"mileage": "150 Tkm"}), welt.chef))
    assert r["prices"]["public"] == 10500 and r["data"]["mileage"] == 150000
    assert r["updated_at"] != doc["updated_at"]


# ============================================================ RP-492 / RP-093
def test_rp492_veralteter_tab_trifft_keine_b2b_reservierung(welt):
    R, db = welt.R, welt.db
    vid, lid = f"v11_{welt.s}", f"l11_{welt.s}"
    welt.run(db.vehicles.insert_one(welt.fahrzeug(vid, "reserviert")))
    welt.run(db.resale_listings.insert_one(welt.inserat(lid, vid, "reserviert",
                                                        reserved_for="k1")))
    for body in (R.ListingStatusIn(status="verkauft", sold_price=9000, von_status="veroeffentlicht"),
                 R.ListingStatusIn(status="reserviert", von_status="veroeffentlicht")):
        e = _fehler(welt, R.set_listing_status(lid, body, welt.chef))
        assert e.status_code == 409 and "Reserviert" in e.detail
    d = welt.run(db.resale_listings.find_one({"id": lid}))
    assert d["status"] == "reserviert" and d["reserved_for"] == "k1"


def test_rp093_hand_reservierung_beendet_offene_anfragen(welt):
    R, db = welt.R, welt.db
    vid, lid = f"v12_{welt.s}", f"l12_{welt.s}"
    welt.run(db.vehicles.insert_one(welt.fahrzeug(vid, "veroeffentlicht")))
    welt.run(db.resale_listings.insert_one(welt.inserat(lid, vid, "veroeffentlicht")))
    welt.run(db.listing_interest.insert_many([
        {"id": f"i1_{welt.s}", "listing_id": lid, "dealer_id": welt.dealer_id,
         "status": "offen", "history": []},
        {"id": f"i2_{welt.s}", "listing_id": lid, "dealer_id": welt.dealer_id,
         "status": "gegenangebot_kaeufer", "history": []}]))
    welt.run(R.set_listing_status(lid, R.ListingStatusIn(status="reserviert",
                                                         von_status="veroeffentlicht"), welt.chef))
    d = welt.run(db.resale_listings.find_one({"id": lid}))
    assert d["status"] == "reserviert" and d["reserviert_manuell"] is True
    for iid in (f"i1_{welt.s}", f"i2_{welt.s}"):
        it = welt.run(db.listing_interest.find_one({"id": iid}))
        assert it["status"] == "abgelehnt" and it["beendet_grund"] == "inserat_reserviert"
    # Aufheben: Kennzeichen weg
    welt.run(R.set_listing_status(lid, R.ListingStatusIn(status="verkaufsbereit"), welt.chef))
    d = welt.run(db.resale_listings.find_one({"id": lid}))
    assert d["status"] == "verkaufsbereit" and "reserviert_manuell" not in d


def test_rp093_letztes_foto_auch_bei_reserviert_geschuetzt(welt):
    R, db = welt.R, welt.db
    vid = f"v13_{welt.s}"
    key = f"resale/{welt.dealer_id}/l13-1.jpg"
    welt.run(db.resale_listings.insert_many([
        welt.inserat(f"l13_{welt.s}", vid, "reserviert", reserved_for="k1",
                     photos={"mode": "neu", "einkauf_urls": [], "uploaded_keys": [key]}),
        welt.inserat(f"l13e_{welt.s}", vid + "e", "veroeffentlicht",
                     photos={"mode": "einkauf", "einkauf_urls": ["https://x/1.jpg"],
                             "uploaded_keys": []})]))
    e = _fehler(welt, R.remove_photo(f"l13_{welt.s}", R.PhotoRemoveIn(key=key), welt.chef))
    assert e.status_code == 400 and "letzte Foto" in e.detail
    e = _fehler(welt, R.remove_photo(f"l13e_{welt.s}", R.PhotoRemoveIn(url="https://x/1.jpg"),
                                     welt.chef))
    assert e.status_code == 400
    assert welt.run(db.resale_listings.find_one({"id": f"l13e_{welt.s}"}))["photos"]["einkauf_urls"] \
        == ["https://x/1.jpg"]


# ============================================================ RP-460 / RP-467 / RP-468
def test_rp460_467_468_editor_bekommt_preis_profil_und_laufzeit(welt):
    R, db = welt.R, welt.db
    vid, lid = f"v14_{welt.s}", f"l14_{welt.s}"
    welt.run(db.resale_listings.insert_one(welt.inserat(
        lid, vid, "reserviert", reserved_for="k1", published_at=_vor(days=5))))
    welt.run(db.listing_interest.insert_one(
        {"id": f"i14_{welt.s}", "listing_id": lid, "dealer_id": welt.dealer_id,
         "buyer_user_id": "k1", "status": "akzeptiert", "agreed_price": 18500.0}))
    l = welt.run(R.get_listing(lid, welt.chef))
    assert l["vereinbarter_preis"] == 18500.0
    assert l["marktplatz_profil_oeffentlich"] is False
    bis = datetime.fromisoformat(l["laufzeit_bis"])
    rest = bis - datetime.now(timezone.utc)
    assert timedelta(days=R.INSERAT_LAUFZEIT_TAGE - 6) < rest < timedelta(days=R.INSERAT_LAUFZEIT_TAGE - 4)
    welt.run(db.dealers.insert_one({"id": welt.dealer_id, "marketplace": {"public": True}}))
    assert welt.run(R.get_listing(lid, welt.chef))["marktplatz_profil_oeffentlich"] is True


def test_rp467_publish_meldet_privates_profil(welt):
    R, db = welt.R, welt.db
    vid, lid = f"v15_{welt.s}", f"l15_{welt.s}"
    welt.run(db.vehicles.insert_one(welt.fahrzeug(vid, "verkaufsbereit")))
    welt.run(db.resale_listings.insert_one(welt.inserat(lid, vid, "verkaufsbereit")))
    r = welt.run(R.publish_listing(lid, R.PublishIn(visibility="public"), welt.chef))
    assert r["status"] == "veroeffentlicht" and r["profil_oeffentlich"] is False
    assert "nicht öffentlich" in r["hinweis"]


def test_rp468_nach_ablauf_keine_wiederveroeffentlichung(welt):
    R, db = welt.R, welt.db
    vid, lid = f"v16_{welt.s}", f"l16_{welt.s}"
    welt.run(db.vehicles.insert_one(welt.fahrzeug(vid, "verkaufsbereit")))
    welt.run(db.resale_listings.insert_one(welt.inserat(
        lid, vid, "zurueckgezogen", published_at=_vor(days=R.INSERAT_LAUFZEIT_TAGE + 2),
        counted_periods=["2026-08"])))
    e = _fehler(welt, R.publish_listing(lid, R.PublishIn(visibility="private"), welt.chef))
    assert e.status_code == 409 and "Laufzeit" in e.detail
    d = welt.run(db.resale_listings.find_one({"id": lid}))
    assert d["status"] == "zurueckgezogen" and d["counted_periods"] == ["2026-08"]
    assert "publish_lock_token" not in d
    # innerhalb der Laufzeit geht es weiter (mit der gewuenschten Sichtbarkeit)
    welt.run(db.resale_listings.update_one({"id": lid}, {"$set": {"published_at": _vor(days=3)}}))
    r = welt.run(R.publish_listing(lid, R.PublishIn(visibility="private"), welt.chef))
    assert r["visibility"] == "private"


# ============================================================ RP-469
def test_rp469_reihenfolge_und_titelbild(welt):
    R, db = welt.R, welt.db
    lid = f"l17_{welt.s}"
    keys = [f"resale/{welt.dealer_id}/{i}.jpg" for i in "abc"]
    welt.run(db.resale_listings.insert_one(welt.inserat(
        lid, f"v17_{welt.s}", "veroeffentlicht",
        photos={"mode": "neu", "einkauf_urls": [], "uploaded_keys": keys})))
    r = welt.run(R.fotos_reihenfolge(lid, R.FotoReihenfolgeIn(keys=[keys[2], keys[0], keys[1]]),
                                     welt.chef))
    assert r["uploaded_keys"][0] == keys[2]
    assert welt.run(db.resale_listings.find_one({"id": lid}))["photos"]["uploaded_keys"] \
        == [keys[2], keys[0], keys[1]]
    e = _fehler(welt, R.fotos_reihenfolge(lid, R.FotoReihenfolgeIn(keys=[keys[0], keys[1]]),
                                          welt.chef))
    assert e.status_code == 409
    e = _fehler(welt, R.fotos_reihenfolge(lid, R.FotoReihenfolgeIn(
        keys=[keys[0], keys[1], "resale/fremd/x.jpg"]), welt.chef))
    assert e.status_code == 409


# ============================================================ RP-090 / RP-340
def test_rp090_fahrerfotos_fehlende_einzeln_uebersprungen(welt):
    R, db = welt.R, welt.db
    from storage_service import make_key, storage
    vid, lid = f"v18_{welt.s}", f"l18_{welt.s}"
    ok_key = make_key("pickup", welt.dealer_id, "foto.jpg")
    storage.save(ok_key, _jpeg())
    weg_key = make_key("pickup", welt.dealer_id, "foto.jpg")          # nie gespeichert
    offen_key = make_key("pickup", welt.dealer_id, "foto.jpg")
    welt.run(db.pickup_reports.insert_one(
        {"id": f"r18_{welt.s}", "dealer_id": welt.dealer_id, "vehicle_id": vid,
         "appointment_id": "a18", "superseded": False, "version": 1, "created_at": _jetzt(),
         "deviations": [{"label": "A", "photo_key": ok_key},
                        {"label": "B", "photo_key": weg_key},
                        {"label": "C", "photo_key": offen_key, "photo_loeschung_offen": True}]}))
    welt.run(db.resale_listings.insert_one(welt.inserat(
        lid, vid, "entwurf", photos={"mode": "neu", "einkauf_urls": [], "uploaded_keys": []})))
    vorher = welt.run(R.get_listing(lid, welt.chef))["abholfotos"]
    assert [f["key"] for f in vorher] == [ok_key, weg_key], "vorgemerkte Loeschung nicht angeboten"
    r = welt.run(R.fotos_aus_abholbericht(lid, None, welt.chef))
    assert r["uebernommen"] == 1 and r["fehlend"] == 1
    d = welt.run(db.resale_listings.find_one({"id": lid}))
    assert len(d["photos"]["uploaded_keys"]) == 1
    assert d["photos"]["aus_abholbericht"] == [ok_key]
    assert d["photos"]["abholfotos_fehlend"] == [weg_key]
    assert welt.run(R.get_listing(lid, welt.chef))["abholfotos"] == []


def test_rp340_mehr_fahrerfotos_als_freie_plaetze(welt):
    R, db = welt.R, welt.db
    from storage_service import make_key, storage
    vid, lid = f"v19_{welt.s}", f"l19_{welt.s}"
    k1, k2 = make_key("pickup", welt.dealer_id, "foto.jpg"), make_key("pickup", welt.dealer_id, "foto.jpg")
    storage.save(k1, _jpeg())
    storage.save(k2, _jpeg())
    welt.run(db.pickup_reports.insert_one(
        {"id": f"r19_{welt.s}", "dealer_id": welt.dealer_id, "vehicle_id": vid,
         "appointment_id": "a19", "superseded": False, "version": 1, "created_at": _jetzt(),
         "deviations": [{"label": "A", "photo_key": k1}, {"label": "B", "photo_key": k2}]}))
    voll = [f"resale/{welt.dealer_id}/x{i}.jpg" for i in range(R.INSERAT_FOTOS_MAX - 1)]
    welt.run(db.resale_listings.insert_one(welt.inserat(
        lid, vid, "entwurf", photos={"mode": "neu", "einkauf_urls": [], "uploaded_keys": voll})))
    r = welt.run(R.fotos_aus_abholbericht(lid, None, welt.chef))
    assert r["uebernommen"] == 1 and r["kein_platz"] == 1
    d = welt.run(db.resale_listings.find_one({"id": lid}))
    assert len(d["photos"]["uploaded_keys"]) == R.INSERAT_FOTOS_MAX


# ============================================================ RP-532
def test_rp532_upload_schaltet_altinserat_auf_beide(welt):
    R, db = welt.R, welt.db
    lid = f"l20_{welt.s}"
    welt.run(db.resale_listings.insert_one(welt.inserat(
        lid, f"v20_{welt.s}", "entwurf",
        photos={"mode": "einkauf", "einkauf_urls": ["https://x/1.jpg"], "uploaded_keys": []})))
    r = welt.run(R.upload_photos(lid, R.PhotoUploadIn(photos_b64=[_jpeg_b64()]), welt.chef))
    assert r["total"] == 1
    assert welt.run(db.resale_listings.find_one({"id": lid}))["photos"]["mode"] == "beide"


# ============================================================ RP-518
def test_rp518_rueckweg_vor_der_abholung(welt, monkeypatch):
    R, db = welt.R, welt.db
    # Welle 2: lifecycle.py kennt den Rueckweg jetzt (termine_protokoll,
    # VERKAUF_RUECKWEG_VOR_ABHOLUNG) — delete_listing nimmt ihn ohne Attrappe.
    assert R._pfad_zurueck("veroeffentlicht", "abholung_geplant") == \
        ["verkaufsbereit", "abholung_geplant"]
    vid = f"v21_{welt.s}"
    welt.run(db.vehicles.insert_one(welt.fahrzeug(vid, "verkaufsentwurf")))
    welt.run(db.kaufvorgaenge.insert_one({"id": f"kv_{welt.s}", "dealer_id": welt.dealer_id,
                                          "vehicle_id": vid, "status": "abholung_geplant"}))
    assert welt.run(R._zustand_vor_abholung(vid, welt.dealer_id)) == "abholung_geplant"
    assert R._pfad_zurueck("reserviert", "abholung_geplant") == \
        ["veroeffentlicht", "verkaufsbereit", "abholung_geplant"]
    lid = f"l21_{welt.s}"
    welt.run(db.vehicles.update_one({"id": vid}, {"$set": {"lifecycle": "verkaufsbereit"}}))
    welt.run(db.resale_listings.insert_one(welt.inserat(lid, vid, "verkaufsbereit")))
    welt.run(R.delete_listing(lid, welt.chef))
    assert welt.run(db.vehicles.find_one({"id": vid}))["lifecycle"] == "abholung_geplant"
    # abgeholtes Fahrzeug -> kein Rueckweg
    welt.run(db.vehicles.update_one({"id": vid}, {"$set": {"purchase_price": 5000}}))
    assert welt.run(R._zustand_vor_abholung(vid, welt.dealer_id)) is None
