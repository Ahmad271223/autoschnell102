# -*- coding: utf-8 -*-
"""Leseseiten (Seiten, die die Erweiterung nicht lesen konnte) STRUKTURELL untersuchen — ohne die Seite zu
uebertragen (Systemcheck 12.10.2026: 20 mobile.de-Seiten "keine Inseratsdaten", alle ~60 KB).

Gibt je Seite nur Kennzahlen und Marker aus (Titel, Groesse, welche Datenbloecke vorkommen, erste sichtbare Worte),
keine Personendaten.

Aufruf im Backend-Container:
    docker compose cp backend/scripts/leseseite_analysieren.py backend:/app/scripts/leseseite_analysieren.py
    docker compose exec -T backend python scripts/leseseite_analysieren.py            # die 5 neuesten
    docker compose exec -T backend python scripts/leseseite_analysieren.py --id <id>  # eine bestimmte
    docker compose exec -T backend python scripts/leseseite_analysieren.py --anzahl 10 --text 1200
"""
import argparse
import asyncio
import html as htmllib
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

MARKER = (
    ("__next_f.push", "Next.js-Datenstrom (Flight)"),
    ('"listing":{', '"listing":{ (Inserat-Objekt)'),
    ('"listing":{"attributes":', '"listing":{"attributes": (alte Marke)'),
    ('"attributes":[', '"attributes":[ (Attributliste)'),
    ("__NEXT_DATA__", "__NEXT_DATA__ (Pages-Router)"),
    ("application/ld+json", "ld+json"),
    ("__INITIAL_STATE__", "__INITIAL_STATE__"),
    ("window.__data", "window.__data"),
    ("priceRating", "priceRating"),
    ("htmlDescription", "htmlDescription"),
    ("img.classistatic.de", "Bildadressen classistatic"),
    ("mo-prod/images", "mo-prod/images"),
    ("captcha", "captcha"),
    ("Zugriff verweigert", "Zugriff verweigert"),
    ("Access Denied", "Access Denied"),
    ("akamai", "akamai"),
    ("challenge", "challenge"),
    ("consent", "consent"),
    ("Datenschutzeinstellungen", "Datenschutzeinstellungen"),
    ("nicht mehr verfügbar", "nicht mehr verfügbar"),
    ("nicht mehr online", "nicht mehr online"),
    ("Inserat wurde", "Inserat wurde"),
    ('<div id="root"', 'div#root'),
    ('<div id="__next"', 'div#__next'),
    ("vip-", "vip- (Klassen)"),
    ("data-testid", "data-testid"),
)


def sichtbarer_text(html: str, n: int) -> str:
    t = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
    t = re.sub(r"(?s)<[^>]+>", " ", t)
    t = htmllib.unescape(t)
    t = " ".join(t.split())
    return t[:n]


def untersuchen(html: str, text_n: int) -> None:
    print(f"    Laenge entpackt: {len(html):,} Zeichen")
    m = re.search(r"(?is)<title[^>]*>(.*?)</title>", html)
    print(f"    Titel: {htmllib.unescape(m.group(1)).strip()[:160] if m else '(kein <title>)'}")
    m = re.search(r'(?i)<link[^>]+rel="canonical"[^>]+href="([^"]+)"', html)
    print(f"    canonical: {m.group(1)[:160] if m else '(keiner)'}")
    m = re.search(r'(?i)<meta[^>]+http-equiv="refresh"[^>]+content="([^"]+)"', html)
    if m:
        print(f"    META-REFRESH: {m.group(1)[:160]}")
    print(f"    <script>-Bloecke: {len(re.findall(r'(?i)<script', html))}   davon __next_f.push: "
          f"{html.count('__next_f.push')}   JSON-Skripte: {len(re.findall(r'application/json', html))}")
    print("    Marker:")
    klein = html.lower()
    for muster, name in MARKER:
        n = klein.count(muster.lower())
        if n:
            print(f"      {n:>5} x {name}")
    # Script-Quellen (nur Hostnamen) — zeigen, welche Anwendung die Seite ist
    hosts = Counter()
    for src in re.findall(r'(?i)<script[^>]+src="([^"]+)"', html):
        h = re.sub(r"^https?://", "", src).split("/")[0]
        hosts[h] += 1
    if hosts:
        print("    Skript-Quellen: " + ", ".join(f"{h} ({n})" for h, n in hosts.most_common(6)))
    # grosse JSON-Schluessel im Datenstrom: welche Objekte stecken drin?
    schluessel = Counter(re.findall(r'"([A-Za-z_]{4,40})":\{', html))
    if schluessel:
        print("    haeufigste Objekt-Schluessel: " + ", ".join(f"{k} ({n})" for k, n in schluessel.most_common(25)))
    listen = Counter(re.findall(r'"([A-Za-z_]{4,40})":\[', html))
    if listen:
        print("    haeufigste Listen-Schluessel: " + ", ".join(f"{k} ({n})" for k, n in listen.most_common(15)))
    print(f"    sichtbarer Text (Anfang): {sichtbarer_text(html, text_n)}")


