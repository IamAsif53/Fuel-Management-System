"""Tests for API rate limiting middleware."""
from __future__ import annotations

import httpx
import pytest
from app.config import get_settings
from app.main import app
from app.middleware.ratelimit import _in_memory_counters


@pytest.mark.asyncio
async def test_rate_limiting_enforcement():
    """Verify rate limit middleware returns 429 when exceeding quota."""
    settings = get_settings()
    original_limit = settings.api_rate_limit_per_min
    settings.api_rate_limit_per_min = 3
    _in_memory_counters.clear()

    # Clear redis on app.state to test in-memory fallback directly
    old_redis = getattr(app.state, "redis", None)
    app.state.redis = None

    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            # 3 allowed requests
            for _ in range(3):
                r = await client.get("/api/ping")
                assert r.status_code == 200

            # 4th request must be rate limited
            r4 = await client.get("/api/ping")
            assert r4.status_code == 429
            assert r4.headers.get("Retry-After") == "60"
            assert r4.json()["detail"]["code"] == "RATE_LIMITED"

            # Exempt endpoints must still pass
            r_live = await client.get("/health/live")
            assert r_live.status_code == 200
    finally:
        settings.api_rate_limit_per_min = original_limit
        app.state.redis = old_redis
        _in_memory_counters.clear()
