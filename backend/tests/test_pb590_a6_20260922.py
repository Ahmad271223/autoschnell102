# -*- coding: utf-8 -*-
"""Prüfbericht 20.09.2026, Reparaturwelle A6 (22.09.2026): Kaufvertrag,
Abholprotokoll-PDF, Beweisdokument, Mailversand, Aufräumen.

P-08   Fußzeile des Kaufvertrags kürzt einen langen Firmennamen (wie das
       Protokoll); auf_breite wohnt jetzt in pdf_schrift.
P-10   Hängender SMTP-Idempotenz-Eintrag sperrt nach 24 h nicht mehr.
P-11   Mailanhang-Dateiname wird gesäubert (Altverträge).
P-13   Digitale Fassung nimmt die Käufer-Basis des Erstellers (kaeufer_basis).
P-15   Ausstattung als Text bricht den Vertrag nicht.
P-16   Kaufpreis im deutschen Format; gescheiterte Neuerzeugung -> Betriebsalarm.
P-18   Zu großer Anhang: (False, "anhang_zu_gross") und 413 mit Link-Weg.
P-20   hu_until/service_book_until: MM/JJJJ normiert, Unlesbares abgelehnt.
P-28   Monat/Jahr-Spannen im Beweisdokument (siehe test_beweis_pdf test_19).
P-30   Leere Ausstattungsliste bricht das Protokoll nicht.
P-32   Protokoll-Tabellen bleiben innerhalb des Rahmens (TABELLEN_B).
P-33   Kilometerstand bei Abholung mit Tausenderpunkt.
P-34   Kleine Fotos werden im Beweisdokument nicht vergrößert.
P-35   Fehlender Abrufzeitpunkt -> "spätestens am" (Vormerkung).
P-36   useA85 einmal beim Import statt je Aufbau.
R1-07  Grabstein VOR der Protokoll-Prüfung, bei Ablehnung zurückgenommen.
R1-33  Kein "90-Tage"-Kommentar mehr bei der Vertragsfrist.
R2-10  Fahrzeugstatus "Vertrag erstellt" nur ohne gesperrten Lebenszyklus.

In-Prozess mit der Wegwerf-Welt aus test_befunde_runde17_termine (nur Mongo).
"""
import asyncio
import inspect
import io
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException
from PIL import Image
from pydantic import ValidationError
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.pdfbase.pdfmetrics import stringWidth

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
from test_befunde_runde17_termine import _module, welt  # noqa: E402,F401


def _text(pdf: bytes) -> str:
    from pypdf import PdfReader
    roh = "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(pdf)).pages)
    return " ".join(roh.split())


def _vertrag(**extra):
    c = {"seller_name": "Max Muster", "seller_address": "Weg 1", "seller_zip": "10115",
         "seller_city": "Berlin", "purchase_price": 15900, "payment_method": "bar",
         "pickup_date": "2026-10-03", "contract_no": "KV-A6-1"}
    c.update(extra)
    return c


def _pdf_vertrag(vehicle=None, dealer=None, **extra) -> bytes:
    from pdf_service import generate_contract_pdf
    return generate_contract_pdf(
        dealer=dealer or {"company_name": "Autohaus Test", "address": "Weg 1",
                          "zip_code": "10115", "city": "Berlin"},
        vehicle=vehicle if vehicle is not None else {"make_label": "BMW", "model_label": "320d"},
        contract=_vertrag(**extra))


def _foto(w: int, h: int) -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (w, h), (30, 90, 160)).save(b, "JPEG", quality=70)
    return b.getvalue()


