# -*- coding: utf-8 -*-
"""Pruefer-Restpunkte 28.09.2026 zur Startpruefung vom 27.09. (K3/K4/K5).

(1) Altvertraege vom 24.-27.09. tragen ein automatisch gesetztes
    contract_data.empfang_schluessel=True (Schluesselanzahl eingetippt oder
    aus dem Inserat). empfang_kaestchen_leeren griff nur beim Anlegen und in
    der Vorschau; jede neue Fassung (regenerate_contract_for_pickup) uebernahm
    das Kreuz. Jetzt: Migration 21 leert die Kaestchen einmalig fuer Vertraege
    ab 24.09., deren Uebergabe noch nicht stattgefunden hat (mit Vermerk), und
    jede neue Fassung VOR der Uebergabe leert sie ebenfalls.
(2) Vorschau = Anlage: kaeufer_einfrieren in preview_contract war durch
    keinen Test festgeschrieben.
(3) Abholprotokoll-PDF: "nein (vorhanden, defekt)" kombinierte "nein" mit
    "vorhanden" — jetzt "nein (fehlt)", "nein (defekt)", "nein (anders als
    beschrieben)" und die passende Legende.

PDF-Tests ohne Datenbank; die uebrigen Tests laufen gegen eine eigene
Wegwerf-Datenbank (Praefix DB_NAME).
"""
import asyncio
import base64
import io
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from vertrag_felder import EMPFANG_KAESTCHEN, KAEUFER_FELDER  # noqa: E402

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"

FIRMA = {"company_name": "Altvertrag Test GmbH", "contact_person": "Chef A",
         "address": "Weg 2", "zip_code": "10117", "city": "Berlin",
         "phone": "030 2", "email": "chef@altvertrag.test", "empfang_drucken": True}
VEHICLE = {"make_label": "Audi", "model_label": "A4"}
DIALOG = {"seller_name": "Vera Verkauf", "seller_city": "Dresden", "seller_phone": "0170 1234567",
          "purchase_price": 9900, "pickup_date": "2026-09-30",
          "empfang_datum": "2026-09-30", "empfang_ort_kaeufer": "Berlin",
          "empfang_ort_verkaeufer": "Dresden", "schluessel_anzahl": "2",
          "empfang_schluessel": True}
#: so sah ein Vertrag vom 24.-27.09. aus (Kreuz automatisch gesetzt)
ALT_ANGELEGT = "2026-09-25T09:30:00+00:00"


def _jetzt():
    return datetime.now(timezone.utc).isoformat()


def _module(name):
    import importlib
    return importlib.import_module(name)


def _text(pdf: bytes) -> str:
    from pypdf import PdfReader
    return " ".join("\n".join((p.extract_text() or "")
                              for p in PdfReader(io.BytesIO(pdf)).pages).split())


# =============================================================== (3) Protokoll-PDF
def _pdf_text_roh(pdf: bytes) -> str:
    from pypdf import PdfReader
    text = "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(pdf)).pages)
    return re.sub(r"[ \xa0]+", " ", text)


def test_p3_nein_gruende_ohne_vorhanden():
    """Kein "nein" steht mehr neben "vorhanden"; die Kurzformen sind fest."""
    PDF = _module("pickup_pdf_service")
    assert PDF.NEIN_GRUENDE == {"fehlt": "fehlt", "defekt": "defekt",
                                "anders": "anders als beschrieben"}
    for grund in PDF.NEIN_GRUENDE.values():
        assert "vorhanden" not in grund and "komplett" not in grund, grund
    assert PDF._checkwert(" DEFEKT ") == ("nein", "defekt")
    assert PDF._checkwert("fehlt") == ("nein", "fehlt")


