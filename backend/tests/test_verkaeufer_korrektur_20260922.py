# -*- coding: utf-8 -*-
"""Verkäuferdaten eines verschickten Vertrags korrigieren (Entscheidung
Ahmad 22.09.2026, Rollenprüfung RP-481).

Vorher: Tippfehler im Namen oder falsche Anschrift in einem verschickten
Vertrag waren nur durch Löschen zu beheben (das nahm auch das Protokoll mit).
Jetzt: PUT /contracts/{id}/verkaeufer erzeugt eine neue Fassung über denselben
Weg wie der verschobene Termin (Archiv, Compare-and-Set), zieht den offenen
Termin mit und leert im Protokoll-Entwurf einen noch alten Namen.
In-Prozess mit der Wegwerf-Welt aus test_befunde_runde17_termine.
"""
import base64
import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_befunde_runde17_termine import _jetzt, _module, welt  # noqa: E402,F401


def _pdf_text(pdf: bytes) -> str:
    from pypdf import PdfReader
    return " ".join("\n".join((p.extract_text() or "")
                              for p in PdfReader(io.BytesIO(pdf)).pages).split())


def _auto_daten_aus(monkeypatch):
    AD = _module("auto_daten")

    async def _nichts(*a, **k):
        return None
    monkeypatch.setattr(AD, "nachfuehren", _nichts)


def _vertrag(w, cid, **extra):
    doc = w.vertrag(cid)
    doc["contract_data"].update({
        "seller_address": "Alte Straße 1", "seller_zip": "10115", "seller_city": "Berlin",
        "purchase_price": 12500, "contract_no": "KV-VK-1"})
    doc["purchase_price"] = 12500
    doc["contract_no"] = "KV-VK-1"
    doc.update(extra)
    return doc


def _termin(w, appt_id, cid, status="offen", **extra):
    doc = {"id": appt_id, "dealer_id": w.dealer_id, "created_by": w.chef["id"],
           "contract_id": cid, "status": status, "seller_name": "Vera Verkauf",
           "seller_phone": "0170 1111111", "seller_email": "vera@e2etest-mail.de",
           "pickup_address": "Alte Straße 1 10115 Berlin", "pickup_date": "2099-09-10",
           "created_at": _jetzt()}
    doc.update(extra)
    return doc


NEU = {"seller_name": "Vera Verkauf-Müller", "seller_address": "Neue Allee 7",
       "seller_zip": "20095", "seller_city": "Hamburg", "seller_phone": "0170 2222222",
       "seller_email": "vera.neu@e2etest-mail.de", "id_document": "L01X2Y3Z4"}


def test_01_korrektur_erzeugt_neue_fassung_und_zieht_termin_mit(welt, monkeypatch):
    C = _module("routes.contracts")
    w, db = welt.w, welt.db
    _auto_daten_aus(monkeypatch)
    cid, offen, zu = f"cvk_{w.s}", f"a_offen_{w.s}", f"a_zu_{w.s}"

    async def lauf():
        await db.generated_pdfs.insert_one(_vertrag(w, cid, status="versendet", send_status=[
            {"channel": "email", "version": 1, "zustellung": "versendet"}]))
        await db.appointments.insert_many([
            _termin(w, offen, cid),
            _termin(w, zu, cid, status="abgeholt"),
        ])
        await db.pickup_protocols.insert_many([
            # (appointment_id, version) ist eindeutig -> zwei Fassungen
            {"id": f"p_offen_{w.s}", "appointment_id": offen, "dealer_id": w.dealer_id,
             "version": 1, "status": "entwurf", "seller_name": "Vera Verkauf"},
            {"id": f"p_eigen_{w.s}", "appointment_id": offen, "dealer_id": w.dealer_id,
             "version": 2, "status": "entwurf", "seller_name": "Vor Ort korrigiert"},
        ])
        body = C.VerkaeuferKorrekturIn(**NEU)
        antwort = await C.verkaeufer_korrigieren(cid, body, user=w.chef)
        doc = await db.generated_pdfs.find_one({"id": cid}, {"_id": 0})
        archiv = await db.generated_pdf_versions.find_one({"contract_id": cid, "version": 1},
                                                          {"_id": 0})
        t_offen = await db.appointments.find_one({"id": offen}, {"_id": 0})
        t_zu = await db.appointments.find_one({"id": zu}, {"_id": 0})
        p_alt = await db.pickup_protocols.find_one({"id": f"p_offen_{w.s}"}, {"_id": 0})
        p_eigen = await db.pickup_protocols.find_one({"id": f"p_eigen_{w.s}"}, {"_id": 0})
        return antwort, doc, archiv, t_offen, t_zu, p_alt, p_eigen

    antwort, doc, archiv, t_offen, t_zu, p_alt, p_eigen = welt.run(lauf())
    assert antwort["geaendert"] is True and antwort["version"] == 2
    assert antwort["verkaeufer"]["seller_name"] == NEU["seller_name"]
    assert antwort["termine_aktualisiert"] == 1
    # Vertrag: neue Fassung, Daten oben und in contract_data, Status "neu erstellt"
    assert doc["version"] == 2 and doc["status"] == "neu erstellt"
    for k, v in NEU.items():
        assert doc["contract_data"][k] == v, k
    assert doc["seller_name"] == NEU["seller_name"]
    assert doc["seller_phone"] == NEU["seller_phone"]
    assert doc["seller_email"] == NEU["seller_email"]
    assert doc["contract_data"]["fassung"] == 2, "Fassungskennzeichen (RP-494)"
    # Archiv: die alte Fassung bleibt als Beleg
    assert archiv and archiv["contract_data"]["seller_name"] == "Vera Verkauf"
    assert archiv["grund"] == "verkaeufer_korrigiert"
    # PDF beider Fassungen nennt den neuen Namen und die neue Anschrift
    for feld in ("pdf_b64", "pdf_digital_b64"):
        text = _pdf_text(base64.b64decode(doc[feld]))
        assert "Vera Verkauf-Müller" in text and "Neue Allee 7" in text, feld
    # Offener Termin mitgezogen, abgeschlossener bleibt als Beleg
    assert t_offen["seller_name"] == NEU["seller_name"]
    assert t_offen["seller_phone"] == NEU["seller_phone"]
    assert t_offen["pickup_address"] == "Neue Allee 7 20095 Hamburg"
    assert t_offen.get("seller_name_geaendert_am")
    assert t_zu["seller_name"] == "Vera Verkauf" and t_zu["pickup_address"] == "Alte Straße 1 10115 Berlin"
    # Entwurf mit dem alten Terminnamen verliert ihn (RP-082 nimmt den neuen),
    # ein vor Ort korrigierter Name bleibt
    assert "seller_name" not in p_alt
    assert p_eigen["seller_name"] == "Vor Ort korrigiert"


