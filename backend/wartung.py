# -*- coding: utf-8 -*-
"""Wartungsmodus — eine Stelle, die sagt, ob die Plattform gerade pausiert.

Vorher lag die Regel dreifach verstreut: die Middleware in server.py las das
Flag selbst, das Sicherungs-Skript setzte es selbst, das Restore-Skript noch
einmal anders. Die Nachpruefung vom 20.09.2026 (Nr. 64–68) hat gezeigt, was
daran fehlte:

  Nr. 64  Das Flag las NUR die HTTP-Middleware. Link-, Beweis-, Aufraeum- und
          Abo-Worker schrieben waehrend der "Schreibpause" munter weiter —
          die Sicherung nannte sich trotzdem stichtagsgenau.
  Nr. 65  Nach dem Setzen wartete das Skript stur 6 s. Anfragen, die vorher
          durch die Middleware kamen, durften danach noch minutenlang laufen
          und schreiben.
  Nr. 66  Ohne Ablaufzeit und ohne Besitzer-Kennung konnte ein abgestuerztes
          Skript die Plattform DAUERHAFT sperren (das aufraeumende `finally`
          laeuft nach einem kill nicht mehr).
  Nr. 67  /api/ready blieb gruen — der Lastverteiler schickte also weiter
          Anfragen auf eine Instanz, die alles mit 503 beantwortete.
  Nr. 68  Und die "Schreibpause" war in Wahrheit ein kompletter Ausfall: auch
          Lesen, Downloads und Marktplatz bekamen 503.

Deshalb hat der Merker jetzt vier Angaben:

    aktiv       True/False
    umfang      "schreiben" (Lesen bleibt erlaubt) oder "alles" (Restore)
    gilt_bis    ISO-Zeit; danach gilt er als abgelaufen — auch wenn niemand
                ihn ausgeschaltet hat. Wer laenger braucht, verlaengert.
    besitzer    Zufallskennung dessen, der ihn gesetzt hat. Ausschalten darf
                nur der Besitzer (oder ausdruecklich jemand mit `zwang`).

Alle lesen ueber `pausiert()` / `aktiv_async()`, alle schreiben ueber
`setzen()` / `aufheben()`.
"""
import os
import secrets
from datetime import datetime, timedelta, timezone

FLAG_COLLECTION = "system_flags"
FLAG_ID = "wartungsmodus"

UMFANG_SCHREIBEN = "schreiben"
UMFANG_ALLES = "alles"

#: Wege, die IMMER erreichbar bleiben (sonst kaeme niemand mehr an die Lage).
FREIE_PFADE = ("/api/health", "/api/ready", "/api/")

#: Anfragen, die nichts veraendern — bei umfang="schreiben" duerfen sie durch.
LESENDE_METHODEN = ("GET", "HEAD", "OPTIONS")

#: Laenger als das haelt kein Merker ohne Verlaengerung (Nr. 66).
STANDARD_FRIST_MIN = 30


def _jetzt() -> datetime:
    return datetime.now(timezone.utc)


