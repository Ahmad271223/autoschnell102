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

Go-Live-Pruefung 27.09.2026 (K1/K2/K6/K7): Die Vorbelegung darf dem
Verkaeufer NIE eine Zusicherung unterschieben, die das Inserat nicht klar
hergibt.
  * Verneinungen im Umkreis (bis 4 Woerter davor, im selben Satzteil):
    "nicht mehr unfallfrei", "war nie unfallfrei", "nicht lueckenlos
    scheckheftgepflegt" -> "Nein" bzw. nichts; "bedingt/eingeschraenkt
    fahrbereit" oder "Unfallfrei? ..." ohne klare Antwort -> nichts.
  * "Ja" aus dem Freitext nur ohne jeden Widerspruch im Text (ein
    Hagelschaden neben "kein Unfallschaden" ist strittig -> nichts).
  * Portalfelder (accident_damaged=False, "Unbeschaedigtes Fahrzeug",
    roadworthy=True) ergeben NIE allein "Ja" — "unbeschaedigt" heisst nicht
    "unfallfrei". Nur ein Hinweis mit dem ECHTEN Portalwert. Widerspricht der
    Text dem Portalfeld, wird nichts vorbelegt.
  * HU: Datum gegen den aktuellen Monat (Europe/Berlin); abgelaufen -> nichts
    vorbelegen, Hinweis "HU abgelaufen (MM/JJJJ)". "abgelaufen / keine HU /
    ohne TUEV" im Text hat Vorrang vor dem Portalfeld.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

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


def _heute_berlin() -> date:
    """Heutiges Datum in deutscher Zeit (Server laeuft ggf. in UTC)."""
    try:
        from betriebsmeldung import _berlin
        return _berlin(datetime.now(timezone.utc)).date()
    except Exception:  # noqa: BLE001 — lieber UTC als gar kein Vergleich
        return datetime.now(timezone.utc).date()


# ------------------------------------------------ Verneinung im Umkreis
# Satzteil-Grenzen: Satzzeichen und Bindewoerter. "Klima geht nicht, aber
# fahrbereit" — das "nicht" gehoert zur Klima, nicht zu "fahrbereit".
_GRENZE = re.compile(r"[.!?;,:()\[\]\n–—/]|\s-\s"
                     r"|\b(?:aber|jedoch|sondern|doch|trotzdem|dennoch|und|sowie|oder|bzw)\b")
_HART = re.compile(r"\b(?:nicht|nie|niemals|nimmer|kein\w*|ohne|nein)\b")
_WEICH = re.compile(r"\b(?:bedingt|eingeschr[äa]e?nkt\w*|teilweise|teils|fast|nahezu|weitgehend"
                    r"|weitestgehend|eher|angeblich|vermutlich|wahrscheinlich|laut|soweit|eventuell"
                    r"|evtl|vielleicht|m[öo]e?glicherweise|halbwegs|kaum|bedingungsweise|einigerma[ßs]+en)\b")
# Nach dem Stichwort: "Unfallfrei? Leider nein", "fahrbereit: nein"
_NACH_NEIN = re.compile(r"\s*[:?!(\-–]?\s*(?:leider\s+|eher\s+|definitiv\s+|nat[üu]e?rlich\s+)?"
                        r"(?:nein|no)\b|\s*:\s*keine?r?s?\b")
_NACH_JA = re.compile(r"\s*[:?!(\-–]?\s*(?:ja|yes|jawohl)\b")
_NACH_NICHT = re.compile(r"\s*[:?!(\-–]?\s*(?:leider\s+)?(?:nicht|nie)\b")
_NACH_WEICH = re.compile(r"\s*[:?!(\-–,]?\s*(?:nur\s+)?(?:bedingt|eingeschr|mit\s+einschr|teilweise"
                         r"|laut\s|angeblich|soweit|vermutlich|unter\s+vorbehalt)")


def _vorher(t: str, start: int, woerter: int = 4) -> str:
    """Bis zu `woerter` Woerter vor dem Stichwort, nur im eigenen Satzteil."""
    stueck = t[max(0, start - 160):start]
    teile = _GRENZE.split(stueck)
    rest = teile[-1] if teile else ""
    worte = rest.split()[-woerter:]
    return " ".join(worte)


def _nachher(t: str, ende: int) -> str:
    stueck = t[ende:ende + 40]
    m = re.search(r"[.!;\n]", stueck)
    return stueck[:m.start()] if m else stueck


