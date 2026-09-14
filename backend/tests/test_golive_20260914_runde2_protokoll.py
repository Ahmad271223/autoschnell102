# -*- coding: utf-8 -*-
"""Pruefung 14.09.2026, zweite Liste — Protokoll, Termin, Fahrer.

  P1   Freigabe ist an den Vertrags-/Fahrzeugstand gebunden: aendert sich er
       nach der Freigabe, geht das Protokoll zurueck in den Entwurf (409).
  P2   Ort und Verkaeufername sind beim Abschicken Pflicht und beim
       Unterschreiben nicht mehr aenderbar.
  P3   Finales PDF traegt eine Pruefsumme; Abweichung beim Abruf -> 409 + Alarm.
  P4   Korrektur uebernimmt das Fahrzeug des TERMINS.
  P5   Selbstheilung erkennt auch einen Fahrzeugwechsel.
  P6/P7 Fahrer-Notizen werden atomar angehaengt (keine verlorene Chef-Notiz).
  P10  Neuer Fahrer bekommt nicht das PDF des Vorgaengers.
  P11  Verkaeufer/Anschrift/Termin waehrend Freigabe/Abschluss gesperrt.
  F1/F2 Geloeschter Termin waehrend Abschicken/Abschluss.
  F3   Vertrag mit laufendem Protokoll nicht loeschbar.
  F5   Bewusst wiedergeoeffneter Termin springt nicht per Doppeltipp zurueck.
  Wunsch Ahmad 14.09.2026: alle Abschnitte Pflicht; Preisvorschlag und
       Sondervereinbarung des Fahrers, Chef-Freigabe uebernimmt den Vorschlag.
"""
import hashlib
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from protokoll_daten import vollstaendig  # noqa: E402
from test_golive_20260913_abschluss import (  # noqa: E402,F401  (welt = Fixture)
    _PNG_B64, _abholung, _doc, _fin, _jetzt, _m, welt)


def _fehler(w, coro):
    with pytest.raises(HTTPException) as e:
        w.run(coro)
    return e.value.status_code, e.value.detail


def _in(w, sekunden):
    return (datetime.now(timezone.utc) + timedelta(seconds=sekunden)).isoformat()


# ============================================================ Alles Pflicht + Fahrer-Preis
def test_alle_abschnitte_pflicht_beim_abschicken(welt):
    w = welt
    P = _m("routes.protocols")
    t = _abholung(w, proto_status="entwurf")
    voll = vollstaendig(P)
    faelle = [
        ({"documents": {**voll["documents"], P.DOCUMENT_ITEMS[0]: None}}, "Abschnitt 2"),
        ({"keys_expected": ""}, "vereinbarten"),
        ({"condition": {**voll["condition"], "battery": ""}}, "Abschnitt 4"),
        ({"condition": {**voll["condition"], "battery": "kaputt"}}, "ungültige"),
        ({"place": ""}, "Ort"),
        ({"seller_name": ""}, "Verkäufer"),
    ]
    w.run(w.db.appointments.update_one({"id": t.aid}, {"$set": {"seller_name": ""}}))
    for aenderung, erwartet in faelle:
        w.run(w.db.pickup_protocols.update_one({"id": t.pid}, {"$set": {**voll, **aenderung}}))
        code, text = _fehler(w, P.submit_protocol(t.aid, w.driver))
        assert code == 422 and erwartet in text, (aenderung, text)
    # Ausstattung laut Inserat: jede Zeile Ja/Nein
    w.run(w.db.vehicles.update_one({"id": t.vid}, {"$set": {"data.features": ["Klima", "AHK"]}}))
    w.run(w.db.pickup_protocols.update_one({"id": t.pid}, {"$set": {**voll, "features": {"Klima": True}}}))
    code, text = _fehler(w, P.submit_protocol(t.aid, w.driver))
    assert code == 422 and "AHK" in text
    w.run(w.db.pickup_protocols.update_one(
        {"id": t.pid}, {"$set": {**voll, "features": {"Klima": True, "AHK": False}}}))
    assert w.run(P.submit_protocol(t.aid, w.driver))["status"] == P.ZUR_FREIGABE
    p = _doc(w, "pickup_protocols", t.pid)
    assert p["seller_name"] == "Vera" and p["place"] == "Hannover" and p["vertragswerte_stand"]


