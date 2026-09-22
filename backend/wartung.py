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
# Nachpruefung 20.09.2026 (N6): Die Schreibpause wartete nach dem Einschalten
# stur eine feste Zeit (Standard 30 s) und begann dann den Dump — ohne zu
# WISSEN, ob wirklich niemand mehr schreibt. Eine Anfrage, die vorher noch
# durchkam und laenger braucht, konnte also mitten im Dump schreiben, und die
# Sicherung nannte sich trotzdem stichtagsgenau.
#
# Der Zaehler in der Middleware (`_offene_schreiber`) weiss das zwar, aber nur
# JE PROZESS — bei vier Workern auf zwei Servern wissen die anderen sieben
# nichts davon. Deshalb meldet jeder Prozess seinen Stand hierher, solange
# eine Schreibpause laeuft; die Sicherung wartet, bis ALLE null melden.
#
# Im Normalbetrieb wird hier NICHTS geschrieben, und die Sammlung ist vom
# Dump ausgenommen — sonst wuerde ausgerechnet der Melder die Datenbank
# waehrend der Sicherung veraendern.
SCHREIBER_COLLECTION = "wartung_schreiber"
#: So alt darf eine Meldung hoechstens sein, um noch zu zaehlen.
SCHREIBER_FRISCH_S = 10

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
           frist_min: int | None = None, kennung: str | None = None,
           zwang: bool = False) -> str | None:
    """Wartungsmodus einschalten (synchron, fuer die Skripte).

    Liefert die Besitzer-Kennung, mit der er wieder aufgehoben wird.

    Rollenprüfung 22.09.2026 (RP-246/RP-397): vorher ueberschrieb setzen()
    per replace_one JEDEN bestehenden Merker. Lief gerade ein Restore
    (umfang "alles", besitzer "restore") und startete die Sicherung ihre
    Schreibpause, war der Restore-Merker weg — aus "alles" wurde "schreiben"
    (Lesen wieder frei auf einer halb umgeschalteten Datenbank), und das
    aufheben() der Sicherung oeffnete die Plattform mitten im Restore.
    Jetzt wird nur geschrieben, wenn kein FREMDER, noch gueltiger Merker
    steht (Compare-and-Set). Sonst: None — der Aufrufer bricht ab. Ein
    Merker ohne Ablaufzeit (Restore) gilt als gueltig. `zwang=True`
    ueberschreibt wie frueher (nur fuer ausdruecklich staerkere Merker)."""
    kennung = kennung or neue_kennung()
    satz = {"_id": FLAG_ID, **_satz(kennung, grund, umfang, frist_minuten(frist_min))}
    if zwang:
        coll.replace_one({"_id": FLAG_ID}, satz, upsert=True)
        return kennung
    frei = {"_id": FLAG_ID,
            "$or": [{"aktiv": {"$ne": True}},
                    {"gilt_bis": {"$lt": _jetzt().isoformat()}},
                    {"besitzer": kennung}]}
    try:
        coll.replace_one(frei, satz, upsert=True)
    except Exception as exc:  # noqa: BLE001
        # Kein Treffer, aber das Dokument existiert: der Upsert will ein
        # zweites mit derselben _id anlegen -> DuplicateKeyError. Genau das
        # ist "fremder, gueltiger Merker".
        if getattr(exc, "code", None) == 11000 or "E11000" in str(exc) \
                or type(exc).__name__ == "DuplicateKeyError":
            return None
        raise
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


# ---------------------------------------------------------------- N6: Auslaufen
def schreiber_kennung() -> str:
    """Diesen Prozess eindeutig benennen (Rechner + Prozessnummer)."""
    import os as _os
    import socket as _socket
    return f"{_socket.gethostname()}:{_os.getpid()}"


#: Rollenprüfung 22.09.2026 (RP-245/RP-396): Hintergrundarbeiten dieses
#: Prozesses, die gerade schreiben (Aufraeumlauf). Die Middleware zaehlt nur
#: HTTP-Anfragen — ein laufender Aufraeumlauf loeschte deshalb weiter, waehrend
#: die Sicherung schon "0 offene Schreibzugriffe" las. server.py meldet die
#: Summe aus beidem (run_schreiber_melden_forever).
_HINTERGRUND = {"offen": 0}


def hintergrund_offen() -> int:
    """Wie viele schreibende Hintergrundarbeiten laufen in diesem Prozess?"""
    return max(0, int(_HINTERGRUND["offen"]))


class hintergrund_schreibt:
    """`with wartung.hintergrund_schreibt(): ...` — zaehlt eine laufende
    Hintergrundarbeit als Schreiber, bis der Block endet (auch bei Fehlern)."""

    def __enter__(self):
        _HINTERGRUND["offen"] += 1
        return self

    def __exit__(self, *_):
        _HINTERGRUND["offen"] = max(0, _HINTERGRUND["offen"] - 1)
        return False


async def schreiber_melden(db, offen: int) -> None:
    """Stand dieses Prozesses melden. Wirft nie — die Sicherung faellt sonst
    auf ihre Wartezeit zurueck, aber der Betrieb darf daran nicht scheitern."""
    try:
        await db[SCHREIBER_COLLECTION].update_one(
            {"_id": schreiber_kennung()},
            {"$set": {"offen": int(offen), "stand": _jetzt()}},
            upsert=True)
    except Exception:  # noqa: BLE001
        pass


def schreiber_stand(coll, frisch_s: int = SCHREIBER_FRISCH_S) -> tuple:
    """Fuer die Sicherung (pymongo, synchron): (ruhig, offen_gesamt, prozesse).

    `ruhig` ist nur dann True, wenn MINDESTENS EIN Prozess gemeldet hat und
    alle frischen Meldungen null sagen. Meldet niemand, ist das kein "ruhig"
    — dann weiss die Sicherung schlicht nichts und wartet weiter.
    """
    from datetime import timedelta as _td
    grenze = _jetzt() - _td(seconds=frisch_s)
    offen = prozesse = 0
    for d in coll.find({"stand": {"$gte": grenze}}, {"_id": 0, "offen": 1}):
        prozesse += 1
        offen += int(d.get("offen") or 0)
    return (prozesse > 0 and offen == 0), offen, prozesse


async def schreiben_pausiert(db) -> bool:
    """Pruefbericht 20.09.2026 (Nr. 3): Die Middleware entscheidet nach der
    HTTP-Methode — GET darf durch. Einige GET-Wege schreiben aber trotzdem
    (Altbestands-Reparatur, Abrufzaehler, abgelaufene Merker). Waehrend einer
    Schreibpause aendern sie damit die Datenbank mitten im Dump.

    Solche Schreibzugriffe sind ALLE nachholbar oder entbehrlich — sie
    fragen hier nach und lassen es dann einfach. Wirft nie: kann die Frage
    nicht beantwortet werden, wird geschrieben wie bisher (lieber ein
    Zaehler zu viel als eine kaputte Funktion)."""
    try:
        return await aktiv_async(db, "POST")
    except Exception:  # noqa: BLE001
        return False
