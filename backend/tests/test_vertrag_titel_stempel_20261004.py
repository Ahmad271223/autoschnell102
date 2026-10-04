# -*- coding: utf-8 -*-
"""Wunsch Ahmad 04.10.2026 (Vertrags-PDF):
  - "Fahrzeugbeschreibung (vom Inserat)"            -> "Beschreibung"
  - "Ausstattung laut Inserat / Verkäuferangaben"   -> "Ausstattung"
  - "2 · Zusicherungen & Zustand"                   -> "2 · Zustand"
  - Firmenstempel (Stempel & Unterschrift des Chefs im Kasten "Käufer") dreimal so gross; beide
    Unterschriftskaesten bleiben gleich hoch, die Kundenunterschrift steht unten auf der Linie."""
import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pdf_service as P  # noqa: E402


def _png(breite, hoehe):
    from PIL import Image
    puffer = io.BytesIO()
    Image.new("RGB", (breite, hoehe), (20, 40, 160)).save(puffer, format="PNG")
    return puffer.getvalue()


def _text(pdf: bytes) -> str:
    from pypdf import PdfReader
    return " ".join(" ".join((s.extract_text() or "").split()) for s in PdfReader(io.BytesIO(pdf)).pages)


@pytest.mark.parametrize("layout", ["modern", "formular"])
def test_kurze_ueberschriften(layout):
    contract = {"seller_name": "V", "purchase_price": 1000, "contract_no": "KV-T", "service_book": "ja",
                "vehicle_description": "Sehr gepflegt.", "vertrag_layout": layout}
    vehicle = {"make_label": "Cupra", "model_label": "Leon", "features": ["ABS", "Navigationssystem"]}
    f = _text(P.generate_contract_pdf(dealer={"company_name": "Firma"}, vehicle=vehicle, contract=contract))
    gross = f.upper()
    for neu in ("2 · ZUSTAND", "AUSSTATTUNG", "BESCHREIBUNG"):
        assert neu in gross, neu
    for alt in ("ZUSICHERUNGEN & ZUSTAND", "LAUT INSERAT / VERKÄUFERANGABEN", "FAHRZEUGBESCHREIBUNG (VOM INSERAT)"):
        assert alt not in gross, alt
    assert "Ausstattung laut Inseratsangaben." in f, "die Hinweiszeile bleibt"


def test_stempel_dreimal_so_gross():
    assert P.STEMPEL_HOEHE == pytest.approx(3 * P.UNTERSCHRIFT_HOEHE)
    quadrat = _png(600, 600)
    normal = P._unterschrift_flowable(quadrat, 10_000)
    stempel = P._unterschrift_flowable(quadrat, 10_000, P.STEMPEL_HOEHE)
    assert stempel.drawHeight == pytest.approx(3 * normal.drawHeight)
    # breiter als der Kasten wird er nie
    breit = P._unterschrift_flowable(_png(3000, 600), P.COL_W - 16, P.STEMPEL_HOEHE)
    assert breit.drawWidth <= P.COL_W - 16 + 0.01


def test_kaesten_gleich_hoch_und_stempel_im_kaeufer_kasten():
    st = P._styles() if hasattr(P, "_styles") else None
    if st is None:
        pytest.skip("Stil-Fabrik nicht gefunden")
    paar = P._empfang_paar({}, st, True, bilder={"verkaeufer": _png(800, 200), "kaeufer": _png(600, 600)},
                           portal=True)
    verkaeufer, _, kaeufer = paar._cellvalues[0]
    hoehen = [k.wrap(P.COL_W, 1000)[1] for k in (verkaeufer, kaeufer)]
    assert hoehen[0] == pytest.approx(hoehen[1]), hoehen
    # im Kaeufer-Kasten steckt das grosse Bild
    zelle = kaeufer._cellvalues[2][0]
    bild = list(zelle)[-1] if isinstance(zelle, (list, tuple)) else zelle
    assert bild.drawHeight == pytest.approx(P.STEMPEL_HOEHE)
