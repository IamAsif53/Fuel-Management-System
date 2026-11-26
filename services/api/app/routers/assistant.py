"""Ops Assistant and Executive Summary router for Phase 09.

Follows 21_API_CONTRACTS.md §2.6 and 05_INTELLIGENCE_DESIGN.md §7.2, §7.3.
Provides:
- POST /api/assistant/ask (viewer, rate-limited 20/min)
- GET /api/summary?window_ticks=96 (viewer)
"""
from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fsip_common.explain import LLMExplainer, render_template, rules_based_assistant
from fsip_common.logging import get_logger
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_viewer
from app.config import ApiSettings, get_settings
from app.db import (
    fetch_alerts,
    fetch_decisions,
    fetch_incidents,
    get_session,
)
from app.services.state_service import get_current_state

log = get_logger()
router = APIRouter(prefix="/api", tags=["assistant"])

# In-memory sliding window for assistant-specific rate limit (20 req/min)
_assistant_ratelimit_counters: dict[str, list[float]] = {}


def _check_assistant_rate_limit(request: Request) -> None:
    """Enforce 20 requests/minute per client IP for the Ops Assistant."""
    forwarded = request.headers.get("X-Forwarded-For")
    ip = forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else "unknown")

    now = time.time()
    cutoff = now - 60.0

    history = _assistant_ratelimit_counters.get(ip, [])
    # Filter to last 60 seconds
    history = [t for t in history if t > cutoff]

    if len(history) >= 20:
        _assistant_ratelimit_counters[ip] = history
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "RATE_LIMITED", "message": "Rate limit exceeded (20 requests per minute)"},
            headers={"Retry-After": "60"},
        )

    history.append(now)
    _assistant_ratelimit_counters[ip] = history


# ---------------------------------------------------------------- Schemas
class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=500, description="Control-room operator query")


class SourceItem(BaseModel):
    type: str
    id: str


class AskResponse(BaseModel):
    answer: str
    engine: str
    sources: list[SourceItem]
    generated_at: str


class SummaryStats(BaseModel):
    window_ticks: int
    service_level: float
    unmet_liters: float
    total_dispatched_liters: float
    decisions_count: int
    autopilot_count: int
    manual_count: int
    alerts_opened: int
    alerts_resolved: int
    incidents_count: int
    active_policy: str


class SummaryResponse(BaseModel):
    text: str
    engine: str
    stats: dict[str, Any]


