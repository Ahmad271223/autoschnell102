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

from backup_bewertung import inkonsistenz, ist_gut, ist_stichtagsgenau, mangel
from deps import log

BACKUP_HOUR = int(os.environ.get("BACKUP_HOUR", "3"))
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


async def _run_backup(db=None) -> None:
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-X", "utf8", str(_SCRIPT), "--dir", str(BACKUP_DIR),
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
        return
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
    else:
        log.error("[backup] FEHLGESCHLAGEN (Code %s): %s", rc, tail)
        await _alarm(db, "backup_fehlgeschlagen", ref or f"code-{rc}",
                     ausgabe=ausgabe, code=rc)
    await stand_speichern(db)


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


async def run_backup_forever(db=None) -> None:
    """Backup-Schleife. Bei mehreren Worker-Prozessen (WEB_CONCURRENCY>1)
    sorgt eine Sperre in MongoDB dafuer, dass pro Tag nur EIN Worker das
    Backup zieht — sonst gaebe es acht identische Backups gleichzeitig."""

    async def _may_run(tag: str) -> bool:
        if db is None:
            return True  # Einzelprozess (lokal) — keine Sperre noetig
        from job_lock import acquire
        # 20h TTL: erst am naechsten Tag darf wieder jemand ran; faellt der
        # Gewinner aus, uebernimmt nach Ablauf ein anderer Worker.
        return await acquire(db, f"backup-{tag}", ttl_seconds=20 * 3600)

    # Nachholen: wenn das letzte VOLLSTAENDIGE Backup >24h alt ist, sofort
    # eins ziehen (PC koennte zur geplanten Zeit ausgeschaltet gewesen sein).
    await asyncio.sleep(30)  # Backend erst in Ruhe hochfahren lassen
    # Runde 10: fuer das Nachholen zaehlt nur eine VOLLSTAENDIGE Sicherung.
    alter = await letztes_vollstaendiges_alter_global(db)
    if alter > 24 and await _may_run(
            datetime.now().strftime("%Y-%m-%d")):
        log.info("[backup] Letztes vollstaendiges Backup >24h alt — hole nach …")
        await _run_backup(db)
    while True:
        wait = _seconds_until_next_run()
        log.info("[backup] Naechstes Backup in %.1f h (%02d:00 Uhr)",
                 wait / 3600, BACKUP_HOUR)
        await asyncio.sleep(wait)
        if await _may_run(datetime.now().strftime("%Y-%m-%d")):
            await _run_backup(db)
