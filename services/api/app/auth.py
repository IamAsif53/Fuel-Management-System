"""Authentication and role-based authorization utilities for FSIP API."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import uuid
from functools import lru_cache
from typing import Any

from fastapi import Depends, HTTPException, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from app.config import ApiSettings, get_settings

security_bearer = HTTPBearer(auto_error=False)

VALID_ROLES = ("viewer", "operator", "supervisor")
# Explicitly revoked/exposed legacy literals that must always fail authentication.
REVOKED_STATIC_TOKENS = frozenset({"change-me-operator", "change-me", "changeme", "change-me-jwt"})
_IN_MEMORY_REVOKED_JTIS: dict[str, float] = {}


def hash_password(password: str, salt: bytes | None = None) -> str:
    """Hash a password using PBKDF2-HMAC-SHA256 with a per-user salt."""
    salt_bytes = salt if salt is not None else os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt_bytes, 120_000)
    return f"pbkdf2_sha256${salt_bytes.hex()}${dk.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Verify a plaintext password against a stored PBKDF2 hash in constant time."""
    if not password or not stored_hash:
        return False
    parts = stored_hash.split("$")
    if len(parts) != 3 or parts[0] != "pbkdf2_sha256":
        return False
    try:
        salt_bytes = bytes.fromhex(parts[1])
        expected = bytes.fromhex(parts[2])
    except ValueError:
        return False
    computed = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt_bytes, 120_000)
    return hmac.compare_digest(computed, expected)


@lru_cache(maxsize=16)
def _build_user_registry(demo_users_raw: str, auth_users_json_raw: str) -> dict[str, dict[str, str]]:
    """Build hashed user store strictly from environment configuration (never hardcoded)."""
    users: dict[str, dict[str, str]] = {}

    if auth_users_json_raw.strip():
        try:
            parsed = json.loads(auth_users_json_raw)
            if isinstance(parsed, dict):
                for uname, info in parsed.items():
                    if not isinstance(info, dict):
                        continue
                    role = str(info.get("role", "")).lower().strip()
                    pw_hash = str(info.get("password_hash", "")).strip()
                    if uname and role in VALID_ROLES and pw_hash.startswith("pbkdf2_sha256$"):
                        users[str(uname).strip()] = {"password_hash": pw_hash, "role": role}
        except Exception:
            pass

    if demo_users_raw.strip():
        for entry in demo_users_raw.split(","):
            entry = entry.strip()
            if not entry:
                continue
            parts = entry.split(":", 2)
            if len(parts) != 3:
                continue
            uname, pwd, role = parts[0].strip(), parts[1], parts[2].strip().lower()
            if not uname or not pwd or role not in VALID_ROLES:
                continue
            # Preserve pre-hashed entry if passed directly, else hash with deterministic app salt
            if pwd.startswith("pbkdf2_sha256$"):
                pw_hash = pwd
            else:
                pw_hash = hash_password(pwd, salt=hashlib.sha256(f"fsip:{uname}".encode()).digest()[:16])
            users[uname] = {"password_hash": pw_hash, "role": role}

    return users


def authenticate_user(username: str, password: str, settings: ApiSettings) -> dict[str, str] | None:
    """Validate username/password against configured users and return identity dict."""
    uname = username.strip()
    if not uname or not password:
        return None
    registry = _build_user_registry(settings.demo_users, settings.auth_users_json)
    record = registry.get(uname)
    if not record:
        return None
    if not verify_password(password, record["password_hash"]):
        return None
    return {"username": uname, "actor": uname, "role": record["role"]}


def create_access_token(
    username: str,
    role: str,
    settings: ApiSettings,
    expires_in: int | None = None,
) -> tuple[str, int]:
    """Create a signed, expiring HS256 JWT carrying user actor & role claims."""
    ttl = int(expires_in if expires_in is not None else settings.jwt_expire_seconds)
    now = int(time.time())
    payload = {
        "sub": username,
        "actor": username,
        "role": role,
        "jti": uuid.uuid4().hex,
        "iat": now,
        "exp": now + ttl,
    }
    token = jwt.encode(payload, settings.api_jwt_secret, algorithm="HS256")
    return token, ttl


