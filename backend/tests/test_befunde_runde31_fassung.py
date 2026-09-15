# -*- coding: utf-8 -*-
"""Runde 31 (12.09.2026) — Vorfall "tote Seite" in Fahrer-App und Super-Admin.

Was passiert ist (gemessen):
  * Rollout Server fuer Server: prod2 lief ab 12:20:35 UTC mit dem neuen
    Stand, prod1 erst ab 12:33:58 UTC. 13 Minuten lang holten Browser die
    Startseite vom einen und Seitenteile vom anderen Server -> 404.
  * Die Oberflaeche lieferte diese 404 mit "public, max-age=31536000,
    immutable" aus. Chromium haelt so einen 404 fest und fragt den Server
    nie wieder (nachgestellt) — Fahrer und Super-Admin kamen danach bei JEDER
    Anmeldung nicht weiter, obwohl beide Server laengst sauber waren.
  * Die Fehlergrenze setzte sich nie zurueck, die Oberflaeche wusste nichts
    von neuen Fassungen, und ein automatisches Neuladen haette ungespeicherte
    Unterschriften vernichtet.

Die Nachbar-Kette selbst ist mit echtem nginx in Docker nachgestellt (zwei
Server, verschiedene Staende, Archiv, Ausfall des Nachbarn); die Oberflaeche
mit Vitest (lib/fassung.test.js, lib/ungespeichert.test.js). Hier wird
festgehalten, dass Vorlagen, Compose, Rollout und Verdrahtung so bleiben.
"""
import re
from pathlib import Path

WURZEL = Path(__file__).resolve().parents[2]


def _lies(*teile) -> str:
    return WURZEL.joinpath(*teile).read_text(encoding="utf-8")


def _ohne_kommentare(text: str, zeichen: str = "#") -> str:
    return "\n".join(z for z in text.splitlines() if not z.lstrip().startswith(zeichen))


def _block(text: str, kopf: str, ab: int = 0) -> str:
    """Den {...}-Block ab `kopf` ausschneiden (zaehlt Klammern; ${VAR} ist
    ausgeglichen und stoert nicht)."""
    i = text.index(kopf, ab)
    tiefe = 0
    for j in range(i, len(text)):
        if text[j] == "{":
            tiefe += 1
        elif text[j] == "}":
            tiefe -= 1
            if tiefe == 0:
                return text[i:j + 1]
    raise AssertionError(f"Block nicht geschlossen: {kopf}")


def _lb():
    return _ohne_kommentare(_lies("deploy", "hinter-loadbalancer.conf.template"))


# ------------------------------------------------------ Nachbar-Kette (nginx)
def test_01_fehlende_bundle_datei_fragt_erst_beide_server():
    t = _lb()
    static = _block(t, "location /static/ {")
    assert "proxy_intercept_errors on;" in static
    assert "recursive_error_pages on;" in static, "ohne das bricht die Kette nach der ersten Station ab"
    assert "error_page 404 = @static_nachbar1;" in static
    assert "@static_fehlt" not in static, "der 404 darf nicht mehr direkt ausgeliefert werden"

    n1 = _block(t, "location @static_nachbar1 {")
    assert "set $nachbar1 http://${PROD1_IP}:8081;" in n1 and "proxy_pass $nachbar1;" in n1
    assert "error_page 403 404 500 502 503 504 = @static_nachbar2;" in n1
    n2 = _block(t, "location @static_nachbar2 {")
    assert "set $nachbar2 http://${PROD2_IP}:8081;" in n2 and "proxy_pass $nachbar2;" in n2
    assert "error_page 403 404 500 502 503 504 = @static_fehlt;" in n2
    for station in (n1, n2):
        # Ist der Nachbar weg (Neubau, Drain), wartet der Besucher kurz.
        assert "proxy_connect_timeout 1s;" in station
        assert "recursive_error_pages on;" in station
        # 200 vom Nachbarn darf lange gecacht werden, Fehler nie (kein always).
        zeile = next(z for z in station.splitlines() if "Cache-Control" in z)
        assert "immutable" in zeile and "always" not in zeile

    ende = _block(t, "location @static_fehlt {")
    assert 'add_header Cache-Control "no-store" always;' in ende and "return 404;" in ende


