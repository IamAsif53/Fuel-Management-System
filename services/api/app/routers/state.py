"""State endpoint router (/api/state)."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import fetch_available_ticks, fetch_snapshot_by_tick, get_session
from app.services.state_service import get_current_state

router = APIRouter(prefix="/api", tags=["state"])


@router.get("/state")
async def get_state_endpoint(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    if_none_match: Annotated[str | None, Header(alias="If-None-Match")] = None,
) -> Response:
    """Retrieve full live WorldState projection with 304 ETag caching and DB fallback."""
    redis_client = getattr(request.app.state, "redis", None)
    state, tick, is_degraded, etag = await get_current_state(redis_client, session)

    headers = {
        "X-Data-Tick": str(tick),
        "X-Degraded": "true" if is_degraded else "false",
        "ETag": etag,
    }

    # Evaluate If-None-Match header
    if if_none_match:
        client_tag = if_none_match.strip()
        server_tag = etag.strip()
        raw_server_tag = server_tag.strip('"')
        raw_client_tag = client_tag.strip('"')

        if client_tag == server_tag or raw_client_tag == raw_server_tag:
            return Response(status_code=304, headers=headers)

    return JSONResponse(content=state, headers=headers)


@router.get("/state/history")
async def get_state_history_endpoint(
    session: Annotated[AsyncSession, Depends(get_session)],
    tick: Annotated[int, Query(description="Simulation tick to retrieve")],
    sim_run_id: Annotated[int | None, Query(description="Optional simulation run ID")] = None,
) -> Response:
    """Retrieve historical WorldState snapshot for time-travel replay (Phase 17)."""
    snapshot = await fetch_snapshot_by_tick(session, tick=tick, sim_run_id=sim_run_id)
    if not snapshot:
        msg = f"No historical snapshot found for tick {tick}"
        if sim_run_id:
            msg += f" and sim_run_id {sim_run_id}"
        raise HTTPException(
            status_code=404,
            detail={
                "code": "SNAPSHOT_NOT_FOUND",
                "message": msg,
            },
        )
    headers = {
        "X-Data-Tick": str(tick),
        "X-Historical-Replay": "true",
    }
    return JSONResponse(content=snapshot, headers=headers)


@router.get("/state/history/ticks")
async def get_available_ticks_endpoint(
    session: Annotated[AsyncSession, Depends(get_session)],
    sim_run_id: Annotated[int | None, Query(description="Optional simulation run ID")] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 500,
) -> dict[str, Any]:
    """List available recorded simulation ticks for time-travel navigation (Phase 17)."""
    ticks = await fetch_available_ticks(session, sim_run_id=sim_run_id, limit=limit)
    return {"ticks": ticks, "total": len(ticks)}
