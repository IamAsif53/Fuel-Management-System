"""Tests for Phase 09 Ops Assistant and Summary endpoints, and F-TRUST cognitive forcing."""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.db import get_session
from app.main import app
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def dummy_state() -> dict[str, Any]:
    return {
        "sim_run_id": 1,
        "tick": 140,
        "degraded": False,
        "engine_mode": "NORMAL",
        "policy": "optimizer",
        "metrics": {"service_level": 0.994, "unmet_liters": 150.0},
        "stations": [
            {
                "id": "station-coxsbazar",
                "name": "Cox's Bazar",
                "inventory": {"DIESEL": 2400.0, "PETROL": 1000.0},
                "region_id": "chittagong",
            },
            {
                "id": "station-mirpur",
                "name": "Mirpur",
                "inventory": {"DIESEL": 8500.0, "PETROL": 4000.0},
                "region_id": "dhaka",
            },
        ],
        "risks": [
            {
                "station_id": "station-coxsbazar",
                "fuel_type": "DIESEL",
                "level": "CRITICAL",
                "hours_to_stockout": 2.2,
                "stockout_prob_horizon": 0.88,
            }
        ],
        "depots": [],
        "routes": [],
        "events": [],
    }


@pytest.fixture(autouse=True)
def override_db_session():
    """Mock database session for all assistant API tests."""
    mock_session = AsyncMock()
    mock_res = MagicMock()
    mock_mappings = MagicMock()
    mock_mappings.all.return_value = []
    mock_res.mappings.return_value = mock_mappings
    mock_res.scalar.return_value = 45000.0
    mock_session.execute = AsyncMock(return_value=mock_res)

    async def _get_mock_session():
        yield mock_session

    app.dependency_overrides[get_session] = _get_mock_session
    yield mock_session
    app.dependency_overrides.pop(get_session, None)


@pytest.fixture(autouse=True)
def override_assistant_auth():
    """Authenticated viewer for assistant endpoints (DEF-020).

    Only override `require_viewer` so operator JWT flows (approve tests) still work.
    """
    from app.auth import require_viewer

    async def _viewer():
        return {
            "username": "viewer",
            "actor": "viewer",
            "role": "viewer",
            "jti": "test-jti",
            "exp": 9_999_999_999,
        }

    app.dependency_overrides[require_viewer] = _viewer
    yield
    app.dependency_overrides.pop(require_viewer, None)


@pytest.mark.asyncio
async def test_ask_assistant_open_mode_without_bearer():
    """Brief-aligned open mode: assistant works without Sign-In / bearer token."""
    from app.auth import require_viewer

    app.dependency_overrides.pop(require_viewer, None)
    try:
        with patch("app.routers.assistant.get_current_state", new=AsyncMock(return_value={
            "sim_run_id": 1,
            "tick": 10,
            "degraded": False,
            "engine_mode": "NORMAL",
            "metrics": {"service_level": 1.0, "unmet_liters": 0.0},
            "stations": [],
            "depots": [],
            "risks": [],
        })), patch("app.routers.assistant.fetch_alerts", new=AsyncMock(return_value=[])), \
             patch("app.routers.assistant.fetch_decisions", new=AsyncMock(return_value=[])), \
             patch("app.routers.assistant.fetch_incidents", new=AsyncMock(return_value=[])):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post("/api/assistant/ask", json={"question": "How is system health?"})
                assert resp.status_code == 200, resp.text
    finally:
        async def _viewer():
            return {
                "username": "viewer",
                "actor": "viewer",
                "role": "viewer",
                "jti": "test-jti",
                "exp": 9_999_999_999,
            }

        app.dependency_overrides[require_viewer] = _viewer


@pytest.mark.asyncio
async def test_ask_assistant_grounded_and_unsupported(dummy_state: dict[str, Any]):
    """Verify POST /api/assistant/ask returns grounded facts or 'not in current data'."""
    with patch("app.routers.assistant.get_current_state", new=AsyncMock(return_value=dummy_state)), \
         patch("app.routers.assistant.fetch_alerts", new=AsyncMock(return_value=[])), \
         patch("app.routers.assistant.fetch_decisions", new=AsyncMock(return_value=[])), \
         patch("app.routers.assistant.fetch_incidents", new=AsyncMock(return_value=[])):

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Grounded question
            resp = await client.post("/api/assistant/ask", json={"question": "Why is Cox's Bazar at risk?"})
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert data["engine"] in ["rules", "llm"]
            assert "station-coxsbazar" in data["answer"] or "CRITICAL" in data["answer"]
            assert any(s["id"] == "station-coxsbazar" for s in data["sources"])
            assert "generated_at" in data

            # 2. Unsupported question
            resp2 = await client.post("/api/assistant/ask", json={"question": "Tell me a joke about airplanes"})
            assert resp2.status_code == 200
            data2 = resp2.json()
            assert data2["answer"] == "not in current data"
            assert data2["sources"] == []


