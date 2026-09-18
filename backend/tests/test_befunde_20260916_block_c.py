# -*- coding: utf-8 -*-
"""Reviewer-Befunde 16.09.2026, Block C (Vergleich / Abrufe / Link-Jobs /
Beweisdokumente): 74/75, 79, 82-85, 87, 88, 90, 95, 116-120, 122, 123,
129-133 — in-process gegen eine Wegwerf-Datenbank (echtes Mongo).

- Vergleichseintrag erst nach der Fahrzeuguebernahme; Tempolimit erst nach
  der Adresspruefung
- Cache-Zeitstempel nach dem erfolgreichen Abruf; verlorene Lease merkt
  keinen Beweisstand vor
- Beweis-Worker: Claim je Beanspruchung, Aufraeumen nur gegen den gelesenen
  Stand, _gehalten ohne 500er-Deckel, geloeschte Inserate halten nicht
- Link-Jobs: Rueckstellung nur gegen den gelesenen Stand, Import-Fehlerpfad
  mit eigenem Claim, nur Sachtexte nach aussen, geteilter Job bucht beim
  Konto mit Kontingent
- Tageslimit: "Inserat weg" zaehlt, technischer Fehler nicht
- Provider-Slots: haengende Freigaben werden geheilt
- Alte Snapshots: Fahrzeug ist der Anker, Kollegen-IDs maskiert, no-store
- Produktionspruefung warnt ohne Konto-Tageslimit
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

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import beweis_service as BS  # noqa: E402
import deps  # noqa: E402
import link_jobs as LJ  # noqa: E402
import listing_identity as LI  # noqa: E402
import production_check as PC  # noqa: E402
import provider_fetch as PF  # noqa: E402
import provider_limiter as PL  # noqa: E402
import routes.listings as L  # noqa: E402

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"


def _jetzt():
    return datetime.now(timezone.utc)


@pytest.fixture
def wegwerf(monkeypatch):
    from motor.motor_asyncio import AsyncIOMotorClient
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    name = f"autoschnell_bc_{uuid.uuid4().hex[:10]}"
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


class _Sammlung:
    """Sammlung, deren find() zwischen Lesen und Weiterverarbeiten einen
    Eingriff erlaubt (Rennen Lesen -> Schreiben nachstellen)."""

    def __init__(self, echt, dazwischen):
        self._echt, self._dazwischen = echt, dazwischen

    def __getattr__(self, name):
        return getattr(self._echt, name)

    def find(self, *a, **k):
        cur = self._echt.find(*a, **k)
        dazwischen = self._dazwischen

        async def gen():
            async for d in cur:
                await dazwischen(d)
                yield d
        return gen()


class _Db:
    def __init__(self, echt, **ersatz):
        self._echt, self._ersatz = echt, ersatz

    def __getattr__(self, name):
        if name in self._ersatz:
            return self._ersatz[name]
        return getattr(self._echt, name)


def _job(uid="uA", did="d1", user_ids=None, **extra):
    n = uuid.uuid4().hex[:8]
    doc = {"id": f"job_{n}", "cache_key": f"kleinanzeigen:{n}", "source": "kleinanzeigen",
           "item_id": n, "url": f"https://www.kleinanzeigen.de/s-anzeige/x/{n}-216-1",
           "status": "queued", "active": True, "attempts": 0, "error": None,
           "requested_by_dealer": did, "requested_by_user": uid,
           "dealer_ids": [did], "user_ids": [uid] if user_ids is None else user_ids,
           "created_at": _jetzt(), "updated_at": _jetzt()}
    doc.update(extra)
    return doc


# ------------------------------------------------------------- Quelltext
def test_quelltext_block_c():
    q = inspect.getsource(L.compare)
    # 133: erst Adresse, dann Tempolimit; 74/75: erst Fahrzeug, dann Vergleich
    assert q.index("get_listing_identity(raw_url)") < q.index("_vergleich_limiter.check(")
    assert q.index("_vergleich_limiter.check(") < q.index("get_or_fetch_listing(")
    assert q.index("_fahrzeug_uebernehmen(") < q.index("vehicle_comparisons.insert_one(")
    # 87: Audit auch beim Quarantaene-Treffer
    assert inspect.getsource(L.listings_resolve).count('"inserat.aufgeloest"') == 2
    # 83/84: Firmen-Sperre und no-store bei alten Snapshots
    assert "Depends(_snapshot_nutzer)" in inspect.getsource(L.snapshot_status)
    dl = inspect.getsource(L.snapshot_download)
    assert "Depends(_snapshot_nutzer)" in dl and '"Cache-Control": "no-store"' in dl
    # 88: Live-Zaehler ohne Quelle = mobile
    lc = inspect.getsource(L.live_counter)
    assert "{quelle or 'mobile'}:{ad_id}" in lc and '"mobile_ad_id": ad_id' not in lc
    # 79/116: Zeitstempel nach dem Abruf; verlorene Lease ohne Vormerkung
    g = inspect.getsource(LI.get_or_fetch_listing)
    assert '"fetched_at": abruf_ende' in g and "abgerufen_am=abruf_ende" in g
    assert (g.index("res.matched_count == 0") < g.index("return data, False, None")
            < g.index("from beweis_service import beweis_vormerken"))
    # 119/120/122: Link-Jobs
    assert '"processing_until": j.get("processing_until")' in inspect.getsource(LJ._requeue_stale)
    p = inspect.getsource(LJ._process)
    assert p.index("eigener_claim = {") < p.index("from listing_identity import ListingBusy")
    assert '"error": FEHLER_TECHNISCH' in p and "fehlertext(exc)" in p
    assert "error_intern" not in inspect.getsource(L.listings_check_status)
    # 130: "Inserat weg" zaehlt
    f = inspect.getsource(PF.fetch_listing)
    assert f.index("except ListingGone:") < f.index("except AnbieterFehler")
    # 132: Freigabe-Zeitstempel und Heilung
    assert '"freigegeben_am"' in inspect.getsource(PL.release_slot)
    assert "FREIGABE_NACHLAUF_SEKUNDEN" in inspect.getsource(PL._heal_stale)
    # 131: Produktionspruefung
    assert "ANBIETER_TAGESLIMIT_JE_KONTO fehlt oder ist 0" in inspect.getsource(PC.pruefe_produktion)
    # 117/118/123: Beweis-Worker
    assert '"bearbeitung_claim": uuid.uuid4().hex' in inspect.getsource(BS._beanspruchen)
    assert '"bearbeitung_bis": d.get("bearbeitung_bis")' in inspect.getsource(BS._aufraeumen)
    assert "_produktion()" in inspect.getsource(BS.beweis_vormerken)
    for fn in (BS.beweis_erzeugen, BS._herzschlag, BS._bearbeiten):
        assert "_eigene_bearbeitung(doc)" in inspect.getsource(fn), fn.__name__


# ------------------------------------------------------------- 122
def test_fehlertext_nur_eigene_sachtexte():
    from kleinanzeigen_service import ListingGone
    assert LJ.fehlertext(ListingGone("Das Inserat ist weg")) == "Das Inserat ist weg"
    assert LJ.fehlertext(PF.TageslimitErreicht("Tageslimit erreicht")) == "Tageslimit erreicht"
    assert LJ.fehlertext(RuntimeError("HTTPSConnectionPool(host='api.apify.com')")) == LJ.FEHLER_TECHNISCH
    assert LJ.fehlertext(KeyError("data")) == LJ.FEHLER_TECHNISCH
    assert "api.apify.com" not in LJ.FEHLER_TECHNISCH


def test_linkjob_technischer_fehler_ohne_rohtext(wegwerf, monkeypatch):
    db, run = wegwerf.db, wegwerf.run

    async def kaputt(*a, **k):
        raise RuntimeError("HTTPSConnectionPool(host='geheim.intern', port=443)")
    monkeypatch.setattr(LI, "get_or_fetch_listing", kaputt)

    async def lauf():
        doc = _job()
        await db.link_jobs.insert_one(doc)
        job = await LJ._claim_one(db)
        await LJ._process(db, job)
        return await db.link_jobs.find_one({"id": doc["id"]}, {"_id": 0})

    d = run(lauf())
    assert d["status"] == "queued" and d["error"] == LJ.FEHLER_TECHNISCH
    assert "geheim.intern" in d["error_intern"] and "geheim.intern" not in d["error"]


# ------------------------------------------------------------- 129
def test_geteilter_job_bucht_beim_konto_mit_kontingent(wegwerf, monkeypatch):
    db, run = wegwerf.db, wegwerf.run
    versuche = []

    async def gof(db_, url, fetcher, ttl_hours=6):
        return await fetcher("kleinanzeigen", "1", url), False, None
    monkeypatch.setattr(LI, "get_or_fetch_listing", gof)

    async def fetch(db_, src, iid, url, dealer_id="", user_id=""):
        versuche.append((user_id, dealer_id))
        if user_id in ("uA", "uC"):
            raise PF.TageslimitErreicht(f"Tageslimit fuer neue Links erreicht ({user_id})")
        return {"mobile_ad_id": iid}
    monkeypatch.setattr(PF, "fetch_listing", fetch)

    async def lauf():
        await db.users.insert_one({"id": "uB", "dealer_id": "d2", "role": "sucher"})
        geteilt = _job(uid="uA", did="d1", user_ids=["uA", "uB"])
        await db.link_jobs.insert_one(geteilt)
        job = await LJ._claim_one(db)
        await LJ._process(db, job)
        erg1 = await db.link_jobs.find_one({"id": geteilt["id"]}, {"_id": 0})
        versuche.clear()
        alle_am_limit = _job(uid="uA", did="d1", user_ids=["uA", "uC"])
        await db.link_jobs.insert_one(alle_am_limit)
        job2 = await LJ._claim_one(db)
        await LJ._process(db, job2)
        erg2 = await db.link_jobs.find_one({"id": alle_am_limit["id"]}, {"_id": 0})
        return erg1, erg2

    erg1, erg2 = run(lauf())
    assert erg1["status"] == "completed", erg1
    assert erg2["status"] == "failed" and erg2["error"].startswith("Tageslimit"), erg2
    assert [v[0] for v in versuche] == ["uA", "uC"], "alle wartenden Konten versucht, dann endgueltig"


# ------------------------------------------------------------- 119
def test_requeue_stale_trifft_nur_den_gelesenen_stand(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    alt = _jetzt() - timedelta(minutes=5)

    async def neu_vergeben(d):
        # zwischen Lesen und Schreiben beansprucht ein anderer Worker den Job
        await db.link_jobs.update_one(
            {"id": d["id"]}, {"$set": {"claim_id": "neu", "processing_until": _jetzt() + timedelta(minutes=4)}})

    async def lauf():
        a = _job(status="processing", attempts=1, claim_id="alt", processing_until=alt)
        b = _job(status="processing", attempts=1, claim_id="alt", processing_until=alt)
        await db.link_jobs.insert_many([a, b])
        await LJ._requeue_stale(_Db(db, link_jobs=_Sammlung(db.link_jobs, neu_vergeben)))
        nach_rennen = await db.link_jobs.find_one({"id": a["id"]}, {"_id": 0})
        # ohne Eingriff: der abgelaufene Claim wird zurueckgestellt
        await db.link_jobs.update_one({"id": b["id"]}, {"$set": {"claim_id": "alt", "processing_until": alt}})
        await LJ._requeue_stale(db)
        normal = await db.link_jobs.find_one({"id": b["id"]}, {"_id": 0})
        return nach_rennen, normal

    nach_rennen, normal = run(lauf())
    assert nach_rennen["status"] == "processing" and nach_rennen["claim_id"] == "neu", nach_rennen
    assert normal["status"] == "queued" and "claim_id" not in normal, normal


# ------------------------------------------------------------- 117 / 118
def test_beweis_aufraeumen_trifft_nur_den_gelesenen_stand(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    alt = _jetzt() - timedelta(minutes=10)

    async def herzschlag(d):
        await db.inserat_beweise.update_one(
            {"id": d["id"]}, {"$set": {"bearbeitung_bis": _jetzt() + timedelta(minutes=5)}})

    async def lauf():
        await db.inserat_beweise.insert_many([
            {"id": "b1", "cache_key": "mobile:1", "status": "in_arbeit", "versuche": 1,
             "bearbeitung_bis": alt, "erstellt_am": alt},
            {"id": "b2", "cache_key": "mobile:2", "status": "in_arbeit", "versuche": 1,
             "bearbeitung_bis": alt, "erstellt_am": alt}])
        await BS._aufraeumen(_Db(db, inserat_beweise=_Sammlung(db.inserat_beweise, herzschlag)))
        lebt = await db.inserat_beweise.find_one({"id": "b1"}, {"_id": 0})
        await db.inserat_beweise.update_one({"id": "b2"}, {"$set": {"bearbeitung_bis": alt}})
        await BS._aufraeumen(db)
        frei = await db.inserat_beweise.find_one({"id": "b2"}, {"_id": 0})
        return lebt, frei

    lebt, frei = run(lauf())
    assert lebt["status"] == "in_arbeit", "Herzschlag dazwischen: Auftrag bleibt in Arbeit"
    assert frei["status"] == "offen"


def test_beweis_claim_je_beanspruchung(wegwerf, monkeypatch):
    db, run = wegwerf.db, wegwerf.run
    monkeypatch.setattr(BS, "HERZSCHLAG_SEKUNDEN", 0.05)

    async def kaputt(db_, d):
        raise RuntimeError("db-intern")
    monkeypatch.setattr(BS, "beweis_erzeugen", kaputt)

    async def lauf():
        await db.inserat_beweise.insert_one({"id": "c1", "cache_key": "mobile:c1", "status": "offen",
                                             "versuche": 0, "erstellt_am": _jetzt()})
        doc1 = await BS._beanspruchen(db)
        # Aufraeumen gibt frei, DERSELBE Prozess beansprucht erneut
        await db.inserat_beweise.update_one({"id": "c1"}, {"$set": {"status": "offen", "bearbeitung_bis": None}})
        doc2 = await BS._beanspruchen(db)
        t = asyncio.ensure_future(BS._herzschlag(db, doc1))
        await asyncio.sleep(0.25)
        alter_herzschlag_beendet = t.done()
        t.cancel()
        await BS._bearbeiten(db, doc1)           # ueberholter Versuch scheitert
        z = await db.inserat_beweise.find_one({"id": "c1"}, {"_id": 0})
        return doc1, doc2, alter_herzschlag_beendet, z

    doc1, doc2, beendet, z = run(lauf())
    assert doc1["bearbeiter"] == doc2["bearbeiter"] and doc1["bearbeitung_claim"] != doc2["bearbeitung_claim"]
    assert beendet, "alter Herzschlag endet, sobald die Zeile einem neuen Claim gehoert"
    assert z["status"] == "in_arbeit" and z["bearbeitung_claim"] == doc2["bearbeitung_claim"], z
    assert not z.get("fehler")


# ------------------------------------------------------------- 90 / 95
def test_gehalten_ohne_deckel_und_geloeschtes_inserat(wegwerf):
    db, run = wegwerf.db, wegwerf.run

    async def lauf():
        viele = [{"id": f"v{i}", "dealer_id": f"d{i}", "lifecycle": "verglichen",
                  "inserat_schluessel": "mobile:viele"} for i in range(500)]
        viele.append({"id": "v500", "dealer_id": "d500", "lifecycle": "bestand",
                      "inserat_schluessel": "mobile:viele"})
        await db.vehicles.insert_many(viele)
        await db.vehicles.insert_many([
            {"id": "vg", "dealer_id": "dg", "lifecycle": "verglichen", "inserat_schluessel": "mobile:weg"},
            {"id": "va", "dealer_id": "da", "lifecycle": "verglichen", "inserat_schluessel": "mobile:aktiv"}])
        await db.resale_listings.insert_many([
            {"id": "lg", "vehicle_id": "vg", "dealer_id": "dg", "status": "geloescht"},
            {"id": "la", "vehicle_id": "va", "dealer_id": "da", "status": "veroeffentlicht"}])
        return (await BS._gehalten(db, "mobile:viele"), await BS._gehalten(db, "mobile:weg"),
                await BS._gehalten(db, "mobile:aktiv"), await BS._gehalten(db, "mobile:nix"))

    viele, weg, aktiv, nix = run(lauf())
    assert viele is True, "der echte Vorgang in Fahrzeug Nr. 501 haelt"
    assert weg is False and aktiv is True and nix is False


# ------------------------------------------------------------- 79 / 116
def test_abrufzeit_nach_dem_abruf_und_verlorene_lease_ohne_vormerkung(wegwerf, monkeypatch):
    # Der verlorene Abruf darf nichts vormerken, der gewonnene schon. Geprueft
    # mit dem alten Verhalten (BEWEIS_AUTOMATISCH=true) — seit 18.09.2026
    # (Wunsch Ahmad) merkt der Abruf sonst gar nichts mehr vor, dann pruefte
    # dieser Test nichts.
    monkeypatch.setenv("BEWEIS_AUTOMATISCH", "true")
    db, run = wegwerf.db, wegwerf.run
    vorgemerkt = []

    async def vormerken(*a, **k):
        vorgemerkt.append(k.get("cache_key"))
        return None
    monkeypatch.setattr(BS, "beweis_vormerken", vormerken)
    url1 = "https://suchen.mobile.de/fahrzeuge/details.html?id=79001"
    url2 = "https://suchen.mobile.de/fahrzeuge/details.html?id=11601"

    async def langsam(src, iid, u):
        await asyncio.sleep(1.1)
        return {"mobile_ad_id": iid}

    async def stiehlt(src, iid, u):
        await db.listings_cache.update_one({"cache_key": f"{src}:{iid}"},
                                           {"$set": {"fetching_claim": "fremd"}})
        return {"mobile_ad_id": iid}

    async def lauf():
        start = _jetzt()
        await LI.get_or_fetch_listing(db, url1, langsam, ttl_hours=6)
        c = await db.listings_cache.find_one({"cache_key": "mobile:79001"}, {"_id": 0})
        daten, cached, _ = await LI.get_or_fetch_listing(db, url2, stiehlt, ttl_hours=6)
        return start, c, daten, cached

    start, c, daten, cached = run(lauf())
    fetched = c["fetched_at"].replace(tzinfo=timezone.utc)
    assert fetched >= start + timedelta(seconds=1), "fetched_at stempelt das Abruf-Ende"
    assert (c["expires_at"].replace(tzinfo=timezone.utc) - fetched) >= timedelta(hours=5, minutes=59)
    assert vorgemerkt == ["mobile:79001"], vorgemerkt
    assert daten == {"mobile_ad_id": "11601"} and cached is False


# ------------------------------------------------------------- 130
def test_inserat_weg_zaehlt_technischer_fehler_nicht(wegwerf, monkeypatch):
    from kleinanzeigen_service import ListingGone
    db, run = wegwerf.db, wegwerf.run
    monkeypatch.setattr(PF, "MOCK_PROVIDER_FETCH", False)
    tag = _jetzt().strftime("%Y-%m-%d")
    fehler = {"art": "weg"}

    async def abrufen(db_, source, item_id, url):
        if fehler["art"] == "weg":
            raise ListingGone("Das Inserat ist bei Kleinanzeigen nicht mehr vorhanden.")
        raise RuntimeError("Zeitueberschreitung")
    monkeypatch.setattr(PF, "_abrufen", abrufen)

    async def stand():
        d = await db.provider_budget.find_one({"_id": f"{tag}:konto:u1"})
        return (d or {}).get("n", 0)

    async def lauf():
        with pytest.raises(ListingGone):
            await PF.fetch_listing(db, "kleinanzeigen", "1", "https://www.kleinanzeigen.de/s-anzeige/x/1-216-1",
                                   dealer_id="d1", user_id="u1")
        nach_weg = await stand()
        fehler["art"] = "technisch"
        with pytest.raises(RuntimeError):
            await PF.fetch_listing(db, "kleinanzeigen", "1", "https://www.kleinanzeigen.de/s-anzeige/x/1-216-1",
                                   dealer_id="d1", user_id="u1")
        return nach_weg, await stand()

    nach_weg, nach_technisch = run(lauf())
    assert nach_weg == 1, "Inserat weg: der Abruf zaehlt"
    assert nach_technisch == 1, "technischer Fehler: zurueckgebucht"


# ------------------------------------------------------------- 132
def test_haengende_freigabe_wird_geheilt(wegwerf):
    db, run = wegwerf.db, wegwerf.run

    async def lauf():
        slot = None
        for _ in range(5):
            slot = await PL.acquire_slot(db, "mobile")
            if slot:
                break
        assert slot
        # Freigabe begann (freigegeben=True), Zaehler sank nie (Mongo weg)
        await db.provider_slots.update_one(
            {"id": slot}, {"$set": {"freigegeben": True,
                                    "freigegeben_am": _jetzt() - timedelta(seconds=PL.FREIGABE_NACHLAUF_SEKUNDEN + 5)}})
        await db.provider_limits.update_one({"provider": "mobile"}, {"$set": {"heal_bis": None}})
        await PL._heal_stale(db, "mobile")
        lim = await db.provider_limits.find_one({"provider": "mobile"}, {"_id": 0})
        rest = await db.provider_slots.count_documents({"provider": "mobile"})
        # eine FRISCHE Freigabe bleibt stehen (der Aufrufer zaehlt gleich ab)
        slot2 = None
        for _ in range(5):
            slot2 = await PL.acquire_slot(db, "mobile")
            if slot2:
                break
        await db.provider_slots.update_one({"id": slot2}, {"$set": {"freigegeben": True, "freigegeben_am": _jetzt()}})
        await db.provider_limits.update_one({"provider": "mobile"}, {"$set": {"heal_bis": None}})
        await PL._heal_stale(db, "mobile")
        frisch = await db.provider_slots.count_documents({"id": slot2})
        return lim, rest, frisch

    lim, rest, frisch = run(lauf())
    assert rest == 0 and lim["active"] == 0, (lim, rest)
    assert frisch == 1


# ------------------------------------------------------------- 82 / 85
def test_alte_snapshots_fahrzeug_ist_der_anker_und_kollegen_maskiert(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    a = {"id": "sa", "dealer_id": "d1", "role": "sucher"}
    b = {"id": "sb", "dealer_id": "d1", "role": "sucher"}
    jetzt = _jetzt().isoformat()

    def snap(sid, **extra):
        d = {"id": sid, "dealer_id": "d1", "status": "ready", "created_at": jetzt,
             "png_path": "x", "pdf_path": "y", "user_id": "sa", "vehicle_id": "v1"}
        d.update(extra)
        return d

    async def lauf():
        await db.vehicles.insert_one({"id": "v1", "dealer_id": "d1", "owner_user_id": "sa"})
        await db.listing_snapshots.insert_many([snap("s_v1"), snap("s_ohne", vehicle_id=None),
                                                snap("s_kollege", user_id="sb", vehicle_id="v1")])
        vorher = (await L._load_snapshot_or_404("s_v1", a))["id"]
        # Uebergabe des Fahrzeugs an B
        await db.vehicles.update_one({"id": "v1"}, {"$set": {"owner_user_id": "sb"}})
        with pytest.raises(HTTPException) as e:
            await L._load_snapshot_or_404("s_v1", a)
        nach_b = (await L._load_snapshot_or_404("s_v1", b))["id"]
        ohne = (await L._load_snapshot_or_404("s_ohne", a))["id"]
        liste_a = {s["id"] for s in await L.list_snapshots(None, a)}
        liste_b = await L.list_snapshots(None, b)
        status_b = await L.snapshot_status("s_v1", b)       # Snapshot des Kollegen A
        return vorher, e.value.status_code, nach_b, ohne, liste_a, liste_b, status_b

    vorher, status, nach_b, ohne, liste_a, liste_b, status_b = run(lauf())
    assert vorher == "s_v1" and status == 404 and nach_b == "s_v1" and ohne == "s_ohne"
    assert liste_a == {"s_ohne"}, "nach der Uebergabe haengt s_v1 am Fahrzeug von B"
    assert {s["id"] for s in liste_b} == {"s_v1", "s_kollege"}
    fremd = next(s for s in liste_b if s["id"] == "s_v1")
    assert "user_id" not in fremd and "user_id" in next(s for s in liste_b if s["id"] == "s_kollege")
    assert "user_id" not in status_b and "dealer_id" not in status_b and status_b["id"] == "s_v1"


# ------------------------------------------------------------- 131
def test_produktionspruefung_warnt_ohne_konto_tageslimit(monkeypatch, tmp_path):
    try:
        import test_befunde_runde13_g2 as g2
    except ImportError:
        pytest.skip("Vorlage der Produktionsumgebung nicht importierbar")
    for k, v in g2._PROD_UMGEBUNG.items():
        monkeypatch.setenv(k, v)
    for k in g2._PROD_LOESCHEN:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setenv("ANBIETER_TAGESLIMIT_JE_KONTO", "0")
    log = g2._Protokoll()
    PC.pruefe_produktion(log)
    assert any("ANBIETER_TAGESLIMIT_JE_KONTO" in t for t in log.texte("warning")), log.texte("warning")
    monkeypatch.setenv("ANBIETER_TAGESLIMIT_JE_KONTO", "400")
    log2 = g2._Protokoll()
    PC.pruefe_produktion(log2)
    assert not any("ANBIETER_TAGESLIMIT_JE_KONTO" in t for t in log2.texte("warning")), log2.texte("warning")
