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
    assert "trap undrain EXIT" in s, "Drain muss bei Abbruch aufgehoben werden"
    assert "COMPOSE_FILE=docker-compose.yml:deploy/docker-compose.replica.yml" in s
    assert "PUBLIC_HOST" in s


def test_doku_und_ci_kennen_den_rollout():
    d = (WURZEL / "DEPLOYMENT.md").read_text(encoding="utf-8")
    assert "sh deploy/rollout.sh" in d and "Drain" in d
    ci = (WURZEL / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "touch /tmp/drain" in ci and '[ "$CODE" = 503 ]' in ci
