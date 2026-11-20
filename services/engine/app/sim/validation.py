"""Simulator payload validation and topological cross-checking.

Features:
- ValidatedSnapshot Pydantic model aggregating all core simulator entities.
- validate_resource(name, raw): validates raw payloads into existing shared domain models.
  - List resources validated item-by-item with precise error locations.
  - Raises SimInvalidPayload with structured errors and raw payload on failure.
  - Increments Prometheus counter fsip_sim_invalid_responses_total{resource}.
- cross_check(snap): cross-entity consistency checks.
  - Route source depots exist in depots.
  - Route destination stations exist in stations.
  - Inventory <= capacity * 1.01 (1% tolerance).
  - All 3 fuel keys (DIESEL, PETROL, OCTANE) exist in fuel maps.
  - Numeric values are finite and nonnegative.
"""
from __future__ import annotations

import math
from typing import Any

from fsip_common.models import (
    FUELS,
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
    world_issues,
)
from prometheus_client import REGISTRY, Counter
from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from .client import SimInvalidPayload

# ---------------------------------------------------------------- Prometheus Metrics
try:
    FSIP_SIM_INVALID_RESPONSES = Counter(
        "fsip_sim_invalid_responses_total",
        "Total invalid simulator payloads received",
        ["resource"],
    )
except ValueError:
    FSIP_SIM_INVALID_RESPONSES = REGISTRY._names_to_collectors.get("fsip_sim_invalid_responses_total")  # type: ignore[assignment]


# ---------------------------------------------------------------- ValidatedSnapshot
class ValidatedSnapshot(BaseModel):
    """Aggregated and validated simulator world state snapshot."""

    model_config = ConfigDict(extra="ignore")

    instance: Instance
    regions: list[Region]
    depots: list[Depot]
    stations: list[Station]
    routes: list[Route]
    events: list[SimEvent]
    supply: list[SupplyArrival]
    allocations: list[Allocation]
    metrics: SimMetrics

    @model_validator(mode="before")
    @classmethod
    def _normalize_snapshot_input(cls, data: Any) -> Any:
        if isinstance(data, dict):
            unwrapped: dict[str, Any] = {}
            for k, v in data.items():
                if hasattr(v, "data"):
                    unwrapped[k] = v.data
                else:
                    unwrapped[k] = v
            # Simulator / client may supply 'supply_arrivals' instead of 'supply'
            if "supply" not in unwrapped and "supply_arrivals" in unwrapped:
                unwrapped["supply"] = unwrapped["supply_arrivals"]
            return unwrapped
        return data


# ---------------------------------------------------------------- Resource Mapping
# Maps clean_name -> (model_cls, is_list, canonical_label)
RESOURCE_REGISTRY: dict[str, tuple[type[BaseModel], bool, str]] = {
    "instance": (Instance, False, "instance"),
    "health": (Health, False, "health"),
    "metrics": (SimMetrics, False, "metrics"),
    "allocation_request": (AllocationRequest, False, "allocation_request"),
    "admin_step": (AdminStepResult, False, "admin_step"),
    "depots": (Depot, True, "depots"),
    "depot": (Depot, False, "depots"),
    "stations": (Station, True, "stations"),
    "station": (Station, False, "stations"),
    "routes": (Route, True, "routes"),
    "route": (Route, False, "routes"),
    "regions": (Region, True, "regions"),
    "region": (Region, False, "regions"),
    "events": (SimEvent, True, "events"),
    "event": (SimEvent, False, "events"),
    "supply": (SupplyArrival, True, "supply"),
    "supply_arrivals": (SupplyArrival, True, "supply"),
    "supply_arrival": (SupplyArrival, False, "supply"),
    "allocations": (Allocation, True, "allocations"),
    "allocation": (Allocation, False, "allocations"),
    "demand": (DemandObservation, True, "demand_history"),
    "demand_history": (DemandObservation, True, "demand_history"),
    "admin_audit": (AdminAuditEntry, True, "admin_audit"),
}


def _clean_resource_name(name: str) -> tuple[str, str]:
    """Normalize raw resource name and derive bounded metric label."""
    raw = str(name).strip().lower()
    clean = raw.strip("/").removeprefix("v1/").split("?")[0].replace("-", "_")
    if clean in RESOURCE_REGISTRY:
        return clean, RESOURCE_REGISTRY[clean][2]
    if clean == "snapshot":
        return "snapshot", "snapshot"
    bounded_label = clean if (clean.isidentifier() and len(clean) < 50) else "unknown"
    return clean, bounded_label


