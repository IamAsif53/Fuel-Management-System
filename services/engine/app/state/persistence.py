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
from app.db.repo import close_sim_run, create_sim_run, insert_demand_rows, upsert_snapshot
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
        self.db_run_id: int | None = None
        self._ready_event = asyncio.Event()

    def set_id(self, run_id: int) -> None:
        """Record the database-assigned sequence ID and signal readiness."""
        self.db_run_id = run_id
        self._ready_event.set()

    @property
    def is_ready(self) -> bool:
        """Return True if the database run ID has been established."""
        return self.db_run_id is not None


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
                handle.set_id(sim_run.id)
                store.sim_run_id = sim_run.id
                log.info(
                    "db.sim_run.created",
                    sim_run_id=sim_run.id,
                    scenario_id=scenario_id,
                    seed=seed,
                    first_tick=first_tick,
                )

            try:
                self.write_buffer.submit(_create_run_op, kind="sim_run")
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
                d.model_dump(mode="json") if hasattr(d, "model_dump") else copy.deepcopy(d)
                for d in store.depots
            ]
            stations_copy = [
                s.model_dump(mode="json") if hasattr(s, "model_dump") else copy.deepcopy(s)
                for s in store.stations
            ]
            routes_copy = [
                r.model_dump(mode="json") if hasattr(r, "model_dump") else copy.deepcopy(r)
                for r in store.routes
            ]
            events_copy = [
                e.model_dump(mode="json") if hasattr(e, "model_dump") else copy.deepcopy(e)
                for e in store.events
            ]
            supply_copy = [
                s.model_dump(mode="json") if hasattr(s, "model_dump") else copy.deepcopy(s)
                for s in store.supply
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
                if not current_handle.is_ready:
                    raise RuntimeError(
                        f"Cannot persist snapshot for tick {tick}: sim_run_id not yet resolved"
                    )
                snapshot_data["sim_run_id"] = current_handle.db_run_id
                await upsert_snapshot(session, snapshot_data)
                log.debug(
                    "db.snapshot.persisted",
                    tick=tick,
                    sim_run_id=current_handle.db_run_id,
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
            for obs in demand_items:
                obs_id = obs.id if hasattr(obs, "id") else obs.get("id")
                if obs_id is None:
                    continue
                if obs_id in self.persisted_demand_ids:
                    continue
                self.persisted_demand_ids.add(obs_id)

                station_id = obs.station_id if hasattr(obs, "station_id") else obs.get("station_id")
                fuel_type = str(obs.fuel_type if hasattr(obs, "fuel_type") else obs.get("fuel_type"))
                obs_tick = obs.tick if hasattr(obs, "tick") else obs.get("tick")
                obs_time = ensure_utc_datetime(
                    obs.sim_time if hasattr(obs, "sim_time") else obs.get("sim_time")
                )
                raw_demand = obs.demand_liters if hasattr(obs, "demand_liters") else obs.get("demand_liters", 0.0)
                demand_liters = float(raw_demand)
                raw_served = obs.served_liters if hasattr(obs, "served_liters") else obs.get("served_liters", 0.0)
                served_liters = float(raw_served)
                raw_unmet = obs.unmet_liters if hasattr(obs, "unmet_liters") else obs.get("unmet_liters", 0.0)
                unmet_liters = float(raw_unmet)

                new_demand_records.append({
                    "sim_row_id": obs_id,
                    "station_id": station_id,
                    "fuel_type": fuel_type,
                    "tick": obs_tick,
                    "sim_time": obs_time,
                    "demand_liters": demand_liters,
                    "served_liters": served_liters,
                    "unmet_liters": unmet_liters,
                })

            if new_demand_records:
                async def _save_demand_op(session: AsyncSession) -> None:
                    if not current_handle.is_ready:
                        raise RuntimeError(
                            f"Cannot persist {len(new_demand_records)} demand rows: sim_run_id not yet resolved"
                        )
                    for row in new_demand_records:
                        row["sim_run_id"] = current_handle.db_run_id
                    await insert_demand_rows(session, new_demand_records)
                    log.debug(
                        "db.demand.persisted",
                        count=len(new_demand_records),
                        sim_run_id=current_handle.db_run_id,
                    )

                try:
                    self.write_buffer.submit(_save_demand_op, kind="demand")
                    if FSIP_DEMAND_ROWS_ENQUEUED is not None:
                        FSIP_DEMAND_ROWS_ENQUEUED.inc(len(new_demand_records))
                    log.info(
                        "db.demand.enqueued",
                        count=len(new_demand_records),
                        tick=tick,
                    )
                except Exception as exc:
                    if FSIP_PERSISTENCE_ERRORS is not None:
                        FSIP_PERSISTENCE_ERRORS.labels(kind="demand").inc()
                    log.error(
                        "db.persistence.enqueue_failed",
                        kind="demand",
                        error=str(exc),
                        exc_info=True,
                    )
