# -*- coding: utf-8 -*-
"""Review 26.09.2026 — Abholprotokoll: Validierung, Rueckfragen, Fahrer-Datenschutz
(Nr. 57-66, 86, 101-110, 113-115, 121-129, 133-135).

  57/58/134  Antworten des Fahrers nur zur aktuell gestellten Frage (frage_id),
             nur angebotene Optionen (oder Freitext), Zeitstempel vom Server.
  59         Abschicken ohne Antwort auf die offene Rueckfrage -> 400.
  60-62/127  Je Frage genau eine Antwort; Runden wandern in rueckfrage_verlauf.
  63-66      Schaeden: Whitelist Art/Ansicht/Bauteil/Koordinaten/Merkmale;
             Abschicken verlangt vollstaendige Schaeden; Konstanten synchron
             mit kiSchaden.js / DamageSelector.jsx (Quelltext-Test).
  86/101-105/113-115  keys_count Ganzzahl 0-20, mileage Ganzzahl 0-5 Mio.;
             keys_expected nur vom Server (Vertrag); 0 ist ein Wert.
  106        "weicht ab"-Werte typgeprueft (km, EZ, FIN, Leistung, Text).
  107-110    documents/features nur mit Schluesseln der Servervorlage.
  121-124/135  Korrektur uebernimmt keine Rueckfrage-Felder.
  125        Rueckfrage braucht >= 2 Optionen oder freitext.
  126        source_id der Rueckfrage muss zu KI-Position/Schaden gehoeren.
  128/129/133  Fahrer sieht vom Fahrzeug keine seller_*-Felder.

In-Prozess, Welt aus test_golive_20260913_abschluss.
"""
import os
import re
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_golive_20260913_abschluss import _abholung, _doc, _m, welt  # noqa: E402,F401

DB_NAME = os.environ.get("DB_NAME") or "autoschnell"
FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"


def _fehler(w, coro):
    with pytest.raises(HTTPException) as e:
        w.run(coro)
    return e.value.status_code, e.value.detail


def _speichern(w, t, **felder):
    """save_protocol mit der aktuellen Revision (Pflicht ab dem zweiten Speichern)."""
    P = _m("routes.protocols")
    rev = (_doc(w, "pickup_protocols", t.pid) or {}).get("revision")
    return w.run(P.save_protocol(t.aid, P.ProtocolIn(revision=rev, **felder), w.driver))


def _schaden(**extra):
    d = {"id": "s1", "view": "front", "type_key": "kratzer", "type_label": "Kratzer",
         "zone": "Motorhaube", "x": 700, "y": 400,
         "severity_data": {"laenge": "5–15 cm", "tiefe": "oberflächlich", "anzahl": "einzeln"}}
    d.update(extra)
    return d


# ============================================================ Quelltext: Frontend synchron
@pytest.mark.quelltext
def test_66_schwere_fragen_synchron_mit_kischaden_js():
    P = _m("routes.protocols")
    src = (FRONTEND / "lib" / "kiSchaden.js").read_text(encoding="utf-8")
    block = re.search(r"export const SCHWERE_FRAGEN = \{\n(.*?)\n\};", src, re.S).group(1)
    js = {}
    for art in re.finditer(r"^  (\w+): \[\n(.*?)^  \],", block, re.S | re.M):
        fragen = []
        # ein Frage-Objekt: { key: "...", label: "...", [nurWenn: {...},] options: [...][, betragBei..] }
        for f in re.finditer(r'\{\s*key:\s*"(\w+)".*?options:\s*\[(.*?)\][^}]*\}', art.group(2), re.S):
            objekt = f.group(0)
            eintrag = {"key": f.group(1), "options": re.findall(r'"([^"]*)"', f.group(2))}
            nw = re.search(r'nurWenn:\s*\{\s*(\w+):\s*"([^"]*)"', objekt)
            if nw:
                eintrag["nur_wenn"] = {nw.group(1): nw.group(2)}
            bb = re.search(r'betragBei:\s*"([^"]*)"', objekt)
            bk = re.search(r'betragKey:\s*"(\w+)"', objekt)
            if bb:
                eintrag["betrag_bei"] = bb.group(1)
            if bk:
                eintrag["betrag_key"] = bk.group(1)
            fragen.append(eintrag)
        js[art.group(1)] = fragen
    py = {art: [{k: v for k, v in f.items() if k != "label"} for f in fragen]
          for art, fragen in P.SCHWERE_FRAGEN.items()}
    assert js == py, "kiSchaden.js SCHWERE_FRAGEN und routes.protocols.SCHWERE_FRAGEN weichen ab"
    bereiche = re.search(r"export const TECHNIK_BEREICHE = \[(.*?)\];", src, re.S).group(1)
    assert tuple(re.findall(r'"([^"]*)"', bereiche)) == P.TECHNIK_BEREICHE
    assert re.search(r'TECHNIK_TYP = \{ key: "(\w+)"', src).group(1) == "technik"


