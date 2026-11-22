"""Contract tests: models must parse REAL simulator responses (fixtures captured by scripts/capture_fixtures.sh)
and must reject malformed variants (the organizers' 'malformed fields' inject)."""
import copy
import json
from pathlib import Path

import pytest
from fsip_common.models import (
    AdminAuditEntry,
    AdminStepResult,
    Allocation,
    AllocationRequest,
    DemandObservation,
    Depot,
    FaultCreate,
    Health,
    Instance,
    Region,
    Route,
    SimErrorEnvelope,
    SimEvent,
    SimMetrics,
    Station,
    SupplyArrival,
    world_issues,
)
from fsip_common.timeutil import expected_arrival_tick, parse_sim_time
from pydantic import TypeAdapter, ValidationError

FX = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FX / f"{name}.json").read_text())


@pytest.mark.parametrize("name,model,many", [
    ("health", Health, False),
    ("instance", Instance, False),
    ("regions", Region, True),
    ("depots", Depot, True),
    ("depot_single", Depot, False),
    ("stations", Station, True),
    ("routes", Route, True),
    ("supply_arrivals", SupplyArrival, True),
    ("events", SimEvent, True),
    ("allocations", Allocation, True),
    ("allocation_created_201", Allocation, False),
    ("demand_history", DemandObservation, True),
    ("metrics", SimMetrics, False),
    ("admin_step", AdminStepResult, False),
    ("admin_audit", AdminAuditEntry, True),
    ("admin_event_created", SimEvent, False),
    ("admin_fault_created", FaultCreate, False),
])
def test_real_fixtures_parse(name, model, many):
    data = load(name)
    if many:
        items = TypeAdapter(list[model]).validate_python(data)
        assert len(items) > 0
    else:
        model.model_validate(data)


def test_world_is_consistent():
    depots = TypeAdapter(list[Depot]).validate_python(load("depots"))
    stations = TypeAdapter(list[Station]).validate_python(load("stations"))
    routes = TypeAdapter(list[Route]).validate_python(load("routes"))
    regions = TypeAdapter(list[Region]).validate_python(load("regions"))
    assert world_issues(depots, stations, routes, regions) == []
    assert len(depots) == 2 and len(stations) == 4 and len(routes) == 6


def test_idempotent_replay_returns_same_allocation():
    assert load("allocation_created_201")["id"] == load("allocation_replay_201")["id"]


@pytest.mark.parametrize("mutate", [
    lambda s: s["inventory"].pop("OCTANE"),                     # missing fuel key
    lambda s: s["inventory"].__setitem__("DIESEL", -5),        # negative
    lambda s: s["inventory"].__setitem__("DIESEL", "lots"),    # wrong type
    lambda s: s.__setitem__("status", "EXPLODED"),             # unknown enum
    lambda s: s.pop("capacity"),                               # missing field
    lambda s: s.__setitem__("demand_multiplier", float("nan")),  # NaN
])
def test_malformed_station_rejected(mutate):
    st = copy.deepcopy(load("stations")[0])
    mutate(st)
    with pytest.raises(ValidationError):
        Station.model_validate(st)


@pytest.mark.parametrize("mutate", [
    lambda d: d["capacity"].pop("DIESEL"),                   # missing fuel key
    lambda d: d["inventory"].__setitem__("PETROL", -10),     # negative inventory
    lambda d: d.__setitem__("dispatch_capacity_per_tick", -1),  # negative dispatch cap
    lambda d: d.__setitem__("status", "DESTROYED"),          # unknown enum
    lambda d: d.pop("region_id"),                            # missing required field
    lambda d: d.__setitem__("dispatch_capacity_per_tick", "infinite"),  # wrong type
])
def test_malformed_depot_rejected(mutate):
    dp = copy.deepcopy(load("depots")[0])
    mutate(dp)
    with pytest.raises(ValidationError):
        Depot.model_validate(dp)


@pytest.mark.parametrize("mutate", [
    lambda r: r.__setitem__("transit_ticks", 0),             # non-positive transit
    lambda r: r.__setitem__("transit_ticks", -2),            # negative transit
    lambda r: r.__setitem__("max_shipment", 0),              # non-positive shipment
    lambda r: r.__setitem__("status", "BLOCKED"),            # unknown enum
    lambda r: r.pop("source_depot_id"),                      # missing field
    lambda r: r.pop("destination_station_id"),               # missing field
])
def test_malformed_route_rejected(mutate):
    rt = copy.deepcopy(load("routes")[0])
    mutate(rt)
    with pytest.raises(ValidationError):
        Route.model_validate(rt)


