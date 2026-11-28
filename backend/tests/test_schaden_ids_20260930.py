# -*- coding: utf-8 -*-
"""Kompletter Lauf 30.09.2026: Ein neuer Schaden OHNE id (Protokoll ueber die API statt ueber die Skizze
der App) liess die KI-Abholbewertung scheitern — "KI hat Position Kratzer Tür vorne links nicht bewertet"
samt Betriebsalarm: die KI erfand eine source_id, der Abgleich verwarf die Position als fremd. Zwei
Schaeden ohne id haetten sich zudem gegenseitig als "doppelt" verdraengt.

Jetzt vergibt der Server beim Speichern fehlende/doppelte ids, und das KI-Paket gibt Altbestand ohne id
eine eindeutige Ersatz-id."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import routes.protocols as P  # noqa: E402
from ai import pickup_assessment as K  # noqa: E402
from ai import schemas  # noqa: E402

KRATZER = {"view": "left", "zone": "Tür vorne links", "x": 520, "y": 480, "type_key": "kratzer",
           "type_label": "Kratzer", "severity_data": {"laenge": "15–30 cm", "tiefe": "bis Grundierung", "anzahl": "einzeln"}}
DELLE = {"view": "right", "zone": "Kotflügel hinten rechts", "x": 1100, "y": 520, "type_key": "delle",
         "type_label": "Delle", "severity_data": {"groesse": "2–5 cm", "lack": "nein", "lage": "Fläche"}}


def test_server_vergibt_fehlende_und_doppelte_ids():
    p = P.ProtocolIn(new_damages=[dict(KRATZER), dict(DELLE), {**KRATZER, "id": "  "},
                                  {**DELLE, "id": "app-1"}, {**KRATZER, "id": "app-1"}])
    ids = [d.id for d in p.new_damages]
    assert all(ids) and len(set(ids)) == 5, ids
    assert ids[3] == "app-1"                                   # die id der App bleibt
    assert all(i.startswith("n-") and len(i) == 14 for i in ids[:3] + ids[4:]), ids
    # so landet es im Entwurf (model_dump wie in save_protocol)
    gespeichert = p.model_dump(exclude_none=True)["new_damages"]
    assert [d["id"] for d in gespeichert] == ids
    # ohne Schaeden bleibt das Feld, wie es war
    assert P.ProtocolIn().new_damages is None and P.ProtocolIn(new_damages=[]).new_damages == []


def test_ki_paket_gibt_altbestand_ohne_id_eindeutige_positionen():
    protokoll = {"id": "p1", "new_damages": [dict(KRATZER), dict(DELLE), {**KRATZER, "zone": "Tür hinten links", "id": "x"},
                                             {**DELLE, "zone": "Haube", "view": "top", "id": "x"}],
                 "vehicle_check": {}, "condition": {}, "documents": {}, "features": {}, "damages_confirmed": True}
    paket = K.paket_bauen(protokoll, {"id": "t1"}, {"make_label": "VW", "model_label": "Golf"},
                          {"contract_data": {"purchase_price": 12500}, "purchase_price": 12500})
    ids = [d["id"] for d in paket["new_damages"]]
    assert ids == ["neu:1", "neu:2", "x", "neu:4"], ids
    erwartet = K._erwartete_positionen(paket)
    assert set(ids) <= set(erwartet) and "" not in erwartet
    # Antwort der KI mit genau diesen source_ids: nichts fehlt, nichts ist fremd oder doppelt
    antwort = {"items": [{"source_id": i} for i in erwartet]}
    _neu, fehlende, doppelte, fremde = schemas.positionen_abgleichen(antwort, list(erwartet))
    assert (fehlende, doppelte, fremde) == ([], [], [])
    # erfindet die KI eine id, fehlt die Position weiter ehrlich (Lauf bricht mit "fehler" ab)
    _neu, fehlende, _d, fremde = schemas.positionen_abgleichen({"items": [{"source_id": "kratzer_1"}]}, list(erwartet))
    assert fremde == ["kratzer_1"] and "neu:1" in fehlende