def test_02_nachbar_block_ist_abgeschottet_und_fragt_nie_weiter():
    t = _lb()
    i = t.index("listen 8081;")
    server = _block(t, "server {", t.rindex("server {", 0, i))
    for zeile in ("allow 127.0.0.1;", "allow ${PROD1_IP};", "allow ${PROD2_IP};", "deny all;"):
        assert zeile in server, zeile
    # Kein real_ip in diesem Block — allow/deny prueft die echte Adresse.
    assert "set_real_ip_from" not in server and "real_ip_header" not in server
    # Nur Bundle-Dateien, nichts sonst.
    assert "location /static/ {" in server and "location / { return 404; }" in server
    assert "/api" not in server
    # Keine Weiterleitung an andere Server: sonst koennten sich beide im Kreis schicken.
    assert "@static_nachbar" not in server and "PROD1_IP}:8081" not in server


def test_03_port_8081_nur_auf_der_privaten_adresse():
    replica = _lies("deploy", "docker-compose.replica.yml")
    proxy = replica[replica.index("\n  proxy:"):]
    assert '- "${PRIVATE_IP}:8081:8081"' in proxy
    compose = _lies("docker-compose.yml")
    assert "8081:8081" not in compose, "im Grund-Compose wuerde 8081 auf allen Adressen lauschen"
    for zeile in ("- PROD1_IP=${PROD1_IP:-127.0.0.1}", "- PROD2_IP=${PROD2_IP:-127.0.0.1}"):
        assert zeile in compose, "ohne Vorgabe waere die Vorlage ungueltig (leere Adresse)"


# ------------------------------------------------------ Fassungs-Stempel
def test_04_stempel_kommt_aus_dem_commit_nicht_aus_der_bauzeit():
    s = _ohne_kommentare(_lies("deploy", "rollout.sh"))
    assert "APP_FASSUNG=$(git log -1 --format=%ct-%h" in s
    assert "export APP_FASSUNG" in s
    assert s.index("git pull --ff-only") < s.index("APP_FASSUNG=$(git log") < s.index("up -d --build"), \
        "erst den neuen Stand holen, dann stempeln, dann bauen"
    compose = _lies("docker-compose.yml")
    assert "APP_FASSUNG: ${APP_FASSUNG:-}" in compose, "Build-Argument der Oberflaeche"
    assert "- APP_FASSUNG=${APP_FASSUNG:-}" in compose, "Umgebung des Backends"
    docker = _lies("frontend", "Dockerfile")
    assert "ARG APP_FASSUNG=" in docker and "ENV APP_FASSUNG=$APP_FASSUNG" in docker
    assert docker.index("ENV APP_FASSUNG=$APP_FASSUNG") < docker.index("RUN CI=true yarn build")
    vite = _lies("frontend", "vite.config.mjs")
    assert '"import.meta.env.APP_FASSUNG"' in vite


def test_05_backend_schickt_den_stempel_nur_wenn_gesetzt():
    s = _lies("backend", "server.py")
    assert 'APP_FASSUNG = os.environ.get("APP_FASSUNG", "").strip()' in s
    block = s[s.index("class SecurityHeadersMiddleware"):s.index("class ErrorReportingMiddleware")]
    assert re.search(r'if APP_FASSUNG:\s*\n\s*response\.headers\.setdefault\("X-AH-Fassung", APP_FASSUNG\)', block), block
    assert block.count("X-AH-Fassung") == 1


# ------------------------------------------------------ Oberflaeche selbst
def test_06_fehlende_dateien_werden_nie_mehr_lange_gecacht():
    docker = _lies("frontend", "Dockerfile")
    assert "try_files $uri /alt$uri @fehlt;" in docker
    assert 'location @fehlt { add_header Cache-Control "no-store" always; return 404; }' in docker
    assert "location /alt/ { internal; }" in docker, "das Archiv ist kein oeffentlicher Pfad"


def test_07_einmalig_neue_dateinamen_fuer_alle():
    """Wer den 404 festhaelt, fragt nie wieder — nur ein neuer Name hilft."""
    vite = _lies("frontend", "vite.config.mjs")
    assert 'banner: "/*! AutoSchnell R31 */"' in vite


