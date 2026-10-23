# -*- coding: utf-8 -*-
"""Systemcheck (Wunsch Ahmad 12.10.2026: "suche nach kritischen Punkten im System und bei den Benutzern — schau in
den Speicher, Nutzer-Aktivitaeten, Handlungen im System-Inneren").

NUR LESEN. Gibt Zahlen, Fehlerarten und Zustaende aus — keine Namen, keine Adressen, keine Schluessel. Konten
erscheinen nur als Kontonummer (z. B. 10007-1), Firmen als Kundennummer.

Aufruf auf dem Server (im Backend-Container, dort gelten MONGO_URL/DB_NAME):
    cd /opt/autoschnell && docker compose exec -T backend python scripts/systemcheck.py            # letzte 7 Tage
    cd /opt/autoschnell && docker compose exec -T backend python scripts/systemcheck.py --tage 14

Die Ausgabe kann 1:1 an den Betreiber/Entwickler weitergegeben werden.
"""
import argparse
import asyncio
import os
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")      # Windows-Konsole: Umlaute/Gedankenstriche
except Exception:  # noqa: BLE001
    pass

BREIT = 100


def kopf(text: str) -> None:
    print()
    print("=" * BREIT)
    print(text)
    print("=" * BREIT)


def zeile(text: str = "") -> None:
    print(text)


def kurz(text, n=140) -> str:
    t = " ".join(str(text or "").split())
    return t if len(t) <= n else t[: n - 1] + "…"


_MASKE_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_MASKE_TELEFON = re.compile(r"(?<![\w/])\+?\d[\d \-/().]{5,}\d(?![\w/])")


def maskieren(text) -> str:
    """Pruefung 23.10.2026 (zweiter Bericht): E-Mail-Adressen und Telefonnummern in freien Texten (Alarme, Fehler,
    Seitentext) unkenntlich machen — die Ausgabe wird kopiert und weitergegeben."""
    t = _MASKE_EMAIL.sub("[e-mail]", str(text or ""))
    return _MASKE_TELEFON.sub(lambda m: "[nummer]" if sum(c.isdigit() for c in m.group(0)) >= 7 else m.group(0), t)


def _als_utc(wert):
    if isinstance(wert, datetime):
        return wert if wert.tzinfo else wert.replace(tzinfo=timezone.utc)
    if isinstance(wert, str) and wert:
        try:
            d = datetime.fromisoformat(wert.replace("Z", "+00:00"))
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


ZEITFELDER = ("created_at", "erstellt_am", "am", "gelesen_am", "begonnen", "zuletzt", "zuletzt_am", "updated_at",
              "timestamp", "ts", "datum", "fetched_at")


async def zeitfeld(db, sammlung: str):
    """(Feldname, Typ 'str'|'dt') des Zeitfelds dieser Sammlung — aus einem Beispiel-Dokument; sonst None."""
    doc = await db[sammlung].find_one({}, sort=[("_id", -1)])
    if not doc:
        return None
    for f in ZEITFELDER:
        w = doc.get(f)
        if isinstance(w, datetime):
            return f, "dt"
        if isinstance(w, str) and _als_utc(w):
            return f, "str"
    return None


def seit_filter(feld, typ, seit: datetime) -> dict:
    return {feld: {"$gte": seit if typ == "dt" else seit.isoformat()}}


async def anzahl(db, sammlung, filt=None) -> int:
    try:
        return await db[sammlung].count_documents(filt or {})
    except Exception as exc:  # noqa: BLE001
        return -1 if "not found" not in str(exc).lower() else 0


async def gruppen(db, sammlung, feld, filt=None, hoechstens=15):
    """[(wert, anzahl)] absteigend — ueber eine Aggregation (nichts wird geladen)."""
    try:
        cursor = db[sammlung].aggregate([
            {"$match": filt or {}},
            {"$group": {"_id": f"${feld}", "n": {"$sum": 1}}},
            {"$sort": {"n": -1}},
            {"$limit": hoechstens},
        ])
        return [(d.get("_id"), d.get("n", 0)) async for d in cursor]
    except Exception as exc:  # noqa: BLE001
        return [(f"(Fehler: {kurz(exc, 80)})", 0)]


