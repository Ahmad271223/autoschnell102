# -*- coding: utf-8 -*-
"""Runde 23 (11.09.2026): Beweisdokument-Daten beim ersten Gebrauch einfrieren.

Befund (bestaetigt): beweis_vormerken speicherte nur Inseratschluessel und
Metadaten; der Worker las die Fahrzeugdaten spaeter aus dem veraenderlichen
listings_cache. Nach Verzoegerung, Fehlversuch, Wiederbelebung oder erneutem
Portalabruf enthielt das PDF einen neueren Stand als beim Erstgebrauch.

In-Prozess gegen eine eigene Wegwerf-Datenbank (wird gedroppt), Fotos ohne
Netz, kein Server-Import."""
import asyncio
import io
import json
import os
import random
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from PIL import Image
from pypdf import PdfReader

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
T0 = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)   # "Erstgebrauch"


def _jetzt():
    return datetime.now(timezone.utc)


def _utc(wert):
    return wert if wert.tzinfo else wert.replace(tzinfo=timezone.utc)


def _jpeg() -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (600, 400), (30, 90, 160)).save(b, "JPEG", quality=80)
    return b.getvalue()


def _daten(item, preis=9990, text="Alter Stand", **mehr):
    d = {"make_label": "VW", "model_label": "Golf", "mobile_ad_id": item,
         "list_price": preis, "description": text, "seller_type": "haendler",
         "seller_name": "Autohaus Test",
         "images": ["https://img.classistatic.de/a", "https://img.classistatic.de/b"]}
    d.update(mehr)
    return d


@pytest.fixture
def welt(monkeypatch):
    from motor.motor_asyncio import AsyncIOMotorClient
    import beweis_service as BS
    import bild_proxy
    import provider_limiter

    # Runde 23 (11.09.2026, Gegenpruefung): test_08 ruft das echte
    # get_or_fetch_listing; acquire_slot setzte dabei einen PROZESSWEITEN
    # Merker _indexes_ready. Blieb er stehen, legte ein spaeterer Test mit
    # frischer Datenbank (test_listing_cache) weder den Unique-Index auf
    # provider_limits.provider noch die Zaehler an -> Limit vervielfacht.
    # Runde 31 (12.09.2026): Der Merker haengt jetzt am Datenbanknamen, das
    # Zuruecksetzen ist damit nicht mehr noetig. Es bleibt als Guertel zum
    # Hosentraeger stehen — und haelt fest, dass der Merker eine MENGE ist.
    assert isinstance(provider_limiter._indexes_ready, set)
    monkeypatch.setattr(provider_limiter, "_indexes_ready", set())

    class _W:
        pass

    w = _W()
    w.s = uuid.uuid4().hex[:10]
    w.loop = asyncio.new_event_loop()
    asyncio.set_event_loop(w.loop)
    w.client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    w.db = w.client[f"autoschnell_r23_beweis_{w.s}"]
    w.run = lambda coro: w.loop.run_until_complete(coro)
    w.keys = []
    w.run(BS.ensure_beweis_indexes(w.db))

    async def _foto(url, kante=800, qualitaet=0):   # "a" liefert ein Bild, alles andere nichts
        return _jpeg() if url.endswith("/a") else None

    monkeypatch.setattr(bild_proxy, "laden_fuer_pdf", _foto)
    yield w
    try:
        for d in w.run(w.db.inserat_beweise.find({}, {"pdf_key": 1, "alle_keys": 1})
                       .to_list(1000)):
            w.keys.extend([d.get("pdf_key")] + list(d.get("alle_keys") or []))
        from storage_service import delete_async
        for k in {k for k in w.keys if k}:
            try:
                w.run(delete_async(k))
            except Exception:
                pass
        w.run(w.client.drop_database(w.db.name))
    finally:
        w.client.close()
        w.loop.close()


def _ck(w, n):
    return f"mobile:r23{w.s}{n}"


def _vormerken(w, ck, daten=None, abgerufen_am=None, anlass="abruf"):
    import beweis_service as BS
    quelle, item = ck.split(":", 1)
    return w.run(BS.beweis_vormerken(
        w.db, cache_key=ck, quelle=quelle, item_id=item,
        url=f"https://suchen.mobile.de/fahrzeuge/details.html?id={item}",
        anlass=anlass, daten=daten, abgerufen_am=abgerufen_am))


