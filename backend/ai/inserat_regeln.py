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

Runde inserat4 (28.09.2026, Entscheidung Auftraggeber): Die Zusicherungen
(HU, Scheckheft, unfallfrei, fahrbereit, EU-Import) sind im Vertragsdialog
nur noch VORSCHLAEGE mit Fundstelle und "Übernehmen"-Knopf — der Dialog setzt
sie nie selbst. Die Regeln bleiben so genau wie moeglich:
  * Umgangssprache/Formular ("nö", "nee", "net", "k.A.", "❌", "ausgeschlossen",
    "Fehlanzeige"); nach "<Stichwort>:" ist nur ja/x/✓ ein "ja".
  * Ein Folgesatzteil ohne eigenes Stichwort, der nur einen Vorbehalt traegt
    ("Unfallfrei, laut Vorbesitzer", ", soweit bekannt", ", leider nicht"),
    gehoert zum Stichwort davor.
  * Hoerensagen (sagt/meinte/gemaess/Vorbesitzer) und Zeitraeume ("seit 2019",
    "bis letztes Jahr", "bei uns"; nicht "seit Erstzulassung") -> unklar.
  * Unfall-Komposita ("Auffahrunfall") widersprechen "unfallfrei";
    "keine groesseren Unfaelle" deutet auf kleinere -> unklar mit Hinweis.
  * fahrbereit in Vergangenheit/Bedingung (war, waere, wenn, nach) -> unklar.
  * HU: "TÜV neu" als Angebot ("auf Wunsch", "gegen Aufpreis") und "TÜV neu
    gemacht 09/2024" -> unklar; "fällig" mit Zeitangabe -> unklar;
    "TÜV-relevante Mängel" ist kein HU-Stichwort.
  * Laufzeit: jeder Satzteil wird einmal zerlegt (vorher quadratisch),
    Texte werden auf 20.000 Zeichen begrenzt.

