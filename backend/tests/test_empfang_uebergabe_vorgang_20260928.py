# -*- coding: utf-8 -*-
"""Nachtrag 28.09.2026 (empfang4) zu uebergabe_erfolgt (f6d2b0e).

(1) MITTEL: vehicles ist je Firma GEMEINSAM, es gibt einen Kaufvorgang je
    Vertrag. Die Pruefung des Fahrzeug-Lebenszyklus (abgeholt bzw.
    abgeholt_kaufvorgang_id) lief VOR den Vertragspruefungen und fragte
    nicht, zu welchem Vorgang die Abholung gehoert: hatte Sucher A das Auto
    abgeholt, galt Vertrag B von Sucher B am selben Auto (Vorgang offen) als
    uebergeben — Migration 21 liess sein automatisches Schluessel-Kreuz
    stehen, eine neue Fassung uebernahm es, empfang_kreuz_liste.py zeigte B
    nicht an. Jetzt zaehlen Fahrzeug-Nachweise nur, wenn
    abgeholt_kaufvorgang_id leer oder der eigene Vorgang ist und kein
    abgeholter Vorgang eines ANDEREN Vertrags am Fahrzeug steht; Termine und
    Protokolle am Fahrzeug nur, wenn sie keinem anderen Vertrag gehoeren.
(2) NIEDRIG: "bestand"/"verkauft"/"archiviert" sind ohne Abholung erreichbar
    (Weiterverkauf ab vertrag_erstellt). Dort zaehlt der Lebenszyklus allein
    nicht mehr.

Laeuft gegen eine eigene Wegwerf-Datenbank (Praefix DB_NAME).
"""
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

from test_empfang_altvertraege_20260928 import (_altvertrag, _module, _roh,  # noqa: E402
                                                welt)  # noqa: F401
from test_empfang_uebergabe_fahrzeug_20260928 import _fahrzeug_roh, _pdf  # noqa: E402


def _zwei_sucher(w, fall: str) -> dict:
    """Auto vid, Sucher A (Vertrag k_a, Vorgang kv_a) hat abgeholt, Sucher B
    (Vertrag k_b, Vorgang kv_b) ist offen. Liefert die IDs."""
    s = w.s
    ids = {"vid": f"v_{fall}_{s}", "k_a": f"k_a_{fall}_{s}", "k_b": f"k_b_{fall}_{s}",
           "kv_a": f"kv_a_{fall}_{s}", "kv_b": f"kv_b_{fall}_{s}", "t_a": f"t_a_{fall}_{s}"}
    vid = ids["vid"]
    # Termin/Protokoll-Faelle: noch nichts abgeholt/festgehalten — dort soll
    # allein die Zuordnung von Termin bzw. Protokoll entscheiden.
    nur_zuordnung = fall in ("termin_mit_fremdem_vorgang", "protokoll_am_fremden_termin")
    fest = None if fall == "fest_leer" or nur_zuordnung else ids["kv_a"]
    lc = "abholung_geplant" if nur_zuordnung else "abgeholt"
    _fahrzeug_roh(w, vid, lifecycle=lc, **({"abgeholt_kaufvorgang_id": fest} if fest else {}))
    status_a = "abholung_geplant" if lc == "abholung_geplant" else "abgeholt"
    w.run(w.db.kaufvorgaenge.insert_many([
        {"id": ids["kv_a"], "dealer_id": w.dealer_id, "vehicle_id": vid,
         "contract_id": ids["k_a"], "user_id": "sucher_a", "status": status_a},
        {"id": ids["kv_b"], "dealer_id": w.dealer_id, "vehicle_id": vid,
         "contract_id": ids["k_b"], "user_id": "sucher_b", "status": "vertrag_erstellt"}]))
    if fall == "termin_mit_fremdem_vorgang":
        # Termin nur am Fahrzeug, gehoert ueber kaufvorgang_id zu Sucher A
        w.run(w.db.appointments.insert_one({"id": ids["t_a"], "dealer_id": w.dealer_id,
                                            "vehicle_id": vid, "kaufvorgang_id": ids["kv_a"],
                                            "status": "abgeholt"}))
    elif fall == "protokoll_am_fremden_termin":
        # Termin von A (noch geplant), finales Protokoll nur am Fahrzeug, ohne Vertrag
        w.run(w.db.appointments.insert_one({"id": ids["t_a"], "dealer_id": w.dealer_id,
                                            "vehicle_id": vid, "contract_id": ids["k_a"],
                                            "status": "geplant"}))
        w.run(w.db.pickup_protocols.insert_one({"id": f"p_{fall}_{s}", "dealer_id": w.dealer_id,
                                                "vehicle_id": vid, "appointment_id": ids["t_a"],
                                                "contract_id": None, "status": "final"}))
    else:
        w.run(w.db.appointments.insert_one({"id": ids["t_a"], "dealer_id": w.dealer_id,
                                            "vehicle_id": vid, "contract_id": ids["k_a"],
                                            "kaufvorgang_id": ids["kv_a"], "status": "abgeholt"}))
        w.run(w.db.pickup_protocols.insert_one({"id": f"p_{fall}_{s}", "dealer_id": w.dealer_id,
                                                "vehicle_id": vid, "appointment_id": ids["t_a"],
                                                "contract_id": ids["k_a"], "status": "final"}))
    return ids