def _cache(w, ck, daten, fetched_at=None):
    quelle, item = ck.split(":", 1)
    w.run(w.db.listings_cache.update_one({"cache_key": ck}, {"$set": {
        "cache_key": ck, "source": quelle, "item_id": item,
        "url": f"https://suchen.mobile.de/fahrzeuge/details.html?id={item}",
        "data": daten, "fetched_at": fetched_at or _jetzt()}}, upsert=True))


def _durchlaufen(w):
    """Eine Zeile beanspruchen und bearbeiten (wie die Worker-Schleife)."""
    import beweis_service as BS
    doc = w.run(BS._beanspruchen(w.db))
    assert doc, "keine offene Zeile"
    w.run(BS._bearbeiten(w.db, doc))
    return w.run(w.db.inserat_beweise.find_one({"id": doc["id"]}, {"_id": 0}))


def _pdf_text(w, zeile) -> str:
    from storage_service import load_async
    w.keys.append(zeile["pdf_key"])
    pdf = w.run(load_async(zeile["pdf_key"]))
    return "".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf)).pages)


# ---------------------------------------------------------------- Befund --
def test_01_pdf_zeigt_den_erststand_auch_nach_fehlversuch_und_neuem_cache(welt, monkeypatch):
    import beweis_pdf as BP
    import storage_service
    ck = _ck(welt, "a")
    item = ck.split(":", 1)[1]
    alt = _daten(item)
    _cache(welt, ck, alt, fetched_at=T0)
    _vormerken(welt, ck, daten=alt, abgerufen_am=T0)

    # Erster Versuch scheitert (Speicher kurz weg) ...
    echt = storage_service.save_async
    aufrufe = {"n": 0}

    async def _stoerung(*a, **k):
        aufrufe["n"] += 1
        if aufrufe["n"] == 1:
            raise RuntimeError("Speicher kurz weg")
        return await echt(*a, **k)

    monkeypatch.setattr(storage_service, "save_async", _stoerung)
    zeile = _durchlaufen(welt)
    assert zeile["status"] == "offen" and "Speicher kurz weg" in zeile["fehler"]
    # ... in der Zwischenzeit ruft jemand das Inserat neu ab (neuer Stand).
    _cache(welt, ck, _daten(item, preis=12345, text="Neuer Stand",
                            images=["https://img.classistatic.de/neu"]), fetched_at=_jetzt())
    welt.run(welt.db.inserat_beweise.update_one({"cache_key": ck},
                                                {"$set": {"naechster_versuch_ab": None}}))
    zeile = _durchlaufen(welt)
    assert zeile["status"] == "fertig", zeile.get("fehler")
    text = _pdf_text(welt, zeile)
    assert "Alter Stand" in text and "9.990" in text
    assert "Neuer Stand" not in text and "12.345" not in text
    assert "classistatic.de/neu" not in text
    assert BP.zeit_text(T0) in text, "Abrufzeit des Erstgebrauchs im PDF"
    assert _utc(zeile["daten_abgerufen_am"]) == T0
    assert zeile["fotos_gesamt"] == 2 and zeile["fotos_eingebettet"] == 1


