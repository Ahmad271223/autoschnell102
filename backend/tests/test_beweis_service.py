# -*- coding: utf-8 -*-
"""Beweisdokument je Inserat (ersetzt die Snapshots, 10.09.2026):
Warteschlange, Erzeugung, Aufbewahrung, Zugriff, sicherer Foto-Abruf.

Laeuft gegen die lokale MongoDB (eigene Kennungen, raeumt auf); Fotos und
Portal werden ersetzt — kein Netz."""
import asyncio
import io
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException
from PIL import Image
from pypdf import PdfReader

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
DB_NAME = os.environ.get("DB_NAME") or "autoschnell"


def _jetzt():
    return datetime.now(timezone.utc)


def _jpeg(farbe=(30, 90, 160), groesse=(900, 600), exif=False) -> bytes:
    im = Image.new("RGB", groesse, farbe)
    b = io.BytesIO()
    if exif:
        e = Image.Exif()
        e[0x010F] = "Testhersteller"
        im.save(b, "JPEG", quality=85, exif=e.tobytes())
    else:
        im.save(b, "JPEG", quality=85)
    return b.getvalue()


@pytest.fixture
def welt(monkeypatch):
    import importlib
    from motor.motor_asyncio import AsyncIOMotorClient
    mods = [importlib.import_module(n) for n in ("deps", "routes.beweise", "routes.drivers")]
    alt = [(m, getattr(m, "db", None)) for m in mods]

    class _W:
        pass

    w = _W()
    w.s = uuid.uuid4().hex[:10]
    w.dealer_id = f"d_bew_{w.s}"
    w.anderer = f"d_bew2_{w.s}"
    w.loop = asyncio.new_event_loop()
    asyncio.set_event_loop(w.loop)
    w.client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    w.db = w.client[DB_NAME]
    for m in mods:
        m.db = w.db
    w.run = lambda coro: w.loop.run_until_complete(coro)
    w.keys = []
    import beweis_service as _BS
    w.run(_BS.ensure_beweis_indexes(w.db))

    def ck(n="1", quelle="mobile"):
        return f"{quelle}:bew{w.s}{n}"

    def cache(n="1", quelle="mobile", bilder=("https://img.classistatic.de/a",
                                              "https://img.classistatic.de/b")):
        w.run(w.db.listings_cache.insert_one({
            "cache_key": ck(n, quelle), "source": quelle, "item_id": f"bew{w.s}{n}",
            "url": f"https://suchen.mobile.de/fahrzeuge/details.html?id=bew{w.s}{n}",
            "data": {"make_label": "VW", "model_label": "Golf", "mobile_ad_id": f"bew{w.s}{n}",
                     "list_price": 9990, "images": list(bilder), "description": "Test"},
            "fetched_at": _jetzt() - timedelta(minutes=1)}))
        return ck(n, quelle)

    w.ck, w.cache = ck, cache

    # Fotos ohne Netz: "a" liefert ein Bild, alles andere nichts.
    import bild_proxy

    async def _foto(url, kante=800, qualitaet=0):
        return _jpeg() if url.endswith("/a") else None

    monkeypatch.setattr(bild_proxy, "laden_fuer_pdf", _foto)
    yield w
    try:
        rx = {"$regex": f"^(mobile|kleinanzeigen|autoscout24):bew{w.s}"}
        for d in w.run(w.db.inserat_beweise.find({"cache_key": rx}, {"pdf_key": 1}).to_list(100)):
            if d.get("pdf_key"):
                w.keys.append(d["pdf_key"])
        for d in w.run(w.db.inserat_beweise.find({"cache_key": rx}, {"alle_keys": 1}).to_list(100)):
            w.keys.extend(d.get("alle_keys") or [])
        w.run(w.db.inserat_beweise.delete_many({"cache_key": rx}))
        w.run(w.db.betriebsalarme.delete_many({"ref": rx}))
        w.run(w.db.listings_cache.delete_many({"cache_key": rx}))
        for c in ("vehicles", "vehicle_comparisons", "generated_pdfs", "appointments",
                  "dealer_drivers", "storage_delete_retry"):
            w.run(w.db[c].delete_many({"dealer_id": {"$in": [w.dealer_id, w.anderer]}}))
        w.run(w.db.storage_delete_retry.delete_many({"ref.collection": "inserat_beweise",
                                                      "key": {"$in": w.keys}}))
        from storage_service import delete_async
        for k in set(w.keys):
            try:
                w.run(delete_async(k))
            except Exception:
                pass
    finally:
        for m, d in alt:
            if d is not None:
                m.db = d
        w.client.close()
        w.loop.close()


