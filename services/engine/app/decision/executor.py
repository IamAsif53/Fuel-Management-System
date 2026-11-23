"""Idempotent allocation executor for FSIP.

Submits approved allocation lines to the simulator at POST /v1/allocations,
guaranteeing idempotency, tracking line-level submission state, handling capacity
exhaustion and simulator rejection, and updating decision lifecycle state atomically.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from fsip_common.logging import get_logger
from prometheus_client import REGISTRY, Counter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import EngineSettings, get_settings
from app.db.models import Decision, DecisionLine
from app.sim.client import (
    SimClient,
    SimDomainError,
    SimFaultInjected,
    SimResult,
    SimUnavailable,
)

if TYPE_CHECKING:
    from app.state.publisher import Publisher

# Metrics
try:
    FSIP_ALLOCATIONS_SUBMITTED_TOTAL = Counter(
        "fsip_allocations_submitted_total",
        "Total simulator allocations submitted successfully",
        ["fuel_type", "depot_id"],
    )
except ValueError:
    FSIP_ALLOCATIONS_SUBMITTED_TOTAL = REGISTRY._names_to_collectors.get("fsip_allocations_submitted_total")  # type: ignore[assignment]

try:
    FSIP_ALLOCATIONS_DEFERRED_TOTAL = Counter(
        "fsip_allocations_deferred_total",
        "Total simulator allocations deferred due to capacity limits",
        ["fuel_type", "depot_id"],
    )
except ValueError:
    FSIP_ALLOCATIONS_DEFERRED_TOTAL = REGISTRY._names_to_collectors.get("fsip_allocations_deferred_total")  # type: ignore[assignment]

try:
    FSIP_ALLOCATIONS_FAILED_TOTAL = Counter(
        "fsip_allocations_failed_total",
        "Total simulator allocations failed or rejected",
        ["fuel_type", "reason"],
    )
except ValueError:
    FSIP_ALLOCATIONS_FAILED_TOTAL = REGISTRY._names_to_collectors.get("fsip_allocations_failed_total")  # type: ignore[assignment]


class Executor:
    """Executes approved decision lines against the simulator."""

    def __init__(
        self,
        sim_client: SimClient,
        session_factory: async_sessionmaker[AsyncSession],
        publisher: Publisher | None = None,
        settings: EngineSettings | None = None,
        max_retries: int = 3,
        initial_backoff: float = 0.2,
    ) -> None:
        self.sim_client = sim_client
        self.session_factory = session_factory
        self.publisher = publisher
        self.settings = settings or get_settings()
        self.max_retries = max_retries
        self.initial_backoff = initial_backoff
        self._log = get_logger()

    async def execute(self, decision: Decision, lines: list[DecisionLine]) -> Decision:
        """Submit approved lines to the simulator idempotently.

        Updates lines and decision status in the database and emits an audit event.
        """
        if not lines:
            self._log.info("executor.skip_empty", decision_id=decision.id)
            return decision

        self._log.info(
            "executor.executing",
            decision_id=decision.id,
            sim_run_id=decision.sim_run_id,
            lines_count=len(lines),
        )

        for line in lines:
            # Skip lines already in terminal/submitted state
            if line.status in ("SUBMITTED", "ARRIVED", "CANCELLED"):
                continue

            await self._execute_line(decision, line)

        # Re-assess overall decision status
        submitted_count = sum(1 for ln in lines if ln.status in ("SUBMITTED", "ARRIVED"))
        deferred_count = sum(1 for ln in lines if ln.status == "DEFERRED")
        failed_count = sum(1 for ln in lines if ln.status in ("FAILED", "REJECTED_BY_SIM"))

        if submitted_count == len(lines):
            decision_status = "EXECUTED"
            event_type = "decision.executed"
        elif submitted_count > 0:
            decision_status = "PARTIAL"
            event_type = "decision.partial"
        else:
            decision_status = "FAILED"
            event_type = "decision.failed"

        decision.status = decision_status

        # Persist line and decision status updates to database
        async with self.session_factory() as session:
            # Load decision and lines attached to this session
            db_dec = await session.get(Decision, decision.id)
            if db_dec:
                db_dec.status = decision_status

            # Update lines
            line_ids = [ln.id for ln in lines if ln.id is not None]
            if line_ids:
                stmt = select(DecisionLine).where(DecisionLine.id.in_(line_ids))
                res = await session.execute(stmt)
                db_lines = {ln.id: ln for ln in res.scalars().all()}

                for ln in lines:
                    db_ln = db_lines.get(ln.id)
                    if db_ln:
                        db_ln.status = ln.status
                        db_ln.sim_allocation_id = ln.sim_allocation_id
                        db_ln.sim_error_code = ln.sim_error_code
                        db_ln.reason = ln.reason

            await session.commit()

        # Publish state notification
        if self.publisher is not None:
            await self.publisher.event(
                type=event_type,
                data={
                    "decision_id": decision.id,
                    "sim_run_id": decision.sim_run_id,
                    "status": decision_status,
                    "submitted_lines": submitted_count,
                    "deferred_lines": deferred_count,
                    "failed_lines": failed_count,
                    "total_lines": len(lines),
                },
                sim_run_id=decision.sim_run_id,
                tick=decision.tick,
            )

        self._log.info(
            "executor.completed",
            decision_id=decision.id,
            status=decision_status,
            submitted=submitted_count,
            deferred=deferred_count,
            failed=failed_count,
        )

        return decision

    async def _execute_line(self, decision: Decision, line: DecisionLine) -> None:
        """Submit a single line to the simulator with bounded retries."""
        payload: dict[str, Any] = {
            "idempotency_key": line.idempotency_key,
            "source_depot_id": line.source_depot_id,
            "destination_station_id": line.destination_station_id,
            "route_id": line.route_id,
            "fuel_type": line.fuel_type,
            "quantity": float(line.quantity),
        }

        backoff = self.initial_backoff

        for attempt in range(1, self.max_retries + 1):
            try:
                res: SimResult = await self.sim_client.create_allocation(payload)

                # Success
                data = res.data if isinstance(res.data, dict) else getattr(res.data, "__dict__", {})
                alloc_id = data.get("id")
                line.status = "SUBMITTED"
                line.sim_allocation_id = alloc_id
                line.sim_error_code = None

                if FSIP_ALLOCATIONS_SUBMITTED_TOTAL is not None:
                    FSIP_ALLOCATIONS_SUBMITTED_TOTAL.labels(
                        fuel_type=line.fuel_type,
                        depot_id=line.source_depot_id,
                    ).inc()

                self._log.info(
                    "line.submitted",
                    decision_id=decision.id,
                    line_no=line.line_no,
                    idempotency_key=line.idempotency_key,
                    sim_allocation_id=alloc_id,
                )
                return

            except SimDomainError as err:
                # 409 or other domain rejections
                if err.code == "DISPATCH_CAPACITY_EXCEEDED":
                    line.status = "DEFERRED"
                    line.sim_error_code = err.code
                    line.reason = (line.reason or "") + " [DEFERRED: Dispatch capacity exceeded]"

                    if FSIP_ALLOCATIONS_DEFERRED_TOTAL is not None:
                        FSIP_ALLOCATIONS_DEFERRED_TOTAL.labels(
                            fuel_type=line.fuel_type,
                            depot_id=line.source_depot_id,
                        ).inc()

                    self._log.warn(
                        "line.deferred",
                        decision_id=decision.id,
                        line_no=line.line_no,
                        reason=err.message,
                    )
                    return

                if err.code in ("DUPLICATE_IDEMPOTENCY_KEY", "ALREADY_EXISTS"):
                    # Simulator replay
                    alloc_id = err.data.get("allocation_id") or err.data.get("id") if err.data else None
                    line.status = "SUBMITTED"
                    if alloc_id:
                        line.sim_allocation_id = int(alloc_id)
                    line.sim_error_code = None
                    self._log.info(
                        "line.already_submitted",
                        decision_id=decision.id,
                        line_no=line.line_no,
                        alloc_id=alloc_id,
                    )
                    return

                # Other domain rejections (404, 422, unrecoverable 409)
                line.status = "REJECTED_BY_SIM"
                line.sim_error_code = err.code
                line.reason = (line.reason or "") + f" [REJECTED: {err.code}: {err.message}]"

                if FSIP_ALLOCATIONS_FAILED_TOTAL is not None:
                    FSIP_ALLOCATIONS_FAILED_TOTAL.labels(
                        fuel_type=line.fuel_type,
                        reason=err.code,
                    ).inc()

                self._log.error(
                    "line.rejected_by_sim",
                    decision_id=decision.id,
                    line_no=line.line_no,
                    code=err.code,
                    message=err.message,
                )
                return

            except (SimUnavailable, SimFaultInjected, Exception) as err:
                # Transient error, retry if attempts remain
                if attempt < self.max_retries:
                    self._log.warn(
                        "line.transient_error_retry",
                        decision_id=decision.id,
                        line_no=line.line_no,
                        attempt=attempt,
                        error=str(err),
                        backoff=backoff,
                    )
                    await asyncio.sleep(backoff)
                    backoff *= 2.0
                    continue

                # Exhausted retries
                line.status = "FAILED"
                line.sim_error_code = "SIMULATOR_UNAVAILABLE"
                line.reason = (line.reason or "") + f" [FAILED: {err}]"

                if FSIP_ALLOCATIONS_FAILED_TOTAL is not None:
                    FSIP_ALLOCATIONS_FAILED_TOTAL.labels(
                        fuel_type=line.fuel_type,
                        reason="UNAVAILABLE",
                    ).inc()

                self._log.error(
                    "line.failed_exhausted",
                    decision_id=decision.id,
                    line_no=line.line_no,
                    attempts=attempt,
                    error=str(err),
                )
                return