def tabelle(paare, titel="", leer="  (nichts)") -> None:
    if titel:
        zeile(titel)
    if not paare:
        zeile(leer)
        return
    for wert, n in paare:
        zeile(f"  {n:>7}  {kurz(wert if wert not in (None, '') else '(leer)', 110)}")


async def konten_namen(db) -> dict:
    """user_id -> 'Kontonummer (Rolle)' — nur Kontonummern, keine Namen."""
    aus = {}
    async for u in db.users.find({}, {"_id": 0, "id": 1, "role": 1, "kontonummer": 1, "account_no": 1,
                                        "konto": 1, "username": 1, "dealer_id": 1}):
        nr = u.get("kontonummer") or u.get("account_no") or u.get("konto") or u.get("username") or "?"
        aus[u.get("id")] = f"{nr} ({u.get('role') or '?'})"
    return aus


async def firmen_namen(db) -> dict:
    aus = {}
    async for d in db.dealers.find({}, {"_id": 0, "id": 1, "kunden_nr": 1}):
        aus[d.get("id")] = f"Kd.-Nr. {d.get('kunden_nr') or '?'}"
    return aus


# ------------------------------------------------------------------------------------------------------ Abschnitte
async def abschnitt_bereitschaft():
    kopf("1. Bereitschaft (/api/ready, aus dem Container)")
    try:
        import requests
        port = os.environ.get("PORT") or "8001"
        r = requests.get(f"http://127.0.0.1:{port}/api/ready", timeout=15)
        zeile(f"  HTTP {r.status_code}")
        try:
            d = r.json()
        except ValueError:
            zeile("  " + kurz(r.text, 300))
            return
        for k in ("status", "ok"):
            if k in d:
                zeile(f"  {k}: {d[k]}")
        for k in ("fehler", "errors"):
            for e in d.get(k) or []:
                zeile(f"  FEHLER   {kurz(e, 160)}")
        for k in ("warnungen", "warnings"):
            for w in d.get(k) or []:
                zeile(f"  WARNUNG  {kurz(w, 160)}")
        info = d.get("info") or {}
        for k, v in list(info.items())[:40]:
            zeile(f"  info.{k}: {kurz(v, 120)}")
    except Exception as exc:  # noqa: BLE001
        zeile(f"  nicht erreichbar: {kurz(exc, 160)}")


async def abschnitt_alarme(db, seit):
    kopf("2. Betriebsalarme (betriebsalarme)")
    zeile(f"  offen gesamt: {await anzahl(db, 'betriebsalarme', {'offen': True})}")
    tabelle(await gruppen(db, "betriebsalarme", "typ", {"offen": True}), "  offen je Typ:")
    tabelle(await gruppen(db, "betriebsalarme", "typ", {"created_at": {"$gte": seit}}), "  neu im Zeitraum je Typ:")
    zeile("  juengste offene Alarme:")
    n = 0
    async for a in db.betriebsalarme.find({"offen": True}, {"_id": 0, "typ": 1, "ref": 1, "anzahl": 1, "details": 1,
                                                             "zuletzt": 1, "created_at": 1}).sort("zuletzt", -1).limit(12):
        n += 1
        det = a.get("details") or {}
        text = det.get("text") or det.get("message") or det.get("grund") or det.get("fehler") or ""
        zeile(f"    {str(a.get('zuletzt') or a.get('created_at'))[:16]}  {a.get('typ')}  x{a.get('anzahl') or 1}  "
              f"{maskieren(kurz(text or {k: v for k, v in det.items() if k not in ('key', 'token')}, 150))}")
    if not n:
        zeile("    (keine)")


