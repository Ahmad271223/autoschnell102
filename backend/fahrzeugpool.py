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

POOL_MAX = int(os.environ.get("FAHRZEUGPOOL_MAX_VERGLEICHE", "30") or 30)


async def _aktive_konten(db, dealer_id: str, ids) -> set:
    """Runde 23: welche der Konten sind aktive Konten DIESER Firma (Chef
    oder Sucher)? Gleiche Regel wie der Besitzerwechsel durch den Chef
    (routes/bestand.py set_vehicle_owner): active=False zaehlt nicht."""
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


async def fahrzeugpool_trimmen(db, dealer_id: str, limit=None,
                               owner_user_id=None) -> int:
    """Runde 16: mit owner_user_id gilt das Limit JE KONTO — die Vergleiche
    eines Suchers verdraengen nicht mehr die eines Kollegen (vorher 30 je
    Firma). Ohne owner_user_id wie bisher firmenweit (Altaufrufer).

    Rueckgabe: Anzahl GELOESCHTER Fahrzeuge (an Mitbearbeiter uebergebene
    zaehlen nicht mit, sie existieren weiter)."""
    limit = POOL_MAX if limit is None else int(limit)
    if limit <= 0 or not dealer_id:
        return 0
    filter_ = {"dealer_id": dealer_id, "lifecycle": "verglichen"}
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
    # (CAS wie lifecycle.py, Muster wie der Besitzerwechsel in
    # routes/bestand.py: neuer Hauptbearbeiter ist nicht zugleich
    # Mitbearbeiter). updated_at bleibt unveraendert — die Reihenfolge im
    # Pool des Nachfolgers richtet sich weiter nach dem letzten Vergleich.
    alle_mitbearbeiter = {m for v in offen for m in (v.get("mitbearbeiter_ids") or [])
                          if m and m != v.get("owner_user_id")}
    aktiv = await _aktive_konten(db, dealer_id, alle_mitbearbeiter)
    geloescht = 0
    for v in offen:
        besitzer = v.get("owner_user_id")
        mitbearbeiter = v.get("mitbearbeiter_ids") or []
        # Runde 23 (Nachpruefung): Jede Schreibbedingung enthaelt den
        # gelesenen Stand von updated_at. Vergleicht der Besitzer selbst (oder
        # ein Kollege) das Fahrzeug zwischen Lesen und Schreiben erneut, setzt
        # _fahrzeug_uebernehmen updated_at neu — es ist dann nicht mehr das
        # aelteste und darf weder geloescht noch uebergeben werden. None
        # trifft auch Altdokumente ohne das Feld.
        stand = {"id": v["id"], "dealer_id": dealer_id, "lifecycle": "verglichen",
                 "owner_user_id": besitzer, "updated_at": v.get("updated_at")}
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
            continue
        # Nur deaktivierte/fremde Mitbearbeiter: niemand Aktives braucht das
        # Fahrzeug — loeschen wie bisher, aber nur mit UNVERAENDERTER
        # Mitbearbeiterliste (kommt parallel ein Kollege dazu, bleibt es).
        r = await db.vehicles.delete_one({**stand, "mitbearbeiter_ids": mitbearbeiter})
        geloescht += r.deleted_count
    return geloescht