# =============================================================== (1) zwei Sucher
FAELLE_ZWEI = ["fest_fremd", "fest_leer", "b_ohne_zeiger", "b_zeiger_auf_fremden_vorgang",
               "termin_mit_fremdem_vorgang", "protokoll_am_fremden_termin"]


@pytest.mark.parametrize("fall", FAELLE_ZWEI)
def test_e4_zweiter_sucher_am_selben_auto_nicht_uebergeben(welt, fall):
    """Sucher A hat abgeholt (bzw. traegt Termin/Protokoll am Fahrzeug) —
    Vertrag B am selben Auto ist NICHT uebergeben. Vorher True in jedem Fall
    (Lebenszyklus/abgeholt_kaufvorgang_id bzw. Termin/Protokoll ohne Vertrag
    am Fahrzeug zaehlten fuer jeden Vertrag des Autos)."""
    C = _module("routes.contracts")
    w = welt
    ids = _zwei_sucher(w, fall)
    doc_b = {"id": ids["k_b"], "dealer_id": w.dealer_id, "vehicle_id": ids["vid"],
             "kaufvorgang_id": ids["kv_b"]}
    if fall == "b_ohne_zeiger":
        doc_b.pop("kaufvorgang_id")            # Altvertrag ohne Zeiger
    elif fall == "b_zeiger_auf_fremden_vorgang":
        doc_b["kaufvorgang_id"] = ids["kv_a"]  # beschaedigter Zeiger
    assert w.run(C.uebergabe_erfolgt(w.db, doc_b)) is False, fall


@pytest.mark.parametrize("fall", ["fest_fremd", "fest_leer", "termin_mit_fremdem_vorgang",
                                  "protokoll_am_fremden_termin"])
def test_e4_erster_sucher_bleibt_uebergeben(welt, fall):
    """Echte Uebergabe weiter erkannt: Vertrag A (dessen Vorgang/Termin/
    Protokoll die Abholung traegt) gilt als uebergeben."""
    C = _module("routes.contracts")
    w = welt
    ids = _zwei_sucher(w, fall)
    doc_a = {"id": ids["k_a"], "dealer_id": w.dealer_id, "vehicle_id": ids["vid"],
             "kaufvorgang_id": ids["kv_a"]}
    assert w.run(C.uebergabe_erfolgt(w.db, doc_a)) is True, fall


@pytest.mark.parametrize("nachweis", ["eigener_termin", "eigener_vorgang_abgeholt",
                                      "eigenes_protokoll"])
