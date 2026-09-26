"""Fahrzeug-Lebenszyklus (Statusmaschine) für das B2B-Händlermodul.

Jedes Fahrzeug durchläuft einen festen Lebenszyklus:

    gefunden → verglichen → besichtigung → verhandlung → vertrag_erstellt
             → gekauft → abholung_geplant → abgeholt
                                             ├─ bestand           (nur gespeichert)
                                             ├─ verkaufsentwurf → verkaufsbereit
                                             │        → veroeffentlicht → reserviert → verkauft
                                             └─ geloescht
    Seitenausgänge: nicht_abgeholt, storniert, archiviert

Der Status wird ausschließlich über `set_lifecycle()` geändert — dort werden
erlaubte Übergänge validiert und jede Änderung im Audit-Log protokolliert
(Audit best effort nach dem Write, Audit 13.09.2026 #46).
Alte Freitext-Status (vehicles.status) bleiben als `legacy_status` erhalten.
"""
import logging
from typing import Optional

from deps import db, log_activity_sicher, now_iso

# Reihenfolge dient auch der Anzeige (Fortschrittsbalken im Frontend).
LIFECYCLE_STATES = [
    "gefunden",
    "verglichen",
    "besichtigung",
    "verhandlung",
    "vertrag_erstellt",
    "gekauft",
    "abholung_geplant",
    "abgeholt",
    "bestand",
    "verkaufsentwurf",
    "verkaufsbereit",
    "veroeffentlicht",
    "reserviert",
    "verkauft",
    # Seitenausgänge
    "nicht_abgeholt",
    "storniert",
    "geloescht",
    "archiviert",
]

# Erlaubte Folge-Status. "*" = aus jedem Status erreichbar (Admin/Aufräumen).
ALLOWED_TRANSITIONS: dict = {
    "gefunden":         {"verglichen", "besichtigung", "storniert"},
    "verglichen":       {"besichtigung", "verhandlung", "vertrag_erstellt", "storniert"},
    "besichtigung":     {"verhandlung", "vertrag_erstellt", "storniert"},
    "verhandlung":      {"vertrag_erstellt", "storniert"},
    # Weiterverkauf ist schon ab Vertragserstellung erlaubt (Wunsch 08/2026):
    # der Chef kann inserieren, waehrend die Abholung noch laeuft. Der
    # Abholbericht landet weiterhin in der Fahrzeugakte (pickup_reports).
    "vertrag_erstellt": {"gekauft", "abholung_geplant", "verkaufsentwurf", "storniert"},
    "gekauft":          {"abholung_geplant", "abgeholt", "verkaufsentwurf", "storniert"},
    "abholung_geplant": {"abgeholt", "nicht_abgeholt", "verkaufsentwurf", "storniert"},
    "abgeholt":         {"bestand", "verkaufsentwurf", "geloescht"},
    "nicht_abgeholt":   {"abholung_geplant", "storniert", "geloescht"},
    "bestand":          {"verkaufsentwurf", "geloescht", "archiviert"},
    # Rollenprüfung 22.09.2026 (RP-518): Rueckweg in den Kaufzustand, wenn ein
    # VOR der Abholung angelegtes Inserat geloescht wird (siehe
    # VERKAUF_RUECKWEG_VOR_ABHOLUNG — nur ueber set_lifecycle, nie ueber
    # try_set_lifecycle).
    "verkaufsentwurf":  {"verkaufsbereit", "bestand", "geloescht",
                         "gekauft", "abholung_geplant"},
    # reserviert/verkauft auch direkt aus verkaufsbereit — solange kein
    # Marktplatz existiert (Phase 1/2), wird ohne "veroeffentlicht" verkauft.
    "verkaufsbereit":   {"veroeffentlicht", "reserviert", "verkauft",
                         "verkaufsentwurf", "bestand", "geloescht",
                         "gekauft", "abholung_geplant"},
    "veroeffentlicht":  {"reserviert", "verkauft", "verkaufsbereit", "bestand"},
    "reserviert":       {"verkauft", "veroeffentlicht"},
    "verkauft":         {"archiviert"},
    "storniert":        {"verglichen", "geloescht"},
    "geloescht":        set(),
    "archiviert":       set(),
}

