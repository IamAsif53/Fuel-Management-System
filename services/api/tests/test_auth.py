"""Tests for JWT auth and RBAC (operator / supervisor)."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from app.auth import create_access_token, hash_password
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


def test_missing_bearer_is_unauthorized(override_db):
    client = TestClient(app)
    res = client.post("/api/control/mode", json={"mode": "advisory"})
    assert res.status_code == 401
    assert res.json()["detail"]["code"] == "UNAUTHORIZED"


def test_revoked_static_token_rejected(override_db):
    client = TestClient(app)
    res = client.post(
        "/api/control/mode",
        headers={"Authorization": "Bearer change-me-operator"},
        json={"mode": "advisory"},
    )
    assert res.status_code == 401


def test_login_roles_and_rbac(override_db):
    settings = get_settings()
    demo_users = (
        f"viewer-1:vpass123:viewer,"
        f"op-1:opass123:operator,"
        f"sup-1:{hash_password('spass123')}:supervisor"
    )
    with patch.object(settings, "demo_users", demo_users):
        client = TestClient(app)

        bad = client.post("/api/auth/login", json={"username": "op-1", "password": "wrong"})
        assert bad.status_code == 401

        res_o = client.post("/api/auth/login", json={"username": "op-1", "password": "opass123"})
        assert res_o.status_code == 200
        o_tok = res_o.json()["access_token"]
        assert res_o.json()["role"] == "operator"

        res_v = client.post("/api/auth/login", json={"username": "viewer-1", "password": "vpass123"})
        assert res_v.status_code == 200
        v_tok = res_v.json()["access_token"]

        res_s = client.post("/api/auth/login", json={"username": "sup-1", "password": "spass123"})
        assert res_s.status_code == 200
        s_tok = res_s.json()["access_token"]
        assert res_s.json()["role"] == "supervisor"

        me_o = client.get("/api/auth/me", headers={"Authorization": f"Bearer {o_tok}"})
        assert me_o.status_code == 200
        assert me_o.json()["role"] == "operator"

        with (
            patch("app.routers.decisions._call_engine", new=AsyncMock(return_value={"status": "ok", "mode": "advisory"})),
            patch("app.routers.decisions.insert_audit_entry", new=AsyncMock()),
        ):
            # Viewer cannot change mode
            assert client.post(
                "/api/control/mode",
                headers={"Authorization": f"Bearer {v_tok}"},
                json={"mode": "advisory"},
            ).status_code == 403

            # Operator can change mode
            assert client.post(
                "/api/control/mode",
                headers={"Authorization": f"Bearer {o_tok}"},
                json={"mode": "advisory"},
            ).status_code == 200

            # Operator cannot set policy; supervisor can
            assert client.post(
                "/api/control/policy",
                headers={"Authorization": f"Bearer {o_tok}"},
                json={"policy": "optimizer"},
            ).status_code == 403

            assert client.post(
                "/api/control/policy",
                headers={"Authorization": f"Bearer {s_tok}"},
                json={"policy": "heuristic"},
            ).status_code == 200


def test_create_access_token_claims():
    settings = get_settings()
    tok, ttl = create_access_token("sup-2", "supervisor", settings)
    assert ttl == settings.jwt_expire_seconds
    claims = jwt.decode(tok, settings.api_jwt_secret, algorithms=["HS256"])
    assert claims["sub"] == "sup-2"
    assert claims["role"] == "supervisor"
