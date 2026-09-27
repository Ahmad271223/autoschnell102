# -*- coding: utf-8 -*-
"""Kostendeckel je KI-Lauf (Betriebsalarm 27.09.2026 11:10: ein Vertrag
kostete 15,31 ct bei 15 ct Grenze — die Grenze wurde erst NACH dem Lauf
geprueft, das Geld war schon ausgegeben. Vorgabe Ahmad: "mach maximum 20ct
aber versuchen 15ct").

Zwei Grenzen je Lauf (Vertrag und Abholung):

* HART (KI_KOSTEN_MAX_CT, Standard 20 ct): kein Lauf kostet je mehr. Vor
  JEDEM Aufruf (Bewertung, jede Recherche-Anfrage inkl. Fortsetzung nach
  pause_turn) wird eine sichere Obergrenze gerechnet; passt sie nicht in den
  Rest der Kasse, wird der Aufruf NICHT gemacht.
* ZIEL (KI_KOSTEN_ZIEL_CT, Standard 15 ct): so wird geplant. Zuerst wird die
  Bewertung (Sonnet) mit dem laengstmoeglichen Fall-Text eingeplant; die
  Websuche bekommt nur den Rest bis zum Ziel. Die Spanne Ziel..Hart ist
  Puffer, falls Suchergebnisse groesser ausfallen als eingeplant — dann
  laeuft die Bewertung trotzdem, solange die harte Grenze haelt; sonst wird
  der Fall-Text gekuerzt/weggelassen, dann max_tokens gesenkt, zuletzt
  entfaellt die Bewertung (Status "kostendeckel", Alarm
  ki_kostendeckel_gegriffen) — nie ueberschreiten.

Sichere Obergrenze eines Aufrufs (Cent):
    Eingabe-Tokens x Eingabepreis x 1,25   (jedes Eingabe-Token kostet 1x,
                                            beim Cache-Schreiben 1,25x, beim
                                            Cache-Lesen 0,1x — 1,25 ist der
                                            schlimmste Fall; kein 1h-Cache,
                                            kein inference_geo gesetzt)
  + max_tokens x Ausgabepreis            (max_tokens ist hart)
  + erlaubte Suchen x 1 ct               (10 USD je 1.000 Suchen)
  + bei Websuche die Ergebnis-Tokens     (SUCHE_TOKENS_MAX, siehe unten)

Die Bewertung wird per messages.count_tokens mit denselben Parametern wie
der echte Aufruf gezaehlt (provider.eingabe_tokens_zaehlen, haengt als
Attribut `zaehlen` an provider.json_bewerten — eine Attrappe ohne das
Attribut zaehlt nie gegen die echte API). Die Recherche laesst sich NICHT
zaehlen (count_tokens lehnt Server-Werkzeuge wie web_search ab) — dort
rechnen wir sicher nach oben aus der Laenge.

Die KI bleibt beratend: ein Deckel stoppt nur die KI, nie Vertrag/Freigabe.
"""
from __future__ import annotations

import json
import logging
import math
from typing import Any, Callable, Dict, List, Optional

from ai import budget, kalibrierung

log = logging.getLogger("autohandel.ki")

