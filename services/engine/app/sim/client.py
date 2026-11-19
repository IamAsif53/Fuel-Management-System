"""Resilient asynchronous HTTP client for BUP Fuel Supply Simulator.

Features:
- Singleton/managed httpx.AsyncClient with connection pooling and timeouts.
- Exponential backoff with jitter on transient infrastructure failures (503, timeouts, network errors).
- CircuitBreaker integration protecting simulator and recovering via half-open probe.
- Dual error envelope parsing (domain 404/409, fault 503, validation 422) via SimErrorEnvelope.
- Stale response detection from x-simulator-stale header.
- Prometheus metrics: fsip_sim_request_duration_seconds, fsip_sim_errors_total.
- Structured JSON logging.
"""
from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx
from fsip_common.logging import get_logger
from fsip_common.models import AllocationRequest, EventCreate, FaultCreate, SimErrorDetail, SimErrorEnvelope
from prometheus_client import REGISTRY, Counter, Histogram
from pydantic import BaseModel

from .circuit import CircuitBreaker, CircuitOpenError

log = get_logger()

# ---------------------------------------------------------------- Metrics
try:
    FSIP_SIM_REQUEST_DURATION = Histogram(
        "fsip_sim_request_duration_seconds",
        "Simulator request duration in seconds",
        ["endpoint", "status"],
        buckets=(0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75, 1.0, 2.0, 5.0),
    )
except ValueError:
    FSIP_SIM_REQUEST_DURATION = REGISTRY._names_to_collectors.get("fsip_sim_request_duration_seconds")  # type: ignore[assignment]

try:
    FSIP_SIM_ERRORS_TOTAL = Counter(
        "fsip_sim_errors_total",
        "Total errors encountered when calling simulator",
        ["endpoint", "code"],
    )
except ValueError:
    FSIP_SIM_ERRORS_TOTAL = REGISTRY._names_to_collectors.get("fsip_sim_errors_total")  # type: ignore[assignment]


# ---------------------------------------------------------------- Exceptions
class SimError(Exception):
    """Base exception for all simulator client errors."""


class SimUnavailable(SimError):
    """Network timeout, connection drop, or unformatted 5xx response."""


class SimFaultInjected(SimError):
    """Simulator injected fault (503 FAULT_INJECTED or stream_disconnect)."""

    def __init__(self, message: str = "Simulated fault injected: unavailable"):
        super().__init__(message)
        self.message = message


class SimDomainError(SimError):
    """Simulator domain rejection (404, 409, etc.)."""

    def __init__(self, code: str, message: str, status: int = 409, data: dict[str, Any] | None = None):
        super().__init__(f"{status} {code}: {message}")
        self.code = code
        self.message = message
        self.status = status
        self.data = data or {}


class SimValidationError(SimError):
    """Simulator HTTP 422 input validation failure."""

    def __init__(self, details: list[Any], message: str = ""):
        super().__init__(message or str(details))
        self.details = details
        self.message = message or str(details)


class SimInvalidPayload(SimError):
    """Raised when simulator returns an invalid, malformed, or unparseable response body."""

    def __init__(self, resource: str, errors: Any, raw: Any = None):
        super().__init__(f"Invalid response payload for {resource}: {errors}")
        self.resource = resource
        self.errors = errors
        self.raw = raw


RETRYABLE_EXCEPTIONS = (SimUnavailable, SimFaultInjected)


# ---------------------------------------------------------------- Result container
@dataclass
class SimResult:
    data: Any
    stale: bool
    status: int
    latency_ms: float


