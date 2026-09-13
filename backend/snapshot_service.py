# -*- coding: utf-8 -*-
"""Alte Beweis-Snapshots — nur noch LESEN, LOESCHEN, PSEUDONYMISIEREN.

Seit 10.09.2026 entstehen keine Snapshots mehr (kein Playwright, kein
Browser). Neue Inserate bekommen ein Beweisdokument (beweis_service,
beweis_pdf). Dieses Modul haelt nur noch den Zugriff auf die bis dahin
erzeugten Aufnahmen (listing_snapshots + Dateien), bis sie nach der
bestehenden Regel verfallen sind (cleanup_service._expire_old_snapshots:
60 Tage, laenger nur bei Kaufvertrag). Danach kann das Modul entfallen.

Wo liegen die alten Dateien?
  1. Objektspeicher (R2/S3), sobald S3_ENDPOINT und S3_BUCKET gesetzt sind
     (Normalfall seit 09/2026, derselbe Eimer wie fuer Fotos)
  2. sonst der externe Altdienst (EMERGENT_LLM_KEY) — in Produktion aus
  3. sonst die lokale Platte unter backend/local_storage/ (Entwicklung)
Noch lokal liegende Altkopien werden beim Lesen weiterhin gefunden.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

log = logging.getLogger("autohandel.snapshot")

STORAGE_URL = "https://integrations.emergentagent.com/objstore/api/v1/storage"

_LOCAL_STORAGE = Path(__file__).parent / "local_storage"
_USE_S3 = bool(os.environ.get("S3_ENDPOINT", "").strip()
               and os.environ.get("S3_BUCKET", "").strip())
_USE_LOCAL = not _USE_S3 and not os.environ.get("EMERGENT_LLM_KEY")

_storage_key: Optional[str] = None


def _s3_speicher():
    """Der gemeinsame Datei-Speicher (storage_service) — nur bei _USE_S3."""
    from storage_service import storage
    return storage


def speicherort() -> str:
    """Fuer Diagnose und Tests: "s3", "extern" oder "lokal"."""
    return "s3" if _USE_S3 else ("lokal" if _USE_LOCAL else "extern")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_storage() -> Optional[str]:
    """Sitzung beim externen Altdienst (nur ohne R2 und mit EMERGENT_LLM_KEY)."""
    global _storage_key
    if _storage_key:
        return _storage_key
    emergent_key = os.environ.get("EMERGENT_LLM_KEY", "")
    if not emergent_key:
        return None
    try:
        r = requests.post(f"{STORAGE_URL}/init", json={"emergent_key": emergent_key},
                          timeout=30)
        r.raise_for_status()
        _storage_key = r.json()["storage_key"]
        return _storage_key
    except Exception as exc:  # noqa: BLE001
        log.error("snapshot storage init failed: %s", exc)
        return None


def _safe_local_path(path: str) -> Path:
    """Pfad relativ zu _LOCAL_STORAGE, ohne das Verzeichnis zu verlassen
    (Path-Traversal-Schutz). Wirft ValueError bei Ausbruchsversuch."""
    base = _LOCAL_STORAGE.resolve()
    dest = (base / path).resolve()
    if dest != base and base not in dest.parents:
        raise ValueError(f"Ungueltiger Storage-Pfad (Traversal): {path!r}")
    return dest


def get_object(path: str) -> tuple[bytes, str]:
    """Datei lesen. Liefert (bytes, content_type)."""
    if _USE_S3:
        ext = path.lower().rsplit(".", 1)[-1]
        ct = "application/pdf" if ext == "pdf" else "image/jpeg"
        try:
            return _s3_speicher().load(path), ct
        except Exception as exc:  # noqa: BLE001
            dest = _safe_local_path(path)
            if dest.exists():
                return dest.read_bytes(), ct
            raise FileNotFoundError(f"snapshot {path}: {exc}") from exc
    if _USE_LOCAL:
        dest = _safe_local_path(path)
        if not dest.exists():
            raise FileNotFoundError(f"local_storage: {path} not found")
        ct = "application/pdf" if dest.suffix.lower() == ".pdf" else "image/jpeg"
        return dest.read_bytes(), ct
    key = init_storage()
    if not key:
        raise RuntimeError("Storage not initialized")
    r = requests.get(f"{STORAGE_URL}/objects/{path}", headers={"X-Storage-Key": key},
                     timeout=60)
    r.raise_for_status()
    return r.content, r.headers.get("Content-Type", "application/octet-stream")


async def get_object_async(path: str) -> tuple[bytes, str]:
    return await asyncio.to_thread(get_object, path)


def delete_object(path: str) -> bool:
    """Loeschen. True bei Erfolg oder wenn die Datei schon fehlt."""
    if _USE_S3:
        ok = True
        try:
            ok = bool(_s3_speicher().delete(path))
        except Exception as exc:  # noqa: BLE001
            log.warning("snapshot-loeschung %s im objektspeicher: %s", path, exc)
            ok = False
        try:
            _safe_local_path(path).unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass
        return ok
    if _USE_LOCAL:
        try:
            _safe_local_path(path).unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass
        return True
    key = init_storage()
    if not key:
        return False
    try:
        r = requests.delete(f"{STORAGE_URL}/objects/{path}",
                            headers={"X-Storage-Key": key}, timeout=30)
        if r.status_code in (200, 204, 404):
            return True
        log.warning("delete_object %s -> %s", path, r.status_code)
        return False
    except Exception as exc:  # noqa: BLE001
        log.warning("delete_object %s failed: %s", path, exc)
        return False


async def delete_object_async(path: str) -> bool:
    return await asyncio.to_thread(delete_object, path)


def snapshot_pseudonym(kennung: str) -> str:
    """Runde 13: B8 — deterministisches Pseudonym fuer dealer_id/user_id in
    listing_snapshots (SHA-256, 12 Hex-Zeichen, Bindestrich statt
    Doppelpunkt, weil die alte dealer_id im Storage-Pfad steckt)."""
    return "geloescht-" + hashlib.sha256(str(kennung).encode("utf-8")).hexdigest()[:12]


async def snapshots_pseudonymisieren(db, *, dealer_id: Optional[str] = None,
                                     user_id: Optional[str] = None) -> int:
    """Runde 13: B8 — Firmen-/Nutzerkennung in alten Snapshots
    pseudonymisieren; die Aufnahmen selbst bleiben bis zu ihrem Verfall.
    Immer $set, nie $unset. Liefert die Zahl geaenderter Zeilen."""
    if not dealer_id and not user_id:
        return 0
    jetzt = _now_iso()
    n = 0
    if dealer_id:
        pseudo_dealer = snapshot_pseudonym(dealer_id)
        async for snap in db.listing_snapshots.find(
                {"dealer_id": dealer_id}, {"_id": 0, "id": 1, "user_id": 1}):
            ersatz = {"dealer_id": pseudo_dealer, "pseudonymisiert_at": jetzt}
            alt_user = snap.get("user_id")
            if alt_user and not str(alt_user).startswith("geloescht-"):
                ersatz["user_id"] = snapshot_pseudonym(alt_user)
            r = await db.listing_snapshots.update_one(
                {"id": snap["id"], "dealer_id": dealer_id}, {"$set": ersatz})
            n += r.modified_count
    if user_id:
        r = await db.listing_snapshots.update_many(
            {"user_id": user_id},
            {"$set": {"user_id": snapshot_pseudonym(user_id), "pseudonymisiert_at": jetzt}})
        n += r.modified_count
    return n
