# -*- coding: utf-8 -*-
"""Ueberweisung mit GiroCode im Abholauftrag (Wunsch Ahmad 24.11.2026).

  * IBAN-Pruefung (SEPA-Land, Laenge, Mod-97), Normalisierung, Anzeige
  * GiroCode-Inhalt exakt nach EPC069-12 (Version 002, UTF-8, SCT)
  * Betrag offen vs. fest (Protokoll-Freigabe, geaenderter Preis, Abholung ohne Protokoll)
  * Als ueberwiesen markieren: atomar, zweites Mal 409, alter Stand 409, Betrag offen 409
  * Rueckgaengig nur Hauptchef; Sucher nur eigene Vorgaenge; fremde Firma 404
  * Fahrer: Erfassen, Bar -> 409, nach Markierung 409, sieht "ueberwiesen"; darf nicht markieren
  * Meldungen je einmal; Loeschung mit dem Vertrag und nach Frist; keine IBAN in Logs

In-Prozess gegen eine Wegwerf-DB (autoschnell_wt_a_<uuid>). Die HTTP-Proben am
Ende laufen nur mit UEBERWEISUNG_HTTP=<Basis-URL> und DB_NAME des Test-Backends.
"""
import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
WURZEL = Path(__file__).resolve().parents[2]
IBAN_DE = "DE89370400440532013000"


def _jetzt():
    return datetime.now(timezone.utc).isoformat()


def _module(name):
    import importlib
    return importlib.import_module(name)


# =================================================================== reine Funktionen
def test_01_iban_pruefen_und_normalisieren():
    U = _module("ueberweisung")
    assert U.iban_pruefen(" de89 3704-0044 0532 0130 00 ") == IBAN_DE
    assert U.iban_formatiert(IBAN_DE) == "DE89 3704 0044 0532 0130 00"
    for gut in ("AT611904300234573201", "FR1420041010050500013M02606", "GB29NWBK60161331926819",
                "CH9300762011623852957", "NL91ABNA0417164300"):
        assert U.iban_pruefen(gut) == gut
    for schlecht, teil in (("", "eingeben"), ("DE89370400440532013001", "stimmt nicht"),
                           ("DE8937040044053201300", "22 Stellen"), ("1234", "ungültige Zeichen"),
                           ("TR330006100519786457841326", "SEPA"), ("DE89 3704 0044 0532 0130 0Ä", "ungültige")):
        with pytest.raises(ValueError) as e:
            U.iban_pruefen(schlecht)
        assert teil in str(e.value), (schlecht, str(e.value))


def test_02_kontoinhaber_und_namensvergleich():
    U = _module("ueberweisung")
    assert U.kontoinhaber_pruefen("  Max   Mustermann\n") == "Max Mustermann"
    with pytest.raises(ValueError):
        U.kontoinhaber_pruefen("x")
    with pytest.raises(ValueError):
        U.kontoinhaber_pruefen("a" * 71)
    assert U.namen_passen("Jürgen Müller", "MUELLER, JUERGEN")
    assert U.namen_passen("Jurgen Muller", "Jürgen Müller")
    assert U.namen_passen("Herr Dr. Max Mustermann", "mustermann max")
    assert U.namen_passen("Max Peter Mustermann", "Max Mustermann")
    assert U.namen_passen("Straße", "Strasse")
    assert not U.namen_passen("Erika Musterfrau", "Max Mustermann")
    assert not U.namen_passen("Max Müller", "Max Mustermann")
    assert U.namen_passen("", "Max Mustermann"), "ohne Vergleichsbasis keine Warnung"


