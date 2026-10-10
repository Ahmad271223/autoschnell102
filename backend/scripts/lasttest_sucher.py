# -*- coding: utf-8 -*-
"""EINE Firma, EIN Chef, N Sucher — der Test, der bisher fehlte.

Warum (Befund Ahmad 12.09.2026, Punkt 13): Die vorhandenen Lasttests legen
fuer jeden Nutzer eine EIGENE Firma an (lasttest.py: "Lasttest {i}") oder
verteilen die Last auf 2-3 geteilte Konten (lasttest_stoss.py). Genau der
Fall, auf den es ankommt — VIELE Sucher DERSELBEN Firma gleichzeitig —
wurde nie belastet, und die Trennung der Konten unter Last nie geprueft.

Geprueft wird nach jedem Lauf automatisch:
  * 0 fremde Daten     — kein Sucher sieht ein Fahrzeug, einen Vertrag oder
                         einen Termin eines Kollegen; keine Kollegen-Namen
                         und keine Konto-Kennungen in seinen Antworten
  * 0 fremde Limits    — das Fahrzeug-Limit eines Suchers verdraengt keine
                         Fahrzeuge eines anderen
  * 0 Dubletten        — je (Firma, Fahrzeug) genau ein Datensatz, je Vertrag
                         genau ein Kaufvorgang
  * 0 Serverfehler     — keine Antwort mit 5xx
Dazu die Laufzeiten je Weg (p50/p95/p99) und die Zahl echter Anbieter-Abrufe.

Zwei Betriebsarten:

  1) Konten selbst anlegen (nur lokal/Staging). Kontonummer (13.09.2026):
     Konten legt nur der Super-Admin an. Das Skript legt dafuer einen
     Wegwerf-Super-Admin in der Datenbank --db-name an und loescht ihn nach
     der Anlage wieder. Ohne --db-name bricht es ab — KEIN Rueckfall auf den
     Betreiber-Zugang (SUPER_ADMIN_*): sonst entstuenden bei einem Aufruf
     gegen Produktion dauerhafte Konten mit fortlaufenden Nummern, und die
     Sitzung des Betreibers endete. Vor der Anlage der Sucher prueft ein
     Vergleich des Chefs den Mock-Modus (wie lasttest.py). Das Passwort ist je
     Lauf zufaellig (--passwort setzt es bewusst); nach jedem Lauf werden
     Firma, Konten, Abos und Fahrzeuge der Lauf-Firma wieder geloescht:
       python -X utf8 scripts/lasttest_sucher.py --sucher 30 --db-name autoschnell_last
       python -X utf8 scripts/lasttest_sucher.py --sucher 100 --stufen 30,50,100 --db-name autoschnell_last

  2) VORHANDENE Konten benutzen (z.B. die echten Testkonten einer Firma):
       python -X utf8 scripts/lasttest_sucher.py --konten konten.json
     konten.json (Anmeldung per Kontonummer):
       {"basis": "https://app.auto-schnellkauf.de",
        "chef": {"kontonummer": "10023", "passwort": "..."},
        "sucher": [{"kontonummer": "10023-2", "passwort": "..."}, ...]}
     Alte Dateien mit "email" statt "kontonummer" werden noch gelesen — eine
     Anmeldung per E-Mail gibt es aber nicht mehr (401); Nummern eintragen.

Ohne --echte-abrufe verlangt das Skript ein Backend mit
MOCK_PROVIDER_FETCH=true und ruft KEINE echten Anbieter an. Mit
--echte-abrufe werden echte Inserate geholt (und echte Daten angelegt) —
das ist eine bewusste Entscheidung und wird im Bericht vermerkt.
"""
import argparse
import asyncio
import json
import os
import secrets
import statistics
import sys
import time
import uuid
from datetime import datetime, timezone

import aiohttp

import lasttest_konten as LK

STANDARD_BASIS = (os.environ.get("TEST_BASE_URL") or "http://localhost:8002").rstrip("/")

