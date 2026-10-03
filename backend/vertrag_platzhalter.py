# -*- coding: utf-8 -*-
"""Platzhalter in Vertragstexten — EINE Stelle fuer alles.

Wunsch Ahmad 20.09.2026: In den Besonderen Vereinbarungen steht

    • Die Fahrzeugübergabe findet bis/am {abholdatum} in {ort}
      gegen {zahlungsart} statt.

und beim Erstellen des Vertrags sollen Abholdatum, Übergabeort und
Zahlungsart von selbst eingesetzt werden — nicht von Hand nachgetippt.

Bisher wurden Platzhalter NUR im Browser ersetzt (SendDialog.jsx), und
auch nur fuer E-Mail und WhatsApp. Die Besonderen Vereinbarungen stehen
aber im PDF, das der Server baut — dort kam nie eine Ersetzung an. Dieser
Baustein macht daraus eine gemeinsame Quelle:

  * das Vertrags-PDF (Besondere Vereinbarungen, Vertragsbedingungen)
  * die Folge-Mails (Korrektur, Hinweis nach Kaufabschluss, Bahnverbindung)
  * der Versand-Dialog im Browser nutzt dieselben NAMEN (der Test
    test_vertragstexte_20260920.py haelt beide Listen zusammen)

Grundsatz: Ein Platzhalter, zu dem es keine Angabe gibt, wird NICHT
stehen gelassen — sonst stuende "{abholdatum}" woertlich im Kaufvertrag
des Kunden. Er wird durch den Ersatztext ersetzt (Standard: "____"), also
genau die Luecke, die man frueher von Hand ausgefuellt haette.
"""
from typing import Dict, Optional

#: Wird eingesetzt, wenn zu einem Platzhalter nichts vorliegt.
LUECKE = "____"

#: Alle Platzhalter mit einer kurzen Erklaerung — die Oberflaeche zeigt
#: diese Liste in den Einstellungen an, damit niemand raten muss.
PLATZHALTER_HILFE = {
    "{haendler_name}": "Name der Firma",
    "{händler_name}": "Name der Firma (mit Umlaut)",
    "{kunde_name}": "Name des Verkäufers",
    "{fahrzeug}": "Marke und Modell",
    "{marke}": "Marke",
    "{modell}": "Modell",
    # Rollenpruefung 22.09.2026 (RP-484): im Kaufvertrag nur das Datum.
    "{abholdatum}": "Abholdatum (in Mails mit Uhrzeit, im Kaufvertrag nur das Datum)",
    "{ort}": "Übergabeort — Anschrift des Verkäufers",
    "{zahlungsart}": "Bar, Echtzeitüberweisung oder Banküberweisung",
    "{kaufpreis}": "Kaufpreis in Euro",
    "{vertragsnummer}": "Nummer des Kaufvertrags",
    "{kundennummer}": "Kundennummer für Verträge (nicht die Anmeldenummer)",
    "{telefon}": "Telefonnummer der Firma",
    "{email}": "E-Mail-Adresse der Firma",
}


def _text(wert) -> str:
    return str(wert or "").strip()


def datum_de(iso: str) -> str:
    """JJJJ-MM-TT -> TT.MM.JJJJ. Alles andere unveraendert zurueck."""
    roh = _text(iso)
    teile = roh.split("-")
    if len(teile) == 3 and len(teile[0]) == 4 and all(t.isdigit() for t in teile):
        return f"{teile[2]}.{teile[1]}.{teile[0]}"
    return roh


def abholzeitpunkt(vertrag: dict, mit_uhrzeit: bool = True) -> str:
    """Datum plus Uhrzeit, so wie es im Satz stehen soll.

    Rollenpruefung 22.09.2026 (RP-484): mit_uhrzeit=False fuer den
    Kaufvertrag — dort steht nur das Datum (Wunsch Ahmad 15.09.2026, der
    Dialog sagt "Abholuhrzeit … steht nicht im Vertrag"). Mails behalten
    Datum und Uhrzeit."""
    daten = vertrag.get("contract_data") or {}
    datum = datum_de(vertrag.get("pickup_date") or daten.get("pickup_date") or "")
    if not mit_uhrzeit:
        return datum
    zeit = _text(vertrag.get("pickup_time") or daten.get("pickup_time"))
    if datum and zeit:
        return f"{datum} um {zeit} Uhr"
    return datum or zeit


def uebergabeort(vertrag: dict) -> str:
    """Wunsch Ahmad 20.09.2026: der Übergabeort ist die Anschrift des
    Verkäufers — Strasse, PLZ und Ort, so wie sie im Vertrag stehen."""
    daten = vertrag.get("contract_data") or {}

    def feld(name):
        return _text(vertrag.get(name) or daten.get(name))

    strasse = feld("seller_address")
    plz_ort = " ".join(x for x in (feld("seller_zip"), feld("seller_city")) if x)
    return ", ".join(x for x in (strasse, plz_ort) if x)