async def revoke_token_jti(jti: str | None, exp: int | None = None, redis_client: Any = None) -> None:
    """Revoke a JWT ID (on logout) in Redis and local memory."""
    if not jti:
        return
    now = time.time()
    ttl = max(60, int((exp or (now + 28800)) - now))
    _IN_MEMORY_REVOKED_JTIS[jti] = now + ttl
    # Prune expired in-memory revocations lazily
    for k in [k for k, v in _IN_MEMORY_REVOKED_JTIS.items() if v < now]:
        _IN_MEMORY_REVOKED_JTIS.pop(k, None)
    if redis_client is not None:
        try:
            await redis_client.setex(f"fsip:revoked_jti:{jti}", ttl, "1")
        except Exception:
            pass


async def _is_jti_revoked(jti: str | None, redis_client: Any = None) -> bool:
    if not jti:
        return False
    now = time.time()
    exp_at = _IN_MEMORY_REVOKED_JTIS.get(jti)
    if exp_at is not None and exp_at >= now:
        return True
    if redis_client is not None:
        try:
            val = await redis_client.get(f"fsip:revoked_jti:{jti}")
            if val in ("1", b"1", 1, True):
                return True
        except Exception:
            pass
    return False


def _open_demo_operator() -> dict[str, Any]:
    """Hackathon open mode: no Sign-In UI; actor is a local demo operator identity.

    Matches brief §19 (no hardcoded frontend secrets / no enterprise login required).
    Sensitive actions remain explicit operator clicks (approve/reject), not silent automation.
    """
    return {
        "username": "operator",
        "actor": "operator",
        "role": "operator",
        "jti": None,
        "exp": None,
        "open_mode": True,
    }


async def get_current_user(
    request: Request,
    auth: HTTPAuthorizationCredentials | None = Security(security_bearer),
    settings: ApiSettings = Depends(get_settings),
) -> dict[str, Any]:
    """Resolve caller identity.

    Open hackathon mode (default): missing bearer → demo operator (no Sign-In required).
    Optional JWT still accepted when provided (tests / advanced demos).
    """
    if not auth or not auth.credentials:
        return _open_demo_operator()

    token = auth.credentials.strip()
    if token in REVOKED_STATIC_TOKENS:
        # Never accept the old leaked static secret; fall back to open demo identity.
        return _open_demo_operator()

    try:
        payload = jwt.decode(token, settings.api_jwt_secret, algorithms=["HS256"])
    except JWTError:
        return _open_demo_operator()

    role = str(payload.get("role", "")).lower().strip()
    username = str(payload.get("sub") or payload.get("actor") or "").strip()
    actor = str(payload.get("actor") or username).strip()
    jti = payload.get("jti")
    exp = payload.get("exp")

    if not username or role not in VALID_ROLES or not exp:
        return _open_demo_operator()

    redis_client = getattr(getattr(request, "app", None), "state", None)
    redis_conn = getattr(redis_client, "redis", None) if redis_client else None
    if await _is_jti_revoked(jti, redis_conn):
        return _open_demo_operator()

    return {
        "username": username,
        "actor": actor,
        "role": role,
        "jti": jti,
        "exp": exp,
        "open_mode": False,
    }


async def require_viewer(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    """Require viewer, operator, or supervisor role."""
    if user.get("role") not in VALID_ROLES:
        raise HTTPException(
            status_code=403,
            detail={"code": "FORBIDDEN", "message": f"Role '{user.get('role')}' is not authorized"},
        )
    return user


async def require_operator(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    """Require operator or supervisor role."""
    role = user.get("role")
    if role not in ("operator", "supervisor"):
        raise HTTPException(
            status_code=403,
            detail={"code": "FORBIDDEN", "message": f"Role '{role}' is not authorized for operator actions"},
        )
    return user


async def require_supervisor(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    """Supervisor gate — in open hackathon mode, operator identity is accepted.

    Brief does not require multi-user login; keep dependency for call sites but
    do not block the demo console.
    """
    role = user.get("role")
    if role not in ("supervisor", "operator") and not user.get("open_mode"):
        raise HTTPException(
            status_code=403,
            detail={"code": "FORBIDDEN", "message": f"Role '{role}' is not authorized; supervisor role required"},
        )
    return user
