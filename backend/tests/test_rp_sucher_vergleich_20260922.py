# -*- coding: utf-8 -*-
"""Rollenprüfung 22.09.2026 — Team Sucher/Vergleich.

Reine Einheitentests plus einige In-Prozess-Tests gegen eine Wegwerf-
Datenbank (echtes Mongo, wird am Ende gedroppt). Keine HTTP-Aufrufe.

  RP-202/353  Offline-Inserat bei mobile.de/AutoScout -> ListingGone
  RP-549      leer gesetzter Actor-Name -> Standard-Actor
  RP-553      Apify 402 "memory" -> Tempolimit statt "Guthaben aufgebraucht"
  RP-444      AutoScout-Haendler: Firma statt Verkaufsberater
  RP-435/447  AutoScout-Modelle: kein M3 fuer M340i, kein EQE 300 fuer EQE
  RP-419/421  mobile.de: T6.1 -> T6, Ceed SW; Hinweise im Vergleich
  RP-422      Nutzfahrzeug-Hinweis
  RP-436-441  Kleinanzeigen-HTML: km, Preis, PLZ/Ort, Bundesland
  RP-439/440  Kleinanzeigen-API: Marke aus dem Titel, kein Pseudonym im Vertrag
  RP-409      geteilter Text mit Link
  RP-205/356  AutoScout-Auslandsseiten klar abgelehnt
  RP-209/360  /listings/resolve: ungueltige Adresse -> 400
  RP-026      /listings/resolve beachtet die Erweiterungspflicht
  RP-442      abgelaufene Quarantaene wird ersetzt
  RP-156      zweites dealer-Konto gilt im Worker als Sucher
  RP-204/355  Job ohne Wartende ruft nicht ohne Konto ab
  RP-048/538  Chef-Vergleich: Hinweis + Trimm-Schutz, kein Mitbearbeiter
  RP-210      geloeschtes Fahrzeug wird gemeldet
"""
import asyncio
import os
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import anbieter_fehler as AF  # noqa: E402
import autoscout_service as AS  # noqa: E402
import deps  # noqa: E402
import fahrzeugpool as FP  # noqa: E402
import kleinanzeigen_api as KAPI  # noqa: E402
import kleinanzeigen_service as KS  # noqa: E402
import link_jobs as LJ  # noqa: E402
import listing_identity as LI  # noqa: E402
import mobile_service as MS  # noqa: E402
import provider_fetch as PF  # noqa: E402
import routes.listings as L  # noqa: E402

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"


def _jetzt(delta_s: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=delta_s)).isoformat()


@pytest.fixture
def wegwerf(monkeypatch):
    from motor.motor_asyncio import AsyncIOMotorClient
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    name = f"autoschnell_rp_sv_{uuid.uuid4().hex[:10]}"
    db = client[name]
    for mod in (deps, L):
        monkeypatch.setattr(mod, "db", db)
    try:
        yield SimpleNamespace(db=db, run=loop.run_until_complete)
    finally:
        try:
            loop.run_until_complete(client.drop_database(name))
        finally:
            client.close()
            loop.close()


# ---------------------------------------------------------------- RP-202/353
class _Antwort:
    def __init__(self, status, daten=None, text=""):
        self.status_code, self._daten, self.text = status, daten, text

    def json(self):
        return self._daten


class _Klient:
    antwort = None
    anfragen: list = []

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, **kw):
        _Klient.anfragen.append((url, kw))
        return _Klient.antwort


@pytest.fixture
def apify(monkeypatch):
    monkeypatch.setattr(MS, "APIFY_TOKEN", "t", raising=False)
    monkeypatch.setattr(MS, "MOBILE_USER", "", raising=False)
    monkeypatch.setattr(MS, "MOBILE_PASS", "", raising=False)
    monkeypatch.setattr(MS, "MOBILE_SANDBOX_MODE", False, raising=False)
    monkeypatch.setattr(MS.httpx, "AsyncClient", _Klient)
    monkeypatch.setattr(AS, "APIFY_TOKEN", "t", raising=False)
    monkeypatch.setattr(AS._httpx, "AsyncClient", _Klient)
    _Klient.antwort, _Klient.anfragen = None, []
    yield