# ------------------------------------------------ Konstanten (mit Begruendung)
# Cache-Schreiben kostet 1,25x — der teuerste Fall je Eingabe-Token.
CACHE_FAKTOR = 1.25
# Sicherheitsabstand unter der harten Grenze: 3 %, mindestens 0,3 ct. Deckt
# Rundung (kosten_ct auf 2 Stellen), eine Abweichung von count_tokens ueber
# den Zuschlag hinaus und Preis-Rundungen ab. Gerechnet wird in US-Cent, die
# Grenze ist als Euro-Cent gemeint — US-Cent liegen zahlenmaessig hoeher,
# das ist zusaetzlich vorsichtig.
SICHERHEIT_ANTEIL = 0.03
SICHERHEIT_MIN_CT = 0.3
# count_tokens ist laut Doku eine Schaetzung, die "um eine kleine Menge"
# abweichen kann -> +5 % und +300 Tokens (strukturierte Ausgabe schiebt
# einen kleinen System-Teil ein).
ZAEHL_ZUSCHLAG_ANTEIL = 0.05
ZAEHL_ZUSCHLAG_TOKENS = 300
# Ob count_tokens das JSON-Schema (output_config) mitzaehlt, ist nicht
# dokumentiert — wir rechnen das Schema deshalb IMMER zusaetzlich (Bytes/2).
# Ersatzschaetzung ohne count_tokens: hoechstens 1 Token je 2 Byte UTF-8.
# Gemessen/ueblich sind ~3,2 Zeichen je Token (deutscher Text, JSON);
# Ziffern-, Hex- und Satzzeichenfolgen liegen bei >= 2 Byte je Token,
# Umlaute zaehlen 2 Byte — Bytes/2 liegt also sicher ueber dem Echtwert.
BYTES_JE_TOKEN_MIN = 2.0
# Websuche: Tokens, die EIN Suchergebnis in den Kontext bringt. Die Server-
# Schleife liest jedes Ergebnis in jeder weiteren Runde erneut; wir rechnen
# das ausdruecklich (siehe recherche_eingabe_max). Messwert 26.09.2026: vier
# Suchen in einer Anfrage ~140.000 Eingabe-Tokens -> ~13.000 Tokens je
# Ergebnis nach diesem Modell; +50 % Reserve = 20.000. Die echten Werte je
# Anfrage stehen im Bericht (posten[].tokens_je_suche) und im Log — damit
# laesst sich die Konstante spaeter nachschaerfen.
SUCHE_TOKENS_MAX = 20000
SUCHE_CT = 1.0
# Das Websuch-Werkzeug bringt einen eigenen System-Teil mit (Haiku 4.5: 496
# Tokens laut Preisliste) plus die Werkzeugdefinition (gesperrte Domains,
# Ort) -> aufgerundet 1.000.
WERKZEUG_TOKENS = 1000
# Recherche: erlaubte Ausgabe je Anfrage in dieser Reihenfolge probieren. Bei
# zwei Suchen nicht unter 2.000 (sonst reicht es nicht fuer die Werte samt
# Datenblock), bei einer Suche bis 1.500 (Kostenkarte: typisch ~1.500).
RECHERCHE_MAX_TOKENS = {2: (3000, 2000), 1: (3000, 2000, 1500)}
# Bewertung: max_tokens nur bis zu dieser Untergrenze senken — der Probelauf
# 26.09.2026 schnitt die JSON-Antwort bei 1.600 schon ab.
BEWERTUNG_MAX_TOKENS_MIN = 1800
# Stufen, wenn die Bewertung nicht mehr in die harte Grenze passt.
FALL_ANTEILE = (1.0, 0.5, 0.0)

HINWEIS_WEBSUCHE_ENTFALLEN = "Websuche entfallen — Kostendeckel je Lauf"


def _preis_ct_je_token(modell: str) -> tuple:
    """(Eingabe, Ausgabe) in Cent je Token. Unbekanntes Modell: fuer die
    Obergrenze der TEUERSTE bekannte Preis (sicher nach oben)."""
    preise = kalibrierung.PREIS_JE_MIO
    if modell in preise:
        ein, aus = preise[modell]
    else:
        ein = max(p[0] for p in preise.values())
        aus = max(p[1] for p in preise.values())
    return ein / 1e6 * 100, aus / 1e6 * 100


def tokens_schaetzen(*teile: Any) -> int:
    """Sichere Obergrenze der Tokens aus der Laenge (Bytes/2)."""
    n = 0
    for t in teile:
        if t is None:
            continue
        if not isinstance(t, str):
            t = json.dumps(t, ensure_ascii=False, sort_keys=True)
        n += len(t.encode("utf-8"))
    return int(math.ceil(n / BYTES_JE_TOKEN_MIN))


def eingabe_gesamt(usage: Dict[str, Any]) -> int:
    u = usage or {}
    return int(u.get("input_tokens") or 0) + int(u.get("cache_creation_input_tokens") or 0) \
        + int(u.get("cache_read_input_tokens") or 0)


def recherche_eingabe_max(basis_tokens: int, suchen: int, max_tokens: int) -> int:
    """Hoechstmenge Eingabe-Tokens einer Recherche-Anfrage mit bis zu `suchen`
    Suchen: hoechstens suchen+1 Runden der Server-Schleife, jede liest die
    Basis (System + Frage + Werkzeug) erneut; die bis dahin erzeugte Ausgabe
    (<= max_tokens) wird hoechstens `suchen`-mal erneut gelesen; das i-te
    Suchergebnis wird in allen folgenden Runden gelesen -> SUCHE_TOKENS_MAX x
    suchen x (suchen+1) / 2."""
    n = max(0, int(suchen))
    return (n + 1) * int(basis_tokens) + n * int(max_tokens) + SUCHE_TOKENS_MAX * n * (n + 1) // 2