def test_02_gleiche_daten_aendern_nichts(welt, monkeypatch):
    C = _module("routes.contracts")
    w, db = welt.w, welt.db
    _auto_daten_aus(monkeypatch)
    cid = f"cvk2_{w.s}"

    async def lauf():
        doc = _vertrag(w, cid)
        await db.generated_pdfs.insert_one(doc)
        cd = doc["contract_data"]
        body = C.VerkaeuferKorrekturIn(
            seller_name=" Vera Verkauf ", seller_address=cd["seller_address"],
            seller_zip=cd["seller_zip"], seller_city=cd["seller_city"],
            seller_phone=cd["seller_phone"], seller_email=cd["seller_email"])
        antwort = await C.verkaeufer_korrigieren(cid, body, user=w.chef)
        nachher = await db.generated_pdfs.find_one({"id": cid}, {"_id": 0, "version": 1})
        archiv = await db.generated_pdf_versions.count_documents({"contract_id": cid})
        return antwort, nachher, archiv

    antwort, nachher, archiv = welt.run(lauf())
    assert antwort == {"geaendert": False, "version": 1}
    assert nachher["version"] == 1 and archiv == 0


def test_03_sucher_nur_eigene_vertraege_und_name_pflicht(welt, monkeypatch):
    from fastapi import HTTPException
    C = _module("routes.contracts")
    w, db = welt.w, welt.db
    _auto_daten_aus(monkeypatch)
    cid = f"cvk3_{w.s}"

    async def lauf():
        await db.generated_pdfs.insert_one(_vertrag(w, cid))    # gehoert dem Chef
        try:
            await C.verkaeufer_korrigieren(cid, C.VerkaeuferKorrekturIn(**NEU), user=w.sucher)
            return None
        except HTTPException as exc:
            return exc.status_code

    assert welt.run(lauf()) == 404, "fremder Vertrag: fuer den Sucher nicht sichtbar"
    with pytest.raises(ValueError):
        C.VerkaeuferKorrekturIn(seller_name="   ")


def test_04_korrektur_ueberlebt_die_neuerzeugung_nach_der_abholung(welt, monkeypatch):
    """Nach der Abholung baut die Neuerzeugung aus dem Stand VOR der Abholung
    auf (RP-479). Die Verkaeuferdaten muessen dabei aus der AKTUELLEN Fassung
    kommen — sonst kaeme mit der naechsten Protokollversion der alte Name
    zurueck."""
    C = _module("routes.contracts")
    w, db = welt.w, welt.db
    _auto_daten_aus(monkeypatch)
    cid = f"cvk4_{w.s}"
    assert set(C.VERKAEUFER_FELDER) <= set(C._AUS_AKTUELLER_FASSUNG)

    async def lauf():
        doc = _vertrag(w, cid)
        basis = dict(doc["contract_data"])                      # Stand vor der Abholung
        doc["contract_data"].update(NEU)                        # korrigiert (v2)
        doc["version"] = 2
        doc["vertrag_vor_abholung"] = basis
        doc["nach_abholung_protokoll_id"] = f"p1_{w.s}"
        await db.generated_pdfs.insert_one(doc)
        ok = await C.regenerate_contract_for_pickup(
            contract_id=cid, dealer_id=w.dealer_id, user=w.chef, neuer_preis=11900,
            grund="abholung_abgeschlossen", protokoll_id=f"p2_{w.s}")
        return ok, await db.generated_pdfs.find_one({"id": cid}, {"_id": 0, "contract_data": 1})

    ok, doc = welt.run(lauf())
    assert ok is True
    assert doc["contract_data"]["seller_name"] == NEU["seller_name"]
    assert doc["contract_data"]["seller_city"] == "Hamburg"
    assert doc["contract_data"]["purchase_price"] == 11900


def test_05_route_und_oberflaeche_sind_verdrahtet():
    quelle = (Path(__file__).resolve().parents[1] / "routes" / "contracts.py").read_text(encoding="utf-8")
    assert '@router.put("/contracts/{contract_id}/verkaeufer")' in quelle
    projekt = Path(__file__).resolve().parents[2]
    archiv = (projekt / "frontend" / "src" / "pages" / "app" / "PDFArchiv.jsx").read_text(encoding="utf-8")
    assert "VerkaeuferKorrekturDialog" in archiv and "verkaeufer-korrektur-${it.id}" in archiv
    dialog = (projekt / "frontend" / "src" / "components" / "VerkaeuferKorrekturDialog.jsx").read_text(encoding="utf-8")
    assert "api.put(`/contracts/${contract.id}/verkaeufer`" in dialog
