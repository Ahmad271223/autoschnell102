# -*- coding: utf-8 -*-
"""Pruefbericht 20.09.2026 (K-04): Die Vorpruefung der Oberflaeche
(frontend/src/lib/passwort.js) kennt dieselbe Sperrliste und dieselben
Sonderzeichen wie der Server (backend/passwoerter.py). Vorher fehlte beides —
"Passwort123!" ging im Formular durch und scheiterte erst am Server."""
import json
import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
JS = (BACKEND.parent / "frontend" / "src" / "lib" / "passwort.js").read_text(encoding="utf-8")


def test_sperrliste_gleich():
    import passwoerter
    block = JS[JS.index("export const VERBOTEN = new Set(["):]
    block = block[:block.index("]);")]
    js_liste = set(re.findall(r'"([^"]*)"', block))
    assert js_liste == set(passwoerter._VERBOTEN), (
        sorted(js_liste ^ set(passwoerter._VERBOTEN)))


def test_sonderzeichen_gleich():
    import passwoerter
    kopf = "export const SONDERZEICHEN = new Set("
    i = JS.index(kopf) + len(kopf)
    literal = JS[i:JS.index(");", i)]
    m = literal if literal.startswith('"') and literal.endswith('"') else None
    assert m, "SONDERZEICHEN nicht gefunden"
    assert set(json.loads(m)) == set(passwoerter._SONDERZEICHEN)