#: Rollenprüfung 22.09.2026 (RP-518): Rueckweg aus dem Verkaufsblock in den
#: Kaufzustand VOR der Abholung. Inserieren ist schon ab Vertragserstellung
#: erlaubt; wurde so ein Inserat geloescht, setzte routes/resale.delete_listing
#: das Fahrzeug bisher auf "bestand" — danach aenderte "nicht abgeholt" nichts
#: mehr (kaufvorgang._schritte kennt keinen Weg aus "bestand"), und nach 50
#: Tagen archivierte der Aufraeumer ein nie abgeholtes Auto.
#: Diese Uebergaenge stehen in ALLOWED_TRANSITIONS (resale._pfad_zurueck sucht
#: dort), sind aber NUR ueber einen ausdruecklichen set_lifecycle-Aufruf
#: erlaubt: try_set_lifecycle — die Best-effort-Hooks von Vertrag, Termin,
#: Fahrer-App und Aufraeumer — lehnt sie ab. Sonst zoege ein neuer Termin ohne
#: Vertrag ein Fahrzeug mit aktivem Inserat still aus dem Verkauf (Inserat
#: "entwurf", Fahrzeug "abholung_geplant"), genau wie beim Rueckweg aus
#: "abgeholt" (abholung_zuruecknehmen).
VERKAUF_RUECKWEG_VOR_ABHOLUNG = {
    "verkaufsentwurf": frozenset({"gekauft", "abholung_geplant"}),
    "verkaufsbereit": frozenset({"gekauft", "abholung_geplant"}),
}

# Mapping der alten Freitext-Status auf den neuen Lebenszyklus (Migration).
_LEGACY_MAP = {
    "verglichen": "verglichen",
    "Vertrag erstellt": "vertrag_erstellt",
    "Termin erstellt": "abholung_geplant",
}


log = logging.getLogger("autohandel")


class LifecycleError(ValueError):
    """Unerlaubter Statusübergang."""


#: Welchen Fahrzeugzustand ein abgeschlossener Terminstatus bedeutet.
#: Nachpruefung 20.09.2026, Nr. 37/38: Das stand vorher an ZWEI Stellen
#: verschieden. Das Buero (routes/appointments.py) bildete nur "abgeholt"
#: und "nicht abgeholt" ab; die Fahrer-App (routes/drivers.py) schrieb
#: "abgeholt" if status == "abgeholt" else "nicht_abgeholt" — und machte
#: damit aus "erledigt" und "storniert" ein "nicht_abgeholt". Derselbe
#: Endstatus erzeugte also je nach Weg verschiedene Fahrzeugzustaende.
#: Jetzt entscheidet diese eine Tabelle.
#:
#: "erledigt" zaehlt als abgeschlossene Abholung — drivers.py,
#: protocols.py und cleanup_service.CLEANUP_RULES behandeln es laengst so.
#: "storniert" bleibt bewusst OHNE Zuordnung: ein abgesagter Termin sagt
#: nichts darueber, ob das Fahrzeug spaeter geholt wird; ein Endzustand
#: waere hier eine Behauptung. Das Buero hat es nie gesetzt, und dabei
#: bleibt es.
TERMINSTATUS_FAHRZEUGZUSTAND = {
    "abgeholt": "abgeholt",
    "erledigt": "abgeholt",
    "nicht abgeholt": "nicht_abgeholt",
    "nicht_abgeholt": "nicht_abgeholt",
}


def zustand_fuer_terminstatus(status: str) -> Optional[str]:
    """Fahrzeugzustand zu einem Terminstatus — None heisst "nichts aendern"."""
    return TERMINSTATUS_FAHRZEUGZUSTAND.get(str(status or "").strip().lower())