def _vormerken(w, ck, anlass="abruf"):
    import beweis_service as BS
    quelle, item = ck.split(":", 1)
    return w.run(BS.beweis_vormerken(w.db, cache_key=ck, quelle=quelle, item_id=item,
                                     url=f"https://suchen.mobile.de/fahrzeuge/details.html?id={item}#x",
                                     anlass=anlass))


def _durchlaufen(w):
    """Eine Zeile beanspruchen und bearbeiten (wie die Worker-Schleife)."""
    import beweis_service as BS
    doc = w.run(BS._beanspruchen(w.db))
    if doc:
        w.run(BS._bearbeiten(w.db, doc))
    return doc


# ------------------------------------------------------------ Warteschlange
def test_01_je_inserat_genau_ein_dokument_auch_parallel(welt):
    import beweis_service as BS
    ck = welt.ck()

    async def _viele():
        return await asyncio.gather(*(BS.beweis_vormerken(
            welt.db, cache_key=ck, quelle="mobile", item_id="x", url="https://x", anlass="abruf")
            for _ in range(25)))

    ergebnisse = welt.run(_viele())
    assert len({e["id"] for e in ergebnisse}) == 1
    assert welt.run(welt.db.inserat_beweise.count_documents({"cache_key": ck})) == 1
    # Spaeterer Gebrauch (andere Firma, Vergleich) aendert nichts.
    zweit = _vormerken(welt, ck, anlass="vergleich")
    assert zweit["id"] == ergebnisse[0]["id"]
    zeile = welt.run(welt.db.inserat_beweise.find_one({"cache_key": ck}))
    assert zeile["anlass"] == "abruf" and "#" not in zeile["url"]
    assert "dealer_id" not in zeile and "user_id" not in zeile, "Dokument gehoert zum Inserat"


def test_01b_index_wird_auch_ohne_serverstart_sichergestellt(welt):
    """Fehlt der Unique-Index (Serverstart mit altem Code, frische DB),
    legt beweis_vormerken ihn selbst an — sonst doppelte Dokumente."""
    import beweis_service as BS
    namen = welt.run(welt.db.inserat_beweise.index_information())
    assert namen.get("beweis_je_inserat", {}).get("unique") is True
    BS._index_sicher.clear()
    _vormerken(welt, welt.ck("idx"))
    assert BS._index_sicher, "Pruefung einmal je Prozess und Datenbank"


def test_02_nur_ein_worker_gewinnt(welt):
    import beweis_service as BS
    welt.cache()
    _vormerken(welt, welt.ck())

    async def _alle():
        return await asyncio.gather(*(BS._beanspruchen(welt.db) for _ in range(6)))

    gewonnen = [d for d in welt.run(_alle()) if d and d["cache_key"] == welt.ck()]
    assert len(gewonnen) == 1
    assert gewonnen[0]["status"] == "in_arbeit" and gewonnen[0]["versuche"] == 1


def test_03_erzeugen_mit_fotos_url_und_pruefsumme(welt):
    import hashlib
    from storage_service import load_async
    welt.cache()
    b = _vormerken(welt, welt.ck())
    assert b["status"] == "offen"
    _durchlaufen(welt)
    z = welt.run(welt.db.inserat_beweise.find_one({"id": b["id"]}, {"_id": 0}))
    welt.keys.append(z["pdf_key"])
    assert z["status"] == "fertig", z.get("fehler")
    assert z["pdf_key"].startswith(f"beweise/mobile/{b['id']}-") and z["pdf_key"].endswith(".pdf")
    assert z["alle_keys"] == [z["pdf_key"]]
    assert z["fotos_eingebettet"] == 1 and z["fotos_gesamt"] == 2
    pdf = welt.run(load_async(z["pdf_key"]))
    assert hashlib.sha256(pdf).hexdigest() == z["pdf_sha256"] and len(pdf) == z["pdf_bytes"]
    text = "".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf)).pages)
    assert f"bew{welt.s}1" in text and "mobile.de" in text
    assert "1 von 2 Inseratsfotos eingebettet" in text


