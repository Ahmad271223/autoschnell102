# -*- coding: utf-8 -*-
"""Rollenprüfung 22.09.2026 (Review, Welle 4) — Team Fahrer-App (routes/drivers.py).

  Sichtfrist   Ein Wechsel des Chefs zwischen ENDZUSTAENDEN (abgeholt -> erledigt,
               V-12 abgeholt -> nicht abgeholt, storniert <-> nicht abgeholt)
               startet die Sichtfrist der Fahrer-App nicht neu: die eingefrorene
               Uhr (zuletzt_abgeschlossen_am) zaehlt statt status_changed_at.
               Wieder-Oeffnen + neu Abschliessen (RP-237) bleibt sichtbar.
  Abhol-Check  Wiederholung mit demselben Idempotenz-Schluessel nur bei gleichem
               Inhalt (200 "wiederholt"); anderer Inhalt -> 409 statt still der
               alte Bericht. Ein in der Wiederholung fehlendes Foto (Zwischenstand
               ohne Fotos) gilt nicht als anderer Inhalt.

In-Prozess gegen DB_NAME (welt-Fixture aus test_golive_20260913_abschluss).
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
# Module am Dateianfang laden — nie erstmals in der Test-Schleife (Loop-Bindung).
import beweis_service  # noqa: E402,F401
import routes.drivers as D  # noqa: E402
import routes.protocols as P  # noqa: E402,F401
from test_golive_20260913_abschluss import (  # noqa: E402,F401  (welt = Fixture)
    _PNG_B64, _abholung, _doc, _jetzt, welt)

UHR = D.SICHTFRIST_UHR


def _vor(tage=0, stunden=0):
    return (datetime.now(timezone.utc) - timedelta(days=tage, hours=stunden)).isoformat()


def _fehler(w, coro):
    with pytest.raises(HTTPException) as e:
        w.run(coro)
    return e.value.status_code, e.value.detail


def _termin(w, tid, **extra):
    doc = {"id": tid, "dealer_id": w.dealer_id, "driver_id": w.driver["id"],
           "status": "offen", "zuteilung": "angenommen", "created_by": w.chef["id"],
           "pickup_date": "2099-09-10", "pickup_time": "10:00", "title": f"Fahrt {tid}",
           "pickup_address": "Musterstr. 5, 30159 Hannover", "seller_name": "Vera",
           "seller_phone": "0511 999", "created_at": _jetzt(), "updated_at": _jetzt()}
    doc.update(extra)
    w.run(w.db.appointments.insert_one(doc))
    return doc


async def _chef_wechselt_status(w, tid, neu):
    """So, wie der Terminplaner es nach der Uebergabe tun soll: status_changed_at
    frisch, dazu die Felder aus sichtfrist_bei_statuswechsel."""
    vorher = await w.db.appointments.find_one({"id": tid}, {"_id": 0})
    setzen, weg = D.sichtfrist_bei_statuswechsel(vorher, neu)
    update = {"$set": {"status": neu, "status_changed_at": _jetzt(), **setzen}}
    if weg:
        update["$unset"] = {f: "" for f in weg}
    await w.db.appointments.update_one({"id": tid}, update)


def _liste(w):
    return {a["id"] for a in w.run(D.driver_appointments(w.driver))}


# ============================================================ Sichtfrist (Helfer)
def test_sichtfrist_helfer_regeln():
    a, s = "2026-08-01T00:00:00+00:00", "2026-08-02T00:00:00+00:00"
    # Endzustand -> anderer Endzustand: bisherigen Abschluss festhalten
    assert D.sichtfrist_bei_statuswechsel(
        {"status": "abgeholt", "abgeschlossen_seit": a, "status_changed_at": s},
        "erledigt") == ({UHR: s}, [])
    # schon eingefroren -> bleibt (weiterer Wechsel startet nichts neu)
    assert D.sichtfrist_bei_statuswechsel(
        {"status": "erledigt", "abgeschlossen_seit": a, "status_changed_at": "2026-09-20",
         UHR: s}, "nicht abgeholt") == ({UHR: s}, [])
    # offen -> geschlossen und Wieder-Oeffnen: Uhr weg (status_changed_at zaehlt)
    assert D.sichtfrist_bei_statuswechsel({"status": "offen", UHR: s}, "abgeholt") == ({}, [UHR])
    assert D.sichtfrist_bei_statuswechsel({"status": "abgeholt", UHR: s}, "offen") == ({}, [UHR])
    assert D.sichtfrist_bei_statuswechsel({}, "storniert") == ({}, [UHR])
    # kein Wechsel -> nichts
    assert D.sichtfrist_bei_statuswechsel({"status": "erledigt", UHR: s}, "erledigt") == ({}, [])
    # Altbestand ohne jeden Zeitstempel -> nichts festzuhalten
    assert D.sichtfrist_bei_statuswechsel({"status": "abgeholt"}, "erledigt") == ({}, [])
    # _letzter_abschluss: eingefrorene Uhr statt status_changed_at
    assert D._letzter_abschluss({"abgeschlossen_seit": a, "status_changed_at": "2026-09-20",
                                 UHR: s}) == s
    assert D._letzter_abschluss({"abgeschlossen_seit": a, "status_changed_at": "2026-09-20"}) \
        == "2026-09-20"


# ============================================================ Sichtfrist (Liste + Unterlagen)
def test_endzustand_wechsel_startet_sichtfrist_nicht_neu(welt):
    w = welt
    # Tag 0 abgeholt, an "Tag 60" setzt der Chef abgeholt -> erledigt
    er, tag0 = f"sf_erl_{w.s}", _vor(60)
    _termin(w, er, status="abgeholt", abgeschlossen_seit=tag0, status_changed_at=tag0,
            contract_id=f"c_sf_{w.s}")
    w.run(_chef_wechselt_status(w, er, "erledigt"))
    t = _doc(w, "appointments", er)
    assert t["status"] == "erledigt" and t[UHR] == tag0, "Uhr haelt den ersten Abschluss fest"
    assert t["status_changed_at"] > tag0, "status_changed_at ist frisch"
    # V-12: abgeholt vor 40 Tagen, gestern auf "nicht abgeholt" (30-Tage-Frist)
    na = f"sf_na_{w.s}"
    _termin(w, na, status="abgeholt", abgeschlossen_seit=_vor(40), status_changed_at=_vor(40))
    w.run(_chef_wechselt_status(w, na, "nicht abgeholt"))
    # storniert vor 45 Tagen -> heute "nicht abgeholt"
    st = f"sf_st_{w.s}"
    _termin(w, st, status="storniert", abgeschlossen_seit=_vor(45), status_changed_at=_vor(45))
    w.run(_chef_wechselt_status(w, st, "nicht abgeholt"))
    # RP-237 bleibt: abgeholt vor 40 Tagen, wieder geoeffnet, gestern neu abgeholt
    kor = f"sf_kor_{w.s}"
    _termin(w, kor, status="abgeholt", abgeschlossen_seit=_vor(40), status_changed_at=_vor(40),
            **{UHR: _vor(40)})
    w.run(_chef_wechselt_status(w, kor, "offen"))
    assert UHR not in _doc(w, "appointments", kor), "Wieder-Oeffnen loescht die Uhr"
    w.run(_chef_wechselt_status(w, kor, "abgeholt"))
    # Endzustand-Wechsel innerhalb der Frist: bleibt sichtbar (Uhr = gestern)
    frisch = f"sf_frisch_{w.s}"
    _termin(w, frisch, status="abgeholt", abgeschlossen_seit=_vor(1), status_changed_at=_vor(1))
    w.run(_chef_wechselt_status(w, frisch, "erledigt"))

    ids = _liste(w)
    assert er not in ids and na not in ids and st not in ids, ids
    assert kor in ids and frisch in ids
    for tid in (er, na):
        with pytest.raises(HTTPException) as e:
            D.unterlagen_zugriff_oder_404(_doc(w, "appointments", tid))
        assert e.value.status_code == 404
    D.unterlagen_zugriff_oder_404(_doc(w, "appointments", kor))
    D.unterlagen_zugriff_oder_404(_doc(w, "appointments", frisch))
    # Dokument-Wege mit eigener Projektion (Kaufvertrag, Snapshot, beweise.py)
    # laden die Uhr mit: die alte Fahrt legitimiert keinen Zugriff mehr.
    code, text = _fehler(w, D._erste_fahrt_mit_zugriff(
        {"id": er}, {"_id": 0, "id": 1, "status": 1, "abgeschlossen_seit": 1,
                     "status_changed_at": 1, "updated_at": 1}, "Kein Zugriff"))
    assert code == 404 and "abgeschlossen" in text
    code, _ = _fehler(w, D.driver_contract_pdf(f"c_sf_{w.s}", w.driver))
    assert code == 404


def test_fahrer_abschluss_entfernt_alte_uhr(welt):
    """offen -> geschlossen durch den Fahrer: eine stehengebliebene Uhr gilt
    nicht mehr — die gerade abgeschlossene Fahrt bleibt in der App."""
    w = welt
    tid = f"sf_fahrer_{w.s}"
    _termin(w, tid, abgeschlossen_seit=_vor(50), **{UHR: _vor(50)})
    r = w.run(D.driver_set_status(tid, D.DriverStatusIn(status="nicht abgeholt"), w.driver))
    assert r["ok"] is True
    t = _doc(w, "appointments", tid)
    assert t["status"] == "nicht abgeholt" and UHR not in t
    assert tid in _liste(w)


# ============================================================ Abhol-Check: Idempotenz nur bei gleichem Inhalt
def test_abholcheck_wiederholung_nur_mit_gleichem_inhalt(welt):
    w = welt
    t = _abholung(w, name="fp", proto_status="final")
    k = f"kfp-{w.s}"
    try:
        r1 = w.run(D.driver_submit_report(t.aid, D.PickupReportIn(
            mileage_at_pickup=85120, notes="alles gut", client_bericht_id=k), w.driver))
        assert r1["version"] == 1
        bericht = w.run(w.db.pickup_reports.find_one({"id": r1["report_id"]}, {"_id": 0}))
        assert set(bericht["client_bericht_fp"]) == {"text", "fotos"}
        # Gleicher Inhalt -> 200 wiederholt
        wieder = w.run(D.driver_submit_report(t.aid, D.PickupReportIn(
            mileage_at_pickup=85120, notes="alles gut", client_bericht_id=k), w.driver))
        assert wieder["wiederholt"] is True and wieder["report_id"] == r1["report_id"]
        # Chef hat wieder geoeffnet, Fahrer schickt mit altem Schluessel NEUE Angaben
        code, text = _fehler(w, D.driver_submit_report(t.aid, D.PickupReportIn(
            mileage_at_pickup=90000, notes="alles gut", client_bericht_id=k), w.driver))
        assert code == 409 and text == D.BERICHT_ANDERER_INHALT
        code, _ = _fehler(w, D.driver_submit_report(t.aid, D.PickupReportIn(
            mileage_at_pickup=85120, notes="alles gut", client_bericht_id=k,
            deviations=[D.DeviationIn(field="damage", label="Kratzer")]), w.driver))
        assert code == 409
        assert w.run(w.db.pickup_reports.count_documents({"appointment_id": t.aid})) == 1
        # Mit neuem Schluessel (so macht es die App nach dem 409) -> Version 2
        r2 = w.run(D.driver_submit_report(t.aid, D.PickupReportIn(
            mileage_at_pickup=90000, notes="alles gut", client_bericht_id=f"{k}-2"), w.driver))
        assert r2["version"] == 2 and "wiederholt" not in r2
    finally:
        w.run(w.db.pickup_reports.delete_many({"appointment_id": t.aid}))


def test_abholcheck_foto_fehlt_in_wiederholung_ist_derselbe_bericht(welt):
    w = welt
    t = _abholung(w, name="fpf", proto_status="final")
    k = f"kfpf-{w.s}"
    abw = dict(field="damage", label="Kratzer hinten", note="")
    try:
        r1 = w.run(D.driver_submit_report(t.aid, D.PickupReportIn(
            mileage_at_pickup=1000, client_bericht_id=k,
            deviations=[D.DeviationIn(**abw, photo_b64=_PNG_B64)]), w.driver))
        # Zwischenstand ohne Fotos (zu gross fuer den Tab-Speicher) -> derselbe Bericht
        wieder = w.run(D.driver_submit_report(t.aid, D.PickupReportIn(
            mileage_at_pickup=1000, client_bericht_id=k,
            deviations=[D.DeviationIn(**abw)]), w.driver))
        assert wieder["wiederholt"] is True and wieder["report_id"] == r1["report_id"]
        # ein ANDERES Foto -> anderer Inhalt (Pruefung vor jeder Bildverarbeitung)
        code, text = _fehler(w, D.driver_submit_report(t.aid, D.PickupReportIn(
            mileage_at_pickup=1000, client_bericht_id=k,
            deviations=[D.DeviationIn(**abw, photo_b64="QW5kZXJlc0ZvdG8=")]), w.driver))
        assert code == 409 and text == D.BERICHT_ANDERER_INHALT
    finally:
        w.run(w.db.pickup_reports.delete_many({"appointment_id": t.aid}))


def test_abholcheck_altbestand_ohne_fingerabdruck_wie_bisher(welt):
    w = welt
    t = _abholung(w, name="fpa", proto_status="final")
    k = f"kfpa-{w.s}"
    try:
        r1 = w.run(D.driver_submit_report(t.aid, D.PickupReportIn(
            mileage_at_pickup=1, client_bericht_id=k), w.driver))
        w.run(w.db.pickup_reports.update_one({"id": r1["report_id"]},
                                             {"$unset": {"client_bericht_fp": ""}}))
        wieder = w.run(D.driver_submit_report(t.aid, D.PickupReportIn(
            mileage_at_pickup=2, client_bericht_id=k), w.driver))
        assert wieder["wiederholt"] is True and wieder["report_id"] == r1["report_id"]
    finally:
        w.run(w.db.pickup_reports.delete_many({"appointment_id": t.aid}))


def test_gleicher_bericht_regeln():
    fp = D._bericht_fingerabdruck(D.PickupReportIn(
        mileage_at_pickup=5, deviations=[D.DeviationIn(label="a", photo_b64="x"),
                                         D.DeviationIn(label="b")]))
    assert fp["fotos"][0] and fp["fotos"][1] is None
    assert D._gleicher_bericht(fp, fp) and D._gleicher_bericht(None, fp)
    ohne_foto = {**fp, "fotos": [None, None]}
    assert D._gleicher_bericht(fp, ohne_foto)
    assert not D._gleicher_bericht(fp, {**fp, "fotos": [None, "y"]})
    assert not D._gleicher_bericht(fp, {**fp, "text": "anders"})
