# -*- coding: utf-8 -*-
"""Rollenpruefung 22.09.2026, Welle 2 — Uebergaben an Team markt_haendler
(routes/resale.py).

In-Prozess gegen eine Wegwerf-Datenbank (Fixture aus
test_rp_markt_haendler_20260922.py).

  RP-507 (2)       leeres Zahlenfeld wird None statt ""
  RP-505           Titelvorschlag ohne Kontaktangaben aus model_description
  RP-506           data.fuel als Kraftstoffcode (Anlegen, Korrektur im Editor)
  RP-092/191/342   Entwurf hebt die alte Bestandsfrist auf; Loeschen -> neue Frist
  RP-533           unbrauchbares Einzelfoto wird uebersprungen und gemeldet
  RP-550           Speichern der Inseratsfotos im eigenen Speicher-Pool
  RP-519           laeuft_ab_am in Liste und Editor
  RP-491           Antwort-Form der Kaufanfragen-Seite passt zu InterestAnswerIn
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_rp_markt_haendler_20260922 import (  # noqa: E402,F401
    _fehler, _jpeg_b64, _vor, welt)


# ============================================================ RP-507 (2)
def test_rp507_leeres_zahlenfeld_wird_none(welt):
    R, db = welt.R, welt.db
    f = R._fahrzeugwert_bereinigen
    assert f("mileage", "") == (True, None)
    assert f("power_ps", "   ") == (True, None)
    assert f("accident_free", "") == (True, ""), "Textfelder bleiben Text"
    vid, lid = f"v1_{welt.s}", f"l1_{welt.s}"
    welt.run(db.vehicles.insert_one(welt.fahrzeug(vid, "verkaufsentwurf")))
    welt.run(db.resale_listings.insert_one(welt.inserat(lid, vid, "entwurf")))
    welt.run(R.update_listing(lid, R.ListingUpdateIn(data={"mileage": "", "power_ps": ""}),
                              welt.chef))
    d = welt.run(db.resale_listings.find_one({"id": lid}))["data"]
    assert d["mileage"] is None and d["power_ps"] is None


# ============================================================ RP-505
def test_rp505_titel_ohne_kontaktangaben(welt):
    R, db = welt.R, welt.db
    assert R._build_title({"make_label": "VW", "model_label": "Golf",
                           "model_description": "Golf VII 1.4 TSI Highline"}) \
        == "VW Golf VII 1.4 TSI Highline"
    for text in ("Golf, Tel. 0151/98765432", "Golf 0171 1234567", "golf@privat.de",
                 "Golf www.mein-auto.de", "Golf WhatsApp bitte", "x" * 90):
        titel = R._build_title({"make_label": "VW", "model_label": "Golf",
                                "model_description": text})
        assert titel == "VW Golf", (text, titel)
    vid = f"v2_{welt.s}"
    welt.run(db.vehicles.insert_one(welt.fahrzeug(vid, data={
        "make_label": "VW", "model_label": "Golf", "mileage": 90000,
        "model_description": "Golf TOP Zustand Tel 0171/1234567"})))
    l = welt.run(R.create_draft(vid, welt.chef))
    assert "0171" not in l["title"] and l["title"].startswith("VW Golf")


# ============================================================ RP-506
def test_rp506_kraftstoffcode_beim_anlegen_und_korrigieren(welt):
    R, db = welt.R, welt.db
    vid = f"v3_{welt.s}"
    welt.run(db.vehicles.insert_one(welt.fahrzeug(vid, data={
        "make_label": "Toyota", "model_label": "Yaris", "mileage": 50000,
        "fuel": "Elektro/Benzin", "fuel_label": "Elektro/Benzin"})))
    l = welt.run(R.create_draft(vid, welt.chef))
    assert l["data"]["fuel"] == "HYBRID"
    # Haendler korrigiert die Beschriftung -> der Code zieht mit
    welt.run(db.resale_listings.update_one({"id": l["id"]}, {"$set": {"status": "entwurf"}}))
    welt.run(R.update_listing(l["id"], R.ListingUpdateIn(data={"fuel_label": "Diesel"}),
                              welt.chef))
    assert welt.run(db.resale_listings.find_one({"id": l["id"]}))["data"]["fuel"] == "DIESEL"
    # Speichern ohne Aenderung der Beschriftung laesst den Code stehen
    welt.run(R.update_listing(l["id"], R.ListingUpdateIn(data={"fuel_label": "Diesel",
                                                               "color": "rot"}), welt.chef))
    assert welt.run(db.resale_listings.find_one({"id": l["id"]}))["data"]["fuel"] == "DIESEL"
    # geleerte/unerkannte Beschriftung -> kein Code mehr (Filter nimmt die Beschriftung)
    welt.run(R.update_listing(l["id"], R.ListingUpdateIn(data={"fuel_label": ""}), welt.chef))
    assert "fuel" not in welt.run(db.resale_listings.find_one({"id": l["id"]}))["data"]
    # unerkannter Rohwert beim Anlegen bleibt stehen
    d = {"fuel": "Holzvergaser", "fuel_label": ""}
    R._kraftstoff_normieren(d)
    assert d["fuel"] == "Holzvergaser"


# ============================================================ RP-092/191/342
def test_rp092_entwurf_hebt_bestandsfrist_auf_loeschen_setzt_neue(welt):
    R, db = welt.R, welt.db
    vid = f"v4_{welt.s}"
    alt = _vor(days=3)                       # laengst abgelaufene Frist
    welt.run(db.vehicles.insert_one(welt.fahrzeug(
        vid, "bestand", bestand={"saved_at": _vor(days=53), "expires_at": alt, "costs": []})))
    l = welt.run(R.create_draft(vid, welt.chef))
    v = welt.run(db.vehicles.find_one({"id": vid}))
    assert v["lifecycle"] == "verkaufsentwurf"
    assert v["bestand"]["expires_at"] is None, "keine alte 50-Tage-Frist am Entwurf"
    assert v["bestand"]["saved_at"], "Aufnahmedatum bleibt"
    # Inserat loeschen -> Fahrzeug zurueck in den Bestand mit NEUER Frist
    welt.run(R.delete_listing(l["id"], welt.chef))
    v = welt.run(db.vehicles.find_one({"id": vid}))
    assert v["lifecycle"] == "bestand"
    neu = datetime.fromisoformat(v["bestand"]["expires_at"])
    rest = neu - datetime.now(timezone.utc)
    assert timedelta(days=49) < rest <= timedelta(days=50, minutes=1), rest
    assert v["bestand"].get("inserat_beendet_am")


def test_rp092_fahrzeug_ohne_bestand_bleibt_unberuehrt(welt):
    R, db = welt.R, welt.db
    vid = f"v5_{welt.s}"
    welt.run(db.vehicles.insert_one(welt.fahrzeug(vid, "abgeholt")))
    welt.run(R.create_draft(vid, welt.chef))
    v = welt.run(db.vehicles.find_one({"id": vid}))
    assert "bestand" not in v, "kein leeres bestand-Objekt anlegen"


# ============================================================ RP-533 / RP-550
def test_rp533_unbrauchbares_einzelfoto_wird_uebersprungen(welt):
    R, db = welt.R, welt.db
    lid = f"l6_{welt.s}"
    welt.run(db.resale_listings.insert_one(welt.inserat(
        lid, f"v6_{welt.s}", "entwurf",
        photos={"mode": "neu", "einkauf_urls": [], "uploaded_keys": []})))
    heic = "data:image/heic;base64,AAAAHGZ0eXBoZWljAAAAAG1pZjFoZWlj"
    r = welt.run(R.upload_photos(lid, R.PhotoUploadIn(
        photos_b64=[_jpeg_b64(), heic, _jpeg_b64()]), welt.chef))
    assert r["total"] == 2 and len(r["uploaded"]) == 2
    assert [a["index"] for a in r["abgelehnt"]] == [1]
    assert r["abgelehnt"][0]["grund"]
    assert len(welt.run(db.resale_listings.find_one({"id": lid}))["photos"]["uploaded_keys"]) == 2
    # Nur Unbrauchbares -> weiter 400 mit Grund, nichts gespeichert
    e = _fehler(welt, R.upload_photos(lid, R.PhotoUploadIn(photos_b64=[heic]), welt.chef))
    assert e.status_code == 400 and "Foto konnte nicht gespeichert werden" in e.detail
    e = _fehler(welt, R.upload_photos(lid, R.PhotoUploadIn(photos_b64=[heic, "abc"]), welt.chef))
    assert e.status_code == 400 and "Foto 1" in e.detail and "Foto 2" in e.detail
    assert len(welt.run(db.resale_listings.find_one({"id": lid}))["photos"]["uploaded_keys"]) == 2


def test_rp550_speichern_im_eigenen_pool_und_ausfall_gibt_503(welt, monkeypatch):
    R, db = welt.R, welt.db
    import storage_service
    lid = f"l7_{welt.s}"
    welt.run(db.resale_listings.insert_one(welt.inserat(
        lid, f"v7_{welt.s}", "entwurf",
        photos={"mode": "neu", "einkauf_urls": [], "uploaded_keys": []})))
    aufrufe = []
    echt = storage_service.speicher_aufruf

    async def _merken(funktion, *args):
        aufrufe.append(getattr(funktion, "__name__", ""))
        return await echt(funktion, *args)
    monkeypatch.setattr(storage_service, "speicher_aufruf", _merken)
    welt.run(R.upload_photos(lid, R.PhotoUploadIn(photos_b64=[_jpeg_b64()]), welt.chef))
    assert aufrufe == ["_alle_speichern"]

    # Speicher faellt nach dem ersten Foto aus -> 503, Halbgespeichertes weg
    gespeichert, weggeraeumt = [], []
    echt_save = storage_service.storage.save

    def _save(key, raw):
        if gespeichert:
            raise RuntimeError("R2 haengt")
        gespeichert.append(key)
        return echt_save(key, raw)

    async def _weg(db_, *, key, **k):
        weggeraeumt.append(key)
        return True
    monkeypatch.setattr(storage_service.storage, "save", _save)
    monkeypatch.setattr(storage_service, "loeschen_oder_vormerken", _weg)
    e = _fehler(welt, R.upload_photos(lid, R.PhotoUploadIn(
        photos_b64=[_jpeg_b64(), _jpeg_b64()]), welt.chef))
    assert e.status_code == 503 and "Fotospeicher" in e.detail
    assert weggeraeumt == gespeichert and len(gespeichert) == 1
    assert len(welt.run(db.resale_listings.find_one({"id": lid}))["photos"]["uploaded_keys"]) == 1


# ============================================================ RP-519
def test_rp519_laeuft_ab_am_in_liste_und_editor(welt):
    R, db = welt.R, welt.db
    vid, lid = f"v8_{welt.s}", f"l8_{welt.s}"
    erst = datetime.now(timezone.utc) - timedelta(days=5)
    welt.run(db.vehicles.insert_one(welt.fahrzeug(vid, "veroeffentlicht")))
    welt.run(db.resale_listings.insert_one(welt.inserat(
        lid, vid, "veroeffentlicht", published_at=erst.isoformat())))
    erwartet = (erst + timedelta(days=R.INSERAT_LAUFZEIT_TAGE)).isoformat()
    l = welt.run(R.get_listing(lid, welt.chef))
    assert l["laeuft_ab_am"] == erwartet == l["laufzeit_bis"]
    from fastapi import Response
    liste = welt.run(R.list_listings(Response(), welt.chef))
    eintrag = next(x for x in liste if x["id"] == lid)
    assert eintrag["laeuft_ab_am"] == erwartet
    # nie veroeffentlicht -> kein Datum
    lid2 = f"l9_{welt.s}"
    welt.run(db.resale_listings.insert_one(welt.inserat(lid2, f"v9_{welt.s}", "entwurf")))
    assert welt.run(R.get_listing(lid2, welt.chef))["laeuft_ab_am"] is None


# ============================================================ RP-491
def test_rp491_antwortform_der_oberflaeche_passt_zum_modell():
    import importlib
    M = importlib.import_module("routes.marketplace")
    # So schickt Anfragen.jsx (antwortDaten) — null muss als gesetzt gelten.
    body = M.InterestAnswerIn(**{"action": "akzeptieren", "message": "",
                                 "erwarteter_status": "offen", "erwarteter_betrag": None})
    assert "erwarteter_betrag" in body.model_fields_set and body.erwarteter_betrag is None
    body = M.InterestAnswerIn(**{"action": "gegenangebot", "message": "x",
                                 "erwarteter_status": "gegenangebot_kaeufer",
                                 "counter_offer": 18000})
    assert "erwarteter_betrag" not in body.model_fields_set
    with pytest.raises(Exception):
        M.InterestAnswerIn(**{"action": "akzeptieren", "erwarteter_status": "x" * 41})
