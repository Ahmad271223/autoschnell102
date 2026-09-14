# -*- coding: utf-8 -*-
"""Kontonummer (13.09.2026): EINE Stelle fuer Nummernvergabe und Kontenanlage.

Alle Funktionen bekommen `db` vom Aufrufer (Modul-Global des Routenmoduls zur
Aufrufzeit) — Tests, die routes.admin.db, routes.marketplace.db oder deps.db
ersetzen, greifen damit weiter. Kein Import von server.py oder routes.*.

Regeln fuer Aufrufer:
1. Eingaben und Ablaufdatum VOR dem Ziehen einer Nummer pruefen (die
   E-Mail ist seit Schritt 5 nur Kontaktadresse und nicht mehr eindeutig).
2. DuplicateKeyError, der NICHT die Kontonummer betrifft, wird durchgereicht
   und bleibt beim Aufrufer eine 409.
3. Nummern vergibt nur die Anlage — es gibt KEIN automatisches Nachziehen
   fuer Bestandskonten (konten_ohne_nummer zaehlt nur).
"""
import secrets

from fastapi import HTTPException
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from kontonummer import ist_kontonummer_dublette, nummer_bedingung, sucher_nummer

# Rollen in users, die sich mit Kontonummer anmelden.
NUMMERN_ROLLEN = ("dealer", "sucher", "b2b_buyer")

_VERSUCHE = 3


# ------------------------------------------------------------ Nummernreihe
async def _hoechste_vergebene(db) -> int:
    """Hoechste bereits vergebene Nummer ueber alle drei Sammlungen
    (Firmen, Konten in users, Fahrerkonten). Jede Abfrage ist ein
    Indexzugriff (kunden_nr_unique bzw. kontonummer_basis)."""
    hoechste = 0
    for coll, feld in ((db.dealers, "kunden_nr"),
                       (db.users, "kontonummer_basis"),
                       (db.driver_accounts, "kontonummer_basis")):
        top = await coll.find_one({feld: {"$type": "number"}}, {"_id": 0, feld: 1},
                                  sort=[(feld, -1)])
        if top and int(top[feld]) > hoechste:
            hoechste = int(top[feld])
    return hoechste


async def naechste_nummer(db) -> int:
    """Naechste Nummer der EINEN Reihe counters._id='kunden_nr' (Firmen,
    Kaeufer, Fahrer). Selbstheilung wie deps.naechste_kunden_nr seit Runde 22,
    jetzt ueber drei Sammlungen: nie unter oder auf die hoechste vergebene
    Nummer — auch nicht nach einem Restore ohne counters."""
    for _ in range(5):
        try:
            doc = await db.counters.find_one_and_update(
                {"_id": "kunden_nr"},
                {"$inc": {"seq": 1}, "$setOnInsert": {"start": 1000}},
                upsert=True, return_document=ReturnDocument.AFTER)
        except DuplicateKeyError:
            # Zwei gleichzeitige Upserts des ersten Zaehlers: erneut lesen.
            continue
        nr = 1000 + int(doc["seq"])
        hoechste = await _hoechste_vergebene(db)
        if nr > hoechste:
            return nr
        await db.counters.update_one(
            {"_id": "kunden_nr"}, {"$max": {"seq": hoechste - 1000}})
    raise RuntimeError("Kontonummer: kein freier Wert gefunden")


async def kunden_nr_sicherstellen(db, dealer_id: str) -> dict:
    """Firma lesen und — falls ihr die kunden_nr fehlt (Altbestand) — eine
    nachziehen ($exists-Schutz: parallele Aufrufe erzeugen hoechstens Luecken).
    404 nur, wenn die Firma fehlt; 409, wenn ihre Loeschung laeuft."""
    proj = {"_id": 0, "id": 1, "company_name": 1, "kunden_nr": 1, "loeschung": 1}
    firma = await db.dealers.find_one({"id": dealer_id}, proj)
    if not firma:
        raise HTTPException(404, "Firma nicht gefunden")
    if (firma.get("loeschung") or {}).get("status") == "laeuft":
        raise HTTPException(409, "Die Firma wird gerade gelöscht")
    for _ in range(_VERSUCHE):
        if isinstance(firma.get("kunden_nr"), int):
            return firma
        try:
            await db.dealers.update_one(
                {"id": dealer_id, "kunden_nr": {"$exists": False}},
                {"$set": {"kunden_nr": await naechste_nummer(db)}})
        except DuplicateKeyError as exc:
            if "kunden_nr" not in str(exc):
                raise
        firma = await db.dealers.find_one({"id": dealer_id}, proj) or {}
        if not firma:
            raise HTTPException(404, "Firma nicht gefunden")
    if isinstance(firma.get("kunden_nr"), int):
        return firma
    raise HTTPException(409, "Kundennummer der Firma konnte nicht vergeben werden")


