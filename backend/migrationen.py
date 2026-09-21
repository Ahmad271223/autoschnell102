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

ZIEL_VERSION = 9
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


async def m7_kaeuferdaten_einfrieren(db) -> dict:
    """Runde 25 (12.09.2026, Pruefbefund "Kaeuferdaten nicht unveraenderlich"):

    Vertraege, deren contract_data keine Kaeuferfelder tragen (Altvertraege,
    Anlage ueber die API), holten Firma/Anschrift des Kaeufers spaeter erneut
    aus den HEUTIGEN Einstellungen — Abholprotokoll und neue Vertragsfassung
    konnten dadurch von der urspruenglichen Ausfertigung abweichen.

    Diese Migration friert den heute gueltigen Stand EINMALIG ein (bestmoeglich:
    der urspruengliche Stand steht nur im bereits erzeugten PDF, das unveraendert
    bleibt). Gesetzt werden nur fehlende Felder; der Vertrag bekommt die Marke
    kaeufer_nachtraeglich_eingefroren. Idempotent.
    """
    from routes.contracts import KAEUFER_FELDER

    stats = {"eingefroren": 0, "schon_gesetzt": 0, "ohne_quelle": 0}
    dealers: dict = {}

    async def firma_doc(dealer_id):
        f = dealers.get(dealer_id)
        if f is None:
            f = await db.dealers.find_one({"id": dealer_id}, {"_id": 0}) or {}
            dealers[dealer_id] = f
        return f

    async def konto_basis(user_id, dealer_id):
        """Wie deps.effective_dealer (ohne an deps.db zu haengen) — None,
        wenn es das Konto in dieser Firma nicht (mehr) gibt."""
        if not user_id:
            return None
        u = await db.users.find_one(
            {"id": user_id, "dealer_id": dealer_id},
            {"_id": 0, "role": 1, "settings_override": 1})
        if not u:
            return None
        firma = await firma_doc(dealer_id)
        if u.get("role") != "sucher":
            return dict(firma)
        from deps import SUCHER_SETTINGS_FIELDS
        merged = dict(firma)
        for k, v in (u.get("settings_override") or {}).items():
            if k in SUCHER_SETTINGS_FIELDS and v is not None:
                merged[k] = v
        return merged

    async for doc in db.generated_pdfs.find(
            {}, {"_id": 0, "id": 1, "dealer_id": 1, "user_id": 1, "contract_data": 1}):
        daten = doc.get("contract_data")
        if not isinstance(daten, dict):
            continue
        fehlend = [f for f in KAEUFER_FELDER
                   if not str(daten.get(f) or "").strip()]
        if not fehlend:
            stats["schon_gesetzt"] += 1
            continue
        # Gegenpruefung Runde 25: dieselbe Kette wie auftraggeber.py —
        # Vertrags-Ersteller, sonst Termin-Ersteller, sonst Firma. Ohne den
        # mittleren Schritt schrieb die Migration bei Altvertraegen ohne
        # Ersteller die Firmendaten fest, obwohl das Abholprotokoll bis
        # dahin die Sucher-Daten zeigte.
        dealer_id = doc.get("dealer_id")
        quelle = await konto_basis(doc.get("user_id"), dealer_id)
        if quelle is None:
            termin = await db.appointments.find_one(
                {"contract_id": doc["id"], "dealer_id": dealer_id},
                {"_id": 0, "created_by": 1}) or {}
            quelle = await konto_basis(termin.get("created_by"), dealer_id)
        if quelle is None:
            quelle = await firma_doc(dealer_id)
        neu = {}
        for feld in fehlend:
            wert = quelle.get(KAEUFER_FELDER[feld])
            wert = str(wert).strip() if wert is not None else ""
            if wert:
                neu[f"contract_data.{feld}"] = wert
        if not neu:
            # Nichts nachzutragen: entweder stehen die Kaeuferdaten schon im
            # Vertrag (es fehlen nur Felder, die auch in den Einstellungen
            # leer sind, z. B. WhatsApp) oder es gibt gar keine Quelle.
            leer = len(fehlend) == len(KAEUFER_FELDER)
            stats["ohne_quelle" if leer else "schon_gesetzt"] += 1
            continue
        neu["contract_data.kaeufer_nachtraeglich_eingefroren"] = True
        await db.generated_pdfs.update_one({"id": doc["id"]}, {"$set": neu})
        stats["eingefroren"] += 1
    return stats


