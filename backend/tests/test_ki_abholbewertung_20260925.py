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


def _erwartete_ids(paket):
    """Alle Positionen, die die KI bewerten muss (wie im Prompt gefordert)."""
    ids = [d["id"] for d in paket.get("new_damages") or [] if not d.get("already_known")]
    ids += [d["id"] for d in paket.get("damages") or []]
    ids += [a["id"] for a in paket.get("deviations") or []]
    return ids


def vollstaendig(antwort, paket):
    """Review 26.09.2026 (Nr. 6): das Backend verlangt GENAU eine Position je
    Eingabe. Die Attrappe ergaenzt fuer jede Eingabe-ID ohne Position eine
    neutrale (0 EUR, nicht preisrelevant) — so bleiben die Zahlen der
    festen Antwort unveraendert."""
    vorhanden = {str(i.get("source_id")) for i in antwort.get("items") or []}
    items = list(antwort.get("items") or [])
    for pid in _erwartete_ids(paket):
        if pid in vorhanden:
            continue
        items.append({"source_id": pid, "category": "other", "title": f"Position {pid}", "price_relevant": False,
                      "repair_method": "", "repair_estimate_eur": 0, "minimum_justified_eur": 0,
                      "fair_discount_eur": 0, "best_realistic_eur": 0, "negotiation_start_eur": 0,
                      "manual_review_required": False, "assessment_kind": "repair_estimate",
                      "diagnosis_cost_eur": 0, "scenario_low_eur": 0, "scenario_mid_eur": 0, "scenario_high_eur": 0,
                      "reason": "Ohne wirtschaftlichen Einfluss."})
    return {**antwort, "items": items}


def _attrappe(monkeypatch, antwort=ANTWORT, status="ok", zaehler=None, ergaenzen=True, pause_s=0.0):
    K = _module("ai.pickup_assessment")

    async def _bewerten(**kw):
        if zaehler is not None:
            zaehler.append(kw["nutzer"])
        if pause_s:
            import asyncio
            await asyncio.sleep(pause_s)
        daten = vollstaendig(antwort, kw["nutzer"]) if (ergaenzen and status == "ok") else antwort
        # Modell wie konfiguriert: der Zwischenspeicher (Nr. 4/5) vergleicht es
        return {"status": status, "grund": "" if status == "ok" else "Attrappe",
                "daten": daten if status == "ok" else None, "dauer_ms": 5,
                "modell": K.ki_modell(), "usage": {"input_tokens": 1000, "output_tokens": 500}}
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
        # KI je Konto freigeschaltet (25.09.2026 abends) — Chef und Sucher der Testfirma
        await db.users.update_many({"id": {"$in": [w.chef["id"], w.sucher["id"]]}}, {"$set": {"ki_aktiv": True}})
    welt.run(lauf())
    return cid, tid, vid, pid


def _aufraeumen(welt):
    db, w = welt.db, welt.w
    welt.run(db.ki_bewertungen.delete_many({"dealer_id": w.dealer_id}))
    welt.run(db.ki_lernfaelle.delete_many({"dealer_id": w.dealer_id}))
    welt.run(db.ki_budget.delete_many({"_id": {"$regex": f":{w.dealer_id}:"}}))


