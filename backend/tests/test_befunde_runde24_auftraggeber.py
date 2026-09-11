# -*- coding: utf-8 -*-
"""Runde 24 (11.09.2026, Befund Ahmad): Auftraggeber im Abholprotokoll.

  * Im Abholprotokoll stand unter "AUFTRAGGEBER" nur "—" und im Kopf
    "Autohändler" -> Firma bzw. Kaeufer aus dem Vertrag / Sucher-Einstellungen
  * auftraggeber.auftraggeber_fuer_termin: Kaeuferfelder des Vertrags >
    Sucher-Overrides des Erstellers > Firmendaten; alle drei PDF-Wege
    (Terminkalender, Fahrer-App, Protokoll-Abschluss) nutzen sie
  * POST /contracts: ohne Kaeufername (Formular UND Einstellungen leer) -> 422;
    die Vorschau bleibt ohne Pflicht (Entwurf)

In-Prozess gegen eine Wegwerf-DB (autoschnell_r24_auftraggeber_<uuid>), die
am Ende geloescht wird. Kein Server-Import, keine HTTP-Aufrufe.
"""
import asyncio
import base64
import inspect
import io
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"

FIRMA = {"company_name": "Firma R24 GmbH", "contact_person": "Chef Firma",
         "address": "Firmenstr. 1", "zip_code": "30159", "city": "Hannover",
         "phone": "0511 111", "email": "firma@r24.test"}
SUCHER_OVERRIDE = {"company_name": "Sucher Filiale Süd", "contact_person": "Sam Sucher",
                   "address": "Suchergasse 7", "zip_code": "04109", "city": "Leipzig",
                   "phone": "0341 777"}          # ohne E-Mail -> Firmen-E-Mail bleibt
KAEUFER_VERTRAG = {"dealer_company": "Käufer aus Vertrag GmbH",
                   "dealer_contact": "Erika Käufer", "dealer_address": "Vertragsweg 5",
                   "dealer_zip": "10115", "dealer_city": "Berlin",
                   "dealer_phone": "030 555", "dealer_email": "kaeufer@r24.test"}
# Minimal-PNG (nur der Magic-Header wird geprueft) fuer die Unterschriften.
_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
_PNG_B64 = base64.b64encode(_PNG).decode()


def _jetzt():
    return datetime.now(timezone.utc).isoformat()


def _module(name):
    import importlib
    return importlib.import_module(name)


def _text(pdf: bytes, seite=None) -> str:
    from pypdf import PdfReader
    pages = PdfReader(io.BytesIO(pdf)).pages
    if seite is not None:
        pages = [pages[seite]]
    return "\n".join((p.extract_text() or "") for p in pages)


def _pickup(dealer):
    import pickup_pdf_service as P
    return P.build_pickup_pdf(
        appointment={"id": "a-r24", "pickup_date": "2099-01-01", "pickup_time": "10:00",
                     "seller_name": "Verkäufer V", "pickup_address": "Abholweg 9"},
        vehicle={"make_label": "BMW", "model_label": "320d"},
        contract={}, dealer=dealer, driver={"display_name": "Fahrer"})


def _erwarte_auftraggeber(pdf: bytes, firma: str, *zeilen: str):
    seite1 = _text(pdf, 0)
    # Kopf + Auftraggeber-Karte + Fusszeile tragen dieselbe Firma
    assert seite1.count(firma) >= 3, seite1
    for z in zeilen:
        assert z in seite1, (z, seite1)
    alles = _text(pdf)
    assert "Autohändler" not in alles, "kein Platzhalter mehr im Protokoll"