async def _hoechster_zusatz(db, kunden_nr: int) -> int:
    hoechster = 0
    praefix = f"{int(kunden_nr)}-"
    # Typfilter mitfuehren, sonst liest der Planer die ganze Sammlung.
    async for u in db.users.find({"kontonummer": nummer_bedingung(
                                     {"$regex": f"^{praefix}"})},
                                 {"_id": 0, "kontonummer": 1}):
        try:
            z = int(str(u["kontonummer"])[len(praefix):])
        except (KeyError, ValueError):
            continue
        hoechster = max(hoechster, z)
    return hoechster


async def naechster_sucher_zusatz(db, dealer_id: str, kunden_nr: int) -> int:
    """Zusatz fuer den naechsten Sucher: dealers.sucher_seq nur per $inc, nie
    zurueckgesetzt — der Zusatz eines geloeschten Suchers wird nie neu
    vergeben. Liegt der Zaehler nicht ueber dem hoechsten vorhandenen Zusatz
    (Restore, Altbestand), wird er erst per $max angehoben."""
    for _ in range(5):
        doc = await db.dealers.find_one_and_update(
            {"id": dealer_id}, {"$inc": {"sucher_seq": 1}},
            projection={"_id": 0, "sucher_seq": 1},
            return_document=ReturnDocument.AFTER)
        if not doc:
            raise HTTPException(404, "Firma nicht gefunden")
        zusatz = int(doc["sucher_seq"])
        hoechster = await _hoechster_zusatz(db, kunden_nr)
        if zusatz > hoechster:
            return zusatz
        # Nachpruefung 14.09.2026 (CI): Liegt ein HOEHERER Zusatz schon vor,
        # gibt es zwei Gruende. (a) Der Zaehler ist hinterher (Restore,
        # Altbestand): heilen und neu ziehen — ein geloeschter Zusatz wird so
        # nie wieder vergeben. (b) Ein PARALLELER Aufruf hat seine hoehere
        # Nummer nur schneller eingefuegt: der Zaehler hat beide vergeben, die
        # eigene Nummer ist frei — sie zu verwerfen riss eine Luecke in die
        # Reihe (1001-1 … 1001-5, 1001-7).
        stand = await db.dealers.find_one({"id": dealer_id}, {"_id": 0, "sucher_seq": 1})
        zaehler_gesund = int((stand or {}).get("sucher_seq") or 0) >= hoechster
        if zaehler_gesund and not await db.users.find_one(
                {"kontonummer": sucher_nummer(kunden_nr, zusatz)}, {"_id": 1}):
            return zusatz
        await db.dealers.update_one({"id": dealer_id},
                                    {"$max": {"sucher_seq": hoechster}})
    raise RuntimeError("Sucher-Zusatz: kein freier Wert gefunden")


# ------------------------------------------------------------ Anlage
async def firma_einfuegen(db, doc: dict, nummer_ziehen=None) -> int:
    """Firmenprofil mit frischer Kundennummer einfuegen; bei DuplicateKey auf
    kunden_nr (Rennen mit korrigiertem Zaehler) neue Nummer, max. 3 Versuche.
    `nummer_ziehen` (async, ohne Argumente) ersetzt die Reihe — nur fuer die
    alte Weiterleitung routes.admin._dealer_anlegen_mit_kunden_nr."""
    for versuch in range(_VERSUCHE):
        doc.pop("_id", None)            # insert_one schreibt _id ins dict
        doc["kunden_nr"] = await (nummer_ziehen() if nummer_ziehen else naechste_nummer(db))
        try:
            await db.dealers.insert_one(doc)
            return doc["kunden_nr"]
        except DuplicateKeyError as e:
            if "kunden_nr" not in str(e) or versuch == _VERSUCHE - 1:
                raise
    raise RuntimeError("Firma: keine freie Kundennummer")  # pragma: no cover


async def firma_mit_chef_anlegen(db, firma: dict, chef: dict) -> dict:
    """Erst die Firma mit Nummer, dann der Chef mit kontonummer=str(kunden_nr).

    - DuplicateKey auf kunden_nr: neue Nummer (firma_einfuegen).
    - Kontonummer-Dublette beim Chef: Firma loeschen, mit neuer Nummer neu.
    - Jeder andere Fehler: Firma loeschen und werfen (kein Profil ohne Chef).
    Rueckgabe {user_id, dealer_id, kunden_nr, kontonummer}."""
    for versuch in range(_VERSUCHE):
        firma_doc = dict(firma)
        nr = await firma_einfuegen(db, firma_doc)
        chef_doc = dict(chef)
        chef_doc.pop("_id", None)
        chef_doc.update({"kontonummer": str(nr), "kontonummer_basis": nr,
                         "dealer_id": firma_doc["id"]})
        chef_doc.setdefault("role", "dealer")
        chef_doc.setdefault("current_session_id", None)
        try:
            await db.users.insert_one(chef_doc)
        except DuplicateKeyError as e:
            await db.dealers.delete_one({"id": firma_doc["id"]})
            if ist_kontonummer_dublette(e) and versuch < _VERSUCHE - 1:
                continue
            raise
        except Exception:
            await db.dealers.delete_one({"id": firma_doc["id"]})
            raise
        return {"user_id": chef_doc["id"], "dealer_id": firma_doc["id"],
                "kunden_nr": nr, "kontonummer": str(nr)}
    raise RuntimeError("Firma: keine freie Kontonummer")  # pragma: no cover