# ============================================================ P-08
def test_p08_fusszeile_des_kaufvertrags_kuerzt_langen_firmennamen():
    import pdf_service as PS
    from pdf_schrift import auf_breite, ersatz_fuer
    firma = "Autohaus Müller-Lüdenscheidt Fahrzeughandel und Service GmbH & Co. KG"
    assert len(firma) >= 60
    mitte = "Kaufvertrag KV-2026-000123 · erstellt am 22.09.2026 · digitale Ausfertigung · Fassung 2"
    # Nur die Fusszeile zeichnen (Canvas-Fabrik des Kaufvertrags)
    buf = io.BytesIO()
    c = PS._numbered_canvas_factory(firma, mitte)(buf, pagesize=A4)
    c.showPage()
    c.save()
    text = _text(buf.getvalue())
    schrift = ersatz_fuer("Helvetica")
    links_max = (PS.PAGE_W / 2 - stringWidth(mitte, schrift, 7) / 2) - PS.MARGIN - 0.4 * cm
    erwartet = auf_breite(firma, schrift, 7, links_max)
    assert erwartet.endswith("…") and erwartet != firma
    assert stringWidth(erwartet, schrift, 7) <= links_max
    assert erwartet in text and firma not in text, text
    assert "Seite 1 von 1" in text
    # Kurzer Name bleibt ganz
    assert auf_breite("GL Abschluss", schrift, 7, links_max) == "GL Abschluss"
    # Das ganze Dokument entsteht weiter (Name im Kopf ungekuerzt, Fusszeile mit "…")
    ganz = _text(_pdf_vertrag(dealer={"company_name": firma, "address": "Weg 1",
                                      "zip_code": "10115", "city": "Berlin"}))
    assert "…" in ganz
    # Alter Name im Protokollmodul zeigt auf dieselbe Funktion
    import pickup_pdf_service as PK
    assert PK._auf_breite is auf_breite


# ============================================================ P-15 / P-16 / P-20 (PDF)
def test_p15_ausstattung_als_text_oder_mit_leeren_eintraegen():
    import pdf_service as PS
    assert PS._ausstattung_liste("Klimaautomatik, Navi ,, Sitzheizung ") == \
        ["Klimaautomatik", "Navi", "Sitzheizung"]
    assert PS._ausstattung_liste(["a", "", None, " b "]) == ["a", "b"]
    assert PS._ausstattung_liste(42) == [] and PS._ausstattung_liste(None) == []
    text = _text(_pdf_vertrag(vehicle={"make_label": "BMW", "model_label": "320d",
                                       "features": "Klimaautomatik, Navi ,, Sitzheizung"}))
    assert "Klimaautomatik" in text and "Sitzheizung" in text and "Ausstattung laut" in text
    assert "Ausstattung laut" not in _text(_pdf_vertrag(
        vehicle={"make_label": "BMW", "features": ["", " "]}))


def test_p16_kaufpreis_in_allen_schreibweisen():
    import pdf_service as PS
    assert PS._preis_zahl("12.500,00 EUR") == 12500.0
    assert PS._preis_zahl("12500,50") == 12500.5
    assert PS._preis_zahl("12,500.00") == 12500.0
    assert PS._preis_zahl("12.500") == 12500.0
    assert PS._preis_zahl("1.250.000") == 1250000.0
    assert PS._preis_zahl("12500.75") == 12500.75
    assert PS._preis_zahl(15900) == 15900.0 and PS._preis_zahl(15900.5) == 15900.5
    assert PS._preis_zahl("abc") == 0.0 and PS._preis_zahl(None) == 0.0
    assert PS._preis_zahl(float("nan")) == 0.0 and PS._preis_zahl(True) == 0.0
    text = _text(_pdf_vertrag(purchase_price="12.500,00"))
    assert "12.500,00 EUR" in text