def test_03_girocode_zeilen_exakt():
    U = _module("ueberweisung")
    t = U.girocode_text("Max Mustermann", "de89 3704 0044 0532 0130 00", 1500050,
                        "Kaufpreis VW Golf FIN WVWZZZ1JZXW000001")
    assert t.split("\n") == ["BCD", "002", "1", "SCT", "", "Max Mustermann", IBAN_DE,
                             "EUR15000.50", "", "", "Kaufpreis VW Golf FIN WVWZZZ1JZXW000001"]
    assert "\r" not in t
    ohne = U.girocode_text("Max Mustermann", IBAN_DE, None, "Kaufpreis")
    assert ohne.split("\n")[7] == "", "offener Betrag: Zeile 8 leer"
    assert len(ohne.split("\n")) == 11
    assert U.girocode_text("A" * 90, IBAN_DE, 100, "Z" * 200).split("\n")[5] == "A" * 70
    assert len(U.girocode_text("Max", IBAN_DE, 100, "Z" * 200).split("\n")[10]) == 140
    with pytest.raises(ValueError):
        U.girocode_text("Max", IBAN_DE, 0, "x")
    svg = U.girocode_svg(t)
    assert svg.startswith("<svg") and "<script" not in svg.lower()
    # Der QR-Code enthaelt genau diesen Text (segno: decodierbar ueber die Matrix-Gegenprobe)
    import segno
    assert segno.make_qr(t, error="m", boost_error=False, encoding="utf-8").svg_inline(
        scale=5, border=4, dark="#000000", light="#ffffff") == svg


def test_04_verwendungszweck_und_zahlungsart():
    U = _module("ueberweisung")
    assert U.verwendungszweck("VW Golf", "WVW1", "H-AB 1", "V-7") == "Kaufpreis VW Golf FIN WVW1"
    assert U.verwendungszweck("VW Golf", "", "H-AB 1", "V-7") == "Kaufpreis VW Golf H-AB 1 Vertrag V-7"
    assert U.zahlungsart({"payment_method": "Bar"})["ueberweisung"] is False
    assert U.zahlungsart({"contract_data": {"payment_method": "Echtzeitüberweisung"}})["echtzeit"] is True
    assert U.zahlungsart({"payment_method": "Bar / Überweisung"})["ueberweisung"] is True
    assert U.zahlungsart(None)["ueberweisung"] is True
    assert U.betrag_text(1500050) == "15.000,50 €"


# =================================================================== Welt
@pytest.fixture
def welt(monkeypatch):
    from motor.motor_asyncio import AsyncIOMotorClient
    s = uuid.uuid4().hex[:10]
    names = ["deps", "ueberweisung", "routes.ueberweisung", "routes.protocols", "routes.drivers",
             "routes.kundenportal"]
    mods = [_module(n) for n in names]
    alt = [(m, getattr(m, "db", None)) for m in mods]

    class _W:
        pass

    w = _W()
    w.s = s
    w.loop = asyncio.new_event_loop()
    asyncio.set_event_loop(w.loop)
    w.client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    w.db_name = f"autoschnell_wt_a_{s}"
    w.db = w.client[w.db_name]
    for m in mods:
        if hasattr(m, "db"):
            m.db = w.db
    w.run = lambda coro: w.loop.run_until_complete(coro)
    w.U = _module("ueberweisung")
    w.R = _module("routes.ueberweisung")
    w.run(w.U.indizes_anlegen(w.db))

    w.dealer_id = f"d_ue_{s}"
    w.chef = {"id": f"chef_ue_{s}", "dealer_id": w.dealer_id, "role": "dealer", "first_name": "Carla",
              "last_name": "Chef"}
    w.sucher = {"id": f"su_ue_{s}", "dealer_id": w.dealer_id, "role": "sucher", "first_name": "Sven",
                "last_name": "Sucher"}
    w.sucher2 = {"id": f"su2_ue_{s}", "dealer_id": w.dealer_id, "role": "sucher"}
    w.fremd = {"id": f"chef_fremd_{s}", "dealer_id": f"d_fremd_{s}", "role": "dealer"}
    w.fahrer = {"id": f"fa_ue_{s}", "active": True, "display_name": "Fritz Fahrer"}
    for u in (w.chef, w.sucher, w.sucher2, w.fremd):
        w.run(w.db.users.insert_one({**u, "active": True, "created_at": "2026-01-02T00:00:00+00:00"}))
    w.run(w.db.dealers.insert_many([
        {"id": w.dealer_id, "user_id": w.chef["id"], "company_name": "UE GmbH"},
        {"id": w.fremd["dealer_id"], "user_id": w.fremd["id"], "company_name": "Fremd GmbH"}]))
    w.run(w.db.driver_accounts.insert_one(dict(w.fahrer)))
    w.run(w.db.dealer_drivers.insert_one({"dealer_id": w.dealer_id, "driver_account_id": w.fahrer["id"]}))
    yield w
    try:
        w.run(w.client.drop_database(w.db_name))
    finally:
        for m, d in alt:
            if d is not None:
                m.db = d
        w.client.close()
        w.loop.close()


