"""Decision, recommendation, engine control, and audit API endpoints."""
from __future__ import annotations

import os
import time
from typing import Annotated, Any, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_operator, require_supervisor, require_viewer
from app.config import ApiSettings, get_settings
from app.db import (
    fetch_audit_logs,
    fetch_decision_by_id,
    fetch_decisions,
    fetch_recommendations,
    get_session,
    insert_audit_entry,
)
from app.services.state_service import get_current_state

router = APIRouter(prefix="/api", tags=["decisions"])

_DECISIONS_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


def _get_cached(key: str) -> dict[str, Any] | None:
    now = time.time()
    if key in _DECISIONS_CACHE:
        exp, val = _DECISIONS_CACHE[key]
        if now < exp:
            return val
        del _DECISIONS_CACHE[key]
    return None


def _set_cached(key: str, val: dict[str, Any], ttl: float = 4.0) -> None:
    if len(_DECISIONS_CACHE) > 500:
        _DECISIONS_CACHE.clear()
    _DECISIONS_CACHE[key] = (time.time() + ttl, val)


def _invalidate_cache() -> None:
    _DECISIONS_CACHE.clear()


# ---------------------------------------------------------------- Request Models
class ApproveRequest(BaseModel):
    line_ids: list[int] | None = None
    quantities: dict[str, float] | None = None
    selected_line_ids: list[int] | None = None
    modified_quantities: dict[str, float] | None = None
    reviewed_driver: bool | None = None


class WhatIfLineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    route_id: str
    fuel_type: str
    quantity: float


class WhatIfEventRequest(BaseModel):
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


class WhatIfRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    horizon_ticks: int = Field(default=48, ge=1, le=96)
    lines: list[WhatIfLineRequest] = Field(default_factory=list, max_length=20)
    events: list[WhatIfEventRequest] = Field(default_factory=list, max_length=5)
    compare: list[Literal["do_nothing", "recommended", "custom"]] | None = None


class RejectRequest(BaseModel):
    reason_code: str | None = None
    comment: str | None = None


class PlanNowRequest(BaseModel):
    pass


class ControlModeRequest(BaseModel):
    mode: str = Field(..., description="Operating mode: advisory or autopilot")


class ControlPolicyRequest(BaseModel):
    policy: str = Field(..., description="Active policy: heuristic or optimizer")
    scarcity_policy: str | None = None


class ManualAllocationRequest(BaseModel):
    source_depot_id: str
    destination_station_id: str
    route_id: str
    fuel_type: str
    quantity: float
    comment: str | None = None


# ---------------------------------------------------------------- HTTP Helper
async def _call_engine(
    request: Request,
    settings: ApiSettings,
    method: str,
    path: str,
    json_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    url = f"{settings.engine_internal_url.rstrip('/')}{path}"
    headers = {"X-Internal-Token": settings.engine_internal_token}

    client = getattr(request.app.state, "http", None)
    own_client = False
    if client is None or client.is_closed:
        client = httpx.AsyncClient(timeout=5.0)
        own_client = True

    try:
        resp = await client.request(method, url, json=json_data, headers=headers)
        if resp.is_error:
            try:
                err_body = resp.json()
                detail = err_body.get("detail", err_body)
            except Exception:
                detail = {"code": "ENGINE_ERROR", "message": resp.text}
            raise HTTPException(status_code=resp.status_code, detail=detail)
        return resp.json()
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "ENGINE_UNAVAILABLE", "message": f"Could not reach engine service: {exc}"},
        ) from exc
    finally:
        if own_client:
            await client.aclose()


# ---------------------------------------------------------------- Recommendation & Decision Endpoints
@router.get("/recommendations")
async def get_recommendations(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    sim_run_id: int | None = Query(None, description="Optional sim_run_id filter"),
    all_runs: bool = Query(False, description="Set True to return recommendations across all historical runs"),
) -> dict[str, Any]:
    """Fetch active pending recommendations (decisions pending approval with lines)."""
    target_run_id = sim_run_id
    current_tick: int | None = None

    if target_run_id is None and not all_runs:
        redis_client = getattr(request.app.state, "redis", None)
        try:
            state, tick, _, _ = await get_current_state(redis_client, session)
            if state and state.get("sim_run_id"):
                target_run_id = int(state["sim_run_id"])
            current_tick = tick
        except Exception:
            pass

    items = await fetch_recommendations(
        session,
        sim_run_id=target_run_id if not all_runs else None,
        current_tick=current_tick,
    )
    return {"items": items}


