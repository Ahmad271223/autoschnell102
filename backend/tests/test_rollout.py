# -*- coding: utf-8 -*-
"""Rollout ohne 502 hinter dem Load Balancer (Vorfall 07.09.2026, 15:04 UTC).

Der Load Balancer prueft nur /api/health (Backend). Waehrend "docker compose
up -d --build" den Oberflaechen-Container neu baute, blieb das Backend
gesund, der LB schickte weiter Besucher hin, und alle statischen Dateien
antworteten rund 45 s mit 502. Abhilfe: Drain-Marker in der LB-Vorlage und
deploy/rollout.sh, das einen Server je Aufruf aus der Rotation nimmt.
Der funktionale Teil (503 mit Marker, 200 ohne) laeuft in der CI-Stack-Probe.
"""
import re
from pathlib import Path

WURZEL = Path(__file__).resolve().parents[2]


def test_lb_vorlage_hat_drain_marker_in_beiden_health_locations():
    t = (WURZEL / "deploy" / "hinter-loadbalancer.conf.template").read_text(encoding="utf-8")
    bloecke = re.findall(r"location = /api/health \{(.*?)\n  \}", t, re.S)
    assert len(bloecke) == 2, "default_server- und Domain-Block brauchen je einen Health-Block"
    for b in bloecke:
        assert "if (-f /tmp/drain) { return 503; }" in b, b
        assert "proxy_pass http://backend:8001;" in b


def test_rollout_skript_setzt_drain_baut_und_hebt_auf():
    roh = (WURZEL / "deploy" / "rollout.sh").read_text(encoding="utf-8")
    assert roh.startswith("#!/bin/sh")
    # nur ausfuehrbare Zeilen (der Kopfkommentar nennt die Befehle ebenfalls)
    s = "\n".join(z for z in roh.splitlines() if not z.lstrip().startswith("#"))
    assert "touch /tmp/drain" in s and "rm -f /tmp/drain" in s
    assert s.index("touch /tmp/drain") < s.index("git pull --ff-only") < s.index("up -d --build")
    assert "/api/ready" in s, "Backend-Bereitschaft wird abgewartet"
    assert "http://127.0.0.1/" in s, "Oberflaeche wird ueber den Proxy geprueft"
    assert "trap undrain EXIT" in s, "Drain muss bei Abbruch aufgehoben werden"
    assert "COMPOSE_FILE=docker-compose.yml:deploy/docker-compose.replica.yml" in s
    assert "PUBLIC_HOST" in s


def test_doku_und_ci_kennen_den_rollout():
    d = (WURZEL / "DEPLOYMENT.md").read_text(encoding="utf-8")
    assert "sh deploy/rollout.sh" in d and "Drain" in d
    ci = (WURZEL / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "touch /tmp/drain" in ci and '[ "$CODE" = 503 ]' in ci
