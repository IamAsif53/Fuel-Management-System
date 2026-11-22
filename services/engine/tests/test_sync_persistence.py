"""Tests for SyncLoop to Database Persistence (Task 2.4).

Verifies:
- Category A: Initial simulation run creation & lifecycle.
- Category B: Snapshot persistence & JSONB payload isolation.
- Category C: Demand observation persistence & deduplication.
- Category D: Simulator reset lifecycle & run ID isolation.
- Category E: Failure isolation & non-blocking resilience.
- Category F: Live TimescaleDB integration test.
"""
from __future__ import annotations

import asyncio
import copy
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from app.db.buffer import WriteBuffer
from app.db.models import DemandObservation as DBObservation
from app.db.models import SimRun, WorldSnapshot
from app.db.session import check_db_health, resolve_database_url
from app.sim.client import SimClient, SimResult
from app.state.persistence import StatePersistence
from app.state.store import WorldState
from app.state.sync import SyncLoop
from fsip_common.models import DemandObservation, Depot, Instance, SimMetrics, Station
from sqlalchemy import NullPool, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

pytestmark = pytest.mark.asyncio

FX = Path(__file__).resolve().parents[3] / "libs" / "fsip_common" / "tests" / "fixtures"
DB_TEST_URL = resolve_database_url()


# =============================================================================
# Test Doubles & Fixtures
# =============================================================================

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
    """Create a mock SimClient dynamically returning SimResults reflecting current data."""
    client = AsyncMock(spec=SimClient)
    client.instance.side_effect = lambda: SimResult(
        data=copy.deepcopy(data["instance"]), stale=False, status=200, latency_ms=5.0
    )
    client.regions.side_effect = lambda: SimResult(
        data=copy.deepcopy(data["regions"]), stale=False, status=200, latency_ms=5.0
    )
    client.depots.side_effect = lambda: SimResult(
        data=copy.deepcopy(data["depots"]), stale=False, status=200, latency_ms=5.0
    )
    client.stations.side_effect = lambda: SimResult(
        data=copy.deepcopy(data["stations"]), stale=False, status=200, latency_ms=5.0
    )
    client.routes.side_effect = lambda: SimResult(
        data=copy.deepcopy(data["routes"]), stale=False, status=200, latency_ms=5.0
    )
    client.events.side_effect = lambda: SimResult(
        data=copy.deepcopy(data["events"]), stale=False, status=200, latency_ms=5.0
    )
    client.supply_arrivals.side_effect = lambda: SimResult(
        data=copy.deepcopy(data["supply_arrivals"]), stale=False, status=200, latency_ms=5.0
    )
    client.allocations.side_effect = lambda: SimResult(
        data=copy.deepcopy(data["allocations"]), stale=False, status=200, latency_ms=5.0
    )
    client.metrics.side_effect = lambda: SimResult(
        data=copy.deepcopy(data["metrics"]), stale=False, status=200, latency_ms=5.0
    )
    client.demand_history.side_effect = lambda limit=100: SimResult(
        data=copy.deepcopy(data["demand_history"]), stale=False, status=200, latency_ms=5.0
    )
    return client


class RecordingWriteBuffer:
    """Synchronous test double for WriteBuffer recording submitted operations."""

    def __init__(self, should_fail_submit: bool = False) -> None:
        self.submitted_ops: list[tuple[Callable[[AsyncSession], Awaitable[None]], str]] = []
        self.should_fail_submit = should_fail_submit

    def submit(self, fn: Callable[[AsyncSession], Awaitable[None]], kind: str = "unknown") -> None:
        if self.should_fail_submit:
            raise RuntimeError("Simulated WriteBuffer submission failure")
        self.submitted_ops.append((fn, kind))

    @property
    def kinds(self) -> list[str]:
        return [kind for _, kind in self.submitted_ops]


@pytest_asyncio.fixture
async def live_db_engine() -> AsyncEngine:
    """Fixture providing connected AsyncEngine against TimescaleDB (skips if unreachable)."""
    engine = create_async_engine(DB_TEST_URL, poolclass=NullPool)
    is_healthy = await check_db_health(engine)
    if not is_healthy:
        pytest.skip(f"Database at {DB_TEST_URL} is not reachable.")
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def live_sessionmaker(live_db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        bind=live_db_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )


