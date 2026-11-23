"""Decision, recommendation, engine control, and audit API endpoints."""
from __future__ import annotations

from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_operator
from app.config import ApiSettings, get_settings
from app.db import (
    fetch_audit_logs,
    fetch_decision_by_id,
    fetch_decisions,
    fetch_recommendations,
    get_session,
    insert_audit_entry,
)

router = APIRouter(prefix="/api", tags=["decisions"])


# ---------------------------------------------------------------- Request Models
class ApproveRequest(BaseModel):
    line_ids: list[int] | None = None
    quantities: dict[str, float] | None = None
    reviewed_driver: bool | None = None


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
    session: Annotated[AsyncSession, Depends(get_session)],
    sim_run_id: int | None = Query(None, description="Optional sim_run_id filter"),
) -> dict[str, Any]:
    """Fetch active pending recommendations (decisions pending approval with lines)."""
    items = await fetch_recommendations(session, sim_run_id=sim_run_id)
    return {"items": items}


@router.get("/decisions")
async def list_decisions(
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

    engine_payload: dict[str, Any] = {
        "operator": actor,
        "selected_line_ids": req.line_ids,
        "modified_quantities": {int(k): float(v) for k, v in req.quantities.items()} if req.quantities else None,
    }

    await _call_engine(
        request=request,
        settings=settings,
        method="POST",
        path=f"/engine/decisions/{decision_id}/approve",
        json_data=engine_payload,
    )

    await insert_audit_entry(
        session=session,
        actor=actor,
        action="DECISION_APPROVED",
        entity_type="decision",
        entity_id=str(decision_id),
        payload=req.model_dump(exclude_none=True),
    )
    await session.commit()

    updated = await fetch_decision_by_id(session, decision_id=decision_id)
    return updated or {"status": "ok", "decision_id": decision_id, "approved_by": actor}


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
    user: Annotated[dict[str, Any], Depends(require_operator)],
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
