"""Decision Engine and Approval Policy module for Phase 04.

Follows 05_INTELLIGENCE_DESIGN.md §4.4, §4.5 and 23_MODULE_SPECS.md B15.
Manages:
- Planning cycle execution (ENGINE_DECISION_INTERVAL_TICKS)
- Heuristic and optimizer plan generation
- Decision lifecycle (PENDING_APPROVAL -> APPROVED / EXECUTED / REJECTED / EXPIRED)
- Autopilot truth-table evaluation
- Idempotency key generation: fsip-{sim_run_id}-{decision_id}-{line_no}
- Superseding older pending decisions on new plan creation
- Domain event emission to Redis stream
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from fsip_common.logging import get_logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import EngineSettings, get_settings
from app.db.models import Decision, DecisionLine
from app.decision.constraints import PlanLine, check_line
from app.decision.heuristic import plan_heuristic

if TYPE_CHECKING:
    from app.decision.executor import Executor
    from app.state.publisher import Publisher
    from app.state.store import WorldState


class DecisionError(Exception):
    """Base error for decision operations."""

    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class DecisionNotFoundError(DecisionError):
    def __init__(self, decision_id: int) -> None:
        super().__init__("DECISION_NOT_FOUND", f"Decision {decision_id} not found", status_code=404)


class DecisionExpiredError(DecisionError):
    def __init__(self, decision_id: int, message: str = "Decision has expired") -> None:
        super().__init__("RECOMMENDATION_EXPIRED", message, status_code=409)


class DecisionInvalidStateError(DecisionError):
    def __init__(self, current_status: str, required_status: str = "PENDING_APPROVAL") -> None:
        super().__init__(
            "INVALID_DECISION_STATE",
            f"Decision in status '{current_status}' cannot be transitioned (expected '{required_status}')",
            status_code=409,
        )


class DecisionConstraintViolationError(DecisionError):
    def __init__(self, message: str) -> None:
        super().__init__("CONSTRAINT_VIOLATION", message, status_code=422)


class DecisionEngine:
    """Core decision engine coordinating planning, policy, approvals, and execution."""

    def __init__(
        self,
        session_factory: Any,
        publisher: Publisher | None = None,
        settings: EngineSettings | None = None,
        executor: Executor | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.publisher = publisher
        self.settings = settings or get_settings()
        self.executor = executor
        self.last_plan_tick: int = -1
        self._log = get_logger()

    async def on_cycle(
        self,
        store: WorldState,
        risk: Any = None,
        fb: Any = None,
        force_plan: bool = False,
    ) -> Decision | None:
        """Execute one decision cycle.

        Called on simulator synchronisation cycles.
        """
        cur_tick = getattr(store, "tick", 0)
        interval = self.settings.engine_decision_interval_ticks

        if not force_plan and self.last_plan_tick >= 0 and (cur_tick - self.last_plan_tick) < interval:
            # Check expiry of older decisions even when not planning
            await self.expire_stale_decisions(cur_tick, store.sim_run_id)
            return None

        decision = await self._build_and_persist_decision(store, cur_tick)
        if decision:
            self.last_plan_tick = cur_tick

            # If autopilot auto-approved, trigger execution immediately
            if decision.status == "APPROVED" and self.executor is not None:
                try:
                    await self.executor.execute(decision, list(decision.lines))
                except Exception as exc:
                    self._log.error("autopilot.execute_failed", decision_id=decision.id, error=str(exc))

        return decision

    async def plan_now(self, store: WorldState) -> Decision | None:
        """Trigger an immediate planning cycle, bypassing interval check."""
        return await self.on_cycle(store, force_plan=True)

    async def _build_and_persist_decision(
        self,
        store: WorldState,
        cur_tick: int,
    ) -> Decision | None:
        """Run policy, evaluate approval conditions, and persist the new decision."""
        # 1. Generate plan using configured policy
        # In Phase 04, heuristic allocator is the primary engine policy
        plan = plan_heuristic(
            state=store,
            target_fill=self.settings.station_target_fill,
        )

        if not plan.lines:
            # Nothing to recommend: expire any past-due decisions and return
            await self.expire_stale_decisions(cur_tick, store.sim_run_id)
            return None

        # 2. Assign confidence: 0.9 normal, 0.5 degraded
        is_degraded = bool(getattr(store, "degraded", False))
        is_stale = bool(getattr(store, "stale", False))
        confidence = 0.5 if (is_degraded or is_stale) else 0.9

        # 3. Expiry tick = cur_tick + 2
        expires_at_tick = cur_tick + 2

        # 4. Evaluate approval policy (Autopilot truth table)
        mode = getattr(store, "engine_mode", self.settings.engine_mode) or "advisory"
        is_autopilot = (mode.lower() == "autopilot")

        auto_eligible = (
            is_autopilot
            and confidence >= self.settings.auto_approve_min_confidence
            and all(line.quantity <= self.settings.auto_approve_max_liters for line in plan.lines)
            and not is_stale
            and not is_degraded
        )

        initial_status = "APPROVED" if auto_eligible else "PENDING_APPROVAL"
        approved_by = "autopilot" if auto_eligible else None
        approved_at = datetime.now(UTC) if auto_eligible else None

        # 5. Persist decision and lines atomically
        async with self.session_factory() as session:
            # Supersede older PENDING_APPROVAL decisions for this run
            await self._supersede_older_decisions(session, store.sim_run_id, cur_tick)

            dec = Decision(
                sim_run_id=store.sim_run_id,
                tick=cur_tick,
                policy=plan.policy,
                policy_version="1.0.0",
                trigger="interval" if not self.last_plan_tick else "auto",
                status=initial_status,
                mode=mode,
                confidence=confidence,
                degraded=is_degraded or is_stale,
                fallback_reason="State degraded" if (is_degraded or is_stale) else None,
                explanation={"reasons": plan.reasons, "solver_status": plan.solver_status},
                approved_by=approved_by,
                approved_at=approved_at,
                expires_at_tick=expires_at_tick,
                created_at=datetime.now(UTC),
            )
            session.add(dec)
            await session.flush()

            # Create lines with formatted idempotency keys
            db_lines: list[DecisionLine] = []
            routes_map = getattr(store, "routes", {})
            for idx, pl in enumerate(plan.lines):
                line_no = idx + 1
                idem_key = f"fsip-{store.sim_run_id or 0}-{dec.id}-{line_no}"
                route_obj = routes_map.get(pl.route_id) if isinstance(routes_map, dict) else None
                transit = int(getattr(route_obj, "transit_ticks", 2)) if route_obj else 2
                exp_arrival = cur_tick + transit

                d_line = DecisionLine(
                    decision_id=dec.id,
                    line_no=line_no,
                    source_depot_id=pl.source_depot_id,
                    destination_station_id=pl.destination_station_id,
                    route_id=pl.route_id,
                    fuel_type=pl.fuel_type,
                    quantity=pl.quantity,
                    idempotency_key=idem_key,
                    status="PLANNED",
                    expected_arrival_tick=exp_arrival,
                    reason=pl.reason,
                    created_at=datetime.now(UTC),
                )
                session.add(d_line)
                db_lines.append(d_line)

            await session.commit()
            await session.refresh(dec, ["lines"])

        # 6. Publish Redis domain event
        if self.publisher is not None:
            await self.publisher.event(
                type="decision.created",
                data={
                    "decision_id": dec.id,
                    "tick": cur_tick,
                    "status": dec.status,
                    "mode": dec.mode,
                    "policy": dec.policy,
                    "confidence": dec.confidence,
                    "lines_count": len(db_lines),
                    "approved_by": dec.approved_by,
                },
                sim_run_id=store.sim_run_id,
                tick=cur_tick,
            )

        self._log.info(
            "decision.created",
            decision_id=dec.id,
            tick=cur_tick,
            status=dec.status,
            lines_count=len(db_lines),
        )
        return dec

    async def _supersede_older_decisions(
        self,
        session: AsyncSession,
        sim_run_id: int | None,
        cur_tick: int,
    ) -> list[int]:
        """Mark older PENDING_APPROVAL decisions as EXPIRED."""
        stmt = (
            select(Decision)
            .where(
                Decision.sim_run_id == sim_run_id,
                Decision.status == "PENDING_APPROVAL",
            )
            .with_for_update()
        )
        res = await session.execute(stmt)
        older = list(res.scalars().all())
        superseded_ids: list[int] = []

        for d in older:
            d.status = "EXPIRED"
            d.rejected_reason = f"Superseded by fresh plan at tick {cur_tick}"
            superseded_ids.append(d.id)

        if superseded_ids:
            await session.flush()
            if self.publisher is not None:
                for d_id in superseded_ids:
                    await self.publisher.event(
                        type="decision.expired",
                        data={"decision_id": d_id, "reason": "superseded"},
                        sim_run_id=sim_run_id,
                        tick=cur_tick,
                    )
        return superseded_ids

    async def expire_stale_decisions(
        self,
        current_tick: int,
        sim_run_id: int | None = None,
    ) -> list[int]:
        """Expire decisions where current_tick >= expires_at_tick."""
        expired_ids: list[int] = []
        async with self.session_factory() as session:
            stmt = select(Decision).where(
                Decision.status == "PENDING_APPROVAL",
                Decision.expires_at_tick <= current_tick,
            )
            if sim_run_id is not None:
                stmt = stmt.where(Decision.sim_run_id == sim_run_id)

            res = await session.execute(stmt.with_for_update())
            stale_decisions = list(res.scalars().all())

            for d in stale_decisions:
                d.status = "EXPIRED"
                d.rejected_reason = f"Expired at tick {current_tick} (limit was {d.expires_at_tick})"
                expired_ids.append(d.id)

            if expired_ids:
                await session.commit()
                if self.publisher is not None:
                    for d_id in expired_ids:
                        await self.publisher.event(
                            type="decision.expired",
                            data={"decision_id": d_id, "reason": "ttl_expired"},
                            sim_run_id=sim_run_id,
                            tick=current_tick,
                        )

        return expired_ids

    async def approve_decision(
        self,
        decision_id: int,
        actor: str = "operator",
        selected_line_ids: Sequence[int] | None = None,
        modified_quantities: dict[int, float] | None = None,
        current_state: WorldState | None = None,
    ) -> Decision:
        """Approve a pending decision, optionally selecting or modifying line quantities."""
        async with self.session_factory() as session:
            stmt = (
                select(Decision)
                .options(selectinload(Decision.lines))
                .where(Decision.id == decision_id)
                .with_for_update()
            )
            res = await session.execute(stmt)
            dec = res.scalar_one_or_none()

            if not dec:
                raise DecisionNotFoundError(decision_id)

            cur_tick = getattr(current_state, "tick", dec.tick) if current_state else dec.tick

            # Expiry check
            if dec.expires_at_tick is not None and cur_tick >= dec.expires_at_tick:
                dec.status = "EXPIRED"
                await session.commit()
                raise DecisionExpiredError(
                    decision_id,
                    f"Decision {decision_id} expired at tick {dec.expires_at_tick} (current tick {cur_tick})",
                )

            if dec.status != "PENDING_APPROVAL":
                raise DecisionInvalidStateError(dec.status, "PENDING_APPROVAL")

            # Check stale state before approving
            if current_state and (getattr(current_state, "stale", False) or getattr(current_state, "degraded", False)):
                raise DecisionError(
                    "STALE_STATE",
                    "Cannot approve decision against stale or degraded state",
                    status_code=409,
                )

            # Apply quantity modifications and validations
            mod_qtys = modified_quantities or {}
            sel_ids = set(selected_line_ids) if selected_line_ids is not None else {ln.id for ln in dec.lines}

            approved_lines: list[DecisionLine] = []
            used_disp: dict[str, float] = {}

            for line in dec.lines:
                if line.id not in sel_ids:
                    line.status = "CANCELLED"
                    continue

                if line.id in mod_qtys:
                    new_q = float(mod_qtys[line.id])
                    line.quantity = new_q

                # If current_state is provided, validate each approved line against constraints
                if current_state:
                    pl = PlanLine(
                        route_id=line.route_id,
                        source_depot_id=line.source_depot_id,
                        destination_station_id=line.destination_station_id,
                        fuel_type=line.fuel_type,
                        quantity=line.quantity,
                    )
                    violations = check_line(
                        state=current_state,
                        line=pl,
                        used_dispatch=used_disp,
                        for_execution=True,
                    )
                    if violations:
                        violation_msgs = "; ".join(f"[{v.rule}] {v.message}" for v in violations)
                        raise DecisionConstraintViolationError(
                            f"Line {line.line_no} violated constraints: {violation_msgs}"
                        )
                    used_disp[line.source_depot_id] = used_disp.get(line.source_depot_id, 0.0) + line.quantity

                approved_lines.append(line)

            dec.status = "APPROVED"
            dec.approved_by = actor
            dec.approved_at = datetime.now(UTC)

            await session.commit()
            await session.refresh(dec, ["lines"])

        # Publish approval event
        if self.publisher is not None:
            await self.publisher.event(
                type="decision.approved",
                data={
                    "decision_id": dec.id,
                    "approved_by": actor,
                    "lines_approved": len(approved_lines),
                },
                sim_run_id=dec.sim_run_id,
                tick=cur_tick,
            )

        # Trigger execution if executor is attached
        if self.executor is not None and approved_lines:
            try:
                await self.executor.execute(dec, approved_lines)
            except Exception as exc:
                self._log.error("executor.execute_failed", decision_id=dec.id, error=str(exc))

        return dec

    async def reject_decision(
        self,
        decision_id: int,
        actor: str = "operator",
        reason: str = "Rejected by operator",
    ) -> Decision:
        """Reject a pending decision."""
        async with self.session_factory() as session:
            stmt = (
                select(Decision)
                .options(selectinload(Decision.lines))
                .where(Decision.id == decision_id)
                .with_for_update()
            )
            res = await session.execute(stmt)
            dec = res.scalar_one_or_none()

            if not dec:
                raise DecisionNotFoundError(decision_id)

            if dec.status != "PENDING_APPROVAL":
                raise DecisionInvalidStateError(dec.status, "PENDING_APPROVAL")

            dec.status = "REJECTED"
            dec.approved_by = actor
            dec.rejected_reason = reason

            for line in dec.lines:
                line.status = "CANCELLED"

            await session.commit()
            await session.refresh(dec, ["lines"])

        if self.publisher is not None:
            await self.publisher.event(
                type="decision.rejected",
                data={
                    "decision_id": dec.id,
                    "rejected_by": actor,
                    "reason": reason,
                },
                sim_run_id=dec.sim_run_id,
                tick=dec.tick,
            )

        return dec
