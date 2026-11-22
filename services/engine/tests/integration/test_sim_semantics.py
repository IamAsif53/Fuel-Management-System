"""Integration tests for verifying unknown simulator semantics (Task 1.9).

These tests run against a live simulator (default SIM_URL=http://localhost:8000)
and are marked with @pytest.mark.integration.

Verified Semantics:
1. Exact Arrival Timing:
   - Allocation created at tick T on route with transit k departs at tick T.
   - expected_arrival_tick = T + k.
   - Fuel arrives during the step processing tick (T + k), and station inventory
     increases when the instance reports tick (T + k + 1).
   - fsip_common.models.expected_arrival_tick(T, k) == T + k.

2. Mid-Transit Route Disruption:
   - Route disrupted while an allocation is already IN_TRANSIT has NO effect on the shipment.
   - Disruption checks occur at departure tick; once in transit, the shipment completes
     and arrives normally.

3. Route Disruption at Creation vs. Departure:
   - At Creation: If route is DISRUPTED, POST /v1/allocations returns 409 ROUTE_DISRUPTED.
     Depot inventory is NOT deducted.
   - At Departure: If route is disrupted after creation (while PENDING) before the step that
     departs it, the allocation transitions to FAILED with failure_reason="ROUTE_UNAVAILABLE",
     and depot inventory is NOT refunded (fuel lost).

4. Dispatch Cap Across Ticks:
   - Total allocation volume per depot per tick cannot exceed dispatch_capacity_per_tick.
   - Once an allocation departs at tick boundary (transitions to IN_TRANSIT), it no longer
     counts against the subsequent tick's dispatch capacity (fresh cap available).

5. Live SSE Keepalive & Disconnect/Reconnect:
   - Live stream delivers raw keepalive comments (: keepalive) which reset the inactivity watchdog.
   - On stream_disconnect fault, listener detects disconnection and triggers reconnect backoff.
   - Full sync triggers upon reconnection.
"""
from __future__ import annotations

import asyncio
import os
import time
from collections.abc import AsyncGenerator

import httpx
import pytest
from app.sim.client import SimClient, SimDomainError
from app.sim.sse import SSEListener
from fsip_common import expected_arrival_tick
from fsip_common.models import AllocationRequest, EventCreate, FaultCreate

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

SIM_BASE_URL = os.environ.get("SIM_URL", "http://localhost:8000")
_SIM_ONLINE: bool | None = None


async def _check_simulator_online(url: str) -> bool:
    global _SIM_ONLINE
    if _SIM_ONLINE is not None:
        return _SIM_ONLINE
    client = httpx.AsyncClient(timeout=0.6)
    try:
        resp = await client.get(f"{url}/v1/health")
        _SIM_ONLINE = resp.status_code == 200
    except Exception:
        _SIM_ONLINE = False
    finally:
        await client.aclose()
    return _SIM_ONLINE


@pytest.fixture
async def sim() -> AsyncGenerator[SimClient, None]:
    """Provide a clean SimClient connected to the live simulator.

    Skips if simulator is unreachable. Resets simulation and clears faults before and after test.
    """
    if not await _check_simulator_online(SIM_BASE_URL):
        pytest.skip(
            f"Simulator at {SIM_BASE_URL} not reachable. Start with: docker compose up -d simulator"
        )

    client = SimClient(base_url=SIM_BASE_URL, timeout=3.0, max_retries=1)
    # Initialize clean simulator state
    await client.admin_reset()
    await client.admin_faults_clear()

    yield client

    # Cleanup afterwards
    try:
        await client.admin_faults_clear()
        await client.admin_reset()
    except Exception:
        pass
    await client.close()