async def set_lifecycle(
    vehicle_id: str, dealer_id: str, new_state: str, *,
    user: Optional[dict] = None, force: bool = False,
    extra_set: Optional[dict] = None, extra_unset: Optional[dict] = None,
    verkauf_rueckweg: bool = True,
) -> dict:
    """Setzt den Lebenszyklus-Status eines Fahrzeugs.

    Validiert den Übergang (außer force=True, z.B. für Migrationen) und
    schreibt einen Audit-Log-Eintrag. Gibt das aktualisierte Fahrzeug zurück.

    Rollenprüfung 22.09.2026 (RP-518): verkauf_rueckweg=False sperrt die
    Uebergaenge aus VERKAUF_RUECKWEG_VOR_ABHOLUNG (so ruft try_set_lifecycle).
    """
    if new_state not in LIFECYCLE_STATES:
        raise LifecycleError(f"Unbekannter Status: {new_state}")
    v = await db.vehicles.find_one({"id": vehicle_id, "dealer_id": dealer_id})
    if not v:
        raise LifecycleError("Fahrzeug nicht gefunden")
    current = v.get("lifecycle") or _LEGACY_MAP.get(v.get("status") or "", "verglichen")
    # Runde 17: Der Write prueft den GELESENEN Zustand mit (CAS) — ein
    # paralleler Statuswechsel zwischen Lesen und Schreiben wird nicht mehr
    # ueberschrieben. Altdokumente ohne lifecycle-Feld: Feld darf nicht
    # inzwischen entstanden sein.
    cas: dict = {"id": vehicle_id, "dealer_id": dealer_id,
                 "lifecycle": v["lifecycle"] if "lifecycle" in v else {"$exists": False}}
    if current == new_state:
        # Zustand steht schon — nur die mitgegebenen Zusatzfelder schreiben
        # (z.B. Bestandsfrist), ohne zweiten Statuswechsel/Audit.
        if extra_set or extra_unset:
            upd: dict = {}
            if extra_set:
                upd["$set"] = {**extra_set, "updated_at": now_iso()}
            if extra_unset:
                upd["$unset"] = dict(extra_unset)
            r = await db.vehicles.update_one(cas, upd)
            if r.matched_count == 0:
                raise LifecycleError("Fahrzeugstatus wurde zwischenzeitlich geändert — bitte neu laden")
        return v
    if not force and new_state not in ALLOWED_TRANSITIONS.get(current, set()):
        raise LifecycleError(
            f"Übergang '{current}' → '{new_state}' ist nicht erlaubt")
    if not force and not verkauf_rueckweg \
            and new_state in VERKAUF_RUECKWEG_VOR_ABHOLUNG.get(current, ()):
        raise LifecycleError(
            f"Übergang '{current}' → '{new_state}' nur beim Löschen des Inserats")
    # Runde 17: Zusatzfelder (Fotos leeren, Bestandsfrist, deleted_at ...)
    # im SELBEN Write wie der Statuswechsel — kein Zwischenzustand mehr
    # ("geloescht" ohne Fotoloeschung, "bestand" ohne Frist).
    upd = {"$set": {
        "lifecycle": new_state,
        "lifecycle_changed_at": now_iso(),
        "updated_at": now_iso(),
        **(extra_set or {}),
    }}
    if extra_unset:
        upd["$unset"] = dict(extra_unset)
    r = await db.vehicles.update_one(cas, upd)
    if r.matched_count == 0:
        raise LifecycleError("Fahrzeugstatus wurde zwischenzeitlich geändert — bitte neu laden")
    # Audit 13.09.2026 (#46), Runde-17-Muster: Der Statuswechsel ist bereits
    # geschrieben (CAS) — das Audit darf ihn nicht mehr als Fehler melden.
    # Vorher lief eine Exception hier an allen `except LifecycleError`-
    # Rueckbauten vorbei (Fahrzeug im Zwischenstatus, Inserate zum geloeschten
    # Fahrzeug blieben offen, Fahrer-App sah 500). Fehler stehen im Log.
    await log_activity_sicher(
        dealer_id, (user or {}).get("id", ""), f"fahrzeug.status.{new_state}",
        ref=vehicle_id, meta={"von": current, "nach": new_state},
    )
    v["lifecycle"] = new_state
    return v


async def try_set_lifecycle(vehicle_id: str, dealer_id: str, new_state: str, *,
                            user: Optional[dict] = None) -> bool:
    """Best-effort-Variante für Hooks in bestehenden Flows: ein ungültiger
    Übergang (z.B. zweiter Vertrag für dasselbe Fahrzeug) darf den
    Hauptvorgang niemals abbrechen. Phase 2 (15.09.2026, G5): liefert True,
    wenn der Status gesetzt wurde (oder schon stand), sonst False — die
    Fahrzeug-Zusammenfassung meldet dann keinen falschen Erfolg mehr.
    Rollenprüfung 22.09.2026 (RP-518): nie den Rueckweg aus dem Verkaufsblock
    (VERKAUF_RUECKWEG_VOR_ABHOLUNG) — ein Hook zieht kein inseriertes Fahrzeug
    aus dem Verkauf."""
    try:
        await set_lifecycle(vehicle_id, dealer_id, new_state, user=user,
                            verkauf_rueckweg=False)
        return True
    except LifecycleError as exc:
        # Runde 17: nicht mehr stumm — im Log nachvollziehbar, warum ein
        # Fahrzeug nach Vertrag/Termin nicht mitgezogen wurde.
        log.info("Lifecycle uebersprungen %s -> %s: %s", vehicle_id, new_state, exc)
        return False


