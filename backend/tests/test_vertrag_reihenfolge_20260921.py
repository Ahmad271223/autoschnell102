# -*- coding: utf-8 -*-
"""Reihenfolge am Vertragsende (Wunsch Ahmad 21.09.2026).

"Besondere Vereinbarungen", danach die AGB, danach die Unterschrift — diese
drei Teile stehen immer ZULETZT im Vertrag, in der Druck- und in der
digitalen Fassung. Fahrzeugbeschreibung und der Gewaehrleistungs-Absatz
stehen davor (vorher standen sie zwischen Besonderen Vereinbarungen und AGB).

Dazu: "Notizen (intern)" (contract.notes, nur der Chef traegt sie ein) stehen
gar nicht mehr im Vertrag — beide Fassungen gehen an den Verkaeufer.

Und: Eine Ueberschrift am Vertragsende haengt nie allein unten auf einer
Seite — der erste Absatz steht immer auf derselben Seite.

Laeuft ohne Server (pdf_service direkt, Text per pypdf).
"""
import io
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
PROJEKT = BACKEND.parent

import vertrag_vorlagen as V  # noqa: E402
from pdf_service import VERTRAGSTEXT_START, generate_contract_pdf  # noqa: E402

FIRMA = {"company_name": "Autohaus Reihenfolge GmbH", "address": "Hauptstr. 1",
         "zip_code": "10115", "city": "Berlin", "phone": "030 123",
         "email": "info@reihenfolge.test", "kunden_nr": "4711"}
FAHRZEUG = {"make_label": "BMW", "model_label": "320d",
            "features": ["Navigationssystem", "Klimaautomatik", "Sitzheizung", "Tempomat"]}
NOTIZ = "CHEF-NOTIZ-7731 Verkaeufer handelt noch 500 runter"
VERTRAG = {
    "seller_name": "Max Muster", "seller_address": "Musterweg 5",
    "seller_zip": "20095", "seller_city": "Hamburg", "purchase_price": 12500,
    "contract_no": "KV-REIHE", "pickup_date": "2026-09-25", "payment_method": "Bar",
    "hu_valid": "ja", "hu_until": "05/2027", "accident_free": "ja",
    "damages_text": "Kratzer Tür links\nDelle Heckklappe",
    "vehicle_description": "Scheckheft vorhanden.\n\nNichtraucherfahrzeug.",
    "additional_terms": (V.sondervereinbarungen({})
                         + "\n\n• Eigene Regel: Schlüssel liegen im Handschuhfach."),
    "agb_text": "AGB-ALT Absatz eins.\n\nAGB-ALT Absatz zwei.",
    "digital_vertragstext": VERTRAGSTEXT_START,
    "notes": NOTIZ,
}

# Suchbegriffe: "Gewährleistung:" MIT Doppelpunkt (Klausel 1 der
# Vertragsbedingungen enthaelt das Wort auch), der Gueltigkeitssatz
# vollstaendig (Klausel 4 enthaelt "auch ohne Unterschrift gültig").
# Wunsch Ahmad 04.10.2026: kuerzere Ueberschriften (vorher "Ausstattung laut Inserat / Verkäuferangaben",
# "Fahrzeugbeschreibung (vom Inserat)", "2 · Zusicherungen & Zustand")
# Die Hinweiszeile "Ausstattung laut Inseratsangaben." ist seit 09.10.2026 weg (Wunsch Ahmad) — fuer Reihenfolge
# und "genau einmal" zaehlt die erste Ausstattung des Testfahrzeugs (steht nur in diesem Abschnitt).
AUSSTATTUNG = "Navigationssystem"
BESCHREIBUNG = "Fahrzeugbeschreibung"      # Wunsch Ahmad 09.10.2026: wieder die lange Ueberschrift
GEWAEHR = "Gewährleistung:"
BESONDERE = "Besondere Vereinbarungen"
AGB_ALT = "Allgemeine Geschäftsbedingungen"
AVB = "Allgemeine Vertragsbedingungen"
UNTERSCHRIFTEN = "Unterschriften"
GUELTIG = "Dieser Vertrag ist ohne Unterschrift gültig."


