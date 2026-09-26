# -*- coding: utf-8 -*-
"""Pruefung 14.09.2026 — Bereich "beweise" (Beweisdokument je Inserat).

  B4   Der rohe Ausnahmetext (Pfade, Hostnamen) stand in `fehler` und ging
       ueber oeffentlich() an die Oberflaeche. Jetzt nur Sachtexte; der Rest
       in fehler_intern.
  B7   Inserat mit Fotos, keines ladbar -> vorher "fertig" ohne ein Bild.
       Jetzt erneut versuchen; erst der letzte Versuch stellt ohne Fotos fertig.
  B9   Ein stornierter / nicht abgeholter Termin hielt das Dokument fuer
       immer. Jetzt halten nur offene Termine.
  B10  Die Pruefsumme im Antwortkopf war nur der gespeicherte Wert. Jetzt wird
       die Datei gehasht; Abweichung -> 409 + Betriebsalarm.
  C22/C23  Fahrer-Zugriff nur ueber angenommene, nicht stornierte Termine.

Welt aus test_beweis_service (lokale Mongo, Fotos ohne Netz).
"""
import hashlib
import os
import sys
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_beweis_service import _alt_fertig, _jetzt, _vormerken, welt  # noqa: E402,F401


def _fehler(w, coro):
    with pytest.raises(HTTPException) as e:
        w.run(coro)
    return e.value.status_code, e.value.detail


def test_b4_fremder_ausnahmetext_bleibt_intern(welt, monkeypatch):
    import beweis_service as BS
    ck = welt.cache("b4")
    _vormerken(welt, ck)
    doc = welt.run(BS._beanspruchen(welt.db))
    assert doc["cache_key"] == ck

    async def _kracht(db, d):
        raise OSError("C:\\geheim\\pfad\\storage.sock: connection refused by host db-intern-7")
    monkeypatch.setattr(BS, "beweis_erzeugen", _kracht)
    welt.run(BS._bearbeiten(welt.db, doc))
    z = welt.run(welt.db.inserat_beweise.find_one({"cache_key": ck}, {"_id": 0}))
    assert z["status"] == "offen" and z["fehler"] == "Technischer Fehler bei der Erzeugung"
    assert "db-intern-7" in z["fehler_intern"]
    aussen = BS.oeffentlich(z)
    assert aussen["fehler"] == "Technischer Fehler bei der Erzeugung"
    assert "fehler_intern" not in aussen and "db-intern-7" not in str(aussen)
    # Eigene Sachtexte gehen weiter durch (Zeitlimit, fehlende Daten)
    async def _eigen(db, d):
        raise BS.BeweisFehler("Inseratsdaten fehlen im Zwischenspeicher")
    monkeypatch.setattr(BS, "beweis_erzeugen", _eigen)
    welt.run(welt.db.inserat_beweise.update_one(
        {"cache_key": ck}, {"$set": {"status": "in_arbeit", "bearbeiter": doc["bearbeiter"]}}))
    welt.run(BS._bearbeiten(welt.db, doc))
    z = welt.run(welt.db.inserat_beweise.find_one({"cache_key": ck}, {"_id": 0}))
    assert z["fehler"] == "Inseratsdaten fehlen im Zwischenspeicher"


def test_b7_ohne_ladbare_fotos_erst_wiederholen_dann_ohne_fotos_fertig(welt, monkeypatch):
    import beweis_service as BS
    import bild_proxy

    async def _nichts(url, kante=800, qualitaet=0):
        return None
    monkeypatch.setattr(bild_proxy, "laden_fuer_pdf", _nichts)
    ck = welt.cache("b7")                     # zwei Fotos im Inserat
    _vormerken(welt, ck)
    doc = welt.run(BS._beanspruchen(welt.db))
    assert doc["cache_key"] == ck and doc["versuche"] == 1
    welt.run(BS._bearbeiten(welt.db, doc))
    z = welt.run(welt.db.inserat_beweise.find_one({"cache_key": ck}, {"_id": 0}))
    assert z["status"] == "offen" and "fotos" in z["fehler"].lower(), z
    # Letzter Versuch: fertig, aber sichtbar ohne Fotos
    doc["versuche"] = BS.MAX_VERSUCHE
    welt.run(welt.db.inserat_beweise.update_one(
        {"cache_key": ck}, {"$set": {"status": "in_arbeit", "bearbeiter": doc["bearbeiter"],
                                     "versuche": BS.MAX_VERSUCHE}}))
    welt.run(BS._bearbeiten(welt.db, doc))
    z = welt.run(welt.db.inserat_beweise.find_one({"cache_key": ck}, {"_id": 0}))
    assert z["status"] == "fertig" and z["fotos_eingebettet"] == 0 and z["fotos_gesamt"] == 2, z


def test_b7_inserat_ohne_fotos_wird_sofort_fertig(welt):
    import beweis_service as BS
    ck = welt.cache("b7o", bilder=())
    _vormerken(welt, ck)
    doc = welt.run(BS._beanspruchen(welt.db))
    welt.run(BS._bearbeiten(welt.db, doc))
    z = welt.run(welt.db.inserat_beweise.find_one({"cache_key": ck}, {"_id": 0}))
    assert z["status"] == "fertig" and z["fotos_gesamt"] == 0