def _zeit(wert) -> datetime | None:
    if isinstance(wert, datetime):
        return wert if wert.tzinfo else wert.replace(tzinfo=timezone.utc)
    if isinstance(wert, str) and wert.strip():
        try:
            d = datetime.fromisoformat(wert.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    return None


def neue_kennung() -> str:
    return secrets.token_hex(8)


def frist_minuten(vorgabe: int | None = None) -> int:
    """Wie lange ein frisch gesetzter Merker hoechstens gilt."""
    if vorgabe:
        return max(1, int(vorgabe))
    try:
        return max(1, int(os.environ.get("WARTUNG_FRIST_MIN", "").strip()
                          or STANDARD_FRIST_MIN))
    except ValueError:
        return STANDARD_FRIST_MIN


def abgelaufen(doc: dict | None, jetzt: datetime | None = None) -> bool:
    """Steht der Merker noch, obwohl seine Zeit abgelaufen ist?

    Genau der Fall aus Nr. 66: das Skript wurde hart beendet, sein
    Aufraeumen lief nie. Dann ignoriert ihn jeder Leser, und der naechste
    Start raeumt ihn weg."""
    if not doc or not doc.get("aktiv"):
        return False
    bis = _zeit(doc.get("gilt_bis"))
    return bis is not None and (jetzt or _jetzt()) > bis


def pausiert(doc: dict | None, methode: str = "POST",
             jetzt: datetime | None = None) -> bool:
    """Muss diese Anfrage jetzt abgewiesen werden?

    `methode` entscheidet nur bei umfang="schreiben": Lesen darf dann weiter
    (Nr. 68). Ein Merker ohne Zeitangabe gilt unbegrenzt — das koennen nur
    noch alte Eintraege sein, deshalb bleibt er wirksam, aber der Waechter
    beim Start meldet ihn."""
    if not doc or not doc.get("aktiv"):
        return False
    if abgelaufen(doc, jetzt):
        return False
    if (doc.get("umfang") or UMFANG_ALLES) == UMFANG_SCHREIBEN:
        return (methode or "").upper() not in LESENDE_METHODEN
    return True


def beschreibung(doc: dict | None) -> str:
    """Kurzer Satz fuer /api/ready und die 503-Antwort."""
    grund = (doc or {}).get("grund") or "Wartung"
    bis = _zeit((doc or {}).get("gilt_bis"))
    if bis is None:
        return f"{grund} (ohne Ablaufzeit)"
    rest = max(0, int((bis - _jetzt()).total_seconds() // 60))
    return f"{grund} (laengstens noch {rest} min)"


# --------------------------------------------------------------- schreiben
def _satz(kennung: str, grund: str, umfang: str, frist_min: int) -> dict:
    jetzt = _jetzt()
    return {"aktiv": True, "grund": grund, "umfang": umfang,
            "besitzer": kennung, "gesetzt_am": jetzt.isoformat(),
            "gilt_bis": (jetzt + timedelta(minutes=frist_min)).isoformat()}


def setzen(coll, grund: str, umfang: str = UMFANG_SCHREIBEN,
           frist_min: int | None = None, kennung: str | None = None) -> str:
    """Wartungsmodus einschalten (synchron, fuer die Skripte).

    Liefert die Besitzer-Kennung, mit der er wieder aufgehoben wird."""
    kennung = kennung or neue_kennung()
    coll.replace_one({"_id": FLAG_ID},
                     {"_id": FLAG_ID, **_satz(kennung, grund, umfang,
                                              frist_minuten(frist_min))},
                     upsert=True)
    return kennung


def verlaengern(coll, kennung: str, frist_min: int | None = None) -> bool:
    """Frist neu setzen, solange der Lauf noch arbeitet (Nr. 66)."""
    bis = (_jetzt() + timedelta(minutes=frist_minuten(frist_min))).isoformat()
    r = coll.update_one({"_id": FLAG_ID, "besitzer": kennung, "aktiv": True},
                        {"$set": {"gilt_bis": bis}})
    return r.matched_count == 1


def aufheben(coll, kennung: str | None = None, zwang: bool = False) -> bool:
    """Wartungsmodus beenden. Ohne `zwang` nur den eigenen (Nr. 66)."""
    filter_ = {"_id": FLAG_ID}
    if kennung and not zwang:
        filter_["besitzer"] = kennung
    r = coll.update_one(filter_, {"$set": {"aktiv": False,
                                           "beendet": _jetzt().isoformat()}})
    return r.matched_count == 1


# ------------------------------------------------------------ asynchron
async def lesen_async(db):
    return await db[FLAG_COLLECTION].find_one({"_id": FLAG_ID})


async def aktiv_async(db, methode: str = "POST") -> bool:
    """Fuer die Worker: darf ich gerade schreiben? (Nr. 64)

    Stoerungen gelten bewusst als "keine Pause": eine kaputte Abfrage darf
    die Plattform nicht anhalten."""
    try:
        return pausiert(await lesen_async(db), methode)
    except Exception:  # noqa: BLE001
        return False


async def abgelaufenen_merker_aufraeumen(db) -> bool:
    """Waechter beim Start (Nr. 66): steht ein abgelaufener Merker, weg damit.

    Ein Merker OHNE Ablaufzeit wird nicht angefasst — der kann von einem
    laufenden Restore stammen; er wird in /api/ready gemeldet."""
    doc = await lesen_async(db)
    if not abgelaufen(doc):
        return False
    await db[FLAG_COLLECTION].update_one(
        {"_id": FLAG_ID, "aktiv": True},
        {"$set": {"aktiv": False, "beendet": _jetzt().isoformat(),
                  "abgelaufen": True}})
    return True
