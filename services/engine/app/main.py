"""Engine service for BUP Fuel Supply Resilience Platform.

Lifespan manages:
- In-memory WorldState store.
- Resilient SimClient with circuit breaker and retries.
- Supervised SyncLoop (polling fallback and state coalescing).
- Supervised SSEListener (real-time stream with inactivity watchdog).
- Independent crash supervision and restart with backoff.
- Health endpoints (/health/live, /health/ready).
- Authenticated debug state endpoint (/debug/state).
- Prometheus metrics exposition (/metrics).
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException
from fsip_common.logging import configure_logging, get_logger
from prometheus_client import Counter, Gauge, make_asgi_app
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.buffer import WriteBuffer
from app.db.models import Decision, DecisionLine, SimRun
from app.db.repo import insert_forecasts
from app.db.session import get_sessionmaker, run_migrations
from app.decision.constraints import PlanLine, check_line
from app.decision.engine import (
    DecisionConstraintViolationError,
    DecisionEngine,
    DecisionError,
    DecisionExpiredError,
    DecisionInvalidStateError,
    DecisionNotFoundError,
)
from app.decision.executor import Executor
from app.decision.tracker import AllocationOutcomeTracker
from app.intelligence import ForecastProvider, ForecastQualityMonitor
from app.sim.circuit import CircuitBreaker
from app.sim.client import SimClient
from app.sim.sse import SSEListener
from app.state.publisher import Publisher
from app.state.store import WorldState
from app.state.sync import FSIP_LOOP_RESTARTS, SyncLoop

from .config import EngineSettings, get_settings

log = get_logger()

# ---------------------------------------------------------------- Metrics
ENGINE_INFO = Gauge("fsip_engine_info", "Engine build info", ["version", "git_sha"])
READY_CHECKS = Counter("fsip_ready_checks_total", "Readiness checks", ["result"])


async def _supervised_runner(
    name: str,
    target: Callable[[], Awaitable[None]],
    stop_event: asyncio.Event,
    initial_backoff: float = 1.0,
    max_backoff: float = 30.0,
) -> None:
    """Supervises a background loop, restarting it with exponential backoff on crash."""
    backoff = initial_backoff
    while not stop_event.is_set():
        try:
            await target()
            if stop_event.is_set():
                break
        except asyncio.CancelledError:
            break
        except Exception as exc:
            if FSIP_LOOP_RESTARTS is not None:
                FSIP_LOOP_RESTARTS.labels(loop=name).inc()
            log.error("loop.crashed", loop=name, error=str(exc), exc_info=True)

        if not stop_event.is_set():
            log.info("loop.restarting", loop=name, backoff_s=round(backoff, 2))
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=backoff)
                break
            except TimeoutError:
                pass
            except asyncio.CancelledError:
                break
            backoff = min(max_backoff, backoff * 2.0)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Engine lifespan: manages initialization, supervision of sync loops, and graceful shutdown."""
    s = get_settings()
    configure_logging("engine", s.log_level)
    ENGINE_INFO.labels(s.app_version, s.git_sha).set(1)

    # Run database migrations on startup if enabled
    if s.auto_migrate:
        await run_migrations(s.database_url)

    # Initialize store and client (respecting existing app.state if injected by tests)
    store = getattr(app.state, "store", None) or WorldState(
        engine_mode="NORMAL", operating_mode=s.engine_mode, policy=s.engine_policy
    )
    breaker = CircuitBreaker(
        "simulator",
        failure_threshold=s.breaker_failure_threshold,
        reset_timeout=s.breaker_reset_sec,
    )
    client = getattr(app.state, "client", None) or SimClient(
        base_url=s.sim_base_url,
        timeout=s.sim_timeout_sec,
        max_retries=s.sim_max_retries,
        breaker=breaker,
    )

    # Initialize WriteBuffer for asynchronous DB persistence
    sessionmaker = getattr(app.state, "sessionmaker", None) or get_sessionmaker()
    write_buffer = getattr(app.state, "write_buffer", None) or WriteBuffer(sessionmaker=sessionmaker)
    app.state.write_buffer = write_buffer

    # Initialize Redis publisher
    publisher = getattr(app.state, "publisher", None) or Publisher(redis_url=s.redis_url)
    app.state.publisher = publisher

    # Initialize AlertManager and IncidentManager
    from app.alerts.manager import AlertManager, IncidentManager
    alert_manager = getattr(app.state, "alert_manager", None) or AlertManager(
        session_factory=sessionmaker,
        redis_client=publisher._get_client(),
    )
    app.state.alert_manager = alert_manager

    incident_manager = getattr(app.state, "incident_manager", None) or IncidentManager(
        session_factory=sessionmaker,
        redis_client=publisher._get_client(),
    )
    app.state.incident_manager = incident_manager

    # Initialize SyncLoop and SSEListener
    sync_loop = getattr(app.state, "sync_loop", None) or SyncLoop(
        client=client,
        store=store,
        poll_interval=s.engine_poll_interval_sec,
        write_buffer=write_buffer,
        publisher=publisher,
        alert_manager=alert_manager,
    )
    sse_listener = getattr(app.state, "sse_listener", None) or SSEListener(
        base_url=s.sim_base_url,
        on_tick=sync_loop.request_cycle,
        on_notice=sync_loop.on_notice,
        on_connect=sync_loop.full_sync,
    )

    # Initialize Executor, DecisionEngine, AllocationOutcomeTracker
    executor = getattr(app.state, "executor", None) or Executor(
        sim_client=client,
        session_factory=sessionmaker,
        publisher=publisher,
        settings=s,
    )
    decision_engine = getattr(app.state, "decision_engine", None) or DecisionEngine(
        session_factory=sessionmaker,
        publisher=publisher,
        settings=s,
        executor=executor,
    )
    tracker = getattr(app.state, "tracker", None) or AllocationOutcomeTracker(
        session_factory=sessionmaker,
        publisher=publisher,
    )
    forecast_provider = getattr(app.state, "forecast_provider", None) or ForecastProvider(
        base_url=s.intelligence_url,
        timeout_sec=s.intelligence_timeout_sec,
    )
    forecast_monitor = getattr(app.state, "forecast_monitor", None) or ForecastQualityMonitor(
        session_factory=sessionmaker,
        write_buffer=write_buffer,
    )

    async def forecast_hook(current_store: WorldState) -> None:
        try:
            # 1. Evaluate previous forecasts against newly realized demand
            if forecast_monitor:
                await forecast_monitor.evaluate_tick(current_store, forecast_provider)

            # 2. Retrieve multi-horizon forecasts for upcoming ticks
            fb = await forecast_provider.get_forecast(current_store)
            current_store.forecast_bundle = fb

            # 3. Persist forecast rows
            if current_store.sim_run_id is not None and write_buffer:
                rows = fb.to_db_rows(current_store.sim_run_id, current_store.sim_time)
                if rows:
                    async def _persist_forecasts_op(session: AsyncSession) -> None:
                        run_id = await session.scalar(
                            select(SimRun.id).where(SimRun.id == current_store.sim_run_id)
                        )
                        if not run_id:
                            log.warning(
                                "forecast.skipped_uncommitted_run",
                                sim_run_id=current_store.sim_run_id,
                            )
                            return
                        for r in rows:
                            r["sim_run_id"] = run_id
                        await insert_forecasts(session, forecasts=rows)

                    write_buffer.submit(_persist_forecasts_op, kind="forecast")

            # 4. Record new forecasts in monitor for future verification
            if forecast_monitor:
                forecast_monitor.record_forecast(fb)
        except Exception as exc:
            log.warning("engine.forecast_hook_failed", error=str(exc))

    # Register lifecycle hooks with sync loop
    sync_loop.add_hook(forecast_hook)
    sync_loop.add_hook(tracker.track_cycle)
    sync_loop.add_hook(decision_engine.on_cycle)

    # Attach to app.state
    app.state.store = store
    app.state.client = client
    app.state.sync_loop = sync_loop
    app.state.sse_listener = sse_listener
    app.state.executor = executor
    app.state.decision_engine = decision_engine
    app.state.tracker = tracker
    app.state.forecast_provider = forecast_provider
    app.state.forecast_monitor = forecast_monitor

    log.info(
        "engine.start",
        sim=s.sim_base_url,
        mode=s.engine_mode,
        policy=s.engine_policy,
        poll_interval=s.engine_poll_interval_sec,
    )

    # Perform initial synchronization and startup allocation reconciliation
    try:
        reconciled_count = await tracker.reconcile_on_startup(client)
        if reconciled_count > 0:
            log.info("engine.startup_reconciliation_ok", reconciled=reconciled_count)
    except Exception as exc:
        log.warning("engine.startup_reconciliation_failed", error=str(exc))

    try:
        await sync_loop.full_sync()
        log.info("engine.initial_sync_ok", tick=store.tick)
    except Exception as exc:
        log.warning("engine.initial_sync_failed", error=str(exc))
        store.degraded_reasons.add("sim_unavailable")

    # Start supervised background tasks
    stop_event = asyncio.Event()
    app.state.stop_event = stop_event

    sync_task = asyncio.create_task(
        _supervised_runner("sync", sync_loop.run, stop_event),
        name="supervised-sync-loop",
    )
    sse_task = asyncio.create_task(
        _supervised_runner("sse", sse_listener.run, stop_event),
        name="supervised-sse-listener",
    )
    buffer_task = asyncio.create_task(
        _supervised_runner("write_buffer", write_buffer.run, stop_event),
        name="supervised-write-buffer",
    )
    app.state.tasks = [sync_task, sse_task]
    app.state.buffer_task = buffer_task

    yield

    # Graceful shutdown
    log.info("engine.stopping")
    stop_event.set()
    sync_loop.stop()
    sse_listener.stop()
    await write_buffer.stop(timeout=5.0, task=buffer_task)

    for task in [sync_task, sse_task, buffer_task]:
        if not task.done():
            task.cancel()

    await asyncio.gather(sync_task, sse_task, buffer_task, return_exceptions=True)
    await client.close()
    await publisher.close()
    app.state.sync_loop = None
    app.state.sse_listener = None
    app.state.client = None
    app.state.store = None
    app.state.executor = None
    app.state.decision_engine = None
    app.state.tracker = None
    log.info("engine.stop")