def test_fahrer_preisvorschlag_und_sondervereinbarung(welt, monkeypatch):
    w = welt
    P = _m("routes.protocols")
    t = _abholung(w, proto_status="entwurf", preis_vorschlag=8700.0, neuer_preis=None,
                  sondervereinbarung="Winterreifen werden nachgeliefert")
    w.run(P.submit_protocol(t.aid, w.driver))
    e = next(x for x in w.run(P.protokolle_zur_freigabe(w.chef)) if x["protocol_id"] == t.pid)
    assert e["preis_vorschlag_fahrer"] == 8700.0 and "Winterreifen" in e["sondervereinbarung"]
    # Chef gibt OHNE eigenen Preis frei -> der Vorschlag des Fahrers gilt
    fr = w.run(P.protokoll_freigeben(t.pid, P.FreigabeIn(stand=e["stand"]), w.chef))
    assert fr["neuer_preis"] == 8700.0
    p = _doc(w, "pickup_protocols", t.pid)
    assert p["neuer_preis"] == 8700.0 and p["preis_quelle"] == "fahrer"
    # Die Sondervereinbarung landet im PDF (filled)
    PDF = _m("pickup_pdf_service")
    gesehen = {}

    def _pdf(**k):
        gesehen.update(k.get("filled") or {})
        return b"%PDF-1.4 test"
    monkeypatch.setattr(PDF, "build_pickup_pdf", _pdf)
    assert w.run(P.finalize_protocol(t.aid, _fin(P, 8700.0, fr["stand"]), w.driver))["ok"]
    assert "Winterreifen" in gesehen.get("sondervereinbarung", "")
    kv = _doc(w, "kaufvorgaenge", t.ka)
    assert kv["purchase_price"] == 8700.0
    # Vorschlag 0 = kein Vorschlag; Chef-Preis hat Vorrang
    t2 = _abholung(w, name="b", proto_status="entwurf", preis_vorschlag=0, neuer_preis=None)
    w.run(P.submit_protocol(t2.aid, w.driver))
    e2 = next(x for x in w.run(P.protokolle_zur_freigabe(w.chef)) if x["protocol_id"] == t2.pid)
    assert e2["preis_vorschlag_fahrer"] is None
    fr2 = w.run(P.protokoll_freigeben(t2.pid, P.FreigabeIn(stand=e2["stand"]), w.chef))
    assert fr2["neuer_preis"] is None


# ============================================================ P1 / P2
def test_p1_vertragsaenderung_nach_freigabe_macht_freigabe_hinfaellig(welt):
    w = welt
    P = _m("routes.protocols")
    t = _abholung(w, proto_status="entwurf")
    w.run(P.submit_protocol(t.aid, w.driver))
    stand = _doc(w, "pickup_protocols", t.pid)["freigabe_stand"]
    fr = w.run(P.protokoll_freigeben(t.pid, P.FreigabeIn(neuer_preis=9000, stand=stand), w.chef))
    # Im Buero wird der Vertragspreis geaendert ...
    w.run(w.db.generated_pdfs.update_one({"id": t.ca},
                                         {"$set": {"contract_data.purchase_price": 12000.0}}))
    code, text = _fehler(w, P.finalize_protocol(t.aid, _fin(P, 9000.0, fr["stand"]), w.driver))
    assert code == 409 and "Vertrag" in text
    p = _doc(w, "pickup_protocols", t.pid)
    assert p["status"] == "entwurf" and "vertragswerte_stand" not in p and p["rueckfrage"]
    # Fahrer schickt neu ab, Chef gibt neu frei -> jetzt geht es
    w.run(P.submit_protocol(t.aid, w.driver))
    stand = _doc(w, "pickup_protocols", t.pid)["freigabe_stand"]
    fr = w.run(P.protokoll_freigeben(t.pid, P.FreigabeIn(neuer_preis=9000, stand=stand), w.chef))
    assert w.run(P.finalize_protocol(t.aid, _fin(P, 9000.0, fr["stand"]), w.driver))["ok"]


