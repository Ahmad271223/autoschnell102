# -*- coding: utf-8 -*-
"""Welche Tests pruefen nur den Quelltext? (Pruefbericht 20.09.2026, T-06)

Viele Tests der Suite lesen Quelltext ein (inspect.getsource(...) oder
Path(...).read_text(...)) und pruefen darin Zeichenketten — sie belegen, DASS
eine Zeile im Code steht, nicht, dass sie wirkt. Der Marker `quelltext`
(pytest.ini) soll sie kennzeichnen, damit man sie gezielt ausschliessen
(`-m "not quelltext"`) oder Schritt fuer Schritt in Verhaltenstests umbauen
kann.

Dieses Programm LISTET die Kandidaten — es aendert keine Datei:

    python scripts/quelltext_tests_markieren.py             # datei::test je Zeile
    python scripts/quelltext_tests_markieren.py --zusammenfassung   # Zahlen je Datei
    python scripts/quelltext_tests_markieren.py --ohne-marker       # nur unmarkierte

Ein Test gilt als Quelltext-Test, wenn in seinem Rumpf (auch in verschachtelten
Funktionen) `getsource(` oder `read_text(` vorkommt. Helfer ausserhalb der
Testfunktion (z.B. `def _quelle(): return Path(...).read_text()`) werden
ueber den Namen erkannt: ruft der Test eine Modul-Funktion auf, die selbst
Quelltext liest, zaehlt er ebenfalls. Exit 0; laeuft ohne Datenbank.
"""
import ast
import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parents[1] / "tests"
MUSTER = ("getsource(", "read_text(")
MARKER = "quelltext"


def _liest_quelltext(knoten: ast.AST, quelle: str) -> bool:
    text = ast.get_source_segment(quelle, knoten) or ""
    return any(m in text for m in MUSTER)


def _hat_marker(fn: ast.FunctionDef) -> bool:
    for d in fn.decorator_list:
        text = ast.unparse(d)
        if text.startswith(("pytest.mark.", "mark.")) and MARKER in text:
            return True
    return False


def _aufrufe(fn: ast.AST) -> set:
    namen = set()
    for k in ast.walk(fn):
        if isinstance(k, ast.Call):
            if isinstance(k.func, ast.Name):
                namen.add(k.func.id)
            elif isinstance(k.func, ast.Attribute):
                namen.add(k.func.attr)
    return namen


def kandidaten(datei: Path):
    """[(testname, hat_marker)] fuer eine Testdatei."""
    quelle = datei.read_text(encoding="utf-8")
    try:
        baum = ast.parse(quelle)
    except SyntaxError as exc:
        print(f"WARNUNG {datei.name}: {exc}", file=sys.stderr)
        return []
    helfer = set()
    funktionen = []
    for k in ast.walk(baum):
        if not isinstance(k, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if k.name.startswith("test_") or k.name.startswith("test"):
            funktionen.append(k)
        elif _liest_quelltext(k, quelle):
            helfer.add(k.name)
    # Modulweite pytestmark mit dem Marker zaehlt fuer alle Tests der Datei
    modul_marker = any(
        isinstance(k, ast.Assign) and any(
            isinstance(z, ast.Name) and z.id == "pytestmark" for z in k.targets)
        and MARKER in ast.unparse(k.value)
        for k in baum.body)
    out = []
    for fn in funktionen:
        if not fn.name.startswith("test"):
            continue
        direkt = _liest_quelltext(fn, quelle)
        ueber_helfer = bool(_aufrufe(fn) & helfer)
        if direkt or ueber_helfer:
            out.append((fn.name, modul_marker or _hat_marker(fn)))
    return out


def main(argv) -> int:
    zusammenfassung = "--zusammenfassung" in argv
    nur_ohne = "--ohne-marker" in argv
    gesamt = markiert = dateien = 0
    for datei in sorted(TESTS.glob("test_*.py")):
        treffer = kandidaten(datei)
        if not treffer:
            continue
        dateien += 1
        gesamt += len(treffer)
        markiert += sum(1 for _, m in treffer if m)
        if zusammenfassung:
            ohne = sum(1 for _, m in treffer if not m)
            print(f"{datei.name}\t{len(treffer)} Quelltext-Tests\t{ohne} ohne Marker")
            continue
        for name, hat in treffer:
            if nur_ohne and hat:
                continue
            zusatz = "" if hat else "\t(ohne Marker)"
            print(f"tests/{datei.name}::{name}{zusatz}")
    print(f"\n{gesamt} Quelltext-Tests in {dateien} Dateien, davon {markiert} mit "
          f"@pytest.mark.{MARKER}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
