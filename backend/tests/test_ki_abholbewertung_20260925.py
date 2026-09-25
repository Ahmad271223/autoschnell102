# -*- coding: utf-8 -*-
"""KI-Abholbewertung (Wunsch Ahmad 25.09.2026, Umbau 26.09.2026) — ohne echten
KI-Aufruf: der Provider wird durch eine Attrappe ersetzt. Geprueft werden
Paketbau (Abgleich bekannter Schaeden, Verschlechterung, Ausstattung
fehlt/defekt, Unterlagen nur wenn vereinbart, negative km), Ueberspringen
ohne Abweichung, Absicherung der vier Geldwerte, Ablage/Veralten/Lease,
Kostenbremse, Chef-Routen und der Lernfall bei der Freigabe."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_befunde_runde17_termine import _jetzt, _module, welt  # noqa: E402,F401

ANTWORT = {
    "items": [
        {"source_id": "d1", "category": "damage", "title": "Delle Kotflügel vorne rechts",
         "price_relevant": True, "repair_method": "Ausbeulen ohne Lackieren", "repair_estimate_eur": 180,
         "minimum_justified_eur": 200, "fair_discount_eur": 250, "best_realistic_eur": 700,
         "negotiation_start_eur": 100, "manual_review_required": False, "reason": "Kleine Delle ohne Lackschaden."},
        {"source_id": "dev:keys", "category": "keys", "title": "1 Schlüssel fehlt",
         "price_relevant": True, "repair_method": "Ersatz + Anlernen", "repair_estimate_eur": 250,
         "minimum_justified_eur": 240, "fair_discount_eur": 280, "best_realistic_eur": 320,
         "negotiation_start_eur": 360, "manual_review_required": False, "reason": "BMW-Funkschlüssel."},
        {"source_id": "dev:accident_free", "category": "accident_history", "title": "Unfallfreiheit weicht ab",
         "price_relevant": True, "repair_method": "", "repair_estimate_eur": 0,
         "minimum_justified_eur": 500, "fair_discount_eur": 800, "best_realistic_eur": 1000,
         "negotiation_start_eur": 1200, "manual_review_required": True, "reason": "Manuelle Entscheidung."},
    ],
    "combined": {"sum_fair_eur": 530, "overlap_adjustment_eur": 0, "minimum_justified_eur": 440,
                 "fair_discount_eur": 530, "best_realistic_eur": 600, "negotiation_start_eur": 2000,
                 "deal_risk": "normal", "manual_review_required": False},
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
                "modell": "attrappe", "usage": {"input_tokens": 1000, "output_tokens": 500}}
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
           "features": {"Rückfahrkamera": False, "Sitzheizung": True, "Navigationssystem": "defekt"},
           "documents": {"Bedienungsanleitung": False, "Servicebuch / Scheckheft": False},
           "new_damages": [
               {"id": "d1", "type_key": "delle", "type_label": "Delle", "zone": "Kotflügel vorne rechts",
                "view": "right", "severity_data": {"groesse": "2–5 cm", "lack": "nein", "lage": "Fläche"}},
               {"id": "k1", "type_key": "kratzer", "type_label": "Kratzer", "zone": "Stoßfänger hinten",
                "view": "rear", "severity_data": {"laenge": "5–15 cm", "tiefe": "oberflächlich", "anzahl": "einzeln"}}],
           "preis_vorschlag": 7300, "notes": "Kamera ohne Bild", "created_at": _jetzt()}
    doc.update(extra)
    return doc


def _welt_aufbauen(welt, suffix, **contract_extra):
    w, db = welt.w, welt.db
    cid, tid, vid, pid = f"c_ki{suffix}_{w.s}", f"t_ki{suffix}_{w.s}", f"v_ki{suffix}_{w.s}", f"p_ki{suffix}_{w.s}"

    async def lauf():
        await db.vehicles.insert_one(w.fahrzeug(vid, data={
            "make_label": "BMW", "model_label": "530 Gran Turismo", "mileage": 206000,
            "first_registration": "01/2010", "power_kw": 180, "fuel_label": "Diesel", "price": 8900,
            "known_defects": ["Panoramadach-Rollo lose"]}))
        cd = {"seller_name": "Vera Verkauf", "purchase_price": 8100, "accident_free": "Ja",
              "previous_owners": 2, "schluessel_anzahl": "2", "service_book": "ja",
              "damages": [{"id": "k1", "type_key": "kratzer", "type_label": "Kratzer",
                           "zone": "Stoßfänger hinten", "view": "rear",
                           "severity_data": {"laenge": "5–15 cm", "tiefe": "oberflächlich"}}]}
        cd.update(contract_extra)
        await db.generated_pdfs.insert_one(w.vertrag(cid, contract_data=cd))
        await db.appointments.insert_one(w.appt(tid, vehicle_id=vid, contract_id=cid))
        await db.pickup_protocols.insert_one(_protokoll(w, pid, tid, vid))
    welt.run(lauf())
    return cid, tid, vid, pid


def _aufraeumen(welt):
    db, w = welt.db, welt.w
    welt.run(db.ki_bewertungen.delete_many({"dealer_id": w.dealer_id}))
    welt.run(db.ki_lernfaelle.delete_many({"dealer_id": w.dealer_id}))


def test_01_paket_ohne_verkaeuferdaten_mit_abgleich_und_referenzen(welt, monkeypatch):
    K = _attrappe(monkeypatch)
    _cid, _tid, _vid, pid = _welt_aufbauen(welt, "1")
    w = welt.w
    grund = welt.run(K._grundlagen(pid, w.dealer_id))
    paket = K.paket_bauen(*grund)
    text = str(paket)
    assert "Vera" not in text and "0170" not in text and "Teststr" not in text
    assert paket["prices"] == {"contract_price_eur": 8100.0, "listing_price_eur": 8900.0, "driver_proposal_eur": 7300.0}
    fz = paket["vehicle"]
    assert fz["make"] == "BMW" and fz["power_ps"] == 245 and fz["age_years"] >= 16 and fz["mileage_pickup_km"] == 208400
    neu = {d["id"]: d for d in paket["new_damages"]}
    assert neu["d1"]["already_known"] is False and neu["d1"]["repair_reference"]["key"] == "delle_klein"
    assert neu["d1"]["repair_reference"]["assumption_made"] is False
    assert neu["k1"]["already_known"] is True, "gleicher Schaden, gleiche Auspraegung = bekannt"
    typen = {d["id"]: d["type"] for d in paket["deviations"]}
    assert typen["dev:keys"] == "keys" and typen["dev:mileage_contract"] == "mileage"
    assert typen["dev:feature:Rückfahrkamera"] == "equipment_missing"
    assert typen["dev:feature:Navigationssystem"] == "equipment_defect", "defekt != fehlt"
    assert typen["dev:accident_free"] == "accident_history"
    docs = {d["id"]: d for d in paket["deviations"] if d["type"] == "documents"}
    assert docs["dev:doc:Bedienungsanleitung"]["agreed"] is False
    assert docs["dev:doc:Servicebuch / Scheckheft"]["agreed"] is True, "Scheckheft war vereinbart"
    km = next(d for d in paket["deviations"] if d["id"] == "dev:mileage_contract")
    assert km["difference_km"] == 2400 and km["repair_reference"]["key"] == "km"
    assert paket["precomputed"]["repair_reference_total_eur"] > 0
    assert K.relevant(paket)
    _aufraeumen(welt)


def test_02_verschlechterung_und_negative_km(welt, monkeypatch):
    K = _attrappe(monkeypatch)
    _cid, _tid, _vid, pid = _welt_aufbauen(welt, "2")
    w, db = welt.w, welt.db
    # Kratzer am Stossfaenger jetzt tief und laenger, Kilometer 30.000 weniger
    welt.run(db.pickup_protocols.update_one({"id": pid}, {"$set": {
        "new_damages": [{"id": "k1", "type_key": "kratzer", "type_label": "Kratzer", "zone": "Stoßfänger hinten",
                         "severity_data": {"laenge": "15–30 cm", "tiefe": "bis Blech", "anzahl": "einzeln"}},
                        {"id": "d9", "type_key": "kratzer", "type_label": "Kratzer", "zone": "Stoßfänger vorne",
                         "severity_data": {"laenge": "bis 5 cm", "tiefe": "oberflächlich", "anzahl": "einzeln"}}],
        "condition": {"mileage": "176000", "warning_lights": "nein"}}}))
    grund = welt.run(K._grundlagen(pid, w.dealer_id))
    paket = K.paket_bauen(*grund)
    neu = {d["id"]: d for d in paket["new_damages"]}
    assert neu["k1"]["already_known"] is True and neu["k1"].get("worse") is True
    assert neu["d9"]["possibly_known"] is True and neu["d9"]["already_known"] is False, "gleiche Art, aehnliches Bauteil"
    worse = next(d for d in paket["deviations"] if d["type"] == "damage_worse")
    assert worse["expected"]["tiefe"] == "oberflächlich" and worse["actual"]["tiefe"] == "bis Blech"
    km = next(d for d in paket["deviations"] if d["id"] == "dev:mileage_contract")
    assert km["type"] == "other" and km["manual_hint"] is True and km["difference_km"] == -30000
    _aufraeumen(welt)


def test_03_bewertung_wird_abgelegt_bereinigt_und_priorisiert(welt, monkeypatch):
    aufrufe = []
    K = _attrappe(monkeypatch, zaehler=aufrufe)
    _cid, _tid, _vid, pid = _welt_aufbauen(welt, "3")
    w, db = welt.w, welt.db
    erg = welt.run(K.bewertung_ausfuehren(pid, w.dealer_id))
    assert erg["status"] == "ok" and erg["protocol_revision"] == 3 and erg["kaufpreis"] == 8100.0
    items = {i["source_id"]: i for i in erg["ergebnis"]["items"]}
    d1 = items["d1"]
    # Reihenfolge erzwungen: min <= fair <= best <= start, best <= 1,6 x fair, start <= 1,4 x best
    assert d1["minimum_justified_eur"] == 200 and d1["fair_discount_eur"] == 250
    assert d1["best_realistic_eur"] == 400 and d1["negotiation_start_eur"] == 400
    assert items["dev:accident_free"]["manual_review_required"] and items["dev:accident_free"]["priority"] == "rot"
    assert items["dev:accident_free"]["fair_discount_eur"] == 0
    assert erg["ergebnis"]["items"][0]["source_id"] == "dev:accident_free", "rot zuerst"
    comb = erg["ergebnis"]["combined"]
    assert comb["fair_discount_eur"] == 530.0 and comb["negotiation_start_eur"] <= round(600 * 1.4)
    assert comb["recommended_purchase_price_eur"] == 8100 - 530
    assert comb["manual_review_required"] is True and comb["deal_risk"] == "normal"
    assert erg["ergebnis"]["datenlage"] in ("hoch", "mittel", "niedrig")
    assert "needs_information" not in erg["ergebnis"]
    assert erg["kosten_ct"] is not None and erg["kosten_ct"] < 5
    assert len(aufrufe) == 1 and "driver_answers" in aufrufe[0] and "precomputed" in aufrufe[0]
    # zweiter Aufruf mit gleichem Stand: kein neuer KI-Aufruf
    erg2 = welt.run(K.bewertung_ausfuehren(pid, w.dealer_id))
    assert erg2["input_hash"] == erg["input_hash"] and len(aufrufe) == 1
    gespeichert = welt.run(db.ki_bewertungen.find_one({"protocol_id": pid}, {"_id": 0}))
    assert gespeichert["status"] == "ok" and gespeichert["prompt_version"] == "abholung_v3"
    assert "Vera" not in str(gespeichert.get("eingabe")) and "lease_until" not in gespeichert
    _aufraeumen(welt)


def test_04_ohne_abweichung_kein_aufruf(welt, monkeypatch):
    aufrufe = []
    K = _attrappe(monkeypatch, zaehler=aufrufe)
    _cid, _tid, _vid, pid = _welt_aufbauen(welt, "4")
    w, db = welt.w, welt.db
    welt.run(db.pickup_protocols.update_one({"id": pid}, {"$set": {
        "vehicle_check": {}, "condition": {"mileage": "206500"}, "keys_count": "2",
        "features": {"Sitzheizung": True}, "documents": {}, "new_damages": [
            {"id": "k1", "type_key": "kratzer", "type_label": "Kratzer", "zone": "Stoßfänger hinten",
             "severity_data": {"laenge": "5–15 cm", "tiefe": "oberflächlich"}}]}}))
    erg = welt.run(K.bewertung_ausfuehren(pid, w.dealer_id))
    assert erg["status"] == "keine" and not aufrufe
    gelesen = welt.run(K.bewertung_lesen(pid, w.dealer_id))
    assert gelesen["status"] == "keine"
    _aufraeumen(welt)


def test_05_veraltet_lease_und_fehler_blockiert_nichts(welt, monkeypatch):
    K = _attrappe(monkeypatch)
    _cid, _tid, _vid, pid = _welt_aufbauen(welt, "5")
    w, db = welt.w, welt.db
    erg = welt.run(K.bewertung_ausfuehren(pid, w.dealer_id))
    assert erg["status"] == "ok"
    # Fahrer traegt nach -> anderer Hash -> alte Bewertung gilt als veraltet
    welt.run(db.pickup_protocols.update_one({"id": pid}, {"$set": {"keys_count": "2", "revision": 4}}))
    monkeypatch.setattr(K, "bewertung_anstossen", lambda *a, **k: None)
    gelesen = welt.run(K.bewertung_lesen(pid, w.dealer_id))
    assert gelesen["status"] == "veraltet" and gelesen["ergebnis"] is not None
    # haengender Lauf: "laeuft" mit abgelaufenem Lease gilt als abgestuerzt -> neu
    grund = welt.run(K._grundlagen(pid, w.dealer_id))
    h = K.eingabe_hash(K.paket_bauen(*grund))
    welt.run(db.ki_bewertungen.update_one({"protocol_id": pid, "input_hash": erg["input_hash"]},
                                          {"$set": {"status": "laeuft", "lease_until": "2020-01-01T00:00:00+00:00",
                                                    "input_hash": h}}))
    gelesen = welt.run(K.bewertung_lesen(pid, w.dealer_id))
    assert gelesen["status"] == "laeuft"
    erg3 = welt.run(K.bewertung_ausfuehren(pid, w.dealer_id))
    assert erg3["status"] == "ok", "abgelaufenes Lease wird neu gerechnet"
    # KI faellt aus: Ergebnis mit Status, kein Wurf
    _attrappe(monkeypatch, status="zeitlimit")
    erg4 = welt.run(K.bewertung_ausfuehren(pid, w.dealer_id, erzwingen=True))
    assert erg4["status"] == "zeitlimit" and erg4["ergebnis"] is None
    _aufraeumen(welt)


def test_06_routen_nur_chef_und_lernfall_bei_freigabe(welt, monkeypatch):
    from fastapi import HTTPException
    K = _attrappe(monkeypatch)
    P = _module("routes.protocols")
    monkeypatch.setattr(P, "_pflichtfelder_pruefen", lambda *a, **k: None)
    _cid, _tid, _vid, pid = _welt_aufbauen(welt, "6")
    w, db = welt.w, welt.db
    erg = welt.run(P.protokoll_ki_bewertung_neu(pid, user=w.chef))
    assert erg["status"] == "ok"
    erg = welt.run(P.protokoll_ki_bewertung(pid, user=w.chef))
    assert erg["status"] == "ok"
    with pytest.raises(HTTPException) as ex:
        welt.run(P.protokoll_ki_bewertung(f"gibtsnicht_{w.s}", user=w.chef))
    assert ex.value.status_code == 404
    liste = welt.run(P.protokolle_zur_freigabe(user=w.chef))
    eintrag = next(e for e in liste if e["protocol_id"] == pid)
    assert eintrag["ki_bewertung"]["status"] == "ok" and eintrag["ki_bewertung"]["fairer_nachlass"] == 530.0
    assert eintrag["ki_bewertung"]["datenlage"] in ("hoch", "mittel", "niedrig")
    stand = welt.run(db.pickup_protocols.find_one({"id": pid}, {"_id": 0, "freigabe_stand": 1, "updated_at": 1}))
    body = P.FreigabeIn(neuer_preis=7600, stand=stand.get("freigabe_stand") or stand.get("updated_at"))
    welt.run(P.protokoll_freigeben(pid, body, user=w.chef))
    lern = welt.run(db.ki_lernfaelle.find_one({"protocol_id": pid}, {"_id": 0}))
    assert lern and lern["chef_preis"] == 7600 and lern["tatsaechlicher_nachlass"] == 500.0
    assert lern["ki_nachlass"] == 530.0 and lern["fahrer_vorschlag"] == 7300.0 and lern["art"] == "abholung"
    assert "Vera" not in str(lern)
    _aufraeumen(welt)


def test_07_schema_prioritaet_und_ausstattung_tristate():
    S = _module("ai.schemas")
    PB = _module("ai.preisbasis")
    assert S.ANTWORT_SCHEMA["additionalProperties"] is False
    assert set(S.ANTWORT_SCHEMA["required"]) == {"items", "combined", "arguments"}
    assert "needs_information" not in S.ANTWORT_SCHEMA["properties"]
    assert PB.prioritaet("accident_history") == "rot"
    assert PB.prioritaet("keys", betrag=280, kaufpreis=8100) == "orange"
    assert PB.prioritaet("mileage", betrag=80, kaufpreis=8100) == "gelb"
    assert PB.prioritaet("damage", betrag=600, kaufpreis=8100) == "rot"
    assert "Ausgangswerte AutoSchnell" in PB.basis_als_text()
    # Zuordnung aus dem Formular: unbekannt -> vorsichtige Annahme
    z, annahme = PB.zuordnen("delle", {"groesse": "2–5 cm", "lack": "nein"})
    assert z["schluessel"] == "delle_klein" and annahme is False
    z, annahme = PB.zuordnen("delle", {"groesse": "2–5 cm", "lack": "unbekannt"})
    assert z["schluessel"] == "delle_lack" and annahme is True
    z, _ = PB.zuordnen("rost", {"umfang": "Blasen", "groesse": "5–15 cm"}, zone="Tür hinten links")
    assert z["schluessel"] == "rost_blasen_mittel"
    z, _ = PB.zuordnen("rost", {"umfang": "Blasen"}, zone="Schweller links")
    assert z["schluessel"] == "rost_tragend" and z["manuell"]
    z, _ = PB.zuordnen("hagelschaden", {"umfang": "Viele (10-30)", "lack": "nein"})
    assert z["schluessel"] == "hagel_viele_bauteil"
    z, _ = PB.zuordnen("beleuchtung", {"welches": "Scheinwerfer", "funktion": "komplett ausgefallen", "technik": "LED"})
    assert z["schluessel"] == "licht_led"
    z, _ = PB.zuordnen("steinschlag", {"wo": "Windschutzscheibe", "umfang": "Riss"})
    assert z["schluessel"] == "steinschlag_scheibe_tausch"
    # DamageIn / ProtocolIn
    C = _module("routes.contracts")
    d = C.DamageIn(type_key="delle", zone="Tür", severity_data={"groesse": "2-5 cm", "lack": "nein"})
    assert d.severity_data == {"groesse": "2-5 cm", "lack": "nein"}
    with pytest.raises(ValueError):
        C.DamageIn(type_key="delle", severity_data={str(i): "x" for i in range(9)})
    P = _module("routes.protocols")
    assert P.ProtocolIn(features={"Navi": "Defekt", "ABS": True}).features == {"Navi": "defekt", "ABS": True}
    with pytest.raises(ValueError):
        P.ProtocolIn(features={"Navi": "kaputt"})


def test_08_budget_bremst(welt, monkeypatch):
    K = _attrappe(monkeypatch)
    B = _module("ai.budget")
    _cid, _tid, _vid, pid = _welt_aufbauen(welt, "8")
    w, db = welt.w, welt.db
    monkeypatch.setenv("KI_BUDGET_MONAT_EUR", "0.01")
    # ein teurer Lauf dieser Firma in diesem Monat -> Budget voll
    welt.run(db.ki_bewertungen.insert_one({"id": f"alt_{w.s}", "art": "abholung", "dealer_id": w.dealer_id,
                                          "protocol_id": f"p_alt_{w.s}", "input_hash": "x", "status": "ok",
                                          "created_at": _jetzt(), "kosten_ct": 2.0}))
    bud = welt.run(B.pruefen(user_id=None, dealer_id=w.dealer_id, art="abholung"))
    assert bud["erlaubt"] is False and "Monatsbudget" in bud["grund"]
    erg = welt.run(K.bewertung_ausfuehren(pid, w.dealer_id))
    assert erg["status"] == "budget" and erg["ergebnis"] is None
    monkeypatch.setenv("KI_BUDGET_MONAT_EUR", "15")
    monkeypatch.setenv("KI_KOSTEN_MAX_CT", "1")
    bud = welt.run(B.pruefen(user_id=None, dealer_id=w.dealer_id, art="abholung"))
    assert bud["erlaubt"] is True and bud["sparmodus"] is True, "letzter Lauf ueber der Einzelgrenze -> Sparmodus"
    _aufraeumen(welt)
