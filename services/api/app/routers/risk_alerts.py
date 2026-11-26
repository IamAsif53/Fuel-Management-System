"""Risk, Alert, Forecast, Cover, and Incident API endpoints.

Implements Task 6.7 per 21_API_CONTRACTS.md and 12_ADVANCED_FEATURES.md.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

import orjson
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_operator
from app.db import (
    ack_alert,
    fetch_alerts,
    fetch_forecast_series,
    fetch_incidents,
    fetch_reconciliation_report,
    get_session,
)
from app.services.state_service import get_current_state

router = APIRouter(prefix="/api", tags=["risk_alerts"])

LEVEL_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
# Baseline daily demand from simulator profiles (mirpur+tongi+karnaphuli+cox):
# DIESEL 40.2k + PETROL 33.6k + OCTANE 17.6k ≈ 91.4k L/day network (DEF-020).
NOMINAL_DAILY_DEMAND = {"DIESEL": 40200.0, "PETROL": 33600.0, "OCTANE": 17600.0}


def _normalize_region_id(val: str | None) -> str:
    """Normalize region IDs so both 'region-dhaka' and 'dhaka' filter forms match."""
    if not val:
        return ""
    s = val.strip().lower()
    return s[7:] if s.startswith("region-") else s


@router.get("/risk")
async def get_risk(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    region_id: Annotated[str | None, Query()] = None,
    fuel: Annotated[str | None, Query()] = None,
    min_level: Annotated[str | None, Query()] = None,
) -> Response:
    """Retrieve station risk assessments sorted CRITICAL->LOW then hours ascending."""
    redis_client = getattr(request.app.state, "redis", None)
    state, tick, is_degraded, _ = await get_current_state(redis_client, session)

    stations = state.get("stations", [])
    rows: list[dict[str, Any]] = []

    target_fuel = fuel.upper() if fuel else None
    target_min_order = LEVEL_ORDER.get(min_level.upper(), 99) if min_level else 99
    target_region = _normalize_region_id(region_id)

    for st in stations:
        st_region = st.get("region_id")
        if target_region and _normalize_region_id(st_region) != target_region:
            continue

        risk_map = st.get("risk", {})
        inventory_map = st.get("inventory", {})

        for f_type, r_info in risk_map.items():
            if target_fuel and f_type.upper() != target_fuel:
                continue

            lvl = str(r_info.get("level") or "LOW").upper()
            order = LEVEL_ORDER.get(lvl, 3)
            if min_level and order > target_min_order:
                continue

            raw_hours = r_info.get("hours_to_stockout")
            hours = float(raw_hours) if raw_hours is not None else 999.0
            p_lead = float(r_info.get("stockout_prob_lead") or 0.0)
            p_horizon = float(r_info.get("stockout_prob_horizon") or 0.0)
            raw_prob = r_info.get("stockout_prob")
            prob = float(raw_prob) if raw_prob is not None else max(p_lead, p_horizon)
            drivers = r_info.get("drivers", [])

            rows.append(
                {
                    "station_id": st.get("id"),
                    "station_name": st.get("name"),
                    "region_id": st_region,
                    "fuel_type": f_type,
                    "risk_level": lvl,
                    "hours_to_stockout": hours,
                    "stockout_prob": prob,
                    "stockout_prob_lead": p_lead,
                    "stockout_prob_horizon": p_horizon,
                    "cover_hours": r_info.get("cover_hours"),
                    "inventory": float(inventory_map.get(f_type, 0.0)),
                    "drivers": drivers,
                }
            )

    # Sort CRITICAL -> LOW (ascending order integer), then hours ascending, then highest prob
    rows.sort(key=lambda x: (LEVEL_ORDER.get(x["risk_level"], 99), x["hours_to_stockout"], -x["stockout_prob"]))

    content = {"tick": tick, "rows": rows}
    return JSONResponse(
        content=content,
        headers={
            "X-Data-Tick": str(tick),
            "X-Degraded": "true" if is_degraded else "false",
        },
    )


@router.get("/alerts")
async def get_alerts(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    status: Annotated[str, Query()] = "OPEN",
    category: Annotated[str | None, Query()] = None,
    severity: Annotated[str | None, Query()] = None,
    sim_run_id: Annotated[int | None, Query()] = None,
    all_runs: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict[str, Any]:
    """Retrieve alerts scoped to active simulation run by default."""
    redis_client = getattr(request.app.state, "redis", None)
    target_run_id = sim_run_id
    state: dict[str, Any] = {}

    if target_run_id is None and not all_runs:
        try:
            state, _, _, _ = await get_current_state(redis_client, session)
            active_run = int(state.get("sim_run_id") or 0)
            if active_run > 0:
                target_run_id = active_run
        except Exception:
            target_run_id = None

    fetch_kwargs: dict[str, Any] = {
        "session": session,
        "status": status,
        "category": category,
        "severity": severity,
        "limit": limit,
    }
    if target_run_id is not None:
        fetch_kwargs["sim_run_id"] = target_run_id

    items, total = await fetch_alerts(**fetch_kwargs)

    # Fallback to in-memory state alerts if DB returned 0 and status is OPEN
    if total == 0 and status.upper() == "OPEN" and not all_runs:
        if not state:
            try:
                state, _, _, _ = await get_current_state(redis_client, session)
            except Exception:
                state = {}
        state_alerts = state.get("alerts_open", [])
        filtered = []
        for a in state_alerts:
            if target_run_id is not None and a.get("sim_run_id") not in (None, target_run_id):
                continue
            if category and a.get("category") != category:
                continue
            if severity and a.get("severity") != severity.upper():
                continue
            filtered.append(a)
        if filtered:
            return {"items": filtered[:limit], "total": len(filtered)}

    return {"items": items, "total": total}


@router.post("/alerts/{alert_id}/ack")
async def acknowledge_alert(
    alert_id: int,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    actor_info: Annotated[dict[str, Any], Depends(require_operator)],
) -> dict[str, Any]:
    """Operator acknowledges an open alert."""
    actor = actor_info.get("actor", "operator")
    updated = await ack_alert(session, alert_id=alert_id, actor=actor)
    if not updated:
        raise HTTPException(
            status_code=404,
            detail={"code": "ALERT_NOT_FOUND", "message": f"Alert {alert_id} not found"},
        )
    redis_client = getattr(request.app.state, "redis", None)
    if redis_client is not None:
        try:
            now_iso = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            await redis_client.xadd(
                "fsip:domain-events",
                {
                    "type": "alert.acknowledged",
                    "at": now_iso,
                    "sim_run_id": str(updated.get("sim_run_id") or 0),
                    "tick": str(updated.get("opened_tick") or 0),
                    "data": orjson.dumps(updated).decode("utf-8"),
                },
                maxlen=20000,
                approximate=True,
            )
        except Exception:
            pass
    return updated


@router.get("/forecast")
async def get_forecast(
    session: Annotated[AsyncSession, Depends(get_session)],
    station_id: Annotated[str, Query()],
    fuel: Annotated[str, Query()],
    horizon: Annotated[int, Query(ge=1, le=200)] = 48,
    history: Annotated[int, Query(ge=1, le=500)] = 96,
) -> dict[str, Any]:
    """Retrieve forecast series with historical demand and p10/p50/p90 intervals."""
    return await fetch_forecast_series(
        session=session,
        station_id=station_id,
        fuel_type=fuel.upper(),
        horizon=horizon,
        history=history,
    )


@router.get("/cover")
async def get_cover(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """Retrieve network and regional fuel cover metrics per F-COVER specification."""
    redis_client = getattr(request.app.state, "redis", None)
    state, tick, is_degraded, _ = await get_current_state(redis_client, session)

    depots = state.get("depots", [])
    stations = state.get("stations", [])
    allocations = state.get("allocations_active", [])
    scarcity_flags = state.get("scarcity", {})

    fuels = ["DIESEL", "PETROL", "OCTANE"]

    # Compute network totals
    net_available = {f: 0.0 for f in fuels}
    net_demand_96 = {f: 0.0 for f in fuels}

    # Region aggregations
    region_available: dict[str, dict[str, float]] = {}
    region_demand_96: dict[str, dict[str, float]] = {}

    for d in depots:
        r_id = d.get("region_id", "unknown")
        region_available.setdefault(r_id, {f: 0.0 for f in fuels})
        region_demand_96.setdefault(r_id, {f: 0.0 for f in fuels})
        for f, qty in d.get("inventory", {}).items():
            if f in net_available:
                net_available[f] += float(qty)
                region_available[r_id][f] += float(qty)

    for s in stations:
        r_id = s.get("region_id", "unknown")
        region_available.setdefault(r_id, {f: 0.0 for f in fuels})
        region_demand_96.setdefault(r_id, {f: 0.0 for f in fuels})
        mult = float(s.get("demand_multiplier", 1.0) or 1.0)
        for f, qty in s.get("inventory", {}).items():
            if f in net_available:
                net_available[f] += float(qty)
                region_available[r_id][f] += float(qty)

        for f in fuels:
            st_d96 = NOMINAL_DAILY_DEMAND[f] * mult
            net_demand_96[f] += st_d96
            region_demand_96[r_id][f] += st_d96

    # In-flight allocations
    for alloc in allocations:
        f = alloc.get("fuel_type")
        qty = float(alloc.get("quantity", 0.0))
        if f in net_available:
            net_available[f] += qty

    # Calculate cover days
    network_cover: dict[str, dict[str, float]] = {}
    calculated_scarcity: dict[str, bool] = {}

    for f in fuels:
        d96 = net_demand_96[f]
        avail = net_available[f]
        cover_days = round(avail / max(1.0, d96), 2)
        network_cover[f] = {
            "available": round(avail, 1),
            "demand_96": round(d96, 1),
            "cover_days": cover_days,
        }
        calculated_scarcity[f] = bool(scarcity_flags.get(f, cover_days < 3.0))

    regions_cover: dict[str, dict[str, dict[str, float]]] = {}
    for r_id in region_available:
        regions_cover[r_id] = {}
        for f in fuels:
            avail = region_available[r_id][f]
            d96 = region_demand_96[r_id][f]
            cover_days = round(avail / max(1.0, d96), 2)
            regions_cover[r_id][f] = {
                "available": round(avail, 1),
                "demand_96": round(d96, 1),
                "cover_days": cover_days,
            }

    content = {
        "network": network_cover,
        "regions": regions_cover,
        "supply_ends_tick": 212,
        "scarcity": calculated_scarcity,
    }
    return JSONResponse(
        content=content,
        headers={
            "X-Data-Tick": str(tick),
            "X-Degraded": "true" if is_degraded else "false",
        },
    )


@router.get("/incidents")
async def get_incidents(
    session: Annotated[AsyncSession, Depends(get_session)],
    status: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict[str, Any]:
    """Retrieve system and component incidents."""
    items = await fetch_incidents(session=session, status=status, limit=limit)
    return {"items": items}


@router.get("/reports/reconciliation")
async def get_reconciliation(
    session: Annotated[AsyncSession, Depends(get_session)],
    from_tick: Annotated[int | None, Query()] = None,
    to_tick: Annotated[int | None, Query()] = None,
) -> Response:
    """Retrieve allocation reconciliation and fuel loss report (F-RECON)."""
    report = await fetch_reconciliation_report(session, from_tick=from_tick, to_tick=to_tick)
    return JSONResponse(content=report)
