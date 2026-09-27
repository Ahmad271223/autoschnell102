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

Grundsatz (Vorgabe Auftraggeber 28.09.2026, Runde inserat3 — ersetzt die
Einzelregeln der Runden vom 27./28.09.): Pro Stichwort-Vorkommen wird im
SATZTEIL (bis Komma, Punkt, Semikolon, Ausrufezeichen, Klammer, Zeilenende,
"|" oder "•"; Bindestrich und Gedankenstrich trennen NICHT) ein Urteil
"ja" / "nein" / "unklar" gebildet.
  * "ja" nur, wenn im Satzteil kein Verneinungs- oder Vorbehaltswort steht
    (nicht, kein*, nie, ohne, weder, noch, kaum, bedingt, eingeschraenkt,
    laut, lt., nach Angaben, bekannt, "(nicht)", ...) ODER ein ausdruecklich
    erlaubtes Positivmuster vorliegt ("kein/keine/keinen/keinerlei/ohne
    [jeglich*|irgendwelch*|einzig*|nennenswert*|groesser*|bekannt*] Unfall*",
    "nicht nur unfallfrei", "<Stichwort>: ja").
  * "nein" nur bei ausdruecklichen Negativmustern: "nicht|nie|keinesfalls|
    keineswegs" (+ mehr/ganz/zu 100 %) direkt vor dem Stichwort,
    "nicht-<Stichwort>", "<Stichwort>: nein", "<Stichwort>? (leider) nein",
    "Unfallwagen", "Unfallschaden" ohne eigene Verneinung, "keinen TUEV".
  * alles andere mit einem Verneinungs-/Vorbehaltswort -> "unklar".
  * EINZIGE Ausnahme: Eine Verneinung, die zu einem ANDEREN Nomen gehoert,
    das zwischen ihr und dem Stichwort steht ("keine Maengel unfallfrei",
    "ohne Rost unfallfrei") bzw. "Preis nicht verhandelbar unfallfrei"
    (Verneinung + Aussagewort wie verhandelbar/moeglich), verhindert "ja"
    nicht. Nomen = gross geschrieben, kein Fuell-/Gradwort; in Texten in
    Titelschreibung ("Nicht Wirklich Unfallfrei") gilt die Ausnahme nicht.
Mehrere Vorkommen: widersprechen sie sich oder ist eines unklar -> Feld
LEER mit Hinweis; sonst das gemeinsame Urteil.

Weiter gilt (Go-Live-Pruefung 27.09.2026, K1/K2/K6/K7):
  * Portalfelder (accident_damaged=False, "Unbeschaedigtes Fahrzeug",
    roadworthy=True) ergeben NIE allein "Ja" — nur ein Hinweis mit dem
    ECHTEN Portalwert. Widerspricht der Text dem Portalfeld: nichts.
  * HU: "abgelaufen/ueberzogen/faellig" im Satzteil des HU-Worts -> "nein",
    ausser direkt davor steht ein anderes Nomen ("Bremsbelaege abgelaufen")
    — dann gilt nur das Datum. Datum in der Vergangenheit -> nichts, Hinweis.
    Text "abgelaufen" gegen gueltiges Portalfeld -> nichts, Hinweis
    "Inserat widersprüchlich"; nie "Ja".
  * Scheckheft: "Ja, lueckenlos" nur bei "lueckenlos"/"vollstaendig" ohne
    Verneinung, sonst Hinweis.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

import protokoll_vergleich as PV

_ZAHLWORT = {"ein": 1, "einen": 1, "einem": 1, "1": 1, "zwei": 2, "2": 2, "drei": 3, "3": 3,
             "vier": 4, "4": 4}


def _text(v: dict) -> str:
    """Beschreibung + Ausstattungsliste + bekannte Maengel (Originalschreibung —
    die Gross-/Kleinschreibung braucht die Nomen-Ausnahme)."""
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


# ================================================ Woerter
# Verneinungs- und Vorbehaltswoerter (klein, ohne Rand-Satzzeichen, ganzes Wort).
_STOER = re.compile(
    r"(?:nicht|nichts|nie|niemals|nimmer|niemand|nirgends|nirgendwo|kein\w*|ohne|weder|noch|kaum|nein"
    r"|bedingt\w*|eingeschr(?:ä|ae|a)nkt\w*|laut|lt|angeblich\w*|vermutlich|wahrscheinlich|vielleicht"
    r"|eventuell|evtl|m(?:ö|oe|o)glicherweise|soweit|sofern|fast|nahezu|weitgehend|weitestgehend"
    r"|teilweise|teils|halbwegs|eher|wohl|(?:un)?bekannt\w*|unklar|ungewiss|ungepr(?:ü|ue|u)ft"
    r"|vorbehalt\w*|angabe|angaben|aussage|aussagen|auskunft|wissens|gew(?:ä|ae|a)hr|glaube|glaub"
    r"|denke|sollte|m(?:ü|ue|u)sste|d(?:ü|ue|u)rfte|quasi|praktisch|sozusagen|eigentlich"
    r"|grunds(?:ä|ae|a)tzlich|relativ|ziemlich|optisch|(?:ä|ae|a)u(?:ß|ss)erlich|augenscheinlich"
    r"|scheinbar|anscheinend|offenbar|offensichtlich|au(?:ß|ss)er|abgesehen|ausgenommen|bald"
    r"|demn(?:ä|ae|a)chst|besitz"
    # Hoerensagen / Einschraenkung: "als unfallfrei gekauft", "Kratzer, sonst unfallfrei"
    r"|gekauft|erworben|gesagt|erz(?:ä|ae|a)hlt|versichert|sonst|ansonsten)")