# ---------------------------------------------------------------- Error parsing helper
def parse_error(resp: httpx.Response, endpoint: str = "") -> SimError:
    """Parse simulator error response body into typed domain exception using SimErrorEnvelope."""
    try:
        body = resp.json()
    except Exception:
        return SimUnavailable(f"{resp.status_code} non-json response: {resp.text[:200]}")

    if not isinstance(body, dict):
        if resp.status_code == 422 and isinstance(body, list):
            return SimValidationError(details=body, message=str(body)[:200])
        return SimUnavailable(f"{resp.status_code} unexpected response format: {str(body)[:200]}")

    try:
        envelope = SimErrorEnvelope.model_validate(body)
        if envelope.is_fault_injected:
            return SimFaultInjected(envelope.message or "Simulated fault injected: unavailable")
        if envelope.is_validation_error or resp.status_code == 422:
            details = [err.model_dump() for err in envelope.validation_errors] if envelope.validation_errors else (
                envelope.detail if isinstance(envelope.detail, list) else []
            )
            return SimValidationError(details=details, message=envelope.message)

        data = envelope.detail.data if isinstance(envelope.detail, SimErrorDetail) else {}
        return SimDomainError(
            code=envelope.code or "UNKNOWN",
            message=envelope.message,
            status=resp.status_code,
            data=data,
        )
    except Exception:
        # Fallback if envelope parsing fails on malformed body
        if resp.status_code == 503 or resp.status_code >= 500:
            return SimUnavailable(f"{resp.status_code} {str(body)[:200]}")
        if resp.status_code == 422:
            return SimValidationError(details=[body], message=str(body)[:200])
        return SimDomainError(code="UNKNOWN", message=str(body)[:200], status=resp.status_code)


def _normalize_endpoint(path: str) -> str:
    """Map concrete path to low-cardinality endpoint template for metrics."""
    clean = path.split("?")[0].strip("/").split("/")
    if len(clean) == 4 and clean[0] == "v1" and clean[1] == "allocations" and clean[3] == "cancel":
        return "/v1/allocations/{id}/cancel"
    return "/" + "/".join(clean)


