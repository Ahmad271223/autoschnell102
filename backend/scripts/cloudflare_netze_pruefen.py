# -*- coding: utf-8 -*-
"""Stimmen die Cloudflare-Netze in der Load-Balancer-Vorlage noch?

Die Vorlage deploy/hinter-loadbalancer.conf.template traegt die
Cloudflare-Adressbereiche fest ein (set_real_ip_from). Kommt bei
Cloudflare ein Bereich dazu, saehe nginx fuer Besucher aus diesem Bereich
die Cloudflare-Adresse statt der echten — und die Anfragesperre wuerde
alle diese Besucher in EINEN Zaehler werfen. Dieses Programm holt die
aktuelle Liste von Cloudflare und vergleicht.

    python scripts/cloudflare_netze_pruefen.py            # Bericht, Exit 1 bei Abweichung
    python scripts/cloudflare_netze_pruefen.py --schreiben  # Vorlage anpassen

Laeuft ohne Datenbank, braucht nur Internet.
"""
import ipaddress
import re
import sys
import urllib.request
from pathlib import Path

VORLAGE = Path(__file__).resolve().parents[2] / "deploy" / "hinter-loadbalancer.conf.template"
QUELLEN = ("https://www.cloudflare.com/ips-v4", "https://www.cloudflare.com/ips-v6")
_ZEILE = re.compile(r"^(\s*)set_real_ip_from\s+([0-9a-fA-F:.]+/\d+);\s*$", re.M)


def cloudflare_netze():
    netze = set()
    for url in QUELLEN:
        # Cloudflare weist die Standardkennung von urllib mit 403 ab.
        anfrage = urllib.request.Request(url, headers={"User-Agent": "autoschnell-netzpruefung/1.0"})
        with urllib.request.urlopen(anfrage, timeout=20) as antw:   # noqa: S310
            for zeile in antw.read().decode("utf-8").splitlines():
                zeile = zeile.strip()
                if zeile:
                    netze.add(str(ipaddress.ip_network(zeile)))
    return netze


def netze_in_vorlage(text):
    return {str(ipaddress.ip_network(m.group(2))) for m in _ZEILE.finditer(text)}


def vorlage_anpassen(text, soll):
    """Ersetzt den Block fester Cloudflare-Zeilen durch die Soll-Liste."""
    treffer = list(_ZEILE.finditer(text))
    if not treffer:
        raise SystemExit("keine set_real_ip_from-Zeilen mit festen Netzen gefunden")
    einzug = treffer[0].group(1)
    neu = "\n".join(f"{einzug}set_real_ip_from {n};" for n in sorted(
        soll, key=lambda n: (":" in n, ipaddress.ip_network(n))))
    anfang, ende = treffer[0].start(), treffer[-1].end()
    return text[:anfang] + neu + text[ende:]


def main(argv):
    schreiben = "--schreiben" in argv
    text = VORLAGE.read_text(encoding="utf-8")
    ist = netze_in_vorlage(text)
    try:
        soll = cloudflare_netze()
    except Exception as exc:                        # noqa: BLE001
        print(f"FEHLER: Cloudflare-Liste nicht abrufbar: {exc}")
        return 2
    fehlt = sorted(soll - ist)
    zuviel = sorted(ist - soll)
    if not fehlt and not zuviel:
        print(f"OK: alle {len(soll)} Cloudflare-Netze stehen in der Vorlage")
        return 0
    for n in fehlt:
        print(f"FEHLT in der Vorlage: {n}")
    for n in zuviel:
        print(f"NICHT mehr bei Cloudflare: {n}")
    if schreiben:
        VORLAGE.write_text(vorlage_anpassen(text, soll), encoding="utf-8", newline="\n")
        print(f"Vorlage angepasst: {VORLAGE} — Proxy neu starten "
              "(docker compose up -d --force-recreate proxy)")
        return 0
    print("Abweichung — mit --schreiben anpassen, dann Proxy neu starten")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
