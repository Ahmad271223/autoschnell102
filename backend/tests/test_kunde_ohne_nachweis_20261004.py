# -*- coding: utf-8 -*-
"""Wunsch Ahmad 04.10.2026 ("das darf der Kunde nicht sehen"):

  * im Vertrag steht nicht mehr "FASSUNG · 3 · ersetzt Fassung 2 vom …" (Kopf) und "· Fassung 3" (Fusszeile) —
    in keiner Ausfertigung (Druck, Versand, Kundenportal, beide Layouts); gespeichert bleibt die Fassung trotzdem
  * der Kunde bekommt den unterschriebenen Vertrag OHNE das Blatt "Signaturnachweis"; die Firma behaelt es
  * neben der Unterschrift der Firma steht im Vertrag nicht mehr "hinterlegte Unterschrift" (nur im Nachweis)
"""
import base64
import io

from pypdf import PdfReader

import portal_pdf
import routes.kundenportal as KP
from pdf_service import generate_contract_pdf

from test_kundenportal_20260929 import _b64, _lauf, _png, _request, _vertrag, welt  # noqa: F401

HAENDLER = {"company_name": "KFZ Müller GmbH", "address": "Hauptstraße 1", "zip_code": "12345", "city": "Berlin"}
FZ = {"make_label": "VW", "model_label": "Golf"}
CD = {"seller_name": "Erika Mustermann", "purchase_price": 12500.0, "vehicle_make": "VW", "vehicle_model": "Golf",
      "contract_no": "KV-F3", "fassung": 3, "ersetzt_fassung_am": "2026-10-04"}


def _seiten(pdf: bytes) -> list:
    return [" ".join((s.extract_text() or "").split()) for s in PdfReader(io.BytesIO(pdf)).pages]


def _text(pdf: bytes) -> str:
    return " ".join(_seiten(pdf))


def test_01_keine_fassung_im_vertrag_in_keiner_ausfertigung():
    for layout in ("modern", "formular"):
        for art in ({}, {"digital": True}, {"portal": True}):
            text = _text(generate_contract_pdf(dealer=HAENDLER, vehicle=FZ, contract=CD, layout=layout, **art))
            assert "KV-F3" in text and "Erika Mustermann" in text, (layout, art)
            for verboten in ("FASSUNG", "ersetzt Fassung", "Fassung 3", "Fassung 2", "04.10.2026 · Fassung"):
                assert verboten not in text, (layout, art, verboten)


def _signiert():
    original = generate_contract_pdf(dealer=HAENDLER, vehicle=FZ, contract=CD, portal=True)
    return original, portal_pdf.unterschreiben(
        original, verkaeufer_png=_png(), kaeufer_png=_png(), name="Erika Mustermann", zeit="04.10.2026, 22:21",
        firma="KFZ Müller GmbH", vertragsnummer=CD["contract_no"], fassung=3)


def test_02_kundenfassung_ohne_nachweisblatt():
    original, (pdf, summe, in_feldern) = _signiert()
    assert in_feldern is True
    voll = _seiten(pdf)
    assert voll[-1].startswith("Signaturnachweis") and summe in voll[-1]
    kunde = portal_pdf.kundenfassung(pdf)
    seiten = _seiten(kunde)
    assert len(seiten) == len(voll) - 1 == len(PdfReader(io.BytesIO(original)).pages)
    alles = " ".join(seiten)
    for verboten in ("Signaturnachweis", "Prüfsumme", "SHA-256", summe, "Vertragsfassung", "hinterlegte Unterschrift"):
        assert verboten not in alles, verboten
    # die Unterschriften stehen weiter in den Feldern des Vertrags
    assert "Erika Mustermann · digital am 04.10.2026, 22:21 Uhr" in alles
    assert seiten == voll[:-1]