def test_p3_echtes_protokoll_wortlaut_und_legende():
    """Ende zu Ende: das ausgefuellte Protokoll druckt die neuen Zeilen und
    die Legende '[–] = nein / fehlt / defekt / anders'."""
    PDF = _module("pickup_pdf_service")
    features = {"Navigationssystem": "defekt", "Sitzheizung": "fehlt",
                "Tempomat": "anders", "Klimaanlage": True}
    pdf = PDF.build_pickup_pdf(
        appointment={"id": f"t_{uuid.uuid4().hex[:8]}", "pickup_date": "2026-09-28"},
        vehicle={"make_label": "BMW", "model_label": "320d", "features": list(features)},
        contract={"purchase_price": 18000.0},
        dealer={"company_name": "Autohaus Test"}, driver={"name": "Fahrer"},
        filled={"features": features, "documents": {}, "condition": {}, "version": 1})
    text = _pdf_text_roh(pdf)
    assert "[–] Navigationssystem — nein (defekt)" in text, text
    assert "[–] Sitzheizung — nein (fehlt)" in text, text
    assert "[–] Tempomat — nein (anders als beschrieben)" in text, text
    assert "vorhanden, defekt" not in text and "fehlt komplett" not in text, text
    assert "[–] = nein / fehlt / defekt / anders." in text, text
    assert "[X] Klimaanlage" in text, text


# =============================================================== Wegwerf-DB
@pytest.fixture
def welt():
    from motor.motor_asyncio import AsyncIOMotorClient
    s = uuid.uuid4().hex[:10]
    names = ["deps", "routes.contracts", "migrationen", "kaufvorgang", "lifecycle",
             "auto_daten", "routes.appointments", "betrieb"]
    mods = [_module(n) for n in names]
    alt = [(m, getattr(m, "db", None)) for m in mods]

    class _W:
        pass

    w = _W()
    w.s = s
    w.dealer_id = f"d_alt_{s}"
    w.chef = {"id": f"chef_alt_{s}", "dealer_id": w.dealer_id, "role": "dealer"}
    w.loop = asyncio.new_event_loop()
    asyncio.set_event_loop(w.loop)
    w.client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    basis = os.environ.get("DB_NAME") or "autoschnell_spfix_altvertrag"
    w.db_name = f"{basis}_{s}"
    w.db = w.client[w.db_name]
    for m in mods:
        if hasattr(m, "db"):
            m.db = w.db
    w.run = lambda coro: w.loop.run_until_complete(coro)
    w.run(w.db.users.insert_one({**w.chef, "active": True, "created_at": _jetzt()}))
    w.run(w.db.dealers.insert_one({"id": w.dealer_id, "user_id": w.chef["id"],
                                   **FIRMA, "created_at": _jetzt()}))
    yield w
    try:
        w.run(w.client.drop_database(w.db_name))
    finally:
        for m, d in alt:
            if d is not None:
                m.db = d
        w.client.close()
        w.loop.close()


def _fahrzeug(w, vid):
    w.run(w.db.vehicles.insert_one({"id": vid, "dealer_id": w.dealer_id,
                                    "owner_user_id": w.chef["id"],
                                    "lifecycle": "verglichen", "status": "verglichen",
                                    "data": dict(VEHICLE), "created_at": _jetzt()}))


def _altvertrag(w, tag: str) -> dict:
    """Vertrag ueber create_contract anlegen und so zuruecksetzen, wie er vom
    24.-27.09. gespeichert wurde: automatisch gesetztes Schluessel-Kreuz."""
    C = _module("routes.contracts")
    vid = f"v_alt_{tag}_{w.s}"
    _fahrzeug(w, vid)
    body = C.ContractIn.model_validate(dict(DIALOG, vehicle_id=vid))
    w.run(C.create_contract(body, w.chef))
    doc = w.run(w.db.generated_pdfs.find_one({"vehicle_id": vid}, {"_id": 0}))
    w.run(w.db.generated_pdfs.update_one(
        {"id": doc["id"]},
        {"$set": {"contract_data.empfang_schluessel": True, "created_at": ALT_ANGELEGT}}))
    return w.run(w.db.generated_pdfs.find_one({"id": doc["id"]}, {"_id": 0}))


class _KreuzSpion:
    """Merkt sich, was pdf_service._angekreuzt beim Druck liefert."""

    def __init__(self, monkeypatch):
        P = _module("pdf_service")
        self.werte = []
        echt = P._angekreuzt

        def spion(wert):
            erg = echt(wert)
            self.werte.append(erg)
            return erg

        monkeypatch.setattr(P, "_angekreuzt", spion)