def test_04_fehler_mit_pause_und_nach_drei_versuchen_endgueltig(welt):
    import beweis_service as BS
    ck = welt.ck("f")                      # absichtlich KEIN Cache-Eintrag
    _vormerken(welt, ck)
    doc = welt.run(BS._beanspruchen(welt.db))
    assert doc["cache_key"] == ck
    welt.run(BS._bearbeiten(welt.db, doc))
    z = welt.run(welt.db.inserat_beweise.find_one({"cache_key": ck}))
    assert z["status"] == "offen" and "Inseratsdaten fehlen" in z["fehler"]
    assert z["naechster_versuch_ab"] is not None
    nochmal = welt.run(BS._beanspruchen(welt.db))
    assert not nochmal or nochmal["cache_key"] != ck, "Pause vor dem naechsten Versuch"
    doc["versuche"] = BS.MAX_VERSUCHE
    welt.run(welt.db.inserat_beweise.update_one(
        {"cache_key": ck}, {"$set": {"status": "in_arbeit", "bearbeiter": doc["bearbeiter"]}}))
    welt.run(BS._bearbeiten(welt.db, doc))
    assert welt.run(welt.db.inserat_beweise.find_one({"cache_key": ck}))["status"] == "fehlgeschlagen"


def test_05_verwaiste_bearbeitung_wird_freigegeben(welt):
    import beweis_service as BS
    alt = _jetzt() - timedelta(minutes=10)
    for n, versuche in (("w1", 1), ("w2", BS.MAX_VERSUCHE)):
        welt.run(welt.db.inserat_beweise.insert_one({
            "id": str(uuid.uuid4()), "cache_key": welt.ck(n), "status": "in_arbeit",
            "versuche": versuche, "bearbeitung_bis": alt, "erstellt_am": alt}))
    welt.run(BS._aufraeumen(welt.db))
    assert welt.run(welt.db.inserat_beweise.find_one({"cache_key": welt.ck("w1")}))["status"] == "offen"
    assert welt.run(welt.db.inserat_beweise.find_one({"cache_key": welt.ck("w2")}))["status"] == "fehlgeschlagen"


# ------------------------------------------------------------ Aufbewahrung
def test_06_verfall_nur_ohne_fahrzeug_und_grabstein_verhindert_neues_erstes(welt):
    import beweis_service as BS
    from storage_service import StorageError, load_async, save_async
    alt = _jetzt() - timedelta(days=BS.BEWEIS_AUFBEWAHRUNG_TAGE + 5)
    for n in ("frei", "fz"):
        key = f"beweise/mobile/test{welt.s}{n}.pdf"
        welt.run(save_async(key, b"%PDF-1.4 test"))
        welt.keys.append(key)
        welt.run(welt.db.inserat_beweise.insert_one({
            "id": f"id{welt.s}{n}", "cache_key": welt.ck(n), "status": "fertig", "url": "https://x",
            "pdf_key": key, "erstellt_am": alt}))
    welt.run(welt.db.vehicles.insert_one({"id": f"v{welt.s}", "dealer_id": welt.dealer_id,
                                          "lifecycle": "bestand",
                                          "inserat_schluessel": welt.ck("fz")}))
    assert welt.run(BS.beweise_verfallen(welt.db, altbestand_filter={"dealer_id": welt.dealer_id})) >= 1
    frei = welt.run(welt.db.inserat_beweise.find_one({"cache_key": welt.ck("frei")}))
    fz = welt.run(welt.db.inserat_beweise.find_one({"cache_key": welt.ck("fz")}))
    assert frei["status"] == "geloescht" and frei["pdf_key"] is None and "url" not in frei
    assert fz["status"] == "fertig", "Fahrzeug einer Firma haelt das Dokument"
    with pytest.raises(StorageError):
        welt.run(load_async(f"beweise/mobile/test{welt.s}frei.pdf"))
    # Derselbe Link spaeter erneut: kein zweites "erstes" Dokument.
    assert _vormerken(welt, welt.ck("frei"))["status"] == "geloescht"


# ------------------------------------------------------------ Fahrzeuge
def test_07_inserat_schluessel_fuer_fahrzeuge():
    import beweis_service as BS
    assert BS.inserat_schluessel({"inserat_schluessel": "mobile:1"}) == "mobile:1"
    as_url = "https://www.autoscout24.de/angebote/vw-golf-benzin-grau-0f9e8d7c-6b5a-4c3d-9e8f-123456789abc"
    k = BS.inserat_schluessel({"quelle": "autoscout24", "mobile_ad_id": "uniq-anders",
                               "data": {"detail_url": as_url}})
    assert k and k.startswith("autoscout24:") and "uniq-anders" not in k
    assert BS.inserat_schluessel({"quelle": "mobile", "mobile_ad_id": "77"}) == "mobile:77"
    assert BS.inserat_schluessel({"quelle": "autoscout24", "mobile_ad_id": "uniq"}) is None
    assert BS.inserat_schluessel(None) is None