app = FastAPI(title="fsip-engine", version="0.1.0", lifespan=lifespan)
app.mount("/metrics", make_asgi_app())


def require_internal(
    x_internal_token: str | None = Header(default=None),
    s: EngineSettings = Depends(get_settings),
) -> None:
    """Dependency enforcing X-Internal-Token authentication."""
    if not x_internal_token or x_internal_token != s.engine_internal_token:
        raise HTTPException(
            status_code=401,
            detail={"code": "UNAUTHORIZED", "message": "bad internal token"},
        )


@app.get("/health/live")
async def live():
    """Liveness probe: returns 200 when engine process is alive."""
    return {"status": "ok", "service": "engine"}


@app.get("/health/ready")
async def ready():
    """Readiness probe.

    - Returns 200 ready if successful sync happened within last 30s and not degraded.
    - Returns 200 degraded if serving cached state in degraded mode or sync > 30s old.
    - Returns 503 if never successfully synchronized and no usable cached state exists.
    """
    store: WorldState = getattr(app.state, "store", None)
    if store is None:
        READY_CHECKS.labels("fail").inc()
        raise HTTPException(
            status_code=503,
            detail={"code": "NOT_READY", "message": "Engine store not initialized"},
        )

    last_sync = max(store.last_good_at.values()) if store.last_good_at else None
    if last_sync is None and store.instance is None:
        READY_CHECKS.labels("fail").inc()
        raise HTTPException(
            status_code=503,
            detail={"code": "NOT_READY", "message": "No successful sync yet"},
        )

    now = time.time()
    age_s = round(now - last_sync, 2) if last_sync is not None else 999.0
    is_ready = age_s <= 30.0 and not store.degraded
    status = "ready" if is_ready else "degraded"

    READY_CHECKS.labels("ok").inc()
    dec_engine: DecisionEngine = getattr(app.state, "decision_engine", None)
    return {
        "status": status,
        "last_sync_age_s": age_s,
        "engine_mode": store.engine_mode,
        "operating_mode": getattr(store, "operating_mode", "advisory"),
        "policy": getattr(store, "policy", "heuristic"),
        "last_plan_tick": getattr(dec_engine, "last_plan_tick", -1) if dec_engine else -1,
    }


