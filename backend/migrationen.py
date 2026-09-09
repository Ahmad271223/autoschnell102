# -*- coding: utf-8 -*-
"""Versionierte Datenbank-Migrationen mit Sperre (Audit 09/2026, Punkt 18).

Vorher fuehrten ALLE Worker-Prozesse Index-Anlage, Seeds und Backfills
gleichzeitig aus (bis zu 8x), Fehler wurden nur protokolliert. Jetzt:
- genau EIN Prozess uebernimmt (Mongo-Sperre "migration"), die anderen
  warten, bis `system_flags._id="schema"` die Zielversion traegt;
- jede Datenmigration ist nummeriert, idempotent und wird in
  `schema_migrations` festgehalten;
- in Produktion bricht ein Fehler den Start ab (fail-closed), lokal wird
  gewarnt.

Aufruf im Container VOR den Web-Workern: `python migrationen.py`
(Dockerfile CMD) — und zusaetzlich beim App-Start als Absicherung.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import datetime, timezone

log = logging.getLogger("autohandel.migrationen")

ZIEL_VERSION = 6
_SPERRE = "migration"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ist_prod() -> bool:
    return os.environ.get("APP_ENV", "").strip().lower() == "production"


# ---------------------------------------------------------------------
# Datenmigrationen (nummeriert, idempotent)
# ---------------------------------------------------------------------
async def m1_abos_normalisieren(db) -> dict:
    """Abo-Audit: naive Ablaufdaten -> UTC, unbekannte Plaene sperren,
    alte firmenweite Abos dem Chef persoenlich zuordnen (eine zentrale
    Aufloesung fuer Anzeige, Zugriff und Abrechnung)."""
    from deps import ABO_PLAENE_ERLAUBT
    stats = {"tz_ergaenzt": 0, "plan_ungueltig": 0, "firmenweit_zugeordnet": 0}
    async for sub in db.subscriptions.find(
            {"expires_at": {"$type": "string"}}, {"_id": 0, "id": 1, "expires_at": 1}):
        ea = sub.get("expires_at") or ""
        try:
            dt = datetime.fromisoformat(ea.replace("Z", "+00:00"))
        except ValueError:
            continue
        if dt.tzinfo is None:
            neu = dt.replace(tzinfo=timezone.utc).isoformat()
            await db.subscriptions.update_one({"id": sub["id"]},
                                              {"$set": {"expires_at": neu}})
            stats["tz_ergaenzt"] += 1
    r = await db.subscriptions.update_many(
        {"plan": {"$nin": sorted(ABO_PLAENE_ERLAUBT)}, "status": {"$in": ["active", "cancelled"]}},
        {"$set": {"status": "ungueltig", "migration_hinweis": "unbekannter Plan (m1)"}})
    stats["plan_ungueltig"] = r.modified_count
    async for sub in db.subscriptions.find(
            {"$or": [{"subject_user_id": {"$exists": False}}, {"subject_user_id": None}],
             "status": {"$in": ["active", "cancelled"]}},
            {"_id": 0, "id": 1, "dealer_id": 1}):
        chef = await db.users.find_one({"dealer_id": sub.get("dealer_id"), "role": "dealer"},
                                       {"_id": 0, "id": 1})
        if not chef:
            continue
        await db.subscriptions.update_one(
            {"id": sub["id"]},
            {"$set": {"subject_user_id": chef["id"], "migriert_von": "firmenweit",
                      "updated_at": _now()}})
        stats["firmenweit_zugeordnet"] += 1
    return stats


async def m2_lifecycle(db) -> dict:
    from lifecycle import migrate_missing_lifecycles
    n = await migrate_missing_lifecycles()
    return {"fahrzeuge": n}


async def m3_kundennummern(db) -> dict:
    from deps import kunden_nummern_nachziehen
    return {"nummern": await kunden_nummern_nachziehen()}


def _konto_pruefer(db):
    """Runde 17 (Migrations-Befunde 2-4): ein Kandidat zaehlt nur, wenn das
    Konto noch zur Firma gehoert, aktiv ist und Chef oder Sucher ist —
    vorher reichte irgendein users-Dokument, auch ein deaktiviertes."""
    cache: dict = {}

    async def gueltig(uid, did) -> bool:
        if not isinstance(uid, str) or not uid or not did:
            return False
        k = (uid, did)
        if k not in cache:
            cache[k] = await db.users.count_documents(
                {"id": uid, "dealer_id": did, "role": {"$in": ["sucher", "dealer"]},
                 "active": {"$ne": False}}, limit=1) > 0
        return cache[k]
    return gueltig


async def _besitzer_ermitteln(db, v: dict, gueltig, chefs: dict):
    """Besitzer eines Fahrzeugs aus der Historie: aeltester GUELTIGER Vertrag
    -> aeltester gueltiger Vergleich -> Aktivitaet -> aeltester gueltiger
    Termin -> Chef. Runde 17 (Befunde 2/3): je Quelle werden die Kandidaten
    der Reihe nach durchgegangen — vorher entschied nur der ALLERaelteste
    Datensatz, und war dessen Konto ausgeschieden, fiel die ganze Quelle weg."""
    vid, did, ad = v["id"], v.get("dealer_id"), v.get("mobile_ad_id")
    quellen = [
        ("vertrag", db.generated_pdfs, {"vehicle_id": vid, "dealer_id": did}, "user_id"),
        ("vergleich", db.vehicle_comparisons,
         {"mobile_ad_id": ad, "dealer_id": did} if ad else None, "user_id"),
        ("aktivitaet", db.activity_logs,
         {"action": "vergleich.gestartet", "ref": ad, "dealer_id": did} if ad else None, "user_id"),
        ("termin", db.appointments, {"vehicle_id": vid, "dealer_id": did}, "created_by"),
    ]
    for quelle, coll, filt, feld in quellen:
        if not filt:
            continue
        kandidaten = await coll.find(filt, {"_id": 0, feld: 1}).sort("created_at", 1).to_list(50)
        for d in kandidaten:
            if await gueltig(d.get(feld), did):
                return d[feld], quelle
    if did not in chefs:
        chef = await db.users.find_one({"dealer_id": did, "role": "dealer", "active": {"$ne": False}},
                                       {"_id": 0, "id": 1})
        chefs[did] = (chef or {}).get("id")
    if chefs[did]:
        return chefs[did], "chef"
    return None, None


async def m4_fahrzeug_besitzer(db) -> dict:
    """Runde 16 (Beschluss 08.09.2026): owner_user_id fuer den Altbestand
    (organisatorischer Bearbeiter im Pool). Idempotent: nur Dokumente ohne
    owner_user_id. Fahrzeuge ohne zuordenbares Konto ("offen") bleiben fuer
    den Chef sichtbar, der sie zuweisen kann — seit dem Umbau Kaufvorgaenge
    (09.09.2026) entscheidet der Besitzer nicht mehr ueber Vertraege."""
    stats = {"vertrag": 0, "vergleich": 0, "aktivitaet": 0, "termin": 0,
             "chef": 0, "offen": 0}
    gueltig = _konto_pruefer(db)
    chefs: dict = {}
    async for v in db.vehicles.find(
            {"$or": [{"owner_user_id": {"$exists": False}}, {"owner_user_id": None}]},
            {"_id": 0, "id": 1, "dealer_id": 1, "mobile_ad_id": 1}):
        uid, quelle = await _besitzer_ermitteln(db, v, gueltig, chefs)
        if not uid:
            stats["offen"] += 1
            continue
        await db.vehicles.update_one(
            {"id": v["id"], "dealer_id": v.get("dealer_id")},
            {"$set": {"owner_user_id": uid, "besitzer_migriert_von": quelle}})
        stats[quelle] += 1
    # Die offen gebliebenen Fahrzeuge haben weiterhin keinen Besitzer und
    # werden im Helfer direkt aus der Datenbank gezaehlt.
    await _offene_besitzer_melden(db, 0)
    return stats


_TERMIN_ZU_VORGANG = {"abgeholt": "abgeholt", "erledigt": "abgeholt",
                      "nicht abgeholt": "nicht_abgeholt", "storniert": "storniert"}


async def m5_kaufvorgaenge(db) -> dict:
    """Umbau Kaufvorgaenge (09.09.2026): fuer jeden bestehenden Vertrag ohne
    kaufvorgang_id EINEN Vorgang anlegen (Sucher, Fahrzeug, Kaufpreis,
    Status aus Vertrag/Termin, Termin verknuepfen). Der Vertragsersteller
    wird Mitbearbeiter des Fahrzeugs, wenn er nicht der Besitzer ist —
    das Fahrzeug bleibt so in seinem Bereich. Idempotent."""
    import uuid as _uuid
    stats = {"vorgaenge": 0, "termine_verknuepft": 0, "uebersprungen": 0}
    async for c in db.generated_pdfs.find(
            {"$or": [{"kaufvorgang_id": {"$exists": False}}, {"kaufvorgang_id": None}]},
            {"_id": 0, "id": 1, "dealer_id": 1, "user_id": 1, "vehicle_id": 1,
             "purchase_price": 1, "status": 1, "appointment_id": 1, "created_at": 1}):
        if not c.get("vehicle_id") or not c.get("dealer_id"):
            stats["uebersprungen"] += 1
            continue
        appt = None
        if c.get("appointment_id"):
            appt = await db.appointments.find_one({"id": c["appointment_id"]},
                                                  {"_id": 0, "id": 1, "status": 1})
        if not appt:
            appt = await db.appointments.find_one(
                {"contract_id": c["id"], "dealer_id": c["dealer_id"]},
                {"_id": 0, "id": 1, "status": 1}, sort=[("created_at", -1)])
        if appt:
            status = _TERMIN_ZU_VORGANG.get(appt.get("status") or "offen", "abholung_geplant")
        elif (c.get("status") or "") in ("versendet", "versand_vorbereitet"):
            status = "gesendet"
        else:
            status = "vertrag_erstellt"
        kv_id = str(_uuid.uuid4())
        doc = {"id": kv_id, "dealer_id": c["dealer_id"], "user_id": c.get("user_id"),
               "vehicle_id": c["vehicle_id"], "contract_id": c["id"],
               "purchase_price": c.get("purchase_price"), "status": status,
               "appointment_id": (appt or {}).get("id"),
               "created_at": c.get("created_at") or _now(), "updated_at": _now(),
               "migriert": True}
        try:
            await db.kaufvorgaenge.insert_one(doc)
        except Exception:
            alt = await db.kaufvorgaenge.find_one({"contract_id": c["id"]}, {"_id": 0, "id": 1})
            if not alt:
                raise
            kv_id = alt["id"]
        # Runde 18: Termin- und Mitbearbeiter-Verknuepfung VOR dem Merker am
        # Vertrag — die kaufvorgang_id ist das Fertig-Kennzeichen. Brach die
        # Migration frueher dazwischen ab, uebersprang die Wiederholung den
        # Vertrag und die Verknuepfungen fehlten dauerhaft.
        if appt:
            await db.appointments.update_one({"id": appt["id"]}, {"$set": {"kaufvorgang_id": kv_id}})
            stats["termine_verknuepft"] += 1
        if c.get("user_id"):
            await db.vehicles.update_one(
                {"id": c["vehicle_id"], "dealer_id": c["dealer_id"],
                 "owner_user_id": {"$ne": c["user_id"]}},
                {"$addToSet": {"mitbearbeiter_ids": c["user_id"]}})
        await db.generated_pdfs.update_one({"id": c["id"]}, {"$set": {"kaufvorgang_id": kv_id}})
        stats["vorgaenge"] += 1
    return stats


async def m6_besitzer_nachbessern(db) -> dict:
    """Runde 17 (Migrations-Befund 4): Fahrzeuge, deren Besitzer inzwischen
    kein aktives Chef-/Sucher-Konto der Firma mehr ist, bekommen ueber die
    verbesserte Heuristik einen gueltigen Besitzer (sonst sah kein aktiver
    Sucher das Fahrzeug, bis der Chef es zuwies). Idempotent."""
    stats = {"nachgebessert": 0, "offen": 0, "in_ordnung": 0}
    gueltig = _konto_pruefer(db)
    chefs: dict = {}
    async for v in db.vehicles.find({"owner_user_id": {"$type": "string"}},
                                    {"_id": 0, "id": 1, "dealer_id": 1, "mobile_ad_id": 1,
                                     "owner_user_id": 1}):
        if await gueltig(v.get("owner_user_id"), v.get("dealer_id")):
            stats["in_ordnung"] += 1
            continue
        uid, quelle = await _besitzer_ermitteln(db, v, gueltig, chefs)
        if not uid:
            stats["offen"] += 1
            continue
        await db.vehicles.update_one(
            {"id": v["id"], "dealer_id": v.get("dealer_id")},
            {"$set": {"owner_user_id": uid, "besitzer_migriert_von": f"nachgebessert:{quelle}",
                      "besitzer_vorher": v.get("owner_user_id")}})
        stats["nachgebessert"] += 1
    # Runde 18: m6 sieht nur Fahrzeuge MIT (ungueltigem) Besitzer. Die von m4
    # offen gelassenen Fahrzeuge OHNE Besitzer zaehlt der Helfer selbst aus
    # der Datenbank — vorher schloss m6 den Alarm von m4 wieder, obwohl
    # weiterhin besitzerlose Fahrzeuge existierten.
    await _offene_besitzer_melden(db, stats["offen"])
    return stats


OHNE_BESITZER = {"$or": [{"owner_user_id": {"$exists": False}},
                         {"owner_user_id": None}, {"owner_user_id": ""}]}


async def _offene_besitzer_melden(db, ungueltige: int = 0) -> None:
    """Runde 17 (Migrations-Befund 5): Fahrzeuge ohne zuordenbaren Besitzer
    duerfen nicht still bleiben — als Betriebsalarm sichtbar (/admin/betrieb),
    der Chef weist sie in der Akte zu. Kein Startabbruch: seit dem Umbau
    Kaufvorgaenge ist der Besitzer nur organisatorisch.

    Runde 18: Gesamtzahl = Fahrzeuge OHNE Besitzer (immer frisch aus der
    Datenbank gezaehlt) + `ungueltige` (Besitzer-ID zeigt auf kein aktives
    Konto und liess sich nicht ersetzen — nur m6 kennt diese Zahl)."""
    from betrieb import alarm, alarm_schliessen
    ohne = await db.vehicles.count_documents(OHNE_BESITZER)
    anzahl = ohne + max(0, int(ungueltige or 0))
    if anzahl > 0:
        await alarm(db, "fahrzeuge_ohne_besitzer", ref="vehicles", anzahl=anzahl,
                    ohne_besitzer=ohne, ungueltiger_besitzer=int(ungueltige or 0))
    else:
        await alarm_schliessen(db, "fahrzeuge_ohne_besitzer", ref="vehicles")


MIGRATIONEN = [
    (1, "abos_normalisieren", m1_abos_normalisieren),
    (2, "lifecycle_nachziehen", m2_lifecycle),
    (3, "kundennummern", m3_kundennummern),
    (4, "fahrzeug_besitzer", m4_fahrzeug_besitzer),
    (5, "kaufvorgaenge", m5_kaufvorgaenge),
    (6, "besitzer_nachbessern", m6_besitzer_nachbessern),
]


# ---------------------------------------------------------------------
# Ablauf
# ---------------------------------------------------------------------
async def aktuelle_version(db) -> int:
    doc = await db.system_flags.find_one({"_id": "schema"})
    return int((doc or {}).get("version") or 0)


async def _sperre_holen(db) -> bool:
    from job_lock import acquire
    return await acquire(db, _SPERRE, ttl_seconds=600)


async def _sperre_loesen(db) -> None:
    from job_lock import release
    await release(db, _SPERRE)


async def ausfuehren(db, indexe=None, seeds=()) -> dict:
    """Als Leader: Indizes, Seeds, Datenmigrationen — in dieser Reihenfolge."""
    if indexe is not None:
        await indexe()
    for seed in seeds:
        await seed()
    stand = await aktuelle_version(db)
    erledigt = {}
    for nr, name, fn in MIGRATIONEN:
        if nr <= stand:
            continue
        log.info("Migration %d (%s) laeuft ...", nr, name)
        stats = await fn(db)
        await db.schema_migrations.update_one(
            {"version": nr},
            {"$set": {"version": nr, "name": name, "applied_at": _now(),
                      "stats": stats}}, upsert=True)
        await db.system_flags.update_one(
            {"_id": "schema"}, {"$set": {"version": nr, "updated_at": _now()}},
            upsert=True)
        erledigt[name] = stats
        log.info("Migration %d (%s) fertig: %s", nr, name, stats)
    await db.system_flags.update_one(
        {"_id": "schema"},
        {"$set": {"version": max(stand, ZIEL_VERSION), "updated_at": _now(),
                  "letzter_start": _now()}}, upsert=True)
    return erledigt


async def ausfuehren_oder_warten(db, indexe=None, seeds=(), warte_sekunden: int = 180) -> str:
    """Genau ein Prozess migriert; die anderen warten auf die Zielversion.
    Rueckgabe: "leader" | "gewartet" | "timeout"."""
    if await _sperre_holen(db):
        try:
            await ausfuehren(db, indexe=indexe, seeds=seeds)
            return "leader"
        except Exception:
            log.exception("Migration fehlgeschlagen")
            if _ist_prod():
                log.error("Start ABGEBROCHEN: Migration fehlgeschlagen (fail-closed)")
                raise SystemExit(78)
            return "fehler"
        finally:
            await _sperre_loesen(db)
    # Kein Leader: warten, bis die Zielversion erreicht ist
    for _ in range(max(1, warte_sekunden)):
        if await aktuelle_version(db) >= ZIEL_VERSION:
            # Indizes sind idempotent — zur Sicherheit auch hier anlegen
            # (z.B. wenn der Leader ein aelterer Prozess war).
            if indexe is not None:
                try:
                    await indexe()
                except Exception as exc:
                    log.warning("Index-Anlage im Wartenden fehlgeschlagen: %s", exc)
            return "gewartet"
        await asyncio.sleep(1)
    log.error("Migration nicht innerhalb von %ds abgeschlossen", warte_sekunden)
    if _ist_prod():
        raise SystemExit(78)
    return "timeout"


def _main() -> int:
    """CLI: `python migrationen.py` — laeuft VOR den Web-Workern."""
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s - %(levelname)s - %(message)s")

    # Audit 09/2026: Die strenge Produktionspruefung MUSS vor jeder
    # Datenbankaenderung laufen. Das Image startet erst migrationen.py
    # (Indizes + Seeds) und danach uvicorn — eine unsichere Konfiguration
    # haette also bereits Migrationen und Seeds ausgefuehrt, bevor der
    # Serverstart abbricht.
    from production_check import pruefe_produktion
    pruefe_produktion(log)

    async def lauf():
        import server  # registriert ensure_indexes/seeds
        from deps import db
        ergebnis = await ausfuehren_oder_warten(
            db, indexe=server.ensure_indexes,
            seeds=(server.seed_admin, server.seed_super_admin), warte_sekunden=300)
        log.info("Migration: %s (Version %d)", ergebnis, await aktuelle_version(db))
        return 0 if ergebnis in ("leader", "gewartet") else 1

    return asyncio.run(lauf())


if __name__ == "__main__":
    sys.exit(_main())
