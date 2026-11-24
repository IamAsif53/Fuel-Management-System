"""Rolling-Horizon Linear Programming (LP) Optimizer Module (Task 8.3 & 8.4).

Per PHASE_08_OPTIMIZER_AND_WHATIF.md, 05_INTELLIGENCE_DESIGN.md §4.3, and reference/policy_benchmark.py:
- Horizon H = 96 ticks with checkpoints [4, 8, 12, 16, 24, 32, 48, 64, 80, 96].
- Tentative future dispatch slots [4, 8, 12, 16, 24, 32, 48, 64].
- Implements F-SAFETY (rolling safety stock with k=16 ticks, noise=0.15, weight=2.0).
- Implements F-DEPOT-PUSH (prevents depot overflow clipping by pushing downstream, weight=20.0).
- Implements F-PRESTOCK (pre-stock targets before scheduled cuts of single-route stations).
- Scarcity-mode max-min coverage fairness term (z >= 0, weight=5.0).
- Overflow gap elimination using p10 demand guard path.
- Solver: PuLP 2.9.0 with CBC, 2-5s time limit.
- Automatic fallback to heuristic on timeout/infeasibility or OPTIMIZER_FORCE_FAIL=true.
- Full post-processing and re-validation via constraints.py.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any

import pulp
from fsip_common.logging import get_logger
from prometheus_client import REGISTRY, Counter, Histogram

from app.decision.constraints import PlanLine, check_plan
from app.decision.heuristic import plan_heuristic
from app.state.store import WorldState

logger = get_logger()

# ---------------------------------------------------------------- Prometheus Metrics
try:
    FSIP_SOLVER_DURATION_SECONDS = Histogram(
        "fsip_solver_duration_seconds",
        "PuLP solver optimization runtime in seconds",
        buckets=(0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0),
    )
except ValueError:
    FSIP_SOLVER_DURATION_SECONDS = REGISTRY._names_to_collectors.get(  # type: ignore[assignment]
        "fsip_solver_duration_seconds"
    )

try:
    FSIP_FALLBACK_ACTIVATIONS_TOTAL = Counter(
        "fsip_fallback_activations_total",
        "Total fallback activations by component",
        ["component"],
    )
except ValueError:
    FSIP_FALLBACK_ACTIVATIONS_TOTAL = REGISTRY._names_to_collectors.get(  # type: ignore[assignment]
        "fsip_fallback_activations_total"
    )

FUELS = ["DIESEL", "PETROL", "OCTANE"]
CHECKPOINTS = [4, 8, 12, 16, 24, 32, 48, 64, 80, 96]
FUTURE_SLOTS = [4, 8, 12, 16, 24, 32, 48, 64]
SAFETY_TICKS = 16
SAFETY_NOISE = 0.15
SAFETY_WEIGHT = 2.0
TERM_WEIGHT = 0.5
TERM_FILL = 0.90
DEPOT_OVERFLOW_WEIGHT = 20.0


def _val(obj: Any, key: str, default: Any = None) -> Any:
    """Helper to safely extract field from dict, Pydantic model, or dataclass."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


@dataclass
class OptimizerPlan:
    """Result returned by OptimizerPolicy.solve()."""

    lines: list[PlanLine]
    policy_name: str  # "optimizer" | "heuristic"
    policy_version: str  # "optimizer@1.0.0" | "heuristic@1.0.0"
    is_fallback: bool
    fallback_reason: str | None
    solver_status: str  # "Optimal" | "Infeasible" | "Timeout" | "Error" | "Heuristic"
    solve_duration_ms: float
    objective_value: float | None = None
    scarcity_active: dict[str, bool] = field(default_factory=dict)


