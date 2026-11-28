# -*- coding: utf-8 -*-
"""Rollenprüfung 22.09.2026 (Review, Welle 4): zwei Übergaben der Fahrer-App
an den Terminplaner (routes/appointments.update_appointment).

1. Sichtfrist: Ein Wechsel zwischen zwei Endzuständen darf die Frist, in der
   der Fahrer Verkäuferdaten und Unterlagen sieht, nicht neu starten.
2. Verkäufername: Ändert der Chef den Namen am Termin, während ein
   Korrektur-Entwurf den ALTEN Namen trägt, wird er im Entwurf geleert.
Die Helfer-Logik selbst prüft test_rp_fahrer_app_welle4_20260922.py.
"""
import ast
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))


def _update_appointment_quelle() -> str:
    quelle = (BACKEND / "routes" / "appointments.py").read_text(encoding="utf-8")
    fn = next(k for k in ast.walk(ast.parse(quelle))
              if isinstance(k, ast.AsyncFunctionDef) and k.name == "update_appointment")
    return ast.unparse(fn)


def test_01_statuswechsel_haelt_die_sichtfrist_fest():
    koerper = _update_appointment_quelle()
    assert "sichtfrist_bei_statuswechsel(existing, update['status'])" in koerper
    assert "update.update(uhr_setzen)" in koerper
    assert "unset[feld] = ''" in koerper
    # direkt beim Statuswechsel, nicht irgendwo anders
    assert koerper.index("status_changed_at'] = now_iso()") \
        < koerper.index("sichtfrist_bei_statuswechsel(")


def test_02_helfer_verhalten():
    from routes.drivers import SICHTFRIST_UHR, sichtfrist_bei_statuswechsel
    abgeholt = {"status": "abgeholt", "status_changed_at": "2026-08-01T10:00:00+00:00",
                "abgeschlossen_seit": "2026-08-01T10:00:00+00:00"}
    setzen, weg = sichtfrist_bei_statuswechsel(abgeholt, "erledigt")
    assert setzen.get(SICHTFRIST_UHR) and not weg, "Endzustand -> Endzustand haelt die Uhr"
    setzen, weg = sichtfrist_bei_statuswechsel(abgeholt, "offen")
    assert not setzen and weg == [SICHTFRIST_UHR], "Wieder-Oeffnen setzt zurueck"
    assert sichtfrist_bei_statuswechsel(abgeholt, "abgeholt") == ({}, [])


def test_03_entwurfsname_folgt_nur_wenn_er_der_alte_war():
    koerper = _update_appointment_quelle()
    assert "db.pickup_protocols.update_many(" in koerper
    stelle = koerper[koerper.index("db.pickup_protocols.update_many("):]
    stelle = stelle[:stelle.index(")")]
    for teil in ("'status': 'entwurf'", "'superseded': {'$ne': True}",
                 "'seller_name': alter_name", "'dealer_id': user['dealer_id']"):
        assert teil in koerper[koerper.index("db.pickup_protocols.update_many("):][:600], teil
