"""Geteilte Abhängigkeiten und Helpers — wird von server.py UND routes/*
importiert. Vermeidet Zirkular-Imports.
"""
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from motor.motor_asyncio import AsyncIOMotorClient

from auth import decode_token

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

# ---------- Logging ----------
log = logging.getLogger("autohandel")

# ---------- Mongo ----------
# Connection-Pool explizit dimensioniert fuer 200-500 gleichzeitige Nutzer.
# maxPoolSize: max. gleichzeitige Sockets pro Prozess; minPoolSize haelt
# warme Verbindungen vor (kein Cold-Start unter Last). Timeouts verhindern,
# dass ein langsamer DB-Call den Request unbegrenzt blockiert.
_MONGO_MAX_POOL = int(os.environ.get("MONGO_MAX_POOL_SIZE", "100"))
_MONGO_MIN_POOL = int(os.environ.get("MONGO_MIN_POOL_SIZE", "10"))
client = AsyncIOMotorClient(
    os.environ["MONGO_URL"],
    maxPoolSize=_MONGO_MAX_POOL,
    minPoolSize=_MONGO_MIN_POOL,
    serverSelectionTimeoutMS=5000,
    connectTimeoutMS=5000,
    socketTimeoutMS=30000,
    retryWrites=True,
)
db = client[os.environ["DB_NAME"]]

# ---------- Auth ----------
bearer = HTTPBearer(auto_error=False)


# ---------- Helpers ----------
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean_doc(d: dict) -> dict:
    if not d:
        return d
    d.pop("_id", None)
    return d


# ---------- Auth dependencies ----------
async def current_user(creds: Optional[HTTPAuthorizationCredentials] = Depends(bearer)):
    if not creds or not creds.credentials:
        raise HTTPException(401, "Nicht authentifiziert")
    try:
        payload = decode_token(creds.credentials)
    except Exception:
        raise HTTPException(401, "Token ungültig")
    user = await db.users.find_one({"id": payload.get("sub")}, {"_id": 0, "password_hash": 0})
    if not user or not user.get("active"):
        raise HTTPException(401, "Account deaktiviert")
    # Single-session enforcement: jwt sid must equal user's current sid
    if user.get("current_session_id") and payload.get("sid") != user.get("current_session_id"):
        raise HTTPException(401, "Session beendet (anderes Gerät aktiv)")
    return user


async def current_admin(user=Depends(current_user)):
    if user.get("role") != "admin":
        raise HTTPException(403, "Admin erforderlich")
    return user


async def get_subscription_status(dealer_id: str) -> dict:
    sub = await db.subscriptions.find_one({"dealer_id": dealer_id}, sort=[("created_at", -1)])
    if not sub:
        return {"active": False, "plan": None, "expires_at": None, "status": "none"}
    plan = sub.get("plan")
    status_ = sub.get("status", "active")
    expires_at = sub.get("expires_at")
    if plan == "lifetime":
        return {"active": True, "plan": "lifetime", "expires_at": None, "status": "active"}
    # Gekündigte Abos bleiben aktiv, bis das Ablaufdatum erreicht ist.
    # Erst dann läuft der Account wirklich aus.
    active = status_ in ("active", "cancelled")
    if expires_at:
        try:
            ea = datetime.fromisoformat(expires_at)
            if ea < datetime.now(timezone.utc):
                active = False
                status_ = "expired"
        except Exception:
            pass
    return {"active": active, "plan": plan, "expires_at": expires_at, "status": status_}


async def require_active_sub(user=Depends(current_user)):
    if user.get("role") == "admin":
        return user
    sub = await get_subscription_status(user["dealer_id"])
    if not sub["active"]:
        raise HTTPException(402, "Kein aktives Abo")
    return user


async def log_activity(dealer_id: str, user_id: str, action: str,
                       ref: Optional[str] = None, meta: Optional[dict] = None):
    await db.activity_logs.insert_one({
        "id": str(uuid.uuid4()), "dealer_id": dealer_id, "user_id": user_id,
        "action": action, "ref": ref, "meta": meta or {},
        "created_at": now_iso(),
    })
