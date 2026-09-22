# -*- coding: utf-8 -*-
"""Rollenpruefung 22.09.2026 — Team Fahrer-App (routes/drivers.py).

  RP-013/112/263  Konfliktliste: Sucher sehen Termine der Kollegen nur als "belegt um".
  RP-066/165      Abholbericht mit Idempotenz-Schluessel (Wiederholung = 200).
  RP-168          /driver/me listet gesperrte Firmen nicht mehr.
  RP-175          Fahrer nicht entfernbar, solange ein Protokoll beim Chef liegt.
  RP-234/385      Ablehnen mit "$..." im Namen/Grund (Update-Pipeline, $literal).
  RP-235/386      Stornierte Fahrten ohne Verkaeuferkontakt/Notizen/Dokumente.
  RP-236/387      Chef-Notizen erst nach der Annahme.
  RP-237/388      Sichtfrist ab dem LETZTEN Abschluss.
  RP-238/389      Adressen ohne PLZ vor der Annahme nicht mehr komplett.
  RP-239/390      Vertrag/Snapshot: erster Kandidat, der die Frist besteht.
  RP-240/391      Vertrag in laufender Loeschung nicht mehr ladbar.
  RP-457          Fahrer-ID ohne Bindestrich/klein geschrieben.
  RP-537          Grund fuer "nicht abgeholt" im Verlauf.
  RP-542          Telefonnummer + offene Fahrten in der Fahrerliste des Chefs.

In-Prozess gegen DB_NAME (welt-Fixture aus test_golive_20260913_abschluss:
Chef, Firma, zwei Fahrerkonten aus der Kontenanlage, Storage/PDF gefaelscht).
"""
import base64
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
# Module am Dateianfang laden — nie erstmals in der Test-Schleife (Loop-Bindung).
import beweis_service  # noqa: E402,F401
import routes.drivers as D  # noqa: E402
import routes.protocols as P  # noqa: E402
from test_golive_20260913_abschluss import (  # noqa: E402,F401  (welt = Fixture)
    _abholung, _doc, _jetzt, welt)


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


# ============================================================ RP-238/389
@pytest.mark.parametrize("adresse,erwartet", [
    ("Musterstr. 5, 30159 Hannover", "30159 Hannover"),     # unveraendert (PLZ)
    ("Hannover", "Hannover"),
    ("Musterstr. 5, Hannover", "Hannover"),
    ("Musterstr. 5", ""),
    ("Am Marktplatz, Musterdorf", "Musterdorf"),
    ("Lindenallee Musterstadt", ""),
    ("Hauptstraße, Musterdorf", "Musterdorf"),
    ("Am Markt Musterdorf", ""),
    ("Musterdorf, Hauptstraße", ""),
    ("Frankfurt am Main", "Frankfurt am Main"),                # "am" nur am Anfang
    ("Thüringen", "Thüringen"),                                # "...ring" nur am Wortende
    ("", ""),
])
def test_rp238_ort_ohne_strasse_ohne_plz(adresse, erwartet):
    assert D.ort_ohne_strasse(adresse) == erwartet


# ============================================================ RP-234/385
def test_rp234_notiz_ausdruck_nimmt_nutzertext_woertlich():
    ausdruck = D.notiz_anhaengen_ausdruck("$seller_phone")
    assert ausdruck["$concat"][-1] == {"$literal": "$seller_phone"}


def test_rp234_ablehnen_mit_dollar_im_namen_und_grund(welt):
    w = welt
    _termin(w, f"abl_{w.s}", zuteilung="offen", notes="Chef-Notiz")
    fahrer = {**w.driver, "display_name": "$seller_phone"}
    r = w.run(D.driver_zuteilung(f"abl_{w.s}", D.DriverZuteilungIn(action="ablehnen", grund="$"),
                                 fahrer))
    assert r == {"ok": True, "zuteilung": "abgelehnt"}
    t = _doc(w, "appointments", f"abl_{w.s}")
    assert t["zuteilung_abgelehnt_von"] == "$seller_phone", "Name nicht als Feldpfad gelesen"
    assert t["zuteilung_abgelehnt_grund"] == "$"
    assert t["notes"] == "Chef-Notiz\n[Fahrer] Fahrt abgelehnt: $"
    assert "driver_id" not in t and isinstance(t["updated_at"], str)


