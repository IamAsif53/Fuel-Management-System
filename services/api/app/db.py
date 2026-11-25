"""Database access layer for FSIP API service.

Provides async SQLAlchemy sessions and database query helpers for snapshot fallback
and entity history retrieval.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any

import orjson
from fsip_common.logging import get_logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings

log = get_logger()

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    """Return or initialize singleton AsyncEngine."""
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.database_url,
            pool_size=15,
            max_overflow=10,
            pool_timeout=10,
            pool_pre_ping=True,
            connect_args={"server_settings": {"application_name": "fsip_api"}},
        )
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Return or initialize singleton async sessionmaker."""
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            bind=get_engine(),
            expire_on_commit=False,
            autoflush=False,
        )
    return _sessionmaker


async def close_db() -> None:
    """Dispose singleton engine connections."""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _sessionmaker = None


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding an AsyncSession."""
    sm = get_sessionmaker()
    async with sm() as session:
        yield session


async def check_db_health() -> bool:
    """Check database reachability via SELECT 1."""
    try:
        sm = get_sessionmaker()
        async with sm() as session:
            res = await session.execute(text("SELECT 1"))
            return res.scalar() == 1
    except Exception as exc:
        log.warning("api.db.health_check_failed", error=str(exc))
        return False


def _parse_json_field(val: Any) -> Any:
    if val is None:
        return None
    if isinstance(val, dict | list):
        return val
    if isinstance(val, str):
        try:
            return orjson.loads(val)
        except Exception:
            return val
    return val


def snapshot_row_to_state(row: dict[str, Any]) -> dict[str, Any]:
    """Reconstruct public WorldState JSON dictionary from a world_snapshots database row."""
    depots = _parse_json_field(row.get("depots")) or []
    stations = _parse_json_field(row.get("stations")) or []
    routes = _parse_json_field(row.get("routes")) or []
    events = _parse_json_field(row.get("events")) or []
    supply = _parse_json_field(row.get("supply")) or []
    metrics = _parse_json_field(row.get("metrics")) or {}

    sim_time_val = row.get("sim_time")
    if hasattr(sim_time_val, "isoformat"):
        sim_time_str = sim_time_val.isoformat()
    else:
        sim_time_str = str(sim_time_val or "")

    now_iso = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    tick = int(row.get("tick") or 0)
    sim_run_id = int(row.get("sim_run_id") or 0)

    return {
        "sim_run_id": sim_run_id,
        "tick": tick,
        "sim_time": sim_time_str,
        "tick_minutes": 15,
        "sim_status": "RUNNING",
        "clock_label": f"t={tick}",
        "generated_at": now_iso,
        "age_seconds": 0.0,
        "stale": bool(row.get("stale", False)),
        "degraded": True,  # DB fallback state is marked degraded per specification
        "degraded_reasons": ["redis_down"],
        "engine_mode": "NORMAL",
        "operating_mode": "advisory",
        "metrics": metrics,
        "depots": depots,
        "stations": stations,
        "routes": routes,
        "events": events,
        "supply_arrivals_upcoming": supply,
        "allocations_active": [],
        "alerts_open": [],
        "pending_recommendations": [],
        "system": {
            "simulator": "UP",
            "sse": "DISCONNECTED",
            "intelligence": "UP",
            "db": "UP",
            "redis": "DOWN",
        },
    }


async def fetch_latest_world_snapshot(session: AsyncSession) -> dict[str, Any] | None:
    """Fetch the latest world snapshot from the database."""
    query = text(
        """
        SELECT id, sim_run_id, tick, sim_time, depots, stations, routes,
               events, supply, metrics, stale, degraded, created_at
        FROM world_snapshots
        ORDER BY id DESC
        LIMIT 1
        """
    )
    result = await session.execute(query)
    row = result.mappings().first()
    if row is None:
        return None
    return snapshot_row_to_state(dict(row))


async def fetch_snapshot_by_tick(
    session: AsyncSession,
    tick: int,
    sim_run_id: int | str | None = None,
) -> dict[str, Any] | None:
    """Fetch a specific historical world snapshot by tick and optional sim_run_id."""
    if sim_run_id is not None:
        query = text(
            """
            SELECT id, sim_run_id, tick, sim_time, depots, stations, routes,
                   events, supply, metrics, stale, degraded, created_at
            FROM world_snapshots
            WHERE sim_run_id = :sim_run_id AND tick = :tick
            ORDER BY id DESC
            LIMIT 1
            """
        )
        params = {"sim_run_id": int(sim_run_id), "tick": int(tick)}
    else:
        query = text(
            """
            SELECT id, sim_run_id, tick, sim_time, depots, stations, routes,
                   events, supply, metrics, stale, degraded, created_at
            FROM world_snapshots
            WHERE tick = :tick
            ORDER BY id DESC
            LIMIT 1
            """
        )
        params = {"tick": int(tick)}

    result = await session.execute(query, params)
    row = result.mappings().first()
    if row is None:
        return None
    return snapshot_row_to_state(dict(row))


async def fetch_available_ticks(
    session: AsyncSession,
    sim_run_id: int | str | None = None,
    limit: int = 500,
) -> list[int]:
    """Fetch recorded simulation ticks available for time-travel replay."""
    if sim_run_id is not None:
        query = text(
            """
            SELECT DISTINCT tick
            FROM world_snapshots
            WHERE sim_run_id = :sim_run_id
            ORDER BY tick ASC
            LIMIT :limit
            """
        )
        params = {"sim_run_id": int(sim_run_id), "limit": limit}
    else:
        query = text(
            """
            SELECT DISTINCT tick
            FROM world_snapshots
            ORDER BY tick ASC
            LIMIT :limit
            """
        )
        params = {"limit": limit}

    result = await session.execute(query, params)
    return [int(r["tick"]) for r in result.mappings().all()]


async def fetch_station_demand_history(
    session: AsyncSession,
    station_id: str,
    limit: int = 96,
) -> list[dict[str, Any]]:
    """Fetch recent demand observations for a station."""
    query = text(
        """
        SELECT tick, sim_time, station_id, fuel_type, demand_liters, served_liters, unmet_liters
        FROM demand_observations
        WHERE station_id = :station_id
        ORDER BY sim_time DESC, tick DESC
        LIMIT :limit
        """
    )
    result = await session.execute(query, {"station_id": station_id, "limit": limit})
    rows = result.mappings().all()
    demand_list = []
    for r in rows:
        sim_time = r["sim_time"].isoformat() if hasattr(r["sim_time"], "isoformat") else str(r["sim_time"])
        demand_list.append(
            {
                "tick": r["tick"],
                "sim_time": sim_time,
                "station_id": r["station_id"],
                "fuel_type": r["fuel_type"],
                "demand": r["demand_liters"],
                "served": r["served_liters"],
                "unmet": r["unmet_liters"],
            }
        )
    return demand_list


async def fetch_recent_snapshots(
    session: AsyncSession,
    limit: int = 96,
) -> list[dict[str, Any]]:
    """Fetch recent snapshots to build entity inventory history."""
    query = text(
        """
        SELECT id, sim_run_id, tick, sim_time, depots, stations
        FROM world_snapshots
        ORDER BY id DESC
        LIMIT :limit
        """
    )
    result = await session.execute(query, {"limit": limit})
    rows = result.mappings().all()
    return [dict(r) for r in reversed(rows)]


# ---------------------------------------------------------------- Decisions & Audit Queries
async def fetch_decisions(
    session: AsyncSession,
    sim_run_id: int | None = None,
    status: str | None = None,
    policy: str | None = None,
    from_tick: int | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """Fetch decisions with optional filtering, returning (items, total_count)."""
    conditions = []
    params: dict[str, Any] = {"limit": limit, "offset": offset}

    if sim_run_id is not None:
        conditions.append("sim_run_id = :sim_run_id")
        params["sim_run_id"] = sim_run_id
    if status is not None:
        conditions.append("status = :status")
        params["status"] = status
    if policy is not None:
        conditions.append("policy = :policy")
        params["policy"] = policy
    if from_tick is not None:
        conditions.append("tick >= :from_tick")
        params["from_tick"] = from_tick

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    count_q = text(f"SELECT COUNT(*) FROM decisions {where_clause}")
    count_res = await session.execute(count_q, params)
    total = count_res.scalar() or 0

    select_q = text(
        f"""
        SELECT id, sim_run_id, tick, policy, policy_version, trigger, status, mode,
               confidence, degraded, fallback_reason, narrative, approved_by, approved_at,
               rejected_reason, expires_at_tick, created_at
        FROM decisions
        {where_clause}
        ORDER BY tick DESC, id DESC
        LIMIT :limit OFFSET :offset
        """
    )
    res = await session.execute(select_q, params)
    rows = [dict(r) for r in res.mappings().all()]
    return rows, int(total)


async def fetch_decision_by_id(session: AsyncSession, decision_id: int) -> dict[str, Any] | None:
    """Fetch full decision by ID, including lines and outcome."""
    q_dec = text(
        """
        SELECT id, sim_run_id, tick, policy, policy_version, trigger, status, mode,
               confidence, degraded, fallback_reason, narrative, approved_by, approved_at,
               rejected_reason, expires_at_tick, created_at, explanation, impact, objective
        FROM decisions
        WHERE id = :decision_id
        """
    )
    res_dec = await session.execute(q_dec, {"decision_id": decision_id})
    dec_row = res_dec.mappings().first()
    if not dec_row:
        return None

    dec_dict = dict(dec_row)

    # Fetch lines
    q_lines = text(
        """
        SELECT id, line_no, source_depot_id, destination_station_id, route_id,
               fuel_type, quantity, idempotency_key, status, sim_allocation_id,
               sim_error_code, expected_arrival_tick, actual_arrival_tick,
               received_liters, reason, created_at
        FROM decision_lines
        WHERE decision_id = :decision_id
        ORDER BY line_no ASC
        """
    )
    res_lines = await session.execute(q_lines, {"decision_id": decision_id})
    dec_dict["lines"] = [dict(r) for r in res_lines.mappings().all()]

    # Fetch outcome if present
    q_outcome = text(
        """
        SELECT decision_id, evaluated_tick, predicted, realised, hit, created_at
        FROM decision_outcomes
        WHERE decision_id = :decision_id
        """
    )
    res_out = await session.execute(q_outcome, {"decision_id": decision_id})
    out_row = res_out.mappings().first()
    dec_dict["outcome"] = dict(out_row) if out_row else None

    return dec_dict


async def fetch_recommendations(session: AsyncSession, sim_run_id: int | None = None) -> list[dict[str, Any]]:
    """Fetch active pending recommendations (decisions pending approval with lines)."""
    conditions = ["status IN ('PENDING_APPROVAL', 'PROPOSED')"]
    params: dict[str, Any] = {}
    if sim_run_id is not None:
        conditions.append("sim_run_id = :sim_run_id")
        params["sim_run_id"] = sim_run_id

    where_clause = "WHERE " + " AND ".join(conditions)

    q = text(
        f"""
        SELECT id, sim_run_id, tick, policy, policy_version, trigger, status, mode,
               confidence, degraded, fallback_reason, narrative, expires_at_tick, created_at,
               explanation, impact, objective
        FROM decisions
        {where_clause}
        ORDER BY id DESC
        """
    )
    res = await session.execute(q, params)
    decisions = [dict(r) for r in res.mappings().all()]

    if not decisions:
        return []

    dec_ids = [d["id"] for d in decisions]
    q_lines = text(
        """
        SELECT id, decision_id, line_no, source_depot_id, destination_station_id, route_id,
               fuel_type, quantity, idempotency_key, status, expected_arrival_tick, reason
        FROM decision_lines
        WHERE decision_id = ANY(:dec_ids)
        ORDER BY decision_id ASC, line_no ASC
        """
    )
    lines_res = await session.execute(q_lines, {"dec_ids": dec_ids})
    lines_by_dec: dict[int, list[dict[str, Any]]] = {}
    for ln in lines_res.mappings().all():
        lines_by_dec.setdefault(ln["decision_id"], []).append(dict(ln))

    for d in decisions:
        d["lines"] = lines_by_dec.get(d["id"], [])

    return decisions


async def insert_audit_entry(
    session: AsyncSession,
    actor: str,
    action: str,
    entity_type: str | None = None,
    entity_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> int:
    """Insert an operator action record into audit_log table."""
    q = text(
        """
        INSERT INTO audit_log (at, actor, action, entity_type, entity_id, payload)
        VALUES (NOW(), :actor, :action, :entity_type, :entity_id, :payload)
        RETURNING id
        """
    )
    res = await session.execute(
        q,
        {
            "actor": actor,
            "action": action,
            "entity_type": entity_type,
            "entity_id": str(entity_id) if entity_id is not None else None,
            "payload": orjson.dumps(payload).decode() if payload else None,
        },
    )
    row_id = res.scalar() or 0
    return int(row_id)


async def fetch_audit_logs(
    session: AsyncSession,
    action: str | None = None,
    action_prefix: str | None = None,
    actor: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Fetch audit logs ordered newest first."""
    conditions = []
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if action:
        conditions.append("action = :action")
        params["action"] = action
    elif action_prefix:
        conditions.append("action LIKE :prefix")
        params["prefix"] = f"{action_prefix}%"
    if actor:
        conditions.append("actor = :actor")
        params["actor"] = actor

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    q = text(
        f"""
        SELECT id, at, actor, action, entity_type, entity_id, payload
        FROM audit_log
        {where_clause}
        ORDER BY at DESC, id DESC
        LIMIT :limit OFFSET :offset
        """
    )
    res = await session.execute(q, params)
    rows = []
    for r in res.mappings().all():
        d = dict(r)
        d["payload"] = _parse_json_field(d.get("payload"))
        rows.append(d)
    return rows


