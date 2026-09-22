# -*- coding: utf-8 -*-
"""Rollenprüfung 22.09.2026 — Team termine_protokoll, Welle 3 (restliche
Übergaben in Termin-Dateien).

In-Prozess gegen DB_NAME (Welt aus test_golive_20260913_abschluss wie in
test_rp_termine_protokoll_20260922.py). Die Welt räumt alle Termine,
Fahrzeuge usw. ihrer Firma selbst auf; Termine einer fremden Firma legt der
Test selbst an und löscht sie im finally.

  RP-464  GET /appointments/fahrer-abgelehnt/anzahl — Zähler "vom Fahrer
          abgelehnt" für die Seitenleiste (lib/abgelehntZaehler.js)
"""
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from starlette.routing import Match

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_golive_20260913_abschluss import (  # noqa: E402,F401  (welt = Fixture)
    _abholung, _doc, _jetzt, _m, welt)
from test_rp_termine_protokoll_20260922 import _put  # noqa: E402

PFAD = "/appointments/fahrer-abgelehnt/anzahl"


def _treffer(router, methode, pfad):
    """Erste Route, die FastAPI fuer methode+pfad nimmt (Reihenfolge wie im Router)."""
    scope = {"type": "http", "method": methode, "path": pfad, "root_path": ""}
    for r in router.routes:
        passt, _ = r.matches(scope)
        if passt == Match.FULL:
            return r
    return None


# ======================================================================= RP-464
def test_rp464_route_ist_eindeutig_und_nur_fuer_den_hauptchef():
    A = _m("routes.appointments")
    deps = _m("deps")
    route = _treffer(A.router, "GET", PFAD)
    assert route is not None and route.endpoint is A.fahrer_abgelehnt_anzahl, \
        "darf nicht als GET /appointments/{appt_id}(/report) landen"
    # Abhaengigkeit: current_chef (Hauptchef), nicht current_firma
    assert [d.call for d in route.dependant.dependencies] == [deps.current_chef]
    # Die bestehenden Routen bleiben erreichbar
    assert _treffer(A.router, "GET", "/appointments/abc").endpoint is A.get_appointment
    assert _treffer(A.router, "GET", "/appointments/abc/report").endpoint is A.get_pickup_report
    # Frontend und Backend nennen denselben Pfad
    js = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib"
          / "abgelehntZaehler.js").read_text(encoding="utf-8")
    assert f'ABGELEHNT_PFAD = "{PFAD}"' in js


def test_rp464_zaehlt_nur_offene_abgelehnte_ohne_fahrer_der_eigenen_firma(welt):
    w = welt
    A = _m("routes.appointments")
    D = _m("routes.drivers")
    deps = _m("deps")
    fremd = f"d_fremd464_{w.s}"

    def zahl():
        return w.run(A.fahrer_abgelehnt_anzahl(w.chef))["anzahl"]

    def termin(name, dealer_id=None, **felder):
        doc = {"id": f"t464_{name}_{w.s}", "dealer_id": dealer_id or w.dealer_id,
               "status": "offen", "pickup_date": "2099-09-11", "created_at": _jetzt(), **felder}
        w.run(w.db.appointments.insert_one(doc))
        return doc["id"]

    try:
        assert zahl() == 0
        # zaehlt: abgelehnt, driver_id fehlt (so hinterlaesst drivers.py den Termin)
        termin("fehlt", zuteilung="abgelehnt")
        # zaehlt: abgelehnt, driver_id leer, Status fehlt (Altbestand = offen)
        termin("leer", zuteilung="abgelehnt", driver_id="", status=None)
        # zaehlt: abgelehnt, verschoben (noch offen)
        termin("verschoben", zuteilung="abgelehnt", status="verschoben")
        assert zahl() == 3
        # zaehlt NICHT: schon wieder ein Fahrer eingetragen
        termin("neu_zugeteilt", zuteilung="abgelehnt", driver_id=w.driver2["id"])
        # zaehlt NICHT: Termin abgeschlossen
        for st in ("storniert", "nicht abgeholt", "abgeholt", "erledigt"):
            termin(f"zu_{st.replace(' ', '_')}", zuteilung="abgelehnt", status=st)
        # zaehlt NICHT: andere Zuteilung ohne Fahrer
        termin("offen_ohne", zuteilung="offen")
        termin("ohne_zuteilung")
        # zaehlt NICHT: fremde Firma
        termin("fremd", dealer_id=fremd, zuteilung="abgelehnt")
        assert zahl() == 3

        # Echter Weg: Fahrer lehnt ab -> +1; Chef teilt neu zu -> wieder weg
        t = _abholung(w, proto_status="entwurf")
        w.run(w.db.appointments.update_one({"id": t.aid}, {"$set": {"zuteilung": "offen"}}))
        r = w.run(D.driver_zuteilung(t.aid, D.DriverZuteilungIn(action="ablehnen", grund="krank"),
                                     w.driver))
        assert r["zuteilung"] == "abgelehnt" and "driver_id" not in _doc(w, "appointments", t.aid)
        assert zahl() == 4
        _put(w, t.aid, driver_id=w.driver2["id"])
        assert _doc(w, "appointments", t.aid)["zuteilung"] == "offen"
        assert zahl() == 3

        # Obergrenze: der Zaehler zaehlt hoechstens ABGELEHNT_ZAEHLER_GRENZE
        w.run(w.db.appointments.insert_many([
            {"id": f"t464_viele_{i}_{w.s}", "dealer_id": w.dealer_id, "status": "offen",
             "zuteilung": "abgelehnt", "created_at": _jetzt()}
            for i in range(A.ABGELEHNT_ZAEHLER_GRENZE)]))
        assert zahl() == A.ABGELEHNT_ZAEHLER_GRENZE

        # Nur der Hauptchef: zweites dealer-Konto und Sucher bekommen 403
        # (der Zaehler im Frontend fragt dann in diesem Tab nicht weiter).
        assert w.run(deps.current_chef(w.chef)) is w.chef
        for fremdes_konto in ({"id": f"zweit464_{w.s}", "dealer_id": w.dealer_id, "role": "dealer"},
                              {"id": f"sucher464_{w.s}", "dealer_id": w.dealer_id, "role": "sucher"}):
            with pytest.raises(HTTPException) as e:
                w.run(deps.current_chef(fremdes_konto))
            assert e.value.status_code == 403
    finally:
        # Termine der eigenen Firma raeumt die Welt auf, die fremde Firma wir.
        w.run(w.db.appointments.delete_many({"dealer_id": fremd}))
        w.run(w.db.appointments.delete_many({"id": {"$regex": f"^t464_.*_{w.s}$"}}))
