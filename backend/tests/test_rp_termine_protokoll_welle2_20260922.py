# -*- coding: utf-8 -*-
"""Rollenprüfung 22.09.2026 — Team termine_protokoll, Welle 2 (Übergaben
anderer Teams in Termin-/Protokoll-Dateien).

In-Prozess gegen DB_NAME (Welt aus test_golive_20260913_abschluss wie in
test_rp_termine_protokoll_20260922.py). Der PDF-Test baut ein ECHTES PDF.

  RP-518          Rueckweg Verkaufsblock -> Kaufzustand (nur ausdruecklich,
                  nie per Hook), Inserat vor der Abholung geloescht
  RP-057 b        firmenweiter Einkaufspreis bei Doppel-Vertraegen: mehrdeutig
  RP-479/480      Korrektur ohne Angaben fragt die Neuerzeugung, wenn der
                  Vertrag schon einen Stand vor der Abholung festhaelt
  RP-073/172      Alarm vertrag_nach_abholung_offen schliesst bei Erfolg
  RP-146          Freigabe-Vermerk laesst sich leeren
  RP-080/179 (2)  deaktiviertes Fahrerkonto: Nachpruefung nimmt Zuweisung zurueck
  RP-080/179 (4)  neuer Fahrer-Vorschlag ersetzt einen frueheren Fahrer-Preis
  RP-175          Handabschluss schliesst den Alarm protokoll_final_termin_geschlossen
  RP-542          Telefon des Fahrers nur fuer den Hauptchef
  RP-067/166      Abschnitt 5 druckt die Antwort des Fahrers
  RP-404          "" bei der Halterzahl heisst "im Vertrag leer"
"""
import io
import sys
import uuid
from pathlib import Path

from fastapi import Response

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_golive_20260913_abschluss import (  # noqa: E402,F401  (welt = Fixture)
    _abholung, _doc, _jetzt, _m, welt)
from test_rp_termine_protokoll_20260922 import _ausgefuellt, _final, _put  # noqa: E402

# Routenmodule VOR der ersten Welt importieren (kein Binden an deren Loop).
from routes import marketplace as _marketplace  # noqa: E402,F401
from routes import resale as R  # noqa: E402


def _alarm(w, typ, ref, **details):
    w.run(w.db.betriebsalarme.insert_one({
        "id": uuid.uuid4().hex, "typ": typ, "ref": ref, "offen": True, "anzahl": 1,
        "details": details, "created_at": _jetzt()}))


def _alarm_offen(w, typ, ref):
    return bool(w.run(w.db.betriebsalarme.count_documents({"typ": typ, "ref": ref, "offen": True})))


# ======================================================================= RP-518
def test_rp518_rueckweg_nur_ausdruecklich(welt):
    w = welt
    LC = _m("lifecycle")
    t = _abholung(w)
    for zustand in ("verkaufsentwurf", "verkaufsbereit"):
        assert {"gekauft", "abholung_geplant"} <= LC.ALLOWED_TRANSITIONS[zustand]
        w.run(w.db.vehicles.update_one({"id": t.vid}, {"$set": {"lifecycle": zustand}}))
        # Hook (Termin ohne Vertrag, Vertrag, Aufraeumer): zieht nichts aus dem Verkauf
        assert w.run(LC.try_set_lifecycle(t.vid, w.dealer_id, "abholung_geplant")) is False
        assert _doc(w, "vehicles", t.vid)["lifecycle"] == zustand
    # ausdruecklich (Loeschen des Inserats): erlaubt
    w.run(LC.set_lifecycle(t.vid, w.dealer_id, "abholung_geplant"))
    assert _doc(w, "vehicles", t.vid)["lifecycle"] == "abholung_geplant"
    # Weg aus "reserviert" nur ueber Verkaufszustaende
    assert R._pfad_zurueck("reserviert", "abholung_geplant") == \
        ["veroeffentlicht", "verkaufsbereit", "abholung_geplant"]
    assert R._pfad_zurueck("veroeffentlicht", "gekauft") == ["verkaufsbereit", "gekauft"]
    # "abgeholt" bleibt ohne Rueckweg (V-12 nur ueber abholung_zuruecknehmen)
    assert LC.ALLOWED_TRANSITIONS["abgeholt"] == {"bestand", "verkaufsentwurf", "geloescht"}


