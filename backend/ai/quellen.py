# -*- coding: utf-8 -*-
"""Quellenpruefung fuer die KI-Recherche (Pruefliste 30.09.2026).

Vorher galt eine Quelle als vertrauenswuerdig, wenn ein Stichwort IRGENDWO im
Rechnernamen oder im frei geschriebenen Quellennamen stand:

  * "atu" steckt in "Reparatur", "dat" in "Datenbank" — fast jeder Name passte,
  * "adac.de.beispiel.com" enthielt "adac.de",
  * ein Sprachmodell konnte "ADAC" neben jeden Wert schreiben.

Jetzt zaehlt nur die ADRESSE: der Rechnername muss genau eine bekannte Domain
sein (oder eine Unterdomain davon), und die Adresse muss in diesem Lauf
wirklich unter den Suchtreffern bzw. Zitaten der Websuche gewesen sein
("belegt"). Ein Quellenname ohne Adresse zaehlt nur, wenn er als GANZES Wort
auf eine belegte Adresse passt ("ADAC" -> adac.de).
"""
from __future__ import annotations

import re
from typing import Iterable, Optional, Set

#: Eingetragene Domains, exakt (Unterdomains eingeschlossen).
VERTRAUTE_DOMAINS = (
    "adac.de", "fairgarage.com", "fairgarage.de", "dat.de", "autobutler.de", "atu.de", "carglass.de", "wintec.de",
    "dekra.de", "boschcarservice.com", "bosch-car-service.de", "repareo.de", "werkstattvergleich.de",
    "dellen-doktor.de", "dellendoktor.de", "pitstop.de", "reifen.com", "reifendirekt.de", "autobild.de",
    "auto-motor-und-sport.de", "hella.com", "tuv.com", "tuvsud.com", "tuev-sued.de", "tuev-nord.de", "gtue.de",
    "autoscout24.de", "mobile.de", "kfz-betrieb.vogel.de", "autoservicepraxis.de", "kfz.net", "autoplenum.de",
    "reifenleader.de", "meinauto.de", "vergoelst.de", "euromaster.de", "point-s.de", "premio.de",
    "driver-center.de",
)
#: Fachbetriebe ohne feste Liste (Smart-Repair, Dellentechnik, Autoglas, Lackierer): das Stichwort muss im
#: NAMEN der eingetragenen Domain stehen (der Teil vor der Endung) — nie irgendwo im Rechnernamen.
VERTRAUTE_STICHWORTE = ("dellen", "smart-repair", "smartrepair", "autoglas", "lackprofi", "lackier")

_HOST_RE = re.compile(r"^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,}$")


def host_von(url) -> str:
    """Rechnername einer Adresse, klein, ohne www., Port und Zugangsdaten. '' wenn keiner erkennbar."""
    u = str(url or "").strip().lower()
    if not u:
        return ""
    u = u.split("//", 1)[-1]
    u = re.split(r"[/?#\s]", u, maxsplit=1)[0]
    u = u.rsplit("@", 1)[-1].split(":", 1)[0].strip(".")
    if u.startswith("www."):
        u = u[4:]
    return u if _HOST_RE.match(u) else ""


def stamm_domain(host: str) -> str:
    """Eingetragene Domain (die letzten zwei Teile): 'www.preise.adac.de' -> 'adac.de'."""
    teile = [t for t in str(host or "").split(".") if t]
    return ".".join(teile[-2:]) if len(teile) >= 2 else ""


def host_vertraut(host: str) -> bool:
    h = str(host or "").lower()
    if not h:
        return False
    if any(h == d or h.endswith("." + d) for d in VERTRAUTE_DOMAINS):
        return True
    name = stamm_domain(h).rsplit(".", 1)[0]
    return any(s in name for s in VERTRAUTE_STICHWORTE)


def name_passt(quelle, host: str) -> bool:
    """Nennt der Quellenname diese Domain als ganzes Wort? 'ADAC (2024)' -> adac.de ja;
    'Reparaturkosten-Portal' -> atu.de NEIN."""
    name = stamm_domain(host).rsplit(".", 1)[0]
    if not name:
        return False
    q = str(quelle or "").lower()
    if name in set(re.findall(r"[a-z0-9äöüß]+", q)):
        return True
    kompakt_name, kompakt_q = re.sub(r"[^a-z0-9]", "", name), re.sub(r"[^a-z0-9]", "", q)
    return len(kompakt_name) >= 6 and kompakt_name in kompakt_q


def belegter_host(quelle, url, belegt: Optional[Iterable[str]]) -> str:
    """Der Rechnername, auf den sich die Zeile wirklich stuetzt — nur wenn er in diesem Lauf unter den
    Treffern/Zitaten war. Erst die Adresse der Zeile, sonst der Quellenname als ganzes Wort. '' = nicht belegt."""
    hosts: Set[str] = {h for h in (belegt or []) if h}
    if not hosts:
        return ""
    staemme = {stamm_domain(h): h for h in sorted(hosts)}
    eigener = host_von(url)
    if eigener and stamm_domain(eigener) in staemme:
        return eigener if eigener in hosts else staemme[stamm_domain(eigener)]
    for h in sorted(hosts):
        if name_passt(quelle, h):
            return h
    return ""


def quelle_vertraut(quelle, url, belegt: Optional[Iterable[str]] = None) -> bool:
    """Ohne `belegt`: nur die Adresse der Zeile zaehlt (der Quellenname allein nie).
    Mit `belegt` (Rechnernamen der echten Suchtreffer): die Zeile muss darauf gestuetzt sein."""
    if belegt is None:
        return host_vertraut(host_von(url))
    return host_vertraut(belegter_host(quelle, url, belegt))