def test_rp202_mobile_offline_ist_listing_gone_bis_nach_oben(apify):
    """Leere Apify-Antwort: ListingGone kommt durch get_vehicle durch (nicht
    MobileUnavailable), ist eine RuntimeError-Unterklasse fuer die Routen,
    und der Link-Job zeigt den Sachtext statt "Technischer Fehler"."""
    _Klient.antwort = _Antwort(201, daten=[])

    class _Db:
        class vehicle_cache:
            @staticmethod
            async def find_one(*a, **k):
                return None

    with pytest.raises(AF.ListingGone) as weg:
        asyncio.run(MS.get_vehicle(_Db(), "412345678",
                                   url="https://suchen.mobile.de/fahrzeuge/details.html?id=412345678"))
    assert isinstance(weg.value, RuntimeError)
    assert KS.ListingGone is AF.ListingGone, "alle Importe muessen dieselbe Klasse sehen"
    assert LJ.fehlertext(weg.value) == MS.INSERAT_WEG_MOBILE
    assert LJ.fehlertext(weg.value) != LJ.FEHLER_TECHNISCH


def test_rp202_zaehlt_fuer_das_tageslimit(wegwerf, monkeypatch):
    """provider_fetch bucht nur technische Fehler zurueck — ein Offline-
    Inserat (bezahlter Apify-Lauf) zaehlt jetzt auch bei mobile.de."""
    db, run = wegwerf.db, wegwerf.run

    async def weg(*a, **k):
        raise AF.ListingGone(MS.INSERAT_WEG_MOBILE)
    monkeypatch.setattr(PF, "_abrufen", weg)
    monkeypatch.setattr(PF, "MOCK_PROVIDER_FETCH", False)
    with pytest.raises(AF.ListingGone):
        run(PF.fetch_listing(db, "mobile", "1", "https://suchen.mobile.de/x", dealer_id="d1", user_id="u1"))
    tag = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    assert run(db.provider_budget.find_one({"_id": f"{tag}:konto:u1"}))["n"] == 1


# ---------------------------------------------------------------- RP-549
def test_rp549_leerer_actor_name_nimmt_den_standard():
    code = ("import os, sys; sys.path.insert(0, r'%s');"
            "os.environ['APIFY_MOBILE_ACTOR']=''; os.environ['APIFY_AUTOSCOUT_ACTOR']='  ';"
            "import mobile_service as m, autoscout_service as a;"
            "print(m.APIFY_MOBILE_ACTOR); print(a.APIFY_AUTOSCOUT_ACTOR)") % BACKEND
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         cwd=str(BACKEND), env=env, timeout=120)
    zeilen = [z for z in out.stdout.splitlines() if "~" in z]
    assert zeilen == ["memo23~mobile-de-scraper", "ivanvs~autoscout-scraper"], (out.stdout, out.stderr[-500:])


# ---------------------------------------------------------------- RP-553
def test_rp553_speichergrenze_ist_kein_leeres_guthaben(monkeypatch):
    f = AF.aus_http_antwort(402, '{"error":{"type":"actor-memory-limit-exceeded","message":'
                                 '"By launching this job you will exceed the memory limit"}}', "mobile.de")
    assert f.art == AF.ART_LIMIT and not f.betreiber_relevant
    assert LJ._ist_tempolimit(f), "Link-Job wartet kurz statt sofort erneut"
    g = AF.aus_http_antwort(402, "Monthly usage hard limit exceeded", "mobile.de")
    assert g.art == AF.ART_GUTHABEN
    # Speicher/Build nur, wenn gesetzt
    monkeypatch.delenv("APIFY_MEMORY_MB", raising=False)
    monkeypatch.delenv("APIFY_MOBILE_BUILD", raising=False)
    assert MS.apify_lauf_parameter("APIFY_MOBILE_BUILD") == {"format": "json", "clean": "1"}
    monkeypatch.setenv("APIFY_MEMORY_MB", "1024")
    monkeypatch.setenv("APIFY_MOBILE_BUILD", "0.1.7")
    assert MS.apify_lauf_parameter("APIFY_MOBILE_BUILD") == {
        "format": "json", "clean": "1", "memory": "1024", "build": "0.1.7"}
    monkeypatch.setenv("APIFY_MEMORY_MB", "viel")
    assert "memory" not in MS.apify_lauf_parameter("APIFY_MOBILE_BUILD")


