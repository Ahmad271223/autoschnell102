# -*- coding: utf-8 -*-
"""Wunsch Ahmad 26.09.2026 abends: Kundennummer und Vertragsnummer selbst
vergeben.

- Vertragsnummer je Vertrag (ContractIn.contract_no): getrimmt, 3-40 Zeichen,
  eindeutig je Firma ueber alle Vertraege (Vorpruefung 409 + Teilindex
  vertragsnummer_je_firma als Rennschutz); leer = automatisch KV-<Datum>-….
- Kundennummer je Vertrag (ContractIn.kundennummer): leer = Firmenwert; sonst
  steht sie als vertrags_kundennummer in den Vertragsdaten und damit im
  PDF-Platzhalter {kundennummer}, im Abholauftrag und im Versand-Dialog.
- Firmen-Kundennummer (PUT /dealer/vertrags-kundennummer): Chef UND Sucher,
  firmenweit, eindeutig ueber alle Firmen, nie eine Anmeldenummer, Audit.

In-Prozess mit der `welt` aus test_befunde_runde17_termine (nur Mongo noetig).
"""
import io
import sys
import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_befunde_runde17_termine import _jetzt, _module, welt  # noqa: E402,F401

PROJEKT = Path(__file__).resolve().parents[2]
FIRMEN_NR = "482913"


def _pdf_stub(monkeypatch, aufrufe=None):
    """ReportLab ersetzen; merkt sich, womit das PDF gebaut wuerde (die
    Datenstruktur ist das Pruefobjekt, nicht das Bild)."""
    C = _module("routes.contracts")

    def _bauen(*, dealer, vehicle, contract, digital=False):
        if aufrufe is not None:
            aufrufe.append({"dealer": dict(dealer), "contract": dict(contract), "digital": digital})
        return b"%PDF-1.4 test"
    monkeypatch.setattr(C, "generate_contract_pdf", _bauen)


def _auto_daten_stub(monkeypatch):
    AD = _module("auto_daten")

    async def _anlegen(db, contract_dict, vehicle, gekauft_am=None):
        return f"ad_stub_{uuid.uuid4().hex[:8]}"

    async def _nichts(*a, **k):
        return None
    monkeypatch.setattr(AD, "anlegen", _anlegen)
    monkeypatch.setattr(AD, "zurueckrollen", _nichts)


def _pdf_text(pdf: bytes) -> str:
    from pypdf import PdfReader
    return "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(pdf)).pages)


def _body(C, vid, **felder):
    return C.ContractIn(vehicle_id=vid, seller_name="Vera Verkauf", purchase_price=4500,
                        payment_method="Bar", **felder)


def _vorbereiten(welt, monkeypatch, aufrufe=None):
    """Zwei Fahrzeuge des Suchers, Firma mit Kundennummer, Stubs."""
    C = _module("routes.contracts")
    w, db = welt.w, welt.db
    _pdf_stub(monkeypatch, aufrufe)
    _auto_daten_stub(monkeypatch)
    vids = [f"v_nr{i}_{w.s}" for i in range(3)]
    welt.run(db.vehicles.insert_many([w.fahrzeug(v, owner=w.sucher["id"], lifecycle="verglichen")
                                      for v in vids]))
    welt.run(db.dealers.update_one({"id": w.dealer_id},
                                   {"$set": {"vertrags_kundennummer": FIRMEN_NR, "address": "Weg 1",
                                             "zip_code": "10115", "city": "Berlin"}}))
    return C, vids


