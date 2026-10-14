# -*- coding: utf-8 -*-
"""Weiterverkauf — Regeln vor der Wiederaktivierung (Ahmad, 20.09.2026).

  * Fahrzeugdaten kommen weiter aus dem Einkauf und duerfen angepasst
    werden (unveraendert).
  * Beschreibung hoechstens 500 Zeichen (vorher 30.000).
  * Hoechstens 10 Fotos je Inserat (vorher 40).
  * Fotos werden NICHT mehr aus dem Portal-Inserat uebernommen — sie
    muessen neu hochgeladen werden. Ohne eigenes Foto laesst sich nicht
    veroeffentlichen.
  * Ein veroeffentlichtes Inserat laeuft hoechstens 3 Wochen und
    verschwindet danach samt Fotos. Kaufvertrag, Kaufvorgang und
    Fahrzeugakte bleiben unberuehrt — es verschwindet nur die Anzeige.
"""
import asyncio
import inspect
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

BACKEND = Path(__file__).resolve().parents[1]
WURZEL = BACKEND.parent
sys.path.insert(0, str(BACKEND))

import deps  # noqa: E402
import routes.resale as R  # noqa: E402

MONGO_URL = __import__("os").environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"


def _jetzt():
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture
def welt(monkeypatch):
    from motor.motor_asyncio import AsyncIOMotorClient
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    name = f"autoschnell_mp_{uuid.uuid4().hex[:10]}"
    db = client[name]
    # Rollenpruefung 22.09.2026: ERST importieren, DANN umbiegen — sonst
    # bindet sich ein hier erstmals importiertes Modul per "from deps import
    # db" dauerhaft an diese Wegwerf-DB (geschlossene Schleife), und spaetere
    # Testdateien scheiterten mit "Event loop is closed".
    import importlib
    import cleanup_service
    importlib.import_module("routes.marketplace")   # wird im Aufraeumlauf nachgeladen
    monkeypatch.setattr(deps, "db", db)
    monkeypatch.setattr(cleanup_service, "db", db, raising=False)
    try:
        yield SimpleNamespace(db=db, run=loop.run_until_complete)
    finally:
        try:
            loop.run_until_complete(client.drop_database(name))
        finally:
            client.close()
            loop.close()


# ------------------------------------------------------- Die Zahlen
def test_01_die_grenzen_stehen_fest():
    assert R.INSERAT_BESCHREIBUNG_MAX == 500
    assert R.INSERAT_FOTOS_MAX == 10
    assert R.INSERAT_LAUFZEIT_TAGE == 21


def test_02_die_oberflaeche_nennt_dieselben_zahlen():
    """Sonst sagt der Browser 10 und der Server 40 — oder umgekehrt."""
    quelle = (WURZEL / "frontend" / "src" / "pages" / "app"
              / "Inserat.jsx").read_text(encoding="utf-8")
    assert f"const BESCHREIBUNG_MAX = {R.INSERAT_BESCHREIBUNG_MAX};" in quelle
    assert f"const FOTOS_MAX = {R.INSERAT_FOTOS_MAX};" in quelle


def test_03_die_einstellungen_erreichen_den_container():
    from tests.test_haertung_20260919 import _compose_umgebung
    umgebung = _compose_umgebung()
    for name in ("INSERAT_BESCHREIBUNG_MAX", "INSERAT_FOTOS_MAX",
                 "INSERAT_LAUFZEIT_TAGE"):
        assert name in umgebung, name


# ------------------------------------------------- Beschreibung 500
def test_04_beschreibung_ueber_500_wird_abgelehnt():
    from pydantic import ValidationError
    R.ListingUpdateIn(description="x" * 500)            # genau die Grenze
    with pytest.raises(ValidationError):
        R.ListingUpdateIn(description="x" * 501)


def test_05_die_oberflaeche_zeigt_einen_zaehler():
    quelle = (WURZEL / "frontend" / "src" / "pages" / "app"
              / "Inserat.jsx").read_text(encoding="utf-8")
    assert 'data-testid="beschreibung-zaehler"' in quelle
    assert "maxLength={BESCHREIBUNG_MAX}" in quelle, \
        "das Feld soll gar nicht erst mehr annehmen"


