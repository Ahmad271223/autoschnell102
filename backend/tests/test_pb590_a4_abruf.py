# -*- coding: utf-8 -*-
"""Pruefbericht 20.09.2026 (590 Befunde), Team A4: Inserats-Abruf, Quellen-
Parser, Warteschlange, Limiter.

Abruf-Pfad (echtes Mongo, Wegwerf-Datenbank je Test):
  A-17/A-06  Gesamtfrist je Abruf; der Abruf laeuft danach im Hintergrund zu
             Ende, Lease und Plaetze werden ueber den finally-Pfad frei
  B-04       Firmenanteil an den Anbieter-Plaetzen (nur wenn andere warten)
  A-05       enqueue_job meldet `neu`; gescheiterte/abgebrochene Jobs geben
             den gebuchten Rueckfall zurueck
  U-11       peek_cached_listing liefert die Herkunft (Speicher/Quarantaene)

Parser und Helfer (ohne Netz, ohne Datenbank):
  S-09 S-19 S-20 S-21 S-22 S-23 S-24 S-25 S-26 S-27 S-28 S-29 S-30 S-31
  A-11 A-13 B-06 B-07 B-08 B-10 B-12 B-13 B-14 B-15 SV-16 A-16 A-20 A-21 R1-05 DP-04
"""
import asyncio
import inspect
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import autoscout_service as AS  # noqa: E402
import bild_proxy as BP  # noqa: E402
import deps  # noqa: E402
import fahrzeug_codes as FC  # noqa: E402
import kleinanzeigen_api as KAPI  # noqa: E402
import kleinanzeigen_service as KS  # noqa: E402
import link_jobs as LJ  # noqa: E402
import listing_identity as LI  # noqa: E402
import mobile_service as M  # noqa: E402
import provider_fetch as PF  # noqa: E402
import provider_limiter as PL  # noqa: E402
import rate_limiter as RL  # noqa: E402
import regeln as R  # noqa: E402
import routes.listings as L  # noqa: E402
import routes.manual_search as MS  # noqa: E402

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def wegwerf(monkeypatch):
    from motor.motor_asyncio import AsyncIOMotorClient
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    name = f"autoschnell_pb590a4_{uuid.uuid4().hex[:10]}"
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


def _ka_url():
    return f"https://www.kleinanzeigen.de/s-anzeige/pb590/{uuid.uuid4().int % 10**9 + 10**9}-216-1"


def _q(url):
    return parse_qs(urlparse(url).query)


# ============================================================ A-17 / A-06
def test_a17_gesamtfrist_abruf_laeuft_im_hintergrund_zu_ende(wegwerf):
    """Nach Ablauf der Frist bekommt der Aufrufer sofort eine klare Meldung;
    der Abruf selbst laeuft weiter, legt sein Ergebnis in den Speicher und
    gibt Lease und Anbieter-Plaetze ueber das finally frei."""
    db, run = wegwerf.db, wegwerf.run
    url = _ka_url()
    key = LI.get_listing_identity(url)["cache_key"]
    from kleinanzeigen_api import api_verfuegbar
    topf = "kleinanzeigen_api" if api_verfuegbar() else "kleinanzeigen"

    async def lauf():
        await LI.ensure_cache_indexes(db)
        aufrufe = {"n": 0}

        async def langsam(src, iid, u):
            aufrufe["n"] += 1
            await asyncio.sleep(2.5)
            return {"mobile_ad_id": iid, "title": "spaet", "list_price": 1}

        t0 = time.monotonic()
        with pytest.raises(LI.AbrufDauertZuLange) as e:
            await LI.get_or_fetch_listing(db, url, langsam, ttl_hours=1, frist_sekunden=1)
        dauer = time.monotonic() - t0
        assert e.value.hintergrund is True and "dauert zu lange" in str(e.value)
        assert dauer < 2.3, f"die Frist griff nicht ({dauer:.1f} s)"
        assert isinstance(e.value, LI.ListingBusy)
        doc = await db.listings_cache.find_one({"cache_key": key})
        assert doc and doc.get("fetching_claim"), "Lease muss waehrend des Hintergrund-Abrufs stehen"
        for _ in range(40):
            await asyncio.sleep(0.2)
            doc = await db.listings_cache.find_one({"cache_key": key})
            if doc and doc.get("data"):
                break
        assert doc.get("data", {}).get("title") == "spaet", "Hintergrund-Abruf hat nicht gespeichert"
        assert "fetching_claim" not in doc and doc.get("fetching_until") is None
        await asyncio.sleep(0.3)
        lim = await db.provider_limits.find_one({"provider": topf}, {"_id": 0})
        assert (lim or {}).get("active", 0) == 0, lim
        assert await db.provider_slots.count_documents({"provider": topf}) == 0
        daten, aus_cache = await LI.get_or_fetch_listing(db, url, langsam, ttl_hours=1)
        assert aus_cache is True and daten["title"] == "spaet"
        assert aufrufe["n"] == 1, "kein zweiter Anbieter-Abruf"

    run(lauf())


