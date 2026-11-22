"""Tests for /api/state endpoint (Redis caching, ETag, DB fallback)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import orjson
import pytest
from app.db import get_session
from app.main import app
from redis.exceptions import ConnectionError as RedisConnectionError


@pytest.fixture
def sample_state():
    return {
        "sim_run_id": 10,
        "tick": 45,
        "sim_time": "2026-01-01T11:15:00Z",
        "tick_minutes": 15,
        "sim_status": "RUNNING",
        "clock_label": "t=45",
        "generated_at": "2026-01-01T11:15:01Z",
        "age_seconds": 0.5,
        "stale": False,
        "degraded": False,
        "degraded_reasons": [],
        "engine_mode": "NORMAL",
        "operating_mode": "advisory",
        "metrics": {"service_level": 0.99},
        "depots": [{"id": "depot-1", "name": "Depot 1", "inventory": {"DIESEL": 50000}}],
        "stations": [{"id": "station-1", "name": "Station 1", "inventory": {"DIESEL": 12000}}],
        "routes": [],
        "events": [],
        "supply_arrivals_upcoming": [],
    }


@pytest.mark.asyncio
async def test_get_state_from_redis(sample_state):
    """Verify /api/state reads from Redis and sets headers and ETag."""
    mock_redis = MagicMock()
    mock_redis.get = AsyncMock(return_value=orjson.dumps(sample_state).decode())

    app.state.redis = mock_redis

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get("/api/state")
        assert res.status_code == 200
        assert res.headers["X-Data-Tick"] == "45"
        assert res.headers["X-Degraded"] == "false"
        assert res.headers["ETag"] == '"10-45"'

        data = res.json()
        assert data["tick"] == 45
        assert data["stations"][0]["id"] == "station-1"


@pytest.mark.asyncio
async def test_get_state_if_none_match_returns_304(sample_state):
    """Verify If-None-Match header returns 304 Not Modified when ETag matches."""
    mock_redis = MagicMock()
    mock_redis.get = AsyncMock(return_value=orjson.dumps(sample_state).decode())

    app.state.redis = mock_redis

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get("/api/state", headers={"If-None-Match": '"10-45"'})
        assert res.status_code == 304
        assert res.headers["ETag"] == '"10-45"'
        assert res.headers["X-Data-Tick"] == "45"
        assert res.content == b""


@pytest.mark.asyncio
async def test_get_state_redis_down_fallback_to_db(sample_state):
    """Verify /api/state falls back to TimescaleDB snapshot when Redis is down."""
    mock_redis = MagicMock()
    mock_redis.get = AsyncMock(side_effect=RedisConnectionError("Redis down"))
    app.state.redis = mock_redis

    db_row = {
        "id": 1,
        "sim_run_id": 10,
        "tick": 44,
        "sim_time": "2026-01-01T11:00:00Z",
        "depots": sample_state["depots"],
        "stations": sample_state["stations"],
        "routes": [],
        "events": [],
        "supply": [],
        "metrics": {"service_level": 0.98},
        "stale": False,
        "degraded": False,
        "created_at": "2026-01-01T11:00:01Z",
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
            res = await client.get("/api/state")
            assert res.status_code == 200
            assert res.headers["X-Data-Tick"] == "44"
            assert res.headers["X-Degraded"] == "true"
            assert res.headers["ETag"] == '"10-44"'

            data = res.json()
            assert data["tick"] == 44
            assert data["degraded"] is True
            assert "redis_down" in data["degraded_reasons"]
    finally:
        app.dependency_overrides.pop(get_session, None)
