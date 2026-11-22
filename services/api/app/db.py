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
            pool_size=10,
            max_overflow=5,
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
