# -*- coding: utf-8 -*-
"""Wunsch Ahmad 24.11.2026 (Paket C2).

1. STORNIERT-Zeichen: steht der Kaufvorgang des Vertrags oder das Fahrzeug auf
   "storniert", traegt jedes ausgelieferte Vertrags-PDF schraeg, rot und
   halbtransparent "STORNIERT" auf jeder Seite. Gespeicherte PDFs bleiben
   unveraendert; nach Reaktivierung ist das Zeichen weg.
2. Zulassung bei Uebergabe: abgemeldet/angemeldet -> Satz unter "Zusätzliche
   Vereinbarung"; unbekannt (leer/fehlt) -> kein Satz.

Ohne Server: reine Funktionen und In-Prozess-Aufrufe gegen eine Wegwerf-DB
(DB_NAME, Standard autoschnell_wt_c2_<zufall>).
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

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"

FIRMA = {"company_name": "Autohaus Storno GmbH", "address": "Hauptstr. 1", "zip_code": "30159",
         "city": "Hannover", "phone": "0511 1", "email": "info@storno.test"}
FAHRZEUG = {"make_label": "VW", "model_label": "Golf", "mileage": 120000}
VERTRAG = {"seller_name": "Max Muster", "seller_phone": "0170 1234567", "purchase_price": 4500,
           "contract_no": "KV-STORNO-1", "payment_method": "Bar"}


def _jetzt() -> str:
    return datetime.now(timezone.utc).isoformat()


def _seiten_texte(pdf: bytes) -> list:
    from pypdf import PdfReader
    return [" ".join((p.extract_text() or "").split()) for p in PdfReader(io.BytesIO(pdf)).pages]


def _text(pdf: bytes) -> str:
    return " ".join(_seiten_texte(pdf))


def _vertrag_pdf(**zusatz) -> bytes:
    from pdf_service import generate_contract_pdf
    return generate_contract_pdf(dealer=dict(FIRMA), vehicle=dict(FAHRZEUG),
                                 contract={**VERTRAG, **zusatz})


# =================================================================== Zeichen ohne DB
def test_01_zeichen_auf_jeder_seite_eingabe_unveraendert():
    import vertrag_storno as S
    # lange Vereinbarung -> sicher mehrere Seiten
    original = _vertrag_pdf(sonstige_vereinbarung="Winterräder auf Felgen, zwei Schlüssel. " * 60)
    kopie = bytes(original)
    gestempelt = S.zeichen_auflegen(original)
    assert original == kopie, "die Eingabe darf nicht veraendert werden"
    vorher, nachher = _seiten_texte(original), _seiten_texte(gestempelt)
    assert len(vorher) >= 2 and len(nachher) == len(vorher)
    assert all("STORNIERT" not in t for t in vorher)
    assert all("STORNIERT" in t for t in nachher), "Zeichen fehlt auf einer Seite"
    # Vertragsinhalt bleibt lesbar
    assert "Max Muster" in " ".join(nachher)


def test_02_zeichen_ist_rot_schraeg_halbtransparent():
    import vertrag_storno as S
    assert S.ZEICHEN_FARBE[0] > 0.7 and S.ZEICHEN_FARBE[1] < 0.2 and S.ZEICHEN_FARBE[2] < 0.2
    assert 0.1 <= S.ZEICHEN_DECKKRAFT <= 0.5
    assert 20 <= S.ZEICHEN_WINKEL <= 70
    roh = S._auflage(595.0, 842.0)
    assert b"/ca 0.28" in roh or b"/CA 0.28" in roh or b"ExtGState" in roh


# =================================================================== In-Prozess mit Wegwerf-DB
@pytest.fixture
def welt(monkeypatch):
    from motor.motor_asyncio import AsyncIOMotorClient
    import importlib
    s = uuid.uuid4().hex[:8]
    mods = [importlib.import_module(n) for n in
            ("deps", "routes.contracts", "routes.drivers", "routes.admin", "routes.kundenportal")]

    class _W:
        pass

    w = _W()
    w.loop = asyncio.new_event_loop()
    asyncio.set_event_loop(w.loop)
    w.client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    w.db_name = f"{os.environ.get('DB_NAME') or 'autoschnell_wt_c2'}_{s}"
    w.db = w.client[w.db_name]
    for m in mods:
        if hasattr(m, "db"):
            monkeypatch.setattr(m, "db", w.db)
    w.run = lambda coro: w.loop.run_until_complete(coro)
    w.dealer_id = f"d_storno_{s}"
    w.chef = {"id": f"chef_storno_{s}", "dealer_id": w.dealer_id, "role": "dealer", "active": True}
    w.vid, w.cid, w.kvid = f"v_storno_{s}", f"c_storno_{s}", f"kv_storno_{s}"
    w.druck = _vertrag_pdf()
    w.digital = _vertrag_pdf()
    w.run(w.db.vehicles.insert_one({"id": w.vid, "dealer_id": w.dealer_id, "lifecycle": "vertrag_erstellt",
                                    "data": dict(FAHRZEUG), "created_at": _jetzt()}))
    w.run(w.db.kaufvorgaenge.insert_one({"id": w.kvid, "dealer_id": w.dealer_id, "user_id": w.chef["id"],
                                         "vehicle_id": w.vid, "contract_id": w.cid,
                                         "status": "vertrag_erstellt", "created_at": _jetzt()}))
    w.run(w.db.generated_pdfs.insert_one({
        "id": w.cid, "dealer_id": w.dealer_id, "user_id": w.chef["id"], "vehicle_id": w.vid,
        "kaufvorgang_id": w.kvid, "version": 2, "filename": "Kaufvertrag_VW_Golf.pdf",
        "contract_data": dict(VERTRAG),
        "pdf_b64": base64.b64encode(w.druck).decode(),
        "pdf_digital_b64": base64.b64encode(w.digital).decode(), "created_at": _jetzt()}))
    w.run(w.db.generated_pdf_versions.insert_one({
        "contract_id": w.cid, "dealer_id": w.dealer_id, "version": 1, "filename": "alt.pdf",
        "pdf_b64": base64.b64encode(w.druck).decode()}))

    def status(kv=None, fahrzeug=None):
        if kv:
            w.run(w.db.kaufvorgaenge.update_one({"id": w.kvid}, {"$set": {"status": kv}}))
        if fahrzeug:
            w.run(w.db.vehicles.update_one({"id": w.vid}, {"$set": {"lifecycle": fahrzeug}}))
    w.status = status
    yield w
    try:
        w.run(w.client.drop_database(w.db_name))
    finally:
        w.client.close()
        w.loop.close()


def _abrufen(w, variante="druck") -> bytes:
    import routes.contracts as C
    return w.run(C.get_contract_pdf(w.cid, w.chef, variante=variante)).body


def _gespeichert(w):
    doc = w.run(w.db.generated_pdfs.find_one({"id": w.cid}))
    alt = w.run(w.db.generated_pdf_versions.find_one({"contract_id": w.cid}))
    return doc["pdf_b64"], doc["pdf_digital_b64"], alt["pdf_b64"]


def test_03_nur_bei_storniert_und_weg_nach_reaktivierung(welt):
    w = welt
    vorher = _gespeichert(w)
    for variante in ("druck", "digital"):
        assert "STORNIERT" not in _text(_abrufen(w, variante)), "aktiver Vertrag ohne Zeichen"
    w.status(kv="storniert")
    for variante in ("druck", "digital"):
        seiten = _seiten_texte(_abrufen(w, variante))
        assert seiten and all("STORNIERT" in t for t in seiten), variante
    assert _gespeichert(w) == vorher, "gespeicherte PDFs duerfen sich nicht aendern"
    # wieder aktiv -> Zeichen weg
    w.status(kv="abholung_geplant")
    assert "STORNIERT" not in _text(_abrufen(w))
    assert _gespeichert(w) == vorher


def test_04_fahrzeug_storniert_reicht(welt):
    w = welt
    w.status(fahrzeug="storniert")
    assert "STORNIERT" in _text(_abrufen(w))
    w.status(fahrzeug="verglichen")
    assert "STORNIERT" not in _text(_abrufen(w))


def test_05_archivierte_fassung_und_betreiber(welt):
    import routes.admin as A
    import routes.contracts as C
    w = welt
    w.status(kv="storniert")
    alt = w.run(C.get_contract_version_pdf(w.cid, 1, w.chef)).body
    assert all("STORNIERT" in t for t in _seiten_texte(alt))

    async def _admin():
        antwort = await A.admin_contract_pdf(w.cid, None)
        teile = [t async for t in antwort.body_iterator]
        return b"".join(t if isinstance(t, bytes) else t.encode() for t in teile)
    assert "STORNIERT" in _text(w.run(_admin()))
    assert _gespeichert(w)[2] == base64.b64encode(w.druck).decode()


def test_06_falls_storniert_unbekannter_vertrag_und_leere_bytes(welt):
    import vertrag_storno as S
    w = welt
    assert w.run(S.falls_storniert("gibt_es_nicht", w.druck)) == w.druck
    assert w.run(S.falls_storniert(w.cid, None)) is None
    assert w.run(S.falls_storniert(w.cid, b"")) == b""


def test_07_alle_auslieferwege_legen_das_zeichen_auf():
    """Quelltext-Waechter: jeder Weg, der ein Vertrags-PDF ausliefert, ruft falls_storniert —
    der Mailanhang, der oeffentliche Link, Fahrer-App, Kundenportal (vor der Unterschrift)."""
    import routes.admin as A
    import routes.contracts as C
    import routes.drivers as D
    import routes.kundenportal as K
    for fn in (C.get_contract_pdf, C.public_vertrag_pdf, C.get_contract_version_pdf,
               C.send_contract, D.driver_contract_pdf, A.admin_contract_pdf, K.portal_sitzung_pdf):
        assert "falls_storniert(" in inspect.getsource(fn), fn.__name__
    # Der Speicher wird nie mit dem Zeichen beschrieben
    quelle = (BACKEND / "vertrag_storno.py").read_text(encoding="utf-8")
    assert "update_one" not in quelle and "insert_one" not in quelle and "put_object" not in quelle
    # unterschriebene Fassung (Nachweis) bleibt ungestempelt
    assert "falls_storniert(" not in inspect.getsource(K.portal_pdf)


def test_08_kein_zweites_zeichen_in_der_app_vorschau():
    """Die App zeigt das PDF per pdf.js — das Zeichen kommt nur vom Server."""
    anzeige = (BACKEND.parent / "frontend" / "src" / "lib" / "pdfAnzeige.js").read_text(encoding="utf-8")
    assert "STORNIERT" not in anzeige


# =================================================================== Zulassung bei Uebergabe
def test_10_zulassungssatz_je_wert():
    from vertrag_zulassung import SATZ_ABGEMELDET, SATZ_ANGEMELDET
    ab = _text(_vertrag_pdf(zulassung="abgemeldet"))
    an = _text(_vertrag_pdf(zulassung="angemeldet"))
    leer = _text(_vertrag_pdf(zulassung=""))
    assert SATZ_ABGEMELDET in ab and "Zusätzliche Vereinbarung" in ab and "drei Werktagen" not in ab
    assert " ".join(SATZ_ANGEMELDET.split()) in an and "abgemeldet übergeben" not in an
    assert "übergeben" not in leer and "Zusätzliche Vereinbarung" not in leer


def test_11_altvertrag_ohne_feld_unveraendert():
    """Altvertraege ohne Feld: kein Satz, kein neuer Abschnitt — der Text ist derselbe wie bei "unbekannt"."""
    alt = _text(_vertrag_pdf())
    assert "übergeben" not in alt and "Zusätzliche Vereinbarung" not in alt
    assert alt == _text(_vertrag_pdf(zulassung=""))


def test_12_mit_sonstiger_vereinbarung_beides():
    t = _text(_vertrag_pdf(zulassung="abgemeldet", sonstige_vereinbarung="8-fach bereift."))
    assert "8-fach bereift." in t and "Das Fahrzeug wird abgemeldet übergeben." in t
    assert t.index("8-fach bereift.") < t.index("abgemeldet übergeben")
    assert t.count("Zusätzliche Vereinbarung") == 1


def test_13_feld_optional_und_geprueft():
    from pydantic import ValidationError
    from routes.contracts import ContractIn
    kopf = {"vehicle_id": "v1", "seller_name": "Max", "purchase_price": 1000}
    assert ContractIn(**kopf).zulassung == ""
    assert ContractIn(**kopf, zulassung="unbekannt").zulassung == ""
    assert ContractIn(**kopf, zulassung=" Abgemeldet ").zulassung == "abgemeldet"
    assert ContractIn(**kopf, zulassung="angemeldet").zulassung == "angemeldet"
    with pytest.raises(ValidationError):
        ContractIn(**kopf, zulassung="vielleicht")


def test_14_hilfsfunktion():
    from vertrag_zulassung import zulassung_satz, zusaetzliche_vereinbarung_text
    assert zulassung_satz({}) == "" and zulassung_satz({"zulassung": None}) == ""
    assert zulassung_satz({"zulassung": "unbekannt"}) == ""
    assert zulassung_satz({"zulassung": "ABGEMELDET"}).startswith("Das Fahrzeug wird abgemeldet")
    assert zusaetzliche_vereinbarung_text({"sonstige_vereinbarung": "  "}) == ""
    assert zusaetzliche_vereinbarung_text({"sonstige_vereinbarung": "x", "zulassung": "angemeldet"}) \
        .startswith("x\n\nDas Fahrzeug wird angemeldet")
