# -*- coding: utf-8 -*-
"""Pruefliste 30.09.2026 (Kundenportal Nr. 4, 5, 7, 29):

  * unterschrieben wird das GESPEICHERTE Dokument — die Seiten, die der Kunde gelesen hat, bleiben;
    die Unterschriften stehen in den Feldern, ein Signaturnachweis mit Pruefsumme wird angefuegt;
    nichts wird neu aus Vertrags- und heutigen Fahrzeugdaten erzeugt
  * gleichzeitige Absendungen: EINE rechnet, die anderen bekommen sofort 409
  * ein gescheiterter Versuch gibt den Anspruch wieder frei
"""
import asyncio
import base64
import hashlib
import io

from pypdf import PdfReader

import portal_pdf
import routes.kundenportal as KP
from pdf_service import generate_contract_pdf

from test_kundenportal_20260929 import _b64, _fehler, _lauf, _png, _request, _vertrag, welt  # noqa: F401

CD = {"seller_name": "Erika Mustermann", "seller_address": "Musterweg 2", "seller_zip": "10115", "seller_city": "Berlin",
      "purchase_price": 12500.0, "vehicle_make": "VW", "vehicle_model": "Golf VII", "vehicle_mileage": "85000",
      "vehicle_first_registration": "05/2019", "contract_no": "KV-PDF30",
      # ein einzelnes Wort "Unterschrift" im Vertragstext darf nicht als Unterschriftsfeld gelten
      "additional_terms": "Mit seiner Unterschrift bestätigt der Verkäufer die Angaben.\nUnterschrift"}
HAENDLER = {"company_name": "KFZ Müller GmbH", "address": "Hauptstraße 1", "zip_code": "12345", "city": "Berlin"}


def _text(pdf: bytes, seite=None) -> str:
    seiten = PdfReader(io.BytesIO(pdf)).pages
    return "".join((s.extract_text() or "") for s in (seiten if seite is None else [seiten[seite]]))


def test_unterschrieben_wird_das_gespeicherte_dokument():
    original = generate_contract_pdf(dealer=HAENDLER, vehicle={"make_label": "VW", "model_label": "Golf VII"}, contract=CD)
    leser = PdfReader(io.BytesIO(original))
    n = len(leser.pages)
    felder = portal_pdf.felder_finden(leser)
    assert felder is not None
    nr, links, rechts = felder
    assert nr == n - 1 and links[0] < rechts[0] and abs(links[1] - rechts[1]) <= 4, felder
    assert links[1] < 300, "das Paar unten auf der Seite — nicht das einzelne Wort im Vertragstext"
    pdf, summe, in_feldern = portal_pdf.unterschreiben(
        original, verkaeufer_png=_png(), kaeufer_png=_png(), name="Erika Mustermann", zeit="30.09.2026, 10:15",
        firma="KFZ Müller GmbH", vertragsnummer="KV-PDF30", fassung=1)
    assert summe == hashlib.sha256(original).hexdigest() and in_feldern is True
    assert pdf[:4] == b"%PDF" and len(PdfReader(io.BytesIO(pdf)).pages) == n + 1
    # die gelesenen Seiten bleiben: ihr ganzer Text steht unveraendert in der unterschriebenen Fassung
    for i in range(n):
        alt, neu = _text(original, i), _text(pdf, i)
        for zeile in [z for z in alt.splitlines() if z.strip()]:
            assert zeile in neu, (i, zeile)
    # in den Feldern: Name und Zeitpunkt neben der Beschriftung; beim Haendler seit 04.10.2026 (Wunsch Ahmad)
    # NICHT mehr "hinterlegte Unterschrift" — die Seite bekommt der Kunde, vermerkt ist es nur im Nachweis
    letzte = _text(pdf, n - 1)
    assert "Erika Mustermann · digital am 30.09.2026, 10:15 Uhr" in letzte and "hinterlegte Unterschrift" not in letzte
    assert "KFZ Müller GmbH — hinterlegte Unterschrift" in _text(pdf, n)
    # Signaturnachweis: wer, wann, welche Fassung, Pruefsumme des gelesenen Dokuments
    blatt = _text(pdf, n)
    assert "Signaturnachweis" in blatt and "Kaufvertrag KV-PDF30 · Vertragsfassung 1" in blatt
    assert "Kundenportal von KFZ Müller GmbH am 30.09.2026, 10:15 Uhr" in blatt and summe in blatt
    anfang = f"Die Seiten 1 bis {n} sind" if n > 1 else "Die Seite 1 ist"
    assert anfang + " das unveränderte Vertragsdokument" in blatt and "Unterschriftsfelder eingesetzt" in blatt
    # ohne hinterlegte Haendler-Unterschrift: gleiches Dokument, ehrlich vermerkt
    ohne, _s, _f = portal_pdf.unterschreiben(original, verkaeufer_png=_png(), kaeufer_png=None, name="Erika Mustermann",
                                             zeit="30.09.2026, 10:15", firma="KFZ Müller GmbH", vertragsnummer="KV-PDF30", fassung=1)
    assert "ohne hinterlegte Unterschrift" in _text(ohne, n) and "hinterlegte Unterschrift" not in _text(ohne, n - 1)
    # Name mit Zeichen ausserhalb der Standardschrift bricht nichts ab
    fremd, _s, _f = portal_pdf.unterschreiben(original, verkaeufer_png=_png(), kaeufer_png=None, name="Šimić Ğül 日本",
                                              zeit="30.09.2026, 10:15", firma="KFZ Müller GmbH", vertragsnummer="KV-PDF30", fassung=2)
    assert len(PdfReader(io.BytesIO(fremd)).pages) == n + 1
    # digitale Ausfertigung (ohne Unterschriftsfelder): Unterschriften nur auf dem Nachweisblatt
    digital = generate_contract_pdf(dealer=HAENDLER, vehicle={"make_label": "VW"}, contract=CD, digital=True)
    assert portal_pdf.felder_finden(PdfReader(io.BytesIO(digital))) is None
    nur_blatt, _s, in_feldern = portal_pdf.unterschreiben(digital, verkaeufer_png=_png(), kaeufer_png=_png(), name="E M",
                                                          zeit="30.09.2026, 10:15", firma="F", vertragsnummer="K", fassung=1)
    assert in_feldern is False and "Die Unterschriften stehen auf diesem Blatt" in _text(nur_blatt)