@app.get("/debug/state", dependencies=[Depends(require_internal)])
async def debug_state():
    """Debug endpoint: returns public JSON projection of in-memory WorldState."""
    store: WorldState = getattr(app.state, "store", None)
    if store is None:
        raise HTTPException(
            status_code=503,
            detail={"code": "NOT_INITIALIZED", "message": "Store not initialized"},
        )
    return store.to_public_json()


# ---------------------------------------------------------------- Decision & Control Endpoints
class ApproveDecisionRequest(BaseModel):
    selected_line_ids: list[int] | None = None
    modified_quantities: dict[int, float] | None = None
    operator: str = "operator"
    reviewed_driver: bool | None = None


class RejectDecisionRequest(BaseModel):
    reason: str
    operator: str = "operator"


class EngineModeRequest(BaseModel):
    mode: str = Field(..., description="advisory or autopilot")


class EnginePolicyRequest(BaseModel):
    policy: str = Field(..., description="heuristic or optimizer")


class ManualAllocationRequest(BaseModel):
    source_depot_id: str
    destination_station_id: str
    route_id: str
    fuel_type: str
    quantity: float
    reason: str = "Operator manual dispatch"
    operator: str = "operator"


@app.post("/engine/decisions/{decision_id}/approve", dependencies=[Depends(require_internal)])
async def approve_decision(decision_id: int, req: ApproveDecisionRequest):
    """Approve a decision, optionally selecting a subset of lines and modifying quantities."""
    dec_engine: DecisionEngine = getattr(app.state, "decision_engine", None)
    if not dec_engine:
        raise HTTPException(status_code=503, detail="Decision engine not initialized")

    try:
        dec = await dec_engine.approve_decision(
            decision_id=decision_id,
            actor=req.operator,
            selected_line_ids=req.selected_line_ids,
            modified_quantities=req.modified_quantities,
            current_state=getattr(app.state, "store", None),
            reviewed_driver=req.reviewed_driver,
        )
    except DecisionNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"code": "DECISION_NOT_FOUND", "message": str(exc)}) from exc
    except DecisionExpiredError as exc:
        raise HTTPException(status_code=409, detail={"code": "DECISION_EXPIRED", "message": str(exc)}) from exc
    except DecisionInvalidStateError as exc:
        raise HTTPException(status_code=409, detail={"code": "INVALID_STATE", "message": str(exc)}) from exc
    except DecisionConstraintViolationError as exc:
        raw_violations = getattr(exc, "violations", None) or []
        raise HTTPException(
            status_code=422,
            detail={
                "code": "CONSTRAINT_VIOLATION",
                "message": exc.message,
                "violations": [
                    {"rule": getattr(v, "rule", str(v)), "message": getattr(v, "message", str(v))}
                    for v in raw_violations
                ],
            },
        ) from exc
    except DecisionError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": exc.message}) from exc
    except Exception as exc:
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail={"code": "INTERNAL_ERROR", "message": f"{type(exc).__name__}: {exc}"},
        ) from exc

    return {
        "status": "ok",
        "decision_id": dec.id,
        "decision_status": dec.status,
        "approved_by": dec.approved_by,
        "lines_count": len(dec.lines),
    }