# ------------------------------------------------------------ PDF direkt
def test_01_auftraggeber_karte_und_kopf_zeigen_die_firma():
    d = {"company_name": "Käufer aus Vertrag GmbH", "contact_person": "Erika Käufer",
         "address": "Vertragsweg 5", "zip_code": "10115", "city": "Berlin",
         "phone": "030 555", "email": "kaeufer@r24.test"}
    pdf = _pickup(d)
    assert pdf[:4] == b"%PDF"
    _erwarte_auftraggeber(pdf, "Käufer aus Vertrag GmbH",
                          "AUFTRAGGEBER", "Ansprechpartner: Erika Käufer",
                          "Vertragsweg 5", "10115 Berlin", "Tel.: 030 555",
                          "E-Mail: kaeufer@r24.test")


def test_02_ohne_daten_bleibt_strich_und_nichts_bricht():
    for d in ({}, None, {"company_name": None, "name": None, "contact_person": None,
                         "address": None, "zip_code": None, "city": None,
                         "phone": None, "email": None}):
        pdf = _pickup(d)
        seite1 = _text(pdf, 0)
        assert pdf[:4] == b"%PDF" and "AUFTRAGGEBER" in seite1
        assert "—" in seite1
        assert "Autohändler" not in _text(pdf)


def test_03_xml_sonderzeichen_und_zahlen_brechen_nichts():
    firma = 'Müller & Söhne <Kfz> "Nord"'
    d = {"company_name": firma, "contact_person": "A & B <Team>",
         "address": "Weg <1> & 2", "zip_code": 30159, "city": "Hannover & Umland",
         "phone": 5111234, "email": "a&b@r24.test"}
    pdf = _pickup(d)
    _erwarte_auftraggeber(pdf, "Müller & Söhne <Kfz>", "Ansprechpartner: A & B <Team>",
                          "Weg <1> & 2", "30159 Hannover & Umland", "Tel.: 5111234")


def test_04_ansprechpartner_gleich_firma_wird_nicht_doppelt_gedruckt():
    pdf = _pickup({"company_name": "Max Privat", "contact_person": "Max Privat"})
    assert "Ansprechpartner" not in _text(pdf, 0)


# ------------------------------------------------------------ Wegwerf-DB
@pytest.fixture
def welt():
    from motor.motor_asyncio import AsyncIOMotorClient
    s = uuid.uuid4().hex[:10]
    # Nachpruefung Runde 24 (Gegenpruefer): auch routes.protocols (signierter
    # Abschluss) sowie lifecycle/kaufvorgang, die finalize_protocol intern
    # nutzt — sonst haengen sie an der echten DB bzw. am Loop eines anderen
    # Tests. Nur das Modul-Attribut db wird getauscht und danach zurueckgesetzt.
    names = ["deps", "routes.contracts", "routes.appointments", "routes.drivers",
             "routes.protocols", "lifecycle", "kaufvorgang"]
    mods = [_module(n) for n in names]
    alt = [(m, getattr(m, "db", None)) for m in mods]

    class _W:
        pass

    w = _W()
    w.s = s
    w.dealer_id = f"d_r24a_{s}"
    w.fremd_id = f"dfremd_r24a_{s}"
    w.leer_id = f"dleer_r24a_{s}"           # Firma ohne company_name
    w.chef = {"id": f"chef_r24a_{s}", "dealer_id": w.dealer_id, "role": "dealer"}
    w.a = {"id": f"sa_r24a_{s}", "dealer_id": w.dealer_id, "role": "sucher",
           "settings_override": dict(SUCHER_OVERRIDE)}
    w.b = {"id": f"sb_r24a_{s}", "dealer_id": w.dealer_id, "role": "sucher"}
    w.x = {"id": f"sx_r24a_{s}", "dealer_id": w.fremd_id, "role": "sucher",
           "settings_override": {"company_name": "Fremdfirma"}}
    w.chef_leer = {"id": f"chefl_r24a_{s}", "dealer_id": w.leer_id, "role": "dealer"}
    w.sucher_leer = {"id": f"sl_r24a_{s}", "dealer_id": w.leer_id, "role": "sucher",
                     "settings_override": {"company_name": "Nur Einstellungen"}}
    w.driver = {"id": f"f_r24a_{s}", "display_name": "Fahrer R24", "email": "f@r24.test"}
    w.loop = asyncio.new_event_loop()
    asyncio.set_event_loop(w.loop)
    w.client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    w.db_name = f"autoschnell_r24_auftraggeber_{s}"
    w.db = w.client[w.db_name]
    for m in mods:
        if hasattr(m, "db"):
            m.db = w.db
    w.run = lambda coro: w.loop.run_until_complete(coro)

    def konto(u):
        return {**u, "active": True, "email": f"{u['id']}@e2etest-mail.de",
                "created_at": _jetzt()}

    w.run(w.db.users.insert_many([konto(u) for u in
                                  (w.chef, w.a, w.b, w.x, w.chef_leer, w.sucher_leer)]))
    w.run(w.db.dealers.insert_many([
        {"id": w.dealer_id, "user_id": w.chef["id"], **FIRMA, "created_at": _jetzt()},
        {"id": w.fremd_id, "user_id": f"cheffremd_{s}", "company_name": "Fremdfirma",
         "created_at": _jetzt()},
        {"id": w.leer_id, "user_id": w.chef_leer["id"], "company_name": "",
         "created_at": _jetzt()}]))
    yield w
    try:
        w.run(w.client.drop_database(w.db_name))
    finally:
        for m, d in alt:
            if d is not None:
                m.db = d
        w.client.close()
        w.loop.close()


