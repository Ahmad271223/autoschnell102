# -*- coding: utf-8 -*-
"""Fahrzeugpool-Begrenzung (Wunsch 09/2026): je Firma bleiben nur die
neuesten N verglichenen Fahrzeuge (Default 30) — kommt ein neuer Vergleich,
faellt der aelteste raus. Geschuetzt bleiben Fahrzeuge, die schon in einen
Vertrag, einen Abholtermin oder ein Weiterverkaufs-Inserat uebernommen
wurden, sowie alles, was nicht mehr nur "verglichen" ist (Bestand etc.).
Snapshots werden NICHT angefasst (firmenuebergreifend geteilt; die
Aufbewahrung regelt cleanup_service).

Runde 23 (11.09.2026): Das Fahrzeug ist firmenweit GEMEINSAM
(owner_user_id = Hauptbearbeiter, mitbearbeiter_ids = weitere Sucher, die
denselben Link verglichen haben). Das Limit zaehlt je Hauptbearbeiter —
faellt ein Fahrzeug aus dessen Pool, das noch Mitbearbeiter hat, wird es
nicht mehr geloescht, sondern an den ersten aktiven Mitbearbeiter
uebergeben (zaehlt ab dann gegen dessen Pool). Geloescht wird nur, was
keinen (aktiven) Mitbearbeiter hat."""
import logging
import os

log = logging.getLogger("autohandel")

from konfig import zahl_env  # Pruefung 14.09.2026: keine Abstuerze durch .env-Tippfehler
POOL_MAX = zahl_env("FAHRZEUGPOOL_MAX_VERGLEICHE", 30, unten=1)


async def _aktive_konten(db, dealer_id: str, ids) -> set:
    """Runde 23: welche der Konten sind aktive Konten DIESER Firma (Chef
    oder Sucher)? Gleiche Regel wie der fruehere Besitzerwechsel durch den
    Chef (bis 21.09.2026, seitdem entfallen — R1-01): active=False zaehlt nicht."""
    ids = [i for i in ids if i]
    if not ids:
        return set()
    aktiv = set()
    async for u in db.users.find(
            {"id": {"$in": ids}, "dealer_id": dealer_id,
             "role": {"$in": ["dealer", "sucher"]}},
            {"_id": 0, "id": 1, "active": 1}):
        if u.get("active") is not False:
            aktiv.add(u["id"])
    return aktiv


SCHUTZ_SEKUNDEN = 300

# Rollenprüfung 22.09.2026 (RP-538): Vergleicht der CHEF ein Fahrzeug, das
# einem Sucher gehoert, wird er nicht Mitbearbeiter (RP-048). Ohne Schutz
# loeschte das Pool-Trimmen DES SUCHERS das Fahrzeug nach 30 weiteren
# Vergleichen — "Kaufvertrag erstellen" des Chefs endete dann mit 404
# "Fahrzeug nicht gefunden". So lange bleibt es nach dem Chef-Vergleich
# vom Trimmen ausgenommen (jeder weitere Chef-Vergleich verlaengert).
CHEF_VERGLEICH_SCHUTZ_SEKUNDEN = 7 * 24 * 3600


async def kurz_schuetzen(db, dealer_id: str, vehicle_id, sekunden: int = SCHUTZ_SEKUNDEN) -> None:
    """Runde 19 (16.09.2026, Nr. 37): waehrend ein Vertrag oder Termin zu
    einem Fahrzeug entsteht, darf das Pool-Trimmen es nicht loeschen. Der
    Schutzscan (Vertraege/Termine/Inserate) sieht den neuen Datensatz erst
    nach dem Insert — dazwischen schuetzt dieser kurze Stempel.

    Rollenprüfung 22.09.2026 (RP-538): $max statt $set — ein kurzer Stempel
    (Vertragsanlage, 5 Minuten) verkuerzt nie einen laengeren Schutz (Chef-
    Vergleich, 7 Tage). ISO-Zeitstempel gleichen Formats sortieren als Text
    richtig; so vergleicht auch das Trimmen."""
    if not dealer_id or not vehicle_id:
        return
    from datetime import datetime, timedelta, timezone
    bis = (datetime.now(timezone.utc) + timedelta(seconds=sekunden)).isoformat()
    try:
        await db.vehicles.update_one({"id": vehicle_id, "dealer_id": dealer_id},
                                     {"$max": {"geschuetzt_bis": bis}})
    except Exception:  # noqa: BLE001 — Schutz ist Zusatz, der Vorgang laeuft
        log.exception("Fahrzeug %s nicht kurz geschuetzt", vehicle_id)


