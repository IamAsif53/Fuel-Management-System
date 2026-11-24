"""Deterministic greedy heuristic allocation policy for Phase 04.

Follows 05_INTELLIGENCE_DESIGN.md §4.2 and 23_MODULE_SPECS.md B13.
Produces a safe, deterministic plan that refills stations below REORDER_FILL (0.60)
or with risk >= HIGH, respecting all shared feasibility constraints.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.decision.constraints import (
    PlanLine,
    _find_entity,
    _get_field,
    _get_fuel_val,
    check_line,
    route_safe_at_departure,
    station_room_at_arrival,
)
from app.risk.projection import project_station_fuel

REORDER_FILL = 0.60
STATION_TARGET_FILL = 0.90
MIN_SHIPMENT_LITERS = 300.0


@dataclass
class Plan:
    """A collection of planned shipment lines and explanation metadata."""

    lines: list[PlanLine] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    policy: str = "heuristic@1.0.0"
    solver_status: str = "heuristic"
    has_critical_risk: bool = False


RISK_LEVEL_WEIGHTS = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}


def _assess_station_fuel_risk(
    proj: Any,
    station_status: str,
) -> tuple[int, str]:
    """Assess risk level: CRITICAL (4), HIGH (3), MEDIUM (2), LOW (1)."""
    if station_status != "OPEN":
        return 0, "INACTIVE"

    fill = proj.fill_pct_with_inflight
    h = proj.hours_to_stockout

    # Critical thresholds: fill < 20% or stockout within 4 hours
    if fill < 0.20 or (h is not None and h < 4.0):
        return 4, "CRITICAL"
    # High thresholds: fill < 40% or stockout within 8 hours
    if fill < 0.40 or (h is not None and h < 8.0):
        return 3, "HIGH"
    # Medium thresholds: fill < 60% or stockout within 16 hours
    if fill < 0.60 or (h is not None and h < 16.0):
        return 2, "MEDIUM"

    return 1, "LOW"


def plan_heuristic(
    state: Any,
    reserve: dict[tuple[str, str], float] | None = None,
    target_fill: float = STATION_TARGET_FILL,
    reorder_fill: float = REORDER_FILL,
) -> Plan:
    """Generate a deterministic greedy allocation plan for the current WorldState.

    Algorithm:
    1. For each open station and each fuel type (DIESEL, PETROL, OCTANE):
       - Project inventory and calculate fill_pct_with_inflight and hours_to_stockout.
       - Assess risk level.
       - Consider (station, fuel) if fill_pct_with_inflight < reorder_fill or risk >= HIGH.
    2. Sort targets by: (-risk_level, hours_to_stockout or 9999.0, fill_pct, station_id, fuel_type).
    3. For each target, examine candidate routes sorted by:
       - transit_ticks ascending
       - depot available supply descending
       - route_id, depot_id ascending (tie-breaker)
    4. Calculate feasible quantity and validate with constraints.
    5. Update local tracking state after each line.
    """
    cur_tick = _get_field(state, "tick", 0)
    stations = _get_field(state, "stations", [])
    routes = _get_field(state, "routes", {})
    depots = _get_field(state, "depots", [])

    all_inflight = state.inflight() if callable(getattr(state, "inflight", None)) else _get_field(state, "inflight", [])
    demand_map = state.demand() if callable(getattr(state, "demand", None)) else _get_field(state, "demand", {})
    forecast_bundle = getattr(state, "forecast_bundle", None)

    # Local tracking copies
    local_used_dispatch: dict[str, float] = {}
    local_depot_inv: dict[tuple[str, str], float] = {}
    local_inflight = list(all_inflight)

    # Initialize local depot inventory
    depot_list = depots.values() if isinstance(depots, dict) else depots
    for d in depot_list:
        d_id = _get_field(d, "id")
        d_inv = _get_field(d, "inventory", {})
        for f in ("DIESEL", "PETROL", "OCTANE"):
            local_depot_inv[(d_id, f)] = _get_fuel_val(d_inv, f)

    # Convert routes to list for inspection
    route_list = list(routes.values() if isinstance(routes, dict) else routes)

    # Identify needs across all (station, fuel) pairs
    station_list = list(stations.values() if isinstance(stations, dict) else stations)
    targets: list[dict[str, Any]] = []

    for st in station_list:
        st_id = _get_field(st, "id")
        st_status = _get_field(st, "status", "OPEN")
        if st_status != "OPEN":
            continue

        for fuel in ("DIESEL", "PETROL", "OCTANE"):
            history = demand_map.get((st_id, fuel)) if isinstance(demand_map, dict) else None
            proj = project_station_fuel(
                station=st,
                fuel_type=fuel,
                demand_history=history,
                inflight=local_inflight,
                current_tick=cur_tick,
                forecast_bundle=forecast_bundle,
            )

            risk_results = _get_field(state, "risk_results", {})
            if (st_id, fuel) in risk_results:
                r_res = risk_results[(st_id, fuel)]
                risk_label = r_res.risk_level
                risk_num = RISK_LEVEL_WEIGHTS.get(risk_label, 1)
                hours_to_stockout = r_res.hours_to_stockout if r_res.hours_to_stockout is not None else 9999.0
            else:
                risk_num, risk_label = _assess_station_fuel_risk(proj, st_status)
                hours_to_stockout = proj.hours_to_stockout if proj.hours_to_stockout is not None else 9999.0

            fill_incl = proj.fill_pct_with_inflight

            if fill_incl < reorder_fill or risk_num >= 3:
                s_cap = _get_fuel_val(_get_field(st, "capacity", {}), fuel)
                # Need = target_fill * capacity - current projected inventory
                inflight_sum = sum(q for _, q in proj.inflight_shipments)
                need = max(0.0, (s_cap * target_fill) - (proj.current_inventory + inflight_sum))
                targets.append(
                    {
                        "station_id": st_id,
                        "fuel_type": fuel,
                        "risk_num": risk_num,
                        "risk_label": risk_label,
                        "hours_to_stockout": hours_to_stockout,
                        "fill_pct": fill_incl,
                        "capacity": s_cap,
                        "need": need,
                        "is_critical": (risk_label == "CRITICAL"),
                    }
                )

    # Sort targets deterministically:
    # Most critical first (risk_num desc), lowest hours to stockout, lowest fill, stable IDs
    targets.sort(
        key=lambda x: (
            -x["risk_num"],
            x["hours_to_stockout"],
            x["fill_pct"],
            x["station_id"],
            x["fuel_type"],
        )
    )

    planned_lines: list[PlanLine] = []
    plan_reasons: list[str] = []

    for item in targets:
        st_id = item["station_id"]
        fuel = item["fuel_type"]
        rem_need = item["need"]
        is_crit = item["is_critical"]

        if rem_need <= 0.0:
            continue

        # Find eligible routes to this station
        candidate_routes: list[Any] = []
        for r in route_list:
            if _get_field(r, "destination_station_id") != st_id:
                continue
            r_id = _get_field(r, "id")
            if not route_safe_at_departure(state, r_id, cur_tick):
                continue
            src_d = _get_field(r, "source_depot_id")
            depot_obj = _find_entity(depot_list, src_d)
            if not depot_obj or _get_field(depot_obj, "status") not in ("OPEN", "CONSTRAINED"):
                continue

            candidate_routes.append(r)

        # Sort routes: lower transit ticks first, higher available inventory at depot, stable IDs
        def _route_sort_key(r: Any, target_fuel: str = fuel) -> tuple[int, float, str, str]:
            t_ticks = int(_get_field(r, "transit_ticks", 2))
            s_depot = _get_field(r, "source_depot_id")
            r_id = _get_field(r, "id")
            avail_inv = local_depot_inv.get((s_depot, target_fuel), 0.0)
            return (t_ticks, -avail_inv, r_id, s_depot)

        candidate_routes.sort(key=_route_sort_key)

        for route in candidate_routes:
            if rem_need <= 0.0:
                break

            r_id = _get_field(route, "id")
            d_id = _get_field(route, "source_depot_id")
            depot_obj = _find_entity(depot_list, d_id)
            if not depot_obj:
                continue

            transit = int(_get_field(route, "transit_ticks", 2))
            arrival_tick = cur_tick + transit

            # Feasibility checks for candidate quantity:
            # 1. Route max shipment
            r_max = float(_get_field(route, "max_shipment", 0.0))

            # 2. Depot inventory after reserve
            d_res = float(reserve.get((d_id, fuel), 0.0)) if reserve else 0.0
            d_avail = max(0.0, local_depot_inv.get((d_id, fuel), 0.0) - d_res)

            # 3. Depot dispatch remaining
            raw_disp_cap = float(_get_field(depot_obj, "dispatch_capacity_per_tick", 0.0))
            is_constrained = (_get_field(depot_obj, "status") == "CONSTRAINED")
            eff_disp_cap = raw_disp_cap * 0.5 if is_constrained else raw_disp_cap
            disp_used = local_used_dispatch.get(d_id, 0.0)
            disp_rem = max(0.0, eff_disp_cap - disp_used)

            # 4. Station headroom at arrival (98% threshold)
            st_headroom = station_room_at_arrival(state, st_id, fuel, arrival_tick)

            # 5. Station immediate capacity check (current physical headroom)
            st_obj = _find_entity(station_list, st_id)
            curr_st_inv = _get_fuel_val(_get_field(st_obj, "inventory", {}), fuel)
            st_cap = _get_fuel_val(_get_field(st_obj, "capacity", {}), fuel)
            imm_headroom = max(0.0, st_cap - curr_st_inv)

            # Max feasible quantity
            max_feasible = min(rem_need, r_max, d_avail, disp_rem, st_headroom, imm_headroom)
            qty = round(max_feasible, 1)

            # Minimum shipment check
            min_thresh = 1.0 if is_crit else MIN_SHIPMENT_LITERS
            if qty < min_thresh:
                continue

            reason_str = (
                f"{item['risk_label']} {fuel} at {st_id} "
                f"(fill {item['fill_pct']:.1%}, need {rem_need:.0f} L) -> from {d_id} via {r_id}"
            )
            candidate_line = PlanLine(
                route_id=r_id,
                source_depot_id=d_id,
                destination_station_id=st_id,
                fuel_type=fuel,
                quantity=qty,
                reason=reason_str,
            )

            # Final safety check against shared constraints
            violations = check_line(
                state=state,
                line=candidate_line,
                used_dispatch=local_used_dispatch,
                reserve=reserve,
                critical=is_crit,
                for_execution=False,
            )
            if violations:
                continue

            # Accepted: record line and update local state
            planned_lines.append(candidate_line)
            plan_reasons.append(reason_str)

            local_used_dispatch[d_id] = local_used_dispatch.get(d_id, 0.0) + qty
            local_depot_inv[(d_id, fuel)] = max(0.0, local_depot_inv.get((d_id, fuel), 0.0) - qty)
            local_inflight.append((st_id, fuel, arrival_tick, qty))
            rem_need = max(0.0, rem_need - qty)

    has_critical = any(
        item.get("is_critical", False)
        for item in targets
        if any(
            line.destination_station_id == item["station_id"] and line.fuel_type == item["fuel_type"]
            for line in planned_lines
        )
    )

    return Plan(
        lines=planned_lines,
        reasons=plan_reasons,
        policy="heuristic@1.0.0",
        solver_status="optimal" if planned_lines else "idle",
        has_critical_risk=has_critical,
    )
