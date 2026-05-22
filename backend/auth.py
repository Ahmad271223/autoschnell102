"""Authentication helpers: JWT + bcrypt + single-session enforcement."""
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt

_jwt_secret_env = os.environ.get("JWT_SECRET", "")
if not _jwt_secret_env or _jwt_secret_env == "dev-secret":
    import secrets as _secrets
    import warnings as _warnings
    _jwt_secret_env = _secrets.token_hex(32)
    _warnings.warn(
        "JWT_SECRET is not set (or uses the insecure 'dev-secret' default). "
        "A random secret was generated — all existing sessions will be invalidated "
        "on every restart. Set JWT_SECRET in your .env file.",
        RuntimeWarning, stacklevel=1,
    )
JWT_SECRET = _jwt_secret_env
JWT_ALG = "HS256"
TOKEN_TTL_DAYS = 7

bearer = HTTPBearer(auto_error=False)


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
        return False


# Constant-time dummy hash: used when the requested account does not exist so
# that timing-based user-enumeration attacks measure a consistent bcrypt delay
# whether or not the user is in the database.
_DUMMY_HASH: str = hash_password("__autoschnell_dummy_never_matches__")


def create_token(user_id: str, session_id: str) -> str:
    exp = datetime.now(timezone.utc) + timedelta(days=TOKEN_TTL_DAYS)
    payload = {"sub": user_id, "sid": session_id, "exp": exp}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)


def decode_token(token: str) -> dict:
    return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])


def new_session_id() -> str:
    return str(uuid.uuid4())


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
):
    """Dependency injected via app state to access db. Real impl in server.py wrapper."""
    raise NotImplementedError  # replaced in server.py
