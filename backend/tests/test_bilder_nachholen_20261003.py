# -*- coding: utf-8 -*-
"""Wunsch Ahmad 03.10.2026: "Bilder nachholen".

Kommen beim Auslesen eines neuen Inserats nur die Daten, aber keine Fotos, bietet
die Vergleichsseite den Knopf "Bilder nachholen". Der Server holt das Inserat dann
noch einmal KOMPLETT beim Anbieter. Das klappt NUR, wenn der erste Abruf geklappt
hat (Daten im gemeinsamen Speicher) und dabei keine Fotos kamen — ein gescheiterter
Abruf laeuft weiter ueber "Auslesen".

In-Prozess gegen eine Wegwerf-Datenbank (echtes Mongo, wird gedroppt).
"""
import asyncio
import inspect
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import deps  # noqa: E402
import provider_fetch as PF  # noqa: E402
import routes.listings as L  # noqa: E402
from listing_identity import ListingBusy  # noqa: E402

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
USER = {"id": "u_bn", "dealer_id": "d_bn", "role": "sucher"}
FOTOS = ["https://img.kleinanzeigen.de/api/v1/prod-ads/images/aa/1.jpg",
         "https://img.kleinanzeigen.de/api/v1/prod-ads/images/aa/2.jpg",
         "https://img.kleinanzeigen.de/api/v1/prod-ads/images/aa/3.jpg"]


@pytest.fixture
def w(monkeypatch):
    from motor.motor_asyncio import AsyncIOMotorClient
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    name = f"autoschnell_bn_{uuid.uuid4().hex[:10]}"
    db = client[name]
    for mod in (deps, L):
        monkeypatch.setattr(mod, "db", db)
    monkeypatch.setattr(L, "_erweiterung_noetig", lambda: False)
    try:
        yield SimpleNamespace(db=db, run=loop.run_until_complete)
    finally:
        try:
            loop.run_until_complete(client.drop_database(name))
        finally:
            client.close()
            loop.close()


def _nr() -> str:
    return "97" + str(uuid.uuid4().int)[:8]


def _url(n: str) -> str:
    return f"https://www.kleinanzeigen.de/s-anzeige/bilder-test/{n}-216-1"


def _anlegen(w, n, bilder=None, lease=False, ck=None, quelle="kleinanzeigen", url=None):
    ck = ck or f"kleinanzeigen:{n}"
    jetzt = datetime.now(timezone.utc)
    b = list(bilder or [])
    doc = {"cache_key": ck, "source": quelle, "item_id": n, "url": url or _url(n),
           "data": {"make": "VW", "model": "Golf", "list_price": 9000,
                    "images": b, "image_urls": b, "image_count": len(b)},
           "fetched_at": jetzt - timedelta(hours=1), "expires_at": jetzt + timedelta(days=13),
           "fetching_until": None, "use_count": 1, "fetch_count": 1}
    if lease:
        doc["fetching_until"] = jetzt + timedelta(seconds=60)
        doc["fetching_claim"] = "fremd"
    w.run(w.db.listings_cache.insert_one(doc))
    w.run(w.db.vehicle_comparisons.insert_one({"id": uuid.uuid4().hex, "cache_key": ck,
                                               "user_id": USER["id"], "dealer_id": USER["dealer_id"]}))
    return ck


def _cache(w, ck):
    return w.run(w.db.listings_cache.find_one({"cache_key": ck}, {"_id": 0}))


def _fz(w, vid):
    return w.run(w.db.vehicles.find_one({"id": vid}, {"_id": 0}))


def _holer(antwort=None, fehler=None, pause=0.0):
    aufrufe = []

    async def holen(db, src, iid, url, dealer_id="", user_id=""):
        aufrufe.append((src, iid, dealer_id, user_id))
        if pause:
            await asyncio.sleep(pause)
        if fehler:
            raise fehler
        return dict(antwort)
    return holen, aufrufe


def _mit_fotos():
    return {"make": "VW", "model": "Golf", "list_price": 8900,
            "images": FOTOS, "image_urls": FOTOS, "image_count": len(FOTOS)}


