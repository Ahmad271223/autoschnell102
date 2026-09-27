# -*- coding: utf-8 -*-
"""Wunsch Ahmad 27.09.2026:
1. Marktdaten (Karte im Vergleich, Hinweis im Vertrag, Chancen) sehen Chef und Sucher erst nach Freischaltung
   im Admin ("die kommen erst nach ein paar Monaten dazu") — Standard AUS, fail-closed.
2. Befund zu den Alarmen markt_lauf_leer (27.09.): beide waren Marktluecken — Alarm nur noch, wenn die Segmente
   des leeren Buendels vorher nennenswert Treffer hatten."""
import inspect
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_befunde_runde17_termine import _module, welt  # noqa: E402,F401
from test_markt_20260926 import _antwort, _aufraeumen, _eigenen_beanspruchen, _segment, _vorbereiten  # noqa: E402

K = _module("markt.konfig")
JOBS = _module("markt.jobs")
# Beim Laden der Datei importieren (NICHT im Test): ein Modul mit `from deps import db`, das erst waehrend eines
# welt-Tests geladen wird, behaelt dessen Datenbank-Client — nach dem Test ist dessen Event-Loop zu.
R = _module("routes.markt")
S = _module("server")
APIFY = _module("markt.apify")
BACKEND = Path(__file__).resolve().parent.parent


def test_01_marktdaten_fuer_firmen_standard_aus_und_schalter(welt, monkeypatch):
    from fastapi import HTTPException
    w, db = welt.w, welt.db
    monkeypatch.setattr(S, "db", db)          # server und routes.markt halten ein eigenes db (nicht in den welt-Listen) —
    monkeypatch.setattr(R, "db", db)          # sonst bindet der globale Motor-Client an diese Test-Schleife
    vid = f"v_sicht_{w.s}"
    welt.run(db.vehicles.insert_one(w.fahrzeug(vid, data={"make_label": "BMW", "model_label": "320"})))
    sicherung = welt.run(db[K.KONFIG].find_one({"_id": K.SICHTBARKEIT_DOK}))
    welt.run(db[K.KONFIG].delete_one({"_id": K.SICHTBARKEIT_DOK}))

    async def _karte(db_, daten, listing_id=None):
        return {"sample_size": 3, "median_top20_price": 18400}
    monkeypatch.setattr(R.abfrage, "karte", _karte)
    monkeypatch.setenv("MARKT_CHANCEN_AKTIV", "true")
    try:
        # Standard: aus -> Karte 404 (still wie ohne Daten), Chancen/Modelle 404, /features sagt aus
        assert welt.run(K.firmen_sichtbar(db)) is False
        for user in (w.chef, w.sucher):
            with pytest.raises(HTTPException) as ex:
                welt.run(R.markt_karte(vid, user=user))
            assert ex.value.status_code == 404
        for fn in (R.markt_chancen, R.markt_modelle):
            with pytest.raises(HTTPException) as ex:
                welt.run(fn(user=w.chef))
            assert ex.value.status_code == 404
        f = welt.run(S.features())
        assert f["marktdaten"] is False and f["markt_chancen"] is False, "Chancen fuer Firmen nur mit beiden Schaltern"
        # freigeschaltet -> Karte kommt, Chancen folgen dem eigenen Schalter
        assert welt.run(K.firmen_sichtbar_setzen(db, True, wer="test")) is True
        assert welt.run(R.markt_karte(vid, user=w.chef))["sample_size"] == 3
        f = welt.run(S.features())
        assert f["marktdaten"] is True and f["markt_chancen"] is True
        monkeypatch.setenv("MARKT_CHANCEN_AKTIV", "false")
        assert welt.run(S.features())["markt_chancen"] is False
        # wieder aus -> sofort weg
        welt.run(K.firmen_sichtbar_setzen(db, False, wer="test"))
        with pytest.raises(HTTPException):
            welt.run(R.markt_karte(vid, user=w.chef))
    finally:
        welt.run(db[K.KONFIG].delete_one({"_id": K.SICHTBARKEIT_DOK}))
        if sicherung:
            welt.run(db[K.KONFIG].insert_one(sicherung))
        welt.run(db.vehicles.delete_many({"id": vid}))


def test_02_schalter_fail_closed_und_nur_super_admin(welt):
    class _Kaputt:
        def __getitem__(self, name):
            raise RuntimeError("Datenbank weg")
    assert welt.run(K.firmen_sichtbar(_Kaputt())) is False, "Stoerung -> aus"
    r = (BACKEND / "routes" / "markt_admin.py").read_text(encoding="utf-8")
    i = r.index('"/admin/market/sichtbarkeit"')
    kopf = r[i:i + 400]
    assert "current_super_admin" in kopf and "log_activity_sicher" in r[i:i + 900]
    assert '"firmen_sichtbar": await konfig.firmen_sichtbar(db)' in r
    # die Admin-Marktanalyse und die interne Nutzung haengen NICHT am Schalter
    for datei in ("markt/abfrage.py", "markt/jobs.py", "ai/kontext.py"):
        assert "firmen_sichtbar" not in (BACKEND / datei).read_text(encoding="utf-8"), datei
    q = inspect.getsource(R.markt_karte)
    assert q.index("firmen_sichtbar") < q.index("db.vehicles.find_one"), "Schalter vor jedem Fahrzeuglesen"


def test_03_lauf_leer_alarm_nur_bei_vorher_gefuellten_segmenten(welt, monkeypatch):
    v = JOBS.lauf_leer_verdaechtig
    assert v([{"last_rows": 2}, {"last_rows": 1}]) is True
    assert v([{"last_rows": 0}] * 10) is False, "Astra-Fall 27.09.: nie Treffer -> Marktluecke"
    assert v([{"last_rows": 1}, {}]) is False, "Q5/Octavia-Fall: kaum Treffer -> Marktluecke"
    assert v([{"last_rows": 5}, {"last_rows": 0}, {"last_rows": 0}]) is False, "weniger als die Haelfte gefuellt"
    assert v([]) is False
    # im Worker: leeres Buendel aus nie gefuellten Segmenten -> kein Alarm
    s, seg = _vorbereiten(welt, monkeypatch)
    db = welt.db
    seg_b = {**_segment(welt.w), "id": f"test-320d-{s}:2019-2021:1-2", "min_km": 1, "max_km": 2}
    welt.run(db[K.SEGMENTE].insert_one(dict(seg_b)))
    monkeypatch.setattr(APIFY, "lauf", _antwort([], usd=0.005, run_id=f"r-leer-{s}"))
    jobs = [welt.run(JOBS.job_sofort(db, x["id"])) for x in (seg, seg_b)]
    welt.run(JOBS.verarbeiten_buendel(db, [_eigenen_beanspruchen(welt, j["id"]) for j in jobs]))
    assert welt.run(db.betriebsalarme.find_one({"typ": "markt_lauf_leer", "ref": f"r-leer-{s}"})) is None
    assert all(welt.run(db[K.SEGMENTE].find_one({"id": x["id"]}, {"_id": 0}))["leer_in_folge"] == 1 for x in (seg, seg_b))
    welt.run(db.betriebsalarme.delete_many({"typ": "markt_lauf_leer", "ref": {"$regex": f"^r-leer-{s}"}}))
    _aufraeumen(welt)