@pytest.mark.quelltext
def test_63_schadensarten_ansichten_skizze_synchron_mit_damageselector():
    P = _m("routes.protocols")
    src = (FRONTEND / "components" / "DamageSelector.jsx").read_text(encoding="utf-8")
    arten = re.findall(r'\{ key: "(\w+)",\s+abbr:', src)
    assert tuple(arten) + ("technik",) == P.SCHADEN_ARTEN
    views = re.search(r"const VIEW_LABELS = \{(.*?)\};", src, re.S).group(1)
    assert tuple(re.findall(r"^\s*(\w+):", views, re.M)) == P.SCHADEN_ANSICHTEN
    assert int(re.search(r"const IMG_W = (\d+);", src).group(1)) == P.SKIZZE_BREITE
    assert int(re.search(r"const IMG_H = (\d+);", src).group(1)) == P.SKIZZE_HOEHE


# ============================================================ 63-65: Schadensmodell
def test_63_schaden_whitelist():
    P = _m("routes.protocols")
    ok = P.ProtocolIn(new_damages=[_schaden()]).new_damages[0]
    assert ok.type_key == "kratzer" and ok.zone == "Motorhaube"
    for kaputt, text in ((_schaden(type_key="explosion"), "Schadensart"),
                         (_schaden(view="unten"), "Ansicht"),
                         (_schaden(zone=""), "Bauteil"),
                         (_schaden(zone="z" * 61), "Bauteil"),
                         (_schaden(x=2000), "ausserhalb"),
                         (_schaden(y=-1), "ausserhalb"),
                         (_schaden(x=None), "Position"),
                         (_schaden(severity_data={"laenge": "5–15 cm", "farbe": "rot"}), "unbekanntes Merkmal"),
                         (_schaden(severity_data={"laenge": "sehr lang"}), "keine angebotene Antwort")):
        with pytest.raises(ValidationError) as e:
            P.ProtocolIn(new_damages=[kaputt])
        assert text in str(e.value), (kaputt, str(e.value))


def test_63_technischer_mangel():
    P = _m("routes.protocols")
    t = {"id": "t1", "view": "technik", "type_key": "technik", "type_label": "Technischer Mangel",
         "zone": "Motor", "severity_data": {"bereich": "Motor", "status": "Werkstatt hat Diagnose bestätigt",
                                            "fahrbereit": "ja", "warnleuchte": "keine",
                                            "umfang": "Bauteil tauschen", "kva": "liegt vor", "kva_eur": "1200"}}
    d = P.ProtocolIn(new_damages=[t]).new_damages[0]
    assert d.view == "technik" and d.x is None
    with pytest.raises(ValidationError):
        P.ProtocolIn(new_damages=[{**t, "zone": "Dach"}])
    with pytest.raises(ValidationError):
        P.ProtocolIn(new_damages=[{**t, "severity_data": {**t["severity_data"], "kva_eur": "12€"}}])
    with pytest.raises(ValidationError):
        P.ProtocolIn(new_damages=[{**t, "view": "front", "x": 1, "y": 1}])


def test_66_schaeden_vollstaendig():
    P = _m("routes.protocols")
    P.schaeden_vollstaendig_pruefen([_schaden()])
    P.schaeden_vollstaendig_pruefen([_schaden(severity_data={"laenge": "unbekannt", "tiefe": "unbekannt",
                                                             "anzahl": "unbekannt"})])
    with pytest.raises(HTTPException) as e:
        P.schaeden_vollstaendig_pruefen([_schaden(severity_data={"laenge": "5–15 cm"})])
    assert e.value.status_code == 400 and "Tiefe, Anzahl" in e.value.detail and "Kratzer Motorhaube" in e.value.detail
    # Technik: Folgefragen nur bei bestaetigter Diagnose; "liegt vor" braucht den Betrag
    sd = {"bereich": "Motor", "status": "nur Symptom bemerkt", "fahrbereit": "ja", "warnleuchte": "keine"}
    P.schaeden_vollstaendig_pruefen([{"type_key": "technik", "zone": "Motor", "severity_data": sd}])
    sd2 = {**sd, "status": "Werkstatt hat Diagnose bestätigt"}
    assert P.schaden_offen({"type_key": "technik", "severity_data": sd2}) == ["Umfang lt. Werkstatt", "Kostenvoranschlag"]
    sd3 = {**sd2, "umfang": "Bauteil tauschen", "kva": "liegt vor"}
    assert P.schaden_offen({"type_key": "technik", "severity_data": sd3}) == ["Betrag"]
    # Altbestand ohne bekannte Art: keine Fragen, kein Fehler
    P.schaeden_vollstaendig_pruefen([{"type": "SS", "zone": "hood"}])


