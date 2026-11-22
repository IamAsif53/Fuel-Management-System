"""Unit tests for the in-memory WorldState store."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from app.sim.client import SimUnavailable
from app.sim.validation import ValidatedSnapshot, validate_snapshot
from app.state.store import WorldState, WorldStateOut
from fsip_common.models import DemandObservation

FX = Path(__file__).resolve().parents[3] / "libs" / "fsip_common" / "tests" / "fixtures"


def load_fx(name: str) -> Any:
    return json.loads((FX / f"{name}.json").read_text())


@pytest.fixture
def valid_snapshot_dict() -> dict[str, Any]:
    return {
        "instance": load_fx("instance"),
        "regions": load_fx("regions"),
        "depots": load_fx("depots"),
        "stations": load_fx("stations"),
        "routes": load_fx("routes"),
        "events": load_fx("events"),
        "supply_arrivals": load_fx("supply_arrivals"),
        "allocations": load_fx("allocations"),
        "metrics": load_fx("metrics"),
    }


@pytest.fixture
def valid_snapshot(valid_snapshot_dict: dict[str, Any]) -> ValidatedSnapshot:
    return validate_snapshot(valid_snapshot_dict)


# =============================================================================
# 1. Initial State Construction and Defaults
# =============================================================================

def test_initial_state_defaults():
    store = WorldState()
    assert store.sim_run_id == 1
    assert store.instance is None
    assert store.regions == []
    assert store.depots == []
    assert store.stations == []
    assert store.routes == []
    assert store.events == []
    assert store.supply == []
    assert store.allocations == []
    assert store.metrics is None
    assert len(store.demand_rows) == 0
    assert store.last_good_at == {}
    assert store.stale is False
    assert store.degraded_reasons == set()
    assert store.degraded is False
    assert store.engine_mode == "NORMAL"
    assert store.operating_mode == "advisory"
    assert store.policy == "optimizer"
    assert store.risk == {}
    assert store.pending == []
    assert store.bottleneck is None
    assert store.tick == 0
    assert store.sim_time is None
    assert store.snapshot is None


# =============================================================================
# 2. Applying Valid Snapshot Updates All Corresponding Resources
# =============================================================================

def test_apply_valid_snapshot_updates_all_resources(valid_snapshot: ValidatedSnapshot):
    store = WorldState(sim_run_id=5)
    store.apply_snapshot(valid_snapshot, now=100.0)

    assert store.instance is not None
    assert store.instance.id == valid_snapshot.instance.id
    assert len(store.depots) == len(valid_snapshot.depots)
    assert len(store.stations) == len(valid_snapshot.stations)
    assert len(store.routes) == len(valid_snapshot.routes)
    assert len(store.events) == len(valid_snapshot.events)
    assert len(store.supply) == len(valid_snapshot.supply)
    assert len(store.allocations) == len(valid_snapshot.allocations)
    assert store.metrics is not None
    assert store.metrics.service_level == valid_snapshot.metrics.service_level

    # Check last_good_at updated for all 9 resources
    expected_resources = (
        "instance", "regions", "depots", "stations", "routes",
        "events", "supply", "allocations", "metrics",
    )
    for res in expected_resources:
        assert store.last_good_at.get(res) == 100.0

    assert store.degraded_reasons == set()
    assert store.degraded is False

    # Snapshot property should now resolve
    snap = store.snapshot
    assert snap is not None
    assert snap.instance.id == valid_snapshot.instance.id


# =============================================================================
# 3-6. Partial Updates, Failures, and last_good_at Preservation
# =============================================================================

def test_failed_resource_preserves_previous_good_value(valid_snapshot: ValidatedSnapshot):
    store = WorldState()
    store.apply_snapshot(valid_snapshot, now=10.0)
    original_depots = list(store.depots)

    # In partial update, depots fails with an exception
    partial_update = {
        "instance": valid_snapshot.instance,
        "depots": SimUnavailable("503 Simulator Unavailable"),
    }
    store.apply_snapshot(partial=partial_update, now=20.0)

    # Depots preserved
    assert store.depots == original_depots
    # last_good_at unchanged for depots
    assert store.last_good_at["depots"] == 10.0
    # last_good_at updated for instance
    assert store.last_good_at["instance"] == 20.0
    # Degraded reason recorded
    assert "depots_failed" in store.degraded_reasons
    assert store.degraded is True


def test_missing_resource_does_not_erase_cached_data(valid_snapshot: ValidatedSnapshot):
    store = WorldState()
    store.apply_snapshot(valid_snapshot, now=10.0)
    original_routes = list(store.routes)

    # Partial update without routes key
    store.apply_snapshot(partial={"instance": valid_snapshot.instance}, now=20.0)

    assert store.routes == original_routes
    assert store.last_good_at["routes"] == 10.0
    assert store.last_good_at["instance"] == 20.0


def test_one_resource_failure_does_not_block_other_updates(valid_snapshot: ValidatedSnapshot):
    store = WorldState()
    store.apply_snapshot(valid_snapshot, now=10.0)

    new_inst = copy.deepcopy(valid_snapshot.instance)
    new_inst.tick = 42

    # Depots fails, but instance and stations succeed
    partial_update = {
        "instance": new_inst,
        "depots": SimUnavailable("Depot error"),
        "stations": valid_snapshot.stations,
    }
    store.apply_snapshot(partial=partial_update, now=30.0)

    assert store.instance.tick == 42
    assert store.last_good_at["instance"] == 30.0
    assert store.last_good_at["stations"] == 30.0
    assert store.last_good_at["depots"] == 10.0  # unchanged
    assert "depots_failed" in store.degraded_reasons


def test_recovery_clears_degraded_reason(valid_snapshot: ValidatedSnapshot):
    store = WorldState()
    store.apply_snapshot(valid_snapshot, now=10.0)

    # Inject failure
    store.apply_snapshot(partial={"depots": SimUnavailable("503")}, now=15.0)
    assert "depots_failed" in store.degraded_reasons

    # Next cycle recovers
    store.apply_snapshot(partial={"depots": valid_snapshot.depots}, now=25.0)
    assert "depots_failed" not in store.degraded_reasons
    assert store.last_good_at["depots"] == 25.0


# =============================================================================
# 7. Stale and Degraded Reasons Consistency
# =============================================================================

def test_stale_and_degraded_reasons_behavior(valid_snapshot: ValidatedSnapshot):
    store = WorldState()
    store.apply_snapshot(valid_snapshot)

    assert store.stale is False
    assert store.degraded is False

    # Mark stale
    store.apply_snapshot(stale=True)
    assert store.stale is True
    assert "stale_data" in store.degraded_reasons
    assert store.degraded is True

    # Mark not stale
    store.apply_snapshot(stale=False)
    assert store.stale is False
    assert "stale_data" not in store.degraded_reasons
    assert store.degraded is False


# =============================================================================
# 8-10. Demand Rows Ring Buffers and Deduplication
# =============================================================================

def test_demand_rows_grouped_by_station_and_fuel():
    store = WorldState()
    raw_demand = load_fx("demand_history")
    store.apply_demand(raw_demand)

    # demand_history fixture has observations for station-mirpur, DIESEL
    key = ("station-mirpur", "DIESEL")
    assert key in store.demand_rows
    assert len(store.demand_rows[key]) > 0
    obs = store.demand_rows[key][0]
    assert obs.station_id == "station-mirpur"
    assert obs.fuel_type == "DIESEL"


def test_demand_rows_deduplicated_by_id():
    store = WorldState()
    raw_demand = load_fx("demand_history")

    added1 = store.apply_demand(raw_demand)
    assert added1 > 0
    key = ("station-mirpur", "DIESEL")
    initial_len = len(store.demand_rows[key])

    # Ingest the exact same observations again
    added2 = store.apply_demand(raw_demand)
    assert added2 == 0
    assert len(store.demand_rows[key]) == initial_len


def test_demand_series_retains_at_most_2000_records_evicting_oldest():
    store = WorldState()
    station = "station-mirpur"
    fuel = "DIESEL"
    key = (station, fuel)

    # Ingest 2,050 records with unique IDs and monotonically increasing ticks
    observations = [
        DemandObservation(
            id=i,
            station_id=station,
            fuel_type=fuel,
            tick=i,
            sim_time="2026-01-01T00:00:00Z",
            demand_liters=100.0,
            served_liters=100.0,
            unmet_liters=0.0,
        )
        for i in range(1, 2051)
    ]

    added = store.apply_demand(observations)
    assert added == 2050

    q = store.demand_rows[key]
    assert len(q) == 2000

    # Oldest 50 records (IDs 1..50) must have been evicted
    assert q[0].id == 51
    assert q[-1].id == 2050

    # IDs 1..50 should no longer be in the seen set (can be accepted if re-observed)
    assert 1 not in store._demand_seen_ids[key]
    assert 50 not in store._demand_seen_ids[key]
    assert 51 in store._demand_seen_ids[key]
    assert 2050 in store._demand_seen_ids[key]


# =============================================================================
# 11-13. Public JSON Serialization and Immutability
# =============================================================================

def test_to_public_json_serializability_and_fields(valid_snapshot: ValidatedSnapshot):
    store = WorldState(sim_run_id=3)
    store.apply_snapshot(valid_snapshot)

    public_dict = store.to_public_json()

    # Must be JSON serializable
    json_str = json.dumps(public_dict)
    assert isinstance(json_str, str)
    roundtrip = json.loads(json_str)

    # Must contain all required contract fields
    expected_keys = {
        "sim_run_id", "tick", "sim_time", "tick_minutes", "sim_status",
        "clock_label", "generated_at", "age_seconds", "stale", "degraded",
        "degraded_reasons", "engine_mode", "operating_mode", "policy",
        "scarcity", "metrics", "depots", "stations", "routes", "events",
        "supply_upcoming", "allocations_active", "alerts_open_count",
        "pending_recommendations", "bottleneck", "system",
    }
    assert expected_keys.issubset(roundtrip.keys())

    assert roundtrip["sim_run_id"] == 3
    assert roundtrip["tick"] == valid_snapshot.instance.tick
    assert roundtrip["sim_status"] == valid_snapshot.instance.status
    assert "t=" in roundtrip["clock_label"]
    assert len(roundtrip["depots"]) == len(valid_snapshot.depots)
    assert len(roundtrip["stations"]) == len(valid_snapshot.stations)
    assert len(roundtrip["routes"]) == len(valid_snapshot.routes)

    # Validate against WorldStateOut schema
    validated_out = WorldStateOut.model_validate(public_dict)
    assert validated_out.sim_run_id == 3
    assert validated_out.tick == valid_snapshot.instance.tick


def test_to_public_json_empty_state_serializable():
    store = WorldState()
    public_dict = store.to_public_json()

    json_str = json.dumps(public_dict)
    assert isinstance(json_str, str)
    validated_out = WorldStateOut.model_validate(public_dict)
    assert validated_out.sim_run_id == 1
    assert validated_out.tick == 0
    assert validated_out.sim_status == "PAUSED"


def test_mutating_public_json_does_not_mutate_internal_store(valid_snapshot: ValidatedSnapshot):
    store = WorldState()
    store.apply_snapshot(valid_snapshot)
    original_depot_name = store.depots[0].name
    original_service_level = store.metrics.service_level

    public_dict = store.to_public_json()

    # Attempt mutation of the output dictionary
    public_dict["depots"][0]["name"] = "HACKED_NAME"
    public_dict["degraded_reasons"].append("MALICIOUS_REASON")
    public_dict["metrics"]["service_level"] = -999.0
    public_dict["system"]["simulator"] = "COMPROMISED"

    # Internal store state remains completely unchanged
    assert store.depots[0].name == original_depot_name
    assert "MALICIOUS_REASON" not in store.degraded_reasons
    assert store.metrics.service_level == original_service_level


# =============================================================================
# Additional Helper Methods: inflight() and apply()
# =============================================================================

def test_inflight_helper(valid_snapshot: ValidatedSnapshot):
    store = WorldState()
    store.apply_snapshot(valid_snapshot)

    inflight_list = store.inflight()
    assert isinstance(inflight_list, list)
    for entry in inflight_list:
        station_id, fuel_type, arr_tick, qty = entry
        assert isinstance(station_id, str)
        assert fuel_type in ("DIESEL", "PETROL", "OCTANE")
        assert isinstance(arr_tick, int)
        assert qty > 0


def test_apply_alias_method(valid_snapshot: ValidatedSnapshot):
    store = WorldState()
    store.apply({"instance": valid_snapshot.instance, "depots": valid_snapshot.depots}, tick=99)
    assert store.instance is not None
    assert store.instance.tick == 99
    assert len(store.depots) == len(valid_snapshot.depots)