@app.post("/engine/decisions/{decision_id}/reject", dependencies=[Depends(require_internal)])
async def reject_decision(decision_id: int, req: RejectDecisionRequest):
    """Reject a pending decision with a reason."""
    dec_engine: DecisionEngine = getattr(app.state, "decision_engine", None)
    if not dec_engine:
        raise HTTPException(status_code=503, detail="Decision engine not initialized")

    try:
        dec = await dec_engine.reject_decision(
            decision_id=decision_id,
            reason=req.reason,
            actor=req.operator,
        )
    except DecisionNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"code": "DECISION_NOT_FOUND", "message": str(exc)}) from exc
    except DecisionExpiredError as exc:
        raise HTTPException(status_code=409, detail={"code": "DECISION_EXPIRED", "message": str(exc)}) from exc
    except DecisionInvalidStateError as exc:
        raise HTTPException(status_code=409, detail={"code": "INVALID_STATE", "message": str(exc)}) from exc

    return {
        "status": "ok",
        "decision_id": dec.id,
        "decision_status": dec.status,
        "rejected_reason": dec.rejected_reason,
    }


@app.post("/engine/plan-now", dependencies=[Depends(require_internal)])
async def plan_now():
    """Trigger an immediate planning cycle."""
    dec_engine: DecisionEngine = getattr(app.state, "decision_engine", None)
    store: WorldState = getattr(app.state, "store", None)
    if not dec_engine or not store:
        raise HTTPException(status_code=503, detail="Engine components not initialized")

    dec = await dec_engine.plan_now(store)
    if dec is None:
        return {"status": "no_op", "message": "No allocations required", "decision_id": None}

    return {
        "status": "ok",
        "decision_id": dec.id,
        "decision_status": dec.status,
        "lines_count": len(dec.lines),
    }