def test_66_abschicken_verlangt_vollstaendige_schaeden(welt):
    w = welt
    P = _m("routes.protocols")
    t = _abholung(w, proto_status="entwurf",
                  new_damages=[_schaden(severity_data={"laenge": "5–15 cm"})])
    code, text = _fehler(w, P.submit_protocol(t.aid, w.driver))
    assert code == 400 and "unbekannt" in text and "Tiefe" in text


# ============================================================ 86/113-115: Zahlen
def test_86_keys_count_und_kilometer_als_zahl():
    P = _m("routes.protocols")
    assert P.ProtocolIn(keys_count="2").keys_count == 2
    assert P.ProtocolIn(keys_count=0).keys_count == 0
    assert P.ProtocolIn(keys_count="").keys_count is None
    assert P.ProtocolIn(keys_count=" 3 ").keys_count == 3
    for schlecht in ("abc", "2 Stück", 21, -1, 1.5, True):
        with pytest.raises(ValidationError):
            P.ProtocolIn(keys_count=schlecht)
    assert P.ProtocolIn(condition={"mileage": "123456"}).condition["mileage"] == 123456
    assert P.ProtocolIn(condition={"mileage": "86.000 km"}).condition["mileage"] == 86000
    assert P.ProtocolIn(condition={"mileage": ""}).condition["mileage"] == ""
    assert P.ProtocolIn(condition={"fuel_level": "voll"}).condition == {"fuel_level": "voll"}
    for schlecht in ("12a", "ca. 72.000", 5_000_001, -5):
        with pytest.raises(ValidationError):
            P.ProtocolIn(condition={"mileage": schlecht})
    # keys_expected kennt das Modell nicht mehr — wird still ignoriert
    assert "keys_expected" not in P.ProtocolIn(keys_expected="9").model_dump(exclude_none=True)


def test_101_keys_expected_vom_server_aus_dem_vertrag(welt):
    w = welt
    P = _m("routes.protocols")
    t = _abholung(w, proto_status="entwurf")
    w.run(w.db.generated_pdfs.update_one({"id": t.ca}, {"$set": {"contract_data.schluessel_anzahl": "3"}}))
    assert w.run(P.get_protocol(t.aid, w.driver))["template"]["keys_expected"] == 3
    # Fahrer schickt 9 mit — der Server nimmt den Vertragswert
    _speichern(w, t, keys_count="0", keys_expected="9")
    doc = _doc(w, "pickup_protocols", t.pid)
    assert doc["keys_count"] == 0 and doc["keys_expected"] == 3
    # 0 Schluessel ist ein Wert: Abschicken geht, Liste zeigt "0" und "3"
    w.run(P.submit_protocol(t.aid, w.driver))
    doc = _doc(w, "pickup_protocols", t.pid)
    assert doc["status"] == P.ZUR_FREIGABE and doc["keys_expected"] == 3
    liste = w.run(P.protokolle_zur_freigabe(user=w.chef))
    e = next(x for x in liste if x["protocol_id"] == t.pid)
    assert e["schluessel"] == "0" and e["schluessel_vereinbart"] == "3"
    # Altbestand: String-Werte lesen weiter
    w.run(w.db.pickup_protocols.update_one({"id": t.pid}, {"$set": {"keys_count": "2", "keys_expected": "2"}}))
    e = next(x for x in w.run(P.protokolle_zur_freigabe(user=w.chef)) if x["protocol_id"] == t.pid)
    assert e["schluessel"] == "2" and e["schluessel_vereinbart"] == "2"
    # ohne Vertragswert: None (kein Fehler)
    assert P.schluessel_vereinbart({}) is None and P.schluessel_vereinbart({"schluessel_anzahl": ""}) is None
    assert P.anzahl_text(0) == "0" and P.anzahl_text(None) == "" and P.anzahl_text("") == ""


def test_114_pdf_druckt_null_schluessel():
    PDF = _m("pickup_pdf_service")
    assert PDF._anzahl(0) == "0" and PDF._anzahl(None) == "_____" and PDF._anzahl("") == "_____"
    assert PDF._anzahl(2) == "2" and PDF._anzahl("2") == "2"