@pytest.mark.parametrize("mutate", [
    lambda ev: ev.__setitem__("start_tick", -1),             # negative start_tick
    lambda ev: ev.__setitem__("end_tick", -1),               # negative end_tick
    lambda ev: ev.__setitem__("type", "alien_invasion"),     # unknown event type
    lambda ev: ev.__setitem__("status", "PENDING"),          # unknown event status
    lambda ev: ev.pop("id"),                                 # missing required id
])
def test_malformed_event_rejected(mutate):
    ev = copy.deepcopy(load("events")[0])
    mutate(ev)
    with pytest.raises(ValidationError):
        SimEvent.model_validate(ev)



@pytest.mark.parametrize("mutate", [
    lambda sa: sa.__setitem__("quantity", -100),             # negative quantity
    lambda sa: sa.__setitem__("planned_tick", -1),           # negative planned_tick
    lambda sa: sa.__setitem__("fuel_type", "BIODIESEL"),     # unknown fuel type
    lambda sa: sa.__setitem__("status", "LOST"),             # unknown status enum
    lambda sa: sa.pop("depot_id"),                           # missing depot_id
])
def test_malformed_supply_arrival_rejected(mutate):
    sa = copy.deepcopy(load("supply_arrivals")[0])
    mutate(sa)
    with pytest.raises(ValidationError):
        SupplyArrival.model_validate(sa)


@pytest.mark.parametrize("mutate", [
    lambda inst: inst.__setitem__("tick", -1),               # negative tick
    lambda inst: inst.__setitem__("tick_minutes", 0),        # non-positive tick_minutes
    lambda inst: inst.__setitem__("status", "TERMINATED"),   # unknown status enum
    lambda inst: inst.pop("scenario_id"),                    # missing field
    lambda inst: inst.__setitem__("sim_time", "not-a-date"),  # invalid timestamp
])
def test_malformed_instance_rejected(mutate):
    inst = copy.deepcopy(load("instance"))
    mutate(inst)
    with pytest.raises((ValidationError, ValueError)):
        Instance.model_validate(inst)


@pytest.mark.parametrize("mutate", [
    lambda m: m.__setitem__("service_level", 1.5),           # > 1.0
    lambda m: m.__setitem__("service_level", -0.1),          # < 0.0
    lambda m: m.__setitem__("served_demand_liters", -1),     # negative
    lambda m: m.__setitem__("unmet_demand_liters", -1),      # negative
    lambda m: m.__setitem__("allocation_failures", -1),      # negative
])
def test_malformed_metrics_rejected(mutate):
    m = copy.deepcopy(load("metrics"))
    mutate(m)
    with pytest.raises(ValidationError):
        SimMetrics.model_validate(m)



def test_extra_fields_ignored():
    st = copy.deepcopy(load("stations")[0])
    st["brand_new_field"] = 123
    Station.model_validate(st)


def test_world_issues_detects_bad_route_and_overfull():
    depots = TypeAdapter(list[Depot]).validate_python(load("depots"))
    stations = TypeAdapter(list[Station]).validate_python(load("stations"))
    routes = TypeAdapter(list[Route]).validate_python(load("routes"))
    routes[0].source_depot_id = "depot-ghost"
    stations[0].inventory["DIESEL"] = stations[0].capacity["DIESEL"] * 2
    issues = world_issues(depots, stations, routes)
    assert any("depot-ghost" in i for i in issues) and any("> capacity" in i for i in issues)


def test_error_envelopes_have_expected_shapes():
    assert load("error_409_idempotency_mismatch")["detail"]["code"] == "IDEMPOTENCY_KEY_MISMATCH"
    assert load("error_409_route_mismatch")["detail"]["code"] == "ROUTE_MISMATCH"
    assert load("error_404_not_found")["detail"]["code"] == "NOT_FOUND"
    assert isinstance(load("error_422_validation")["detail"], list)
    assert load("error_503_fault_injected")["error"]["code"] == "FAULT_INJECTED"
    assert "x-simulator-stale: true" in (FX / "stale_headers.txt").read_text().lower()


