# -*- coding: utf-8 -*-
"""Scraper-Zeile (Suchergebnis) -> unser Listing. Nur fahrzeug-/preisbezogene
Felder plus seller_id/seller_type/PLZ/Ort — keine Telefonnummer, kein
Verkaeufername (Datenschutz, Auftrag Punkt 33)."""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

from markt.konfig import QUELLE

_KW = re.compile(r"(\d{2,4})\s*kW", re.I)
_PS = re.compile(r"\((\d{2,4})\s*PS\)", re.I)
_EUR = re.compile(r"\d[\d.]*")
# Reparaturwelle 6 Nr. 85: Preise kommen als 19.990 / 19,990 / 19 990 / 19.990,00 / 19,990.00 / 19990
_PREIS_TOKEN = re.compile(r"\d[\d.,\s\xa0]*\d|\d")
PREIS_MIN, PREIS_MAX = 100, 5_000_000      # Plausibilitaet: sonst 'Preis unplausibel'
GRUND_PREIS_UNPLAUSIBEL = "Preis unplausibel"


def _int(w: Any) -> Optional[int]:
    if w is None or isinstance(w, bool):
        return None
    if isinstance(w, (int, float)):
        return int(w)
    m = _EUR.search(str(w).replace("\xa0", " "))
    if not m:
        return None
    try:
        return int(m.group(0).replace(".", ""))
    except ValueError:
        return None


def preis_parsen(w: Any) -> Optional[float]:
    """Nr. 85: robustes Parsen eines Preises. Regel: steht hinter dem LETZTEN Punkt/Komma eine
    Gruppe mit genau zwei Ziffern, sind das Nachkommastellen; alles andere sind
    Tausendertrennzeichen. None, wenn keine Zahl erkennbar ist (nicht: unplausibel)."""
    if w is None or isinstance(w, bool):
        return None
    if isinstance(w, (int, float)):
        return float(w)
    m = _PREIS_TOKEN.search(str(w))
    if not m:
        return None
    t = re.sub(r"[\s\xa0]", "", m.group(0))
    idx = max(t.rfind(","), t.rfind("."))
    try:
        if idx >= 0 and len(t) - idx - 1 == 2:
            ganz = re.sub(r"[.,]", "", t[:idx]) or "0"
            return float(int(ganz)) + int(t[idx + 1:]) / 100.0
        return float(int(re.sub(r"[.,]", "", t)))
    except ValueError:
        return None


def preis_plausibel(p: Optional[float]) -> bool:
    return p is not None and PREIS_MIN <= float(p) <= PREIS_MAX


def _leistung(text: Any) -> Dict[str, Optional[int]]:
    s = str(text or "").replace("\xa0", " ")
    kw = _KW.search(s)
    ps = _PS.search(s)
    return {"power_kw": int(kw.group(1)) if kw else None, "power_ps": int(ps.group(1)) if ps else None}


def _preisbewertung(pr: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(pr, dict):
        return None
    grenzen = [_int(x) for x in (pr.get("thresholdLabels") or [])]
    return {"rating": pr.get("rating"), "label": pr.get("ratingLabel"),
            "thresholds": [g for g in grenzen if g is not None] or None}


_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:?\d{2})?)?$")
_MONAT_JAHR = re.compile(r"^(0[1-9]|1[0-2])/(\d{4})$")


def _iso(w: Any) -> Optional[str]:
    """Reparaturwelle 6 Nr. 86: nur ein gueltiger ISO-Zeitstempel (Datum oder Datum+Zeit) oder
    'MM/JJJJ' — alles andere (kaputte Werte wie '2026-13-45', '2026abc') wird None."""
    from datetime import datetime as _dt
    s = str(w or "").strip()
    if not s:
        return None
    if _MONAT_JAHR.match(s):
        return s
    if not _ISO.match(s):
        return None
    try:
        _dt.fromisoformat(s.replace("Z", "+00:00").replace(" ", "T"))
    except ValueError:
        return None
    return s


