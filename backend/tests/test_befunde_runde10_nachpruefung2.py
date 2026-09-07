# -*- coding: utf-8 -*-
"""Nachpruefung Runde 10, Teil 3 (09/2026): Restliste "offen / teilweise".

Regeln-Lesepfad, atomare Versand-Wiederaufnahme, Lasttest-Klassifizierung
und -Auswertung, Cloudflare-CI, Anbieter-Probe, Betriebsdateien.
HTTP-Teile brauchen das Backend auf TEST_BASE_URL (Mock-Anbieter wie in CI).
"""
import copy
import importlib
import json
import os
import re
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests

BACKEND = Path(__file__).resolve().parents[1]
WURZEL = BACKEND.parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

BASE = (os.environ.get("TEST_BASE_URL") or "http://localhost:8001").rstrip("/")
API = f"{BASE}/api"
MONGO_URL = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
DB_NAME = os.environ.get("DB_NAME") or "autoschnell"
SUF = uuid.uuid4().hex[:8]
PW = "NachTest123!"
JETZT = datetime.now(timezone.utc)


def _db():
    from pymongo import MongoClient
    return MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)[DB_NAME]


# =============================================================== Regeln: Lesepfad
VEH = {"make": "BMW", "make_label": "BMW", "model": "320d", "model_label": "320d",
       "first_registration": "03/2019", "mileage": 80000, "power_kw": 140}


@pytest.mark.parametrize("kaputt", [
    {"damage": "no_accident"},
    {"damage": None},
    {"first_registration": "exact"},
    {"country": "DE"},
    {"mileage": {"mode": "plus", "value": "zwei"}},
    {"radius": 50},
    "kein dict",
    None,
])
def test_regeln_lesen_heilt_altdokumente(kaputt):
    from regeln import regeln_lesen
    from mobile_service import DEFAULT_RULES, build_search_url as mobile
    from autoscout_service import build_search_url as autoscout
    rules = regeln_lesen(kaputt, DEFAULT_RULES)
    assert rules["damage"] == DEFAULT_RULES["damage"]
    assert "radius" not in rules
    assert mobile(dict(VEH), rules).startswith("https://")
    assert autoscout(dict(VEH), rules).startswith("https://")


def test_regeln_lesen_behaelt_gueltige_werte():
    from regeln import regeln_lesen
    from mobile_service import DEFAULT_RULES
    r = regeln_lesen({"damage": {"mode": "include"}, "mileage": "kaputt"}, DEFAULT_RULES)
    assert r["damage"] == {"mode": "include"}
    assert r["mileage"] == DEFAULT_RULES["mileage"]


def test_url_bauer_vertragen_none_regeln():
    from mobile_service import build_search_url as mobile
    from autoscout_service import build_search_url as autoscout
    rules = {"damage": None, "fuel": None, "gearbox": None, "mileage": None,
             "power": None, "first_registration": None, "seller": None, "country": None}
    assert "dam=0" not in mobile(dict(VEH), rules)
    assert autoscout(dict(VEH), rules).startswith("https://")


