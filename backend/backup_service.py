"""Automatisches tägliches Backup — läuft IM Backend (kein OS-Scheduler nötig).

Jede Nacht um BACKUP_HOUR (Standard 03:00 lokale Zeit) wird
scripts/backup_mongo.py als eigener Prozess gestartet: MongoDB-Collections
als .bson.gz + kompletter Datei-Speicher (uploads/), Aufbewahrung 14 Tage.
Funktioniert identisch auf Windows (jetzt) und Linux (späterer Server).

Zusätzlich: beim Backend-Start wird nachgeholt, falls das letzte Backup
älter als 24h ist (PC war um 03:00 evtl. aus).

Gültig (Go-Live-Audit) ist ein Backup nur, wenn seine manifest.json existiert
und keine "unvollstaendig"-Einträge hat — nur solche zählen für die
Nachhol-Logik und die Readiness-Auskunft (letztes_backup_info). Exit-Code 2
des Skripts (UNVOLLSTAENDIG) und 1 (FEHLER) lösen einen Betriebsalarm aus
(betrieb.alarm: backup_unvollstaendig / backup_fehlgeschlagen).

Runde 21 (Pruefbefund Backup A): Zusaetzlich muss das Backup in sich stimmig
sein (backup_bewertung.ist_gut). Ein Rueckfall nach gescheitertem Snapshot
(Manifest "inkonsistent", Exit 3) zaehlt weder als letzter guter Stand
(Nachholung, system_flags.letztes_vollstaendiges_backup) noch als
"vollstaendig" fuer /ready und Admin/Betrieb, und loest den Alarm
backup_inkonsistent aus.
"""
import asyncio
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backup_bewertung import (BESCHAEDIGT_FELD, inkonsistenz, ist_gut,
                              ist_stichtagsgenau, mangel)
from deps import log

from konfig import schalter_env, zahl_env  # Pruefung 14.09.2026: keine Abstuerze durch .env-Tippfehler
BACKUP_HOUR = zahl_env("BACKUP_HOUR", 3, unten=0, oben=23)
#: Pruefbericht 20.09.2026 (AL-11): alle so viele Tage die Pruefsummen des
#: juengsten guten Backups nachrechnen (je Server, siehe pruefsummen_nachrechnen)
PRUEFSUMMEN_INTERVALL_TAGE = zahl_env("BACKUP_PRUEFSUMMEN_TAGE", 7, unten=1)


def backup_aktiv() -> bool:
    """Pruefbericht 20.09.2026 (AL-20): sichert DIESER Server? BACKUP_AKTIV
    (Standard true). Auf einem Server bewusst false setzen (z. B. prod2),
    damit die lokalen Sicherungen nicht mal hier, mal dort liegen."""
    return schalter_env("BACKUP_AKTIV", True)
BACKUP_DIR = Path(os.environ.get("BACKUP_DIR", r"C:\AutoSchnell-Backups")
                  if sys.platform == "win32"
                  else os.environ.get("BACKUP_DIR", "/var/backups/autoschnell"))
_SCRIPT = Path(__file__).resolve().parent / "scripts" / "backup_mongo.py"
_BACKUP_NAME = re.compile(r"autoschnell-\d{4}-\d{2}-\d{2}_\d{4}")


def _backup_ordner() -> list:
    """Alle Backup-Ordner, juengster zuerst (der Name traegt den Zeitstempel)."""
    try:
        return sorted([p for p in BACKUP_DIR.iterdir()
                       if p.is_dir() and p.name.startswith("autoschnell-")],
                      key=lambda p: p.name, reverse=True)
    except OSError:
        return []


def _manifest(p: Path):
    try:
        m = json.loads((p / "manifest.json").read_text(encoding="utf-8"))
        return m if isinstance(m, dict) else None
    except (OSError, ValueError):
        return None


def _ist_gut(manifest) -> bool:
    """Runde 21: vollstaendig UND stimmig (gemeinsame Bewertung mit den
    Skripten). Bisher genuegte "unvollstaendig leer" — ein Rueckfall nach
    gescheitertem Snapshot galt damit als gutes Backup."""
    return ist_gut(manifest)


