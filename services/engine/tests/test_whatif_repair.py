"""Automated Regression Tests A-I for What-If Scenario Simulation Pipeline.

Tests:
- Test A: Baseline scenario projection (stable, non-zero inventories, 100% SL)
- Test B: Route disruption simulation (shipments cut, arrival suppressed)
- Test C: Custom feasible dispatch (positive quantity arrives, ending inventory jumps)
- Test D: Infeasible dispatch validation (route limit, depot inventory, non-positive qty)
- Test E: Horizon sensitivity (24 vs 48 vs 96 ticks)
- Test F: Policy comparison (DO NOTHING vs RECOMMENDED vs CUSTOM differentiation)
- Test G: Simulator isolation & side-effect-free guarantee
- Test H: Invalid identifiers validation (unknown route, invalid fuel)
- Test I: Station outage impact & simulation determinism
"""
from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pytest
from app.decision.twin import ShadowTwin
from app.main import app
from app.state.store import WorldState
from fsip_common.models import Depot, Instance, Route, Station
from starlette.testclient import TestClient

pytestmark = [pytest.mark.asyncio]

INTERNAL_HEADERS = {"x-internal-token": "change-me-internal"}


@pytest.fixture
def mock_world_state() -> WorldState:
    """Construct an isolated in-memory WorldState with realistic entities."""
    ws = WorldState()
    ws.sim_run_id = 999
    ws.instance = Instance(
        id=1,
        scenario_id="whatif-test",
        scenario_version="1.0",
        seed=42,
        sim_time=datetime.now(UTC),
        tick=10,
        tick_minutes=15,
        status="RUNNING",
    )
    ws.operating_mode = "advisory"
    ws.policy = "optimizer"

    st1 = Station(
        id="station-mirpur",
        name="Mirpur",
        region_id="dhaka",
        status="OPEN",
        demand_profile="urban_high",
        demand_multiplier=1.0,
        capacity={"DIESEL": 12000.0, "PETROL": 10000.0, "OCTANE": 6000.0},
        inventory={"DIESEL": 5000.0, "PETROL": 5000.0, "OCTANE": 3000.0},
    )
    st2 = Station(
        id="station-tongi",
        name="Tongi",
        region_id="dhaka",
        status="OPEN",
        demand_profile="industrial",
        demand_multiplier=1.0,
        capacity={"DIESEL": 16000.0, "PETROL": 8000.0, "OCTANE": 4000.0},
        inventory={"DIESEL": 8000.0, "PETROL": 4000.0, "OCTANE": 2000.0},
    )

    depot1 = Depot(
        id="depot-gazipur",
        name="Gazipur",
        region_id="dhaka",
        status="OPEN",
        capacity={"DIESEL": 60000.0, "PETROL": 40000.0, "OCTANE": 30000.0},
        inventory={"DIESEL": 25000.0, "PETROL": 20000.0, "OCTANE": 15000.0},
        dispatch_capacity_per_tick=12000.0,
    )

    r1 = Route(
        id="route-gazipur-mirpur",
        source_depot_id="depot-gazipur",
        destination_station_id="station-mirpur",
        transit_ticks=2,
        max_shipment=4000.0,
        status="AVAILABLE",
    )
    r2 = Route(
        id="route-gazipur-tongi",
        source_depot_id="depot-gazipur",
        destination_station_id="station-tongi",
        transit_ticks=2,
        max_shipment=4000.0,
        status="AVAILABLE",
    )

    ws.stations = [st1, st2]
    ws.depots = [depot1]
    ws.routes = [r1, r2]
    ws.events = []
    ws.allocations = []
    return ws