def test_rp518_inserat_vor_abholung_geloescht(welt, monkeypatch):
    w = welt
    monkeypatch.setattr(R, "db", w.db)
    t = _abholung(w, proto_status="entwurf")
    lid = f"l518_{w.s}"
    w.run(w.db.vehicles.update_one({"id": t.vid}, {"$set": {"lifecycle": "verkaufsbereit"}}))
    w.run(w.db.resale_listings.insert_one({
        "id": lid, "dealer_id": w.dealer_id, "vehicle_id": t.vid, "status": "verkaufsbereit",
        "title": "BMW", "counted_periods": [], "created_at": _jetzt(), "updated_at": _jetzt()}))
    try:
        w.run(R.delete_listing(lid, w.chef))
        assert _doc(w, "resale_listings", lid)["status"] == "geloescht"
        assert _doc(w, "vehicles", t.vid)["lifecycle"] == "abholung_geplant", \
            "vorher: bestand (danach aenderte 'nicht abgeholt' nichts mehr)"
        # "nicht abgeholt" wirkt jetzt wieder auf das Fahrzeug (kein offener Vorgang mehr)
        w.run(w.db.kaufvorgaenge.update_one({"id": t.kb}, {"$set": {"status": "storniert"}}))
        _put(w, t.aid, status="nicht abgeholt")
        assert _doc(w, "vehicles", t.vid)["lifecycle"] == "nicht_abgeholt"
    finally:
        w.run(w.db.resale_listings.delete_many({"dealer_id": w.dealer_id}))


# ======================================================================= RP-057 b
def test_rp057b_doppelvertraege_verschiedener_konten_sind_mehrdeutig(welt):
    w = welt
    KV = _m("kaufvorgang")
    t = _abholung(w)
    # beide Vorgaenge vom selben Konto: juengster gilt wie bisher
    w.run(w.db.kaufvorgaenge.update_one({"id": t.kb}, {"$set": {"updated_at": "2099-01-01T00:00:00+00:00"}}))
    assert w.run(KV.einkaufspreis_vorschlag(t.vid, w.dealer_id)) == \
        {"preis": 20000.0, "quelle": "vertrag", "kaufvorgang_id": t.kb}
    # zweites Konto mit anderem Preis: kein Zufallspreis mehr
    w.run(w.db.kaufvorgaenge.update_one({"id": t.kb}, {"$set": {"user_id": f"sucher_{w.s}"}}))
    assert w.run(KV.einkaufspreis_vorschlag(t.vid, w.dealer_id)) == \
        {"preis": None, "quelle": "mehrdeutig", "kaufvorgang_id": None}
    # der Sucher selbst sieht weiter seinen eigenen Vertragspreis
    assert w.run(KV.einkaufspreis_vorschlag(t.vid, w.dealer_id, user_id=f"sucher_{w.s}")) == \
        {"preis": 20000.0, "quelle": "vertrag", "kaufvorgang_id": t.kb}
    # gleicher Preis bei beiden Konten: eindeutig
    w.run(w.db.kaufvorgaenge.update_one({"id": t.kb}, {"$set": {"purchase_price": 10000.0}}))
    assert w.run(KV.einkaufspreis_vorschlag(t.vid, w.dealer_id))["quelle"] == "vertrag"
    # abgeholt entscheidet
    w.run(w.db.kaufvorgaenge.update_one({"id": t.kb}, {"$set": {"purchase_price": 20000.0}}))
    w.run(w.db.kaufvorgaenge.update_one({"id": t.ka}, {"$set": {"status": "abgeholt"}}))
    assert w.run(KV.einkaufspreis_vorschlag(t.vid, w.dealer_id)) == \
        {"preis": 10000.0, "quelle": "abgeholt", "kaufvorgang_id": t.ka}


# ======================================================================= RP-479 / RP-073
def _regen_aufzeichnen(monkeypatch, ergebnis_grund=None, ok=True):
    C = _m("routes.contracts")
    aufrufe = []

    async def _regen(**k):
        aufrufe.append(k)
        if ergebnis_grund and isinstance(k.get("ergebnis"), dict):
            k["ergebnis"]["grund"] = ergebnis_grund
        return ok
    monkeypatch.setattr(C, "regenerate_contract_for_pickup", _regen)
    return aufrufe