def hintergrund_abwarten(welt, K):
    """Review 26.09.2026 (Nr. 26): "Neu berechnen" rechnet im Hintergrund —
    die Tests warten die Aufgaben ab, bevor sie das Ergebnis lesen."""
    async def _warten():
        for t in list(K._laufende):
            await t
    welt.run(_warten())


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
                         "severity_data": {"laenge": "bis 5 cm", "tiefe": "oberflächlich", "anzahl": "einzeln"}},
                        {"id": "d10", "type_key": "kratzer", "type_label": "Kratzer", "zone": "Stoßstange",
                         "severity_data": {"laenge": "bis 5 cm", "tiefe": "oberflächlich", "anzahl": "einzeln"}}],
        "condition": {"mileage": "176000", "warning_lights": "nein"}}}))
    grund = welt.run(K._grundlagen(pid, w.dealer_id))
    paket = K.paket_bauen(*grund)
    neu = {d["id"]: d for d in paket["new_damages"]}
    assert neu["k1"]["already_known"] is True and neu["k1"].get("worse") is True
    # Review 26.09.2026 (Nr. 84): andere Position (vorne statt hinten) ist NICHT "moeglich", sondern neu;
    # "moeglich" nur bei gleichem Bauteil mit unklarer Position/Seite
    assert neu["d9"]["possibly_known"] is False and neu["d9"]["already_known"] is False, "andere Position = neu"
    assert neu["d10"]["possibly_known"] is True and neu["d10"]["already_known"] is False, "Bauteil gleich, Position unklar"
    worse = next(d for d in paket["deviations"] if d["type"] == "damage_worse")
    assert worse["expected"]["tiefe"] == "oberflächlich" and worse["actual"]["tiefe"] == "bis Blech"
    assert worse["worse_field"] in ("laenge", "tiefe")
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
    # Review 25.09.2026 abends: eine Fachpruefungs-Position (Unfallfreiheit) hebt das Risiko auf high
    assert comb["manual_review_required"] is True and comb["deal_risk"] == "high" and comb["expert_items"] == 1
    assert erg["ergebnis"]["datenlage"] in ("hoch", "mittel", "niedrig")
    assert "needs_information" not in erg["ergebnis"]
    assert erg["kosten_ct"] is not None and erg["kosten_ct"] < 5
    assert len(aufrufe) == 1 and "driver_answers" in aufrufe[0] and "precomputed" in aufrufe[0]
    # zweiter Aufruf mit gleichem Stand: kein neuer KI-Aufruf
    erg2 = welt.run(K.bewertung_ausfuehren(pid, w.dealer_id))
    assert erg2["input_hash"] == erg["input_hash"] and len(aufrufe) == 1
    gespeichert = welt.run(db.ki_bewertungen.find_one({"protocol_id": pid}, {"_id": 0}))
    assert gespeichert["status"] == "ok" and gespeichert["prompt_version"] == "abholung_v5"
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
    # Nr. 26: "Neu berechnen" antwortet sofort mit laeuft, rechnet im Hintergrund
    erg = welt.run(P.protokoll_ki_bewertung_neu(pid, user=w.chef))
    assert erg["status"] == "laeuft" and erg["protocol_id"] == pid
    hintergrund_abwarten(welt, K)
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
    # ein teurer Lauf dieser Firma in diesem Monat -> Budget voll. Review
    # 26.09.2026 (Nr. 22): pruefen() liest den Zaehler, der Abgleich (stuendlich)
    # bringt ihn auf den Stand der Bewertungen.
    welt.run(db.ki_bewertungen.insert_one({"id": f"alt_{w.s}", "art": "abholung", "dealer_id": w.dealer_id,
                                          "protocol_id": f"p_alt_{w.s}", "input_hash": "x", "status": "ok",
                                          "created_at": _jetzt(), "kosten_ct": 2.0}))
    welt.run(B.abgleichen(db))
    assert welt.run(B.zaehler_ct(user_id=None, dealer_id=w.dealer_id, art="abholung")) == 2.0
    bud = welt.run(B.pruefen(user_id=None, dealer_id=w.dealer_id, art="abholung"))
    assert bud["erlaubt"] is False and "Monatsbudget" in bud["grund"]
    erg = welt.run(K.bewertung_ausfuehren(pid, w.dealer_id))
    assert erg["status"] == "budget" and erg["ergebnis"] is None
    monkeypatch.setenv("KI_BUDGET_MONAT_EUR", "15")
    monkeypatch.setenv("KI_KOSTEN_MAX_CT", "1")
    bud = welt.run(B.pruefen(user_id=None, dealer_id=w.dealer_id, art="abholung"))
    assert bud["erlaubt"] is True and bud["sparmodus"] is True, "letzter Lauf ueber der Einzelgrenze -> Sparmodus"
    _aufraeumen(welt)