# ---------------------------------------------------------------- Alerts, Incidents & Forecasts
async def fetch_alerts(
    session: AsyncSession,
    status: str | None = "OPEN",
    category: str | None = None,
    severity: str | None = None,
    limit: int = 100,
) -> tuple[list[dict[str, Any]], int]:
    """Fetch alerts with optional filtering, returning (items, total_count)."""
    conditions = []
    params: dict[str, Any] = {"limit": limit}

    if status and status.upper() != "ALL":
        conditions.append("status = :status")
        params["status"] = status.upper()
    if category:
        conditions.append("category = :category")
        params["category"] = category
    if severity:
        conditions.append("severity = :severity")
        params["severity"] = severity.upper()

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    count_q = text(f"SELECT COUNT(*) FROM alerts {where_clause}")
    count_res = await session.execute(count_q, params)
    total = count_res.scalar() or 0

    select_q = text(
        f"""
        SELECT id, sim_run_id, fingerprint, category, severity, title, message,
               entity_type, entity_id, fuel_type, status, opened_tick, resolved_tick,
               opened_at, resolved_at, acknowledged_by, data, related_decision_id
        FROM alerts
        {where_clause}
        ORDER BY opened_at DESC, id DESC
        LIMIT :limit
        """
    )
    res = await session.execute(select_q, params)
    rows = []
    for r in res.mappings().all():
        d = dict(r)
        d["data"] = _parse_json_field(d.get("data"))
        if hasattr(d.get("opened_at"), "isoformat"):
            d["opened_at"] = d["opened_at"].isoformat()
        if hasattr(d.get("resolved_at"), "isoformat"):
            d["resolved_at"] = d["resolved_at"].isoformat()
        rows.append(d)
    return rows, int(total)


