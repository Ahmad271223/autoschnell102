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
"""
import asyncio
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

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


def _ist_vollstaendig(manifest) -> bool:
    return bool(manifest) and not manifest.get("unvollstaendig")


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
    """Alter des juengsten VOLLSTAENDIGEN Backups in Stunden (inf = keins).
    Ordner ohne Manifest oder mit unvollstaendig-Eintraegen zaehlen nicht —
    sonst wuerde ein kaputtes Backup die Nachhol-Logik beruhigen."""
    for p in _backup_ordner():
        m = _manifest(p)
        if _ist_vollstaendig(m):
            return _alter_stunden(_erstellt(p, m))
    return float("inf")


def letztes_backup_info() -> dict:
    """Auskunft fuer die Readiness-Pruefung: Zustand des JUENGSTEN Backups
    (vollstaendig? offsite?) plus Hinweis auf das letzte vollstaendige."""
    info = {"alter_stunden": None, "pfad": None, "vollstaendig": False,
            "offsite": False, "erstellt": None, "hinweis": ""}
    ordner = _backup_ordner()
    if not ordner:
        info["hinweis"] = f"kein Backup unter {BACKUP_DIR}"
        return info
    p = ordner[0]
    m = _manifest(p)
    zeit = _erstellt(p, m)
    alter = _alter_stunden(zeit)
    info.update(alter_stunden=round(alter, 2), pfad=str(p), erstellt=zeit.isoformat(),
                vollstaendig=_ist_vollstaendig(m), offsite=bool((m or {}).get("offsite")))
    if m is None:
        info["hinweis"] = "manifest.json fehlt oder unlesbar"
    elif not info["vollstaendig"]:
        info["hinweis"] = "UNVOLLSTAENDIG: " + "; ".join(
            str(x) for x in m.get("unvollstaendig") or [])
    elif alter > 26:
        info["hinweis"] = f"letztes vollstaendiges Backup ist {alter:.0f} h alt"
    else:
        info["hinweis"] = "ok"
    if not info["vollstaendig"]:
        for q in ordner[1:]:
            mq = _manifest(q)
            if _ist_vollstaendig(mq):
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
        return
    zeilen = (out or b"").decode("utf-8", "replace").strip().splitlines()
    tail = zeilen[-1] if zeilen else ""
    ausgabe = "\n".join(zeilen[-8:])
    namen = _BACKUP_NAME.findall(ausgabe)
    ref = namen[-1] if namen else ""
    if proc.returncode == 0:
        log.info("[backup] %s", tail)
    elif proc.returncode == 2:
        log.error("[backup] UNVOLLSTAENDIG: %s", tail)
        await _alarm(db, "backup_unvollstaendig", ref, ausgabe=ausgabe)
    else:
        log.error("[backup] FEHLGESCHLAGEN (Code %s): %s", proc.returncode, tail)
        await _alarm(db, "backup_fehlgeschlagen", ref or f"code-{proc.returncode}",
                     ausgabe=ausgabe, code=proc.returncode)


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
    if _last_backup_age_hours() > 24 and await _may_run(
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
