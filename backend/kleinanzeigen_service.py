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


# Rollenprüfung 22.09.2026 (RP-436): Obergrenze fuer einen plausiblen
# Kilometerstand. Alles darueber ist ein Lesefehler, kein Auto.
KM_MAX = 2_000_000


def _km_aus_text(value: Any) -> Optional[int]:
    """Kilometerstand aus einem Tabellen- oder Textwert.

    Rollenprüfung 22.09.2026 (RP-436): _to_int verkettete ALLE Ziffern —
    "185.000 km (Motor bei 120.000 km getauscht)" wurde 185000120000 und
    landete so in Vergleich, Vertrag und Suchlink. Jetzt zaehlt nur die ERSTE
    Zahl (deutsches Tausenderformat oder ohne Punkte), "150 Tkm" gilt als
    150.000, und Unplausibles (ueber 2 Mio.) wird verworfen."""
    if value is None:
        return None
    s = str(value).replace("\xa0", " ")
    m = re.search(r"\d{1,3}(?:[. ]\d{3})+(?!\d)|\d+", s)
    if not m:
        return None
    km = int(re.sub(r"\D", "", m.group(0)))
    rest = s[m.end():].lstrip().lower()
    if rest.startswith(("tkm", "tsd", "tausend")):
        km *= 1000
    return km if km <= KM_MAX else None


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
    # copy.copy() dupliziert den Baum direkt — der alte Weg
    # (_make_soup(str(soup))) serialisierte und parste die komplette
    # Seite ein ZWEITES Mal (Review 09/2026: halbiert die CPU-Zeit).
    import copy as _copy
    kopie = _copy.copy(soup)
    for tag in kopie(["script", "style", "noscript", "svg"]):
        tag.decompose()
    return _clean(kopie.get_text("\n")) or ""


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


def _preis_aus_seite(soup: BeautifulSoup, visible: str) -> Tuple[Optional[str], Optional[int]]:
    """Preis (Anzeige, Betrag) einer Kleinanzeigen-Detailseite.

    Rollenprüfung 22.09.2026 (RP-437): Vorher galt der ERSTE Betrag mit "€"
    im ganzen sichtbaren Text. Bei "VB" ohne Betrag wurde so aus "Zahnriemen
    neu fuer 800 € gewechselt" der Listenpreis 800 — im Vergleich und als
    "Preis laut Inserat" im Beweisdokument. Jetzt zaehlt das Preis-Element
    der Seite; nur ohne Element der Text VOR der Beschreibung."""
    def _mit_vb(text: str, label: Optional[str], betrag: Optional[int]):
        vb = bool(re.search(r"\bVB\b|verhandlungsbasis", text or "", re.I))
        if betrag:
            return (f"{label} VB" if vb and label else label), betrag
        if vb:
            return "VB", None
        if re.search(r"verschenken", text or "", re.I):
            return "Zu verschenken", None
        return None, None

    el = soup.find(id="viewad-price")
    if el is not None:
        text = _clean(el.get_text(" ")) or ""
        label, betrag = _parse_price(text)
        return _mit_vb(text, label, betrag)
    meta = soup.find("meta", attrs={"itemprop": "price"})
    roh = (meta.get("content") if meta else None) or _meta(
        soup, "product:price:amount", "og:price:amount")
    if roh:
        try:
            betrag = int(round(float(str(roh).replace(",", "."))))
        except ValueError:
            betrag = None
        if betrag and betrag > 0:
            return f"{betrag:,}".replace(",", ".") + " €", betrag
    kopf = visible or ""
    m = re.search(r"(?m)^\s*Beschreibung\s*$", kopf)
    if m:
        kopf = kopf[:m.start()]
    label, betrag = _parse_price(kopf)
    if betrag:
        return label, betrag
    if re.search(r"(?mi)^\s*(VB|Verhandlungsbasis)\s*$", kopf):
        return "VB", None
    return None, None


