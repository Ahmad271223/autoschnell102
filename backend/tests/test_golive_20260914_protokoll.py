# -*- coding: utf-8 -*-
"""Pruefung 14.09.2026 — Bereich "protokoll": Zugriff, Beweiskette, Korrektur,
Freigabe-Stand, PDF, Termin-Loeschung, Nachholer.

  C22/C23  Nur eine ANGENOMMENE Fahrt gibt Zugriff auf Protokoll, Abholauftrag
           und Kaufvertrag; stornierte Termine geben den Vertrag nicht frei.
  C2/C3    Korrektur traegt den Fahrer, der sie startet; Version = hoechste
           vorhandene + 1 (keine Kollision mit einer verworfenen Korrektur).
  C17/C18  Termin ohne aktuelle Protokollversion wird repariert (hoechste
           finale Version wieder aktiv) — nicht waehrend einer frischen Abloesung.
  C12      Abschnitt 1 nur mit angebotenen Antworten; "weicht ab" braucht den Wert.
  C7/C16   Freigabe und Abschluss ohne Stand -> 409, sobald ein Stand existiert.
  C9/C10/C20  Fahrer-PDF: letzte finale Version auch waehrend einer Korrektur;
           no-store; vorgemerkte Dateiloeschung -> 410.
  C11      Fahrerwechsel waehrend Freigabe/Abschluss gesperrt (siehe auch
           test_golive_20260913_abschluss::test_p3_put_sperrt_umhaengen...).
  C15      Termin mit finalem/laufendem Protokoll nicht loeschbar; Entwuerfe
           werden mit dem Termin geloescht.
  C19      Scheitert die Freigabe-Ruecknahme beim Schliessen: Betriebsalarm,
           der Aufraeum-Job holt nach; beim Wiederoeffnen 503.
  C4       Merker nacharbeit_offen wird vom Aufraeum-Job nachgeholt.
  C8       Audit-Fehler brechen Abschicken/Freigabe nicht mehr ab.
  A4/A6    Fahrer-Notizen begrenzt; Namensabgleich mit Merker und Nachholer.

In-Prozess (kein Server), Welt aus test_golive_20260913_abschluss.
"""
import base64
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_golive_20260913_abschluss import (  # noqa: E402,F401  (welt = Fixture)
    _PNG_B64, _abholung, _doc, _fin, _jetzt, _m, welt)

DB_NAME = os.environ.get("DB_NAME") or "autoschnell"


def _vor(sekunden):
    return (datetime.now(timezone.utc) - timedelta(seconds=sekunden)).isoformat()


def _fehler(w, coro):
    with pytest.raises(HTTPException) as e:
        w.run(coro)
    return e.value.status_code, e.value.detail


# ============================================================ C22 / C23
def test_c22_zuteilung_offen_sperrt_protokoll_und_dokumente(welt):
    w = welt
    P, D = _m("routes.protocols"), _m("routes.drivers")
    t = _abholung(w, proto_status="entwurf")
    w.run(w.db.generated_pdfs.update_one(
        {"id": t.ca}, {"$set": {"pdf_b64": base64.b64encode(b"%PDF-1.4 kv").decode()}}))
    w.run(w.db.appointments.update_one({"id": t.aid}, {"$set": {"zuteilung": "offen"}}))
    for name, coro in (("protocol", P.get_protocol(t.aid, w.driver)),
                       ("speichern", P.save_protocol(t.aid, P.ProtocolIn(notes="x"), w.driver)),
                       ("abholauftrag", D.driver_pickup_order_pdf(t.aid, 0, w.driver))):
        code, text = _fehler(w, coro)
        assert code == 409 and "annehmen" in text, (name, code, text)
    assert _fehler(w, D.driver_contract_pdf(t.ca, w.driver))[0] == 404
    # abgelehnt: ebenfalls kein Zugriff
    w.run(w.db.appointments.update_one({"id": t.aid}, {"$set": {"zuteilung": "abgelehnt"}}))
    assert _fehler(w, P.get_protocol(t.aid, w.driver))[0] == 409
    # angenommen (bzw. Alttermin ohne Feld): alles da
    w.run(w.db.appointments.update_one({"id": t.aid}, {"$set": {"zuteilung": "angenommen"}}))
    assert w.run(P.get_protocol(t.aid, w.driver))["protocol"]["id"] == t.pid
    assert w.run(D.driver_contract_pdf(t.ca, w.driver)).status_code == 200
    w.run(w.db.appointments.update_one({"id": t.aid}, {"$unset": {"zuteilung": ""}}))
    assert w.run(P.get_protocol(t.aid, w.driver))["protocol"]["id"] == t.pid
    # C23: stornierter Termin gibt den Vertrag nicht mehr frei
    w.run(w.db.appointments.update_one({"id": t.aid}, {"$set": {"status": "storniert"}}))
    assert _fehler(w, D.driver_contract_pdf(t.ca, w.driver))[0] == 404


