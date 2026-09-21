# -*- coding: utf-8 -*-
"""Vertrag und Vorlagen nach Wunsch Ahmad (21.09.2026).

1. "beim Preis im Vertrag die Nummer 2 entfernen, die Daten danach bekommen
   die 1" — Kaufpreis ohne Nummer, danach "1 · Fahrzeugdaten",
   "2 · Zusicherungen & Zustand".
2. "wenn man unsere Besondere Vereinbarung übernimmt, soll nicht unter dem
   Preis wieder Ort/Abholung stehen — das nur, wenn man unsere Vereinbarung
   nicht hat" — die Zeile "Abholung: Wird abgeholt am …" nur ohne unseren
   Satz.
3. Bahnverbindung und Hinweis nach Kaufabschluss (E-Mail UND WhatsApp):
   "wir selber schicken die nicht raus — diese Vorlagen sollen da nur sein,
   damit der Kunde sie immer kopieren kann". Die App verschickt sie nicht.
4. Mail- und WhatsApp-Texte woertlich nach seiner Vorlage.
"""
import asyncio
import io
import os
import re
import sys
import uuid
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
PROJEKT = BACKEND.parent

import vertrag_platzhalter as P  # noqa: E402
import vertrag_vorlagen as V  # noqa: E402

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"

FIRMA = {"company_name": "Autohaus Probe GmbH", "address": "Hauptstr. 1",
         "zip_code": "10115", "city": "Berlin", "phone": "030 123",
         "email": "info@probe.test", "kunden_nr": "4711"}
VERTRAG = {"seller_name": "Max Muster", "seller_address": "Musterweg 5",
           "seller_zip": "20095", "seller_city": "Hamburg", "purchase_price": 12500,
           "contract_no": "KV-0921", "pickup_date": "2026-09-25",
           "payment_method": "Bar"}


def _text(pdf: bytes) -> str:
    from pypdf import PdfReader
    return " ".join("\n".join((p.extract_text() or "")
                              for p in PdfReader(io.BytesIO(pdf)).pages).split())


def _pdf(**vertrag) -> str:
    from pdf_service import generate_contract_pdf
    c = dict(VERTRAG, **vertrag)
    return _text(generate_contract_pdf(dealer=dict(FIRMA),
                                       vehicle={"make_label": "BMW", "model_label": "320d"},
                                       contract=c))


# ------------------------------------------------------------ 1. Nummern
def test_01_kaufpreis_ohne_nummer_danach_ab_eins():
    f = _pdf(additional_terms="", hu_valid="ja", hu_until="05/2027")
    assert "Kaufpreis & Konditionen" in f
    assert "2 · Kaufpreis" not in f, "die 2 vor dem Kaufpreis ist noch da"
    assert "1 · Fahrzeugdaten" in f
    assert "2 · Zusicherungen & Zustand" in f
    assert "3 · " not in f and "4 · " not in f, "alte Nummern 3/4 stehen noch drin"
    assert f.index("Kaufpreis & Konditionen") < f.index("1 · Fahrzeugdaten") \
        < f.index("2 · Zusicherungen")


def test_02_quelle_hat_keine_alten_nummern():
    quelle = (BACKEND / "pdf_service.py").read_text(encoding="utf-8")
    for alt in ('"2 · Kaufpreis', '"3 · Fahrzeugdaten', '"4 · Zusicherungen'):
        assert alt not in quelle, alt


# ------------------------------------------- 2. Abholzeile nur ohne unseren Satz
def test_03_mit_unserer_vereinbarung_keine_abholzeile():
    f = _pdf(additional_terms=V.sondervereinbarungen({}))
    assert "Wird abgeholt am" not in f, "Datum und Ort stehen doppelt im Vertrag"
    # Datum und Ort stehen trotzdem im Vertrag — in unserem Satz.
    assert "Die Fahrzeugübergabe findet bis/am 25.09.2026 in Musterweg 5, 20095 Hamburg" in f


def test_04_ohne_unsere_vereinbarung_bleibt_die_abholzeile():
    for sonder in ("", "• Eigene Regel: Schlüssel liegen im Handschuhfach."):
        f = _pdf(additional_terms=sonder)
        assert "Abholung: Wird abgeholt am 25.09.2026, Musterweg 5, 20095 Hamburg" in f, sonder
        assert f.index("Wird abgeholt am") < f.index("Kaufpreis & Konditionen")


