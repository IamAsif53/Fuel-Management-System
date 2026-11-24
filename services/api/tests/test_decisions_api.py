"""Tests for Decision, Recommendation, Engine Control, and Audit API endpoints."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from app.config import get_settings
from app.db import get_session
from app.main import app


@pytest.fixture
def auth_headers():
    settings = get_settings()
    return {"Authorization": f"Bearer {settings.api_operator_token}"}


@pytest.mark.asyncio
async def test_get_recommendations():
    """Verify /api/recommendations returns pending recommendations."""
    mock_session = AsyncMock()
    mock_items = [
        {
            "id": 1,
            "sim_run_id": 10,
            "tick": 24,
            "policy": "heuristic",
            "status": "PENDING_APPROVAL",
            "lines": [
                {
                    "id": 101,
                    "line_no": 1,
                    "source_depot_id": "depot-dhaka",
                    "destination_station_id": "station-banani",
                    "fuel_type": "DIESEL",
                    "quantity": 5000.0,
                }
            ],
        }
    ]

    with patch("app.routers.decisions.fetch_recommendations", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = mock_items

        async def override_get_session():
            yield mock_session

        app.dependency_overrides[get_session] = override_get_session
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                res = await client.get("/api/recommendations")
                assert res.status_code == 200
                data = res.json()
                assert "items" in data
                assert len(data["items"]) == 1
                assert data["items"][0]["id"] == 1
        finally:
            app.dependency_overrides.pop(get_session, None)


@pytest.mark.asyncio
async def test_list_decisions():
    """Verify /api/decisions returns paginated items and total count."""
    mock_session = AsyncMock()
    mock_items = [{"id": 1, "tick": 24, "status": "APPROVED", "policy": "heuristic"}]

    with patch("app.routers.decisions.fetch_decisions", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = (mock_items, 1)

        async def override_get_session():
            yield mock_session

        app.dependency_overrides[get_session] = override_get_session
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                res = await client.get("/api/decisions?limit=10&offset=0")
                assert res.status_code == 200
                data = res.json()
                assert data["total"] == 1
                assert len(data["items"]) == 1
        finally:
            app.dependency_overrides.pop(get_session, None)


@pytest.mark.asyncio
async def test_get_decision_by_id():
    """Verify /api/decisions/{id} returns full decision or 404."""
    mock_session = AsyncMock()
    mock_decision = {
        "id": 42,
        "sim_run_id": 1,
        "tick": 24,
        "status": "APPROVED",
        "lines": [{"id": 1, "quantity": 5000.0}],
        "outcome": None,
    }

    with patch("app.routers.decisions.fetch_decision_by_id", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.side_effect = lambda session, decision_id: mock_decision if decision_id == 42 else None

        async def override_get_session():
            yield mock_session

        app.dependency_overrides[get_session] = override_get_session
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                # 200 for existing
                res = await client.get("/api/decisions/42")
                assert res.status_code == 200
                assert res.json()["id"] == 42

                # 404 for missing
                res_missing = await client.get("/api/decisions/999")
                assert res_missing.status_code == 404
                assert res_missing.json()["detail"]["code"] == "DECISION_NOT_FOUND"
        finally:
            app.dependency_overrides.pop(get_session, None)


@pytest.mark.asyncio
async def test_approve_decision(auth_headers):
    """Verify /api/decisions/{id}/approve validates auth, forwards to engine, and audits."""
    mock_session = AsyncMock()
    mock_decision = {"id": 10, "status": "APPROVED", "approved_by": "operator", "lines": []}

    with (
        patch("app.routers.decisions._call_engine", new_callable=AsyncMock) as mock_call,
        patch("app.routers.decisions.insert_audit_entry", new_callable=AsyncMock) as mock_audit,
        patch("app.routers.decisions.fetch_decision_by_id", new_callable=AsyncMock) as mock_fetch,
    ):
        mock_call.return_value = {"status": "ok", "decision_id": 10}
        mock_fetch.return_value = mock_decision

        async def override_get_session():
            yield mock_session

        app.dependency_overrides[get_session] = override_get_session
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                # Unauthorized without token
                res_unauth = await client.post("/api/decisions/10/approve", json={})
                assert res_unauth.status_code == 401

                # Authorized with operator token
                res = await client.post(
                    "/api/decisions/10/approve",
                    headers=auth_headers,
                    json={"line_ids": [1], "quantities": {"1": 4000.0}},
                )
                assert res.status_code == 200
                assert res.json()["status"] == "APPROVED"
                assert mock_call.await_count == 1
                assert mock_audit.await_count == 1
                assert mock_audit.await_args[1]["action"] == "DECISION_APPROVED"
        finally:
            app.dependency_overrides.pop(get_session, None)


@pytest.mark.asyncio
async def test_reject_decision(auth_headers):
    """Verify /api/decisions/{id}/reject validates auth, forwards to engine, and audits."""
    mock_session = AsyncMock()
    mock_decision = {"id": 10, "status": "REJECTED", "rejected_reason": "NOT_NEEDED: unnecessary"}

    with (
        patch("app.routers.decisions._call_engine", new_callable=AsyncMock) as mock_call,
        patch("app.routers.decisions.insert_audit_entry", new_callable=AsyncMock) as mock_audit,
        patch("app.routers.decisions.fetch_decision_by_id", new_callable=AsyncMock) as mock_fetch,
    ):
        mock_call.return_value = {"status": "ok", "decision_id": 10}
        mock_fetch.return_value = mock_decision

        async def override_get_session():
            yield mock_session

        app.dependency_overrides[get_session] = override_get_session
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                res = await client.post(
                    "/api/decisions/10/reject",
                    headers=auth_headers,
                    json={"reason_code": "NOT_NEEDED", "comment": "unnecessary"},
                )
                assert res.status_code == 200
                assert res.json()["status"] == "REJECTED"
                assert mock_audit.await_count == 1
                assert mock_audit.await_args[1]["action"] == "DECISION_REJECTED"
        finally:
            app.dependency_overrides.pop(get_session, None)


@pytest.mark.asyncio
async def test_plan_now(auth_headers):
    """Verify POST /api/plan-now forwards to engine and audits."""
    mock_session = AsyncMock()

    with (
        patch("app.routers.decisions._call_engine", new_callable=AsyncMock) as mock_call,
        patch("app.routers.decisions.insert_audit_entry", new_callable=AsyncMock) as mock_audit,
    ):
        mock_call.return_value = {"status": "ok", "decision_id": 15, "lines_count": 2}

        async def override_get_session():
            yield mock_session

        app.dependency_overrides[get_session] = override_get_session
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                res = await client.post("/api/plan-now", headers=auth_headers)
                assert res.status_code == 202
                assert res.json()["accepted"] is True
                assert mock_audit.await_count == 1
                assert mock_audit.await_args[1]["action"] == "PLAN_NOW_TRIGGERED"
        finally:
            app.dependency_overrides.pop(get_session, None)


@pytest.mark.asyncio
async def test_control_mode_and_policy(auth_headers):
    """Verify mode and policy endpoints require auth, forward to engine, and audit."""
    mock_session = AsyncMock()

    with (
        patch("app.routers.decisions._call_engine", new_callable=AsyncMock) as mock_call,
        patch("app.routers.decisions.insert_audit_entry", new_callable=AsyncMock) as mock_audit,
    ):
        mock_call.return_value = {"status": "ok"}

        async def override_get_session():
            yield mock_session

        app.dependency_overrides[get_session] = override_get_session
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                # Mode
                res_mode = await client.post("/api/control/mode", headers=auth_headers, json={"mode": "autopilot"})
                assert res_mode.status_code == 200

                # Policy
                res_pol = await client.post(
                    "/api/control/policy",
                    headers=auth_headers,
                    json={"policy": "optimizer"},
                )
                assert res_pol.status_code == 200
                assert mock_audit.await_count == 2
        finally:
            app.dependency_overrides.pop(get_session, None)


@pytest.mark.asyncio
async def test_manual_allocation(auth_headers):
    """Verify /api/manual-allocation dispatches manual line and records audit log."""
    mock_session = AsyncMock()

    with (
        patch("app.routers.decisions._call_engine", new_callable=AsyncMock) as mock_call,
        patch("app.routers.decisions.insert_audit_entry", new_callable=AsyncMock) as mock_audit,
    ):
        mock_call.return_value = {"status": "ok", "decision_id": 99, "line_id": 101}

        async def override_get_session():
            yield mock_session

        app.dependency_overrides[get_session] = override_get_session
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                payload = {
                    "source_depot_id": "depot-dhaka",
                    "destination_station_id": "station-banani",
                    "route_id": "route-dhaka-banani",
                    "fuel_type": "DIESEL",
                    "quantity": 5000.0,
                    "comment": "Urgent top-up",
                }
                res = await client.post("/api/manual-allocation", headers=auth_headers, json=payload)
                assert res.status_code == 201
                assert res.json()["decision_id"] == 99
                assert mock_audit.await_count == 1
                assert mock_audit.await_args[1]["action"] == "MANUAL_ALLOCATION_DISPATCHED"
        finally:
            app.dependency_overrides.pop(get_session, None)


@pytest.mark.asyncio
async def test_get_audit_logs():
    """Verify /api/audit returns audit log entries."""
    mock_session = AsyncMock()
    mock_logs = [
        {
            "id": 1,
            "at": "2026-11-23T12:00:00Z",
            "actor": "operator",
            "action": "DECISION_APPROVED",
            "entity_type": "decision",
            "entity_id": "10",
            "payload": {"line_ids": [1]},
        }
    ]

    with patch("app.routers.decisions.fetch_audit_logs", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = mock_logs

        async def override_get_session():
            yield mock_session

        app.dependency_overrides[get_session] = override_get_session
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                res = await client.get("/api/audit")
                assert res.status_code == 200
                data = res.json()
                assert "items" in data
                assert len(data["items"]) == 1
                assert data["items"][0]["action"] == "DECISION_APPROVED"
        finally:
            app.dependency_overrides.pop(get_session, None)


@pytest.mark.asyncio
async def test_whatif_endpoints(auth_headers):
    """Verify both /api/whatif and alias /api/decisions/what-if forward to engine."""
    mock_response = {
        "tick": 100,
        "scenarios": [
            {"name": "do_nothing", "unmet": 0.0, "overflow": 0.0, "min_station_sl": 1.0, "stations": []},
            {"name": "custom", "unmet": 0.0, "overflow": 0.0, "min_station_sl": 1.0, "stations": []},
        ],
        "disclaimer": "Projection by our model; not executed.",
    }

    with patch("app.routers.decisions._call_engine", new_callable=AsyncMock) as mock_call:
        mock_call.return_value = mock_response

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            payload = {"horizon_ticks": 48, "lines": [], "events": []}

            # 1. Canonical endpoint per 21_API_CONTRACTS.md §2.4
            res_canon = await client.post("/api/whatif", headers=auth_headers, json=payload)
            assert res_canon.status_code == 200
            assert res_canon.json()["disclaimer"] == "Projection by our model; not executed."
            assert mock_call.await_args[1]["path"] == "/internal/whatif"

            # 2. Compatibility alias endpoint
            res_alias = await client.post("/api/decisions/what-if", headers=auth_headers, json=payload)
            assert res_alias.status_code == 200
            assert res_alias.json()["disclaimer"] == "Projection by our model; not executed."

