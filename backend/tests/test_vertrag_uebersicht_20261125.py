# -*- coding: utf-8 -*-
"""Wunsch Ahmad 25.11.2026: drittes Vertragslayout "uebersicht" nach seiner Vorlage.

Käufer links, Verkäufer rechts mit "KdNr:", dann "Kaufpreis", EIN Raster "Fahrzeugdaten", Ausstattung,
Beschreibung, Vereinbarungen, Bedingungen. Die bisherigen Layouts bleiben wählbar. Bedingung: es darf NIE eine
Angabe verloren gehen — jedes Wort aus "modern" steht auch in "uebersicht", ausser den umbenannten Beschriftungen.
"""
import io
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pdf_service as P  # noqa: E402
import routes.contracts as C  # noqa: E402
import routes.dealer as D  # noqa: E402

FIRMA = {"company_name": "Autohaus Beispiel GmbH", "address": "Musterstraße 12", "zip_code": "12345",
         "city": "Musterstadt", "phone": "030 5550100", "email": "info@autohaus-beispiel.example"}
FAHRZEUG = {"make_label": "Seat", "model_label": "Ateca 1.4 EcoTSI Xcellence", "first_registration": "11/2016",
            "mileage": 91000, "color": "Grau Metallic", "power_kw": 110, "power_ps": 150, "displacement": 1395,
            "gearbox_label": "Schaltgetriebe", "fuel_label": "Benzin", "category_label": "SUV", "doors": "4/5",
            "seats": 5, "vin": "VSSZZZ5FZHR012345", "previous_owners": 2,
            "features": ["Navigationssystem", "Sitzheizung", "Rückfahrkamera", "Tempomat"],
            "accident_damaged": True, "roadworthy": False}
VERTRAG = {"seller_name": "Max Mustermann", "seller_address": "Beispielweg 4", "seller_zip": "12345",
           "seller_city": "Musterstadt", "seller_phone": "0151 0000000", "seller_email": "max@example.de",
           "id_document": "L01X00T47", "purchase_price": 11000, "contract_no": "KV-UEB-1",
           "vertrags_kundennummer": "25304", "payment_method": "Überweisung", "pickup_date": "2026-10-02",
           "show_vat": True,
           "additional_terms": "• Die Fahrzeugübergabe findet bis/am {abholdatum} in {ort} statt.\n• Zweiter Punkt.",
           "hu_valid": "ja", "hu_until": "2027-06", "accident_free": "nein", "accident_location": "Stoßstange hinten",
           "service_book": "ja", "eu_import": "Nein", "commercial_since_ez": "Nein", "drivable": "Ja",
           "nachlackierungsfrei": "ja", "technik_zustand": "Schaden vorhanden", "technik_schaden_text": "Kupplung rutscht",
           "tires": "Sommer und Winter", "zulassung": "angemeldet",
           "damages_text": "Delle – Tür vorne links\nKratzer – Heckklappe", "vehicle_damage_note": "Steinschlag Scheibe",
           "sonstige_vereinbarung": "8-fach bereift", "vehicle_description": "Fahrzeug aus zweiter Hand.\n\n• Nichtraucher",
           "agb_text": "AGB-Punkt Eins.", "digital_vertragstext": P.DIGITAL_VERTRAGSTEXT_STANDARD}

#: Woerter (klein geschrieben), die NUR in den alten Beschriftungen/Ueberschriften stehen (UEBERSICHT_BESCHRIFTUNG)
#: oder bewusst wegfallen (Wunsch Ahmad 25.11.2026: keine Vertragsnummer; Preis "11.000 €" statt "11.000,00 EUR";
#: kein Untertitel "für ein gebrauchtes Kraftfahrzeug"). Kein Wert eines Feldes darf hier stehen.
UMBENANNT = {"anschrift", "fahrtauglich", "fahrzeugbeschreibung", "hu/au", "kfz-kaufvertrag", "kategorie",
             "kilometerstand", "leistung", "motor/getriebe/kupplung", "nachlackierungsfrei", "scheckheftgepflegt",
             "seit", "fahrzeughalter", "anzahl", "1", "2", "name", "firma", "hersteller", "typ",
             "datum", "kunden-nr.", "vertrags-nr.", "kv-ueb-1", "00", "eur", "ein", "gebrauchtes", "kraftfahrzeug"}


