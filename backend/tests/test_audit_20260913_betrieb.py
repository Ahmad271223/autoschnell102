# -*- coding: utf-8 -*-
"""Audit 13.09.2026 — Bereich Betrieb (Befunde #33 bis #42).

  #33 Provider-Limiter: Selbstheilung konnte den Zaehler zwischen "$inc" und
      "insert" eines Belegens zu tief setzen; der innere Scrape-Slot hatte
      keinen Herzschlag.
  #34 Limiter-Indexaufbau scheiterte still und wurde bei jedem Belegen
      wiederholt; /ready blieb nach einem Startfehler bis zum Neustart 503.
  #35 listings_cache wurde nach Ablauf nie geloescht (Verkaeuferangaben).
  #36 listings_cache.cache_key: Scheitern nur als Warnung.
  #37 Beweis-Unique-Index: fehlte er, nur Log bei jedem Vergleich.
  #38 Beweis-Worker startete nicht, wenn die Indizes scheiterten; kein Stau-
      Hinweis in /ready.
  #39/#40/#41 Teil-Unique-Indizes (Abo, Anfragen, Loesch-Nachholung): kein
      Alarm, Dublettenpruefung konnte den Start abbrechen.
  #42 TTL-Index vehicle_cache: falscher Text, kein Alarm.

In-Prozess gegen eine Wegwerf-DB je Test; kein Server, kein Import von
server.py (Verdrahtung dort per Quelltextpruefung).
"""
import asyncio
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MONGO_URL = "mongodb://127.0.0.1:27017"
WURZEL = Path(__file__).resolve().parents[2]


def _quelle(*teile) -> str:
    return WURZEL.joinpath(*teile).read_text(encoding="utf-8")


def _jetzt():
    return datetime.now(timezone.utc)


class _CollProxy:
    """Sammlung mit einzelnen ersetzten Methoden, Rest wie echt."""

    def __init__(self, echt, **ersatz):
        self._echt = echt
        for name, fn in ersatz.items():
            setattr(self, name, fn)

    def __getattr__(self, name):
        return getattr(self._echt, name)


class _DbProxy:
    """Datenbank mit einzelnen ersetzten Sammlungen. name/client bleiben
    gleich, damit die Merker (je Datenbank) dieselben sind."""

    def __init__(self, echt, **sammlungen):
        self._echt = echt
        self._sammlungen = sammlungen

    @property
    def name(self):
        return self._echt.name

    @property
    def client(self):
        return self._echt.client

    def __getattr__(self, name):
        if name in self._sammlungen:
            return self._sammlungen[name]
        return getattr(self._echt, name)

    def __getitem__(self, name):
        return self._sammlungen.get(name) or self._echt[name]


@pytest.fixture
def welt(monkeypatch):
    from motor.motor_asyncio import AsyncIOMotorClient
    import beweis_service as BS
    import provider_limiter as PL

    monkeypatch.setattr(PL, "_indexes_ready", set())
    monkeypatch.setattr(PL, "_fehlversuch", {}, raising=False)
    monkeypatch.setattr(BS, "_index_sicher", set())
    monkeypatch.setattr(BS, "_index_versuch", {}, raising=False)

    class _W:
        pass

    w = _W()
    w.PL, w.BS = PL, BS
    w.loop = asyncio.new_event_loop()
    asyncio.set_event_loop(w.loop)
    w.client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    w.db = w.client[f"autoschnell_wt_betrieb_{uuid.uuid4().hex[:8]}"]
    w.run = lambda coro: w.loop.run_until_complete(coro)

    def _alarm(typ, ref):
        return w.run(w.db.betriebsalarme.find_one({"typ": typ, "ref": ref, "offen": True}))
    w.alarm = _alarm
    yield w
    try:
        w.run(w.client.drop_database(w.db.name))
    finally:
        w.client.close()
        w.loop.close()


