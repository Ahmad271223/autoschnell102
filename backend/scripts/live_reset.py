# -*- coding: utf-8 -*-
"""Kontonummer (13.09.2026), Schritt 7 — Live-Reset: alle Testdaten loeschen,
nur der Super-Admin bleibt.

Einmalig beim Live-Gang der Anmeldung mit Kontonummer (Runbook in
DEPLOYMENT.md, "Go-Live: Anmeldung mit Kontonummer und Live-Reset"). Auch fuer
die lokale Dev-DB nutzbar (ersetzt das bewusst NICHT gebaute automatische
Nachziehen von Kontonummern).

Aufruf (im Container, Backend auf BEIDEN Servern gestoppt, Backup vorher):
    python scripts/live_reset.py --db autoschnell                  # PROBELAUF (Standard)
    python scripts/live_reset.py --db autoschnell \\
        --ausfuehren --bestaetige autoschnell [--nummern-ab 10001]  # fragt 'LOESCHEN' ab

Schutz (Vorfall geloeschte Alt-Vertraege):
  - ohne --ausfuehren wird NICHTS geaendert,
  - --bestaetige muss den DB-Namen exakt nennen, dazu die Eingabe LOESCHEN
    (oder --ja),
  - der Super-Admin muss GENAU einmal gefunden werden
    (role admin, is_super_admin True, username),
  - feste Loesch- und Behalte-Listen; jede Sammlung, die in keiner Liste
    steht, blockiert das Ausfuehren,
  - nur delete_many — nie drop_collection, nie drop_index (alle Indizes
    bleiben),
  - Dateien werden eingesammelt und geloescht, BEVOR die Dokumente mit ihren
    Referenzen verschwinden (Praefixe + Snapshot-Pfade aus listing_snapshots),
  - Grabstein system_flags._id='live_reset' (status 'laeuft'): ein Abbruch
    wird durch einen erneuten Aufruf fortgesetzt.

Optionen:
  --db NAME                  Datenbank (Standard: env DB_NAME)
  --super-admin-username U   (Standard: env SUPER_ADMIN_USERNAME)
  --ohne-dateien             Datei-Speicher nicht anfassen
  --caches-leeren            auch listings_cache, listings_cache_client, vehicle_cache
  --nummern-ab N             counters.kunden_nr nur ANHEBEN ($max), naechste Nummer >= N
  --bericht datei.json       Bericht als JSON schreiben (auch im Probelauf)

Exit 0 = Probelauf bzw. erledigt, 1 = keine Verbindung / DB-Name fehlt,
2 = Super-Admin fehlt oder ist doppelt, 3 = unbekannte Sammlung blockiert,
4 = Bestaetigung fehlt oder falsch (nichts geaendert),
5 = Datenbank erledigt, aber nicht alle Dateien geloescht (Ziele auf der
    Konsole und in system_flags.live_reset.datei_fehler; ein erneuter Aufruf
    versucht sie erneut, der vorige Lauf bleibt unter vorige_laeufe).
"""
import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

FLAG_ID = "live_reset"
BESTAETIGUNG = "LOESCHEN"

