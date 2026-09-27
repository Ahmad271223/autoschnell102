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

from konfig import kommazahl_env, schalter_env, zahl_env

log = logging.getLogger("autohandel.ki")

KI_MODELL_STANDARD = "claude-sonnet-5"


def ki_cache_tage() -> int:
    """Review 26.09.2026 (Nr. 4/5/34/35): Ein abgelegtes Ergebnis gilt nur
    KI_CACHE_TAGE (Standard 7) — der Eingabe-Hash laesst Markt, Referenzen
    und Historie bewusst weg, deshalb braucht der Zwischenspeicher ein
    Verfallsdatum. Danach wird derselbe Stand neu gerechnet."""
    return zahl_env("KI_CACHE_TAGE", 7, unten=1, oben=365)


def ergebnis_gueltig(doc, prompt_version: str) -> bool:
    """Passt ein abgelegtes Ergebnis noch zu Prompt-Fassung, Modell und
    Verfallsdatum? Ein anderes Modell oder eine neue Prompt-Fassung
    liefert andere Zahlen — dann rechnen wir neu statt Altes zu zeigen."""
    if not doc:
        return False
    if doc.get("prompt_version") != prompt_version or doc.get("modell") != ki_modell():
        return False
    stand = str(doc.get("created_at") or "")
    if not stand:
        return False
    from datetime import datetime, timedelta, timezone
    try:
        t = datetime.fromisoformat(stand.replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    return datetime.now(timezone.utc) - t <= timedelta(days=ki_cache_tage())
# Gemessen 25.09.2026 (BMW-Beispiel, 6 Positionen): 19-35 s je Bewertung —
# die Bewertung laeuft im Hintergrund, der Chef sieht sie beim Oeffnen der
# Liste; 30 s Zeitlimit statt der urspruenglich geplanten 12 s.
KI_ZEITLIMIT_SEKUNDEN = kommazahl_env("KI_ZEITLIMIT_SEKUNDEN", 30.0, unten=3.0, oben=90.0)
KI_MAX_TOKENS = 2500      # Probelauf 26.09.2026: 1600 schnitt die JSON-Antwort ab; Denken ist aus


def ki_modell() -> str:
    return (os.environ.get("KI_MODELL") or "").strip() or KI_MODELL_STANDARD


def ki_effort() -> str:
    wert = (os.environ.get("KI_EFFORT") or "").strip().lower()
    # Gemessen 25.09.2026: "low" liefert dieselben Werte wie "medium" in
    # 16-20 s statt 24-35 s.
    return wert if wert in ("low", "medium", "high", "xhigh", "max") else "low"


def ki_denken_aus() -> bool:
    """KI_DENKEN_AUS (Standard true seit 26.09.2026): ohne Vorab-Nachdenken
    des Modells — Denk-Tokens zaehlen als Ausgabe und kosteten mehr, als sie
    an Genauigkeit brachten (der Fall ist vorher vollstaendig aufbereitet)."""
    return schalter_env("KI_DENKEN_AUS", True)


def ki_recherche_modell() -> str:
    """Modell fuer die Websuche (KI_RECHERCHE_MODELL, Standard Haiku 4.5):
    Suchergebnisse sind viele Eingabe-Tokens — das guenstige Modell liest sie
    fuer ein Drittel des Preises; bewertet wird weiter mit KI_MODELL."""
    return (os.environ.get("KI_RECHERCHE_MODELL") or "").strip() or "claude-haiku-4-5-20251001"


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


def _bewerten_parameter(system: str, zusatz: Optional[str], nutzer: Dict[str, Any],
                        schema: Dict[str, Any]) -> Dict[str, Any]:
    """Alle Parameter der Bewertung ausser max_tokens — EINE Quelle fuer den
    echten Aufruf und fuer count_tokens (Kostendeckel 27.09.2026: gezaehlt
    wird exakt, was gesendet wird)."""
    p: Dict[str, Any] = {
        "model": ki_modell(),
        "system": _system_bloecke(system, zusatz),
        "output_config": {"effort": ki_effort(), "format": {"type": "json_schema", "schema": schema}},
        "messages": [{"role": "user", "content": json.dumps(nutzer, ensure_ascii=False, sort_keys=True)}],
    }
    if ki_denken_aus() and ki_effort() in ("low", "medium", "high"):
        p["thinking"] = {"type": "disabled"}
    return p


KI_ZAEHL_ZEITLIMIT = 10.0


async def eingabe_tokens_zaehlen(*, system: str, zusatz: Optional[str], nutzer: Dict[str, Any],
                                 schema: Dict[str, Any]) -> int:
    """Eingabe-Tokens der Bewertung per messages.count_tokens (kostenlos,
    eigenes Anfragelimit) mit denselben Parametern wie json_bewerten.
    Wirft bei Fehlern — der Kostendeckel schaetzt dann sicher nach oben."""
    if not ki_aktiv():
        raise RuntimeError("KI nicht aktiv")
    client = _klient().with_options(timeout=KI_ZAEHL_ZEITLIMIT, max_retries=0)
    r = await client.messages.count_tokens(**_bewerten_parameter(system, zusatz, nutzer, schema))
    return int(getattr(r, "input_tokens", 0) or 0)


async def json_bewerten(*, system: str, nutzer: Dict[str, Any], schema: Dict[str, Any],
                        zeitlimit: Optional[float] = None, zusatz: Optional[str] = None,
                        max_tokens: Optional[int] = None, max_retries: Optional[int] = None) -> KiAntwort:
    """Ein Aufruf, eine JSON-Antwort. Wirft NIE — jeder Fehler wird zum
    status im Ergebnis (der Vertragsprozess laeuft weiter). `zusatz` ist
    der wechselnde Teil des System-Prompts (26.09.2026: Marktdaten,
    Erfahrungswerte, Recherche je Fall) — getrennt vom gecachten Teil.
    max_retries=0 (Kostendeckel 27.09.2026): kein zweiter, womoeglich
    bezahlter Versuch des SDK."""
    t0 = time.perf_counter()
    antwort = KiAntwort(status="fehler", grund="", daten=None, dauer_ms=0,
                        modell=ki_modell(), usage={})
    if not ki_aktiv():
        antwort.update(status="aus", grund="KI-Bewertung nicht aktiv")
        return antwort
    try:
        import anthropic
        client = _klient()
        optionen: Dict[str, Any] = {}
        if zeitlimit:
            optionen["timeout"] = float(zeitlimit)
        if max_retries is not None:
            optionen["max_retries"] = int(max_retries)
        if optionen:
            client = client.with_options(**optionen)
        r = await client.messages.create(
            max_tokens=int(max_tokens or KI_MAX_TOKENS),
            **_bewerten_parameter(system, zusatz, nutzer, schema),
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


# Der Kostendeckel (ai.kostenkasse) zaehlt die Eingabe ueber die Funktion, die
# er aufruft: nur die ECHTE Bewertung traegt einen Zaehler. Eine Attrappe im
# Test hat keinen und wird nie gegen die echte API gezaehlt.
json_bewerten.zaehlen = eingabe_tokens_zaehlen  # type: ignore[attr-defined]


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
RECHERCHE_MAX_TOKENS = 3000
HINWEIS_FORTSETZUNG_ENTFALLEN = "Fortsetzung der Websuche entfallen — Kostendeckel je Lauf"


async def recherche(*, system: str, frage: str, max_suchen: int = 6,
                    zeitlimit: Optional[float] = None, max_tokens: int = RECHERCHE_MAX_TOKENS,
                    kasse=None, basis_tokens: Optional[int] = None,
                    plan: Optional[Dict[str, Any]] = None) -> KiAntwort:
    """Wunsch Ahmad 26.09.2026 (Marktanalyse): ein Aufruf MIT Websuche,
    Antwort als Text plus Quellen (Zitate). Kein JSON-Schema — Zitate und
    strukturierte Ausgabe schliessen sich aus; die Umwandlung macht danach
    json_bewerten. Wirft nie; status ok | fehler | zeitlimit | ... .

    Kostendeckel (27.09.2026): mit `kasse` (ai.kostenkasse) wird jede
    Anfrage einzeln abgerechnet; die erste laeuft mit dem Plan des Aufrufers
    (`plan`: max_uses, max_tokens, Obergrenze), VOR jeder Fortsetzung nach
    pause_turn plant die Kasse neu — passt sie nicht mehr, endet die
    Recherche mit dem bis dahin gefundenen Text. Kein SDK-Wiederholversuch
    (max_retries=0). Auch bei einem Fehler mitten in der Schleife bleibt die
    bis dahin summierte usage im Ergebnis (vorher ging sie verloren)."""
    t0 = time.perf_counter()
    antwort = KiAntwort(status="fehler", grund="", text="", quellen=[], suchen=0, dauer_ms=0,
                        modell=ki_recherche_modell(), usage={})
    if not ki_aktiv():
        antwort.update(status="aus", grund="KI-Bewertung nicht aktiv")
        return antwort
    usage: Dict[str, int] = {}
    aktuell: Dict[str, Any] = dict(plan or {"max_uses": int(max_suchen), "max_tokens": int(max_tokens)})
    offen_nr = 0                       # Anfrage gesendet, aber noch nicht abgerechnet
    try:
        optionen: Dict[str, Any] = {"timeout": float(zeitlimit or KI_RECHERCHE_ZEITLIMIT)}
        if kasse is not None:
            optionen["max_retries"] = 0
            antwort["gebucht"] = True      # die Kasse bucht je Anfrage selbst
        client = _klient().with_options(**optionen)
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
        for i in range(_PAUSEN_MAX):
            if kasse is not None and i > 0:
                # Fortsetzung: der ganze Verlauf geht erneut mit — nur wenn sie
                # noch in den Rest bis zum Ziel passt
                neu = kasse.recherche_plan(kasse.fortsetzung_basis(int(basis_tokens or 0), usage),
                                           int(aktuell.get("max_uses") or max_suchen), ki_recherche_modell())
                if neu is None:
                    kasse.hinweis(HINWEIS_FORTSETZUNG_ENTFALLEN)
                    break
                aktuell = neu
            tools[0]["max_uses"] = int(aktuell.get("max_uses") or max_suchen)
            offen_nr = i + 1
            r = await client.messages.create(
                model=ki_recherche_modell(), max_tokens=int(aktuell.get("max_tokens") or max_tokens),
                system=_system_bloecke(system, None),
                tools=tools, messages=messages,
            )
            u_r = _usage(r)
            _usage_addieren(usage, u_r)
            if kasse is not None:
                kasse.recherche_buchen(ki_recherche_modell(), u_r, aktuell, i + 1)
            offen_nr = 0
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
        antwort.update(status=_status_aus_ausnahme(exc), grund=f"{type(exc).__name__}: {str(exc)[:200]}",
                       usage=usage, suchen=int(usage.get("web_search_requests") or 0))
        if kasse is not None and offen_nr:
            # die gescheiterte Anfrage: ob berechnet, ist offen -> zur Obergrenze gebunden
            kasse.unsicher_buchen(f"recherche#{offen_nr}", float(aktuell.get("obergrenze_ct") or 0),
                                  antwort["grund"])
        log.warning("KI-Recherche gescheitert: %s", antwort["grund"])
    finally:
        antwort["dauer_ms"] = int((time.perf_counter() - t0) * 1000)
    return antwort
