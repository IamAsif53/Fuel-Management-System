"""Operational Detectors Module (Task 7.4).

Per PHASE_07_ANOMALY_DETECTION.md & 05_INTELLIGENCE_DESIGN.md §3:
- SupplyDelayDetector: status DELAYED or planned_tick < current_tick and not ARRIVED.
  Calculates depot cover lost in hours.
- DisruptionDetector: route status DISRUPTED, station OUTAGE, depot CONSTRAINED,
  plus pre-emptive alerts for SCHEDULED disruptions within horizon H=48.
- BottleneckDetector: depot dispatch utilization >= 90% for 3 consecutive ticks with CRITICAL risk.
- RegionalRiskDetector: >= 2 stations in same region with HIGH+ risk within 4 ticks -> REGIONAL_RISK.
- DataQualityDetector: simulator validation failures, NaN, negative inventories -> DATA_QUALITY,
  and marks degraded if >= 3 consecutive invalid cycles.
"""

from __future__ import annotations

import math
from typing import Any

from fsip_common.logging import get_logger
from prometheus_client import REGISTRY, Counter

from app.alerts.manager import AlertSpec

logger = get_logger()

# ---------------------------------------------------------------- Prometheus Metrics
try:
    FSIP_DISRUPTIONS_DETECTED_TOTAL = Counter(
        "fsip_disruptions_detected_total",
        "Total operational disruptions detected by detector",
        ["type"],
    )
except ValueError:
    FSIP_DISRUPTIONS_DETECTED_TOTAL = REGISTRY._names_to_collectors.get(  # type: ignore[assignment]
        "fsip_disruptions_detected_total"
    )


class SupplyDelayDetector:
    """Detects delayed supply arrivals and estimates lost depot cover hours."""

    def __init__(self) -> None:
        pass

    def detect(self, store: Any) -> list[AlertSpec]:
        alerts: list[AlertSpec] = []
        tick = getattr(store, "tick", 0)
        supply_arrivals = getattr(store, "supply_arrivals", []) or []

        for supply in supply_arrivals:
            s_id = getattr(supply, "id", None) or (supply.get("id") if isinstance(supply, dict) else "unknown")
            status = getattr(supply, "status", "") or (supply.get("status") if isinstance(supply, dict) else "")
            planned_tick = (
                getattr(supply, "planned_tick", 0) if hasattr(supply, "planned_tick") else supply.get("planned_tick", 0)
            )
            depot_id = getattr(supply, "depot_id", "") or (supply.get("depot_id") if isinstance(supply, dict) else "")
            fuel_type = getattr(supply, "fuel_type", "") or (
                supply.get("fuel_type") if isinstance(supply, dict) else ""
            )
            quantity = getattr(supply, "quantity", 0.0) if hasattr(supply, "quantity") else supply.get("quantity", 0.0)

            is_delayed = (status.upper() == "DELAYED") or (
                planned_tick < tick and status.upper() not in ("ARRIVED", "COMPLETED")
            )
            if not is_delayed:
                continue

            # Estimate depot cover lost
            # Nominal hourly depot outflow ~ 2000 L / h (4 ticks = 1h -> 500 L/tick)
            hourly_rate = 2000.0
            hours_lost = round(float(quantity) / hourly_rate, 1)

            if FSIP_DISRUPTIONS_DETECTED_TOTAL is not None:
                FSIP_DISRUPTIONS_DETECTED_TOTAL.labels(type="supply_delay").inc()

            alerts.append(
                AlertSpec(
                    fingerprint=f"supply:{s_id}:delay",
                    category="SUPPLY_DELAY",
                    severity="WARNING",
                    title=f"Supply Delay: Shipment #{s_id}",
                    message=(
                        f"Supply shipment #{s_id} ({quantity:,.0f} L {fuel_type}) to depot {depot_id} is DELAYED "
                        f"(planned tick {planned_tick}, current {tick}). "
                        f"Depot loses approximately {hours_lost:.1f} hours of supply cover."
                    ),
                    entity_type="depot",
                    entity_id=depot_id,
                    fuel_type=fuel_type,
                    opened_tick=tick,
                    data={
                        "supply_id": s_id,
                        "planned_tick": planned_tick,
                        "quantity": quantity,
                        "hours_lost": hours_lost,
                    },
                )
            )

        return alerts