# ============================================================ C2 / C3
def test_c2_c3_korrektur_eigener_fahrer_und_naechste_freie_version(welt):
    w = welt
    P = _m("routes.protocols")
    t = _abholung(w, proto_status="final", pdf_path="test/v1.pdf", finalized_at=_jetzt())
    # Der Haendler hat inzwischen Fahrer 2 zugeteilt (im Entwurf/final erlaubt)
    w.run(w.db.appointments.update_one({"id": t.aid}, {"$set": {"driver_id": w.driver2["id"]}}))
    v2 = w.run(P.start_correction(t.aid, w.driver2))
    assert v2["version"] == 2 and v2["corrects_version"] == 1
    assert v2["driver_account_id"] == w.driver2["id"] and v2["driver_name"] == "Fahrer GL 2"
    # Korrektur verworfen (Termin geschlossen) -> v1 wieder aktuell
    assert w.run(P.korrektur_verwerfen(t.aid)) is True
    assert _doc(w, "pickup_protocols", t.pid)["superseded"] is False
    # Naechste Korrektur: Version 3, nicht 2 (die verworfene 2 existiert noch)
    w.run(w.db.appointments.update_one({"id": t.aid}, {"$set": {"driver_id": w.driver["id"]}}))
    v3 = w.run(P.start_correction(t.aid, w.driver))
    assert v3["version"] == 3 and v3["driver_account_id"] == w.driver["id"]
    versionen = sorted(d["version"] for d in w.run(
        w.db.pickup_protocols.find({"appointment_id": t.aid}, {"_id": 0}).to_list(10)))
    assert versionen == [1, 2, 3]


# ============================================================ C17 / C18
def test_c17_termin_ohne_aktuelle_version_wird_repariert(welt):
    w = welt
    P, CS = _m("routes.protocols"), _m("cleanup_service")
    t = _abholung(w, proto_status="final", pdf_path="test/v1.pdf", finalized_at=_jetzt())
    # v2 = verworfener Korrektur-Entwurf; v1 versehentlich abgeloest geblieben
    w.run(w.db.pickup_protocols.insert_one(
        {"id": f"{t.pid}_v2", "appointment_id": t.aid, "dealer_id": w.dealer_id, "version": 2,
         "status": "entwurf", "superseded": True, "corrects_version": 1,
         "verworfen_am": _vor(600), "created_at": _jetzt()}))
    w.run(w.db.pickup_protocols.update_one(
        {"id": t.pid}, {"$set": {"superseded": True, "superseded_at": _vor(10)}}))
    # frische Abloesung (laufende Korrektur?) -> noch nicht reparieren
    assert w.run(P._current(t.aid)) is None
    w.run(w.db.pickup_protocols.update_one({"id": t.pid}, {"$set": {"superseded_at": _vor(600)}}))
    doc = w.run(P._current(t.aid))
    assert doc and doc["id"] == t.pid and doc["status"] == "final" and doc.get("repariert_am")
    assert _doc(w, "pickup_protocols", t.pid)["superseded"] is False
    # Aufraeum-Job: nichts mehr zu tun; mit erneuter Abloesung genau eine Reparatur
    assert w.run(CS.protokolle_ohne_aktuelle_version_reparieren(w.db)) == 0
    w.run(w.db.pickup_protocols.update_one(
        {"id": t.pid}, {"$set": {"superseded": True, "superseded_at": _vor(600)}}))
    assert w.run(CS.protokolle_ohne_aktuelle_version_reparieren(w.db)) == 1
    assert _doc(w, "pickup_protocols", t.pid)["superseded"] is False


