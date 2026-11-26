"""Alert and Incident Lifecycle Management Module for FSIP Engine.

Implements Tasks 6.4 & 6.5 per B18:
- AlertManager: raise_, clear_check (2 clean cycles to resolve), ack
- Deterministic entity/category/condition fingerprints
- In-place deduplication: severity can escalate (INFO -> WARNING -> CRITICAL), never silent downgrade
- Acknowledge != Resolution
- Domain events published to Redis Stream fsip:domain-events
- Centrally registered Prometheus metrics:
  fsip_alerts_open{category, severity}
  fsip_shortage_alerts_total{severity}
- IncidentManager: component_down, mitigated, recovered with JSONB timeline
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import orjson
import redis.asyncio as aioredis
from fsip_common.logging import get_logger
from prometheus_client import REGISTRY, Counter, Gauge
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Alert, Incident
from app.db.repo import (
    append_incident_event,
    open_incident,
    resolve_alert,
    resolve_incident,
    upsert_alert,
)

logger = get_logger()

# ---------------------------------------------------------------- Prometheus Metrics
try:
    FSIP_ALERTS_OPEN = Gauge(
        "fsip_alerts_open",
        "Number of currently open or acknowledged alerts",
        ["category", "severity"],
    )
except ValueError:
    FSIP_ALERTS_OPEN = REGISTRY._names_to_collectors.get("fsip_alerts_open")  # type: ignore[assignment]

try:
    FSIP_SHORTAGE_ALERTS_TOTAL = Counter(
        "fsip_shortage_alerts_total",
        "Total shortage alerts raised",
        ["severity"],
    )
except ValueError:
    FSIP_SHORTAGE_ALERTS_TOTAL = REGISTRY._names_to_collectors.get("fsip_shortage_alerts_total")  # type: ignore[assignment]


SEVERITY_ORDER = {"INFO": 1, "WARNING": 2, "CRITICAL": 3}


@dataclass
class AlertSpec:
    """Specification for raising or updating an alert."""

    fingerprint: str
    category: str
    severity: str  # INFO | WARNING | CRITICAL
    title: str
    message: str | None = None
    entity_type: str | None = None
    entity_id: str | None = None
    fuel_type: str | None = None
    opened_tick: int | None = None
    sim_run_id: int | None = None
    related_decision_id: int | None = None
    data: dict[str, Any] = field(default_factory=dict)


class AlertManager:
    """Manages the full lifecycle of system and operational alerts."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        redis_client: aioredis.Redis | None = None,
        stream_name: str = "fsip:domain-events",
        stream_maxlen: int = 20000,
    ) -> None:
        self.session_factory = session_factory
        self.redis_client = redis_client
        self.stream_name = stream_name
        self.stream_maxlen = stream_maxlen

        # In-memory tracking for fast deduplication and 2-clean-cycle auto-resolution
        # fingerprint -> Alert
        self.active_alerts: dict[str, Alert] = {}
        # fingerprint -> consecutive clean cycles absent
        self.clean_cycles: dict[str, int] = {}

    async def _publish_event(self, event_type: str, alert: Alert, tick: int | None) -> None:
        """Publish alert lifecycle event to Redis Stream fsip:domain-events."""
        if self.redis_client is None:
            return

        try:
            now_iso = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            alert_payload = {
                "id": alert.id,
                "fingerprint": alert.fingerprint,
                "category": alert.category,
                "severity": alert.severity,
                "status": alert.status,
                "title": alert.title,
                "message": alert.message,
                "entity_type": alert.entity_type,
                "entity_id": alert.entity_id,
                "fuel_type": alert.fuel_type,
                "opened_tick": alert.opened_tick,
                "resolved_tick": alert.resolved_tick,
                "opened_at": alert.opened_at.isoformat() if alert.opened_at else None,
                "acknowledged_by": alert.acknowledged_by,
                "related_decision_id": alert.related_decision_id,
                "data": alert.data or {},
            }
            entry = {
                "type": event_type,
                "at": now_iso,
                "sim_run_id": str(alert.sim_run_id or 0),
                "tick": str(tick if tick is not None else alert.opened_tick or 0),
                "data": orjson.dumps(alert_payload).decode("utf-8"),
            }
            await self.redis_client.xadd(
                self.stream_name,
                entry,
                maxlen=self.stream_maxlen,
                approximate=True,
            )
        except Exception as exc:
            logger.warning("alert.publish_failed", event_type=event_type, error=str(exc))

    def _sync_metrics(self) -> None:
        """Synchronize open alert counts with Prometheus Gauges."""
        if FSIP_ALERTS_OPEN is None:
            return

        counts: dict[tuple[str, str], int] = {}
        for alert in self.active_alerts.values():
            if alert.status in ("OPEN", "ACKNOWLEDGED"):
                k = (alert.category, alert.severity)
                counts[k] = counts.get(k, 0) + 1

        # Clear and update gauges
        FSIP_ALERTS_OPEN.clear()
        for (cat, sev), count in counts.items():
            FSIP_ALERTS_OPEN.labels(category=cat, severity=sev).set(count)

    def reset(self) -> None:
        """Clear in-memory active alerts and cooldown counters on simulation reset."""
        self.active_alerts.clear()
        self.clean_cycles.clear()
        self._sync_metrics()

    async def resolve_stale_runs(self, active_sim_run_id: int, resolved_tick: int | None = None) -> int:
        """Resolve stale database alerts from prior simulation runs."""
        if self.session_factory is None:
            return 0
        try:
            async with self.session_factory() as session:
                async with session.begin():
                    from app.db.repo import resolve_stale_run_alerts

                    return await resolve_stale_run_alerts(
                        session,
                        active_sim_run_id=active_sim_run_id,
                        resolved_tick=resolved_tick,
                    )
        except Exception as exc:
            logger.warning("alert.resolve_stale_runs_failed", error=str(exc))
            return 0

    def get_open_counts(self) -> dict[str, int]:
        """Return count of currently OPEN alerts by severity."""
        counts = {"INFO": 0, "WARNING": 0, "CRITICAL": 0}
        for alert in self.active_alerts.values():
            if alert.status == "OPEN":
                sev = str(alert.severity or "INFO").upper()
                if sev in counts:
                    counts[sev] += 1
        return counts

    def get_open_alerts_summary(self) -> list[dict[str, Any]]:
        """Return lightweight serializable summaries of currently OPEN alerts."""
        items: list[dict[str, Any]] = []
        for alert in self.active_alerts.values():
            if alert.status == "OPEN":
                items.append(
                    {
                        "id": alert.id,
                        "sim_run_id": alert.sim_run_id,
                        "fingerprint": alert.fingerprint,
                        "category": alert.category,
                        "severity": alert.severity,
                        "title": alert.title,
                        "message": alert.message,
                        "entity_type": alert.entity_type,
                        "entity_id": alert.entity_id,
                        "fuel_type": alert.fuel_type,
                        "status": alert.status,
                        "opened_tick": alert.opened_tick,
                        "opened_at": alert.opened_at.isoformat() if alert.opened_at else None,
                    }
                )
        items.sort(
            key=lambda a: (
                0 if a["severity"] == "CRITICAL" else (1 if a["severity"] == "WARNING" else 2),
                -(a["id"] or 0),
            )
        )
        return items

    async def raise_(self, spec: AlertSpec) -> Alert:
        """Raise or update an alert.

        Deduplication:
        - If an active alert with the same fingerprint exists:
          - Updates message, data, title in place
          - Severity can escalate (e.g. WARNING -> CRITICAL), never downgrade
          - Status remains ACKNOWLEDGED unless severity escalates to CRITICAL
        - Otherwise creates a new Alert in OPEN status.
        - Emits alert.raised or alert.updated event to Redis Stream.
        """
        fp = spec.fingerprint
        self.clean_cycles[fp] = 0  # Condition is active

        existing = self.active_alerts.get(fp)
        if existing is not None and spec.sim_run_id is not None and existing.sim_run_id != spec.sim_run_id:
            existing = None
        event_type = "alert.raised"

        if existing is not None and existing.status in ("OPEN", "ACKNOWLEDGED"):
            # Existing active alert: update in-place
            old_sev = existing.severity
            new_sev = spec.severity
            if SEVERITY_ORDER.get(new_sev, 1) > SEVERITY_ORDER.get(old_sev, 1):
                existing.severity = new_sev
                # Escalate status back to OPEN if acknowledged alert worsened to CRITICAL
                if new_sev == "CRITICAL" and existing.status == "ACKNOWLEDGED":
                    existing.status = "OPEN"

            existing.title = spec.title
            existing.message = spec.message
            existing.data = {**(existing.data or {}), **spec.data}
            if spec.related_decision_id is not None:
                existing.related_decision_id = spec.related_decision_id

            target_alert = existing
            event_type = "alert.updated"
        else:
            # New alert
            target_alert = Alert(
                sim_run_id=spec.sim_run_id,
                fingerprint=spec.fingerprint,
                category=spec.category,
                severity=spec.severity,
                title=spec.title,
                message=spec.message,
                entity_type=spec.entity_type,
                entity_id=spec.entity_id,
                fuel_type=spec.fuel_type,
                status="OPEN",
                opened_tick=spec.opened_tick,
                opened_at=datetime.now(UTC),
                related_decision_id=spec.related_decision_id,
                data=spec.data,
            )
            self.active_alerts[fp] = target_alert

            if spec.category in ("STOCKOUT_RISK", "STOCKOUT_OCCURRED"):
                if FSIP_SHORTAGE_ALERTS_TOTAL is not None:
                    FSIP_SHORTAGE_ALERTS_TOTAL.labels(severity=spec.severity).inc()

        # Database upsert if session factory is available
        if self.session_factory is not None:
            try:
                async with self.session_factory() as session:
                    async with session.begin():
                        persisted = await upsert_alert(session, target_alert)
                        target_alert.id = persisted.id
            except Exception as exc:
                logger.error("alert.db_upsert_failed", fingerprint=fp, error=str(exc))

        self._sync_metrics()
        await self._publish_event(event_type, target_alert, spec.opened_tick)
        return target_alert

    async def clear_check(self, active_fingerprints: set[str], current_tick: int | None = None) -> list[Alert]:
        """Check all active alerts and resolve those whose condition has cleared for 2 consecutive cycles."""
        resolved: list[Alert] = []

        for fp, alert in list(self.active_alerts.items()):
            if alert.status in ("OPEN", "ACKNOWLEDGED"):
                if fp not in active_fingerprints:
                    self.clean_cycles[fp] = self.clean_cycles.get(fp, 0) + 1
                    if self.clean_cycles[fp] >= 2:
                        # 2 clean cycles: resolve alert
                        alert.status = "RESOLVED"
                        alert.resolved_tick = current_tick
                        alert.resolved_at = datetime.now(UTC)
                        resolved.append(alert)

                        if self.session_factory is not None and alert.id is not None:
                            try:
                                async with self.session_factory() as session:
                                    async with session.begin():
                                        await resolve_alert(
                                            session,
                                            alert_id=alert.id,
                                            resolved_tick=current_tick,
                                            resolved_at=alert.resolved_at,
                                        )
                            except Exception as exc:
                                logger.error("alert.db_resolve_failed", alert_id=alert.id, error=str(exc))

                        await self._publish_event("alert.resolved", alert, current_tick)
                        self.active_alerts.pop(fp, None)
                        self.clean_cycles.pop(fp, None)
                else:
                    self.clean_cycles[fp] = 0

        if resolved:
            self._sync_metrics()

        return resolved

    async def ack(self, alert_id: int, actor: str) -> Alert | None:
        """Acknowledge an active alert without resolving it."""
        target: Alert | None = None
        for alert in self.active_alerts.values():
            if alert.id == alert_id:
                target = alert
                break

        if target is None and self.session_factory is not None:
            # Query from DB if not in memory
            try:
                async with self.session_factory() as session:
                    res = await session.execute(select(Alert).where(Alert.id == alert_id))
                    target = res.scalar_one_or_none()
            except Exception as exc:
                logger.error("alert.db_query_failed", alert_id=alert_id, error=str(exc))

        if target is None:
            return None

        target.status = "ACKNOWLEDGED"
        target.acknowledged_by = actor

        if self.session_factory is not None and target.id is not None:
            try:
                async with self.session_factory() as session:
                    async with session.begin():
                        await upsert_alert(session, target)
            except Exception as exc:
                logger.error("alert.db_ack_failed", alert_id=alert_id, error=str(exc))

        self._sync_metrics()
        await self._publish_event("alert.acknowledged", target, target.opened_tick)
        return target


