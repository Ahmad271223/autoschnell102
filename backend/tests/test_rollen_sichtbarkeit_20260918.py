# -*- coding: utf-8 -*-
"""Rollentest 18.09.2026 (Wunsch Ahmad: "teste komplett Firmenchef und Sucher").

Befund: Die Seite "Mitarbeiter / Sucher" steht im Menue nur beim Chef — ueber
ein Lesezeichen oder eine alte Adresse kam ein Sucher trotzdem hin. Dann lief
sie in drei 403-Antworten und zeigte rote Fehlermeldungen, obwohl der Server
alles richtig gemacht hat. Diese Tests halten fest:

  * der Server bleibt streng (Chef = der eingetragene Hauptaccount),
  * das Menue zeigt dem Sucher die Chef-Seiten nicht,
  * die Seite selbst schickt ihn still auf seine eigene Startseite,
  * "Freigaben" erklaert es stattdessen freundlich (dort ist es Absicht).
"""
import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import deps  # noqa: E402

SRC = Path(__file__).resolve().parents[2] / "frontend" / "src"


def _lies(*teile):
    return (SRC.joinpath(*teile)).read_text(encoding="utf-8")


def test_01_server_laesst_nur_den_hauptaccount_ans_team():
    q = inspect.getsource(deps.current_chef)
    assert 'user.get("role") != "dealer"' in q
    assert "Nur der Händler-Hauptaccount darf das" in q


def test_02_team_seite_schickt_den_sucher_zurueck():
    seite = _lies("pages", "app", "Team.jsx")
    assert 'const chef = user?.role === "dealer";' in seite
    assert "if (!chef) return <Navigate to={startseite(user)} replace />;" in seite
    # Ohne diese Bremse liefen die drei Chef-Abrufe trotzdem los (403 + rote
    # Meldung), bevor die Umleitung greift.
    assert "if (!chef) return;" in seite


def test_03_menue_zeigt_dem_sucher_keine_chef_seiten():
    layout = _lies("components", "AppLayout.jsx")
    assert 'to: "/app/team", label: "Mitarbeiter / Sucher"' in layout
    for eintrag in ('"/app/team"', '"/app/freigaben"', '"/app/bestand"', '"/app/anfragen"'):
        zeile = next(z for z in layout.splitlines() if eintrag in z and "to:" in z)
        assert "haendlerOnly: true" in zeile, zeile
    assert 'NAV.filter((it) => !(it.haendlerOnly && user?.role === "sucher")' in layout


def test_04_freigaben_erklaert_es_statt_zu_scheitern():
    """Andere Loesung, gleiche Regel: "Freigaben" ist Chefsache, zeigt dem
    Sucher aber einen Hinweis — und ruft die Chef-Route gar nicht erst auf."""
    seite = _lies("pages", "app", "Freigaben.jsx")
    assert 'const chef = user?.role === "dealer";' in seite
    assert "Freigaben sind Chefsache" in seite