def test_02_spaetere_vormerkungen_ueberschreiben_den_stand_nie(welt):
    import beweis_service as BS
    ck = _ck(welt, "b")
    item = ck.split(":", 1)[1]
    staende = [_daten(item, preis=1000 + i, text=f"Stand {i}") for i in range(12)]

    async def _viele():
        return await asyncio.gather(*(BS.beweis_vormerken(
            welt.db, cache_key=ck, quelle="mobile", item_id=item, url="https://x",
            anlass="abruf", daten=d, abgerufen_am=T0 + timedelta(minutes=i))
            for i, d in enumerate(staende)))

    erg = welt.run(_viele())
    assert len({e["id"] for e in erg}) == 1
    assert welt.run(welt.db.inserat_beweise.count_documents({"cache_key": ck})) == 1
    assert all("quelle_daten" not in e for e in erg), "nie in der oeffentlichen Antwort"
    zeile = welt.run(welt.db.inserat_beweise.find_one({"cache_key": ck}))
    gewonnen = zeile["quelle_daten"]
    i = next(i for i, d in enumerate(staende) if d["description"] == gewonnen["description"])
    assert gewonnen["list_price"] == 1000 + i
    assert _utc(zeile["quelle_abgerufen_am"]) == T0 + timedelta(minutes=i), \
        "Stand und Abrufzeit stammen aus derselben (ersten) Vormerkung"

    # Spaeter: Vergleich einer anderen Firma, neuer Portalabruf — nichts aendert sich.
    _vormerken(welt, ck, daten=_daten(item, preis=5, text="Spaeter"), abgerufen_am=_jetzt(),
               anlass="vergleich")
    _vormerken(welt, ck, anlass="vergleich")
    nachher = welt.run(welt.db.inserat_beweise.find_one({"cache_key": ck}))
    assert nachher["quelle_daten"] == gewonnen
    assert nachher["quelle_abgerufen_am"] == zeile["quelle_abgerufen_am"]

    # Endgueltig gescheitert und beim naechsten Gebrauch wiederbelebt: Stand bleibt.
    welt.run(welt.db.inserat_beweise.update_one({"cache_key": ck}, {"$set": {
        "status": "fehlgeschlagen", "versuche": 3,
        "fehlgeschlagen_am": _jetzt() - timedelta(hours=1)}}))
    assert _vormerken(welt, ck, daten=_daten(item, text="Wiederbelebt"),
                      abgerufen_am=_jetzt())["status"] == "offen"
    assert welt.run(welt.db.inserat_beweise.find_one({"cache_key": ck}))["quelle_daten"] == gewonnen


def test_03_altbestand_ohne_quelle_daten_nutzt_weiter_den_cache(welt):
    ck = _ck(welt, "c")
    item = ck.split(":", 1)[1]
    _vormerken(welt, ck)                      # wie routes/listings.compare: ohne Daten
    zeile = welt.run(welt.db.inserat_beweise.find_one({"cache_key": ck}))
    assert "quelle_daten" not in zeile and "quelle_abgerufen_am" not in zeile
    _cache(welt, ck, _daten(item, text="Stand im Zwischenspeicher"), fetched_at=T0)
    zeile = _durchlaufen(welt)
    assert zeile["status"] == "fertig", zeile.get("fehler")
    assert "Stand im Zwischenspeicher" in _pdf_text(welt, zeile)
    assert _utc(zeile["daten_abgerufen_am"]) == T0
    # Leere oder nur interne Daten frieren nichts ein (-> Cache wie bisher).
    for n, leer in (("c1", {}), ("c2", {"_mock": True, "images_thumbs": ["x"]})):
        _vormerken(welt, _ck(welt, n), daten=leer)
        assert "quelle_daten" not in welt.run(
            welt.db.inserat_beweise.find_one({"cache_key": _ck(welt, n)}))


def test_04_eingefrorener_stand_braucht_den_cache_nicht_mehr(welt):
    """listings_cache verfaellt per TTL-Index — frueher scheiterte eine
    verzoegerte Erzeugung dann an "Inseratsdaten fehlen"."""
    ck = _ck(welt, "d")
    _vormerken(welt, ck, daten=_daten(ck.split(":", 1)[1], text="Nur eingefroren"),
               abgerufen_am=T0)
    assert not welt.run(welt.db.listings_cache.find_one({"cache_key": ck}))
    zeile = _durchlaufen(welt)
    assert zeile["status"] == "fertig", zeile.get("fehler")
    assert "Nur eingefroren" in _pdf_text(welt, zeile)


def test_05_verfall_entfernt_quelle_daten_grabstein_ohne_daten(welt):
    import beweis_service as BS
    ck = _ck(welt, "e")
    item = ck.split(":", 1)[1]
    _vormerken(welt, ck, daten=_daten(item), abgerufen_am=T0)
    alt = _jetzt() - timedelta(days=BS.BEWEIS_AUFBEWAHRUNG_TAGE + 5)
    welt.run(welt.db.inserat_beweise.update_one({"cache_key": ck}, {"$set": {
        "status": "fertig", "erstellt_am": alt}}))
    assert welt.run(BS.beweise_verfallen(welt.db)) == 1
    zeile = welt.run(welt.db.inserat_beweise.find_one({"cache_key": ck}))
    assert zeile["status"] == "geloescht" and "quelle_daten" not in zeile
    # Derselbe Link spaeter erneut: Grabstein bleibt, kein neuer Datenstand.
    assert _vormerken(welt, ck, daten=_daten(item, text="Neu"), abgerufen_am=_jetzt())["status"] \
        == "geloescht"
    assert "quelle_daten" not in welt.run(welt.db.inserat_beweise.find_one({"cache_key": ck}))