def test_p20_monat_jahr_normiert_und_abgelehnt():
    C = _module("routes.contracts")
    basis = dict(vehicle_id="v", seller_name="V", purchase_price=100)
    assert C.ContractIn(**basis, hu_valid="Ja", hu_until="2027-03").hu_until == "03/2027"
    assert C.ContractIn(**basis, hu_valid="Ja", hu_until="3.2027").hu_until == "03/2027"
    assert C.ContractIn(**basis, hu_valid="Ja", hu_until="06/26").hu_until == "06/2026"
    assert C.ContractIn(**basis, hu_valid="Ja", hu_until="05/2027").hu_until == "05/2027"
    assert C.ContractIn(**basis, service_book="teilweise",
                        service_book_until="2024-05").service_book_until == "05/2024"
    assert C.ContractIn(**basis, hu_until="").hu_until == ""
    assert C.ContractIn(**basis, hu_until=None).hu_until == ""
    for kaputt in ("bald", "13/2027", "2027", "03/1800"):
        with pytest.raises(ValidationError):
            C.ContractIn(**basis, hu_valid="Ja", hu_until=kaputt)
    with pytest.raises(ValidationError):
        C.ContractIn(**basis, service_book="teilweise", service_book_until="irgendwann")
    # HU Nein leert das Datum weiter (RP-405)
    assert C.ContractIn(**basis, hu_valid="Nein", hu_until="2027-03").hu_until == ""
    # Altvertrag mit rohem Wert: das PDF druckt MM/JJJJ
    text = _text(_pdf_vertrag(hu_valid="Ja", hu_until="2027-03",
                              service_book="teilweise", service_book_until="2024-5"))
    assert "gültig bis 03/2027" in text and "2027-03" not in text
    assert "Teilweise, bis 05/2024" in text


# ============================================================ P-30 / P-32 / P-33 (Protokoll)
def test_p30_leere_ausstattungsliste_bricht_das_protokoll_nicht():
    from pickup_pdf_service import build_pickup_pdf
    text = _text(build_pickup_pdf(appointment={"seller_name": "Max"}, contract=_vertrag(),
                                  vehicle={"features": ["", " ", None]}, dealer={}))
    assert "Standardliste" in text and "Klimaanlage" in text
    text = _text(build_pickup_pdf(appointment={"seller_name": "Max"}, contract=_vertrag(),
                                  vehicle={"features": "Navigationssystem, Standheizung"},
                                  dealer={}))
    assert "Navigationssystem" in text and "Standardliste" not in text


def test_p32_tabellen_innerhalb_des_rahmens():
    import pickup_pdf_service as PK
    assert PK.TABELLEN_B == PK.CONTENT_W - 12
    for breiten in ((4.5, 4.2, 4.5, 4.3), (4.6, 6.4, 2.0, 2.0, 2.5)):
        anteile = PK._anteilig(*breiten)
        assert abs(sum(anteile) - PK.TABELLEN_B) < 1e-6
        assert all(b <= PK.TABELLEN_B for b in anteile)
    q = inspect.getsource(PK)
    assert "17.5 * cm" not in q, "feste Tabellenbreite ausserhalb des Rahmens"
    from pickup_pdf_service import build_pickup_pdf
    assert build_pickup_pdf(appointment={"seller_name": "Max"}, contract=_vertrag(),
                            vehicle={}, dealer={})[:5] == b"%PDF-"


def test_p33_kilometerstand_bei_abholung_mit_tausenderpunkt():
    from pickup_pdf_service import build_pickup_pdf
    text = _text(build_pickup_pdf(appointment={"seller_name": "Max"}, contract=_vertrag(),
                                  vehicle={}, dealer={},
                                  filled={"condition": {"mileage": "85120"}}))
    assert "85.120 km" in text and "85120 km" not in text
    text = _text(build_pickup_pdf(appointment={"seller_name": "Max"}, contract=_vertrag(),
                                  vehicle={}, dealer={},
                                  filled={"condition": {"mileage": "ca. 85000"}}))
    assert "ca. 85000 km" in text


# ============================================================ P-34 / P-35 / P-36 (Beweis)
def test_p34_kleine_bilder_werden_nicht_vergroessert():
    B = _module("beweis_pdf")
    from reportlab.platypus import Image as RLImage, Paragraph
    from reportlab.lib.styles import getSampleStyleSheet
    ersatz = Paragraph("kein Bild", getSampleStyleSheet()["Normal"])
    klein = B._bild(_foto(120, 80), 400, 400, ersatz)
    assert isinstance(klein, RLImage) and (klein.drawWidth, klein.drawHeight) == (120, 80)
    gross = B._bild(_foto(1200, 800), 400, 400, ersatz)
    assert abs(gross.drawWidth - 400) < 1e-6 and abs(gross.drawHeight - 400 * 800 / 1200) < 1e-6
    assert B._bild(b"kaputt", 400, 400, ersatz) is ersatz


