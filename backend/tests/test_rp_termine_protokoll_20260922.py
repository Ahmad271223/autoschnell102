# -*- coding: utf-8 -*-
"""Rollenprüfung 22.09.2026 — Team termine_protokoll (Terminplaner, Abholprotokoll,
Freigabe, Kaufvorgang, Protokoll-PDF).

In-Prozess gegen DB_NAME (Wegwerf-Daten je Test, Welt aus
test_golive_20260913_abschluss: Firma, Chef, zwei Fahrer, Fahrzeug mit Vertrag
A (Termin, Vorgang kA) und Vertrag B (Vorgang kB) — Storage und PDF-Bau sind
dort im Speicher gefaelscht). Die PDF-Tests unten bauen ECHTE PDFs (ohne Welt).

  RP-015/114/265  Termin ohne Vertrag bewegt das gemeinsame Fahrzeug nicht
  RP-027/126/277  Wiederoeffnen + Vertrag/Fahrzeug tauschen gesperrt
  RP-482/075/174  offene Korrektur: klare 409, Chef verwirft, Vorgang bleibt abgeholt
  RP-222/373      deaktivierter (unveraenderter) Fahrer sperrt keine Aenderung
  RP-223/374      gleich geschlossen angelegter Termin traegt die Fristfelder
  RP-516          Standardtitel statt leerem Titel
  RP-454          Termin zu geloeschtem Fahrzeug bleibt aenderbar
  RP-497          Loeschen mit Abholbericht nur nach ausdruecklicher Bestaetigung
  RP-536          derselbe Fahrer behaelt seinen Entwurf
  RP-062/161      Kontaktkorrektur hebt die Zusage nicht auf
  RP-058/157/074/173/082/181  Verkaeufername im Entwurf, in der Korrektur, frisch
  RP-063/162      lange Ausstattungsnamen
  RP-453          verworfener Fahrer-Vorschlag kommt nicht zurueck
  RP-077/176/480  Vergleichsbasis = Preis vor der Abholung
  RP-076/489      Preis und Vertrag nach Handabschluss / Nachholen
  RP-086/185      Hinweis auf aktives Inserat nach geplatztem Kauf
  RP-407          Dateiname mit "Škoda"
  RP-068/071/170/535  PDF: Nein, Unterschrift, Ortszeit, Fusszeile, lange Bemerkung
  RP-488          HU-Korrektur setzt auch hu_valid
"""
import io
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_golive_20260913_abschluss import (  # noqa: E402,F401  (welt = Fixture)
    _abholung, _doc, _fin, _jetzt, _m, welt)


def _put(w, aid, user=None, **felder):
    A = _m("routes.appointments")
    return w.run(A.update_appointment(aid, A.AppointmentIn(**felder), user or w.chef))


def _fehler(w, coro):
    with pytest.raises(HTTPException) as e:
        w.run(coro)
    return e.value.status_code, e.value.detail


def _final(w, t, **extra):
    """Protokoll von t als unterschrieben (final) markieren, Termin/Vorgang abgeholt."""
    w.run(w.db.pickup_protocols.update_one(
        {"id": t.pid}, {"$set": {"status": "final", "contract_id": t.ca, "pdf_path": "x.pdf",
                                 "finalized_at": _jetzt(), **extra}}))


# ======================================================================= RP-015
def test_rp015_termin_ohne_vertrag_bewegt_fahrzeug_mit_vorgaengen_nicht(welt):
    w = welt
    A = _m("routes.appointments")
    t = _abholung(w)
    # Vorgang A ist (wieder) nur Vertrag, das Fahrzeug steht auf "gekauft".
    w.run(w.db.kaufvorgaenge.update_one({"id": t.ka}, {"$set": {"status": "vertrag_erstellt"}}))
    w.run(w.db.vehicles.update_one({"id": t.vid}, {"$set": {"lifecycle": "gekauft"}}))
    neu = w.run(A.create_appointment(A.AppointmentIn(vehicle_id=t.vid, pickup_date="2099-09-11"),
                                     w.chef))
    assert _doc(w, "vehicles", t.vid)["lifecycle"] == "gekauft", "vorher: abholung_geplant"
    r = _put(w, neu["id"], status="nicht abgeholt")
    assert _doc(w, "vehicles", t.vid)["lifecycle"] == "gekauft", "vorher: nicht_abgeholt"
    assert A.FAHRZEUG_VORGAENGE_HINWEIS in r.get("hinweis", ""), r
    r = _put(w, neu["id"], status="abgeholt")
    assert _doc(w, "vehicles", t.vid)["lifecycle"] == "gekauft", "vorher: abgeholt"

    # Gegenprobe: Fahrzeug OHNE Kaufvorgaenge wie bisher direkt.
    w.run(w.db.vehicles.update_one({"id": t.vid2}, {"$set": {"lifecycle": "gekauft"}}))
    neu2 = w.run(A.create_appointment(A.AppointmentIn(vehicle_id=t.vid2), w.chef))
    assert _doc(w, "vehicles", t.vid2)["lifecycle"] == "abholung_geplant"
    r2 = _put(w, neu2["id"], status="nicht abgeholt")
    assert _doc(w, "vehicles", t.vid2)["lifecycle"] == "nicht_abgeholt"
    assert A.FAHRZEUG_VORGAENGE_HINWEIS not in (r2.get("hinweis") or "")


