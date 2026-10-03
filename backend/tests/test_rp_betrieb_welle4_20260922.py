# -*- coding: utf-8 -*-
"""Rollenprüfung 22.09.2026 (Review) — Team Betrieb, vierte Welle: Befunde
der Gegenpruefung nach den drei Fix-Wellen.

  * Automatisches Inseratsende (21 Tage) und Nachholer setzen ein noch nicht
    abgeholtes Fahrzeug NICHT mehr auf "bestand" (RP-518-Rueckweg).
  * Alarm mehrfache_aktive_firmen_abos aus Migration 11 bleibt offen, bis der
    Altbestand bereinigt ist.
  * Sicherungsskripte laufen mit eigenen S3-Zeitlimits (nicht 2 Versuche/30 s).
  * Runbook "prod2 laenger weg": Ruecknahme ueber rs.reconfigForPSASet.
  * Nachholer "Vertrag nach Abholung" schliesst den v1-Alarm, wenn die
    aktuelle Version nichts Neues traegt.

In-Prozess gegen Wegwerf-Datenbanken (autoschnell_rpb4_<zufall>), kein
Server, kein `import server`. Audit-Eintraege werden abgefangen (sonst
landeten sie in der globalen Test-Datenbank).
"""
import asyncio
import importlib
import os
import re
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

BACKEND = Path(__file__).resolve().parents[1]
WURZEL = BACKEND.parent
SCRIPTS = BACKEND / "scripts"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(SCRIPTS))
MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"


def _iso(delta_s: float = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=delta_s)).isoformat()


def _tage(n: float) -> str:
    return _iso(n * 86400)


def _m(name):
    return importlib.import_module(name)


@pytest.fixture
def welt():
    """Asynchrone Wegwerf-Datenbank (Motor) mit eigenem Event-Loop."""
    from motor.motor_asyncio import AsyncIOMotorClient
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    name = f"autoschnell_rpb4_{uuid.uuid4().hex[:10]}"
    w = SimpleNamespace(db=client[name], run=loop.run_until_complete, name=name)
    try:
        yield w
    finally:
        try:
            loop.run_until_complete(client.drop_database(name))
        finally:
            client.close()
            loop.close()


@pytest.fixture
def audit(monkeypatch):
    """Audit-Eintraege abfangen statt in die globale Datenbank zu schreiben."""
    deps = _m("deps")
    eintraege = []

    async def _log(dealer_id, user_id, action, ref=None, meta=None):
        eintraege.append({"action": action, "ref": ref, "meta": meta or {}})
        return True
    monkeypatch.setattr(deps, "log_activity_sicher", _log)
    return eintraege


def _alarm_offen(w, typ, ref):
    return w.run(w.db.betriebsalarme.count_documents({"typ": typ, "ref": ref, "offen": True}))


# ================================================ Inseratsende vor der Abholung (RP-518)
def _inserat(w, lid, vid, status="veroeffentlicht", tage=-25):
    w.run(w.db.resale_listings.insert_one({
        "id": lid, "dealer_id": "d1", "vehicle_id": vid, "status": status,
        "photos": {"uploaded_keys": []}, "published_at": _tage(tage)}))


def _kauf(w, vid, status):
    w.run(w.db.kaufvorgaenge.insert_one({"id": f"k_{vid}", "dealer_id": "d1",
                                         "vehicle_id": vid, "status": status}))