# ============================================================ C12
def test_c12_abschnitt1_nur_angebotene_antworten(welt):
    w = welt
    P = _m("routes.protocols")
    t = _abholung(w, proto_status="entwurf")
    gut = {k: {"status": o[0]} for k, _l, o in P.VEHICLE_CHECK_FIELDS}
    appt = _doc(w, "appointments", t.aid)
    P._pflichtfelder_pruefen({**_doc(w, "pickup_protocols", t.pid), "vehicle_check": gut},
                             appt, mit_uebergabe=False)
    schlecht = {**gut, "make": {"status": "x"}, "commercial": {"status": "stimmt"}}
    with pytest.raises(HTTPException) as e:
        P._pflichtfelder_pruefen({"vehicle_check": schlecht}, appt, mit_uebergabe=False)
    assert e.value.status_code == 422 and "Marke" in e.value.detail \
        and "Gewerbliche Nutzung" in e.value.detail
    ohne_wert = {**gut, "vin": {"status": "weicht ab", "value": "  "}}
    with pytest.raises(HTTPException) as e:
        P._pflichtfelder_pruefen({"vehicle_check": ohne_wert}, appt, mit_uebergabe=False)
    assert e.value.status_code == 422 and "FIN" in e.value.detail and "Wert" in e.value.detail
    mit_wert = {**gut, "vin": {"status": "weicht ab", "value": "WVWZZZ1KZAW000001"}}
    w.run(w.db.pickup_protocols.update_one(
        {"id": t.pid}, {"$set": {"vehicle_check": mit_wert, "condition": {"mileage": "1"},
                                 "keys_count": "2", "damages_confirmed": True}}))
    assert w.run(P.submit_protocol(t.aid, w.driver))["status"] == P.ZUR_FREIGABE


# ============================================================ C7 / C16
def test_c16_abschluss_und_freigabe_ohne_stand_abgelehnt(welt):
    w = welt
    P = _m("routes.protocols")
    t = _abholung(w)                      # freigegeben, freigabe_stand "s1"
    code, text = _fehler(w, P.finalize_protocol(t.aid, _fin(P, 9000.0, None), w.driver))
    assert code == 409 and text == P.STAND_FEHLT
    assert _doc(w, "pickup_protocols", t.pid)["status"] == "freigegeben"
    code, text = _fehler(w, P.protokoll_freigeben(t.pid, P.FreigabeIn(neuer_preis=1), w.chef))
    assert code == 409 and text == P.STAND_FEHLT_FREIGABE
    assert _doc(w, "pickup_protocols", t.pid)["neuer_preis"] == 9000.0
    # mit Stand geht beides
    fr = w.run(P.protokoll_freigeben(t.pid, P.FreigabeIn(neuer_preis=8000, stand="s1"), w.chef))
    assert w.run(P.finalize_protocol(t.aid, _fin(P, 8000.0, fr["stand"]), w.driver))["ok"] is True