# ============================================================ 106: Abweichungen typgeprueft
@pytest.mark.parametrize("schluessel,wert,ok", [
    ("mileage_contract", "75.200 km", True), ("mileage_contract", "75200", True),
    ("mileage_contract", "ca. 72.000", False), ("mileage_contract", "abc", False),
    ("first_registration", "06/2020", True), ("first_registration", "2020-06", True),
    ("first_registration", "06/20", False), ("first_registration", "2020", False),
    ("vin", "WBA3A5C51CF256789", True), ("vin", "WBA3A5C51CF25678", False),
    ("vin", "WBA3A5C51CF25678I", False),
    ("power", "150 PS", True), ("power", "110 kW / 150 PS", True), ("power", "110", True),
    ("power", "viel", False), ("power", "150,5 PS", False),
    ("previous_owners", "2", True), ("previous_owners", "zwei", False), ("previous_owners", "123", False),
    ("color", "Schwarz metallic", True), ("color", "x" * 61, False),
    ("hu", "03/2027", True), ("hu", "keine HU", True), ("hu", "bald", False),
    ("commercial", "", True),
])
def test_106_abweichung_pruefen(schluessel, wert, ok):
    P = _m("routes.protocols")
    fehler = P.abweichung_pruefen(schluessel, schluessel, wert)
    assert (fehler is None) == ok, (schluessel, wert, fehler)


def test_106_abschicken_mit_falschem_typ(welt):
    w = welt
    P = _m("routes.protocols")
    t = _abholung(w, proto_status="entwurf")
    vc = {k: {"status": o[0]} for k, _l, o in P.VEHICLE_CHECK_FIELDS}
    vc["mileage_contract"] = {"status": "weicht ab", "value": "ungefähr viel"}
    vc["vin"] = {"status": "weicht ab", "value": "KURZ"}
    w.run(w.db.pickup_protocols.update_one({"id": t.pid}, {"$set": {"vehicle_check": vc}}))
    code, text = _fehler(w, P.submit_protocol(t.aid, w.driver))
    assert code == 400 and "KM-Stand laut Vertrag" in text and "FIN" in text and "ohne I, O und Q" in text
    vc["mileage_contract"] = {"status": "weicht ab", "value": "75.200 km"}
    vc["vin"] = {"status": "weicht ab", "value": "wba3a5c51cf256789"}
    w.run(w.db.pickup_protocols.update_one({"id": t.pid}, {"$set": {"vehicle_check": vc}}))
    assert w.run(P.submit_protocol(t.aid, w.driver))["status"] == P.ZUR_FREIGABE


# ============================================================ 107-110: Vorlage
def test_107_fremde_schluessel_werden_verworfen(welt):
    w = welt
    P = _m("routes.protocols")
    t = _abholung(w, proto_status="entwurf")
    w.run(w.db.vehicles.update_one({"id": t.vid}, {"$set": {"data.features": ["Sitzheizung", "Navi"]}}))
    body = P.ProtocolIn(documents={P.DOCUMENT_ITEMS[0]: True, "Zulassungsbescheinigung Teil II": False},
                        features={"Sitzheizung": True, "Panoramadach": "fehlt"})
    w.run(P.save_protocol(t.aid, body, w.driver))
    doc = _doc(w, "pickup_protocols", t.pid)
    assert doc["documents"] == {P.DOCUMENT_ITEMS[0]: True}
    assert doc["features"] == {"Sitzheizung": True}
    payload = {"documents": {"x": True}, "features": {"Navi": True, "y": True}}
    P.vorlage_filtern(payload, {"features": ["Navi"]})
    assert payload == {"documents": {}, "features": {"Navi": True}}


# ============================================================ 125/126: Frage des Chefs
def test_125_frage_braucht_optionen_oder_freitext():
    P = _m("routes.protocols")
    with pytest.raises(ValidationError):
        P.FreigabeIn(zurueck=True, rueckfrage_frage={"question": "Lack?"})
    with pytest.raises(ValidationError):
        P.FreigabeIn(zurueck=True, rueckfrage_frage={"question": "Lack?", "options": ["Ja", " Ja "]})
    f = P.FreigabeIn(zurueck=True, rueckfrage_frage={"question": " Lack? ", "options": ["Ja", "Nein", "Ja"]})
    assert f.rueckfrage_frage == {"source_id": "", "question": "Lack?", "options": ["Ja", "Nein"], "freitext": False}
    f = P.FreigabeIn(zurueck=True, rueckfrage_frage={"question": "Was genau?", "freitext": True})
    assert f.rueckfrage_frage["freitext"] is True and f.rueckfrage_frage["options"] == []


def _zur_freigabe(w, **extra):
    P = _m("routes.protocols")
    t = _abholung(w, proto_status="entwurf", **extra)
    w.run(P.submit_protocol(t.aid, w.driver))
    return t


def _stand(w, pid):
    return _doc(w, "pickup_protocols", pid)["freigabe_stand"]