# Mehrwort-Vorbehalte im Satzteil
_STOER_PHRASE = re.compile(
    r"\bbis\s+auf\b|\bbei\s+mir\b|\bso\s+gut\s+wie\b|\bmehr\s+oder\s+weniger\b"
    r"|\bim\s+(?:grunde|prinzip|wesentlichen|gro(?:ß|ss)en\s+und\s+ganzen)\b|\bnur\s+(?:mit|noch|kurz\w*)\b"
    r"|\bseit\s+ich\b|\bin\s+meine[mr]\s+(?:besitz|zeit)\b|\bmeine[rs]?\s+(?:meinung|zeit)\b", re.I)
# Diese Verneinungen koennen zu einem ANDEREN Nomen gehoeren ("keine Maengel")
_NEG_ANH = frozenset({"nicht", "kein", "keine", "keinen", "keinem", "keiner", "keines", "keinerlei", "ohne"})
# Negativmuster direkt vor dem Stichwort + erlaubte Fuellwoerter dazwischen
_NEG_MUSTER = frozenset({"nicht", "nie", "niemals", "keinesfalls", "keineswegs"})
_FUELL = frozenset({"mehr", "ganz", "zu", "100", "100%", "%", "prozent"})
# "Preis nicht verhandelbar unfallfrei": das "nicht" gehoert zu diesem Aussagewort
_PRAEDIKAT = frozenset({
    "verhandelbar", "möglich", "moeglich", "moglich", "gewünscht", "gewuenscht", "erwünscht", "erwuenscht",
    "angemeldet", "abgemeldet", "zugelassen", "vorhanden", "dabei", "inklusive", "inkl", "notwendig",
    "nötig", "noetig", "erforderlich", "verfügbar", "verfuegbar", "erreichbar", "gestattet", "erlaubt",
    "vorgesehen", "enthalten"})
# "Klima geht nicht fahrbereit": das "nicht" gehoert zum Verb davor
_VERB_VOR_NICHT = frozenset({
    "geht", "gehen", "gehts", "funktioniert", "funktionieren", "läuft", "laeuft", "lauft", "laufen", "klappt",
    "tut", "zieht", "schaltet", "springt", "heizt", "kühlt", "kuehlt", "öffnet", "oeffnet", "schließt",
    "schliesst", "leuchtet", "brennt", "lädt", "laedt", "arbeitet", "reagiert", "hält", "haelt", "passt",
    "stimmt", "piept", "dreht", "startet"})
# Steht direkt vor dem Stichwort ein solches Wort, regiert es das Stichwort
# ("keine Garantie ob unfallfrei") — dann keine Ausnahme.
_BINDE = frozenset({"ob", "für", "fuer", "dass", "daß", "bzgl", "bezüglich", "bezueglich", "hinsichtlich",
                    "betreffend", "wegen", "auf", "über", "ueber", "als", "wie", "zu", "zur", "zum"})
# Gross geschrieben, aber KEIN Nomen (Titelschreibung, Satzanfang)
_KEIN_NOMEN = frozenset({
    "mehr", "ganz", "zu", "prozent", "wirklich", "komplett", "völlig", "voellig", "vollig", "voll",
    "vollständig", "vollstaendig", "gänzlich", "gaenzlich", "total", "absolut", "richtig", "unbedingt",
    "immer", "so", "sehr", "gerade", "sicher", "hundertprozentig", "durchgehend", "lückenlos", "lueckenlos",
    "einwandfrei", "technisch", "top", "super", "echt", "tatsächlich", "tatsaechlich", "auch", "schon",
    "bereits", "direkt", "sofort", "jederzeit", "uneingeschränkt", "uneingeschraenkt", "rundum", "leider",
    "natürlich", "natuerlich", "definitiv", "zwingend", "nur", "sondern", "aber", "und", "oder", "ist",
    "war", "sind", "waren", "wird", "wurde", "das", "der", "die", "den", "dem", "des", "ein", "eine",
    "einen", "einem", "einer", "eines", "es", "er", "sie", "mit", "von", "vom", "zum", "zur", "im", "in",
    "am", "an", "auf", "aus", "bei", "für", "fuer", "nach", "vor", "seit", "über", "ueber", "unter", "ab",
    "bis", "als", "wie", "hier", "dort", "jetzt", "alle", "allem", "alles", "neu", "neue", "neuer",
    "neues", "gut", "sehr", "mega", "extrem", "wie", "dann", "da", "ja", "gerne", "bitte"})
_ADJ_ENDUNG = re.compile(
    r".*(?:frei|freie[nrsm]?|bereit\w*|gepflegt\w*|tauglich\w*|f(?:ä|ae)hig\w*|t(?:ü|ue)chtig\w*|los"
    r"|lose[nrsm]?|lich\w*|isch\w*|bar|bare[nrsm]?|ig|ige[nrsm]?|iert\w*)")

_RAND = "\"'„“”‚‘’»«*·:?!.,;()[]"
_STRICH = ("-", "–", "—")


class _Tok(NamedTuple):
    roh: str        # Originalschreibung ohne Rand-Satzzeichen
    klein: str      # klein geschrieben
    start: int
    klammer: bool   # ganz in Klammern: "(nicht)"
    strich: str     # "-" / "–" wenn das Wort auf einen Strich endet ("nicht-")


_WORT = re.compile(r"[^\s/|•]+")


def _tokens(t: str, a: int, b: int) -> List[_Tok]:
    out: List[_Tok] = []
    for m in _WORT.finditer(t, a, b):
        w = m.group(0)
        klammer = w[:1] in "([" and w[-1:] in ")]"
        roh = w.strip(_RAND)
        strich = ""
        if roh[-1:] in _STRICH:
            strich = roh[-1]
            roh = roh.rstrip("".join(_STRICH)).strip(_RAND)
        roh = roh.lstrip("".join(_STRICH))
        if not roh:
            out.append(_Tok("-", "-", m.start(), klammer, strich or "-"))
            continue
        out.append(_Tok(roh, roh.lower(), m.start(), klammer, strich))
    return out


def _ist_stoer(w: str) -> bool:
    return bool(_STOER.fullmatch(w))