def _art(t: str, m: "re.Match", positiv: bool = True) -> str:
    """Wie steht das Stichwort da? "ja" (bejaht), "nein" (verneint) oder
    "unklar" (eingeschraenkt/unsicher). positiv=True fuer Zusicherungs-
    Stichwoerter ("unfallfrei", "fahrbereit", "lueckenlos"): dort macht auch
    ein "nicht" direkt danach oder eine offene Frage die Stelle unklar."""
    vor = _vorher(t, m.start())
    vor_ohne = re.sub(r"\bnicht\s+nur\b", " ", vor)       # "nicht nur unfallfrei, sondern ..."
    nach = _nachher(t, m.end())
    if _HART.search(vor_ohne):
        return "nein"
    if _WEICH.search(vor):
        return "unklar"
    if _NACH_NEIN.match(nach):
        return "nein"
    if _NACH_JA.match(nach):
        return "ja"
    if positiv:
        if _NACH_NICHT.match(nach) or _NACH_WEICH.match(nach):
            return "unklar"
        if re.match(r"\s*\?", nach):
            return "unklar"
    return "ja"


def _stellen(t: str, muster: str, positiv: bool) -> List[Tuple["re.Match", str]]:
    return [(m, _art(t, m, positiv)) for m in re.finditer(muster, t)]


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


_HU_WORT = r"(?:hu|t[üu]e?v|hauptuntersuchung)"
# "keine HU", "ohne TUEV", "abgelaufener TUEV", "HU abgelaufen",
# "TUEV/AU 05/2026 abgelaufen", "TUEV seit 05.2026 abgelaufen"
_HU_NEIN = re.compile(
    r"\b(?:keine?|ohne)\s+(?:g[üu]e?ltige[nrs]?\s+|aktuelle[nrs]?\s+)?" + _HU_WORT + r"\b"
    r"|\babgelaufene[nrs]?\s+" + _HU_WORT + r"\b"
    r"|\b" + _HU_WORT + r"\b(?:[^.!?;,\n]|(?<=\d)\.(?=\d)){0,30}?\babgelaufen")
_HU_DATUM = re.compile(
    r"\b" + _HU_WORT + r"\s*(?:/\s*au|&\s*au|und\s+au)?\s*(?:neu\s*)?(?:ist\s+)?"
    r"(?:bis|:|gültig\s+bis|gueltig\s+bis)?\s*(?:zum\s+)?(\d{1,2})\s*[./-]\s*(\d{4}|\d{2})\b")
_HU_NEU = re.compile(r"\b" + _HU_WORT + r"\s*(?:/\s*au|&\s*au|und\s+au)?\s*(?:ist\s+)?neu\b"
                     r"|\bneue[rs]?\s+" + _HU_WORT + r"\b")


def _abgelaufen(mj: str, heute: date) -> bool:
    r = re.fullmatch(r"(\d{2})/(\d{4})", mj or "")
    if not r:
        return False
    return (int(r.group(2)), int(r.group(1))) < (heute.year, heute.month)


def _hu_abgelaufen_hinweis(mj: str, fund: str) -> str:
    return (f"HU abgelaufen ({mj}) laut Inserat („{fund}“) — „HU/AU vorhanden“ bitte beim "
            "Verkäufer erfragen und selbst wählen.")