def test_rp114_abschluss_ohne_vertrag_an_fahrzeug_mit_vorgang(welt):
    w = welt
    P = _m("routes.protocols")
    t = _abholung(w, mit_vertrag=False)
    w.run(w.db.kaufvorgaenge.insert_one({
        "id": f"kx_{w.s}", "dealer_id": w.dealer_id, "user_id": w.chef["id"], "vehicle_id": t.vid,
        "contract_id": f"cx_{w.s}", "status": "abholung_geplant", "purchase_price": 5000.0,
        "created_at": _jetzt(), "updated_at": _jetzt()}))
    fertig = w.run(P.finalize_protocol(t.aid, _fin(P), w.driver))
    assert fertig["ok"] and _doc(w, "appointments", t.aid)["status"] == "abgeholt"
    assert _doc(w, "vehicles", t.vid)["lifecycle"] == "abholung_geplant", \
        "der offene Kauf des Kollegen bestimmt das Fahrzeug"


def test_rp015_kaufvorgang_helfer():
    KV = _m("kaufvorgang")
    assert callable(KV.fahrzeug_hat_vorgaenge) and callable(KV.abholung_protokoll_belegt)


# ======================================================================= RP-027
def test_rp027_wiederoeffnen_und_umhaengen_gesperrt(welt):
    w = welt
    A = _m("routes.appointments")
    t = _abholung(w)
    _final(w, t)
    w.run(w.db.appointments.update_one({"id": t.aid}, {"$set": {"status": "abgeholt"}}))
    w.run(w.db.kaufvorgaenge.update_one({"id": t.ka}, {"$set": {"status": "abgeholt"}}))
    for felder in ({"status": "offen", "contract_id": t.cb},
                   {"status": "offen", "fahrzeug_loesen": True},
                   {"status": "offen", "contract_loesen": True}):
        code, text = _fehler(w, A.update_appointment(t.aid, A.AppointmentIn(**felder), w.chef))
        assert code == 409 and "neuen Termin" in text, (felder, text)
    # anderes Fahrzeug: schon die Paarpruefung Vertrag/Fahrzeug lehnt ab
    code, _ = _fehler(w, A.update_appointment(
        t.aid, A.AppointmentIn(status="offen", vehicle_id=t.vid2), w.chef))
    assert code == 409
    termin = _doc(w, "appointments", t.aid)
    assert termin["status"] == "abgeholt" and termin["contract_id"] == t.ca
    assert _doc(w, "kaufvorgaenge", t.ka)["status"] == "abgeholt"
    # Nur Wiederoeffnen geht weiter — Umhaengen danach nicht.
    _put(w, t.aid, status="offen")
    code, _ = _fehler(w, A.update_appointment(t.aid, A.AppointmentIn(contract_id=t.cb), w.chef))
    assert code == 409
    assert _doc(w, "kaufvorgaenge", t.kb)["status"] == "vertrag_erstellt"


def test_rp277_abgeholt_braucht_passenden_beleg_auch_ohne_fahrer(welt):
    w = welt
    A = _m("routes.appointments")
    t = _abholung(w)
    _final(w, t)
    # Wiedergeoeffnet, Fahrer entfernt (eigener PUT)
    _put(w, t.aid, driver_id=None)
    assert not _doc(w, "appointments", t.aid).get("driver_id")
    # Beleg gehoert (simuliert) zu einem anderen Vertrag -> kein "abgeholt"
    w.run(w.db.pickup_protocols.update_one({"id": t.pid}, {"$set": {"contract_id": t.cb}}))
    code, text = _fehler(w, A.update_appointment(t.aid, A.AppointmentIn(status="abgeholt"), w.chef))
    assert code == 409 and "Vertrag" in text, text
    # Passender Beleg: "abgeholt" geht auch ohne Fahrer am Termin
    w.run(w.db.pickup_protocols.update_one({"id": t.pid}, {"$set": {"contract_id": t.ca}}))
    assert _put(w, t.aid, status="abgeholt")["ok"]
    assert _doc(w, "appointments", t.aid)["status"] == "abgeholt"


# ======================================================================= RP-482
def _korrektur_offen(w, t):
    _final(w, t, superseded=True, superseded_at="2000-01-01T00:00:00+00:00")
    w.run(w.db.pickup_protocols.insert_one({
        "id": f"p2_{w.s}", "appointment_id": t.aid, "dealer_id": w.dealer_id,
        "vehicle_id": t.vid, "driver_account_id": w.driver["id"], "version": 2,
        "corrects_version": 1, "status": "entwurf", "superseded": False,
        "created_at": _jetzt()}))
    w.run(w.db.kaufvorgaenge.update_one({"id": t.ka}, {"$set": {"status": "abgeholt"}}))
    return f"p2_{w.s}"


