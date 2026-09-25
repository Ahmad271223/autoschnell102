# -*- coding: utf-8 -*-
"""KI-Abholbewertung (Wunsch Ahmad 25.09.2026) — ohne echten KI-Aufruf:
der Provider wird durch eine Attrappe ersetzt. Geprueft werden Paketbau,
Ueberspringen ohne Abweichung, Absicherung der Antwort, Ablage/Veralten,
Chef-Routen und der Lernfall bei der Freigabe."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_befunde_runde17_termine import _jetzt, _module, welt  # noqa: E402,F401

ANTWORT = {
    "items": [
        {"source_id": "d1", "category": "damage", "title": "Delle Kotflügel vorne rechts",
         "price_relevant": True, "priority": "gelb", "repair_method": "Ausbeulen ohne Lackieren",
         "repair_estimate_eur": 180, "recommended_discount_eur": 250, "discount_min_eur": 100,
         "discount_max_eur": 600, "confidence": 0.88, "manual_review_required": False,
         "reason": "Kleine Delle ohne Lackschaden."},
        {"source_id": "dev:keys", "category": "keys", "title": "1 Schlüssel fehlt",
         "price_relevant": True, "priority": "gelb", "repair_method": "Ersatz + Anlernen",
         "repair_estimate_eur": 250, "recommended_discount_eur": 280, "discount_min_eur": 240,
         "discount_max_eur": 320, "confidence": 0.9, "manual_review_required": False,
         "reason": "BMW-Funkschlüssel."},
        {"source_id": "dev:accident_free", "category": "accident_history", "title": "Unfallfreiheit weicht ab",
         "price_relevant": True, "priority": "gelb", "repair_method": "", "repair_estimate_eur": 0,
         "recommended_discount_eur": 800, "discount_min_eur": 100, "discount_max_eur": 3000,
         "confidence": 0.5, "manual_review_required": True, "reason": "Manuelle Entscheidung."},
    ],
    "combined": {"sum_of_items_eur": 530, "overlap_adjustment_eur": 0, "recommended_discount_eur": 530,
                 "discount_min_eur": 100, "discount_max_eur": 2000, "negotiation_start_eur": 2000,
                 "confidence": 0.8, "manual_review_required": False},
    "needs_information": [{"source_id": "d1", "question": "Ist der Lack beschädigt?",
                           "options": ["Ja", "Nein", "Unklar"]}],
    "arguments": ["Der Kotflügel vorne rechts hat eine nicht dokumentierte Delle.",
                  "Es ist nur ein statt zwei Schlüssel vorhanden."],
}


def _attrappe(monkeypatch, antwort=ANTWORT, status="ok", zaehler=None):
    K = _module("ai.pickup_assessment")

    async def _bewerten(**kw):
        if zaehler is not None:
            zaehler.append(kw["nutzer"])
        return {"status": status, "grund": "" if status == "ok" else "Attrappe",
                "daten": antwort if status == "ok" else None, "dauer_ms": 5,
                "modell": "attrappe", "usage": {"input_tokens": 1}}
    monkeypatch.setattr(K, "json_bewerten", _bewerten)
    monkeypatch.setattr(K, "ki_aktiv", lambda: True)
    # Stufe 5: nie eine echte Websuche im Test (Attrappe "aus")
    MD = _module("ai.marktdaten")

    async def _keine_recherche(**kw):
        return {"status": "aus", "grund": "Test", "text": "", "quellen": [], "suchen": 0, "dauer_ms": 0, "usage": {}}
    monkeypatch.setattr(MD, "recherche", _keine_recherche)
    return K


def _protokoll(w, pid, tid, vid, **extra):
    doc = {"id": pid, "appointment_id": tid, "dealer_id": w.dealer_id, "vehicle_id": vid,
           "driver_account_id": w.driver_id, "driver_name": w.driver["display_name"],
           "version": 1, "revision": 3, "status": "zur_freigabe", "superseded": False,
           # Ja/Nein-Felder: die Antwort steht im Status (Vertrag sagt Ja)
           "vehicle_check": {"accident_free": {"status": "Nein"}},
           "condition": {"mileage": "208400", "warning_lights": "nein"},
           "keys_count": "1", "keys_expected": "2",
           "features": {"Rückfahrkamera": False, "Sitzheizung": True},
           "documents": {"Bedienungsanleitung": False},
           "new_damages": [
               {"id": "d1", "type_key": "delle", "type_label": "Delle", "zone": "Kotflügel vorne rechts",
                "view": "right", "severity_data": {"groesse": "2-5 cm", "lack": "nein"}},
               {"id": "k1", "type_key": "kratzer", "type_label": "Kratzer", "zone": "Stoßfänger hinten",
                "view": "rear"}],
           "preis_vorschlag": 7300, "notes": "Kamera ohne Bild", "created_at": _jetzt()}
    doc.update(extra)
    return doc


def _welt_aufbauen(welt, suffix):
    w, db = welt.w, welt.db
    cid, tid, vid, pid = f"c_ki{suffix}_{w.s}", f"t_ki{suffix}_{w.s}", f"v_ki{suffix}_{w.s}", f"p_ki{suffix}_{w.s}"

    async def lauf():
        await db.vehicles.insert_one(w.fahrzeug(vid, data={
            "make_label": "BMW", "model_label": "530 Gran Turismo", "mileage": 206000,
            "first_registration": "01/2010", "power_kw": 180, "fuel_label": "Diesel", "price": 8900,
            "known_defects": ["Panoramadach-Rollo lose"]}))
        await db.generated_pdfs.insert_one(w.vertrag(cid, contract_data={
            "seller_name": "Vera Verkauf", "purchase_price": 8100, "accident_free": "Ja",
            "previous_owners": 2, "schluessel_anzahl": "2",
            "damages": [{"id": "k1", "type_key": "kratzer", "type_label": "Kratzer",
                         "zone": "Stoßfänger hinten", "view": "rear"}]}))
        await db.appointments.insert_one(w.appt(tid, vehicle_id=vid, contract_id=cid))
        await db.pickup_protocols.insert_one(_protokoll(w, pid, tid, vid))
    welt.run(lauf())
    return cid, tid, vid, pid


def test_01_paket_ohne_verkaeuferdaten_und_ohne_bekannte_schaeden(welt, monkeypatch):
    K = _attrappe(monkeypatch)
    _cid, _tid, _vid, pid = _welt_aufbauen(welt, "1")
    w = welt.w
    grund = welt.run(K._grundlagen(pid, w.dealer_id))
    paket = K.paket_bauen(*grund)
    text = str(paket)
    assert "Vera" not in text and "0170" not in text and "Teststr" not in text
    assert paket["prices"] == {"contract_price_eur": 8100.0, "listing_price_eur": 8900.0, "driver_proposal_eur": 7300.0}
    neu = {d["id"]: d for d in paket["new_damages"]}
    assert neu["d1"]["already_in_contract"] is False and neu["d1"]["severity_data"] == {"groesse": "2-5 cm", "lack": "nein"}
    assert neu["k1"]["already_in_contract"] is True, "bekannter Schaden zaehlt nicht als neu"
    typen = {d["id"]: d["type"] for d in paket["deviations"]}
    assert typen["dev:keys"] == "keys" and typen["dev:mileage_contract"] == "mileage"
    assert typen["dev:feature:Rückfahrkamera"] == "equipment_missing"
    assert typen["dev:accident_free"] == "accident_history"
    assert typen["dev:doc:Bedienungsanleitung"] == "documents"
    km = next(d for d in paket["deviations"] if d["id"] == "dev:mileage_contract")
    assert km["difference_km"] == 2400
    assert K.relevant(paket)


def test_02_bewertung_wird_abgelegt_bereinigt_und_priorisiert(welt, monkeypatch):
    aufrufe = []
    K = _attrappe(monkeypatch, zaehler=aufrufe)
    _cid, _tid, _vid, pid = _welt_aufbauen(welt, "2")
    w, db = welt.w, welt.db
    erg = welt.run(K.bewertung_ausfuehren(pid, w.dealer_id))
    assert erg["status"] == "ok" and erg["protocol_revision"] == 3 and erg["kaufpreis"] == 8100.0
    items = {i["source_id"]: i for i in erg["ergebnis"]["items"]}
    # enge Spanne: 100-600 um 250 -> 200-300
    assert (items["d1"]["discount_min_eur"], items["d1"]["discount_max_eur"]) == (200.0, 300.0)
    # Unfallfreiheit: manuell, rot, keine Zahl
    assert items["dev:accident_free"]["manual_review_required"] and items["dev:accident_free"]["priority"] == "rot"
    assert items["dev:accident_free"]["recommended_discount_eur"] == 0
    assert erg["ergebnis"]["items"][0]["source_id"] == "dev:accident_free", "rot zuerst"
    comb = erg["ergebnis"]["combined"]
    assert comb["recommended_discount_eur"] == 530.0 and comb["negotiation_start_eur"] <= round(530 * 1.35)
    assert comb["recommended_purchase_price_eur"] == 8100 - 530
    assert comb["manual_review_required"] is True
    assert erg["ergebnis"]["needs_information"][0]["question"] == "Ist der Lack beschädigt?"
    assert len(aufrufe) == 1
    # zweiter Aufruf mit gleichem Stand: kein neuer KI-Aufruf
    erg2 = welt.run(K.bewertung_ausfuehren(pid, w.dealer_id))
    assert erg2["input_hash"] == erg["input_hash"] and len(aufrufe) == 1
    gespeichert = welt.run(db.ki_bewertungen.find_one({"protocol_id": pid}, {"_id": 0}))
    assert gespeichert["status"] == "ok" and gespeichert["prompt_version"] == "abholung_v2"
    assert "Vera" not in str(gespeichert.get("eingabe"))


def test_03_ohne_abweichung_kein_aufruf(welt, monkeypatch):
    aufrufe = []
    K = _attrappe(monkeypatch, zaehler=aufrufe)
    _cid, _tid, _vid, pid = _welt_aufbauen(welt, "3")
    w, db = welt.w, welt.db
    welt.run(db.pickup_protocols.update_one({"id": pid}, {"$set": {
        "vehicle_check": {}, "condition": {"mileage": "206500"}, "keys_count": "2",
        "features": {"Sitzheizung": True}, "documents": {}, "new_damages": [
            {"id": "k1", "type_key": "kratzer", "type_label": "Kratzer", "zone": "Stoßfänger hinten"}]}}))
    erg = welt.run(K.bewertung_ausfuehren(pid, w.dealer_id))
    assert erg["status"] == "keine" and not aufrufe
    gelesen = welt.run(K.bewertung_lesen(pid, w.dealer_id))
    assert gelesen["status"] == "keine"


def test_04_veraltet_nach_aenderung_und_fehler_blockiert_nichts(welt, monkeypatch):
    K = _attrappe(monkeypatch)
    _cid, _tid, _vid, pid = _welt_aufbauen(welt, "4")
    w, db = welt.w, welt.db
    erg = welt.run(K.bewertung_ausfuehren(pid, w.dealer_id))
    assert erg["status"] == "ok"
    # Fahrer traegt nach -> anderer Hash -> alte Bewertung gilt als veraltet
    welt.run(db.pickup_protocols.update_one({"id": pid}, {"$set": {"keys_count": "2", "revision": 4}}))
    monkeypatch.setattr(K, "bewertung_anstossen", lambda *a, **k: None)
    gelesen = welt.run(K.bewertung_lesen(pid, w.dealer_id))
    assert gelesen["status"] == "veraltet" and gelesen["ergebnis"] is not None
    # KI faellt aus: Ergebnis mit Status, kein Wurf
    _attrappe(monkeypatch, status="zeitlimit")
    erg2 = welt.run(K.bewertung_ausfuehren(pid, w.dealer_id, erzwingen=True))
    assert erg2["status"] == "zeitlimit" and erg2["ergebnis"] is None


def test_05_routen_nur_chef_und_lernfall_bei_freigabe(welt, monkeypatch):
    from fastapi import HTTPException
    K = _attrappe(monkeypatch)
    P = _module("routes.protocols")
    # Vollstaendigkeit des Protokolls prueft die Freigabe selbst ausfuehrlich
    # (andere Tests) — hier zaehlt nur der Lernfall.
    monkeypatch.setattr(P, "_pflichtfelder_pruefen", lambda *a, **k: None)
    _cid, _tid, _vid, pid = _welt_aufbauen(welt, "5")
    w, db = welt.w, welt.db
    erg = welt.run(P.protokoll_ki_bewertung_neu(pid, user=w.chef))
    assert erg["status"] == "ok"
    erg = welt.run(P.protokoll_ki_bewertung(pid, user=w.chef))
    assert erg["status"] == "ok"
    with pytest.raises(HTTPException) as ex:
        welt.run(P.protokoll_ki_bewertung(f"gibtsnicht_{w.s}", user=w.chef))
    assert ex.value.status_code == 404
    # Liste traegt die Kurzform
    liste = welt.run(P.protokolle_zur_freigabe(user=w.chef))
    eintrag = next(e for e in liste if e["protocol_id"] == pid)
    assert eintrag["ki_bewertung"]["status"] == "ok"
    assert eintrag["ki_bewertung"]["empfohlener_nachlass"] == 530.0
    # Freigabe mit Preis -> Lernfall
    stand = welt.run(db.pickup_protocols.find_one({"id": pid}, {"_id": 0, "freigabe_stand": 1, "updated_at": 1}))
    body = P.FreigabeIn(neuer_preis=7600, stand=stand.get("freigabe_stand") or stand.get("updated_at"))
    welt.run(P.protokoll_freigeben(pid, body, user=w.chef))
    lern = welt.run(db.ki_lernfaelle.find_one({"protocol_id": pid}, {"_id": 0}))
    assert lern and lern["chef_preis"] == 7600 and lern["chef_nachlass"] == 500.0
    assert lern["ki_nachlass"] == 530.0 and lern["fahrer_vorschlag"] == 7300.0
    assert "Vera" not in str(lern)


def test_06_schema_und_prioritaet_deterministisch():
    S = _module("ai.schemas")
    PB = _module("ai.preisbasis")
    assert S.ANTWORT_SCHEMA["additionalProperties"] is False
    assert set(S.ANTWORT_SCHEMA["required"]) == {"items", "combined", "needs_information", "arguments"}
    assert PB.prioritaet("accident_history") == "rot"
    assert PB.prioritaet("keys", betrag=280, kaufpreis=8100) == "orange"
    assert PB.prioritaet("mileage", betrag=80, kaufpreis=8100) == "gelb"
    assert PB.prioritaet("damage", betrag=600, kaufpreis=8100) == "rot"
    assert "Ausgangswerte AutoSchnell" in PB.basis_als_text()
    # DamageIn nimmt severity_data an (hoechstens 8 Merkmale)
    C = _module("routes.contracts")
    d = C.DamageIn(type_key="delle", zone="Tür", severity_data={"groesse": "2-5 cm", "lack": "nein"})
    assert d.severity_data == {"groesse": "2-5 cm", "lack": "nein"}
    with pytest.raises(ValueError):
        C.DamageIn(type_key="delle", severity_data={str(i): "x" for i in range(9)})
