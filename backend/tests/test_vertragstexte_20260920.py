# -*- coding: utf-8 -*-
"""Vertragstexte und Platzhalter (Vorlage Ahmad 20.09.2026).

Der Wunsch: In den Besonderen Vereinbarungen steht

    • Die Fahrzeugübergabe findet bis/am ___ in ___ gegen ___ statt.

und Abholdatum, Übergabeort (Anschrift des Verkäufers) sowie Zahlungsart
sollen von selbst eingesetzt werden — nicht von Hand nachgetippt.

Vorher wurden Platzhalter NUR im Browser ersetzt (SendDialog.jsx), und auch
nur fuer E-Mail und WhatsApp. Die Besonderen Vereinbarungen stehen im PDF,
das der Server baut — dort kam nie etwas an.

Dazu drei Mails, die der Sucher NACHTRAEGLICH von Hand schickt: erneuter
Versand nach einer Korrektur, Hinweis nach dem Kaufabschluss und die
Bahnverbindung.
"""
import re
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
PROJEKT = BACKEND.parent

import vertrag_platzhalter as P  # noqa: E402
import vertrag_vorlagen as V  # noqa: E402


VERTRAG = {
    "seller_name": "Max Mustermann",
    "seller_address": "Hauptstr. 5",
    "seller_zip": "40210",
    "seller_city": "Düsseldorf",
    "pickup_date": "2026-10-03",
    "pickup_time": "14:30",
    "payment_method": "Echtzeitüberweisung",
    "purchase_price": 15900,
    "contract_no": "KV-2026-0042",
    "make": "BMW", "model": "320d",
}
FIRMA = {"company_name": "Autohaus Schnell", "kunden_nr": 10023,
         "phone": "0211 12345", "email": "info@example.invalid"}


# ------------------------------------------------------------ Ersetzung
def test_01_die_drei_luecken_werden_gefuellt():
    text = P.ersetzen(V.BESONDERE_VEREINBARUNGEN, VERTRAG, FIRMA)
    assert "03.10.2026 um 14:30 Uhr" in text, text
    assert "Hauptstr. 5, 40210 Düsseldorf" in text, text
    assert "Echtzeitüberweisung" in text, text
    assert "10023" in text, "die Kundennummer fehlt"
    assert "{" not in text, f"ein Platzhalter blieb stehen: {text}"


def test_02_fehlende_angabe_wird_zur_luecke_nie_zum_platzhalter():
    """Ein Kunde darf in seinem Kaufvertrag NIE '{abholdatum}' lesen."""
    text = P.ersetzen(V.BESONDERE_VEREINBARUNGEN, {}, {})
    assert "{abholdatum}" not in text and "{ort}" not in text
    assert "{zahlungsart}" not in text and "{kundennummer}" not in text
    assert text.count(P.LUECKE) >= 4, text
    assert "bis/am ____ in ____ gegen ____ statt" in text, text


@pytest.mark.parametrize("eingabe,erwartet", [
    ("Bar", "Barzahlung"),
    ("bar", "Barzahlung"),
    ("Echtzeitüberweisung", "Echtzeitüberweisung"),
    ("Überweisung", "Banküberweisung"),
    ("Banküberweisung", "Banküberweisung"),
    ("", ""),
    # Freitext alter Vertraege bleibt unveraendert — nichts umdeuten.
    ("Bar / Überweisung", "Bar / Überweisung"),
    ("Verrechnung mit Inzahlungnahme", "Verrechnung mit Inzahlungnahme"),
])
def test_03_zahlungsart_lesbar(eingabe, erwartet):
    assert P.zahlungsart({"payment_method": eingabe}) == erwartet


def test_04_ort_ist_die_anschrift_des_verkaeufers():
    """Wunsch Ahmad: 'bei ort adresse'."""
    assert P.uebergabeort(VERTRAG) == "Hauptstr. 5, 40210 Düsseldorf"
    # Teilangaben sind erlaubt, es darf nur kein Komma-Salat entstehen.
    assert P.uebergabeort({"seller_city": "Köln"}) == "Köln"
    assert P.uebergabeort({"seller_address": "Weg 1"}) == "Weg 1"
    assert P.uebergabeort({}) == ""