def test_08_alle_drei_verbindungen_hoeren_mit():
    assert "fassungMithoeren(api);" in _lies("frontend", "src", "lib", "api.js")
    assert "fassungMithoeren(driverApi);" in _lies("frontend", "src", "context", "DriverContext.jsx")
    assert "fassungMithoeren(buyerApi);" in _lies("frontend", "src", "context", "BuyerContext.jsx")


def test_09_nachladen_heilt_den_zwischenspeicher_und_schont_eingaben():
    app = _lies("frontend", "src", "App.jsx")
    seite = app[app.index("function seite(laden)"):app.index("// Angemeldete Nutzer")]
    assert "await zwischenspeicherErneuern(fehler);" in seite
    assert 'cache: "reload"' in app
    assert seite.index("hatUngespeichert()") < seite.index("window.location.reload()"), \
        "nie neu laden, solange Eingaben offen sind"
    assert "nachladenGescheitert();" in seite
    assert "<FassungsHinweis />" in app
    for teile, aufruf in (
        (("src", "pages", "driver", "Protokoll.jsx"), "useUngespeichert(Boolean((sigDriver || sigSeller) && !isFinal));"),
        (("src", "components", "ContractDialog.jsx"), "useUngespeichert(Boolean(open));"),
        (("src", "components", "AbholCheckDialog.jsx"), "useUngespeichert(Boolean(mileage || notes.trim() || deviations.length));"),
        (("src", "pages", "admin_v2", "Settings.jsx"), "useUngespeichert(Boolean(codes?.length));"),
    ):
        assert aufruf in _lies("frontend", *teile), teile[-1]


def test_10_fehlergrenze_haengt_nicht_mehr_fest():
    s = _lies("frontend", "src", "components", "NachladeFehler.jsx")
    assert "componentDidUpdate(vorher)" in s
    assert "vorher.pfad !== this.props.pfad" in s
    assert "useLocation()" in s


def test_11_nach_der_anmeldung_die_neue_fassung():
    for teile in (("src", "pages", "Login.jsx"),
                  ("src", "pages", "driver", "DriverLogin.jsx"),
                  ("src", "pages", "markt", "BuyerLogin.jsx")):
        assert "if (!neueFassungLaden(" in _lies("frontend", *teile), teile[-1]


# ------------------------------------------------------ Betrieb
def test_12_aufheben_meldet_sich_laut_und_haelt_90_tage():
    s = _ohne_kommentare(_lies("deploy", "rollout.sh"))
    assert "WARNUNG: kein laufender Oberflaechen-Container" in s
    assert "WARNUNG: Bundle-Dateien liessen sich nicht" in s
    assert "-mtime +90 -delete" in s and "-mtime +14" not in s
    assert "-type d -empty -delete" in s


def test_13_doku_und_ci():
    d = _lies("DEPLOYMENT.md")
    for wort in ("8081", "APP_FASSUNG", "X-AH-Fassung", "12:33:58 UTC", "Purge Everything"):
        assert wort in d, wort
    rollback = d[d.index("**Rollback**"):d.index("**Zwischen den beiden Servern**")]
    assert "export APP_FASSUNG=" in rollback and "deploy/assets-alt/static/" in rollback
    ci = _lies(".github", "workflows", "ci.yml")
    assert "Hauptskript ueber die LB-Vorlage" in ci


# ------------------------------------------------------ Rollout-Probe
def _probe_modul():
    import importlib.util
    pfad = WURZEL / "backend" / "scripts" / "betriebsprobe.py"
    spec = importlib.util.spec_from_file_location("betriebsprobe_r31", pfad)
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


class _Antwort:
    def __init__(self, code, text=""):
        self.status_code, self.text, self.headers = code, text, {}


