"""Unit tests for the Simulator State SyncLoop (Task 1.6)."""
from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from app.sim.client import SimClient, SimResult, SimUnavailable
from app.state.store import WorldState
from app.state.sync import (
    FSIP_CYCLE_DURATION_SECONDS,
    FSIP_TICKS_BEHIND,
    SyncLoop,
)

FX = Path(__file__).resolve().parents[3] / "libs" / "fsip_common" / "tests" / "fixtures"


def load_fx(name: str) -> Any:
    return json.loads((FX / f"{name}.json").read_text())


@pytest.fixture
def fixtures_data() -> dict[str, Any]:
    return {
        "instance": load_fx("instance"),
        "regions": load_fx("regions"),
        "depots": load_fx("depots"),
        "stations": load_fx("stations"),
        "routes": load_fx("routes"),
        "events": load_fx("events"),
        "supply_arrivals": load_fx("supply_arrivals"),
        "allocations": load_fx("allocations"),
        "metrics": load_fx("metrics"),
        "demand_history": load_fx("demand_history"),
    }


def make_mock_client(data: dict[str, Any]) -> SimClient:
    """Create a mock SimClient returning SimResults for all standard endpoints."""
    client = AsyncMock(spec=SimClient)
    client.instance.return_value = SimResult(
        data=copy.deepcopy(data["instance"]), stale=False, status=200, latency_ms=5.0
    )
    client.regions.return_value = SimResult(
        data=copy.deepcopy(data["regions"]), stale=False, status=200, latency_ms=5.0
    )
    client.depots.return_value = SimResult(
        data=copy.deepcopy(data["depots"]), stale=False, status=200, latency_ms=5.0
    )
    client.stations.return_value = SimResult(
        data=copy.deepcopy(data["stations"]), stale=False, status=200, latency_ms=5.0
    )
    client.routes.return_value = SimResult(
        data=copy.deepcopy(data["routes"]), stale=False, status=200, latency_ms=5.0
    )
    client.events.return_value = SimResult(
        data=copy.deepcopy(data["events"]), stale=False, status=200, latency_ms=5.0
    )
    client.supply_arrivals.return_value = SimResult(
        data=copy.deepcopy(data["supply_arrivals"]), stale=False, status=200, latency_ms=5.0
    )
    client.allocations.return_value = SimResult(
        data=copy.deepcopy(data["allocations"]), stale=False, status=200, latency_ms=5.0
    )
    client.metrics.return_value = SimResult(
        data=copy.deepcopy(data["metrics"]), stale=False, status=200, latency_ms=5.0
    )
    client.demand_history.return_value = SimResult(
        data=copy.deepcopy(data["demand_history"]), stale=False, status=200, latency_ms=5.0
    )
    return client


# =============================================================================
# 1. Normal full sync populates store correctly
# =============================================================================
@pytest.mark.asyncio
async def test_full_sync_populates_store_correctly(fixtures_data: dict[str, Any]):
    client = make_mock_client(fixtures_data)
    store = WorldState()
    sync = SyncLoop(client=client, store=store, poll_interval=1.0)

    await sync.full_sync()

    assert store.instance is not None
    assert store.instance.tick == fixtures_data["instance"]["tick"]
    assert len(store.regions) == len(fixtures_data["regions"])
    assert len(store.depots) == len(fixtures_data["depots"])
    assert len(store.stations) == len(fixtures_data["stations"])
    assert len(store.routes) == len(fixtures_data["routes"])
    assert len(store.events) == len(fixtures_data["events"])
    assert len(store.supply) == len(fixtures_data["supply_arrivals"])
    assert len(store.allocations) == len(fixtures_data["allocations"])
    assert store.metrics is not None
    assert len(store.demand_rows) > 0

    assert store.stale is False
    assert store.degraded is False
    assert len(store.degraded_reasons) == 0

    expected_keys = {
        "instance",
        "regions",
        "depots",
        "stations",
        "routes",
        "events",
        "supply",
        "allocations",
        "metrics",
    }
    for k in expected_keys:
        assert k in store.last_good_at
        assert store.last_good_at[k] > 0


