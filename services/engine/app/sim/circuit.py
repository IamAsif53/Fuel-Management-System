"""Asynchronous circuit breaker for external service communication (simulator, intelligence).

States:
- CLOSED: Requests are allowed through. Failures are counted. If failures >= failure_threshold,
  the breaker opens.
- OPEN: Requests are rejected immediately with CircuitOpenError. After reset_timeout seconds,
  the breaker transitions to HALF_OPEN.
- HALF_OPEN: A single probe request is allowed through to test service recovery. If the probe
  succeeds, the breaker returns to CLOSED. If it fails, the breaker reopens for reset_timeout.

Observability:
- Gauge: fsip_breaker_state{name} (0=CLOSED, 1=OPEN, 2=HALF_OPEN)
- Log event: breaker.state_change (name, old_state, new_state)
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any

from fsip_common.logging import get_logger
from prometheus_client import REGISTRY, Gauge

log = get_logger()

# Metric: fsip_breaker_state{name}
try:
    FSIP_BREAKER_STATE = Gauge(
        "fsip_breaker_state",
        "Circuit breaker state: 0=CLOSED, 1=OPEN, 2=HALF_OPEN",
        ["name"],
    )
except ValueError:
    # Collector already registered in registry
    FSIP_BREAKER_STATE = REGISTRY._names_to_collectors.get("fsip_breaker_state")  # type: ignore[assignment]


class CircuitOpenError(Exception):
    """Raised when an operation is attempted while the circuit breaker is OPEN or busy in HALF_OPEN."""

    def __init__(self, name: str = "simulator", message: str = "Circuit breaker is OPEN"):
        super().__init__(f"{name}: {message}")
        self.name = name


class CircuitBreaker:
    """Reusable asynchronous circuit breaker."""

    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"

    STATE_METRIC_MAP = {
        "CLOSED": 0,
        "OPEN": 1,
        "HALF_OPEN": 2,
    }

    def __init__(
        self,
        name: str = "simulator",
        failure_threshold: int = 5,
        reset_timeout: float = 15.0,
        success_threshold: int = 1,
        time_fn: Callable[[], float] = time.monotonic,
        gauge: Any = FSIP_BREAKER_STATE,
        exclude_exceptions: tuple[type[Exception], ...] = (),
        is_failure: Callable[[Exception], bool] | None = None,
    ):
        self.name = name
        self.failure_threshold = max(1, failure_threshold)
        self.reset_timeout = max(0.0, reset_timeout)
        self.success_threshold = max(1, success_threshold)
        self.time_fn = time_fn
        self._gauge = gauge
        self.exclude_exceptions = tuple(exclude_exceptions)
        self.is_failure = is_failure

        self.state: str = self.CLOSED
        self.failures: int = 0
        self.successes: int = 0
        self.opened_at: float = 0.0
        self._probe_in_flight: bool = False
        self._async_lock = asyncio.Lock()

        # Initialize gauge for this breaker instance
        self._update_metric()

    def _update_metric(self) -> None:
        if self._gauge is not None:
            try:
                val = self.STATE_METRIC_MAP.get(self.state, 0)
                self._gauge.labels(name=self.name).set(val)
            except Exception:  # noqa: BLE001
                pass

    def _transition(self, new_state: str) -> None:
        if new_state != self.state:
            old_state = self.state
            self.state = new_state

            if new_state == self.CLOSED:
                self.failures = 0
                self.successes = 0
                self._probe_in_flight = False
            elif new_state == self.HALF_OPEN:
                self.successes = 0
            elif new_state == self.OPEN:
                self._probe_in_flight = False

            self._update_metric()
            log.info(
                "breaker.state_change",
                name=self.name,
                old_state=old_state,
                new_state=new_state,
            )

    def allow(self) -> bool:
        """Synchronous check if a request can proceed.

        Returns True if request is allowed, False if blocked.
        If in OPEN state and reset_timeout has elapsed, transitions to HALF_OPEN
        and permits the first caller as a probe.
        """
        now = self.time_fn()
        if self.state == self.OPEN:
            if now - self.opened_at >= self.reset_timeout:
                self._transition(self.HALF_OPEN)
            else:
                return False

        if self.state == self.HALF_OPEN:
            if self._probe_in_flight:
                return False
            self._probe_in_flight = True
            return True

        return True

    def record_success(self) -> None:
        """Record a successful operation."""
        if self.state == self.HALF_OPEN:
            self.successes += 1
            if self.successes >= self.success_threshold:
                self._transition(self.CLOSED)
            else:
                self._probe_in_flight = False
        elif self.state == self.CLOSED:
            self.failures = 0

    def record_failure(self) -> None:
        """Record a failed operation."""
        self.failures += 1
        if self.state == self.HALF_OPEN or self.failures >= self.failure_threshold:
            self.opened_at = self.time_fn()
            self._transition(self.OPEN)

    def _is_failure_exception(self, exc_val: Exception) -> bool:
        """Determine if an exception should be counted as a circuit failure."""
        if self.is_failure is not None:
            return self.is_failure(exc_val)
        if self.exclude_exceptions and isinstance(exc_val, self.exclude_exceptions):
            return False
        return True

    async def __aenter__(self) -> CircuitBreaker:
        """Async context manager entrance: checks allow() and raises CircuitOpenError if blocked."""
        async with self._async_lock:
            if not self.allow():
                raise CircuitOpenError(self.name)
        return self

    async def __aexit__(self, exc_type: type[Exception] | None, exc_val: Exception | None, exc_tb: Any) -> bool:
        """Async context manager exit: updates success or failure."""
        async with self._async_lock:
            try:
                if exc_type is None:
                    self.record_success()
                elif issubclass(exc_type, CircuitOpenError):
                    # Request was rejected before it started; do not count as failure
                    pass
                elif issubclass(exc_type, asyncio.CancelledError):
                    # Task cancelled; do not count as a failure
                    pass
                elif exc_val is not None and not self._is_failure_exception(exc_val):
                    # Excluded exception (e.g. domain 404/409, client validation error); service is alive
                    self.record_success()
                else:
                    self.record_failure()
            finally:
                if self.state == self.HALF_OPEN and self._probe_in_flight:
                    self._probe_in_flight = False
        return False  # Never suppress exceptions
