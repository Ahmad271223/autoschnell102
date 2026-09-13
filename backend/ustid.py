# -*- coding: utf-8 -*-
"""USt-IdNr.: Formatpruefung je EU-Land + optionale Online-Pruefung (VIES).

Go-Live-Audit 09/2026, Punkt 40: Zwischenhaendler geben bei der
Registrierung "USt-IdNr. oder Handelsregister-Nr." an. Bisher wurde der
Wert nur gespeichert. Jetzt:
  - sieht der Wert wie eine USt-IdNr. aus (zwei Buchstaben + Nummer), wird
    das Landesformat geprueft — Tippfehler fallen sofort auf;
  - Handelsregister-Angaben (HRB 12345, FN 123456a ...) bleiben erlaubt;
  - der Super-Admin kann die Nummer beim EU-Dienst VIES pruefen lassen
    (Ergebnis + Firmenname/Adresse werden am Kaeufer gespeichert).

Keine Abhaengigkeit ausser httpx (bereits vorhanden).
"""
import re
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

# Landesformate (Ziffern/Buchstaben nach dem Laenderkuerzel), Quelle:
# EU-Kommission "VAT identification number structure".
FORMATE: Dict[str, str] = {
    "AT": r"U\d{8}",
    "BE": r"[01]\d{9}",
    "BG": r"\d{9,10}",
    "CY": r"\d{8}[A-Z]",
    "CZ": r"\d{8,10}",
    "DE": r"\d{9}",
    "DK": r"\d{8}",
    "EE": r"\d{9}",
    "EL": r"\d{9}",
    "ES": r"[A-Z0-9]\d{7}[A-Z0-9]",
    "FI": r"\d{8}",
    "FR": r"[A-Z0-9]{2}\d{9}",
    "HR": r"\d{11}",
    "HU": r"\d{8}",
    "IE": r"(\d{7}[A-Z]{1,2}|\d[A-Z+*]\d{5}[A-Z])",
    "IT": r"\d{11}",
    "LT": r"(\d{9}|\d{12})",
    "LU": r"\d{8}",
    "LV": r"\d{11}",
    "MT": r"\d{8}",
    "NL": r"\d{9}B\d{2}",
    "PL": r"\d{10}",
    "PT": r"\d{9}",
    "RO": r"\d{2,10}",
    "SE": r"\d{12}",
    "SI": r"\d{8}",
    "SK": r"\d{10}",
    "XI": r"(\d{9}|\d{12}|(GD|HA)\d{3})",   # Nordirland
    "CH": r"E\d{9}(MWST|TVA|IVA)?",        # Schweiz (kein VIES)
    "GB": r"(\d{9}|\d{12}|(GD|HA)\d{3})",  # UK (kein VIES)
}
VIES_LAENDER = set(FORMATE) - {"CH", "GB"}
_LAND_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{2,}$")
# Handelsregister-Angaben (DE: HRA/HRB/GnR/PR/VR, AT: FN, CH: CHE-…-HR): kein
# Landesformat, nur Plausibilitaet.
_REGISTER_RE = re.compile(r"^(HRA|HRB|GNR|PR|VR|FN)\d")


def normalisieren(wert: str) -> str:
    """Grossbuchstaben, ohne Leer-/Trennzeichen; 'GR' -> 'EL' (VIES-Kuerzel)."""
    s = re.sub(r"[\s.\-/]", "", (wert or "").upper())
    if s.startswith("GR"):
        s = "EL" + s[2:]
    return s


def sieht_aus_wie_ustid(wert: str) -> bool:
    """True, wenn der Wert eine USt-IdNr. sein soll (Laenderkuerzel + Nummer),
    False fuer Handelsregister-Angaben wie 'HRB 12345' oder 'FN 123456a'."""
    s = normalisieren(wert)
    # Handelsregister-Kuerzel zuerst: 'HRB 12345' wuerde sonst als Kroatien
    # (HR) + 'B12345' gelesen und abgelehnt.
    if _REGISTER_RE.match(s):
        return False
    return bool(_LAND_RE.match(s)) and s[:2] in FORMATE


