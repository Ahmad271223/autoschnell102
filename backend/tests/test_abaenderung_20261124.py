# -*- coding: utf-8 -*-
"""Abänderungsvertrag nach der Unterschrift (Wunsch Ahmad 24.11.2026).

Ändert das Büro einen schon unterschriebenen Kaufvertrag (neue Fassung) oder ergeben sich bei der Abholung
Änderungen (Preis, Schaden, fehlende Ausstattung, Nachlackierung aus der Lackdicke-Messung), entsteht auf Wunsch
ein eigenes PDF „Abänderungsvertrag“ mit NUR den Änderungen. Geprüft:
  * PDF enthält nur die Änderungen (bisher → neu), keine Fassung, kein „ohne Unterschrift“,
  * die unterschriebene Ursprungsfassung bleibt unverändert,
  * Versand nur per Knopf und nie doppelt (409 bereits_versendet, erneut=true),
  * Online-Unterschrift über den Link setzt den Stand, Meldung an das Büro, Kunde ohne Nachweisblatt,
  * fremde Firma / fremder Sucher gesperrt, falscher Link 404,
  * Lackschwelle, keine Änderungen -> kein Abänderungsvertrag, nicht unterschrieben -> keiner.
In-Prozess gegen eine Wegwerf-Datenbank (Welt aus test_kundenportal_20260929), keine echten Mails.
"""
import base64
import io
import re
from datetime import datetime, timedelta, timezone

import pytest

import abaenderung as AB
import cleanup_service
import email_service
import provider_fetch
import routes.abaenderungen as AR
import routes.contracts as C
import routes.kundenportal as KP
import routes.protocols as P

from test_kundenportal_20260929 import _b64, _fehler, _lauf, _png, _request, _vertrag, welt  # noqa: F401


def _text(pdf: bytes) -> str:
    from pypdf import PdfReader
    return " ".join(" ".join((s.extract_text() or "").split()) for s in PdfReader(io.BytesIO(pdf)).pages)


@pytest.fixture
def w(welt, monkeypatch):  # noqa: F811
    """Welt + eigene Module auf die Wegwerf-DB, Mailversand abgefangen (nie echt)."""
    monkeypatch.setattr(AR, "db", welt.db)
    monkeypatch.setattr(cleanup_service, "db", welt.db, raising=False)

    async def loeschen(_db, key=None, prefix=None, grund="", dealer_id="", ref=None, art="storage"):
        welt.ablage.pop(key, None)
        return True
    monkeypatch.setattr(cleanup_service, "loeschen_oder_vormerken", loeschen)
    import auto_daten

    async def _nichts(*a, **k):
        return None
    monkeypatch.setattr(auto_daten, "nachfuehren", _nichts)
    post = []

    async def senden(to, subject, text, anhang=None, anhang_name="", **kw):
        post.append({"to": to, "subject": subject, "text": text, "anhang": anhang, "anhang_name": anhang_name, **kw})
        return True, f"resend:test-{len(post)}"
    monkeypatch.setattr(email_service, "send_email_mit_beleg", senden)
    monkeypatch.setattr(email_service, "email_configured", lambda: True)
    monkeypatch.setattr(provider_fetch, "MOCK_PROVIDER_FETCH", False)
    monkeypatch.setattr(AR, "_index_fertig", False)
    welt.post = post
    return welt


def _wie_dialog(w, c, **cd_extra):
    """Was der Vertragsdialog zusätzlich speichert (Abholdatum, unser Standardsatz mit Platzhaltern)."""
    from vertrag_vorlagen import BESONDERE_VEREINBARUNGEN
    felder = {"pickup_date": "2026-10-03", "additional_terms": BESONDERE_VEREINBARUNGEN, **cd_extra}
    w.run(w.db.generated_pdfs.update_one({"id": c["id"]}, {"$set": {f"contract_data.{k}": v for k, v in felder.items()}}))


