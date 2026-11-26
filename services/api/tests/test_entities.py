"""Tests for /api/stations/{id} and /api/depots/{id} endpoints."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import orjson
import pytest
from app.db import get_session
from app.main import app


@pytest.fixture
def entity_state():
    return {
        "sim_run_id": 1,
        "tick": 20,
        "sim_time": "2026-01-01T05:00:00Z",
        "stations": [
            {
                "id": "station-banani",
                "name": "Banani Fuel Station",
                "region_id": "dhaka_north",
                "status": "OPEN",
                "inventory": {"DIESEL": 15000.0, "OCTANE": 8000.0},
            }
        ],
        "depots": [
            {
                "id": "depot-dhaka",
                "name": "Dhaka Main Depot",
                "region_id": "dhaka_central",
                "status": "OPEN",
                "inventory": {"DIESEL": 120000.0, "OCTANE": 60000.0},
            }
        ],
        "routes": [],
        "events": [],
        "supply_arrivals_upcoming": [],
        "degraded": False,
    }


@pytest.mark.asyncio
async def test_get_station_success(entity_state):
    """Verify /api/stations/{id} returns station details, inventory history, and demand."""
    mock_redis = MagicMock()
    mock_redis.get = AsyncMock(return_value=orjson.dumps(entity_state).decode())
    app.state.redis = mock_redis

    mock_session = AsyncMock()
    # Mock demand query
    mock_demand_result = MagicMock()
    mock_demand_result.mappings.return_value.all.return_value = [
        {
            "tick": 19,
            "sim_time": "2026-01-01T04:45:00Z",
            "station_id": "station-banani",
            "fuel_type": "DIESEL",
            "demand_liters": 500.0,
            "served_liters": 500.0,
            "unmet_liters": 0.0,
        }
    ]
    # Mock snapshots query
    mock_snap_result = MagicMock()
    mock_snap_result.mappings.return_value.all.return_value = [
        {
            "id": 1,
            "sim_run_id": 1,
            "tick": 19,
            "sim_time": "2026-01-01T04:45:00Z",
            "depots": [],
            "stations": entity_state["stations"],
        }
    ]
    mock_session.execute = AsyncMock(side_effect=[mock_demand_result, mock_snap_result])

    async def override_get_session():
        yield mock_session

    from unittest.mock import patch

    app.dependency_overrides[get_session] = override_get_session
    try:
        with (
            patch("app.services.state_service.fetch_recommendations", new=AsyncMock(return_value=[])),
            patch(
                "app.services.state_service.fetch_open_alert_counts",
                new=AsyncMock(return_value={"INFO": 0, "WARNING": 0, "CRITICAL": 0}),
            ),
        ):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                res = await client.get("/api/stations/station-banani")
                assert res.status_code == 200
                assert res.headers["X-Data-Tick"] == "20"
                data = res.json()
                assert data["station"]["id"] == "station-banani"
                assert len(data["demand"]) == 1
                assert data["demand"][0]["demand"] == 500.0
                assert len(data["history"]) == 1
                assert data["history"][0]["tick"] == 19
    finally:
        app.dependency_overrides.pop(get_session, None)


@pytest.mark.asyncio
async def test_get_station_not_found(entity_state):
    """Verify 404 is returned when station ID does not exist."""
    mock_redis = MagicMock()
    mock_redis.get = AsyncMock(return_value=orjson.dumps(entity_state).decode())
    app.state.redis = mock_redis

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get("/api/stations/non-existent-station")
        assert res.status_code == 404
        data = res.json()
        assert data["detail"]["code"] == "STATION_NOT_FOUND"


@pytest.mark.asyncio
async def test_get_depot_success(entity_state):
    """Verify /api/depots/{id} returns depot details and inventory history."""
    mock_redis = MagicMock()
    mock_redis.get = AsyncMock(return_value=orjson.dumps(entity_state).decode())
    app.state.redis = mock_redis

    mock_session = AsyncMock()
    mock_snap_result = MagicMock()
    mock_snap_result.mappings.return_value.all.return_value = [
        {
            "id": 1,
            "sim_run_id": 1,
            "tick": 19,
            "sim_time": "2026-01-01T04:45:00Z",
            "depots": entity_state["depots"],
            "stations": [],
        }
    ]
    mock_session.execute = AsyncMock(return_value=mock_snap_result)

    async def override_get_session():
        yield mock_session

    app.dependency_overrides[get_session] = override_get_session
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            res = await client.get("/api/depots/depot-dhaka")
            assert res.status_code == 200
            assert res.headers["X-Data-Tick"] == "20"
            data = res.json()
            assert data["depot"]["id"] == "depot-dhaka"
            assert len(data["history"]) == 1
    finally:
        app.dependency_overrides.pop(get_session, None)


@pytest.mark.asyncio
async def test_get_depot_not_found(entity_state):
    """Verify 404 is returned when depot ID does not exist."""
    mock_redis = MagicMock()
    mock_redis.get = AsyncMock(return_value=orjson.dumps(entity_state).decode())
    app.state.redis = mock_redis

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get("/api/depots/non-existent-depot")
        assert res.status_code == 404
        data = res.json()
        assert data["detail"]["code"] == "DEPOT_NOT_FOUND"
