# -*- coding: utf-8 -*-
"""Pruefung 14.09.2026, dritte und vierte Liste — Vertrag, Protokoll-Entwurf,
Termin, Abholbericht, Frist.

  L3-1  Vertragsanlage mit Idempotenz-Schluessel: Doppelklick = derselbe Vertrag.
  L3-2/8 kein 0-Euro-Vertrag, kein leerer Verkaeufer.
  L3-10/11 Abholbericht: nur Dubletten werden wiederholt, Folgeschritte best effort.
  L4-1/2/3/6 Entwurf wird bei Fahrzeug-/Vertrags-/Fahrerwechsel verworfen; der
        Entwurf traegt immer den aktuellen Fahrer und das aktuelle Fahrzeug.
  L4-4/5 Zusage des Fahrers erlischt bei Fahrzeug-/Vertrags-/Verkaeuferwechsel.
  L4-7/8 Freigabe nur fuer ein vollstaendiges, gueltiges Protokoll.
  L4-9/10 final_price am Termin ist gesperrt, sobald ein Protokoll laeuft/final ist.
  L4-11 offener Kaufvorgang mit Bewegung haelt den Vertrag in der Frist.
  L4-12 bereinigtes finales Protokoll heilt keinen Termin mehr.
  L4-13 PII-Scrub entfernt tote Vertragsverweise aus dem Protokoll.
  Wunsch Ahmad: nach dem Abschluss wird der Vertrag mit neuem Preis und
        Sondervereinbarung neu erstellt (neue Fassung, alte im Archiv).
"""
import importlib
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
# Die echte Neuerzeugung sichern, BEVOR die Welt sie fuer die anderen Tests ersetzt.
ECHT_REGEN = importlib.import_module("routes.contracts").regenerate_contract_for_pickup
from test_golive_20260913_abschluss import (  # noqa: E402,F401  (welt = Fixture)
    _PNG_B64, _abholung, _doc, _fin, _jetzt, _m, welt)


def _fehler(w, coro):
    with pytest.raises(HTTPException) as e:
        w.run(coro)
    return e.value.status_code, e.value.detail


def _vertrag_in(C, vid, purchase_price=12000, **extra):
    return C.ContractIn(vehicle_id=vid, seller_name="Vera Verkauf", seller_address="Weg 3",
                        seller_zip="30159", seller_city="Hannover",
                        purchase_price=purchase_price, **extra)


# ============================================================ L3-1 / L3-2 / L3-8
def test_l3_vertrag_idempotent_und_validiert(welt):
    w = welt
    C = _m("routes.contracts")
    with pytest.raises(ValidationError):
        _vertrag_in(C, w.driver["id"], purchase_price=0)
    with pytest.raises(ValidationError):
        C.ContractIn(vehicle_id="x", seller_name="  ", purchase_price=100)
    t = _abholung(w, proto_status="entwurf")
    chef = {**w.chef, "active": True}
    eins = w.run(C.create_contract(_vertrag_in(C, t.vid, idempotency_key="k-" + w.s), chef))
    assert eins["id"] and "pdf_b64" not in eins
    zwei = w.run(C.create_contract(_vertrag_in(C, t.vid, idempotency_key="k-" + w.s), chef))
    assert zwei["id"] == eins["id"] and zwei.get("bereits_vorhanden") is True
    assert w.run(w.db.generated_pdfs.count_documents({"idempotency_key": "k-" + w.s})) == 1
    drei = w.run(C.create_contract(_vertrag_in(C, t.vid, idempotency_key="k2-" + w.s), chef))
    assert drei["id"] != eins["id"]