_START = '<script type="module" crossorigin src="/static/js/main.Mm_1.js"></script>'
# So nennt ein Vite-Bau seine Seitenteile: in der Abhaengigkeitsliste mit
# "static/js/", beim dynamischen Import mit "./".
_HAUPT = ('const __vite__mapDeps=(i,m=__vite__mapDeps,d=(m.f||(m.f=["static/js/DriverLayout.AAAA1111.chunk.js",'
          '"static/js/react.BBBB2222.chunk.js"])))=>i.map(i=>d[i]);'
          'const x=()=>import("./Comparisons.CCCC3333.chunk.js");')


def _probe_lauf(monkeypatch, codes_je_teil=None, cache_code=200, zwischenstand=False):
    import types
    modul = _probe_modul()
    geholt, zaehler = [], {}

    def get(url, **_kw):
        geholt.append(url)
        if url.endswith("/"):
            return _Antwort(200, _START)
        if "/static/js/main." in url:
            return _Antwort(200, _HAUPT)
        name = url.split("/static/js/")[1].split("?")[0]
        if "?probe=" not in url:
            return _Antwort(cache_code)
        i = zaehler.get(name, 0)
        zaehler[name] = i + 1
        folge = (codes_je_teil or {}).get(name, [200])
        return _Antwort(folge[i % len(folge)])

    monkeypatch.setattr(modul, "requests", types.SimpleNamespace(get=get))
    modul.oberflaeche_pruefen("app.example.test", zwischenstand=zwischenstand)
    return modul, geholt


def test_14_probe_prueft_alle_nachladbaren_seitenteile(monkeypatch):
    modul, geholt = _probe_lauf(monkeypatch)
    assert not modul.FEHLER and not modul.WARNUNGEN, (modul.FEHLER, modul.WARNUNGEN)
    assert any("3 nachladbare Seitenteile" in o for o in modul.OK), modul.OK
    for name in ("DriverLayout.AAAA1111.chunk.js", "react.BBBB2222.chunk.js", "Comparisons.CCCC3333.chunk.js"):
        frisch = [u for u in geholt if name in u and "?probe=" in u]
        assert len(frisch) >= 2, f"{name}: beide Server muessen drankommen ({frisch})"


def test_15_fehlt_ein_teil_auf_einem_server_ist_das_ein_fehler(monkeypatch):
    """Genau der Vorfall: der eine Server liefert 200, der andere 404."""
    modul, _ = _probe_lauf(monkeypatch, {"DriverLayout.AAAA1111.chunk.js": [200, 404]})
    assert len(modul.FEHLER) == 1, modul.FEHLER
    assert "DriverLayout.AAAA1111.chunk.js" in modul.FEHLER[0] and "tote Seite" in modul.FEHLER[0]


def test_16_zwischen_den_servern_nur_eine_warnung(monkeypatch):
    modul, _ = _probe_lauf(monkeypatch, {"DriverLayout.AAAA1111.chunk.js": [200, 404]}, zwischenstand=True)
    assert not modul.FEHLER and len(modul.WARNUNGEN) == 1, (modul.FEHLER, modul.WARNUNGEN)
    # 5xx bleibt auch zwischen den Servern ein Fehler.
    modul, _ = _probe_lauf(monkeypatch, {"DriverLayout.AAAA1111.chunk.js": [200, 502]}, zwischenstand=True)
    assert len(modul.FEHLER) == 1, modul.FEHLER


def test_17_cloudflare_haelt_einen_fehler_fest(monkeypatch):
    modul, _ = _probe_lauf(monkeypatch, cache_code=404)
    assert len(modul.FEHLER) == 3 and all("Cloudflare" in f for f in modul.FEHLER), modul.FEHLER


def test_18_das_muster_passt_auf_den_echten_bau():
    """Gegen den echten Vite-Bau (lokal vorhanden, in CI uebersprungen): alle
    gefundenen Namen existieren als Datei, und es sind nicht zu wenige."""
    import pytest
    js = WURZEL / "frontend" / "build" / "static" / "js"
    haupt = sorted(js.glob("main.*.js")) if js.exists() else []
    if not haupt:
        pytest.skip("kein Frontend-Bau vorhanden")
    text = haupt[0].read_text(encoding="utf-8")
    namen = set(re.findall(r"(?:\./|static/js/)([A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.chunk\.js)", text))
    assert len(namen) >= 10, namen
    for n in namen:
        assert (js / n).exists(), n
