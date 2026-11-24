# -*- coding: utf-8 -*-
"""Ueberweisung mit GiroCode im Abholauftrag (Wunsch Ahmad 24.11.2026).

Das Buero ueberweist den Kaufpreis direkt aus dem Abholauftrag — ohne Abtippen
und nie versehentlich mit einem alten Preis.

  * Der Fahrer erfasst vor Ort IBAN und Kontoinhaber des Verkaeufers (nur wenn
    laut Vertrag per Ueberweisung bezahlt wird). Der Server prueft die IBAN
    (Land im SEPA-Raum, Laenge je Land, Pruefziffer Mod-97) und speichert sie
    ohne Leerzeichen in Grossbuchstaben.
  * Der BETRAG kommt nur vom Server und baut auf der vorhandenen Vor-Ort-Logik
    auf (keine zweite Wahrheit): fest ist er, sobald der Chef das Abhol-
    protokoll freigegeben hat (dann gilt neuer_preis bzw. der Vertragspreis vor
    der Abholung — derselbe Preis, den Verkaeufer und Fahrer unterschreiben),
    oder — bei einer Abholung ohne Protokoll — sobald der Termin abgeholt/
    erledigt ist (final_price des Termins, sonst der Vertragspreis). Vorher ist
    der Betrag "offen" und der GiroCode traegt KEINEN Betrag.
  * "Als ueberwiesen markieren" ist atomar (zweiter Versuch 409), haelt
    Zeitpunkt, Benutzer, Betrag, Vertragsfassung und Protokollstand fest und
    verlangt den Stand, den das Buero gesehen hat (Betrag + Kontodaten) —
    aendert sich dazwischen etwas, 409 statt Ueberweisung eines alten Preises.
  * Persoenliche Daten (IBAN, Kontoinhaber) liegen NUR in der Sammlung
    `ueberweisungen` — nie im Marktplatz, im Vergleich, in Lesungen oder in
    Log-/Audit-Zeilen — und gehen mit dem Kaufvertrag bzw. nach der Frist
    (VERTRAG_AUFBEWAHRUNG_TAGE, 60 Tage) weg (cleanup_service).

Reine Funktionen (IBAN, Namen, GiroCode) sind ohne Datenbank testbar.
"""
from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from deps import db, now_iso

log = logging.getLogger("autohandel.ueberweisung")

SAMMLUNG = "ueberweisungen"

# --------------------------------------------------------------------- IBAN
#: Laender des SEPA-Raums mit IBAN-Laenge. Nur diese gehen als SEPA-
#: Ueberweisung (und damit als GiroCode, Dienst "SCT").
IBAN_LAENGEN: Dict[str, int] = {
    "AD": 24, "AL": 28, "AT": 20, "BE": 16, "BG": 22, "CH": 21, "CY": 28,
    "CZ": 24, "DE": 22, "DK": 18, "EE": 20, "ES": 24, "FI": 18, "FR": 27,
    "GB": 22, "GI": 23, "GR": 27, "HR": 21, "HU": 28, "IE": 22, "IS": 26,
    "IT": 27, "LI": 21, "LT": 20, "LU": 20, "LV": 21, "MC": 27, "MD": 24,
    "ME": 22, "MK": 19, "MT": 31, "NL": 18, "NO": 15, "PL": 28, "PT": 25,
    "RO": 24, "SE": 24, "SI": 19, "SK": 24, "SM": 27, "VA": 22,
}

LAENDER: Dict[str, str] = {
    "AD": "Andorra", "AL": "Albanien", "AT": "Österreich", "BE": "Belgien",
    "BG": "Bulgarien", "CH": "der Schweiz", "CY": "Zypern", "CZ": "Tschechien",
    "DE": "Deutschland", "DK": "Dänemark", "EE": "Estland", "ES": "Spanien",
    "FI": "Finnland", "FR": "Frankreich", "GB": "Großbritannien", "GI": "Gibraltar",
    "GR": "Griechenland", "HR": "Kroatien", "HU": "Ungarn", "IE": "Irland",
    "IS": "Island", "IT": "Italien", "LI": "Liechtenstein", "LT": "Litauen",
    "LU": "Luxemburg", "LV": "Lettland", "MC": "Monaco", "MD": "Moldau",
    "ME": "Montenegro", "MK": "Nordmazedonien", "MT": "Malta", "NL": "den Niederlanden",
    "NO": "Norwegen", "PL": "Polen", "PT": "Portugal", "RO": "Rumänien",
    "SE": "Schweden", "SI": "Slowenien", "SK": "der Slowakei", "SM": "San Marino",
    "VA": "dem Vatikan",
}

