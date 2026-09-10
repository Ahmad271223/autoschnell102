# -*- coding: utf-8 -*-
"""Runde 21 (Befund Ahmad 10.09.2026): Fahrerfotos — wer, wie lange, wo.

  * Frist ab dem HOCHLADEN (FAHRERFOTO_TAGE, Standard 90) statt 7/14 Tage ab
    Terminabschluss: geloeschte, stornierte, wiedergeoeffnete Termine sind
    abgedeckt; die Terminschleife loescht Berichtsfotos nicht mehr
  * vorgemerkte Loeschungen werden nicht jede Stunde neu angestossen
  * Fahrer: Foto nur, solange der Termin ihm zugeteilt ist; Bericht nur der
    eigene (nicht der des Vorgaengers nach Neuzuteilung)
  * Akte fuer Sucher: massgeblicher Bericht unter den EIGENEN Terminen
  * EXIF/GPS wird beim Speichern immer entfernt
  * Fahrerfotos mit einem Klick ins Inserat (eigene Kopie, keine fremden Keys)
"""
import asyncio
import inspect
import io
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
DB_NAME = os.environ.get("DB_NAME") or "autoschnell"


def _jetzt():
    return datetime.now(timezone.utc)


def _iso(dt):
    return dt.isoformat()


def _jpeg(exif: bool = False, groesse=(400, 300)) -> bytes:
    from PIL import Image
    im = Image.new("RGB", groesse, (180, 40, 40))
    buf = io.BytesIO()
    if exif:
        e = Image.Exif()
        e[0x010F] = "Testhersteller"          # Make
        e[0x0110] = "Testhandy"               # Model
        e[0x0132] = "2026:09:10 10:00:00"     # DateTime
        im.save(buf, "JPEG", quality=90, exif=e.tobytes())
    else:
        im.save(buf, "JPEG", quality=90)
    return buf.getvalue()


def _module(name):
    import importlib
    return importlib.import_module(name)


@pytest.fixture
def welt():
    from motor.motor_asyncio import AsyncIOMotorClient
    s = uuid.uuid4().hex[:10]
    names = ["deps", "kaufvorgang", "routes.drivers", "routes.resale", "routes.bestand"]
    mods = [_module(n) for n in names]
    alt = [(m, getattr(m, "db", None)) for m in mods]

    class _W:
        pass

    w = _W()
    w.s = s
    w.dealer_id = f"d_r21_{s}"
    w.loop = asyncio.new_event_loop()
    asyncio.set_event_loop(w.loop)
    w.client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    w.db = w.client[DB_NAME]
    for m in mods:
        if hasattr(m, "db"):
            m.db = w.db
    w.run = lambda coro: w.loop.run_until_complete(coro)
    w.keys = []

    def speichern(raw: bytes, kat: str = "pickup") -> str:
        from storage_service import make_key, storage
        k = make_key(kat, w.dealer_id, "foto.jpg")
        storage.save(k, raw)
        w.keys.append(k)
        return k

    def existiert(k: str) -> bool:
        from storage_service import load_async, StorageError
        try:
            w.run(load_async(k))
            return True
        except StorageError:
            return False

    w.speichern = speichern
    w.existiert = existiert
    yield w
    try:
        for c in ("pickup_reports", "appointments", "resale_listings", "dealer_drivers",
                  "activity_logs", "storage_delete_retry", "vehicles", "kaufvorgaenge"):
            w.run(w.db[c].delete_many({"dealer_id": w.dealer_id}))
        from storage_service import delete_prefix_async
        for kat in ("pickup", "resale"):
            try:
                w.run(delete_prefix_async(f"{kat}/{w.dealer_id}/"))
            except Exception:
                pass
    finally:
        for m, d in alt:
            if d is not None:
                m.db = d
        w.client.close()
        w.loop.close()


# ------------------------------------------------------------------ Frist
def test_01_frist_standard_90_tage_und_terminschleife_loescht_keine_berichtsfotos():
    import cleanup_service as C
    assert C.FAHRERFOTO_TAGE == int(os.environ.get("FAHRERFOTO_TAGE") or 90)
    quelle = inspect.getsource(C._cleanup_once)
    assert "_delete_report_photos(" not in quelle, "Terminschleife darf Berichtsfotos nicht mehr loeschen"
    assert "berichtsfotos_nach_frist_loeschen(" in quelle