def test_126_source_id_muss_existieren(welt, monkeypatch):
    w = welt
    P = _m("routes.protocols")
    t = _zur_freigabe(w, new_damages=[_schaden(id="s1")])

    async def _ki(protocol_id, dealer_id, **_k):
        return {"status": "ok", "ergebnis": {"items": [{"source_id": "dev:mileage"}]}}
    monkeypatch.setattr(P.KI, "bewertung_lesen", _ki)

    def frage(sid):
        return P.FreigabeIn(zurueck=True, stand=_stand(w, t.pid),
                            rueckfrage_frage={"source_id": sid, "question": "Sicher?", "options": ["Ja", "Nein"]})
    code, text = _fehler(w, P.protokoll_freigeben(t.pid, frage("erfunden"), user=w.chef))
    assert code == 400 and "Position" in text
    assert _doc(w, "pickup_protocols", t.pid)["status"] == P.ZUR_FREIGABE
    for sid in ("s1", "dev:mileage", ""):
        w.run(P.protokoll_freigeben(t.pid, frage(sid), user=w.chef))
        doc = _doc(w, "pickup_protocols", t.pid)
        assert doc["status"] == "entwurf" and doc["rueckfrage_frage"]["source_id"] == sid
        assert doc["rueckfrage_frage"]["frage_id"] and doc["rueckfrage_frage"]["gestellt_am"]
        assert doc["rueckfrage_antworten"] == []
        w.run(w.db.pickup_protocols.update_one({"id": t.pid}, {"$set": {"status": P.ZUR_FREIGABE}}))

    async def _ki_kaputt(*a, **k):
        raise RuntimeError("KI weg")
    monkeypatch.setattr(P.KI, "bewertung_lesen", _ki_kaputt)
    assert _fehler(w, P.protokoll_freigeben(t.pid, frage("dev:mileage"), user=w.chef))[0] == 400
    w.run(P.protokoll_freigeben(t.pid, frage("s1"), user=w.chef))


# ============================================================ 57-62/134: Antworten des Fahrers
def test_57_antworten_gebunden_an_frage_und_server_zeit(welt):
    w = welt
    P = _m("routes.protocols")
    t = _zur_freigabe(w)
    w.run(P.protokoll_freigeben(t.pid, P.FreigabeIn(
        zurueck=True, notiz="Bitte pruefen", stand=_stand(w, t.pid),
        rueckfrage_frage={"question": "Lack beschädigt?", "options": ["Ja", "Nein", "Unklar"]}), user=w.chef))
    fid = _doc(w, "pickup_protocols", t.pid)["rueckfrage_frage"]["frage_id"]
    # 59: Abschicken ohne Antwort
    code, text = _fehler(w, P.submit_protocol(t.aid, w.driver))
    assert code == 400 and text == P.RUECKFRAGE_OFFEN
    # fremde frage_id / erfundene Frage -> verworfen
    _speichern(w, t, rueckfrage_antworten=[
        {"frage_id": "x", "source_id": "d9", "question": "Panoramadach kaputt?", "answer": "Ja"}])
    assert _doc(w, "pickup_protocols", t.pid)["rueckfrage_antworten"] == []
    # passende frage_id, aber keine angebotene Antwort -> 400
    with pytest.raises(HTTPException) as e:
        _speichern(w, t, rueckfrage_antworten=[{"frage_id": fid, "answer": "Vielleicht"}])
    code, text = e.value.status_code, e.value.detail
    assert code == 400 and "Ja, Nein, Unklar" in text
    # gueltig: source_id/question kommen von der Frage, "at" vom Server, nur EINE Antwort
    _speichern(w, t, rueckfrage_antworten=[
        {"frage_id": fid, "answer": "Ja", "at": "1999-01-01T00:00:00+00:00"},
        {"frage_id": fid, "answer": "Nein", "at": "1999-01-01T00:00:00+00:00", "source_id": "gefaelscht"}])
    doc = _doc(w, "pickup_protocols", t.pid)
    assert len(doc["rueckfrage_antworten"]) == 1
    a = doc["rueckfrage_antworten"][0]
    assert a["answer"] == "Nein" and a["frage_id"] == fid and a["source_id"] == "" \
        and a["question"] == "Lack beschädigt?" and a["at"].startswith("20")
    assert P.aktuelle_antwort(doc)["answer"] == "Nein"
    # 60-62: Abschicken -> Runde im Verlauf, Frage weg, Antwort bleibt fuer Chef und KI
    w.run(P.submit_protocol(t.aid, w.driver))
    doc = _doc(w, "pickup_protocols", t.pid)
    assert "rueckfrage_frage" not in doc and doc["rueckfrage_antworten"][0]["answer"] == "Nein"
    assert len(doc["rueckfrage_verlauf"]) == 1
    assert doc["rueckfrage_verlauf"][0]["frage"]["frage_id"] == fid
    assert doc["rueckfrage_verlauf"][0]["antworten"][0]["answer"] == "Nein"
    e = next(x for x in w.run(P.protokolle_zur_freigabe(user=w.chef)) if x["protocol_id"] == t.pid)
    assert e["rueckfrage_antworten"][0]["answer"] == "Nein" and len(e["rueckfrage_verlauf"]) == 1
    # zweite Runde ohne Frage: alte Antwort bleibt (Historie), App darf sie nicht ueberschreiben
    w.run(P.protokoll_freigeben(t.pid, P.FreigabeIn(zurueck=True, notiz="Ort?", stand=_stand(w, t.pid)), user=w.chef))
    _speichern(w, t, rueckfrage_antworten=[{"frage_id": fid, "answer": "Ja"}])
    doc = _doc(w, "pickup_protocols", t.pid)
    assert doc["rueckfrage_antworten"][0]["answer"] == "Nein" and "rueckfrage_frage" not in doc
    w.run(P.submit_protocol(t.aid, w.driver))
    assert len(_doc(w, "pickup_protocols", t.pid)["rueckfrage_verlauf"]) == 1
    # dritte Runde mit neuer Frage (Freitext): Antworten leer, Freitext geht
    w.run(P.protokoll_freigeben(t.pid, P.FreigabeIn(
        zurueck=True, stand=_stand(w, t.pid),
        rueckfrage_frage={"question": "Was genau?", "freitext": True}), user=w.chef))
    doc = _doc(w, "pickup_protocols", t.pid)
    fid2 = doc["rueckfrage_frage"]["frage_id"]
    assert fid2 != fid and doc["rueckfrage_antworten"] == []
    _speichern(w, t, rueckfrage_antworten=[{"frage_id": fid2, "answer": "Kratzer bis aufs Blech, 20 cm"}])
    w.run(P.submit_protocol(t.aid, w.driver))
    doc = _doc(w, "pickup_protocols", t.pid)
    assert doc["rueckfrage_antworten"][0]["answer"] == "Kratzer bis aufs Blech, 20 cm"
    assert [r["frage"]["frage_id"] for r in doc["rueckfrage_verlauf"]] == [fid, fid2]


