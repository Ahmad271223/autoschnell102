# -*- coding: utf-8 -*-
"""Go-Live-Schalter (15.09.2026, Wunsch Ahmad): B2B-Marktplatz und Inserieren
sind abgeschaltet ("Demnaechst verfuegbar"), bis MARKTPLATZ_AKTIV=true gesetzt
ist. Der Code bleibt erhalten; nur der Schalter entscheidet.

- konfig.marktplatz_aktiv liest den Schalter (Standard aus)
- deps.marktplatz_freigeschaltet antwortet 503 mit klarem Text
- Marktplatz-/Inserats-Router und Verkaufspaket-Routen tragen die Abhaengigkeit
- "Jetzt inserieren" (Fahrzeug-Entscheidung verkaufsentwurf) ist gesperrt
- GET /api/features sagt der Oberflaeche, was frei ist (HTTP, Suite-Backend)
"""
import asyncio
import inspect
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tests"))

import deps  # noqa: E402
import konfig  # noqa: E402
import routes.bestand as BST  # noqa: E402

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
http = pytest.mark.skipif(not os.environ.get("RUNDE14_HTTP"), reason="HTTP-Tests nur mit RUNDE14_HTTP=1")
API = (os.environ.get("TEST_BASE_URL") or "http://127.0.0.1:8002").rstrip("/") + "/api"


@pytest.fixture
def wegwerf(monkeypatch):
    from motor.motor_asyncio import AsyncIOMotorClient
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    name = f"autoschnell_mp_{uuid.uuid4().hex[:10]}"
    db = client[name]
    for mod in (deps, BST):
        monkeypatch.setattr(mod, "db", db)
    try:
        yield SimpleNamespace(db=db, run=loop.run_until_complete)
    finally:
        try:
            loop.run_until_complete(client.drop_database(name))
        finally:
            client.close()
            loop.close()


def test_schalter_standard_aus(monkeypatch):
    monkeypatch.delenv("MARKTPLATZ_AKTIV", raising=False)
    assert konfig.marktplatz_aktiv() is False, "Produktions-Standard: aus"
    for wert in ("true", "1", "ja", "yes", "on", "TRUE"):
        monkeypatch.setenv("MARKTPLATZ_AKTIV", wert)
        assert konfig.marktplatz_aktiv() is True, wert
    for wert in ("false", "0", "nein", "", "aus"):
        monkeypatch.setenv("MARKTPLATZ_AKTIV", wert)
        assert konfig.marktplatz_aktiv() is False, wert


def test_abhaengigkeit_antwortet_503(monkeypatch):
    monkeypatch.delenv("MARKTPLATZ_AKTIV", raising=False)
    with pytest.raises(HTTPException) as e:
        asyncio.run(deps.marktplatz_freigeschaltet())
    assert e.value.status_code == 503 and "Demnächst verfügbar" in e.value.detail
    monkeypatch.setenv("MARKTPLATZ_AKTIV", "true")
    assert asyncio.run(deps.marktplatz_freigeschaltet()) is None


def test_router_und_routen_tragen_den_schalter():
    server = (BACKEND / "server.py").read_text(encoding="utf-8")
    assert "api.include_router(resale_routes.router, dependencies=[Depends(marktplatz_freigeschaltet)])" in server
    assert "api.include_router(marketplace_routes.router, dependencies=[Depends(marktplatz_freigeschaltet)])" in server
    assert '@api.get("/features")' in server
    team = (BACKEND / "routes" / "team.py").read_text(encoding="utf-8")
    assert '@router.get("/dealer/sale-plan", dependencies=[Depends(marktplatz_freigeschaltet)])' in team
    assert '@router.post("/dealer/sale-plan/upgrade-request", dependencies=[Depends(marktplatz_freigeschaltet)])' in team
    # Produktion: Compose-Default aus, CI: an
    compose = (BACKEND.parent / "docker-compose.yml").read_text(encoding="utf-8")
    assert "MARKTPLATZ_AKTIV=${MARKTPLATZ_AKTIV:-false}" in compose
    ci = (BACKEND.parent / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert ci.count('MARKTPLATZ_AKTIV: "true"') == 2
    # Oberflaeche: Schalter-Seiten und Ausblendungen
    front = BACKEND.parent / "frontend" / "src"
    app = (front / "App.jsx").read_text(encoding="utf-8")
    assert app.count("<FeatureGate ") == 4
    for datei, muster in (("components/AppLayout.jsx", 'it.to !== "/app/anfragen" || features.marktplatz'),
                          ("pages/app/Bestand.jsx", "features.marktplatz && ["),
                          ("pages/app/FahrzeugAkte.jsx", "features.marktplatz && !sucher"),
                          ("pages/app/Einstellungen.jsx", '!features.marktplatz)'),
                          ("pages/Landing.jsx", 'data-testid="markt-demnaechst"'),
                          ("pages/Anfrage.jsx", "marktGesperrt"),
                          ("pages/AppStart.jsx", 'w.testid !== "start-markt" || features.marktplatz')):
        assert muster in (front / datei).read_text(encoding="utf-8"), datei


def test_jetzt_inserieren_gesperrt(wegwerf, monkeypatch):
    db, run = wegwerf.db, wegwerf.run
    jetzt = datetime.now(timezone.utc).isoformat()
    run(db.dealers.insert_one({"id": "d1", "company_name": "X", "user_id": "chef", "created_at": jetzt}))
    run(db.vehicles.insert_one({"id": "v1", "dealer_id": "d1", "lifecycle": "bestand",
                                "owner_user_id": "chef", "data": {}, "created_at": jetzt}))
    chef = {"id": "chef", "dealer_id": "d1", "role": "dealer", "active": True}
    monkeypatch.delenv("MARKTPLATZ_AKTIV", raising=False)
    with pytest.raises(HTTPException) as e:
        run(BST.vehicle_decision("v1", BST.DecisionIn(decision="verkaufsentwurf"), chef))
    assert e.value.status_code == 503 and "Demnächst verfügbar" in e.value.detail
    assert run(db.vehicles.find_one({"id": "v1"}))["lifecycle"] == "bestand", "nichts veraendert"
    # "bestand" (50 Tage speichern) bleibt erlaubt
    monkeypatch.setenv("MARKTPLATZ_AKTIV", "true")
    src = inspect.getsource(BST.vehicle_decision)
    assert 'body.decision == "verkaufsentwurf" and not marktplatz_aktiv()' in src


@http
def test_http_features_und_503():
    import requests
    r = requests.get(f"{API}/features", timeout=30)
    assert r.status_code == 200 and isinstance(r.json().get("marktplatz"), bool), r.text[:200]
    # Suite-Backend laeuft mit Schalter an (MARKTPLATZ_AKTIV=true): oeffentliche
    # Marktplatz-Route antwortet nicht mit 503 — sonst 503 mit klarem Text.
    m = requests.get(f"{API}/marktplatz/haendler", timeout=30)
    if r.json()["marktplatz"]:
        assert m.status_code != 503, m.text[:200]
    else:
        assert m.status_code == 503 and "Demnächst verfügbar" in m.text