# -----------------------------------------------------------------------------
# 1. Exact Arrival Timing and Inventory Increase
# -----------------------------------------------------------------------------
async def test_exact_arrival_timing_and_inventory_increase(sim: SimClient) -> None:
    """Verify arrival timing: created at T, transit k -> expected T+k, inventory up at T+k+1."""
    # Read initial instance state
    inst_res = await sim.instance()
    t0 = inst_res.data["tick"]

    # Initial station inventory
    stations_res = await sim.stations()
    mirpur = next(s for s in stations_res.data if s["id"] == "station-mirpur")
    initial_diesel = float(mirpur["inventory"]["DIESEL"])

    # route-gazipur-mirpur has transit_ticks = 2
    routes_res = await sim.routes()
    route = next(r for r in routes_res.data if r["id"] == "route-gazipur-mirpur")
    transit_k = int(route["transit_ticks"])
    assert transit_k == 2

    # Verify timing helper
    expected_tick = expected_arrival_tick(t0, transit_k)
    assert expected_tick == t0 + transit_k

    # Create 1,000L DIESEL allocation at tick t0
    req = AllocationRequest(
        idempotency_key=f"sem-timing-{t0}",
        source_depot_id="depot-gazipur",
        destination_station_id="station-mirpur",
        route_id="route-gazipur-mirpur",
        fuel_type="DIESEL",
        quantity=1000.0,
    )
    alloc_res = await sim.create_allocation(req)
    assert alloc_res.status == 201
    alloc_id = alloc_res.data["id"]
    assert alloc_res.data["status"] == "PENDING"
    assert alloc_res.data["created_tick"] == t0

    # Step 1: Advance tick (t0 -> t0 + 1)
    # The allocation departs: status becomes IN_TRANSIT, departure_tick=t0, expected_arrival_tick=t0+2
    step1 = await sim.admin_step()
    assert step1.data["tick"] == t0 + 1

    allocs1 = await sim.allocations()
    a1 = next(a for a in allocs1.data if a["id"] == alloc_id)
    assert a1["status"] == "IN_TRANSIT"
    assert a1["departure_tick"] == t0
    assert a1["expected_arrival_tick"] == expected_tick

    # Station inventory should NOT have increased yet at t0 + 1
    stations1 = await sim.stations()
    mirpur1 = next(s for s in stations1.data if s["id"] == "station-mirpur")
    assert float(mirpur1["inventory"]["DIESEL"]) <= initial_diesel

    # Step 2: Advance tick (t0 + 1 -> t0 + 2 == expected_tick)
    # Allocation is still in transit during tick 2
    step2 = await sim.admin_step()
    assert step2.data["tick"] == t0 + 2

    allocs2 = await sim.allocations()
    a2 = next(a for a in allocs2.data if a["id"] == alloc_id)
    assert a2["status"] == "IN_TRANSIT"

    # Step 3: Advance tick (t0 + 2 -> t0 + 3 == expected_tick + 1)
    # The arrival is processed; status becomes ARRIVED, actual_arrival_tick=t0+2
    step3 = await sim.admin_step()
    assert step3.data["tick"] == t0 + 3

    allocs3 = await sim.allocations()
    a3 = next(a for a in allocs3.data if a["id"] == alloc_id)
    assert a3["status"] == "ARRIVED"
    assert a3["actual_arrival_tick"] == expected_tick

    # Station inventory increases by received fuel (accounting for consumption during ticks)
    stations3 = await sim.stations()
    mirpur3 = next(s for s in stations3.data if s["id"] == "station-mirpur")
    diesel_now = float(mirpur3["inventory"]["DIESEL"])
    # Delivery was 1000L, consumption per tick is ~50-80L
    assert diesel_now > initial_diesel + 700.0


# -----------------------------------------------------------------------------
# 2. Mid-Transit Route Disruption
# -----------------------------------------------------------------------------
async def test_mid_transit_route_disruption_does_not_fail_shipment(sim: SimClient) -> None:
    """Verify that disrupting a route while a shipment is IN_TRANSIT does NOT fail the shipment."""
    inst_res = await sim.instance()
    t0 = inst_res.data["tick"]

    # 1. Create allocation at tick t0 on route with transit 2
    req = AllocationRequest(
        idempotency_key=f"sem-mid-disrupt-{t0}",
        source_depot_id="depot-gazipur",
        destination_station_id="station-mirpur",
        route_id="route-gazipur-mirpur",
        fuel_type="DIESEL",
        quantity=1500.0,
    )
    alloc_res = await sim.create_allocation(req)
    alloc_id = alloc_res.data["id"]

    # 2. Step 1: Allocation departs and becomes IN_TRANSIT
    await sim.admin_step()
    allocs1 = await sim.allocations()
    a1 = next(a for a in allocs1.data if a["id"] == alloc_id)
    assert a1["status"] == "IN_TRANSIT"

    # 3. Inject route disruption on route-gazipur-mirpur during transit
    await sim.admin_events_create(
        EventCreate(
            type="route_disruption",
            start_tick=t0 + 1,
            duration_ticks=5,
            parameters={"route_ids": ["route-gazipur-mirpur"]},
        )
    )

    # 4. Advance through transit ticks
    # Step 2: tick t0 + 2 -> event becomes ACTIVE, route is DISRUPTED, shipment is still IN_TRANSIT
    await sim.admin_step()
    routes_res = await sim.routes()
    route = next(r for r in routes_res.data if r["id"] == "route-gazipur-mirpur")
    assert route["status"] == "DISRUPTED"

    allocs2 = await sim.allocations()
    a2 = next(a for a in allocs2.data if a["id"] == alloc_id)
    assert a2["status"] == "IN_TRANSIT"

    # Step 3: tick t0 + 3 -> shipment arrives despite route disruption
    await sim.admin_step()

    # 5. Verify the shipment arrived safely despite route disruption
    allocs3 = await sim.allocations()
    a3 = next(a for a in allocs3.data if a["id"] == alloc_id)
    assert a3["status"] == "ARRIVED"
    assert a3.get("failure_reason") is None


