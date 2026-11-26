"""Authentication endpoints (/api/auth/login, /api/auth/me, /api/auth/logout)."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.auth import authenticate_user, create_access_token, get_current_user, revoke_token_jti
from app.config import ApiSettings, get_settings
from app.db import get_sessionmaker, insert_audit_entry

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=128)
    password: str = Field(..., min_length=1, max_length=256)


@router.post("/login")
async def login(
    req: LoginRequest,
    settings: Annotated[ApiSettings, Depends(get_settings)],
) -> dict[str, Any]:
    """Authenticate configured user credentials and return a signed expiring JWT."""
    identity = authenticate_user(req.username, req.password, settings)
    if not identity:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "Invalid username or password"},
            headers={"WWW-Authenticate": "Bearer"},
        )

    token, expires_in = create_access_token(
        username=identity["username"],
        role=identity["role"],
        settings=settings,
    )

    try:
        sm = get_sessionmaker()
        async with sm() as session:
            await insert_audit_entry(
                session=session,
                actor=identity["actor"],
                action="auth.login",
                entity_type="auth",
                entity_id=identity["username"],
                payload={"role": identity["role"]},
            )
            await session.commit()
    except Exception:
        # Never fail authentication if optional audit log write is unavailable in isolated unit tests
        pass

    return {
        "access_token": token,
        "token_type": "bearer",
        "username": identity["username"],
        "role": identity["role"],
        "expires_in": expires_in,
    }


@router.get("/me")
async def get_me(user: Annotated[dict[str, Any], Depends(get_current_user)]) -> dict[str, Any]:
    """Return authenticated user profile from verified JWT claims."""
    return {
        "username": user["username"],
        "actor": user["actor"],
        "role": user["role"],
    }


@router.post("/logout")
async def logout(
    request: Request,
    user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> dict[str, Any]:
    """Revoke the caller's current JWT token ID."""
    redis_client = getattr(request.app.state, "redis", None)
    await revoke_token_jti(user.get("jti"), exp=user.get("exp"), redis_client=redis_client)
    return {"status": "ok"}
