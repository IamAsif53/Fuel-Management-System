"""Rate limiting middleware using Redis counter with sliding in-memory fallback."""
from __future__ import annotations

import time
from collections.abc import Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from fsip_common.logging import get_logger
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import get_settings

log = get_logger()

# In-memory fallback tracking: ip -> (count, window_start_time)
_in_memory_counters: dict[str, tuple[int, float]] = {}


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Enforces per-IP rate limits using Redis counter with in-memory fallback."""

    EXEMPT_PATHS = {
        "/health/live",
        "/health/ready",
        "/metrics",
        "/metrics/",
        "/api/docs",
        "/api/openapi.json",
        "/api/stream",
    }

    async def dispatch(self, request: Request, call_next: Callable[[Request], Response]) -> Response:
        path = request.url.path
        if path in self.EXEMPT_PATHS or path.startswith("/api/docs"):
            return await call_next(request)

        settings = get_settings()
        limit = settings.api_rate_limit_per_min

        # Determine client IP
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            client_ip = forwarded.split(",")[0].strip()
        else:
            client_ip = request.client.host if request.client else "unknown"

        is_allowed = await self._check_rate_limit(request, client_ip, limit)
        if not is_allowed:
            log.warning("api.rate_limited", ip=client_ip, path=path)
            return JSONResponse(
                status_code=429,
                headers={"Retry-After": "60"},
                content={"detail": {"code": "RATE_LIMITED", "message": "Rate limit exceeded"}},
            )

        return await call_next(request)

    async def _check_rate_limit(self, request: Request, ip: str, limit: int) -> bool:
        redis_client = getattr(request.app.state, "redis", None)
        if redis_client is not None:
            try:
                key = f"fsip:ratelimit:{ip}"
                async with redis_client.pipeline(transaction=False) as pipe:
                    pipe.incr(key)
                    pipe.expire(key, 60)
                    results = await pipe.execute()
                if results and isinstance(results[0], int):
                    count = results[0]
                    return count <= limit
            except Exception as exc:
                log.warning("api.ratelimit.redis_failed_falling_back", error=str(exc))

        # In-memory fallback
        now = time.time()
        count, window_start = _in_memory_counters.get(ip, (0, now))
        if now - window_start > 60.0:
            count = 1
            window_start = now
        else:
            count += 1

        _in_memory_counters[ip] = (count, window_start)

        # Clean old entries periodically
        if len(_in_memory_counters) > 5000:
            cutoff = now - 60.0
            stale_ips = [k for k, (_, w_start) in _in_memory_counters.items() if w_start < cutoff]
            for stale_ip in stale_ips:
                _in_memory_counters.pop(stale_ip, None)

        return count <= limit
