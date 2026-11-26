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
    from app.auth import create_access_token

    settings = get_settings()
    token, _ = create_access_token("operator", "operator", settings)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def supervisor_headers():
    from app.auth import create_access_token

    settings = get_settings()
    token, _ = create_access_token("supervisor", "supervisor", settings)
    return {"Authorization": f"Bearer {token}"}


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
                res_unauth = await client.post("/api/decisions/10/approve", json={})
                assert res_unauth.status_code == 401

                res = await client.post(
                    "/api/decisions/10/approve",
                    headers=auth_headers,
                    json={"line_ids": [1], "quantities": {"1": 4000.0}, "reviewed_driver": True},
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
async def test_control_mode_and_policy(auth_headers, supervisor_headers):
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

                # Policy: operator rejected (403), supervisor allowed (200)
                res_pol = await client.post(
                    "/api/control/policy",
                    headers=auth_headers,
                    json={"policy": "optimizer"},
                )
                assert res_pol.status_code == 403

                res_pol_sup = await client.post(
                    "/api/control/policy",
                    headers=supervisor_headers,
                    json={"policy": "heuristic"},
                )
                assert res_pol_sup.status_code == 200
                assert mock_audit.await_count == 2
        finally:
            app.dependency_overrides.pop(get_session, None)


@pytest.mark.asyncio
async def test_control_mode_stale_rejection(auth_headers):
    """Verify /api/control/mode returns 409 STALE_DATA if engine rejects enabling autopilot."""
    from fastapi import HTTPException

    mock_session = AsyncMock()

    with (
        patch("app.routers.decisions._call_engine", new_callable=AsyncMock) as mock_call,
        patch("app.routers.decisions.insert_audit_entry", new_callable=AsyncMock) as mock_audit,
    ):
        mock_call.side_effect = HTTPException(
            status_code=409,
            detail={
                "code": "STALE_DATA",
                "message": "Cannot enable Autopilot when data is stale or system is degraded",
            },
        )

        async def override_get_session():
            yield mock_session

        app.dependency_overrides[get_session] = override_get_session
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                res = await client.post("/api/control/mode", headers=auth_headers, json={"mode": "autopilot"})
                assert res.status_code == 409
                data = res.json()
                assert data["detail"]["code"] == "STALE_DATA"
                # Rejected call must not record audit entry
                assert mock_audit.await_count == 0
        finally:
            app.dependency_overrides.pop(get_session, None)


@pytest.mark.asyncio
async def test_approve_large_decision_awaits_second_approval(auth_headers, supervisor_headers):
    """Large allocations require four-eyes: operator then distinct supervisor."""
    mock_session = AsyncMock()

    with (
        patch("app.routers.decisions.fetch_decision_by_id", new_callable=AsyncMock) as mock_fetch,
        patch("app.routers.decisions._call_engine", new_callable=AsyncMock) as mock_call,
        patch("app.routers.decisions.insert_audit_entry", new_callable=AsyncMock) as mock_audit,
    ):
        # fetch is called twice per approve (cognitive pre-check + post-refresh)
        pending = {
            "id": 88,
            "status": "PENDING_APPROVAL",
            "lines": [{"id": 1, "quantity": 8000.0}],
            "confidence": 0.9,
            "degraded": False,
        }
        awaiting = {
            "id": 88,
            "status": "AWAITING_SECOND_APPROVAL",
            "lines": [{"id": 1, "quantity": 8000.0}],
            "confidence": 0.9,
            "degraded": False,
            "approved_by": "operator",
        }
        approved = {
            "id": 88,
            "status": "APPROVED",
            "lines": [{"id": 1, "quantity": 8000.0}],
            "confidence": 0.9,
            "degraded": False,
            "approved_by": "operator, supervisor",
        }
        mock_fetch.side_effect = [pending, awaiting, awaiting, approved]
        mock_call.side_effect = [
            {
                "status": "AWAITING_SECOND_APPROVAL",
                "decision_id": 88,
                "decision_status": "AWAITING_SECOND_APPROVAL",
                "approved_by": "operator",
            },
            {
                "status": "APPROVED",
                "decision_id": 88,
                "decision_status": "APPROVED",
                "approved_by": "operator, supervisor",
            },
        ]

        async def override_get_session():
            yield mock_session

        app.dependency_overrides[get_session] = override_get_session
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                res1 = await client.post(
                    "/api/decisions/88/approve",
                    headers=auth_headers,
                    json={"reviewed_driver": True},
                )
                assert res1.status_code == 200
                assert res1.json()["status"] == "AWAITING_SECOND_APPROVAL"

                res2 = await client.post(
                    "/api/decisions/88/approve",
                    headers=supervisor_headers,
                    json={"reviewed_driver": True},
                )
                assert res2.status_code == 200
                assert res2.json()["status"] == "APPROVED"
                assert mock_audit.await_count >= 2
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


@pytest.mark.asyncio
async def test_state_service_reconciles_pending_recommendations_and_filters_superseded_or_expired():
    """Verify get_current_state replaces any stale Redis pending recommendations with active unexpired DB decisions."""
    import orjson
    from app.services.state_service import get_current_state

    mock_redis = AsyncMock()
    # Stale Redis snapshot containing old superseded decision 1586
    stale_redis_state = {
        "sim_run_id": 1979,
        "tick": 10,
        "sim_status": "PAUSED",
        "pending_recommendations": [
            {
                "decision_id": 1586,
                "summary": "Old plan at tick 9",
                "confidence": 0.99,
                "total_liters": 8760.0,
                "expires_at_tick": 11,
            }
        ],
    }
    mock_redis.get.return_value = orjson.dumps(stale_redis_state)

    # Database source of truth has fresh active decision 1587
    active_db_recs = [
        {
            "id": 1587,
            "sim_run_id": 1979,
            "tick": 10,
            "status": "PENDING_APPROVAL",
            "narrative": "Fresh plan at tick 10",
            "confidence": 0.99,
            "lines": [{"quantity": 6690.0}],
            "expires_at_tick": 12,
        }
    ]

    mock_session = AsyncMock()
    with patch("app.services.state_service.fetch_recommendations", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = active_db_recs

        state, tick, is_degraded, etag = await get_current_state(mock_redis, mock_session)

        assert tick == 10
        assert state["sim_run_id"] == 1979
        # Verified: pending_recommendations now contains 1587, NOT the stale 1586
        assert len(state["pending_recommendations"]) == 1
        assert state["pending_recommendations"][0]["decision_id"] == 1587
        assert state["pending_recommendations"][0]["expires_at_tick"] == 12
        mock_fetch.assert_awaited_once_with(mock_session, sim_run_id=1979, current_tick=10)


@pytest.mark.asyncio
async def test_approve_decision_selected_lines_and_modified_quantities_aliases(auth_headers):
    """Verify DEF-002: ApproveRequest forwards selected_line_ids/modified_quantities and validates inputs."""
    mock_decision = {
        "id": 77,
        "tick": 12,
        "status": "PENDING_APPROVAL",
        "confidence": 0.95,
        "degraded": False,
        "lines": [
            {"id": 2862, "line_no": 1, "quantity": 6400.0},
            {"id": 2863, "line_no": 2, "quantity": 2470.0},
        ],
    }

    mock_session = AsyncMock()
    app.dependency_overrides[get_session] = lambda: mock_session
    try:
        with (
            patch("app.routers.decisions.fetch_decision_by_id", new_callable=AsyncMock) as mock_fetch,
            patch("app.routers.decisions._call_engine", new_callable=AsyncMock) as mock_engine,
            patch("app.routers.decisions.insert_audit_entry", new_callable=AsyncMock),
        ):
            mock_fetch.return_value = mock_decision
            mock_engine.return_value = {"status": "ok", "decision_status": "EXECUTED", "approved_by": "operator"}

            import httpx

            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                # 1. Selecting only line 2862 reduced to 1200L should NOT trigger F-TRUST >5000L cognitive forcing
                res_ok = await client.post(
                    "/api/decisions/77/approve",
                    headers=auth_headers,
                    json={
                        "selected_line_ids": [2862],
                        "modified_quantities": {"2862": 1200},
                    },
                )
                assert res_ok.status_code == 200, res_ok.text
                sent_payload = mock_engine.call_args.kwargs["json_data"]
                assert sent_payload["selected_line_ids"] == [2862]
                assert sent_payload["modified_quantities"] == {2862: 1200.0}
                assert sent_payload["operator"] == "operator"

                # 2. Empty selected_line_ids -> 422 NO_LINES_SELECTED
                res_empty = await client.post(
                    "/api/decisions/77/approve",
                    headers=auth_headers,
                    json={"selected_line_ids": []},
                )
                assert res_empty.status_code == 422
                assert res_empty.json()["detail"]["code"] == "NO_LINES_SELECTED"

                # 3. Negative / zero quantity -> 422 INVALID_QUANTITY
                res_neg = await client.post(
                    "/api/decisions/77/approve",
                    headers=auth_headers,
                    json={"line_ids": [2862], "quantities": {"2862": 0}},
                )
                assert res_neg.status_code == 422
                assert res_neg.json()["detail"]["code"] == "INVALID_QUANTITY"

                # 4. Non-existent line ID -> 422 INVALID_LINE_SELECTION
                res_bad_id = await client.post(
                    "/api/decisions/77/approve",
                    headers=auth_headers,
                    json={"selected_line_ids": [999999]},
                )
                assert res_bad_id.status_code == 422
                assert res_bad_id.json()["detail"]["code"] == "INVALID_LINE_SELECTION"
    finally:
        app.dependency_overrides.pop(get_session, None)


@pytest.mark.asyncio
async def test_whatif_invalid_payload_returns_422_not_500(auth_headers):
    """Verify DEF-007: invalid What-If payloads (negative horizon, unknown scenario_type/event) return 422."""
    import httpx

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for bad_payload in (
            {"scenario_type": "nonexistent_type", "horizon_ticks": -5},
            {"horizon_ticks": 0},
            {"horizon_ticks": 200},
            {"events": [{"type": "unknown_event", "start_offset": 0, "duration_ticks": 10}]},
            {"events": [{"type": "demand_spike", "start_offset": -1, "duration_ticks": 0}]},
        ):
            res = await client.post("/api/whatif", headers=auth_headers, json=bad_payload)
            assert res.status_code == 422, f"Expected 422 for {bad_payload}, got {res.status_code}: {res.text}"


@pytest.mark.asyncio
async def test_fetch_decisions_hydrates_lines_totals_and_outcomes():
    """Verify DEF-008: fetch_decisions attaches lines, total_liters, line_count, and outcome."""
    from unittest.mock import MagicMock

    from app.db import fetch_decisions

    count_res = MagicMock()
    count_res.scalar.return_value = 2

    dec_rows = [
        {
            "id": 1622,
            "sim_run_id": 2006,
            "tick": 12,
            "policy": "optimizer",
            "policy_version": "optimizer@1.0.0",
            "trigger": "auto",
            "status": "PENDING_APPROVAL",
            "mode": "advisory",
            "confidence": 0.99,
            "degraded": False,
            "fallback_reason": None,
            "objective": None,
            "impact": None,
            "explanation": {"headline": "7 lines"},
            "narrative": "Ship 10900 L",
            "approved_by": None,
            "approved_at": None,
            "rejected_reason": None,
            "expires_at_tick": 14,
            "created_at": "2026-11-26T00:00:00Z",
        },
        {
            "id": 1731,
            "sim_run_id": 2085,
            "tick": 2,
            "policy": "optimizer",
            "policy_version": "optimizer@1.0.0",
            "trigger": "interval",
            "status": "EXECUTED",
            "mode": "advisory",
            "confidence": 0.99,
            "degraded": False,
            "fallback_reason": None,
            "objective": None,
            "impact": None,
            "explanation": None,
            "narrative": "Partial",
            "approved_by": "operator",
            "approved_at": "2026-11-26T01:00:00Z",
            "rejected_reason": None,
            "expires_at_tick": 4,
            "created_at": "2026-11-26T01:00:00Z",
        },
    ]
    decs_res = MagicMock()
    decs_res.mappings.return_value.all.return_value = dec_rows

    line_rows = [
        {"id": 1, "decision_id": 1622, "line_no": 1, "quantity": 6000.0, "status": "PLANNED"},
        {"id": 2, "decision_id": 1622, "line_no": 2, "quantity": 4900.0, "status": "PLANNED"},
        {"id": 3, "decision_id": 1731, "line_no": 1, "quantity": 1500.0, "status": "SUBMITTED"},
        {"id": 4, "decision_id": 1731, "line_no": 2, "quantity": 3990.0, "status": "CANCELLED"},
    ]
    lines_res = MagicMock()
    lines_res.mappings.return_value.all.return_value = line_rows

    outcome_rows = [
        {"decision_id": 1731, "evaluated_tick": 6, "predicted": {}, "realised": {}, "hit": True},
    ]
    outcomes_res = MagicMock()
    outcomes_res.mappings.return_value.all.return_value = outcome_rows

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=[count_res, decs_res, lines_res, outcomes_res])

    items, total = await fetch_decisions(mock_session, sim_run_id=None, limit=50, offset=0)

    assert total == 2
    assert len(items) == 2
    assert items[0]["line_count"] == 2
    assert items[0]["active_line_count"] == 2
    assert items[0]["total_liters"] == 10900.0
    assert len(items[0]["lines"]) == 2
    assert items[0]["outcome"] is None

    # Partial execution sums active non-CANCELLED volume while preserving full audit lines
    assert items[1]["line_count"] == 2
    assert items[1]["active_line_count"] == 1
    assert items[1]["total_liters"] == 1500.0
    assert len(items[1]["lines"]) == 2
    assert items[1]["outcome"]["hit"] is True


@pytest.mark.asyncio
async def test_list_decisions_does_not_serve_stale_cached_status():
    """Verify sequential GET /api/decisions reflects updated DB state immediately without cross-worker stale cache."""
    import httpx

    first = (
        [{"id": 99, "sim_run_id": 10, "tick": 1, "status": "PENDING_APPROVAL", "lines": [], "total_liters": 5000.0}],
        1,
    )
    second = (
        [{"id": 99, "sim_run_id": 10, "tick": 1, "status": "EXECUTED", "lines": [], "total_liters": 1500.0}],
        1,
    )

    with patch("app.routers.decisions.fetch_decisions", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.side_effect = [first, second]
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            r1 = await client.get("/api/decisions?sim_run_id=10")
            r2 = await client.get("/api/decisions?sim_run_id=10")

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["items"][0]["status"] == "PENDING_APPROVAL"
    assert r2.json()["items"][0]["status"] == "EXECUTED"
    assert r2.json()["items"][0]["total_liters"] == 1500.0
    assert mock_fetch.await_count == 2



