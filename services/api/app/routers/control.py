"""Simulator control, crisis presets, and chaos drill API endpoints.

Implements spec 21_API_CONTRACTS.md §2.8 and 22_UI_SPEC.md §2.10.
Respects DEMO_CONTROLS flag, requires operator authorization, and writes audit logs.
"""
from __future__ import annotations

from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fsip_common.logging import get_logger
from pydantic import BaseModel, Field

from app.auth import require_operator
from app.config import ApiSettings, get_settings
from app.db import fetch_audit_logs, get_sessionmaker, insert_audit_entry

router = APIRouter(prefix="/api/control", tags=["control"])
log = get_logger()

SUPPORTED_PRESETS = {
    "demand_spike_dhaka": {
        "title": "Demand Spike — Dhaka",
        "description": "Surges retail customer demand in Dhaka region by 1.8× for 24 ticks (6 hours)",
        "events": [
            {
                "type": "demand_spike",
                "duration_ticks": 24,
                "parameters": {"multiplier": 1.8, "region_ids": ["region-dhaka"]},
            }
        ],
    },
    "route_cut_mirpur": {
        "title": "Route Cut — Mirpur",
        "description": "Completely blocks route-gazipur-mirpur for 24 ticks (6 hours)",
        "events": [
            {
                "type": "route_disruption",
                "duration_ticks": 24,
                "parameters": {"route_ids": ["route-gazipur-mirpur"]},
            }
        ],
    },
    "depot_constraint_patiya": {
        "title": "Depot Constraint — Patiya",
        "description": "Restricts Patiya Depot throughput and loading capacity to 50% for 24 ticks",
        "events": [
            {
                "type": "depot_constraint",
                "duration_ticks": 24,
                "parameters": {"depot_ids": ["depot-patiya"]},
            }
        ],
    },
    "shipment_delay_gazipur": {
        "title": "Shipment Delay — Gazipur",
        "description": "Delays all outbound consignments from Gazipur Depot by +8 ticks (2 hours)",
        "events": [
            {
                "type": "shipment_delay",
                "duration_ticks": 24,
                "parameters": {"depot_ids": ["depot-gazipur"], "delay_ticks": 8},
            }
        ],
    },
    "supply_shortfall": {
        "title": "Supply Shortfall",
        "description": "Halves incoming replenishment pipeline across all regional hubs (50% shortfall)",
        "events": [
            {
                "type": "supply_shortfall",
                "duration_ticks": 24,
                "parameters": {"factor": 0.5},
            }
        ],
    },
    "station_outage_tongi": {
        "title": "Station Outage — Tongi",
        "description": "Simulates complete operational shutdown at Tongi Industrial Station for 12 ticks",
        "events": [
            {
                "type": "station_outage",
                "duration_ticks": 12,
                "parameters": {"station_ids": ["station-tongi"]},
            }
        ],
    },
    "single_route_cut": {
        "title": "Dual Corridor Disruption",
        "description": "Simultaneously isolates Tongi and Cox's Bazar distribution corridors for 40 ticks",
        "events": [
            {
                "type": "route_disruption",
                "duration_ticks": 40,
                "parameters": {"route_ids": ["route-gazipur-tongi", "route-patiya-coxsbazar"]},
            }
        ],
    },
    "combined_crisis": {
        "title": "Combined Multi-Vector Crisis",
        "description": "Simultaneous demand spike (1.8×), corridor cut (Mirpur), and Gazipur depot delay (+8 ticks)",
        "events": [
            {
                "type": "demand_spike",
                "duration_ticks": 24,
                "parameters": {"multiplier": 1.8, "region_ids": ["region-dhaka"]},
            },
            {
                "type": "route_disruption",
                "duration_ticks": 24,
                "parameters": {"route_ids": ["route-gazipur-mirpur"]},
            },
            {
                "type": "shipment_delay",
                "duration_ticks": 24,
                "parameters": {"depot_ids": ["depot-gazipur"], "delay_ticks": 8},
            },
        ],
    },
}

SUPPORTED_FAULTS = {
    "unavailable": "Simulator API Unavailable (HTTP 503)",
    "error_rate": "30% Injected Transient Errors",
    "latency": "1.5s Network Delay Injection",
    "stale_data": "Stale Snapshot Freeze",
    "stream_disconnect": "SSE Stream Disconnection",
}


class StepRequest(BaseModel):
    count: int = Field(default=1, ge=1, le=50, description="Number of simulation ticks to advance (1-50)")