@router.get("/decisions")
async def list_decisions(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    sim_run_id: int | None = Query(None),
    status: str | None = Query(None),
    policy: str | None = Query(None),
    from_tick: int | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """Fetch paginated decisions with optional filters."""
    items, total = await fetch_decisions(
        session=session,
        sim_run_id=sim_run_id,
        status=status,
        policy=policy,
        from_tick=from_tick,
        limit=limit,
        offset=offset,
    )
    return {"items": items, "total": total}


@router.get("/decisions/metrics")
async def decision_metrics(
    session: Annotated[AsyncSession, Depends(get_session)],
    sim_run_id: int | None = Query(None),
) -> dict[str, Any]:
    """Human-AI teaming metrics for the Recommendations panel (DEF-014)."""
    from app.db import fetch_decision_metrics

    return await fetch_decision_metrics(session, sim_run_id=sim_run_id)


@router.get("/decisions/{decision_id}")
async def get_decision(
    decision_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """Fetch a single decision by ID with lines and outcome."""
    item = await fetch_decision_by_id(session, decision_id=decision_id)
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "DECISION_NOT_FOUND", "message": f"Decision {decision_id} not found"},
        )
    return item


@router.post("/decisions/{decision_id}/approve")
async def approve_decision(
    decision_id: int,
    req: ApproveRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[dict[str, Any], Depends(require_operator)],
    settings: Annotated[ApiSettings, Depends(get_settings)],
) -> dict[str, Any]:
    """Approve a decision recommendation, forward to engine, and record audit log."""
    actor = user.get("actor") or user.get("sub") or "operator"

    sel_line_ids = req.selected_line_ids if req.selected_line_ids is not None else req.line_ids
    raw_quantities = req.modified_quantities if req.modified_quantities is not None else req.quantities

    parsed_quantities: dict[int, float] | None = None
    if raw_quantities is not None:
        parsed_quantities = {}
        for k, v in raw_quantities.items():
            try:
                lid = int(k)
            except (TypeError, ValueError) as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={"code": "INVALID_LINE_SELECTION", "message": f"Invalid line ID '{k}'"},
                ) from exc
            try:
                qty = float(v)
            except (TypeError, ValueError) as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={"code": "INVALID_QUANTITY", "message": f"Invalid quantity for line {lid}: {v}"},
                ) from exc
            if qty != qty or qty in (float("inf"), float("-inf")) or qty <= 0.0:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "code": "INVALID_QUANTITY",
                        "message": f"Modified quantity for line {lid} must be a positive finite number (got {qty})",
                    },
                )
            parsed_quantities[lid] = qty

    if sel_line_ids is not None and len(sel_line_ids) == 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "NO_LINES_SELECTED",
                "message": "At least one allocation line must be selected for approval.",
            },
        )

    # F-TRUST Cognitive Forcing check (12_ADVANCED_FEATURES.md / Phase 09 §4.5)
    existing = await fetch_decision_by_id(session, decision_id=decision_id)
    if existing:
        lines = existing.get("lines") or []
        valid_line_ids = {int(ln["id"]) for ln in lines if ln.get("id") is not None}

        if sel_line_ids is not None and valid_line_ids:
            unknown_sel = [lid for lid in sel_line_ids if lid not in valid_line_ids]
            if unknown_sel:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "code": "INVALID_LINE_SELECTION",
                        "message": f"Selected line IDs do not belong to decision {decision_id}: {unknown_sel}",
                    },
                )

        if parsed_quantities is not None and valid_line_ids:
            unknown_mod = [lid for lid in parsed_quantities if lid not in valid_line_ids]
            if unknown_mod:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "code": "INVALID_LINE_SELECTION",
                        "message": f"Modified line IDs do not belong to decision {decision_id}: {unknown_mod}",
                    },
                )

        conf = existing.get("confidence")
        conf_val = float(conf) if conf is not None else 1.0
        is_deg = bool(existing.get("degraded", False))
        selected_set = set(sel_line_ids) if sel_line_ids is not None else None
        max_q = 0.0
        for ln in lines:
            lid_int = ln.get("id")
            if selected_set is not None and lid_int not in selected_set:
                continue
            q = float(ln.get("quantity", 0.0))
            if parsed_quantities and lid_int in parsed_quantities:
                q = parsed_quantities[int(lid_int)]
            max_q = max(max_q, q)
        if (max_q > 5000.0 or conf_val < 0.7 or is_deg) and not req.reviewed_driver:
            msg = (
                "Cognitive forcing review required: you must expand Why and review "
                "the main risk driver before approving this recommendation."
            )
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "REVIEW_REQUIRED",
                    "message": msg,
                },
            )

    engine_payload: dict[str, Any] = {
        "operator": actor,
        "selected_line_ids": sel_line_ids,
        "modified_quantities": parsed_quantities,
        "reviewed_driver": req.reviewed_driver,
    }

    engine_res = await _call_engine(
        request=request,
        settings=settings,
        method="POST",
        path=f"/engine/decisions/{decision_id}/approve",
        json_data=engine_payload,
    )

    try:
        dec_status = engine_res.get("decision_status") or engine_res.get("status")
        is_awaiting = dec_status == "AWAITING_SECOND_APPROVAL"
        action_name = "DECISION_FIRST_APPROVAL" if is_awaiting else "DECISION_APPROVED"
        await insert_audit_entry(
            session=session,
            actor=actor,
            action=action_name,
            entity_type="decision",
            entity_id=str(decision_id),
            payload={**req.model_dump(exclude_none=True), "status": dec_status},
        )
        await session.commit()
    except Exception:
        pass
    _invalidate_cache()

    try:
        updated = await fetch_decision_by_id(session, decision_id=decision_id)
        if updated:
            return updated
    except Exception:
        pass

    return engine_res or {"status": "APPROVED", "decision_id": decision_id, "approved_by": actor}