def test_a06_herzschlag_ist_begrenzt_und_frist_ist_konfigurierbar():
    assert LI.ABRUF_GESAMT_SEKUNDEN == 90
    assert LI.LEASE_HERZSCHLAG_MAX_SEKUNDEN >= 60
    q = inspect.getsource(LI.get_or_fetch_listing)
    assert "LEASE_HERZSCHLAG_MAX_SEKUNDEN" in q and "_aio.wait_for(" in q
    # beim Warten auf den Topf wird der Quellen-Platz zurueckgegeben
    schleife = q.split("slot_bis = ")[1].split("except BaseException")[0]
    assert 'acquire_slot(db, "apify"' in schleife and "release_slot(db, slot_id)" in schleife
    # Aufraeumen ueber den bestehenden finally-Pfad (Test 18.09. prueft dieselben Strings)
    assert "_aio.shield(release_slot(db, slot_id))" in q
    assert "_aio.shield(_lease_freigeben(db, cache_key, claim))" in q


# ================================================================== B-04
def test_b04_firma_haelt_hoechstens_ihren_anteil_sobald_andere_warten(wegwerf, monkeypatch):
    db, run = wegwerf.db, wegwerf.run
    monkeypatch.setitem(PL.PROVIDER_MAX_CONCURRENT, "mobile", 4)
    monkeypatch.setattr(PL, "FIRMENANTEIL_PROZENT", 50)
    assert PL.firmen_anteil("mobile") == 2

    async def lauf():
        await PL.ensure_slot_indexes(db)
        # allein auf der Quelle: alle vier Plaetze (Lasttest einer Firma bleibt)
        allein = [await PL.acquire_slot(db, "mobile", dealer_id="A") for _ in range(4)]
        assert all(allein), allein
        for s in allein:
            await PL.release_slot(db, s)
        a1 = await PL.acquire_slot(db, "mobile", dealer_id="A")
        a2 = await PL.acquire_slot(db, "mobile", dealer_id="A")
        b1 = await PL.acquire_slot(db, "mobile", dealer_id="B")
        a3 = await PL.acquire_slot(db, "mobile", dealer_id="A")   # A: 2 = Anteil, B wartet mit
        b2 = await PL.acquire_slot(db, "mobile", dealer_id="B")
        ohne = await PL.acquire_slot(db, "mobile")                # ohne Firma: nur die Grenze
        lim = await db.provider_limits.find_one({"provider": "mobile"}, {"_id": 0})
        slots = await db.provider_slots.count_documents({"provider": "mobile", "dealer_id": "A"})
        for s in (a1, a2, b1, b2, ohne):
            await PL.release_slot(db, s)
        return a1, a2, b1, a3, b2, ohne, lim, slots

    a1, a2, b1, a3, b2, ohne, lim, slots = run(lauf())
    assert a1 and a2 and b1 and b2, "unter dem Anteil bekommt jede Firma Plaetze"
    assert a3 is None, "Firma A ueber ihrem Anteil, waehrend B Plaetze haelt"
    assert ohne is None and lim["active"] == 4, "ohne Firma gilt allein die Obergrenze (4 belegt)"
    assert slots == 2


