# -*- coding: utf-8 -*-
"""Runde 29 (12.09.2026, Pruefbefunde) — funf Lecks bzw. echte Fehler:

  1. Fahrzeugakte: Die Vergleichsliste lief nur ueber mobile_ad_id +
     dealer_id. Ein Sucher sah damit, wann seine KOLLEGEN dasselbe Auto
     verglichen haben.
  2. Fahrzeugakte: Die Mitbearbeiter-Namen gingen an jeden Sucher.
  3. Client-Quarantaene: Der Datensatz traegt, wer ihn eingereicht hat
     (ingested_by_user). Beim Lesen sah das jeder in der Firma.
  4. Einkaufspreis: Ohne offenen Vorgang fiel der Code auf ALLE Vorgaenge
     zurueck — ein stornierter Kauf erschien als "Vertragspreis". Ausserdem
     deckelten to_list(200)/to_list(500) die Entscheidungsgrundlage.
  5. Datei-Nachholung: Liess sich der Verweis in der Datenbank nicht
     bereinigen, wurde die Vormerkung trotzdem geloescht — die Datei war
     weg, der Verweis blieb fuer immer stehen.
  6. Registrierung: Firmen- und Fahrer-Registrierung teilten sich denselben
     Zaehler (Name "registrierung").

In-Prozess gegen eine Wegwerf-DB; kein Server.
"""
import asyncio
import importlib
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MONGO_URL = "mongodb://127.0.0.1:27017"


def _jetzt():
    return datetime.now(timezone.utc).isoformat()


def _modul(name):
    return importlib.import_module(name)


@pytest.fixture
def welt():
    from motor.motor_asyncio import AsyncIOMotorClient
    s = uuid.uuid4().hex[:10]
    namen = ["deps", "routes.bestand", "routes.contracts", "routes.listings",
             "routes.appointments", "kaufvorgang", "lifecycle",
             "rate_limiter", "routes.team"]
    mods = [_modul(n) for n in namen]
    alt = [(m, getattr(m, "db", None)) for m in mods]

    class _W:
        pass

    w = _W()
    w.s = s
    w.dealer_id = f"d_r29_{s}"
    w.chef = {"id": f"chef_r29_{s}", "dealer_id": w.dealer_id, "role": "dealer"}
    w.a = {"id": f"sa_r29_{s}", "dealer_id": w.dealer_id, "role": "sucher"}
    w.b = {"id": f"sb_r29_{s}", "dealer_id": w.dealer_id, "role": "sucher"}
    w.loop = asyncio.new_event_loop()
    asyncio.set_event_loop(w.loop)
    w.client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    w.db_name = f"autoschnell_r29_{s}"
    w.db = w.client[w.db_name]
    for m in mods:
        if hasattr(m, "db"):
            m.db = w.db
    w.run = lambda coro: w.loop.run_until_complete(coro)
    w.run(w.db.users.insert_many([
        {**w.chef, "active": True, "created_at": _jetzt(),
         "first_name": "Chef", "last_name": "C"},
        {**w.a, "active": True, "created_at": _jetzt(),
         "first_name": "Anna", "last_name": "A"},
        {**w.b, "active": True, "created_at": _jetzt(),
         "first_name": "Ben", "last_name": "B"}]))
    w.run(w.db.dealers.insert_one({"id": w.dealer_id, "user_id": w.chef["id"],
                                   "company_name": "Firma R29 GmbH",
                                   "created_at": _jetzt()}))
    yield w
    try:
        w.run(w.client.drop_database(w.db_name))
    finally:
        for m, d in alt:
            if d is not None:
                m.db = d
        w.client.close()
        w.loop.close()


def _fahrzeug(w, vid, ad_id):
    return {"id": vid, "dealer_id": w.dealer_id, "owner_user_id": w.a["id"],
            "mitbearbeiter_ids": [w.b["id"]], "mobile_ad_id": ad_id,
            "lifecycle": "verglichen", "make": "VW", "model": "Golf",
            "created_at": _jetzt(), "updated_at": _jetzt()}


def _vergleich(w, user_id, ad_id, quelle="mobile"):
    return {"id": f"vc_{uuid.uuid4().hex[:8]}", "dealer_id": w.dealer_id,
            "user_id": user_id, "mobile_ad_id": ad_id,
            "cache_key": f"{quelle}:{ad_id}", "source": quelle,
            "created_at": datetime.now(timezone.utc)}


