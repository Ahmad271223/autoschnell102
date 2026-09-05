# -*- coding: utf-8 -*-
"""Beweis-Datenblatt fuer mobile.de (Ersatz fuer den geblockten
Seiten-Snapshot). Prueft PDF- und Bild-Erzeugung ohne Netzwerk:
Fahrzeugdaten aus dem echten Apify-Datensatz, Fotos synthetisch."""
import io
import json
from pathlib import Path

import pytest
from PIL import Image as PILImage
from pypdf import PdfReader

from datenblatt_service import datenblatt_bild, datenblatt_pdf, rebuild_html
from mobile_service import _parse_apify_item

FIXTURE = Path(__file__).parent / "fixtures" / "apify_mobile_item.json"
URL = "https://suchen.mobile.de/fahrzeuge/details.html?id=42196329136896"


def _foto(farbe, b=800, h=600) -> bytes:
    puffer = io.BytesIO()
    PILImage.new("RGB", (b, h), farbe).save(puffer, "JPEG")
    return puffer.getvalue()


@pytest.fixture()
def daten():
    item = json.loads(FIXTURE.read_text(encoding="utf-8"))[0]
    return _parse_apify_item(item, "42196329136896")


@pytest.fixture()
def fotos():
    # Querformat + Hochformat: beide muessen sauber zugeschnitten werden.
    return [_foto("#334455"), _foto("#553344", 600, 800), _foto("#445533")]


def test_pdf_inhalt_und_kennzeichnung(daten, fotos):
    pdf = datenblatt_pdf(daten, URL, "2026-08-31T18:10:19", fotos)
    assert pdf.startswith(b"%PDF")
    text = "".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(pdf)).pages)
    # Fahrzeugdaten
    for muss in ["Volkswagen", "Golf", "23.850", "111.016 km", "213 kW (290 PS)",
                 "Benzin", "Automatik", "06/2019", "AUTO-MAGER.DE", "97078"]:
        assert muss in text, f"fehlt im PDF: {muss}"
    # Ehrliche Kennzeichnung: Quelle, ID, Abrufzeit, KEIN Original-Hinweis
    assert "Mobile Rebuild" in text
    assert "42196329136896" in text
    assert "31.08.2026" in text
    assert "Original-Screenshot" in text
    assert URL.replace("https://", "")[:30] in text.replace("https://", "")


def test_pdf_ohne_fotos_funktioniert(daten):
    pdf = datenblatt_pdf(daten, URL, None, [])
    assert pdf.startswith(b"%PDF")
    text = "".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(pdf)).pages)
    assert "Volkswagen" in text and "Mobile Rebuild" in text


def test_bild_erzeugung(daten, fotos):
    jpg = datenblatt_bild(daten, URL, "2026-08-31T18:10:19", fotos)
    assert jpg[:2] == b"\xff\xd8"                 # JPEG-Magic
    img = PILImage.open(io.BytesIO(jpg))
    assert img.width == 1200 and img.height > 800
    # Kaputtes Einzelfoto darf die Erzeugung nicht stoppen
    jpg2 = datenblatt_bild(daten, URL, None, [b"kein-bild"] + fotos)
    assert jpg2[:2] == b"\xff\xd8"


def test_rebuild_html_inhalt_und_kennzeichnung(daten, fotos):
    """Dunkle Inserats-Ansicht (Mobile Rebuild): alle Kerndaten, ehrliche
    Herkunftsangabe, Fotos als eingebettete data-URIs."""
    html = rebuild_html(daten, URL, "2026-08-31T18:10:19", fotos)
    for muss in ["AutoSchnell · Mobile Rebuild", "Anzeigen-ID 42196329136896",
                 "23.850 €", "111.016 km", "213 kW (290 PS)", "Benzin",
                 "Automatik", "06/2019", "AUTO-MAGER.DE", "97078 Würzburg",
                 "kein Original-Screenshot", "Ausstattung (69)"]:
        assert muss in html, f"fehlt im Rebuild-HTML: {muss}"
    assert html.count("data:image/jpeg;base64,") == 3
    # Kein fremdes Markenzeichen als Absender — nur die Quellenangabe.
    assert "mobile.de-Logo" not in html


def test_rebuild_html_ohne_fotos(daten):
    html = rebuild_html(daten, URL, None, [])
    assert "AutoSchnell · Mobile Rebuild" in html
    assert "data:image/jpeg" not in html
