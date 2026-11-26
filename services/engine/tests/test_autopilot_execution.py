"""End-to-end Autopilot functionality, eligibility gates, and safety verification tests.

Complies with:
- 20_FINAL_SPEC_LOCK.md §5
- 05_INTELLIGENCE_DESIGN.md §4.5
- 21_API_CONTRACTS.md §2.8
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from app.config import EngineSettings
from app.db.models import AuditLog, Decision, DecisionLine, SimRun
from app.db.session import check_db_health, resolve_database_url
from app.decision.engine import DecisionEngine
from app.decision.executor import Executor
from app.sim.client import SimClient, SimResult
from app.state.store import WorldState
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


class MockPublisher:
    def __init__(self):
        self.events = []

    async def event(self, type: str, data: dict, sim_run_id: int | None = None, tick: int | None = None):
        self.events.append({"type": type, "data": data, "sim_run_id": sim_run_id, "tick": tick})
        return "event-id-1"

    async def publish_state(self, store: WorldState):
        pass


def _make_autopilot_state(
    sim_run_id: int,
    tick: int = 10,
    operating_mode: str = "autopilot",
    stale: bool = False,
    degraded: bool = False,
    degraded_reasons: set | None = None,
    engine_mode: str = "NORMAL",
    critical_dq_alert: bool = False,
    service_level: float = 1.0,
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

    class DummyMetrics:
        def __init__(self, sl: float):
            self.served_demand_liters = 1000.0
            self.unmet_demand_liters = round((1.0 - sl) * 1000.0, 1)
            self.service_level = sl
            self.allocation_liters = 1000.0
            self.allocation_failures = 0

    class DummyState:
        def __init__(self):
            self.tick = tick
            self.sim_run_id = sim_run_id
            self.operating_mode = operating_mode
            self.engine_mode = engine_mode
            self.policy = "heuristic"
            self.stale = stale
            self.degraded = degraded
            self.degraded_reasons = set(degraded_reasons or set())
            self.has_critical_data_quality_alert = critical_dq_alert
            self.stations = [DummyStation()]
            self.depots = [DummyDepot()]
            self.routes = {"route-gazipur-mirpur": DummyRoute()}
            self.events = []
            self.metrics = DummyMetrics(service_level)

        def inflight(self):
            return []

        def demand(self):
            return {("station-mirpur", "DIESEL"): [50.0] * 8}

    return DummyState()


@pytest_asyncio.fixture
async def autopilot_sim_run(session_factory):
    async with session_factory() as session:
        run = SimRun(
            scenario_id="autopilot-test-run",
            started_at=datetime.now(UTC),
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
        await session.execute(delete(AuditLog).where(AuditLog.actor == "autopilot"))
        await session.execute(delete(SimRun).where(SimRun.id == run_id))
        await session.commit()


@pytest.mark.asyncio
async def test_autopilot_full_eligible_auto_execution(session_factory, autopilot_sim_run):
    """Eligible recommendation in Autopilot mode auto-approves and executes immediately."""
    publisher = MockPublisher()
    settings = EngineSettings(engine_mode="autopilot")
    engine = DecisionEngine(session_factory, publisher=publisher, settings=settings)

    mock_sim = AsyncMock(spec=SimClient)
    mock_sim.create_allocation.return_value = SimResult(
        data={"id": 8801, "status": "SUBMITTED"},
        stale=False,
        status=201,
        latency_ms=12.0,
    )
    executor = Executor(sim_client=mock_sim, session_factory=session_factory, publisher=publisher)
    engine.executor = executor

    state = _make_autopilot_state(autopilot_sim_run, tick=10, operating_mode="autopilot")
    dec = await engine.on_cycle(state, force_plan=True)

    assert dec is not None
    assert dec.status in ("APPROVED", "EXECUTED")
    assert dec.approved_by == "autopilot"
    assert dec.approved_at is not None

    # Verify simulator create_allocation was called with correct payload
    mock_sim.create_allocation.assert_called_once()
    call_args = mock_sim.create_allocation.call_args[0][0]
    assert call_args["source_depot_id"] == "depot-gazipur"
    assert call_args["destination_station_id"] == "station-mirpur"
    assert call_args["quantity"] <= 7000.0

    # Verify audit log entry was created for automatic execution
    async with session_factory() as session:
        logs = (await session.execute(
            select(AuditLog).where(AuditLog.actor == "autopilot")
        )).scalars().all()
        assert len(logs) >= 1
        actions = [log.action for log in logs]
        assert "DECISION_AUTO_APPROVED" in actions or "ALLOCATION_EXECUTED" in actions


@pytest.mark.asyncio
async def test_autopilot_ineligible_safety_cases(session_factory, autopilot_sim_run):
    """Verify that all safety edge cases keep recommendation as PENDING_APPROVAL with clear blocked reasons."""
    publisher = MockPublisher()
    settings = EngineSettings(engine_mode="autopilot")
    engine = DecisionEngine(session_factory, publisher=publisher, settings=settings)

    # 1. Stale data
    state_stale = _make_autopilot_state(autopilot_sim_run, tick=11, stale=True)
    dec_stale = await engine.on_cycle(state_stale, force_plan=True)
    assert dec_stale is not None
    assert dec_stale.status == "PENDING_APPROVAL"
    assert "stale" in dec_stale.explanation.get("autopilot_blocked_reason", "").lower()

    # 2. Degraded state
    state_deg = _make_autopilot_state(autopilot_sim_run, tick=12, degraded=True, degraded_reasons={"sim_latency_high"})
    dec_deg = await engine.on_cycle(state_deg, force_plan=True)
    assert dec_deg is not None
    assert dec_deg.status == "PENDING_APPROVAL"
    assert "degraded" in dec_deg.explanation.get("autopilot_blocked_reason", "").lower()

    # 3. Critical Data Quality alert
    state_dq = _make_autopilot_state(autopilot_sim_run, tick=13, critical_dq_alert=True)
    dec_dq = await engine.on_cycle(state_dq, force_plan=True)
    assert dec_dq is not None
    assert dec_dq.status == "PENDING_APPROVAL"
    assert "data-quality" in dec_dq.explanation.get("autopilot_blocked_reason", "").lower()

    # 4. Engine mode non-NORMAL (e.g. SAFE_HOLD)
    state_safe = _make_autopilot_state(autopilot_sim_run, tick=14, engine_mode="SAFE_HOLD")
    dec_safe = await engine.on_cycle(state_safe, force_plan=True)
    # SAFE_HOLD suppresses planning cycle or stays pending
    if dec_safe is not None:
        assert dec_safe.status == "PENDING_APPROVAL"

    # 5. Advisory operating mode
    state_adv = _make_autopilot_state(autopilot_sim_run, tick=15, operating_mode="advisory")
    dec_adv = await engine.on_cycle(state_adv, force_plan=True)
    assert dec_adv is not None
    assert dec_adv.status == "PENDING_APPROVAL"


@pytest.mark.asyncio
async def test_large_decision_approves_with_single_operator_click(session_factory, autopilot_sim_run):
    """Large allocations (>7k L) approve with one operator click — no Sign-In / four-eyes."""
    publisher = MockPublisher()
    engine = DecisionEngine(session_factory, publisher=publisher)

    async with session_factory() as session:
        dec = Decision(
            sim_run_id=autopilot_sim_run,
            tick=20,
            policy="optimizer",
            status="PENDING_APPROVAL",
            mode="advisory",
            confidence=0.9,
            degraded=False,
            created_at=datetime.now(UTC),
        )
        session.add(dec)
        await session.flush()

        line = DecisionLine(
            decision_id=dec.id,
            line_no=1,
            source_depot_id="depot-gazipur",
            destination_station_id="station-mirpur",
            route_id="route-gazipur-mirpur",
            fuel_type="DIESEL",
            quantity=8000.0,
            idempotency_key=f"fsip-4eyes-{dec.id}-1",
            status="PLANNED",
            created_at=datetime.now(UTC),
        )
        session.add(line)
        await session.commit()
        dec_id = dec.id

    approved = await engine.approve_decision(dec_id, actor="operator", reviewed_driver=True)
    assert approved.status == "APPROVED"
    assert approved.approved_by == "operator"
