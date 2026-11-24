"""Database persistence adapter for Simulator SyncLoop (Task 2.4).

Integrates SyncLoop, WorldState, database repository, and WriteBuffer:
- Manages database simulation run lifecycle (initial creation, closing on reset, recreating).
- Enqueues world snapshot records on every successful tick.
- Enqueues newly observed demand rows with deduplication.
- Preserves asynchronous, non-blocking operation: SyncLoop never blocks on DB writes.
- Ensures deep-copied, immutable payloads for queued callbacks.
- Enforces FIFO ordering so dependent writes (snapshots, demand) always resolve their sim_run_id.
"""

from __future__ import annotations

import asyncio
import copy
from datetime import UTC, datetime
from typing import Any

from fsip_common.logging import get_logger
from fsip_common.models import DemandObservation
from fsip_common.timeutil import parse_sim_time
from prometheus_client import REGISTRY, Counter
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.buffer import WriteBuffer
from app.db.repo import (
    close_sim_run,
    create_sim_run,
    insert_demand_rows,
    insert_risk_rows,
    upsert_snapshot,
)
from app.state.store import WorldState

log = get_logger()

# -----------------------------------------------------------------------------
# Prometheus Metrics
# -----------------------------------------------------------------------------

try:
    FSIP_SNAPSHOTS_ENQUEUED = Counter(
        "fsip_snapshots_enqueued_total",
        "Total world snapshots enqueued for persistence",
    )
except ValueError:
    FSIP_SNAPSHOTS_ENQUEUED = REGISTRY._names_to_collectors.get("fsip_snapshots_enqueued_total")  # type: ignore[assignment]

try:
    FSIP_DEMAND_ROWS_ENQUEUED = Counter(
        "fsip_demand_rows_enqueued_total",
        "Total demand observation rows enqueued for persistence",
    )
except ValueError:
    FSIP_DEMAND_ROWS_ENQUEUED = REGISTRY._names_to_collectors.get("fsip_demand_rows_enqueued_total")  # type: ignore[assignment]

try:
    FSIP_PERSISTENCE_ERRORS = Counter(
        "fsip_persistence_enqueue_errors_total",
        "Total persistence enqueue errors in sync loop",
        ["kind"],
    )
except ValueError:
    FSIP_PERSISTENCE_ERRORS = REGISTRY._names_to_collectors.get("fsip_persistence_enqueue_errors_total")  # type: ignore[assignment]


# -----------------------------------------------------------------------------
# Timestamp Helper
# -----------------------------------------------------------------------------


def ensure_utc_datetime(dt: datetime | str | None) -> datetime:
    """Ensure a timestamp is a valid UTC-aware datetime object."""
    if dt is None:
        return datetime.now(UTC)
    if isinstance(dt, str):
        return parse_sim_time(dt)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


# -----------------------------------------------------------------------------
# RunHandle: Asynchronous Run ID Resolution
# -----------------------------------------------------------------------------


class RunHandle:
    """Handle tracking an asynchronous database simulation run creation."""

    def __init__(self, key: str | None = None) -> None:
        self.key = key
        self.committed_id: int | None = None
        self.pending_id: int | None = None
        self._ready_event = asyncio.Event()

    @property
    def db_run_id(self) -> int | None:
        """The committed database run ID exposed to external callers."""
        return self.committed_id

    @property
    def is_ready(self) -> bool:
        """Return True if the database run ID has been successfully committed."""
        return self.committed_id is not None

    def set_id(self, run_id: int) -> None:
        """Directly record committed database run ID (for testing or synchronous setup)."""
        self.committed_id = run_id
        self.pending_id = None
        self._ready_event.set()

    def set_pending_id(self, run_id: int) -> None:
        """Record the pending database-assigned sequence ID within an in-flight transaction."""
        self.pending_id = run_id

    def commit_id(self) -> int | None:
        """Promote the pending ID to committed status upon successful transaction commit."""
        if self.pending_id is not None:
            self.committed_id = self.pending_id
            self.pending_id = None
            self._ready_event.set()
        return self.committed_id

    def rollback_pending(self) -> None:
        """Clear the pending ID on transaction rollback or failure."""
        self.pending_id = None

    def resolve_tx_run_id(self) -> int:
        """Resolve run ID for operations executing within the same transaction or committed."""
        if self.pending_id is not None:
            return self.pending_id
        if self.committed_id is not None:
            return self.committed_id
        raise RuntimeError("sim_run_id not yet resolved")


# -----------------------------------------------------------------------------
# StatePersistence Adapter
# -----------------------------------------------------------------------------