def hu(v: dict, text: str, heute: Optional[date] = None) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
    """Liefert (felder, hinweise)."""
    heute = heute or _heute_berlin()
    raus: Dict[str, Dict[str, Any]] = {}
    hinweise: List[str] = []
    t = text.lower()
    roh = str(v.get("hu") or "").strip()
    portal_mj = PV.monat_jahr_text(roh, "hu") if roh else ""
    if not re.fullmatch(r"\d{2}/\d{4}", portal_mj or ""):
        portal_mj = ""

    # K2: "abgelaufen / keine HU / ohne TUEV" im Text hat Vorrang vor dem Portalfeld
    m = _HU_NEIN.search(t)
    if m:
        raus["hu_valid"] = _eintrag("Nein", "listing_description", _fund(text, m))
        if portal_mj and not _abgelaufen(portal_mj, heute) or roh.lower() in ("neu", "new"):
            hinweise.append(f"Inserat widersprüchlich: Portalfeld „HU {portal_mj or roh}“, im Text "
                            f"„{_fund(text, m)}“ — „HU/AU vorhanden: Nein“ vorbelegt, bitte prüfen.")
        return raus, hinweise

    if portal_mj:
        if _abgelaufen(portal_mj, heute):
            hinweise.append(_hu_abgelaufen_hinweis(portal_mj, f"HU {portal_mj}"))
            return raus, hinweise
        raus["hu_valid"] = _eintrag("Ja", "listing_field", f"HU {portal_mj}")
        raus["hu_until"] = _eintrag(portal_mj, "listing_field", f"HU {portal_mj}")
        return raus, hinweise
    if roh.lower() in ("neu", "new"):
        raus["hu_valid"] = _eintrag("Ja", "listing_field", "HU neu")
        return raus, hinweise

    for m in _HU_DATUM.finditer(t):
        if _art(t, m, positiv=False) != "ja":
            continue
        mj = PV.monat_jahr_text(f"{m.group(1)}/{m.group(2)}", "hu")
        if not re.fullmatch(r"\d{2}/\d{4}", mj):
            continue
        if _abgelaufen(mj, heute):
            hinweise.append(_hu_abgelaufen_hinweis(mj, _fund(text, m)))
            return raus, hinweise
        raus["hu_valid"] = _eintrag("Ja", "listing_description", _fund(text, m))
        raus["hu_until"] = _eintrag(mj, "listing_description", _fund(text, m))
        return raus, hinweise
    for m in _HU_NEU.finditer(t):
        if _art(t, m, positiv=True) == "ja":
            raus["hu_valid"] = _eintrag("Ja", "listing_description", _fund(text, m))
            return raus, hinweise
    return raus, hinweise


_SCHECK = r"(?:scheckheft|serviceheft|service-?historie|wartungs-?historie|servicebuch)"
_SCHECK_VOLL = re.compile(
    r"(?:l[üu]e?ckenlos|vollst[äa]ndig|komplett|durchgehend)\w*\s+(?:" + _SCHECK + r"|scheckheftgepflegt|"
    r"scheckheft\s*gepflegt)|" + _SCHECK + r"\s+(?:l[üu]e?ckenlos|vollst[äa]ndig|komplett)"
    r"|alle\s+inspektionen\s+(?:nachweisbar|belegt|gemacht|durchgef)")


def scheckheft(text: str) -> Dict[str, Any]:
    """Liefert {"wert": ..} oder {"hinweis": ..} oder {}."""
    t = text.lower()
    kein = re.search(r"(?:kein(?:e|es)?|ohne|nicht)\s+" + _SCHECK + r"|" + _SCHECK + r"\s+(?:fehlt|nicht\s+vorhanden|unvollst[äa]ndig)"
                     r"|nicht\s+scheckheftgepflegt|scheckheft\s+l[üu]ckenhaft", t)
    voll_stellen = _stellen(t, _SCHECK_VOLL.pattern, positiv=True)
    voll = next((m for m, a in voll_stellen if a == "ja"), None)
    # K1: "nicht lueckenlos / nicht durchgehend scheckheftgepflegt" ist KEIN "Ja, lueckenlos"
    voll_verneint = next((m for m, a in voll_stellen if a != "ja"), None)
    nur = re.search(r"scheckheft\s*gepflegt|scheckheftgepflegt|" + _SCHECK + r"\s+vorhanden|mit\s+" + _SCHECK, t)
    if kein and voll:
        return {"hinweis": "Das Inserat sagt beides — „lückenlos“ und „kein/unvollständiges Scheckheft“. Bitte selbst prüfen."}
    if kein:
        return {"wert": _eintrag("nein", "listing_description", _fund(text, kein))}
    if voll and voll_verneint:
        return {"hinweis": "Das Inserat ist beim Scheckheft widersprüchlich („" + _fund(text, voll_verneint)
                           + "“). Bitte das Scheckheft ansehen und dann wählen."}
    if voll:
        return {"wert": _eintrag("ja", "listing_description", _fund(text, voll))}
    if voll_verneint:
        return {"hinweis": "Inserat: „" + _fund(text, voll_verneint) + "“ — also nicht „Ja, lückenlos“. "
                           "Bitte das Scheckheft ansehen und dann wählen."}
    if nur:
        return {"hinweis": "Inserat: „" + _fund(text, nur) + "“ — „scheckheftgepflegt“ heißt nicht "
                           "zwingend lückenlos. Bitte das Scheckheft ansehen und dann wählen."}
    return {}


# ------------------------------------------------ Unfall
_UNFALLFREI = r"\bunfall-?frei(?:e[nrsm]?)?\b"
_UNFALL = (r"\b(?:unf[äa]e?ll(?:e|en)?|unfall(?:schaden|sch[äa]e?den|wagen|fahrzeug|auto|instandsetzung"
           r"|beteiligung|historie)?)\b(?!-frei)")
