# -*- coding: utf-8 -*-
"""Nachpruefung Nr. 144-165 (19.09.2026).

Drei Themen, die alle dasselbe Muster haben: Eine Berechtigung wurde EINMAL
geprueft — beim Einreihen, beim Anmelden, beim Anzeigen — und danach nie
wieder, obwohl zwischen Pruefung und Wirkung Minuten oder Monate liegen.

  A  Zugang (144, 145, 164): Die Sucher-Funktionen haengen am Abo. Diese
     Sperre kannte Firmenexistenz und Loeschsperre nicht.
  B  Warteschlange (146-152, 165): Der Worker ruft spaeter extern ab —
     ohne zu pruefen, ob Konto, Abo und Firma das noch erlauben.
  C  Zwischenspeicher/Beweis (153-155) und Fahrer-Unterlagen (156-163).
"""
import asyncio
import inspect
import os
import sys
import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import deps  # noqa: E402
import link_jobs as LJ  # noqa: E402

MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
DB_NAME = os.environ.get("DB_NAME") or "autoschnell"


@pytest.fixture
def welt():
    """Eigener Event-Loop + echte Datenbank (wie test_beweis_service.welt);
    pytest-asyncio ist in diesem Projekt nicht im Einsatz."""
    from motor.motor_asyncio import AsyncIOMotorClient

    class _W:
        pass

    w = _W()
    w.s = uuid.uuid4().hex[:8]
    w.loop = asyncio.new_event_loop()
    asyncio.set_event_loop(w.loop)
    w.client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    w.db = w.client[DB_NAME]
    w.run = lambda coro: w.loop.run_until_complete(coro)
    yield w
    try:
        w.run(w.db.link_jobs.delete_many({"id": {"$regex": f"^job_{w.s}"}}))
        w.run(w.db.users.delete_many({"id": {"$regex": f"^u_{w.s}"}}))
    finally:
        w.client.close()
        w.loop.close()


# ------------------------------------------------------------------ A
def test_144_abo_sperre_haengt_an_der_firma():
    """144/145/164: `require_active_sub` baut jetzt auf `current_firma` auf —
    damit gelten Firmendokument und Loeschsperre automatisch fuer Vergleich,
    Linkpruefung, resolve, ingest und die Vertragswege."""
    unterschrift = inspect.signature(deps.require_active_sub)
    vorgabe = unterschrift.parameters["user"].default
    assert getattr(vorgabe, "dependency", None) is deps.current_firma, \
        "Abo-Sperre haengt wieder direkt an current_user"


def test_144b_current_firma_prueft_loeschung_und_existenz():
    q = inspect.getsource(deps.current_firma)
    assert "db.dealers.find_one" in q and 'loeschung' in q


# ------------------------------------------------------------------ B
class _FakeCursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def __aiter__(self):
        async def gen():
            for d in self._docs:
                yield d
        return gen()


class _FakeColl:
    """Nur so viel Mongo, wie wartender_darf_abrufen braucht."""

    def __init__(self, docs):
        self.docs = docs

    async def find_one(self, filt, *a, **kw):
        return self.docs.get(filt.get("id"))


class _FakeDB:
    def __init__(self, users, dealers):
        self.users = _FakeColl(users)
        self.dealers = _FakeColl(dealers)


def _welt(**abweichung):
    user = {"id": "u1", "role": "sucher", "dealer_id": "d1", "active": True}
    user.update(abweichung.pop("user", {}))
    firma = {"id": "d1"}
    firma.update(abweichung.pop("firma", {}))
    return _FakeDB({"u1": user}, {"d1": firma})


def _job(**abweichung):
    job = {"id": "j1", "requested_by_user": "u1", "requested_by_dealer": "d1",
           "user_ids": ["u1"], "dealer_ids": ["d1"]}
    job.update(abweichung)
    return job


def _abo(aktiv=True):
    async def _f(_user):
        return {"active": aktiv}
    return _f


def test_146_gesperrtes_konto_ruft_nicht_mehr_ab(monkeypatch):
    monkeypatch.setattr(deps, "subscription_for", _abo(True))
    db = _welt(user={"active": False})
    assert asyncio.run(LJ.wartender_darf_abrufen(db, "u1", _job())) is None


