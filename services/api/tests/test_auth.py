"""Tests for open-mode identity (no Sign-In) and optional JWT helpers."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from app.auth import create_access_token, get_current_user, hash_password
from app.config import get_settings
from app.db import get_session
from app.main import app
from fastapi.testclient import TestClient
from jose import jwt


@pytest.fixture
def override_db():
    mock_session = AsyncMock()

    async def _sess():
        yield mock_session

    app.dependency_overrides[get_session] = _sess
    yield mock_session
    app.dependency_overrides.pop(get_session, None)


def test_open_mode_allows_operator_actions_without_bearer(override_db):
    """Brief-aligned: missing bearer uses demo operator — no Sign-In required."""
    client = TestClient(app)
    with (
        patch("app.routers.decisions._call_engine", new=AsyncMock(return_value={"status": "ok", "mode": "advisory"})),
        patch("app.routers.decisions.insert_audit_entry", new=AsyncMock()),
    ):
        res = client.post("/api/control/mode", json={"mode": "advisory"})
    assert res.status_code == 200, res.text


def test_revoked_static_change_me_operator_falls_back_to_open_mode(override_db):
    """Legacy leaked secret must never authenticate; open demo operator is used instead."""
    client = TestClient(app)
    headers = {"Authorization": "Bearer change-me-operator"}
    with (
        patch("app.routers.decisions._call_engine", new=AsyncMock(return_value={"status": "ok", "mode": "advisory"})),
        patch("app.routers.decisions.insert_audit_entry", new=AsyncMock()),
    ):
        res = client.post("/api/control/mode", headers=headers, json={"mode": "advisory"})
    assert res.status_code == 200, res.text


def test_optional_login_still_works_for_role_tokens(override_db):
    """Optional JWT login remains available for tests, but UI Sign-In is removed."""
    settings = get_settings()
    demo_users = (
        f"viewer-1:vpass123:viewer,"
        f"op-1:opass123:operator,"
        f"sup-1:{hash_password('spass123')}:supervisor"
    )
    with patch.object(settings, "demo_users", demo_users):
        client = TestClient(app)

        bad_login = client.post("/api/auth/login", json={"username": "op-1", "password": "wrong"})
        assert bad_login.status_code == 401

        res_o = client.post("/api/auth/login", json={"username": "op-1", "password": "opass123"})
        assert res_o.status_code == 200
        o_tok = res_o.json()["access_token"]
        assert res_o.json()["role"] == "operator"

        res_v = client.post("/api/auth/login", json={"username": "viewer-1", "password": "vpass123"})
        assert res_v.status_code == 200
        v_tok = res_v.json()["access_token"]

        me_o = client.get("/api/auth/me", headers={"Authorization": f"Bearer {o_tok}"})
        assert me_o.status_code == 200
        assert me_o.json()["username"] == "op-1"
        assert me_o.json()["role"] == "operator"

        # Viewer JWT still cannot perform operator writes
        with (
            patch("app.routers.decisions._call_engine", new=AsyncMock(return_value={"status": "ok"})),
            patch("app.routers.decisions.insert_audit_entry", new=AsyncMock()),
        ):
            res_v_write = client.post(
                "/api/control/mode",
                headers={"Authorization": f"Bearer {v_tok}"},
                json={"mode": "advisory"},
            )
        assert res_v_write.status_code == 403
        assert res_v_write.json()["detail"]["code"] == "FORBIDDEN"


def test_create_access_token_claims():
    settings = get_settings()
    tok, ttl = create_access_token("sup-2", "supervisor", settings)
    assert ttl == settings.jwt_expire_seconds
    claims = jwt.decode(tok, settings.api_jwt_secret, algorithms=["HS256"])
    assert claims["sub"] == "sup-2"
    assert claims["actor"] == "sup-2"
    assert claims["role"] == "supervisor"
    assert "jti" in claims and "exp" in claims


@pytest.mark.asyncio
async def test_get_current_user_open_mode_without_credentials():
    from unittest.mock import MagicMock

    from fastapi.security import HTTPAuthorizationCredentials

    settings = get_settings()
    request = MagicMock()
    request.app.state.redis = None

    user = await get_current_user(request, auth=None, settings=settings)
    assert user["username"] == "operator"
    assert user["role"] == "operator"
    assert user.get("open_mode") is True

    bogus = HTTPAuthorizationCredentials(scheme="Bearer", credentials="not-a-jwt")
    user2 = await get_current_user(request, auth=bogus, settings=settings)
    assert user2.get("open_mode") is True