def test_inseratsende_vor_abholung_geht_in_den_kaufzustand(welt, audit):
    CS, L = _m("cleanup_service"), _m("lifecycle")
    w = welt
    alte_frist = _tage(-100)
    w.run(w.db.vehicles.insert_one({"id": "v1", "dealer_id": "d1",
                                    "lifecycle": "veroeffentlicht",
                                    "bestand": {"expires_at": alte_frist}}))
    _kauf(w, "v1", "abholung_geplant")
    _inserat(w, "l1", "v1")
    assert w.run(CS.abgelaufene_inserate_entfernen(w.db, datetime.now(timezone.utc))) == 1
    v = w.run(w.db.vehicles.find_one({"id": "v1"}, {"_id": 0}))
    assert v["lifecycle"] == "abholung_geplant", "nicht mehr 'bestand' vor der Abholung"
    assert v["bestand"]["expires_at"] == alte_frist, "keine neue Bestandsfrist"
    assert "inserat_beendet_am" not in v["bestand"]
    # "nicht abgeholt" greift danach wieder (aus "bestand" ging es nicht)
    assert "nicht_abgeholt" in L.ALLOWED_TRANSITIONS[v["lifecycle"]]
    # Audit haelt jeden Schritt des Rueckwegs fest
    schritte = [(e["meta"]["von"], e["meta"]["nach"]) for e in audit
                if e["action"].startswith("fahrzeug.status.")]
    assert schritte == [("veroeffentlicht", "verkaufsbereit"),
                        ("verkaufsbereit", "abholung_geplant")], schritte
    assert w.run(w.db.resale_listings.find_one({"id": "l1"})) is None, "Anzeige weg"


def test_nachholer_ohne_inserat_geht_bei_offenem_vertrag_nach_gekauft(welt, audit):
    CS = _m("cleanup_service")
    w = welt
    alt = _tage(-2)
    w.run(w.db.vehicles.insert_many([
        # reserviert, Vertrag erstellt, nichts abgeholt -> gekauft
        {"id": "va", "dealer_id": "d1", "lifecycle": "reserviert", "lifecycle_changed_at": alt},
        # abgeholt -> wie bisher Bestand mit frischer Frist
        {"id": "vb", "dealer_id": "d1", "lifecycle": "veroeffentlicht",
         "lifecycle_changed_at": alt, "abgeholt_kaufvorgang_id": "k_vb"},
        # Preis am Fahrzeug (Abholung gebucht) -> Bestand
        {"id": "vc", "dealer_id": "d1", "lifecycle": "veroeffentlicht",
         "lifecycle_changed_at": alt, "purchase_price": 5000},
        # gar kein Kauf -> Bestand
        {"id": "vd", "dealer_id": "d1", "lifecycle": "veroeffentlicht",
         "lifecycle_changed_at": alt},
    ]))
    _kauf(w, "va", "vertrag_erstellt")
    _kauf(w, "vb", "abgeholt")
    _kauf(w, "vc", "abholung_geplant")
    n = w.run(CS.fahrzeuge_ohne_inserat_zuruecksetzen(w.db, datetime.now(timezone.utc)))
    assert n == 4
    stand = {v["id"]: v for v in w.run(w.db.vehicles.find({}, {"_id": 0}).to_list(20))}
    assert stand["va"]["lifecycle"] == "gekauft"
    assert "bestand" not in stand["va"], "keine Bestandsfrist fuer ein nicht abgeholtes Auto"
    for vid in ("vb", "vc", "vd"):
        assert stand[vid]["lifecycle"] == "bestand", vid
        assert stand[vid]["bestand"]["expires_at"] > _tage(45), vid


def test_rueckweg_nutzt_die_uebergebene_datenbank():
    """Die Zielbestimmung liest ueber die Datenbank des Aufraeumlaufs (nicht
    ueber die globale deps.db wie routes.resale._zustand_vor_abholung) und
    folgt derselben Regel."""
    import inspect
    CS = _m("cleanup_service")
    q = inspect.getsource(CS._fahrzeug_nach_inseratsende_zuruecksetzen)
    assert "_zustand_vor_abholung_in(db," in q and "_pfad_zurueck(" in q
    regel = inspect.getsource(CS._zustand_vor_abholung_in)
    for stueck in ('"abholung_geplant"', '"gekauft"', '"vertrag_erstellt"', '"gesendet"',
                   "abgeholt_kaufvorgang_id", "purchase_price"):
        assert stueck in regel, stueck


# ================================================ Firmen-Abo-Alarm (RP-145)
def _firmen_alarm(w):
    return w.run(w.db.betriebsalarme.find_one(
        {"typ": "mehrfache_aktive_firmen_abos", "ref": "subscriptions", "offen": True}))