# --------------------------------------------------------------- Akte
def test_01_akte_zeigt_sucher_nur_die_eigenen_vergleiche(welt):
    """Befund 1: Sucher A sah in der Akte auch die Vergleiche von Kollege B."""
    w = welt
    B = _modul("routes.bestand")
    vid, ad = f"v_r29_{w.s}", f"ad{w.s}"

    async def lauf():
        await w.db.vehicles.insert_one(_fahrzeug(w, vid, ad))
        await w.db.vehicle_comparisons.insert_many([
            _vergleich(w, w.a["id"], ad), _vergleich(w, w.a["id"], ad),
            _vergleich(w, w.b["id"], ad)])
        return (await B.vehicle_akte(vid, w.a),
                await B.vehicle_akte(vid, w.b),
                await B.vehicle_akte(vid, w.chef))

    akte_a, akte_b, akte_chef = w.run(lauf())
    assert len(akte_a["comparisons"]) == 2, akte_a["comparisons"]
    assert len(akte_b["comparisons"]) == 1, akte_b["comparisons"]
    # Der Chef sieht weiterhin die ganze Firma.
    assert len(akte_chef["comparisons"]) == 3, akte_chef["comparisons"]


def test_02_akte_nennt_suchern_keine_mitbearbeiter(welt):
    """Befund 2: Die Namensliste der Mitbearbeiter ging an jeden Sucher."""
    w = welt
    B = _modul("routes.bestand")
    vid, ad = f"v_r29m_{w.s}", f"adm{w.s}"

    async def lauf():
        await w.db.vehicles.insert_one(_fahrzeug(w, vid, ad))
        return (await B.vehicle_akte(vid, w.a),
                await B.vehicle_akte(vid, w.chef))

    akte_a, akte_chef = w.run(lauf())
    assert akte_a["mitbearbeiter"] == [], akte_a["mitbearbeiter"]
    assert akte_a["owner"] is None or akte_a.get("owner") in (None, {}), akte_a.get("owner")
    namen_chef = [m["name"] for m in akte_chef["mitbearbeiter"]]
    assert namen_chef == ["Ben B"], namen_chef


# -------------------------------------------------------- Quarantaene
def test_03_quarantaene_verraet_den_einreicher_nicht(welt):
    """Befund 3: ingested_by_user/-dealer gingen beim Lesen mit."""
    w = welt
    LI = _modul("listing_identity")
    nummer = int(w.s[:8], 16)          # Kleinanzeigen-IDs sind Zahlen
    url = f"https://www.kleinanzeigen.de/s-anzeige/golf/{nummer}-216-1"

    async def lauf():
        daten = {"title": "Golf", "price": 9000,
                 "ingested_by_user": w.a["id"], "ingested_by_dealer": w.dealer_id}
        stand = await LI.store_client_listing(w.db, url, daten, w.dealer_id)
        gelesen = await LI.peek_cached_listing(w.db, url, dealer_id=w.dealer_id)
        roh = await w.db.listings_cache_client.find_one(
            {"dealer_id": w.dealer_id}, {"_id": 0, "data": 1})
        return stand, gelesen, roh

    stand, gelesen, roh = w.run(lauf())
    assert stand == "quarantined", stand
    daten, _snap = gelesen
    assert daten["title"] == "Golf" and daten["price"] == 9000
    assert "ingested_by_user" not in daten and "ingested_by_dealer" not in daten, daten
    # In der Datenbank bleibt die Herkunft — das Aufraeumen braucht sie.
    assert roh["data"]["ingested_by_user"] == w.a["id"]


# ------------------------------------------------------- Einkaufspreis
def _vorgang(w, vid, status, preis, user_id=None, wann=None):
    return {"id": f"kv_{uuid.uuid4().hex[:10]}", "dealer_id": w.dealer_id,
            "user_id": user_id or w.a["id"], "vehicle_id": vid,
            "status": status, "purchase_price": preis,
            "created_at": _jetzt(), "updated_at": wann or _jetzt()}


def test_04_stornierter_kauf_ist_kein_vertragspreis(welt):
    """Befund 4: Ohne offenen Vorgang galt der Preis eines stornierten
    bzw. nicht abgeholten Kaufs als aktueller Vertragspreis."""
    w = welt
    KV = _modul("kaufvorgang")
    vid = f"v_r29p_{w.s}"

    async def lauf():
        await w.db.vehicles.insert_one(_fahrzeug(w, vid, f"adp{w.s}"))
        await w.db.kaufvorgaenge.insert_many([
            _vorgang(w, vid, "storniert", 15000),
            _vorgang(w, vid, "nicht_abgeholt", 16000)])
        ohne = await KV.einkaufspreis_vorschlag(vid, w.dealer_id,
                                                user_id=w.a["id"])
        await w.db.kaufvorgaenge.insert_one(
            _vorgang(w, vid, "vertrag_erstellt", 14000))
        mit = await KV.einkaufspreis_vorschlag(vid, w.dealer_id,
                                               user_id=w.a["id"])
        await w.db.kaufvorgaenge.insert_one(_vorgang(w, vid, "abgeholt", 13500))
        abgeholt = await KV.einkaufspreis_vorschlag(vid, w.dealer_id,
                                                    user_id=w.a["id"])
        return ohne, mit, abgeholt

    ohne, mit, abgeholt = w.run(lauf())
    assert ohne == {"preis": None, "quelle": "keiner", "kaufvorgang_id": None}, ohne
    assert mit["preis"] == 14000 and mit["quelle"] == "vertrag", mit
    assert abgeholt["preis"] == 13500 and abgeholt["quelle"] == "abgeholt", abgeholt