async def abschnitt_fehler(db, seit):
    kopf("3. Server-Fehler (error_logs) und Oberflaechen-Fehler")
    zf = await zeitfeld(db, "error_logs")
    filt = seit_filter(*zf, seit) if zf else {}
    zeile(f"  Eintraege im Zeitraum: {await anzahl(db, 'error_logs', filt)}  (Zeitfeld: {zf})")
    tabelle(await gruppen(db, "error_logs", "source", filt), "  je Quelle:")
    tabelle(await gruppen(db, "error_logs", "error_type", filt), "  je Fehlerart:")
    tabelle(await gruppen(db, "error_logs", "path", filt), "  je Pfad:")
    tabelle(await gruppen(db, "error_logs", "status", filt), "  je Status:")
    zeile("  juengste 15 (gekuerzt):")
    async for e in db.error_logs.find(filt, {"_id": 0, "source": 1, "method": 1, "path": 1, "error_type": 1,
                                             "message": 1, "created_at": 1}).sort("_id", -1).limit(15):
        zeile(f"    {str(e.get('created_at'))[:16]}  {e.get('source')}  {e.get('method') or ''} {kurz(e.get('path'), 50)}  "
              f"{e.get('error_type') or ''}: {maskieren(kurz(e.get('message'), 120))}")


async def abschnitt_aktivitaet(db, seit, tage):
    kopf(f"4. Nutzer-Aktivitaet (activity_logs, letzte {tage} Tage)")
    filt = {"created_at": {"$gte": seit.isoformat()}}
    gesamt = await anzahl(db, "activity_logs", filt)
    zeile(f"  Handlungen gesamt: {gesamt}")
    tabelle(await gruppen(db, "activity_logs", "action", filt, 40), "  je Handlung:")
    konten = await konten_namen(db)
    firmen = await firmen_namen(db)
    je_konto = await gruppen(db, "activity_logs", "user_id", filt, 25)
    tabelle([(konten.get(u, f"unbekanntes Konto {str(u)[:8]}"), n) for u, n in je_konto], "  je Konto (Top 25):")
    je_firma = await gruppen(db, "activity_logs", "dealer_id", filt, 20)
    tabelle([(firmen.get(d, f"unbekannte Firma {str(d)[:8]}"), n) for d, n in je_firma], "  je Firma:")
    # aktive Konten je Tag
    zeile("  aktive Konten je Tag:")
    cursor = db.activity_logs.aggregate([
        {"$match": filt},
        {"$group": {"_id": {"tag": {"$substr": ["$created_at", 0, 10]}, "u": "$user_id"}}},
        {"$group": {"_id": "$_id.tag", "konten": {"$sum": 1}}},
        {"$sort": {"_id": 1}},
    ])
    async for d in cursor:
        zeile(f"    {d['_id']}  {d['konten']} Konten")
    # Konten ohne jede Handlung im Zeitraum
    aktive = {u for u, _ in await gruppen(db, "activity_logs", "user_id", filt, 10000)}
    stille = []
    async for u in db.users.find({"active": {"$ne": False}, "role": {"$in": ["sucher", "dealer"]}},
                                  {"_id": 0, "id": 1, "role": 1}):
        if u.get("id") not in aktive:
            stille.append(konten.get(u.get("id"), "?"))
    zeile(f"  aktive Sucher/Chef-Konten ohne Handlung im Zeitraum: {len(stille)}"
          + (f" — {', '.join(sorted(stille)[:30])}" if stille else ""))