# ---------------------------------------------------------------- Listen
# Ganz geleert (Reihenfolge = Loeschreihenfolge; dealers und users kommen
# danach, ganz zum Schluss).
GANZ_LEEREN = (
    # Konten und Zuordnung (drivers = Alt-Sammlung)
    "driver_accounts", "dealer_drivers", "drivers",
    # Marktplatz
    "dealer_invites", "network_members", "buyer_favorites", "listing_interest",
    # Anfragen
    "plan_requests",
    # Fahrzeuge und Vertraege (generated_pdfs samt eingebetteter PDFs)
    "vehicles", "kaufvorgaenge", "generated_pdfs", "generated_pdf_versions",
    # Termine, Protokolle, Berichte
    "appointments", "pickup_protocols", "pickup_reports",
    # Inserate, Vergleiche, Beweise
    "resale_listings", "vehicle_comparisons", "listing_snapshots",
    "inserat_beweise", "link_jobs",
    # Zahlungen und Abos
    "manual_payments", "payment_transactions", "zugang_grants",
    "abo_vorgaenge", "zugangs_aenderungen",
    # Auto-Daten aus Test-Vertraegen
    "admin_vehicle_data",
    # Protokolle und Zustand (betriebsalarme setzt der Start neu, falls
    # weiter zutreffend; kaputte_docs ist nur ein Testrest)
    "password_resets", "activity_logs", "error_logs", "storage_delete_retry",
    "rate_limits", "system_reports", "betriebsalarme", "kaputte_docs",
)
# Ausnahmen beim Leeren: Abschluss-Eintraege system.live_reset frueherer
# Laeufe bleiben (Nachbesserung Schritt 7 — ein zweiter Lauf loescht den
# Nachweis des ersten nicht)
LEEREN_AUSSER = {"activity_logs": {"action": {"$ne": "system.live_reset"}}}
# Teilweise geleert: alles ausser dem Super-Admin (Filter siehe _teil_filter)
TEILWEISE = ("subscriptions", "dealers", "users")
# Bleiben unberuehrt (Schema-Version, Wartungsmodus, Backup-Stand, die
# Nummernreihe — Testnummern werden nie neu vergeben —, Sperren und
# Anbieter-Zaehler)
BEHALTEN = (
    "system_flags", "schema_migrations", "counters", "job_locks", "sperren",
    "provider_limits", "provider_budget", "provider_slots", "provider_stats",
)
# Oeffentliche Inserats-Caches: bleiben, nur mit --caches-leeren geleert
CACHES = ("listings_cache", "listings_cache_client", "vehicle_cache")

# Datei-Praefixe, die komplett geloescht werden. Unterschriften und
# Protokoll-PDFs (pickup_protocols.pdf_path, signature_*_key) liegen unter
# protocol/<dealer_id>/, Beweis-PDFs samt Versionen unter beweise/.
PRAEFIXE = ("protocol/", "pickup/", "resale/", "beweise/")


def _jetzt() -> str:
    return datetime.now(timezone.utc).isoformat()


def _nummern_ab(wert: str) -> int:
    try:
        n = int(wert)
    except ValueError:
        raise argparse.ArgumentTypeError("--nummern-ab braucht eine ganze Zahl")
    # Nummer = 1000 + counters.kunden_nr.seq (kontenanlage.naechste_nummer);
    # Kontonummern haben 4 bis 9 Stellen (kontonummer.MUSTER).
    if not 1001 <= n <= 999_999_999:
        raise argparse.ArgumentTypeError("--nummern-ab muss zwischen 1001 und 999999999 liegen")
    return n


def _teil_filter(name: str, sa_id: str, sa_dealer_id) -> dict:
    """Loesch-Filter der teilweise geleerten Sammlungen. Ohne Firma des
    Super-Admins gibt es dort nichts zu behalten ({} = alles)."""
    if name == "users":
        return {"id": {"$ne": sa_id}}
    if name == "dealers":
        return {"id": {"$ne": sa_dealer_id}} if sa_dealer_id else {}
    if name == "subscriptions":
        # Nachbesserung Schritt 7: persoenliche Abos anderer Konten
        # (subject_user_id, z.B. Sucher in der Firma des Super-Admins) gehen
        # mit ihrem Konto — wie bei der Kontoloeschung in routes/admin.py.
        if not sa_dealer_id:
            return {}
        return {"$or": [{"dealer_id": {"$ne": sa_dealer_id}},
                        {"subject_user_id": {"$type": "string", "$ne": sa_id}}]}
    raise KeyError(name)


def _leeren_filter(name: str) -> dict:
    """Filter der ganz geleerten Sammlungen: alles, nur die Abschluss-
    Eintraege frueherer Live-Resets bleiben (Nachweis, Nachbesserung)."""
    return LEEREN_AUSSER.get(name, {})