def _erstellt(p: Path, manifest) -> datetime:
    """Zeitpunkt des Backups: created_at aus dem Manifest, sonst mtime (UTC)."""
    try:
        t = datetime.fromisoformat(str((manifest or {}).get("created_at")))
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return datetime.now(timezone.utc)


def _alter_stunden(zeit: datetime) -> float:
    return (datetime.now(timezone.utc) - zeit).total_seconds() / 3600


def _last_backup_age_hours() -> float:
    """Alter des juengsten GUTEN Backups in Stunden (inf = keins).
    Ordner ohne Manifest, mit unvollstaendig-Eintraegen oder (Runde 21)
    inkonsistente zaehlen nicht — sonst wuerde ein kaputtes Backup die
    Nachhol-Logik beruhigen."""
    for p in _backup_ordner():
        m = _manifest(p)
        if _ist_gut(m):
            return _alter_stunden(_erstellt(p, m))
    return float("inf")


def letztes_backup_info() -> dict:
    """Auskunft fuer die Readiness-Pruefung: Zustand des JUENGSTEN Backups
    (vollstaendig? stimmig? offsite?) plus Hinweis auf das letzte gute.

    "vollstaendig" heisst seit Runde 21 "zaehlt als gutes Backup": False
    auch dann, wenn nur die Konsistenz fehlt (Rueckfall nach gescheitertem
    Snapshot). Dafuer gibt es die Felder "konsistent" (kein
    Konsistenzmangel), "konsistenz" (Text aus dem Manifest),
    "inkonsistent" (Grund) und "stichtagsgenau" (Snapshot/Schreibpause).
    Runde 21 (Gegenpruefung): ohne Manifest ist "konsistent" None
    (unbekannt), nicht False — sonst meldete der ANDERE Server ueber den
    Datenbank-Eintrag "INKONSISTENT", obwohl nur das Manifest fehlt."""
    info = {"alter_stunden": None, "pfad": None, "vollstaendig": False,
            "konsistent": None, "konsistenz": None, "inkonsistent": "",
            "stichtagsgenau": False,
            "offsite": False, "erstellt": None, "hinweis": ""}
    ordner = _backup_ordner()
    if not ordner:
        info["hinweis"] = f"kein Backup unter {BACKUP_DIR}"
        return info
    p = ordner[0]
    m = _manifest(p)
    zeit = _erstellt(p, m)
    alter = _alter_stunden(zeit)
    fehlend, grund = mangel(m)
    info.update(alter_stunden=round(alter, 2), pfad=str(p), erstellt=zeit.isoformat(),
                vollstaendig=_ist_gut(m),
                konsistent=(not grund) if m is not None else None,
                konsistenz=(m or {}).get("konsistenz"), inkonsistent=grund,
                stichtagsgenau=ist_stichtagsgenau(m),
                offsite=bool((m or {}).get("offsite")))
    if m is None:
        info["hinweis"] = "manifest.json fehlt oder unlesbar"
    elif not info["vollstaendig"]:
        teile = []
        if grund:
            teile.append("INKONSISTENT: " + grund)
        if fehlend:
            teile.append("UNVOLLSTAENDIG: " + "; ".join(fehlend))
        info["hinweis"] = "; ".join(teile)
    elif alter > 26:
        info["hinweis"] = f"letztes vollstaendiges Backup ist {alter:.0f} h alt"
    else:
        info["hinweis"] = "ok"
    if not info["vollstaendig"]:
        for q in ordner[1:]:
            mq = _manifest(q)
            if _ist_gut(mq):
                info["hinweis"] += (f"; letztes vollstaendiges Backup: {q.name} "
                                    f"({_alter_stunden(_erstellt(q, mq)):.0f} h alt)")
                break
        else:
            info["hinweis"] += "; KEIN vollstaendiges Backup vorhanden"
    return info


async def _alarm(db, typ: str, ref: str, **details) -> None:
    if db is None:
        return  # Einzelprozess ohne DB-Handle (lokal) — nur Log
    from betrieb import alarm
    await alarm(db, typ, ref=ref, **details)


