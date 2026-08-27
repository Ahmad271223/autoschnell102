"""Kleinanzeigen.de URL parser → vehicle dict.

Adapted from the user's `ebaypythonparaser.txt` reference but slimmed down to
reuse the existing `mobile_service` make/model resolution (178 makes, 2721
models from `mobile_makes_models.json`). The output dict matches the schema
that `mobile_service.build_search_url` consumes, so the same compare flow
works whether the source URL is mobile.de or kleinanzeigen.de.
"""
from __future__ import annotations

import asyncio
import html as html_lib
import random
import re
from typing import Any, Dict, List, Optional, Tuple

import os
import httpx
from bs4 import BeautifulSoup


def _make_soup(markup: str) -> "BeautifulSoup":
    """Robustes Parsen: kleinanzeigen.de liefert teils kaputte Entities
    (z.B. `&#8203` ohne Semikolon), an denen Pythons eingebauter
    html.parser ab 3.14 mit ValueError crasht. lxml verkraftet das."""
    try:
        return BeautifulSoup(markup, "lxml")
    except Exception:
        return BeautifulSoup(markup, "html.parser")

from proxy_config import get_proxy_url, random_user_agent, SCRAPE_MAX_RETRIES

# Lokal (Windows) scheitert die SSL-Verifikation an fehlenden Intermediate-CAs.
# SSL_VERIFY=false in .env deaktiviert die Prüfung für lokale Entwicklung.
_SSL_VERIFY = os.environ.get("SSL_VERIFY", "true").lower() != "false"

from mobile_service import (
    FUEL_LABELS,
    GEAR_LABELS,
    _resolve_make,
    _resolve_model,
    kw_to_ps,
)
from owners_extractor import extract_owners_from_text


STOP_MARKERS = (
    "Andere Anzeigen des Anbieters",
    "Alle Anzeigen dieses Anbieters",
    "Das könnte dich auch interessieren",
    "Ähnliche Anzeigen",
    "Aehnliche Anzeigen",
    "Weitere Anzeigen",
    "Empfohlene Anzeigen",
)

MONTHS_DE = {
    "januar": "01", "februar": "02", "maerz": "03", "marz": "03", "märz": "03",
    "april": "04", "mai": "05", "juni": "06", "juli": "07", "august": "08",
    "september": "09", "oktober": "10", "november": "11", "dezember": "12",
}

WANTED_FIELDS = (
    "Marke", "Modell", "Kilometerstand", "Fahrzeugzustand", "Erstzulassung",
    "Kraftstoffart", "Leistung", "Getriebe", "Fahrzeugtyp", "Anzahl Türen",
    "Anzahl der Türen", "HU bis", "HU", "Umweltplakette", "Schadstoffklasse",
    "Außenfarbe", "Farbe", "Material Innenausstattung", "Innenausstattung",
    "Hubraum", "Anzahl Sitzplätze", "Anzahl der Fahrzeughalter",
)