class StatePersistence:
    """Orchestrates database persistence for SyncLoop via WriteBuffer."""

    def __init__(self, write_buffer: WriteBuffer) -> None:
        self.write_buffer = write_buffer
        self.active_run_handle: RunHandle | None = None
        self.last_persisted_tick: int | None = None
        self.persisted_demand_ids: set[int] = set()
        self.pending_demand_ids: set[int] = set()
        self.last_persisted_risk_levels: dict[tuple[str, str], str] = {}
        self.last_risk_persist_tick: int = -1

    def handle_reset(self, old_tick: int | None, new_tick: int) -> None:
        """Handle simulator reset: enqueue close for old run and reset tracking state."""
        old_handle = self.active_run_handle
        if old_handle is not None:
            # Enqueue closing old simulation run
            async def _close_old_run_op(session: AsyncSession) -> None:
                if old_handle.db_run_id is not None:
                    await close_sim_run(
                        session,
                        sim_run_id=old_handle.db_run_id,
                        ended_at=datetime.now(UTC),
                        last_tick=old_tick,
                    )
                    log.info(
                        "db.sim_run.closed",
                        sim_run_id=old_handle.db_run_id,
                        last_tick=old_tick,
                    )

            try:
                self.write_buffer.submit(_close_old_run_op, kind="sim_run")
            except Exception as exc:
                if FSIP_PERSISTENCE_ERRORS is not None:
                    FSIP_PERSISTENCE_ERRORS.labels(kind="close_run").inc()
                log.error(
                    "db.persistence.enqueue_failed",
                    kind="close_run",
                    error=str(exc),
                    exc_info=True,
                )

        # Reset active run state so next cycle creates a fresh database run
        self.active_run_handle = None
        self.last_persisted_tick = None
        self.persisted_demand_ids.clear()
        self.pending_demand_ids.clear()
        self.last_persisted_risk_levels.clear()
        self.last_risk_persist_tick = -1

    def persist_cycle(
        self,
        store: WorldState,
        demand_items: list[DemandObservation | dict[str, Any]] | None = None,
    ) -> None:
        """Persist simulation run (if needed), snapshot, and newly observed demand rows."""
        inst = store.instance
        if inst is None:
            return

        # ---------------------------------------------------------------------
        # 1. Initial / New Simulation Run Creation
        # ---------------------------------------------------------------------
        if self.active_run_handle is None:
            handle = RunHandle(key=f"{inst.scenario_id}:{inst.seed}")
            self.active_run_handle = handle

            scenario_id = inst.scenario_id
            seed = inst.seed
            tick_minutes = inst.tick_minutes
            first_tick = inst.tick
            started_at = ensure_utc_datetime(inst.sim_time)

            async def _create_run_op(session: AsyncSession) -> None:
                sim_run = await create_sim_run(
                    session,
                    scenario_id=scenario_id,
                    seed=seed,
                    tick_minutes=tick_minutes,
                    started_at=started_at,
                    first_tick=first_tick,
                )
                handle.set_pending_id(sim_run.id)
                log.debug(
                    "db.sim_run.pending",
                    sim_run_id=sim_run.id,
                    scenario_id=scenario_id,
                    seed=seed,
                    first_tick=first_tick,
                )

            def _on_create_run_commit() -> None:
                committed_id = handle.commit_id()
                if committed_id is not None:
                    store.sim_run_id = committed_id
                    log.info(
                        "db.sim_run.created",
                        sim_run_id=committed_id,
                        scenario_id=scenario_id,
                        seed=seed,
                        first_tick=first_tick,
                    )

            def _on_create_run_rollback() -> None:
                handle.rollback_pending()
                log.debug("db.sim_run.rollback_pending", handle_key=handle.key)

            try:
                self.write_buffer.submit(
                    _create_run_op,
                    kind="sim_run",
                    on_commit=_on_create_run_commit,
                    on_rollback=_on_create_run_rollback,
                )
            except Exception as exc:
                if FSIP_PERSISTENCE_ERRORS is not None:
                    FSIP_PERSISTENCE_ERRORS.labels(kind="sim_run").inc()
                log.error(
                    "db.persistence.enqueue_failed",
                    kind="sim_run",
                    error=str(exc),
                    exc_info=True,
                )

        current_handle = self.active_run_handle
        tick = store.tick
        sim_time = ensure_utc_datetime(store.sim_time)

        # ---------------------------------------------------------------------
        # 2. World Snapshot Persistence (One per distinct successful tick)
        # ---------------------------------------------------------------------
        if self.last_persisted_tick != tick:
            self.last_persisted_tick = tick

            # Create isolated deep copies of JSONB fields to prevent race with in-memory mutations
            depots_copy = [
                d.model_dump(mode="json") if hasattr(d, "model_dump") else copy.deepcopy(d) for d in store.depots
            ]
            stations_copy = [
                s.model_dump(mode="json") if hasattr(s, "model_dump") else copy.deepcopy(s) for s in store.stations
            ]
            routes_copy = [
                r.model_dump(mode="json") if hasattr(r, "model_dump") else copy.deepcopy(r) for r in store.routes
            ]
            events_copy = [
                e.model_dump(mode="json") if hasattr(e, "model_dump") else copy.deepcopy(e) for e in store.events
            ]
            supply_copy = [
                s.model_dump(mode="json") if hasattr(s, "model_dump") else copy.deepcopy(s) for s in store.supply
            ]
            metrics_copy = (
                store.metrics.model_dump(mode="json")
                if store.metrics and hasattr(store.metrics, "model_dump")
                else (copy.deepcopy(store.metrics) if store.metrics else None)
            )
            stale_copy = bool(store.stale)
            degraded_copy = bool(store.degraded)

            snapshot_data: dict[str, Any] = {
                "tick": tick,
                "sim_time": sim_time,
                "depots": depots_copy,
                "stations": stations_copy,
                "routes": routes_copy,
                "events": events_copy,
                "supply": supply_copy,
                "metrics": metrics_copy,
                "stale": stale_copy,
                "degraded": degraded_copy,
            }

            async def _save_snapshot_op(session: AsyncSession) -> None:
                run_id = current_handle.resolve_tx_run_id()
                snapshot_data["sim_run_id"] = run_id
                await upsert_snapshot(session, snapshot_data)
                log.debug(
                    "db.snapshot.persisted",
                    tick=tick,
                    sim_run_id=run_id,
                )

            try:
                self.write_buffer.submit(_save_snapshot_op, kind="snapshot")
                if FSIP_SNAPSHOTS_ENQUEUED is not None:
                    FSIP_SNAPSHOTS_ENQUEUED.inc()
                log.info(
                    "db.snapshot.enqueued",
                    tick=tick,
                    stale=stale_copy,
                    degraded=degraded_copy,
                )
            except Exception as exc:
                if FSIP_PERSISTENCE_ERRORS is not None:
                    FSIP_PERSISTENCE_ERRORS.labels(kind="snapshot").inc()
                log.error(
                    "db.persistence.enqueue_failed",
                    kind="snapshot",
                    error=str(exc),
                    exc_info=True,
                )

        # ---------------------------------------------------------------------
        # 3. Demand Observations Persistence (Only newly observed rows)
        # ---------------------------------------------------------------------
        if demand_items:
            new_demand_records: list[dict[str, Any]] = []
            new_obs_ids: list[int] = []
            for obs in demand_items:
                obs_id = obs.id if hasattr(obs, "id") else obs.get("id")
                if obs_id is None:
                    continue
                if obs_id in self.persisted_demand_ids or obs_id in self.pending_demand_ids:
                    continue

                new_obs_ids.append(obs_id)
                station_id = obs.station_id if hasattr(obs, "station_id") else obs.get("station_id")
                fuel_type = str(obs.fuel_type if hasattr(obs, "fuel_type") else obs.get("fuel_type"))
                obs_tick = obs.tick if hasattr(obs, "tick") else obs.get("tick")
                obs_time = ensure_utc_datetime(obs.sim_time if hasattr(obs, "sim_time") else obs.get("sim_time"))
                raw_demand = obs.demand_liters if hasattr(obs, "demand_liters") else obs.get("demand_liters", 0.0)
                demand_liters = float(raw_demand)
                raw_served = obs.served_liters if hasattr(obs, "served_liters") else obs.get("served_liters", 0.0)
                served_liters = float(raw_served)
                raw_unmet = obs.unmet_liters if hasattr(obs, "unmet_liters") else obs.get("unmet_liters", 0.0)
                unmet_liters = float(raw_unmet)

                new_demand_records.append(
                    {
                        "sim_row_id": obs_id,
                        "station_id": station_id,
                        "fuel_type": fuel_type,
                        "tick": obs_tick,
                        "sim_time": obs_time,
                        "demand_liters": demand_liters,
                        "served_liters": served_liters,
                        "unmet_liters": unmet_liters,
                    }
                )

            if new_demand_records:
                batch_obs_ids = list(new_obs_ids)
                self.pending_demand_ids.update(batch_obs_ids)

                async def _save_demand_op(session: AsyncSession) -> None:
                    run_id = current_handle.resolve_tx_run_id()
                    for row in new_demand_records:
                        row["sim_run_id"] = run_id
                    await insert_demand_rows(session, new_demand_records)
                    log.debug(
                        "db.demand.persisted",
                        count=len(new_demand_records),
                        sim_run_id=run_id,
                    )

                def _on_demand_commit() -> None:
                    self.pending_demand_ids.difference_update(batch_obs_ids)
                    self.persisted_demand_ids.update(batch_obs_ids)
                    if len(self.persisted_demand_ids) > 50000:
                        min_keep = max(self.persisted_demand_ids) - 40000
                        self.persisted_demand_ids = {i for i in self.persisted_demand_ids if i >= min_keep}

                def _on_demand_rollback() -> None:
                    self.pending_demand_ids.difference_update(batch_obs_ids)

                def _on_demand_discard() -> None:
                    self.pending_demand_ids.difference_update(batch_obs_ids)

                try:
                    self.write_buffer.submit(
                        _save_demand_op,
                        kind="demand",
                        on_commit=_on_demand_commit,
                        on_rollback=_on_demand_rollback,
                        on_discard=_on_demand_discard,
                    )
                    if FSIP_DEMAND_ROWS_ENQUEUED is not None:
                        FSIP_DEMAND_ROWS_ENQUEUED.inc(len(new_demand_records))
                    log.info(
                        "db.demand.enqueued",
                        count=len(new_demand_records),
                        tick=tick,
                    )
                except Exception as exc:
                    self.pending_demand_ids.difference_update(batch_obs_ids)
                    if FSIP_PERSISTENCE_ERRORS is not None:
                        FSIP_PERSISTENCE_ERRORS.labels(kind="demand").inc()
                    log.error(
                        "db.persistence.enqueue_failed",
                        kind="demand",
                        error=str(exc),
                        exc_info=True,
                    )

        # ---------------------------------------------------------------------
        # 4. Risk Assessments Persistence (Task 6.3)
        # ---------------------------------------------------------------------
        # Persist risk_assessments each cycle only when level changes or every 4 ticks
        risk_results = getattr(store, "risk_results", {})
        if risk_results:
            any_level_changed = any(
                res.risk_level != self.last_persisted_risk_levels.get(k)
                for k, res in risk_results.items()
            )
            should_persist_risk = (
                any_level_changed
                or (tick % 4 == 0)
                or (self.last_risk_persist_tick < 0)
            )

            if should_persist_risk:
                risk_records = []
                for (st_id, f_type), res in risk_results.items():
                    compact_drivers = {
                        "drivers": res.drivers,
                        "stockout_prob_lead": round(res.stockout_prob_lead, 4),
                        "stockout_prob_horizon": round(res.stockout_prob_horizon, 4),
                        "lead_time_hours": res.lead_time_hours,
                        "cover_hours": res.cover_hours,
                        "band_p10": res.band_p10,
                        "band_p50": res.band_p50,
                        "band_p90": res.band_p90,
                    }
                    risk_records.append({
                        "station_id": st_id,
                        "fuel_type": f_type,
                        "tick": tick,
                        "sim_time": sim_time,
                        "inventory": float(res.inventory),
                        "inflight": float(res.inflight),
                        "hours_to_stockout": res.hours_to_stockout,
                        "stockout_prob": float(res.stockout_prob_horizon),
                        "risk_level": res.risk_level,
                        "drivers": compact_drivers,
                    })

                new_levels = {k: res.risk_level for k, res in risk_results.items()}

                async def _save_risk_op(session: AsyncSession) -> None:
                    run_id = current_handle.resolve_tx_run_id()
                    for r in risk_records:
                        r["sim_run_id"] = run_id
                    await insert_risk_rows(session, risk_records)
                    log.debug("db.risk.persisted", count=len(risk_records), sim_run_id=run_id, tick=tick)

                def _on_risk_commit() -> None:
                    self.last_persisted_risk_levels.update(new_levels)
                    self.last_risk_persist_tick = tick

                try:
                    self.write_buffer.submit(
                        _save_risk_op,
                        kind="risk",
                        on_commit=_on_risk_commit,
                    )
                    log.info("db.risk.enqueued", count=len(risk_records), tick=tick)
                except Exception as exc:
                    if FSIP_PERSISTENCE_ERRORS is not None:
                        FSIP_PERSISTENCE_ERRORS.labels(kind="risk").inc()
                    log.error("db.persistence.enqueue_failed", kind="risk", error=str(exc), exc_info=True)