# ----------------------------------------------------------------- #33 ----
def test_33a_selbstheilung_waehrend_belegen_senkt_zaehler_nicht(welt):
    """Belegen A zaehlt hoch, fuegt den Slot aber erst danach ein; eine
    Selbstheilung dazwischen setzte den Zaehler auf die (zu kleine) Slot-Zahl."""
    w, PL, p = welt, welt.PL, "kleinanzeigen"
    grenze = PL.PROVIDER_MAX_CONCURRENT[p]

    async def lauf():
        await PL.ensure_slot_indexes(w.db)
        for _ in range(grenze - 1):
            assert await PL.acquire_slot(w.db, p)
        begonnen, weiter = asyncio.Event(), asyncio.Event()
        echt = w.db.provider_slots

        async def insert_langsam(doc, *a, **k):
            begonnen.set()
            await weiter.wait()
            return await echt.insert_one(doc, *a, **k)

        proxy = _DbProxy(w.db, provider_slots=_CollProxy(echt, insert_one=insert_langsam))
        a = asyncio.create_task(PL.acquire_slot(proxy, p))
        await asyncio.wait_for(begonnen.wait(), 5)
        await PL._heal_stale(w.db, p)
        weiter.set()
        slot = await a
        stand = (await w.db.provider_limits.find_one({"provider": p}))["active"]
        slots = await w.db.provider_slots.count_documents({"provider": p})
        return slot, stand, slots

    slot, stand, slots = w.run(lauf())
    assert slot
    assert stand == slots == grenze, f"Zaehler {stand}, Slots {slots}"


def test_33b_belegen_mitten_in_der_selbstheilung(welt):
    """Ein vollstaendiges Belegen zwischen "Slots zaehlen" und "Zaehler lesen"
    der Selbstheilung liess den Zaehler um eins zu tief zurueck."""
    w, PL, p = welt, welt.PL, "kleinanzeigen"
    grenze = PL.PROVIDER_MAX_CONCURRENT[p]

    async def lauf():
        await PL.ensure_slot_indexes(w.db)
        for _ in range(grenze - 1):
            assert await PL.acquire_slot(w.db, p)
        gezaehlt, weiter = asyncio.Event(), asyncio.Event()
        echt = w.db.provider_slots

        async def count_langsam(filt, *a, **k):
            n = await echt.count_documents(filt, *a, **k)
            gezaehlt.set()
            await weiter.wait()
            return n

        proxy = _DbProxy(w.db, provider_slots=_CollProxy(echt, count_documents=count_langsam))
        heilung = asyncio.create_task(PL._heal_stale(proxy, p))
        await asyncio.wait_for(gezaehlt.wait(), 5)
        slot = await PL.acquire_slot(w.db, p)
        weiter.set()
        await heilung
        stand = (await w.db.provider_limits.find_one({"provider": p}))["active"]
        slots = await w.db.provider_slots.count_documents({"provider": p})
        return slot, stand, slots

    slot, stand, slots = w.run(lauf())
    assert slot
    assert stand == slots == grenze, f"Zaehler {stand}, Slots {slots}"


def test_33c_scrape_slot_hat_herzschlag(welt, monkeypatch):
    """Ein eigener Abruf kann laenger dauern als die Slot-Lebensdauer."""
    import provider_fetch as PF
    w, PL = welt, welt.PL
    geschlagen = []

    async def extend(db, slot_id):
        geschlagen.append(slot_id)

    monkeypatch.setattr(PL, "extend_slot", extend)
    monkeypatch.setattr(PF, "SCRAPE_HERZSCHLAG_SEKUNDEN", 0.05, raising=False)

    async def holen(url):
        await asyncio.sleep(0.4)
        return {"ok": True}

    async def lauf():
        await PL.ensure_slot_indexes(w.db)
        erg = await PF._mit_scrape_bremse(w.db, True, holen, "https://example.org/x")
        stand = (await w.db.provider_limits.find_one({"provider": "kleinanzeigen"}))["active"]
        return erg, stand

    erg, stand = w.run(lauf())
    assert erg == {"ok": True}
    assert geschlagen, "kein Herzschlag fuer den Scrape-Slot"
    assert stand == 0, "Slot muss freigegeben sein"


def test_33d_viele_belegungen_mit_selbstheilung_halten_die_grenze(welt, monkeypatch):
    """Stresstest: Belegen/Freigeben mit erzwungener Selbstheilung — nie mehr
    gleichzeitig belegte Slots als erlaubt, am Ende Zaehler 0."""
    w, PL, p = welt, welt.PL, "kleinanzeigen"
    grenze = PL.PROVIDER_MAX_CONCURRENT[p]
    monkeypatch.setattr(PL, "HEAL_INTERVAL_SECONDS", 0)
    belegt = {"jetzt": 0, "max": 0}

    async def arbeiter():
        for _ in range(8):
            slot = await PL.acquire_slot(w.db, p)
            if not slot:
                await asyncio.sleep(0.01)
                continue
            belegt["jetzt"] += 1
            belegt["max"] = max(belegt["max"], belegt["jetzt"])
            await asyncio.sleep(0.005)
            belegt["jetzt"] -= 1
            await PL.release_slot(w.db, slot)

    async def lauf():
        await PL.ensure_slot_indexes(w.db)
        await asyncio.gather(*[arbeiter() for _ in range(grenze * 5)])
        return (await w.db.provider_limits.find_one({"provider": p}))["active"]

    stand = w.run(lauf())
    assert belegt["max"] <= grenze, belegt
    assert stand == 0


