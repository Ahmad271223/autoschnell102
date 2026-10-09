# -*- coding: utf-8 -*-
"""Wunsch Ahmad 09.10.2026: beim Vertrag-Erstellen die Frage "Motor, Getriebe, Kupplung — alles in Ordnung oder
Schaden vorhanden?"; Ueberschrift wieder "Fahrzeugbeschreibung"."""
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


def test_02_contractin_nimmt_und_prueft():
    d = ContractIn(**_KOPF, motor_zustand="ok", getriebe_zustand="Schaden vorhanden", kupplung_zustand="",
                   technik_schaden_text="Getriebe springt im 3. Gang raus").model_dump()
    assert (d["motor_zustand"], d["getriebe_zustand"], d["kupplung_zustand"]) == ("in Ordnung", "Schaden vorhanden", "")
    assert d["technik_schaden_text"].startswith("Getriebe springt")
    with pytest.raises(Exception):
        ContractIn(**_KOPF, motor_zustand="vielleicht")
    with pytest.raises(Exception):
        ContractIn(**_KOPF, technik_schaden_text="x" * 301)
    alt = ContractIn(**_KOPF).model_dump()                   # Altvertraege/API ohne Angabe
    assert (alt["motor_zustand"], alt["technik_schaden_text"]) == ("", "")


@pytest.mark.parametrize("layout", ["modern", "formular"])
def test_03_pdf_zeilen_und_ueberschrift(layout):
    dealer = {"company_name": "Firma"}
    vehicle = {"make_label": "VW", "model_label": "Golf"}
    c = {**_KOPF, "vertrag_layout": layout, "vehicle_description": "Sehr gepflegt.",
         "motor_zustand": "in Ordnung", "getriebe_zustand": "Schaden vorhanden", "kupplung_zustand": "in Ordnung",
         "technik_schaden_text": "springt im 3. Gang raus"}
    f = _text(P.generate_contract_pdf(dealer=dealer, vehicle=vehicle, contract=c))
    assert "Motor in Ordnung" in f and "Getriebe Schaden vorhanden" in f and "Kupplung in Ordnung" in f
    assert "Schaden an Motor/Getriebe/Kupplung springt im 3. Gang raus" in f
    assert "FAHRZEUGBESCHREIBUNG" in f.upper() and "Sehr gepflegt." in f
    # ohne Angabe: keine der Zeilen, kein Schadentext
    ohne = _text(P.generate_contract_pdf(dealer=dealer, vehicle=vehicle,
                                         contract={**_KOPF, "technik_schaden_text": "vergessen"}))
    for wort in ("Motor", "Getriebe", "Kupplung", "vergessen"):
        assert wort not in ohne, wort
