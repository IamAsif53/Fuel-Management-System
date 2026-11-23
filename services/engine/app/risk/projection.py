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

from fsip_common.models import DemandObservation, Station


def estimate_demand(
    history: Sequence[DemandObservation | float | int] | None,
    capacity: float,
) -> float:
    """Estimate per-tick demand for a (station, fuel) pair.

    Rules:
    1. If history is available, take the mean of up to the last 8 observations.
    2. If no history is available, fallback to capacity * 0.004.
    3. Cannot be negative.
    """
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
            return max(0.0, float(mean_val))

    # Cold-start fallback: station_fuel_capacity * 0.004
    return max(0.0, float(capacity) * 0.004)


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
        if inv <= 0.0 and self.estimated_demand > 0.0 and not self.is_outage:
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
            demand_liters = 0.0 if self.is_outage else self.estimated_demand
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
) -> StationFuelProjection:
    """Build a projection for a single station and fuel combination."""
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
    est_demand = estimate_demand(demand_history, capacity)

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
    )


def project_world_state(
    state: Any,
    horizon_ticks: int = 96,
) -> dict[tuple[str, str], StationFuelProjection]:
    """Project all stations and fuels in a WorldState."""
    projections: dict[tuple[str, str], StationFuelProjection] = {}
    current_tick = state.tick if hasattr(state, "tick") else 0
    all_inflight = state.inflight() if hasattr(state, "inflight") else []
    demand_map = state.demand() if hasattr(state, "demand") else {}

    stations = state.stations if hasattr(state, "stations") else []
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
            )
            projections[(st_id, f)] = proj

    return projections