_IBAN_ZEICHEN = re.compile(r"^[A-Z]{2}[0-9]{2}[A-Z0-9]+$")


def iban_normalisieren(roh: Any) -> str:
    """Leerzeichen, Bindestriche und Punkte raus, Grossbuchstaben."""
    return re.sub(r"[\s\-.]", "", str(roh or "")).upper()


def iban_mod97_ok(iban: str) -> bool:
    """Pruefziffer nach ISO 13616: Laenderkennung + Pruefziffer ans Ende,
    Buchstaben als Zahlen (A=10 … Z=35), Rest bei Division durch 97 == 1."""
    umgestellt = iban[4:] + iban[:4]
    try:
        zahl = "".join(str(int(z, 36)) for z in umgestellt)
    except ValueError:
        return False
    return int(zahl) % 97 == 1


def iban_pruefen(roh: Any) -> str:
    """Normalisierte IBAN oder ValueError mit einem Satz fuer den Fahrer."""
    iban = iban_normalisieren(roh)
    if not iban:
        raise ValueError("Bitte die IBAN eingeben.")
    if len(iban) > 34 or not _IBAN_ZEICHEN.match(iban):
        raise ValueError("Die IBAN enthält ungültige Zeichen — sie beginnt mit zwei Buchstaben "
                         "(z. B. DE) und zwei Ziffern.")
    land = iban[:2]
    soll = IBAN_LAENGEN.get(land)
    if soll is None:
        raise ValueError(f"Konten mit „{land}“ gehen hier nicht — nur Konten aus dem SEPA-Raum.")
    if len(iban) != soll:
        raise ValueError(f"Eine IBAN aus {LAENDER.get(land, land)} hat {soll} Stellen — "
                         f"eingegeben sind {len(iban)}.")
    if not iban_mod97_ok(iban):
        raise ValueError("Die IBAN stimmt nicht — bitte Zeichen für Zeichen prüfen.")
    return iban


def iban_formatiert(iban: Optional[str]) -> str:
    """Zum Lesen: Vierergruppen ("DE89 3704 0044 0532 0130 00")."""
    s = iban_normalisieren(iban)
    return " ".join(s[i:i + 4] for i in range(0, len(s), 4))


# --------------------------------------------------------------- Kontoinhaber
KONTOINHABER_MAX = 70          # EPC069-12: Name des Empfaengers hoechstens 70 Zeichen


def kontoinhaber_pruefen(roh: Any) -> str:
    text = "".join(z for z in str(roh or "") if unicodedata.category(z)[0] != "C")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) < 2:
        raise ValueError("Bitte den Namen des Kontoinhabers eingeben.")
    if len(text) > KONTOINHABER_MAX:
        raise ValueError(f"Der Name des Kontoinhabers ist zu lang (höchstens {KONTOINHABER_MAX} Zeichen).")
    return text


_TITEL = {"herr", "herrn", "frau", "hr", "fr", "dr", "prof", "dipl", "ing", "med"}


def _teile(name: str, umlaut_als_e: bool) -> frozenset:
    s = str(name or "").lower()
    if umlaut_als_e:
        s = s.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
    s = s.replace("ß", "ss")
    s = "".join(z for z in unicodedata.normalize("NFKD", s) if not unicodedata.combining(z))
    return frozenset(t for t in re.findall(r"[a-z]+", s) if len(t) > 1 and t not in _TITEL)


def namen_passen(a: Optional[str], b: Optional[str]) -> bool:
    """Tolerant: Gross/Klein, Umlaute (ä = ae = a), Titel, Satzzeichen und die
    Reihenfolge von Vor- und Nachname spielen keine Rolle. Ein zusaetzlicher
    Vorname ("Max Peter Mustermann" / "Max Mustermann") gilt als passend.
    Fehlt einer der Namen, gibt es nichts zu vergleichen (passt)."""
    for e in (True, False):
        x, y = _teile(a, e), _teile(b, e)
        if not x or not y:
            return True
        if x == y:
            return True
        klein, gross = (x, y) if len(x) <= len(y) else (y, x)
        if len(klein) >= 2 and klein <= gross:
            return True
    return False