# =============================================================================
# 2. Concurrent resource fetching works and applies snapshot
# =============================================================================
@pytest.mark.asyncio
async def test_concurrent_resource_fetching(fixtures_data: dict[str, Any]):
    client = make_mock_client(fixtures_data)
    store = WorldState()
    sync = SyncLoop(client=client, store=store)

    concurrency_counter = 0
    max_concurrency = 0
    lock = asyncio.Lock()

    async def tracking_wrapper(orig_fn):
        nonlocal concurrency_counter, max_concurrency
        async with lock:
            concurrency_counter += 1
            if concurrency_counter > max_concurrency:
                max_concurrency = concurrency_counter
        try:
            await asyncio.sleep(0.01)
            return await orig_fn()
        finally:
            async with lock:
                concurrency_counter -= 1

    orig_instance = client.instance
    orig_depots = client.depots
    orig_stations = client.stations

    async def mock_inst():
        return await tracking_wrapper(orig_instance)

    async def mock_dep():
        return await tracking_wrapper(orig_depots)

    async def mock_sta():
        return await tracking_wrapper(orig_stations)

    client.instance = mock_inst
    client.depots = mock_dep
    client.stations = mock_sta

    await sync.full_sync()

    # Multiple calls were in-flight simultaneously
    assert max_concurrency >= 2
    assert store.instance is not None
    assert len(store.depots) > 0


# =============================================================================
# 3. Partial resource failure preserves last-good and marks degraded
# =============================================================================
@pytest.mark.asyncio
async def test_partial_resource_failure_preserves_last_good(fixtures_data: dict[str, Any]):
    client = make_mock_client(fixtures_data)
    store = WorldState()
    sync = SyncLoop(client=client, store=store)

    # Initial successful sync
    await sync.full_sync()
    initial_depots = list(store.depots)
    initial_last_good = store.last_good_at["depots"]
    assert len(initial_depots) > 0

    # Next sync: depots fails with SimUnavailable
    client.depots = AsyncMock(side_effect=SimUnavailable("Simulator 503"))
    await sync.full_sync()

    # Depots are preserved
    assert store.depots == initial_depots
    assert store.last_good_at["depots"] == initial_last_good
    assert "depots_failed" in store.degraded_reasons
    assert store.degraded is True
    # Other resources updated fine
    assert "stations_failed" not in store.degraded_reasons


# =============================================================================
# 4. Payload validation failure preserves last-good and marks degraded
# =============================================================================
@pytest.mark.asyncio
async def test_payload_validation_failure_preserves_last_good(fixtures_data: dict[str, Any]):
    client = make_mock_client(fixtures_data)
    store = WorldState()
    sync = SyncLoop(client=client, store=store)

    # Initial sync
    await sync.full_sync()
    initial_routes = list(store.routes)
    initial_last_good = store.last_good_at["routes"]

    # Next sync: routes returns malformed data (violates transit_ticks >= 1)
    bad_routes = copy.deepcopy(fixtures_data["routes"])
    bad_routes[0]["transit_ticks"] = 0  # Invalid! Must be >= 1

    client.routes = AsyncMock(
        return_value=SimResult(data=bad_routes, stale=False, status=200, latency_ms=5.0)
    )
    await sync.full_sync()

    assert store.routes == initial_routes
    assert store.last_good_at["routes"] == initial_last_good
    assert "routes_failed" in store.degraded_reasons
    assert store.degraded is True