# ============================================================ RP-013/112/263
def test_rp013_konflikte_sucher_sieht_kollegen_nur_als_belegt(welt):
    w = welt
    sucher = {"id": f"su_{w.s}", "dealer_id": w.dealer_id, "role": "sucher"}
    datum = "2099-10-01"
    _termin(w, f"k_eigen_{w.s}", pickup_date=datum, created_by=sucher["id"], pickup_time="09:00")
    _termin(w, f"k_kollege_{w.s}", pickup_date=datum, created_by=w.chef["id"], pickup_time="11:00")
    # eigener Vertrag des Suchers, Termin vom Chef angelegt -> gehoert zum Bereich
    w.run(w.db.generated_pdfs.insert_one({"id": f"c_su_{w.s}", "dealer_id": w.dealer_id,
                                          "user_id": sucher["id"], "created_at": _jetzt()}))
    _termin(w, f"k_vertrag_{w.s}", pickup_date=datum, created_by=w.chef["id"],
            contract_id=f"c_su_{w.s}", pickup_time="13:00")

    r = w.run(D.driver_conflicts(w.driver["id"], datum, sucher))
    assert r["count"] == 3 and len(r["conflicts"]) == 3
    nach_zeit = {c["pickup_time"]: c for c in r["conflicts"]}
    for zeit, tid in (("09:00", f"k_eigen_{w.s}"), ("13:00", f"k_vertrag_{w.s}")):
        c = nach_zeit[zeit]
        assert c["id"] == tid and c["pickup_address"] and c["title"].startswith("Fahrt")
    kollege = nach_zeit["11:00"]
    assert kollege["id"] is None and kollege["title"] == "Andere Fahrt"
    assert "pickup_address" not in kollege and "dealer_id" not in kollege
    assert kollege["is_own"] is True, "Firmenzugehoerigkeit bleibt, nur ohne Details"
    # Der Chef sieht weiter alles der Firma
    r = w.run(D.driver_conflicts(w.driver["id"], datum, w.chef))
    assert sorted(c["id"] for c in r["conflicts"]) == sorted(
        [f"k_eigen_{w.s}", f"k_kollege_{w.s}", f"k_vertrag_{w.s}"])


# ============================================================ RP-235/236/386/387
def test_rp235_236_storniert_und_vor_annahme_ohne_kontakt_und_notizen(welt):
    w = welt
    _termin(w, f"st_{w.s}", status="storniert", status_changed_at=_vor(1),
            abgeschlossen_seit=_vor(1), notes="Tel 0170 123", contract_id=f"c_st_{w.s}")
    _termin(w, f"va_{w.s}", zuteilung="offen", notes="Schlüssel beim Nachbarn, Tel 0170")
    _termin(w, f"an_{w.s}", notes="Bitte klingeln")
    liste = {a["id"]: a for a in w.run(D.driver_appointments(w.driver))}
    st = liste[f"st_{w.s}"]
    assert st["seller_name"] is None and st["seller_phone"] is None
    assert st["pickup_address"] == "30159 Hannover" and st["notes"] is None
    assert st["contract_id"] is None and st["beweis_id"] is None and st["snapshot_id"] is None
    assert st["kontakt_nach_annahme"] is False and st["notizen_nach_annahme"] is False
    va = liste[f"va_{w.s}"]
    assert va["notes"] is None and va["notizen_nach_annahme"] is True
    assert va["seller_name"] is None and va["kontakt_nach_annahme"] is True
    an = liste[f"an_{w.s}"]
    assert an["notes"] == "Bitte klingeln" and an["notizen_nach_annahme"] is False
    assert an["seller_phone"] == "0511 999"


# ============================================================ RP-237/388
def test_rp237_sichtfrist_ab_letztem_abschluss(welt):
    w = welt
    _termin(w, f"korr_{w.s}", status="abgeholt", abgeschlossen_seit=_vor(40),
            status_changed_at=_vor(1))
    _termin(w, f"alt_{w.s}", status="abgeholt", abgeschlossen_seit=_vor(40),
            status_changed_at=_vor(40))
    ids = {a["id"] for a in w.run(D.driver_appointments(w.driver))}
    assert f"korr_{w.s}" in ids, "korrigierte, gestern neu abgeschlossene Fahrt bleibt sichtbar"
    assert f"alt_{w.s}" not in ids
    D.unterlagen_zugriff_oder_404({"status": "abgeholt", "abgeschlossen_seit": _vor(40),
                                   "status_changed_at": _vor(1)})
    with pytest.raises(HTTPException) as e:
        D.unterlagen_zugriff_oder_404({"status": "abgeholt", "abgeschlossen_seit": _vor(40),
                                       "status_changed_at": _vor(40)})
    assert e.value.status_code == 404
    assert D._letzter_abschluss({"abgeschlossen_seit": "2026-09-01", "status_changed_at": "2026-09-20"}) \
        == "2026-09-20"
    assert D._letzter_abschluss({"updated_at": "2026-09-02"}) == "2026-09-02"