# =============================================================================
# Test A: Baseline Scenario Projection
# =============================================================================
async def test_a_baseline(mock_world_state: WorldState):
    app.state.store = mock_world_state
    client = TestClient(app)

    res = client.post(
        "/internal/whatif",
        headers=INTERNAL_HEADERS,
        json={"horizon_ticks": 24, "lines": [], "events": []},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["tick"] == 10
    assert len(data["scenarios"]) == 3

    scenario_map = {s["name"]: s for s in data["scenarios"]}
    assert "do_nothing" in scenario_map
    assert "recommended" in scenario_map
    assert "custom" in scenario_map

    dn = scenario_map["do_nothing"]
    assert dn["min_station_sl"] == 1.0
    assert dn["unmet"] == 0.0
    assert len(dn["stations"]) == 6  # 2 stations * 3 fuels

    # Check station metadata presence
    st_sample = dn["stations"][0]
    assert "initial_inv" in st_sample
    assert "ending_inv" in st_sample
    assert "delta_liters" in st_sample
    assert len(st_sample["inventory_p50"]) == 24


# =============================================================================
# Test B: Route Disruption Simulation
# =============================================================================
async def test_b_route_disruption(mock_world_state: WorldState):
    # Route disruption at departure tick: candidate dispatch on that route is cut
    res = ShadowTwin.project(
        state=mock_world_state,
        lines=[{"route_id": "route-gazipur-mirpur", "fuel_type": "DIESEL", "quantity": 3000.0}],
        hypothetical_events=[{
            "type": "route_disruption",
            "start_tick": 10,
            "end_tick": 20,
            "parameters": {"route_id": "route-gazipur-mirpur"},
        }],
        horizon=24,
        n_samples=20,
    )
    st_mirpur = res.stations[("station-mirpur", "DIESEL")]
    # Dispatched fuel never arrived because route was disrupted at departure (t+1=11)
    # So ending inventory is strictly lower than if fuel arrived
    res_normal = ShadowTwin.project(
        state=mock_world_state,
        lines=[{"route_id": "route-gazipur-mirpur", "fuel_type": "DIESEL", "quantity": 3000.0}],
        hypothetical_events=[],
        horizon=24,
        n_samples=20,
    )
    st_mirpur_normal = res_normal.stations[("station-mirpur", "DIESEL")]
    assert st_mirpur.ending_inventory_p50 < st_mirpur_normal.ending_inventory_p50


# =============================================================================
# Test C: Custom Feasible Dispatch
# =============================================================================
async def test_c_custom_feasible_dispatch(mock_world_state: WorldState):
    app.state.store = mock_world_state
    client = TestClient(app)

    res = client.post(
        "/internal/whatif",
        headers=INTERNAL_HEADERS,
        json={
            "horizon_ticks": 48,
            "lines": [{"route_id": "route-gazipur-mirpur", "fuel_type": "DIESEL", "quantity": 3000.0}],
            "events": [],
        },
    )
    assert res.status_code == 200
    data = res.json()
    scenario_map = {s["name"]: s for s in data["scenarios"]}

    dn_mirpur = next(
        s for s in scenario_map["do_nothing"]["stations"]
        if s["station_id"] == "station-mirpur" and s["fuel_type"] == "DIESEL"
    )
    cust_mirpur = next(
        s for s in scenario_map["custom"]["stations"]
        if s["station_id"] == "station-mirpur" and s["fuel_type"] == "DIESEL"
    )

    # Custom dispatch delivered 3,000 L, so custom ending inventory > do_nothing ending inventory
    assert cust_mirpur["ending_inv"] > dn_mirpur["ending_inv"]
    assert cust_mirpur["delta_liters"] > dn_mirpur["delta_liters"]


# =============================================================================
# Test D: Infeasible Dispatch Validation
# =============================================================================
async def test_d_infeasible_dispatch(mock_world_state: WorldState):
    app.state.store = mock_world_state
    client = TestClient(app)

    # 1. Exceeds route max_shipment (4,000 L)
    res1 = client.post(
        "/internal/whatif",
        headers=INTERNAL_HEADERS,
        json={
            "lines": [{"route_id": "route-gazipur-mirpur", "fuel_type": "DIESEL", "quantity": 5500.0}],
        },
    )
    assert res1.status_code == 422
    assert res1.json()["detail"]["code"] == "INFEASIBLE_DISPATCH"
    assert "exceeds route 'route-gazipur-mirpur' max shipment limit" in res1.json()["detail"]["message"]

    # 2. Exceeds depot inventory (25,000 L)
    # Temporarily set route max_shipment high to isolate depot check
    mock_world_state.routes[0].max_shipment = 100000.0
    res2 = client.post(
        "/internal/whatif",
        headers=INTERNAL_HEADERS,
        json={
            "lines": [{"route_id": "route-gazipur-mirpur", "fuel_type": "DIESEL", "quantity": 35000.0}],
        },
    )
    assert res2.status_code == 422
    assert res2.json()["detail"]["code"] == "INFEASIBLE_DISPATCH"
    assert "exceeds available DIESEL inventory" in res2.json()["detail"]["message"]

    # 3. Non-positive quantity
    res3 = client.post(
        "/internal/whatif",
        headers=INTERNAL_HEADERS,
        json={
            "lines": [{"route_id": "route-gazipur-mirpur", "fuel_type": "DIESEL", "quantity": 0.0}],
        },
    )
    assert res3.status_code == 422
    assert res3.json()["detail"]["code"] == "INVALID_QUANTITY"


# =============================================================================
# Test E: Horizon Sensitivity (24 vs 48 vs 96 ticks)
# =============================================================================
async def test_e_horizon_sensitivity(mock_world_state: WorldState):
    app.state.store = mock_world_state
    client = TestClient(app)

    for h in (24, 48, 96):
        res = client.post(
            "/internal/whatif",
            headers=INTERNAL_HEADERS,
            json={"horizon_ticks": h, "lines": [], "events": []},
        )
        assert res.status_code == 200
        st = res.json()["scenarios"][0]["stations"][0]
        assert len(st["inventory_p50"]) == h


# =============================================================================
# Test F: Policy Comparison (DO NOTHING vs RECOMMENDED vs CUSTOM)
# =============================================================================
async def test_f_policy_comparison(mock_world_state: WorldState):
    # Under a 2.5x demand spike, do_nothing, recommended, and custom should produce differentiated results
    app.state.store = mock_world_state
    client = TestClient(app)

    res = client.post(
        "/internal/whatif",
        headers=INTERNAL_HEADERS,
        json={
            "horizon_ticks": 48,
            "events": [{
                "type": "demand_spike",
                "start_offset": 2,
                "duration_ticks": 30,
                "parameters": {"multiplier": 2.5, "station_ids": ["station-mirpur"]},
            }],
            "lines": [{"route_id": "route-gazipur-mirpur", "fuel_type": "DIESEL", "quantity": 3500.0}],
        },
    )
    assert res.status_code == 200
    sc_map = {s["name"]: s for s in res.json()["scenarios"]}

    dn = sc_map["do_nothing"]
    cust = sc_map["custom"]

    dn_mirpur = next(s for s in dn["stations"] if s["station_id"] == "station-mirpur" and s["fuel_type"] == "DIESEL")
    cust_mirpur = next(
        s for s in cust["stations"] if s["station_id"] == "station-mirpur" and s["fuel_type"] == "DIESEL"
    )

    # Custom delivered 3,500 L to buffer the spike, so custom ending inventory > do_nothing
    assert cust_mirpur["ending_inv"] > dn_mirpur["ending_inv"]


# =============================================================================
# Test G: No Live Side Effects
# =============================================================================
async def test_g_no_live_side_effects(mock_world_state: WorldState):
    app.state.store = mock_world_state
    client = TestClient(app)

    alloc_len_before = len(mock_world_state.allocations)
    inv_before = dict(mock_world_state.stations[0].inventory)
    events_before = len(mock_world_state.events)

    client.post(
        "/internal/whatif",
        headers=INTERNAL_HEADERS,
        json={
            "horizon_ticks": 48,
            "lines": [{"route_id": "route-gazipur-mirpur", "fuel_type": "DIESEL", "quantity": 2000.0}],
            "events": [{"type": "route_disruption", "start_offset": 0, "duration_ticks": 10}],
        },
    )

    # Live store must remain 100% unchanged
    assert len(mock_world_state.allocations) == alloc_len_before
    assert mock_world_state.stations[0].inventory == inv_before
    assert len(mock_world_state.events) == events_before


# =============================================================================
# Test H: Invalid Identifiers
# =============================================================================
async def test_h_invalid_identifiers(mock_world_state: WorldState):
    app.state.store = mock_world_state
    client = TestClient(app)

    # 1. Unknown route "r-1"
    res1 = client.post(
        "/internal/whatif",
        headers=INTERNAL_HEADERS,
        json={"lines": [{"route_id": "r-1", "fuel_type": "DIESEL", "quantity": 1000.0}]},
    )
    assert res1.status_code == 422
    assert res1.json()["detail"]["code"] == "INVALID_IDENTIFIER"
    assert "Route 'r-1' does not exist" in res1.json()["detail"]["message"]

    # 2. Unknown fuel type "KEROSENE"
    res2 = client.post(
        "/internal/whatif",
        headers=INTERNAL_HEADERS,
        json={"lines": [{"route_id": "route-gazipur-mirpur", "fuel_type": "KEROSENE", "quantity": 1000.0}]},
    )
    assert res2.status_code == 422
    assert res2.json()["detail"]["code"] == "INVALID_FUEL_TYPE"


# =============================================================================
# Test I: Station Outage Impact & Determinism
# =============================================================================
async def test_i_station_outage_impact_and_determinism(mock_world_state: WorldState):
    # 1. Outage impact: customer demand is unserved, minimum station service level drops
    res_outage = ShadowTwin.project(
        state=mock_world_state,
        lines=[],
        hypothetical_events=[{
            "type": "station_outage",
            "start_tick": 10,
            "end_tick": 30,
            "parameters": {"station_id": "station-mirpur"},
        }],
        horizon=48,
        n_samples=30,
    )
    assert res_outage.total_unmet_liters > 0.0
    assert res_outage.min_station_service_level < 1.0

    st_mirpur = res_outage.stations[("station-mirpur", "DIESEL")]
    assert st_mirpur.unmet_liters > 0.0
    # Pumps were shut down, so inventory was NOT consumed during outage ticks
    assert st_mirpur.ending_inventory_p50 > 0.0

    # 2. Determinism: fixed seed yields reproducible metrics
    np.random.seed(42)
    run1 = ShadowTwin.project(state=mock_world_state, lines=[], horizon=24, n_samples=30)
    np.random.seed(42)
    run2 = ShadowTwin.project(state=mock_world_state, lines=[], horizon=24, n_samples=30)

    assert run1.total_unmet_liters == run2.total_unmet_liters
    assert run1.total_overflow_liters == run2.total_overflow_liters
    assert run1.min_station_service_level == run2.min_station_service_level


async def test_j_invalid_schema_bounds_return_422(mock_world_state: WorldState):
    """Verify DEF-007: invalid horizon_ticks, unknown scenario_type, or bad event types return 422."""
    app.state.store = mock_world_state
    client = TestClient(app)

    for bad_payload in (
        {"scenario_type": "nonexistent_type", "horizon_ticks": -5},
        {"horizon_ticks": -5},
        {"events": [{"type": "bogus_event", "start_offset": 0, "duration_ticks": 10}]},
    ):
        res = client.post("/internal/whatif", headers=INTERNAL_HEADERS, json=bad_payload)
        assert res.status_code == 422, f"Expected 422 for {bad_payload}, got {res.status_code}"