def test_58_altfrage_ohne_frage_id_ueber_source_und_question():
    P = _m("routes.protocols")
    frage = {"source_id": "d1", "question": "Lack?", "options": ["Ja", "Nein"]}
    assert P.antwort_passt(frage, {"source_id": "d1", "question": "Lack?", "answer": "Ja"})
    assert not P.antwort_passt(frage, {"source_id": "d2", "question": "Lack?", "answer": "Ja"})
    assert not P.antwort_passt({**frage, "frage_id": "f1"}, {"source_id": "d1", "question": "Lack?"})
    assert P.antwort_passt({**frage, "frage_id": "f1"}, {"frage_id": "f1"})
    raus = P.antworten_zur_frage(frage, [{"source_id": "d1", "question": "Lack?", "answer": "Ja"}])
    assert raus[0]["answer"] == "Ja" and raus[0]["frage_id"] == "" and raus[0]["at"]
    assert P.antworten_zur_frage(frage, [{"source_id": "d1", "question": "Lack?", "answer": " "}]) == []


# ============================================================ 121-124/135: Korrektur
def test_121_korrektur_ohne_rueckfrage_felder(welt):
    w = welt
    P = _m("routes.protocols")
    t = _abholung(w, proto_status="final", pdf_path="p.pdf", finalized_at="2026-09-26T10:00:00+00:00",
                  rueckfrage_frage={"question": "Lack?", "options": ["Ja", "Nein"], "frage_id": "f1"},
                  rueckfrage_antworten=[{"frage_id": "f1", "answer": "Ja"}],
                  rueckfrage_verlauf=[{"frage": {"frage_id": "f0"}, "antworten": []}])
    neu = w.run(P.start_correction(t.aid, w.driver))
    for feld in ("rueckfrage_frage", "rueckfrage_antworten", "rueckfrage_verlauf", "rueckfrage"):
        assert feld not in neu, feld
    alt = _doc(w, "pickup_protocols", t.pid)
    assert alt["superseded"] and alt["rueckfrage_frage"]["frage_id"] == "f1" and len(alt["rueckfrage_verlauf"]) == 1


