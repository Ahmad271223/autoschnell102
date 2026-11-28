# -*- coding: utf-8 -*-
"""Prüfbericht 20.09.2026, Reparaturwelle A3 (22.09.2026): U-73 und U-83.

U-73  GET /contracts/{id}/pdf liefert die Kopfzeile X-Vertrag-Version; der
      Versand-Dialog merkt sie zum vorab geladenen Blob und schickt sie bei
      methode=teilen als `version` mit. Weicht sie von der aktuellen Fassung
      ab, antwortet der Server 409 fassung_veraltet — KEIN Vermerk, die
      geteilte Datei war alt.
U-83  Idempotenz-Hash-Konflikt beim Anlegen (gleicher Schlüssel, anderer
      Inhalt) liefert detail={code: idempotenz_konflikt, contract_id,
      purchase_price} statt nur "bitte neu laden" — die Oberfläche fragt
      damit nach und verwirft das Formular nicht.
In-Prozess mit der Wegwerf-Welt aus test_befunde_runde17_termine.
"""
import base64
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_befunde_runde17_termine import _module, welt  # noqa: E402,F401

PROJEKT = Path(__file__).resolve().parents[2]


def _teilen(C, **extra):
    return C.SendIn(channel="whatsapp", recipient="+491701234567", message="Hallo",
                    methode="teilen", **extra)


# ------------------------------------------------------------------ U-73
def test_01_sendin_kennt_die_fassung():
    C = _module("routes.contracts")
    assert _teilen(C, version=2).version == 2
    assert C.SendIn(channel="whatsapp", recipient="+491701234567", message="x").version is None
    with pytest.raises(ValueError):
        _teilen(C, version=0)


def test_02_teilen_mit_veralteter_fassung_409_ohne_vermerk(welt):
    C = _module("routes.contracts")
    w, db = welt.w, welt.db
    cid = f"c_a3_alt_{w.s}"

    async def lauf():
        await db.generated_pdfs.insert_one(w.vertrag(cid, version=2))
        try:
            await C.send_contract(cid, _teilen(C, idempotency_key="k-a3-alt", version=1), w.chef)
            fehler = None
        except HTTPException as exc:
            fehler = exc
        c = await db.generated_pdfs.find_one({"id": cid}, {"_id": 0})
        return fehler, c

    fehler, c = welt.run(lauf())
    assert fehler is not None and fehler.status_code == 409, fehler
    assert fehler.detail["code"] == "fassung_veraltet"
    assert fehler.detail["version"] == 2 and fehler.detail["geteilt"] == 1
    assert "Fassung 2" in fehler.detail["msg"]
    # nichts vermerkt, nichts reserviert
    assert not c.get("send_status") and c["status"] == "erstellt"


def test_03_teilen_mit_aktueller_oder_ohne_fassung_wird_vermerkt(welt):
    C = _module("routes.contracts")
    w, db = welt.w, welt.db
    cid = f"c_a3_ok_{w.s}"

    async def lauf():
        await db.generated_pdfs.insert_one(w.vertrag(cid, version=2))
        mit = await C.send_contract(cid, _teilen(C, idempotency_key="k-a3-mit", version=2), w.chef)
        ohne = await C.send_contract(cid, _teilen(C, idempotency_key="k-a3-ohne"), w.chef)
        c = await db.generated_pdfs.find_one({"id": cid}, {"_id": 0})
        return mit, ohne, c

    mit, ohne, c = welt.run(lauf())
    assert mit["zustellung"] == "geteilt" and ohne["zustellung"] == "geteilt"
    assert c["status"] == "versand_vorbereitet"
    assert [e["methode"] for e in c["send_status"]] == ["teilen", "teilen"]
    assert all(e["version"] == 2 for e in c["send_status"])


def test_04_pdf_antwort_traegt_die_fassung(welt):
    C = _module("routes.contracts")
    w, db = welt.w, welt.db
    cid = f"c_a3_pdf_{w.s}"

    async def lauf():
        await db.generated_pdfs.insert_one(w.vertrag(
            cid, version=3, pdf_b64=base64.b64encode(b"%PDF-1.4 test").decode()))
        return await C.get_contract_pdf(cid, user=w.chef, variante="druck")

    r = welt.run(lauf())
    assert r.headers["x-vertrag-version"] == "3"
    assert r.headers["cache-control"] == "no-store"
    assert bytes(r.body).startswith(b"%PDF")


# ------------------------------------------------------------------ U-83
def test_05_idempotenz_konflikt_nennt_vertrag_und_preis(welt):
    C = _module("routes.contracts")
    w, db = welt.w, welt.db
    cid = f"c_a3_idem_{w.s}"

    async def lauf():
        await db.generated_pdfs.insert_one(w.vertrag(
            cid, idempotency_key="schluessel-a3-1", idempotency_hash="anderer-inhalt",
            purchase_price=12500))
        body = C.ContractIn(vehicle_id="v-a3", seller_name="Max Verkaeufer",
                            purchase_price=9900, idempotency_key="schluessel-a3-1")
        try:
            await C.create_contract(body, w.chef)
            return None
        except HTTPException as exc:
            return exc

    exc = welt.run(lauf())
    assert exc is not None and exc.status_code == 409
    d = exc.detail
    assert d["code"] == "idempotenz_konflikt"
    assert d["contract_id"] == cid and d["purchase_price"] == 12500.0
    assert "neu laden" in d["msg"]
    # ohne Preis am Dokument: None statt Absturz
    assert C._idempotenz_konflikt({"id": "x"})["purchase_price"] is None


def test_06_oberflaeche_ist_verdrahtet():
    send = (PROJEKT / "frontend" / "src" / "components" / "SendDialog.jsx").read_text(encoding="utf-8")
    assert "x-vertrag-version" in send and 'd?.code === "fassung_veraltet"' in send
    assert "version: fassung" in send
    dialog = (PROJEKT / "frontend" / "src" / "components" / "ContractDialog.jsx").read_text(encoding="utf-8")
    assert 'd?.code === "idempotenz_konflikt"' in dialog
    assert "idempotenz.current = neuerIdempotenzSchluessel()" in dialog
    assert "data?.nacharbeit_hinweis" in dialog and "data?.bereits_vorhanden" in dialog
