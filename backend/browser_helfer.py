# -*- coding: utf-8 -*-
"""Browser-Helfer (04.10.2026, Wunsch Ahmad) — Auswertung auf dem Server.

Die AutoSchnell-Erweiterung fuer Chrome/Edge (browser-extension/) schickt beim Oeffnen
eines Inserats auf mobile.de, AutoScout24 oder Kleinanzeigen die Seite aus dem Browser
des Suchers (gzip, base64) — und spaeter die Vergleichsseite, die sie selbst geoeffnet
hat. Alles Wissen ueber den Seitenaufbau der Portale liegt HIER: baut ein Portal um,
reicht ein Server-Update, die Erweiterung bleibt ein Bote.

  seite_entpacken(b64)                 -> HTML (mit Groessen-Grenze, kein Zip-Bombe)
  inserat_auslesen(identity, url, html) -> (fahrzeug, portal_bewertung)
  treffer_auslesen(url, html)          -> {"portal", "gesamt", "sortierung", "treffer": [...]}
  marktlage(preis, eigene_id, liste)   -> Platz des Inserats unter den Vergleichsangeboten + Ampel

mobile.de legt das Inserat als Next.js-Datenstrom in die Seite — im selben Aufbau, den der
Apify-Actor liefert; deshalb wertet mobile_service._parse_apify_item es aus (eine Stelle
fuer beide Wege). AutoScout24 legt es in __NEXT_DATA__ ab, Kleinanzeigen wertet der
vorhandene HTML-Parser aus.

Kein FastAPI, keine Datenbank — die Tests nutzen das Modul direkt.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import statistics
import zlib
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, parse_qsl, urlparse

#: Grenzen fuer eine eingeschickte Seite (mobile.de-Inserat ~1 MB, gepackt ~200 KB)
MAX_GEPACKT = 3 * 1024 * 1024
#: Paket 2 (05.10.2026): 4 statt 8 MB — echte Inserats-/Ergebnisseiten liegen bei 1–2 MB; vier gleichzeitige
#: Auswertungen halten so hoechstens ~64 MB Text im Speicher.
#: ACHTUNG (Befund 06.10.2026): Kleinanzeigen im neuen Astro-Format (~28 <astro-island>, ~12 davon mit den kompletten
#: Inseratsdaten in props) hat bei 25 Fotos 6,2 MB — die Grenze gilt deshalb erst NACH _astro_props_kuerzen (2,1 MB).
#: Nicht ohne Messung echter Seiten senken.
MAX_HTML = 4 * 1024 * 1024
#: Befund 06.10.2026 ("Die Seite ist zu groß" bei Kleinanzeigen): so viel darf eine Seite ENTPACKT haben, bevor die
#: Wiederholungen entfernt sind (_astro_props_kuerzen) — danach gilt wieder MAX_HTML. Schutz gegen Zip-Bomben bleibt.
MAX_ROH = 16 * 1024 * 1024

#: Pruefung 05.10.2026 (Paket 1): was aus einer Browserseite gespeichert wird, gilt 24 h fuer ALLE Konten —
#: deshalb feste Grenzen fuer jeden Wert (fahrzeug_bereinigen), bevor irgendetwas gespeichert wird.
MAX_BESCHREIBUNG_HTML = 200_000
MAX_BESCHREIBUNG = 20_000
MAX_TEXT = 500
MAX_LISTE = 400
MAX_BILDER = 60
MAX_HREF = 2000
#: Pruefung 08.10.2026: eine echte Ergebnisseite hat je Angebot ein bis drei Inserat-Links (20–50 Angebote) — eine
#: gebaute Seite mit Tausenden hielt einen Prozess sekundenlang fest (mobile_karten)
MAX_KARTEN_LINKS = 600


def _keine_konstante(name):
    """NaN / Infinity sind kein JSON (JSON.stringify schreibt sie nie) — eine Seite damit ist nicht echt.
    Vorher kam ein Preis NaN durch die Pruefung "Preis vorhanden", wurde gespeichert und endete als 500."""
    raise ValueError(f"kein JSON: {name}")


_DEC = json.JSONDecoder(parse_constant=_keine_konstante)


class SeiteUngueltig(ValueError):
    """Die eingeschickte Seite ist keine lesbare Inserats- bzw. Ergebnisseite (HTTP 422)."""


# ---------------------------------------------------------------- Inserat fuer den Kaufvertrag merken
#: Browser-Helfer-Snapshots sind eine nicht vertrauenswuerdige Eingabequelle.
#: Sicherheits-/Vertragsregel 08.10.2026: ein Snapshot darf nur von DEM Konto
#: wiederverwendet werden, dessen Browser ihn geliefert hat. Nie konto- oder
#: firmenuebergreifend: manipulierte oder veraltete Browserdaten duerfen
#: keinen fremden Vergleich oder Kaufvertrag speisen.
SAMMLUNG_INSERATE = "werkzeug_inserate"
INSERAT_STUNDEN = 24
#: Pruefung 08.10.2026 (Datenschutz): auch der Name des Ansprechpartners eines Haendlers (eine Person) bleibt in der
#: eigenen Firma — und bei PRIVATEN Verkaeufern Name und Kleinanzeigen-Name (personenbezogen). Haendlername, PLZ und
#: Ort bleiben fuer alle (Firmenangaben, Standort fuer Vergleich und Abholung).
KONTAKT_FELDER = ("seller_phone", "seller_email", "seller_address", "seller_ansprechpartner")
PRIVAT_FELDER = ("seller_name", "seller_alias")


def _pruefsumme(daten: dict) -> str:
    return hashlib.sha256(json.dumps(daten, sort_keys=True, default=str, ensure_ascii=False)
                          .encode("utf-8")).hexdigest()


async def inserat_merken(db, identity: dict, url: str, fahrzeug: dict, user: dict) -> None:
    jetzt = datetime.now(timezone.utc)
    daten = {k: v for k, v in fahrzeug.items() if not str(k).startswith("_")}
    pruef = _pruefsumme(daten)
    zeit = {"gelesen_am": jetzt, "ablauf": jetzt + timedelta(hours=INSERAT_STUNDEN)}
    filt = {"cache_key": identity["cache_key"], "user_id": user["id"]}
    # Pruefung 08.10.2026 (Last): dieselbe Seite noch einmal (Neuladen, naechster Vorgang zum selben Auto) — nur die
    # Zeit auffrischen statt die ganze Lesung (10–20 KB) neu zu schreiben (jeder Schreibzugriff geht ins Oplog)
    r = await db[SAMMLUNG_INSERATE].update_one(
        {**filt, "pruefsumme": pruef, "url": url, "dealer_id": user.get("dealer_id") or ""}, {"$set": zeit})
    if r.matched_count:
        return
    await db[SAMMLUNG_INSERATE].update_one(
        filt,
        {"$set": {"dealer_id": user.get("dealer_id") or "", "source": identity["source"],
                  "item_id": identity["item_id"], "url": url, "data": daten, "pruefsumme": pruef, **zeit}},
        upsert=True)


async def inserat_lesen(db, cache_key: str, user_id: str,
                        dealer_id: Optional[str] = None) -> Optional[Tuple[dict, datetime, dict]]:
    """Browser-Helfer-Daten ausschliesslich fuer dasselbe Konto lesen.

    dealer_id bleibt als Parameter fuer bestehende Aufrufer, wird aber
    bewusst NICHT zum Teilen von Browser-Snapshots benutzt. Ein Snapshot ist
    eine nicht vertrauenswuerdige Browser-Eingabe und darf weder einen
    Kollegen noch eine fremde Firma mit Fahrzeug-/Vertragsdaten versorgen.
    """
    if not cache_key or not user_id:
        return None
    projektion = {"_id": 0, "data": 1, "gelesen_am": 1, "user_id": 1, "dealer_id": 1}
    d = await db[SAMMLUNG_INSERATE].find_one(
        {"cache_key": cache_key, "user_id": user_id,
         "ablauf": {"$gt": datetime.now(timezone.utc)},
         "data": {"$type": "object"}},
        projektion)
    if d is None or not isinstance(d.get("data"), dict):
        return None
    return dict(d["data"]), d.get("gelesen_am"), {
        "user_id": d.get("user_id") or "",
        "dealer_id": d.get("dealer_id") or "",
    }


# ---------------------------------------------------------------- Seite entpacken
# Befund 06.10.2026: Kleinanzeigen zeigt Inserate im Browser im neuen Seitenformat ("Astro"). Jede Komponente
# (<astro-island props="…">) traegt die KOMPLETTEN Inseratsdaten samt aller Bildadressen — bei 25 Fotos ~430 KB,
# rund zwoelfmal auf derselben Seite: 6,2 MB. Gelesen wird ohnehin nur der sichtbare Teil (viewad-*, Tabelle) und die
# Bildadressen; dafuer reicht EINE Kopie jedes Werts (6,2 -> 2,1 MB).
_GROSSE_PROPS = re.compile(r'(\sprops=")([^"]{10000,})(")')


def _astro_props_kuerzen(html: str) -> str:
    """Wiederholte grosse props-Werte leeren (der erste bleibt). Reicht das nicht fuer MAX_HTML, bleibt nur der
    erste grosse Wert ueberhaupt (darin stehen die Bildadressen der Galerie)."""
    if 'props="' not in html:
        return html
    gesehen = set()

    def _doppelt(m):
        merkmal = (len(m.group(2)), hash(m.group(2)))
        if merkmal in gesehen:
            return m.group(1) + m.group(3)
        gesehen.add(merkmal)
        return m.group(0)
    kurz = _GROSSE_PROPS.sub(_doppelt, html)
    if len(kurz) > MAX_HTML:
        erster = []

        def _nur_erster(m):
            if erster:
                return m.group(1) + m.group(3)
            erster.append(1)
            return m.group(0)
        kurz = _GROSSE_PROPS.sub(_nur_erster, kurz)
    return kurz


def seite_entpacken(b64: str) -> str:
    """base64(gzip(HTML)) -> HTML. Groessen-Grenze VOR dem vollstaendigen Entpacken (Zip-Bombe): hoechstens MAX_ROH
    entpackt, nach dem Kuerzen wiederholter Seitendaten (_astro_props_kuerzen) hoechstens MAX_HTML."""
    try:
        roh = base64.b64decode(str(b64 or ""), validate=True)
    except (ValueError, TypeError):
        raise SeiteUngueltig("Die Seite kam nicht lesbar an.")
    if not roh or len(roh) > MAX_GEPACKT:
        raise SeiteUngueltig("Die Seite ist leer oder zu groß.")
    entpacker = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        daten = entpacker.decompress(roh, MAX_ROH + 1)
    except zlib.error:
        raise SeiteUngueltig("Die Seite kam nicht lesbar an.")
    if len(daten) > MAX_ROH or entpacker.unconsumed_tail:
        raise SeiteUngueltig("Die Seite ist zu groß.")
    html = daten.decode("utf-8", errors="replace")
    del daten
    if len(html) > MAX_HTML:
        html = _astro_props_kuerzen(html)
    if len(html) > MAX_HTML:
        raise SeiteUngueltig("Die Seite ist zu groß.")
    return html


# ---------------------------------------------------------------- Next.js-Daten
_FLIGHT_START = "self.__next_f.push([1,"
_FLIGHT_FEHLVERSUCHE = 50


def next_flight_text(html: str) -> str:
    """Den Next.js-Datenstrom (App-Router) einer Seite zusammensetzen: jedes
    self.__next_f.push([1,"…"]) traegt ein JSON-Textstueck."""
    teile, pos, fehlversuche = [], 0, 0
    while True:
        i = html.find(_FLIGHT_START, pos)
        if i < 0:
            break
        j = i + len(_FLIGHT_START)
        try:
            wert, ende = _DEC.raw_decode(html, j)
        except ValueError:
            # Pruefung 06.10.2026 (Paket 1): jeder Fehlversuch kostet Zeit im Verhaeltnis zur Seitenlaenge (die
            # Fehlermeldung zaehlt die Zeilen bis zur Stelle) — eine Seite aus lauter kaputten Stuecken lief
            # quadratisch (120 KB = 0,4 s, 8 MB = Minuten). Echte Seiten haben keine oder kaum kaputte Stuecke.
            fehlversuche += 1
            if fehlversuche > _FLIGHT_FEHLVERSUCHE:
                break
            pos = j
            continue
        if isinstance(wert, str):
            teile.append(wert)
        pos = ende
    return "".join(teile)


def json_objekt_nach(text: str, marke: str) -> Optional[dict]:
    """Das JSON-Objekt, das mit dem ersten "{" in `marke` beginnt — z.B. marke
    '"listing":{"attributes":' liefert das ganze listing-Objekt (nicht irgendein
    frueheres "listing":null)."""
    if "{" not in marke:
        return None
    i = text.find(marke)
    if i < 0:
        return None
    j = i + marke.index("{")
    try:
        wert, _ = _DEC.raw_decode(text, j)
    except ValueError:
        return None
    return wert if isinstance(wert, dict) else None


def flight_textblock(text: str, verweis) -> Optional[str]:
    """Lange Texte stehen im Datenstrom als eigener Block: "$46" verweist auf
    "46:T<Laenge hex>,<Text>" (Laenge in UTF-8-Bytes)."""
    if not isinstance(verweis, str):
        return None
    if not verweis.startswith("$"):
        return verweis
    kennung = verweis[1:]
    if not re.fullmatch(r"[0-9a-fA-F]{1,8}", kennung):
        return None
    marke = f"{kennung}:T"
    i = text.find("\n" + marke)
    if i >= 0:
        i += 1
    elif text.startswith(marke):
        i = 0
    else:
        return None
    komma = text.find(",", i)
    try:
        laenge = int(text[i + len(marke):komma], 16)
    except ValueError:
        return None
    if laenge <= 0 or laenge > MAX_HTML:
        return None
    start = komma + 1
    # Jedes Zeichen hat mindestens ein Byte: `laenge` Zeichen reichen immer, dann auf Bytes kuerzen.
    return text[start:start + laenge].encode("utf-8")[:laenge].decode("utf-8", errors="ignore")


def next_data(html: str) -> Optional[dict]:
    """<script id="__NEXT_DATA__" type="application/json">…</script> (Pages-Router, AutoScout24)."""
    # [^<>] statt [^>]: sonst laeuft die Suche auf einer Seite aus lauter "<script " quadratisch
    # (Pruefung 05.10.2026, gemessen 64 KB = 0,9 s, jede Verdopplung x4 — und re gibt die Sperre nicht frei)
    m = re.search(r'<script[^<>]*id="__NEXT_DATA__"[^<>]*>', html)
    if not m:
        return None
    try:
        wert, _ = _DEC.raw_decode(html, m.end())
    except ValueError:
        return None
    return wert if isinstance(wert, dict) else None


def _euro(text) -> Optional[int]:
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        return int(text)
    ziffern = re.sub(r"\D", "", str(text or ""))
    return int(ziffern) if ziffern and len(ziffern) <= 9 else None


# ---------------------------------------------------------------- Inserat: mobile.de
_MOBILE_STUFEN = ("sehr guter Preis", "guter Preis", "fairer Preis", "erhöhter Preis", "hoher Preis")


def mobile_bewertung(listing: dict) -> Optional[dict]:
    """mobile.de-Preisbewertung des Inserats. Die sechs Grenzen begrenzen die fuenf Stufen
    (sehr gut | gut | fair | erhoeht | hoch) — am 04.10.2026 an zwei Inseraten geprueft
    (16.500 € "Fairer Preis" zwischen Grenze 3 und 4, 33.550 € "Guter Preis" zwischen 2 und 3)."""
    pr = listing.get("priceRating") if isinstance(listing.get("priceRating"), dict) else None
    if not pr or not pr.get("rating"):
        return None
    grenzen = [_euro(x) for x in pr.get("thresholdLabels") or []]
    gut = len(grenzen) == 6 and all(isinstance(g, int) for g in grenzen) and grenzen == sorted(grenzen)
    return {
        "portal": "mobile.de",
        "stufe": str(pr.get("ratingLabel") or "").strip() or None,
        "code": str(pr.get("rating") or ""),
        "fair_von": grenzen[2] if gut else None,
        "fair_bis": grenzen[3] if gut else None,
        "stufen": ([{"text": t, "von": grenzen[i], "bis": grenzen[i + 1]} for i, t in enumerate(_MOBILE_STUFEN)]
                   if gut else []),
    }


def mobile_inserat(html: str, item_id: str, url: str) -> Tuple[dict, Optional[dict]]:
    import mobile_service as ms
    text = next_flight_text(html)
    listing = json_objekt_nach(text, '"listing":{"attributes":')
    if not listing:
        raise SeiteUngueltig("Auf der Seite stehen keine Inseratsdaten (Prüfseite von mobile.de oder "
                             "Inserat nicht mehr online).")
    if str(listing.get("id") or "") != str(item_id):
        raise SeiteUngueltig("Die Seite gehört zu einem anderen Inserat.")
    listing = dict(listing)
    listing["htmlDescription"] = (flight_textblock(text, listing.get("htmlDescription")) or "")[:MAX_BESCHREIBUNG_HTML]
    fahrzeug = ms._parse_apify_item(listing, str(item_id), url)
    return fahrzeug, mobile_bewertung(listing)


# ---------------------------------------------------------------- Inserat: AutoScout24
def autoscout_bewertung(details: dict) -> Optional[dict]:
    pub = ((details.get("prices") or {}).get("public") or {}) if isinstance(details.get("prices"), dict) else {}
    mitte = _euro(pub.get("median"))
    if not mitte:
        return None
    return {"portal": "AutoScout24", "stufe": None, "code": "", "mitte": mitte}


def autoscout_inserat(html: str, item_id: str, url: str) -> Tuple[dict, Optional[dict]]:
    from ausstattung_de import uebersetzen
    from fahrzeug_codes import getriebe_code, kraftstoff_code, tueren_text
    from mobile_service import _apify_html_zu_text, telefon_aus
    nd = next_data(html) or {}
    details = ((nd.get("props") or {}).get("pageProps") or {}).get("listingDetails")
    if not isinstance(details, dict) or not isinstance(details.get("vehicle"), dict):
        raise SeiteUngueltig("Auf der Seite stehen keine Inseratsdaten (Prüfseite von AutoScout24 oder "
                             "Inserat nicht mehr online).")
    if str(details.get("id") or "").lower() != str(item_id).lower():
        raise SeiteUngueltig("Die Seite gehört zu einem anderen Inserat.")
    v = details["vehicle"]
    preise = ((details.get("prices") or {}).get("public") or {})
    kraftstoff = ((v.get("fuelCategory") or {}).get("formatted") or (v.get("primaryFuel") or {}).get("formatted")
                  or "") if isinstance(v.get("fuelCategory"), dict) else ""
    getriebe = str(v.get("transmissionType") or "")
    verkaeufer = details.get("seller") if isinstance(details.get("seller"), dict) else {}
    ort = details.get("location") if isinstance(details.get("location"), dict) else {}
    haendler = verkaeufer.get("isDealer") is True or str(verkaeufer.get("type") or "").lower() == "dealer"
    name = (verkaeufer.get("companyName") if haendler else verkaeufer.get("contactName")) or None
    ansprech = verkaeufer.get("contactName") if haendler and verkaeufer.get("contactName") != name else None
    telefone = [{"number": p.get("formattedNumber") or p.get("callTo"), "type": p.get("phoneType")}
                for p in verkaeufer.get("phones") or [] if isinstance(p, dict)]
    merkmale, gesehen = [], set()
    for gruppe in (v.get("equipment") or {}).values() if isinstance(v.get("equipment"), dict) else []:
        for e in gruppe if isinstance(gruppe, list) else []:
            t = uebersetzen(e.get("id") if isinstance(e, dict) else e)
            if t and t.casefold() not in gesehen and len(merkmale) < 400:
                gesehen.add(t.casefold())
                merkmale.append(t)
    bilder = [u for u in details.get("images") or [] if isinstance(u, str) and u.startswith("https://")][:60]
    unfall = v.get("hadAccident")
    halter = v.get("noOfPreviousOwners")
    hu = v.get("nextVehicleSafetyInspection")
    fahrzeug = {
        "mobile_ad_id": str(item_id),
        "detail_url": url.split("?")[0],
        "make": str(v.get("make") or "").upper(),
        "make_label": v.get("make") or "",
        "model": v.get("model") or v.get("modelGroup") or "",
        "model_label": v.get("model") or v.get("modelGroup") or "",
        "model_description": " ".join(x for x in (v.get("make"), v.get("model"), v.get("modelVersionInput")) if x),
        "category": v.get("bodyType") or "",
        "category_label": v.get("bodyType") or "",
        "first_registration": v.get("firstRegistrationDate") or None,
        "neufahrzeug": details.get("isNew") is True,
        "mileage": v.get("mileageInKmRaw") if isinstance(v.get("mileageInKmRaw"), int) else None,
        "fuel": kraftstoff_code(kraftstoff) or kraftstoff.upper(),
        "fuel_label": kraftstoff,
        "gearbox": getriebe_code(getriebe) or getriebe.upper(),
        "gearbox_label": getriebe,
        "power_kw": v.get("rawPowerInKw") if isinstance(v.get("rawPowerInKw"), int) else None,
        "power_ps": v.get("rawPowerInHp") if isinstance(v.get("rawPowerInHp"), int) else None,
        "displacement": v.get("rawDisplacementInCCM") if isinstance(v.get("rawDisplacementInCCM"), int) else None,
        "doors": tueren_text(v.get("numberOfDoors")),
        "seats": v.get("numberOfSeats") if isinstance(v.get("numberOfSeats"), int) else None,
        "color": v.get("bodyColor") or None,
        "vin": None,
        "license_plate": None,
        "hu": str(hu).strip() if hu else None,
        "previous_owners": str(halter) if isinstance(halter, int) else None,
        "accident_damaged": unfall if isinstance(unfall, bool) else None,
        "zustand_portal": ("AutoScout24: Unfallfahrzeug" if unfall is True
                           else "AutoScout24: unfallfrei" if unfall is False else None),
        "roadworthy": None,
        "features": merkmale,
        "description": _apify_html_zu_text(str(details.get("description") or "")[:MAX_BESCHREIBUNG_HTML]),
        "list_price": float(preise["priceRaw"]) if isinstance(preise.get("priceRaw"), (int, float)) else None,
        "currency": "EUR",
        "price_type": "NEGOTIABLE" if preise.get("negotiable") is True else None,
        "price_negotiable": preise.get("negotiable") is True,
        "mwst_ausweisbar": preise.get("taxDeductible") if isinstance(preise.get("taxDeductible"), bool) else None,
        "seller_name": name,
        "seller_ansprechpartner": ansprech,
        "seller_type": "haendler" if haendler else "privat",
        "seller_address": (ort.get("street") or None) if haendler else None,
        "seller_zip": ort.get("zip") or None,
        "seller_city": ort.get("city") or None,
        "seller_phone": telefon_aus(telefone),
        "seller_email": "",
        "image_urls": bilder,
        "images": list(bilder),
        "image_count": len(bilder),
    }
    return fahrzeug, autoscout_bewertung(details)


# ---------------------------------------------------------------- Inserat: Kleinanzeigen
def kleinanzeigen_inserat(html: str, item_id: str, url: str) -> Tuple[dict, Optional[dict]]:
    from kleinanzeigen_service import looks_like_kleinanzeigen_listing, parse_kleinanzeigen_html
    if not looks_like_kleinanzeigen_listing(html, url=url):
        raise SeiteUngueltig("Die Seite sieht nicht nach einer Kleinanzeigen-Fahrzeugseite aus.")
    fahrzeug = parse_kleinanzeigen_html(url, html)
    fahrzeug["mobile_ad_id"] = fahrzeug.get("kleinanzeigen_id") or item_id
    fahrzeug.setdefault("kleinanzeigen_id", item_id)
    return fahrzeug, None


_LESER = {"mobile": mobile_inserat, "autoscout24": autoscout_inserat, "kleinanzeigen": kleinanzeigen_inserat}


def inserat_auslesen(identity: dict, url: str, html: str) -> Tuple[dict, Optional[dict]]:
    """(fahrzeug im Schema von /mobile/compare, Preisbewertung des Portals oder None).
    Ohne Marke ist es kein verwertbares Inserat. Wunsch Ahmad 06.10.2026: OHNE Preis (Kleinanzeigen "VB",
    "Preis auf Anfrage") schon — vorher "Auf der Seite steht kein Preis." und man kam gar nicht zum Kaufvertrag;
    den Kaufpreis traegt der Sucher im Vertrag ohnehin selbst ein. Die Ampel sagt dann "Kein Preis im Inserat"."""
    fahrzeug, bewertung = _LESER[identity["source"]](html, identity["item_id"], url)
    fahrzeug = fahrzeug_bereinigen(fahrzeug)
    if not (fahrzeug.get("make_label") or fahrzeug.get("make_id")):
        raise SeiteUngueltig("Auf der Seite steht keine erkennbare Fahrzeugmarke.")
    return fahrzeug, bewertung


#: Zahlenfelder mit plausiblem Bereich — ausserhalb = nicht gelesen (None). Absurde Werte (10^20 km) liessen die
#: Umrechnung ueberlaufen (500), ein Preis NaN bestand die Pruefung "Preis vorhanden".
_ZAHL_GRENZEN = {"list_price": (1, 100_000_000), "mileage": (0, 3_000_000), "power_kw": (1, 2000),
                 "power_ps": (1, 3000), "displacement": (1, 20_000), "seats": (1, 99)}
#: Textfelder, mit denen der Server weiterrechnet (Links, Hinweise, Kurzfassung): immer Text oder None
_TEXT_FELDER = frozenset({
    "mobile_ad_id", "kleinanzeigen_id", "detail_url", "make", "make_label", "model", "model_label",
    "model_description", "title", "category", "category_label", "first_registration", "fuel", "fuel_label",
    "gearbox", "gearbox_label", "description", "seller_name", "seller_type"})
_BILD_FELDER = frozenset({"image_urls", "images"})


def _wert_bereinigen(wert, in_liste: bool = False):
    """Nur einfache Werte in festen Grenzen: Text gekuerzt, Zahlen endlich, Listen flach (nur einfache Werte).
    Alle drei Leser liefern flache Daten — ein Objekt oder eine Liste in einer Liste ist nie echt (None)."""
    if wert is None or isinstance(wert, bool):
        return wert
    if isinstance(wert, int):
        return wert if abs(wert) <= 10 ** 12 else None
    if isinstance(wert, float):
        return wert if math.isfinite(wert) and abs(wert) <= 1e12 else None
    if isinstance(wert, str):
        return wert[:MAX_TEXT]
    if isinstance(wert, (list, tuple)) and not in_liste:
        roh = [_wert_bereinigen(w, True) for w in list(wert)[:MAX_LISTE]]
        return [w for w in roh if w is not None]
    return None


def fahrzeug_bereinigen(fahrzeug: dict) -> dict:
    """Pruefung 05.10.2026 (Paket 1): die Werte kommen aus einer Browserseite und werden auch
    fuer dasselbe Konto nur in festen, validierten Formen gespeichert:
      * Zahlen nur endlich und im plausiblen Bereich (sonst None), Texte gekuerzt, falsche Typen (Objekt statt
        Text) werden zu None statt spaeter zu einem 500
      * Beschreibung hoechstens 20.000 Zeichen, Listen hoechstens 400 Eintraege
      * Fotos nur von den Bild-Servern der Portale (bild_proxy.erlaubt) — eine fremde Adresse wuerde sonst im
        Browser anderer Firmen geladen (deren IP-Adresse geht an den fremden Server)"""
    import bild_proxy
    sauber: Dict[str, Any] = {}
    for k, w in list(fahrzeug.items())[:200]:
        k = str(k)[:60]
        if k in _BILD_FELDER:
            sauber[k] = [u for u in (w if isinstance(w, (list, tuple)) else [])
                         if isinstance(u, str) and bild_proxy.erlaubt(u)][:MAX_BILDER]
        elif k in _ZAHL_GRENZEN:
            von, bis = _ZAHL_GRENZEN[k]
            ok = (isinstance(w, (int, float)) and not isinstance(w, bool) and math.isfinite(w) and von <= w <= bis)
            sauber[k] = w if ok else None
        elif k in _TEXT_FELDER:
            if isinstance(w, str):
                sauber[k] = w[:MAX_BESCHREIBUNG if k == "description" else MAX_TEXT]
            elif isinstance(w, (int, float)) and not isinstance(w, bool) and math.isfinite(w):
                sauber[k] = str(w)[:MAX_TEXT]
            else:
                sauber[k] = None
        else:
            sauber[k] = _wert_bereinigen(w)
    if "image_count" in sauber:
        sauber["image_count"] = len(sauber.get("image_urls") or sauber.get("images") or [])
    return sauber


def fahrzeug_kurz(fahrzeug: dict, identity: dict, inserat_url: str) -> dict:
    """Die Felder, die das Programm-Protokoll (werkzeug_vergleiche.fahrzeug) und die App
    ("Deine letzten Autos", Chef-Uebersicht) kennen."""
    ez = str(fahrzeug.get("first_registration") or "")
    m = re.match(r"^(\d{1,2})/(\d{4})$", ez)
    jahr = int(m.group(2)) if m else (int(ez) if re.fullmatch(r"\d{4}", ez) else None)
    return {
        "marke": fahrzeug.get("make_label") or "",
        "modell": fahrzeug.get("model_label") or "",
        "titel": (fahrzeug.get("model_description") or "")[:300],
        "ez_monat": int(m.group(1)) if m else None,
        "ez_jahr": jahr,
        "kilometer": fahrzeug.get("mileage"),
        "kw": fahrzeug.get("power_kw"),
        "ps": fahrzeug.get("power_ps"),
        "kraftstoff": fahrzeug.get("fuel_label") or "",
        "getriebe": fahrzeug.get("gearbox_label") or "",
        "preis": int(fahrzeug["list_price"]) if fahrzeug.get("list_price") else None,
        "quelle": identity["source"],
        "inserat_id": identity["item_id"],
        "inserat_url": inserat_url,
    }


#: Woerter in der Beschreibung, die beim Preis zaehlen (kurz, fuer die Box im Inserat). Mit Fundstelle als
#: Zitat: Haendler schreiben oft "wir kaufen auch Autos mit Motorschaden an" (E2E 04.10.2026) — das soll der
#: Sucher selbst einordnen koennen, statt einen Schaden behauptet zu bekommen.
_BESCHREIBUNG_HINWEISE = (
    (re.compile(r"motorschaden", re.I), "Motorschaden"),
    (re.compile(r"getriebeschaden", re.I), "Getriebeschaden"),
    (re.compile(r"\bbastler", re.I), "Bastler"),
    (re.compile(r"\b(ohne|kein(e|en)?)\s+(tüv|tuev|hu)\b|\b(tüv|tuev|hu)\s+(ist\s+)?(fällig|faellig|abgelaufen)", re.I),
     "HU/TÜV"),
    (re.compile(r"hagel", re.I), "Hagel"),
    (re.compile(r"\brost(schaden|stellen|ansatz|ansätze)?\b", re.I), "Rost"),
    (re.compile(r"\bdefekt", re.I), "defekt"),
)
_ZITAT_RAND = 28


def _zitat(text: str, start: int, ende: int) -> str:
    a, b = max(0, start - _ZITAT_RAND), min(len(text), ende + _ZITAT_RAND)
    stueck = re.sub(r"\s+", " ", text[a:b]).strip()
    return ("…" if a > 0 else "") + stueck + ("…" if b < len(text) else "")


def _hu_hinweis(hu, heute=None) -> Optional[str]:
    from datetime import date
    m = re.match(r"^\s*(\d{1,2})/(\d{4})\s*$", str(hu or ""))
    if not m:
        return None
    heute = heute or date.today()
    monate = (int(m.group(2)) - heute.year) * 12 + int(m.group(1)) - heute.month
    if monate < 0:
        return f"HU abgelaufen ({int(m.group(1)):02d}/{m.group(2)})"
    if monate <= 3:
        return f"HU bald fällig ({int(m.group(1)):02d}/{m.group(2)})"
    return None


def verhandlung_hinweise(fahrzeug: dict, max_anzahl: int = 5, heute=None) -> List[str]:
    """Kurze, kostenlose Hinweise aus dem Inserat fuer die Preisverhandlung (keine KI):
    Unfall, HU, Vorbesitzer, Fahrbereitschaft, Schadenswoerter in der Beschreibung, VB."""
    hinweise: List[str] = []
    if fahrzeug.get("accident_damaged") is True:
        hinweise.append("Unfallschaden laut Inserat")
    if fahrzeug.get("roadworthy") is False:
        hinweise.append("Nicht fahrbereit laut Inserat")
    hu = _hu_hinweis(fahrzeug.get("hu"), heute)
    if hu:
        hinweise.append(hu)
    try:
        halter = int(str(fahrzeug.get("previous_owners") or "0").strip() or 0)
    except ValueError:
        halter = 0
    if halter >= 3:
        hinweise.append(f"{halter} Vorbesitzer")
    text = str(fahrzeug.get("description") or "")
    for muster, stichwort in _BESCHREIBUNG_HINWEISE:
        m = muster.search(text)
        if m:
            hinweise.append(f"{stichwort} in der Beschreibung: „{_zitat(text, m.start(), m.end())}“")
    if fahrzeug.get("price_negotiable"):
        hinweise.append("Preis verhandelbar (VB)")
    return hinweise[:max_anzahl]


# ---------------------------------------------------------------- Vergleichsseite (Ergebnisliste)
#: mobile.de: Werbeplaetze ueber der Liste koennen ausserhalb der Filter liegen — nicht mitzaehlen
_MOBILE_WERBUNG = {"topofpage", "topincategory"}


def _sortierung_mobile(url: str) -> str:
    q = parse_qs(urlparse(url).query)
    sb, od = (q.get("sb") or [""])[0], (q.get("od") or [""])[0]
    return "preis_auf" if sb == "p" and od in ("up", "") else (f"{sb}:{od}" if sb else "relevanz")


def _sortierung_autoscout(url: str) -> str:
    q = parse_qs(urlparse(url).query)
    sort, desc = (q.get("sort") or [""])[0], (q.get("desc") or ["0"])[0]
    return "preis_auf" if sort == "price" and desc in ("0", "") else (f"{sort}:{desc}" if sort else "relevanz")


#: Zustandszeilen der mobile.de-Ergebniskarten (stehen NICHT in den eingebetteten Daten, live 04.10.2026)
_MOBILE_ZUSTAENDE = {"unfallfrei", "beschädigt", "unfallfahrzeug", "nicht fahrtauglich", "fahrtauglich", "repariert"}
_MOBILE_ABZEICHEN = {"gesponsert", "neu", "top", "neues angebot"}
_PREISZEILE = re.compile(r"^\d[\d.]*\s*€")


def mobile_karten(html: str) -> Dict[str, dict]:
    """Je Inserat-ID aus der sichtbaren Ergebnisliste: Titel (Marke/Modell + Zusatz) und Zustandszeilen
    ("Unfallfrei", "Beschädigt", "Unfallfahrzeug", "Nicht fahrtauglich") — fuers Aussortieren."""
    try:
        import lxml.html
        baum = lxml.html.fromstring(html)
    except Exception:  # noqa: BLE001 — ohne Karten zaehlen nur die eingebetteten Daten
        return {}
    karten: Dict[str, dict] = {}
    for n, a in enumerate(baum.xpath('//a[contains(@href, "details.html?")]')):
        if n >= MAX_KARTEN_LINKS:
            break
        href = a.get("href") or ""
        if len(href) > MAX_HREF:             # eine echte Inserat-Adresse ist kurz; lange liefen quadratisch
            continue
        # Pruefung 08.10.2026: die Nummer per parse_qs (linear) statt mit einem Muster, das bei gebauten Adressen
        # vielfach zuruecksprang
        kennung = ((parse_qs(urlparse(href).query).get("id") or [""])[0]).strip()
        if not (kennung.isascii() and kennung.isdigit()) or kennung in karten:
            continue
        zeilen = [z.strip() for z in a.itertext() if z.strip() and z.strip() != "•"]
        titel = []
        for z in zeilen:
            if _PREISZEILE.match(z):
                break
            if z.lower() not in _MOBILE_ABZEICHEN:
                titel.append(z)
        karten[kennung] = {"titel": " ".join(titel)[:200],
                              "zustand": [z for z in zeilen if z.lower() in _MOBILE_ZUSTAENDE]}
    return karten


def mobile_treffer(html: str, url: str) -> dict:
    from mobile_service import _apify_leistung, _apify_zahl
    text = next_flight_text(html)
    sr = json_objekt_nach(text, '"searchResults":{"numResultsTotal"')
    if not sr or not isinstance(sr.get("listings"), list):
        raise SeiteUngueltig("Auf der Vergleichsseite stehen keine Ergebnisse (Prüfseite von mobile.de?).")
    karten = mobile_karten(html)
    treffer, gesehen = [], set()
    for x in sr["listings"]:
        if not isinstance(x, dict) or str(x.get("type") or "").lower() in _MOBILE_WERBUNG:
            continue
        kennung = str(x.get("id") or "")
        if kennung in gesehen:               # ein hervorgehobenes Angebot steht evtl. zweimal in der Liste
            continue
        gesehen.add(kennung)
        attr = x.get("attr") if isinstance(x.get("attr"), dict) else {}
        kw, ps = _apify_leistung(attr.get("pw"))
        preis = ((x.get("price") or {}).get("grs") or {}).get("amount") if isinstance(x.get("price"), dict) else None
        karte = karten.get(kennung) or {}
        zustand_neu = " ".join(str(attr.get(k) or "") for k in ("con", "subc")).lower()
        treffer.append({
            "id": kennung,
            "titel": karte.get("titel") or " ".join(
                str((x.get(k) or {}).get("localized") or "") for k in ("make", "model")).strip(),
            "zustand": karte.get("zustand") or [],
            "neu": "tageszulassung" in zustand_neu or "neufahrzeug" in zustand_neu,
            "preis": int(preis) if isinstance(preis, (int, float)) and preis > 0 else None,
            "ez": attr.get("fr") or None,
            "km": _apify_zahl(attr.get("ml")),
            "kw": kw, "ps": ps,
            "kraftstoff": attr.get("ft") or None,
            "getriebe": attr.get("tr") or None,
            "verkaeufer": {"DEALER": "haendler", "COMM_FSBO": "haendler", "FSBO": "privat", "PRIVATE": "privat"}.get(
                str((x.get("contact") or {}).get("enumType") or "").upper()),
            "bewertung": (x.get("priceRating") or {}).get("rating") if isinstance(x.get("priceRating"), dict) else None,
        })
    gesamt = sr.get("numResultsTotal")
    return {"portal": "mobile.de", "gesamt": gesamt if isinstance(gesamt, int) else None,
            "sortierung": _sortierung_mobile(url), "treffer": treffer}


def autoscout_treffer(html: str, url: str) -> dict:
    from mobile_service import _apify_leistung, _apify_zahl
    nd = next_data(html) or {}
    pp = (nd.get("props") or {}).get("pageProps") or {}
    if not isinstance(pp.get("listings"), list):
        raise SeiteUngueltig("Auf der Vergleichsseite stehen keine Ergebnisse (Prüfseite von AutoScout24?).")
    treffer = []
    for x in pp["listings"]:
        if not isinstance(x, dict):
            continue
        art = str(x.get("searchResultType") or "Organic")
        if art.lower() != "organic":
            continue                                 # gesponserte Plaetze koennen ausserhalb der Filter liegen
        t = x.get("tracking") if isinstance(x.get("tracking"), dict) else {}
        leistung = next((d.get("data") for d in x.get("vehicleDetails") or []
                         if isinstance(d, dict) and d.get("iconName") == "speedometer"), None)
        kw, ps = _apify_leistung(leistung)
        preis = (x.get("price") or {}).get("priceRaw") if isinstance(x.get("price"), dict) else None
        ez = str(t.get("firstRegistration") or "").replace("-", "/") or None
        fz = x.get("vehicle") if isinstance(x.get("vehicle"), dict) else {}
        treffer.append({
            "id": str(x.get("id") or "").lower(),
            "titel": " ".join(str(fz.get(k) or "") for k in ("make", "model", "modelVersionInput", "subtitle")
                              if fz.get(k)).strip()[:200],
            "zustand": [],
            # AutoScout-Angebotsart: N = neu, S = Tageszulassung (U = gebraucht, J = Jahreswagen bleiben)
            "neu": str(fz.get("offerType") or "").upper() in ("N", "S"),
            "preis": int(preis) if isinstance(preis, (int, float)) and preis > 0 else None,
            "ez": ez, "km": _apify_zahl(t.get("mileage")), "kw": kw, "ps": ps,
            "kraftstoff": (x.get("vehicle") or {}).get("fuel") if isinstance(x.get("vehicle"), dict) else None,
            "getriebe": (x.get("vehicle") or {}).get("transmission") if isinstance(x.get("vehicle"), dict) else None,
            "verkaeufer": "haendler" if str((x.get("seller") or {}).get("type") or "").lower() == "dealer"
            else "privat",
            "bewertung": None,
        })
    gesamt = pp.get("numberOfResults")
    return {"portal": "AutoScout24", "gesamt": gesamt if isinstance(gesamt, int) else None,
            "sortierung": _sortierung_autoscout(url), "treffer": treffer}


#: AutoScout24 schreibt die Such-Adresse nach dem Laden um (04.10.2026 im Browser gesehen):
#:   /lst/volkswagen?atype=C&cat=ma74mo2090&fuel=B&gear=M&kmto=158000&…
#:   -> /lst/volkswagen/polo/ft_benzin/tr_schaltgetriebe?kmto=158000&…&atype=C
#: Diese Angaben wandern in den Pfad; alle anderen Filter bleiben (in anderer Reihenfolge) stehen.
_AUTOSCOUT_IN_DEN_PFAD = frozenset({"cat", "fuel", "gear", "body", "mmvmk0", "mmvmd0", "mmmv"})
_NIE_VERGLEICHEN = frozenset({"ref"})


def _filter(query: str, ohne=frozenset()) -> list:
    return sorted((k, v) for k, v in parse_qsl(query, keep_blank_values=True)
                  if k not in _NIE_VERGLEICHEN and k not in ohne)


def gleiche_suche(link_url: str, seiten_url: str) -> bool:
    """Ist die geoeffnete Ergebnisseite genau die Suche aus diesem Link (dieselben Filter, erste Seite)?

    Wunsch Ahmad 04.10.2026: der Helfer wertet auch die Vergleichsseiten aus, die das Windows-Programm geoeffnet
    hat. Lieber keine Ampel als die Ampel eines anderen Autos: ein geaenderter oder zusaetzlicher Filter, eine
    andere Seite oder Sortierung passt nicht."""
    art = ist_vergleichsseite(link_url)
    if not art or art != ist_vergleichsseite(seiten_url):
        return False
    a, b = urlparse(link_url), urlparse(seiten_url)
    if art == "mobile":
        return a.path == b.path and _filter(a.query) == _filter(b.query)
    host = lambda p: (p.hostname or "").lower().removeprefix("www.")
    return host(a) == host(b) and _autoscout_kern(a) == _autoscout_kern(b)


def _autoscout_kern(p) -> tuple:
    """Was von einer AutoScout24-Suche nach dem Umschreiben der Adresse gleich bleibt: Marke, Erstzulassung und
    alle Filter, die NICHT in den Pfad wandern. Am 06.10.2026 an fuenf echten Suchen geprueft:
      /lst/opel?…&cat=ma54mo1918&fregfrom=2012&fregto=2012&fuel=B&gear=M&kmfrom=…
      -> /lst/opel/corsa/re_2012/ft_benzin/tr_schaltgetriebe?kmfrom=…        (gleiches Jahr von/bis -> "re_2012")
      /lst/tesla?…&cat=…&fuel=E&gear=A -> /lst/tesla/model-3?…               (Kraftstoff/Getriebe ganz weg)
    Vorher fiel "Erstzulassung genau" (fregfrom = fregto) durch — keine Ampel auf den Programm-Seiten."""
    q = dict(parse_qsl(p.query, keep_blank_values=True))
    teile = [s.lower() for s in p.path.split("/") if s]
    marke = teile[1] if len(teile) > 1 else ""
    von, bis = q.pop("fregfrom", ""), q.pop("fregto", "")
    jahr = next((s[3:] for s in teile[2:] if s.startswith("re_")), "")
    if jahr and not von and not bis:
        von = bis = jahr
    for k in _AUTOSCOUT_IN_DEN_PFAD | _NIE_VERGLEICHEN:
        q.pop(k, None)
    return marke, von, bis, sorted(q.items())


def ist_vergleichsseite(url: str) -> Optional[str]:
    """"mobile" / "autoscout24" fuer eine Ergebnisseite (Vergleich), sonst None."""
    try:
        p = urlparse(url)
    except ValueError:
        return None
    host = (p.hostname or "").lower()
    if p.scheme != "https":
        return None
    if host == "suchen.mobile.de" and p.path.startswith("/fahrzeuge/search.html"):
        return "mobile"
    if re.fullmatch(r"(www\.)?autoscout24\.(de|at|ch|com)", host) and p.path.startswith("/lst"):
        return "autoscout24"
    return None


#: Pruefung 05.10.2026 (Nr. 11): eine Ergebnisseite hat 20–50 Treffer — mehr wertet der Server nie aus (eine
#: praeparierte 8-MB-Seite mit Zehntausenden "Treffern" haette sonst einen Prozess minutenlang belegt)
MAX_TREFFER = 200


def treffer_auslesen(url: str, html: str) -> dict:
    art = ist_vergleichsseite(url)
    if art == "mobile":
        liste = mobile_treffer(html, url)
    elif art == "autoscout24":
        liste = autoscout_treffer(html, url)
    else:
        raise SeiteUngueltig("Das ist keine Vergleichsseite von mobile.de oder AutoScout24.")
    liste["treffer"] = [_treffer_bereinigen(t) for t in (liste.get("treffer") or [])[:MAX_TREFFER]]
    return liste


def _zahl_im_bereich(wert, von: int, bis: int) -> Optional[int]:
    ok = isinstance(wert, (int, float)) and not isinstance(wert, bool) and math.isfinite(wert) and von <= wert <= bis
    return int(wert) if ok else None


def _treffer_bereinigen(t: dict) -> dict:
    """Wie fahrzeug_bereinigen fuer ein Vergleichsangebot: absurde Kilometer (10^20) liessen die Umrechnung
    ueberlaufen (OverflowError -> 500), Texte/Listen kamen ungekuerzt in werkzeug_vergleiche.marktlage."""
    text = lambda w, n: w[:n] if isinstance(w, str) else None  # noqa: E731
    return {
        "id": text(t.get("id"), 64) or "",
        "titel": text(t.get("titel"), 200) or "",
        "zustand": [z[:60] for z in (t.get("zustand") or []) if isinstance(z, str)][:10],
        "neu": t.get("neu") is True,
        "preis": _zahl_im_bereich(t.get("preis"), 1, 100_000_000),
        "ez": text(t.get("ez"), 10),
        "km": _zahl_im_bereich(t.get("km"), 0, 3_000_000),
        "kw": _zahl_im_bereich(t.get("kw"), 1, 2000),
        "ps": _zahl_im_bereich(t.get("ps"), 1, 3000),
        "kraftstoff": text(t.get("kraftstoff"), 40),
        "getriebe": text(t.get("getriebe"), 40),
        "verkaeufer": t.get("verkaeufer") if t.get("verkaeufer") in ("haendler", "privat") else None,
        "bewertung": text(t.get("bewertung"), 40),
    }


# ---------------------------------------------------------------- Marktlage + Ampel
#: Wunsch Ahmad 04.10.2026 (so erklaert): gruen = unter den guenstigsten 25 % der Vergleiche,
#: gelb = bis zur Mitte, rot = teurer als die Haelfte. Keine KI, kein erfundener Abschlag.
GRUEN_BIS = 0.25
GELB_BIS = 0.5
MIN_VERGLEICHE = 3

# Wunsch Ahmad 04.10.2026: "Aussortierte Angebote: Unfallwagen, Export oder Neuwagen" — sie verfaelschen sonst die
# Ampel (E2E: auf einer Golf-Seite ohne Schadensfilter waren die fuenf billigsten alle "Beschädigt").
_AUS_UNFALL = re.compile(r"unfallfahrzeug|unfallwagen|unfallschaden|(?<!un)besch(ä|ae)digt|hagel|\bunfall\b(?!\s*frei)",
                         re.I)
_AUS_DEFEKT = re.compile(r"nicht\s+fahr(tauglich|bereit|f(ä|ae)hig)|motorschaden|getriebeschaden|\bdefekt|\bbastler"
                         r"|\b(ohne|kein(e|en)?)\s+(tüv|tuev|hu)\b|\bmotor\s+(kaputt|defekt)", re.I)
_AUS_EXPORT = re.compile(r"\bexport(?!\s*(m(ö|oe)glich|auf\s+anfrage|gerne))|\bnur\s+(an\s+)?(h(ä|ae)ndler|gewerbe)"
                         r"|h(ä|ae)ndler\s*/\s*export|gewerbe\s*/\s*(h(ä|ae)ndler|export)|h(ä|ae)ndlerpreis", re.I)
GRUENDE = {
    "unfall": "Unfall/beschädigt",
    "defekt": "nicht fahrbereit/defekt",
    "export": "Export/Händlerpreis",
    "neu": "Neuwagen/Tageszulassung",
    "preis": "Preis auffällig niedrig",
}
#: Lockangebot/Teileauto: unter 40 % der Mitte der uebrigen Angebote (erst ab 5 Angeboten)
PREIS_AUFFAELLIG = 0.4
#: Neuwagen ohne Kennzeichnung: hoechstens so viele km, wenn das eigene Auto deutlich mehr hat
NEU_KM = 1000


def _aussortieren(t: dict, eigene_km) -> Optional[str]:
    text = " ".join([str(t.get("titel") or "")] + [str(z) for z in t.get("zustand") or []])
    if _AUS_UNFALL.search(text):
        return "unfall"
    if _AUS_DEFEKT.search(text):
        return "defekt"
    if _AUS_EXPORT.search(text):
        return "export"
    km = t.get("km")
    if t.get("neu") or (isinstance(km, int) and km <= NEU_KM and isinstance(eigene_km, int) and eigene_km >= 10 * NEU_KM):
        return "neu"
    return None


def _jahr(ez) -> Optional[float]:
    # nur 1900–2100: "01/9999" liess (1 + je_jahr) ** Jahre ueberlaufen (OverflowError)
    m = re.match(r"^\s*(\d{1,2})/(\d{4})\s*$", str(ez or "")[:20])
    if m and 1 <= int(m.group(1)) <= 12 and 1900 <= int(m.group(2)) <= 2100:
        return int(m.group(2)) + (int(m.group(1)) - 1) / 12
    m = re.match(r"^\s*(\d{4})\s*$", str(ez or "")[:20])
    return int(m.group(1)) + 0.5 if m and 1900 <= int(m.group(1)) <= 2100 else None


#: Faustwerte, wenn die Vergleichsangebote keine eigene Umrechnung hergeben (zu wenige, zu gleich, unplausibel)
FAUST_JE_10000_KM = -0.015       # -1,5 % je 10.000 km mehr
FAUST_JE_JAHR = 0.08             # +8 % je Jahr juenger
#: plausibler Bereich fuer die aus den Angeboten berechneten Werte
_KM_BEREICH = (-0.04, 0.0)
_JAHR_BEREICH = (0.0, 0.20)


def _loesen(a: List[List[float]], b: List[float]) -> Optional[List[float]]:
    """Kleines lineares Gleichungssystem (Gauss mit Spaltenpivot); None, wenn (fast) singulaer."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for k in range(n):
        p = max(range(k, n), key=lambda i: abs(m[i][k]))
        if abs(m[p][k]) < 1e-9:
            return None
        m[k], m[p] = m[p], m[k]
        for i in range(k + 1, n):
            f = m[i][k] / m[k][k]
            for j in range(k, n + 1):
                m[i][j] -= f * m[k][j]
    x = [0.0] * n
    for i in range(n - 1, -1, -1):
        x[i] = (m[i][n] - sum(m[i][j] * x[j] for j in range(i + 1, n))) / m[i][i]
    return x