@pytest.mark.asyncio
async def test_ask_assistant_tuple_return_and_all_categories(dummy_state: dict[str, Any]):
    """Verify assistant handles tuple return from get_current_state and answers all operational categories."""
    mock_tuple = (dummy_state, 140, False, "W/etag-abc")
    mock_alerts = [
        {"id": 1, "station_id": "station-coxsbazar", "severity": "CRITICAL", "message": "Diesel stockout imminent"}
    ]
    mock_decisions = [
        {"id": 55, "tick": 140, "status": "APPROVED", "policy": "optimizer", "total_volume": 12000.0, "lines_count": 2}
    ]
    mock_incidents = [
        {"id": "inc-1", "title": "Highway N1 Disruption", "status": "ACTIVE"}
    ]

    with patch("app.routers.assistant.get_current_state", new=AsyncMock(return_value=mock_tuple)), \
         patch("app.routers.assistant.fetch_alerts", new=AsyncMock(return_value=mock_alerts)), \
         patch("app.routers.assistant.fetch_decisions", new=AsyncMock(return_value=mock_decisions)), \
         patch("app.routers.assistant.fetch_incidents", new=AsyncMock(return_value=mock_incidents)):

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Regression test: Cox's Bazar risk inquiry
            r_cox = await client.post("/api/assistant/ask", json={"question": "Why is Cox's Bazar at risk?"})
            assert r_cox.status_code == 200, r_cox.text
            d_cox = r_cox.json()
            assert d_cox["engine"] == "rules"
            assert "station-coxsbazar" in d_cox["answer"] or "CRITICAL" in d_cox["answer"]
            assert any(s["id"] == "station-coxsbazar" for s in d_cox["sources"])

            # 2. Alerts category
            r_alert = await client.post("/api/assistant/ask", json={"question": "Show me active shortage alerts"})
            assert r_alert.status_code == 200
            d_alert = r_alert.json()
            assert "Active alerts" in d_alert["answer"]

            # 3. Decisions / recommendations category
            r_dec = await client.post(
                "/api/assistant/ask", json={"question": "What is the latest dispatch recommendation?"}
            )
            assert r_dec.status_code == 200
            d_dec = r_dec.json()
            assert "55" in d_dec["answer"] or "APPROVED" in d_dec["answer"]

            # 4. System health category
            r_health = await client.post("/api/assistant/ask", json={"question": "How is system health right now?"})
            assert r_health.status_code == 200
            d_health = r_health.json()
            assert "Healthy" in d_health["answer"] or "NORMAL" in d_health["answer"]

            # 5. Shift summary inquiry
            r_sum = await client.post("/api/assistant/ask", json={"question": "Summarize the current shift"})
            assert r_sum.status_code == 200
            d_sum = r_sum.json()
            assert "service level" in d_sum["answer"].lower() or "99.40%" in d_sum["answer"]

            # 6. Inventory inquiry
            r_inv = await client.post("/api/assistant/ask", json={"question": "What is the current inventory?"})
            assert r_inv.status_code == 200
            d_inv = r_inv.json()
            assert "Network inventory summary" in d_inv["answer"]


@pytest.mark.asyncio
async def test_ask_assistant_llm_failure_fallback_to_rules(dummy_state: dict[str, Any]):
    """Verify that when external LLM raises an unexpected error or times out, assistant falls back to rules."""
    with patch("app.routers.assistant.get_current_state", new=AsyncMock(return_value=dummy_state)), \
         patch("app.routers.assistant.fetch_alerts", new=AsyncMock(return_value=[])), \
         patch("app.routers.assistant.fetch_decisions", new=AsyncMock(return_value=[])), \
         patch("app.routers.assistant.fetch_incidents", new=AsyncMock(return_value=[])), \
         patch("app.routers.assistant.LLMExplainer.answer_assistant", side_effect=RuntimeError("503 Provider Error")):

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/assistant/ask", json={"question": "Why is Cox's Bazar at risk?"})
            assert resp.status_code == 200, resp.text
            data = resp.json()
            # Must fall back gracefully to rules
            assert data["engine"] == "rules"
            assert "station-coxsbazar" in data["answer"] or "CRITICAL" in data["answer"]