def format_pruefen(wert: str) -> Tuple[Optional[str], str]:
    """Liefert (Fehlertext oder None, normalisierter Wert).

    Leere Werte und Handelsregister-Angaben sind ok (kein Fehler). Nur
    Werte, die wie eine USt-IdNr. aussehen, muessen dem Landesformat
    entsprechen.
    """
    s = normalisieren(wert)
    if not s:
        return None, ""
    if not sieht_aus_wie_ustid(wert):
        # Handelsregister o.ae. — hoechstens grob auf Plausibilitaet pruefen
        if len(s) < 3 or not re.search(r"\d", s):
            return "USt-IdNr. oder Handelsregister-Nummer bitte vollständig angeben (z.B. DE123456789 oder HRB 12345)", s
        return None, (wert or "").strip()
    land, rest = s[:2], s[2:]
    if not re.fullmatch(FORMATE[land], rest):
        beispiel = {"DE": "DE123456789", "AT": "ATU12345678", "NL": "NL123456789B01",
                    "FR": "FRXX123456789", "PL": "PL1234567890", "IT": "IT12345678901"}.get(land, f"{land}…")
        return f"USt-IdNr. hat nicht das Format von {land} (Beispiel: {beispiel})", s
    if land == "DE" and rest[0] == "0":
        return "Deutsche USt-IdNr. beginnt nie mit 0 nach 'DE'", s
    return None, s


async def vies_pruefen(wert: str, timeout: float = 12.0) -> Dict:
    """Online-Pruefung beim EU-Dienst VIES (REST). Ergebnis:
      {"status": "gueltig" | "ungueltig" | "nicht_pruefbar", "ust_id": ...,
       "name": ..., "adresse": ..., "hinweis": ..., "geprueft_am": ...}
    Nie eine Ausnahme nach aussen — Nichterreichbarkeit ist ein Ergebnis."""
    import httpx
    fehler, s = format_pruefen(wert)
    ergebnis = {"ust_id": s, "geprueft_am": datetime.now(timezone.utc).isoformat(),
                "name": None, "adresse": None, "quelle": "vies"}
    if fehler or not sieht_aus_wie_ustid(wert):
        return {**ergebnis, "status": "nicht_pruefbar",
                "hinweis": fehler or "Keine USt-IdNr. (Handelsregister-Nr.) — nur manuell prüfbar"}
    land, nummer = s[:2], s[2:]
    if land not in VIES_LAENDER:
        return {**ergebnis, "status": "nicht_pruefbar",
                "hinweis": f"{land} ist nicht im EU-Dienst VIES enthalten — bitte manuell prüfen"}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(
                "https://ec.europa.eu/taxation_customs/vies/rest-api/check-vat-number",
                json={"countryCode": land, "vatNumber": nummer},
                headers={"Accept": "application/json"})
        if r.status_code != 200:
            return {**ergebnis, "status": "nicht_pruefbar",
                    "hinweis": f"VIES antwortet mit HTTP {r.status_code} — später erneut versuchen"}
        d = r.json()
        if d.get("userError") not in (None, "VALID", "INVALID"):
            # z.B. MS_UNAVAILABLE (Mitgliedstaat offline), SERVICE_UNAVAILABLE
            return {**ergebnis, "status": "nicht_pruefbar",
                    "hinweis": f"VIES: {d.get('userError')} — Mitgliedstaat/Dienst gerade nicht erreichbar"}
        gueltig = bool(d.get("valid"))
        name = (d.get("name") or "").strip()
        adresse = " ".join((d.get("address") or "").split())
        return {**ergebnis, "status": "gueltig" if gueltig else "ungueltig",
                "name": name if name and name != "---" else None,
                "adresse": adresse if adresse and adresse != "---" else None,
                "hinweis": None if gueltig else "VIES kennt diese USt-IdNr. nicht (ungültig oder nicht für EU-Handel registriert)"}
    except Exception as exc:  # noqa: BLE001 — Netz/Timeout/JSON
        return {**ergebnis, "status": "nicht_pruefbar",
                "hinweis": f"VIES nicht erreichbar ({type(exc).__name__}) — später erneut versuchen"}