def _vertrag_unterschrieben(w, nutzer=None, **cd_extra) -> dict:
    nutzer = nutzer or w.sucher
    _lauf(KP.put_webseite(KP.WebseiteIn(slug="kfz-mueller", aktiv=True), user=w.chef))
    c = _vertrag(w, nutzer)
    _wie_dialog(w, c, **cd_extra)
    frei = _lauf(KP.portal_freigeben(c["id"], user=nutzer))
    offen = _lauf(KP.portal_oeffnen(KP.OeffnenIn(code=frei["code"], slug="kfz-mueller"), _request()))
    _lauf(KP.portal_sitzung_pdf(offen["sitzung"]))
    _lauf(KP.portal_unterschreiben(offen["sitzung"], KP.UnterschreibenIn(
        signature_b64=_b64(_png()), name="Erika Mustermann", einverstanden=True), _request("198.51.100.9")))
    return c


def _aendern(w, c, user, **felder):
    """Büro ändert den Vertrag (derselbe Weg wie der Dialog „Kaufvertrag ändern“)."""
    cd = w.run(w.db.generated_pdfs.find_one({"id": c["id"]}, {"_id": 0, "contract_data": 1}))["contract_data"]
    body = C.ContractIn(vehicle_id="v-egal", seller_name=cd["seller_name"],
                        purchase_price=felder.pop("purchase_price", cd["purchase_price"]), **felder)
    return _lauf(C.vertrag_nachtraeglich_aendern(c["id"], body, user=user))


def _token(post: dict) -> str:
    m = re.search(r"/abaenderung#([A-Za-z0-9_\-]+)", post["text"])
    assert m, post["text"]
    return m.group(1)


# ------------------------------------------------------------------ rein: Lackschwelle, Vergleich
def test_01_lackschwelle_und_nachlackierung():
    assert AB.LACK_AUFFAELLIG_AB_UM == 160
    mess = [{"zone": "Motorhaube", "wert_um": 160}, {"zone": "Dach", "wert_um": 95}, {"zone": "Tür", "wert_um": None}]
    assert AB.lack_auffaellig(mess) == [] and AB.lack_korrektur(mess) == {} and AB.lack_befund(mess, {}) is None
    mess += [{"zone": "Motorhaube", "wert_um": 161}, {"zone": "Tür vorne links", "wert_um": 240},
             {"zone": "Motorhaube", "wert_um": 210}, {"zone": "x", "wert_um": True}, "kaputt"]
    auff = AB.lack_auffaellig(mess)
    assert auff == [{"zone": "Tür vorne links", "wert_um": 240}, {"zone": "Motorhaube", "wert_um": 210}]
    assert AB.lack_text(auff) == "Ja, Tür vorne links (240 µm), Motorhaube (210 µm)"
    assert AB.lack_korrektur(mess) == {"nachlackierungsfrei": "Nein"}
    b = AB.lack_befund(mess, {"nachlackierungsfrei": "Ja"})
    assert b["abweichend"] is True and b["vertrag"] == "Ja" and b["text"].startswith("Ja, Tür vorne links")
    assert AB.lack_befund(mess, {"nachlackierungsfrei": "Nein"})["abweichend"] is False
    # im Abaenderungsvertrag: "Nachlackierung: Nein -> Ja, Bauteil (Wert)" statt der Zeile Nachlackierungsfrei
    alt = {"nachlackierungsfrei": "Ja", "purchase_price": 8500}
    neu = {"nachlackierungsfrei": "Nein", "purchase_price": 8500}
    z = AB.aenderungen(alt, neu, protokoll={"lackmessungen": mess})
    assert [AB.zeile_text(x) for x in z] == ["Nachlackierung: Nein → Ja, Tür vorne links (240 µm), Motorhaube (210 µm)"]
    # ohne Protokoll: die normale Feldzeile
    assert [AB.zeile_text(x) for x in AB.aenderungen(alt, neu)] == ["Nachlackierungsfrei: Ja → Nein"]
    # Freigabe-Liste des Bueros traegt den Befund schon vor der Unterschrift
    import inspect
    assert '"nachlackierung": lack_befund(d.get("lackmessungen"), vertrag)' in inspect.getsource(P.protokolle_zur_freigabe)
    assert "lack_korrektur(protokoll.get(\"lackmessungen\"))" in inspect.getsource(P.protokoll_korrekturen)