# ============================================================ RP-239/240/390/391
def test_rp239_240_vertrag_erster_gueltiger_kandidat_und_loeschung(welt):
    w = welt
    cid = f"c_kand_{w.s}"
    # Die ALTE, abgelaufene Fahrt zuerst — find_one haette sie genommen (404).
    _termin(w, f"kand_alt_{w.s}", contract_id=cid, status="abgeholt",
            abgeschlossen_seit=_vor(60), status_changed_at=_vor(60))
    _termin(w, f"kand_neu_{w.s}", contract_id=cid)
    w.run(w.db.generated_pdfs.insert_one(
        {"id": cid, "dealer_id": w.dealer_id, "user_id": w.chef["id"],
         "pdf_b64": base64.b64encode(b"%PDF-1.4 test").decode(), "created_at": _jetzt()}))
    antwort = w.run(D.driver_contract_pdf(cid, w.driver))
    assert antwort.status_code == 200 and antwort.body.startswith(b"%PDF")
    # Nur noch die abgelaufene Fahrt -> 404 wie bisher
    w.run(w.db.appointments.delete_one({"id": f"kand_neu_{w.s}"}))
    code, text = _fehler(w, D.driver_contract_pdf(cid, w.driver))
    assert code == 404 and "abgeschlossen" in text
    # RP-240: Loeschung laeuft -> kein Vertrag mehr
    _termin(w, f"kand_neu2_{w.s}", contract_id=cid)
    w.run(w.db.generated_pdfs.update_one({"id": cid}, {"$set": {"loeschung": {"status": "laeuft"}}}))
    code, text = _fehler(w, D.driver_contract_pdf(cid, w.driver))
    assert code == 404 and text == "Vertrag nicht gefunden"


def test_rp239_snapshot_erster_gueltiger_kandidat(welt):
    w = welt
    vid, sid = f"v_snap_{w.s}", f"snap_rp_{w.s}"
    _termin(w, f"sn_alt_{w.s}", vehicle_id=vid, status="abgeholt",
            abgeschlossen_seit=_vor(60), status_changed_at=_vor(60))
    _termin(w, f"sn_neu_{w.s}", vehicle_id=vid)
    w.run(w.db.listing_snapshots.insert_one({"id": sid, "vehicle_id": vid, "status": "ready"}))
    try:
        # Zugriff besteht -> erst danach fehlt die Datei (ohne pdf_path)
        code, text = _fehler(w, D.driver_snapshot(sid, "pdf", w.driver))
        assert code == 404 and text == "Datei fehlt im Objectstore", text
        w.run(w.db.appointments.delete_one({"id": f"sn_neu_{w.s}"}))
        code, text = _fehler(w, D.driver_snapshot(sid, "pdf", w.driver))
        assert code == 404 and "abgeschlossen" in text
    finally:
        w.run(w.db.listing_snapshots.delete_many({"id": sid}))


# ============================================================ RP-175 (+ RP-179 Punkt 3)
def test_rp175_fahrer_nicht_entfernbar_solange_protokoll_beim_chef(welt):
    w = welt
    t = _abholung(w, proto_status="zur_freigabe")
    for status in ("zur_freigabe", "freigegeben", "wird_abgeschlossen"):
        w.run(w.db.pickup_protocols.update_one({"id": t.pid}, {"$set": {"status": status}}))
        code, text = _fehler(w, D.delete_driver(w.driver["id"], w.chef))
        assert code == 409 and "Freigaben" in text, (status, text)
        assert w.run(w.db.dealer_drivers.find_one(
            {"dealer_id": w.dealer_id, "driver_account_id": w.driver["id"]})), "Verknuepfung bleibt"
    # Ein ersetztes (superseded) Protokoll zaehlt nicht; zurueckgeschickt (Entwurf) -> erlaubt
    w.run(w.db.pickup_protocols.update_one({"id": t.pid}, {"$set": {"status": "entwurf"}}))
    r = w.run(D.delete_driver(w.driver["id"], w.chef))
    assert r["ok"] is True and r["offene_termine_getrennt"] == 1
    assert "driver_id" not in _doc(w, "appointments", t.aid)
    # nicht (mehr) verknuepft -> 404 wie bisher
    code, _ = _fehler(w, D.delete_driver(w.driver["id"], w.chef))
    assert code == 404