# ============================================================ C9 / C10 / C20
def test_c9_c10_c20_fahrer_pdf_letzte_finale_version_no_store_und_410(welt, monkeypatch):
    w = welt
    P = _m("routes.protocols")
    SS = _m("storage_service")

    async def _load(key):
        return b"%PDF-1.4 " + key.encode()
    monkeypatch.setattr(SS, "load_async", _load)
    t = _abholung(w, proto_status="final", pdf_path="test/v1.pdf", finalized_at=_jetzt())
    r = w.run(P.driver_protocol_pdf(t.aid, w.driver))
    assert r.status_code == 200 and r.body.endswith(b"test/v1.pdf")
    assert r.headers.get("cache-control") == "no-store"
    # Korrektur laeuft (v2 Entwurf): die unterschriebene v1 bleibt abrufbar
    w.run(P.start_correction(t.aid, w.driver))
    r = w.run(P.driver_protocol_pdf(t.aid, w.driver))
    assert r.status_code == 200 and r.body.endswith(b"test/v1.pdf")
    # Haendler-PDF: ebenfalls no-store
    r = w.run(P.dealer_protocol_pdf(t.pid, w.chef))
    assert r.status_code == 200 and r.headers.get("cache-control") == "no-store"
    # Dateiloeschung vorgemerkt, aber noch nicht nachgeholt -> 410, nicht das PDF
    w.run(w.db.pickup_protocols.update_one({"id": t.pid},
                                           {"$set": {"pdf_path_loeschung_offen": True}}))
    assert _fehler(w, P.driver_protocol_pdf(t.aid, w.driver))[0] == 410
    assert _fehler(w, P.dealer_protocol_pdf(t.pid, w.chef))[0] == 410


# ============================================================ C15
def test_c15_termin_mit_protokoll_nicht_loeschbar_entwurf_geht_mit(welt):
    w = welt
    A = _m("routes.appointments")
    for status in ("final", "zur_freigabe", "freigegeben", "wird_abgeschlossen"):
        t = _abholung(w, name=f"del_{status}", proto_status=status)
        code, text = _fehler(w, A.delete_appointment(t.aid, w.chef))
        assert code == 409 and "stornieren" in text, (status, text)
        assert _doc(w, "appointments", t.aid) is not None
    t = _abholung(w, name="del_entwurf", proto_status="entwurf")
    assert w.run(A.delete_appointment(t.aid, w.chef)) == {"ok": True}
    assert _doc(w, "appointments", t.aid) is None
    assert _doc(w, "pickup_protocols", t.pid) is None


# ============================================================ C19
def test_c19_ruecknahme_fehler_alarm_nachholer_und_503_beim_wiederoeffnen(welt, monkeypatch):
    w = welt
    P, A, CS = _m("routes.protocols"), _m("routes.appointments"), _m("cleanup_service")
    t = _abholung(w, proto_status="zur_freigabe")

    async def _kaputt(*a, **k):
        raise RuntimeError("Mongo weg")
    echt = P._freigabe_zuruecknehmen
    monkeypatch.setattr(P, "_freigabe_zuruecknehmen", _kaputt)
    # Schliessen geht durch (der Termin ist geschlossen), aber mit Alarm
    w.run(A.update_appointment(t.aid, A.AppointmentIn(status="storniert"), w.chef))
    assert _doc(w, "appointments", t.aid)["status"] == "storniert"
    assert _doc(w, "pickup_protocols", t.pid)["status"] == "zur_freigabe"
    alarm = w.run(w.db.betriebsalarme.find_one(
        {"typ": "protokoll_freigabe_ruecknahme_offen", "ref": t.aid, "offen": True}))
    assert alarm
    # Wiederoeffnen mit alter Freigabe: nein
    code, _ = _fehler(w, A.update_appointment(t.aid, A.AppointmentIn(status="offen"), w.chef))
    assert code == 503 and _doc(w, "appointments", t.aid)["status"] == "storniert"
    # Aufraeum-Job holt die Ruecknahme nach und schliesst den Alarm
    monkeypatch.setattr(P, "_freigabe_zuruecknehmen", echt)
    assert w.run(CS.protokoll_freigaben_nachziehen(w.db)) == 1
    p = _doc(w, "pickup_protocols", t.pid)
    assert p["status"] == "entwurf" and p["rueckfrage"] == P.TERMIN_GESCHLOSSEN_RUECKFRAGE
    assert w.run(w.db.betriebsalarme.find_one(
        {"typ": "protokoll_freigabe_ruecknahme_offen", "ref": t.aid, "offen": True})) is None
    assert w.run(CS.protokoll_freigaben_nachziehen(w.db)) == 0


