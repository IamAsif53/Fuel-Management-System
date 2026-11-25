"""Redis state publisher module for FSIP Engine.

Implements Task 2.5:
- Publishes serialized WorldState snapshot to Redis key `fsip:state`.
- Sets current simulation tick to Redis key `fsip:state:tick`.
- Updates component health in Redis hash `fsip:health`.
- Appends domain events (e.g. `state.updated`) to Redis Stream `fsip:domain-events` (MAXLEN ~ 20000).
- Catches and isolates all Redis errors without crashing the sync cycle, incrementing `fsip_redis_errors_total`.
"""
from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

import orjson
import redis.asyncio as aioredis
from fsip_common.logging import get_logger
from prometheus_client import REGISTRY, Counter

from app.state.store import WorldState

# ---------------------------------------------------------------- Prometheus Metrics
try:
    FSIP_REDIS_ERRORS_TOTAL = Counter(
        "fsip_redis_errors_total",
        "Total number of Redis errors encountered by the publisher",
    )
except ValueError:
    FSIP_REDIS_ERRORS_TOTAL = REGISTRY._names_to_collectors.get("fsip_redis_errors_total")  # type: ignore[assignment]


class Publisher:
    """Asynchronous Redis state and domain event publisher."""

    def __init__(
        self,
        redis_client: aioredis.Redis | None = None,
        redis_url: str | None = None,
        stream_name: str = "fsip:domain-events",
        state_key: str = "fsip:state",
        tick_key: str = "fsip:state:tick",
        health_key: str = "fsip:health",
        stream_maxlen: int = 20000,
    ) -> None:
        self.stream_name = stream_name
        self.state_key = state_key
        self.tick_key = tick_key
        self.health_key = health_key
        self.stream_maxlen = stream_maxlen
        self._log = get_logger()

        self._owned_client: bool = False
        if redis_client is not None:
            self._client = redis_client
        elif redis_url:
            self._client = aioredis.from_url(redis_url, decode_responses=True)
            self._owned_client = True
        else:
            self._client = None

    def _get_client(self) -> aioredis.Redis | None:
        return self._client

    async def publish_state(self, store: WorldState) -> None:
        """Publish the current public WorldState projection, tick, health, and state.updated event to Redis."""
        client = self._get_client()
        if client is None:
            return

        try:
            state_dict = store.to_public_json()
            state_json = orjson.dumps(state_dict).decode("utf-8")
            tick_str = str(store.tick)
            now_dt = datetime.now(UTC)
            now_iso = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

            last_sync = max(store.last_good_at.values()) if store.last_good_at else None
            age_s = round(time.time() - last_sync, 2) if last_sync is not None else None
            is_ready = age_s is not None and age_s <= 30.0 and not store.degraded
            health_dict = {
                "status": "ready" if is_ready else "degraded",
                "last_sync_age_s": age_s,
                "engine_mode": store.engine_mode,
                "tick": store.tick,
                "sim_run_id": store.sim_run_id,
                "service_level": float(getattr(store.metrics, "service_level", 1.0)) if store.metrics else 1.0,
                "degraded": store.degraded,
                "degraded_reasons": sorted(list(store.degraded_reasons)),
                "updated_at": now_iso,
            }
            health_json = orjson.dumps(health_dict).decode("utf-8")

            etag = f"{store.sim_run_id or 0}-{store.tick}"
            event_data_json = orjson.dumps({"tick": store.tick, "etag": etag}).decode("utf-8")

            stream_entry = {
                "type": "state.updated",
                "at": now_iso,
                "sim_run_id": str(store.sim_run_id or 0),
                "tick": tick_str,
                "etag": etag,
                "data": event_data_json,
            }

            async with client.pipeline(transaction=False) as pipe:
                pipe.set(self.state_key, state_json)
                pipe.set(self.tick_key, tick_str)
                pipe.hset(self.health_key, "engine", health_json)
                pipe.xadd(
                    self.stream_name,
                    stream_entry,
                    maxlen=self.stream_maxlen,
                    approximate=True,
                )
                await pipe.execute()

            self._log.debug(
                "redis.published_state",
                tick=store.tick,
                sim_run_id=store.sim_run_id,
                etag=etag,
            )
        except Exception as exc:
            if FSIP_REDIS_ERRORS_TOTAL is not None:
                FSIP_REDIS_ERRORS_TOTAL.inc()
            self._log.error("redis.publish_state.failed", error=str(exc))

    async def event(
        self,
        type: str,
        data: dict[str, Any] | Any,
        sim_run_id: int | None = None,
        tick: int | None = None,
    ) -> str:
        """Append a domain event to the Redis Stream fsip:domain-events."""
        client = self._get_client()
        if client is None:
            return ""

        try:
            now_dt = datetime.now(UTC)
            now_iso = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            if isinstance(data, dict | list):
                data_json = orjson.dumps(data).decode("utf-8")
            else:
                data_json = str(data)

            stream_entry = {
                "type": type,
                "at": now_iso,
                "sim_run_id": str(sim_run_id if sim_run_id is not None else 0),
                "tick": str(tick if tick is not None else 0),
                "data": data_json,
            }

            stream_id = await client.xadd(
                self.stream_name,
                stream_entry,
                maxlen=self.stream_maxlen,
                approximate=True,
            )
            return str(stream_id)
        except Exception as exc:
            if FSIP_REDIS_ERRORS_TOTAL is not None:
                FSIP_REDIS_ERRORS_TOTAL.inc()
            self._log.error("redis.event.failed", event_type=type, error=str(exc))
            return ""

    async def close(self) -> None:
        """Close connection if owned by this publisher instance."""
        if self._owned_client and self._client is not None:
            await self._client.aclose()
            self._client = None
