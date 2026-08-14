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
erlaubte Übergänge validiert und jede Änderung im Audit-Log protokolliert.
Alte Freitext-Status (vehicles.status) bleiben als `legacy_status` erhalten.
"""
from typing import Optional

from deps import db, log_activity, now_iso

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
    "verkaufsentwurf":  {"verkaufsbereit", "bestand", "geloescht"},
    # reserviert/verkauft auch direkt aus verkaufsbereit — solange kein
    # Marktplatz existiert (Phase 1/2), wird ohne "veroeffentlicht" verkauft.
    "verkaufsbereit":   {"veroeffentlicht", "reserviert", "verkauft",
                         "verkaufsentwurf", "bestand", "geloescht"},
    "veroeffentlicht":  {"reserviert", "verkauft", "verkaufsbereit"},
    "reserviert":       {"verkauft", "veroeffentlicht"},
    "verkauft":         {"archiviert"},
    "storniert":        {"verglichen", "geloescht"},
    "geloescht":        set(),
    "archiviert":       set(),
}

# Mapping der alten Freitext-Status auf den neuen Lebenszyklus (Migration).
_LEGACY_MAP = {
    "verglichen": "verglichen",
    "Vertrag erstellt": "vertrag_erstellt",
    "Termin erstellt": "abholung_geplant",
}


class LifecycleError(ValueError):
    """Unerlaubter Statusübergang."""


async def set_lifecycle(
    vehicle_id: str, dealer_id: str, new_state: str, *,
    user: Optional[dict] = None, force: bool = False,
) -> dict:
    """Setzt den Lebenszyklus-Status eines Fahrzeugs.

    Validiert den Übergang (außer force=True, z.B. für Migrationen) und
    schreibt einen Audit-Log-Eintrag. Gibt das aktualisierte Fahrzeug zurück.
    """
    if new_state not in LIFECYCLE_STATES:
        raise LifecycleError(f"Unbekannter Status: {new_state}")
    v = await db.vehicles.find_one({"id": vehicle_id, "dealer_id": dealer_id})
    if not v:
        raise LifecycleError("Fahrzeug nicht gefunden")
    current = v.get("lifecycle") or _LEGACY_MAP.get(v.get("status") or "", "verglichen")
    if current == new_state:
        return v
    if not force and new_state not in ALLOWED_TRANSITIONS.get(current, set()):
        raise LifecycleError(
            f"Übergang '{current}' → '{new_state}' ist nicht erlaubt")
    await db.vehicles.update_one(
        {"id": vehicle_id, "dealer_id": dealer_id},
        {"$set": {
            "lifecycle": new_state,
            "lifecycle_changed_at": now_iso(),
            "updated_at": now_iso(),
        }},
    )
    await log_activity(
        dealer_id, (user or {}).get("id", ""), f"fahrzeug.status.{new_state}",
        ref=vehicle_id, meta={"von": current, "nach": new_state},
    )
    v["lifecycle"] = new_state
    return v


async def try_set_lifecycle(vehicle_id: str, dealer_id: str, new_state: str, *,
                            user: Optional[dict] = None) -> None:
    """Best-effort-Variante für Hooks in bestehenden Flows: ein ungültiger
    Übergang (z.B. zweiter Vertrag für dasselbe Fahrzeug) darf den
    Hauptvorgang niemals abbrechen."""
    try:
        await set_lifecycle(vehicle_id, dealer_id, new_state, user=user)
    except LifecycleError:
        pass


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