def _vorgang(w, tag, zahlung="Überweisung", status="offen", **termin_extra):
    cid, tid, vid = f"c_{tag}_{w.s}", f"t_{tag}_{w.s}", f"v_{tag}_{w.s}"
    w.run(w.db.vehicles.insert_one({"id": vid, "dealer_id": w.dealer_id,
                                    "data": {"make_label": "VW", "model_label": "Golf"}}))
    w.run(w.db.generated_pdfs.insert_one({
        "id": cid, "dealer_id": w.dealer_id, "user_id": w.sucher["id"], "version": 2,
        "contract_no": f"V-{tag}", "seller_name": "Jürgen Müller", "payment_method": zahlung,
        "vehicle_id": vid, "created_at": _jetzt(),
        "contract_data": {"purchase_price": 15000, "seller_name": "Jürgen Müller", "payment_method": zahlung,
                          "vehicle_make": "VW", "vehicle_model": "Golf", "vehicle_vin": "WVWZZZ1JZXW000001"}}))
    w.run(w.db.appointments.insert_one({
        "id": tid, "dealer_id": w.dealer_id, "vehicle_id": vid, "contract_id": cid, "status": status,
        "driver_id": w.fahrer["id"], "zuteilung": "angenommen", "created_by": w.sucher["id"],
        "seller_name": "Jürgen Müller", "pickup_date": "2026-11-25", "created_at": _jetzt(), **termin_extra}))
    return tid, cid


def _protokoll(w, tid, status, neuer_preis=None, stand="s1"):
    w.run(w.db.pickup_protocols.delete_many({"appointment_id": tid}))
    w.run(w.db.pickup_protocols.insert_one({
        "id": f"p_{tid}", "dealer_id": w.dealer_id, "appointment_id": tid, "status": status, "version": 1,
        "superseded": False, "neuer_preis": neuer_preis, "freigabe_stand": stand}))


def _konto(w, tid, iban="DE89 3704 0044 0532 0130 00", inhaber="Juergen Mueller"):
    return w.run(w.R.fahrer_kontodaten(tid, w.R.KontodatenIn(iban=iban, kontoinhaber=inhaber), dict(w.fahrer)))


def _karte(w, tid, user=None):
    return w.run(w.R.buero_ueberweisung(tid, dict(user or w.chef)))


def _markieren(w, tid, stand, user=None):
    return w.run(w.R.als_ueberwiesen_markieren(tid, w.R.UeberwiesenIn(stand=stand), dict(user or w.chef)))


def _fehler(w, coro):
    with pytest.raises(HTTPException) as e:
        w.run(coro)
    return e.value


# =================================================================== Betrag
def test_10_betrag_offen_ohne_betrag_im_qr(welt, monkeypatch):
    w = welt
    tid, _ = _vorgang(w, "offen")
    texte = []
    echt = w.U.girocode_svg
    monkeypatch.setattr(w.U, "girocode_svg", lambda t: (texte.append(t), echt(t))[1])
    _protokoll(w, tid, "entwurf")
    out = _konto(w, tid)
    assert out["iban_formatiert"] == "DE89 3704 0044 0532 0130 00"
    k = _karte(w, tid)
    assert k["betrag_fest"] is False and k["betrag_cents"] is None and k["betrag_text"] == ""
    assert texte[-1].split("\n")[7] == "", "QR ohne Betrag"
    assert k["girocode_svg"].startswith("<svg")
    assert k["verwendungszweck"] == "Kaufpreis VW Golf FIN WVWZZZ1JZXW000001"
    assert k["empfaenger"] == "Juergen Mueller" and k["iban"] == IBAN_DE
    assert not [x for x in k["warnungen"] if x["art"] == "name"], "Umlaut-tolerant"
    e = _fehler(w, w.R.als_ueberwiesen_markieren(tid, w.R.UeberwiesenIn(stand=k["stand"]), dict(w.chef)))
    assert e.status_code == 409 and "offen" in e.detail
    # zur Freigabe geschickt: weiter offen
    _protokoll(w, tid, "zur_freigabe")
    assert _karte(w, tid)["betrag_fest"] is False