def test_portal_unterschreibt_das_gelesene_dokument_nicht_den_heutigen_stand(welt, monkeypatch):  # noqa: F811
    w = welt
    _lauf(KP.put_webseite(KP.WebseiteIn(slug="kfz-mueller", aktiv=True), user=w.chef))
    c = _vertrag(w, w.sucher, vehicle_id=f"v-{w.s}")
    # Wunsch Ahmad 02.10.2026: beim Erzeugen des Codes entsteht die Portal-Fassung (ohne Empfangsbestaetigung)
    # aus dem Stand des Vertrags — DANACH aendert sich der Fahrzeugdatensatz, und es wird nichts mehr erzeugt.
    code = _lauf(KP.portal_freigeben(c["id"], user=w.sucher))["code"]
    w.run(w.db.vehicles.insert_one({"id": f"v-{w.s}", "dealer_id": w.dealer_id,
                                    "data": {"make_label": "ANDERE MARKE", "model_label": "ANDERES MODELL",
                                             "features": ["Heute dazugekommen"], "mileage": 999999}}))

    def _verboten(**kw):
        raise AssertionError("beim Unterschreiben darf kein neues Vertrags-PDF erzeugt werden")
    import pdf_service
    monkeypatch.setattr(pdf_service, "generate_contract_pdf", _verboten)
    sitzung = _lauf(KP.portal_oeffnen(KP.OeffnenIn(code=code, slug="kfz-mueller"), _request()))["sitzung"]
    gelesen = _lauf(KP.portal_sitzung_pdf(sitzung)).body
    assert gelesen != base64.b64decode(c["pdf_b64"]), "Portal-Fassung, nicht die Druckfassung"
    assert "bestätigt Empfang von" not in _text(gelesen) and "Datum und Ort" not in _text(gelesen)
    assert _lauf(KP.portal_unterschreiben(sitzung, KP.UnterschreibenIn(
        signature_b64=_b64(_png()), name="Erika Mustermann", einverstanden=True), _request()))["ok"]
    doc = w.run(w.db.generated_pdfs.find_one({"id": c["id"]}, {"_id": 0}))
    signiert = base64.b64decode(doc["pdf_signiert_b64"])
    text = _text(signiert)
    assert "ANDERE MARKE" not in text and "Heute dazugekommen" not in text and "999999" not in text
    for zeile in [z for z in _text(gelesen).splitlines() if z.strip()]:
        assert zeile in text, zeile
    p = doc["portal"]
    assert p["gelesen_sha256"] == hashlib.sha256(gelesen).hexdigest() and p["gelesen_sha256"] in text
    assert p["unterschrift_in_feldern"] is True and "anspruch" not in p and "anspruch_bis" not in p
    assert doc["pdf_b64"] == c["pdf_b64"], "das gelesene Dokument selbst bleibt unangetastet"
    # geoeffnet wurde gezaehlt (eine Operation: pruefen + zaehlen)
    assert p["abrufe"] == 1 and p["zuletzt_geoeffnet"]


