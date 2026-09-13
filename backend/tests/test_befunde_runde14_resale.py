# -*- coding: utf-8 -*-
"""Nachpruefung Runde 14, Gruppe "resale" (09/2026).

Befunde 26, 27, 28, 29, 45, 52, 53, 54, 67, 79, 80, 81, 89, 90, 91, 106,
107, 108, 117 — alle in backend/routes/resale.py.

Einheitentests laufen ohne Server gegen eine kleine Mongo-Attrappe (nur die
Operatoren, die resale.py benutzt). HTTP-Tests laufen nur mit
RUNDE14_HTTP=1 (Backend auf TEST_BASE_URL mit neuem Code, SELF_SIGNUP=true).
"""
import asyncio
import base64
import copy
import io
import os
import sys
import types
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
import requests
from fastapi import HTTPException

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

HTTP = os.environ.get("RUNDE14_HTTP") == "1"
# Runde 21 (Pruefbefund C): klarer Skip-Grund statt "HTTP nach Neustart" —
# die CI setzt RUNDE14_HTTP=1 im Schritt "Selbsttest-Suite".
HTTP_GRUND = ("RUNDE14_HTTP=1 nicht gesetzt — HTTP-Test braucht ein laufendes "
              "Backend auf TEST_BASE_URL (CI: Schritt Selbsttest-Suite)")
BASE = (os.environ.get("TEST_BASE_URL") or "http://localhost:8001").rstrip("/")
API = f"{BASE}/api"
MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
DB_NAME = os.environ.get("DB_NAME") or "autoschnell"
SUF = uuid.uuid4().hex[:8]
PW = "NachTest123!"
MAIL = "e2etest-mail.de"


def _jetzt():
    return datetime.now(timezone.utc).isoformat()


def _db():
    from pymongo import MongoClient
    return MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)[DB_NAME]


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# =============================================================== Mongo-Attrappe
def _get(doc, pfad):
    cur = doc
    for teil in pfad.split("."):
        if isinstance(cur, dict):
            cur = cur.get(teil)
        elif isinstance(cur, list) and teil.isdigit():
            cur = cur[int(teil)] if int(teil) < len(cur) else None
        else:
            return None
    return cur


def _set(doc, pfad, wert):
    teile = pfad.split(".")
    cur = doc
    for teil in teile[:-1]:
        cur = cur.setdefault(teil, {})
    cur[teile[-1]] = wert


def _passt_wert(ist, soll):
    if isinstance(soll, dict) and any(k.startswith("$") for k in soll):
        for op, arg in soll.items():
            if op == "$ne" and ist == arg:
                return False
            if op == "$nin" and ist in arg:
                return False
            if op == "$in" and ist not in arg:
                return False
            if op == "$exists" and (ist is not None) != bool(arg):
                return False
            if op == "$lt" and not (ist is not None and ist < arg):
                return False
        return True
    if isinstance(ist, list) and not isinstance(soll, list):
        return soll in ist
    return ist == soll


def _passt(doc, q):
    for k, v in q.items():
        if k == "$or":
            if not any(_passt(doc, alt) for alt in v):
                return False
            continue
        if not _passt_wert(_get(doc, k), v):
            return False
    return True


class _Res:
    def __init__(self, matched, modified):
        self.matched_count, self.modified_count = matched, modified


def _anwenden(doc, op):
    for pfad, wert in (op.get("$set") or {}).items():
        _set(doc, pfad, wert)
    for pfad in (op.get("$unset") or {}):
        teile = pfad.split(".")
        cur = doc
        for teil in teile[:-1]:
            cur = cur.get(teil) or {}
        cur.pop(teile[-1], None)
    for pfad, wert in (op.get("$pull") or {}).items():
        liste = _get(doc, pfad)
        if isinstance(liste, list) and wert in liste:
            liste.remove(wert)
    for pfad, wert in (op.get("$push") or {}).items():
        liste = _get(doc, pfad)
        if liste is None:
            liste = []
            _set(doc, pfad, liste)
        liste.extend(wert["$each"] if isinstance(wert, dict) and "$each" in wert else [wert])
    for pfad, wert in (op.get("$addToSet") or {}).items():
        liste = _get(doc, pfad)
        if liste is None:
            liste = []
            _set(doc, pfad, liste)
        if wert not in liste:
            liste.append(wert)
    for pfad, wert in (op.get("$inc") or {}).items():
        _set(doc, pfad, (_get(doc, pfad) or 0) + wert)


class _Cursor:
    """Runde 21: find()-Attrappe (sort/limit/to_list/async for) — das Inserat
    liest seit der Fahrerfoto-Uebernahme und dem Einkaufspreis aus dem
    Vertrag auch Listen (pickup_reports, kaufvorgaenge)."""
    def __init__(self, docs):
        self.docs = docs

    def sort(self, *a, **k):
        return self

    def limit(self, n):
        self.docs = self.docs[:n] if n else self.docs
        return self

    async def to_list(self, n=None):
        return self.docs[:n] if n else list(self.docs)

    def __aiter__(self):
        self._it = iter(self.docs)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


class _Coll:
    def __init__(self, docs=None):
        self.docs = [copy.deepcopy(d) for d in (docs or [])]

    def find(self, q=None, proj=None, **kw):
        return _Cursor([copy.deepcopy(d) for d in self._treffer(q or {})])

    def _treffer(self, q):
        return [d for d in self.docs if _passt(d, q)]

    async def find_one(self, q, proj=None, **kw):
        t = self._treffer(q)
        return copy.deepcopy(t[0]) if t else None

    async def find_one_and_update(self, q, op, **kw):
        t = self._treffer(q)
        if not t:
            return None
        vorher = copy.deepcopy(t[0])
        _anwenden(t[0], op)
        return vorher

    async def update_one(self, q, op):
        t = self._treffer(q)
        if not t:
            return _Res(0, 0)
        _anwenden(t[0], op)
        return _Res(1, 1)

    async def update_many(self, q, op):
        t = self._treffer(q)
        for d in t:
            _anwenden(d, op)
        return _Res(len(t), len(t))

    async def count_documents(self, q):
        return len(self._treffer(q))

    async def insert_one(self, doc):
        self.docs.append(copy.deepcopy(doc))

    def one(self, **q):
        t = self._treffer(q)
        assert t, f"kein Dokument fuer {q}"
        return t[0]