# ------------------------------------------------------------ Formpruefung
def test_01_vertragsnummer_form():
    C = _module("routes.contracts")
    ok = C.ContractIn(vehicle_id="v", seller_name="V", purchase_price=1,
                      contract_no="  AH-2026/001 ", kundennummer=" K-77 ")
    assert ok.contract_no == "AH-2026/001" and ok.kundennummer == "K-77", "getrimmt"
    leer = C.ContractIn(vehicle_id="v", seller_name="V", purchase_price=1, contract_no="  ")
    assert leer.contract_no == "" and leer.kundennummer == "", "leer bleibt leer (= automatisch)"
    assert C.ContractIn(vehicle_id="v", seller_name="V", purchase_price=1).contract_no == ""
    for schlecht in ("ab", "x" * 41, "KV#1", "KV{1}", "Nr:1"):
        with pytest.raises(ValidationError):
            C.ContractIn(vehicle_id="v", seller_name="V", purchase_price=1, contract_no=schlecht)
    assert C.ContractIn(vehicle_id="v", seller_name="V", purchase_price=1,
                        contract_no="KV 2026.09_26/A-1").contract_no == "KV 2026.09_26/A-1"
    for schlecht in ("K 1", "K_1", "x" * 31, "K/1"):
        with pytest.raises(ValidationError):
            C.ContractIn(vehicle_id="v", seller_name="V", purchase_price=1, kundennummer=schlecht)
    assert C.ContractIn(vehicle_id="v", seller_name="V", purchase_price=1,
                        kundennummer="AB-12-cd").kundennummer == "AB-12-cd"
    # Zahlen kommen als Zahl (coerce_numbers_to_str) — 482913 ist eine Kundennummer
    assert C.ContractIn(vehicle_id="v", seller_name="V", purchase_price=1,
                        kundennummer=482913).kundennummer == "482913"


def test_02_firmen_kundennummer_form():
    K = _module("kontenanlage")
    D = _module("routes.dealer")
    assert K.vertrags_kundennummer_pruefen(" AB-12 ") == "AB-12"
    for schlecht in ("abc", "a" * 21, "AB_12", "AB 12", ""):
        with pytest.raises(ValueError):
            K.vertrags_kundennummer_pruefen(schlecht)
        with pytest.raises(ValidationError):
            D.VertragsKundennummerIn(vertrags_kundennummer=schlecht)
    assert D.VertragsKundennummerIn(vertrags_kundennummer="482913").vertrags_kundennummer == "482913"


# ------------------------------------------------------------ Vertragsnummer
def test_03_eigene_vertragsnummer_und_duplikat(welt, monkeypatch):
    C, (v1, v2, v3) = _vorbereiten(welt, monkeypatch)
    w, db = welt.w, welt.db

    async def lauf():
        a = await C.create_contract(_body(C, v1, contract_no=" AH-2026-0042 "), w.sucher)
        doc_a = await db.generated_pdfs.find_one({"id": a["id"]}, {"_id": 0, "pdf_b64": 0})
        # dieselbe Nummer fuer ein anderes Fahrzeug der Firma -> 409 mit Klartext
        with pytest.raises(HTTPException) as e:
            await C.create_contract(_body(C, v2, contract_no="AH-2026-0042"), w.sucher)
        # auch der Chef derselben Firma bekommt sie nicht
        with pytest.raises(HTTPException) as e_chef:
            await C.create_contract(_body(C, v2, contract_no="AH-2026-0042"), w.chef)
        # ohne Angabe: automatisch wie bisher
        b = await C.create_contract(_body(C, v2), w.sucher)
        doc_b = await db.generated_pdfs.find_one({"id": b["id"]}, {"_id": 0, "pdf_b64": 0})
        # eine eigene Nummer darf auch keiner AUTOMATISCHEN gleichen
        with pytest.raises(HTTPException) as e_auto:
            await C.create_contract(_body(C, v3, contract_no=doc_b["contract_no"]), w.sucher)
        # andere Nummer geht
        c = await C.create_contract(_body(C, v3, contract_no="AH-2026-0043"), w.sucher)
        anzahl = await db.generated_pdfs.count_documents({"dealer_id": w.dealer_id})
        return a, doc_a, e.value, e_chef.value, doc_b, e_auto.value, c, anzahl

    a, doc_a, e, e_chef, doc_b, e_auto, c, anzahl = welt.run(lauf())
    assert a["contract_no"] == "AH-2026-0042" and doc_a["contract_no"] == "AH-2026-0042"
    assert doc_a["contract_no_eigen"] is True
    assert doc_a["contract_data"]["contract_no"] == "AH-2026-0042", "das PDF nimmt die Nummer von hier"
    assert e.status_code == 409 and "Vertragsnummer bereits vergeben" in str(e.detail), e.detail
    assert "AH-2026-0042" in str(e.detail), "Klartext nennt die Nummer"
    assert e_chef.status_code == 409
    assert doc_b["contract_no"].startswith("KV-") and len(doc_b["contract_no"]) == len("KV-20260926-ABCDEF")
    assert doc_b["contract_no_eigen"] is False
    assert e_auto.status_code == 409
    assert c["contract_no"] == "AH-2026-0043"
    assert anzahl == 3, "der abgelehnte Versuch hat nichts angelegt"