def test_rp482_offene_korrektur_klare_meldung_und_verwerfen(welt):
    w = welt
    A, KV = _m("routes.appointments"), _m("kaufvorgang")
    t = _abholung(w)
    p2 = _korrektur_offen(w, t)
    code, text = _fehler(w, A.update_appointment(t.aid, A.AppointmentIn(status="abgeholt"), w.chef))
    assert code == 409 and "Korrektur verwerfen" in text and "neu laden" not in text, text
    assert _doc(w, "pickup_protocols", p2)["superseded"] is False, "ohne Bestaetigung nichts verworfen"

    # RP-075: der Frischabgleich laesst den Vorgang waehrend der Korrektur abgeholt
    assert w.run(KV.abholung_protokoll_belegt(t.aid)) is True
    w.run(KV.termin_status_uebernehmen({"id": t.aid}, "offen"))
    assert _doc(w, "kaufvorgaenge", t.ka)["status"] == "abgeholt"

    r = _put(w, t.aid, status="abgeholt", korrektur_verwerfen=True)
    assert r["ok"]
    v2, v1 = _doc(w, "pickup_protocols", p2), _doc(w, "pickup_protocols", t.pid)
    assert v2["superseded"] is True and v2.get("verworfen_am")
    assert v1["superseded"] is False, "die unterschriebene Vorversion gilt wieder"
    assert _doc(w, "appointments", t.aid)["status"] == "abgeholt"
    assert _doc(w, "kaufvorgaenge", t.ka)["status"] == "abgeholt"


def test_rp482_korrektur_verwerfen_ist_steuerfeld():
    A = _m("routes.appointments")
    assert "korrektur_verwerfen" in A._STEUERFELDER
    assert "neu laden" not in A.KORREKTUR_OFFEN_HINWEIS
    assert "Korrektur verwerfen" in A.KORREKTUR_OFFEN_HINWEIS


# ======================================================================= RP-222
def test_rp222_deaktivierter_fahrer_sperrt_keine_aenderung(welt):
    w = welt
    A = _m("routes.appointments")
    t = _abholung(w, proto_status="entwurf")
    w.run(w.db.driver_accounts.update_one({"id": w.driver["id"]}, {"$set": {"active": False}}))
    r = _put(w, t.aid, driver_id=w.driver["id"], notes="nur eine Notiz")
    assert r["ok"]
    termin = _doc(w, "appointments", t.aid)
    assert termin["notes"] == "nur eine Notiz" and termin["driver_id"] == w.driver["id"]
    # Einen deaktivierten Fahrer NEU zuteilen bleibt verboten.
    w.run(w.db.driver_accounts.update_one({"id": w.driver2["id"]}, {"$set": {"active": False}}))
    code, text = _fehler(w, A.update_appointment(
        t.aid, A.AppointmentIn(driver_id=w.driver2["id"]), w.chef))
    assert code == 400 and "deaktiviert" in text


# ======================================================================= RP-223 / RP-516
def test_rp223_rp516_anlegen_titel_und_fristfelder(welt):
    w = welt
    A = _m("routes.appointments")
    t = _abholung(w, mit_vertrag=False)
    zu = w.run(A.create_appointment(A.AppointmentIn(title="Werkstatt", status="storniert"), w.chef))
    doc = _doc(w, "appointments", zu["id"])
    assert doc["status_changed_at"] and doc["abgeschlossen_seit"] == doc["status_changed_at"]
    offen = w.run(A.create_appointment(A.AppointmentIn(title="Besichtigung"), w.chef))
    assert "abgeschlossen_seit" not in _doc(w, "appointments", offen["id"])

    mit_auto = w.run(A.create_appointment(A.AppointmentIn(vehicle_id=t.vid2, title=""), w.chef))
    assert _doc(w, "appointments", mit_auto["id"])["title"] == "BMW 320d abholen"
    ohne = w.run(A.create_appointment(A.AppointmentIn(title="   "), w.chef))
    assert _doc(w, "appointments", ohne["id"])["title"] == "Fahrzeug abholen"
    _put(w, ohne["id"], title="")
    assert _doc(w, "appointments", ohne["id"])["title"] == "Fahrzeug abholen"


# ======================================================================= RP-454
def test_rp454_termin_zu_geloeschtem_fahrzeug_bleibt_aenderbar(welt):
    w = welt
    A = _m("routes.appointments")
    t = _abholung(w, mit_vertrag=False)
    w.run(w.db.pickup_protocols.delete_one({"id": t.pid}))
    w.run(w.db.vehicles.update_many({"id": {"$in": [t.vid, t.vid2]}},
                                    {"$set": {"lifecycle": "geloescht"}}))
    r = _put(w, t.aid, vehicle_id=t.vid, notes="bitte stornieren", status="storniert")
    assert r["ok"] and _doc(w, "appointments", t.aid)["status"] == "storniert"
    # Ein NEUER Verweis auf ein geloeschtes Fahrzeug bleibt 404.
    code, _ = _fehler(w, A.update_appointment(t.aid, A.AppointmentIn(vehicle_id=t.vid2), w.chef))
    assert code == 404


