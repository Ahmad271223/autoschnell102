# -*- coding: utf-8 -*-
"""Unique-Indizes mit Dublettenschutz (Runde 5/15/17) — eigenes Modul, damit
Tests und der Admin-Reparaturlauf sie ohne Import von server.py nutzen
koennen (server.py bindet beim Import den Motor-Client an den aktuellen
Event-Loop)."""
import os

from deps import db, log


async def _unique_index_sicher(coll, feld, abbruch_in_produktion: bool = True) -> bool:
    """Unique-Index nur anlegen, wenn keine Dubletten existieren (Runde 5).
    Vorher scheiterte die Anlage still, und die Eindeutigkeit (z.B. eine
    E-Mail = ein Konto) galt dann einfach nicht. In Produktion bricht der
    Start ab, sonst wird gewarnt — bereinigen mit scripts/dubletten_pruefen.py.
    Runde 17: `feld` darf eine Feldliste sein (zusammengesetzter Schluessel,
    z.B. vehicles (dealer_id, id)); mit abbruch_in_produktion=False wird
    statt des Abbruchs ein Betriebsalarm gesetzt (neue Regel auf Altdaten).
    Liefert True, wenn der Index steht."""
    felder = [feld] if isinstance(feld, str) else list(feld)
    name = ".".join(felder)
    dubletten = await coll.aggregate([
        {"$match": {f: {"$exists": True, "$ne": None} for f in felder}},
        {"$group": {"_id": {f: f"${f}" for f in felder}, "n": {"$sum": 1}}},
        {"$match": {"n": {"$gt": 1}}}, {"$limit": 5}]).to_list(5)
    if dubletten:
        beispiele = ", ".join(
            str(d["_id"][felder[0]] if len(felder) == 1 else d["_id"]) for d in dubletten)
        msg = (f"{coll.name}.{name}: doppelte Werte vorhanden ({beispiele}) — "
               "Unique-Index NICHT angelegt. Bereinigen: "
               "python scripts/dubletten_pruefen.py")
        if abbruch_in_produktion and os.environ.get("APP_ENV", "").strip().lower() == "production":
            log.error("Start ABGEBROCHEN: %s", msg)
            raise SystemExit(78)
        log.error("ensure_indexes: %s", msg)
        from betrieb import alarm
        await alarm(db, "unique_index_fehlt", ref=f"{coll.name}.{name}", beispiele=beispiele)
        return False
    if len(felder) == 1:
        await coll.create_index(felder[0], unique=True)
    else:
        await coll.create_index([(f, 1) for f in felder], unique=True)
    from betrieb import alarm_schliessen
    await alarm_schliessen(db, "unique_index_fehlt", ref=f"{coll.name}.{name}")
    return True