class ChaosFaultRequest(BaseModel):
    duration_seconds: int = Field(default=60, ge=1, le=3600, description="Duration in seconds (e.g. 30, 60, 120)")
    parameters: dict[str, Any] = Field(default_factory=dict, description="Fault-specific injection parameters")


def _check_demo_enabled(settings: ApiSettings) -> None:
    if not getattr(settings, "demo_controls", True):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "DEMO_CONTROLS_DISABLED",
                "message": "Simulator controls are disabled in this environment.",
            },
        )


async def _call_simulator(
    request: Request,
    settings: ApiSettings,
    method: str,
    path: str,
    json_data: Any = None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    http_client: httpx.AsyncClient = getattr(request.app.state, "http", None) or httpx.AsyncClient(timeout=timeout)
    url = f"{settings.sim_base_url.rstrip('/')}/{path.lstrip('/')}"
    try:
        res = await http_client.request(method, url, json=json_data, timeout=timeout)
        if res.status_code >= 400:
            try:
                err_body = res.json()
            except Exception:
                err_body = res.text
            raise HTTPException(
                status_code=res.status_code,
                detail={"code": "SIMULATOR_ERROR", "message": f"Simulator error ({res.status_code}): {err_body}"},
            )
        return res.json() if res.headers.get("content-type", "").startswith("application/json") else {"status": "ok"}
    except HTTPException:
        raise
    except Exception as exc:
        log.error("simulator_call_failed", url=url, error=str(exc))
        raise HTTPException(
            status_code=502,
            detail={"code": "SIMULATOR_COMMUNICATION_ERROR", "message": f"Unable to reach simulator: {str(exc)}"},
        ) from exc


async def _record_audit(
    actor: str,
    action: str,
    payload: dict[str, Any],
    entity_type: str = "simulation",
    entity_id: str | None = None,
) -> None:
    try:
        sessionmaker = get_sessionmaker()
        async with sessionmaker() as session:
            await insert_audit_entry(
                session=session,
                actor=actor,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                payload=payload,
            )
            await session.commit()
    except Exception as exc:
        log.warning("audit_log_write_failed", action=action, error=str(exc))


# ---------------------------------------------------------------- Status & Metadata
@router.get("/status")
async def get_control_status(
    request: Request,
    settings: Annotated[ApiSettings, Depends(get_settings)],
) -> dict[str, Any]:
    """Retrieve simulator status, active presets, chaos drills, and demo control flag."""
    enabled = getattr(settings, "demo_controls", True)
    if not enabled:
        return {
            "demo_controls_enabled": False,
            "sim_status": "DISABLED",
            "tick": 0,
            "sim_time": None,
            "active_events_count": 0,
            "active_events": [],
            "active_faults": [],
            "presets": SUPPORTED_PRESETS,
            "faults": SUPPORTED_FAULTS,
        }

    sim_info: dict[str, Any] = {}
    events: list[dict[str, Any]] = []
    faults: list[dict[str, Any]] = []
    try:
        inst = await _call_simulator(request, settings, "GET", "/v1/instance", timeout=3.0)
        sim_info = inst
    except Exception:
        sim_info = {"status": "UNKNOWN", "tick": 0}

    try:
        ev_data = await _call_simulator(request, settings, "GET", "/v1/events", timeout=3.0)
        if isinstance(ev_data, list):
            events = [e for e in ev_data if isinstance(e, dict) and e.get("status") in ("SCHEDULED", "ACTIVE")]
    except Exception:
        events = []

    try:
        # Check if chaos proxy or simulator has active faults
        fault_data = await _call_simulator(request, settings, "GET", "/admin/audit?limit=20", timeout=3.0)
        if isinstance(fault_data, list):
            faults = [f for f in fault_data if isinstance(f, dict) and "fault" in f.get("action", "").lower()]
    except Exception:
        faults = []

    return {
        "demo_controls_enabled": True,
        "sim_status": sim_info.get("status", "UNKNOWN"),
        "tick": sim_info.get("tick", 0),
        "sim_time": sim_info.get("sim_time"),
        "scenario_id": sim_info.get("scenario_id", "baseline"),
        "active_events_count": len(events),
        "active_events": events,
        "active_faults": faults,
        "presets": SUPPORTED_PRESETS,
        "faults": SUPPORTED_FAULTS,
    }


@router.get("/logs")
async def get_control_logs(
    limit: int = 30,
) -> dict[str, Any]:
    """Retrieve audit history of simulator control actions."""
    try:
        sessionmaker = get_sessionmaker()
        async with sessionmaker() as session:
            rows = await fetch_audit_logs(session=session, action_prefix="control.", limit=max(1, min(100, limit)))
        logs = []
        for r in rows:
            payload = r.get("payload") or {}
            logs.append({
                "id": r["id"],
                "at": r["at"].isoformat() if hasattr(r["at"], "isoformat") else str(r["at"]),
                "actor": r["actor"],
                "action": r["action"],
                "status": payload.get("status", "SUCCESS"),
                "result": payload.get("result", ""),
                "details": payload,
            })
        return {"logs": logs}
    except Exception as exc:
        log.warning("fetch_control_logs_failed", error=str(exc))
        return {"logs": []}


# ---------------------------------------------------------------- Simulator Controls
@router.post("/sim/run")
async def control_sim_run(
    request: Request,
    user: Annotated[dict[str, Any], Depends(require_operator)],
    settings: Annotated[ApiSettings, Depends(get_settings)],
) -> dict[str, Any]:
    """Resume discrete-event simulation ticks (POST /admin/run)."""
    _check_demo_enabled(settings)
    actor = user.get("actor") or user.get("sub") or "operator"

    data = await _call_simulator(request, settings, "POST", "/admin/run")
    await _record_audit(
        actor=actor,
        action="control.sim.run",
        payload={"status": "SUCCESS", "result": f"Simulation resumed (tick {data.get('tick', 'now')})", "sim": data},
    )
    return {"status": "ok", "action": "run", "sim": data}


@router.post("/sim/pause")
async def control_sim_pause(
    request: Request,
    user: Annotated[dict[str, Any], Depends(require_operator)],
    settings: Annotated[ApiSettings, Depends(get_settings)],
) -> dict[str, Any]:
    """Pause discrete-event simulation ticks (POST /admin/pause)."""
    _check_demo_enabled(settings)
    actor = user.get("actor") or user.get("sub") or "operator"

    data = await _call_simulator(request, settings, "POST", "/admin/pause")
    await _record_audit(
        actor=actor,
        action="control.sim.pause",
        payload={"status": "SUCCESS", "result": f"Simulation paused at tick {data.get('tick', 'now')}", "sim": data},
    )
    return {"status": "ok", "action": "pause", "sim": data}


@router.post("/sim/step")
async def control_sim_step(
    req: StepRequest,
    request: Request,
    user: Annotated[dict[str, Any], Depends(require_operator)],
    settings: Annotated[ApiSettings, Depends(get_settings)],
) -> dict[str, Any]:
    """Advance simulation by 1..50 ticks (POST /admin/step x n)."""
    _check_demo_enabled(settings)
    actor = user.get("actor") or user.get("sub") or "operator"

    last_step: dict[str, Any] = {}
    for _ in range(req.count):
        last_step = await _call_simulator(request, settings, "POST", "/admin/step")

    end_tick = last_step.get("tick")
    sim_time = last_step.get("sim_time")

    await _record_audit(
        actor=actor,
        action="control.sim.step",
        payload={
            "status": "SUCCESS",
            "result": f"Advanced {req.count} tick(s) -> reached tick {end_tick}",
            "count": req.count,
            "end_tick": end_tick,
            "sim_time": sim_time,
        },
    )
    return {
        "status": "ok",
        "action": "step",
        "count": req.count,
        "tick": end_tick,
        "sim_time": sim_time,
    }


@router.post("/sim/reset")
async def control_sim_reset(
    request: Request,
    user: Annotated[dict[str, Any], Depends(require_operator)],
    settings: Annotated[ApiSettings, Depends(get_settings)],
) -> dict[str, Any]:
    """Reset simulation back to initial scenario state (POST /admin/reset)."""
    _check_demo_enabled(settings)
    actor = user.get("actor") or user.get("sub") or "operator"

    data = await _call_simulator(request, settings, "POST", "/admin/reset")
    await _record_audit(
        actor=actor,
        action="control.sim.reset",
        payload={"status": "SUCCESS", "result": "Simulation reset to initial scenario baseline", "sim": data},
    )
    return {"status": "ok", "action": "reset", "sim": data}


# ---------------------------------------------------------------- Crisis Presets
@router.post("/scenario/{preset}")
async def control_trigger_preset(
    preset: str,
    request: Request,
    user: Annotated[dict[str, Any], Depends(require_operator)],
    settings: Annotated[ApiSettings, Depends(get_settings)],
) -> dict[str, Any]:
    """Inject one of the 8 crisis presets specified in 21_API_CONTRACTS.md §2.8."""
    _check_demo_enabled(settings)
    actor = user.get("actor") or user.get("sub") or "operator"

    if preset not in SUPPORTED_PRESETS:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "INVALID_PRESET",
                "message": f"Preset '{preset}' is not supported. Supported presets: {list(SUPPORTED_PRESETS.keys())}",
            },
        )

    preset_def = SUPPORTED_PRESETS[preset]

    # Query current tick to schedule starting from now
    try:
        inst = await _call_simulator(request, settings, "GET", "/v1/instance")
        current_tick = int(inst.get("tick", 0))
    except Exception:
        current_tick = 0

    injected: list[dict[str, Any]] = []
    for ev_tpl in preset_def["events"]:
        ev_payload = {
            "type": ev_tpl["type"],
            "start_tick": current_tick,
            "duration_ticks": ev_tpl["duration_ticks"],
            "parameters": ev_tpl["parameters"],
        }
        res = await _call_simulator(request, settings, "POST", "/admin/events", json_data=ev_payload)
        injected.append(res)

    await _record_audit(
        actor=actor,
        action=f"control.scenario.{preset}",
        payload={
            "status": "SUCCESS",
            "result": f"Crisis preset '{preset_def['title']}' injected at tick {current_tick}",
            "preset": preset,
            "start_tick": current_tick,
            "events_count": len(injected),
            "events": injected,
        },
    )

    return {
        "status": "ok",
        "preset": preset,
        "title": preset_def["title"],
        "start_tick": current_tick,
        "events_injected": injected,
    }