def test_rp175_abgeschlossene_fahrt_mit_altem_protokoll_sperrt_nicht(welt):
    w = welt
    t = _abholung(w, proto_status="zur_freigabe")
    # Termin geschlossen (z. B. storniert) -> ein haengengebliebenes Protokoll
    # blockiert das Entfernen nicht (nur OFFENE Fahrten verlieren den Fahrer).
    w.run(w.db.appointments.update_one({"id": t.aid}, {"$set": {"status": "storniert"}}))
    assert w.run(D.delete_driver(w.driver["id"], w.chef))["ok"] is True


# ============================================================ RP-066/165
def test_rp066_abholbericht_wiederholung_liefert_gespeicherten(welt):
    w = welt
    t = _abholung(w, proto_status="final")
    k1, k2 = f"k1-{w.s}", f"k2-{w.s}"
    r1 = w.run(D.driver_submit_report(t.aid, D.PickupReportIn(mileage_at_pickup=85120,
                                                                client_bericht_id=k1), w.driver))
    assert r1["version"] == 1 and "wiederholt" not in r1
    nochmal = w.run(D.driver_submit_report(t.aid, D.PickupReportIn(mileage_at_pickup=85120,
                                                                    client_bericht_id=k1), w.driver))
    assert nochmal["wiederholt"] is True and nochmal["report_id"] == r1["report_id"]
    assert nochmal["version"] == 1
    assert w.run(w.db.pickup_reports.count_documents({"appointment_id": t.aid})) == 1
    bericht = w.run(w.db.pickup_reports.find_one({"id": r1["report_id"]}, {"_id": 0}))
    assert bericht["client_bericht_id"] == k1
    # Neuer Schluessel = neuer Bericht (Korrekturversion wie bisher)
    r2 = w.run(D.driver_submit_report(t.aid, D.PickupReportIn(client_bericht_id=k2), w.driver))
    assert r2["version"] == 2
    # Termin inzwischen "abgeholt": Wiederholung von k1 -> 200 statt irrefuehrendem 409
    # (Rollenprüfung 22.09.2026, Review: mit DEMSELBEN Inhalt — anderer Inhalt
    # unter demselben Schluessel -> 409, siehe test_rp_fahrer_app_welle4).
    w.run(w.db.appointments.update_one({"id": t.aid}, {"$set": {"status": "abgeholt",
                                                               "status_changed_at": _jetzt()}}))
    wieder = w.run(D.driver_submit_report(t.aid, D.PickupReportIn(mileage_at_pickup=85120,
                                                                   client_bericht_id=k1), w.driver))
    assert wieder["wiederholt"] is True and wieder["report_id"] == r1["report_id"]
    code, text = _fehler(w, D.driver_submit_report(
        t.aid, D.PickupReportIn(client_bericht_id=f"k3-{w.s}"), w.driver))
    assert code == 409 and "nur einmal" in text
    # Ohne Schluessel (aeltere App) unveraendert
    code, _ = _fehler(w, D.driver_submit_report(t.aid, D.PickupReportIn(), w.driver))
    assert code == 409


def test_rp066_reservierung_desselben_berichts_meldet_speichern(welt):
    w = welt
    t = _abholung(w, name="res", proto_status="final")
    k = f"kres-{w.s}"
    w.run(w.db.appointments.update_one({"id": t.aid}, {"$set": {
        "status": "abgeholt", "status_changed_at": _jetzt(),
        "erstbericht_reserviert_at": _jetzt(), "erstbericht_schluessel": k}}))
    code, text = _fehler(w, D.driver_submit_report(t.aid, D.PickupReportIn(client_bericht_id=k),
                                                   w.driver))
    assert code == 409 and text == D.BERICHT_WIRD_GESPEICHERT
    code, text = _fehler(w, D.driver_submit_report(
        t.aid, D.PickupReportIn(client_bericht_id=f"anders-{w.s}"), w.driver))
    assert code == 409 and "nur einmal" in text