def umrechnungs_faktoren(saubere: List[dict]) -> Tuple[float, float, str]:
    """(je 10.000 km, je Jahr, Quelle) als Anteil (log-linear). Aus den Vergleichsangeboten per Ausgleichsrechnung
    log(Preis) ~ km + Erstzulassung, wenn genug unterschiedliche Angebote da sind und das Ergebnis plausibel ist;
    sonst die Faustwerte."""
    import math
    punkte = [(math.log(t["preis"]), t["km"] / 10000, _jahr(t.get("ez"))) for t in saubere
              if t.get("preis") and isinstance(t.get("km"), int) and _jahr(t.get("ez")) is not None]
    if len(punkte) >= 6:
        kms = [p[1] for p in punkte]
        jahre = [p[2] for p in punkte]
        if max(kms) - min(kms) >= 2 and max(jahre) - min(jahre) >= 1:
            s = lambda f: sum(f(p) for p in punkte)  # noqa: E731
            n = float(len(punkte))
            a = [[n, s(lambda p: p[1]), s(lambda p: p[2])],
                 [s(lambda p: p[1]), s(lambda p: p[1] ** 2), s(lambda p: p[1] * p[2])],
                 [s(lambda p: p[2]), s(lambda p: p[1] * p[2]), s(lambda p: p[2] ** 2)]]
            b = [s(lambda p: p[0]), s(lambda p: p[0] * p[1]), s(lambda p: p[0] * p[2])]
            x = _loesen(a, b)
            if x:
                je_km, je_jahr = math.exp(x[1]) - 1, math.exp(x[2]) - 1
                if _KM_BEREICH[0] <= je_km <= _KM_BEREICH[1] and _JAHR_BEREICH[0] <= je_jahr <= _JAHR_BEREICH[1]:
                    return je_km, je_jahr, f"aus {len(punkte)} Vergleichsangeboten berechnet"
    return FAUST_JE_10000_KM, FAUST_JE_JAHR, "Faustwert"


