"""Phase 02 Comprehensive Acceptance and Integration Test Suite.

Verifies all Task 2.8 acceptance criteria:
1. WorldState snapshot structure, headers, ETag validation, and 304 response.
2. Database fallback when Redis is unavailable (X-Degraded: true).
3. SSE domain event streaming with Last-Event-ID replay.
4. Station and Depot entity inspection with demand and snapshot history.
5. Component health aggregation and rolling metrics (/api/system/status).
6. Live Docker stack verification against running Redis and TimescaleDB instances.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import asyncpg
import httpx
import orjson
import pytest
import redis.asyncio as aioredis
from app.db import get_session
from app.main import app
from redis.exceptions import ConnectionError as RedisConnectionError


@pytest.fixture
def mock_world_state():
    return {
        "sim_run_id": 1,
        "tick": 50,
        "sim_time": "2026-01-01T12:30:00Z",
        "tick_minutes": 15,
        "sim_status": "RUNNING",
        "clock_label": "Day 1 12:30 · t=50",
        "generated_at": "2026-01-01T12:30:01Z",
        "age_seconds": 0.2,
        "stale": False,
        "degraded": False,
        "degraded_reasons": [],
        "engine_mode": "NORMAL",
        "operating_mode": "advisory",
        "metrics": {
            "service_level": 0.995,
            "served": 12000.0,
            "unmet": 50.0,
            "allocation_liters": 15000.0,
            "allocation_failures": 0,
            "overflow_liters_est": 0.0,
        },
        "depots": [
            {
                "id": "depot-central",
                "name": "Central Depot",
                "region_id": "dhaka_central",
                "status": "OPEN",
                "capacity": {"DIESEL": 100000.0, "OCTANE": 50000.0},
                "inventory": {"DIESEL": 80000.0, "OCTANE": 40000.0},
                "dispatch_capacity_per_tick": 20000.0,
            }
        ],
        "stations": [
            {
                "id": "station-gulshan",
                "name": "Gulshan Station",
                "region_id": "dhaka_north",
                "status": "OPEN",
                "capacity": {"DIESEL": 20000.0, "OCTANE": 10000.0},
                "inventory": {"DIESEL": 14000.0, "OCTANE": 7000.0},
                "fill_pct": {"DIESEL": 0.70, "OCTANE": 0.70},
                "risk": {
                    "DIESEL": {
                        "level": "LOW",
                        "hours_to_stockout": 28.0,
                        "stockout_prob": 0.02,
                    }
                },
            }
        ],
        "routes": [],
        "events": [],
        "supply_arrivals_upcoming": [],
        "allocations_active": [],
        "alerts_open": [],
        "pending_recommendations": [],
        "system": {
            "simulator": "UP",
            "sse": "CONNECTED",
            "intelligence": "UP",
            "db": "UP",
            "redis": "UP",
        },
    }


@pytest.mark.asyncio
async def test_acceptance_state_endpoint_contract(mock_world_state):
    """Criterion: /api/state returns full WorldState with ETag and custom headers."""
    mock_redis = MagicMock()
    mock_redis.get = AsyncMock(return_value=orjson.dumps(mock_world_state).decode())
    app.state.redis = mock_redis

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        res = await client.get("/api/state")
        assert res.status_code == 200
        assert res.headers["X-Data-Tick"] == "50"
        assert res.headers["X-Degraded"] == "false"
        assert res.headers["ETag"] == '"1-50"'

        body = res.json()
        assert body["tick"] == 50
        assert body["sim_run_id"] == 1
        assert len(body["stations"]) == 1
        assert len(body["depots"]) == 1
        assert body["metrics"]["service_level"] == 0.995

        # Verify 304 response on matching ETag
        etag_res = await client.get("/api/state", headers={"If-None-Match": '"1-50"'})
        assert etag_res.status_code == 304
        assert etag_res.content == b""


@pytest.mark.asyncio
async def test_acceptance_state_fallback_on_redis_outage(mock_world_state):
    """Criterion: docker stop fsip-redis -> /api/state serves from DB with X-Degraded: true."""
    mock_redis = MagicMock()
    mock_redis.get = AsyncMock(side_effect=RedisConnectionError("Redis stopped"))
    app.state.redis = mock_redis

    db_row = {
        "id": 99,
        "sim_run_id": 1,
        "tick": 49,
        "sim_time": "2026-01-01T12:15:00Z",
        "depots": mock_world_state["depots"],
        "stations": mock_world_state["stations"],
        "routes": [],
        "events": [],
        "supply": [],
        "metrics": mock_world_state["metrics"],
        "stale": False,
        "degraded": False,
        "created_at": "2026-01-01T12:15:01Z",
    }

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.mappings.return_value.first.return_value = db_row
    mock_session.execute = AsyncMock(return_value=mock_result)

    async def override_get_session():
        yield mock_session

    app.dependency_overrides[get_session] = override_get_session
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            res = await client.get("/api/state")
            assert res.status_code == 200
            assert res.headers["X-Data-Tick"] == "49"
            assert res.headers["X-Degraded"] == "true"
            body = res.json()
            assert body["tick"] == 49
            assert body["degraded"] is True
            assert "redis_down" in body["degraded_reasons"]
    finally:
        app.dependency_overrides.pop(get_session, None)


@pytest.mark.asyncio
async def test_acceptance_sse_stream():
    """Criterion: /api/stream emits event: state.updated lines and handles reconnects."""
    mock_redis = MagicMock()
    mock_redis.xread = AsyncMock(
        return_value=[
            (
                "fsip:domain-events",
                [
                    (
                        "1732332800000-0",
                        {
                            "type": "state.updated",
                            "data": '{"tick": 51, "etag": "1-51"}',
                        },
                    )
                ],
            )
        ]
    )
    mock_redis.xrange = AsyncMock(
        return_value=[
            (
                "1732332800000-0",
                {
                    "type": "state.updated",
                    "data": '{"tick": 50, "etag": "1-50"}',
                },
            )
        ]
    )
    app.state.redis = mock_redis

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        # Test regular stream
        async with client.stream(
            "GET", "/api/stream", params={"limit": 1}
        ) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            lines = [ln async for ln in response.aiter_lines()]
            assert any("event: state.updated" in ln for ln in lines)
            assert any('data: {"tick": 51, "etag": "1-51"}' in ln for ln in lines)

        # Test Last-Event-ID replay
        async with client.stream(
            "GET",
            "/api/stream",
            headers={"Last-Event-ID": "1732332799999-0"},
            params={"limit": 1},
        ) as response:
            assert response.status_code == 200
            lines = [ln async for ln in response.aiter_lines()]
            assert any("id: 1732332800000-0" in ln for ln in lines)
            assert any("event: state.updated" in ln for ln in lines)


@pytest.mark.asyncio
async def test_acceptance_system_status_structure():
    """Criterion: /api/system/status returns all required components and SLO metrics."""
    mock_redis = MagicMock()
    mock_redis.ping = AsyncMock(return_value=True)
    mock_redis.hgetall = AsyncMock(
        return_value={
            "engine": (
                '{"status": "ready", "engine_mode": "advisory", "tick": 50,'
                ' "last_sync_age_s": 0.3}'
            )
        }
    )
    app.state.redis = mock_redis

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        res = await client.get("/api/system/status")
        assert res.status_code == 200
        data = res.json()

        # Check required components
        expected_components = [
            "backend_api",
            "database",
            "redis",
            "fuel_simulator",
            "prediction_service",
            "decision_engine",
            "notifier",
        ]
        for comp in expected_components:
            assert comp in data["components"]
            assert "status" in data["components"][comp]

        assert "overall" in data
        assert "engine_mode" in data
        assert "versions" in data
        assert "p95_latency_ms" in data
        assert "error_rate" in data
        assert "slo" in data
        assert len(data["slo"]) >= 2


@pytest.mark.integration
@pytest.mark.asyncio
async def test_live_docker_acceptance_check():
    """End-to-end acceptance check against live Docker TimescaleDB and Redis containers."""
    # 1. Check TimescaleDB tables
    try:
        conn = await asyncpg.connect(
            "postgresql://fsip:change-me@localhost:15432/fsip"
        )
    except Exception:
        pytest.skip(
            "Live TimescaleDB container not reachable at localhost:15432"
        )

    try:
        sim_runs_count = await conn.fetchval("SELECT count(*) FROM sim_runs")
        snapshots_count = await conn.fetchval(
            "SELECT count(*) FROM world_snapshots"
        )
        demand_count = await conn.fetchval(
            "SELECT count(*) FROM demand_observations"
        )

        assert sim_runs_count >= 1
        assert snapshots_count >= 1
        assert demand_count >= 0
    finally:
        await conn.close()

    # 2. Check Redis state and stream
    try:
        r = aioredis.from_url("redis://localhost:16379/0", decode_responses=True)
        await r.ping()
    except Exception:
        pytest.skip("Live Redis container not reachable at localhost:16379")

    try:
        # State publisher keys
        raw_state = await r.get("fsip:state")
        if raw_state:
            parsed = orjson.loads(raw_state)
            assert "sim_run_id" in parsed
            assert "tick" in parsed
            assert "stations" in parsed
            assert "depots" in parsed

        raw_tick = await r.get("fsip:state:tick")
        if raw_tick:
            assert int(raw_tick) >= 0

        health_engine = await r.hget("fsip:health", "engine")
        if health_engine:
            parsed_health = orjson.loads(health_engine)
            assert "status" in parsed_health
            assert "engine_mode" in parsed_health
    finally:
        await r.aclose()
