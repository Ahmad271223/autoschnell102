# -*- coding: utf-8 -*-
"""Rollout ohne 502 hinter dem Load Balancer (Vorfall 07.09.2026, 15:04 UTC).

Der Load Balancer prueft nur /api/health (Backend). Waehrend "docker compose
up -d --build" den Oberflaechen-Container neu baute, blieb das Backend
gesund, der LB schickte weiter Besucher hin, und alle statischen Dateien
antworteten rund 45 s mit 502. Abhilfe: Drain-Marker in der LB-Vorlage und
deploy/rollout.sh, das einen Server je Aufruf aus der Rotation nimmt.
Der funktionale Teil (503 mit Marker, 200 ohne) laeuft in der CI-Stack-Probe.
"""
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

WURZEL = Path(__file__).resolve().parents[2]


def test_lb_vorlage_hat_drain_marker_in_beiden_health_locations():
    t = (WURZEL / "deploy" / "hinter-loadbalancer.conf.template").read_text(encoding="utf-8")
    bloecke = re.findall(r"location = /api/health \{(.*?)\n  \}", t, re.S)
    assert len(bloecke) == 2, "default_server- und Domain-Block brauchen je einen Health-Block"
    for b in bloecke:
        assert "if (-f /etc/nginx/drain/aktiv) { return 503; }" in b, b
        assert "if (-f /tmp/drain) { return 503; }" in b, b
        assert "auth_request /_oberflaeche_ok;" in b, "Health muss die Oberflaeche mitpruefen"
        assert "proxy_pass $backend_up;" in b
    assert t.count("location = /_oberflaeche_ok {") == 2
    assert "proxy_pass $web_up/;" in t and "proxy_method HEAD;" in t
    # Docker-DNS: nginx darf die Container-Adressen nicht nur beim Start aufloesen
    assert t.count("resolver 127.0.0.11 valid=10s ipv6=off;") == 2
    assert "proxy_pass http://" not in t, "feste Upstream-Adressen veralten nach einem Container-Neustart"
    d = (WURZEL / "deploy" / "default.conf.template").read_text(encoding="utf-8")
    assert "resolver 127.0.0.11" in d and "proxy_pass http://" not in d
    # Fehlerantworten duerfen nie als "ein Jahr cachebar" markiert sein — sonst
    # haelt Cloudflare einen 502 fest (Vorfall 07.09.2026: schwarzer Bildschirm)
    for text in (t, d):
        for zeile in text.splitlines():
            if "add_header Cache-Control" in zeile and "max-age=31536000" in zeile:
                assert "always" not in zeile, zeile
        # 08.09.2026: waehrend eines Rollouts Server fuer Server kennt der alte
        # Server das neue Bundle nicht -> 404; Cloudflare cacht 404 drei
        # Minuten. Die 404 muss deshalb "no-store" tragen, die 200 nicht.
        assert "proxy_intercept_errors on;" in text and "error_page 404 = @static_fehlt;" in text
        i = text.index("location @static_fehlt {")
        assert "add_header Cache-Control \"no-store\" always;" in text[i:i + 200]
        assert "return 404;" in text[i:i + 200]
    compose = (WURZEL / "docker-compose.yml").read_text(encoding="utf-8")
    assert "./deploy/drain:/etc/nginx/drain:ro" in compose, "Host-Marker muss eingehaengt sein"
    assert (WURZEL / "deploy" / "drain" / ".gitkeep").exists()
    assert "deploy/drain/aktiv" in (WURZEL / ".gitignore").read_text(encoding="utf-8")