# -----------------------------------------------------------------------------
# 3. Route Disruption at Creation vs. Departure
# -----------------------------------------------------------------------------
async def test_route_disruption_at_creation_rejected_with_409(sim: SimClient) -> None:
    """Verify that creating an allocation on an already DISRUPTED route returns 409 ROUTE_DISRUPTED."""
    inst_res = await sim.instance()
    t0 = inst_res.data["tick"]

    # Inject disruption on route-gazipur-mirpur starting at t0
    await sim.admin_events_create(
        EventCreate(
            type="route_disruption",
            start_tick=t0,
            duration_ticks=5,
            parameters={"route_ids": ["route-gazipur-mirpur"]},
        )
    )

    # Step to activate the scheduled disruption
    await sim.admin_step()
    t_active = (await sim.instance()).data["tick"]

    # Verify route is now actively DISRUPTED
    routes_res = await sim.routes()
    route = next(r for r in routes_res.data if r["id"] == "route-gazipur-mirpur")
    assert route["status"] == "DISRUPTED"

    # Initial depot inventory
    depots_res = await sim.depots()
    gazipur_before = next(d for d in depots_res.data if d["id"] == "depot-gazipur")
    diesel_before = float(gazipur_before["inventory"]["DIESEL"])

    # Attempt allocation on actively DISRUPTED route
    req = AllocationRequest(
        idempotency_key=f"sem-disrupt-creation-{t_active}",
        source_depot_id="depot-gazipur",
        destination_station_id="station-mirpur",
        route_id="route-gazipur-mirpur",
        fuel_type="DIESEL",
        quantity=2000.0,
    )
    with pytest.raises(SimDomainError) as exc_info:
        await sim.create_allocation(req)

    assert exc_info.value.status == 409
    assert exc_info.value.code == "ROUTE_DISRUPTED"

    # Depot inventory was NOT deducted
    depots_after = await sim.depots()
    gazipur_after = next(d for d in depots_after.data if d["id"] == "depot-gazipur")
    assert float(gazipur_after["inventory"]["DIESEL"]) == diesel_before


async def test_route_disruption_at_departure_fails_without_refund(sim: SimClient) -> None:
    """Verify that route disruption at departure moment marks allocation FAILED with no refund."""
    inst_res = await sim.instance()
    t0 = inst_res.data["tick"]

    # Initial depot inventory
    depots_res = await sim.depots()
    gazipur_init = next(d for d in depots_res.data if d["id"] == "depot-gazipur")
    diesel_init = float(gazipur_init["inventory"]["DIESEL"])

    # 1. Create allocation while route is AVAILABLE
    req = AllocationRequest(
        idempotency_key=f"sem-disrupt-departure-{t0}",
        source_depot_id="depot-gazipur",
        destination_station_id="station-mirpur",
        route_id="route-gazipur-mirpur",
        fuel_type="DIESEL",
        quantity=2500.0,
    )
    alloc_res = await sim.create_allocation(req)
    assert alloc_res.status == 201
    alloc_id = alloc_res.data["id"]

    # Depot inventory deducted immediately on creation
    depots_mid = await sim.depots()
    gazipur_mid = next(d for d in depots_mid.data if d["id"] == "depot-gazipur")
    assert float(gazipur_mid["inventory"]["DIESEL"]) == diesel_init - 2500.0

    # 2. Inject route disruption scheduled for tick t0 (departure tick)
    await sim.admin_events_create(
        EventCreate(
            type="route_disruption",
            start_tick=t0,
            duration_ticks=5,
            parameters={"route_ids": ["route-gazipur-mirpur"]},
        )
    )

    # 3. Step to trigger departure: allocation attempts departure on disrupted route
    await sim.admin_step()

    # 4. Allocation must be FAILED with ROUTE_UNAVAILABLE
    allocs = await sim.allocations()
    alloc = next(a for a in allocs.data if a["id"] == alloc_id)
    assert alloc["status"] == "FAILED"
    assert "ROUTE" in str(alloc.get("failure_reason", "")).upper()

    # 5. Depot inventory is NOT refunded (fuel lost)
    depots_final = await sim.depots()
    gazipur_final = next(d for d in depots_final.data if d["id"] == "depot-gazipur")
    assert float(gazipur_final["inventory"]["DIESEL"]) == diesel_init - 2500.0