def test_08_oeffentliche_felder_ohne_speicherpfad_und_mit_zeitzone():
    import beweis_service as BS
    aus = BS.oeffentlich({"id": "x", "pdf_key": "beweise/m/x.pdf", "bearbeiter": "w",
                          "cache_key": "mobile:1", "fertig_am": datetime(2026, 9, 10, 13, 0)})
    assert "pdf_key" not in aus and "bearbeiter" not in aus and "cache_key" not in aus
    assert aus["fertig_am"].endswith("+00:00")


# ------------------------------------------------------------ Zugriff
def _nutzer(w, rolle="chef", dealer=None, uid=None):
    return {"id": uid or f"u_{uuid.uuid4().hex[:6]}", "role": rolle,
            "dealer_id": dealer or w.dealer_id}


def test_09_zugriff_ueber_vergleich_oder_fahrzeug_im_bereich(welt):
    import routes.beweise as R
    welt.cache()
    b = _vormerken(welt, welt.ck())
    _durchlaufen(welt)
    doc = welt.run(welt.db.inserat_beweise.find_one({"id": b["id"]}, {"_id": 0}))
    chef, fremd = _nutzer(welt), _nutzer(welt, dealer=welt.anderer)
    sucher_a, sucher_b = _nutzer(welt, "sucher"), _nutzer(welt, "sucher")
    assert not welt.run(R._darf_sehen(chef, doc))
    welt.run(welt.db.vehicle_comparisons.insert_one({"cache_key": welt.ck(), "dealer_id": welt.dealer_id,
                                                     "user_id": sucher_a["id"]}))
    assert welt.run(R._darf_sehen(chef, doc)) and welt.run(R._darf_sehen(sucher_a, doc))
    assert not welt.run(R._darf_sehen(sucher_b, doc)), "fremder Vergleich gibt Sucher keinen Zugriff"
    assert not welt.run(R._darf_sehen(fremd, doc))
    welt.run(welt.db.vehicles.insert_one({"id": f"v{welt.s}", "dealer_id": welt.dealer_id,
                                          "owner_user_id": sucher_b["id"],
                                          "inserat_schluessel": welt.ck()}))
    assert welt.run(R._darf_sehen(sucher_b, doc))
    assert welt.run(R._darf_sehen({"id": "a", "role": "admin", "is_super_admin": True}, doc))
    assert not welt.run(R._darf_sehen({"id": "a", "role": "admin"}, doc)), "nur der Super-Admin"
    antwort = welt.run(R.beweis_pdf(b["id"], user=chef))
    welt.keys.append(doc["pdf_key"])
    assert antwort.body.startswith(b"%PDF") and antwort.headers["X-Beweis-SHA256"] == doc["pdf_sha256"]
    with pytest.raises(HTTPException) as e:
        welt.run(R.beweis_pdf(b["id"], user=fremd))
    assert e.value.status_code == 404


def test_10_fahrzeug_altbestand_wird_zugeordnet(welt):
    import routes.beweise as R
    welt.cache("alt")
    b = _vormerken(welt, welt.ck("alt"))
    chef = _nutzer(welt)
    welt.run(welt.db.vehicles.insert_one({
        "id": f"valt{welt.s}", "dealer_id": welt.dealer_id, "quelle": "mobile",
        "mobile_ad_id": f"bew{welt.s}alt",
        "data": {"detail_url": f"https://suchen.mobile.de/fahrzeuge/details.html?id=bew{welt.s}alt"}}))
    aus = welt.run(R.beweis_zum_fahrzeug(f"valt{welt.s}", user=chef))
    assert aus["beweis"]["id"] == b["id"] and aus["beweis"]["status"] == "offen"
    v = welt.run(welt.db.vehicles.find_one({"id": f"valt{welt.s}"}))
    assert v["inserat_schluessel"] == welt.ck("alt")
    with pytest.raises(HTTPException):
        welt.run(R.beweis_zum_fahrzeug(f"valt{welt.s}", user=_nutzer(welt, dealer=welt.anderer)))
    with pytest.raises(HTTPException) as e:
        welt.run(R.beweis_pdf(b["id"], user=chef))
    assert e.value.status_code == 409, "noch nicht fertig"


