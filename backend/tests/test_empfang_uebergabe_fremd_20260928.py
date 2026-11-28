# -*- coding: utf-8 -*-
"""Nachtrag 28.09.2026 (rest5) zu uebergabe_erfolgt (b1e894d, empfang4).

Pruefung Runde 4, zwei Restloecher:

(P1/P2) Die Fahrzeug-Pruefung schaute bei fremden Abholungen nur auf
    Kaufvorgaenge mit status "abgeholt" und auf abgeholt_kaufvorgang_id.
    Stand das Auto auf lifecycle "abgeholt" mit LEEREM
    abgeholt_kaufvorgang_id (Altbestand vor Runde 18, oder Vorgang von A
    zurueckgesetzt) und trug A die Abholung nur ueber einen Termin
    (contract_id bzw. kaufvorgang_id von A, abgeholt/erledigt) oder ein
    finales Protokoll (contract_id von A oder an einem Termin von A), galt
    Vertrag B mit offenem Vorgang als uebergeben. Jetzt: fremde Abholung am
    Fahrzeug -> False, bevor Lebenszyklus/Zeiger zaehlen.
(P4) Ein kaufvorgang_id-Zeiger auf einen Vorgang OHNE contract_id zaehlte
    als eigener Vorgang, auch an einem ANDEREN Fahrzeug. Jetzt wie
    kaufvorgang._passt: der Vorgang muss am Fahrzeug des Vertrags stehen.

Laeuft gegen eine eigene Wegwerf-Datenbank (Praefix DB_NAME).
"""
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

from test_empfang_altvertraege_20260928 import _module, welt  # noqa: E402,F401
from test_empfang_uebergabe_fahrzeug_20260928 import _fahrzeug_roh  # noqa: E402


def _auto_von_a(w, fall: str, lifecycle: str = "abgeholt") -> dict:
    """Auto steht auf `lifecycle`, abgeholt_kaufvorgang_id LEER. Vorgang von
    A (kv_a) ist NICHT abgeholt, Vorgang von B (kv_b) offen. Die Abholung von
    A steckt je nach Fall nur in einem Termin oder Protokoll."""
    s = w.s
    ids = {"vid": f"v_{fall}_{s}", "k_a": f"k_a_{fall}_{s}", "k_b": f"k_b_{fall}_{s}",
           "kv_a": f"kv_a_{fall}_{s}", "kv_b": f"kv_b_{fall}_{s}", "t_a": f"t_a_{fall}_{s}",
           "p_a": f"p_a_{fall}_{s}"}
    vid = ids["vid"]
    _fahrzeug_roh(w, vid, lifecycle=lifecycle)
    w.run(w.db.kaufvorgaenge.insert_many([
        {"id": ids["kv_a"], "dealer_id": w.dealer_id, "vehicle_id": vid,
         "contract_id": ids["k_a"], "user_id": "sucher_a", "status": "abholung_geplant"},
        {"id": ids["kv_b"], "dealer_id": w.dealer_id, "vehicle_id": vid,
         "contract_id": ids["k_b"], "user_id": "sucher_b", "status": "vertrag_erstellt"}]))
    termin = {"id": ids["t_a"], "dealer_id": w.dealer_id, "vehicle_id": vid}
    prot = {"id": ids["p_a"], "dealer_id": w.dealer_id, "vehicle_id": vid,
            "appointment_id": ids["t_a"], "status": "final"}
    if fall == "termin_vertrag_a":                     # P1
        w.run(w.db.appointments.insert_one({**termin, "contract_id": ids["k_a"],
                                            "status": "abgeholt"}))
    elif fall == "termin_vorgang_a":                   # P1, nur ueber den Vorgang
        w.run(w.db.appointments.insert_one({**termin, "kaufvorgang_id": ids["kv_a"],
                                            "status": "erledigt"}))
    elif fall == "protokoll_vertrag_a":                # P2
        w.run(w.db.appointments.insert_one({**termin, "contract_id": ids["k_a"],
                                            "status": "geplant"}))
        w.run(w.db.pickup_protocols.insert_one({**prot, "contract_id": ids["k_a"]}))
    elif fall == "protokoll_am_termin_a":              # P2, Protokoll ohne Vertrag
        w.run(w.db.appointments.insert_one({**termin, "contract_id": ids["k_a"],
                                            "status": "geplant"}))
        w.run(w.db.pickup_protocols.insert_one({**prot, "contract_id": None}))
    elif fall == "termin_a_storniert":                 # Gegenstueck: keine Abholung
        w.run(w.db.appointments.insert_one({**termin, "contract_id": ids["k_a"],
                                            "status": "storniert"}))
    elif fall == "protokoll_a_ersetzt":                # Gegenstueck: ersetztes Protokoll
        w.run(w.db.appointments.insert_one({**termin, "contract_id": ids["k_a"],
                                            "status": "geplant"}))
        w.run(w.db.pickup_protocols.insert_one({**prot, "contract_id": ids["k_a"],
                                                "superseded": True}))
    else:
        raise AssertionError(fall)
    return ids


