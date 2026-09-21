# -*- coding: utf-8 -*-
"""Pruefbericht 20.09.2026 — Dokumente (am FERTIGEN PDF geprueft).

P-01/P-02  Nicht-westeuropaeische Namen ("Şahin Yıldırım", "Łukasz",
           kyrillisch) erschienen in Kaufvertrag und Abholprotokoll als
           Kaestchen — die Standardschrift Helvetica kennt die Zeichen nicht.
P-03       Ein Vertrag mit Freitext-Schaden liess das Abholprotokoll
           abstuerzen; der Fahrer konnte vor Ort nie abschliessen.
P-04/P-09  accident_free=None brach die Vertragsanlage, payment_method=None
           stand als "None" im Vertrag.
P-21/P-26/P-27  None-Anschrift, ungueltige Schadensfarbe, Pruefeintrag als Text.
S-01/S-02  Keine Angabe im Inserat -> keine "Unfallschaden/Fahrbereit"-Zeile.
U-156      Ein leeres Unterschriftsfeld galt als unterschrieben.
"""
import io
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

NAMEN = ["Şahin Yıldırım", "Łukasz Wójcik", "Дмитрий Иванов"]


def _text(roh: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        from PyPDF2 import PdfReader
    roh_text = "\n".join(s.extract_text() or "" for s in PdfReader(io.BytesIO(roh)).pages)
    # Leerraum zusammenfassen: ein langer Name darf im PDF umbrechen.
    return " ".join(roh_text.split())


def _helvetica_text(roh: bytes) -> list:
    """Sichtbarer Text, der in Helvetica gesetzt ist. (reportlab traegt
    Helvetica immer als Ressource ein — entscheidend ist, ob TEXT sie nutzt.)"""
    from pypdf import PdfReader
    teile = []

    def besucher(text, cm, tm, font, groesse):
        if font and "Helvetica" in str(font.get("/BaseFont")) and text.strip():
            teile.append(text)
    for seite in PdfReader(io.BytesIO(roh)).pages:
        seite.extract_text(visitor_text=besucher)
    return teile


def _unicode_schrift_da() -> bool:
    from pdf_schrift import schriften
    return schriften()[2]


def _vertrag(**extra):
    c = {"seller_name": NAMEN[0], "seller_address": "Łódzka 5", "seller_zip": "40210",
         "seller_city": "Düsseldorf", "purchase_price": 15900, "payment_method": "bar",
         "pickup_date": "2026-10-03", "contract_no": "KV-TEST-1"}
    c.update(extra)
    return c


def _pdf_vertrag(**extra):
    from pdf_service import generate_contract_pdf
    return generate_contract_pdf(
        dealer={"company_name": "Autohaus Test", "address": "Weg 1", "zip_code": "10115",
                "city": "Berlin"},
        vehicle={"make_label": "BMW", "model_label": "320d"},
        contract=_vertrag(**extra))


@pytest.mark.skipif(not _unicode_schrift_da(), reason="keine TrueType-Schrift auf diesem Rechner")
def test_p01_kaufvertrag_zeigt_fremde_buchstaben():
    roh = _pdf_vertrag(seller_name=" / ".join(NAMEN))
    text = _text(roh)
    for name in NAMEN:
        assert name in text, f"{name!r} fehlt oder als Kaestchen im Kaufvertrag"
    assert _helvetica_text(roh) == [], "Text im Kaufvertrag steht noch in Helvetica"


@pytest.mark.skipif(not _unicode_schrift_da(), reason="keine TrueType-Schrift auf diesem Rechner")
def test_p02_abholprotokoll_zeigt_fremde_buchstaben():
    from pickup_pdf_service import build_pickup_pdf
    roh = build_pickup_pdf(appointment={"seller_name": " / ".join(NAMEN), "title": "Abholung"},
                           contract=_vertrag(), vehicle={"make_label": "BMW"},
                           dealer={"company_name": "Autohaus Łódź"})
    text = _text(roh)
    for name in NAMEN:
        assert name in text, f"{name!r} fehlt oder als Kaestchen im Protokoll"
    assert _helvetica_text(roh) == [], "Text im Protokoll steht noch in Helvetica"


def test_p03_freitext_schaden_bricht_das_protokoll_nicht():
    from pickup_pdf_service import build_pickup_pdf
    roh = build_pickup_pdf(
        appointment={"seller_name": "Max"},
        contract=_vertrag(damages=["Kratzer hinten links",
                                   {"view": "front", "x": 10, "y": 10, "type_label": "Delle",
                                    "color": "rot; <b>"}]),
        vehicle={}, dealer={})
    text = _text(roh)
    assert "Kratzer hinten links" in text, "Freitext-Schaden fehlt im Protokoll"


def test_p21_p27_fehlende_anschrift_und_texteintrag():
    from pickup_pdf_service import build_pickup_pdf
    roh = build_pickup_pdf(
        appointment={"seller_name": "Max"},
        contract=_vertrag(seller_address=None, seller_zip=None),
        vehicle={}, dealer={},
        filled={"vehicle_check": {"make": "stimmt"}, "new_damages": ["irgendwas"]})
    assert roh[:4] == b"%PDF"


def test_p04_p09_leere_felder_brechen_den_vertrag_nicht():
    roh = _pdf_vertrag(accident_free=None, payment_method=None)
    text = _text(roh)
    assert "None" not in text
    assert "Bar / Überweisung" in text


@pytest.mark.parametrize("wert, zeile", [(None, False), (True, True), (False, True)])
def test_s01_zustandszeilen_nur_bei_echter_angabe(wert, zeile):
    from pdf_service import generate_contract_pdf
    roh = generate_contract_pdf(
        dealer={"company_name": "Autohaus Test"},
        vehicle={"make_label": "BMW", "accident_damaged": wert, "roadworthy": wert},
        contract=_vertrag())
    text = _text(roh)
    assert ("Unfallschaden (Inserat)" in text) is zeile
    assert ("Fahrbereit (Inserat)" in text) is zeile


def _png(bild) -> bytes:
    buf = io.BytesIO()
    bild.save(buf, "PNG")
    return buf.getvalue()


def test_u156_leere_unterschrift_ist_keine_unterschrift():
    from PIL import Image, ImageDraw
    from routes.protocols import unterschrift_hat_tinte
    leer = Image.new("RGBA", (1800, 480), (255, 255, 255, 255))
    assert unterschrift_hat_tinte(_png(leer)) is False
    assert unterschrift_hat_tinte(_png(Image.new("RGBA", (900, 240), (0, 0, 0, 0)))) is False
    strich = leer.copy()
    ImageDraw.Draw(strich).line((100, 200, 400, 260), fill=(17, 24, 39, 255), width=6)
    assert unterschrift_hat_tinte(_png(strich)) is True
    assert unterschrift_hat_tinte(b"kein bild") is False