async def abschnitt_konten(db):
    kopf("5. Konten, Firmen, Abos, Sperren")
    tabelle(await gruppen(db, "users", "role"), "  Konten je Rolle:")
    zeile(f"  deaktivierte Konten: {await anzahl(db, 'users', {'active': False})}")
    zeile(f"  Firmen: {await anzahl(db, 'dealers')}   geloeschte Firmen (firmen_geloescht): {await anzahl(db, 'firmen_geloescht')}")
    zeile(f"  Fahrer-Konten: {await anzahl(db, 'driver_accounts')}   Fahrer-Zuordnungen: {await anzahl(db, 'dealer_drivers')}")
    tabelle(await gruppen(db, "subscriptions", "status"), "  Abos je Status:")
    beispiel = await db.subscriptions.find_one({}, sort=[("_id", -1)])
    if beispiel:
        zeile("  Abo-Felder (nur Namen): " + ", ".join(sorted(k for k in beispiel if k != "_id")))
        jetzt = datetime.now(timezone.utc)
        bald = 0
        abgelaufen = 0
        async for s in db.subscriptions.find({}, {"_id": 0}):
            ende = None
            for k in ("valid_until", "ends_at", "expires_at", "gueltig_bis", "ende", "current_period_end", "bis"):
                ende = _als_utc(s.get(k))
                if ende:
                    break
            if ende and ende < jetzt:
                abgelaufen += 1
            elif ende and ende < jetzt + timedelta(days=7):
                bald += 1
        zeile(f"  Abos abgelaufen (Datum in der Vergangenheit): {abgelaufen}   laufen in 7 Tagen ab: {bald}")
        konten = await konten_namen(db)
        zeile("  Abos mit Status active, die abgelaufen sind oder binnen 14 Tagen ablaufen (Konto, Ablauf):")
        n = 0
        async for s in db.subscriptions.find({"status": "active"}, {"_id": 0}):
            ende = None
            for k in ("expires_at", "valid_until", "ends_at", "gueltig_bis", "ende", "current_period_end", "bis"):
                ende = _als_utc(s.get(k))
                if ende:
                    break
            if ende and ende < jetzt + timedelta(days=14):
                n += 1
                wer = konten.get(s.get("subject_user_id") or s.get("user_id"), f"Firma {str(s.get('dealer_id'))[:8]}")
                zeile(f"    {wer:<24} {ende.strftime('%d.%m.%Y')}  {'ABGELAUFEN' if ende < jetzt else 'laeuft ab'}  Plan {s.get('plan')}")
        if not n:
            zeile("    (keine)")
    tabelle(await gruppen(db, "abo_vorgaenge", "status"), "  Abo-Vorgaenge je Status:")
    tabelle(await gruppen(db, "manual_payments", "status"), "  manuelle Zahlungen je Status:")
    tabelle(await gruppen(db, "sperren", "art"), "  Sperren je Art:")
    zeile(f"  Sperren gesamt: {await anzahl(db, 'sperren')}   offene Passwort-Resets: {await anzahl(db, 'password_resets')}")
    zeile(f"  Zugangs-Aenderungen gesamt: {await anzahl(db, 'zugangs_aenderungen')}   Einladungen: {await anzahl(db, 'dealer_invites')}")


