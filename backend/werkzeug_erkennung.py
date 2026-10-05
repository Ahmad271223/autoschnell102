# -*- coding: utf-8 -*-
"""Marke/Modell-Erkennung fuer das Windows-Programm (AutoPointer-Vergleich).

Wunsch Ahmad 03.10.2026: "Die Erkennung auf den Server verlegen — das Programm liest nur noch den Text vom
Bildschirm und schickt ihn ab, es enthaelt kaum noch Wissen." Bis Programmversion 1.3.5 lief diese Logik im
Programm (autopointer-vergleich/src/Katalog.cs + Zuordnung.cs); hier ist sie 1:1 uebertragen — dieselben
Kataloge (mobile_makes_models.json, autoscout_makes.json), dieselben Regeln, dieselbe Reihenfolge. Die
Gleichheit pruefte am 03.10.2026 ein Abgleich beider Fassungen ueber tausende Faelle (tests/test_werkzeug_erkennung).

Was sie kann (jeweils mit Befund):
  * Marke/Modell trennen, laengste bekannte Marke am Anfang, Lesefehler im Markennamen ("Bentlev")
  * Modell exakt / per Praefix wie mobile_service._resolve_model, AutoScout wie autoscout_service._find_model
  * unscharfer Abgleich (Levenshtein) und Lesefehler i/l/1, o/0 ("Hyundai ilO" -> i10)
  * Platzhalter ("Andere", "Weitere VW") und Kategorien ("VW-Busse"): Modell aus der Ueberschrift, auch
    verstreut oder zusammengezogen ("T5 Bulli multivan" -> T5 Multivan, "XC 60" -> XC60)
  * fehlt die Marke ("Andere"), Marke UND Modell aus der Ueberschrift ("Ford Mondeo Turnier ..." -> Ford Mondeo)
"""
from __future__ import annotations

import functools
import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_ORDNER = Path(__file__).resolve().parent

# mobile_service._MAKE_ALIASES
MOBILE_ALIASE = {"vw": "volkswagen", "mercedes": "mercedesbenz", "rangerover": "landrover",
                 "dsautomobiles": "ds", "ds": "ds"}
# autoscout_service._MAKE_ALIASES
AUTOSCOUT_ALIASE = {"vw": "Volkswagen", "merc": "Mercedes-Benz", "mercedesbenz": "Mercedes-Benz",
                    "mercedes": "Mercedes-Benz", "rangerover": "Land Rover", "rolls": "Rolls-Royce",
                    "rollsroyce": "Rolls-Royce", "astonmartin": "Aston Martin", "alfaromeo": "Alfa Romeo",
                    "lambo": "Lamborghini"}
# mobile_service._MODELL_ALIASE (RP-419)
MOBILE_MODELL_ALIASE = {("kia", "ceedsw"): "ceedsportswagon", ("kia", "ceedswceedsw"): "ceedsportswagon"}

_GENERATION_PUNKT = re.compile(r"\b(T\d)\.\d\b", re.IGNORECASE)
_KLAMMER = re.compile(r"\s*\([^)]*\)\s*")
_GENERISCH = re.compile(r"^\s*(weitere|andere|sonstige|other|others|misc)\b", re.IGNORECASE)
_WORT = re.compile(r"[^\W_]+")
_SAMMELWOERTER = {"andere", "alle", "weitere", "sonstige", "other", "others", "misc"}


# Pruefung 05.10.2026 (Paket 1): "Mercedes Andere" + Beschreibung brauchte 2,5 s — 474.000 Aufrufe von
# lese_form/norm fuer immer dieselben Woerter (jedes Katalogmodell x jedes Wort der Beschreibung). Mit Merkzettel
# rechnet jedes Wort einmal; das Ergebnis ist dasselbe (reine Funktionen).
@functools.lru_cache(maxsize=100_000)
def norm(wert: Optional[str]) -> str:
    """Kleinbuchstaben ohne Akzente, nur a-z und 0-9 (FahrzeugCodes.Norm / mobile_service._normalize)."""
    if not wert:
        return ""
    raus = []
    for c in unicodedata.normalize("NFKD", wert):
        if unicodedata.category(c) == "Mn":
            continue
        k = c.lower()
        if len(k) == 1 and ("a" <= k <= "z" or "0" <= k <= "9"):
            raus.append(k)
    return "".join(raus)