def _leeren(db, name: str, filt: dict) -> int:
    """Die EINE Stelle, an der Dokumente geloescht werden (nur delete_many)."""
    return db[name].delete_many(filt).deleted_count


def _plan(db, sa_id, sa_dealer_id, caches_leeren: bool):
    """Tabelle je vorhandener Sammlung: (name, art, loeschen, bleibt, filter)."""
    zeilen, unbekannt = [], []
    for name in sorted(n for n in db.list_collection_names() if not n.startswith("system.")):
        gesamt = db[name].count_documents({})
        if name in GANZ_LEEREN:
            if name in LEEREN_AUSSER:
                n = db[name].count_documents(_leeren_filter(name))
                zeilen.append((name, "leeren", n, gesamt - n,
                               "alles ausser Eintraegen system.live_reset"))
            else:
                zeilen.append((name, "leeren", gesamt, 0, "alles"))
        elif name in TEILWEISE:
            n = db[name].count_documents(_teil_filter(name, sa_id, sa_dealer_id))
            text = {"users": "alles ausser Super-Admin",
                    "dealers": "alles ausser Firma des Super-Admins",
                    "subscriptions": "alles ausser Firmen-Abo der Super-Admin-Firma "
                                     "(persoenliche Abos anderer Konten weg)"}[name]
            zeilen.append((name, "teilweise", n, gesamt - n, text))
        elif name in BEHALTEN:
            zeilen.append((name, "behalten", 0, gesamt, "bleibt"))
        elif name in CACHES:
            if caches_leeren:
                zeilen.append((name, "cache", gesamt, 0, "Cache (--caches-leeren)"))
            else:
                zeilen.append((name, "cache", 0, gesamt, "Cache bleibt"))
        else:
            unbekannt.append(name)
            zeilen.append((name, "unbekannt", 0, gesamt, "UNBEKANNT - nicht angefasst"))
    return zeilen, unbekannt


def _datei_ziele(db, sa_dealer_id):
    """Praefixe (inkl. logo/<firma>/ ausser Super-Admin) und Snapshot-Keys —
    eingesammelt, solange die Dokumente noch da sind."""
    praefixe = list(PRAEFIXE)
    logos, fehler = [], []
    from storage_service import StorageError, _validate_key
    for d in db.dealers.find({}, {"_id": 0, "id": 1}):
        did = d.get("id")
        if not did or did == sa_dealer_id:
            continue
        p = f"logo/{did}/"
        try:
            _validate_key(p + "x.jpg")
        except StorageError:
            fehler.append({"art": "prefix", "ziel": p, "fehler": "ungueltige Firmen-ID"})
            continue
        logos.append(p)
    keys = set()
    for s in db.listing_snapshots.find({}, {"_id": 0, "png_path": 1, "pdf_path": 1}):
        for feld in ("png_path", "pdf_path"):
            v = s.get(feld)
            if isinstance(v, str) and v.strip():
                keys.add(v.strip())
    return praefixe, logos, sorted(keys), fehler