async def ack_alert(
    session: AsyncSession,
    alert_id: int,
    actor: str,
) -> dict[str, Any] | None:
    """Acknowledge an alert, setting status=ACKNOWLEDGED and returning updated alert."""
    q = text(
        """
        UPDATE alerts
        SET status = 'ACKNOWLEDGED', acknowledged_by = :actor
        WHERE id = :alert_id
        RETURNING id, sim_run_id, fingerprint, category, severity, title, message,
                  entity_type, entity_id, fuel_type, status, opened_tick, resolved_tick,
                  opened_at, resolved_at, acknowledged_by, data, related_decision_id
        """
    )
    res = await session.execute(q, {"alert_id": alert_id, "actor": actor})
    row = res.mappings().first()
    if not row:
        return None
    d = dict(row)
    d["data"] = _parse_json_field(d.get("data"))
    if hasattr(d.get("opened_at"), "isoformat"):
        d["opened_at"] = d["opened_at"].isoformat()
    if hasattr(d.get("resolved_at"), "isoformat"):
        d["resolved_at"] = d["resolved_at"].isoformat()
    await session.commit()
    return d


async def fetch_incidents(
    session: AsyncSession,
    status: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Fetch incidents ordered by started_at DESC."""
    conditions = []
    params: dict[str, Any] = {"limit": limit}
    if status and status.upper() != "ALL":
        conditions.append("status = :status")
        params["status"] = status.upper()

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    q = text(
        f"""
        SELECT id, kind, component, status, started_at, mitigated_at, resolved_at,
               summary, timeline, created_at
        FROM incidents
        {where_clause}
        ORDER BY started_at DESC, id DESC
        LIMIT :limit
        """
    )
    res = await session.execute(q, params)
    rows = []
    for r in res.mappings().all():
        d = dict(r)
        d["timeline"] = _parse_json_field(d.get("timeline"))
        if hasattr(d.get("started_at"), "isoformat"):
            d["started_at"] = d["started_at"].isoformat()
        if hasattr(d.get("mitigated_at"), "isoformat"):
            d["mitigated_at"] = d["mitigated_at"].isoformat()
        if hasattr(d.get("resolved_at"), "isoformat"):
            d["resolved_at"] = d["resolved_at"].isoformat()
        if hasattr(d.get("created_at"), "isoformat"):
            d["created_at"] = d["created_at"].isoformat()
        rows.append(d)
    return rows


async def fetch_forecast_series(
    session: AsyncSession,
    station_id: str,
    fuel_type: str,
    horizon: int = 48,
    history: int = 96,
) -> dict[str, Any]:
    """Fetch forecast series combining history demand observations and forecast p10/p50/p90 points."""
    hist_q = text(
        """
        SELECT tick, demand_liters
        FROM demand_observations
        WHERE station_id = :station_id AND fuel_type = :fuel_type
        ORDER BY tick DESC
        LIMIT :history
        """
    )
    hist_res = await session.execute(hist_q, {"station_id": station_id, "fuel_type": fuel_type, "history": history})
    hist_rows = [dict(r) for r in hist_res.mappings().all()]
    hist_points = [{"tick": r["tick"], "demand": float(r["demand_liters"] or 0.0)} for r in reversed(hist_rows)]

    pts_q = text(
        """
        SELECT target_tick, p10, p50, p90, model_name, model_version, is_fallback
        FROM forecasts
        WHERE station_id = :station_id AND fuel_type = :fuel_type
        ORDER BY made_at_tick DESC, target_tick ASC
        LIMIT :horizon
        """
    )
    pts_res = await session.execute(pts_q, {"station_id": station_id, "fuel_type": fuel_type, "horizon": horizon})
    pts_rows = [dict(r) for r in pts_res.mappings().all()]

    if pts_rows:
        model_name = pts_rows[0].get("model_name") or "seasonal"
        model_version = pts_rows[0].get("model_version") or "builtin"
        model = f"{model_name}@{model_version}"
        is_fallback = bool(pts_rows[0].get("is_fallback", False))
        forecast_points = [
            {
                "tick": r["target_tick"],
                "p10": float(r["p10"]),
                "p50": float(r["p50"]),
                "p90": float(r["p90"]),
            }
            for r in pts_rows
        ]
    else:
        model = "seasonal@builtin"
        is_fallback = False
        forecast_points = []

    return {
        "station_id": station_id,
        "fuel_type": fuel_type,
        "model": model,
        "is_fallback": is_fallback,
        "history": hist_points,
        "points": forecast_points,
        "coverage_80": 0.80,
    }


async def fetch_reconciliation_report(
    session: AsyncSession,
    from_tick: int | None = None,
    to_tick: int | None = None,
) -> dict[str, Any]:
    """Fetch allocation reconciliation report and loss accounting (F-RECON)."""
    query = """
        SELECT
            ar.decision_line_id,
            ar.sent,
            ar.received,
            ar.loss,
            ar.loss_reason,
            ar.method,
            dl.tick
        FROM allocation_reconciliation ar
        LEFT JOIN decision_lines dl ON dl.id = ar.decision_line_id
        WHERE 1=1
    """
    params: dict[str, Any] = {}
    if from_tick is not None:
        query += " AND dl.tick >= :from_tick"
        params["from_tick"] = from_tick
    if to_tick is not None:
        query += " AND dl.tick <= :to_tick"
        params["to_tick"] = to_tick
    query += " ORDER BY ar.decision_line_id DESC LIMIT 500"

    try:
        result = await session.execute(text(query), params)
        rows = result.mappings().all()
    except Exception as exc:
        log.warning("db.fetch_reconciliation_report_failed", error=str(exc))
        rows = []

    lines = []
    total_sent = 0.0
    total_received = 0.0
    total_loss = 0.0

    for r in rows:
        sent = float(r["sent"] or 0.0)
        received = float(r["received"] or 0.0)
        loss = float(r["loss"] or 0.0)
        total_sent += sent
        total_received += received
        total_loss += loss
        lines.append(
            {
                "line_id": r["decision_line_id"],
                "sent": sent,
                "received": received,
                "loss": loss,
                "loss_reason": r["loss_reason"] or "NONE",
                "method": r["method"] or "audit",
            }
        )

    loss_rate = round((total_loss / max(1.0, total_sent)) * 100, 2)

    return {
        "lines": lines,
        "totals": {
            "sent": total_sent,
            "received": total_received,
            "loss": total_loss,
            "loss_rate": loss_rate,
        },
        "stations": [],
    }