def test_rp479_korrektur_ohne_angaben_nimmt_stand_vor_abholung(welt, monkeypatch):
    w = welt
    P = _m("routes.protocols")
    t = _abholung(w)
    termin = _doc(w, "appointments", t.aid)
    aufrufe = _regen_aufzeichnen(monkeypatch, ergebnis_grund="kein_anlass", ok=False)
    # nie eine Fassung nach Abholung: ohne Angaben nichts zu tun
    assert w.run(P.vertrag_nach_abholung_aktualisieren(termin, t.pid, None, "")) is False
    assert aufrufe == []
    # Vertrag haelt den Stand vor der Abholung fest (contracts.py RP-479):
    # die Neuerzeugung wird gefragt, "kein Anlass" ist harmlos (kein Alarm)
    w.run(w.db.generated_pdfs.update_one(
        {"id": t.ca}, {"$set": {"vertrag_vor_abholung": {"purchase_price": 10000.0}}}))
    assert w.run(P.vertrag_nach_abholung_aktualisieren(termin, t.pid, None, "")) is True
    assert len(aufrufe) == 1 and aufrufe[0]["protokoll_id"] == t.pid
    assert aufrufe[0]["neuer_preis"] is None
    assert not _alarm_offen(w, "vertrag_nach_abholung_offen", t.ca)


def test_rp073_alarm_schliesst_bei_erfolg_nicht_fuer_abgeloeste_version(welt, monkeypatch):
    w = welt
    P = _m("routes.protocols")
    t = _abholung(w)
    termin = _doc(w, "appointments", t.aid)
    _regen_aufzeichnen(monkeypatch, ok=True)
    # abgeloeste Version: Erfolg sagt nichts ueber die aktuelle Fassung
    _alarm(w, "vertrag_nach_abholung_offen", t.ca, protokoll_id=t.pid)
    w.run(w.db.pickup_protocols.update_one({"id": t.pid}, {"$set": {"superseded": True}}))
    assert w.run(P.vertrag_nach_abholung_aktualisieren(termin, t.pid, 9500.0, "")) is True
    assert _alarm_offen(w, "vertrag_nach_abholung_offen", t.ca)
    # aktuelle Version: Neuerzeugung gelungen -> Alarm zu
    w.run(w.db.pickup_protocols.update_one({"id": t.pid}, {"$set": {"superseded": False}}))
    assert w.run(P.vertrag_nach_abholung_aktualisieren(termin, t.pid, 9500.0, "")) is True
    assert not _alarm_offen(w, "vertrag_nach_abholung_offen", t.ca)
    # idempotenter Ausgang (Vertrag traegt diese Version schon)
    _alarm(w, "vertrag_nach_abholung_offen", t.ca, protokoll_id="p_alt")
    w.run(w.db.generated_pdfs.update_one({"id": t.ca}, {"$set": {"nach_abholung_protokoll_id": t.pid}}))
    assert w.run(P.vertrag_nach_abholung_aktualisieren(termin, t.pid, 9500.0, "")) is True
    assert not _alarm_offen(w, "vertrag_nach_abholung_offen", t.ca)
    # "keine Aenderung" ist ebenfalls erledigt
    _alarm(w, "vertrag_nach_abholung_offen", t.ca, protokoll_id="p_alt")
    w.run(w.db.generated_pdfs.update_one({"id": t.ca}, {"$unset": {"nach_abholung_protokoll_id": ""}}))
    _regen_aufzeichnen(monkeypatch, ergebnis_grund="keine_aenderung", ok=False)
    assert w.run(P.vertrag_nach_abholung_aktualisieren(termin, t.pid, 9500.0, "")) is True
    assert not _alarm_offen(w, "vertrag_nach_abholung_offen", t.ca)


# ======================================================================= RP-146 / RP-080 (4)
def _freigeben(w, pid, **felder):
    P = _m("routes.protocols")
    stand = _doc(w, "pickup_protocols", pid).get("freigabe_stand")
    return w.run(P.protokoll_freigeben(pid, P.FreigabeIn(stand=stand, **felder), w.chef))


