"""Test domain SLO service-level error budget enforcement.

Verifies:
- Detection when domain service-level error budget drops below 25% (SL < 0.9925).
- Disabling of autopilot auto-approval (auto_eligible = False, status = PENDING_APPROVAL).
- Generation of operator warning: 'Autopilot frozen by error-budget policy'.
- Clean recovery and re-enabling of autopilot when budget returns to >= 25%.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from app.config import EngineSettings
from app.db.models import Decision, DecisionLine, SimRun
from app.db.session import check_db_health, resolve_database_url
from app.decision.engine import DecisionEngine
from fsip_common.models import SimMetrics
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
            scenario_id="slo-enforcement-test",
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
        return "event-1"


def _make_state(sim_run_id: int, tick: int, service_level: float):
    class DummyStation:
        id = "station-mirpur"
        status = "OPEN"
        capacity = {"DIESEL": 10000.0, "PETROL": 8000.0, "OCTANE": 5000.0}
        inventory = {"DIESEL": 2000.0, "PETROL": 7000.0, "OCTANE": 4500.0}

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
            self.engine_mode = "NORMAL"
            self.operating_mode = "autopilot"
            self.policy = "heuristic"
            self.stale = False
            self.degraded = False
            self.degraded_reasons = set()
            self.stations = [DummyStation()]
            self.depots = [DummyDepot()]
            self.routes = {"route-gazipur-mirpur": DummyRoute()}
            self.events = []
            self.metrics = SimMetrics(
                served_demand_liters=1000.0,
                unmet_demand_liters=round((1.0 - service_level) * 1000.0, 1),
                service_level=service_level,
                allocation_liters=1000.0,
                allocation_failures=0,
            )

        def inflight(self):
            return []

        def demand(self):
            return {("station-mirpur", "DIESEL"): [50.0] * 8}

    return DummyState()


async def test_domain_slo_error_budget_enforcement(session_factory, test_sim_run_id):
    publisher = MockPublisher()
    settings = EngineSettings(engine_mode="autopilot", auto_approve_max_liters=10000)
    engine = DecisionEngine(
        session_factory=session_factory,
        publisher=publisher,
        settings=settings,
    )

    # -------------------------------------------------------------------------
    # CASE 1: Nominal Service Level (1.0 -> 100% budget remaining)
    # -------------------------------------------------------------------------
    state1 = _make_state(test_sim_run_id, tick=10, service_level=1.0)
    dec1 = await engine.on_cycle(state1)
    assert dec1 is not None
    # Nominal: autopilot enabled
    assert dec1.status == "APPROVED"
    assert dec1.approved_by == "autopilot"
    assert "error_budget_exhausted" not in state1.degraded_reasons
    print(f"\n[Case 1 - Nominal SL 100%] Decision status: {dec1.status}, Approved by: {dec1.approved_by}")

    # -------------------------------------------------------------------------
    # CASE 2: Service Level drops to 99.1% -> Error budget remaining = (0.991 - 0.99)/0.01 = 10% (< 25%)
    # -------------------------------------------------------------------------
    state2 = _make_state(test_sim_run_id, tick=11, service_level=0.991)
    dec2 = await engine.on_cycle(state2)
    assert dec2 is not None
    # Budget < 25%: Autopilot disabled!
    assert dec2.status == "PENDING_APPROVAL"
    assert dec2.approved_by is None
    assert "error_budget_exhausted" in state2.degraded_reasons

    # Verify warning in decision explanation
    assert dec2.explanation is not None
    expl = dec2.explanation
    assert "warning" in expl
    assert "Autopilot frozen by error-budget policy" in expl["warning"]
    assert expl["domain_slo_budget_remaining"] == 0.1
    print(f"[Case 2 - SL 99.1% (<25% budget)] Decision status: {dec2.status}, Approved by: {dec2.approved_by}")
    print(f"         Warning: {expl['warning']}")
    print(f"         Budget Remaining: {expl['domain_slo_budget_remaining'] * 100:.1f}%")

    # -------------------------------------------------------------------------
    # CASE 3: Recovery -> Service Level returns to 99.8% -> Error budget remaining = (0.998 - 0.99)/0.01 = 80% (>= 25%)
    # -------------------------------------------------------------------------
    state3 = _make_state(test_sim_run_id, tick=12, service_level=0.998)
    dec3 = await engine.on_cycle(state3)
    assert dec3 is not None
    # Recovered: Autopilot restored
    assert "error_budget_exhausted" not in state3.degraded_reasons
    assert dec3.status == "APPROVED"
    assert dec3.approved_by == "autopilot"
    print(f"[Case 3 - Recovery SL 99.8%] Decision status: {dec3.status}, Approved by: {dec3.approved_by}")
    print("[Success] Domain SLO error budget enforcement verified: detected, frozen, warned, and recovered.")
