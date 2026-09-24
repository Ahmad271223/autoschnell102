# -*- coding: utf-8 -*-
"""Anbindung an Claude (Anthropic) — die einzige Stelle, die die KI ruft.

Regeln (Wunsch Ahmad 25.09.2026):
  * nur vom Server, Schluessel nur aus der Umgebung (ANTHROPIC_API_KEY)
  * Antwort ausschliesslich als JSON nach vorgegebenem Schema
    (output_config.format json_schema) — kein Fliesstext
  * hartes Zeitlimit (KI_ZEITLIMIT_SEKUNDEN, Standard 30 s); ein Ausfall
    blockiert nie Vertrag, Freigabe oder Unterschrift — der Aufrufer bekommt
    ein Ergebnis mit status "fehler" statt einer Ausnahme
  * Modell einstellbar (KI_MODELL, Standard claude-opus-5; guenstiger:
    claude-sonnet-5), Denktiefe KI_EFFORT (Standard low)
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, Optional

from konfig import kommazahl_env, schalter_env

log = logging.getLogger("autohandel.ki")

KI_MODELL_STANDARD = "claude-opus-5"
# Gemessen 25.09.2026 (BMW-Beispiel, 6 Positionen): 19-35 s je Bewertung —
# die Bewertung laeuft im Hintergrund, der Chef sieht sie beim Oeffnen der
# Liste; 30 s Zeitlimit statt der urspruenglich geplanten 12 s.
KI_ZEITLIMIT_SEKUNDEN = kommazahl_env("KI_ZEITLIMIT_SEKUNDEN", 30.0, unten=3.0, oben=90.0)
KI_MAX_TOKENS = 3000


def ki_modell() -> str:
    return (os.environ.get("KI_MODELL") or "").strip() or KI_MODELL_STANDARD


def ki_effort() -> str:
    wert = (os.environ.get("KI_EFFORT") or "").strip().lower()
    # Gemessen 25.09.2026: "low" liefert dieselben Werte wie "medium" in
    # 16-20 s statt 24-35 s.
    return wert if wert in ("low", "medium", "high", "xhigh", "max") else "low"


def ki_denken_aus() -> bool:
    """KI_DENKEN_AUS=true: ohne Vorab-Nachdenken des Modells (schneller,
    etwas grober). Standard: an (adaptiv)."""
    return schalter_env("KI_DENKEN_AUS", False)


def ki_aktiv() -> bool:
    """Schalter KI_BEWERTUNG_AKTIV (Standard an) UND ein Schluessel vorhanden.
    Ohne Schluessel ist die Funktion still aus — kein Fehler, keine Karte."""
    if not schalter_env("KI_BEWERTUNG_AKTIV", True):
        return False
    return bool((os.environ.get("ANTHROPIC_API_KEY") or "").strip())


_client = None


def _klient():
    """AsyncAnthropic einmal je Prozess (liest ANTHROPIC_API_KEY selbst)."""
    global _client
    if _client is None:
        import ssl
        import anthropic
        # Eigener SSL-Kontext: httpx2 (Unterbau des SDK) laeuft auf Python 3.14
        # beim Anlegen seines Standardkontexts in eine Endlosrekursion
        # (Windows-Entwicklungsrechner); mit einem fertigen Kontext nicht.
        # Auf dem Server (Python 3.12) ist beides gleichwertig.
        _client = anthropic.AsyncAnthropic(
            timeout=KI_ZEITLIMIT_SEKUNDEN, max_retries=1,
            http_client=anthropic.DefaultAsyncHttpxClient(verify=ssl.create_default_context()))
    return _client


class KiAntwort(dict):
    """Ergebnis eines Aufrufs: status "ok" (daten = geparstes JSON) oder
    "fehler"/"zeitlimit"/"abgelehnt" (grund als Text). dauer_ms und usage
    immer dabei — fuer die Betriebsseite und die Kostenkontrolle."""


async def json_bewerten(*, system: str, nutzer: Dict[str, Any], schema: Dict[str, Any],
                        zeitlimit: Optional[float] = None) -> KiAntwort:
    """Ein Aufruf, eine JSON-Antwort. Wirft NIE — jeder Fehler wird zum
    status im Ergebnis (der Vertragsprozess laeuft weiter)."""
    t0 = time.perf_counter()
    antwort = KiAntwort(status="fehler", grund="", daten=None, dauer_ms=0,
                        modell=ki_modell(), usage={})
    if not ki_aktiv():
        antwort.update(status="aus", grund="KI-Bewertung nicht aktiv")
        return antwort
    try:
        import anthropic
        client = _klient()
        if zeitlimit:
            client = client.with_options(timeout=float(zeitlimit))
        extra: Dict[str, Any] = {}
        if ki_denken_aus() and ki_effort() in ("low", "medium", "high"):
            extra["thinking"] = {"type": "disabled"}
        r = await client.messages.create(
            model=ki_modell(),
            max_tokens=KI_MAX_TOKENS,
            system=[{"type": "text", "text": system,
                     "cache_control": {"type": "ephemeral"}}],
            output_config={"effort": ki_effort(),
                           "format": {"type": "json_schema", "schema": schema}},
            messages=[{"role": "user",
                       "content": json.dumps(nutzer, ensure_ascii=False, sort_keys=True)}],
            **extra,
        )
        antwort["usage"] = _usage(r)
        if r.stop_reason == "refusal":
            antwort.update(status="abgelehnt",
                           grund=str(getattr(getattr(r, "stop_details", None), "explanation", "") or "abgelehnt"))
            return antwort
        if r.stop_reason == "max_tokens":
            antwort.update(status="fehler", grund="Antwort abgeschnitten (max_tokens)")
            return antwort
        text = next((b.text for b in r.content if getattr(b, "type", "") == "text"), "")
        antwort.update(status="ok", daten=json.loads(text))
    except json.JSONDecodeError as exc:
        antwort.update(status="fehler", grund=f"kein gueltiges JSON: {exc}")
    except Exception as exc:  # noqa: BLE001 — bewusst breit: die KI ist Beiwerk
        antwort.update(status=_status_aus_ausnahme(exc), grund=f"{type(exc).__name__}: {str(exc)[:200]}")
        log.warning("KI-Aufruf gescheitert: %s", antwort["grund"])
    finally:
        antwort["dauer_ms"] = int((time.perf_counter() - t0) * 1000)
    return antwort


def _status_aus_ausnahme(exc: Exception) -> str:
    try:
        import anthropic
        if isinstance(exc, anthropic.APITimeoutError):
            return "zeitlimit"
        if isinstance(exc, anthropic.RateLimitError):
            return "ueberlastet"
        if isinstance(exc, anthropic.AuthenticationError):
            return "schluessel"
    except Exception:  # noqa: BLE001
        pass
    return "fehler"


def _usage(r) -> Dict[str, int]:
    u = getattr(r, "usage", None)
    if not u:
        return {}
    out = {}
    for k in ("input_tokens", "output_tokens", "cache_read_input_tokens",
              "cache_creation_input_tokens"):
        w = getattr(u, k, None)
        if isinstance(w, int):
            out[k] = w
    return out
