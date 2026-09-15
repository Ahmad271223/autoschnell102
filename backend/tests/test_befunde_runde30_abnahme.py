# -*- coding: utf-8 -*-
"""Runde 30 (12.09.2026) — Abnahme der gemeldeten Befunde.

Eine unabhaengige Pruefung jedes Befunds gegen den echten Code hat gezeigt,
dass mehrere als "behoben" gemeldete Punkte nur zur Haelfte umgesetzt waren.
Diese Tests halten die Nachbesserungen fest:

  * Die KENNUNG des Kollegen (owner_user_id / mitbearbeiter_ids) ging
    weiterhin an den Sucher — nur die NAMEN waren ausgeblendet. Damit stand
    in /bestand, /vehicles und der Fahrzeugakte, wer sonst an dem Auto
    arbeitet.
  * Der Chef sah in der Freigabe nur einen Auszug des Protokolls, nicht die
    abgehakten Dokumente und Ausstattungsmerkmale.
  * Eine unerwartet geformte Antwort der Kleinanzeigen-API wurde zum harten
    Fehler fuer den Sucher statt zum stillen Rueckfall auf den eigenen Abruf.

In-Prozess gegen eine Wegwerf-DB; kein Server.
"""
import asyncio
import importlib
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MONGO_URL = "mongodb://127.0.0.1:27017"
WURZEL = Path(__file__).resolve().parents[1]


def _jetzt():
    return datetime.now(timezone.utc).isoformat()


def _modul(name):
    return importlib.import_module(name)


@pytest.fixture
def welt():
    from motor.motor_asyncio import AsyncIOMotorClient
    s = uuid.uuid4().hex[:10]
    namen = ["deps", "routes.bestand", "routes.listings", "routes.contracts",
             "kaufvorgang", "lifecycle"]
    mods = [_modul(n) for n in namen]
    alt = [(m, getattr(m, "db", None)) for m in mods]

    class _W:
        pass

    w = _W()
    w.s = s
    w.dealer_id = f"d_r30a_{s}"
    w.chef = {"id": f"chef_r30a_{s}", "dealer_id": w.dealer_id, "role": "dealer"}
    w.a = {"id": f"sa_r30a_{s}", "dealer_id": w.dealer_id, "role": "sucher"}
    w.b = {"id": f"sb_r30a_{s}", "dealer_id": w.dealer_id, "role": "sucher"}
    w.vid = f"v_r30a_{s}"
    w.loop = asyncio.new_event_loop()
    asyncio.set_event_loop(w.loop)
    w.client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    w.db_name = f"autoschnell_r30a_{s}"
    w.db = w.client[w.db_name]
    for m in mods:
        if hasattr(m, "db"):
            m.db = w.db
    w.run = lambda coro: w.loop.run_until_complete(coro)
    w.run(w.db.users.insert_many([
        {**w.chef, "active": True, "first_name": "Chef", "last_name": "C",
         "created_at": _jetzt()},
        {**w.a, "active": True, "first_name": "Anna", "last_name": "A",
         "created_at": _jetzt()},
        {**w.b, "active": True, "first_name": "Ben", "last_name": "B",
         "created_at": _jetzt()}]))
    w.run(w.db.dealers.insert_one({"id": w.dealer_id, "user_id": w.chef["id"],
                                   "company_name": "Firma R30A",
                                   "created_at": _jetzt()}))
    w.run(w.db.vehicles.insert_one({
        "id": w.vid, "dealer_id": w.dealer_id, "owner_user_id": w.a["id"],
        "mitbearbeiter_ids": [w.b["id"]], "uebernommen_von": "irgendwer",
        "lifecycle": "verglichen", "mobile_ad_id": f"ad{s}",
        "data": {"make_label": "VW", "model_label": "Golf"},
        "created_at": _jetzt(), "updated_at": _jetzt()}))
    yield w
    try:
        w.run(w.client.drop_database(w.db_name))
    finally:
        for m, d in alt:
            if d is not None:
                m.db = d
        w.client.close()
        w.loop.close()


# ------------------------------------------- Kennungen der Kollegen
def test_01_sucher_bekommt_keine_konto_kennungen(welt):
    """Befund der Abnahme: Nur die Namen waren weg, die Konto-IDs standen
    weiter in jeder Antwort."""
    w = welt
    D = _modul("deps")
    L = _modul("routes.listings")
    B = _modul("routes.bestand")

    async def lauf():
        liste = await L.list_vehicles(w.a)
        detail = await L.get_vehicle_detail(w.vid, w.a)
        akte = await B.vehicle_akte(w.vid, w.a)
        chef_liste = await L.list_vehicles(w.chef)
        return liste, detail, akte, chef_liste

    liste, detail, akte, chef_liste = w.run(lauf())
    for feld in D.KONTO_FELDER:
        assert feld not in liste[0], (feld, liste[0])
        assert feld not in detail, (feld, detail)
        assert feld not in akte["vehicle"], (feld, akte["vehicle"])
    # Der Chef braucht sie (er weist zu) — bei ihm bleiben sie stehen.
    assert chef_liste[0]["owner_user_id"] == w.a["id"]
    assert chef_liste[0]["mitbearbeiter_ids"] == [w.b["id"]]


