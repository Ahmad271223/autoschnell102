# -*- coding: utf-8 -*-
"""Klare Fehlermeldungen fuer Anbieter-Ausfaelle (Go-Live-Audit 09/2026, Punkt 48).

Vorher verschluckten die Apify-Abrufe (mobile.de / AutoScout24) JEDEN
Fehler und lieferten None -> der Nutzer sah immer nur "Fahrzeug konnte
nicht geladen werden", egal ob der Token abgelaufen, das Apify-Guthaben
leer, der Anbieter down oder nur das Inserat weg war. Und der Betreiber
erfuhr davon nichts.

Jetzt:
  - `AnbieterFehler` (eine RuntimeError-Unterklasse, damit die bestehenden
    502-Zuordnungen in den Routen weiter greifen) traegt eine `art` und
    einen verstaendlichen Text fuer den Nutzer;
  - `aus_http_antwort()` / `aus_ausnahme()` ordnen Statuscodes und
    httpx-Ausnahmen den Faellen zu;
  - Token-/Guthabenprobleme loesen einen Betriebsalarm aus (einmal je
    Stunde), damit sie im Super-Admin-Bereich "Betrieb" sichtbar sind.
"""
import logging
import time
from typing import Optional

log = logging.getLogger("anbieter")

ART_TOKEN = "anbieter_zugang"
ART_GUTHABEN = "anbieter_guthaben"
ART_LIMIT = "anbieter_limit"
ART_ZEIT = "anbieter_zeitueberschreitung"
ART_AUSFALL = "anbieter_ausfall"

_TEXTE = {
    ART_TOKEN: ("{quelle}-Abruf nicht möglich: der Zugang zum Abruf-Dienst (Apify-Token) "
                "ist ungültig oder abgelaufen. Der Betreiber wurde benachrichtigt — "
                "bekannte Links kommen weiter aus dem Speicher."),
    ART_GUTHABEN: ("{quelle}-Abruf nicht möglich: das Guthaben beim Abruf-Dienst ist "
                   "aufgebraucht. Der Betreiber wurde benachrichtigt — bekannte Links "
                   "kommen weiter aus dem Speicher."),
    ART_LIMIT: ("{quelle}-Abruf gerade nicht möglich: der Abruf-Dienst hat sein Limit "
                "erreicht (zu viele Anfragen). Bitte in einigen Minuten erneut versuchen."),
    ART_ZEIT: ("{quelle} antwortet nicht (Zeitüberschreitung). Der Anbieter ist "
               "vermutlich langsam oder gestört — bitte in ein paar Minuten erneut versuchen."),
    ART_AUSFALL: ("{quelle}-Abruf vorübergehend gestört (Anbieter-Dienst antwortet mit "
                  "einem Fehler). Bitte später erneut versuchen; bekannte Links kommen "
                  "weiter aus dem Speicher."),
}

# Betriebsalarm hoechstens einmal je Stunde je (art, quelle) — sonst wuerde
# ein Ausfall bei jedem Klick einen neuen Alarm erzeugen.
_ALARM_ZULETZT: dict = {}
_ALARM_ABSTAND_S = 3600


class AnbieterFehler(RuntimeError):
    def __init__(self, art: str, quelle: str, detail: str = ""):
        self.art = art
        self.quelle = quelle
        self.detail = (detail or "")[:300]
        super().__init__(_TEXTE.get(art, _TEXTE[ART_AUSFALL]).format(quelle=quelle))

    @property
    def betreiber_relevant(self) -> bool:
        return self.art in (ART_TOKEN, ART_GUTHABEN)


def aus_http_antwort(status: int, text: str, quelle: str) -> Optional[AnbieterFehler]:
    """Apify-/Anbieter-HTTP-Antwort -> Fehler oder None (2xx)."""
    if status in (200, 201):
        return None
    t = (text or "").lower()
    if status in (401, 403):
        return AnbieterFehler(ART_TOKEN, quelle, f"HTTP {status}: {text[:120]}")
    if status == 402 or "insufficient" in t or "usage limit" in t or "credit" in t and "exceed" in t:
        return AnbieterFehler(ART_GUTHABEN, quelle, f"HTTP {status}: {text[:120]}")
    if status == 429 or "rate limit" in t or "too many" in t:
        return AnbieterFehler(ART_LIMIT, quelle, f"HTTP {status}: {text[:120]}")
    if status in (408, 504):
        return AnbieterFehler(ART_ZEIT, quelle, f"HTTP {status}")
    return AnbieterFehler(ART_AUSFALL, quelle, f"HTTP {status}: {text[:120]}")


def aus_ausnahme(exc: BaseException, quelle: str) -> AnbieterFehler:
    """httpx-/Netz-Ausnahme -> Fehler."""
    try:
        import httpx
        if isinstance(exc, httpx.TimeoutException):
            return AnbieterFehler(ART_ZEIT, quelle, type(exc).__name__)
        if isinstance(exc, (httpx.ConnectError, httpx.NetworkError, httpx.RemoteProtocolError)):
            return AnbieterFehler(ART_AUSFALL, quelle, type(exc).__name__)
    except ImportError:
        pass
    if isinstance(exc, TimeoutError):
        return AnbieterFehler(ART_ZEIT, quelle, type(exc).__name__)
    return AnbieterFehler(ART_AUSFALL, quelle, f"{type(exc).__name__}: {exc}"[:200])


async def melden(db, fehler: AnbieterFehler) -> None:
    """Loggt jeden Anbieter-Fehler; Token-/Guthabenprobleme zusaetzlich als
    Betriebsalarm (gedrosselt). Wirft nie."""
    log.warning("Anbieter %s: %s (%s)", fehler.quelle, fehler.art, fehler.detail)
    if not fehler.betreiber_relevant or db is None:
        return
    schluessel = (fehler.art, fehler.quelle)
    jetzt = time.monotonic()
    if jetzt - _ALARM_ZULETZT.get(schluessel, -_ALARM_ABSTAND_S) < _ALARM_ABSTAND_S:
        return
    _ALARM_ZULETZT[schluessel] = jetzt
    try:
        from betrieb import alarm
        await alarm(db, fehler.art, ref=fehler.quelle,
                    anbieter=fehler.quelle, detail=fehler.detail,
                    hinweis="Apify-Token bzw. Guthaben pruefen (apify.com -> Settings/Billing), "
                            "danach APIFY_TOKEN in der .env erneuern und Backend neu starten")
    except Exception:  # noqa: BLE001 — Alarm darf den Abruf nie zusaetzlich brechen
        log.exception("Betriebsalarm fuer Anbieter-Fehler konnte nicht geschrieben werden")
