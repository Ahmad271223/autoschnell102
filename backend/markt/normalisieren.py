# -*- coding: utf-8 -*-
"""Scraper-Zeile (Suchergebnis) -> unser Listing. Nur fahrzeug-/preisbezogene
Felder plus seller_id/seller_type/PLZ/Ort — keine Telefonnummer, kein
Verkaeufername (Datenschutz, Auftrag Punkt 33)."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from markt.konfig import QUELLE

_KW = re.compile(r"(\d{2,4})\s*kW", re.I)
_PS = re.compile(r"\((\d{2,4})\s*PS\)", re.I)
_EUR = re.compile(r"\d[\d.]*")


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


def _iso(w: Any) -> Optional[str]:
    s = str(w or "").strip()
    return s if s and s[:4].isdigit() else None


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
        if w is True or (isinstance(w, str) and w.strip().lower() in ("true", "yes", "ja", "1")):
            return True
    zustand = str(item.get("condition") or item.get("damageCondition") or "").lower()
    return "unfall" in zustand or "damaged" in zustand or "accident" in zustand


def listing_aus_item(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """None, wenn keine ID oder kein Bruttopreis (dann unbrauchbar).
    Versteht beide Scraper: sourabhbgp (priceGross, power "135 kW (184 PS)",
    fuel/gearbox, seller{type}, zip/location, priceRating{...}) und
    scrapesmith (price, powerKw/powerHp, fuelType/transmission, sellerType
    "Dealer"/"Private Seller", sellerAddress, priceRating "GOOD_PRICE",
    searchPosition, makeId/modelId, vinHsn/vinTsn)."""
    lid = item.get("id") or item.get("listingId")
    if beschaedigt(item):
        # Wunsch Ahmad 26.09.2026: nie Unfall-/beschaedigte Autos unter den guenstigsten —
        # die URL sagt schon dam=0, das hier ist die zweite Sicherung je Zeile.
        return None
    preis = _int(item.get("priceGross"))
    if not preis:
        p = item.get("price")
        if isinstance(p, dict):
            p = p.get("gross") or p.get("grossAmount") or p.get("amount")
        preis = _int(p)
    if not lid or not preis or preis <= 0:
        return None
    seller = item.get("seller") if isinstance(item.get("seller"), dict) else {}
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
        "seller_type": _seller_typ(seller.get("type") or item.get("sellerType")),
        "seller_id": str(item.get("sellerId") or "") or None,
        "postal_code": plz or None, "city": ort or None,
        "country": item.get("country") or item.get("sellerCountry") or seller.get("country") or None,
        "latitude": item.get("latitude") if item.get("latitude") is not None else item.get("sellerLatitude"),
        "longitude": item.get("longitude") if item.get("longitude") is not None else item.get("sellerLongitude"),
        "mobile_created_at": _iso(item.get("createdAt")), "mobile_modified_at": _iso(item.get("modifiedAt")),
        "mobile_renewed_at": _iso(item.get("renewedAt")), "mobile_scraped_at": _iso(item.get("scrapedAt")),
        "input_context": (item.get("inputContext") if isinstance(item.get("inputContext"), str) else None),
        "image": item.get("image") or ((item.get("images") or [None])[0] if isinstance(item.get("images"), list) else None),
        "num_images": _int(item.get("numImages")),
        "make_id": str(item.get("makeId") or "") or None, "model_id": str(item.get("modelId") or "") or None,
        "hsn": item.get("vinHsn") or None, "tsn": item.get("vinTsn") or None,
        "search_position": _int(item.get("searchPosition")),
        "previous_owners": _int(item.get("numberOfPreviousOwners")) or None,
    }


def listings_aus_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """In Suchreihenfolge (= Preis aufsteigend), Dubletten je ID entfernt.
    Liefert der Scraper eine Positionsnummer (searchPosition — mit Detailseiten
    kommen die Zeilen sonst in Abrufreihenfolge), gilt diese Reihenfolge."""
    raus, gesehen = [], set()
    for it in items or []:
        l = listing_aus_item(it)
        if not l or l["listing_id"] in gesehen:
            continue
        gesehen.add(l["listing_id"])
        raus.append(l)
    if raus and all(l.get("search_position") for l in raus):
        raus.sort(key=lambda l: l["search_position"])
    return raus


def preise_aufsteigend(listings: List[Dict[str, Any]]) -> bool:
    p = [l["price_gross"] for l in listings]
    return p == sorted(p)