# --------------------------------------------------------------- Zahlungsart
def zahlungsart(vertrag: Optional[dict]) -> Dict[str, Any]:
    """Zahlungsart laut Vertrag. "Bar" allein heisst: keine Ueberweisung.
    Alles andere ("Überweisung", "Echtzeitüberweisung", der alte Standard
    "Bar / Überweisung", leer/ohne Vertrag) laesst die Ueberweisung zu."""
    v = vertrag or {}
    roh = str(v.get("payment_method") or (v.get("contract_data") or {}).get("payment_method") or "").strip()
    klein = roh.lower()
    nur_bar = klein in ("bar", "barzahlung", "bar zahlung", "in bar")
    return {"text": roh, "ueberweisung": not nur_bar, "echtzeit": "echtzeit" in klein}


# ------------------------------------------------------------------ GiroCode
ZWECK_MAX = 140                # EPC069-12: unstrukturierter Verwendungszweck


def betrag_text(cents: Optional[int]) -> str:
    """Deutsch zum Lesen: 15.000,00 €"""
    if cents is None:
        return ""
    euro, rest = divmod(int(cents), 100)
    return f"{euro:,}".replace(",", ".") + f",{rest:02d} €"


def verwendungszweck(fahrzeug: str = "", fin: str = "", kennzeichen: str = "",
                     vertragsnummer: str = "") -> str:
    """Fahrzeug + FIN, sobald sie erfasst ist; sonst Kennzeichen/Vertragsnummer."""
    teile = ["Kaufpreis"]
    if fahrzeug:
        teile.append(fahrzeug)
    if fin:
        teile.append(f"FIN {fin}")
    else:
        if kennzeichen:
            teile.append(kennzeichen)
        if vertragsnummer:
            teile.append(f"Vertrag {vertragsnummer}")
    zweck = re.sub(r"\s+", " ", " ".join(teile)).strip()
    return zweck[:ZWECK_MAX]


def girocode_text(name: str, iban: str, betrag_cents: Optional[int], zweck: str,
                  bic: str = "") -> str:
    """Inhalt des GiroCodes (EPC069-12, Version 002, UTF-8, SEPA-Ueberweisung).

    Zeilen (getrennt mit LF):
      1 BCD   2 002   3 1 (UTF-8)   4 SCT   5 BIC (in 002 optional, leer)
      6 Name  7 IBAN  8 Betrag "EUR15000.00" (leer = Betrag gibt der Zahler ein)
      9 Zweck-Code (leer)  10 strukturierte Referenz (leer)  11 Verwendungszweck
    """
    if betrag_cents is not None and not (1 <= int(betrag_cents) <= 99_999_999_999):
        raise ValueError("Betrag ausserhalb des GiroCode-Bereichs")
    betrag = "" if betrag_cents is None else f"EUR{int(betrag_cents) // 100}.{int(betrag_cents) % 100:02d}"
    zeilen = ["BCD", "002", "1", "SCT", (bic or "").strip().upper(),
              str(name or "").strip()[:KONTOINHABER_MAX], iban_normalisieren(iban),
              betrag, "", "", str(zweck or "").strip()[:ZWECK_MAX]]
    return "\n".join(zeilen)


def girocode_svg(text: str) -> str:
    """QR-Code als SVG (Fehlerkorrektur M wie von EPC069-12 verlangt). Schwarz
    auf Weiss in jeder Ansicht — sonst lesen Banking-Apps ihn nicht sicher."""
    import segno
    qr = segno.make_qr(text, error="m", boost_error=False, encoding="utf-8")
    return qr.svg_inline(scale=5, border=4, dark="#000000", light="#ffffff")


# ------------------------------------------------------------ Datenzugriff
#: Protokollstaende, in denen der Preis vor Ort feststeht (Freigabe des Chefs
#: ist Pflicht vor den Unterschriften — protocols.finalize_protocol).
PREIS_FEST_STATI = ("freigegeben", "wird_abgeschlossen", "final")
TERMIN_ABGEHOLT = ("abgeholt", "erledigt")