def test_p35_ohne_abrufzeit_steht_spaetestens_die_vormerkung(welt, monkeypatch):
    B = _module("beweis_pdf")
    BS = _module("beweis_service")
    zeit = datetime(2026, 9, 10, 13, 5, 12, tzinfo=timezone.utc)
    daten = dict(make_label="VW", model_label="Golf", mobile_ad_id="1", list_price=9990.0,
                 seller_type="haendler")
    pdf = B.beweis_pdf(quelle="mobile", daten=daten, url="https://suchen.mobile.de/x?id=1",
                       item_id="1", beweis_id="abc", abgerufen_am=zeit, erstellt_am=zeit,
                       fotos=[], foto_urls=[], abgerufen_spaetestens=True)
    text = _text(pdf)
    assert "spätestens am 10.09.2026, 15:05:12 Uhr" in text and "unbekannt" not in text
    assert "Vormerkung" in text
    # ohne Kennzeichen wie bisher
    ohne = _text(B.beweis_pdf(
        quelle="mobile", daten=daten, url="https://suchen.mobile.de/x?id=1", item_id="1",
        beweis_id="abc", abgerufen_am=zeit, erstellt_am=zeit, fotos=[], foto_urls=[]))
    assert "Daten ausgelesen am" in ohne and "15:05:12 Uhr" in ohne and "spätestens" not in ohne

    # Worker: eingefrorener Stand OHNE quelle_abgerufen_am -> Vormerkung als spaetester Zeitpunkt
    import storage_service
    gespeichert = {}

    async def _speichern(key, blob):
        gespeichert[key] = blob

    async def _keine_fotos(urls):
        return []

    monkeypatch.setattr(storage_service, "save_async", _speichern)
    monkeypatch.setattr(BS, "_fotos_laden", _keine_fotos)
    db = welt.db
    bid = f"bw_a6_{welt.w.s}"
    doc = {"id": bid, "cache_key": f"mobile:a6{welt.w.s}", "quelle": "mobile", "item_id": "1",
           "url": "https://suchen.mobile.de/x?id=1", "status": "in_arbeit",
           "bearbeiter": BS._WORKER, "versuche": 1, "erstellt_am": zeit,
           "quelle_daten": daten}
    try:
        welt.run(db.inserat_beweise.insert_one(dict(doc)))
        assert welt.run(BS.beweis_erzeugen(db, doc)) is True
        zeile = welt.run(db.inserat_beweise.find_one({"id": bid}, {"_id": 0}))
        assert zeile["status"] == "fertig"
        assert zeile["daten_abgerufen_am"].replace(tzinfo=timezone.utc) == zeit
        assert zeile["daten_abgerufen_spaetestens"] is True
        assert "spätestens am 10.09.2026" in _text(gespeichert[zeile["pdf_key"]])
    finally:
        welt.run(db.inserat_beweise.delete_many({"id": bid}))


def test_p36_usea85_einmal_beim_import():
    import pdf_schrift  # noqa: F401 — setzt den Wert beim Import
    from reportlab import rl_config
    assert rl_config.useA85 == 0
    assert "rl_config.useA85 = 0" in inspect.getsource(pdf_schrift)
    B = _module("beweis_pdf")
    assert "rl_config.useA85 = " not in inspect.getsource(B.beweis_pdf)