# ---------------------------------------------------------------- RP-444
def test_rp444_autoscout_haendler_firma_statt_berater():
    import json
    item = json.loads((BACKEND / "tests" / "fixtures" / "apify_autoscout_item.json")
                      .read_text(encoding="utf-8"))[0]
    item["seller"] = "Händler"
    item["contactName"] = "Herr Meier"
    item["dealer"] = {"companyName": "Autohaus Muster GmbH", "address": {}}
    v = AS.parse_autoscout_item(item, "x")
    assert v["seller_name"] == "Autohaus Muster GmbH"
    assert v["seller_ansprechpartner"] == "Herr Meier"
    assert v["seller_type"] == "haendler"
    # ohne Firmenangabe bleibt der Kontaktname der Rueckfall
    item["dealer"] = {"dealerRatings": "", "address": {}}
    v = AS.parse_autoscout_item(item, "x")
    assert v["seller_name"] == "Herr Meier" and v["seller_ansprechpartner"] is None
    # Privat: keine Firma, kein Ansprechpartner
    item["seller"], item["contactName"] = "Privat", ""
    v = AS.parse_autoscout_item(item, "x")
    assert v["seller_name"] is None and v["seller_type"] == "privat"


# ---------------------------------------------------------------- RP-435/447
@pytest.mark.parametrize("marke,modell,erwartet", [
    ("BMW", "M340i", None), ("BMW", "M135i", None), ("BMW", "M240i", None),
    ("BMW", "M440i", None), ("BMW", "M550i", "M550"), ("BMW", "320d Touring", "320"),
    ("BMW", "M3", "M3"),
    ("Mercedes-Benz", "EQE", None), ("Mercedes-Benz", "EQB", None), ("Mercedes-Benz", "EQV", None),
    ("Mercedes-Benz", "CLA Shooting Brake", None), ("Mercedes-Benz", "E 220 d", "E 220"),
    ("Mercedes-Benz", "C 220", "C 220"), ("Mercedes-Benz", "S 55", "S 55 AMG"),
    ("Kia", "Ceed", "Ceed / cee'd"), ("Kia", "Ceed SW", "Ceed SW / cee'd SW"),
    ("Volkswagen", "T6.1 Multivan", "T6.1 Multivan"), ("Volkswagen", "Golf", "Golf"),
])
def test_rp435_447_autoscout_modell(marke, modell, erwartet):
    m = AS._find_model(AS._find_make(marke), modell)
    assert (m and m["modelName"]) == erwartet, (marke, modell, m)


# ---------------------------------------------------------------- RP-419/421
def _mobile_modell(marke, modell):
    import json
    _, eintrag = MS._resolve_make({"make_label": marke})
    mid = MS._resolve_model(eintrag, {"model_label": modell})
    daten = json.loads((BACKEND / "mobile_makes_models.json").read_text(encoding="utf-8"))
    namen = {str(x["id"]): x["name"] for m in daten["marken"] if m["name"] == eintrag["raw_name"]
             for x in m["modelle"]}
    return namen.get(mid)


@pytest.mark.parametrize("marke,modell,erwartet", [
    ("VW", "T6.1 Transporter", "T6 Transporter"),
    ("VW", "T6.1 Multivan", "T6 Multivan"),
    ("VW", "T6.1 California", "T6 California"),
    ("VW", "T5.1 Transporter", "T5 Transporter"),
    ("VW", "T6 Transporter", "T6 Transporter"),
    ("VW", "Golf", "Golf"),
    ("Kia", "Ceed SW", "cee'd Sportswagon"),
    ("Kia", "Ceed", "cee'd / Ceed"),
])
def test_rp421_419_mobile_modell(marke, modell, erwartet):
    assert _mobile_modell(marke, modell) == erwartet