async def _aktuelles_protokoll(appt_id: str) -> Optional[dict]:
    return await db.pickup_protocols.find_one(
        {"appointment_id": appt_id, "superseded": {"$ne": True}},
        {"_id": 0, "id": 1, "status": 1, "neuer_preis": 1, "freigabe_stand": 1,
         "vehicle_check": 1, "seller_name": 1, "version": 1},
        sort=[("version", -1)])


async def _vertrag(appt: dict) -> Optional[dict]:
    if not appt.get("contract_id"):
        return None
    return await db.generated_pdfs.find_one(
        {"id": appt["contract_id"], "dealer_id": appt.get("dealer_id")},
        {"_id": 0, "id": 1, "version": 1, "user_id": 1, "contract_no": 1, "seller_name": 1,
         "payment_method": 1, "make": 1, "model": 1, "vehicle_make": 1, "vehicle_model": 1,
         "purchase_price": 1, "contract_data": 1})


def betrag_aus(appt: dict, vertrag: Optional[dict], protokoll: Optional[dict]) -> Dict[str, Any]:
    """Der Betrag, der ueberwiesen wird — oder "offen".

    Baut auf der vorhandenen Vor-Ort-Logik auf:
      * Protokoll freigegeben/abgeschlossen: neuer_preis (Chef oder Vorschlag
        des Fahrers, bei der Freigabe uebernommen), sonst der Vertragspreis VOR
        der Abholung (protocols.vertragspreis_vor_abholung) — genau der Preis,
        den das Protokoll ueber den Unterschriften traegt.
      * Abholung ohne (fertiges) Protokoll, Termin abgeholt/erledigt:
        final_price des Termins ("vor Ort vereinbarter Preis"), sonst der
        Vertragspreis.
      * sonst: offen.
    """
    from routes.protocols import vertragspreis_vor_abholung
    cdata = (vertrag or {}).get("contract_data") or {}
    basis = vertragspreis_vor_abholung(cdata)
    if basis is None:
        basis = (vertrag or {}).get("purchase_price")
    preis, quelle, stand = None, None, None
    if protokoll and protokoll.get("status") in PREIS_FEST_STATI:
        preis = protokoll.get("neuer_preis") if protokoll.get("neuer_preis") is not None else basis
        quelle = "protokoll"
        stand = f"p:{protokoll.get('id')}:{protokoll.get('freigabe_stand') or ''}"
    elif (appt.get("status") or "") in TERMIN_ABGEHOLT:
        preis = appt.get("final_price") if appt.get("final_price") is not None else basis
        quelle = "termin"
        stand = f"t:{appt.get('id')}"
    try:
        cents = int(round(float(preis) * 100)) if preis is not None else None
    except (TypeError, ValueError):
        cents = None
    if not cents or cents <= 0:
        return {"fest": False, "betrag_cents": None, "quelle": None, "stand": "offen"}
    return {"fest": True, "betrag_cents": cents, "quelle": quelle, "stand": f"{stand}:{cents}"}


def _fin(vehicle: dict, vertrag: Optional[dict], protokoll: Optional[dict]) -> str:
    """FIN: vom Fahrer vor Ort korrigiert, sonst laut Vertrag/Inserat."""
    eintrag = ((protokoll or {}).get("vehicle_check") or {}).get("vin")
    if isinstance(eintrag, dict) and str(eintrag.get("status") or "") == "weicht ab":
        wert = re.sub(r"\s+", "", str(eintrag.get("value") or "")).upper()
        if wert:
            return wert
    import protokoll_vergleich as PV
    try:
        return str(PV.vertragswerte(vehicle, (vertrag or {}).get("contract_data") or {})
                   .get("vin", {}).get("wert") or "")
    except Exception:  # noqa: BLE001 — Verwendungszweck nie daran scheitern lassen
        return ""


async def _fahrzeug_daten(appt: dict) -> dict:
    if not appt.get("vehicle_id"):
        return {}
    v = await db.vehicles.find_one({"id": appt["vehicle_id"], "dealer_id": appt.get("dealer_id")},
                                   {"_id": 0, "data": 1})
    return dict((v or {}).get("data") or {})


def _fahrzeug_name(vehicle: dict, vertrag: Optional[dict]) -> str:
    from vertrag_platzhalter import marke_modell
    marke, modell = marke_modell(vertrag or {}) if vertrag else ("", "")
    marke = marke or str(vehicle.get("make_label") or vehicle.get("make") or "")
    modell = modell or str(vehicle.get("model_label") or vehicle.get("model") or "")
    return " ".join(x for x in (marke.strip(), modell.strip()) if x)