# =============================================================== (1) neue Fassung
def test_p1_neue_fassung_vor_uebergabe_leert_das_kreuz(welt, monkeypatch):
    """Termin verschoben, Uebergabe noch offen: die neue Fassung druckt
    "KFZ mit 2 Schluessel(n)" NICHT angekreuzt, contract_data ist leer, der
    Vertrag traegt den Vermerk. Vorher: Kreuz aus contract_data uebernommen."""
    C = _module("routes.contracts")
    w = welt
    doc = _altvertrag(w, "vor")
    spion = _KreuzSpion(monkeypatch)
    ok = w.run(C.regenerate_contract_for_pickup(
        contract_id=doc["id"], dealer_id=w.dealer_id, user=w.chef,
        pickup_date="2026-10-02"))
    assert ok is True
    neu = w.run(w.db.generated_pdfs.find_one({"id": doc["id"]}, {"_id": 0}))
    assert neu["version"] == 2
    for feld in EMPFANG_KAESTCHEN:
        assert neu["contract_data"][feld] is False, feld
    vermerk = neu.get("empfang_kaestchen_geleert") or {}
    assert vermerk.get("felder") == ["empfang_schluessel"], vermerk
    assert vermerk.get("quelle") == "neue_fassung:abholtermin_geaendert", vermerk
    assert spion.werte and not any(spion.werte), spion.werte
    assert "KFZ mit 2 Schlüssel(n)" in _text(base64.b64decode(neu["pdf_b64"]))
    # die archivierte Fassung 1 bleibt, wie sie war (Beweis)
    archiv = w.run(w.db.generated_pdf_versions.find_one({"contract_id": doc["id"], "version": 1}))
    assert archiv["contract_data"]["empfang_schluessel"] is True


@pytest.mark.parametrize("uebergabe", ["termin_abgeholt", "protokoll_final", "kaufvorgang",
                                       "nach_abholung"])
def test_p1_neue_fassung_nach_uebergabe_behaelt_den_stand(welt, uebergabe):
    """Nach der Uebergabe (Termin abgeholt, finales Protokoll, Kaufvorgang
    abgeholt, schon eine Fassung nach der Abholung) bleibt das Kreuz auch in
    einer spaeteren Fassung (Verkaeufer korrigiert) stehen."""
    C = _module("routes.contracts")
    w = welt
    doc = _altvertrag(w, uebergabe)
    tid = f"t_{uebergabe}_{w.s}"
    if uebergabe == "termin_abgeholt":
        w.run(w.db.appointments.insert_one({"id": tid, "dealer_id": w.dealer_id,
                                            "contract_id": doc["id"], "status": "abgeholt"}))
    elif uebergabe == "protokoll_final":
        w.run(w.db.appointments.insert_one({"id": tid, "dealer_id": w.dealer_id,
                                            "contract_id": doc["id"], "status": "geplant"}))
        w.run(w.db.pickup_protocols.insert_one({"id": f"p_{w.s}", "appointment_id": tid,
                                                "status": "final"}))
    elif uebergabe == "kaufvorgang":
        w.run(w.db.kaufvorgaenge.update_many({"contract_id": doc["id"]},
                                             {"$set": {"status": "abgeholt"}}))
        assert w.run(w.db.kaufvorgaenge.count_documents({"contract_id": doc["id"]})) == 1
    else:
        w.run(w.db.generated_pdfs.update_one({"id": doc["id"]},
                                             {"$set": {"nach_abholung_protokoll_id": "p_alt"}}))
    ok = w.run(C.regenerate_contract_for_pickup(
        contract_id=doc["id"], dealer_id=w.dealer_id, user=w.chef,
        verkaeufer={"seller_name": "Vera Verkauf-Neu"}, grund="verkaeufer_korrigiert"))
    assert ok is True
    neu = w.run(w.db.generated_pdfs.find_one({"id": doc["id"]}, {"_id": 0}))
    assert neu["contract_data"]["seller_name"] == "Vera Verkauf-Neu"
    assert neu["contract_data"]["empfang_schluessel"] is True, uebergabe
    assert "empfang_kaestchen_geleert" not in neu