# ---------------------------------------------------------------- SimErrorEnvelope tests
def test_sim_error_envelope_real_fixtures():
    # 409 idempotency mismatch
    env1 = SimErrorEnvelope.model_validate(load("error_409_idempotency_mismatch"))
    assert env1.code == "IDEMPOTENCY_KEY_MISMATCH"
    assert "Idempotency key mismatch" in env1.message
    assert not env1.is_fault_injected
    assert not env1.is_validation_error

    # 409 route mismatch
    env2 = SimErrorEnvelope.model_validate(load("error_409_route_mismatch"))
    assert env2.code == "ROUTE_MISMATCH"
    assert "Route does not connect specified depot and station" in env2.message
    assert not env2.is_fault_injected

    # 404 not found
    env3 = SimErrorEnvelope.model_validate(load("error_404_not_found"))
    assert env3.code == "NOT_FOUND"
    assert "Entity not found" in env3.message
    assert not env3.is_fault_injected

    # 503 fault injected
    env4 = SimErrorEnvelope.model_validate(load("error_503_fault_injected"))
    assert env4.code == "FAULT_INJECTED"
    assert "Simulated fault injected" in env4.message
    assert env4.is_fault_injected
    assert not env4.is_validation_error

    # 422 validation error
    env5 = SimErrorEnvelope.model_validate(load("error_422_validation"))
    assert env5.code == "VALIDATION_ERROR"
    assert env5.is_validation_error
    assert not env5.is_fault_injected
    assert env5.validation_errors is not None
    assert len(env5.validation_errors) == 1
    assert env5.validation_errors[0].type == "greater_than"
    assert env5.validation_errors[0].loc == ["body", "quantity"]
    assert env5.validation_errors[0].field_name == "quantity"
    assert "body.quantity: Input should be greater than 0" in env5.message


def test_sim_error_envelope_stream_disconnect():
    env = SimErrorEnvelope.model_validate({"detail": {"code": "FAULT_INJECTED"}})
    assert env.code == "FAULT_INJECTED"
    assert env.is_fault_injected
    assert not env.is_validation_error


def test_sim_error_envelope_multiple_422_items():
    raw = {
        "detail": [
            {"type": "greater_than", "loc": ["body", "quantity"], "msg": "Input should be greater than 0"},
            {"type": "missing", "loc": ["body", "fuel_type"], "msg": "Field required"},
        ]
    }
    env = SimErrorEnvelope.model_validate(raw)
    assert env.code == "VALIDATION_ERROR"
    assert env.is_validation_error
    assert len(env.validation_errors) == 2
    assert env.validation_errors[0].field_name == "quantity"
    assert env.validation_errors[1].field_name == "fuel_type"
    assert "body.quantity: Input should be greater than 0" in env.message
    assert "body.fuel_type: Field required" in env.message


def test_sim_error_envelope_with_data_payload():
    raw = {
        "detail": {
            "code": "CONSTRAINT_VIOLATION",
            "message": "Resource constraint violated",
            "data": {"violations": ["dispatch_capacity_exceeded"]},
        }
    }
    env = SimErrorEnvelope.model_validate(raw)
    assert env.code == "CONSTRAINT_VIOLATION"
    assert env.message == "Resource constraint violated"
    assert env.detail.data == {"violations": ["dispatch_capacity_exceeded"]}


def test_sim_error_envelope_string_fallback():
    env = SimErrorEnvelope.model_validate({"detail": "502 Bad Gateway"})
    assert env.code == "UNKNOWN"
    assert env.message == "502 Bad Gateway"
    assert not env.is_fault_injected


@pytest.mark.parametrize("bad_payload", [
    {},                                       # empty dict
    {"foo": "bar"},                           # unrelated keys
    {"detail": {"message": "missing code"}},  # missing code in detail
    {"error": {"message": "missing code"}},   # missing code in error
    "not a dict",                             # non-dict
    [{"type": "error"}],                      # list instead of envelope dict
    None,                                     # None
])
def test_sim_error_envelope_malformed_rejected(bad_payload):
    with pytest.raises((ValidationError, ValueError)):
        SimErrorEnvelope.model_validate(bad_payload)


# ---------------------------------------------------------------- AllocationRequest tests
def test_allocation_request_valid():
    req = AllocationRequest(
        idempotency_key="key-001",
        source_depot_id="depot-gazipur",
        destination_station_id="station-mirpur",
        route_id="route-gazipur-mirpur",
        fuel_type="DIESEL",
        quantity=3000,
    )
    assert req.idempotency_key == "key-001"
    assert req.quantity == 3000.0  # coerced to float


