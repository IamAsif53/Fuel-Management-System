"""Unit tests for the AllocationOutcomeTracker."""

from __future__ import annotations

from typing import Any

import pytest
import pytest_asyncio
from app.db.models import Decision, DecisionLine, DecisionOutcome, SimRun
from app.db.session import check_db_health, resolve_database_url
from app.decision.tracker import AllocationOutcomeTracker
from sqlalchemy import NullPool, delete, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

pytestmark = [pytest.mark.asyncio]

DB_TEST_URL = resolve_database_url()


@pytest_asyncio.fixture
async def db_engine():
    engine = create_async_engine(DB_TEST_URL, poolclass=NullPool)
    is_healthy = await check_db_health(engine)
    if not is_healthy:
        pytest.skip(f"Database at {DB_TEST_URL} is not reachable.")
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def session_factory(db_engine: AsyncEngine):
    return async_sessionmaker(bind=db_engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture
async def test_sim_run_id(session_factory):
    async with session_factory() as session:
        run = SimRun(
            scenario_id="tracker-test-run",
            tick_minutes=15,
            first_tick=0,
        )
        session.add(run)
        await session.commit()
        run_id = run.id

    yield run_id

    async with session_factory() as session:
        decs = (await session.execute(select(Decision.id).where(Decision.sim_run_id == run_id))).scalars().all()
        if decs:
            await session.execute(delete(DecisionOutcome).where(DecisionOutcome.decision_id.in_(decs)))
            await session.execute(delete(DecisionLine).where(DecisionLine.decision_id.in_(decs)))
        await session.execute(delete(Decision).where(Decision.sim_run_id == run_id))
        await session.execute(delete(SimRun).where(SimRun.id == run_id))
        await session.commit()


class MockPublisher:
    def __init__(self):
        self.events: list[dict[str, Any]] = []

    async def event(self, type: str, data: dict, sim_run_id: int | None = None, tick: int | None = None):
        self.events.append({"type": type, "data": data, "sim_run_id": sim_run_id, "tick": tick})
        return "mock-event-id"


class DummyAllocation:
    def __init__(
        self,
        id: int,
        idempotency_key: str,
        status: str,
        departure_tick: int | None = None,
        expected_arrival_tick: int | None = None,
        actual_arrival_tick: int | None = None,
        failure_reason: str | None = None,
    ):
        self.id = id
        self.idempotency_key = idempotency_key
        self.status = status
        self.departure_tick = departure_tick
        self.expected_arrival_tick = expected_arrival_tick
        self.actual_arrival_tick = actual_arrival_tick
        self.failure_reason = failure_reason


class DummyState:
    def __init__(self, tick: int, sim_run_id: int, allocations: list[Any], stations: list[Any] | None = None):
        self.tick = tick
        self.sim_run_id = sim_run_id
        self.allocations = allocations
        self.stations = stations or []


async def test_track_in_transit_and_arrived(session_factory, test_sim_run_id):
    async with session_factory() as session:
        dec = Decision(sim_run_id=test_sim_run_id, tick=5, status="EXECUTED")
        session.add(dec)
        await session.flush()

        line = DecisionLine(
            decision_id=dec.id,
            line_no=1,
            source_depot_id="depot-gazipur",
            destination_station_id="station-mirpur",
            route_id="route-gazipur-mirpur",
            fuel_type="DIESEL",
            quantity=3000.0,
            idempotency_key="fsip-track-1",
            status="SUBMITTED",
        )
        session.add(line)
        await session.commit()
        dec_id = dec.id
        line_id = line.id

    publisher = MockPublisher()
    tracker = AllocationOutcomeTracker(session_factory=session_factory, publisher=publisher)

    # 1. Simulator reports IN_TRANSIT
    alloc_in_transit = DummyAllocation(
        id=501,
        idempotency_key="fsip-track-1",
        status="IN_TRANSIT",
        departure_tick=6,
        expected_arrival_tick=8,
    )
    state1 = DummyState(tick=6, sim_run_id=test_sim_run_id, allocations=[alloc_in_transit])
    updated = await tracker.track_cycle(state1)
    assert updated == 1

    async with session_factory() as session:
        db_ln = await session.get(DecisionLine, line_id)
        assert db_ln.status == "IN_TRANSIT"
        assert db_ln.sim_allocation_id == 501
        assert db_ln.expected_arrival_tick == 8

    # 2. Simulator reports ARRIVED at tick 8
    station = {
        "id": "station-mirpur",
        "capacity": {"DIESEL": 10000.0},
        "inventory": {"DIESEL": 5000.0},
    }
    alloc_arrived = DummyAllocation(
        id=501,
        idempotency_key="fsip-track-1",
        status="ARRIVED",
        actual_arrival_tick=8,
    )
    state2 = DummyState(tick=8, sim_run_id=test_sim_run_id, allocations=[alloc_arrived], stations=[station])
    updated = await tracker.track_cycle(state2)
    assert updated == 1

    async with session_factory() as session:
        db_ln = await session.get(DecisionLine, line_id)
        assert db_ln.status == "ARRIVED"
        assert db_ln.actual_arrival_tick == 8
        assert db_ln.received_liters == 3000.0

        # DecisionOutcome should be recorded
        outcome = await session.get(DecisionOutcome, dec_id)
        assert outcome is not None
        assert outcome.hit is True
        assert outcome.realised["arrived_lines"] == 1


async def test_track_arrival_with_overflow_detected(session_factory, test_sim_run_id):
    async with session_factory() as session:
        dec = Decision(sim_run_id=test_sim_run_id, tick=10, status="EXECUTED")
        session.add(dec)
        await session.flush()

        line = DecisionLine(
            decision_id=dec.id,
            line_no=1,
            source_depot_id="depot-gazipur",
            destination_station_id="station-mirpur",
            route_id="route-gazipur-mirpur",
            fuel_type="DIESEL",
            quantity=4000.0,
            idempotency_key="fsip-track-overflow",
            status="IN_TRANSIT",
        )
        session.add(line)
        await session.commit()
        line_id = line.id

    tracker = AllocationOutcomeTracker(session_factory=session_factory)

    # Station has 9000L current inventory, capacity is 10000L -> Headroom is 1000L
    # Quantity is 4000L -> Overflow is 3000L, received is 1000L
    station = {
        "id": "station-mirpur",
        "capacity": {"DIESEL": 10000.0},
        "inventory": {"DIESEL": 9000.0},
    }
    alloc = DummyAllocation(
        id=601,
        idempotency_key="fsip-track-overflow",
        status="ARRIVED",
        actual_arrival_tick=12,
    )
    state = DummyState(tick=12, sim_run_id=test_sim_run_id, allocations=[alloc], stations=[station])
    updated = await tracker.track_cycle(state)
    assert updated == 1

    async with session_factory() as session:
        db_ln = await session.get(DecisionLine, line_id)
        assert db_ln.status == "ARRIVED"
        assert db_ln.received_liters == 1000.0


async def test_track_failed_allocation(session_factory, test_sim_run_id):
    async with session_factory() as session:
        dec = Decision(sim_run_id=test_sim_run_id, tick=10, status="EXECUTED")
        session.add(dec)
        await session.flush()

        line = DecisionLine(
            decision_id=dec.id,
            line_no=1,
            source_depot_id="depot-gazipur",
            destination_station_id="station-mirpur",
            route_id="route-gazipur-mirpur",
            fuel_type="OCTANE",
            quantity=2000.0,
            idempotency_key="fsip-track-fail",
            status="IN_TRANSIT",
        )
        session.add(line)
        await session.commit()
        line_id = line.id
        dec_id = dec.id

    publisher = MockPublisher()
    tracker = AllocationOutcomeTracker(session_factory=session_factory, publisher=publisher)

    alloc_failed = DummyAllocation(
        id=701,
        idempotency_key="fsip-track-fail",
        status="FAILED",
        failure_reason="TRUCK_BREAKDOWN",
    )
    state = DummyState(tick=11, sim_run_id=test_sim_run_id, allocations=[alloc_failed])
    updated = await tracker.track_cycle(state)
    assert updated == 1

    async with session_factory() as session:
        db_ln = await session.get(DecisionLine, line_id)
        assert db_ln.status == "FAILED"
        assert "TRUCK_BREAKDOWN" in db_ln.reason

        outcome = await session.get(DecisionOutcome, dec_id)
        assert outcome is not None
        assert outcome.hit is False
        assert outcome.realised["failed_lines"] == 1

    assert len(publisher.events) == 1
    assert publisher.events[0]["type"] == "allocation.failed"