def test_02_vergleich_nur_aenderungen_und_keine_aenderung_leer():
    a = {"purchase_price": 8500.0, "vehicle_mileage": "120000", "seller_name": "Max", "notes": "intern",
         "fassung": 1, "logo_key": "a", "damages_text": "• Delle: Tür vorne links", "pickup_date": "2026-12-01"}
    assert AB.aenderungen(a, dict(a)) == []
    assert AB.aenderungen(a, {**a, "fassung": 3, "logo_key": "b", "notes": "anders", "vehicle_mileage": "120.000"}) == []
    b = {**a, "purchase_price": 8100, "vehicle_mileage": "123456",
         "damages_text": "• Delle: Tür vorne links\n• Kratzer: Tür hinten links (bei Abholung festgestellt)",
         "pickup_date": "2026-12-05"}
    zeilen = [AB.zeile_text(z) for z in AB.aenderungen(a, b, protokoll={"features": {"Navigationssystem": False,
                                                                                     "Klima": True, "AHK": "fehlt"}})]
    assert zeilen == ["Kaufpreis: 8.500 € → 8.100 €", "Kilometerstand: 120.000 km → 123.456 km",
                      "Abholdatum: 01.12.2026 → 05.12.2026",
                      "Neuer Schaden: Kratzer: Tür hinten links (bei Abholung festgestellt)",
                      "Ausstattung entfällt: AHK", "Ausstattung entfällt: Navigationssystem"]
    assert AB.euro(8500.5) == "8.500,50 €" and AB.euro(None) == "keine Angabe"
    # Platzhalter: verglichen wird der Rohtext, angezeigt der eingesetzte (nie „{abholdatum}“)
    roh = "• Übergabe am {abholdatum}."
    einsetzen = lambda t, d: t.replace("{abholdatum}", d.get("pickup_date") or "____")  # noqa: E731
    assert AB.aenderungen({**a, "additional_terms": roh}, {**a, "additional_terms": roh, "pickup_date": "2026-12-01"},
                          text_ersetzen=einsetzen) == []
    z = AB.aenderungen({**a, "additional_terms": roh}, {**a, "additional_terms": roh + " Bar."}, text_ersetzen=einsetzen)
    assert [AB.zeile_text(x) for x in z] == [
        "Besondere Vereinbarungen: • Übergabe am 2026-12-01. → • Übergabe am 2026-12-01. Bar."]


# ------------------------------------------------------------------ Büro: anlegen, PDF, Ursprungsfassung
def test_03_nicht_unterschrieben_oder_ohne_aenderung_kein_abaenderungsvertrag(w):
    c = _vertrag(w, w.sucher)
    st = _lauf(AR.abaenderungen_liste(c["id"], user=w.sucher))
    assert st["unterschrieben"] is False and st["vorschlag"] == []
    e = _fehler(AR.abaenderung_anlegen(c["id"], AR.AnlegenIn(art="online"), user=w.sucher))
    assert e.status_code == 409 and e.detail["code"] == "nicht_unterschrieben"
    c2 = _vertrag_unterschrieben(w)
    st = _lauf(AR.abaenderungen_liste(c2["id"], user=w.sucher))
    assert st["unterschrieben"] is True and st["quelle"] == "online" and st["basis_version"] == 1 and st["vorschlag"] == []
    e = _fehler(AR.abaenderung_anlegen(c2["id"], AR.AnlegenIn(art="online"), user=w.sucher))
    assert e.status_code == 409 and e.detail["code"] == "keine_aenderungen"
    assert w.run(w.db.vertrag_abaenderungen.count_documents({})) == 0