def _schreibpause_noetig() -> bool:
    """Soll dieser Lauf mit Schreibpause (`--wartung`) laufen?

    NUR auf ausdrueckliche Anweisung (BACKUP_WARTUNG=true). Zwischenstand
    dieser Ueberlegung, damit ihn niemand noch einmal gehen muss:

    Am 20.09.2026 vormittags stand hier "automatisch, sobald kein Replica
    Set laeuft" — gedacht als Abhilfe fuer Sicherungen, die sonst nicht aus
    EINEM Zeitpunkt stammen. Die naechste Durchsicht hat daran gleich drei
    Loecher gezeigt, und sie hatte recht:

      * Der "Wartungsmodus" ist keine Schreibpause, sondern ein KOMPLETTER
        API-Ausfall: die Middleware beantwortet auch lesende Anfragen,
        Downloads und den Marktplatz mit 503. Automatisch eingeschaltet
        haette das taeglich einen Ausfall fuer die Dauer des Dumps bedeutet.
      * Er haelt ohnehin nur die HTTP-Wege an. Link-, Beweis-, Aufraeum- und
        Abo-Worker schreiben weiter — das Ergebnis waere also trotzdem nicht
        stichtagsgenau, haette sich aber so genannt.
      * Bricht das Skript hart ab (Zeitlimit -> kill), laeuft sein
        Aufraeumen nicht: das Flag bliebe stehen. Dagegen gibt es jetzt eine
        Ablaufzeit (siehe wartung_setzen/Middleware), aber eine taegliche
        Automatik waere trotzdem das falsche Standardverhalten.

    Wer wirklich stichtagsgenau sichern will, betreibt Mongo als Replica
    Set — dann liest die Sicherung alle Collections in EINER Snapshot-
    Sitzung, ohne jede Unterbrechung. /api/ready sagt es, wenn das fehlt.
    """
    return os.environ.get("BACKUP_WARTUNG", "").strip().lower() in (
        "1", "true", "ja", "yes")


async def _run_backup(db=None) -> bool:
    argumente = [str(_SCRIPT), "--dir", str(BACKUP_DIR)]
    if _schreibpause_noetig():
        argumente.append("--wartung")
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-X", "utf8", *argumente,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    try:
        # Review 09/2026: ohne Zeitlimit blieb ein haengendes Backup ewig
        # offen und blockierte alle folgenden Laeufe.
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=3 * 3600)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        log.error("[backup] FEHLGESCHLAGEN: Zeitlimit (3 h) ueberschritten, "
                  "Prozess beendet")
        await _alarm(db, "backup_fehlgeschlagen", "zeitlimit",
                     ausgabe="Zeitlimit (3 h) ueberschritten, Prozess beendet")
        await stand_speichern(db)
        return False
    zeilen = (out or b"").decode("utf-8", "replace").strip().splitlines()
    tail = zeilen[-1] if zeilen else ""
    ausgabe = "\n".join(zeilen[-8:])
    namen = _BACKUP_NAME.findall(ausgabe)
    ref = namen[-1] if namen else ""
    # Runde 21: nicht nur dem Exit-Code glauben — das Manifest des gerade
    # geschriebenen Ordners ist die Wahrheit (so bleibt auch ein Skript,
    # das trotz Mangel 0 liefert, nicht unbemerkt).
    ordner = (BACKUP_DIR / ref) if ref else None
    ordner_da = bool(ordner) and ordner.is_dir()
    m = _manifest(ordner) if ordner_da else None
    fehlend, grund = mangel(m)
    rc = proc.returncode
    ok = False
    if rc in (0, 2, 3):
        inkonsistent = rc == 3 or bool(grund)
        unvoll = rc == 2 or bool(fehlend) or (rc == 3 and "UNVOLLSTAENDIG" in ausgabe)
        if inkonsistent:
            log.error("[backup] INKONSISTENT (zaehlt nicht als gutes Backup): %s", tail)
            await _alarm(db, "backup_inkonsistent", ref, ausgabe=ausgabe,
                         grund=grund or "siehe Ausgabe",
                         hinweis="Snapshot gescheitert, Collections nacheinander "
                                 "gelesen. Replica Set pruefen (rs.status()), "
                                 "Sicherung erneut starten; zum Einspielen ein "
                                 "gutes Backup waehlen.")
        if unvoll:
            log.error("[backup] UNVOLLSTAENDIG: %s", tail)
            await _alarm(db, "backup_unvollstaendig", ref, ausgabe=ausgabe)
        if rc == 0 and ordner_da and m is None:
            log.error("[backup] Manifest fehlt trotz Exit 0: %s", ordner)
            await _alarm(db, "backup_fehlgeschlagen", ref, ausgabe=ausgabe,
                         code=rc, grund="manifest.json fehlt oder unlesbar")
        elif not inkonsistent and not unvoll:
            log.info("[backup] %s", tail)
            ok = True
    else:
        log.error("[backup] FEHLGESCHLAGEN (Code %s): %s", rc, tail)
        await _alarm(db, "backup_fehlgeschlagen", ref or f"code-{rc}",
                     ausgabe=ausgabe, code=rc)
    await stand_speichern(db)
    return ok