def _doc(w, ids, wer: str) -> dict:
    return {"id": ids[f"k_{wer}"], "dealer_id": w.dealer_id, "vehicle_id": ids["vid"],
            "kaufvorgang_id": ids[f"kv_{wer}"]}


FREMDE_ABHOLUNG = ["termin_vertrag_a", "termin_vorgang_a", "protokoll_vertrag_a",
                   "protokoll_am_termin_a"]


# =============================================================== P1/P2
@pytest.mark.parametrize("fall", FREMDE_ABHOLUNG)
def test_r5_fremde_abholung_ohne_zeiger_nicht_uebergeben(welt, fall):
    """Auto abgeholt, abgeholt_kaufvorgang_id leer, A traegt die Abholung
    nur per Termin/Protokoll -> Vertrag B (Vorgang offen) NICHT uebergeben.
    Vorher True (Lebenszyklus 'abgeholt' zaehlte)."""
    C = _module("routes.contracts")
    w = welt
    ids = _auto_von_a(w, fall)
    assert w.run(C.uebergabe_erfolgt(w.db, _doc(w, ids, "b"))) is False, fall


@pytest.mark.parametrize("fall", FREMDE_ABHOLUNG)
def test_r5_fremde_abholung_ohne_zeiger_altvertrag_ohne_kaufvorgang_id(welt, fall):
    """Wie oben, Vertrag B ohne kaufvorgang_id-Zeiger (Altbestand)."""
    C = _module("routes.contracts")
    w = welt
    ids = _auto_von_a(w, fall)
    doc_b = _doc(w, ids, "b")
    doc_b.pop("kaufvorgang_id")
    assert w.run(C.uebergabe_erfolgt(w.db, doc_b)) is False, fall


@pytest.mark.parametrize("fall", ["termin_vertrag_a", "termin_vorgang_a",
                                  "protokoll_vertrag_a", "protokoll_am_termin_a"])
def test_r5_eigentuemer_der_abholung_bleibt_uebergeben(welt, fall):
    """Echte Uebergabe weiter erkannt: Vertrag A (dessen Termin/Protokoll die
    Abholung traegt) gilt als uebergeben."""
    C = _module("routes.contracts")
    w = welt
    ids = _auto_von_a(w, fall)
    assert w.run(C.uebergabe_erfolgt(w.db, _doc(w, ids, "a"))) is True, fall


@pytest.mark.parametrize("fall", ["termin_a_storniert", "protokoll_a_ersetzt"])
def test_r5_ohne_fremde_abholung_zaehlt_der_lebenszyklus_weiter(welt, fall):
    """Gegenstueck: Termin von A storniert bzw. Protokoll von A ersetzt ->
    keine fremde Abholung; der Lebenszyklus 'abgeholt' (Altbestand ohne
    Zeiger) zaehlt fuer B weiter wie bisher."""
    C = _module("routes.contracts")
    w = welt
    ids = _auto_von_a(w, fall)
    assert w.run(C.uebergabe_erfolgt(w.db, _doc(w, ids, "b"))) is True, fall