def test_05_datum_und_uhrzeit():
    assert P.abholzeitpunkt(VERTRAG) == "03.10.2026 um 14:30 Uhr"
    assert P.abholzeitpunkt({"pickup_date": "2026-01-09"}) == "09.01.2026"
    assert P.abholzeitpunkt({}) == ""
    # Kaputtes Datum nicht heimlich umbauen.
    assert P.datum_de("irgendwas") == "irgendwas"


def test_06_die_daten_kommen_auch_aus_contract_data():
    """Alte Vertraege tragen die Felder unter contract_data."""
    alt = {"contract_data": {"seller_city": "Essen", "pickup_date": "2026-05-04",
                             "payment_method": "Bar"}}
    assert P.uebergabeort(alt) == "Essen"
    assert P.abholzeitpunkt(alt) == "04.05.2026"
    assert P.zahlungsart(alt) == "Barzahlung"


# ------------------------------------------------------------- Im PDF
def test_07_das_pdf_setzt_die_platzhalter_ein():
    """Gegenprobe an der Quelle: frueher ging die Ersetzung am PDF vorbei."""
    quelle = (BACKEND / "pdf_service.py").read_text(encoding="utf-8")
    code = "\n".join(z.split("#", 1)[0] for z in quelle.splitlines())
    assert "_platzhalter_ersetzen" in code, (
        "das Vertrags-PDF ersetzt keine Platzhalter — die Besonderen "
        "Vereinbarungen blieben leer")
    # Beide Textbloecke, nicht nur einer.
    assert code.count("_platzhalter_ersetzen(") >= 3, (
        "nicht alle Textbloecke des Vertrags gehen durch die Ersetzung")


# --------------------------------------------------------- Die Vorlagen
def test_08_startwerte_sind_gesetzt_und_ohne_leere_texte():
    for feld, wert in V.STARTWERTE.items():
        if isinstance(wert, bool):
            continue                       # Schalter, kein Text
        if feld == "default_special_agreements":
            # Bewusst leer: das Freitextfeld gehoert der Firma, unser
            # Standardsatz kommt ueber den Schalter dazu.
            assert wert == ""
            continue
        assert wert and wert.strip(), f"{feld} ist leer"
    # Die Texte, die Ahmad vorgegeben hat, muessen wirklich drinstehen.
    assert "vielen Dank für das nette Gespräch" in V.EMAIL_TEXT
    assert "Inserat nun aus dem Netz zu nehmen" in V.EMAIL_TEXT_NACH_KAUF
    assert "Inserat nun aus dem Netz zu nehmen" in V.WHATSAPP_TEXT_NACH_KAUF
    assert "Bahnverbindung" in V.EMAIL_TEXT_BAHN
    assert "Zulassungsbescheinigung" in V.BESONDERE_VEREINBARUNGEN


def test_09_jede_vorlage_laesst_sich_vollstaendig_fuellen():
    """Keine Vorlage darf einen Platzhalter tragen, den es nicht gibt."""
    bekannt = set(P.werte(VERTRAG, FIRMA))
    # Der Standardsatz gehoert mit geprueft, auch wenn er nicht mehr in
    # STARTWERTE steht (er haengt jetzt am Schalter).
    zu_pruefen = {f: w for f, w in V.STARTWERTE.items() if isinstance(w, str) and w}
    zu_pruefen["BESONDERE_VEREINBARUNGEN"] = V.BESONDERE_VEREINBARUNGEN
    for feld, text in zu_pruefen.items():
        gefunden = set(re.findall(r"\{[a-zä-ü_]+\}", text))
        unbekannt = gefunden - bekannt
        assert not unbekannt, f"{feld} nutzt unbekannte Platzhalter: {unbekannt}"
        fertig = P.ersetzen(text, VERTRAG, FIRMA)
        assert "{" not in fertig, f"{feld} bleibt unvollstaendig: {fertig[:120]}"


def test_10_vorlage_nimmt_den_eigenen_text_der_firma():
    eigen = {"email_subject_bahn": "Mein Betreff",
             "email_template_bahn": "Mein Text"}
    assert V.vorlage(eigen, "bahn") == ("Mein Betreff", "Mein Text")
    # Leer = Standard, nicht leer bleiben.
    assert V.vorlage({"email_template_bahn": "   "}, "bahn")[1] == V.EMAIL_TEXT_BAHN
    assert V.vorlage({}, "unbekannt") == ("", "")