def test_11_betrag_fest_nach_freigabe_und_geaenderter_preis(welt, monkeypatch):
    w = welt
    tid, _ = _vorgang(w, "fest")
    texte = []
    echt = w.U.girocode_svg
    monkeypatch.setattr(w.U, "girocode_svg", lambda t: (texte.append(t), echt(t))[1])
    _konto(w, tid)
    _protokoll(w, tid, "freigegeben")
    k = _karte(w, tid)
    assert k["betrag_fest"] and k["betrag_cents"] == 1500000 and k["betrag_text"] == "15.000,00 €"
    assert texte[-1].split("\n")[7] == "EUR15000.00"
    # Chef gibt mit neuem Preis frei (oder Vorschlag des Fahrers uebernommen)
    _protokoll(w, tid, "freigegeben", neuer_preis=14200.5, stand="s2")
    k2 = _karte(w, tid)
    assert k2["betrag_cents"] == 1420050 and texte[-1].split("\n")[7] == "EUR14200.50"
    assert k2["stand"] != k["stand"]
    # alter Stand -> 409 (nie den alten Preis ueberweisen)
    e = _fehler(w, w.R.als_ueberwiesen_markieren(tid, w.R.UeberwiesenIn(stand=k["stand"]), dict(w.chef)))
    assert e.status_code == 409 and "neu laden" in e.detail
    # abgeschlossen: weiter der Protokollpreis
    _protokoll(w, tid, "final", neuer_preis=14200.5, stand="s2")
    assert _karte(w, tid)["betrag_cents"] == 1420050


def test_12_abholung_ohne_protokoll(welt):
    w = welt
    tid, _ = _vorgang(w, "ohne")
    _konto(w, tid)
    assert _karte(w, tid)["betrag_fest"] is False
    w.run(w.db.appointments.update_one({"id": tid}, {"$set": {"status": "abgeholt", "final_price": 13900}}))
    assert _karte(w, tid)["betrag_cents"] == 1390000
    w.run(w.db.appointments.update_one({"id": tid}, {"$unset": {"final_price": ""}}))
    assert _karte(w, tid)["betrag_cents"] == 1500000, "ohne Vor-Ort-Preis: Vertragspreis"