def test_11_fahrer_nur_mit_eigenem_termin(welt):
    import routes.beweise as R
    welt.cache()
    b = _vormerken(welt, welt.ck())
    _durchlaufen(welt)
    fahrer = {"id": f"drv_{welt.s}"}
    welt.run(welt.db.dealer_drivers.insert_one({"driver_account_id": fahrer["id"],
                                                "dealer_id": welt.dealer_id}))
    welt.run(welt.db.vehicles.insert_one({"id": f"v{welt.s}", "dealer_id": welt.dealer_id,
                                          "inserat_schluessel": welt.ck()}))
    with pytest.raises(HTTPException):
        welt.run(R.driver_beweis_pdf(b["id"], driver=fahrer))
    welt.run(welt.db.appointments.insert_one({"id": f"t{welt.s}", "dealer_id": welt.dealer_id,
                                              "vehicle_id": f"v{welt.s}", "driver_id": fahrer["id"]}))
    assert welt.run(R.driver_beweis_pdf(b["id"], driver=fahrer)).body.startswith(b"%PDF")
    z = welt.run(welt.db.inserat_beweise.find_one({"id": b["id"]}))
    welt.keys.append(z["pdf_key"])


# ------------------------------------------------------------ Foto-Abruf
class _Antwort:
    def __init__(self, status, headers=None, body=b""):
        self.status_code = status
        self.headers = headers or {}
        self._body = body

    async def aiter_bytes(self):
        yield self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _fake_httpx(monkeypatch, antworten, aufrufe):
    import httpx

    class _Client:
        def __init__(self, *a, **k):
            assert k.get("follow_redirects") is False, "Weiterleitungen nur von Hand"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def stream(self, methode, url):
            aufrufe.append(url)
            return antworten.pop(0)

    monkeypatch.setattr(httpx, "AsyncClient", _Client)


def test_12_weiterleitung_auf_fremden_host_wird_verworfen(monkeypatch):
    import bild_proxy as bp
    aufrufe = []
    _fake_httpx(monkeypatch, [_Antwort(302, {"location": "http://169.254.169.254/latest"})], aufrufe)
    assert asyncio.run(bp._holen("https://img.classistatic.de/a")) is None
    assert aufrufe == ["https://img.classistatic.de/a"]
    aufrufe = []
    _fake_httpx(monkeypatch, [_Antwort(302, {"location": "/b"}),
                              _Antwort(200, {"content-type": "image/jpeg"}, _jpeg())], aufrufe)
    assert asyncio.run(bp._holen("https://img.classistatic.de/a"))
    assert aufrufe[-1] == "https://img.classistatic.de/b"
    assert asyncio.run(bp._holen("https://evil.example/a.jpg")) is None


def test_13_pdf_foto_verkleinert_ohne_metadaten_und_ohne_riesenbild(monkeypatch):
    import bild_proxy as bp
    aufrufe = []
    _fake_httpx(monkeypatch, [_Antwort(200, {"content-type": "image/jpeg"},
                                       _jpeg(groesse=(3000, 2000), exif=True))], aufrufe)
    klein = asyncio.run(bp.laden_fuer_pdf("https://img.classistatic.de/a", 800))
    im = Image.open(io.BytesIO(klein))
    assert max(im.size) == 800 and not im.getexif()
    with pytest.raises(ValueError):
        bp._fuer_pdf(_jpeg(groesse=(8000, 6000)), 800)


# ------------------------------------------------------------ Gegenpruefung 10.09.2026
def test_14_kanonische_url_ohne_parameter_und_ohne_einschleusung():
    import beweis_service as BS
    assert BS.kanonische_url("mobile", "412345678",
                             "https://m.mobile.de/x.html?id=412345678&utm_source=news#top") == \
        "https://suchen.mobile.de/fahrzeuge/details.html?id=412345678"
    ka = BS.kanonische_url("kleinanzeigen", "2891",
                           "https://www.kleinanzeigen.de/s-anzeige/golf/2891-216-1?ref=suche&x=1#bild")
    assert ka == "https://www.kleinanzeigen.de/s-anzeige/golf/2891-216-1"
    boese = BS.kanonische_url("autoscout24", "a1", "https://www.autoscout24.de/angebote/a1'x\" href='https://evil/")
    assert "'" not in boese and '"' not in boese and "evil" not in boese.split("/angebote/")[0]
    assert BS.kanonische_url("kleinanzeigen", "1", "javascript:alert(1)") == ""