# ---------------------------------------------------------- Datenschutz --
def _privat(**mehr):
    d = {"make_label": "VW", "model_label": "Golf", "list_price": 5000,
         "title": "Golf 7 – Tel. 0151 23456789",
         "description": "Bitte nur anrufen: 0171 1234567 oder max.mustermann@example.de",
         "seller_type": "privat", "seller_name": "Max Mustermann",
         "seller_phone": "0171 1234567", "seller_email": "max.mustermann@example.de",
         "seller_address": "Musterweg 12", "seller_zip": "10115", "seller_city": "Berlin",
         "images": ["https://img.classistatic.de/a"],
         "image_urls": ["https://img.classistatic.de/a"],
         "images_thumbs": ["/api/bild/x"], "_mock": True, "_resolved_make_id": 1}
    d.update(mehr)
    return d


def test_06_private_kontaktdaten_landen_nicht_unmaskiert_in_quelle_daten(welt, monkeypatch):
    import beweis_pdf as BP
    import beweis_service as BS
    ck = f"kleinanzeigen:r23{welt.s}f"
    _vormerken(welt, ck, daten=_privat(), abgerufen_am=T0)
    q = welt.run(welt.db.inserat_beweise.find_one({"cache_key": ck}))["quelle_daten"]
    roh = json.dumps(q, ensure_ascii=False)
    for geheim in ("Mustermann", "1234567", "23456789", "example.de", "Musterweg"):
        assert geheim not in roh, geheim
    assert q["seller_name"] == q["seller_phone"] == BP.KONTAKT_ENTFERNT
    assert q["seller_zip"] == "10115" and q["seller_city"] == "Berlin", "stehen auch im PDF"
    assert BP.verkaeufer_art(q, "kleinanzeigen") == "privat"
    assert "_mock" not in q and "_resolved_make_id" not in q
    assert "images_thumbs" not in q and "image_urls" not in q, "PDF liest sie nie"
    assert q["images"] == ["https://img.classistatic.de/a"]

    # Ersatzname bestimmt die Anbieterart und bleibt.
    ersatz = BS.quelle_einfrieren("mobile", {"seller_name": "Privatverkäufer",
                                             "description": "Tel 0171 1234567"})
    assert ersatz["seller_name"] == "Privatverkäufer"
    assert BP.verkaeufer_art(ersatz, "mobile") == "privat"
    assert "1234567" not in ersatz["description"]

    # Haendler: unveraendert — das PDF druckt die Angaben vollstaendig.
    haendler = _privat(seller_type="haendler", seller_name="Autohaus Muster GmbH")
    q2 = BS.quelle_einfrieren("mobile", haendler)
    assert q2["seller_phone"] == "0171 1234567" and q2["description"] == haendler["description"]
    assert "_kontakt_maskiert" not in q2 and "_mock" not in q2

    # Betreiber hat BEWEIS_PRIVATDATEN gesetzt: dann druckt das PDF auch die
    # Privatdaten — der eingefrorene Stand deckt sich damit.
    monkeypatch.setattr(BS, "BEWEIS_PRIVATDATEN", True)
    assert BS.quelle_einfrieren("kleinanzeigen", _privat())["seller_name"] == "Max Mustermann"


def _pdf_text_direkt(daten, quelle, privatdaten=False) -> str:
    import beweis_pdf as BP
    import beweis_service as BS
    urls = BS.foto_urls(daten)
    pdf = BP.beweis_pdf(quelle=quelle, daten=daten,
                        url="https://www.kleinanzeigen.de/s-anzeige/golf/2891-216-1",
                        item_id="2891", beweis_id="abcdef1234", abgerufen_am=T0,
                        erstellt_am=T0, fotos=[None] * len(urls), foto_urls=urls,
                        privatdaten=privatdaten)
    return "".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf)).pages)


HINWEIS = "privater Anbieter werden nicht"