def test_09_fahrer_sieht_auswertung_ohne_kosten(welt, monkeypatch):
    """Wunsch Ahmad 25.09.2026 (abends): Fahrer-Route — vor dem Abschicken
    'keine' mit Grund, danach dasselbe Ergebnis wie beim Chef, aber ohne
    Kosten/Budget/Modell; fremder Termin 404."""
    from fastapi import HTTPException
    K = _attrappe(monkeypatch)
    P = _module("routes.protocols")
    _cid, tid, _vid, pid = _welt_aufbauen(welt, "9")
    w, db = welt.w, welt.db
    # Fahrer-Zugriff wie im Betrieb: in der Fahrerliste, zugeteilt UND angenommen (C22)
    link_neu = not welt.run(db.dealer_drivers.find_one({"dealer_id": w.dealer_id, "driver_account_id": w.driver_id}))
    if link_neu:
        welt.run(db.dealer_drivers.insert_one(w.link()))
    welt.run(db.appointments.update_one({"id": tid}, {"$set": {"driver_id": w.driver_id, "zuteilung": "angenommen"}}))
    welt.run(db.pickup_protocols.update_one({"id": pid}, {"$set": {"status": "entwurf"}}))
    erg = welt.run(P.fahrer_ki_bewertung(tid, driver=w.driver))
    assert erg["status"] == "keine" and "Abschicken" in erg["grund"]
    welt.run(db.pickup_protocols.update_one({"id": pid}, {"$set": {"status": "zur_freigabe"}}))
    welt.run(P.protokoll_ki_bewertung_neu(pid, user=w.chef))
    hintergrund_abwarten(welt, K)
    erg = welt.run(P.fahrer_ki_bewertung(tid, driver=w.driver))
    assert erg["status"] == "ok" and erg["preis_vorschlag"] == 7300
    assert erg["ergebnis"]["combined"]["fair_discount_eur"] == 530.0
    assert erg["kaufpreis"] == 8100
    for verboten in ("kosten_ct", "budget", "modell", "prompt_version"):
        assert verboten not in erg
    with pytest.raises(HTTPException) as ex:
        welt.run(P.fahrer_ki_bewertung(tid, driver={**w.driver, "id": "fremd_" + w.s}))
    assert ex.value.status_code == 404
    if link_neu:
        welt.run(db.dealer_drivers.delete_many({"dealer_id": w.dealer_id, "driver_account_id": w.driver_id}))
    _aufraeumen(welt)