def test_05_erkennung_des_satzes():
    assert V.uebergabe_in_vereinbarungen(V.BESONDERE_VEREINBARUNGEN)
    assert V.UEBERGABE_SATZ_ANFANG in V.BESONDERE_VEREINBARUNGEN
    # Von Hand ausgefuellt (keine Platzhalter mehr) — trotzdem unser Satz.
    assert V.uebergabe_in_vereinbarungen(
        "• Die Fahrzeugübergabe findet bis/am 01.10.2026 in Köln gegen Bar statt.")
    # Eigener Satz mit Datum UND Ort als Platzhalter: steht ebenfalls da.
    assert V.uebergabe_in_vereinbarungen("Übergabe am {abholdatum} in {ort}.")
    # Nur eines von beiden reicht nicht — dann fehlt Datum oder Ort.
    assert not V.uebergabe_in_vereinbarungen("Übergabe am {abholdatum}.")
    assert not V.uebergabe_in_vereinbarungen("")
    assert not V.uebergabe_in_vereinbarungen(None)
    # Schalter aus, nur eigener Text: keine Uebergabe drin.
    assert not V.uebergabe_in_vereinbarungen(V.sondervereinbarungen(
        {"sondervereinbarung_standard_aktiv": False,
         "default_special_agreements": "• Nur eigene Regel."}))


# ------------------------------------------------------- 4. Texte woertlich
def _saetze(text):
    return " ".join(text.split())


def test_06_mail_und_whatsapp_woertlich():
    email = _saetze(V.EMAIL_TEXT)
    assert V.EMAIL_BETREFF == "KFZ-E-Mail-Bestätigung"
    assert ("vielen Dank für das nette Gespräch. Wie besprochen erhalten Sie im "
            "Anhang dieser E-Mail den Kaufvertrag. Bitte überprüfen Sie sorgfältig "
            "die im Kaufvertrag eingetragenen Daten und bestätigen Sie anschließend "
            "diese E-Mail.") in email
    wa = _saetze(V.WHATSAPP_TEXT)
    assert ("danke für das nette Gespräch bezüglich Ihres Fahrzeugs. Wie bereits "
            "besprochen, erhalten Sie nachfolgend den Kaufvertrag. Bitte überprüfen "
            "Sie diesen sorgfältig auf die darin gemachten Angaben, und bestätigen "
            "Sie ihn anschließend.") in wa
    for text in (V.EMAIL_TEXT, V.WHATSAPP_TEXT, V.EMAIL_TEXT_KORREKTUR,
                 V.EMAIL_TEXT_BAHN, V.EMAIL_TEXT_NACH_KAUF, V.WHATSAPP_TEXT_NACH_KAUF):
        assert text.startswith("Sehr geehrte/r Frau/Herr {kunde_name},\n\n"), text[:60]
        assert text.endswith("Ihr Autohaus\n{haendler_name}"), text[-60:]


def test_07_bahn_und_hinweis_woertlich():
    bahn = _saetze(V.EMAIL_TEXT_BAHN)
    assert ("anbei erhalten Sie die Bahnverbindung mit der voraussichtlichen "
            "Ankunftszeit unseres Fahrers. Sollte es zu einer Verspätung kommen, "
            "wird sich unser Fahrer bei Ihnen telefonisch melden.") in bahn
    kern = ("Auf Grundlage von Angebot und Annahme ist somit zwischen uns beiden ein "
            "rechtskräftiger und im Rechtsverkehr mustergültiger Vertrag zustande "
            "gekommen, der seine Gültigkeit hat. Daher bitte ich Sie darum, weiteren "
            "Interessenten mitzuteilen, dass das Fahrzeug bereits verkauft ist. "
            "Sollte das Fahrzeug nach der Übergabe noch angemeldet sein, verpflichten "
            "wir uns, das Fahrzeug innerhalb von fünf Werktagen abzumelden. Bitte "
            "geben Sie keine Auskünfte über Kaufpreis, Abholzeit und Käufer aus. Ich "
            "melde mich stets eingangs des Gesprächs mit der Kundennummer. Nach "
            "telefonischer Vereinbarung wird ein Fahrer bei Ihnen erscheinen, der das "
            "Fahrzeug entgegennimmt und Ihnen die Kaufsumme wie vertraglich vereinbart "
            "mittels der im Vertrag festgelegten Zahlungsmethode überreicht. Ich bitte "
            "Sie ferner darum, das Inserat nun aus dem Netz zu nehmen. Bei weiteren "
            "Fragen können Sie uns gerne anrufen oder eine Email schreiben. "
            "Liebe Grüße")
    wa = _saetze(V.WHATSAPP_TEXT_NACH_KAUF)
    mail = _saetze(V.EMAIL_TEXT_NACH_KAUF)
    assert "ich bedanke mich für die Bestätigung des Kaufvertrags per WhatsApp. " + kern in wa
    assert "ich bedanke mich für die Bestätigung des Kaufvertrags per E-Mail. " + kern in mail


