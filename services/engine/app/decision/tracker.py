"""Allocation outcome tracker for FSIP.

Tracks the status of in-flight and submitted allocations against the simulator's
active allocations on each cycle. Updates line statuses (IN_TRANSIT, ARRIVED, FAILED,
CANCELLED), detects suspected overflows at station arrival, computes received liters,
records Prometheus metrics, emits audit events, and evaluates DecisionOutcomes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fsip_common.logging import get_logger
from prometheus_client import REGISTRY, Counter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.db.models import Decision, DecisionLine, DecisionOutcome

if TYPE_CHECKING:
    from app.state.publisher import Publisher

# Metrics
try:
    FSIP_ALLOCATION_FAILURES_TOTAL = Counter(
        "fsip_allocation_failures_total",
        "Total simulator allocations that failed",
        ["fuel_type"],
    )
except ValueError:
    FSIP_ALLOCATION_FAILURES_TOTAL = REGISTRY._names_to_collectors.get("fsip_allocation_failures_total")  # type: ignore[assignment]

try:
    FSIP_OVERFLOW_LITERS_TOTAL = Counter(
        "fsip_overflow_liters_total",
        "Total liters overflowing station capacity upon delivery",
        ["fuel_type"],
    )
except ValueError:
    FSIP_OVERFLOW_LITERS_TOTAL = REGISTRY._names_to_collectors.get("fsip_overflow_liters_total")  # type: ignore[assignment]


def _get_field(obj: Any, key: str, default: Any = None) -> Any:
    """Helper to extract field from either a dict or object instance."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


