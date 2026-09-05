# -*- coding: utf-8 -*-
"""Go-Live-Audit 09/2026 — Loeschung und Aufbewahrung:
Vertragsloeschung (Trockenlauf, Opt-in, Auto-Datensatz-Pflicht, Grabstein-
Wiederaufnahme), paketweiser Auto-Daten-Backfill (>500), Datei-Loeschung
mit Vormerkung und Nachholung, Fehlerprotokoll-Deckel und Anfragen-Frist.

In-Prozess-Tests (kein laufendes Backend, nur die lokale Mongo). Alle
eingefuegten Dokumente tragen ein eindeutiges Suffix und werden wieder
entfernt. Fristen werden mit einem `now` im Jahr 2001 geprueft, damit NUR
die Testdaten (Jahr 2000) als Kandidaten gelten und echte Daten der
gemeinsamen Dev-DB unberuehrt bleiben (Vorfall 09/2026: geloeschte
Altvertraege in der Dev-DB).
"""
import asyncio
import importlib.util
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
DB_NAME = os.environ.get("DB_NAME") or "autoschnell"
SUF = uuid.uuid4().hex[:10]
DEALER = f"d_loesch_{SUF}"
ALT = datetime(2000, 3, 1, tzinfo=timezone.utc).isoformat()   # Testdaten
NOW = datetime(2001, 1, 1, tzinfo=timezone.utc)               # "jetzt" fuer Fristen
_COLLS = ("generated_pdfs", "generated_pdf_versions", "appointments",
          "pickup_protocols", "pickup_reports", "admin_vehicle_data",
          "activity_logs", "error_logs", "plan_requests", "resale_listings",
          "listing_snapshots", "storage_delete_retry", "betriebsalarme")


def _run(coro_factory):
    async def _inner():
        from motor.motor_asyncio import AsyncIOMotorClient
        cl = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
        try:
            return await coro_factory(cl[DB_NAME])
        finally:
            cl.close()
    return asyncio.run(_inner())


async def _aufraeumen(db):
    for coll in _COLLS:
        await db[coll].delete_many({"id": {"$regex": SUF}})
    await db.generated_pdfs.delete_many({"dealer_id": DEALER})
    await db.activity_logs.delete_many({"ref": {"$regex": SUF}})
    await db.betriebsalarme.delete_many({"ref": {"$regex": SUF}})
    await db.storage_delete_retry.delete_many(
        {"$or": [{"key": {"$regex": SUF}}, {"prefix": {"$regex": SUF}}]})
    await db.system_reports.delete_many(
        {"typ": "vertrag_loeschvorschau", "ids": {"$regex": SUF}})


@pytest.fixture(autouse=True)
def _sauber():
    """Jeder Test raeumt seine Dokumente weg — auch bei Fehlschlag."""
    yield
    _run(_aufraeumen)


# ---------------------------------------------------------------------------
# 1) Fristloeschung: Trockenlauf / Opt-in / Auto-Datensatz-Pflicht
# ---------------------------------------------------------------------------
def test_trockenlauf_speichert_vorschau_und_loescht_nichts(monkeypatch):
    monkeypatch.delenv("VERTRAG_LOESCHUNG_AKTIV", raising=False)

    async def lauf(db):
        import cleanup_service as cs
        ids = [f"c_dry_{SUF}_{i}" for i in range(3)]
        await db.generated_pdfs.insert_many([
            {"id": cid, "dealer_id": DEALER, "contract_no": "T",
             "created_at": ALT, "admin_vehicle_data_id": f"avd_{cid}"}
            for cid in ids])
        stats = {}
        assert await cs.vertraege_nach_frist_loeschen(
            db, NOW, aktiv=False, stats=stats) == 0
        assert await db.generated_pdfs.count_documents({"id": {"$in": ids}}) == 3
        rep = await db.system_reports.find_one(
            {"typ": "vertrag_loeschvorschau"}, {"_id": 0})
        assert rep and rep["anzahl"] == 3 and set(rep["ids"]) == set(ids)
        assert rep["id"] and rep["created_at"] and rep["cutoff"] < "2001"
        assert stats["contracts_vorschau"] == 3
        # Genau EIN Vorschau-Dokument (wird ersetzt, nicht angehaeuft)
        assert await cs.vertraege_nach_frist_loeschen(db, NOW, aktiv=False) == 0
        assert await db.system_reports.count_documents(
            {"typ": "vertrag_loeschvorschau"}) == 1
        # Standard ohne env = Trockenlauf
        assert cs.vertrag_loeschung_aktiv() is False
        assert await cs.vertraege_nach_frist_loeschen(db, NOW) == 0
        assert await db.generated_pdfs.count_documents({"id": {"$in": ids}}) == 3
        assert await db.betriebsalarme.count_documents(
            {"ref": {"$in": ids}}) == 0

    _run(lauf)