async def sucher_anlegen(db, dealer_id: str, konto: dict) -> dict:
    """Sucher mit Nummer '<kunden_nr>-<zusatz>'. Fehlt der Firma die
    kunden_nr, wird sie nachgezogen. Neuer Zusatz NUR bei Kontonummer-
    Dublette (max. 3 Versuche); jede andere Dublette wird geworfen.
    Rueckgabe {user_id, kontonummer}."""
    firma = await kunden_nr_sicherstellen(db, dealer_id)
    kunden_nr = int(firma["kunden_nr"])
    for versuch in range(_VERSUCHE):
        zusatz = await naechster_sucher_zusatz(db, dealer_id, kunden_nr)
        doc = dict(konto)
        doc.pop("_id", None)
        doc.update({"kontonummer": sucher_nummer(kunden_nr, zusatz),
                    "kontonummer_basis": kunden_nr, "dealer_id": dealer_id,
                    "role": "sucher"})
        doc.setdefault("current_session_id", None)
        try:
            await db.users.insert_one(doc)
        except DuplicateKeyError as e:
            if ist_kontonummer_dublette(e) and versuch < _VERSUCHE - 1:
                continue
            raise
        return {"user_id": doc["id"], "kontonummer": doc["kontonummer"]}
    raise RuntimeError("Sucher: keine freie Kontonummer")  # pragma: no cover


async def kaeufer_anlegen(db, konto: dict) -> dict:
    """Zwischenhaendler (b2b_buyer) mit eigener Nummer aus der Reihe.
    Rueckgabe {user_id, kontonummer}."""
    for versuch in range(_VERSUCHE):
        nr = await naechste_nummer(db)
        doc = dict(konto)
        doc.pop("_id", None)
        doc.update({"kontonummer": str(nr), "kontonummer_basis": nr,
                    "role": "b2b_buyer", "dealer_id": None})
        doc.setdefault("current_session_id", None)
        try:
            await db.users.insert_one(doc)
        except DuplicateKeyError as e:
            if ist_kontonummer_dublette(e) and versuch < _VERSUCHE - 1:
                continue
            raise
        return {"user_id": doc["id"], "kontonummer": doc["kontonummer"]}
    raise RuntimeError("Kaeufer: keine freie Kontonummer")  # pragma: no cover


# ------------------------------------------------------------ Fahrer
DRIVER_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # ohne I,O,0,1


def generate_driver_code() -> str:
    """Public Fahrer-ID im Format `FD-XXXXXXXX` (8 Zeichen, gut lesbar)."""
    suffix = "".join(secrets.choice(DRIVER_CODE_ALPHABET) for _ in range(8))
    return f"FD-{suffix}"


async def ensure_unique_driver_code(db=None) -> str:
    if db is None:
        from deps import db
    for _ in range(30):
        code = generate_driver_code()
        existing = await db.driver_accounts.find_one(
            {"driver_code": code}, {"_id": 0, "id": 1},
        )
        if not existing:
            return code
    raise HTTPException(500, "Konnte keinen eindeutigen Fahrer-Code erzeugen")


def _ist_code_dublette(exc) -> bool:
    details = getattr(exc, "details", None) or {}
    muster = details.get("keyPattern") if isinstance(details, dict) else None
    if muster:
        return "driver_code" in muster
    return "driver_code" in str(exc)


async def fahrer_anlegen(db, konto: dict) -> dict:
    """Fahrerkonto mit Nummer aus derselben Reihe und FD-Code.
    Rueckgabe {driver_id, kontonummer, driver_code}."""
    for versuch in range(_VERSUCHE):
        nr = await naechste_nummer(db)
        doc = dict(konto)
        doc.pop("_id", None)
        doc.update({"kontonummer": str(nr), "kontonummer_basis": nr,
                    "driver_code": await ensure_unique_driver_code(db)})
        doc.setdefault("current_session_id", None)
        try:
            await db.driver_accounts.insert_one(doc)
        except DuplicateKeyError as e:
            if ((ist_kontonummer_dublette(e) or _ist_code_dublette(e))
                    and versuch < _VERSUCHE - 1):
                continue
            raise
        return {"driver_id": doc["id"], "kontonummer": doc["kontonummer"],
                "driver_code": doc["driver_code"]}
    raise RuntimeError("Fahrer: keine freie Kontonummer")  # pragma: no cover


# ------------------------------------------------------------ Betrieb
async def konten_ohne_nummer(db) -> dict:
    """Nur ZAEHLEN (keine Vergabe): Konten, die sich mit Kontonummer anmelden
    muessten, aber keine haben — ausser laufenden Loeschungen."""
    ohne = {"kontonummer": {"$not": {"$type": "string"}},
            "loeschung.status": {"$ne": "laeuft"}}
    users = await db.users.count_documents({**ohne, "role": {"$in": list(NUMMERN_ROLLEN)}})
    fahrer = await db.driver_accounts.count_documents(ohne)
    return {"users": users, "driver_accounts": fahrer}