def _kurz(usage: Dict[str, Any]) -> Dict[str, int]:
    return {k: int(v) for k, v in (usage or {}).items() if isinstance(v, (int, float))}


class Kostenkasse:
    """Kasse EINES Laufs: plant gegen das Ziel, laesst nie ueber die harte
    Grenze gehen und fuehrt die Einzelposten (fuer Abrechnung und Alarm)."""

    def __init__(self, *, art: str, ref: str = "", ziel_ct: Optional[float] = None,
                 hart_ct: Optional[float] = None):
        hart = float(hart_ct if hart_ct is not None else budget.kosten_max_ct())
        ziel = float(ziel_ct if ziel_ct is not None else budget.ziel_ct())
        self.art, self.ref = art, ref
        self.hart_ct = round(hart, 2)
        self.ziel_ct = round(min(ziel, hart), 2)
        self.sicherheit_ct = round(max(SICHERHEIT_MIN_CT, hart * SICHERHEIT_ANTEIL), 2)
        self.hart_budget_ct = round(max(0.0, hart - self.sicherheit_ct), 4)
        self.ziel_budget_ct = round(min(self.ziel_ct, self.hart_budget_ct), 4)
        self.posten: List[Dict[str, Any]] = []
        self.unsicher_ct = 0.0          # Aufrufe mit unbekanntem Ausgang (Zeitlimit/Abbruch), zur Obergrenze
        self.bewertung_reserve_ct = 0.0  # eingeplante Obergrenze der Bewertung (laengster Fall-Text)
        self.geplant_max_ct = 0.0        # Plan (gegen das ZIEL): Bewertung eingeplant + Websuche
        self.obergrenze_max_ct = 0.0     # vor einem Aufruf: gebunden + dessen Obergrenze (gegen HART)
        self.hinweise: List[str] = []
        self.bewertung_stufe: Optional[str] = None
        self.gegriffen = False           # Bewertung wegen des Deckels entfallen

    # -------------------------------------------- Stand
    @property
    def kosten_ct(self) -> float:
        """Tatsaechliche (aus usage gerechnete) Kosten des Laufs."""
        return round(sum(float(p.get("ct") or 0) for p in self.posten), 2)

    @property
    def gebunden_ct(self) -> float:
        """Fuer den Deckel: tatsaechlich + unsichere Aufrufe (zur Obergrenze)."""
        return round(self.kosten_ct + self.unsicher_ct, 4)

    def hinweis(self, text: str) -> None:
        if text and text not in self.hinweise:
            self.hinweise.append(text)

    def _geplant(self, betrag_ct: float) -> None:
        self.geplant_max_ct = round(max(self.geplant_max_ct, betrag_ct), 4)

    def _vor_aufruf(self, obergrenze_ct: float) -> None:
        self.obergrenze_max_ct = round(max(self.obergrenze_max_ct, self.gebunden_ct + obergrenze_ct), 4)

    def obergrenze_ct(self, modell: str, eingabe_tokens: int, max_tokens: int, suchen: int = 0) -> float:
        ein, aus = _preis_ct_je_token(modell)
        return round(eingabe_tokens * CACHE_FAKTOR * ein + int(max_tokens) * aus + suchen * SUCHE_CT, 4)

    # -------------------------------------------- Buchen
    def buchen(self, schritt: str, modell: str, usage: Dict[str, Any], *, obergrenze_ct: Optional[float] = None,
               **info: Any) -> float:
        ct = round(kalibrierung._kosten_usd(modell or "", usage or {}) * 100, 4)
        eintrag = {"schritt": schritt, "modell": modell or "", "ct": round(ct, 2), "usage": _kurz(usage),
                   "obergrenze_ct": round(obergrenze_ct, 2) if obergrenze_ct is not None else None}
        eintrag.update({k: v for k, v in info.items() if v is not None})
        self.posten.append(eintrag)
        return ct

    def unsicher_buchen(self, schritt: str, obergrenze_ct: float, grund: str = "") -> None:
        """Aufruf ohne usage (Zeitlimit, Abbruch): ob und wie viel berechnet
        wurde, ist nicht dokumentiert — fuer den Deckel zaehlt die Obergrenze."""
        self.unsicher_ct = round(self.unsicher_ct + max(0.0, float(obergrenze_ct or 0)), 4)
        self.posten.append({"schritt": schritt, "ct": 0.0, "unsicher_bis_ct": round(float(obergrenze_ct or 0), 2),
                            "grund": (grund or "")[:120]})

    # -------------------------------------------- Recherche (Websuche)
    def recherche_basis_tokens(self, system: str, frage: str) -> int:
        """Basis einer Recherche-Anfrage (System + Frage + Werkzeug). Nicht per
        count_tokens zaehlbar (Server-Werkzeug) -> sicher aus der Laenge."""
        return tokens_schaetzen(system, frage) + WERKZEUG_TOKENS

    def recherche_rest_ct(self) -> float:
        """Was die Websuche noch ausgeben darf: bis zum ZIEL, abzueglich der
        eingeplanten Bewertung und allem, was schon gebunden ist."""
        return round(self.ziel_budget_ct - self.bewertung_reserve_ct - self.gebunden_ct, 4)

    def recherche_plan(self, basis_tokens: int, max_suchen: int, modell: str) -> Optional[Dict[str, Any]]:
        """Groesste Anfrage (Suchen, dann max_tokens), deren Obergrenze in den
        Rest passt. None = keine Anfrage mehr (Websuche entfaellt/endet)."""
        rest = self.recherche_rest_ct()
        for n in range(max(0, int(max_suchen)), 0, -1):
            for m in RECHERCHE_MAX_TOKENS.get(n, RECHERCHE_MAX_TOKENS[2]):
                eingabe = recherche_eingabe_max(basis_tokens, n, m)
                b = self.obergrenze_ct(modell, eingabe, m, suchen=n)
                if b <= rest:
                    self._geplant(self.gebunden_ct + self.bewertung_reserve_ct + b)
                    self._vor_aufruf(b)
                    return {"max_uses": n, "max_tokens": m, "obergrenze_ct": b, "basis_tokens": int(basis_tokens),
                            "eingabe_max": eingabe}
        return None

    @staticmethod
    def fortsetzung_basis(basis_tokens: int, usage_bisher: Dict[str, Any]) -> int:
        """Basis einer Fortsetzung nach pause_turn: der ganze Verlauf geht
        erneut mit. Sicher nach oben: der Kontext am Ende der vorigen Anfragen
        ist hoechstens die Summe ihrer Eingabe plus ihrer Ausgabe."""
        return int(basis_tokens) + eingabe_gesamt(usage_bisher) + int((usage_bisher or {}).get("output_tokens") or 0)

    def recherche_buchen(self, modell: str, usage: Dict[str, Any], plan: Optional[Dict[str, Any]], nr: int) -> float:
        """Eine Recherche-Anfrage abrechnen und die tatsaechlichen Tokens je
        Suche festhalten (zum Nachschaerfen von SUCHE_TOKENS_MAX)."""
        suchen = int((usage or {}).get("web_search_requests") or 0)
        je_suche = None
        if suchen and plan:
            ueber = eingabe_gesamt(usage) - (suchen + 1) * int(plan.get("basis_tokens") or 0) \
                - suchen * int((usage or {}).get("output_tokens") or 0)
            je_suche = max(0, int(ueber / (suchen * (suchen + 1) / 2)))
        ct = self.buchen(f"recherche#{nr}", modell, usage, obergrenze_ct=(plan or {}).get("obergrenze_ct"),
                         suchen=suchen, tokens_je_suche=je_suche)
        if je_suche is not None:
            log.info("KI-Recherche %s (%s): %d Suche(n), %d Eingabe-Tokens, ~%d Tokens je Suche "
                     "(eingeplant %d), %.2f ct (Obergrenze %.2f ct)", self.ref, self.art, suchen,
                     eingabe_gesamt(usage), je_suche, SUCHE_TOKENS_MAX, ct, float((plan or {}).get("obergrenze_ct") or 0))
        return ct

    # -------------------------------------------- Bewertung
    async def _bewertung_eingabe(self, aufruf: Callable, *, system: str, zusatz: Optional[str], nutzer: Any,
                                 schema: Dict[str, Any]) -> Dict[str, Any]:
        """Eingabe-Tokens der Bewertung, sicher nach oben: count_tokens mit
        denselben Parametern (+5 % +300) plus das Schema; scheitert das Zaehlen
        (oder ist keins da), die Laengen-Schaetzung Bytes/2."""
        schema_tokens = tokens_schaetzen(schema)
        zaehlen = getattr(aufruf, "zaehlen", None)
        if zaehlen is not None:
            try:
                n = int(await zaehlen(system=system, zusatz=zusatz, nutzer=nutzer, schema=schema))
                if n > 0:
                    return {"tokens": int(math.ceil(n * (1 + ZAEHL_ZUSCHLAG_ANTEIL))) + ZAEHL_ZUSCHLAG_TOKENS
                            + schema_tokens, "gezaehlt": True}
            except Exception as exc:  # noqa: BLE001 — dann sicher schaetzen
                log.warning("count_tokens gescheitert (%s) — Laengen-Schaetzung", type(exc).__name__)
        return {"tokens": tokens_schaetzen(system, zusatz or "", nutzer) + schema_tokens + ZAEHL_ZUSCHLAG_TOKENS,
                "gezaehlt": False}

    async def bewertung_einplanen(self, aufruf: Callable, *, modell: str, system: str, nutzer: Any,
                                  schema: Dict[str, Any], zusatz_teile: List[str], max_tokens: int,
                                  fall_text_max_zeichen: int) -> float:
        """VOR der Recherche: Obergrenze der Bewertung mit dem laengstmoeglichen
        Fall-Text reservieren (Zeichen/2 als Tokens — sicher nach oben fuer
        Text, Quellen-Titel und Adressen)."""
        zusatz = _zusatz(zusatz_teile, "")
        e = await self._bewertung_eingabe(aufruf, system=system, zusatz=zusatz, nutzer=nutzer, schema=schema)
        fall_tokens = int(math.ceil(fall_text_max_zeichen / BYTES_JE_TOKEN_MIN))
        self.bewertung_reserve_ct = self.obergrenze_ct(modell, e["tokens"] + fall_tokens, max_tokens)
        self._geplant(self.gebunden_ct + self.bewertung_reserve_ct)
        return self.bewertung_reserve_ct

    async def bewerten(self, aufruf: Callable, *, modell: str, system: str, nutzer: Any, schema: Dict[str, Any],
                       zusatz_teile: List[str], fall_texte: List[str], max_tokens: int) -> Dict[str, Any]:
        """Die Bewertung nur aufrufen, wenn ihre Obergrenze in den Rest bis zur
        HARTEN Grenze passt. Stufen: voller Fall-Text -> gekuerzt -> ohne ->
        max_tokens bis BEWERTUNG_MAX_TOKENS_MIN senken -> entfallen."""
        rest = round(self.hart_budget_ct - self.gebunden_ct, 4)
        ein, aus = _preis_ct_je_token(modell)
        gewaehlt = None
        letzte = None
        gesehen = set()
        for i, fall_text in enumerate(fall_texte or [""]):
            zusatz = _zusatz(zusatz_teile, fall_text)
            if zusatz in gesehen:
                continue
            gesehen.add(zusatz)
            e = await self._bewertung_eingabe(aufruf, system=system, zusatz=zusatz, nutzer=nutzer, schema=schema)
            b = self.obergrenze_ct(modell, e["tokens"], max_tokens)
            if b <= rest:
                gewaehlt = {"zusatz": zusatz, "max_tokens": max_tokens, "obergrenze_ct": b, "gezaehlt": e["gezaehlt"],
                            "stufe": ("voll", "fall_gekuerzt", "ohne_fall")[min(i, 2)] if (fall_texte or [""])[0] else "voll"}
                break
            letzte = (zusatz, e)
        if gewaehlt is None and letzte is not None:
            zusatz, e = letzte
            eingabe_ct = e["tokens"] * CACHE_FAKTOR * ein
            m = int((rest - eingabe_ct) / aus) if aus > 0 else 0
            m = min(m, int(max_tokens))
            if m >= BEWERTUNG_MAX_TOKENS_MIN:
                gewaehlt = {"zusatz": zusatz, "max_tokens": m, "obergrenze_ct": self.obergrenze_ct(modell, e["tokens"], m),
                            "gezaehlt": e["gezaehlt"], "stufe": "max_tokens_gesenkt"}
        if gewaehlt is None:
            self.gegriffen = True
            self.bewertung_stufe = "entfallen"
            grund = (f"KI-Bewertung entfallen: der Kostendeckel je Lauf ({self.hart_ct:g} ct) wäre überschritten — "
                     "der Vorgang läuft normal weiter.")
            self.hinweis(grund)
            await self._alarm("ki_kostendeckel_gegriffen", grund=grund, rest_ct=round(rest, 2),
                              grenze_ct=self.hart_ct, posten=self.posten_text())
            return {"status": "kostendeckel", "grund": grund, "daten": None, "dauer_ms": 0, "modell": modell,
                    "usage": {}}
        self.bewertung_stufe = gewaehlt["stufe"]
        if gewaehlt["stufe"] != "voll":
            self.hinweis({"fall_gekuerzt": "Marktrecherche gekürzt — Kostendeckel je Lauf",
                          "ohne_fall": "Marktrecherche nicht an die Bewertung gegeben — Kostendeckel je Lauf",
                          "max_tokens_gesenkt": "Antwortlänge der Bewertung begrenzt — Kostendeckel je Lauf"}
                         [gewaehlt["stufe"]])
        self._vor_aufruf(gewaehlt["obergrenze_ct"])
        antwort = await aufruf(system=system, nutzer=nutzer, schema=schema, zusatz=gewaehlt["zusatz"] or None,
                               max_tokens=gewaehlt["max_tokens"], max_retries=0)
        usage = antwort.get("usage") or {}
        if usage:
            self.buchen("bewertung", antwort.get("modell") or modell, usage, obergrenze_ct=gewaehlt["obergrenze_ct"],
                        gezaehlt=gewaehlt["gezaehlt"], max_tokens=gewaehlt["max_tokens"])
        elif antwort.get("status") not in ("aus",):
            self.unsicher_buchen("bewertung", gewaehlt["obergrenze_ct"], antwort.get("grund") or "")
        return antwort

    # -------------------------------------------- Abschluss
    async def abschliessen(self) -> float:
        """Tatsaechliche Kosten; darueber hinaus nur die letzte Sicherung:
        Alarm ki_kosten_ueberschritten, wenn die HARTE Grenze doch gerissen
        wurde (darf praktisch nie kommen) — mit den Einzelposten."""
        kosten = self.kosten_ct
        if kosten > self.hart_ct:
            await self._alarm("ki_kosten_ueberschritten", kosten_ct=kosten, grenze_ct=self.hart_ct,
                              ziel_ct=self.ziel_ct, posten=self.posten_text())
        elif kosten > self.ziel_ct:
            log.info("KI-Lauf %s (%s) ueber dem Ziel: %.2f ct (Ziel %.2f, hart %.2f) — %s", self.ref, self.art,
                     kosten, self.ziel_ct, self.hart_ct, self.posten_text())
        return kosten

    def posten_text(self) -> str:
        teile = []
        for p in self.posten:
            if p.get("unsicher_bis_ct") is not None:
                teile.append(f"{p['schritt']} unsicher bis {p['unsicher_bis_ct']} ct")
                continue
            u = p.get("usage") or {}
            teile.append(f"{p['schritt']} {p.get('modell') or '?'} {p.get('ct')} ct (ein {eingabe_gesamt(u)}, "
                         f"aus {u.get('output_tokens', 0)}, Suchen {u.get('web_search_requests', 0)}; "
                         f"Obergrenze {p.get('obergrenze_ct')})")
        return "; ".join(teile)[:480]

    def bericht(self) -> Dict[str, Any]:
        kosten = self.kosten_ct
        return {"ziel_ct": self.ziel_ct, "hart_ct": self.hart_ct, "geplant_ct": round(self.geplant_max_ct, 2),
                "obergrenze_ct": round(self.obergrenze_max_ct, 2),
                "bewertung_reserve_ct": round(self.bewertung_reserve_ct, 2), "kosten_ct": kosten,
                "unsicher_ct": round(self.unsicher_ct, 2), "ueber_ziel": kosten > self.ziel_ct,
                "ueber_hart": kosten > self.hart_ct, "bewertung": self.bewertung_stufe, "gegriffen": self.gegriffen,
                "hinweise": list(self.hinweise), "posten": list(self.posten)}

    async def _alarm(self, typ: str, **details: Any) -> None:
        try:
            import betrieb
            from deps import db
            await betrieb.alarm(db, typ, ref=self.ref, art=self.art, **details)
        except Exception:  # noqa: BLE001 — der Alarm ist Beiwerk
            pass


def _zusatz(teile: List[str], fall_text: str) -> str:
    return "\n\n".join(t for t in (*(teile or []), fall_text) if t)