def test_08_alle_vorlagen_lassen_sich_fuellen():
    bekannt = set(P.werte(VERTRAG, FIRMA))
    for art in V.FOLGE_MAILS:
        betreff, text = V.vorlage({}, art)
        for teil in (betreff, text):
            assert set(re.findall(r"\{[a-zä-ü_]+\}", teil)) <= bekannt, art
            assert "{" not in P.ersetzen(teil, VERTRAG, FIRMA), art
    fertig = P.ersetzen(V.EMAIL_TEXT_BAHN, VERTRAG, FIRMA)
    assert "Max Muster" in fertig and fertig.endswith("Ihr Autohaus\nAutohaus Probe GmbH")


# ----------------------------------------------- 3. nur kopieren, nie senden
def test_09_kopiervorlagen_und_whatsapp_ohne_betreff():
    assert set(V.KOPIER_VORLAGEN) == {"nach_kauf", "nach_kauf_whatsapp", "bahn"}
    assert set(V.FOLGE_MAILS) == {"korrektur", *V.KOPIER_VORLAGEN}
    assert V.vorlage({}, "nach_kauf_whatsapp") == ("", V.WHATSAPP_TEXT_NACH_KAUF)
    eigen = {"whatsapp_template_nach_kauf": "Mein WhatsApp-Text"}
    assert V.vorlage(eigen, "nach_kauf_whatsapp") == ("", "Mein WhatsApp-Text")


def test_10_verschicken_nur_von_hand_mit_bremse():
    """Wunsch Ahmad 21.09.2026 (spaet): "doch zum Verschicken kann bleiben".
    Die E-Mail-Fassungen gehen wieder per Knopf raus — nie automatisch, mit
    Versandbremse, nicht nach Storno, WhatsApp und Korrektur nicht per Mail."""
    import ast
    import routes.contracts as C
    quelle = (BACKEND / "routes" / "contracts.py").read_text(encoding="utf-8")
    fn = next(k for k in ast.walk(ast.parse(quelle))
              if isinstance(k, ast.AsyncFunctionDef) and k.name == "folge_mail_senden")
    koerper = ast.unparse(fn)
    assert "await _versand_limiter.check(" in koerper, "keine Versandbremse"
    assert "FOLGE_MAIL_STORNIERT" in koerper and "kaufvorgaenge" in koerper
    assert "_firma_des_vertrags(" in koerper, "Firma des Aufrufers statt des Vertrags"
    assert "_ersetzen((body.message" in koerper, "eigener Text ohne Platzhalter-Ersetzung"
    assert C.FOLGE_MAIL_VERSCHICKBAR == ("nach_kauf", "bahn")
    assert set(C.FOLGE_MAIL_ART_HINWEIS) == {"korrektur", "nach_kauf_whatsapp"}
    # Firma aus dem Vertrag gewinnt ueber die des Aufrufers
    f = C._firma_des_vertrags({"contract_data": {"dealer_company": "Sucher GmbH"}},
                              {"company_name": "Chef AG", "phone": "1"})
    assert f["company_name"] == "Sucher GmbH" and f["phone"] == "1"


def test_11_oberflaeche_kopiert_oder_verschickt():
    dialog = (PROJEKT / "frontend" / "src" / "components"
              / "FolgeMailDialog.jsx").read_text(encoding="utf-8")
    for art in V.KOPIER_VORLAGEN:
        assert f'id: "{art}"' in dialog, art
    assert 'id: "korrektur"' not in dialog, "Korrektur geht ueber den Vertragsversand"
    assert "KopierKnopf" in dialog
    assert "api.post(`/contracts/${contract.id}/folge-mail`" in dialog
    # EIN Schluessel je Dialog/Vorlage, kein crypto.randomUUID ohne Ersatz
    assert "idempotency_key: schluessel.current" in dialog
    assert "crypto.randomUUID()" not in dialog
    assert 'globalThis.crypto?.randomUUID?.()' in dialog
    einst = (PROJEKT / "frontend" / "src" / "pages" / "app"
             / "Einstellungen.jsx").read_text(encoding="utf-8")
    block = einst[einst.index('title="Hinweis nach Kaufabschluss & Bahnverbindung"'):]
    block = block[:block.index("</Section>")]
    for feld in ("email_subject_bahn", "email_template_bahn", "email_subject_nach_kauf",
                 "email_template_nach_kauf", "whatsapp_template_nach_kauf"):
        assert f"value={{form.{feld}}}" in block, feld
    assert block.count(" kopieren") >= 5, "nicht jede Vorlage hat einen Kopier-Knopf"


def test_12_versand_dialog_nimmt_die_korrektur_vorlage():
    jsx = (PROJEKT / "frontend" / "src" / "components"
           / "SendDialog.jsx").read_text(encoding="utf-8")
    assert "nachKorrektur(contract)" in jsx
    assert "dealer?.email_subject_korrektur" in jsx
    assert "dealer?.email_template_korrektur" in jsx