# ----------------------------------------------------------------- #34 ----
def test_34a_doppelte_zaehler_werden_automatisch_repariert(welt):
    w, PL, p = welt, welt.PL, "kleinanzeigen"
    grenze = PL.PROVIDER_MAX_CONCURRENT[p]

    async def lauf():
        await w.db.provider_limits.insert_many(
            [{"provider": p, "active": 0}, {"provider": p, "active": 0}])
        slots = await asyncio.gather(*[PL.acquire_slot(w.db, p) for _ in range(grenze * 5)])
        zaehler = await w.db.provider_limits.count_documents({"provider": p})
        idx = await w.db.provider_limits.index_information()
        return [s for s in slots if s], zaehler, idx

    vergeben, zaehler, idx = w.run(lauf())
    assert zaehler == 1, f"{zaehler} Zaehler-Dokumente"
    assert any(i.get("unique") and i["key"] == [("provider", 1)] for i in idx.values()), idx
    assert len(vergeben) <= grenze
    assert w.alarm("anbieter_grenze_index_fehlt", "provider_limits") is None


def test_34b_indexfehler_wird_gemeldet_und_gedrosselt(welt, monkeypatch):
    w, PL, p = welt, welt.PL, "kleinanzeigen"
    aufrufe, warnungen = [], []
    echt_ensure = PL.ensure_slot_indexes

    async def zaehlend(db):
        aufrufe.append(1)
        return await echt_ensure(db)

    class _Log:
        def warning(self, *a, **k):
            warnungen.append(a)

        def __getattr__(self, name):
            return lambda *a, **k: None

    monkeypatch.setattr(PL, "ensure_slot_indexes", zaehlend)
    monkeypatch.setattr(PL, "log", _Log(), raising=False)

    async def lauf():
        # Konflikt: gleicher Name, aber nicht eindeutig (z.B. von Hand angelegt)
        await w.db.provider_limits.create_index("provider", name="uniq_provider")
        for _ in range(50):
            slot = await PL.acquire_slot(w.db, p)
            await PL.release_slot(w.db, slot)

    w.run(lauf())
    assert len(aufrufe) <= 1, f"{len(aufrufe)} Aufbauversuche statt hoechstens einem"
    assert warnungen, "Scheitern muss protokolliert werden"
    assert w.alarm("anbieter_grenze_index_fehlt", "provider_limits"), "Betriebsalarm fehlt"


def test_34d_dublettenreparatur_ueberschreibt_belegen_nicht(welt):
    """Nachbesserung: ein Belegen zwischen "Slots zaehlen" und "Stand setzen"
    der Dublettenreparatur liess den Zaehler eins zu tief zurueck."""
    w, PL, p = welt, welt.PL, "kleinanzeigen"

    async def lauf():
        await w.db.provider_limits.insert_many(
            [{"provider": p, "active": 0}, {"provider": p, "active": 0}])
        ids = sorted([d["_id"] for d in await w.db.provider_limits.find({}).to_list(5)], key=str)
        behalten = ids[0]
        echt = w.db.provider_slots

        async def count_mit_belegen(filt, *a, **k):
            n = await echt.count_documents(filt, *a, **k)
            await w.db.provider_limits.update_one(
                {"_id": behalten}, {"$inc": {"active": 1, "rev": 1}})
            await echt.insert_one({"provider": p, "slot": "34d",
                                   "expires_at": _jetzt() + timedelta(minutes=5)})
            return n

        proxy = _DbProxy(w.db, provider_slots=_CollProxy(echt, count_documents=count_mit_belegen))
        await PL._zaehler_dubletten_bereinigen(proxy)
        stand = (await w.db.provider_limits.find_one({"_id": behalten}))["active"]
        slots = await w.db.provider_slots.count_documents({"provider": p})
        return stand, slots

    stand, slots = w.run(lauf())
    assert stand == slots == 1, f"Zaehler {stand}, Slots {slots}"