# =============================================================================
# A. Initial Simulation Run Lifecycle
# =============================================================================

async def test_initial_sync_creates_single_sim_run(fixtures_data: dict[str, Any]):
    """Verify first sync cycle creates exactly one database simulation run, not repeated."""
    buf = RecordingWriteBuffer()
    client = make_mock_client(fixtures_data)
    store = WorldState()
    sync = SyncLoop(client=client, store=store, write_buffer=buf)  # type: ignore[arg-type]

    # Initial sync
    await sync.full_sync()

    # Must have exactly 1 'sim_run' write enqueued
    sim_run_ops = [kind for kind in buf.kinds if kind == "sim_run"]
    assert len(sim_run_ops) == 1

    # Order must be sim_run first, then snapshot, then demand
    assert buf.kinds[0] == "sim_run"
    assert "snapshot" in buf.kinds
    assert "demand" in buf.kinds

    # Second sync on the same run/session
    buf.submitted_ops.clear()
    await sync.full_sync()

    # Must NOT create a second sim_run
    sim_run_ops_second = [kind for kind in buf.kinds if kind == "sim_run"]
    assert len(sim_run_ops_second) == 0


async def test_run_creation_enqueued_before_dependent_writes(fixtures_data: dict[str, Any]):
    """Verify run creation is enqueued strictly before snapshot and demand operations."""
    buf = RecordingWriteBuffer()
    client = make_mock_client(fixtures_data)
    store = WorldState()
    sync = SyncLoop(client=client, store=store, write_buffer=buf)  # type: ignore[arg-type]

    await sync.full_sync()

    kinds = buf.kinds
    sim_run_idx = kinds.index("sim_run")
    snapshot_idx = kinds.index("snapshot")
    demand_idx = kinds.index("demand")

    assert sim_run_idx < snapshot_idx
    assert sim_run_idx < demand_idx


async def test_run_handle_resolves_id_for_dependent_writes(fixtures_data: dict[str, Any]):
    """Verify RunHandle resolves the sequence ID to all dependent operations in FIFO sequence."""
    buf = RecordingWriteBuffer()
    persistence = StatePersistence(write_buffer=buf)  # type: ignore[arg-type]

    store = WorldState()
    store.instance = Instance(
        id=1,
        scenario_id="scenario-test",
        scenario_version="1.0.0",
        seed=42,
        sim_time=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
        tick=0,
        tick_minutes=15,
        status="RUNNING",
    )
    store.depots = [Depot.model_validate(fixtures_data["depots"][0])]
    store.stations = [Station.model_validate(fixtures_data["stations"][0])]
    store.metrics = SimMetrics.model_validate(fixtures_data["metrics"])

    obs = DemandObservation(
        id=101,
        station_id=store.stations[0].id,
        fuel_type="DIESEL",
        tick=0,
        sim_time=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
        demand_liters=100.0,
        served_liters=100.0,
        unmet_liters=0.0,
    )

    persistence.persist_cycle(store, demand_items=[obs])

    assert len(buf.submitted_ops) == 3
    create_op, _ = buf.submitted_ops[0]
    snap_op, _ = buf.submitted_ops[1]
    demand_op, _ = buf.submitted_ops[2]

    # Mock an AsyncSession that establishes run_id = 999
    captured_snapshot: dict[str, Any] = {}
    captured_demand: list[dict[str, Any]] = []

    class MockSession:
        def add(self, obj):
            if isinstance(obj, SimRun):
                obj.id = 999

        async def flush(self):
            pass

        async def execute(self, stmt):
            pass

        async def get(self, model, ident, **kwargs):
            return WorldSnapshot(id=1, sim_run_id=ident[0], tick=0, sim_time=ident[1])

    sess = MockSession()

    # Step 1: create_sim_run sets handle
    await create_op(sess)  # type: ignore[arg-type]
    assert persistence.active_run_handle is not None
    assert persistence.active_run_handle.db_run_id == 999
    assert store.sim_run_id == 999

    # Step 2 & 3: snap_op and demand_op execute without error and resolve 999
    # Monkeypatch upsert_snapshot and insert_demand_rows to capture payloads
    async def mock_upsert(s, snap):
        captured_snapshot.update(snap)
        return WorldSnapshot(id=1, sim_time=snap["sim_time"])

    async def mock_insert_demand(s, rows):
        captured_demand.extend(rows)
        return len(rows)

    with (
        pytest.MonkeyPatch.context() as mp,
    ):
        mp.setattr("app.state.persistence.upsert_snapshot", mock_upsert)
        mp.setattr("app.state.persistence.insert_demand_rows", mock_insert_demand)

        await snap_op(sess)  # type: ignore[arg-type]
        await demand_op(sess)  # type: ignore[arg-type]

    assert captured_snapshot["sim_run_id"] == 999
    assert captured_demand[0]["sim_run_id"] == 999


