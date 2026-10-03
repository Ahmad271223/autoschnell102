# -*- coding: utf-8 -*-
"""Wunsch Ahmad 21.09.2026 (Audit R1-01), woertlich: "nein das soll entfernt
werden man soll nie an dem sein abgeschlossenen Vertrag oder sonstwas
wegnehmen".

Bis dahin konnte der Chef in der Fahrzeugakte ueber das Feld "Bearbeiter"
ein Fahrzeug einem anderen Konto umhaengen (PUT /vehicles/{id}/besitzer);
mit dem Fahrzeug gingen Vertraege, Kaufvorgaenge und Termine des bisherigen
Suchers mit. Das ist entfernt:

  * die Route antwortet immer 410 und aendert nichts (Rollenpruefung davor:
    ohne Anmeldung 401, Sucher 403, erst der Chef sieht die 410),
  * die Akte liefert keine Kontenliste zum Umhaengen mehr, nur den Bearbeiter,
  * FahrzeugAkte.jsx hat kein Auswahlfeld und keinen PUT auf /besitzer mehr,
  * vorgang_uebergeben bleibt — fuer "Aus meiner Liste entfernen" (der Sucher
    selbst) und das Loeschen eines Sucher-Kontos durch den Betreiber.

In-Prozess: echte Wegwerf-Datenbank fuer "nichts wandert", eine Attrappe fuer
den HTTP-Weg (TestClient, keine Netzwerkverbindung).
"""
import asyncio
import inspect
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

BACKEND = Path(__file__).resolve().parents[1]
WURZEL = BACKEND.parent
sys.path.insert(0, str(BACKEND))

import deps  # noqa: E402
import kaufvorgang as KV  # noqa: E402
import lifecycle as LC  # noqa: E402
import routes.bestand as B  # noqa: E402
import routes.contracts as C  # noqa: E402

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"

CHEF = {"id": "chef", "dealer_id": "d1", "role": "dealer", "active": True}
SA = {"id": "sa", "dealer_id": "d1", "role": "sucher", "active": True}


def _jetzt() -> str:
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture
def wegwerf(monkeypatch):
    from motor.motor_asyncio import AsyncIOMotorClient
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    name = f"autoschnell_r101_{uuid.uuid4().hex[:10]}"
    db = client[name]
    for mod in (deps, KV, LC, B, C):
        monkeypatch.setattr(mod, "db", db)
    try:
        yield SimpleNamespace(db=db, run=loop.run_until_complete)
    finally:
        try:
            loop.run_until_complete(client.drop_database(name))
        finally:
            client.close()
            loop.close()


async def _stand(db) -> dict:
    """Alles, was ein Umhaengen frueher veraendert hat — sortiert, ohne _id."""
    out = {}
    for c in ("vehicles", "generated_pdfs", "kaufvorgaenge", "appointments", "activity_logs"):
        out[c] = sorted((await db[c].find({}, {"_id": 0}).to_list(None)),
                        key=lambda d: str(d.get("id")))
    return out


