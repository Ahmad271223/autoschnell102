# -*- coding: utf-8 -*-
"""Nachtrag 28.09.2026 (empfang3) zu Migration 21 / uebergabe_erfolgt.

(1) contract_id ist am Termin optional (AppointmentIn). Ein abgeholter
    Termin, der nur vehicle_id traegt, blieb unerkannt: uebergabe_erfolgt
    lieferte False, Migration 21 haette den Vertrag eines uebergebenen Autos
    geleert. Jetzt zaehlen Termine/Protokolle/Kaufvorgaenge am Fahrzeug (ohne
    Vertrag oder mit diesem Vertrag) und der Lebenszyklus des Fahrzeugs.
(2) scripts/empfang_kreuz_liste.py: NUR LESENDE Liste der Vertraege ab
    24.09., deren aktuelles PDF noch das Kreuz "KFZ mit n Schluessel(n)" traegt.

Laeuft gegen eine eigene Wegwerf-Datenbank (Praefix DB_NAME).
"""
import base64
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

from test_empfang_altvertraege_20260928 import (ALT_ANGELEGT, FIRMA, _altvertrag,  # noqa: E402
                                                _module, _roh, welt)  # noqa: F401


def _fahrzeug_roh(w, vid, lifecycle="vertrag_erstellt", dealer_id=None, **extra):
    w.run(w.db.vehicles.insert_one({"id": vid, "dealer_id": dealer_id or w.dealer_id,
                                    "lifecycle": lifecycle, **extra}))


# =============================================================== (1) uebergabe_erfolgt
# empfang4: "bestand"/"verkauft"/"archiviert" allein zaehlen nicht mehr
# (Weiterverkauf vor der Abholung) — nur mit dem EIGENEN festgehaltenen
# Abhol-Vorgang; abgeholt_kaufvorgang_id zaehlt nur, wenn er der eigene ist
# (Faelle ohne Nachweis: test_empfang_uebergabe_vorgang_20260928.py).
FAELLE_JA = ["termin_abgeholt", "termin_erledigt", "termin_leere_contract_id",
             "protokoll_am_fahrzeug", "protokoll_am_fahrzeugtermin", "kaufvorgang_am_fahrzeug",
             "lc_abgeholt", "lc_bestand", "lc_verkauft", "lc_archiviert", "abhol_vorgang_fest"]
MIT_EIGENEM_VORGANG = {"lc_bestand", "lc_verkauft", "lc_archiviert", "abhol_vorgang_fest"}


@pytest.mark.parametrize("fall", FAELLE_JA)
def test_e3_uebergabe_ueber_das_fahrzeug(welt, fall):
    """Vorher False in jedem Fall (nur contract_id wurde gesucht)."""
    C = _module("routes.contracts")
    w = welt
    s = w.s
    cid, vid = f"k_{fall}_{s}", f"v_{fall}_{s}"
    lc = fall[3:] if fall.startswith("lc_") else "abholung_geplant"
    _fahrzeug_roh(w, vid, lifecycle=lc,
                  **({"abgeholt_kaufvorgang_id": "kv_x"} if fall in MIT_EIGENEM_VORGANG else {}))
    if fall.startswith("termin_"):
        termin = {"id": f"t_{s}", "dealer_id": w.dealer_id, "vehicle_id": vid,
                  "status": "erledigt" if fall == "termin_erledigt" else "abgeholt"}
        if fall == "termin_leere_contract_id":
            termin["contract_id"] = ""
        elif fall == "termin_abgeholt":
            termin["contract_id"] = None
        w.run(w.db.appointments.insert_one(termin))
    elif fall == "protokoll_am_fahrzeug":
        w.run(w.db.pickup_protocols.insert_one({"id": f"p_{s}", "dealer_id": w.dealer_id,
                                                "vehicle_id": vid, "appointment_id": "t_weg",
                                                "contract_id": None, "status": "final"}))
    elif fall == "protokoll_am_fahrzeugtermin":
        w.run(w.db.appointments.insert_one({"id": f"t_{s}", "dealer_id": w.dealer_id,
                                            "vehicle_id": vid, "status": "geplant"}))
        w.run(w.db.pickup_protocols.insert_one({"id": f"p_{s}", "appointment_id": f"t_{s}",
                                                "status": "final"}))
    elif fall == "kaufvorgang_am_fahrzeug":
        w.run(w.db.kaufvorgaenge.insert_one({"id": f"kv_{s}", "dealer_id": w.dealer_id,
                                             "vehicle_id": vid, "status": "abgeholt"}))
    doc = {"id": cid, "dealer_id": w.dealer_id, "vehicle_id": vid}
    if fall in MIT_EIGENEM_VORGANG:
        doc["kaufvorgang_id"] = "kv_x"
    assert w.run(C.uebergabe_erfolgt(w.db, doc)) is True, fall


