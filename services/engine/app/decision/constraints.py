"""Feasibility constraints engine for FSIP allocations.

Implements all shared feasibility rules defined in 05_INTELLIGENCE_DESIGN.md §4.1
and 23_MODULE_SPECS.md B12.

Rules:
R1_ROUTE_MATCH: Route exists and its source/destination match the requested depot and station.
R2_ROUTE_BLOCKED: Route must be AVAILABLE now and not disrupted at departure tick t+1.
R3_NODE_STATUS: Source depot status in {OPEN, CONSTRAINED}, Destination station status in {OPEN}.
R4_MAX_SHIPMENT: 0 < quantity <= route.max_shipment.
R5_DEPOT_INVENTORY: quantity <= depot.inventory[f] - reserve[d, f].
R6_DISPATCH: Depot dispatch capacity across all fuels <= dispatch_cap (50% if CONSTRAINED).
R7_STATION_CAPACITY: station.inventory[f] + quantity <= station.capacity[f] (simulator check).
R7B_OVERFLOW_AT_ARRIVAL: Projected inventory at arrival + quantity <= station.capacity[f] * 0.98.
R8_MIN_SIZE: quantity >= 300 L unless destination is CRITICAL.
R9_STALE: Disallow automatic execution if state is stale or degraded.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.risk.projection import project_station_fuel


@dataclass
class PlanLine:
    """A proposed allocation shipment line."""

    route_id: str
    source_depot_id: str
    destination_station_id: str
    fuel_type: str
    quantity: float
    reason: str = ""


@dataclass
class Violation:
    """A feasibility rule violation."""

    rule: str
    message: str


def _get_field(obj: Any, key: str, default: Any = None) -> Any:
    """Helper to extract a field from either an object or a dictionary."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def route_blocked(
    events: list[Any] | None,
    routes: dict[str, Any] | list[Any],
    route_id: str,
    tick: int,
) -> bool:
    """Check if a route is unavailable now or blocked at departure tick (tick + 1).

    Checks:
    1. Route status is not AVAILABLE right now.
    2. Any SCHEDULED or ACTIVE route_disruption event covers tick + 1 and matches route_id
       (an empty route_ids list in event parameters matches all routes).
    """
    # 1. Check current route status
    route_obj = None
    if isinstance(routes, dict):
        route_obj = routes.get(route_id)
    elif isinstance(routes, list):
        for r in routes:
            if _get_field(r, "id") == route_id:
                route_obj = r
                break

    if not route_obj:
        return True

    status = _get_field(route_obj, "status")
    if status != "AVAILABLE":
        return True

    # 2. Check route_disruption events covering departure tick (tick + 1)
    dep_tick = tick + 1
    if events:
        for ev in events:
            ev_type = _get_field(ev, "type")
            if ev_type != "route_disruption":
                continue

            ev_status = _get_field(ev, "status")
            if ev_status not in ("SCHEDULED", "ACTIVE"):
                continue

            start_t = _get_field(ev, "start_tick")
            end_t = _get_field(ev, "end_tick")
            if start_t is not None and end_t is not None:
                if not (start_t <= dep_tick <= end_t):
                    continue
            else:
                continue

            params = _get_field(ev, "parameters", {}) or {}
            route_ids = _get_field(params, "route_ids", [])
            # Empty list means ALL routes are disrupted
            if not route_ids or route_id in route_ids:
                return True

    return False


def route_safe_at_departure(state: Any, route_id: str, tick: int | None = None) -> bool:
    """Helper to check if a route is safe at departure tick."""
    events = _get_field(state, "events", [])
    routes = _get_field(state, "routes", {})
    cur_tick = _get_field(state, "tick", 0) if tick is None else tick
    return not route_blocked(events, routes, route_id, cur_tick)


def depot_available(
    state: Any,
    depot_id: str,
    fuel_type: str,
    reserve: dict[tuple[str, str], float] | None = None,
) -> float:
    """Compute available inventory at depot after subtracting safety reserve."""
    depots = _get_field(state, "depots", [])
    depot_obj = None
    if isinstance(depots, dict):
        depot_obj = depots.get(depot_id)
    else:
        for d in depots:
            if _get_field(d, "id") == depot_id:
                depot_obj = d
                break

    if not depot_obj:
        return 0.0

    inv_map = _get_field(depot_obj, "inventory", {})
    inv_val = _get_fuel_val(inv_map, fuel_type)

    res_val = 0.0
    if reserve:
        res_val = float(reserve.get((depot_id, fuel_type), 0.0))

    return max(0.0, inv_val - res_val)