def test_10_technik_drei_ergebnisarten_netto(welt, monkeypatch):
    """Schadenkatalog 25.09.2026 abends: Technik-Mangel ohne Skizze -> Referenz
    'Diagnose erforderlich' mit Szenarien (bestaetigte Diagnose -> Reparaturpreis);
    bereinigen leitet die vier Werte aus den Szenarien ab, Fachpruefung = 0;
    Datenlage sinkt von hoch auf mittel; Netto-Quellen werden brutto gelernt;
    Technik gilt als 'im Inserat genannt', wenn der Text den Bereich nennt."""
    PB = _module("ai.preisbasis")
    S = _module("ai.schemas")
    MD = _module("ai.marktdaten")
    DP = _module("ai.damage_pricing")
    sym = {"bereich": "Getriebe/Kupplung", "status": "nur Symptom bemerkt", "fahrbereit": "ja", "warnleuchte": "keine"}
    z, annahme = PB.zuordnen("technik", sym, "Getriebe/Kupplung")
    assert z["schluessel"] == "technik_getriebe" and annahme is False
    ref = PB.referenz("technik", sym, "Getriebe/Kupplung")
    assert ref["kind"] == "diagnosis_required" and ref["scenarios"] == {"low": 300, "mid": 1200, "high": 3500}
    assert ref["diagnosis"] == {"low": 80, "high": 150} and ref["median"] == 1200
    best = PB.referenz("technik", {**sym, "status": "Werkstatt hat Diagnose bestätigt"}, "Getriebe/Kupplung")
    assert best["kind"] == "repair_estimate" and "scenarios" not in best
    z2, annahme2 = PB.zuordnen("technik", {"bereich": "unbekannt", "status": "unbekannt"}, "")
    assert z2["schluessel"] == "technik_elektrik" and annahme2 is True
    assert PB.zuordnen("technik", {"bereich": "Klima/Heizung"}, "")[0]["schluessel"] == "technik_klima"
    # Warnleuchte ist keine manuelle Entscheidung mehr, sondern Diagnose + Szenarien
    K = _module("ai.kontext")
    wl = K.abweichungsreferenz("warning_light")
    assert wl["kind"] == "diagnosis_required" and wl["manual_review"] is False and wl["scenarios"]["high"] == 2500
    assert "diagnosis_required" in PB.basis_als_text() and "technical / Getriebe" in PB.basis_als_text()

    roh = {"items": [
        {"source_id": "t1", "category": "technical", "title": "Automatik ruckelt", "price_relevant": True,
         "repair_method": "Diagnose", "repair_estimate_eur": 0, "minimum_justified_eur": 0, "fair_discount_eur": 0,
         "best_realistic_eur": 0, "negotiation_start_eur": 0, "manual_review_required": False,
         "assessment_kind": "diagnosis_required", "diagnosis_cost_eur": 120, "scenario_low_eur": 3500,
         "scenario_mid_eur": 1200, "scenario_high_eur": 300, "reason": "Szenario."},
        {"source_id": "t2", "category": "damage", "title": "Delle", "price_relevant": True, "repair_method": "PDR",
         "repair_estimate_eur": 150, "minimum_justified_eur": 80, "fair_discount_eur": 130, "best_realistic_eur": 160,
         "negotiation_start_eur": 220, "manual_review_required": False, "assessment_kind": "repair_estimate",
         "diagnosis_cost_eur": 0, "scenario_low_eur": 0, "scenario_mid_eur": 0, "scenario_high_eur": 0, "reason": "ok"},
        {"source_id": "t3", "category": "accident_history", "title": "Unfall", "price_relevant": True,
         "repair_method": "", "repair_estimate_eur": 0, "minimum_justified_eur": 500, "fair_discount_eur": 800,
         "best_realistic_eur": 900, "negotiation_start_eur": 1000, "manual_review_required": False,
         "assessment_kind": "expert_check_required", "diagnosis_cost_eur": 0, "scenario_low_eur": 0,
         "scenario_mid_eur": 0, "scenario_high_eur": 0, "reason": "Fachpruefung."}],
        "combined": {"sum_fair_eur": 430, "overlap_adjustment_eur": 0, "minimum_justified_eur": 200,
                     "fair_discount_eur": 430, "best_realistic_eur": 1360, "negotiation_start_eur": 3720,
                     "deal_risk": "normal", "manual_review_required": False},
        "arguments": []}
    erg = S.bereinigen(roh, kaufpreis=8000)
    it = {i["source_id"]: i for i in erg["items"]}
    # Szenarien sortiert (die KI hatte sie verdreht), vier Werte daraus
    assert (it["t1"]["scenario_low_eur"], it["t1"]["scenario_mid_eur"], it["t1"]["scenario_high_eur"]) == (300.0, 1200.0, 3500.0)
    assert (it["t1"]["minimum_justified_eur"], it["t1"]["fair_discount_eur"],
            it["t1"]["best_realistic_eur"], it["t1"]["negotiation_start_eur"]) == (120.0, 300.0, 1200.0, 3500.0)
    assert it["t1"]["assessment_kind"] == "diagnosis_required" and it["t1"]["manual_review_required"] is False
    assert it["t3"]["manual_review_required"] is True and it["t3"]["fair_discount_eur"] == 0
    assert it["t2"]["assessment_kind"] == "repair_estimate" and it["t2"]["scenario_high_eur"] == 0
    c = erg["combined"]
    assert c["diagnosis_items"] == 1 and c["expert_items"] == 1 and c["uncertain_eur"] == 3200.0
    assert c["deal_risk"] == "high"            # aufwendiger Fall >= 25 % des Preises
    assert c["manual_review_required"] is True
    assert S.datenlage_anpassen(erg, "hoch") == "mittel" and S.datenlage_anpassen(erg, "niedrig") == "niedrig"
    assert S.datenlage_anpassen({"items": [it["t2"]]}, "hoch") == "hoch"
    # ohne assessment_kind (alte Antwort): manuell -> Fachpruefung, sonst Reparaturpreis
    alt = S.bereinigen({"items": [{**roh["items"][1], "assessment_kind": None},
                                  {**roh["items"][2], "assessment_kind": None, "manual_review_required": True}],
                        "combined": roh["combined"], "arguments": []}, kaufpreis=8000)
    assert [i["assessment_kind"] for i in alt["items"]] == ["repair_estimate", "expert_check_required"]

    zeilen = MD._daten_parsen("Text\n###DATEN\nt1|100|200|150|DEKRA Stundensatz netto|https://x\nt2|50|80|60|ADAC|https://y")
    assert zeilen[0]["min_eur"] == 119.0 and zeilen[0]["max_eur"] == 238.0 and zeilen[0]["typisch_eur"] == 178.5
    assert "brutto" in zeilen[0]["quelle"] and zeilen[1]["min_eur"] == 50.0
    assert MD._netto("DEKRA, netto") and not MD._netto("ADAC") and not MD._netto("DEKRA netto, auf brutto umgerechnet")
    assert "Technik" in MD.RECHERCHE_SYSTEM and "Bosch" in MD.QUELLEN_JE_GRUPPE

    tech = {"type_key": "technik", "zone": "Getriebe/Kupplung", "severity_data": {"bereich": "Getriebe/Kupplung"}}
    assert DP._moeglich_im_inserat(tech, "automatik ruckelt beim kaltstart, sonst top") is True
    assert DP._moeglich_im_inserat(tech, "unfallfrei, scheckheft, kleiner kratzer") is False
    assert DP._moeglich_im_inserat({**tech, "zone": "Klima/Heizung", "severity_data": {"bereich": "Klima/Heizung"}},
                                   "klimaanlage ohne funktion") is True