Runde inserat5 (28.09.2026, Regel Auftraggeber): AUS DEM INSERAT WIRD NICHTS
MEHR UNGEFRAGT IN DEN VERTRAG GESCHRIEBEN — auch Bereifung und
Schluesselanzahl sind nur noch Vorschlaege. Dazu:
  * Bereifung/Schluessel beachten Frage und Verneinung ("Keine Winterreifen
    dabei", "Winterreifen dabei? Nein", "Reifen gesucht", "2 Schlüssel? Nein,
    nur einer"); "4-fach"/"8-fach" nur mit Reifen-Bezug ("4-fach Airbags").
  * HU-Angebot im Folgesatzteil mit Praeposition (", auf Wunsch", ", gegen
    Aufpreis", ", nach Absprache") und "TÜV neu vor 2 Jahren/bei Abholung/mit
    Mängelbericht" -> unklar.
  * Kappung je Teil (Beschreibung 20.000, Ausstattung/Maengel je 5.000),
    ueberlange Teile behalten Anfang und Ende.
  * Symbol-Verneinungen (🚫 👎 ☐ "[ ]" "(?)"), "scheint", "theoretisch",
    "naja", "wer weiß"; Antriebsmangel ("Zahnriemen gerissen", "Notlauf")
    widerspricht "fahrbereit".
"""
from __future__ import annotations

import bisect
import logging
import re
from datetime import date, datetime, timezone
from functools import lru_cache
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

import protokoll_vergleich as PV

log = logging.getLogger("autohandel")

_ZAHLWORT = {"ein": 1, "einen": 1, "einem": 1, "1": 1, "zwei": 2, "2": 2, "drei": 3, "3": 3,
             "vier": 4, "4": 4}


# inserat4 (Laufzeit): laengere Texte werden abgeschnitten — ein echtes Inserat
# ist deutlich kuerzer, und die Vorschlaege sind nur Beiwerk.
# inserat5 (Pruefung Runde 4): JE TEIL gekappt, nicht der zusammengesetzte
# Text — sonst fiel ein Mangel am Ende (known_defects, Schluss der
# Beschreibung: "Unfallfrei … Unfallschaden vorne") still weg. Ein zu langer
# Teil behaelt Anfang UND Ende (dort stehen Maengel und Einschraenkungen).
_MAX_TEXT = 20_000          # Beschreibung
_MAX_LISTE = 5_000          # Ausstattung, bekannte Maengel (je Liste)


def _kappen(s: str, n: int) -> str:
    if len(s) <= n:
        return s
    ende = n // 4
    return s[:n - ende] + "\n…\n" + s[-ende:]


def _text(v: dict) -> str:
    """Beschreibung + Ausstattungsliste + bekannte Maengel (Originalschreibung —
    die Gross-/Kleinschreibung braucht die Nomen-Ausnahme)."""
    teile = [_kappen(str(v.get("description") or ""), _MAX_TEXT)]
    for liste in ("features", "known_defects"):
        eintraege = "\n".join(str(x) for x in (v.get(liste) or []) if x)
        if eintraege:
            teile.append(_kappen(eintraege, _MAX_LISTE))
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
    r"|gekauft|erworben|gesagt|erz(?:ä|ae|a)hlt|versichert|sonst|ansonsten"
    # inserat4 (Pruefung Runde 3): Umgangssprache und Formular ("Unfallfrei: nö",
    # "net fahrbereit", "k.A.", "❌", "EU-Import ausgeschlossen")
    r"|n(?:ö|oe)|nee|ne|jein|net|ned|nich|garnicht|garnich|k\.a|fehlanzeige|ausgeschlossen|❌|✗|✘|✖"
    # Hoerensagen: "sagt der Vorbesitzer", "gemäß Vorbesitzer", "Vorbesitzer meinte"
    r"|sagt|sagte|sagen|meint|meinte|meinten|gem(?:ä|ae|a)(?:ß|ss)|gem|vorbesitz[\w-]*|vorhalter[\w-]*"
    # inserat5 (Pruefung Runde 4): "Scheint unfallfrei", "Theoretisch fahrbereit",
    # "Unfallfrei … naja", Symbol-Verneinungen ("Unfallfrei 🚫", "👎", "☐")
    r"|scheint|scheinen|theoretisch|naja|🚫|👎[\U0001F3FB-\U0001F3FF]?|⛔|☐|□)")
# Stoerwoerter, die Nomen sind (gross geschrieben ist hier normal, keine Titelschrift)
_STOER_NOMEN = re.compile(r"(?:vorbesitz|vorhalter|fehlanzeige)[\w-]*")
# Mehrwort-Vorbehalte im Satzteil
_STOER_PHRASE = re.compile(
    r"\bbis\s+auf\b|\bbei\s+mir\b|\bso\s+gut\s+wie\b|\bmehr\s+oder\s+weniger\b"
    r"|\bim\s+(?:grunde|prinzip|wesentlichen|gro(?:ß|ss)en\s+und\s+ganzen)\b|\bnur\s+(?:mit|noch|kurz\w*)\b"
    r"|\bseit\s+ich\b|\bin\s+meine[mr]\s+(?:besitz|zeit|hand)\b|\bmeine[rs]?\s+(?:meinung|zeit)\b"
    # inserat4: "Bei uns unfallfrei", "nach Angaben", "so der Verkäufer"
    r"|\bbei\s+uns\b|\bnach\s+angabe|\bso\s+(?:der|die)\s+(?:vorbesitz\w*|verk(?:ä|ae|a)ufer\w*|halter\w*"
    r"|besitzer\w*|h(?:ä|ae|a)ndler\w*)"
    # inserat5: "Unfallfrei - aber wer weiß das schon"
    r"|\bwer\s+wei(?:ß|ss)\b", re.I)
# Diese Verneinungen koennen zu einem ANDEREN Nomen gehoeren ("keine Maengel")
_NEG_ANH = frozenset({"nicht", "kein", "keine", "keinen", "keinem", "keiner", "keines", "keinerlei", "ohne"})
# Negativmuster direkt vor dem Stichwort + erlaubte Fuellwoerter dazwischen
_NEG_MUSTER = frozenset({"nicht", "nie", "niemals", "keinesfalls", "keineswegs",
                         # inserat4: Umgangssprache wie "nicht" ("net fahrbereit")
                         "nich", "net", "ned", "garnicht", "garnich"})
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
    ende: int = -1  # Ende des Worts im Text (inserat4: Satzteil einmal zerlegen)


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
            out.append(_Tok("-", "-", m.start(), klammer, strich or "-", m.end()))
            continue
        out.append(_Tok(roh, roh.lower(), m.start(), klammer, strich, m.end()))
    return out


@lru_cache(maxsize=8192)
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
               and (tk.klein in _KEIN_NOMEN or (_ist_stoer(tk.klein) and not _STOER_NOMEN.fullmatch(tk.klein)))
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
_ABKUERZUNG = frozenset({"lt", "ca", "inkl", "evtl", "ggf", "bzw", "zb", "z", "b", "u", "a", "nr", "usw",
                         "etc", "gem", "d", "bj", "ez", "vs"})
# Moegliche Satzteil-Grenzen (inserat4: nur diese Zeichen ansehen statt jedes Zeichens)
_GRENZ_KANDIDAT = re.compile(r"[,;!|•\n\r()\[\]….]")


def _hat_stoerwort(s: str) -> bool:
    return bool(_STOER_PHRASE.search(s)) or any(_ist_stoer(w.strip(_RAND).lower()) for w in _WORT.findall(s))


def _satzteile_berechnen(t: str) -> List[Tuple[int, int]]:
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
    n = len(t)
    for m in _GRENZ_KANDIDAT.finditer(t):
        i = m.start()
        ch = t[i]
        if ch in "()[]":
            grenze = i not in bleibt
        elif ch == ".":
            if 0 < i < n - 1 and t[i - 1].isdigit() and t[i + 1].isdigit():
                grenze = False
            else:
                # Wort direkt vor dem Punkt (rueckwaerts, nur Buchstaben)
                j = i
                while j > a and (t[j - 1].isascii() and t[j - 1].isalpha() or t[j - 1] in "ÄÖÜäöüß"):
                    j -= 1
                wort = t[j:i].lower()
                grenze = not (j < i and (wort in _ABKUERZUNG or (wort == "k" and t[i + 1:i + 2] in ("A", "a"))))
        else:
            grenze = True
        if grenze:
            spans.append((a, i))
            a = i + 1
    spans.append((a, n))
    return spans


class _Satz:
    """Ein Satzteil, EINMAL zerlegt (inserat4, Laufzeit): Woerter, Titelschrift,
    Vorbehalts-Phrase und — je Feld (hu, extra) — vorberechnete Stellen der
    Verneinungs-/Vorbehaltswoerter. Damit kostet jedes Stichwort-Vorkommen
    nur noch eine Binaersuche statt eines Durchlaufs durch den Satzteil
    (vorher wuchs die Laufzeit quadratisch mit der Textlaenge)."""

    def __init__(self, t: str, s: int, e: int):
        self.t, self.s, self.e = t, s, e
        self.toks = _tokens(t, s, e)
        self.starts = [tk.start for tk in self.toks]
        self.titel = _titelschrift(self.toks)
        self.phrase = bool(_STOER_PHRASE.search(t, s, e))
        self._vor_info: Dict[Any, Any] = {}
        self._nach_max: Dict[Any, int] = {}
        self._naechst: Optional[Tuple[List[int], List[int]]] = None
        self.zeitraum: Optional[bool] = None

    # --- Woerter vor / nach einer Textstelle
    def k_vor(self, a: int) -> Optional[int]:
        """Anzahl Woerter des Satzteils vor Position a — None, wenn a mitten in
        einem Wort liegt ("nicht-unfallfrei"): dann zerlegt der Aufrufer selbst."""
        k = bisect.bisect_left(self.starts, a)
        if k and self.toks[k - 1].ende > a:
            return None
        return k

    def j_nach(self, b: int) -> Optional[int]:
        j = bisect.bisect_left(self.starts, b)
        if j and self.toks[j - 1].ende > b:
            return None
        return j

    def vor(self, a: int) -> Tuple[List[_Tok], Optional[int]]:
        k = self.k_vor(a)
        if k is None:
            return _tokens(self.t, self.s, a), None
        return self.toks[:k], k

    def sv(self, vor: List[_Tok], k: Optional[int], n: int, hu: bool = False,
           extra: frozenset = frozenset()) -> bool:
        """_stoer_vor auf vor[:n] (vor aus self.vor)."""
        if n <= 0:
            return False
        if k is not None:
            return self.stoer_vor(n, hu, extra)
        return _stoer_vor(vor[:n], self.titel, hu, extra)

    def sv_pos(self, a: int, hu: bool = False, extra: frozenset = frozenset()) -> bool:
        vor, k = self.vor(a)
        return self.sv(vor, k, len(vor), hu, extra)

    def sn_pos(self, b: int, hu: bool = False, extra: frozenset = frozenset()) -> bool:
        """_stoer_nach auf die Woerter von b bis zum Satzteil-Ende."""
        j = self.j_nach(b)
        if j is None:
            return _stoer_nach(_tokens(self.t, b, self.e), hu, extra)
        return self.stoer_nach(j, hu, extra)

    # --- vorberechnet
    def _noch_mitte_ok(self, i: int) -> bool:
        """"noch" an Stelle i mit einem Folgewort, das es zur HU-Angabe macht."""
        if i + 1 >= len(self.toks):
            return False
        n = self.toks[i + 1].klein
        return n in ("bis", "gültig", "gueltig") or bool(_HU_RE.fullmatch(n)) or bool(re.match(r"\d", n))

    def _naechste(self) -> Tuple[List[int], List[int]]:
        """Je Stelle: naechstes Nomen bzw. naechstes Nomen-oder-Aussagewort rechts davon."""
        if self._naechst is None:
            n = len(self.toks)
            nomen = [n] * (n + 1)
            nom_praed = [n] * (n + 1)
            for i in range(n - 1, -1, -1):
                tk = self.toks[i]
                ist_nomen = _nomen(tk, self.titel)
                nomen[i] = i if ist_nomen else nomen[i + 1]
                nom_praed[i] = i if (ist_nomen or tk.klein in _PRAEDIKAT) else nom_praed[i + 1]
            self._naechst = (nomen, nom_praed)
        return self._naechst

    def _vor_daten(self, hu: bool, extra: frozenset):
        key = (hu, extra)
        d = self._vor_info.get(key)
        if d is not None:
            return d
        n = len(self.toks)
        immer = n            # kleinste Stelle, die "Stoerwort davor" immer ausloest
        noch_schlecht = n    # kleinste Stelle eines "noch", das nur am Ende erlaubt ist
        g_nicht: List[int] = []
        g_andere: List[int] = []
        for i, tk in enumerate(self.toks):
            st = _ist_stoer(tk.klein)
            if tk.strich and tk.strich != "-" and tk.klein != "-" and st:
                immer = min(immer, i)          # "nicht–unfallfrei"
                continue
            if not st and tk.klein not in extra:
                continue
            if hu and tk.klein == "noch":
                if not self._noch_mitte_ok(i):
                    noch_schlecht = min(noch_schlecht, i)
                continue
            if tk.klein in _NEG_ANH and not tk.klammer:
                (g_nicht if tk.klein == "nicht" else g_andere).append(i)
                continue
            immer = min(immer, i)
        d = (immer, noch_schlecht, g_nicht, g_andere)
        self._vor_info[key] = d
        return d

    def stoer_vor(self, k: int, hu: bool = False, extra: frozenset = frozenset()) -> bool:
        """Wie _stoer_vor(self.toks[:k]) — ohne Durchlauf."""
        if k <= 0:
            return False
        immer, noch_schlecht, g_nicht, g_andere = self._vor_daten(hu, extra)
        if immer < k or noch_schlecht < k - 1:
            return True
        binde = self.toks[k - 1].klein in _BINDE
        if not (g_nicht or g_andere):
            return False
        nomen, nom_praed = self._naechste()
        for liste, naechst in ((g_andere, nomen), (g_nicht, nom_praed)):
            p = bisect.bisect_left(liste, k) - 1
            if p >= 0:
                # die rechteste Verneinung vor k hat den kuerzesten Abstand: hat
                # sie kein eigenes Nomen dahinter, hat es keine weiter links
                if binde or naechst[liste[p] + 1] >= k:
                    return True
        return False

    def stoer_nach(self, j: int, hu: bool = False, extra: frozenset = frozenset()) -> bool:
        """Wie _stoer_nach(self.toks[j:]) — ohne Durchlauf."""
        key = (hu, extra)
        mx = self._nach_max.get(key)
        if mx is None:
            mx = -1
            for i, tk in enumerate(self.toks):
                if _ist_stoer(tk.klein) or tk.klein in extra:
                    if hu and tk.klein == "noch" and self._noch_mitte_ok(i):
                        continue
                    mx = i
            self._nach_max[key] = mx
        return mx >= j


class _Analyse:
    """Satzteile eines Texts und ihre Zerlegung — je Text einmal (inserat4)."""

    def __init__(self, t: str):
        self.t = t
        self.spans = _satzteile_berechnen(t)
        self._anf = [s for s, _ in self.spans]
        self._saetze: Dict[Tuple[int, int], _Satz] = {}
        self.folge: Dict[Any, bool] = {}

    def span(self, pos: int) -> Tuple[int, int]:
        i = bisect.bisect_right(self._anf, pos) - 1
        if i < 0:
            return 0, 0
        s, e = self.spans[i]
        return (s, e) if s <= pos <= e else (0, 0)

    def satz(self, s: int, e: int) -> _Satz:
        x = self._saetze.get((s, e))
        if x is None:
            x = self._saetze[(s, e)] = _Satz(self.t, s, e)
        return x


@lru_cache(maxsize=4)
def _analyse(t: str) -> _Analyse:
    return _Analyse(t)


def _satzteile(t: str) -> List[Tuple[int, int]]:
    return _analyse(t).spans


def _satzteil_von(t: str, pos: int) -> Tuple[int, int]:
    return _analyse(t).span(pos)


def _satz(t: str, s: int, e: int) -> _Satz:
    return _analyse(t).satz(s, e)


# ================================================ Urteil je Vorkommen
def _stoer_vor(vor: List[_Tok], titel: bool, hu: bool = False, extra: frozenset = frozenset()) -> bool:
    """Steht vor dem Stichwort ein Verneinungs-/Vorbehaltswort, das NICHT zu
    einem anderen Nomen gehoert? (Listenform — nur noch fuer Stellen mitten
    in einem Wort; sonst _Satz.stoer_vor.)"""
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
    sz = _satz(t, s, e)
    return sz.phrase or sz.sv_pos(a, hu, extra) or sz.sn_pos(b, hu, extra)


_NACH_JA_NEIN = re.compile(r"\s*[:?]\s*(?:leider\s+)?(nein|ja|jawohl)\b", re.I)
# inserat4: nach "<Stichwort>:" zaehlt nur ein ausdrueckliches ja / x / ✓ als "ja"
_NACH_JA_ZEICHEN = re.compile(r"\s*:\s*(?:x|✓|✔|✅|☑)(?!\w)", re.I)
_NACH_DOPPELPUNKT = re.compile(r"\s*:")
_FRAGE = re.compile(r"\s*\?")
# inserat5: leeres Kaestchen / Fragezeichen in Klammern direkt nach dem Stichwort
# ("Unfallfrei [ ]", "Unfallfrei (?)", "Unfallfrei ☐") — Klammern trennen sonst
# den Satzteil, der Rest waere leer.
_NACH_KAESTCHEN = re.compile(r"\s*(?:\[\s*[?_]*\s*\]|\(\s*\?+\s*\)|\(\s*\)|☐|□|🚫|👎|⛔)")

# inserat4: Zeitraum-Vorbehalt ("Seit 2019 unfallfrei", "Bis letztes Jahr unfallfrei").
# "seit Erstzulassung/EZ/neu" ist kein Vorbehalt, "TÜV (noch) bis" auch nicht.
_ZEIT_WORT_RX = re.compile(
    r"\bseit\b(?!\s+(?:der\s+|dem\s+)?(?:erstzulassung|erstzul\w*|ez|werk|auslieferung|neukauf|neu|neuwagen)\b)"
    r"|\bbis\b", re.I)
_BIS_ERLAUBT_DAVOR = re.compile(r"(?:hu|t(?:ü|ue|u)v|au|hu/au|t(?:ü|ue|u)v/au|noch|g(?:ü|ue|u)ltig)[:.]?")


def _zeitraum(sz: "_Satz") -> bool:
    """Zeitraum-/Besitzvorbehalt im Satzteil — je Satzteil einmal berechnet."""
    if sz.zeitraum is None:
        sz.zeitraum = False
        for m in _ZEIT_WORT_RX.finditer(sz.t, sz.s, sz.e):
            if m.group(0).lower() == "bis":
                k = sz.k_vor(m.start())
                if k and _BIS_ERLAUBT_DAVOR.fullmatch(sz.toks[k - 1].klein):
                    continue                   # "TÜV bis 05/2027", "TÜV noch bis ..."
            sz.zeitraum = True
            break
    return sz.zeitraum


# Stoerwoerter, die im FOLGENDEN Satzteil nichts ueber das Stichwort sagen
# ("TÜV neu, sonst top", "fahrbereit, noch angemeldet")
_FOLGE_NEUTRAL = frozenset({"sonst", "ansonsten", "noch"})


def _folge_vorbehalt(t: str, s: int, e: int, extra: frozenset = frozenset(), zeit: bool = False) -> bool:
    """inserat4 (Pruefung Runde 3, "Unfallfrei, laut Vorbesitzer"): Ein Satzteil
    direkt danach OHNE eigenes Stichwort, der nur einen Vorbehalt oder eine
    Verneinung traegt (", soweit bekannt", ", vermutlich", ", leider nicht",
    ", so der Vorbesitzer", "(seit Kauf)"), gehoert zum Stichwort davor."""
    an = _analyse(t)
    key = (s, e, extra, zeit)
    if key in an.folge:
        return an.folge[key]
    erg = False
    i = bisect.bisect_right(an._anf, s) - 1
    for s2, e2 in an.spans[i + 1:i + 4]:
        if not t[s2:e2].strip():
            continue                            # leerer Satzteil zwischen ")" und ","
        sz = an.satz(s2, e2)
        if _IRGENDEIN_STICHWORT.search(t, s2, e2):
            break
        toks = sz.toks
        treffer = []
        for j, tk in enumerate(toks):
            if not (_ist_stoer(tk.klein) or tk.klein in extra) or tk.klein in _FOLGE_NEUTRAL:
                continue
            if tk.klein in _NEG_ANH and j + 1 < len(toks) and not tk.klammer \
                    and not (_ist_stoer(toks[j + 1].klein) or toks[j + 1].klein in _KEIN_NOMEN):
                continue                        # "keine Tiere", "nicht verhandelbar"
            if tk.klein == "nicht" and j > 0 and toks[j - 1].klein in _VERB_VOR_NICHT:
                continue                        # "Klima geht nicht"
            treffer.append(j)
        vorbehalt = sz.phrase or (zeit and _zeitraum(sz))
        nomen, _ = sz._naechste()
        hat_nomen = nomen[0] < len(toks)
        erg = bool(vorbehalt or (treffer and (not hat_nomen or treffer[0] == 0)))
        break
    an.folge[key] = erg
    return erg


def _urteil_adj(t: str, a: int, b: int, s: int, e: int, extra: frozenset = frozenset(),
                zeit: bool = False) -> str:
    """Urteil fuer ein Zusicherungs-Stichwort (Adjektiv: unfallfrei,
    fahrbereit, lueckenlos, EU-Import) an [a, b) im Satzteil [s, e).
    extra: weitere Vorbehaltswoerter nur fuer dieses Feld; zeit: Zeitraum-
    Vorbehalt ("seit 2019", "bis letztes Jahr") zaehlt."""
    sz = _satz(t, s, e)
    vor, k = sz.vor(a)
    n = len(vor)

    def sv(bis: int) -> bool:
        return sz.sv(vor, k, bis, extra=extra)

    def sn(pos: int) -> bool:
        return sz.sn_pos(pos, extra=extra)

    phrase = sz.phrase or (zeit and _zeitraum(sz))
    # "<Stichwort>: nein" / "<Stichwort>? (leider) nein" / "<Stichwort>: ja"
    m = _NACH_JA_NEIN.match(t, b, e) or _NACH_JA_ZEICHEN.match(t, b, e)
    if m:
        if phrase or sv(n) or sn(m.end()):
            return "unklar"
        return "nein" if m.lastindex and m.group(1).lower() == "nein" else "ja"
    if _NACH_DOPPELPUNKT.match(t, b, e):
        return "unklar"                        # "Unfallfrei: nö", "Unfallfrei: -", "Unfallfrei: k.A."
    if _FRAGE.match(t, b, e):
        return "unklar"                        # "Unfallfrei?" — offene Frage
    if _NACH_KAESTCHEN.match(t, b):
        return "unklar"                        # "Unfallfrei [ ]", "Unfallfrei (?)" — ueber die Satzteilgrenze
    # "nicht-unfallfrei" (Bindestrich); "nicht–unfallfrei" bleibt unklar
    if vor and vor[-1].strich == "-" and vor[-1].klein == "nicht" and not vor[-1].klammer:
        if phrase or sv(n - 1) or sn(b):
            return "unklar"
        return "nein"
    # "nicht nur unfallfrei" (Positivmuster)
    if n >= 2 and vor[-2].klein == "nicht" and vor[-1].klein == "nur" \
            and not (vor[-2].strich or vor[-1].strich or vor[-2].klammer):
        if phrase or sv(n - 2) or sn(b):
            return "unklar"
        return "ja"
    # Negativmuster: nicht|nie|keinesfalls|keineswegs (+ mehr/ganz/zu 100 %) direkt davor
    i = n
    while i > 0 and vor[i - 1].klein in _FUELL and not vor[i - 1].strich and not vor[i - 1].klammer:
        i -= 1
    if i > 0:
        tk = vor[i - 1]
        if tk.klein in _NEG_MUSTER and not tk.strich and not tk.klammer:
            if tk.klein == "nicht" and i >= 2 and vor[i - 2].klein in _VERB_VOR_NICHT:
                return "unklar"                # "Klima geht nicht fahrbereit"
            if phrase or sv(i - 1) or sn(b):
                return "unklar"
            return "nein"
    if phrase or sv(n) or sn(b):
        return "unklar"
    return "ja"


def _stellen_adj(t: str, rx: "re.Pattern", extra: frozenset = frozenset(),
                 zeit: bool = False, folge_extra: Optional[frozenset] = None) -> List[Tuple["re.Match", str]]:
    an = _analyse(t)
    folge_extra = extra if folge_extra is None else folge_extra
    out = []
    for m in rx.finditer(t):
        s, e = an.span(m.start())
        u = _urteil_adj(t, m.start(), m.end(), s, e, extra, zeit)
        if u != "unklar" and _folge_vorbehalt(t, s, e, folge_extra, zeit):
            u = "unklar"                        # "Unfallfrei, laut Vorbesitzer"
        out.append((m, u))
    return out


# Positivmuster fuer Nomen: "kein/keine/keinen/keinerlei/ohne [Beiwort] <Nomen>"
_POS_NEG = frozenset({"kein", "keine", "keinen", "keinerlei", "ohne"})
# inserat4: "keine größeren/nennenswerten Unfälle" deutet auf kleinere -> kein Positivmuster
_POS_BEIWORT = re.compile(r"(?:jeglich|irgendwelch|einzig|bekannt)\w*")


def _positivmuster(vor: List[_Tok], neg: frozenset = _POS_NEG) -> int:
    """Anzahl der Woerter des Positivmusters direkt vor dem Nomen (0 = keins)."""
    if len(vor) >= 2 and _POS_BEIWORT.fullmatch(vor[-1].klein) and vor[-2].klein in neg:
        n = 2
    elif vor and vor[-1].klein in neg:
        n = 1
    else:
        return 0
    return 0 if any(tk.klammer or tk.strich for tk in vor[-n:]) else n


def _nomen_verneint_sauber(t: str, m: "re.Match", neg: frozenset = _POS_NEG) -> bool:
    """Ist das Nomen an m sauber verneint ("kein Hagelschaden") — ohne
    weiteren Vorbehalt im Satzteil?"""
    s, e = _satzteil_von(t, m.start())
    sz = _satz(t, s, e)
    vor, k = sz.vor(m.start())
    n = _positivmuster(vor, neg)
    if not n:
        return False
    return not (sz.phrase or sz.sv(vor, k, len(vor) - n) or sz.sn_pos(m.end()))


# ------------------------------------------------ einzelne Regeln
_SCHL_EINS = re.compile(
    r"nur\s+(?:ein|einen|1)\s+(?:funk|fahrzeug)?schl(?:ü|ue|u)e?ssel|kein(?:en)?\s+zweitschl(?:ü|ue|u)e?ssel"
    r"|ohne\s+zweitschl(?:ü|ue|u)e?ssel|zweitschl(?:ü|ue|u)e?ssel\s+(?:fehlt|nicht\s+vorhanden|ist\s+nicht\s+dabei)",
    re.I)
_SCHL_ZWEI = re.compile(
    r"zweitschl(?:ü|ue|u)e?ssel\s+(?:vorhanden|dabei|ist\s+dabei|inklusive|inkl\.?)"
    r"|(?:mit|inkl\.?|inklusive)\s+zweitschl(?:ü|ue|u)e?ssel|beide\s+schl(?:ü|ue|u)e?ssel", re.I)
_SCHL_ZAHL = re.compile(r"\b(ein|einen|zwei|drei|vier|[1-4])\s*(?:x\s*)?(?:original|originale|originalen)?\s*"
                        r"(?:funk|fahrzeug)?schl(?:ü|ue|u)e?ssel", re.I)
# inserat5: Vorbehalte nur bei Schluesseln/Reifen ("Ein Schlüssel fehlt", "Winterreifen gesucht")
_SACH_EXTRA = frozenset({"fehlt", "fehlen", "verloren", "gesucht", "suche", "suchen", "defekt", "kaputt",
                         "separat", "extra", "aufpreis", "wunsch", "optional", "nachmachen", "nachgemacht"})


def _sach_urteil(t: str, m: "re.Match", innen_ok: bool = False) -> str:
    """inserat5: Urteil fuer eine Sachangabe (Schluessel, Bereifung) — "ja"
    oder "unklar". Unklar bei Frage ("2 Schlüssel? Nein, nur einer",
    "Winterreifen dabei?"), Verneinung/Vorbehalt im Satzteil ("Keine
    Winterreifen dabei", "Winterreifen gesucht") oder im Folgesatzteil
    (", leider nicht"). innen_ok: die Verneinung steckt im Muster selbst
    ("kein Zweitschlüssel") und zaehlt dort nicht."""
    s, e = _satzteil_von(t, m.start())
    if _FRAGE.match(t, m.end(), e) or _NACH_KAESTCHEN.match(t, m.end()):
        return "unklar"
    if _stoer_rest(t, s, e, m.start(), m.end(), extra=_SACH_EXTRA):
        return "unklar"
    if not innen_ok and any(_ist_stoer(tk.klein) for tk in _tokens(t, m.start(), m.end())):
        return "unklar"
    if _folge_vorbehalt(t, s, e, _SACH_EXTRA):
        return "unklar"
    return "ja"


def schluessel(v: dict, text: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Liefert (eintrag, hinweis). inserat5: Frage/Verneinung beachten —
    "2 Schlüssel? Nein, nur einer" ergab vorher 2."""
    strukturiert = v.get("keys_count")
    if strukturiert not in (None, ""):
        z = PV.zahl(strukturiert)
        if z is not None and 1 <= z <= 9:
            return _eintrag(str(int(z)), "listing_field", f"Schlüssel: {int(z)}"), None
    t = text
    # Reihenfolge wie bisher: "nur ein / kein Zweitschluessel" -> 1, "mit Zweitschluessel" -> 2, Zahl
    for rx, wert, innen_ok in ((_SCHL_EINS, "1", True), (_SCHL_ZWEI, "2", False), (_SCHL_ZAHL, None, False)):
        treffer = [m for m in rx.finditer(t) if wert or m.group(1).lower() in _ZAHLWORT]
        if not treffer:
            continue
        unklar = [m for m in treffer if _sach_urteil(t, m, innen_ok) != "ja"]
        if unklar:
            return None, (f"Schlüsselanzahl im Inserat nicht eindeutig („{_fund(text, unklar[0])}“) — "
                          "bitte beim Verkäufer erfragen und selbst eintragen.")
        m = treffer[0]
        return _eintrag(wert or str(_ZAHLWORT[m.group(1).lower()]), "listing_description", _fund(text, m)), None
    return None, None


# ------------------------------------------------ HU
# inserat4: "TÜV-relevante Mängel", "HU/AU-Bericht" — das HU-Wort vor einem
# Bindestrich ist Teil eines anderen Worts, kein HU-Stichwort.
_HU_WORT = r"(?:hu|t(?:ü|ue|u)v|hauptuntersuchung)\b(?!-|\s*/\s*au-)"
_HU_RE = re.compile(r"\b" + _HU_WORT, re.I)
_AU = r"(?:\s*/\s*au|\s*&\s*au|\s+und\s+au)?"
_HU_KEIN = re.compile(r"\b(?:kein|keine|keinen|ohne)\s+(?:g(?:ü|ue|u)ltige[nrs]?\s+|aktuelle[nrs]?\s+)?"
                      + _HU_WORT + _AU + r"(?!-)", re.I)
_HU_ABGEL_VOR = re.compile(r"\b(?:abgelaufene[nrs]?|(?:ü|ue|u)berzogene[nrs]?)\s+" + _HU_WORT + _AU, re.I)
_HU_NEIN_NACH = re.compile(r"\b" + _HU_WORT + _AU + r"\s*[:?]\s*(?:leider\s+)?nein\b", re.I)
_ABGEL = re.compile(r"\b(?:abgelaufen|abgel|(?:ü|ue|u)berzogen|(?:ü|ue|u)berf(?:ä|ae|a)llig|f(?:ä|ae|a)llig)\b", re.I)
_HU_DATUM = re.compile(
    r"\b" + _HU_WORT + _AU + r"\s*(?:neu\s*)?(?:ist\s+)?(?:noch\s+)?(?:g(?:ü|ue|u)ltig\s+)?(?:bis\s*)?"
    r"(?::\s*)?(?:zum\s+)?(?:(\d{1,2})\s*\.\s*)?(\d{1,2})\s*[./-]\s*(\d{4}|\d{2})(?![./-]?\d)", re.I)
_HU_NEU = re.compile(r"\b" + _HU_WORT + _AU + r"\s*(?:ist\s+)?(?:ganz\s+)?neu\b"
                     r"|\b(?:neue[rs]?|frische[rs]?)\s+" + _HU_WORT, re.I)
# Nur bei der HU: "TUEV neu machen", "HU muss neu" -> kein "Ja"
_HU_STOER = frozenset({"machen", "werden", "wird", "muss", "müssen", "muessen", "nötig", "noetig", "notwendig",
                       "erforderlich", "ansteht", "anstehend",
                       # inserat4: "TÜV neu" als Angebot oder Plan ("auf Wunsch", "gegen Aufpreis")
                       "wunsch", "aufpreis", "möglich", "moeglich", "moglich", "absprache", "kommt", "kommen",
                       "geplant", "beantragt", "angefragt", "anfrage", "optional", "gegen", "kann", "können",
                       "koennen", "würde", "wuerde", "machbar", "übergabe", "uebergabe"})
# ... und im Satzteil danach ("TÜV neu, auf Wunsch")
_HU_FOLGE = frozenset({"wunsch", "aufpreis", "absprache", "geplant", "beantragt", "möglich", "moeglich"})
# inserat5 (Pruefung Runde 4): "TÜV neu" als Angebot, Bedingung oder altes
# Pruefdatum — im selben Satzteil ("TÜV neu bei Abholung", "TÜV neu vor 2
# Jahren", "TÜV neu nur bei Übernahme der Kosten", "TÜV neu mit Mängelbericht",
# "TÜV neu wenn gewünscht") ...
_HU_NEU_VORBEHALT = frozenset({
    "wenn", "falls", "sofern", "sobald", "gewünscht", "gewuenscht", "erwünscht", "erwuenscht", "anfrage",
    "abholung", "übernahme", "uebernahme", "kosten", "käufer", "kaeufer", "käuferseite", "mängelbericht",
    "maengelbericht", "prüfbericht", "pruefbericht", "vor", "letzte", "letzten", "letztes", "vorjahr",
    "dafür", "dafuer", "zzgl", "zuzüglich", "zuzueglich", "extra", "aufpreis", "wunsch", "absprache"})
# ... und im Satzteil danach, auch MIT Praeposition davor und Nomen dahinter
# (", auf Wunsch", ", gegen Aufpreis", ", nach Absprache", ", wenn gewünscht",
# ", dafür Aufpreis", "(letztes Jahr)"). Anders als _folge_vorbehalt zaehlt
# das Wort auch mitten im Folgesatzteil — solange dort kein ANDERES Nomen
# steht (", Probefahrt nach Absprache", ", Abholung gegen Aufpreis" betreffen
# nicht die HU und lassen sie eindeutig).
_HU_FOLGE_ANGEBOT = frozenset({
    "wunsch", "aufpreis", "absprache", "geplant", "beantragt", "wenn", "falls", "sofern", "sobald",
    "gewünscht", "gewuenscht", "erwünscht", "erwuenscht", "anfrage", "angefragt", "übernahme", "uebernahme",
    "mängelbericht", "maengelbericht", "prüfbericht", "pruefbericht", "dafür", "dafuer", "zzgl", "zuzüglich",
    "zuzueglich", "gegen", "optional", "machbar"})
# Nomen, die im Folgesatzteil zum HU-Angebot gehoeren duerfen ("Übernahme der Kosten", "sofern Käufer zahlt")
_HU_FOLGE_NOMEN_OK = _HU_FOLGE_ANGEBOT | frozenset({"kosten", "käufer", "kaeufer", "käufers", "kaeufers",
                                                    "kunden", "kunde", "euro", "eur", "jahr", "jahren", "jahre"})
# Zeitangaben nur in einem KURZEN Folgesatzteil ("(letztes Jahr)", ", vor 2 Jahren")
# — "TÜV neu, letzte Inspektion bei 90.000 km" bleibt eindeutig.
_HU_FOLGE_ZEIT = frozenset({"letzte", "letzten", "letztes", "vorjahr", "vor"})


def _hu_folge_angebot(t: str, s: int, e: int) -> bool:
    """inserat5: Folgesatzteil ohne eigenes Stichwort mit einem Angebots-,
    Bedingungs- oder Zeitwort (und ohne fremdes Nomen) -> die HU-Angabe
    davor ist unklar."""
    an = _analyse(t)
    i = bisect.bisect_right(an._anf, s) - 1
    for s2, e2 in an.spans[i + 1:i + 4]:
        if not t[s2:e2].strip():
            continue
        if _IRGENDEIN_STICHWORT.search(t, s2, e2):
            return False
        sz = an.satz(s2, e2)
        toks = [tk for tk in sz.toks if tk.klein != "-"]
        if len(toks) <= 3 and any(tk.klein in _HU_FOLGE_ZEIT for tk in toks):
            return True
        if not any(tk.klein in _HU_FOLGE_ANGEBOT for tk in toks):
            return False
        return not any(_nomen(tk, sz.titel) and tk.klein not in _HU_FOLGE_NOMEN_OK for tk in toks)
    return False
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
# inserat4: "TÜV im Oktober fällig" — "fällig" mit Zeitangabe heisst: gilt noch
_FAELLIG_ZUKUNFT = frozenset({"im", "ab", "in", "zum", "bis", "ende", "anfang", "mitte", "kommenden", "kommender",
                              "kommendes", "kommend", "nächsten", "nächste", "nächster", "nächstes", "naechsten",
                              "naechste", "naechster", "naechstes", "diesen", "dieses", "diesem", "jahresende",
                              "monatsende"})
# irgendein Datum im Satzteil (auch NACH "abgelaufen/fällig")
_DATUM_IRGENDWO = re.compile(r"(?<![\d.,/])(?:(\d{1,2})\s*\.\s*)?(\d{1,2})\s*[./-]\s*(\d{4}|\d{2})(?![./-]?\d)")
# inserat5: "TÜV neu" mit Angebots-/Bedingungs-/Zeitwort im selben Satzteil
_HU_NEU_EXTRA = _HU_STOER | _HU_NEU_VORBEHALT
_HU_NEU_EXTRA_DATUM = _HU_PRUEFDATUM | _HU_NEU_VORBEHALT
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
        sz = _satz(t, s, e)
        titel = sz.titel
        daten = [(m, _hu_mj(m)) for m in _HU_DATUM.finditer(t, s, e)]
        daten = [(m, mj) for m, mj in daten if mj]
        alle_daten = [mj for mj in (_hu_mj(m) for m in _DATUM_IRGENDWO.finditer(t, s, e)) if mj]
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
                vor, _ = sz.vor(m.start())
                j = len(vor)
                while j > 0 and vor[j - 1].klein in _HU_KOPULA:
                    j -= 1
                davor = vor[j - 1] if j > 0 else None
                if davor is not None and _fremdes_nomen(davor, titel):
                    continue                   # "Bremsbelaege abgelaufen" — nur das Datum zaehlt
                if davor is not None and davor.klein in _NEG_MUSTER:
                    unklar_m = m               # "noch nicht abgelaufen"
                    break
                if re.fullmatch(r"f(?:ä|ae|a)llig", m.group(0), re.I) and any(
                        tk.klein in _FAELLIG_ZUKUNFT or _MONAT.fullmatch(tk.klein.rstrip("."))
                        for tk in sz.toks) or re.fullmatch(r"f(?:ä|ae|a)llig", m.group(0), re.I) and any(
                        not _abgelaufen(mj, heute) for mj in alle_daten):
                    unklar_m = m               # "TÜV im Oktober fällig", "HU fällig 10/2026"
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
            stelle = _tokens(t, a, b)
            if _stoer_rest(t, s, e, a, b, hu=True, extra=_HU_STOER) or any(
                    _ist_stoer(tk.klein) and not _hu_noch_ok(stelle, i)
                    for i, tk in enumerate(stelle)
                    if not (nein_m.start() <= tk.start < nein_m.end())):
                out.append(_HuBefund("unklar", "", nein_m))
            elif any(not _abgelaufen(mj, heute) for mj in alle_daten):
                # "TUEV 10/2027 abgelaufen", "TÜV abgelaufen 10/2027" — Datum gueltig, Text abgelaufen
                out.append(_HuBefund("unklar", "", nein_m))
            elif _folge_vorbehalt(t, s, e, _HU_FOLGE):
                out.append(_HuBefund("unklar", "", nein_m))
            else:
                out.append(_HuBefund("nein", "", nein_m))
            continue
        # --- Ja-Kandidaten: Datum / "neu"
        folge = _folge_vorbehalt(t, s, e, _HU_FOLGE) or _hu_folge_angebot(t, s, e)
        for m, mj in daten:
            frage = _FRAGE.match(t, m.end(), e)
            if frage or folge or _stoer_rest(t, s, e, m.start(), m.end(), hu=True, extra=_HU_PRUEFDATUM):
                out.append(_HuBefund("unklar", mj, m))
            elif _abgelaufen(mj, heute):
                out.append(_HuBefund("vergangen", mj, m))
            else:
                out.append(_HuBefund("ja", mj, m))
        # inserat4: "TÜV neu gemacht 09/2024", "TÜV neu seit 08/2024" — mit einem Datum
        # im Satzteil sind gemacht/seit/vom ... ein Pruefdatum, kein "neu = gueltig"
        neu_extra = _HU_NEU_EXTRA_DATUM if alle_daten else _HU_NEU_EXTRA
        for m in _HU_NEU.finditer(t, s, e):
            frage = _FRAGE.match(t, m.end(), e)
            if frage or folge or _stoer_rest(t, s, e, m.start(), m.end(), hu=True, extra=neu_extra):
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
_SCHECK_VORBEHALT = frozenset({"bis", "damals", "anfangs", "früher", "frueher", "anfänglich", "anfaenglich",
                               # inserat4: "ab 2019 freie Werkstatt", "die letzten 3 Jahre",
                               # "letzte Inspektion fehlt", "Scheckheft leider verloren"
                               "ab", "letzte", "letzten", "letztes", "fehlt", "fehlen", "fehlend", "fehlende",
                               "fehlenden", "verloren", "verlegt"})
# ... und im Satzteil danach (ohne "letzte": "Scheckheft lückenlos, letzte Inspektion 2024")
_SCHECK_FOLGE = _SCHECK_VORBEHALT - {"letzte", "letzten", "letztes"}
_SCHECK_NUR = re.compile(r"\bscheckheft\s+gepflegt\w*|\b" + _SCHECK, re.I)


def _scheck_folge(t: str, m: "re.Match") -> bool:
    an = _analyse(t)
    s, _ = an.span(m.start())
    i = bisect.bisect_right(an._anf, s) - 1
    for s2, e2 in an.spans[i + 1:i + 4]:
        if not t[s2:e2].strip():
            continue
        sz = an.satz(s2, e2)
        return any(tk.klein in _SCHECK_FOLGE for tk in sz.toks) or _zeitraum(sz)
    return False


def scheckheft(text: str) -> Dict[str, Any]:
    """Liefert {"wert": ..} oder {"hinweis": ..} oder {}."""
    t = text
    voll = _stellen_adj(t, _SCHECK_VOLL, _SCHECK_VORBEHALT, zeit=True, folge_extra=_SCHECK_FOLGE)
    # inserat4: Einschraenkung im Folgesatzteil zaehlt auch MIT eigenem Stichwort
    # ("Lückenlos scheckheftgepflegt, Scheckheft leider verloren", "(bis 2019)")
    voll = [(m, "unklar" if u == "ja" and _scheck_folge(t, m) else u) for m, u in voll]
    voll_ja = [m for m, u in voll if u == "ja"]
    voll_nicht = [m for m, u in voll if u != "ja"]
    kein_sauber, kein_unklar = [], []
    for m in _SCHECK_KEIN.finditer(t):
        s, e = _satzteil_von(t, m.start())
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
# inserat4: Unfall-Komposita ("Auffahrunfall", "Wildunfall", "Unfallreparatur") — Gegenstimme zu "unfallfrei"
_UNFALL_KOMPOSITUM = re.compile(r"\b\w*unf(?:ä|ae|a)ll\w*", re.I)
_UNFALL_KLEINER = re.compile(r"\b(?:kein\w*|ohne)\s+(?:nennenswert\w*|gr(?:ö|oe|o)(?:ß|ss)er\w*|schwer\w*"
                             r"|gravierend\w*|erheblich\w*)\s+unf(?:ä|ae|a)ll\w*", re.I)
_REPARIERT = re.compile(r"\brepariert|\brepaired|\binstand\s*gesetzt")


_UNFALL_EXTRA: frozenset = frozenset()


def _urteil_unfall_nomen(t: str, m: "re.Match") -> str:
    """Urteil fuer das FELD "unfallfrei" aus einem Unfall-Nomen."""
    s, e = _satzteil_von(t, m.start())
    sz = _satz(t, s, e)
    vor, k = sz.vor(m.start())
    n = _positivmuster(vor)
    if n:
        if sz.phrase or _zeitraum(sz) or sz.sv(vor, k, len(vor) - n, extra=_UNFALL_EXTRA) \
                or sz.sn_pos(m.end(), extra=_UNFALL_EXTRA) or _folge_vorbehalt(t, s, e, _UNFALL_EXTRA, True):
            return "unklar"
        return "ja"                            # "kein Unfall", "keinerlei Unfallschaeden"
    if _UNFALL_NEGATIV.fullmatch(m.group(0)):
        # inserat4: "Unfallschaden? Nö", "Unfallschaden: -" — nach "?"/":" nur "ja" ist eine Aussage
        if (_FRAGE.match(t, m.end(), e) or _NACH_DOPPELPUNKT.match(t, m.end(), e)) \
                and not re.match(r"\s*[:?]\s*(?:ja|jawohl|x|✓|✔|✅)(?!\w)", t[m.end():e], re.I):
            return "unklar"
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
    urteile = _stellen_adj(t, _UNFALLFREI, _UNFALL_EXTRA, zeit=True)
    urteile += [(m, _urteil_unfall_nomen(t, m)) for m in _UNFALL.finditer(t)]
    urteile.sort(key=lambda x: x[0].start())
    anderer = [m for m in _ANDERER_SCHADEN.finditer(t) if not _nomen_verneint_sauber(t, m, _NEG_SCHADEN)]
    anderer += [m for m in _UNFALL_KOMPOSITUM.finditer(t)
                if not (_UNFALLFREI.fullmatch(m.group(0)) or _UNFALL.fullmatch(m.group(0))
                        or re.match(r"[-\s]?frei", t[m.end():m.end() + 6], re.I))
                and not _nomen_verneint_sauber(t, m, _NEG_SCHADEN)]
    anderer.sort(key=lambda x: x.start())
    text_wert, fund = _zusammen(urteile, anderer)

    portal, portal_text = _portal_unfall(v)
    bitte = "„Unfallfrei“ bitte beim Verkäufer erfragen und selbst wählen."

    if text_wert == "unklar":
        klein = _UNFALL_KLEINER.search(t)
        if klein:
            return None, (f"Inserat: „{_fund(text, klein)}“ — deutet auf kleinere Unfälle hin, also nicht "
                          f"sicher unfallfrei. {bitte}")
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
    r"\b(?:motor|getriebe|kupplungs|zahnriemen)schaden\b"
    r"|\b(?:motor|getriebe|kupplung|turbo|zylinderkopf|lenkung|bremse|bremsen|zahnriemen|steuerkette)"
    r"\s+(?:ist\s+|sind\s+)?(?:defekt|kaputt"
    # inserat5 (Pruefung Runde 4): "Zahnriemen gerissen", "Getriebe hinüber", "Bremsen fest"
    r"|gerissen|hin(?:ü|ue|u)ber|fest(?:gerostet|gefressen)?\b)"
    r"|\bnotlauf\w*"
    r"|\bspringt\s+nicht\s+(?:mehr\s+)?an|\bl(?:ä|ae|a)uft\s+nicht\b|\babschlepp\w*|\bbastler\w*"
    r"|\bnur\s+(?:f(?:ü|ue|u)r\s+|als\s+)?(?:export|teilespender|trailer)", re.I)


# inserat4: fahrbereit in der Vergangenheit oder unter Bedingung ("war beim Abstellen
# fahrbereit", "Wäre fahrbereit wenn ...", "Nach Batteriewechsel fahrbereit")
_FAHR_EXTRA = frozenset({"war", "waren", "wäre", "waere", "wären", "waeren", "gewesen", "wenn", "falls", "sobald",
                         "nach", "zuletzt", "damals", "vorher", "früher", "frueher"})


def fahrbereit(v: dict, text: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Liefert (eintrag, hinweis)."""
    t = text
    urteile = _stellen_adj(t, _FAHRBEREIT, _FAHR_EXTRA, zeit=True)
    # "fahruntuechtig" ohne Vorbehalt = ausdrueckliches Nein; verneint/eingeschraenkt = unklar
    urteile += [(m, "nein" if u == "ja" else "unklar") for m, u in _stellen_adj(t, _FAHR_NEIN)]
    urteile.sort(key=lambda x: x[0].start())
    anderer = [m for m in _FAHR_ANDERS.finditer(t) if not _nomen_verneint_sauber(t, m, _NEG_SCHADEN)]
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


# Irgendein Zusicherungs-Stichwort (fuer _folge_vorbehalt: ein Folgesatzteil MIT
# eigenem Stichwort gehoert nicht zum vorigen)
_IRGENDEIN_STICHWORT = re.compile("|".join(
    rx.pattern for rx in (_UNFALLFREI, _UNFALL, _UNFALL_KOMPOSITUM, _FAHRBEREIT, _FAHR_NEIN, _HU_RE, _EU_IMPORT))
    + r"|\b" + _SCHECK, re.I)


# inserat5 (Pruefung Runde 4): "4-fach"/"8-fach" nur mit Reifen-Bezug ("4-fach
# Airbags", "8-fach verstellbare Sitze" sind keine Bereifung).
_REIFEN_BEZUG = r"[\s-]*(?:bereift\w*|bereifung\w*|\w*reifen\w*|\w*r(?:ä|ae|a)der\w*|auf\s+alu\w*)"
_REIFEN_8 = re.compile(
    r"\b8[- ]?fach(?=" + _REIFEN_BEZUG + r")|\b(?:bereifung|reifen)\s*:?\s*8[- ]?fach\b"
    r"|\b2\s*(?:satz|s(?:ä|ae|a)tze)\s+(?:\w*reifen|\w*r(?:ä|ae|a)der)|\bzweite[rn]?\s+(?:rad|reifen)satz\b"
    r"|\bzweitsatz\b|\bsommer-?\s*(?:und|\+|&)\s*winter(?:reifen|r(?:ä|ae|a)der|kompletträder)"
    r"|\bwinter(?:reifen|r(?:ä|ae|a)der|kompletträder)\s+(?:sind\s+)?(?:mit\s+)?(?:dabei|inklusive|inkl\.?|vorhanden)",
    re.I)
_REIFEN_4 = re.compile(r"\b4[- ]?fach(?=" + _REIFEN_BEZUG + r")|\b(?:bereifung|reifen)\s*:?\s*4[- ]?fach\b", re.I)


def bereifung(text: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Liefert (eintrag, hinweis). inserat5: Verneinung und Frage beachten
    ("Keine Winterreifen dabei", "Winterreifen dabei? Nein", "Sommer- und
    Winterreifen gesucht" ergaben vorher 8-fach)."""
    t = text
    stellen = [(m, "8-fach") for m in _REIFEN_8.finditer(t)] + [(m, "4-fach") for m in _REIFEN_4.finditer(t)]
    if not stellen:
        return None, None
    stellen.sort(key=lambda x: x[0].start())
    bitte = "„Bereifung“ bitte beim Verkäufer erfragen und selbst wählen."
    unklar = [m for m, _ in stellen if _sach_urteil(t, m) != "ja"]
    if unklar:
        return None, f"Inserat zur Bereifung nicht eindeutig („{_fund(text, unklar[0])}“) — {bitte}"
    werte = {w for _, w in stellen}
    if len(werte) > 1:
        return None, (f"Inserat widersprüchlich zur Bereifung („{_fund(text, stellen[0][0])}“ / "
                      f"„{_fund(text, stellen[-1][0])}“) — {bitte}")
    m, wert = stellen[0]
    return _eintrag(wert, "listing_description", _fund(text, m)), None


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
        s, s_hinweis = schluessel(v, text)
        if s:
            felder["schluessel_anzahl"] = s
        if s_hinweis:
            hinweise.append(s_hinweis)

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
        b, b_hinweis = bereifung(text)
        if b:
            felder["tires"] = b
        if b_hinweis:
            hinweise.append(b_hinweis)

    for regel in (_schluessel, _hu, _scheckheft, _unfall, _fahrbereit, _eu, _reifen):
        try:
            regel()
        except Exception:  # noqa: BLE001 — Vorschlaege sind Beiwerk; inserat4: aber nie stumm
            log.exception("Inserat-Vorschlag: Regel %s fehlgeschlagen", regel.__name__.lstrip("_"))
    return {"felder": felder, "hinweise": hinweise,
            "bekannte_maengel": [str(m)[:200] for m in (v.get("known_defects") or []) if str(m or "").strip()][:20]}