def _stand(betrag: Dict[str, Any], doc: Optional[dict]) -> str:
    """Was das Buero gesehen hat: Betrag-Stand + Kontodaten (ohne die IBAN
    selbst preiszugeben — nur als Pruefsumme)."""
    roh = "|".join([betrag.get("stand") or "offen", (doc or {}).get("iban") or "",
                    (doc or {}).get("kontoinhaber") or ""])
    return hashlib.sha256(roh.encode("utf-8")).hexdigest()[:24]


async def lesen(appt_id: str, dealer_id: str) -> Optional[dict]:
    return await getattr(db, SAMMLUNG).find_one(
        {"appointment_id": appt_id, "dealer_id": dealer_id}, {"_id": 0})


async def lage(appt: dict) -> Dict[str, Any]:
    """Alles, was Buero-Karte, Fahrer-App und Meldungen brauchen."""
    doc = await lesen(appt["id"], appt.get("dealer_id"))
    vertrag = await _vertrag(appt)
    protokoll = await _aktuelles_protokoll(appt["id"])
    vehicle = await _fahrzeug_daten(appt)
    betrag = betrag_aus(appt, vertrag, protokoll)
    za = zahlungsart(vertrag)
    fz = _fahrzeug_name(vehicle, vertrag)
    fin = _fin(vehicle, vertrag, protokoll)
    cdata = (vertrag or {}).get("contract_data") or {}
    kennz = str(cdata.get("vehicle_license_plate") or vehicle.get("license_plate") or "").strip()
    zweck = verwendungszweck(fz, fin, kennz, str((vertrag or {}).get("contract_no") or "").strip())
    verkaeufer = [n for n in {(protokoll or {}).get("seller_name"), (vertrag or {}).get("seller_name"),
                              cdata.get("seller_name"), appt.get("seller_name")} if n and str(n).strip()]
    return {"doc": doc, "vertrag": vertrag, "protokoll": protokoll, "betrag": betrag,
            "zahlungsart": za, "fahrzeug": fz, "fin": fin, "zweck": zweck,
            "verkaeufer": sorted(str(n).strip() for n in verkaeufer)}


def warnungen(l: Dict[str, Any]) -> List[Dict[str, str]]:
    doc = l.get("doc") or {}
    raus: List[Dict[str, str]] = []
    inhaber = doc.get("kontoinhaber")
    if inhaber and l.get("verkaeufer") and not any(namen_passen(inhaber, n) for n in l["verkaeufer"]):
        raus.append({"art": "name", "text": "Kontoinhaber weicht vom Verkäufer im Vertrag ab "
                                            f"({' / '.join(l['verkaeufer'])}). Bitte vor dem Überweisen klären."})
    iban = doc.get("iban") or ""
    if iban and iban[:2] != "DE":
        raus.append({"art": "ausland", "text": f"Konto aus {LAENDER.get(iban[:2], iban[:2])} — "
                                               "Überweisung kann länger dauern."})
    ue = doc.get("ueberwiesen")
    b = l.get("betrag") or {}
    if ue and b.get("fest") and b.get("betrag_cents") != ue.get("betrag_cents"):
        raus.append({"art": "betrag_geaendert",
                     "text": f"Der Betrag hat sich nach der Überweisung geändert: überwiesen "
                             f"{betrag_text(ue.get('betrag_cents'))}, jetzt {betrag_text(b.get('betrag_cents'))}."})
    return raus


