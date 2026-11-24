"""Simulator state synchronization loop for BUP Fuel Supply Resilience Platform.

Features:
- SyncLoop: continuous state synchronization between simulator and in-memory WorldState.
- Full sync: concurrent GETs of all resources with last-good preservation and degraded tracking.
- Dynamic demand history retrieval with deduplication.
- Coalescing: at most one active cycle running, at most one pending tick remembered.
- Polling fallback: polls /v1/instance to detect tick progression or resets when SSE is unavailable.
- Reset detection: detects backwards tick movement, increments sim_run_id, clears state, logs reset.detected.
- Lifecycle hooks: executes on_cycle hooks sequentially with failure isolation.
- Prometheus observability: tracks fsip_cycle_duration_seconds and fsip_ticks_behind.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

from fsip_common.logging import get_logger
from fsip_common.models import Instance
from prometheus_client import REGISTRY, Counter, Gauge, Histogram

from app.db.buffer import WriteBuffer
from app.risk.stockout import assess
from app.sim.client import SimClient, SimInvalidPayload
from app.sim.validation import validate_resource
from app.state.persistence import StatePersistence
from app.state.publisher import Publisher
from app.state.store import WorldState

log = get_logger()

# ---------------------------------------------------------------- Prometheus Metrics
try:
    FSIP_CYCLE_DURATION_SECONDS = Histogram(
        "fsip_cycle_duration_seconds",
        "Duration of simulator state synchronization cycle in seconds",
        buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
    )
except ValueError:
    FSIP_CYCLE_DURATION_SECONDS = REGISTRY._names_to_collectors.get("fsip_cycle_duration_seconds")  # type: ignore[assignment]

try:
    FSIP_TICKS_BEHIND = Gauge(
        "fsip_ticks_behind",
        "Number of ticks the engine state is behind the simulator",
    )
except ValueError:
    FSIP_TICKS_BEHIND = REGISTRY._names_to_collectors.get("fsip_ticks_behind")  # type: ignore[assignment]

try:
    FSIP_LOOP_RESTARTS = Counter(
        "fsip_loop_restarts_total",
        "Number of loop crashes/restarts",
        ["loop"],
    )
except ValueError:
    FSIP_LOOP_RESTARTS = REGISTRY._names_to_collectors.get("fsip_loop_restarts_total")  # type: ignore[assignment]


# ---------------------------------------------------------------- SyncLoop
class SyncLoop:
    """Orchestrates periodic and event-driven state synchronization with the simulator."""

    def __init__(
        self,
        client: SimClient,
        store: WorldState,
        hooks: list[Callable[[WorldState], Awaitable[None]]] | None = None,
        poll_interval: float = 1.0,
        write_buffer: WriteBuffer | None = None,
        publisher: Publisher | None = None,
    ) -> None:
        self.client = client
        self.store = store
        self.hooks: list[Callable[[WorldState], Awaitable[None]]] = list(hooks or [])
        self.poll_interval = poll_interval
        self.write_buffer = write_buffer
        self.publisher = publisher
        self.persistence: StatePersistence | None = (
            StatePersistence(write_buffer) if write_buffer is not None else None
        )

        self.last_tick: int | None = store.tick if store.instance is not None else None
        self._is_running: bool = False
        self._pending_tick: int | None = None
        self._lock = asyncio.Lock()
        self._stop_event = asyncio.Event()

    def add_hook(self, hook: Callable[[WorldState], Awaitable[None]]) -> None:
        """Register a lifecycle hook to execute after each successful sync cycle."""
        self.hooks.append(hook)

    @property
    def is_running(self) -> bool:
        """Return True if a synchronization cycle is actively executing."""
        return self._is_running

    @property
    def pending_tick(self) -> int | None:
        """Return currently queued pending tick, if any."""
        return self._pending_tick

    def stop(self) -> None:
        """Signal the polling loop to stop gracefully."""
        self._stop_event.set()

    def on_reset(self, new_tick: int) -> None:
        """Handle simulator reset condition.

        Increments sim_run_id, clears cached store resources, resets last_tick,
        closes the active database simulation run, and logs reset.detected.
        """
        old_run = self.store.sim_run_id
        new_run = old_run + 1
        log.info(
            "reset.detected",
            old_sim_run_id=old_run,
            new_sim_run_id=new_run,
            old_tick=self.last_tick,
            new_tick=new_tick,
        )
        if self.persistence is not None:
            self.persistence.handle_reset(old_tick=self.last_tick, new_tick=new_tick)
        self.store.reset(new_sim_run_id=new_run)
        self.last_tick = new_tick

    async def on_notice(self, payload: dict[str, Any]) -> None:
        """Handle simulator.notice SSE event payloads (e.g. simulation reset)."""
        msg = str(payload.get("message", "")).lower()
        if "reset" in msg:
            log.info("sync.notice.reset_detected", message=payload.get("message"))
            self.on_reset(new_tick=0)
            await self.full_sync()


    def _calc_demand_limit(self, target_tick: int | None) -> int:
        """Calculate dynamic demand history retrieval limit according to formula:

        limit = min(2000, 12 * max(1, tick - last_tick) + 12).
        """
        if target_tick is not None and self.last_tick is not None:
            diff = max(1, target_tick - self.last_tick)
        elif target_tick is not None:
            diff = max(1, target_tick)
        else:
            diff = 1
        return min(2000, 12 * diff + 12)

    async def full_sync(self, tick: int | None = None) -> None:
        """Perform a full synchronization cycle across all simulator resources.

        - Fetches resources concurrently via asyncio.gather with return_exceptions=True.
        - Validates payloads with validate_resource().
        - Preserves last-good values for failing resources and updates degraded_reasons.
        - Retrieves demand history with dynamic limit and deduplicates observations.
        - Triggers reset detection on backward tick movement.
        - Executes registered lifecycle hooks sequentially with failure isolation.
        - Updates fsip_cycle_duration_seconds and fsip_ticks_behind Prometheus metrics.
        """
        start_time = time.perf_counter()
        demand_limit = self._calc_demand_limit(tick)

        # Check reset if tick was explicitly provided and went backward
        if tick is not None and self.last_tick is not None and tick < self.last_tick:
            self.on_reset(tick)

        resource_names = [
            "instance",
            "regions",
            "depots",
            "stations",
            "routes",
            "events",
            "supply",
            "allocations",
            "metrics",
            "demand",
        ]

        calls = [
            self.client.instance(),
            self.client.regions(),
            self.client.depots(),
            self.client.stations(),
            self.client.routes(),
            self.client.events(),
            self.client.supply_arrivals(),
            self.client.allocations(),
            self.client.metrics(),
            self.client.demand_history(limit=demand_limit),
        ]

        try:
            raw_results = await asyncio.gather(*calls, return_exceptions=True)
        except Exception as exc:
            duration = time.perf_counter() - start_time
            if FSIP_CYCLE_DURATION_SECONDS is not None:
                FSIP_CYCLE_DURATION_SECONDS.observe(duration)
            log.error("sim.sync.failed", error=str(exc), tick=tick, exc_info=True)
            raise

        partial: dict[str, Any] = {}
        demand_items: list[Any] | None = None
        demand_failed = False
        is_stale = False
        any_resource_failed = False

        for name, res in zip(resource_names, raw_results, strict=False):
            if isinstance(res, Exception):
                log.warning("sim.sync.resource_failed", resource=name, error=str(res))
                if name == "demand":
                    demand_failed = True
                else:
                    partial[name] = res
                any_resource_failed = True
                continue

            raw_data = res.data if hasattr(res, "data") else res
            if hasattr(res, "stale") and res.stale:
                is_stale = True

            try:
                validated = validate_resource(name, raw_data)
                if name == "demand":
                    demand_items = validated
                else:
                    partial[name] = validated
            except SimInvalidPayload as exc:
                log.warning("sim.invalid_payload", resource=name, errors=exc.errors)
                if name == "demand":
                    demand_failed = True
                else:
                    partial[name] = exc
                any_resource_failed = True
            except Exception as exc:
                log.warning("sim.sync.validation_error", resource=name, error=str(exc))
                if name == "demand":
                    demand_failed = True
                else:
                    partial[name] = exc
                any_resource_failed = True

        # Check if instance tick indicates reset (when tick was not passed or instance returned backward tick)
        if "instance" in partial and not isinstance(partial["instance"], Exception):
            inst = partial["instance"]
            if isinstance(inst, Instance):
                inst_tick = inst.tick
            elif isinstance(inst, dict):
                inst_tick = inst.get("tick")
            else:
                inst_tick = None

            if inst_tick is not None:
                if self.last_tick is not None and inst_tick < self.last_tick:
                    self.on_reset(inst_tick)
                else:
                    self.last_tick = inst_tick
        elif tick is not None and (self.last_tick is None or tick > self.last_tick):
            self.last_tick = tick

        # Apply snapshot updates to WorldState
        self.store.apply_snapshot(partial=partial, stale=is_stale)
        if demand_items is not None:
            self.store.apply_demand(demand_items)
            self.store.degraded_reasons.discard("demand_failed")
        elif demand_failed:
            self.store.degraded_reasons.add("demand_failed")

        # Assess risk across all stations, fuels, and depots
        try:
            station_risks, depot_projections = assess(self.store)
            self.store.risk_results = station_risks
            self.store.risk.clear()
            for (st_id, f_type), r_res in station_risks.items():
                self.store.risk.setdefault(st_id, {})[f_type] = r_res.to_compact_dict()

            self.store.depot_risk.clear()
            for (d_id, f_type), d_proj in depot_projections.items():
                self.store.depot_risk[(d_id, f_type)] = {
                    "projected_days_of_supply": d_proj.projected_days_of_supply,
                    "hours_to_depletion": d_proj.hours_to_depletion,
                    "hours_to_overflow": d_proj.hours_to_overflow,
                    "waste_liters": d_proj.total_predicted_waste_liters,
                }
        except Exception as exc:
            log.error("risk.assessment_failed", error=str(exc), exc_info=True)

        # Asynchronously enqueue persistence via WriteBuffer
        if self.persistence is not None:
            self.persistence.persist_cycle(self.store, demand_items=demand_items)

        # Publish state to Redis
        if self.publisher is not None:
            await self.publisher.publish_state(self.store)

        # Update metrics
        current_sim_tick = self.last_tick or (self.store.tick if self.store.instance else 0)
        if tick is not None:
            current_sim_tick = max(current_sim_tick, tick)
        behind = max(0, current_sim_tick - self.store.tick)
        if FSIP_TICKS_BEHIND is not None:
            FSIP_TICKS_BEHIND.set(behind)

        duration = time.perf_counter() - start_time
        if FSIP_CYCLE_DURATION_SECONDS is not None:
            FSIP_CYCLE_DURATION_SECONDS.observe(duration)

        if any_resource_failed:
            log.warning(
                "sim.sync.partial_failed",
                tick=self.store.tick,
                sim_run_id=self.store.sim_run_id,
                degraded_reasons=list(self.store.degraded_reasons),
                duration_ms=round(duration * 1000, 2),
            )
        else:
            log.info(
                "sim.sync.ok",
                tick=self.store.tick,
                sim_run_id=self.store.sim_run_id,
                stale=self.store.stale,
                duration_ms=round(duration * 1000, 2),
            )

        # Run registered lifecycle hooks sequentially & isolated
        for idx, hook in enumerate(self.hooks):
            try:
                await hook(self.store)
            except Exception as exc:
                log.error(
                    "sync.hook_failed",
                    hook_index=idx,
                    hook_name=getattr(hook, "__name__", str(hook)),
                    error=str(exc),
                    exc_info=True,
                )

    async def request_cycle(self, tick: int) -> None:
        """Request execution of a sync cycle for the specified simulator tick.

        Implements coalescing:
        - At most 1 cycle executes at any time.
        - If a cycle is currently running, records `tick` as `_pending_tick`.
        - Once the active cycle finishes, if `_pending_tick` is set, runs a single cycle
          for that pending tick.
        - Never queues more than one pending tick.
        """
        async with self._lock:
            if self._is_running:
                self._pending_tick = tick
                return
            self._is_running = True

        target_tick: int | None = tick
        try:
            while target_tick is not None:
                await self.full_sync(tick=target_tick)
                async with self._lock:
                    target_tick = self._pending_tick
                    self._pending_tick = None
        finally:
            async with self._lock:
                self._is_running = False

    async def run(self) -> None:
        """Long-running polling loop that polls /v1/instance every poll_interval seconds.

        Serves as a fallback tick source when SSE is not available or disconnected.
        Detects tick changes or simulator resets and requests state synchronization.
        Handles connection errors gracefully without crashing.
        """
        log.info("sync.loop_started", poll_interval=self.poll_interval)
        while not self._stop_event.is_set():
            try:
                res = await self.client.instance()
                raw_inst = res.data if hasattr(res, "data") else res

                cur_tick: int | None = None
                if isinstance(raw_inst, Instance):
                    cur_tick = raw_inst.tick
                elif isinstance(raw_inst, dict) and "tick" in raw_inst:
                    cur_tick = int(raw_inst["tick"])

                if cur_tick is not None:
                    if self.last_tick is None or cur_tick != self.last_tick:
                        await self.request_cycle(cur_tick)
            except asyncio.CancelledError:
                log.info("sync.loop_cancelled")
                break
            except Exception as exc:
                log.warning("sync.poll_failed", error=str(exc))

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.poll_interval)
                break
            except TimeoutError:
                pass
            except asyncio.CancelledError:
                break

        log.info("sync.loop_stopped")