def test_34c_ready_holt_die_anbieter_grenze_nach():
    s = _quelle("backend", "server.py")
    block = s[s.index("kritische_indizes = {"):s.index("bereit = not fehler")]
    nachholen = block.index("ensure_slot_indexes(db)")
    assert nachholen < block.index("_TEILE = {"), "vor der Pruefung der Start-Merker"
    assert 'BETRIEBSBEREIT["anbieter_grenze"] = True' in block


# ----------------------------------------------------------------- #35 ----
def test_35_abgelaufener_inseratscache_wird_geloescht(welt):
    import cleanup_service as CS
    w = welt
    jetzt = _jetzt()
    tag = timedelta(days=1)

    def eintrag(k, **felder):
        return {"cache_key": f"kleinanzeigen:{k}", "source": "kleinanzeigen",
                "item_id": str(k), **felder}

    daten = {"seller_name": "Max Privat", "seller_phone": "0170 1234567"}

    async def lauf():
        await w.db.listings_cache.insert_many([
            eintrag(1, data=daten, expires_at=jetzt - 30 * tag, fetching_until=None,
                    created_at=jetzt - 120 * tag),
            eintrag(2, data=daten, expires_at=jetzt + 10 * tag, created_at=jetzt),
            eintrag(3, data=daten, expires_at=jetzt - 30 * tag,
                    fetching_until=jetzt + timedelta(seconds=60), created_at=jetzt - 120 * tag),
            eintrag(4, created_at=jetzt - 2 * tag, fetching_until=jetzt - timedelta(hours=1)),
            eintrag(5, data=daten, expires_at=jetzt - timedelta(minutes=1), created_at=jetzt),
            eintrag(6, created_at=jetzt - timedelta(minutes=10),
                    fetching_until=jetzt + timedelta(seconds=60)),
            eintrag(7, data=daten, expires_at=jetzt - 30 * tag, created_at=jetzt - 120 * tag),
        ])
        # Altbestand-Beweis ohne eingefrorene Daten liest noch aus dem Cache
        await w.db.inserat_beweise.insert_one(
            {"id": "b7", "cache_key": "kleinanzeigen:7", "status": "offen",
             "erstellt_am": jetzt - 30 * tag})
        await w.db.vehicle_cache.insert_many([
            {"mobile_ad_id": "alt", "expires_at_dt": jetzt - 2 * tag},
            {"mobile_ad_id": "neu", "expires_at_dt": jetzt + timedelta(minutes=10)}])
        n = await CS.inseratscache_rotieren(w.db, jetzt)
        rest = {d["item_id"] for d in await w.db.listings_cache.find({}, {"item_id": 1}).to_list(50)}
        fz = {d["mobile_ad_id"] for d in await w.db.vehicle_cache.find({}).to_list(50)}
        return n, rest, fz

    n, rest, fz = w.run(lauf())
    assert rest == {"2", "3", "5", "6", "7"}, rest
    assert fz == {"neu"}, fz
    assert n == 3
    quelle = _quelle("backend", "cleanup_service.py")
    block = quelle[quelle.index("async def _cleanup_once("):quelle.index("async def _cleanup_once(") + 9000]
    assert "inseratscache_rotieren(db, now)" in block, "im stuendlichen Lauf verdrahtet"