# ======================================================================= RP-497
def test_rp497_loeschen_mit_abholbericht_nur_bestaetigt(welt):
    w = welt
    A = _m("routes.appointments")
    t = _abholung(w, mit_vertrag=False)
    w.run(w.db.pickup_protocols.delete_one({"id": t.pid}))
    rid = f"r_{w.s}"
    w.run(w.db.pickup_reports.insert_one({
        "id": rid, "appointment_id": t.aid, "dealer_id": w.dealer_id, "version": 1,
        "deviations": [], "created_at": _jetzt()}))
    try:
        code, text = _fehler(w, A.delete_appointment(t.aid, w.chef))
        assert code == 409 and "Abholbericht des Fahrers" in text
        assert _doc(w, "appointments", t.aid) and _doc(w, "pickup_reports", rid)
        assert w.run(A.delete_appointment(t.aid, w.chef, bericht_loeschen=1))["ok"]
        assert _doc(w, "appointments", t.aid) is None and _doc(w, "pickup_reports", rid) is None
        log = w.run(w.db.activity_logs.find_one({"action": "termin.geloescht", "ref": t.aid}))
        assert log and log["meta"]["bericht_loeschen"] is True
    finally:
        w.run(w.db.pickup_reports.delete_many({"dealer_id": w.dealer_id}))


# ======================================================================= RP-536
def test_rp536_derselbe_fahrer_behaelt_seinen_entwurf(welt):
    w = welt
    t = _abholung(w, mit_vertrag=False, proto_status="entwurf")
    # Der Fahrer hat abgelehnt: Zuteilung weg, Entwurf bleibt.
    w.run(w.db.appointments.update_one({"id": t.aid}, {"$unset": {"driver_id": ""},
                                                       "$set": {"zuteilung": "abgelehnt"}}))
    _put(w, t.aid, driver_id=w.driver["id"])
    assert _doc(w, "pickup_protocols", t.pid) is not None, "Entwurf desselben Fahrers bleibt"
    _put(w, t.aid, driver_id=w.driver2["id"])
    assert _doc(w, "pickup_protocols", t.pid) is None, "anderer Fahrer: Entwurf verworfen"


# ======================================================================= RP-062
def test_rp062_kontaktkorrektur_haelt_die_zusage():
    A = _m("routes.appointments")
    alt = {"zuteilung": "angenommen", "seller_name": "Vera", "seller_phone": "0170 1",
           "seller_email": "vera@e2etest-mail.de"}
    assert A.zusage_zuruecksetzen_wenn_geaendert(
        alt, {"seller_phone": "0171 2", "seller_email": "neu@e2etest-mail.de"}) == ({}, {})
    s, _ = A.zusage_zuruecksetzen_wenn_geaendert(alt, {"seller_name": "Anderer"})
    assert s["zuteilung"] == "offen", "ein anderer Verkaeufer bleibt eine andere Fahrt"


# ======================================================================= RP-058 / RP-074 / RP-082
def test_rp058_verkaeufername_im_entwurf(welt):
    w = welt
    P = _m("routes.protocols")
    t = _abholung(w, mit_vertrag=False, proto_status="entwurf")
    w.run(w.db.appointments.update_one({"id": t.aid}, {"$unset": {"seller_name": ""}}))
    w.run(w.db.pickup_protocols.update_one({"id": t.pid}, {"$unset": {"seller_name": ""}}))
    code, text = _fehler(w, P.submit_protocol(t.aid, w.driver))
    assert code == 422 and "Verkäufers" in text
    gespeichert = w.run(P.save_protocol(t.aid, P.ProtocolIn(seller_name="  Max Muster "), w.driver))
    assert gespeichert["seller_name"] == "Max Muster"
    r = w.run(P.submit_protocol(t.aid, w.driver))
    assert r["status"] == "zur_freigabe"
    assert _doc(w, "pickup_protocols", t.pid)["seller_name"] == "Max Muster"
    with pytest.raises(ValidationError):
        P.ProtocolIn(seller_name="x" * 201)


def test_rp074_korrektur_nimmt_den_namen_vom_termin(welt):
    w = welt
    P = _m("routes.protocols")
    t = _abholung(w)
    _final(w, t)
    # Review 22.09.2026: der Chef aendert den Namen ueber den Termin (PUT setzt den
    # Zeitpunkt seller_name_geaendert_am, an dem die Korrektur das erkennt).
    _put(w, t.aid, seller_name="Vera Neu")
    neu = w.run(P.start_correction(t.aid, w.driver))
    assert neu["seller_name"] == "Vera Neu" and neu["corrects_version"] == 1