# ------------------------------------------------------ Fotos: 10
def test_06_hoechstens_zehn_fotos():
    q = inspect.getsource(R)
    assert "> 40:" not in q, "die alte 40er-Grenze ist weg"
    assert q.count("> INSERAT_FOTOS_MAX") >= 2, \
        "Upload UND Uebernahme aus dem Abholbericht"


def test_07_keine_portal_fotos_in_neuen_inseraten():
    """Der Kern der Aenderung: Bilder des Verkaeufers bleiben bei ihm."""
    q = inspect.getsource(R)
    stelle = q.split('"photos": {')[1][:400]
    assert '"einkauf_urls": []' in stelle, (
        "neue Inserate duerfen keine Fotos aus dem Portal-Inserat mehr "
        "mitbekommen")
    assert '"mode": "neu"' in stelle
    assert "data.get(\"image_urls\")" not in stelle


def test_08_ohne_eigenes_foto_keine_veroeffentlichung():
    q = inspect.getsource(R.publish_listing)
    assert "uploaded_keys" in q and "einkauf_urls" in q
    assert "mindestens ein eigenes Foto" in q
    # Vor dem SCHREIBEN des neuen Status, nicht danach. (Die erste
    # Fundstelle von "veroeffentlicht" ist die Doppelklick-Erkennung ganz
    # oben — die soll davor bleiben und gibt ein schon veroeffentlichtes
    # Inserat unveraendert zurueck.)
    assert q.index("mindestens ein eigenes Foto") < q.index('{"$set": {"status": "veroeffentlicht"')


def test_09_altbestand_darf_weiter_veroeffentlichen():
    """Inserate, die ihre Einkaufsfotos schon haben, sollen nicht
    ploetzlich blockiert sein — rueckwirkend wird nichts geloescht."""
    q = inspect.getsource(R.publish_listing)
    stelle = q.split("_fotos = l.get(\"photos\") or {}")[1][:300]
    assert 'or _fotos.get("einkauf_urls")' in stelle


def test_10_die_oberflaeche_schneidet_selbst_ab():
    quelle = (WURZEL / "frontend" / "src" / "pages" / "app"
              / "Inserat.jsx").read_text(encoding="utf-8")
    teil = quelle.split("const uploadPhotos")[1][:900]
    assert "FOTOS_MAX - (l.photos?.uploaded_keys || []).length" in teil
    assert "slice(0, frei)" in teil
    assert "toast.error" in teil, "und sagt es, statt still zu schlucken"


def test_11_der_umschalter_einkauf_beide_ist_weg():
    quelle = (WURZEL / "frontend" / "src" / "pages" / "app"
              / "Inserat.jsx").read_text(encoding="utf-8")
    assert '"Einkauf + neue"' not in quelle
    assert 'data-testid="fotos-regel"' in quelle
    assert "werden nicht übernommen" in quelle


# ------------------------------------------- Laufzeit: 3 Wochen
def test_12_abgelaufene_inserate_verschwinden(welt):
    db = welt.db

    async def lauf():
        import cleanup_service as CS
        jetzt = datetime.now(timezone.utc)
        await db.resale_listings.insert_many([
            # 22 Tage alt -> weg
            {"id": "alt", "dealer_id": "f1", "status": "veroeffentlicht",
             "vehicle_id": "v1", "photos": {"uploaded_keys": []},
             "published_at": (jetzt - timedelta(days=22)).isoformat()},
            # 20 Tage alt -> bleibt
            {"id": "jung", "dealer_id": "f1", "status": "veroeffentlicht",
             "vehicle_id": "v2", "photos": {"uploaded_keys": []},
             "published_at": (jetzt - timedelta(days=20)).isoformat()},
            # reserviert: da verhandelt jemand -> bleibt, egal wie alt
            {"id": "reserviert", "dealer_id": "f1", "status": "reserviert",
             "vehicle_id": "v3", "photos": {"uploaded_keys": []},
             "published_at": (jetzt - timedelta(days=99)).isoformat()},
            # verkauft: Beweis-Historie -> bleibt
            {"id": "verkauft", "dealer_id": "f1", "status": "verkauft",
             "vehicle_id": "v4", "photos": {"uploaded_keys": []},
             "published_at": (jetzt - timedelta(days=99)).isoformat()},
            # Entwurf war nie oeffentlich -> bleibt
            {"id": "entwurf", "dealer_id": "f1", "status": "entwurf",
             "vehicle_id": "v5", "photos": {"uploaded_keys": []}},
        ])
        n = await CS.abgelaufene_inserate_entfernen(db, jetzt)
        uebrig = sorted([x["id"] async for x in
                         db.resale_listings.find({}, {"_id": 0, "id": 1})])
        return n, uebrig

    n, uebrig = welt.run(lauf())
    assert n == 1, "nur das 22 Tage alte veroeffentlichte Inserat"
    assert uebrig == ["entwurf", "jung", "reserviert", "verkauft"]