_SELLER_TYP = {"dealer": "DEALER", "haendler": "DEALER", "händler": "DEALER", "private": "PRIVATE",
               "private seller": "PRIVATE", "privat": "PRIVATE", "privatanbieter": "PRIVATE"}
_PLZ = re.compile(r"\b(\d{5})\b")


def _seller_typ(w: Any) -> str:
    s = str(w or "").strip()
    return _SELLER_TYP.get(s.lower(), s.upper()) if s else ""


def _preisbewertung_text(rating: Any, label: Any, offset: Any) -> Optional[Dict[str, Any]]:
    if not rating:
        return None
    return {"rating": str(rating), "label": label, "thresholds": None,
            "offset": _int(offset) if offset not in (None, "") else None}


def beschaedigt(item: Dict[str, Any]) -> bool:
    """Unfall-/beschaedigtes Fahrzeug laut Scraper (scrapesmith: hasDamage/isDamageCase,
    sourabhbgp: damaged/accident, Freitext 'Unfallfahrzeug' im Zustand)."""
    for k in ("hasDamage", "isDamageCase", "damaged", "damageCase", "accident", "accidentDamaged", "isAccidentDamaged"):
        w = item.get(k)
        if w is True or (isinstance(w, (int, float)) and not isinstance(w, bool) and w == 1):
            return True
        if isinstance(w, str) and w.strip().lower() in ("true", "yes", "ja", "1"):
            return True
    zustand = str(item.get("condition") or item.get("damageCondition") or "").lower()
    if not zustand:
        return False
    # Verneinungen zuerst ("not damaged", "accident free", "unfallfrei") — sonst falsch positiv
    if any(v in zustand for v in ("not damaged", "undamaged", "accident free", "accident-free", "unfallfrei", "kein unfall", "no damage")):
        return False
    return "unfall" in zustand or "damaged" in zustand or "accident" in zustand