# ============================================================ C4
def test_c4_nacharbeit_offen_wird_vom_aufraeumjob_nachgeholt(welt):
    w = welt
    CS = _m("cleanup_service")
    t = _abholung(w, proto_status="final", pdf_path="test/v1.pdf", finalized_at=_jetzt(),
                  contract_id=None)
    w.run(w.db.pickup_protocols.update_one({"id": t.pid}, {"$set": {"contract_id": t.ca}}))
    w.run(w.db.appointments.update_one(
        {"id": t.aid}, {"$set": {"status": "abgeholt", "nacharbeit_offen": True,
                                 "updated_at": _vor(3600)}}))
    jetzt = datetime.now(timezone.utc)
    # zu frisch (Merker juenger als 10 min): nichts
    w.run(w.db.appointments.update_one({"id": t.aid}, {"$set": {"updated_at": _vor(60)}}))
    assert w.run(CS.termin_nacharbeit_nachholen(w.db, jetzt)) == 0
    w.run(w.db.appointments.update_one({"id": t.aid}, {"$set": {"updated_at": _vor(3600)}}))
    assert w.run(CS.termin_nacharbeit_nachholen(w.db, jetzt)) == 1
    kv = _doc(w, "kaufvorgaenge", t.ka)
    assert kv["status"] == "abgeholt" and kv["purchase_price"] == 9000.0, kv
    assert "nacharbeit_offen" not in _doc(w, "appointments", t.aid)
    assert w.run(CS.termin_nacharbeit_nachholen(w.db, jetzt)) == 0


# ============================================================ C8
def test_c8_audit_fehler_bricht_abschicken_und_freigabe_nicht_ab(welt, monkeypatch):
    w = welt
    P, DEPS = _m("routes.protocols"), _m("deps")

    async def _kaputt(*a, **k):
        raise RuntimeError("activity_logs weg")
    monkeypatch.setattr(DEPS, "log_activity", _kaputt)
    t = _abholung(w, proto_status="entwurf")
    assert w.run(P.submit_protocol(t.aid, w.driver))["status"] == P.ZUR_FREIGABE
    stand = _doc(w, "pickup_protocols", t.pid)["freigabe_stand"]
    fr = w.run(P.protokoll_freigeben(t.pid, P.FreigabeIn(neuer_preis=8500, stand=stand), w.chef))
    assert fr["status"] == P.FREIGEGEBEN
    z = w.run(P.protokoll_freigeben(t.pid, P.FreigabeIn(zurueck=True, stand=fr["stand"]), w.chef))
    assert z["status"] == "entwurf"


# ============================================================ A4 / A6
def test_a4_notizen_begrenzt():
    D = _m("routes.drivers")
    assert D.DriverStatusIn(status="abgeholt", notes="x" * 2000).notes
    with pytest.raises(ValidationError):
        D.DriverStatusIn(status="abgeholt", notes="x" * 2001)


def test_a6_namensabgleich_mit_merker_und_nachholer(welt, monkeypatch):
    w = welt
    D, CS = _m("routes.drivers"), _m("cleanup_service")

    async def _scheitert(*a, **k):
        return False
    monkeypatch.setattr(D, "fahrername_verteilen", _scheitert)
    r = w.run(D.driver_update_me(D.DriverProfileUpdate(display_name="Neuer Name"), w.driver))
    assert r["display_name"] == "Neuer Name"
    konto = w.run(w.db.driver_accounts.find_one({"id": w.driver["id"]}, {"_id": 0}))
    assert konto["display_name"] == "Neuer Name" and konto.get("name_sync_offen") is True
    link = w.run(w.db.dealer_drivers.find_one({"driver_account_id": w.driver["id"]}, {"_id": 0}))
    assert link["display_name"] == "Fahrer GL", "zweiter Write scheiterte — alter Name"
    assert w.run(CS.fahrernamen_nachziehen(w.db)) == 1
    link = w.run(w.db.dealer_drivers.find_one({"driver_account_id": w.driver["id"]}, {"_id": 0}))
    konto = w.run(w.db.driver_accounts.find_one({"id": w.driver["id"]}, {"_id": 0}))
    assert link["display_name"] == "Neuer Name" and "name_sync_offen" not in konto
    assert w.run(CS.fahrernamen_nachziehen(w.db)) == 0