def test_147_abgelaufenes_abo_stoppt_den_abruf(monkeypatch):
    monkeypatch.setattr(deps, "subscription_for", _abo(False))
    assert asyncio.run(LJ.wartender_darf_abrufen(_welt(), "u1", _job())) is None


def test_148_firma_in_loeschung_stoppt_den_abruf(monkeypatch):
    monkeypatch.setattr(deps, "subscription_for", _abo(True))
    db = _welt(firma={"loeschung": {"status": "laeuft"}})
    assert asyncio.run(LJ.wartender_darf_abrufen(db, "u1", _job())) is None
    # Firma ganz weg (verwaistes Konto, Befund 145) ebenso
    db2 = _FakeDB({"u1": {"id": "u1", "role": "sucher", "dealer_id": "d1",
                          "active": True}}, {})
    assert asyncio.run(LJ.wartender_darf_abrufen(db2, "u1", _job())) is None


def test_149_firmenwechsel_bucht_nicht_auf_die_neue_firma(monkeypatch):
    """Der Job wurde unter Firma d1 eingereiht. Wandert der Sucher zu d9,
    darf sein Abruf nicht d9 angelastet werden."""
    monkeypatch.setattr(deps, "subscription_for", _abo(True))
    db = _welt(user={"dealer_id": "d9"}, firma={"id": "d1"})
    assert asyncio.run(LJ.wartender_darf_abrufen(db, "u1", _job())) is None
    # Gegenprobe: unveraenderte Firma geht durch und wird zurueckgegeben
    assert asyncio.run(LJ.wartender_darf_abrufen(_welt(), "u1", _job())) == "d1"


def test_150_151_abbrechen_raeumt_einreicher_und_firma(welt):
    """150: Der Abbrecher bleibt sonst erster Kandidat fuer den Abruf.
    151: Seine Firma zaehlt sonst weiter gegen deren Warteschlangen-Grenze."""
    a, b = f"u_{welt.s}_A", f"u_{welt.s}_B"
    welt.run(welt.db.users.insert_many([
        {"id": a, "dealer_id": "dA", "role": "sucher", "active": True},
        {"id": b, "dealer_id": "dB", "role": "sucher", "active": True}]))
    welt.run(welt.db.link_jobs.insert_one({
        "id": f"job_{welt.s}_x", "cache_key": f"mobile:x{welt.s}",
        "status": "queued", "active": True,
        "requested_by_user": a, "requested_by_dealer": "dA",
        "user_ids": [a, b], "dealer_ids": ["dA", "dB"]}))
    erg = welt.run(LJ.warten_beenden(welt.db, f"job_{welt.s}_x", "dA", a))
    assert erg["status"] == "laeuft_weiter"
    job = welt.run(welt.db.link_jobs.find_one({"id": f"job_{welt.s}_x"}, {"_id": 0}))
    assert job["user_ids"] == [b]
    assert job["requested_by_user"] == b, "Abbrecher bleibt erster Kandidat"
    assert job["requested_by_dealer"] == "dB"
    assert job["dealer_ids"] == ["dB"], "Firma des Abbrechers zaehlt weiter"


def test_151b_firma_bleibt_wenn_noch_jemand_von_dort_wartet(welt):
    c, d = f"u_{welt.s}_C", f"u_{welt.s}_D"
    welt.run(welt.db.users.insert_many([
        {"id": c, "dealer_id": "dC", "role": "sucher", "active": True},
        {"id": d, "dealer_id": "dC", "role": "sucher", "active": True}]))
    welt.run(welt.db.link_jobs.insert_one({
        "id": f"job_{welt.s}_y", "cache_key": f"mobile:y{welt.s}",
        "status": "queued", "active": True,
        "requested_by_user": c, "requested_by_dealer": "dC",
        "user_ids": [c, d], "dealer_ids": ["dC"]}))
    welt.run(LJ.warten_beenden(welt.db, f"job_{welt.s}_y", "dC", c))
    job = welt.run(welt.db.link_jobs.find_one({"id": f"job_{welt.s}_y"}, {"_id": 0}))
    assert job["dealer_ids"] == ["dC"] and job["requested_by_user"] == d