class AllocationOutcomeTracker:
    """Tracks active allocations and evaluates delivery outcomes."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        publisher: Publisher | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.publisher = publisher
        self._log = get_logger()

    async def track_cycle(self, state: Any) -> int:
        """Inspect current simulator allocations and reconcile with database decision lines.

        Returns the number of decision lines that were updated.
        """
        cur_tick = int(_get_field(state, "tick", 0))
        sim_run_id = _get_field(state, "sim_run_id", None)
        if sim_run_id is None:
            return 0

        raw_allocations = _get_field(state, "allocations", [])
        if not raw_allocations:
            return 0

        # Build lookup tables for simulator allocations
        alloc_by_key: dict[str, Any] = {}
        alloc_by_id: dict[int, Any] = {}

        for a in raw_allocations:
            key = _get_field(a, "idempotency_key")
            a_id = _get_field(a, "id")
            if key:
                alloc_by_key[str(key)] = a
            if a_id is not None:
                try:
                    alloc_by_id[int(a_id)] = a
                except (ValueError, TypeError):
                    pass

        updated_count = 0
        touched_decisions: set[int] = set()

        async with self.session_factory() as session:
            # Query active lines for this simulation run
            stmt = (
                select(DecisionLine)
                .join(Decision, DecisionLine.decision_id == Decision.id)
                .where(
                    Decision.sim_run_id == sim_run_id,
                    DecisionLine.status.in_(["SUBMITTED", "IN_TRANSIT", "PENDING"]),
                )
            )
            res = await session.execute(stmt)
            active_lines = res.scalars().all()

            if not active_lines:
                return 0

            stations_by_id = self._build_station_map(state)

            for line in active_lines:
                sim_alloc = alloc_by_key.get(line.idempotency_key)
                if not sim_alloc and line.sim_allocation_id:
                    sim_alloc = alloc_by_id.get(line.sim_allocation_id)

                if not sim_alloc:
                    continue

                sim_status = str(_get_field(sim_alloc, "status", "")).upper()
                sim_id = _get_field(sim_alloc, "id")
                arr_tick = _get_field(sim_alloc, "actual_arrival_tick")
                exp_arr = _get_field(sim_alloc, "expected_arrival_tick")

                if sim_id and line.sim_allocation_id is None:
                    line.sim_allocation_id = int(sim_id)

                if exp_arr is not None:
                    line.expected_arrival_tick = int(exp_arr)

                # State Transitions
                if sim_status == "IN_TRANSIT" and line.status != "IN_TRANSIT":
                    line.status = "IN_TRANSIT"
                    updated_count += 1
                    touched_decisions.add(line.decision_id)
                    self._log.info(
                        "allocation.departed",
                        line_id=line.id,
                        sim_allocation_id=line.sim_allocation_id,
                        tick=cur_tick,
                    )

                elif sim_status == "ARRIVED" and line.status != "ARRIVED":
                    line.status = "ARRIVED"
                    line.actual_arrival_tick = int(arr_tick) if arr_tick is not None else cur_tick

                    # Calculate received liters & detect overflow
                    st = stations_by_id.get(line.destination_station_id)
                    received_liters, overflow = self._calculate_received_and_overflow(line, st)
                    line.received_liters = received_liters

                    if overflow > 0.0:
                        if FSIP_OVERFLOW_LITERS_TOTAL is not None:
                            FSIP_OVERFLOW_LITERS_TOTAL.labels(fuel_type=line.fuel_type).inc(overflow)
                        self._log.warn(
                            "allocation.overflow_suspected",
                            line_id=line.id,
                            station_id=line.destination_station_id,
                            fuel_type=line.fuel_type,
                            overflow_liters=overflow,
                        )

                    updated_count += 1
                    touched_decisions.add(line.decision_id)
                    self._log.info(
                        "allocation.arrived",
                        line_id=line.id,
                        sim_allocation_id=line.sim_allocation_id,
                        actual_arrival_tick=line.actual_arrival_tick,
                        received_liters=line.received_liters,
                    )

                    if self.publisher is not None:
                        await self.publisher.event(
                            type="allocation.arrived",
                            data={
                                "line_id": line.id,
                                "decision_id": line.decision_id,
                                "sim_allocation_id": line.sim_allocation_id,
                                "fuel_type": line.fuel_type,
                                "received_liters": line.received_liters,
                                "actual_arrival_tick": line.actual_arrival_tick,
                            },
                            sim_run_id=sim_run_id,
                            tick=cur_tick,
                        )

                elif sim_status == "FAILED" and line.status != "FAILED":
                    line.status = "FAILED"
                    fail_reason = _get_field(sim_alloc, "failure_reason") or "Simulator allocation failed"
                    line.reason = (line.reason or "") + f" [FAILED: {fail_reason}]"

                    if FSIP_ALLOCATION_FAILURES_TOTAL is not None:
                        FSIP_ALLOCATION_FAILURES_TOTAL.labels(fuel_type=line.fuel_type).inc()

                    updated_count += 1
                    touched_decisions.add(line.decision_id)
                    self._log.error(
                        "allocation.failed",
                        line_id=line.id,
                        sim_allocation_id=line.sim_allocation_id,
                        reason=fail_reason,
                    )

                    if self.publisher is not None:
                        await self.publisher.event(
                            type="allocation.failed",
                            data={
                                "line_id": line.id,
                                "decision_id": line.decision_id,
                                "sim_allocation_id": line.sim_allocation_id,
                                "fuel_type": line.fuel_type,
                                "reason": fail_reason,
                            },
                            sim_run_id=sim_run_id,
                            tick=cur_tick,
                        )

                elif sim_status == "CANCELLED" and line.status != "CANCELLED":
                    line.status = "CANCELLED"
                    updated_count += 1
                    touched_decisions.add(line.decision_id)

            await session.commit()

            # Check if any touched decision has reached all-terminal line state
            if touched_decisions:
                await self._evaluate_decision_outcomes(session, touched_decisions, cur_tick)
                await session.commit()

        return updated_count

    def _build_station_map(self, state: Any) -> dict[str, Any]:
        """Index stations by ID from state."""
        st_list = _get_field(state, "stations", [])
        res: dict[str, Any] = {}
        for s in st_list:
            s_id = _get_field(s, "id")
            if s_id:
                res[str(s_id)] = s
        return res

    def _calculate_received_and_overflow(
        self,
        line: DecisionLine,
        station: Any,
    ) -> tuple[float, float]:
        """Estimate received liters and suspected overflow without calling /admin/audit.

        Returns (received_liters, overflow_liters).
        """
        qty = float(line.quantity)
        if station is None:
            return qty, 0.0

        cap_dict = _get_field(station, "capacity", {})
        inv_dict = _get_field(station, "inventory", {})

        cap = _get_field(cap_dict, line.fuel_type, 0.0)
        inv = _get_field(inv_dict, line.fuel_type, 0.0)

        try:
            cap_val = float(cap)
            inv_val = float(inv)
        except (ValueError, TypeError):
            return qty, 0.0

        if cap_val <= 0.0:
            return qty, 0.0

        # Current headroom
        headroom = max(0.0, cap_val - inv_val)
        if qty > headroom:
            overflow = qty - headroom
            received = max(0.0, headroom)
            return received, overflow

        return qty, 0.0

    async def _evaluate_decision_outcomes(
        self,
        session: AsyncSession,
        decision_ids: set[int],
        cur_tick: int,
    ) -> None:
        """Create DecisionOutcome records for decisions where all lines reached terminal states."""
        stmt = (
            select(Decision)
            .where(Decision.id.in_(decision_ids))
            .options(selectinload(Decision.lines))
        )
        dec_res = await session.execute(stmt)
        decisions = dec_res.scalars().all()

        for dec in decisions:
            if not dec.lines:
                continue

            terminal_statuses = {"ARRIVED", "FAILED", "CANCELLED", "REJECTED_BY_SIM", "DEFERRED"}
            all_terminal = all(ln.status in terminal_statuses for ln in dec.lines)

            if not all_terminal:
                continue

            # Check if outcome already recorded
            existing = await session.get(DecisionOutcome, dec.id)
            if existing is not None:
                continue

            total_lines = len(dec.lines)
            arrived_lines = sum(1 for ln in dec.lines if ln.status == "ARRIVED")
            failed_lines = sum(1 for ln in dec.lines if ln.status in ("FAILED", "REJECTED_BY_SIM"))
            hit = (arrived_lines == total_lines and failed_lines == 0)

            outcome = DecisionOutcome(
                decision_id=dec.id,
                evaluated_tick=cur_tick,
                predicted={
                    "total_lines": total_lines,
                    "target_liters": sum(float(ln.quantity) for ln in dec.lines),
                },
                realised={
                    "arrived_lines": arrived_lines,
                    "failed_lines": failed_lines,
                    "received_liters": sum(float(ln.received_liters or 0.0) for ln in dec.lines),
                },
                hit=hit,
            )
            session.add(outcome)
            self._log.info(
                "decision.outcome_evaluated",
                decision_id=dec.id,
                evaluated_tick=cur_tick,
                hit=hit,
                arrived=arrived_lines,
                total=total_lines,
            )

    async def reconcile_on_startup(self, sim_client: Any) -> int:
        """Scan unfinalized allocations in database and reconcile against simulator.

        Prevents duplicate dispatches after engine restart by reconciling in-flight
        lines matching simulator allocations by idempotency_key or sim_allocation_id.
        """
        try:
            alloc_res = await sim_client.allocations()
            raw_allocs = alloc_res.data if hasattr(alloc_res, "data") else alloc_res
            if not isinstance(raw_allocs, list):
                return 0
        except Exception as exc:
            self._log.warning("reconcile_on_startup.sim_fetch_failed", error=str(exc))
            return 0

        alloc_by_key: dict[str, Any] = {}
        alloc_by_id: dict[int, Any] = {}
        for a in raw_allocs:
            key = _get_field(a, "idempotency_key")
            a_id = _get_field(a, "id")
            if key:
                alloc_by_key[str(key)] = a
            if a_id is not None:
                try:
                    alloc_by_id[int(a_id)] = a
                except (ValueError, TypeError):
                    pass

        reconciled = 0
        async with self.session_factory() as session:
            stmt = select(DecisionLine).where(
                DecisionLine.status.in_(["SUBMITTED", "IN_TRANSIT", "PENDING"])
            )
            res = await session.execute(stmt)
            lines = res.scalars().all()
            if not lines:
                return 0

            for line in lines:
                match = alloc_by_key.get(line.idempotency_key)
                if not match and line.sim_allocation_id:
                    match = alloc_by_id.get(line.sim_allocation_id)

                if match:
                    sim_status = str(_get_field(match, "status", "")).upper()
                    sim_id = _get_field(match, "id")
                    arr_tick = _get_field(match, "actual_arrival_tick")
                    exp_arr = _get_field(match, "expected_arrival_tick")

                    if sim_id and line.sim_allocation_id is None:
                        line.sim_allocation_id = int(sim_id)
                    if exp_arr is not None:
                        line.expected_arrival_tick = int(exp_arr)

                    if sim_status in ("IN_TRANSIT", "ARRIVED", "FAILED", "CANCELLED"):
                        if line.status != sim_status:
                            line.status = sim_status
                            if sim_status == "ARRIVED" and arr_tick is not None:
                                line.actual_arrival_tick = int(arr_tick)
                                line.received_liters = float(_get_field(match, "quantity", line.quantity))
                            reconciled += 1

            if reconciled > 0:
                await session.commit()
                self._log.info("reconcile_on_startup.reconciled", count=reconciled)

        return reconciled