# ============================================================ P-10 / P-18 (Mail)
def test_p10_haengender_smtp_eintrag_ist_nach_24h_wieder_frei(welt):
    ES = _module("email_service")
    db = welt.db
    alt, jung = f"pb590a-{welt.w.s}", f"pb590b-{welt.w.s}"
    vor_25h = datetime.now(timezone.utc) - timedelta(hours=25)
    try:
        welt.run(db.mail_idempotenz.insert_many([
            {"key": alt, "status": "laeuft", "begonnen": vor_25h, "empfaenger": "x"},
            {"key": jung, "status": "laeuft",
             "begonnen": datetime.now(timezone.utc) - timedelta(hours=1), "empfaenger": "x"},
        ]))
        assert welt.run(ES._smtp_idempotenz_beanspruchen(alt, "v@example.test")) == "neu"
        doc = welt.run(db.mail_idempotenz.find_one({"key": alt}, {"_id": 0}))
        assert doc["uebernahmen"] == 1 and doc["empfaenger"] == "v@example.test"
        assert doc["begonnen"].replace(tzinfo=timezone.utc) > vor_25h + timedelta(hours=24)
        # frisch uebernommen -> fuer den naechsten Aufruf wieder "unklar" (laeuft)
        assert welt.run(ES._smtp_idempotenz_beanspruchen(alt, "v@example.test")) == "unklar"
        assert welt.run(ES._smtp_idempotenz_beanspruchen(jung, "v@example.test")) == "unklar"
        assert welt.run(db.mail_idempotenz.find_one({"key": jung}, {"_id": 0})).get("uebernahmen") is None
        assert ES.SMTP_UNKLAR_SPERRE_SEKUNDEN == 24 * 3600
        assert not hasattr(ES, "SMTP_VERSUCH_MAX_SEKUNDEN"), "toter Zweig entfernt"
    finally:
        welt.run(db.mail_idempotenz.delete_many({"key": {"$in": [alt, jung]}}))


def test_p18_zu_grosser_anhang_wird_nicht_gesendet(monkeypatch):
    ES = _module("email_service")
    C = _module("routes.contracts")
    if not os.environ.get("MAIL_ANHANG_MAX_BYTES"):
        assert ES.MAIL_ANHANG_MAX_BYTES == 20 * 1024 * 1024
    gesendet = []
    monkeypatch.setattr(ES, "email_configured", lambda: True)
    monkeypatch.setattr(ES, "resend_aktiv", lambda: False)
    monkeypatch.setattr(ES, "smtp_aktiv", lambda: True)
    monkeypatch.setattr(ES, "_send_sync", lambda **k: gesendet.append(len(k["anhang"] or b"")))
    monkeypatch.setattr(ES, "MAIL_ANHANG_MAX_BYTES", 100)
    ok, beleg = asyncio.run(ES.send_email_mit_beleg("v@example.test", "B", "T",
                                                    anhang=b"x" * 101, anhang_name="a.pdf"))
    assert (ok, beleg) == (False, "anhang_zu_gross") and gesendet == []
    assert asyncio.run(ES.send_email("v@example.test", "B", "T", anhang=b"x" * 101)) is False
    ok, beleg = asyncio.run(ES.send_email_mit_beleg("v@example.test", "B", "T",
                                                    anhang=b"x" * 100, anhang_name="a.pdf"))
    assert (ok, beleg) == (True, "smtp") and gesendet == [100]
    # Versandweg: klare Meldung mit Link-Weg statt "spaeter erneut" (413)
    hinweis = C._anhang_zu_gross_hinweis()
    assert "WhatsApp" in hinweis and "herunterladen" in hinweis and "NICHT versendet" in hinweis
    q = inspect.getsource(C.send_contract)
    assert 'beleg == "anhang_zu_gross"' in q and "HTTPException(413, _anhang_zu_gross_hinweis())" in q
    assert q.index('beleg == "anhang_zu_gross"') < q.index('raise HTTPException(502, "E-Mail-Versand fehlgeschlagen')


# ============================================================ P-11 / R2-10 / R1-33 (Quelltext)
def test_p11_r2_10_r1_33_quelltext():
    C = _module("routes.contracts")
    CS = _module("cleanup_service")
    q = inspect.getsource(C.send_contract)
    assert '_safe_filename(c.get("filename") or "", fallback="Kaufvertrag.pdf")' in q
    assert 'c.get("filename") or "Kaufvertrag.pdf"' not in q
    name = C._safe_filename("Vertrag\r\nX-Evil: 1.pdf", fallback="Kaufvertrag.pdf")
    assert "\r" not in name and "\n" not in name and name
    assert C._safe_filename("", fallback="Kaufvertrag.pdf") == "Kaufvertrag.pdf"
    # R2-10: Statuswechsel nur ohne gesperrten Lebenszyklus
    q = inspect.getsource(C.create_contract)
    stelle = q.index('{"$set": {"status": "Vertrag erstellt"}}')
    assert q.rfind('"lifecycle": {"$nin": list(VERTRAG_GESPERRT)}', 0, stelle) > stelle - 400
    # R1-33: die Vertragsfrist heisst nicht mehr "90 Tage"
    quelle = Path(CS.__file__).read_text(encoding="utf-8")
    assert "versprochene 90-Tage-Frist" not in quelle and "90-Tage-Vertragsloeschung" not in quelle
    assert "VERTRAG_AUFBEWAHRUNG_TAGE, 60 Tage" in quelle