async def m8_konten_aktiv_feld(db) -> dict:
    """Nachpruefung 15.09.2026 (Anmeldung, Legacy-active): ein Konto OHNE das
    Feld `active` galt bei users als gesperrt (Login: `if not active`) und bei
    driver_accounts als aktiv (`get("active", True)`). Beide Bedeutungen werden
    festgeschrieben — danach traegt jedes Konto das Feld ausdruecklich, und
    Restore/Import erzeugen keine stillen Sonderfaelle mehr."""
    u = await db.users.update_many({"active": {"$exists": False}}, {"$set": {"active": False}})
    d = await db.driver_accounts.update_many({"active": {"$exists": False}},
                                             {"$set": {"active": True}})
    return {"users_gesperrt": u.modified_count, "fahrer_aktiv": d.modified_count}


async def m9_chef_zeiger(db) -> dict:
    """Pruefbericht 20.09.2026 (N3): `dealers.user_id` zeigt auf den EINEN
    Hauptchef der Firma. Daran haengen current_chef, ist_haupt_chef und seit
    dem 20.09. auch die Einnordung in current_firma — wer nicht der
    eingetragene Chef ist, arbeitet als Sucher.

    Bei Altbestand fehlt der Zeiger. Dann galt die Ersatzregel "aeltestes
    dealer-Konto", die zwar richtig entscheidet, aber bei JEDER Anfrage eine
    zusaetzliche Abfrage kostet — und solange sie greift, bleibt ein zweites
    altes dealer-Konto unentdeckt, wenn es zufaellig das aelteste ist.

    Diese Migration traegt den Zeiger einmalig nach: je Firma ohne
    `user_id` das aelteste Konto mit role="dealer". Firmen ohne ein einziges
    dealer-Konto bleiben unberuehrt (die haben ein groesseres Problem, das
    hier nicht still repariert werden soll) und werden gezaehlt.
    """
    gesetzt = ohne_chef = 0
    async for firma in db.dealers.find(
            {"$or": [{"user_id": {"$exists": False}}, {"user_id": None},
                     {"user_id": ""}]},
            {"_id": 0, "id": 1}):
        chef = await db.users.find_one(
            {"dealer_id": firma["id"], "role": "dealer"},
            {"_id": 0, "id": 1}, sort=[("created_at", 1)])
        if not chef:
            ohne_chef += 1
            continue
        res = await db.dealers.update_one(
            {"id": firma["id"],
             "$or": [{"user_id": {"$exists": False}}, {"user_id": None},
                     {"user_id": ""}]},
            {"$set": {"user_id": chef["id"]}})
        gesetzt += res.modified_count
    return {"chef_zeiger_gesetzt": gesetzt, "firmen_ohne_chef": ohne_chef}


MIGRATIONEN = [
    (1, "abos_normalisieren", m1_abos_normalisieren),
    (2, "lifecycle_nachziehen", m2_lifecycle),
    (3, "kundennummern", m3_kundennummern),
    (4, "fahrzeug_besitzer", m4_fahrzeug_besitzer),
    (5, "kaufvorgaenge", m5_kaufvorgaenge),
    (6, "besitzer_nachbessern", m6_besitzer_nachbessern),
    (7, "kaeuferdaten_einfrieren", m7_kaeuferdaten_einfrieren),
    (8, "konten_aktiv_feld", m8_konten_aktiv_feld),
    (9, "chef_zeiger", m9_chef_zeiger),
]


# ---------------------------------------------------------------------
# Ablauf
# ---------------------------------------------------------------------
async def aktuelle_version(db) -> int:
    doc = await db.system_flags.find_one({"_id": "schema"})
    return int((doc or {}).get("version") or 0)