class OptimizerPolicy:
    """Rolling-horizon LP decision engine using PuLP CBC."""

    def __init__(self, time_limit: int = 2) -> None:
        self.time_limit = time_limit

    def solve(
        self,
        store: WorldState,
        forecast_bundle: Any = None,
        force_fail: bool | None = None,
    ) -> OptimizerPlan:
        """Solve rolling-horizon LP and return post-processed dispatch lines."""
        t0 = time.perf_counter()

        # Check chaos drill override
        env_force = os.environ.get("OPTIMIZER_FORCE_FAIL", "false").lower() in ("true", "1")
        if force_fail or env_force:
            logger.info("optimizer.forced_fallback_active")
            if FSIP_FALLBACK_ACTIVATIONS_TOTAL is not None:
                FSIP_FALLBACK_ACTIVATIONS_TOTAL.labels(component="optimizer").inc()
            h_plan = plan_heuristic(state=store)
            h_lines = h_plan.lines
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            return OptimizerPlan(
                lines=h_lines,
                policy_name="heuristic",
                policy_version="heuristic@1.0.0",
                is_fallback=True,
                fallback_reason="OPTIMIZER_FORCE_FAIL=true",
                solver_status="Heuristic",
                solve_duration_ms=round(elapsed_ms, 2),
            )

        try:
            plan = self._build_and_solve_lp(store=store, forecast_bundle=forecast_bundle)
            elapsed = time.perf_counter() - t0
            if FSIP_SOLVER_DURATION_SECONDS is not None:
                FSIP_SOLVER_DURATION_SECONDS.observe(elapsed)
            plan.solve_duration_ms = round(elapsed * 1000.0, 2)
            return plan
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            logger.error("optimizer.solve_exception", error=str(exc), exc_info=True)
            if FSIP_FALLBACK_ACTIVATIONS_TOTAL is not None:
                FSIP_FALLBACK_ACTIVATIONS_TOTAL.labels(component="optimizer").inc()
            h_plan = plan_heuristic(state=store)
            h_lines = h_plan.lines
            return OptimizerPlan(
                lines=h_lines,
                policy_name="heuristic",
                policy_version="heuristic@1.0.0",
                is_fallback=True,
                fallback_reason=f"Solver exception: {exc}",
                solver_status="Error",
                solve_duration_ms=round(elapsed_ms, 2),
            )

    def _build_and_solve_lp(
        self,
        store: WorldState,
        forecast_bundle: Any = None,
    ) -> OptimizerPlan:
        current_tick = getattr(store, "tick", 0)
        max_h = CHECKPOINTS[-1]

        # Extract entities
        st_items = store.stations.values() if isinstance(store.stations, dict) else store.stations
        stations_dict = {st.id: st for st in st_items}

        dp_items = store.depots.values() if isinstance(store.depots, dict) else store.depots
        depots_dict = {d.id: d for d in dp_items}

        rt_items = store.routes.values() if isinstance(store.routes, dict) else store.routes
        routes_dict = {r.id: r for r in rt_items}

        events = getattr(store, "events", []) or []

        # Helper to check route blockage at tick
        def is_route_blocked(r_id: str, tick: int) -> bool:
            r_obj = routes_dict.get(r_id)
            if r_obj:
                status = _val(r_obj, "status", "")
                if str(status).upper() == "DISRUPTED":
                    return True
            for ev in events:
                ev_type = _val(ev, "type", "")
                if ev_type == "route_disruption":
                    st_tk = _val(ev, "start_tick", 0)
                    end_tk = _val(ev, "end_tick", 999999)
                    if st_tk <= tick <= end_tk:
                        params = _val(ev, "parameters", {}) or {}
                        r_targets = params.get("route_ids") or params.get("route_id")
                        if not r_targets:
                            return True
                        if isinstance(r_targets, list) and r_id in r_targets:
                            return True
                        if isinstance(r_targets, str) and r_id == r_targets:
                            return True
            return False

        # Helper to check station outage at tick
        def is_station_outage(st_id: str, tick: int) -> bool:
            st_obj = stations_dict.get(st_id)
            if st_obj:
                status = _val(st_obj, "status", "")
                if str(status).upper() == "OUTAGE":
                    return True
            for ev in events:
                ev_type = _val(ev, "type", "")
                if ev_type == "station_outage":
                    st_tk = _val(ev, "start_tick", 0)
                    end_tk = _val(ev, "end_tick", 999999)
                    if st_tk <= tick <= end_tk:
                        params = _val(ev, "parameters", {}) or {}
                        targets = params.get("station_ids") or params.get("station_id")
                        if not targets:
                            return True
                        if isinstance(targets, list) and st_id in targets:
                            return True
                        if isinstance(targets, str) and st_id == targets:
                            return True
            return False

        # Precompute cumulative demand: cumD[(s_id, f_type)][k]
        cum_d50: dict[tuple[str, str], list[float]] = {}
        cum_dp10: dict[tuple[str, str], list[float]] = {}

        for st_id, st in stations_dict.items():
            cap_map = _val(st, "capacity", {}) or {}
            st_mult = float(_val(st, "demand_multiplier", 1.0))
            for f_type in FUELS:
                cap = float(cap_map.get(f_type, 10000.0)) if isinstance(cap_map, dict) else 10000.0
                nominal_rate = cap * 0.004 * st_mult

                acc50 = 0.0
                acc10 = 0.0
                arr50 = [0.0]
                arr10 = [0.0]

                for k in range(max_h + 40):
                    sim_tk = current_tick + k
                    if is_station_outage(st_id, sim_tk):
                        rate50 = 0.0
                        rate10 = 0.0
                    else:
                        rate50 = nominal_rate
                        rate10 = nominal_rate * 0.85  # p10 guard for overflow prevention

                    acc50 += rate50
                    acc10 += rate10
                    arr50.append(acc50)
                    arr10.append(acc10)

                cum_d50[(st_id, f_type)] = arr50
                cum_dp10[(st_id, f_type)] = arr10

        # Detect Scarcity Mode: network cover < 3.0 days or supply ends tick <= 212
        scarcity_active: dict[str, bool] = {}
        for f_type in FUELS:
            total_station_inv = sum(
                float((_val(st, "inventory", {}) or {}).get(f_type, 0.0))
                for st in stations_dict.values()
            )
            total_depot_inv = sum(
                float((_val(d, "inventory", {}) or {}).get(f_type, 0.0))
                for d in depots_dict.values()
            )
            total_96_demand = sum(cum_d50[(st.id, f_type)][96] for st in stations_dict.values())
            # Available / demand over 96 ticks (1 day = 96 ticks)
            days_cover = (total_station_inv + total_depot_inv) / max(1.0, total_96_demand)
            scarcity_active[f_type] = days_cover < 3.0 or current_tick >= 212

        prob = pulp.LpProblem("fsip_allocation_optimizer", pulp.LpMinimize)

        # Decision Variables
        # x[r_id, f]: quantity to dispatch this tick
        # y[r_id, f, j]: tentative quantity to dispatch at future slot j
        x: dict[tuple[str, str], pulp.LpVariable] = {}
        y: dict[tuple[str, str, int], pulp.LpVariable] = {}

        for r_id, r in routes_dict.items():
            dst = _val(r, "destination_station_id", "")
            max_ship = float(_val(r, "max_shipment", 5000.0))
            st_dst = stations_dict.get(dst)
            st_open = False
            if st_dst:
                st_stat = _val(st_dst, "status", "")
                st_open = str(st_stat).upper() == "OPEN"

            for f_type in FUELS:
                # Rule 2: route must be available now AND NOT BLOCKED at departure tick t+1
                ok_now = (
                    st_open
                    and not is_route_blocked(r_id, current_tick)
                    and not is_route_blocked(r_id, current_tick + 1)
                )
                st_cap = float((_val(st_dst, "capacity", {}) or {}).get(f_type, 10000.0)) if st_dst else 10000.0
                st_inv = float((_val(st_dst, "inventory", {}) or {}).get(f_type, 5000.0)) if st_dst else 5000.0
                room_now = max(0.0, st_cap - st_inv)
                ub_x = min(2 * max_ship, room_now) if ok_now else 0.0

                x[(r_id, f_type)] = pulp.LpVariable(
                    f"x_{r_id}_{f_type}",
                    lowBound=0,
                    upBound=ub_x,
                )

                for j in FUTURE_SLOTS:
                    ok_j = st_open and not is_route_blocked(r_id, current_tick + j + 1)
                    ub_y = (2 * max_ship) if ok_j else 0.0
                    y[(r_id, f_type, j)] = pulp.LpVariable(
                        f"y_{r_id}_{f_type}_{j}",
                        lowBound=0,
                        upBound=ub_y,
                    )

        obj_terms: list[Any] = []
        cover_map: dict[tuple[str, str], tuple[Any, float]] = {}
        z_var = pulp.LpVariable("fairness_z", lowBound=0, upBound=10)

        # Known in-flight arrivals from state
        inflight: list[tuple[str, str, int, float]] = []
        for alloc in getattr(store, "allocations", []) or []:
            stat = _val(alloc, "status", "")
            if str(stat).upper() in ("IN_TRANSIT", "PENDING"):
                dst = _val(alloc, "destination_station_id", "")
                f = _val(alloc, "fuel_type", "")
                c_tk = _val(alloc, "created_tick", 0)
                t_tk = _val(alloc, "transit_ticks", 0)
                if not t_tk:
                    r_id = _val(alloc, "route_id", "")
                    if r_id in routes_dict:
                        t_tk = int(_val(routes_dict[r_id], "transit_ticks", 2))
                exp_arr = _val(alloc, "expected_arrival_tick", None)
                arr_tk = (exp_arr + 1) if exp_arr is not None else (c_tk + t_tk + 1)
                q = float(_val(alloc, "quantity", 0.0))
                inflight.append((dst, f, arr_tk, q))

        # Station inventory constraints across checkpoints
        for st_id, st in stations_dict.items():
            st_cap_map = _val(st, "capacity", {}) or {}
            st_inv_map = _val(st, "inventory", {}) or {}

            # Check single route cut / F-PRESTOCK requirement
            in_routes = [r_id for r_id, r in routes_dict.items() if _val(r, "destination_station_id", "") == st_id]
            is_single_route = len(in_routes) <= 1

            for f_type in FUELS:
                cap = float(st_cap_map.get(f_type, 10000.0)) if isinstance(st_cap_map, dict) else 10000.0
                curr_inv = float(st_inv_map.get(f_type, 5000.0)) if isinstance(st_inv_map, dict) else 5000.0
                prev_u = None

                for m in CHECKPOINTS:
                    arr_known = sum(
                        q for (dst, f, arr_tk, q) in inflight
                        if dst == st_id and f == f_type and (arr_tk - current_tick) < m
                    )
                    arr_x = pulp.lpSum(
                        x[(r_id, f_type)]
                        for r_id, r in routes_dict.items()
                        if _val(r, "destination_station_id", "") == st_id
                        and _val(r, "transit_ticks", 4) < m
                    )
                    arr_y = pulp.lpSum(
                        y[(r_id, f_type, j)]
                        for r_id, r in routes_dict.items()
                        for j in FUTURE_SLOTS
                        if _val(r, "destination_station_id", "") == st_id
                        and (j + _val(r, "transit_ticks", 4)) < m
                    )

                    u = pulp.LpVariable(f"u_{st_id}_{f_type}_{m}", lowBound=0)
                    o = pulp.LpVariable(f"o_{st_id}_{f_type}_{m}", lowBound=0)

                    # Dynamic inventory equation using p50 demand
                    inv_expr = (
                        curr_inv + arr_known + arr_x + arr_y
                        - cum_d50[(st_id, f_type)][m]
                        + u - o
                    )
                    prob += inv_expr >= 0

                    # Overflow constraint: use p10 demand to eliminate the 222 L overflow gap
                    inv_overflow_expr = (
                        curr_inv + arr_known + arr_x + arr_y
                        - cum_dp10[(st_id, f_type)][m]
                    )
                    prob += inv_overflow_expr <= cap * 0.98

                    if prev_u is not None:
                        prob += u >= prev_u
                    prev_u = u

                    decay_m = max(0.6, 1.0 - 0.4 * (m / max_h))
                    obj_terms.append(10.0 * decay_m * u)
                    obj_terms.append(40.0 * o)

                    # F-SAFETY: Rolling safety stock constraint
                    ss = min(
                        cap * 0.85,
                        (cum_d50[(st_id, f_type)][m + SAFETY_TICKS] - cum_d50[(st_id, f_type)][m])
                        + SAFETY_NOISE * cum_d50[(st_id, f_type)][m],
                    )
                    b = pulp.LpVariable(f"b_{st_id}_{f_type}_{m}", lowBound=0)
                    prob += b >= ss - inv_expr
                    obj_terms.append(SAFETY_WEIGHT * b)

                    # F-PRESTOCK: If single route or route cut expected, raise target inventory
                    if is_single_route and m <= 48:
                        horizon_demand = (
                            cum_d50[(st_id, f_type)][m + 24] - cum_d50[(st_id, f_type)][m]
                        )
                        prestock_target = min(cap * 0.90, horizon_demand)
                        ps_var = pulp.LpVariable(f"ps_{st_id}_{f_type}_{m}", lowBound=0)
                        prob += ps_var >= prestock_target - inv_expr
                        obj_terms.append(3.0 * ps_var)

                    # Terminal target at m = 96
                    if m == CHECKPOINTS[-1]:
                        target = min(cap * 0.9, cum_d50[(st_id, f_type)][max_h] - cum_d50[(st_id, f_type)][max_h - 32])
                        if TERM_FILL > 0:
                            target = max(target, TERM_FILL * cap)
                        sh = pulp.LpVariable(f"sh_{st_id}_{f_type}", lowBound=0)
                        prob += sh >= target - inv_expr
                        obj_terms.append(TERM_WEIGHT * sh)

                        dem_terminal = max(1.0, cum_d50[(st_id, f_type)][max_h] - cum_d50[(st_id, f_type)][max_h - 32])
                        cover_map[(st_id, f_type)] = (inv_expr, dem_terminal)

        # Depot inventory and dispatch constraints
        for d_id, d in depots_dict.items():
            d_cap_map = _val(d, "capacity", {}) or {}
            d_inv_map = _val(d, "inventory", {}) or {}
            d_disp_cap = float(_val(d, "dispatch_capacity_per_tick", 50000.0))

            for f_type in FUELS:
                d_cap = float(d_cap_map.get(f_type, 100000.0)) if isinstance(d_cap_map, dict) else 100000.0
                d_curr = float(d_inv_map.get(f_type, 50000.0)) if isinstance(d_inv_map, dict) else 50000.0

                for j in [0] + FUTURE_SLOTS:
                    # Scheduled supply arrivals
                    sup = sum(
                        float(_val(a, "quantity", 0.0))
                        for a in (getattr(store, "supply_arrivals", []) or getattr(store, "supply", []) or [])
                        if _val(a, "depot_id", "") == d_id
                        and _val(a, "fuel_type", "") == f_type
                        and str(_val(a, "status", "")).upper() != "ARRIVED"
                        and (_val(a, "planned_tick", 0) - current_tick) <= j
                    )

                    shipped = pulp.lpSum(
                        x[(r_id, f_type)]
                        for r_id, r in routes_dict.items()
                        if (getattr(r, "source_depot_id", "") or r.get("source_depot_id")) == d_id
                    ) + pulp.lpSum(
                        y[(r_id, f_type, jj)]
                        for r_id, r in routes_dict.items()
                        for jj in FUTURE_SLOTS
                        if (getattr(r, "source_depot_id", "") or r.get("source_depot_id")) == d_id and jj <= j
                    )

                    prob += shipped <= max(0.0, d_curr) + sup

                    # F-DEPOT-PUSH: Depot overflow clipping prevention
                    ov = pulp.LpVariable(f"dov_{d_id}_{f_type}_{j}", lowBound=0)
                    prob += d_curr + sup - shipped - ov <= d_cap
                    obj_terms.append(DEPOT_OVERFLOW_WEIGHT * ov)

            # Dispatch capacity constraint per tick
            prob += (
                pulp.lpSum(
                    x[(r_id, f)]
                    for r_id, r in routes_dict.items()
                    if (getattr(r, "source_depot_id", "") or r.get("source_depot_id")) == d_id
                    for f in FUELS
                )
                <= d_disp_cap
            )

            for j in FUTURE_SLOTS:
                prob += (
                    pulp.lpSum(
                        y[(r_id, f, j)]
                        for r_id, r in routes_dict.items()
                        if (getattr(r, "source_depot_id", "") or r.get("source_depot_id")) == d_id
                        for f in FUELS
                    )
                    <= d_disp_cap
                )

        # Route transit distance costs
        for (r_id, _f), v in x.items():
            r_obj = routes_dict[r_id]
            transit = float(_val(r_obj, "transit_ticks", 4))
            obj_terms.append(0.002 * transit * v)

        for (r_id, _f, j), v in y.items():
            r_obj = routes_dict[r_id]
            transit = float(_val(r_obj, "transit_ticks", 4))
            obj_terms.append(0.002 * transit * (1.0 + 0.0005 * j) * v)

        # Scarcity mode max-min fairness
        any_scarcity = any(scarcity_active.values())
        if any_scarcity and cover_map:
            for (st_id, _f_type), (inv_expr, dem_term) in cover_map.items():
                st_obj = stations_dict.get(st_id)
                st_stat = getattr(st_obj, "status", "") or (st_obj.get("status") if isinstance(st_obj, dict) else "")
                if st_stat.upper() == "OPEN":
                    prob += inv_expr >= z_var * dem_term
            obj_terms.append(-5.0 * z_var)

        prob += pulp.lpSum(obj_terms)

        # Solve PuLP problem
        solver = pulp.PULP_CBC_CMD(msg=False, timeLimit=self.time_limit)
        prob.solve(solver)

        status_str = pulp.LpStatus.get(prob.status, "Unknown")
        if status_str != "Optimal":
            logger.warning("optimizer.solve_not_optimal", status=status_str)
            if FSIP_FALLBACK_ACTIVATIONS_TOTAL is not None:
                FSIP_FALLBACK_ACTIVATIONS_TOTAL.labels(component="optimizer").inc()
            h_plan = plan_heuristic(state=store)
            h_lines = h_plan.lines
            return OptimizerPlan(
                lines=h_lines,
                policy_name="heuristic",
                policy_version="heuristic@1.0.0",
                is_fallback=True,
                fallback_reason=f"Solver returned {status_str}",
                solver_status=status_str,
                solve_duration_ms=0.0,
                scarcity_active=scarcity_active,
            )

        # Post-Processing: split into shipment lines <= max_shipment, round, filter < 300 L
        raw_lines: list[PlanLine] = []
        for (r_id, f_type), v in x.items():
            val = float(v.varValue or 0.0)
            if val <= 0.001:
                continue

            r_obj = routes_dict[r_id]
            src_depot = _val(r_obj, "source_depot_id", "")
            dst_station = _val(r_obj, "destination_station_id", "")
            max_ship = float(_val(r_obj, "max_shipment", 5000.0))

            q = val
            while q >= 300.0:
                part = min(q, max_ship)
                # Round to nearest 10 L
                rounded_part = round(part / 10.0) * 10.0
                if rounded_part >= 300.0:
                    raw_lines.append(
                        PlanLine(
                            route_id=r_id,
                            source_depot_id=src_depot,
                            destination_station_id=dst_station,
                            fuel_type=f_type,
                            quantity=float(rounded_part),
                            reason="optimizer_allocation",
                        )
                    )
                q -= part

        # Re-validate with constraints.py
        valid_lines, rejected = check_plan(store, raw_lines)
        if rejected:
            logger.warning(
                "optimizer.lines_dropped_by_constraints",
                dropped_count=len(rejected),
            )

        return OptimizerPlan(
            lines=valid_lines,
            policy_name="optimizer",
            policy_version="optimizer@1.0.0",
            is_fallback=False,
            fallback_reason=None,
            solver_status="Optimal",
            solve_duration_ms=0.0,
            objective_value=float(pulp.value(prob.objective) or 0.0),
            scarcity_active=scarcity_active,
        )
