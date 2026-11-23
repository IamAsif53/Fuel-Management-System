"""100% Coverage Unit tests for services/engine/app/decision/constraints.py."""

from app.decision.constraints import (
    PlanLine,
    check_line,
    check_plan,
    depot_available,
    route_blocked,
    route_safe_at_departure,
    station_room_at_arrival,
)


def _make_base_state():
    return {
        "tick": 10,
        "stale": False,
        "degraded": False,
        "routes": {
            "r-1": {
                "id": "r-1",
                "source_depot_id": "d-1",
                "destination_station_id": "s-1",
                "status": "AVAILABLE",
                "max_shipment": 5000.0,
                "transit_ticks": 2,
            },
            "r-2": {
                "id": "r-2",
                "source_depot_id": "d-1",
                "destination_station_id": "s-2",
                "status": "DISRUPTED",
                "max_shipment": 4000.0,
                "transit_ticks": 2,
            },
        },
        "depots": [
            {
                "id": "d-1",
                "status": "OPEN",
                "inventory": {"DIESEL": 10000.0, "PETROL": 8000.0, "OCTANE": 5000.0},
                "capacity": {"DIESEL": 20000.0, "PETROL": 15000.0, "OCTANE": 10000.0},
                "dispatch_capacity_per_tick": 8000.0,
            },
            {
                "id": "d-2",
                "status": "CONSTRAINED",
                "inventory": {"DIESEL": 10000.0},
                "dispatch_capacity_per_tick": 8000.0,
            },
            {
                "id": "d-3",
                "status": "CLOSED",
                "inventory": {"DIESEL": 10000.0},
                "dispatch_capacity_per_tick": 8000.0,
            },
        ],
        "stations": [
            {
                "id": "s-1",
                "status": "OPEN",
                "inventory": {"DIESEL": 2000.0, "PETROL": 2000.0, "OCTANE": 1000.0},
                "capacity": {"DIESEL": 10000.0, "PETROL": 8000.0, "OCTANE": 5000.0},
            },
            {
                "id": "s-2",
                "status": "OUTAGE",
                "inventory": {"DIESEL": 2000.0},
                "capacity": {"DIESEL": 10000.0},
            },
        ],
        "events": [],
        "inflight": [],
        "demand": {("s-1", "DIESEL"): [50.0, 50.0]},
    }


def test_valid_line_passes():
    state = _make_base_state()
    line = PlanLine(
        route_id="r-1",
        source_depot_id="d-1",
        destination_station_id="s-1",
        fuel_type="DIESEL",
        quantity=3000.0,
    )
    v = check_line(state, line)
    assert len(v) == 0


def test_r1_route_match():
    state = _make_base_state()
    # 1. Non-existent route
    line_no_route = PlanLine(
        route_id="r-unknown",
        source_depot_id="d-1",
        destination_station_id="s-1",
        fuel_type="DIESEL",
        quantity=1000.0,
    )
    v1 = check_line(state, line_no_route)
    assert any(x.rule == "R1_ROUTE_MATCH" for x in v1)

    # 2. Endpoint mismatch
    line_mismatch = PlanLine(
        route_id="r-1",
        source_depot_id="d-1",
        destination_station_id="s-2",  # r-1 goes to s-1
        fuel_type="DIESEL",
        quantity=1000.0,
    )
    v2 = check_line(state, line_mismatch)
    assert any(x.rule == "R1_ROUTE_MATCH" for x in v2)


def test_r2_route_blocked_direct():
    state = _make_base_state()
    # Route r-2 is DISRUPTED right now
    line = PlanLine(
        route_id="r-2",
        source_depot_id="d-1",
        destination_station_id="s-2",
        fuel_type="DIESEL",
        quantity=1000.0,
    )
    v = check_line(state, line)
    assert any(x.rule == "R2_ROUTE_BLOCKED" for x in v)