# Echte, am 12.09.2026 aktive Kleinanzeigen-Inserate. Nur fuer --echte-abrufe;
# im Mock-Betrieb werden synthetische Nummern benutzt.
ECHTE_INSERATE = [
    "https://www.kleinanzeigen.de/s-anzeige/mercedes-v300/3364685616-216-3545",
    "https://www.kleinanzeigen.de/s-anzeige/citroen-2cv/3240146581-216-1202",
    "https://www.kleinanzeigen.de/s-anzeige/vw-caddy/3458821471-216-3166",
    "https://www.kleinanzeigen.de/s-anzeige/hyundai-tucson/3483347763-216-1",
    "https://www.kleinanzeigen.de/s-anzeige/renault-kangoo/3481916346-216-1",
    "https://www.kleinanzeigen.de/s-anzeige/hyundai-tucson-2/3473537342-216-1",
    "https://www.kleinanzeigen.de/s-anzeige/hyundai-tucson-3/3478459067-216-1",
    "https://www.kleinanzeigen.de/s-anzeige/bmw-118/3485378267-216-1",
]


def jetzt():
    return datetime.now(timezone.utc).isoformat()


class Messung:
    """Laufzeiten je Weg + Fehler, ohne Fremdbibliothek."""

    def __init__(self):
        self.zeiten: dict = {}
        self.fehler: list = []
        self.serverfehler: list = []

    def dazu(self, weg: str, dauer: float, code: int, text: str = ""):
        self.zeiten.setdefault(weg, []).append(dauer)
        if code >= 500:
            self.serverfehler.append((weg, code, text[:200]))
        elif code >= 400:
            self.fehler.append((weg, code, text[:200]))

    def bericht(self) -> dict:
        raus = {}
        for weg, werte in sorted(self.zeiten.items()):
            w = sorted(werte)
            raus[weg] = {
                "anzahl": len(w),
                "p50": round(w[len(w) // 2], 3),
                "p95": round(w[min(len(w) - 1, int(len(w) * 0.95))], 3),
                "p99": round(w[min(len(w) - 1, int(len(w) * 0.99))], 3),
                "max": round(w[-1], 3),
            }
        return raus


class Klient:
    def __init__(self, sitzung: aiohttp.ClientSession, basis: str, messung: Messung):
        self.s = sitzung
        self.api = f"{basis}/api"
        self.m = messung

    async def ruf(self, methode: str, pfad: str, *, token=None, daten=None,
                 weg=None, erlaubt=(200,)):
        kopf = {"Accept": "application/json"}
        if token:
            kopf["Authorization"] = f"Bearer {token}"
        t0 = time.perf_counter()
        try:
            async with self.s.request(methode, f"{self.api}{pfad}", headers=kopf,
                                      json=daten) as a:
                text = await a.text()
                dauer = time.perf_counter() - t0
                self.m.dazu(weg or f"{methode} {pfad.split('?')[0]}", dauer,
                            a.status, text)
                try:
                    inhalt = json.loads(text) if text else None
                except json.JSONDecodeError:
                    inhalt = text
                return a.status, inhalt
        except Exception as exc:  # noqa: BLE001
            dauer = time.perf_counter() - t0
            self.m.dazu(weg or f"{methode} {pfad}", dauer, 599, str(exc))
            return 599, str(exc)


def kennung(konto: dict) -> str:
    """Kontonummer (13.09.2026) des Eintrags; alte konten.json-Dateien mit
    "email" werden noch gelesen (die Anmeldung damit ergibt 401)."""
    return str(konto.get("kontonummer") or konto.get("email") or "").strip()


async def anmelden(k: Klient, kontonummer: str, passwort: str, fahrer=False):
    pfad = "/driver/login" if fahrer else "/auth/login"
    code, daten = await k.ruf("POST", pfad,
                              daten={"kontonummer": kontonummer, "password": passwort},
                              weg="anmelden")
    if code != 200 or not isinstance(daten, dict):
        return None, f"{code}: {str(daten)[:160]}"
    return daten.get("token"), None


# ------------------------------------------------------------- Aufbau
def _sa_loeschen(args, ids):
    from pymongo import MongoClient
    c = MongoClient(args.mongo, serverSelectionTimeoutMS=5000)
    try:
        LK.konten_loeschen(c[args.db_name], ids)
    finally:
        c.close()


async def super_admin_fuer_anlage(k: Klient, args):
    """Kontonummer (13.09.2026): Token eines Wegwerf-Super-Admins fuer die
    Anlage (Plan, Risiko 7). -> (Kopfzeilen, ids des Wegwerf-Kontos).

    Nur mit --db-name. KEIN Rueckfall auf SUPER_ADMIN_USERNAME/PASSWORD: der
    Betreiber-Zugang funktioniert gegen jede --basis, auch gegen Produktion —
    dort entstuenden dauerhafte Konten mit fortlaufenden Nummern, und seine
    offene Sitzung endete."""
    if not args.db_name:
        raise SystemExit("Konten anlegen braucht --db-name (Wegwerf-Super-Admin in der "
                         "Datenbank des Test-Backends, nur lokal/Staging). Fuer vorhandene "
                         "Konten --konten benutzen.")
    from pymongo import MongoClient
    ids = LK.neue_ids()
    c = MongoClient(args.mongo, serverSelectionTimeoutMS=5000)
    try:
        sa = LK.wegwerf_super_admin(c[args.db_name], uuid.uuid4().hex[:8], ids)
    finally:
        c.close()
    try:
        return await LK.super_admin_anmelden(k.s, k.api, sa), ids
    except BaseException:
        _sa_loeschen(args, ids)
        raise


def lauf_passwort() -> str:
    """Kontonummer (13.09.2026): Passwort der angelegten Konten — je Lauf
    zufaellig statt eines festen Werts im Repository."""
    return f"Lt-{secrets.token_urlsafe(12)}!7"


async def chef_anlegen(k: Klient, passwort: str, admin_h: dict, ids: dict):
    """Firma + Chef ueber den Super-Admin (IDs landen in `ids`); der Chef
    meldet sich einmal per Kontonummer an. -> (chef, dealer_id)"""
    s = uuid.uuid4().hex[:8]
    code, daten = await LK.firma_anlegen(
        k.s, k.api, admin_h, ids, passwort, f"Lasttest Firma {s}", kontakt="Chef",
        telefon="0511 1", email=f"lt_chef_{s}@e2etest-mail.de")
    if code != 200:
        raise SystemExit(f"Firma anlegen fehlgeschlagen ({code}): {str(daten)[:200]}")
    chef = {"kontonummer": daten["kontonummer"], "passwort": passwort, "id": daten["user_id"]}
    chef["token"], fehler = await anmelden(k, chef["kontonummer"], passwort)
    if not chef["token"]:
        raise SystemExit(f"Chef-Anmeldung fehlgeschlagen: {fehler}")
    return chef, daten["dealer_id"]


async def sucher_anlegen(k: Klient, anzahl: int, passwort: str, admin_h: dict,
                         ids: dict, dealer_id: str) -> list:
    sucher = []
    for i in range(anzahl):
        code, d = await LK.sucher_anlegen(k.s, k.api, admin_h, ids, dealer_id,
                                          passwort, "Sucher", f"{i}")
        if code != 200:
            raise SystemExit(f"Sucher {i} anlegen fehlgeschlagen ({code}): {str(d)[:200]}")
        sucher.append({"kontonummer": d["kontonummer"], "passwort": passwort,
                       "id": d.get("sucher_id")})
    return sucher


MOCK_PROBE_URL = "https://www.kleinanzeigen.de/s-anzeige/lasttest-probe/95999999-216-1"


async def mock_pruefen(k: Klient, token: str):
    """Kontonummer (13.09.2026): VOR der Anlage der Sucher muss ein Vergleich
    des Chefs als synthetisch markiert sein (vehicle._mock, wie lasttest.py).
    /health allein sagt nichts ueber den Mock — ein Backend ohne
    MOCK_PROVIDER_FETCH (z.B. Produktion, production_check verbietet ihn dort)
    bekommt so keine N Konten und keine echten Abrufe."""
    code, daten = await k.ruf("POST", "/mobile/compare", token=token,
                              daten=_vergleich_rumpf(MOCK_PROBE_URL), weg="mock-probe")
    fahrzeug = (daten.get("vehicle") or {}) if isinstance(daten, dict) else {}
    if code != 200 or not fahrzeug.get("_mock"):
        raise SystemExit(
            f"ABBRUCH: Das Backend laeuft NICHT im Mock-Modus (MOCK_PROVIDER_FETCH=true; "
            f"Probe gab {code}). Ohne ihn loeste der Lasttest echte Abrufe aus — "
            "bewusst nur mit --echte-abrufe.")


def aufraeumen(args, angelegt: dict) -> dict:
    """Kontonummer (13.09.2026): alles entfernen, was dieser Lauf angelegt hat —
    Fahrzeuge und Vergleiche der Lauf-Firma, dann Konten, Firma und Abos genau
    ueber die gesammelten IDs (LK.konten_loeschen). So bleiben keine Konten
    mit bekanntem Passwort liegen."""
    if not args.db_name or not any(angelegt.values()):
        return {}
    from pymongo import MongoClient
    c = MongoClient(args.mongo, serverSelectionTimeoutMS=5000)
    try:
        dbx = c[args.db_name]
        n = {}
        if angelegt["dealers"]:
            for sammlung in ("vehicle_comparisons", "vehicles"):
                n[sammlung] = dbx[sammlung].delete_many(
                    {"dealer_id": {"$in": angelegt["dealers"]}}).deleted_count
        n.update(LK.konten_loeschen(dbx, angelegt))
        return n
    finally:
        c.close()


def abos_seeden(mongo_url: str, db_name: str, dealer_id: str, nutzer: list) -> int:
    """Abos direkt in die Datenbank schreiben — NUR fuer selbst angelegte
    Konten in einer Wegwerf-Datenbank. Ohne Abo lehnt der Vergleich mit 402
    ab und der ganze Lauf misst nichts. Echte Konten (--konten) haben ihr
    Abo ohnehin."""
    from pymongo import MongoClient
    from datetime import timedelta
    c = MongoClient(mongo_url, serverSelectionTimeoutMS=5000)
    db = c[db_name]
    bis = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    n = 0
    for uid in nutzer:
        if not uid:
            continue
        db.subscriptions.insert_one({
            "id": str(uuid.uuid4()), "dealer_id": dealer_id,
            "subject_user_id": uid, "plan": "monthly", "status": "active",
            "expires_at": bis, "created_at": jetzt()})
        n += 1
    c.close()
    return n


async def alle_anmelden(k: Klient, konten: dict):
    """Jeder bekommt ein EIGENES Token — die Einmal-Sitzungs-Regel verbietet
    das Teilen, und genau darum geht es hier."""
    ohne_nummer = [kennung(x) for x in [konten["chef"]] + konten["sucher"]
                   if not x.get("kontonummer")]
    if ohne_nummer:
        print(f"!! {len(ohne_nummer)} Eintraege ohne 'kontonummer' (z.B. {ohne_nummer[0]!r}) — "
              "die Anmeldung per E-Mail gibt es nicht mehr, Kontonummern eintragen.")
    if not konten["chef"].get("token"):
        tok, fehler = await anmelden(k, kennung(konten["chef"]), konten["chef"]["passwort"])
        if not tok:
            raise SystemExit(f"Chef-Anmeldung fehlgeschlagen: {fehler}")
        konten["chef"]["token"] = tok
    ergebnis = await asyncio.gather(*[
        anmelden(k, kennung(su), su["passwort"]) for su in konten["sucher"]])
    gescheitert = []
    for su, (tok, fehler) in zip(konten["sucher"], ergebnis):
        su["token"] = tok
        if not tok:
            gescheitert.append(f"{kennung(su)}: {fehler}")
    return gescheitert


# Wird von lauf() gesetzt: Bei echten Abrufen bittet der Server sonst die
# Browser-Erweiterung (CLIENT_FETCH_KLEINANZEIGEN) und legt nichts an.
OHNE_ERWEITERUNG = False


def _vergleich_rumpf(url: str) -> dict:
    rumpf = {"url": url}
    if OHNE_ERWEITERUNG:
        rumpf["ohne_erweiterung"] = True
    return rumpf


# ------------------------------------------------------------ Szenarien
async def szenario_gleiches_auto(k: Klient, sucher: list, url: str):
    """ALLE vergleichen im selben Moment DASSELBE Auto."""
    tor = asyncio.Event()

    async def einer(su):
        await tor.wait()
        code, daten = await k.ruf("POST", "/mobile/compare", token=su["token"],
                                  daten=_vergleich_rumpf(url),
                                  weg="vergleich (gleiches Auto)")
        return su, code, daten

    aufgaben = [asyncio.create_task(einer(su)) for su in sucher]
    await asyncio.sleep(0.2)
    tor.set()
    return await asyncio.gather(*aufgaben)


async def szenario_eigene_autos(k: Klient, sucher: list, urls: list):
    """Jeder vergleicht ein ANDERES Auto — danach darf keiner das des
    anderen sehen."""
    async def einer(i, su):
        url = urls[i % len(urls)]
        code, daten = await k.ruf("POST", "/mobile/compare", token=su["token"],
                                  daten=_vergleich_rumpf(url),
                                  weg="vergleich (eigenes Auto)")
        return su, url, code, daten

    return await asyncio.gather(*[einer(i, su) for i, su in enumerate(sucher)])


async def szenario_chef_liest(k: Klient, chef_token: str, runden: int = 5):
    for _ in range(runden):
        await asyncio.gather(
            k.ruf("GET", "/bestand", token=chef_token, weg="chef /bestand"),
            k.ruf("GET", "/vehicles", token=chef_token, weg="chef /vehicles"),
            k.ruf("GET", "/dealer/sucher", token=chef_token, weg="chef /dealer/sucher"),
        )


# ------------------------------------------------------------ Pruefungen
async def pruefe_trennung(k: Klient, sucher: list, eigene: dict) -> list:
    """Sieht ein Sucher Daten eines Kollegen? `eigene` = {kontonummer: {ids}}."""
    verstoesse = []
    KONTO_FELDER = ("owner_user_id", "mitbearbeiter_ids", "uebernommen_von")

    async def einer(su):
        wer = kennung(su)
        meine = eigene.get(wer, set())
        code, fahrzeuge = await k.ruf("GET", "/vehicles", token=su["token"],
                                      weg="sucher /vehicles")
        if code != 200 or not isinstance(fahrzeuge, list):
            return
        for f in fahrzeuge:
            fid = f.get("id")
            if fid and meine and fid not in meine:
                fremd = [e for e, ids in eigene.items() if e != wer and fid in ids]
                if fremd:
                    verstoesse.append(
                        f"FREMDES FAHRZEUG: {wer} sieht {fid} von {fremd[0]}")
            for feld in KONTO_FELDER:
                if feld in f:
                    verstoesse.append(
                        f"KONTO-KENNUNG: {wer} bekommt '{feld}' in /vehicles")
            for feld in ("owner_name", "mitbearbeiter_namen"):
                if f.get(feld):
                    verstoesse.append(
                        f"KOLLEGENNAME: {wer} bekommt '{feld}' = {f[feld]}")
        code, bestand = await k.ruf("GET", "/bestand", token=su["token"],
                                    weg="sucher /bestand")
        if code == 200 and isinstance(bestand, dict):
            for f in bestand.get("items", []):
                for feld in KONTO_FELDER:
                    if feld in f:
                        verstoesse.append(
                            f"KONTO-KENNUNG: {wer} bekommt '{feld}' in /bestand")
        code, vertraege = await k.ruf("GET", "/contracts", token=su["token"],
                                      weg="sucher /contracts")
        if code == 200 and isinstance(vertraege, list):
            for v in vertraege:
                if v.get("user_id") and v["user_id"] != su.get("id"):
                    verstoesse.append(
                        f"FREMDER VERTRAG: {wer} sieht Vertrag von {v['user_id']}")

    await asyncio.gather(*[einer(su) for su in sucher])
    return verstoesse


async def pruefe_dubletten(mongo_url: str, db_name: str, dealer_id: str) -> list:
    """Je (Firma, Fahrzeug) genau EIN Datensatz, je Vertrag EIN Kaufvorgang."""
    try:
        from pymongo import MongoClient
    except ImportError:
        return ["(pymongo fehlt — Dublettenpruefung uebersprungen)"]
    raus = []
    c = MongoClient(mongo_url, serverSelectionTimeoutMS=5000)
    db = c[db_name]
    for sammlung, schluessel in (("vehicles", "id"), ("kaufvorgaenge", "contract_id")):
        doppelt = list(db[sammlung].aggregate([
            {"$match": {"dealer_id": dealer_id, schluessel: {"$ne": None}}},
            {"$group": {"_id": f"${schluessel}", "n": {"$sum": 1}}},
            {"$match": {"n": {"$gt": 1}}}, {"$limit": 5}]))
        for d in doppelt:
            raus.append(f"DUBLETTE in {sammlung}: {schluessel}={d['_id']} kommt {d['n']}x vor")
    c.close()
    return raus


async def pruefe_dubletten_http(k: Klient, chef_token: str) -> list:
    """Dieselbe Pruefung ohne Datenbankzugang (Produktion): Der Chef sieht
    alle Fahrzeuge der Firma — dort darf jede Fahrzeug-ID genau einmal
    stehen und je (Konto, Inserat) genau ein Datensatz existieren.

    Runde 32: ueber /vehicles, nicht /bestand. Der Bestand zeigt seitdem nur
    Autos mit Kaufvertrag — die verglichenen Autos dieses Laufs stehen dort
    gar nicht, die Pruefung waere blind gewesen."""
    code, fahrzeuge = await k.ruf("GET", "/vehicles", token=chef_token,
                                  weg="chef /vehicles (Dubletten)")
    if code != 200 or not isinstance(fahrzeuge, list):
        return [f"(Dublettenpruefung nicht moeglich — /vehicles gab {code})"]
    raus, ids, paare = [], {}, {}
    for f in fahrzeuge:
        fid = f.get("id")
        if fid:
            ids[fid] = ids.get(fid, 0) + 1
        schluessel = (f.get("owner_user_id"), f.get("mobile_ad_id"))
        if schluessel[0] and schluessel[1]:
            paare[schluessel] = paare.get(schluessel, 0) + 1
    for fid, n in ids.items():
        if n > 1:
            raus.append(f"DUBLETTE: Fahrzeug-ID {fid} kommt {n}x vor")
    for (konto, inserat), n in paare.items():
        if n > 1:
            raus.append(f"DUBLETTE: Konto {konto} hat Inserat {inserat} {n}x")
    if not fahrzeuge:
        raus.append("(Hinweis: /vehicles lieferte keine Fahrzeuge — nichts geprueft)")
    return raus


# ---------------------------------------------------------------- Lauf
async def lauf(args, angelegt: dict):
    """`angelegt` sammelt die IDs selbst angelegter Konten; main() raeumt sie
    nach dem Lauf ab — auch bei einem Abbruch."""
    global OHNE_ERWEITERUNG
    OHNE_ERWEITERUNG = bool(args.echte_abrufe) and not args.mit_erweiterung
    grenze = aiohttp.TCPConnector(limit=max(50, args.sucher * 2))
    zeit = aiohttp.ClientTimeout(total=args.timeout)
    messung = Messung()
    async with aiohttp.ClientSession(connector=grenze, timeout=zeit) as sitzung:
        k = Klient(sitzung, args.basis, messung)

        # Mock-Pflicht, solange nicht ausdruecklich echte Abrufe gewollt sind
        if not args.echte_abrufe:
            code, daten = await k.ruf("GET", "/health", weg="health")
            if code != 200:
                raise SystemExit(f"Backend nicht erreichbar ({args.basis}): {code}")

        if args.konten:
            konten = json.loads(open(args.konten, encoding="utf-8").read())
            konten.setdefault("chef", {}).setdefault("token", None)
            args.basis = (konten.get("basis") or args.basis).rstrip("/")
            k.api = f"{args.basis}/api"
            print(f"Vorhandene Konten: 1 Chef + {len(konten['sucher'])} Sucher "
                  f"gegen {args.basis}")
        else:
            print(f"Lege Firma mit {args.sucher} Suchern an ...")
            admin_h, sa_ids = await super_admin_fuer_anlage(k, args)
            try:
                chef, dealer_id = await chef_anlegen(k, args.passwort, admin_h, angelegt)
                n = abos_seeden(args.mongo, args.db_name, dealer_id, [chef["id"]])
                if not args.echte_abrufe:
                    await mock_pruefen(k, chef["token"])
                    print("     Mock-Modus bestaetigt — keine echten Anbieter-Abrufe.")
                sucher_liste = await sucher_anlegen(k, args.sucher, args.passwort,
                                                    admin_h, angelegt, dealer_id)
            finally:
                _sa_loeschen(args, sa_ids)   # Wegwerf-Super-Admin nur fuer die Anlage
            n += abos_seeden(args.mongo, args.db_name, dealer_id,
                             [su.get("id") for su in sucher_liste])
            print(f"     {n} Abos gesetzt (nur fuer diesen Lauf, wird danach geloescht)")
            konten = {"chef": chef, "sucher": sucher_liste}

        gescheitert = await alle_anmelden(k, konten)
        if gescheitert:
            print(f"!! {len(gescheitert)} Anmeldungen fehlgeschlagen:")
            for g in gescheitert[:5]:
                print("   ", g)
        sucher = [su for su in konten["sucher"] if su.get("token")]
        if not sucher:
            raise SystemExit("Kein Sucher angemeldet — Abbruch.")
        print(f"Angemeldet: {len(sucher)} Sucher + Chef")

        urls = (ECHTE_INSERATE if args.echte_abrufe else
                [f"https://www.kleinanzeigen.de/s-anzeige/lasttest/"
                 f"{96_000_000 + i}-216-1" for i in range(max(8, len(sucher)))])

        # --- Szenario 1: alle gleichzeitig dasselbe Auto ---
        print(f"1/4  {len(sucher)} Sucher vergleichen GLEICHZEITIG dasselbe Auto ...")
        t0 = time.perf_counter()
        gleiche = await szenario_gleiches_auto(k, sucher, urls[0])
        print(f"     {time.perf_counter() - t0:.1f}s")

        # --- Szenario 2: jeder ein eigenes Auto, Chef liest parallel mit ---
        print(f"2/4  jeder ein eigenes Auto, Chef liest gleichzeitig mit ...")
        t0 = time.perf_counter()
        eigene_roh, _ = await asyncio.gather(
            szenario_eigene_autos(k, sucher, urls[1:] or urls),
            szenario_chef_liest(k, konten["chef"]["token"]))
        print(f"     {time.perf_counter() - t0:.1f}s")

        # Bat der Server um die Browser-Erweiterung? Dann wurde NICHTS
        # abgerufen und der Lauf hat die Vergleichswege gar nicht belastet.
        gebeten = sum(1 for _s, c, d in gleiche
                      if c == 200 and isinstance(d, dict) and d.get("needs_client_fetch"))
        gebeten += sum(1 for _s, _u, c, d in eigene_roh
                       if c == 200 and isinstance(d, dict) and d.get("needs_client_fetch"))
        if gebeten:
            print(f"!! {gebeten} Vergleiche wurden an die Browser-Erweiterung "
                  "verwiesen (needs_client_fetch) — es wurde NICHTS abgerufen.")

        # Wem gehoert was?
        eigene: dict = {}
        for su, url, code, daten in eigene_roh:
            if code == 200 and isinstance(daten, dict) and daten.get("vehicle_id"):
                eigene.setdefault(kennung(su), set()).add(daten["vehicle_id"])
        for su, code, daten in gleiche:
            if code == 200 and isinstance(daten, dict) and daten.get("vehicle_id"):
                # Das gemeinsame Auto gehoert allen, die es verglichen haben.
                eigene.setdefault(kennung(su), set()).add(daten["vehicle_id"])

        # --- Szenario 3: Trennung pruefen ---
        print("3/4  pruefe Trennung der Konten ...")
        gemeinsam = {d.get("vehicle_id") for _s, c, d in gleiche
                     if c == 200 and isinstance(d, dict)}
        trenn_basis = {e: ids - gemeinsam for e, ids in eigene.items()}
        verstoesse = await pruefe_trennung(k, sucher, trenn_basis)

        # --- Szenario 4: Dubletten in der Datenbank ---
        print("4/4  pruefe Dubletten ...")
        dubletten = []
        if args.db_name:
            code, me = await k.ruf("GET", "/auth/me", token=konten["chef"]["token"],
                                   weg="auth/me")
            dealer_id = (me or {}).get("user", {}).get("dealer_id") if isinstance(me, dict) else None
            if dealer_id:
                dubletten = await pruefe_dubletten(args.mongo, args.db_name, dealer_id)
        else:
            dubletten = await pruefe_dubletten_http(k, konten["chef"]["token"])

    return {
        "basis": args.basis,
        "sucher": len(sucher),
        "echte_abrufe": bool(args.echte_abrufe),
        "zeiten": messung.bericht(),
        "serverfehler": messung.serverfehler,
        "fehler_4xx": messung.fehler[:20],
        "verstoesse_trennung": verstoesse,
        "dubletten": dubletten,
        "fahrzeuge_je_sucher": {e: len(ids) for e, ids in sorted(eigene.items())},
        "gebeten_um_erweiterung": gebeten,
        "mit_erweiterung": bool(args.mit_erweiterung),
        "zeitpunkt": jetzt(),
    }


def bewerten(b: dict) -> int:
    print()
    print("=" * 72)
    print(f"ERGEBNIS — {b['sucher']} Sucher einer Firma gegen {b['basis']}")
    print(f"echte Anbieter-Abrufe: {'JA' if b['echte_abrufe'] else 'nein (Mock)'}")
    print("=" * 72)
    print("\nLaufzeiten (Sekunden):")
    for weg, w in b["zeiten"].items():
        print(f"  {weg:34s} n={w['anzahl']:4d}  p50={w['p50']:6.2f}  "
              f"p95={w['p95']:6.2f}  p99={w['p99']:6.2f}  max={w['max']:6.2f}")
    fehler = 0
    print()
    for titel, liste in (("Serverfehler (5xx)", b["serverfehler"]),
                         ("Verstoesse gegen die Trennung", b["verstoesse_trennung"]),
                         ("Dubletten", [d for d in b["dubletten"] if d.startswith("DUBLETTE")])):
        if liste:
            fehler += len(liste)
            print(f"!! {titel}: {len(liste)}")
            for z in liste[:10]:
                print("     ", z)
        else:
            print(f"OK {titel}: 0")
    gebeten = b.get("gebeten_um_erweiterung") or 0
    if gebeten and not b.get("mit_erweiterung"):
        fehler += 1
        print(f"!! {gebeten} Vergleiche an die Browser-Erweiterung verwiesen — "
              "der Lauf hat die Abrufwege NICHT belastet")
    elif b["echte_abrufe"]:
        print("OK An die Erweiterung verwiesen: 0")
    for hinweis in [d for d in b["dubletten"] if not d.startswith("DUBLETTE")]:
        print(f"   {hinweis}")
    if b["fehler_4xx"]:
        print(f"   (Hinweis: {len(b['fehler_4xx'])} Antworten mit 4xx — "
              "erwartet z.B. bei Limits/Abos)")
        for z in b["fehler_4xx"][:5]:
            print("     ", z)
    print(f"\nFahrzeuge je Sucher: "
          f"{sorted(b['fahrzeuge_je_sucher'].values()) if b['fahrzeuge_je_sucher'] else '-'}")
    print("\n" + ("BESTANDEN" if fehler == 0 else f"DURCHGEFALLEN ({fehler} Punkte)"))
    return 0 if fehler == 0 else 1


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--sucher", type=int, default=30, help="Anzahl Sucher (Standard 30)")
    p.add_argument("--stufen", help="mehrere Laeufe, z.B. 30,50,100")
    p.add_argument("--konten", help="JSON mit vorhandenen Konten (siehe Kopf)")
    p.add_argument("--basis", default=STANDARD_BASIS, help="Adresse des Backends")
    p.add_argument("--passwort", default=None,
                   help="Passwort der angelegten Konten (Standard: je Lauf zufaellig)")
    p.add_argument("--timeout", type=float, default=120.0)
    p.add_argument("--mongo", default=os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017")
    p.add_argument("--db-name", default=os.environ.get("DB_NAME"),
                   help="Datenbank des Test-Backends (nur lokal/Staging): Pflicht fuer "
                        "die Kontenanlage, dazu Dublettenpruefung")
    p.add_argument("--mit-erweiterung", action="store_true",
                   help="NICHT ohne_erweiterung senden — dann verweist ein "
                        "Server im Client-Abruf-Modus auf die Erweiterung "
                        "und es wird nichts abgerufen")
    p.add_argument("--echte-abrufe", action="store_true",
                   help="ECHTE Inserate abrufen und ECHTE Daten anlegen")
    p.add_argument("--bericht", help="Ergebnis zusaetzlich als JSON hier ablegen")
    args = p.parse_args()

    if not args.passwort:
        args.passwort = lauf_passwort()
    stufen = ([int(x) for x in args.stufen.split(",")] if args.stufen else [args.sucher])
    schlecht = 0
    for n in stufen:
        args.sucher = n
        angelegt = LK.neue_ids()
        try:
            bericht = asyncio.run(lauf(args, angelegt))
        finally:
            # Kontonummer (13.09.2026): selbst angelegte Konten nie liegen lassen
            if any(angelegt.values()):
                try:
                    print(f"Aufgeraeumt: {aufraeumen(args, angelegt)}")
                except Exception as exc:  # noqa: BLE001
                    print(f"!! Aufraeumen fehlgeschlagen ({exc}) — angelegte IDs: {angelegt}")
        schlecht += bewerten(bericht)
        if args.bericht:
            pfad = args.bericht if len(stufen) == 1 else f"{args.bericht}.{n}"
            with open(pfad, "w", encoding="utf-8") as f:
                json.dump(bericht, f, ensure_ascii=False, indent=2)
            print(f"Bericht: {pfad}")
    sys.exit(1 if schlecht else 0)


if __name__ == "__main__":
    main()