# =============================================================================
# B. World Snapshot Persistence
# =============================================================================

async def test_snapshot_enqueued_per_successful_tick(fixtures_data: dict[str, Any]):
    """Verify one snapshot is enqueued per successful tick with correct fields."""
    buf = RecordingWriteBuffer()
    client = make_mock_client(fixtures_data)
    store = WorldState()
    sync = SyncLoop(client=client, store=store, write_buffer=buf)  # type: ignore[arg-type]

    await sync.full_sync(tick=1)

    snap_ops = [kind for kind in buf.kinds if kind == "snapshot"]
    assert len(snap_ops) == 1


async def test_duplicate_tick_does_not_re_enqueue_snapshot(fixtures_data: dict[str, Any]):
    """Verify repeated synchronization for the same tick does not duplicate snapshot enqueues."""
    buf = RecordingWriteBuffer()
    client = make_mock_client(fixtures_data)
    store = WorldState()
    sync = SyncLoop(client=client, store=store, write_buffer=buf)  # type: ignore[arg-type]

    # Tick 1
    await sync.full_sync(tick=1)
    assert buf.kinds.count("snapshot") == 1

    # Tick 1 repeated (e.g. repeated SSE event or poll)
    await sync.full_sync(tick=1)
    assert buf.kinds.count("snapshot") == 1  # Still exactly 1!


async def test_snapshot_jsonb_isolation_from_world_state_mutation(fixtures_data: dict[str, Any]):
    """Verify queued snapshot payload is completely isolated from later mutations to WorldState."""
    buf = RecordingWriteBuffer()
    persistence = StatePersistence(write_buffer=buf)  # type: ignore[arg-type]

    store = WorldState()
    store.instance = Instance(
        id=1, scenario_id="s1", scenario_version="1", seed=1,
        sim_time=datetime(2026, 1, 1, 12, 0, tzinfo=UTC), tick=1, tick_minutes=15, status="RUNNING",
    )
    store.depots = [Depot.model_validate(fixtures_data["depots"][0])]
    store.stations = [Station.model_validate(fixtures_data["stations"][0])]
    store.metrics = SimMetrics.model_validate(fixtures_data["metrics"])

    orig_depot_inv = copy.deepcopy(store.depots[0].inventory)
    orig_station_inv = copy.deepcopy(store.stations[0].inventory)
    orig_service_level = store.metrics.service_level

    persistence.persist_cycle(store)

    # Now mutate WorldState in-memory
    store.depots[0].inventory["DIESEL"] = 999999.0
    store.stations[0].inventory["DIESEL"] = 888888.0
    store.metrics.service_level = 0.10

    # Execute snapshot op and inspect persisted dictionary
    captured: dict[str, Any] = {}

    async def mock_upsert(s, snap):
        captured.update(snap)
        return WorldSnapshot(id=1, sim_time=snap["sim_time"])

    snap_op = [fn for fn, kind in buf.submitted_ops if kind == "snapshot"][0]
    persistence.active_run_handle.set_id(777)  # type: ignore[union-attr]

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("app.state.persistence.upsert_snapshot", mock_upsert)
        await snap_op(AsyncMock())

    # Snapshot MUST retain original values, NOT the mutated values
    assert captured["depots"][0]["inventory"] == orig_depot_inv
    assert captured["stations"][0]["inventory"] == orig_station_inv
    assert captured["metrics"]["service_level"] == orig_service_level


