# -*- coding: utf-8 -*-
"""Schadenspruefung eines Inserats aus seinem Text — OHNE KI (Wunsch Ahmad 27.11.2026, erster Baustein fuer den
geplanten Dauerlauf: "wenn Schaden dabei ist, nicht weiter verarbeiten; ohne Schaden oder mit kleinem Schaden weiter").

Eingang: die GANZE Beschreibung (Programm ab 1.5.22 liest sie aus AutoPointer, ``beschreibung_voll``), dazu die
Portalangaben Zustand/Kategorie und die Ueberschrift. Ausgang: eine von fuenf Stufen

  * ``schwer``    — Unfall, Motor/Getriebe/Antrieb, nicht fahrbereit, Bastler/Export, grosse Schaeden
  * ``unklar``    — ein Schaden wird genannt, aber nicht, wie gross ("hat einen Schaden", widerspruechlich)
  * ``leicht``    — Kratzer, Dellen, Steinschlag, Parkrempler, Gebrauchsspuren, ein kleines Teil defekt
  * ``kein``      — Text vorhanden, nichts gefunden
  * ``ohne_text`` — keine Beschreibung: nicht beurteilbar (nie als "kein Schaden" werten)

Mehrere Funde: die schwerste Stufe gilt (schwer > unklar > leicht). Verneinungen ("keine Kratzer", "ohne
Motorschaden", "unfallfrei") zaehlen nicht als Fund — die Regeln dafuer kommen aus ``ai.inserat_regeln`` (dort seit
28.09.2026 in mehreren Runden geprueft), ebenso das Urteil zu Unfall und Fahrbereitschaft.

Die Beschreibung kommt aus einer Texterkennung: einzelne Buchstaben fehlen manchmal ("Alradantrieb"). Darum sind die
Stichwoerter Wortstaemme und doppelte Buchstaben duerfen einfach stehen ("Unfal", "Dele"). Was trotzdem verloren
geht, wird nicht geraten — ``kein`` heisst "nichts gefunden", nicht "garantiert unbeschaedigt".
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from ai import inserat_regeln as R

SCHWER, UNKLAR, LEICHT, KEIN, OHNE_TEXT = "schwer", "unklar", "leicht", "kein", "ohne_text"
_RANG = {SCHWER: 3, UNKLAR: 2, LEICHT: 1, KEIN: 0, OHNE_TEXT: 0}
#: So viele Fundstellen gehen hoechstens in die Antwort (und ins Protokoll des Vergleichs).
MAX_GRUENDE = 5
#: Unter so vielen Zeichen gilt eine Beschreibung als nicht vorhanden ("VB", "siehe Bilder").
MIN_TEXT = 15

_A = r"(?:ä|ae|a)"
_U = r"(?:ü|ue|u)"
_O = r"(?:ö|oe|o)"

# ---- schwer -------------------------------------------------------------------
_SCHWER = re.compile(
    # grosse Schaeden an Karosserie/Fahrzeug
    r"\b(?:total|rahmen|wasser|brand|flut|hagel|unfal+|front|heck|seiten|karos+erie|blech|wild|vor)-?sch" + _A + r"den\w*"
    r"|\bhagel\w*|\bunfal+(?:wagen|fahrzeug|auto)\w*|\bcrash\w*"
    r"|\b(?:wirtschaftlicher\s+)?totalschaden\w*"
    # Antrieb
    r"|\b(?:motor|getriebe|kup+lungs|zahnriemen|turbo|lager|kolben|zylinderkopf|kopfdichtungs|differential)-?sch" + _A + r"den\w*"
    r"|\bzylinderkopfdichtung\w*|\bkopfdichtung\w*|\bkolbenfres+er\w*|\blagerschaden\w*"
    r"|\b(?:motor|getriebe|kup+lung|turbo\w*|zylinderkopf|lenkung|zahnriemen|steuerkette|automatik\w*|dsg|injektor\w*)"
    r"\s+(?:ist\s+|sind\s+|leider\s+)*(?:defekt|kaput+|gerissen|hin" + _U + r"ber|fest\w*|platt|durch|am\s+ende)\b"
    # Steuerkette: Geraeusche oder "muss erneuert werden" — NICHT "wurde erneuert" (das ist eine gute Nachricht)
    r"|\bsteuerkette\w*[^.!?\n]{0,60}?(?:ger" + _A + r"usch|ras+el|klapper|l" + _A + r"ngt|defekt|f" + _A + r"l+ig"
    r"|mus+|m" + _U + r"s+te|sol+te)\w*"
    r"|\b(?:motor|getriebe)\w*[^.!?\n]{0,40}?(?:klopft|klackert|ras+elt|ruckelt|rutscht|verliert\s+" + _O + r"l|qualmt|raucht)\w*"
    r"|\bnotlauf\w*|\bmotorkontrol+leuchte\w*|\bmkl\b"
    # steht / faehrt nicht
    r"|\bspringt\s+nicht\s+(?:mehr\s+)?an|\bl" + _A + r"uft\s+nicht\b|\bf" + _A + r"hrt\s+nicht\b|\babschlep+\w*"
    r"|\b(?:nicht|bedingt|nur\s+bedingt|eingeschr" + _A + r"nkt)\s+fahrbereit\w*|\bfahr(?:unt" + _U + r"chtig|unf" + _A + r"hig|untauglich)\w*"
    # so wird es verkauft
    r"|\bbastler\w*|\bteiletr" + _A + r"ger\w*|\bteilespender\w*|\bschlachtfest\w*|\bausschlachten\w*"
    r"|\bnur\s+(?:f" + _U + r"r\s+|als\s+|an\s+)?(?:export|h" + _A + r"ndler|gewerbe)\b|\bexport(?:fahrzeug|auto|ware)\w*"
    r"|\breparaturbed" + _U + r"rftig\w*|\bdefektes?\s+(?:fahrzeug|auto|kfz)\b|\bbesch" + _A + r"digt\w*",
    re.I)

# ---- leicht -------------------------------------------------------------------
_LEICHT = re.compile(
    r"\bkratzer\w*|\bschram+e\w*|\bdel+e\w*|\bbeule\w*|\bsteinschl" + _A + r"g\w*|\bparkrempler\w*|\bparkschaden\w*"
    r"|\b(?:lack|bagatel+|park)-?sch" + _A + r"den\w*|\blackplatzer\w*|\bklarlack\w*[^.!?\n]{0,30}?(?:ab|l" + _O + r"st|platzt)\w*"
    r"|\bgebrauchs+puren\w*|\bgebrauchs+pur\b|\bkampfspuren\w*|\bsch" + _O + r"nheitsfehler\w*|\bmacken?\b"
    r"|\b(?:leichte[rnms]?|etwas|bis+chen|kleine[rnms]?)\s+rost\w*|\bflugrost\w*|\brostans" + _A + r"tze\w*"
    r"|\bris+\s+in\s+der\s+(?:front|wind)?scheibe\w*|\bscheibe\s+(?:hat\s+)?(?:einen\s+)?ris+\w*"
    r"|\b\w+\s+(?:ist\s+|sind\s+|leider\s+)*(?:defekt|kaput+)\b|\b(?:defekte[rnms]?|kaput+e[rnms]?)\s+\w+"
    r"|\b(?:funktioniert|geht|gehen|funktionieren)\s+nicht\b|\bohne\s+funktion\b",
    re.I)

# ---- unklar: "Schaden" ohne Groesse ------------------------------------------------
_SCHADEN = re.compile(r"\bsch" + _A + r"den\w*|\bschaden\w*|\bm" + _A + r"ngel\w*|\bmangel\b|\bdefekte?\b", re.I)
_KLEIN_DAVOR = re.compile(r"(?:klein\w*|leicht\w*|gering\w*|minimal\w*|winzig\w*|optisch\w*|kosmetisch\w*)\s+$", re.I)
_NEG = R._NEG_SCHADEN | {"keinerlei", "frei"}
# "mängelfrei", "schadenfrei", "rostfrei", "frei von Mängeln", "keine bekannten Mängel", "Mängel: keine"
_FREI_DAHINTER = re.compile(r"\s*-?\s*frei\w*|\s*[:=]\s*(?:kein\w*|nein|-|–|nicht\s+bekannt)", re.I)
# auch in Aufzaehlungen: "frei von Kratzern und Beulen", "keine Kratzer, Dellen oder Beulen"
_FREI_DAVOR = re.compile(r"(?:(?:frei\s+von|ohne|kein\w*)\s+(?:\w+\s+)?(?:[\w-]+\s*(?:,|und|oder|noch|sowie)\s+)*"
                         r"|nicht\s+bekannt\w*\s+)$", re.I)
_FREI_IM_WORT = re.compile(r"frei\w*$", re.I)


def _verneint(t: str, m: "re.Match") -> bool:
    """Ist der Fund verneint? Erst die geprueften Regeln (``kein Hagelschaden`` ohne weiteren Vorbehalt), dann die
    einfachen Formen direkt am Wort ("mängelfrei", "frei von Kratzern", "keine sichtbaren Dellen", "Mängel: keine")."""
    try:
        if R._nomen_verneint_sauber(t, m, _NEG):
            return True
    except Exception:           # die Satzteil-Zerlegung darf die Pruefung nie umwerfen
        pass
    if _FREI_IM_WORT.search(m.group(0)) or _FREI_DAHINTER.match(t, m.end()):
        return True
    return bool(_FREI_DAVOR.search(t[max(0, m.start() - 60):m.start()]))


def _fund(t: str, m: "re.Match", rand: int = 40) -> str:
    a, b = max(0, m.start() - rand), min(len(t), m.end() + rand)
    s = " ".join(t[a:b].split())
    return ("…" if a > 0 else "") + s + ("…" if b < len(t) else "")


def _funde(t: str, muster: "re.Pattern") -> List["re.Match"]:
    return [m for m in muster.finditer(t) if not _verneint(t, m)]


def _ueberdeckt(m: "re.Match", andere: List["re.Match"]) -> bool:
    return any(a.start() < m.end() and m.start() < a.end() for a in andere)


def pruefen(beschreibung: Optional[str], zustand: Optional[str] = "", kategorie: Optional[str] = "",
            titel: Optional[str] = "") -> Dict[str, Any]:
    """Stufe + Fundstellen. ``gruende`` sind kurze Ausschnitte (hoechstens MAX_GRUENDE), die schwersten zuerst."""
    text = (beschreibung or "").strip()
    kopf = " ".join(x for x in (zustand or "", kategorie or "", titel or "") if x).strip()
    funde: List[Tuple[str, str]] = []           # (stufe, ausschnitt)

    # Portalangaben und Ueberschrift: "Unfallwagen", "Beschädigt", "Motorschaden" im Titel
    for m in _funde(kopf, _SCHWER):
        funde.append((SCHWER, "Angabe im Inserat: " + _fund(kopf, m)))

    if len(text) < MIN_TEXT and not funde:
        return {"stufe": OHNE_TEXT, "gruende": [], "text_laenge": len(text)}

    schwer = _funde(text, _SCHWER)
    funde += [(SCHWER, _fund(text, m)) for m in schwer]
    leicht = [m for m in _funde(text, _LEICHT) if not _ueberdeckt(m, schwer)]
    funde += [(LEICHT, _fund(text, m)) for m in leicht]
    for m in _funde(text, _SCHADEN):
        if _ueberdeckt(m, schwer) or _ueberdeckt(m, leicht):
            continue
        klein = bool(_KLEIN_DAVOR.search(text[max(0, m.start() - 30):m.start()]))
        funde.append((LEICHT if klein else UNKLAR, _fund(text, m)))

    # Unfall und Fahrbereitschaft: das gepruefte Urteil aus dem Vertrags-Vorschlag ("Nein" = ausdruecklich)
    for regel, wort in ((R.unfall, "Unfall"), (R.fahrbereit, "nicht fahrbereit")):
        try:
            eintrag, hinweis = regel({}, text)
        except Exception:
            continue
        if eintrag and eintrag.get("value") == "Nein":
            funde.append((SCHWER, f"{wort}: {eintrag.get('source_text') or ''}".strip()))
        elif hinweis and regel is R.unfall and "nicht eindeutig" in hinweis and not any(s == SCHWER for s, _ in funde):
            funde.append((UNKLAR, "Unfallfreiheit nicht eindeutig"))

    if not funde:
        return {"stufe": KEIN, "gruende": [], "text_laenge": len(text)}
    funde.sort(key=lambda f: -_RANG[f[0]])
    gruende: List[str] = []
    for _, g in funde:
        if g not in gruende:
            gruende.append(g)
    return {"stufe": funde[0][0], "gruende": gruende[:MAX_GRUENDE], "text_laenge": len(text)}


def meldung(ergebnis: Dict[str, Any]) -> Optional[str]:
    """Ein Satz fuer den Sucher — nur bei ``schwer`` (das Programm zeigt ihn sofort)."""
    if ergebnis.get("stufe") != SCHWER or not ergebnis.get("gruende"):
        return None
    return "Schaden im Inserat: " + ergebnis["gruende"][0] + " – bitte vor dem Anruf prüfen."
