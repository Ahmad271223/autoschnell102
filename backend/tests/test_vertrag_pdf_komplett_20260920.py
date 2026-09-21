# -*- coding: utf-8 -*-
"""Vollstaendiger Test der Vertrags-PDF-Erstellung (Wunsch Ahmad 20.09.2026).

"teste komplett pdf erstellung und auch bei bestehenden accs und bei neuen
und bei aenderungen usw komplett erst dann pushen, muss live dicht sein."

Geprueft wird am FERTIGEN PDF (Text wird ausgelesen), nicht am Quelltext,
und ueber den echten HTTP-Weg — Anlage, Aenderung, Neuerzeugung:

  A  NEUE Firma          bekommt die Startwerte, PDF ist vollstaendig
  B  BESTEHENDE Firma    ohne die neuen Felder (Altbestand) — darf nicht
                         brechen und bekommt den Standardsatz dazu
  C  AENDERUNGEN         Schalter aus/an, eigener Text, Sucher-Fassung
  D  NEUERZEUGUNG        neues Abholdatum -> das PDF zeigt das NEUE Datum
  E  GRENZFAELLE         fehlende Angaben, Sonderzeichen, sehr langer Text

Alles ueber den echten Weg: Konten in der Datenbank, Vertrag ueber die API,
PDF ueber die API geladen und ausgelesen.
"""
import io
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import konten  # noqa: E402  zentrale Konto-Helfer
import vertrag_vorlagen as V  # noqa: E402

API = konten.API
MONGO_URL = konten.MONGO_URL if hasattr(konten, "MONGO_URL") else None
SUF = uuid.uuid4().hex[:8]
PW = "Rt7Kq2Mx9-Sicher!42"


def _db():
    import os
    from pymongo import MongoClient
    url = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
    return MongoClient(url, serverSelectionTimeoutMS=5000)[
        os.environ.get("DB_NAME") or "autoschnell"]


def _jetzt():
    return datetime.now(timezone.utc).isoformat()


