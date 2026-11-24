"""Operator API Service for BUP Fuel Supply Intelligence & Resilience Platform.

Serves /api/state, /api/stream, /api/stations/{id}, /api/depots/{id}, /api/system/status,
with Redis state caching, TimescaleDB fallback, SSE streaming replay, and rate limiting.
"""
from __future__ import annotations

import time
from contextlib import asynccontextmanager

import httpx
import redis.asyncio as aioredis
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fsip_common.logging import configure_logging, get_logger
from prometheus_client import REGISTRY, Counter, Histogram, make_asgi_app

from app.config import get_settings
from app.db import check_db_health, close_db
from app.metrics_tracker import tracker
from app.middleware.ratelimit import RateLimitMiddleware
from app.routers import (
    assistant_router,
    decisions_router,
    entities_router,
    risk_alerts_router,
    state_router,
    stream_router,
    system_router,
)

log = get_logger()

# ---------------------------------------------------------------- Prometheus Metrics
try:
    REQS = Counter("http_requests_total", "HTTP requests", ["service", "route", "method", "status"])
except ValueError:
    REQS = REGISTRY._names_to_collectors.get("http_requests_total")  # type: ignore[assignment]

try:
    LAT = Histogram(
        "http_request_duration_seconds",
        "HTTP latency",
        ["service", "route"],
        buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
    )
except ValueError:
    LAT = REGISTRY._names_to_collectors.get("http_request_duration_seconds")  # type: ignore[assignment]


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging("api", settings.log_level)
    app.state.redis = aioredis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_timeout=1,
        socket_connect_timeout=1,
    )
    app.state.http = httpx.AsyncClient(timeout=1.5)
    yield
    await app.state.http.aclose()
    await app.state.redis.aclose()
    await close_db()


app = FastAPI(
    title="fsip-api",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Rate limiting
app.add_middleware(RateLimitMiddleware)

# Prometheus metrics
app.mount("/metrics", make_asgi_app())


@app.middleware("http")
async def metrics_mw(request: Request, call_next):
    t0 = time.perf_counter()
    response = await call_next(request)
    duration = time.perf_counter() - t0
    lat_ms = duration * 1000.0

    route = request.scope.get("route")
    path = getattr(route, "path", "unmatched")  # route template -> low cardinality
    if not path.startswith("/metrics"):
        if REQS is not None:
            REQS.labels("api", path, request.method, str(response.status_code)).inc()
        if LAT is not None:
            LAT.labels("api", path).observe(duration)
        tracker.record(lat_ms, response.status_code >= 500)
    return response


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Ensure all HTTP exceptions adhere to standardized detail code and message structure."""
    if isinstance(exc.detail, dict) and "code" in exc.detail:
        detail = exc.detail
    else:
        detail = {"code": "HTTP_ERROR", "message": str(exc.detail)}
    return JSONResponse(status_code=exc.status_code, content={"detail": detail}, headers=exc.headers)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    detail = {
        "code": "VALIDATION_ERROR",
        "message": "Invalid request parameters",
        "errors": exc.errors(),
    }
    return JSONResponse(status_code=422, content={"detail": detail})


# ---------------------------------------------------------------- Core Health Endpoints
@app.get("/health/live")
async def live():
    return {"status": "ok", "service": "api"}


@app.get("/health/ready")
async def ready():
    """Readiness probe checking Redis and Database."""
    redis_ok = False
    try:
        redis_client = getattr(app.state, "redis", None)
        if redis_client is not None:
            await redis_client.ping()
            redis_ok = True
    except Exception:
        redis_ok = False

    db_ok = await check_db_health()

    if redis_ok:
        return {"status": "ready", "redis": "UP", "database": "UP" if db_ok else "DOWN"}
    if db_ok:
        # Fallback to database available -> degraded ready
        return {"status": "degraded", "redis": "DOWN", "database": "UP"}

    return JSONResponse(
        status_code=503,
        content={"status": "down", "redis": "DOWN", "database": "DOWN"},
    )


# Include Routers
app.include_router(assistant_router)
app.include_router(decisions_router)
app.include_router(risk_alerts_router)
app.include_router(state_router)
app.include_router(stream_router)
app.include_router(entities_router)
app.include_router(system_router)