# ------------------------------------------- leere Felder zeigen den Standard
def test_13_leere_vorlagen_zeigen_den_standard():
    from routes.dealer import _mit_digital_standard
    firma = {"email_template": "", "email_subject": "Mein Betreff",
             "whatsapp_template_nach_kauf": "   "}
    out = _mit_digital_standard(firma)
    assert out["email_template"] == V.EMAIL_TEXT
    assert out["email_subject"] == "Mein Betreff", "eigener Text wurde ueberschrieben"
    assert out["whatsapp_template_nach_kauf"] == V.WHATSAPP_TEXT_NACH_KAUF
    assert out["email_template_bahn"] == V.EMAIL_TEXT_BAHN
    # Das Freitextfeld der Vereinbarungen bleibt leer (gehoert der Firma).
    assert not out.get("default_special_agreements")
    assert _mit_digital_standard(None) is None


# ---------------------------------------------- Migration fuer Bestandsfirmen
@pytest.fixture
def wegwerf_db():
    from motor.motor_asyncio import AsyncIOMotorClient
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    name = f"autoschnell_v0921_{uuid.uuid4().hex[:10]}"
    db = client[name]
    try:
        yield db, loop.run_until_complete
    finally:
        loop.run_until_complete(client.drop_database(name))
        client.close()
        loop.close()
        asyncio.set_event_loop(None)


def test_14_migration_ersetzt_nur_unveraenderte_alte_texte(wegwerf_db):
    import migrationen as M
    db, run = wegwerf_db
    alt_bahn = M._FRUEHERE_VORLAGEN["email_template_bahn"][0]
    alt_mail = M._FRUEHERE_VORLAGEN["email_template"][0]
    run(db.dealers.insert_many([
        {"id": "alt", "email_template_bahn": alt_bahn,
         "email_template": alt_mail.replace("\n", "\r\n")},
        {"id": "eigen", "email_template_bahn": "Unser eigener Bahn-Text",
         "email_template": alt_mail + " Zusatz"},
    ]))
    run(db.users.insert_many([
        {"id": "su-alt", "settings_override": {"email_template_bahn": alt_bahn}},
        {"id": "su-eigen", "settings_override": {"email_template_bahn": "Meiner"}},
    ]))
    erg = run(M.m10_vorlagen_texte(db))
    alt = run(db.dealers.find_one({"id": "alt"}))
    eigen = run(db.dealers.find_one({"id": "eigen"}))
    assert alt["email_template_bahn"] == V.EMAIL_TEXT_BAHN
    assert alt["email_template"] == V.EMAIL_TEXT
    assert eigen["email_template_bahn"] == "Unser eigener Bahn-Text"
    assert eigen["email_template"] == alt_mail + " Zusatz"
    su = run(db.users.find_one({"id": "su-alt"}))
    assert su["settings_override"]["email_template_bahn"] == V.EMAIL_TEXT_BAHN
    su2 = run(db.users.find_one({"id": "su-eigen"}))
    assert su2["settings_override"]["email_template_bahn"] == "Meiner"
    assert erg == {"firmen_felder": 2, "sucher_felder": 1}
    # Idempotent: ein zweiter Lauf aendert nichts mehr.
    assert run(M.m10_vorlagen_texte(db)) == {"firmen_felder": 0, "sucher_felder": 0}


def test_14b_migration_kennt_die_texte_der_live_firmen(wegwerf_db):
    """Jede Live-Firma seit dem Reset am 14.09. wurde vom Betreiber angelegt
    und hat die festen Texte von routes/admin.py gespeichert (vor b6cd893) —
    die muessen genauso umgestellt werden (Nachpruefung 21.09.2026)."""
    import migrationen as M
    db, run = wegwerf_db
    run(db.dealers.insert_one({
        "id": "live", "email_subject": "Kaufvertrag für Ihr Fahrzeug",
        "email_template": "Guten Tag,\n\nanbei sende ich Ihnen den Kaufvertrag.\n\nMfG\n{händler_name}",
        "whatsapp_template": "Hallo, hier ist der Kaufvertrag. Bitte prüfen."}))
    run(M.m10_vorlagen_texte(db))
    live = run(db.dealers.find_one({"id": "live"}))
    assert live["email_subject"] == V.EMAIL_BETREFF == "KFZ-E-Mail-Bestätigung"
    assert live["email_template"] == V.EMAIL_TEXT
    assert live["whatsapp_template"] == V.WHATSAPP_TEXT


def test_15_migration_ist_eingetragen():
    import migrationen as M
    assert (10, "vorlagen_texte", M.m10_vorlagen_texte) in M.MIGRATIONEN
    assert M.ZIEL_VERSION == 10
    for feld, alte in M._FRUEHERE_VORLAGEN.items():
        assert feld in V.STARTWERTE
        assert V.STARTWERTE[feld] not in alte, f"{feld}: neuer Text gleich altem"
