"""Unit tests for Task 6.3: Risk persistence and state integration."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from app.db.buffer import WriteBuffer
from app.risk.stockout import RiskResult
from app.state.persistence import StatePersistence
from app.state.store import WorldState
from fsip_common.models import Depot, Instance, Station


def test_state_to_public_json_contains_station_risk_and_depot_supply():
    """Verify that WorldState.to_public_json() surfaces station risk and depot projected_days_of_supply."""
    store = WorldState()
    store.instance = Instance(
        id=1,
        scenario_id="baseline",
        scenario_version="1.0.0",
        seed=1,
        tick_minutes=15,
        status="RUNNING",
        sim_time="2026-01-01T00:00:00Z",
        tick=10,
    )
    store.stations = [
        Station(
            id="station-mirpur",
            name="Mirpur Station",
            region_id="region-dhaka",
            demand_profile="urban_high",
            demand_multiplier=1.0,
            status="OPEN",
            capacity={"DIESEL": 10000.0, "PETROL": 10000.0, "OCTANE": 10000.0},
            inventory={"DIESEL": 2000.0, "PETROL": 5000.0, "OCTANE": 5000.0},
        )
    ]
    store.depots = [
        Depot(
            id="depot-gazipur",
            name="Gazipur Depot",
            region_id="region-dhaka",
            status="OPEN",
            dispatch_capacity_per_tick=10000.0,
            capacity={"DIESEL": 50000.0, "PETROL": 50000.0, "OCTANE": 50000.0},
            inventory={"DIESEL": 30000.0, "PETROL": 30000.0, "OCTANE": 30000.0},
        )
    ]

    # Populate risk and depot_risk
    store.risk["station-mirpur"] = {
        "DIESEL": {
            "level": "CRITICAL",
            "hours_to_stockout": 4.5,
            "stockout_prob_lead": 0.65,
            "stockout_prob_horizon": 0.85,
            "inventory": 2000.0,
            "inflight": 0.0,
            "fill_pct": 0.20,
            "fill_pct_with_inflight": 0.20,
            "cover_hours": 4.5,
            "drivers": ["stockout_within_lead_time"],
            "forecast_model": "seasonal@builtin",
        }
    }
    store.depot_risk[("depot-gazipur", "DIESEL")] = {
        "projected_days_of_supply": 4.2,
        "hours_to_depletion": 36.0,
        "hours_to_overflow": None,
        "waste_liters": 0.0,
        "cover_hours": 36.0,
    }

    public_json = store.to_public_json()

    # Station verification
    stations = public_json["stations"]
    assert len(stations) == 1
    st = stations[0]
    assert st["id"] == "station-mirpur"
    assert "risk" in st
    assert st["risk"]["DIESEL"]["level"] == "CRITICAL"
    assert st["risk"]["DIESEL"]["stockout_prob_lead"] == 0.65

    # Depot verification
    depots = public_json["depots"]
    assert len(depots) == 1
    d = depots[0]
    assert d["id"] == "depot-gazipur"
    assert "projected_days_of_supply" in d
    assert d["projected_days_of_supply"]["DIESEL"] == 4.2


@pytest.mark.asyncio
async def test_risk_persistence_level_change_and_cadence():
    """Verify that StatePersistence persists risk rows on level changes and every 4 ticks."""
    write_buffer = MagicMock(spec=WriteBuffer)
    persistence = StatePersistence(write_buffer=write_buffer)

    store = WorldState()
    store.instance = Instance(
        id=1,
        scenario_id="baseline",
        scenario_version="1.0.0",
        seed=1,
        tick_minutes=15,
        status="RUNNING",
        sim_time="2026-01-01T00:00:00Z",
        tick=1,  # Not multiple of 4
    )

    r_result = RiskResult(
        station_id="station-mirpur",
        fuel_type="DIESEL",
        tick=1,
        risk_level="HIGH",
        hours_to_stockout=5.0,
        stockout_prob_lead=0.2,
        stockout_prob_horizon=0.7,
        inventory=1500.0,
        inflight=0.0,
        capacity=10000.0,
        fill_pct=0.15,
        fill_pct_with_inflight=0.15,
        cover_hours=5.0,
        lead_ticks=5,
        lead_time_hours=1.25,
        drivers=["low_fill_level"],
    )
    store.risk_results = {("station-mirpur", "DIESEL"): r_result}

    # 1. First cycle (last_risk_persist_tick < 0): should enqueue risk
    persistence.persist_cycle(store)

    assert write_buffer.submit.call_count >= 2  # snapshot and risk
    calls = [call.kwargs.get("kind") for call in write_buffer.submit.call_args_list]
    assert "risk" in calls

    # Simulate commit callback
    commit_callbacks = [
        call.kwargs.get("on_commit")
        for call in write_buffer.submit.call_args_list
        if call.kwargs.get("kind") == "risk"
    ]
    for cb in commit_callbacks:
        if cb:
            cb()

    assert persistence.last_risk_persist_tick == 1
    assert persistence.last_persisted_risk_levels[("station-mirpur", "DIESEL")] == "HIGH"

    # 2. Cycle tick=2 with NO level change and tick%4 != 0: should NOT enqueue risk
    write_buffer.submit.reset_mock()
    store.instance.tick = 2
    persistence.persist_cycle(store)
    calls2 = [call.kwargs.get("kind") for call in write_buffer.submit.call_args_list]
    assert "risk" not in calls2

    # 3. Cycle tick=3 with a level change to CRITICAL: should enqueue risk!
    write_buffer.submit.reset_mock()
    store.instance.tick = 3
    r_result.risk_level = "CRITICAL"
    persistence.persist_cycle(store)
    calls3 = [call.kwargs.get("kind") for call in write_buffer.submit.call_args_list]
    assert "risk" in calls3