def test_aktiv_loescht_nur_mit_auto_datensatz_und_alarmiert_sonst(monkeypatch):
    monkeypatch.setenv("VERTRAG_LOESCHUNG_AKTIV", "true")

    async def lauf(db):
        import cleanup_service as cs
        assert cs.vertrag_loeschung_aktiv() is True
        gut, avd = f"c_gut_{SUF}", f"avd_gut_{SUF}"
        haengend, ohne = f"c_haeng_{SUF}", f"c_ohne_{SUF}"
        appt, prot = f"a_{SUF}", f"p_{SUF}"
        await db.admin_vehicle_data.insert_one(
            {"id": avd, "brand": "Test", "damages": [], "schema_version": 2})
        await db.generated_pdfs.insert_many([
            {"id": gut, "dealer_id": DEALER, "contract_no": "G",
             "created_at": ALT, "admin_vehicle_data_id": avd},
            {"id": haengend, "dealer_id": DEALER, "contract_no": "H",
             "created_at": ALT, "admin_vehicle_data_id": f"avd_fehlt_{SUF}"},
            {"id": ohne, "dealer_id": DEALER, "contract_no": "O",
             "created_at": ALT},
        ])
        await db.generated_pdf_versions.insert_one(
            {"id": f"v_{SUF}", "contract_id": gut, "dealer_id": DEALER, "version": 1})
        await db.appointments.insert_one(
            {"id": appt, "dealer_id": DEALER, "contract_id": gut,
             "seller_name": "Max Muster", "seller_phone": "0170",
             "seller_email": "m@x.de", "pickup_address": "Weg 1"})
        await db.pickup_protocols.insert_one(
            {"id": prot, "appointment_id": appt, "dealer_id": DEALER,
             "seller_name": "Max Muster", "pickup_address": "Weg 1",
             "pdf_path": f"protocol/{DEALER}/{SUF}.pdf",
             "signature_seller_key": f"protocol/{DEALER}/{SUF}_s.png"})
        stats = {}
        n = await cs.vertraege_nach_frist_loeschen(db, NOW, stats=stats)
        assert n == 1 and stats["contracts_uebersprungen"] == 2
        assert await db.generated_pdfs.find_one({"id": gut}) is None
        assert await db.generated_pdfs.find_one({"id": haengend}) is not None
        assert await db.generated_pdfs.find_one({"id": ohne}) is not None
        assert await db.generated_pdf_versions.count_documents({"contract_id": gut}) == 0
        a = await db.appointments.find_one({"id": appt}, {"_id": 0})
        assert a["contract_id"] is None and a["seller_name"] == "" \
            and a["seller_email"] == "" and a["pii_geloescht_at"]
        p = await db.pickup_protocols.find_one({"id": prot}, {"_id": 0})
        assert p["seller_name"] == "" and p["pickup_address"] == ""
        assert "pdf_path" not in p and "signature_seller_key" not in p
        # Der anonyme Datensatz bleibt unangetastet
        assert await db.admin_vehicle_data.find_one({"id": avd}) is not None
        alarme = [x async for x in db.betriebsalarme.find(
            {"typ": "vertrag_ohne_auto_daten", "offen": True,
             "ref": {"$in": [haengend, ohne]}}, {"_id": 0})]
        assert len(alarme) == 2
        assert all(x["details"]["dealer_id"] == DEALER for x in alarme)
        assert await db.betriebsalarme.count_documents({"ref": gut}) == 0
        log = await db.activity_logs.find_one(
            {"action": "vertrag.geloescht.90tage", "ref": gut}, {"_id": 0})
        assert log and log["dealer_id"] == DEALER \
            and log["meta"]["contract_no"] == "G"
        # Zweiter Lauf: die uebersprungenen bleiben, Alarm wird hochgezaehlt
        assert await cs.vertraege_nach_frist_loeschen(db, NOW) == 0
        alarm = await db.betriebsalarme.find_one(
            {"typ": "vertrag_ohne_auto_daten", "ref": ohne}, {"_id": 0})
        assert alarm["anzahl"] == 2

    _run(lauf)