# =================================================================== Markieren
def test_20_markieren_atomar_409_und_fahrer_sieht_es(welt):
    w = welt
    tid, cid = _vorgang(w, "mark")
    _konto(w, tid)
    _protokoll(w, tid, "freigegeben", neuer_preis=14000)
    k = _karte(w, tid)
    out = _markieren(w, tid, k["stand"])
    assert out["ueberwiesen"]["betrag_cents"] == 1400000
    assert out["ueberwiesen"]["betrag_text"] == "14.000,00 €"
    assert out["ueberwiesen"]["von_name"] == "Carla Chef"
    doc = w.run(w.db.ueberweisungen.find_one({"appointment_id": tid}))
    ue = doc["ueberwiesen"]
    assert ue["von"] == w.chef["id"] and ue["contract_id"] == cid and ue["contract_version"] == 2
    assert ue["protokoll_id"] == f"p_{tid}" and ue["quelle"] == "protokoll" and ue["am"]
    e = _fehler(w, w.R.als_ueberwiesen_markieren(tid, w.R.UeberwiesenIn(stand=k["stand"]), dict(w.chef)))
    assert e.status_code == 409
    # gleichzeitig: genau einer gewinnt
    w.run(w.db.ueberweisungen.update_one({"appointment_id": tid}, {"$set": {"ueberwiesen": None}}))

    async def _zwei():
        return await asyncio.gather(
            w.R.als_ueberwiesen_markieren(tid, w.R.UeberwiesenIn(stand=k["stand"]), dict(w.chef)),
            w.R.als_ueberwiesen_markieren(tid, w.R.UeberwiesenIn(stand=k["stand"]), dict(w.sucher)),
            return_exceptions=True)
    erg = w.run(_zwei())
    assert sum(1 for x in erg if isinstance(x, dict)) == 1
    assert sum(1 for x in erg if isinstance(x, HTTPException) and x.status_code == 409) == 1
    # Fahrer sieht den Stand
    f = w.run(w.R.fahrer_ueberweisung(tid, dict(w.fahrer)))
    assert f["ueberwiesen"]["betrag_text"] == "14.000,00 €" and f["ueberwiesen"]["am"]
    assert "iban" not in f, "Fahrer bekommt nur die formatierte Anzeige"
    appt = w.run(w.db.appointments.find_one({"id": tid}, {"_id": 0}))
    assert w.run(w.U.fahrer_sicht_sicher(appt)) == f, "gleicher Stand in der Protokoll-Antwort"
    # Kontodaten jetzt nicht mehr aenderbar
    e = _fehler(w, w.R.fahrer_kontodaten(tid, w.R.KontodatenIn(iban=IBAN_DE, kontoinhaber="X Y"), dict(w.fahrer)))
    assert e.status_code == 409
    # Preis aendert sich danach -> Warnung in der Karte
    _protokoll(w, tid, "freigegeben", neuer_preis=13500, stand="s9")
    arten = [x["art"] for x in _karte(w, tid)["warnungen"]]
    assert "betrag_geaendert" in arten


def test_21_zuruecknehmen_nur_hauptchef(welt):
    w = welt
    tid, _ = _vorgang(w, "zur")
    _konto(w, tid)
    _protokoll(w, tid, "freigegeben")
    _markieren(w, tid, _karte(w, tid)["stand"], user=w.sucher)
    assert _karte(w, tid, w.sucher)["darf_zuruecknehmen"] is False
    assert _karte(w, tid)["darf_zuruecknehmen"] is True
    e = _fehler(w, w.R.ueberwiesen_zuruecknehmen(tid, dict(w.sucher)))
    assert e.status_code == 403
    out = w.run(w.R.ueberwiesen_zuruecknehmen(tid, dict(w.chef)))
    assert out["ueberwiesen"] is None
    e = _fehler(w, w.R.ueberwiesen_zuruecknehmen(tid, dict(w.chef)))
    assert e.status_code == 409
    doc = w.run(w.db.ueberweisungen.find_one({"appointment_id": tid}))
    assert [v["art"] for v in doc["verlauf"]] == ["markiert", "zurueckgenommen"]
    # danach darf der Fahrer wieder aendern
    assert _konto(w, tid, inhaber="Jürgen Müller")["kontoinhaber"] == "Jürgen Müller"


# =================================================================== Rechte
def test_30_fremde_firma_und_fremder_sucher_404(welt):
    w = welt
    tid, _ = _vorgang(w, "recht")
    _konto(w, tid)
    for wer in (w.fremd, w.sucher2):
        for coro in (w.R.buero_ueberweisung(tid, dict(wer)),
                     w.R.als_ueberwiesen_markieren(tid, w.R.UeberwiesenIn(stand="x" * 24), dict(wer)),
                     w.R.ueberwiesen_zuruecknehmen(tid, dict(wer))):
            assert _fehler(w, coro).status_code == 404
    assert _karte(w, tid, w.sucher)["kontodaten"] is True, "eigener Vorgang des Suchers"
    # Fahrer ohne Verknuepfung / fremder Fahrer: 404
    fremder = {"id": f"fa_fremd_{w.s}", "active": True}
    w.run(w.db.driver_accounts.insert_one(dict(fremder)))
    assert _fehler(w, w.R.fahrer_ueberweisung(tid, dict(fremder))).status_code == 404