@router.post("/decisions/{decision_id}/reject")
async def reject_decision(
    decision_id: int,
    req: RejectRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[dict[str, Any], Depends(require_operator)],
    settings: Annotated[ApiSettings, Depends(get_settings)],
) -> dict[str, Any]:
    """Reject a decision recommendation, forward to engine, and record audit log."""
    actor = user.get("actor") or user.get("sub") or "operator"

    reason = req.comment or req.reason_code or "Rejected by operator"
    if req.reason_code and req.comment:
        reason = f"{req.reason_code}: {req.comment}"

    engine_payload = {
        "operator": actor,
        "reason": reason,
    }

    await _call_engine(
        request=request,
        settings=settings,
        method="POST",
        path=f"/engine/decisions/{decision_id}/reject",
        json_data=engine_payload,
    )

    await insert_audit_entry(
        session=session,
        actor=actor,
        action="DECISION_REJECTED",
        entity_type="decision",
        entity_id=str(decision_id),
        payload=req.model_dump(exclude_none=True),
    )
    await session.commit()
    _invalidate_cache()

    updated = await fetch_decision_by_id(session, decision_id=decision_id)
    return updated or {"status": "ok", "decision_id": decision_id, "rejected_reason": reason}


# ---------------------------------------------------------------- Engine Control Endpoints
@router.post("/plan-now", status_code=status.HTTP_202_ACCEPTED)
async def plan_now(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[dict[str, Any], Depends(require_operator)],
    settings: Annotated[ApiSettings, Depends(get_settings)],
) -> dict[str, Any]:
    """Trigger an immediate planning cycle in the engine."""
    actor = user.get("actor") or user.get("sub") or "operator"

    engine_res = await _call_engine(
        request=request,
        settings=settings,
        method="POST",
        path="/engine/plan-now",
        json_data={},
    )

    await insert_audit_entry(
        session=session,
        actor=actor,
        action="PLAN_NOW_TRIGGERED",
        entity_type="engine",
        entity_id=None,
        payload=engine_res,
    )
    await session.commit()

    return {"accepted": True, "details": engine_res}


@router.post("/control/mode")
async def set_operating_mode(
    req: ControlModeRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[dict[str, Any], Depends(require_operator)],
    settings: Annotated[ApiSettings, Depends(get_settings)],
) -> dict[str, Any]:
    """Switch operating mode (advisory vs autopilot)."""
    actor = user.get("actor") or user.get("sub") or "operator"

    engine_res = await _call_engine(
        request=request,
        settings=settings,
        method="POST",
        path="/engine/control/mode",
        json_data={"mode": req.mode},
    )

    await insert_audit_entry(
        session=session,
        actor=actor,
        action="ENGINE_MODE_CHANGED",
        entity_type="engine",
        entity_id=None,
        payload={"mode": req.mode},
    )
    await session.commit()

    return engine_res


@router.post("/control/policy")
async def set_operating_policy(
    req: ControlPolicyRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[dict[str, Any], Depends(require_supervisor)],
    settings: Annotated[ApiSettings, Depends(get_settings)],
) -> dict[str, Any]:
    """Switch decision policy (heuristic vs optimizer)."""
    actor = user.get("actor") or user.get("sub") or "operator"

    engine_res = await _call_engine(
        request=request,
        settings=settings,
        method="POST",
        path="/engine/control/policy",
        json_data={"policy": req.policy},
    )

    await insert_audit_entry(
        session=session,
        actor=actor,
        action="ENGINE_POLICY_CHANGED",
        entity_type="engine",
        entity_id=None,
        payload={"policy": req.policy, "scarcity_policy": req.scarcity_policy},
    )
    await session.commit()

    return engine_res


