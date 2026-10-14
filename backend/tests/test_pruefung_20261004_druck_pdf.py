# -*- coding: utf-8 -*-
"""Pruefung 04.10.2026 (Nr. 42): Die Druckfassung (pdf_b64) hatte keinen Reparaturweg — fehlte sie oder
war sie beschaedigt, gab GET /contracts/{id}/pdf einen 500 (KeyError/binascii). Die digitale Fassung
konnte sich laengst aus den eingefrorenen Vertragsdaten nacherzeugen; jetzt die Druckfassung auch
(gleiche Basis: Ersteller, festgehaltene Kaeuferdaten/Logo/Design, Compare-and-Set auf die Fassung).

In-Prozess mit der Wegwerf-Welt aus test_rp_vertrag_20260922 (ReportLab dort ersetzt)."""
import base64
import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_rp_vertrag_20260922 import _erwarte, _modul, _vertrag, welt  # noqa: E402,F401


def test_42_fehlende_druckfassung_wird_nacherzeugt(welt):
    C = _modul("routes.contracts")
    w = welt
    cid = f"cdr_{w.s}"
    doc = _vertrag(w, cid, w.a)
    del doc["pdf_b64"]
    w.run(w.db.generated_pdfs.insert_one(doc))
    r = w.run(C.get_contract_pdf(cid, user=w.a))
    assert r.body == b"%PDF-1.4 rpv", "aus den Vertragsdaten erzeugt (ReportLab ist im Test ersetzt)"
    gespeichert = w.run(w.db.generated_pdfs.find_one({"id": cid}))
    assert base64.b64decode(gespeichert["pdf_b64"]) == b"%PDF-1.4 rpv" and gespeichert["pdf_druck_nacherzeugt_am"]


def test_42_beschaedigte_druckfassung_wird_ersetzt_intakte_bleibt(welt):
    C = _modul("routes.contracts")
    w = welt
    kaputt, heil = f"cdk_{w.s}", f"cdh_{w.s}"
    w.run(w.db.generated_pdfs.insert_one(_vertrag(w, kaputt, w.a, pdf_b64="kein base64 !!")))
    w.run(w.db.generated_pdfs.insert_one(_vertrag(w, heil, w.a)))
    assert w.run(C.get_contract_pdf(kaputt, user=w.a)).body == b"%PDF-1.4 rpv"
    assert w.run(C.get_contract_pdf(heil, user=w.a)).body == b"%PDF-1.4 d", "intakte Fassung unveraendert"
    assert "pdf_druck_nacherzeugt_am" not in w.run(w.db.generated_pdfs.find_one({"id": heil}))


def test_42_ohne_vertragsdaten_kein_500_sondern_503(welt):
    C = _modul("routes.contracts")
    w = welt
    cid = f"cdo_{w.s}"
    doc = _vertrag(w, cid, w.a, contract_data={})
    del doc["pdf_b64"]
    w.run(w.db.generated_pdfs.insert_one(doc))
    w.run(_erwarte(503, C.get_contract_pdf(cid, user=w.a)))


def test_42_fahrer_und_admin_nutzen_denselben_weg():
    for modul, funktion in (("routes.drivers", "driver_contract_pdf"),
                            ("routes.admin", "admin_contract_pdf")):
        q = inspect.getsource(getattr(_modul(modul), funktion))
        assert "_druck_pdf_bytes(" in q and 'b64decode(doc["pdf_b64"])' not in q, modul
