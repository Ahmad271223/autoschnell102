# -*- coding: utf-8 -*-
"""Regelbasierte Uebernahme eindeutiger Vertragsangaben aus dem Inserat
(Wunsch Ahmad 25.09.2026, Stufe 3) — OHNE KI. Nur, was das Inserat klar sagt:

  * "2 Schluessel" / "Zweitschluessel vorhanden"      -> Schluesselanzahl
  * "HU 07/2028" / "TUEV neu" / "HU abgelaufen"      -> HU vorhanden (+ bis)
  * "lueckenlos scheckheftgepflegt" / "kein Scheckheft" -> Scheckheft
  * "scheckheftgepflegt" ALLEIN                      -> NUR ein Hinweis —
    im Vertrag heisst "Ja" "Ja, lueckenlos", und das sagt das Inserat nicht
  * unfallfrei / Unfallschaden, fahrbereit, EU-Import, Bereifung

Nicht erwaehnt oder widerspruechlich = nichts vorauswaehlen. Jeder Wert traegt
Quelle und Fundstelle ("aus Inserat uebernommen"); der Sucher sieht und
korrigiert ihn im Vertragsdialog wie jeden anderen Wert.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

import protokoll_vergleich as PV

_ZAHLWORT = {"ein": 1, "einen": 1, "einem": 1, "1": 1, "zwei": 2, "2": 2, "drei": 3, "3": 3,
             "vier": 4, "4": 4}


def _text(v: dict) -> str:
    """Beschreibung + Ausstattungsliste + bekannte Maengel, klein geschrieben."""
    teile = [str(v.get("description") or "")]
    teile += [str(x) for x in (v.get("features") or []) if x]
    teile += [str(x) for x in (v.get("known_defects") or []) if x]
    return "\n".join(teile)


def _fund(text: str, m: "re.Match", rand: int = 28) -> str:
    a, b = max(0, m.start() - rand), min(len(text), m.end() + rand)
    s = " ".join(text[a:b].split())
    return (("…" if a > 0 else "") + s + ("…" if b < len(text) else ""))[:120]


def _eintrag(wert: Any, quelle: str, fund: str) -> Dict[str, Any]:
    return {"value": wert, "source": quelle, "source_text": fund, "confidence": 1.0}


# ------------------------------------------------ einzelne Regeln
def schluessel(v: dict, text: str) -> Optional[Dict[str, Any]]:
    strukturiert = v.get("keys_count")
    if strukturiert not in (None, ""):
        z = PV.zahl(strukturiert)
        if z is not None and 1 <= z <= 9:
            return _eintrag(str(int(z)), "listing_field", f"Schlüssel: {int(z)}")
    t = text.lower()
    # "nur ein Schluessel", "kein Zweitschluessel", "Zweitschluessel fehlt" -> 1
    m = re.search(r"(nur\s+(?:ein|einen|1)\s+(?:funk|fahrzeug)?schl[üu]e?ssel|kein(?:en)?\s+zweitschl[üu]e?ssel"
                  r"|ohne\s+zweitschl[üu]e?ssel|zweitschl[üu]e?ssel\s+(?:fehlt|nicht\s+vorhanden|ist\s+nicht\s+dabei))", t)
    if m:
        return _eintrag("1", "listing_description", _fund(text, m))
    m = re.search(r"(zweitschl[üu]e?ssel\s+(?:vorhanden|dabei|ist\s+dabei|inklusive|inkl\.?)"
                  r"|(?:mit|inkl\.?|inklusive)\s+zweitschl[üu]e?ssel|beide\s+schl[üu]e?ssel)", t)
    if m:
        return _eintrag("2", "listing_description", _fund(text, m))
    m = re.search(r"\b(ein|einen|zwei|drei|vier|[1-4])\s*(?:x\s*)?(?:original|originale|originalen)?\s*"
                  r"(?:funk|fahrzeug)?schl[üu]e?ssel", t)
    if m and m.group(1) in _ZAHLWORT:
        return _eintrag(str(_ZAHLWORT[m.group(1)]), "listing_description", _fund(text, m))
    return None


def hu(v: dict, text: str) -> Dict[str, Dict[str, Any]]:
    raus: Dict[str, Dict[str, Any]] = {}
    roh = str(v.get("hu") or "").strip()
    if roh:
        mj = PV.monat_jahr_text(roh, "hu")
        if re.fullmatch(r"\d{2}/\d{4}", mj):
            raus["hu_valid"] = _eintrag("Ja", "listing_field", f"HU {mj}")
            raus["hu_until"] = _eintrag(mj, "listing_field", f"HU {mj}")
            return raus
        if roh.lower() in ("neu", "new"):
            raus["hu_valid"] = _eintrag("Ja", "listing_field", "HU neu")
            return raus
    t = text.lower()
    m = re.search(r"(?:keine|ohne|abgelaufene?[rs]?)\s+(?:hu|t[üu]v)\b|(?:hu|t[üu]v)\s*(?:/\s*au)?\s*(?:ist\s+)?abgelaufen", t)
    if m:
        raus["hu_valid"] = _eintrag("Nein", "listing_description", _fund(text, m))
        return raus
    m = re.search(r"\b(?:hu|t[üu]v|hauptuntersuchung)\s*(?:/\s*au)?\s*(?:bis|:|gültig\s+bis|gueltig\s+bis)?\s*"
                  r"(\d{1,2})\s*[./-]\s*(\d{4}|\d{2})\b", t)
    if m:
        mj = PV.monat_jahr_text(f"{m.group(1)}/{m.group(2)}", "hu")
        if re.fullmatch(r"\d{2}/\d{4}", mj):
            raus["hu_valid"] = _eintrag("Ja", "listing_description", _fund(text, m))
            raus["hu_until"] = _eintrag(mj, "listing_description", _fund(text, m))
            return raus
    m = re.search(r"\b(?:hu|t[üu]v)\s*(?:/\s*au|&\s*au|und\s+au)?\s*(?:ist\s+)?neu\b|neue[rs]?\s+(?:hu|t[üu]v)\b", t)
    if m:
        raus["hu_valid"] = _eintrag("Ja", "listing_description", _fund(text, m))
    return raus


_SCHECK = r"(?:scheckheft|serviceheft|service-?historie|wartungs-?historie|servicebuch)"


def scheckheft(text: str) -> Dict[str, Any]:
    """Liefert {"wert": ..} oder {"hinweis": ..} oder {}."""
    t = text.lower()
    kein = re.search(r"(?:kein(?:e|es)?|ohne|nicht)\s+" + _SCHECK + r"|" + _SCHECK + r"\s+(?:fehlt|nicht\s+vorhanden|unvollst[äa]ndig)"
                     r"|nicht\s+scheckheftgepflegt|scheckheft\s+l[üu]ckenhaft", t)
    voll = re.search(r"(?:l[üu]e?ckenlos|vollst[äa]ndig|komplett|durchgehend)\w*\s+(?:" + _SCHECK + r"|scheckheftgepflegt|"
                     r"scheckheft\s*gepflegt)|" + _SCHECK + r"\s+(?:l[üu]e?ckenlos|vollst[äa]ndig|komplett)|alle\s+inspektionen\s+(?:nachweisbar|belegt|gemacht|durchgef)", t)
    nur = re.search(r"scheckheft\s*gepflegt|scheckheftgepflegt|" + _SCHECK + r"\s+vorhanden|mit\s+" + _SCHECK, t)
    if kein and voll:
        return {"hinweis": "Das Inserat sagt beides — „lückenlos“ und „kein/unvollständiges Scheckheft“. Bitte selbst prüfen."}
    if kein:
        return {"wert": _eintrag("nein", "listing_description", _fund(text, kein))}
    if voll:
        return {"wert": _eintrag("ja", "listing_description", _fund(text, voll))}
    if nur:
        return {"hinweis": "Inserat: „" + _fund(text, nur) + "“ — „scheckheftgepflegt“ heißt nicht "
                           "zwingend lückenlos. Bitte das Scheckheft ansehen und dann wählen."}
    return {}


def unfall(v: dict, text: str) -> Optional[Dict[str, Any]]:
    if v.get("accident_damaged") is True or v.get("damage_unrepaired") is True:
        return _eintrag("Nein", "listing_field", "Unfallschaden laut Inserat")
    if v.get("accident_damaged") is False:
        return _eintrag("Ja", "listing_field", "unfallfrei laut Inserat")
    t = text.lower()
    nein = re.search(r"nicht\s+unfallfrei|unfallschaden(?!\s*:\s*nein)|unfallwagen|unfallfahrzeug"
                     r"|hatte\s+(?:\w+\s+){0,2}einen\s+unfall|unfall\s+gehabt", t)
    kein_schaden = re.search(r"(?:kein(?:e|en)?|ohne)\s+unfallsch[äa]den|unfallschaden\s*:\s*nein|unfallfrei\s*:\s*ja", t)
    ja = re.search(r"(?<!nicht\s)\bunfallfrei\b(?!\s*:\s*nein)", t)
    if nein and not kein_schaden:
        if ja:
            return None      # widerspruechlich: nichts vorauswaehlen
        return _eintrag("Nein", "listing_description", _fund(text, nein))
    if ja or kein_schaden:
        return _eintrag("Ja", "listing_description", _fund(text, ja or kein_schaden))
    return None


def fahrbereit(v: dict, text: str) -> Optional[Dict[str, Any]]:
    if v.get("roadworthy") is True:
        return _eintrag("Ja", "listing_field", "fahrbereit laut Inserat")
    if v.get("roadworthy") is False:
        return _eintrag("Nein", "listing_field", "nicht fahrbereit laut Inserat")
    t = text.lower()
    m = re.search(r"nicht\s+fahrbereit|fahrbereit\s*:\s*nein|fahruntüchtig|fahruntuechtig", t)
    if m:
        return _eintrag("Nein", "listing_description", _fund(text, m))
    m = re.search(r"\bfahrbereit\b", t)
    if m:
        return _eintrag("Ja", "listing_description", _fund(text, m))
    return None


def eu_import(text: str) -> Optional[Dict[str, Any]]:
    t = text.lower()
    m = re.search(r"\b(?:eu[- ]?import|re-?import|reimport|importfahrzeug)\b", t)
    if m and not re.search(r"kein\s+(?:eu[- ]?import|re-?import|reimport)", t):
        return _eintrag("Ja", "listing_description", _fund(text, m))
    return None


def bereifung(text: str) -> Optional[Dict[str, Any]]:
    t = text.lower()
    m = re.search(r"8[- ]?fach|2\s*(?:satz|sätze|saetze)\s+(?:reifen|räder|raeder)|zweiter\s+(?:rad|reifen)satz|zweitsatz"
                  r"|sommer-?\s*(?:und|\+|&)\s*winterreifen|winterreifen\s+(?:dabei|inklusive|inkl\.?|vorhanden)|winterr[äa]der\s+(?:dabei|inklusive|inkl\.?|vorhanden)", t)
    if m:
        return _eintrag("8-fach", "listing_description", _fund(text, m))
    m = re.search(r"4[- ]?fach\s+bereift|4[- ]?fach\b", t)
    if m:
        return _eintrag("4-fach", "listing_description", _fund(text, m))
    return None


# ------------------------------------------------ alles zusammen
def vorschlaege(v: dict) -> Dict[str, Any]:
    """Vertragsfelder, die das Inserat eindeutig belegt, plus Hinweise fuer
    das, was der Sucher selbst pruefen muss. Wirft nie."""
    v = v or {}
    text = _text(v)
    felder: Dict[str, Dict[str, Any]] = {}
    hinweise: List[str] = []
    try:
        s = schluessel(v, text)
        if s:
            felder["schluessel_anzahl"] = s
        felder.update(hu(v, text))
        sh = scheckheft(text)
        if sh.get("wert"):
            felder["service_book"] = sh["wert"]
        elif sh.get("hinweis"):
            hinweise.append(sh["hinweis"])
        u = unfall(v, text)
        if u:
            felder["accident_free"] = u
        f = fahrbereit(v, text)
        if f:
            felder["drivable"] = f
        e = eu_import(text)
        if e:
            felder["eu_import"] = e
        b = bereifung(text)
        if b:
            felder["tires"] = b
    except Exception:  # noqa: BLE001 — Vorschlaege sind Beiwerk
        pass
    return {"felder": felder, "hinweise": hinweise,
            "bekannte_maengel": [str(m)[:200] for m in (v.get("known_defects") or []) if str(m or "").strip()][:20]}
