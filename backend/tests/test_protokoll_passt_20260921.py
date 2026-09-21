# -*- coding: utf-8 -*-
"""Pruefbericht 20.09.2026, V-13 / R1-10 (21.09.2026).

Mit eingeteiltem Fahrer entsteht "abgeholt"/"erledigt" nur ueber ein finales
Protokoll. Bisher genuegte IRGENDEIN altes finales Protokoll: nach dem
Wiederoeffnen liessen sich Fahrer, Fahrzeug oder Vertrag tauschen und der
Termin mit dem Beleg der ALTEN Abholung wieder schliessen. Jetzt muss das
Protokoll zu dem Stand passen, der nach dem Update gilt.

In-Prozess mit der Welt aus test_befunde_runde17_termine.
"""
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tests"))

from test_befunde_runde17_termine import _module, _put, welt  # noqa: E402,F401


def _termin_mit_protokoll(welt, name, *, termin_extra=None, proto_extra=None):
    P = _module("routes.protocols")
    w, db = welt.w, welt.db
    aid, pid = f"a_{name}_{w.s}", f"p_{name}_{w.s}"
    termin = w.appt(aid, **{"driver_id": w.driver_id, "zuteilung": "angenommen",
                            "vehicle_id": f"v1_{w.s}", **(termin_extra or {})})
    proto = w.entwurf(P, aid, pid, status="final", vehicle_id=f"v1_{w.s}",
                      finalized_at="2026-09-20T10:00:00+00:00", **(proto_extra or {}))

    async def lauf():
        await db.appointments.insert_one(termin)
        await db.pickup_protocols.insert_one(proto)
    welt.run(lauf())
    return aid


@pytest.fixture
def mit_fahrer(welt):
    welt.run(welt.w.fahrer_anlegen(welt.db))
    return welt


def test_passendes_protokoll_schliesst_wieder(mit_fahrer):
    A = _module("routes.appointments")
    aid = _termin_mit_protokoll(mit_fahrer, "ok")
    r = mit_fahrer.run(_put(A, aid, mit_fahrer.w.chef, status="abgeholt"))
    assert r["ok"] is True
    t = mit_fahrer.run(mit_fahrer.db.appointments.find_one({"id": aid}, {"_id": 0}))
    assert t["status"] == "abgeholt"


@pytest.mark.parametrize("was,termin_extra,proto_extra", [
    ("Fahrzeug", {"vehicle_id": "v_anderes"}, None),
    ("Fahrer", None, {"driver_account_id": "f_jemand_anders"}),
    ("Vertrag", {"contract_id": "c_neu"}, {"contract_id": "c_alt"}),
])
def test_protokoll_eines_anderen_stands_schliesst_nicht(mit_fahrer, was, termin_extra, proto_extra):
    A = _module("routes.appointments")
    aid = _termin_mit_protokoll(mit_fahrer, was.lower(), termin_extra=termin_extra,
                                proto_extra=proto_extra)
    with pytest.raises(HTTPException) as e:
        mit_fahrer.run(_put(A, aid, mit_fahrer.w.chef, status="abgeholt"))
    assert e.value.status_code == 409 and was in e.value.detail and "Korrektur" in e.value.detail
    t = mit_fahrer.run(mit_fahrer.db.appointments.find_one({"id": aid}, {"_id": 0}))
    assert t["status"] == "offen", "nichts geschrieben"
