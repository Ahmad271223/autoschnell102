# -*- coding: utf-8 -*-
"""Anbindung an Claude (Anthropic) — die einzige Stelle, die die KI ruft.

Regeln (Wunsch Ahmad 25.09.2026):
  * nur vom Server, Schluessel nur aus der Umgebung (ANTHROPIC_API_KEY)
  * Antwort ausschliesslich als JSON nach vorgegebenem Schema
    (output_config.format json_schema) — kein Fliesstext
  * hartes Zeitlimit (KI_ZEITLIMIT_SEKUNDEN, Standard 30 s); ein Ausfall
    blockiert nie Vertrag, Freigabe oder Unterschrift — der Aufrufer bekommt
    ein Ergebnis mit status "fehler" statt einer Ausnahme
  * Modell einstellbar (KI_MODELL, Standard claude-sonnet-5 — Entscheidung
    Ahmad 26.09.2026; claude-opus-5 genauer, aber dreimal so teuer),
    Denktiefe KI_EFFORT (Standard low)
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, Optional

from konfig import kommazahl_env, schalter_env

log = logging.getLogger("autohandel.ki")

KI_MODELL_STANDARD = "claude-sonnet-5"
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


def _system_bloecke(system: str, zusatz: Optional[str]) -> list:
    """Fester Teil (im Prompt-Cache) + wechselnder Zusatz (Marktdaten,
    Erfahrungswerte, Fall-Recherche) als eigener, ungecachter Block."""
    bloecke = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
    if zusatz and zusatz.strip():
        bloecke.append({"type": "text", "text": zusatz.strip()})
    return bloecke


async def json_bewerten(*, system: str, nutzer: Dict[str, Any], schema: Dict[str, Any],
                        zeitlimit: Optional[float] = None, zusatz: Optional[str] = None) -> KiAntwort:
    """Ein Aufruf, eine JSON-Antwort. Wirft NIE — jeder Fehler wird zum
    status im Ergebnis (der Vertragsprozess laeuft weiter). `zusatz` ist
    der wechselnde Teil des System-Prompts (26.09.2026: Marktdaten,
    Erfahrungswerte, Recherche je Fall) — getrennt vom gecachten Teil."""
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
            system=_system_bloecke(system, zusatz),
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
    # Websuche (26.09.2026): Anzahl Suchen fuer die Kostenschaetzung
    stu = getattr(u, "server_tool_use", None)
    n = getattr(stu, "web_search_requests", None) if stu is not None else None
    if isinstance(n, int):
        out["web_search_requests"] = n
    return out


def _usage_addieren(summe: Dict[str, int], neu: Dict[str, int]) -> None:
    for k, v in (neu or {}).items():
        summe[k] = int(summe.get(k) or 0) + int(v or 0)


# ------------------------------------------------ Websuche (Marktanalyse)
WEBSUCHE_TOOL = "web_search_20260209"
GESPERRTE_DOMAINS = ("ebay.de", "ebay.com", "ebay-kleinanzeigen.de", "kleinanzeigen.de", "amazon.de", "amazon.com",
                     "aliexpress.com", "temu.com", "fahrzeugpflegeforum.de", "motor-talk.de", "gutefrage.net",
                     "reddit.com", "facebook.com", "youtube.com", "pinterest.com", "myhammer.de", "kamux.de")
KI_RECHERCHE_ZEITLIMIT = kommazahl_env("KI_RECHERCHE_ZEITLIMIT_SEKUNDEN", 120.0, unten=10.0, oben=300.0)
_PAUSEN_MAX = 4


async def recherche(*, system: str, frage: str, max_suchen: int = 6,
                    zeitlimit: Optional[float] = None) -> KiAntwort:
    """Wunsch Ahmad 26.09.2026 (Marktanalyse): ein Aufruf MIT Websuche,
    Antwort als Text plus Quellen (Zitate). Kein JSON-Schema — Zitate und
    strukturierte Ausgabe schliessen sich aus; die Umwandlung macht danach
    json_bewerten. Wirft nie; status ok | fehler | zeitlimit | ... ."""
    t0 = time.perf_counter()
    antwort = KiAntwort(status="fehler", grund="", text="", quellen=[], suchen=0, dauer_ms=0,
                        modell=ki_modell(), usage={})
    if not ki_aktiv():
        antwort.update(status="aus", grund="KI-Bewertung nicht aktiv")
        return antwort
    try:
        client = _klient().with_options(timeout=float(zeitlimit or KI_RECHERCHE_ZEITLIMIT))
        # Probelauf 26.09.2026: mit der Vorfilterung (Code-Ausfuehrung) meldete
        # das Modell "Suchergebnisse nicht verwertbar" und verbrauchte alle
        # Suchen — deshalb direkte Suche, Ergebnisse als Text im Kontext.
        # Handels-, Kleinanzeigen- und Forenseiten liefern keine Preise, die
        # wir wollen (Wunsch Ahmad: ADAC, Smart-Repair-Anbieter).
        tools = [{"type": WEBSUCHE_TOOL, "name": "web_search", "max_uses": int(max_suchen),
                  "allowed_callers": ["direct"],
                  "blocked_domains": list(GESPERRTE_DOMAINS),
                  "user_location": {"type": "approximate", "country": "DE", "timezone": "Europe/Berlin"}}]
        messages: list = [{"role": "user", "content": frage}]
        texte: list = []
        zitiert: Dict[str, str] = {}
        gefunden: Dict[str, str] = {}
        usage: Dict[str, int] = {}
        for _ in range(_PAUSEN_MAX):
            r = await client.messages.create(
                model=ki_modell(), max_tokens=4000,
                system=_system_bloecke(system, None),
                tools=tools, messages=messages,
                output_config={"effort": ki_effort()},
            )
            _usage_addieren(usage, _usage(r))
            for b in r.content:
                art = getattr(b, "type", "")
                if art == "text":
                    texte.append(getattr(b, "text", "") or "")
                    for c in (getattr(b, "citations", None) or []):
                        url = getattr(c, "url", None)
                        if url:
                            zitiert.setdefault(url, getattr(c, "title", "") or "")
                elif art == "web_search_tool_result":
                    inhalt = getattr(b, "content", None)
                    if isinstance(inhalt, list):
                        for e in inhalt:
                            url = getattr(e, "url", None)
                            if url:
                                gefunden.setdefault(url, getattr(e, "title", "") or "")
            if r.stop_reason == "pause_turn":
                messages.append({"role": "assistant",
                                 "content": [b.model_dump(exclude_none=True) for b in r.content]})
                continue
            if r.stop_reason == "refusal":
                antwort.update(status="abgelehnt", grund="abgelehnt")
                antwort["usage"] = usage
                return antwort
            break
        quellen = zitiert or gefunden
        antwort.update(status="ok", text="\n".join(t for t in texte if t).strip(),
                       quellen=[{"url": u, "titel": t[:120]} for u, t in list(quellen.items())[:20]],
                       suchen=int(usage.get("web_search_requests") or 0), usage=usage)
    except Exception as exc:  # noqa: BLE001 — Beiwerk, nie ein 500
        antwort.update(status=_status_aus_ausnahme(exc), grund=f"{type(exc).__name__}: {str(exc)[:200]}")
        log.warning("KI-Recherche gescheitert: %s", antwort["grund"])
    finally:
        antwort["dauer_ms"] = int((time.perf_counter() - t0) * 1000)
    return antwort
