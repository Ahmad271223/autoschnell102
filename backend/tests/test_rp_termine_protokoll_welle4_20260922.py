# -*- coding: utf-8 -*-
"""Rollenprüfung 22.09.2026 — Team termine_protokoll, Welle 4 (Review-Befunde).

In-Prozess gegen DB_NAME, Welt aus test_golive_20260913_abschluss (Wegwerf-
Daten je Test, Aufraeumen im finally der Fixture).

  Review 11  Korrektur-Version nahm den Verkaeufernamen vom Termin — ein vom
             Fahrer vor Ort korrigierter Name (RP-058) ging verloren. Jetzt
             gilt der Termin-Name nur, wenn der Chef ihn nach dem Abschluss
             geaendert hat (Zeitpunkt seller_name_geaendert_am am Termin; kein
             Name im Protokoll, der die Personendaten-Loeschung ueberstuende).
  Review 12  "Korrektur verwerfen" lief vor dem Termin-Write mit Stand-
             Pruefung — bei veraltetem Stand war die Korrektur weg, der Status
             aber nicht gesetzt. Jetzt: Stand zuerst, Verwerfen im Write-Schritt
             (Replica-Set: dieselbe Transaktion), nur die gelesene Korrektur.
"""
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_golive_20260913_abschluss import (  # noqa: E402,F401  (welt = Fixture)
    _abholung, _doc, _fin, _jetzt, _m, welt)

_STAND = "2026-09-22T08:00:00+00:00"


def _fehler(w, coro):
    with pytest.raises(HTTPException) as e:
        w.run(coro)
    return e.value.status_code, e.value.detail


# ======================================================================= Review 11
def test_review11_verkaeufername_regel():
    P = _m("routes.protocols")
    f = P._korrektur_verkaeufername
    M = P.SELLER_NAME_GEAENDERT_AM
    v1 = {"seller_name": "Max Müller-Lüdenscheidt",
          "finalized_at": "2026-09-22T10:00:00.000001+00:00"}
    # Fahrer korrigierte vor Ort, Chef aenderte am Termin nichts -> Name der Vorversion
    assert f(v1, {"seller_name": "Müller"}) == "Max Müller-Lüdenscheidt"
    # Chef aenderte den Namen VOR dem Abschluss -> unterschrieben ist der der Vorversion
    assert f(v1, {"seller_name": "Müller", M: "2026-09-22T09:00:00+00:00"}) \
        == "Max Müller-Lüdenscheidt"
    # Chef aenderte den Namen am Termin NACH dem Abschluss -> Name am Termin (RP-074),
    # auch in der Sekunde des Abschlusses (Zeitpunkt ohne Mikrosekunden)
    assert f(v1, {"seller_name": "Maximilian Müller", M: "2026-09-22T10:00:01+00:00"}) \
        == "Maximilian Müller"
    assert f({**v1, "finalized_at": "2026-09-22T10:00:00+00:00"},
             {"seller_name": "Maximilian Müller", M: "2026-09-22T10:00:00.5+00:00"}) \
        == "Maximilian Müller"
    # Termin ohne Namen -> Vorversion; Vorversion ohne Namen -> Termin
    assert f(v1, {"seller_name": " ", M: "2026-09-23T00:00:00+00:00"}) == v1["seller_name"]
    assert f({}, {"seller_name": " Vera "}) == "Vera"


def _abschliessen_und_wieder_oeffnen(w, t, name_vor_ort):
    P = _m("routes.protocols")
    # Der Fahrer hat den Namen vor Ort korrigiert (RP-058), der Termin traegt den alten.
    w.run(w.db.pickup_protocols.update_one({"id": t.pid},
                                           {"$set": {"seller_name": name_vor_ort}}))
    assert w.run(P.finalize_protocol(t.aid, _fin(P), w.driver))["ok"] is True
    v1 = _doc(w, "pickup_protocols", t.pid)
    assert v1["status"] == "final" and v1["seller_name"] == name_vor_ort
    assert "Vera" not in str({k: v for k, v in v1.items() if k != "seller_name"}), \
        "keine Kopie des Termin-Namens im Protokoll (Personendaten-Loeschung)"
    # Chef oeffnet den Termin wieder (Status direkt, der Weg ist hier egal).
    w.run(w.db.appointments.update_one({"id": t.aid}, {"$set": {"status": "offen"}}))
    return P