FAELLE_NEIN = ["termin_geplant", "termin_anderer_vertrag", "termin_andere_firma",
               "protokoll_entwurf", "protokoll_anderer_vertrag", "kaufvorgang_offen",
               "lc_vertrag_erstellt", "lc_verkaufsentwurf", "fahrzeug_andere_firma",
               "ohne_fahrzeug"]


@pytest.mark.parametrize("fall", FAELLE_NEIN)
def test_e3_keine_uebergabe_bleibt_false(welt, fall):
    """Die Erweiterung macht nicht jeden Vertrag zu 'uebergeben': offene
    Termine, Protokoll-Entwuerfe, ausdruecklich andere Vertraege, fremde
    Firmen und Lebenszyklus-Stufen vor der Abholung zaehlen nicht."""
    C = _module("routes.contracts")
    w = welt
    s = w.s
    cid, vid = f"k_{fall}_{s}", f"v_{fall}_{s}"
    fremd = f"d_fremd_{s}"
    if fall == "fahrzeug_andere_firma":
        _fahrzeug_roh(w, vid, lifecycle="abgeholt", dealer_id=fremd)
    elif fall.startswith("lc_"):
        _fahrzeug_roh(w, vid, lifecycle=fall[3:])
    else:
        _fahrzeug_roh(w, vid)
    if fall == "termin_geplant":
        w.run(w.db.appointments.insert_one({"id": f"t_{s}", "dealer_id": w.dealer_id,
                                            "vehicle_id": vid, "status": "geplant"}))
    elif fall == "termin_anderer_vertrag":
        w.run(w.db.appointments.insert_one({"id": f"t_{s}", "dealer_id": w.dealer_id,
                                            "vehicle_id": vid, "contract_id": "anderer",
                                            "status": "abgeholt"}))
    elif fall == "termin_andere_firma":
        w.run(w.db.appointments.insert_one({"id": f"t_{s}", "dealer_id": fremd,
                                            "vehicle_id": vid, "status": "abgeholt"}))
    elif fall == "protokoll_entwurf":
        w.run(w.db.pickup_protocols.insert_one({"id": f"p_{s}", "dealer_id": w.dealer_id,
                                                "vehicle_id": vid, "status": "entwurf"}))
    elif fall == "protokoll_anderer_vertrag":
        w.run(w.db.pickup_protocols.insert_one({"id": f"p_{s}", "dealer_id": w.dealer_id,
                                                "vehicle_id": vid, "contract_id": "anderer",
                                                "status": "final"}))
    elif fall == "kaufvorgang_offen":
        w.run(w.db.kaufvorgaenge.insert_one({"id": f"kv_{s}", "dealer_id": w.dealer_id,
                                             "vehicle_id": vid, "status": "offen"}))
    doc = {"id": cid, "dealer_id": w.dealer_id,
           "vehicle_id": None if fall == "ohne_fahrzeug" else vid}
    assert w.run(C.uebergabe_erfolgt(w.db, doc)) is False, fall


def test_e3_migration_21_laesst_uebergebenes_auto_stehen(welt):
    """Migration 21 liest vehicle_id mit: ein Vertrag, dessen Auto ueber einen
    Termin OHNE contract_id abgeholt wurde, und einer, dessen Auto schon
    verkauft ist, behalten das Kreuz. Vorher: beide geleert.
    empfang4: "verkauft" zaehlt nur mit dem eigenen Abhol-Vorgang am Fahrzeug
    (abgeholt_kaufvorgang_id = kaufvorgang_id des Vertrags) — dafuer liest die
    Migration kaufvorgang_id mit."""
    M = _module("migrationen")
    w = welt
    s = w.s
    _fahrzeug_roh(w, f"v1_{s}", lifecycle="abholung_geplant")
    w.run(w.db.appointments.insert_one({"id": f"t1_{s}", "dealer_id": w.dealer_id,
                                        "vehicle_id": f"v1_{s}", "status": "abgeholt"}))
    _roh(w, f"k_termin_{s}", vehicle_id=f"v1_{s}")
    _fahrzeug_roh(w, f"v2_{s}", lifecycle="verkauft", abgeholt_kaufvorgang_id=f"kv2_{s}")
    _roh(w, f"k_verkauft_{s}", vehicle_id=f"v2_{s}", kaufvorgang_id=f"kv2_{s}")
    _fahrzeug_roh(w, f"v3_{s}", lifecycle="abholung_geplant")
    _roh(w, f"k_offen_{s}", vehicle_id=f"v3_{s}")

    z = w.run(M.m21_empfang_kaestchen_leeren(w.db))
    assert z["geleert"] == 1 and z["nach_uebergabe"] == 2, z
    assert z["vertraege"] == [f"k_offen_{s}"], z
    for cid in (f"k_termin_{s}", f"k_verkauft_{s}"):
        d = w.run(w.db.generated_pdfs.find_one({"id": cid}, {"_id": 0}))
        assert d["contract_data"]["empfang_schluessel"] is True, cid
        assert "empfang_kaestchen_geleert" not in d, cid