def _prozent(x: float) -> str:
    return f"{x * 100:+.1f} %".replace(".", ",").replace("-", "−")


def umrechnung(saubere: List[dict], eigen: Optional[dict], preis: Optional[int]) -> Optional[dict]:
    """Wunsch Ahmad 04.10.2026: das guenstigste Angebot auf km und Erstzulassung des eigenen Autos umrechnen.
    Gerechnet wird jedes saubere Angebot; das guenstigste NACH der Umrechnung zaehlt (das kann ein anderes sein
    als das guenstigste auf der Seite)."""
    eigen = eigen or {}
    km_e = eigen.get("kilometer")
    jahr_e = (eigen["ez_jahr"] + ((eigen.get("ez_monat") or 7) - 1) / 12) if eigen.get("ez_jahr") else None
    if not isinstance(km_e, int) or jahr_e is None:
        return None
    je_km, je_jahr, quelle = umrechnungs_faktoren(saubere)
    beste = None
    for t in saubere:
        j = _jahr(t.get("ez"))
        if not t.get("preis") or not isinstance(t.get("km"), int) or j is None:
            continue
        try:
            faktor = (1 + je_km) ** ((km_e - t["km"]) / 10000) * (1 + je_jahr) ** (jahr_e - j)
            wert = t["preis"] * faktor
        except (OverflowError, ZeroDivisionError):      # absurde Werte (EZ 9999, 10^9 km): nicht umrechenbar
            continue
        if beste is None or wert < beste[0]:
            beste = (wert, t)
    if beste is None:
        return None
    wert, t = int(round(beste[0], -1)), beste[1]
    km_text = f"{t['km']:,}".replace(",", ".")
    raus = {"preis": wert, "angebot_preis": t["preis"], "angebot_ez": t.get("ez"), "angebot_km": t.get("km"),
            "je_10000_km": round(je_km, 4), "je_jahr": round(je_jahr, 4), "quelle": quelle,
            "text": (f"Günstigstes sauberes Angebot umgerechnet auf euer Auto: ca. {_eur(wert)} "
                     f"(Angebot {_eur(t['preis'])}, EZ {t.get('ez')}, {km_text} km)"),
            "text_faktoren": (f"Umrechnung ({quelle}): je 10.000 km mehr {_prozent(je_km)}, "
                              f"je Jahr jünger {_prozent(je_jahr)}")}
    if preis:
        diff = preis - wert
        raus["text_inserat"] = (f"Inserat ca. {_eur(diff)} darüber" if diff > 0
                                else f"Inserat ca. {_eur(-diff)} darunter" if diff < 0 else "Inserat liegt gleichauf")
    return raus