@router.post("/manual-allocation", status_code=status.HTTP_201_CREATED)
async def manual_allocation(
    req: ManualAllocationRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[dict[str, Any], Depends(require_operator)],
    settings: Annotated[ApiSettings, Depends(get_settings)],
) -> dict[str, Any]:
    """Dispatch a manual operator allocation."""
    actor = user.get("actor") or user.get("sub") or "operator"

    engine_res = await _call_engine(
        request=request,
        settings=settings,
        method="POST",
        path="/engine/manual-allocation",
        json_data={
            "source_depot_id": req.source_depot_id,
            "destination_station_id": req.destination_station_id,
            "route_id": req.route_id,
            "fuel_type": req.fuel_type,
            "quantity": req.quantity,
            "reason": req.comment or "Operator manual dispatch",
            "operator": actor,
        },
    )

    dec_id = engine_res.get("decision_id")
    await insert_audit_entry(
        session=session,
        actor=actor,
        action="MANUAL_ALLOCATION_DISPATCHED",
        entity_type="decision",
        entity_id=str(dec_id) if dec_id is not None else None,
        payload=req.model_dump(exclude_none=True),
    )
    await session.commit()

    return engine_res


# ---------------------------------------------------------------- Audit Log Endpoint
@router.get("/audit")
async def get_audit_logs(
    session: Annotated[AsyncSession, Depends(get_session)],
    action: str | None = Query(None),
    actor: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """Retrieve audit log entries ordered newest first."""
    logs = await fetch_audit_logs(
        session=session,
        action=action,
        actor=actor,
        limit=limit,
        offset=offset,
    )
    return {"items": logs}


# ---------------------------------------------------------------- What-if Endpoint
# Canonical path per 21_API_CONTRACTS.md §2.4 is /api/whatif; alias /api/decisions/what-if preserved for compatibility
@router.post("/whatif")
@router.post("/decisions/what-if")
async def post_whatif(
    req: WhatIfRequest,
    request: Request,
    settings: Annotated[ApiSettings, Depends(get_settings)],
    _user: Annotated[dict, Depends(require_viewer)],
) -> dict[str, Any]:
    """Execute what-if forward scenario simulations via ShadowTwin (Task 8.7)."""
    return await _call_engine(
        request=request,
        settings=settings,
        method="POST",
        path="/internal/whatif",
        json_data=req.model_dump(exclude_none=True),
    )


# ---------------------------------------------------------------- Break-glass Endpoints (Phase 10: F-BREAKGLASS)
@router.get("/breakglass/plan.csv")
async def get_breakglass_plan_csv(
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[ApiSettings, Depends(get_settings)],
) -> Response:
    """Retrieve raw CSV emergency dispatch plan for break-glass operations."""
    csv_file = os.path.join(settings.breakglass_dir, "latest.csv")
    if os.path.exists(csv_file):
        try:
            with open(csv_file, encoding="utf-8") as f:
                content = f.read()
            return Response(
                content=content,
                media_type="text/csv",
                headers={"Content-Disposition": 'attachment; filename="latest.csv"'},
            )
        except Exception:
            pass

    # Fallback to DB query for latest decision
    items, _ = await fetch_decisions(session=session, limit=1)
    lines: list[str] = ["depot,station,route,fuel,quantity,reason,generated_tick"]
    if items:
        latest = items[0]
        cur_tick = latest.get("tick", 0)
        for ln in latest.get("lines", []):
            d = ln.get("source_depot_id", "")
            s = ln.get("destination_station_id", "")
            r = ln.get("route_id", "")
            f = ln.get("fuel_type", "")
            q = ln.get("quantity", 0.0)
            reason = ln.get("reason", "")
            lines.append(f"{d},{s},{r},{f},{q},{reason},{cur_tick}")

    csv_data = "\n".join(lines) + "\n"
    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="latest.csv"'},
    )


@router.get("/breakglass/plan.html")
async def get_breakglass_plan_html(
    settings: Annotated[ApiSettings, Depends(get_settings)],
) -> Response:
    """Retrieve static HTML emergency dispatch plan for break-glass operations."""
    html_file = os.path.join(settings.breakglass_dir, "latest.html")
    if os.path.exists(html_file):
        try:
            with open(html_file, encoding="utf-8") as f:
                content = f.read()
            return Response(content=content, media_type="text/html")
        except Exception:
            pass

    fallback_html = (
        "<html><body><h2>FSIP Break-Glass Plan</h2><p>No static emergency plan generated yet.</p></body></html>"
    )
    return Response(content=fallback_html, media_type="text/html")