def test_rp082_abschicken_nimmt_den_frischen_namen(welt, monkeypatch):
    w = welt
    P = _m("routes.protocols")
    t = _abholung(w, proto_status="entwurf")
    w.run(w.db.pickup_protocols.update_one({"id": t.pid}, {"$unset": {"seller_name": ""}}))
    echt = P._fahrzeug_und_vertrag

    async def dazwischen(appt):
        # der Chef korrigiert den Namen genau zwischen Lesen und Abschicken
        await w.db.appointments.update_one({"id": t.aid}, {"$set": {"seller_name": "Vera Korrigiert"}})
        return await echt(appt)
    monkeypatch.setattr(P, "_fahrzeug_und_vertrag", dazwischen)
    w.run(P.submit_protocol(t.aid, w.driver))
    assert _doc(w, "pickup_protocols", t.pid)["seller_name"] == "Vera Korrigiert"


# ======================================================================= RP-063
def test_rp063_lange_ausstattungsnamen():
    P = _m("routes.protocols")
    name = "Radio DAB+, Bluetooth-Freisprecheinrichtung, Apple CarPlay " + "x" * 150
    assert P.ProtocolIn(features={name: True}).features == {name: True}
    with pytest.raises(ValidationError):
        P.ProtocolIn(features={"x" * (P.FELDNAME_MAX + 1): True})


# ======================================================================= RP-453
def test_rp453_verworfener_vorschlag_kommt_nicht_zurueck(welt):
    w = welt
    P = _m("routes.protocols")
    t = _abholung(w, proto_status="zur_freigabe", neuer_preis=None, preis_vorschlag=8000.0)
    fr = w.run(P.protokoll_freigeben(t.pid, P.FreigabeIn(stand="s1"), w.chef))
    assert fr["neuer_preis"] == 8000.0, "ohne eigenen Preis gilt der Vorschlag"
    z = w.run(P.protokoll_freigeben(t.pid, P.FreigabeIn(preis_zuruecksetzen=True, stand=fr["stand"]),
                                    w.chef))
    doc = _doc(w, "pickup_protocols", t.pid)
    assert doc.get("neuer_preis") is None and doc["preis_vorschlag_verworfen"] is True
    fr2 = w.run(P.protokoll_freigeben(t.pid, P.FreigabeIn(stand=z["stand"]), w.chef))
    assert fr2["neuer_preis"] is None, "Preis aktualisieren bringt den Vorschlag nicht zurueck"
    assert _doc(w, "pickup_protocols", t.pid).get("neuer_preis") is None
    liste = w.run(P.protokolle_zur_freigabe(w.chef))
    eintrag = next(e for e in liste if e["protocol_id"] == t.pid)
    assert eintrag["preis_vorschlag_verworfen"] is True
    # Zurueck an den Fahrer, NEUER Vorschlag -> gilt wieder
    w.run(P.protokoll_freigeben(t.pid, P.FreigabeIn(zurueck=True, stand=fr2["stand"]), w.chef))
    w.run(P.save_protocol(t.aid, P.ProtocolIn(preis_vorschlag=7500.0), w.driver))
    assert not _doc(w, "pickup_protocols", t.pid).get("preis_vorschlag_verworfen")


# ======================================================================= RP-077 / RP-480
def test_rp077_480_vergleichsbasis_ist_der_preis_vor_der_abholung(welt):
    w = welt
    P = _m("routes.protocols")
    t = _abholung(w)
    appt = {"id": t.aid, "dealer_id": w.dealer_id, "contract_id": t.ca,
            "kaufvorgang_id": t.ka, "vehicle_id": t.vid}
    # Nach der ersten Abholung: neue Vertragsfassung 18.000, vorher 20.000
    w.run(w.db.generated_pdfs.update_one(
        {"id": t.ca}, {"$set": {"contract_data.purchase_price": 18000.0,
                                "contract_data.preis_vor_abholung": 20000.0}}))
    assert P.vertragspreis_vor_abholung({"purchase_price": 18000.0,
                                         "preis_vor_abholung": 20000.0}) == 20000.0
    assert P.vertragspreis_vor_abholung({"purchase_price": 18000.0}) == 18000.0
    # a) frischer Vorgang: preis_vorher = Ursprung, nicht der Zwischenstand
    w.run(P._preis_uebernehmen(appt, {"id": t.pid, "neuer_preis": 17000.0}))
    kv = _doc(w, "kaufvorgaenge", t.ka)
    assert kv["purchase_price"] == 17000.0 and kv["preis_vorher"] == 20000.0, kv
    # b) Korrektur mit neuem Preis: ein festgehaltenes preis_vorher bleibt
    w.run(w.db.generated_pdfs.update_one({"id": t.ca},
                                         {"$unset": {"contract_data.preis_vor_abholung": ""}}))
    w.run(w.db.pickup_protocols.insert_one({
        "id": f"p2_{w.s}", "appointment_id": t.aid, "dealer_id": w.dealer_id, "version": 2,
        "status": "final", "superseded": True, "created_at": _jetzt()}))
    w.run(P._preis_uebernehmen(appt, {"id": f"p2_{w.s}", "neuer_preis": 16000.0}))
    kv = _doc(w, "kaufvorgaenge", t.ka)
    assert kv["purchase_price"] == 16000.0 and kv["preis_vorher"] == 20000.0, kv
    # c) Korrektur ohne Preis (zurueckgesetzt): zurueck auf den Ursprung
    w.run(P._preis_uebernehmen(appt, {"id": t.pid, "neuer_preis": None}))
    kv = _doc(w, "kaufvorgaenge", t.ka)
    assert kv["purchase_price"] == 20000.0 and "preis_vorher" not in kv, kv


