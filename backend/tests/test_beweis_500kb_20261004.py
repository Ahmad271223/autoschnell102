# -*- coding: utf-8 -*-
"""Beweisdokument (Wunsch Ahmad 04.10.2026): alle Fotos, sehr stark komprimiert, hoechstens ~500 KB je Auto.

Laeuft ohne Server: echte PDF-Erzeugung (beweis_pdf) mit erzeugten Fotos.
"""
import io
import os
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import beweis_service as BS  # noqa: E402
from beweis_pdf import beweis_pdf  # noqa: E402

ZEIT = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)


def _foto(glatt: bool, i: int) -> bytes:
    """glatt: wie ein normales Autofoto (Flaechen, Verlaeufe); sonst Rauschen (unguenstigster Fall)."""
    from PIL import Image
    random.seed(i)
    if glatt:
        klein = Image.frombytes("RGB", (48, 36), bytes(random.randrange(256) for _ in range(48 * 36 * 3)))
        im = klein.resize((1024, 768), Image.BICUBIC)
    else:
        im = Image.frombytes("RGB", (900, 675), os.urandom(900 * 675 * 3))
    b = io.BytesIO()
    im.save(b, "JPEG", quality=85)
    return b.getvalue()


def _wie_geladen(roh):
    """Wie _fotos_laden: erstes Foto 1000 px, weitere 640 px (Stufe 0)."""
    from bild_proxy import _fuer_pdf
    return [_fuer_pdf(r, BS._FOTO_KANTE_ERSTE if i == 0 else BS._FOTO_KANTE,
                      BS._FOTO_QUALITAET_ERSTE if i == 0 else BS._FOTO_QUALITAET) for i, r in enumerate(roh)]


def _bauer(urls):
    def bauen(fotos, begrenzt):
        return beweis_pdf(quelle="mobile", daten={"make_label": "VW", "model_label": "Golf", "list_price": 9900},
                          url="https://suchen.mobile.de/fahrzeuge/details.html?id=1", item_id="1", beweis_id="b1",
                          abgerufen_am=ZEIT, erstellt_am=ZEIT, fotos=fotos, foto_urls=urls,
                          groesse_begrenzt_kb=BS.BEWEIS_MAX_KB if begrenzt else None)
    return bauen


def test_01_grenze_und_alle_fotos_als_standard():
    assert BS.BEWEIS_MAX_KB == 500
    assert BS.BEWEIS_FOTOS_MAX >= 60, "nicht mehr bei 20 abschneiden"


def test_02_viele_normale_fotos_alle_drin_und_unter_500_kb():
    roh = [_foto(True, i) for i in range(40)]
    urls = [f"https://img.classistatic.de/api/v1/mo-prod/images/{i}" for i in range(40)]
    fotos = _wie_geladen(roh)
    pdf, eingebettet, stufe = BS.pdf_unter_grenze(_bauer(urls), fotos, BS.BEWEIS_MAX_KB * 1024)
    assert len(pdf) <= BS.BEWEIS_MAX_KB * 1024, len(pdf)
    assert len(eingebettet) == 40 and all(eingebettet), "alle 40 Fotos im Dokument"
    assert stufe >= 1, "dafuer staerker komprimiert"


def test_03_unguenstigster_fall():
    """30 Fotos aus reinem Rauschen passen nach dem Komprimieren noch ganz hinein; erst sehr viele werden gekuerzt."""
    roh = [_foto(False, i) for i in range(30)]
    urls = [f"https://img.classistatic.de/api/v1/mo-prod/images/r{i}" for i in range(30)]
    pdf, eingebettet, stufe = BS.pdf_unter_grenze(_bauer(urls), _wie_geladen(roh), BS.BEWEIS_MAX_KB * 1024)
    assert len(pdf) <= BS.BEWEIS_MAX_KB * 1024, len(pdf)
    assert len(eingebettet) == 30 and stufe >= 3
    # erst wenn selbst die staerkste Stufe nicht reicht, fallen hintere Fotos weg (hier mit kleiner Grenze erzwungen)
    viele = [_foto(False, 100 + i) for i in range(40)]
    urls = [f"https://img.classistatic.de/api/v1/mo-prod/images/v{i}" for i in range(40)]
    pdf, eingebettet, stufe = BS.pdf_unter_grenze(_bauer(urls), _wie_geladen(viele), 200 * 1024)
    assert len(pdf) <= 200 * 1024, len(pdf)
    assert 0 < len(eingebettet) < 40 and stufe == len(BS._STUFEN)


def test_04_kleines_dokument_bleibt_wie_es_ist():
    roh = [_foto(True, i) for i in range(3)]
    fotos = _wie_geladen(roh)
    pdf, eingebettet, stufe = BS.pdf_unter_grenze(_bauer(["https://a/1", "https://a/2", "https://a/3"]), fotos,
                                                  BS.BEWEIS_MAX_KB * 1024)
    assert stufe == 0 and eingebettet == fotos


def test_05_hinweis_im_dokument_bei_kuerzung():
    """Gekuerzte Fotos stehen nicht als "nicht geladen" da, sondern mit dem Grund."""
    import beweis_pdf as B
    quelle = Path(B.__file__).read_text(encoding="utf-8")
    assert "wegen der Dateigröße (höchstens {groesse_begrenzt_kb} KB je Dokument)" in quelle
    # die Kuerzung arbeitet mit einer KUERZEREN Liste (keine Platzhalter "konnte nicht geladen werden")
    fotos = _wie_geladen([_foto(False, i) for i in range(40)])
    pdf, eingebettet, _ = BS.pdf_unter_grenze(_bauer([f"https://x/{i}" for i in range(40)]), fotos, 200 * 1024)
    assert None not in eingebettet and 0 < len(eingebettet) < 40 and len(pdf) <= 200 * 1024