def test_35b_altbestand_mit_jahresablauf_wird_nach_abruf_geloescht(welt, monkeypatch):
    """Nachbesserung #35: Eintraege aus der Zeit vor ce63a99 tragen
    expires_at = Abruf + 1 Jahr. Massgeblich ist der Abruf plus TTL plus
    Karenz, hoechstens aber 90 Tage (Datenschutzerklaerung: "max. 90 Tage");
    Lease und Altbestand-Beweis schuetzen weiterhin."""
    import cleanup_service as CS
    w = welt
    monkeypatch.setattr(CS, "LISTING_CACHE_TTL_HOURS", 2160)
    jetzt = _jetzt()
    tag = timedelta(days=1)
    daten = {"seller_name": "Max Privat", "seller_phone": "0170 1234567"}

    def eintrag(k, abruf_tage, **felder):
        abruf = jetzt - abruf_tage * tag
        return {"cache_key": f"kleinanzeigen:35b{k}", "source": "kleinanzeigen",
                "item_id": f"35b{k}", "data": daten, "fetched_at": abruf,
                "expires_at": abruf + 365 * tag, "created_at": abruf, **felder}

    async def lauf():
        await w.db.listings_cache.insert_many([
            eintrag("alt", 120),                                          # weg
            eintrag("jung", 30),                                          # bleibt
            eintrag("ueber90", 95),                                       # weg: max. 90 Tage
            eintrag("grenze", 89),                                        # bleibt
            eintrag("lease", 120, fetching_until=jetzt + timedelta(seconds=60)),
            eintrag("beweis", 120),
        ])
        await w.db.inserat_beweise.insert_one(
            {"id": "b35b", "cache_key": "kleinanzeigen:35bbeweis", "status": "offen",
             "erstellt_am": jetzt - 120 * tag})
        n = await CS.inseratscache_rotieren(w.db, jetzt)
        rest = {d["item_id"] for d in await w.db.listings_cache.find({}, {"item_id": 1}).to_list(50)}
        return n, rest

    n, rest = w.run(lauf())
    assert rest == {"35bjung", "35bgrenze", "35blease", "35bbeweis"}, rest
    assert n == 2
    # Auch mit laengerer TTL bleibt es bei hoechstens 90 Tagen.
    monkeypatch.setattr(CS, "LISTING_CACHE_TTL_HOURS", 24 * 365)

    async def lauf_lang():
        await w.db.listings_cache.insert_one(eintrag("lang", 91))
        await CS.inseratscache_rotieren(w.db, jetzt)
        return await w.db.listings_cache.count_documents({"item_id": "35blang"})

    assert w.run(lauf_lang()) == 0
    s = _quelle("backend", "server.py")
    alle = s[s.index("async def _alle_indexe():"):s.index("async def run_abgleich_forever(")]
    assert 'create_index("fetched_at", name="cache_abruf")' in alle


# ----------------------------------------------------------------- #36 ----
def test_36a_cache_dubletten_werden_zusammengelegt(welt):
    import indizes as IX
    w = welt
    jetzt = _jetzt()

    async def lauf():
        basis = {"cache_key": "kleinanzeigen:36", "source": "kleinanzeigen", "item_id": "36"}
        await w.db.listings_cache.insert_many([
            dict(basis, created_at=jetzt),                                   # Lease-Rest
            dict(basis, data={"v": "alt"}, fetched_at=jetzt - timedelta(days=1)),
            dict(basis, data={"v": "neu"}, fetched_at=jetzt),
            {"cache_key": "kleinanzeigen:37", "source": "kleinanzeigen", "item_id": "37",
             "data": {"v": 1}},
        ])
        ok = await IX.listings_cache_unique_index(w.db)
        ok2 = await IX.listings_cache_unique_index(w.db)          # idempotent
        docs = await w.db.listings_cache.find({"cache_key": "kleinanzeigen:36"}).to_list(10)
        idx = await w.db.listings_cache.index_information()
        return ok, ok2, docs, idx

    ok, ok2, docs, idx = w.run(lauf())
    assert ok and ok2
    assert len(docs) == 1 and docs[0]["data"] == {"v": "neu"}, docs
    assert any(i.get("unique") and i["key"] == [("cache_key", 1)] for i in idx.values())


def test_36b_scheitern_meldet_alarm_und_heilt(welt):
    import indizes as IX
    w = welt
    ref = "listings_cache.cache_key"

    async def lauf(schritt):
        if schritt == 1:
            await w.db.listings_cache.create_index("cache_key")          # nicht eindeutig
        else:
            await w.db.listings_cache.drop_index("cache_key_1")
        return await IX.listings_cache_unique_index(w.db)

    assert w.run(lauf(1)) is False
    assert w.alarm("unique_index_fehlt", ref), "Alarm fehlt"
    assert w.run(lauf(2)) is True
    assert w.alarm("unique_index_fehlt", ref) is None, "Alarm muss geschlossen sein"


def test_36c_server_verdrahtet_cache_index_mit_alarm():
    s = _quelle("backend", "server.py")
    ensure = s[s.index("async def ensure_indexes():"):s.index("async def on_start(")]
    assert "listings_cache_unique_index(db)" in ensure
    assert 'db.listings_cache.create_index("cache_key", unique=True)' not in ensure
    alle = s[s.index("async def _alle_indexe():"):s.index("async def run_abgleich_forever(")]
    assert "await listings_cache_indizes(db)" in alle
    assert "ensure_cache_indexes(db)" not in alle, "nur ueber den Helfer mit Alarm"