def test_15_fehlgeschlagen_wird_beim_naechsten_gebrauch_wiederbelebt(welt):
    alt = _jetzt() - timedelta(minutes=30)
    for n, status, am in (("alt", "fehlgeschlagen", alt), ("frisch", "fehlgeschlagen", _jetzt()),
                          ("grab", "geloescht", alt)):
        welt.run(welt.db.inserat_beweise.insert_one({
            "id": f"id{welt.s}{n}", "cache_key": welt.ck(n), "status": status, "versuche": 3,
            "fehler": "R2 weg", "fehlgeschlagen_am": am, "erstellt_am": alt}))
    assert _vormerken(welt, welt.ck("alt"))["status"] == "offen"
    z = welt.run(welt.db.inserat_beweise.find_one({"cache_key": welt.ck("alt")}))
    assert z["versuche"] == 0 and z["fehler"] is None
    assert _vormerken(welt, welt.ck("frisch"))["status"] == "fehlgeschlagen", "Pause vor Wiederbelebung"
    assert _vormerken(welt, welt.ck("grab"))["status"] == "geloescht", "Grabstein bleibt"


def _alt_fertig(welt, n, key=None):
    import beweis_service as BS
    alt = _jetzt() - timedelta(days=BS.BEWEIS_AUFBEWAHRUNG_TAGE + 5)
    welt.run(welt.db.inserat_beweise.insert_one({
        "id": f"id{welt.s}{n}", "cache_key": welt.ck(n), "quelle": "mobile", "status": "fertig",
        "pdf_key": key, "alle_keys": [key] if key else [], "erstellt_am": alt}))


def test_16_verfall_blaettert_weiter_und_gehaltene_warten_einen_tag(welt):
    import beweis_service as BS
    for n in ("h1", "h2", "h3"):
        _alt_fertig(welt, n)
        welt.run(welt.db.vehicles.insert_one({"id": f"v{welt.s}{n}", "dealer_id": welt.dealer_id,
                                              "lifecycle": "bestand", "inserat_schluessel": welt.ck(n)}))
    for n in ("f1", "f2"):
        _alt_fertig(welt, n)
    welt.run(BS.beweise_verfallen(welt.db, seite=2, altbestand_filter={"dealer_id": welt.dealer_id}))
    st = {n: welt.run(welt.db.inserat_beweise.find_one({"cache_key": welt.ck(n)})) for n in
          ("h1", "h2", "h3", "f1", "f2")}
    assert st["f1"]["status"] == st["f2"]["status"] == "geloescht", "freie verfallen trotz gehaltener davor"
    assert all(st[n]["status"] == "fertig" and st[n]["verfall_pruefen_ab"] for n in ("h1", "h2", "h3"))


def test_17_nur_echter_vorgang_haelt_das_dokument(welt):
    import beweis_service as BS
    for n in ("vgl", "vertrag"):
        _alt_fertig(welt, n)
        welt.run(welt.db.vehicles.insert_one({"id": f"v{welt.s}{n}", "dealer_id": welt.dealer_id,
                                              "lifecycle": "verglichen", "inserat_schluessel": welt.ck(n)}))
    welt.run(welt.db.generated_pdfs.insert_one({"id": f"g{welt.s}", "dealer_id": welt.dealer_id,
                                                "vehicle_id": f"v{welt.s}vertrag"}))
    welt.run(BS.beweise_verfallen(welt.db, altbestand_filter={"dealer_id": welt.dealer_id}))
    assert welt.run(welt.db.inserat_beweise.find_one({"cache_key": welt.ck("vgl")}))["status"] == "geloescht"
    assert welt.run(welt.db.inserat_beweise.find_one({"cache_key": welt.ck("vertrag")}))["status"] == "fertig"


