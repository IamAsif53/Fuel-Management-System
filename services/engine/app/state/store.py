"""In-memory WorldState store for BUP Fuel Supply Resilience Platform.

Features:
- Encapsulates complete simulator state snapshot (instance, regions, depots,
  stations, routes, events, supply, allocations, metrics).
- Maintains per-(station_id, fuel_type) 2,000-row ring buffers for demand history, deduplicated by observation ID.
- Tracks per-resource last_good_at update timestamps.
- Preserves cached state upon partial or failed updates.
- Manages stale flag and degraded_reasons set.
- Projects internal state to API contract 21 §2.1 JSON structure via to_public_json().
"""
from __future__ import annotations

import copy
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fsip_common.models import (
    FUELS,
    Allocation,
    DemandObservation,
    Depot,
    Instance,
    Region,
    Route,
    SimEvent,
    SimMetrics,
    Station,
    SupplyArrival,
)
from pydantic import BaseModel, ConfigDict, Field

from app.sim.validation import ValidatedSnapshot

# =============================================================================
# Public Projection Schemas (API Contract 21 §2.1)
# =============================================================================

class WorldStateMetricsOut(BaseModel):
    """Metrics block in public WorldState projection."""

    model_config = ConfigDict(extra="ignore")

    service_level: float = 1.0
    served_liters: float = 0.0
    unmet_liters: float = 0.0
    allocation_liters: float = 0.0
    allocation_failures: int = 0
    overflow_liters_est: float = 0.0
    depot_clipped_liters_est: float = 0.0


class WorldStateOut(BaseModel):
    """Public WorldState representation matching API contract 21 §2.1."""

    model_config = ConfigDict(extra="ignore")

    sim_run_id: int
    tick: int
    sim_time: str | None = None
    tick_minutes: int = 15
    sim_status: str = "PAUSED"
    clock_label: str = ""
    generated_at: str
    age_seconds: float = 0.0
    stale: bool = False
    degraded: bool = False
    degraded_reasons: list[str] = Field(default_factory=list)
    engine_mode: str = "NORMAL"
    operating_mode: str = "advisory"
    policy: str = "optimizer"
    scarcity: dict[str, bool] = Field(default_factory=lambda: {"DIESEL": False, "PETROL": False, "OCTANE": False})
    metrics: WorldStateMetricsOut = Field(default_factory=WorldStateMetricsOut)
    depots: list[dict[str, Any]] = Field(default_factory=list)
    stations: list[dict[str, Any]] = Field(default_factory=list)
    routes: list[dict[str, Any]] = Field(default_factory=list)
    events: list[dict[str, Any]] = Field(default_factory=list)
    supply_upcoming: list[dict[str, Any]] = Field(default_factory=list)
    allocations_active: list[dict[str, Any]] = Field(default_factory=list)
    alerts_open_count: dict[str, int] = Field(default_factory=lambda: {"INFO": 0, "WARNING": 0, "CRITICAL": 0})
    pending_recommendations: list[dict[str, Any]] = Field(default_factory=list)
    bottleneck: dict[str, Any] | None = None
    system: dict[str, Any] = Field(default_factory=dict)


# =============================================================================
# In-Memory WorldState Store
# =============================================================================