def test_review11_korrektur_behaelt_den_vor_ort_korrigierten_namen(welt):
    w = welt
    A = _m("routes.appointments")
    t = _abholung(w)
    P = _abschliessen_und_wieder_oeffnen(w, t, "Vera Müller-Lüdenscheidt")
    # Eine Aenderung ohne neuen Namen setzt keinen Merker.
    w.run(A.update_appointment(t.aid, A.AppointmentIn(seller_name="Vera", notes="x"), w.chef))
    assert P.SELLER_NAME_GEAENDERT_AM not in _doc(w, "appointments", t.aid)
    neu = w.run(P.start_correction(t.aid, w.driver))
    assert neu["seller_name"] == "Vera Müller-Lüdenscheidt", "vorher: 'Vera' vom Termin"
    assert neu["corrects_version"] == 1
    assert _doc(w, "appointments", t.aid)["seller_name"] == "Vera", "Termin unberuehrt"


def test_review11_chef_korrektur_am_termin_gilt_weiter(welt):
    """RP-074 bleibt: aendert der Chef den Namen NACH dem Abschluss am Termin,
    traegt die Korrektur seinen Namen."""
    w = welt
    A = _m("routes.appointments")
    t = _abholung(w)
    P = _abschliessen_und_wieder_oeffnen(w, t, "Vera Müller-Lüdenscheidt")
    w.run(A.update_appointment(t.aid, A.AppointmentIn(seller_name="Vera Neu"), w.chef))
    termin = _doc(w, "appointments", t.aid)
    assert termin[P.SELLER_NAME_GEAENDERT_AM] == termin["updated_at"]
    neu = w.run(P.start_correction(t.aid, w.driver))
    assert neu["seller_name"] == "Vera Neu"


# ======================================================================= Review 12
def _korrektur_offen(w, t):
    """v1 unterschrieben und abgeloest, v2 (Korrektur) liegt beim Chef; der
    Termin traegt einen Stand (updated_at)."""
    w.run(w.db.pickup_protocols.update_one(
        {"id": t.pid}, {"$set": {"status": "final", "contract_id": t.ca, "pdf_path": "x.pdf",
                                 "finalized_at": _jetzt(), "superseded": True,
                                 "superseded_at": "2000-01-01T00:00:00+00:00"}}))
    w.run(w.db.pickup_protocols.insert_one({
        "id": f"p2_{w.s}", "appointment_id": t.aid, "dealer_id": w.dealer_id,
        "vehicle_id": t.vid, "driver_account_id": w.driver["id"], "version": 2,
        "corrects_version": 1, "status": "zur_freigabe", "superseded": False,
        "created_at": _jetzt()}))
    w.run(w.db.kaufvorgaenge.update_one({"id": t.ka}, {"$set": {"status": "abgeholt"}}))
    w.run(w.db.appointments.update_one({"id": t.aid}, {"$set": {"updated_at": _STAND}}))
    return f"p2_{w.s}"


def _unveraendert(w, t, p2):
    v2, v1 = _doc(w, "pickup_protocols", p2), _doc(w, "pickup_protocols", t.pid)
    assert v2["superseded"] is False and not v2.get("verworfen_am"), "Korrektur bleibt"
    assert v1["superseded"] is True, "Vorversion bleibt abgeloest"
    assert _doc(w, "appointments", t.aid)["status"] == "offen"


def test_review12_veralteter_dialog_verwirft_nichts(welt):
    w = welt
    A = _m("routes.appointments")
    t = _abholung(w)
    p2 = _korrektur_offen(w, t)
    # Der Dialog wurde vor dem Abschicken der Korrektur geladen (alter Stand).
    alt = "2026-09-22T07:00:00+00:00"
    # Schon die Rueckfrage meldet den veralteten Stand (wie bei V-12) ...
    code, text = _fehler(w, A.update_appointment(
        t.aid, A.AppointmentIn(status="abgeholt", stand=alt), w.chef))
    assert code == 409 and text == A.TERMIN_VERALTET_HINWEIS, text
    _unveraendert(w, t, p2)
    # ... und auch die bestaetigte Fassung verwirft auf altem Stand nichts.
    code, text = _fehler(w, A.update_appointment(
        t.aid, A.AppointmentIn(status="abgeholt", stand=alt, korrektur_verwerfen=True), w.chef))
    assert code == 409 and text == A.TERMIN_VERALTET_HINWEIS, text
    _unveraendert(w, t, p2)
    # Mit dem aktuellen Stand geht es durch.
    r = w.run(A.update_appointment(
        t.aid, A.AppointmentIn(status="abgeholt", stand=_STAND, korrektur_verwerfen=True), w.chef))
    assert r["ok"]
    v2, v1 = _doc(w, "pickup_protocols", p2), _doc(w, "pickup_protocols", t.pid)
    assert v2["superseded"] is True and v2.get("verworfen_am")
    assert v1["superseded"] is False
    assert _doc(w, "appointments", t.aid)["status"] == "abgeholt"


