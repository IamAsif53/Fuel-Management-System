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

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Attempt approval without reviewed_driver -> 422 REVIEW_REQUIRED
            resp = await client.post(
                "/api/decisions/42/approve",
                headers={"Authorization": "Bearer change-me-operator"},
                json={"line_ids": [101]},
            )
            assert resp.status_code == 422, resp.text
            err = resp.json()
            detail = err.get("detail", err)
            assert detail.get("code") == "REVIEW_REQUIRED"

            # 2. Approval with reviewed_driver=True -> succeeds
            resp2 = await client.post(
                "/api/decisions/42/approve",
                headers={"Authorization": "Bearer change-me-operator"},
                json={"line_ids": [101], "reviewed_driver": True},
            )
            assert resp2.status_code == 200, resp2.text
