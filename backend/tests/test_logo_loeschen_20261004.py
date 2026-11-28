# -*- coding: utf-8 -*-
"""Wunsch Ahmad 04.10.2026: "man soll sein Logo wieder loeschen koennen". Vorher nur ein kleiner grauer Text
im Formular, der erst mit "Speichern" wirkte. Jetzt DELETE /dealer/logo — sofort, nur der Hauptchef, und
bereits erstellte Vertraege behalten ihr festgehaltenes Logo (die Datei bleibt, solange ein Vertrag sie nennt).

In-Prozess mit der Wegwerf-Welt aus test_rp_vertrag_20260922."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_rp_vertrag_20260922 import _erwarte, _modul, _vertrag, welt  # noqa: E402,F401


def _vorbereiten(w, monkeypatch, logo):
    D = _modul("routes.dealer")
    monkeypatch.setattr(D, "db", w.db)
    geloescht = []

    async def _weg(db, *, key, grund, dealer_id):
        geloescht.append(key)
    import storage_service
    monkeypatch.setattr(storage_service, "loeschen_oder_vormerken", _weg)
    w.run(w.db.dealers.update_one({"id": w.dealer_id}, {"$set": {"logo_url": logo}}))
    return D, geloescht


def test_chef_loescht_logo_sofort_und_die_datei_geht_weg(welt, monkeypatch):
    w = welt
    logo = f"/api/files/logo/{w.dealer_id}/abc.png"
    D, geloescht = _vorbereiten(w, monkeypatch, logo)
    assert w.run(D.logo_loeschen(user=w.chef)) == {"ok": True, "logo_url": ""}
    assert w.run(w.db.dealers.find_one({"id": w.dealer_id}))["logo_url"] == ""
    assert geloescht == [f"logo/{w.dealer_id}/abc.png"]
    # zweites Mal: nichts mehr da, kein Fehler
    assert w.run(D.logo_loeschen(user=w.chef))["logo_url"] == ""
    assert len(geloescht) == 1


def test_vertraege_behalten_ihr_logo(welt, monkeypatch):
    w = welt
    logo = f"/api/files/logo/{w.dealer_id}/fest.png"
    D, geloescht = _vorbereiten(w, monkeypatch, logo)
    doc = _vertrag(w, f"cl_{w.s}", w.a)
    doc["contract_data"]["logo_key"] = f"logo/{w.dealer_id}/fest.png"
    w.run(w.db.generated_pdfs.insert_one(doc))
    w.run(D.logo_loeschen(user=w.chef))
    assert w.run(w.db.dealers.find_one({"id": w.dealer_id}))["logo_url"] == ""
    assert geloescht == [], "ein Vertrag nennt die Datei -> sie bleibt fuer seine spaeteren Fassungen"


def test_sucher_darf_nicht(welt, monkeypatch):
    w = welt
    logo = f"/api/files/logo/{w.dealer_id}/abc.png"
    D, geloescht = _vorbereiten(w, monkeypatch, logo)
    e = w.run(_erwarte(403, D.logo_loeschen(user=w.a)))
    assert "Chef" in e.detail
    assert w.run(w.db.dealers.find_one({"id": w.dealer_id}))["logo_url"] == logo and geloescht == []


def test_oberflaeche_hat_einen_echten_knopf():
    front = Path(__file__).resolve().parents[2] / "frontend" / "src" / "pages" / "app" / "Einstellungen.jsx"
    q = front.read_text(encoding="utf-8")
    assert 'data-testid="logo-loeschen"' in q and 'api.delete("/dealer/logo")' in q
    assert "Bereits erstellte Verträge behalten ihr Logo" in q
    assert 'onClick={() => setProfile("logo_url", "")}' not in q, "der alte Text-Knopf wirkte erst nach Speichern"