async def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--id", default=None)
    p.add_argument("--anzahl", type=int, default=5)
    p.add_argument("--text", type=int, default=600)
    args = p.parse_args()
    from deps import db
    import browser_helfer as bh
    import werkzeuge as wz
    from listing_identity import ListingIdentityError, get_listing_identity
    filt = {"id": args.id} if args.id else {}
    n = 0
    async for s in db[wz.SAMMLUNG_LESESEITEN].find(filt, {"_id": 0}).sort("erstellt_am", -1).limit(args.anzahl):
        n += 1
        print("=" * 100)
        print(f"Leseseite {s.get('id')}  {str(s.get('erstellt_am'))[:19]}  Portal {s.get('portal')}  gepackt {s.get('groesse')} B")
        print(f"  Grund: {s.get('grund')}")
        url = s.get("url") or ""
        print(f"  Adresse (Pfad): {re.sub(r'^https?://[^/]+', '', url)[:200]}")
        ident = {}
        try:
            ident = get_listing_identity(url)
            print(f"  erkannt als: {ident.get('source')} / Nummer {ident.get('item_id')}")
        except ListingIdentityError as exc:
            print(f"  Adresse NICHT erkannt: {exc}")
        try:
            html = bh.seite_entpacken(s.get("seite") or "")
        except Exception as exc:  # noqa: BLE001
            print(f"  nicht entpackbar: {exc}")
            continue
        untersuchen(html, args.text)
        # was sagt unser Leser heute dazu? — und wie ist der ENTPACKTE Datenstrom aufgebaut (die Schluessel stehen im
        # rohen HTML mit Backslash-Anfuehrungszeichen, darum hier auf dem entpackten Text)
        try:
            text = bh.next_flight_text(html)
            print(f"    Flight-Text: {len(text):,} Zeichen; listing-Objekt gefunden: "
                  f"{bool(bh.mobile_listing_objekt(text, (ident or {}).get('item_id') or ''))}")
            schluessel = Counter(re.findall(r'"([A-Za-z_]{3,40})":\{', text))
            print("    Objekt-Schluessel im Datenstrom: " + ", ".join(f"{k} ({n})" for k, n in schluessel.most_common(60)))
            listen = Counter(re.findall(r'"([A-Za-z_]{3,40})":\[', text))
            print("    Listen-Schluessel im Datenstrom: " + ", ".join(f"{k} ({n})" for k, n in listen.most_common(30)))
            verweise = Counter(re.findall(r'"([A-Za-z_]{3,40})":"\$L?[0-9a-f]{1,4}"', text))
            if verweise:
                print("    Verweis-Schluessel ($-Referenzen): " + ", ".join(f"{k} ({n})" for k, n in verweise.most_common(30)))
            for wort in ("listing", "attributes", "priceRating", "make", "model", "firstRegistration", "mileage",
                         "images", "htmlDescription", "seller", "contact", "vehicle", "adId", "adDetails", "searchResult",
                         "ad", "classified", "id"):
                n = text.count(f'"{wort}"')
                if n:
                    print(f"      \"{wort}\": {n} x")
            for m in list(re.finditer(r'"listing"\s*:\s*.{0,60}', text))[:5]:
                print(f"      listing-Stelle: {m.group(0)[:80]!r}")
            for m in list(re.finditer(r'"attributes"\s*:\s*.{0,80}', text))[:3]:
                print(f"      attributes-Stelle: {m.group(0)[:100]!r}")
            # die laengsten Datenstrom-Zeilen (Kennung: Anfang) — dort steckt meist das Inserat
            zeilen = sorted(text.split("\n"), key=len, reverse=True)[:10]
            print("    laengste Datenstrom-Zeilen (Laenge, Anfang):")
            for z in zeilen:
                print(f"      {len(z):>8}  {z[:110]!r}")
        except Exception as exc:  # noqa: BLE001
            print(f"    Leser: {exc}")
    if not n:
        print("keine Leseseiten gefunden")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
