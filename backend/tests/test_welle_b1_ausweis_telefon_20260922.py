# -*- coding: utf-8 -*-
"""Welle B1 (Entscheidungen Ahmad 22.09.2026).

  Ausweisnummer   Der Fahrer traegt vor Ort die Ausweisnummer des Verkaeufers
                  nach: Entwurf (Autosave, getrimmt, max. 60), Protokoll-PDF
                  ("Ausweis-Nr.: …" neben dem Verkaeufer), neue Vertrags-
                  fassung nach der Abholung (Feld "Ausweis"), nie im Audit-
                  Meta, geleert mit den uebrigen Personendaten.
  RP-157 Rest     Verkaeufername im Entwurf (save_protocol) — beim
                  Wiederoeffnen steht er im Protokoll.
  RP-542          Telefon des Fahrers fuer Chef UND Sucher; Fahrer-ID und
                  E-Mail weiter nur fuer den Hauptchef.

In-Prozess mit der Wegwerf-Welt aus test_befunde_runde17_termine; die
PDF-Tests bauen ECHTE PDFs und lesen sie per pypdf zurueck.
"""
import base64
import io
import json
import sys
from pathlib import Path

import pytest
from fastapi import Response

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_befunde_runde17_termine import _PNG_B64, _jetzt, _module, welt  # noqa: E402,F401

AUSWEIS = "L01X2Y3Z4"


def _pdf_text(pdf: bytes) -> str:
    from pypdf import PdfReader
    return " ".join("\n".join((p.extract_text() or "")
                              for p in PdfReader(io.BytesIO(pdf)).pages).split())


def _auto_daten_aus(monkeypatch):
    AD = _module("auto_daten")

    async def _nichts(*a, **k):
        return None
    monkeypatch.setattr(AD, "nachfuehren", _nichts)


# ============================================================ Entwurf
def test_01_ausweisnummer_und_name_im_entwurf(welt):
    P = _module("routes.protocols")
    w, db = welt.w, welt.db
    aid, vid = f"a1_{w.s}", f"v1_{w.s}"

    async def lauf():
        await w.fahrer_anlegen(db)
        await db.vehicles.insert_one(w.fahrzeug(vid))
        await db.appointments.insert_one(w.appt(aid, driver_id=w.driver_id, vehicle_id=vid))
        e = await P.save_protocol(aid, P.ProtocolIn(seller_name="  Max Muster ",
                                                    seller_id_document=f"  {AUSWEIS} "), w.driver)
        g = await P.get_protocol(aid, w.driver)
        # zweites Speichern OHNE die Felder laesst sie stehen (exclude_none)
        await P.save_protocol(aid, P.ProtocolIn(notes="x", revision=e["revision"]), w.driver)
        g2 = await P.get_protocol(aid, w.driver)
        return e, g, g2

    e, g, g2 = welt.run(lauf())
    assert e["seller_name"] == "Max Muster" and e["seller_id_document"] == AUSWEIS
    assert g["protocol"]["seller_name"] == "Max Muster"
    assert g["protocol"]["seller_id_document"] == AUSWEIS
    assert g2["protocol"]["seller_id_document"] == AUSWEIS and g2["protocol"]["notes"] == "x"
    with pytest.raises(ValueError):
        P.ProtocolIn(seller_id_document="x" * 61)
    assert P.verkaeufer_aus_protokoll({"seller_id_document": " L1 "}) == {"id_document": "L1"}
    assert P.verkaeufer_aus_protokoll({}) == {} and P.verkaeufer_aus_protokoll(None) == {}


# ============================================================ Abschluss: PDF + Vertrag
def test_02_abschluss_druckt_ausweis_und_neue_vertragsfassung_traegt_ihn(welt, monkeypatch):
    P = _module("routes.protocols")
    import storage_service
    w, db = welt.w, welt.db
    _auto_daten_aus(monkeypatch)
    aid, vid, pid, cid = f"a2_{w.s}", f"v2_{w.s}", f"p2_{w.s}", f"c2_{w.s}"
    gespeichert = []
    alt_save = storage_service.save_async

    async def save_merken(key, data):
        gespeichert.append((key, data))
        return await alt_save(key, data)
    monkeypatch.setattr(storage_service, "save_async", save_merken)
    fin = P.FinalizeIn(signature_driver_b64=_PNG_B64, signature_seller_b64=_PNG_B64)

    async def lauf():
        await w.fahrer_anlegen(db)
        await db.vehicles.insert_one(w.fahrzeug(vid))
        vertrag = w.vertrag(cid)
        vertrag["contract_data"].update({"seller_address": "Alte Straße 1", "seller_zip": "10115",
                                         "seller_city": "Berlin", "purchase_price": 12500,
                                         "contract_no": "KV-B1-1"})
        vertrag.update({"purchase_price": 12500, "contract_no": "KV-B1-1"})
        await db.generated_pdfs.insert_one(vertrag)
        await db.appointments.insert_one(
            w.appt(aid, driver_id=w.driver_id, vehicle_id=vid, contract_id=cid))
        await db.pickup_protocols.insert_one(w.entwurf(P, aid, pid, seller_id_document=AUSWEIS))
        r = await P.finalize_protocol(aid, fin, w.driver)
        pr = await db.pickup_protocols.find_one({"id": pid}, {"_id": 0})
        doc = await db.generated_pdfs.find_one({"id": cid}, {"_id": 0})
        logs = [x async for x in db.activity_logs.find({"dealer_id": w.dealer_id}, {"_id": 0})]
        return r, pr, doc, logs

    r, pr, doc, logs = welt.run(lauf())
    assert r["ok"] is True and pr["status"] == "final"
    assert pr["seller_id_document"] == AUSWEIS
    # Protokoll-PDF: Ausweisnummer neben dem Verkaeufer
    pdfs = [data for key, data in gespeichert
            if key == pr["pdf_path"] or bytes(data[:4]) == b"%PDF"]
    assert pdfs, "Protokoll-PDF wurde gespeichert"
    text = _pdf_text(pdfs[-1])
    assert "Ausweis-Nr.:" in text and AUSWEIS in text
    # Neue Vertragsfassung: Feld "Ausweis" traegt die Nummer, das PDF druckt sie
    assert doc["version"] == 2 and doc["nach_abholung_protokoll_id"] == pid
    assert doc["contract_data"]["id_document"] == AUSWEIS
    for feld in ("pdf_b64", "pdf_digital_b64"):
        if doc.get(feld):
            assert AUSWEIS in _pdf_text(base64.b64decode(doc[feld])), feld
    assert "pdf_b64" in doc
    # Datenschutz: die Nummer steht in keinem Audit-Eintrag
    assert logs and AUSWEIS not in json.dumps(logs, default=str)


