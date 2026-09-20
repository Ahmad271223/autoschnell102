# -*- coding: utf-8 -*-
"""Vertragsbedingungen/AGB und Besondere Vereinbarungen im Vertragsdialog
(Wunsch Ahmad 18.09.2026).

Befund: Seit Runde 26 stehen die Vertragsbedingungen in EINEM Feld der
Einstellungen (`digital_vertragstext`). Der Vertragsdialog zeigte aber weiter
nur das alte, seitdem leere Feld `default_terms` — der Text wurde still ins
PDF uebernommen, war im Dialog aber nirgends zu sehen ("aktuell werden die da
nicht angezeigt aber uebernommen"). Jetzt:

  1  Der Dialog zeigt den Text aus den Einstellungen (bzw. den Standardtext).
  2  Wer will, ueberarbeitet ihn dort fuer DIESEN einen Vertrag.
  3  Leer gelassen gilt weiter der Text aus den Einstellungen.
  4  Dasselbe fuer "Besondere Vereinbarungen".
"""
import inspect
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import routes.contracts as C  # noqa: E402

SRC = Path(__file__).resolve().parents[2] / "frontend" / "src"
DIALOG = (SRC / "components" / "ContractDialog.jsx").read_text(encoding="utf-8")


PFLICHT = {"vehicle_id": "v1", "seller_name": "Max Verkaeufer", "purchase_price": 10000}


def test_01_eingabemodell_kennt_den_vertragstext():
    """Ohne Feld im Modell wirft Pydantic den Text still weg."""
    assert "digital_vertragstext" in C.ContractIn.model_fields
    m = C.ContractIn(**PFLICHT, digital_vertragstext="Nur fuer diesen Vertrag")
    assert m.digital_vertragstext == "Nur fuer diesen Vertrag"
    # Nicht mitgeschickt = None (dann gilt der Text aus den Einstellungen).
    assert C.ContractIn(**PFLICHT).digital_vertragstext is None


def test_02_langer_text_ist_erlaubt_aber_gedeckelt():
    """Vertragsbedingungen sind ein Freitextblock wie AGB — 20.000 Zeichen."""
    assert C.ContractIn(**PFLICHT, digital_vertragstext="x" * 19_000)
    with pytest.raises(Exception):
        C.ContractIn(**PFLICHT, digital_vertragstext="x" * 20_001)


@pytest.mark.parametrize("funktion", ["create_contract", "preview_contract"])
def test_03_eigener_text_hat_vorrang_leer_bleibt_die_einstellung(funktion):
    q = inspect.getsource(getattr(C, funktion))
    assert 'eigener_text = (contract_dict.get("digital_vertragstext") or "").strip()' in q, funktion
    assert "eigener_text or digitaler_vertragstext(dealer)" in q, funktion


def test_04_dialog_zeigt_die_bedingungen_und_schickt_sie_mit():
    # Vorbelegung: gespeicherter Text, sonst der Standardtext aus den Einstellungen
    assert 'digital_vertragstext: (dealer?.digital_vertragstext || "").trim()' in DIALOG
    assert "dealer?.digital_vertragstext_standard" in DIALOG
    # Sichtbares Feld mit Hinweis, dass Aenderungen nur diesen Vertrag betreffen
    assert 'testid="contract-vertragsbedingungen"' in DIALOG
    assert "Vertragsbedingungen & AGB" in DIALOG
    assert "nur für diesen einen Vertrag" in DIALOG
    # buildPayload verteilt das ganze Formular -> das Feld geht mit raus
    assert "...form," in DIALOG


def test_05_nachtragen_wenn_die_einstellungen_spaeter_kommen():
    """Beim Neuladen der Seite ist `dealer` erst nach dem ersten Rendern da —
    dann muessen die Felder nachtraeglich gefuellt werden, aber nur solange
    sie leer und unberuehrt sind."""
    assert "beruehrt.current[feld]" in DIALOG
    # 20.09.2026: Vorbelegt wird mit der WIRKSAMEN Fassung — unser
    # Standardsatz (falls eingeschaltet) plus dem eigenen Text der Firma.
    # Vorher stand hier nur das Freitextfeld; seit dem Schalter waere unser
    # Satz beim Anlegen eines Vertrags verloren gegangen.
    assert "additional_terms: dealer.sondervereinbarungen_effektiv" in DIALOG
    assert "}, [dealer]);" in DIALOG
    # Die Hooks stehen VOR dem fruehen Ausstieg (React-Regel).
    assert DIALOG.index("}, [dealer]);") < DIALOG.index("if (!open) return null;")


def test_06_besondere_vereinbarungen_bleiben_vorbelegt():
    assert 'testid="contract-terms"' in DIALOG
    assert "Aus deinen Einstellungen vorausgefüllt" in DIALOG
    q = inspect.getsource(C.create_contract)
    # 20.09.2026 (Wunsch Ahmad): nicht mehr nur das Freitextfeld, sondern
    # Standardsatz UND eigener Text — zusammengesetzt in einer Stelle.
    assert "_vorlagen.sondervereinbarungen(dealer)" in q, (
        "die Vertragsanlage nimmt nicht die wirksame Fassung")
    assert 'dealer.get("default_special_agreements", "")' not in q, (
        "es wird weiter nur das Freitextfeld kopiert — der Schalter waere "
        "dort wirkungslos")