def test_p1_neue_fassung_ohne_kreuz_ohne_vermerk(welt):
    """Ein Vertrag ohne Kreuz bekommt keinen Vermerk (und keine Abfrage der
    Uebergabe)."""
    C = _module("routes.contracts")
    w = welt
    doc = _altvertrag(w, "leer")
    w.run(w.db.generated_pdfs.update_one({"id": doc["id"]},
                                         {"$set": {"contract_data.empfang_schluessel": False}}))
    assert w.run(C.regenerate_contract_for_pickup(
        contract_id=doc["id"], dealer_id=w.dealer_id, user=w.chef, pickup_date="2026-10-03"))
    neu = w.run(w.db.generated_pdfs.find_one({"id": doc["id"]}, {"_id": 0}))
    assert "empfang_kaestchen_geleert" not in neu
    assert neu["contract_data"]["empfang_schluessel"] is False


# =============================================================== (1) Migration 21
def _roh(w, cid, *, angelegt=ALT_ANGELEGT, kreuz=None, **extra):
    daten = {"seller_name": "V", "purchase_price": 1, "empfang_schluessel": True}
    daten.update(kreuz or {})
    w.run(w.db.generated_pdfs.insert_one({
        "id": cid, "dealer_id": w.dealer_id, "user_id": w.chef["id"], "version": 1,
        "created_at": angelegt, "pdf_b64": "UERG", "contract_data": daten, **extra}))


def test_p1_migration_21_nur_vor_der_uebergabe_und_idempotent(welt):
    M = _module("migrationen")
    w = welt
    s = w.s
    # geleert werden:
    _roh(w, f"a_offen_{s}")
    _roh(w, f"g_mitternacht_{s}", angelegt="2026-09-23T22:30:00+00:00")   # 24.09. 00:30 Uhr
    _roh(w, f"h_entwurf_{s}", kreuz={"empfang_kaufpreis": "True",
                                     "empfang_zulassungsbescheinigung": True})
    w.run(w.db.appointments.insert_one({"id": f"th_{s}", "dealer_id": w.dealer_id,
                                        "contract_id": f"h_entwurf_{s}", "status": "geplant"}))
    w.run(w.db.pickup_protocols.insert_many([
        {"id": f"ph1_{s}", "appointment_id": f"th_{s}", "status": "draft"},
        # finales Protokoll, das ausdruecklich an einem ANDEREN Vertrag haengt
        {"id": f"ph2_{s}", "appointment_id": f"th_{s}", "status": "final",
         "contract_id": "anderer_vertrag"},
        # abgeloeste Protokollversion zaehlt nicht
        {"id": f"ph3_{s}", "appointment_id": f"th_{s}", "status": "final", "superseded": True}]))
    # bleiben unberuehrt:
    _roh(w, f"b_abgeholt_{s}")
    w.run(w.db.appointments.insert_one({"id": f"tb_{s}", "dealer_id": w.dealer_id,
                                        "contract_id": f"b_abgeholt_{s}", "status": "erledigt"}))
    _roh(w, f"c_protokoll_{s}")
    w.run(w.db.appointments.insert_one({"id": f"tc_{s}", "dealer_id": w.dealer_id,
                                        "contract_id": f"c_protokoll_{s}", "status": "geplant"}))
    w.run(w.db.pickup_protocols.insert_one({"id": f"pc_{s}", "appointment_id": f"tc_{s}",
                                            "status": "final"}))
    _roh(w, f"d_nach_abholung_{s}", nach_abholung_protokoll_id="pc_x")
    _roh(w, f"e_vor_24_{s}", angelegt="2026-09-23T21:59:00+00:00")        # 23.09. 23:59 Uhr
    _roh(w, f"f_vorgang_{s}")
    w.run(w.db.kaufvorgaenge.insert_one({"id": f"kv_{s}", "dealer_id": w.dealer_id,
                                         "contract_id": f"f_vorgang_{s}", "status": "abgeholt"}))
    _roh(w, f"i_protokoll_direkt_{s}")
    w.run(w.db.pickup_protocols.insert_one({"id": f"pi_{s}", "contract_id": f"i_protokoll_direkt_{s}",
                                            "appointment_id": "irgendwo", "status": "final"}))

    z = w.run(M.m21_empfang_kaestchen_leeren(w.db))
    assert z["geleert"] == 3 and z["nach_uebergabe"] == 5, z
    assert sorted(z["vertraege"]) == sorted([f"a_offen_{s}", f"g_mitternacht_{s}",
                                             f"h_entwurf_{s}"]), z

    def doc(cid):
        return w.run(w.db.generated_pdfs.find_one({"id": cid}, {"_id": 0}))

    for cid in (f"a_offen_{s}", f"g_mitternacht_{s}", f"h_entwurf_{s}"):
        d = doc(cid)
        for feld in EMPFANG_KAESTCHEN:
            assert d["contract_data"][feld] is False, (cid, feld)
        assert d["empfang_kaestchen_geleert"]["quelle"] == "migration_21"
        assert "Uebergabe" in d["empfang_kaestchen_geleert"]["hinweis"]
        assert d["pdf_b64"] == "UERG" and d["version"] == 1, "PDF/Fassung unveraendert"
    assert sorted(doc(f"h_entwurf_{s}")["empfang_kaestchen_geleert"]["felder"]) == sorted(
        EMPFANG_KAESTCHEN)
    for cid in (f"b_abgeholt_{s}", f"c_protokoll_{s}", f"d_nach_abholung_{s}",
                f"e_vor_24_{s}", f"f_vorgang_{s}", f"i_protokoll_direkt_{s}"):
        d = doc(cid)
        assert d["contract_data"]["empfang_schluessel"] is True, cid
        assert "empfang_kaestchen_geleert" not in d, cid

    # Idempotenz: zweiter Lauf aendert nichts, der Vermerk bleibt wie er war
    vorher = doc(f"a_offen_{s}")["empfang_kaestchen_geleert"]
    z2 = w.run(M.m21_empfang_kaestchen_leeren(w.db))
    assert z2["geleert"] == 0 and z2["vertraege"] == [], z2
    assert doc(f"a_offen_{s}")["empfang_kaestchen_geleert"] == vorher