class _Db:
    def __init__(self, **colls):
        # Umbau Kaufvorgaenge 09.09.2026: create_draft liest den abgeholten Vorgang
        for name in ("resale_listings", "vehicles", "listing_interest",
                     "dealers", "pickup_reports", "kaufvorgaenge"):
            setattr(self, name, _Coll(colls.get(name)))


USER = {"id": "chef-1", "dealer_id": "firma-1"}


def _listing(status, **extra):
    d = {"id": "L1", "dealer_id": "firma-1", "vehicle_id": "V1", "status": status,
         "title": "Golf", "description": "alt", "known_defects": [],
         "data": {"mileage": 100000, "vin": "ALT"},
         "prices": {"public": 9900.0, "b2b": None, "network": None},
         "photos": {"mode": "einkauf", "einkauf_urls": ["https://x/1.jpg"],
                    "uploaded_keys": ["resale/firma-1/a.jpg"]},
         "costs": [], "counted_periods": [], "created_at": _jetzt(),
         "updated_at": _jetzt()}
    d.update(extra)
    return d


def _fahrzeug(lifecycle):
    return {"id": "V1", "dealer_id": "firma-1", "lifecycle": lifecycle,
            "data": {"make_label": "VW", "model_label": "Golf", "mileage": 100000}}


@pytest.fixture
def welt(monkeypatch):
    """resale mit Attrappen-DB, stummem Protokoll und aufgezeichnetem
    Lebenszyklus (set_lifecycle wird NICHT geschluckt)."""
    from routes import resale
    from lifecycle import ALLOWED_TRANSITIONS, LifecycleError
    z = types.SimpleNamespace(resale=resale, lifecycle_schritte=[])

    async def _log(*a, **k):
        return None

    async def _set_lifecycle(vehicle_id, dealer_id, ziel, *, user=None, force=False):
        v = z.db.vehicles.one(id=vehicle_id, dealer_id=dealer_id)
        cur = v.get("lifecycle")
        if cur != ziel and not force and ziel not in ALLOWED_TRANSITIONS.get(cur, set()):
            raise LifecycleError(f"Übergang '{cur}' → '{ziel}' ist nicht erlaubt")
        v["lifecycle"] = ziel
        z.lifecycle_schritte.append(ziel)
        return v

    def bauen(**colls):
        z.db = _Db(**colls)
        monkeypatch.setattr(resale, "db", z.db)
        import kaufvorgang as _kv          # Runde 21: Einkaufspreis aus dem Vertrag
        monkeypatch.setattr(_kv, "db", z.db)
        return z

    async def _try_set_lifecycle(vehicle_id, dealer_id, ziel, *, user=None):
        try:
            await _set_lifecycle(vehicle_id, dealer_id, ziel, user=user)
        except LifecycleError:
            pass

    monkeypatch.setattr(resale, "log_activity", _log)
    monkeypatch.setattr(resale, "set_lifecycle", _set_lifecycle)
    monkeypatch.setattr(resale, "try_set_lifecycle", _try_set_lifecycle)
    z.bauen = bauen
    return z


def _fehler(coro):
    with pytest.raises(HTTPException) as e:
        _run(coro)
    return e.value


# =============================================================== Modelle (80, 117)
@pytest.mark.parametrize("wert", ["1e400", "Infinity", "-Infinity", "NaN"])
def test_unit_80_preise_ohne_unendlich(wert):
    from pydantic import ValidationError
    from routes.resale import ListingStatusIn, ListingUpdateIn
    for feld in ("price_public", "price_b2b", "price_network"):
        with pytest.raises(ValidationError):
            ListingUpdateIn.model_validate_json('{"%s": %s}' % (feld, wert))
    with pytest.raises(ValidationError):
        ListingStatusIn.model_validate_json('{"status": "verkauft", "sold_price": %s}' % wert)
    assert ListingUpdateIn.model_validate_json('{"price_public": 12345.678}').price_public == 12345.678


def test_unit_117_foto_einzelstring_gedeckelt():
    from pydantic import ValidationError
    from routes.resale import PhotoUploadIn, _B64_MAX_LEN
    with pytest.raises(ValidationError) as e:
        PhotoUploadIn(photos_b64=["a" * (_B64_MAX_LEN + 1)])
    assert e.value.errors()[0]["type"] == "string_too_long"
    assert len(PhotoUploadIn(photos_b64=["a" * 1000] * 20).photos_b64) == 20
    with pytest.raises(ValidationError):
        PhotoUploadIn(photos_b64=["a"] * 21)


# =============================================================== Lebenszyklus-Weg (52, 53, 81)
def test_unit_52_53_weg_aus_reserviert_ueber_veroeffentlicht():
    from routes.resale import _lifecycle_pfad
    assert _lifecycle_pfad("reserviert", "verkaufsbereit") == ["veroeffentlicht", "verkaufsbereit"]
    assert _lifecycle_pfad("reserviert", "bestand") == ["veroeffentlicht", "bestand"]
    assert _lifecycle_pfad("reserviert", "verkauft") == ["verkauft"]
    assert _lifecycle_pfad("veroeffentlicht", "veroeffentlicht") == []
    assert _lifecycle_pfad("verkaufsbereit", "veroeffentlicht") == ["veroeffentlicht"]