# Andere Schaeden: kein klares "Nein", aber ein Widerspruch zu "unfallfrei: Ja"
# (Hagelschaden neben "kein Unfallschaden" ist strittig).
_ANDERER_SCHADEN = (r"\b(?:hagel|vor|karosserie|blech|front|heck|seiten|rahmen|total|park|wild|wasser"
                    r"|brand|flut)?sch[äa]e?den\b|\bhagel\w*|\bbesch[äa]e?digt\w*|\binstand\s*gesetzt\w*"
                    r"|\bcrash\w*")
_REPARIERT = re.compile(r"\brepariert|\brepaired|\binstand\s*gesetzt")


def _portal_unfall(v: dict) -> Tuple[Optional[str], str]:
    """(Art, echter Portalwert). Art: "schaden", "unbeschaedigt", "repariert" oder None."""
    roh = str(v.get("zustand_portal") or "").strip()
    rep = bool(roh) and bool(_REPARIERT.search(roh.lower())) and not re.search(
        r"nicht\s+repariert|not\s+repaired|unrepaired", roh.lower())
    if v.get("damage_unrepaired") is True:
        return "schaden", roh or "Portalfeld „beschädigt, nicht repariert“: ja"
    if v.get("accident_damaged") is True:
        return "schaden", roh or "Portalfeld „Unfallschaden“: ja"
    if rep:
        return "repariert", roh
    if v.get("accident_damaged") is False:
        return "unbeschaedigt", roh or "Portalfeld „Unfallschaden“: nein"
    return None, roh