def test_e4_doppel_abholung_eigener_nachweis_zaehlt(welt, nachweis):
    """Doppel-Abholung bleibt erlaubt: Hat B selbst einen Nachweis (eigener
    Termin abgeholt, eigener Vorgang abgeholt, eigenes finales Protokoll),
    gilt B als uebergeben, auch wenn A am Fahrzeug festgehalten ist."""
    C = _module("routes.contracts")
    w = welt
    s = w.s
    ids = _zwei_sucher(w, "fest_fremd")
    if nachweis == "eigener_termin":
        w.run(w.db.appointments.insert_one({"id": f"t_b_{s}", "dealer_id": w.dealer_id,
                                            "vehicle_id": ids["vid"], "contract_id": ids["k_b"],
                                            "status": "erledigt"}))
    elif nachweis == "eigener_vorgang_abgeholt":
        w.run(w.db.kaufvorgaenge.update_one({"id": ids["kv_b"]},
                                            {"$set": {"status": "abgeholt"}}))
    else:
        w.run(w.db.pickup_protocols.insert_one({"id": f"p_b_{s}", "dealer_id": w.dealer_id,
                                                "vehicle_id": ids["vid"],
                                                "contract_id": ids["k_b"], "status": "final"}))
    doc_b = {"id": ids["k_b"], "dealer_id": w.dealer_id, "vehicle_id": ids["vid"],
             "kaufvorgang_id": ids["kv_b"]}
    assert w.run(C.uebergabe_erfolgt(w.db, doc_b)) is True, nachweis


def test_e4_fahrzeugtermin_ohne_zuordnung_zaehlt_ohne_fremde_abholung(welt):
    """Gegenstueck: Termin nur am Fahrzeug (kein Vertrag, kein Vorgang),
    abgeholt, der eigene Vorgang ist offen und kein anderer Vorgang am Auto
    ist abgeholt -> weiter uebergeben (empfang3 bleibt erhalten)."""
    C = _module("routes.contracts")
    w = welt
    s = w.s
    vid = f"v_frei_{s}"
    _fahrzeug_roh(w, vid, lifecycle="abholung_geplant")
    w.run(w.db.kaufvorgaenge.insert_many([
        {"id": f"kv_b_{s}", "dealer_id": w.dealer_id, "vehicle_id": vid,
         "contract_id": f"k_b_{s}", "status": "abholung_geplant"},
        {"id": f"kv_a_{s}", "dealer_id": w.dealer_id, "vehicle_id": vid,
         "contract_id": f"k_a_{s}", "status": "storniert"}]))
    w.run(w.db.appointments.insert_one({"id": f"t_{s}", "dealer_id": w.dealer_id,
                                        "vehicle_id": vid, "status": "abgeholt"}))
    doc = {"id": f"k_b_{s}", "dealer_id": w.dealer_id, "vehicle_id": vid,
           "kaufvorgang_id": f"kv_b_{s}"}
    assert w.run(C.uebergabe_erfolgt(w.db, doc)) is True


# =============================================================== (2) Weiterverkauf
@pytest.mark.parametrize("lc", ["verkauft", "bestand", "archiviert"])
def test_e4_weiterverkauf_vor_abholung_ist_keine_uebergabe(welt, lc):
    """Weiterverkauf ab vertrag_erstellt: Auto verkauft/Bestand/archiviert,
    der eigene Vorgang wartet noch auf die Abholung, kein Termin abgeholt ->
    NICHT uebergeben. Vorher True (Lebenszyklus allein reichte)."""
    C = _module("routes.contracts")
    w = welt
    s = w.s
    vid = f"v_{lc}_{s}"
    _fahrzeug_roh(w, vid, lifecycle=lc)
    w.run(w.db.kaufvorgaenge.insert_one({"id": f"kv_{s}", "dealer_id": w.dealer_id,
                                         "vehicle_id": vid, "contract_id": f"k_{s}",
                                         "status": "abholung_geplant"}))
    w.run(w.db.appointments.insert_one({"id": f"t_{s}", "dealer_id": w.dealer_id,
                                        "vehicle_id": vid, "contract_id": f"k_{s}",
                                        "status": "geplant"}))
    doc = {"id": f"k_{s}", "dealer_id": w.dealer_id, "vehicle_id": vid,
           "kaufvorgang_id": f"kv_{s}"}
    assert w.run(C.uebergabe_erfolgt(w.db, doc)) is False, lc


@pytest.mark.parametrize("nachweis", ["eigener_vorgang_fest", "fahrzeugtermin_abgeholt",
                                      "eigener_vorgang_abgeholt"])