def test_e3_neue_fassung_nach_abholung_ueber_fahrzeugtermin_behaelt_kreuz(welt):
    """Ende zu Ende: Termin nur am Fahrzeug abgeholt, danach Verkaeufer
    korrigiert — die neue Fassung behaelt das Kreuz (vorher geleert)."""
    C = _module("routes.contracts")
    w = welt
    doc = _altvertrag(w, "fz")
    w.run(w.db.appointments.insert_one({"id": f"t_fz_{w.s}", "dealer_id": w.dealer_id,
                                        "vehicle_id": doc["vehicle_id"], "status": "abgeholt"}))
    assert w.run(C.regenerate_contract_for_pickup(
        contract_id=doc["id"], dealer_id=w.dealer_id, user=w.chef,
        verkaeufer={"seller_name": "Vera Verkauf-Neu"}, grund="verkaeufer_korrigiert"))
    neu = w.run(w.db.generated_pdfs.find_one({"id": doc["id"]}, {"_id": 0}))
    assert neu["contract_data"]["seller_name"] == "Vera Verkauf-Neu"
    assert neu["contract_data"]["empfang_schluessel"] is True
    assert "empfang_kaestchen_geleert" not in neu


# =============================================================== (2) Skript
def _pdf(kreuz: bool, drucken: bool = True) -> str:
    P = _module("pdf_service")
    c = {"seller_name": "Vera Verkauf", "purchase_price": 9900, "schluessel_anzahl": "2",
         "empfang_drucken": drucken, "empfang_schluessel": kreuz,
         "empfang_zulassungsbescheinigung": True, "empfang_kaufpreis": True}
    return base64.b64encode(P.generate_contract_pdf(
        dealer=dict(FIRMA), vehicle={"make_label": "Audi"}, contract=c)).decode()


def test_e3_pdf_pruefung_erkennt_nur_das_schluessel_kreuz():
    """Haken genau am Schluessel-Kaestchen; die anderen Kaestchen (hier
    angekreuzt) verwechselt die Pruefung nicht; ohne Empfangsblock bzw.
    digitale Ausfertigung / kaputtes PDF: None."""
    L = _module("empfang_kreuz_liste")
    P = _module("pdf_service")
    assert L.schluessel_kreuz_im_pdf(base64.b64decode(_pdf(True))) is True
    assert L.schluessel_kreuz_im_pdf(base64.b64decode(_pdf(False))) is False
    assert L.schluessel_kreuz_im_pdf(base64.b64decode(_pdf(True, drucken=False))) is None
    digital = P.generate_contract_pdf(dealer=dict(FIRMA), vehicle={"make_label": "Audi"},
                                      contract={"seller_name": "V", "empfang_schluessel": True},
                                      digital=True)
    assert L.schluessel_kreuz_im_pdf(digital) is None
    assert L.schluessel_kreuz_im_pdf(b"kein pdf") is None
    assert L.verkaeufer_kurz("Vera Maria Verkauf") == "Vera M. V."
    assert L.verkaeufer_kurz("") == "—"


def _alles(w):
    namen = sorted(w.run(w.db.list_collection_names()))
    return {n: sorted(w.run(w.db[n].find({}, {"_id": 0}).to_list(None)), key=repr)
            for n in namen}