# ---------------------------------------------------------------------------
# 2) Backfill in Paketen: 600 Vertraege ohne admin_vehicle_data_id
# ---------------------------------------------------------------------------
def test_backfill_verarbeitet_mehr_als_500_vertraege_in_einem_lauf(monkeypatch):
    async def lauf(db):
        import auto_daten
        import cleanup_service as cs
        ids = [f"c_bf_{SUF}_{i:04d}" for i in range(600)]
        await db.generated_pdfs.insert_many([
            {"id": cid, "dealer_id": DEALER, "created_at": ALT, "contract_data": {}}
            for cid in ids])
        aufrufe = []

        async def stub(db_, c):
            if SUF not in c["id"]:
                return None          # fremde Dokumente der Dev-DB nicht anfassen
            aufrufe.append(c["id"])
            r = await db_.generated_pdfs.update_one(
                {"id": c["id"], "admin_vehicle_data_id": {"$exists": False}},
                {"$set": {"admin_vehicle_data_id": f"avd_stub_{c['id']}"}})
            return f"avd_stub_{c['id']}" if r.modified_count else None

        monkeypatch.setattr(auto_daten, "nachtragen", stub)
        n = await cs.auto_daten_reparieren(db)
        assert n >= 600
        assert len(aufrufe) == 600 and len(set(aufrufe)) == 600
        assert await db.generated_pdfs.count_documents(
            {"id": {"$in": ids}, "admin_vehicle_data_id": {"$exists": False}}) == 0
        # Nichts mehr offen -> kein weiterer Aufruf fuer unsere Vertraege
        aufrufe.clear()
        await cs.auto_daten_reparieren(db)
        assert aufrufe == []

    _run(lauf)


def test_backfill_bricht_bei_unveraendertem_paket_ab(monkeypatch):
    """Schlaegt nachtragen dauerhaft fehl, darf der Lauf nicht haengen."""
    async def lauf(db):
        import auto_daten
        import cleanup_service as cs
        ids = [f"c_stuck_{SUF}_{i}" for i in range(3)]
        await db.generated_pdfs.insert_many([
            {"id": cid, "dealer_id": DEALER, "created_at": ALT} for cid in ids])
        zaehler = {"n": 0}

        async def stub(db_, c):
            zaehler["n"] += 1
            return None

        monkeypatch.setattr(auto_daten, "nachtragen", stub)
        await cs.auto_daten_reparieren(db, limit=2)
        # 2 Pakete (erstes + identische Wiederholung) statt 500
        assert zaehler["n"] <= 4 + 2 * 3

    _run(lauf)


