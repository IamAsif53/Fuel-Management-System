"""Server-Sent Events (SSE) streaming endpoint (/api/stream).

Streams domain events from Redis Stream `fsip:domain-events` with Last-Event-ID replay.
Falls back to database polling if Redis is unavailable.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import StreamingResponse
from fsip_common.logging import get_logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import fetch_latest_world_snapshot, get_session

router = APIRouter(prefix="/api", tags=["stream"])
log = get_logger()

STREAM_NAME = "fsip:domain-events"
HEARTBEAT_INTERVAL_SEC = 15.0
THROTTLE_MIN_INTERVAL_SEC = 0.5  # <= 2 events / sec per client


@router.get("/stream")
async def event_stream_endpoint(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    limit: Annotated[int | None, Query(description="Optional max events to receive before closing stream")] = None,
) -> StreamingResponse:
    """Stream domain events to connected clients via Server-Sent Events (SSE)."""
    redis_client = getattr(request.app.state, "redis", None)

    async def event_generator() -> AsyncGenerator[str, None]:
        last_yield_time = time.monotonic()
        last_msg_id = last_event_id or "$"
        current_tick = -1
        emitted_count = 0

        try:
            # Phase 1: Replay missed events if client reconnected with Last-Event-ID
            if redis_client is not None and last_event_id and last_event_id != "$":
                try:
                    # Exclusive range: (last_event_id to +
                    range_start = f"({last_event_id}" if not last_event_id.startswith("(") else last_event_id
                    missed_entries = await redis_client.xrange(STREAM_NAME, range_start, "+", count=500)
                    for entry_id, entry_data in missed_entries:
                        event_type = entry_data.get("type", "state.updated")
                        event_payload = entry_data.get("data", "{}")

                        # Enforce throttling
                        now = time.monotonic()
                        elapsed = now - last_yield_time
                        if elapsed < THROTTLE_MIN_INTERVAL_SEC:
                            await asyncio.sleep(THROTTLE_MIN_INTERVAL_SEC - elapsed)

                        yield f"id: {entry_id}\nevent: {event_type}\ndata: {event_payload}\n\n"
                        last_yield_time = time.monotonic()
                        last_msg_id = entry_id
                        emitted_count += 1
                        if limit is not None and emitted_count >= limit:
                            return
                except Exception as exc:
                    log.warning("api.stream.replay_failed", error=str(exc))

            # Phase 2: Live event loop
            while True:
                redis_ok = False
                if redis_client is not None:
                    try:
                        # Block for up to 15s waiting for new events
                        block_ms = int(HEARTBEAT_INTERVAL_SEC * 1000)
                        streams = {STREAM_NAME: last_msg_id}
                        results = await redis_client.xread(streams, count=50, block=block_ms)
                        redis_ok = True

                        if results:
                            for _sname, entries in results:
                                for entry_id, entry_data in entries:
                                    event_type = entry_data.get("type", "state.updated")
                                    event_payload = entry_data.get("data", "{}")

                                    now = time.monotonic()
                                    elapsed = now - last_yield_time
                                    if elapsed < THROTTLE_MIN_INTERVAL_SEC:
                                        await asyncio.sleep(THROTTLE_MIN_INTERVAL_SEC - elapsed)

                                    yield f"id: {entry_id}\nevent: {event_type}\ndata: {event_payload}\n\n"
                                    last_yield_time = time.monotonic()
                                    last_msg_id = entry_id
                                    emitted_count += 1
                                    if limit is not None and emitted_count >= limit:
                                        return
                        else:
                            # Timeout elapsed without new events -> send heartbeat keepalive comment
                            yield ": keepalive\n\n"
                            last_yield_time = time.monotonic()
                    except Exception as exc:
                        log.warning("api.stream.redis_stream_read_error", error=str(exc))
                        redis_ok = False

                if not redis_ok:
                    # Redis unavailable -> DB polling fallback every 2.0s
                    try:
                        snap = await fetch_latest_world_snapshot(session)
                        if snap is not None:
                            tick = snap.get("tick", 0)
                            sim_run_id = snap.get("sim_run_id", 0)
                            if tick != current_tick:
                                current_tick = tick
                                etag = f"{sim_run_id}-{tick}"
                                data_payload = f'{{"tick": {tick}, "etag": "{etag}"}}'
                                db_msg = f"id: db-{tick}\nevent: state.updated\ndata: {data_payload}\n\n"
                                yield db_msg
                                last_yield_time = time.monotonic()
                                emitted_count += 1
                                if limit is not None and emitted_count >= limit:
                                    return
                        now = time.monotonic()
                        if now - last_yield_time >= HEARTBEAT_INTERVAL_SEC:
                            yield ": keepalive\n\n"
                            last_yield_time = now
                    except Exception as db_exc:
                        log.error("api.stream.db_poll_fallback_failed", error=str(db_exc))

                    await asyncio.sleep(2.0)
        except (asyncio.CancelledError, GeneratorExit):
            return

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