def test_31_fahrer_darf_nicht_markieren_routen_und_rechte():
    """Buero-Routen haengen an current_firma (nur Chef/Sucher), Fahrer-Routen an
    current_driver; der Server berechnet den Betrag, der Client schickt nur den Stand."""
    import inspect
    R = _module("routes.ueberweisung")
    deps = _module("deps")
    drivers = _module("routes.drivers")
    for f in (R.buero_ueberweisung, R.als_ueberwiesen_markieren, R.ueberwiesen_zuruecknehmen):
        assert inspect.signature(f).parameters["user"].default.dependency is deps.current_firma
    for f in (R.fahrer_ueberweisung, R.fahrer_kontodaten):
        assert inspect.signature(f).parameters["driver"].default.dependency is drivers.current_driver
    assert set(R.UeberwiesenIn.model_fields) == {"stand"}, "kein Betrag vom Frontend"
    # Fahrer-Konto (role driver_account) kommt durch current_firma nie durch
    with pytest.raises(HTTPException) as e:
        asyncio.new_event_loop().run_until_complete(
            deps.current_firma({"id": "fa", "role": "driver_account", "dealer_id": "d"}))
    assert e.value.status_code == 403
    pfade = {(r.path, tuple(sorted(r.methods))) for r in R.router.routes}
    assert ("/appointments/{appt_id}/ueberweisung/ueberwiesen", ("POST",)) in pfade
    assert not any(p.startswith("/driver/") and "ueberwiesen" in p for p, _m in pfade)


def test_32_fahrer_erfassen_pruefungen(welt):
    w = welt
    tid, _ = _vorgang(w, "bar", zahlung="Bar")
    e = _fehler(w, w.R.fahrer_kontodaten(tid, w.R.KontodatenIn(iban=IBAN_DE, kontoinhaber="Max M"), dict(w.fahrer)))
    assert e.status_code == 409 and "bar" in e.detail
    assert w.run(w.R.fahrer_ueberweisung(tid, dict(w.fahrer)))["zahlungsart"]["ueberweisung"] is False
    tid2, _ = _vorgang(w, "ung")
    e = _fehler(w, w.R.fahrer_kontodaten(tid2, w.R.KontodatenIn(iban="DE00 1234", kontoinhaber="Max M"),
                                         dict(w.fahrer)))
    assert e.status_code == 422
    # Fahrt noch nicht angenommen -> kein Zugriff
    w.run(w.db.appointments.update_one({"id": tid2}, {"$set": {"zuteilung": "offen"}}))
    assert _fehler(w, w.R.fahrer_ueberweisung(tid2, dict(w.fahrer))).status_code == 409


def test_33_warnungen_name_und_ausland(welt):
    w = welt
    tid, _ = _vorgang(w, "warn")
    _konto(w, tid, iban="AT61 1904 3002 3457 3201", inhaber="Erika Musterfrau")
    arten = {x["art"]: x["text"] for x in _karte(w, tid)["warnungen"]}
    assert "name" in arten and "Jürgen Müller" in arten["name"]
    assert "ausland" in arten and "Österreich" in arten["ausland"]


# =================================================================== Meldungen
def test_40_meldungen_je_einmal(welt):
    w = welt
    tid, _ = _vorgang(w, "meld")
    _protokoll(w, tid, "entwurf")
    _konto(w, tid)
    _konto(w, tid, inhaber="Jürgen Müller")            # zweites Speichern: keine neue Meldung
    _karte(w, tid)
    m = w.run(w.db.meldungen.find({"termin_id": tid}, {"_id": 0}).to_list(10))
    assert [x["typ"] for x in m] == ["ueberweisung_kontodaten"]
    assert "Kontodaten liegen vor" in m[0]["text"] and "Betrag noch offen" in m[0]["text"]
    assert sorted(m[0]["empfaenger"]) == sorted([w.chef["id"], w.sucher["id"]])
    assert IBAN_DE not in m[0]["text"] and "DE89" not in m[0]["text"]
    _protokoll(w, tid, "freigegeben", neuer_preis=14999)
    w.run(w.U.meldungen_pruefen_sicher(tid, w.dealer_id))
    w.run(w.U.meldungen_pruefen_sicher(tid, w.dealer_id))
    _karte(w, tid)
    m = w.run(w.db.meldungen.find({"termin_id": tid}, {"_id": 0}).sort("erstellt_am", 1).to_list(10))
    assert [x["typ"] for x in m] == ["ueberweisung_kontodaten", "ueberweisung_betrag"]
    assert m[1]["text"] == "Betrag steht fest – jetzt überweisen: VW Golf, 14.999,00 €."
    # Meldungs-Sicht liefert den Termin fuer "Abholung oeffnen"
    K = _module("routes.kundenportal")
    assert K._meldung_sicht(m[1], w.chef["id"])["termin_id"] == tid