def test_rp146_vermerk_laesst_sich_leeren(welt):
    w = welt
    t = _abholung(w)
    _freigeben(w, t.pid, notiz="  Rost am Schweller ")
    assert _doc(w, "pickup_protocols", t.pid)["preis_notiz"] == "Rost am Schweller"
    _freigeben(w, t.pid)                       # ohne notiz: Vermerk bleibt
    assert _doc(w, "pickup_protocols", t.pid)["preis_notiz"] == "Rost am Schweller"
    _freigeben(w, t.pid, notiz="   ")           # geleert: Vermerk weg
    assert "preis_notiz" not in _doc(w, "pickup_protocols", t.pid)
    assert _doc(w, "pickup_protocols", t.pid)["neuer_preis"] == 9000.0, "Preis unberuehrt"


def test_rp080_neuer_fahrer_vorschlag_ersetzt_frueheren_fahrer_preis(welt):
    w = welt
    t = _abholung(w, proto_status="zur_freigabe")
    w.run(w.db.pickup_protocols.update_one({"id": t.pid}, {"$set": {
        "neuer_preis": 9000.0, "preis_quelle": "fahrer", "preis_vorschlag": 9500.0}}))
    r = _freigeben(w, t.pid)
    doc = _doc(w, "pickup_protocols", t.pid)
    assert r["neuer_preis"] == 9500.0 and doc["neuer_preis"] == 9500.0, "vorher: klebte bei 9000"
    assert doc["preis_quelle"] == "fahrer"
    # Preis des Chefs bleibt, auch bei anderem Fahrer-Vorschlag
    w.run(w.db.pickup_protocols.update_one({"id": t.pid}, {"$set": {
        "neuer_preis": 8800.0, "preis_quelle": "chef", "preis_vorschlag": 9700.0}}))
    _freigeben(w, t.pid)
    assert _doc(w, "pickup_protocols", t.pid)["neuer_preis"] == 8800.0
    # verworfener Vorschlag kommt nicht zurueck (RP-453)
    w.run(w.db.pickup_protocols.update_one({"id": t.pid}, {"$set": {
        "neuer_preis": 9000.0, "preis_quelle": "fahrer", "preis_vorschlag": 9900.0,
        "preis_vorschlag_verworfen": True}}))
    _freigeben(w, t.pid)
    assert _doc(w, "pickup_protocols", t.pid)["neuer_preis"] == 9000.0


def test_rp080_zurueck_loescht_weiter_nichts(welt):
    w = welt
    t = _abholung(w, proto_status="zur_freigabe")
    w.run(w.db.pickup_protocols.update_one({"id": t.pid}, {"$set": {
        "neuer_preis": 9000.0, "preis_quelle": "fahrer", "preis_notiz": "Vermerk"}}))
    _freigeben(w, t.pid, zurueck=True, notiz="bitte km nachtragen")
    doc = _doc(w, "pickup_protocols", t.pid)
    assert doc["status"] == "entwurf" and doc["neuer_preis"] == 9000.0 and doc["preis_notiz"] == "Vermerk"


def test_rp080_liste_liefert_preisquelle(welt):
    w = welt
    P = _m("routes.protocols")
    t = _abholung(w, proto_status="zur_freigabe")
    w.run(w.db.pickup_protocols.update_one({"id": t.pid}, {"$set": {"preis_quelle": "fahrer"}}))
    liste = w.run(P.protokolle_zur_freigabe(w.chef))
    eintrag = next(e for e in liste if e["protocol_id"] == t.pid)
    assert eintrag["preis_quelle"] == "fahrer"


# ======================================================================= RP-080 (2)
def test_rp080_nachpruefung_nimmt_deaktivierten_fahrer_zurueck(welt):
    w = welt
    A = _m("routes.appointments")
    t = _abholung(w)
    assert w.run(A._fahrer_nachpruefen(t.aid, w.dealer_id, w.driver["id"])) is True
    w.run(w.db.driver_accounts.update_one({"id": w.driver["id"]}, {"$set": {"active": False}}))
    assert w.run(A._fahrer_nachpruefen(t.aid, w.dealer_id, w.driver["id"])) is False
    termin = _doc(w, "appointments", t.aid)
    assert not termin.get("driver_id") and termin.get("zuteilung") is None
    # in Loeschung: ebenso
    w.run(w.db.appointments.update_one({"id": t.aid}, {"$set": {"driver_id": w.driver2["id"]}}))
    w.run(w.db.driver_accounts.update_one({"id": w.driver2["id"]},
                                          {"$set": {"loeschung": {"status": "laeuft"}}}))
    assert w.run(A._fahrer_nachpruefen(t.aid, w.dealer_id, w.driver2["id"])) is False
    assert "deaktiviert" in A.FAHRER_ENTFERNT_HINWEIS