def test_migrationsalarm_bleibt_nach_erneutem_indexlauf_offen(welt):
    I, M = _m("indizes"), _m("migrationen")
    w = welt
    # erster Rollout: Index entsteht vor der Migration (noch keine Dubletten)
    assert w.run(I.firmen_abo_unique_index(w.db)) is True
    w.run(w.db.subscriptions.insert_many([
        {"id": "alt1", "dealer_id": "d1", "status": "active"},
        {"id": "alt2", "dealer_id": "d1", "status": "active"},
        {"id": "ok", "dealer_id": "d2", "status": "active"},
    ]))
    assert w.run(M.m11_firmen_abo_art(w.db))["konflikte"] == 1
    assert _firmen_alarm(w)
    # wartende Worker / Start von prod1 rufen die Indexe erneut auf
    assert w.run(I.firmen_abo_unique_index(w.db)) is False
    a = _firmen_alarm(w)
    assert a, "der Migrationsalarm darf nicht sofort wieder zugehen"
    assert "d1" in a["details"]["beispiele"] and "d2" not in a["details"]["beispiele"]
    assert "ein_aktives_firmen_abo_je_firma" in w.run(w.db.subscriptions.index_information()), \
        "ein schon stehender Index bleibt"
    # Betreiber bereinigt das unmarkierte Altabo -> Alarm zu
    unmarkiert = w.run(w.db.subscriptions.find_one({"dealer_id": "d1", "art": {"$exists": False}}))
    w.run(w.db.subscriptions.update_one({"_id": unmarkiert["_id"]},
                                        {"$set": {"status": "ersetzt"}}))
    assert w.run(I.firmen_abo_unique_index(w.db)) is True
    assert _firmen_alarm(w) is None


def test_altdubletten_vor_dem_ersten_indexlauf_melden_ohne_index(welt):
    I, M = _m("indizes"), _m("migrationen")
    w = welt
    w.run(w.db.subscriptions.insert_many([
        {"id": "alt1", "dealer_id": "d1", "status": "active"},
        {"id": "alt2", "dealer_id": "d1", "status": "active", "subject_user_id": None},
        {"id": "pers", "dealer_id": "d1", "status": "active", "subject_user_id": "u1"},
    ]))
    assert w.run(I.firmen_abo_unique_index(w.db)) is False
    assert _firmen_alarm(w)
    assert "ein_aktives_firmen_abo_je_firma" not in w.run(w.db.subscriptions.index_information())
    assert "ein_aktives_firmen_abo_je_firma" not in I.FEHLENDE_UNIQUE, "kein Startabbruch"
    stats = w.run(M.m11_firmen_abo_art(w.db))
    assert stats["aktiv_markiert"] == 2 and stats["konflikte"] == 0
    assert w.run(I.firmen_abo_unique_index(w.db)) is False
    assert _firmen_alarm(w), "zwei markierte Firmen-Abos: Alarm bleibt"
    w.run(w.db.subscriptions.update_one({"id": "alt1"}, {"$set": {"status": "ersetzt"}}))
    assert w.run(I.firmen_abo_unique_index(w.db)) is True
    assert _firmen_alarm(w) is None
    # persoenliche Abos zaehlen nie als Firmen-Abo
    w.run(w.db.subscriptions.insert_one({"id": "pers2", "dealer_id": "d1",
                                         "status": "active", "subject_user_id": "u2"}))
    assert w.run(I.firmen_abo_unique_index(w.db)) is True


# ================================================ S3-Zeitlimits der Sicherung (RP-550)
R2 = "https://1234567890abcdef.r2.cloudflarestorage.com"
AWS = "https://s3.eu-central-1.amazonaws.com"


def _s3_umgebung_leeren(monkeypatch):
    for name in ("S3_PRUEFSUMMEN", "S3_VERBINDUNG_TIMEOUT_S", "S3_LESE_TIMEOUT_S", "S3_VERSUCHE",
                 "BACKUP_S3_VERBINDUNG_TIMEOUT_S", "BACKUP_S3_LESE_TIMEOUT_S",
                 "BACKUP_S3_VERSUCHE", "BACKUP_S3_ENDPOINT", "BACKUP_S3_ACCESS_KEY",
                 "BACKUP_S3_SECRET_KEY", "BACKUP_S3_REGION"):
        monkeypatch.delenv(name, raising=False)