# Werbewoerter, die auch mitten im Satz oft gross stehen ("TUEV Neu", "Top
# Zustand") — sie allein machen noch keine Titelschreibung.
_WERBEWORT = frozenset({"neu", "neue", "neuer", "neues", "top", "super", "gut", "mega", "extrem", "ja",
                        "bitte", "gerne"})


def _titelschrift(alle: List[_Tok]) -> bool:
    """Titelschreibung ("Nicht Wirklich Unfallfrei"): Gross-/Kleinschreibung
    sagt nichts ueber Nomen -> Nomen-Ausnahme aus."""
    return any(tk.roh[:1].isupper() and tk.klein not in _WERBEWORT
               and (tk.klein in _KEIN_NOMEN or _ist_stoer(tk.klein))
               for tk in alle[1:])


def _nomen(tk: _Tok, titel: bool) -> bool:
    if titel or tk.klammer:
        return False
    r, k = tk.roh, tk.klein
    if len(r) < 3 or not r[0].isupper() or not any(c.islower() for c in r):
        return False
    if k in _KEIN_NOMEN or k in _PRAEDIKAT or _ist_stoer(k) or re.search(r"\d", k):
        return False
    if _ADJ_ENDUNG.fullmatch(k) or (k.startswith("ge") and k.endswith("t")):
        return False
    return True


# ================================================ Satzteile
_TRENN = frozenset(",;!|•\n\r")
_ABKUERZUNG = frozenset({"lt", "ca", "inkl", "evtl", "ggf", "bzw", "zb", "z", "b", "u", "a", "nr", "usw",
                         "etc", "gem", "d", "bj", "ez", "vs"})


def _hat_stoerwort(s: str) -> bool:
    return bool(_STOER_PHRASE.search(s)) or any(_ist_stoer(w.strip(_RAND).lower()) for w in _WORT.findall(s))


def _satzteile(t: str) -> List[Tuple[int, int]]:
    """Satzteil-Grenzen. Eine kurze Klammer MIT Verneinungs-/Vorbehaltswort
    ("unfallfrei (laut Vorbesitzer)", "(nicht) fahrbereit") gehoert zum
    Satzteil, sonst trennt die Klammer. Punkt zwischen Ziffern ("05.2026")
    und nach Abkuerzungen ("lt.", "z.B.") trennt nicht."""
    bleibt = set()
    for m in re.finditer(r"[(\[]([^()\[\]\n]{0,60})[)\]]", t):
        if _hat_stoerwort(m.group(1)):
            bleibt.update((m.start(), m.end() - 1))
    spans: List[Tuple[int, int]] = []
    a = 0
    for i, ch in enumerate(t):
        grenze = False
        if ch in _TRENN:
            grenze = True
        elif ch in "()[]":
            grenze = i not in bleibt
        elif ch == "…":
            grenze = True
        elif ch == ".":
            if 0 < i < len(t) - 1 and t[i - 1].isdigit() and t[i + 1].isdigit():
                grenze = False
            else:
                wm = re.search(r"([A-Za-zÄÖÜäöüß]+)$", t[a:i])
                grenze = not (wm and wm.group(1).lower() in _ABKUERZUNG)
        if grenze:
            spans.append((a, i))
            a = i + 1
    spans.append((a, len(t)))
    return spans


def _satzteil_von(spans: List[Tuple[int, int]], pos: int) -> Tuple[int, int]:
    for s, e in spans:
        if s <= pos <= e:
            return s, e
    return 0, 0


# ================================================ Urteil je Vorkommen
def _stoer_vor(vor: List[_Tok], titel: bool, hu: bool = False, extra: frozenset = frozenset()) -> bool:
    """Steht vor dem Stichwort ein Verneinungs-/Vorbehaltswort, das NICHT zu
    einem anderen Nomen gehoert?"""
    for i, tk in enumerate(vor):
        if tk.strich and tk.strich != "-" and tk.klein != "-" and _ist_stoer(tk.klein):
            return True                       # "nicht–unfallfrei"
        if not _ist_stoer(tk.klein) and tk.klein not in extra:
            continue
        if hu and _hu_noch_ok(vor, i):
            continue
        if tk.klein in _NEG_ANH and not tk.klammer:
            zwischen = vor[i + 1:]
            if any(_nomen(z, titel) for z in zwischen) or (
                    tk.klein == "nicht" and any(z.klein in _PRAEDIKAT for z in zwischen)):
                if vor[-1].klein in _BINDE:
                    return True               # "keine Garantie ob unfallfrei"
                continue
        return True
    return False


def _stoer_nach(nach: List[_Tok], hu: bool = False, extra: frozenset = frozenset()) -> bool:
    for i, tk in enumerate(nach):
        if _ist_stoer(tk.klein) or tk.klein in extra:
            if hu and _hu_noch_ok(nach, i, am_ende=False):
                continue
            return True
    return False


def _stoer_rest(t: str, s: int, e: int, a: int, b: int, hu: bool = False,
                extra: frozenset = frozenset()) -> bool:
    """Verneinungs-/Vorbehaltswort im Satzteil ausserhalb der Stelle [a, b)?"""
    titel = _titelschrift(_tokens(t, s, e))
    return (bool(_STOER_PHRASE.search(t, s, e)) or _stoer_vor(_tokens(t, s, a), titel, hu, extra)
            or _stoer_nach(_tokens(t, b, e), hu, extra))


_NACH_JA_NEIN = re.compile(r"\s*[:?]\s*(?:leider\s+)?(nein|ja|jawohl)\b", re.I)