def test_rp480_fahrer_und_freigabe_zeigen_den_preis_vor_der_abholung(welt):
    w = welt
    P = _m("routes.protocols")
    t = _abholung(w, proto_status="zur_freigabe")
    w.run(w.db.generated_pdfs.update_one(
        {"id": t.ca}, {"$set": {"contract_data.purchase_price": 18000.0,
                                "contract_data.preis_vor_abholung": 20000.0}}))
    assert w.run(P.get_protocol(t.aid, w.driver))["preis_vertrag"] == 20000.0
    liste = w.run(P.protokolle_zur_freigabe(w.chef))
    assert next(e for e in liste if e["protocol_id"] == t.pid)["preis_vertrag"] == 20000.0


def test_rp480_korrektur_ohne_neue_angaben_fragt_die_neuerzeugung(welt, monkeypatch):
    w = welt
    P, C = _m("routes.protocols"), _m("routes.contracts")
    t = _abholung(w)
    aufrufe = []

    async def _regen(**k):
        aufrufe.append(k)
        k["ergebnis"]["grund"] = "kein_anlass"
        return False
    monkeypatch.setattr(C, "regenerate_contract_for_pickup", _regen)
    appt = {"id": t.aid, "dealer_id": w.dealer_id, "contract_id": t.ca, "created_by": w.chef["id"]}
    # ohne fruehere Fassung: nichts zu tun, wie bisher kein Aufruf
    assert w.run(P.vertrag_nach_abholung_aktualisieren(appt, "p_v2", None, None)) is False
    assert aufrufe == []
    # mit Fassung einer frueheren Version: Neuerzeugung wird gefragt, "kein Anlass" ist ok
    w.run(w.db.generated_pdfs.update_one({"id": t.ca}, {"$set": {"nach_abholung_protokoll_id": "p_v1"}}))
    assert w.run(P.vertrag_nach_abholung_aktualisieren(appt, "p_v2", None, None)) is True
    assert len(aufrufe) == 1 and aufrufe[0]["protokoll_id"] == "p_v2"
    assert w.run(w.db.betriebsalarme.count_documents(
        {"typ": "vertrag_nach_abholung_offen", "ref": t.ca, "offen": True})) == 0


# ======================================================================= RP-076 / RP-489
def test_rp076_handabschluss_zieht_preis_und_vertrag_nach(welt, monkeypatch):
    w = welt
    P = _m("routes.protocols")
    t = _abholung(w)
    _final(w, t, neuer_preis=9000.0)
    # Der Abschluss traf einen inzwischen geschlossenen Termin (kein Preis, kein Vertrag).
    w.run(w.db.appointments.update_one({"id": t.aid}, {"$set": {"status": "storniert"}}))
    w.run(w.db.kaufvorgaenge.update_one({"id": t.ka}, {"$set": {"status": "storniert"}}))
    aufrufe = []

    async def _sicher(appt, protokoll):
        aufrufe.append(protokoll["id"])
        return True
    monkeypatch.setattr(P, "vertrag_nach_abholung_sicherstellen", _sicher)
    assert _put(w, t.aid, status="abgeholt")["ok"]
    kv = _doc(w, "kaufvorgaenge", t.ka)
    assert kv["status"] == "abgeholt" and kv["purchase_price"] == 9000.0, kv
    assert kv["preis_protokoll_id"] == t.pid
    assert aufrufe == [t.pid]


def test_rp489_nachholen_loescht_den_merker_erst_mit_vertrag(welt, monkeypatch):
    w = welt
    P = _m("routes.protocols")
    t = _abholung(w)
    _final(w, t)
    w.run(w.db.appointments.update_one(
        {"id": t.aid}, {"$set": {"status": "abgeholt", "nacharbeit_offen": True,
                                 "nacharbeit_protokoll_id": t.pid}}))
    w.run(w.db.kaufvorgaenge.update_one({"id": t.ka}, {"$set": {"status": "abgeholt"}}))
    ergebnis = {"ok": False}
    aufrufe = []

    async def _sicher(appt, protokoll):
        aufrufe.append(protokoll["id"])
        return ergebnis["ok"]
    monkeypatch.setattr(P, "vertrag_nach_abholung_sicherstellen", _sicher)
    _put(w, t.aid, notes="erster Versuch")
    assert aufrufe == [t.pid]
    assert _doc(w, "appointments", t.aid).get("nacharbeit_offen") is True, "Vertrag fehlt noch"
    ergebnis["ok"] = True
    _put(w, t.aid, notes="zweiter Versuch")
    assert "nacharbeit_offen" not in _doc(w, "appointments", t.aid)