def _eur(n) -> str:
    return f"{int(n):,} €".replace(",", ".")


def marktlage(preis: Optional[int], eigene_id: str, liste: dict, eigen: Optional[dict] = None) -> dict:
    """Wo liegt das Inserat unter den Vergleichsangeboten?

    Nach Preis aufsteigend sortiert (Standard der Firmenregeln) zeigt Seite 1 die
    guenstigsten Angebote: liegt der eigene Preis darin, ist der Platz unter ALLEN
    Treffern exakt bekannt; liegt er darueber, ist nur eine Untergrenze bekannt.
    Andere Sortierung: Seite 1 ist nur eine Stichprobe.

    Unfall/beschaedigt, nicht fahrbereit, Export/Haendlerpreis, Neuwagen und auffaellig billige Angebote werden
    aussortiert (eigene Liste) und zaehlen nicht; das guenstigste saubere Angebot wird zusaetzlich auf km und
    Erstzulassung des eigenen Autos umgerechnet (`eigen`: kilometer, ez_jahr, ez_monat)."""
    eigene_id = str(eigene_id or "").lower()
    alle = [t for t in (liste.get("treffer") or [])[:MAX_TREFFER] if t.get("preis")]
    selbst_dabei = any(str(t.get("id") or "").lower() == eigene_id for t in alle)
    eigene_km = (eigen or {}).get("kilometer")
    sauber, aussortiert = [], []
    for t in alle:
        if str(t.get("id") or "").lower() == eigene_id:
            continue
        grund = _aussortieren(t, eigene_km)
        (aussortiert.append((t, grund)) if grund else sauber.append(t))
    if len(sauber) >= 5:
        mitte = statistics.median(t["preis"] for t in sauber)
        billig = [t for t in sauber if t["preis"] < PREIS_AUFFAELLIG * mitte]
        sauber = [t for t in sauber if t["preis"] >= PREIS_AUFFAELLIG * mitte]   # linear (Nr. 11), dieselbe Grenze
        aussortiert += [(t, "preis") for t in billig]
    preise = sorted(t["preis"] for t in sauber)
    gelesen = len(preise)
    gesamt = liste.get("gesamt")
    gesamt = max((gesamt - (1 if selbst_dabei else 0) - len(aussortiert)) if isinstance(gesamt, int) else gelesen,
                 gelesen)
    zaehlung: Dict[str, int] = {}
    for _t, g in aussortiert:
        zaehlung[g] = zaehlung.get(g, 0) + 1
    raus: Dict[str, Any] = {
        "portal": liste.get("portal"), "preis": preis, "gesamt": gesamt, "gelesen": gelesen,
        "guenstigstes": preise[0] if preise else None,
        "mitte": int(statistics.median(preise)) if preise and gesamt <= gelesen else None,
        "stichprobe": liste.get("sortierung") != "preis_auf",
        "aussortiert_anzahl": len(aussortiert),
        "aussortiert": [{"preis": t["preis"], "grund": g, "grund_text": GRUENDE[g],
                         "titel": (t.get("titel") or "")[:120], "ez": t.get("ez"), "km": t.get("km")}
                        for t, g in sorted(aussortiert, key=lambda x: x[0]["preis"])][:25],
        "text_aussortiert": (f"{len(aussortiert)} aussortiert: "
                             + ", ".join(f"{n}× {GRUENDE[g]}" for g, n in sorted(zaehlung.items(), key=lambda x: -x[1]))
                             if aussortiert else ""),
        "umgerechnet": umrechnung(sauber, eigen, preis),
    }
    if not preis:
        return {**raus, "ampel": "grau", "text": "Kein Preis im Inserat – keine Einordnung."}
    if gelesen == 0 and aussortiert:
        return {**raus, "ampel": "grau",
                "text": "Auf der ersten Vergleichsseite stehen nur aussortierte Angebote – bitte selbst durchsehen."}
    if gesamt < MIN_VERGLEICHE:
        return {**raus, "ampel": "grau",
                "text": f"Nur {gesamt} Vergleichsangebot{'e' if gesamt != 1 else ''} – zu wenig für eine Einordnung."}
    guenstiger = sum(1 for p in preise if p < preis)
    raus["abstand_guenstigstes"] = preis - preise[0] if preise else None
    if raus["stichprobe"]:
        anteil = guenstiger / gelesen if gelesen else 0
        exakt = False
        basis = gelesen
    elif guenstiger < gelesen or gesamt <= gelesen:
        anteil, exakt, basis = guenstiger / gesamt, True, gesamt
    else:
        anteil, exakt, basis = gelesen / gesamt, False, gesamt     # Untergrenze
    raus.update({"guenstiger": guenstiger, "platz": guenstiger + 1 if exakt or raus["stichprobe"] else None,
                 "anteil": round(anteil, 3), "exakt": exakt})
    if exakt or raus["stichprobe"]:
        if anteil <= GRUEN_BIS:
            ampel, wo = "gruen", "unter den günstigsten 25 %"
        elif anteil <= GELB_BIS:
            ampel, wo = "gelb", "in der günstigeren Hälfte"
        else:
            ampel, wo = "rot", "teurer als die Hälfte"
        platz = f"Platz {guenstiger + 1} von {basis + 1}"
        text = f"{platz} – {wo}" + (" (Stichprobe Seite 1)" if raus["stichprobe"] else "")
    elif anteil > GELB_BIS:
        ampel, text = "rot", f"Teurer als die {gelesen} günstigsten von {gesamt} – teurer als die Hälfte"
    elif anteil > GRUEN_BIS:
        ampel, text = "gelb", f"Teurer als die {gelesen} günstigsten von {gesamt} – nicht unter den günstigsten 25 %"
    else:
        ampel, text = "grau", f"Teurer als die {gelesen} günstigsten von {gesamt} – genauer Platz unbekannt"
    raus.update({"ampel": ampel, "text": text})
    if preise:
        raus["text_guenstigstes"] = (f"Günstigstes sauberes Angebot {_eur(preise[0])}"
                                     + (f" (Inserat {_eur(preis - preise[0])} teurer)" if preis > preise[0]
                                        else " – das Inserat ist günstiger" if preis < preise[0]
                                        else " – gleicher Preis (vielleicht dasselbe Auto auf dem anderen Portal)"))
    return raus