def test_04_rennen_zweier_anlagen_faengt_der_teilindex(welt, monkeypatch):
    """Vorpruefung ausgeschaltet (zwei Anlagen gleichzeitig): der Teilindex
    vertragsnummer_je_firma meldet DuplicateKey, die Antwort ist 409 —
    kein 500, kein zweiter Vertrag. Automatische Nummern liegen nicht im
    Index (contract_no_eigen fehlt/false)."""
    C, (v1, v2, _) = _vorbereiten(welt, monkeypatch)
    I = _module("indizes")
    w, db = welt.w, welt.db

    async def _frei(dealer_id, nr):
        return False
    monkeypatch.setattr(C, "_vertragsnummer_belegt", _frei)

    async def lauf():
        assert await I.vertragsnummer_index(db), "Teilindex steht (idempotent)"
        assert await I.vertragsnummer_index(db)
        info = await db.generated_pdfs.index_information()
        assert I.VERTRAGSNUMMER_INDEX in info
        assert info[I.VERTRAGSNUMMER_INDEX].get("partialFilterExpression") == {"contract_no_eigen": True}
        await db.generated_pdfs.insert_one({
            "id": f"c_race_{w.s}", "dealer_id": w.dealer_id, "user_id": w.chef["id"],
            "contract_no": "RACE-1", "contract_no_eigen": True, "created_at": _jetzt()})
        with pytest.raises(HTTPException) as e:
            await C.create_contract(_body(C, v1, contract_no="RACE-1"), w.sucher)
        # zwei automatische mit gleicher Nummer stoeren den Index nicht
        await db.generated_pdfs.insert_many([
            {"id": f"c_auto{i}_{w.s}", "dealer_id": w.dealer_id, "user_id": w.chef["id"],
             "contract_no": "KV-GLEICH", "contract_no_eigen": False, "created_at": _jetzt()}
            for i in range(2)])
        anzahl = await db.generated_pdfs.count_documents({"dealer_id": w.dealer_id})
        return e.value, anzahl

    e, anzahl = welt.run(lauf())
    assert e.status_code == 409 and "Vertragsnummer bereits vergeben" in str(e.detail), e.detail
    assert anzahl == 3, "nur die drei direkt eingefuegten"


# ------------------------------------------------------------ Kundennummer je Vertrag
def test_05_kundennummer_je_vertrag_in_pdf_daten_platzhalter_abholauftrag(welt, monkeypatch):
    aufrufe = []
    C, (v1, v2, _) = _vorbereiten(welt, monkeypatch, aufrufe)
    P = _module("vertrag_platzhalter")
    Pk = _module("pickup_pdf_service")
    from vertrag_felder import _apply_contract_overrides
    w, db = welt.w, welt.db
    firma = welt.run(db.dealers.find_one({"id": w.dealer_id}, {"_id": 0}))

    async def lauf():
        mit = await C.create_contract(_body(C, v1, kundennummer="K-77"), w.sucher)
        ohne = await C.create_contract(_body(C, v2), w.sucher)
        return (await db.generated_pdfs.find_one({"id": mit["id"]}, {"_id": 0, "pdf_b64": 0}),
                await db.generated_pdfs.find_one({"id": ohne["id"]}, {"_id": 0, "pdf_b64": 0}))

    doc_mit, doc_ohne = welt.run(lauf())
    # am Vertrag gespeichert: oben (Liste/Suche) und in den Vertragsdaten
    assert doc_mit["kundennummer"] == "K-77"
    assert doc_mit["contract_data"]["kundennummer"] == "K-77"
    assert doc_mit["contract_data"]["vertrags_kundennummer"] == "K-77", "eingefroren wie die Kaeuferdaten"
    assert doc_ohne["kundennummer"] == FIRMEN_NR and doc_ohne["contract_data"]["vertrags_kundennummer"] == FIRMEN_NR
    # PDF-Datenstruktur (ohne Rendern): das Kaeufer-Dokument, das der PDF-Bauer bekam
    mit_pdf = [a for a in aufrufe if a["contract"].get("kundennummer") == "K-77"]
    assert mit_pdf and all(a["dealer"]["vertrags_kundennummer"] == "K-77" for a in mit_pdf)
    ohne_pdf = [a for a in aufrufe if not a["contract"].get("kundennummer")]
    assert ohne_pdf and all(a["dealer"]["vertrags_kundennummer"] == FIRMEN_NR for a in ohne_pdf)
    # Platzhalter {kundennummer}: Vertrag gewinnt, Firma ist Rueckfall
    assert P.werte(doc_mit, firma)["{kundennummer}"] == "K-77"
    assert P.werte(doc_ohne, firma)["{kundennummer}"] == FIRMEN_NR
    assert P.ersetzen("Nr. {kundennummer}", doc_mit, firma) == "Nr. K-77"
    # Abholauftrag / Fahrer-Ansicht: auftraggeber_fuer_termin laeuft ueber
    # _apply_contract_overrides mit den Vertragsdaten
    _, auftraggeber = _apply_contract_overrides(contract=doc_mit["contract_data"], vehicle={}, dealer=firma)
    assert auftraggeber["vertrags_kundennummer"] == "K-77"
    pdf = Pk.build_pickup_pdf(
        appointment={"id": "a-nr", "pickup_date": "2099-01-01", "pickup_time": "10:00",
                     "seller_name": "Vera Verkauf", "pickup_address": "Abholweg 9"},
        vehicle={"make_label": "BMW", "model_label": "320d"}, contract={},
        dealer=auftraggeber, driver={"display_name": "Fahrer"})
    text = _pdf_text(pdf)
    assert "Kundennummer (Vertrag): K-77" in text and FIRMEN_NR not in text
    # echtes Vertrags-PDF mit Platzhalter in den Besonderen Vereinbarungen
    from pdf_service import generate_contract_pdf
    text = _pdf_text(generate_contract_pdf(
        dealer=auftraggeber, vehicle={"make_label": "BMW", "model_label": "320d"},
        contract={**doc_mit["contract_data"], "additional_terms": "Nur mit Kundennummer {kundennummer}."},
        digital=True))
    assert "Nur mit Kundennummer K-77." in text, text
    assert "AH-" not in text or "K-77" in text