def test_rp066_schluessel_format():
    with pytest.raises(Exception):
        D.PickupReportIn(client_bericht_id="kurz")
    with pytest.raises(Exception):
        D.PickupReportIn(client_bericht_id="$ungueltig$zeichen")
    assert D.PickupReportIn(client_bericht_id=str(uuid.uuid4())).client_bericht_id


# ============================================================ RP-457 + RP-542
def test_rp457_542_fahrer_id_normalisiert_telefon_und_offene_fahrten(welt):
    w = welt
    w.run(w.db.dealer_drivers.delete_many({"driver_account_id": w.driver["id"]}))
    w.run(w.db.driver_accounts.update_one({"id": w.driver["id"]}, {"$set": {"phone": "0511 4711"}}))
    code = w.driver["driver_code"]
    assert code.startswith("FD-")
    eingabe = " " + code.replace("-", "").lower() + " "       # "fd7k2m9qx4"
    r = w.run(D.add_driver_by_code(D.DriverLinkIn(driver_code=eingabe), w.chef))
    assert r["id"] == w.driver["id"] and r["phone"] == "0511 4711"
    _termin(w, f"of1_{w.s}")
    _termin(w, f"of2_{w.s}", status="verschoben")
    _termin(w, f"zu_{w.s}", status="abgeholt", status_changed_at=_jetzt())
    liste = {d["id"]: d for d in w.run(D.list_drivers(w.chef))}
    assert liste[w.driver["id"]]["phone"] == "0511 4711"
    assert liste[w.driver["id"]]["offene_fahrten"] == 2
    assert liste[w.driver2["id"]]["offene_fahrten"] == 0
    # Sucher: weder Telefonnummer noch Zaehler
    sucher = {"id": f"su2_{w.s}", "dealer_id": w.dealer_id, "role": "sucher"}
    knapp = {d["id"]: d for d in w.run(D.list_drivers(sucher))}
    assert knapp[w.driver["id"]]["phone"] is None and "offene_fahrten" not in knapp[w.driver["id"]]
    # Unbekannter Code weiter 404
    code_, _ = _fehler(w, D.add_driver_by_code(D.DriverLinkIn(driver_code="FD-ZZZZZZZZ"), w.chef))
    assert code_ == 404


# ============================================================ RP-168
def test_rp168_driver_me_ohne_gesperrte_firmen(welt):
    w = welt
    d2, u2 = f"d2_rp_{w.s}", f"u2_rp_{w.s}"
    try:
        w.run(w.db.users.insert_one({"id": u2, "dealer_id": d2, "role": "dealer", "active": False,
                                     "created_at": _jetzt()}))
        w.run(w.db.dealers.insert_one({"id": d2, "user_id": u2, "company_name": "Gesperrt GmbH"}))
        w.run(w.db.dealer_drivers.insert_one({"id": str(uuid.uuid4()), "dealer_id": d2,
                                              "driver_account_id": w.driver["id"],
                                              "added_at": _jetzt()}))
        me = w.run(D.driver_me(w.driver))
        ids = [d["id"] for d in me["dealers"]]
        assert w.dealer_id in ids and d2 not in ids
    finally:
        w.run(w.db.dealer_drivers.delete_many({"dealer_id": d2}))
        w.run(w.db.dealers.delete_many({"id": d2}))
        w.run(w.db.users.delete_many({"id": u2}))


# ============================================================ RP-537
def test_rp537_grund_fuer_nicht_abgeholt_im_verlauf(welt):
    w = welt
    _termin(w, f"na_{w.s}")
    grund = "Nicht abgeholt: Verkäufer hat abgesagt — $kein Feldpfad"
    r = w.run(D.driver_set_status(f"na_{w.s}", D.DriverStatusIn(status="nicht abgeholt", notes=grund),
                                  w.driver))
    assert r["ok"] is True
    t = _doc(w, "appointments", f"na_{w.s}")
    assert t["status"] == "nicht abgeholt" and t["notes"].endswith(f"[Fahrer] {grund}")
    log = w.run(w.db.activity_logs.find_one({"dealer_id": w.dealer_id, "ref": f"na_{w.s}",
                                             "action": "termin.fahrer.nicht_abgeholt"}, {"_id": 0}))
    assert log and log["meta"]["grund"] == grund