@app.post("/engine/control/mode", dependencies=[Depends(require_internal)])
async def control_mode(req: EngineModeRequest):
    """Switch operating mode (advisory vs autopilot)."""
    mode = req.mode.lower()
    if mode not in ("advisory", "autopilot"):
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_MODE", "message": "Mode must be advisory or autopilot"},
        )

    store: WorldState = getattr(app.state, "store", None)
    dec_engine: DecisionEngine = getattr(app.state, "decision_engine", None)
    publisher: Publisher = getattr(app.state, "publisher", None)
    sim_client: SimClient = getattr(app.state, "client", None)
    sync_loop: SyncLoop = getattr(app.state, "sync_loop", None)

    # 21_API_CONTRACTS.md §2.8 & 05_INTELLIGENCE_DESIGN.md §4.5:
    # 409 STALE_DATA if turning autopilot on while degraded, stale, or non-NORMAL engine mode.
    # Probe live simulator /v1/instance immediately so paused stale_data/faults cannot slip past poll intervals.
    if mode == "autopilot" and store is not None:
        if sim_client is not None:
            try:
                inst_res = await sim_client.instance()
                if bool(getattr(inst_res, "stale", False)):
                    store.stale = True
                    store.degraded_reasons.add("stale_data")
                    if sync_loop is not None and hasattr(sync_loop, "_evaluate_mode"):
                        sync_loop._evaluate_mode(cycle_failed=False)
                    if publisher is not None and hasattr(publisher, "publish_state"):
                        try:
                            await publisher.publish_state(store)
                        except Exception:
                            pass
            except Exception:
                store.degraded_reasons.add("sim_unavailable")
                if sync_loop is not None and hasattr(sync_loop, "_evaluate_mode"):
                    sync_loop._evaluate_mode(cycle_failed=True)
                if publisher is not None and hasattr(publisher, "publish_state"):
                    try:
                        await publisher.publish_state(store)
                    except Exception:
                        pass

        from app.state.mode import SOFT_DEGRADED_REASONS

        is_stale = bool(getattr(store, "stale", False))
        resilience_mode = str(getattr(store, "engine_mode", "NORMAL")).upper()
        degraded_reasons = set(getattr(store, "degraded_reasons", set()))
        # Soft SLO budget still freezes per-decision auto-approvals; it must not
        # block entering Autopilot when the engine is otherwise healthy.
        hard_reasons = degraded_reasons - SOFT_DEGRADED_REASONS
        is_hard_degraded = bool(getattr(store, "degraded", False)) or bool(hard_reasons)

        if is_stale or is_hard_degraded or resilience_mode != "NORMAL":
            reasons: list[str] = []
            if is_stale:
                reasons.append("data is stale")
            if is_hard_degraded:
                reasons_str = f" ({', '.join(sorted(hard_reasons))})" if hard_reasons else ""
                reasons.append(f"system is degraded{reasons_str}")
            if resilience_mode != "NORMAL":
                reasons.append(f"engine mode is {resilience_mode} (must be NORMAL)")

            reason_msg = "; ".join(reasons)
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "STALE_DATA",
                    "message": f"Cannot enable Autopilot when data is stale or system is degraded: {reason_msg}",
                },
            )

    if store:
        store.operating_mode = mode
    if dec_engine:
        dec_engine.settings.engine_mode = mode

    if publisher and store:
        await publisher.event(
            type="engine.mode_changed",
            data={"mode": mode},
            sim_run_id=store.sim_run_id,
            tick=store.tick,
        )
        # Immediately update Redis live snapshot so frontend header reflects mode
        try:
            if hasattr(publisher, "publish_state"):
                await publisher.publish_state(store)
        except Exception as exc:
            log.warning("autopilot.snapshot_update_failed", error=str(exc))

    auto_approved_count = 0
    if mode == "autopilot" and dec_engine and store:
        try:
            auto_approved_count = await dec_engine.evaluate_autopilot_pending(store)
            if auto_approved_count == 0:
                dec = await dec_engine.plan_now(store)
                if dec and dec.status == "APPROVED":
                    auto_approved_count += 1
        except Exception as exc:
            log.warning("autopilot.immediate_evaluation_failed", error=str(exc))

    return {"status": "ok", "mode": mode, "operating_mode": mode, "auto_approved": auto_approved_count}