def _ohne_fotos():
    return {"make": "VW", "model": "Golf", "list_price": 9000, "images": [], "image_urls": [], "image_count": 0}


def _fehler(coro, run):
    with pytest.raises(HTTPException) as e:
        run(coro)
    return e.value


def _jetzt_naiv():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def test_01_holt_komplett_neu_und_traegt_die_fotos_ins_fahrzeug(w, monkeypatch):
    n = _nr()
    ck = _anlegen(w, n)
    w.run(w.db.vehicles.insert_many([
        {"id": f"v_{n}", "dealer_id": "d_bn", "inserat_schluessel": ck, "lifecycle": "verglichen",
         "data": {"make": "VW", "images": [], "image_urls": []}},
        # schon weiter (abgeholt) mit Haendlerkorrektur: Fotos ja, Korrektur bleibt
        {"id": f"v_{n}_b", "dealer_id": "d_bn", "inserat_schluessel": ck, "lifecycle": "abgeholt",
         "data": {"make": "VW Händlerkorrektur", "image_urls": None}},
        # hat schon Fotos -> bleibt
        {"id": f"v_{n}_c", "dealer_id": "d_bn", "inserat_schluessel": ck, "lifecycle": "verglichen",
         "data": {"make": "VW", "images": ["https://alt/1.jpg"]}},
        # andere Firma -> bleibt
        {"id": f"v_{n}_x", "dealer_id": "d_anders", "inserat_schluessel": ck, "data": {"images": []}},
    ]))
    holen, aufrufe = _holer(_mit_fotos())
    monkeypatch.setattr(L, "fetch_listing", holen)
    r = w.run(L.bilder_nachholen(L.CompareIn(url=_url(n)), USER))
    assert r["ok"] and r["nachgeholt"] and r["bilder"] == 3 and r["images"] == FOTOS
    assert len(r["images_thumbs"]) == 3 and r["hinweis"] is None
    assert aufrufe == [("kleinanzeigen", n, "d_bn", "u_bn")], "ein echter Abruf, auf das eigene Konto"
    c = _cache(w, ck)
    assert c["data"]["list_price"] == 8900, "komplett neu abgerufen, nicht nur die Fotos"
    assert c["data"]["images"] == FOTOS and c["fetch_count"] == 2
    assert c["expires_at"] > _jetzt_naiv()
    assert _fz(w, f"v_{n}")["data"]["images"] == FOTOS and _fz(w, f"v_{n}")["data"]["image_count"] == 3
    b = _fz(w, f"v_{n}_b")
    assert b["data"]["image_urls"] == FOTOS and b["data"]["make"] == "VW Händlerkorrektur"
    assert _fz(w, f"v_{n}_c")["data"]["images"] == ["https://alt/1.jpg"]
    assert _fz(w, f"v_{n}_x")["data"]["images"] == []


def test_02_nur_wenn_der_erste_abruf_geklappt_hat_und_keine_fotos_kamen(w, monkeypatch):
    holen, aufrufe = _holer(_mit_fotos())
    monkeypatch.setattr(L, "fetch_listing", holen)
    # nie ausgelesen (kein Vergleich dieses Kontos) -> 404
    n0 = _nr()
    w.run(w.db.listings_cache.insert_one({"cache_key": f"kleinanzeigen:{n0}", "data": {"images": []}}))
    assert _fehler(L.bilder_nachholen(L.CompareIn(url=_url(n0)), USER), w.run).status_code == 404
    # ausgelesen, aber der Abruf hat nicht geklappt (keine Daten) -> 409, kein Abruf
    n1 = _nr()
    w.run(w.db.vehicle_comparisons.insert_one({"id": "vc1", "cache_key": f"kleinanzeigen:{n1}",
                                               "user_id": USER["id"]}))
    f = _fehler(L.bilder_nachholen(L.CompareIn(url=_url(n1)), USER), w.run)
    assert f.status_code == 409 and "neu auslesen" in f.detail
    # Fotos sind schon da -> kein Abruf, nur ins Fahrzeug
    n2 = _nr()
    ck2 = _anlegen(w, n2, bilder=FOTOS[:2])
    w.run(w.db.vehicles.insert_one({"id": f"v_{n2}", "dealer_id": "d_bn", "inserat_schluessel": ck2,
                                    "data": {"images": []}}))
    r = w.run(L.bilder_nachholen(L.CompareIn(url=_url(n2)), USER))
    assert r["bilder"] == 2 and r["nachgeholt"] is False
    assert _fz(w, f"v_{n2}")["data"]["images"] == FOTOS[:2]
    assert aufrufe == [], "nie ein Abruf, wenn die Voraussetzungen fehlen"


