# -*- coding: utf-8 -*-
"""Pruefbericht 20.09.2026, Reparaturwelle A1 (Frontend-Kern, Netz, Sitzung, PWA).

Quelltext-Pruefungen fuer Aenderungen, die sich in Vitest nicht sinnvoll
fassen lassen (App.jsx zieht alle Seiten; vite.config, manifest.json und der
Service Worker sind keine Module) — plus die Backend-Seite von K-06.
Die Verhaltens-Tests liegen daneben in Vitest (frontend/src/**.test.js[x]).

  K-06        Rand-Leerzeichen auch am Server abgelehnt; Meldung "Bytes" beidseitig
  K-07        index.jsx: Fehlermelder VOR dem Design
  K-08        vite.config: connect-src aus REACT_APP_BACKEND_URL
  K-11        App.jsx: Zeitlimit beim Nachladen einer Seite
  K-15/K-17   manifest.json: keine Verknuepfung auf /fahrer oder /app/vergleich
  K-16        Service Worker: Offline-Seite laedt nur bei r.ok neu
  K-20/RE-11  kein Stripe mehr in CSP und requirements.txt
  M-15        .apple-fab mit safe-area
  U-148       Route "*" zeigt NichtGefunden statt <Navigate to="/">
  U-150       zweite, routerlose Fehlergrenze in index.jsx
"""
import json
import re
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
WURZEL = BACKEND.parent
sys.path.insert(0, str(BACKEND))


def _lies(*teile):
    return (WURZEL / Path(*teile)).read_text(encoding="utf-8")


# ------------------------------------------------------------------- K-06
def test_k06_rand_leerzeichen_auch_am_server():
    import passwoerter
    for pw in (" Sommerregen2026!", "Sommerregen2026! ", "\tSommerregen2026!"):
        with pytest.raises(ValueError, match="Leerzeichen"):
            passwoerter.pruefe_passwort(pw)
    assert passwoerter.pruefe_passwort("Sommerregen2026!") == "Sommerregen2026!"
    # Leerzeichen IM Passwort bleiben erlaubt (Sonderzeichen).
    assert passwoerter.pruefe_passwort("Sommer regen2026") == "Sommer regen2026"


def test_k06_meldung_nennt_bytes_auf_beiden_seiten():
    import passwoerter
    with pytest.raises(ValueError, match="72 Bytes"):
        passwoerter.pruefe_passwort("ä" * 40 + "1")          # 81 Bytes, 41 Zeichen
    js = _lies("frontend", "src", "lib", "passwort.js")
    assert "Bytes lang sein" in js, "die Oberflaeche sagte 'Zeichen', gemessen wurden Bytes"
    assert "Zeichen lang sein`" not in js.split("PASSWORT_MAX_BYTES) {")[1][:200]


# ------------------------------------------------------------------- K-07 / U-150
def test_k07_fehlermelder_vor_dem_design():
    s = _lies("frontend", "src", "index.jsx")
    assert s.index("installErrorReporter();") < s.index("applyStoredTheme();")


def test_u150_zweite_fehlergrenze_ohne_router():
    s = _lies("frontend", "src", "index.jsx")
    assert "<FehlerGrenze>" in s and "<App />" in s
    assert s.index("<FehlerGrenze>") < s.index("<App />") < s.index("</FehlerGrenze>")
    grenze = _lies("frontend", "src", "components", "NachladeFehler.jsx")
    assert "export class FehlerGrenze" in grenze
    assert "export function istNachladefehler" in grenze
    # Runde 31 bleibt: die Grenze setzt sich beim Seitenwechsel zurueck.
    assert "vorher.pfad !== this.props.pfad" in grenze


# ------------------------------------------------------------------- K-08
def test_k08_csp_connect_folgt_der_backend_adresse():
    vite = _lies("frontend", "vite.config.mjs")
    assert "export function cspConnectStandard(env, dev)" in vite
    fn = vite.split("export function cspConnectStandard")[1].split("\nfunction dateiname")[0]
    assert "new URL(backend).origin" in fn
    assert "throw new Error(" in fn, "eine ungueltige Adresse muss den Bau abbrechen"
    assert "REACT_APP_CSP_CONNECT: cspConnectStandard(env, dev)" in vite


# ------------------------------------------------------------------- K-11 / U-148
def test_k11_nachladen_mit_zeitlimit():
    app = _lies("frontend", "src", "App.jsx")
    seite = app[app.index("function seite(laden)"):app.index("// Angemeldete Nutzer")]
    assert "return await mitZeitlimit(laden());" in seite
    assert "const LADEN_ZEITLIMIT_MS = 20000;" in app
    assert "clearTimeout(t)" in app.split("function mitZeitlimit")[1][:600]


def test_u148_unbekannte_adresse_ohne_blinde_weiterleitung():
    app = _lies("frontend", "src", "App.jsx")
    assert '<Route path="*" element={<NichtGefunden />} />' in app
    assert '<Route path="*" element={<Navigate to="/" replace />} />' not in app
    assert "nichtGefundenZiel(user, pathname)" in app
    assert "export function nichtGefundenZiel" in _lies("frontend", "src", "lib", "rollen.js")


# ------------------------------------------------------------------- K-15 / K-17
def test_k15_k17_manifest_verknuepfungen():
    m = json.loads(_lies("frontend", "public", "manifest.json"))
    urls = [s["url"] for s in m.get("shortcuts", [])]
    assert "/fahrer" not in urls, "K-15: Fahrer sehen sonst Vergleich/Termine, Sucher die Fahrer-Anmeldung"
    assert "/app/vergleich" not in urls, "K-17: Chef ohne Abo landete auf /abo"
    # Was bleibt, ist fuer Chef UND Sucher ohne Abo erreichbar.
    assert urls and all(u in ("/app/termine", "/app/vertraege", "/start") for u in urls), urls
    assert m["start_url"] == "/start"


# ------------------------------------------------------------------- K-16
def test_k16_offline_seite_laedt_nur_bei_guter_antwort():
    sw = _lies("frontend", "public", "service-worker.js")
    assert ".then(function (r) { if (r.ok) location.reload(); }, function () {});" in sw
    assert ".then(function () { location.reload(); }" not in sw


# ------------------------------------------------------------------- K-20 / RE-11
def test_k20_re11_kein_stripe_mehr():
    html = _lies("frontend", "index.html")
    csp = re.search(r'content="([^"]*connect-src[^"]*)"', html).group(1)
    assert "stripe" not in csp.lower()
    assert "connect-src 'self' %REACT_APP_CSP_CONNECT%;" in csp
    req = _lies("backend", "requirements.txt").splitlines()
    assert not any(z.strip().lower().startswith("stripe") for z in req)
    # Und wirklich kein Import mehr im Backend:
    for datei in sorted(BACKEND.glob("**/*.py")):
        if "tests" in datei.parts:
            continue
        for zeile in datei.read_text(encoding="utf-8", errors="replace").splitlines():
            assert not re.match(r"\s*(import stripe|from stripe)", zeile), f"{datei.name}: {zeile}"


# ------------------------------------------------------------------- M-15
def test_m15_fab_mit_safe_area():
    css = _lies("frontend", "src", "index.css")
    block = css[css.index(".apple-fab {"):]
    block = block[:block.index("}")]
    assert "env(safe-area-inset-bottom" in block
    assert "env(safe-area-inset-right" in block