def _flach(s: str) -> str:
    return " ".join((s or "").split())


def _seiten(pdf: bytes):
    from pypdf import PdfReader
    return [_flach(p.extract_text() or "") for p in PdfReader(io.BytesIO(pdf)).pages]


def _pdf(digital=False, **vertrag) -> bytes:
    c = dict(VERTRAG, **vertrag)
    return generate_contract_pdf(dealer=dict(FIRMA), vehicle=dict(FAHRZEUG),
                                 contract=c, digital=digital)


def _text(digital=False, **vertrag) -> str:
    return " ".join(_seiten(_pdf(digital=digital, **vertrag)))


def _schluss(digital):
    """Letzter Teil: Druck = Abschnitt Unterschriften; digital = die Vertragsbedingungen selbst
    (Wunsch Ahmad 01.10.2026: der Gueltigkeitssatz am Ende entfaellt)."""
    return AVB if digital else UNTERSCHRIFTEN


# ------------------------------------------------------------ Reihenfolge
@pytest.mark.parametrize("digital", [False, True], ids=["druck", "digital"])
def test_01_reihenfolge_mit_allen_teilen(digital):
    f = _text(digital=digital)
    folge = [AUSSTATTUNG, BESCHREIBUNG, GEWAEHR, BESONDERE, AGB_ALT, AVB] + ([] if digital else [UNTERSCHRIFTEN])
    assert GUELTIG not in f, "der Schlusssatz ist seit dem 01.10.2026 weg"
    for teil in folge:
        assert f.count(teil) == 1, f"{teil!r} steht {f.count(teil)}x im Vertrag"
    pos = [f.index(teil) for teil in folge]
    assert pos == sorted(pos), (
        "falsche Reihenfolge: " + " -> ".join(t for _, t in sorted(zip(pos, folge))))


@pytest.mark.parametrize("digital", [False, True], ids=["druck", "digital"])
def test_02_die_letzten_drei_teile(digital):
    """Hinter "Besondere Vereinbarungen" kommen nur noch AGB und Unterschrift."""
    f = _text(digital=digital)
    hinten = f[f.index(BESONDERE):]
    for frueher in (AUSSTATTUNG, BESCHREIBUNG, GEWAEHR, "Schäden / Beschädigungen",
                    "2 · Zustand", "1 · Fahrzeugdaten",
                    "KAUFPREIS (VEREINBART)", "Notizen"):
        assert frueher not in hinten, f"{frueher!r} steht hinter den Besonderen Vereinbarungen"
    assert hinten.index(BESONDERE) < hinten.index("Allgemeine") < hinten.index(_schluss(digital))
    # Inhalt der Besonderen Vereinbarungen steht vor den AGB (Platzhalter ersetzt).
    assert hinten.index("Die Fahrzeugübergabe findet bis/am 25.09.2026") \
        < hinten.index("Eigene Regel: Schlüssel liegen im Handschuhfach") < hinten.index(AGB_ALT)
    # Nach dem Schlussteil folgt kein weiterer Abschnitt mehr.
    danach = f[f.index(_schluss(digital)) + len(_schluss(digital)):]
    for abschnitt in (BESONDERE, AGB_ALT, AVB, GEWAEHR, BESCHREIBUNG):
        assert abschnitt not in danach, f"{abschnitt!r} steht hinter der Unterschrift"
    if digital:
        assert UNTERSCHRIFTEN not in f, "die digitale Fassung hat keinen Abschnitt Unterschriften"
        assert "Mit ihrer Unterschrift" not in f
    else:
        # Wunsch Ahmad 09.10.2026: unter den Unterschriften steht kein Satz mehr
        assert "Mit ihrer Unterschrift bestätigen beide Parteien" not in f


