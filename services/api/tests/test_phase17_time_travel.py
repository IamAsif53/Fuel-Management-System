"""Tests for Phase 17 Simulation Time-Travel and Replay endpoints (/api/state/history, /api/state/history/ticks)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from app.db import get_session
from app.main import app


@pytest.fixture
def historical_snapshot():
    return {
        "id": 96,
        "sim_run_id": 101,
        "tick": 96,
        "sim_time": "2026-01-02T00:00:00Z",
        "tick_minutes": 15,
        "sim_status": "RUNNING",
        "stale": False,
        "degraded": False,
        "metrics": {"service_level": 0.995},
        "depots": [{"id": "depot-1", "name": "Depot 1", "inventory": {"DIESEL": 45000}}],
        "stations": [{"id": "station-1", "name": "Station 1", "inventory": {"DIESEL": 11000}}],
        "routes": [{"id": "r-1", "status": "AVAILABLE"}],
        "events": [],
        "supply": [],
    }


@pytest.mark.asyncio
async def test_get_available_ticks():
    """Verify /api/state/history/ticks returns available simulation ticks."""
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.mappings.return_value.all.return_value = [{"tick": 10}, {"tick": 20}, {"tick": 30}]
    mock_session.execute = AsyncMock(return_value=mock_result)

    async def override_get_session():
        yield mock_session

    app.dependency_overrides[get_session] = override_get_session
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            res = await client.get("/api/state/history/ticks")
            assert res.status_code == 200
            data = res.json()
            assert data["total"] == 3
            assert data["ticks"] == [10, 20, 30]
    finally:
        app.dependency_overrides.pop(get_session, None)


@pytest.mark.asyncio
async def test_get_state_history_success(historical_snapshot):
    """Verify /api/state/history?tick=96 returns snapshot with historical headers."""
    db_row = {
        "id": 96,
        "sim_run_id": 101,
        "tick": 96,
        "sim_time": "2026-01-02T00:00:00Z",
        "depots": historical_snapshot["depots"],
        "stations": historical_snapshot["stations"],
        "routes": historical_snapshot["routes"],
        "events": [],
        "supply": [],
        "metrics": {"service_level": 0.995},
        "stale": False,
        "degraded": False,
        "created_at": "2026-01-02T00:00:01Z",
    }

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.mappings.return_value.first.return_value = db_row
    mock_session.execute = AsyncMock(return_value=mock_result)

    async def override_get_session():
        yield mock_session

    app.dependency_overrides[get_session] = override_get_session
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            res = await client.get("/api/state/history?tick=96")
            assert res.status_code == 200
            assert res.headers["X-Data-Tick"] == "96"
            assert res.headers["X-Historical-Replay"] == "true"
            data = res.json()
            assert data["tick"] == 96
            assert data["stations"][0]["id"] == "station-1"
    finally:
        app.dependency_overrides.pop(get_session, None)


@pytest.mark.asyncio
async def test_get_state_history_not_found():
    """Verify /api/state/history returns 404 for non-existent tick."""
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.mappings.return_value.first.return_value = None
    mock_session.execute = AsyncMock(return_value=mock_result)

    async def override_get_session():
        yield mock_session

    app.dependency_overrides[get_session] = override_get_session
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            res = await client.get("/api/state/history?tick=999999")
            assert res.status_code == 404
            data = res.json()
            assert data["detail"]["code"] == "SNAPSHOT_NOT_FOUND"
    finally:
        app.dependency_overrides.pop(get_session, None)