@app.post("/engine/control/policy", dependencies=[Depends(require_internal)])
async def control_policy(req: EnginePolicyRequest):
    """Switch active policy (heuristic vs optimizer)."""
    policy = req.policy.lower()
    if policy not in ("heuristic", "optimizer"):
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_POLICY", "message": "Policy must be heuristic or optimizer"},
        )

    store: WorldState = getattr(app.state, "store", None)
    dec_engine: DecisionEngine = getattr(app.state, "decision_engine", None)
    publisher: Publisher = getattr(app.state, "publisher", None)

    if store:
        store.policy = policy
    if dec_engine:
        dec_engine.settings.engine_policy = policy

    if publisher and store:
        await publisher.event(
            type="engine.policy_changed",
            data={"policy": policy},
            sim_run_id=store.sim_run_id,
            tick=store.tick,
        )

    return {"status": "ok", "policy": policy}


@app.post("/engine/manual-allocation", dependencies=[Depends(require_internal)])
async def manual_allocation(req: ManualAllocationRequest):
    """Validate and execute a manual operator dispatch."""
    store: WorldState = getattr(app.state, "store", None)
    dec_engine: DecisionEngine = getattr(app.state, "decision_engine", None)
    executor: Executor = getattr(app.state, "executor", None)

    if not store or not dec_engine or not executor:
        raise HTTPException(status_code=503, detail="Engine not ready")

    plan_line = PlanLine(
        route_id=req.route_id,
        source_depot_id=req.source_depot_id,
        destination_station_id=req.destination_station_id,
        fuel_type=req.fuel_type,
        quantity=float(req.quantity),
        reason=req.reason,
    )

    violations = check_line(store, plan_line, for_execution=True)
    if violations:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "CONSTRAINT_VIOLATION",
                "message": "Manual allocation violates constraints",
                "violations": [
                    {"rule": v.rule, "message": v.message}
                    for v in violations
                ],
            },
        )

    # Persist manual decision and line
    async with dec_engine.session_factory() as session:
        cur_tick = int(getattr(store, "tick", 0))
        sim_run_id = getattr(store, "sim_run_id", None)

        dec = Decision(
            sim_run_id=sim_run_id,
            tick=cur_tick,
            policy="manual",
            status="APPROVED",
            approved_by=req.operator,
            approved_at=datetime.now(UTC),
            trigger="MANUAL_OPERATOR",
        )
        session.add(dec)
        await session.flush()

        idemp_key = f"fsip-{sim_run_id or 0}-{dec.id}-1"
        line = DecisionLine(
            decision_id=dec.id,
            line_no=1,
            source_depot_id=req.source_depot_id,
            destination_station_id=req.destination_station_id,
            route_id=req.route_id,
            fuel_type=req.fuel_type,
            quantity=float(req.quantity),
            idempotency_key=idemp_key,
            status="APPROVED",
            reason=req.reason,
        )
        session.add(line)
        await session.commit()
        await session.refresh(dec)
        await session.refresh(line)

    # Execute line via executor
    await executor.execute(dec, [line])

    return {
        "status": "ok",
        "decision_id": dec.id,
        "line_id": line.id,
        "execution_status": dec.status,
    }