def test_rollout_skript_setzt_drain_baut_und_hebt_auf():
    roh = (WURZEL / "deploy" / "rollout.sh").read_text(encoding="utf-8")
    assert roh.startswith("#!/bin/sh")
    # nur ausfuehrbare Zeilen (der Kopfkommentar nennt die Befehle ebenfalls)
    s = "\n".join(z for z in roh.splitlines() if not z.lstrip().startswith("#"))
    assert "touch /tmp/drain" in s and "rm -f /tmp/drain" in s
    assert "touch deploy/drain/aktiv" in s and "rm -f deploy/drain/aktiv" in s, \
        "Host-Marker muss gesetzt und entfernt werden (ueberlebt Proxy-Neustart)"
    assert s.index("touch /tmp/drain") < s.index("git pull --ff-only") < s.index("up -d --build")
    assert "/api/ready" in s, "Backend-Bereitschaft wird abgewartet"
    assert "http://127.0.0.1/" in s, "Oberflaeche wird ueber den Proxy geprueft"
    # Befund 08.09.2026: der EXIT-Trap hob den Drain bei JEDEM Abbruch auf und
    # gab damit einen halb fertigen Server wieder an den Load Balancer.
    assert "trap abbruch EXIT" in s and "trap undrain" not in s
    assert "trap 'abbruch 130' INT" in s and "trap 'abbruch 143' TERM" in s
    # Runde 21 (Pruefbefund D, Zusatz): erst den Marker wirklich entfernen,
    # dann den Drain-Trap abloesen — scheitert undrain, stimmt "BLEIBT im Drain".
    aufruf = re.search(r"^undrain$", s, re.M)
    assert aufruf, "undrain wird auf oberster Ebene (Erfolgspfad) aufgerufen"
    assert aufruf.start() < s.index("DRAIN_AUFGEHOBEN=1") < s.index("trap in_rotation_abbruch EXIT"), \
        "Drain wird nur auf dem Erfolgspfad aufgehoben, danach uebernimmt der Rotations-Trap"
    assert "freigeben.sh" in s, "Abbruchmeldung nennt den Weg zur Freigabe"
    assert "COMPOSE_FILE=docker-compose.yml:deploy/docker-compose.replica.yml" in s
    assert "PUBLIC_HOST" in s


def test_probe_von_aussen_prueft_die_oberflaeche_wie_der_browser():
    """Vorfall 07.09.2026: Server gesund, Cloudflare lieferte trotzdem einen
    gecachten 502 fuer das Skript der Oberflaeche. Die Betriebsprobe muss
    das erkennen, und das Rollout ruft sie am Ende auf."""
    import importlib.util
    pfad = WURZEL / "backend" / "scripts" / "betriebsprobe.py"
    spec = importlib.util.spec_from_file_location("betriebsprobe", pfad)
    modul = importlib.util.module_from_spec(spec); spec.loader.exec_module(modul)
    assert callable(getattr(modul, "oberflaeche_pruefen", None))
    quelle = pfad.read_text(encoding="utf-8")
    assert "Purge Everything" in quelle and "?probe=" in quelle, "Cache-Umgehung zur Unterscheidung fehlt"
    assert "oberflaeche_pruefen(host" in quelle[quelle.index("def main()"):]
    roh = (WURZEL / "deploy" / "rollout.sh").read_text(encoding="utf-8")
    s = "\n".join(z for z in roh.splitlines() if not z.lstrip().startswith("#"))
    assert "scripts/betriebsprobe.py" in s and s.index("rm -f deploy/drain/aktiv") < s.index("scripts/betriebsprobe.py")


