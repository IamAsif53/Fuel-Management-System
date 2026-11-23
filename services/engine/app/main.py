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

from fastapi import Depends, FastAPI, Header, HTTPException
from fsip_common.logging import configure_logging, get_logger
from prometheus_client import Counter, Gauge, make_asgi_app
from pydantic import BaseModel, Field

from app.db.buffer import WriteBuffer
from app.db.models import Decision, DecisionLine
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

    # Initialize SyncLoop and SSEListener
    sync_loop = getattr(app.state, "sync_loop", None) or SyncLoop(
        client=client,
        store=store,
        poll_interval=s.engine_poll_interval_sec,
        write_buffer=write_buffer,
        publisher=publisher,
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

    # Register lifecycle hooks with sync loop
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

    log.info(
        "engine.start",
        sim=s.sim_base_url,
        mode=s.engine_mode,
        policy=s.engine_policy,
        poll_interval=s.engine_poll_interval_sec,
    )

    # Perform initial synchronization
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
        )
    except DecisionNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"code": "DECISION_NOT_FOUND", "message": str(exc)}) from exc
    except DecisionExpiredError as exc:
        raise HTTPException(status_code=409, detail={"code": "DECISION_EXPIRED", "message": str(exc)}) from exc
    except DecisionInvalidStateError as exc:
        raise HTTPException(status_code=409, detail={"code": "INVALID_STATE", "message": str(exc)}) from exc
    except DecisionConstraintViolationError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "CONSTRAINT_VIOLATION",
                "message": exc.message,
                "violations": [
                    {"rule": getattr(v, "rule", str(v)), "message": getattr(v, "message", str(v))}
                    for v in exc.violations
                ],
            },
        ) from exc
    except DecisionError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": exc.message}) from exc

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

    return {"status": "ok", "mode": mode}


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