# ---------------------------------------------------------------------------
# 3) Grabstein: abgebrochene Loeschung wird zu Ende gefuehrt
# ---------------------------------------------------------------------------
def test_grabstein_wiederaufnahme_fuehrt_halbfertige_loeschung_zu_ende():
    async def lauf(db):
        import cleanup_service as cs
        cid, appt = f"c_grab_{SUF}", f"a_grab_{SUF}"
        await db.generated_pdfs.insert_one(
            {"id": cid, "dealer_id": DEALER, "contract_no": "GR", "created_at": ALT,
             "admin_vehicle_data_id": f"avd_x_{SUF}",
             "loeschung": {"status": "laeuft", "grund": "90tage", "scrub_pii": True,
                           "gestartet": (NOW - timedelta(minutes=30)).isoformat()}})
        await db.generated_pdf_versions.insert_one(
            {"id": f"v_grab_{SUF}", "contract_id": cid, "version": 1})
        await db.appointments.insert_one(
            {"id": appt, "dealer_id": DEALER, "contract_id": cid,
             "seller_name": "Erika", "seller_phone": "1", "seller_email": "e@x.de",
             "pickup_address": "A 1"})
        # Frischer Grabstein (< 10 min) gehoert noch dem laufenden Prozess
        frisch = f"c_frisch_{SUF}"
        await db.generated_pdfs.insert_one(
            {"id": frisch, "dealer_id": DEALER, "created_at": ALT,
             "loeschung": {"status": "laeuft", "grund": "manuell", "scrub_pii": False,
                           "gestartet": (NOW - timedelta(minutes=2)).isoformat()}})
        assert await cs.vertragsloeschungen_wiederaufnehmen(db, NOW) == 1
        assert await db.generated_pdfs.find_one({"id": cid}) is None
        assert await db.generated_pdfs.find_one({"id": frisch}) is not None
        assert await db.generated_pdf_versions.count_documents({"contract_id": cid}) == 0
        a = await db.appointments.find_one({"id": appt}, {"_id": 0})
        assert a["contract_id"] is None and a["seller_name"] == ""
        log = await db.activity_logs.find_one(
            {"action": "vertrag.geloescht.90tage", "ref": cid}, {"_id": 0})
        assert log and log["meta"]["wiederaufnahme"] is True
        # Idempotent: nochmal auf dem geloeschten Vertrag -> False, kein 2. Log
        assert await cs.vertrag_endgueltig_loeschen(
            db, cid, scrub_pii=True, grund="90tage") is False
        assert await db.activity_logs.count_documents({"ref": cid}) == 1

    _run(lauf)


def test_manuelle_loeschung_setzt_grabstein_und_kappt_termine():
    async def lauf(db):
        import cleanup_service as cs
        cid, appt = f"c_man_{SUF}", f"a_man_{SUF}"
        await db.generated_pdfs.insert_one(
            {"id": cid, "dealer_id": DEALER, "contract_no": "M", "created_at": ALT})
        await db.generated_pdf_versions.insert_one(
            {"id": f"v_man_{SUF}", "contract_id": cid, "version": 1})
        await db.appointments.insert_one(
            {"id": appt, "dealer_id": DEALER, "contract_id": cid,
             "seller_name": "bleibt"})
        assert await cs.vertrag_endgueltig_loeschen(
            db, cid, scrub_pii=False, grund="manuell", audit=False) is True
        assert await db.generated_pdfs.find_one({"id": cid}) is None
        assert await db.generated_pdf_versions.count_documents({"contract_id": cid}) == 0
        a = await db.appointments.find_one({"id": appt}, {"_id": 0})
        # ohne scrub_pii: nur der Verweis wird gekappt
        assert a["contract_id"] is None and a["seller_name"] == "bleibt"
        assert await db.activity_logs.count_documents({"ref": cid}) == 0  # audit=False
        assert await cs.vertrag_endgueltig_loeschen(
            db, f"gibt_es_nicht_{SUF}", scrub_pii=False, grund="manuell") is False

    _run(lauf)