def test_11_ki_freischaltung_je_konto(welt, monkeypatch):
    """Wunsch Ahmad 25.09.2026 abends: KI je Konto freischalten wie das Abo.
    Ohne users.ki_aktiv: Status 'freischaltung' (Chef, Fahrer, Vertrag), kein
    Aufruf, nichts abgelegt; Admin schaltet frei -> naechster Aufruf rechnet."""
    from fastapi import HTTPException
    aufrufe = []
    K = _attrappe(monkeypatch, zaehler=aufrufe)
    P = _module("routes.protocols")
    A = _module("routes.admin")
    DP = _module("ai.damage_pricing")
    monkeypatch.setattr(DP, "json_bewerten", K.json_bewerten)     # nie die echte KI im Test
    monkeypatch.setattr(DP, "ki_aktiv", lambda: True)
    F = _module("ai.freischaltung")
    monkeypatch.setattr(P, "_pflichtfelder_pruefen", lambda *a, **k: None)
    _cid, tid, vid, pid = _welt_aufbauen(welt, "11")
    w, db = welt.w, welt.db
    SA = {"id": f"sa_ki_{w.s}", "role": "admin", "is_super_admin": True, "username": "sa", "dealer_id": ""}
    welt.run(db.users.update_one({"id": w.chef["id"]}, {"$unset": {"ki_aktiv": ""}}))
    welt.run(db.dealers.update_one({"id": w.dealer_id}, {"$set": {"user_id": w.chef["id"]}}))
    assert welt.run(F.firma_freigeschaltet(w.dealer_id)) is False
    assert welt.run(F.konto_freigeschaltet(w.chef["id"])) is False
    # Chef: gesperrt, KI nie gerufen, nichts in ki_bewertungen
    erg = welt.run(P.protokoll_ki_bewertung(pid, user=w.chef))
    assert erg["status"] == "freischaltung" and "nicht freigeschaltet" in erg["grund"]
    erg = welt.run(P.protokoll_ki_bewertung_neu(pid, user=w.chef))
    assert erg["status"] == "freischaltung"
    assert aufrufe == []
    assert welt.run(db.ki_bewertungen.find_one({"protocol_id": pid})) is None
    # Fahrer sieht denselben Grund
    link_neu = not welt.run(db.dealer_drivers.find_one({"dealer_id": w.dealer_id, "driver_account_id": w.driver_id}))
    if link_neu:
        welt.run(db.dealer_drivers.insert_one(w.link()))
    welt.run(db.appointments.update_one({"id": tid}, {"$set": {"driver_id": w.driver_id, "zuteilung": "angenommen"}}))
    erg = welt.run(P.fahrer_ki_bewertung(tid, driver=w.driver))
    assert erg["status"] == "freischaltung"
    # Vertrag: eigenes Konto entscheidet
    vehicle_doc = welt.run(db.vehicles.find_one({"id": vid}, {"_id": 0}))
    dmg = [{"id": "d1", "type_key": "delle", "type_label": "Delle", "zone": "Tür vorne links",
            "severity_data": {"groesse": "2–5 cm", "lack": "nein", "lage": "Fläche"}}]
    erg = welt.run(DP.bewerten(user=w.chef, vehicle_doc=vehicle_doc, damages=dmg, kaufpreis=8000.0, warten=True))
    assert erg["status"] == "freischaltung" and erg.get("vorschau")
    # Admin schaltet frei (nur Super-Admin; deaktiviertes Konto nicht)
    with pytest.raises(HTTPException) as ex:
        welt.run(A.admin_set_sucher_ki(f"gibtsnicht_{w.s}", A.KiFreischaltenIn(aktiv=True), admin=SA))
    assert ex.value.status_code == 404
    r = welt.run(A.admin_set_sucher_ki(w.chef["id"], A.KiFreischaltenIn(aktiv=True, grund="Test"), admin=SA))
    assert r == {"ok": True, "ki_aktiv": True}
    assert welt.run(F.firma_freigeschaltet(w.dealer_id)) is True
    zeile = next(s for s in welt.run(A.admin_list_dealer_sucher(w.dealer_id, _Antwort(), _=SA)) if s["id"] == w.chef["id"])
    assert zeile["ki_aktiv"] is True
    erg = welt.run(P.protokoll_ki_bewertung_neu(pid, user=w.chef))
    assert erg["status"] == "laeuft"
    hintergrund_abwarten(welt, K)
    erg = welt.run(P.protokoll_ki_bewertung(pid, user=w.chef))
    assert erg["status"] == "ok"
    erg = welt.run(DP.bewerten(user=w.chef, vehicle_doc=vehicle_doc, damages=dmg, kaufpreis=8000.0, warten=True))
    assert erg["status"] == "ok" and len(aufrufe) == 2
    # sperren -> wieder gesperrt; das alte Ergebnis bleibt lesbar (Hash passt), neue Laeufe nicht
    welt.run(A.admin_set_sucher_ki(w.chef["id"], A.KiFreischaltenIn(aktiv=False), admin=SA))
    erg = welt.run(P.protokoll_ki_bewertung(pid, user=w.chef))
    assert erg["status"] == "ok"                      # bereits berechnet, passt zum Stand
    welt.run(db.ki_bewertungen.delete_many({"protocol_id": pid}))
    erg = welt.run(P.protokoll_ki_bewertung(pid, user=w.chef))
    assert erg["status"] == "freischaltung"
    log_eintrag = welt.run(db.activity_logs.find_one({"action": "admin.sucher.ki.freigeschaltet", "ref": w.chef["id"]}))
    assert log_eintrag is not None
    if link_neu:
        welt.run(db.dealer_drivers.delete_many({"dealer_id": w.dealer_id, "driver_account_id": w.driver_id}))
    welt.run(db.users.update_one({"id": w.chef["id"]}, {"$unset": {"ki_aktiv": ""}}))
    welt.run(db.activity_logs.delete_many({"ref": w.chef["id"], "action": {"$regex": "^admin.sucher.ki"}}))
    _aufraeumen(welt)