def test_02_fotos_werden_ab_hochladen_geloescht_auch_ohne_termin(welt):
    import cleanup_service as C
    alt_k = welt.speichern(_jpeg())
    neu_k = welt.speichern(_jpeg())
    offen_k = welt.speichern(_jpeg())
    jetzt = _jetzt()
    alt_iso = _iso(jetzt - timedelta(days=C.FAHRERFOTO_TAGE + 1))
    welt.run(welt.db.pickup_reports.insert_many([
        # Termin existiert nicht mehr (geloescht) und war nie abgeschlossen
        {"id": f"r_alt_{welt.s}", "dealer_id": welt.dealer_id, "appointment_id": f"weg_{welt.s}",
         "vehicle_id": f"v_{welt.s}", "superseded": False, "version": 1, "created_at": alt_iso,
         "deviations": [{"label": "Kratzer", "photo_key": alt_k},
                        {"label": "Vorgemerkt", "photo_key": offen_k, "photo_loeschung_offen": True}]},
        # frisch hochgeladen — auch wenn der Termin laengst abgeschlossen ist
        {"id": f"r_neu_{welt.s}", "dealer_id": welt.dealer_id, "appointment_id": f"a_neu_{welt.s}",
         "vehicle_id": f"v_{welt.s}", "superseded": False, "version": 1,
         "created_at": _iso(jetzt - timedelta(days=5)),
         "deviations": [{"label": "Delle", "photo_key": neu_k}]},
    ]))
    welt.run(welt.db.appointments.insert_one({
        "id": f"a_neu_{welt.s}", "dealer_id": welt.dealer_id, "status": "abgeholt",
        "abgeschlossen_seit": _iso(jetzt - timedelta(days=30))}))
    stats: dict = {}
    n = welt.run(C.berichtsfotos_nach_frist_loeschen(welt.db, jetzt, stats))
    assert n == 1
    alt = welt.run(welt.db.pickup_reports.find_one({"id": f"r_alt_{welt.s}"}))
    assert alt["deviations"][0]["photo_key"] is None and alt["deviations"][0]["photo_deleted_at"]
    assert alt["deviations"][1]["photo_key"] == offen_k, "vorgemerkte Loeschung nicht erneut anstossen"
    assert not welt.existiert(alt_k)
    neu = welt.run(welt.db.pickup_reports.find_one({"id": f"r_neu_{welt.s}"}))
    assert neu["deviations"][0]["photo_key"] == neu_k and welt.existiert(neu_k)
    # zweiter Lauf: nichts mehr zu tun (vorgemerkter Eintrag wird uebersprungen)
    assert welt.run(C.berichtsfotos_nach_frist_loeschen(welt.db, jetzt, {})) == 0


# ------------------------------------------------------------------ Fahrer
def test_03_fahrer_sieht_foto_nur_solange_termin_ihm_gehoert(welt):
    D = _module("routes.drivers")
    k = welt.speichern(_jpeg())
    appt_id = f"a_drv_{welt.s}"
    drv_a, drv_b = {"id": f"fa_{welt.s}"}, {"id": f"fb_{welt.s}"}
    welt.run(welt.db.dealer_drivers.insert_many([
        {"dealer_id": welt.dealer_id, "driver_account_id": drv_a["id"]},
        {"dealer_id": welt.dealer_id, "driver_account_id": drv_b["id"]}]))
    welt.run(welt.db.appointments.insert_one(
        {"id": appt_id, "dealer_id": welt.dealer_id, "driver_id": drv_a["id"], "status": "abgeholt"}))
    welt.run(welt.db.pickup_reports.insert_one(
        {"id": f"r_drv_{welt.s}", "dealer_id": welt.dealer_id, "appointment_id": appt_id,
         "driver_account_id": drv_a["id"], "driver_name": "Fahrer A", "superseded": False,
         "version": 1, "created_at": _iso(_jetzt()), "notes": "privat",
         "deviations": [{"label": "Kratzer", "photo_key": k}]}))
    antwort = welt.run(D.driver_pickup_foto(k, drv_a))
    assert antwort.status_code == 200 and antwort.body[:2] == b"\xff\xd8"
    # Neuzuteilung an Fahrer B
    welt.run(welt.db.appointments.update_one({"id": appt_id}, {"$set": {"driver_id": drv_b["id"]}}))
    with pytest.raises(HTTPException) as e:
        welt.run(D.driver_pickup_foto(k, drv_a))
    assert e.value.status_code == 404
    # B sieht den Bericht von A nicht (auch nicht Name/Notizen/Fotoschluessel)
    assert welt.run(D.driver_get_report(appt_id, drv_b)) == {}


