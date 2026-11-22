"""System status router (/api/system/status and /api/ping)."""
from __future__ import annotations

import asyncio
import os
import time
from typing import Any

import httpx
import orjson
from fastapi import APIRouter, Request
from fsip_common.logging import get_logger

from app.config import get_settings
from app.db import check_db_health
from app.metrics_tracker import tracker

router = APIRouter(prefix="/api", tags=["system"])
log = get_logger()


async def _probe_http(http_client: httpx.AsyncClient, name: str, url: str) -> tuple[str, dict[str, Any]]:
    t0 = time.perf_counter()
    try:
        r = await http_client.get(url, timeout=1.5)
        ok = r.status_code == 200
        lat = round((time.perf_counter() - t0) * 1000, 1)
        data = r.json() if ok and r.headers.get("content-type", "").startswith("application/json") else {}
        status = "Healthy" if ok else "Degraded"
        res: dict[str, Any] = {"status": status, "latency_ms": lat}
        if "engine_mode" in data:
            res["engine_mode"] = data["engine_mode"]
        if "status" in data and data["status"] in ("ready", "degraded"):
            res["status"] = "Healthy" if data["status"] == "ready" else "Degraded"
        return name, res
    except Exception as exc:
        return name, {"status": "Down", "error": type(exc).__name__}


@router.get("/ping")
async def ping() -> dict[str, Any]:
    """Basic ping endpoint."""
    return {"pong": True, "version": os.getenv("APP_VERSION", "dev")}


@router.get("/system/status")
async def system_status(request: Request) -> dict[str, Any]:
    """Aggregate comprehensive component health, Redis hash metadata, and rolling SLO metrics."""
    settings = get_settings()
    http_client: httpx.AsyncClient = getattr(request.app.state, "http", None) or httpx.AsyncClient(timeout=1.5)

    # 1. Probe external HTTP services
    probe_tasks = [
        _probe_http(http_client, "decision_engine", f"{settings.engine_internal_url}/health/ready"),
        _probe_http(http_client, "prediction_service", f"{settings.intelligence_url}/health/ready"),
        _probe_http(http_client, "fuel_simulator", f"{settings.sim_base_url}/v1/health"),
    ]
    probes = await asyncio.gather(*probe_tasks)
    comps: dict[str, dict[str, Any]] = dict(probes)

    # 2. Probe Redis and read fsip:health hash
    redis_client = getattr(request.app.state, "redis", None)
    redis_healthy = False
    redis_health_hash: dict[str, Any] = {}
    if redis_client is not None:
        try:
            await redis_client.ping()
            redis_healthy = True
            raw_hash = await redis_client.hgetall(settings.redis_health_key)
            for k, v in raw_hash.items():
                try:
                    redis_health_hash[k] = orjson.loads(v)
                except Exception:
                    redis_health_hash[k] = v
        except Exception:
            redis_healthy = False

    comps["redis"] = {"status": "Healthy" if redis_healthy else "Down"}

    # Enrich decision_engine with redis fsip:health metadata if available
    engine_health = redis_health_hash.get("engine")
    if isinstance(engine_health, dict):
        engine_status = "Healthy" if engine_health.get("status") == "ready" else "Degraded"
        comps["decision_engine"].setdefault("status", engine_status)
        comps["decision_engine"]["last_sync_age_s"] = engine_health.get("last_sync_age_s")
        comps["decision_engine"]["engine_mode"] = engine_health.get("engine_mode")
        comps["decision_engine"]["tick"] = engine_health.get("tick")

    # 3. Probe Database
    db_ok = await check_db_health()
    comps["database"] = {"status": "Healthy" if db_ok else "Down"}

    # 4. In-process rolling metrics for backend_api
    p95_lat, err_rate = tracker.get_stats()
    comps["backend_api"] = {
        "status": "Healthy",
        "p95_ms": p95_lat,
        "error_rate": err_rate,
    }

    # Notifier component
    comps["notifier"] = {"status": "Healthy"}

    # 5. Evaluate overall health
    worst = "Healthy"
    if any(c.get("status") == "Down" for c in comps.values()):
        worst = "Down" if comps.get("fuel_simulator", {}).get("status") == "Down" else "Degraded"
    elif any(c.get("status") == "Degraded" for c in comps.values()):
        worst = "Degraded"

    # Extract engine mode
    engine_mode = comps.get("decision_engine", {}).get("engine_mode", "advisory")

    app_ver = os.getenv("APP_VERSION", "dev")
    git_sha = os.getenv("GIT_SHA", "unknown")

    current_availability = round(max(0.0, 1.0 - err_rate), 4)

    return {
        "overall": worst,
        "engine_mode": engine_mode,
        "components": comps,
        "versions": {
            "api": app_ver,
            "engine": app_ver,
            "intelligence": app_ver,
            "frontend": app_ver,
            "git_sha": git_sha,
        },
        "p95_latency_ms": p95_lat,
        "error_rate": err_rate,
        "slo": [
            {
                "name": "API Availability",
                "target": 0.999,
                "current": current_availability,
                "budget_remaining": 1.0,
            },
            {
                "name": "p95 Latency",
                "target": 500.0,
                "current": p95_lat,
                "budget_remaining": 1.0,
            },
        ],
    }