class _Antwort:
    """Minimaler Response-Ersatz fuer admin_list_dealer_sucher (Kopfzeilen)."""
    def __init__(self):
        self.headers = {}


def test_12_haertung_reservierung_dedupe_risiko_kva(welt, monkeypatch):
    """Review 25.09.2026 abends: Budget atomar reservieren, Vertrag dedupliziert,
    Deal-Risk-Stufen fest, bestaetigte Diagnose mit Kostenvoranschlag/Umfang,
    Quellenqualitaet vor dem Lernen."""
    B = _module("ai.budget")
    S = _module("ai.schemas")
    PB = _module("ai.preisbasis")
    MD = _module("ai.marktdaten")
    DP = _module("ai.damage_pricing")
    w, db = welt.w, welt.db
    # --- Budget: 45 ct Grenze, 15 ct je Lauf -> drei Reservierungen passen, die vierte nicht
    monkeypatch.setenv("KI_BUDGET_MONAT_EUR", "0.45")
    monkeypatch.setenv("KI_KOSTEN_MAX_CT", "15")
    welt.run(db.ki_budget.delete_many({"_id": {"$regex": f":{w.dealer_id}:"}}))
    r1 = welt.run(B.reservieren(user_id=None, dealer_id=w.dealer_id, art="abholung"))
    r2 = welt.run(B.reservieren(user_id=None, dealer_id=w.dealer_id, art="abholung"))
    r3 = welt.run(B.reservieren(user_id=None, dealer_id=w.dealer_id, art="abholung"))
    assert r1 and r2 and r3 and r1["est_ct"] == 15.0
    assert welt.run(B.reservieren(user_id=None, dealer_id=w.dealer_id, art="abholung")) is None
    assert welt.run(B.zaehler_ct(user_id=None, dealer_id=w.dealer_id, art="abholung")) == 45.0
    welt.run(B.abrechnen(r1, 11.4))                                 # echte Kosten statt Reservierung
    assert welt.run(B.zaehler_ct(user_id=None, dealer_id=w.dealer_id, art="abholung")) == 41.4
    welt.run(B.abrechnen(r2, 0))                                    # freigeben
    assert welt.run(B.reservieren(user_id=None, dealer_id=w.dealer_id, art="abholung")) is not None
    assert welt.run(B.reservieren(user_id=None, dealer_id=w.dealer_id, art="abholung")) is None
    welt.run(B.abrechnen(None, 5))                                  # ohne Reservierung: nichts
    monkeypatch.setenv("KI_BUDGET_MONAT_EUR", "0")
    assert welt.run(B.reservieren(user_id=None, dealer_id=w.dealer_id, art="abholung"))["schluessel"] is None
    welt.run(db.ki_budget.delete_many({"_id": {"$regex": f":{w.dealer_id}:"}}))
    # --- Vertrag: EIN laufender Lauf je Firma und Stand (Unique-Index), abgelaufener Lease uebernehmbar
    IX = _module("indizes")
    welt.run(IX.ki_indizes(db))
    h = f"hash_{w.s}"
    start = {"id": f"b1_{w.s}", "art": "vertrag", "dealer_id": w.dealer_id, "user_id": w.sucher["id"], "input_hash": h,
             "status": "laeuft", "created_at": _jetzt(), "lease_until": "2099-01-01T00:00:00+00:00"}
    assert welt.run(DP._lauf_beanspruchen(dict(start), w.dealer_id, h)) is None
    fremd = welt.run(DP._lauf_beanspruchen({**start, "id": f"b2_{w.s}"}, w.dealer_id, h))
    assert fremd is not None and fremd["id"] == f"b1_{w.s}", "zweiter Start bekommt den laufenden"
    welt.run(db.ki_bewertungen.update_one({"id": f"b1_{w.s}"}, {"$set": {"lease_until": "2000-01-01T00:00:00+00:00"}}))
    assert welt.run(DP._lauf_beanspruchen({**start, "id": f"b3_{w.s}"}, w.dealer_id, h)) is None, "abgelaufen: uebernommen"
    docs = welt.run(db.ki_bewertungen.find({"dealer_id": w.dealer_id, "input_hash": h}, {"_id": 0, "id": 1}).to_list(10))
    assert [d["id"] for d in docs] == [f"b3_{w.s}"]
    welt.run(db.ki_bewertungen.delete_many({"dealer_id": w.dealer_id, "input_hash": h}))
    # --- Deal-Risk-Stufen: nur hoeher, nie niedriger
    f = S.deal_risk_stufe
    assert f("normal", kaufpreis=8000, fair=500, szenarien_hoch=0, experten=0) == "normal"
    assert f("normal", kaufpreis=8000, fair=4000, szenarien_hoch=0, experten=0) == "high"
    assert f("normal", kaufpreis=8000, fair=0, szenarien_hoch=2000, experten=0) == "high"
    assert f("normal", kaufpreis=8000, fair=0, szenarien_hoch=0, experten=1) == "high"
    assert f("normal", kaufpreis=8000, fair=4800, szenarien_hoch=0, experten=0) == "reconsider_purchase"
    assert f("normal", kaufpreis=8000, fair=2500, szenarien_hoch=3600, experten=0) == "reconsider_purchase"
    assert f("reconsider_purchase", kaufpreis=8000, fair=0, szenarien_hoch=0, experten=0) == "reconsider_purchase"
    assert f("high", kaufpreis=0, fair=0, szenarien_hoch=0, experten=0) == "high"
    assert f("quatsch", kaufpreis=0, fair=0, szenarien_hoch=0, experten=0) == "normal"
    # --- bestaetigte Diagnose: Kostenvoranschlag gewinnt, sonst Umfang engt die Spanne ein
    best = {"bereich": "Getriebe/Kupplung", "status": "Werkstatt hat Diagnose bestätigt", "fahrbereit": "ja",
            "warnleuchte": "keine"}
    r = PB.referenz("technik", {**best, "kva": "liegt vor", "kva_eur": "1200"}, "Getriebe/Kupplung")
    assert r["kind"] == "repair_estimate" and (r["low"], r["median"], r["high"]) == (1080, 1200, 1440)
    assert r["basis"] == "kostenvoranschlag" and r["assumption_made"] is False
    r = PB.referenz("technik", {**best, "umfang": "Kleinteil/Einstellung"}, "Getriebe/Kupplung")
    assert r["basis"] == "umfang" and r["low"] == 300 and r["high"] == round(300 + 3200 * 0.35)
    r = PB.referenz("technik", {**best, "umfang": "Austauschaggregat"}, "Getriebe/Kupplung")
    assert r["low"] == round(300 + 3200 * 0.7) and r["high"] == 3500
    r = PB.referenz("technik", best, "Getriebe/Kupplung")
    assert r["basis"] == "bestaetigt_ohne_umfang" and r["assumption_made"] is True and r["kind"] == "repair_estimate"
    assert PB.kva_betrag({"kva_eur": "1.200,50"}) == 1200.5 and PB.kva_betrag({"kva_eur": "3"}) is None
    # --- Quellenqualitaet
    assert MD.quelle_vertraut("ADAC", "https://www.adac.de/x") and MD.quelle_vertraut("ADAC", "")
    assert not MD.quelle_vertraut("irgendwer", "https://foren.example.org/x") and not MD.quelle_vertraut("blog", "")
    ref = {"low": 300, "high": 3500}
    assert MD.wert_plausibel({"min_eur": 250, "max_eur": 4000}, ref)
    assert not MD.wert_plausibel({"min_eur": 10, "max_eur": 50}, ref)
    assert not MD.wert_plausibel({"min_eur": 5000, "max_eur": 20000}, ref)
    assert MD.wert_plausibel({"min_eur": 5, "max_eur": 50}, {})

