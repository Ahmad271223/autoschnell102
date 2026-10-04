# -*- coding: utf-8 -*-
"""Entscheidung Ahmad 04.10.2026 (Pruefung Nr. 37): Einen bestehenden Kaufvertrag aendern (neue Fassung,
Verkaeuferkorrektur) darf ein SUCHER nur mit aktivem Abo — ohne Abo nur ansehen und herunterladen. Der Chef
bleibt frei (kostenlos fuers Verwalten). Vorher reichte current_firma: mit abgelaufenem Abo liessen sich neue
Fassungen anlegen (versenden aber nicht). Ohne Server: Abhaengigkeit direkt + Routen-Verdrahtung."""
import asyncio
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import deps  # noqa: E402


def _mit_abo(monkeypatch, aktiv):
    async def _sub(user):
        return {"active": aktiv}
    monkeypatch.setattr(deps, "subscription_for", _sub)


def test_37_sucher_ohne_abo_darf_nicht_aendern(monkeypatch):
    _mit_abo(monkeypatch, False)
    with pytest.raises(HTTPException) as e:
        asyncio.run(deps.aendern_braucht_abo({"id": "s1", "role": "sucher", "dealer_id": "d1"}))
    assert e.value.status_code == 402 and "ansehen und herunterladen" in e.value.detail


def test_37_sucher_mit_abo_und_chef_ohne_abo_duerfen(monkeypatch):
    _mit_abo(monkeypatch, True)
    s = {"id": "s1", "role": "sucher", "dealer_id": "d1"}
    assert asyncio.run(deps.aendern_braucht_abo(s)) is s
    _mit_abo(monkeypatch, False)
    chef = {"id": "c1", "role": "dealer", "dealer_id": "d1"}
    assert asyncio.run(deps.aendern_braucht_abo(chef)) is chef, "der Chef verwaltet kostenlos"


def test_37_beide_aenderungswege_sind_verdrahtet():
    import routes.contracts as C
    for pfad in ("/contracts/{contract_id}/neue-fassung", "/contracts/{contract_id}/verkaeufer"):
        namen = {d.call.__name__ for r in C.router.routes if getattr(r, "path", "") == pfad
                 for d in r.dependant.dependencies}
        assert namen == {"aendern_braucht_abo"}, (pfad, namen)
    # Ansehen/Herunterladen bleiben ohne Abo
    namen = {d.call.__name__ for r in C.router.routes if getattr(r, "path", "") == "/contracts/{contract_id}/pdf"
             for d in r.dependant.dependencies}
    assert "current_firma" in namen and "aendern_braucht_abo" not in namen


def test_37_oberflaeche_graut_aendern_aus():
    front = Path(__file__).resolve().parents[2] / "frontend" / "src"
    archiv = (front / "pages" / "app" / "PDFArchiv.jsx").read_text(encoding="utf-8")
    aktionen = (front / "components" / "VertragAktionen.jsx").read_text(encoding="utf-8")
    assert 'auth.user?.role === "sucher" && !auth.subscription?.active' in archiv and "aendernOhneAbo," in archiv
    assert aktionen.count("disabled={!!zustand.aendernOhneAbo}") == 2 and "Nur mit aktivem Abo" in aktionen
