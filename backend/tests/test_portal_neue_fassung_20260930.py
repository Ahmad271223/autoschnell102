# -*- coding: utf-8 -*-
"""Kompletter Lauf 30.09.2026 (Sucher -> Kundenportal -> Abholung -> Chef aendert den Preis):

Der Kunde unterschreibt Fassung 1 online. Danach entsteht Fassung 2 (Preis bei der Abholung geaendert
oder Termin verschoben). Befund: die App zeigte weiter "digital unterschrieben", obwohl die Unterschrift
nur zu Fassung 1 gehoert — und das Unterschriftsbild blieb nach der Vertragsloeschung im Speicher.

Jetzt:
  * Stand `unterschrieben_alt` + `unterschrieben_version`, Meldung an Sucher und Chef,
  * die unterschriebene Fassung 1 wandert samt Nachweis ins Archiv und bleibt abrufbar (?fassung=1),
  * fuer Fassung 2 gibt es einen neuen Code; nach der zweiten Unterschrift sind beide Fassungen belegt,
  * die Fassungsliste verraet weder Code noch Adresse des Kunden,
  * die Loeschung des Vertrags raeumt die Unterschriftsbilder und die Meldungen weg.
"""
import base64

import cleanup_service
import routes.contracts as C
import routes.kundenportal as KP
from starlette.responses import Response

from test_kundenportal_20260929 import _b64, _fehler, _lauf, _png, _request, _vertrag, welt  # noqa: F401


def _unterschreiben(w, c, nutzer, name="Erika Mustermann"):
    frei = _lauf(KP.portal_freigeben(c["id"], user=nutzer))
    offen = _lauf(KP.portal_oeffnen(KP.OeffnenIn(code=frei["code"], slug="kfz-mueller"), _request()))
    _lauf(KP.portal_sitzung_pdf(offen["sitzung"]))               # Pruefliste 01.10.2026 (Nr. 2): erst lesen
    _lauf(KP.portal_unterschreiben(offen["sitzung"], KP.UnterschreibenIn(
        signature_b64=_b64(_png()), name=name, einverstanden=True), _request("198.51.100.9")))
    return frei


def _neue_fassung(w, c, **wie):
    wie = wie or {"pickup_date": "2099-09-11"}
    return _lauf(C.regenerate_contract_for_pickup(contract_id=c["id"], dealer_id=w.dealer_id, user=w.chef, **wie))