# ---------------------------------------------------------------- Cross Checks
def cross_check(snap: ValidatedSnapshot, *, increment_metric: bool = True) -> list[str]:
    """Perform topological, domain, and consistency cross-checks on a validated snapshot.

    Rules:
    1. Every route's source depot exists.
    2. Every route's destination station exists.
    3. Every depot/station inventory value <= corresponding capacity * 1.01.
    4. All three fuel keys (DIESEL, PETROL, OCTANE) exist in relevant fuel maps.
    5. Numeric values are finite and nonnegative.

    Returns a list of actionable issue descriptions (empty list = OK).
    """
    # 1-3. Route endpoints and capacity checks
    d_ids = {d.id for d in snap.depots}
    s_ids = {s.id for s in snap.stations}
    r_ids = {r.id for r in snap.regions} if snap.regions is not None else None

    # Check if all fuel maps are complete across all nodes
    all_fuels_complete = True
    for node in [*snap.depots, *snap.stations]:
        for f in FUELS:
            if f not in node.capacity or f not in node.inventory:
                all_fuels_complete = False
                break
        if not all_fuels_complete:
            break

    if all_fuels_complete:
        issues: list[str] = world_issues(
            depots=snap.depots,
            stations=snap.stations,
            routes=snap.routes,
            regions=snap.regions,
            tolerance=1.01,
        )
    else:
        # Check routes, regions, and overfull for available keys safely
        issues = []
        for r in snap.routes:
            if r.source_depot_id not in d_ids:
                issues.append(f"route {r.id}: unknown depot {r.source_depot_id}")
            if r.destination_station_id not in s_ids:
                issues.append(f"route {r.id}: unknown station {r.destination_station_id}")
        for node in [*snap.depots, *snap.stations]:
            if r_ids is not None and node.region_id not in r_ids:
                issues.append(f"{node.id}: unknown region {node.region_id}")
            for f in FUELS:
                if f in node.inventory and f in node.capacity:
                    if node.inventory[f] > node.capacity[f] * 1.01:
                        issues.append(f"{node.id}: {f} inventory {node.inventory[f]} > capacity {node.capacity[f]}")

    # 4. Fuel key completeness check across all nodes
    for node in [*snap.depots, *snap.stations]:
        for f in FUELS:
            if f not in node.capacity:
                issues.append(f"{node.id}: missing fuel key '{f}' in capacity")
            if f not in node.inventory:
                issues.append(f"{node.id}: missing fuel key '{f}' in inventory")

    # 5. Non-negative and finite numeric values
    for node in [*snap.depots, *snap.stations]:
        for f in FUELS:
            cap = node.capacity.get(f)
            if cap is not None and (not math.isfinite(cap) or cap < 0):
                issues.append(f"{node.id}: {f} capacity must be finite and >= 0, got {cap}")
            inv = node.inventory.get(f)
            if inv is not None and (not math.isfinite(inv) or inv < 0):
                issues.append(f"{node.id}: {f} inventory must be finite and >= 0, got {inv}")

        if isinstance(node, Depot):
            d_cap = node.dispatch_capacity_per_tick
            if not math.isfinite(d_cap) or d_cap < 0:
                issues.append(f"depot {node.id}: dispatch_capacity_per_tick must be finite and >= 0, got {d_cap}")
        elif isinstance(node, Station):
            d_mult = node.demand_multiplier
            if not math.isfinite(d_mult) or d_mult < 0:
                issues.append(f"station {node.id}: demand_multiplier must be finite and >= 0, got {d_mult}")

    for r in snap.routes:
        if not math.isfinite(r.max_shipment) or r.max_shipment <= 0:
            issues.append(f"route {r.id}: max_shipment must be finite and > 0, got {r.max_shipment}")
        if r.transit_ticks < 1:
            issues.append(f"route {r.id}: transit_ticks must be >= 1, got {r.transit_ticks}")

    for reg in snap.regions:
        if not math.isfinite(reg.demand_factor) or reg.demand_factor <= 0:
            issues.append(f"region {reg.id}: demand_factor must be finite and > 0, got {reg.demand_factor}")

    for s in snap.supply:
        if not math.isfinite(s.quantity) or s.quantity < 0:
            issues.append(f"supply {s.id}: quantity must be finite and >= 0, got {s.quantity}")

    for a in snap.allocations:
        if not math.isfinite(a.quantity) or a.quantity <= 0:
            issues.append(f"allocation {a.id}: quantity must be finite and > 0, got {a.quantity}")

    m = snap.metrics
    for field_name in ("served_demand_liters", "unmet_demand_liters", "allocation_liters"):
        val = getattr(m, field_name)
        if not math.isfinite(val) or val < 0:
            issues.append(f"metrics: {field_name} must be finite and >= 0, got {val}")
    if not math.isfinite(m.service_level) or not (0.0 <= m.service_level <= 1.0):
        issues.append(f"metrics: service_level must be between 0 and 1, got {m.service_level}")
    if m.allocation_failures < 0:
        issues.append(f"metrics: allocation_failures must be >= 0, got {m.allocation_failures}")

    if issues and increment_metric and FSIP_SIM_INVALID_RESPONSES is not None:
        FSIP_SIM_INVALID_RESPONSES.labels(resource="snapshot").inc()

    return issues


