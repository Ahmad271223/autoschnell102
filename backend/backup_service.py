"""Automatisches tägliches Backup — läuft IM Backend (kein OS-Scheduler nötig).

Jede Nacht um BACKUP_HOUR (Standard 03:00 lokale Zeit) wird
scripts/backup_mongo.py als eigener Prozess gestartet: MongoDB-Collections
als .bson.gz + kompletter Datei-Speicher (uploads/), Aufbewahrung 14 Tage.
Funktioniert identisch auf Windows (jetzt) und Linux (späterer Server).

Zusätzlich: beim Backend-Start wird nachgeholt, falls das letzte Backup
älter als 24h ist (PC war um 03:00 evtl. aus).
"""
import asyncio
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

from deps import log

BACKUP_HOUR = int(os.environ.get("BACKUP_HOUR", "3"))
BACKUP_DIR = Path(os.environ.get("BACKUP_DIR", r"C:\AutoSchnell-Backups")
                  if sys.platform == "win32"
                  else os.environ.get("BACKUP_DIR", "/var/backups/autoschnell"))
_SCRIPT = Path(__file__).resolve().parent / "scripts" / "backup_mongo.py"


def _last_backup_age_hours() -> float:
    """Alter des juengsten Backups in Stunden (inf = noch keins)."""
    try:
        dumps = [p for p in BACKUP_DIR.iterdir()
                 if p.is_dir() and p.name.startswith("autoschnell-")]
        if not dumps:
            return float("inf")
        newest = max(p.stat().st_mtime for p in dumps)
        return (datetime.now().timestamp() - newest) / 3600
    except OSError:
        return float("inf")


async def _run_backup() -> None:
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-X", "utf8", str(_SCRIPT), "--dir", str(BACKUP_DIR),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    tail = (out or b"").decode("utf-8", "replace").strip().splitlines()[-1:] or [""]
    if proc.returncode == 0:
        log.info("[backup] %s", tail[0])
    else:
        log.error("[backup] FEHLGESCHLAGEN (Code %s): %s", proc.returncode, tail[0])


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

    # Nachholen: wenn das letzte Backup >24h alt ist, sofort eins ziehen
    # (PC koennte zur geplanten Zeit ausgeschaltet gewesen sein).
    await asyncio.sleep(30)  # Backend erst in Ruhe hochfahren lassen
    if _last_backup_age_hours() > 24 and await _may_run(
            datetime.now().strftime("%Y-%m-%d")):
        log.info("[backup] Letztes Backup >24h alt — hole nach …")
        await _run_backup()
    while True:
        wait = _seconds_until_next_run()
        log.info("[backup] Naechstes Backup in %.1f h (%02d:00 Uhr)",
                 wait / 3600, BACKUP_HOUR)
        await asyncio.sleep(wait)
        if await _may_run(datetime.now().strftime("%Y-%m-%d")):
            await _run_backup()
