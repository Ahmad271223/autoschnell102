"""Zentrale Proxy- & Anti-Blocking-Konfiguration.

Alle ausgehenden Scrape-/Snapshot-Requests laufen ueber denselben
rotierenden Proxy-Endpoint (sofern in der .env gesetzt) und nutzen
eine rotierende User-Agent-Liste. Ohne gesetzte PROXY_URL verhaelt sich
alles wie bisher (direkter Zugriff) — ideal fuer lokale Entwicklung.

.env-Konfiguration:
    PROXY_URL=http://USER:PASS@gate.anbieter.com:7000
    PROXY_ENABLED=true            # optional, default: true wenn PROXY_URL gesetzt
    SCRAPE_MAX_RETRIES=3          # optional, Retries bei 403/429
"""
from __future__ import annotations

import os
import random
from typing import Optional
from urllib.parse import urlparse


# ---------------------------------------------------------------------------
# Proxy
# ---------------------------------------------------------------------------
PROXY_URL: Optional[str] = (os.environ.get("PROXY_URL") or "").strip() or None
_PROXY_ENABLED = os.environ.get("PROXY_ENABLED", "").strip().lower()
# Default: aktiv, sobald eine PROXY_URL gesetzt ist.
PROXY_ENABLED: bool = (
    bool(PROXY_URL) if _PROXY_ENABLED == "" else _PROXY_ENABLED == "true"
)

SCRAPE_MAX_RETRIES: int = int(os.environ.get("SCRAPE_MAX_RETRIES", "3"))


def get_proxy_url() -> Optional[str]:
    """Liefert die Proxy-URL fuer httpx (oder None = direkter Zugriff)."""
    if PROXY_ENABLED and PROXY_URL:
        return PROXY_URL
    return None


def get_playwright_proxy() -> Optional[dict]:
    """Zerlegt die PROXY_URL in das von Playwright erwartete dict-Format:
        {"server": "http://host:port", "username": "...", "password": "..."}
    Gibt None zurueck, wenn kein Proxy konfiguriert ist.
    """
    url = get_proxy_url()
    if not url:
        return None
    parsed = urlparse(url)
    scheme = parsed.scheme or "http"
    host = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    proxy: dict = {"server": f"{scheme}://{host}{port}"}
    if parsed.username:
        proxy["username"] = parsed.username
    if parsed.password:
        proxy["password"] = parsed.password
    return proxy


# ---------------------------------------------------------------------------
# User-Agent-Rotation
# ---------------------------------------------------------------------------
# Aktuelle, plausible Desktop-Browser-UAs. Rotierend, damit nicht alle
# Requests denselben Fingerprint tragen.
USER_AGENTS = [
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
     "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
     "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"),
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
     "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
     "(KHTML, like Gecko) Version/17.4 Safari/605.1.15"),
    ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
     "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) "
     "Gecko/20100101 Firefox/125.0"),
]


def random_user_agent() -> str:
    return random.choice(USER_AGENTS)