def issues_to_structured_errors(issues: list[str]) -> list[dict[str, Any]]:
    """Convert cross-check issue strings to structured error dictionaries."""
    return [{"loc": ("snapshot",), "msg": iss, "type": "cross_check_error"} for iss in issues]


def validate_snapshot(raw: Any) -> ValidatedSnapshot:
    """Validate and cross-check full simulator snapshot dictionary."""
    if not isinstance(raw, dict | ValidatedSnapshot):
        if FSIP_SIM_INVALID_RESPONSES is not None:
            FSIP_SIM_INVALID_RESPONSES.labels(resource="snapshot").inc()
        raise SimInvalidPayload(
            "snapshot",
            [{"loc": (), "msg": f"Expected dict for snapshot, got {type(raw).__name__}", "type": "type_error.dict"}],
            raw,
        )

    if isinstance(raw, ValidatedSnapshot):
        snap = raw
    else:
        try:
            snap = ValidatedSnapshot.model_validate(raw)
        except ValidationError as ve:
            if FSIP_SIM_INVALID_RESPONSES is not None:
                FSIP_SIM_INVALID_RESPONSES.labels(resource="snapshot").inc()
            raise SimInvalidPayload("snapshot", list(ve.errors()), raw) from ve
        except Exception as e:
            if FSIP_SIM_INVALID_RESPONSES is not None:
                FSIP_SIM_INVALID_RESPONSES.labels(resource="snapshot").inc()
            raise SimInvalidPayload("snapshot", [{"loc": (), "msg": str(e), "type": "value_error"}], raw) from e

    issues = cross_check(snap, increment_metric=True)
    if issues:
        errors = issues_to_structured_errors(issues)
        raise SimInvalidPayload("snapshot", errors, raw)

    return snap


# ---------------------------------------------------------------- validate_resource
def validate_resource(name: str, raw: Any) -> Any:
    """Validate raw payload for a simulator resource against its domain model.

    Raises SimInvalidPayload on validation failure and increments
    fsip_sim_invalid_responses_total{resource}.
    """
    clean_name, label = _clean_resource_name(name)

    if clean_name == "snapshot":
        return validate_snapshot(raw)

    if clean_name not in RESOURCE_REGISTRY:
        if FSIP_SIM_INVALID_RESPONSES is not None:
            FSIP_SIM_INVALID_RESPONSES.labels(resource=label).inc()
        raise SimInvalidPayload(
            resource=name,
            errors=[{"loc": (), "msg": f"Unknown simulator resource '{name}'", "type": "unknown_resource"}],
            raw=raw,
        )

    model_cls, is_list, _ = RESOURCE_REGISTRY[clean_name]

    if is_list:
        if not isinstance(raw, list):
            if FSIP_SIM_INVALID_RESPONSES is not None:
                FSIP_SIM_INVALID_RESPONSES.labels(resource=label).inc()
            err_msg = f"Expected list for resource '{name}', got {type(raw).__name__}"
            raise SimInvalidPayload(
                resource=name,
                errors=[{"loc": (), "msg": err_msg, "type": "type_error.list"}],
                raw=raw,
            )

        all_errors: list[dict[str, Any]] = []
        validated_items: list[Any] = []
        for idx, item in enumerate(raw):
            try:
                validated_items.append(model_cls.model_validate(item))
            except ValidationError as ve:
                for err in ve.errors():
                    err_dict = dict(err)
                    err_dict["loc"] = (idx, *err.get("loc", ()))
                    all_errors.append(err_dict)
            except Exception as e:
                all_errors.append({"loc": (idx,), "msg": str(e), "type": "value_error"})

        if all_errors:
            if FSIP_SIM_INVALID_RESPONSES is not None:
                FSIP_SIM_INVALID_RESPONSES.labels(resource=label).inc()
            raise SimInvalidPayload(resource=name, errors=all_errors, raw=raw)

        return validated_items

    # Single entity validation
    try:
        return model_cls.model_validate(raw)
    except ValidationError as ve:
        if FSIP_SIM_INVALID_RESPONSES is not None:
            FSIP_SIM_INVALID_RESPONSES.labels(resource=label).inc()
        raise SimInvalidPayload(resource=name, errors=list(ve.errors()), raw=raw) from ve
    except Exception as e:
        if FSIP_SIM_INVALID_RESPONSES is not None:
            FSIP_SIM_INVALID_RESPONSES.labels(resource=label).inc()
        raise SimInvalidPayload(
            resource=name,
            errors=[{"loc": (), "msg": str(e), "type": "value_error"}],
            raw=raw,
        ) from e