def _pdf_text(roh: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:                       # aeltere Umgebungen
        from PyPDF2 import PdfReader
    return "\n".join(s.extract_text() or ""
                     for s in PdfReader(io.BytesIO(roh)).pages)


# --------------------------------------------------------------- Aufbau
@pytest.fixture(scope="module")
def welt():
    """Eine Firma mit Chef und Sucher, angelegt ueber den echten Weg."""
    firma = konten.registrieren({
        "company_name": f"PDF-Test {SUF}",
        "contact_person": "Chef Test",
        "email": f"chef{SUF}@e2etest-mail.de",
        "phone": "0211 1234567",
        "password": PW,
    })
    assert firma.status_code == 200, firma.text[:300]
    daten = firma.json()
    kopf = {"Authorization": f"Bearer {daten['token']}"}
    dealer_id = daten["user"]["dealer_id"]

    # Der Helfer legt den Sucher ueber den Super-Admin an und meldet ihn
    # NICHT an — die Anmeldung laeuft ueber die Kontonummer aus der Antwort.
    sucher = konten.sucher_als_chef_anlegen(kopf, json={
        "first_name": "Sucher", "last_name": "Test",
        "email": f"such{SUF}@e2etest-mail.de", "password": PW})
    assert sucher.status_code == 200, sucher.text[:300]
    s_daten = sucher.json()
    s_login = konten.anmelden(s_daten["kontonummer"], PW)
    assert s_login.status_code == 200, s_login.text[:300]
    s_kopf = {"Authorization": f"Bearer {s_login.json()['token']}"}
    s_user_id = s_daten["sucher_id"]

    # Ohne aktives Abo antwortet die Vertragsanlage mit 402 — hier geht es
    # um das PDF, nicht um die Abo-Pruefung.
    dbx = _db()
    eintraege = [{"id": str(uuid.uuid4()), "dealer_id": dealer_id,
                  "subject_user_id": daten["user"]["id"], "plan": "yearly",
                  "status": "active", "created_at": _jetzt(),
                  "expires_at": (datetime.now(timezone.utc)
                                 + timedelta(days=90)).isoformat()}]
    eintraege.append({"id": str(uuid.uuid4()), "dealer_id": dealer_id,
                      "subject_user_id": s_user_id,
                      "plan": "monthly", "status": "active",
                      "created_at": _jetzt(),
                      "expires_at": (datetime.now(timezone.utc)
                                     + timedelta(days=30)).isoformat()})
    dbx.subscriptions.insert_many(eintraege)

    w = {"dealer_id": dealer_id, "kopf": kopf, "sucher_kopf": s_kopf,
         "user_id": daten["user"]["id"]}
    yield w

    dbx = _db()
    dbx.users.delete_many({"dealer_id": dealer_id})
    dbx.dealers.delete_many({"id": dealer_id})
    for coll in ("subscriptions", "vehicles", "generated_pdfs",
                 "generated_pdf_versions", "appointments", "kaufvorgaenge",
                 "activity_logs"):
        dbx[coll].delete_many({"dealer_id": dealer_id})


def _fahrzeug(w, nr=0):
    r = requests.post(f"{API}/vehicles/manual", headers=w["kopf"], timeout=60, json={
        "make_label": "BMW", "model_label": "320d",
        "model_description": f"PDF-Test {SUF}-{nr}",
        "first_registration": "03/2019", "mileage": 85000,
        "fuel_label": "Diesel", "gearbox_label": "Automatik",
        "power_kw": 140, "power_ps": 190, "color": "Schwarz",
        "purchase_price": 15000,
    })
    assert r.status_code == 200, r.text[:300]
    return r.json()["id"]


def _vertrag(w, kopf=None, **extra):
    vid = _fahrzeug(w, extra.pop("nr", 0))
    koerper = {
        "vehicle_id": vid, "seller_name": "Max Mustermann",
        "seller_address": "Hauptstr. 5", "seller_zip": "40210",
        "seller_city": "Düsseldorf", "purchase_price": 15900,
        "payment_method": "Echtzeitüberweisung",
        "pickup_date": "2026-10-03", "pickup_time": "14:30",
        "idempotency_key": f"pdf-{uuid.uuid4().hex[:12]}",
    }
    koerper.update(extra)
    r = requests.post(f"{API}/contracts", headers=kopf or w["kopf"],
                      timeout=90, json=koerper)
    assert r.status_code in (200, 201), r.text[:400]
    return r.json()


def _pdf(w, contract_id, kopf=None, digital=True):
    r = requests.get(f"{API}/contracts/{contract_id}/pdf"
                     + ("?variante=digital" if digital else ""),
                     headers=kopf or w["kopf"], timeout=90)
    assert r.status_code == 200, r.text[:300]
    return _pdf_text(r.content)


# ------------------------------------------------------ A: NEUE Firma
def test_a1_neue_firma_bekommt_die_startwerte(welt):
    r = requests.get(f"{API}/dealer/settings", headers=welt["kopf"], timeout=30)
    assert r.status_code == 200, r.text[:300]
    d = r.json()
    assert d.get("email_template"), "E-Mail-Vorlage fehlt bei einer neuen Firma"
    assert "nette Gespräch" in d["email_template"]
    assert d.get("whatsapp_template") and d.get("email_template_nach_kauf")
    assert d.get("email_template_bahn") and d.get("email_template_korrektur")
    # Der Schalter steht auf AN, das Freitextfeld ist leer.
    assert d.get("sondervereinbarung_standard_aktiv") is not False
    assert not (d.get("default_special_agreements") or "").strip()
    # Und die wirksame Fassung enthaelt unseren Standardsatz.
    assert "{abholdatum}" in (d.get("sondervereinbarungen_effektiv") or "")


def test_a2_das_pdf_einer_neuen_firma_ist_vollstaendig(welt):
    v = _vertrag(welt, nr=1)
    text = _pdf(welt, v["id"])
    assert "03.10.2026" in text, "Abholdatum fehlt im PDF"
    assert "Hauptstr. 5, 40210" in text, "Übergabeort fehlt"
    assert "Echtzeitüberweisung" in text, "Zahlungsart fehlt"
    for p in ("{abholdatum}", "{ort}", "{zahlungsart}", "{kundennummer}",
              "{kunde_name}", "{haendler_name}"):
        assert p not in text, f"{p} steht woertlich im Vertrag des Kunden"


def test_a3_kaufpreis_steht_vor_den_fahrzeugdaten(welt):
    v = _vertrag(welt, nr=2)
    text = _pdf(welt, v["id"])
    assert text.index("Kaufpreis") < text.index("Fahrzeugdaten")
    assert text.index("Käufer") < text.index("Kaufpreis")


def test_a4_auch_die_druckfassung_ist_vollstaendig(welt):
    """Nicht nur die digitale Fassung — die Druckfassung mit
    Unterschriftslinien geht denselben Weg."""
    v = _vertrag(welt, nr=3)
    text = _pdf(welt, v["id"], digital=False)
    assert "03.10.2026" in text and "{abholdatum}" not in text


# ------------------------------------------- B: BESTEHENDE Firma (Altbestand)
def test_b1_firma_ohne_die_neuen_felder_bricht_nicht(welt):
    """Altbestand: die Felder gibt es im dealers-Dokument gar nicht."""
    dbx = _db()
    dbx.dealers.update_one(
        {"id": welt["dealer_id"]},
        {"$unset": {"sondervereinbarung_standard_aktiv": "",
                    "default_special_agreements": "",
                    "email_template_nach_kauf": "",
                    "email_template_bahn": ""}})
    try:
        v = _vertrag(welt, nr=4)
        text = _pdf(welt, v["id"])
        # Fehlt der Schalter, gilt AN — der Standardsatz ist also da.
        assert "Fahrzeugübergabe findet" in text, (
            "Altbestand bekommt den Standardsatz nicht")
        assert "03.10.2026" in text and "{abholdatum}" not in text
        # Und die Folge-Mail-Vorlage faellt auf den Standard zurueck.
        r = requests.get(f"{API}/contracts/{v['id']}/folge-mail/bahn",
                         headers=welt["kopf"], timeout=30)
        assert r.status_code == 200, r.text[:300]
        assert "Bahnverbindung" in r.json()["text"]
        assert "{" not in r.json()["text"], r.json()["text"]
    finally:
        dbx.dealers.update_one({"id": welt["dealer_id"]},
                               {"$set": {"default_special_agreements": ""}})


def test_b2_altbestand_mit_eigenem_text_behaelt_ihn(welt):
    """Eine bestehende Firma hat ihren eigenen Satz im Freitextfeld — der
    darf NICHT verschwinden, er kommt unter unseren."""
    eigen = f"• Eigene Altregel {SUF}."
    dbx = _db()
    dbx.dealers.update_one({"id": welt["dealer_id"]},
                           {"$set": {"default_special_agreements": eigen},
                            "$unset": {"sondervereinbarung_standard_aktiv": ""}})
    try:
        v = _vertrag(welt, nr=5)
        text = _pdf(welt, v["id"])
        assert eigen.strip("• ") in text, "der eigene Text ging verloren"
        assert "Fahrzeugübergabe findet" in text, "unser Satz fehlt"
        assert text.index("Fahrzeugübergabe") < text.index(f"Eigene Altregel {SUF}")
    finally:
        dbx.dealers.update_one({"id": welt["dealer_id"]},
                               {"$set": {"default_special_agreements": ""}})


# -------------------------------------------------------- C: AENDERUNGEN
def test_c1_schalter_aus_entfernt_nur_unseren_satz(welt):
    eigen = f"• Nur meine Regel {SUF}."
    r = requests.put(f"{API}/dealer/settings", headers=welt["kopf"], timeout=30,
                     json={"sondervereinbarung_standard_aktiv": False,
                           "default_special_agreements": eigen})
    assert r.status_code == 200, r.text[:300]
    try:
        v = _vertrag(welt, nr=6)
        text = _pdf(welt, v["id"])
        assert f"Nur meine Regel {SUF}" in text
        assert "Fahrzeugübergabe findet" not in text, (
            "der abgeschaltete Standardsatz steht trotzdem im Vertrag")
    finally:
        requests.put(f"{API}/dealer/settings", headers=welt["kopf"], timeout=30,
                     json={"sondervereinbarung_standard_aktiv": True,
                           "default_special_agreements": ""})


def test_c2_schalter_wieder_an(welt):
    v = _vertrag(welt, nr=7)
    text = _pdf(welt, v["id"])
    assert "Fahrzeugübergabe findet" in text and "03.10.2026" in text


def test_c3_eigene_platzhalter_im_freitext_werden_auch_gefuellt(welt):
    eigen = "• Übergabe an {kunde_name} zum Preis von {kaufpreis}."
    requests.put(f"{API}/dealer/settings", headers=welt["kopf"], timeout=30,
                 json={"default_special_agreements": eigen})
    try:
        v = _vertrag(welt, nr=8)
        text = _pdf(welt, v["id"])
        assert "Max Mustermann" in text
        assert "15.900,00" in text.replace(" ", "").replace("\xa0", ""), text[:600]
        assert "{kunde_name}" not in text and "{kaufpreis}" not in text
    finally:
        requests.put(f"{API}/dealer/settings", headers=welt["kopf"], timeout=30,
                     json={"default_special_agreements": ""})


def test_c4_der_sucher_hat_seine_eigene_fassung(welt):
    """Wunsch Ahmad: 'für sein acc'. Die Fassung des Suchers gilt fuer
    SEINE Vertraege, die des Chefs bleibt unberuehrt."""
    eigen = f"• Nur der Sucher {SUF}."
    r = requests.put(f"{API}/dealer/settings", headers=welt["sucher_kopf"],
                     timeout=30, json={"default_special_agreements": eigen})
    assert r.status_code == 200, r.text[:300]
    # Der Chef sieht seine eigene (leere) Fassung weiter.
    chef = requests.get(f"{API}/dealer/settings", headers=welt["kopf"],
                        timeout=30).json()
    assert f"Nur der Sucher {SUF}" not in (
        chef.get("default_special_agreements") or "")
    such = requests.get(f"{API}/dealer/settings", headers=welt["sucher_kopf"],
                        timeout=30).json()
    assert f"Nur der Sucher {SUF}" in (such.get("default_special_agreements") or "")
    assert f"Nur der Sucher {SUF}" in (such.get("sondervereinbarungen_effektiv") or "")


def test_c5_der_vertrag_darf_den_text_ueberschreiben(welt):
    """Im Vertragsdialog laesst sich der Text fuer DIESEN Vertrag aendern."""
    v = _vertrag(welt, nr=9,
                 additional_terms="• Sondervereinbarung nur hier: {zahlungsart}.")
    text = _pdf(welt, v["id"])
    assert "Sondervereinbarung nur hier: Echtzeitüberweisung" in text
    assert "Fahrzeugübergabe findet" not in text, (
        "der Standardsatz wurde trotz eigener Eingabe angehaengt")


# ------------------------------------------------------ D: NEUERZEUGUNG
def test_d1_neues_abholdatum_erscheint_im_neuen_pdf(welt):
    """Der eigentliche Gewinn der Platzhalter: wird der Abholtermin
    verschoben, zeigt die NEUE Fassung das NEUE Datum — ohne dass jemand
    den Text von Hand anfasst."""
    v = _vertrag(welt, nr=10)
    alt = _pdf(welt, v["id"])
    assert "03.10.2026" in alt

    neu_datum = "2026-11-17"
    import asyncio
    import routes.contracts as C
    dbx = _db()
    nutzer = dbx.users.find_one({"id": welt["user_id"]}, {"_id": 0})
    geaendert = asyncio.run(C.regenerate_contract_for_pickup(
        contract_id=v["id"], dealer_id=welt["dealer_id"], user=nutzer,
        pickup_date=neu_datum, pickup_time="09:00",
        grund="abholtermin_geaendert"))
    assert geaendert, "die Neuerzeugung meldete keine Aenderung"

    neu = _pdf(welt, v["id"])
    assert "17.11.2026" in neu, "das neue Abholdatum steht nicht im PDF"
    assert "03.10.2026" not in neu, "das alte Datum steht noch im PDF"
    assert "{abholdatum}" not in neu


# ------------------------------------------------------- E: GRENZFAELLE
def test_e1_fehlende_angaben_ergeben_luecken_keine_platzhalter(welt):
    v = _vertrag(welt, nr=11, pickup_date="", pickup_time="",
                 seller_address="", seller_zip="", seller_city="",
                 payment_method="")
    text = _pdf(welt, v["id"])
    assert "bis/am ____ in ____ gegen ____ statt" in text, text[:800]
    for p in ("{abholdatum}", "{ort}", "{zahlungsart}"):
        assert p not in text


def test_e2_sonderzeichen_im_eigenen_text(welt):
    """XML-Sonderzeichen duerfen das PDF nicht zerlegen."""
    eigen = "• Preis < 20.000 & > 10.000 — \"Sonderfall\" <b>fett?</b>"
    requests.put(f"{API}/dealer/settings", headers=welt["kopf"], timeout=30,
                 json={"default_special_agreements": eigen})
    try:
        v = _vertrag(welt, nr=12)
        text = _pdf(welt, v["id"])
        assert "20.000" in text and "10.000" in text
        # Steht "<b>" WOERTLICH im PDF, wurde es entschaerft — genau richtig.
        # Waere es ausgewertet worden, stuende dort nur "fett?" in fett, und
        # jeder koennte ueber den Text das Layout des Kaufvertrags steuern.
        assert "<b>fett?</b>" in text, (
            "die Auszeichnung wurde ausgewertet statt als Text gedruckt")
    finally:
        requests.put(f"{API}/dealer/settings", headers=welt["kopf"], timeout=30,
                     json={"default_special_agreements": ""})


def test_e3_sehr_langer_text_bricht_das_pdf_nicht(welt):
    lang = "\n".join(f"• Regel Nummer {i} mit etwas Text dahinter." for i in range(60))
    requests.put(f"{API}/dealer/settings", headers=welt["kopf"], timeout=30,
                 json={"default_special_agreements": lang})
    try:
        v = _vertrag(welt, nr=13)
        text = _pdf(welt, v["id"])
        assert "Regel Nummer 0 " in text and "Regel Nummer 59 " in text
    finally:
        requests.put(f"{API}/dealer/settings", headers=welt["kopf"], timeout=30,
                     json={"default_special_agreements": ""})


def test_e4_folge_mail_vorschau_und_versand_sind_gefuellt(welt):
    v = _vertrag(welt, nr=14, seller_email=f"kunde{SUF}@e2etest-mail.de")
    for art in ("korrektur", "nach_kauf", "nach_kauf_whatsapp", "bahn"):
        r = requests.get(f"{API}/contracts/{v['id']}/folge-mail/{art}",
                         headers=welt["kopf"], timeout=30)
        assert r.status_code == 200, r.text[:300]
        d = r.json()
        # WhatsApp hat keinen Betreff (21.09.2026)
        assert d["text"] and (d["betreff"] or art == "nach_kauf_whatsapp")
        assert "{" not in d["text"], f"{art}: Platzhalter blieb stehen: {d['text'][:200]}"
        assert "Max Mustermann" in d["text"]
    # Unbekannte Vorlage -> 404, nicht stillschweigend irgendwas.
    r = requests.get(f"{API}/contracts/{v['id']}/folge-mail/quatsch",
                     headers=welt["kopf"], timeout=30)
    assert r.status_code == 404