def main(argv=None, db=None, speicher=None, snapshot_loescher=None) -> int:
    ap = argparse.ArgumentParser(
        description="Live-Reset: Testdaten loeschen, nur der Super-Admin bleibt "
                    "(ohne --ausfuehren nur Probelauf)")
    ap.add_argument("--db", default=None, help="Datenbankname (Standard: env DB_NAME)")
    ap.add_argument("--super-admin-username", default=None,
                    help="Benutzername des Super-Admins (Standard: env SUPER_ADMIN_USERNAME)")
    ap.add_argument("--ausfuehren", action="store_true", help="wirklich loeschen")
    ap.add_argument("--bestaetige", default=None, metavar="DB_NAME",
                    help="Pflicht mit --ausfuehren: exakt der Name der Datenbank")
    ap.add_argument("--ja", action="store_true", help="Eingabe 'LOESCHEN' nicht abfragen")
    ap.add_argument("--ohne-dateien", action="store_true", help="Datei-Speicher nicht anfassen")
    ap.add_argument("--caches-leeren", action="store_true",
                    help="auch listings_cache, listings_cache_client, vehicle_cache leeren")
    ap.add_argument("--nummern-ab", type=_nummern_ab, default=None, metavar="N",
                    help="Nummernzaehler nur anheben: naechste Kontonummer >= N")
    ap.add_argument("--bericht", default=None, metavar="DATEI", help="Bericht als JSON schreiben")
    args = ap.parse_args(argv)

    if db is None:
        try:
            from dotenv import load_dotenv
            load_dotenv(Path(__file__).resolve().parents[1] / ".env")
        except ImportError:
            pass
        name = args.db or os.environ.get("DB_NAME") or ""
        if not name:
            print("FEHLER: kein Datenbankname (--db oder DB_NAME).")
            return 1
        from pymongo import MongoClient
        url = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017"
        db = MongoClient(url, serverSelectionTimeoutMS=8000)[name]
    try:
        db.command("ping")
    except Exception as exc:  # noqa: BLE001
        print(f"FEHLER: Datenbank nicht erreichbar: {exc}")
        return 1

    bericht = {"db": db.name, "app_env": os.environ.get("APP_ENV", "") or "(leer)",
               "modus": "ausfuehren" if args.ausfuehren else "probelauf",
               "erstellt_am": _jetzt()}

    def _bericht_schreiben():
        if args.bericht:
            with open(args.bericht, "w", encoding="utf-8") as f:
                json.dump(bericht, f, ensure_ascii=False, indent=2, default=str)
            print(f"Bericht: {args.bericht}")

    # 1. Verbindung, Umgebung, Speicher, Super-Admin
    if not args.ohne_dateien and speicher is None:
        from storage_service import storage as speicher
    if not args.ohne_dateien and snapshot_loescher is None:
        import snapshot_service
        snapshot_loescher = snapshot_service.delete_object
        snap_art = snapshot_service.speicherort()
    else:
        snap_art = "eingesteckt" if snapshot_loescher else "-"
    speicher_art = "-" if args.ohne_dateien else getattr(speicher, "name", type(speicher).__name__)
    bericht["speicher"] = {"dateien": speicher_art, "snapshots": snap_art}
    print(f"Datenbank: {db.name}   APP_ENV: {bericht['app_env']}   "
          f"Speicher: {speicher_art}   Snapshots: {snap_art}")

    sa_name = (args.super_admin_username or os.environ.get("SUPER_ADMIN_USERNAME") or "").strip()
    if not sa_name:
        print("FEHLER: kein Benutzername des Super-Admins (--super-admin-username "
              "oder SUPER_ADMIN_USERNAME).")
        return 2
    treffer = list(db.users.find(
        {"role": "admin", "is_super_admin": True, "username": sa_name},
        {"_id": 0, "id": 1, "dealer_id": 1}))
    if len(treffer) != 1:
        print(f"FEHLER: Super-Admin '{sa_name}' {len(treffer)}x gefunden "
              "(erwartet: genau 1 mit role admin und is_super_admin) - Abbruch.")
        return 2
    sa_id, sa_dealer_id = treffer[0]["id"], treffer[0].get("dealer_id") or None
    bericht["super_admin"] = {"id": sa_id, "username": sa_name, "dealer_id": sa_dealer_id}
    print(f"Super-Admin: {sa_name} (id {sa_id}, Firma {sa_dealer_id or '-'}) - bleibt.")

    flag = db.system_flags.find_one({"_id": FLAG_ID}) or {}
    fortsetzung = flag.get("status") == "laeuft"
    bericht["fortsetzung"] = fortsetzung
    if fortsetzung:
        print(f"HINWEIS: Ein Live-Reset von {flag.get('gestartet_am', '?')} ist nicht "
              "abgeschlossen - dieser Lauf ist eine Fortsetzung.")
    # Nachbesserung Schritt 7: nicht geloeschte Dateien eines abgeschlossenen
    # Laufs (Exit 5) gehen nicht verloren — sie werden zuerst erneut versucht
    # (die Dokumente mit den Referenzen sind dann schon weg).
    nachholen = list((flag.get("nachholen") if fortsetzung else flag.get("datei_fehler")) or [])
    bericht["nachholen"] = nachholen
    if nachholen:
        print(f"HINWEIS: {len(nachholen)} Dateien/Praefixe des vorigen Laufs sind nicht "
              "geloescht - dieser Lauf versucht sie erneut.")

    # 2. Tabelle je Sammlung
    zeilen, unbekannt = _plan(db, sa_id, sa_dealer_id, args.caches_leeren)
    bericht["sammlungen"] = [{"name": n, "art": a, "loeschen": l, "bleibt": b, "filter": f}
                             for n, a, l, b, f in zeilen]
    bericht["unbekannt"] = unbekannt
    print()
    print(f"{'Sammlung':<26} {'wird geloescht':>15} {'bleibt':>10}  Filter")
    for n, _a, l, b, f in zeilen:
        print(f"{n:<26} {l:>15} {b:>10}  {f}")
    if unbekannt:
        print(f"\nUNBEKANNTE Sammlungen (blockieren --ausfuehren): {', '.join(unbekannt)}")

    bezahlt = db.payment_transactions.count_documents({"payment_status": "paid"})
    manuell = db.manual_payments.count_documents({})
    bericht["zahlungen"] = {"payment_transactions_paid": bezahlt, "manual_payments": manuell}
    if bezahlt or manuell:
        print(f"\nWARNUNG: {bezahlt} bezahlte payment_transactions und {manuell} "
              "manual_payments - echte Zahlungen? pruefen, bevor geloescht wird!")

    zaehler = db.counters.find_one({"_id": "kunden_nr"}) or {}
    seq = int(zaehler.get("seq") or 0)
    bericht["zaehler"] = {"seq": seq, "naechste_nummer_mindestens": 1000 + seq + 1,
                          "nummern_ab": args.nummern_ab}
    zeile = f"\nNummernzaehler: naechste Kontonummer mindestens {1000 + seq + 1}"
    if args.nummern_ab:
        zeile += f" (mit --nummern-ab: mindestens {max(1000 + seq + 1, args.nummern_ab)})"
    print(zeile)

    # 3. Dateien (Probelauf: nur zaehlen)
    praefixe = logos = keys = []
    if args.ohne_dateien:
        print("\nDateien: --ohne-dateien, Speicher wird nicht angefasst.")
        bericht["dateien"] = {"uebersprungen": True}
    else:
        praefixe, logos, keys, ziel_fehler = _datei_ziele(db, sa_dealer_id)
        zaehlung = {}
        zaehle = getattr(speicher, "zaehle_prefix", None)

        def _zaehlen(p):
            if zaehle is None:
                return None
            try:
                return int(zaehle(p))
            except Exception as exc:  # noqa: BLE001
                print(f"  Zaehlung {p} nicht moeglich: {exc}")
                return None
        for p in praefixe:
            zaehlung[p] = _zaehlen(p)
        logo_n = [_zaehlen(p) for p in logos]
        logo_summe = None if any(x is None for x in logo_n) else sum(logo_n)
        bericht["dateien"] = {"praefixe": zaehlung,
                              "logo": {"firmen": len(logos), "dateien": logo_summe},
                              "logo_bleibt": f"logo/{sa_dealer_id}/" if sa_dealer_id else None,
                              "snapshot_keys": len(keys), "ziel_fehler": ziel_fehler}
        print("\nDateien (werden geloescht):")
        for p in praefixe:
            print(f"  {p:<24} {('?' if zaehlung[p] is None else zaehlung[p]):>8} Dateien")
        print(f"  {'logo/<firma>/':<24} {('?' if logo_summe is None else logo_summe):>8} "
              f"Dateien ({len(logos)} Firmen; logo/{sa_dealer_id or '-'}/ bleibt)")
        print(f"  {'Snapshots':<24} {len(keys):>8} referenzierte Keys (listing_snapshots)")

    if not args.ausfuehren:
        print("\nPROBELAUF - nichts geaendert. Ausfuehren mit "
              f"--ausfuehren --bestaetige {db.name}")
        _bericht_schreiben()
        return 0

    # 4. Ausfuehren — Sperren und Bestaetigung
    if unbekannt:
        print(f"\nABBRUCH: unbekannte Sammlungen {', '.join(unbekannt)} - erst klaeren "
              "(Liste im Skript ergaenzen). Nichts geaendert.")
        return 3
    if args.bestaetige != db.name:
        print(f"\nABBRUCH: --bestaetige muss exakt '{db.name}' lauten. Nichts geaendert.")
        return 4
    if not args.ja:
        try:
            eingabe = input(f"\nAlle Testdaten in '{db.name}' loeschen? "
                            f"Zum Bestaetigen {BESTAETIGUNG} eintippen: ")
        except EOFError:
            eingabe = ""
        if (eingabe or "").strip() != BESTAETIGUNG:
            print("ABBRUCH: nicht bestaetigt. Nichts geaendert.")
            return 4

    jetzt = _jetzt()
    if fortsetzung:
        db.system_flags.update_one(
            {"_id": FLAG_ID},
            {"$set": {"fortgesetzt_am": jetzt, "sa_id": sa_id}, "$inc": {"laeufe": 1}})
    else:
        # voriger abgeschlossener Lauf bleibt als Verlauf erhalten (max. 10)
        vorige = list(flag.get("vorige_laeufe") or [])
        if flag:
            vorige.append({k: v for k, v in flag.items() if k not in ("_id", "vorige_laeufe")})
        db.system_flags.replace_one(
            {"_id": FLAG_ID},
            {"_id": FLAG_ID, "status": "laeuft", "gestartet_am": jetzt, "db": db.name,
             "sa_id": sa_id, "laeufe": 1, "dateien_fertig": False, "stats": {},
             "datei_fehler": [], "nachholen": nachholen, "vorige_laeufe": vorige[-10:]},
            upsert=True)

    # 5. Dateien — vor den Dokumenten
    datei_fehler = []
    if not args.ohne_dateien:
        if (db.system_flags.find_one({"_id": FLAG_ID}) or {}).get("dateien_fertig"):
            print("\nDateien: im abgebrochenen Lauf schon erledigt - uebersprungen.")
        else:
            ziel_fehler = bericht["dateien"].get("ziel_fehler") or []
            datei_fehler.extend(ziel_fehler)
            schon = {f.get("ziel") for f in ziel_fehler}
            nach_praefixe = [f.get("ziel") for f in nachholen
                             if f.get("art") == "prefix" and f.get("ziel") not in schon]
            nach_keys = [f.get("ziel") for f in nachholen if f.get("art") == "snapshot"]
            inc = {}
            for p in dict.fromkeys(nach_praefixe + list(praefixe) + list(logos)):
                art = p.split("/", 1)[0]
                try:
                    n = int(speicher.delete_prefix(p) or 0)
                except Exception as exc:  # noqa: BLE001
                    datei_fehler.append({"art": "prefix", "ziel": p, "fehler": str(exc)[:300]})
                    continue
                inc[f"stats.dateien.{art}"] = inc.get(f"stats.dateien.{art}", 0) + n
            snap_ok = 0
            for k in dict.fromkeys(nach_keys + list(keys)):
                try:
                    ok = bool(snapshot_loescher(k))
                    fehler = None if ok else "Loeschen meldet Fehlschlag"
                except Exception as exc:  # noqa: BLE001
                    ok, fehler = False, str(exc)[:300]
                if ok:
                    snap_ok += 1
                else:
                    datei_fehler.append({"art": "snapshot", "ziel": k, "fehler": fehler})
            inc["stats.dateien.snapshots"] = snap_ok
            aenderung = {"$set": {"dateien_fertig": True}, "$inc": inc}
            if datei_fehler:
                aenderung["$push"] = {"datei_fehler": {"$each": datei_fehler, "$slice": -500}}
            db.system_flags.update_one({"_id": FLAG_ID}, aenderung)
            print(f"\nDateien geloescht: {inc}")

    # 6. Dokumente — Nebendaten, dann dealers und users zuletzt
    reihenfolge = [(n, _leeren_filter(n)) for n in GANZ_LEEREN]
    reihenfolge.append(("subscriptions", _teil_filter("subscriptions", sa_id, sa_dealer_id)))
    if args.caches_leeren:
        reihenfolge += [(n, {}) for n in CACHES]
    reihenfolge.append(("dealers", _teil_filter("dealers", sa_id, sa_dealer_id)))
    reihenfolge.append(("users", _teil_filter("users", sa_id, sa_dealer_id)))
    vorhanden = set(db.list_collection_names())
    for name, filt in reihenfolge:
        if name not in vorhanden:
            continue
        n = _leeren(db, name, filt)
        db.system_flags.update_one({"_id": FLAG_ID}, {"$inc": {f"stats.geloescht.{name}": n}})
        print(f"  {name:<26} {n:>10} geloescht")

    # 7. Super-Admin: frische Anmeldung (MFA und login_ips_bekannt bleiben)
    db.users.update_one({"id": sa_id}, {"$set": {"current_session_id": None}})
    if args.nummern_ab:
        db.counters.update_one({"_id": "kunden_nr"},
                               {"$max": {"seq": args.nummern_ab - 1001}}, upsert=True)

    # 8. Abschluss
    flag_doc = db.system_flags.find_one({"_id": FLAG_ID}) or {}
    stats = flag_doc.get("stats") or {}
    alle_fehler = list(flag_doc.get("datei_fehler") or [])
    abschluss = {"$set": {"status": "fertig", "beendet_am": _jetzt(),
                          "nummern_ab": args.nummern_ab}}
    if not flag_doc.get("dateien_fertig") and flag_doc.get("nachholen"):
        # --ohne-dateien: die offenen Ziele des vorigen Laufs bleiben offen
        alle_fehler += flag_doc["nachholen"]
        abschluss["$push"] = {"datei_fehler": {"$each": flag_doc["nachholen"], "$slice": -500}}
    db.system_flags.update_one({"_id": FLAG_ID}, abschluss)
    db.activity_logs.insert_one({
        "id": str(uuid.uuid4()), "dealer_id": "", "user_id": "system",
        "action": "system.live_reset",
        "meta": {"geloescht": stats.get("geloescht") or {},
                 "dateien": stats.get("dateien") or {},
                 "datei_fehler": len(alle_fehler), "nummern_ab": args.nummern_ab,
                 "fortsetzung": fortsetzung, "quelle": "scripts/live_reset.py"},
        "created_at": _jetzt(),
    })
    bericht["ergebnis"] = {"geloescht": stats.get("geloescht") or {},
                           "dateien": stats.get("dateien") or {},
                           "datei_fehler": alle_fehler,
                           "zaehler_seq": int((db.counters.find_one({"_id": "kunden_nr"}) or {})
                                              .get("seq") or 0)}
    _bericht_schreiben()
    if alle_fehler:
        print(f"\nFERTIG (Datenbank), aber {len(alle_fehler)} Dateien/Praefixe NICHT "
              "geloescht - Liste in system_flags.live_reset.datei_fehler; ein erneuter "
              "Aufruf versucht sie erneut:")
        for f in alle_fehler[:50]:
            print(f"  {f.get('art', '?'):<9} {f.get('ziel', '?')}  ({f.get('fehler') or '-'})")
        if len(alle_fehler) > 50:
            print(f"  ... {len(alle_fehler) - 50} weitere")
        return 5
    print("\nFERTIG: nur der Super-Admin und die Systemdaten sind geblieben. "
          "Super-Admin neu anmelden (Benutzername + 2FA).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