def listing_aus_item(item: Dict[str, Any], *, beschaedigte_verwerfen: bool = True) -> Optional[Dict[str, Any]]:
    """None, wenn keine ID oder kein Bruttopreis (dann unbrauchbar).
    Versteht beide Scraper: sourabhbgp (priceGross, power "135 kW (184 PS)",
    fuel/gearbox, seller{type}, zip/location, priceRating{...}) und
    scrapesmith (price, powerKw/powerHp, fuelType/transmission, sellerType
    "Dealer"/"Private Seller", sellerAddress, priceRating "GOOD_PRICE",
    searchPosition, makeId/modelId, vinHsn/vinTsn)."""
    lid = item.get("id") or item.get("listingId")
    if beschaedigte_verwerfen and beschaedigt(item):
        # Wunsch Ahmad 26.09.2026: nie Unfall-/beschaedigte Autos unter den guenstigsten —
        # die URL sagt schon dam=0, das hier ist die zweite Sicherung je Zeile.
        # NICHT bei der Entfernungspruefung (entfernung.py): dort heisst "Zeile da" nur "noch online".
        return None
    # Reparaturwelle 6 Nr. 85: robuster Preisparser ("19,990 EUR" ergab vorher 19); ein erkennbarer,
    # aber unplausibler Preis (< 100 oder > 5 Mio.) bleibt als Zeile erhalten und wird vom Zeilenfilter
    # mit Grund 'Preis unplausibel' verworfen (zaehlt, Alarm ab 3 je Lauf) — ohne jeden Preis: unbrauchbar
    preis_f = preis_parsen(item.get("priceGross"))
    if preis_f is None:
        p = item.get("price")
        if isinstance(p, dict):
            p = p.get("gross") or p.get("grossAmount") or p.get("amount")
        preis_f = preis_parsen(p)
    if not lid or preis_f is None:
        return None
    verwerfen_grund = None
    if preis_plausibel(preis_f):
        preis: Optional[int] = int(round(preis_f))
    else:
        preis = None
        verwerfen_grund = GRUND_PREIS_UNPLAUSIBEL
    seller = item.get("seller") if isinstance(item.get("seller"), dict) else {}
    seller_type = _seller_typ(seller.get("type") or item.get("sellerType"))
    # Nr. 91: Privatverkaeufer — keine exakten Koordinaten (auf ~1 km gerundet), keine seller_id
    privat = seller_type == "PRIVATE"
    lat = item.get("latitude") if item.get("latitude") is not None else item.get("sellerLatitude")
    lon = item.get("longitude") if item.get("longitude") is not None else item.get("sellerLongitude")
    if privat:
        lat, lon = _grob(lat), _grob(lon)
    if item.get("powerKw") not in (None, "", 0):
        lst = {"power_kw": _int(item.get("powerKw")), "power_ps": _int(item.get("powerHp")) or None}
    else:
        lst = _leistung(item.get("power"))
    adresse = str(item.get("sellerAddress") or "")
    plz = str(item.get("zip") or "") or (_PLZ.search(adresse).group(1) if _PLZ.search(adresse) else "")
    ort = item.get("location") or (adresse.split(plz, 1)[-1].strip(" ,") if plz and plz in adresse else None)
    rating = item.get("priceRating")
    bewertung = _preisbewertung(rating) if isinstance(rating, dict) else \
        _preisbewertung_text(rating, item.get("priceRatingLabel"), item.get("priceRatingVehicleOffset"))
    return {
        "source": QUELLE, "listing_id": str(lid), "url": item.get("url") or "",
        "make": item.get("make") or "", "model": item.get("model") or "",
        "variant": (item.get("variant") or item.get("subTitle") or "")[:200],
        "title": (item.get("title") or item.get("shortTitle") or "")[:200],
        "category": item.get("category") or item.get("bodyType") or "",
        "first_registration": item.get("firstRegistration") or "", "mileage_km": _int(item.get("mileageKm")),
        "power_kw": lst["power_kw"], "power_ps": lst["power_ps"],
        "fuel": item.get("fuel") or item.get("fuelType") or "", "gearbox": item.get("gearbox") or item.get("transmission") or "",
        "hu": item.get("hu") or item.get("inspectionStatus") or "",
        "color": item.get("color") or "", "doors": _int(item.get("doors") or item.get("doorCount")),
        "seats": _int(item.get("seats") or item.get("numSeats")) or None,
        "condition": item.get("condition") or "",
        "price_gross": preis, "price_net": _int(item.get("priceNet")), "vat": item.get("vat") if "vat" in item else item.get("priceType"),
        "price_rating": bewertung,
        "seller_type": seller_type,
        "seller_id": None if privat else (str(item.get("sellerId") or "") or None),
        "postal_code": plz or None, "city": ort or None,
        "country": item.get("country") or item.get("sellerCountry") or seller.get("country") or None,
        "latitude": lat, "longitude": lon,
        "mobile_created_at": _iso(item.get("createdAt")), "mobile_modified_at": _iso(item.get("modifiedAt")),
        "mobile_renewed_at": _iso(item.get("renewedAt")), "mobile_scraped_at": _iso(item.get("scrapedAt")),
        "input_context": (item.get("inputContext") if isinstance(item.get("inputContext"), str) else None),
        "image": item.get("image") or ((item.get("images") or [None])[0] if isinstance(item.get("images"), list) else None),
        "num_images": _int(item.get("numImages")),
        "make_id": str(item.get("makeId") or "") or None, "model_id": str(item.get("modelId") or "") or None,
        "hsn": item.get("vinHsn") or None, "tsn": item.get("vinTsn") or None,
        "search_position": _int(item.get("searchPosition")),
        "previous_owners": _int(item.get("numberOfPreviousOwners")) or None,
        "verwerfen_grund": verwerfen_grund,
    }