def test_41_kontodaten_wenn_betrag_schon_fest_nur_eine_meldung(welt):
    w = welt
    tid, _ = _vorgang(w, "meld2")
    _protokoll(w, tid, "freigegeben")
    _konto(w, tid)
    w.run(w.U.meldungen_pruefen_sicher(tid, w.dealer_id))
    m = w.run(w.db.meldungen.find({"termin_id": tid}, {"_id": 0}).to_list(10))
    assert [x["typ"] for x in m] == ["ueberweisung_kontodaten"]
    assert m[0]["text"] == "Kontodaten liegen vor – jetzt überweisen: VW Golf, 15.000,00 €."


# =================================================================== Loeschung + Logs
def test_50_loeschung_nach_frist_und_mit_vertrag(welt):
    w = welt
    tid, cid = _vorgang(w, "alt")
    tid2, _ = _vorgang(w, "neu")
    _konto(w, tid)
    _konto(w, tid2)
    alt = (datetime.now(timezone.utc) - timedelta(days=61)).isoformat()
    w.run(w.db.ueberweisungen.update_one({"appointment_id": tid}, {"$set": {"angelegt_am": alt}}))
    jetzt = datetime.now(timezone.utc)
    assert w.run(w.U.ueberweisungen_nach_frist_loeschen(w.db, jetzt, 60)) == 1
    assert w.run(w.db.ueberweisungen.count_documents({"appointment_id": tid})) == 0
    assert w.run(w.db.ueberweisungen.count_documents({"appointment_id": tid2})) == 1
    # Termin weg -> Eintrag weg
    w.run(w.db.appointments.delete_one({"id": tid2}))
    assert w.run(w.U.ueberweisungen_nach_frist_loeschen(w.db, jetzt, 60)) == 1
    # mit dem Vertrag (Vertragsloeschung nach Frist)
    tid3, cid3 = _vorgang(w, "vertrag")
    _konto(w, tid3)
    C = _module("cleanup_service")
    w.run(C.vertrag_endgueltig_loeschen(w.db, cid3, scrub_pii=True, grund="test", audit=False))
    assert w.run(w.db.ueberweisungen.count_documents({})) == 0


def test_51_verdrahtung_loeschung_und_firmenloeschung():
    C = (WURZEL / "backend" / "cleanup_service.py").read_text("utf-8")
    assert "await ueberweisungen_entfernen(db, termin_ids, contract_id)" in C
    assert "await ueberweisungen_entfernen(db, [a[\"id\"]])" in C
    assert 'await s("ueberweisungen_frist"' in C
    A = _module("routes.admin")
    assert "ueberweisungen" in A._COMPANY_COLLECTIONS
    reset = (WURZEL / "backend" / "scripts" / "live_reset.py").read_text("utf-8")
    assert '"ueberweisungen"' in reset
    server = (WURZEL / "backend" / "server.py").read_text("utf-8")
    assert "api.include_router(ueberweisung_routes.router)" in server
    proto = (WURZEL / "backend" / "routes" / "protocols.py").read_text("utf-8")
    assert '"ueberweisung": await _ueberweisung.fahrer_sicht_sicher(appt)' in proto
    assert "await meldungen_pruefen_sicher(doc.get(\"appointment_id\"), user[\"dealer_id\"])" in proto
    assert "segno==1.6.6" in (WURZEL / "backend" / "requirements.txt").read_text("utf-8")