def buero_sicht(l: Dict[str, Any], *, darf_zuruecknehmen: bool) -> Dict[str, Any]:
    doc = l.get("doc") or {}
    b = l["betrag"]
    hat_konto = bool(doc.get("iban") and doc.get("kontoinhaber"))
    ue = doc.get("ueberwiesen")
    out: Dict[str, Any] = {
        "zahlungsart": l["zahlungsart"],
        "kontodaten": hat_konto,
        "empfaenger": doc.get("kontoinhaber") or "",
        "iban": doc.get("iban") or "",
        "iban_formatiert": iban_formatiert(doc.get("iban")) if doc.get("iban") else "",
        "kontodaten_am": doc.get("erfasst_am"),
        "betrag_fest": b["fest"],
        "betrag_cents": b["betrag_cents"],
        "betrag_text": betrag_text(b["betrag_cents"]) if b["fest"] else "",
        "betrag_quelle": b["quelle"],
        "verwendungszweck": l["zweck"],
        "warnungen": warnungen(l),
        "stand": _stand(b, doc),
        "ueberwiesen": ({"am": ue.get("am"), "von_name": ue.get("von_name") or "",
                         "betrag_cents": ue.get("betrag_cents"),
                         "betrag_text": betrag_text(ue.get("betrag_cents")),
                         "vertrag_version": ue.get("contract_version")} if ue else None),
        "darf_zuruecknehmen": bool(ue) and darf_zuruecknehmen,
        "girocode_svg": "",
    }
    if hat_konto:
        try:
            out["girocode_svg"] = girocode_svg(girocode_text(
                doc["kontoinhaber"], doc["iban"], b["betrag_cents"] if b["fest"] else None, l["zweck"]))
        except Exception:  # noqa: BLE001 — Karte ohne QR besser als 500
            log.exception("GiroCode fuer Termin nicht erzeugt")
    return out


def fahrer_sicht(l: Dict[str, Any]) -> Dict[str, Any]:
    doc = l.get("doc") or {}
    ue = doc.get("ueberwiesen")
    return {
        "zahlungsart": l["zahlungsart"],
        "kontoinhaber": doc.get("kontoinhaber") or "",
        "iban_formatiert": iban_formatiert(doc.get("iban")) if doc.get("iban") else "",
        "erfasst_am": doc.get("erfasst_am"),
        "ueberwiesen": ({"am": ue.get("am"), "betrag_cents": ue.get("betrag_cents"),
                         "betrag_text": betrag_text(ue.get("betrag_cents"))} if ue else None),
    }


async def fahrer_sicht_sicher(appt: dict) -> Optional[Dict[str, Any]]:
    """Fuer die Protokoll-Antwort der Fahrer-App: None statt Fehler."""
    try:
        return fahrer_sicht(await lage(appt))
    except Exception:  # noqa: BLE001 — das Protokoll darf daran nie scheitern
        log.exception("Ueberweisungs-Stand fuer die Fahrer-App nicht gelesen")
        return None


# ------------------------------------------------------------------ Meldungen
MELDUNG_TAGE = 90


async def empfaenger(appt: dict, vertrag: Optional[dict]) -> List[str]:
    """Chef der Firma und der Sucher des Vorgangs (wie Kundenportal)."""
    from deps import haupt_chef_id
    ids = {await haupt_chef_id(appt.get("dealer_id")), (vertrag or {}).get("user_id"), appt.get("created_by")}
    ids = [i for i in ids if i]
    if not ids:
        return []
    # nur Konten DIESER Firma
    return sorted([u["id"] async for u in db.users.find(
        {"id": {"$in": ids}, "dealer_id": appt.get("dealer_id")}, {"_id": 0, "id": 1})])


async def _meldung(appt: dict, vertrag: Optional[dict], typ: str, text: str) -> None:
    an = await empfaenger(appt, vertrag)
    if not an:
        return
    await db.meldungen.insert_one({
        "id": uuid.uuid4().hex, "dealer_id": appt.get("dealer_id"), "empfaenger": an, "typ": typ,
        "text": text[:500], "ref": appt.get("contract_id") or None, "termin_id": appt["id"],
        "contract_no": (vertrag or {}).get("contract_no"), "erstellt_am": now_iso(), "gelesen": {},
        "laeuft_ab": datetime.now(timezone.utc) + timedelta(days=MELDUNG_TAGE)})