@app.post("/internal/policy", dependencies=[Depends(require_internal)])
async def internal_policy(req: dict[str, Any]):
    """Runtime policy switching endpoint per 21 §4 (Task 8.6)."""
    policy = str(req.get("policy", "")).lower()
    if policy not in ("heuristic", "optimizer"):
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_POLICY", "message": "Policy must be heuristic or optimizer"},
        )
    store: WorldState = getattr(app.state, "store", None)
    dec_engine: DecisionEngine = getattr(app.state, "decision_engine", None)
    publisher: Publisher = getattr(app.state, "publisher", None)

    if store:
        store.policy = policy
    if dec_engine:
        dec_engine.settings.engine_policy = policy

    if publisher and store:
        await publisher.event(
            type="engine.policy_changed",
            data={"policy": policy},
            sim_run_id=store.sim_run_id,
            tick=store.tick,
        )

    scarcity_policy = req.get("scarcity_policy")
    return {"policy": policy, "scarcity_policy": scarcity_policy}


class InternalWhatIfLine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    route_id: str = ""
    fuel_type: str = ""
    quantity: float = 0.0


class InternalWhatIfEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal[
        "demand_spike",
        "route_disruption",
        "station_outage",
        "depot_constraint",
        "shipment_delay",
        "supply_shortfall",
    ]
    start_offset: int = Field(default=0, ge=0, le=96)
    duration_ticks: int = Field(default=24, ge=1, le=240)
    parameters: dict[str, Any] = Field(default_factory=dict)


class InternalWhatIfRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    horizon_ticks: int = Field(default=48, ge=1, le=96)
    lines: list[InternalWhatIfLine] = Field(default_factory=list, max_length=20)
    events: list[InternalWhatIfEvent] = Field(default_factory=list, max_length=5)
    compare: list[Literal["do_nothing", "recommended", "custom"]] | None = None