# ============================================================ Wunsch Ahmad: Vertrag nach Abholung
def test_vertrag_wird_nach_abholung_mit_neuem_preis_neu_erstellt(welt, monkeypatch):
    w = welt
    P = _m("routes.protocols")
    t = _abholung(w, sondervereinbarung="Winterreifen werden nachgeliefert")
    # 1) Der Abschluss ruft die Neuerzeugung mit Preis + Sondervereinbarung auf
    C = _m("routes.contracts")
    gesehen = {}

    async def _regen(**k):
        gesehen.update(k)
        return True
    monkeypatch.setattr(C, "regenerate_contract_for_pickup", _regen)
    assert w.run(P.finalize_protocol(t.aid, _fin(P), w.driver))["ok"]
    assert gesehen["contract_id"] == t.ca and gesehen["neuer_preis"] == 9000.0
    assert "Winterreifen" in gesehen["sondervereinbarung"] and gesehen["protokoll_id"] == t.pid
    assert w.run(w.db.activity_logs.find_one({"action": "vertrag.nach_abholung_aktualisiert",
                                              "ref": t.ca}))
    # 2) Die echte Neuerzeugung: neue Fassung mit neuem Preis, alte im Archiv
    ok = w.run(ECHT_REGEN(contract_id=t.ca, dealer_id=w.dealer_id, user=w.chef,
                          neuer_preis=9000.0, sondervereinbarung="Winterreifen werden nachgeliefert",
                          grund="abholung_abgeschlossen", protokoll_id=t.pid))
    assert ok is True
    c = _doc(w, "generated_pdfs", t.ca)
    assert c["version"] == 2 and c["purchase_price"] == 9000.0
    assert c["contract_data"]["purchase_price"] == 9000.0
    assert c["contract_data"]["preis_vor_abholung"] == 10000.0
    assert "Winterreifen" in c["contract_data"]["additional_terms"]
    assert c["nach_abholung_protokoll_id"] == t.pid and c["pdf_b64"]
    archiv = w.run(w.db.generated_pdf_versions.find_one({"contract_id": t.ca, "version": 1}, {"_id": 0}))
    assert archiv and archiv["grund"] == "abholung_abgeschlossen" \
        and archiv["contract_data"]["purchase_price"] == 10000.0
    # unveraendert -> keine weitere Fassung
    assert w.run(ECHT_REGEN(contract_id=t.ca, dealer_id=w.dealer_id, user=w.chef,
                            neuer_preis=9000.0, sondervereinbarung="Winterreifen werden nachgeliefert",
                            grund="abholung_abgeschlossen")) is False
    assert _doc(w, "generated_pdfs", t.ca)["version"] == 2


# ============================================================ L4-1/2/3/6 + L4-4/5
def test_l4_entwurf_und_zusage_bei_terminaenderung(welt):
    w = welt
    P, A = _m("routes.protocols"), _m("routes.appointments")
    t = _abholung(w, proto_status="entwurf", mit_vertrag=False)   # ohne Vertrag: Fahrzeug frei wechselbar
    w.run(w.db.appointments.update_one({"id": t.aid}, {"$set": {"zuteilung": "angenommen"}}))
    # Fahrzeugwechsel: Entwurf weg, Zusage erlischt
    w.run(A.update_appointment(t.aid, A.AppointmentIn(vehicle_id=t.vid2), w.chef))
    assert _doc(w, "pickup_protocols", t.pid) is None
    a = _doc(w, "appointments", t.aid)
    assert a["vehicle_id"] == t.vid2 and a["zuteilung"] == "offen"
    # neuer Entwurf (der geloeschte Entwurf gibt Version 1 frei), Fahrzeug des Termins
    w.run(w.db.appointments.update_one({"id": t.aid}, {"$set": {"zuteilung": "angenommen"}}))
    neu = w.run(P.save_protocol(t.aid, P.ProtocolIn(notes="neu"), w.driver))
    assert neu["version"] == 1 and neu["vehicle_id"] == t.vid2
    # Fahrer 2 speichert -> der Entwurf gehoert jetzt ihm (Fahrerwechsel im Entwurf
    # verwirft ihn sogar; hier direkt per Speichern)
    w.run(w.db.appointments.update_one({"id": t.aid}, {"$set": {"driver_id": w.driver2["id"]}}))
    # Runde 13: ein Entwurf mit Revision braucht beim Speichern den geladenen Stand
    d = w.run(P.save_protocol(t.aid, P.ProtocolIn(notes="von 2", revision=neu["revision"]), w.driver2))
    assert d["driver_account_id"] == w.driver2["id"] and d["driver_name"] == "Fahrer GL 2"
    # Verkaeuferwechsel: Zusage erlischt ebenfalls
    w.run(w.db.appointments.update_one({"id": t.aid}, {"$set": {"zuteilung": "angenommen"}}))
    w.run(A.update_appointment(t.aid, A.AppointmentIn(seller_name="Anderer"), w.chef))
    assert _doc(w, "appointments", t.aid)["zuteilung"] == "offen"
    # Fahrerwechsel im Entwurf: Entwurf weg
    w.run(A.update_appointment(t.aid, A.AppointmentIn(driver_id=w.driver["id"]), w.chef))
    assert w.run(w.db.pickup_protocols.count_documents({"appointment_id": t.aid})) == 0


# ============================================================ L4-9/10
def test_l4_final_price_nur_ohne_laufendes_oder_finales_protokoll(welt):
    w = welt
    A = _m("routes.appointments")
    t = _abholung(w, proto_status="entwurf")
    assert w.run(A.update_appointment(t.aid, A.AppointmentIn(final_price=9500), w.chef))
    for status in ("zur_freigabe", "freigegeben", "wird_abgeschlossen", "final"):
        w.run(w.db.pickup_protocols.update_one({"id": t.pid}, {"$set": {"status": status,
                                                                         "claim_bis": "2999-01-01T00:00:00+00:00"}}))
        code, text = _fehler(w, A.update_appointment(t.aid, A.AppointmentIn(final_price=8000), w.chef))
        assert code == 409 and "Abholprotokoll" in text, status
        # unveraenderter Wert (Oberflaeche schickt das ganze Objekt) stoert nicht
        assert w.run(A.update_appointment(t.aid, A.AppointmentIn(final_price=9500, notes=status), w.chef))