# ================================================================== A-05
def test_a05_beitritt_und_scheitern_geben_den_rueckfall_zurueck(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    url = _ka_url()

    async def lauf():
        await LJ.ensure_job_indexes(db)
        schl = PF.rueckfall_schluessel("uA")
        await db.provider_budget.insert_one({"_id": schl, "n": 1})
        j1 = await LJ.enqueue_job(db, url, dealer_id="d1", user_id="uA", rueckfall_schluessel=schl)
        assert j1["neu"] is True
        gespeichert = await db.link_jobs.find_one({"id": j1["id"]}, {"_id": 0})
        assert gespeichert["rueckfall_schluessel"] == schl and "neu" not in gespeichert
        j2 = await LJ.enqueue_job(db, url, dealer_id="d1", user_id="uB")
        assert j2["id"] == j1["id"] and j2["neu"] is False, "Beitritt ist kein neuer Job"
        # Job scheitert endgueltig -> Punkt zurueck, aber nur EINMAL
        ok = await LJ._job_scheitern(db, gespeichert, {"id": j1["id"], "status": "queued"}, "kaputt")
        assert ok is True
        assert (await db.provider_budget.find_one({"_id": schl}))["n"] == 0
        ok2 = await LJ._job_scheitern(db, gespeichert, {"id": j1["id"], "status": "queued"}, "x")
        assert ok2 is False
        assert (await db.provider_budget.find_one({"_id": schl}))["n"] == 0
        # Abbruch durch den Nutzer (X), bevor ein Worker begann -> Punkt zurueck
        schl2 = PF.rueckfall_schluessel("uC")
        await db.provider_budget.insert_one({"_id": schl2, "n": 1})
        j3 = await LJ.enqueue_job(db, _ka_url(), dealer_id="d1", user_id="uC",
                                  rueckfall_schluessel=schl2)
        erg = await LJ.warten_beenden(db, j3["id"], dealer_id="d1", user_id="uC")
        assert erg["status"] == "abgebrochen"
        assert (await db.provider_budget.find_one({"_id": schl2}))["n"] == 0

    run(lauf())


def test_a05_listings_check_verdrahtung_und_hinweis(monkeypatch):
    q = inspect.getsource(L.listings_check)
    assert "rueckfall_schluessel=rueckfall_am_job" in q
    assert 'if rueckfall_gebucht and not job.get("neu"):' in q
    assert "await _rueckfall_zurueck(user)" in q
    monkeypatch.setattr(L, "RUECKFALL_TAGESLIMIT", 25)
    assert "0 Uhr" in L._erweiterung_hinweis(True) and "25/Tag" in L._erweiterung_hinweis(True)
    assert "0 Uhr" not in L._erweiterung_hinweis(False)
    # der Worker bucht bei jedem Endzustand "failed" zurueck
    p = inspect.getsource(LJ._process)
    assert p.count("_job_scheitern(db, job, eigener_claim") >= 6
    assert "_rueckfall_freigeben(db, job)" in p                  # Import-Fehlerpfad
    assert "_rueckfall_freigeben(db, j)" in inspect.getsource(LJ._requeue_stale)


# ================================================================== U-11
def test_u11_herkunft_aus_speicher_oder_quarantaene(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    url1, url2 = _ka_url(), _ka_url()
    jetzt = datetime.now(timezone.utc)

    async def lauf():
        await LI.ensure_cache_indexes(db)
        k1 = LI.get_listing_identity(url1)
        await db.listings_cache.insert_one({
            "cache_key": k1["cache_key"], "source": k1["source"], "item_id": k1["item_id"],
            "url": url1, "data": {"title": "server"}, "fetched_at": jetzt,
            "expires_at": jetzt.replace(year=jetzt.year + 1)})
        await LI.store_client_listing(db, url2, {"title": "browser", "list_price": 1}, "dX")
        return (await LI.peek_cached_listing(db, url1, dealer_id="dX"),
                await LI.peek_cached_listing(db, url2, dealer_id="dX"),
                await LI.peek_cached_listing(db, url2, dealer_id="dY"))

    speicher, quarantaene, fremd = run(lauf())
    assert speicher == ({"title": "server"}, "speicher")
    assert quarantaene[1] == "quarantaene" and quarantaene[0]["title"] == "browser"
    assert fremd is None, "die Quarantaene gehoert der einreichenden Firma"
    q = inspect.getsource(L.compare)
    assert '"beweis_moeglich": beweis_moeglich' in q
    assert 'beweis_moeglich = client_hit is None or client_hit[1] == "speicher"' in q
    # die Suche nach einem vorhandenen Dokument laeuft auch beim Treffer
    assert "if client_hit is None:\n        from beweis_service" not in q


# ================================================================== A-20
def test_a20_snapshot_id_ist_weg():
    assert not hasattr(LI, "set_cache_snapshot")
    assert "set_cache_snapshot" not in LI.__all__
    assert '"snapshot_id"' not in inspect.getsource(LI.get_or_fetch_listing)
    assert '"snapshot_id"' not in inspect.getsource(L.listings_resolve)     # kein Antwortfeld mehr
    assert "cached_snapshot_id" not in inspect.getsource(L.compare)
    import server
    assert "await asyncio.to_thread(init_storage)" in inspect.getsource(server)


# ================================================================== A-21
def test_a21_promotion_nimmt_die_aelteste_einreichung():
    q = inspect.getsource(LI.store_client_listing)
    assert '.sort("created_at", 1)' in q and "if True:" not in q


# ================================================================= R1-05
def test_r1_05_dritter_duplicatekey_wird_503_statt_500():
    q = inspect.getsource(L._fahrzeug_uebernehmen)
    stelle = q.split("except DuplicateKeyError:")[1].split("if vorher is not None:")[0]
    assert "HTTPException(" in stelle and "503" in stelle and '"X-Wiederholen"' in stelle
    assert "\n            raise\n" not in stelle


# ================================================================== A-16
def test_a16_leere_schlange_kostet_keine_aggregation():
    q = inspect.getsource(LJ._claim_many)
    assert q.index("find_one(") < q.index("aggregate("), "der billige Blick muss VOR den Aggregationen stehen"
    assert "**_reif()" in q.split("aggregate(")[0]
    w = inspect.getsource(LJ.run_job_worker_forever)
    assert "takt = LEERLAUF_TAKT_S" in w and "REQUEUE_ABSTAND_S" in w
    assert LJ.LEERLAUF_TAKT_S >= 1.0 and LJ.REQUEUE_ABSTAND_S >= 2.0
    assert "wartung.aktiv_async(db)" in w                       # Nr. 64 bleibt


# ================================================================= DP-04
def test_dp04_apify_zeitlimit_und_meldung():
    assert 10 <= M.APIFY_TIMEOUT_SEKUNDEN <= 85
    assert "APIFY_TIMEOUT_SEKUNDEN" in inspect.getsource(AS.fetch_autoscout_vehicle)
    assert "180.0" not in inspect.getsource(M._fetch_from_apify)
    e = LI.AbrufDauertZuLange()
    assert isinstance(e, LI.ListingBusy) and "in einer Minute" in str(e) and e.hintergrund is False
    assert LJ.fehlertext(e) == LI.ABRUF_ZU_LANGE_TEXT, "eigener Sachtext geht an den Sucher"
    p = inspect.getsource(LJ._process)
    assert "isinstance(exc, AbrufDauertZuLange) and not exc.hintergrund" in p


# ================================================================== B-13
def test_b13_tagesschluessel_in_deutscher_zeit():
    utc = timezone.utc
    assert PF.tagesschluessel(datetime(2026, 9, 22, 22, 30, tzinfo=utc)) == "2026-09-23"   # 00:30 MESZ
    assert PF.tagesschluessel(datetime(2026, 9, 22, 21, 30, tzinfo=utc)) == "2026-09-22"   # 23:30 MESZ
    assert PF.tagesschluessel(datetime(2026, 1, 10, 23, 30, tzinfo=utc)) == "2026-01-11"   # 00:30 MEZ
    assert PF.rueckfall_schluessel("u1").endswith(":rueckfall:u1")
    assert PF.rueckfall_schluessel("") .endswith(":rueckfall:ohne")
    assert "tagesschluessel()" in inspect.getsource(PF._budget_pruefen)
    assert "rueckfall_schluessel(" in inspect.getsource(L._rueckfall_erlaubt)
    assert "rueckfall_schluessel(" in inspect.getsource(L._rueckfall_zurueck)


# ============================================================ B-15 / SV-16
def test_b15_lokaler_rueckfall_teilt_das_limit(monkeypatch):
    lim = RL.SlidingWindowRateLimiter(max_attempts=8, window_seconds=60, name="pb590-a4-test")
    monkeypatch.setattr(RL, "_WEB_CONCURRENCY", 4)
    assert lim._lokale_grenze() == 2
    assert lim._check_lokal("k") and lim._check_lokal("k") and not lim._check_lokal("k")
    monkeypatch.setattr(RL, "_WEB_CONCURRENCY", 100)
    assert lim._lokale_grenze() == 1, "mindestens ein Versuch je Prozess"
    assert "_rueckfall_melden" in inspect.getsource(RL.SlidingWindowRateLimiter.check)
    assert '"limiter_lokal"' in inspect.getsource(RL.SlidingWindowRateLimiter._rueckfall_melden)


def test_sv16_limiter_braucht_einen_namen():
    with pytest.raises(ValueError):
        RL.SlidingWindowRateLimiter(max_attempts=20, window_seconds=60)
    assert L._status_limiter.name == "listings-status"
    import server
    assert server._client_error_limiter.name == "client-errors"


# ================================================================== B-09
def test_b09_bildproxy_negativspeicher_und_gesamtdeckel(monkeypatch):
    assert BP.HOLEN_GESAMT_SEKUNDEN <= 20
    assert "asyncio.wait_for(" in inspect.getsource(BP._holen)
    assert "_clients.get(loop)" in inspect.getsource(BP._holen), "ein Client je Loop statt je Bild"
    BP._negativ.clear()
    monkeypatch.setattr(BP, "NEGATIV_SEKUNDEN", 600)
    aufrufe = {"n": 0}

    async def kaputt(url):
        aufrufe["n"] += 1
        return None
    monkeypatch.setattr(BP, "_holen", kaputt)
    u = "https://img.kleinanzeigen.de/api/v1/prod-ads/images/pb590/x.jpg"
    assert asyncio.run(BP.laden(u)) is None
    assert asyncio.run(BP.laden(u)) is None
    assert aufrufe["n"] == 1, "der zweite Aufruf muss aus dem Negativspeicher kommen"
    assert BP._negativ_gemerkt(u)
    BP._negativ.clear()


# ================================================================== B-04 (Signatur)
def test_b04_slot_dokument_traegt_die_firma():
    q = inspect.getsource(PL.acquire_slot)
    assert '"dealer_id": dealer_id or ""' in q and "_firma_ueber_anteil" in q
    assert 1 <= PL.FIRMENANTEIL_PROZENT <= 100


# ================================================================== S-19
def test_s19_tueren_einheitlich():
    assert FC.tueren_text("FOUR_OR_FIVE") == "4/5" and FC.tueren_text("TWO_OR_THREE") == "2/3"
    assert FC.tueren_text("SIX_OR_SEVEN") == "6/7"
    assert FC.tueren_text("5") == "5" and FC.tueren_text(5) == "5" and FC.tueren_text("4 / 5") == "4/5"
    assert FC.tueren_text("5 Türen") == "5"
    assert FC.tueren_text("") is None and FC.tueren_text(None) is None and FC.tueren_text("viele") is None
    assert FC.tueren_bereich("4/5") == (4, 5) and FC.tueren_bereich("3") == (3, 3)
    assert FC.tueren_code("4/5") == "FOUR_OR_FIVE" and FC.tueren_code("3") == "TWO_OR_THREE"
    assert FC.tueren_code("6/7") == "SIX_OR_SEVEN" and FC.tueren_code(None) is None
    v = {"make_label": "BMW", "model_label": "320d", "doors": "4/5", "mileage": 1000}
    regeln = {**M.DEFAULT_RULES, "doors": {"mode": "exact"}}
    assert _q(M.build_search_url(v, regeln)).get("doors") == ["FOUR_OR_FIVE"]
    q = _q(AS.build_search_url(v, regeln))
    assert q.get("doorfrom") == ["4"] and q.get("doorto") == ["5"]
    # Altbestand mit Code funktioniert weiter
    assert _q(M.build_search_url({**v, "doors": "FOUR_OR_FIVE"}, regeln)).get("doors") == ["FOUR_OR_FIVE"]


# ================================================================== mobile.de XML (S-09/S-20/S-27/S-30)
def test_xml_parser_preisart_mwst_tueren_bilder_kuerzel():
    v = M._SANDBOX_BUNDLE.get("1000006")
    assert v, "Sandbox-Inserat 1000006 fehlt"
    assert v["price_type"] == "FIXED" and v["price_negotiable"] is False
    assert v["mwst_ausweisbar"] is False
    assert v["doors"] == "4/5"
    assert v["images"] == v["image_urls"] and v["image_count"] == len(v["images"])
    assert "ABS" in v["features"] and "ESP" in v["features"], v["features"][:10]
    assert "Abs" not in v["features"]
    assert M._feature_label("ALLOY_WHEELS") == "Alloy Wheels"
    assert M._feature_label("ABS") == "ABS" and M._feature_label("METALLIC") == "Metallic"
    assert M.preis_verhandelbar("NEGOTIABLE") and not M.preis_verhandelbar("FIXED")
    # S-20: kaputte Knoten werfen nicht
    leer = M._parse_ad_xml({"ad:vehicle": {"ad:specifics": ""}, "ad:price": ""})
    assert leer["make"] is None and leer["list_price"] is None


# ================================================================== Apify mobile (S-23/S-25/S-09/S-19)
def test_apify_mobile_erstzulassung_waehrung_preisart():
    item = json.loads((FIX / "apify_mobile_item.json").read_text(encoding="utf-8"))[0]
    v = M._parse_apify_item(item, "42196329136896")
    assert v["currency"] == "EUR" and v["price_type"] == "FIXED" and v["price_negotiable"] is False
    assert v["first_registration"] == "06/2019" and v["neufahrzeug"] is False
    assert v["doors"] == "4/5"
    item["price"] = {"grs": {"amount": 100, "currency": "CHF"}, "type": "NEGOTIABLE", "vatable": True}
    for a in item["attributes"]:
        if a["tag"] == "firstRegistration":
            a["value"] = "New"
    v = M._parse_apify_item(item, "1")
    assert v["currency"] == "CHF" and v["price_negotiable"] is True and v["mwst_ausweisbar"] is True
    assert v["first_registration"] is None and v["neufahrzeug"] is True
    assert any("Neufahrzeug" in h for h in AS.regeln_nicht_abgebildet(v, M.DEFAULT_RULES))
    assert not any("Neufahrzeug" in h for h in AS.regeln_nicht_abgebildet(
        v, {**M.DEFAULT_RULES, "first_registration": {"mode": "ignore"}}))
    assert M.apify_erstzulassung("2019-06") == ("06/2019", False)
    assert M.apify_erstzulassung("6/2019") == ("06/2019", False)
    assert M.apify_erstzulassung("Neu") == (None, True)
    assert M.apify_erstzulassung("irgendwas") == (None, False)


# ================================================================== AutoScout (S-21/S-24/S-28/S-29/S-09)
def test_autoscout_merkmale_bilder_beschreibung_farbe_preisart():
    item = json.loads((FIX / "apify_autoscout_item.json").read_text(encoding="utf-8"))[0]
    item["comfort"] = "Audiosystem (Touchscreen, MP3), ABS, abs"
    item["safety"] = ["ESP", "ABS"]
    bild = item["images"][0]
    item["images"] = [bild, bild, "kein-http", item["images"][1]]
    item["colour"] = item["manufacturerColour"] = ""
    item["paint"] = "Rot"
    item["isPriceNegotiable"] = True
    item["isPriceDeductable"] = True
    v = AS.parse_autoscout_item(item, "719b9573-d82f-4d29-85e0-54b5708d9aa6")
    assert v["features"] == ["Audiosystem (Touchscreen, MP3)", "ABS", "ESP"]
    assert v["images"] == [bild, item["images"][3]] and v["image_count"] == 2
    assert v["image_urls"] == v["images"]
    assert "\n" in v["description"] and "Mercedes-Benz B 160 zu verkaufen" in v["description"]
    assert v["color"] == "Rot"
    assert v["price_negotiable"] is True and v["mwst_ausweisbar"] is True and v["price_type"] == "NEGOTIABLE"
    item["paint"] = "Andere"
    assert AS.parse_autoscout_item(item, "x")["color"] is None
    item["description"] = ""
    assert AS.parse_autoscout_item(item, "x")["description"] == item["descriptionText"].strip()


# ================================================================== Kleinanzeigen (S-31/A-11/S-22/S-26/S-30)
def test_ka_erstzulassung_zweistellig_und_halter_plausibel():
    assert KS._parse_first_registration("03/19") == "03/2019"
    assert KS._parse_first_registration("EZ 3/19") == "03/2019"
    assert KS._parse_first_registration("12/99") == "12/1999"
    assert KS._parse_first_registration("März 2019") == "03/2019"
    assert KS._parse_first_registration("06/2018") == "06/2018"
    assert KS._halter("125.000 km") is None and KS._halter("27") is None
    assert KS._halter("0") == 0 and KS._halter("3") == 3 and KS._halter(None) is None
    tab = KS._parse_structured("Anzahl der Fahrzeughalter\n125.000 km\nKilometerstand\n125.000 km\n"
                               "Anzahl Türen\n4/5\nAnzahl Sitzplätze\n5\nBeschreibung\nText")
    assert "Anzahl der Fahrzeughalter" not in tab, tab
    assert tab["Kilometerstand"] == "125.000 km" and tab["Anzahl Türen"] == "4/5" and tab["Anzahl Sitzplätze"] == "5"


def _ka_ad(**details):
    return {"ad_id": "3012345678", "title": "VW Golf VII 1.6 TDI", "description": "Gepflegt, 3. Hand",
            "price": {"amount": 9000, "currency_code": "EUR", "price_type": "SPECIFIED_AMOUNT"},
            "images": ["https://img.kleinanzeigen.de/a.jpg"], "status": "ACTIVE",
            "seller": {"name": "X", "type": "PRIVATE"},
            "location": {"zip": "30159", "city": "Hannover", "state": "Niedersachsen"},
            "details": {"Marke": "Volkswagen", "Modell": "Golf", "Kilometerstand": "100.000",
                        "Erstzulassung": "03/19", "Anzahl Türen": "4/5", **details}}


def test_ka_api_halter_null_zaehlt_und_felder_einheitlich():
    v = KAPI.fahrzeug_aus_api(_ka_ad(**{"Anzahl der Fahrzeughalter": "0"}),
                              "https://www.kleinanzeigen.de/s-anzeige/x/3012345678-216-1", "3012345678")
    assert v["previous_owners"] == 0, "Tabellenwert 0 darf nicht auf den Freitext (3. Hand) zurueckfallen"
    assert v["doors"] == "4/5" and v["first_registration"] == "03/2019"
    assert v["image_urls"] == v["images"] == ["https://img.kleinanzeigen.de/a.jpg"]
    v2 = KAPI.fahrzeug_aus_api(_ka_ad(**{"Anzahl der Fahrzeughalter": "125000"}),
                               "https://www.kleinanzeigen.de/s-anzeige/x/3012345678-216-1", "3012345678")
    assert v2["previous_owners"] == 3, "unplausibel -> Freitext (3. Hand)"


def test_s26_merkmale_in_beide_richtungen():
    text = "Antiblockiersystem (ABS), Klimaanlage, Sitzheizung, Tempomat, Navi, USB"
    m = KAPI._alle_merkmale({"ABS": "true", "Klimaanlage": "true"}, text)
    assert "ABS" in m and "Klimaanlage" in m
    assert "Antiblockiersystem (ABS)" not in m, m
    assert "Sitzheizung" in m and "USB" in m
    assert KAPI._merkmal_enthalten("abs", "antiblockiersystem (abs)")
    assert not KAPI._merkmal_enthalten("ahk", "fahrkomfort")
    assert KAPI._merkmal_enthalten("sitzheizung", "sitzheizung vorn")


def test_a13_eigener_abruf_meldet_anbieterfehler():
    q = inspect.getsource(KS._fetch_html)
    code = "\n".join(z.split("#", 1)[0] for z in q.splitlines())      # ohne Kommentare
    assert "raise_for_status" not in code
    assert "AnbieterFehler(" in q and "ART_LIMIT" in q and "aus_ausnahme(" in q
    assert "follow_redirects=False" in q and "_get_mit_geprueften_weiterleitungen" in q


# ================================================================== B-10
def test_b10_beschreibung_bestimmt_kein_modell_mehr():
    v = {"make_label": "Volkswagen", "make": "VOLKSWAGEN", "model_label": "Weitere Volkswagen",
         "model_description": "Weitere Volkswagen", "description": "Günstiger als jeder Golf!"}
    M._enhance_generic_model(v)
    assert v["model_label"] == "Weitere Volkswagen", v
    assert v.get("model_vorschlag") == "Golf"
    v2 = {"make_label": "Volkswagen", "model_label": "Weitere Volkswagen",
          "model_description": "Volkswagen Golf VII Variant", "description": "Guenstig"}
    M._enhance_generic_model(v2)
    assert v2["model_label"].lower().startswith("golf"), v2


# ============================================================ B-08 / B-12 / B-14
def test_b08_result_count_entfaellt():
    assert "result_count" not in M.DEFAULT_RULES and "result_count" not in M.DEFAULT_EXPORT_RULES
    assert "result_count" not in R.regeln_validieren({"result_count": 50, "sort": "price_asc"})
    assert "result_count" not in R.regeln_lesen({"result_count": 4}, M.DEFAULT_RULES)
    assert not hasattr(R, "RESULT_COUNT_MAX")


def test_b12_gelesene_regeln_teilen_nichts_mit_dem_standard():
    for rohe in ({}, "kaputt", {"damage": "kaputt"}):
        r = R.regeln_lesen(rohe, M.DEFAULT_RULES)
        r["mileage"]["value"] = 1
        r["country"]["codes"].append("XX")
        assert M.DEFAULT_RULES["mileage"]["value"] == 30000
        assert M.DEFAULT_RULES["country"]["codes"] == ["DE"]


def test_b14_kaputtes_ablaufdatum_gilt_als_abgelaufen():
    class _Db:
        class vehicle_cache:
            @staticmethod
            async def find_one(*a, **k):
                return {"mobile_ad_id": "1", "data": {"x": 1}, "expires_at": "kein datum"}
    assert asyncio.run(M.cache_get(_Db(), "1")) is None


# ============================================================ B-06 / B-07
def test_b06_b07_manuelle_suche():
    assert MS._fuel_code("Hybrid Diesel") == "HYBRID_DIESEL"
    assert MS._fuel_code("Hybrid (Diesel/Elektro)") == "HYBRID_DIESEL"
    assert MS._fuel_code("Diesel") == "DIESEL" and MS._fuel_code("Dieselll") == ""
    assert FC.autoscout_kraftstoff("HYBRID_DIESEL") == "3"
    q = inspect.getsource(MS.manual_search)
    assert "autoscout_kraftstoff(fuel_code)" in q
    assert q.index("mobile_url = (") < q.index("Kein Modell gewaehlt")
    assert 'welche = "beide Links zeigen" if mobile_url else "der AutoScout-Link zeigt"' in q