# ---- Zwei Server (06.09.2026) ----
# Die Sicherung laeuft auf EINEM der beiden Server (Sperre in der
# Datenbank); ihr Manifest liegt auf dessen Platte. Fragt der Load
# Balancer den ANDEREN Server nach /api/ready, fand der dort keine
# Sicherung und warnte. Deshalb steht der Stand der letzten Sicherung
# zusaetzlich in der Datenbank, die beide sehen.
_STAND_ID = "letztes_backup"


_STAND_VOLL_ID = "letztes_vollstaendiges_backup"


async def stand_speichern(db) -> None:
    """Nach jedem Lauf: Ergebnis der juengsten Sicherung in system_flags.

    Runde 10: Der letzte VERSUCH und die letzte VOLLSTAENDIGE Sicherung
    werden getrennt gefuehrt. Sonst ueberschrieb ein fehlgeschlagener Lauf
    den gueltigen Stand, und die Nachholung glaubte, es gaebe eine junge
    Sicherung."""
    if db is None:
        return
    try:
        import socket
        info = letztes_backup_info()
        info["server"] = socket.gethostname()
        info["gespeichert"] = datetime.now(timezone.utc).isoformat()
        await db.system_flags.update_one({"_id": _STAND_ID}, {"$set": info}, upsert=True)
        # Runde 21: nur ein GUTES Backup (vollstaendig UND stimmig) wird zum
        # letzten guten Stand, den beide Server fuer die Nachholung sehen.
        if info.get("vollstaendig") and info.get("konsistent") and not info.get("inkonsistent"):
            await db.system_flags.update_one({"_id": _STAND_VOLL_ID}, {"$set": info}, upsert=True)
    except Exception as exc:                        # noqa: BLE001
        log.warning("[backup] Stand konnte nicht gespeichert werden: %s", exc)


def _db_eintrag_inkonsistent(doc) -> str:
    """Runde 21: Konsistenzmangel eines system_flags-Eintrags (Felder
    konsistent/konsistenz/inkonsistent, wie stand_speichern sie schreibt).
    Eintraege von vor Runde 21 tragen keine Konsistenzangabe; sie werden
    nach ihren Feldern bewertet, soweit vorhanden."""
    if not isinstance(doc, dict):
        return ""
    grund = inkonsistenz(doc)
    if not grund and doc.get("konsistent") is False:
        grund = "laut Datenbank-Eintrag nicht stimmig"
    return grund


