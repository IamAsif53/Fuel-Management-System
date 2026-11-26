"""Tests for Redis state and domain event publisher (Task 2.5)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import orjson
import pytest
import redis.asyncio as aioredis
from app.sim.client import SimResult
from app.state.publisher import FSIP_REDIS_ERRORS_TOTAL, Publisher
from app.state.store import WorldState
from app.state.sync import SyncLoop
from redis.exceptions import ConnectionError as RedisConnectionError


@pytest.fixture
def sample_store():
    """Create a populated WorldState instance for testing."""
    store = WorldState(engine_mode="NORMAL", operating_mode="advisory", policy="optimizer")
    store.sim_run_id = 42
    store.last_good_at["instance"] = 1000.0
    store.last_good_at["depots"] = 1000.0
    store.last_good_at["stations"] = 1000.0
    store.last_good_at["routes"] = 1000.0
    return store


@pytest.mark.asyncio
async def test_publisher_publish_state_pipeline(sample_store):
    """Verify that publish_state sets fsip:state, fsip:state:tick, fsip:health, and adds to stream."""
    mock_redis = MagicMock(spec=aioredis.Redis)
    mock_pipe = MagicMock()
    mock_pipe.set = MagicMock()
    mock_pipe.hset = MagicMock()
    mock_pipe.xadd = MagicMock()
    mock_pipe.execute = AsyncMock(return_value=[True, True, 1, "1732332800000-0"])

    # Setup pipeline context manager
    mock_redis.pipeline.return_value.__aenter__ = AsyncMock(return_value=mock_pipe)
    mock_redis.pipeline.return_value.__aexit__ = AsyncMock(return_value=None)

    publisher = Publisher(redis_client=mock_redis)
    await publisher.publish_state(sample_store)

    mock_redis.pipeline.assert_called_once_with(transaction=False)
    mock_pipe.set.assert_any_call("fsip:state", unittest_matcher(lambda s: "sim_run_id" in s))
    mock_pipe.set.assert_any_call("fsip:state:tick", str(sample_store.tick))
    mock_pipe.hset.assert_called_once_with("fsip:health", "engine", unittest_matcher(lambda h: "engine_mode" in h))

    mock_pipe.xadd.assert_called_once()
    args, kwargs = mock_pipe.xadd.call_args
    assert args[0] == "fsip:domain-events"
    entry = args[1]
    assert entry["type"] == "state.updated"
    assert entry["tick"] == str(sample_store.tick)
    assert entry["sim_run_id"] == "42"
    assert entry["etag"] == f"42-{sample_store.tick}"
    assert kwargs.get("maxlen") == 20000
    assert kwargs.get("approximate") is True
    mock_pipe.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_publisher_custom_event():
    """Verify that event() appends a custom event to the domain stream."""
    mock_redis = MagicMock(spec=aioredis.Redis)
    mock_redis.xadd = AsyncMock(return_value="1732332800000-1")

    publisher = Publisher(redis_client=mock_redis)
    stream_id = await publisher.event("alert.opened", {"id": "alt-1", "severity": "CRITICAL"}, sim_run_id=5, tick=12)

    assert stream_id == "1732332800000-1"
    mock_redis.xadd.assert_awaited_once()
    args, kwargs = mock_redis.xadd.call_args
    assert args[0] == "fsip:domain-events"
    entry = args[1]
    assert entry["type"] == "alert.opened"
    assert entry["sim_run_id"] == "5"
    assert entry["tick"] == "12"
    parsed_data = orjson.loads(entry["data"])
    assert parsed_data["id"] == "alt-1"
    assert parsed_data["severity"] == "CRITICAL"


@pytest.mark.asyncio
async def test_publisher_handles_redis_connection_error_without_raising(sample_store):
    """Verify that Redis failure is isolated and increments error metric."""
    mock_redis = MagicMock(spec=aioredis.Redis)
    mock_redis.pipeline.side_effect = RedisConnectionError("Connection refused")

    initial_errors = FSIP_REDIS_ERRORS_TOTAL._value.get() if FSIP_REDIS_ERRORS_TOTAL else 0

    publisher = Publisher(redis_client=mock_redis)
    # Must not raise
    await publisher.publish_state(sample_store)

    if FSIP_REDIS_ERRORS_TOTAL:
        assert FSIP_REDIS_ERRORS_TOTAL._value.get() >= initial_errors + 1


@pytest.mark.asyncio
async def test_publisher_event_handles_redis_error_without_raising():
    """Verify that event() failure returns empty string and increments error metric."""
    mock_redis = MagicMock(spec=aioredis.Redis)
    mock_redis.xadd = AsyncMock(side_effect=RedisConnectionError("Connection timeout"))

    initial_errors = FSIP_REDIS_ERRORS_TOTAL._value.get() if FSIP_REDIS_ERRORS_TOTAL else 0

    publisher = Publisher(redis_client=mock_redis)
    res = await publisher.event("test.event", {"k": "v"})
    assert res == ""

    if FSIP_REDIS_ERRORS_TOTAL:
        assert FSIP_REDIS_ERRORS_TOTAL._value.get() >= initial_errors + 1


@pytest.mark.asyncio
async def test_sync_loop_invokes_publisher(sample_store):
    """Verify that SyncLoop.full_sync() invokes publisher.publish_state."""
    mock_client = MagicMock()
    res = SimResult(
        data={
            "id": 1,
            "scenario_id": "default",
            "scenario_version": "1.0",
            "seed": 42,
            "status": "RUNNING",
            "tick": 10,
            "tick_minutes": 15,
            "sim_time": "2026-01-01T02:30:00Z",
        },
        stale=False,
        status=200,
        latency_ms=1.0,
    )
    empty_res = SimResult(data=[], stale=False, status=200, latency_ms=1.0)
    metrics_res = SimResult(
        data={
            "served_demand_liters": 0.0,
            "unmet_demand_liters": 0.0,
            "service_level": 1.0,
            "allocation_liters": 0.0,
            "allocation_failures": 0,
        },
        stale=False,
        status=200,
        latency_ms=1.0,
    )
    mock_client.instance = AsyncMock(return_value=res)
    mock_client.regions = AsyncMock(return_value=empty_res)
    mock_client.depots = AsyncMock(return_value=empty_res)
    mock_client.stations = AsyncMock(return_value=empty_res)
    mock_client.routes = AsyncMock(return_value=empty_res)
    mock_client.events = AsyncMock(return_value=empty_res)
    mock_client.supply_arrivals = AsyncMock(return_value=empty_res)
    mock_client.allocations = AsyncMock(return_value=empty_res)
    mock_client.metrics = AsyncMock(return_value=metrics_res)
    mock_client.demand_history = AsyncMock(return_value=empty_res)

    mock_publisher = MagicMock(spec=Publisher)
    mock_publisher.publish_state = AsyncMock()

    sync_loop = SyncLoop(
        client=mock_client,
        store=sample_store,
        publisher=mock_publisher,
    )

    await sync_loop.full_sync()
    # full_sync publishes twice by design: once after persistence (pre-hooks) and
    # once after lifecycle hooks so hook mutations (risk/alerts/mode) are visible.
    assert mock_publisher.publish_state.await_count == 2
    mock_publisher.publish_state.assert_awaited_with(sample_store)


@pytest.mark.asyncio
async def test_live_redis_publisher_integration(sample_store):
    """Live integration test against running Redis container on port 16379."""
    redis_url = "redis://localhost:16379/0"
    try:
        r = aioredis.from_url(redis_url, decode_responses=True)
        await r.ping()
    except Exception:
        pytest.skip("Live Redis container not reachable at localhost:16379")

    try:
        publisher = Publisher(
            redis_client=r,
            stream_name="test:fsip:domain-events",
            state_key="test:fsip:state",
            tick_key="test:fsip:state:tick",
            health_key="test:fsip:health",
        )

        sample_store.sim_run_id = 99
        await publisher.publish_state(sample_store)

        state_val = await r.get("test:fsip:state")
        assert state_val is not None
        parsed_state = orjson.loads(state_val)
        assert parsed_state["sim_run_id"] == 99

        tick_val = await r.get("test:fsip:state:tick")
        assert tick_val == str(sample_store.tick)

        health_val = await r.hget("test:fsip:health", "engine")
        assert health_val is not None
        parsed_health = orjson.loads(health_val)
        assert parsed_health["sim_run_id"] == 99

        entries = await r.xrange("test:fsip:domain-events", "-", "+")
        assert len(entries) >= 1
        entry_id, entry_data = entries[-1]
        assert entry_data["type"] == "state.updated"
        assert entry_data["etag"] == f"99-{sample_store.tick}"

        # Test custom event
        custom_id = await publisher.event("mode.changed", {"engine_mode": "DEGRADED"}, sim_run_id=99, tick=10)
        assert custom_id != ""

        entries = await r.xrange("test:fsip:domain-events", "-", "+")
        assert len(entries) >= 2
        last_id, last_data = entries[-1]
        assert last_data["type"] == "mode.changed"
    finally:
        await r.delete("test:fsip:state", "test:fsip:state:tick", "test:fsip:health", "test:fsip:domain-events")
        await r.aclose()


class unittest_matcher:
    """Helper to match argument predicates in mock assertions."""

    def __init__(self, predicate):
        self.predicate = predicate

    def __eq__(self, other):
        return self.predicate(other)