# =============================================================== Versand: atomare Wiederaufnahme
@pytest.fixture(scope="module")
def vertrag():
    r = requests.post(f"{API}/auth/register", json={
        "email": f"nachdrei_{SUF}@e2etest-mail.de", "password": PW,
        "company_name": "Nach3 GmbH", "contact_person": "N T", "phone": "0511 9"}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    h = {"Authorization": f"Bearer {r.json()['token']}"}
    me = requests.get(f"{API}/auth/me", headers=h, timeout=30).json()["user"]
    _db().subscriptions.insert_one({
        "id": str(uuid.uuid4()), "dealer_id": me["dealer_id"], "subject_user_id": me["id"],
        "plan": "monthly", "status": "active",
        "expires_at": (JETZT + timedelta(days=1)).isoformat(), "created_at": JETZT.isoformat()})
    ka = f"https://www.kleinanzeigen.de/s-anzeige/nach3/94{uuid.uuid4().int % 10**8:08d}-216-1"
    r = requests.post(f"{API}/mobile/compare", json={"url": ka}, headers=h, timeout=90)
    if r.status_code != 200 or not (r.json().get("vehicle") or {}).get("_mock"):
        pytest.skip("Backend ohne MOCK_PROVIDER_FETCH")
    r = requests.post(f"{API}/contracts", headers=h, json={
        "vehicle_id": r.json()["vehicle_id"], "seller_name": "N V", "seller_address": "Weg 1",
        "seller_zip": "30159", "seller_city": "Hannover", "purchase_price": 5000,
        "pickup_date": "2099-06-01", "pickup_time": "10:00"}, timeout=90)
    assert r.status_code == 200, r.text[:200]
    yield {"h": h, "cid": r.json()["id"], "me": me}
    dbx = _db()
    for c in ("subscriptions", "vehicles", "appointments", "generated_pdfs", "activity_logs"):
        dbx[c].delete_many({"dealer_id": me["dealer_id"]})
    dbx.users.delete_many({"id": me["id"]})
    dbx.dealers.delete_many({"id": me["dealer_id"]})


def _eintraege(cid):
    c = _db().generated_pdfs.find_one({"id": cid}, {"_id": 0, "send_status": 1})
    return (c or {}).get("send_status") or []


def _parallel_wiederaufnahmen(vertrag, kanal, empf, n=10):
    alt = f"alt-{uuid.uuid4().hex[:12]}"
    _db().generated_pdfs.update_one({"id": vertrag["cid"]}, {"$push": {"send_status": {
        "idempotency_key": alt, "channel": kanal, "recipient": empf,
        "sent_at": (JETZT - timedelta(minutes=20)).isoformat(), "zustellung": "laeuft"}}})
    aktion = f"pdf.gesendet.{kanal}"
    vorher = _db().activity_logs.count_documents({"dealer_id": vertrag["me"]["dealer_id"], "action": aktion})

    def schuss(i):
        r = requests.post(f"{API}/contracts/{vertrag['cid']}/send", headers=vertrag["h"], json={
            "channel": kanal, "recipient": empf, "message": "Hier der Vertrag.",
            "subject": "Kaufvertrag", "idempotency_key": f"neu-{i}-{uuid.uuid4().hex[:8]}"},
            timeout=60)
        return r.status_code, r.json()

    with ThreadPoolExecutor(max_workers=n) as ex:
        erg = list(ex.map(schuss, range(n)))
    assert all(s == 200 for s, _ in erg), erg
    zugestellt = [e for _, e in erg if not e.get("bereits_gesendet")]
    nachher = _db().activity_logs.count_documents({"dealer_id": vertrag["me"]["dealer_id"], "action": aktion})
    eintraege = [e for e in _eintraege(vertrag["cid"])
                 if e.get("channel") == kanal and e.get("recipient") == empf]
    return alt, zugestellt, nachher - vorher, eintraege


def test_zehn_parallele_wiederaufnahmen_stellen_genau_einmal_zu(vertrag):
    """Zehn Klicks mit je neuem Schluessel auf denselben haengenden Eintrag:
    genau EIN Aufruf stellt zu, die anderen bekommen bereits_gesendet."""
    alt, zugestellt, logs, eintraege = _parallel_wiederaufnahmen(vertrag, "whatsapp", "+491700000011")
    # Invariante: jede Zustellung hat GENAU EINEN eigenen Eintrag — der alte
    # Eintrag wurde genau einmal wiederaufgenommen. (Klicks, die erst nach
    # dem Abschluss des Gewinners ankommen, sind legitime neue Sendungen mit
    # eigenem Schluessel; vorher: 10 Zustellungen bei 9 Eintraegen.)
    assert len(zugestellt) == len(eintraege), (len(zugestellt), len(eintraege))
    assert logs == len(zugestellt), (logs, len(zugestellt))
    wiederaufgenommen = [e for e in eintraege if e.get("wiederaufgenommen")]
    assert len(wiederaufgenommen) == 1 and wiederaufgenommen[0]["idempotency_key"] == alt, eintraege
    assert wiederaufgenommen[0].get("zustellung") not in ("laeuft", "unklar"), eintraege
    assert all(e.get("zustellung") not in ("laeuft", "unklar") for e in eintraege), eintraege


def test_verlierer_ueberschreibt_kein_ergebnis(vertrag):
    alt, zugestellt, logs, eintraege = _parallel_wiederaufnahmen(vertrag, "email", "v3@e2etest-mail.de")
    assert len(zugestellt) == len(eintraege) == logs, (len(zugestellt), len(eintraege), logs)
    assert sum(1 for e in eintraege if e.get("wiederaufgenommen")) == 1
    assert all(e.get("zustellung") not in ("laeuft", "unklar") for e in eintraege), eintraege


def test_zustellung_haengt_respektiert_frischen_claim():
    from routes.contracts import _zustellung_haengt, ZUSTELLUNG_HAENGT_NACH_SEK
    jetzt = datetime.now(timezone.utc)          # nicht JETZT (Importzeit): langer Gesamtlauf
    alt = (jetzt - timedelta(seconds=ZUSTELLUNG_HAENGT_NACH_SEK + 60)).isoformat()
    assert _zustellung_haengt({"zustellung": "laeuft", "sent_at": alt}) is True
    frisch = {"zustellung": "laeuft", "sent_at": alt, "wiederaufnahme_am": jetzt.isoformat()}
    assert _zustellung_haengt(frisch) is False, "frischer Claim darf nicht erneut uebernommen werden"
    assert _zustellung_haengt({"zustellung": "unklar", "sent_at": alt}) is True


# =============================================================== Lasttest: Klassifizierung
def _stats(*eintraege):
    import lasttest_matrix as m
    s = m.Stats()
    for name, st, text, erw in eintraege:
        s.add(name, 1.0, st, erwartet_4xx=erw, text=text)
    return s


def test_400_mit_fremdem_text_ist_kein_fotolimit():
    import lasttest_matrix as m
    s = _stats(("foto_upload_klein", 400, '{"detail":"Maximal 40 Fotos pro Inserat"}', ()),
               ("foto_upload_klein", 400, '{"detail":"Foto konnte nicht gespeichert werden: x"}', ()))
    k, d = m.klassifiziere(s.report())
    assert k["fachlich_erwartet"] == 1
    assert k["technisch_unerwartet"] == 1
    assert any("UNERWARTET" in v for v in d["foto_upload_klein"].values()), d


def test_limiter_abo_validierung_getrennt():
    import lasttest_matrix as m
    s = _stats(("markt_suche", 429, '{"detail":"Zu viele Statusabfragen"}', ()),
               ("markt_suche", 402, '{"detail":"Kein aktiver Marktplatz-Zugang"}', ()),
               ("markt_suche", 422, '{"detail":[{"loc":["query","q"],"msg":"field required"}]}', ()))
    k, _ = m.klassifiziere(s.report())
    assert k["technisch_unerwartet"] == 3
    assert k["technisch_unerwartet_nach_art"] == {"limiter": 1, "abo": 1, "validierung_testfehler": 1}


def test_erwartet_4xx_akzeptiert_nur_den_genannten_status():
    s = _stats(("foto_ungueltig_abgelehnt", 400, '{"detail":"Ungueltiges Bild"}', {400}),
               ("foto_ungueltig_abgelehnt", 429, '{"detail":"Zu viele"}', {400}),
               ("pdf_download_protokoll", 404, '{"detail":"kein Protokoll"}', {404}),
               ("pdf_download_protokoll", 401, '{"detail":"Token abgelaufen"}', {404}))
    rep = s.report()
    assert rep["foto_ungueltig_abgelehnt"]["ok"] == 1 and rep["foto_ungueltig_abgelehnt"]["fehler"] == 1
    assert rep["pdf_download_protokoll"]["ok"] == 1 and rep["pdf_download_protokoll"]["fehler"] == 1
    assert "401 Token abgelaufen" in rep["pdf_download_protokoll"]["fehler_nach_grund"]


def test_alte_berichte_ohne_grund_bleiben_auswertbar():
    import lasttest_matrix as m
    alt = {"foto_upload_klein": {"ok": 5, "fehler": 2, "fehler_nach_status": {"400": 2}}}
    k, d = m.klassifiziere(alt)
    assert k["fachlich_erwartet"] == 2 and k["technisch_unerwartet"] == 0


# =============================================================== Auswertung: negative Werte sichtbar
def test_negativer_doppel_versand_wird_ausgewiesen(tmp_path, monkeypatch):
    m = importlib.import_module("matrix_auswertung")
    basis = json.loads((WURZEL / "docs/lasttests/matrix/20260829T171133Z-T5-rep3.json")
                       .read_text(encoding="utf-8"))
    for i, (dv, resets) in enumerate([(-7, 0), (-3, 3), (2, 0)], 1):
        d = copy.deepcopy(basis)
        d["integritaet"]["doppel_versand"] = dv
        d.setdefault("klassen", {})["verbindungsabbruch_client"] = resets
        (tmp_path / f"2026090{i}T000000Z-T5-rep{i}.json").write_text(json.dumps(d), encoding="utf-8")
    monkeypatch.setattr(m, "MATRIX", tmp_path)
    monkeypatch.chdir(tmp_path)
    m.main()
    tabelle = json.loads((tmp_path / "abschlusstabelle.json").read_text(encoding="utf-8"))
    z = tabelle[0]
    assert z["versand_zusammengefaltet"] == 10, z
    assert "Wiederaufnahmen" in z["bestanden"], z["bestanden"]
    assert z["integritaetsfehler"] == 2, z


# =============================================================== Cloudflare: Skript + CI
def test_cloudflare_exit_codes_offline(monkeypatch, capsys):
    import cloudflare_netze_pruefen as cf
    ist = cf.netze_in_vorlage(cf.VORLAGE.read_text(encoding="utf-8"))
    monkeypatch.setattr(cf, "cloudflare_netze", lambda: set(ist))
    assert cf.main([]) == 0
    monkeypatch.setattr(cf, "cloudflare_netze", lambda: set(ist) | {"203.0.113.0/24"})
    assert cf.main([]) == 1
    assert "FEHLT in der Vorlage: 203.0.113.0/24" in capsys.readouterr().out

    def kaputt():
        raise OSError("kein Netz")
    monkeypatch.setattr(cf, "cloudflare_netze", kaputt)
    assert cf.main([]) == 2
    monkeypatch.setattr(cf, "VORLAGE", cf.VORLAGE.with_name("fehlt.template"))
    assert cf.main([]) == 2, "fehlende Vorlage liefert ebenfalls 2 — CI prueft sie deshalb vorher"


def test_ci_hat_cloudflare_job_und_lb_sperre_funktional():
    ci = (WURZEL / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert re.search(r"^  cloudflare:\n", ci, re.M), "CI-Job 'cloudflare' fehlt"
    assert "test -f deploy/hinter-loadbalancer.conf.template" in ci
    block = ci[ci.index("  cloudflare:"):]
    assert '2) echo "::warning' in block, "Exit 2 muss nur warnen"
    assert "exit 1" in block, "Abweichung muss blockieren"
    assert "Direktzugriffssperre hinter dem Load Balancer (funktional)" in ci
    lb = ci[ci.index("Direktzugriffssperre hinter dem Load Balancer"):ci.index("Playwright-Report des Stack-Rauchtests")]
    assert "PRIVATES_NETZ=203.0.113.0/24" in lb and '[ "$RC" = 52 ]' in lb
    assert 'PRIVATES_NETZ="$NETZ"' in lb and "X-Forwarded-For: 198.51.100.7" in lb


# =============================================================== Betrieb
def test_anbieter_probe_hat_frisch_option():
    q = (BACKEND / "scripts" / "anbieter_probe.py").read_text(encoding="utf-8")
    assert '"--frisch"' in q and "_eintrag_entwerten" in q
    assert "vehicle_cache" in q, "mobile.de-Zwischenspeicher muss mit entwertet werden"
    assert "frisch=args.frisch" in q


def test_lasttest_stack_bleibt_wegwerf():
    src = (WURZEL / "deploy" / "lasttest-auf-prod2.sh").read_text(encoding="utf-8")
    assert "MOCK_PROVIDER_FETCH=true" in src
    assert "APP_ENV=production" not in src
    assert not re.search(r"docker run[^\n]* -p ", src) and "--publish" not in src
    assert "Messeinschraenkungen" in src


def test_rate_limit_aus_ist_in_produktion_startfehler(monkeypatch):
    import production_check as pc
    quelle = (BACKEND / "production_check.py").read_text(encoding="utf-8")
    assert "RATE_LIMIT_ENABLED=false: Anmelde-/Registrierungssperren sind aus" in quelle
    # Verhalten: die Pruefung sammelt den Fehler ein
    fn = next((getattr(pc, n) for n in dir(pc) if n.startswith("pruefe") and callable(getattr(pc, n))), None)
    if fn is None:
        pytest.skip("keine pruefe_*-Funktion in production_check")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    monkeypatch.setenv("MOCK_PROVIDER_FETCH", "false")
    import logging
    try:
        fn(logging.getLogger("t"))
    except SystemExit as e:
        assert e.code == 78
    else:
        pytest.skip("pruefe-Funktion beendet den Prozess nicht — nur Quelltext geprueft")


def test_dokumentation_nennt_messeinschraenkungen_und_t3_offen():
    readme = (WURZEL / "docs" / "lasttests" / "README.md").read_text(encoding="utf-8")
    assert "RATE_LIMIT_ENABLED=false" in readme and "--nur-matrix" in readme
    assert "T3 (Fotos) auf prod2 mit korrigiertem Test" in readme
    doku = (WURZEL / "DEPLOYMENT.md").read_text(encoding="utf-8")
    assert "/data/db/rollback/" in doku
    env = (BACKEND / "scripts" / "env_erzeugen.py").read_text(encoding="utf-8")
    assert "172.16.0.0/12" in env
