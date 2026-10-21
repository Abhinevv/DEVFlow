"""Password hashing and signed bearer tokens (standard library only)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from datetime import UTC, datetime

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import ApiKey, Project, User

_SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32}
_bearer = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_hex, digest_hex = stored.split("$")
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), **_SCRYPT)
    return hmac.compare_digest(digest.hex(), digest_hex)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def create_token(user_id: int) -> str:
    settings = get_settings()
    payload = _b64(json.dumps({"sub": user_id, "exp": int(time.time()) + settings.token_ttl_hours * 3600}).encode())
    sig = _b64(hmac.new(settings.resolved_secret_key().encode(), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{sig}"


def decode_token(token: str) -> int | None:
    try:
        payload, sig = token.split(".")
    except ValueError:
        return None
    expected = _b64(hmac.new(get_settings().resolved_secret_key().encode(), payload.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        data = json.loads(_unb64(payload))
    except (ValueError, json.JSONDecodeError):
        return None
    if data.get("exp", 0) < time.time():
        return None
    return int(data["sub"])


API_KEY_PREFIX = "dfk_"


def new_api_key() -> tuple[str, str]:
    """Return (plaintext key, sha256 hash). The plaintext is shown to the user once and never stored."""
    key = API_KEY_PREFIX + secrets.token_urlsafe(32)
    return key, hash_api_key(key)


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _user_from_api_key(db: Session, key: str) -> User | None:
    record = db.scalar(select(ApiKey).where(ApiKey.key_hash == hash_api_key(key), ApiKey.revoked_at.is_(None)))
    if record is None:
        return None
    record.last_used_at = datetime.now(UTC)
    db.commit()
    return db.get(User, record.user_id)


def current_user(creds: HTTPAuthorizationCredentials | None = Depends(_bearer), db: Session = Depends(get_db)) -> User:
    user = None
    if creds and creds.credentials.startswith(API_KEY_PREFIX):
        user = _user_from_api_key(db, creds.credentials)
    elif creds:
        user_id = decode_token(creds.credentials)
        user = db.get(User, user_id) if user_id else None
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated", headers={"WWW-Authenticate": "Bearer"})
    return user


def owned_project(project_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> Project:
    project = db.get(Project, project_id)
    # Same 404 for "missing" and "not yours" so ids cannot be probed.
    if project is None or (project.owner_id != user.id and user.role != "admin"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    return project