def _urteil_adj(t: str, a: int, b: int, s: int, e: int, extra: frozenset = frozenset()) -> str:
    """Urteil fuer ein Zusicherungs-Stichwort (Adjektiv: unfallfrei,
    fahrbereit, lueckenlos, EU-Import) an [a, b) im Satzteil [s, e).
    extra: weitere Vorbehaltswoerter nur fuer dieses Feld."""
    titel = _titelschrift(_tokens(t, s, e))

    def sv(toks: List[_Tok]) -> bool:
        return _stoer_vor(toks, titel, extra=extra)

    def sn(toks: List[_Tok]) -> bool:
        return _stoer_nach(toks, extra=extra)

    phrase = bool(_STOER_PHRASE.search(t, s, e))
    vor = _tokens(t, s, a)
    nach = _tokens(t, b, e)
    # "<Stichwort>: nein" / "<Stichwort>? (leider) nein" / "<Stichwort>: ja"
    m = _NACH_JA_NEIN.match(t, b, e)
    if m:
        if phrase or sv(vor) or sn(_tokens(t, m.end(), e)):
            return "unklar"
        return "nein" if m.group(1).lower() == "nein" else "ja"
    if re.match(r"\s*\?", t[b:e]):
        return "unklar"                        # "Unfallfrei?" — offene Frage
    # "nicht-unfallfrei" (Bindestrich); "nicht–unfallfrei" bleibt unklar
    if vor and vor[-1].strich == "-" and vor[-1].klein == "nicht" and not vor[-1].klammer:
        if phrase or sv(vor[:-1]) or sn(nach):
            return "unklar"
        return "nein"
    # "nicht nur unfallfrei" (Positivmuster)
    if len(vor) >= 2 and vor[-2].klein == "nicht" and vor[-1].klein == "nur" \
            and not (vor[-2].strich or vor[-1].strich or vor[-2].klammer):
        if phrase or sv(vor[:-2]) or sn(nach):
            return "unklar"
        return "ja"
    # Negativmuster: nicht|nie|keinesfalls|keineswegs (+ mehr/ganz/zu 100 %) direkt davor
    i = len(vor)
    while i > 0 and vor[i - 1].klein in _FUELL and not vor[i - 1].strich and not vor[i - 1].klammer:
        i -= 1
    if i > 0:
        tk = vor[i - 1]
        if tk.klein in _NEG_MUSTER and not tk.strich and not tk.klammer:
            if tk.klein == "nicht" and i >= 2 and vor[i - 2].klein in _VERB_VOR_NICHT:
                return "unklar"                # "Klima geht nicht fahrbereit"
            if phrase or sv(vor[:i - 1]) or sn(nach):
                return "unklar"
            return "nein"
    if phrase or sv(vor) or sn(nach):
        return "unklar"
    return "ja"


def _stellen_adj(t: str, rx: "re.Pattern", extra: frozenset = frozenset()) -> List[Tuple["re.Match", str]]:
    spans = _satzteile(t)
    out = []
    for m in rx.finditer(t):
        s, e = _satzteil_von(spans, m.start())
        out.append((m, _urteil_adj(t, m.start(), m.end(), s, e, extra)))
    return out


# Positivmuster fuer Nomen: "kein/keine/keinen/keinerlei/ohne [Beiwort] <Nomen>"
_POS_NEG = frozenset({"kein", "keine", "keinen", "keinerlei", "ohne"})
_POS_BEIWORT = re.compile(r"(?:jeglich|irgendwelch|einzig|nennenswert|gr(?:ö|oe|o)(?:ß|ss)er|bekannt)\w*")


def _positivmuster(vor: List[_Tok], neg: frozenset = _POS_NEG) -> int:
    """Anzahl der Woerter des Positivmusters direkt vor dem Nomen (0 = keins)."""
    if len(vor) >= 2 and _POS_BEIWORT.fullmatch(vor[-1].klein) and vor[-2].klein in neg:
        n = 2
    elif vor and vor[-1].klein in neg:
        n = 1
    else:
        return 0
    return 0 if any(tk.klammer or tk.strich for tk in vor[-n:]) else n


def _nomen_verneint_sauber(t: str, m: "re.Match", spans, neg: frozenset = _POS_NEG) -> bool:
    """Ist das Nomen an m sauber verneint ("kein Hagelschaden") — ohne
    weiteren Vorbehalt im Satzteil?"""
    s, e = _satzteil_von(spans, m.start())
    vor = _tokens(t, s, m.start())
    n = _positivmuster(vor, neg)
    if not n:
        return False
    titel = _titelschrift(_tokens(t, s, e))
    return not (_STOER_PHRASE.search(t, s, e) or _stoer_vor(vor[:-n], titel)
                or _stoer_nach(_tokens(t, m.end(), e)))


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


# ------------------------------------------------ HU
_HU_WORT = r"(?:hu|t(?:ü|ue|u)v|hauptuntersuchung)"
_HU_RE = re.compile(r"\b" + _HU_WORT + r"\b", re.I)
_AU = r"(?:\s*/\s*au|\s*&\s*au|\s+und\s+au)?"
_HU_KEIN = re.compile(r"\b(?:kein|keine|keinen|ohne)\s+(?:g(?:ü|ue|u)ltige[nrs]?\s+|aktuelle[nrs]?\s+)?"
                      + _HU_WORT + r"\b" + _AU, re.I)
_HU_ABGEL_VOR = re.compile(r"\b(?:abgelaufene[nrs]?|(?:ü|ue|u)berzogene[nrs]?)\s+" + _HU_WORT + r"\b" + _AU, re.I)
_HU_NEIN_NACH = re.compile(r"\b" + _HU_WORT + r"\b" + _AU + r"\s*[:?]\s*(?:leider\s+)?nein\b", re.I)
_ABGEL = re.compile(r"\b(?:abgelaufen|abgel|(?:ü|ue|u)berzogen|(?:ü|ue|u)berf(?:ä|ae|a)llig|f(?:ä|ae|a)llig)\b", re.I)
_HU_DATUM = re.compile(
    r"\b" + _HU_WORT + r"\b" + _AU + r"\s*(?:neu\s*)?(?:ist\s+)?(?:noch\s+)?(?:g(?:ü|ue|u)ltig\s+)?(?:bis\s*)?"
    r"(?::\s*)?(?:zum\s+)?(?:(\d{1,2})\s*\.\s*)?(\d{1,2})\s*[./-]\s*(\d{4}|\d{2})(?![./-]?\d)", re.I)