def _vertrag(w, cid, user, dealer_id=None, **kaeufer):
    return {"id": cid, "dealer_id": dealer_id or w.dealer_id, "user_id": user["id"],
            "contract_no": f"KV-{cid}", "seller_name": "Verkäufer V",
            "contract_data": {"seller_name": "Verkäufer V", "purchase_price": 1000,
                              **kaeufer}}


def _termin(w, aid, **extra):
    doc = {"id": aid, "dealer_id": w.dealer_id, "status": "offen",
           "pickup_date": "2099-01-01", "pickup_time": "10:00",
           "seller_name": "Verkäufer V", "pickup_address": "Abholweg 9",
           "created_at": _jetzt()}
    doc.update(extra)
    return {k: v for k, v in doc.items() if v is not None}


def test_05_vertrag_mit_kaeuferfeldern_gewinnt(welt):
    from auftraggeber import auftraggeber_fuer_termin
    w = welt
    cid, aid = f"c1_{w.s}", f"t1_{w.s}"
    w.run(w.db.generated_pdfs.insert_one(_vertrag(w, cid, w.a, **KAEUFER_VERTRAG)))
    appt = _termin(w, aid, contract_id=cid, created_by=w.b["id"])
    d = w.run(auftraggeber_fuer_termin(appt))
    assert d["company_name"] == "Käufer aus Vertrag GmbH"
    assert d["contact_person"] == "Erika Käufer"
    assert (d["address"], d["zip_code"], d["city"]) == ("Vertragsweg 5", "10115", "Berlin")
    assert d["phone"] == "030 555" and d["email"] == "kaeufer@r24.test"
    _erwarte_auftraggeber(_pickup(d), "Käufer aus Vertrag GmbH",
                          "Ansprechpartner: Erika Käufer", "Vertragsweg 5", "10115 Berlin")


def test_06_vertrag_ohne_kaeuferfelder_nimmt_sucher_einstellungen(welt):
    from auftraggeber import auftraggeber_fuer_termin
    w = welt
    cid = f"c2_{w.s}"
    # Leere/Leerzeichen-Felder im Vertrag ueberschreiben nichts (wie im Kaufvertrag)
    w.run(w.db.generated_pdfs.insert_one(_vertrag(w, cid, w.a, dealer_company="  ",
                                                  dealer_contact=None)))
    d = w.run(auftraggeber_fuer_termin(_termin(w, f"t2_{w.s}", contract_id=cid,
                                               created_by=w.chef["id"])))
    assert d["company_name"] == "Sucher Filiale Süd"
    assert d["contact_person"] == "Sam Sucher"
    assert (d["address"], d["zip_code"], d["city"]) == ("Suchergasse 7", "04109", "Leipzig")
    assert d["email"] == FIRMA["email"], "Felder ohne Override bleiben die der Firma"
    _erwarte_auftraggeber(_pickup(d), "Sucher Filiale Süd",
                          "Ansprechpartner: Sam Sucher", "Suchergasse 7", "04109 Leipzig")