@pytest.mark.asyncio
async def test_ask_assistant_validation():
    """Verify validation on empty or overly long questions."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Empty question
        resp1 = await client.post("/api/assistant/ask", json={"question": ""})
        assert resp1.status_code == 422

        # Too long question (> 500 chars)
        resp2 = await client.post("/api/assistant/ask", json={"question": "a" * 501})
        assert resp2.status_code == 422


@pytest.mark.asyncio
async def test_get_summary(dummy_state: dict[str, Any]):
    """Verify GET /api/summary returns text, engine, and stats."""
    with patch("app.routers.assistant.get_current_state", new=AsyncMock(return_value=dummy_state)), \
         patch("app.routers.assistant.fetch_incidents", new=AsyncMock(return_value=[])):

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/summary?window_ticks=96")
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert "text" in data
            assert data["engine"] in ["template", "llm"]
            assert "stats" in data
            assert data["stats"]["window_ticks"] == 96
            assert data["stats"]["service_level"] == 0.994


@pytest.mark.asyncio
async def test_cognitive_forcing_in_approval():
    """Verify F-TRUST cognitive forcing blocks approval of large line (>5000L) without reviewed_driver."""
    mock_decision = {
        "id": 42,
        "tick": 140,
        "status": "PENDING_APPROVAL",
        "confidence": 0.85,
        "degraded": False,
        "lines": [
            {"id": 101, "line_no": 1, "quantity": 6000.0, "fuel_type": "DIESEL"}
        ],
    }

    with patch("app.routers.decisions.fetch_decision_by_id", new=AsyncMock(return_value=mock_decision)), \
         patch("app.routers.decisions._call_engine", new=AsyncMock(return_value={"status": "ok"})):

        from app.auth import create_access_token
        from app.config import get_settings

        op_token, _ = create_access_token("operator", "operator", get_settings())
        op_headers = {"Authorization": f"Bearer {op_token}"}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Attempt approval without reviewed_driver -> 422 REVIEW_REQUIRED
            resp = await client.post(
                "/api/decisions/42/approve",
                headers=op_headers,
                json={"line_ids": [101]},
            )
            assert resp.status_code == 422, resp.text
            err = resp.json()
            detail = err.get("detail", err)
            assert detail.get("code") == "REVIEW_REQUIRED"

            # 2. Approval with reviewed_driver=True -> succeeds
            resp2 = await client.post(
                "/api/decisions/42/approve",
                headers=op_headers,
                json={"line_ids": [101], "reviewed_driver": True},
            )
            assert resp2.status_code == 200, resp2.text


@pytest.mark.asyncio
async def test_assistant_unpacks_tuples_scopes_active_run_and_reflects_degraded_engine_mode(
    dummy_state: dict[str, Any],
):
    """Verify DEF-011: assistant unpacks (items, total) tuples and reports Degraded when engine_mode != NORMAL."""
    degraded_state = {**dummy_state, "sim_run_id": 77, "engine_mode": "DEGRADED", "degraded": False}
    alerts_tuple = (
        [
            {
                "id": 91,
                "severity": "WARNING",
                "entity_type": "station",
                "entity_id": "station-mirpur",
                "fuel_type": "PETROL",
                "title": "Mirpur PETROL risk",
                "category": "STOCKOUT_RISK",
            }
        ],
        1,
    )
    decisions_tuple = (
        [
            {
                "id": 501,
                "status": "PENDING_APPROVAL",
                "policy": "optimizer",
                "confidence": 0.95,
                "explanation": {"headline": "Ship 4,000 L to Mirpur"},
            }
        ],
        1,
    )

    with (
        patch(
            "app.routers.assistant.get_current_state",
            new=AsyncMock(return_value=(degraded_state, 10, False, '"77-10"')),
        ),
        patch("app.routers.assistant.fetch_alerts", new=AsyncMock(return_value=alerts_tuple)) as mock_fa,
        patch("app.routers.assistant.fetch_decisions", new=AsyncMock(return_value=decisions_tuple)) as mock_fd,
        patch("app.routers.assistant.fetch_incidents", new=AsyncMock(return_value=[])),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r_alert = await client.post("/api/assistant/ask", json={"question": "What alerts are open?"})
            assert r_alert.status_code == 200
            d_alert = r_alert.json()
            assert "Mirpur PETROL risk" in d_alert["answer"]
            assert any(s["type"] == "alert" and s["id"] == "91" for s in d_alert["sources"])
            assert mock_fa.call_args.kwargs.get("sim_run_id") == 77

            r_dec = await client.post("/api/assistant/ask", json={"question": "What is the latest decision?"})
            assert r_dec.status_code == 200
            d_dec = r_dec.json()
            assert "#501" in d_dec["answer"]
            assert any(s["type"] == "decision" and s["id"] == "501" for s in d_dec["sources"])
            assert mock_fd.call_args.kwargs.get("sim_run_id") == 77

            r_health = await client.post("/api/assistant/ask", json={"question": "How is system health right now?"})
            assert r_health.status_code == 200
            d_health = r_health.json()
            assert "Degraded" in d_health["answer"]
            assert "Healthy" not in d_health["answer"]