def test_rp419_439_422_hinweise_im_vergleich():
    h, mobile, autoscout = L._katalog_pruefen({"make_label": "VW", "model_label": "Golf"})
    assert h == [] and mobile and autoscout
    h, _, _ = L._katalog_pruefen({"make_label": "VW", "model_label": "Multivan"})
    assert any("ganze Marke VW" in x for x in h), h
    h, mobile, _ = L._katalog_pruefen({"make_label": "BMW", "model_label": "M340i"})
    assert mobile and any("AutoScout" in x and "ganze Marke BMW" in x for x in h), h
    h, mobile, autoscout = L._katalog_pruefen({"make_label": None, "model_label": None})
    assert not mobile and not autoscout and "Marke" in h[0], "ohne Marke keine Breitensuche"
    h, mobile, autoscout = L._katalog_pruefen({"make_label": "Gibtesnicht", "model_label": "X"})
    assert not mobile and autoscout and "mobile.de kennt die Marke" in h[0]
    # Nutzfahrzeug (Kleinanzeigen-Kategorie 276 bzw. Aufbau)
    assert L._nutzfahrzeug_hinweis({}, "https://www.kleinanzeigen.de/s-anzeige/t6/3012345678-276-1",
                                    "kleinanzeigen")
    assert L._nutzfahrzeug_hinweis({"category_label": "Kastenwagen"}, "https://x", "mobile")
    assert L._nutzfahrzeug_hinweis({"category_label": "Limousine"},
                                   "https://www.kleinanzeigen.de/s-anzeige/golf/3012345678-216-1",
                                   "kleinanzeigen") is None


# ---------------------------------------------------------------- RP-436-441
_KA_SEITE = """
<html><head><title>x</title></head><body>
<h1 id="viewad-title">VW Golf 7 1.4 TSI 85000 km Scheckheft</h1>
<h2 id="viewad-price">VB</h2>
<span id="viewad-locality" itemprop="addressLocality">84130 Bayern - Dingolfing</span>
<div id="viewad-details">
<div>Marke</div><div>Volkswagen</div>
<div>Modell</div><div>Golf</div>
<div>Kilometerstand</div><div>185.000 km</div>
<div>Erstzulassung</div><div>06/2016</div>
</div>
<div>Beschreibung</div>
<div>Kilometerstand: 185.000 km (Motor bei 120.000 km getauscht)</div>
<div>Zahnriemen neu für 800 € gewechselt.</div>
<div>Erstzulassung: 01/2010</div>
</body></html>
"""
_KA_URL = "https://www.kleinanzeigen.de/s-anzeige/vw-golf/3012345678-216-1234"


def test_rp436_437_438_kleinanzeigen_html():
    v = KS.parse_kleinanzeigen_html(_KA_URL, _KA_SEITE)
    assert v["mileage"] == 185000, "Tabellenwert, nicht mit der Beschreibung verkettet"
    assert v["first_registration"] == "06/2016", "Fliesstext ueberschreibt die Tabelle nicht"
    assert v["list_price"] is None and v["price_label"] == "VB", "800 € aus der Beschreibung ist kein Preis"
    assert (v["seller_zip"], v["seller_city"]) == ("84130", "Dingolfing")


def test_rp438_rueckfall_ohne_ortselement_ignoriert_den_titel():
    seite = _KA_SEITE.replace(
        '<span id="viewad-locality" itemprop="addressLocality">84130 Bayern - Dingolfing</span>',
        "<div>10115 Berlin</div>")
    v = KS.parse_kleinanzeigen_html(_KA_URL, seite)
    assert (v["seller_zip"], v["seller_city"]) == ("10115", "Berlin")
    ohne = _KA_SEITE.replace(
        '<span id="viewad-locality" itemprop="addressLocality">84130 Bayern - Dingolfing</span>', "")
    v = KS.parse_kleinanzeigen_html(_KA_URL, ohne)
    assert v["seller_zip"] is None, "85000 aus dem Titel ist keine PLZ"


def test_rp437_preis_aus_dem_preiselement():
    seite = _KA_SEITE.replace('<h2 id="viewad-price">VB</h2>',
                              '<h2 id="viewad-price">18.500 € VB</h2>')
    v = KS.parse_kleinanzeigen_html(_KA_URL, seite)
    assert v["list_price"] == 18500.0 and v["price_label"] == "18.500 € VB"