def test_sicherung_hat_eigene_grosszuegige_s3_grenzen(monkeypatch):
    k = _m("s3_kompatibel")
    _s3_umgebung_leeren(monkeypatch)
    monkeypatch.setenv("S3_VERSUCHE", "2")          # wie im Compose-Backend
    monkeypatch.setenv("S3_LESE_TIMEOUT_S", "30")
    for ziel in (R2, AWS):
        cfg = k.client_konfiguration(ziel, sicherung=True)
        assert cfg.retries == {"max_attempts": 5, "mode": "standard"}, ziel
        assert cfg.read_timeout == 120 and cfg.connect_timeout == 10, ziel
        # der Anfrageweg bleibt knapp
        knapp = k.client_konfiguration(ziel)
        assert knapp.retries["max_attempts"] == 2 and knapp.read_timeout == 30
    assert getattr(k.client_konfiguration(R2, sicherung=True),
                   "request_checksum_calculation", None) == "when_required", \
        "die R2-Eigenheiten gelten auch fuer die Sicherung"
    monkeypatch.setenv("BACKUP_S3_VERSUCHE", "8")
    monkeypatch.setenv("BACKUP_S3_LESE_TIMEOUT_S", "kaputt")
    cfg = k.client_konfiguration(AWS, sicherung=True)
    assert cfg.retries["max_attempts"] == 8 and cfg.read_timeout == 120


def test_backup_client_nutzt_die_sicherungsgrenzen(monkeypatch):
    _s3_umgebung_leeren(monkeypatch)
    monkeypatch.setenv("S3_ENDPOINT", R2)
    monkeypatch.setenv("S3_ACCESS_KEY", "a")
    monkeypatch.setenv("S3_SECRET_KEY", "b")
    monkeypatch.setenv("S3_VERSUCHE", "2")
    B = _m("backup_mongo")
    c = B._backup_s3_client()
    assert c.meta.config.read_timeout == 120
    # botocore zaehlt max_attempts als Wiederholungen: 5 -> 6 Versuche insgesamt
    r = c.meta.config.retries
    assert (r.get("total_max_attempts") or r.get("max_attempts", 0) + 1) == 6, r
    # der Speicher-Client des Anfragewegs bleibt bei den knappen Werten
    k = _m("s3_kompatibel")
    assert k.s3_client(endpoint=R2, access_key="a", secret_key="b").meta.config.read_timeout == 30
    quelle = (SCRIPTS / "offsite_pruefen.py").read_text(encoding="utf-8")
    assert "sicherung=True" in quelle


def test_compose_reicht_die_sicherungsgrenzen_durch():
    compose = (WURZEL / "docker-compose.yml").read_text(encoding="utf-8")
    for name in ("BACKUP_S3_VERBINDUNG_TIMEOUT_S", "BACKUP_S3_LESE_TIMEOUT_S",
                 "BACKUP_S3_VERSUCHE"):
        assert f"- {name}=${{{name}:-" in compose, name


# ================================================ Runbook prod2 (PSA, MongoDB 8.2)
def test_runbook_ruecknahme_ueber_reconfig_for_psa_set():
    text = (WURZEL / "DEPLOYMENT.md").read_text(encoding="utf-8")
    start = text.index("**Ruecknahme**, sobald prod2 wieder laeuft")
    abschnitt = text[start:start + 2500]
    block = re.search(r"```bash\n(.*?)```", abschnitt, re.S).group(1)
    assert "rs.reconfigForPSASet(i, cfg)" in block
    assert "votes = 1" in block and "priority = 0.5" in block
    assert not re.search(r"rs\.reconfig\(cfg\)", block), \
        "rs.reconfig lehnt PSA mit waehlbarem Secondary ab MongoDB 5.0 ab"