@dataclass
class WorldState:
    """In-memory state store aggregating simulator resources, observations, and telemetry."""

    sim_run_id: int = 1
    instance: Instance | None = None
    regions: list[Region] = field(default_factory=list)
    depots: list[Depot] = field(default_factory=list)
    stations: list[Station] = field(default_factory=list)
    routes: list[Route] = field(default_factory=list)
    events: list[SimEvent] = field(default_factory=list)
    supply: list[SupplyArrival] = field(default_factory=list)
    allocations: list[Allocation] = field(default_factory=list)
    metrics: SimMetrics | None = None

    # Ring buffers: per (station_id, fuel_type) max 2,000 observations
    demand_rows: dict[tuple[str, str], deque[DemandObservation]] = field(
        default_factory=lambda: defaultdict(lambda: deque(maxlen=2000))
    )
    _demand_seen_ids: dict[tuple[str, str], set[int]] = field(
        default_factory=lambda: defaultdict(set)
    )

    last_good_at: dict[str, float] = field(default_factory=dict)
    stale: bool = False
    degraded_reasons: set[str] = field(default_factory=set)

    engine_mode: str = "NORMAL"
    operating_mode: str = "advisory"
    policy: str = "optimizer"

    risk: dict[str | tuple[str, str], Any] = field(default_factory=dict)
    pending: list[Any] = field(default_factory=list)
    bottleneck: dict[str, Any] | None = None
    forecaster_label: str = "seasonal@builtin"
    forecast_bundle: Any = None

    # ---------------------------------------------------------------- Properties
    @property
    def tick(self) -> int:
        return self.instance.tick if self.instance is not None else 0

    @property
    def sim_time(self) -> datetime | None:
        return self.instance.sim_time if self.instance is not None else None

    @property
    def degraded(self) -> bool:
        return self.stale or bool(self.degraded_reasons)

    @property
    def demand(self) -> dict[tuple[str, str], deque[DemandObservation]]:
        """Alias for demand_rows to support Module Spec B4."""
        return self.demand_rows

    @property
    def snapshot(self) -> ValidatedSnapshot | None:
        """Construct ValidatedSnapshot if core simulator resources are populated."""
        if self.instance is not None and self.metrics is not None:
            return ValidatedSnapshot(
                instance=self.instance,
                regions=self.regions,
                depots=self.depots,
                stations=self.stations,
                routes=self.routes,
                events=self.events,
                supply=self.supply,
                allocations=self.allocations,
                metrics=self.metrics,
            )
        return None

    # ---------------------------------------------------------------- In-Flight Helper
    def inflight(self) -> list[tuple[str, str, int, float]]:
        """Return active shipments as (station_id, fuel_type, expected_arrival_tick, quantity)."""
        cur_tick = self.tick
        active: list[tuple[str, str, int, float]] = []
        for a in self.allocations:
            if a.status in ("PENDING", "IN_TRANSIT"):
                arr_tick = a.expected_arrival_tick if a.expected_arrival_tick is not None else (cur_tick + 2)
                active.append((a.destination_station_id, a.fuel_type, arr_tick, float(a.quantity)))
        return active

    # ---------------------------------------------------------------- Demand Ingestion
    def apply_demand(self, observations: list[DemandObservation | dict[str, Any]]) -> int:
        """Ingest demand observations into per-(station, fuel) ring buffers.

        Deduplicates by row ID. Retains at most 2,000 rows per series, evicting oldest.
        Returns the count of newly added observations.
        """
        if not observations:
            return 0

        parsed: list[DemandObservation] = []
        for item in observations:
            if isinstance(item, DemandObservation):
                parsed.append(item)
            elif isinstance(item, dict):
                parsed.append(DemandObservation.model_validate(item))

        # Sort chronologically
        parsed.sort(key=lambda o: (o.tick, o.id))

        added = 0
        for obs in parsed:
            key = (obs.station_id, obs.fuel_type)
            seen = self._demand_seen_ids[key]
            if obs.id in seen:
                continue

            q = self.demand_rows[key]
            if len(q) >= 2000:
                oldest = q[0]
                seen.discard(oldest.id)

            q.append(obs)
            seen.add(obs.id)
            added += 1

        self.last_good_at["demand"] = time.time()
        return added

    # ---------------------------------------------------------------- Snapshot Update
    def apply_snapshot(
        self,
        snapshot: ValidatedSnapshot | dict[str, Any] | None = None,
        *,
        partial: dict[str, Any] | None = None,
        demand_rows: list[DemandObservation | dict[str, Any]] | None = None,
        stale: bool | None = None,
        now: float | None = None,
        sim_run_id: int | None = None,
    ) -> None:
        """Apply full or partial snapshot update.

        Preserves existing good state for failed/omitted resources.
        Updates last_good_at and degraded_reasons accordingly.
        """
        ts = now if now is not None else time.time()

        if sim_run_id is not None:
            self.sim_run_id = sim_run_id

        if stale is not None:
            self.stale = bool(stale)
            if self.stale:
                self.degraded_reasons.add("stale_data")
            else:
                self.degraded_reasons.discard("stale_data")

        # 1. Direct ValidatedSnapshot instance
        if isinstance(snapshot, ValidatedSnapshot):
            self.instance = snapshot.instance
            self.regions = list(snapshot.regions)
            self.depots = list(snapshot.depots)
            self.stations = list(snapshot.stations)
            self.routes = list(snapshot.routes)
            self.events = list(snapshot.events)
            self.supply = list(snapshot.supply)
            self.allocations = list(snapshot.allocations)
            self.metrics = snapshot.metrics

            res_keys = (
                "instance", "regions", "depots", "stations", "routes",
                "events", "supply", "allocations", "metrics",
            )
            for k in res_keys:
                self.last_good_at[k] = ts
                self.degraded_reasons.discard(f"{k}_failed")

            if demand_rows:
                self.apply_demand(demand_rows)
            return

        # 2. Dictionary input (full or partial)
        data: dict[str, Any] = {}
        if isinstance(snapshot, dict):
            data.update(snapshot)
        if partial:
            data.update(partial)

        if not data and not demand_rows:
            return

        if "stale" in data and stale is None:
            self.stale = bool(data["stale"])
            if self.stale:
                self.degraded_reasons.add("stale_data")
            else:
                self.degraded_reasons.discard("stale_data")

        # Map resource aliases
        if "supply_arrivals" in data and "supply" not in data:
            data["supply"] = data["supply_arrivals"]

        core_resources = (
            "instance", "regions", "depots", "stations", "routes",
            "events", "supply", "allocations", "metrics",
        )

        for key in core_resources:
            if key not in data:
                continue

            val = data[key]
            if isinstance(val, Exception):
                self.degraded_reasons.add(f"{key}_failed")
                continue

            # Unwrap SimResult if passed
            raw_val = val.data if hasattr(val, "data") and not isinstance(val, BaseModel | list | dict) else val

            if key == "instance":
                self.instance = Instance.model_validate(raw_val) if isinstance(raw_val, dict) else raw_val
            elif key == "metrics":
                self.metrics = SimMetrics.model_validate(raw_val) if isinstance(raw_val, dict) else raw_val
            elif key == "regions":
                self.regions = [Region.model_validate(x) if isinstance(x, dict) else x for x in raw_val]
            elif key == "depots":
                self.depots = [Depot.model_validate(x) if isinstance(x, dict) else x for x in raw_val]
            elif key == "stations":
                self.stations = [Station.model_validate(x) if isinstance(x, dict) else x for x in raw_val]
            elif key == "routes":
                self.routes = [Route.model_validate(x) if isinstance(x, dict) else x for x in raw_val]
            elif key == "events":
                self.events = [SimEvent.model_validate(x) if isinstance(x, dict) else x for x in raw_val]
            elif key == "supply":
                self.supply = [SupplyArrival.model_validate(x) if isinstance(x, dict) else x for x in raw_val]
            elif key == "allocations":
                self.allocations = [Allocation.model_validate(x) if isinstance(x, dict) else x for x in raw_val]

            self.last_good_at[key] = ts
            self.degraded_reasons.discard(f"{key}_failed")

        # Handle demand observations if provided
        dem_data = demand_rows or data.get("demand") or data.get("demand_history")
        if dem_data and not isinstance(dem_data, Exception):
            self.apply_demand(dem_data)

    def apply(self, partial: dict[str, Any | Exception], tick: int | None = None) -> None:
        """Alias for apply_snapshot to satisfy Module Spec B4."""
        self.apply_snapshot(partial=partial)
        if tick is not None and self.instance is not None:
            self.instance.tick = tick

    def reset(self, new_sim_run_id: int | None = None) -> None:
        """Clear simulator state resources for a new simulation run."""
        if new_sim_run_id is not None:
            self.sim_run_id = new_sim_run_id
        else:
            self.sim_run_id += 1
        self.instance = None
        self.regions.clear()
        self.depots.clear()
        self.stations.clear()
        self.routes.clear()
        self.events.clear()
        self.supply.clear()
        self.allocations.clear()
        self.metrics = None
        self.demand_rows.clear()
        self._demand_seen_ids.clear()
        self.last_good_at.clear()
        self.stale = False
        self.degraded_reasons.clear()
        self.risk.clear()
        self.pending.clear()
        self.bottleneck = None

    # ---------------------------------------------------------------- Public JSON Projection
    def to_public_json(self) -> dict[str, Any]:
        """Generate public JSON-serializable projection according to API Contract 21 §2.1.

        Returns an isolated, deep-copied dictionary to prevent external mutation of store state.
        """
        now_utc = datetime.now(UTC)
        generated_at = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

        cur_tick = self.tick
        tick_min = self.instance.tick_minutes if self.instance else 15
        sim_status = self.instance.status if self.instance else "PAUSED"

        if self.instance and self.instance.sim_time:
            st = self.instance.sim_time
            if st.tzinfo is None:
                st = st.replace(tzinfo=UTC)
            sim_time_str: str | None = st.strftime("%Y-%m-%dT%H:%M:%SZ")
            day = (cur_tick * tick_min) // (24 * 60) + 1
            clock_label = f"Day {day} {st.strftime('%H:%M')} · t={cur_tick}"
        else:
            sim_time_str = None
            clock_label = f"t={cur_tick}"

        t_inst = self.last_good_at.get("instance")
        age_seconds = max(0.0, round(time.time() - t_inst, 2)) if t_inst is not None else 0.0

        # 1. Metrics view
        if self.metrics is not None:
            metrics_view = {
                "service_level": float(self.metrics.service_level),
                "served_liters": float(self.metrics.served_demand_liters),
                "unmet_liters": float(self.metrics.unmet_demand_liters),
                "allocation_liters": float(self.metrics.allocation_liters),
                "allocation_failures": int(self.metrics.allocation_failures),
                "overflow_liters_est": 0.0,
                "depot_clipped_liters_est": 0.0,
            }
        else:
            metrics_view = {
                "service_level": 1.0,
                "served_liters": 0.0,
                "unmet_liters": 0.0,
                "allocation_liters": 0.0,
                "allocation_failures": 0,
                "overflow_liters_est": 0.0,
                "depot_clipped_liters_est": 0.0,
            }

        # 2. Depots view
        depots_view: list[dict[str, Any]] = []
        for d in self.depots:
            upcoming_supply = [
                {
                    "id": s.id,
                    "fuel_type": s.fuel_type,
                    "quantity": float(s.quantity),
                    "planned_tick": s.planned_tick,
                    "status": s.status,
                }
                for s in self.supply
                if s.depot_id == d.id and s.status in ("SCHEDULED", "DELAYED")
            ]
            depots_view.append({
                "id": d.id,
                "name": d.name,
                "region_id": d.region_id,
                "status": d.status,
                "dispatch_capacity_per_tick": float(d.dispatch_capacity_per_tick),
                "dispatch_used_this_tick": 0.0,
                "capacity": {k: float(v) for k, v in d.capacity.items()},
                "inventory": {k: float(v) for k, v in d.inventory.items()},
                "next_supply": upcoming_supply,
                "cover_hours": {k: 0.0 for k in FUELS},
                "headroom_before_next_supply": {
                    k: max(0.0, float(d.capacity[k] - d.inventory[k])) for k in FUELS
                },
                "constrained_self_cap": d.status == "CONSTRAINED",
            })

        # 3. Stations view
        stations_view: list[dict[str, Any]] = []
        for s in self.stations:
            inflight_map = {f: 0.0 for f in FUELS}
            for a in self.allocations:
                if a.destination_station_id == s.id and a.status in ("PENDING", "IN_TRANSIT"):
                    inflight_map[a.fuel_type] = inflight_map.get(a.fuel_type, 0.0) + float(a.quantity)

            fill_pct_map = {
                f: round(s.inventory[f] / s.capacity[f], 4) if s.capacity.get(f, 0) > 0 else 0.0
                for f in FUELS
            }

            routes_to_st = [r for r in self.routes if r.destination_station_id == s.id and r.status == "AVAILABLE"]

            risk_entry = self.risk.get(s.id) or {}
            risk_map: dict[str, Any] = {}
            for f in FUELS:
                if isinstance(risk_entry, dict) and f in risk_entry:
                    risk_map[f] = risk_entry[f]
                else:
                    risk_map[f] = {
                        "level": "LOW",
                        "hours_to_stockout": 99.0,
                        "stockout_prob_lead": 0.0,
                        "stockout_prob_horizon": 0.0,
                        "inventory": float(s.inventory[f]),
                        "inflight": inflight_map[f],
                        "fill_pct": fill_pct_map[f],
                        "cover_hours": 99.0,
                        "drivers": [],
                        "forecast_model": "seasonal@builtin",
                    }

            stations_view.append({
                "id": s.id,
                "name": s.name,
                "region_id": s.region_id,
                "status": s.status,
                "demand_profile": s.demand_profile,
                "demand_multiplier": float(s.demand_multiplier),
                "capacity": {k: float(v) for k, v in s.capacity.items()},
                "inventory": {k: float(v) for k, v in s.inventory.items()},
                "inflight": inflight_map,
                "fill_pct": fill_pct_map,
                "risk": risk_map,
                "routes_available": len(routes_to_st),
                "single_route": len(routes_to_st) == 1,
            })

        # 4. Routes view
        routes_view = [
            {
                "id": r.id,
                "source_depot_id": r.source_depot_id,
                "destination_station_id": r.destination_station_id,
                "transit_ticks": r.transit_ticks,
                "max_shipment": float(r.max_shipment),
                "status": r.status,
                "scheduled_disruption": None,
            }
            for r in self.routes
        ]

        # 5. Events view
        events_view = [e.model_dump(mode="json") for e in self.events]

        # 6. Upcoming supply
        supply_upcoming = [
            {
                "id": s.id,
                "depot_id": s.depot_id,
                "fuel_type": s.fuel_type,
                "quantity": float(s.quantity),
                "planned_tick": s.planned_tick,
                "status": s.status,
            }
            for s in self.supply
            if s.status in ("SCHEDULED", "DELAYED")
        ]

        # 7. Active allocations
        allocations_active: list[dict[str, Any]] = []
        for a in self.allocations:
            if a.status in ("PENDING", "IN_TRANSIT"):
                dep_tick = a.departure_tick if a.departure_tick is not None else a.created_tick
                arr_tick = a.expected_arrival_tick if a.expected_arrival_tick is not None else (dep_tick + 2)
                if arr_tick > dep_tick:
                    progress = max(0.0, min(1.0, round((cur_tick - dep_tick) / (arr_tick - dep_tick), 2)))
                else:
                    progress = 0.0

                allocations_active.append({
                    "sim_allocation_id": a.id,
                    "decision_id": 0,
                    "line_id": 0,
                    "idempotency_key": a.idempotency_key,
                    "route_id": a.route_id,
                    "source_depot_id": a.source_depot_id,
                    "destination_station_id": a.destination_station_id,
                    "fuel_type": a.fuel_type,
                    "quantity": float(a.quantity),
                    "status": a.status,
                    "created_tick": a.created_tick,
                    "departure_tick": a.departure_tick,
                    "expected_arrival_tick": a.expected_arrival_tick,
                    "progress": progress,
                })

        # 8. System view
        sim_down = (
            self.stale
            or "instance_failed" in self.degraded_reasons
            or "simulator_unavailable" in self.degraded_reasons
        )
        system_view = {
            "simulator": "DOWN" if sim_down else "UP",
            "sse": "CONNECTED",
            "intelligence": "DOWN" if "prediction_service_down" in self.degraded_reasons else "UP",
            "db": "UP",
            "redis": "UP",
            "breakers": {
                "simulator": "OPEN" if "simulator_breaker_open" in self.degraded_reasons else "CLOSED",
                "intelligence": "OPEN" if "intelligence_breaker_open" in self.degraded_reasons else "CLOSED",
            },
            "forecaster": self.forecaster_label,
        }

        output: dict[str, Any] = {
            "sim_run_id": self.sim_run_id,
            "tick": cur_tick,
            "sim_time": sim_time_str,
            "tick_minutes": tick_min,
            "sim_status": sim_status,
            "clock_label": clock_label,
            "generated_at": generated_at,
            "age_seconds": age_seconds,
            "stale": self.stale,
            "degraded": self.degraded,
            "degraded_reasons": sorted(list(self.degraded_reasons)),
            "engine_mode": self.engine_mode,
            "operating_mode": self.operating_mode,
            "policy": self.policy,
            "scarcity": {"DIESEL": False, "PETROL": False, "OCTANE": False},
            "metrics": metrics_view,
            "depots": depots_view,
            "stations": stations_view,
            "routes": routes_view,
            "events": events_view,
            "supply_upcoming": supply_upcoming,
            "allocations_active": allocations_active,
            "alerts_open_count": {"INFO": 0, "WARNING": 0, "CRITICAL": 0},
            "pending_recommendations": copy.deepcopy(self.pending),
            "bottleneck": copy.deepcopy(self.bottleneck),
            "system": system_view,
        }

        return copy.deepcopy(output)