def test_02_maskieren_nimmt_einzelnes_fahrzeug_und_liste(welt):
    w = welt
    D = _modul("deps")
    einzeln = {"id": "x", "owner_user_id": "u1", "mitbearbeiter_ids": ["u2"],
               "make": "VW"}
    D.konten_maskieren(w.a, einzeln)
    assert einzeln == {"id": "x", "make": "VW"}
    mehrere = [{"owner_user_id": "u1"}, {"mitbearbeiter_ids": ["u2"]}]
    D.konten_maskieren(w.a, mehrere)
    assert mehrere == [{}, {}]
    # Chef unveraendert
    chef_daten = [{"owner_user_id": "u1"}]
    D.konten_maskieren(w.chef, chef_daten)
    assert chef_daten == [{"owner_user_id": "u1"}]


# ------------------------------------------------- Freigabe-Ansicht
def test_03_chef_sieht_das_ganze_ausgefuellte_protokoll():
    """Befund: Die Freigabe-Liste lieferte nur sechs Inhaltsfelder — die
    abgehakten Dokumente und Ausstattungsmerkmale fehlten."""
    quelle = (WURZEL / "routes" / "protocols.py").read_text(encoding="utf-8")
    block = quelle[quelle.index("async def protokolle_zur_freigabe"):]
    block = block[:block.index("@router.post(\"/protocols/{protocol_id}/freigabe\")")]
    for feld in ('"dokumente"', '"ausstattung"', '"zustand"', '"schluessel"',
                 '"fahrzeugdaten"', '"abweichungen"', '"neue_schaeden"',
                 '"bemerkungen"'):
        assert feld in block, feld


# ------------------------------------------------ Rueckfall der API
def test_04_unerwartete_api_antwort_faellt_zurueck(monkeypatch):
    """Befund: Weicht die Antwort von der erwarteten Form ab, wurde daraus
    ein harter Fehler fuer den Sucher statt eines stillen Rueckfalls."""
    A = _modul("kleinanzeigen_api")

    class _Antwort:
        status_code = 200

        def json(self):
            # Form kaputt: price/location/seller sind Listen statt Objekte
            return {"success": True, "data": {"ad": {
                "ad_id": "123", "title": "Auto", "price": [1, 2],
                "location": ["x"], "seller": "Name statt Objekt",
                "details": {"Marke": "VW"}, "images": "keine Liste"}}}

    class _Klient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **k):
            return _Antwort()

    monkeypatch.setattr(A, "API_KEY", "klaz_test")
    monkeypatch.setattr(A.httpx, "AsyncClient", lambda **k: _Klient())
    # Entweder es klappt trotzdem (robuste Umsetzung) oder es faellt sauber
    # zurueck — ein anderer Fehler waere der Befund.
    try:
        v = asyncio.run(A.hole_inserat("123", "https://www.kleinanzeigen.de/s-anzeige/x/123-216-1"))
        assert v["mobile_ad_id"] == "123"
    except A.ApiNichtNutzbar:
        pass


def test_05_terminloeschung_protokolliert_vor_dem_loeschen():
    """Befund: Der Audit-Eintrag stand NACH dem Hard-Delete und konnte
    selbst werfen — dann war der Termin weg und die Spur fehlte."""
    quelle = (WURZEL / "routes" / "appointments.py").read_text(encoding="utf-8")
    block = quelle[quelle.index('@router.delete("/appointments/{appt_id}")'):]
    block = block[:block.index('@router.get("/appointments/{appt_id}/pickup-order.pdf")')]
    assert block.index('"termin.geloescht"') < block.index("db.appointments.delete_one("), \
        "erst protokollieren, dann loeschen"
    # ... und ein Fehler dabei darf das Loeschen nicht stoppen.
    audit = block[block.index('"termin.geloescht"') - 200:block.index('"termin.geloescht"') + 700]
    assert "except Exception" in audit, audit[-200:]


def test_06_whatsapp_link_liefert_die_digitale_fassung():
    """Befund: Fehlte die digitale Fassung im Versionsarchiv, kam still die
    DRUCKfassung mit Unterschriftslinien."""
    quelle = (WURZEL / "routes" / "contracts.py").read_text(encoding="utf-8")
    block = quelle[quelle.index("geteilte_version = int("):]
    block = block[:block.index("await db.generated_pdfs.update_one(")]
    assert "_digitales_pdf_bytes({**c, **alt}" in block, block[:600]
    assert 'base64.b64decode(alt["pdf_b64"])' not in block, \
        "die Druckfassung darf nicht mehr als Ersatz dienen"
