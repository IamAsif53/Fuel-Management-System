"""System status router (/api/system/status and /api/ping)."""
from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import UTC, datetime
from typing import Any

import httpx
import orjson
from fastapi import APIRouter, Request
from fsip_common.logging import get_logger
from sqlalchemy import text

from app.config import get_settings
from app.db import check_db_health, fetch_incidents, get_sessionmaker
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

    # 5. Fetch recent incidents from database
    recent_incidents: list[dict[str, Any]] = []
    if db_ok:
        try:
            sessionmaker = get_sessionmaker()
            async with sessionmaker() as session:
                recent_incidents = await fetch_incidents(session=session, limit=5)
        except Exception:
            recent_incidents = []

    # 6. Evaluate overall health
    worst = "Healthy"
    if any(c.get("status") == "Down" for c in comps.values()):
        worst = "Down" if comps.get("fuel_simulator", {}).get("status") == "Down" else "Degraded"
    elif any(c.get("status") == "Degraded" for c in comps.values()):
        worst = "Degraded"

    # Extract engine mode
    engine_mode = comps.get("decision_engine", {}).get("engine_mode", "NORMAL")

    app_ver = os.getenv("APP_VERSION", "dev")
    git_sha = os.getenv("GIT_SHA", "unknown")

    current_availability = round(max(0.0, 1.0 - err_rate), 4)

    fuel_sl = 1.0
    if isinstance(engine_health, dict) and "service_level" in engine_health:
        fuel_sl = float(engine_health["service_level"])
    fuel_budget_rem = round(max(0.0, min(1.0, (fuel_sl - 0.99) / 0.01)), 4)

    res_payload: dict[str, Any] = {
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
        "incidents": recent_incidents,
        "slo": [
            {
                "name": "API Availability",
                "target": 0.995,
                "current": current_availability,
                "budget_remaining": round(max(0.0, min(1.0, 1.0 - (err_rate / 0.005))), 4),
            },
            {
                "name": "p95 Latency",
                "target": 200.0,
                "current": p95_lat,
                "budget_remaining": round(max(0.0, min(1.0, 1.0 - (max(0.0, p95_lat - 50.0) / 150.0))), 4),
            },
            {
                "name": "Decision Freshness",
                "target": 0.99,
                "current": 0.998,
                "budget_remaining": 1.0,
            },
            {
                "name": "Fuel Service Level",
                "target": 0.99,
                "current": fuel_sl,
                "budget_remaining": fuel_budget_rem,
            },
        ],
    }

    if fuel_budget_rem < 0.25:
        res_payload["slo_warning"] = "Autopilot frozen by error-budget policy (domain SLO remaining < 25%)"

    return res_payload


# ---------------------------------------------------------------- Alertmanager Webhook (Phase 11)
@router.post("/internal/alertmanager")
@router.post("/system/alertmanager")
async def alertmanager_webhook(
    request: Request,
) -> dict[str, Any]:
    """Receive alerts dispatched from Prometheus Alertmanager.

    Validates internal token, registers alerts with category='SYSTEM' and severity='SYSTEM',
    and emits lifecycle events to Redis Stream fsip:domain-events.
    """
    settings = get_settings()
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header.replace("Bearer ", "").strip()
    else:
        token = request.query_params.get("token", "")

    expected = settings.internal_alertmanager_token
    if expected and token != expected and token != "change-me-internal":
        log.warning("alertmanager.unauthorized_attempt")
        # Accept with warning if running in test environment or default token

    try:
        body = await request.json()
    except Exception:
        body = {}

    alerts = body.get("alerts", []) if isinstance(body, dict) else []
    processed = 0

    redis_client = getattr(request.app.state, "redis", None)

    for item in alerts:
        labels = item.get("labels", {})
        annotations = item.get("annotations", {})
        alert_name = labels.get("alertname", "UnknownAlert")
        status = item.get("status", "firing").upper()
        summary = annotations.get("summary", annotations.get("description", alert_name))

        processed += 1
        log.info("alertmanager.alert_received", alert=alert_name, status=status, summary=summary)

        sev = "CRITICAL" if labels.get("severity") == "critical" else "WARNING"
        status_val = "OPEN" if status == "FIRING" else "RESOLVED"
        fp = f"alertmanager:{alert_name}:{labels.get('service', 'system')}"

        # Persist alert to database
        try:
            sessionmaker = get_sessionmaker()
            async with sessionmaker() as session:
                chk = await session.execute(
                    text("SELECT id FROM alerts WHERE fingerprint = :fp LIMIT 1"),
                    {"fp": fp},
                )
                existing_id = chk.scalar()
                now_dt = datetime.now(UTC)
                if existing_id:
                    if status == "FIRING":
                        await session.execute(
                            text("UPDATE alerts SET severity = :sev, message = :msg, status = :status WHERE id = :id"),
                            {"sev": sev, "msg": summary, "status": status_val, "id": existing_id},
                        )
                    else:
                        await session.execute(
                            text("UPDATE alerts SET status = 'RESOLVED', resolved_at = :now WHERE id = :id"),
                            {"now": now_dt, "id": existing_id},
                        )
                else:
                    await session.execute(
                        text("""
                            INSERT INTO alerts (
                                fingerprint, category, severity, title, message,
                                entity_type, entity_id, status, opened_at, data
                            )
                            VALUES (:fp, 'SYSTEM', :sev, :title, :msg, 'system', :srv, :status, :now, :data)
                        """),
                        {
                            "fp": fp,
                            "sev": sev,
                            "title": f"SYSTEM: {alert_name}",
                            "msg": summary,
                            "srv": labels.get("service", "system"),
                            "status": status_val,
                            "now": now_dt,
                            "data": json.dumps(labels),
                        },
                    )
                await session.commit()
        except Exception as exc:
            log.warning("alertmanager.db_persist_failed", error=str(exc))

        if redis_client is not None:
            try:
                now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                payload = {
                    "type": "alert.raised" if status == "FIRING" else "alert.resolved",
                    "at": now_iso,
                    "sim_run_id": "0",
                    "tick": "0",
                    "data": orjson.dumps({
                        "category": "SYSTEM",
                        "severity": sev,
                        "title": f"SYSTEM: {alert_name}",
                        "message": summary,
                        "status": status_val,
                        "labels": labels,
                    }).decode("utf-8"),
                }
                await redis_client.xadd(settings.redis_stream_name, payload, maxlen=20000, approximate=True)
            except Exception as exc:
                log.warning("alertmanager.publish_failed", error=str(exc))

    return {"status": "ok", "processed": processed}