def test_07_termin_ohne_vertrag_nimmt_den_termin_ersteller(welt):
    from auftraggeber import auftraggeber_fuer_termin
    w = welt
    d = w.run(auftraggeber_fuer_termin(_termin(w, f"t3_{w.s}", created_by=w.a["id"])))
    assert d["company_name"] == "Sucher Filiale Süd"
    # Vertrag unbekannt (geloescht) -> ebenfalls der Termin-Ersteller
    d2 = w.run(auftraggeber_fuer_termin(_termin(w, f"t3b_{w.s}", contract_id="weg",
                                                created_by=w.a["id"])))
    assert d2["company_name"] == "Sucher Filiale Süd"


def test_08_ohne_vertrag_und_overrides_bleiben_firmendaten(welt):
    from auftraggeber import auftraggeber_fuer_termin
    w = welt
    for ersteller in (w.b["id"], w.chef["id"], None, "gibt-es-nicht"):
        d = w.run(auftraggeber_fuer_termin(_termin(w, f"t4_{w.s}", created_by=ersteller)))
        for k, v in FIRMA.items():
            assert d.get(k) == v, (ersteller, k, d.get(k))
    _erwarte_auftraggeber(_pickup(d), FIRMA["company_name"],
                          "Ansprechpartner: Chef Firma", "Firmenstr. 1", "30159 Hannover")


def test_09_fremde_konten_und_vertraege_zaehlen_nicht(welt):
    from auftraggeber import auftraggeber_fuer_termin
    w = welt
    fremd_cid = f"cf_{w.s}"
    w.run(w.db.generated_pdfs.insert_one(
        _vertrag(w, fremd_cid, w.x, dealer_id=w.fremd_id,
                 dealer_company="Fremder Käufer")))
    d = w.run(auftraggeber_fuer_termin(_termin(w, f"t5_{w.s}", contract_id=fremd_cid,
                                               created_by=w.x["id"])))
    assert d["company_name"] == FIRMA["company_name"]
    assert w.run(auftraggeber_fuer_termin({"id": "ohne-firma"})) == {}
    assert w.run(auftraggeber_fuer_termin(None)) == {}


def test_10_terminkalender_pdf_zeigt_kaeufer_aus_dem_vertrag(welt):
    A = _module("routes.appointments")
    w = welt
    cid, aid = f"c6_{w.s}", f"t6_{w.s}"
    w.run(w.db.generated_pdfs.insert_one(_vertrag(w, cid, w.a, **KAEUFER_VERTRAG)))
    w.run(w.db.appointments.insert_one(_termin(w, aid, contract_id=cid,
                                               created_by=w.a["id"])))
    resp = w.run(A.get_pickup_order_pdf(aid, 0, w.chef))
    assert resp.body[:4] == b"%PDF"
    _erwarte_auftraggeber(resp.body, "Käufer aus Vertrag GmbH",
                          "Ansprechpartner: Erika Käufer", "Vertragsweg 5", "10115 Berlin")


def test_11_fahrer_pdf_zeigt_sucher_einstellungen(welt):
    D = _module("routes.drivers")
    w = welt
    aid = f"t7_{w.s}"
    w.run(w.db.dealer_drivers.insert_one({"id": str(uuid.uuid4()), "dealer_id": w.dealer_id,
                                          "driver_account_id": w.driver["id"],
                                          "display_name": w.driver["display_name"],
                                          "added_at": _jetzt()}))
    w.run(w.db.appointments.insert_one(_termin(w, aid, created_by=w.a["id"],
                                               driver_id=w.driver["id"])))
    resp = w.run(D.driver_pickup_order_pdf(aid, 0, w.driver))
    _erwarte_auftraggeber(resp.body, "Sucher Filiale Süd",
                          "Ansprechpartner: Sam Sucher", "Suchergasse 7")


