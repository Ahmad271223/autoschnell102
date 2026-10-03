# -*- coding: utf-8 -*-
"""Pruefung 21.09.2026 (pdf): vor Ort gefundene Schaeden in der neuen Fassung.

Befund: pdf_service druckt NUR `damages_text`, sobald er gesetzt ist — die
Liste `damages` ist nur der Rueckfall. Jeder Vertrag mit Skizzen-Schaeden hat
den Text (DamageSelector schreibt beides). regenerate_contract_for_pickup
haengte die bei der Abholung aufgenommenen Schaeden nur an `damages` an; in
der neuen Fassung, die der Verkaeufer als aktuellen Stand bekommt, fehlten
genau diese Schaeden.

Jetzt: die neuen Schaeden kommen zusaetzlich als eigene Zeile in den
Schadenstext ("• Delle: Motorhaube (bei Abholung festgestellt)"), nie doppelt
— auch nicht, wenn die Neuerzeugung fuer dieselbe Abholung ein zweites Mal
laeuft (Nachholer).

Teil 1 ohne Server und Datenbank (Helfer + pdf_service, Text per pypdf).
Teil 2 in-Prozess gegen Mongo (Welt aus test_befunde_runde17_termine):
regenerate_contract_for_pickup mit ECHTER PDF-Erzeugung.
"""
import base64
import io
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tests"))

from pdf_service import VERTRAGSTEXT_START, generate_contract_pdf  # noqa: E402
from test_befunde_runde17_termine import _module, welt  # noqa: E402,F401

ALT = {"id": "d1", "view": "left", "type_key": "kratzer", "type_label": "Kratzer",
       "zone": "Tür vorne links", "x": 10, "y": 20}
NEU = {"id": "d2", "view": "top", "type_key": "delle", "type_label": "Delle",
       "zone": "Motorhaube", "x": 30, "y": 40}
ALT_TEXT = "• Kratzer: Tür vorne links"
NEU_ZEILE = "• Delle: Motorhaube (bei Abholung festgestellt)"

FIRMA = {"company_name": "Autohaus Schaden GmbH", "address": "Hauptstr. 1",
         "zip_code": "10115", "city": "Berlin", "phone": "030 123",
         "email": "info@schaden.test"}
FAHRZEUG = {"make_label": "BMW", "model_label": "320d"}


def _helfer():
    return _module("routes.contracts")._schadenstext_nach_abholung


def _flach(s: str) -> str:
    return " ".join((s or "").split())


def _pdf_text(pdf: bytes) -> str:
    from pypdf import PdfReader
    return " ".join(_flach(p.extract_text() or "") for p in PdfReader(io.BytesIO(pdf)).pages)


def _vertrag(**felder) -> dict:
    c = {"seller_name": "Vera Verkauf", "seller_address": "Musterweg 5",
         "seller_zip": "20095", "seller_city": "Hamburg", "purchase_price": 9000,
         "contract_no": "KV-SCHADEN", "pickup_date": "2026-09-25",
         "digital_vertragstext": VERTRAGSTEXT_START}
    c.update(felder)
    return c


# ================================================================ Helfer
def test_01_neuer_schaden_kommt_als_eigene_zeile_dazu():
    text = _helfer()(ALT_TEXT, [NEU])
    assert text.split("\n") == [ALT_TEXT, NEU_ZEILE], text


def test_02_rueckfaelle_wie_im_pdf():
    """Dieselben Rueckfaelle wie pdf_service beim Drucken der Liste."""
    zeilen = _helfer()(ALT_TEXT, [
        {"type_key": "steinschlag", "zone": "Frontscheibe"},   # type_key statt Label
        {"label": "Rost", "part_label": "Schweller"},           # alte Freitext-Schluessel
        {"type": "Beule", "part": "Heckklappe"},
        {"zone": "Dach"},                                       # ohne Art -> "Schaden"
        {"type_label": "Riss", "zone": ""},                     # ohne Bauteil
        "Kupplung rutscht",                                     # Freitext-Eintrag
        {"type_label": "Kratzer", "zone": "Tür\nhinten  rechts"},  # Umbruch im Eintrag
    ]).split("\n")[1:]
    assert zeilen == [
        "• steinschlag: Frontscheibe (bei Abholung festgestellt)",
        "• Rost: Schweller (bei Abholung festgestellt)",
        "• Beule: Heckklappe (bei Abholung festgestellt)",
        "• Schaden: Dach (bei Abholung festgestellt)",
        "• Riss (bei Abholung festgestellt)",
        "• Kupplung rutscht (bei Abholung festgestellt)",
        "• Kratzer: Tür hinten rechts (bei Abholung festgestellt)",
    ]


@pytest.mark.parametrize("text", [None, "", "  \n "])
def test_03_ohne_schadenstext_bleibt_er_leer(text):
    """Leerer Text: pdf_service druckt die Liste, und die hat die neuen
    Schaeden schon — ein Text nur mit den neuen wuerde die alten verdecken."""
    assert _helfer()(text, [NEU]) == text


def test_04_ohne_neue_schaeden_unveraendert():
    assert _helfer()(ALT_TEXT, []) == ALT_TEXT