async def fahrzeugpool_trimmen(db, dealer_id: str, limit=None,
                               owner_user_id=None, _tiefe: int = 0) -> int:
    """Runde 16: mit owner_user_id gilt das Limit JE KONTO — die Vergleiche
    eines Suchers verdraengen nicht mehr die eines Kollegen (vorher 30 je
    Firma). Ohne owner_user_id wie bisher firmenweit (Altaufrufer).

    Rueckgabe: Anzahl GELOESCHTER Fahrzeuge (an Mitbearbeiter uebergebene
    zaehlen nicht mit, sie existieren weiter)."""
    limit = POOL_MAX if limit is None else int(limit)
    if limit <= 0 or not dealer_id:
        return 0
    from datetime import datetime, timezone
    filter_ = {"dealer_id": dealer_id, "lifecycle": "verglichen",
               # Runde 19 (Nr. 37): frisch geschuetzte Fahrzeuge (Vertrag/Termin
               # entsteht gerade) bleiben aussen vor.
               "$or": [{"geschuetzt_bis": {"$exists": False}},
                       {"geschuetzt_bis": None},
                       {"geschuetzt_bis": {"$lt": datetime.now(timezone.utc).isoformat()}}]}
    if owner_user_id:
        filter_["owner_user_id"] = owner_user_id
    cur = db.vehicles.find(
        filter_, {"_id": 0, "id": 1, "owner_user_id": 1, "mitbearbeiter_ids": 1,
                  "updated_at": 1},
    ).sort([("updated_at", -1), ("created_at", -1)]).skip(limit)
    kandidaten = [v async for v in cur]
    if not kandidaten:
        return 0
    kandidaten_ids = [v["id"] for v in kandidaten]
    geschuetzt = set()
    # Runde 17 (Nr. 288): Schutz nur durch EIGENE Vertraege/Termine/Inserate.
    # Fahrzeug-IDs (v_<Anzeige>) sind firmenuebergreifend gleich — ohne
    # dealer_id schuetzte der Vertrag einer fremden Firma das eigene
    # Vergleichsfahrzeug (Pool lief voll und trimmte nie).
    for coll in ("generated_pdfs", "appointments", "resale_listings"):
        async for d in db[coll].find({"dealer_id": dealer_id,
                                      "vehicle_id": {"$in": kandidaten_ids}},
                                     {"_id": 0, "vehicle_id": 1}):
            geschuetzt.add(d.get("vehicle_id"))
    offen = [v for v in kandidaten if v["id"] not in geschuetzt]
    if not offen:
        return 0

    # Runde 23 (11.09.2026): Befund — das Aufraeumen loeschte das GANZE
    # gemeinsame Fahrzeug, obwohl ein Kollege (Mitbearbeiter) denselben Link
    # verglichen hatte und es noch in seinem Bereich brauchte. Jetzt:
    # Fahrzeuge mit aktivem Mitbearbeiter wechseln den Hauptbearbeiter
    # (CAS wie lifecycle.py, Muster wie der fruehere Besitzerwechsel in
    # routes/bestand.py: neuer Hauptbearbeiter ist nicht zugleich
    # Mitbearbeiter). Betrifft nur reine Vergleichsfahrzeuge ohne Vertrag
    # und Termin — kein "Wegnehmen" im Sinne von R1-01 (21.09.2026). updated_at bleibt unveraendert — die Reihenfolge im
    # Pool des Nachfolgers richtet sich weiter nach dem letzten Vergleich.
    alle_mitbearbeiter = {m for v in offen for m in (v.get("mitbearbeiter_ids") or [])
                          if m and m != v.get("owner_user_id")}
    aktiv = await _aktive_konten(db, dealer_id, alle_mitbearbeiter)
    geloescht = 0
    nachfolger_konten: set = set()
    for v in offen:
        besitzer = v.get("owner_user_id")
        mitbearbeiter = v.get("mitbearbeiter_ids") or []
        # Runde 23 (Nachpruefung): Jede Schreibbedingung enthaelt den
        # gelesenen Stand von updated_at. Vergleicht der Besitzer selbst (oder
        # ein Kollege) das Fahrzeug zwischen Lesen und Schreiben erneut, setzt
        # _fahrzeug_uebernehmen updated_at neu — es ist dann nicht mehr das
        # aelteste und darf weder geloescht noch uebergeben werden. None
        # trifft auch Altdokumente ohne das Feld.
        # Befund 112 (16.09.2026): auch der Schutzstempel gehoert in den CAS —
        # setzt eine beginnende Vertrags-/Terminanlage (kurz_schuetzen) den
        # Stempel zwischen Lesen und Loeschen, trifft weder Loeschen noch
        # Uebergabe (updated_at aendert der Stempel bewusst nicht).
        stand = {"id": v["id"], "dealer_id": dealer_id, "lifecycle": "verglichen",
                 "owner_user_id": besitzer, "updated_at": v.get("updated_at"),
                 "$or": [{"geschuetzt_bis": {"$exists": False}},
                         {"geschuetzt_bis": None},
                         {"geschuetzt_bis": {"$lt": datetime.now(timezone.utc).isoformat()}}]}
        if not mitbearbeiter:
            # Ohne Mitbearbeiter: loeschen wie bisher — nur, solange (noch)
            # keiner dazugekommen ist ($addToSet in routes/listings.py).
            r = await db.vehicles.delete_one(
                {**stand, "mitbearbeiter_ids": {"$in": [None, []]}})
            geloescht += r.deleted_count
            continue
        nachfolger = next((m for m in mitbearbeiter
                           if m in aktiv and m != besitzer), None)
        if nachfolger:
            # CAS: nur, wenn Stand (Besitzer, Lebenszyklus, Mitbearbeiter,
            # updated_at) noch dem Gelesenen entspricht — ein paralleler
            # Aufraeumlauf (zweiter Worker/Server) findet danach keinen Treffer.
            r = await db.vehicles.update_one(
                {**stand, "mitbearbeiter_ids": nachfolger},
                {"$set": {"owner_user_id": nachfolger},
                 "$pull": {"mitbearbeiter_ids": nachfolger}})
            if r.modified_count:
                log.info("Fahrzeugpool %s: %s von %s an Mitbearbeiter %s uebergeben",
                         dealer_id, v["id"], besitzer, nachfolger)
                nachfolger_konten.add(nachfolger)
            continue
        # Nur deaktivierte/fremde Mitbearbeiter: niemand Aktives braucht das
        # Fahrzeug — loeschen wie bisher, aber nur mit UNVERAENDERTER
        # Mitbearbeiterliste (kommt parallel ein Kollege dazu, bleibt es).
        r = await db.vehicles.delete_one({**stand, "mitbearbeiter_ids": mitbearbeiter})
        geloescht += r.deleted_count
    # Runde 19 (Nr. 10): der Pool des Nachfolgers darf durch die Uebergabe
    # nicht ueber die Grenze wachsen — einmal nachtrimmen (begrenzte Tiefe).
    if owner_user_id and nachfolger_konten and _tiefe < 2:
        for konto in sorted(nachfolger_konten):
            try:
                geloescht += await fahrzeugpool_trimmen(db, dealer_id, limit,
                                                        owner_user_id=konto, _tiefe=_tiefe + 1)
            except Exception:  # noqa: BLE001
                log.exception("Fahrzeugpool %s: Nachtrimmen fuer %s fehlgeschlagen", dealer_id, konto)
    return geloescht