def station_room_at_arrival(
    state: Any,
    station_id: str,
    fuel_type: str,
    arrival_tick: int,
) -> float:
    """Compute safe headroom available at arrival tick without exceeding 98% capacity."""
    stations = _get_field(state, "stations", [])
    station_obj = None
    if isinstance(stations, dict):
        station_obj = stations.get(station_id)
    else:
        for s in stations:
            if _get_field(s, "id") == station_id:
                station_obj = s
                break

    if not station_obj:
        return 0.0

    cap_map = _get_field(station_obj, "capacity", {})
    cap_val = _get_fuel_val(cap_map, fuel_type)
    if cap_val <= 0.0:
        return 0.0

    cur_tick = _get_field(state, "tick", 0)
    all_inflight = state.inflight() if callable(getattr(state, "inflight", None)) else _get_field(state, "inflight", [])
    demand_map = state.demand() if callable(getattr(state, "demand", None)) else _get_field(state, "demand", {})
    history = demand_map.get((station_id, fuel_type)) if isinstance(demand_map, dict) else None

    proj = project_station_fuel(
        station=station_obj,
        fuel_type=fuel_type,
        demand_history=history,
        inflight=all_inflight,
        current_tick=cur_tick,
        horizon_ticks=max(96, arrival_tick - cur_tick + 10),
    )

    inv_at_arrival = proj.inv_at_tick(arrival_tick + 1)
    safe_max = cap_val * 0.98
    return max(0.0, safe_max - inv_at_arrival)


def _find_entity(entities: Any, entity_id: str) -> Any | None:
    """Helper to locate an entity by ID from dict or list."""
    if isinstance(entities, dict):
        return entities.get(entity_id)
    if isinstance(entities, list):
        for e in entities:
            if _get_field(e, "id") == entity_id:
                return e
    return None


def _get_fuel_val(container: Any, fuel: str) -> float:
    """Helper to extract fuel capacity/inventory float."""
    if not container:
        return 0.0
    if isinstance(container, dict):
        return float(container.get(fuel, 0.0))
    return float(getattr(container, fuel, 0.0))