# ------------------------------------------------ Backend und Oberflaeche
def test_11_die_platzhalter_liste_ist_in_beiden_gleich():
    """Der haeufigste Fehler waere, im Browser einen Platzhalter zu bewerben,
    den der Server nicht kennt — er stuende dann woertlich im Vertrag."""
    jsx = (PROJEKT / "frontend" / "src" / "pages" / "app"
           / "Einstellungen.jsx").read_text(encoding="utf-8")
    block = jsx[jsx.index("const PLATZHALTER = ["):]
    block = block[:block.index("];")]
    beworben = set(re.findall(r'"(\{[^"]+\})"', block))
    bekannt = set(P.werte(VERTRAG, FIRMA))
    assert beworben <= bekannt, (
        f"die Oberflaeche bewirbt Platzhalter, die der Server nicht kennt: "
        f"{beworben - bekannt}")
    # Und die drei neuen muessen wirklich beworben werden.
    assert {"{ort}", "{zahlungsart}"} <= beworben


def test_12_der_versand_dialog_kennt_dieselben_namen():
    jsx = (PROJEKT / "frontend" / "src" / "components"
           / "SendDialog.jsx").read_text(encoding="utf-8")
    for name in ("{ort}", "{zahlungsart}", "{kaufpreis}", "{vertragsnummer}"):
        assert f'"{name}"' in jsx, f"{name} fehlt im Versand-Dialog"
    # Auch dort darf nie ein Platzhalter stehen bleiben.
    assert '"____"' in jsx, (
        "der Versand-Dialog laesst leere Platzhalter woertlich stehen")


# ------------------------------------------------------- Die Folge-Mails
def test_13_die_folge_mails_gibt_es_als_route():
    """Vorschau zum Kopieren und Versand per Knopf (Wunsch Ahmad 21.09.2026:
    erst nur kopieren, dann "doch zum Verschicken kann bleiben"). Der Versand
    haengt an derselben Bremse wie der Vertragsversand.
    Pruefbericht 20.09.2026: Hier stand frueher `_versand_limiter.erlaubt` —
    eine Methode, die es nicht gibt; jeder Versand endete mit 500. Deshalb
    wird am echten Funktionskoerper geprueft UND in
    test_sucher_dashboard_20260920 wirklich versendet."""
    quelle = (BACKEND / "routes" / "contracts.py").read_text(encoding="utf-8")
    assert '@router.get("/contracts/{contract_id}/folge-mail/{art}")' in quelle
    assert '@router.post("/contracts/{contract_id}/folge-mail")' in quelle
    import ast
    import rate_limiter
    assert hasattr(rate_limiter.SlidingWindowRateLimiter, "check")
    assert not hasattr(rate_limiter.SlidingWindowRateLimiter, "erlaubt")
    fn = next(k for k in ast.walk(ast.parse(quelle))
              if isinstance(k, ast.AsyncFunctionDef) and k.name == "folge_mail_senden")
    koerper = ast.unparse(fn)
    assert "await _versand_limiter.check(" in koerper
    assert set(V.FOLGE_MAILS) == {"korrektur", "nach_kauf", "nach_kauf_whatsapp", "bahn"}


def test_14_die_oberflaeche_hat_den_knopf():
    jsx = (PROJEKT / "frontend" / "src" / "pages" / "app"
           / "PDFArchiv.jsx").read_text(encoding="utf-8")
    assert "FolgeMailDialog" in jsx, "der Knopf fehlt in der Vertragsliste"
    dialog = (PROJEKT / "frontend" / "src" / "components"
              / "FolgeMailDialog.jsx").read_text(encoding="utf-8")
    for art in ("nach_kauf", "nach_kauf_whatsapp", "bahn"):
        assert f'id: "{art}"' in dialog, f"{art} fehlt im Dialog"
    assert "KopierKnopf" in dialog
    assert "idempotency_key" in dialog, "kein Doppelklick-Schutz"