@pytest.mark.parametrize("digital", [False, True], ids=["druck", "digital"])
def test_03_reihenfolge_mit_teilweise_leeren_teilen(digital):
    """Fehlen Ausstattung, Beschreibung, alte AGB oder Vereinbarungen, bleibt
    die Reihenfolge der vorhandenen Teile gleich — ohne Nummern."""
    ohne = dict(agb_text="", vehicle_description="", damages_text="")
    f = _text(digital=digital, **ohne)
    assert AGB_ALT not in f and BESCHREIBUNG not in f
    assert f.index(GEWAEHR) < f.index(BESONDERE) < f.index(AVB) <= f.index(_schluss(digital))
    f = _text(digital=digital, additional_terms="", **ohne)
    assert BESONDERE not in f
    assert f.index(GEWAEHR) < f.index(AVB) <= f.index(_schluss(digital))
    for text in (f, _text(digital=digital)):
        assert "3 · " not in text and "4 · " not in text, "neue Nummern am Vertragsende"


# -------------------------------------------------------- Notizen (intern)
@pytest.mark.parametrize("digital", [False, True], ids=["druck", "digital"])
def test_04_interne_notizen_stehen_nicht_im_vertrag(digital):
    f = _text(digital=digital)
    assert "Notizen" not in f, "interne Notizen stehen im Kundenvertrag"
    assert "CHEF-NOTIZ-7731" not in f and "500 runter" not in f


def test_05_quelle_liest_die_notizen_nicht_mehr():
    quelle = (BACKEND / "pdf_service.py").read_text(encoding="utf-8")
    code = "\n".join(z.split("#", 1)[0] for z in quelle.splitlines())
    assert 'contract.get("notes")' not in code
    assert "Notizen (intern)" not in code


def test_06_dialog_sagt_dass_notizen_nicht_im_vertrag_stehen():
    """Wunsch Ahmad 09.10.2026: das Notiz-Feld steht nicht mehr im Vertragsdialog (Notizen gehoeren an den Termin);
    eine vorhandene Notiz bleibt im Vertragsarchiv sichtbar."""
    jsx = (PROJEKT / "frontend" / "src" / "components"
           / "ContractDialog.jsx").read_text(encoding="utf-8")
    assert 'label="Notizen (intern)"' not in jsx and 'label="Abholuhrzeit' not in jsx
    assert 'testid="contract-pickup-date"' in jsx, "das Abholdatum bleibt"
    # ... im Archiv wird eine vorhandene Notiz weiter angezeigt
    archiv = (PROJEKT / "frontend" / "src" / "pages" / "app"
              / "PDFArchiv.jsx").read_text(encoding="utf-8")
    assert "it.contract_data?.notes" in archiv and "Notiz (intern)" in archiv


# --------------------------------------- Ueberschrift nie allein am Seitenende
def test_07_ueberschrift_haengt_nie_allein_unten():
    """Die Fahrzeugbeschreibung waechst Zeile fuer Zeile (je 14 pt), damit die
    Ueberschriften am Vertragsende einmal ueber das Seitenende wandern. Auf
    der Seite mit der Ueberschrift muss immer auch ihr erster Absatz stehen."""
    erster_absatz = {
        BESONDERE: "Die Fahrzeugübergabe findet",
        AGB_ALT: "AGB-ALT Absatz eins.",
        AVB: "Folgende Vertragsbedingungen werden",
    }
    seitenwechsel = set()
    for n in range(0, 60):
        beschreibung = "\n\n".join(f"Beschreibungszeile {i}." for i in range(n))
        seiten = _seiten(_pdf(vehicle_description=beschreibung))
        for kopf, anfang in erster_absatz.items():
            nr = next(i for i, s in enumerate(seiten) if kopf in s)
            assert anfang in seiten[nr], (
                f"n={n}: {kopf!r} steht allein unten auf Seite {nr + 1}")
            seitenwechsel.add((kopf, nr))
    # Gegenprobe: die Ueberschriften sind dabei wirklich auf eine andere
    # Seite gewandert — sonst haette der Test nichts geprueft.
    for kopf in erster_absatz:
        assert len({nr for k, nr in seitenwechsel if k == kopf}) >= 2, kopf