# ============================================================ P-13
def test_p13_digitale_fassung_nimmt_die_kaeufer_basis_des_erstellers(welt):
    C = _module("routes.contracts")
    w, db = welt.w, welt.db
    cid, vid = f"c_a6_{w.s}", f"v_a6_{w.s}"
    welt.run(db.users.update_one({"id": w.sucher["id"]},
                                 {"$set": {"settings_override": {"company_name": f"Filiale Süd {w.s}"}}}))
    welt.run(db.vehicles.insert_one(w.fahrzeug(vid, owner=w.sucher["id"])))
    doc = w.vertrag(cid, user_id=w.sucher["id"], vehicle_id=vid)
    # Altvertrag: keine eingefrorenen Kaeuferdaten, keine digitale Fassung
    doc["contract_data"].update({"purchase_price": 12500, "contract_no": "KV-A6-13",
                                 "pickup_date": "2099-09-10"})
    welt.run(db.generated_pdfs.insert_one(doc))
    c = welt.run(db.generated_pdfs.find_one({"id": cid}, {"_id": 0}))
    # Abrufender ist der CHEF — die Identitaet des Erstellers (Sucher) zaehlt
    pdf = welt.run(C._digitales_pdf_bytes(c, w.chef))
    assert pdf and pdf[:5] == b"%PDF-"
    text = _text(pdf)
    assert f"Filiale Süd {w.s}" in text and "R17 GmbH" not in text
    # gecacht wurde dieselbe Fassung; contract_data blieb unangetastet
    nachher = welt.run(db.generated_pdfs.find_one({"id": cid}, {"_id": 0}))
    assert nachher.get("pdf_digital_b64") and "dealer_company" not in nachher["contract_data"]
    assert "kaeufer_basis" in inspect.getsource(C._digitales_pdf_bytes)
    assert 'db.dealers.find_one({"id": c.get("dealer_id")}' not in inspect.getsource(C._digitales_pdf_bytes)


# ============================================================ P-16 (Alarm)
def test_p16_gescheiterte_neuerzeugung_loest_betriebsalarm_aus(welt, monkeypatch):
    C = _module("routes.contracts")
    w, db = welt.w, welt.db
    cid = f"c_a6p16_{w.s}"
    doc = w.vertrag(cid)
    doc["contract_data"].update({"purchase_price": 12500, "contract_no": "KV-A6-16"})
    welt.run(db.generated_pdfs.insert_one(doc))

    def _kaputt(**k):
        raise RuntimeError("ReportLab weg")
    monkeypatch.setattr(C, "_pdfs_erzeugen", _kaputt)
    erg = {}
    assert welt.run(C.regenerate_contract_for_pickup(
        contract_id=cid, dealer_id=w.dealer_id, user=w.chef, pickup_date="2099-10-01",
        ergebnis=erg)) is False
    assert erg["grund"] == "pdf_fehler"
    alarm = welt.run(db.betriebsalarme.find_one(
        {"typ": "vertrag_neuerzeugung_fehlgeschlagen", "ref": cid, "offen": True}, {"_id": 0}))
    assert alarm and alarm["details"]["grund"] == "abholtermin_geaendert"
    # der Vertrag blieb unveraendert (Fassung 1)
    assert welt.run(db.generated_pdfs.find_one({"id": cid}, {"_id": 0, "version": 1}))["version"] == 1