async def _termin_unique_index() -> bool:
    """Runde 15 (Nr. 6): hoechstens EIN offener Abholtermin je Fahrzeug und
    Firma. Zwei parallele Vertragsanlagen (oder Doppelklicks) erzeugten
    zwei Termine fuer dasselbe Auto; die Vorabpruefung der Routen ist nicht
    atomar, der Teil-Unique-Index ist der Backstop. Abgeschlossene Termine
    (abgeholt, storniert, ...) sind ausgenommen — ein Fahrzeug darf spaeter
    erneut einen Termin bekommen. Bestehende Dubletten blockieren nur den
    Index (Warnung), nicht den Start: die Regel ist neu, Altdaten werden
    ueber den Terminplaner bereinigt."""
    from betrieb import alarm, alarm_schliessen
    from deps import TERMIN_OFFEN
    # Runde 17: "" ist ein String — ein Termin mit vehicle_id "" darf nicht
    # mit anderen leeren kollidieren ($gt "" = nicht leer).
    # Umbau Kaufvorgaenge 09.09.2026: EIN offener Termin je VERTRAG (nicht
    # mehr je Fahrzeug — mehrere Sucher duerfen dasselbe Inserat kaufen).
    filter_ = {"contract_id": {"$type": "string", "$gt": ""},
               "status": {"$in": list(TERMIN_OFFEN)}}
    name = "termin_offen_je_vertrag"
    try:
        alt_index = await db.appointments.index_information()
        if "termin_offen_je_fahrzeug" in alt_index:
            await db.appointments.drop_index("termin_offen_je_fahrzeug")
    except Exception as exc:
        log.warning("alter Termin-Index nicht entfernt: %s", exc)
    dubletten = await db.appointments.aggregate([
        {"$match": filter_},
        {"$group": {"_id": {"d": "$dealer_id", "c": "$contract_id"}, "n": {"$sum": 1}}},
        {"$match": {"n": {"$gt": 1}}}, {"$limit": 5}]).to_list(5)
    if dubletten:
        beispiele = ", ".join(str(d["_id"].get("c")) for d in dubletten)
        log.error("ensure_indexes: appointments: mehrere OFFENE Termine je "
                  "Vertrag vorhanden (%s) — Unique-Index NICHT angelegt. "
                  "Bitte doppelte offene Termine im Terminplaner schliessen "
                  "oder loeschen, dann Backend neu starten.", beispiele)
        # Runde 17: sichtbar im Admin-Bereich (/admin/betrieb), nicht nur im Log
        await alarm(db, "termin_index_fehlt", ref="appointments", beispiele=beispiele)
        return False
    try:
        vorhanden = await db.appointments.index_information()
        alt = vorhanden.get(name)
        if alt is not None and alt.get("partialFilterExpression") != filter_:
            # Filter hat sich geaendert (Runde 17: $gt "") -> neu anlegen
            await db.appointments.drop_index(name)
        await db.appointments.create_index(
            [("dealer_id", 1), ("contract_id", 1)], unique=True,
            name=name, partialFilterExpression=filter_)
        await alarm_schliessen(db, "termin_index_fehlt", ref="appointments")
        return True
    except Exception as exc:
        log.error("ensure_indexes: termin_offen_je_vertrag: %s", exc)
        await alarm(db, "termin_index_fehlt", ref="appointments", fehler=str(exc)[:300])
        return False


async def _unique_index_mit_bereinigung(coll, name: str, felder: list, filter_: dict,
                                        sortierung: list, bereinigen) -> bool:
    """Audit 13.09.2026 (#18/#19/#27): Teil-Unique-Index fuer eine NEUE Regel
    auf Altdaten. Anders als _unique_index_sicher werden bestehende Dubletten
    ZUERST automatisch bereinigt (idempotent, protokolliert): je Schluessel
    bleibt das erste Dokument nach `sortierung`, fuer die uebrigen fuehrt
    `bereinigen(_ids)` die fachlich passende Reparatur aus (loeschen bzw.
    schliessen). Erst dann wird der Index angelegt.

    Der Start bricht NIE ab: scheitert es, gibt es den Betriebsalarm
    unique_index_fehlt (/admin/betrieb); die Route behaelt ihre Vorabpruefung.
    Zwei Versuche, weil beim Rollout die alte Fassung zwischen Bereinigung und
    Indexanlage noch eine Dublette schreiben kann. Liefert True, wenn der
    Index steht."""
    from betrieb import alarm, alarm_schliessen
    ref = f"{coll.name}.{name}"
    fehler = None
    for versuch in (1, 2):
        try:
            bereinigt = 0
            gruppen = coll.aggregate([
                {"$match": filter_},
                {"$sort": {**dict(sortierung), "_id": 1}},
                {"$group": {"_id": {f: f"${f}" for f in felder},
                            "ids": {"$push": "$_id"}, "n": {"$sum": 1}}},
                {"$match": {"n": {"$gt": 1}}}], allowDiskUse=True)
            async for g in gruppen:
                rest = g["ids"][1:]
                await bereinigen(rest)
                bereinigt += len(rest)
                log.warning("ensure_indexes: %s: Dublette %s automatisch bereinigt "
                            "(%d ueberzaehlige Dokumente)", ref, g["_id"], len(rest))
            if bereinigt:
                log.warning("ensure_indexes: %s: %d Altdubletten bereinigt", ref, bereinigt)
            vorhanden = await coll.index_information()
            alt = vorhanden.get(name)
            if alt is not None and (alt.get("partialFilterExpression") != filter_
                                    or not alt.get("unique")):
                await coll.drop_index(name)
            await coll.create_index([(f, 1) for f in felder], unique=True, name=name,
                                    partialFilterExpression=filter_)
            await alarm_schliessen(db, "unique_index_fehlt", ref=ref)
            return True
        except Exception as exc:
            fehler = exc
            log.warning("ensure_indexes: %s: Versuch %d gescheitert: %s", ref, versuch, exc)
    log.error("ensure_indexes: %s: Unique-Index NICHT angelegt (%s) — die Route "
              "arbeitet mit ihrer Vorabpruefung weiter", ref, fehler)
    await alarm(db, "unique_index_fehlt", ref=ref, fehler=str(fehler)[:300])
    return False