# ---------------------------------------- Schalter fuer unseren Standardsatz
def test_15_standardsatz_laesst_sich_ein_und_ausschalten():
    """Wunsch Ahmad 20.09.2026: 'man kann sie einschalten dann ist sie da
    oder abschalten oder man kann eigene einfügen und abspeichern'."""
    eigen = "• Eigene Regel eins.\n• Eigene Regel zwei."

    # AN (Standardstellung, auch wenn das Feld fehlt): beide untereinander.
    an = V.sondervereinbarungen({"default_special_agreements": eigen})
    assert V.BESONDERE_VEREINBARUNGEN in an
    assert eigen in an
    assert an.index(V.BESONDERE_VEREINBARUNGEN) < an.index(eigen), (
        "der eigene Text steht ueber unserem Standardsatz")

    # AUS: nur der eigene Text.
    aus = V.sondervereinbarungen({"sondervereinbarung_standard_aktiv": False,
                                  "default_special_agreements": eigen})
    assert aus == eigen
    assert "{abholdatum}" not in aus

    # AUS und nichts Eigenes: der Abschnitt entfaellt im Vertrag.
    assert V.sondervereinbarungen({"sondervereinbarung_standard_aktiv": False}) == ""

    # AN und nichts Eigenes: nur unser Satz.
    assert V.sondervereinbarungen({}) == V.BESONDERE_VEREINBARUNGEN


def test_16_der_schalter_steht_standardmaessig_auf_an():
    assert V.standard_an({}) is True, "fehlt das Feld, gilt AN"
    assert V.standard_an({"sondervereinbarung_standard_aktiv": True}) is True
    assert V.standard_an({"sondervereinbarung_standard_aktiv": False}) is False
    assert V.standard_an(None) is True


def test_17_der_vertrag_nimmt_beide_teile():
    """Gegenprobe an der Quelle: frueher wurde nur das Freitextfeld kopiert."""
    quelle = (BACKEND / "routes" / "contracts.py").read_text(encoding="utf-8")
    code = "\n".join(z.split("#", 1)[0] for z in quelle.splitlines())
    assert code.count("_vorlagen.sondervereinbarungen(dealer)") == 2, (
        "nicht beide Vertragswege nehmen Standardsatz UND eigenen Text")
    assert 'dealer.get("default_special_agreements", "")' not in code, (
        "ein Weg kopiert weiter nur das Freitextfeld — der Schalter waere "
        "dort wirkungslos")


def test_18_der_schalter_kommt_durch_die_einstellungen():
    dealer_py = (BACKEND / "routes" / "dealer.py").read_text(encoding="utf-8")
    assert "sondervereinbarung_standard_aktiv" in dealer_py, (
        "der Schalter laesst sich gar nicht speichern")
    deps_py = (BACKEND / "deps.py").read_text(encoding="utf-8")
    assert "sondervereinbarung_standard_aktiv" in deps_py, (
        "der Schalter erreicht die Firmen-Einstellungen nicht")
    jsx = (PROJEKT / "frontend" / "src" / "pages" / "app"
           / "Einstellungen.jsx").read_text(encoding="utf-8")
    assert 'data-testid="set-sonder-standard"' in jsx, (
        "in der Oberflaeche gibt es keinen Schalter")


def test_19_die_oberflaeche_zeigt_denselben_satz():
    """Der Text neben dem Schalter muss der sein, der wirklich gedruckt wird."""
    jsx = (PROJEKT / "frontend" / "src" / "pages" / "app"
           / "Einstellungen.jsx").read_text(encoding="utf-8")
    block = jsx[jsx.index("const STANDARD_SONDERSATZ ="):]
    block = block[:block.index(";\n")]
    for stueck in ("{abholdatum}", "{ort}", "{zahlungsart}", "{kundennummer}",
                   "Zulassungsbescheinigung"):
        assert stueck in block, f"{stueck} fehlt im angezeigten Standardsatz"


def test_20_jeder_sucher_speichert_seine_eigene_fassung():
    """Wunsch Ahmad 20.09.2026: 'man muss das alles in den Einstellungen
    festlegen und speichern für seinen Account, damit das PDF klappt'.

    Der Chef fuellt die Firmen-Einstellungen vor; jeder Sucher darf sie FUER
    SICH ueberschreiben (users.settings_override). Genommen wird beim
    Erstellen des Vertrags die wirksame Fassung (deps.effective_dealer) —
    und die landet damit auch im PDF.
    """
    import deps
    neu = {
        "sondervereinbarung_standard_aktiv",
        "default_special_agreements", "default_terms", "digital_vertragstext",
        "email_subject", "email_template", "whatsapp_template",
        "email_subject_korrektur", "email_template_korrektur",
        "email_subject_nach_kauf", "email_template_nach_kauf",
        "whatsapp_template_nach_kauf",
        "email_subject_bahn", "email_template_bahn",
    }
    fehlt = neu - deps.SUCHER_SETTINGS_FIELDS
    assert not fehlt, (
        f"diese Einstellungen kann ein Sucher NICHT fuer sich speichern: {fehlt}")