# ================================================ Nachholer Vertrag nach Abholung
def _abholung(w, s, *, v2_preis=None, stand=None):
    cid, aid = f"c_{s}", f"t_{s}"
    w.run(w.db.appointments.insert_one({"id": aid, "dealer_id": "d1", "contract_id": cid,
                                        "status": "abgeholt", "created_by": "u1"}))
    w.run(w.db.generated_pdfs.insert_one({"id": cid, "dealer_id": "d1", **(stand or {})}))
    w.run(w.db.pickup_protocols.insert_many([
        {"id": f"p1_{s}", "appointment_id": aid, "status": "final", "superseded": True,
         "version": 1, "contract_id": cid, "neuer_preis": 1000},
        {"id": f"p2_{s}", "appointment_id": aid, "status": "final", "superseded": False,
         "version": 2, "contract_id": cid, "neuer_preis": v2_preis},
    ]))
    w.run(w.db.betriebsalarme.insert_one({"typ": "vertrag_nach_abholung_offen", "ref": cid,
                                          "offen": True, "details": {"protokoll_id": f"p1_{s}"}}))
    return cid


def _nachholer_attrappen(monkeypatch, korrekturen=None):
    P = _m("routes.protocols")
    aufrufe = []

    async def _korrekturen(appt, p):
        return dict(korrekturen or {}), []

    async def _aktualisieren(appt, protokoll_id, preis, sonder, **kw):
        aufrufe.append(protokoll_id)
        return False                     # wie das fruehe "nichts zu tun"
    monkeypatch.setattr(P, "protokoll_korrekturen", _korrekturen)
    monkeypatch.setattr(P, "vertrag_nach_abholung_aktualisieren", _aktualisieren)
    return aufrufe


def test_v1_alarm_geht_zu_wenn_v2_nichts_neues_traegt(welt, monkeypatch):
    CS = _m("cleanup_service")
    w = welt
    cid = _abholung(w, "leer")
    aufrufe = _nachholer_attrappen(monkeypatch)
    assert w.run(CS.vertrag_nach_abholung_nachholen(w.db)) == 0
    assert aufrufe == [], "kein erfolgloser Neuversuch mehr"
    assert _alarm_offen(w, "vertrag_nach_abholung_offen", cid) == 0


@pytest.mark.parametrize("fall", ["preis", "korrektur", "fassung_nach_abholung", "vor_abholung"])
def test_v1_alarm_bleibt_wenn_es_etwas_zu_tun_gibt(welt, monkeypatch, fall):
    CS = _m("cleanup_service")
    w = welt
    cid = _abholung(
        w, fall, v2_preis=1500 if fall == "preis" else None,
        stand={"fassung_nach_abholung": {"nach_abholung_protokoll_id": "p1_x"},
               "vor_abholung": {"vertrag_vor_abholung": {"purchase_price": 900}}}.get(fall))
    aufrufe = _nachholer_attrappen(
        monkeypatch, korrekturen={"km": 1} if fall == "korrektur" else None)
    assert w.run(CS.vertrag_nach_abholung_nachholen(w.db)) == 0
    assert aufrufe == [f"p2_{fall}"], "die Neuerzeugung wird weiter gefragt"
    assert _alarm_offen(w, "vertrag_nach_abholung_offen", cid) == 1


def test_ohne_umleitung_bleibt_der_alarm_fuer_den_naechsten_versuch(welt, monkeypatch):
    """Nennt der Alarm schon die aktuelle Version, kann "nichts Neues" ein
    voruebergehender Lesefehler sein — dann weiter versuchen."""
    CS = _m("cleanup_service")
    w = welt
    cid = _abholung(w, "direkt")
    w.run(w.db.betriebsalarme.update_one({"ref": cid},
                                         {"$set": {"details.protokoll_id": "p2_direkt"}}))
    aufrufe = _nachholer_attrappen(monkeypatch)
    assert w.run(CS.vertrag_nach_abholung_nachholen(w.db)) == 0
    assert aufrufe == ["p2_direkt"]
    assert _alarm_offen(w, "vertrag_nach_abholung_offen", cid) == 1