def test_r2_route_blocked_by_scheduled_or_active_event():
    state = _make_base_state()
    # Departure is at tick + 1 = 11
    # Add route_disruption event covering tick 11
    state["events"] = [
        {
            "type": "route_disruption",
            "status": "SCHEDULED",
            "start_tick": 11,
            "end_tick": 20,
            "parameters": {"route_ids": ["r-1"]},
        }
    ]
    line = PlanLine(
        route_id="r-1",
        source_depot_id="d-1",
        destination_station_id="s-1",
        fuel_type="DIESEL",
        quantity=1000.0,
    )
    v = check_line(state, line)
    assert any(x.rule == "R2_ROUTE_BLOCKED" for x in v)
    assert not route_safe_at_departure(state, "r-1", 10)

    # Empty route_ids means ALL routes are disrupted
    state["events"] = [
        {
            "type": "route_disruption",
            "status": "ACTIVE",
            "start_tick": 5,
            "end_tick": 15,
            "parameters": {},  # empty
        }
    ]
    v_empty = check_line(state, line)
    assert any(x.rule == "R2_ROUTE_BLOCKED" for x in v_empty)

    # Resolved event does not block
    state["events"] = [
        {
            "type": "route_disruption",
            "status": "RESOLVED",
            "start_tick": 5,
            "end_tick": 15,
            "parameters": {"route_ids": ["r-1"]},
        }
    ]
    assert not route_blocked(state["events"], state["routes"], "r-1", 10)

    # Event for a different route does not block r-1
    state["events"] = [
        {
            "type": "route_disruption",
            "status": "ACTIVE",
            "start_tick": 5,
            "end_tick": 15,
            "parameters": {"route_ids": ["r-other"]},
        }
    ]
    assert not route_blocked(state["events"], state["routes"], "r-1", 10)

    # Event covering a future tick after departure does not block
    state["events"] = [
        {
            "type": "route_disruption",
            "status": "SCHEDULED",
            "start_tick": 15,
            "end_tick": 25,
            "parameters": {"route_ids": ["r-1"]},
        }
    ]
    assert not route_blocked(state["events"], state["routes"], "r-1", 10)


def test_r3_node_status():
    state = _make_base_state()
    # 1. Unknown depot
    line_unknown_depot = PlanLine(
        route_id="r-1",
        source_depot_id="d-unknown",
        destination_station_id="s-1",
        fuel_type="DIESEL",
        quantity=1000.0,
    )
    v1 = check_line(state, line_unknown_depot)
    assert any(x.rule == "R3_NODE_STATUS" for x in v1)

    # 2. Closed depot
    line_closed_depot = PlanLine(
        route_id="r-1",
        source_depot_id="d-3",
        destination_station_id="s-1",
        fuel_type="DIESEL",
        quantity=1000.0,
    )
    v2 = check_line(state, line_closed_depot)
    assert any(x.rule == "R3_NODE_STATUS" for x in v2)

    # 3. Unknown station
    line_unknown_station = PlanLine(
        route_id="r-1",
        source_depot_id="d-1",
        destination_station_id="s-unknown",
        fuel_type="DIESEL",
        quantity=1000.0,
    )
    v3 = check_line(state, line_unknown_station)
    assert any(x.rule == "R3_NODE_STATUS" for x in v3)

    # 4. Station in OUTAGE
    state["routes"]["r-outage"] = {
        "id": "r-outage",
        "source_depot_id": "d-1",
        "destination_station_id": "s-2",
        "status": "AVAILABLE",
        "max_shipment": 5000.0,
        "transit_ticks": 2,
    }
    line_outage = PlanLine(
        route_id="r-outage",
        source_depot_id="d-1",
        destination_station_id="s-2",
        fuel_type="DIESEL",
        quantity=1000.0,
    )
    v4 = check_line(state, line_outage)
    assert any(x.rule == "R3_NODE_STATUS" for x in v4)


def test_r4_max_shipment():
    state = _make_base_state()
    # Route max is 5000.0
    line_exceed = PlanLine(
        route_id="r-1",
        source_depot_id="d-1",
        destination_station_id="s-1",
        fuel_type="DIESEL",
        quantity=5500.0,
    )
    v1 = check_line(state, line_exceed)
    assert any(x.rule == "R4_MAX_SHIPMENT" for x in v1)

    # Zero or negative quantity
    line_zero = PlanLine(
        route_id="r-1",
        source_depot_id="d-1",
        destination_station_id="s-1",
        fuel_type="DIESEL",
        quantity=0.0,
    )
    v2 = check_line(state, line_zero)
    assert any(x.rule == "R4_MAX_SHIPMENT" for x in v2)


def test_r5_depot_inventory_and_reserve():
    state = _make_base_state()
    # Depot DIESEL inventory is 10000.0
    line_over = PlanLine(
        route_id="r-1",
        source_depot_id="d-1",
        destination_station_id="s-1",
        fuel_type="DIESEL",
        quantity=4500.0,
    )
    # Reserve of 6000.0 means only 4000.0 available
    v = check_line(state, line_over, reserve={("d-1", "DIESEL"): 6000.0})
    assert any(x.rule == "R5_DEPOT_INVENTORY" for x in v)