def test_21_der_vertrag_nimmt_die_wirksame_fassung():
    """Gegenprobe: der Vertragsweg darf nicht am Firmen-Dokument vorbei
    lesen — sonst waere die eigene Fassung des Suchers wirkungslos."""
    quelle = (BACKEND / "routes" / "contracts.py").read_text(encoding="utf-8")
    anfang = quelle.index("async def create_contract")
    block = quelle[anfang:anfang + 4000]
    assert "effective_dealer" in block, (
        "die Vertragsanlage liest die Firmen-Einstellungen roh statt die "
        "wirksame Fassung des Suchers")


# ------------------------------------------------- Das fertige PDF
def _pdf_text(contract: dict, dealer: dict) -> str:
    """Einen Vertrag wirklich bauen und den Text auslesen — nicht nur die
    Quelle ansehen. Nur so faellt auf, wenn etwas im PDF fehlt."""
    import io as _io
    import pdf_service as PS
    roh = PS.generate_contract_pdf(
        dealer=dealer, vehicle={"make": "BMW", "model": "320d"},
        contract=contract, digital=True)
    try:
        from pypdf import PdfReader
    except ImportError:                      # aeltere Umgebungen
        from PyPDF2 import PdfReader
    return "\n".join(s.extract_text() or ""
                     for s in PdfReader(_io.BytesIO(roh)).pages)


def test_22_kaufpreis_steht_vor_den_fahrzeugdaten():
    """Wunsch Ahmad 20.09.2026: 'preis bitte unter Halter und Käufer'.

    Vorher stand der Kaufpreis weit unten, hinter Fahrzeugdaten und
    Ausstattung — wer den Vertrag ueberfliegt, sucht die Zahl aber oben bei
    den beiden Parteien.
    """
    firma = dict(FIRMA)
    vertrag = dict(VERTRAG,
                   additional_terms=V.sondervereinbarungen(firma))
    text = _pdf_text(vertrag, firma)
    assert "Kaufpreis" in text and "Fahrzeugdaten" in text, text[:400]
    assert text.index("Kaufpreis") < text.index("Fahrzeugdaten"), (
        "der Kaufpreis steht immer noch hinter den Fahrzeugdaten")
    # Und unter den beiden Parteien, nicht darueber.
    assert text.index("Käufer") < text.index("Kaufpreis"), (
        "der Kaufpreis steht ueber den Angaben zu Halter und Käufer")


def test_23_die_besonderen_vereinbarungen_stehen_gefuellt_im_pdf():
    """Der eigentliche Wunsch, am fertigen Dokument geprueft."""
    firma = dict(FIRMA)
    vertrag = dict(VERTRAG, additional_terms=V.sondervereinbarungen(firma))
    text = _pdf_text(vertrag, firma)
    assert "03.10.2026" in text, "das Abholdatum wurde nicht eingesetzt"
    assert "Hauptstr. 5, 40210" in text, "der Übergabeort fehlt"
    assert "Echtzeitüberweisung" in text, "die Zahlungsart fehlt"
    assert "10023" in text, "die Kundennummer fehlt"
    for platzhalter in ("{abholdatum}", "{ort}", "{zahlungsart}", "{kundennummer}"):
        assert platzhalter not in text, (
            f"{platzhalter} steht woertlich im Kaufvertrag des Kunden")


def test_24_abgeschalteter_standardsatz_steht_nicht_im_pdf():
    firma = dict(FIRMA, sondervereinbarung_standard_aktiv=False,
                 default_special_agreements="• Nur meine eigene Regel.")
    vertrag = dict(VERTRAG, additional_terms=V.sondervereinbarungen(firma))
    text = _pdf_text(vertrag, firma)
    assert "Nur meine eigene Regel" in text
    assert "Fahrzeugübergabe findet" not in text, (
        "der abgeschaltete Standardsatz steht trotzdem im Vertrag")