# ---------------------------------------------------------------- SimClient
class SimClient:
    """Asynchronous client for communicating with BUP Fuel Supply Simulator."""

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        timeout: float = 2.0,
        max_retries: int = 3,
        breaker: CircuitBreaker | None = None,
        initial_backoff_sec: float = 0.2,
        max_backoff_sec: float = 2.0,
        sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep,
        http_client: httpx.AsyncClient | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max(0, max_retries)
        self.breaker = breaker or CircuitBreaker("simulator")
        self.initial_backoff_sec = initial_backoff_sec
        self.max_backoff_sec = max_backoff_sec
        self._sleep_fn = sleep_fn

        if http_client is not None:
            self.http = http_client
            self._owns_http = False
        else:
            self.http = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout,
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            )
            self._owns_http = True

    async def close(self) -> None:
        """Close underlying HTTP client."""
        if self._owns_http:
            await self.http.aclose()

    async def __aenter__(self) -> SimClient:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        retry: bool = True,
    ) -> SimResult:
        """Execute request with retries, circuit breaker, metrics, and structured logging."""
        endpoint = _normalize_endpoint(path)
        attempts = self.max_retries + 1 if retry else 1
        last_exception: Exception | None = None

        for attempt in range(attempts):
            if not self.breaker.allow():
                if FSIP_SIM_ERRORS_TOTAL is not None:
                    FSIP_SIM_ERRORS_TOTAL.labels(endpoint=endpoint, code="CIRCUIT_OPEN").inc()
                raise CircuitOpenError(self.breaker.name)

            t0 = time.perf_counter()
            resp: httpx.Response | None = None
            try:
                resp = await self.http.request(method, path, json=json, params=params)
                latency_sec = time.perf_counter() - t0
                latency_ms = latency_sec * 1000.0

                if resp.status_code < 400:
                    self.breaker.record_success()
                    if FSIP_SIM_REQUEST_DURATION is not None:
                        FSIP_SIM_REQUEST_DURATION.labels(endpoint=endpoint, status=str(resp.status_code)).observe(
                            latency_sec
                        )

                    stale = resp.headers.get("x-simulator-stale", "").lower() == "true"
                    try:
                        data = resp.json()
                    except Exception as e:
                        raise SimInvalidPayload(endpoint, f"Failed to parse JSON response: {e}", resp.text) from e

                    log.info(
                        "sim.request.ok",
                        method=method,
                        endpoint=endpoint,
                        status=resp.status_code,
                        latency_ms=round(latency_ms, 2),
                        stale=stale,
                    )
                    return SimResult(data=data, stale=stale, status=resp.status_code, latency_ms=latency_ms)

                # Non-2xx response: parse error envelope
                err = parse_error(resp, endpoint)
                if not isinstance(err, RETRYABLE_EXCEPTIONS):
                    # Domain errors (404, 409, 422) indicate the simulator is online and functioning
                    self.breaker.record_success()
                    code = getattr(err, "code", "VALIDATION_ERROR")
                    if FSIP_SIM_ERRORS_TOTAL is not None:
                        FSIP_SIM_ERRORS_TOTAL.labels(endpoint=endpoint, code=code).inc()
                    if FSIP_SIM_REQUEST_DURATION is not None:
                        FSIP_SIM_REQUEST_DURATION.labels(endpoint=endpoint, status=str(resp.status_code)).observe(
                            latency_sec
                        )
                    raise err

                # Retryable infrastructure failure (e.g. 503 FAULT_INJECTED)
                self.breaker.record_failure()
                last_exception = err
                if FSIP_SIM_ERRORS_TOTAL is not None:
                    FSIP_SIM_ERRORS_TOTAL.labels(endpoint=endpoint, code="FAULT_INJECTED").inc()
                if FSIP_SIM_REQUEST_DURATION is not None:
                    FSIP_SIM_REQUEST_DURATION.labels(endpoint=endpoint, status=str(resp.status_code)).observe(
                        latency_sec
                    )

            except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError) as e:
                latency_sec = time.perf_counter() - t0
                err_code = "TIMEOUT" if isinstance(e, httpx.TimeoutException) else "CONNECTION_ERROR"
                if FSIP_SIM_REQUEST_DURATION is not None:
                    FSIP_SIM_REQUEST_DURATION.labels(endpoint=endpoint, status="error").observe(latency_sec)
                if FSIP_SIM_ERRORS_TOTAL is not None:
                    FSIP_SIM_ERRORS_TOTAL.labels(endpoint=endpoint, code=err_code).inc()

                last_exception = SimUnavailable(f"{type(e).__name__}: {e}")
                self.breaker.record_failure()

            # If more attempts remain, back off
            if attempt < attempts - 1:
                log.warning(
                    "sim.request.retry",
                    method=method,
                    endpoint=endpoint,
                    attempt=attempt + 1,
                    max_attempts=attempts,
                    error=str(last_exception),
                )
                await self._sleep_backoff(attempt, resp)

        log.error(
            "sim.request.failed",
            method=method,
            endpoint=endpoint,
            attempts=attempts,
            error=str(last_exception),
        )
        raise last_exception  # type: ignore[misc]

    async def _sleep_backoff(self, attempt: int, resp: httpx.Response | None = None) -> None:
        """Handle exponential backoff with jitter and Retry-After support."""
        if resp is not None:
            retry_after = resp.headers.get("retry-after")
            if retry_after:
                try:
                    delay = float(retry_after)
                    if delay > 0:
                        await self._sleep_fn(min(self.max_backoff_sec, delay))
                        return
                except ValueError:
                    pass

        base_delay = min(self.max_backoff_sec, self.initial_backoff_sec * (2 ** attempt))
        jittered_delay = base_delay * (0.5 + random.random())
        await self._sleep_fn(jittered_delay)

    # ---------------------------------------------------------------- Read operations
    async def health(self) -> SimResult:
        """GET /v1/health"""
        return await self._request("GET", "/v1/health")

    async def instance(self) -> SimResult:
        """GET /v1/instance"""
        return await self._request("GET", "/v1/instance")

    async def regions(self) -> SimResult:
        """GET /v1/regions"""
        return await self._request("GET", "/v1/regions")

    async def depots(self) -> SimResult:
        """GET /v1/depots"""
        return await self._request("GET", "/v1/depots")

    async def stations(self) -> SimResult:
        """GET /v1/stations"""
        return await self._request("GET", "/v1/stations")

    async def routes(self) -> SimResult:
        """GET /v1/routes"""
        return await self._request("GET", "/v1/routes")

    async def supply_arrivals(self) -> SimResult:
        """GET /v1/supply-arrivals"""
        return await self._request("GET", "/v1/supply-arrivals")

    async def events(self, status: str | None = None) -> SimResult:
        """GET /v1/events"""
        params = {"status": status} if status else None
        return await self._request("GET", "/v1/events", params=params)

    async def allocations(self, status: str | None = None) -> SimResult:
        """GET /v1/allocations"""
        params = {"status": status} if status else None
        return await self._request("GET", "/v1/allocations", params=params)

    async def demand_history(
        self, station_id: str | None = None, fuel_type: str | None = None, limit: int = 200
    ) -> SimResult:
        """GET /v1/demand-history"""
        p: dict[str, Any] = {"limit": max(1, min(2000, limit))}
        if station_id:
            p["station_id"] = station_id
        if fuel_type:
            p["fuel_type"] = fuel_type
        return await self._request("GET", "/v1/demand-history", params=p)

    async def metrics(self) -> SimResult:
        """GET /v1/metrics"""
        return await self._request("GET", "/v1/metrics")

    async def snapshot(self) -> dict[str, SimResult | Exception]:
        """Parallel read of all key simulator resources."""
        names = ["instance", "depots", "stations", "routes", "events", "supply_arrivals", "allocations", "metrics"]
        res = await asyncio.gather(*(getattr(self, n)() for n in names), return_exceptions=True)
        return dict(zip(names, res, strict=False))

    # ---------------------------------------------------------------- Production write operations
    async def create_allocation(self, req: AllocationRequest | dict[str, Any]) -> SimResult:
        """POST /v1/allocations (idempotent write protected by idempotency_key)."""
        body = req.model_dump(mode="json") if isinstance(req, BaseModel) else req
        return await self._request("POST", "/v1/allocations", json=body)

    async def cancel_allocation(self, alloc_id: int) -> SimResult:
        """POST /v1/allocations/{id}/cancel (no retry to prevent non-idempotent re-submission)."""
        return await self._request("POST", f"/v1/allocations/{alloc_id}/cancel", retry=False)

    # ---------------------------------------------------------------- Admin operations (tests/harness/demo only)
    async def admin_step(self) -> SimResult:
        """POST /admin/step"""
        return await self.admin("POST", "step")

    async def admin_reset(self) -> SimResult:
        """POST /admin/reset"""
        return await self.admin("POST", "reset")

    async def admin_run(self) -> SimResult:
        """POST /admin/run"""
        return await self.admin("POST", "run")

    async def admin_pause(self) -> SimResult:
        """POST /admin/pause"""
        return await self.admin("POST", "pause")

    async def admin_events_create(self, body: EventCreate | dict[str, Any]) -> SimResult:
        """POST /admin/events"""
        payload = body.model_dump(mode="json") if isinstance(body, BaseModel) else body
        return await self.admin("POST", "events", json=payload)

    async def admin_faults_create(self, body: FaultCreate | dict[str, Any]) -> SimResult:
        """POST /admin/faults"""
        payload = body.model_dump(mode="json") if isinstance(body, BaseModel) else body
        return await self.admin("POST", "faults", json=payload)

    async def admin_faults_clear(self) -> SimResult:
        """POST /admin/faults/clear"""
        return await self.admin("POST", "faults/clear")

    async def admin_audit(self, limit: int = 100) -> SimResult:
        """GET /admin/audit?limit={limit}"""
        return await self._request("GET", "/admin/audit", params={"limit": max(1, min(1000, limit))}, retry=False)

    async def admin(
        self, method: str, path: str, json: dict[str, Any] | None = None, params: dict[str, Any] | None = None
    ) -> SimResult:
        """Execute admin call (never retried)."""
        clean_path = path.lstrip("/")
        return await self._request(method, f"/admin/{clean_path}", json=json, params=params, retry=False)
