# -*- coding: utf-8 -*-
"""Helle Ansicht (Pruefung 18.09.2026, Wunsch Ahmad "mach die helle Variante
auch perfekt").

Im hellen Design standen an vielen Stellen Farben, die nur auf dunklem Grund
funktionieren: Gruen/Gelb/Amber/Rot als Schrift (unter 3:1 auf Weiss), helle
Tailwind-Stufen wie text-emerald-400, ein fest dunkler Marktplatz und fest
dunkle Meldungen/Dialoge. Die Tests halten die Regeln fest, damit es nicht
zurueckfaellt:

  1  Jede Statusfarbe ist ein Token und hat in BEIDEN Designs einen Wert.
  2  Die Seiten benutzen die Token statt fester Hex-Farben.
  3  Der Marktplatz folgt dem Design-Schalter (kein data-theme="dark").
  4  Meldungen (sonner) und die shadcn-Dialoge folgen dem Schalter.
  5  Helle Tailwind-Schriftfarben werden im hellen Design umgelenkt.
"""
import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "frontend" / "src"
CSS = (SRC / "index.css").read_text(encoding="utf-8")

# Statusfarben (Termine, Bestand, Banner) und Schrift auf getoenten Flaechen.
TOKEN = ["--st-blau", "--st-himmel", "--st-cyan", "--st-gruen", "--st-gelb",
         "--st-amber", "--st-rot", "--st-grau", "--st-lila",
         "--tx-blau", "--tx-cyan", "--tx-gruen", "--tx-amber", "--tx-rot",
         "--tx-lila"]


def _block(start: str) -> str:
    """Der Inhalt eines Regelblocks ab `start` bis zur schliessenden Klammer."""
    i = CSS.index(start)
    return CSS[i:CSS.index("\n}", i)]


# ---------------------------------------------------------------- Regel 1
@pytest.mark.parametrize("token", TOKEN)
def test_01_statusfarben_gibt_es_in_beiden_designs(token):
    """Ein Token, das nur im dunklen Block steht, erbt im hellen Design den
    dunklen Wert — genau so entstanden die unlesbaren Stellen."""
    # Seit 18.09.2026 gelten die dunklen Werte auch fuer einzelne Bereiche, die
    # immer dunkel bleiben (Startseite, linke Haelfte der Anmeldung) — deshalb
    # heisst der Block jetzt ":root, [data-theme="dark"]".
    dunkel = _block('\n:root, [data-theme="dark"] {')
    hell = _block('\n[data-theme="light"] {')
    assert f"{token}:" in dunkel, f"{token} fehlt im dunklen Design"
    assert f"{token}:" in hell, f"{token} fehlt im hellen Design"
    wert_dunkel = re.search(rf"{token}:\s*([^;]+);", dunkel).group(1).strip()
    wert_hell = re.search(rf"{token}:\s*([^;]+);", hell).group(1).strip()
    assert wert_dunkel != wert_hell, (
        f"{token} ist in beiden Designs gleich — dann bringt das Token nichts")


# ---------------------------------------------------------------- Regel 2
# Datei -> Farben, die dort NICHT mehr als feste Schrift-/Flaechenfarbe
# stehen duerfen (sie sind durch Token ersetzt).
VERBOTEN = {
    "pages/app/Termine.jsx": ['"#34c759"', '"#0a84ff"', '"#ff9f0a"', '"#ffd60a"',
                              '"#8e8e93"', '"#ffb340"', '"#FFB020"'],
    "pages/app/Freigaben.jsx": ['"#34c759"', '"#ff9f0a"', '"#ff8a80"'],
    "pages/app/Inserat.jsx": ['"#34c759"', '"#7dd3fc"'],
    "pages/app/Anfragen.jsx": ['"#34c759"', '"#fbbf24"', '"#60a5fa"', '"#93c5fd"'],
    "pages/app/Bestand.jsx": ['"#fbbf24"', '"#7dd3fc"'],
    "pages/app/Einstellungen.jsx": ['"#ff453a"', '"#ffd9a3"', '"#ffb3a8"', '"#bcd9ff"'],
    "pages/driver/Protokoll.jsx": ['"#34c759"', '"#ff453a"', '"#64a8ff"'],
    "pages/driver/DriverDashboard.jsx": ['"#ff6b5f"'],
    "components/BeweisCard.jsx": ['"#34c759"', '"#ff6b6b"', '"#f5a524"'],
    "lib/fahrzeugStatus.js": ['"#34c759"', '"#eab308"', '"#f59e0b"', '"#71717a"'],
}


@pytest.mark.parametrize("datei,farben", sorted(VERBOTEN.items()))
def test_02_seiten_benutzen_die_token(datei, farben):
    text = (SRC / datei).read_text(encoding="utf-8")
    for farbe in farben:
        assert farbe not in text, (
            f"{datei}: {farbe} steht wieder fest im Quelltext — im hellen "
            f"Design ist diese Farbe auf Weiss kaum zu lesen. Token benutzen "
            f"(--st-* fuer Flaechen/Schrift, --tx-* fuer Schrift auf Tonung).")


