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
    assert "trap abbruch EXIT INT TERM" in s and "trap undrain" not in s
    assert s.index("FERTIG=1") < s.index("undrain\ntrap - EXIT INT TERM"), \
        "Drain wird nur auf dem Erfolgspfad aufgehoben"
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
    assert "oberflaeche_pruefen(host)" in quelle[quelle.index("def main()"):]
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
echo "docker $*" >> "$FAKE_LOG"
case "$*" in
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


def _skript_lauf(tmp_path, skript, fehler=None, args=()):
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
    for name, inhalt in (("docker", _FAKE_DOCKER), ("git", _FAKE_GIT)):
        f = fake / name
        f.write_text(inhalt, encoding="utf-8", newline="\n")
        f.chmod(f.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    log = tmp_path / "aufrufe.log"
    env = dict(os.environ)
    env.update({"PATH": str(fake) + os.pathsep + env.get("PATH", ""),
                "VERZ": str(verz).replace("\\", "/"), "WARTE_LB": "0", "SCHLAF": "0",
                "FAKE_LOG": str(log).replace("\\", "/"),
                "FAKE_BUILD_FAIL": "0", "FAKE_READY_FAIL": "0", "FAKE_WEB_FAIL": "0"})
    if fehler:
        env[fehler] = "1"
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