# ======================================================================= RP-086
def test_rp086_hinweis_auf_aktives_inserat_nach_storno(welt):
    w = welt
    t = _abholung(w)
    w.run(w.db.pickup_protocols.delete_one({"id": t.pid}))
    w.run(w.db.kaufvorgaenge.update_one({"id": t.kb}, {"$set": {"status": "storniert"}}))
    w.run(w.db.vehicles.update_one({"id": t.vid}, {"$set": {"lifecycle": "veroeffentlicht"}}))
    lid = f"l_{w.s}"
    w.run(w.db.resale_listings.insert_one({"id": lid, "dealer_id": w.dealer_id, "vehicle_id": t.vid,
                                           "status": "veroeffentlicht", "created_at": _jetzt()}))
    try:
        r = _put(w, t.aid, status="storniert")
        assert r.get("inserat_aktiv") is True and "Inserat" in r.get("hinweis", ""), r
        # Gegenprobe: ohne aktives Inserat kein Hinweis
        w.run(w.db.resale_listings.update_one({"id": lid}, {"$set": {"status": "verkauft"}}))
        _put(w, t.aid, status="offen")
        r2 = _put(w, t.aid, status="storniert")
        assert not r2.get("inserat_aktiv"), r2
    finally:
        w.run(w.db.resale_listings.delete_many({"dealer_id": w.dealer_id}))


# ======================================================================= RP-407
def test_rp407_dateiname_ueberlebt_jedes_zeichen():
    A = _m("routes.appointments")
    kopf = A._content_disposition("attachment", "Abholauftrag_Škoda_CX‑6e_20260922.pdf")
    kopf.encode("latin-1")
    assert 'filename="Abholauftrag_Skoda_CX-6e_20260922.pdf"' in kopf, kopf
    # Welle 2 (RP-200/351): gemeinsamer Helfer vertrag_dateiname — Bindestrich-
    # Varianten werden "-", Umlaute ausgeschrieben.
    assert "filename*=UTF-8''Abholauftrag_%C5%A0koda_CX-6e_20260922.pdf" in kopf
    assert A._content_disposition("inline", "äöü.pdf").startswith('inline; filename="aeoeue.pdf"')


def test_rp407_abholauftrag_mit_skoda(welt):
    w = welt
    A = _m("routes.appointments")
    t = _abholung(w)
    w.run(w.db.vehicles.update_one({"id": t.vid}, {"$set": {"data.make_label": "Škoda",
                                                            "data.model_label": "Octavia‑RS"}}))
    antwort = w.run(A.get_pickup_order_pdf(t.aid, download=1, user=w.chef))
    kopf = antwort.headers["content-disposition"]
    assert kopf.startswith("attachment; filename=\"Abholauftrag_Skoda_Octavia_RS_"), kopf
    assert "%C5%A0koda" in kopf


# ======================================================================= PDF (echt, ohne Welt)
def _pdf():
    import importlib
    return importlib.import_module("pickup_pdf_service")


def test_rp068_nein_ist_nicht_unbeantwortet():
    PDF = _pdf()
    t = PDF._checklist([("A", ""), ("B", ""), ("C", "")], PDF._styles(), col_count=1,
                       checked={"A": True, "B": False})
    texte = [zeile[0].text for zeile in t._cellvalues]
    assert "[X]" in texte[0]
    assert "[–]" in texte[1] and "nein" in texte[1]
    assert "[&nbsp;&nbsp;]" in texte[2] and "nein" not in texte[2]


def test_rp170_unterschrift_proportional_und_ortszeit():
    PDF = _pdf()
    f = PDF._sig_faktor(400, 400, 184.0, 51.0)
    assert abs((400 * f) / (400 * f) - 1) < 1e-9 and 400 * f <= 51.0 + 1e-9
    f2 = PDF._sig_faktor(1200, 300, 184.0, 51.0)
    assert 1200 * f2 <= 184.0 + 1e-9 and 300 * f2 <= 51.0 + 1e-9
    sommer = PDF._jetzt_berlin(datetime(2026, 9, 21, 22, 30, tzinfo=timezone.utc))
    assert sommer.strftime("%d.%m.%Y %H:%M") == "22.09.2026 00:30"
    winter = PDF._jetzt_berlin(datetime(2026, 1, 10, 23, 30, tzinfo=timezone.utc))
    assert winter.strftime("%d.%m.%Y %H:%M") == "11.01.2026 00:30"


def test_rp170_fusszeile_kuerzt():
    from reportlab.pdfbase.pdfmetrics import stringWidth
    PDF = _pdf()
    kurz = PDF._auf_breite("Autohaus " * 40, "Helvetica", 7, 120)
    assert kurz.endswith("…") and stringWidth(kurz, "Helvetica", 7) <= 120
    assert PDF._auf_breite("GL Abschluss", "Helvetica", 7, 120) == "GL Abschluss"