def test_152_bremse_probiert_den_naechsten_wartenden():
    q = inspect.getsource(LJ._process)
    assert "except AbrufGebremst" in q, \
        "60/min bzw. 8 gleichzeitig legen weiter den ganzen gemeinsamen Job still"
    assert "raise letzte or gebremst" in q


def test_146b_worker_prueft_vor_jedem_abruf():
    q = inspect.getsource(LJ._process)
    assert "wartender_darf_abrufen" in q
    assert "KeinBerechtigterWartender" in q


def test_165_firmenloeschung_raeumt_die_warteschlange(welt):
    """148/165: Waehrend der Loeschung kann der Sucher nicht mehr abbrechen —
    also muss die Loeschung selbst aufraeumen."""
    e, f = f"u_{welt.s}_E", f"u_{welt.s}_F"
    firma = f"dE_{welt.s}"
    welt.run(welt.db.users.insert_one(
        {"id": e, "dealer_id": firma, "role": "sucher", "active": False}))
    welt.run(welt.db.link_jobs.insert_many([
        {"id": f"job_{welt.s}_e1", "cache_key": f"mobile:e1{welt.s}",
         "status": "queued", "active": True, "requested_by_user": e,
         "requested_by_dealer": firma, "user_ids": [e], "dealer_ids": [firma]},
        {"id": f"job_{welt.s}_e2", "cache_key": f"mobile:e2{welt.s}",
         "status": "queued", "active": True, "requested_by_user": e,
         "requested_by_dealer": firma, "user_ids": [e, f],
         "dealer_ids": [firma, "dF"]}]))
    weg = welt.run(LJ.firma_austragen(welt.db, firma))
    assert weg == 1
    assert welt.run(welt.db.link_jobs.find_one({"id": f"job_{welt.s}_e1"})) is None
    geteilt = welt.run(welt.db.link_jobs.find_one({"id": f"job_{welt.s}_e2"}, {"_id": 0}))
    assert geteilt["user_ids"] == [f] and geteilt["dealer_ids"] == ["dF"]


def test_165b_loeschkaskade_ruft_das_aufraeumen_auf():
    quelle = (Path(__file__).resolve().parents[1] / "routes" / "admin.py").read_text(
        encoding="utf-8")
    assert "firma_austragen" in quelle


# ------------------------------------------------------------------ C
def test_153_lease_verlierer_gibt_seine_daten_nicht_mehr_heraus():
    import listing_identity as LI
    q = inspect.getsource(LI.get_or_fetch_listing)
    # Nur der Zweig "Lease verloren" — das reguläre return am Ende bleibt.
    stelle = q.split("Lease waehrend des Abrufs verloren")[1].split("# Beweisdokument")[0]
    assert "return data, False, None" not in stelle, \
        "Der Verlierer arbeitet weiter mit seinem verworfenen Stand"
    assert "gewinner" in stelle and "raise ListingBusy" in stelle


def test_154_155_beweis_nimmt_nur_den_rohen_inseratsstand():
    quelle = (Path(__file__).resolve().parents[1] / "routes" / "beweise.py").read_text(
        encoding="utf-8")
    anfordern = quelle.split("async def beweis_anfordern")[1].split("\n@router")[0]
    assert '(fahrzeug or {}).get("data")' not in anfordern, \
        "Haendlerlokal korrigierte Daten werden zum gemeinsamen Beweis (155)"
    assert "_ist_neuer" in anfordern and "hinweis" in anfordern, \
        "Neuerer Stand als der Vergleich wird nicht kenntlich gemacht (154)"


def test_154b_vergleich_merkt_sich_den_stand():
    quelle = (Path(__file__).resolve().parents[1] / "routes" / "listings.py").read_text(
        encoding="utf-8")
    assert 'quelle_set["inserat_stand_am"]' in quelle


def test_154c_ist_neuer_rechnet_richtig():
    from datetime import datetime, timedelta, timezone
    from routes.beweise import _ist_neuer
    jetzt = datetime.now(timezone.utc)
    assert _ist_neuer(jetzt, (jetzt - timedelta(hours=2)).isoformat()) is True
    assert _ist_neuer(jetzt - timedelta(hours=2), jetzt.isoformat()) is False
    assert _ist_neuer(None, jetzt.isoformat()) is False
    assert _ist_neuer(jetzt, "") is False
    assert _ist_neuer(jetzt, "kein Datum") is False