def test_05_preis_findet_den_vorgang_auch_bei_vielen(welt):
    """Befund 4b: to_list(200) deckelte die Entscheidungsgrundlage."""
    w = welt
    KV = _modul("kaufvorgang")
    vid = f"v_r29v_{w.s}"
    alt = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()

    async def lauf():
        await w.db.vehicles.insert_one(_fahrzeug(w, vid, f"adv{w.s}"))
        await w.db.kaufvorgaenge.insert_many(
            [_vorgang(w, vid, "storniert", 9000, wann=alt) for _ in range(220)])
        # Der entscheidende (offene, juengste) Vorgang kommt ZULETZT.
        await w.db.kaufvorgaenge.insert_one(
            _vorgang(w, vid, "gesendet", 12345))
        return await KV.einkaufspreis_vorschlag(vid, w.dealer_id,
                                                user_id=w.a["id"])

    treffer = w.run(lauf())
    assert treffer["preis"] == 12345 and treffer["quelle"] == "vertrag", treffer


def test_06_fahrzeugstatus_ohne_500er_deckel(welt):
    """Befund 4c: Der Status las hoechstens 500 Vorgaenge — der abgeholte
    dahinter blieb unsichtbar, das Auto stand weiter auf 'storniert'."""
    w = welt
    KV = _modul("kaufvorgang")
    vid = f"v_r29s_{w.s}"

    async def lauf():
        await w.db.vehicles.insert_one(_fahrzeug(w, vid, f"ads{w.s}"))
        await w.db.kaufvorgaenge.insert_many(
            [_vorgang(w, vid, "storniert", None) for _ in range(505)])
        await w.db.kaufvorgaenge.insert_one(_vorgang(w, vid, "abgeholt", 11000))
        ziel = await KV.fahrzeug_status_aggregieren(vid, w.dealer_id)
        v = await w.db.vehicles.find_one({"id": vid}, {"_id": 0})
        return ziel, v

    ziel, v = w.run(lauf())
    assert ziel == "abgeholt", ziel
    assert v.get("purchase_price") == 11000, v.get("purchase_price")


# ---------------------------------------------------- Datei-Nachholung
class _FakeStorage:
    def __init__(self):
        self.geloescht = []

    def delete(self, key):
        self.geloescht.append(key)
        return True

    def delete_prefix(self, praefix):
        self.geloescht.append(praefix)
        return 1


def test_07_nachholung_behaelt_vormerkung_bei_kaputtem_verweis(welt, monkeypatch):
    """Befund 5: Datei geloescht, Verweis nicht bereinigt — die Vormerkung
    verschwand trotzdem und der tote Verweis blieb fuer immer stehen."""
    w = welt
    CS = _modul("cleanup_service")
    SS = _modul("storage_service")
    fake = _FakeStorage()
    monkeypatch.setattr(SS, "storage", fake)
    eid = f"sr_{w.s}"

    async def lauf():
        await w.db.storage_delete_retry.insert_one({
            "id": eid, "art": "key", "key": f"uploads/{w.s}.jpg",
            "dealer_id": w.dealer_id, "grund": "test", "versuche": 0,
            # Ungueltiger Sammlungsname -> die Bereinigung scheitert sicher.
            "ref": {"collection": "kaputt$name", "id": "doc1",
                    "unset_fields": ["foto"]},
            "created_at": _jetzt()})
        erst = await CS.storage_loeschungen_nachholen(w.db)
        nach_erst = await w.db.storage_delete_retry.find_one({"id": eid}, {"_id": 0})
        zweit = await CS.storage_loeschungen_nachholen(w.db)
        nach_zweit = await w.db.storage_delete_retry.find_one({"id": eid}, {"_id": 0})
        # Verweis reparieren -> jetzt darf die Vormerkung weg.
        await w.db.kaputte_docs.insert_one({"id": "doc1", "foto": "x"})
        await w.db.storage_delete_retry.update_one(
            {"id": eid}, {"$set": {"ref.collection": "kaputte_docs"}})
        dritt = await CS.storage_loeschungen_nachholen(w.db)
        weg = await w.db.storage_delete_retry.find_one({"id": eid})
        doc = await w.db.kaputte_docs.find_one({"id": "doc1"}, {"_id": 0})
        return erst, nach_erst, zweit, nach_zweit, dritt, weg, doc

    erst, nach_erst, zweit, nach_zweit, dritt, weg, doc = w.run(lauf())
    assert erst == 0 and zweit == 0, (erst, zweit)
    assert nach_erst is not None, "Vormerkung darf nicht verschwinden"
    assert nach_erst["storage_deleted"] is True and nach_erst["versuche"] == 1
    assert nach_zweit["versuche"] == 2, nach_zweit
    # Die Datei wird NICHT ein zweites Mal geloescht.
    assert len(fake.geloescht) == 1, fake.geloescht
    assert dritt == 1 and weg is None, (dritt, weg)
    assert "foto" not in doc, doc


