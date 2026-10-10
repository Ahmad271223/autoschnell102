# -*- coding: utf-8 -*-
"""Wunsch Ahmad 28.09.2026: "wo sehe ich, wie viele Autos heute gecrawlt wurden — immer mit Haken oder X":
Tagesstand je Suchauftrag (✓ alle Segmente, O ein Teil, ✗ keins, – ausstehend / nicht dran) und gesamt."""
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_befunde_runde17_termine import _module, welt  # noqa: E402,F401
from test_markt_20260926 import K, _aufraeumen, _modell  # noqa: E402

ABF = _module("markt.abfrage")


def test_01_symbol_regeln():
    s = ABF.tages_symbol
    assert s({"geplant": 0}) == "-"
    assert s({"geplant": 4, "ok": 4}) == "✓"
    assert s({"geplant": 4, "ok": 4, "fehler": 0, "offen": 0, "autos": 0}) == "✓"
    assert s({"geplant": 4, "ok": 2, "fehler": 1, "offen": 1}) == "O"
    assert s({"geplant": 4, "ok": 1, "fehler": 3, "offen": 0}) == "O"
    assert s({"geplant": 4, "ok": 0, "fehler": 0, "offen": 4}) == "-"
    assert s({"geplant": 4, "ok": 0, "fehler": 2, "offen": 2}) == "-"      # noch nicht fertig, kein Erfolg
    assert s({"geplant": 4, "ok": 0, "fehler": 4, "offen": 0}) == "✗"


def _job(model_id, seg, tag, status, rows=None):
    return {"id": uuid.uuid4().hex, "segment_id": seg, "model_id": model_id, "tag": tag, "job_type": "daily",
            "status": status, "actual_rows": rows, "max_items": 5, "scheduled_at": "2026-01-01T00:00:00+00:00"}


def test_02_tagesstand_je_modell_und_gesamt(welt):
    w, db = welt.w, welt.db
    _aufraeumen(welt)
    s = w.s
    heute = f"2097-05-{int(s[:1], 16) % 28 + 1:02d}"
    gestern = f"2097-04-{int(s[:1], 16) % 28 + 1:02d}"
    m_ok, m_teil, m_fehl, m_offen = (f"test-ok-{s}", f"test-teil-{s}", f"test-fehl-{s}", f"test-offen-{s}")
    jobs = [
        _job(m_ok, f"{m_ok}:a", heute, "completed", 5), _job(m_ok, f"{m_ok}:b", heute, "completed", 0),
        _job(m_ok, f"{m_ok}:b", f"{heute}#2", "completed", 4),                                   # zweiter Abruf des Tages
        _job(m_ok, f"{m_ok}:a", gestern, "failed"),                                               # gestern zaehlt nicht
        _job(m_ok, f"{m_ok}:c", heute, "cancelled"),                                              # abgebrochen = nicht geplant
        _job(m_teil, f"{m_teil}:a", heute, "completed", 3), _job(m_teil, f"{m_teil}:b", heute, "data_invalid"),
        _job(m_teil, f"{m_teil}:c", heute, "queued"),
        _job(m_fehl, f"{m_fehl}:a", heute, "failed"), _job(m_fehl, f"{m_fehl}:b", heute, "data_invalid"),
        _job(m_offen, f"{m_offen}:a", heute, "queued"), _job(m_offen, f"{m_offen}:b", heute, "running"),
    ]
    welt.run(db[K.JOBS].insert_many(jobs))
    try:
        st = welt.run(ABF.tages_stand(db, heute))
        j = st["je_modell"]
        # autos_soll = bestellte Zeilen (max_items 5) je geplantem Abruf — Ahmad: "wie viele haetten geladen werden muessen"
        # ok_mit_treffern = fertige Laeufe mit mindestens einem Auto (m_ok: 5 und 4 ja, 0 nein)
        assert j[m_ok] == {"geplant": 3, "ok": 3, "ok_mit_treffern": 2, "fehler": 0, "ungueltig": 0, "offen": 0, "autos": 9, "autos_soll": 15, "symbol": "✓"}
        assert j[m_teil] == {"geplant": 3, "ok": 1, "ok_mit_treffern": 1, "fehler": 1, "ungueltig": 1, "offen": 1, "autos": 3, "autos_soll": 15, "symbol": "O"}
        assert j[m_fehl] == {"geplant": 2, "ok": 0, "ok_mit_treffern": 0, "fehler": 2, "ungueltig": 1, "offen": 0, "autos": 0, "autos_soll": 10, "symbol": "✗"}
        assert j[m_offen] == {"geplant": 2, "ok": 0, "ok_mit_treffern": 0, "fehler": 0, "ungueltig": 0, "offen": 2, "autos": 0, "autos_soll": 10, "symbol": "-"}
        g = st["gesamt"]
        eigene = {k: v for k, v in j.items() if k.startswith("test-")}
        assert sum(z["geplant"] for z in eigene.values()) == 10 and sum(z["autos"] for z in eigene.values()) == 12
        assert sum(z["autos_soll"] for z in eigene.values()) == 50 and g["autos_soll"] >= 50
        assert sum(z["ok_mit_treffern"] for z in eigene.values()) == 3 and g["ok_mit_treffern"] >= 3
        assert g["geplant"] >= 10 and g["autos"] >= 12 and g["modelle_ok"] >= 1 and g["modelle_teil"] >= 1 and g["modelle_fehler"] >= 1
        # Modelluebersicht und Status tragen den Stand mit
        welt.run(db[K.MODELLE].insert_many([{**_modell(w), "id": m, "label": m} for m in (m_ok, m_teil, m_fehl, m_offen)]))
        mp = __import__("pytest").MonkeyPatch()
        mp.setattr(K, "heute_tag", lambda zeit=None: heute)
        try:
            ueb = {u["id"]: u for u in welt.run(ABF.modelle_uebersicht(db)) if u["id"].startswith(f"test-") and u["id"].endswith(s)}
            assert ueb[m_ok]["heute"]["symbol"] == "✓" and ueb[m_teil]["heute"]["symbol"] == "O"
            assert ueb[m_fehl]["heute"]["symbol"] == "✗" and ueb[m_offen]["heute"]["symbol"] == "-"
            neu = {**_modell(w), "id": f"test-nix-{s}", "label": "ohne Jobs"}
            welt.run(db[K.MODELLE].insert_one(neu))
            u2 = next(u for u in welt.run(ABF.modelle_uebersicht(db)) if u["id"] == neu["id"])
            assert u2["heute"] == dict(ABF.STAND_LEER)
            status = welt.run(ABF.status(db))
            assert status["heute"]["geplant"] >= 10 and status["heute"]["symbol"] in ("✓", "O", "✗", "-")
        finally:
            mp.undo()
    finally:
        _aufraeumen(welt)