def test_03_scheitert_der_neue_abruf_bleibt_der_alte_stand_und_der_versuch_zaehlt_nicht(w, monkeypatch):
    n = _nr()
    ck = _anlegen(w, n)
    alt = _cache(w, ck)
    holen, aufrufe = _holer(fehler=RuntimeError("Der Anbieter antwortet nicht."))
    monkeypatch.setattr(L, "fetch_listing", holen)
    f = _fehler(L.bilder_nachholen(L.CompareIn(url=_url(n)), USER), w.run)
    assert f.status_code == 502 and "antwortet nicht" in f.detail
    c = _cache(w, ck)
    assert c["expires_at"] == alt["expires_at"], "alter Stand wieder gueltig"
    assert c["data"] == alt["data"] and c["bilder_nachholen"]["versuche"] == 0
    assert "zuletzt" not in c["bilder_nachholen"], "keine Pause nach einem Fehlschlag"
    # sofort noch einmal (keine 429) — diesmal klappt es
    holen2, _ = _holer(_mit_fotos())
    monkeypatch.setattr(L, "fetch_listing", holen2)
    assert w.run(L.bilder_nachholen(L.CompareIn(url=_url(n)), USER))["bilder"] == 3
    # belegt -> 503 mit Retry-After (die Oberflaeche wiederholt selbst)
    n2 = _nr()
    _anlegen(w, n2)
    holen3, _ = _holer(fehler=ListingBusy("gerade belegt"))
    monkeypatch.setattr(L, "fetch_listing", holen3)
    f = _fehler(L.bilder_nachholen(L.CompareIn(url=_url(n2)), USER), w.run)
    assert f.status_code == 503 and f.headers.get("Retry-After") == "5"


def test_04_ohne_fotos_hinweis_pause_und_hoechstens_drei_versuche(w, monkeypatch):
    n = _nr()
    _anlegen(w, n)
    holen, aufrufe = _holer(_ohne_fotos())
    monkeypatch.setattr(L, "fetch_listing", holen)
    r = w.run(L.bilder_nachholen(L.CompareIn(url=_url(n)), USER))
    assert r["bilder"] == 0 and r["nachgeholt"] is False and r["versuche_uebrig"] == 2
    assert r["hinweis"] == L.KEINE_FOTOS_HINWEIS
    # gleich noch einmal -> Pause
    assert _fehler(L.bilder_nachholen(L.CompareIn(url=_url(n)), USER), w.run).status_code == 429
    monkeypatch.setattr(L, "BILDER_NACHHOLEN_PAUSE_S", 0)
    assert w.run(L.bilder_nachholen(L.CompareIn(url=_url(n)), USER))["versuche_uebrig"] == 1
    assert w.run(L.bilder_nachholen(L.CompareIn(url=_url(n)), USER))["versuche_uebrig"] == 0
    f = _fehler(L.bilder_nachholen(L.CompareIn(url=_url(n)), USER), w.run)
    assert f.status_code == 409 and "keine Fotos" in f.detail
    assert len(aufrufe) == 3


def test_05_doppelklick_und_zwei_kollegen_gleichzeitig_nur_ein_abruf(w, monkeypatch):
    n = _nr()
    ck = _anlegen(w, n)
    kollege = {"id": "u_bn2", "dealer_id": "d_bn", "role": "sucher"}
    w.run(w.db.vehicle_comparisons.insert_one({"id": "vc2", "cache_key": ck, "user_id": "u_bn2"}))
    holen, aufrufe = _holer(_mit_fotos(), pause=0.4)
    monkeypatch.setattr(L, "fetch_listing", holen)

    async def beide():
        return await asyncio.gather(
            L.bilder_nachholen(L.CompareIn(url=_url(n)), USER),
            L.bilder_nachholen(L.CompareIn(url=_url(n)), kollege),
            return_exceptions=True)
    erg = w.run(beide())
    ok = [e for e in erg if isinstance(e, dict)]
    abgewiesen = [e for e in erg if isinstance(e, HTTPException)]
    assert len(ok) == 1 and len(abgewiesen) == 1 and abgewiesen[0].status_code == 429, erg
    assert len(aufrufe) == 1


