"""Unit tests for Phase 10: Engine Resilience Modes, F-BREAKGLASS, and Reconciler."""

import os
import tempfile
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from app.breakglass import BreakglassWriter
from app.decision.engine import DecisionEngine
from app.decision.tracker import AllocationOutcomeTracker
from app.state.mode import EngineModeManager
from app.state.store import WorldState
from fsip_common.models import Depot, Instance, Route, Station


@pytest.fixture
def mock_store():
    store = WorldState(sim_run_id=1, engine_mode="NORMAL")
    store.instance = Instance(
        id=1,
        sim_run_id=1,
        scenario_id="scenario_01",
        scenario_version="1.0.0",
        seed=42,
        tick=10,
        sim_time=datetime.now(UTC),
        tick_minutes=15,
        status="RUNNING",
    )
    store.stations = [
        Station(
            id="ST_01",
            region_id="REG_NORTH",
            name="Station North 1",
            status="OPEN",
            demand_profile="FLAT",
            demand_multiplier=1.0,
            inventory={"DIESEL": 5000.0, "PETROL": 4000.0, "OCTANE": 3000.0},
            capacity={"DIESEL": 10000.0, "PETROL": 10000.0, "OCTANE": 10000.0},
        )
    ]
    store.depots = [
        Depot(
            id="DEPOT_01",
            region_id="REG_NORTH",
            name="Depot North",
            status="OPEN",
            dispatch_capacity_per_tick=10000.0,
            inventory={"DIESEL": 80000.0, "PETROL": 70000.0, "OCTANE": 50000.0},
            capacity={"DIESEL": 100000.0, "PETROL": 100000.0, "OCTANE": 100000.0},
        )
    ]
    store.routes = [
        Route(
            id="ROUTE_01",
            source_depot_id="DEPOT_01",
            destination_station_id="ST_01",
            transit_ticks=2,
            max_shipment=5000.0,
            status="AVAILABLE",
        )
    ]
    return store


def test_error_budget_does_not_latch_console_degraded(mock_store):
    """Cumulative SL dips must freeze Autopilot, not permanently paint the UI DEGRADED."""
    mgr = EngineModeManager()
    mock_store.stale = False
    mock_store.degraded_reasons = {"error_budget_exhausted"}
    mode = mgr.evaluate(mock_store)
    assert mode == "NORMAL"
    assert mock_store.engine_mode == "NORMAL"
    assert mock_store.degraded is False


def test_engine_mode_transitions(mock_store):
    mgr = EngineModeManager()
    assert mgr.current_mode == "NORMAL"

    # 1. Stale data triggers DEGRADED
    mock_store.stale = True
    mode = mgr.evaluate(mock_store)
    assert mode == "DEGRADED"
    assert mock_store.engine_mode == "DEGRADED"

    # 2. Clearing stale requires 2 clean cycles to return to NORMAL
    mock_store.stale = False
    mode = mgr.evaluate(mock_store)
    assert mode == "DEGRADED"  # Cycle 1: still DEGRADED

    mode = mgr.evaluate(mock_store)
    assert mode == "NORMAL"  # Cycle 2: recovered to NORMAL
    assert mock_store.engine_mode == "NORMAL"

    # 3. Breaker open > 60s triggers SAFE_HOLD
    mode = mgr.evaluate(mock_store, sim_breaker_open=True, sim_breaker_open_duration=65.0)
    assert mode == "SAFE_HOLD"
    assert mock_store.engine_mode == "SAFE_HOLD"

    # 4. All routes disrupted triggers NO_FEASIBLE_ROUTES
    for r in mock_store.routes:
        r.status = "DISRUPTED"
    mode = mgr.evaluate(mock_store, sim_breaker_open=False)
    assert mode == "NO_FEASIBLE_ROUTES"
    assert mock_store.engine_mode == "NO_FEASIBLE_ROUTES"


def test_breakglass_writer(mock_store):
    with tempfile.TemporaryDirectory() as tmp_dir:
        writer = BreakglassWriter(breakglass_dir=tmp_dir)

        plan_lines = [
            {
                "source_depot_id": "DEPOT_01",
                "destination_station_id": "ST_01",
                "route_id": "ROUTE_01",
                "fuel_type": "DIESEL",
                "quantity": 4000.0,
                "reason": "Emergency stockout mitigation",
            }
        ]

        csv_path, html_path = writer.write(mock_store, plan_lines=plan_lines, force=True)

        assert csv_path is not None and os.path.exists(csv_path)
        assert html_path is not None and os.path.exists(html_path)

        with open(csv_path, encoding="utf-8") as f:
            csv_text = f.read()
        assert "depot,station,route,fuel,quantity,reason,generated_tick" in csv_text
        assert "DEPOT_01,ST_01,ROUTE_01,DIESEL,4000.0,Emergency stockout mitigation,10" in csv_text

        with open(html_path, encoding="utf-8") as f:
            html_text = f.read()
        assert "FSIP Emergency Break-Glass Dispatch & Station Cover Plan" in html_text
        assert "DEPOT_01" in html_text
        assert "ST_01" in html_text
        assert "DIESEL" in html_text


@pytest.mark.asyncio
async def test_safe_hold_decision_engine(mock_store):
    mock_session = AsyncMock()
    mock_res = MagicMock()
    mock_res.scalars.return_value.all.return_value = []
    mock_session.execute.return_value = mock_res

    mock_sessionmaker = MagicMock()
    mock_sessionmaker.return_value.__aenter__.return_value = mock_session

    engine = DecisionEngine(session_factory=mock_sessionmaker)

    # When engine_mode is SAFE_HOLD, on_cycle must return None and do zero writes
    mock_store.engine_mode = "SAFE_HOLD"
    decision = await engine.on_cycle(mock_store)
    assert decision is None


@pytest.mark.asyncio
async def test_reconcile_on_startup():
    mock_session = AsyncMock()
    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__.return_value = mock_session

    mock_line = MagicMock()
    mock_line.id = 1
    mock_line.idempotency_key = "fsip-1-100-1"
    mock_line.sim_allocation_id = None
    mock_line.status = "SUBMITTED"
    mock_line.quantity = 4000.0

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_line]
    mock_session.execute.return_value = mock_result

    sim_client = MagicMock()
    sim_client.allocations = AsyncMock(
        return_value=[
            {
                "id": 999,
                "idempotency_key": "fsip-1-100-1",
                "status": "ARRIVED",
                "actual_arrival_tick": 12,
                "quantity": 4000.0,
            }
        ]
    )

    tracker = AllocationOutcomeTracker(session_factory=mock_session_factory)
    reconciled = await tracker.reconcile_on_startup(sim_client)

    assert reconciled == 1
    assert mock_line.status == "ARRIVED"
    assert mock_line.sim_allocation_id == 999
    assert mock_line.actual_arrival_tick == 12