def test_b9_nur_offene_termine_halten_das_dokument(welt):
    import beweis_service as BS
    for n, status in (("st", "storniert"), ("na", "nicht abgeholt"), ("of", "offen"),
                      ("ve", "verschoben")):
        _alt_fertig(welt, n)
        welt.run(welt.db.vehicles.insert_one({"id": f"v{welt.s}{n}", "dealer_id": welt.dealer_id,
                                              "lifecycle": "verglichen",
                                              "inserat_schluessel": welt.ck(n)}))
        welt.run(welt.db.appointments.insert_one({"id": f"a{welt.s}{n}", "dealer_id": welt.dealer_id,
                                                  "vehicle_id": f"v{welt.s}{n}", "status": status}))
    assert welt.run(BS._gehalten(welt.db, welt.ck("st"))) is False
    assert welt.run(BS._gehalten(welt.db, welt.ck("na"))) is False
    assert welt.run(BS._gehalten(welt.db, welt.ck("of"))) is True
    assert welt.run(BS._gehalten(welt.db, welt.ck("ve"))) is True
    welt.run(BS.beweise_verfallen(welt.db, altbestand_filter={"dealer_id": welt.dealer_id}))
    st = {n: welt.run(welt.db.inserat_beweise.find_one({"cache_key": welt.ck(n)}))["status"]
          for n in ("st", "na", "of", "ve")}
    assert st == {"st": "geloescht", "na": "geloescht", "of": "fertig", "ve": "fertig"}, st


def test_b10_pruefsumme_wird_beim_ausliefern_gegengerechnet(welt, monkeypatch):
    import routes.beweise as RB
    import storage_service as SS
    pdf = b"%PDF-1.4 beweis"

    async def _load(key):
        return pdf
    monkeypatch.setattr(SS, "load_async", _load)
    echt = hashlib.sha256(pdf).hexdigest()
    doc = {"id": f"bew{welt.s}b10", "cache_key": welt.ck("b10"), "quelle": "mobile",
           "item_id": "x", "status": "fertig", "pdf_key": "test/b10.pdf", "pdf_sha256": echt}
    r = welt.run(RB._pdf_antwort(doc))
    assert r.status_code == 200 and r.headers["x-beweis-sha256"] == echt
    # Datei im Speicher veraendert / vertauscht
    code, text = _fehler(welt, RB._pdf_antwort({**doc, "pdf_sha256": "0" * 64}))
    assert code == 409 and "Prüfsumme" in text
    alarm = welt.run(welt.db.betriebsalarme.find_one(
        {"typ": "beweis_pruefsumme_abweichend", "ref": doc["id"], "offen": True}, {"_id": 0}))
    if alarm is None:
        # Ueber ALARM_JE_TYP_MAX offenen Alarmen (lange lokale Test-DB) faltet
        # betrieb.alarm den Einzelalarm in den Sammelalarm "*weitere*".
        alarm = welt.run(welt.db.betriebsalarme.find_one(
            {"typ": "beweis_pruefsumme_abweichend", "ref": "*weitere*", "offen": True,
             "details.letzter_ref": doc["id"][:80]}, {"_id": 0}))
    assert alarm and alarm["details"]["ist"] == echt
    welt.run(welt.db.betriebsalarme.delete_many(
        {"typ": "beweis_pruefsumme_abweichend", "ref": doc["id"]}))
    # Altbestand ohne gespeicherte Pruefsumme: Kopf traegt die errechnete
    r = welt.run(RB._pdf_antwort({**doc, "pdf_sha256": None}))
    assert r.status_code == 200 and r.headers["x-beweis-sha256"] == echt


def test_c22_fahrer_beweis_pdf_nur_bei_angenommener_fahrt(welt, monkeypatch):
    import routes.beweise as RB
    import storage_service as SS

    async def _load(key):
        return b"%PDF-1.4 x"
    monkeypatch.setattr(SS, "load_async", _load)
    pdf_hash = hashlib.sha256(b"%PDF-1.4 x").hexdigest()
    ck = welt.ck("c22")
    welt.run(welt.db.inserat_beweise.insert_one(
        {"id": f"bew{welt.s}c22", "cache_key": ck, "quelle": "mobile", "status": "fertig",
         "pdf_key": "test/c22.pdf", "pdf_sha256": pdf_hash, "alle_keys": [], "erstellt_am": _jetzt()}))
    fahrer = {"id": f"f{welt.s}", "display_name": "F"}
    welt.run(welt.db.dealer_drivers.insert_one(
        {"id": str(uuid.uuid4()), "dealer_id": welt.dealer_id, "driver_account_id": fahrer["id"],
         "added_at": _jetzt()}))
    welt.run(welt.db.vehicles.insert_one({"id": f"v{welt.s}c22", "dealer_id": welt.dealer_id,
                                          "lifecycle": "abholung_geplant", "inserat_schluessel": ck}))
    termin = {"id": f"a{welt.s}c22", "dealer_id": welt.dealer_id, "vehicle_id": f"v{welt.s}c22",
              "driver_id": fahrer["id"], "status": "offen", "zuteilung": "offen"}
    welt.run(welt.db.appointments.insert_one(termin))
    assert _fehler(welt, RB.driver_beweis_pdf(f"bew{welt.s}c22", fahrer))[0] == 404
    welt.run(welt.db.appointments.update_one({"id": termin["id"]},
                                             {"$set": {"zuteilung": "angenommen"}}))
    assert welt.run(RB.driver_beweis_pdf(f"bew{welt.s}c22", fahrer)).status_code == 200
    welt.run(welt.db.appointments.update_one({"id": termin["id"]},
                                             {"$set": {"status": "storniert"}}))
    assert _fehler(welt, RB.driver_beweis_pdf(f"bew{welt.s}c22", fahrer))[0] == 404