# Bundeslaender in allen Schreibweisen der Kleinanzeigen-Ortszeile.
_BUNDESLAENDER = (
    "baden-württemberg", "baden-wuerttemberg", "bayern", "berlin", "brandenburg",
    "bremen", "hamburg", "hessen", "mecklenburg-vorpommern", "niedersachsen",
    "nordrhein-westfalen", "nrw", "rheinland-pfalz", "saarland", "sachsen",
    "sachsen-anhalt", "schleswig-holstein", "thüringen", "thueringen",
)


def _ist_bundesland(text: Optional[str]) -> bool:
    return (text or "").strip().lower() in _BUNDESLAENDER


def _ort_aus_seite(soup: BeautifulSoup, visible: str,
                   titel: Optional[str] = None) -> Optional[str]:
    """Ortszeile: zuerst das Ortselement der Seite, sonst der Text.

    Rollenprüfung 22.09.2026 (RP-438): der Rueckfall nahm die erste Zeile mit
    irgendeiner fuenfstelligen Zahl — aus dem Titel "VW Golf 7 1.4 TSI
    85000 km Scheckheft" wurden PLZ 85000 und Ort "km Scheckheft" im Vertrag."""
    el = soup.find(id="viewad-locality") or soup.find(attrs={"itemprop": "addressLocality"})
    if el is not None:
        t = _clean(el.get_text(" "))
        if t and re.search(r"\b\d{5}\b", t):
            return t
    return _parse_location(visible, titel=titel)


def _strasse_aus_seite(soup: BeautifulSoup) -> Optional[str]:
    """RP-441: Strasse, wenn die Seite sie zeigt (gewerbliche Anbieter)."""
    el = soup.find(id="street-address") or soup.find(attrs={"itemprop": "streetAddress"})
    if el is None:
        return None
    t = (_clean(el.get_text(" ")) or "").strip().rstrip(",").strip()
    return t or None