def test_unit_52_altbestand_holt_entwurfsschritt_nach():
    from routes.resale import _lifecycle_pfad
    assert _lifecycle_pfad("bestand", "verkaufsbereit") == ["verkaufsentwurf", "verkaufsbereit"]
    assert _lifecycle_pfad("abgeholt", "verkaufsbereit") == ["verkaufsentwurf", "verkaufsbereit"]
    from lifecycle import LifecycleError
    with pytest.raises(LifecycleError):                    # kein lifecycle -> "verglichen"
        _lifecycle_pfad(None, "verkaufsentwurf")


def test_unit_81_kein_weg_ist_ein_fehler():
    from lifecycle import LifecycleError
    from routes.resale import _lifecycle_pfad
    with pytest.raises(LifecycleError):
        _lifecycle_pfad("bestand", "veroeffentlicht")
    with pytest.raises(LifecycleError):
        _lifecycle_pfad("verkauft", "verkaufsbereit")
    with pytest.raises(LifecycleError):
        _lifecycle_pfad("bestand", "reserviert")


# =============================================================== update_listing (28, 89, 91, 107, 108)
@pytest.mark.parametrize("status", ["verkauft", "geloescht"])
def test_unit_28_abgeschlossene_inserate_nicht_bearbeitbar(welt, status):
    z = welt.bauen(resale_listings=[_listing(status)])
    from routes.resale import ListingUpdateIn
    e = _fehler(z.resale.update_listing("L1", ListingUpdateIn(title="NEU", price_public=1), USER))
    assert e.status_code == 400
    d = z.db.resale_listings.one(id="L1")
    assert d["title"] == "Golf" and d["prices"]["public"] == 9900.0


def test_unit_28_geloescht_einzeln_404(welt):
    z = welt.bauen(resale_listings=[_listing("geloescht")])
    assert _fehler(z.resale.get_listing("L1", USER)).status_code == 404


def test_unit_89_rennen_mit_verkauf_wird_409(welt):
    z = welt.bauen(resale_listings=[_listing("veroeffentlicht")])
    from routes.resale import ListingUpdateIn
    echt = z.db.resale_listings.find_one

    async def lesen_dann_verkaufen(q, proj=None, **kw):
        doc = await echt(q, proj)
        z.db.resale_listings.one(id="L1")["status"] = "verkauft"   # zwischen Lesen und Schreiben
        return doc
    z.db.resale_listings.find_one = lesen_dann_verkaufen
    e = _fehler(z.resale.update_listing("L1", ListingUpdateIn(title="NACH VERKAUF"), USER))
    assert e.status_code == 409
    d = z.db.resale_listings.one(id="L1")
    assert d["status"] == "verkauft" and d["title"] == "Golf"


def test_unit_91_preise_einzeln_geschrieben(welt):
    z = welt.bauen(resale_listings=[_listing("entwurf", prices={"public": None, "b2b": None, "network": None})])
    from routes.resale import ListingUpdateIn
    stale = copy.deepcopy(z.db.resale_listings.one(id="L1"))

    async def alter_stand(q, proj=None, **kw):   # beide PUTs sehen denselben Snapshot
        return copy.deepcopy(stale) if q.get("id") == "L1" else None
    z.db.resale_listings.find_one = alter_stand
    _run(z.resale.update_listing("L1", ListingUpdateIn(price_public=9000), USER))
    _run(z.resale.update_listing("L1", ListingUpdateIn(price_network=8000), USER))
    p = z.db.resale_listings.one(id="L1")["prices"]
    assert p == {"public": 9000.0, "b2b": None, "network": 8000.0}


@pytest.mark.parametrize("status", ["verkaufsbereit", "veroeffentlicht"])
def test_unit_107_preis_null_nach_freigabe_abgelehnt(welt, status):
    z = welt.bauen(resale_listings=[_listing(status)])
    from routes.resale import ListingUpdateIn
    e = _fehler(z.resale.update_listing("L1", ListingUpdateIn(price_public=0), USER))
    assert e.status_code == 400
    assert z.db.resale_listings.one(id="L1")["prices"]["public"] == 9900.0
    # nur ein anderes Preisfeld -> ok, public bleibt
    r = _run(z.resale.update_listing("L1", ListingUpdateIn(price_b2b=8000), USER))
    assert r["prices"] == {"public": 9900.0, "b2b": 8000.0, "network": None}


def test_unit_107_entwurf_darf_platzhalter_null(welt):
    z = welt.bauen(resale_listings=[_listing("entwurf")])
    from routes.resale import ListingUpdateIn
    r = _run(z.resale.update_listing("L1", ListingUpdateIn(price_public=0), USER))
    assert r["prices"]["public"] is None


def test_unit_108_reserviert_sperrt_preis_daten_maengel(welt):
    z = welt.bauen(resale_listings=[_listing("reserviert", reserved_for="k1")])
    from routes.resale import ListingUpdateIn
    for body in (ListingUpdateIn(price_public=19999), ListingUpdateIn(data={"mileage": 250000}),
                 ListingUpdateIn(known_defects=["Motorschaden"])):
        assert _fehler(z.resale.update_listing("L1", body, USER)).status_code == 400
    d = z.db.resale_listings.one(id="L1")
    assert d["prices"]["public"] == 9900.0 and d["data"]["mileage"] == 100000 and d["known_defects"] == []
    r = _run(z.resale.update_listing("L1", ListingUpdateIn(description="neu", costs=[{"label": "Reifen", "amount": 100}]), USER))
    assert r["description"] == "neu" and r["status"] == "reserviert"


# =============================================================== delete_listing (53, 54, 90)
def test_unit_90_doppeltes_loeschen_409_und_rennen(welt):
    z = welt.bauen(resale_listings=[_listing("geloescht")], vehicles=[_fahrzeug("bestand")])
    assert _fehler(z.resale.delete_listing("L1", USER)).status_code == 409
    z = welt.bauen(resale_listings=[_listing("veroeffentlicht")], vehicles=[_fahrzeug("veroeffentlicht")])
    echt = z.db.resale_listings.find_one

    async def lesen_dann_verkaufen(q, proj=None, **kw):
        doc = await echt(q, proj)
        z.db.resale_listings.one(id="L1")["status"] = "verkauft"
        return doc
    z.db.resale_listings.find_one = lesen_dann_verkaufen
    assert _fehler(z.resale.delete_listing("L1", USER)).status_code == 409
    assert z.db.resale_listings.one(id="L1")["status"] == "verkauft"
    assert z.lifecycle_schritte == []          # Fahrzeug nicht auf bestand gezogen


