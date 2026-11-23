"""Unit tests for services/engine/app/risk/projection.py."""

from app.risk.projection import (
    StationFuelProjection,
    estimate_demand,
    project_station_fuel,
    project_world_state,
)
from fsip_common.models import DemandObservation


def test_estimate_demand_cold_start():
    # When no history is given, fallback is capacity * 0.004
    d = estimate_demand(None, capacity=10000.0)
    assert d == 40.0

    d_empty = estimate_demand([], capacity=25000.0)
    assert d_empty == 100.0


def test_estimate_demand_history_last_8():
    # Given 10 rows, should only average the last 8
    history = [10.0, 10.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0]
    # last 8 are all 100.0
    d = estimate_demand(history, capacity=10000.0)
    assert d == 100.0

    # Test with DemandObservation objects
    obs_list = [
        DemandObservation(
            id=i,
            station_id="station-mirpur",
            fuel_type="DIESEL",
            tick=i,
            sim_time="2026-01-01T00:00:00Z",
            demand_liters=50.0 + i,
            served_liters=50.0 + i,
            unmet_liters=0.0,
        )
        for i in range(12)
    ]
    # last 8 are i = 4 to 11 (values 54 to 61)
    # average = (54+55+56+57+58+59+60+61)/8 = 460 / 8 = 57.5
    d_obs = estimate_demand(obs_list, capacity=10000.0)
    assert d_obs == 57.5


def test_estimate_demand_zero_and_negative_safe():
    d = estimate_demand([0.0, 0.0, 0.0], capacity=10000.0)
    assert d == 0.0

    # Negative inputs should clamp to 0.0
    d_neg = estimate_demand([-50.0, -10.0], capacity=10000.0)
    assert d_neg == 0.0


def test_projection_normal_drain_and_stockout():
    # 1000L current inventory, 100L demand per tick, no arrivals
    proj = StationFuelProjection(
        station_id="station-mirpur",
        fuel_type="DIESEL",
        current_tick=10,
        current_inventory=1000.0,
        capacity=10000.0,
        estimated_demand=100.0,
        inflight_shipments=[],
        horizon_ticks=20,
    )

    # Initial inventory at tick 10
    assert proj.inv_at_tick(10) == 1000.0
    # Step 10 -> 11 consumes 100L
    assert proj.inv_at_tick(11) == 900.0
    assert proj.inv_at_tick(15) == 500.0
    assert proj.inv_at_tick(20) == 0.0

    # Stockout occurs at tick 20 (after 10 ticks)
    assert proj.ticks_to_stockout == 10
    # 10 ticks * 15 min = 150 min = 2.5 hours
    assert proj.hours_to_stockout == 2.5


def test_projection_with_inflight_arrival():
    # 200L inventory, 50L demand per tick
    # At tick 12, an in-flight shipment of 1000L arrives
    proj = StationFuelProjection(
        station_id="station-mirpur",
        fuel_type="DIESEL",
        current_tick=10,
        current_inventory=200.0,
        capacity=5000.0,
        estimated_demand=50.0,
        inflight_shipments=[(12, 1000.0)],
        horizon_ticks=10,
    )

    # Tick 10: 200L
    # Tick 11: 200 - 50 = 150L
    # Tick 12: 150 - 50 = 100L (during tick 12, arrival of 1000L delivers for tick 13)
    # Tick 13: 100 - 50 + 1000 = 1050L
    assert proj.inv_at_tick(10) == 200.0
    assert proj.inv_at_tick(11) == 150.0
    assert proj.inv_at_tick(12) == 100.0
    assert proj.inv_at_tick(13) == 1050.0
    assert proj.inv_at_tick(14) == 1000.0

    # No stockout because shipment arrived before depletion
    assert proj.ticks_to_stockout is None
    assert proj.hours_to_stockout is None

    # Fill percentage with inflight: (200 + 1000) / 5000 = 0.24
    assert proj.fill_pct_with_inflight == 0.24
    assert proj.fill_pct_current == 0.04


