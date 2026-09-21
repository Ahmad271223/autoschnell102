"""Eine Unicode-Schrift fuer ALLE PDFs (Kaufvertrag, Abholprotokoll, Beweisdokument).

Pruefbericht 20.09.2026 (P-01/P-02/R1/R2): Kaufvertrag und Abholprotokoll
nutzten die Standardschrift Helvetica — die kennt nur westeuropaeische
Zeichen. "Şahin Yıldırım" wurde zu "■ahin Y■ld■r■m", "Łukasz" zu "■ukasz",
kyrillische Namen vollstaendig zu Kaestchen, ohne Fehler und ohne Warnung —
ausgerechnet bei Verkaeufername, Anschrift und Unterschriftszeile. Umlaute
gingen, deshalb fiel es in keinem Test auf. Das Beweisdokument registrierte
schon eine echte TrueType-Schrift; dieselbe gilt jetzt ueberall.

Im Docker-Image liegt Liberation Sans (Paket fonts-liberation, siehe
backend/Dockerfile), lokal unter Windows Arial. Fehlt beides, bleibt es bei
Helvetica (ok=False) — dann meldet /api/ready das nicht, aber die PDFs
entstehen weiter.
"""
import os
from typing import Dict, Tuple

from reportlab.pdfbase import pdfmetrics

FAMILIE = "BeweisSans"          # Name aus dem Beweisdokument, bewusst beibehalten

_KANDIDATEN = [
    ("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
     "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    (r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\arialbd.ttf"),
]
_schrift = None


def schriften() -> Tuple[str, str, bool]:
    """(normal, fett, ok). ok=False heisst: nur Helvetica verfuegbar."""
    global _schrift
    if _schrift is not None:
        return _schrift
    from reportlab.lib.fonts import addMapping
    from reportlab.pdfbase.ttfonts import TTFont
    for normal, fett in _KANDIDATEN:
        if not (os.path.isfile(normal) and os.path.isfile(fett)):
            continue
        try:
            pdfmetrics.registerFont(TTFont(FAMILIE, normal))
            pdfmetrics.registerFont(TTFont(f"{FAMILIE}-Bold", fett))
            # <b>/<i> in Absaetzen auf dieselbe Familie abbilden (kursiv gibt
            # es nicht als eigene Datei — dann eben aufrecht).
            addMapping(FAMILIE, 0, 0, FAMILIE)
            addMapping(FAMILIE, 1, 0, f"{FAMILIE}-Bold")
            addMapping(FAMILIE, 0, 1, FAMILIE)
            addMapping(FAMILIE, 1, 1, f"{FAMILIE}-Bold")
            _schrift = (FAMILIE, f"{FAMILIE}-Bold", True)
            return _schrift
        except Exception:  # noqa: BLE001 — naechste Schrift versuchen
            continue
    _schrift = ("Helvetica", "Helvetica-Bold", False)
    return _schrift


def ersatz_fuer(helvetica_name: str) -> str:
    """'Helvetica' / 'Helvetica-Oblique' -> normal, alles mit 'Bold' -> fett."""
    normal, fett, _ = schriften()
    return fett if "Bold" in str(helvetica_name or "") else normal


def styles_anpassen(styles: Dict) -> Dict:
    """Jede ParagraphStyle auf die Unicode-Schrift umstellen (fett bleibt fett)."""
    _, _, ok = schriften()
    if ok:
        for st in styles.values():
            st.fontName = ersatz_fuer(getattr(st, "fontName", ""))
    return styles