def test_e4_verkauft_nach_abholung_bleibt_uebergeben(welt, nachweis):
    """Echte Uebergabe weiter erkannt: verkauft MIT eigenem festgehaltenem
    Abhol-Vorgang, mit abgeholtem Fahrzeugtermin oder mit abgeholtem
    eigenem Vorgang -> uebergeben."""
    C = _module("routes.contracts")
    w = welt
    s = w.s
    vid = f"v_vk_{s}"
    _fahrzeug_roh(w, vid, lifecycle="verkauft",
                  **({"abgeholt_kaufvorgang_id": f"kv_{s}"}
                     if nachweis == "eigener_vorgang_fest" else {}))
    w.run(w.db.kaufvorgaenge.insert_one({
        "id": f"kv_{s}", "dealer_id": w.dealer_id, "vehicle_id": vid, "contract_id": f"k_{s}",
        "status": "abgeholt" if nachweis == "eigener_vorgang_abgeholt" else "abholung_geplant"}))
    if nachweis == "fahrzeugtermin_abgeholt":
        w.run(w.db.appointments.insert_one({"id": f"t_{s}", "dealer_id": w.dealer_id,
                                            "vehicle_id": vid, "status": "abgeholt"}))
    doc = {"id": f"k_{s}", "dealer_id": w.dealer_id, "vehicle_id": vid,
           "kaufvorgang_id": f"kv_{s}"}
    assert w.run(C.uebergabe_erfolgt(w.db, doc)) is True, nachweis


# =============================================================== Migration 21
def test_e4_migration_21_leert_vertrag_des_zweiten_suchers(welt):
    """Migration 21 profitiert: A (abgeholt) behaelt das Kreuz, B am selben
    Auto (offen) und ein vor der Abholung weiterverkauftes Auto werden
    geleert. Vorher: geleert=0, nach_uebergabe=3."""
    M = _module("migrationen")
    w = welt
    s = w.s
    ids = _zwei_sucher(w, "fest_fremd")
    _roh(w, ids["k_a"], vehicle_id=ids["vid"], kaufvorgang_id=ids["kv_a"])
    _roh(w, ids["k_b"], vehicle_id=ids["vid"], kaufvorgang_id=ids["kv_b"])
    _fahrzeug_roh(w, f"v_vk_{s}", lifecycle="verkauft")
    w.run(w.db.kaufvorgaenge.insert_one({"id": f"kv_vk_{s}", "dealer_id": w.dealer_id,
                                         "vehicle_id": f"v_vk_{s}", "contract_id": f"k_vk_{s}",
                                         "status": "abholung_geplant"}))
    _roh(w, f"k_vk_{s}", vehicle_id=f"v_vk_{s}", kaufvorgang_id=f"kv_vk_{s}")

    z = w.run(M.m21_empfang_kaestchen_leeren(w.db))
    assert z["geleert"] == 2 and z["nach_uebergabe"] == 1, z
    assert sorted(z["vertraege"]) == sorted([ids["k_b"], f"k_vk_{s}"]), z
    a = w.run(w.db.generated_pdfs.find_one({"id": ids["k_a"]}, {"_id": 0}))
    assert a["contract_data"]["empfang_schluessel"] is True
    assert "empfang_kaestchen_geleert" not in a
    b = w.run(w.db.generated_pdfs.find_one({"id": ids["k_b"]}, {"_id": 0}))
    assert b["contract_data"]["empfang_schluessel"] is False
    assert b["empfang_kaestchen_geleert"]["quelle"] == "migration_21"


def test_e4_migration_21_liest_kaufvorgang_id(welt):
    """Verkauftes Auto, festgehalten ist der Vorgang, auf den NUR der Vertrag
    zeigt (Vorgang ohne contract_id, Altbestand): nur mit kaufvorgang_id in
    der Projektion erkennt die Migration den eigenen Vorgang."""
    M = _module("migrationen")
    w = welt
    s = w.s
    _fahrzeug_roh(w, f"v_alt_{s}", lifecycle="verkauft", abgeholt_kaufvorgang_id=f"kv_alt_{s}")
    w.run(w.db.kaufvorgaenge.insert_one({"id": f"kv_alt_{s}", "dealer_id": w.dealer_id,
                                         "vehicle_id": f"v_alt_{s}", "status": "abgeholt"}))
    _roh(w, f"k_alt_{s}", vehicle_id=f"v_alt_{s}", kaufvorgang_id=f"kv_alt_{s}")
    z = w.run(M.m21_empfang_kaestchen_leeren(w.db))
    assert z["geleert"] == 0 and z["nach_uebergabe"] == 1, z


