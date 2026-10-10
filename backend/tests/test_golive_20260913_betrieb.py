# -*- coding: utf-8 -*-
"""Go-Live 13.09.2026 — Bereich "betrieb".

  * B4   server.on_start rief production_check.pruefe_produktion in einem
         try/except auf, das jede andere Exception als SystemExit nur als
         Warnung loggte und WEITERSTARTETE (fail-open). In Produktion schuetzte
         bisher nur, dass der Dockerfile-CMD vorher `python migrationen.py`
         (ohne try/except) ausfuehrt — startet uvicorn einmal ohne diesen
         Vorlauf, liefe die App trotz nicht gelaufener Produktionspruefung.
         Jetzt: in Produktion (APP_ENV=production) Abbruch mit SystemExit(78),
         ausserhalb wie bisher nur ein Log.

In-Prozess ohne server-Import: das Verhalten steckt in
production_check.produktionspruefung_beim_start, server.on_start ruft sie auf
(per Quelltext geprueft).
"""
import logging
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import production_check  # noqa: E402


class _Log:
    def __init__(self):
        self.eintraege = []

    def _merke(self, stufe):
        def f(msg, *args):
            self.eintraege.append((stufe, msg % args if args else msg))
        return f

    def __getattr__(self, name):
        if name in ("info", "warning", "error", "debug", "exception", "critical"):
            return self._merke(name)
        raise AttributeError(name)


def _kaputte_pruefung(log):
    raise RuntimeError("unerwarteter Fehler in der Pruefung")


def test_b4_produktion_bricht_bei_unerwarteter_exception_ab(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setattr(production_check, "pruefe_produktion", _kaputte_pruefung)
    log = _Log()
    with pytest.raises(SystemExit) as e:
        production_check.produktionspruefung_beim_start(log)
    assert e.value.code == 78
    assert any(stufe == "error" and "unerwarteter Fehler" in text
               for stufe, text in log.eintraege), log.eintraege


def test_b4_ausserhalb_produktion_nur_log(monkeypatch):
    for wert in ("", "development"):
        monkeypatch.setenv("APP_ENV", wert)
        monkeypatch.setattr(production_check, "pruefe_produktion", _kaputte_pruefung)
        log = _Log()
        production_check.produktionspruefung_beim_start(log)   # kein SystemExit
        assert any(stufe == "warning" and "unerwarteter Fehler" in text
                   for stufe, text in log.eintraege), log.eintraege


def test_b4_systemexit_der_pruefung_bleibt_unveraendert(monkeypatch):
    def _abbruch(log):
        raise SystemExit(78)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setattr(production_check, "pruefe_produktion", _abbruch)
    with pytest.raises(SystemExit) as e:
        production_check.produktionspruefung_beim_start(logging.getLogger("t"))
    assert e.value.code == 78

    # gueltige Pruefung ohne Fehler: kein Abbruch, kein Log-Eintrag des Wrappers
    monkeypatch.setattr(production_check, "pruefe_produktion", lambda log: None)
    log = _Log()
    production_check.produktionspruefung_beim_start(log)
    assert log.eintraege == []


def test_b4_server_on_start_nutzt_fail_closed_startpruefung():
    s = (BACKEND / "server.py").read_text(encoding="utf-8")
    i = s.index("async def on_start(")
    j = s.index("from migrationen import ausfuehren_oder_warten", i)
    rumpf = s[i:j]
    assert "produktionspruefung_beim_start(log)" in rumpf
    # das alte fail-open-Muster ist weg
    assert 'log.warning("production check failed to run' not in rumpf
    assert "pruefe_produktion(log)" not in rumpf
    # auch ein Importfehler der Pruefung bricht in Produktion ab
    k = rumpf.index("from production_check import produktionspruefung_beim_start")
    zweig = rumpf[k:rumpf.index("produktionspruefung_beim_start(log)")]
    assert "except Exception" in zweig and '"production"' in zweig and "SystemExit(78)" in zweig
