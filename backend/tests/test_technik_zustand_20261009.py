# -*- coding: utf-8 -*-
"""Wunsch Ahmad 09.10.2026: beim Vertrag-Erstellen die Frage "Motor, Getriebe, Kupplung — alles in Ordnung oder
Schaden vorhanden?"; Ueberschrift wieder "Fahrzeugbeschreibung".
Wunsch Ahmad 13.10.2026: daraus EIN freiwilliger Punkt "Motor/Getriebe/Kupplung" (`technik_zustand`); die drei
Einzelfelder der Altvertraege werden weiter angenommen und im PDF zu einer Zeile zusammengefasst. Ausserdem steht
"Kaufpreis" nicht mehr doppelt (keine Ueberschrift ueber dem Preiskasten)."""
import io
import sys
from pathlib import Path

import pytest
from pypdf import PdfReader

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pdf_service as P  # noqa: E402
from routes.contracts import ContractIn, technik_zustand_normieren  # noqa: E402

_KOPF = {"vehicle_id": "v1", "seller_name": "V", "seller_phone": "0170 1", "purchase_price": 1000, "contract_no": "KV-T"}


def _text(pdf: bytes) -> str:
    return " ".join(" ".join((p.extract_text() or "").split()) for p in PdfReader(io.BytesIO(pdf)).pages)


def test_01_normieren():
    assert technik_zustand_normieren("") == "" and technik_zustand_normieren(None) == ""
    assert technik_zustand_normieren("in Ordnung") == "in Ordnung" and technik_zustand_normieren("OK") == "in Ordnung"
    assert technik_zustand_normieren("Schaden vorhanden") == "Schaden vorhanden"
    assert technik_zustand_normieren("schaden") == "Schaden vorhanden"
    with pytest.raises(ValueError):
        technik_zustand_normieren("vielleicht")


def test_02_contractin_ein_feld_freiwillig_und_altfelder():
    d = ContractIn(**_KOPF, technik_zustand="ok", technik_schaden_text="").model_dump()
    assert d["technik_zustand"] == "in Ordnung"
    s = ContractIn(**_KOPF, technik_zustand="schaden", technik_schaden_text="Getriebe springt im 3. Gang raus").model_dump()
    assert s["technik_zustand"] == "Schaden vorhanden" and s["technik_schaden_text"].startswith("Getriebe springt")
    # Altvertraege/API mit den drei Einzelfeldern werden weiter angenommen und normiert
    alt = ContractIn(**_KOPF, motor_zustand="ok", getriebe_zustand="Schaden vorhanden", kupplung_zustand="").model_dump()
    assert (alt["motor_zustand"], alt["getriebe_zustand"], alt["kupplung_zustand"]) == ("in Ordnung", "Schaden vorhanden", "")
    with pytest.raises(Exception):
        ContractIn(**_KOPF, technik_zustand="vielleicht")
    with pytest.raises(Exception):
        ContractIn(**_KOPF, technik_schaden_text="x" * 301)
    # ohne Angabe: freiwillig — kein Fehler, alles leer
    ohne = ContractIn(**_KOPF).model_dump()
    assert (ohne["technik_zustand"], ohne["motor_zustand"], ohne["technik_schaden_text"]) == ("", "", "")


def test_03_technik_gesamt():
    assert P.technik_gesamt({}) == "" and P.technik_gesamt(None) == ""
    assert P.technik_gesamt({"technik_zustand": "in Ordnung"}) == "in Ordnung"
    assert P.technik_gesamt({"technik_zustand": "Schaden vorhanden"}) == "Schaden vorhanden"
    # das neue Feld gewinnt gegen die alten
    assert P.technik_gesamt({"technik_zustand": "in Ordnung", "getriebe_zustand": "Schaden vorhanden"}) == "in Ordnung"
    # Altvertraege: ein Schaden zaehlt als Schaden, sonst in Ordnung; nichts angegeben = nichts
    assert P.technik_gesamt({"motor_zustand": "in Ordnung", "getriebe_zustand": "Schaden vorhanden"}) == "Schaden vorhanden"
    assert P.technik_gesamt({"motor_zustand": "in Ordnung", "kupplung_zustand": ""}) == "in Ordnung"
    assert P.technik_gesamt({"motor_zustand": "", "getriebe_zustand": "", "kupplung_zustand": ""}) == ""


@pytest.mark.parametrize("layout", ["modern", "formular"])
def test_04_pdf_eine_zeile_und_ueberschrift(layout):
    dealer = {"company_name": "Firma"}
    vehicle = {"make_label": "VW", "model_label": "Golf"}
    c = {**_KOPF, "vertrag_layout": layout, "vehicle_description": "Sehr gepflegt.",
         "technik_zustand": "Schaden vorhanden", "technik_schaden_text": "springt im 3. Gang raus"}
    f = _text(P.generate_contract_pdf(dealer=dealer, vehicle=vehicle, contract=c))
    assert "Motor/Getriebe/Kupplung Schaden vorhanden" in f
    assert "Schaden an Motor/Getriebe/Kupplung springt im 3. Gang raus" in f
    assert "Motor in Ordnung" not in f and "Getriebe Schaden vorhanden" not in f, "keine drei Einzelzeilen mehr"
    assert "FAHRZEUGBESCHREIBUNG" in f.upper() and "Sehr gepflegt." in f
    # in Ordnung: eine Zeile, kein Schadentext (auch wenn einer im Feld steht)
    ok = _text(P.generate_contract_pdf(dealer=dealer, vehicle=vehicle,
                                       contract={**c, "technik_zustand": "in Ordnung"}))
    assert "Motor/Getriebe/Kupplung in Ordnung" in ok and "springt im 3. Gang raus" not in ok
    # ohne Angabe: keine der Zeilen, kein Schadentext
    ohne = _text(P.generate_contract_pdf(dealer=dealer, vehicle=vehicle,
                                         contract={**_KOPF, "technik_schaden_text": "vergessen"}))
    for wort in ("Motor", "Getriebe", "Kupplung", "vergessen"):
        assert wort not in ohne, wort
    # Altvertrag (drei Einzelfelder, einer mit Schaden) -> dieselbe eine Zeile
    alt = _text(P.generate_contract_pdf(dealer=dealer, vehicle=vehicle, contract={
        **_KOPF, "motor_zustand": "in Ordnung", "getriebe_zustand": "Schaden vorhanden", "kupplung_zustand": "in Ordnung",
        "technik_schaden_text": "Oelverlust"}))
    assert "Motor/Getriebe/Kupplung Schaden vorhanden" in alt and "Schaden an Motor/Getriebe/Kupplung Oelverlust" in alt
    assert "Motor in Ordnung" not in alt


@pytest.mark.parametrize("layout", ["modern", "formular"])
def test_05_kaufpreis_nicht_doppelt(layout):
    f = _text(P.generate_contract_pdf(dealer={"company_name": "Firma"}, vehicle={"make_label": "VW", "model_label": "Golf"},
                                      contract={**_KOPF, "vertrag_layout": layout, "purchase_price": 12000}))
    assert "KAUFPREIS (VEREINBART) 12.000,00 EUR" in f
    assert "Kaufpreis KAUFPREIS" not in f and "KAUFPREIS KAUFPREIS" not in f, "Kaufpreis steht doppelt"