def _parse_location(text: str, titel: Optional[str] = None) -> Optional[str]:
    """Return e.g. '10115 Berlin' or full plz+stadt+land line.

    Rollenprüfung 22.09.2026 (RP-438): nur Zeilen, die MIT der PLZ beginnen
    und danach einen Ortsnamen tragen — keine Titelzeile, nichts mit km, €
    oder PS."""
    states = (
        "Bayern|NRW|Nordrhein|Hessen|Sachsen|Berlin|Hamburg|Bremen|Saarland|"
        "Brandenburg|Thüringen|Niedersachsen|Rheinland|Schleswig|Mecklenburg|"
        "Baden|Württemberg|Sachsen-Anhalt"
    )
    titel_norm = (titel or "").strip().lower()
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    for i, line in enumerate(lines):
        if not re.match(r"^\d{5}\s+[A-ZÄÖÜ]", line):
            continue
        if titel_norm and line.lower() == titel_norm:
            continue
        if re.search(r"\bkm\b|€|\bPS\b|\bkW\b", line, re.I):
            continue
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
        ('10115', 'Berlin')    fuer '10115 Berlin'
        ('10115', 'Berlin')    fuer '10115 Berlin - Mitte'   (Bezirk verworfen)
        ('80331', 'Muenchen')  fuer '80331 Muenchen Bayern'  (Bundesland verworfen)
        ('84130', 'Dingolfing') fuer '84130 Bayern - Dingolfing' (RP-441)
        ('70173', 'Stuttgart') fuer '70173 Baden-Württemberg - Stuttgart'
        (None, None)           wenn keine PLZ gefunden
    """
    if not location:
        return (None, None)
    # Rollenprüfung 22.09.2026 (RP-441): Kleinanzeigen schreibt die Ortszeile
    # auch als "PLZ Bundesland - Ort". Vorher wurde das Bundesland zum Ort
    # ("Bayern") bzw. am Bindestrich zerschnitten ("Baden"). Ist der Teil vor
    # " - " ein Bundesland, ist der Teil danach der Ort — ausser in den
    # Stadtstaaten, dort ist er der Stadtteil (wie im API-Weg).
    m2 = re.search(r"\b(\d{5})\b\s*(.+?)\s+-\s+(.+)$", location.strip())
    if m2 and _ist_bundesland(m2.group(2)):
        plz, land, ort = m2.group(1), m2.group(2).strip(), m2.group(3).strip()
        land_klein = land.lower()
        if land_klein in ("berlin", "hamburg"):
            return (plz, land)
        if land_klein == "bremen":
            return (plz, "Bremerhaven" if ort.lower().startswith("bremerhaven") else "Bremen")
        # "PLZ Bundesland - Ort - Ortsteil": der Ortsteil gehoert nicht dazu.
        ort = ort.split(" - ")[0].strip()
        return (plz, ort or None)
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
    Mirrors the layout of kleinanzeigen.de's vehicle property table.

    Rollenprüfung 22.09.2026 (RP-436): Eine Zeile "Kilometerstand: …" aus der
    Beschreibung des Verkaeufers ueberschrieb den Tabellenwert. Jetzt:
    Tabellenwerte (Beschriftung + naechste Zeile) haben Vorrang, "Feld: Wert"
    aus dem Text fuellt nur fehlende Felder, und gelesen wird nur bis zur
    Ueberschrift "Beschreibung". Findet sich davor gar keine Tabelle
    (abweichendes Layout), wird wie bisher die ganze Seite gelesen."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    wanted_lower = {w.lower(): w for w in WANTED_FIELDS}
    skip_values = {"beschreibung", "ausstattung"}
    ende = next((i for i, ln in enumerate(lines) if ln.lower() == "beschreibung"), len(lines))

    def _sammeln(bis: int) -> Tuple[Dict[str, str], Dict[str, str]]:
        tabelle: Dict[str, str] = {}
        fliesstext: Dict[str, str] = {}
        for i in range(bis):
            line = lines[i]
            low = line.lower()
            if low in wanted_lower:
                for j in range(i + 1, min(i + 6, bis)):
                    val = lines[j]
                    vlow = val.lower()
                    if vlow not in wanted_lower and vlow not in skip_values:
                        tabelle[wanted_lower[low]] = val
                        break
            for w_lower, original in wanted_lower.items():
                if low.startswith(w_lower + ":"):
                    raw = line.split(":", 1)[1].strip()
                    if raw and raw.lower() != w_lower:
                        fliesstext.setdefault(original, raw)
        return tabelle, fliesstext

    tabelle, fliesstext = _sammeln(ende)
    if not tabelle and not fliesstext and ende < len(lines):
        tabelle, fliesstext = _sammeln(len(lines))
    result: Dict[str, str] = {**fliesstext, **tabelle}

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
    # 17.09.2026: zentrale Zuordnung (fahrzeug_codes) — vorher wurde z. B.
    # "Hybrid (Diesel/Elektro)" wegen "DIESEL" zuerst als Diesel erkannt.
    from fahrzeug_codes import kraftstoff_code
    key = kraftstoff_code(value)
    if key:
        return key, FUEL_LABELS.get(key, value)
    return None, value


def _parse_gearbox(value: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    if not value:
        return None, None
    # 17.09.2026: zentrale Zuordnung — "Halbautomatik" war vorher
    # AUTOMATIC_GEAR (enthaelt "AUTOMATIK").
    from fahrzeug_codes import getriebe_code
    key = getriebe_code(value)
    if key:
        return key, GEAR_LABELS.get(key, value)
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


# Runde 29 (12.09.2026): Aus der Funktion herausgezogen, damit der Weg ueber
# die Kleinanzeigen-API (kleinanzeigen_api.py) GENAU dieselben Rausch-Regeln
# benutzt. Vorher lagen sie innen und der API-Weg musste seine Merkmale zum
# Filtern wieder zu einer Komma-Zeile zusammenfuegen — dabei zerfielen
# Merkmale mit Komma in der Klammer ("Audiosystem (Touchscreen, MP3)").
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
    # Pruefbericht 20.09.2026 (S-15): Zustands- und Verkaufsaussagen aus dem
    # Fliesstext landeten als "Ausstattung laut Inserat" im Kaufvertrag
    # ("Unfallfrei", "TÜV neu", "2. Hand", "kein Raucherauto").
    re.compile(r"unfall", re.I),
    re.compile(r"\bt[üu]v\b|\bhu\b|\bau\b neu|hauptuntersuchung", re.I),
    re.compile(r"\b\d+\.?\s*hand\b|\bhand\s*\d", re.I),
    # "kein Raucherauto" faengt die naechste Zeile; "Nichtraucherfahrzeug"
    # (ein Wort) ist ein offizielles Merkmal und bleibt.
    re.compile(r"^(kein|keine|ohne|nicht)\b", re.I),
    re.compile(r"\b(verkaufe|verkaufen|biete|tausch|inzahlung|probefahrt|besichtigung)\w*", re.I),
    # "Scheckheftgepflegt" und "Nichtraucherfahrzeug" sind dagegen offizielle
    # Merkmale der Portale (Vokabelliste unten) und bleiben erhalten.
    re.compile(r"\b(top|guter|sehr guter|gepflegter)\s+zustand\b", re.I),
    re.compile(r"\b(mängel|maengel|defekt|kratzer|delle|rost)\b", re.I),
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


def _zustand_unfall(text) -> Optional[bool]:
    from mobile_service import zustand_unfall
    return zustand_unfall(None, str(text or ""))


# Obergrenze der Ausstattungsliste (wie kleinanzeigen_api.MAX_MERKMALE).
MAX_AUSSTATTUNG = 150


def _parse_equipment(visible: str) -> List[str]:
    """Pull comma-separated list under 'Ausstattung' if present, else fall
    back to a known-vocabulary scan. Filters out obvious noise items
    (price hints, seller-type lines, postcodes, country names).

    Pruefbericht 20.09.2026 (A-01): Die Dublettenpruefung lief ueber eine
    Liste (`it not in items`) ohne Obergrenze — quadratisch. 0,5 MB
    eingereichtes HTML hielten den Server 12 Sekunden fest, die erlaubten
    6 MB ueber zwanzig Minuten. Jetzt Menge + Obergrenze."""

    items: List[str] = []
    gesehen: set = set()
    m = re.search(
        r"\nAusstattung\n(.+?)(?:\nInserat bereitgestellt von|\nAnbieter|"
        r"\nNachricht|\nDer Preis|\nVerhandlungsbasis|\nPrivatanbieter|$)",
        "\n" + (visible or ""), re.S,
    )
    if m:
        for part in re.split(r",|\n", m.group(1)):
            it = _clean(part)
            if it and _is_equipment_like(it) and it not in gesehen:
                gesehen.add(it)
                items.append(it)
                if len(items) >= MAX_AUSSTATTUNG:
                    break
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


# Rollenprüfung 22.09.2026 (RP-202/RP-353): Die Klasse liegt jetzt in
# anbieter_fehler (auch mobile.de/AutoScout24 melden damit ein Offline-
# Inserat). Hier nur weitergereicht — `from kleinanzeigen_service import
# ListingGone` bleibt ueberall gueltig und ist dieselbe Klasse.
from anbieter_fehler import ListingGone  # noqa: E402,F401


_WEITERLEITUNGEN = (301, 302, 303, 307, 308)
_MAX_WEITERLEITUNGEN = 5


async def _get_mit_geprueften_weiterleitungen(client, url: str):
    """GET, bei dem JEDE Weiterleitung VOR dem Abruf geprueft wird.

    Nachpruefung 20.09.2026, Nr. 74: vorher lief der Abruf mit
    `follow_redirects=True`. httpx holte damit das Ziel der Weiterleitung
    bereits, und erst DANACH wurde die Endadresse gegen interne Adressen
    geprueft. Bei einer offenen Weiterleitung auf der erlaubten Domain war
    der Zugriff auf eine interne Adresse also schon passiert — die
    Pruefung kam zu spaet. Der Bild-Proxy loest dasselbe Problem seit
    laengerem richtig (bild_proxy._holen); hier ist es jetzt genauso."""
    ziel = url
    for _ in range(_MAX_WEITERLEITUNGEN):
        r = await client.get(ziel, follow_redirects=False)
        if r.status_code not in _WEITERLEITUNGEN:
            return r, ziel
        ort = r.headers.get("location") or ""
        if not ort:
            return r, ziel
        ziel = str(httpx.URL(ziel).join(ort))
        # ZUERST pruefen, DANN abrufen.
        await _assert_public_host(ziel)
    raise RuntimeError("Zu viele Weiterleitungen beim Abruf.")


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
        # Nr. 74: follow_redirects=False — die Weiterleitungen verfolgt
        # _get_mit_geprueften_weiterleitungen() von Hand und prueft jede
        # Stufe VOR dem Abruf.
        async with httpx.AsyncClient(
            headers=headers, follow_redirects=False, timeout=30.0,
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
                r, final_url = await _get_mit_geprueften_weiterleitungen(client, url)
            except ListingGone:
                raise
            except Exception as exc:
                last_exc = exc
                await _backoff()
                continue
            if final_url != url:
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


# -------------------- Marke aus dem Titel --------------------
# Rollenprüfung 22.09.2026 (Review): Katalogmarken, die zugleich gewoehnliche
# Woerter sind ("Man kann ihn besichtigen", "Smart Key", "Ruf mich an",
# "Mini Bagger", "Klima/AC", "Ego"). Aus dem Titel zaehlen sie nur, wenn
# direkt dahinter ein bekanntes Modell DIESER Marke steht ("MAN TGE",
# "smart fortwo", "Mini Cooper", "MG ZS", "ORA Funky Cat") ...
_MEHRDEUTIGE_MARKEN = frozenset({"man", "smart", "ruf", "mini", "ego", "ora", "ac", "mg"})
# ... oder als Kuerzel genau in dieser Schreibweise, am Titelanfang bzw. in
# einem nicht durchgehend gross geschriebenen Titel ("MAN TGX 18.510 Sattel-
# zugmaschine" — Lkw-Modelle kennt der Pkw-Katalog nicht).
_MARKEN_KUERZEL = {"man": "MAN", "mg": "MG"}
# Sammelposten des Katalogs ("Andere") sind keine Marke.
_KEINE_MARKE = frozenset({"andere", "sonstige", "weitere"})


def _modell_folgt(eintrag: Dict[str, Any], danach: List[str]) -> bool:
    """Steht direkt hinter dem Markenwort ein Modell dieser Marke (ein oder
    zwei Woerter, exakt — ohne die Praefix-Verkuerzung von _resolve_model,
    sonst passte "Man kann" ueber "k…")?"""
    from mobile_service import _normalize
    modelle = eintrag.get("models") or {}
    for anzahl in (1, 2):
        if len(danach) < anzahl:
            break
        norm = _normalize("".join(danach[:anzahl]))
        if norm and norm not in _KEINE_MARKE and norm in modelle:
            return True
    return False


def _marke_aus_titel(titel: Optional[str]) -> Optional[Dict[str, Any]]:
    """Marke aus dem Inseratstitel, wenn die Detailtabelle keine nennt.

    Rollenprüfung 22.09.2026 (Review zu RP-439): NUR der Titel — die
    Beschreibung nennt zu oft andere Marken ("baugleich Opel Movano",
    "Motor von Renault") und gewoehnliche Woerter ("Man kann ..."). Wortweise
    (nicht als Teilzeichenkette — "Mini" steckt in "Minimal", "Ruf" in
    "Anruf"): je Wortposition zuerst drei, dann zwei Woerter zusammen
    ("Land Rover", "Mercedes Benz"), dann das Einzelwort; Kuerzel wie "VW"
    ueber die Aliasliste. Mehrdeutige Marken nur mit Modell dahinter bzw.
    als Kuerzel (siehe oben); "Andere" nie."""
    if not titel:
        return None
    from mobile_service import _MAKE_ALIASES, _MAKES_INDEX, _normalize
    text = str(titel)[:200]
    woerter = re.findall(r"[0-9A-Za-zÄÖÜäöüßéèëçÉ]+", text)
    durchgehend_gross = not any(c.islower() for c in text)
    for i in range(len(woerter)):
        for laenge in (3, 2, 1):
            if i + laenge > len(woerter):
                continue
            geschrieben = "".join(woerter[i:i + laenge])
            norm = _normalize(geschrieben)
            if len(norm) < 2 or norm in _KEINE_MARKE:
                continue
            norm = _MAKE_ALIASES.get(norm, norm)
            eintrag = _MAKES_INDEX.get(norm)
            if not eintrag or norm in _KEINE_MARKE:
                continue
            if norm in _MEHRDEUTIGE_MARKEN:
                als_kuerzel = (_MARKEN_KUERZEL.get(norm) == geschrieben
                               and (i == 0 or not durchgehend_gross))
                if not (als_kuerzel or _modell_folgt(eintrag, woerter[i + laenge:i + laenge + 2])):
                    continue
            return eintrag
    return None


def _marke_fehlt(roh: Optional[str]) -> bool:
    """Nennt die Detailtabelle keine echte Marke? Leer oder ein Sammelposten
    wie "Weitere Automarken" — nur dann darf der Titel aushelfen. Eine
    konkrete, dem Katalog unbekannte Marke bleibt stehen (Review zu RP-439)."""
    from mobile_service import _is_generic_model_label
    return not str(roh or "").strip() or _is_generic_model_label(roh)


# -------------------- public entry point --------------------
def is_kleinanzeigen_url(url: str) -> bool:
    return "kleinanzeigen.de" in (url or "").lower()


async def fetch_kleinanzeigen_vehicle(url: str) -> Dict[str, Any]:
    """Holt die Seite (Server-Abruf) UND wertet sie aus. Für den normalen
    server-seitigen Weg (mobile.de/AutoScout brauchen das ohnehin)."""
    html_text = await _fetch_html(url)
    # CPU-Parse (BS4/lxml) in den Threadpool — der Fetch-Pfad laeuft in
    # /mobile/compare, /listings/resolve UND im Link-Job-Worker; sync
    # blockierte jeder Parse alle gleichzeitigen Requests (Review 09/2026).
    return await asyncio.to_thread(parse_kleinanzeigen_html, url, html_text)


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
    # Rollenprüfung 22.09.2026 (RP-437/RP-438/RP-441): Preis und Ort aus den
    # Elementen der Seite statt aus dem ersten passenden Text irgendwo.
    price, price_amount = _preis_aus_seite(soup, visible)
    location = _ort_aus_seite(soup, visible, titel=title)
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

    # Rollenprüfung 22.09.2026 (Review zu RP-439): Ohne Marke in der Tabelle
    # die Marke aus dem TITEL — wortweise und streng (_marke_aus_titel). Vorher
    # suchte hier eine Teilzeichenkette im zusammengezogenen Seitentext
    # ("Kein Anruf" -> "Ruf", "manuell" -> "MAN") und ueberschrieb sogar eine
    # konkrete, nur dem Katalog unbekannte Marke aus der Tabelle.
    marke_aus_titel = False
    if not make_entry and _marke_fehlt(raw_brand):
        entry = _marke_aus_titel(title)
        if entry:
            make_entry = entry
            make_id = entry["id"]
            marke_aus_titel = True
            pseudo["make_label"] = pseudo["make"] = entry["raw_name"]
            model_id = _resolve_model(entry, pseudo)

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
        # RP-436: erste Zahl, plausibel begrenzt (vorher alle Ziffern verkettet)
        "mileage": _km_aus_text(structured.get("Kilometerstand")),
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
        # Pruefbericht 20.09.2026 (S-05/S-06): nur echte Angaben, sonst None.
        "accident_damaged": _zustand_unfall(structured.get("Fahrzeugzustand")),
        "roadworthy": None,
        "features": _parse_equipment(visible),
        "description": _parse_description(visible) or _meta(soup, "og:description", "description"),
        "list_price": float(price_amount) if price_amount else None,
        "currency": "EUR",
        "seller_name": None,
        # KA zeigt die Strasse selten oeffentlich — wenn doch (RP-441), mitnehmen.
        "seller_address": _strasse_aus_seite(soup),
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
        # Review zu RP-439: Marke nur aus dem Titel geschlossen -> der
        # Vergleich bittet den Sucher, sie vor dem Kaufvertrag zu pruefen.
        "_marke_aus_titel": marke_aus_titel,
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