def test_12_alle_drei_pdf_wege_nutzen_die_hilfsfunktion():
    A = _module("routes.appointments")
    D = _module("routes.drivers")
    P = _module("routes.protocols")
    for fn in (A.get_pickup_order_pdf, D.driver_pickup_order_pdf, P.finalize_protocol):
        quelle = inspect.getsource(fn)
        assert "auftraggeber_fuer_termin(appt)" in quelle, fn.__name__
        assert "db.dealers.find_one" not in quelle, fn.__name__


# ------------------------------------------------------------ Pflichtpruefung
def test_13_kaeufer_pflicht_hilfsfunktion():
    C = _module("routes.contracts")
    for d in (None, {}, {"company_name": None}, {"company_name": ""},
              {"company_name": "   "}, {"name": "Nur name"}):
        with pytest.raises(HTTPException) as e:
            C.kaeufer_pflicht_pruefen(d)
        assert e.value.status_code == 422
        assert e.value.detail == ("Käuferdaten fehlen: bitte Firma/Name im Kaufvertrag "
                                  "oder in den Einstellungen eintragen.")
    C.kaeufer_pflicht_pruefen({"company_name": "Käufer GmbH"})


def test_14_anlegen_ohne_kaeufername_422_vorschau_bleibt_erlaubt(welt, monkeypatch):
    C = _module("routes.contracts")
    w = welt
    vid = f"v_r24a_{w.s}"
    w.run(w.db.vehicles.insert_one({"id": vid, "dealer_id": w.leer_id,
                                    "lifecycle": "verglichen", "status": "verglichen",
                                    "data": {"make_label": "BMW", "model_label": "320d"},
                                    "created_at": _jetzt()}))

    def body(**kw):
        return C.ContractIn(vehicle_id=vid, seller_name="Verkäufer V",
                            purchase_price=1000, **kw)

    pdf_versuche = []

    def pdf_boom(**kw):
        pdf_versuche.append(kw["dealer"].get("company_name"))
        raise RuntimeError("Test: hier ist die Pflichtpruefung schon bestanden")
    monkeypatch.setattr(C, "_pdfs_erzeugen", pdf_boom)

    async def erwarte(status, coro):
        with pytest.raises(HTTPException) as e:
            await coro
        assert e.value.status_code == status, (e.value.status_code, e.value.detail)
        return e.value

    # Firma ohne company_name, Formular ohne Namen -> 422 (vor PDF und Speichern)
    e = w.run(erwarte(422, C.create_contract(body(), w.chef_leer)))
    assert e.detail == C.KAEUFER_FEHLT and "Käuferdaten fehlen" in e.detail
    w.run(erwarte(422, C.create_contract(body(dealer_company="   "), w.chef_leer)))
    assert pdf_versuche == [], "ohne Kaeufername darf kein PDF entstehen"
    # Name im Formular -> Pruefung bestanden (PDF-Schritt erreicht)
    w.run(erwarte(400, C.create_contract(body(dealer_company="Käufer GmbH"), w.chef_leer)))
    # Name nur in den Sucher-Einstellungen -> ebenfalls bestanden
    w.run(erwarte(400, C.create_contract(body(), w.sucher_leer)))
    assert pdf_versuche == ["Käufer GmbH", "Nur Einstellungen"]
    assert w.run(w.db.generated_pdfs.count_documents({"dealer_id": w.leer_id})) == 0
    # Vorschau (Entwurf) bleibt ohne Pflicht
    vorschau = w.run(C.preview_contract(body(), w.chef_leer))
    assert vorschau.body[:4] == b"%PDF"