@pytest.mark.parametrize("zeile,erwartet", [
    ("84130 Bayern - Dingolfing", ("84130", "Dingolfing")),
    ("70173 Baden-Württemberg - Stuttgart", ("70173", "Stuttgart")),
    ("10365 Berlin - Lichtenberg", ("10365", "Berlin")),
    ("20095 Hamburg - Altstadt", ("20095", "Hamburg")),
    ("27568 Bremen - Bremerhaven", ("27568", "Bremerhaven")),
    ("10115 Berlin", ("10115", "Berlin")),
    ("10115 Berlin - Mitte", ("10115", "Berlin")),
    ("80331 Muenchen Bayern", ("80331", "Muenchen")),
    ("53111 Bonn - Beuel", ("53111", "Bonn")),
    ("80331 Bayern - München - Altstadt", ("80331", "München")),
    ("84130 Dingolfing - Bayern", ("84130", "Dingolfing")),
])
def test_rp441_ortszeile(zeile, erwartet):
    assert KS._split_location(zeile) == erwartet


@pytest.mark.parametrize("wert,erwartet", [
    ("185.000 km", 185000), ("185.000 km (Motor bei 120.000 km getauscht)", 185000),
    ("222.245", 222245), ("85120", 85120), ("150 Tkm", 150000),
    ("3.000.000 km", None), ("", None), (None, None),
])
def test_rp436_km_leser(wert, erwartet):
    assert KS._km_aus_text(wert) == erwartet


# ---------------------------------------------------------------- RP-439/440
def _ka_ad(**aenderung):
    ad = {"ad_id": "3458821471", "title": "Mercedes-Benz Sprinter 316 CDI Kasten",
          "description": "Gepflegter Transporter", "status": "ACTIVE",
          "price": {"amount": 13900, "currency_code": "EUR"},
          "seller": {"name": "vnightx", "type": "PRIVATE"},
          "location": {"zip": "30179", "city": "Nord", "state": "Niedersachsen"},
          "category": {"id": "276"},
          "details": {"Kilometerstand": "222.245", "Art": "Transporter"}}
    ad.update(aenderung)
    return ad


def test_rp440_pseudonym_ist_kein_vertragsname():
    v = KAPI.fahrzeug_aus_api(_ka_ad(), "https://www.kleinanzeigen.de/s-anzeige/x/3458821471-276-1")
    assert v["seller_name"] is None and v["seller_alias"] == "vnightx"
    assert v["seller_type"] == "privat"
    v = KAPI.fahrzeug_aus_api(_ka_ad(seller={"name": "Davidoff GmbH", "type": "COMMERCIAL"}), "u")
    assert v["seller_name"] == "Davidoff GmbH" and v["seller_alias"] is None


def test_rp439_nutzfahrzeug_marke_aus_dem_titel():
    v = KAPI.fahrzeug_aus_api(_ka_ad(), "u")
    assert v["make_label"] == "Mercedes-Benz", v["make_label"]
    assert v["category_label"] == "Transporter"
    assert v["mileage"] == 222245
    # ohne erkennbare Marke bleibt sie leer (kein Raten ueber Teilwoerter)
    v = KAPI.fahrzeug_aus_api(_ka_ad(title="Minimal genutzter Kastenwagen", description=""), "u")
    assert not v["make_label"]


# ---------------------------------------------------------------- RP-409 / RP-205
def test_rp409_geteilter_text():
    url = "https://www.kleinanzeigen.de/s-anzeige/vw-golf/3012345678-216-1234"
    assert LI.inserats_url_aus_text(f"Schau mal: {url}") == url
    assert LI.inserats_url_aus_text(f"Schau mal ({url})!") == url
    assert LI.inserats_url_aus_text(f"  {url}  ") == url
    assert LI.inserats_url_aus_text("Hallo") == "Hallo"
    assert LI.inserats_url_aus_text("siehe https://example.com/x") == "siehe https://example.com/x"
    assert L.CompareIn(url=f"Guck mal hier: {url} :)").url == url
    assert L.ListingURLIn(url=f"{url}").url == url
    assert L.IngestIn(url=f"Link: {url}", html="x" * 600).url == url