async def letztes_vollstaendiges_alter_global(db) -> float:
    """Alter (Stunden) der letzten GUTEN Sicherung, egal auf welchem
    Server — fuer die Nachhol-Entscheidung. Unbekannt = sehr alt.
    Runde 21: ein Eintrag, der als inkonsistent oder nicht vollstaendig
    gekennzeichnet ist, zaehlt nicht."""
    lokal = _last_backup_age_hours()
    if db is None:
        return lokal
    try:
        doc = await db.system_flags.find_one(
            {"_id": _STAND_VOLL_ID},
            {"_id": 0, "erstellt": 1, "vollstaendig": 1, "konsistent": 1,
             "konsistenz": 1, "inkonsistent": 1})
        if doc and (doc.get("vollstaendig") is False or _db_eintrag_inkonsistent(doc)):
            return lokal
        if doc and doc.get("erstellt"):
            zeit = datetime.fromisoformat(doc["erstellt"])
            if zeit.tzinfo is None:
                zeit = zeit.replace(tzinfo=timezone.utc)
            return min(lokal, _alter_stunden(zeit))
    except Exception:                               # noqa: BLE001
        pass
    return lokal


async def letztes_backup_info_global(db) -> dict:
    """Wie letztes_backup_info(), aber serveruebergreifend: die juengere
    von lokaler Platte und Datenbank-Eintrag gewinnt. Das Alter wird aus
    dem Erstellzeitpunkt neu berechnet, nicht aus dem gespeicherten Wert."""
    lokal = letztes_backup_info()
    if db is None:
        return lokal
    try:
        doc = await db.system_flags.find_one({"_id": _STAND_ID}, {"_id": 0})
    except Exception:                               # noqa: BLE001
        doc = None
    if not doc or not doc.get("erstellt"):
        return lokal
    try:
        zeit = datetime.fromisoformat(doc["erstellt"])
        if zeit.tzinfo is None:
            zeit = zeit.replace(tzinfo=timezone.utc)
        doc["alter_stunden"] = round(_alter_stunden(zeit), 2)
    except (TypeError, ValueError):
        return lokal
    if lokal.get("alter_stunden") is not None and lokal["alter_stunden"] <= doc["alter_stunden"]:
        return lokal
    # Runde 21: ein als inkonsistent gekennzeichneter Eintrag gilt nie als
    # vollstaendig — /ready und Admin/Betrieb warnen dann.
    grund = _db_eintrag_inkonsistent(doc)
    if grund:
        doc["vollstaendig"] = False
        doc["konsistent"] = False
        doc["inkonsistent"] = grund
        if "INKONSISTENT" not in str(doc.get("hinweis") or ""):
            doc["hinweis"] = ("INKONSISTENT: " + grund
                              + (f"; {doc['hinweis']}" if doc.get("hinweis") else ""))
    doc["quelle"] = f"Datenbank (Sicherung lief auf {doc.get('server', '?')})"
    return doc


def _seconds_until_next_run() -> float:
    now = datetime.now()
    nxt = now.replace(hour=BACKUP_HOUR, minute=0, second=0, microsecond=0)
    if nxt <= now:
        nxt += timedelta(days=1)
    return (nxt - now).total_seconds()


# ---- Pruefsummen-Nachrechnung (Pruefbericht 20.09.2026, AL-11) ----
# ist_gut() bewertete nur Manifest-Felder; ob die .bson.gz auf der Platte
# noch zum Manifest passen, pruefte bis jetzt nur der Restore selbst (und
# offsite_pruefen.py --laden von Hand). Eine kippende Platte haette man also
# erst im Notfall bemerkt. Jetzt rechnet jeder Server alle
# PRUEFSUMMEN_INTERVALL_TAGE die SHA-256 seines juengsten guten Backups nach;
# eine Abweichung setzt einen Betriebsalarm und markiert das Manifest als
# "beschaedigt" — damit liefert ist_gut() False (Rotation, Restore, /ready,
# Nachholung suchen sich das naechste gute).
_PRUEFSUMMEN_MERKER = ".pruefsummen.json"