def test_36d_cache_indizes_alarm_wird_bei_erfolg_geschlossen(welt, monkeypatch):
    """Nachbesserung #36: ein einmaliges Scheitern (z.B. Primaerwechsel beim
    Start) liess den Alarm listings_cache.indizes fuer immer offen."""
    import indizes as IX
    import listing_identity as LI
    w = welt
    ref = "listings_cache.indizes"
    echt = LI.ensure_cache_indexes

    async def kaputt(db):
        raise RuntimeError("Primaerwechsel")

    monkeypatch.setattr(LI, "ensure_cache_indexes", kaputt)
    assert w.run(IX.listings_cache_indizes(w.db)) is False
    assert w.alarm("unique_index_fehlt", ref), "Alarm fehlt"
    monkeypatch.setattr(LI, "ensure_cache_indexes", echt)
    assert w.run(IX.listings_cache_indizes(w.db)) is True
    assert w.alarm("unique_index_fehlt", ref) is None, "Alarm muss geschlossen sein"
    idx = w.run(w.db.listings_cache.index_information())
    assert idx.get("uniq_source_item", {}).get("unique") is True


# ----------------------------------------------------------------- #37 ----
def _vormerken(w, db, ck):
    return w.run(w.BS.beweis_vormerken(
        db, cache_key=ck, quelle="kleinanzeigen", item_id=ck.split(":")[-1],
        url=f"https://www.kleinanzeigen.de/s-anzeige/test/{ck.split(':')[-1]}", anlass="test"))


def test_37a_fehlender_beweis_index_alarm_drossel_und_heilung(welt):
    w, BS = welt, welt.BS
    ck = "kleinanzeigen:37001"
    ref = "inserat_beweise.cache_key"
    aufrufe = []
    echt = w.db.inserat_beweise

    async def ci(keys, *a, **k):
        aufrufe.append(k.get("name") or keys)
        return await echt.create_index(keys, *a, **k)

    proxy = _DbProxy(w.db, inserat_beweise=_CollProxy(echt, create_index=ci))

    async def vorbereiten():
        await BS.ensure_beweis_indexes(w.db)
        await w.db.inserat_beweise.drop_index("beweis_je_inserat")
        await w.db.inserat_beweise.insert_many([
            {"id": str(uuid.uuid4()), "cache_key": ck, "status": "fertig", "erstellt_am": _jetzt()},
            {"id": str(uuid.uuid4()), "cache_key": ck, "status": "fertig", "erstellt_am": _jetzt()}])

    w.run(vorbereiten())
    for _ in range(25):
        _vormerken(w, proxy, ck)
    versuche = [a for a in aufrufe if a == "beweis_je_inserat"]
    assert len(versuche) <= 1, f"{len(versuche)} Aufbauversuche statt hoechstens einem"
    offen = w.alarm("unique_index_fehlt", ref)
    assert offen and offen.get("anzahl", 0) >= 1, "Betriebsalarm fehlt"
    assert not BS._index_sicher

    # Dublette entfernt, Drosselfenster vorbei: Index steht, Alarm zu.
    w.run(w.db.inserat_beweise.delete_one({"cache_key": ck}))
    BS._index_versuch.clear()
    _vormerken(w, proxy, ck)
    idx = w.run(w.db.inserat_beweise.index_information())
    assert idx.get("beweis_je_inserat", {}).get("unique") is True
    assert w.alarm("unique_index_fehlt", ref) is None
    assert BS._index_sicher


def test_37b_nur_nebenindex_scheitert_kein_unique_alarm(welt):
    w, BS = welt, welt.BS

    async def vorbereiten():
        # gleicher Name wie fahrzeug_inserat, aber ohne sparse -> Konflikt
        await w.db.vehicles.create_index("inserat_schluessel", name="fahrzeug_inserat")

    w.run(vorbereiten())
    doc = _vormerken(w, w.db, "kleinanzeigen:37002")
    assert doc and doc.get("status") == "offen"
    assert BS._index_sicher, "der Unique-Index steht — Merker muss gesetzt sein"
    assert w.alarm("unique_index_fehlt", "inserat_beweise.cache_key") is None


def test_37c_server_meldet_fehlenden_beweis_index():
    s = _quelle("backend", "server.py")
    alle = s[s.index("async def _alle_indexe():"):s.index("async def run_abgleich_forever(")]
    assert "beweis_indizes_sichern(db)" in alle


