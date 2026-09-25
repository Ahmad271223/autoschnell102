# -*- coding: utf-8 -*-
"""KI Stufe 3+4 (Wunsch Ahmad 25./26.09.2026, Umbau 26.09.) — ohne echten
KI-Aufruf: Inserats-Regeln, KI-Schadennachlass beim Vertrag (Paket mit
Referenzen und possibly_known, Vorschau, Start/Lesen, Deckel je Stunde,
Routen), Lernfall nur fuer den Schadennachlass, Kalibrierung global und je
Firma, Fahrer-Antworten auf Rueckfragen und die Betriebszahlen."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_befunde_runde17_termine import _jetzt, _module, welt  # noqa: E402,F401
from test_ki_abholbewertung_20260925 import _attrappe as _attrappe_abholung, _welt_aufbauen  # noqa: E402

ANTWORT = {
    "items": [
        {"source_id": "d1", "category": "damage", "title": "Delle Kotflügel vorne rechts",
         "price_relevant": True, "repair_method": "Smart-Repair", "repair_estimate_eur": 180,
         "minimum_justified_eur": 200, "fair_discount_eur": 250, "best_realistic_eur": 300,
         "negotiation_start_eur": 340, "manual_review_required": False, "reason": "Kleine Delle ohne Lackschaden."},
        {"source_id": "d2", "category": "damage", "title": "Kratzer Stoßfänger hinten",
         "price_relevant": True, "repair_method": "Spot-Repair", "repair_estimate_eur": 120,
         "minimum_justified_eur": 100, "fair_discount_eur": 150, "best_realistic_eur": 180,
         "negotiation_start_eur": 200, "manual_review_required": False,
         "reason": "Im Inserat genannt, daher geringer."},
    ],
    "combined": {"sum_fair_eur": 400, "overlap_adjustment_eur": 0, "minimum_justified_eur": 300,
                 "fair_discount_eur": 400, "best_realistic_eur": 480, "negotiation_start_eur": 540,
                 "deal_risk": "normal", "manual_review_required": False},
    "arguments": ["Der Kotflügel vorne rechts hat eine Delle, die im Inserat nicht genannt ist."],
}

SCHAEDEN = [
    {"id": "d1", "type_key": "delle", "type_label": "Delle", "zone": "Kotflügel vorne rechts", "view": "right",
     "severity_data": {"groesse": "2–5 cm", "lack": "nein", "lage": "Fläche"}},
    {"id": "d2", "type_key": "kratzer", "type_label": "Kratzer", "zone": "Stoßfänger hinten", "view": "rear",
     "severity_data": {"laenge": "5–15 cm", "tiefe": "oberflächlich", "anzahl": "einzeln"}},
]


def _attrappe(monkeypatch, antwort=ANTWORT, status="ok", zaehler=None):
    D = _module("ai.damage_pricing")

    async def _bewerten(**kw):
        if zaehler is not None:
            zaehler.append(kw)
        return {"status": status, "grund": "" if status == "ok" else "Attrappe",
                "daten": antwort if status == "ok" else None, "dauer_ms": 5,
                "modell": "attrappe", "usage": {"input_tokens": 1000, "output_tokens": 500}}
    monkeypatch.setattr(D, "json_bewerten", _bewerten)
    monkeypatch.setattr(D, "ki_aktiv", lambda: True)
    MD = _module("ai.marktdaten")

    async def _keine_recherche(**kw):
        return {"status": "aus", "grund": "Test", "text": "", "quellen": [], "suchen": 0, "dauer_ms": 0, "usage": {}}
    monkeypatch.setattr(MD, "recherche", _keine_recherche)
    return D


def _fahrzeug(welt, vid, **data):
    w, db = welt.w, welt.db
    daten = {"make_label": "BMW", "model_label": "530 Gran Turismo", "mileage": 206000,
             "first_registration": "01/2010", "power_kw": 180, "fuel_label": "Diesel", "price": 8900,
             "description": "Gepflegter Wagen, kleiner Kratzer an der Stoßstange hinten. 2 Schlüssel.",
             "known_defects": ["Panoramadach-Rollo lose"], "seller_name": "Vera Verkauf",
             "seller_phone": "0170 1111111"}
    daten.update(data)
    doc = w.fahrzeug(vid, data=daten)
    welt.run(db.vehicles.insert_one(doc))
    return doc


def _ki_aufraeumen(welt):
    db, w = welt.db, welt.w
    welt.run(db.ki_bewertungen.delete_many({"dealer_id": w.dealer_id}))
    welt.run(db.ki_lernfaelle.delete_many({"dealer_id": w.dealer_id}))


# ------------------------------------------------ Inserats-Regeln (ohne DB)
@pytest.mark.parametrize("v,erwartet", [
    ({"description": "2 Schlüssel vorhanden, HU 07/2028, scheckheftgepflegt, unfallfrei"},
     {"schluessel_anzahl": "2", "hu_valid": "Ja", "hu_until": "07/2028", "accident_free": "Ja"}),
    ({"description": "lückenlos scheckheftgepflegt bei BMW"}, {"service_book": "ja"}),
    ({"description": "Vollständiges Scheckheft vorhanden"}, {"service_book": "ja"}),
    ({"description": "Scheckheft komplett, TÜV neu"}, {"service_book": "ja", "hu_valid": "Ja"}),
    ({"description": "Kein Scheckheft vorhanden"}, {"service_book": "nein"}),
    ({"description": "Nur ein Schlüssel vorhanden"}, {"schluessel_anzahl": "1"}),
    ({"description": "Zweitschlüssel fehlt leider"}, {"schluessel_anzahl": "1"}),
    ({"description": "inkl. Zweitschlüssel"}, {"schluessel_anzahl": "2"}),
    ({"description": "drei Schlüssel dabei"}, {"schluessel_anzahl": "3"}),
    ({"keys_count": "2"}, {"schluessel_anzahl": "2"}),
    ({"hu": "2028-07"}, {"hu_valid": "Ja", "hu_until": "07/2028"}),
    ({"hu": "Neu"}, {"hu_valid": "Ja"}),
    ({"description": "TÜV abgelaufen, Bastlerfahrzeug"}, {"hu_valid": "Nein"}),
    ({"description": "HU/AU bis 3/2027"}, {"hu_valid": "Ja", "hu_until": "03/2027"}),
    ({"accident_damaged": True}, {"accident_free": "Nein"}),
    ({"accident_damaged": False}, {"accident_free": "Ja"}),
    ({"description": "Unfallschaden vorne links, fahrbereit"}, {"accident_free": "Nein", "drivable": "Ja"}),
    ({"description": "kein Unfallschaden"}, {"accident_free": "Ja"}),
    ({"roadworthy": False}, {"drivable": "Nein"}),
    ({"description": "EU-Import aus Italien"}, {"eu_import": "Ja"}),
    ({"description": "Sommer- und Winterreifen auf Alu"}, {"tires": "8-fach"}),
    ({"description": "Winterreifen dabei"}, {"tires": "8-fach"}),
    ({}, {}),
])
def test_01_inserat_regeln_eindeutige_werte(v, erwartet):
    R = _module("ai.inserat_regeln")
    erg = R.vorschlaege(v)
    werte = {k: e["value"] for k, e in erg["felder"].items()}
    assert werte == erwartet, erg
    for e in erg["felder"].values():
        assert e["source"] in ("listing_field", "listing_description") and e["source_text"]


def test_02_scheckheftgepflegt_allein_ist_nur_ein_hinweis():
    R = _module("ai.inserat_regeln")
    erg = R.vorschlaege({"description": "Fahrzeug ist scheckheftgepflegt"})
    assert "service_book" not in erg["felder"], "„scheckheftgepflegt“ heisst nicht lückenlos"
    assert erg["hinweise"] and "lückenlos" in erg["hinweise"][0]
    erg = R.vorschlaege({"features": ["Scheckheftgepflegt"]})
    assert "service_book" not in erg["felder"] and erg["hinweise"]
    erg = R.vorschlaege({"description": "unfallfrei, hatte aber einen Unfall"})
    assert "accident_free" not in erg["felder"]
    erg = R.vorschlaege({"description": "lückenlos scheckheftgepflegt, Scheckheft fehlt"})
    assert "service_book" not in erg["felder"] and erg["hinweise"]
    erg = R.vorschlaege({"known_defects": ["Rollo lose"]})
    assert erg["bekannte_maengel"] == ["Rollo lose"]


# ------------------------------------------------ Schadennachlass beim Vertrag
def test_03_paket_vorschau_und_bewertung_vertrag(welt, monkeypatch):
    aufrufe = []
    D = _attrappe(monkeypatch, zaehler=aufrufe)
    w = welt.w
    vid = f"v_kiv3_{w.s}"
    fz = _fahrzeug(welt, vid)
    paket = D.paket_bauen(fz["data"], SCHAEDEN, kaufpreis=None)
    assert "Vera" not in str(paket) and "0170" not in str(paket)
    assert paket["prices"] == {"listing_price_eur": 8900.0, "agreed_price_eur": None}
    s = {d["id"]: d for d in paket["damages"]}
    assert s["d2"]["possibly_known"] is True, "Kratzer + Stossstange stehen im Inserat"
    assert s["d1"]["possibly_known"] is False
    # oberflaechlicher Kratzer bleibt polierbar, auch am Stossfaenger
    assert s["d1"]["repair_reference"]["key"] == "delle_klein" and s["d2"]["repair_reference"]["key"] == "kratzer_polierbar"
    assert paket["vehicle"]["power_ps"] == 245 and paket["vehicle"]["age_years"] >= 16
    assert paket["listing_state"]["known_defects"] == ["Panoramadach-Rollo lose"]
    # Vorschau ohne KI: Summe der Referenz-Mediane
    v = D.vorschau(fz, SCHAEDEN, None)
    assert v["vorlaeufig"] is True and v["fair_discount_eur"] > 0 and v["basis"] == "inseratspreis"
    assert v["minimum_justified_eur"] <= v["fair_discount_eur"] <= v["best_realistic_eur"] <= v["negotiation_start_eur"]
    assert len(v["positionen"]) == 2 and v["datenlage"] in ("hoch", "mittel", "niedrig")
    # Bewertung inline
    erg = welt.run(D.bewerten(user=w.sucher, vehicle_doc=fz, damages=SCHAEDEN))
    assert erg["status"] == "ok" and erg["id"] and erg["basis"] == "inseratspreis" and erg["kaufpreis"] == 8900.0
    items = {i["source_id"]: i for i in erg["ergebnis"]["items"]}
    assert items["d1"]["fair_discount_eur"] == 250.0 and items["d1"]["negotiation_start_eur"] == 340.0
    comb = erg["ergebnis"]["combined"]
    assert comb["fair_discount_eur"] == 400.0 and comb["recommended_purchase_price_eur"] == 8500.0
    assert comb["deal_risk"] == "normal" and erg["ergebnis"]["datenlage"] in ("hoch", "mittel", "niedrig")
    assert erg["vorschau"]["vorlaeufig"] is True and erg["kosten_ct"] < 5
    assert len(aufrufe) == 1 and "Ausgangswerte AutoSchnell" in aufrufe[0]["system"]
    assert "listing_state" in aufrufe[0]["nutzer"] and "precomputed" in aufrufe[0]["nutzer"]
    # derselbe Stand: kein zweiter Aufruf, dieselbe id; Lesen liefert es
    erg2 = welt.run(D.bewerten(user=w.sucher, vehicle_doc=fz, damages=SCHAEDEN))
    assert erg2["id"] == erg["id"] and len(aufrufe) == 1
    assert welt.run(D.lesen(erg["id"], w.dealer_id))["status"] == "ok"
    assert welt.run(D.lesen(erg["id"], "d_fremd")) is None
    # mit verhandeltem Kaufpreis: neuer Stand, neue Basis
    erg3 = welt.run(D.bewerten(user=w.sucher, vehicle_doc=fz, damages=SCHAEDEN, kaufpreis=8000))
    assert erg3["basis"] == "kaufpreis" and erg3["kaufpreis"] == 8000 and len(aufrufe) == 2
    assert erg3["ergebnis"]["combined"]["recommended_purchase_price_eur"] == 7600.0
    gespeichert = welt.run(welt.db.ki_bewertungen.find_one({"id": erg["id"]}, {"_id": 0}))
    assert gespeichert["art"] == "vertrag" and gespeichert["prompt_version"] == "vertrag_v2"
    assert gespeichert["user_id"] == w.sucher["id"] and "Vera" not in str(gespeichert) and "lease_until" not in gespeichert
    _ki_aufraeumen(welt)


def test_04_start_im_hintergrund_keine_aus_deckel(welt, monkeypatch):
    aufrufe = []
    D = _attrappe(monkeypatch, zaehler=aufrufe)
    w = welt.w
    fz = _fahrzeug(welt, f"v_kiv4_{w.s}")
    assert welt.run(D.bewerten(user=w.sucher, vehicle_doc=fz, damages=[]))["status"] == "keine"
    assert not aufrufe
    monkeypatch.setattr(D, "ki_aktiv", lambda: False)
    erg = welt.run(D.bewerten(user=w.sucher, vehicle_doc=fz, damages=SCHAEDEN))
    assert erg["status"] == "aus" and erg["vorschau"]["fair_discount_eur"] > 0, "auch ohne KI eine Vorschau"
    monkeypatch.setattr(D, "ki_aktiv", lambda: True)

    # Start ohne Warten: sofort laeuft + Vorschau, danach ok
    async def _start_und_warten():
        start = await D.bewerten(user=w.sucher, vehicle_doc=fz, damages=SCHAEDEN, warten=False)
        for t in list(D._laufende):
            await t
        return start, await D.lesen(start["id"], w.dealer_id)
    start, fertig = welt.run(_start_und_warten())
    assert start["status"] == "laeuft" and start["vorschau"]["vorlaeufig"] is True
    assert fertig["status"] == "ok" and fertig["id"] == start["id"] and len(aufrufe) == 1
    # Deckel je Stunde
    monkeypatch.setattr(D, "MAX_JE_STUNDE", 1)
    andere = [dict(SCHAEDEN[0], zone="Tür hinten links")]
    erg = welt.run(D.bewerten(user=w.sucher, vehicle_doc=fz, damages=andere))
    assert erg["status"] == "limit" and "je Stunde" in erg["grund"] and len(aufrufe) == 1
    monkeypatch.setattr(D, "MAX_JE_STUNDE", 100)
    _attrappe(monkeypatch, status="zeitlimit")
    erg = welt.run(D.bewerten(user=w.sucher, vehicle_doc=fz, damages=andere))
    assert erg["status"] == "zeitlimit" and erg["ergebnis"] is None
    _ki_aufraeumen(welt)


def test_05_routen_und_lernfall_nur_schadennachlass(welt, monkeypatch):
    from fastapi import HTTPException
    D = _attrappe(monkeypatch)
    C = _module("routes.contracts")
    w, db = welt.w, welt.db
    vid = f"v_kiv5_{w.s}"
    _fahrzeug(welt, vid, description="HU 07/2028, lückenlos scheckheftgepflegt, 2 Schlüssel")
    vs = welt.run(C.vertrag_vorschlaege(vid, user=w.chef))
    assert vs["felder"]["hu_until"]["value"] == "07/2028" and vs["felder"]["service_book"]["value"] == "ja"
    with pytest.raises(HTTPException) as ex:
        welt.run(C.vertrag_vorschlaege(f"gibtsnicht_{w.s}", user=w.chef))
    assert ex.value.status_code == 404
    body = C.KiSchadenIn(vehicle_id=vid, damages=SCHAEDEN, purchase_price=8300)
    vor = welt.run(C.vertrag_ki_vorschau(body, user=w.chef))
    assert vor["vorlaeufig"] is True and vor["kaufpreis"] == 8300

    async def _route():
        start = await C.vertrag_ki_schadennachlass(body, user=w.chef)
        for t in list(D._laufende):
            await t
        return start, await C.vertrag_ki_schadennachlass_stand(start["id"], user=w.chef)
    start, stand = welt.run(_route())
    assert start["status"] == "laeuft" and stand["status"] == "ok" and stand["kaufpreis"] == 8300
    with pytest.raises(HTTPException):
        welt.run(C.vertrag_ki_schadennachlass_stand("gibtsnicht", user=w.chef))
    with pytest.raises(HTTPException):
        welt.run(C.vertrag_ki_schadennachlass(C.KiSchadenIn(vehicle_id="x_" + w.s, damages=SCHAEDEN), user=w.chef))
    a = C.ContractIn(vehicle_id=vid, seller_name="V", purchase_price=8300, ki_bewertung_id=stand["id"])
    b = C.ContractIn(vehicle_id=vid, seller_name="V", purchase_price=8300)
    assert C._anfrage_hash(a) == C._anfrage_hash(b)
    # Lernfall: Basis war der VOR der Schadenverhandlung vereinbarte Preis 8300,
    # Vertrag 8000 -> gelernt werden 300 (nur der Schadennachlass)
    vertrag = {"id": f"c_kiv5_{w.s}", "dealer_id": w.dealer_id, "purchase_price": 8000}
    welt.run(D.lernfall_speichern(vertrag, stand["id"]))
    lern = welt.run(db.ki_lernfaelle.find_one({"contract_id": vertrag["id"]}, {"_id": 0}))
    assert lern and lern["art"] == "vertrag" and lern["tatsaechlicher_nachlass"] == 300.0
    assert lern["preis_vor_maengelverhandlung"] == 8300 and lern["ki_nachlass"] == 400.0 and "Vera" not in str(lern)
    # Basis Inseratspreis: Inserat minus Vertrag enthaelt den allgemeinen Nachlass -> NICHT gelernt
    erg_ins = welt.run(D.bewerten(user=w.chef, vehicle_doc=welt.run(db.vehicles.find_one({"id": vid}, {"_id": 0})),
                                  damages=SCHAEDEN))
    vertrag2 = {"id": f"c_kiv5b_{w.s}", "dealer_id": w.dealer_id, "purchase_price": 7000}
    welt.run(D.lernfall_speichern(vertrag2, erg_ins["id"]))
    lern2 = welt.run(db.ki_lernfaelle.find_one({"contract_id": vertrag2["id"]}, {"_id": 0}))
    assert lern2 and lern2["tatsaechlicher_nachlass"] is None
    welt.run(D.lernfall_speichern({"id": "c_x_" + w.s, "dealer_id": "d_fremd", "purchase_price": 1}, stand["id"]))
    assert welt.run(db.ki_lernfaelle.count_documents({"contract_id": "c_x_" + w.s})) == 0
    _ki_aufraeumen(welt)


# ------------------------------------------------ Kalibrierung (Stufe 4)
def test_06_erfahrungswerte_global_und_je_firma(welt, monkeypatch):
    K = _module("ai.kalibrierung")
    w, db = welt.w, welt.db
    docs = []
    for i in range(6):
        docs.append({"art": "vertrag", "dealer_id": w.dealer_id, "contract_id": f"c_kal{i}_{w.s}",
                     "created_at": _jetzt(), "ki_nachlass": 400.0, "tatsaechlicher_nachlass": 200.0,
                     "items": [{"category": "tires", "fair_discount_eur": 400.0}]})
    welt.run(db.ki_lernfaelle.insert_many(docs))
    try:
        monkeypatch.setattr(K, "MIN_FAELLE", 6)
        monkeypatch.setattr(K, "MIN_FIRMA", 5)
        werte = welt.run(K.erfahrungswerte(frisch=True))
        assert werte["gesamt"]["n"] >= 6 and werte["je_kategorie"]["tires"]["faktor_median"] == 0.5
        firma = welt.run(K.erfahrungswerte(frisch=True, dealer_id=w.dealer_id))
        assert firma["gesamt"]["n"] == 6 and firma["gesamt"]["faktor_median"] == 0.5
        text = welt.run(K.prompt_zusatz(w.dealer_id))
        assert "Faellen dieser Firma" in text and "50 %" in text
        assert K.als_text(firma, minimum=100) == ""
        assert K._faktor({"ki_nachlass": 100, "tatsaechlicher_nachlass": 900}) == 3.0
        assert K._faktor({"ki_nachlass": 0, "tatsaechlicher_nachlass": 50}) is None
        st = welt.run(K.statistik(tage=30))
        for k in ("bewertungen", "je_status", "tokens", "kosten_usd_geschaetzt", "lernfaelle", "erfahrungswerte",
                  "marktdaten", "eigene_preise", "budget"):
            assert k in st
        assert st["budget"]["monat_eur"] > 0
    finally:
        welt.run(db.ki_lernfaelle.delete_many({"dealer_id": w.dealer_id}))
        K.zuruecksetzen()


def test_07_kalibrierung_landet_im_zusatz(welt, monkeypatch):
    aufrufe = []
    D = _attrappe(monkeypatch, zaehler=aufrufe)
    K = _module("ai.kalibrierung")

    async def _zusatz(dealer_id=None):
        return "Erfahrungswerte aus 9 abgeschlossenen AutoSchnell-Faellen: Test."
    monkeypatch.setattr(K, "prompt_zusatz", _zusatz)
    w = welt.w
    fz = _fahrzeug(welt, f"v_kiv7_{w.s}")
    erg = welt.run(D.bewerten(user=w.sucher, vehicle_doc=fz, damages=SCHAEDEN))
    assert erg["status"] == "ok" and aufrufe[0]["zusatz"].endswith("Faellen: Test.")
    KA = _attrappe_abholung(monkeypatch, zaehler=None)
    _cid, _tid, _vid, pid = _welt_aufbauen(welt, "7")
    gesehen = {}

    async def _bewerten(**kw):
        gesehen.update(kw)
        return {"status": "fehler", "grund": "Attrappe", "daten": None, "dauer_ms": 1, "modell": "a", "usage": {}}
    monkeypatch.setattr(KA, "json_bewerten", _bewerten)
    welt.run(KA.bewertung_ausfuehren(pid, w.dealer_id))
    assert gesehen["zusatz"].endswith("Faellen: Test.")
    _ki_aufraeumen(welt)


# ------------------------------------------------ Fahrer antwortet per Knopf
def test_08_rueckfrage_mit_knopf_und_fahrer_antwort(welt, monkeypatch):
    KA = _attrappe_abholung(monkeypatch)
    P = _module("routes.protocols")
    w, db = welt.w, welt.db
    _cid, _tid, _vid, pid = _welt_aufbauen(welt, "8")
    vorher = welt.run(KA.bewertung_ausfuehren(pid, w.dealer_id))
    assert vorher["status"] == "ok"
    frage = {"source_id": "d1", "question": "Ist der Lack beschädigt?", "options": ["Ja", "Nein", "Unklar"]}
    stand = welt.run(db.pickup_protocols.find_one({"id": pid}, {"_id": 0, "freigabe_stand": 1, "updated_at": 1}))
    body = P.FreigabeIn(zurueck=True, notiz="Bitte prüfen: Ist der Lack beschädigt?",
                        rueckfrage_frage=frage, stand=stand.get("freigabe_stand") or stand.get("updated_at"))
    welt.run(P.protokoll_freigeben(pid, body, user=w.chef))
    doc = welt.run(db.pickup_protocols.find_one({"id": pid}, {"_id": 0}))
    assert doc["status"] == "entwurf" and doc["rueckfrage_frage"] == frage
    ein = P.ProtocolIn(rueckfrage_antworten=[{"source_id": "d1", "question": frage["question"], "answer": "Nein",
                                              "at": _jetzt()}])
    assert ein.rueckfrage_antworten[0]["answer"] == "Nein"
    with pytest.raises(ValueError):
        P.ProtocolIn(rueckfrage_antworten=[{"a": 1}] * 11)
    with pytest.raises(ValueError):
        P.FreigabeIn(zurueck=True, rueckfrage_frage={"question": ""})
    welt.run(db.pickup_protocols.update_one({"id": pid}, {"$set": {"rueckfrage_antworten": ein.rueckfrage_antworten,
                                                                  "revision": 5}}))
    grund = welt.run(KA._grundlagen(pid, w.dealer_id))
    paket = KA.paket_bauen(*grund)
    assert paket["driver_answers"] == [{"source_id": "d1", "question": frage["question"], "answer": "Nein"}]
    assert KA.eingabe_hash(paket) != vorher["input_hash"], "Antwort aendert den Stand -> neue Bewertung"
    welt.run(db.pickup_protocols.update_one({"id": pid}, {"$set": {"status": "zur_freigabe"}}))
    liste = welt.run(P.protokolle_zur_freigabe(user=w.chef))
    eintrag = next(e for e in liste if e["protocol_id"] == pid)
    assert eintrag["rueckfrage_antworten"][0]["answer"] == "Nein"
    _ki_aufraeumen(welt)


def test_09_admin_ki_zahlen(welt):
    A = _module("routes.admin")
    erg = welt.run(A.admin_ki(admin={"id": "x"}))
    assert set(erg) >= {"aktiv", "modell", "bewertungen", "je_status", "lernfaelle", "erfahrungswerte", "marktdaten",
                        "eigene_preise", "budget"}