def _sha256(p: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _pruefsummen_vergleichen(ordner: Path, manifest: dict) -> list:
    """Blockierend: jede Datei aus manifest['files'] nachrechnen. Liefert die
    Abweichungen (leer = alles in Ordnung)."""
    fehler = []
    for rel, info in sorted((manifest.get("files") or {}).items()):
        f = ordner / rel
        if not f.is_file():
            fehler.append(f"fehlt: {rel}")
            continue
        try:
            if f.stat().st_size != int(info.get("bytes", -1)):
                fehler.append(f"Groesse weicht ab: {rel}")
            elif _sha256(f) != info.get("sha256"):
                fehler.append(f"Pruefsumme falsch: {rel}")
        except OSError as exc:
            fehler.append(f"nicht lesbar: {rel} ({exc})")
    return fehler


def _manifest_schreiben(ordner: Path, manifest: dict) -> None:
    tmp = ordner / "manifest.json.tmp"
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, ordner / "manifest.json")


def _pruefsummen_merker_lesen() -> dict:
    try:
        m = json.loads((BACKUP_DIR / _PRUEFSUMMEN_MERKER).read_text(encoding="utf-8"))
        return m if isinstance(m, dict) else {}
    except (OSError, ValueError):
        return {}


def pruefsummen_faellig() -> bool:
    """Ist die Nachrechnung fuer das juengste gute Backup dran? (noch nie,
    anderes Backup als beim letzten Mal, oder laenger als das Intervall her)"""
    for p in _backup_ordner():
        if _ist_gut(_manifest(p)):
            merker = _pruefsummen_merker_lesen()
            if merker.get("ordner") != p.name:
                return True
            try:
                zeit = datetime.fromisoformat(str(merker.get("geprueft")))
            except (TypeError, ValueError):
                return True
            if zeit.tzinfo is None:
                zeit = zeit.replace(tzinfo=timezone.utc)
            return _alter_stunden(zeit) >= PRUEFSUMMEN_INTERVALL_TAGE * 24
    return False


async def pruefsummen_nachrechnen(db=None, *, erzwingen: bool = False):
    """AL-11: Pruefsummen des juengsten GUTEN lokalen Backups nachrechnen.

    Liefert None (nichts faellig / kein gutes Backup) oder
    {"ordner", "dateien", "fehler": [...]}. Bei Abweichung: Betriebsalarm
    backup_beschaedigt und Manifest-Feld "beschaedigt" (ist_gut -> False).
    Wirft nie."""
    try:
        if not erzwingen and not pruefsummen_faellig():
            return None
        ziel = None
        for p in _backup_ordner():
            m = _manifest(p)
            if _ist_gut(m):
                ziel = (p, m)
                break
        if ziel is None:
            return None
        ordner, manifest = ziel
        log.info("[backup] Pruefsummen-Nachrechnung: %s", ordner.name)
        fehler = await asyncio.to_thread(_pruefsummen_vergleichen, ordner, manifest)
        ergebnis = {"ordner": ordner.name, "dateien": len(manifest.get("files") or {}),
                    "fehler": fehler, "geprueft": datetime.now(timezone.utc).isoformat()}
        if fehler:
            grund = (f"Pruefsummen-Nachrechnung am {ergebnis['geprueft'][:10]}: "
                     + "; ".join(fehler[:5])
                     + (f" (+{len(fehler) - 5} weitere)" if len(fehler) > 5 else ""))
            log.error("[backup] BESCHAEDIGT: %s — %s", ordner.name, grund)
            try:
                manifest[BESCHAEDIGT_FELD] = grund
                await asyncio.to_thread(_manifest_schreiben, ordner, manifest)
            except Exception as exc:  # noqa: BLE001
                log.error("[backup] Manifest %s nicht markierbar: %s", ordner.name, exc)
            await _alarm(db, "backup_beschaedigt", ordner.name, grund=grund,
                         hinweis="Lokale Sicherung passt nicht mehr zum Manifest "
                                 "(Platte pruefen). Sie zaehlt nicht mehr als gutes "
                                 "Backup; naechste Sicherung abwarten oder von Hand "
                                 "starten, Offsite-Kopie mit offsite_pruefen.py --laden "
                                 "pruefen.")
            await stand_speichern(db)
        else:
            log.info("[backup] Pruefsummen in Ordnung: %s (%d Dateien)",
                     ordner.name, ergebnis["dateien"])
        try:
            (BACKUP_DIR / _PRUEFSUMMEN_MERKER).write_text(
                json.dumps({"ordner": ordner.name, "geprueft": ergebnis["geprueft"],
                            "fehler": fehler}), encoding="utf-8")
        except OSError as exc:
            log.warning("[backup] Pruefsummen-Merker nicht geschrieben: %s", exc)
        return ergebnis
    except Exception as exc:  # noqa: BLE001
        log.warning("[backup] Pruefsummen-Nachrechnung nicht moeglich: %s", exc)
        return None


