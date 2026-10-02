# -*- coding: utf-8 -*-
"""Wunsch Ahmad 02.10.2026: Unterschreibt der Kunde ueber die Firmenseite, steht unten NUR seine Unterschrift
und die der Firma — ohne "Kaufpreis erhalten", ohne Schluesselanzahl, ohne "Datum und Ort". Dafuer entsteht beim
Erzeugen des Codes eine eigene Portal-Fassung (pdf_portal_b64 je Vertragsfassung); der Kunde liest und
unterschreibt genau sie. Die Druckfassung fuer Fahrer/Sucher/Chef bleibt mit Empfangsbestaetigung."""
import base64
import io

import pdf_service as P
import routes.kundenportal as KP

from test_kundenportal_20260929 import _b64, _lauf, _png, _request, _vertrag, welt  # noqa: F401


def _text(pdf: bytes) -> str:
    from pypdf import PdfReader
    return " ".join(" ".join((p.extract_text() or "").split()) for p in PdfReader(io.BytesIO(pdf)).pages)


def _seite_an(w):
    _lauf(KP.put_webseite(KP.WebseiteIn(slug="kfz-mueller", aktiv=True), user=w.chef))


def test_01_portal_fassung_ohne_empfangsbestaetigung():
    cd = {"seller_name": "Erika Mustermann", "purchase_price": 12500.0, "vehicle_make": "VW", "vehicle_model": "Golf",
          "contract_no": "KV-P", "schluessel_anzahl": "2", "empfang_datum": "2026-10-03", "empfang_ort_kaeufer": "Berlin"}
    dealer = {"company_name": "KFZ Müller GmbH", "address": "Hauptstraße 1", "zip_code": "12345", "city": "Berlin"}
    fz = {"make_label": "VW", "model_label": "Golf"}
    druck = _text(P.generate_contract_pdf(dealer=dealer, vehicle=fz, contract=cd))
    portal = _text(P.generate_contract_pdf(dealer=dealer, vehicle=fz, contract=cd, portal=True))
    assert "bestätigt Empfang von" in druck and "Schlüssel(n)" in druck and "Datum und Ort" in druck
    for verboten in ("bestätigt Empfang von", "Schlüssel(n)", "Datum und Ort", "Kaufpreis erhalten", "Zulassungsbescheinigung Teil"):
        assert verboten not in portal, verboten
    assert portal.count("Unterschrift") >= 3                      # zwei Kaesten + Abschnittstitel
    assert "Verkäufer" in portal and "Käufer" in portal and "Mit ihrer Unterschrift bestätigen beide Parteien" in portal
    # der Rest des Vertrags ist identisch (Kaufpreis, Fahrzeug, Verkaeufer)
    for stueck in ("12.500,00", "Golf", "Erika Mustermann", "KFZ-KAUFVERTRAG"):
        assert stueck in portal, stueck
    # die Portal-Fassung ignoriert die Firmeneinstellung "Empfangsbestaetigung drucken"
    assert "bestätigt Empfang von" not in _text(P.generate_contract_pdf(
        dealer={**dealer, "empfang_drucken": True}, vehicle={}, contract=cd, portal=True))


def test_02_code_erzeugt_die_portal_fassung_kunde_liest_und_unterschreibt_sie(welt):  # noqa: F811
    w = welt
    _seite_an(w)
    c = _vertrag(w, w.sucher)
    assert "bestätigt Empfang von" in _text(base64.b64decode(c["pdf_b64"])), "Druckfassung traegt die Bestaetigung"
    frei = _lauf(KP.portal_freigeben(c["id"], user=w.sucher))
    doc = w.run(w.db.generated_pdfs.find_one({"id": c["id"]}, {"_id": 0, "pdf_portal_b64": 1, "pdf_portal_version": 1,
                                                                "pdf_b64": 1, "version": 1}))
    assert doc["pdf_portal_version"] == 1 and doc["pdf_portal_b64"] and doc["pdf_b64"] == c["pdf_b64"]
    portal = base64.b64decode(doc["pdf_portal_b64"])
    assert "bestätigt Empfang von" not in _text(portal) and "Datum und Ort" not in _text(portal)
    # der Kunde bekommt genau diese Fassung
    s = _lauf(KP.portal_oeffnen(KP.OeffnenIn(code=frei["code"], slug="kfz-mueller"), _request()))["sitzung"]
    gelesen = _lauf(KP.portal_sitzung_pdf(s)).body
    assert gelesen == portal
    erg = _lauf(KP.portal_unterschreiben(s, KP.UnterschreibenIn(signature_b64=_b64(_png()), name="Erika Mustermann",
                                                                einverstanden=True), _request()))
    assert erg["ok"]
    doc2 = w.run(w.db.generated_pdfs.find_one({"id": c["id"]}, {"_id": 0, "pdf_signiert_b64": 1, "portal": 1}))
    signiert = _text(base64.b64decode(doc2["pdf_signiert_b64"]))
    assert "bestätigt Empfang von" not in signiert and "Schlüssel(n)" not in signiert and "Datum und Ort" not in signiert
    assert "Signaturnachweis" in signiert and "Erika Mustermann" in signiert
    assert doc2["portal"]["unterschrift_in_feldern"] is True, "beide Unterschriftsfelder gefunden"
    # zweiter Code fuer dieselbe Fassung: keine zweite Erzeugung noetig (gleiche Version)
    _lauf(KP.portal_zurueckziehen(c["id"], user=w.sucher))
    doc3 = w.run(w.db.generated_pdfs.find_one({"id": c["id"]}, {"_id": 0, "pdf_portal_b64": 1}))
    assert doc3["pdf_portal_b64"] == doc["pdf_portal_b64"]


def test_03_rueckfall_druckfassung_und_neue_fassung(welt):  # noqa: F811
    w = welt
    _seite_an(w)
    c = _vertrag(w, w.sucher)
    # Code vor der Umstellung (keine Portal-Fassung): der Kunde sieht die Druckfassung — nie ein neues PDF
    assert KP._portal_dokument({**c}) == base64.b64decode(c["pdf_b64"])
    # Portal-Fassung einer ALTEN Vertragsfassung zaehlt nicht
    assert KP._portal_dokument({**c, "pdf_portal_b64": base64.b64encode(b"%PDF-alt").decode(), "pdf_portal_version": 1,
                                "version": 2}) == base64.b64decode(c["pdf_b64"])
    assert KP._portal_dokument({**c, "pdf_portal_b64": base64.b64encode(b"%PDF-neu").decode(), "pdf_portal_version": 1,
                                "version": 1}) == b"%PDF-neu"
    # Fassung 2 (nach Abholung/Aenderung): der naechste Code erzeugt die Portal-Fassung neu
    _lauf(KP.portal_freigeben(c["id"], user=w.sucher))
    w.run(w.db.generated_pdfs.update_one({"id": c["id"]}, {"$set": {"version": 2}, "$unset": {"portal": ""}}))
    _lauf(KP.portal_freigeben(c["id"], user=w.sucher))
    doc = w.run(w.db.generated_pdfs.find_one({"id": c["id"]}, {"_id": 0, "pdf_portal_version": 1}))
    assert doc["pdf_portal_version"] == 2