def test_52_keine_iban_in_logs_und_fremden_sammlungen(welt):
    w = welt
    tid, _ = _vorgang(w, "log")
    _konto(w, tid)
    _protokoll(w, tid, "freigegeben")
    _markieren(w, tid, _karte(w, tid)["stand"])
    w.run(w.R.ueberwiesen_zuruecknehmen(tid, dict(w.chef)))
    for name in w.run(w.db.list_collection_names()):
        if name == "ueberweisungen":
            continue
        for d in w.run(getattr(w.db, name).find({}, {"_id": 0}).to_list(1000)):
            assert "0532013000" not in str(d), (name, d)
    logs = w.run(w.db.activity_logs.find({}, {"_id": 0}).to_list(100))
    assert {x["action"] for x in logs} >= {"ueberweisung.kontodaten", "ueberweisung.markiert",
                                          "ueberweisung.zurueckgenommen"}
    # Quelltext: Marktplatz/Vergleich/Lesungen lesen die Sammlung nie
    for datei in ("routes/marketplace.py", "routes/resale.py", "routes/listings.py", "routes/werkzeuge.py",
                  "browser_helfer.py"):
        assert "ueberweisung" not in (WURZEL / "backend" / datei).read_text("utf-8").lower(), datei


# =================================================================== HTTP (optional)
HTTP = os.environ.get("UEBERWEISUNG_HTTP")


@pytest.mark.skipif(not HTTP, reason="nur mit UEBERWEISUNG_HTTP=<Basis-URL> (eigenes Test-Backend)")
def test_90_http_fahrer_token_kann_nicht_markieren():
    import httpx
    from pymongo import MongoClient
    from auth import create_token
    from routes.drivers import create_driver_token
    dbn = os.environ["DB_NAME"]
    m = MongoClient(MONGO_URL)[dbn]
    s = uuid.uuid4().hex[:8]
    did, uid, fid, tid = f"d_h_{s}", f"u_h_{s}", f"f_h_{s}", f"t_h_{s}"
    m.users.insert_one({"id": uid, "dealer_id": did, "role": "dealer", "active": True,
                        "current_session_id": "sess", "created_at": _jetzt()})
    m.dealers.insert_one({"id": did, "user_id": uid, "company_name": "HTTP GmbH"})
    m.driver_accounts.insert_one({"id": fid, "active": True, "current_session_id": "fsess"})
    m.dealer_drivers.insert_one({"dealer_id": did, "driver_account_id": fid})
    m.appointments.insert_one({"id": tid, "dealer_id": did, "driver_id": fid, "zuteilung": "angenommen",
                               "status": "offen", "created_by": uid, "created_at": _jetzt()})
    try:
        with httpx.Client(base_url=HTTP, trust_env=False, timeout=20) as c:
            ft = {"Authorization": f"Bearer {create_driver_token(fid, 'fsess')}"}
            ct = {"Authorization": f"Bearer {create_token(uid, 'sess')}"}
            r = c.post(f"/api/appointments/{tid}/ueberweisung/ueberwiesen", json={"stand": "x" * 24}, headers=ft)
            assert r.status_code in (401, 403), r.text
            r = c.put(f"/api/driver/appointments/{tid}/ueberweisung",
                      json={"iban": IBAN_DE, "kontoinhaber": "Max Muster"}, headers=ft)
            assert r.status_code == 200, r.text
            assert r.json()["iban_formatiert"] == "DE89 3704 0044 0532 0130 00"
            r = c.get(f"/api/appointments/{tid}/ueberweisung", headers=ct)
            assert r.status_code == 200 and r.json()["kontodaten"] is True
            assert r.json()["betrag_fest"] is False
            r = c.get(f"/api/driver/appointments/{tid}/ueberweisung", headers=ct)
            assert r.status_code in (401, 403)
    finally:
        for coll, f in (("users", {"id": uid}), ("dealers", {"id": did}), ("driver_accounts", {"id": fid}),
                        ("dealer_drivers", {"dealer_id": did}), ("appointments", {"id": tid}),
                        ("ueberweisungen", {"appointment_id": tid}), ("meldungen", {"dealer_id": did}),
                        ("activity_logs", {"dealer_id": did})):
            m[coll].delete_many(f)