async def test_snapshot_preserves_stale_and_degraded_flags():
    """Verify stale and degraded flags in WorldState are accurately mapped to the snapshot."""
    buf = RecordingWriteBuffer()
    persistence = StatePersistence(write_buffer=buf)  # type: ignore[arg-type]

    store = WorldState()
    store.instance = Instance(
        id=1, scenario_id="s1", scenario_version="1", seed=1,
        sim_time=datetime(2026, 1, 1, 12, 0, tzinfo=UTC), tick=1, tick_minutes=15, status="RUNNING",
    )
    store.stale = True
    store.degraded_reasons.add("routes_failed")

    persistence.persist_cycle(store)

    captured: dict[str, Any] = {}

    async def mock_upsert(s, snap):
        captured.update(snap)
        return WorldSnapshot(id=1, sim_time=snap["sim_time"])

    snap_op = [fn for fn, kind in buf.submitted_ops if kind == "snapshot"][0]
    persistence.active_run_handle.set_id(1)  # type: ignore[union-attr]

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("app.state.persistence.upsert_snapshot", mock_upsert)
        await snap_op(AsyncMock())

    assert captured["stale"] is True
    assert captured["degraded"] is True


# =============================================================================
# C. Demand Observations Persistence & Deduplication
# =============================================================================

async def test_newly_observed_demand_rows_enqueued():
    """Verify newly observed demand rows are enqueued with complete mapped fields."""
    buf = RecordingWriteBuffer()
    persistence = StatePersistence(write_buffer=buf)  # type: ignore[arg-type]

    store = WorldState()
    store.instance = Instance(
        id=1, scenario_id="s1", scenario_version="1", seed=1,
        sim_time=datetime(2026, 1, 1, 12, 0, tzinfo=UTC), tick=1, tick_minutes=15, status="RUNNING",
    )

    rows = [
        DemandObservation(
            id=i,
            station_id=f"station-{i}",
            fuel_type="DIESEL",
            tick=1,
            sim_time=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
            demand_liters=100.0 * i,
            served_liters=90.0 * i,
            unmet_liters=10.0 * i,
        )
        for i in range(1, 4)
    ]

    persistence.persist_cycle(store, demand_items=rows)

    assert "demand" in buf.kinds
    demand_op = [fn for fn, kind in buf.submitted_ops if kind == "demand"][0]
    persistence.active_run_handle.set_id(50)  # type: ignore[union-attr]

    captured: list[dict[str, Any]] = []

    async def mock_insert_demand(s, r):
        captured.extend(r)
        return len(r)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("app.state.persistence.insert_demand_rows", mock_insert_demand)
        await demand_op(AsyncMock())

    assert len(captured) == 3
    assert captured[0]["sim_run_id"] == 50
    assert captured[0]["sim_row_id"] == 1
    assert captured[0]["demand_liters"] == 100.0


async def test_overlapping_demand_windows_deduplicated():
    """Verify overlapping demand-history windows do not re-enqueue previously seen rows."""
    buf = RecordingWriteBuffer()
    persistence = StatePersistence(write_buffer=buf)  # type: ignore[arg-type]

    store = WorldState()
    store.instance = Instance(
        id=1, scenario_id="s1", scenario_version="1", seed=1,
        sim_time=datetime(2026, 1, 1, 12, 0, tzinfo=UTC), tick=1, tick_minutes=15, status="RUNNING",
    )

    def make_obs(row_id: int):
        return DemandObservation(
            id=row_id, station_id="s1", fuel_type="DIESEL", tick=1,
            sim_time=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
            demand_liters=50.0, served_liters=50.0, unmet_liters=0.0,
        )

    # Window 1: rows 1 to 5
    window1 = [make_obs(i) for i in range(1, 6)]
    persistence.persist_cycle(store, demand_items=window1)

    captured_batch1: list[dict[str, Any]] = []

    async def mock_insert1(s, r):
        captured_batch1.extend(r)
        return len(r)

    demand_op1 = [fn for fn, kind in buf.submitted_ops if kind == "demand"][0]
    persistence.active_run_handle.set_id(1)  # type: ignore[union-attr]

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("app.state.persistence.insert_demand_rows", mock_insert1)
        await demand_op1(AsyncMock())

    assert len(captured_batch1) == 5
    buf.submitted_ops.clear()

    # Window 2: rows 3 to 8 (overlapping rows 3, 4, 5 and new rows 6, 7, 8)
    store.instance.tick = 2
    window2 = [make_obs(i) for i in range(3, 9)]
    persistence.persist_cycle(store, demand_items=window2)

    captured_batch2: list[dict[str, Any]] = []

    async def mock_insert2(s, r):
        captured_batch2.extend(r)
        return len(r)

    demand_op2 = [fn for fn, kind in buf.submitted_ops if kind == "demand"][0]

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("app.state.persistence.insert_demand_rows", mock_insert2)
        await demand_op2(AsyncMock())

    # Only rows 6, 7, 8 should be submitted (exactly 3 new rows)!
    assert len(captured_batch2) == 3
    assert [r["sim_row_id"] for r in captured_batch2] == [6, 7, 8]


