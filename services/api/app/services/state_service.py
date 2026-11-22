"""Service for retrieving current WorldState from Redis or Postgres fallback."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import orjson
import redis.asyncio as aioredis
from fsip_common.logging import get_logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import fetch_latest_world_snapshot

log = get_logger()


async def get_current_state(
    redis_client: aioredis.Redis | None,
    session: AsyncSession,
) -> tuple[dict[str, Any], int, bool, str]:
    """Retrieve the latest WorldState projection.

    Returns:
        tuple of (state_dict, tick, is_degraded, etag)
    """
    # 1. Try Redis live snapshot
    if redis_client is not None:
        try:
            state_raw = await redis_client.get("fsip:state")
            if state_raw:
                state = orjson.loads(state_raw)
                tick = int(state.get("tick", 0))
                sim_run_id = int(state.get("sim_run_id", 0))
                is_degraded = bool(state.get("degraded", False))
                etag = f'"{sim_run_id}-{tick}"'
                return state, tick, is_degraded, etag
        except Exception as exc:
            log.warning("api.state.redis_read_failed_falling_back_to_db", error=str(exc))

    # 2. Fallback to latest TimescaleDB snapshot
    try:
        db_state = await fetch_latest_world_snapshot(session)
        if db_state is not None:
            tick = int(db_state.get("tick", 0))
            sim_run_id = int(db_state.get("sim_run_id", 0))
            etag = f'"{sim_run_id}-{tick}"'
            return db_state, tick, True, etag
    except Exception as exc:
        log.error("api.state.db_fallback_failed", error=str(exc))

    # 3. Complete degradation empty state
    now_iso = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    empty_state = {
        "sim_run_id": 0,
        "tick": 0,
        "sim_time": now_iso,
        "tick_minutes": 15,
        "sim_status": "UNKNOWN",
        "clock_label": "t=0",
        "generated_at": now_iso,
        "age_seconds": 999.0,
        "stale": True,
        "degraded": True,
        "degraded_reasons": ["redis_down", "db_unavailable"],
        "engine_mode": "NORMAL",
        "operating_mode": "advisory",
        "metrics": {},
        "depots": [],
        "stations": [],
        "routes": [],
        "events": [],
        "supply_arrivals_upcoming": [],
        "allocations_active": [],
        "alerts_open": [],
        "pending_recommendations": [],
        "system": {
            "simulator": "UNKNOWN",
            "sse": "DISCONNECTED",
            "intelligence": "UNKNOWN",
            "db": "DOWN",
            "redis": "DOWN",
        },
    }
    return empty_state, 0, True, '"0-0"'