_HU_NEU = re.compile(r"\b" + _HU_WORT + r"\b" + _AU + r"\s*(?:ist\s+)?(?:ganz\s+)?neu\b"
                     r"|\b(?:neue[rs]?|frische[rs]?)\s+" + _HU_WORT + r"\b", re.I)
# Nur bei der HU: "TUEV neu machen", "HU muss neu" -> kein "Ja"
_HU_STOER = frozenset({"machen", "werden", "wird", "muss", "müssen", "muessen", "nötig", "noetig", "notwendig",
                       "erforderlich", "ansteht", "anstehend"})
# Nur beim HU-DATUM: "HU 09/2026 neu gemacht" ist das Pruefdatum, nicht "gueltig bis"
_HU_PRUEFDATUM = _HU_STOER | frozenset({"gemacht", "durchgeführt", "durchgefuehrt", "erneuert", "bestanden",
                                        "vom", "abgenommen", "seit"})
# Zwischen HU-Wort und "abgelaufen" erlaubt, ohne dass ein anderes Nomen gemeint ist
_HU_KOPULA = frozenset({"ist", "sind", "war", "waren", "leider", "bereits", "schon", "jetzt", "mittlerweile",
                        "inzwischen", "gerade", "eben"})
_MONAT = re.compile(r"(?:januar|jan|februar|feb|m(?:ä|ae|a)rz|mrz|april|apr|mai|juni|jun|juli|jul|august|aug"
                    r"|september|sept|sep|oktober|okt|november|nov|dezember|dez)")
_ZEIT_WORT = frozenset({"tag", "tage", "tagen", "woche", "wochen", "monat", "monate", "monaten", "jahr", "jahre",
                        "jahren", "ende", "anfang", "mitte", "kurzem", "langem", "sommer", "winter", "frühjahr",
                        "fruehjahr", "herbst", "letzten", "letztes", "letzte", "vorjahr", "au"})
# Andere Dinge, die "abgelaufen" sein koennen (auch fuer klein geschriebene Texte)
_FREMD_ABLAUF = ("bremsbel", "bremsscheib", "bremse", "garantie", "gewährleist", "gewaehrleist", "leasing",
                 "reifen", "versicherung", "batterie", "akku", "vertrag", "zahnriemen", "inspektion", "service",
                 "wartung", "ölwechsel", "oelwechsel", "finanzierung", "kennzeichen", "frist", "zulassung")


def _hu_noch_ok(toks: List[_Tok], i: int, am_ende: bool = True) -> bool:
    """"TUEV noch bis 05/2027", "noch TUEV bis ..." — dieses "noch" ist kein Vorbehalt."""
    if toks[i].klein != "noch":
        return False
    if i + 1 >= len(toks):
        return am_ende                         # direkt vor dem HU-Stichwort
    n = toks[i + 1].klein
    return n in ("bis", "gültig", "gueltig") or bool(_HU_RE.fullmatch(n)) or bool(re.match(r"\d", n))


def _fremdes_nomen(tk: _Tok, titel: bool) -> bool:
    k = tk.klein
    if _HU_RE.fullmatch(k) or k in _ZEIT_WORT or _MONAT.fullmatch(k.rstrip(".")) or re.search(r"\d", k):
        return False
    if k.startswith(_FREMD_ABLAUF):
        return True
    return _nomen(tk, titel)


def _abgelaufen(mj: str, heute: date) -> bool:
    r = re.fullmatch(r"(\d{2})/(\d{4})", mj or "")
    if not r:
        return False
    return (int(r.group(2)), int(r.group(1))) < (heute.year, heute.month)


def _hu_abgelaufen_hinweis(mj: str, fund: str) -> str:
    return (f"HU abgelaufen ({mj}) laut Inserat („{fund}“) — „HU/AU vorhanden“ bitte beim "
            "Verkäufer erfragen und selbst wählen.")


class _HuBefund(NamedTuple):
    art: str                 # "nein" | "ja" | "unklar" | "vergangen"
    mj: str                  # MM/JJJJ oder ""
    m: "re.Match"


def _hu_mj(m: "re.Match") -> str:
    mon = int(m.group(2))
    if not 1 <= mon <= 12:
        return ""
    mj = PV.monat_jahr_text(f"{m.group(2)}/{m.group(3)}", "hu")
    return mj if re.fullmatch(r"\d{2}/\d{4}", mj or "") else ""


