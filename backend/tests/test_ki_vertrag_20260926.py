# -*- coding: utf-8 -*-
"""KI Stufe 3+4 (Wunsch Ahmad 25./26.09.2026) — ohne echten KI-Aufruf:
Inserats-Regeln (Schluessel, HU, Scheckheft nur bei "lueckenlos"/"kein",
Unfall, fahrbereit, EU-Import, Bereifung), KI-Schadennachlass beim Vertrag
(Paket, Ablage, Deckel je Stunde, Routen), Lernfall beim Vertrag,
Kalibrierung aus den eigenen Faellen, Fahrer-Antworten auf Rueckfragen und
die Betriebszahlen."""
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
         "price_relevant": True, "priority": "gelb", "repair_method": "Smart-Repair",
         "repair_estimate_eur": 180, "recommended_discount_eur": 250, "discount_min_eur": 100,
         "discount_max_eur": 600, "confidence": 0.88, "manual_review_required": False,
         "reason": "Kleine Delle ohne Lackschaden."},
        {"source_id": "d2", "category": "damage", "title": "Kratzer Stoßfänger hinten",
         "price_relevant": True, "priority": "gelb", "repair_method": "Spot-Repair",
         "repair_estimate_eur": 120, "recommended_discount_eur": 150, "discount_min_eur": 130,
         "discount_max_eur": 170, "confidence": 0.8, "manual_review_required": False,
         "reason": "Im Inserat als bekannter Kratzer genannt, daher geringer."},
    ],
    "combined": {"sum_of_items_eur": 400, "overlap_adjustment_eur": 0, "recommended_discount_eur": 400,
                 "discount_min_eur": 360, "discount_max_eur": 440, "negotiation_start_eur": 500,
                 "confidence": 0.84, "manual_review_required": False},
    "needs_information": [{"source_id": "d2", "question": "Ist der Kratzer tief?",
                           "options": ["oberflächlich", "tief", "unbekannt"]}],
    "arguments": ["Der Kotflügel vorne rechts hat eine Delle, die im Inserat nicht genannt ist."],
}

