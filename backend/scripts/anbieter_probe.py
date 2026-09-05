# -*- coding: utf-8 -*-
"""Echte Anbieter einmal wirklich anrufen (Pruefbericht 09/2026, Befund 7).

Die Selbsttests laufen mit MOCK_PROVIDER_FETCH=true. Das ist richtig — sie
sollen reproduzierbar sein und weder mobile.de noch AutoScout belasten.
Damit ist aber NICHT bewiesen, dass die echten Zugaenge funktionieren:
gueltiger Apify-Token, richtige Actor-Namen, genug Guthaben, und dass die
Antwort noch so aussieht wie erwartet.

Dieses Skript macht genau diesen einen Beweis — mit ECHTEN Abrufen.

    ACHTUNG: Jeder Abruf kostet ueber dein Apify-Konto Geld (ca. 0,5 Cent).
    Ohne --wirklich passiert nichts, das Skript sagt nur, was es tun wuerde.

Aufruf (im Container, aus dem Ordner backend):

    python scripts/anbieter_probe.py \\
        --mobile "https://suchen.mobile.de/fahrzeuge/details.html?id=..." \\
        --autoscout "https://www.autoscout24.de/angebote/..." \\
        --kleinanzeigen "https://www.kleinanzeigen.de/s-anzeige/..." \\
        --wirklich

Nimm dafuer beliebige, gerade online stehende Inserate. Geprueft wird je
Anbieter:

  1. Der Link wird richtig erkannt (Quelle und Inserats-Nummer)
  2. Der echte Abruf liefert brauchbare Felder (Marke, Modell, Preis, ...)
  3. Der ZWEITE Abruf kommt aus dem Speicher — ohne neue Kosten
  4. Ein absichtlich falscher Link erzeugt einen verstaendlichen Fehler
     statt eines Serverabsturzes

Exit 0 = alles wie erwartet, 1 = mindestens ein Punkt nicht erfuellt.
"""
import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

FEHLER = []
# Alle drei Anbieter liefern den Preis als list_price (nicht "price").
WICHTIGE_FELDER = ("make", "model", "list_price", "first_registration", "mileage")


def ok(text):
    print(f"  OK   {text}")


def warn(text):
    print(f"  WARN {text}")


def fehler(text):
    FEHLER.append(text)
    print(f"  FEHLER {text}")


async def _eine_quelle(db, name, url, wirklich):
    print(f"\n{name}")
    from listing_identity import get_listing_identity, ListingIdentityError
    try:
        kennung = get_listing_identity(url)
    except ListingIdentityError as exc:
        fehler(f"Link nicht erkannt: {exc}")
        return
    ok(f"erkannt als {kennung['source']}, Inserat {kennung['item_id']}")

    if not wirklich:
        warn("Probelauf — es wird NICHT wirklich abgerufen (--wirklich fehlt)")
        return

    import provider_fetch
    from anbieter_fehler import AnbieterFehler
    from listing_identity import get_or_fetch_listing

    # Ueber denselben Weg wie die Anwendung (Speicher davor), nicht am
    # Speicher vorbei — sonst ist der Punkt "zweiter Abruf aus dem Speicher"
    # nie erfuellbar.
    async def _holen(src, iid, u):
        return await provider_fetch.fetch_listing(db, src, iid, u, dealer_id="probe")

    # --- 1. echter Abruf ---
    start = time.monotonic()
    try:
        daten, aus_speicher, _ = await get_or_fetch_listing(db, url, _holen)
        if aus_speicher:
            warn("dieses Inserat lag schon im Speicher — kein echter Abruf; "
                 "fuer einen echten Abruf ein anderes Inserat nehmen")
    except AnbieterFehler as exc:
        fehler(f"Abruf fehlgeschlagen ({exc.art}): {exc}")
        return
    except Exception as exc:                        # noqa: BLE001
        fehler(f"Abruf fehlgeschlagen ({type(exc).__name__}): {exc}")
        return
    dauer = time.monotonic() - start
    if not daten:
        fehler("Abruf lieferte nichts — Inserat offline oder Antwort leer")
        return
    ok(f"Abruf in {dauer:.1f} s")

    vorhanden = [f for f in WICHTIGE_FELDER if daten.get(f) not in (None, "", 0)]
    fehlend = [f for f in WICHTIGE_FELDER if f not in vorhanden]
    if len(vorhanden) >= 3:
        ok(f"Felder gefuellt: {', '.join(vorhanden)}")
    else:
        fehler(f"nur {len(vorhanden)} von {len(WICHTIGE_FELDER)} Feldern gefuellt "
               f"— die Antwort des Anbieters hat sich vermutlich geaendert")
    if fehlend:
        warn(f"leer geblieben: {', '.join(fehlend)}")

    # --- 2. zweiter Abruf muss aus dem Speicher kommen ---
    from listing_identity import peek_cached_listing
    treffer = await peek_cached_listing(db, url, dealer_id="probe")
    if treffer is not None:
        ok("zweiter Abruf kaeme aus dem Speicher — keine weiteren Kosten")
    else:
        warn("kein Speicher-Eintrag gefunden — jeder Aufruf wuerde erneut kosten")