def _hu_befunde(t: str, heute: date) -> List[_HuBefund]:
    out: List[_HuBefund] = []
    for s, e in _satzteile(t):
        if not _HU_RE.search(t, s, e):
            continue
        titel = _titelschrift(_tokens(t, s, e))
        daten = [(m, _hu_mj(m)) for m in _HU_DATUM.finditer(t, s, e)]
        daten = [(m, mj) for m, mj in daten if mj]
        # --- Nein: "keine HU", "abgelaufener TUEV", "TUEV: nein"
        nein_m = None
        for rx in (_HU_KEIN, _HU_ABGEL_VOR, _HU_NEIN_NACH):
            nein_m = rx.search(t, s, e)
            if nein_m:
                a, b = nein_m.start(), nein_m.end()
                break
        unklar_m = None
        if not nein_m:
            # --- "abgelaufen/ueberzogen/faellig" im Satzteil des HU-Worts
            for m in _ABGEL.finditer(t, s, e):
                vor = _tokens(t, s, m.start())
                j = len(vor)
                while j > 0 and vor[j - 1].klein in _HU_KOPULA:
                    j -= 1
                davor = vor[j - 1] if j > 0 else None
                if davor is not None and _fremdes_nomen(davor, titel):
                    continue                   # "Bremsbelaege abgelaufen" — nur das Datum zaehlt
                if davor is not None and davor.klein in _NEG_MUSTER:
                    unklar_m = m               # "noch nicht abgelaufen"
                    break
                hu_m = [h for h in _HU_RE.finditer(t, s, e)]
                a = min([h.start() for h in hu_m] + [m.start()])
                b = max([h.end() for h in hu_m if h.start() < m.start()] + [m.end()])
                nein_m = m
                break
        if unklar_m:
            out.append(_HuBefund("unklar", "", unklar_m))
            continue
        if nein_m:
            if _stoer_rest(t, s, e, a, b, hu=True, extra=_HU_STOER) or any(
                    _ist_stoer(tk.klein) and not _hu_noch_ok(_tokens(t, a, b), i)
                    for i, tk in enumerate(_tokens(t, a, b))
                    if not (nein_m.start() <= tk.start < nein_m.end())):
                out.append(_HuBefund("unklar", "", nein_m))
            elif any(not _abgelaufen(mj, heute) for _, mj in daten):
                # "TUEV 10/2027 abgelaufen" — Datum gueltig, Text abgelaufen
                out.append(_HuBefund("unklar", "", nein_m))
            else:
                out.append(_HuBefund("nein", "", nein_m))
            continue
        # --- Ja-Kandidaten: Datum / "neu"
        for m, mj in daten:
            frage = re.match(r"\s*\?", t[m.end():e])
            if frage or _stoer_rest(t, s, e, m.start(), m.end(), hu=True, extra=_HU_PRUEFDATUM):
                out.append(_HuBefund("unklar", mj, m))
            elif _abgelaufen(mj, heute):
                out.append(_HuBefund("vergangen", mj, m))
            else:
                out.append(_HuBefund("ja", mj, m))
        for m in _HU_NEU.finditer(t, s, e):
            frage = re.match(r"\s*\?", t[m.end():e])
            if frage or _stoer_rest(t, s, e, m.start(), m.end(), hu=True, extra=_HU_STOER):
                out.append(_HuBefund("unklar", "", m))
            else:
                out.append(_HuBefund("ja", "", m))
    return out


def hu(v: dict, text: str, heute: Optional[date] = None) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
    """Liefert (felder, hinweise)."""
    heute = heute or _heute_berlin()
    raus: Dict[str, Dict[str, Any]] = {}
    hinweise: List[str] = []
    roh = str(v.get("hu") or "").strip()
    portal_mj = PV.monat_jahr_text(roh, "hu") if roh else ""
    if not re.fullmatch(r"\d{2}/\d{4}", portal_mj or ""):
        portal_mj = ""
    portal_neu = roh.lower() in ("neu", "new")
    portal_gueltig = (portal_mj and not _abgelaufen(portal_mj, heute)) or portal_neu
    bitte = "„HU/AU vorhanden“ bitte beim Verkäufer erfragen und selbst wählen."

    bef = _hu_befunde(text, heute)
    nein = [b for b in bef if b.art == "nein"]
    ja = [b for b in bef if b.art == "ja"]
    unklar = [b for b in bef if b.art == "unklar"]
    vergangen = [b for b in bef if b.art == "vergangen"]

    if unklar:
        hinweise.append(f"HU-Angabe im Inserat nicht eindeutig („{_fund(text, unklar[0].m)}“) — {bitte}")
        return raus, hinweise
    if nein and ja:
        hinweise.append(f"Inserat widersprüchlich zur HU („{_fund(text, nein[0].m)}“ / "
                        f"„{_fund(text, ja[0].m)}“) — {bitte}")
        return raus, hinweise
    if nein:
        # K2: "abgelaufen / keine HU" im Text hat Vorrang — gegen ein gueltiges
        # Portalfeld aber nur ein Hinweis, nie "Ja" (inserat3).
        if portal_gueltig:
            hinweise.append(f"Inserat widersprüchlich: Portalfeld „HU {portal_mj or roh}“, im Text "
                            f"„{_fund(text, nein[0].m)}“ — {bitte}")
            return raus, hinweise
        raus["hu_valid"] = _eintrag("Nein", "listing_description", _fund(text, nein[0].m))
        return raus, hinweise
    if vergangen:
        if ja:
            hinweise.append(f"Inserat widersprüchlich zur HU („{_fund(text, vergangen[0].m)}“ / "
                            f"„{_fund(text, ja[0].m)}“) — {bitte}")
        else:
            hinweise.append(_hu_abgelaufen_hinweis(vergangen[0].mj, _fund(text, vergangen[0].m)))
        return raus, hinweise
    if ja:
        text_mj = next((b.mj for b in ja if b.mj), "")
        andere = {b.mj for b in ja if b.mj}
        if portal_mj and _abgelaufen(portal_mj, heute) or len(andere) > 1 \
                or (portal_mj and text_mj and portal_mj != text_mj):
            hinweise.append(f"Inserat widersprüchlich zur HU: Portalfeld „HU {portal_mj or roh}“, im Text "
                            f"„{_fund(text, ja[0].m)}“ — {bitte}")
            return raus, hinweise
        fund_ja = next((b for b in ja if b.mj), ja[0])
        raus["hu_valid"] = _eintrag("Ja", "listing_description", _fund(text, fund_ja.m))
        if text_mj:
            raus["hu_until"] = _eintrag(text_mj, "listing_description", _fund(text, fund_ja.m))
        elif portal_mj:
            raus["hu_until"] = _eintrag(portal_mj, "listing_field", f"HU {portal_mj}")
        return raus, hinweise

    if portal_mj:
        if _abgelaufen(portal_mj, heute):
            hinweise.append(_hu_abgelaufen_hinweis(portal_mj, f"HU {portal_mj}"))
            return raus, hinweise
        raus["hu_valid"] = _eintrag("Ja", "listing_field", f"HU {portal_mj}")
        raus["hu_until"] = _eintrag(portal_mj, "listing_field", f"HU {portal_mj}")
        return raus, hinweise
    if portal_neu:
        raus["hu_valid"] = _eintrag("Ja", "listing_field", "HU neu")
    return raus, hinweise