# ----------------------------------------------------------------- #38 ----
def test_38a_beweis_stau_wird_gezaehlt(welt):
    w, BS = welt, welt.BS
    jetzt = _jetzt()

    async def lauf():
        await w.db.inserat_beweise.insert_many([
            {"id": "a", "cache_key": "k:a", "status": "offen",
             "erstellt_am": jetzt - timedelta(minutes=20)},
            {"id": "b", "cache_key": "k:b", "status": "offen",
             "erstellt_am": jetzt - timedelta(minutes=20),
             "naechster_versuch_ab": jetzt + timedelta(minutes=10)},
            {"id": "c", "cache_key": "k:c", "status": "offen",
             "erstellt_am": jetzt - timedelta(minutes=1)},
            {"id": "d", "cache_key": "k:d", "status": "fertig",
             "erstellt_am": jetzt - timedelta(days=1)},
            {"id": "e", "cache_key": "k:e", "status": "offen",
             "erstellt_am": jetzt - timedelta(minutes=30), "naechster_versuch_ab": None},
        ])
        return await BS.beweise_haengend(w.db, 15)

    assert w.run(lauf()) == 2


def test_38b_worker_startet_auch_ohne_indizes_und_ready_warnt():
    s = _quelle("backend", "server.py")
    start = s.index("from beweis_service import beweis_indizes_sichern, run_beweis_worker_forever")
    block = s[start:s.index("run_cleanup_forever(db)", start)]
    index = block.index("await beweis_indizes_sichern(db)")
    worker = block.index("asyncio.create_task(run_beweis_worker_forever(db))")
    assert "except Exception" in block[index:worker], \
        "ein Indexfehler darf den Worker-Start nicht ueberspringen"
    ready = s[s.index("async def readiness_check("):s.index("kritische_indizes = {")]
    assert "beweise_haengend" in ready and "Beweisdokumente warten > 15 min" in ready
    # Warnung, kein Fehler
    stau = ready[ready.index("beweise_haengend"):]
    assert "warnungen.append" in stau[:600] and "fehler.append" not in stau[:600]


# ----------------------------------------------------------------- #39 ----
def test_39a_abo_index_alarm_und_schliessen(welt):
    import indizes as IX
    w = welt
    ref = "subscriptions.ein_aktives_abo_je_konto"

    async def lauf(schritt):
        if schritt == 1:
            await w.db.subscriptions.insert_many([
                {"id": "s1", "subject_user_id": "u39", "status": "active"},
                {"id": "s2", "subject_user_id": "u39", "status": "active"}])
        else:
            await w.db.subscriptions.update_one({"id": "s1"}, {"$set": {"status": "ersetzt"}})
        return await IX.abo_unique_index(w.db)

    assert w.run(lauf(1)) is False
    assert w.alarm("mehrfache_aktive_abos", "subscriptions")
    assert w.alarm("unique_index_fehlt", ref)
    assert w.run(lauf(2)) is True
    assert w.alarm("mehrfache_aktive_abos", "subscriptions") is None
    assert w.alarm("unique_index_fehlt", ref) is None
    idx = w.run(w.db.subscriptions.index_information())
    assert idx["ein_aktives_abo_je_konto"].get("unique") is True


def test_39b_abo_index_anderer_fehler_und_werfende_pruefung(welt):
    import indizes as IX
    w = welt
    ref = "subscriptions.ein_aktives_abo_je_konto"

    async def lauf():
        await w.db.subscriptions.create_index(
            [("subject_user_id", 1)], unique=True, name="ein_aktives_abo_je_konto",
            partialFilterExpression={"status": "active"})
        erg = await IX.abo_unique_index(w.db)

        echt = w.db.subscriptions

        async def ci(*a, **k):
            raise RuntimeError("Primaerwechsel")

        def agg(*a, **k):
            raise RuntimeError("Primaerwechsel")

        proxy = _DbProxy(w.db, subscriptions=_CollProxy(echt, create_index=ci, aggregate=agg))
        erg2 = await IX.abo_unique_index(proxy)       # darf nicht werfen
        return erg, erg2

    erg, erg2 = w.run(lauf())
    assert erg is False and erg2 is False
    assert w.alarm("unique_index_fehlt", ref), "auch ohne Dubletten ein Alarm"


def test_39c_server_nutzt_abo_helfer():
    s = _quelle("backend", "server.py")
    alle = s[s.index("async def _alle_indexe():"):s.index("async def run_abgleich_forever(")]
    assert "abo_unique_index(db)" in alle
    assert "doppelte = await db.subscriptions.aggregate" not in alle