def test_rp205_autoscout_auslandsseiten_klar_abgelehnt():
    uid = "719b9573-d82f-4d29-85e0-54b5708d9aa6"
    for adresse in (f"https://www.autoscout24.com/offers/vw-golf-{uid}",
                    f"https://www.autoscout24.it/annunci/vw-golf-{uid}",
                    f"https://www.autoscout24.de/lst/vw-golf-{uid}"):
        with pytest.raises(LI.ListingIdentityError) as e:
            LI.get_listing_identity(adresse)
        assert "/angebote/" in str(e.value)
    assert LI.get_listing_identity(f"https://www.autoscout24.de/angebote/vw-golf-{uid}")["item_id"] == uid
    assert LI.get_listing_identity(f"https://www.autoscout24.at/angebote/vw-golf-{uid}")["source"] == "autoscout24"


# ---------------------------------------------------------------- RP-209 / RP-026
USER = {"id": "u_rp", "dealer_id": "d_rp", "role": "sucher", "active": True}


def test_rp209_resolve_ungueltige_adresse_ist_400():
    with pytest.raises(HTTPException) as e:
        asyncio.run(L.listings_resolve(L.ListingURLIn(url="https://example.com/kein-inserat"), USER))
    assert e.value.status_code == 400


def test_rp026_resolve_beachtet_die_erweiterungspflicht(wegwerf, monkeypatch):
    aufrufe = []

    async def nie(*a, **k):
        aufrufe.append(a)
        raise AssertionError("Server-Abruf trotz Erweiterungspflicht")

    async def kein_rueckfall(gewuenscht, user):
        return False

    monkeypatch.setattr(L, "_erweiterung_noetig", lambda: True)
    monkeypatch.setattr(L, "_rueckfall_erlaubt", kein_rueckfall)
    monkeypatch.setattr(L, "get_or_fetch_listing", nie)
    r = wegwerf.run(L.listings_resolve(L.ListingURLIn(url=_KA_URL), USER))
    assert r["needs_client_fetch"] is True and aufrufe == []


