"""Inventory projection module for Phase 04.

Implements deterministic inventory trajectory simulation for stations:
- Demand estimation (mean of last 8 rows or capacity * 0.004 fallback)
- In-flight arrival modeling (step arrival semantics)
- Stockout calculation and overflow tracking
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from fsip_common.models import DemandObservation, Depot, Station
from fsip_common.timeutil import expected_arrival_tick

__all__ = [
    "DepotFuelProjection",
    "StationFuelProjection",
    "WorldStateProjections",
    "estimate_demand",
    "expected_arrival_tick",
    "project_depot_fuel",
    "project_station_fuel",
    "project_world_state",
]


def estimate_demand(
    history: Sequence[DemandObservation | float | int] | None,
    capacity: float,
    multiplier: float = 1.0,
) -> float:
    """Estimate per-tick demand for a (station, fuel) pair.

    Rules:
    1. If history is available, take the mean of up to the last 8 observations.
    2. If no history is available, fallback to capacity * 0.004.
    3. Scaled by multiplier (e.g. station demand_multiplier or spike).
    4. Cannot be negative.
    """
    mult = max(0.0, float(multiplier))
    if history:
        # Take the last up to 8 rows
        last_8 = list(history)[-8:]
        values: list[float] = []
        for item in last_8:
            if isinstance(item, DemandObservation):
                values.append(float(item.demand_liters))
            elif isinstance(item, int | float):
                values.append(float(item))
            elif isinstance(item, dict) and "demand_liters" in item:
                values.append(float(item["demand_liters"]))
        if values:
            mean_val = sum(values) / len(values)
            return max(0.0, float(mean_val) * mult)

    # Cold-start fallback: station_fuel_capacity * 0.004 * multiplier
    return max(0.0, float(capacity) * 0.004 * mult)


@dataclass
class StationFuelProjection:
    """Projected trajectory and risk metrics for a single station and fuel type."""

    station_id: str
    fuel_type: str
    current_tick: int
    current_inventory: float
    capacity: float
    estimated_demand: float
    inflight_shipments: list[tuple[int, float]] = field(default_factory=list)
    horizon_ticks: int = 96
    tick_minutes: int = 15
    is_outage: bool = False
    demand_trajectory: Sequence[float] | None = None

    # Computed trajectories:
    # _inv_trajectory[i] is projected inventory at tick (current_tick + i), length = horizon_ticks + 1
    _inv_trajectory: list[float] = field(init=False, repr=False)
    _overflow_trajectory: list[float] = field(init=False, repr=False)
    _stockout_tick: int | None = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.current_inventory = max(0.0, min(float(self.current_inventory), float(self.capacity)))
        self.capacity = max(0.0, float(self.capacity))
        self.estimated_demand = max(0.0, float(self.estimated_demand))
        self._compute_trajectory()

    def _compute_trajectory(self) -> None:
        """Simulate step-by-step inventory progression over the projection horizon."""
        inv = self.current_inventory
        self._inv_trajectory = [inv]
        self._overflow_trajectory = [0.0]
        self._stockout_tick = None

        # Check immediate stockout at current_tick if current inventory is 0 and demand > 0
        step0_demand = (
            float(self.demand_trajectory[0])
            if (self.demand_trajectory and len(self.demand_trajectory) > 0)
            else self.estimated_demand
        )
        if inv <= 0.0 and step0_demand > 0.0 and not self.is_outage:
            self._stockout_tick = self.current_tick

        # Group in-flight arrivals by expected arrival tick
        arrivals_by_tick: dict[int, float] = {}
        for arr_tick, qty in self.inflight_shipments:
            if qty > 0:
                arrivals_by_tick[arr_tick] = arrivals_by_tick.get(arr_tick, 0.0) + float(qty)

        curr_t = self.current_tick
        for step in range(self.horizon_ticks):
            t = curr_t + step

            # 1. Demand occurs during tick t (if not in outage)
            if self.demand_trajectory and step < len(self.demand_trajectory):
                step_demand = max(0.0, float(self.demand_trajectory[step]))
            else:
                step_demand = self.estimated_demand

            demand_liters = 0.0 if self.is_outage else step_demand
            if inv < demand_liters and self._stockout_tick is None and demand_liters > 0.0:
                self._stockout_tick = t

            inv_after_demand = max(0.0, inv - demand_liters)

            # 2. In-flight arrivals during step t -> t+1
            # Shipments with expected_arrival_tick == t arrive during step t -> t+1,
            # delivering fuel into the station for instance tick t + 1.
            incoming = arrivals_by_tick.get(t, 0.0)

            total_inv = inv_after_demand + incoming
            if total_inv > self.capacity:
                overflow = total_inv - self.capacity
                inv = self.capacity
            else:
                overflow = 0.0
                inv = total_inv

            self._inv_trajectory.append(round(inv, 4))
            self._overflow_trajectory.append(round(overflow, 4))

    def inv_at_tick(self, k: int) -> float:
        """Return projected inventory at tick k."""
        if k <= self.current_tick:
            return self.current_inventory

        offset = k - self.current_tick
        if offset < len(self._inv_trajectory):
            return self._inv_trajectory[offset]

        # Extrapolate beyond pre-computed horizon
        last_inv = self._inv_trajectory[-1]
        extra_steps = offset - self.horizon_ticks
        demand_per_step = 0.0 if self.is_outage else self.estimated_demand
        return max(0.0, round(last_inv - (extra_steps * demand_per_step), 4))

    def overflow_at_tick(self, k: int) -> float:
        """Return projected arrival overflow at tick k."""
        if k <= self.current_tick:
            return 0.0
        offset = k - self.current_tick
        if offset < len(self._overflow_trajectory):
            return self._overflow_trajectory[offset]
        return 0.0

    @property
    def ticks_to_stockout(self) -> int | None:
        """Ticks until station runs out of fuel, or None if safe across horizon."""
        if self._stockout_tick is None:
            return None
        return max(0, self._stockout_tick - self.current_tick)

    @property
    def hours_to_stockout(self) -> float | None:
        """Hours until station runs out of fuel, or None if safe across horizon."""
        ticks = self.ticks_to_stockout
        if ticks is None:
            return None
        return round(ticks * (self.tick_minutes / 60.0), 2)

    @property
    def fill_pct_with_inflight(self) -> float:
        """Fill percentage including all active in-flight shipments."""
        if self.capacity <= 0.0:
            return 0.0
        total_inflight = sum(qty for _, qty in self.inflight_shipments if qty > 0)
        return min(1.0, max(0.0, (self.current_inventory + total_inflight) / self.capacity))

    @property
    def fill_pct_current(self) -> float:
        """Current physical fill percentage without in-flight shipments."""
        if self.capacity <= 0.0:
            return 0.0
        return min(1.0, max(0.0, self.current_inventory / self.capacity))


def project_station_fuel(
    station: Station | dict[str, Any],
    fuel_type: str,
    demand_history: Sequence[DemandObservation | float | int] | None,
    inflight: Sequence[tuple[str, str, int, float]] | Sequence[tuple[int, float]] | None,
    current_tick: int,
    horizon_ticks: int = 96,
    tick_minutes: int = 15,
    forecast_bundle: Any | None = None,
    demand_trajectory: Sequence[float] | None = None,
) -> StationFuelProjection:
    """Build a projection for a single station and fuel combination.

    Uses forecast_bundle p50 trajectory when available, with graceful fallback to estimated_demand.
    """
    def _extract_fuel_val(container: Any, fuel_key: str) -> float:
        if isinstance(container, dict):
            return float(container.get(fuel_key, 0.0))
        return float(getattr(container, fuel_key, 0.0))

    if isinstance(station, dict):
        station_id = station["id"]
        status = station.get("status", "OPEN")
        capacity = _extract_fuel_val(station.get("capacity", {}), fuel_type)
        inventory = _extract_fuel_val(station.get("inventory", {}), fuel_type)
    else:
        station_id = station.id
        status = station.status
        capacity = _extract_fuel_val(station.capacity, fuel_type)
        inventory = _extract_fuel_val(station.inventory, fuel_type)

    is_outage = (status == "OUTAGE")
    d_mult = float(
        getattr(station, "demand_multiplier", 1.0)
        if hasattr(station, "demand_multiplier")
        else (station.get("demand_multiplier", 1.0) if isinstance(station, dict) else 1.0) or 1.0
    )
    est_demand = estimate_demand(demand_history, capacity, multiplier=d_mult)

    # Extract p50 demand trajectory from forecast_bundle if not explicitly passed
    if demand_trajectory is None and forecast_bundle is not None:
        if hasattr(forecast_bundle, "get_trajectory"):
            demand_trajectory = forecast_bundle.get_trajectory(station_id, fuel_type, horizon_ticks)

    # Filter matching in-flight shipments for this (station, fuel)
    relevant_inflight: list[tuple[int, float]] = []
    if inflight:
        for item in inflight:
            if len(item) == 4:
                # (st_id, f_type, arr_tick, qty)
                s_id, f_t, arr_tick, qty = item  # type: ignore[misc]
                if s_id == station_id and f_t == fuel_type:
                    relevant_inflight.append((int(arr_tick), float(qty)))
            elif len(item) == 2:
                # (arr_tick, qty)
                arr_tick, qty = item  # type: ignore[misc]
                relevant_inflight.append((int(arr_tick), float(qty)))

    return StationFuelProjection(
        station_id=station_id,
        fuel_type=fuel_type,
        current_tick=current_tick,
        current_inventory=inventory,
        capacity=capacity,
        estimated_demand=est_demand,
        inflight_shipments=relevant_inflight,
        horizon_ticks=horizon_ticks,
        tick_minutes=tick_minutes,
        is_outage=is_outage,
        demand_trajectory=demand_trajectory,
    )


@dataclass
class DepotFuelProjection:
    """Projected trajectory and risk metrics for a single depot and fuel type.

    Implements Task 6.1 depot projection:
    - Current inventory + scheduled/delayed supply arrivals at planned_tick
    - Planned outflow = pending recommendations + average historical outflow estimate
    - Capacity clipping = predicted supply waste (alert DEPOT_OVERFLOW_RISK)
    - Projected days of supply and hours to depletion (alert DEPOT_DEPLETION_RISK)
    """

    depot_id: str
    fuel_type: str
    current_tick: int
    current_inventory: float
    capacity: float
    supply_arrivals: list[tuple[int, float]] = field(default_factory=list)
    outflow_trajectory: Sequence[float] | None = None
    average_outflow_per_tick: float = 0.0
    horizon_ticks: int = 48
    tick_minutes: int = 15

    _inv_trajectory: list[float] = field(init=False, repr=False)
    _waste_trajectory: list[float] = field(init=False, repr=False)
    _depletion_tick: int | None = field(init=False, repr=False)
    _first_overflow_tick: int | None = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.current_inventory = max(0.0, min(float(self.current_inventory), float(self.capacity)))
        self.capacity = max(0.0, float(self.capacity))
        self.average_outflow_per_tick = max(0.0, float(self.average_outflow_per_tick))
        self._compute_trajectory()

    def _compute_trajectory(self) -> None:
        inv = self.current_inventory
        self._inv_trajectory = [inv]
        self._waste_trajectory = [0.0]
        self._depletion_tick = None
        self._first_overflow_tick = None

        arrivals_by_tick: dict[int, float] = {}
        for planned_tick, qty in self.supply_arrivals:
            if qty > 0:
                arrivals_by_tick[planned_tick] = arrivals_by_tick.get(planned_tick, 0.0) + float(qty)

        curr_t = self.current_tick
        for step in range(self.horizon_ticks):
            t = curr_t + step
            if self.outflow_trajectory and step < len(self.outflow_trajectory):
                outflow = max(0.0, float(self.outflow_trajectory[step]))
            else:
                outflow = self.average_outflow_per_tick

            incoming = arrivals_by_tick.get(t, 0.0)

            if inv < outflow and self._depletion_tick is None and outflow > 0.0:
                self._depletion_tick = t

            inv_after_outflow = max(0.0, inv - outflow)
            total_inv = inv_after_outflow + incoming

            if total_inv > self.capacity:
                waste = total_inv - self.capacity
                inv = self.capacity
                if self._first_overflow_tick is None and waste > 0.0:
                    self._first_overflow_tick = t
            else:
                waste = 0.0
                inv = total_inv

            self._inv_trajectory.append(round(inv, 4))
            self._waste_trajectory.append(round(waste, 4))

    def inv_at_tick(self, k: int) -> float:
        """Return projected depot inventory at tick k."""
        if k <= self.current_tick:
            return self.current_inventory
        offset = k - self.current_tick
        if offset < len(self._inv_trajectory):
            return self._inv_trajectory[offset]
        last_inv = self._inv_trajectory[-1]
        extra_steps = offset - self.horizon_ticks
        return max(0.0, round(last_inv - (extra_steps * self.average_outflow_per_tick), 4))

    def waste_at_tick(self, k: int) -> float:
        """Return projected capacity overflow waste at tick k."""
        if k <= self.current_tick:
            return 0.0
        offset = k - self.current_tick
        if offset < len(self._waste_trajectory):
            return self._waste_trajectory[offset]
        return 0.0

    @property
    def ticks_to_depletion(self) -> int | None:
        if self._depletion_tick is None:
            return None
        return max(0, self._depletion_tick - self.current_tick)

    @property
    def hours_to_depletion(self) -> float | None:
        ticks = self.ticks_to_depletion
        if ticks is None:
            return None
        return round(ticks * (self.tick_minutes / 60.0), 2)

    @property
    def projected_days_of_supply(self) -> float:
        ticks_per_day = (24 * 60) // self.tick_minutes
        daily_outflow = self.average_outflow_per_tick * ticks_per_day
        if daily_outflow <= 0.0:
            return 99.0
        return round(self.current_inventory / daily_outflow, 2)

    @property
    def total_predicted_waste_liters(self) -> float:
        return sum(self._waste_trajectory)

    @property
    def ticks_to_overflow(self) -> int | None:
        if self._first_overflow_tick is None:
            return None
        return max(0, self._first_overflow_tick - self.current_tick)

    @property
    def hours_to_overflow(self) -> float | None:
        ticks = self.ticks_to_overflow
        if ticks is None:
            return None
        return round(ticks * (self.tick_minutes / 60.0), 2)


def project_depot_fuel(
    depot: Depot | dict[str, Any],
    fuel_type: str,
    supply_arrivals: Sequence[Any] | None,
    current_tick: int,
    connected_stations_demand: float = 0.0,
    pending_outflow: Sequence[float] | None = None,
    horizon_ticks: int = 48,
    tick_minutes: int = 15,
) -> DepotFuelProjection:
    """Build a projection for a single depot and fuel combination."""
    def _extract_val(container: Any, key: str) -> float:
        if isinstance(container, dict):
            return float(container.get(key, 0.0))
        return float(getattr(container, key, 0.0))

    if isinstance(depot, dict):
        depot_id = depot["id"]
        capacity = _extract_val(depot.get("capacity", {}), fuel_type)
        inventory = _extract_val(depot.get("inventory", {}), fuel_type)
    else:
        depot_id = depot.id
        capacity = _extract_val(depot.capacity, fuel_type)
        inventory = _extract_val(depot.inventory, fuel_type)

    # Relevant scheduled/delayed supply arrivals
    relevant_supply: list[tuple[int, float]] = []
    if supply_arrivals:
        for s in supply_arrivals:
            s_depot = s.get("depot_id") if isinstance(s, dict) else getattr(s, "depot_id", None)
            s_fuel = s.get("fuel_type") if isinstance(s, dict) else getattr(s, "fuel_type", None)
            s_status = s.get("status") if isinstance(s, dict) else getattr(s, "status", None)
            s_tick = s.get("planned_tick") if isinstance(s, dict) else getattr(s, "planned_tick", 0)
            s_qty = s.get("quantity") if isinstance(s, dict) else getattr(s, "quantity", 0.0)

            if s_depot == depot_id and s_fuel == fuel_type and s_status in ("SCHEDULED", "DELAYED"):
                relevant_supply.append((int(s_tick), float(s_qty)))

    # Estimate average per-tick outflow from connected stations' demand or fallback
    est_outflow = connected_stations_demand if connected_stations_demand > 0.0 else max(10.0, capacity * 0.003)

    return DepotFuelProjection(
        depot_id=depot_id,
        fuel_type=fuel_type,
        current_tick=current_tick,
        current_inventory=inventory,
        capacity=capacity,
        supply_arrivals=relevant_supply,
        outflow_trajectory=pending_outflow,
        average_outflow_per_tick=est_outflow,
        horizon_ticks=horizon_ticks,
        tick_minutes=tick_minutes,
    )


class WorldStateProjections(dict):
    """Container for station projections with backward-compatible dict indexing and .depots."""

    def __init__(
        self,
        station_projections: dict[tuple[str, str], StationFuelProjection],
        depot_projections: dict[tuple[str, str], DepotFuelProjection],
    ) -> None:
        super().__init__(station_projections)
        self.stations = station_projections
        self.depots = depot_projections


def project_world_state(
    state: Any,
    horizon_ticks: int = 96,
) -> WorldStateProjections:
    """Project all stations and depots across all fuel types in WorldState."""
    station_projections: dict[tuple[str, str], StationFuelProjection] = {}
    depot_projections: dict[tuple[str, str], DepotFuelProjection] = {}

    current_tick = state.tick if hasattr(state, "tick") else 0
    all_inflight = state.inflight() if hasattr(state, "inflight") else []
    demand_map = state.demand() if hasattr(state, "demand") else {}
    forecast_bundle = getattr(state, "forecast_bundle", None)
    supply_arrivals = state.supply if hasattr(state, "supply") else []

    stations = state.stations if hasattr(state, "stations") else []
    routes = state.routes if hasattr(state, "routes") else []
    depots = state.depots if hasattr(state, "depots") else []

    # 1. Station projections
    for st in stations:
        st_id = st.id if hasattr(st, "id") else st["id"]
        for f in ("DIESEL", "PETROL", "OCTANE"):
            history = demand_map.get((st_id, f))
            proj = project_station_fuel(
                station=st,
                fuel_type=f,
                demand_history=history,
                inflight=all_inflight,
                current_tick=current_tick,
                horizon_ticks=horizon_ticks,
                forecast_bundle=forecast_bundle,
            )
            station_projections[(st_id, f)] = proj

    # 2. Depot projections (aggregating connected station demands)
    # Map which stations are connected to each depot
    depot_station_map: dict[str, set[str]] = {}
    for r in routes:
        d_id = r.origin_depot_id if hasattr(r, "origin_depot_id") else r.get("origin_depot_id")
        s_id = r.destination_station_id if hasattr(r, "destination_station_id") else r.get("destination_station_id")
        if d_id and s_id:
            depot_station_map.setdefault(d_id, set()).add(s_id)

    for d in depots:
        d_id = d.id if hasattr(d, "id") else d["id"]
        connected = depot_station_map.get(d_id, set())

        for f in ("DIESEL", "PETROL", "OCTANE"):
            # Sum estimated demand of connected stations
            connected_demand = sum(
                station_projections[(s_id, f)].estimated_demand
                for s_id in connected
                if (s_id, f) in station_projections
            )
            d_proj = project_depot_fuel(
                depot=d,
                fuel_type=f,
                supply_arrivals=supply_arrivals,
                current_tick=current_tick,
                connected_stations_demand=connected_demand,
                horizon_ticks=horizon_ticks,
            )
            depot_projections[(d_id, f)] = d_proj

    return WorldStateProjections(station_projections, depot_projections)

