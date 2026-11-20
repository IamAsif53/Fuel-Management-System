"""Unit tests for simulator payload validation and snapshot cross-checking."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from app.sim.client import SimInvalidPayload
from app.sim.validation import (
    FSIP_SIM_INVALID_RESPONSES,
    ValidatedSnapshot,
    cross_check,
    validate_resource,
    validate_snapshot,
)
from fsip_common.models import (
    AdminAuditEntry,
    AdminStepResult,
    Allocation,
    AllocationRequest,
    DemandObservation,
    Depot,
    Health,
    Instance,
    Region,
    Route,
    SimEvent,
    SimMetrics,
    Station,
    SupplyArrival,
)

FX = Path(__file__).resolve().parents[3] / "libs" / "fsip_common" / "tests" / "fixtures"


def load_fx(name: str) -> Any:
    return json.loads((FX / f"{name}.json").read_text())


def get_metric_val(resource: str) -> float:
    """Helper to read current value of fsip_sim_invalid_responses_total{resource}."""
    if FSIP_SIM_INVALID_RESPONSES is None:
        return 0.0
    for metric in FSIP_SIM_INVALID_RESPONSES.collect():
        for sample in metric.samples:
            if sample.labels.get("resource") == resource:
                return sample.value
    return 0.0


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


# =============================================================================
# Resource validation tests
# =============================================================================

def test_valid_payload_for_every_supported_resource():
    inst = validate_resource("instance", load_fx("instance"))
    assert isinstance(inst, Instance)

    hlth = validate_resource("health", load_fx("health"))
    assert isinstance(hlth, Health)

    met = validate_resource("metrics", load_fx("metrics"))
    assert isinstance(met, SimMetrics)

    regs = validate_resource("regions", load_fx("regions"))
    assert isinstance(regs, list) and all(isinstance(r, Region) for r in regs)

    deps = validate_resource("depots", load_fx("depots"))
    assert isinstance(deps, list) and all(isinstance(d, Depot) for d in deps)

    dep_single = validate_resource("depot", load_fx("depot_single"))
    assert isinstance(dep_single, Depot)

    stas = validate_resource("stations", load_fx("stations"))
    assert isinstance(stas, list) and all(isinstance(s, Station) for s in stas)

    routs = validate_resource("routes", load_fx("routes"))
    assert isinstance(routs, list) and all(isinstance(r, Route) for r in routs)

    evts = validate_resource("events", load_fx("events"))
    assert isinstance(evts, list) and all(isinstance(e, SimEvent) for e in evts)

    sup = validate_resource("supply_arrivals", load_fx("supply_arrivals"))
    assert isinstance(sup, list) and all(isinstance(s, SupplyArrival) for s in sup)

    sup_alt = validate_resource("supply", load_fx("supply_arrivals"))
    assert isinstance(sup_alt, list) and all(isinstance(s, SupplyArrival) for s in sup_alt)

    allocs = validate_resource("allocations", load_fx("allocations"))
    assert isinstance(allocs, list) and all(isinstance(a, Allocation) for a in allocs)

    dem = validate_resource("demand_history", load_fx("demand_history"))
    assert isinstance(dem, list) and all(isinstance(d, DemandObservation) for d in dem)

    step = validate_resource("admin_step", load_fx("admin_step"))
    assert isinstance(step, AdminStepResult)

    audit = validate_resource("admin_audit", load_fx("admin_audit"))
    assert isinstance(audit, list) and all(isinstance(a, AdminAuditEntry) for a in audit)

    req_dict = {
        "idempotency_key": "test-key-1",
        "source_depot_id": "depot-gazipur",
        "destination_station_id": "station-mirpur",
        "route_id": "route-gazipur-mirpur",
        "fuel_type": "DIESEL",
        "quantity": 2500.0,
    }
    req = validate_resource("allocation_request", req_dict)
    assert isinstance(req, AllocationRequest)


def test_unknown_resource_name():
    with pytest.raises(SimInvalidPayload) as exc_info:
        validate_resource("unknown_galaxy", {"foo": "bar"})
    assert exc_info.value.resource == "unknown_galaxy"
    assert exc_info.value.raw == {"foo": "bar"}
    assert any("Unknown simulator resource" in str(e) for e in exc_info.value.errors)


def test_missing_required_fields():
    # Missing scenario_id in Instance
    inst_data = copy.deepcopy(load_fx("instance"))
    del inst_data["scenario_id"]
    with pytest.raises(SimInvalidPayload) as exc_info:
        validate_resource("instance", inst_data)
    assert exc_info.value.resource == "instance"
    assert any("scenario_id" in str(e["loc"]) for e in exc_info.value.errors)

    # Missing destination_station_id in Route
    routes_data = copy.deepcopy(load_fx("routes"))
    del routes_data[0]["destination_station_id"]
    with pytest.raises(SimInvalidPayload) as exc_info:
        validate_resource("routes", routes_data)
    assert exc_info.value.resource == "routes"
    assert any("destination_station_id" in str(e["loc"]) for e in exc_info.value.errors)


def test_invalid_status_enum():
    # Invalid status in instance
    inst_data = copy.deepcopy(load_fx("instance"))
    inst_data["status"] = "HALTED"  # Only PAUSED or RUNNING
    with pytest.raises(SimInvalidPayload) as exc_info:
        validate_resource("instance", inst_data)
    assert any("status" in str(e["loc"]) for e in exc_info.value.errors)

    # Invalid status in depot
    depots_data = copy.deepcopy(load_fx("depots"))
    depots_data[0]["status"] = "SHUTDOWN"  # Only OPEN or CONSTRAINED
    with pytest.raises(SimInvalidPayload) as exc_info:
        validate_resource("depots", depots_data)
    assert any("status" in str(e["loc"]) for e in exc_info.value.errors)


def test_missing_fuel_key():
    # Depot capacity missing OCTANE
    depots_data = copy.deepcopy(load_fx("depots"))
    del depots_data[0]["capacity"]["OCTANE"]
    with pytest.raises(SimInvalidPayload) as exc_info:
        validate_resource("depots", depots_data)
    assert "OCTANE" in str(exc_info.value.errors)

    # Station inventory missing PETROL
    stations_data = copy.deepcopy(load_fx("stations"))
    del stations_data[0]["inventory"]["PETROL"]
    with pytest.raises(SimInvalidPayload) as exc_info:
        validate_resource("stations", stations_data)
    assert "PETROL" in str(exc_info.value.errors)


def test_negative_numeric_value():
    inst_data = copy.deepcopy(load_fx("instance"))
    inst_data["tick"] = -1
    with pytest.raises(SimInvalidPayload) as exc_info:
        validate_resource("instance", inst_data)
    assert any("tick" in str(e["loc"]) for e in exc_info.value.errors)

    routes_data = copy.deepcopy(load_fx("routes"))
    routes_data[0]["transit_ticks"] = 0  # transit_ticks must be >= 1
    with pytest.raises(SimInvalidPayload) as exc_info:
        validate_resource("routes", routes_data)
    assert any("transit_ticks" in str(e["loc"]) for e in exc_info.value.errors)

    stations_data = copy.deepcopy(load_fx("stations"))
    stations_data[0]["inventory"]["DIESEL"] = -50.0
    with pytest.raises(SimInvalidPayload) as exc_info:
        validate_resource("stations", stations_data)
    assert any("inventory" in str(e["loc"]) for e in exc_info.value.errors)


def test_nan_and_infinity_rejected():
    regs_data = copy.deepcopy(load_fx("regions"))
    regs_data[0]["demand_factor"] = float("nan")
    with pytest.raises(SimInvalidPayload) as exc_info:
        validate_resource("regions", regs_data)
    assert exc_info.value.resource == "regions"

    routes_data = copy.deepcopy(load_fx("routes"))
    routes_data[0]["max_shipment"] = float("inf")
    with pytest.raises(SimInvalidPayload) as exc_info:
        validate_resource("routes", routes_data)
    assert exc_info.value.resource == "routes"


def test_malformed_timestamps():
    inst_data = copy.deepcopy(load_fx("instance"))
    inst_data["sim_time"] = "not-a-valid-timestamp"
    with pytest.raises(SimInvalidPayload) as exc_info:
        validate_resource("instance", inst_data)
    assert any("sim_time" in str(e["loc"]) for e in exc_info.value.errors)


def test_malformed_list_item_preserves_error_loc():
    stations_data = copy.deepcopy(load_fx("stations"))
    assert len(stations_data) >= 3
    # Introduce error specifically at index 2
    stations_data[2]["status"] = "INVALID_STATUS"
    with pytest.raises(SimInvalidPayload) as exc_info:
        validate_resource("stations", stations_data)
    errors = exc_info.value.errors
    # loc should start with 2
    matching = [e for e in errors if len(e.get("loc", ())) >= 2 and e["loc"][0] == 2 and e["loc"][1] == "status"]
    assert len(matching) > 0


def test_extra_unknown_fields_tolerated():
    depots_data = copy.deepcopy(load_fx("depots"))
    depots_data[0]["future_firmware_version"] = "9.9.9"
    depots_data[0]["unrecognized_boolean"] = True
    validated = validate_resource("depots", depots_data)
    assert isinstance(validated, list)
    assert validated[0].id == depots_data[0]["id"]


def test_expected_list_got_dict_raises():
    with pytest.raises(SimInvalidPayload) as exc_info:
        validate_resource("depots", {"id": "single-depot"})
    assert any("Expected list" in str(e["msg"]) for e in exc_info.value.errors)


# =============================================================================
# Cross-checking tests
# =============================================================================

def test_valid_complete_snapshot_returns_no_issues(valid_snapshot_dict: dict[str, Any]):
    snap = validate_resource("snapshot", valid_snapshot_dict)
    assert isinstance(snap, ValidatedSnapshot)
    issues = cross_check(snap)
    assert issues == []


def test_cross_check_route_references_nonexistent_depot(valid_snapshot_dict: dict[str, Any]):
    snap_data = copy.deepcopy(valid_snapshot_dict)
    snap_data["routes"][0]["source_depot_id"] = "depot-ghost"
    with pytest.raises(SimInvalidPayload) as exc_info:
        validate_resource("snapshot", snap_data)
    assert any("unknown depot depot-ghost" in str(e) for e in exc_info.value.errors)


def test_cross_check_route_references_nonexistent_station(valid_snapshot_dict: dict[str, Any]):
    snap_data = copy.deepcopy(valid_snapshot_dict)
    snap_data["routes"][0]["destination_station_id"] = "station-phantom"
    with pytest.raises(SimInvalidPayload) as exc_info:
        validate_resource("snapshot", snap_data)
    assert any("unknown station station-phantom" in str(e) for e in exc_info.value.errors)


def test_cross_check_inventory_exceeds_capacity_by_more_than_1_percent(valid_snapshot_dict: dict[str, Any]):
    snap_data = copy.deepcopy(valid_snapshot_dict)
    # depot-gazipur DIESEL capacity is 60000; 1% tolerance is 60600. Set to 60700.
    snap_data["depots"][0]["capacity"]["DIESEL"] = 60000.0
    snap_data["depots"][0]["inventory"]["DIESEL"] = 60700.0
    with pytest.raises(SimInvalidPayload) as exc_info:
        validate_resource("snapshot", snap_data)
    assert any("> capacity" in str(e) for e in exc_info.value.errors)


def test_cross_check_inventory_within_1_percent_tolerance_accepted(valid_snapshot_dict: dict[str, Any]):
    snap_data = copy.deepcopy(valid_snapshot_dict)
    # depot-gazipur DIESEL capacity is 60000; 1% tolerance allows up to 60600. Set to 60500.
    snap_data["depots"][0]["capacity"]["DIESEL"] = 60000.0
    snap_data["depots"][0]["inventory"]["DIESEL"] = 60500.0
    snap = validate_snapshot(snap_data)
    assert isinstance(snap, ValidatedSnapshot)
    issues = cross_check(snap)
    assert not any(f"{snap.depots[0].id}: DIESEL inventory" in i for i in issues)


def test_cross_check_detects_missing_fuel_keys_on_model(valid_snapshot_dict: dict[str, Any]):
    snap = ValidatedSnapshot.model_validate(valid_snapshot_dict)
    # Manually remove fuel key from capacity dict
    del snap.depots[0].capacity["OCTANE"]
    issues = cross_check(snap)
    assert any("missing fuel key 'OCTANE' in capacity" in i for i in issues)


def test_cross_check_detects_non_finite_and_negative_numerics(valid_snapshot_dict: dict[str, Any]):
    snap = ValidatedSnapshot.model_validate(valid_snapshot_dict)
    # Force negative capacity on node
    snap.stations[0].capacity["DIESEL"] = -10.0
    issues = cross_check(snap)
    assert any("must be finite and >= 0" in i for i in issues)

    # Force NaN on demand_multiplier
    snap.stations[0].demand_multiplier = float("nan")
    issues2 = cross_check(snap)
    assert any("demand_multiplier must be finite" in i for i in issues2)


# =============================================================================
# Metrics and Exception Behavior tests
# =============================================================================

def test_invalid_resource_increments_counter_exactly_once():
    initial = get_metric_val("stations")
    with pytest.raises(SimInvalidPayload):
        validate_resource("stations", [{"id": "bad-station"}])
    after = get_metric_val("stations")
    assert after == initial + 1.0


def test_invalid_snapshot_increments_counter_exactly_once(valid_snapshot_dict: dict[str, Any]):
    initial = get_metric_val("snapshot")
    snap_data = copy.deepcopy(valid_snapshot_dict)
    # Corrupt route to trigger cross_check failure
    snap_data["routes"][0]["source_depot_id"] = "depot-nowhere"
    with pytest.raises(SimInvalidPayload):
        validate_snapshot(snap_data)
    after = get_metric_val("snapshot")
    assert after == initial + 1.0


def test_sim_invalid_payload_contract():
    raw_data = {"id": "corrupted"}
    with pytest.raises(SimInvalidPayload) as exc_info:
        validate_resource("instance", raw_data)

    exc = exc_info.value
    assert exc.resource == "instance"
    assert exc.raw == raw_data
    assert isinstance(exc.errors, list)
    assert len(exc.errors) > 0
    assert "Invalid response payload for instance" in str(exc)


def test_metric_labels_contain_only_resource_names():
    if FSIP_SIM_INVALID_RESPONSES is None:
        pytest.skip("Prometheus metrics not registered")

    # Trigger validation error on a query-parameter styled resource name
    with pytest.raises(SimInvalidPayload):
        validate_resource("/v1/demand-history?limit=100", {"invalid": "not a list"})

    # Check collector label names
    for metric in FSIP_SIM_INVALID_RESPONSES.collect():
        for sample in metric.samples:
            assert list(sample.labels.keys()) == ["resource"]
            label_val = sample.labels["resource"]
            # Label must not contain slash, question mark, or raw payload content
            assert "/" not in label_val
            assert "?" not in label_val
            assert "{" not in label_val