# =============================================================== neue Fassung
def test_e4_neue_fassung_des_zweiten_suchers_leert_das_kreuz(welt):
    """Ende zu Ende: Sucher A hat das Auto abgeholt (Fahrzeug abgeholt,
    Vorgang festgehalten). Vertrag B am selben Auto bekommt eine neue
    Fassung (Termin verschoben) — das automatische Kreuz faellt weg.
    Vorher: uebernommen, weil das Fahrzeug als abgeholt galt."""
    C = _module("routes.contracts")
    w = welt
    s = w.s
    doc = _altvertrag(w, "zweit")
    vid = doc["vehicle_id"]
    w.run(w.db.kaufvorgaenge.insert_one({"id": f"kv_a_{s}", "dealer_id": w.dealer_id,
                                         "vehicle_id": vid, "contract_id": f"k_a_{s}",
                                         "user_id": "sucher_a", "status": "abgeholt"}))
    w.run(w.db.vehicles.update_one({"id": vid}, {"$set": {
        "lifecycle": "abgeholt", "abgeholt_kaufvorgang_id": f"kv_a_{s}"}}))
    assert w.run(C.regenerate_contract_for_pickup(
        contract_id=doc["id"], dealer_id=w.dealer_id, user=w.chef, pickup_date="2026-10-02"))
    neu = w.run(w.db.generated_pdfs.find_one({"id": doc["id"]}, {"_id": 0}))
    assert neu["contract_data"]["empfang_schluessel"] is False
    assert neu["empfang_kaestchen_geleert"]["felder"] == ["empfang_schluessel"]


# =============================================================== Skript
def test_e4_skript_zeigt_zweiten_sucher_und_weiterverkauf(welt):
    """empfang_kreuz_liste.py profitiert: Vertrag B am Auto von A und ein vor
    der Abholung verkauftes Auto stehen in der Standardliste (offen), A nur
    mit --mit-uebergabe. Vorher fehlten B und das verkaufte Auto."""
    L = _module("empfang_kreuz_liste")
    w = welt
    s = w.s
    mit = _pdf(True)
    ids = _zwei_sucher(w, "fest_fremd")
    _roh(w, ids["k_a"], vehicle_id=ids["vid"], kaufvorgang_id=ids["kv_a"], pdf_b64=mit)
    _roh(w, ids["k_b"], vehicle_id=ids["vid"], kaufvorgang_id=ids["kv_b"], pdf_b64=mit)
    _fahrzeug_roh(w, f"v_vk_{s}", lifecycle="verkauft")
    w.run(w.db.kaufvorgaenge.insert_one({"id": f"kv_vk_{s}", "dealer_id": w.dealer_id,
                                         "vehicle_id": f"v_vk_{s}", "contract_id": f"k_vk_{s}",
                                         "status": "abholung_geplant"}))
    _roh(w, f"k_vk_{s}", vehicle_id=f"v_vk_{s}", kaufvorgang_id=f"kv_vk_{s}", pdf_b64=mit)

    e = w.run(L.liste(w.db, dealer_id=w.dealer_id))
    assert e["mit_kreuz"] == 3 and e["nach_uebergabe"] == 1, e
    zeilen = {r["id"]: r for r in e["vertraege"]}
    assert sorted(zeilen) == sorted([ids["k_b"], f"k_vk_{s}"]), zeilen
    assert zeilen[ids["k_b"]]["vorgang"] == "vertrag_erstellt"
    assert zeilen[f"k_vk_{s}"]["vorgang"] == "abholung_geplant"
    alle = w.run(L.liste(w.db, dealer_id=w.dealer_id, mit_uebergabe=True))
    uebergabe = {r["id"]: r["uebergabe"] for r in alle["vertraege"]}
    assert uebergabe == {ids["k_a"]: True, ids["k_b"]: False, f"k_vk_{s}": False}, uebergabe
