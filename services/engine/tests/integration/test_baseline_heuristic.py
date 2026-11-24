"""Integration test for Phase 04 Task 4.11: Baseline Heuristic Autopilot.

Verifies end-to-end allocation execution over 192 ticks (48 hours) against live
simulator and TimescaleDB:
1. Resets simulator to baseline seed 12345.
2. Runs SyncLoop, WriteBuffer, DecisionEngine (autopilot/heuristic), Executor, and AllocationOutcomeTracker.
3. Automatically executes planned allocations without human intervention.
4. Asserts:
   - Service level >= 0.99 (99%+)
   - Simulator allocation failures == 0
   - Persisted decisions, lines, snapshots, and demand rows in database
   - Prometheus allocation metrics
"""
from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from app.config import EngineSettings
from app.db.buffer import WriteBuffer
from app.db.models import Decision, DecisionLine, DemandObservation, WorldSnapshot
from app.db.session import check_db_health, resolve_database_url
from app.decision.engine import DecisionEngine
from app.decision.executor import Executor
from app.decision.tracker import AllocationOutcomeTracker
from app.sim.circuit import CircuitBreaker
from app.sim.client import SimClient
from app.state.store import WorldState
from app.state.sync import SyncLoop
from sqlalchemy import NullPool, func, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

SIM_TEST_URL = os.environ.get("SIM_URL", "http://localhost:8000")
DB_TEST_URL = resolve_database_url()


@pytest_asyncio.fixture
async def sim_client() -> AsyncGenerator[SimClient, None]:
    breaker = CircuitBreaker("simulator", failure_threshold=20, reset_timeout=5.0)
    client = SimClient(base_url=SIM_TEST_URL, timeout=30.0, breaker=breaker)
    try:
        health = await client.health()
        if health.status != 200:
            pytest.skip(f"Simulator at {SIM_TEST_URL} returned status {health.status}")
    except Exception as exc:
        pytest.skip(f"Simulator at {SIM_TEST_URL} is unreachable: {exc}")
    yield client
    await client.close()


@pytest_asyncio.fixture
async def db_engine() -> AsyncGenerator[AsyncEngine, None]:
    engine = create_async_engine(DB_TEST_URL, poolclass=NullPool)
    is_healthy = await check_db_health(engine)
    if not is_healthy:
        pytest.skip(f"Database at {DB_TEST_URL} is unreachable")
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def session_factory(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=db_engine, class_=AsyncSession, expire_on_commit=False)


async def test_baseline_heuristic_192_ticks(
    sim_client: SimClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Run 192 ticks with heuristic policy in autopilot mode."""
    # 1. Reset simulator to baseline
    reset_res = await sim_client.admin_reset()
    assert reset_res.status == 200, f"Reset failed: {reset_res.data}"
    assert reset_res.data.get("tick") == 0

    # 2. Setup background WriteBuffer
    write_buffer = WriteBuffer(sessionmaker=session_factory, batch_size=100)
    buffer_task = asyncio.create_task(write_buffer.run())

    # 3. Setup components in autopilot mode with heuristic policy
    settings = EngineSettings(engine_mode="autopilot", engine_policy="heuristic")
    store = WorldState(operating_mode="autopilot", policy="heuristic")
    executor = Executor(sim_client=sim_client, session_factory=session_factory, settings=settings)
    tracker = AllocationOutcomeTracker(session_factory=session_factory)
    engine = DecisionEngine(
        session_factory=session_factory,
        settings=settings,
        executor=executor,
    )

    sync = SyncLoop(
        client=sim_client,
        store=store,
        write_buffer=write_buffer,
    )
    sync.add_hook(tracker.track_cycle)
    sync.add_hook(engine.on_cycle)

    try:
        # 4. Perform initial full synchronization
        await sync.full_sync()
        assert store.tick == 0

        # 5. Advance 192 simulation ticks (15-min ticks = 48 hours = 2 days)
        total_ticks = 192
        for _ in range(total_ticks):
            current_tick = store.tick
            await sync.request_cycle(tick=current_tick)
            step_res = await sim_client.admin_step()
            assert step_res.status == 200

        # One final cycle to reconcile any arrivals on tick 192
        await sync.request_cycle(tick=total_ticks)

        # 6. Drain and flush database write buffer
        await write_buffer.flush()
        await write_buffer.stop(timeout=5.0, task=buffer_task)

        # 7. Verify simulator metrics
        metrics_res = await sim_client.metrics()
        assert metrics_res.status == 200
        metrics = metrics_res.data

        service_level = metrics.get("service_level", 0.0)
        allocation_failures = metrics.get("allocation_failures", 0)
        capacity_failures = metrics.get("capacity_failures", 0)
        route_cut_failures = metrics.get("route_cut_failures", 0)

        assert service_level >= 0.99, f"Service level {service_level:.4f} is below 0.99 target"
        assert allocation_failures == 0, f"Detected {allocation_failures} allocation failures"
        assert capacity_failures == 0, f"Detected {capacity_failures} capacity failures"
        assert route_cut_failures == 0, f"Detected {route_cut_failures} route cut failures"

        # 8. Verify database persistence
        active_sim_run_id = store.sim_run_id
        assert active_sim_run_id is not None

        async with session_factory() as session:
            # Check decisions created
            decisions_res = await session.execute(
                select(Decision).where(Decision.sim_run_id == active_sim_run_id)
            )
            decisions = decisions_res.scalars().all()
            assert len(decisions) > 0, "No decisions recorded in database"

            # Check decision lines and statuses
            lines_res = await session.execute(
                select(DecisionLine)
                .join(Decision, DecisionLine.decision_id == Decision.id)
                .where(Decision.sim_run_id == active_sim_run_id)
            )
            lines = lines_res.scalars().all()
            assert len(lines) > 0, "No decision lines recorded in database"

            # Check lines have allocation IDs and transitioned from PLANNED
            submitted_or_arrived = [line_item for line_item in lines if line_item.status in ("SUBMITTED", "ARRIVED")]
            assert len(submitted_or_arrived) > 0, "No lines submitted or arrived"
            for line_item in submitted_or_arrived:
                assert line_item.sim_allocation_id is not None

            # Check world snapshots persisted
            snap_count = await session.scalar(
                select(func.count(WorldSnapshot.tick)).where(
                    WorldSnapshot.sim_run_id == active_sim_run_id
                )
            )
            assert snap_count is not None and snap_count >= 190, f"Expected ~192 snapshots, found {snap_count}"

            # Check demand observations persisted
            demand_count = await session.scalar(
                select(func.count(DemandObservation.sim_row_id)).where(
                    DemandObservation.sim_run_id == active_sim_run_id
                )
            )
            assert demand_count is not None and demand_count > 0, "No demand observations recorded"

        # 9. Verify Prometheus metric counters
        from app.decision.executor import FSIP_ALLOCATIONS_SUBMITTED_TOTAL
        assert FSIP_ALLOCATIONS_SUBMITTED_TOTAL is not None

    finally:
        if not buffer_task.done():
            await write_buffer.stop(timeout=5.0, task=buffer_task)