# ------------------------------------------------------------------ Akte/Sucher
def test_04_massgeblicher_bericht_unter_eigenen_terminen(welt):
    from abholbericht import massgeblicher_bericht
    vid = f"v_mb_{welt.s}"
    jetzt = _jetzt()
    welt.run(welt.db.appointments.insert_many([
        {"id": f"a_kollege_{welt.s}", "dealer_id": welt.dealer_id, "status": "abgeholt",
         "pickup_date": "2099-01-02"},
        {"id": f"a_eigen_{welt.s}", "dealer_id": welt.dealer_id, "status": "nicht abgeholt",
         "pickup_date": "2099-01-01"}]))
    welt.run(welt.db.pickup_reports.insert_many([
        {"id": f"r_kollege_{welt.s}", "dealer_id": welt.dealer_id, "vehicle_id": vid,
         "appointment_id": f"a_kollege_{welt.s}", "superseded": False, "version": 1,
         "created_at": _iso(jetzt)},
        {"id": f"r_eigen_{welt.s}", "dealer_id": welt.dealer_id, "vehicle_id": vid,
         "appointment_id": f"a_eigen_{welt.s}", "superseded": False, "version": 1,
         "created_at": _iso(jetzt - timedelta(days=1))}]))
    firma = welt.run(massgeblicher_bericht(welt.db, vid, welt.dealer_id))
    assert firma["id"] == f"r_kollege_{welt.s}"
    eigen = welt.run(massgeblicher_bericht(welt.db, vid, welt.dealer_id,
                                           nur_termine=[f"a_eigen_{welt.s}"]))
    assert eigen["id"] == f"r_eigen_{welt.s}"
    assert welt.run(massgeblicher_bericht(welt.db, vid, welt.dealer_id, nur_termine=[])) is None


# ------------------------------------------------------------------ EXIF
def test_05_exif_wird_beim_speichern_entfernt():
    from PIL import Image
    from storage_service import bild_verkleinern
    mit = _jpeg(exif=True)
    assert len(Image.open(io.BytesIO(mit)).getexif()) > 0
    ohne = bild_verkleinern(mit, wo="Test")
    bild = Image.open(io.BytesIO(ohne))
    assert len(bild.getexif()) == 0 and not bild.info.get("exif")
    assert bild.format == "JPEG" and bild.size == (400, 300)
    # metadatenfreie, passende Bilder bleiben unveraendert
    sauber = _jpeg(exif=False)
    assert bild_verkleinern(sauber, wo="Test") == sauber


# ------------------------------------------------------------------ Inserat
def test_06_fahrerfotos_ins_inserat_uebernehmen(welt):
    R = _module("routes.resale")
    user = {"id": f"chef_{welt.s}", "dealer_id": welt.dealer_id, "role": "dealer"}
    vid = f"v_res_{welt.s}"
    k1 = welt.speichern(_jpeg(exif=True))
    fremd = f"pickup/andere_firma_{welt.s}/x.jpg"
    welt.run(welt.db.pickup_reports.insert_one(
        {"id": f"r_res_{welt.s}", "dealer_id": welt.dealer_id, "vehicle_id": vid,
         "appointment_id": f"a_res_{welt.s}", "superseded": False, "version": 1,
         "created_at": _iso(_jetzt()),
         "deviations": [{"label": "Kratzer hinten", "photo_key": k1},
                        {"label": "ohne Foto", "photo_key": None}]}))
    lid = f"l_{welt.s}"
    welt.run(welt.db.resale_listings.insert_one(
        {"id": lid, "dealer_id": welt.dealer_id, "vehicle_id": vid, "status": "entwurf",
         "photos": {"mode": "einkauf", "einkauf_urls": [], "uploaded_keys": []},
         "prices": {"public": None}, "created_at": _iso(_jetzt())}))
    # fremder Schluessel wird ignoriert -> nichts uebrig -> 400
    with pytest.raises(HTTPException) as e:
        welt.run(R.fotos_aus_abholbericht(lid, R.AbholfotosIn(photo_keys=[fremd]), user))
    assert e.value.status_code == 400
    out = welt.run(R.fotos_aus_abholbericht(lid, None, user))
    assert out["uebernommen"] == 1
    l = welt.run(welt.db.resale_listings.find_one({"id": lid}))
    neu = l["photos"]["uploaded_keys"]
    assert len(neu) == 1 and neu[0].startswith(f"resale/{welt.dealer_id}/") and neu[0] != k1
    welt.keys.append(neu[0])
    assert l["photos"]["aus_abholbericht"] == [k1]
    assert l["photos"]["mode"] == "beide", "sonst waeren uebernommene Fotos unsichtbar"
    from PIL import Image
    from storage_service import load_async
    kopie = welt.run(load_async(neu[0]))
    assert len(Image.open(io.BytesIO(kopie)).getexif()) == 0, "Inseratskopie ohne EXIF"
    # zweimal uebernehmen geht nicht; Abruf zeigt nichts mehr an
    with pytest.raises(HTTPException):
        welt.run(R.fotos_aus_abholbericht(lid, None, user))
    detail = welt.run(R.get_listing(lid, user))
    assert detail["abholfotos"] == []
    # das Original im Abholbericht bleibt unberuehrt
    assert welt.existiert(k1)