def test_05_idempotent_und_ohne_doppelte_zeilen():
    helfer = _helfer()
    einmal = helfer(ALT_TEXT, [NEU])
    assert helfer(einmal, [NEU]) == einmal, "zweite Neuerzeugung haengt nichts mehr an"
    # Derselbe Schaden mit anderer id (zweimal erfasst) -> nur EINE Zeile
    zweimal = helfer(ALT_TEXT, [NEU, {**NEU, "id": "d3"}])
    assert zweimal.count("Motorhaube") == 1, zweimal


# ================================================================ PDF
@pytest.mark.parametrize("digital", [False, True], ids=["druck", "digital"])
def test_06_neuer_schaden_steht_im_pdf(digital):
    # Gegenprobe (alter Stand): Liste ergaenzt, Text nicht -> fehlt im PDF
    vorher = _pdf_text(generate_contract_pdf(
        dealer=dict(FIRMA), vehicle=dict(FAHRZEUG), digital=digital,
        contract=_vertrag(damages=[ALT, NEU], damages_text=ALT_TEXT)))
    assert "Tür vorne links" in vorher and "Motorhaube" not in vorher

    text = _helfer()(ALT_TEXT, [NEU])
    nachher = _pdf_text(generate_contract_pdf(
        dealer=dict(FIRMA), vehicle=dict(FAHRZEUG), digital=digital,
        contract=_vertrag(damages=[ALT, NEU], damages_text=text)))
    assert "Kratzer: Tür vorne links" in nachher
    assert "Delle: Motorhaube (bei Abholung festgestellt)" in nachher
    assert nachher.count("Motorhaube") == 1

    # Zweite Neuerzeugung: keine doppelte Zeile im PDF
    nochmal = _pdf_text(generate_contract_pdf(
        dealer=dict(FIRMA), vehicle=dict(FAHRZEUG), digital=digital,
        contract=_vertrag(damages=[ALT, NEU], damages_text=_helfer()(text, [NEU]))))
    assert nochmal.count("Motorhaube") == 1


# ======================================================== Neuerzeugung (Mongo)
def _auto_daten_aus(monkeypatch):
    AD = _module("auto_daten")

    async def _nichts(*a, **k):
        return None
    monkeypatch.setattr(AD, "nachfuehren", _nichts)


def test_07_neuerzeugung_nach_abholung_traegt_neuen_schaden_einmal(welt, monkeypatch):
    C = _module("routes.contracts")
    w, db = welt.w, welt.db
    _auto_daten_aus(monkeypatch)
    cid = f"cschaden_{w.s}"
    vertrag = w.vertrag(cid)
    vertrag["contract_data"].update(_vertrag(damages=[ALT], damages_text=ALT_TEXT))

    async def neu(protokoll_id, **kw):
        erg = {}
        ok = await C.regenerate_contract_for_pickup(
            contract_id=cid, dealer_id=w.dealer_id, user=w.chef, neue_schaeden=[NEU],
            grund="abholung_abgeschlossen", protokoll_id=protokoll_id, ergebnis=erg, **kw)
        doc = await db.generated_pdfs.find_one({"id": cid}, {"_id": 0})
        return ok, erg, doc

    async def lauf():
        await db.generated_pdfs.insert_one(vertrag)
        erster = await neu(f"p1_{w.s}")
        # Nachholer fuer dieselbe Abholung (anderer Protokoll-Stand), dazu ein
        # verschobener Termin, damit wirklich eine weitere Fassung entsteht.
        zweiter = await neu(f"p2_{w.s}", pickup_date="2099-09-12")
        # Nur die Schaeden, sonst nichts neu -> keine weitere Fassung
        dritter = await neu(f"p3_{w.s}")
        return erster, zweiter, dritter

    erster, zweiter, dritter = welt.run(lauf())

    ok, erg, doc = erster
    assert ok is True, erg
    daten = doc["contract_data"]
    assert daten["damages"] == [ALT, NEU]
    assert daten["damages_text"].split("\n") == [ALT_TEXT, NEU_ZEILE]
    assert doc["nach_abholung_aenderungen"]["neue_schaeden"] == 1
    for feld in ("pdf_b64", "pdf_digital_b64"):
        pdf = _pdf_text(base64.b64decode(doc[feld]))
        assert "Kratzer: Tür vorne links" in pdf, feld
        assert "Delle: Motorhaube (bei Abholung festgestellt)" in pdf, feld
        assert pdf.count("Motorhaube") == 1, feld

    ok, erg, doc = zweiter
    assert ok is True, erg
    assert doc["version"] == 3 and doc["pickup_date"] == "2099-09-12"
    daten = doc["contract_data"]
    assert daten["damages"] == [ALT, NEU], "der Schaden steht nicht doppelt in der Liste"
    assert daten["damages_text"].split("\n") == [ALT_TEXT, NEU_ZEILE], \
        "die Zeile steht nach der zweiten Neuerzeugung nicht doppelt im Text"
    for feld in ("pdf_b64", "pdf_digital_b64"):
        assert _pdf_text(base64.b64decode(doc[feld])).count("Motorhaube") == 1, feld

    ok, erg, doc = dritter
    assert ok is False and erg == {"grund": "keine_aenderung"}
    assert doc["version"] == 3
