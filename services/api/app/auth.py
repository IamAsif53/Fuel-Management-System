"""Authentication and authorization dependencies for FSIP API."""
from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from app.config import ApiSettings, get_settings

security_bearer = HTTPBearer(auto_error=False)


async def require_operator(
    auth: HTTPAuthorizationCredentials | None = Security(security_bearer),
    settings: ApiSettings = Depends(get_settings),
) -> dict[str, Any]:
    """Require operator or supervisor authorization via Bearer token or JWT.

    Accepts:
    1. Static operator token matching settings.api_operator_token
    2. Valid JWT signed with settings.api_jwt_secret containing role 'operator' or 'supervisor'
    """
    if not auth or not auth.credentials:
        raise HTTPException(
            status_code=401,
            detail={"code": "UNAUTHORIZED", "message": "Missing bearer authentication token"},
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = auth.credentials

    # 1. Match configured operator token
    if token == settings.api_operator_token:
        return {"actor": "operator", "role": "operator"}

    # 2. Match signed JWT
    try:
        payload = jwt.decode(token, settings.api_jwt_secret, algorithms=["HS256"])
        role = payload.get("role")
        if role not in ("operator", "supervisor"):
            raise HTTPException(
                status_code=403,
                detail={"code": "FORBIDDEN", "message": f"Role '{role}' is not authorized for operator actions"},
            )
        return payload
    except JWTError:
        pass

    raise HTTPException(
        status_code=401,
        detail={"code": "UNAUTHORIZED", "message": "Invalid authentication token"},
        headers={"WWW-Authenticate": "Bearer"},
    )
