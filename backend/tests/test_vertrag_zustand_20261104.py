# -*- coding: utf-8 -*-
"""Wunsch Ahmad 04.11.2026 (Vertragsdialog): "alles in Ordnung" bei Motor/Getriebe/Kupplung, neuer Punkt
"Nachlackierungsfrei" neben "Unfallfrei"; die Standardwerte (Scheckheft ja, Gewerblich Nein, Fahrtauglich Ja) setzt
der Dialog — der Server nimmt sie wie jede Angabe. Dazu (Entscheidung Ahmad 29.10.2026): KI fuer alle aus —
/api/features traegt "ki", der Marktdaten-Schritt schliesst offene Alarme, wenn die KI aus ist."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pdf_service as P  # noqa: E402
from test_technik_zustand_20261009 import _KOPF, _text  # noqa: E402


def test_01_nachlackierungsfrei_und_alles_in_ordnung_im_pdf():
    from routes.contracts import ContractIn, technik_zustand_normieren
    assert technik_zustand_normieren("alles in Ordnung") == "in Ordnung"
    c = ContractIn(**_KOPF, nachlackierungsfrei="Nein", accident_free="Ja", technik_zustand="alles in Ordnung")
    assert c.nachlackierungsfrei == "Nein" and c.technik_zustand == "in Ordnung"
    assert ContractIn(**_KOPF).nachlackierungsfrei == ""
    dealer, vehicle = {"company_name": "Firma"}, {"make_label": "VW", "model_label": "Golf"}
    f = _text(P.generate_contract_pdf(dealer=dealer, vehicle=vehicle,
                                      contract={**_KOPF, "accident_free": "Ja", "nachlackierungsfrei": "Nein",
                                                "technik_zustand": "in Ordnung"}))
    assert "Unfallfrei Ja" in f and "Nachlackierungsfrei Nein" in f
    assert "Motor/Getriebe/Kupplung alles in Ordnung" in f
    # ohne Angabe (Altvertrag ohne das Feld): keine Zusicherung dazu im Vertrag, nichts bricht
    ohne = _text(P.generate_contract_pdf(dealer=dealer, vehicle=vehicle, contract={**_KOPF, "accident_free": "Nein"}))
    assert "Unfallfrei Nein" in ohne
    assert "Nachlackierungsfrei Ja" not in ohne and "Nachlackierungsfrei Nein" not in ohne


def test_02_features_traegt_ki_und_ist_aus_ohne_schalter(monkeypatch):
    import inspect
    import server
    from ai import provider
    quelle = inspect.getsource(server.features)
    assert '"ki": ki_aktiv()' in quelle
    monkeypatch.setenv("KI_BEWERTUNG_AKTIV", "false")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    assert provider.ki_aktiv() is False
    compose = (Path(__file__).resolve().parents[2] / "docker-compose.yml").read_text(encoding="utf-8")
    assert "KI_BEWERTUNG_AKTIV=${KI_BEWERTUNG_AKTIV:-false}" in compose, "Standard aus (Entscheidung 29.10.2026)"
    assert "KI_MARKTANALYSE_AKTIV=${KI_MARKTANALYSE_AKTIV:-false}" in compose


def test_03_marktdaten_schliesst_alarme_wenn_ki_aus(monkeypatch):
    from ai import marktdaten
    import betrieb
    geschlossen = []

    class _Cursor:
        def __init__(self, docs):
            self._docs = docs

        def limit(self, n):
            return self

        def __aiter__(self):
            self._it = iter(self._docs)
            return self

        async def __anext__(self):
            try:
                return next(self._it)
            except StopIteration:
                raise StopAsyncIteration

    class _Alarme:
        def find(self, filt, proj):
            assert filt["offen"] is True and set(filt["typ"]["$in"]) == set(marktdaten.ALARM_TYPEN)
            return _Cursor([{"typ": "ki_marktdaten_limit", "ref": "2026-10"},
                            {"typ": "ki_marktdaten_fehlgeschlagen", "ref": "aktuell"}])

    class _Db:
        betriebsalarme = _Alarme()

    async def schliessen(db, typ, ref=""):
        geschlossen.append((typ, ref))
        return 1

    monkeypatch.setattr(betrieb, "alarm_schliessen", schliessen)
    monkeypatch.setattr(marktdaten, "aktiv", lambda: False)
    assert asyncio.run(marktdaten.pruefen_und_aktualisieren(_Db())) == {"status": "aus"}
    assert geschlossen == [("ki_marktdaten_limit", "2026-10"), ("ki_marktdaten_fehlgeschlagen", "aktuell")]
