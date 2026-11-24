# -*- coding: utf-8 -*-
"""Abänderungsvertrag nach der Unterschrift (Wunsch Ahmad 24.11.2026) — reine Logik, ohne Datenbank.

Ein Kaufvertrag wird nach wie vor über NEUE FASSUNGEN geändert (routes.contracts: neue-fassung,
regenerate_contract_for_pickup nach der Abholung). Diese Fassungen bleiben die eine Wahrheit. Ist eine
frühere Fassung schon UNTERSCHRIEBEN (online im Kundenportal, bei der Übergabe auf Papier oder als
Abänderungsvertrag), entsteht daraus auf Wunsch ein eigenes Dokument „Abänderungsvertrag vom <Datum>“,
das NUR die Unterschiede zwischen der unterschriebenen und der aktuellen Fassung nennt („bisher → neu“).

Hier steht:
  * welche Vertragsfelder verglichen werden und wie sie heißen (FELDER),
  * der Vergleich zweier Fassungen (aenderungen),
  * Nachlackierung aus der Lackdicke-Messung des Fahrers (LACK_AUFFAELLIG_AB_UM, lack_befund, ...),
  * fehlende Ausstattung aus dem Abholprotokoll (ausstattung_entfaellt).
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional

#: Lackdicke, AB DER eine Messung als Nachlackierung gilt (Mikrometer). Werkslack liegt meist bei
#: 80–160 µm; darüber ist mit großer Wahrscheinlichkeit nachlackiert bzw. gespachtelt. Ahmad kann den
#: Wert hier ändern — er gilt für die Abweichungsliste der Freigabe, die neue Vertragsfassung nach der
#: Abholung und den Abänderungsvertrag gleichermaßen.
LACK_AUFFAELLIG_AB_UM = 160

#: Höchstzahl Änderungszeilen in einem Abänderungsvertrag (DoS-Bremse, praktisch nie erreicht).
AENDERUNGEN_MAX = 200

#: Unbekannter / leerer Wert in der Liste „bisher → neu“.
KEINE_ANGABE = "keine Angabe"

# (Vertragsfeld, Beschriftung, Art). Nur diese Felder zählen als Änderung — interne Angaben (Fassung, Logo,
# Design, eingefrorene Käuferdaten, Notizen, Empfangs-Kästchen, Vertragstext/AGB) gehören nicht hinein.
FELDER = (
    ("purchase_price", "Kaufpreis", "euro"),
    ("payment_method", "Zahlungsart", "text"),
    ("show_vat", "MwSt-Ausweis", "bool"),
    ("seller_name", "Verkäufer", "text"),
    ("seller_address", "Anschrift des Verkäufers", "text"),
    ("seller_zip", "PLZ des Verkäufers", "text"),
    ("seller_city", "Ort des Verkäufers", "text"),
    ("seller_phone", "Telefon des Verkäufers", "text"),
    ("seller_email", "E-Mail des Verkäufers", "text"),
    ("id_document", "Ausweis des Verkäufers", "text"),
    ("vehicle_make", "Hersteller", "text"),
    ("vehicle_model", "Typ", "text"),
    ("vehicle_category", "Kategorie", "text"),
    ("vehicle_first_registration", "Erstzulassung", "text"),
    ("vehicle_mileage", "Kilometerstand", "km"),
    ("vehicle_fuel", "Kraftstoff", "text"),
    ("vehicle_gearbox", "Getriebe", "text"),
    ("vehicle_power_kw", "Leistung (kW)", "text"),
    ("vehicle_power_ps", "Leistung (PS)", "text"),
    ("vehicle_displacement", "Hubraum", "text"),
    ("vehicle_color", "Farbe", "text"),
    ("vehicle_doors", "Türen", "text"),
    ("vehicle_seats", "Sitze", "text"),
    ("vehicle_vin", "FIN", "text"),
    ("vehicle_license_plate", "Kennzeichen", "text"),
    ("previous_owners", "Anzahl Halter", "text"),
    ("hu_valid", "HU/AU", "text"),
    ("hu_until", "HU gültig bis", "text"),
    ("accident_free", "Unfallfrei", "text"),
    ("accident_location", "Unfallschaden", "text"),
    ("nachlackierungsfrei", "Nachlackierungsfrei", "text"),
    ("service_book", "Scheckheftgepflegt", "scheckheft"),
    ("service_book_until", "Scheckheft bis", "text"),
    ("tires", "Bereifung", "text"),
    ("eu_import", "EU-Import", "text"),
    ("drivable", "Fahrtauglich", "text"),
    ("commercial_since_ez", "Gewerblich genutzt seit EZ", "text"),
    ("technik_zustand", "Motor/Getriebe/Kupplung", "technik"),
    ("technik_schaden_text", "Schaden an Motor/Getriebe/Kupplung", "text"),
    ("zulassung", "Zulassung", "zulassung"),
    ("schluessel_anzahl", "Schlüsselanzahl", "text"),
    ("pickup_date", "Abholdatum", "datum"),
    ("pickup_time", "Abholzeit", "text"),
    ("vehicle_damage_note", "Hinweis zu Schäden", "lang"),
    ("sonstige_vereinbarung", "Zusätzliche Vereinbarung", "lang"),
    ("additional_terms", "Besondere Vereinbarungen", "lang"),
    ("vehicle_description", "Fahrzeugbeschreibung", "lang"),
)
_LABEL = {f: l for f, l, _a in FELDER}


def _leer(wert: Any) -> bool:
    return wert is None or (isinstance(wert, str) and not wert.strip()) or wert == []


def _zahl(wert: Any) -> Optional[float]:
    if wert is None or wert == "" or isinstance(wert, bool):
        return None
    if isinstance(wert, (int, float)):
        return float(wert)
    s = re.sub(r"[^0-9,.\-]", "", str(wert))
    if not s:
        return None
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    elif s.count(".") > 1 or re.fullmatch(r"\d{1,3}\.\d{3}", s):
        s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


def euro(wert: Any) -> str:
    """8500 -> "8.500 €", 8500.5 -> "8.500,50 €" (deutsches Format)."""
    n = _zahl(wert)
    if n is None:
        return KEINE_ANGABE
    if abs(n - round(n)) < 0.005:
        text = f"{int(round(n)):,}".replace(",", ".")
    else:
        text = f"{n:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{text} €"


def km(wert: Any) -> str:
    n = _zahl(wert)
    if n is None:
        return KEINE_ANGABE if _leer(wert) else str(wert).strip()
    return f"{int(round(n)):,} km".replace(",", ".")


def _datum(wert: Any) -> str:
    s = str(wert or "").strip()
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2}).*", s)
    return f"{m.group(3)}.{m.group(2)}.{m.group(1)}" if m else (s or KEINE_ANGABE)


def wert_text(art: str, wert: Any) -> str:
    """Ein Vertragswert so, wie er im Abänderungsvertrag steht."""
    if art == "euro":
        return euro(wert)
    if art == "km":
        return km(wert)
    if art == "datum":
        return _datum(wert)
    if art == "bool":
        return "Ja" if wert is True or str(wert).strip().lower() in ("true", "ja", "1") else "Nein"
    if _leer(wert):
        return KEINE_ANGABE
    s = " ".join(str(wert).split()) if art != "lang" else str(wert).strip()
    if art == "scheckheft":
        return {"ja": "Ja", "nein": "Nein", "teilweise": "Teilweise"}.get(s.lower(), s)
    if art == "technik":
        return "alles in Ordnung" if s.lower() == "in ordnung" else s
    if art == "zulassung":
        return s[:1].upper() + s[1:]
    return s


def _gleich(art: str, a: Any, b: Any) -> bool:
    if art in ("euro", "km"):
        za, zb = _zahl(a), _zahl(b)
        if za is None or zb is None:
            return _leer(a) == _leer(b) and (za is None) == (zb is None)
        return abs(za - zb) < (0.005 if art == "euro" else 0.5)
    if art == "bool":
        return wert_text(art, a) == wert_text(art, b)
    norm = (lambda w: "" if _leer(w) else " ".join(str(w).split()).lower())
    return norm(a) == norm(b)


def _schaden_zeilen(cd: dict) -> List[str]:
    from pdf_service import schaden_zeilen
    raus = []
    for z in schaden_zeilen(cd or {}):
        z = re.sub(r"^[•\-\*\s]+", "", str(z)).strip()
        if z:
            raus.append(" ".join(z.split()))
    return raus


def _zeile(feld: str, label: str, bisher: str, neu: str, art: str = "wert") -> Dict[str, str]:
    return {"feld": feld, "label": label, "bisher": bisher, "neu": neu, "art": art}


# ------------------------------------------------------------------ Lackdicke / Nachlackierung
def lack_auffaellig(messungen: Optional[Iterable[Any]], schwelle: int = LACK_AUFFAELLIG_AB_UM) -> List[Dict[str, Any]]:
    """Die Messungen ÜBER der Schwelle (Bauteil + Wert), höchster Wert zuerst, je Bauteil nur der höchste."""
    je_bauteil: Dict[str, int] = {}
    for m in messungen or []:
        if not isinstance(m, dict):
            continue
        w = m.get("wert_um")
        if isinstance(w, bool) or not isinstance(w, (int, float)) or w <= schwelle:
            continue
        zone = " ".join(str(m.get("zone") or "Bauteil").split())[:80] or "Bauteil"
        je_bauteil[zone] = max(int(w), je_bauteil.get(zone, 0))
    return [{"zone": z, "wert_um": w} for z, w in sorted(je_bauteil.items(), key=lambda x: (-x[1], x[0]))]


def lack_text(auffaellig: List[Dict[str, Any]]) -> str:
    """„Ja, Tür vorne links (210 µm), Motorhaube (180 µm)“ — leer, wenn nichts auffällig ist."""
    if not auffaellig:
        return ""
    return "Ja, " + ", ".join(f"{m['zone']} ({m['wert_um']} µm)" for m in auffaellig[:20])


def lack_korrektur(messungen: Optional[Iterable[Any]]) -> Dict[str, str]:
    """Für die neue Vertragsfassung nach der Abholung (routes.protocols.protokoll_korrekturen): ist eine Messung
    auffällig, ist das Auto NICHT nachlackierungsfrei. Immer gesetzt, solange das Protokoll es sagt — die
    Neuerzeugung vergleicht selbst mit dem Vertrag (gleicher Wert ändert nichts)."""
    return {"nachlackierungsfrei": "Nein"} if lack_auffaellig(messungen) else {}


def lack_befund(messungen: Optional[Iterable[Any]], vertrag: Optional[dict]) -> Optional[Dict[str, Any]]:
    """Für die Abweichungsliste des Büros (Freigaben) — schon vor der Unterschrift: None, wenn keine Messung
    auffällig ist; sonst {"text": "Ja, …", "messungen": [...], "vertrag": "Ja"|"Nein"|"", "abweichend": bool,
    "schwelle_um": n}. abweichend = der Vertrag sagt nicht schon „Nachlackierungsfrei: Nein“."""
    auff = lack_auffaellig(messungen)
    if not auff:
        return None
    im_vertrag = str((vertrag or {}).get("nachlackierungsfrei") or "").strip()
    return {"text": lack_text(auff), "messungen": auff, "vertrag": im_vertrag,
            "abweichend": im_vertrag.lower() != "nein", "schwelle_um": LACK_AUFFAELLIG_AB_UM}


# ------------------------------------------------------------------ Ausstattung aus dem Protokoll
def ausstattung_entfaellt(protokoll: Optional[dict]) -> List[str]:
    """Ausstattung, die der Fahrer vor Ort als „fehlt“ markiert hat (features: False oder "fehlt")."""
    merkmale = (protokoll or {}).get("features") or {}
    if not isinstance(merkmale, dict):
        return []
    raus = []
    for name, w in merkmale.items():
        text = str(w).strip().lower() if isinstance(w, str) else ""
        if w is False or text in ("fehlt", "false", "nein"):
            n = " ".join(str(name).split())[:80]
            if n:
                raus.append(n)
    return sorted(dict.fromkeys(raus))


# ------------------------------------------------------------------ Vergleich zweier Fassungen
def aenderungen(bisher: Optional[dict], neu: Optional[dict], *, protokoll: Optional[dict] = None,
                text_ersetzen=None) -> List[Dict[str, str]]:
    """Was sich zwischen der unterschriebenen Fassung (bisher) und der aktuellen (neu) geändert hat — als Liste
    {"feld", "label", "bisher", "neu", "art"}. art: "wert" (bisher → neu), "neu" (neuer Schaden), "entfaellt"
    (Schaden/Ausstattung entfällt), "lack" (Nachlackierung). Leer = keine Änderung = kein Abänderungsvertrag.

    protokoll: das finale Abholprotokoll, wenn es NACH der unterschriebenen Fassung entstand — dann kommen fehlende
    Ausstattung und eine auffällige Lackdicke-Messung dazu (beides steht nicht in den Vertragsfeldern).

    text_ersetzen(text, vertragsdaten): setzt Platzhalter ({abholdatum}, {ort} …) in den langen Texten ein, wie
    sie im Kaufvertrag stehen. Verglichen wird der Rohtext (sonst stuende ein neues Abholdatum doppelt drin),
    angezeigt der eingesetzte — ein Kunde liest nie „{abholdatum}“."""
    a, b = dict(bisher or {}), dict(neu or {})

    def anzeige(art: str, wert: Any, daten: dict) -> str:
        if art == "lang" and text_ersetzen and isinstance(wert, str) and "{" in wert:
            try:
                wert = text_ersetzen(wert, daten)
            except Exception:  # noqa: BLE001 — Anzeige darf den Vergleich nie kippen
                pass
        return wert_text(art, wert)
    raus: List[Dict[str, str]] = []
    lack = lack_auffaellig((protokoll or {}).get("lackmessungen"))
    for feld, label, art in FELDER:
        if feld == "nachlackierungsfrei" and lack and str(a.get(feld) or "").strip().lower() != "nein":
            continue                                    # steht unten als „Nachlackierung: Nein → Ja, …“
        if _gleich(art, a.get(feld), b.get(feld)):
            continue
        raus.append(_zeile(feld, label, anzeige(art, a.get(feld), a), anzeige(art, b.get(feld), b)))
    alt_s, neu_s = _schaden_zeilen(a), _schaden_zeilen(b)
    alt_set = {z.lower() for z in alt_s}
    neu_set = {z.lower() for z in neu_s}
    for z in neu_s:
        if z.lower() not in alt_set:
            raus.append(_zeile("schaden", "Neuer Schaden", "", z, "neu"))
    for z in alt_s:
        if z.lower() not in neu_set:
            raus.append(_zeile("schaden", "Schaden entfällt", z, "", "entfaellt"))
    if protokoll:
        for name in ausstattung_entfaellt(protokoll):
            raus.append(_zeile("ausstattung", "Ausstattung entfällt", name, "", "entfaellt"))
        if lack and str(a.get("nachlackierungsfrei") or "").strip().lower() != "nein":
            bisher_text = "Nein" if str(a.get("nachlackierungsfrei") or "").strip().lower() == "ja" else KEINE_ANGABE
            raus.append(_zeile("nachlackierung", "Nachlackierung", bisher_text, lack_text(lack), "lack"))
    return raus[:AENDERUNGEN_MAX]


def zeile_text(z: Dict[str, str]) -> str:
    """Eine Änderung als Satz (PDF, E-Mail, Oberfläche): „Kaufpreis: 8.500 € → 8.100 €“,
    „Neuer Schaden: Kratzer: Tür hinten links“, „Ausstattung entfällt: Navigationssystem“."""
    art = z.get("art") or "wert"
    if art == "neu":
        return f"{z.get('label')}: {z.get('neu')}"
    if art == "entfaellt":
        return f"{z.get('label')}: {z.get('bisher')}"
    return f"{z.get('label')}: {z.get('bisher')} → {z.get('neu')}"