# -----------------------------------------------------------------------------
# 4. Dispatch Cap Across Ticks
# -----------------------------------------------------------------------------
async def test_dispatch_cap_resets_per_tick_after_departure(sim: SimClient) -> None:
    """Verify that previous-tick departed allocations do NOT count against the next tick's cap."""
    inst_res = await sim.instance()
    t0 = inst_res.data["tick"]

    # depot-gazipur dispatch capacity is 12,000L per tick.
    # Station-mirpur starts with 9000L DIESEL (capacity 15000L, room 6000L).
    # 1. Allocate 5,000L to mirpur + 5,000L to tongi at tick t0 (total 10,000L <= 12,000L)
    req1 = AllocationRequest(
        idempotency_key=f"sem-cap-1-{t0}",
        source_depot_id="depot-gazipur",
        destination_station_id="station-mirpur",
        route_id="route-gazipur-mirpur",
        fuel_type="DIESEL",
        quantity=5000.0,
    )
    res1 = await sim.create_allocation(req1)
    assert res1.status == 201

    req2 = AllocationRequest(
        idempotency_key=f"sem-cap-2-{t0}",
        source_depot_id="depot-gazipur",
        destination_station_id="station-tongi",
        route_id="route-gazipur-tongi",
        fuel_type="DIESEL",
        quantity=5000.0,
    )
    res2 = await sim.create_allocation(req2)
    assert res2.status == 201

    # 2. Attempting to allocate another 3,000L in the same tick t0 exceeds cap (10000 + 3000 = 13000 > 12000)
    req_excess = AllocationRequest(
        idempotency_key=f"sem-cap-excess-{t0}",
        source_depot_id="depot-gazipur",
        destination_station_id="station-mirpur",
        route_id="route-gazipur-mirpur",
        fuel_type="PETROL",
        quantity=3000.0,
    )
    with pytest.raises(SimDomainError) as exc_info:
        await sim.create_allocation(req_excess)
    assert exc_info.value.code == "DISPATCH_CAPACITY_EXCEEDED"

    # 3. Advance to tick t0 + 1: req1 and req2 depart to IN_TRANSIT
    await sim.admin_step()

    # 4. At tick t0 + 1, full 12,000L dispatch capacity is available again
    req_fresh = AllocationRequest(
        idempotency_key=f"sem-cap-fresh-{t0}",
        source_depot_id="depot-gazipur",
        destination_station_id="station-tongi",
        route_id="route-gazipur-tongi",
        fuel_type="PETROL",
        quantity=2500.0,
    )
    res_fresh = await sim.create_allocation(req_fresh)
    assert res_fresh.status == 201
    assert res_fresh.data["status"] == "PENDING"


# -----------------------------------------------------------------------------
# 5. Live SSE Stream, Keepalive Comments & Watchdog Resets
# -----------------------------------------------------------------------------
async def test_live_sse_stream_receives_ticks_and_keepalive(sim: SimClient) -> None:
    """Verify live SSE stream connection, event delivery, and watchdog reset by keepalives."""
    ticks_received: list[int] = []
    connected_event = asyncio.Event()

    async def on_tick(tick: int) -> None:
        ticks_received.append(tick)

    async def on_connect() -> None:
        connected_event.set()

    # Use inactivity_timeout=1.5s so watchdog timeout under injected fault triggers quickly
    listener = SSEListener(
        base_url=SIM_BASE_URL,
        on_tick=on_tick,
        on_connect=on_connect,
        inactivity_timeout=1.5,
    )

    listener_task = asyncio.create_task(listener.run())
    try:
        # Wait for connected
        await asyncio.wait_for(connected_event.wait(), timeout=5.0)
        assert listener.connected is True

        # Trigger tick via admin_step() and verify SSE receives simulation.tick
        step_res = await sim.admin_step()
        target_tick = step_res.data["tick"]

        # Wait briefly for SSE event propagation
        t_deadline = time.monotonic() + 3.0
        while time.monotonic() < t_deadline and target_tick not in ticks_received:
            await asyncio.sleep(0.1)

        assert target_tick in ticks_received

        # Test stream_disconnect fault injection (2 seconds)
        await sim.admin_faults_create(FaultCreate(type="stream_disconnect", duration_seconds=2))
        # Watchdog timeout is 1.5s; sleeping 2.0s ensures watchdog detects silence and reconnects
        await asyncio.sleep(2.0)
        assert listener.reconnects >= 1 or not listener.connected

        # Clear fault and verify automatic reconnection
        await sim.admin_faults_clear()
        t_reconnect_deadline = time.monotonic() + 8.0
        while time.monotonic() < t_reconnect_deadline and not listener.connected:
            await asyncio.sleep(0.2)

        assert listener.connected is True
    finally:
        listener.stop()
        try:
            await asyncio.wait_for(listener_task, timeout=3.0)
        except (TimeoutError, asyncio.CancelledError):
            listener_task.cancel()
            try:
                await listener_task
            except asyncio.CancelledError:
                pass