def test_02b_statusschild_mischt_ueber_css():
    """Das Schild bekommt die Farbe als Variable; Rahmen und Flaeche mischt
    .status-schild. Frueher wurde "66"/"1a" an den Hex-Wert gehaengt — mit
    einem Token ergibt das Unsinn wie "var(--st-gruen)66"."""
    schild = (SRC / "components" / "StatusSchild.jsx").read_text(encoding="utf-8")
    assert '"--st": farbe' in schild
    assert "status-schild" in schild
    assert "${farbe}" not in schild
    assert ".status-schild" in CSS and "color-mix(in srgb, var(--st)" in CSS


# ---------------------------------------------------------------- Regel 3
def test_03_marktplatz_folgt_dem_schalter():
    markt = (SRC / "pages" / "markt" / "Marktplatz.jsx").read_text(encoding="utf-8")
    assert 'data-theme="dark"' not in markt, (
        "Der Marktplatz war fest dunkel verdrahtet und ignorierte den Schalter")
    for fest in ['"#0a0a0a"', '"#141416"', 'bg-[#141416]', '"#fff"']:
        assert fest not in markt, f"{fest} im Marktplatz — Token benutzen"


# ---------------------------------------------------------------- Regel 4
def test_04_meldungen_und_dialoge_folgen_dem_schalter():
    app = (SRC / "App.jsx").read_text(encoding="utf-8")
    assert 'theme="dark"' not in app, "Die Meldungen waren fest dunkel"
    assert "<Toaster theme={design}" in app
    schalter = (SRC / "components" / "ThemeToggle.jsx").read_text(encoding="utf-8")
    assert "export function useTheme()" in schalter
    # shadcn-Vorlage (Installieren-Dialog, Popover) bringt nur dunkle Werte mit;
    # ohne helle Gegenstuecke stand dort ein schwarzer Kasten auf heller Seite.
    assert "--background: 0 0% 100%" in CSS, "shadcn-Token fehlen fuers helle Design"


# ---------------------------------------------------------------- Regel 5
@pytest.mark.parametrize("klasse", [
    "text-emerald-400", "text-red-400", "text-amber-400", "text-sky-400",
    "text-blue-400", "text-purple-300", "text-red-300", "text-amber-300",
])
def test_05_helle_tailwind_schriftfarben_werden_umgelenkt(klasse):
    """Diese Stufen sind fuer dunklen Grund gemacht (z. B. text-emerald-400
    kommt auf Weiss nur auf 2,0:1)."""
    assert f'[data-theme="light"] .{klasse}' in CSS, (
        f"{klasse} hat keine Umlenkung fuers helle Design")


def test_05b_graue_schrift_im_hellen_design_ist_dunkel_genug():
    """Apples Grautoene (#6e6e73/#86868b) kommen auf hellem Grund nur auf
    3,3:1 — Nebentexte und Wochentage waren kaum zu lesen."""
    hell = _block('\n[data-theme="light"] {')
    for token, verboten in (("--text-secondary", "#6e6e73"),
                            ("--text-muted", "#86868b")):
        wert = re.search(rf"{token}:\s*([^;]+);", hell).group(1).strip()
        assert wert != verboten, f"{token} ist wieder zu hell ({wert})"


# ---------------------------------------------------------------- Regel 6
def test_06_werbeseiten_bleiben_in_beiden_designs_dunkel():
    """Startseite und die linke Haelfte der Anmeldung liegen auf dunklen
    Fotos. Ohne Markierung zog im hellen Design NUR die Schrift ins Helle um
    und stand dunkelgrau auf dunklem Grund (Screenshots Ahmad 18.09.2026)."""
    for datei, marker in (
            (SRC / "pages" / "Landing.jsx", 'className="bleibt-dunkel min-h-screen'),
            (SRC / "pages" / "Login.jsx", 'className="bleibt-dunkel hidden lg:block')):
        q = datei.read_text(encoding="utf-8")
        assert marker in q, f"{datei.name}: Bereich nicht als dauerhaft dunkel markiert"
        assert 'data-theme="dark"' in q, f"{datei.name}: dunkle Token fehlen"
    # Die Ausnahme gilt fuer den markierten Bereich UND alles darin ...
    assert ":not(.bleibt-dunkel):not(.bleibt-dunkel *)" in CSS
    # ... und ein solcher Bereich bringt seine helle Schrift selbst mit,
    # sonst erbt er die dunkle Schrift des hellen Seitenkoerpers.
    assert '[data-theme="dark"] {\n  color: var(--text-primary);\n}' in CSS
