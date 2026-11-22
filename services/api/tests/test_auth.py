"""Tests for operator authorization dependency."""
from __future__ import annotations

from app.auth import require_operator
from app.config import get_settings
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from jose import jwt

auth_app = FastAPI()


@auth_app.post("/test-operator-action")
def operator_endpoint(operator=Depends(require_operator)):
    return {"ok": True, "actor": operator.get("actor")}


def test_require_operator_missing_token():
    client = TestClient(auth_app)
    res = client.post("/test-operator-action")
    assert res.status_code == 401
    assert res.json()["detail"]["code"] == "UNAUTHORIZED"


def test_require_operator_valid_static_token():
    settings = get_settings()
    client = TestClient(auth_app)
    headers = {"Authorization": f"Bearer {settings.api_operator_token}"}
    res = client.post("/test-operator-action", headers=headers)
    assert res.status_code == 200
    assert res.json()["ok"] is True
    assert res.json()["actor"] == "operator"


def test_require_operator_valid_jwt():
    settings = get_settings()
    token = jwt.encode(
        {"actor": "supervisor-1", "role": "supervisor"},
        settings.api_jwt_secret,
        algorithm="HS256",
    )
    client = TestClient(auth_app)
    headers = {"Authorization": f"Bearer {token}"}
    res = client.post("/test-operator-action", headers=headers)
    assert res.status_code == 200
    assert res.json()["actor"] == "supervisor-1"


def test_require_operator_forbidden_role():
    settings = get_settings()
    token = jwt.encode(
        {"actor": "viewer-1", "role": "viewer"},
        settings.api_jwt_secret,
        algorithm="HS256",
    )
    client = TestClient(auth_app)
    headers = {"Authorization": f"Bearer {token}"}
    res = client.post("/test-operator-action", headers=headers)
    assert res.status_code == 403
    assert res.json()["detail"]["code"] == "FORBIDDEN"