def test_unit_53_54_reserviertes_inserat_loeschen(welt):
    z = welt.bauen(
        resale_listings=[_listing("reserviert", reserved_for="k1")],
        vehicles=[_fahrzeug("reserviert")],
        listing_interest=[{"id": "I1", "listing_id": "L1", "status": "akzeptiert", "history": []},
                          {"id": "I2", "listing_id": "L1", "status": "gegenangebot", "history": []},
                          {"id": "I3", "listing_id": "L1", "status": "abgelehnt", "history": []},
                          {"id": "I4", "listing_id": "L9", "status": "offen", "history": []}])
    r = _run(z.resale.delete_listing("L1", USER))
    assert r["ok"] is True
    assert z.db.resale_listings.one(id="L1")["status"] == "geloescht"
    assert z.lifecycle_schritte == ["veroeffentlicht", "bestand"]
    assert z.db.vehicles.one(id="V1")["lifecycle"] == "bestand"
    for iid in ("I1", "I2"):
        it = z.db.listing_interest.one(id=iid)
        assert it["status"] == "abgelehnt" and it["beendet_grund"] == "inserat_geloescht"
        assert it["history"][-1]["aktion"] == "inserat_geloescht"
    assert z.db.listing_interest.one(id="I3")["history"] == []
    assert z.db.listing_interest.one(id="I4")["status"] == "offen"


def test_unit_53_fahrzeug_ausserhalb_verkaufsblock_bleibt_unberuehrt(welt):
    z = welt.bauen(resale_listings=[_listing("entwurf")], vehicles=[_fahrzeug("archiviert")])
    assert _run(z.resale.delete_listing("L1", USER))["ok"] is True
    assert z.lifecycle_schritte == [] and z.db.vehicles.one(id="V1")["lifecycle"] == "archiviert"


# =============================================================== set_listing_status (26, 27, 52, 54, 79, 81)
def test_unit_79_verkauft_braucht_preis(welt):
    z = welt.bauen(resale_listings=[_listing("verkaufsbereit")], vehicles=[_fahrzeug("verkaufsbereit")])
    from routes.resale import ListingStatusIn
    e = _fehler(z.resale.set_listing_status("L1", ListingStatusIn(status="verkauft"), USER))
    assert e.status_code == 400
    assert z.db.resale_listings.one(id="L1")["status"] == "verkaufsbereit"
    r = _run(z.resale.set_listing_status("L1", ListingStatusIn(status="verkauft", sold_price=8100.004), USER))
    d = z.db.resale_listings.one(id="L1")
    assert r["status"] == "verkauft" and d["sold_price"] == 8100.0 and d["sold_at"]
    assert "sold_to_user_id" not in d and "reserved_for" not in d
    assert z.db.vehicles.one(id="V1")["lifecycle"] == "verkauft"


def test_unit_27_reserviert_verkauft_kaeufer_uebernommen(welt):
    z = welt.bauen(
        resale_listings=[_listing("reserviert", reserved_for="k1")],
        vehicles=[_fahrzeug("reserviert")],
        listing_interest=[{"id": "I1", "listing_id": "L1", "status": "akzeptiert", "history": []},
                          {"id": "I2", "listing_id": "L1", "status": "offen", "history": []}])
    from routes.resale import ListingStatusIn
    _run(z.resale.set_listing_status("L1", ListingStatusIn(status="verkauft", sold_price=8100), USER))
    d = z.db.resale_listings.one(id="L1")
    assert d["status"] == "verkauft" and d["sold_to_user_id"] == "k1" and "reserved_for" not in d
    assert z.lifecycle_schritte == ["verkauft"]
    assert z.db.listing_interest.one(id="I1")["status"] == "akzeptiert"   # der Kaeufer bleibt
    assert z.db.listing_interest.one(id="I2")["status"] == "abgelehnt"    # offene enden


def test_unit_26_52_reservierung_aufheben(welt):
    z = welt.bauen(
        resale_listings=[_listing("reserviert", reserved_for="k1")],
        vehicles=[_fahrzeug("reserviert")],
        listing_interest=[{"id": "I1", "listing_id": "L1", "status": "akzeptiert", "history": []}])
    from routes.resale import ListingStatusIn
    r = _run(z.resale.set_listing_status("L1", ListingStatusIn(status="verkaufsbereit"), USER))
    assert r["status"] == "verkaufsbereit"
    d = z.db.resale_listings.one(id="L1")
    assert d["status"] == "verkaufsbereit" and "reserved_for" not in d
    assert z.lifecycle_schritte == ["veroeffentlicht", "verkaufsbereit"]
    assert z.db.vehicles.one(id="V1")["lifecycle"] == "verkaufsbereit"
    it = z.db.listing_interest.one(id="I1")
    assert it["status"] == "abgelehnt" and it["beendet_grund"] == "reservierung_aufgehoben"
    # Nr. 106: danach liefert der Entwurf-Aufruf DIESES Inserat, kein zweites
    r2 = _run(z.resale.create_draft("V1", USER))
    assert r2["id"] == "L1" and len(z.db.resale_listings.docs) == 1


def test_unit_81_desync_fahrzeug_gibt_409_inserat_unveraendert(welt):
    z = welt.bauen(resale_listings=[_listing("verkaufsbereit")], vehicles=[_fahrzeug("bestand")])
    from routes.resale import ListingStatusIn
    e = _fehler(z.resale.set_listing_status("L1", ListingStatusIn(status="reserviert"), USER))
    assert e.status_code == 409 and "Fahrzeugstatus" in e.detail
    assert z.db.resale_listings.one(id="L1")["status"] == "verkaufsbereit"
    assert z.db.vehicles.one(id="V1")["lifecycle"] == "bestand"