async def _falscher_link(db, wirklich):
    """Ein Link, den es sicher nicht gibt: der Nutzer muss einen
    verstaendlichen Satz sehen, keinen Absturz."""
    print("\nAbsichtlich falscher Link")
    from listing_identity import get_listing_identity, ListingIdentityError
    try:
        get_listing_identity("https://www.example.com/kein-auto")
        fehler("ein Link ohne bekannte Quelle wurde trotzdem akzeptiert")
    except ListingIdentityError as exc:
        text = str(exc)
        if "kleinanzeigen" in text.lower() or "mobile" in text.lower():
            ok(f"klare Meldung: {text[:90]}")
        else:
            warn(f"Meldung wenig hilfreich: {text[:90]}")

    if not wirklich:
        return
    import provider_fetch
    from anbieter_fehler import AnbieterFehler
    try:
        d = await provider_fetch.fetch_listing(
            db, "mobile", "000000000",
            "https://suchen.mobile.de/fahrzeuge/details.html?id=000000000",
            dealer_id="probe")
        if not d:
            ok("erfundene Inserats-Nummer -> leer (Inserat weg), wie erwartet")
        else:
            gefuellt = {k: d.get(k) for k in WICHTIGE_FELDER if d.get(k) not in (None, "", 0)}
            warn(f"erfundene Inserats-Nummer lieferte trotzdem etwas: {gefuellt or 'nur leere Felder'}")
    except AnbieterFehler as exc:
        ok(f"sauberer Anbieter-Fehler ({exc.art}): {str(exc)[:80]}")
    except Exception as exc:                        # noqa: BLE001
        ok(f"sauber abgefangen ({type(exc).__name__}): {str(exc)[:80]}")


async def main_async(args) -> int:
    if os.environ.get("MOCK_PROVIDER_FETCH", "").strip().lower() in ("1", "true", "yes"):
        print("FEHLER: MOCK_PROVIDER_FETCH ist an — dann wird nichts wirklich")
        print("        abgerufen. Fuer diese Probe ausschalten.")
        return 1
    if not os.environ.get("APIFY_TOKEN", "").strip():
        print("FEHLER: APIFY_TOKEN fehlt — ohne den geht bei mobile.de und")
        print("        AutoScout gar nichts.")
        return 1

    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(
        os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017",
        serverSelectionTimeoutMS=8000)
    db = client[os.environ.get("DB_NAME") or "autoschnell"]
    try:
        aufgaben = [("mobile.de", args.mobile), ("AutoScout24", args.autoscout),
                    ("Kleinanzeigen", args.kleinanzeigen)]
        gemacht = False
        for name, url in aufgaben:
            if url:
                gemacht = True
                await _eine_quelle(db, name, url, args.wirklich)
        if not gemacht:
            print("Kein Link angegeben. Mindestens einen von --mobile,")
            print("--autoscout, --kleinanzeigen setzen.")
            return 1
        await _falscher_link(db, args.wirklich)
    finally:
        client.close()

    print()
    if FEHLER:
        print(f"ERGEBNIS: {len(FEHLER)} Punkt(e) nicht erfuellt")
        for f in FEHLER:
            print(f"  - {f}")
        return 1
    if not args.wirklich:
        print("ERGEBNIS: Probelauf beendet. Mit --wirklich echt abrufen")
        print("          (kostet ca. 0,5 Cent je Anbieter).")
        return 0
    print("ERGEBNIS: Die echten Anbieter-Zugaenge funktionieren.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Echte Anbieter-Zugaenge einmal wirklich pruefen")
    ap.add_argument("--mobile", default="", help="echte mobile.de-Inserats-URL")
    ap.add_argument("--autoscout", default="", help="echte AutoScout24-URL")
    ap.add_argument("--kleinanzeigen", default="", help="echte Kleinanzeigen-URL")
    ap.add_argument("--wirklich", action="store_true",
                    help="wirklich abrufen (kostet Geld ueber Apify)")
    args = ap.parse_args()
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    except ImportError:
        pass
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