class DisruptionDetector:
    """Detects active route/station/depot disruptions and pre-emptive scheduled disruptions."""

    def __init__(self, horizon: int = 48) -> None:
        self.horizon = horizon

    def detect(self, store: Any) -> list[AlertSpec]:
        alerts: list[AlertSpec] = []
        tick = getattr(store, "tick", 0)

        # 1. Active route disruptions
        routes = getattr(store, "routes", {}) or {}
        route_items = routes.values() if isinstance(routes, dict) else routes
        for r in route_items:
            r_id = getattr(r, "id", None) or (r.get("id") if isinstance(r, dict) else None)
            status = getattr(r, "status", "") or (r.get("status") if isinstance(r, dict) else "")
            src = getattr(r, "source_depot_id", "") or (r.get("source_depot_id") if isinstance(r, dict) else "")
            dst = getattr(r, "destination_station_id", "") or (
                r.get("destination_station_id") if isinstance(r, dict) else ""
            )

            if status.upper() == "DISRUPTED":
                if FSIP_DISRUPTIONS_DETECTED_TOTAL is not None:
                    FSIP_DISRUPTIONS_DETECTED_TOTAL.labels(type="route_disrupted").inc()
                alerts.append(
                    AlertSpec(
                        fingerprint=f"route:{r_id}:disrupted",
                        category="ROUTE_DISRUPTED",
                        severity="WARNING",
                        title=f"Route Disrupted: {r_id}",
                        message=f"Route {r_id} ({src} -> {dst}) is currently DISRUPTED and unavailable for shipments.",
                        entity_type="route",
                        entity_id=r_id,
                        opened_tick=tick,
                        data={"route_id": r_id, "source": src, "destination": dst},
                    )
                )

        # 2. Active station outages
        stations = getattr(store, "stations", {}) or {}
        st_items = stations.values() if isinstance(stations, dict) else stations
        for st in st_items:
            st_id = getattr(st, "id", None) or (st.get("id") if isinstance(st, dict) else None)
            status = getattr(st, "status", "") or (st.get("status") if isinstance(st, dict) else "")
            if status.upper() == "OUTAGE":
                if FSIP_DISRUPTIONS_DETECTED_TOTAL is not None:
                    FSIP_DISRUPTIONS_DETECTED_TOTAL.labels(type="station_outage").inc()
                alerts.append(
                    AlertSpec(
                        fingerprint=f"station:{st_id}:outage",
                        category="STATION_OUTAGE",
                        severity="CRITICAL",
                        title=f"Station Outage: {st_id}",
                        message=f"Station {st_id} is in OUTAGE. Pumps stopped; no deliveries can be accepted.",
                        entity_type="station",
                        entity_id=st_id,
                        opened_tick=tick,
                        data={"station_id": st_id},
                    )
                )

        # 3. Active depot constraints
        depots = getattr(store, "depots", {}) or {}
        d_items = depots.values() if isinstance(depots, dict) else depots
        for d in d_items:
            d_id = getattr(d, "id", None) or (d.get("id") if isinstance(d, dict) else None)
            status = getattr(d, "status", "") or (d.get("status") if isinstance(d, dict) else "")
            if status.upper() == "CONSTRAINED":
                alerts.append(
                    AlertSpec(
                        fingerprint=f"depot:{d_id}:constrained",
                        category="DEPOT_CONSTRAINED",
                        severity="WARNING",
                        title=f"Depot Constrained: {d_id}",
                        message=f"Depot {d_id} throughput is CONSTRAINED (operating at reduced 50% dispatch rate).",
                        entity_type="depot",
                        entity_id=d_id,
                        opened_tick=tick,
                        data={"depot_id": d_id},
                    )
                )

        # 4. Pre-emptive alerts for scheduled disruptions within planning horizon H
        events = getattr(store, "events", []) or []
        for ev in events:
            ev_type = getattr(ev, "type", None) or (ev.get("type") if isinstance(ev, dict) else None)
            if ev_type != "route_disruption":
                continue

            start_tick = getattr(ev, "start_tick", 0) if hasattr(ev, "start_tick") else ev.get("start_tick", 0)
            end_tick = getattr(ev, "end_tick", 0) if hasattr(ev, "end_tick") else ev.get("end_tick", 0)
            duration = max(1, end_tick - start_tick)

            # Scheduled in future within horizon
            if tick < start_tick <= tick + self.horizon:
                params = getattr(ev, "parameters", {}) if hasattr(ev, "parameters") else ev.get("parameters", {})
                if not isinstance(params, dict):
                    params = {}
                route_filter = params.get("route_ids") or params.get("route_id")

                affected_routes: list[str] = []
                if route_filter:
                    if isinstance(route_filter, list):
                        affected_routes.extend(route_filter)
                    elif isinstance(route_filter, str):
                        affected_routes.append(route_filter)
                else:
                    # None means all routes
                    affected_routes.append("all-routes")

                for r_target in affected_routes:
                    alerts.append(
                        AlertSpec(
                            fingerprint=f"route:{r_target}:scheduled_disruption",
                            category="ROUTE_DISRUPTED",
                            severity="WARNING",
                            title=f"Pre-emptive Disruption Warning: {r_target}",
                            message=(
                                f"Route {r_target} will be DISRUPTED from tick {start_tick} "
                                f"for {duration} ticks. Pre-stocking recommended before closure."
                            ),
                            entity_type="route",
                            entity_id=r_target,
                            opened_tick=tick,
                            data={
                                "start_tick": start_tick,
                                "end_tick": end_tick,
                                "duration": duration,
                            },
                        )
                    )

        return alerts


