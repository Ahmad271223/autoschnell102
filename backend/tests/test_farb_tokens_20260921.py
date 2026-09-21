# -*- coding: utf-8 -*-
"""Keine Farbnamen, die es nicht gibt (Bildschirmfoto Ahmad 21.09.2026).

Der Dialog "Nachträgliche Mail" nutzte var(--surface-1), var(--surface-2),
var(--line) und var(--apple-btn-primary-bg). Keiner dieser Namen ist in
frontend/src/index.css festgelegt — der Browser nimmt dann "durchsichtig":
Das Fenster hatte keinen Hintergrund, der Text lief über die Vertragsliste,
der gewählte Knopf war unsichtbar. Ein Test, der das Aussehen nicht sieht,
merkt so etwas nie; dieser Wächter vergleicht deshalb jeden var(--…)-Namen
in der Oberfläche mit den festgelegten.
"""
import re
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"

#: Werte, die eine Bibliothek zur Laufzeit selbst setzt (Radix UI).
LAUFZEIT = re.compile(r"^--radix-")


def _festgelegt() -> set:
    texte = [p.read_text(encoding="utf-8", errors="ignore")
             for p in (FRONTEND / "src").rglob("*.css")]
    for p in list(FRONTEND.glob("*.config.*")) + [FRONTEND / "index.html"]:
        if p.exists():
            texte.append(p.read_text(encoding="utf-8", errors="ignore"))
    namen = set(re.findall(r"(--[a-zA-Z0-9-]+)\s*:", "\n".join(texte)))
    # auch im Code gesetzte Variablen ({"--x": ...} oder setProperty("--x", ...))
    for p in (FRONTEND / "src").rglob("*.js*"):
        namen |= set(re.findall(r"""["'](--[a-zA-Z0-9-]+)["']\s*[:,]""",
                                p.read_text(encoding="utf-8", errors="ignore")))
    return namen


def test_01_jede_farbe_in_der_oberflaeche_ist_festgelegt():
    festgelegt = _festgelegt()
    assert "--bg-elevated" in festgelegt and "--border-default" in festgelegt, (
        "die Liste der festgelegten Namen ist leer — Pfad falsch?")
    fehlt = []
    for p in (FRONTEND / "src").rglob("*.jsx"):
        if ".test." in p.name:
            continue
        for nr, zeile in enumerate(p.read_text(encoding="utf-8", errors="ignore")
                                   .splitlines(), 1):
            for name in re.findall(r"var\((--[a-zA-Z0-9-]+)", zeile):
                if name in festgelegt or LAUFZEIT.match(name):
                    continue
                # var(--x, Ersatzwert) ist unkritisch — es gibt einen Ersatz
                if re.search(r"var\(" + re.escape(name) + r"\s*,", zeile):
                    continue
                fehlt.append(f"{p.relative_to(FRONTEND).as_posix()}:{nr} {name}")
    assert not fehlt, ("Farbnamen ohne Festlegung in index.css (werden durchsichtig): "
                       + ", ".join(fehlt))


def test_02_der_kopier_dialog_hat_einen_festen_hintergrund():
    dialog = (FRONTEND / "src" / "components" / "FolgeMailDialog.jsx").read_text(
        encoding="utf-8")
    assert 'background: "var(--bg-elevated)"' in dialog
    for alt in ("--surface-1", "--surface-2", "var(--line)", "--apple-btn-primary-bg"):
        assert alt not in dialog, alt