def test_04_pdf_nur_aenderungen_ursprungsfassung_unveraendert(w):
    c = _vertrag_unterschrieben(w)
    vorher = w.run(w.db.generated_pdfs.find_one({"id": c["id"]}, {"_id": 0}))
    assert vorher["pdf_signiert_version"] == 1
    assert _aendern(w, c, w.chef, purchase_price=11900.0)["geaendert"] is True
    nach_fassung = w.run(w.db.generated_pdfs.find_one({"id": c["id"]}, {"_id": 0}))
    archiv = w.run(w.db.generated_pdf_versions.find_one({"contract_id": c["id"], "version": 1}, {"_id": 0}))
    st = _lauf(AR.abaenderungen_liste(c["id"], user=w.sucher))
    assert st["version"] == 2 and st["basis_version"] == 1
    assert st["vorschlag_zeilen"] == ["Kaufpreis: 12.500 € → 11.900 €"]
    assert w.post == [], "Anlegen/Ändern verschickt nie selbst"
    ab = _lauf(AR.abaenderung_anlegen(c["id"], AR.AnlegenIn(art="online"), user=w.sucher))
    assert ab["status"] == "offen" and ab["zeilen"] == ["Kaufpreis: 12.500 € → 11.900 €"] and w.post == []
    # idempotent: derselbe Stand -> derselbe Abaenderungsvertrag
    nochmal = _lauf(AR.abaenderung_anlegen(c["id"], AR.AnlegenIn(art="email"), user=w.chef))
    assert nochmal["id"] == ab["id"] and nochmal["bereits_vorhanden"] is True
    assert _lauf(AR.abaenderungen_liste(c["id"], user=w.sucher))["vorschlag"] == []
    pdf = _lauf(AR.abaenderung_pdf(c["id"], ab["id"], user=w.sucher)).body
    t = _text(pdf)
    assert "ABÄNDERUNGSVERTRAG" in t and "zum KFZ-Kaufvertrag vom" in t
    assert f"KV-{w.s}" not in t and "VERTRAGS-NR" not in t          # 28.11.2026: keine Vertrags-Nr. mehr
    assert "Kaufpreis: 12.500 € → 11.900 €" in t and "Golf VII" in t
    assert "Alle übrigen Vereinbarungen des Kaufvertrags bleiben unverändert" in t
    for nie in ("Kilometerstand", "Erstzulassung", "Diesel", "Fassung", "ohne Unterschrift", "Signaturnachweis"):
        assert nie not in t, nie
    assert t.count("Unterschrift") >= 2
    # Ursprungsfassung (unterschrieben) bleibt Byte fuer Byte
    jetzt = w.run(w.db.generated_pdfs.find_one({"id": c["id"]}, {"_id": 0}))
    for k in ("pdf_b64", "pdf_signiert_b64", "pdf_signiert_version", "version", "contract_data", "portal"):
        assert jetzt.get(k) == nach_fassung.get(k), k
    assert base64.b64decode(archiv["pdf_signiert_b64"]) == base64.b64decode(vorher["pdf_signiert_b64"])
    assert w.run(w.db.generated_pdf_versions.find_one({"contract_id": c["id"], "version": 1}, {"_id": 0})) == archiv


