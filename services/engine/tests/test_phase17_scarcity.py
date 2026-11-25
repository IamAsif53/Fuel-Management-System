"""Phase 17 Scarcity Rationing Policy Test Suite.

Verifies:
1. Scarcity activation threshold (days_cover < 3.0 or tick >= 212).
2. Max-min fairness formulation (protecting lowest-coverage stations).
3. Disrupted route avoidance during scarcity.
4. Determinism of scarcity allocations.
5. Infeasibility fallback to heuristic.
"""
from __future__ import annotations

import copy
import pytest

from app.decision.constraints import check_plan
from app.decision.optimizer import OptimizerPolicy
from app.state.store import WorldState
from fsip_common.models import Depot, Instance, Route, Station


def _build_test_store(
    tick: int = 100,
    depot_diesel: float = 50000.0,
    station_invs: dict[str, float] | None = None,
    route_disrupted: bool = False,
) -> WorldState:
    store = WorldState(sim_run_id=101)
    store.instance = Instance(
        id=1,
        scenario_id="scenario_17",
        scenario_version="1.0",
        seed=42,
        tick=tick,
        tick_minutes=15,
        sim_time="2026-11-25T12:00:00Z",
        status="RUNNING",
    )

    if station_invs is None:
        station_invs = {"st-1": 1500.0, "st-2": 8000.0}

    store.stations = {
        "st-1": Station(
            id="st-1",
            name="Station Urban 1",
            region_id="reg-dhaka",
            location={"lat": 23.8, "lon": 90.4},
            capacity={"DIESEL": 20000.0, "PETROL": 15000.0, "OCTANE": 10000.0},
            inventory={"DIESEL": station_invs.get("st-1", 1500.0), "PETROL": 5000.0, "OCTANE": 3000.0},
            demand_profile="urban",
            demand_multiplier=1.0,
            status="OPEN",
        ),
        "st-2": Station(
            id="st-2",
            name="Station Highway 2",
            region_id="reg-dhaka",
            location={"lat": 23.9, "lon": 90.5},
            capacity={"DIESEL": 25000.0, "PETROL": 15000.0, "OCTANE": 10000.0},
            inventory={"DIESEL": station_invs.get("st-2", 8000.0), "PETROL": 6000.0, "OCTANE": 4000.0},
            demand_profile="highway",
            demand_multiplier=1.0,
            status="OPEN",
        ),
    }

    store.depots = {
        "dp-1": Depot(
            id="dp-1",
            name="Primary Depot 1",
            region_id="reg-dhaka",
            location={"lat": 23.7, "lon": 90.3},
            capacity={"DIESEL": 100000.0, "PETROL": 80000.0, "OCTANE": 50000.0},
            inventory={"DIESEL": depot_diesel, "PETROL": 50000.0, "OCTANE": 30000.0},
            dispatch_capacity_per_tick=15000.0,
            status="OPEN",
        )
    }

    store.routes = {
        "r-1": Route(
            id="r-1",
            source_depot_id="dp-1",
            destination_station_id="st-1",
            transit_ticks=3,
            max_shipment=5000.0,
            fuel_types=["DIESEL", "PETROL", "OCTANE"],
            cost_per_liter=0.05,
            status="DISRUPTED" if route_disrupted else "AVAILABLE",
        ),
        "r-2": Route(
            id="r-2",
            source_depot_id="dp-1",
            destination_station_id="st-2",
            transit_ticks=4,
            max_shipment=5000.0,
            fuel_types=["DIESEL", "PETROL", "OCTANE"],
            cost_per_liter=0.06,
            status="AVAILABLE",
        ),
    }
    return store


def test_scarcity_activation_by_days_cover() -> None:
    """Days cover < 3.0 triggers scarcity active even before tick 212."""
    opt = OptimizerPolicy(time_limit=3)
    # Total diesel inv = 1000 + 2000 + 2000 = 5000 L
    # Over 96 ticks, demand will exceed 5000 / 3 => cover < 3 days
    store = _build_test_store(tick=50, depot_diesel=2000.0, station_invs={"st-1": 1000.0, "st-2": 2000.0})
    plan = opt.solve(store)

    assert plan.scarcity_active.get("DIESEL") is True, f"Expected DIESEL scarcity active, got {plan.scarcity_active}"


def test_scarcity_activation_by_tick_threshold() -> None:
    """Current tick >= 212 triggers scarcity active regardless of inventory."""
    opt = OptimizerPolicy(time_limit=3)
    store = _build_test_store(tick=212, depot_diesel=80000.0)
    plan = opt.solve(store)

    assert plan.scarcity_active.get("DIESEL") is True, "Expected tick 212 to activate scarcity mode"


def test_scarcity_max_min_fairness_protects_critical_station() -> None:
    """Max-min fairness prioritizes the most vulnerable station to balance coverage."""
    opt = OptimizerPolicy(time_limit=3)
    # st-1 has only 500 L remaining (critically low), st-2 has 12,000 L
    store = _build_test_store(tick=215, depot_diesel=3000.0, station_invs={"st-1": 500.0, "st-2": 12000.0})
    plan = opt.solve(store)

    assert plan.solver_status in ("Optimal", "Heuristic")
    diesel_lines = [ln for ln in plan.lines if ln.fuel_type == "DIESEL"]
    if diesel_lines:
        destinations = {ln.destination_station_id for ln in diesel_lines}
        assert "st-1" in destinations, "Critically depleted station st-1 must receive rationing allocation"


def test_scarcity_respects_route_disruptions() -> None:
    """Scarcity optimizer never routes shipments across blocked or disrupted routes."""
    opt = OptimizerPolicy(time_limit=3)
    store = _build_test_store(tick=220, depot_diesel=4000.0, route_disrupted=True)
    plan = opt.solve(store)

    for ln in plan.lines:
        assert ln.route_id != "r-1", f"Disrupted route r-1 must not be selected under scarcity: {ln}"


def test_scarcity_decisions_are_deterministic() -> None:
    """Identical state inputs produce strictly identical allocation decisions under scarcity."""
    opt1 = OptimizerPolicy(time_limit=3)
    opt2 = OptimizerPolicy(time_limit=3)
    store1 = _build_test_store(tick=215, depot_diesel=3000.0, station_invs={"st-1": 600.0, "st-2": 5000.0})
    store2 = copy.deepcopy(store1)

    plan1 = opt1.solve(store1)
    plan2 = opt2.solve(store2)

    assert len(plan1.lines) == len(plan2.lines)
    for l1, l2 in zip(plan1.lines, plan2.lines):
        assert l1.route_id == l2.route_id
        assert l1.fuel_type == l2.fuel_type
        assert abs(l1.quantity - l2.quantity) < 1e-4


def test_scarcity_fallback_on_solver_failure() -> None:
    """When solver is forced to fail under scarcity, it falls back to heuristic cleanly."""
    opt = OptimizerPolicy(time_limit=3)
    store = _build_test_store(tick=225, depot_diesel=2000.0)
    plan = opt.solve(store, force_fail=True)

    assert plan.is_fallback is True
    assert plan.solver_status == "Heuristic"
    assert "OPTIMIZER_FORCE_FAIL" in (plan.fallback_reason or "")