# =============================================================================
# D. Simulator Reset Handling
# =============================================================================

async def test_reset_closes_old_run_and_creates_new_run(fixtures_data: dict[str, Any]):
    """Verify simulator reset enqueues close_sim_run for Run A and create_sim_run for Run B."""
    buf = RecordingWriteBuffer()
    client = make_mock_client(fixtures_data)
    store = WorldState()
    sync = SyncLoop(client=client, store=store, write_buffer=buf)  # type: ignore[arg-type]

    # Initial sync -> Run A
    await sync.full_sync(tick=1)
    run_a_handle = sync.persistence.active_run_handle
    assert run_a_handle is not None

    # Simulate run A execution to establish run_id = 100
    run_a_handle.set_id(100)
    buf.submitted_ops.clear()

    # Trigger reset (tick moves backward from 1 to 0)
    sync.on_reset(new_tick=0)

    # Must enqueue close_sim_run for Run A
    assert "sim_run" in buf.kinds
    close_op = buf.submitted_ops[0][0]

    closed_runs: list[int] = []

    async def mock_close(s, sim_run_id, **kwargs):
        closed_runs.append(sim_run_id)
        return None

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("app.state.persistence.close_sim_run", mock_close)
        await close_op(AsyncMock())

    assert closed_runs == [100]

    # Next full_sync after reset creates Run B
    buf.submitted_ops.clear()
    await sync.full_sync(tick=0)

    run_b_handle = sync.persistence.active_run_handle
    assert run_b_handle is not None
    assert run_b_handle is not run_a_handle  # Distinct run handle
    assert "sim_run" in buf.kinds
    assert "snapshot" in buf.kinds


async def test_ordinary_sse_reconnect_does_not_trigger_reset(fixtures_data: dict[str, Any]):
    """Verify ordinary SSE reconnect full_sync without backward tick does not create a new run."""
    buf = RecordingWriteBuffer()
    client = make_mock_client(fixtures_data)
    store = WorldState()
    sync = SyncLoop(client=client, store=store, write_buffer=buf)  # type: ignore[arg-type]

    await sync.full_sync(tick=5)
    run_handle = sync.persistence.active_run_handle
    assert run_handle is not None

    buf.submitted_ops.clear()

    # Reconnect triggers full_sync(tick=5)
    await sync.full_sync(tick=5)

    # Active run handle MUST remain the same; no new sim_run created
    assert sync.persistence.active_run_handle is run_handle
    assert buf.kinds.count("sim_run") == 0


# =============================================================================
# E. Failure Isolation & Robustness
# =============================================================================

async def test_write_buffer_submission_failure_does_not_crash_sync(fixtures_data: dict[str, Any]):
    """Verify an unexpected exception in WriteBuffer.submit() does not crash SyncLoop."""
    buf = RecordingWriteBuffer(should_fail_submit=True)
    client = make_mock_client(fixtures_data)
    store = WorldState()
    sync = SyncLoop(client=client, store=store, write_buffer=buf)  # type: ignore[arg-type]

    # full_sync must succeed without raising exception
    await sync.full_sync(tick=1)

    # In-memory WorldState was updated normally
    assert store.instance is not None
    assert store.tick == fixtures_data["instance"]["tick"]


async def test_unresolved_run_id_raises_runtime_error_for_retry():
    """Verify attempting to persist snapshot or demand before run ID is ready raises for retry."""
    buf = RecordingWriteBuffer()
    persistence = StatePersistence(write_buffer=buf)  # type: ignore[arg-type]

    store = WorldState()
    store.instance = Instance(
        id=1, scenario_id="s1", scenario_version="1", seed=1,
        sim_time=datetime(2026, 1, 1, 12, 0, tzinfo=UTC), tick=1, tick_minutes=15, status="RUNNING",
    )

    persistence.persist_cycle(store)
    snap_op = [fn for fn, kind in buf.submitted_ops if kind == "snapshot"][0]

    # Run ID is NOT resolved yet (handle.is_ready is False)
    with pytest.raises(RuntimeError, match="sim_run_id not yet resolved"):
        await snap_op(AsyncMock())