def test_r6_dispatch_normal_and_constrained():
    state = _make_base_state()
    # 1. Normal depot d-1: dispatch_capacity = 8000.0
    line1 = PlanLine(
        route_id="r-1",
        source_depot_id="d-1",
        destination_station_id="s-1",
        fuel_type="DIESEL",
        quantity=4000.0,
    )
    # If 5000.0 already used, 5000 + 4000 = 9000 > 8000
    v1 = check_line(state, line1, used_dispatch={"d-1": 5000.0})
    assert any(x.rule == "R6_DISPATCH" for x in v1)

    # 2. Constrained depot d-2: dispatch_capacity = 8000.0 * 0.5 = 4000.0
    state["routes"]["r-d2"] = {
        "id": "r-d2",
        "source_depot_id": "d-2",
        "destination_station_id": "s-1",
        "status": "AVAILABLE",
        "max_shipment": 5000.0,
        "transit_ticks": 2,
    }
    line2 = PlanLine(
        route_id="r-d2",
        source_depot_id="d-2",
        destination_station_id="s-1",
        fuel_type="DIESEL",
        quantity=4500.0,  # exceeds 4000.0 effective cap
    )
    v2 = check_line(state, line2)
    assert any(x.rule == "R6_DISPATCH" for x in v2)


def test_r7_station_capacity():
    state = _make_base_state()
    # Station s-1 inventory is 2000.0, capacity is 10000.0
    # Line quantity 8500 -> 2000 + 8500 = 10500 > 10000
    line = PlanLine(
        route_id="r-1",
        source_depot_id="d-1",
        destination_station_id="s-1",
        fuel_type="DIESEL",
        quantity=4500.0,
    )
    # Set current inventory to 6000 -> 6000 + 4500 = 10500
    state["stations"][0]["inventory"]["DIESEL"] = 6000.0
    v = check_line(state, line)
    assert any(x.rule == "R7_STATION_CAPACITY" for x in v)


def test_r7b_overflow_at_arrival():
    state = _make_base_state()
    # Station s-1 capacity is 10000.0, 98% is 9800.0
    # Current inventory = 8000.0, demand = 0.0
    # In-flight already has 1500.0 arriving at tick 12
    # New shipment of 1000.0 arrives at tick 12:
    # 8000 + 1500 + 1000 = 10500 > 9800
    state["stations"][0]["inventory"]["DIESEL"] = 8000.0
    state["inflight"] = [("s-1", "DIESEL", 12, 1500.0)]
    line = PlanLine(
        route_id="r-1",
        source_depot_id="d-1",
        destination_station_id="s-1",
        fuel_type="DIESEL",
        quantity=1000.0,
    )
    v = check_line(state, line)
    assert any(x.rule == "R7B_OVERFLOW_AT_ARRIVAL" for x in v)


def test_r8_min_size():
    state = _make_base_state()
    line_small = PlanLine(
        route_id="r-1",
        source_depot_id="d-1",
        destination_station_id="s-1",
        fuel_type="DIESEL",
        quantity=200.0,
    )
    # 1. Non-critical destination violates R8_MIN_SIZE
    v1 = check_line(state, line_small, critical=False)
    assert any(x.rule == "R8_MIN_SIZE" for x in v1)

    # 2. Critical destination allows small shipment
    v2 = check_line(state, line_small, critical=True)
    assert not any(x.rule == "R8_MIN_SIZE" for x in v2)


def test_r9_stale_for_execution():
    state = _make_base_state()
    state["stale"] = True
    line = PlanLine(
        route_id="r-1",
        source_depot_id="d-1",
        destination_station_id="s-1",
        fuel_type="DIESEL",
        quantity=1000.0,
    )
    # For advisory planning: passes
    v_advisory = check_line(state, line, for_execution=False)
    assert not any(x.rule == "R9_STALE" for x in v_advisory)

    # For execution: violates R9_STALE
    v_exec = check_line(state, line, for_execution=True)
    assert any(x.rule == "R9_STALE" for x in v_exec)

    # Degraded state also triggers R9_STALE
    state["stale"] = False
    state["degraded"] = True
    v_degraded = check_line(state, line, for_execution=True)
    assert any(x.rule == "R9_STALE" for x in v_degraded)


def test_depot_available_helper():
    state = _make_base_state()
    avail = depot_available(state, "d-1", "DIESEL", reserve={("d-1", "DIESEL"): 2000.0})
    assert avail == 8000.0

    # Non-existent depot
    assert depot_available(state, "d-unknown", "DIESEL") == 0.0


def test_station_room_at_arrival_helper():
    state = _make_base_state()
    # s-1 capacity 10000, 98% is 9800. Current inv 2000, demand 50/tick
    # arrival at tick 12 (evaluates at arrival step 12->13, inv = 2000 - 3*50 = 1850)
    # room = 9800 - 1850 = 7950
    room = station_room_at_arrival(state, "s-1", "DIESEL", 12)
    assert room == 7950.0

    # Non-existent station
    assert station_room_at_arrival(state, "s-unknown", "DIESEL", 12) == 0.0