def test_p2_ort_und_verkaeufer_beim_unterschreiben_nicht_mehr_aenderbar(welt, monkeypatch):
    w = welt
    P = _m("routes.protocols")
    t = _abholung(w)                       # freigegeben, place Hannover, seller Vera
    PDF = _m("pickup_pdf_service")
    gesehen = {}

    def _pdf(**k):
        gesehen.update(k.get("filled") or {})
        return b"%PDF-1.4 test"
    monkeypatch.setattr(PDF, "build_pickup_pdf", _pdf)
    fin = P.FinalizeIn(signature_driver_b64=_PNG_B64, signature_seller_b64=_PNG_B64,
                       seller_name="Jemand anderes", place="Woanders",
                       neuer_preis_gesehen=9000.0, freigabe_stand_gesehen="s1")
    assert w.run(P.finalize_protocol(t.aid, fin, w.driver))["ok"]
    assert gesehen["place"] == "Hannover" and gesehen["seller_name"] == "Vera"
    p = _doc(w, "pickup_protocols", t.pid)
    assert p["place"] == "Hannover" and p["seller_name"] == "Vera"


# ============================================================ P3
def test_p3_final_pdf_hat_pruefsumme_und_abweichung_wird_erkannt(welt, monkeypatch):
    w = welt
    P = _m("routes.protocols")
    SS = _m("storage_service")
    t = _abholung(w)
    assert w.run(P.finalize_protocol(t.aid, _fin(P), w.driver))["ok"]
    p = _doc(w, "pickup_protocols", t.pid)
    assert p["pdf_sha256"] == hashlib.sha256(b"%PDF-1.4 test").hexdigest()
    inhalt = {"daten": b"%PDF-1.4 test"}

    async def _load(key):
        return inhalt["daten"]
    monkeypatch.setattr(SS, "load_async", _load)
    r = w.run(P.driver_protocol_pdf(t.aid, w.driver))
    assert r.status_code == 200 and r.headers["x-protokoll-sha256"] == p["pdf_sha256"]
    inhalt["daten"] = b"%PDF-1.4 manipuliert"
    code, text = _fehler(w, P.dealer_protocol_pdf(t.pid, w.chef))
    assert code == 409 and "Prüfsumme" in text
    assert w.run(w.db.betriebsalarme.find_one(
        {"typ": "protokoll_pdf_pruefsumme_abweichend", "ref": t.pid, "offen": True}))


# ============================================================ P4 / P5 / F5 / P10
def test_p4_p5_fahrzeugwechsel_korrektur_und_selbstheilung(welt):
    w = welt
    P = _m("routes.protocols")
    t = _abholung(w, proto_status="final", pdf_path="x", finalized_at=_jetzt(),
                  contract_id=None)
    w.run(w.db.pickup_protocols.update_one({"id": t.pid}, {"$set": {"contract_id": t.ca}}))
    # Chef oeffnet den Termin wieder und haengt ihn an Fahrzeug 2
    w.run(w.db.appointments.update_one(
        {"id": t.aid}, {"$set": {"vehicle_id": t.vid2, "status_changed_at": _in(w, 5)}}))
    # P5: ein alter Finalize-Retry heilt NICHT (Fahrzeug anders)
    r = w.run(P.finalize_protocol(t.aid, _fin(P), w.driver))
    assert r.get("nachgezogen") is True and r.get("hinweis") == P.TERMIN_UMGEHAENGT_HINWEIS
    assert _doc(w, "appointments", t.aid)["status"] == "offen"
    # P4: die Korrektur traegt das Fahrzeug des Termins
    v2 = w.run(P.start_correction(t.aid, w.driver))
    assert v2["vehicle_id"] == t.vid2