# ---------------------------------------------------------------- Context Builder
async def _build_assistant_context(
    request: Request,
    session: AsyncSession,
) -> dict[str, Any]:
    """Assemble a grounded platform context snapshot for the assistant, capped at ~6000 tokens."""
    # 1. State
    redis_client = getattr(request.app.state, "redis", None)
    state_res = await get_current_state(redis_client, session)
    if isinstance(state_res, tuple):
        state, tick, is_degraded, _ = state_res
    elif isinstance(state_res, dict):
        state = state_res
        tick = state.get("tick", 0)
        is_degraded = bool(state.get("degraded", False))
    else:
        state = {}
        tick = 0
        is_degraded = False

    stations_compact = []
    all_risks = []

    for st in state.get("stations", []):
        inv = st.get("inventory", {})
        cap = st.get("capacity", {})
        st_risk = st.get("risk", {})
        st_id = st.get("id", "")

        # Flatten station risks into all_risks list for easy querying
        if isinstance(st_risk, dict):
            for fuel, rdata in st_risk.items():
                if isinstance(rdata, dict):
                    all_risks.append({
                        "station_id": st_id,
                        "fuel_type": fuel,
                        "level": rdata.get("level", "LOW"),
                        "hours_to_stockout": rdata.get("hours_to_stockout", 99.0),
                        "stockout_prob_horizon": rdata.get("stockout_prob_horizon", 0.0),
                        "stockout_prob_lead": rdata.get("stockout_prob_lead", 0.0),
                        "drivers": rdata.get("drivers", []),
                        "inventory": rdata.get("inventory", inv.get(fuel, 0.0)),
                        "inflight": rdata.get("inflight", 0.0),
                        "fill_pct": rdata.get("fill_pct", 0.0),
                    })

        stations_compact.append({
            "id": st_id,
            "name": st.get("name"),
            "inventory": inv,
            "capacity": cap,
            "risk": st_risk,
            "region_id": st.get("region_id"),
            "status": st.get("status", "ACTIVE"),
        })

    # Include explicit risks list from state if present
    for r in state.get("risks", []):
        if not any(
            ar.get("station_id") == r.get("station_id") and ar.get("fuel_type") == r.get("fuel_type")
            for ar in all_risks
        ):
            all_risks.append(r)

    # Sort all_risks by severity
    severity_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    all_risks.sort(
        key=lambda r: (severity_order.get(r.get("level", "LOW"), 4), r.get("hours_to_stockout", 99.0))
    )

    # Depots
    depots_compact = []
    for dp in state.get("depots", []):
        depots_compact.append({
            "id": dp.get("id"),
            "name": dp.get("name"),
            "inventory": dp.get("inventory", {}),
            "capacity": dp.get("capacity", {}),
            "status": dp.get("status", "OPEN"),
        })

    sim_run_id = int(state.get("sim_run_id") or 0)
    active_run_filter = sim_run_id if sim_run_id > 0 else None

    # 2. Open alerts (active run)
    try:
        alerts_res = await fetch_alerts(
            session,
            status="OPEN",
            sim_run_id=active_run_filter,
            limit=15,
        )
        alerts_data = alerts_res[0] if isinstance(alerts_res, tuple) else alerts_res
        alerts_compact = [
            {
                "id": a.get("id"),
                "severity": a.get("severity"),
                "station_id": a.get("station_id") or a.get("entity_id"),
                "fuel_type": a.get("fuel_type"),
                "title": a.get("title"),
                "category": a.get("category"),
            }
            for a in alerts_data
        ]
    except Exception as exc:
        log.warning("assistant.fetch_alerts_failed", error=str(exc))
        alerts_compact = []

    # 3. Last 10 decisions (active run)
    try:
        decisions_res = await fetch_decisions(
            session,
            sim_run_id=active_run_filter,
            limit=10,
        )
        decisions_data = decisions_res[0] if isinstance(decisions_res, tuple) else decisions_res
        decisions_compact = [
            {
                "id": d.get("id"),
                "status": d.get("status"),
                "policy": d.get("policy"),
                "confidence": d.get("confidence"),
                "explanation": d.get("explanation"),
            }
            for d in decisions_data
        ]
    except Exception as exc:
        log.warning("assistant.fetch_decisions_failed", error=str(exc))
        decisions_compact = []

    # 4. Open Incidents
    try:
        incidents_data = await fetch_incidents(session, limit=5)
        incidents_compact = [
            {
                "id": inc.get("id"),
                "component": inc.get("component"),
                "status": inc.get("status"),
                "started_at": inc.get("started_at"),
            }
            for inc in incidents_data
        ]
    except Exception as exc:
        log.warning("assistant.fetch_incidents_failed", error=str(exc))
        incidents_compact = []

    cur_tick = tick if tick else state.get("tick", 0)
    metrics = state.get("metrics", {})
    engine_mode = str(state.get("engine_mode", "NORMAL") or "NORMAL").upper()
    overall_degraded = bool(is_degraded or state.get("degraded") or engine_mode != "NORMAL")

    return {
        "state_summary": {
            "tick": cur_tick,
            "sim_run_id": state.get("sim_run_id", 1),
            "service_level": metrics.get("service_level", 1.0),
            "unmet_liters": metrics.get("unmet_liters", 0.0),
            "engine_mode": state.get("engine_mode", "NORMAL"),
            "policy": state.get("policy", "optimizer"),
        },
        "stations": stations_compact,
        "depots": depots_compact,
        "risks": all_risks,
        "alerts": alerts_compact,
        "decisions": decisions_compact,
        "incidents": incidents_compact,
        "events": state.get("events", []),
        "health": {
            "overall": "Degraded" if overall_degraded else "Healthy",
            "engine_mode": state.get("engine_mode", "NORMAL"),
            "tick": cur_tick,
        },
    }


# ---------------------------------------------------------------- Endpoints
@router.post("/assistant/ask", response_model=AskResponse)
async def ask_assistant(
    req: AskRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[ApiSettings, Depends(get_settings)],
    _user: Annotated[dict[str, Any], Depends(require_viewer)],
) -> AskResponse:
    """Grounded control-room assistant query endpoint (rate-limited 20/min)."""
    # 1. Enforce rate limit
    _check_assistant_rate_limit(request)

    # 2. Validate input
    clean_q = req.question.strip()
    if not clean_q or len(clean_q) > 500:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "VALIDATION_ERROR", "message": "Question must be between 1 and 500 characters"},
        )

    # 3. Build grounded context safely
    try:
        context = await _build_assistant_context(request, session)
    except Exception as exc:
        log.error("assistant.build_context_failed", error=str(exc))
        context = {
            "state_summary": {"tick": 0, "service_level": 1.0, "unmet_liters": 0.0, "engine_mode": "NORMAL"},
            "stations": [],
            "depots": [],
            "risks": [],
            "alerts": [],
            "decisions": [],
            "health": {"overall": "Degraded", "engine_mode": "NORMAL"},
        }

    # 4. Initialize LLM adapter
    explainer = LLMExplainer(
        api_key=settings.anthropic_api_key,
        model=settings.llm_model,
        timeout_sec=settings.llm_timeout_sec,
    )

    # 5. Answer grounded query with guaranteed fallback
    try:
        answer, engine_used, sources = await explainer.answer_assistant(clean_q, context)
    except Exception as exc:
        log.error("assistant.execution_failed_falling_back_to_rules", error=str(exc))
        answer, engine_used, sources = rules_based_assistant(clean_q, context)

    now_iso = datetime.now(UTC).isoformat().replace("+00:00", "Z")

    return AskResponse(
        answer=answer,
        engine=engine_used,
        sources=[SourceItem(type=s["type"], id=str(s["id"])) for s in sources],
        generated_at=now_iso,
    )