def _grob(w: Any) -> Optional[float]:
    """Nr. 91: Koordinate auf zwei Nachkommastellen (~1 km) — fuer Privatverkaeufer."""
    try:
        return round(float(w), 2) if w is not None else None
    except (TypeError, ValueError):
        return None


_MARKT_GESAMT_FELDER = ("totalResults", "resultCount", "totalCount", "numResults", "searchResultCount", "totalItems")


def markt_gesamt(items_roh: List[Dict[str, Any]]) -> Optional[int]:
    """Nr. 143: Gesamtzahl der Treffer der Suche, wenn der Scraper sie je Zeile mitliefert
    (totalResults/resultCount/...). None, wenn keine Zeile die Angabe traegt."""
    for it in items_roh or []:
        if not isinstance(it, dict):
            continue
        for k in _MARKT_GESAMT_FELDER:
            n = _int(it.get(k))
            if n is not None and n >= 0:
                return n
    return None


def listings_aus_items(items: List[Dict[str, Any]], *, beschaedigte_verwerfen: bool = True) -> List[Dict[str, Any]]:
    """In Suchreihenfolge (= Preis aufsteigend), Dubletten je ID entfernt.
    Liefert der Scraper eine Positionsnummer (searchPosition — mit Detailseiten
    kommen die Zeilen sonst in Abrufreihenfolge), gilt diese Reihenfolge.
    Nr. 85: Zeilen mit unplausiblem Preis bleiben (verwerfen_grund), der Zeilenfilter verwirft sie."""
    raus, gesehen = [], set()
    for it in items or []:
        l = listing_aus_item(it, beschaedigte_verwerfen=beschaedigte_verwerfen)
        if not l or l["listing_id"] in gesehen:
            continue
        gesehen.add(l["listing_id"])
        raus.append(l)
    if raus and all(l.get("search_position") for l in raus):
        raus.sort(key=lambda l: l["search_position"])
    return raus


def preise_aufsteigend(listings: List[Dict[str, Any]]) -> bool:
    p = [l["price_gross"] for l in listings if l.get("price_gross") is not None]
    return p == sorted(p)


# Reparaturwelle 5 (Review 26.09.2026 abends, Nr. 1): "sortiert" hiess bisher nur "monoton" —
# drei Zeilen 9.000/9.500/12.000 sind monoton, auch wenn der Scraper die Plaetze 1, 4 und 9
# geliefert hat. Der Top-N-Nachweis prueft die Positionsnummer (scrapesmith searchPosition)
# VOR jedem Zeilenfilter auf den Rohzeilen: mit 1 beginnend und fortlaufend.
SORTIERUNG_BEWIESEN = "bewiesen"            # searchPosition 1..n lueckenlos
SORTIERUNG_NUR_MONOTON = "nur_monoton"      # keine Positionsnummer (Ersatz-Scraper) — Statistik ja, Kennzeichen
SORTIERUNG_UNGUELTIG = "ungueltig"          # Positionsnummern vorhanden, aber mit Luecken -> data_invalid


def top_n_nachweis(items_roh: List[Dict[str, Any]]) -> Tuple[str, str]:
    """(status, grund) fuer die ROHEN Zeilen EINER Suche (vor Normalisierung, Dedupe und Filter).
    Luecken duerfen nur durch den Zeilenfilter entstehen — deshalb wird hier vor dem Filter
    geprueft. Beginnt die Nummerierung nicht bei 1 (Buendel eines Scrapers, der global zaehlt),
    gilt der Lauf als 'nur_monoton' (Daten bleiben, Top-N nicht bewiesen), nicht als ungueltig."""
    zeilen = [it for it in items_roh or [] if isinstance(it, dict)]
    if not zeilen:
        return SORTIERUNG_BEWIESEN, ""
    positionen = [_int(it.get("searchPosition")) for it in zeilen]
    vorhanden = [p for p in positionen if p is not None and p > 0]
    if not vorhanden:
        return SORTIERUNG_NUR_MONOTON, "keine searchPosition (Top-N nicht bewiesen)"
    if len(vorhanden) != len(zeilen):
        return SORTIERUNG_UNGUELTIG, f"searchPosition unvollstaendig ({len(vorhanden)} von {len(zeilen)})"
    eindeutig = sorted(set(vorhanden))
    if eindeutig != list(range(eindeutig[0], eindeutig[0] + len(eindeutig))):
        fehlend = sorted(set(range(eindeutig[0], eindeutig[-1] + 1)) - set(eindeutig))
        return SORTIERUNG_UNGUELTIG, f"searchPosition mit Luecken (fehlt: {', '.join(str(x) for x in fehlend[:5])})"
    if eindeutig[0] != 1:
        return SORTIERUNG_NUR_MONOTON, f"searchPosition beginnt bei {eindeutig[0]} (Top-N nicht bewiesen)"
    return SORTIERUNG_BEWIESEN, ""