# Runde 8 (15.09.2026, Liste 3 Nr. 12/13): Die Sperre lief nach 600 s ab, ohne
# dass eine laufende Migration sie verlaengerte; und ein wartender zweiter
# Server gab nach 300 s auf. Jetzt haelt der Leader die Sperre per Heartbeat,
# und Wartende bleiben dran, solange ein lebender Leader die Sperre haelt.
_SPERRE_TTL_S = 600
_HEARTBEAT_S = 30
_WARTEN_MAX_S = 4 * 3600


_TOKEN = None      # Besitzer-Token der Migrations-Sperre (Phase 3, 3.1)


async def _sperre_holen(db) -> bool:
    global _TOKEN
    from job_lock import acquire
    _TOKEN = await acquire(db, _SPERRE, ttl_seconds=_SPERRE_TTL_S)
    return bool(_TOKEN)


async def _sperre_verlaengern(db) -> bool:
    from job_lock import verlaengern
    return await verlaengern(db, _SPERRE, ttl_seconds=_SPERRE_TTL_S, token=_TOKEN)


async def _sperre_gehalten(db) -> bool:
    from job_lock import gehalten
    return await gehalten(db, _SPERRE)


class SperreVerloren(RuntimeError):
    """Die Migrations-Sperre gehoert jetzt einem anderen Prozess (Nr. 35)."""


async def _heartbeat(db, stop: "asyncio.Event", wache: "_Wache") -> None:
    """Verlaengert die Migrations-Sperre alle _HEARTBEAT_S Sekunden, bis stop
    gesetzt ist.

    Nachpruefung 20.09.2026, Nr. 35: Ging die Sperre verloren, stand das nur
    im Protokoll — der alte Migrationslauf machte weiter. Nach Ablauf der
    600-s-Frist konnte ein zweiter Prozess uebernehmen, und dann liefen ZWEI
    Migrationen gleichzeitig ueber dieselben Daten. Jetzt merkt sich die
    Wache den Verlust; `ausfuehren()` sieht vor jeder einzelnen Migration
    nach und hoert auf."""
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=_HEARTBEAT_S)
            return
        except asyncio.TimeoutError:
            pass
        try:
            gehalten = await _sperre_verlaengern(db)
        except Exception:  # noqa: BLE001
            continue        # Stoerung ist kein Beweis fuer den Verlust
        if not gehalten:
            wache.verloren = True
            log.error("Migrations-Sperre konnte nicht verlaengert werden — "
                      "ein zweiter Prozess koennte parallel migrieren; der "
                      "laufende Lauf bricht ab")
            return


class _Wache:
    def __init__(self):
        self.verloren = False

    def pruefen(self) -> None:
        if self.verloren:
            raise SperreVerloren(
                "Die Migrations-Sperre ging waehrend des Laufs verloren — "
                "abgebrochen, damit nicht zwei Prozesse gleichzeitig "
                "migrieren. Der neue Besitzer der Sperre macht weiter.")


async def _sperre_loesen(db) -> None:
    from job_lock import release
    await release(db, _SPERRE, token=_TOKEN)


