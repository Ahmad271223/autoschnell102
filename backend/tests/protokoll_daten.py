# -*- coding: utf-8 -*-
"""Gemeinsame Testdaten fuer ein VOLLSTAENDIG ausgefuelltes Abholprotokoll.

Wunsch Ahmad 14.09.2026: Beim Abschicken zur Freigabe muessen ALLE Abschnitte
beantwortet sein (Fahrzeugdaten, Dokumente je Ja/Nein, Schluessel erhalten
und vereinbart, Zustand komplett, bekannte Schaeden, Ort, Verkaeufer).
Tests, die ein Protokoll ueber submit_protocol schicken, bauen es hiermit."""
from typing import Optional


def vollstaendig(P, *, ausstattung=(), place: str = "Hannover",
                 seller_name: Optional[str] = "Vera", mileage: str = "123456") -> dict:
    """Alle Pflichtabschnitte — `P` ist das Modul routes.protocols."""
    zustand = {}
    for key, _label, opts in P.CONDITION_FIELDS:
        zustand[key] = mileage if key == "mileage" else (
            opts[0] if isinstance(opts, list) else "5/5/4/4")
    doc = {
        "vehicle_check": {k: {"status": o[0]} for k, _l, o in P.VEHICLE_CHECK_FIELDS},
        "documents": {d: (i % 2 == 0) for i, d in enumerate(P.DOCUMENT_ITEMS)},
        "features": {f: True for f in ausstattung},
        "keys_count": "2", "keys_expected": "2",
        "condition": zustand,
        "damages_confirmed": True,
        "place": place,
    }
    if seller_name:
        doc["seller_name"] = seller_name
    return doc