def test_f5_wiedergeoeffneter_termin_springt_nicht_zurueck(welt):
    w = welt
    P = _m("routes.protocols")
    t = _abholung(w, proto_status="final", pdf_path="x", finalized_at=_jetzt())
    # Termin wurde nach dem Abschluss bewusst wieder geoeffnet
    w.run(w.db.appointments.update_one(
        {"id": t.aid}, {"$set": {"status": "offen", "status_changed_at": _in(w, 5)}}))
    r = w.run(P.finalize_protocol(t.aid, _fin(P), w.driver))
    assert r["nachgezogen"] is False and r["hinweis"] == P.TERMIN_WIEDER_OFFEN_HINWEIS
    assert _doc(w, "appointments", t.aid)["status"] == "offen"
    # Echte Selbstheilung (Termin-Write war beim Abschluss gescheitert): heilt
    w.run(w.db.appointments.update_one(
        {"id": t.aid}, {"$set": {"status_changed_at": "2000-01-01T00:00:00+00:00"}}))
    r = w.run(P.finalize_protocol(t.aid, _fin(P), w.driver))
    assert r["nachgezogen"] is True and "hinweis" not in r
    assert _doc(w, "appointments", t.aid)["status"] == "abgeholt"


def test_p10_neuer_fahrer_bekommt_nicht_das_pdf_des_vorgaengers(welt, monkeypatch):
    w = welt
    P = _m("routes.protocols")
    SS = _m("storage_service")

    async def _load(key):
        return b"%PDF-1.4 x"
    monkeypatch.setattr(SS, "load_async", _load)
    t = _abholung(w, proto_status="final", pdf_path="x", finalized_at=_jetzt(),
                  pdf_sha256=hashlib.sha256(b"%PDF-1.4 x").hexdigest())
    assert w.run(P.driver_protocol_pdf(t.aid, w.driver)).status_code == 200
    w.run(w.db.appointments.update_one({"id": t.aid}, {"$set": {"driver_id": w.driver2["id"]}}))
    assert _fehler(w, P.driver_protocol_pdf(t.aid, w.driver2))[0] == 404


# ============================================================ P6 / P7
def test_p6_p7_fahrer_notiz_ueberschreibt_keine_chef_notiz(welt, monkeypatch):
    w = welt
    D = _m("routes.drivers")
    t = _abholung(w, proto_status="entwurf")
    w.run(w.db.appointments.update_one({"id": t.aid}, {"$set": {"zuteilung": "offen", "notes": "alt"}}))
    echt = D._zugriff_pruefen

    async def _dazwischen(appt, driver):
        # Der Chef schreibt GENAU zwischen Lesen und Schreiben des Fahrers
        await w.db.appointments.update_one({"id": appt["id"]}, {"$set": {"notes": "Chef: neu"}})
        return await echt(appt, driver)
    monkeypatch.setattr(D, "_zugriff_pruefen", _dazwischen)
    r = w.run(D.driver_zuteilung(t.aid, D.DriverZuteilungIn(action="ablehnen", grund="krank"), w.driver))
    assert r["zuteilung"] == "abgelehnt"
    a = _doc(w, "appointments", t.aid)
    assert a["notes"] == "Chef: neu\n[Fahrer] Fahrt abgelehnt: krank", a["notes"]
    assert "driver_id" not in a
    # Statuswechsel mit Notiz: dasselbe
    monkeypatch.setattr(D, "_zugriff_pruefen", echt)
    w.run(w.db.appointments.update_one(
        {"id": t.aid}, {"$set": {"driver_id": w.driver["id"], "zuteilung": "angenommen", "notes": ""}}))
    monkeypatch.setattr(D, "_zugriff_pruefen", _dazwischen)
    w.run(D.driver_set_status(t.aid, D.DriverStatusIn(status="nicht abgeholt", notes="keiner da"),
                              w.driver))
    a = _doc(w, "appointments", t.aid)
    assert a["status"] == "nicht abgeholt" and a["notes"] == "Chef: neu\n[Fahrer] keiner da", a["notes"]