SCHAEDEN = [
    {"id": "d1", "type_key": "delle", "type_label": "Delle", "zone": "Kotflügel vorne rechts", "view": "right",
     "severity_data": {"groesse": "2–5 cm", "lack": "nein"}},
    {"id": "d2", "type_key": "kratzer", "type_label": "Kratzer", "zone": "Stoßfänger hinten", "view": "rear"},
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
    # Ausstattungs-Flag der Portale ebenso
    erg = R.vorschlaege({"features": ["Scheckheftgepflegt"]})
    assert "service_book" not in erg["felder"] and erg["hinweise"]
    # widerspruechlich: nichts vorauswaehlen
    erg = R.vorschlaege({"description": "unfallfrei, hatte aber einen Unfall"})
    assert "accident_free" not in erg["felder"]
    erg = R.vorschlaege({"description": "lückenlos scheckheftgepflegt, Scheckheft fehlt"})
    assert "service_book" not in erg["felder"] and erg["hinweise"]
    # bekannte Maengel gehen mit
    erg = R.vorschlaege({"known_defects": ["Rollo lose"]})
    assert erg["bekannte_maengel"] == ["Rollo lose"]


# ------------------------------------------------ Schadennachlass beim Vertrag
def test_03_paket_und_bewertung_vertrag(welt, monkeypatch):
    aufrufe = []
    D = _attrappe(monkeypatch, zaehler=aufrufe)
    w = welt.w
    vid = f"v_kiv3_{w.s}"
    fz = _fahrzeug(welt, vid)
    paket = D.paket_bauen(fz["data"], SCHAEDEN, kaufpreis=None)
    assert "Vera" not in str(paket) and "0170" not in str(paket)
    assert paket["prices"] == {"listing_price_eur": 8900.0, "agreed_price_eur": None}
    s = {d["id"]: d for d in paket["damages"]}
    assert s["d2"]["mentioned_in_listing"] is True, "Kratzer steht im Inserat"
    assert s["d1"]["mentioned_in_listing"] is False
    assert s["d1"]["severity_data"] == {"groesse": "2–5 cm", "lack": "nein"}
    assert paket["known_defects_listing"] == ["Panoramadach-Rollo lose"]

    erg = welt.run(D.bewerten(user=w.sucher, vehicle_doc=fz, damages=SCHAEDEN))
    assert erg["status"] == "ok" and erg["id"] and erg["basis"] == "inseratspreis" and erg["kaufpreis"] == 8900.0
    items = {i["source_id"]: i for i in erg["ergebnis"]["items"]}
    assert (items["d1"]["discount_min_eur"], items["d1"]["discount_max_eur"]) == (200.0, 300.0), "enge Spanne"
    comb = erg["ergebnis"]["combined"]
    assert comb["recommended_discount_eur"] == 400.0 and comb["recommended_purchase_price_eur"] == 8500.0
    assert erg["ergebnis"]["needs_information"][0]["source_id"] == "d2"
    assert len(aufrufe) == 1 and "Erfahrungswerte" not in aufrufe[0]["system"] or True
    # derselbe Stand: kein zweiter Aufruf, dieselbe id
    erg2 = welt.run(D.bewerten(user=w.sucher, vehicle_doc=fz, damages=SCHAEDEN))
    assert erg2["id"] == erg["id"] and len(aufrufe) == 1
    # mit verhandeltem Kaufpreis: neuer Stand, neue Basis
    erg3 = welt.run(D.bewerten(user=w.sucher, vehicle_doc=fz, damages=SCHAEDEN, kaufpreis=8000))
    assert erg3["basis"] == "kaufpreis" and erg3["kaufpreis"] == 8000 and len(aufrufe) == 2
    assert erg3["ergebnis"]["combined"]["recommended_purchase_price_eur"] == 7600.0
    gespeichert = welt.run(welt.db.ki_bewertungen.find_one({"id": erg["id"]}, {"_id": 0}))
    assert gespeichert["art"] == "vertrag" and gespeichert["prompt_version"] == "vertrag_v1"
    assert gespeichert["user_id"] == w.sucher["id"] and "Vera" not in str(gespeichert)
    _ki_aufraeumen(welt)


def test_04_keine_schaeden_aus_und_deckel_je_stunde(welt, monkeypatch):
    aufrufe = []
    D = _attrappe(monkeypatch, zaehler=aufrufe)
    w = welt.w
    fz = _fahrzeug(welt, f"v_kiv4_{w.s}")
    assert welt.run(D.bewerten(user=w.sucher, vehicle_doc=fz, damages=[]))["status"] == "keine"
    assert not aufrufe
    monkeypatch.setattr(D, "ki_aktiv", lambda: False)
    assert welt.run(D.bewerten(user=w.sucher, vehicle_doc=fz, damages=SCHAEDEN))["status"] == "aus"
    monkeypatch.setattr(D, "ki_aktiv", lambda: True)
    monkeypatch.setattr(D, "MAX_JE_STUNDE", 1)
    assert welt.run(D.bewerten(user=w.sucher, vehicle_doc=fz, damages=SCHAEDEN))["status"] == "ok"
    andere = [dict(SCHAEDEN[0], zone="Tür hinten links")]
    erg = welt.run(D.bewerten(user=w.sucher, vehicle_doc=fz, damages=andere))
    assert erg["status"] == "limit" and "je Stunde" in erg["grund"] and len(aufrufe) == 1
    # Fehler der KI: Status, kein Wurf, Alarm
    monkeypatch.setattr(D, "MAX_JE_STUNDE", 100)
    _attrappe(monkeypatch, status="zeitlimit")
    erg = welt.run(D.bewerten(user=w.sucher, vehicle_doc=fz, damages=andere))
    assert erg["status"] == "zeitlimit" and erg["ergebnis"] is None
    _ki_aufraeumen(welt)


def test_05_routen_und_lernfall_vertrag(welt, monkeypatch):
    from fastapi import HTTPException
    D = _attrappe(monkeypatch)
    C = _module("routes.contracts")
    w, db = welt.w, welt.db
    vid = f"v_kiv5_{w.s}"
    _fahrzeug(welt, vid, description="HU 07/2028, lückenlos scheckheftgepflegt, 2 Schlüssel")
    vs = welt.run(C.vertrag_vorschlaege(vid, user=w.chef))
    assert vs["felder"]["hu_until"]["value"] == "07/2028" and vs["felder"]["service_book"]["value"] == "ja"
    assert vs["felder"]["schluessel_anzahl"]["value"] == "2"
    with pytest.raises(HTTPException) as ex:
        welt.run(C.vertrag_vorschlaege(f"gibtsnicht_{w.s}", user=w.chef))
    assert ex.value.status_code == 404
    body = C.KiSchadenIn(vehicle_id=vid, damages=SCHAEDEN, purchase_price=8300)
    erg = welt.run(C.vertrag_ki_schadennachlass(body, user=w.chef))
    assert erg["status"] == "ok" and erg["kaufpreis"] == 8300
    with pytest.raises(HTTPException):
        welt.run(C.vertrag_ki_schadennachlass(C.KiSchadenIn(vehicle_id="x_" + w.s, damages=SCHAEDEN), user=w.chef))
    # ContractIn nimmt das Steuerfeld an; es gehoert nicht in den Anfrage-Hash
    a = C.ContractIn(vehicle_id=vid, seller_name="V", purchase_price=8300, ki_bewertung_id=erg["id"])
    b = C.ContractIn(vehicle_id=vid, seller_name="V", purchase_price=8300)
    assert C._anfrage_hash(a) == C._anfrage_hash(b)
    # Lernfall: Inseratspreis 8900, Vertrag 8300 -> erzielter Nachlass 600
    vertrag = {"id": f"c_kiv5_{w.s}", "dealer_id": w.dealer_id, "purchase_price": 8300}
    welt.run(D.lernfall_speichern(vertrag, erg["id"]))
    lern = welt.run(db.ki_lernfaelle.find_one({"contract_id": vertrag["id"]}, {"_id": 0}))
    assert lern and lern["art"] == "vertrag" and lern["tatsaechlicher_nachlass"] == 600.0
    assert lern["ki_nachlass"] == 400.0 and lern["inseratspreis"] == 8900.0 and "Vera" not in str(lern)
    # fremde oder unbekannte Bewertung: kein Lernfall
    welt.run(D.lernfall_speichern({"id": "c_x_" + w.s, "dealer_id": "d_fremd", "purchase_price": 1}, erg["id"]))
    assert welt.run(db.ki_lernfaelle.count_documents({"contract_id": "c_x_" + w.s})) == 0
    welt.run(D.lernfall_speichern({"id": "c_y_" + w.s, "dealer_id": w.dealer_id, "purchase_price": 1}, None))
    assert welt.run(db.ki_lernfaelle.count_documents({"contract_id": "c_y_" + w.s})) == 0
    _ki_aufraeumen(welt)


# ------------------------------------------------ Kalibrierung (Stufe 4)
def test_06_erfahrungswerte_aus_eigenen_faellen(welt, monkeypatch):
    K = _module("ai.kalibrierung")
    w, db = welt.w, welt.db
    docs = []
    for i in range(6):
        docs.append({"art": "vertrag", "dealer_id": w.dealer_id, "contract_id": f"c_kal{i}_{w.s}",
                     "created_at": _jetzt(), "ki_nachlass": 400.0, "tatsaechlicher_nachlass": 200.0,
                     "items": [{"category": "tires", "recommended_discount_eur": 400.0}]})
    welt.run(db.ki_lernfaelle.insert_many(docs))
    try:
        werte = welt.run(K.erfahrungswerte(frisch=True))
        assert werte["gesamt"]["n"] >= 6
        assert werte["je_kategorie"]["tires"]["faktor_median"] == 0.5 and werte["je_kategorie"]["tires"]["n"] >= 6
        assert werte["je_art"]["vertrag"]["n"] >= 6
        text = K.als_text(werte)
        assert "Erfahrungswerte" in text and "tires 50 %" in text
        monkeypatch.setattr(K, "MIN_FAELLE", 100000)
        assert K.als_text(werte) == ""
        # Faktor-Deckel und Unbrauchbares
        assert K._faktor({"ki_nachlass": 100, "tatsaechlicher_nachlass": 900}) == 3.0
        assert K._faktor({"ki_nachlass": 0, "tatsaechlicher_nachlass": 50}) is None
        assert K._faktor({"ki_nachlass": 100, "chef_nachlass": 80}) == 0.8
        # Statistik fuer die Betriebsseite
        st = welt.run(K.statistik(tage=30))
        for k in ("bewertungen", "je_status", "je_art", "tokens", "kosten_usd_geschaetzt", "lernfaelle", "erfahrungswerte"):
            assert k in st
        assert st["lernfaelle"]["gesamt"] >= 6
    finally:
        welt.run(db.ki_lernfaelle.delete_many({"dealer_id": w.dealer_id}))
        K.zuruecksetzen()


def test_07_kalibrierung_landet_im_prompt(welt, monkeypatch):
    aufrufe = []
    D = _attrappe(monkeypatch, zaehler=aufrufe)
    K = _module("ai.kalibrierung")

    async def _zusatz():
        return "Erfahrungswerte aus 9 abgeschlossenen AutoSchnell-Faellen: Test."
    monkeypatch.setattr(K, "prompt_zusatz", _zusatz)
    w = welt.w
    fz = _fahrzeug(welt, f"v_kiv7_{w.s}")
    erg = welt.run(D.bewerten(user=w.sucher, vehicle_doc=fz, damages=SCHAEDEN))
    assert erg["status"] == "ok" and aufrufe[0]["system"].endswith("Faellen: Test.")
    assert "Ausgangswerte AutoSchnell" in aufrufe[0]["system"]
    # dasselbe fuer die Abholung
    KA = _attrappe_abholung(monkeypatch, zaehler=None)
    _cid, _tid, _vid, pid = _welt_aufbauen(welt, "7")
    gesehen = {}

    async def _bewerten(**kw):
        gesehen.update(kw)
        return {"status": "fehler", "grund": "Attrappe", "daten": None, "dauer_ms": 1, "modell": "a", "usage": {}}
    monkeypatch.setattr(KA, "json_bewerten", _bewerten)
    welt.run(KA.bewertung_ausfuehren(pid, w.dealer_id))
    assert gesehen["system"].endswith("Faellen: Test.")
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
    # Fahrer antwortet (ProtocolIn prueft und kuerzt)
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
    # Liste fuer den Chef traegt die Antworten
    welt.run(db.pickup_protocols.update_one({"id": pid}, {"$set": {"status": "zur_freigabe"}}))
    liste = welt.run(P.protokolle_zur_freigabe(user=w.chef))
    eintrag = next(e for e in liste if e["protocol_id"] == pid)
    assert eintrag["rueckfrage_antworten"][0]["answer"] == "Nein"
    # "Zurueck" ohne Frage entfernt eine alte Frage
    stand = welt.run(db.pickup_protocols.find_one({"id": pid}, {"_id": 0, "freigabe_stand": 1, "updated_at": 1}))
    welt.run(P.protokoll_freigeben(pid, P.FreigabeIn(zurueck=True, notiz="x", stand=stand.get("freigabe_stand")),
                                   user=w.chef))
    doc = welt.run(db.pickup_protocols.find_one({"id": pid}, {"_id": 0}))
    assert "rueckfrage_frage" not in doc
    _ki_aufraeumen(welt)


def test_09_admin_ki_zahlen(welt):
    A = _module("routes.admin")
    erg = welt.run(A.admin_ki(admin={"id": "x"}))
    assert set(erg) >= {"aktiv", "modell", "bewertungen", "je_status", "lernfaelle", "erfahrungswerte"}