def test_18_altbestand_ohne_schluessel_wird_zugeordnet_und_haelt(welt):
    import beweis_service as BS
    _alt_fertig(welt, "alt")
    item = welt.ck("alt").split(":", 1)[1]
    welt.run(welt.db.vehicles.insert_one({
        "id": f"v{welt.s}alt", "dealer_id": welt.dealer_id, "lifecycle": "bestand", "quelle": "mobile",
        "mobile_ad_id": item,
        "data": {"detail_url": f"https://suchen.mobile.de/fahrzeuge/details.html?id={item}"}}))
    welt.run(BS.beweise_verfallen(welt.db, altbestand_filter={"dealer_id": welt.dealer_id}))
    v = welt.run(welt.db.vehicles.find_one({"id": f"v{welt.s}alt"}))
    assert v["inserat_schluessel"] == welt.ck("alt")
    assert welt.run(welt.db.inserat_beweise.find_one({"cache_key": welt.ck("alt")}))["status"] == "fertig"


def test_19_alle_versuchs_schluessel_werden_beim_verfall_geloescht(welt):
    import beweis_service as BS
    from storage_service import StorageError, load_async, save_async
    k1, k2 = f"beweise/mobile/id{welt.s}v-a.pdf", f"beweise/mobile/id{welt.s}v-b.pdf"
    for k in (k1, k2):
        welt.run(save_async(k, b"%PDF-1.4 x"))
        welt.keys.append(k)
    _alt_fertig(welt, "v", key=k2)
    welt.run(welt.db.inserat_beweise.update_one({"cache_key": welt.ck("v")},
                                                {"$set": {"alle_keys": [k1, k2]}}))
    welt.run(BS.beweise_verfallen(welt.db, altbestand_filter={"dealer_id": welt.dealer_id}))
    for k in (k1, k2):
        with pytest.raises(StorageError):
            welt.run(load_async(k))


def test_20_zeitlimit_und_alarm_beim_endgueltigen_scheitern(welt, monkeypatch):
    import beweis_service as BS

    async def _haengt(db, doc):
        await asyncio.sleep(5)
    monkeypatch.setattr(BS, "beweis_erzeugen", _haengt)
    monkeypatch.setattr(BS, "ERZEUGUNG_MAX_SEKUNDEN", 0.2)
    _vormerken(welt, welt.ck("z"))
    doc = welt.run(BS._beanspruchen(welt.db))
    assert doc["cache_key"] == welt.ck("z")
    welt.run(BS._bearbeiten(welt.db, doc))
    z = welt.run(welt.db.inserat_beweise.find_one({"cache_key": welt.ck("z")}))
    assert z["status"] == "offen" and z["fehler"] == "Zeitlimit ueberschritten"
    welt.run(welt.db.inserat_beweise.update_one({"cache_key": welt.ck("z")},
                                                {"$set": {"status": "in_arbeit"}}))
    doc["versuche"] = BS.MAX_VERSUCHE
    welt.run(BS._bearbeiten(welt.db, doc))
    z = welt.run(welt.db.inserat_beweise.find_one({"cache_key": welt.ck("z")}))
    assert z["status"] == "fehlgeschlagen" and z["fehlgeschlagen_am"]
    assert welt.run(welt.db.betriebsalarme.count_documents(
        {"typ": "beweis_fehlgeschlagen", "ref": welt.ck("z"), "offen": True})) == 1


def test_21_herzschlag_verlaengert_die_lease_nur_fuer_den_eigenen_worker(welt, monkeypatch):
    import beweis_service as BS
    monkeypatch.setattr(BS, "HERZSCHLAG_SEKUNDEN", 0.05)
    bald = _jetzt() + timedelta(seconds=2)
    welt.run(welt.db.inserat_beweise.insert_one({
        "id": f"id{welt.s}hs", "cache_key": welt.ck("hs"), "status": "in_arbeit",
        "bearbeiter": "ich", "bearbeitung_bis": bald, "erstellt_am": _jetzt()}))

    async def _kurz(bearbeiter):
        t = asyncio.create_task(BS._herzschlag(welt.db, {"id": f"id{welt.s}hs", "bearbeiter": bearbeiter}))
        await asyncio.sleep(0.2)
        erledigt = t.done()
        t.cancel()
        return erledigt

    assert welt.run(_kurz("fremd")) is True, "fremder Worker: Herzschlag endet sofort"
    welt.run(_kurz("ich"))
    z = welt.run(welt.db.inserat_beweise.find_one({"cache_key": welt.ck("hs")}))
    bis = z["bearbeitung_bis"].replace(tzinfo=timezone.utc)
    assert bis > _jetzt() + timedelta(seconds=BS.BEARBEITUNG_SEKUNDEN - 30)
