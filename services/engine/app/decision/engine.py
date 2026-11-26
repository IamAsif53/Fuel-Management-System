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

import asyncio
import math
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from fsip_common.logging import get_logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import EngineSettings, get_settings
from app.db.models import AuditLog, Decision, DecisionLine, SimRun
from app.db.repo import ledger_append
from app.decision.constraints import PlanLine, check_line
from app.decision.heuristic import plan_heuristic
from app.decision.optimizer import OptimizerPolicy
from app.decision.twin import ShadowTwin
from app.explain.explainer import build_explanation, generate_decision_narrative
from app.explain.llm import FSIP_RECOMMENDATION_TIME_TO_DECISION, LLMExplainer

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
    def __init__(self, message: str, violations: list[Any] | None = None) -> None:
        super().__init__("CONSTRAINT_VIOLATION", message, status_code=422)
        self.violations: list[Any] = list(violations or [])


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
        self.optimizer = OptimizerPolicy()
        self.last_plan_tick: int = -1
        self._log = get_logger()
        self.llm = LLMExplainer(
            api_key=getattr(self.settings, "anthropic_api_key", None),
            model=getattr(self.settings, "llm_model", "claude-haiku-5-5"),
            timeout_sec=getattr(self.settings, "llm_timeout_sec", 8.0),
        )

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
        engine_mode = getattr(store, "engine_mode", "NORMAL")
        if engine_mode in ("SAFE_HOLD", "NO_FEASIBLE_ROUTES"):
            self._log.info("decision.planning_paused", engine_mode=engine_mode, tick=cur_tick)
            await self.expire_stale_decisions(cur_tick, store.sim_run_id)
            await self._sync_store_pending(store, cur_tick)
            return None

        interval = self.settings.engine_decision_interval_ticks

        if not force_plan and self.last_plan_tick >= 0 and (cur_tick - self.last_plan_tick) < interval:
            # Check expiry of older decisions even when not planning
            await self.expire_stale_decisions(cur_tick, store.sim_run_id)
            # Evaluate existing pending decisions if in autopilot mode
            if str(getattr(store, "operating_mode", "advisory")).lower() == "autopilot":
                try:
                    await self.evaluate_autopilot_pending(store)
                except Exception as exc:
                    self._log.warning("autopilot.cycle_eval_failed", error=str(exc))
            await self._sync_store_pending(store, cur_tick)
            return None

        # Check expiry of older decisions
        await self.expire_stale_decisions(cur_tick, store.sim_run_id)
        # Evaluate existing pending decisions if in autopilot mode
        if str(getattr(store, "operating_mode", "advisory")).lower() == "autopilot":
            try:
                await self.evaluate_autopilot_pending(store)
            except Exception as exc:
                self._log.warning("autopilot.cycle_eval_failed", error=str(exc))

        decision = await self._build_and_persist_decision(store, cur_tick)
        if decision:
            self.last_plan_tick = cur_tick

            # If autopilot auto-approved, trigger execution immediately
            if decision.status == "APPROVED" and self.executor is not None:
                try:
                    await self.executor.execute(decision, list(decision.lines))
                except Exception as exc:
                    self._log.error("autopilot.execute_failed", decision_id=decision.id, error=str(exc))

        await self._sync_store_pending(store, cur_tick)
        return decision

    async def plan_now(self, store: WorldState) -> Decision | None:
        """Trigger an immediate planning cycle, bypassing interval check."""
        return await self.on_cycle(store, force_plan=True)

    def check_autopilot_eligibility(
        self,
        store: WorldState,
        lines: list[Any],
        confidence: float,
    ) -> tuple[bool, list[str]]:
        """Evaluate locked Autopilot eligibility criteria (20_FINAL_SPEC_LOCK.md & 05_INTELLIGENCE_DESIGN.md §4.5).

        Conditions:
        1. operating_mode == 'autopilot'
        2. confidence >= 0.7
        3. every allocation line <= 7,000 L
        4. total decision volume <= 7,000 L (volumes > 7,000 L require four-eyes supervisory approval)
        5. data must not be stale (store.stale == False)
        6. not degraded (store.degraded == False)
        7. no CRITICAL data-quality alert active
        8. engine mode must be NORMAL
        9. domain SLO error budget remaining >= 25%
        10. simulator service available

        Returns:
            (is_eligible, blocked_reasons)
        """
        mode = getattr(store, "operating_mode", None)
        if mode is None:
            raw_mode = getattr(store, "engine_mode", self.settings.engine_mode)
            if str(raw_mode).lower() in ("advisory", "autopilot"):
                mode = raw_mode
            else:
                mode = self.settings.engine_mode or "advisory"
        is_autopilot = (str(mode).lower() == "autopilot")

        raw_engine_mode = getattr(store, "engine_mode", "NORMAL")
        if str(raw_engine_mode).lower() in ("advisory", "autopilot"):
            resilience_mode = "NORMAL"
        else:
            resilience_mode = str(raw_engine_mode).upper()

        is_stale = bool(getattr(store, "stale", False))
        is_degraded = bool(getattr(store, "degraded", False))
        degraded_reasons = set(getattr(store, "degraded_reasons", set()))

        # Domain SLO Error Budget enforcement: Target 99.0% (allowable unreliability 1.0%)
        # Error budget remaining = (service_level - 0.99) / 0.01
        sl = getattr(getattr(store, "metrics", None), "service_level", 1.0)
        domain_slo_budget = (float(sl) - 0.99) / 0.01 if sl is not None else 1.0
        slo_exhausted = domain_slo_budget < 0.25

        if slo_exhausted:
            if hasattr(store, "degraded_reasons"):
                store.degraded_reasons.add("error_budget_exhausted")
        else:
            if hasattr(store, "degraded_reasons"):
                store.degraded_reasons.discard("error_budget_exhausted")

        # Check critical data quality alert
        has_critical_dq = (
            bool(getattr(store, "has_critical_data_quality_alert", False))
            or "invalid_simulator_data" in degraded_reasons
            or "data_quality_critical" in degraded_reasons
        )
        active_alerts = getattr(store, "active_alerts", None) or getattr(store, "alerts", None)
        if isinstance(active_alerts, dict):
            for alt in active_alerts.values():
                cat = getattr(alt, "category", None) or (alt.get("category") if isinstance(alt, dict) else None)
                sev = getattr(alt, "severity", None) or (alt.get("severity") if isinstance(alt, dict) else None)
                if cat == "DATA_QUALITY" and sev == "CRITICAL":
                    has_critical_dq = True
                    break
        elif isinstance(active_alerts, list):
            for alt in active_alerts:
                cat = getattr(alt, "category", None) or (alt.get("category") if isinstance(alt, dict) else None)
                sev = getattr(alt, "severity", None) or (alt.get("severity") if isinstance(alt, dict) else None)
                if cat == "DATA_QUALITY" and sev == "CRITICAL":
                    has_critical_dq = True
                    break

        total_qty = sum(float(getattr(ln, "quantity", 0.0)) for ln in lines)
        max_line_qty = max((float(getattr(ln, "quantity", 0.0)) for ln in lines), default=0.0)

        blocked_reasons: list[str] = []
        if not is_autopilot:
            blocked_reasons.append("Operating mode is ADVISORY")
        if confidence < self.settings.auto_approve_min_confidence:
            min_c = self.settings.auto_approve_min_confidence
            blocked_reasons.append(f"Confidence score {confidence:.2f} is below required {min_c:.2f} threshold")
        if max_line_qty > self.settings.auto_approve_max_liters:
            blocked_reasons.append(
                f"Allocation line ({max_line_qty:,.0f} L) exceeds 7,000 L maximum autonomous ceiling"
            )
        if total_qty > 7000.0:
            blocked_reasons.append(
                f"Decision volume ({total_qty:,.0f} L) exceeds 7,000 L threshold (requires four-eyes signoff)"
            )
        if is_stale:
            blocked_reasons.append("Telemetry data is stale")
        if is_degraded:
            reasons_str = f" ({', '.join(sorted(degraded_reasons))})" if degraded_reasons else ""
            blocked_reasons.append(f"System operating in degraded state{reasons_str}")
        if has_critical_dq:
            blocked_reasons.append("Active CRITICAL data-quality alert detected")
        if resilience_mode != "NORMAL":
            blocked_reasons.append(f"Engine resilience mode is {resilience_mode} (must be NORMAL)")
        if slo_exhausted:
            blocked_reasons.append(
                f"Domain SLO error budget exhausted ({domain_slo_budget * 100:.1f}% remaining, minimum 25% required)"
            )
        if "sim_unavailable" in degraded_reasons or "simulator_unavailable" in degraded_reasons:
            blocked_reasons.append("Simulator service is unavailable")

        is_eligible = len(blocked_reasons) == 0
        return is_eligible, blocked_reasons

    async def evaluate_autopilot_pending(self, store: WorldState) -> int:
        """Evaluate active PENDING_APPROVAL decisions against the Autopilot eligibility gate.

        If eligible, automatically approves and executes them.
        If ineligible, updates blocked reasons in explanation.
        Returns the count of auto-approved decisions.
        """
        cur_tick = getattr(store, "tick", 0)
        sim_run_id = getattr(store, "sim_run_id", None)
        if sim_run_id is None:
            return 0

        approved_count = 0
        to_execute: list[tuple[Decision, list[DecisionLine]]] = []

        async with self.session_factory() as session:
            stmt = (
                select(Decision)
                .options(selectinload(Decision.lines))
                .where(
                    Decision.status == "PENDING_APPROVAL",
                    Decision.sim_run_id == sim_run_id,
                )
                .order_by(Decision.id.asc())
            )
            res = await session.execute(stmt)
            pending = list(res.scalars().all())

            for dec in pending:
                if dec.expires_at_tick is not None and cur_tick >= dec.expires_at_tick:
                    dec.status = "EXPIRED"
                    continue

                conf = dec.confidence if dec.confidence is not None else 0.5
                is_eligible, blocked_reasons = self.check_autopilot_eligibility(store, list(dec.lines), conf)

                expl = dict(dec.explanation or {})
                total_liters = sum(float(ln.quantity) for ln in dec.lines)
                expl["autopilot_eligibility"] = {
                    "eligible": is_eligible,
                    "blocked_reasons": blocked_reasons,
                    "requires_four_eyes": total_liters > 7000.0,
                }
                if blocked_reasons:
                    expl["autopilot_blocked_reason"] = "; ".join(blocked_reasons)
                else:
                    expl.pop("autopilot_blocked_reason", None)
                dec.explanation = expl

                if is_eligible:
                    dec.status = "APPROVED"
                    dec.approved_by = "autopilot"
                    dec.approved_at = datetime.now(UTC)

                    audit_entry = AuditLog(
                        actor="autopilot",
                        action="DECISION_AUTO_APPROVED",
                        entity_type="decision",
                        entity_id=str(dec.id),
                        payload={
                            "decision_id": dec.id,
                            "tick": cur_tick,
                            "policy": dec.policy,
                            "confidence": dec.confidence,
                            "lines_count": len(dec.lines),
                            "total_liters": total_liters,
                        },
                    )
                    session.add(audit_entry)
                    approved_count += 1
                    to_execute.append((dec, list(dec.lines)))

            await session.commit()

        # If any were approved, execute them via executor
        for approved_dec, approved_lines in to_execute:
            if self.executor is not None:
                try:
                    await self.executor.execute(approved_dec, approved_lines)
                except Exception as exc:
                    self._log.error("autopilot.execute_pending_failed", decision_id=approved_dec.id, error=str(exc))

        return approved_count

    async def _build_and_persist_decision(
        self,
        store: WorldState,
        cur_tick: int,
    ) -> Decision | None:
        """Run policy, evaluate approval conditions, and persist the new decision."""
        # 1. Generate plan using configured / runtime policy
        active_policy = getattr(store, "policy", "optimizer") or "optimizer"
        is_degraded = bool(getattr(store, "degraded", False))
        is_stale = bool(getattr(store, "stale", False))

        if active_policy.lower() == "optimizer":
            opt_plan = self.optimizer.solve(store)
            plan_lines = opt_plan.lines
            policy_name = opt_plan.policy_name
            policy_version = opt_plan.policy_version
            is_fallback = opt_plan.is_fallback
            fallback_reason = opt_plan.fallback_reason
            solver_status = opt_plan.solver_status
            explanation_payload = {"solver_status": solver_status, "fallback_reason": fallback_reason}
            has_critical_risk = False
        else:
            h_plan = plan_heuristic(
                state=store,
                target_fill=self.settings.station_target_fill,
            )
            plan_lines = h_plan.lines
            policy_name = "heuristic"
            policy_version = "heuristic@1.0.0"
            is_fallback = False
            fallback_reason = None
            solver_status = "Heuristic"
            explanation_payload = {"reasons": h_plan.reasons, "solver_status": h_plan.solver_status}
            has_critical_risk = getattr(h_plan, "has_critical_risk", False)

        if not plan_lines:
            # Nothing to recommend: expire any past-due decisions and return
            await self.expire_stale_decisions(cur_tick, store.sim_run_id)
            return None

        # 2. Evaluate impact via ShadowTwin
        twin_dn = ShadowTwin.project(state=store, lines=[], horizon=48, n_samples=30)
        twin_rec = ShadowTwin.project(state=store, lines=plan_lines, horizon=48, n_samples=30)

        impact_stations: dict[str, Any] = {}
        for pl in plan_lines:
            key = (pl.destination_station_id, pl.fuel_type)
            b_p = twin_dn.stations.get(key)
            a_p = twin_rec.stations.get(key)
            if b_p and a_p:
                impact_stations[f"{pl.destination_station_id}:{pl.fuel_type}"] = {
                    "stockout_prob_before": round(b_p.stockout_prob, 2),
                    "stockout_prob_after": round(a_p.stockout_prob, 2),
                    "unmet_before": round(b_p.unmet_liters, 1),
                    "unmet_after": round(a_p.unmet_liters, 1),
                }

        impact_data: dict[str, Any] = {
            "unmet_before": round(twin_dn.total_unmet_liters, 1),
            "unmet_after": round(twin_rec.total_unmet_liters, 1),
            "overflow_before": round(twin_dn.total_overflow_liters, 1),
            "overflow_after": round(twin_rec.total_overflow_liters, 1),
            "stations": impact_stations,
        }

        # 3. Assign confidence using exact formula (Phase 08 Task 8.5 & 05 §4.4):
        if active_policy == "heuristic":
            confidence = 0.5 if (is_degraded or is_stale) else 0.9
        else:
            # confidence = 0.4*forecast_quality + 0.2*data_freshness + 0.2*solver_status + 0.2*(1 - twin_uncertainty)
            fq = 1.0
            freshness = 0.0 if (is_degraded or is_stale) else 1.0
            solver_score = 1.0 if (not is_fallback and solver_status == "Optimal") else 0.3
            uncertainty = 0.05
            confidence = round(0.4 * fq + 0.2 * freshness + 0.2 * solver_score + 0.2 * (1.0 - uncertainty), 2)

        # 4. Expiry tick = cur_tick + 2
        expires_at_tick = cur_tick + 2

        # 5. Evaluate approval policy (Autopilot truth table)
        mode = getattr(store, "operating_mode", getattr(store, "engine_mode", self.settings.engine_mode)) or "advisory"
        auto_eligible, blocked_reasons = self.check_autopilot_eligibility(store, plan_lines, confidence)

        initial_status = "APPROVED" if auto_eligible else "PENDING_APPROVAL"
        approved_by = "autopilot" if auto_eligible else None
        approved_at = datetime.now(UTC) if auto_eligible else None

        # 5. Persist decision and lines atomically
        async with self.session_factory() as session:
            # Ensure simulation run row exists before inserting referencing decision
            if store.sim_run_id is not None:
                run_exists = await session.scalar(
                    select(SimRun.id).where(SimRun.id == store.sim_run_id)
                )
                if not run_exists:
                    self._log.warning(
                        "decision.skipped_uncommitted_run",
                        sim_run_id=store.sim_run_id,
                        tick=cur_tick,
                    )
                    return None
            else:
                return None

            # Supersede older PENDING_APPROVAL decisions for this run
            await self._supersede_older_decisions(session, store.sim_run_id, cur_tick)

            # Build structured explanation and narrative matching 05 §7.1
            decision_meta = {
                "lines": plan_lines,
                "tick": cur_tick,
                "policy": policy_name,
                "confidence": confidence,
                "degraded": is_degraded or is_stale,
            }
            structured_explanation = build_explanation(
                decision=decision_meta,
                state=store,
                risk=getattr(store, "risk", {}),
                impact=impact_data,
                plan_meta=explanation_payload,
            )
            sl = getattr(getattr(store, "metrics", None), "service_level", 1.0)
            domain_slo_budget = (float(sl) - 0.99) / 0.01 if sl is not None else 1.0
            slo_exhausted = domain_slo_budget < 0.25

            if slo_exhausted:
                structured_explanation["warning"] = (
                    "Autopilot frozen by error-budget policy (domain SLO remaining < 25%)"
                )
                structured_explanation["domain_slo_budget_remaining"] = round(domain_slo_budget, 4)

            total_plan_liters = sum(float(line.quantity) for line in plan_lines)
            structured_explanation["autopilot_eligibility"] = {
                "eligible": auto_eligible,
                "blocked_reasons": blocked_reasons,
                "requires_four_eyes": total_plan_liters > 7000.0,
            }
            if blocked_reasons and str(mode).lower() == "autopilot":
                structured_explanation["autopilot_blocked_reason"] = "; ".join(blocked_reasons)

            initial_narrative = generate_decision_narrative(structured_explanation, tick=cur_tick)
            if slo_exhausted:
                initial_narrative += " Warning: Autopilot frozen by error-budget policy (domain SLO remaining < 25%)."

            dec = Decision(
                sim_run_id=store.sim_run_id,
                tick=cur_tick,
                policy=policy_name,
                policy_version=policy_version,
                trigger=(
                    "alert"
                    if has_critical_risk
                    else ("interval" if not self.last_plan_tick else "auto")
                ),
                status=initial_status,
                mode=mode,
                confidence=confidence,
                degraded=is_degraded or is_stale,
                fallback_reason=fallback_reason or ("State degraded" if (is_degraded or is_stale) else None),
                explanation=structured_explanation,
                narrative=initial_narrative,
                impact=impact_data,
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
            for idx, pl in enumerate(plan_lines):
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

            if auto_eligible:
                audit_entry = AuditLog(
                    actor="autopilot",
                    action="DECISION_AUTO_APPROVED",
                    entity_type="decision",
                    entity_id=str(dec.id),
                    payload={
                        "decision_id": dec.id,
                        "tick": cur_tick,
                        "policy": policy_name,
                        "confidence": confidence,
                        "lines_count": len(db_lines),
                        "total_liters": total_plan_liters,
                    },
                )
                session.add(audit_entry)

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

        # 6b. Asynchronously enhance narrative with LLM if enabled (Phase 09 §4.3)
        if getattr(self, "llm", None) and self.llm.enabled:
            asyncio.create_task(
                self._enhance_narrative_with_llm(dec.id, structured_explanation, cur_tick)
            )

        # 7. If auto-approved, execute immediately
        if auto_eligible and self.executor is not None:
            await self.executor.execute(dec, db_lines)

        return dec

    async def _enhance_narrative_with_llm(
        self,
        decision_id: int,
        explanation: dict[str, Any],
        tick: int,
    ) -> None:
        """Asynchronously update decision narrative if LLM generates a valid narrative."""
        try:
            new_narrative = await self.llm.explain_decision(explanation, tick=tick)
            if not new_narrative:
                return
            async with self.session_factory() as session:
                dec = await session.get(Decision, decision_id)
                if dec and dec.narrative != new_narrative:
                    dec.narrative = new_narrative
                    await session.commit()
                    if self.publisher is not None:
                        await self.publisher.event(
                            type="decision.narrative_updated",
                            data={
                                "decision_id": decision_id,
                                "narrative": new_narrative,
                            },
                            sim_run_id=dec.sim_run_id,
                            tick=tick,
                        )
                    self._log.info("decision.narrative_enhanced_with_llm", decision_id=decision_id)
        except Exception as exc:
            self._log.warning("decision.llm_narrative_failed", decision_id=decision_id, error=str(exc))

    async def _supersede_older_decisions(
        self,
        session: AsyncSession,
        sim_run_id: int | None,
        cur_tick: int,
    ) -> list[int]:
        """Mark older unfinalized decisions as EXPIRED."""
        stmt = (
            select(Decision)
            .where(
                Decision.sim_run_id == sim_run_id,
                Decision.status.in_(["PENDING_APPROVAL", "AWAITING_SECOND_APPROVAL", "PROPOSED"]),
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

    async def _sync_store_pending(self, store: WorldState, current_tick: int) -> None:
        """Keep store.pending synchronized with active pending decisions for store.sim_run_id."""
        if store is None:
            return
        try:
            async with self.session_factory() as session:
                unfinalized_statuses = ["PENDING_APPROVAL", "AWAITING_SECOND_APPROVAL", "PROPOSED"]
                stmt = (
                    select(Decision)
                    .options(selectinload(Decision.lines))
                    .where(
                        Decision.sim_run_id == store.sim_run_id,
                        Decision.status.in_(unfinalized_statuses),
                        (Decision.expires_at_tick > current_tick) | (Decision.expires_at_tick.is_(None)),
                    )
                    .order_by(Decision.id.desc())
                )
                res = await session.execute(stmt)
                active = list(res.scalars().all())
                store.pending = [
                    {
                        "decision_id": d.id,
                        "summary": d.narrative or f"Decision #{d.id} at tick {d.tick}",
                        "confidence": float(d.confidence) if d.confidence is not None else 0.8,
                        "total_liters": sum(float(ln.quantity) for ln in getattr(d, "lines", [])),
                        "expires_at_tick": d.expires_at_tick,
                    }
                    for d in active
                ]
            if self.publisher is not None and hasattr(self.publisher, "publish_state"):
                await self.publisher.publish_state(store)
        except Exception as exc:
            self._log.warning("decision.sync_store_pending_failed", error=str(exc))

    async def expire_stale_decisions(
        self,
        current_tick: int,
        sim_run_id: int | None = None,
    ) -> list[int]:
        """Expire decisions where current_tick >= expires_at_tick or from older ended runs."""
        expired_ids: list[int] = []
        unfinalized_statuses = ["PENDING_APPROVAL", "AWAITING_SECOND_APPROVAL", "PROPOSED"]
        async with self.session_factory() as session:
            # 1. Expire past-due decisions for the current run
            stmt = select(Decision).where(
                Decision.status.in_(unfinalized_statuses),
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

            # 2. Expire orphaned decisions only from other runs that have ended.
            # Do not expire pending decisions belonging to still-open parallel runs
            # (e.g. integration tests sharing the DB with a live engine).
            if sim_run_id is not None:
                old_stmt = (
                    select(Decision)
                    .join(SimRun, SimRun.id == Decision.sim_run_id)
                    .where(
                        Decision.status.in_(unfinalized_statuses),
                        Decision.sim_run_id != sim_run_id,
                        SimRun.ended_at.is_not(None),
                    )
                )
                old_res = await session.execute(old_stmt.with_for_update())
                for d in old_res.scalars().all():
                    d.status = "EXPIRED"
                    d.rejected_reason = f"Simulation run {d.sim_run_id} ended (active run is {sim_run_id})"
                    expired_ids.append(d.id)

            if expired_ids:
                await session.commit()
                if self.publisher is not None:
                    # Only emit real-time expiration events for active run decisions
                    for d in stale_decisions:
                        await self.publisher.event(
                            type="decision.expired",
                            data={"decision_id": d.id, "reason": "ttl_expired"},
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
        reviewed_driver: bool | None = None,
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
            cur_sim_run_id = getattr(current_state, "sim_run_id", dec.sim_run_id) if current_state else dec.sim_run_id

            if current_state and cur_sim_run_id and dec.sim_run_id != cur_sim_run_id:
                raise DecisionError(
                    "SIM_RUN_MISMATCH",
                    (
                        f"Decision {decision_id} belongs to past simulation run {dec.sim_run_id}, "
                        f"but active run is {cur_sim_run_id}"
                    ),
                    status_code=409,
                )

            # Expiry check
            if dec.expires_at_tick is not None and cur_tick >= dec.expires_at_tick:
                dec.status = "EXPIRED"
                await session.commit()
                raise DecisionExpiredError(
                    decision_id,
                    f"Decision {decision_id} expired at tick {dec.expires_at_tick} (current tick {cur_tick})",
                )

            if dec.status not in ("PENDING_APPROVAL", "AWAITING_SECOND_APPROVAL"):
                raise DecisionInvalidStateError(dec.status, "PENDING_APPROVAL or AWAITING_SECOND_APPROVAL")

            # Check stale state before approving
            if current_state and getattr(current_state, "stale", False):
                raise DecisionError(
                    "STALE_DATA",
                    "Cannot approve decision against stale simulator state",
                    status_code=409,
                )

            # Apply quantity modifications and validations
            line_by_id = {ln.id: ln for ln in dec.lines}
            mod_qtys = {int(k): float(v) for k, v in (modified_quantities or {}).items()}

            if selected_line_ids is not None:
                if len(selected_line_ids) == 0:
                    raise DecisionError(
                        "NO_LINES_SELECTED",
                        "At least one allocation line must be selected for approval.",
                        status_code=422,
                    )
                unknown_sel = [lid for lid in selected_line_ids if lid not in line_by_id]
                if unknown_sel:
                    raise DecisionError(
                        "INVALID_LINE_SELECTION",
                        f"Selected line IDs do not belong to decision {decision_id}: {unknown_sel}",
                        status_code=422,
                    )
                sel_ids = set(selected_line_ids)
            else:
                sel_ids = set(line_by_id.keys())

            unknown_mod = [lid for lid in mod_qtys if lid not in line_by_id]
            if unknown_mod:
                raise DecisionError(
                    "INVALID_LINE_SELECTION",
                    f"Modified line IDs do not belong to decision {decision_id}: {unknown_mod}",
                    status_code=422,
                )

            for lid, new_q in mod_qtys.items():
                if not math.isfinite(new_q) or new_q <= 0:
                    raise DecisionError(
                        "INVALID_QUANTITY",
                        f"Modified quantity for line {lid} must be a finite positive number (got {new_q}).",
                        status_code=422,
                    )

            approved_lines: list[DecisionLine] = []
            used_disp: dict[str, float] = {}

            for line in dec.lines:
                if line.id not in sel_ids:
                    line.status = "CANCELLED"
                    continue

                if line.id in mod_qtys:
                    line.quantity = mod_qtys[line.id]

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
                            f"Line {line.line_no} violated constraints: {violation_msgs}",
                            violations=violations,
                        )
                    used_disp[line.source_depot_id] = used_disp.get(line.source_depot_id, 0.0) + line.quantity

                approved_lines.append(line)

            # F-TRUST Cognitive Forcing check (12_ADVANCED_FEATURES.md / Phase 09 §4.5):
            max_qty = max((line.quantity for line in approved_lines), default=0.0)
            total_qty = sum((line.quantity for line in approved_lines), 0.0)
            conf = dec.confidence if dec.confidence is not None else 1.0
            state_deg = bool(getattr(current_state, "degraded", False)) if current_state is not None else False
            is_deg = bool(dec.degraded) or state_deg

            # Cognitive forcing review applies to human operators
            if actor != "autopilot":
                requires_review = (max_qty > 5000.0) or (conf < 0.7) or is_deg
                if requires_review and not reviewed_driver:
                    forcing_msg = (
                        "Cognitive forcing review required: you must expand Why and review "
                        "the main risk driver before approving this recommendation."
                    )
                    raise DecisionError(
                        "REVIEW_REQUIRED",
                        forcing_msg,
                        status_code=422,
                    )

            # Human-in-the-loop is the explicit Approve click (brief §26).
            # Multi-user four-eyes login was removed to match the participant brief
            # (no Sign-In / enterprise auth requirement).
            if dec.status == "AWAITING_SECOND_APPROVAL":
                # Legacy in-flight rows: complete with the current operator click.
                if dec.approved_by and actor not in dec.approved_by:
                    dec.approved_by = f"{dec.approved_by}, {actor}"
                else:
                    dec.approved_by = actor

            dec.status = "APPROVED"
            if not dec.approved_by or actor == "autopilot" or "," not in str(dec.approved_by):
                dec.approved_by = actor
            dec.approved_at = datetime.now(UTC)

            audit_entry = AuditLog(
                actor=actor,
                action="DECISION_APPROVED",
                entity_type="decision",
                entity_id=str(dec.id),
                payload={
                    "decision_id": dec.id,
                    "tick": cur_tick,
                    "approved_by": dec.approved_by,
                    "lines_approved": len(approved_lines),
                    "total_liters": total_qty,
                },
            )
            session.add(audit_entry)

            # Tamper-evident ledger (DEF-019)
            try:
                await ledger_append(
                    session,
                    actor=actor,
                    action="decision.approved",
                    payload={
                        "decision_id": dec.id,
                        "tick": cur_tick,
                        "sim_run_id": dec.sim_run_id,
                        "lines_approved": len(approved_lines),
                        "total_liters": total_qty,
                        "status": dec.status,
                    },
                )
            except Exception as ledger_exc:
                self._log.warning(
                    "ledger.append_failed",
                    decision_id=dec.id,
                    action="decision.approved",
                    error=str(ledger_exc),
                )

            await session.commit()
            await session.refresh(dec, ["lines"])

        # Record recommendation time-to-decision metric
        if dec.created_at:
            elapsed = (datetime.now(UTC) - dec.created_at).total_seconds()
            if FSIP_RECOMMENDATION_TIME_TO_DECISION and elapsed > 0:
                FSIP_RECOMMENDATION_TIME_TO_DECISION.observe(elapsed)

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

            if dec.status not in ("PENDING_APPROVAL", "AWAITING_SECOND_APPROVAL"):
                raise DecisionInvalidStateError(dec.status, "PENDING_APPROVAL or AWAITING_SECOND_APPROVAL")

            dec.status = "REJECTED"
            dec.approved_by = actor
            dec.rejected_reason = reason

            for line in dec.lines:
                line.status = "CANCELLED"

            try:
                await ledger_append(
                    session,
                    actor=actor,
                    action="decision.rejected",
                    payload={
                        "decision_id": dec.id,
                        "tick": dec.tick,
                        "sim_run_id": dec.sim_run_id,
                        "reason": reason,
                        "status": "REJECTED",
                    },
                )
            except Exception as ledger_exc:
                self._log.warning(
                    "ledger.append_failed",
                    decision_id=dec.id,
                    action="decision.rejected",
                    error=str(ledger_exc),
                )

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