# ---------------------------------------------------------------- RP-442
def test_rp442_abgelaufene_quarantaene_wird_ersetzt(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    run(LI.store_client_listing(db, _KA_URL, {"price": 1}, "d1"))
    run(db.listings_cache_client.update_one(
        {"dealer_id": "d1"}, {"$set": {"expires_at": datetime.now(timezone.utc) - timedelta(minutes=1)}}))
    run(LI.store_client_listing(db, _KA_URL, {"price": 2}, "d1"))
    doc = run(db.listings_cache_client.find_one({"dealer_id": "d1"}))
    exp = doc["expires_at"] if doc["expires_at"].tzinfo else doc["expires_at"].replace(tzinfo=timezone.utc)
    assert doc["data"]["price"] == 2 and exp > datetime.now(timezone.utc)
    # gueltiger Eintrag: first-wins bleibt
    run(LI.store_client_listing(db, _KA_URL, {"price": 3}, "d1"))
    assert run(db.listings_cache_client.find_one({"dealer_id": "d1"}))["data"]["price"] == 2
    assert run(db.listings_cache_client.count_documents({"dealer_id": "d1"})) == 1


# ---------------------------------------------------------------- RP-156
class _Sammlung:
    def __init__(self, docs, schluessel="id"):
        self.docs, self.schluessel = docs, schluessel

    async def find_one(self, filt, *a, **kw):
        if "id" in filt:
            return self.docs.get(filt["id"])
        treffer = [d for d in self.docs.values()
                   if all(d.get(k) == v for k, v in filt.items())]
        return treffer[0] if treffer else None


def test_rp156_zweites_dealer_konto_gilt_im_worker_als_sucher(monkeypatch):
    gesehen = []

    async def abo(u):
        gesehen.append(u["role"])
        return {"active": u["role"] == "dealer"}      # Firmen-Abo-Rueckfall nur fuer den Chef

    async def nicht_gesperrt(_f):
        return False
    monkeypatch.setattr(deps, "subscription_for", abo)
    monkeypatch.setattr(deps, "firma_gesperrt", nicht_gesperrt)
    db = SimpleNamespace(
        users=_Sammlung({"chef": {"id": "chef", "role": "dealer", "dealer_id": "d1", "active": True},
                         "zweit": {"id": "zweit", "role": "dealer", "dealer_id": "d1", "active": True}}),
        dealers=_Sammlung({"d1": {"id": "d1", "user_id": "chef"}}))
    job = {"id": "j", "requested_by_dealer": "d1", "dealer_ids": ["d1"]}
    assert asyncio.run(LJ.wartender_darf_abrufen(db, "zweit", job)) is None
    assert asyncio.run(LJ.wartender_darf_abrufen(db, "chef", job)) == "d1"
    assert gesehen == ["sucher", "dealer"]


# ---------------------------------------------------------------- RP-204/355
def _job_doc(s, **extra):
    doc = {"id": f"job_rp_{s}", "cache_key": f"mobile:{s}", "source": "mobile", "item_id": s,
           "url": f"https://suchen.mobile.de/fahrzeuge/details.html?id={s}",
           "status": "processing", "active": True, "attempts": 1, "claim_id": "c1",
           "requested_by_user": "", "requested_by_dealer": "", "user_ids": [], "dealer_ids": [],
           "created_at": datetime.now(timezone.utc), "updated_at": datetime.now(timezone.utc)}
    doc.update(extra)
    return doc


def _abruf_stubs(monkeypatch, fehler=None):
    abrufe = []

    async def holen(db, url, fetcher, ttl_hours=0):
        return await fetcher("mobile", "1", url)

    async def fetch(db, src, iid, url, dealer_id="", user_id=""):
        abrufe.append((dealer_id, user_id))
        if fehler:
            raise fehler
        return {"make": "VW"}
    monkeypatch.setattr(LI, "get_or_fetch_listing", holen)
    monkeypatch.setattr(PF, "fetch_listing", fetch)
    return abrufe


def test_rp204_konto_job_ohne_wartende_ruft_nicht_ab(wegwerf, monkeypatch):
    db, run = wegwerf.db, wegwerf.run
    abrufe = _abruf_stubs(monkeypatch)
    s = uuid.uuid4().hex[:8]
    job = _job_doc(s, intern=False)
    run(db.link_jobs.insert_one(dict(job)))
    run(LJ._process(db, job))
    doc = run(db.link_jobs.find_one({"id": job["id"]}))
    assert abrufe == [], "ohne Wartende kein Abruf ohne Konto"
    assert doc["status"] == "failed" and doc["error"] == LJ.NIEMAND_WARTET
    # Gegenprobe: ein interner Job (ohne Konto eingereiht) ruft weiter ab
    job2 = _job_doc(s + "i", intern=True)
    run(db.link_jobs.insert_one(dict(job2)))
    run(LJ._process(db, job2))
    assert abrufe == [("", "")]
    assert run(db.link_jobs.find_one({"id": job2["id"]}))["status"] == "completed"


def test_rp355_ausgestiegen_waehrend_des_versuchs_wird_nicht_neu_eingereiht(wegwerf, monkeypatch):
    db, run = wegwerf.db, wegwerf.run
    abrufe = _abruf_stubs(monkeypatch, fehler=RuntimeError("Anbieter kaputt"))

    async def abo(_u):
        return {"active": True}
    monkeypatch.setattr(deps, "subscription_for", abo)
    s = uuid.uuid4().hex[:8]
    run(db.users.insert_one({"id": f"u_{s}", "role": "sucher", "dealer_id": f"d_{s}", "active": True}))
    run(db.dealers.insert_one({"id": f"d_{s}"}))
    # Beim Beanspruchen wartete u noch; in der Datenbank ist er inzwischen raus.
    beansprucht = _job_doc(s, intern=False, user_ids=[f"u_{s}"], dealer_ids=[f"d_{s}"],
                           requested_by_user=f"u_{s}", requested_by_dealer=f"d_{s}")
    run(db.link_jobs.insert_one({**beansprucht, "user_ids": [], "requested_by_user": ""}))
    run(LJ._process(db, beansprucht))
    doc = run(db.link_jobs.find_one({"id": beansprucht["id"]}))
    assert abrufe == [(f"d_{s}", f"u_{s}")]
    assert doc["status"] == "failed" and doc["error"] == LJ.NIEMAND_WARTET, doc


def test_rp204_neue_jobs_sind_als_konto_job_markiert(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    s = str(uuid.uuid4().int)[:8]
    mit = run(LJ.enqueue_job(db, f"https://suchen.mobile.de/fahrzeuge/details.html?id=7{s}1",
                             dealer_id="d1", user_id="u1"))
    ohne = run(LJ.enqueue_job(db, f"https://suchen.mobile.de/fahrzeuge/details.html?id=7{s}2"))
    assert mit["intern"] is False and ohne["intern"] is True
    assert LJ._ohne_wartende({"intern": False, "user_ids": []})
    assert not LJ._ohne_wartende({"user_ids": []}), "Altjobs ohne Feld: Verhalten wie bisher"


# ---------------------------------------------------------------- RP-445
def test_rp445_erweiterung_kennt_die_live_adresse():
    import json
    m = json.loads((BACKEND.parent / "browser-extension" / "manifest.json").read_text(encoding="utf-8"))
    passt = m["content_scripts"][0]["matches"]
    assert "https://app.auto-schnellkauf.de/*" in passt
    assert "https://app.autoschnell.de/*" in passt           # Beispiel-PUBLIC_HOST
    assert m["version"] != "1.0.0", "Version erhoeht, damit installierte Helfer aktualisieren"


# ---------------------------------------------------------------- RP-048/538/210
def _welt_fahrzeug(run, db, s, **extra):
    run(db.users.insert_many([
        {"id": f"chef_{s}", "dealer_id": f"d_{s}", "role": "dealer", "active": True},
        {"id": f"a_{s}", "dealer_id": f"d_{s}", "role": "sucher", "active": True,
         "first_name": "Anna", "last_name": "A"}]))
    doc = {"id": f"v_{s}", "dealer_id": f"d_{s}", "lifecycle": "verglichen", "status": "verglichen",
           "owner_user_id": f"a_{s}", "data": {"make_label": "BMW"},
           "created_at": "2026-09-01T10:00:00+00:00", "updated_at": "2026-09-01T10:00:00+00:00"}
    doc.update(extra)
    run(db.vehicles.insert_one(doc))
    return {"id": f"chef_{s}", "dealer_id": f"d_{s}", "role": "dealer"}


def test_rp048_538_chef_bekommt_hinweis_und_trimm_schutz(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    s = uuid.uuid4().hex[:8]
    chef = _welt_fahrzeug(run, db, s)
    k = run(L._fahrzeug_uebernehmen(chef, f"v_{s}", s, {"make_label": "BMW", "mileage": 2}))
    assert k == {"user_id": f"a_{s}", "name": "Anna A",
                 "seit": "2026-09-01T10:00:00+00:00", "mitbearbeiter": False}
    v = run(db.vehicles.find_one({"id": f"v_{s}"}))
    assert "mitbearbeiter_ids" not in v and v["owner_user_id"] == f"a_{s}"
    assert v["geschuetzt_bis"] > _jetzt(6 * 24 * 3600), "rund 7 Tage Schutz"
    # Das Pool-Trimmen des Suchers laesst das Fahrzeug stehen ...
    run(db.vehicles.insert_one({"id": f"v2_{s}", "dealer_id": f"d_{s}", "lifecycle": "verglichen",
                                "owner_user_id": f"a_{s}", "updated_at": _jetzt(), "created_at": _jetzt()}))
    run(FP.fahrzeugpool_trimmen(db, f"d_{s}", limit=1, owner_user_id=f"a_{s}"))
    assert run(db.vehicles.find_one({"id": f"v_{s}"})) is not None
    # ... und ein kurzer Vertragsstempel verkuerzt den Schutz nicht ($max).
    run(FP.kurz_schuetzen(db, f"d_{s}", f"v_{s}"))
    assert run(db.vehicles.find_one({"id": f"v_{s}"}))["geschuetzt_bis"] > _jetzt(6 * 24 * 3600)


def test_rp210_geloeschtes_fahrzeug_wird_gemeldet(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    s = uuid.uuid4().hex[:8]
    _welt_fahrzeug(run, db, s, lifecycle="geloescht")
    sucher = {"id": f"a_{s}", "dealer_id": f"d_{s}", "role": "sucher"}
    zustand: dict = {}
    run(L._fahrzeug_uebernehmen(sucher, f"v_{s}", s, {"make_label": "BMW"}, zustand=zustand))
    assert zustand["lifecycle"] == "geloescht"
    v = run(db.vehicles.find_one({"id": f"v_{s}"}))
    assert v["lifecycle"] == "geloescht", "kein stiller Neuanfang (Produktfrage)"