@app.post("/internal/whatif", dependencies=[Depends(require_internal)])
async def internal_whatif(req: InternalWhatIfRequest):
    """Forward-looking scenario projection with ShadowTwin per 21 §4 (Task 8.7)."""
    import copy

    from app.decision.optimizer import OptimizerPolicy
    from app.decision.twin import ShadowTwin

    horizon = min(96, int(req.horizon_ticks))
    raw_lines = req.lines
    events = req.events

    store: WorldState = getattr(app.state, "store", None)
    if not store:
        raise HTTPException(status_code=503, detail="Store state unavailable")

    current_tick = getattr(store, "tick", 0)

    # Resolve route and depot mappings for strict validation
    routes_raw = getattr(store, "routes", []) or []
    routes_dict = {
        r.id: r for r in routes_raw
    } if isinstance(routes_raw, list) else dict(routes_raw or {})

    depots_raw = getattr(store, "depots", []) or []
    depots_dict = {
        d.id: d for d in depots_raw
    } if isinstance(depots_raw, list) else dict(depots_raw or {})

    # Validate candidate custom dispatch lines (reject invalid/infeasible, explain clearly)
    validated_lines = []
    for line in raw_lines:
        r_id = str(line.route_id).strip()
        if not r_id or r_id not in routes_dict:
            avail = sorted(routes_dict.keys())
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "INVALID_IDENTIFIER",
                    "message": f"Route '{r_id}' does not exist. Available routes: {avail}",
                },
            )

        f_type = str(line.fuel_type).strip().upper()
        if f_type not in ("DIESEL", "PETROL", "OCTANE"):
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "INVALID_FUEL_TYPE",
                    "message": f"Invalid fuel type '{f_type}'. Supported fuels: DIESEL, PETROL, OCTANE",
                },
            )

        qty = float(line.quantity)
        if qty <= 0.0:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "INVALID_QUANTITY",
                    "message": f"Quantity must be strictly positive (> 0 L), got {qty}",
                },
            )

        r_obj = routes_dict[r_id]
        max_ship = float(getattr(r_obj, "max_shipment", 0.0) or (
            r_obj.get("max_shipment", 0.0) if isinstance(r_obj, dict) else 0.0
        ))
        if max_ship > 0 and qty > max_ship:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "INFEASIBLE_DISPATCH",
                    "message": f"Quantity {qty:g} L exceeds route '{r_id}' max shipment limit of {max_ship:g} L",
                },
            )

        src_depot = str(getattr(r_obj, "source_depot_id", "") or (
            r_obj.get("source_depot_id", "") if isinstance(r_obj, dict) else ""
        ))
        d_obj = depots_dict.get(src_depot)
        inv_map = getattr(d_obj, "inventory", {}) if d_obj else {}
        avail_inv = float(inv_map.get(f_type, 0.0)) if isinstance(inv_map, dict) else 0.0
        if qty > avail_inv:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "INFEASIBLE_DISPATCH",
                    "message": (
                        f"Quantity {qty:g} L exceeds available {f_type} inventory at depot "
                        f"'{src_depot}' ({avail_inv:g} L available)"
                    ),
                },
            )

        validated_lines.append({
            "route_id": r_id,
            "fuel_type": f_type,
            "quantity": qty,
        })

    # Format hypothetical events
    formatted_events = []
    for ev in events:
        st_offset = int(ev.start_offset)
        dur = int(ev.duration_ticks)
        formatted_events.append({
            "type": ev.type,
            "start_tick": current_tick + st_offset,
            "end_tick": current_tick + st_offset + dur,
            "parameters": ev.parameters,
        })

    # Scenarios: do_nothing, recommended, custom
    # 1. Do nothing
    dn_res = ShadowTwin.project(
        state=store,
        lines=[],
        hypothetical_events=formatted_events,
        horizon=horizon,
        n_samples=40,
    )

    # 2. Recommended (Optimizer Plan on counterfactual context)
    cf_store = copy.copy(store)
    cf_store.events = list(getattr(store, "events", []) or []) + formatted_events
    optimizer = OptimizerPolicy()
    rec_plan = optimizer.solve(cf_store)
    rec_res = ShadowTwin.project(
        state=store,
        lines=rec_plan.lines,
        hypothetical_events=formatted_events,
        horizon=horizon,
        n_samples=40,
    )

    # 3. Custom lines
    custom_res = ShadowTwin.project(
        state=store,
        lines=validated_lines,
        hypothetical_events=formatted_events,
        horizon=horizon,
        n_samples=40,
    )

    def _format_scenario(name: str, res: Any) -> dict[str, Any]:
        stations_out = []
        for (st_id, f_type), st_p in res.stations.items():
            init_inv = getattr(st_p, "initial_inventory", 0.0)
            end_inv = getattr(st_p, "ending_inventory_p50", 0.0)
            if not end_inv and st_p.inventory_p50:
                end_inv = st_p.inventory_p50[-1]
            delta = round(end_inv - init_inv, 1)

            stations_out.append({
                "station_id": st_id,
                "fuel_type": f_type,
                "stockout_prob": st_p.stockout_prob,
                "first_stockout_tick": st_p.first_stockout_tick,
                "inventory_p50": st_p.inventory_p50,
                "initial_inv": init_inv,
                "ending_inv": end_inv,
                "delta_liters": delta,
                "unmet_liters": st_p.unmet_liters,
                "overflow_liters": st_p.overflow_liters,
            })
        return {
            "name": name,
            "unmet": res.total_unmet_liters,
            "overflow": res.total_overflow_liters,
            "min_station_sl": res.min_station_service_level,
            "stations": stations_out,
        }

    return {
        "tick": current_tick,
        "scenarios": [
            _format_scenario("do_nothing", dn_res),
            _format_scenario("recommended", rec_res),
            _format_scenario("custom", custom_res),
        ],
        "disclaimer": "Projection by our model; not executed.",
    }