def test_gleichzeitiges_unterschreiben_rechnet_nur_einmal(welt, monkeypatch):  # noqa: F811
    w = welt
    _lauf(KP.put_webseite(KP.WebseiteIn(slug="kfz-mueller", aktiv=True), user=w.chef))
    c = _vertrag(w, w.sucher)
    code = _lauf(KP.portal_freigeben(c["id"], user=w.sucher))["code"]
    sitzung = _lauf(KP.portal_oeffnen(KP.OeffnenIn(code=code, slug="kfz-mueller"), _request()))["sitzung"]
    _lauf(KP.portal_sitzung_pdf(sitzung))                        # Pruefliste 01.10.2026 (Nr. 2): erst lesen
    echt = KP._signiertes_pdf
    rechnungen = []

    async def _langsam(*a, **k):
        rechnungen.append(1)
        await asyncio.sleep(0.3)
        return await echt(*a, **k)
    monkeypatch.setattr(KP, "_signiertes_pdf", _langsam)
    body = KP.UnterschreibenIn(signature_b64=_b64(_png()), name="Erika Mustermann", einverstanden=True)

    async def alle():
        return await asyncio.gather(*[KP.portal_unterschreiben(sitzung, body, _request(f"198.51.100.{i}"))
                                      for i in range(8)], return_exceptions=True)
    ergebnisse = _lauf(alle())
    gut = [e for e in ergebnisse if isinstance(e, dict)]
    rest = [e for e in ergebnisse if not isinstance(e, dict)]
    assert len(gut) == 1 and gut[0]["ok"] and len(rechnungen) == 1, (len(gut), len(rechnungen), rest)
    assert all(getattr(e, "status_code", None) == 409 for e in rest), rest
    assert any("wird gerade verarbeitet" in e.detail for e in rest)
    assert len([k for k in w.ablage if k.startswith("portal/")]) == 1, "genau EIN Unterschriftsbild im Speicher"
    assert w.run(w.db.meldungen.count_documents({"ref": c["id"]})) == 1

    # ein gescheiterter Versuch gibt den Anspruch frei — der naechste klappt sofort
    c2 = _vertrag(w, w.sucher)
    code2 = _lauf(KP.portal_freigeben(c2["id"], user=w.sucher))["code"]
    sitzung2 = _lauf(KP.portal_oeffnen(KP.OeffnenIn(code=code2, slug="kfz-mueller"), _request()))["sitzung"]
    _lauf(KP.portal_sitzung_pdf(sitzung2))                       # Pruefliste 01.10.2026 (Nr. 2): erst lesen

    async def _kaputt(*a, **k):
        raise RuntimeError("PDF kaputt")
    monkeypatch.setattr(KP, "_signiertes_pdf", _kaputt)
    assert _fehler(KP.portal_unterschreiben(sitzung2, body, _request())).status_code == 500
    p = w.run(w.db.generated_pdfs.find_one({"id": c2["id"]}, {"_id": 0, "portal": 1}))["portal"]
    assert p["status"] == "offen" and "anspruch" not in p and "anspruch_bis" not in p
    monkeypatch.setattr(KP, "_signiertes_pdf", echt)
    assert _lauf(KP.portal_unterschreiben(sitzung2, body, _request()))["ok"]
    # ein haengen gebliebener Anspruch (Prozess gestorben) laeuft ab und blockiert nicht dauerhaft
    c3 = _vertrag(w, w.sucher)
    code3 = _lauf(KP.portal_freigeben(c3["id"], user=w.sucher))["code"]
    sitzung3 = _lauf(KP.portal_oeffnen(KP.OeffnenIn(code=code3, slug="kfz-mueller"), _request()))["sitzung"]
    _lauf(KP.portal_sitzung_pdf(sitzung3))                       # Pruefliste 01.10.2026 (Nr. 2): erst lesen
    w.run(w.db.generated_pdfs.update_one({"id": c3["id"]}, {"$set": {"portal.anspruch": "tot", "portal.anspruch_bis": "2099-01-01T00:00:00+00:00"}}))
    f = _fehler(KP.portal_unterschreiben(sitzung3, body, _request()))
    assert f.status_code == 409 and "wird gerade verarbeitet" in f.detail
    w.run(w.db.generated_pdfs.update_one({"id": c3["id"]}, {"$set": {"portal.anspruch_bis": "2020-01-01T00:00:00+00:00"}}))
    assert _lauf(KP.portal_unterschreiben(sitzung3, body, _request()))["ok"]
    assert KP.SIGNIER_ANSPRUCH_S == 60