def test_06_laufender_abruf_wird_nicht_gestoert(w, monkeypatch):
    n = _nr()
    ck = _anlegen(w, n, lease=True)
    holen, aufrufe = _holer(_mit_fotos())
    monkeypatch.setattr(L, "fetch_listing", holen)
    f = _fehler(L.bilder_nachholen(L.CompareIn(url=_url(n)), USER), w.run)
    assert f.status_code == 503 and aufrufe == []
    c = _cache(w, ck)
    assert c["expires_at"] > _jetzt_naiv() and c["bilder_nachholen"]["versuche"] == 0


def test_07_zaehlt_fuer_das_tageslimit_des_kontos(w, monkeypatch):
    n = _nr()
    _anlegen(w, n)

    async def abrufen(db, source, item_id, url):
        return _mit_fotos()
    monkeypatch.setattr(PF, "MOCK_PROVIDER_FETCH", False)
    monkeypatch.setattr(PF, "_abrufen", abrufen)
    assert w.run(L.bilder_nachholen(L.CompareIn(url=_url(n)), USER))["bilder"] == 3
    tag = PF.tagesschluessel()
    assert w.run(w.db.provider_budget.find_one({"_id": f"{tag}:konto:u_bn"}))["n"] == 1


def test_08_mobile_leert_auch_den_zweiten_speicher(w, monkeypatch):
    n = "37" + str(uuid.uuid4().int)[:7]
    url = f"https://suchen.mobile.de/fahrzeuge/details.html?id={n}"
    ck = _anlegen(w, n, ck=f"mobile:{n}", quelle="mobile", url=url)
    w.run(w.db.vehicle_cache.insert_one({"mobile_ad_id": n, "data": {"images": []}}))
    w.run(w.db.vehicles.insert_one({"id": f"v_mobile_{n}", "dealer_id": "d_bn", "inserat_schluessel": ck,
                                    "data": {"images": []}}))
    monkeypatch.setattr(L, "mobile_quelle_verfuegbar", lambda: True)
    holen, aufrufe = _holer(_mit_fotos())
    monkeypatch.setattr(L, "fetch_listing", holen)
    r = w.run(L.bilder_nachholen(L.CompareIn(url=url), USER))
    assert r["bilder"] == 3 and aufrufe[0][:2] == ("mobile", n)
    assert w.run(w.db.vehicle_cache.find_one({"mobile_ad_id": n})) is None
    assert _fz(w, f"v_mobile_{n}")["data"]["images"] == FOTOS


def test_09_knopf_nur_wenn_moeglich(w, monkeypatch):
    assert L.bilder_nachholen_moeglich("mobile", []) is True
    assert L.bilder_nachholen_moeglich("autoscout24", []) is True
    assert L.bilder_nachholen_moeglich("mobile", ["x"]) is False
    assert L.bilder_nachholen_moeglich("unbekannt", []) is False
    assert L.bilder_nachholen_moeglich("kleinanzeigen", []) is True
    monkeypatch.setattr(L, "_erweiterung_noetig", lambda: True)
    assert L.bilder_nachholen_moeglich("kleinanzeigen", []) is False
    # Kleinanzeigen im Browser-Modus: der Server holt nicht -> 409
    n = _nr()
    _anlegen(w, n)
    f = _fehler(L.bilder_nachholen(L.CompareIn(url=_url(n)), USER), w.run)
    assert f.status_code == 409 and "neu auslesen" in f.detail
    # die Vergleichsantwort traegt den Merker
    quelle = inspect.getsource(L.compare)
    assert '"bilder_nachholen_moeglich": bilder_nachholen_moeglich(source, _bilder)' in quelle