# ----------------------------------------------------------------- #40 ----
def test_40_offene_anfragen_alarm_und_schliessen(welt):
    import indizes as IX
    w = welt
    ref = "plan_requests.uniq_offene_sucher_abo_anfrage"

    async def lauf(schritt):
        if schritt == 1:
            await w.db.plan_requests.insert_many([
                {"id": "r1", "type": "sucher_abo", "subject_user_id": "s1", "status": "offen"},
                {"id": "r2", "type": "sucher_abo", "subject_user_id": "s1", "status": "offen"}])
        else:
            await w.db.plan_requests.update_one({"id": "r1"}, {"$set": {"status": "erledigt"}})
        await IX.plan_requests_unique_indizes(w.db)
        return await w.db.plan_requests.index_information()

    idx = w.run(lauf(1))
    assert w.alarm("unique_index_fehlt", ref), "Alarm fehlt"
    assert "uniq_offene_verkaufspaket_anfrage" in idx, "der zweite Index laeuft trotzdem"
    idx = w.run(lauf(2))
    assert "uniq_offene_sucher_abo_anfrage" in idx
    assert w.alarm("unique_index_fehlt", ref) is None
    s = _quelle("backend", "server.py")
    assert "await plan_requests_unique_indizes(db)" in s


# ----------------------------------------------------------------- #41 ----
def test_41a_loesch_nachholung_index_alarm(welt):
    import indizes as IX
    w = welt
    ref = "storage_delete_retry.retry_je_ziel"

    async def lauf(schritt):
        if schritt == 1:
            await w.db.storage_delete_retry.create_index([("id", 1)], name="retry_je_ziel")
        else:
            await w.db.storage_delete_retry.drop_index("retry_je_ziel")
        await IX.storage_retry_unique_index(w.db)
        return await w.db.storage_delete_retry.index_information()

    w.run(lauf(1))
    assert w.alarm("unique_index_fehlt", ref), "Alarm fehlt"
    idx = w.run(lauf(2))
    assert idx["retry_je_ziel"].get("unique") is True
    assert idx["retry_je_ziel"]["key"] == [("art", 1), ("key", 1), ("prefix", 1)]
    assert w.alarm("unique_index_fehlt", ref) is None
    s = _quelle("backend", "server.py")
    assert "await storage_retry_unique_index(db)" in s


def test_41b_gleich_alte_dubletten_bleiben_genau_einmal(welt):
    import indizes as IX
    w = welt

    async def lauf():
        zeit = "2026-09-01T00:00:00+00:00"
        await w.db.storage_delete_retry.insert_many([
            {"id": str(uuid.uuid4()), "art": "key", "key": "x/41.jpg", "prefix": None,
             "created_at": zeit} for _ in range(4)])
        await asyncio.gather(IX.storage_retry_unique_index(w.db),
                             IX.storage_retry_unique_index(w.db))
        return await w.db.storage_delete_retry.count_documents({"key": "x/41.jpg"})

    assert w.run(lauf()) == 1
    # Ein lokaler mongod liefert gleich alte Zeilen meist ohnehin in derselben
    # Reihenfolge — der Lauf allein beweist die _id-Stufe nicht.
    import inspect
    assert '{"$sort": {"created_at": 1, "_id": 1}}' in inspect.getsource(
        IX.storage_retry_unique_index)


# ----------------------------------------------------------------- #42 ----
def test_42_ttl_index_alarm_und_heilung(welt):
    import indizes as IX
    w = welt
    ref = "vehicle_cache.expires_at_dt"

    async def lauf(schritt):
        if schritt == 1:
            await w.db.vehicle_cache.create_index("expires_at_dt")        # ohne TTL
        else:
            await w.db.vehicle_cache.drop_index("expires_at_dt_1")
        ok = await IX.ttl_index_sicher(w.db, "vehicle_cache", "expires_at_dt")
        return ok, await w.db.vehicle_cache.index_information()

    ok, _ = w.run(lauf(1))
    assert ok is False
    assert w.alarm("ttl_index_fehlt", ref), "Alarm fehlt"
    ok, idx = w.run(lauf(2))
    assert ok is True and idx["expires_at_dt_1"].get("expireAfterSeconds") == 0
    assert w.alarm("ttl_index_fehlt", ref) is None
    s = _quelle("backend", "server.py")
    ensure = s[s.index("async def ensure_indexes():"):s.index("async def on_start(")]
    assert 'ttl_index_sicher(db, "vehicle_cache", "expires_at_dt")' in ensure
    assert 'ttl_index_sicher(db, "vehicle_comparisons", "expires_at_dt")' in ensure
    assert "Eindeutigkeits-Garantie fehlt! %s\", exc)\n    # Vehicle comparisons" not in ensure