def test_unit_81_publish_bei_desync_409(welt):
    z = welt.bauen(resale_listings=[_listing("verkaufsbereit")], vehicles=[_fahrzeug("bestand")],
                   dealers=[{"id": "firma-1"}])
    from routes.resale import PublishIn
    e = _fehler(z.resale.publish_listing("L1", PublishIn(), USER))
    assert e.status_code == 409
    d = z.db.resale_listings.one(id="L1")
    assert d["status"] == "verkaufsbereit" and "publish_lock_until" not in d


def test_unit_107_publish_ohne_preis_400(welt):
    z = welt.bauen(resale_listings=[_listing("zurueckgezogen", prices={"public": None, "b2b": 1, "network": None})],
                   vehicles=[_fahrzeug("verkaufsbereit")], dealers=[{"id": "firma-1"}])
    from routes.resale import PublishIn
    assert _fehler(z.resale.publish_listing("L1", PublishIn(), USER)).status_code == 400
    assert z.db.resale_listings.one(id="L1")["status"] == "zurueckgezogen"


def test_unit_81_publish_synchronisiert_fahrzeug(welt):
    z = welt.bauen(resale_listings=[_listing("verkaufsbereit")], vehicles=[_fahrzeug("verkaufsbereit")],
                   dealers=[{"id": "firma-1"}])
    from routes.resale import PublishIn
    r = _run(z.resale.publish_listing("L1", PublishIn(), USER))
    assert r["status"] == "veroeffentlicht"
    assert z.db.vehicles.one(id="V1")["lifecycle"] == "veroeffentlicht"
    assert z.db.resale_listings.one(id="L1")["status"] == "veroeffentlicht"


def test_unit_54_zurueckziehen_beendet_verhandlungen(welt):
    z = welt.bauen(resale_listings=[_listing("veroeffentlicht")], vehicles=[_fahrzeug("veroeffentlicht")],
                   listing_interest=[{"id": "I1", "listing_id": "L1", "status": "gegenangebot_kaeufer", "history": []}])
    from routes.resale import ListingStatusIn
    _run(z.resale.set_listing_status("L1", ListingStatusIn(status="zurueckgezogen"), USER))
    it = z.db.listing_interest.one(id="I1")
    assert it["status"] == "abgelehnt" and it["beendet_grund"] == "inserat_zurueckgezogen"


# =============================================================== Fotos (29, 67)
@pytest.mark.parametrize("status", ["verkauft", "geloescht"])
def test_unit_67_fotos_abgeschlossener_inserate_bleiben(welt, status, monkeypatch):
    z = welt.bauen(resale_listings=[_listing(status)])
    import storage_service
    geloescht = []

    async def _nie(*a, **k):
        geloescht.append(k.get("key"))
        return True
    monkeypatch.setattr(storage_service, "loeschen_oder_vormerken", _nie)
    from routes.resale import PhotoRemoveIn
    assert _fehler(z.resale.remove_photo("L1", PhotoRemoveIn(key="resale/firma-1/a.jpg"), USER)).status_code == 400
    assert _fehler(z.resale.remove_photo("L1", PhotoRemoveIn(url="https://x/1.jpg"), USER)).status_code == 400
    p = z.db.resale_listings.one(id="L1")["photos"]
    assert p["uploaded_keys"] == ["resale/firma-1/a.jpg"] and p["einkauf_urls"] == ["https://x/1.jpg"]
    assert geloescht == []


def test_unit_67_rennen_erst_pull_dann_datei(welt, monkeypatch):
    z = welt.bauen(resale_listings=[_listing("veroeffentlicht")])
    import storage_service
    geloescht = []

    async def _merken(*a, **k):
        geloescht.append(k.get("key"))
        return True
    monkeypatch.setattr(storage_service, "loeschen_oder_vormerken", _merken)
    echt = z.db.resale_listings.find_one

    async def lesen_dann_verkaufen(q, proj=None, **kw):
        doc = await echt(q, proj)
        z.db.resale_listings.one(id="L1")["status"] = "verkauft"
        return doc
    z.db.resale_listings.find_one = lesen_dann_verkaufen
    from routes.resale import PhotoRemoveIn
    assert _fehler(z.resale.remove_photo("L1", PhotoRemoveIn(key="resale/firma-1/a.jpg"), USER)).status_code == 409
    assert geloescht == []
    assert z.db.resale_listings.one(id="L1")["photos"]["uploaded_keys"] == ["resale/firma-1/a.jpg"]


def test_unit_67_normalfall_entfernt_und_loescht(welt, monkeypatch):
    z = welt.bauen(resale_listings=[_listing("veroeffentlicht")])
    import storage_service
    geloescht = []

    async def _merken(*a, **k):
        geloescht.append(k.get("key"))
        return True
    monkeypatch.setattr(storage_service, "loeschen_oder_vormerken", _merken)
    from routes.resale import PhotoRemoveIn
    r = _run(z.resale.remove_photo("L1", PhotoRemoveIn(key="resale/firma-1/a.jpg"), USER))
    assert r["uploaded_keys"] == [] and geloescht == ["resale/firma-1/a.jpg"]


@pytest.mark.parametrize("status", ["verkauft", "geloescht"])
def test_unit_29_kein_upload_auf_abgeschlossene(welt, status):
    z = welt.bauen(resale_listings=[_listing(status)])
    from routes.resale import PhotoUploadIn
    e = _fehler(z.resale.upload_photos("L1", PhotoUploadIn(photos_b64=["abc"]), USER))
    assert e.status_code == 400
    assert z.db.resale_listings.one(id="L1")["photos"]["uploaded_keys"] == ["resale/firma-1/a.jpg"]