# ------------------------------------------------ Scheckheft
_SCHECK = r"(?:scheckheft\w*|serviceheft\w*|service-?historie|wartungs-?historie|servicebuch|wartungsheft|inspektionsheft)"
_VOLL = r"(?:l(?:ü|ue|u)ckenlose?[nrsm]?|vollst(?:ä|ae|a)ndige?[nrsm]?)"
_SCHECK_VOLL = re.compile(r"\b" + _VOLL + r"\s+" + _SCHECK + r"|\b" + _SCHECK + r"\s+(?:ist\s+)?" + _VOLL + r"\b",
                          re.I)
_SCHECK_KEIN = re.compile(r"\b(?:kein|keine|keinen|keines|ohne)\s+" + _SCHECK
                          + r"|\b" + _SCHECK + r"\s+(?:fehlt|nicht\s+vorhanden)\b|\bnicht\s+scheckheft\s*gepflegt\b",
                          re.I)
_SCHECK_LUECKE = re.compile(r"\b" + _SCHECK + r"\s+(?:ist\s+)?(?:unvollst\w*|l(?:ü|ue|u)ckenhaft\w*)"
                            r"|\b(?:unvollst(?:ä|ae|a)ndige?[nrsm]?|l(?:ü|ue|u)ckenhafte?[nrsm]?)\s+" + _SCHECK, re.I)
# "lueckenlos scheckheftgepflegt bis 2019" -> danach Luecke, also kein "Ja, lueckenlos"
_SCHECK_VORBEHALT = frozenset({"bis", "damals", "anfangs", "früher", "frueher", "anfänglich", "anfaenglich"})
_SCHECK_NUR = re.compile(r"\bscheckheft\s+gepflegt\w*|\b" + _SCHECK, re.I)


def scheckheft(text: str) -> Dict[str, Any]:
    """Liefert {"wert": ..} oder {"hinweis": ..} oder {}."""
    t = text
    spans = _satzteile(t)
    voll = _stellen_adj(t, _SCHECK_VOLL, _SCHECK_VORBEHALT)
    voll_ja = [m for m, u in voll if u == "ja"]
    voll_nicht = [m for m, u in voll if u != "ja"]
    kein_sauber, kein_unklar = [], []
    for m in _SCHECK_KEIN.finditer(t):
        s, e = _satzteil_von(spans, m.start())
        (kein_unklar if _stoer_rest(t, s, e, m.start(), m.end()) else kein_sauber).append(m)
    luecke = list(_SCHECK_LUECKE.finditer(t))
    nur = _SCHECK_NUR.search(t)
    if voll_ja and (kein_sauber or kein_unklar or luecke or voll_nicht):
        andere = (kein_sauber + kein_unklar + luecke + voll_nicht)[0]
        return {"hinweis": "Das Inserat ist beim Scheckheft widersprüchlich („" + _fund(text, voll_ja[0])
                           + "“ / „" + _fund(text, andere) + "“). Bitte das Scheckheft ansehen und dann wählen."}
    if voll_ja:
        return {"wert": _eintrag("ja", "listing_description", _fund(text, voll_ja[0]))}
    if kein_sauber and not (kein_unklar or luecke or voll_nicht):
        return {"wert": _eintrag("nein", "listing_description", _fund(text, kein_sauber[0]))}
    if voll_nicht:
        return {"hinweis": "Inserat: „" + _fund(text, voll_nicht[0]) + "“ — also nicht „Ja, lückenlos“. "
                           "Bitte das Scheckheft ansehen und dann wählen."}
    if kein_sauber or kein_unklar or luecke:
        m = (kein_unklar + luecke + kein_sauber)[0]
        return {"hinweis": "Inserat zum Scheckheft nicht eindeutig („" + _fund(text, m) + "“). "
                           "Bitte das Scheckheft ansehen und dann wählen."}
    if nur:
        return {"hinweis": "Inserat: „" + _fund(text, nur) + "“ — „scheckheftgepflegt“ heißt nicht "
                           "zwingend lückenlos. Bitte das Scheckheft ansehen und dann wählen."}
    return {}


# ------------------------------------------------ Unfall
_UNFALLFREI = re.compile(r"\bunfall(?:-|\s)?frei(?:e[nrsm]?)?\b", re.I)     # auch "Unfall frei"
_UNFALL = re.compile(r"\b(?:unf(?:ä|ae|a)lle[n]?|unfall(?:-?sch(?:ä|ae|a)den|-?schadens?|wagen|fahrzeug|auto"
                     r"|instandsetzung|beteiligung|historie)?)\b(?![-\s]?frei)", re.I)
# Ausdrueckliche Negativ-Nomen: "Unfallwagen", "Unfallschaden" ohne eigene Verneinung
_UNFALL_NEGATIV = re.compile(r".*(?:wagen|fahrzeug|auto|sch(?:ä|ae|a)den|schadens?)", re.I)
# Andere Schaeden: kein klares "Nein", aber ein Widerspruch zu "unfallfrei: Ja"
# (Hagelschaden neben "kein Unfallschaden" ist strittig).
_ANDERER_SCHADEN = re.compile(
    r"\b(?:hagel|vor|karosserie|blech|front|heck|seiten|rahmen|total|park|wild|wasser|brand|flut|bagatell"
    r"|lack)?sch(?:ä|ae|a)den\b|\bhagel\w*|\bbesch(?:ä|ae|a)digt\w*|\binstand\s*gesetzt\w*|\bcrash\w*"
    r"|\bparkrempler\w*", re.I)
_NEG_SCHADEN = _POS_NEG | {"nicht", "keinem", "keiner", "keines"}
_REPARIERT = re.compile(r"\brepariert|\brepaired|\binstand\s*gesetzt")