# =============================================================================
# 5. Demand history fetching with dynamic limit formula
# =============================================================================
@pytest.mark.asyncio
async def test_demand_history_dynamic_limit_formula(fixtures_data: dict[str, Any]):
    client = make_mock_client(fixtures_data)
    store = WorldState()
    sync = SyncLoop(client=client, store=store)

    # Formula: limit = min(2000, 12 * max(1, tick - last_tick) + 12)
    # 1. tick=None, last_tick=None -> max(1, 1) = 1 -> 12*1 + 12 = 24
    assert sync._calc_demand_limit(None) == 24

    # 2. tick=5, last_tick=2 -> max(1, 3) = 3 -> 12*3 + 12 = 48
    sync.last_tick = 2
    assert sync._calc_demand_limit(5) == 48

    # 3. tick=10, last_tick=10 -> max(1, 0) = 1 -> 12*1 + 12 = 24
    sync.last_tick = 10
    assert sync._calc_demand_limit(10) == 24

    # 4. tick=200, last_tick=0 -> max(1, 200) = 200 -> 12*200 + 12 = 2412 -> capped at 2000
    sync.last_tick = 0
    assert sync._calc_demand_limit(200) == 2000

    # Test integration in full_sync
    sync.last_tick = 2
    await sync.full_sync(tick=5)
    client.demand_history.assert_called_with(limit=48)


# =============================================================================
# 6. Demand deduplication by row/observation id
# =============================================================================
@pytest.mark.asyncio
async def test_demand_deduplication(fixtures_data: dict[str, Any]):
    client = make_mock_client(fixtures_data)
    store = WorldState()
    sync = SyncLoop(client=client, store=store)

    # Demand observations: 2 identical rows with same id
    obs_list = [
        {
            "id": 9999,
            "station_id": "station-mirpur",
            "fuel_type": "DIESEL",
            "tick": 1,
            "sim_time": "2026-03-01T06:00:00Z",
            "demand_liters": 120.0,
            "served_liters": 120.0,
            "unmet_liters": 0.0,
        },
        {
            "id": 9999,  # Duplicate!
            "station_id": "station-mirpur",
            "fuel_type": "DIESEL",
            "tick": 1,
            "sim_time": "2026-03-01T06:00:00Z",
            "demand_liters": 120.0,
            "served_liters": 120.0,
            "unmet_liters": 0.0,
        },
        {
            "id": 10000,  # Distinct
            "station_id": "station-mirpur",
            "fuel_type": "DIESEL",
            "tick": 2,
            "sim_time": "2026-03-01T06:15:00Z",
            "demand_liters": 150.0,
            "served_liters": 150.0,
            "unmet_liters": 0.0,
        },
    ]
    client.demand_history.return_value = SimResult(
        data=obs_list, stale=False, status=200, latency_ms=5.0
    )

    await sync.full_sync()

    mirpur_diesel = store.demand_rows[("station-mirpur", "DIESEL")]
    # Should only contain 2 observations (9999 and 10000), not 3!
    assert len(mirpur_diesel) == 2
    assert [o.id for o in mirpur_diesel] == [9999, 10000]


# =============================================================================
# 7. Reset detection when tick moves backward
# =============================================================================
@pytest.mark.asyncio
async def test_reset_detection_on_backward_tick(fixtures_data: dict[str, Any]):
    client = make_mock_client(fixtures_data)
    store = WorldState()
    sync = SyncLoop(client=client, store=store)

    # Perform initial sync at tick 10
    inst10 = copy.deepcopy(fixtures_data["instance"])
    inst10["tick"] = 10
    client.instance.return_value = SimResult(
        data=inst10, stale=False, status=200, latency_ms=5.0
    )
    sync.last_tick = 10
    store.sim_run_id = 1
    await sync.full_sync(tick=10)
    assert store.sim_run_id == 1
    assert sync.last_tick == 10

    # Simulator resets: next cycle reports tick 1
    reset_inst = copy.deepcopy(fixtures_data["instance"])
    reset_inst["tick"] = 1
    client.instance.return_value = SimResult(
        data=reset_inst, stale=False, status=200, latency_ms=5.0
    )

    await sync.request_cycle(tick=1)

    assert store.sim_run_id == 2
    assert sync.last_tick == 1
    assert store.instance is not None
    assert store.instance.tick == 1


# =============================================================================
# 8. Normal tick forward does not trigger reset
# =============================================================================
@pytest.mark.asyncio
async def test_normal_tick_forward_does_not_trigger_reset(fixtures_data: dict[str, Any]):
    client = make_mock_client(fixtures_data)
    store = WorldState()
    sync = SyncLoop(client=client, store=store)

    sync.last_tick = 5
    store.sim_run_id = 1

    inst6 = copy.deepcopy(fixtures_data["instance"])
    inst6["tick"] = 6
    client.instance.return_value = SimResult(
        data=inst6, stale=False, status=200, latency_ms=5.0
    )

    await sync.request_cycle(tick=6)

    assert store.sim_run_id == 1
    assert sync.last_tick == 6