def test_p1_migration_21_eingetragen():
    M = _module("migrationen")
    namen = {n: name for n, name, _ in M.MIGRATIONEN}
    assert namen[21] == "empfang_kaestchen_leeren"
    assert M.ZIEL_VERSION >= 21          # 22 seit 06.10.2026 (werkzeug_vergleiche_ablauf)


# =============================================================== (2) Vorschau = Anlage
def test_p2_vorschau_zeigt_dieselben_eingefrorenen_kaeuferdaten(welt, monkeypatch):
    """preview_contract friert die Kaeuferdaten ein wie create_contract. Die
    Vertragsdaten, aus denen die Vorschau druckt, muessen in allen
    Kaeuferfeldern und Empfangs-Kaestchen dem gespeicherten Vertrag gleichen —
    auch fuer ein Feld, das eingefroren anders aussieht (Telefon mit
    Leerzeichen -> getrimmt). Ohne kaeufer_einfrieren in der Vorschau fehlen
    dort dealer_company, empfang_drucken usw. bzw. steht das rohe Telefon."""
    C = _module("routes.contracts")
    w = welt
    vid = f"v_vorschau_{w.s}"
    _fahrzeug(w, vid)
    gedruckt = []
    echt = C.generate_contract_pdf

    def spion(*, dealer, vehicle, contract, digital=False):
        gedruckt.append((dict(contract), digital))
        return echt(dealer=dealer, vehicle=vehicle, contract=contract, digital=digital)

    monkeypatch.setattr(C, "generate_contract_pdf", spion)
    body = C.ContractIn.model_validate(dict(DIALOG, vehicle_id=vid, contract_no="KV-VORSCHAU-1",
                                            dealer_phone="  030 999  "))
    vorschau = w.run(C.preview_contract(body, w.chef, variante="druck"))
    vorschau_daten = gedruckt[0][0]
    w.run(C.create_contract(body, w.chef))
    doc = w.run(w.db.generated_pdfs.find_one({"vehicle_id": vid}, {"_id": 0}))
    gespeichert = doc["contract_data"]

    assert gespeichert["dealer_phone"] == "030 999"
    assert gespeichert["dealer_company"] == FIRMA["company_name"]
    assert gespeichert["empfang_drucken"] is True
    for feld in KAEUFER_FELDER:
        assert vorschau_daten.get(feld) == gespeichert.get(feld), (
            feld, vorschau_daten.get(feld), gespeichert.get(feld))
    for feld in EMPFANG_KAESTCHEN:
        assert vorschau_daten[feld] is False and gespeichert[feld] is False, feld
    # und das Dokument selbst: Vorschau-Text == Text der gespeicherten Druckfassung
    assert _text(vorschau.body) == _text(base64.b64decode(doc["pdf_b64"]))