def test_05_versand_nur_per_knopf_nie_doppelt(w):
    c = _vertrag_unterschrieben(w)
    _aendern(w, c, w.chef, purchase_price=11900.0)
    ab = _lauf(AR.abaenderung_anlegen(c["id"], AR.AnlegenIn(art="intern"), user=w.chef))
    assert ab["status"] == "intern" and w.post == []
    an = "erika@example.org"
    r = _lauf(AR.abaenderung_senden(c["id"], ab["id"], AR.SendenIn(art="online", recipient=an), user=w.sucher))
    assert r["zustellung"] == "versendet" and len(w.post) == 1
    m = w.post[0]
    assert m["to"] == an and m["anhang"] is None and "Abänderungsvertrag" in m["subject"]
    assert "Kaufpreis: 12.500 € → 11.900 €" in m["text"] and "https://app.auto-schnellkauf.de/abaenderung#" in m["text"]
    token = _token(m)
    e = _fehler(AR.abaenderung_senden(c["id"], ab["id"], AR.SendenIn(art="online", recipient=an), user=w.sucher))
    assert e.status_code == 409 and e.detail["code"] == "bereits_versendet" and len(w.post) == 1
    r = _lauf(AR.abaenderung_senden(c["id"], ab["id"], AR.SendenIn(art="online", recipient=an, erneut=True), user=w.sucher))
    assert len(w.post) == 2 and _token(w.post[1]) == token, "derselbe Link"
    assert w.post[0]["idempotency_key"] != w.post[1]["idempotency_key"]
    # PDF per E-Mail: Anhang = das Dokument; zweites Mal 409
    _lauf(AR.abaenderung_senden(c["id"], ab["id"], AR.SendenIn(art="pdf", recipient=an), user=w.chef))
    assert len(w.post) == 3 and w.post[2]["anhang"] and "ABÄNDERUNGSVERTRAG" in _text(w.post[2]["anhang"])
    e = _fehler(AR.abaenderung_senden(c["id"], ab["id"], AR.SendenIn(art="pdf", recipient=an), user=w.chef))
    assert e.status_code == 409 and e.detail["code"] == "bereits_versendet" and len(w.post) == 3
    liste = _lauf(AR.abaenderungen_liste(c["id"], user=w.chef))["liste"]
    assert liste[0]["status"] == "gesendet" and len(liste[0]["versand"]) == 3
    assert "pdf_b64" not in liste[0] and "online" not in liste[0]
    # Fehlschlag beim Anbieter: kein Eintrag bleibt haengen, erneut versuchbar
    async def kaputt(*a, **k):
        return False, ""
    import email_service as E
    alt = E.send_email_mit_beleg
    E.send_email_mit_beleg = kaputt
    try:
        assert _fehler(AR.abaenderung_senden(c["id"], ab["id"], AR.SendenIn(art="pdf", recipient="x@example.org"),
                                             user=w.chef)).status_code == 502
    finally:
        E.send_email_mit_beleg = alt
    doc = w.run(w.db.vertrag_abaenderungen.find_one({"id": ab["id"]}, {"_id": 0}))
    assert all(v["zustellung"] != "laeuft" for v in doc["versand"])
    assert _fehler(AR.abaenderung_senden(c["id"], ab["id"], AR.SendenIn(art="pdf", recipient="kein"),
                                         user=w.chef)).status_code == 400