def test_review12_stand_aendert_sich_nach_dem_lesen(welt, monkeypatch):
    """Ohne Client-Stand: aendert sich der Termin zwischen Lesen und Schreiben,
    scheitert der Write-Schritt VOR dem Verwerfen (vorher: Korrektur weg, 409)."""
    w = welt
    A = _m("routes.appointments")
    import deps
    t = _abholung(w)
    p2 = _korrektur_offen(w, t)

    async def chef_und_dazwischen(user):
        # Ein Kollege speichert genau jetzt (nach dem Lesen des Termins).
        await w.db.appointments.update_one({"id": t.aid}, {"$set": {"updated_at": _jetzt()}})
        return True
    monkeypatch.setattr(deps, "ist_haupt_chef", chef_und_dazwischen)
    code, text = _fehler(w, A.update_appointment(
        t.aid, A.AppointmentIn(status="abgeholt", korrektur_verwerfen=True), w.chef))
    assert code == 409 and text == A.TERMIN_VERALTET_HINWEIS, text
    _unveraendert(w, t, p2)


def test_review12_verwerfen_laeuft_in_der_sitzung_des_writes(welt, monkeypatch):
    """Replica-Set-Weg: korrektur_verwerfen bekommt die Sitzung des Termin-
    Writes und genau die gelesene Korrektur (nur_id). Die Test-DB ist kein
    Replica-Set — die Sitzung laeuft hier ohne Transaktion."""
    w = welt
    A, P = _m("routes.appointments"), _m("routes.protocols")
    import deps
    t = _abholung(w)
    p2 = _korrektur_offen(w, t)
    aufrufe, im_verwerfen = [], []
    echt, echte_transaktion = P.korrektur_verwerfen, deps.transaktion

    async def spion(appt_id, **kw):
        aufrufe.append(kw)
        im_verwerfen.append(True)
        try:
            return await echt(appt_id, **kw)
        finally:
            im_verwerfen.pop()
    monkeypatch.setattr(P, "korrektur_verwerfen", spion)

    async def mit_sitzung(fn):
        async with await w.client.start_session() as s:
            return await fn(s)
    monkeypatch.setattr(A, "_transaktion", mit_sitzung)

    async def keine_eigene_transaktion(fn):
        # Mit Sitzung darf korrektur_verwerfen keine EIGENE Transaktion starten.
        assert not im_verwerfen, "mit Sitzung keine eigene Transaktion"
        return await echte_transaktion(fn)
    monkeypatch.setattr(deps, "transaktion", keine_eigene_transaktion)

    r = w.run(A.update_appointment(
        t.aid, A.AppointmentIn(status="abgeholt", stand=_STAND, korrektur_verwerfen=True), w.chef))
    assert r["ok"]
    assert len(aufrufe) == 1 and aufrufe[0]["nur_id"] == p2
    assert aufrufe[0]["session"] is not None
    assert _doc(w, "pickup_protocols", p2)["superseded"] is True
    assert _doc(w, "pickup_protocols", t.pid)["superseded"] is False
    assert _doc(w, "appointments", t.aid)["status"] == "abgeholt"


def test_review12_nur_die_gelesene_korrektur(welt):
    w = welt
    P = _m("routes.protocols")
    t = _abholung(w)
    p2 = _korrektur_offen(w, t)
    assert w.run(P.korrektur_verwerfen(t.aid, nur_id=f"andere_{w.s}")) is False
    _unveraendert(w, t, p2)
    assert w.run(P.korrektur_verwerfen(t.aid, nur_id=p2)) is True
    assert _doc(w, "pickup_protocols", p2)["superseded"] is True
    assert _doc(w, "pickup_protocols", t.pid)["superseded"] is False