async def abschnitt_abrufe(db, seit):
    kopf("6. Inserats-Abrufe (link_jobs, listings_cache, provider_budget)")
    filt = {"created_at": {"$gte": seit}}
    zeile(f"  Jobs im Zeitraum: {await anzahl(db, 'link_jobs', filt)}")
    tabelle(await gruppen(db, "link_jobs", "status", filt), "  je Status:")
    tabelle(await gruppen(db, "link_jobs", "source", filt), "  je Quelle:")
    tabelle(await gruppen(db, "link_jobs", "error", {**filt, "status": "failed"}, 15), "  Fehlertexte (failed):")
    tabelle(await gruppen(db, "link_jobs", "attempts", filt), "  Versuche je Job:")
    alt = datetime.now(timezone.utc) - timedelta(minutes=10)
    zeile(f"  JETZT wartend (queued): {await anzahl(db, 'link_jobs', {'status': 'queued'})}   "
          f"davon aelter als 10 min: {await anzahl(db, 'link_jobs', {'status': 'queued', 'created_at': {'$lt': alt}})}   "
          f"in Arbeit (processing): {await anzahl(db, 'link_jobs', {'status': 'processing'})}")
    zeile(f"  Speicher: listings_cache {await anzahl(db, 'listings_cache')}  (ohne Daten: "
          f"{await anzahl(db, 'listings_cache', {'data': None})})   listings_cache_client {await anzahl(db, 'listings_cache_client')}"
          f"   vehicle_cache {await anzahl(db, 'vehicle_cache')}")
    zeile(f"  Browser-Lesungen (werkzeug_inserate): gesamt {await anzahl(db, 'werkzeug_inserate')}  im Zeitraum "
          f"{await anzahl(db, 'werkzeug_inserate', {'gelesen_am': {'$gte': seit}})}  davon ohne Fotos "
          f"{await anzahl(db, 'werkzeug_inserate', {'gelesen_am': {'$gte': seit}, '$or': [{'data.images': {'$exists': False}}, {'data.images': None}, {'data.images': {'$size': 0}}]})}")
    tabelle(await gruppen(db, "werkzeug_inserate", "source", {"gelesen_am": {"$gte": seit}}), "  Lesungen je Portal:")
    beispiel = await db.provider_budget.find_one({}, sort=[("_id", -1)])
    if beispiel:
        zeile("  provider_budget-Felder (nur Namen): " + ", ".join(sorted(k for k in beispiel if k != "_id")))
        heute = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        n = 0
        async for b in db.provider_budget.find({}, {"_id": 0}).sort("_id", -1).limit(400):
            tag = str(b.get("tag") or b.get("day") or b.get("datum") or b.get("date") or "")
            if tag.startswith(heute):
                n += 1
                zahlen = {k: v for k, v in b.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
                zeile(f"    heute: { {k: b.get(k) for k in ('herkunft', 'art', 'scope', 'quelle') if k in b} } {zahlen}")
                if n >= 15:
                    break
        if not n:
            zeile("    (heute noch keine Budget-Eintraege)")
    tabelle(await gruppen(db, "provider_stats", "_id", None, 10), "  provider_stats (Kennungen):")


async def abschnitt_werkzeuge(db, seit, tage):
    kopf(f"7. Windows-Programm + Browser-Erweiterung (letzte {tage} Tage)")
    konten = await konten_namen(db)
    zeile("  Verbindungen (werkzeug_verbindungen):")
    async for v in db.werkzeug_verbindungen.find({}, {"_id": 0, "werkzeug": 1, "user_id": 1, "programm_version": 1,
                                                        "zuletzt_am": 1, "vorgang_verpasst_am": 1}).sort("zuletzt_am", -1).limit(60):
        zeile(f"    {v.get('werkzeug'):<20} {konten.get(v.get('user_id'), '?'):<22} v{v.get('programm_version') or '?':<8} "
              f"zuletzt {str(v.get('zuletzt_am'))[:16]}" + ("  (Vorgang verpasst)" if v.get("vorgang_verpasst_am") else ""))
    filt = {"erstellt_am": {"$gte": seit.isoformat()}, "probelauf": {"$ne": True}}
    zeile(f"  Programm-Vergleiche im Zeitraum: {await anzahl(db, 'werkzeug_vergleiche', filt)}   "
          f"Probelaeufe: {await anzahl(db, 'werkzeug_vergleiche', {'erstellt_am': {'$gte': seit.isoformat()}, 'probelauf': True})}")
    tabelle(await gruppen(db, "werkzeug_vergleiche", "fahrzeug.erkennung", filt), "  Erkennung (bildschirm/inserat/gelernt/zweite_lesung):")
    zeile(f"  davon Modell NICHT gefunden: {await anzahl(db, 'werkzeug_vergleiche', {**filt, 'fahrzeug.modell_gefunden': False})}   "
          f"ohne Inserat-Link: {await anzahl(db, 'werkzeug_vergleiche', {**filt, 'fahrzeug.inserat_url': None})}   "
          f"Nummer unsicher: {await anzahl(db, 'werkzeug_vergleiche', {**filt, 'fahrzeug.inserat_nummer_unsicher': True})}")
    tabelle(await gruppen(db, "werkzeug_vergleiche", "fahrzeug.quelle", filt), "  je Portal (vom Bildschirm):")
    tabelle(await gruppen(db, "werkzeug_vergleiche", "vorab", filt), "  Vorab-Abruf-Status:")
    # Hinweise (Texte sind Systemtexte, keine Personendaten)
    cursor = db.werkzeug_vergleiche.aggregate([
        {"$match": filt}, {"$unwind": "$hinweise"},
        {"$group": {"_id": "$hinweise", "n": {"$sum": 1}}}, {"$sort": {"n": -1}}, {"$limit": 20}])
    tabelle([(d["_id"], d["n"]) async for d in cursor], "  haeufigste Hinweise an die Sucher:")
    tabelle([(konten.get(u, f"unbekanntes Konto {str(u)[:8]}"), n)
             for u, n in await gruppen(db, "werkzeug_vergleiche", "user_id", filt, 15)], "  Vergleiche je Konto:")
    tabelle([(konten.get(u, f"unbekanntes Konto {str(u)[:8]}"), n)
             for u, n in await gruppen(db, "werkzeug_vergleiche", "user_id", {**filt, "fahrzeug.inserat_url": None}, 10)],
            "  davon OHNE Inserat-Link je Konto:")
    tabelle(await gruppen(db, "werkzeug_app_starts", "zustand", {"am": {"$gte": seit.isoformat()}}),
            "  App-Starts (Vertrag aus dem Programm) je Zustand:")
    zeile(f"  Lesebilder (nicht erkannte Anzeigen): gesamt {await anzahl(db, 'werkzeug_lesebilder')}")
    tabelle(await gruppen(db, "werkzeug_lesebilder", "grund"), "    je Grund:")
    cursor = db.werkzeug_lesebilder.aggregate([
        {"$group": {"_id": "$fahrzeug.marke_modell_text", "n": {"$sum": 1}}}, {"$sort": {"n": -1}}, {"$limit": 20}])
    tabelle([(d["_id"], d["n"]) async for d in cursor], "    gelesene Texte (Marke/Modell), die nicht erkannt wurden:")
    cursor = db.werkzeug_lesebilder.aggregate([
        {"$unwind": "$fehlt"}, {"$group": {"_id": "$fehlt", "n": {"$sum": 1}}}, {"$sort": {"n": -1}}])
    tabelle([(d["_id"], d["n"]) async for d in cursor], "    fehlende Pflichtfelder:")
    zeile(f"  Leseseiten (Seiten, die die Erweiterung nicht lesen konnte): gesamt {await anzahl(db, 'werkzeug_leseseiten')}")
    tabelle(await gruppen(db, "werkzeug_leseseiten", "grund"), "    je Grund:")
    tabelle(await gruppen(db, "werkzeug_leseseiten", "portal"), "    je Portal:")
    tabelle([(konten.get(u, f"unbekanntes Konto {str(u)[:8]}"), n)
             for u, n in await gruppen(db, "werkzeug_leseseiten", "user_id")], "    je Konto:")
    async for s in db.werkzeug_leseseiten.find({}, {"_id": 0, "id": 1, "url": 1, "grund": 1, "erstellt_am": 1, "groesse": 1}) \
            .sort("erstellt_am", -1).limit(10):
        zeile(f"    {str(s.get('erstellt_am'))[:16]}  {kurz(s.get('grund'), 40)}  {s.get('groesse')} B  {kurz(s.get('url'), 80)}  id={s.get('id')}")
    zeile(f"  Codes offen: {await anzahl(db, 'werkzeug_codes')}   gelernte Lesefehler (erkennung_gelernt): {await anzahl(db, 'erkennung_gelernt')}")


async def abschnitt_vorgaenge(db, seit, tage):
    kopf(f"8. Kaufvorgaenge, Vertraege, Termine, Abholung (letzte {tage} Tage)")
    for sammlung in ("kaufvorgaenge", "vehicles", "appointments", "pickup_protocols", "pickup_reports",
                     "generated_pdfs", "inserat_beweise", "ki_bewertungen"):
        zf = await zeitfeld(db, sammlung)
        filt = seit_filter(*zf, seit) if zf else {}
        zeile(f"  {sammlung:<18} gesamt {await anzahl(db, sammlung):>7}   im Zeitraum {await anzahl(db, sammlung, filt):>6}"
              + (f"   (Zeitfeld {zf[0]})" if zf else "   (kein Zeitfeld erkannt)"))
        if sammlung in ("kaufvorgaenge", "appointments", "pickup_protocols", "vehicles", "ki_bewertungen"):
            tabelle(await gruppen(db, sammlung, "status", filt), f"    {sammlung} je Status (Zeitraum):")
    jetzt = datetime.now(timezone.utc)
    beispiel = await db.appointments.find_one({}, sort=[("_id", -1)])
    if beispiel:
        zeile("  appointments-Felder (nur Namen): " + ", ".join(sorted(k for k in beispiel if k != "_id")))
        ueberfaellig = Counter()
        aeltester = {}
        async for t in db.appointments.find({"status": {"$nin": ["abgeholt", "storniert", "erledigt", "abgesagt", "final"]}},
                                            {"_id": 0}).limit(5000):
            w = None
            for k in ("pickup_date", "datum", "date", "termin", "abholdatum", "start"):
                w = _als_utc(t.get(k))
                if w:
                    break
            if w and w < jetzt - timedelta(days=1):
                ueberfaellig[t.get("dealer_id")] += 1
                aeltester[t.get("dealer_id")] = min(aeltester.get(t.get("dealer_id"), w), w)
        firmen = await firmen_namen(db)
        zeile(f"  Termine offen, aber Datum mehr als 1 Tag vorbei: {sum(ueberfaellig.values())}")
        for d, n in ueberfaellig.most_common(10):
            zeile(f"    {firmen.get(d, 'unbekannte Firma'):<16} {n:>3}  aeltester {aeltester[d].strftime('%d.%m.%Y')}")
    zeile(f"  Fahrzeuge ohne Fotos (data.images leer): "
          f"{await anzahl(db, 'vehicles', {'$or': [{'data.images': {'$exists': False}}, {'data.images': None}, {'data.images': {'$size': 0}}]})}")
    zeile(f"  Protokolle ohne Unterschrift (signature/unterschrift leer): "
          f"{await anzahl(db, 'pickup_protocols', {'$and': [{'$or': [{'signature': {'$exists': False}}, {'signature': None}, {'signature': ''}]}, {'$or': [{'unterschrift': {'$exists': False}}, {'unterschrift': None}, {'unterschrift': ''}]}]})}")
    zeile(f"  Meldungen (meldungen) gesamt: {await anzahl(db, 'meldungen')}   ungelesen: "
          f"{await anzahl(db, 'meldungen', {'$or': [{'gelesen': False}, {'gelesen_am': None}, {'read': False}]})}")


async def abschnitt_mail(db, seit):
    kopf("9. E-Mail-Versand (mail_idempotenz, mail_takt)")
    filt = {"begonnen": {"$gte": seit}}
    zeile(f"  Versuche im Zeitraum: {await anzahl(db, 'mail_idempotenz', filt)}")
    tabelle(await gruppen(db, "mail_idempotenz", "status", filt), "  je Status:")
    alt = datetime.now(timezone.utc) - timedelta(minutes=30)
    zeile(f"  haengend 'laeuft' aelter als 30 min: {await anzahl(db, 'mail_idempotenz', {'status': 'laeuft', 'begonnen': {'$lt': alt}})}")
    takt = await db.mail_takt.find_one({"_id": "resend"})
    if takt:
        zeile(f"  Mail-Takt naechste Sendung: {takt.get('naechste')}")


async def abschnitt_betrieb(db, seit):
    kopf("10. Betrieb: Grenzen, Aufraeumen, Wartung, KI, Markt")
    # rate_limits: Kennungen nach Name (vor dem ersten ':')
    zaehler = Counter()
    async for d in db.rate_limits.find({}, {"_id": 1}).limit(20000):
        zaehler[str(d["_id"]).split(":")[0]] += 1
    tabelle(zaehler.most_common(20), "  Bremsen (rate_limits) — Eintraege je Bremse (viele = oft erreicht/aktiv):")
    sperren = Counter()
    async for d in db.rate_limits.find({"bis": {"$exists": True}}, {"_id": 1}).limit(5000):
        sperren[str(d["_id"]).split(":")[0]] += 1
    tabelle(sperren.most_common(10), "  davon aktive Wartezeiten (Konten/IPs gerade gebremst):")
    zeile(f"  storage_delete_retry (haengende Datei-Loeschungen): {await anzahl(db, 'storage_delete_retry')}")
    zeile(f"  kaputte_docs: {await anzahl(db, 'kaputte_docs')}   zz_unbekannt: {await anzahl(db, 'zz_unbekannt')}")
    zeile(f"  job_locks: {await anzahl(db, 'job_locks')}   wartung_schreiber: {await anzahl(db, 'wartung_schreiber')}")
    async for f in db.system_flags.find({}, {"_id": 0}).limit(30):
        zeile(f"  system_flag: {kurz({k: v for k, v in f.items() if 'secret' not in k and 'key' not in k and 'token' not in k}, 160)}")
    mig = await db.schema_migrations.find_one({}, sort=[("_id", -1)])
    if mig:
        zeile(f"  letzte Migration: {kurz({k: v for k, v in mig.items() if k != '_id'}, 160)}")
    async for b in db.ki_budget.find({}, {"_id": 0}).sort("_id", -1).limit(3):
        zeile(f"  ki_budget: {kurz({k: v for k, v in b.items() if isinstance(v, (int, float, str))}, 200)}")
    tabelle(await gruppen(db, "ki_bewertungen", "status", {}), "  KI-Bewertungen je Status (gesamt):")
    tabelle(await gruppen(db, "ki_bewertungen", "grund", {"status": "fehler"}), "  KI-Fehlergruende (gesamt):")
    tabelle(await gruppen(db, "market_crawl_jobs", "status", {}), "  Markt-Laeufe je Status (gesamt):")
    zf = await zeitfeld(db, "market_crawl_jobs")
    if zf:
        tabelle(await gruppen(db, "market_crawl_jobs", "status", seit_filter(*zf, seit)), "  Markt-Laeufe je Status (Zeitraum):")
    zeile(f"  Marktplatz: resale_listings {await anzahl(db, 'resale_listings')}   Kaufanfragen (plan_requests) {await anzahl(db, 'plan_requests')}")


async def abschnitt_speicher(db):
    kopf("11. Datenbank-Groesse")
    try:
        st = await db.command("dbStats")
        zeile(f"  Datenbank {st.get('db')}: Daten {st.get('dataSize', 0) / 1e6:.0f} MB, Speicher {st.get('storageSize', 0) / 1e6:.0f} MB, "
              f"Indizes {st.get('indexSize', 0) / 1e6:.0f} MB, Sammlungen {st.get('collections')}, Dokumente {st.get('objects')}")
    except Exception as exc:  # noqa: BLE001
        zeile(f"  dbStats nicht lesbar: {kurz(exc, 100)}")
    groessen = []
    for name in await db.list_collection_names():
        try:
            cs = await db.command("collStats", name)
            groessen.append((name, cs.get("count", 0), (cs.get("storageSize", 0) + cs.get("totalIndexSize", 0)) / 1e6))
        except Exception:  # noqa: BLE001
            continue
    groessen.sort(key=lambda x: -x[2])
    zeile("  groesste Sammlungen (Dokumente, MB inkl. Indizes):")
    for name, n, mb in groessen[:15]:
        zeile(f"    {name:<28} {n:>9}  {mb:>8.1f} MB")


async def main() -> int:
    p = argparse.ArgumentParser(description="Systemcheck — nur lesen, nur Zahlen")
    p.add_argument("--tage", type=int, default=7)
    args = p.parse_args()
    from deps import db
    seit = datetime.now(timezone.utc) - timedelta(days=args.tage)
    zeile(f"Systemcheck {datetime.now(timezone.utc).strftime('%d.%m.%Y %H:%M')} UTC — Datenbank {os.environ.get('DB_NAME') or '?'} — "
          f"Zeitraum: letzte {args.tage} Tage (seit {seit.strftime('%d.%m.%Y')})")
    for schritt in (abschnitt_bereitschaft(), abschnitt_alarme(db, seit), abschnitt_fehler(db, seit),
                    abschnitt_aktivitaet(db, seit, args.tage), abschnitt_konten(db), abschnitt_abrufe(db, seit),
                    abschnitt_werkzeuge(db, seit, args.tage), abschnitt_vorgaenge(db, seit, args.tage),
                    abschnitt_mail(db, seit), abschnitt_betrieb(db, seit), abschnitt_speicher(db)):
        try:
            await schritt
        except Exception as exc:  # noqa: BLE001 — ein Abschnitt darf die anderen nicht verhindern
            zeile(f"  !! Abschnitt abgebrochen: {kurz(exc, 200)}")
    zeile()
    zeile("Ende. Bitte die ganze Ausgabe weitergeben.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