def test_06_online_unterschrift_setzt_stand_und_meldet(w):
    c = _vertrag_unterschrieben(w)
    _aendern(w, c, w.chef, purchase_price=11900.0)
    ab = _lauf(AR.abaenderung_anlegen(c["id"], AR.AnlegenIn(art="online"), user=w.sucher))
    _lauf(AR.abaenderung_senden(c["id"], ab["id"], AR.SendenIn(art="online", recipient="erika@example.org"), user=w.sucher))
    token = _token(w.post[0])
    for nutzer in (w.chef, w.sucher):
        _lauf(KP.meldungen_alle_gelesen(user=nutzer))
    info = _lauf(AR.public_abaenderung(token, _request()))["abaenderung"]
    assert info["status"] == "offen" and info["zeilen"] == ["Kaufpreis: 12.500 € → 11.900 €"]
    assert info["firma"] == "KFZ Müller GmbH" and "pdf_b64" not in str(info)
    sig = AR.UnterschreibenIn(signature_b64=_b64(_png()), name="Erika Mustermann", einverstanden=True)
    # erst lesen, dann unterschreiben
    assert _fehler(AR.public_abaenderung_unterschreiben(token, sig, _request())).status_code == 409
    gelesen = _lauf(AR.public_abaenderung_pdf(token, _request())).body
    assert "ABÄNDERUNGSVERTRAG" in _text(gelesen)
    ohne = AR.UnterschreibenIn(signature_b64=_b64(_png()), name="Erika Mustermann", einverstanden=False)
    assert _fehler(AR.public_abaenderung_unterschreiben(token, ohne, _request())).status_code == 400
    r = _lauf(AR.public_abaenderung_unterschreiben(token, sig, _request("198.51.100.20")))
    assert r["ok"] is True
    doc = w.run(w.db.vertrag_abaenderungen.find_one({"id": ab["id"]}, {"_id": 0}))
    assert doc["status"] == "unterschrieben" and doc["unterschrift_name"] == "Erika Mustermann"
    assert doc["unterschrift_in_feldern"] is True and doc["pdf_b64"] and doc["pdf_signiert_b64"]
    assert len(w.post) == 1, "ohne Einstellung keine automatische Mail nach der Unterschrift"
    for nutzer in (w.chef, w.sucher):
        m = _lauf(KP.meldungen_liste(user=nutzer))[0]
        assert m["typ"] == "abaenderung_unterschrieben" and "Erika Mustermann" in m["text"] and m["ref"] == c["id"]
    # Firma: mit Signaturnachweis; Verkaeufer: ohne
    firma_pdf = _text(_lauf(AR.abaenderung_pdf(c["id"], ab["id"], user=w.chef)).body)
    kunde_pdf = _text(_lauf(AR.public_abaenderung_pdf(token, _request())).body)
    assert "Signaturnachweis" in firma_pdf and "Signaturnachweis" not in kunde_pdf and "ABÄNDERUNGSVERTRAG" in kunde_pdf
    assert _fehler(AR.public_abaenderung_unterschreiben(token, sig, _request())).status_code == 409
    st = _lauf(AR.abaenderungen_liste(c["id"], user=w.sucher))
    assert st["quelle"] == "abaenderung" and st["basis_version"] == 2 and st["vorschlag"] == []
    assert st["liste"][0]["status"] == "unterschrieben" and st["liste"][0]["pdf_unterschrieben"] is True
    # Per Knopf: unterschriebene Fassung per E-Mail — ohne Nachweisblatt
    _lauf(AR.abaenderung_senden(c["id"], ab["id"], AR.SendenIn(art="pdf", recipient="erika@example.org"), user=w.chef))
    t = _text(w.post[-1]["anhang"])
    assert "Unterschriebener" in w.post[-1]["subject"] and "Signaturnachweis" not in t and "Erika Mustermann" in t
    # weitere Aenderung -> neuer Vorschlag gegen die unterschriebene Abaenderung; Online-Link der alten bleibt zu
    _aendern(w, c, w.chef, purchase_price=11500.0)
    st = _lauf(AR.abaenderungen_liste(c["id"], user=w.sucher))
    assert st["vorschlag_zeilen"] == ["Kaufpreis: 11.900 € → 11.500 €"]
    # Vertragsloeschung raeumt die Abaenderungsvertraege weg
    assert _lauf(cleanup_service.vertrag_endgueltig_loeschen(w.db, c["id"], scrub_pii=True, grund="test")) is True
    assert w.run(w.db.vertrag_abaenderungen.count_documents({"contract_id": c["id"]})) == 0
    assert _fehler(AR.public_abaenderung(token, _request())).status_code == 404


def test_07_nach_unterschrift_automatisch_nur_wenn_eingestellt(w):
    c = _vertrag_unterschrieben(w)
    _aendern(w, c, w.chef, purchase_price=11900.0)
    ab = _lauf(AR.abaenderung_anlegen(c["id"], AR.AnlegenIn(art="online"), user=w.chef))
    _lauf(AR.abaenderung_senden(c["id"], ab["id"], AR.SendenIn(art="online", recipient="erika@example.org",
                                                                 nach_unterschrift_senden=True), user=w.chef))
    token = _token(w.post[0])
    _lauf(AR.public_abaenderung_pdf(token, _request()))
    _lauf(AR.public_abaenderung_unterschreiben(token, AR.UnterschreibenIn(
        signature_b64=_b64(_png()), name="Erika Mustermann", einverstanden=True), _request()))
    assert len(w.post) == 2 and w.post[1]["anhang"] and "Signaturnachweis" not in _text(w.post[1]["anhang"])
    # danach per Knopf: schon verschickt -> 409 (nie doppelt)
    e = _fehler(AR.abaenderung_senden(c["id"], ab["id"], AR.SendenIn(art="pdf", recipient="erika@example.org"),
                                      user=w.chef))
    assert e.status_code == 409 and e.detail["code"] == "bereits_versendet"