def test_projection_overflow_clipping():
    # Station near full: 4500L / 5000L, demand 10L
    # Shipment of 1000L arrives at tick 5
    proj = StationFuelProjection(
        station_id="station-mirpur",
        fuel_type="PETROL",
        current_tick=5,
        current_inventory=4500.0,
        capacity=5000.0,
        estimated_demand=10.0,
        inflight_shipments=[(5, 1000.0)],
        horizon_ticks=5,
    )

    # Step 5 -> 6:
    # After demand: 4500 - 10 = 4490L
    # Incoming: 1000L -> total 5490L
    # Overflow = 490L, capped at 5000L
    assert proj.inv_at_tick(6) == 5000.0
    assert proj.overflow_at_tick(6) == 490.0


def test_projection_outage_halts_consumption():
    # In outage, demand is unmet, inventory does NOT decrease
    proj = StationFuelProjection(
        station_id="station-mirpur",
        fuel_type="OCTANE",
        current_tick=0,
        current_inventory=1000.0,
        capacity=5000.0,
        estimated_demand=100.0,
        inflight_shipments=[],
        is_outage=True,
    )
    assert proj.inv_at_tick(10) == 1000.0
    assert proj.ticks_to_stockout is None


def test_projection_zero_demand():
    proj = StationFuelProjection(
        station_id="station-mirpur",
        fuel_type="DIESEL",
        current_tick=0,
        current_inventory=1000.0,
        capacity=5000.0,
        estimated_demand=0.0,
    )
    assert proj.inv_at_tick(50) == 1000.0
    assert proj.hours_to_stockout is None


def test_projection_immediate_stockout():
    # Current inventory is 0 and demand > 0
    proj = StationFuelProjection(
        station_id="station-mirpur",
        fuel_type="DIESEL",
        current_tick=15,
        current_inventory=0.0,
        capacity=5000.0,
        estimated_demand=50.0,
    )
    assert proj.ticks_to_stockout == 0
    assert proj.hours_to_stockout == 0.0


def test_project_station_fuel_direct():
    station_dict = {
        "id": "station-tongi",
        "status": "OPEN",
        "capacity": {"DIESEL": 12000.0},
        "inventory": {"DIESEL": 6000.0},
    }
    proj = project_station_fuel(
        station=station_dict,
        fuel_type="DIESEL",
        demand_history=[50.0, 50.0],
        inflight=[("station-tongi", "DIESEL", 5, 2000.0)],
        current_tick=0,
    )
    assert proj.station_id == "station-tongi"
    assert proj.current_inventory == 6000.0
    assert proj.estimated_demand == 50.0
    assert proj.fill_pct_with_inflight == (6000.0 + 2000.0) / 12000.0


def test_project_world_state_helper():
    class DummyStation:
        def __init__(self, sid):
            self.id = sid
            self.status = "OPEN"
            self.capacity = type("Cap", (), {"DIESEL": 10000.0, "PETROL": 8000.0, "OCTANE": 5000.0})()
            self.inventory = type("Inv", (), {"DIESEL": 5000.0, "PETROL": 4000.0, "OCTANE": 2500.0})()

    class DummyWorldState:
        def __init__(self):
            self.tick = 25
            self.stations = [DummyStation("st-1"), DummyStation("st-2")]

        def inflight(self):
            return [("st-1", "DIESEL", 27, 2000.0)]

        def demand(self):
            return {("st-1", "DIESEL"): [100.0, 100.0]}

    state = DummyWorldState()
    projections = project_world_state(state)

    assert ("st-1", "DIESEL") in projections
    p1 = projections[("st-1", "DIESEL")]
    assert p1.current_tick == 25
    assert p1.current_inventory == 5000.0
    assert p1.estimated_demand == 100.0
    assert len(p1.inflight_shipments) == 1
    assert p1.fill_pct_with_inflight == 0.7