# ============================================================ P11 / F3
def test_p11_termindaten_waehrend_freigabe_gesperrt(welt):
    w = welt
    A = _m("routes.appointments")
    t = _abholung(w, proto_status="zur_freigabe")
    for felder in ({"seller_name": "Anders"}, {"pickup_address": "Neu 1"},
                   {"pickup_date": "2099-09-11"}, {"pickup_time": "11:00"}):
        code, _ = _fehler(w, A.update_appointment(t.aid, A.AppointmentIn(**felder), w.chef))
        assert code == 409, felder
    # unveraenderte Werte (Oberflaeche schickt das ganze Objekt) und Notizen gehen
    assert w.run(A.update_appointment(
        t.aid, A.AppointmentIn(seller_name="Vera", pickup_address="Teststr. 1", notes="ok"), w.chef))


def test_f3_vertrag_mit_laufendem_protokoll_nicht_loeschbar(welt):
    w = welt
    C = _m("routes.contracts")
    t = _abholung(w, proto_status="freigegeben")
    code, text = _fehler(w, C.delete_contract(t.ca, w.chef))
    assert code == 409 and "Abholprotokoll" in text
    assert _doc(w, "generated_pdfs", t.ca) is not None


# ============================================================ F1 / F2
def test_f1_f2_geloeschter_termin_waehrend_abschicken_und_abschluss(welt, monkeypatch):
    w = welt
    P = _m("routes.protocols")
    # F2: Termin verschwindet zwischen Vorpruefung und Statuswechsel
    t = _abholung(w, proto_status="entwurf")
    echt = P._fahrzeug_und_vertrag

    async def _weg(appt):
        erg = await echt(appt)
        await w.db.appointments.delete_one({"id": t.aid})
        return erg
    monkeypatch.setattr(P, "_fahrzeug_und_vertrag", _weg)
    code, _ = _fehler(w, P.submit_protocol(t.aid, w.driver))
    monkeypatch.setattr(P, "_fahrzeug_und_vertrag", echt)
    assert code == 404 and _doc(w, "pickup_protocols", t.pid)["status"] == "entwurf"
    # F1: Termin verschwindet waehrend des Abschlusses -> Rollback, nicht final
    t2 = _abholung(w, name="f1")

    async def _loeschen(key):
        await w.db.appointments.delete_one({"id": t2.aid})
    w.save_hook = _loeschen
    code, text = _fehler(w, P.finalize_protocol(t2.aid, _fin(P), w.driver))
    w.save_hook = None
    assert code == 409 and "gelöscht" in text
    p = _doc(w, "pickup_protocols", t2.pid)
    assert p["status"] != "final" and "pdf_path" not in p


# ============================================================ Fahrer-Dashboard: 14 / 30 Tage
def test_fahrer_dashboard_blendet_alte_abgeschlossene_fahrten_aus(welt):
    """Wunsch Ahmad 14.09.2026: abgeholt nach 14 Tagen weg, andere abgeschlossene
    nach 30 Tagen; offene bleiben; der Termin selbst bleibt beim Haendler."""
    w = welt
    D = _m("routes.drivers")
    faelle = {"jung_abgeholt": ("abgeholt", 5), "alt_abgeholt": ("abgeholt", 20),
              "jung_storno": ("storniert", 20), "alt_storno": ("nicht abgeholt", 40),
              "offen_alt": ("offen", 400)}
    for name, (status, tage) in faelle.items():
        t = _abholung(w, name=name, proto_status="entwurf")
        seit = (datetime.now(timezone.utc) - timedelta(days=tage)).isoformat()
        w.run(w.db.appointments.update_one(
            {"id": t.aid}, {"$set": {"status": status, "abgeschlossen_seit": seit,
                                     "status_changed_at": seit, "updated_at": seit}}))
        faelle[name] = t.aid
    ids = {a["id"] for a in w.run(D.driver_appointments(w.driver))}
    assert faelle["jung_abgeholt"] in ids and faelle["jung_storno"] in ids and faelle["offen_alt"] in ids
    assert faelle["alt_abgeholt"] not in ids and faelle["alt_storno"] not in ids
    # Beim Haendler existieren alle weiter
    assert w.run(w.db.appointments.count_documents({"id": {"$in": list(faelle.values())}})) == 5