# ------------------------------------------------------------ Chef: 410, nichts wandert
def test_chef_bekommt_410_und_nichts_wandert(wegwerf):
    db, run = wegwerf.db, wegwerf.run
    run(db.dealers.insert_one({"id": "d1", "user_id": "chef", "company_name": "F", "created_at": _jetzt()}))
    run(db.users.insert_many([
        {**CHEF, "first_name": "Chef", "last_name": "C", "created_at": "2026-01-01"},
        {**SA, "first_name": "Anna", "last_name": "A", "created_at": "2026-01-02"},
        {"id": "sb", "dealer_id": "d1", "role": "sucher", "active": True,
         "first_name": "Ben", "last_name": "B", "created_at": "2026-01-03"}]))
    run(db.vehicles.insert_many([
        # Vertrag unterschrieben, Abholung geplant — der klassische "Wegnehmen"-Fall
        {"id": "v1", "dealer_id": "d1", "owner_user_id": "sa", "mitbearbeiter_ids": ["sb"],
         "lifecycle": "abholung_geplant", "data": {"make_label": "BMW"}, "created_at": _jetzt()},
        # schon abgeholt (abgeschlossener Vorgang)
        {"id": "v2", "dealer_id": "d1", "owner_user_id": "sa", "mitbearbeiter_ids": [],
         "lifecycle": "abgeholt", "data": {}, "created_at": _jetzt()},
        # Altbestand ohne Besitzer: wird auch nicht still zugewiesen
        {"id": "v3", "dealer_id": "d1", "lifecycle": "bestand", "data": {}, "created_at": _jetzt()}]))
    run(db.generated_pdfs.insert_many([
        {"id": "c1", "dealer_id": "d1", "vehicle_id": "v1", "user_id": "sa", "created_at": _jetzt()},
        {"id": "c2", "dealer_id": "d1", "vehicle_id": "v2", "user_id": "sa", "created_at": _jetzt()}]))
    run(db.kaufvorgaenge.insert_many([
        {"id": "k1", "dealer_id": "d1", "vehicle_id": "v1", "user_id": "sa", "contract_id": "c1",
         "status": "abholung_geplant", "created_at": _jetzt(), "updated_at": _jetzt()},
        {"id": "k2", "dealer_id": "d1", "vehicle_id": "v2", "user_id": "sa", "contract_id": "c2",
         "status": "abgeholt", "created_at": _jetzt(), "updated_at": _jetzt()}]))
    run(db.appointments.insert_many([
        {"id": "t1", "dealer_id": "d1", "vehicle_id": "v1", "contract_id": "c1", "kaufvorgang_id": "k1",
         "created_by": "sa", "status": "offen", "created_at": _jetzt(), "updated_at": _jetzt()},
        {"id": "t2", "dealer_id": "d1", "vehicle_id": "v2", "contract_id": "c2", "kaufvorgang_id": "k2",
         "created_by": "sa", "status": "abgeholt", "created_at": _jetzt(), "updated_at": _jetzt()}]))
    vorher = run(_stand(db))

    for vid in ("v1", "v2", "v3", "gibt-es-nicht"):
        with pytest.raises(HTTPException) as e:
            run(B.fahrzeug_umhaengen_entfernt(vid, dict(CHEF)))
        assert e.value.status_code == 410, vid
        assert e.value.detail == B.UMHAENGEN_ENTFERNT
        assert "gibt es nicht mehr" in e.value.detail and "21.09.2026" in e.value.detail

    assert run(_stand(db)) == vorher, "Fahrzeug, Vertraege, Vorgaenge oder Termine wurden veraendert"

    # Die Akte zeigt den Bearbeiter nur noch an — keine Kontenliste zum Umhaengen
    akte = run(B.vehicle_akte("v1", dict(CHEF)))
    assert "zuweisbar" not in akte and "zuweisbar_an" not in akte
    assert akte["owner"] == {"id": "sa", "name": "Anna A", "hauptaccount": False}
    assert akte["mitbearbeiter"] == [{"id": "sb", "name": "Ben B"}]
    run(db.vehicles.update_one({"id": "v3"}, {"$set": {"owner_user_id": "chef"}}))
    assert run(B.vehicle_akte("v3", dict(CHEF)))["owner"] == {
        "id": "chef", "name": "Chef C", "hauptaccount": True}


def test_umhaengen_code_ist_weg():
    """Kein toter Umhaeng-Code mehr in routes/bestand.py; die Route ist nur
    noch die 410-Attrappe hinter der Chef-Pruefung."""
    assert not hasattr(B, "set_vehicle_owner")
    assert not hasattr(B, "BesitzerIn")
    quelle = (BACKEND / "routes" / "bestand.py").read_text(encoding="utf-8")
    assert '"fahrzeug.zugewiesen"' not in quelle, "kein Audit-Eintrag fuer ein Umhaengen mehr"
    assert '"uebergabe_offen": merker' not in quelle, "kein neuer Uebergabe-Merker mehr"
    routen = [r for r in B.router.routes if getattr(r, "path", "") == "/vehicles/{vehicle_id}/besitzer"]
    assert len(routen) == 1 and routen[0].methods == {"PUT"}
    assert routen[0].endpoint is B.fahrzeug_umhaengen_entfernt
    sig = inspect.signature(B.fahrzeug_umhaengen_entfernt)
    assert sig.parameters["user"].default.dependency is B.current_haendler
    assert set(sig.parameters) == {"vehicle_id", "user"}, "kein Body-Modell (sonst 422 vor der 410)"


# ------------------------------------------------------------ HTTP-Weg (in-process)
class _Sammlung:
    """Attrappe einer Collection: find_one liefert ein festes Dokument, jeder
    andere Zugriff (Schreiben, Zaehlen, Suchen) wird protokolliert und schlaegt fehl."""

    def __init__(self, name, protokoll, doc=None):
        self._name, self._protokoll, self._doc = name, protokoll, doc

    async def find_one(self, *a, **k):
        self._protokoll.append((self._name, "find_one"))
        return self._doc

    def __getattr__(self, attr):
        self._protokoll.append((self._name, attr))

        async def _verboten(*a, **k):
            raise AssertionError(f"unerwarteter Zugriff {self._name}.{attr}")
        return _verboten


class _DbAttrappe:
    def __init__(self):
        self.protokoll = []
        self.dealers = _Sammlung("dealers", self.protokoll, {"id": "d1", "user_id": "chef"})

    def __getattr__(self, name):
        return _Sammlung(name, self.protokoll)

    def __getitem__(self, name):
        return _Sammlung(name, self.protokoll)