def check_line(
    state: Any,
    line: PlanLine,
    used_dispatch: dict[str, float] | None = None,
    reserve: dict[tuple[str, str], float] | None = None,
    demand_p10_until_arrival: float | None = None,
    critical: bool = False,
    for_execution: bool = False,
) -> list[Violation]:
    """Validate a single proposed PlanLine against all feasibility rules."""
    violations: list[Violation] = []
    used_disp = used_dispatch or {}
    cur_tick = _get_field(state, "tick", 0)

    # Rule R9_STALE: Disallow automatic execution if state is stale or degraded
    if for_execution:
        is_stale = bool(_get_field(state, "stale", False))
        is_degraded = bool(_get_field(state, "degraded", False))
        if is_stale or is_degraded:
            violations.append(
                Violation(
                    rule="R9_STALE",
                    message="State is stale or degraded; automatic execution is prohibited.",
                )
            )

    # Lookup routes, depots, stations
    routes = _get_field(state, "routes", {})
    depots = _get_field(state, "depots", [])
    stations = _get_field(state, "stations", [])
    events = _get_field(state, "events", [])

    route = _find_entity(routes, line.route_id)
    depot = _find_entity(depots, line.source_depot_id)
    station = _find_entity(stations, line.destination_station_id)

    # Rule R1_ROUTE_MATCH
    if not route:
        violations.append(Violation(rule="R1_ROUTE_MATCH", message=f"Route {line.route_id} does not exist."))
    else:
        src = _get_field(route, "source_depot_id")
        dst = _get_field(route, "destination_station_id")
        if src != line.source_depot_id or dst != line.destination_station_id:
            violations.append(
                Violation(
                    rule="R1_ROUTE_MATCH",
                    message=(
                        f"Route {line.route_id} endpoints ({src} -> {dst}) do not match line "
                        f"({line.source_depot_id} -> {line.destination_station_id})."
                    ),
                )
            )

    # Rule R2_ROUTE_BLOCKED
    if route and route_blocked(events, routes, line.route_id, cur_tick):
        violations.append(
            Violation(
                rule="R2_ROUTE_BLOCKED",
                message=(
                    f"Route {line.route_id} is unavailable or blocked by disruption at departure tick {cur_tick + 1}."
                ),
            )
        )

    # Rule R3_NODE_STATUS
    if not depot:
        violations.append(Violation(rule="R3_NODE_STATUS", message=f"Source depot {line.source_depot_id} not found."))
    else:
        d_status = _get_field(depot, "status")
        if d_status not in ("OPEN", "CONSTRAINED"):
            violations.append(
                Violation(
                    rule="R3_NODE_STATUS",
                    message=f"Source depot {line.source_depot_id} status '{d_status}' is neither OPEN nor CONSTRAINED.",
                )
            )

    if not station:
        violations.append(
            Violation(
                rule="R3_NODE_STATUS",
                message=f"Destination station {line.destination_station_id} not found.",
            )
        )
    else:
        s_status = _get_field(station, "status")
        if s_status != "OPEN":
            violations.append(
                Violation(
                    rule="R3_NODE_STATUS",
                    message=f"Destination station {line.destination_station_id} status '{s_status}' is not OPEN.",
                )
            )

    # Rule R8_MIN_SIZE
    if line.quantity < 300.0 and not critical:
        violations.append(
            Violation(
                rule="R8_MIN_SIZE",
                message=f"Shipment quantity {line.quantity:.1f} L is below minimum 300 L for non-critical station.",
            )
        )

    # Rule R4_MAX_SHIPMENT
    if route:
        max_ship = _get_field(route, "max_shipment")
        if max_ship is not None and (line.quantity > float(max_ship) or line.quantity <= 0.0):
            violations.append(
                Violation(
                    rule="R4_MAX_SHIPMENT",
                    message=f"Quantity {line.quantity:.1f} L exceeds route max shipment {max_ship} L or is <= 0.",
                )
            )

    # Rule R5_DEPOT_INVENTORY
    if depot:
        d_inv = _get_field(depot, "inventory", {})
        cur_d_fuel = _get_fuel_val(d_inv, line.fuel_type)
        res_fuel = reserve.get((line.source_depot_id, line.fuel_type), 0.0) if reserve else 0.0
        avail_d_fuel = cur_d_fuel - res_fuel
        if line.quantity > avail_d_fuel:
            violations.append(
                Violation(
                    rule="R5_DEPOT_INVENTORY",
                    message=(
                        f"Quantity {line.quantity:.1f} L exceeds depot available fuel {avail_d_fuel:.1f} L "
                        f"(inventory {cur_d_fuel:.1f} L, reserve {res_fuel:.1f} L)."
                    ),
                )
            )

    # Rule R6_DISPATCH
    if depot:
        raw_cap = _get_field(depot, "dispatch_capacity_per_tick", 0.0)
        d_status = _get_field(depot, "status")
        eff_dispatch_cap = float(raw_cap) * 0.5 if d_status == "CONSTRAINED" else float(raw_cap)
        currently_used = float(used_disp.get(line.source_depot_id, 0.0))
        if currently_used + line.quantity > eff_dispatch_cap:
            violations.append(
                Violation(
                    rule="R6_DISPATCH",
                    message=(
                        f"Dispatch demand ({currently_used + line.quantity:.1f} L) exceeds "
                        f"effective depot dispatch cap ({eff_dispatch_cap:.1f} L)."
                    ),
                )
            )

    # Rule R7_STATION_CAPACITY (immediate simulator check: current inventory + quantity <= capacity)
    if station:
        s_cap = _get_field(station, "capacity", {})
        s_inv = _get_field(station, "inventory", {})
        fuel_cap = _get_fuel_val(s_cap, line.fuel_type)
        fuel_inv = _get_fuel_val(s_inv, line.fuel_type)
        if fuel_inv + line.quantity > fuel_cap:
            violations.append(
                Violation(
                    rule="R7_STATION_CAPACITY",
                    message=(
                        f"Current inventory ({fuel_inv:.1f} L) + quantity ({line.quantity:.1f} L) "
                        f"exceeds capacity ({fuel_cap:.1f} L)."
                    ),
                )
            )

    # Rule R7B_OVERFLOW_AT_ARRIVAL (projected inventory at arrival + quantity <= capacity * 0.98)
    if station and route:
        transit = _get_field(route, "transit_ticks", 2)
        arrival_tick = cur_tick + int(transit)
        safe_room = station_room_at_arrival(state, line.destination_station_id, line.fuel_type, arrival_tick)
        if line.quantity > safe_room:
            violations.append(
                Violation(
                    rule="R7B_OVERFLOW_AT_ARRIVAL",
                    message=(
                        f"Quantity {line.quantity:.1f} L exceeds safe arrival headroom {safe_room:.1f} L "
                        "(98% capacity threshold)."
                    ),
                )
            )

    return violations


def check_plan(
    state: Any,
    lines: list[PlanLine],
    used_dispatch: dict[str, float] | None = None,
    reserve: dict[tuple[str, str], float] | None = None,
    critical_stations: set[str] | None = None,
    for_execution: bool = False,
) -> tuple[list[PlanLine], list[tuple[PlanLine, list[Violation]]]]:
    """Validate a multi-line plan, tracking cumulative dispatch usage.

    Returns:
    (valid_lines, rejected_lines_with_violations)
    """
    valid: list[PlanLine] = []
    rejected: list[tuple[PlanLine, list[Violation]]] = []
    disp_tracker = dict(used_dispatch or {})
    crit_set = critical_stations or set()

    for line in lines:
        is_crit = line.destination_station_id in crit_set
        v = check_line(
            state=state,
            line=line,
            used_dispatch=disp_tracker,
            reserve=reserve,
            critical=is_crit,
            for_execution=for_execution,
        )
        if v:
            rejected.append((line, v))
        else:
            valid.append(line)
            disp_tracker[line.source_depot_id] = disp_tracker.get(line.source_depot_id, 0.0) + line.quantity

    return valid, rejected
