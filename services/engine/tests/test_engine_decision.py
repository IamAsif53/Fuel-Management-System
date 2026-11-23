"""Tests for DecisionEngine and Approval Policy (Task 4.4).

Verifies:
- Decision creation and persistence
- Empty plan (no-op)
- Advisory mode (PENDING_APPROVAL)
- Autopilot truth-table (auto-execution gates)
- Idempotency key formatting: fsip-{sim_run_id}-{decision_id}-{line_no}
- Superseding older PENDING_APPROVAL decisions
- Expiry logic at expires_at_tick
- Approval and rejection flows with validation
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from app.config import EngineSettings
from app.db.models import Decision, DecisionLine, SimRun
from app.db.session import check_db_health, resolve_database_url
from app.decision.engine import (
    DecisionEngine,
    DecisionExpiredError,
    DecisionInvalidStateError,
    DecisionNotFoundError,
)
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
            scenario_id="decision-engine-test",
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
            await session.execute(delete(DecisionLine).where(DecisionLine.decision_id.in_(decs)))
        await session.execute(delete(Decision).where(Decision.sim_run_id == run_id))
        await session.execute(delete(SimRun).where(SimRun.id == run_id))
        await session.commit()


class MockPublisher:
    def __init__(self):
        self.events = []

    async def event(self, type: str, data: dict, sim_run_id: int | None = None, tick: int | None = None):
        self.events.append({"type": type, "data": data, "sim_run_id": sim_run_id, "tick": tick})
        return "event-id-1"


def _make_dummy_state(
    tick: int = 10,
    sim_run_id: int = 1,
    mode: str = "advisory",
    stale: bool = False,
    degraded: bool = False,
    station_inv: float = 2000.0,
):
    class DummyStation:
        id = "station-mirpur"
        status = "OPEN"
        capacity = {"DIESEL": 10000.0, "PETROL": 8000.0, "OCTANE": 5000.0}
        inventory = {"DIESEL": station_inv, "PETROL": 7000.0, "OCTANE": 4500.0}

    class DummyDepot:
        id = "depot-gazipur"
        status = "OPEN"
        capacity = {"DIESEL": 50000.0, "PETROL": 40000.0, "OCTANE": 30000.0}
        inventory = {"DIESEL": 25000.0, "PETROL": 20000.0, "OCTANE": 15000.0}
        dispatch_capacity_per_tick = 12000.0

    class DummyRoute:
        id = "route-gazipur-mirpur"
        source_depot_id = "depot-gazipur"
        destination_station_id = "station-mirpur"
        status = "AVAILABLE"
        max_shipment = 5000.0
        transit_ticks = 2

    class DummyState:
        def __init__(self):
            self.tick = tick
            self.sim_run_id = sim_run_id
            self.engine_mode = mode
            self.stale = stale
            self.degraded = degraded
            self.stations = [DummyStation()]
            self.depots = [DummyDepot()]
            self.routes = {"route-gazipur-mirpur": DummyRoute()}
            self.events = []

        def inflight(self):
            return []

        def demand(self):
            return {("station-mirpur", "DIESEL"): [50.0] * 8}

    return DummyState()


async def test_decision_creation_advisory_mode(session_factory, test_sim_run_id):
    publisher = MockPublisher()
    settings = EngineSettings(engine_mode="advisory")
    engine = DecisionEngine(session_factory, publisher=publisher, settings=settings)

    state = _make_dummy_state(tick=10, sim_run_id=test_sim_run_id, mode="advisory", station_inv=2000.0)
    decision = await engine.on_cycle(state)

    assert decision is not None
    assert decision.id is not None
    assert decision.tick == 10
    assert decision.status == "PENDING_APPROVAL"
    assert decision.mode == "advisory"
    assert decision.confidence == 0.9
    assert decision.expires_at_tick == 12
    assert len(decision.lines) > 0

    # Verify idempotency key format: fsip-{sim_run_id}-{decision_id}-{line_no}
    line = decision.lines[0]
    expected_key = f"fsip-{test_sim_run_id}-{decision.id}-1"
    assert line.idempotency_key == expected_key
    assert line.expected_arrival_tick == 12  # tick 10 + transit 2

    # Verify Redis event was emitted
    assert len(publisher.events) == 1
    assert publisher.events[0]["type"] == "decision.created"


async def test_empty_plan_returns_none(session_factory, test_sim_run_id):
    engine = DecisionEngine(session_factory)
    # Station is 95% full -> no need to recommend anything
    state = _make_dummy_state(tick=10, sim_run_id=test_sim_run_id, station_inv=9500.0)
    decision = await engine.on_cycle(state)
    assert decision is None


async def test_autopilot_truth_table_approved(session_factory, test_sim_run_id):
    publisher = MockPublisher()
    settings = EngineSettings(engine_mode="autopilot")
    engine = DecisionEngine(session_factory, publisher=publisher, settings=settings)

    # All safety conditions met -> auto-execute approved
    state = _make_dummy_state(tick=15, sim_run_id=test_sim_run_id, mode="autopilot", station_inv=2000.0)
    decision = await engine.on_cycle(state)

    assert decision is not None
    assert decision.status == "APPROVED"
    assert decision.approved_by == "autopilot"
    assert decision.approved_at is not None


async def test_autopilot_gates_prevent_auto_approval(session_factory, test_sim_run_id):
    settings = EngineSettings(engine_mode="autopilot")
    engine = DecisionEngine(session_factory, settings=settings)

    # 1. Stale state gate
    state_stale = _make_dummy_state(tick=20, sim_run_id=test_sim_run_id, mode="autopilot", stale=True)
    dec_stale = await engine.on_cycle(state_stale)
    assert dec_stale is not None
    assert dec_stale.status == "PENDING_APPROVAL"
    assert dec_stale.approved_by is None

    # 2. Degraded state gate
    state_deg = _make_dummy_state(tick=21, sim_run_id=test_sim_run_id, mode="autopilot", degraded=True)
    dec_deg = await engine.on_cycle(state_deg)
    assert dec_deg is not None
    assert dec_deg.status == "PENDING_APPROVAL"
    assert dec_deg.confidence == 0.5


async def test_supersede_older_decisions(session_factory, test_sim_run_id):
    publisher = MockPublisher()
    engine = DecisionEngine(session_factory, publisher=publisher)

    # Cycle 1: creates decision 1 in PENDING_APPROVAL
    state1 = _make_dummy_state(tick=30, sim_run_id=test_sim_run_id, mode="advisory", station_inv=2000.0)
    dec1 = await engine.on_cycle(state1, force_plan=True)
    assert dec1 is not None
    assert dec1.status == "PENDING_APPROVAL"

    # Cycle 2: creates decision 2 -> dec1 must be superseded (EXPIRED)
    state2 = _make_dummy_state(tick=31, sim_run_id=test_sim_run_id, mode="advisory", station_inv=2000.0)
    dec2 = await engine.on_cycle(state2, force_plan=True)
    assert dec2 is not None

    async with session_factory() as session:
        res = await session.execute(select(Decision).where(Decision.id == dec1.id))
        d1_updated = res.scalar_one()
        assert d1_updated.status == "EXPIRED"
        assert "Superseded" in d1_updated.rejected_reason


async def test_expire_stale_decisions_at_limit(session_factory, test_sim_run_id):
    engine = DecisionEngine(session_factory)
    state = _make_dummy_state(tick=40, sim_run_id=test_sim_run_id, mode="advisory", station_inv=2000.0)
    dec = await engine.on_cycle(state, force_plan=True)
    assert dec is not None
    assert dec.expires_at_tick == 42

    # Advance current tick to 42
    expired = await engine.expire_stale_decisions(current_tick=42, sim_run_id=test_sim_run_id)
    assert dec.id in expired

    async with session_factory() as session:
        res = await session.execute(select(Decision).where(Decision.id == dec.id))
        d_reloaded = res.scalar_one()
        assert d_reloaded.status == "EXPIRED"


async def test_approve_and_reject_flows(session_factory, test_sim_run_id):
    publisher = MockPublisher()
    engine = DecisionEngine(session_factory, publisher=publisher)
    state = _make_dummy_state(tick=50, sim_run_id=test_sim_run_id, mode="advisory", station_inv=2000.0)
    dec = await engine.on_cycle(state, force_plan=True)
    assert dec is not None

    # Operator approves decision
    approved = await engine.approve_decision(dec.id, actor="operator-alice")
    assert approved.status == "APPROVED"
    assert approved.approved_by == "operator-alice"

    # Approving an already approved decision raises DecisionInvalidStateError
    with pytest.raises(DecisionInvalidStateError):
        await engine.approve_decision(dec.id)

    # Non-existent decision raises DecisionNotFoundError
    with pytest.raises(DecisionNotFoundError):
        await engine.approve_decision(999999)

    # Create another decision to reject
    dec2 = await engine.on_cycle(state, force_plan=True)
    assert dec2 is not None
    rejected = await engine.reject_decision(dec2.id, actor="operator-bob", reason="Too much inventory")
    assert rejected.status == "REJECTED"
    assert rejected.rejected_reason == "Too much inventory"


async def test_cannot_approve_expired_decision(session_factory, test_sim_run_id):
    engine = DecisionEngine(session_factory)
    state = _make_dummy_state(tick=60, sim_run_id=test_sim_run_id, mode="advisory", station_inv=2000.0)
    dec = await engine.on_cycle(state, force_plan=True)
    assert dec is not None

    # Try to approve with current tick passed expiry tick (63 > 62)
    state_future = _make_dummy_state(tick=63, sim_run_id=test_sim_run_id)
    with pytest.raises(DecisionExpiredError):
        await engine.approve_decision(dec.id, current_state=state_future)