@pytest.mark.parametrize("mutate", [
    lambda r: r.pop("idempotency_key"),
    lambda r: r.pop("source_depot_id"),
    lambda r: r.pop("destination_station_id"),
    lambda r: r.pop("route_id"),
    lambda r: r.pop("fuel_type"),
    lambda r: r.pop("quantity"),
    lambda r: r.__setitem__("idempotency_key", ""),
    lambda r: r.__setitem__("source_depot_id", ""),
    lambda r: r.__setitem__("destination_station_id", ""),
    lambda r: r.__setitem__("route_id", ""),
    lambda r: r.__setitem__("fuel_type", "KEROSENE"),
    lambda r: r.__setitem__("quantity", 0),
    lambda r: r.__setitem__("quantity", -100),
    lambda r: r.__setitem__("quantity", float("nan")),
    lambda r: r.__setitem__("quantity", float("inf")),
])
def test_allocation_request_malformed_rejected(mutate):
    base = {
        "idempotency_key": "key-001",
        "source_depot_id": "depot-gazipur",
        "destination_station_id": "station-mirpur",
        "route_id": "route-gazipur-mirpur",
        "fuel_type": "DIESEL",
        "quantity": 3000.0,
    }
    mutate(base)
    with pytest.raises(ValidationError):
        AllocationRequest.model_validate(base)


# ---------------------------------------------------------------- Entity strict validation tests
@pytest.mark.parametrize("model,payload,error_field", [
    (Depot, {"id": "d1", "name": "D", "region_id": "r1", "status": "OPEN", "dispatch_capacity_per_tick": -1,
             "capacity": {"DIESEL": 1, "PETROL": 1, "OCTANE": 1},
             "inventory": {"DIESEL": 1, "PETROL": 1, "OCTANE": 1}}, "dispatch_capacity_per_tick"),
    (Route, {"id": "r1", "source_depot_id": "d1", "destination_station_id": "s1", "transit_ticks": 0,
             "max_shipment": 1000, "status": "AVAILABLE"}, "transit_ticks"),
    (Route, {"id": "r1", "source_depot_id": "d1", "destination_station_id": "s1", "transit_ticks": 1,
             "max_shipment": 0, "status": "AVAILABLE"}, "max_shipment"),
    (Instance, {"id": 1, "scenario_id": "s", "scenario_version": "1.0", "seed": 1,
                "sim_time": "2026-01-01T00:00:00", "tick": -1, "tick_minutes": 15, "status": "PAUSED"}, "tick"),
    (Instance, {"id": 1, "scenario_id": "s", "scenario_version": "1.0", "seed": 1,
                "sim_time": "2026-01-01T00:00:00", "tick": 0, "tick_minutes": 0, "status": "PAUSED"}, "tick_minutes"),
    (SupplyArrival, {"id": "s1", "depot_id": "d1", "fuel_type": "DIESEL", "quantity": -50,
                     "planned_tick": 5, "status": "SCHEDULED"}, "quantity"),
    (SupplyArrival, {"id": "s1", "depot_id": "d1", "fuel_type": "KEROSENE", "quantity": 100,
                     "planned_tick": 5, "status": "SCHEDULED"}, "fuel_type"),
    (Allocation, {"id": 1, "idempotency_key": "k", "source_depot_id": "d1", "destination_station_id": "s1",
                  "route_id": "r1", "fuel_type": "DIESEL", "quantity": 0, "created_tick": 0,
                  "status": "PENDING"}, "quantity"),
    (SimMetrics, {"served_demand_liters": 0, "unmet_demand_liters": 0, "service_level": 1.5,
                  "allocation_liters": 0, "allocation_failures": 0}, "service_level"),
])
def test_entities_reject_out_of_bounds(model, payload, error_field):
    with pytest.raises(ValidationError) as exc_info:
        model.model_validate(payload)
    assert any(error_field in str(err["loc"]) for err in exc_info.value.errors())


def test_event_helpers():
    ev = SimEvent(id=1, type="route_disruption", start_tick=5, end_tick=10, status="SCHEDULED", parameters={})
    assert ev.targets("route_ids") == []          # empty filter == ALL routes
    assert ev.covers_tick(5) and ev.covers_tick(9) and not ev.covers_tick(10)


def test_timeutil():
    assert parse_sim_time("2026-01-01T00:15:00").tzinfo is not None
    assert parse_sim_time("2026-01-01T00:15:00+00:00") == parse_sim_time("2026-01-01T00:15:00")
    assert expected_arrival_tick(0, 2) == 2
    assert expected_arrival_tick(10, 4) == 14
    with pytest.raises(ValueError):
        expected_arrival_tick(-1, 2)
    with pytest.raises(ValueError):
        expected_arrival_tick(0, -1)
    with pytest.raises(TypeError):
        expected_arrival_tick("0", 2)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        expected_arrival_tick(0, 2.5)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        expected_arrival_tick(True, 2)  # type: ignore[arg-type]