def test_rp535_bemerkung_in_stuecken_ohne_verlust():
    PDF = _pdf()
    zeilen = [f"- Punkt {i}: Kratzer an der Tür hinten links" for i in range(40)]
    stuecke = PDF._notes_stuecke("\n".join(zeilen))
    assert all(len(s.split("\n")) <= PDF._BEMERKUNG_ZEILEN_JE_STUECK for s in stuecke)
    assert "\n".join(stuecke).split("\n") == zeilen
    lang = ("Wort " * 1000).strip()
    stuecke = PDF._notes_stuecke(lang)
    assert all(len(s) <= PDF._BEMERKUNG_ZEICHEN_JE_STUECK for s in stuecke)
    assert " ".join(stuecke).split() == lang.split()


def _png(breite, hoehe):
    from PIL import Image, ImageDraw
    bild = Image.new("RGB", (breite, hoehe), "white")
    ImageDraw.Draw(bild).line((0, 0, breite, hoehe), fill="black", width=5)
    puffer = io.BytesIO()
    bild.save(puffer, "PNG")
    return puffer.getvalue()


def _ausgefuellt(notes):
    return {"notes": notes, "place": "Hannover", "seller_name": "Vera", "driver_name": "Fahrer",
            "documents": {"Fahrzeugschein / Zulassung Teil I": True,
                          "Fahrzeugbrief / Zulassung Teil II": False},
            "signature_seller": _png(200, 600), "signature_driver": _png(600, 200),
            "version": 1}


@pytest.mark.parametrize("notes", [
    "\n".join(f"- Punkt {i}: Kratzer an der Tür hinten links, Delle am Kotflügel" for i in range(90)),
    ("Sehr lange Bemerkung ohne Zeilenumbruch " * 120)[:4990],
])
def test_rp535_lange_bemerkung_baut_das_pdf(notes):
    PDF = _pdf()
    pdf = PDF.build_pickup_pdf(
        appointment={"id": f"t_{uuid.uuid4().hex[:8]}", "pickup_date": "2026-09-22"},
        vehicle={"make_label": "BMW", "model_label": "320d"},
        contract={"purchase_price": 18000.0, "preis_vor_abholung": 20000.0},
        dealer={"company_name": "Autohaus mit einem sehr langen Firmennamen " * 3},
        driver={"name": "Fahrer"}, filled=_ausgefuellt(notes))
    assert pdf[:4] == b"%PDF"


def test_rp535_layoutfehler_baut_mit_gekuerzter_bemerkung_neu(monkeypatch):
    from reportlab.platypus.doctemplate import LayoutError
    PDF = _pdf()
    gesehen = []

    def _bau(**k):
        gesehen.append((k.get("filled") or {}).get("notes"))
        if len(gesehen) == 1:
            raise LayoutError("zu gross")
        return b"%PDF-neu"
    monkeypatch.setattr(PDF, "_build_pickup_pdf", _bau)
    assert PDF.build_pickup_pdf(appointment={"id": "t1"}, filled={"notes": "x" * 4000}) == b"%PDF-neu"
    assert len(gesehen) == 2 and len(gesehen[1]) < 4000
    assert gesehen[1].endswith(PDF.BEMERKUNG_GEKUERZT_HINWEIS)
    # ohne Bemerkung: der Fehler bleibt (nichts zu kuerzen)
    gesehen.clear()
    with pytest.raises(LayoutError):
        PDF.build_pickup_pdf(appointment={"id": "t1"}, filled={})


def test_rp170_skizzen_cache_ohne_temp_reste():
    PDF = _pdf()
    pfad = PDF._pdf_sketch("front.png")
    assert pfad
    reste = list(PDF._SKETCH_CACHE_DIR.glob("*.tmp")) if PDF._SKETCH_CACHE_DIR.exists() else []
    assert reste == []


# ======================================================================= RP-488
def test_rp488_hu_korrektur_setzt_auch_gueltig():
    import importlib
    PV = importlib.import_module("protokoll_vergleich")
    heute = datetime(2026, 9, 22)
    assert PV.hu_korrektur("03/2025", heute) == {"hu_until": "03/2025", "hu_valid": "Nein"}
    assert PV.hu_korrektur("09/2026", heute) == {"hu_until": "09/2026", "hu_valid": "Ja"}
    assert PV.hu_korrektur("12/2027", heute) == {"hu_until": "12/2027", "hu_valid": "Ja"}
    assert PV.hu_korrektur("keine HU", heute) == {"hu_until": "", "hu_valid": "Nein"}
    zeilen = PV.vergleich(PV.FELDER, {"hu": {"status": "weicht ab", "value": "03/2025"}}, {},
                          PV.vertragswerte({}, {"hu_valid": "Ja", "hu_until": "06/2027"}))
    k = PV.vertrags_korrekturen(zeilen)
    assert k["hu_until"] == "03/2025" and k["hu_valid"] == "Nein", k