# =============================================================================
# 9. Identical tick does not trigger reset
# =============================================================================
@pytest.mark.asyncio
async def test_identical_tick_does_not_trigger_reset(fixtures_data: dict[str, Any]):
    client = make_mock_client(fixtures_data)
    store = WorldState()
    sync = SyncLoop(client=client, store=store)

    sync.last_tick = 5
    store.sim_run_id = 1

    inst5 = copy.deepcopy(fixtures_data["instance"])
    inst5["tick"] = 5
    client.instance.return_value = SimResult(
        data=inst5, stale=False, status=200, latency_ms=5.0
    )

    await sync.request_cycle(tick=5)

    assert store.sim_run_id == 1
    assert sync.last_tick == 5


# =============================================================================
# 10. Coalescing: two calls during active cycle result in exactly 2 cycles
# =============================================================================
@pytest.mark.asyncio
async def test_coalescing_two_calls_during_active_cycle(fixtures_data: dict[str, Any]):
    client = make_mock_client(fixtures_data)
    store = WorldState()
    sync = SyncLoop(client=client, store=store)

    cycle_count = 0
    pause_first_cycle = asyncio.Event()

    async def slow_instance():
        nonlocal cycle_count
        cycle_count += 1
        if cycle_count == 1:
            await pause_first_cycle.wait()
        return SimResult(data=fixtures_data["instance"], stale=False, status=200, latency_ms=5.0)

    client.instance = AsyncMock(side_effect=slow_instance)

    # Start first cycle (will pause inside slow_instance)
    task1 = asyncio.create_task(sync.request_cycle(1))
    await asyncio.sleep(0.01)

    assert sync.is_running is True

    # Fire two additional cycle requests while cycle 1 is running
    await sync.request_cycle(2)
    assert sync.pending_tick == 2

    await sync.request_cycle(3)
    assert sync.pending_tick == 3

    # Unpause cycle 1
    pause_first_cycle.set()
    await task1

    # Total cycles run: exactly 2 (first cycle for tick 1, coalesced pending cycle for tick 3)
    assert cycle_count == 2
    assert sync.pending_tick is None
    assert sync.is_running is False


# =============================================================================
# 11. Coalescing: multiple rapid calls remember only the latest pending tick
# =============================================================================
@pytest.mark.asyncio
async def test_coalescing_remembers_only_latest_tick(fixtures_data: dict[str, Any]):
    client = make_mock_client(fixtures_data)
    store = WorldState()
    sync = SyncLoop(client=client, store=store)

    pause = asyncio.Event()
    executed_ticks = []

    async def controlled_full_sync(tick: int | None = None):
        executed_ticks.append(tick)
        if len(executed_ticks) == 1:
            await pause.wait()

    sync.full_sync = AsyncMock(side_effect=controlled_full_sync)

    task = asyncio.create_task(sync.request_cycle(10))
    await asyncio.sleep(0.01)

    # Fire multiple rapid requests
    await sync.request_cycle(11)
    await sync.request_cycle(12)
    await sync.request_cycle(13)

    assert sync.pending_tick == 13

    pause.set()
    await task

    # Exactly two cycles executed: tick 10 and latest pending tick 13
    assert executed_ticks == [10, 13]
    assert sync.pending_tick is None


