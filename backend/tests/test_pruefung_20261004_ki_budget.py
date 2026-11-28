# -*- coding: utf-8 -*-
"""Pruefung 04.10.2026 (Nr. 1/24/41): Das KI-Budget ist eine Kostenbremse — ist
es nicht pruefbar (Datenbankfehler genau bei der Reservierung), startet KEINE
KI. Vorher lieferte reservieren() dann {"schluessel": None, "est_ct": 0} und der
Lauf rechnete ohne Firmen- und Fahrer-Deckel. AutoSchnell selbst laeuft weiter;
nur die kostenpflichtige KI meldet "nicht gestartet (keine Kosten)".

Ohne Server: Attrappen-Datenbank und Quelltext der beiden Aufrufer."""
import asyncio
import inspect
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai import budget as B  # noqa: E402


class _Zaehler:
    def __init__(self, kaputt_fuer=()):
        self.ct = {}
        self.kaputt_fuer = kaputt_fuer

    async def update_one(self, filt, upd, upsert=False):
        key = filt["_id"]
        if key in self.kaputt_fuer:
            raise RuntimeError("Datenbank weg")
        if "$inc" in upd:
            self.ct[key] = round(self.ct.get(key, 0.0) + upd["$inc"]["ct"], 2)
        else:
            self.ct.setdefault(key, 0.0)

    async def find_one_and_update(self, filt, upd, return_document=None):
        key = filt["_id"]
        if key in self.kaputt_fuer:
            raise RuntimeError("Datenbank weg")
        self.ct[key] = round(self.ct.get(key, 0.0) + upd["$inc"]["ct"], 2)
        return {"_id": key, "ct": self.ct[key]}


class _Db:
    def __init__(self, zaehler):
        self.z = zaehler

    def __getitem__(self, name):
        assert name == B.ZAEHLER
        return self.z


@pytest.fixture
def grenzen(monkeypatch):
    monkeypatch.setenv("KI_BUDGET_MONAT_EUR", "15")
    monkeypatch.setenv("KI_BUDGET_FAHRER_EUR", "10")
    monkeypatch.setenv("KI_KOSTEN_MAX_CT", "20")


def test_datenbank_weg_heisst_keine_ki(grenzen):
    z = _Zaehler(kaputt_fuer={B._schluessel(None, "d1", "abholung")})
    with pytest.raises(B.BudgetNichtPruefbar):
        asyncio.run(B.reservieren(user_id=None, dealer_id="d1", art="abholung", db=_Db(z)))


def test_fahrer_schritt_scheitert_firmen_reservierung_wird_zurueckgenommen(grenzen):
    firma = B._schluessel(None, "d1", "abholung")
    z = _Zaehler(kaputt_fuer={B._fahrer_schluessel("f1")})
    with pytest.raises(B.BudgetNichtPruefbar):
        asyncio.run(B.reservieren(user_id=None, dealer_id="d1", art="abholung", driver_id="f1", db=_Db(z)))
    assert z.ct[firma] == 0.0, "keine verwaiste Firmen-Reservierung"


def test_ohne_grenze_bleibt_es_offen_ohne_datenbank(monkeypatch):
    monkeypatch.setenv("KI_BUDGET_MONAT_EUR", "0")
    monkeypatch.setenv("KI_BUDGET_FAHRER_EUR", "0")
    r = asyncio.run(B.reservieren(user_id="u", dealer_id="d1", art="vertrag", db=_Db(_Zaehler({"*"}))))
    assert r == {"schluessel": None, "est_ct": 0.0}


def test_normalfall_unveraendert(grenzen):
    z = _Zaehler()
    r = asyncio.run(B.reservieren(user_id=None, dealer_id="d1", art="abholung", driver_id="f1", db=_Db(z)))
    assert r["est_ct"] == 20.0 and r["schluessel"] and r["fahrer_schluessel"]


def test_beide_aufrufer_starten_bei_nicht_pruefbarem_budget_keine_ki():
    from ai import damage_pricing, pickup_assessment
    for modul, funktion in ((pickup_assessment, pickup_assessment.bewertung_ausfuehren),
                            (damage_pricing, damage_pricing.bewerten)):
        q = inspect.getsource(funktion)
        stelle = q.split("budget.reservieren(")[1]
        assert stelle.index("except budget.BudgetNichtPruefbar") < stelle.index("est_ct_vermerken"), modul
        block = stelle.split("except budget.BudgetNichtPruefbar")[1][:700]
        assert "GRUND_NICHT_PRUEFBAR" in block and "return _oeffentlich(eintrag)" in block, modul
    assert "keine Kosten" in B.GRUND_NICHT_PRUEFBAR