async def ausfuehren(db, indexe=None, seeds=(), wache=None) -> dict:
    """Als Leader: Indizes, Seeds, Datenmigrationen — in dieser Reihenfolge.

    `wache` (Nr. 35): vor jeder einzelnen Migration wird geprueft, ob die
    Sperre noch uns gehoert. Sonst bricht der Lauf ab."""
    if indexe is not None:
        await indexe()
    for seed in seeds:
        await seed()
    stand = await aktuelle_version(db)
    erledigt = {}
    for nr, name, fn in MIGRATIONEN:
        if nr <= stand:
            continue
        if wache is not None:
            wache.pruefen()
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
    verloren = False
    if await _sperre_holen(db):
        stop = asyncio.Event()
        wache = _Wache()
        herz = asyncio.ensure_future(_heartbeat(db, stop, wache))
        try:
            await ausfuehren(db, indexe=indexe, seeds=seeds, wache=wache)
            return "leader"
        except SperreVerloren as exc:
            # Nr. 35: kein Fehlschlag der Migration selbst — ein anderer
            # Prozess hat die Sperre uebernommen. Dieser Prozess faellt
            # deshalb in die Warteschleife unten: er wartet wie jeder
            # andere auf die Zielversion, statt in Produktion abzubrechen
            # oder mit halb migrierten Daten weiterzulaufen.
            log.warning("%s", exc)
            verloren = True
        except Exception:
            log.exception("Migration fehlgeschlagen")
            if _ist_prod():
                log.error("Start ABGEBROCHEN: Migration fehlgeschlagen (fail-closed)")
                raise SystemExit(78)
            return "fehler"
        finally:
            stop.set()
            try:
                await herz
            except Exception:  # noqa: BLE001
                pass
            await _sperre_loesen(db)
    if verloren:
        log.info("Migration: warte jetzt wie ein Nicht-Leader auf die "
                 "Zielversion (die Sperre gehoert einem anderen Prozess)")
    # Kein Leader: warten, bis die Zielversion erreicht ist. warte_sekunden
    # zaehlt nur, solange NIEMAND die Sperre haelt (Leader tot oder fertig,
    # Version trotzdem nicht erreicht); ein lebender Leader darf laenger
    # brauchen — bis zur harten Obergrenze _WARTEN_MAX_S.
    ohne_leader = 0
    gesamt = 0
    while True:
        if await aktuelle_version(db) >= ZIEL_VERSION:
            # Indizes sind idempotent — zur Sicherheit auch hier anlegen
            # (z.B. wenn der Leader ein aelterer Prozess war).
            if indexe is not None:
                try:
                    await indexe()
                except Exception as exc:
                    log.warning("Index-Anlage im Wartenden fehlgeschlagen: %s", exc)
            return "gewartet"
        if gesamt >= _WARTEN_MAX_S:
            break
        if await _sperre_gehalten(db):
            ohne_leader = 0
        else:
            ohne_leader += 1
            if ohne_leader >= max(1, warte_sekunden):
                break
        await asyncio.sleep(1)
        gesamt += 1
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

    async def lauf():
        # Pruefbericht 20.09.2026 (AL-13): ZUERST alle Module laden — erst dabei
        # lesen sie ihre Zahlen aus der Umgebung (konfig.zahl_env), und nur dann
        # kennt die Produktionspruefung jeden Tippfehler (vorher war
        # konfig.FEHLERHAFT hier noch leer). Das Laden aendert nichts an der
        # Datenbank — die Pruefung laeuft weiter VOR jeder Aenderung.
        try:
            import server  # registriert ensure_indexes/seeds
        except Exception:
            pruefe_produktion(log)   # klare Meldung, falls die Konfiguration schuld ist
            raise
        pruefe_produktion(log)
        import indizes
        from deps import db
        ergebnis = await ausfuehren_oder_warten(
            # SV-04: ALLE Indizes. Vorher nur ensure_indexes — protokoll-
            # version_eindeutig, zahlung_je_vorgang u. a. entstanden erst in den
            # wartenden Web-Prozessen, deren Fehler nur als Warnung im Log standen.
            db, indexe=server._alle_indexe,
            # Kontonummer (13.09.2026), Schritt 5: nur noch der Super-Admin
            seeds=(server.seed_super_admin,), warte_sekunden=300)
        log.info("Migration: %s (Version %d)", ergebnis, await aktuelle_version(db))
        if indizes.FEHLENDE_UNIQUE and _ist_prod():
            # SV-04: dieselbe Regel wie server.on_start — nur hier, BEVOR
            # uvicorn ueberhaupt startet, und mit der vollstaendigen Liste.
            log.error("Start ABGEBROCHEN: eindeutige Indizes fehlen: %s — Dubletten "
                      "bereinigen (python scripts/dubletten_pruefen.py)",
                      sorted(indizes.FEHLENDE_UNIQUE))
            return 78
        return 0 if ergebnis in ("leader", "gewartet") else 1

    return asyncio.run(lauf())


if __name__ == "__main__":
    sys.exit(_main())