def test_r5_fremde_abholung_schlaegt_freien_fahrzeugtermin(welt):
    """Auto noch nicht auf 'abgeholt'. Am Fahrzeug steht ein abgeholter
    Termin von A UND ein abgeholter Termin ohne Zuordnung -> fuer B zaehlt
    der freie Termin nicht (wie beim abgeholten fremden Vorgang)."""
    C = _module("routes.contracts")
    w = welt
    ids = _auto_von_a(w, "termin_vertrag_a", lifecycle="abholung_geplant")
    w.run(w.db.appointments.insert_one({"id": f"t_frei_{w.s}", "dealer_id": w.dealer_id,
                                        "vehicle_id": ids["vid"], "status": "abgeholt"}))
    assert w.run(C.uebergabe_erfolgt(w.db, _doc(w, ids, "b"))) is False


def test_r5_eigener_termin_bleibt_trotz_fremder_abholung(welt):
    """Doppel-Abholung: B hat einen eigenen abgeholten Termin -> uebergeben,
    auch wenn A am Fahrzeug einen abgeholten Termin hat."""
    C = _module("routes.contracts")
    w = welt
    ids = _auto_von_a(w, "termin_vertrag_a")
    w.run(w.db.appointments.insert_one({"id": f"t_b_{w.s}", "dealer_id": w.dealer_id,
                                        "vehicle_id": ids["vid"], "contract_id": ids["k_b"],
                                        "status": "abgeholt"}))
    assert w.run(C.uebergabe_erfolgt(w.db, _doc(w, ids, "b"))) is True


# =============================================================== P4
def _zeiger_vorgang(w, am_fahrzeug: str) -> str:
    """Vorgang ohne contract_id, abgeholt, am Fahrzeug `am_fahrzeug`."""
    kv = f"kv_z_{am_fahrzeug}"
    w.run(w.db.kaufvorgaenge.insert_one({"id": kv, "dealer_id": w.dealer_id,
                                         "vehicle_id": am_fahrzeug, "status": "abgeholt"}))
    return kv


def test_r5_zeiger_auf_vorgang_an_anderem_fahrzeug_zaehlt_nicht(welt):
    """P4: Vertrag an Auto v1 zeigt auf einen abgeholten Vorgang OHNE
    contract_id an Auto v2 -> NICHT uebergeben (vorher True)."""
    C = _module("routes.contracts")
    w = welt
    s = w.s
    _fahrzeug_roh(w, f"v1_{s}", lifecycle="vertrag_erstellt")
    _fahrzeug_roh(w, f"v2_{s}", lifecycle="abgeholt")
    kv = _zeiger_vorgang(w, f"v2_{s}")
    doc = {"id": f"k_{s}", "dealer_id": w.dealer_id, "vehicle_id": f"v1_{s}",
           "kaufvorgang_id": kv}
    assert w.run(C.uebergabe_erfolgt(w.db, doc)) is False


def test_r5_zeiger_auf_vorgang_am_eigenen_fahrzeug_zaehlt(welt):
    """Gegenstueck P4: derselbe Vorgang am Auto des Vertrags -> uebergeben."""
    C = _module("routes.contracts")
    w = welt
    s = w.s
    _fahrzeug_roh(w, f"v1_{s}", lifecycle="vertrag_erstellt")
    kv = _zeiger_vorgang(w, f"v1_{s}")
    doc = {"id": f"k_{s}", "dealer_id": w.dealer_id, "vehicle_id": f"v1_{s}",
           "kaufvorgang_id": kv}
    assert w.run(C.uebergabe_erfolgt(w.db, doc)) is True


def test_r5_zeiger_vertrag_ohne_fahrzeug_zaehlt(welt):
    """Vertrag ohne vehicle_id: wie kaufvorgang._passt wird das Fahrzeug nicht
    verglichen -> der abgeholte Zeiger-Vorgang zaehlt weiter."""
    C = _module("routes.contracts")
    w = welt
    s = w.s
    kv = _zeiger_vorgang(w, f"v2_{s}")
    doc = {"id": f"k_{s}", "dealer_id": w.dealer_id, "kaufvorgang_id": kv}
    assert w.run(C.uebergabe_erfolgt(w.db, doc)) is True