# ---------------------------------------------------------------- Segment-Pruefung je Zeile
_JAHR = re.compile(r"(\d{4})")
KW_TOLERANZ = 3


def ez_jahr(listing: Dict[str, Any]) -> Optional[int]:
    """Erstzulassungsjahr aus '03/2020', '2020-03', '2020' — None ohne Angabe."""
    m = _JAHR.search(str(listing.get("first_registration") or ""))
    return int(m.group(1)) if m else None


def getriebe_passt(soll: Optional[str], ist: Optional[str]) -> bool:
    """DSG/S tronic stehen mal als Automatik, mal als Halbautomatik — beides passt zur Automatik."""
    if soll == ist:
        return True
    return soll == "AUTOMATIC_GEAR" and ist == "SEMIAUTOMATIC_GEAR"


# mobile.de-Karosseriecodes (Parameter c=) — dieselben wie im Vergleich (mobile_service.CATEGORY_LABELS)
KAROSSERIE_CODES = ("Limousine", "EstateCar", "OffRoad", "Cabrio", "SportsCar", "SmallCar", "Van")
# Verkaeuferart: der Auftrag sagt DEALER/FSBO (mobile.de st=), die Zeile DEALER/PRIVATE (normalisiert),
# der Vergleich "haendler"/"privat" — alles auf DEALER/PRIVATE abgebildet
VERKAEUFER_CODES = {"DEALER": "DEALER", "HAENDLER": "DEALER", "FSBO": "PRIVATE", "PRIVATE": "PRIVATE", "PRIVAT": "PRIVATE"}


def karosserie_code(text: Any) -> Optional[str]:
    """mobile.de-Karosseriecode aus Code oder Beschriftung (scrapesmith 'Estate car',
    'Saloon', 'SUV/Off-road', 'Small car', 'Sports car', 'Cabrio', 'Van'; sourabhbgp
    'EstateCar'; Formular 'Kombi', 'SUV', 'Coupe'). None = unbekannt (tolerant)."""
    n = re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode().lower())
    if not n:
        return None
    if any(t in n for t in ("kombi", "estate", "station", "touring", "variant")):
        return "EstateCar"
    if any(t in n for t in ("suv", "offroad", "gelande")):
        return "OffRoad"
    if any(t in n for t in ("cabrio", "convertible", "roadster")):
        return "Cabrio"
    if any(t in n for t in ("coupe", "sport")):
        return "SportsCar"
    if any(t in n for t in ("kleinwagen", "smallcar", "small")):
        return "SmallCar"
    if any(t in n for t in ("van", "minibus", "kleinbus", "bus")):
        return "Van"
    if any(t in n for t in ("limousine", "saloon", "sedan")):
        return "Limousine"
    return None


def _marke_norm(text: Any) -> str:
    from markt.katalog import _MARKEN_ALIAS, _norm
    n = _norm(text)
    return _MARKEN_ALIAS.get(n, n)


GRUND_IDENTITAET = "Fahrzeugidentitaet fehlt"


