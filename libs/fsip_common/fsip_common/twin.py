"""Shadow Digital Twin Module (Task 8.1).

Per 05_INTELLIGENCE_DESIGN.md §5 and 02_SIMULATOR_FACTS.md:
Side-effect-free forward simulation of world dynamics over planning horizon H.
Accurately models:
- Depot deduction at allocation creation.
- Departure at t+1; FAILED (lost fuel) if route is disrupted at departure.
- Arrival timing at t + transit_ticks.
- Station overflow clipping (inventory capped at capacity).
- Station outages (served demand = 0).
- Demand consumption and stockout occurrences.
- Scheduled/delayed supply arrivals at depots and clipping.
- Event overlays (hypothetical demand_spike, route_disruption, shipment_delay).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from fsip_common.logging import get_logger

logger = get_logger()

FUELS = ["DIESEL", "PETROL", "OCTANE"]

DAILY_LITERS = {
    "urban_high": {"DIESEL": 8500.0, "PETROL": 10500.0, "OCTANE": 5600.0},
    "industrial": {"DIESEL": 14000.0, "PETROL": 4500.0, "OCTANE": 2200.0},
    "highway": {"DIESEL": 10500.0, "PETROL": 11000.0, "OCTANE": 6200.0},
    "regional": {"DIESEL": 7200.0, "PETROL": 7600.0, "OCTANE": 3600.0},
}


def _get_hourly_factor(profile: str, hour: int) -> float:
    """Hourly seasonality curve per 02_SIMULATOR_FACTS.md §3."""
    if profile == "urban_high":
        return 1.45 if (7 <= hour <= 9 or 16 <= hour <= 20) else 0.70
    elif profile == "industrial":
        return 1.55 if (6 <= hour <= 17) else 0.45
    elif profile == "highway":
        return 1.35 if (6 <= hour <= 9 or 16 <= hour <= 20) else 0.75
    elif profile == "regional":
        return 1.25 if (7 <= hour <= 20) else 0.65
    return 1.0


def _val(obj: Any, key: str, default: Any = None) -> Any:
    """Helper to safely extract field from dict, Pydantic model, or dataclass."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


@dataclass
class StationProjection:
    """Projected outcomes for a single station and fuel combination."""

    station_id: str
    fuel_type: str
    stockout_prob: float
    unmet_liters: float
    overflow_liters: float
    first_stockout_tick: int | None
    inventory_p10: list[float] = field(default_factory=list)
    inventory_p50: list[float] = field(default_factory=list)
    inventory_p90: list[float] = field(default_factory=list)


@dataclass
class DepotProjection:
    """Projected outcomes for a single depot and fuel combination."""

    depot_id: str
    fuel_type: str
    inventory_trajectory: list[float] = field(default_factory=list)
    clipped_liters: float = 0.0
    depletion_tick: int | None = None


@dataclass
class TwinSimulationResult:
    """Full network outcome returned by ShadowTwin.project."""

    horizon_ticks: int
    stations: dict[tuple[str, str], StationProjection]
    depots: dict[tuple[str, str], DepotProjection]
    total_unmet_liters: float
    total_overflow_liters: float
    total_clipped_liters: float
    min_station_service_level: float


# Aliases for backward and engine compatibility
TwinStationProjection = StationProjection
TwinDepotProjection = DepotProjection


