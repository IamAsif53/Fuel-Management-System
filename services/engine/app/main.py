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

from fastapi import Depends, FastAPI, Header, HTTPException
from fsip_common.logging import configure_logging, get_logger
from prometheus_client import Counter, Gauge, make_asgi_app

from app.db.buffer import WriteBuffer
from app.db.session import get_sessionmaker, run_migrations
from app.sim.circuit import CircuitBreaker
from app.sim.client import SimClient
from app.sim.sse import SSEListener
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

    # Initialize store and client
    store = WorldState(engine_mode="NORMAL", operating_mode=s.engine_mode, policy=s.engine_policy)
    breaker = CircuitBreaker(
        "simulator",
        failure_threshold=s.breaker_failure_threshold,
        reset_timeout=s.breaker_reset_sec,
    )
    client = SimClient(
        base_url=s.sim_base_url,
        timeout=s.sim_timeout_sec,
        max_retries=s.sim_max_retries,
        breaker=breaker,
    )

    # Initialize WriteBuffer for asynchronous DB persistence
    sessionmaker = get_sessionmaker()
    write_buffer = WriteBuffer(sessionmaker=sessionmaker)
    app.state.write_buffer = write_buffer

    # Initialize SyncLoop and SSEListener
    sync_loop = SyncLoop(
        client=client,
        store=store,
        poll_interval=s.engine_poll_interval_sec,
        write_buffer=write_buffer,
    )
    sse_listener = SSEListener(
        base_url=s.sim_base_url,
        on_tick=sync_loop.request_cycle,
        on_notice=sync_loop.on_notice,
        on_connect=sync_loop.full_sync,
    )

    # Attach to app.state
    app.state.store = store
    app.state.client = client
    app.state.sync_loop = sync_loop
    app.state.sse_listener = sse_listener

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
    return {
        "status": status,
        "last_sync_age_s": age_s,
        "engine_mode": store.engine_mode,
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