@pytest.mark.parametrize("fall,quelle,daten,hinweis,flag", [
    ("privat_mit_name", "kleinanzeigen", _privat(), True, False),
    ("privat_nur_beschreibung", "mobile",
     {"make_label": "BMW", "seller_name": "Privatverkäufer",
      "description": "Nur per Mail: a.b@example.de, sonst 0171 1234567"}, True, False),
    ("privatanbieter_ohne_kontakt", "kleinanzeigen",
     {"make_label": "Opel", "seller_name": "Privatanbieter", "description": "Top Zustand"},
     True, False),
    ("unbekannt_ohne_kontakt", "autoscout24",
     {"make_label": "Audi", "description": "Scheckheft 2019, 120.000 km",
      "image_urls": ["https://prod.pictures.autoscout24.net/x.jpg"]}, False, False),
    ("unbekannt_mit_telefon", "autoscout24",
     {"make_label": "Audi", "title": "A4 Avant", "description": "Anruf 0049 171 1234567"},
     True, False),
    ("haendler", "mobile", _privat(seller_type="haendler", seller_name="Autohaus X"),
     False, False),
    ("privat_mit_betreiberfreigabe", "kleinanzeigen", _privat(), False, True),
])
def test_07_pdf_aus_eingefrorenen_daten_gleicht_dem_aus_rohdaten(fall, quelle, daten, hinweis,
                                                                  flag, monkeypatch):
    """Das Maskieren beim Einfrieren darf die PDF-Logik nicht brechen: gleicher
    Text, gleicher Hinweis — auch wenn nur die Beschreibung eine Nummer hatte."""
    import beweis_service as BS
    monkeypatch.setattr(BS, "BEWEIS_PRIVATDATEN", flag)
    roh = _pdf_text_direkt(dict(daten), quelle, privatdaten=flag)
    eingefroren = BS.quelle_einfrieren(quelle, dict(daten))
    frost = _pdf_text_direkt(BS._fuer_pdf(eingefroren), quelle, privatdaten=flag)
    assert frost == roh, fall
    assert (HINWEIS in roh) is hinweis, fall
    if not flag:
        assert "1234567" not in json.dumps(eingefroren, ensure_ascii=False) or fall == "haendler"


# ------------------------------------------------------ Aufruf beim Abruf --
def test_08_erster_portalabruf_uebergibt_daten_und_abrufzeit(welt):
    from listing_identity import ensure_cache_indexes, get_or_fetch_listing
    welt.run(ensure_cache_indexes(welt.db))
    nr = random.randint(7_100_000_000, 7_199_999_999)
    url = f"https://www.kleinanzeigen.de/s-anzeige/test-auto/{nr}-216-1"
    aufrufe = []

    async def fetcher(source, item_id, u):
        aufrufe.append(item_id)
        return {"make_label": "VW", "model_label": "Polo", "list_price": 7000 + len(aufrufe),
                "description": f"Abruf {len(aufrufe)}", "seller_type": "haendler",
                "images": ["https://img.classistatic.de/a"], "_mock": True}

    welt.run(get_or_fetch_listing(welt.db, url, fetcher, ttl_hours=1))
    cache = welt.run(welt.db.listings_cache.find_one({"item_id": str(nr)}))
    ck = cache["cache_key"]
    zeile = welt.run(welt.db.inserat_beweise.find_one({"cache_key": ck}))
    assert zeile["anlass"] == "abruf" and zeile["quelle_daten"]["description"] == "Abruf 1"
    assert zeile["quelle_abgerufen_am"] == cache["fetched_at"]
    assert "_mock" not in zeile["quelle_daten"]

    # TTL abgelaufen -> erneuter Portalabruf mit neuem Stand im Cache.
    welt.run(welt.db.listings_cache.update_one(
        {"cache_key": ck}, {"$set": {"expires_at": _jetzt() - timedelta(minutes=1)}}))
    daten, aus_cache, _ = welt.run(get_or_fetch_listing(welt.db, url, fetcher, ttl_hours=1))
    assert len(aufrufe) == 2 and not aus_cache and daten["description"] == "Abruf 2"
    zeile2 = welt.run(welt.db.inserat_beweise.find_one({"cache_key": ck}))
    assert zeile2["quelle_daten"]["description"] == "Abruf 1"
    assert zeile2["quelle_abgerufen_am"] == zeile["quelle_abgerufen_am"]

    fertig = _durchlaufen(welt)
    assert fertig["status"] == "fertig", fertig.get("fehler")
    text = _pdf_text(welt, fertig)
    assert "Abruf 1" in text and "Abruf 2" not in text