class BottleneckDetector:
    """Detects chronic dispatch bottlenecks at depots (>= 90% utilization for 3 ticks with critical risk)."""

    def __init__(self, threshold: float = 0.90, required_ticks: int = 3) -> None:
        self.threshold = threshold
        self.required_ticks = required_ticks
        self.consecutive_ticks: dict[str, int] = {}

    def reset(self) -> None:
        self.consecutive_ticks.clear()

    def detect(self, store: Any) -> list[AlertSpec]:
        alerts: list[AlertSpec] = []
        tick = getattr(store, "tick", 0)
        depots = getattr(store, "depots", {}) or {}
        depot_items = depots.values() if isinstance(depots, dict) else depots

        # Check whether any stations have CRITICAL or HIGH risk
        risk_results = getattr(store, "risk_results", {}) or {}
        has_critical_station = any(
            getattr(r, "risk_level", "LOW") in ("CRITICAL", "HIGH") for r in risk_results.values()
        )

        # Calculate dispatch utilization per depot
        # Sum of allocations departing from depot at current tick
        allocations = getattr(store, "allocations", []) or []
        departing_vol: dict[str, float] = {}
        for alloc in allocations:
            c_tick = (
                getattr(alloc, "created_tick", 0) if hasattr(alloc, "created_tick") else alloc.get("created_tick", 0)
            )
            if c_tick == tick:
                d_id = getattr(alloc, "source_depot_id", "") or (
                    alloc.get("source_depot_id") if isinstance(alloc, dict) else ""
                )
                q = getattr(alloc, "quantity", 0.0) if hasattr(alloc, "quantity") else alloc.get("quantity", 0.0)
                departing_vol[d_id] = departing_vol.get(d_id, 0.0) + float(q)

        for d in depot_items:
            d_id = getattr(d, "id", None) or (d.get("id") if isinstance(d, dict) else None)
            if not d_id:
                continue
            cap = getattr(d, "dispatch_capacity_per_tick", 10000.0) or 10000.0
            used = departing_vol.get(d_id, 0.0)
            utilization = used / max(1.0, float(cap))

            if utilization >= self.threshold:
                self.consecutive_ticks[d_id] = self.consecutive_ticks.get(d_id, 0) + 1
            else:
                self.consecutive_ticks[d_id] = 0

            count = self.consecutive_ticks.get(d_id, 0)
            if count >= self.required_ticks and has_critical_station:
                alerts.append(
                    AlertSpec(
                        fingerprint=f"depot:{d_id}:dispatch_bottleneck",
                        category="BOTTLENECK",
                        severity="WARNING",
                        title=f"Depot Bottleneck at {d_id}",
                        message=(
                            f"Depot {d_id} dispatch capacity utilized {utilization*100:.0f}% "
                            f"(>= {self.threshold*100:.0f}%) for {count} consecutive ticks "
                            "while critical station demand remains unserved."
                        ),
                        entity_type="depot",
                        entity_id=d_id,
                        opened_tick=tick,
                        data={
                            "utilization": round(utilization, 2),
                            "consecutive_ticks": count,
                        },
                    )
                )

        return alerts


