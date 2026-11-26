"""Station and depot entity detail endpoints."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import (
    _parse_json_field,
    fetch_recent_snapshots,
    fetch_station_demand_history,
    get_session,
)
from app.services.state_service import get_current_state

router = APIRouter(prefix="/api", tags=["entities"])


@router.get("/stations/{station_id}")
async def get_station_detail(
    station_id: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    history_ticks: Annotated[int, Query(ge=1, le=500)] = 96,
) -> Response:
    """Retrieve station details, inventory history, recent demand, and forecasts."""
    redis_client = getattr(request.app.state, "redis", None)
    state, tick, is_degraded, _ = await get_current_state(redis_client, session)

    stations = state.get("stations", [])
    station = next((s for s in stations if s.get("id") == station_id), None)
    if not station:
        raise HTTPException(
            status_code=404,
            detail={"code": "STATION_NOT_FOUND", "message": f"Station '{station_id}' not found"},
        )

    active_run = state.get("sim_run_id")
    active_run_id = int(active_run) if active_run is not None else None

    # Fetch demand observations (scoped to active sim run — DEF-021)
    demand = await fetch_station_demand_history(
        session, station_id=station_id, limit=history_ticks, sim_run_id=active_run_id
    )

    # Fetch inventory history from recent snapshots (active-run scoped)
    snapshots = await fetch_recent_snapshots(
        session, limit=history_ticks, sim_run_id=active_run_id
    )
    history: list[dict[str, Any]] = []
    for snap in snapshots:
        raw_st = _parse_json_field(snap.get("stations"))
        if isinstance(raw_st, dict):
            st_list = list(raw_st.values())
        elif isinstance(raw_st, list):
            st_list = raw_st
        else:
            st_list = []

        st_match = next((s for s in st_list if isinstance(s, dict) and s.get("id") == station_id), None)
        if st_match:
            history.append({
                "tick": snap.get("tick", 0),
                "inventory": st_match.get("inventory", {}),
            })

    content = {
        "station": station,
        "history": history,
        "demand": demand,
        "forecast": [],
    }
    return JSONResponse(
        content=content,
        headers={
            "X-Data-Tick": str(tick),
            "X-Degraded": "true" if is_degraded else "false",
        },
    )


@router.get("/depots/{depot_id}")
async def get_depot_detail(
    depot_id: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    history_ticks: Annotated[int, Query(ge=1, le=500)] = 96,
) -> Response:
    """Retrieve depot details, inventory history, and supply projections."""
    redis_client = getattr(request.app.state, "redis", None)
    state, tick, is_degraded, _ = await get_current_state(redis_client, session)

    depots = state.get("depots", [])
    depot = next((d for d in depots if d.get("id") == depot_id), None)
    if not depot:
        raise HTTPException(
            status_code=404,
            detail={"code": "DEPOT_NOT_FOUND", "message": f"Depot '{depot_id}' not found"},
        )

    active_run = state.get("sim_run_id")
    active_run_id = int(active_run) if active_run is not None else None

    # Fetch inventory history from recent snapshots (active-run scoped — DEF-021)
    snapshots = await fetch_recent_snapshots(
        session, limit=history_ticks, sim_run_id=active_run_id
    )
    history: list[dict[str, Any]] = []
    for snap in snapshots:
        raw_dp = _parse_json_field(snap.get("depots"))
        if isinstance(raw_dp, dict):
            dp_list = list(raw_dp.values())
        elif isinstance(raw_dp, list):
            dp_list = raw_dp
        else:
            dp_list = []

        dp_match = next((d for d in dp_list if isinstance(d, dict) and d.get("id") == depot_id), None)
        if dp_match:
            history.append({
                "tick": snap.get("tick", 0),
                "inventory": dp_match.get("inventory", {}),
            })

    content = {
        "depot": depot,
        "history": history,
        "projection": [],
    }
    return JSONResponse(
        content=content,
        headers={
            "X-Data-Tick": str(tick),
            "X-Degraded": "true" if is_degraded else "false",
        },
    )