def test_unit_117_riesenblock_faellt_vor_dem_decode(welt, monkeypatch):
    z = welt.bauen(resale_listings=[_listing("entwurf")])
    import storage_service
    dekodiert = []
    monkeypatch.setattr(storage_service, "validate_image_bytes", lambda raw, wo="": dekodiert.append(len(raw)))
    from routes.resale import PhotoUploadIn
    gross = "A" * (storage_service.MAX_IMAGE_BYTES * 4 // 3 + 2048)
    e = _fehler(z.resale.upload_photos("L1", PhotoUploadIn(photos_b64=[gross]), USER))
    assert e.status_code == 400 and "zu gross" in e.detail
    assert dekodiert == []


# =============================================================== create_draft (45, 106)
@pytest.mark.parametrize("status", ["veroeffentlicht", "reserviert"])
def test_unit_106_aktives_inserat_blockiert_neuen_entwurf(welt, status):
    z = welt.bauen(resale_listings=[_listing(status)], vehicles=[_fahrzeug("verkaufsbereit")])
    e = _fehler(z.resale.create_draft("V1", USER))
    assert e.status_code == 409 and len(z.db.resale_listings.docs) == 1


@pytest.mark.parametrize("status", ["entwurf", "verkaufsbereit", "zurueckgezogen"])
def test_unit_106_reaktivierbares_inserat_wird_zurueckgegeben(welt, status):
    z = welt.bauen(resale_listings=[_listing(status)], vehicles=[_fahrzeug("verkaufsbereit")])
    assert _run(z.resale.create_draft("V1", USER))["id"] == "L1"
    assert len(z.db.resale_listings.docs) == 1


def test_unit_106_abgeschlossene_blockieren_nicht(welt, monkeypatch):
    z = welt.bauen(resale_listings=[_listing("verkauft", id="ALT"), _listing("geloescht", id="ALT2")],
                   vehicles=[_fahrzeug("bestand")])
    monkeypatch.setitem(sys.modules, "abholbericht", None)   # ImportError -> alter Weg
    r = _run(z.resale.create_draft("V1", USER))
    assert r["id"] not in ("ALT", "ALT2") and r["status"] == "entwurf"


def test_unit_45_massgeblicher_bericht_wird_genutzt(welt, monkeypatch):
    z = welt.bauen(resale_listings=[], vehicles=[_fahrzeug("bestand")],
                   pickup_reports=[{"vehicle_id": "V1", "dealer_id": "firma-1", "mileage_at_pickup": 123}])
    aufrufe = []

    async def massgeblicher_bericht(db, vehicle_id, dealer_id):
        aufrufe.append((vehicle_id, dealer_id))
        return {"mileage_at_pickup": 222222,
                "deviations": [{"field": "damage", "label": "Kratzer", "actual": "Tuer links"}]}
    modul = types.ModuleType("abholbericht")
    modul.massgeblicher_bericht = massgeblicher_bericht
    monkeypatch.setitem(sys.modules, "abholbericht", modul)
    r = _run(z.resale.create_draft("V1", USER))
    assert aufrufe == [("V1", "firma-1")]
    assert r["data"]["mileage"] == 222222 and "Kratzer: Tuer links" in r["known_defects"]
    assert any("222222" in n for n in r["auto_notes"])


# =============================================================== HTTP (nach Neustart)
def _kopf(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def firma():
    if not HTTP:
        pytest.skip(HTTP_GRUND)
    r = requests.post(f"{API}/auth/register", json={
        "email": f"r14_resale_{SUF}@{MAIL}", "password": PW,
        "company_name": f"R14 Resale {SUF}", "contact_person": "Chef R", "phone": "0511 4"}, timeout=30)
    assert r.status_code == 200, f"Backend braucht SELF_SIGNUP=true: {r.text[:200]}"
    u = r.json()["user"]
    z = {"kopf": _kopf(r.json()["token"]), "dealer_id": u["dealer_id"], "user_id": u["id"]}
    yield z
    dbx = _db()
    for coll in ("resale_listings", "vehicles", "listing_interest", "activity_logs",
                 "subscriptions", "pickup_reports", "storage_delete_retry"):
        dbx[coll].delete_many({"dealer_id": z["dealer_id"]})
    dbx.users.delete_many({"email": {"$regex": f"r14_resale_{SUF}@"}})
    dbx.dealers.delete_many({"id": z["dealer_id"]})


def _fahrzeug_anlegen(firma, lifecycle="bestand"):
    vid = str(uuid.uuid4())
    _db().vehicles.insert_one({
        "id": vid, "dealer_id": firma["dealer_id"], "lifecycle": lifecycle, "status": "Bestand",
        "purchase_price": 5000,
        "data": {"make_label": "VW", "model_label": f"Golf {SUF}", "mileage": 90000,
                 "first_registration": "01/2020", "fuel_label": "Benzin", "power_ps": 110, "images": []},
        "created_at": _jetzt(), "updated_at": _jetzt()})
    return vid


def _inserat(firma, bis="verkaufsbereit"):
    vid = _fahrzeug_anlegen(firma)
    r = requests.post(f"{API}/resale/draft/{vid}", headers=firma["kopf"], timeout=30)
    assert r.status_code == 200, r.text[:300]
    lid = r.json()["id"]
    if bis == "entwurf":
        return vid, lid
    assert requests.put(f"{API}/resale/{lid}", headers=firma["kopf"],
                        json={"price_public": 9900, "price_b2b": 9000}, timeout=30).status_code == 200
    assert requests.post(f"{API}/resale/{lid}/status", headers=firma["kopf"],
                         json={"status": "verkaufsbereit"}, timeout=30).status_code == 200
    if bis == "veroeffentlicht":
        r = requests.post(f"{API}/resale/{lid}/publish", headers=firma["kopf"],
                          json={"visibility": "public"}, timeout=30)
        assert r.status_code == 200, r.text[:300]
    return vid, lid


def _status(firma, lid, status, **extra):
    return requests.post(f"{API}/resale/{lid}/status", headers=firma["kopf"],
                         json={"status": status, **extra}, timeout=30)


def _lifecycle(vid):
    return _db().vehicles.find_one({"id": vid}, {"lifecycle": 1})["lifecycle"]


def _jpeg_b64():
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (200, 30, 30)).save(buf, format="JPEG")
    return base64.b64encode(buf.getvalue()).decode()


def test_http_28_29_geloescht_sperrt_alles(firma):
    if not HTTP:
        pytest.skip(HTTP_GRUND)
    vid, lid = _inserat(firma, "entwurf")
    assert requests.delete(f"{API}/resale/{lid}", headers=firma["kopf"], timeout=30).status_code == 200
    assert _lifecycle(vid) == "bestand"
    r = requests.put(f"{API}/resale/{lid}", headers=firma["kopf"],
                     json={"title": "NACH LOESCHUNG", "price_public": 1}, timeout=30)
    assert r.status_code == 400, r.text[:200]
    assert requests.get(f"{API}/resale/{lid}", headers=firma["kopf"], timeout=30).status_code == 404
    assert requests.post(f"{API}/resale/{lid}/photos", headers=firma["kopf"],
                         json={"photos_b64": [_jpeg_b64()]}, timeout=60).status_code == 400
    d = _db().resale_listings.find_one({"id": lid})
    assert d["status"] == "geloescht" and d["title"] != "NACH LOESCHUNG"
    assert d["photos"]["uploaded_keys"] == []
    # Nr. 90: doppeltes Loeschen -> 409, kein zweiter Protokolleintrag
    assert requests.delete(f"{API}/resale/{lid}", headers=firma["kopf"], timeout=30).status_code == 409
    assert _db().activity_logs.count_documents({"dealer_id": firma["dealer_id"], "action": "inserat.geloescht",
                                                "ref": lid}) == 1


def test_http_67_79_verkauf_fotos_bleiben(firma):
    if not HTTP:
        pytest.skip(HTTP_GRUND)
    vid, lid = _inserat(firma)
    r = requests.post(f"{API}/resale/{lid}/photos", headers=firma["kopf"],
                      json={"photos_b64": [_jpeg_b64()]}, timeout=60)
    assert r.status_code == 200, r.text[:300]
    key = _db().resale_listings.find_one({"id": lid})["photos"]["uploaded_keys"][0]
    # Nr. 79: ohne Preis kein Verkauf
    assert _status(firma, lid, "verkauft").status_code == 400
    assert _db().resale_listings.find_one({"id": lid})["status"] == "verkaufsbereit"
    assert _status(firma, lid, "verkauft", sold_price=8100.004).status_code == 200
    d = _db().resale_listings.find_one({"id": lid})
    assert d["sold_price"] == 8100.0 and _lifecycle(vid) == "verkauft"
    # Nr. 67: Fotos verkaufter Inserate bleiben
    r = requests.post(f"{API}/resale/{lid}/photos/remove", headers=firma["kopf"], json={"key": key}, timeout=30)
    assert r.status_code == 400, r.text[:200]
    assert _db().resale_listings.find_one({"id": lid})["photos"]["uploaded_keys"] == [key]
    assert requests.post(f"{API}/resale/{lid}/photos", headers=firma["kopf"],
                         json={"photos_b64": [_jpeg_b64()]}, timeout=60).status_code == 400
    assert requests.put(f"{API}/resale/{lid}", headers=firma["kopf"], json={"title": "x"}, timeout=30).status_code == 400
    # Nr. 80: Unendlich als Verkaufspreis
    vid2, lid2 = _inserat(firma)
    r = requests.post(f"{API}/resale/{lid2}/status",
                      headers={**firma["kopf"], "Content-Type": "application/json"},
                      data='{"status": "verkauft", "sold_price": Infinity}',
                      timeout=30)
    assert r.status_code == 422


def test_http_80_unendliche_preise_422_liste_bleibt(firma):
    if not HTTP:
        pytest.skip(HTTP_GRUND)
    vid, lid = _inserat(firma, "entwurf")
    for body in ('{"price_public": 1e400}', '{"price_b2b": Infinity}', '{"price_network": NaN}'):
        r = requests.put(f"{API}/resale/{lid}", headers={**firma["kopf"], "Content-Type": "application/json"},
                         data=body, timeout=30)
        assert r.status_code == 422, (body, r.status_code, r.text[:200])
    assert requests.get(f"{API}/resale", headers=firma["kopf"], timeout=30).status_code == 200
    assert requests.get(f"{API}/resale/{lid}", headers=firma["kopf"], timeout=30).status_code == 200


def test_http_26_52_106_reservierung_aufheben(firma):
    if not HTTP:
        pytest.skip(HTTP_GRUND)
    vid, lid = _inserat(firma)
    assert _status(firma, lid, "reserviert").status_code == 200
    assert _lifecycle(vid) == "reserviert"
    dbx = _db()
    dbx.resale_listings.update_one({"id": lid}, {"$set": {"reserved_for": "kaeufer-x"}})
    dbx.listing_interest.insert_one({"id": f"i_{SUF}_a", "listing_id": lid, "dealer_id": firma["dealer_id"],
                                     "buyer_user_id": "kaeufer-x", "status": "akzeptiert", "history": [],
                                     "created_at": _jetzt(), "updated_at": _jetzt()})
    # Nr. 108: waehrend der Reservierung Preis/Daten/Maengel gesperrt, Beschreibung frei
    assert requests.put(f"{API}/resale/{lid}", headers=firma["kopf"], json={"price_public": 19999}, timeout=30).status_code == 400
    assert requests.put(f"{API}/resale/{lid}", headers=firma["kopf"], json={"data": {"mileage": 250000}}, timeout=30).status_code == 400
    assert requests.put(f"{API}/resale/{lid}", headers=firma["kopf"], json={"description": "frei"}, timeout=30).status_code == 200
    assert _status(firma, lid, "verkaufsbereit").status_code == 200
    d = dbx.resale_listings.find_one({"id": lid})
    assert d["status"] == "verkaufsbereit" and "reserved_for" not in d
    assert _lifecycle(vid) == "verkaufsbereit"                      # Nr. 52
    it = dbx.listing_interest.find_one({"id": f"i_{SUF}_a"})
    assert it["status"] == "abgelehnt" and it["beendet_grund"] == "reservierung_aufgehoben"   # Nr. 26
    # Nr. 106: Entwurf erneut -> dasselbe Inserat
    r = requests.post(f"{API}/resale/draft/{vid}", headers=firma["kopf"], timeout=30)
    assert r.status_code == 200 and r.json()["id"] == lid
    assert dbx.resale_listings.count_documents({"vehicle_id": vid, "status": {"$ne": "geloescht"}}) == 1
    # publish danach ohne reserved_for
    r = requests.post(f"{API}/resale/{lid}/publish", headers=firma["kopf"], json={"visibility": "public"}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    assert "reserved_for" not in dbx.resale_listings.find_one({"id": lid})
    assert _lifecycle(vid) == "veroeffentlicht"


def test_http_27_reserviert_verkauft_kaeufer_festgehalten(firma):
    if not HTTP:
        pytest.skip(HTTP_GRUND)
    vid, lid = _inserat(firma)
    assert _status(firma, lid, "reserviert").status_code == 200
    _db().resale_listings.update_one({"id": lid}, {"$set": {"reserved_for": "kaeufer-y"}})
    assert _status(firma, lid, "verkauft", sold_price=8100).status_code == 200
    d = _db().resale_listings.find_one({"id": lid})
    assert d["sold_to_user_id"] == "kaeufer-y" and "reserved_for" not in d
    assert _lifecycle(vid) == "verkauft"


def test_http_53_54_reserviertes_inserat_loeschen(firma):
    if not HTTP:
        pytest.skip(HTTP_GRUND)
    vid, lid = _inserat(firma, "veroeffentlicht")
    dbx = _db()
    dbx.listing_interest.insert_one({"id": f"i_{SUF}_b", "listing_id": lid, "dealer_id": firma["dealer_id"],
                                     "buyer_user_id": "kaeufer-z", "status": "gegenangebot", "history": [],
                                     "created_at": _jetzt(), "updated_at": _jetzt()})
    assert _status(firma, lid, "reserviert").status_code == 200
    assert _lifecycle(vid) == "reserviert"
    r = requests.delete(f"{API}/resale/{lid}", headers=firma["kopf"], timeout=30)
    assert r.status_code == 200, r.text[:200]
    assert dbx.resale_listings.find_one({"id": lid})["status"] == "geloescht"
    assert _lifecycle(vid) == "bestand"                             # nie geloescht + reserviert
    it = dbx.listing_interest.find_one({"id": f"i_{SUF}_b"})
    assert it["status"] == "abgelehnt" and it["beendet_grund"] == "inserat_geloescht"
    # Fahrzeug ist wieder inserierbar
    assert requests.post(f"{API}/resale/draft/{vid}", headers=firma["kopf"], timeout=30).status_code == 200


def test_http_81_desync_publish_und_status_409(firma):
    if not HTTP:
        pytest.skip(HTTP_GRUND)
    vid, lid = _inserat(firma)
    _db().vehicles.update_one({"id": vid}, {"$set": {"lifecycle": "bestand"}})
    r = requests.post(f"{API}/resale/{lid}/publish", headers=firma["kopf"], json={"visibility": "public"}, timeout=30)
    assert r.status_code == 409, r.text[:200]
    d = _db().resale_listings.find_one({"id": lid})
    assert d["status"] == "verkaufsbereit" and _lifecycle(vid) == "bestand"
    assert "publish_lock_until" not in d
    assert _status(firma, lid, "reserviert").status_code == 409
    assert _db().resale_listings.find_one({"id": lid})["status"] == "verkaufsbereit"


def test_http_106_zurueckgezogen_kein_zweites_inserat(firma):
    if not HTTP:
        pytest.skip(HTTP_GRUND)
    vid, lid = _inserat(firma, "veroeffentlicht")
    assert requests.post(f"{API}/resale/draft/{vid}", headers=firma["kopf"], timeout=30).status_code == 409
    assert _status(firma, lid, "zurueckgezogen").status_code == 200
    r = requests.post(f"{API}/resale/draft/{vid}", headers=firma["kopf"], timeout=30)
    assert r.status_code == 200 and r.json()["id"] == lid
    assert _db().resale_listings.count_documents({"vehicle_id": vid, "status": {"$ne": "geloescht"}}) == 1


def test_http_107_preis_null_und_publish_ohne_preis(firma):
    if not HTTP:
        pytest.skip(HTTP_GRUND)
    vid, lid = _inserat(firma, "veroeffentlicht")
    assert requests.put(f"{API}/resale/{lid}", headers=firma["kopf"], json={"price_public": 0}, timeout=30).status_code == 400
    assert _db().resale_listings.find_one({"id": lid})["prices"]["public"] == 9900.0
    assert _status(firma, lid, "zurueckgezogen").status_code == 200
    _db().resale_listings.update_one({"id": lid}, {"$set": {"prices.public": None}})
    r = requests.post(f"{API}/resale/{lid}/publish", headers=firma["kopf"], json={"visibility": "public"}, timeout=30)
    assert r.status_code == 400, r.text[:200]
    assert _db().resale_listings.find_one({"id": lid})["status"] == "zurueckgezogen"


def test_http_117_foto_riesenstring_422(firma):
    if not HTTP:
        pytest.skip(HTTP_GRUND)
    vid, lid = _inserat(firma, "entwurf")
    r = requests.post(f"{API}/resale/{lid}/photos", headers=firma["kopf"],
                      json={"photos_b64": ["A" * 12_000_001]}, timeout=120)
    assert r.status_code in (422, 413), r.status_code
    assert _db().resale_listings.find_one({"id": lid})["photos"]["uploaded_keys"] == []