def abstand(a: str, b: str) -> int:
    """Levenshtein-Abstand."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    vorher = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        jetzt = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            jetzt[j] = min(vorher[j] + 1, jetzt[j - 1] + 1, vorher[j - 1] + (0 if ca == cb else 1))
        vorher = jetzt
    return vorher[len(b)]


def ist_platzhalter(modell: Optional[str]) -> bool:
    """Platzhalter statt Modell ("weitere VW" bei Kleinanzeigen, "Andere", "Sonstige")."""
    return not (modell or "").strip() or bool(_GENERISCH.match(modell or ""))


@functools.lru_cache(maxsize=100_000)
def lese_form(s: Optional[str]) -> str:
    """i/l/1 -> 1, o/0 -> 0 (Befund 03.10.2026: Hyundai "i10" als "ilO" gelesen)."""
    return "".join("1" if c in "il1" else "0" if c in "o0" else c for c in norm(s))


def verwechslung(namen, modell: str) -> Optional[str]:
    """Katalogname, der sich vom gelesenen Modell nur durch i/l/1- bzw. o/0-Verwechslungen unterscheidet —
    nur fuer Namen mit Ziffern und nur, wenn genau einer passt."""
    ziel = lese_form(modell)
    if len(ziel) < 2:
        return None
    treffer: List[str] = []
    gesehen = set()
    for n in namen:
        if any(ch.isdigit() for ch in n) and lese_form(n) == ziel and n.lower() not in gesehen:
            gesehen.add(n.lower())
            treffer.append(n)
    return treffer[0] if len(treffer) == 1 else None


def _woerter(text: str) -> List[str]:
    return _WORT.findall(text.lower())


def aus_titel(modelle, titel: Optional[str]) -> Optional[str]:
    """Katalogmodell, dessen Woerter ALLE in der Ueberschrift stehen — auch verstreut ("T5 Bulli multivan"
    -> "T5 Multivan") oder zusammengezogen ("XC 60" -> "XC60"). Mehr passende Woerter gewinnen, dann der
    laengere Name. Sammelnamen ("T5 andere", "T5 (Alle)") nie."""
    if not titel or not titel.strip():
        return None
    woerter = _woerter(titel)
    im_titel = set(woerter)
    for i in range(len(woerter) - 1):
        im_titel.add(woerter[i] + woerter[i + 1])
    im_titel_lese = {lese_form(w) for w in im_titel}

    def steht(wort: str) -> bool:
        return wort in im_titel or (any(ch.isdigit() for ch in wort) and lese_form(wort) in im_titel_lese)

    bester: Optional[str] = None
    beste_woerter = 0
    for name in modelle:
        w = _woerter(name)
        if not w or any(x in _SAMMELWOERTER for x in w) or not all(steht(x) for x in w):
            continue
        if len(w) > beste_woerter or (len(w) == beste_woerter and len(name) > len(bester or "")):
            bester = name
            beste_woerter = len(w)
    return bester


def _mit_ziffer(s: str) -> bool:
    return any(ch.isdigit() for ch in s)


def aus_beschreibung(modelle, text: Optional[str]) -> Optional[str]:
    """Befund 04.10.2026 (Mercedes, Feld "Andere", Ueberschrift "Mercedes-Benz Weitere Mercedes Be...",
    Beschreibung "... meinen Mercedes C 300 e ..."): Modell aus der BESCHREIBUNG — strenger als bei der
    Ueberschrift, weil Beschreibungen viel erwaehnen: die Woerter des Katalognamens muessen DIREKT
    hintereinander stehen ("C 300", "C300", "320d" fuer "320"), sehr kurze Namen ("G", "V", "T1") zaehlen nicht,
    Sammelnamen nie. Mehr Woerter gewinnen, dann der laengere Name, dann die fruehere Stelle."""
    if not text or not text.strip():
        return None
    t = _woerter(text[:800])
    bester: Optional[str] = None
    beste = (0, 0)
    beste_pos = 0
    for name in modelle:
        w = _woerter(name)
        if not w or any(x in _SAMMELWOERTER for x in w) or len(norm(name)) <= 2:
            continue
        zusammen = "".join(w)
        ziffer = _mit_ziffer(zusammen)
        gleich = (lambda a, b: a == b or (ziffer and lese_form(a) == lese_form(b)))
        pos = None
        for i in range(len(t)):
            if len(t) - i >= len(w) and all(gleich(t[i + j], w[j]) for j in range(len(w))):
                pos = i
            else:
                for k in (1, 2, 3):
                    stueck = "".join(t[i:i + k])
                    if gleich(stueck, zusammen) or (
                            k == 1 and ziffer and stueck.startswith(zusammen) and stueck[len(zusammen):].isalpha()
                            and len(stueck) - len(zusammen) <= 2):
                        pos = i
                        break
            if pos is not None:
                break
        if pos is None:
            continue
        rang = (len(w), len(name))
        if rang > beste or (rang == beste and pos < beste_pos):
            bester, beste, beste_pos = name, rang, pos
    return bester


def _unscharf(ziel: str, namen) -> Optional[str]:
    """Genau ein Katalogname mit kleinem Abstand (Lesefehler), sonst None."""
    if len(ziel) < 4:
        return None
    erlaubt = 2 if len(ziel) >= 8 else 1
    eindeutig = list(dict.fromkeys(namen))
    beste = sorted(((n, abstand(ziel, n)) for n in eindeutig), key=lambda x: x[1])
    beste = [x for x in beste if x[1] <= erlaubt]
    if not beste:
        return None
    if len(beste) > 1 and beste[1][1] == beste[0][1]:
        return None
    return beste[0][0]


def wortgrenzen(name: str) -> set:
    """autoscout_service._wortgrenzen: Positionen, an denen ein Wortteil endet."""
    grenzen: set = set()
    if not name:
        return grenzen
    text = "".join(c for c in unicodedata.normalize("NFD", name) if unicodedata.category(c) != "Mn").lower()
    n = 0
    vorher = None
    for ch in text:
        if "a" <= ch <= "z" or "0" <= ch <= "9":
            art = "z" if ch.isdigit() else "b"
            if vorher is not None and art != vorher and n > 0:
                grenzen.add(n)
            n += 1
            vorher = art
        else:
            if n > 0:
                grenzen.add(n)
            vorher = None
    if n > 0:
        grenzen.add(n)
    return grenzen


@dataclass
class MobileMarke:
    name: str
    id: str
    modelle: List[Tuple[str, str, str]] = field(default_factory=list)   # (norm, id, name) in Katalogreihenfolge
    index: Dict[str, str] = field(default_factory=dict)
    roh: List[str] = field(default_factory=list)


@dataclass
class AutoScoutMarke:
    make_id: int
    name: str
    modelle: List[Tuple[int, str]] = field(default_factory=list)       # (model_id, model_name)


@dataclass
class ModellTreffer:
    id: str
    name: str
    unscharf: bool
    aus_titel: bool = False


def _klammer_weg(name: str) -> str:
    return _KLAMMER.sub(" ", name).strip()


class Katalog:
    def __init__(self, mobile_json: dict, autoscout_json: list):
        self.mobile: Dict[str, MobileMarke] = {}
        self.autoscout: List[AutoScoutMarke] = []
        for marke in mobile_json.get("marken") or []:
            name = marke.get("name") or ""
            if not name or "id" not in marke:
                continue
            m = MobileMarke(name=name, id=str(marke["id"]))
            roh = set()
            for mod in marke.get("modelle") or []:
                mn = mod.get("name") or ""
                if not mn or "id" not in mod:
                    continue
                mid = str(mod["id"])
                nm = norm(mn)
                if nm and nm not in m.index:
                    m.index[nm] = mid
                    m.modelle.append((nm, mid, mn))
                sauber = _klammer_weg(mn)
                if sauber and sauber != mn:
                    ns = norm(sauber)
                    if ns and ns not in m.index:
                        m.index[ns] = mid
                        m.modelle.append((ns, mid, mn))
                if sauber and sauber not in ("Andere", "Sonstige", "Weitere"):
                    roh.add(sauber)
            m.roh = sorted(roh)                      # C#: SortedSet mit StringComparer.Ordinal
            self.mobile[norm(name)] = m
        for marke in autoscout_json or []:
            m = AutoScoutMarke(make_id=int(marke["makeId"]), name=marke.get("makeName") or "")
            for mod in marke.get("models") or []:
                m.modelle.append((int(mod["modelId"]), mod.get("modelName") or ""))
            self.autoscout.append(m)
        self._as_norm = [norm(a.name) for a in self.autoscout]

    # ---- Marke/Modell trennen ------------------------------------------------
    def _marke_bekannt(self, n: str) -> bool:
        return bool(n) and (n in self.mobile or n in MOBILE_ALIASE or n in AUTOSCOUT_ALIASE or n in self._as_norm)

    def teile(self, text: str) -> Optional[Tuple[str, str]]:
        """"Land Rover Range Rover Evoque" -> ("Land Rover", "Range Rover Evoque")."""
        woerter = [w for w in (text or "").split(" ") if w]
        if not woerter:
            return None
        for n in range(min(4, len(woerter)), 0, -1):
            marke = " ".join(woerter[:n])
            if self._marke_bekannt(norm(marke)):
                return marke, " ".join(woerter[n:])
        erstes = norm(woerter[0])
        if len(erstes) >= 5:
            kandidaten = list(dict.fromkeys(
                k for k in list(self.mobile.keys()) + self._as_norm if len(k) >= 5 and abstand(k, erstes) == 1))
            if len(kandidaten) == 1:
                k = kandidaten[0]
                name = self.mobile[k].name if k in self.mobile else \
                    next(a.name for a in self.autoscout if norm(a.name) == k)
                return name, " ".join(woerter[1:])
        return None

    # ---- mobile.de -----------------------------------------------------------
    def mobile_marke(self, name: Optional[str]) -> Optional[MobileMarke]:
        n = norm(name)
        if not n:
            return None
        if n in self.mobile:
            return self.mobile[n]
        alias = MOBILE_ALIASE.get(n)
        if alias and alias in self.mobile:
            return self.mobile[alias]
        as_name = AUTOSCOUT_ALIASE.get(n)
        if as_name and norm(as_name) in self.mobile:
            return self.mobile[norm(as_name)]
        return None

    @staticmethod
    def _modell_kandidaten(modell: str) -> List[str]:
        raus: List[str] = []
        for c in (_GENERATION_PUNKT.sub(r"\1", modell), modell):
            if c and c not in raus:
                raus.append(c)
        return raus

    @staticmethod
    def _modell_name(marke: MobileMarke, mid: str) -> Optional[str]:
        for _n, i, name in marke.modelle:
            if i == mid:
                return _klammer_weg(name)
        return None

    def _mobile_aufloesen(self, marke: MobileMarke, modell: str) -> Optional[str]:
        marke_norm = norm(marke.name)
        for kandidat in self._modell_kandidaten(modell):
            n = norm(kandidat)
            if not n:
                continue
            n = MOBILE_MODELL_ALIASE.get((marke_norm, n), n)
            if n in marke.index:
                return marke.index[n]
            treffer = sorted((x[0] for x in marke.modelle if x[0].startswith(n + "klasse")), key=len)
            if not treffer and len(n) >= 3:
                treffer = sorted((x[0] for x in marke.modelle if x[0].startswith(n)), key=len)
            if treffer:
                return marke.index[treffer[0]]
            mit_ziffer = any(ch.isdigit() for ch in n)
            for laenge in range(len(n) - 1, 0, -1):
                praefix = n[:laenge]
                for mn, mid, _name in marke.modelle:
                    if mn == praefix or (mit_ziffer and mn.startswith(praefix + "klasse")):
                        return mid
            erstes = re.match(r"^[a-z]+", n) or re.match(r"^\d+", n)
            if erstes and erstes.group(0) in marke.index:
                return marke.index[erstes.group(0)]
        return None

    def mobile_modell(self, marke: MobileMarke, modell: str, titel: Optional[str]) -> Optional[ModellTreffer]:
        if not ist_platzhalter(modell):
            mid = self._mobile_aufloesen(marke, modell)
            if mid is not None:
                return ModellTreffer(mid, self._modell_name(marke, mid) or modell, False)
            u = _unscharf(norm(modell), [x[0] for x in marke.modelle])
            if u is not None:
                uid = marke.index[u]
                return ModellTreffer(uid, self._modell_name(marke, uid) or modell, True)
            v = verwechslung([x[2] for x in marke.modelle], modell)
            if v is not None:
                vid = self._mobile_aufloesen(marke, v)
                if vid is not None:
                    return ModellTreffer(vid, self._modell_name(marke, vid) or v, True)
        t = aus_titel(marke.roh, titel)
        tid = self._mobile_aufloesen(marke, t) if t else None
        return ModellTreffer(tid, self._modell_name(marke, tid) or t, False, True) if tid is not None else None

    # ---- AutoScout24 ---------------------------------------------------------
    def autoscout_marke(self, name: Optional[str]) -> Optional[AutoScoutMarke]:
        ziel = norm(name)
        if not ziel:
            return None
        for a, an in zip(self.autoscout, self._as_norm):
            if an == ziel:
                return a
        alias = AUTOSCOUT_ALIASE.get(ziel)
        if alias:
            for a, an in zip(self.autoscout, self._as_norm):
                if an == norm(alias):
                    return a
        for a, an in zip(self.autoscout, self._as_norm):
            if an.startswith(ziel):
                return a
        return None

    @staticmethod
    def _autoscout_aufloesen(marke: AutoScoutMarke, modell: str) -> Optional[Tuple[int, str]]:
        ziel = norm(modell)
        if not ziel:
            return None
        normiert = [(m, norm(m[1])) for m in marke.modelle]
        for m, n in normiert:
            if n == ziel:
                return m
        for m, _n in normiert:
            teile = [norm(t) for t in m[1].split("/")]
            if len(teile) > 1 and ziel in teile:
                return m
        grenzen = wortgrenzen(modell)

        def an_grenze(n: str) -> bool:
            if len(n) in grenzen:
                return True
            idx = len(n) - 2
            if idx < 0:
                idx += len(ziel)
            return (n[-1].isalpha() and (len(n) - 1) in grenzen
                    and 0 <= idx < len(ziel) and ziel[idx].isalnum())

        anfaenge = [(m, n) for m, n in normiert if 0 < len(n) < len(ziel) and ziel.startswith(n) and an_grenze(n)]
        if anfaenge:
            return sorted(anfaenge, key=lambda x: -len(x[1]))[0][0]
        laenger = [(m, n) for m, n in normiert if n and n.startswith(ziel)]
        an_wortgrenze = [m for m, _n in laenger if len(ziel) in wortgrenzen(m[1])]
        if len(an_wortgrenze) == 1:
            return an_wortgrenze[0]
        if len(laenger) == 1:
            return laenger[0][0]
        if laenger:
            return None
        enthalten = [m for m, n in normiert if ziel in n]
        return enthalten[0] if len(enthalten) == 1 else None

    def autoscout_modell(self, marke: AutoScoutMarke, modell: str, titel: Optional[str]) -> Optional[ModellTreffer]:
        if not ist_platzhalter(modell):
            m = self._autoscout_aufloesen(marke, modell)
            if m is not None:
                return ModellTreffer(str(m[0]), m[1], False)
            u = _unscharf(norm(modell), [norm(x[1]) for x in marke.modelle])
            if u is not None:
                um = next(x for x in marke.modelle if norm(x[1]) == u)
                return ModellTreffer(str(um[0]), um[1], True)
            v = verwechslung([x[1] for x in marke.modelle], modell)
            if v is not None:
                vm = self._autoscout_aufloesen(marke, v)
                if vm is not None:
                    return ModellTreffer(str(vm[0]), vm[1], True)
        t = aus_titel([x[1] for x in marke.modelle if not _GENERISCH.match(x[1])], titel)
        tm = self._autoscout_aufloesen(marke, t) if t else None
        return ModellTreffer(str(tm[0]), tm[1], False, True) if tm is not None else None


_KATALOG: Optional[Katalog] = None


def katalog() -> Katalog:
    global _KATALOG
    if _KATALOG is None:
        mobile = json.loads((_ORDNER / "mobile_makes_models.json").read_text(encoding="utf-8"))
        autoscout = json.loads((_ORDNER / "autoscout_makes.json").read_text(encoding="utf-8"))
        _KATALOG = Katalog(mobile, autoscout)
    return _KATALOG


def zuordnen(marke_modell_text: str, titel: Optional[str] = None, k: Optional[Katalog] = None,
             beschreibung: Optional[str] = None) -> dict:
    """Wie Zuordner.Zuordnen im Programm (bis 1.3.5).

    Rueckgabe:
      marke_text/modell_text — was an die Link-Bauer geht (wie das Programm es schickte: AutoPointer-Text,
                               korrigiert nur bei Lesefehler, Platzhalter oder Ueberschrift)
      marke/modell           — Katalognamen zur Anzeige ("Volkswagen", "Golf")
      erkannt                — Marke in einem der Kataloge gefunden
      aus_beschreibung       — Modell kam aus der Beschreibung (nur wenn Feld und Ueberschrift nichts ergaben)
    """
    k = k or katalog()
    text = marke_modell_text or ""
    teile = k.teile(text)
    ganz_aus_titel = False
    if (teile is None or ist_platzhalter(teile[0])) and titel and titel.strip():
        aus_t = k.teile(titel.strip())
        if aus_t is not None and not ist_platzhalter(aus_t[0]):
            teile = aus_t
            ganz_aus_titel = True
    if teile is None:
        return {"marke_text": text, "modell_text": "", "marke": None, "modell": None, "erkannt": False,
                "aus_beschreibung": False}
    marke, modell = teile
    mm = k.mobile_marke(marke)
    am = k.autoscout_marke(marke)
    mob = k.mobile_modell(mm, modell, titel) if mm else None
    asm = k.autoscout_modell(am, modell, titel) if am else None
    aus_text = False
    if (mm or am) and mob is None and asm is None and beschreibung:
        # Befund 04.10.2026: Feld und Ueberschrift ohne Modell — die Beschreibung fragen (strenger)
        if mm:
            t = aus_beschreibung(mm.roh, beschreibung)
            tid = k._mobile_aufloesen(mm, t) if t else None
            if tid is not None:
                mob = ModellTreffer(tid, k._modell_name(mm, tid) or t, False, True)
        if am:
            t = aus_beschreibung([x[1] for x in am.modelle if not _GENERISCH.match(x[1])], beschreibung)
            tm = k._autoscout_aufloesen(am, t) if t else None
            if tm is not None:
                asm = ModellTreffer(str(tm[0]), tm[1], False, True)
        aus_text = bool(mob or asm)
    ersetzt = ganz_aus_titel or ist_platzhalter(modell) or bool(mob and mob.aus_titel) or bool(asm and asm.aus_titel)
    if mob and mob.unscharf:
        modell_text = mob.name
    elif asm and asm.unscharf:
        modell_text = asm.name
    elif ersetzt:
        modell_text = (mob.name if mob else asm.name if asm else modell)
    else:
        modell_text = modell
    return {
        "marke_text": marke,
        "modell_text": modell_text,
        "marke": mm.name if mm else (am.name if am else marke),
        "modell": mob.name if mob else (asm.name if asm else modell),
        "erkannt": bool(mm or am),
        "aus_beschreibung": aus_text,
    }