def _text(pdf: bytes) -> str:
    from pypdf import PdfReader
    return " ".join(" ".join((p.extract_text() or "").split()) for p in PdfReader(io.BytesIO(pdf)).pages)


def _pdf(layout, digital=False, **mehr):
    return P.generate_contract_pdf(dealer=FIRMA, vehicle=FAHRZEUG, contract={**VERTRAG, **mehr, "vertrag_layout": layout},
                                   digital=digital)


def _woerter(t):
    return {w.lower() for w in re.findall(r"[\wÄÖÜäöüß./@+-]+", t)}


def test_01_layout_bekannt_und_waehlbar():
    assert "uebersicht" in P.VERTRAG_LAYOUTS
    assert P.vertrag_layout("Uebersicht") == "uebersicht"
    ok = D.DealerSettingsIn(vertrag_layout="uebersicht")
    assert D._collect_settings_update(ok) == {"vertrag_layout": "uebersicht"}
    assert D.VertragVorschauIn(layout="uebersicht").layout == "uebersicht"
    assert C.vertrag_design_festhalten({}, {"vertrag_layout": "uebersicht"})["vertrag_layout"] == "uebersicht"


def test_02_nichts_geht_verloren_druck_und_digital():
    for digital in (False, True):
        alt, neu = _text(_pdf("modern", digital)), _text(_pdf("uebersicht", digital))
        fehlt = _woerter(alt) - _woerter(neu) - UMBENANNT
        assert not fehlt, f"digital={digital}: {sorted(fehlt)}"
        # Stichproben aller Bereiche
        for teil in ("Max Mustermann", "L01X00T47", "max@example.de", "info@autohaus-beispiel.example",
                     "11.000 €", "Zahlungsart: Überweisung", "VSSZZZ5FZHR012345", "91.000 km",
                     "Kupplung rutscht", "Delle – Tür vorne links", "Steinschlag Scheibe", "Sommer und Winter",
                     "Rückfahrkamera", "Nichtraucher", "8-fach bereift", "Zweiter Punkt", "AGB-Punkt Eins",
                     "Unfallschaden (Inserat)", "Fahrbereit (Inserat)", "Stoßstange hinten"):
            assert teil in neu, (digital, teil)


def test_03_aufbau_wie_vorlage():
    t = _text(_pdf("uebersicht"))
    reihenfolge = ["KFZ Kaufvertrag", "KÄUFER", "VERKÄUFER KdNr: 25304", "KAUFPREIS", "FAHRZEUGDATEN",
                   "Hersteller/Typ", "AUSSTATTUNG", "BESCHREIBUNG", "ZUSÄTZLICHE VEREINBARUNG",
                   "BESONDERE VEREINBARUNGEN", "ALLGEMEINE VERTRAGSBEDINGUNGEN", "UNTERSCHRIFTEN"]
    pos = [t.find(x) for x in reihenfolge]
    assert -1 not in pos, dict(zip(reihenfolge, pos))
    assert pos == sorted(pos), dict(zip(reihenfolge, pos))
    # Wunsch Ahmad 25.11.2026: keine Vertragsnummer, Kundennummer nur EINMAL (beim Verkäufer)
    assert "KV-UEB-1" not in t and "Vertrags-Nr" not in t.replace("VERTRAGS", "")
    assert t.count("25304") == 1 + t.count("Kundennummer 25304")
    for label in ("Name/Firma", "Straße, Nr.", "PLZ, Ort", "Gesamtfahrleistung", "Halter", "PS/KW", "Fahrzeugtyp",
                  "TÜV/AU", "Scheckheft", "Gewerblich genutzt ab EZ", "Uneingeschränkt Fahrbereit", "Motor/Getriebe",
                  "Nachlackierung Nein", "Unfallfrei"):
        assert label in t, label
    assert "1 · Fahrzeugdaten" not in t and "2 · Zusätzliche" not in t
    # keine "ohne Unterschrift"-Saetze ausser dem Standardtext der Firma (wie in den anderen Layouts)
    assert t.count("ohne Unterschrift") == _text(_pdf("modern")).count("ohne Unterschrift")