# ------------------------------------------------------------ signierter Abschluss
def test_15_protokoll_abschluss_archiviert_den_auftraggeber(welt, monkeypatch):
    """Nachpruefung Runde 24 (Gegenpruefer): finalize_protocol funktional statt
    nur per Quelltext-Suche (test_12). Das GESPEICHERTE, unterschriebene
    Protokoll ist das rechtlich relevante Dokument — geprueft werden das an
    build_pickup_pdf uebergebene dealer-Argument UND der Text des archivierten
    PDFs, so wie Fahrer es ueber driver_protocol_pdf laden."""
    P = _module("routes.protocols")
    import pickup_pdf_service
    import storage_service
    w = welt
    echt = pickup_pdf_service.build_pickup_pdf
    uebergeben = []

    def pdf_merken(**kw):
        uebergeben.append(dict(kw.get("dealer") or {}))
        return echt(**kw)
    monkeypatch.setattr(pickup_pdf_service, "build_pickup_pdf", pdf_merken)

    cid = f"c8_{w.s}"
    faelle = [
        # Vertrag mit Kaeuferfeldern, Termin vom Chef angelegt -> der Vertrag gewinnt
        (f"t8_{w.s}", f"p8_{w.s}", {"contract_id": cid, "created_by": w.chef["id"]},
         "Käufer aus Vertrag GmbH",
         ("Ansprechpartner: Erika Käufer", "Vertragsweg 5", "10115 Berlin")),
        # Termin ohne Vertrag, vom Sucher angelegt -> seine Einstellungen
        # (deckt ab, dass der Abschluss created_by am Termin mitliest)
        (f"t9_{w.s}", f"p9_{w.s}", {"created_by": w.a["id"]},
         "Sucher Filiale Süd",
         ("Ansprechpartner: Sam Sucher", "Suchergasse 7", "04109 Leipzig")),
    ]
    w.run(w.db.dealer_drivers.insert_one({"id": str(uuid.uuid4()), "dealer_id": w.dealer_id,
                                          "driver_account_id": w.driver["id"],
                                          "display_name": w.driver["display_name"],
                                          "added_at": _jetzt()}))
    w.run(w.db.generated_pdfs.insert_one(_vertrag(w, cid, w.a, **KAEUFER_VERTRAG)))
    for aid, pid, extra, _f, _z in faelle:
        w.run(w.db.appointments.insert_one(_termin(w, aid, driver_id=w.driver["id"], **extra)))
        w.run(w.db.pickup_protocols.insert_one({
            "id": pid, "appointment_id": aid, "dealer_id": w.dealer_id,
            "driver_account_id": w.driver["id"], "driver_name": w.driver["display_name"],
            "version": 1, "status": "entwurf", "superseded": False,
            "vehicle_check": {k: {"status": "stimmt"} for k, _l, _o in P.VEHICLE_CHECK_FIELDS},
            "condition": {"mileage": "123456"}, "keys_count": "2",
            "damages_confirmed": True, "place": "Hannover", "created_at": _jetzt()}))
    fin = P.FinalizeIn(signature_driver_b64=_PNG_B64, signature_seller_b64=_PNG_B64)
    try:
        for aid, pid, _e, firma, zeilen in faelle:
            r = w.run(P.finalize_protocol(aid, fin, w.driver))
            assert r["ok"] is True and r["protocol_id"] == pid, r
            assert uebergeben and uebergeben[-1].get("company_name") == firma, uebergeben[-1:]
            proto = w.run(w.db.pickup_protocols.find_one({"id": pid}, {"_id": 0}))
            assert proto["status"] == "final" and proto["pdf_path"]
            archiv = w.run(P.driver_protocol_pdf(aid, w.driver))
            assert archiv.body[:4] == b"%PDF"
            _erwarte_auftraggeber(archiv.body, firma, *zeilen)
        assert uebergeben[0]["contact_person"] == "Erika Käufer"
        assert uebergeben[1]["contact_person"] == "Sam Sucher"
    finally:
        storage_service.storage.delete_prefix(f"protocol/{w.dealer_id}/")
