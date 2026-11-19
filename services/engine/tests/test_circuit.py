"""Unit tests for the simulator asynchronous circuit breaker."""
from __future__ import annotations

import asyncio

import pytest
from app.sim.circuit import CircuitBreaker, CircuitOpenError
from prometheus_client import REGISTRY


class MockClock:
    """Controllable clock for deterministic time progression without real sleeps."""

    def __init__(self, start: float = 1000.0):
        self.current = start

    def time(self) -> float:
        return self.current

    def advance(self, seconds: float) -> None:
        self.current += seconds


class MockDomainError(Exception):
    """Simulated business/domain error (e.g. 404, 409, 422)."""


def get_gauge_value(metric_name: str, label_name: str) -> float | None:
    for metric in REGISTRY.collect():
        if metric.name == metric_name:
            for sample in metric.samples:
                if sample.labels.get("name") == label_name:
                    return sample.value
    return None


# ---------------------------------------------------------------- Tests
def test_initial_closed_state():
    clock = MockClock()
    cb = CircuitBreaker(name="test_init", failure_threshold=3, reset_timeout=15.0, time_fn=clock.time)
    assert cb.state == CircuitBreaker.CLOSED
    assert cb.failures == 0
    assert cb.allow() is True
    assert get_gauge_value("fsip_breaker_state", "test_init") == 0.0


def test_failure_threshold_opens_breaker():
    clock = MockClock()
    cb = CircuitBreaker(name="test_thresh", failure_threshold=3, reset_timeout=15.0, time_fn=clock.time)

    # 1st failure
    cb.record_failure()
    assert cb.state == CircuitBreaker.CLOSED
    assert cb.failures == 1
    assert cb.allow() is True
    assert get_gauge_value("fsip_breaker_state", "test_thresh") == 0.0

    # 2nd failure
    cb.record_failure()
    assert cb.state == CircuitBreaker.CLOSED
    assert cb.failures == 2
    assert cb.allow() is True

    # 3rd failure -> trips to OPEN
    cb.record_failure()
    assert cb.state == CircuitBreaker.OPEN
    assert cb.failures == 3
    assert cb.allow() is False
    assert get_gauge_value("fsip_breaker_state", "test_thresh") == 1.0


def test_success_resets_failure_count():
    clock = MockClock()
    cb = CircuitBreaker(name="test_reset_failures", failure_threshold=3, reset_timeout=15.0, time_fn=clock.time)

    cb.record_failure()
    cb.record_failure()
    assert cb.failures == 2
    assert cb.state == CircuitBreaker.CLOSED

    cb.record_success()
    assert cb.failures == 0
    assert cb.state == CircuitBreaker.CLOSED


@pytest.mark.asyncio
async def test_async_context_manager_success_and_failures():
    clock = MockClock()
    cb = CircuitBreaker(name="test_ctx", failure_threshold=2, reset_timeout=10.0, time_fn=clock.time)

    # Success call
    async with cb:
        result = "ok"
    assert result == "ok"
    assert cb.state == CircuitBreaker.CLOSED
    assert cb.failures == 0

    # 1st failure
    with pytest.raises(RuntimeError):
        async with cb:
            raise RuntimeError("call 1 failed")
    assert cb.failures == 1
    assert cb.state == CircuitBreaker.CLOSED

    # 2nd failure -> trips OPEN
    with pytest.raises(RuntimeError):
        async with cb:
            raise RuntimeError("call 2 failed")
    assert cb.state == CircuitBreaker.OPEN
    assert get_gauge_value("fsip_breaker_state", "test_ctx") == 1.0

    # 3rd call immediately rejected with CircuitOpenError
    with pytest.raises(CircuitOpenError) as exc_info:
        async with cb:
            pytest.fail("Should not execute inner block when OPEN")
    assert "test_ctx" in str(exc_info.value)


@pytest.mark.asyncio
async def test_reset_timeout_permits_half_open_and_recovery():
    clock = MockClock(100.0)
    cb = CircuitBreaker(name="test_recovery", failure_threshold=2, reset_timeout=15.0, time_fn=clock.time)

    # Trip to OPEN
    cb.record_failure()
    cb.record_failure()
    assert cb.state == CircuitBreaker.OPEN

    # 10s later (less than reset_timeout) -> still OPEN
    clock.advance(10.0)
    with pytest.raises(CircuitOpenError):
        async with cb:
            pass
    assert cb.state == CircuitBreaker.OPEN
    assert get_gauge_value("fsip_breaker_state", "test_recovery") == 1.0

    # Advance past reset_timeout (15s total) -> transitions to HALF_OPEN
    clock.advance(5.0)  # Total 15s elapsed
    assert cb.allow() is True
    assert cb.state == CircuitBreaker.HALF_OPEN
    assert get_gauge_value("fsip_breaker_state", "test_recovery") == 2.0

    # Probe succeeds -> transitions to CLOSED
    cb.record_success()
    assert cb.state == CircuitBreaker.CLOSED
    assert cb.failures == 0
    assert get_gauge_value("fsip_breaker_state", "test_recovery") == 0.0