class RegionalRiskDetector:
    """Detects regional risk clustering (>= 2 stations in the same region with HIGH+ risk)."""

    def __init__(self) -> None:
        pass

    def detect(self, store: Any) -> list[AlertSpec]:
        alerts: list[AlertSpec] = []
        tick = getattr(store, "tick", 0)
        risk_results = getattr(store, "risk_results", {}) or {}
        stations = getattr(store, "stations", {}) or {}

        # Map station_id to region_id
        st_region: dict[str, str] = {}
        st_items = stations.values() if isinstance(stations, dict) else stations
        for st in st_items:
            st_id = getattr(st, "id", None) or (st.get("id") if isinstance(st, dict) else None)
            reg = getattr(st, "region_id", None) or (st.get("region_id") if isinstance(st, dict) else None)
            if st_id and reg:
                st_region[st_id] = reg

        # Group high-risk stations by region
        high_risk_by_region: dict[str, set[str]] = {}
        for (st_id, _fuel), r_res in risk_results.items():
            level = getattr(r_res, "risk_level", "LOW")
            if level in ("CRITICAL", "HIGH"):
                reg = st_region.get(st_id, "unknown")
                high_risk_by_region.setdefault(reg, set()).add(st_id)

        for reg_id, st_set in high_risk_by_region.items():
            if len(st_set) >= 2:
                station_list = ", ".join(sorted(st_set))
                alerts.append(
                    AlertSpec(
                        fingerprint=f"region:{reg_id}:regional_risk",
                        category="REGIONAL_RISK",
                        severity="WARNING",
                        title=f"Regional Stockout Cluster in {reg_id}",
                        message=(
                            f"{len(st_set)} stations in {reg_id} ({station_list}) "
                            f"are simultaneously facing HIGH or CRITICAL stockout risks."
                        ),
                        entity_type="region",
                        entity_id=reg_id,
                        opened_tick=tick,
                        data={
                            "region_id": reg_id,
                            "affected_stations": list(st_set),
                            "station_count": len(st_set),
                        },
                    )
                )

        return alerts


class DataQualityDetector:
    """Validates simulator data consistency (negative inventories, NaN, tick regression)."""

    def __init__(self) -> None:
        self.consecutive_invalid: int = 0
        self.last_tick: int | None = None

    def reset(self) -> None:
        self.consecutive_invalid = 0
        self.last_tick = None

    def detect(self, store: Any) -> tuple[list[AlertSpec], bool]:
        """Detect data quality issues.

        Returns (alerts, is_system_degraded).
        """
        alerts: list[AlertSpec] = []
        tick = getattr(store, "tick", 0)
        is_invalid = False
        reasons: list[str] = []

        # Check tick backwards regression without reset
        if self.last_tick is not None and tick < self.last_tick:
            is_invalid = True
            reasons.append(f"tick went backwards from {self.last_tick} to {tick}")
        self.last_tick = tick

        # Check stations inventory sanity (no negative or NaN)
        stations = getattr(store, "stations", {}) or {}
        st_items = stations.values() if isinstance(stations, dict) else stations
        for st in st_items:
            st_id = getattr(st, "id", None) or (st.get("id") if isinstance(st, dict) else "unknown")
            inv = getattr(st, "inventory", {}) if hasattr(st, "inventory") else st.get("inventory", {})
            if isinstance(inv, dict):
                for f_type, val in inv.items():
                    if val is None or math.isnan(val) or val < 0:
                        is_invalid = True
                        reasons.append(f"station {st_id} {f_type} invalid inventory {val}")

        if is_invalid:
            self.consecutive_invalid += 1
            alerts.append(
                AlertSpec(
                    fingerprint="data_quality:simulator_validation",
                    category="DATA_QUALITY",
                    severity="WARNING",
                    title="Simulator Data Quality Issue Detected",
                    message="; ".join(reasons),
                    opened_tick=tick,
                    data={"reasons": reasons, "consecutive_invalid": self.consecutive_invalid},
                )
            )
        else:
            self.consecutive_invalid = 0

        is_degraded = self.consecutive_invalid >= 3
        return alerts, is_degraded