class IncidentManager:
    """Manages infrastructure and platform operational incidents."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        redis_client: aioredis.Redis | None = None,
        stream_name: str = "fsip:domain-events",
        stream_maxlen: int = 20000,
    ) -> None:
        self.session_factory = session_factory
        self.redis_client = redis_client
        self.stream_name = stream_name
        self.stream_maxlen = stream_maxlen
        self.active_incidents: dict[str, Incident] = {}

    async def _publish_incident_event(self, event_type: str, incident: Incident) -> None:
        if self.redis_client is None:
            return
        try:
            now_iso = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            payload = {
                "id": incident.id,
                "kind": incident.kind,
                "component": incident.component,
                "status": incident.status,
                "started_at": incident.started_at.isoformat() if incident.started_at else None,
                "mitigated_at": incident.mitigated_at.isoformat() if incident.mitigated_at else None,
                "resolved_at": incident.resolved_at.isoformat() if incident.resolved_at else None,
                "summary": incident.summary,
                "timeline": incident.timeline or [],
            }
            entry = {
                "type": event_type,
                "at": now_iso,
                "data": orjson.dumps(payload).decode("utf-8"),
            }
            await self.redis_client.xadd(self.stream_name, entry, maxlen=self.stream_maxlen, approximate=True)
        except Exception as exc:
            logger.warning("incident.publish_failed", event_type=event_type, error=str(exc))

    async def component_down(self, component: str, detail: str, kind: str = "DEPENDENCY_DOWN") -> Incident:
        """Record a component failure / dependency down event."""
        now_dt = datetime.now(UTC)
        now_iso = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        timeline_entry = {"at": now_iso, "event": f"detected: {detail}"}

        existing = self.active_incidents.get(component)
        if existing and existing.status in ("OPEN", "MITIGATED"):
            existing.summary = detail
            if existing.timeline is None:
                existing.timeline = []
            existing.timeline.append(timeline_entry)
            target = existing
            event_type = "incident.updated"
        else:
            target = Incident(
                kind=kind,
                component=component,
                status="OPEN",
                started_at=now_dt,
                summary=detail,
                timeline=[timeline_entry],
            )
            self.active_incidents[component] = target
            event_type = "incident.created"

        if self.session_factory is not None:
            try:
                async with self.session_factory() as session:
                    async with session.begin():
                        if target.id is None:
                            inc = await open_incident(
                                session,
                                kind=target.kind,
                                component=target.component,
                                summary=target.summary,
                                timeline_event=timeline_entry,
                            )
                            target.id = inc.id
                        else:
                            await append_incident_event(session, target.id, timeline_entry)
            except Exception as exc:
                logger.error("incident.db_open_failed", component=component, error=str(exc))

        await self._publish_incident_event(event_type, target)
        return target

    async def mitigated(self, component: str, how: str) -> Incident | None:
        """Record mitigation of an active component incident."""
        target = self.active_incidents.get(component)
        if target is None:
            return None

        now_dt = datetime.now(UTC)
        now_iso = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        timeline_entry = {"at": now_iso, "event": f"mitigated: {how}"}

        target.status = "MITIGATED"
        target.mitigated_at = now_dt
        if target.timeline is None:
            target.timeline = []
        target.timeline.append(timeline_entry)

        if self.session_factory is not None and target.id is not None:
            try:
                async with self.session_factory() as session:
                    async with session.begin():
                        await append_incident_event(session, target.id, timeline_entry)
            except Exception as exc:
                logger.error("incident.db_mitigate_failed", component=component, error=str(exc))

        await self._publish_incident_event("incident.mitigated", target)
        return target

    async def recovered(self, component: str, detail: str = "service healthy") -> Incident | None:
        """Mark an incident as fully resolved."""
        target = self.active_incidents.pop(component, None)
        if target is None:
            return None

        now_dt = datetime.now(UTC)
        now_iso = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        timeline_entry = {"at": now_iso, "event": f"recovered: {detail}"}

        target.status = "RESOLVED"
        target.resolved_at = now_dt
        if target.timeline is None:
            target.timeline = []
        target.timeline.append(timeline_entry)

        if self.session_factory is not None and target.id is not None:
            try:
                async with self.session_factory() as session:
                    async with session.begin():
                        await resolve_incident(session, target.id, resolved_at=now_dt)
            except Exception as exc:
                logger.error("incident.db_resolve_failed", component=component, error=str(exc))

        await self._publish_incident_event("incident.resolved", target)
        return target


# -----------------------------------------------------------------------------
# Risk-Originated Alerts Generator (Task 6.5)
# -----------------------------------------------------------------------------


def generate_risk_alert_specs(
    store: Any,
    station_risks: dict[tuple[str, str], Any],
    depot_projections: dict[tuple[str, str], Any],
) -> tuple[list[AlertSpec], set[str]]:
    """Generate AlertSpecs from current WorldState, station risk assessments, and depot projections.

    Produces locked categories per 05 §2.4 & Task 6.5:
    - STOCKOUT_RISK (HIGH -> WARNING, CRITICAL -> CRITICAL)
    - STOCKOUT_OCCURRED (new row unmet_liters > 0)
    - DEPOT_DEPLETION_RISK (projected depletion <= 12h)
    - DEPOT_OVERFLOW_RISK (predicted clipping/waste > 0)
    - ROUTE_DISRUPTED (route status DISRUPTED or active disruption)
    - STATION_OUTAGE (station status OUTAGE)
    - SUPPLY_DELAY (supply arrival status DELAYED)
    - ALLOCATION_FAILED (allocation status FAILED)

    Returns (specs_to_raise, active_fingerprints)
    """
    specs: list[AlertSpec] = []
    active_fingerprints: set[str] = set()

    current_tick = store.tick if hasattr(store, "tick") else 0
    sim_run_id = store.sim_run_id if hasattr(store, "sim_run_id") else None

    # 1. Station stockout risks and outages
    for (st_id, f_type), r_res in station_risks.items():
        if r_res.risk_level in ("HIGH", "CRITICAL"):
            fp = f"stockout_risk:{st_id}:{f_type}"
            active_fingerprints.add(fp)
            severity = "CRITICAL" if r_res.risk_level == "CRITICAL" else "WARNING"
            hours_str = f"{r_res.hours_to_stockout}h" if r_res.hours_to_stockout is not None else "horizon"

            title = (
                f"{st_id} {f_type} critical stockout risk in {hours_str}"
                if severity == "CRITICAL"
                else f"{st_id} {f_type} high stockout risk ({hours_str})"
            )
            prob_summary = (
                f"Stockout probability lead={round(r_res.stockout_prob_lead*100)}%, "
                f"horizon={round(r_res.stockout_prob_horizon*100)}%. Drivers: {', '.join(r_res.drivers)}"
            )
            specs.append(
                AlertSpec(
                    fingerprint=fp,
                    category="STOCKOUT_RISK",
                    severity=severity,
                    title=title,
                    message=prob_summary,
                    entity_type="station",
                    entity_id=st_id,
                    fuel_type=f_type,
                    opened_tick=current_tick,
                    sim_run_id=sim_run_id,
                    data={
                        "risk_level": r_res.risk_level,
                        "hours_to_stockout": r_res.hours_to_stockout,
                        "drivers": r_res.drivers,
                    },
                )
            )

    # 2. Station outages
    stations = store.stations if hasattr(store, "stations") else []
    for st in stations:
        st_id = st.id if hasattr(st, "id") else st["id"]
        st_status = st.status if hasattr(st, "status") else st.get("status")
        if st_status == "OUTAGE":
            fp = f"station_outage:{st_id}"
            active_fingerprints.add(fp)
            specs.append(
                AlertSpec(
                    fingerprint=fp,
                    category="STATION_OUTAGE",
                    severity="CRITICAL",
                    title=f"Station {st_id} is in OUTAGE",
                    message=f"Station {st_id} status is OUTAGE; demand served is zero.",
                    entity_type="station",
                    entity_id=st_id,
                    opened_tick=current_tick,
                    sim_run_id=sim_run_id,
                )
            )

    # 3. Stockout occurred
    metrics = getattr(store, "metrics", None)
    if metrics is not None and getattr(metrics, "unmet_demand_liters", 0.0) > 0.0:
        fp = "stockout_occurred:network"
        active_fingerprints.add(fp)
        specs.append(
            AlertSpec(
                fingerprint=fp,
                category="STOCKOUT_OCCURRED",
                severity="CRITICAL",
                title=f"Stockout occurred: {round(float(metrics.unmet_demand_liters))} L unmet demand",
                message="Fuel exhaustion occurred resulting in unmet customer demand.",
                entity_type="network",
                opened_tick=current_tick,
                sim_run_id=sim_run_id,
                data={"unmet_liters": float(metrics.unmet_demand_liters)},
            )
        )

    # 4. Depot depletion and overflow risks
    for (d_id, f_type), d_proj in depot_projections.items():
        # Depletion risk
        if d_proj.hours_to_depletion is not None and d_proj.hours_to_depletion <= 12.0:
            fp = f"depot_depletion:{d_id}:{f_type}"
            active_fingerprints.add(fp)
            severity = "CRITICAL" if d_proj.hours_to_depletion <= 6.0 else "WARNING"
            depletion_msg = (
                f"Current inventory {round(d_proj.current_inventory)} L. "
                f"Days of supply: {d_proj.projected_days_of_supply}d."
            )
            specs.append(
                AlertSpec(
                    fingerprint=fp,
                    category="DEPOT_DEPLETION_RISK",
                    severity=severity,
                    title=f"Depot {d_id} {f_type} projected depletion in {d_proj.hours_to_depletion}h",
                    message=depletion_msg,
                    entity_type="depot",
                    entity_id=d_id,
                    fuel_type=f_type,
                    opened_tick=current_tick,
                    sim_run_id=sim_run_id,
                    data={
                        "hours_to_depletion": d_proj.hours_to_depletion,
                        "projected_days_of_supply": d_proj.projected_days_of_supply,
                    },
                )
            )

        # Overflow risk (predicted waste)
        if d_proj.total_predicted_waste_liters > 0:
            fp = f"depot_overflow:{d_id}:{f_type}"
            active_fingerprints.add(fp)
            overflow_title = (
                f"Depot {d_id} {f_type} overflow waste risk ({round(d_proj.total_predicted_waste_liters)} L)"
            )
            overflow_msg = (
                "Upcoming supply arrivals exceed depot storage headroom. "
                "Recommend pushing stock downstream early."
            )
            specs.append(
                AlertSpec(
                    fingerprint=fp,
                    category="DEPOT_OVERFLOW_RISK",
                    severity="WARNING",
                    title=overflow_title,
                    message=overflow_msg,
                    entity_type="depot",
                    entity_id=d_id,
                    fuel_type=f_type,
                    opened_tick=current_tick,
                    sim_run_id=sim_run_id,
                    data={
                        "waste_liters": d_proj.total_predicted_waste_liters,
                        "hours_to_overflow": d_proj.hours_to_overflow,
                    },
                )
            )

    # 5. Route disruptions
    routes = store.routes if hasattr(store, "routes") else []
    for r in routes:
        r_id = r.id if hasattr(r, "id") else r["id"]
        r_status = r.status if hasattr(r, "status") else r.get("status")
        if r_status == "DISRUPTED":
            fp = f"route_disrupted:{r_id}"
            active_fingerprints.add(fp)
            specs.append(
                AlertSpec(
                    fingerprint=fp,
                    category="ROUTE_DISRUPTED",
                    severity="WARNING",
                    title=f"Route {r_id} is DISRUPTED",
                    message=f"Transit along {r_id} is unavailable due to disruption.",
                    entity_type="route",
                    entity_id=r_id,
                    opened_tick=current_tick,
                    sim_run_id=sim_run_id,
                )
            )

    # 6. Supply delays
    supply_list = store.supply if hasattr(store, "supply") else []
    for s in supply_list:
        s_id = s.id if hasattr(s, "id") else s.get("id")
        s_status = s.status if hasattr(s, "status") else s.get("status")
        if s_status == "DELAYED":
            fp = f"supply_delayed:{s_id}"
            active_fingerprints.add(fp)
            depot_id = s.depot_id if hasattr(s, "depot_id") else s.get("depot_id")
            fuel = s.fuel_type if hasattr(s, "fuel_type") else s.get("fuel_type")
            qty = s.quantity if hasattr(s, "quantity") else s.get("quantity")
            specs.append(
                AlertSpec(
                    fingerprint=fp,
                    category="SUPPLY_DELAY",
                    severity="WARNING",
                    title=f"Supply shipment {s_id} is DELAYED ({qty} L {fuel})",
                    message=f"Planned delivery to depot {depot_id} is delayed.",
                    entity_type="supply",
                    entity_id=s_id,
                    fuel_type=fuel,
                    opened_tick=current_tick,
                    sim_run_id=sim_run_id,
                )
            )

    # 7. Allocation failures
    allocations = store.allocations if hasattr(store, "allocations") else []
    for a in allocations:
        a_id = a.id if hasattr(a, "id") else a.get("id")
        a_status = a.status if hasattr(a, "status") else a.get("status")
        if a_status == "FAILED":
            fp = f"allocation_failed:{a_id}"
            active_fingerprints.add(fp)
            dest = a.destination_station_id if hasattr(a, "destination_station_id") else a.get("destination_station_id")
            fuel = a.fuel_type if hasattr(a, "fuel_type") else a.get("fuel_type")
            specs.append(
                AlertSpec(
                    fingerprint=fp,
                    category="ALLOCATION_FAILED",
                    severity="WARNING",
                    title=f"Allocation #{a_id} to {dest} FAILED",
                    message=f"Allocation #{a_id} failed in transit without delivery.",
                    entity_type="allocation",
                    entity_id=str(a_id),
                    fuel_type=fuel,
                    opened_tick=current_tick,
                    sim_run_id=sim_run_id,
                )
            )

    return specs, active_fingerprints