@pytest.mark.asyncio
async def test_failed_recovery_reopens_breaker():
    clock = MockClock(200.0)
    cb = CircuitBreaker(name="test_reopen", failure_threshold=2, reset_timeout=15.0, time_fn=clock.time)

    # Trip to OPEN
    cb.record_failure()
    cb.record_failure()
    assert cb.state == CircuitBreaker.OPEN

    # Advance past reset_timeout
    clock.advance(15.0)

    # Half-open probe fails
    with pytest.raises(RuntimeError):
        async with cb:
            assert cb.state == CircuitBreaker.HALF_OPEN
            raise RuntimeError("probe failed")

    # Breaker immediately reopens
    assert cb.state == CircuitBreaker.OPEN
    assert get_gauge_value("fsip_breaker_state", "test_reopen") == 1.0

    # Next call blocked
    with pytest.raises(CircuitOpenError):
        async with cb:
            pass


@pytest.mark.asyncio
async def test_half_open_rejects_concurrent_callers():
    clock = MockClock(300.0)
    cb = CircuitBreaker(name="test_half_open_probe", failure_threshold=1, reset_timeout=10.0, time_fn=clock.time)

    cb.record_failure()
    assert cb.state == CircuitBreaker.OPEN

    # Advance past reset timeout
    clock.advance(10.0)

    probe_started = asyncio.Event()
    probe_finish = asyncio.Event()

    async def probe_task():
        async with cb:
            probe_started.set()
            await probe_finish.wait()
            return "probe_success"

    # Start the probe task
    task1 = asyncio.create_task(probe_task())
    await probe_started.wait()

    # Breaker is in HALF_OPEN with probe in flight
    assert cb.state == CircuitBreaker.HALF_OPEN

    # Second caller attempts entry while probe is running
    with pytest.raises(CircuitOpenError):
        async with cb:
            pass

    # Allow probe task to complete successfully
    probe_finish.set()
    res = await task1
    assert res == "probe_success"

    # Now breaker is CLOSED and allows further calls
    assert cb.state == CircuitBreaker.CLOSED
    assert cb.allow() is True


@pytest.mark.asyncio
async def test_cancellation_releases_probe_lock():
    clock = MockClock(400.0)
    cb = CircuitBreaker(name="test_cancel", failure_threshold=1, reset_timeout=10.0, time_fn=clock.time)

    cb.record_failure()
    assert cb.state == CircuitBreaker.OPEN

    clock.advance(10.0)

    probe_started = asyncio.Event()

    async def probe_task():
        async with cb:
            probe_started.set()
            await asyncio.sleep(100.0)

    task = asyncio.create_task(probe_task())
    await probe_started.wait()

    # Cancel probe while inside context manager
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Breaker should not be stuck; next probe must be allowed
    assert cb.state == CircuitBreaker.HALF_OPEN
    assert cb._probe_in_flight is False
    assert cb.allow() is True


@pytest.mark.asyncio
async def test_excluded_exceptions_do_not_trip_breaker():
    clock = MockClock()
    cb = CircuitBreaker(
        name="test_exclude",
        failure_threshold=2,
        reset_timeout=10.0,
        exclude_exceptions=(MockDomainError,),
        time_fn=clock.time,
    )

    # Domain error occurs 5 times
    for _ in range(5):
        with pytest.raises(MockDomainError):
            async with cb:
                raise MockDomainError("404 Not Found")

    # Breaker should remain CLOSED with 0 failures
    assert cb.state == CircuitBreaker.CLOSED
    assert cb.failures == 0


@pytest.mark.asyncio
async def test_concurrent_requests_safe_transitions():
    clock = MockClock()
    cb = CircuitBreaker(name="test_concurrent", failure_threshold=5, reset_timeout=10.0, time_fn=clock.time)

    async def fail_call():
        try:
            async with cb:
                raise RuntimeError("failure")
        except RuntimeError:
            pass
        except CircuitOpenError:
            pass

    # Run 20 concurrent failing requests
    await asyncio.gather(*(fail_call() for _ in range(20)))

    # Breaker must be cleanly OPEN
    assert cb.state == CircuitBreaker.OPEN
    assert cb.failures >= 5
    assert get_gauge_value("fsip_breaker_state", "test_concurrent") == 1.0