# ============================================================ 128/129/133: Fahrer-Whitelist
def test_128_fahrer_sieht_keine_verkaeuferdaten_des_inserats(welt):
    w = welt
    P = _m("routes.protocols")
    t = _abholung(w, proto_status="entwurf")
    w.run(w.db.vehicles.update_one({"id": t.vid}, {"$set": {"data": {
        "make_label": "BMW", "model_label": "320d", "model_description": "M Sport", "mileage": 86000,
        "first_registration": "03/2019", "vin": "WBA3A5C51CF256789", "color": "Schwarz",
        "fuel_label": "Diesel", "gearbox_label": "Automatik", "power_kw": 140, "power_ps": 190,
        "doors": "4/5", "features": ["Navi"], "damages": [{"zone": "Tür"}], "images": ["a.jpg"],
        "hu": "03/2027", "previous_owners": "2", "schluessel_anzahl": "2",
        "seller_name": "Max Privat", "seller_address": "Geheimweg 1", "seller_zip": "30159",
        "seller_city": "Hannover", "seller_phone": "0170 1234567", "seller_email": "max@privat.de",
        "seller_ansprechpartner": "Herr X", "seller_type": "privat",
        "description": "Anrufen unter 0170 1234567", "detail_url": "https://x", "list_price": 9000}}}))
    out = w.run(P.get_protocol(t.aid, w.driver))
    veh = out["vehicle"]
    assert not [k for k in veh if k.startswith("seller")], veh
    for k in ("description", "detail_url", "list_price"):
        assert k not in veh
    for k in ("make_label", "model_label", "model_description", "mileage", "first_registration", "vin", "color",
              "fuel_label", "gearbox_label", "power_kw", "doors", "features", "damages", "images", "hu",
              "previous_owners", "schluessel_anzahl"):
        assert k in veh, k
    assert out["template"]["features"] == ["Navi"]
    # KI-Welle Nr. 111/116: Schaeden kommen zusammengefuehrt (Vertrag + Inserat, ai.bekannte_schaeden) an
    assert [d["zone"] for d in out["damages"]] == ["Tür"] and out["damages"][0]["quelle"] == "inserat"
    assert out["appointment"]["seller_name"] == "Vera" and out["appointment"]["pickup_address"] == "Teststr. 1"
    assert P.fahrzeug_fuer_fahrer(None) == {}


# ============================================================ Entscheidungen Ahmad 26.09.2026
def test_e2_schluessel_vereinbart_fehlt_template_und_liste(welt):
    """Entscheidung 2: fehlt die Schluesselanzahl im Vertrag, blockiert nichts —
    die Fahrer-App bekommt schluessel_vereinbart_fehlt=True (zeigt "nicht im
    Vertrag hinterlegt"), die Freigabe-Liste den Merker fuer "bitte nachtragen"."""
    w = welt
    P = _m("routes.protocols")
    t = _abholung(w, proto_status="entwurf")
    tpl = w.run(P.get_protocol(t.aid, w.driver))["template"]
    assert tpl["keys_expected"] is None and tpl["schluessel_vereinbart_fehlt"] is True
    # Fahrer traegt nur die erhaltene Anzahl ein, Abschicken geht
    _speichern(w, t, keys_count="1")
    w.run(P.submit_protocol(t.aid, w.driver))
    doc = _doc(w, "pickup_protocols", t.pid)
    assert doc["status"] == P.ZUR_FREIGABE and doc["keys_count"] == 1 and doc["keys_expected"] is None
    e = next(x for x in w.run(P.protokolle_zur_freigabe(user=w.chef)) if x["protocol_id"] == t.pid)
    assert e["schluessel"] == "1" and e["schluessel_vereinbart"] == "" and e["schluessel_vereinbart_fehlt"] is True
    # mit Vertragswert: kein Merker
    w.run(w.db.generated_pdfs.update_one({"id": t.ca}, {"$set": {"contract_data.schluessel_anzahl": "2"}}))
    assert w.run(P.get_protocol(t.aid, w.driver))["template"]["schluessel_vereinbart_fehlt"] is False
    w.run(w.db.pickup_protocols.update_one({"id": t.pid}, {"$set": {"keys_expected": 2}}))
    e = next(x for x in w.run(P.protokolle_zur_freigabe(user=w.chef)) if x["protocol_id"] == t.pid)
    assert e["schluessel_vereinbart"] == "2" and e["schluessel_vereinbart_fehlt"] is False