def test_06_vorschau_nimmt_beide_nummern(welt, monkeypatch):
    aufrufe = []
    C, (v1, _, _) = _vorbereiten(welt, monkeypatch, aufrufe)
    w = welt.w
    r = welt.run(C.preview_contract(_body(C, v1, contract_no="VORSCHAU-1", kundennummer="K-9"), w.sucher))
    assert r.status_code == 200 and aufrufe
    assert aufrufe[-1]["contract"]["contract_no"] == "VORSCHAU-1"
    assert aufrufe[-1]["dealer"]["vertrags_kundennummer"] == "K-9"


# ------------------------------------------------------------ Firmen-Kundennummer
def test_07_firmen_kundennummer_setzen_chef_sucher_kollision_audit(welt, monkeypatch):
    D = _module("routes.dealer")
    monkeypatch.setattr(D, "db", welt.db)      # routes.dealer haelt ein eigenes db
    w, db = welt.w, welt.db
    andere = f"d_andere_{w.s}"
    welt.run(db.dealers.update_one({"id": w.dealer_id}, {"$set": {"vertrags_kundennummer": FIRMEN_NR}}))
    welt.run(db.dealers.insert_one({"id": andere, "user_id": f"chef_andere_{w.s}",
                                    "company_name": "Andere GmbH", "kunden_nr": 77123,
                                    "vertrags_kundennummer": "FREMD-1", "created_at": _jetzt()}))
    try:
        async def lauf():
            # Chef setzt firmenweit
            r1 = await D.vertrags_kundennummer_setzen(
                D.VertragsKundennummerIn(vertrags_kundennummer=" AH-2026 "), dict(w.chef))
            f1 = await db.dealers.find_one({"id": w.dealer_id}, {"_id": 0})
            # unveraendert speichern: kein Fehler, kein zweiter Audit-Eintrag
            r1b = await D.vertrags_kundennummer_setzen(
                D.VertragsKundennummerIn(vertrags_kundennummer="AH-2026"), dict(w.chef))
            # Sucher darf auch — ebenfalls firmenweit, kein persoenlicher Override
            r2 = await D.vertrags_kundennummer_setzen(
                D.VertragsKundennummerIn(vertrags_kundennummer="SU-4711"), dict(w.sucher))
            f2 = await db.dealers.find_one({"id": w.dealer_id}, {"_id": 0})
            su = await db.users.find_one({"id": w.sucher["id"]}, {"_id": 0})
            fehler = []
            for besetzt in ("FREMD-1", "77123"):
                with pytest.raises(HTTPException) as e:
                    await D.vertrags_kundennummer_setzen(
                        D.VertragsKundennummerIn(vertrags_kundennummer=besetzt), dict(w.chef))
                fehler.append(e.value)
            f3 = await db.dealers.find_one({"id": w.dealer_id}, {"_id": 0})
            logs = await db.activity_logs.find(
                {"dealer_id": w.dealer_id, "action": "einstellungen.vertrags_kundennummer.geaendert"},
                {"_id": 0}).sort("created_at", 1).to_list(10)
            return r1, f1, r1b, r2, f2, su, fehler, f3, logs

        r1, f1, r1b, r2, f2, su, fehler, f3, logs = welt.run(lauf())
        assert r1 == {"vertrags_kundennummer": "AH-2026", "geaendert": True}
        assert f1["vertrags_kundennummer"] == "AH-2026"
        assert r1b == {"vertrags_kundennummer": "AH-2026", "geaendert": False}
        assert r2["geaendert"] is True and f2["vertrags_kundennummer"] == "SU-4711"
        assert "vertrags_kundennummer" not in (su.get("settings_override") or {}), "firmenweit, kein Override"
        for e in fehler:
            assert e.status_code == 409 and "Kundennummer bereits vergeben" in str(e.detail), e.detail
        assert f3["vertrags_kundennummer"] == "SU-4711", "Kollision aendert nichts"
        assert [(l["meta"]["vorher"], l["meta"]["nachher"], l["user_id"]) for l in logs] == [
            (FIRMEN_NR, "AH-2026", w.chef["id"]), ("AH-2026", "SU-4711", w.sucher["id"])]
        assert logs[1]["meta"]["rolle"] == "sucher" and logs[1]["meta"]["bereich"] == "firma"
    finally:
        welt.run(db.dealers.delete_one({"id": andere}))