# ============================================================ R1-07
def test_r1_07_grabstein_vor_der_pruefung_und_bei_ablehnung_zurueck(welt):
    C = _module("routes.contracts")
    CS = _module("cleanup_service")
    w, db = welt.w, welt.db
    cid, aid, pid = f"c_a6r107_{w.s}", f"a_a6r107_{w.s}", f"p_a6r107_{w.s}"
    welt.run(db.generated_pdfs.insert_one(w.vertrag(cid)))
    welt.run(db.appointments.insert_one(w.appt(aid, contract_id=cid)))
    welt.run(db.pickup_protocols.insert_one(
        {"id": pid, "appointment_id": aid, "dealer_id": w.dealer_id, "contract_id": cid,
         "version": 1, "status": "entwurf", "superseded": False}))

    # 1) Kaskade direkt: die Pruefung sieht den Grabstein, danach ist er weg
    gesehen = {}

    async def _lehnt_ab():
        doc = await db.generated_pdfs.find_one({"id": cid}, {"_id": 0, "loeschung": 1})
        gesehen["grabstein"] = (doc.get("loeschung") or {}).get("status")
        return "nein, laeuft noch"

    with pytest.raises(CS.LoeschungAbgelehnt) as e:
        welt.run(CS.vertrag_endgueltig_loeschen(db, cid, scrub_pii=True, grund="manuell",
                                                audit=False, vor_kaskade=_lehnt_ab))
    assert str(e.value) == "nein, laeuft noch" and gesehen["grabstein"] == "laeuft"
    doc = welt.run(db.generated_pdfs.find_one({"id": cid}, {"_id": 0}))
    assert doc and "loeschung" not in doc, "eigener Grabstein zurueckgenommen"

    # 2) Route: 409 wegen Entwurf, Vertrag und Termin unberuehrt (kein Storno)
    with pytest.raises(HTTPException) as e:
        welt.run(C.delete_contract(cid, w.chef))
    assert e.value.status_code == 409 and "Abholprotokoll" in e.value.detail
    doc = welt.run(db.generated_pdfs.find_one({"id": cid}, {"_id": 0}))
    assert doc and "loeschung" not in doc
    assert welt.run(db.appointments.find_one({"id": aid}, {"_id": 0}))["status"] == "offen"
    # Reihenfolge im Quelltext: Grabstein (CAS) vor der Pruefung
    q = inspect.getsource(CS.vertrag_endgueltig_loeschen)
    assert q.index('"loeschung.status": {"$ne": "laeuft"}') < q.index("await vor_kaskade()")
    assert "_vor_kaskade" in inspect.getsource(C.delete_contract)

    # 3) Fremder Grabstein (Wiederaufnahme): die Pruefung laeuft nicht, nichts wird zurueckgenommen
    aufrufe = []

    async def _zaehlt():
        aufrufe.append(1)
        return "nein"

    welt.run(db.generated_pdfs.update_one({"id": cid}, {"$set": {"loeschung": {
        "status": "laeuft", "gestartet": "2000-01-01T00:00:00+00:00",
        "grund": "manuell", "scrub_pii": True}}}))
    assert welt.run(CS.vertrag_endgueltig_loeschen(db, cid, scrub_pii=True, grund="manuell",
                                                   audit=False, vor_kaskade=_zaehlt)) is True
    assert aufrufe == [] and welt.run(db.generated_pdfs.count_documents({"id": cid})) == 0

    # 4) Ohne laufendes Protokoll loescht die Route und storniert den Termin
    cid2, aid2 = f"c_a6r107b_{w.s}", f"a_a6r107b_{w.s}"
    welt.run(db.generated_pdfs.insert_one(w.vertrag(cid2)))
    welt.run(db.appointments.insert_one(w.appt(aid2, contract_id=cid2)))
    assert welt.run(C.delete_contract(cid2, w.chef)) == {"ok": True}
    assert welt.run(db.generated_pdfs.count_documents({"id": cid2})) == 0
    t = welt.run(db.appointments.find_one({"id": aid2}, {"_id": 0}))
    assert t["status"] == "storniert" and t["storno_grund"] == "vertrag_geloescht"