# ---------------------------------------------------------------- Chaos Controls
@router.post("/chaos/clear")
async def control_clear_chaos_faults(
    request: Request,
    user: Annotated[dict[str, Any], Depends(require_operator)],
    settings: Annotated[ApiSettings, Depends(get_settings)],
) -> dict[str, Any]:
    """Clear all active simulator faults immediately (POST /admin/faults/clear)."""
    _check_demo_enabled(settings)
    actor = user.get("actor") or user.get("sub") or "operator"

    res = await _call_simulator(request, settings, "POST", "/admin/faults/clear")

    await _record_audit(
        actor=actor,
        action="control.chaos.clear",
        payload={"status": "SUCCESS", "result": "All active chaos faults cleared", "response": res},
    )

    return {"status": "ok", "action": "clear_faults", "result": res}


@router.post("/chaos/{fault}")
async def control_inject_chaos_fault(
    fault: str,
    req: ChaosFaultRequest,
    request: Request,
    user: Annotated[dict[str, Any], Depends(require_operator)],
    settings: Annotated[ApiSettings, Depends(get_settings)],
) -> dict[str, Any]:
    """Inject a resilience drill fault into the simulator (POST /admin/faults)."""
    _check_demo_enabled(settings)
    actor = user.get("actor") or user.get("sub") or "operator"

    if fault not in SUPPORTED_FAULTS:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "INVALID_FAULT_TYPE",
                "message": f"Fault '{fault}' is not supported. Supported faults: {list(SUPPORTED_FAULTS.keys())}",
            },
        )

    # Set fault-specific default parameters if omitted and normalize legacy latency_ms alias
    params = dict(req.parameters)
    if fault == "error_rate" and "rate" not in params:
        params["rate"] = 0.3
    elif fault == "latency":
        if "latency_ms" in params and "delay_ms" not in params:
            params["delay_ms"] = params.pop("latency_ms")
        elif "delay_ms" not in params:
            params["delay_ms"] = 1500

    fault_payload = {
        "type": fault,
        "duration_seconds": req.duration_seconds,
        "parameters": params,
    }

    res = await _call_simulator(request, settings, "POST", "/admin/faults", json_data=fault_payload)

    await _record_audit(
        actor=actor,
        action=f"control.chaos.{fault}",
        payload={
            "status": "SUCCESS",
            "result": f"Fault '{fault}' injected for {req.duration_seconds}s",
            "fault": fault,
            "duration_seconds": req.duration_seconds,
            "parameters": params,
            "response": res,
        },
    )

    return {
        "status": "ok",
        "fault": fault,
        "duration_seconds": req.duration_seconds,
        "parameters": params,
        "result": res,
    }