def test_13_der_kauf_bleibt_unberuehrt(welt):
    """Ahmads Bedingung: 'vertrag usw bleibt vom kauf gespeichert,
    nur inserat verschwindet'."""
    db = welt.db

    async def lauf():
        import cleanup_service as CS
        jetzt = datetime.now(timezone.utc)
        await db.resale_listings.insert_one(
            {"id": "alt", "dealer_id": "f1", "status": "veroeffentlicht",
             "vehicle_id": "v1", "photos": {"uploaded_keys": []},
             "published_at": (jetzt - timedelta(days=30)).isoformat()})
        await db.generated_pdfs.insert_one(
            {"id": "c1", "dealer_id": "f1", "vehicle_id": "v1"})
        await db.kaufvorgaenge.insert_one(
            {"id": "kv1", "dealer_id": "f1", "vehicle_id": "v1"})
        await db.vehicles.insert_one(
            {"id": "v1", "dealer_id": "f1", "lifecycle": "bestand"})
        await db.admin_vehicle_data.insert_one({"id": "ad1", "make": "BMW"})
        await CS.abgelaufene_inserate_entfernen(db, jetzt)
        return {n: await db[n].count_documents({}) for n in
                ("resale_listings", "generated_pdfs", "kaufvorgaenge",
                 "vehicles", "admin_vehicle_data")}

    zahlen = welt.run(lauf())
    assert zahlen["resale_listings"] == 0, "die Anzeige ist weg"
    assert zahlen["generated_pdfs"] == 1, "der Kaufvertrag bleibt"
    assert zahlen["kaufvorgaenge"] == 1, "der Kaufvorgang bleibt"
    assert zahlen["vehicles"] == 1, "das Fahrzeug bleibt im Bestand"
    assert zahlen["admin_vehicle_data"] == 1, "die Auto-Daten bleiben"


def test_14_die_fotos_verschwinden_mit(welt, monkeypatch):
    db = welt.db

    async def lauf():
        import cleanup_service as CS
        geloescht = []

        async def _fake(db_, *, key, grund, dealer_id="", ref=None):
            geloescht.append((key, grund))
            return True

        # Rollenprüfung 22.09.2026: per monkeypatch — die direkte Zuweisung
        # blieb nach dem Test stehen und liess spaetere Tests desselben Laufs
        # (Snapshot-Loeschung mit art=...) an der Attrappe scheitern.
        monkeypatch.setattr(CS, "loeschen_oder_vormerken", _fake)
        jetzt = datetime.now(timezone.utc)
        await db.resale_listings.insert_one(
            {"id": "alt", "dealer_id": "f1", "status": "veroeffentlicht",
             "vehicle_id": "v1",
             "photos": {"uploaded_keys": ["resale/a.jpg", "resale/b.jpg"]},
             "published_at": (jetzt - timedelta(days=25)).isoformat()})
        n = await CS.abgelaufene_inserate_entfernen(db, jetzt)
        return n, geloescht

    n, geloescht = welt.run(lauf())
    assert n == 1
    assert [k for k, _ in geloescht] == ["resale/a.jpg", "resale/b.jpg"]
    assert all(g == "inserat_laufzeit_abgelaufen" for _, g in geloescht)


def test_15_der_aufraeumlauf_ruft_es_auf():
    import cleanup_service as CS
    q = inspect.getsource(CS.marktplatz_rotieren)
    assert "abgelaufene_inserate_entfernen(db, now)" in q
    assert "inserate_abgelaufen" in q, "und zaehlt es mit"


def test_16_reserviert_bleibt_mit_begruendung():
    """Eine bewusste Entscheidung gehoert in den Code, nicht in den Kopf."""
    import cleanup_service as CS
    q = inspect.getsource(CS.abgelaufene_inserate_entfernen)
    assert "reserviert" in q and "verhandelt" in q
    assert '"status": "veroeffentlicht"' in q, "nur veroeffentlichte laufen ab"