def test_03_korrektur_version_uebernimmt_die_nummer(welt):
    P = _module("routes.protocols")
    w, db = welt.w, welt.db
    aid, vid, pid = f"a3_{w.s}", f"v3_{w.s}", f"p3_{w.s}"

    async def lauf():
        await w.fahrer_anlegen(db)
        await db.vehicles.insert_one(w.fahrzeug(vid))
        await db.appointments.insert_one(w.appt(aid, driver_id=w.driver_id, vehicle_id=vid))
        await db.pickup_protocols.insert_one(
            w.entwurf(P, aid, pid, status="final", seller_id_document=AUSWEIS,
                      finalized_at=_jetzt(), pdf_path="x"))
        await P.start_correction(aid, w.driver)
        return await P.get_protocol(aid, w.driver)

    g = welt.run(lauf())
    assert g["protocol"]["status"] == "entwurf" and g["protocol"]["corrects_version"] == 1
    assert g["protocol"]["seller_id_document"] == AUSWEIS


def test_04_personendaten_loeschung_leert_die_nummer(welt):
    CS = _module("cleanup_service")
    w, db = welt.w, welt.db
    aid, pid = f"a4_{w.s}", f"p4_{w.s}"

    async def lauf():
        await db.pickup_protocols.insert_one(
            {"id": pid, "appointment_id": aid, "dealer_id": w.dealer_id, "version": 1,
             "status": "final", "seller_name": "Vera", "seller_id_document": AUSWEIS,
             "place": "Hannover"})
        await CS._protokolle_pii_entfernen(db, [aid], w.dealer_id, _jetzt())
        return await db.pickup_protocols.find_one({"id": pid}, {"_id": 0})

    p = welt.run(lauf())
    assert p["seller_id_document"] == "" and p["seller_name"] == "" and p["pii_geloescht_at"]


# ============================================================ RP-542
def test_05_fahrer_telefon_fuer_chef_und_sucher(welt):
    A = _module("routes.appointments")
    D = _module("routes.drivers")
    w, db = welt.w, welt.db
    aid, vid = f"a5_{w.s}", f"v5_{w.s}"

    async def lauf():
        await db.users.insert_many(w.konten())
        await w.fahrer_anlegen(db)
        await db.driver_accounts.update_one({"id": w.driver_id}, {"$set": {"phone": "0171 9998877"}})
        await db.vehicles.insert_one(w.fahrzeug(vid, owner=w.sucher["id"]))
        await db.appointments.insert_one(
            w.appt(aid, driver_id=w.driver_id, vehicle_id=vid, created_by=w.sucher["id"]))
        liste_s = await A.list_appointments(Response(), w.sucher)
        einzeln_s = await A.get_appointment(aid, w.sucher)
        liste_c = await A.list_appointments(Response(), w.chef)
        fahrer_s = await D.list_drivers(w.sucher)
        return liste_s, einzeln_s, liste_c, fahrer_s

    liste_s, einzeln_s, liste_c, fahrer_s = welt.run(lauf())
    for eintrag in (next(a for a in liste_s if a["id"] == aid), einzeln_s,
                    next(a for a in liste_c if a["id"] == aid)):
        assert eintrag["driver"]["phone"] == "0171 9998877"
    # Sucher: keine Fahrer-ID, keine E-Mail — Telefon ja
    assert einzeln_s["driver"]["driver_code"] is None and einzeln_s["driver"]["email"] is None
    f = next(d for d in fahrer_s if d["id"] == w.driver_id)
    assert f["phone"] == "0171 9998877" and f["driver_code"] is None and f["email"] is None
