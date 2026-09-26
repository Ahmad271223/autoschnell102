# -*- coding: utf-8 -*-
"""Rollenpruefung 22.09.2026, Welle 4 — Review-Befunde fuer das Team "vertrag".

Ohne Server: In-Prozess-Aufrufe gegen eine Wegwerf-Datenbank (Fixture
``welt`` aus test_rp_vertrag_welle2_20260922, wird am Ende geloescht).

  Review (hoch)  Eine Korrektur-Version (v2) des Abholprotokolls setzte die
                 vor Ort korrigierten Ja/Nein-Angaben (unfallfrei, gewerblich),
                 den Kilometerstand und bestaetigte Textkorrekturen im neuen
                 Kaufvertrag auf den Stand VOR der Abholung zurueck.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_rp_vertrag_welle2_20260922 import (  # noqa: E402,F401  (welt = Fixture)
    _fahrzeug, _modul, _vertrag, welt)


def _protokoll(pid, *, unfallfrei, gewerblich, farbe, km, preis=None):
    return {"id": pid, "status": "final", "neuer_preis": preis, "sondervereinbarung": "",
            "vehicle_check": {"accident_free": {"status": unfallfrei},
                              "commercial": {"status": gewerblich},
                              "color": farbe},
            "condition": {"mileage": km}, "new_damages": []}


def test_korrektur_version_behaelt_die_vor_ort_korrekturen(welt):
    """Weg wie in protocols.finalize: protokoll_korrekturen (gegen die
    AKTUELLE Fassung) -> vertrag_nach_abholung_aktualisieren -> contracts."""
    P = _modul("routes.protocols")
    w = welt
    cid, vid = f"cw4_{w.s}", f"vw4_{w.s}"
    w.run(w.db.vehicles.insert_one(_fahrzeug(w, vid, w.a)))
    vertrag = _vertrag(w, cid, w.a, vehicle_id=vid)
    vertrag["contract_data"].update({"accident_free": "Ja", "commercial_since_ez": "Nein",
                                     "vehicle_mileage": 100000, "vehicle_color": "Blau"})
    w.run(w.db.generated_pdfs.insert_one(vertrag))
    appt = {"id": f"t_{w.s}", "dealer_id": w.dealer_id, "contract_id": cid,
            "vehicle_id": vid, "created_by": w.chef["id"]}

    def abschliessen(protokoll):
        korrekturen, schaeden = w.run(P.protokoll_korrekturen(appt, protokoll))
        ok = w.run(P.vertrag_nach_abholung_aktualisieren(
            appt, protokoll["id"], protokoll["neuer_preis"], "",
            korrekturen=korrekturen, neue_schaeden=schaeden))
        return ok, korrekturen, w.run(w.db.generated_pdfs.find_one({"id": cid}))

    # Version 1: Unfall, gewerblich, 120.000 km, rot statt blau, Preis 9.000
    v1 = _protokoll("p1", unfallfrei="Nein", gewerblich="Ja",
                    farbe={"status": "weicht ab", "value": "Rot"}, km="120000", preis=9000.0)
    ok, k1, doc = abschliessen(v1)
    assert ok and k1 == {"accident_free": "Nein", "commercial_since_ez": "Ja",
                         "vehicle_mileage": 120000, "vehicle_color": "Rot"}
    cd = doc["contract_data"]
    assert (cd["accident_free"], cd["commercial_since_ez"], cd["vehicle_mileage"],
            cd["vehicle_color"], cd["purchase_price"]) == ("Nein", "Ja", 120000, "Rot", 9000.0)

    # Version 2 (Korrektur): Kopie von v1, Preis zurueck auf den Vertragspreis,
    # die Farbe jetzt "stimmt" (die App zeigt schon "Rot" laut Vertrag).
    # Gegen die aktuelle Fassung weicht nichts mehr ab:
    v2 = _protokoll("p2", unfallfrei="Nein", gewerblich="Ja",
                    farbe={"status": "stimmt"}, km="120000")
    ok, k2, doc = abschliessen(v2)
    assert ok and k2 == {}
    cd = doc["contract_data"]
    assert cd["purchase_price"] == 10000 and doc["purchase_price"] == 10000.0, \
        "der Preis geht auf den Stand vor der Abholung zurueck (RP-479)"
    assert cd["accident_free"] == "Nein", "vorher: wieder 'Ja' — gegen das Protokoll"
    assert cd["commercial_since_ez"] == "Ja"
    assert cd["vehicle_mileage"] == 120000
    assert cd["vehicle_color"] == "Rot"
    assert doc["nach_abholung_protokoll_id"] == "p2"
    assert doc["nach_abholung_versand_offen"] is True
    ae = doc["nach_abholung_aenderungen"]
    assert ae["nach_protokoll_korrektur"] is True and ae["preis"] is None
    assert ae["felder"] == ["accident_free", "commercial_since_ez", "vehicle_color",
                            "vehicle_mileage"], "gegenueber dem Vertrag vor der Abholung"
    assert doc["vertrag_vor_abholung"]["accident_free"] == "Ja", "Basis bleibt unberuehrt"

    # Version 3: der Fahrer nimmt "Unfall" zurueck -> Korrektur auf "Ja"
    v3 = _protokoll("p3", unfallfrei="Ja", gewerblich="Ja",
                    farbe={"status": "stimmt"}, km="120000")
    ok, k3, doc = abschliessen(v3)
    assert ok and k3 == {"accident_free": "Ja"}
    cd = doc["contract_data"]
    assert (cd["accident_free"], cd["commercial_since_ez"], cd["vehicle_mileage"],
            cd["vehicle_color"]) == ("Ja", "Ja", 120000, "Rot")
    assert doc["nach_abholung_aenderungen"]["felder"] == [
        "commercial_since_ez", "vehicle_color", "vehicle_mileage"]
    assert w.run(w.db.betriebsalarme.count_documents({})) == 0


def test_erste_abholung_unveraendert(welt):
    """Ohne Stand vor der Abholung (erste Neuerzeugung) gilt wie bisher die
    aktuelle Fassung; felder = die tatsaechlich korrigierten Angaben."""
    C = _modul("routes.contracts")
    w = welt
    cid, vid = f"cw4e_{w.s}", f"vw4e_{w.s}"
    w.run(w.db.vehicles.insert_one(_fahrzeug(w, vid, w.a)))
    vertrag = _vertrag(w, cid, w.a, vehicle_id=vid)
    vertrag["contract_data"]["accident_free"] = "Ja"
    w.run(w.db.generated_pdfs.insert_one(vertrag))
    assert w.run(C.regenerate_contract_for_pickup(
        contract_id=cid, dealer_id=w.dealer_id, user=w.chef, grund="abholung_abgeschlossen",
        korrekturen={"accident_free": "Nein", "vehicle_make": "BMW"},
        protokoll_id="p1")) is True
    doc = w.run(w.db.generated_pdfs.find_one({"id": cid}))
    assert doc["contract_data"]["accident_free"] == "Nein"
    assert doc["nach_abholung_aenderungen"]["felder"] == ["accident_free"], \
        "gleiche Werte (Marke BMW) aendern nichts"
    assert doc["vertrag_vor_abholung"]["accident_free"] == "Ja"