def modell_passt(listing: Dict[str, Any], modell: Dict[str, Any]) -> Tuple[bool, str]:
    """Review 26.09.2026 abends P3: Marke/Modell hart pruefen. Hat die Zeile
    make_id/model_id (scrapesmith makeId/modelId), muessen sie dem Suchauftrag gleichen;
    fehlen die IDs, werden die Namen streng normalisiert verglichen (Marke gleich,
    Modellname der Zeile beginnt mit dem Katalog-Modellnamen oder ist gleich).
    Reparaturwelle 6 Nr. 144: es muessen BEIDE IDs oder BEIDE Namen da sein — sonst ist
    die Identitaet des Fahrzeugs unbekannt und die Zeile wird verworfen (vorher tolerant).
    (ok, grund)."""
    from markt.katalog import _norm
    soll_make, soll_model = modell.get("make_id"), modell.get("model_id")
    ist_make, ist_model = listing.get("make_id"), listing.get("model_id")
    if ist_make and ist_model and soll_make and soll_model:
        return (str(ist_make) == str(soll_make) and str(ist_model) == str(soll_model)), "fremdes Modell"
    name_make, name_model = listing.get("make"), listing.get("model")
    if not name_make or not name_model:
        return False, GRUND_IDENTITAET
    if modell.get("make") and _marke_norm(name_make) != _marke_norm(modell["make"]):
        return False, "fremdes Modell"
    if modell.get("model"):
        soll = _norm(modell["model"])
        ist = _norm(name_model)
        if soll and ist != soll and not ist.startswith(soll):
            return False, "fremdes Modell"
    return True, ""


class FilterDefekt(RuntimeError):
    """Nr. 111/112: der Zeilenfilter kann Kraftstoff/Getriebe nicht pruefen (fahrzeug_codes nicht
    ladbar) — dann ist KEINE Zeile gueltig; der Worker schliesst den Lauf als 'data_invalid' ab
    und alarmiert (markt_filter_defekt), statt still alles durchzulassen."""


def _codes():
    try:
        from fahrzeug_codes import getriebe_code, kraftstoff_code
    except Exception as e:  # noqa: BLE001
        raise FilterDefekt(f"fahrzeug_codes nicht ladbar: {e}"[:200])
    return kraftstoff_code, getriebe_code