def test_08_belegt_pruefung_meidet_eigene_firma_und_anmeldenummern(welt):
    K = _module("kontenanlage")
    w, db = welt.w, welt.db
    andere = f"d_andere2_{w.s}"
    welt.run(db.dealers.update_one({"id": w.dealer_id}, {"$set": {"vertrags_kundennummer": "MEINE-1",
                                                                  "kunden_nr": 55001}}))
    welt.run(db.dealers.insert_one({"id": andere, "user_id": f"chef_andere2_{w.s}", "kunden_nr": 55002,
                                    "vertrags_kundennummer": "DEREN-1", "created_at": _jetzt()}))
    try:
        belegt = lambda nr: welt.run(K.vertrags_kundennummer_belegt(db, nr, ausser_dealer_id=w.dealer_id))  # noqa: E731
        assert belegt("MEINE-1") is False, "die eigene Nummer zaehlt nicht als fremd"
        assert belegt("DEREN-1") is True
        assert belegt("55001") is True and belegt("55002") is True, "nie eine Anmeldenummer, auch nicht die eigene"
        assert belegt("FREI-9") is False
    finally:
        welt.run(db.dealers.delete_one({"id": andere}))


# ------------------------------------------------------------ Verdrahtung
def test_09_verdrahtung_oberflaeche_server_index():
    src = (PROJEKT / "frontend" / "src")
    dialog = (src / "components" / "ContractDialog.jsx").read_text(encoding="utf-8")
    assert 'testid="contract-vertragsnummer"' in dialog and 'testid="contract-kundennummer"' in dialog
    assert 'kundennummer: dealer?.vertrags_kundennummer || ""' in dialog, "Vorbelegung Firmenwert"
    assert 'data-testid="contract-nummern-fehler"' in dialog, "409-Text unter dem Feld"
    send = (src / "components" / "SendDialog.jsx").read_text(encoding="utf-8")
    assert "cd.kundennummer || cd.vertrags_kundennummer || d?.vertrags_kundennummer" in send
    einst = (src / "pages" / "app" / "Einstellungen.jsx").read_text(encoding="utf-8")
    assert 'data-testid="set-vertrags-kundennummer"' in einst
    assert 'api.put("/dealer/vertrags-kundennummer"' in einst
    server = (PROJEKT / "backend" / "server.py").read_text(encoding="utf-8")
    assert "vertragsnummer_index" in server, "Teilindex wird beim Start angelegt"
    from routes.dealer import _SUCHER_SICHT_ZUSATZ
    assert "vertrags_kundennummer" in _SUCHER_SICHT_ZUSATZ