def test_04_nachlackierung_umgedreht_und_unbekanntes_bleibt():
    z = P.uebersicht_zeilen([("Nachlackierungsfrei", "Ja"), ("Neu erfunden", "X"), ("Hersteller / Typ", "Seat")])
    assert z == [("Hersteller/Typ", "Seat"), ("Nachlackierung", "Nein"), ("Neu erfunden", "X")]
    assert P.uebersicht_zeilen([("Nachlackierungsfrei", "Nein")]) == [("Nachlackierung", "Ja")]
    # kein Ja/Nein: alte Beschriftung, nichts umgedreht
    assert P.uebersicht_zeilen([("Nachlackierungsfrei", "teilweise")]) == [("Nachlackierungsfrei", "teilweise")]
    assert "Nachlackierung Ja" in _text(_pdf("uebersicht", nachlackierungsfrei="nein"))


def test_05_andere_layouts_unveraendert():
    t = _text(_pdf("modern"))
    assert "KFZ-KAUFVERTRAG" in t and "1 · Fahrzeugdaten" in t and "Kilometerstand" in t and "KdNr" not in t
    f = _text(_pdf("formular"))
    assert "1 · FAHRZEUGDATEN" in f and "KdNr" not in f


KURZ = {"vehicle_description": "Fahrzeug aus zweiter Hand, zwei Schlüssel vorhanden. Scheckheftgepflegt.",
        "show_vat": False, "agb_text": "", "sonstige_vereinbarung": "", "technik_zustand": "in Ordnung",
        "damages_text": "", "vehicle_damage_note": "", "accident_free": "ja"}


def _seiten(pdf):
    from pypdf import PdfReader
    return len(PdfReader(io.BytesIO(pdf)).pages)


def test_06_kurze_beschreibung_eine_seite_ohne_verlust():
    """Wunsch Ahmad 25.11.2026: bei 1–2 Sätzen Beschreibung möglichst alles auf EINER Seite."""
    eng = _pdf("uebersicht", **KURZ)
    assert _seiten(eng) == 1
    assert _seiten(_pdf("modern", **KURZ)) == 2          # zum Vergleich: das bisherige Layout braucht zwei
    t = _text(eng)
    fehlt = _woerter(_text(_pdf("modern", **KURZ))) - _woerter(t) - UMBENANNT
    assert not fehlt, sorted(fehlt)
    for teil in ("UNTERSCHRIFTEN", "Datum und Ort", "Zulassungsbescheinigung", "VSSZZZ5FZHR012345",
                 "ALLGEMEINE VERTRAGSBEDINGUNGEN", "KAUFPREIS 11.000 €"):
        assert teil in t, teil


def test_07_zu_lang_fuer_eine_seite_bleibt_normal():
    """Passt es auch eng nicht auf eine Seite, kommt die normale (nicht verkleinerte) Fassung."""
    lang = _pdf("uebersicht", **{**KURZ, "vehicle_description": "Langer Text über das Fahrzeug. " * 120})
    assert _seiten(lang) >= 2
    from pypdf import PdfReader
    erste = PdfReader(io.BytesIO(lang)).pages[0].extract_text()
    assert "Ausweis" in erste                # alles da, nur auf mehr Seiten
    import pdf_service as P2
    assert P2._GESTALTUNG.get() is None      # Gestaltung zurueckgesetzt