# ------------------------------------------------- Namen der Kollegen
def test_09_bestandsliste_nennt_suchern_keine_kollegen(welt):
    """Regel Ahmad: In Bestandsliste und Fahrzeugpool standen die Namen der
    Kollegen, die am selben Auto arbeiten."""
    w = welt
    D = _modul("deps")

    async def lauf():
        eintrag = {"id": f"v_r29b_{w.s}", "owner_user_id": w.a["id"],
                   "mitbearbeiter_ids": [w.b["id"]]}
        fuer_sucher = (await D.besitzer_anreichern(w.a, [dict(eintrag)]))[0]
        fuer_chef = (await D.besitzer_anreichern(w.chef, [dict(eintrag)]))[0]
        return fuer_sucher, fuer_chef

    fuer_sucher, fuer_chef = w.run(lauf())
    assert fuer_sucher["mitbearbeiter_namen"] == [], fuer_sucher
    assert "owner_name" not in fuer_sucher, fuer_sucher
    assert fuer_chef["owner_name"] == "Anna A", fuer_chef
    assert fuer_chef["mitbearbeiter_namen"] == ["Ben B"], fuer_chef


# ----------------------------------------------- Anfragen an den Betreiber
def test_10_zweite_anfrage_frischt_firmen_und_kontaktdaten_auf(welt):
    """Befund: Firmenname, Kontakt und Verbrauch standen in $setOnInsert —
    eine bereits offene Anfrage zeigte dem Betreiber fuer immer die alten
    Angaben."""
    w = welt
    T = _modul("routes.team")
    schluessel = {"type": "verkaufspaket", "dealer_id": w.dealer_id,
                  "status": "offen"}

    async def lauf():
        erst, neu1 = await T._offene_anfrage_upsert(
            schluessel, {"id": "req1", "company_name": "Alt GmbH",
                         "contact_phone": "0511 1", "current_usage": 3,
                         "created_at": _jetzt()},
            {"wanted_tier": "gross", "updated_at": _jetzt()})
        zweit, neu2 = await T._offene_anfrage_upsert(
            schluessel, {"id": "req2", "company_name": "Neu GmbH",
                         "contact_phone": "0511 999", "current_usage": 12,
                         "created_at": _jetzt()},
            {"wanted_tier": "enterprise", "updated_at": _jetzt()})
        return erst, neu1, zweit, neu2

    erst, neu1, zweit, neu2 = w.run(lauf())
    assert neu1 is True and erst["company_name"] == "Alt GmbH"
    assert neu2 is False, "zweite Anfrage darf keine zweite Zeile anlegen"
    assert zweit["id"] == "req1", "die offene Anfrage bleibt dieselbe"
    assert zweit["company_name"] == "Neu GmbH", zweit
    assert zweit["contact_phone"] == "0511 999" and zweit["current_usage"] == 12
    assert zweit["wanted_tier"] == "enterprise", zweit


# ------------------------------------------------------ Registrierung
def test_08_fahrer_registrierung_hat_eigenen_zaehler(welt, monkeypatch):
    """Befund 6: Beide Limiter hiessen 'registrierung' und teilten sich
    damit dieselben 5 Versuche je Stunde und IP."""
    w = welt
    RL = _modul("rate_limiter")
    D = _modul("deps")
    alt_db = getattr(D, "db", None)
    D.db = w.db
    monkeypatch.setattr(RL, "_RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(RL, "_EXEMPT_LOOPBACK", False)
    assert RL.register_limiter.name != RL.driver_register_limiter.name

    async def lauf():
        ip = f"9.9.9.{w.s[:1]}"
        firma = [await RL.register_limiter.check(ip) for _ in range(6)]
        fahrer = await RL.driver_register_limiter.check(ip)
        return firma, fahrer

    try:
        firma, fahrer = w.run(lauf())
    finally:
        if alt_db is not None:
            D.db = alt_db
    assert firma[:5] == [True] * 5 and firma[5] is False, firma
    assert fahrer is True, "Die Fahrer-Anmeldung darf nicht mitgezaehlt werden"