# ---------------------------------------------------------------------------
# 4) Datei-Loeschung: vormerken statt verschlucken, nachholen mit ref
# ---------------------------------------------------------------------------
def test_datei_loeschung_wird_vorgemerkt_und_nachgeholt(monkeypatch):
    async def lauf(db):
        import cleanup_service as cs
        import snapshot_service as sn
        import storage_service as ss

        def kaputt(_key):
            raise OSError("Platte weg")

        # --- key: Inserats-Foto, ref mit $pull + loeschen_wenn_leer ---
        key, lid = f"resale/{DEALER}/{SUF}.jpg", f"l_{SUF}"
        await db.resale_listings.insert_one(
            {"id": lid, "dealer_id": DEALER, "status": "geloescht",
             "photos": {"uploaded_keys": [key]}})
        monkeypatch.setattr(ss.storage, "delete", kaputt)
        ref = {"collection": "resale_listings", "id": lid,
               "pull_key_from": "photos.uploaded_keys",
               "loeschen_wenn_leer": ["photos.uploaded_keys"]}
        assert await ss.loeschen_oder_vormerken(
            db, key=key, grund="test", dealer_id=DEALER, ref=ref) is False
        e = await db.storage_delete_retry.find_one({"key": key}, {"_id": 0})
        assert e and e["art"] == "key" and e["prefix"] is None
        assert e["versuche"] == 0 and "Platte weg" in e["letzter_fehler"]
        assert e["grund"] == "test" and e["dealer_id"] == DEALER
        assert e["ref"]["id"] == lid and e["id"] and e["created_at"] and e["updated_at"]
        # Nochmal fehlgeschlagen: kein Duplikat
        assert await ss.loeschen_oder_vormerken(
            db, key=key, grund="test", dealer_id=DEALER, ref=ref) is False
        assert await db.storage_delete_retry.count_documents({"key": key}) == 1
        # Nachholung scheitert weiter -> Versuch gezaehlt, Key bleibt im Inserat
        await cs.storage_loeschungen_nachholen(db)
        e = await db.storage_delete_retry.find_one({"key": key}, {"_id": 0})
        assert e["versuche"] == 1 and e.get("aufgegeben") is not True
        l = await db.resale_listings.find_one({"id": lid}, {"_id": 0})
        assert l["photos"]["uploaded_keys"] == [key]
        # Storage wieder heil -> Datei weg, ref angewendet, Eintrag geloescht,
        # leeres Inserat entfernt
        monkeypatch.setattr(ss.storage, "delete", lambda _k: True)
        await cs.storage_loeschungen_nachholen(db)
        assert await db.storage_delete_retry.find_one({"key": key}) is None
        assert await db.resale_listings.find_one({"id": lid}) is None

        # --- key: Protokoll, ref mit unset_fields ---
        key2, pid = f"protocol/{DEALER}/{SUF}.pdf", f"p_ref_{SUF}"
        await db.pickup_protocols.insert_one(
            {"id": pid, "pdf_path": key2, "pdf_path_loeschung_offen": True,
             "seller_name": ""})
        monkeypatch.setattr(ss.storage, "delete", kaputt)
        assert await ss.loeschen_oder_vormerken(
            db, key=key2, grund="test",
            ref={"collection": "pickup_protocols", "id": pid,
                 "unset_fields": ["pdf_path", "pdf_path_loeschung_offen"]}) is False
        monkeypatch.setattr(ss.storage, "delete", lambda _k: True)
        await cs.storage_loeschungen_nachholen(db)
        assert await db.pickup_protocols.find_one({"id": pid}, {"_id": 0}) == \
            {"id": pid, "seller_name": ""}

        # --- key: Berichtsfoto, ref auf Array-Element ---
        key3, rid = f"pickup/{DEALER}/{SUF}.jpg", f"r_ref_{SUF}"
        await db.pickup_reports.insert_one(
            {"id": rid, "deviations": [{"text": "Delle", "photo_key": key3,
                                        "photo_loeschung_offen": True},
                                       {"text": "ok", "photo_key": None}]})
        monkeypatch.setattr(ss.storage, "delete", kaputt)
        assert await ss.loeschen_oder_vormerken(
            db, key=key3, grund="test",
            ref={"collection": "pickup_reports", "id": rid,
                 "array": {"pfad": "deviations", "schluessel": "photo_key"},
                 "unset_fields": ["photo_key", "photo_loeschung_offen"],
                 "set_fields": {"photo_deleted_at": "$now"}}) is False
        monkeypatch.setattr(ss.storage, "delete", lambda _k: True)
        await cs.storage_loeschungen_nachholen(db)
        r = await db.pickup_reports.find_one({"id": rid}, {"_id": 0})
        assert r["deviations"][0] == {"text": "Delle",
                                      "photo_deleted_at": r["deviations"][0]["photo_deleted_at"]}
        assert r["deviations"][0]["photo_deleted_at"]
        assert r["deviations"][1] == {"text": "ok", "photo_key": None}

        # --- snapshot: False vom Snapshot-Storage gilt als Fehlschlag ---
        skey = f"snapshots/{SUF}/x.png"
        monkeypatch.setattr(sn, "delete_object", lambda _p: False)
        assert await ss.loeschen_oder_vormerken(
            db, key=skey, art="snapshot", grund="test") is False
        e = await db.storage_delete_retry.find_one({"key": skey}, {"_id": 0})
        assert e["art"] == "snapshot"
        monkeypatch.setattr(cs, "delete_object", lambda _p: True)
        await cs.storage_loeschungen_nachholen(db)
        assert await db.storage_delete_retry.find_one({"key": skey}) is None

        # --- Alt-Eintrag der Firmenloeschung (ohne art) = Praefix ---
        praefix = f"resale/{DEALER}/"
        await db.storage_delete_retry.insert_one(
            {"id": f"alt_{SUF}", "prefix": praefix, "dealer_id": DEALER,
             "created_at": ALT})
        gerufen = []
        monkeypatch.setattr(ss.storage, "delete_prefix",
                            lambda p: gerufen.append(p) or 0)
        await cs.storage_loeschungen_nachholen(db)
        assert gerufen == [praefix]
        assert await db.storage_delete_retry.find_one({"id": f"alt_{SUF}"}) is None

        # --- Aufgeben nach 20 Versuchen + Betriebsalarm ---
        key4 = f"resale/{DEALER}/{SUF}_gibtauf.jpg"
        await db.storage_delete_retry.insert_one(
            {"id": f"auf_{SUF}", "art": "key", "key": key4, "prefix": None,
             "dealer_id": DEALER, "grund": "test", "versuche": 19,
             "created_at": ALT})
        monkeypatch.setattr(ss.storage, "delete", kaputt)
        await cs.storage_loeschungen_nachholen(db)
        e = await db.storage_delete_retry.find_one({"id": f"auf_{SUF}"}, {"_id": 0})
        assert e["versuche"] == 20 and e["aufgegeben"] is True
        alarm = await db.betriebsalarme.find_one(
            {"typ": "datei_loeschung_aufgegeben", "ref": key4}, {"_id": 0})
        assert alarm and alarm["offen"] is True
        # Aufgegebene Eintraege werden nicht mehr angefasst
        await cs.storage_loeschungen_nachholen(db)
        e = await db.storage_delete_retry.find_one({"id": f"auf_{SUF}"}, {"_id": 0})
        assert e["versuche"] == 20

    _run(lauf)