def test_doku_und_ci_kennen_den_rollout():
    d = (WURZEL / "DEPLOYMENT.md").read_text(encoding="utf-8")
    assert "sh deploy/rollout.sh" in d and "Drain" in d
    ci = (WURZEL / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "touch /tmp/drain" in ci and '[ "$CODE" = 503 ]' in ci


# ============================================================ Fehlerpfad wirklich ausfuehren
_SH = shutil.which("sh")

_FAKE_DOCKER = """#!/bin/sh
# Nachbau von "docker compose ..." fuer den Rollout-Test. Verhalten per Umgebung:
#   FAKE_BUILD_FAIL=1  -> "up -d --build" scheitert
#   FAKE_READY_FAIL=1  -> /api/ready antwortet nie
#   FAKE_WEB_FAIL=1    -> Oberflaeche antwortet nie
#   FAKE_PROBE_RC=<n>  -> Betriebsprobe (Schritt 6/6) endet mit Code n
#   FAKE_TERM_BEI=<t>  -> schickt dem aufrufenden Skript ein Signal (FAKE_SIG,
#                         Standard TERM), wenn der Aufruf <t> enthaelt (wie ein
#                         "kill" von aussen oder Strg+C mit FAKE_SIG=INT)
echo "docker $*" >> "$FAKE_LOG"
if [ -n "$FAKE_TERM_BEI" ]; then
  case "$*" in *"$FAKE_TERM_BEI"*) kill -"${FAKE_SIG:-TERM}" "$PPID" ;; esac
fi
case "$*" in
  *betriebsprobe*)
    if [ "${FAKE_PROBE_RC:-0}" = 0 ]; then echo "ERGEBNIS: 9 ok, 0 Warnungen, 0 Fehler"
    else echo "ERGEBNIS: 8 ok, 0 Warnungen, 1 Fehler"; fi
    exit "${FAKE_PROBE_RC:-0}" ;;
  *"up -d --build"*) [ "$FAKE_BUILD_FAIL" = 1 ] && exit 1; exit 0 ;;
  *"/api/ready"*)    [ "$FAKE_READY_FAIL" = 1 ] && exit 1; exit 0 ;;
  *"wget"*)          [ "$FAKE_WEB_FAIL" = 1 ] && exit 1; exit 0 ;;
esac
exit 0
"""
_FAKE_GIT = """#!/bin/sh
echo "git $*" >> "$FAKE_LOG"
exit 0
"""
# Runde 21: rm, das den Drain-Marker nicht entfernen kann (z.B. root-eigene
# Datei). Alles andere geht an das echte rm.
_FAKE_RM = """#!/bin/sh
case "$*" in
  *deploy/drain/aktiv*) echo "rm: cannot remove 'deploy/drain/aktiv': Permission denied" >&2; exit 1 ;;
esac
for r in /usr/bin/rm /bin/rm; do [ -x "$r" ] && exec "$r" "$@"; done
exit 1
"""
# Runde 21 (Gegenpruefung D): touch, das den Drain-Marker nicht anlegen kann.
_FAKE_TOUCH = """#!/bin/sh
case "$*" in
  *deploy/drain/aktiv*) echo "touch: cannot touch 'deploy/drain/aktiv': Permission denied" >&2; exit 1 ;;
esac
for r in /usr/bin/touch /bin/touch; do [ -x "$r" ] && exec "$r" "$@"; done
exit 1
"""


def _skript_lauf(tmp_path, skript, fehler=None, args=(), extra_env=None, rm_scheitert=False,
                 touch_scheitert=False):
    """rollout.sh / freigeben.sh in einem Wegwerf-Checkout mit Docker-/Git-
    Attrappen ausfuehren. Liefert (Rueckgabecode, Ausgabe, Marker-vorhanden,
    Liste der Attrappen-Aufrufe)."""
    verz = tmp_path / "checkout"
    (verz / "deploy" / "drain").mkdir(parents=True, exist_ok=True)
    (verz / ".env").write_text("PUBLIC_HOST=app.example.test\n", encoding="utf-8")
    for name in ("rollout.sh", "freigeben.sh"):
        shutil.copy(WURZEL / "deploy" / name, verz / "deploy" / name)
    fake = tmp_path / "bin"
    fake.mkdir(exist_ok=True)
    attrappen = [("docker", _FAKE_DOCKER), ("git", _FAKE_GIT)]
    if rm_scheitert:
        attrappen.append(("rm", _FAKE_RM))
    if touch_scheitert:
        attrappen.append(("touch", _FAKE_TOUCH))
    for name, inhalt in attrappen:
        f = fake / name
        f.write_text(inhalt, encoding="utf-8", newline="\n")
        f.chmod(f.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    log = tmp_path / "aufrufe.log"
    env = dict(os.environ)
    # Schalter aus der Umgebung des Testlaufs duerfen nicht durchschlagen
    for k in ("OHNE_AUSSENPROBE", "ERSTER_SERVER", "FAKE_TERM_BEI", "FAKE_SIG"):
        env.pop(k, None)
    env.update({"PATH": str(fake) + os.pathsep + env.get("PATH", ""),
                "VERZ": str(verz).replace("\\", "/"), "WARTE_LB": "0", "SCHLAF": "0",
                "FAKE_LOG": str(log).replace("\\", "/"),
                "FAKE_BUILD_FAIL": "0", "FAKE_READY_FAIL": "0", "FAKE_WEB_FAIL": "0",
                "FAKE_PROBE_RC": "0"})
    if fehler:
        env[fehler] = "1"
    env.update(extra_env or {})
    r = subprocess.run([_SH, str(verz / "deploy" / skript), *args], env=env,
                       capture_output=True, text=True, timeout=120)
    aufrufe = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return r.returncode, r.stdout + r.stderr, (verz / "deploy" / "drain" / "aktiv").exists(), aufrufe


@pytest.mark.skipif(not _SH, reason="kein sh vorhanden")
@pytest.mark.parametrize("fehler", ["FAKE_BUILD_FAIL", "FAKE_READY_FAIL", "FAKE_WEB_FAIL"])
def test_rollout_abbruch_laesst_server_im_drain(tmp_path, fehler):
    """Scheitert Build, Bereitschaft oder Oberflaeche, darf der Drain-Marker
    NICHT entfernt werden — sonst fuehrt der Load Balancer einen defekten
    Server wieder (Befund 08.09.2026)."""
    rc, out, marker, aufrufe = _skript_lauf(tmp_path, "rollout.sh", fehler)
    assert rc != 0, out
    assert marker, "Drain-Marker muss nach Abbruch bestehen bleiben:\n" + out
    assert "BLEIBT im Drain" in out and "freigeben.sh" in out, out
    assert not any("betriebsprobe" in a for a in aufrufe), "keine Probe von aussen nach Abbruch"
    if fehler != "FAKE_BUILD_FAIL":
        assert any("up -d --build" in a for a in aufrufe)
    # Marker im Proxy-Container wurde ebenfalls nicht entfernt
    assert not any("rm -f /tmp/drain" in a for a in aufrufe), aufrufe


@pytest.mark.skipif(not _SH, reason="kein sh vorhanden")
def test_rollout_erfolg_hebt_drain_auf_und_prueft_von_aussen(tmp_path):
    rc, out, marker, aufrufe = _skript_lauf(tmp_path, "rollout.sh")
    assert rc == 0, out
    assert not marker
    reihenfolge = [a for a in aufrufe if any(k in a for k in
                   ("touch /tmp/drain", "pull --ff-only", "up -d --build", "/api/ready",
                    "wget", "rm -f /tmp/drain", "betriebsprobe"))]
    assert "docker compose exec -T proxy sh -c touch /tmp/drain" in reihenfolge[0]
    assert reihenfolge.index(next(a for a in reihenfolge if "pull --ff-only" in a)) \
        < reihenfolge.index(next(a for a in reihenfolge if "up -d --build" in a)) \
        < reihenfolge.index(next(a for a in reihenfolge if "rm -f /tmp/drain" in a)) \
        < reihenfolge.index(next(a for a in reihenfolge if "betriebsprobe" in a))
    assert "FERTIG" in out


@pytest.mark.skipif(not _SH, reason="kein sh vorhanden")
def test_freigeben_verweigert_bei_kaputtem_backend_oder_oberflaeche(tmp_path):
    for fehler in ("FAKE_READY_FAIL", "FAKE_WEB_FAIL"):
        verz = tmp_path / fehler
        verz.mkdir()
        (verz / "checkout" / "deploy" / "drain").mkdir(parents=True)
        (verz / "checkout" / "deploy" / "drain" / "aktiv").touch()
        rc, out, marker, aufrufe = _skript_lauf(verz, "freigeben.sh", fehler)
        assert rc != 0 and marker, (fehler, out)
        assert "Freigabe verweigert" in out
        assert not any("rm -f /tmp/drain" in a for a in aufrufe)


@pytest.mark.skipif(not _SH, reason="kein sh vorhanden")
def test_freigeben_entfernt_marker_bei_gesundem_server(tmp_path):
    (tmp_path / "checkout" / "deploy" / "drain").mkdir(parents=True)
    (tmp_path / "checkout" / "deploy" / "drain" / "aktiv").touch()
    rc, out, marker, aufrufe = _skript_lauf(tmp_path, "freigeben.sh")
    assert rc == 0 and not marker, out
    assert any("rm -f /tmp/drain" in a for a in aufrufe)
    assert any("/api/ready" in a for a in aufrufe) and any("wget" in a for a in aufrufe)
    # --erzwingen: ohne Pruefung, aber laut
    (tmp_path / "checkout" / "deploy" / "drain" / "aktiv").touch()
    rc, out, marker, aufrufe = _skript_lauf(tmp_path, "freigeben.sh", args=("--erzwingen",))
    assert rc == 0 and not marker and "OHNE Pruefung" in out


# ============================================================ Runde 21: Abschlusspruefung (Befund D)
@pytest.mark.skipif(not _SH, reason="kein sh vorhanden")
@pytest.mark.parametrize("probe_rc", ["1", "2", "126"])
def test_rollout_aussenprobe_scheitert_meldet_kein_fertig(tmp_path, probe_rc):
    """Runde 21 (Pruefbefund D): Frueher meldete das Skript auch nach einer
    gescheiterten Probe von aussen "FERTIG ... jetzt denselben Befehl auf dem
    anderen Server" und endete mit 0. Jetzt: Code 3, kein FERTIG, klare
    Anweisung, NICHT auf dem anderen Server weiterzumachen. Der Server bleibt
    bewusst in der Rotation (kein erneuter Drain, keine Meldung "im Drain")."""
    rc, out, marker, aufrufe = _skript_lauf(tmp_path, "rollout.sh", extra_env={"FAKE_PROBE_RC": probe_rc})
    assert rc == 3, out
    assert "FERTIG" not in out, out
    assert "ABSCHLUSSPRUEFUNG GESCHEITERT" in out and "NICHT auf dem anderen Server" in out, out
    assert f"Probe-Code {probe_rc}" in out
    # konkrete Pruefschritte: Probe wiederholen, Cache, anderer Server, Logs
    assert "scripts/betriebsprobe.py app.example.test" in out, "Befehl zum Wiederholen fehlt"
    assert "Purge Everything" in out and "Load Balancer -> Ziele" in out and "logs --tail 80" in out
    assert "--zwischenstand" in out, "Hinweis auf den Zwischenstand beim ersten Server fehlt"
    # Gegenpruefung: passt auch auf dem ZWEITEN Server (nichts erneut starten)
    assert "falls dort noch nicht ausgerollt" in out and "schon ausgerollt" in out, out
    if probe_rc == "1":
        # Code 1 kommt auch von gestopptem Container / Absturz der Probe
        assert "keine ERGEBNIS-Zeile" in out, out
    # in der Rotation: Drain aufgehoben, KEIN erneuter Drain, keine Drain-Meldung
    assert not marker, out
    assert "BLEIBT im Drain" not in out and "UNTERBROCHEN" not in out, out
    assert out.count("ABSCHLUSSPRUEFUNG GESCHEITERT") == 1
    rm_idx = [i for i, a in enumerate(aufrufe) if "rm -f /tmp/drain" in a]
    assert len(rm_idx) == 1, aufrufe
    assert not any("touch /tmp/drain" in a for a in aufrufe[rm_idx[0]:]), "kein erneuter Drain"
    assert sum("betriebsprobe" in a for a in aufrufe) == 1


@pytest.mark.skipif(not _SH, reason="kein sh vorhanden")
def test_rollout_aussenprobe_ok_meldet_fertig(tmp_path):
    rc, out, marker, aufrufe = _skript_lauf(tmp_path, "rollout.sh")
    assert rc == 0, out
    assert "FERTIG auf" in out and "ERGEBNIS: 9 ok, 0 Warnungen, 0 Fehler" in out, out
    assert "NICHT auf dem anderen Server" not in out and "GESCHEITERT" not in out
    assert "UEBERSPRUNGEN" not in out and "UNTERBROCHEN" not in out
    probe = [a for a in aufrufe if "betriebsprobe" in a]
    assert len(probe) == 1 and "--zwischenstand" not in probe[0], probe
    assert not marker


@pytest.mark.skipif(not _SH, reason="kein sh vorhanden")
def test_rollout_ohne_aussenprobe_nur_bewusst_und_laut(tmp_path):
    rc, out, marker, aufrufe = _skript_lauf(tmp_path / "a", "rollout.sh",
                                            extra_env={"OHNE_AUSSENPROBE": "1", "FAKE_PROBE_RC": "1"})
    assert rc == 0, out
    assert not any("betriebsprobe" in a for a in aufrufe), aufrufe
    assert "UEBERSPRUNGEN" in out and "OHNE Abschlusspruefung" in out, out
    assert "scripts/betriebsprobe.py app.example.test" in out, "Befehl fuer die Probe von Hand fehlt"
    assert not marker and any("rm -f /tmp/drain" in a for a in aufrufe)
    # Nur der Wert 1 schaltet ab — alles andere laesst die Probe laufen
    rc, out, marker, aufrufe = _skript_lauf(tmp_path / "b", "rollout.sh",
                                            extra_env={"OHNE_AUSSENPROBE": "ja", "FAKE_PROBE_RC": "1"})
    assert rc == 3 and "FERTIG" not in out and "UEBERSPRUNGEN" not in out, out


@pytest.mark.skipif(not _SH, reason="kein sh vorhanden")
def test_rollout_erster_server_prueft_zwischenstand(tmp_path):
    rc, out, marker, aufrufe = _skript_lauf(tmp_path, "rollout.sh", extra_env={"ERSTER_SERVER": "1"})
    assert rc == 0, out
    probe = [a for a in aufrufe if "betriebsprobe" in a]
    assert len(probe) == 1 and probe[0].endswith("app.example.test --zwischenstand"), probe
    assert "FERTIG auf" in out and "streng" in out, out


@pytest.mark.skipif(not _SH, reason="kein sh vorhanden")
def test_rollout_marker_nicht_entfernbar_meldet_drain(tmp_path):
    """Scheitert das Entfernen des Drain-Markers in Schritt 5, endete das
    Skript frueher still mit der rohen rm-Meldung (FERTIG=1 war schon gesetzt,
    der Trap schwieg). Jetzt: laut, Server BLEIBT im Drain, keine Probe."""
    rc, out, marker, aufrufe = _skript_lauf(tmp_path, "rollout.sh", rm_scheitert=True)
    assert rc == 1, out
    assert marker, out
    assert "laesst sich nicht entfernen" in out and "BLEIBT im Drain" in out and "freigeben.sh" in out, out
    assert "FERTIG" not in out
    assert not any("betriebsprobe" in a for a in aufrufe)
    assert not any("rm -f /tmp/drain" in a for a in aufrufe), "Container-Marker bleibt ebenfalls"


@pytest.mark.skipif(not _SH, reason="kein sh vorhanden")
@pytest.mark.parametrize("sig,code", [("TERM", 143), ("INT", 130)])
def test_rollout_kill_im_drain_meldet_einmal_und_nicht_null(tmp_path, sig, code):
    """kill -TERM waehrend des Builds: frueher lief abbruch() zweimal (das exit
    im TERM-Trap loeste den EXIT-Trap erneut aus) und der Code war 0.
    INT = Strg+C (Gegenpruefung: der realistische Fall am Terminal)."""
    rc, out, marker, aufrufe = _skript_lauf(tmp_path, "rollout.sh",
                                            extra_env={"FAKE_TERM_BEI": "up -d --build", "FAKE_SIG": sig})
    assert rc == code, out
    assert marker and out.count("BLEIBT im Drain") == 1, out
    assert not any("rm -f /tmp/drain" in a for a in aufrufe)


@pytest.mark.skipif(not _SH, reason="kein sh vorhanden")
@pytest.mark.parametrize("sig,code", [("TERM", 143), ("INT", 130)])
def test_rollout_kill_nach_freigabe_meldet_rotation_ohne_fertig(tmp_path, sig, code):
    """Unterbrechung waehrend der Abschlusspruefung: der Server ist schon in der
    Rotation — Meldung sagt das, verlangt die Probe von Hand, kein FERTIG."""
    rc, out, marker, aufrufe = _skript_lauf(tmp_path, "rollout.sh",
                                            extra_env={"FAKE_TERM_BEI": "betriebsprobe", "FAKE_SIG": sig})
    assert rc == code, out
    assert not marker and "FERTIG" not in out, out
    assert out.count("UNTERBROCHEN nach der Freigabe") == 1 and "NICHT auf dem anderen Server" in out, out
    assert "BLEIBT im Drain" not in out


@pytest.mark.skipif(not _SH, reason="kein sh vorhanden")
def test_freigeben_meldet_nicht_entfernbaren_marker_und_fehlenden_drain(tmp_path):
    a = tmp_path / "a"
    (a / "checkout" / "deploy" / "drain").mkdir(parents=True)
    (a / "checkout" / "deploy" / "drain" / "aktiv").touch()
    rc, out, marker, aufrufe = _skript_lauf(a, "freigeben.sh", rm_scheitert=True)
    assert rc != 0 and marker, out
    assert "laesst sich nicht entfernen" in out and "BLEIBT im Drain" in out, out
    assert "Drain aufgehoben" not in out
    assert not any("rm -f /tmp/drain" in x for x in aufrufe)
    # Nach Code 3 des Rollouts (Server in Rotation) ist freigeben.sh harmlos und sagt das
    rc, out, marker, aufrufe = _skript_lauf(tmp_path / "b", "freigeben.sh")
    assert rc == 0 and not marker, out
    assert "nicht im Drain" in out and "Drain aufgehoben" not in out, out
    assert "NICHT im Drain" not in out, "Warnung nur, wenn eine Pruefung scheitert"
    # Gegenpruefung: ohne Marker UND kaputter Pruefung darf "Freigabe verweigert"
    # nicht nach "geschuetzt im Drain" klingen — der Server bekommt Besucher.
    for fehler in ("FAKE_READY_FAIL", "FAKE_WEB_FAIL"):
        rc, out, marker, aufrufe = _skript_lauf(tmp_path / ("c_" + fehler), "freigeben.sh", fehler)
        assert rc == 1 and not marker, (fehler, out)
        assert "Freigabe verweigert" in out and "NICHT im Drain" in out and "in der Rotation" in out, out
        assert "touch deploy/drain/aktiv" in out, out


@pytest.mark.skipif(not _SH, reason="kein sh vorhanden")
def test_rollout_marker_nicht_setzbar_startet_nicht_und_meldet_rotation(tmp_path):
    """Runde 21 (Gegenpruefung D): laesst sich der Drain-Marker gar nicht
    setzen, meldete abbruch() frueher "BLEIBT im Drain" — falsch, der Server
    war nie im Drain. Jetzt: nichts starten, Code 2, ehrliche Meldung."""
    rc, out, marker, aufrufe = _skript_lauf(tmp_path, "rollout.sh", touch_scheitert=True)
    assert rc == 2, out
    assert not marker
    assert "laesst sich nicht setzen" in out and "NICHT gestartet" in out and "in der Rotation" in out, out
    assert "BLEIBT im Drain" not in out and "FERTIG" not in out and "ABBRUCH" not in out, out
    assert not any("pull" in a or "up -d" in a or "/tmp/drain" in a for a in aufrufe), aufrufe


@pytest.mark.skipif(not _SH, reason="kein sh vorhanden")
def test_rollout_vorhandener_marker_ohne_schreibrecht_laeuft_im_drain(tmp_path):
    """Gegenstueck: existiert der Marker schon (z.B. root-eigen nach einem
    frueheren Abbruch), IST der Server im Drain — das Rollout darf laufen und
    scheitert erst beim Entfernen laut (undrain, "BLEIBT im Drain")."""
    (tmp_path / "checkout" / "deploy" / "drain").mkdir(parents=True)
    (tmp_path / "checkout" / "deploy" / "drain" / "aktiv").touch()
    rc, out, marker, aufrufe = _skript_lauf(tmp_path, "rollout.sh", touch_scheitert=True, rm_scheitert=True)
    assert rc == 1 and marker, out
    assert any("up -d --build" in a for a in aufrufe), aufrufe
    assert "laesst sich nicht setzen" not in out
    assert "laesst sich nicht entfernen" in out and out.count("BLEIBT im Drain") == 1, out


def _probe_modul():
    import importlib.util
    pfad = WURZEL / "backend" / "scripts" / "betriebsprobe.py"
    spec = importlib.util.spec_from_file_location("betriebsprobe_r21", pfad)
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


class _Antwort:
    def __init__(self, code, text=""):
        self.status_code, self.text, self.headers = code, text, {}


@pytest.mark.parametrize("skript_code,frisch_code,zwischenstand,erwartet", [
    (404, 200, True, "warn"),     # Bundle-Wechsel: Startseite neu, Skript vom alten Server
    (404, 404, True, "warn"),
    (404, 200, False, "fehler"),  # ohne Schalter streng (Standard)
    (404, 404, False, "fehler"),
    (502, 200, True, "fehler"),   # gecachter 5xx bleibt Fehler, auch im Zwischenstand
    (502, 502, True, "fehler"),
    (404, 502, True, "fehler"),
    (200, None, True, "ok"),
])
def test_betriebsprobe_zwischenstand_nur_404_als_warnung(monkeypatch, skript_code, frisch_code, zwischenstand, erwartet):
    """Runde 21 (Zusatzbefund zu D): zwischen den beiden Servern ist ein 404
    fuer das Oberflaechen-Skript erwartet (DEPLOYMENT.md). Mit --zwischenstand
    darf GENAU das nur warnen; sonst brach jeder erste Server mit neuer
    Oberflaeche mit "NICHT auf dem anderen Server fortfahren" ab."""
    import types
    modul = _probe_modul()
    startseite = '<script defer="defer" src="/static/js/main.abc123.js"></script>'

    def get(url, **_kw):
        if url.endswith("/"):
            return _Antwort(200, startseite)
        return _Antwort(frisch_code if "?probe=" in url else skript_code)
    monkeypatch.setattr(modul, "requests", types.SimpleNamespace(get=get))
    modul.oberflaeche_pruefen("app.example.test", zwischenstand=zwischenstand)
    if erwartet == "warn":
        assert not modul.FEHLER and len(modul.WARNUNGEN) == 1, (modul.FEHLER, modul.WARNUNGEN)
        assert "Bundle-Wechsel" in modul.WARNUNGEN[0]
    elif erwartet == "fehler":
        assert len(modul.FEHLER) == 1 and not modul.WARNUNGEN, (modul.FEHLER, modul.WARNUNGEN)
    else:
        assert not modul.FEHLER and not modul.WARNUNGEN and modul.OK


def test_betriebsprobe_kennt_option_zwischenstand(monkeypatch):
    modul = _probe_modul()
    gesehen = {}
    for name in ("dns_pruefen", "tls_pruefen", "http_pruefen", "header_pruefen",
                 "api_pruefen", "mail_pruefen", "ports_pruefen"):
        monkeypatch.setattr(modul, name, lambda *a, **k: [])
    monkeypatch.setattr(modul, "oberflaeche_pruefen",
                        lambda host, zwischenstand=False: gesehen.update(host=host, z=zwischenstand))
    monkeypatch.setattr(modul.sys, "argv", ["betriebsprobe.py", "app.example.test", "--zwischenstand"])
    assert modul.main() == 0 and gesehen == {"host": "app.example.test", "z": True}
    monkeypatch.setattr(modul.sys, "argv", ["betriebsprobe.py", "app.example.test"])
    assert modul.main() == 0 and gesehen["z"] is False