def passt_zum_segment(listing: Dict[str, Any], segment: Dict[str, Any], modell: Optional[Dict[str, Any]]) -> Tuple[bool, str]:
    """Review 26.09.2026 Nr. 2: der Scraper liefert gelegentlich Zeilen, die den
    Filter der Such-URL nicht erfuellen (mobile.de ignoriert dann still einen
    Parameter). Jede Zeile wird deshalb gegen ihr Segment und den Suchauftrag
    geprueft: Marke/Modell (P3), EZ-Jahr, km, kW (+-3 wie in abfrage), Kraftstoff,
    Getriebe, Karosserie (P7). (ok, grund); grund leer bei ok.

    Review 26.09.2026 abends P2 — Pflichtfelder einer gueltigen Statistik-Zeile:
    listing_id, price_gross, EZ-Jahr, km; zusaetzlich Kraftstoff, Getriebe, kW, wenn der
    Suchauftrag sie verlangt. Fehlt eines -> verworfen mit Grund 'fehlend: <feld>'
    (zaehlt zu verworfen_filter).

    Reparaturwelle 6 Nr. 110/125: verlangt der Auftrag Kraftstoff, Getriebe oder Karosserie,
    muss der Zeilenwert ERKENNBAR sein ('unbekannt: fuel' statt still durchlassen);
    Nr. 85: unplausibler Preis -> 'Preis unplausibel'; Nr. 144: Identitaet Pflicht."""
    modell = modell or {}
    if not listing.get("listing_id"):
        return False, "fehlend: listing_id"
    if listing.get("verwerfen_grund"):
        return False, str(listing["verwerfen_grund"])
    if not listing.get("price_gross"):
        return False, "fehlend: price_gross"
    ok, grund = modell_passt(listing, modell)
    if not ok:
        return False, grund
    von, bis = segment.get("year_from"), segment.get("year_to")
    jahr = ez_jahr(listing)
    if jahr is None:
        return False, "fehlend: ez"
    if von and jahr < int(von):
        return False, f"ez {jahr} < {von}"
    if bis and jahr > int(bis):
        return False, f"ez {jahr} > {bis}"
    km = listing.get("mileage_km")
    if km is None:
        return False, "fehlend: km"
    mn, mx = segment.get("min_km"), segment.get("max_km")
    if mn is not None and int(km) < int(mn):
        return False, f"km {km} < {mn}"
    if mx is not None and int(km) > int(mx):
        return False, f"km {km} > {mx}"
    kw = listing.get("power_kw")
    kw_von, kw_bis = modell.get("power_kw_min"), modell.get("power_kw_max")
    if (kw_von or kw_bis) and not kw:
        return False, "fehlend: power_kw"
    if kw:
        if kw_von and int(kw) < int(kw_von) - KW_TOLERANZ:
            return False, f"kw {kw} < {kw_von}"
        if kw_bis and int(kw) > int(kw_bis) + KW_TOLERANZ:
            return False, f"kw {kw} > {kw_bis}"
    if modell.get("fuel") and not listing.get("fuel"):
        return False, "fehlend: fuel"
    if modell.get("gearbox") and not listing.get("gearbox"):
        return False, "fehlend: gearbox"
    # Reparaturwelle 5 Nr. 7: Verkaeuferart und Land nachvalidieren, wenn der Auftrag sie setzt und
    # die Zeile sie traegt (fehlt die Angabe in der Zeile: tolerant — kein Pflichtfeld der Statistik).
    # PLZ/Radius werden NICHT geprueft: die ersten zwei PLZ-Stellen sagen nichts Verlaessliches
    # ueber die Entfernung (Radius 50 km um 30159 reicht in 31xxx und 38xxx), und Geodaten je
    # PLZ gibt es hier nicht — die URL (zipr=) bleibt die einzige Radiusfilterung.
    if modell.get("seller_type") and listing.get("seller_type"):
        soll = VERKAEUFER_CODES.get(str(modell["seller_type"]).upper(), str(modell["seller_type"]).upper())
        ist = VERKAEUFER_CODES.get(str(listing["seller_type"]).upper(), str(listing["seller_type"]).upper())
        if ist != soll:
            return False, f"verkaeufer {ist} != {soll}"
    if modell.get("country") and listing.get("country"):
        if str(listing["country"]).strip().upper()[:2] != str(modell["country"]).strip().upper()[:2]:
            return False, f"land {str(listing['country']).upper()[:2]} != {str(modell['country']).upper()[:2]}"
    if modell.get("fuel") or modell.get("gearbox"):
        kraftstoff_code, getriebe_code = _codes()
        if modell.get("fuel"):
            code = kraftstoff_code(listing.get("fuel"))
            if not code:
                return False, "unbekannt: fuel"
            if code != modell["fuel"]:
                return False, f"kraftstoff {code} != {modell['fuel']}"
        if modell.get("gearbox"):
            code = getriebe_code(listing.get("gearbox"))
            if not code:
                return False, "unbekannt: gearbox"
            if not getriebe_passt(modell["gearbox"], code):
                return False, f"getriebe {code} != {modell['gearbox']}"
    if modell.get("body"):
        # Nr. 125/126: setzt der Auftrag eine Karosserie, muss die Zeile eine erkennbare tragen
        if not listing.get("category"):
            return False, "fehlend: karosserie"
        code = karosserie_code(listing.get("category"))
        if not code:
            return False, "unbekannt: karosserie"
        if code != modell["body"]:
            return False, f"karosserie {code} != {modell['body']}"
    return True, ""