# ======================================================================= RP-175
def test_rp175_handabschluss_schliesst_alarm(welt):
    w = welt
    t = _abholung(w)
    _final(w, t)
    _alarm(w, "protokoll_final_termin_geschlossen", t.aid, protocol_id=t.pid)
    _put(w, t.aid, driver_id=None)
    assert _alarm_offen(w, "protokoll_final_termin_geschlossen", t.aid), "noch nicht abgeholt"
    assert _put(w, t.aid, status="abgeholt")["ok"]
    assert not _alarm_offen(w, "protokoll_final_termin_geschlossen", t.aid)


# ======================================================================= RP-542
def test_rp542_telefon_nur_fuer_den_hauptchef(welt):
    w = welt
    A = _m("routes.appointments")
    t = _abholung(w)
    w.run(w.db.driver_accounts.update_one({"id": w.driver["id"]}, {"$set": {"phone": "0171 2345678"}}))
    liste = w.run(A.list_appointments(Response(), w.chef))
    eintrag = next(a for a in liste if a["id"] == t.aid)
    assert eintrag["driver"]["phone"] == "0171 2345678"
    einzeln = w.run(A.get_appointment(t.aid, w.chef))
    assert einzeln["driver"]["phone"] == "0171 2345678"
    # Entscheidung Ahmad 22.09.2026 (Welle B1): die Nummer sehen ALLE in der
    # Firma — zweites dealer-Konto und Sucher; Fahrer-ID/E-Mail weiter nur
    # der Hauptchef.
    zweit = {"id": f"zweit_{w.s}", "dealer_id": w.dealer_id, "role": "dealer"}
    liste2 = w.run(A.list_appointments(Response(), zweit))
    eintrag2 = next(a for a in liste2 if a["id"] == t.aid)
    assert eintrag2["driver"]["phone"] == "0171 2345678"
    assert eintrag2["driver"]["driver_code"] is None and eintrag2["driver"]["email"] is None


# ======================================================================= RP-067 (PDF, echt)
def test_rp067_abschnitt5_druckt_antwort():
    PDF = _m("pickup_pdf_service")
    assert PDF.damages_confirmed_zeile({"damages_confirmed": True}).endswith(" Ja")
    assert PDF.damages_confirmed_zeile({"damages_confirmed": False}).endswith(" Nein")
    assert PDF.damages_confirmed_zeile({"notes": "x"}).endswith(" —")
    leer = PDF.damages_confirmed_zeile(None)
    assert "Ja" in leer and "Nein" in leer and "[" in leer
    from pypdf import PdfReader
    filled = {**_ausgefuellt("kurz"), "damages_confirmed": False}
    pdf = PDF.build_pickup_pdf(
        appointment={"id": f"t_{uuid.uuid4().hex[:8]}", "pickup_date": "2026-09-22"},
        vehicle={"make_label": "BMW", "model_label": "320d"},
        contract={"purchase_price": 18000.0}, dealer={"company_name": "GL"},
        driver={"name": "Fahrer"}, filled=filled)
    text = " ".join((s.extract_text() or "") for s in PdfReader(io.BytesIO(pdf)).pages)
    text = " ".join(text.split())
    assert "vom Fahrer bestätigt): Nein" in text, text[:2000]


# ======================================================================= RP-404
def test_rp404_halterzahl_leer_heisst_leer():
    PV = _m("protokoll_vergleich")
    inserat = {"previous_owners": 3}
    assert PV.vertragswerte(inserat, {"purchase_price": 1})["previous_owners"]["text"] == "3"
    assert PV.vertragswerte(inserat, {"previous_owners": None})["previous_owners"]["quelle"] == "inserat"
    leer = PV.vertragswerte(inserat, {"previous_owners": ""})["previous_owners"]
    assert leer["text"] == "" and leer["quelle"] is None, leer
    zwei = PV.vertragswerte(inserat, {"previous_owners": "2"})["previous_owners"]
    assert zwei["text"] == "2" and zwei["quelle"] == "vertrag"