def test_snapshot_zeile_bleibt_bei_offener_dateiloeschung(monkeypatch):
    async def lauf(db):
        import cleanup_service as cs
        import snapshot_service as sn
        vid, sid = f"v_snap_{SUF}", f"s_{SUF}"
        png, pdf = f"snap/{SUF}/a.png", f"snap/{SUF}/a.pdf"
        await db.listing_snapshots.insert_one(
            {"id": sid, "vehicle_id": vid, "status": "ready",
             "png_path": png, "pdf_path": pdf})
        # PNG laesst sich loeschen, PDF nicht
        monkeypatch.setattr(sn, "delete_object", lambda p: p.endswith(".png"))
        assert await cs._delete_snapshots_for_vehicle(db, vid, dealer_id=DEALER) == 0
        s = await db.listing_snapshots.find_one({"id": sid}, {"_id": 0})
        assert s["loeschung_offen"] is True and "png_path" not in s \
            and s["pdf_path"] == pdf
        assert await db.storage_delete_retry.count_documents(
            {"key": pdf, "art": "snapshot"}) == 1
        # Zweiter Lauf ueberspringt die markierte Zeile
        assert await cs._delete_snapshots_for_vehicle(db, vid, dealer_id=DEALER) == 0
        # Nachholung: PDF weg -> beide Dateien weg -> Zeile geloescht
        monkeypatch.setattr(cs, "delete_object", lambda _p: True)
        await cs.storage_loeschungen_nachholen(db)
        assert await db.listing_snapshots.find_one({"id": sid}) is None
        assert await db.storage_delete_retry.count_documents({"key": pdf}) == 0

    _run(lauf)


