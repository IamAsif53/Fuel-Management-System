"""Unit tests for the resilient simulator HTTP client using respx and fixtures."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from app.sim.circuit import CircuitBreaker, CircuitOpenError
from app.sim.client import (
    FSIP_SIM_ERRORS_TOTAL,
    FSIP_SIM_REQUEST_DURATION,
    SimClient,
    SimDomainError,
    SimFaultInjected,
    SimInvalidPayload,
    SimResult,
    SimUnavailable,
    SimValidationError,
)
from fsip_common.models import AllocationRequest, EventCreate, FaultCreate

FX = Path(__file__).resolve().parents[3] / "libs" / "fsip_common" / "tests" / "fixtures"


def load_fx(name: str) -> Any:
    return json.loads((FX / f"{name}.json").read_text())


class MockSleeper:
    def __init__(self):
        self.delays: list[float] = []

    async def sleep(self, delay: float) -> None:
        self.delays.append(delay)


@pytest.fixture
def sleeper() -> MockSleeper:
    return MockSleeper()


@pytest.fixture
def breaker() -> CircuitBreaker:
    return CircuitBreaker(name="test_sim_breaker", failure_threshold=3, reset_timeout=5.0)


# ---------------------------------------------------------------- Basic Read Endpoints
@respx.mock
@pytest.mark.asyncio
async def test_read_endpoints(sleeper: MockSleeper, breaker: CircuitBreaker):
    client = SimClient(
        base_url="http://testsim:8000",
        breaker=breaker,
        sleep_fn=sleeper.sleep,
    )

    respx.get("http://testsim:8000/v1/health").respond(200, json=load_fx("health"))
    respx.get("http://testsim:8000/v1/instance").respond(200, json=load_fx("instance"))
    respx.get("http://testsim:8000/v1/regions").respond(200, json=load_fx("regions"))
    respx.get("http://testsim:8000/v1/depots").respond(200, json=load_fx("depots"))
    respx.get("http://testsim:8000/v1/stations").respond(200, json=load_fx("stations"))
    respx.get("http://testsim:8000/v1/routes").respond(200, json=load_fx("routes"))
    respx.get("http://testsim:8000/v1/supply-arrivals").respond(200, json=load_fx("supply_arrivals"))
    respx.get("http://testsim:8000/v1/events").respond(200, json=load_fx("events"))
    respx.get("http://testsim:8000/v1/allocations").respond(200, json=load_fx("allocations"))
    respx.get("http://testsim:8000/v1/demand-history").respond(200, json=load_fx("demand_history"))
    respx.get("http://testsim:8000/v1/metrics").respond(200, json=load_fx("metrics"))

    async with client:
        r_health = await client.health()
        assert r_health.status == 200 and r_health.data["status"] == "ok"
        assert r_health.stale is False

        r_inst = await client.instance()
        assert r_inst.data["tick_minutes"] == 15

        r_reg = await client.regions()
        assert len(r_reg.data) == 2

        r_dep = await client.depots()
        assert len(r_dep.data) == 2

        r_sta = await client.stations()
        assert len(r_sta.data) == 4

        r_rou = await client.routes()
        assert len(r_rou.data) == 6

        r_sup = await client.supply_arrivals()
        assert len(r_sup.data) == 1

        r_eve = await client.events()
        assert len(r_eve.data) == 1

        r_all = await client.allocations()
        assert len(r_all.data) == 1

        r_dem = await client.demand_history()
        assert len(r_dem.data) == 1

        r_met = await client.metrics()
        assert r_met.data["service_level"] == 1.0


@respx.mock
@pytest.mark.asyncio
async def test_read_with_query_params(sleeper: MockSleeper, breaker: CircuitBreaker):
    client = SimClient(base_url="http://testsim:8000", breaker=breaker, sleep_fn=sleeper.sleep)

    route_events = respx.get("http://testsim:8000/v1/events", params={"status": "ACTIVE"}).respond(200, json=[])
    route_allocs = respx.get("http://testsim:8000/v1/allocations", params={"status": "PENDING"}).respond(200, json=[])
    route_demand = respx.get(
        "http://testsim:8000/v1/demand-history",
        params={"limit": "50", "station_id": "station-mirpur", "fuel_type": "DIESEL"},
    ).respond(200, json=[])

    async with client:
        await client.events(status="ACTIVE")
        assert route_events.called

        await client.allocations(status="PENDING")
        assert route_allocs.called

        await client.demand_history(station_id="station-mirpur", fuel_type="DIESEL", limit=50)
        assert route_demand.called


@respx.mock
@pytest.mark.asyncio
async def test_snapshot(sleeper: MockSleeper, breaker: CircuitBreaker):
    client = SimClient(base_url="http://testsim:8000", breaker=breaker, sleep_fn=sleeper.sleep)

    respx.get("http://testsim:8000/v1/instance").respond(200, json=load_fx("instance"))
    respx.get("http://testsim:8000/v1/depots").respond(200, json=load_fx("depots"))
    respx.get("http://testsim:8000/v1/stations").respond(200, json=load_fx("stations"))
    respx.get("http://testsim:8000/v1/routes").respond(200, json=load_fx("routes"))
    respx.get("http://testsim:8000/v1/events").respond(200, json=load_fx("events"))
    respx.get("http://testsim:8000/v1/supply-arrivals").respond(200, json=load_fx("supply_arrivals"))
    respx.get("http://testsim:8000/v1/allocations").respond(200, json=load_fx("allocations"))
    respx.get("http://testsim:8000/v1/metrics").respond(200, json=load_fx("metrics"))

    async with client:
        snap = await client.snapshot()
        assert all(isinstance(v, SimResult) for v in snap.values())
        assert len(snap) == 8


# ---------------------------------------------------------------- Production Writes & Safety
@respx.mock
@pytest.mark.asyncio
async def test_create_allocation_with_model_and_dict(sleeper: MockSleeper, breaker: CircuitBreaker):
    client = SimClient(base_url="http://testsim:8000", breaker=breaker, sleep_fn=sleeper.sleep)
    route_alloc = respx.post("http://testsim:8000/v1/allocations").respond(201, json=load_fx("allocation_created_201"))

    req = AllocationRequest(
        idempotency_key="gaz-mir-0-001",
        source_depot_id="depot-gazipur",
        destination_station_id="station-mirpur",
        route_id="route-gazipur-mirpur",
        fuel_type="DIESEL",
        quantity=3000,
    )

    async with client:
        res = await client.create_allocation(req)
        assert res.status == 201
        assert res.data["id"] == 1
        assert route_alloc.called
        sent_body = json.loads(route_alloc.calls.last.request.content)
        assert sent_body["idempotency_key"] == "gaz-mir-0-001"
        assert sent_body["quantity"] == 3000.0


@respx.mock
@pytest.mark.asyncio
async def test_cancel_allocation(sleeper: MockSleeper, breaker: CircuitBreaker):
    client = SimClient(base_url="http://testsim:8000", breaker=breaker, sleep_fn=sleeper.sleep)
    route_cancel = respx.post("http://testsim:8000/v1/allocations/1/cancel").respond(
        200, json={"id": 1, "status": "CANCELLED"}
    )

    async with client:
        res = await client.cancel_allocation(1)
        assert res.status == 200
        assert res.data["status"] == "CANCELLED"
        assert route_cancel.called


# ---------------------------------------------------------------- Admin Operations
@respx.mock
@pytest.mark.asyncio
async def test_admin_operations(sleeper: MockSleeper, breaker: CircuitBreaker):
    client = SimClient(base_url="http://testsim:8000", breaker=breaker, sleep_fn=sleeper.sleep)

    respx.post("http://testsim:8000/admin/step").respond(200, json=load_fx("admin_step"))
    respx.post("http://testsim:8000/admin/reset").respond(200, json={"status": "reset"})
    respx.post("http://testsim:8000/admin/run").respond(200, json={"status": "running"})
    respx.post("http://testsim:8000/admin/pause").respond(200, json={"status": "paused"})
    respx.post("http://testsim:8000/admin/events").respond(200, json=load_fx("admin_event_created"))
    respx.post("http://testsim:8000/admin/faults").respond(200, json=load_fx("admin_fault_created"))
    respx.post("http://testsim:8000/admin/faults/clear").respond(200, json={"status": "cleared"})
    respx.get("http://testsim:8000/admin/audit").respond(200, json=load_fx("admin_audit"))

    async with client:
        step_res = await client.admin_step()
        assert step_res.data["tick"] == 3

        reset_res = await client.admin_reset()
        assert reset_res.data["status"] == "reset"

        run_res = await client.admin_run()
        assert run_res.data["status"] == "running"

        pause_res = await client.admin_pause()
        assert pause_res.data["status"] == "paused"

        ev_res = await client.admin_events_create(
            EventCreate(type="demand_spike", start_tick=0, duration_ticks=8, parameters={"multiplier": 1.8})
        )
        assert ev_res.data["id"] == 1

        fault_res = await client.admin_faults_create(FaultCreate(type="unavailable", duration_seconds=3))
        assert fault_res.data["type"] == "unavailable"

        clear_res = await client.admin_faults_clear()
        assert clear_res.data["status"] == "cleared"

        audit_res = await client.admin_audit(limit=10)
        assert len(audit_res.data) == 1


# ---------------------------------------------------------------- Retry & Backoff Behavior
@respx.mock
@pytest.mark.asyncio
async def test_retry_503_twice_then_200_succeeds(sleeper: MockSleeper, breaker: CircuitBreaker):
    client = SimClient(base_url="http://testsim:8000", breaker=breaker, sleep_fn=sleeper.sleep, max_retries=3)

    # 503 x 2 then 200
    route = respx.get("http://testsim:8000/v1/instance").mock(
        side_effect=[
            httpx.Response(503, json=load_fx("error_503_fault_injected")),
            httpx.Response(503, json=load_fx("error_503_fault_injected")),
            httpx.Response(200, json=load_fx("instance")),
        ]
    )

    async with client:
        res = await client.instance()
        assert res.status == 200
        assert route.call_count == 3
        assert len(sleeper.delays) == 2  # slept twice between retries
        assert breaker.state == CircuitBreaker.CLOSED  # recovery reset failures to 0


@respx.mock
@pytest.mark.asyncio
async def test_retry_after_header_respected(sleeper: MockSleeper, breaker: CircuitBreaker):
    client = SimClient(base_url="http://testsim:8000", breaker=breaker, sleep_fn=sleeper.sleep, max_retries=1)

    respx.get("http://testsim:8000/v1/instance").mock(
        side_effect=[
            httpx.Response(503, headers={"retry-after": "1.5"}, json=load_fx("error_503_fault_injected")),
            httpx.Response(200, json=load_fx("instance")),
        ]
    )

    async with client:
        await client.instance()
        assert len(sleeper.delays) == 1
        assert sleeper.delays[0] == 1.5


@respx.mock
@pytest.mark.asyncio
async def test_network_timeout_and_connect_errors_retried(sleeper: MockSleeper, breaker: CircuitBreaker):
    client = SimClient(base_url="http://testsim:8000", breaker=breaker, sleep_fn=sleeper.sleep, max_retries=2)

    respx.get("http://testsim:8000/v1/depots").mock(
        side_effect=[
            httpx.ConnectTimeout("Connect timeout"),
            httpx.ReadTimeout("Read timeout"),
            httpx.Response(200, json=load_fx("depots")),
        ]
    )

    async with client:
        res = await client.depots()
        assert res.status == 200
        assert len(sleeper.delays) == 2


@respx.mock
@pytest.mark.asyncio
async def test_repeated_503_exhausts_retries_and_raises(sleeper: MockSleeper, breaker: CircuitBreaker):
    client = SimClient(base_url="http://testsim:8000", breaker=breaker, sleep_fn=sleeper.sleep, max_retries=2)

    route = respx.get("http://testsim:8000/v1/instance").respond(503, json=load_fx("error_503_fault_injected"))

    async with client:
        with pytest.raises(SimFaultInjected) as exc_info:
            await client.instance()
        assert "Simulated fault injected" in str(exc_info.value)
        assert route.call_count == 3  # initial + 2 retries


# ---------------------------------------------------------------- Error Mapping
@respx.mock
@pytest.mark.asyncio
async def test_error_mapping_404_not_found(sleeper: MockSleeper, breaker: CircuitBreaker):
    client = SimClient(base_url="http://testsim:8000", breaker=breaker, sleep_fn=sleeper.sleep)
    route = respx.get("http://testsim:8000/v1/routes").respond(404, json=load_fx("error_404_not_found"))

    async with client:
        with pytest.raises(SimDomainError) as exc_info:
            await client.routes()
        assert exc_info.value.code == "NOT_FOUND"
        assert exc_info.value.status == 404
        assert route.call_count == 1  # 404 is NOT retried
        assert breaker.state == CircuitBreaker.CLOSED


@respx.mock
@pytest.mark.asyncio
async def test_error_mapping_409_domain_errors(sleeper: MockSleeper, breaker: CircuitBreaker):
    client = SimClient(base_url="http://testsim:8000", breaker=breaker, sleep_fn=sleeper.sleep)

    respx.post("http://testsim:8000/v1/allocations").respond(
        409, json=load_fx("error_409_idempotency_mismatch")
    )

    async with client:
        with pytest.raises(SimDomainError) as exc_info:
            await client.create_allocation({"idempotency_key": "bad"})
        assert exc_info.value.code == "IDEMPOTENCY_KEY_MISMATCH"
        assert exc_info.value.status == 409
        assert breaker.state == CircuitBreaker.CLOSED  # Domain errors do NOT open breaker


@respx.mock
@pytest.mark.asyncio
async def test_error_mapping_422_validation(sleeper: MockSleeper, breaker: CircuitBreaker):
    client = SimClient(base_url="http://testsim:8000", breaker=breaker, sleep_fn=sleeper.sleep)
    route = respx.post("http://testsim:8000/v1/allocations").respond(422, json=load_fx("error_422_validation"))

    async with client:
        with pytest.raises(SimValidationError) as exc_info:
            await client.create_allocation({"quantity": -1})
        assert "quantity" in str(exc_info.value.details)
        assert route.call_count == 1  # 422 is NOT retried
        assert breaker.state == CircuitBreaker.CLOSED


@respx.mock
@pytest.mark.asyncio
async def test_error_mapping_stream_disconnect(sleeper: MockSleeper, breaker: CircuitBreaker):
    client = SimClient(base_url="http://testsim:8000", breaker=breaker, sleep_fn=sleeper.sleep, max_retries=0)
    respx.get("http://testsim:8000/v1/instance").respond(503, json={"detail": {"code": "FAULT_INJECTED"}})

    async with client:
        with pytest.raises(SimFaultInjected):
            await client.instance()


@respx.mock
@pytest.mark.asyncio
async def test_error_mapping_non_json_500(sleeper: MockSleeper, breaker: CircuitBreaker):
    client = SimClient(base_url="http://testsim:8000", breaker=breaker, sleep_fn=sleeper.sleep, max_retries=0)
    respx.get("http://testsim:8000/v1/instance").respond(502, text="<html>502 Bad Gateway</html>")

    async with client:
        with pytest.raises(SimUnavailable) as exc_info:
            await client.instance()
        assert "502 non-json" in str(exc_info.value)


@respx.mock
@pytest.mark.asyncio
async def test_malformed_success_json_raises_invalid_payload(sleeper: MockSleeper, breaker: CircuitBreaker):
    client = SimClient(base_url="http://testsim:8000", breaker=breaker, sleep_fn=sleeper.sleep, max_retries=0)
    respx.get("http://testsim:8000/v1/instance").respond(200, text="not json at all {")

    async with client:
        with pytest.raises(SimInvalidPayload) as exc_info:
            await client.instance()
        assert "/v1/instance" in str(exc_info.value)


# ---------------------------------------------------------------- Stale Headers
@respx.mock
@pytest.mark.asyncio
async def test_stale_response_detected(sleeper: MockSleeper, breaker: CircuitBreaker):
    client = SimClient(base_url="http://testsim:8000", breaker=breaker, sleep_fn=sleeper.sleep)

    respx.get("http://testsim:8000/v1/instance").respond(
        200, headers={"x-simulator-stale": "true"}, json=load_fx("instance")
    )
    respx.get("http://testsim:8000/v1/depots").respond(
        200, headers={"x-simulator-stale": "false"}, json=load_fx("depots")
    )

    async with client:
        r_inst = await client.instance()
        assert r_inst.stale is True

        r_dep = await client.depots()
        assert r_dep.stale is False


# ---------------------------------------------------------------- Circuit Breaker Integration
@respx.mock
@pytest.mark.asyncio
async def test_circuit_breaker_integration_5_failures_open_breaker(sleeper: MockSleeper):
    # Breaker threshold = 5
    cb = CircuitBreaker(name="sim_breaker_5", failure_threshold=5, reset_timeout=10.0)
    client = SimClient(base_url="http://testsim:8000", breaker=cb, sleep_fn=sleeper.sleep, max_retries=0)

    respx.get("http://testsim:8000/v1/instance").respond(503, json=load_fx("error_503_fault_injected"))

    async with client:
        # 5 consecutive 503 failures
        for _ in range(5):
            with pytest.raises(SimFaultInjected):
                await client.instance()

        assert cb.state == CircuitBreaker.OPEN
        assert cb.failures == 5

        # 6th call blocked by circuit breaker without network request
        with pytest.raises(CircuitOpenError):
            await client.instance()


# ---------------------------------------------------------------- Metrics Recording
@respx.mock
@pytest.mark.asyncio
async def test_metrics_recorded(sleeper: MockSleeper, breaker: CircuitBreaker):
    client = SimClient(base_url="http://testsim:8000", breaker=breaker, sleep_fn=sleeper.sleep)

    respx.get("http://testsim:8000/v1/health").respond(200, json={"status": "ok"})
    respx.get("http://testsim:8000/v1/routes").respond(404, json=load_fx("error_404_not_found"))

    async with client:
        await client.health()
        with pytest.raises(SimDomainError):
            await client.routes()

    # Check metrics
    assert FSIP_SIM_REQUEST_DURATION is not None
    assert FSIP_SIM_ERRORS_TOTAL is not None
    # Verify samples exist
    found_200 = False
    for metric in FSIP_SIM_REQUEST_DURATION.collect():
        for sample in metric.samples:
            if sample.labels.get("endpoint") == "/v1/health" and sample.labels.get("status") == "200":
                found_200 = True
    assert found_200 is True

    found_404 = False
    for metric in FSIP_SIM_ERRORS_TOTAL.collect():
        for sample in metric.samples:
            if sample.labels.get("endpoint") == "/v1/routes" and sample.labels.get("code") == "NOT_FOUND":
                found_404 = True
    assert found_404 is True


# ---------------------------------------------------------------- Allocation Idempotency Safety
@respx.mock
@pytest.mark.asyncio
async def test_allocation_idempotency_preserved_during_retries(sleeper: MockSleeper, breaker: CircuitBreaker):
    client = SimClient(base_url="http://testsim:8000", breaker=breaker, sleep_fn=sleeper.sleep, max_retries=2)

    # First attempt: 503, second attempt: 201
    route = respx.post("http://testsim:8000/v1/allocations").mock(
        side_effect=[
            httpx.Response(503, json=load_fx("error_503_fault_injected")),
            httpx.Response(201, json=load_fx("allocation_created_201")),
        ]
    )

    req = AllocationRequest(
        idempotency_key="idemp-key-repeat",
        source_depot_id="depot-gazipur",
        destination_station_id="station-mirpur",
        route_id="route-gazipur-mirpur",
        fuel_type="DIESEL",
        quantity=3000,
    )

    async with client:
        res = await client.create_allocation(req)
        assert res.status == 201
        assert route.call_count == 2
        # Verify both requests sent identical idempotency_key
        call1_body = json.loads(route.calls[0].request.content)
        call2_body = json.loads(route.calls[1].request.content)
        assert call1_body["idempotency_key"] == "idemp-key-repeat"
        assert call2_body["idempotency_key"] == "idemp-key-repeat"


# ---------------------------------------------------------------- Verification Audit Tests
@respx.mock
@pytest.mark.asyncio
async def test_admin_operations_do_not_retry_on_failure(sleeper: MockSleeper, breaker: CircuitBreaker):
    client = SimClient(base_url="http://testsim:8000", breaker=breaker, sleep_fn=sleeper.sleep, max_retries=3)
    route_step = respx.post("http://testsim:8000/admin/step").respond(503, json=load_fx("error_503_fault_injected"))
    route_audit = respx.get("http://testsim:8000/admin/audit").respond(503, json=load_fx("error_503_fault_injected"))

    async with client:
        with pytest.raises(SimFaultInjected):
            await client.admin_step()
        assert route_step.call_count == 1  # Never retried despite max_retries=3

        with pytest.raises(SimFaultInjected):
            await client.admin_audit()
        assert route_audit.call_count == 1  # Never retried


def test_http_client_configuration_defaults():
    client = SimClient(base_url="http://testsim:8000")
    assert client.timeout == 2.0
    assert client.http.timeout.read == 2.0
    # Check connection pool limits
    pool = getattr(client.http._transport, "_pool", None)
    if pool is not None:
        assert pool._max_connections == 20
        assert pool._max_keepalive_connections == 10


@respx.mock
@pytest.mark.asyncio
async def test_circuit_breaker_trips_during_retries_and_aborts_early(sleeper: MockSleeper):
    # Breaker threshold = 2, max_retries = 3
    cb = CircuitBreaker(name="sim_breaker_trips_early", failure_threshold=2, reset_timeout=10.0)
    client = SimClient(base_url="http://testsim:8000", breaker=cb, sleep_fn=sleeper.sleep, max_retries=3)

    route = respx.get("http://testsim:8000/v1/instance").respond(503, json=load_fx("error_503_fault_injected"))

    async with client:
        with pytest.raises(CircuitOpenError):
            await client.instance()

        # Attempt 0: 503 -> breaker failure 1
        # Attempt 1: 503 -> breaker failure 2 -> breaker transitions to OPEN
        # Attempt 2: breaker.allow() is False -> raises CircuitOpenError immediately!
        # Total HTTP calls must be 2, NOT 4 (1 initial + 3 retries)
        assert route.call_count == 2
        assert cb.state == CircuitBreaker.OPEN


@respx.mock
@pytest.mark.asyncio
async def test_cancel_allocation_does_not_retry_on_failure(sleeper: MockSleeper, breaker: CircuitBreaker):
    client = SimClient(base_url="http://testsim:8000", breaker=breaker, sleep_fn=sleeper.sleep, max_retries=3)
    route_cancel = respx.post("http://testsim:8000/v1/allocations/42/cancel").respond(
        503, json=load_fx("error_503_fault_injected")
    )

    async with client:
        with pytest.raises(SimFaultInjected):
            await client.cancel_allocation(42)
        assert route_cancel.call_count == 1  # retry=False prevents repeat cancellation attempts


@respx.mock
@pytest.mark.asyncio
async def test_snapshot_partial_failure_returns_per_resource_exceptions(sleeper: MockSleeper, breaker: CircuitBreaker):
    client = SimClient(base_url="http://testsim:8000", breaker=breaker, sleep_fn=sleeper.sleep, max_retries=0)

    respx.get("http://testsim:8000/v1/instance").respond(200, json=load_fx("instance"))
    respx.get("http://testsim:8000/v1/depots").respond(503, json=load_fx("error_503_fault_injected"))
    respx.get("http://testsim:8000/v1/stations").respond(200, json=load_fx("stations"))
    respx.get("http://testsim:8000/v1/routes").respond(200, json=load_fx("routes"))
    respx.get("http://testsim:8000/v1/events").respond(200, json=load_fx("events"))
    respx.get("http://testsim:8000/v1/supply-arrivals").respond(200, json=load_fx("supply_arrivals"))
    respx.get("http://testsim:8000/v1/allocations").respond(200, json=load_fx("allocations"))
    respx.get("http://testsim:8000/v1/metrics").respond(200, json=load_fx("metrics"))

    async with client:
        snap = await client.snapshot()
        assert len(snap) == 8
        assert isinstance(snap["instance"], SimResult)
        assert isinstance(snap["stations"], SimResult)
        # depots failed: returns SimFaultInjected without failing the whole snapshot
        assert isinstance(snap["depots"], SimFaultInjected)
        assert "Simulated fault injected" in str(snap["depots"])