# =============================================================================
# F. Live TimescaleDB Integration Tests
# =============================================================================

@pytest.mark.integration
async def test_live_timescaledb_persistence_full_lifecycle(
    live_sessionmaker: async_sessionmaker[AsyncSession],
    fixtures_data: dict[str, Any],
):
    """Verify real persistence to live TimescaleDB: runs, snapshots, demand rows, and reset."""
    buf = WriteBuffer(sessionmaker=live_sessionmaker, flush_interval=0.01)
    worker = asyncio.create_task(buf.run())

    client = make_mock_client(fixtures_data)
    store = WorldState()
    sync = SyncLoop(client=client, store=store, write_buffer=buf)

    try:
        # 1. First cycle (Tick 0)
        fixtures_data["instance"]["tick"] = 0
        fixtures_data["instance"]["sim_time"] = "2026-01-01T00:00:00Z"
        await sync.full_sync(tick=0)
        await buf.flush(timeout=5.0)

        run_id_1 = sync.store.sim_run_id
        assert run_id_1 is not None

        # Verify DB records for Run 1
        async with live_sessionmaker() as session:
            # SimRun exists
            run_res = await session.execute(select(SimRun).where(SimRun.id == run_id_1))
            run1 = run_res.scalar_one_or_none()
            assert run1 is not None
            assert run1.first_tick == 0
            assert run1.ended_at is None

            # Snapshot exists for tick 0
            snap_res = await session.execute(
                select(WorldSnapshot).where(
                    WorldSnapshot.sim_run_id == run_id_1,
                    WorldSnapshot.tick == 0,
                )
            )
            snap0 = snap_res.scalar_one_or_none()
            assert snap0 is not None
            assert snap0.sim_time == datetime(2026, 1, 1, 0, 0, tzinfo=UTC)

            # Demand rows exist for Run 1
            dem_res = await session.execute(
                select(DBObservation).where(DBObservation.sim_run_id == run_id_1)
            )
            dem_rows = dem_res.scalars().all()
            assert len(dem_rows) > 0

        # 2. Cycle 2 (Tick 1)
        fixtures_data["instance"]["tick"] = 1
        fixtures_data["instance"]["sim_time"] = "2026-01-01T00:15:00Z"
        await sync.full_sync(tick=1)
        await buf.flush(timeout=5.0)

        # Verify tick 1 snapshot added under same Run 1
        async with live_sessionmaker() as session:
            snap_res = await session.execute(
                select(WorldSnapshot).where(
                    WorldSnapshot.sim_run_id == run_id_1,
                    WorldSnapshot.tick == 1,
                )
            )
            snap1 = snap_res.scalar_one_or_none()
            assert snap1 is not None
            assert snap1.sim_run_id == run_id_1

        # 3. Simulator Reset (tick moves back to 0)
        sync.on_reset(new_tick=0)
        fixtures_data["instance"]["tick"] = 0
        fixtures_data["instance"]["sim_time"] = "2026-01-02T00:00:00Z"
        await sync.full_sync(tick=0)
        await buf.flush(timeout=5.0)

        run_id_2 = sync.store.sim_run_id
        assert run_id_2 != run_id_1

        # Verify Run 1 is now closed and Run 2 is active
        async with live_sessionmaker() as session:
            run1_closed = await session.get(SimRun, run_id_1)
            assert run1_closed is not None
            assert run1_closed.ended_at is not None
            assert run1_closed.last_tick == 1

            run2_active = await session.get(SimRun, run_id_2)
            assert run2_active is not None
            assert run2_active.first_tick == 0
            assert run2_active.ended_at is None

            # Snapshot for new tick 0 is associated with Run 2
            snap_new_res = await session.execute(
                select(WorldSnapshot).where(
                    WorldSnapshot.sim_run_id == run_id_2,
                    WorldSnapshot.tick == 0,
                )
            )
            snap_new = snap_new_res.scalar_one_or_none()
            assert snap_new is not None
            assert snap_new.sim_run_id == run_id_2

    finally:
        await buf.stop(timeout=2.0, task=worker)
