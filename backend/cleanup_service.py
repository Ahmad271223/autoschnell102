"""Hintergrund-Cleanup für Fahrzeug-Assets nach Abholung.

Regeln (Stand B2B-Modul 08/2026):
- Abholung erfolgreich (`status == "abgeholt"`): nach 7 Tagen Inserat-Fotos,
  Snapshots und Cache löschen — ABER NUR, solange der Händler noch keine
  Bestands-Entscheidung getroffen hat. Ab „Speichern"/„Weiterverkaufen"
  regelt der Fahrzeug-Lebenszyklus die Aufbewahrung.
- Nicht abgeholt (`status == "nicht abgeholt"`): nach 14 Tagen dasselbe.
- Bestand abgelaufen (`lifecycle == "bestand"` und `bestand.expires_at`
  überschritten, Standard 50 Tage): NUR Fotos + temporäre Verkaufsdaten
  (Inserats-Entwürfe) werden gelöscht; Kaufvertrag, Abholbericht,
  Einkaufspreis, Historie und Audit-Logs bleiben erhalten. Das Fahrzeug
  erhält den Status `archiviert`.

Der Termin-Eintrag selbst bleibt bestehen (Historie), wird aber mit
`assets_cleaned_at` markiert, damit der Cleanup-Job nicht zweimal läuft.
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

import auto_daten
from betrieb import alarm, alarm_schliessen
from snapshot_service import delete_object
from storage_service import loeschen_oder_vormerken

log = logging.getLogger("autohandel.cleanup")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

# (status, Tage bis Löschung)
# Nachpruefung Runde 14 (Nr. 100): "erledigt" gilt in drivers.py/protocols.py
# als abgeschlossen, fehlte hier aber — Berichts- und Inseratsfotos solcher
# Termine wurden nie ueber die Termin-Frist geloescht (nur ueber die
# 90-Tage-Regeln). Jetzt wie "abgeholt" nach 7 Tagen. Offen (Produkt-
# entscheidung, hier NICHT umgesetzt): Lifecycle-Mapping fuer "erledigt"
# in appointments.py und eine Frist fuer "storniert".
CLEANUP_RULES = (
    ("abgeholt", 7),
    ("nicht abgeholt", 14),
    ("erledigt", 7),
)

# Lebenszyklus-Status, in denen der Händler bereits entschieden hat —
# die 7-Tage-Regel greift dann NICHT mehr.
_DECIDED_STATES = {
    "bestand", "verkaufsentwurf", "verkaufsbereit",
    "veroeffentlicht", "reserviert", "verkauft", "archiviert",
}

# Intervall zwischen Durchläufen
CLEANUP_INTERVAL_SECONDS = 60 * 60  # 1×/Stunde


def _iter_photo_keys() -> Iterable[str]:
    """Die Felder im `vehicle.data`, die wir ausräumen, wenn gecleant wird."""
    return ("image_urls", "images", "photos", "pictures")


async def _delete_snapshots_for_vehicle(db, vehicle_id: str,
                                         dealer_id: str = "") -> int:
    """Löscht listing_snapshots zum Fahrzeug inkl. Storage-Blobs —
    aber NUR, wenn niemand anderes sie noch braucht.

    Fahrzeug-IDs (v_<Anzeigen-ID>) sind haendleruebergreifend identisch
    und Snapshots bewusst geteilt (nie doppelt fotografieren). Vorher
    konnte der abgelaufene Termin EINER Firma das Beweisarchiv ALLER
    Firmen zu diesem Inserat vernichten (PR-Review 09/2026). Jetzt wird
    uebersprungen, wenn (a) irgendein Kaufvertrag auf das Fahrzeug
    verweist oder (b) eine ANDERE Firma dasselbe Fahrzeug fuehrt —
    das normale Verfallsdatum (_expire_old_snapshots) raeumt dann auf."""
    if not vehicle_id:
        return 0
    vertraege = await db.generated_pdfs.count_documents(
        {"vehicle_id": vehicle_id})
    if vertraege:
        return 0
    andere_firma = {"id": vehicle_id}
    if dealer_id:
        andere_firma["dealer_id"] = {"$ne": dealer_id}
        if await db.vehicles.count_documents(andere_firma):
            return 0
    elif await db.vehicles.count_documents(andere_firma) > 1:
        return 0
    count = 0
    # Zeilen mit offener (vorgemerkter) Datei-Loeschung ueberspringen — die
    # Nachholung (storage_loeschungen_nachholen) raeumt sie ueber `ref` weg.
    async for snap in db.listing_snapshots.find(
            {"vehicle_id": vehicle_id, "loeschung_offen": {"$ne": True}},
            {"_id": 0}):
        offen = False
        for key in ("png_path", "pdf_path"):
            path = snap.get(key)
            if not path:
                continue
            ok = await loeschen_oder_vormerken(
                db, key=path, art="snapshot", grund="snapshot_fahrzeug_cleanup",
                dealer_id=dealer_id,
                ref={"collection": "listing_snapshots", "id": snap["id"],
                     "unset_fields": [key],
                     "loeschen_wenn_leer": ["png_path", "pdf_path"]})
            if ok:
                await db.listing_snapshots.update_one(
                    {"id": snap["id"]}, {"$unset": {key: ""}})
            else:
                offen = True
        await db.listings_cache.update_many(
            {"snapshot_id": snap["id"]}, {"$set": {"snapshot_id": None}})
        if offen:
            # Blob liegt noch: Zeile MIT Key behalten, nur markieren. Die
            # Zeile verschwindet erst, wenn beide Dateien wirklich weg sind
            # (Nachholung loescht sie ueber `loeschen_wenn_leer`).
            await db.listing_snapshots.update_one(
                {"id": snap["id"]}, {"$set": {"loeschung_offen": True}})
            continue
        await db.listing_snapshots.delete_one({"id": snap["id"]})
        count += 1
    return count


async def _delete_report_photos(db, appt_id: str, now: datetime, stats: dict,
                                dealer_id: str = "") -> None:
    """Alle Abweichungsfotos der Berichte EINES Termins sofort loeschen.
    Runde 21: Der stuendliche Aufraeumer nutzt diese Funktion nicht mehr —
    Fahrerfotos haben eine eigene Frist ab dem Hochladen
    (berichtsfotos_nach_frist_loeschen). Sie bleibt fuer Aufrufer, die die
    Fotos eines Termins gezielt entfernen. Der Berichtstext (Kilometerstand,
    Maengel) bleibt als Geschaeftsunterlage erhalten, nur die Bilddateien
    gehen."""
    async for rep in db.pickup_reports.find(
            {"appointment_id": appt_id},
            {"_id": 0, "id": 1, "deviations": 1}):
        await _fotos_eines_berichts_loeschen(db, rep, now, stats, dealer_id)


async def _fotos_eines_berichts_loeschen(db, rep: dict, now: datetime, stats: dict,
                                         dealer_id: str = "") -> bool:
    """Alle noch vorhandenen Fotos EINES Berichts loeschen bzw. zur
    Nachholung vormerken. Eintraege mit bereits vorgemerkter Loeschung
    (photo_loeschung_offen) uebernimmt storage_loeschungen_nachholen — sie
    werden hier nicht erneut angestossen (sonst entstuende jede Stunde ein
    weiterer Vormerk-Eintrag)."""
    devs = rep.get("deviations") or []
    rep_changed = False
    for entry in devs:
        key = entry.get("photo_key")
        if not key or entry.get("photo_loeschung_offen"):
            continue
        # Berichtsfotos liegen im UPLOAD-Storage (storage_service, Prefix
        # "pickup/") — nicht im Snapshot-Storage (Runde 4). Fehlschlaege
        # gelten nicht als Erfolg: Key bleibt stehen, die Nachholung raeumt
        # das Array-Element ueber `ref` auf.
        ok = await loeschen_oder_vormerken(
            db, key=key, grund="berichtsfoto_frist", dealer_id=dealer_id,
            ref={"collection": "pickup_reports", "id": rep["id"],
                 "array": {"pfad": "deviations", "schluessel": "photo_key"},
                 "unset_fields": ["photo_key", "photo_loeschung_offen"],
                 "set_fields": {"photo_deleted_at": "$now"}})
        if ok:
            entry["photo_key"] = None
            entry["photo_deleted_at"] = now.isoformat()
            entry.pop("photo_loeschung_offen", None)
        else:
            entry["photo_loeschung_offen"] = True
        rep_changed = True
    if rep_changed:
        await db.pickup_reports.update_one(
            {"id": rep["id"]}, {"$set": {"deviations": devs}})
        stats["report_photos_deleted"] = (
            stats.get("report_photos_deleted", 0) + 1)
    return rep_changed


def _fahrerfoto_tage() -> int:
    try:
        wert = int(os.environ.get("FAHRERFOTO_TAGE") or 90)
    except ValueError:
        wert = 90
    return max(1, min(wert, 3650))


# Runde 21 (Befund Ahmad 10.09.2026): Fahrerfotos bleiben so lange wie der
# Kaufvertrag (90 Tage, vgl. VERTRAG_AUFBEWAHRUNG_TAGE) — gezaehlt ab dem
# Hochladen des Berichts, unabhaengig vom Terminstatus. Vorher 7/14 Tage ab
# dem ERSTEN Terminabschluss: zu kurz als Beweis, Fotos geloeschter oder
# stornierter Termine blieben fuer immer liegen, und wiedergeoeffnete
# Termine verloren frische Fotos schon beim naechsten stuendlichen Lauf.
FAHRERFOTO_TAGE = _fahrerfoto_tage()


async def berichtsfotos_nach_frist_loeschen(db, now: datetime, stats: dict) -> int:
    """Fotos aller Berichte loeschen, die vor mehr als FAHRERFOTO_TAGE
    hochgeladen wurden (created_at des Berichts). Liefert die Zahl der
    bearbeiteten Berichte; hoechstens 500 je Lauf, der Rest folgt stuendlich."""
    cutoff = (now - timedelta(days=FAHRERFOTO_TAGE)).isoformat()
    n = 0
    cursor = db.pickup_reports.find(
        {"created_at": {"$lte": cutoff},
         "deviations": {"$elemMatch": {"photo_key": {"$type": "string"},
                                       "photo_loeschung_offen": {"$ne": True}}}},
        {"_id": 0, "id": 1, "dealer_id": 1, "deviations": 1}).limit(500)
    async for rep in cursor:
        if await _fotos_eines_berichts_loeschen(db, rep, now, stats,
                                                rep.get("dealer_id") or ""):
            n += 1
    return n


_VORGANG_OFFEN = ("vertrag_erstellt", "gesendet", "abholung_geplant")


async def _anderer_vorgang_offen(db, dealer_id: str, vehicle_id: str, appt_id: str) -> bool:
    """Runde 18: Gibt es zum gemeinsamen Fahrzeug noch einen anderen offenen
    Termin oder einen offenen Kaufvorgang (anderer Sucher)?"""
    from deps import TERMIN_OFFEN_WERTE
    if await db.appointments.count_documents(
            {"dealer_id": dealer_id, "vehicle_id": vehicle_id, "id": {"$ne": appt_id},
             "status": {"$in": TERMIN_OFFEN_WERTE}}, limit=1):
        return True
    return bool(await db.kaufvorgaenge.count_documents(
        {"dealer_id": dealer_id, "vehicle_id": vehicle_id,
         "status": {"$in": list(_VORGANG_OFFEN)}}, limit=1))


async def _cleanup_once(db) -> dict:
    """Ein Durchlauf. Liefert Metriken."""
    now = datetime.now(timezone.utc)
    stats = {"checked": 0, "cleaned": 0, "snapshots_deleted": 0, "photos_cleared": 0}

    for status_name, days in CLEANUP_RULES:
        cutoff_iso = (now - timedelta(days=days)).isoformat()
        # Kandidaten: Termin hat den Status, der Abschluss ist älter als
        # cutoff, und assets wurden noch nicht bereits gecleant.
        # Nachpruefung Runde 14 (Nr. 100): Frist ab abgeschlossen_seit
        # (Zeitpunkt des Abschlusses), ersatzweise status_changed_at
        # (Altbestand / Termine ohne das Feld).
        cursor = db.appointments.find(
            {
                "status": status_name,
                "assets_cleaned_at": {"$in": [None, ""]},
                "$or": [
                    {"abgeschlossen_seit": {"$lte": cutoff_iso}},
                    {"abgeschlossen_seit": {"$in": [None, ""]},
                     "status_changed_at": {"$lte": cutoff_iso}},
                ],
            },
            {"_id": 0, "id": 1, "vehicle_id": 1, "dealer_id": 1,
             "status_changed_at": 1, "abgeschlossen_seit": 1},
        )
        async for appt in cursor:
            stats["checked"] += 1
            vehicle_id = appt.get("vehicle_id")

            # Runde 21 (Befund Ahmad 10.09.2026): Fahrerfotos haben eine
            # EIGENE Frist ab dem Hochladen (berichtsfotos_nach_frist_loeschen
            # unten) und haengen nicht mehr am Terminabschluss. Hier geht es
            # nur noch um Inseratsfotos und Snapshots des Fahrzeugs.

            # Händler hat bereits über das Fahrzeug entschieden? Dann regelt
            # der Lebenszyklus die Aufbewahrung — 7-Tage-Regel entfällt
            # (nur fuer Inserats-Fotos/Snapshots, NICHT fuer Berichts-Fotos).
            # Pruefbericht Runde 8: Fahrzeug-IDs sind "v_<Inserat>" und damit
            # bei zwei Firmen mit demselben Inserat IDENTISCH. Ohne Firma in
            # der Abfrage traf der Aufraeumer das Fahrzeug der falschen Firma.
            firma = appt.get("dealer_id", "")
            if vehicle_id:
                v_state = await db.vehicles.find_one(
                    {"id": vehicle_id, "dealer_id": firma}, {"_id": 0, "lifecycle": 1})
                if v_state and v_state.get("lifecycle") in _DECIDED_STATES:
                    await db.appointments.update_one(
                        {"id": appt["id"]},
                        {"$set": {"assets_cleaned_at": now.isoformat(),
                                  "cleanup_skipped": "haendler_entscheidung"}},
                    )
                    continue

            # Runde 18: Das Inserat ist firmenweit gemeinsam (Kaufvorgaenge).
            # Hat ein ANDERER Sucher zum selben Fahrzeug noch einen offenen
            # Termin oder Vorgang, bleiben Inserats-Fotos und Snapshots —
            # sein eigener Termin raeumt nach seinem Abschluss auf. Die
            # Berichtsfotos dieses Termins sind oben bereits weg.
            if vehicle_id and await _anderer_vorgang_offen(db, firma, vehicle_id, appt["id"]):
                # Runde 27 (Gegenpruefung 12.09.2026): NICHT assets_cleaned_at
                # setzen. Sonst gilt der Termin als erledigt und wird nie
                # wieder geprueft — bleibt der Vorgang des Kollegen fuer immer
                # offen (das System storniert bewusst nichts von selbst),
                # lagen Inseratsfotos und Snapshots dauerhaft weiter herum.
                # Nur vermerken und beim naechsten Lauf erneut versuchen.
                await db.appointments.update_one(
                    {"id": appt["id"]},
                    {"$set": {"cleanup_zurueckgestellt_am": now.isoformat(),
                              "cleanup_skipped": "anderer_vorgang_offen"}},
                )
                stats["zurueckgestellt"] = stats.get("zurueckgestellt", 0) + 1
                continue

            # 1) Snapshots + Storage-Objekte wegwerfen
            if vehicle_id:
                deleted = await _delete_snapshots_for_vehicle(
                    db, vehicle_id, dealer_id=appt.get("dealer_id", ""))
                stats["snapshots_deleted"] += deleted

                # 2) Fotos aus dem Vehicle-Cache räumen — Runde 18: NUR die
                #    Fotofelder ($set data.<feld>), nicht das ganze data-Objekt
                #    zurueckschreiben. Vorher gingen zwischenzeitliche
                #    Korrekturen (Kilometerstand, Fahrzeugdaten) verloren.
                v = await db.vehicles.find_one(
                    {"id": vehicle_id, "dealer_id": firma},
                    {"_id": 0, "data": 1, "mobile_ad_id": 1})
                if v and isinstance(v.get("data"), dict):
                    data = v["data"]
                    leeren = {f"data.{key}": [] for key in _iter_photo_keys() if data.get(key)}
                    if leeren:
                        await db.vehicles.update_one(
                            {"id": vehicle_id, "dealer_id": firma},
                            {"$set": {**leeren, "assets_cleaned_at": now.isoformat()}},
                        )
                        stats["photos_cleared"] += 1

                # 3) Listings-Cache-Eintrag entfernen, damit ein neuer
                #    Vergleich wieder frisch zieht. WICHTIG: der
                #    listings_cache ist GEMEINSAM (ein Eintrag je Anzeige,
                #    von allen Haendlern genutzt). Ihn wegen der Frist EINES
                #    Haendlers zu loeschen wuerde alle anderen zu einem
                #    neuen Anbieter-Abruf zwingen. Deshalb nur die
                #    haendlereigenen Fotos (oben) entfernen und den
                #    gemeinsamen Cache ueber seine eigene Ablauffrist
                #    (LISTING_CACHE_TTL_HOURS) auslaufen lassen.

            # 4) Termin markieren, damit wir ihn nicht nochmal anfassen
            await db.appointments.update_one(
                {"id": appt["id"]},
                {"$set": {"assets_cleaned_at": now.isoformat()}},
            )
            stats["cleaned"] += 1

    # ---- Runde 21: Fahrerfotos FAHRERFOTO_TAGE nach dem Hochladen ----
    stats["berichtsfotos_frist"] = await berichtsfotos_nach_frist_loeschen(db, now, stats)
    # ---- 50-Tage-Regel: abgelaufene Bestandsfahrzeuge archivieren ----
    stats["archived"] = await _archive_expired_bestand(db, now)
    # ---- Versand, der nie ein Ergebnis bekam (Runde 8, Befund 3) ----
    stats["zustellungen_unklar"] = await haengende_zustellungen_markieren(db, now)
    # ---- 90-Tage-Regel: Kaufvertraege samt Personendaten loeschen ----
    # Reihenfolge (Runde 5): ZUERST fehlende Auto-Datensaetze nachtragen,
    # DANN loeschen — sonst verschwanden Altvertraege beim allerersten
    # Lauf, bevor ihr dauerhafter Datensatz je existierte.
    stats["auto_daten_repariert"] = await auto_daten_reparieren(db)
    # Abgebrochene Loeschungen (Grabstein aelter als 10 min) zu Ende bringen
    stats["vertragsloeschungen_wiederaufgenommen"] = \
        await vertragsloeschungen_wiederaufnehmen(db, now)
    stats["contracts_deleted"] = await vertraege_nach_frist_loeschen(
        db, now, stats=stats)
    stats["termine_ohne_vertrag_bereinigt"] = await termine_ohne_vertrag_bereinigen(db, now)
    stats["protokoll_orte_nachgezogen"] = await protokoll_orte_nachziehen(db)
    stats["firmenreste_bereinigt"] = await firmenreste_bereinigen(db)
    stats["storage_nachgeholt"] = await storage_loeschungen_nachholen(db)
    stats["logs_rotiert"] = await logs_rotieren(db, now)
    # ---- Aufbewahrungsfristen (Go-Live-Audit 09/2026) ----
    stats["anfragen_rotiert"] = await anfragen_rotieren(db, now)
    stats["fehlerlogs_begrenzt"] = await fehlerlogs_begrenzen(db, now)
    # Audit 13.09.2026 (#35/#42): abgelaufene Inserats-Zwischenspeicher
    stats["inseratscache_rotiert"] = await inseratscache_rotieren(db, now)
    stats.update(await marktplatz_rotieren(db, now))

    if any(stats.values()):
        log.info("cleanup run: %s", stats)
    return stats


# Kaufvertraege (Personendaten des Verkaeufers, Unterschriften, PDF) werden
# nach dieser Frist VOLLSTAENDIG geloescht. Der anonyme Auto-Datensatz in
# admin_vehicle_data bleibt bewusst bestehen (auto_daten.py).
VERTRAG_AUFBEWAHRUNG_TAGE = int(os.environ.get("VERTRAG_AUFBEWAHRUNG_TAGE", "90"))
# Grabsteine (`loeschung.status == laeuft`), die aelter sind, gelten als
# abgebrochen und werden wiederaufgenommen.
VERTRAG_LOESCHUNG_WIEDERAUFNAHME_MINUTEN = 10
# Hoechstzahl an Vertrags-IDs in der Loeschvorschau (system_reports)
_LOESCHVORSCHAU_MAX_IDS = 5000


def vertrag_loeschung_aktiv() -> bool:
    """Opt-in (Go-Live-Audit 09/2026): Die Fristloeschung loescht erst,
    wenn VERTRAG_LOESCHUNG_AKTIV=true gesetzt ist. Bis dahin laeuft sie als
    Trockenlauf und legt nur eine Vorschau in system_reports ab. Wird bei
    jedem Lauf gelesen, damit ein Umschalten keinen Neustart braucht."""
    return os.environ.get("VERTRAG_LOESCHUNG_AKTIV", "false") \
        .strip().lower() in ("1", "true", "yes", "ja")


async def vertraege_nach_frist_loeschen(db, now: datetime,
                                        aktiv: Optional[bool] = None,
                                        stats: Optional[dict] = None) -> int:
    """Loescht alle Kaufvertraege, die aelter als VERTRAG_AUFBEWAHRUNG_TAGE
    sind: Vertragsdokument (inkl. contract_data mit Verkaeufername/Adresse/
    Telefon/E-Mail, PDF, Bilder-URLs, Versandstatus), alle archivierten
    Vorversionen, Protokoll-Personendaten und die Vertragsverweise der
    Termine (Details: vertrag_endgueltig_loeschen).

    Schutzregeln (Go-Live-Audit 09/2026):
    - `aktiv` (Standard: env VERTRAG_LOESCHUNG_AKTIV, aus) — inaktiv heisst
      TROCKENLAUF: Kandidaten zaehlen, protokollieren, Vorschau in
      db.system_reports (typ vertrag_loeschvorschau) ablegen, NICHTS loeschen.
    - Kandidaten werden vorab eingesammelt (kein Loeschen im laufenden Cursor).
    - Ein Vertrag wird nur geloescht, wenn sein dauerhafter Auto-Datensatz
      (admin_vehicle_data_id) existiert — sonst Betriebsalarm
      `vertrag_ohne_auto_daten` und der Vertrag bleibt.

    Bewusst NICHT angefasst: admin_vehicle_data — der dauerhafte Datensatz
    hat keine Verbindung mehr zum Vertrag und darf hier weder geloescht
    noch veraendert werden; die id wird nur auf Existenz geprueft."""
    cutoff = (now - timedelta(days=VERTRAG_AUFBEWAHRUNG_TAGE)).isoformat()
    if aktiv is None:
        aktiv = vertrag_loeschung_aktiv()
    kandidaten = [c async for c in db.generated_pdfs.find(
        {"created_at": {"$lte": cutoff}},
        {"_id": 0, "id": 1, "dealer_id": 1, "contract_no": 1,
         "admin_vehicle_data_id": 1})]
    if not aktiv:
        ids = [c["id"] for c in kandidaten]
        log.info("Vertragsloeschung INAKTIV (VERTRAG_LOESCHUNG_AKTIV=false) — "
                 "Vorschau: %d Vertraege mit created_at <= %s waeren zu "
                 "loeschen; ids: %s", len(ids), cutoff, ids[:20])
        await db.system_reports.replace_one(
            {"typ": "vertrag_loeschvorschau"},
            {"id": str(uuid.uuid4()), "typ": "vertrag_loeschvorschau",
             "created_at": now.isoformat(), "anzahl": len(ids),
             "ids": ids[:_LOESCHVORSCHAU_MAX_IDS], "cutoff": cutoff},
            upsert=True)
        if ids:
            # Nachpruefung Runde 14 (Nr. 97): der Trockenlauf ist gewollt
            # (Go-Live-Entscheidung), aber ein vergessenes Flag darf nicht
            # still bleiben — sonst laeuft die versprochene 90-Tage-Frist nie.
            # Ein Alarm je (typ, ref) wird nur hochgezaehlt, nicht dupliziert.
            await alarm(db, "vertrag_loeschung_trockenlauf",
                        ref="vertrag_loeschvorschau", anzahl=len(ids),
                        cutoff=cutoff,
                        hinweis="VERTRAG_LOESCHUNG_AKTIV ist aus: Vertraege "
                                "ueber der Frist werden nur gezaehlt, nicht "
                                "geloescht (siehe DEPLOYMENT.md, Scharfschalten)")
        if stats is not None:
            stats["contracts_vorschau"] = len(ids)
        return 0
    geloescht = 0
    uebersprungen = 0
    for c in kandidaten:
        avd_id = c.get("admin_vehicle_data_id")
        if not avd_id or not await db[auto_daten.COLLECTION].count_documents(
                {"id": avd_id}, limit=1):
            uebersprungen += 1
            await alarm(db, "vertrag_ohne_auto_daten", ref=c["id"],
                        dealer_id=c.get("dealer_id") or "",
                        contract_no=c.get("contract_no") or "",
                        admin_vehicle_data_id=avd_id or "")
            continue
        if await vertrag_endgueltig_loeschen(db, c["id"], scrub_pii=True,
                                             grund="90tage"):
            geloescht += 1
    if stats is not None:
        stats["contracts_uebersprungen"] = uebersprungen
    return geloescht


async def _protokolle_pii_entfernen(db, termin_ids: list, dealer_id,
                                    jetzt: str) -> None:
    """Abhol-Protokolle dieser Termine: Verkaeufername, Ort, Unterschriften
    und das unterschriebene PDF entfernen (Review 09/2026: blieben
    unbefristet). Der technische Zustandsteil des Protokolls bleibt.
    Dateien, die sich nicht loeschen lassen, bleiben referenziert
    (`<feld>_loeschung_offen: True`) und werden nachgeholt."""
    async for p in db.pickup_protocols.find(
            {"appointment_id": {"$in": termin_ids}},
            {"_id": 0, "id": 1, "pdf_path": 1, "signature_driver_key": 1,
             "signature_seller_key": 1}):
        unset, offen = {}, {}
        for feld in ("pdf_path", "signature_driver_key", "signature_seller_key"):
            key = p.get(feld)
            if not key:
                continue
            ok = await loeschen_oder_vormerken(
                db, key=key, grund="vertrag_loeschung_protokoll",
                dealer_id=dealer_id or "",
                ref={"collection": "pickup_protocols", "id": p["id"],
                     "unset_fields": [feld, f"{feld}_loeschung_offen"]})
            if ok:
                unset[feld] = ""
                unset[f"{feld}_loeschung_offen"] = ""
            else:
                offen[f"{feld}_loeschung_offen"] = True
        # Nachpruefung Runde 14 (Nr. 23): der Ort steht im Protokoll unter
        # `place` (protocols.py), nicht `pickup_address` — der Docstring
        # versprach "Ort", geleert wurde ein Feld, das es nicht gibt.
        upd = {"$set": {"seller_name": "", "place": "", "pickup_address": "",
                        "pii_geloescht_at": jetzt, **offen}}
        if unset:
            upd["$unset"] = unset
        await db.pickup_protocols.update_one({"id": p["id"]}, upd)


async def protokoll_orte_nachziehen(db) -> int:
    """Nachpruefung Runde 14 (Nr. 23), Altbestand: Protokolle, die schon als
    bereinigt markiert sind (pii_geloescht_at), aber den Ort noch tragen,
    nachtraeglich leeren. Idempotent, laeuft in jedem Aufraeumzyklus mit."""
    r = await db.pickup_protocols.update_many(
        {"pii_geloescht_at": {"$nin": [None, ""]},
         "place": {"$nin": [None, ""]}},
        {"$set": {"place": ""}})
    return r.modified_count


# Runde 29 (12.09.2026): Stati, die einen Kaufvorgang als OFFEN ausweisen.
# Bewusst hier gespiegelt (kaufvorgang.OFFEN), damit das Aufraeumen keinen
# Import-Zyklus braucht; der Test haelt beide Listen zusammen.
_KV_OFFEN = ("vertrag_erstellt", "gesendet", "abholung_geplant")

async def vertrag_endgueltig_loeschen(db, contract_id: str, *, scrub_pii: bool,
                                      grund: str, audit: bool = True) -> bool:
    """Einen Kaufvertrag endgueltig loeschen — idempotent und wiederaufnehmbar.

    Ablauf (jeder Schritt darf beliebig oft laufen):
      0) Grabstein `loeschung: {status: laeuft, gestartet, grund, scrub_pii}`
         auf dem Vertrag. Bricht der Prozess mittendrin ab, nimmt der
         Aufraeumjob (vertragsloeschungen_wiederaufnehmen) hier wieder auf.
      1) archivierte Vorversionen loeschen
      2) Termin-IDs einsammeln
      3) scrub_pii: Protokoll-Dateien (PDF, Unterschriften) loeschen bzw.
         vormerken, Protokoll- und Termin-Personendaten entfernen
      4) Termine vom Vertrag loesen (contract_id: None)
      5) Vertrag loeschen, Audit-Eintrag `vertrag.geloescht.<grund>`

    Liefert True, wenn DIESER Aufruf den Vertrag geloescht hat; False, wenn
    es ihn nicht (mehr) gab. `audit=False`: der Aufrufer schreibt den
    Audit-Eintrag selbst (manuelle Loeschung mit Nutzer-Id)."""
    jetzt = datetime.now(timezone.utc).isoformat()
    c = await db.generated_pdfs.find_one(
        {"id": contract_id},
        {"_id": 0, "id": 1, "dealer_id": 1, "contract_no": 1, "loeschung": 1})
    if not c:
        return False
    dealer_id = c.get("dealer_id")
    grab = c.get("loeschung") or {}
    wiederaufnahme = grab.get("status") == "laeuft"
    if wiederaufnahme:
        # Urspruenglichen Grund/Umfang beibehalten, Startzeit auffrischen.
        grund = grab.get("grund") or grund
        scrub_pii = bool(grab.get("scrub_pii", scrub_pii))
        await db.generated_pdfs.update_one(
            {"id": contract_id},
            {"$set": {"loeschung.gestartet": jetzt},
             "$inc": {"loeschung.wiederaufnahmen": 1}})
    else:
        await db.generated_pdfs.update_one(
            {"id": contract_id},
            {"$set": {"loeschung": {"status": "laeuft", "gestartet": jetzt,
                                    "grund": grund, "scrub_pii": scrub_pii}}})
    # 1) Vorversionen
    await db.generated_pdf_versions.delete_many({"contract_id": contract_id})
    # 2) Termine
    termin_ids = [a["id"] async for a in db.appointments.find(
        {"contract_id": contract_id}, {"_id": 0, "id": 1})]
    # 3) Personendaten (Protokolle VOR dem Loesen der Termine — sonst
    #    findet eine Wiederaufnahme die Protokolle nicht mehr)
    if scrub_pii and termin_ids:
        await _protokolle_pii_entfernen(db, termin_ids, dealer_id, jetzt)
        # Termin: Verweis kappen UND die dort kopierten Verkaeuferdaten
        # (Name, Telefon, E-Mail, Abholanschrift) entfernen — sie blieben
        # sonst nach der Vertragsloeschung erhalten (Runde 5).
        await db.appointments.update_many(
            {"contract_id": contract_id},
            {"$set": {"contract_id": None, "seller_name": "", "seller_phone": "",
                      "seller_email": "", "pickup_address": "",
                      "pii_geloescht_at": jetzt, "updated_at": jetzt}})
    # 4) Verweise kappen (auch ohne PII-Bereinigung; idempotent)
    await db.appointments.update_many(
        {"contract_id": contract_id},
        {"$set": {"contract_id": None, "updated_at": jetzt}})
    # 4b) Kaufvorgang (Runde 29, 12.09.2026, Pruefbefund): Der Vorgang blieb
    #     unberuehrt. Ein noch OFFENER Vorgang liess das Fahrzeug damit
    #     dauerhaft auf "gekauft" stehen, obwohl es den Vertrag nicht mehr
    #     gibt. Offene Vorgaenge werden deshalb geschlossen; abgeschlossene
    #     (abgeholt/storniert) bleiben als Beleg stehen. Beide werden
    #     markiert, damit niemand einem toten Vertragszeiger folgt.
    #     Der Zeiger selbst bleibt stehen: kaufvorgaenge.contract_id traegt
    #     einen Unique-Index ohne Teilfilter — zwei leere Werte waeren eine
    #     Dublette und der Schreibvorgang wuerde scheitern.
    #     Gegenpruefung 12.09.2026: Die Auswahl darf NICHT nach der Markierung
    #     filtern. Sonst waere 4b nach einem Abbruch mitten drin (Markierung
    #     schon gesetzt, Fahrzeugstatus noch nicht) bei der Wiederaufnahme
    #     leer — und das Fahrzeug haette fuer immer auf "gekauft" gestanden,
    #     weil danach kein Statuswechsel mehr kommt. Alle Schritte hier sind
    #     mehrfach ausfuehrbar.
    betroffen = [k async for k in db.kaufvorgaenge.find(
        {"contract_id": contract_id},
        {"_id": 0, "id": 1, "status": 1, "vehicle_id": 1})]
    if betroffen:
        await db.kaufvorgaenge.update_many(
            {"contract_id": contract_id,
             "status": {"$in": list(_KV_OFFEN)}},
            {"$set": {"status": "storniert",
                      "storno_grund": "vertrag_geloescht", "updated_at": jetzt}})
        # Runde 32: Den Grund mitschreiben (manuell / 90tage) — damit laesst
        # sich spaeter unterscheiden, ob ein Kauf zurueckgezogen wurde oder nur
        # die Aufbewahrungsfrist ablief.
        await db.kaufvorgaenge.update_many(
            {"contract_id": contract_id, "vertrag_geloescht_am": None},
            {"$set": {"vertrag_geloescht_am": jetzt, "vertrag_geloescht_grund": grund,
                      "updated_at": jetzt}})
        # Gegenpruefung 12.09.2026: Wird eine mit aelterem Code begonnene
        # Loeschung wieder aufgenommen, steht vertrag_geloescht_am schon — der
        # Grund fehlte dann fuer immer. Nachtragen, ohne einen vorhandenen zu
        # ueberschreiben.
        await db.kaufvorgaenge.update_many(
            {"contract_id": contract_id, "vertrag_geloescht_grund": None},
            {"$set": {"vertrag_geloescht_grund": grund, "updated_at": jetzt}})
        # Fahrzeugstatus neu ableiten, damit kein Auto auf einem Stand
        # haengen bleibt, den es nicht mehr gibt. Scheitert das, wird die
        # Loeschung trotzdem zu Ende gefuehrt — aber NICHT still: ein
        # Betriebsalarm nennt das Fahrzeug, damit es jemand nachzieht.
        try:
            import kaufvorgang as _kv
            for vid in {k.get("vehicle_id") for k in betroffen if k.get("vehicle_id")}:
                await _kv.fahrzeug_status_aggregieren(vid, dealer_id)
        except Exception as exc:  # noqa: BLE001
            log.exception("Fahrzeugstatus nach Vertragsloeschung %s nicht aktualisiert", contract_id)
            try:
                await alarm(db, "fahrzeugstatus_nach_loeschung_offen",
                            ref=contract_id, dealer_id=dealer_id or "",
                            fahrzeuge=sorted({k.get("vehicle_id") for k in betroffen
                                              if k.get("vehicle_id")}),
                            fehler=str(exc)[:300])
            except Exception:  # noqa: BLE001
                pass
    # 5) Vertrag
    res = await db.generated_pdfs.delete_one({"id": contract_id})
    if not res.deleted_count:
        return False   # parallel bereits geloescht — nichts doppelt protokollieren
    if audit:
        # Audit 13.09.2026 (#45): Der Vertrag ist bereits geloescht. Ein
        # Fehler beim Audit-Eintrag brach vorher den ganzen Aufraeumzyklus ab
        # (weitere Frist-Kandidaten und Folgeschritte warteten bis zum
        # naechsten Lauf) und der Eintrag ging still verloren. Jetzt: Spur im
        # Fehlerlog und als Betriebsalarm, Rueckgabe bleibt True.
        try:
            await db.activity_logs.insert_one({
                "id": uuid.uuid4().hex,
                "dealer_id": dealer_id, "user_id": "",
                "action": f"vertrag.geloescht.{grund}",
                "ref": contract_id,
                "meta": {"contract_no": c.get("contract_no"),
                         "scrub_pii": scrub_pii, "wiederaufnahme": wiederaufnahme},
                "created_at": jetzt,
            })
        except Exception as exc:  # noqa: BLE001
            log.exception("Audit vertrag.geloescht.%s fuer %s fehlt (Vertrag "
                          "bereits geloescht)", grund, contract_id)
            await alarm(db, "audit_fehlt", ref=contract_id,
                        aktion=f"vertrag.geloescht.{grund}",
                        dealer_id=dealer_id or "",
                        contract_no=c.get("contract_no") or "",
                        fehler=str(exc)[:300])
    return True


async def vertragsloeschungen_wiederaufnehmen(db, now: datetime) -> int:
    """Reparatur: Vertraege mit Grabstein `loeschung.status == laeuft`, deren
    Start laenger als VERTRAG_LOESCHUNG_WIEDERAUFNAHME_MINUTEN zurueckliegt,
    sind mittendrin abgebrochen (Neustart, Absturz). Sie werden ueber
    dieselbe Funktion zu Ende geloescht."""
    cutoff = (now - timedelta(
        minutes=VERTRAG_LOESCHUNG_WIEDERAUFNAHME_MINUTEN)).isoformat()
    haengend = [c async for c in db.generated_pdfs.find(
        {"loeschung.status": "laeuft", "loeschung.gestartet": {"$lt": cutoff}},
        {"_id": 0, "id": 1, "loeschung": 1})]
    n = 0
    for c in haengend:
        grab = c.get("loeschung") or {}
        log.warning("Vertragsloeschung %s (grund=%s, gestartet %s) war "
                    "unvollstaendig — wird wiederaufgenommen",
                    c["id"], grab.get("grund"), grab.get("gestartet"))
        if await vertrag_endgueltig_loeschen(
                db, c["id"],
                scrub_pii=bool(grab.get("scrub_pii",
                                        grab.get("grund") != "manuell")),
                grund=grab.get("grund") or "wiederaufnahme"):
            n += 1
    return n


# Audit-/Fehlerprotokolle und Job-Sperren wuchsen unbegrenzt (N3, Review
# 09/2026). Aufbewahrung in Tagen; created_at ist ISO-String (lexikografisch
# vergleichbar), deshalb Rotation hier statt TTL-Index.
LOG_AUFBEWAHRUNG_TAGE = int(os.environ.get("LOG_AUFBEWAHRUNG_TAGE", "180"))


async def logs_rotieren(db, now: datetime) -> int:
    cutoff = (now - timedelta(days=LOG_AUFBEWAHRUNG_TAGE)).isoformat()
    n = 0
    r = await db.activity_logs.delete_many({"created_at": {"$lt": cutoff}})
    n += r.deleted_count
    # Offene Fehler bleiben, bis ein Admin sie schliesst; erledigte rotieren.
    r = await db.error_logs.delete_many({"created_at": {"$lt": cutoff},
                                         "status": {"$ne": "open"}})
    n += r.deleted_count
    r = await db.job_locks.delete_many(
        {"expires_at": {"$lt": now - timedelta(days=1)}})
    n += r.deleted_count
    return n


# ---------------------------------------------------------------------------
# Aufbewahrungsfristen (Go-Live-Audit 09/2026): Zugangs-/Abo-Anfragen,
# offene Fehlerprotokolle (+ harter Deckel) und Marktplatz-Altdaten wuchsen
# unbegrenzt. created_at/updated_at sind ISO-Strings (lexikografisch
# vergleichbar).
# ---------------------------------------------------------------------------
ANFRAGEN_AUFBEWAHRUNG_TAGE = int(os.environ.get("ANFRAGEN_AUFBEWAHRUNG_TAGE", "90"))
LOG_AUFBEWAHRUNG_TAGE_OFFEN = int(os.environ.get("LOG_AUFBEWAHRUNG_TAGE_OFFEN", "365"))
ERROR_LOG_MAX = int(os.environ.get("ERROR_LOG_MAX", "20000"))
INTERESSEN_AUFBEWAHRUNG_TAGE = 180        # abgeschlossene Interessensanfragen
INSERATE_GELOESCHT_AUFBEWAHRUNG_TAGE = 90  # geloeschte Inserate (Soft-Delete)


def _aelter_als(cutoff: str) -> dict:
    """Filter: updated_at (falls belegt), sonst created_at liegt vor cutoff."""
    return {"$or": [{"updated_at": {"$lt": cutoff}},
                    {"updated_at": {"$in": [None, ""]},
                     "created_at": {"$lt": cutoff}}]}


async def anfragen_rotieren(db, now: datetime,
                            tage: Optional[int] = None) -> int:
    """Erledigte/abgelehnte Zugangs- und Abo-Anfragen (plan_requests) nach
    ANFRAGEN_AUFBEWAHRUNG_TAGE loeschen — sie tragen Kontaktdaten der
    Anfragenden. Offene Anfragen bleiben."""
    tage = ANFRAGEN_AUFBEWAHRUNG_TAGE if tage is None else tage
    cutoff = (now - timedelta(days=tage)).isoformat()
    r = await db.plan_requests.delete_many(
        {"status": {"$in": ["abgelehnt", "erledigt"]}, **_aelter_als(cutoff)})
    return r.deleted_count


async def fehlerlogs_begrenzen(db, now: datetime, *,
                               offen_tage: Optional[int] = None,
                               maximum: Optional[int] = None) -> int:
    """error_logs: (a) OFFENE Eintraege verfallen nach
    LOG_AUFBEWAHRUNG_TAGE_OFFEN (erledigte rotieren bereits in logs_rotieren),
    (b) harter Deckel ERROR_LOG_MAX — darueber werden die aeltesten
    Eintraege entfernt, unabhaengig vom Status."""
    offen_tage = LOG_AUFBEWAHRUNG_TAGE_OFFEN if offen_tage is None else offen_tage
    maximum = ERROR_LOG_MAX if maximum is None else maximum
    cutoff = (now - timedelta(days=offen_tage)).isoformat()
    r = await db.error_logs.delete_many(
        {"status": "open", "created_at": {"$lt": cutoff}})
    n = r.deleted_count
    ueberhang = await db.error_logs.count_documents({}) - maximum
    if ueberhang > 0:
        aelteste = await db.error_logs.find({}, {"_id": 1}) \
            .sort("created_at", 1).limit(ueberhang).to_list(ueberhang)
        ids = [d["_id"] for d in aelteste]
        for i in range(0, len(ids), 1000):
            r = await db.error_logs.delete_many({"_id": {"$in": ids[i:i + 1000]}})
            n += r.deleted_count
    return n


# Audit 13.09.2026 (#35): Punkt 39 (Inserats-Cache hoechstens 90 Tage) war nur
# als Frischegrenze umgesetzt — nach Ablauf wurde neu abgerufen, geloescht
# wurde nie. listings_cache.data traegt Verkaeuferangaben (Name, Telefon,
# Anschrift; bei Kleinanzeigen meist Privatpersonen). Kein TTL-Index: der
# koennte einen Eintrag mitten im Neuabruf (laufender Lease) loeschen und
# erfasst keine Lease-Reste ohne expires_at.
LISTING_CACHE_KARENZ_TAGE = int(os.environ.get("LISTING_CACHE_KARENZ_TAGE", "7"))
# Gleicher Wert und Default wie routes/listings.py (dort Schreib-TTL); hier
# direkt aus der Umgebung, um den Router nicht in den Cleanup zu importieren.
LISTING_CACHE_TTL_HOURS = int(os.environ.get("LISTING_CACHE_TTL_HOURS", "2160"))
# Nachpruefung 13.09.2026: Die Datenschutzerklaerung sagt "Inserats-Cache
# max. 90 Tage". TTL (90 Tage) plus Karenz (7 Tage) waeren 97 — deshalb eine
# harte Grenze ab dem Abruf, unabhaengig von TTL und Karenz. Wer sie aendert,
# muss frontend/src/pages/legal/Datenschutz.jsx (Abschnitt 5) nachziehen.
INSERATSCACHE_MAX_TAGE = 90


async def inseratscache_rotieren(db, now: datetime,
                                 karenz_tage: Optional[int] = None) -> int:
    """listings_cache: (a) Eintraege, deren expires_at seit mehr als
    LISTING_CACHE_KARENZ_TAGE abgelaufen ist, (b) Lease-Reste ohne Daten
    (gescheiterter Erstabruf) nach einem Tag. Laufende Leases bleiben
    unberuehrt. Ausgenommen sind Inserate mit einem noch nicht erzeugten
    Beweisdokument aus der Zeit vor Runde 23 (ohne quelle_daten) — der Worker
    liest deren Daten noch aus dem Zwischenspeicher.
    (c) vehicle_cache (mobile.de, 30 min): zweite Absicherung, falls der
    TTL-Index fehlt (#42) — abgelaufene Eintraege liefert cache_get ohnehin
    nie mehr aus.
    (d) Nachbesserung #35: Eintraege, deren Abruf (fetched_at) laenger als
    LISTING_CACHE_TTL_HOURS plus Karenz zurueckliegt — egal was in
    expires_at steht. Betrifft den Altbestand vor ce63a99 (04.09.2026) mit
    expires_at = Abruf + 1 Jahr; neue Eintraege erfasst (a) vorher."""
    karenz = LISTING_CACHE_KARENZ_TAGE if karenz_tage is None else karenz_tage
    frei = {"$or": [{"fetching_until": None}, {"fetching_until": {"$lt": now}}]}
    geschuetzt = await db.inserat_beweise.distinct(
        "cache_key", {"status": {"$in": ["offen", "in_arbeit"]},
                      "quelle_daten": {"$exists": False}})
    ausnahme = {"cache_key": {"$nin": [k for k in geschuetzt if k]}} if geschuetzt else {}
    r = await db.listings_cache.delete_many(
        {"expires_at": {"$lt": now - timedelta(days=karenz)}, **frei, **ausnahme})
    n = r.deleted_count
    # (d) nutzt den Index cache_abruf (server._alle_indexe). Hoechstens
    # INSERATSCACHE_MAX_TAGE nach dem Abruf — das greift auch vor (a).
    frist = min(timedelta(hours=LISTING_CACHE_TTL_HOURS) + timedelta(days=karenz),
                timedelta(days=INSERATSCACHE_MAX_TAGE))
    r = await db.listings_cache.delete_many(
        {"fetched_at": {"$lt": now - frist}, **frei, **ausnahme})
    n += r.deleted_count
    # expires_at None trifft das fehlende Feld und nutzt den Index cache_ablauf.
    r = await db.listings_cache.delete_many(
        {"expires_at": None, "data": {"$exists": False},
         "created_at": {"$lt": now - timedelta(days=1)}, **frei, **ausnahme})
    n += r.deleted_count
    r = await db.vehicle_cache.delete_many(
        {"expires_at_dt": {"$lt": now - timedelta(days=1)}})
    n += r.deleted_count
    return n


async def _inserat_mit_fotos_loeschen(db, listing: dict, *, grund: str,
                                      dealer_id: str = "") -> bool:
    """Inserat samt hochgeladener Fotos loeschen. Fotos, die sich nicht
    loeschen lassen, werden vorgemerkt; das Inserat bleibt dann mit
    `loeschung_offen: True` und den restlichen Keys stehen und wird von der
    Nachholung geloescht, sobald das letzte Foto weg ist. True = Dokument weg."""
    keys = list((listing.get("photos") or {}).get("uploaded_keys") or [])
    weg = []
    for k in keys:
        ok = await loeschen_oder_vormerken(
            db, key=k, grund=grund, dealer_id=dealer_id,
            ref={"collection": "resale_listings", "id": listing["id"],
                 "pull_key_from": "photos.uploaded_keys",
                 "loeschen_wenn_leer": ["photos.uploaded_keys"]})
        if ok:
            weg.append(k)
    if len(weg) == len(keys):
        await db.resale_listings.delete_one({"id": listing["id"]})
        return True
    upd = {"$set": {"status": "geloescht", "loeschung_offen": True,
                    "updated_at": now_iso()}}
    if weg:
        upd["$pull"] = {"photos.uploaded_keys": {"$in": weg}}
    await db.resale_listings.update_one({"id": listing["id"]}, upd)
    return False


async def marktplatz_rotieren(db, now: datetime) -> dict:
    """Marktplatz-Altdaten: abgeschlossene Interessensanfragen nach 180
    Tagen, geloeschte Inserate (Soft-Delete) samt Fotos nach 90 Tagen,
    verwaiste Merklisten-Eintraege (Inserat existiert nicht mehr)."""
    stats = {"interessen_geloescht": 0, "inserate_geloescht": 0,
             "favoriten_geloescht": 0, "interessen_verwaist_geschlossen": 0}
    # Nachpruefung Runde 14 (Befund 54): Verhandlungen zu Inseraten, die
    # verkauft oder geloescht sind (Altbestand vor dem Fix in resale.py),
    # werden geschlossen — sonst zeigten Kaeufer und Haendler ewig eine
    # "laufende" Verhandlung zu einem Auto, das es nicht mehr gibt.
    # Runde 17 (Nr. 400): Cursor in 1000er-Paketen statt eines festen
    # 5000er-Deckels — darueber blieben Anfragen fuer immer unbehandelt, und
    # 5000 Dokumente auf einmal im Speicher sind unnoetig (Muster: Favoriten
    # unten).
    async def _paket_schliessen(paket: list) -> int:
        ids = list({o.get("listing_id") for o in paket if o.get("listing_id")})
        vorhanden = {l["id"] async for l in db.resale_listings.find(
            {"id": {"$in": ids}, "status": {"$nin": ["verkauft", "geloescht"]}}, {"_id": 0, "id": 1})}
        verwaist = [o["id"] for o in paket if o.get("listing_id") not in vorhanden]
        if not verwaist:
            return 0
        r = await db.listing_interest.update_many(
            {"id": {"$in": verwaist}},
            {"$set": {"status": "abgelehnt", "beendet_grund": "inserat_weg",
                      "updated_at": now.isoformat()},
             "$push": {"history": {"von": "system", "aktion": "inserat_weg",
                                   "zeit": now.isoformat()}}})
        return r.modified_count

    paket: list = []
    async for o in db.listing_interest.find(
            {"status": {"$in": ["offen", "gegenangebot", "gegenangebot_kaeufer"]}},
            {"_id": 0, "id": 1, "listing_id": 1}):
        paket.append(o)
        if len(paket) >= 1000:
            stats["interessen_verwaist_geschlossen"] += await _paket_schliessen(paket)
            paket = []
    if paket:
        stats["interessen_verwaist_geschlossen"] += await _paket_schliessen(paket)
    cutoff = (now - timedelta(days=INTERESSEN_AUFBEWAHRUNG_TAGE)).isoformat()
    r = await db.listing_interest.delete_many(
        {"status": {"$in": ["akzeptiert", "abgelehnt"]}, **_aelter_als(cutoff)})
    stats["interessen_geloescht"] = r.deleted_count

    cutoff = (now - timedelta(days=INSERATE_GELOESCHT_AUFBEWAHRUNG_TAGE)).isoformat()
    alte = await db.resale_listings.find(
        {"status": "geloescht", "loeschung_offen": {"$ne": True},
         "$or": [{"deleted_at": {"$lt": cutoff}},
                 {"deleted_at": {"$in": [None, ""]},
                  "updated_at": {"$lt": cutoff}}]},
        {"_id": 0, "id": 1, "dealer_id": 1, "photos": 1}).to_list(None)
    for l in alte:
        if await _inserat_mit_fotos_loeschen(
                db, l, grund="inserat_geloescht_frist",
                dealer_id=l.get("dealer_id") or ""):
            stats["inserate_geloescht"] += 1

    listing_ids = await db.buyer_favorites.distinct("listing_id")
    for i in range(0, len(listing_ids), 1000):
        chunk = listing_ids[i:i + 1000]
        vorhanden = set(await db.resale_listings.distinct(
            "id", {"id": {"$in": [x for x in chunk if x]}}))
        fehlend = [x for x in chunk if x not in vorhanden]
        if fehlend:
            r = await db.buyer_favorites.delete_many({"listing_id": {"$in": fehlend}})
            stats["favoriten_geloescht"] += r.deleted_count
    return stats


# Nach so vielen Fehlversuchen wird eine vorgemerkte Datei-Loeschung
# aufgegeben (aufgegeben: True) und als Betriebsalarm sichtbar gemacht.
STORAGE_RETRY_MAX_VERSUCHE = 20


def _feld_wert(doc: dict, pfad: str):
    cur = doc
    for teil in pfad.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(teil)
    return cur


async def _retry_ref_anwenden(db, e: dict) -> str:
    """Nach erfolgreicher Nachholung das referenzierende Dokument bereinigen
    (Feld/Key entfernen, Markierung aufheben, leeres Dokument loeschen —
    Schema von `ref` siehe storage_service.loeschen_oder_vormerken).

    Rueckgabe: leerer Text bei Erfolg, sonst der Fehler. Runde 29
    (12.09.2026, Pruefbefund): Frueher wurde ein Fehler hier nur geloggt und
    die Vormerkung trotzdem geloescht — die Datei war weg, der Verweis in der
    Datenbank blieb fuer immer stehen. Jetzt bleibt die Vormerkung liegen und
    wird beim naechsten Lauf erneut versucht."""
    ref = e.get("ref") or {}
    coll, doc_id = ref.get("collection"), ref.get("id")
    if not coll or not doc_id:
        return ""
    jetzt = now_iso()
    key = e.get("key")
    try:
        felder = list(ref.get("unset_fields") or [])
        setzen = {k: (jetzt if v == "$now" else v)
                  for k, v in (ref.get("set_fields") or {}).items()}
        arr = ref.get("array") or {}
        upd = {}
        if arr.get("pfad") and arr.get("schluessel") and key:
            pfad = arr["pfad"]
            if felder:
                upd["$unset"] = {f"{pfad}.$[e].{f}": "" for f in felder}
            if setzen:
                upd["$set"] = {f"{pfad}.$[e].{k}": v for k, v in setzen.items()}
            if upd:
                await db[coll].update_one(
                    {"id": doc_id}, upd,
                    array_filters=[{f"e.{arr['schluessel']}": key}])
        else:
            if felder:
                upd["$unset"] = {f: "" for f in felder}
            if setzen:
                upd["$set"] = setzen
            if ref.get("pull_key_from") and key:
                upd["$pull"] = {ref["pull_key_from"]: key}
            if upd:
                await db[coll].update_one({"id": doc_id}, upd)
        # Gemeinsame Markierung erst aufheben, wenn KEINE weitere Vormerkung
        # mehr auf dasselbe Dokument zeigt.
        andere = await db.storage_delete_retry.count_documents(
            {"ref.collection": coll, "ref.id": doc_id, "id": {"$ne": e["id"]}},
            limit=1)
        if andere:
            return
        await db[coll].update_one({"id": doc_id},
                                  {"$unset": {"loeschung_offen": ""}})
        leer = ref.get("loeschen_wenn_leer") or []
        if leer:
            doc = await db[coll].find_one({"id": doc_id},
                                          {"_id": 0, **{f: 1 for f in leer}})
            if doc is not None and not any(_feld_wert(doc, f) for f in leer):
                await db[coll].delete_one({"id": doc_id})
    except Exception as exc:  # noqa: BLE001
        log.warning("Nachholung: Dokument %s/%s konnte nicht bereinigt werden: %s",
                    coll, doc_id, exc)
        return str(exc)[:300]
    return ""


async def storage_loeschungen_nachholen(db, limit: int = 200) -> int:
    """Vorgemerkte Datei-Loeschungen erneut versuchen — aus
    storage_service.loeschen_oder_vormerken (Fotos, Snapshots, Protokoll-
    dateien) und aus der Firmen-/Nutzerloeschung in routes/admin.py (Runde 5,
    ganze Praefixe). `art`: key (Upload-Storage), prefix (Ordner), snapshot
    (Snapshot-Storage); Alt-Eintraege ohne `art` sind Praefixe. Nach Erfolg
    wird das referenzierende Dokument (`ref`) bereinigt und der Eintrag
    entfernt. Nach STORAGE_RETRY_MAX_VERSUCHE Fehlschlaegen wird aufgegeben
    (`aufgegeben: True`) und ein Betriebsalarm `datei_loeschung_aufgegeben`
    ausgeloest — statt es ewig still zu versuchen."""
    from storage_service import storage
    erledigt = 0
    eintraege = await db.storage_delete_retry.find(
        {"aufgegeben": {"$ne": True}}, {"_id": 0}).limit(limit).to_list(limit)
    async def _fehlschlag(e: dict, art: str, fehler: str, zusatz=None) -> None:
        """Versuch zaehlen, ggf. aufgeben und Alarm schlagen — die Vormerkung
        bleibt in jedem Fall liegen (Runde 29)."""
        versuche = int(e.get("versuche", 0)) + 1
        upd = {"letzter_fehler": fehler[:300], "versuche": versuche,
               "updated_at": now_iso(), **(zusatz or {})}
        if versuche >= STORAGE_RETRY_MAX_VERSUCHE:
            upd["aufgegeben"] = True
            upd["aufgegeben_am"] = now_iso()
            await alarm(db, "datei_loeschung_aufgegeben",
                        ref=e.get("key") or e.get("prefix") or e["id"],
                        dealer_id=e.get("dealer_id") or "", art=art,
                        grund=e.get("grund") or "", versuche=versuche,
                        fehler=fehler[:300])
        await db.storage_delete_retry.update_one({"id": e["id"]}, {"$set": upd})

    for e in eintraege:
        art = e.get("art") or "prefix"
        # Runde 29: Ist die Datei bereits weg (frueherer Lauf), nur noch die
        # Referenz bereinigen — ein zweites Loeschen koennte fehlschlagen.
        if not e.get("storage_deleted"):
            try:
                if art == "prefix":
                    await asyncio.to_thread(storage.delete_prefix, e["prefix"])
                elif art == "snapshot":
                    if not await asyncio.to_thread(delete_object, e["key"]):
                        raise RuntimeError("Snapshot-Storage meldet Fehlschlag")
                else:
                    await asyncio.to_thread(storage.delete, e["key"])
            except Exception as exc:  # noqa: BLE001
                await _fehlschlag(e, art, str(exc))
                continue
        fehler = await _retry_ref_anwenden(db, e)
        if fehler:
            # Datei weg, Verweis steht noch: vormerken lassen und beim
            # naechsten Lauf nur noch die Referenz versuchen.
            await _fehlschlag(e, art, fehler, {"storage_deleted": True})
            continue
        await db.storage_delete_retry.delete_one({"id": e["id"]})
        erledigt += 1
    return erledigt


# Sicherheitsdeckel gegen Endlosschleifen im paketweisen Backfill.
AUTO_DATEN_REPARATUR_MAX_DURCHLAEUFE = 500


async def auto_daten_reparieren(db, limit: int = 500) -> int:
    """Reparatur unvollstaendiger Schreibvorgaenge / Backfill: Vertraege
    ohne admin_vehicle_data_id bekommen ihren anonymen Datensatz
    nachgetragen (atomarer Guard in auto_daten.nachtragen).

    Laeuft in Paketen von `limit`, bis nichts mehr offen ist (Go-Live-Audit
    09/2026: vorher blieb es bei EINEM Paket von 500 — bei mehr
    Altvertraegen haette die Fristloeschung danach Vertraege entfernt, deren
    dauerhafter Datensatz nie angelegt worden war). Deckel
    AUTO_DATEN_REPARATUR_MAX_DURCHLAEUFE; bleibt ein Paket unveraendert
    (nachtragen schlaegt dauerhaft fehl), wird abgebrochen und geloggt."""
    repariert = 0
    projektion = {"_id": 0, "id": 1, "contract_data": 1, "make": 1, "model": 1,
                  "vehicle_id": 1, "dealer_id": 1, "created_at": 1}
    letzte_ids: set = set()
    for _ in range(AUTO_DATEN_REPARATUR_MAX_DURCHLAEUFE):
        paket = await db.generated_pdfs.find(
            {"admin_vehicle_data_id": {"$exists": False}}, projektion
        ).limit(limit).to_list(limit)
        if not paket:
            break
        ids = {c["id"] for c in paket}
        if ids == letzte_ids:
            log.warning("auto_daten_reparieren: %d Vertraege lassen sich nicht "
                        "nachtragen (Paket unveraendert) — Abbruch, z.B. %s",
                        len(ids), sorted(ids)[:5])
            break
        letzte_ids = ids
        for c in paket:
            if await auto_daten.nachtragen(db, c):
                repariert += 1
    else:
        log.warning("auto_daten_reparieren: Sicherheitsdeckel von %d Paketen "
                    "erreicht — Rest im naechsten Lauf",
                    AUTO_DATEN_REPARATUR_MAX_DURCHLAEUFE)
    # Schema v2 (09/2026): Kaufdatum fuer Bestandsdatensaetze nachziehen,
    # solange der Vertrag noch existiert (danach ist es nicht mehr
    # rekonstruierbar — bewusst, denn genau das ist die Anonymisierung).
    # Ebenfalls paketweise ueber ALLE Datensaetze ohne purchase_date.
    ohne_datum = [d["id"] async for d in db[auto_daten.COLLECTION].find(
        {"purchase_date": {"$exists": False}}, {"_id": 0, "id": 1})]
    for i in range(0, len(ohne_datum), limit):
        async for c in db.generated_pdfs.find(
                {"admin_vehicle_data_id": {"$in": ohne_datum[i:i + limit]}},
                {"_id": 0, "admin_vehicle_data_id": 1, "created_at": 1}):
            tag = auto_daten.kaufdatum(c.get("created_at"))
            if tag:
                r = await db[auto_daten.COLLECTION].update_one(
                    {"id": c["admin_vehicle_data_id"],
                     "purchase_date": {"$exists": False}},
                    {"$set": {"purchase_date": tag,
                              "schema_version": auto_daten.SCHEMA_VERSION}})
                repariert += r.modified_count
    return repariert


async def termine_ohne_vertrag_bereinigen(db, now: datetime, frist_tage: int = 90) -> int:
    """Runde 10: Wird ein Vertrag von Hand geloescht, kappt das den Verweis
    am Termin — die 90-Tage-Bereinigung fand diesen Termin danach nie mehr,
    Verkaeuferdaten und Protokoll-Dateien blieben ewig. Hier bekommen
    Termine OHNE Vertrag ihre eigene Frist: aelter als frist_tage (nach
    Abholdatum, sonst Anlagedatum) -> Personendaten und Protokoll-Dateien weg."""
    if not vertrag_loeschung_aktiv():        # gleiche Lesart wie die Vertragsloeschung
        return 0
    grenze = (now - timedelta(days=frist_tage))
    grenze_iso = grenze.isoformat()
    n = 0
    cursor = db.appointments.find(
        {"$and": [
            {"pii_geloescht_at": {"$in": [None, ""]}},
            {"$or": [{"seller_name": {"$nin": [None, ""]}},
                     {"seller_phone": {"$nin": [None, ""]}},
                     {"seller_email": {"$nin": [None, ""]}},
                     {"pickup_address": {"$nin": [None, ""]}}]},
        ]},
        {"_id": 0, "id": 1, "dealer_id": 1, "pickup_date": 1, "created_at": 1,
         "contract_id": 1})
    async for a in cursor:
        stichtag = (a.get("pickup_date") or a.get("created_at") or "")[:10]
        if not stichtag or stichtag > grenze_iso[:10]:
            continue
        if a.get("contract_id"):
            # Nachpruefung Runde 10: Verweis auf einen Vertrag, den es nicht
            # mehr gibt (Loeschung ohne Kappen des Verweises), zaehlt wie
            # "ohne Vertrag". Existiert der Vertrag, gilt dessen eigene Frist.
            if await db.generated_pdfs.count_documents({"id": a["contract_id"]}, limit=1):
                continue
        jetzt = now.isoformat()
        try:
            await _protokolle_pii_entfernen(db, [a["id"]], a.get("dealer_id", ""), jetzt)
        except Exception as exc:                        # noqa: BLE001
            # Nachpruefung Runde 10: NICHT als bereinigt markieren — sonst
            # blieben die Protokoll-Dateien fuer immer liegen. Naechster Lauf.
            log.warning("termine_ohne_vertrag: Protokolle zu %s: %s — naechster Lauf",
                        a["id"], exc)
            continue
        await db.appointments.update_one(
            {"id": a["id"]},
            {"$set": {"seller_name": "", "seller_phone": "", "seller_email": "",
                      "pickup_address": "", "pii_geloescht_at": jetzt,
                      "pii_grund": "termin_ohne_vertrag_frist", "updated_at": jetzt}})
        n += 1
    return n


async def firmenreste_bereinigen(db) -> int:
    """Nachpruefung Runde 14 (Nr. 15/13/14): Die Firmenloeschung (admin.py)
    raeumt nur die dealer_id-gebundenen _COMPANY_COLLECTIONS. Uebrig blieben
    Firmen-IDs in link_jobs (dealer_ids, requested_by_dealer), die
    Quarantaene listings_cache_client (samt ingested_by_*-Herkunft) und
    im geteilten listings_cache (confirmed_by, data.ingested_by_*) — bis
    zur TTL bzw. bei listings_cache bis inseratscache_rotieren (Ablauf plus
    Karenz, Audit 13.09.2026 #35). Hier laeuft je Zyklus ein
    Nachlauf fuer alle Firmen-IDs, die nicht mehr in dealers existieren.
    Liefert die Zahl der bereinigten Dokumente."""
    kandidaten: set = set()
    for coll, feld in (("link_jobs", "dealer_ids"),
                       ("link_jobs", "requested_by_dealer"),
                       ("listings_cache_client", "dealer_id"),
                       ("listings_cache", "confirmed_by"),
                       ("listings_cache", "data.ingested_by_dealer"),
                       # Runde 18: Kaufvorgaenge fehlten in der Loeschkaskade
                       # (jetzt ergaenzt) — Altbestand aus frueher geloeschten
                       # Firmen wird hier nachtraeglich entfernt.
                       ("kaufvorgaenge", "dealer_id")):
        try:
            werte = await db[coll].distinct(feld)
        except Exception as exc:                        # noqa: BLE001
            log.warning("firmenreste: %s.%s nicht lesbar: %s", coll, feld, exc)
            continue
        kandidaten.update(v for v in werte if isinstance(v, str) and v)
    if not kandidaten:
        return 0
    vorhanden = set(await db.dealers.distinct("id", {"id": {"$in": list(kandidaten)}}))
    weg = sorted(kandidaten - vorhanden)
    if not weg:
        return 0
    n = 0
    r = await db.link_jobs.update_many(
        {"dealer_ids": {"$in": weg}}, {"$pull": {"dealer_ids": {"$in": weg}}})
    n += r.modified_count
    # _process nutzt requested_by_dealer nur fuer fetch_listing(dealer_id=…),
    # leer ist dort zulaessig.
    r = await db.link_jobs.update_many(
        {"requested_by_dealer": {"$in": weg}}, {"$set": {"requested_by_dealer": ""}})
    n += r.modified_count
    r = await db.listings_cache_client.delete_many({"dealer_id": {"$in": weg}})
    n += r.deleted_count
    r = await db.kaufvorgaenge.delete_many({"dealer_id": {"$in": weg}})
    n += r.deleted_count
    r = await db.listings_cache.update_many(
        {"confirmed_by": {"$in": weg}}, {"$pull": {"confirmed_by": {"$in": weg}}})
    n += r.modified_count
    r = await db.listings_cache.update_many(
        {"data.ingested_by_dealer": {"$in": weg}},
        {"$unset": {"data.ingested_by_dealer": "", "data.ingested_by_user": ""}})
    n += r.modified_count
    if n:
        log.info("firmenreste_bereinigen: %d Dokumente fuer %d geloeschte Firmen",
                 n, len(weg))
    return n


async def haengende_zustellungen_markieren(db, now: datetime,
                                           nach_minuten: int = 15) -> int:
    """Versand-Eintraege, die seit `nach_minuten` auf "laeuft" stehen, auf
    "unklar" setzen und einen Betriebsalarm ausloesen.

    So ein Eintrag entsteht, wenn der Prozess zwischen Reservierung und
    Ergebnis gestorben ist. Ob die Mail rausging, weiss der Server dann
    nicht — deshalb wird hier NICHT erneut gesendet. Der Nutzer sieht
    "unklar" am Vertrag; klickt er erneut auf Senden, laeuft der Versand
    unter demselben Schluessel weiter, und Resend stellt nicht doppelt zu.
    """
    grenze = (now - timedelta(minutes=nach_minuten)).isoformat()
    n = 0
    cursor = db.generated_pdfs.find(
        {"send_status": {"$elemMatch": {"zustellung": "laeuft",
                                        "sent_at": {"$lt": grenze}}}},
        {"_id": 0, "id": 1, "dealer_id": 1, "contract_no": 1, "send_status": 1})
    async for c in cursor:
        for e in c.get("send_status") or []:
            if e.get("zustellung") != "laeuft" or (e.get("sent_at") or "") >= grenze:
                continue
            res = await db.generated_pdfs.update_one(
                {"id": c["id"], "send_status": {"$elemMatch": {
                    "idempotency_key": e.get("idempotency_key"),
                    "zustellung": "laeuft"}}},
                {"$set": {"send_status.$.zustellung": "unklar"}})
            if res.modified_count:
                n += 1
                try:
                    from betrieb import alarm
                    await alarm(db, "zustellung_unklar", ref=c["id"],
                                dealer_id=c.get("dealer_id"),
                                vertrag=c.get("contract_no"),
                                empfaenger=e.get("recipient"),
                                hinweis="Versand bekam kein Ergebnis (Prozess "
                                        "abgebrochen?). Nutzer kann erneut senden — "
                                        "kein Doppelversand dank Idempotency-Key.")
                except Exception:                       # noqa: BLE001
                    pass
    return n


async def _archive_expired_bestand(db, now: datetime) -> int:
    """Bestand > 50 Tage: löscht NUR Fotos + Inserats-Entwürfe. Vertrag,
    Abholbericht, Einkaufspreis und Historie bleiben — Fahrzeug wird
    `archiviert` (geschäftliche Nachvollziehbarkeit)."""
    archived = 0
    # Runde 17 (Nr. 287): Selbstheilung — Bestandsfahrzeuge OHNE Frist
    # (Altbestand, abgebrochener Zwei-Schritt-Write vor Runde 17) bekommen
    # sie aus lifecycle_changed_at + 50 Tage nachgetragen; sonst blieben sie
    # fuer immer im Bestand, obwohl die Regel "50 Tage" heisst.
    try:
        from routes.bestand import BESTAND_RETENTION_DAYS as _tage
    except Exception:  # noqa: BLE001
        _tage = 50
    async for v in db.vehicles.find(
            {"lifecycle": "bestand", "bestand.expires_at": {"$in": [None]}},
            {"_id": 0, "id": 1, "dealer_id": 1, "lifecycle_changed_at": 1,
             "updated_at": 1, "created_at": 1}):
        basis = v.get("lifecycle_changed_at") or v.get("updated_at") or v.get("created_at")
        try:
            start = datetime.fromisoformat(str(basis)) if basis else now
        except ValueError:
            start = now
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        frist = (start + timedelta(days=_tage)).isoformat()
        await db.vehicles.update_one(
            {"id": v["id"], "dealer_id": v.get("dealer_id"), "lifecycle": "bestand",
             "bestand.expires_at": {"$in": [None]}},
            {"$set": {"bestand.expires_at": frist,
                      "bestand.frist_nachgetragen_at": now.isoformat()}})
        log.info("bestand: Frist fuer %s/%s nachgetragen (%s)",
                 v.get("dealer_id"), v["id"], frist)
    # Audit 13.09.2026 (#48): Nachholung — archivierte Fahrzeuge, deren
    # Aufraeumen (Inserats-Entwuerfe, Snapshots) in einem frueheren Lauf
    # scheiterte. Vorher: Fehler nur geloggt, das Fahrzeug fiel mit dem
    # Statuswechsel aus dem Selektor und der Entwurf blieb samt Fotos fuer
    # immer liegen. VOR der Hauptschleife, damit ein neuer Fehlschlag nicht
    # im selben Lauf gleich zweimal versucht wird.
    async for v in db.vehicles.find(
            {"lifecycle": "archiviert", "archiv_aufraeumen_offen": True},
            {"_id": 0, "id": 1, "dealer_id": 1}):
        firma = v.get("dealer_id", "")
        if await _archiv_nebenaufraeumen(db, v["id"], firma):
            await alarm_schliessen(db, "bestand_archiv_aufraeumen_offen",
                                   ref=f"{firma}/{v['id']}")
        else:
            await alarm(db, "bestand_archiv_aufraeumen_offen", ref=f"{firma}/{v['id']}",
                        dealer_id=firma, vehicle_id=v["id"],
                        hinweis="Inserats-Entwuerfe/Snapshots eines archivierten "
                                "Fahrzeugs lassen sich wiederholt nicht raeumen — "
                                "wird stuendlich erneut versucht.")

    frist_abgelaufen = {"$lte": now.isoformat(), "$ne": None}
    # Audit 13.09.2026 (#50): nur die Fotofelder lesen und schreiben (Muster
    # der Tagesregel oben, Runde 18). Vorher ging das ganze data-Objekt aus
    # dem Lesestand zurueck — eine zwischenzeitliche Kilometer- oder
    # Stammdaten-Korrektur war still verloren.
    cursor = db.vehicles.find(
        {"lifecycle": "bestand", "bestand.expires_at": frist_abgelaufen},
        {"_id": 0, "id": 1, "dealer_id": 1,
         **{f"data.{key}": 1 for key in _iter_photo_keys()}},
    )
    async for v in cursor:
        vid = v["id"]
        # Pruefbericht Runde 8: dieselbe Fahrzeug-ID kann bei mehreren
        # Firmen existieren (v_<Inserat>). Entwuerfe und das Fahrzeug selbst
        # werden deshalb NUR innerhalb der eigenen Firma angefasst — sonst
        # verlor Firma B ihre Inserats-Fotos, weil bei Firma A die Frist ablief.
        firma = v.get("dealer_id", "")
        data = v.get("data")
        # Bei data null/kein Objekt nie in data.* schreiben ("Cannot create field").
        leeren = ({f"data.{key}": [] for key in _iter_photo_keys() if data.get(key)}
                  if isinstance(data, dict) else {})
        # Audit 13.09.2026 (#49): Statuswechsel als CAS VOR dem Aufraeumen.
        # Vorher lief der Write ohne Bedingung auf dem Lesestand: klickte der
        # Chef waehrenddessen "Weiterverkaufen"/"Frist verlaengern"/"Loeschen",
        # wurde seine Entscheidung zu "archiviert" (Endzustand) ueberschrieben
        # und sein neuer Entwurf mitgeloescht. Trifft der CAS nicht, bleibt
        # alles unberuehrt. Der Marker haelt das Aufraeumen bis zum Erfolg offen.
        r = await db.vehicles.update_one(
            {"id": vid, "dealer_id": firma, "lifecycle": "bestand",
             "bestand.expires_at": frist_abgelaufen},
            {"$set": {**leeren, "lifecycle": "archiviert",
                      "lifecycle_changed_at": now.isoformat(),
                      "archived_at": now.isoformat(),
                      "archiv_aufraeumen_offen": True}},
        )
        if r.matched_count == 0:
            log.info("bestand archive: %s/%s zwischenzeitlich geaendert — uebersprungen",
                     firma, vid)
            continue
        archived += 1
        try:
            await db.activity_logs.insert_one({
                "id": __import__("uuid").uuid4().hex,
                "dealer_id": v.get("dealer_id"), "user_id": "",
                "action": "fahrzeug.archiviert.50tage", "ref": vid,
                "meta": {}, "created_at": now.isoformat(),
            })
        except Exception:  # noqa: BLE001
            # Zustand ist schon geschrieben — der Lauf geht weiter.
            log.exception("bestand archive: Audit-Eintrag fuer %s/%s fehlt", firma, vid)
        await _archiv_nebenaufraeumen(db, vid, firma)
    return archived


async def _archiv_nebenaufraeumen(db, vid: str, firma: str) -> bool:
    """Audit 13.09.2026 (#48): Inserats-Entwuerfe (samt hochgeladener Fotos)
    und Snapshots eines archivierten Fahrzeugs entfernen, danach den Marker
    `archiv_aufraeumen_offen` loeschen. Wirft nie — vorher lief ein Fehler in
    _delete_snapshots_for_vehicle durch _cleanup_once und liess fuer diesen
    Zyklus u.a. die 90-Tage-Vertragsloeschung aus. Beide Schritte sind
    idempotent. True = erledigt."""
    try:
        # Nicht loeschbare Fotos werden vorgemerkt, das Inserat bleibt dann
        # bis zur Nachholung mit `loeschung_offen` stehen (kein Key geht verloren).
        async for listing in db.resale_listings.find(
            {"vehicle_id": vid, "dealer_id": firma,
             "status": {"$in": ["entwurf", "verkaufsbereit"]}},
            {"_id": 0, "id": 1, "photos": 1},
        ):
            await _inserat_mit_fotos_loeschen(
                db, listing, grund="bestand_archiv_inserat", dealer_id=firma)
        await _delete_snapshots_for_vehicle(db, vid, dealer_id=firma)
        await db.vehicles.update_one(
            {"id": vid, "dealer_id": firma, "lifecycle": "archiviert"},
            {"$unset": {"archiv_aufraeumen_offen": ""}})
    except Exception as exc:  # noqa: BLE001
        log.warning("bestand archive: Aufraeumen fuer %s/%s fehlgeschlagen, "
                    "wird nachgeholt: %s", firma, vid, exc)
        return False
    return True


async def run_cleanup_forever(db):
    """Endlosschleife; wird beim FastAPI-Startup als Task gestartet."""
    # kurze Verzögerung beim Start, damit andere Init-Jobs fertig werden
    await asyncio.sleep(30)
    from job_lock import acquire
    while True:
        # Bei mehreren Worker-Prozessen raeumt nur EINER pro Zyklus auf —
        # sonst loeschen acht Prozesse gleichzeitig dieselben Dateien.
        if not await acquire(db, "cleanup-cycle",
                             ttl_seconds=CLEANUP_INTERVAL_SECONDS - 60):
            await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)
            continue
        try:
            await _cleanup_once(db)
        except Exception as exc:  # noqa: BLE001
            log.exception("cleanup loop error: %s", exc)
        try:
            await _reap_stuck_snapshots(db)
        except Exception as exc:  # noqa: BLE001
            log.exception("snapshot reaper error: %s", exc)
        try:
            await _expire_old_snapshots(db)
        except Exception as exc:  # noqa: BLE001
            log.exception("snapshot expiry error: %s", exc)
        try:
            from beweis_service import beweise_verfallen
            await beweise_verfallen(db)
        except Exception as exc:  # noqa: BLE001
            log.exception("beweis expiry error: %s", exc)
        await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)


async def _reap_stuck_snapshots(db) -> None:
    """Haengengebliebene Snapshot-Jobs heilen: alles, was seit >15 min in
    pending/running/retrying steckt (z.B. weil das Backend mittendrin neu
    gestartet wurde), wird als failed markiert — das Frontend hoert auf zu
    pollen und der naechste Vergleich erzeugt einen frischen Job."""
    from datetime import datetime, timedelta, timezone
    # Review 09/2026: Wartende Jobs (queued/pending) bekommen 60 min — sie
    # laufen ja noch gar nicht; nur echte 'running' gelten nach 15 min ab
    # Start als haengend.
    jetzt = datetime.now(timezone.utc)
    cutoff = (jetzt - timedelta(minutes=15)).isoformat()
    cutoff_wartend = (jetzt - timedelta(minutes=60)).isoformat()
    # Runde 17 (Nr. 316): Massstab ist das Lebenszeichen heartbeat_at (alle
    # 30 s vom laufenden Job), ersatzweise started_at, zuletzt created_at —
    # vorher galt eine lange, aber lebende Aufnahme nach 15 min als haengend
    # und wurde auf failed gesetzt, waehrend der Job weiterlief.
    r = await db.listing_snapshots.update_many(
        {"$or": [
            {"status": "running",
             "$or": [{"heartbeat_at": {"$lt": cutoff}},
                     {"heartbeat_at": None, "started_at": {"$lt": cutoff}},
                     {"heartbeat_at": None, "started_at": None,
                      "created_at": {"$lt": cutoff}}]},
            {"status": {"$in": ["pending", "queued", "retrying"]},
             "created_at": {"$lt": cutoff_wartend}}]},
        {"$set": {"status": "failed",
                  "error": "Zeitueberschreitung — automatisch abgebrochen "
                           "(Backend-Neustart oder haengender Job).",
                  "completed_at": datetime.now(timezone.utc).isoformat()}})
    if r.modified_count:
        log.info("snapshot reaper: %d haengende Jobs bereinigt", r.modified_count)


# ---------------------------------------------------------------------------
# Snapshot-Verfall (Beschluss 08/2026): Beweis-Snapshots werden 60 Tage
# aufbewahrt, danach werden die Dateien geloescht (Speicher waechst sonst
# unbegrenzt — bei 500k neuen Inseraten/Monat ~300 GB monatlich).
# AUSNAHME: Snapshots zu Fahrzeugen, fuer die ein KAUFVERTRAG existiert,
# bleiben fuer immer — sie sind Teil des Beweis-Archivs des Vertrags.
# Die Inserats-DATEN (listings_cache) haben ihre eigene Frist (expires_at plus
# Karenz, inseratscache_rotieren — Audit 13.09.2026 #35); bis dahin erzeugt
# ein erneuter Vergleich bei Bedarf einen frischen Snapshot, ohne die Quelle
# fuer die Daten erneut anzurufen.
# ---------------------------------------------------------------------------
SNAPSHOT_RETENTION_DAYS = int(os.environ.get("SNAPSHOT_RETENTION_DAYS", "60"))


async def _expire_old_snapshots(db) -> int:
    cutoff = (datetime.now(timezone.utc)
              - timedelta(days=SNAPSHOT_RETENTION_DAYS)).isoformat()
    # Fahrzeuge mit Kaufvertrag sind geschuetzt (Beweis!).
    protected = set()
    async for c in db.generated_pdfs.find({}, {"_id": 0, "vehicle_id": 1}):
        if c.get("vehicle_id"):
            protected.add(c["vehicle_id"])
    n = 0
    cursor = db.listing_snapshots.find(
        {"status": "ready", "completed_at": {"$lt": cutoff},
         "loeschung_offen": {"$ne": True}},
        {"_id": 0, "id": 1, "vehicle_id": 1, "pdf_path": 1, "png_path": 1})
    async for snap in cursor:
        if snap.get("vehicle_id") in protected:
            continue
        # Snapshots liegen im SNAPSHOT-Storage (snapshot_service) — der
        # Upload-Storage kannte diese Pfade nicht, der Verfall loeschte
        # nichts (Runde 4). Nicht loeschbare Dateien bleiben referenziert
        # (`loeschung_offen`) und werden nachgeholt.
        unset, offen = {}, False
        for feld in ("pdf_path", "png_path"):
            key = snap.get(feld)
            if not key:
                continue
            ok = await loeschen_oder_vormerken(
                db, key=key, art="snapshot", grund="snapshot_verfall",
                ref={"collection": "listing_snapshots", "id": snap["id"],
                     "unset_fields": [feld]})
            if ok:
                unset[feld] = ""
            else:
                offen = True
        upd = {"$set": {"status": "expired", "expired_at": now_iso()}}
        if offen:
            upd["$set"]["loeschung_offen"] = True
        if unset:
            upd["$unset"] = unset
        await db.listing_snapshots.update_one({"id": snap["id"]}, upd)
        n += 1
    if n:
        log.info("[cleanup] %s Snapshots nach %s Tagen verfallen",
                 n, SNAPSHOT_RETENTION_DAYS)
    geraeumt = await _snapshot_reste_entfernen(db)
    if geraeumt:
        log.info("[cleanup] %s failed/expired-Snapshotzeilen entfernt", geraeumt)
    return n


# Runde 17 (Nr. 307): failed-/expired-Zeilen ohne Dateien sind nach dieser
# Frist nur noch Ballast (der Vergleich legt bei Bedarf einen neuen an).
SNAPSHOT_RESTE_TAGE = 7


async def _snapshot_reste_entfernen(db) -> int:
    """Zweiter Sweep: listing_snapshots mit status failed/expired, aelter als
    SNAPSHOT_RESTE_TAGE (completed_at, sonst created_at). Vorher blieben
    diese Zeilen fuer immer (die Tabelle wuchs mit jedem Fehlversuch).
    failed-Zeilen, die noch Dateipfade tragen (Abbruch nach Upload):
    zuerst die Dateien loeschen bzw. vormerken, dann die Pfade leeren —
    geloescht wird die Zeile erst, wenn keine Datei mehr an ihr haengt."""
    cutoff = (datetime.now(timezone.utc)
              - timedelta(days=SNAPSHOT_RESTE_TAGE)).isoformat()
    alt = {"$or": [{"completed_at": {"$lt": cutoff}},
                   {"completed_at": {"$in": [None, ""]},
                    "created_at": {"$lt": cutoff}}]}
    async for snap in db.listing_snapshots.find(
            {"status": "failed", "loeschung_offen": {"$ne": True},
             "$and": [alt, {"$or": [{"png_path": {"$nin": [None, ""]}},
                                    {"pdf_path": {"$nin": [None, ""]}}]}]},
            {"_id": 0, "id": 1, "dealer_id": 1, "png_path": 1, "pdf_path": 1}):
        unset, offen = {}, False
        for feld in ("png_path", "pdf_path"):
            key = snap.get(feld)
            if not key:
                continue
            ok = await loeschen_oder_vormerken(
                db, key=key, art="snapshot", grund="snapshot_failed_reste",
                dealer_id=snap.get("dealer_id") or "",
                ref={"collection": "listing_snapshots", "id": snap["id"],
                     "unset_fields": [feld]})
            if ok:
                unset[feld] = ""
            else:
                offen = True
        upd: dict = {}
        if unset:
            upd["$unset"] = unset
        if offen:
            upd["$set"] = {"loeschung_offen": True}
        if upd:
            await db.listing_snapshots.update_one({"id": snap["id"]}, upd)
    r = await db.listing_snapshots.delete_many(
        {"status": {"$in": ["failed", "expired"]},
         "loeschung_offen": {"$ne": True},
         "png_path": {"$in": [None, ""]}, "pdf_path": {"$in": [None, ""]},
         **alt})
    return r.deleted_count