async def _favoriten_unique_index() -> bool:
    """Audit 13.09.2026 (#18): EIN Merklisten-Eintrag je Kaeufer und Inserat.
    Ein Doppelklick auf das Herz legte zwei Eintraege an (Pruefen und
    Einfuegen ueber mehrere Round-Trips); danach loeschte der naechste Klick
    nur einen. Altdubletten sind wertlos und werden geloescht (der aelteste
    Eintrag bleibt)."""
    async def loeschen(ids):
        await db.buyer_favorites.delete_many({"_id": {"$in": ids}})
    return await _unique_index_mit_bereinigung(
        db.buyer_favorites, "favorit_je_kaeufer_inserat", ["buyer_user_id", "listing_id"],
        {"buyer_user_id": {"$type": "string"}, "listing_id": {"$type": "string"}},
        [("created_at", 1)], loeschen)


async def _interesse_unique_index() -> bool:
    """Audit 13.09.2026 (#19): hoechstens EINE laufende Verhandlung je Kaeufer
    und Inserat. Abgeschlossene Anfragen (akzeptiert/abgelehnt) sind
    ausgenommen, danach darf der Kaeufer neu anfragen. Altdubletten: die
    zuletzt bewegte Verhandlung bleibt, die uebrigen werden wie beim Verkauf
    (resale._anfragen_schliessen) mit Grund abgelehnt — nicht geloescht."""
    from deps import now_iso
    from routes.marketplace import INTERESSE_OFFEN
    offen = list(INTERESSE_OFFEN)

    async def schliessen(ids):
        jetzt = now_iso()
        await db.listing_interest.update_many(
            {"_id": {"$in": ids}, "status": {"$in": offen}},
            {"$set": {"status": "abgelehnt", "beendet_grund": "doppelte_anfrage",
                      "updated_at": jetzt},
             "$push": {"history": {"von": "system", "aktion": "doppelte_anfrage",
                                   "zeit": jetzt}}})
    return await _unique_index_mit_bereinigung(
        db.listing_interest, "interesse_offen_je_kaeufer", ["listing_id", "buyer_user_id"],
        {"listing_id": {"$type": "string"}, "buyer_user_id": {"$type": "string"},
         "status": {"$in": offen}},
        [("updated_at", -1)], schliessen)


async def _buyer_access_unique_index() -> bool:
    """Audit 13.09.2026 (#27): hoechstens EINE offene Marktplatz-Zugangsanfrage
    je Kaeufer (wie sucher_abo/verkaufspaket in server._plan_requests_unique_
    indizes). Jeder Klick legte vorher eine neue offene Anfrage an und konnte
    die Freischaltungsliste des Betreibers fluten. Altdubletten: die aelteste
    bleibt offen, die uebrigen werden als erledigt (Grund: doppelte_anfrage)
    markiert — dieselbe Anfrage, nichts geht verloren."""
    from deps import now_iso

    async def zusammenfuehren(ids):
        await db.plan_requests.update_many(
            {"_id": {"$in": ids}, "status": "offen"},
            {"$set": {"status": "erledigt", "erledigt_grund": "doppelte_anfrage",
                      "updated_at": now_iso()}})
    return await _unique_index_mit_bereinigung(
        db.plan_requests, "uniq_offene_buyer_access_anfrage", ["type", "buyer_user_id"],
        {"type": "buyer_access", "status": "offen", "buyer_user_id": {"$type": "string"}},
        [("created_at", 1)], zusammenfuehren)