# ------------------------------------------------------------------ Gegenpruefung
def test_07_beschaedigtes_foto_mit_gps_wird_abgelehnt():
    from PIL import Image
    from storage_service import StorageError, bild_verkleinern
    im = Image.new("RGB", (1200, 900), (10, 120, 40))
    e = Image.Exif()
    e[0x010F] = "Testhersteller"
    gps = {1: "N", 2: (52.0, 31.0, 12.0), 3: "E", 4: (13.0, 24.0, 0.0)}
    e[0x8825] = gps
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=92, exif=e.tobytes())
    roh = buf.getvalue()
    kaputt = roh[: int(len(roh) * 0.7)]
    with pytest.raises(StorageError):
        bild_verkleinern(kaputt, wo="Test")


def test_08_farbprofil_bleibt_exif_geht():
    from PIL import Image, ImageCms
    from storage_service import bild_verkleinern
    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    im = Image.new("RGB", (800, 300), (200, 100, 50))
    e = Image.Exif()
    e[0x010F] = "Testhersteller"
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=90, exif=e.tobytes(), icc_profile=icc)
    aus = Image.open(io.BytesIO(bild_verkleinern(buf.getvalue(), wo="Test")))
    assert len(aus.getexif()) == 0
    assert aus.info.get("icc_profile") == icc


def test_09_doppelte_uebernahme_bei_parallelen_anfragen_verhindert(welt):
    R = _module("routes.resale")
    user = {"id": f"chef_{welt.s}", "dealer_id": welt.dealer_id, "role": "dealer"}
    vid = f"v_par_{welt.s}"
    k1, k2 = welt.speichern(_jpeg()), welt.speichern(_jpeg())
    welt.run(welt.db.pickup_reports.insert_one(
        {"id": f"r_par_{welt.s}", "dealer_id": welt.dealer_id, "vehicle_id": vid,
         "appointment_id": f"a_par_{welt.s}", "superseded": False, "version": 1,
         "created_at": _iso(_jetzt()),
         "deviations": [{"label": "A", "photo_key": k1}, {"label": "B", "photo_key": k2}]}))
    lid = f"lp_{welt.s}"
    welt.run(welt.db.resale_listings.insert_one(
        {"id": lid, "dealer_id": welt.dealer_id, "vehicle_id": vid, "status": "entwurf",
         "photos": {"mode": "einkauf", "einkauf_urls": [], "uploaded_keys": []},
         "prices": {"public": None}, "created_at": _iso(_jetzt())}))

    async def beide():
        return await asyncio.gather(R.fotos_aus_abholbericht(lid, None, user),
                                    R.fotos_aus_abholbericht(lid, None, user),
                                    return_exceptions=True)
    ergebnisse = welt.run(beide())
    ok = [e for e in ergebnisse if isinstance(e, dict)]
    fehler = [e for e in ergebnisse if isinstance(e, HTTPException)]
    assert len(ok) == 1 and len(fehler) == 1 and fehler[0].status_code == 409, ergebnisse
    l = welt.run(welt.db.resale_listings.find_one({"id": lid}))
    assert len(l["photos"]["uploaded_keys"]) == 2, "jedes Fahrerfoto genau einmal im Inserat"
    welt.keys.extend(l["photos"]["uploaded_keys"])