# -------------------- text helpers --------------------
def _clean(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = html_lib.unescape(str(value))
    text = text.replace("\xa0", " ").replace("\u200b", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _to_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    digits = re.sub(r"\D", "", str(value))
    return int(digits) if digits else None


def _cut_at_stop(text: str) -> str:
    if not text:
        return ""
    end = len(text)
    low = text.lower()
    for marker in STOP_MARKERS:
        pos = low.find(marker.lower())
        if pos != -1:
            end = min(end, pos)
    return text[:end].strip()


def _visible_text(soup: BeautifulSoup) -> str:
    copy = _make_soup(str(soup))
    for tag in copy(["script", "style", "noscript", "svg"]):
        tag.decompose()
    return _clean(copy.get_text("\n")) or ""


def _meta(soup: BeautifulSoup, *names: str) -> Optional[str]:
    for name in names:
        tag = soup.find("meta", attrs={"property": name}) or \
              soup.find("meta", attrs={"name": name})
        if tag and tag.get("content"):
            return _clean(tag.get("content"))
    return None


# -------------------- field parsers --------------------
def _parse_title(soup: BeautifulSoup, visible: str) -> Optional[str]:
    h1 = soup.find("h1")
    if h1:
        t = _clean(h1.get_text(" "))
        if t:
            return t
    meta = _meta(soup, "og:title", "twitter:title")
    if meta:
        return _clean(re.sub(r"\s+in\s+.+$", "", meta))
    for line in (visible.splitlines() if visible else [])[:20]:
        line = _clean(line) or ""
        if len(line) > 8 and "kleinanzeigen" not in line.lower():
            return line
    return None


def _parse_price(text: str) -> Tuple[Optional[str], Optional[int]]:
    m = re.search(r"(\d{1,3}(?:[.\s]\d{3})+|\d+)\s*€", text or "")
    if not m:
        return None, None
    amount = _to_int(m.group(1))
    return (f"{amount:,}".replace(",", ".") + " €" if amount else None, amount)


def _parse_location(text: str) -> Optional[str]:
    """Return e.g. '10115 Berlin' or full plz+stadt+land line."""
    states = (
        "Bayern|NRW|Nordrhein|Hessen|Sachsen|Berlin|Hamburg|Bremen|Saarland|"
        "Brandenburg|Thüringen|Niedersachsen|Rheinland|Schleswig|Mecklenburg|"
        "Baden|Württemberg|Sachsen-Anhalt"
    )
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    for i, line in enumerate(lines):
        if re.search(r"\b\d{5}\b", line):
            if " - " in line or re.search(rf"\b({states})\b", line, re.I):
                return line
            if i + 1 < len(lines) and re.search(rf"\b({states})\b", lines[i + 1], re.I):
                return f"{line} {lines[i + 1]}"
            return line
    return None


def _split_location(location: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """Splittet eine location-Zeile wie '10115 Berlin' oder '10115 Berlin - Mitte'
    in (zip, city). Bei '10115 Berlin Berlin (Bundesland)' wird das Bundesland
    entfernt, damit nur der Stadtname in der city-Spalte landet.

    Rueckgabe:
        ('10115', 'Berlin')   fuer '10115 Berlin'
        ('10115', 'Berlin')   fuer '10115 Berlin - Mitte'   (Bezirk verworfen)
        ('80331', 'Muenchen') fuer '80331 Muenchen Bayern'  (Bundesland verworfen)
        (None, None)          wenn keine PLZ gefunden
    """
    if not location:
        return (None, None)
    m = re.search(r"\b(\d{5})\b\s*([^\-\n]*)", location)
    if not m:
        return (None, None)
    plz = m.group(1)
    rest = (m.group(2) or "").strip()
    if not rest:
        return (plz, None)
    # Bundeslaender abschneiden — sie stehen oft hinter dem Stadtnamen.
    state_re = re.compile(
        r"\s+(Bayern|NRW|Nordrhein[-\w]*|Hessen|Sachsen[-\w]*|Berlin|Hamburg|Bremen|"
        r"Saarland|Brandenburg|Th(ue|ü)ringen|Niedersachsen|Rheinland[-\w]*|"
        r"Schleswig[-\w]*|Mecklenburg[-\w]*|Baden[-\w]*|W(ue|ü)rttemberg|"
        r"Sachsen-Anhalt)\b.*$",
        re.IGNORECASE,
    )
    city = state_re.sub("", rest).strip()
    return (plz, city or None)


def _parse_structured(text: str) -> Dict[str, str]:
    """Walk visible text line-by-line, collecting <field>: <next-line> pairs.
    Mirrors the layout of kleinanzeigen.de's vehicle property table."""
    result: Dict[str, str] = {}
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    wanted_lower = {w.lower(): w for w in WANTED_FIELDS}
    skip_values = {"beschreibung", "ausstattung"}

    for i, line in enumerate(lines):
        low = line.lower()
        if low in wanted_lower:
            for j in range(i + 1, min(i + 6, len(lines))):
                val = lines[j]
                vlow = val.lower()
                if vlow not in wanted_lower and vlow not in skip_values:
                    result[wanted_lower[low]] = val
                    break
        for w_lower, original in wanted_lower.items():
            if low.startswith(w_lower + ":"):
                raw = line.split(":", 1)[1].strip()
                if raw and raw.lower() != w_lower:
                    result[original] = raw

    # alias resolution
    if "Anzahl der Türen" in result and "Anzahl Türen" not in result:
        result["Anzahl Türen"] = result["Anzahl der Türen"]
    if "HU" in result and "HU bis" not in result:
        result["HU bis"] = result["HU"]
    if "Farbe" in result and "Außenfarbe" not in result:
        result["Außenfarbe"] = result["Farbe"]
    return result


def _parse_first_registration(value: Optional[str]) -> Optional[str]:
    """Return mobile.de-style 'MM/YYYY' from any KA date format."""
    if not value:
        return None
    v = _clean(value) or ""
    m = re.search(r"(\d{1,2})[./](\d{4})", v)
    if m:
        return f"{m.group(1).zfill(2)}/{m.group(2)}"
    yr_m = re.search(r"\b(19|20)\d{2}\b", v)
    if not yr_m:
        return None
    year = yr_m.group(0)
    low = v.lower()
    for name, num in MONTHS_DE.items():
        if name in low:
            return f"{num}/{year}"
    return year


def _parse_power(value: Optional[str]) -> Tuple[Optional[int], Optional[int]]:
    """Return (power_ps, power_kw)."""
    if not value:
        return None, None
    v = str(value)
    ps_m = re.search(r"(\d{2,4})\s*PS", v, re.I)
    kw_m = re.search(r"(\d{2,4})\s*kW", v, re.I)
    ps = int(ps_m.group(1)) if ps_m else None
    kw = int(kw_m.group(1)) if kw_m else None
    if ps and not kw:
        kw = round(ps * 0.735499)
    elif kw and not ps:
        ps = kw_to_ps(kw)
    return ps, kw


def _parse_fuel(value: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Return (mobile.de fuel key, label)."""
    if not value:
        return None, None
    n = value.upper()
    mapping = [
        ("DIESEL", "DIESEL"),
        ("HYBRID", "HYBRID"),
        ("ELEKTRO", "ELECTRICITY"), ("STROM", "ELECTRICITY"),
        ("BENZIN", "PETROL"), ("PETROL", "PETROL"),
        ("LPG", "LPG"), ("AUTOGAS", "LPG"),
        ("CNG", "CNG"), ("ERDGAS", "CNG"),
    ]
    for needle, key in mapping:
        if needle in n:
            return key, FUEL_LABELS.get(key, value)
    return None, value


def _parse_gearbox(value: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    if not value:
        return None, None
    n = value.upper()
    if "AUTOMATIK" in n or "AUTOMATIC" in n:
        return "AUTOMATIC_GEAR", GEAR_LABELS["AUTOMATIC_GEAR"]
    if "SCHALT" in n or "MANUAL" in n or "MANUELL" in n:
        return "MANUAL_GEAR", GEAR_LABELS["MANUAL_GEAR"]
    return None, value


def _parse_description(visible: str) -> Optional[str]:
    pos = visible.find("Beschreibung")
    if pos < 0:
        return None
    desc = visible[pos + len("Beschreibung"):]
    end = len(desc)
    for marker in ("\nAusstattung\n", "\nInserat bereitgestellt von",
                   "\nAnbieter", "\nNachricht", "\nKontakt"):
        p = desc.find(marker)
        if p >= 0:
            end = min(end, p)
    return _clean(desc[:end])


def _parse_equipment(visible: str) -> List[str]:
    """Pull comma-separated list under 'Ausstattung' if present, else fall
    back to a known-vocabulary scan. Filters out obvious noise items
    (price hints, seller-type lines, postcodes, country names)."""
    NOISE_PATTERNS = (
        re.compile(r"^\d{4,5}\b"),                      # postcode prefix
        re.compile(r"verhandlungs(basis|sache)", re.I),
        re.compile(r"\bvhb\b", re.I),
        re.compile(r"privat(anbieter|verkauf|person)", re.I),
        re.compile(r"^anbieter\b", re.I),
        re.compile(r"^h[äa]ndler\b", re.I),
        re.compile(r"^(deutschland|österreich|oesterreich|schweiz|polen|niederlande|belgien|frankreich|italien)\s*$", re.I),
        re.compile(r"^festpreis", re.I),
        re.compile(r"^preis", re.I),
        re.compile(r"^der preis", re.I),
        re.compile(r"\bzu verkaufen\b", re.I),
    )

    def _is_equipment_like(item: str) -> bool:
        if not item or len(item) < 2 or len(item) > 70:
            return False
        if item.endswith("."):
            return False
        for pat in NOISE_PATTERNS:
            if pat.search(item):
                return False
        return True

    items: List[str] = []
    m = re.search(
        r"\nAusstattung\n(.+?)(?:\nInserat bereitgestellt von|\nAnbieter|"
        r"\nNachricht|\nDer Preis|\nVerhandlungsbasis|\nPrivatanbieter|$)",
        "\n" + (visible or ""), re.S,
    )
    if m:
        for part in re.split(r",|\n", m.group(1)):
            it = _clean(part)
            if it and _is_equipment_like(it) and it not in items:
                items.append(it)
        return items
    # vocabulary fallback
    vocab = (
        "ABS", "Allwetterreifen", "Anhängerkupplung", "Bluetooth", "Bordcomputer",
        "Einparkhilfe", "Elektr. Fensterheber", "ESP", "Freisprecheinrichtung",
        "Isofix", "Klimaanlage", "Klimaautomatik", "Lederausstattung",
        "Leichtmetallfelgen", "Multifunktionslenkrad", "Navigationssystem",
        "Nebelscheinwerfer", "Nichtraucher-Fahrzeug", "Panoramadach",
        "Regensensor", "Scheckheftgepflegt", "Schiebedach", "Servolenkung",
        "Sitzheizung", "Standheizung", "Start/Stopp-Automatik", "Tempomat",
        "USB", "Xenonscheinwerfer", "Zentralverriegelung", "Keyless",
    )
    low = (visible or "").lower()
    for it in vocab:
        if it.lower() in low and it not in items:
            items.append(it)
    return items


def _extract_images(html_text: str, max_images: int = 60) -> List[str]:
    """Pull all kleinanzeigen.de prod-ads image URLs (deduped, normalized)."""
    if not html_text:
        return []
    cut = _cut_at_stop(html_text)
    seen: set = set()
    images: List[str] = []
    soup = _make_soup(cut)

    def add(src: Optional[str]) -> None:
        if not src:
            return
        s = html_lib.unescape(src).replace("\\u002F", "/").replace("\\/", "/").strip()
        if "img.kleinanzeigen.de/api/v1/prod-ads/images/" not in s:
            return
        s = s.split(" ")[0].split(",")[0]
        s = s.split('"')[0].split("'")[0]
        base = s.split("?")[0]
        if base and base not in seen:
            seen.add(base)
            images.append(base + "?rule=$_59.AUTO")

    for img in soup.find_all("img"):
        for attr in ("src", "data-src", "data-imgsrc", "data-lazy-src"):
            add(img.get(attr))
        srcset = img.get("srcset")
        if srcset:
            for part in srcset.split(","):
                add(part.strip().split(" ")[0])

    for pat in (
        r"https://img\.kleinanzeigen\.de/api/v1/prod-ads/images/[^\s\"'<>\\]+",
        r"https:\\/\\/img\.kleinanzeigen\.de\\/api\\/v1\\/prod-ads\\/images\\/[^\s\"'<>]+",
    ):
        for hit in re.findall(pat, cut):
            add(hit)

    return images[:max_images]


def _extract_item_id(url: str) -> Optional[str]:
    # /s-anzeige/<id> or /s-anzeige/<slug>/<id>(-cat-user)?
    m = re.search(r"/s-anzeige/(?:[^/]+/)?(\d{6,})(?:-\d+-\d+)?", url or "")
    if m:
        return m.group(1)
    # Legacy fallback: anywhere in the path "/<id>-<num>-<num>"
    m = re.search(r"/(\d{8,})-\d+-\d+", url or "")
    if m:
        return m.group(1)
    m = re.search(r"[?&]id=(\d{8,})", url or "")
    return m.group(1) if m else None


# -------------------- HTTP fetch (async) --------------------
_FETCH_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Upgrade-Insecure-Requests": "1",
    "Referer": "https://www.kleinanzeigen.de/",
    "DNT": "1",
}


def _assert_ip_public(host: str, infos) -> None:
    """Prueft die aufgeloesten Adressen gegen private/interne Bereiche."""
    import ipaddress
    for info in infos:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            raise RuntimeError(
                f"Abruf interner/privater Adresse blockiert ({host} -> {ip})."
            )


async def _assert_public_host(url: str) -> None:
    """SSRF-Defense-in-Depth: blockt das Abrufen interner/privater Adressen.

    Greift auch dann, wenn ein erlaubter Host per DNS auf eine interne IP
    zeigt (DNS-Rebinding) oder eine Redirect-Kette dorthin fuehrt. Wirft
    RuntimeError, wenn die URL auf eine private/loopback/link-local Adresse
    oder ein nicht-http(s)-Schema zeigt.

    DNS-Aufloesung laeuft ueber den asyncio-Resolver (loop.getaddrinfo),
    damit der Event-Loop bei vielen gleichzeitigen Scrapes NICHT durch
    blockierendes socket.getaddrinfo eingefroren wird.
    """
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if (parsed.scheme or "").lower() not in ("http", "https"):
        raise RuntimeError(f"Ungueltiges URL-Schema: {parsed.scheme!r}")
    host = parsed.hostname
    if not host:
        raise RuntimeError("URL ohne Host.")
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, None)
    except Exception:
        raise RuntimeError(f"Host nicht aufloesbar: {host!r}")
    _assert_ip_public(host, infos)


class ListingGone(RuntimeError):
    """Inserat existiert nicht mehr (404/410) - kein Retry, saubere Meldung."""


async def _fetch_html(url: str) -> str:
    await _assert_public_host(url)
    proxy = get_proxy_url()  # rotierender Proxy-Endpoint (oder None = direkt)
    last_exc: Optional[Exception] = None

    # Retry-Schleife: Bei 403/429 (Bot-Block / Rate-Limit) erneut versuchen.
    # Mit gesetztem rotierenden Proxy bekommt jeder Versuch eine neue IP.
    for attempt in range(max(1, SCRAPE_MAX_RETRIES)):
        # User-Agent pro Versuch rotieren, damit nicht jeder Request denselben
        # Fingerprint traegt.
        headers = dict(_FETCH_HEADERS)
        headers["User-Agent"] = random_user_agent()
        async with httpx.AsyncClient(
            headers=headers, follow_redirects=True, timeout=30.0,
            verify=_SSL_VERIFY, proxy=proxy,
        ) as client:
            if "kleinanzeigen.de" in url:
                try:
                    await client.get("https://www.kleinanzeigen.de/", timeout=15.0)
                except Exception:
                    pass
            is_last = attempt >= max(1, SCRAPE_MAX_RETRIES) - 1

            async def _backoff():
                # Jitter verhindert synchronisierte Retries unter Last;
                # nach dem LETZTEN Versuch nicht mehr sinnlos schlafen.
                if not is_last:
                    await asyncio.sleep((2 ** attempt) * random.uniform(0.6, 1.4))

            try:
                r = await client.get(url)
            except Exception as exc:
                last_exc = exc
                await _backoff()
                continue
            # Nach Redirects erneut pruefen: die finale URL darf ebenfalls
            # nicht auf eine interne Adresse zeigen.
            final_url = str(r.url)
            if final_url != url:
                await _assert_public_host(final_url)
                # Geloeschte Anzeigen werden von Kleinanzeigen mit HTTP 200
                # auf die Kategorie-/Suchseite umgeleitet: der /s-anzeige/-
                # Pfad verschwindet aus der URL -> Anzeige existiert nicht mehr.
                if "/s-anzeige/" in url and "/s-anzeige/" not in final_url:
                    raise ListingGone(
                        "Das Inserat ist bei Kleinanzeigen nicht mehr verfügbar "
                        "(gelöscht, beendet oder verkauft).")
            if r.status_code in (404, 410):
                # Inserat geloescht/deaktiviert - Retry ist sinnlos.
                raise ListingGone(
                    "Das Inserat ist bei Kleinanzeigen nicht mehr verfügbar "
                    "(gelöscht oder deaktiviert).")
            if r.status_code in (403, 429, 500, 502, 503, 504):
                # Block / Rate-Limit / CDN-Wackler -> erneut versuchen
                # (mit rotierendem Proxy = neue IP pro Versuch).
                last_exc = RuntimeError(
                    f"Kleinanzeigen antwortet mit HTTP {r.status_code}."
                )
                await _backoff()
                continue
            r.raise_for_status()
            return r.text

    raise RuntimeError(
        "Kleinanzeigen blockiert automatisierte Anfragen "
        f"(nach {SCRAPE_MAX_RETRIES} Versuchen). "
        "Bitte später erneut versuchen oder Proxy prüfen."
    ) from last_exc


# -------------------- public entry point --------------------
def is_kleinanzeigen_url(url: str) -> bool:
    return "kleinanzeigen.de" in (url or "").lower()


async def fetch_kleinanzeigen_vehicle(url: str) -> Dict[str, Any]:
    """Holt die Seite (Server-Abruf) UND wertet sie aus. Für den normalen
    server-seitigen Weg (mobile.de/AutoScout brauchen das ohnehin)."""
    html_text = await _fetch_html(url)
    return parse_kleinanzeigen_html(url, html_text)


# Marker, an denen wir eine ECHTE Kleinanzeigen-Fahrzeug-Detailseite
# erkennen — schützt den Client-Ingest gegen untergeschobenes Fremd-HTML.
# Wichtig: Die Daten landen im GLOBALEN Speicher, den alle Händler sehen.
# Ein zu lascher Check würde erlauben, fremde Inserate mit gefälschten
# Preisen zu "vergiften". Darum: mehrere strukturelle Marker UND die
# Anzeigen-Nummer aus der URL muss im HTML selbst vorkommen.
_KA_STRUCTURE_MARKERS = (
    "kleinanzeigen.de/s-anzeige",   # kanonischer Link der Detailseite
    "viewad-title",                 # Titel-Element der echten Seite
    "viewad-price",                 # Preis-Element der echten Seite
    "viewad-details",               # Detail-Tabelle (Marke, Modell, EZ …)
    "viewad-locality",              # Standort-Element
    "gsm_ad",                       # internes Tracking der echten Seite
)


def looks_like_kleinanzeigen_listing(html_text: str, url: str = "") -> bool:
    """Strenge Plausibilitätsprüfung: Ist das wirklich DIE Kleinanzeigen-
    Detailseite zu DIESER URL? (Der Nutzer-Browser liefert das HTML — wir
    vertrauen ihm nicht.)"""
    if not html_text or len(html_text) < 500:
        return False
    low = html_text.lower()
    # 1) Mindestens 3 der strukturellen Marker der echten Detailseite.
    if sum(1 for m in _KA_STRUCTURE_MARKERS if m in low) < 3:
        return False
    # 2) Die Anzeigen-Nummer aus der URL muss im HTML vorkommen (kanonischer
    #    Link, Tracking, Teilen-Knopf …) — sonst gehört das HTML zu einer
    #    ANDEREN Anzeige oder ist frei erfunden.
    item_id = _extract_item_id(url) if url else None
    if item_id and item_id not in html_text:
        return False
    return True


def parse_kleinanzeigen_html(url: str, html_text: str) -> Dict[str, Any]:
    """Wertet BEREITS geladenes HTML aus (egal ob vom Server oder vom
    Browser des Nutzers geholt). Return-Form identisch zum mobile_service-
    Fahrzeug, damit PDF/Verträge/Cache unverändert weiterlaufen."""
    soup = _make_soup(html_text)
    visible = _cut_at_stop(_visible_text(soup))

    # Geloeschte/beendete Anzeigen liefern oft HTTP 200 mit einer Hinweis-
    # Seite. Am Seitenanfang erkennen -> saubere Meldung statt Muell-Daten.
    _head = visible[:600].lower()
    _GONE = ("nicht mehr verfügbar", "nicht mehr verfugbar",
             "anzeige wurde gelöscht", "anzeige wurde geloscht",
             "anzeige ist leider nicht mehr", "wurde bereits verkauft",
             "anzeige nicht gefunden")
    if any(m in _head for m in _GONE):
        raise ListingGone(
            "Das Inserat ist bei Kleinanzeigen nicht mehr verfügbar "
            "(gelöscht, beendet oder verkauft).")

    title = _parse_title(soup, visible)
    price, price_amount = _parse_price(visible)
    location = _parse_location(visible)
    seller_zip, seller_city = _split_location(location)
    structured = _parse_structured(visible)

    raw_brand = structured.get("Marke")
    raw_model = structured.get("Modell")
    # Kleinanzeigen often appends a generation/variant suffix like "Aygo (X)"
    # or "Octavia (Mk3)" — strip parenthesized parts before mapping to the
    # mobile.de catalogue, otherwise the lookup misses.
    if raw_model:
        cleaned = re.sub(r"\s*\([^)]*\)\s*", "", raw_model).strip()
        if cleaned:
            raw_model = cleaned

    # Map to mobile.de IDs via the existing JSON catalogue.
    pseudo = {"make_label": raw_brand, "make": raw_brand,
              "model_label": raw_model, "model": raw_model}
    make_id, make_entry = _resolve_make(pseudo)
    model_id = _resolve_model(make_entry, pseudo) if make_entry else None

    # If the brand wasn't found, try matching against the visible text.
    if not make_entry and (title or visible):
        from mobile_service import _MAKES_INDEX, _normalize  # local import to avoid cycle at top
        haystack = _normalize(f"{title or ''} {visible[:500]}")
        for norm, entry in _MAKES_INDEX.items():
            if len(norm) >= 3 and norm in haystack:
                make_entry = entry
                make_id = entry["id"]
                pseudo["make_label"] = entry["raw_name"]
                model_id = _resolve_model(entry, pseudo)
                break

    fr = _parse_first_registration(structured.get("Erstzulassung"))
    ps, kw = _parse_power(structured.get("Leistung"))
    fuel_key, fuel_label = _parse_fuel(structured.get("Kraftstoffart"))
    gear_key, gear_label = _parse_gearbox(structured.get("Getriebe"))
    images = _extract_images(html_text)
    item_id = _extract_item_id(url)

    # Same shape as `_parse_ad_xml` output → reusable in build_search_url.
    result = {
        "mobile_ad_id": item_id or "",
        "kleinanzeigen_id": item_id,
        "kleinanzeigen_url": url,
        "detail_url": url,
        "make": (make_entry or {}).get("raw_name") or raw_brand,
        "make_label": (make_entry or {}).get("raw_name") or raw_brand,
        "model": raw_model,
        "model_label": raw_model,
        "model_description": title,
        "category": None,
        "category_label": structured.get("Fahrzeugtyp"),
        "first_registration": fr,
        "mileage": _to_int(structured.get("Kilometerstand")),
        "fuel": fuel_key,
        "fuel_label": fuel_label,
        "gearbox": gear_key,
        "gearbox_label": gear_label,
        "power_kw": kw,
        "power_ps": ps,
        "displacement": _to_int(structured.get("Hubraum")),
        "doors": structured.get("Anzahl Türen"),
        "seats": _to_int(structured.get("Anzahl Sitzplätze")),
        "color": structured.get("Außenfarbe"),
        "vin": None,
        "license_plate": None,
        "hu": structured.get("HU bis"),
        "previous_owners": _to_int(structured.get("Anzahl der Fahrzeughalter"))
                           or extract_owners_from_text(visible),
        "accident_damaged": False,
        "roadworthy": True,
        "features": _parse_equipment(visible),
        "description": _parse_description(visible) or _meta(soup, "og:description", "description"),
        "list_price": float(price_amount) if price_amount else None,
        "currency": "EUR",
        "seller_name": None,
        "seller_address": None,           # KA zeigt Strasse selten oeffentlich
        "seller_zip": seller_zip,         # aus location-Zeile extrahiert
        "seller_city": seller_city,       # aus location-Zeile extrahiert (ohne Bezirk/Bundesland)
        "seller_phone": None,
        "seller_email": None,
        "title": title,
        "price_label": price,
        "location": location,
        "images": images,
        "image_count": len(images),
        "_resolved_make_id": make_id,
        "_resolved_model_id": model_id,
        "_source": "kleinanzeigen",
    }
    # Generic-model recovery: turn "Weitere Peugeot" into "407" when the
    # title (e.g. "Peugeot 407 sW") or description contains a known model.
    _enhance_kleinanzeigen_model(result)
    return result


def _enhance_kleinanzeigen_model(vehicle: dict) -> dict:
    """Apply the same generic-model recovery that mobile_service uses."""
    try:
        from mobile_service import _enhance_generic_model
        _enhance_generic_model(vehicle)
    except Exception:
        pass
    return vehicle