def test_unterschrift_gilt_nur_fuer_ihre_fassung(welt, monkeypatch):  # noqa: F811
    w = welt
    monkeypatch.setattr(cleanup_service, "db", w.db, raising=False)
    _lauf(KP.put_webseite(KP.WebseiteIn(slug="kfz-mueller", aktiv=True), user=w.chef))
    c = _vertrag(w, w.sucher)
    _unterschreiben(w, c, w.sucher)
    doc1 = w.run(w.db.generated_pdfs.find_one({"id": c["id"]}, {"_id": 0}))
    signiert1, key1 = base64.b64decode(doc1["pdf_signiert_b64"]), doc1["portal"]["unterschrift_key"]
    assert _lauf(KP.portal_stand(c["id"], user=w.sucher))["status"] == "unterschrieben"
    _lauf(KP.meldungen_alle_gelesen(user=w.chef))
    _lauf(KP.meldungen_alle_gelesen(user=w.sucher))

    # Fassung 2 entsteht (hier: Abholtermin verschoben — derselbe Weg wie die Preisaenderung bei der Abholung)
    assert _neue_fassung(w, c) is True
    doc2 = w.run(w.db.generated_pdfs.find_one({"id": c["id"]}, {"_id": 0}))
    assert doc2["version"] == 2 and doc2["pdf_signiert_version"] == 1
    st = _lauf(KP.portal_stand(c["id"], user=w.sucher))
    assert st["status"] == "unterschrieben_alt" and st["unterschrieben_version"] == 1 and st["aktuelle_version"] == 2
    assert st["code"] is None and st["name"] == "Erika Mustermann"
    # Archiv: Fassung 1 traegt das unterschriebene PDF und den Nachweis
    archiv = w.run(w.db.generated_pdf_versions.find_one({"contract_id": c["id"], "version": 1}, {"_id": 0}))
    assert base64.b64decode(archiv["pdf_signiert_b64"]) == signiert1
    assert archiv["portal"]["unterschrift_key"] == key1 and archiv["portal"]["name"] == "Erika Mustermann"
    assert archiv["kunde_unterschrieben_am"] == doc1["kunde_unterschrieben_am"]
    # Meldung an Sucher und Chef: die neue Fassung ist nicht unterschrieben
    for nutzer in (w.chef, w.sucher):
        liste = _lauf(KP.meldungen_liste(user=nutzer))
        assert liste[0]["typ"] == "vertrag_unterschrift_veraltet" and liste[0]["gelesen"] is False
        assert "Fassung 2" in liste[0]["text"] and "nur für Fassung 1" in liste[0]["text"] and liste[0]["ref"] == c["id"]
    # die unterschriebene Fassung 1 bleibt abrufbar — ohne Angabe und mit ?fassung=1; Fassung 2 hat keine
    for kw in ({}, {"fassung": 1}):
        pdf = _lauf(KP.portal_pdf(c["id"], user=w.sucher, **kw))
        assert pdf.body == signiert1 and pdf.headers["x-vertrag-version"] == "1"
        assert "Fassung-1" in pdf.headers["content-disposition"]
    assert _fehler(KP.portal_pdf(c["id"], fassung=2, user=w.sucher)).status_code == 404
    # die Fassungsliste zeigt, DASS Fassung 1 unterschrieben wurde — aber weder Code noch Adresse des Kunden
    fassungen = _lauf(C.list_contract_versions(c["id"], Response(), user=w.sucher))
    assert len(fassungen) == 1 and fassungen[0]["version"] == 1 and fassungen[0]["kunde_unterschrieben_am"]
    assert "portal" not in fassungen[0] and "pdf_signiert_b64" not in fassungen[0]

    # neuer Code fuer Fassung 2 (kein 409 mehr), der alte Nachweis geht dabei nicht verloren
    frei2 = _lauf(KP.portal_freigeben(c["id"], user=w.sucher))
    assert frei2["status"] == "offen" and frei2["version"] == 2 and frei2["unterschrieben_version"] == 1
    offen = _lauf(KP.portal_oeffnen(KP.OeffnenIn(code=frei2["code"], slug="kfz-mueller"), _request()))
    _lauf(KP.portal_sitzung_pdf(offen["sitzung"]))               # Pruefliste 01.10.2026 (Nr. 2): erst lesen
    _lauf(KP.portal_unterschreiben(offen["sitzung"], KP.UnterschreibenIn(
        signature_b64=_b64(_png()), name="Erika Mustermann", einverstanden=True), _request()))
    doc3 = w.run(w.db.generated_pdfs.find_one({"id": c["id"]}, {"_id": 0}))
    signiert2, key2 = base64.b64decode(doc3["pdf_signiert_b64"]), doc3["portal"]["unterschrift_key"]
    assert doc3["pdf_signiert_version"] == 2 and key2 != key1 and signiert2 != signiert1
    st = _lauf(KP.portal_stand(c["id"], user=w.chef))
    assert st["status"] == "unterschrieben" and st["unterschrieben_version"] == 2
    assert _lauf(KP.portal_pdf(c["id"], user=w.chef)).body == signiert2
    assert _lauf(KP.portal_pdf(c["id"], fassung=2, user=w.chef)).body == signiert2
    assert _lauf(KP.portal_pdf(c["id"], fassung=1, user=w.chef)).body == signiert1
    assert key1 in w.ablage and key2 in w.ablage

    # Loeschung des Vertrags: beide Unterschriftsbilder und die Meldungen gehen mit
    geloescht = []

    async def loeschen(_db, key=None, prefix=None, grund="", dealer_id="", ref=None, art="storage"):
        geloescht.append((key, grund))
        w.ablage.pop(key, None)
        return True
    monkeypatch.setattr(cleanup_service, "loeschen_oder_vormerken", loeschen)
    assert w.run(w.db.meldungen.count_documents({"ref": c["id"]})) >= 3
    assert _lauf(cleanup_service.vertrag_endgueltig_loeschen(w.db, c["id"], scrub_pii=True, grund="test")) is True
    assert {k for k, _ in geloescht} >= {key1, key2} and key1 not in w.ablage and key2 not in w.ablage
    assert all(g == "vertrag_geloescht_portal_unterschrift" for k, g in geloescht if k in (key1, key2))
    assert w.run(w.db.meldungen.count_documents({"ref": c["id"]})) == 0
    assert w.run(w.db.generated_pdfs.count_documents({"id": c["id"]})) == 0
    assert w.run(w.db.generated_pdf_versions.count_documents({"contract_id": c["id"]})) == 0


def test_ohne_unterschrift_keine_meldung_und_kein_nachweis(welt):  # noqa: F811
    w = welt
    _lauf(KP.put_webseite(KP.WebseiteIn(slug="kfz-mueller", aktiv=True), user=w.chef))
    c = _vertrag(w, w.sucher)
    frei = _lauf(KP.portal_freigeben(c["id"], user=w.sucher))            # Code offen, nicht unterschrieben
    assert _neue_fassung(w, c) is True
    archiv = w.run(w.db.generated_pdf_versions.find_one({"contract_id": c["id"], "version": 1}, {"_id": 0}))
    assert "pdf_signiert_b64" not in archiv and "portal" not in archiv
    assert w.run(w.db.meldungen.count_documents({"ref": c["id"]})) == 0
    st = _lauf(KP.portal_stand(c["id"], user=w.sucher))
    assert st["status"] == "fassung_veraltet" and st["unterschrieben_version"] is None
    # der alte Code oeffnet die neue Fassung nicht
    assert _fehler(KP.portal_oeffnen(KP.OeffnenIn(code=frei["code"], slug="kfz-mueller"), _request())).status_code == 404