def zahlungsart(vertrag: dict) -> str:
    """Der gewaehlte Weg, in der Form, die im Satz "gegen ___ statt"
    lesbar ist. Freitext alter Vertraege bleibt, wie er ist."""
    daten = vertrag.get("contract_data") or {}
    roh = _text(vertrag.get("payment_method") or daten.get("payment_method"))
    if not roh:
        return ""
    klein = roh.lower()
    # Die drei Werte aus dem Formular lesbar machen; alles andere bleibt.
    if klein == "bar":
        return "Barzahlung"
    if "echtzeit" in klein:
        return "Echtzeitüberweisung"
    if klein in ("überweisung", "ueberweisung", "banküberweisung",
                 "bankueberweisung"):
        return "Banküberweisung"
    return roh


def _euro(wert) -> str:
    try:
        zahl = float(wert)
    except (TypeError, ValueError):
        return ""
    return f"{zahl:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


def marke_modell(vertrag: dict) -> tuple:
    """(Marke, Modell) eines Vertrags.

    Rollenpruefung 22.09.2026 (RP-217/RP-368/RP-432): Zuerst die im Vertrag
    bearbeiteten Werte (vehicle_make/vehicle_model — so heissen sie im
    Dialog und nach einer Korrektur des Fahrers), dann make/model. Vorher
    las die Tabelle NUR make/model: im PDF (dort gibt es nur die vehicle_*-
    Felder) stand "____", und nach einer Korrektur des Fahrers nannte die
    Mail das alte Fahrzeug."""
    daten = (vertrag or {}).get("contract_data") or {}

    def erstes(*namen):
        for name in namen:
            for quelle in (vertrag or {}, daten):
                wert = _text(quelle.get(name))
                if wert:
                    return wert
        return ""

    return erstes("vehicle_make", "make"), erstes("vehicle_model", "model")


def werte(vertrag: dict, firma: Optional[dict] = None,
          sucher: Optional[dict] = None, mit_uhrzeit: bool = True) -> Dict[str, str]:
    """Die Ersetzungstabelle fuer EINEN Vertrag."""
    firma = firma or {}
    sucher = sucher or {}
    daten = vertrag.get("contract_data") or {}

    def aus_vertrag(name):
        return _text(vertrag.get(name) or daten.get(name))

    firmenname = (_text(firma.get("company_name"))
                  or _text(daten.get("dealer_company")))
    marke, modell = marke_modell(vertrag)
    telefon = _text(daten.get("dealer_phone")) or _text(firma.get("phone"))
    mail = (_text(daten.get("dealer_email")) or _text(firma.get("email"))
            or _text(firma.get("contact_email")))
    tabelle = {
        "{haendler_name}": firmenname,
        "{händler_name}": firmenname,
        "{kunde_name}": aus_vertrag("seller_name"),
        "{fahrzeug}": " ".join(x for x in (marke, modell) if x),
        "{marke}": marke,
        "{modell}": modell,
        "{abholdatum}": abholzeitpunkt(vertrag, mit_uhrzeit=mit_uhrzeit),
        "{ort}": uebergabeort(vertrag),
        "{zahlungsart}": zahlungsart(vertrag),
        "{kaufpreis}": _euro(vertrag.get("purchase_price")
                             or daten.get("purchase_price")),
        "{vertragsnummer}": aus_vertrag("contract_no"),
        # Entscheidung Ahmad 22.09.2026 (Rollenpruefung RP-428): NIE die
        # Anmeldenummer des Chefs (kunden_nr) — der Verkaeufer bekam sie im
        # Vertrag genannt. Nur die eigene Vertrags-Kundennummer, beim Vertrag
        # eingefroren, sonst aus der Firma; fehlt sie, bleibt die Luecke.
        # Wunsch Ahmad 26.09.2026 abends: zuerst die je Vertrag selbst
        # vergebene Kundennummer (ContractIn.kundennummer), dann die beim
        # Vertrag eingefrorene Firmen-Kundennummer, sonst die heutige der Firma.
        "{kundennummer}": (_text(daten.get("kundennummer"))
                           or _text(daten.get("vertrags_kundennummer"))
                           or _text(firma.get("vertrags_kundennummer"))),
        "{telefon}": telefon,
        "{email}": mail,
    }
    return tabelle


def ersetzen(text: str, vertrag: dict, firma: Optional[dict] = None,
             sucher: Optional[dict] = None, luecke: str = LUECKE,
             mit_uhrzeit: bool = True) -> str:
    """Alle Platzhalter in `text` ersetzen.

    Fehlt eine Angabe, steht dort `luecke` ("____") — NIE der Platzhalter
    selbst. Ein Kunde darf in seinem Kaufvertrag niemals "{abholdatum}"
    lesen; eine Lücke zum Ausfüllen ist dagegen genau das, was frueher im
    Papiervertrag stand.

    mit_uhrzeit=False (RP-484): {abholdatum} nur als Datum — so im Vertrag.
    """
    if not text:
        return text or ""
    tabelle = werte(vertrag, firma, sucher, mit_uhrzeit=mit_uhrzeit)
    ergebnis = text
    for name, wert in tabelle.items():
        if name in ergebnis:
            ergebnis = ergebnis.replace(name, wert or luecke)
    return ergebnis