#: Pruefbericht 20.09.2026 (V-12), Entscheidung Ahmad 21.09.2026: Der Chef darf
#: eine abgeholte Abholung nachtraeglich auf "storniert" bzw. "nicht abgeholt"
#: setzen. Das Fahrzeug blieb dabei "abgeholt" (ALLOWED_TRANSITIONS kennt
#: keinen Weg zurueck). Erlaubte Ziele des Rueckwegs:
ABHOLUNG_ZURUECK_ZIELE = frozenset({"gekauft", "abholung_geplant", "nicht_abgeholt", "storniert"})
_PREIS_EGAL = object()


async def abholung_zuruecknehmen(vehicle_id: str, dealer_id: str, ziel: str, *,
                                 kaufvorgang_id: Optional[str] = None,
                                 preis=_PREIS_EGAL, preis_entfernen: bool = False,
                                 user: Optional[dict] = None) -> bool:
    """Pruefbericht 20.09.2026 (V-12), Entscheidung Ahmad 21.09.2026: Fahrzeug
    aus "abgeholt" zuruecknehmen, weil die Abholung nachtraeglich storniert
    bzw. als "nicht abgeholt" gewertet wurde.

    Bewusst KEIN Eintrag in ALLOWED_TRANSITIONS["abgeholt"]: sonst setzte jedes
    try_set_lifecycle eines ANDEREN Termins am selben Auto (Fahrer-App,
    Terminplaner) ein abgeholtes Fahrzeug still zurueck. Dieser Weg ist streng:
    Compare-and-Set auf lifecycle == "abgeholt", den festgehaltenen Vorgang
    (abgeholt_kaufvorgang_id; None = Feld fehlt, Termin ohne Vorgang) und —
    wenn mitgegeben — den gelesenen Preis. Der Verweis auf den Vorgang faellt
    weg, der Einkaufspreis nur mit preis_entfernen (er stammte aus genau
    dieser Abholung). Liefert True, wenn geschrieben; False, wenn sich das
    Fahrzeug inzwischen geaendert hat (Aufrufer: Nacharbeit)."""
    if ziel not in ABHOLUNG_ZURUECK_ZIELE:
        raise LifecycleError(f"Rueckweg aus 'abgeholt' nach '{ziel}' ist nicht vorgesehen")
    filt: dict = {"id": vehicle_id, "dealer_id": dealer_id, "lifecycle": "abgeholt",
                  # None trifft auch das fehlende Feld
                  "abgeholt_kaufvorgang_id": kaufvorgang_id or None}
    if preis is not _PREIS_EGAL:
        filt["purchase_price"] = preis
    jetzt = now_iso()
    unset = {"abgeholt_kaufvorgang_id": ""}
    if preis_entfernen:
        unset["purchase_price"] = ""
    r = await db.vehicles.update_one(
        filt, {"$set": {"lifecycle": ziel, "lifecycle_changed_at": jetzt, "updated_at": jetzt},
               "$unset": unset})
    if r.matched_count == 0:
        log.info("Abholung an %s nicht zurueckgenommen (Stand geaendert) -> %s",
                 vehicle_id, ziel)
        return False
    await log_activity_sicher(
        dealer_id, (user or {}).get("id", ""), f"fahrzeug.status.{ziel}",
        ref=vehicle_id, meta={"von": "abgeholt", "nach": ziel,
                              "grund": "abholung_zurueckgenommen",
                              "kaufvorgang_id": kaufvorgang_id,
                              "preis_entfernt": bool(preis_entfernen)})
    return True


async def migrate_missing_lifecycles() -> int:
    """Startup-Migration: setzt `lifecycle` für Fahrzeuge, die noch keins
    haben, anhand des alten Freitext-Status + Terminlage. Idempotent."""
    migrated = 0
    cursor = db.vehicles.find({"lifecycle": {"$exists": False}},
                              {"_id": 0, "id": 1, "dealer_id": 1, "status": 1})
    async for v in cursor:
        state = _LEGACY_MAP.get(v.get("status") or "", "verglichen")
        # Termin bereits abgeholt? Dann ist das Fahrzeug weiter im Zyklus.
        appt = await db.appointments.find_one(
            {"vehicle_id": v["id"], "dealer_id": v["dealer_id"],
             "status": "abgeholt"},
            {"_id": 0, "id": 1},
        )
        if appt:
            state = "abgeholt"
        await db.vehicles.update_one(
            {"id": v["id"], "dealer_id": v["dealer_id"]},
            {"$set": {"lifecycle": state,
                      "legacy_status": v.get("status"),
                      "lifecycle_changed_at": now_iso()}},
        )
        migrated += 1
    return migrated