def test_http_rollen_401_403_und_chef_410(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    attrappe = _DbAttrappe()
    monkeypatch.setattr(deps, "db", attrappe)
    monkeypatch.setattr(B, "db", attrappe)
    app = FastAPI()
    app.include_router(B.router, prefix="/api")
    pfad = "/api/vehicles/v1/besitzer"
    with TestClient(app) as tc:
        # ohne Anmeldung: 401 aus current_user (bearer ohne auto_error)
        assert tc.put(pfad, json={"owner_user_id": "sb"}).status_code == 401
        # Sucher: 403 aus current_chef — wie vor dem 21.09.
        app.dependency_overrides[deps.current_user] = lambda: dict(SA)
        r = tc.put(pfad, json={"owner_user_id": "sb"})
        assert r.status_code == 403, r.text
        # Chef: 410 mit klarer Meldung — mit Body, ohne Body und mit kaputtem Body
        app.dependency_overrides[deps.current_user] = lambda: dict(CHEF)
        for kwargs in ({"json": {"owner_user_id": "sb"}}, {}, {"content": b"kaputt"},
                       {"json": {"owner_user_id": ""}}):
            r = tc.put(pfad, **kwargs)
            assert r.status_code == 410, (kwargs, r.status_code, r.text)
            assert r.json()["detail"] == B.UMHAENGEN_ENTFERNT
    geschrieben = [z for z in attrappe.protokoll if z[1] != "find_one"]
    assert geschrieben == [], geschrieben
    assert {z[0] for z in attrappe.protokoll} <= {"dealers"}, attrappe.protokoll


# ------------------------------------------------------------ Oberflaeche
def test_fahrzeugakte_ohne_auswahlfeld_und_ohne_put_auf_besitzer():
    akte = (WURZEL / "frontend" / "src" / "pages" / "app" / "FahrzeugAkte.jsx").read_text(encoding="utf-8")
    assert "akte-besitzer-select" not in akte
    assert "/besitzer" not in akte and "zuweisen" not in akte and "zuweisbar" not in akte
    assert "<select" not in akte.split('data-testid="akte-besitzer"')[1].split("</span>")[0]
    # Der Bearbeiter bleibt fuer den Chef als Text sichtbar
    assert 'data-testid="akte-besitzer"' in akte
    assert "akte.owner.hauptaccount" in akte and "(Hauptaccount)" in akte
    assert 'data-testid="akte-mitbearbeiter"' in akte
    # Kein anderer Teil der Oberflaeche ruft das Umhaengen mehr auf
    treffer = []
    for datei in (WURZEL / "frontend" / "src").rglob("*"):
        if datei.suffix in (".js", ".jsx", ".ts", ".tsx") and datei.is_file():
            if "/besitzer" in datei.read_text(encoding="utf-8", errors="ignore"):
                treffer.append(str(datei))
    assert treffer == [], treffer


# ------------------------------------------------------------ Was bleibt
def test_vorgang_uebergeben_bleibt_fuer_entfernen_und_kontoloeschung():
    import routes.admin as ADMIN
    assert inspect.iscoroutinefunction(B.vorgang_uebergeben)
    assert inspect.iscoroutinefunction(B.uebergabe_nachholen)
    assert "vorgang_uebergeben(" in inspect.getsource(B.vehicle_fuer_sucher_entfernen), \
        "Aus meiner Liste entfernen uebergibt den Vorgang an den Chef"
    assert "vorgang_uebergeben(" in inspect.getsource(ADMIN.admin_delete_user), \
        "Loeschen eines Sucher-Kontos uebergibt den Vorgang an den Chef"


def test_entfernen_durch_den_sucher_uebergibt_weiter_an_den_chef(wegwerf):
    """Gegenprobe: der Sucher selbst darf sein Auto weiter aus SEINER Liste
    nehmen — das ist kein Wegnehmen durch den Chef, der Vorgang geht an den Chef."""
    db, run = wegwerf.db, wegwerf.run
    run(db.dealers.insert_one({"id": "d1", "user_id": "chef", "company_name": "F", "created_at": _jetzt()}))
    run(db.vehicles.insert_one({"id": "v1", "dealer_id": "d1", "owner_user_id": "sa",
                                "mitbearbeiter_ids": [], "lifecycle": "abgeholt", "data": {}}))
    run(db.generated_pdfs.insert_one({"id": "c1", "dealer_id": "d1", "vehicle_id": "v1", "user_id": "sa",
                                      "created_at": _jetzt()}))
    run(db.appointments.insert_one({"id": "t1", "dealer_id": "d1", "vehicle_id": "v1", "created_by": "sa",
                                    "status": "abgeholt", "created_at": _jetzt()}))
    out = run(B.vehicle_fuer_sucher_entfernen("v1", dict(SA)))
    assert out["an_chef"] is True and out["uebergabe"]["vertraege"] == 1 and out["uebergabe"]["termine"] == 1
    assert run(db.vehicles.find_one({"id": "v1"}))["owner_user_id"] == "chef"
    assert run(db.generated_pdfs.find_one({"id": "c1"}))["user_id"] == "chef"
    assert run(db.appointments.find_one({"id": "t1"}))["created_by"] == "chef"