@pytest.mark.parametrize("status,tage,erwartet", [
    ("abgeholt", 3, False),      # innerhalb der 14 Tage
    ("abgeholt", 20, True),      # danach gesperrt
    ("nicht abgeholt", 20, False),   # 30-Tage-Fenster laeuft noch
    ("nicht abgeholt", 40, True),
    ("offen", 400, False),       # offene Fahrten nie sperren
])
def test_156_unterlagen_nur_solange_die_fahrt_sichtbar_ist(status, tage, erwartet):
    from datetime import datetime, timedelta, timezone
    from routes.drivers import unterlagen_zugriff_oder_404
    appt = {"status": status,
            "abgeschlossen_seit": (datetime.now(timezone.utc)
                                   - timedelta(days=tage)).isoformat()}
    if erwartet:
        with pytest.raises(HTTPException) as e:
            unterlagen_zugriff_oder_404(appt)
        assert e.value.status_code == 404
    else:
        unterlagen_zugriff_oder_404(appt)


def test_157_stornierte_fahrt_gibt_keine_unterlagen():
    from routes.drivers import unterlagen_zugriff_oder_404
    with pytest.raises(HTTPException) as e:
        unterlagen_zugriff_oder_404({"status": "storniert"})
    assert e.value.status_code == 404
    quelle = (Path(__file__).resolve().parents[1] / "routes" / "drivers.py").read_text(
        encoding="utf-8")
    auftrag = quelle.split("async def driver_pickup_order_pdf")[1].split("\n@router")[0]
    assert "unterlagen_zugriff_oder_404" in auftrag, \
        "Abholauftrag mit Verkaeuferanschrift bei stornierter Fahrt (157)"


def test_158_159_snapshot_und_bericht_verlangen_eine_angenommene_fahrt():
    quelle = (Path(__file__).resolve().parents[1] / "routes" / "drivers.py").read_text(
        encoding="utf-8")
    snap = quelle.split("async def driver_snapshot")[1].split("\n@router")[0]
    assert "ZUTEILUNG_ANGENOMMEN" in snap and "unterlagen_zugriff_oder_404" in snap
    bericht = quelle.split("async def driver_get_report")[1].split("\n@router")[0]
    assert "zuteilung_offen_oder_409" in bericht and "unterlagen_zugriff_oder_404" in bericht


def test_160_161_fahrer_beweis_ohne_deckel_und_mit_echter_annahme():
    quelle = (Path(__file__).resolve().parents[1] / "routes" / "beweise.py").read_text(
        encoding="utf-8")
    fahrer = quelle.split("async def driver_beweis_pdf")[1]
    assert ".limit(50)" not in fahrer, "50-Fahrzeuge-Deckel sperrt echte Termine aus (161)"
    assert '"$nin": ["offen", "abgelehnt"]' not in fahrer, \
        "unbekannte Alt-Zuteilungen kommen durch (160)"
    assert "ZUTEILUNG_ANGENOMMEN" in fahrer and "unterlagen_zugriff_oder_404" in fahrer


def test_162_datumslose_fahrten_werden_sortiert_gekappt():
    quelle = (Path(__file__).resolve().parents[1] / "routes" / "drivers.py").read_text(
        encoding="utf-8")
    stelle = quelle.split('"pickup_date": {"$in": ["", None]}')[1][:200]
    assert ".sort(" in stelle, "Bei >500 offenen Fahrten entscheidet die Speicherreihenfolge"


def test_163_fotocleanup_verspricht_keinen_deckel_mehr():
    """163 anders geloest: Der Deckel wurde am 14.09.2026 (Runde 10, 3.4)
    bewusst entfernt — mit ihm blieb bei grossem Rueckstand jeden Lauf ein
    Rest liegen. Statt ihn wieder einzubauen, ist der irrefuehrende Satz weg;
    der Lauf arbeitet vollstaendig, aber in Stapeln."""
    import cleanup_service as CS
    q = inspect.getsource(CS.berichtsfotos_nach_frist_loeschen)
    assert "hoechstens 500 je Lauf, der Rest folgt stuendlich" not in q
    assert ".batch_size(200)" in q and ".limit(" not in q