def test_e3_rueckfrage_bezug_vom_server_und_liste_offener_rueckfragen(welt, monkeypatch):
    """Entscheidung 3: der Server setzt den Bezugstext (source_label) der
    Rueckfrage aus Schaden bzw. KI-Position; GET /protocols/rueckfragen-offen
    zeigt dem Chef, was beim Fahrer liegt — bis der erneut abschickt."""
    w = welt
    P = _m("routes.protocols")
    t = _zur_freigabe(w, new_damages=[_schaden(id="s1", type_label="Delle", zone="Tür vorne links")])

    async def _ki(protocol_id, dealer_id, **_k):
        return {"status": "ok", "ergebnis": {"items": [{"source_id": "dev:mileage", "title": "Kilometer weichen ab"}]}}
    monkeypatch.setattr(P.KI, "bewertung_lesen", _ki)
    assert w.run(P.protokolle_rueckfragen_offen(user=w.chef)) == []

    def frage(sid, **extra):
        return P.FreigabeIn(zurueck=True, stand=_stand(w, t.pid), notiz="Bitte prüfen",
                            rueckfrage_frage={"source_id": sid, "question": "Wie tief?",
                                              "options": ["oberflächlich", "bis aufs Blech"], **extra})
    w.run(P.protokoll_freigeben(t.pid, frage("s1"), user=w.chef))
    doc = _doc(w, "pickup_protocols", t.pid)
    assert doc["rueckfrage_frage"]["source_label"] == "Schaden: Delle · Tür vorne links"
    # der Client kann den Bezugstext nicht setzen (Validator wirft ihn weg)
    assert "source_label" not in P.FreigabeIn(zurueck=True, rueckfrage_frage={
        "question": "x", "options": ["a", "b"], "source_label": "erfunden"}).rueckfrage_frage
    # Fahrer-App sieht Frage samt Bezug
    app = w.run(P.get_protocol(t.aid, w.driver))["protocol"]
    assert app["rueckfrage_frage"]["source_label"] == "Schaden: Delle · Tür vorne links"
    # Liste offener Rueckfragen: Protokoll liegt beim Fahrer
    offen = w.run(P.protokolle_rueckfragen_offen(user=w.chef))
    e = next(x for x in offen if x["protocol_id"] == t.pid)
    assert e["status"] == "entwurf" and e["rueckfrage"] == "Bitte prüfen" and e["rueckfrage_am"]
    assert e["rueckfrage_frage"]["question"] == "Wie tief?" and e["rueckfrage_antworten"] == []
    assert e["neue_schaeden"] == [{"id": "s1", "bezeichnung": "Delle · Tür vorne links"}]
    assert e["fahrzeug"] == "BMW 320d" and e["fahrer"] == w.driver["display_name"]
    assert e["rueckfrage_von_name"]
    # Fahrer speichert die Antwort (noch nicht abgeschickt): Chef sieht sie schon
    fid = doc["rueckfrage_frage"]["frage_id"]
    _speichern(w, t, rueckfrage_antworten=[{"frage_id": fid, "answer": "bis aufs Blech"}])
    e = next(x for x in w.run(P.protokolle_rueckfragen_offen(user=w.chef)) if x["protocol_id"] == t.pid)
    assert e["rueckfrage_antworten"][0]["answer"] == "bis aufs Blech"
    # nicht in der Warteliste, aber nach dem Abschicken wieder — und aus der Rueckfragen-Liste raus
    assert t.pid not in [x["protocol_id"] for x in w.run(P.protokolle_zur_freigabe(user=w.chef))]
    w.run(P.submit_protocol(t.aid, w.driver))
    assert t.pid not in [x["protocol_id"] for x in w.run(P.protokolle_rueckfragen_offen(user=w.chef))]
    e = next(x for x in w.run(P.protokolle_zur_freigabe(user=w.chef)) if x["protocol_id"] == t.pid)
    assert e["rueckfrage_antworten"][0]["answer"] == "bis aufs Blech"
    assert e["rueckfrage_verlauf"][0]["frage"]["source_label"] == "Schaden: Delle · Tür vorne links"
    # KI-Position als Bezug, Freitext-Frage
    w.run(P.protokoll_freigeben(t.pid, P.FreigabeIn(
        zurueck=True, stand=_stand(w, t.pid),
        rueckfrage_frage={"source_id": "dev:mileage", "question": "Was genau?", "freitext": True}), user=w.chef))
    doc = _doc(w, "pickup_protocols", t.pid)
    assert doc["rueckfrage_frage"]["source_label"] == "KI-Position: Kilometer weichen ab"
    # allgemeine Frage: kein Bezug
    w.run(w.db.pickup_protocols.update_one({"id": t.pid}, {"$set": {"status": P.ZUR_FREIGABE}}))
    w.run(P.protokoll_freigeben(t.pid, frage(""), user=w.chef))
    assert _doc(w, "pickup_protocols", t.pid)["rueckfrage_frage"]["source_label"] == ""
    # geschlossener Termin: faellt aus der Liste
    w.run(w.db.appointments.update_one({"id": t.aid}, {"$set": {"status": "storniert"}}))
    assert t.pid not in [x["protocol_id"] for x in w.run(P.protokolle_rueckfragen_offen(user=w.chef))]
    assert P.schaden_bezeichnung({"type_label": "Kratzer"}) == "Kratzer" and P.schaden_bezeichnung({}) == "Schaden"