def _urteil_unfall_nomen(t: str, m: "re.Match", spans) -> str:
    """Urteil fuer das FELD "unfallfrei" aus einem Unfall-Nomen."""
    s, e = _satzteil_von(spans, m.start())
    vor = _tokens(t, s, m.start())
    n = _positivmuster(vor)
    if n:
        titel = _titelschrift(_tokens(t, s, e))
        if _STOER_PHRASE.search(t, s, e) or _stoer_vor(vor[:-n], titel) or _stoer_nach(_tokens(t, m.end(), e)):
            return "unklar"
        return "ja"                            # "kein Unfall", "keinerlei Unfallschaeden"
    if _UNFALL_NEGATIV.fullmatch(m.group(0)):
        if _stoer_rest(t, s, e, m.start(), m.end()):
            return "unklar"                    # "Unfallschaden: nicht bekannt"
        return "nein"                          # "Unfallwagen", "Unfallschaden hinten"
    return "unklar"                            # "hatte einen Unfall", "nie einen Unfall"


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


def _zusammen(urteile: List[Tuple["re.Match", str]],
              blocker: List["re.Match"]) -> Tuple[Optional[str], Optional["re.Match"]]:
    """Mehrere Vorkommen -> ein Textwert: "Ja", "Nein", "unklar" oder None."""
    ja = [m for m, u in urteile if u == "ja"]
    nein = [m for m, u in urteile if u == "nein"]
    unklar = [m for m, u in urteile if u == "unklar"]
    if unklar:
        return "unklar", unklar[0]
    if ja and nein:
        return "unklar", nein[0]
    if nein:
        return "Nein", nein[0]
    if ja and blocker:
        return "unklar", blocker[0]
    if ja:
        return "Ja", ja[0]
    return None, None


def unfall(v: dict, text: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Liefert (eintrag, hinweis) — hoechstens eins von beiden."""
    t = text
    spans = _satzteile(t)
    urteile = _stellen_adj(t, _UNFALLFREI)
    urteile += [(m, _urteil_unfall_nomen(t, m, spans)) for m in _UNFALL.finditer(t)]
    urteile.sort(key=lambda x: x[0].start())
    anderer = [m for m in _ANDERER_SCHADEN.finditer(t) if not _nomen_verneint_sauber(t, m, spans, _NEG_SCHADEN)]
    text_wert, fund = _zusammen(urteile, anderer)

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
_FAHRBEREIT = re.compile(r"\bfahr(?:bereit|tauglich|f(?:ä|ae|a)hig|t(?:ü|ue|u)chtig)(?:e[nrsm]?)?\b", re.I)
_FAHR_NEIN = re.compile(r"\bfahr(?:unt(?:ü|ue|u)chtig|unf(?:ä|ae|a)hig|untauglich)(?:e[nrsm]?)?\b", re.I)
# Kein klares "Nein", aber ein Widerspruch zu "Fahrtauglich: Ja"
_FAHR_ANDERS = re.compile(
    r"\b(?:motor|getriebe|kupplungs|zahnriemen)schaden\b|\b(?:motor|getriebe|kupplung)\s+(?:ist\s+)?defekt"
    r"|\bspringt\s+nicht\s+(?:mehr\s+)?an|\bl(?:ä|ae|a)uft\s+nicht\b|\babschlepp\w*|\bbastler\w*"
    r"|\bnur\s+(?:f(?:ü|ue|u)r\s+|als\s+)?(?:export|teilespender|trailer)", re.I)


def fahrbereit(v: dict, text: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Liefert (eintrag, hinweis)."""
    t = text
    spans = _satzteile(t)
    urteile = _stellen_adj(t, _FAHRBEREIT)
    # "fahruntuechtig" ohne Vorbehalt = ausdrueckliches Nein; verneint/eingeschraenkt = unklar
    urteile += [(m, "nein" if u == "ja" else "unklar") for m, u in _stellen_adj(t, _FAHR_NEIN)]
    urteile.sort(key=lambda x: x[0].start())
    anderer = [m for m in _FAHR_ANDERS.finditer(t) if not _nomen_verneint_sauber(t, m, spans, _NEG_SCHADEN)]
    text_wert, fund = _zusammen(urteile, anderer)

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


_EU_IMPORT = re.compile(r"\b(?:eu[- ]?import|re-?import|reimport|importfahrzeug)\b", re.I)


def eu_import(text: str) -> Optional[Dict[str, Any]]:
    stellen = _stellen_adj(text, _EU_IMPORT)
    if not stellen or any(u != "ja" for _, u in stellen):
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
    das, was der Sucher selbst pruefen muss. Wirft nie; faellt eine Regel aus,
    bleiben die anderen (und ihr Feld bleibt leer)."""
    v = v or {}
    text = _text(v)
    felder: Dict[str, Dict[str, Any]] = {}
    hinweise: List[str] = []

    def _schluessel():
        s = schluessel(v, text)
        if s:
            felder["schluessel_anzahl"] = s

    def _hu():
        hu_felder, hu_hinweise = hu(v, text, heute)
        felder.update(hu_felder)
        hinweise.extend(hu_hinweise)

    def _scheckheft():
        sh = scheckheft(text)
        if sh.get("wert"):
            felder["service_book"] = sh["wert"]
        elif sh.get("hinweis"):
            hinweise.append(sh["hinweis"])

    def _unfall():
        u, u_hinweis = unfall(v, text)
        if u:
            felder["accident_free"] = u
        if u_hinweis:
            hinweise.append(u_hinweis)

    def _fahrbereit():
        f, f_hinweis = fahrbereit(v, text)
        if f:
            felder["drivable"] = f
        if f_hinweis:
            hinweise.append(f_hinweis)

    def _eu():
        e = eu_import(text)
        if e:
            felder["eu_import"] = e

    def _reifen():
        b = bereifung(text)
        if b:
            felder["tires"] = b

    for regel in (_schluessel, _hu, _scheckheft, _unfall, _fahrbereit, _eu, _reifen):
        try:
            regel()
        except Exception:  # noqa: BLE001 — Vorschlaege sind Beiwerk
            pass
    return {"felder": felder, "hinweise": hinweise,
            "bekannte_maengel": [str(m)[:200] for m in (v.get("known_defects") or []) if str(m or "").strip()][:20]}