def test_check_plan_tracking():
    state = _make_base_state()
    # Line 1: 4000L DIESEL from d-1 -> s-1
    # Line 2: 4000L PETROL from d-1 -> s-1 (total 8000L = dispatch cap)
    # Line 3: 1000L OCTANE from d-1 -> s-1 (exceeds dispatch cap)
    line1 = PlanLine("r-1", "d-1", "s-1", "DIESEL", 4000.0)
    state["routes"]["r-petrol"] = dict(state["routes"]["r-1"], id="r-petrol")
    state["routes"]["r-octane"] = dict(state["routes"]["r-1"], id="r-octane")
    line2 = PlanLine("r-petrol", "d-1", "s-1", "PETROL", 4000.0)
    line3 = PlanLine("r-octane", "d-1", "s-1", "OCTANE", 1000.0)

    valid, rejected = check_plan(state, [line1, line2, line3])
    assert len(valid) == 2
    assert len(rejected) == 1
    assert rejected[0][0].fuel_type == "OCTANE"
    assert any(x.rule == "R6_DISPATCH" for x in rejected[0][1])


def test_constraints_edge_cases_for_100_percent_coverage():
    # 1. _get_field with obj=None
    from app.decision.constraints import _get_field, _get_fuel_val
    assert _get_field(None, "foo", default=42) == 42

    # 2. _get_fuel_val with None container or non-dict/attr
    assert _get_fuel_val(None, "DIESEL") == 0.0
    assert _get_fuel_val("invalid", "DIESEL") == 0.0

    # 3. route_blocked with routes as a list where route is missing
    assert route_blocked(None, [{"id": "r-other", "status": "AVAILABLE"}], "r-missing", 0) is True

    # 4. route_blocked with non-disruption event and malformed event ticks (start/end None)
    malformed_events = [
        {"type": "station_surge", "status": "ACTIVE", "start_tick": 0, "end_tick": 10},
        {"type": "route_disruption", "status": "ACTIVE", "start_tick": None, "end_tick": 10},
    ]
    routes = {"r-1": {"id": "r-1", "status": "AVAILABLE"}}
    assert route_blocked(malformed_events, routes, "r-1", 0) is False

    # 5. depot_available when state["depots"] is a dict
    state_dict_depots = {
        "depots": {"d-1": {"id": "d-1", "inventory": {"DIESEL": 5000.0}}},
    }
    assert depot_available(state_dict_depots, "d-1", "DIESEL") == 5000.0

    # 6. station_room_at_arrival when state["stations"] is a dict, and when station capacity is zero
    state_dict_stations = {
        "tick": 0,
        "stations": {
            "s-1": {"id": "s-1", "capacity": {"DIESEL": 0.0}, "inventory": {"DIESEL": 0.0}},
            "s-2": {"id": "s-2", "capacity": {"DIESEL": 5000.0}, "inventory": {"DIESEL": 1000.0}},
        },
    }
    # Zero capacity returns 0.0
    assert station_room_at_arrival(state_dict_stations, "s-1", "DIESEL", 2) == 0.0
    # Normal station from dict returns safe headroom
    assert station_room_at_arrival(state_dict_stations, "s-2", "DIESEL", 2) > 0.0


def test_object_representations_and_corner_cases():
    # Test class-based representations instead of dicts
    class RouteObj:
        def __init__(self):
            self.id = "r-obj"
            self.source_depot_id = "d-obj"
            self.destination_station_id = "s-obj"
            self.status = "AVAILABLE"
            self.max_shipment = 5000.0
            self.transit_ticks = 2

    class DepotObj:
        def __init__(self):
            self.id = "d-obj"
            self.status = "OPEN"
            self.inventory = type("Inv", (), {"DIESEL": 10000.0})()
            self.dispatch_capacity_per_tick = 8000.0

    class StationObj:
        def __init__(self):
            self.id = "s-obj"
            self.status = "OPEN"
            self.capacity = type("Cap", (), {"DIESEL": 10000.0})()
            self.inventory = type("Inv", (), {"DIESEL": 2000.0})()

    class StateObj:
        def __init__(self):
            self.tick = 0
            self.stale = False
            self.degraded = False
            self.routes = [RouteObj()]
            self.depots = [DepotObj()]
            self.stations = [StationObj()]
            self.events = []

        def inflight(self):
            return []

        def demand(self):
            return {}

    state = StateObj()
    line = PlanLine("r-obj", "d-obj", "s-obj", "DIESEL", 2000.0)
    v = check_line(state, line)
    assert len(v) == 0