def test_03_kundenfassung_laesst_fremde_dokumente_in_ruhe():
    # letztes Blatt ist kein Nachweis (z. B. ein altes Dokument) -> unveraendert
    original = generate_contract_pdf(dealer=HAENDLER, vehicle=FZ, contract=CD, portal=True)
    assert portal_pdf.kundenfassung(original) == original
    # nur eine Seite -> unveraendert (nie ein leeres PDF)
    from reportlab.pdfgen import canvas
    puffer = io.BytesIO()
    c = canvas.Canvas(puffer)
    c.drawString(50, 800, "Signaturnachweis")
    c.showPage()
    c.save()
    eine = puffer.getvalue()
    assert portal_pdf.kundenfassung(eine) == eine


def _seite_an(w):
    _lauf(KP.put_webseite(KP.WebseiteIn(slug="kfz-mueller", aktiv=True), user=w.chef))


def _unterschreiben(w, c):
    frei = _lauf(KP.portal_freigeben(c["id"], user=w.sucher))
    s = _lauf(KP.portal_oeffnen(KP.OeffnenIn(code=frei["code"], slug="kfz-mueller"), _request()))["sitzung"]
    _lauf(KP.portal_sitzung_pdf(s))                                # erst lesen
    erg = _lauf(KP.portal_unterschreiben(s, KP.UnterschreibenIn(signature_b64=_b64(_png()), name="Erika Mustermann",
                                                                einverstanden=True), _request()))
    assert erg["ok"]
    return s


def test_04_kunde_bekommt_vertrag_ohne_nachweis_firma_mit(welt):  # noqa: F811
    w = welt
    _seite_an(w)
    c = _vertrag(w, w.sucher)
    s = _unterschreiben(w, c)
    doc = w.run(w.db.generated_pdfs.find_one({"id": c["id"]}, {"_id": 0, "pdf_signiert_b64": 1, "portal": 1}))
    assert doc["portal"]["unterschrift_in_feldern"] is True
    gespeichert = base64.b64decode(doc["pdf_signiert_b64"])
    # Kunde (Firmenseite): ohne Nachweisblatt, Unterschriften in den Feldern
    antwort = _lauf(KP.portal_sitzung_pdf(s))
    kunde = _text(antwort.body)
    assert antwort.media_type == "application/pdf" and "no-store" in antwort.headers["cache-control"]
    assert "Signaturnachweis" not in kunde and "Prüfsumme" not in kunde and "Erika Mustermann" in kunde
    assert len(_seiten(antwort.body)) == len(_seiten(gespeichert)) - 1
    # Firma (Chef und Sucher): das vollstaendige Dokument mit Nachweis — gespeichert bleibt es unveraendert
    for nutzer in (w.chef, w.sucher):
        firma = _lauf(KP.portal_pdf(c["id"], user=nutzer)).body
        assert firma == gespeichert and "Signaturnachweis" in _text(firma)


def test_05_ohne_felder_bleibt_der_nachweis_sonst_fehlten_die_unterschriften(welt):  # noqa: F811
    """Rueckfall (anderes Layout, keine Unterschriftsfelder gefunden): die Unterschriften stehen NUR auf dem
    Nachweisblatt — dann bekommt der Kunde das ganze Dokument, nie einen Vertrag ohne Unterschrift."""
    w = welt
    _seite_an(w)
    c = _vertrag(w, w.sucher)
    s = _unterschreiben(w, c)
    w.run(w.db.generated_pdfs.update_one({"id": c["id"]}, {"$set": {"portal.unterschrift_in_feldern": False}}))
    doc = w.run(w.db.generated_pdfs.find_one({"id": c["id"]}, {"_id": 0, "pdf_signiert_b64": 1}))
    assert _lauf(KP.portal_sitzung_pdf(s)).body == base64.b64decode(doc["pdf_signiert_b64"])


def test_06_quelltext_kein_fassungstext_mehr():
    import inspect

    import pdf_service
    q = inspect.getsource(pdf_service)
    assert "_fassung_text" not in q and '"FASSUNG"' not in q and "ersetzt Fassung {" not in q