@router.get("/summary", response_model=SummaryResponse)
async def get_summary(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    window_ticks: Annotated[int, Query(ge=1, le=1000)] = 96,
) -> SummaryResponse:
    """Retrieve an executive shift summary over the specified window (default 96 ticks = 24 h)."""
    redis_client = getattr(request.app.state, "redis", None)
    state_res = await get_current_state(redis_client, session)
    if isinstance(state_res, tuple):
        state, tick, _, _ = state_res
    elif isinstance(state_res, dict):
        state = state_res
        tick = state.get("tick", 0)
    else:
        state = {}
        tick = 0

    # Compute metrics over the window
    metrics = state.get("metrics", {})
    service_level = float(metrics.get("service_level", 1.0))
    unmet_liters = float(metrics.get("unmet_liters", 0.0))

    # Query decisions in this window
    cur_tick = tick if tick else state.get("tick", 0)
    min_tick = max(0, cur_tick - window_ticks)

    q_dec = text(
        """
        SELECT id, status, approved_by, policy
        FROM decisions
        WHERE tick >= :min_tick
        """
    )
    try:
        res_dec = await session.execute(q_dec, {"min_tick": min_tick})
        dec_rows = res_dec.mappings().all()
    except Exception as exc:
        log.warning("summary.query_decisions_failed", error=str(exc))
        dec_rows = []

    decisions_count = len(dec_rows)
    autopilot_count = sum(1 for d in dec_rows if d.get("approved_by") == "autopilot")
    manual_count = sum(1 for d in dec_rows if d.get("approved_by") and d.get("approved_by") != "autopilot")

    # Volume dispatched
    q_vol = text(
        """
        SELECT COALESCE(SUM(quantity), 0.0) as total_vol
        FROM decision_lines dl
        JOIN decisions d ON dl.decision_id = d.id
        WHERE d.tick >= :min_tick AND d.status IN ('APPROVED', 'EXECUTED')
        """
    )
    res_vol = await session.execute(q_vol, {"min_tick": min_tick})
    total_dispatched = float(res_vol.scalar() or 0.0)

    # Alerts counts
    q_alerts = text(
        """
        SELECT status, COUNT(*) as cnt
        FROM alerts
        GROUP BY status
        """
    )
    res_alerts = await session.execute(q_alerts)
    alert_counts = {r["status"]: r["cnt"] for r in res_alerts.mappings().all()}
    alerts_opened = alert_counts.get("OPEN", 0) + alert_counts.get("ACKNOWLEDGED", 0)
    alerts_resolved = alert_counts.get("RESOLVED", 0)

    # Incidents count
    incidents = await fetch_incidents(session, limit=100)
    incidents_count = len(incidents)

    stats = {
        "window_ticks": window_ticks,
        "service_level": round(service_level, 4),
        "unmet_liters": round(unmet_liters, 1),
        "total_dispatched_liters": round(total_dispatched, 1),
        "decisions_count": decisions_count,
        "autopilot_count": autopilot_count,
        "manual_count": manual_count,
        "alerts_opened": alerts_opened,
        "alerts_resolved": alerts_resolved,
        "incidents_count": incidents_count,
        "active_policy": state.get("policy", "optimizer"),
    }

    # Render template narrative
    try:
        narrative = render_template("shift_summary.j2", stats)
    except Exception as exc:
        log.warning("summary.render_template_failed", error=str(exc))
        narrative = (
            f"Executive Shift Summary ({window_ticks} ticks): "
            f"Network service level is {service_level * 100:.2f}% with {int(unmet_liters):,} L total unmet demand. "
            f"Total fuel dispatched: {int(total_dispatched):,} L across {decisions_count} decisions. "
            f"Active alerts: {alerts_opened} open, {alerts_resolved} resolved. "
            f"Active policy: {state.get('policy', 'optimizer')}."
        )

    return SummaryResponse(
        text=narrative,
        engine="template",
        stats=stats,
    )
