# -*- coding: utf-8 -*-
"""Nachpruefung 20.09.2026 — Code-Befunde aus der Go-Live-Durchsicht.

Geprueft und bestaetigt wurden:
  #4  Der Kaeufer-Login schrieb die Sitzung ohne Bedingung: ein
      Passwortwechsel zwischen Pruefung und Schreiben liess noch eine
      gueltige Sitzung mit dem ALTEN Passwort entstehen. Firma und Fahrer
      hatten diese Sperre laengst.
  #7  Die naechtliche Sicherung lief IMMER ohne Schreibpause — ohne Replica
      Set stammten die Collections damit aus verschiedenen Zeitpunkten.
  #8  Ein solcher Lauf galt trotzdem als "vollstaendig", ohne dass irgendwo
      stand, dass er nicht stichtagsgenau ist.
  #9  Liess sich die Tagessperre wegen einer Datenbank-Stoerung nicht
      pruefen, galt das als "ein anderer Worker sichert schon" — der Tag
      blieb ohne Sicherung UND ohne Wiederholung.
"""
import inspect
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))


# ------------------------------------------------------------------ #4
def test_04_kaeufer_login_schreibt_die_sitzung_nur_unter_bedingung():
    import routes.marketplace as M
    q = inspect.getsource(M)
    stelle = q.split("sid = new_session_id()")[1].split("return {")[0]
    assert "sitzungs_bedingung(u)" in stelle, \
        "ohne CAS kann ein Passwortwechsel waehrend der Anmeldung durchrutschen"
    assert 'matched_count == 0' in stelle and "SITZUNG_UNGUELTIG" in stelle
    assert '{"id": u["id"]},\n' not in stelle, "der alte, bedingungslose Write"


def test_04b_dieselbe_bedingung_wie_bei_firma_und_fahrer():
    """Die Regel gehoert an EINE Stelle — nicht dreimal nachgebaut."""
    import routes.auth as A
    bed = A.sitzungs_bedingung({"password_hash": "abc", "mfa": {"aktiv": False}})
    assert bed["password_hash"] == "abc"
    assert bed["active"] is True
    assert bed["loeschung.status"] == {"$ne": "laeuft"}
    mit_mfa = A.sitzungs_bedingung({"password_hash": "x",
                                    "mfa": {"aktiv": True, "secret": "s"}})
    assert mit_mfa["mfa.aktiv"] is True and mit_mfa["mfa.secret"] == "s"


# ------------------------------------------------------------------ #7
@pytest.mark.parametrize("url,schalter,erwartet", [
    ("mongodb://a/?replicaSet=rs0", "", False),      # Snapshot kann er selbst
    ("mongodb://a/", "", True),                      # ohne Replica Set: Pause
    ("mongodb://a/", "false", False),                # bewusst abgeschaltet
    ("mongodb://a/?replicaSet=rs0", "true", True),   # bewusst erzwungen
])
def test_07_schreibpause_nur_wenn_noetig(monkeypatch, url, schalter, erwartet):
    import backup_service as BS
    monkeypatch.setenv("MONGO_URL", url)
    monkeypatch.setenv("BACKUP_WARTUNG", schalter)
    monkeypatch.delenv("BACKUP_SNAPSHOT_PFLICHT", raising=False)
    assert BS._schreibpause_noetig() is erwartet


def test_07b_der_lauf_gibt_die_wartung_weiter():
    import backup_service as BS
    q = inspect.getsource(BS._run_backup)
    assert "_schreibpause_noetig()" in q and '"--wartung"' in q


# ------------------------------------------------------------------ #8
def test_08_bereitschaft_sagt_wenn_die_sicherung_nicht_stichtagsgenau_ist():
    import server
    q = inspect.getsource(server)
    assert 'b.get("stichtagsgenau") is False' in q
    stelle = q.split('b.get("stichtagsgenau") is False')[1][:400]
    assert "warnungen.append" in stelle, "Warnung, kein Startverbot"
    assert "BACKUP_WARTUNG" in stelle, "die Meldung muss sagen, was zu tun ist"


def test_08b_stichtagsgenau_unterscheidet_die_faelle():
    import backup_bewertung as BB
    assert BB.ist_stichtagsgenau({"konsistenz": BB.KONSISTENZ_SNAPSHOT})
    assert BB.ist_stichtagsgenau({"konsistenz": BB.KONSISTENZ_SCHREIBPAUSE})
    assert not BB.ist_stichtagsgenau({"konsistenz": BB.KONSISTENZ_STANDALONE})
    # ... und ein Standalone-Lauf gilt weiterhin als "gut" (ein Einzelserver
    # kann es nicht besser) — deshalb braucht es die Warnung aus #8.
    assert BB.ist_gut({"konsistenz": BB.KONSISTENZ_STANDALONE, "version": 4,
                       "collections": {"a": 1}, "files": {}, "indexe": {"a": []},
                       "unvollstaendig": []})


# ------------------------------------------------------------------ #9
def test_09_datenbank_stoerung_ist_kein_erfolgreicher_lauf():
    import job_lock
    q = inspect.getsource(job_lock.acquire)
    assert "fehler_melden" in q and "raise" in q
    import backup_service as BS
    lauf = inspect.getsource(BS.run_backup_forever)
    assert "fehler_melden=True" in lauf
    assert "return False" in lauf.split("Tagessperre nicht pruefbar")[1][:300], \
        "eine Stoerung muss als Fehlschlag zaehlen (Wiederholung in 1 h)"


def test_09b_sperre_verhaelt_sich_sonst_unveraendert():
    """Ohne den neuen Schalter bleibt alles wie vorher: None statt Ausnahme."""
    import asyncio
    import job_lock

    class _Kaputt:
        class job_locks:
            @staticmethod
            async def find_one_and_update(*a, **k):
                raise RuntimeError("DB weg")

    assert asyncio.run(job_lock.acquire(_Kaputt(), "x")) is None
    with pytest.raises(RuntimeError):
        asyncio.run(job_lock.acquire(_Kaputt(), "x", fehler_melden=True))


# --------------------------------------------------- #6 (Frist 60 vs 90)
def test_06_vertragsfrist_ueberall_dieselbe_zahl():
    import cleanup_service as CS
    assert CS.VERTRAG_AUFBEWAHRUNG_TAGE == 60
    pruefung = (BACKEND / "production_check.py").read_text(encoding="utf-8")
    assert '("VERTRAG_AUFBEWAHRUNG_TAGE", "60")' in pruefung, \
        "die Produktionspruefung rechnete mit 90, der Code mit 60"
    for datei, text in ((".env.example", "VERTRAG_AUFBEWAHRUNG_TAGE=60"),
                        ("DEPLOYMENT.md", "`VERTRAG_AUFBEWAHRUNG_TAGE` (Standard 60)")):
        assert text in (BACKEND.parent / datei).read_text(encoding="utf-8"), datei