def unfall(v: dict, text: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Liefert (eintrag, hinweis) — hoechstens eins von beiden."""
    t = text.lower()
    frei = _stellen(t, _UNFALLFREI, positiv=True)
    schaden = _stellen(t, _UNFALL, positiv=False)
    anders = _stellen(t, _ANDERER_SCHADEN, positiv=False)
    ja = [m for m, a in frei if a == "ja"] + [m for m, a in schaden if a == "nein"]
    nein = [m for m, a in frei if a == "nein"] + [m for m, a in schaden if a == "ja"]
    unklar = [m for m, a in frei + schaden if a == "unklar"]
    anderer = [m for m, a in anders if a != "nein"]

    text_wert: Optional[str] = None
    fund: Optional["re.Match"] = None
    if unklar:
        text_wert, fund = "unklar", unklar[0]
    elif ja and nein:
        text_wert, fund = "unklar", nein[0]
    elif nein:
        text_wert, fund = "Nein", nein[0]
    elif ja and anderer:
        text_wert, fund = "unklar", anderer[0]
    elif ja:
        text_wert, fund = "Ja", ja[0]

    portal, portal_text = _portal_unfall(v)
    bitte = "„Unfallfrei“ bitte beim Verkäufer erfragen und selbst wählen."

    if text_wert == "unklar":
        return None, f"Inserat zur Unfallfreiheit nicht eindeutig („{_fund(text, fund)}“) — {bitte}"
    if portal == "schaden":
        if text_wert == "Ja":
            return None, (f"Inserat widersprüchlich: {portal_text}, im Text „{_fund(text, fund)}“ — {bitte}")
        return _eintrag("Nein", "listing_field", portal_text), None
    if portal in ("unbeschaedigt", "repariert"):
        if text_wert == "Nein" or (portal == "repariert" and text_wert == "Ja"):
            return None, (f"Inserat widersprüchlich: {portal_text}, im Text „{_fund(text, fund)}“ — {bitte}")
        if text_wert == "Ja":
            return _eintrag("Ja", "listing_description", _fund(text, fund)), None
        # K6: "unbeschaedigt" / kein Schadensfall heisst NICHT "unfallfrei";
        # auch ein Portalhaken "Accident-free" belegt allein nichts vor.
        if re.search(r"unfall-?frei|accident[- ]free|no accident", portal_text.lower()):
            return None, f"Im Inserat: {portal_text} — nur eine Portalangabe. {bitte}"
        return None, (f"Im Inserat: {portal_text} — das heißt nicht „unfallfrei“ (ein reparierter "
                      f"Unfallschaden zählt auch). {bitte}")
    if text_wert in ("Ja", "Nein"):
        return _eintrag(text_wert, "listing_description", _fund(text, fund)), None
    return None, None


# ------------------------------------------------ fahrbereit
_FAHRBEREIT = r"\bfahr(?:bereit|tauglich|f[äa]e?hig|t[üu]e?chtig)(?:e[nrsm]?)?\b"
_FAHR_NEIN = r"\bfahr(?:unt[üu]e?chtig|unf[äa]e?hig|untauglich)\b"
# Kein klares "Nein", aber ein Widerspruch zu "Fahrtauglich: Ja"
_FAHR_ANDERS = (r"\b(?:motor|getriebe|kupplungs|zahnriemen)schaden\b|\b(?:motor|getriebe|kupplung)\s+(?:ist\s+)?defekt"
                r"|\bspringt\s+nicht\s+(?:mehr\s+)?an|\bl[äa]e?uft\s+nicht\b|\babschlepp\w*|\bbastler\w*"
                r"|\bnur\s+(?:f[üu]r\s+|als\s+)?(?:export|teilespender|trailer)")


def fahrbereit(v: dict, text: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Liefert (eintrag, hinweis)."""
    t = text.lower()
    bereit = _stellen(t, _FAHRBEREIT, positiv=True)
    untuechtig = _stellen(t, _FAHR_NEIN, positiv=False)
    anders = _stellen(t, _FAHR_ANDERS, positiv=False)
    ja = [m for m, a in bereit if a == "ja"] + [m for m, a in untuechtig if a == "nein"]
    nein = [m for m, a in bereit if a == "nein"] + [m for m, a in untuechtig if a == "ja"]
    unklar = [m for m, a in bereit + untuechtig if a == "unklar"]
    anderer = [m for m, a in anders if a != "nein"]

    text_wert: Optional[str] = None
    fund: Optional["re.Match"] = None
    if unklar:
        text_wert, fund = "unklar", unklar[0]
    elif ja and nein:
        text_wert, fund = "unklar", nein[0]
    elif nein:
        text_wert, fund = "Nein", nein[0]
    elif ja and anderer:
        text_wert, fund = "unklar", anderer[0]
    elif ja:
        text_wert, fund = "Ja", ja[0]

    bitte = "„Fahrtauglich“ bitte selbst prüfen und wählen."
    portal = v.get("roadworthy")
    portal_text = f"Portalfeld „fahrbereit“: {'ja' if portal else 'nein'}" if isinstance(portal, bool) else ""

    if text_wert == "unklar":
        return None, f"Inserat zur Fahrbereitschaft nicht eindeutig („{_fund(text, fund)}“) — {bitte}"
    if portal is True:
        if text_wert == "Nein":
            # K7: der Portalhaken ueberstimmt den Text nicht
            return None, f"Inserat widersprüchlich: {portal_text}, im Text „{_fund(text, fund)}“ — {bitte}"
        if text_wert == "Ja":
            return _eintrag("Ja", "listing_description", _fund(text, fund)), None
        return None, f"Im Inserat: {portal_text} (nur ein Haken im Portal) — {bitte}"
    if portal is False:
        if text_wert == "Ja":
            return None, f"Inserat widersprüchlich: {portal_text}, im Text „{_fund(text, fund)}“ — {bitte}"
        return _eintrag("Nein", "listing_field", portal_text), None
    if text_wert in ("Ja", "Nein"):
        return _eintrag(text_wert, "listing_description", _fund(text, fund)), None
    return None, None


def eu_import(text: str) -> Optional[Dict[str, Any]]:
    t = text.lower()
    stellen = _stellen(t, r"\b(?:eu[- ]?import|re-?import|reimport|importfahrzeug)\b", positiv=True)
    if not stellen or any(a != "ja" for _, a in stellen):
        return None
    return _eintrag("Ja", "listing_description", _fund(text, stellen[0][0]))


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
def vorschlaege(v: dict, heute: Optional[date] = None) -> Dict[str, Any]:
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
        hu_felder, hu_hinweise = hu(v, text, heute)
        felder.update(hu_felder)
        hinweise += hu_hinweise
        sh = scheckheft(text)
        if sh.get("wert"):
            felder["service_book"] = sh["wert"]
        elif sh.get("hinweis"):
            hinweise.append(sh["hinweis"])
        u, u_hinweis = unfall(v, text)
        if u:
            felder["accident_free"] = u
        if u_hinweis:
            hinweise.append(u_hinweis)
        f, f_hinweis = fahrbereit(v, text)
        if f:
            felder["drivable"] = f
        if f_hinweis:
            hinweise.append(f_hinweis)
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