def test_08_fremde_firma_und_fremder_sucher_gesperrt(w):
    c = _vertrag_unterschrieben(w)
    _aendern(w, c, w.chef, purchase_price=11900.0)
    ab = _lauf(AR.abaenderung_anlegen(c["id"], AR.AnlegenIn(art="online"), user=w.sucher))
    d2 = f"d2-{w.s}"
    fremd = {"id": f"chef2-{w.s}", "dealer_id": d2, "role": "dealer", "active": True}
    w.run(w.db.dealers.insert_one({"id": d2, "user_id": fremd["id"], "company_name": "Fremd GmbH"}))
    kollege = {"id": f"s2-{w.s}", "dealer_id": w.dealer_id, "role": "sucher", "active": True}
    for nutzer in (fremd, kollege):
        assert _fehler(AR.abaenderungen_liste(c["id"], user=nutzer)).status_code == 404
        assert _fehler(AR.abaenderung_anlegen(c["id"], AR.AnlegenIn(art="online"), user=nutzer)).status_code == 404
        assert _fehler(AR.abaenderung_pdf(c["id"], ab["id"], user=nutzer)).status_code == 404
        assert _fehler(AR.abaenderung_senden(c["id"], ab["id"], AR.SendenIn(art="pdf", recipient="a@example.org"),
                                             user=nutzer)).status_code == 404
    # eigener Vertrag der fremden Firma + fremde Abaenderungs-ID: 404
    assert w.post == []
    assert _fehler(AR.public_abaenderung("x" * 43, _request())).status_code == 404
    assert _fehler(AR.public_abaenderung("", _request())).status_code == 404


def test_09_abholung_mit_lack_und_fehlender_ausstattung(w):
    """Unterschrieben bei der Übergabe (finales Protokoll), danach die neue Fassung aus der Abholung: Preis,
    Nachlackierung aus der Lackdicke und fehlende Ausstattung stehen im Abänderungsvertrag."""
    c = _vertrag(w, w.sucher)
    _wie_dialog(w, c, nachlackierungsfrei="Ja")
    appt = {"id": f"a-{w.s}", "dealer_id": w.dealer_id, "contract_id": c["id"], "created_by": w.sucher["id"],
            "status": "abgeholt"}
    w.run(w.db.appointments.insert_one(dict(appt)))
    vorbei = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    prot = {"id": f"p-{w.s}", "appointment_id": appt["id"], "dealer_id": w.dealer_id, "status": "final",
            "finalized_at": vorbei, "features": {"Navigationssystem": False, "Klimaanlage": True},
            "lackmessungen": [{"id": "l1", "view": "top", "zone": "Motorhaube", "x": 1, "y": 1, "wert_um": 210},
                              {"id": "l2", "view": "top", "zone": "Dach", "x": 1, "y": 1, "wert_um": 110}]}
    w.run(w.db.pickup_protocols.insert_one(dict(prot)))
    korr, _schaeden = _lauf(P.protokoll_korrekturen(appt, prot))
    assert korr.get("nachlackierungsfrei") == "Nein"
    assert _lauf(C.regenerate_contract_for_pickup(
        contract_id=c["id"], dealer_id=w.dealer_id, user=w.chef, neuer_preis=11900.0, korrekturen=korr,
        grund="abholung_abgeschlossen", protokoll_id=prot["id"])) is True
    doc = w.run(w.db.generated_pdfs.find_one({"id": c["id"]}, {"_id": 0}))
    assert doc["version"] == 2 and doc["contract_data"]["nachlackierungsfrei"] == "Nein"
    st = _lauf(AR.abaenderungen_liste(c["id"], user=w.chef))
    assert st["quelle"] == "uebergabe" and st["basis_version"] == 1
    assert st["vorschlag_zeilen"] == ["Kaufpreis: 12.500 € → 11.900 €", "Ausstattung entfällt: Navigationssystem",
                                      "Nachlackierung: Nein → Ja, Motorhaube (210 µm)"]
    ab = _lauf(AR.abaenderung_anlegen(c["id"], AR.AnlegenIn(art="email"), user=w.chef))
    t = _text(_lauf(AR.abaenderung_pdf(c["id"], ab["id"], user=w.chef)).body)
    assert "Nachlackierung: Nein → Ja, Motorhaube (210 µm)" in t and "Nachlackierungsfrei" not in t
    assert "Ausstattung entfällt: Navigationssystem" in t and "Dach" not in t