async def run_backup_forever(db=None) -> None:
    """Backup-Schleife. Bei mehreren Worker-Prozessen (WEB_CONCURRENCY>1)
    sorgt eine Sperre in MongoDB dafuer, dass pro Tag nur EIN Worker das
    Backup zieht — sonst gaebe es acht identische Backups gleichzeitig."""

    async def _may_run(tag: str):
        if db is None:
            return True  # Einzelprozess (lokal) — keine Sperre noetig
        from job_lock import acquire
        # 20h TTL: erst am naechsten Tag darf wieder jemand ran; faellt der
        # Gewinner aus, uebernimmt nach Ablauf ein anderer Worker.
        # fehler_melden (20.09.2026): eine STOERUNG der Datenbank kommt als
        # Ausnahme zurueck statt als None — sonst haelt der Aufrufer sie
        # faelschlich fuer "ein anderer Worker sichert schon".
        return await acquire(db, f"backup-{tag}", ttl_seconds=20 * 3600,
                             fehler_melden=True)

    async def _lauf_mit_sperre() -> bool:
        """True = ok oder nichts zu tun (anderer Worker); False = Fehlschlag."""
        tag = datetime.now().strftime("%Y-%m-%d")
        try:
            token = await _may_run(tag)
        except Exception as exc:  # noqa: BLE001
            # Nachpruefung 20.09.2026: Liess sich die Sperre wegen einer
            # Datenbank-Stoerung nicht pruefen, galt der Lauf als "nichts zu
            # tun" — der Tag blieb ohne Sicherung und ohne Wiederholung.
            # Jetzt: Fehlschlag, also in einer Stunde noch einmal.
            log.warning("[backup] Tagessperre nicht pruefbar (%s) — "
                        "Wiederholung in einer Stunde", exc)
            return False
        if not token:
            return True
        ok = await _run_backup(db)
        if not ok and db is not None and isinstance(token, str):
            # Phase 3 (3.5, F3): Tagessperre nach einem Fehlschlag freigeben —
            # vorher gab es an diesem Tag keinen zweiten Versuch mehr.
            from job_lock import release
            await release(db, f"backup-{tag}", token=token)
        return ok

    # Nachholen: wenn das letzte VOLLSTAENDIGE Backup >24h alt ist, sofort
    # eins ziehen (PC koennte zur geplanten Zeit ausgeschaltet gewesen sein).
    await asyncio.sleep(30)  # Backend erst in Ruhe hochfahren lassen
    # Runde 10: fuer das Nachholen zaehlt nur eine VOLLSTAENDIGE Sicherung.
    alter = await letztes_vollstaendiges_alter_global(db)
    fehlversuche = 0
    if alter > 24:
        log.info("[backup] Letztes vollstaendiges Backup >24h alt — hole nach …")
        if not await _lauf_mit_sperre():
            fehlversuche = 1
    while True:
        # AL-11: nach jedem (Nach-)Lauf, wenn faellig — im Thread, blockiert
        # die Schleife nicht laenger als die Sicherung selbst.
        await pruefsummen_nachrechnen(db)
        wait = _seconds_until_next_run()
        if fehlversuche:
            # Phase 3 (F3): nach einem Fehlschlag in einer Stunde erneut (bis 3x)
            wait = min(wait, 3600)
        log.info("[backup] Naechstes Backup in %.1f h (%02d:00 Uhr)",
                 wait / 3600, BACKUP_HOUR)
        await asyncio.sleep(wait)
        if await _lauf_mit_sperre():
            fehlversuche = 0
        else:
            fehlversuche = fehlversuche + 1 if fehlversuche < 3 else 0