# ---------------------------------------------------------------------------
# 5) Aufbewahrung: Fehlerprotokolle (offen + Deckel), Anfragen
# ---------------------------------------------------------------------------
def test_fehlerlogs_deckel_und_anfragen_frist():
    async def lauf(db):
        import cleanup_service as cs
        offen = [{"id": f"e_open_{SUF}_{i}", "status": "open", "message": "t",
                  "created_at": f"2000-01-0{i + 1}T00:00:00+00:00"} for i in range(3)]
        erledigt = [{"id": f"e_done_{SUF}_{i}", "status": "resolved", "message": "t",
                     "created_at": f"2001-01-0{i + 1}T00:00:00+00:00"} for i in range(5)]
        await db.error_logs.insert_many(offen + erledigt)
        vorher = await db.error_logs.count_documents({})
        # offen_tage=300 (Stichtag 2000-03-07): nur unsere offenen von Jan 2000;
        # Deckel = Bestand - 6: nach den 3 offenen bleiben 3 zu viel -> die
        # aeltesten drei (unsere erledigten 01..03) fliegen, 04/05 bleiben.
        n = await cs.fehlerlogs_begrenzen(db, NOW, offen_tage=300, maximum=vorher - 6)
        assert n == 6
        assert await db.error_logs.count_documents(
            {"id": {"$regex": f"e_open_{SUF}"}}) == 0
        rest = sorted([d["id"] async for d in db.error_logs.find(
            {"id": {"$regex": f"e_done_{SUF}"}}, {"_id": 0, "id": 1})])
        assert rest == [f"e_done_{SUF}_3", f"e_done_{SUF}_4"]
        # Unter dem Deckel passiert nichts mehr
        assert await cs.fehlerlogs_begrenzen(
            db, NOW, offen_tage=300, maximum=vorher) == 0

        await db.plan_requests.insert_many([
            {"id": f"pr_alt_erl_{SUF}", "status": "erledigt", "type": "sucher_abo",
             "created_at": "2000-01-01T00:00:00+00:00",
             "updated_at": "2000-02-01T00:00:00+00:00"},
            {"id": f"pr_alt_abg_{SUF}", "status": "abgelehnt", "type": "sucher_abo",
             "created_at": "2000-01-01T00:00:00+00:00"},       # ohne updated_at
            {"id": f"pr_alt_offen_{SUF}", "status": "offen", "type": "sucher_abo",
             "created_at": "2000-01-01T00:00:00+00:00"},
            {"id": f"pr_neu_erl_{SUF}", "status": "erledigt", "type": "sucher_abo",
             "created_at": "2000-01-01T00:00:00+00:00",
             "updated_at": "2000-12-20T00:00:00+00:00"},        # juenger als 90 Tage
        ])
        assert await cs.anfragen_rotieren(db, NOW, tage=90) == 2
        rest = {d["id"] async for d in db.plan_requests.find(
            {"id": {"$regex": SUF}}, {"_id": 0, "id": 1})}
        assert rest == {f"pr_alt_offen_{SUF}", f"pr_neu_erl_{SUF}"}

    _run(lauf)


# ---------------------------------------------------------------------------
# 6) Skript: Freitext-Schaeden erkennen/normalisieren
# ---------------------------------------------------------------------------
def test_schaeden_freitext_erkennung():
    pfad = Path(__file__).resolve().parents[1] / "scripts" / "schaeden_freitext_bereinigen.py"
    spec = importlib.util.spec_from_file_location("schaeden_freitext_bereinigen", pfad)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert m.eintrag_bereinigen("• Kratzer: Motorhaube, Tür vorne links • Delle: Dach") == \
        (["Kratzer: Motorhaube, Tür vorne links", "Delle: Dach"], 0)
    assert m.eintrag_bereinigen("Delle Tür vorne rechts; Steinschlag Frontscheibe") == ([], 2)
    assert m.eintrag_bereinigen("• Rost: Schweller links • Max Mustermann war dabei") == \
        (["Rost: Schweller links"], 1)
    assert m.eintrag_bereinigen("Kratzer") == (["Kratzer"], 0)
    assert m.eintrag_bereinigen("Motorschaden / Unfallschaden vorhanden") == \
        (["Motorschaden / Unfallschaden vorhanden"], 0)
    assert m.datensatz_bereinigen(
        ["Kratzer: Dach", "Kontakt 0176 12345678", "• Kratzer: Dach"]) == \
        (["Kratzer: Dach"], 1)