def test_e3_skript_listet_nur_offene_vertraege_mit_kreuz_und_schreibt_nichts(welt, monkeypatch,
                                                                          capsys):
    L = _module("empfang_kreuz_liste")
    w = welt
    s = w.s
    mit, ohne = _pdf(True), _pdf(False)
    _fahrzeug_roh(w, f"va_{s}", lifecycle="abholung_geplant")
    _roh(w, f"a_offen_{s}", vehicle_id=f"va_{s}", pdf_b64=mit, status="Termin erstellt",
         seller_name="Vera Verkauf", kaufvorgang_id=f"kva_{s}")
    w.run(w.db.kaufvorgaenge.insert_one({"id": f"kva_{s}", "dealer_id": w.dealer_id,
                                         "contract_id": f"a_offen_{s}", "status": "offen"}))
    # Auto ueber Termin OHNE contract_id abgeholt -> nur mit --mit-uebergabe
    _fahrzeug_roh(w, f"vb_{s}", lifecycle="abholung_geplant")
    w.run(w.db.appointments.insert_one({"id": f"tb_{s}", "dealer_id": w.dealer_id,
                                        "vehicle_id": f"vb_{s}", "status": "abgeholt"}))
    _roh(w, f"b_abgeholt_{s}", vehicle_id=f"vb_{s}", pdf_b64=mit, seller_name="Bernd Bauer")
    _roh(w, f"c_ohne_kreuz_{s}", pdf_b64=ohne)
    _roh(w, f"d_vor_24_{s}", angelegt="2026-09-23T21:59:00+00:00", pdf_b64=mit)
    _roh(w, f"e_ohne_block_{s}", pdf_b64=_pdf(True, drucken=False))
    # Migration 21 hat die Daten schon geleert, das PDF traegt das Kreuz weiter
    _roh(w, f"f_geleert_{s}", angelegt="2026-09-26T10:00:00+00:00", pdf_b64=mit,
         kreuz={"empfang_schluessel": False}, seller_name="Frida",
         empfang_kaestchen_geleert={"quelle": "migration_21", "felder": ["empfang_schluessel"]})
    _roh(w, f"g_ohne_pdf_{s}", pdf_b64=None)
    # fremde Firma, fuer --firma
    w.run(w.db.generated_pdfs.insert_one({
        "id": f"h_fremd_{s}", "dealer_id": f"d_fremd_{s}", "version": 1,
        "created_at": ALT_ANGELEGT, "pdf_b64": mit, "contract_data": {}}))
    vorher = _alles(w)

    e = w.run(L.liste(w.db))
    assert e["geprueft"] == 7 and e["ohne_pdf"] == 1 and e["ohne_kaestchen"] == 1, e
    assert e["mit_kreuz"] == 4 and e["nach_uebergabe"] == 1, e
    ids = [r["id"] for r in e["vertraege"]]
    assert sorted(ids) == sorted([f"a_offen_{s}", f"f_geleert_{s}", f"h_fremd_{s}"]), ids
    a = next(r for r in e["vertraege"] if r["id"] == f"a_offen_{s}")
    assert a == {"id": f"a_offen_{s}", "dealer_id": w.dealer_id, "verkaeufer": "Vera V.",
                 "created_at": ALT_ANGELEGT, "status": "Termin erstellt", "vorgang": "offen",
                 "fassung": 1, "daten_geleert": None, "uebergabe": False}
    f = next(r for r in e["vertraege"] if r["id"] == f"f_geleert_{s}")
    assert f["daten_geleert"] == "migration_21" and f["verkaeufer"] == "Frida"

    nur_firma = w.run(L.liste(w.db, dealer_id=w.dealer_id))
    assert f"h_fremd_{s}" not in [r["id"] for r in nur_firma["vertraege"]]
    alle = w.run(L.liste(w.db, mit_uebergabe=True, dealer_id=w.dealer_id))
    b = next(r for r in alle["vertraege"] if r["id"] == f"b_abgeholt_{s}")
    assert b["uebergabe"] is True and b["verkaeufer"] == "Bernd B."

    # Aufruf wie auf dem Server (eigener Client, eigene Schleife)
    monkeypatch.setattr(L, "DB_NAME", w.db_name)
    assert L.main(["--firma", w.dealer_id]) == 0
    out = capsys.readouterr().out
    assert f"a_offen_{s}" in out and f"f_geleert_{s}" in out and "Vera V." in out
    assert f"b_abgeholt_{s}" not in out and "Verkauf " not in out
    assert "geleert (migration_21)" in out and "Nichts wurde geaendert" in out

    # NUR LESEND: kein Dokument in keiner Sammlung hat sich veraendert
    assert _alles(w) == vorher