# ============================================================ L4-7/8
def test_l4_freigabe_nur_fuer_vollstaendiges_protokoll(welt):
    w = welt
    P = _m("routes.protocols")
    t = _abholung(w, proto_status="zur_freigabe", freigabe_stand="s1")
    w.run(w.db.pickup_protocols.update_one({"id": t.pid}, {"$set": {"vehicle_check.make": {"status": "x"}}}))
    code, text = _fehler(w, P.protokoll_freigeben(t.pid, P.FreigabeIn(stand="s1"), w.chef))
    assert code == 422 and "zurückschicken" in text
    assert _doc(w, "pickup_protocols", t.pid)["status"] == "zur_freigabe"
    # zurueck an den Fahrer geht immer
    r = w.run(P.protokoll_freigeben(t.pid, P.FreigabeIn(zurueck=True, stand="s1", notiz="bitte prüfen"), w.chef))
    assert r["status"] == "entwurf"


# ============================================================ L4-12 / L4-13 / L4-11
def test_l4_bereinigtes_protokoll_heilt_nicht_und_verweise_weg(welt):
    w = welt
    P, CS = _m("routes.protocols"), _m("cleanup_service")
    t = _abholung(w, proto_status="final", pdf_path="test/v1.pdf", finalized_at=_jetzt(),
                  contract_id=None)
    w.run(w.db.pickup_protocols.update_one({"id": t.pid}, {"$set": {"contract_id": t.ca,
                                                                     "kaufvorgang_id": t.ka}}))
    jetzt = _jetzt()
    w.run(CS._protokolle_pii_entfernen(w.db, [t.aid], w.dealer_id, jetzt, contract_id=t.ca))
    p = _doc(w, "pickup_protocols", t.pid)
    assert p["vertrag_geloescht"] is True and p["vertrag_geloescht_ref"] == t.ca
    assert "contract_id" not in p and "kaufvorgang_id" not in p and "pdf_path" not in p
    assert p["status"] == "final" and p["pii_geloescht_at"]
    # Termin wieder offen: ein alter Finalize-Retry darf ihn nicht "heilen"
    code, text = _fehler(w, P.finalize_protocol(t.aid, _fin(P), w.driver))
    assert code == 409 and "bereinigt" in text
    assert _doc(w, "appointments", t.aid)["status"] == "offen"


def test_l4_offener_kaufvorgang_mit_bewegung_haelt_den_vertrag(welt):
    w = welt
    CS = _m("cleanup_service")
    t = _abholung(w)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
    w.run(w.db.appointments.update_one({"id": t.aid}, {"$set": {"status": "storniert"}}))
    # kA: abholung_geplant, updated_at jetzt -> haelt
    assert w.run(CS.vertrag_noch_in_gebrauch(w.db, t.ca, cutoff)) is True
    alt = (datetime.now(timezone.utc) - timedelta(days=120)).isoformat()
    w.run(w.db.kaufvorgaenge.update_one({"id": t.ka}, {"$set": {"updated_at": alt}}))
    assert w.run(CS.vertrag_noch_in_gebrauch(w.db, t.ca, cutoff)) is False


# ============================================================ L3-10/11
def test_l3_abholbericht_echter_dbfehler_ist_kein_parallelkonflikt(welt, monkeypatch):
    w = welt
    D = _m("routes.drivers")
    t = _abholung(w, proto_status="final", pdf_path="x", finalized_at=_jetzt())
    w.run(w.db.appointments.update_one({"id": t.aid}, {"$set": {"status": "abgeholt",
                                                                 "status_changed_at": _jetzt()}}))
    echt = D.db

    class _Berichte:
        def __getattr__(self, n):
            return getattr(echt.pickup_reports, n)

        async def insert_one(self, *a, **k):
            raise RuntimeError("Mongo weg")

    class _DB:
        def __getattr__(self, n):
            return _Berichte() if n == "pickup_reports" else getattr(echt, n)

        def __getitem__(self, n):
            return self.__getattr__(n)
    monkeypatch.setattr(D, "db", _DB())
    with pytest.raises(RuntimeError):
        w.run(D.driver_submit_report(t.aid, D.PickupReportIn(notes="vor Ort ok"), w.driver))
    monkeypatch.setattr(D, "db", echt)
    assert w.run(w.db.pickup_reports.count_documents({"appointment_id": t.aid})) == 0
    # ohne Stoerung: gespeichert
    r = w.run(D.driver_submit_report(t.aid, D.PickupReportIn(notes="vor Ort ok"), w.driver))
    assert r["ok"] is True and r["version"] == 1
