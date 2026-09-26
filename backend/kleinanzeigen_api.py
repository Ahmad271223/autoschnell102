# -*- coding: utf-8 -*-
"""Kleinanzeigen-Inserate ueber die API von kleinanzeigen-agent.de.

Warum (Wunsch Ahmad, 12.09.2026): Der eigene Abruf (kleinanzeigen_service)
laedt die HTML-Seite und wertet sie aus. Das dauert Sekunden, bricht bei
jeder Layout-Aenderung von Kleinanzeigen und wird zunehmend geblockt.
Die API liefert dieselben Daten strukturiert.

Gemessen am 12.09.2026 mit dem echten Schluessel:
  * eine Anzeige: 0,38-0,55 s (der eigene Abruf braucht ein Vielfaches)
  * 8 gleichzeitige Anfragen: 0,56 s insgesamt (echte Parallelitaet)
  * Limit laut Kopfzeile: 600 Anfragen je 60 s -> 10/s, reicht fuer
    30 Sucher mit grossem Abstand
  * Verkaeufername und Postleitzahl/Ort kommen mit; der eigene Abruf
    liefert den Namen gar nicht

Der eigene Abruf BLEIBT die Notloesung: faellt die API aus (Schluessel
abgelaufen, Limit erreicht, Stoerung), laeuft alles weiter wie bisher.
Deshalb wirft dieses Modul bei jedem API-Problem `ApiNichtNutzbar`, und
der Aufrufer (provider_fetch) faellt darauf still zurueck.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import httpx

from deps import log

# Basisadresse ohne abschliessenden Schraegstrich; der Pfad /ads/<id> kommt
# aus dem Aufruf. Ueber die Umgebung umstellbar, falls der Anbieter die
# Version wechselt (v2 -> v3), ohne Codeaenderung.
API_BASIS = os.environ.get(
    "KLEINANZEIGEN_API_URL",
    "https://api.kleinanzeigen-agent.de/api/v2/kleinanzeigen").rstrip("/")
API_KEY = os.environ.get("KLEINANZEIGEN_API_KEY", "").strip()
# 12 s: die API antwortet in unter einer Sekunde. Wartet sie laenger, ist
# etwas kaputt — dann lieber frueh auf den eigenen Abruf zurueckfallen,
# statt den Nutzer minutenlang warten zu lassen.
API_TIMEOUT = float(os.environ.get("KLEINANZEIGEN_API_TIMEOUT", "12") or 12)


class ApiNichtNutzbar(RuntimeError):
    """Die API konnte das Inserat nicht liefern — der Aufrufer soll auf den
    eigenen Abruf zurueckfallen. KEIN Fehler fuer den Nutzer."""


def api_verfuegbar() -> bool:
    """Ist ein Schluessel hinterlegt? Ohne Schluessel bleibt alles beim
    eigenen Abruf (so laufen Tests und Entwicklungsrechner unveraendert)."""
    return bool(API_KEY)


# --------------------------------------------------------------- Umsetzen
def _details_als_tabelle(ad: Dict[str, Any]) -> Dict[str, str]:
    """`details` der API ist bereits die Tabelle der Detailseite
    (deutsche Beschriftung -> Wert) — genau die Form, die der eigene
    Parser aus dem HTML herausliest. Deshalb koennen beide Wege
    dieselben Hilfsfunktionen benutzen."""
    roh = ad.get("details")
    if not isinstance(roh, dict):
        return {}
    return {str(k): ("" if v is None else str(v)) for k, v in roh.items()}


def _ausstattung(tabelle: Dict[str, str]) -> List[str]:
    """Ausstattungsmerkmale stehen in derselben Tabelle als Ja-Werte
    (z.B. "Anhaengerkupplung": "true"). Reine Angaben (Marke, km, Farbe)
    bleiben aussen vor."""
    return [k for k, v in tabelle.items() if str(v).strip().lower() == "true"]


def _alle_merkmale(tabelle: Dict[str, str], beschreibung: Optional[str]) -> List[str]:
    """Kleinanzeigens offizielle Haken UND die Aufzaehlung im Anzeigentext.

    Die Haken allein waeren zu wenig: bei einer echten Anzeige (3473537342)
    sind 15 Merkmale angehakt, weitere 30 stehen als Komma-Liste im Text.
    Der eigene Abruf findet beides, also muss der API-Weg das auch.
    Doppelte werden entfernt — auch wenn die Schreibweise abweicht
    ("ABS" steckt in "Antiblockiersystem (ABS)")."""
    from kleinanzeigen_service import _parse_equipment
    merkmale = _ausstattung(tabelle)
    bekannt = {m.lower() for m in merkmale}
    # Zwei Wege durch den Anzeigentext: die lange Komma-Aufzaehlung (viele
    # Haendler fuegen die Werksausstattung ein) UND das bewaehrte Stichwort-
    # Verzeichnis des eigenen Abrufs (findet einzelne Begriffe mitten im
    # Fliesstext, z.B. nur "AHK" und "USB" in einem Satz).
    # Reihenfolge mit Absicht: erst die bewaehrten Stichwoerter (genau das,
    # was der eigene Abruf findet), dann die lange Werksliste. Greift die
    # Obergrenze, faellt nur der Schwanz der Werksliste weg — nie etwas,
    # das der eigene Abruf geliefert haette.
    weitere = list(_parse_equipment(beschreibung or ""))
    for w in _ausstattung_aus_text(beschreibung or ""):
        if w not in weitere:
            weitere.append(w)
    for w in weitere:
        klein = w.lower()
        if klein in bekannt:
            continue
        # Steckt das Wort schon in einem der Haken — oder umgekehrt ein Haken
        # im Wort? Pruefbericht 20.09.2026 (S-26): nur eine Richtung liess
        # "ABS" (Haken) und "Antiblockiersystem (ABS)" (Text) beide stehen.
        if any(_merkmal_enthalten(klein, m) or _merkmal_enthalten(m, klein) for m in bekannt):
            continue
        merkmale.append(w)
        bekannt.add(klein)
        if len(merkmale) >= MAX_MERKMALE:
            break
    return merkmale


def _merkmal_enthalten(kurz: str, lang: str) -> bool:
    """Steckt `kurz` in `lang`? Kuerzel bis 4 Zeichen (ABS, ESP, AHK, LED) nur
    als ganzes Wort — sonst faende "AHK" in "Fahrkomfort" (S-26)."""
    import re
    if len(kurz) <= 4:
        return re.search(r"(?<![a-z0-9äöü])" + re.escape(kurz) + r"(?![a-z0-9äöü])", lang) is not None
    return kurz in lang


# Obergrenze fuer die Ausstattungsliste. Eine echte Haendleranzeige bringt
# bis zu ~125 Werksmerkmale mit (gemessen an 3458821471); der Wert liegt
# bewusst darueber — im Kaufvertrag soll nichts fehlen. Er schuetzt nur
# gegen entgleiste Texte.
MAX_MERKMALE = 150


# Ueberschriften, mit denen Verkaeufer ihre Ausstattungsliste einleiten.
# Sie kleben sonst am ERSTEN Merkmal ("Sonderausstattung: Dachreling") und
# machen es im schlimmsten Fall zu lang, sodass es ganz herausfaellt.
_LISTEN_UEBERSCHRIFTEN = (
    "sonderausstattung", "ausstattung", "serienausstattung", "extras",
    "features", "highlights", "weitere ausstattung", "ausstattungsmerkmale",
)


def _ohne_listenueberschrift(wort: str) -> str:
    if ":" not in wort:
        return wort
    kopf, rest = wort.split(":", 1)
    if kopf.strip().lower() in _LISTEN_UEBERSCHRIFTEN and rest.strip():
        return rest.strip()
    return wort


def _teile_ausserhalb_klammern(block: str) -> List[str]:
    """An Kommas trennen, aber NICHT innerhalb von Klammern — seit S-21 in
    kleinanzeigen_service (auch der AutoScout-Parser nutzt sie)."""
    from kleinanzeigen_service import _teile_ausserhalb_klammern as _teile
    return _teile(block)


def _ausstattung_aus_text(text: str) -> List[str]:
    """Ausstattung aus dem Anzeigentext holen.

    Viele Verkaeufer schreiben ihre Ausstattung als lange Komma-Liste in den
    Anzeigentext — Kleinanzeigens eigene Haken decken nur rund 30 Standard-
    Merkmale ab. Der eigene Abruf findet auf der Webseite die Ueberschrift
    "Ausstattung" und liest die Liste darunter; im reinen Text der API fehlt
    diese Ueberschrift, deshalb hier eigenes Erkennen.

    Eine Aufzaehlung erkennen wir daran, dass mindestens FUENF kurze Glieder
    hintereinander durch Komma getrennt stehen. So werden aus normalen
    Saetzen (die ebenfalls Kommas enthalten) keine Ausstattungsmerkmale.
    Beispiel (echte Anzeige 3473537342): 30 Merkmale, die sonst fehlten.
    """
    from kleinanzeigen_service import _clean
    if not text:
        return []
    raus: List[str] = []
    for block in str(text).replace(";", ",").split("\n"):
        if block.count(",") < 4:
            continue
        glieder = []
        for teil in _teile_ausserhalb_klammern(block):
            wort = _clean(teil)
            if not wort:
                continue
            wort = _ohne_listenueberschrift(wort)
            # Ein Merkmal ist kurz und kein Satz. Die Laengengrenze ist
            # dieselbe wie beim eigenen Abruf (_is_equipment_like, 70
            # Zeichen) — sonst fielen echte Werksbezeichnungen heraus wie
            # "Audiosystem Composition Colour (Touchscreen, MP3, Radio/CD-Player)".
            if (2 <= len(wort) <= 70 and len(wort.split()) <= 8
                    and not wort.endswith(".") and not wort.endswith("!")
                    and not wort.endswith("?")):
                glieder.append(wort)
            else:
                # Ein langes Stueck beendet die Aufzaehlung.
                if len(glieder) >= 5:
                    raus.extend(glieder)
                glieder = []
        if len(glieder) >= 5:
            raus.extend(glieder)
    # Rauschen wie beim eigenen Abruf herausfiltern (Preis, Anbieterzeilen).
    # Dieselbe Pruefung, kein Umweg ueber eine neu zusammengesetzte Zeile —
    # sonst zerfielen Merkmale mit Komma in der Klammer erneut.
    from kleinanzeigen_service import _is_equipment_like
    erlaubt = {w for w in raus if _is_equipment_like(w)}
    ordentlich: List[str] = []
    for w in raus:
        if w in erlaubt and w not in ordentlich:
            ordentlich.append(w)
    return ordentlich


def _bilder(ad: Dict[str, Any], hoechstens: int = 60) -> List[str]:
    roh = ad.get("images")
    if not isinstance(roh, list):
        return []
    raus: List[str] = []
    for b in roh:
        adresse = b if isinstance(b, str) else (b or {}).get("url")
        if isinstance(adresse, str) and adresse.startswith("http") and adresse not in raus:
            raus.append(adresse)
        if len(raus) >= hoechstens:
            break
    return raus


def fahrzeug_aus_api(ad: Dict[str, Any], url: str,
                     item_id: str = "") -> Dict[str, Any]:
    """API-Antwort in die interne Fahrzeugform bringen.

    Reine Funktion (kein Netz) — damit im Test ohne Zugang pruefbar.
    Die Form ist identisch zu kleinanzeigen_service.parse_kleinanzeigen_html;
    ein Test haelt beide Schluesselmengen zusammen, damit sie nicht
    auseinanderlaufen."""
    from kleinanzeigen_service import (_enhance_kleinanzeigen_model, _halter,
                                       _km_aus_text, _marke_aus_titel, _marke_fehlt,
                                       _parse_first_registration, _parse_fuel,
                                       _parse_gearbox, _parse_power,
                                       _resolve_make, _resolve_model, _to_int)
    from fahrzeug_codes import tueren_text
    from owners_extractor import extract_owners_from_text

    tabelle = _details_als_tabelle(ad)
    nummer = str(ad.get("ad_id") or item_id or "")
    titel = (ad.get("title") or "").strip() or None
    beschreibung = (ad.get("description") or "").strip() or None

    preis = ad.get("price") if isinstance(ad.get("price"), dict) else {}
    betrag = preis.get("amount")
    try:
        betrag = float(betrag) if betrag is not None else None
    except (TypeError, ValueError):
        betrag = None
    # Pruefbericht 20.09.2026 (S-13): Preisart und Verhandlungsbasis wurden
    # verworfen — ein Verschenken-Inserat kam als "0 €" in Vergleich und
    # Beweisdokument, "VB" fehlte ganz. Ein Betrag von 0 ist nie ein Preis.
    preisart = str(preis.get("price_type") or "").strip().upper()
    verschenken = preisart in ("GIVE_AWAY", "GIVEAWAY", "FREE", "ZU_VERSCHENKEN")
    verhandelbar = bool(preis.get("negotiable")) or preisart in ("NEGOTIABLE", "VB")
    if verschenken or (betrag is not None and betrag <= 0):
        betrag = None

    ort = ad.get("location") if isinstance(ad.get("location"), dict) else {}
    plz = str(ort.get("zip") or "").strip() or None
    stadt = str(ort.get("city") or "").strip() or None
    bundesland = str(ort.get("state") or "").strip()
    # Kleinanzeigen nennt in Grossstaedten den STADTTEIL statt der Stadt
    # ("Lichtenberg", "Wedding", "Nord"). Fuer den Kaufvertrag zaehlt die
    # Stadt. Bei den Stadtstaaten Berlin und Hamburg steht sie im Bundesland
    # — dann diese nehmen und den Stadtteil anhaengen, genau wie es der
    # eigene Abruf aus der Ortszeile der Webseite macht. Bremen bleibt
    # aussen vor (das Bundesland enthaelt auch Bremerhaven).
    stadtteil = None
    if bundesland in ("Berlin", "Hamburg") and stadt and stadt != bundesland:
        stadtteil, stadt = stadt, bundesland
    ortszeile = " ".join(x for x in (plz, stadt) if x) or None
    if ortszeile and stadtteil:
        ortszeile = f"{ortszeile} - {stadtteil}"

    verkaeufer = ad.get("seller") if isinstance(ad.get("seller"), dict) else {}
    anzeigename = str(verkaeufer.get("name") or "").strip() or None
    verkaeufer_art = {"COMMERCIAL": "haendler", "PRIVATE": "privat"}.get(
        str(verkaeufer.get("type") or "").strip().upper())
    # Rollenprüfung 22.09.2026 (RP-440): Bei Privatanbietern ist seller.name
    # das frei gewaehlte Kleinanzeigen-Pseudonym ("vnightx") — kein Name einer
    # Vertragspartei. Es fuellte im Kaufvertrag das Pflichtfeld "Name / Firma",
    # das damit als erledigt galt. Nur bei gewerblichen Anbietern ist der Name
    # die Firma; sonst bleibt seller_name leer und das Pseudonym steht getrennt
    # in seller_alias (nur als Hinweis fuer den Sucher).
    verkaeufer_name = anzeigename if verkaeufer_art == "haendler" else None
    verkaeufer_alias = None if verkaeufer_art == "haendler" else anzeigename

    # RP-439: Nutzfahrzeuge (Kategorie 276) tragen die Marke teils unter
    # "Hersteller" statt "Marke" und den Aufbau unter "Art".
    marke_roh = tabelle.get("Marke") or tabelle.get("Hersteller")
    modell_roh = tabelle.get("Modell")
    if modell_roh:
        import re
        sauber = re.sub(r"\s*\([^)]*\)\s*", "", modell_roh).strip()
        if sauber:
            modell_roh = sauber

    pseudo = {"make_label": marke_roh, "make": marke_roh,
              "model_label": modell_roh, "model": modell_roh}
    marke_id, marke_eintrag = _resolve_make(pseudo)
    marke_aus_titel = False
    if not marke_eintrag and _marke_fehlt(marke_roh):
        # Rollenprüfung 22.09.2026 (RP-439): Ohne Marke in der Tabelle (z. B.
        # Nutzfahrzeuge, Kategorie 276) gab es anders als im HTML-Weg keinen
        # Rueckfall — der mobile.de-Link lief dann ohne ms= ueber den GANZEN
        # Pkw-Markt. Review 22.09.: nur aus dem TITEL und streng — die erste
        # Fassung las auch die Beschreibung und nahm gewoehnliche Woerter
        # als Marke ("Man kann ihn besichtigen" -> MAN, "andere Extras" ->
        # Andere) und ueberschrieb sogar "Weitere Automarken" damit. Eine
        # konkrete, dem Katalog unbekannte Marke aus der Tabelle bleibt.
        marke_eintrag = _marke_aus_titel(titel)
        if marke_eintrag:
            marke_id = marke_eintrag["id"]
            marke_aus_titel = True
            pseudo["make_label"] = pseudo["make"] = marke_eintrag["raw_name"]
    modell_id = _resolve_model(marke_eintrag, pseudo) if marke_eintrag else None

    ez = _parse_first_registration(tabelle.get("Erstzulassung"))
    ps, kw = _parse_power(tabelle.get("Leistung"))
    if ps is None and kw is None:
        # Kleinanzeigen fuehrt die Leistung in PS; die API liefert die
        # blanke Zahl ohne Einheit ("122"), der HTML-Parser erwartet aber
        # "122 PS". Nur bei einer reinen Zahl ergaenzen — steht eine
        # Einheit dabei, hat _parse_power sie bereits richtig gelesen.
        roh_leistung = (tabelle.get("Leistung") or "").strip()
        if roh_leistung.isdigit():
            ps, kw = _parse_power(f"{roh_leistung} PS")
    kraftstoff, kraftstoff_text = _parse_fuel(tabelle.get("Kraftstoffart"))
    getriebe, getriebe_text = _parse_gearbox(tabelle.get("Getriebe"))
    bilder = _bilder(ad)
    # "Unbeschaedigtes Fahrzeug" / "Beschaedigtes Fahrzeug"
    zustand = (tabelle.get("Fahrzeugzustand") or "").lower()
    # Pruefbericht 20.09.2026 (S-06): ohne Angabe None statt "kein Unfall".
    from mobile_service import zustand_fahrbereit, zustand_unfall
    unfall = zustand_unfall(None, zustand)
    # S-22/A-11: Tabellenwert 0 zaehlt; nur 0-15 gilt als Halterzahl.
    halter = _halter(tabelle.get("Anzahl der Fahrzeughalter"))
    if halter is None:
        halter = extract_owners_from_text(beschreibung or "")

    ergebnis: Dict[str, Any] = {
        "mobile_ad_id": nummer,
        "kleinanzeigen_id": nummer or None,
        "kleinanzeigen_url": url,
        "detail_url": ad.get("ad_url") or url,
        "make": (marke_eintrag or {}).get("raw_name") or marke_roh,
        "make_label": (marke_eintrag or {}).get("raw_name") or marke_roh,
        "model": modell_roh,
        "model_label": modell_roh,
        "model_description": titel,
        "category": None,
        "category_label": tabelle.get("Fahrzeugtyp") or tabelle.get("Art"),
        "first_registration": ez,
        # RP-436: derselbe Kilometer-Leser wie im HTML-Weg (erste Zahl, plausibel)
        "mileage": _km_aus_text(tabelle.get("Kilometerstand")),
        "fuel": kraftstoff,
        "fuel_label": kraftstoff_text,
        "gearbox": getriebe,
        "gearbox_label": getriebe_text,
        "power_kw": kw,
        "power_ps": ps,
        "displacement": _to_int(tabelle.get("Hubraum")),
        "doors": tueren_text(tabelle.get("Anzahl Türen")),         # S-19
        "seats": _to_int(tabelle.get("Anzahl Sitzplätze")),
        "color": tabelle.get("Außenfarbe"),
        "vin": None,
        "license_plate": None,
        "hu": tabelle.get("HU bis"),
        "previous_owners": halter,
        "accident_damaged": unfall,
        # S-05: Kleinanzeigen kennt keine Fahrbereit-Angabe — nichts erfinden.
        "roadworthy": zustand_fahrbereit(None, zustand),
        "features": _alle_merkmale(tabelle, beschreibung),
        "description": beschreibung,
        "list_price": betrag,
        "currency": str(preis.get("currency_code") or "EUR"),
        # Der eigene Abruf liefert den Verkaeufernamen NICHT (er steht nicht
        # im sichtbaren Text) — ueber die API kommt er mit und spart dem
        # Sucher das Abtippen im Kaufvertrag. RP-440: nur die FIRMA eines
        # gewerblichen Anbieters; das Pseudonym eines Privatanbieters steht
        # getrennt in seller_alias.
        "seller_name": verkaeufer_name,
        "seller_alias": verkaeufer_alias,
        # Pruefbericht 20.09.2026 (S-14): gewerblich/privat wie bei den anderen
        # Quellen — vorher galten gewerbliche Anbieter als "unbekannt".
        "seller_type": verkaeufer_art,
        "seller_address": None,
        "seller_zip": plz,
        "seller_city": stadt,
        "seller_phone": None,
        "seller_email": None,
        "title": titel,
        "price_label": ("Zu verschenken" if verschenken
                        else (f"{int(betrag):,} €".replace(",", ".") + (" VB" if verhandelbar else ""))
                        if betrag is not None else None),
        "price_negotiable": verhandelbar,
        "location": ortszeile,
        "images": bilder,
        "image_urls": list(bilder),                     # S-30
        "image_count": len(bilder),
        "_resolved_make_id": marke_id,
        "_resolved_model_id": modell_id,
        # Review zu RP-439: Marke nur aus dem Titel geschlossen -> Hinweis
        # im Vergleich (routes/listings._katalog_pruefen).
        "_marke_aus_titel": marke_aus_titel,
        "_source": "kleinanzeigen",
        # Nur fuer Protokoll/Diagnose: welcher Weg hat die Daten geholt.
        "_abrufweg": "api",
    }
    _enhance_kleinanzeigen_model(ergebnis)
    return ergebnis


# ----------------------------------------------------------------- Abruf
async def hole_inserat(item_id: str, url: str) -> Dict[str, Any]:
    """Ein Inserat ueber die API holen.

    Wirft `ListingGone`, wenn die Anzeige nachweislich beendet/geloescht
    ist (die API sagt das ausdruecklich), und `ApiNichtNutzbar` bei jedem
    anderen Problem — dann uebernimmt der eigene Abruf."""
    from kleinanzeigen_service import ListingGone
    if not API_KEY:
        raise ApiNichtNutzbar("kein Schluessel hinterlegt")
    ziel = f"{API_BASIS}/ads/{item_id}"
    try:
        async with httpx.AsyncClient(timeout=API_TIMEOUT) as klient:
            antwort = await klient.get(ziel, headers={"klaz_key": API_KEY})
    except Exception as exc:  # noqa: BLE001 — Netzfehler jeder Art
        raise ApiNichtNutzbar(f"nicht erreichbar: {exc}") from exc

    if antwort.status_code in (401, 403):
        # Schluessel falsch/abgelaufen: laut protokollieren, damit es
        # auffaellt — sonst laeuft still fuer immer der langsame Weg.
        log.error("Kleinanzeigen-API weist den Schluessel ab (%s) — es laeuft "
                  "der eigene Abruf weiter. Schluessel in der .env pruefen "
                  "(KLEINANZEIGEN_API_KEY).", antwort.status_code)
        raise ApiNichtNutzbar(f"Schluessel abgelehnt ({antwort.status_code})")
    if antwort.status_code == 429:
        raise ApiNichtNutzbar("Limit der API erreicht (600/Minute)")
    if antwort.status_code >= 500:
        raise ApiNichtNutzbar(f"Stoerung bei der API ({antwort.status_code})")

    try:
        daten = antwort.json()
    except Exception as exc:  # noqa: BLE001
        raise ApiNichtNutzbar(f"unlesbare Antwort: {exc}") from exc

    if antwort.status_code == 404 or not daten.get("success"):
        code = str(daten.get("error_code") or "")
        if code in ("AD_DELETED", "AD_ENDED", "UPSTREAM_GONE"):
            raise ListingGone("Das Inserat ist bei Kleinanzeigen nicht mehr "
                              "verfügbar (gelöscht, beendet oder verkauft).")
        # UPSTREAM_NOT_FOUND kann auch eine Stoerung sein — den eigenen
        # Abruf entscheiden lassen, der liefert dann die klare Meldung.
        raise ApiNichtNutzbar(f"kein Treffer ({code or antwort.status_code})")

    ad = ((daten.get("data") or {}).get("ad")) or {}
    if not ad.get("ad_id"):
        raise ApiNichtNutzbar("Antwort ohne Anzeigendaten")
    if ad.get("deleted") is True or str(ad.get("status") or "ACTIVE").upper() not in (
            "ACTIVE", "ONLINE", ""):
        raise ListingGone("Das Inserat ist bei Kleinanzeigen nicht mehr "
                          "verfügbar (gelöscht, beendet oder verkauft).")
    # Abnahme 12.09.2026: Weicht die Antwort von der erwarteten Form ab
    # (der Anbieter aendert etwas, ein Feld ist ploetzlich eine Liste),
    # war das bisher ein harter Fehler FUER DEN SUCHER. Der Auftrag lautet
    # aber "eigener Abruf als Notloesung" — also auch hier still zurueck.
    try:
        return fahrzeug_aus_api(ad, url, item_id)
    except Exception as exc:  # noqa: BLE001
        raise ApiNichtNutzbar(f"Antwort nicht verwertbar: {exc}") from exc
