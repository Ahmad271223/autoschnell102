# -*- coding: utf-8 -*-
"""Wunsch Ahmad 01.10.2026: Chef und Sucher aendern einen bestehenden Kaufvertrag JEDERZEIT nachtraeglich
im selben Dialog wie beim Anlegen (vorausgefuellt, alles aenderbar) -> POST /contracts/{id}/neue-fassung
erzeugt eine neue Fassung, die alte bleibt im Archiv. Beispiele: der Chef will nicht auf den Fahrer
warten; der Sucher will nicht denselben Link erneut einfuegen und einen zweiten Vertrag anlegen.

Bleibt: Vertragsnummer, Abholdatum/-uhrzeit (Terminplaner), Preis vor der Abholung, eingefrorene Daten.
In-Prozess mit der Wegwerf-Welt aus test_befunde_runde17_termine."""
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_befunde_runde17_termine import _jetzt, _module, welt  # noqa: E402,F401


def _auto_daten_aus(monkeypatch):
    AD = _module("auto_daten")

    async def _nichts(*a, **k):
        return None
    monkeypatch.setattr(AD, "nachfuehren", _nichts)


def _vertrag(w, cid, **extra):
    doc = w.vertrag(cid)
    doc["contract_data"].update({
        "seller_address": "Alte Straße 1", "seller_zip": "10115", "seller_city": "Berlin",
        "purchase_price": 12500, "contract_no": "KV-NA-1", "pickup_date": "2099-09-10", "pickup_time": "10:00",
        "preis_vor_abholung": 12000, "vehicle_make": "VW", "vehicle_model": "Golf", "dealer_company": "Firma R17",
        "damages": [{"type_key": "delle", "type_label": "Delle", "zone": "Tür vorne links", "view": "links"}],
        "damages_text": "Delle – Tür vorne links", "additional_terms": "Alte Vereinbarung", "logo_key": "logo/alt.png"})
    doc["purchase_price"] = 12500
    doc["contract_no"] = "KV-NA-1"
    doc["make"], doc["model"] = "VW", "Golf"
    doc.update(extra)
    return doc


def _eingaben(C, **extra):
    felder = {"vehicle_id": "v-egal", "seller_name": "Vera Verkauf-Müller", "seller_address": "Neue Allee 7",
              "seller_zip": "20095", "seller_city": "Hamburg", "seller_phone": "0170 2222222",
              "seller_email": "vera.neu@e2etest-mail.de", "purchase_price": 13900.0,
              "contract_no": "ANDERE-NR", "pickup_date": "2099-12-24", "pickup_time": "08:00",
              "vehicle_make": "VW", "vehicle_model": "Golf VIII", "additional_terms": "Neue Vereinbarung",
              "dealer_company": "Firma R17", "dealer_phone": "030 999",
              "damages": [{"type_key": "delle", "type_label": "Delle", "zone": "Tür vorne links", "view": "links"},
                          {"type_key": "kratzer", "type_label": "Kratzer", "zone": "Stoßstange hinten", "view": "hinten"}],
              "damages_text": "Delle – Tür vorne links; Kratzer – Stoßstange hinten"}
    felder.update(extra)
    return C.ContractIn(**felder)


def test_01_chef_aendert_alles_neue_fassung_altes_im_archiv(welt, monkeypatch):
    C = _module("routes.contracts")
    w, db = welt.w, welt.db
    _auto_daten_aus(monkeypatch)
    cid, offen, zu = f"cna_{w.s}", f"a_na_offen_{w.s}", f"a_na_zu_{w.s}"

    async def lauf():
        await db.generated_pdfs.insert_one(_vertrag(w, cid, status="versendet",
                                                    send_status=[{"channel": "email", "version": 1}]))
        await db.appointments.insert_many([
            {"id": offen, "dealer_id": w.dealer_id, "created_by": w.chef["id"], "contract_id": cid, "status": "offen",
             "seller_name": "Vera Verkauf", "pickup_address": "Alte Straße 1 10115 Berlin", "created_at": _jetzt()},
            {"id": zu, "dealer_id": w.dealer_id, "created_by": w.chef["id"], "contract_id": cid, "status": "abgeholt",
             "seller_name": "Vera Verkauf", "created_at": _jetzt()}])
        antwort = await C.vertrag_nachtraeglich_aendern(cid, _eingaben(C), user=w.chef)
        doc = await db.generated_pdfs.find_one({"id": cid}, {"_id": 0})
        archiv = await db.generated_pdf_versions.find_one({"contract_id": cid, "version": 1}, {"_id": 0})
        t_offen = await db.appointments.find_one({"id": offen}, {"_id": 0})
        t_zu = await db.appointments.find_one({"id": zu}, {"_id": 0})
        return antwort, doc, archiv, t_offen, t_zu

    antwort, doc, archiv, t_offen, t_zu = welt.run(lauf())
    assert antwort["geaendert"] is True and antwort["version"] == 2 and antwort["termine_aktualisiert"] == 1
    assert "pdf_b64" not in antwort and "unterschrift_key" not in str(antwort.get("portal") or {})
    cd = doc["contract_data"]
    assert doc["version"] == 2 and doc["status"] == "neu erstellt", "neue Fassung ist noch nicht versendet"
    assert cd["seller_name"] == "Vera Verkauf-Müller" and doc["seller_name"] == "Vera Verkauf-Müller"
    assert cd["purchase_price"] == 13900.0 and doc["purchase_price"] == 13900.0
    assert cd["vehicle_model"] == "Golf VIII" and doc["model"] == "Golf VIII" and doc["filename"]
    assert len(cd["damages"]) == 2 and cd["additional_terms"] == "Neue Vereinbarung"
    assert cd["dealer_phone"] == "030 999"
    # bleibt: Vertragsnummer, Abholtermin (Terminplaner), Preis vor der Abholung, Logo
    assert doc["contract_no"] == "KV-NA-1" and cd["contract_no"] == "KV-NA-1"
    assert cd["pickup_date"] == "2099-09-10" and cd["pickup_time"] == "10:00"
    assert doc["pickup_date"] == "2099-09-10" and cd["preis_vor_abholung"] == 12000
    assert cd["logo_key"] == "logo/alt.png"
    assert cd["fassung"] == 2 and cd["ersetzt_fassung_am"], "Fassungskennzeichen (RP-494)"
    assert doc["nachtraeglich_geaendert_von"] == w.chef["id"] and doc["pdf_b64"] and doc["pdf_digital_b64"]
    # Archiv: die alte Fassung als Beleg, mit Grund
    assert archiv["grund"] == "nachtraeglich_geaendert" and archiv["contract_data"]["seller_name"] == "Vera Verkauf"
    assert archiv["contract_data"]["purchase_price"] == 12500
    # offener Termin zieht Verkaeuferdaten mit, abgeschlossener bleibt
    assert t_offen["seller_name"] == "Vera Verkauf-Müller" and t_offen["pickup_address"] == "Neue Allee 7 20095 Hamburg"
    assert t_zu["seller_name"] == "Vera Verkauf"