async def meldungen_pruefen(appt: dict) -> List[str]:
    """Jede Meldung nur EINMAL (atomarer Merker am Ueberweisungs-Eintrag):
      * "Kontodaten liegen vor – jetzt überweisen", sobald IBAN + Inhaber da sind
      * "Betrag steht fest – jetzt überweisen", wenn der Preis erst danach feststeht
    Liefert die Typen der neu angelegten Meldungen."""
    coll = getattr(db, SAMMLUNG)
    doc = await lesen(appt["id"], appt.get("dealer_id"))
    if not doc or not (doc.get("iban") and doc.get("kontoinhaber")) or doc.get("ueberwiesen"):
        return []
    l = await lage(appt)
    b = l["betrag"]
    fz = l["fahrzeug"] or "Fahrzeug"
    neu: List[str] = []
    jetzt = now_iso()
    if not doc.get("meldung_kontodaten_am"):
        setzen = {"meldung_kontodaten_am": jetzt}
        if b["fest"]:
            setzen["meldung_betrag_am"] = jetzt          # die Kontodaten-Meldung nennt den Betrag schon
        r = await coll.update_one({"id": doc["id"], "meldung_kontodaten_am": None}, {"$set": setzen})
        if r.modified_count:
            text = (f"Kontodaten liegen vor – jetzt überweisen: {fz}, {betrag_text(b['betrag_cents'])}."
                    if b["fest"] else
                    f"Kontodaten liegen vor – {fz}. Betrag noch offen: Preis vor Ort noch nicht bestätigt.")
            await _meldung(appt, l["vertrag"], "ueberweisung_kontodaten", text)
            neu.append("ueberweisung_kontodaten")
        return neu
    if b["fest"] and not doc.get("meldung_betrag_am"):
        r = await coll.update_one({"id": doc["id"], "meldung_betrag_am": None},
                                  {"$set": {"meldung_betrag_am": jetzt}})
        if r.modified_count:
            await _meldung(appt, l["vertrag"], "ueberweisung_betrag",
                           f"Betrag steht fest – jetzt überweisen: {fz}, {betrag_text(b['betrag_cents'])}.")
            neu.append("ueberweisung_betrag")
    return neu


async def meldungen_pruefen_sicher(appt_id: str, dealer_id: str) -> None:
    """Fuer Freigabe/Terminaenderung: wirft nie (best effort)."""
    try:
        appt = await db.appointments.find_one({"id": appt_id, "dealer_id": dealer_id}, {"_id": 0})
        if appt:
            await meldungen_pruefen(appt)
    except Exception:  # noqa: BLE001
        log.exception("Ueberweisungs-Meldung zu Termin %s nicht geprueft", appt_id)


# ------------------------------------------------------------ Loeschung/Index
async def indizes_anlegen(datenbank) -> None:
    from indizes import unique_anlegen
    coll = getattr(datenbank, SAMMLUNG)
    await unique_anlegen(coll, "appointment_id", weich=True)
    await coll.create_index([("dealer_id", 1), ("contract_id", 1)])
    await coll.create_index("angelegt_am")


async def ueberweisungen_entfernen(datenbank, termin_ids: List[str],
                                   contract_id: Optional[str] = None) -> int:
    """Mit dem Kaufvertrag (bzw. dem Termin ohne Vertrag) gehen IBAN und
    Kontoinhaber — der ganze Eintrag wird geloescht."""
    oder: List[Dict[str, Any]] = []
    if termin_ids:
        oder.append({"appointment_id": {"$in": list(termin_ids)}})
    if contract_id:
        oder.append({"contract_id": contract_id})
    if not oder:
        return 0
    r = await getattr(datenbank, SAMMLUNG).delete_many({"$or": oder})
    return int(r.deleted_count)


async def ueberweisungen_nach_frist_loeschen(datenbank, now: datetime,
                                             tage: Optional[int] = None) -> int:
    """60-Tage-Regel (VERTRAG_AUFBEWAHRUNG_TAGE): Eintraege, deren Kontodaten
    vor mehr als `tage` Tagen zuerst erfasst wurden, und Eintraege ohne Termin."""
    if tage is None:
        from cleanup_service import VERTRAG_AUFBEWAHRUNG_TAGE as tage
    coll = getattr(datenbank, SAMMLUNG)
    grenze = (now - timedelta(days=int(tage))).isoformat()
    n = int((await coll.delete_many({"angelegt_am": {"$lte": grenze}})).deleted_count)
    # verwaiste Eintraege (Termin geloescht)
    ids = [d["appointment_id"] async for d in coll.find({}, {"_id": 0, "appointment_id": 1}).limit(5000)]
    if ids:
        da = set(await datenbank.appointments.distinct("id", {"id": {"$in": ids}}))
        weg = [i for i in ids if i not in da]
        if weg:
            n += int((await coll.delete_many({"appointment_id": {"$in": weg}})).deleted_count)
    return n
