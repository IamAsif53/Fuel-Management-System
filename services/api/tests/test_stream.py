"""Tests for /api/stream SSE endpoint."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from app.db import get_session
from app.main import app


@pytest.mark.asyncio
async def test_stream_emits_redis_events():
    """Verify that /api/stream yields domain events read from Redis stream."""
    mock_redis = MagicMock()
    mock_redis.xread = AsyncMock()

    # First call returns one event, second call blocks/sleeps to allow client exit
    async def mock_xread(streams, count, block):
        if mock_xread.calls == 0:
            mock_xread.calls += 1
            return [
                (
                    "fsip:domain-events",
                    [
                        (
                            "1732332800000-0",
                            {
                                "type": "state.updated",
                                "data": '{"tick": 10, "etag": "1-10"}',
                            },
                        )
                    ],
                )
            ]
        await asyncio.sleep(0.5)
        return []

    mock_xread.calls = 0
    mock_redis.xread.side_effect = mock_xread
    app.state.redis = mock_redis

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        async with client.stream("GET", "/api/stream", params={"limit": 1}) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")

            lines = []
            async for line in response.aiter_lines():
                lines.append(line)

            assert any("event: state.updated" in ln for ln in lines)
            assert any("id: 1732332800000-0" in ln for ln in lines)
            assert any('data: {"tick": 10, "etag": "1-10"}' in ln for ln in lines)


@pytest.mark.asyncio
async def test_stream_replays_missed_events_with_last_event_id():
    """Verify that /api/stream invokes xrange when Last-Event-ID header is present."""
    mock_redis = MagicMock()
    mock_redis.xrange = AsyncMock(
        return_value=[
            (
                "1732332800000-1",
                {
                    "type": "alert.opened",
                    "data": '{"id": "alt-99"}',
                },
            )
        ]
    )
    mock_redis.xread = AsyncMock(side_effect=lambda *args, **kwargs: asyncio.sleep(0.5) or [])
    app.state.redis = mock_redis

    headers = {"Last-Event-ID": "1732332800000-0"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        async with client.stream("GET", "/api/stream", headers=headers, params={"limit": 1}) as response:
            assert response.status_code == 200
            lines = []
            async for line in response.aiter_lines():
                lines.append(line)

            assert any("event: alert.opened" in ln for ln in lines)
            assert any("id: 1732332800000-1" in ln for ln in lines)
            mock_redis.xrange.assert_awaited_once()


@pytest.mark.asyncio
async def test_stream_db_poll_fallback_when_redis_unavailable():
    """Verify /api/stream falls back to polling the database when Redis is down."""
    mock_redis = MagicMock()
    mock_redis.xread = AsyncMock(side_effect=Exception("Redis stream unavailable"))
    app.state.redis = mock_redis

    db_row = {
        "id": 1,
        "sim_run_id": 5,
        "tick": 77,
        "sim_time": "2026-01-01T12:00:00Z",
        "depots": [],
        "stations": [],
        "routes": [],
        "events": [],
        "supply": [],
        "metrics": {},
        "stale": False,
        "degraded": True,
        "created_at": "2026-01-01T12:00:01Z",
    }

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.mappings.return_value.first.return_value = db_row
    mock_session.execute = AsyncMock(return_value=mock_result)

    async def override_get_session():
        yield mock_session

    app.dependency_overrides[get_session] = override_get_session
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            async with client.stream("GET", "/api/stream", params={"limit": 1}) as response:
                assert response.status_code == 200
                lines = []
                async for line in response.aiter_lines():
                    lines.append(line)

                assert any("id: db-77" in ln for ln in lines)
                assert any("event: state.updated" in ln for ln in lines)
    finally:
        app.dependency_overrides.pop(get_session, None)