def test_02_ohne_aenderung_keine_fassung(welt, monkeypatch):
    C = _module("routes.contracts")
    w, db = welt.w, welt.db
    _auto_daten_aus(monkeypatch)
    cid = f"cna2_{w.s}"

    async def lauf():
        await db.generated_pdfs.insert_one(_vertrag(w, cid))
        erste = await C.vertrag_nachtraeglich_aendern(cid, _eingaben(C), user=w.chef)
        d1 = await db.generated_pdfs.find_one({"id": cid}, {"_id": 0, "contract_data": 1, "version": 1})
        # dieselben Eingaben noch einmal: nichts zu tun
        zweite = await C.vertrag_nachtraeglich_aendern(cid, _eingaben(C), user=w.chef)
        d2 = await db.generated_pdfs.find_one({"id": cid}, {"_id": 0, "contract_data": 1, "version": 1})
        archiv = await db.generated_pdf_versions.count_documents({"contract_id": cid})
        return erste, d1, zweite, d2, archiv

    erste, d1, zweite, d2, archiv = welt.run(lauf())
    assert erste["geaendert"] is True and d1["version"] == 2
    assert zweite == {"geaendert": False, "version": 2} and d2["version"] == 2 and archiv == 1
    assert d2["contract_data"] == d1["contract_data"]


def test_03_sucher_nur_eigene_und_kein_versand_mittendrin(welt, monkeypatch):
    C = _module("routes.contracts")
    w, db = welt.w, welt.db
    _auto_daten_aus(monkeypatch)
    fremd, eigen = f"cna3f_{w.s}", f"cna3e_{w.s}"

    async def lauf():
        await db.generated_pdfs.insert_one(_vertrag(w, fremd))                              # gehoert dem Chef
        await db.generated_pdfs.insert_one(_vertrag(w, eigen, user_id=w.sucher["id"]))
        try:
            await C.vertrag_nachtraeglich_aendern(fremd, _eingaben(C), user=w.sucher)
            fremd_status = None
        except HTTPException as exc:
            fremd_status = exc.status_code
        eigene = await C.vertrag_nachtraeglich_aendern(eigen, _eingaben(C), user=w.sucher)
        return fremd_status, eigene

    fremd_status, eigene = welt.run(lauf())
    assert fremd_status == 404, "fremder Vertrag: fuer den Sucher nicht sichtbar"
    assert eigene["geaendert"] is True and eigene["version"] == 2

    async def _laeuft(_cid):
        return True
    monkeypatch.setattr(C, "_versand_laeuft", _laeuft)
    with pytest.raises(HTTPException) as e:
        welt.run(C.vertrag_nachtraeglich_aendern(eigen, _eingaben(C), user=w.sucher))
    assert e.value.status_code == 409


def test_04_route_und_oberflaeche():
    import inspect
    C = _module("routes.contracts")
    pfade = {r.path for r in C.router.routes if "POST" in getattr(r, "methods", set())}
    assert "/contracts/{contract_id}/neue-fassung" in pfade
    namen = {d.call.__name__ for r in C.router.routes if getattr(r, "path", "") == "/contracts/{contract_id}/neue-fassung"
             for d in r.dependant.dependencies}
    assert "current_firma" in namen and "require_active_sub" not in namen, "Chef UND Sucher (eigene)"
    q = inspect.getsource(C.vertrag_nachtraeglich_aendern)
    assert "_NICHT_AUS_DEM_FORMULAR" in q and "_portal_nachweis(doc, alte_version)" in q
    front = Path(__file__).resolve().parent.parent.parent / "frontend" / "src"
    dialog = (front / "components" / "ContractDialog.jsx").read_text(encoding="utf-8")
    archiv = (front / "pages" / "app" / "PDFArchiv.jsx").read_text(encoding="utf-8")
    assert "/neue-fassung`" in dialog and "export function formularAusVertrag" in dialog
    assert "max-w-[min(96vw,1500px)]" in dialog, "Dialog breiter (Wunsch Ahmad 01.10.2026)"
    assert "vertrag-aendern-" in archiv and "<ContractDialog open key={aendern.id} vertrag={aendern}" in archiv