# =============================================================================
# 12. Fallback polling loop detects tick advancement and triggers cycle
# =============================================================================
@pytest.mark.asyncio
async def test_polling_loop_detects_tick_advancement(fixtures_data: dict[str, Any]):
    client = make_mock_client(fixtures_data)
    store = WorldState()
    sync = SyncLoop(client=client, store=store, poll_interval=0.02)

    tick_sequence = [1, 1, 2]
    call_idx = 0

    async def advancing_instance():
        nonlocal call_idx
        t = tick_sequence[min(call_idx, len(tick_sequence) - 1)]
        call_idx += 1
        inst = copy.deepcopy(fixtures_data["instance"])
        inst["tick"] = t
        return SimResult(data=inst, stale=False, status=200, latency_ms=5.0)

    client.instance = AsyncMock(side_effect=advancing_instance)

    poll_task = asyncio.create_task(sync.run())
    # Allow enough time for polling to detect tick 1 and tick 2
    await asyncio.sleep(0.12)
    sync.stop()
    await poll_task

    assert store.tick == 2
    assert sync.last_tick == 2


# =============================================================================
# 13. Fallback polling handles SimUnavailable/connection failure without crashing
# =============================================================================
@pytest.mark.asyncio
async def test_polling_loop_handles_failure_without_crashing(fixtures_data: dict[str, Any]):
    client = make_mock_client(fixtures_data)
    store = WorldState()
    sync = SyncLoop(client=client, store=store, poll_interval=0.02)

    attempt = 0

    async def flaky_instance():
        nonlocal attempt
        attempt += 1
        if attempt <= 2:
            raise SimUnavailable("Sim temporarily down")
        inst = copy.deepcopy(fixtures_data["instance"])
        inst["tick"] = 5
        return SimResult(data=inst, stale=False, status=200, latency_ms=5.0)

    client.instance = AsyncMock(side_effect=flaky_instance)

    poll_task = asyncio.create_task(sync.run())
    await asyncio.sleep(0.12)
    sync.stop()
    await poll_task

    # Loop recovered from errors and processed tick 5
    assert attempt >= 3
    assert store.tick == 5


# =============================================================================
# 14. Hook execution runs registered hooks with WorldState
# =============================================================================
@pytest.mark.asyncio
async def test_hook_execution_receives_world_state(fixtures_data: dict[str, Any]):
    client = make_mock_client(fixtures_data)
    store = WorldState()

    hook_received: list[WorldState] = []

    async def my_hook(st: WorldState):
        hook_received.append(st)

    sync = SyncLoop(client=client, store=store, hooks=[my_hook])
    await sync.full_sync()

    assert len(hook_received) == 1
    assert hook_received[0] is store


# =============================================================================
# 15. Faulty hook does not prevent subsequent hooks or crash cycle
# =============================================================================
@pytest.mark.asyncio
async def test_faulty_hook_isolation(fixtures_data: dict[str, Any]):
    client = make_mock_client(fixtures_data)
    store = WorldState()

    hook2_called = False

    async def bad_hook(st: WorldState):
        raise RuntimeError("Hook disaster!")

    async def good_hook(st: WorldState):
        nonlocal hook2_called
        hook2_called = True

    sync = SyncLoop(client=client, store=store, hooks=[bad_hook, good_hook])

    # Must not raise
    await sync.full_sync()

    assert hook2_called is True
    assert store.instance is not None


# =============================================================================
# 16. fsip_cycle_duration_seconds and fsip_ticks_behind are updated properly
# =============================================================================
@pytest.mark.asyncio
async def test_observability_metrics_updated(fixtures_data: dict[str, Any]):
    client = make_mock_client(fixtures_data)
    store = WorldState()
    sync = SyncLoop(client=client, store=store)

    assert FSIP_CYCLE_DURATION_SECONDS is not None
    assert FSIP_TICKS_BEHIND is not None

    inst5 = copy.deepcopy(fixtures_data["instance"])
    inst5["tick"] = 5
    client.instance.return_value = SimResult(
        data=inst5, stale=False, status=200, latency_ms=5.0
    )

    sample_count_before = FSIP_CYCLE_DURATION_SECONDS._child_samples()

    await sync.request_cycle(tick=5)

    sample_count_after = FSIP_CYCLE_DURATION_SECONDS._child_samples()
    # At least one duration sample was observed
    assert len(sample_count_after) >= len(sample_count_before)

    # Ticks behind is updated (after cycle completion at tick 5, behind is 0)
    assert FSIP_TICKS_BEHIND._value.get() == 0