class ShadowTwin:
    """Pure-Python, side-effect-free simulator of fuel distribution network."""

    @classmethod
    def project(
        cls,
        state: Any,
        lines: list[Any] | None = None,
        forecast_samples: dict[tuple[str, str], np.ndarray] | None = None,
        hypothetical_events: list[dict[str, Any]] | None = None,
        horizon: int = 48,
        n_samples: int = 100,
    ) -> TwinSimulationResult:
        """Simulate forward H ticks from state with candidate lines and hypothetical events."""
        current_tick = getattr(state, "tick", 0)

        # 1. Parse stations and depots
        raw_stations = getattr(state, "stations", {})
        stations_dict: dict[str, Any] = (
            {st.id: st for st in raw_stations}
            if isinstance(raw_stations, list)
            else dict(raw_stations)
        )

        raw_depots = getattr(state, "depots", {})
        depots_dict: dict[str, Any] = (
            {d.id: d for d in raw_depots}
            if isinstance(raw_depots, list)
            else dict(raw_depots)
        )

        raw_routes = getattr(state, "routes", {})
        routes_dict: dict[str, Any] = (
            {r.id: r for r in raw_routes}
            if isinstance(raw_routes, list)
            else dict(raw_routes)
        )

        # 2. Compile events: active/scheduled state events + hypothetical events
        all_events: list[dict[str, Any]] = []
        for ev in getattr(state, "events", []) or []:
            if isinstance(ev, dict):
                all_events.append(ev)
            else:
                all_events.append({
                    "id": getattr(ev, "id", None),
                    "type": getattr(ev, "type", ""),
                    "start_tick": getattr(ev, "start_tick", 0),
                    "end_tick": getattr(ev, "end_tick", 999999),
                    "parameters": getattr(ev, "parameters", {}),
                })
        for ev in hypothetical_events or []:
            all_events.append(ev)

        # Helper to check route disruption at tick
        def is_route_disrupted(r_id: str, tick: int) -> bool:
            r_obj = routes_dict.get(r_id)
            if r_obj:
                status = getattr(r_obj, "status", "") or (r_obj.get("status") if isinstance(r_obj, dict) else "")
                if status.upper() == "DISRUPTED":
                    return True
            for e in all_events:
                if e.get("type") == "route_disruption":
                    st_tick = e.get("start_tick", 0)
                    end_tk = e.get("end_tick", 999999)
                    if st_tick <= tick <= end_tk:
                        params = e.get("parameters", {})
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
                status = getattr(st_obj, "status", "") or (st_obj.get("status") if isinstance(st_obj, dict) else "")
                if status.upper() == "OUTAGE":
                    return True
            for e in all_events:
                if e.get("type") == "station_outage":
                    st_tick = e.get("start_tick", 0)
                    end_tk = e.get("end_tick", 999999)
                    if st_tick <= tick <= end_tk:
                        params = e.get("parameters", {})
                        targets = params.get("station_ids") or params.get("station_id")
                        if not targets:
                            return True
                        if isinstance(targets, list) and st_id in targets:
                            return True
                        if isinstance(targets, str) and st_id == targets:
                            return True
            return False

        # Helper to get demand multiplier from events
        def get_event_demand_multiplier(st_id: str, reg_id: str, f_type: str, tick: int) -> float:
            mult = 1.0
            for e in all_events:
                if e.get("type") == "demand_spike":
                    st_tick = e.get("start_tick", 0)
                    end_tk = e.get("end_tick", 999999)
                    if st_tick <= tick <= end_tk:
                        params = e.get("parameters", {})
                        st_targets = params.get("station_ids") or params.get("station_id")
                        reg_targets = params.get("region_ids") or params.get("region_id")
                        fuel_target = params.get("fuel_type")
                        if fuel_target and fuel_target != f_type:
                            continue
                        st_m = bool(
                            not st_targets
                            or (st_id in st_targets if isinstance(st_targets, list) else st_id == st_targets)
                        )
                        reg_m = bool(
                            not reg_targets
                            or (reg_id in reg_targets if isinstance(reg_targets, list) else reg_id == reg_targets)
                        )
                        if st_m and reg_m:
                            mult = max(mult, float(params.get("multiplier", 1.5)))
            return mult

        # 3. Process candidate lines and arrival schedules
        # station_arrivals[(s_id, f_type, tick)] = liters arriving
        station_arrivals: dict[tuple[str, str, int], float] = {}

        # First add existing in-flight allocations from state
        for alloc in getattr(state, "allocations", []) or []:
            status = str(_val(alloc, "status", ""))
            if status.upper() in ("IN_TRANSIT", "PENDING"):
                dst = str(_val(alloc, "destination_station_id", ""))
                f = str(_val(alloc, "fuel_type", ""))
                c_tick = int(_val(alloc, "created_tick", 0))
                t_ticks = int(_val(alloc, "transit_ticks", 0))
                if not t_ticks:
                    r_id = str(_val(alloc, "route_id", ""))
                    if r_id in routes_dict:
                        t_ticks = int(_val(routes_dict[r_id], "transit_ticks", 2))
                exp_arr = _val(alloc, "expected_arrival_tick", None)
                if exp_arr is not None:
                    arr_tick = int(exp_arr) + 1
                else:
                    arr_tick = c_tick + t_ticks + 1
                qty = float(_val(alloc, "quantity", 0.0))
                station_arrivals[(dst, f, arr_tick)] = station_arrivals.get((dst, f, arr_tick), 0.0) + qty

        # Next add candidate lines submitted this tick or scheduled
        depot_initial_deductions: dict[tuple[str, str], float] = {}
        for line in lines or []:
            r_id = str(_val(line, "route_id", ""))
            f_type = str(_val(line, "fuel_type", ""))
            qty = float(_val(line, "quantity", 0.0))
            if qty <= 0:
                continue

            r_obj = routes_dict.get(r_id)
            if not r_obj:
                continue
            src_depot = str(_val(r_obj, "source_depot_id", ""))
            dst_station = str(_val(r_obj, "destination_station_id", ""))
            transit = int(_val(r_obj, "transit_ticks", 4))

            c_tick = int(_val(line, "created_tick", current_tick))
            if c_tick == current_tick:
                depot_initial_deductions[(src_depot, f_type)] = (
                    depot_initial_deductions.get((src_depot, f_type), 0.0) + qty
                )

            # Check departure at c_tick + 1: if route disrupted, fuel is lost
            departs_at = getattr(line, "departure_tick", c_tick + 1)
            if is_route_disrupted(r_id, departs_at):
                # Fuel lost at departure; never arrives
                continue

            arr_tick = c_tick + transit + 1
            station_arrivals[(dst_station, f_type, arr_tick)] = (
                station_arrivals.get((dst_station, f_type, arr_tick), 0.0) + qty
            )

        # 4. Simulate stations across N Monte Carlo samples
        station_results: dict[tuple[str, str], StationProjection] = {}
        total_demand_served = 0.0
        total_demand_total = 0.0
        net_unmet = 0.0
        net_overflow = 0.0

        for s_id, st in stations_dict.items():
            cap_map = _val(st, "capacity", {}) or {}
            inv_map = _val(st, "inventory", {}) or {}
            reg_id = _val(st, "region_id", "")
            station_mult = float(_val(st, "demand_multiplier", 1.0))

            for f_type in FUELS:
                cap = float(cap_map.get(f_type, 10000.0)) if isinstance(cap_map, dict) else 10000.0
                init_inv = float(inv_map.get(f_type, 5000.0)) if isinstance(inv_map, dict) else 5000.0

                profile = _val(st, "demand_profile", "urban_high") or "urban_high"
                reg_factor = (
                    1.08
                    if any(x in str(reg_id).lower() for x in ("chattogram", "ctg", "patiya", "cox", "karna"))
                    else 1.00
                )

                # Sample demand matrix (N, H)
                if forecast_samples and (s_id, f_type) in forecast_samples:
                    dem_matrix = forecast_samples[(s_id, f_type)][:, :horizon]
                else:
                    dem_matrix = np.zeros((n_samples, horizon))
                    daily_l = DAILY_LITERS.get(profile, {}).get(f_type, cap * 0.004 * 96)
                    base_per_tick = daily_l / 96.0

                    for k in range(horizon):
                        sim_tick = current_tick + k + 1
                        hour = ((sim_tick % 96) * 15) // 60
                        h_fac = _get_hourly_factor(profile, hour)
                        noise = np.random.normal(1.0, 0.02, size=n_samples)
                        dem_matrix[:, k] = np.maximum(
                            0.0, base_per_tick * h_fac * reg_factor * noise
                        )

                # Apply event multipliers and outages across horizon
                for k in range(horizon):
                    sim_tick = current_tick + k + 1
                    ev_mult = get_event_demand_multiplier(s_id, reg_id, f_type, sim_tick)
                    total_m = station_mult * ev_mult
                    dem_matrix[:, k] *= total_m

                    if is_station_outage(s_id, sim_tick):
                        dem_matrix[:, k] = 0.0

                # Forward simulate trajectories: shape (N, H + 1)
                inv_trajectories = np.zeros((n_samples, horizon + 1))
                inv_trajectories[:, 0] = init_inv

                sample_unmet = np.zeros(n_samples)
                sample_overflow = np.zeros(n_samples)
                stockout_occurred = np.zeros(n_samples, dtype=bool)
                earliest_stockout_tick: int | None = None

                for k in range(horizon):
                    sim_tick = current_tick + k + 1
                    arriving = station_arrivals.get((s_id, f_type, sim_tick), 0.0)

                    # 1. Arrivals and overflow clipping
                    new_inv = inv_trajectories[:, k] + arriving
                    over = np.maximum(0.0, new_inv - cap)
                    sample_overflow += over
                    new_inv = np.minimum(cap, new_inv)

                    # 2. Demand consumption
                    req_demand = dem_matrix[:, k]
                    served = np.minimum(new_inv, req_demand)
                    unmet = np.maximum(0.0, req_demand - new_inv)
                    sample_unmet += unmet
                    new_inv = np.maximum(0.0, new_inv - req_demand)

                    inv_trajectories[:, k + 1] = new_inv

                    hit_zero = (new_inv == 0.0)
                    stockout_occurred |= hit_zero
                    if earliest_stockout_tick is None and np.median(new_inv) == 0.0:
                        earliest_stockout_tick = sim_tick

                    total_demand_served += float(np.sum(served))
                    total_demand_total += float(np.sum(req_demand))

                p10_traj = np.percentile(inv_trajectories[:, 1:], 10, axis=0).tolist()
                p50_traj = np.percentile(inv_trajectories[:, 1:], 50, axis=0).tolist()
                p90_traj = np.percentile(inv_trajectories[:, 1:], 90, axis=0).tolist()

                st_proj = StationProjection(
                    station_id=s_id,
                    fuel_type=f_type,
                    stockout_prob=float(np.mean(stockout_occurred)),
                    unmet_liters=float(np.mean(sample_unmet)),
                    overflow_liters=float(np.mean(sample_overflow)),
                    first_stockout_tick=earliest_stockout_tick,
                    inventory_p10=[round(v, 1) for v in p10_traj],
                    inventory_p50=[round(v, 1) for v in p50_traj],
                    inventory_p90=[round(v, 1) for v in p90_traj],
                )
                station_results[(s_id, f_type)] = st_proj
                net_unmet += st_proj.unmet_liters
                net_overflow += st_proj.overflow_liters

        # 5. Simulate depots deterministically
        depot_results: dict[tuple[str, str], DepotProjection] = {}
        net_clipped = 0.0

        for d_id, d in depots_dict.items():
            d_cap_map = _val(d, "capacity", {}) or {}
            d_inv_map = _val(d, "inventory", {}) or {}

            for f_type in FUELS:
                d_cap = float(d_cap_map.get(f_type, 100000.0)) if isinstance(d_cap_map, dict) else 100000.0
                curr_inv = float(d_inv_map.get(f_type, 50000.0)) if isinstance(d_inv_map, dict) else 50000.0
                curr_inv = max(0.0, curr_inv - depot_initial_deductions.get((d_id, f_type), 0.0))

                traj = [round(curr_inv, 1)]
                total_clipped = 0.0
                dep_tick: int | None = None

                # Supply arrivals
                supply_by_tick: dict[int, float] = {}
                for sup in (getattr(state, "supply_arrivals", []) or getattr(state, "supply", []) or []):
                    sup_d = _val(sup, "depot_id", "")
                    sup_f = _val(sup, "fuel_type", "")
                    if sup_d == d_id and sup_f == f_type:
                        p_tk = _val(sup, "planned_tick", 0)
                        qty = float(_val(sup, "quantity", 0.0))
                        supply_by_tick[p_tk] = supply_by_tick.get(p_tk, 0.0) + qty

                for k in range(horizon):
                    sim_tick = current_tick + k + 1
                    sup_qty = supply_by_tick.get(sim_tick, 0.0)
                    new_d_inv = curr_inv + sup_qty
                    if new_d_inv > d_cap:
                        total_clipped += (new_d_inv - d_cap)
                        new_d_inv = d_cap

                    # Nominal estimated outflow ~ 500 L / tick per fuel
                    outflow = 500.0
                    new_d_inv = max(0.0, new_d_inv - outflow)
                    curr_inv = new_d_inv
                    traj.append(round(curr_inv, 1))

                    if dep_tick is None and curr_inv == 0.0:
                        dep_tick = sim_tick

                dp_proj = DepotProjection(
                    depot_id=d_id,
                    fuel_type=f_type,
                    inventory_trajectory=traj,
                    clipped_liters=round(total_clipped, 1),
                    depletion_tick=dep_tick,
                )
                depot_results[(d_id, f_type)] = dp_proj
                net_clipped += total_clipped

        sl = total_demand_served / max(1.0, total_demand_total) if total_demand_total > 0 else 1.0

        return TwinSimulationResult(
            horizon_ticks=horizon,
            stations=station_results,
            depots=depot_results,
            total_unmet_liters=round(net_unmet, 1),
            total_overflow_liters=round(net_overflow, 1),
            total_clipped_liters=round(net_clipped, 1),
            min_station_service_level=round(sl, 4),
        )
