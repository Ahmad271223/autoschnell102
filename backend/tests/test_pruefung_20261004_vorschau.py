# -*- coding: utf-8 -*-
"""Pruefung 04.10.2026 (Nr. 29): POST /contracts/preview erzeugt bei jedem Klick ein PDF (ReportLab, CPU)
und hatte weder Takt noch Grenze. Jetzt: je Konto 30 je Minute (429) und je Prozess hoechstens
VORSCHAU_PARALLEL gleichzeitig; wer zu lange wartet, bekommt 503. Anlegen/neue Fassung bleiben ungebremst.
(Die Textfelder waren entgegen dem Bericht schon begrenzt: 20.000 bzw. 500 Zeichen — hier nur festgehalten.)

In-Prozess mit der Wegwerf-Welt aus test_rp_vertrag_20260922."""
import asyncio
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_rp_vertrag_20260922 import _body, _erwarte, _fahrzeug, _modul, welt  # noqa: E402,F401


def test_29_takt_je_konto(welt, monkeypatch):
    C = _modul("routes.contracts")
    w = welt
    vid = f"v_vs_{w.s}"
    w.run(w.db.vehicles.insert_one(_fahrzeug(w, vid, w.a)))
    erlaubt = {"n": 2}

    async def _check(schluessel):
        assert schluessel == f"konto:{w.a['id']}"
        erlaubt["n"] -= 1
        return erlaubt["n"] >= 0
    monkeypatch.setattr(C._vorschau_limiter, "check", _check)
    for _ in range(2):
        assert w.run(C.preview_contract(_body(C, vid), w.a)).media_type == "application/pdf"
    e = w.run(_erwarte(429, C.preview_contract(_body(C, vid), w.a)))
    assert "Vorschauen" in e.detail


def test_29_hoechstens_zwei_gleichzeitig_sonst_503(welt, monkeypatch):
    C = _modul("routes.contracts")
    w = welt
    vid = f"v_vp_{w.s}"
    w.run(w.db.vehicles.insert_one(_fahrzeug(w, vid, w.a)))

    async def _immer(_s):
        return True
    monkeypatch.setattr(C._vorschau_limiter, "check", _immer)
    monkeypatch.setattr(C, "VORSCHAU_WARTEN_S", 0.3)
    frei = threading.Event()
    gleichzeitig = {"jetzt": 0, "max": 0}
    sperre = threading.Lock()

    def _langsam(*, dealer, vehicle, contract, digital=False):
        with sperre:
            gleichzeitig["jetzt"] += 1
            gleichzeitig["max"] = max(gleichzeitig["max"], gleichzeitig["jetzt"])
        frei.wait(5)
        with sperre:
            gleichzeitig["jetzt"] -= 1
        return b"%PDF-1.4 vorschau"
    monkeypatch.setattr(C, "generate_contract_pdf", _langsam)

    async def lauf():
        aufgaben = [asyncio.ensure_future(C.preview_contract(_body(C, vid), w.a)) for _ in range(3)]
        await asyncio.sleep(0.6)                     # die dritte wartet laenger als VORSCHAU_WARTEN_S
        frei.set()
        return await asyncio.gather(*aufgaben, return_exceptions=True)

    ergebnisse = w.run(lauf())
    ok = [r for r in ergebnisse if not isinstance(r, Exception)]
    fehler = [r for r in ergebnisse if isinstance(r, Exception)]
    assert len(ok) == 2 and len(fehler) == 1 and fehler[0].status_code == 503
    assert gleichzeitig["max"] == 2
    # danach ist die Sperre wieder frei
    frei.set()
    assert w.run(C.preview_contract(_body(C, vid), w.a)).media_type == "application/pdf"


def test_29_textfelder_sind_begrenzt():
    C = _modul("routes.contracts")
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        C.ContractIn(vehicle_id="v", seller_name="V", purchase_price=1, agb_text="x" * 20001)
    with pytest.raises(ValidationError):
        C.ContractIn(vehicle_id="v", seller_name="V", purchase_price=1, seller_phone="1" * 501)
    C.ContractIn(vehicle_id="v", seller_name="V", purchase_price=1, agb_text="x" * 20000)
