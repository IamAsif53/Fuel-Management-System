# Simulator Semantics & Engine Assumptions

This document records verified simulator semantics, operational assumptions, and architectural design invariants for the **BUP Fuel Supply Resilience Platform (FSIP)**, verified against the BUP Fuel Supply Simulator API (v1.0.0).

---

## 1. Allocation Mechanics & Arrival Timing

### Verified Semantics (Task 1.9)
- **Creation & Departure Timing:**
  - An allocation submitted at instance tick $T$ with route transit ticks $k$:
    1. Returns HTTP 201 Created with `status = "PENDING"`, `created_tick = T`.
    2. Depot inventory is deducted immediately upon creation.
    3. During the step advancing the simulation from tick $T \to T + 1$:
       - The allocation departs: `status = "IN_TRANSIT"`.
       - Fields updated: `departure_tick = T`, `expected_arrival_tick = T + k`.
    4. Transit persists across ticks $T + 1, \dots, T + k$.
    5. During the step processing tick $T + k$ (advancing instance clock from $T + k \to T + k + 1$):
       - The allocation arrives: `status = "ARRIVED"`, `actual_arrival_tick = T + k`.
       - Station inventory increases by the received fuel volume.
       - The delivered fuel is visible in station inventory when the instance reports tick $T + k + 1$.
- **Timing Helper Function:**
  - Authoritative function: `expected_arrival_tick(created_tick: int, transit_ticks: int) -> int` in `fsip_common.timeutil`, re-exported via `fsip_common`.
  - Formula: $\text{expected\_arrival\_tick} = \text{created\_tick} + \text{transit\_ticks}$.
  - Strict validation: requires non-negative integers (`isinstance(int)`, `not bool`, `created_tick >= 0`, `transit_ticks >= 0`).

### Verification Evidence Status
- **Finding A (Arrival Timing):** Verified via manual live simulator observation during Phase 00 planning (`02_SIMULATOR_FACTS.md` §3), unit-tested, and automated live verification in `test_sim_semantics.py::test_exact_arrival_timing_and_inventory_increase` PASSED against the live simulator container (`asifmahmoud414/bup-fuel-supply-simulator:1.0.0`).
- **Finding B (Mid-Transit Route Disruption):** Verified via automated live test in `test_sim_semantics.py::test_mid_transit_route_disruption_does_not_fail_shipment` PASSED against live simulator container. Disruption mid-transit has zero negative impact; shipment arrived safely with `failure_reason = None`.
- **Finding C (Disruption at Creation vs. Departure):** Both branches verified against live simulator container. `test_route_disruption_at_creation_rejected_with_409` PASSED (HTTP 409 `ROUTE_DISRUPTED`, zero depot inventory loss). `test_route_disruption_at_departure_fails_without_refund` PASSED (transitions to `FAILED` with `failure_reason = "ROUTE_UNAVAILABLE"` and no fuel refund).
- **Finding D (Dispatch Cap Reset):** Verified via automated live test in `test_sim_semantics.py::test_dispatch_cap_resets_per_tick_after_departure` PASSED against live simulator container. Allocations departing at tick boundary transition to `IN_TRANSIT` and free the full depot dispatch cap for subsequent ticks.
- **Finding E (Live SSE Keepalive & Disconnect/Reconnect):** Verified via automated live test in `test_sim_semantics.py::test_live_sse_stream_receives_ticks_and_keepalive` PASSED against live simulator container. Raw keepalives reset watchdog; `stream_disconnect` fault causes watchdog to trigger reconnection backoff and successfully reconnect once the fault clears.


### Station Inventory Capacity & Spillage
- If $\text{current\_inventory} + \text{quantity} > \text{capacity}$ upon arrival, only the remaining room is received; excess fuel is spilled and lost.
- Audit metadata indicates exact liters received (e.g. `{"received": 173.15}`).
- The simulator's `DESTINATION_CAPACITY_EXCEEDED` creation check evaluates only *current* station inventory, ignoring in-flight shipments. Therefore, the FSIP engine and solver maintain their own projected inventory checks to prevent spillages.

---

## 2. Route Disruptions: Creation, Departure, and Mid-Transit

### Disruption at Creation
- When an allocation is submitted for a route that is currently `DISRUPTED`:
  - Simulator returns **HTTP 409 Conflict** with `{"detail": {"code": "ROUTE_DISRUPTED", "message": "..."}}`.
  - Depot inventory is **NOT** deducted.
  - Handled cleanly as a domain rejection by `SimClient` (`SimDomainError`).

### Disruption at Departure Moment
- If an allocation was created while the route was `AVAILABLE` (`status = "PENDING"`), but the route becomes `DISRUPTED` at the departure step ($T \to T + 1$):
  - Allocation status transitions to **`FAILED`** with `failure_reason = "ROUTE_UNAVAILABLE"`.
  - **Critical Invariant:** Depot inventory is **NOT refunded** (the dispatched fuel is permanently lost).
  - **Engine Mitigation:** Before submitting allocations, the engine inspects scheduled events (`/v1/events`) to verify that the route will not be disrupted at the upcoming departure tick.

### Mid-Transit Disruption
- If a route is disrupted while an allocation is already **`IN_TRANSIT`** (after departure tick has completed):
  - Route disruption has **NO effect** on in-flight shipments.
  - The shipment continues through transit and transitions to `ARRIVED` on tick $T + k$ with station inventory received.
  - *Finding:* Route availability checks are performed strictly at departure, not en-route.

---

## 3. Dispatch Capacity Across Ticks

### Per-Tick Dispatch Cap
- Each depot has a fixed `dispatch_capacity_per_tick` across all fuels (e.g., Gazipur: 12,000 L; Patiya: 11,000 L).
- Total volume of allocations departing in the same tick cannot exceed this threshold.
- Attempting to exceed the cap in a single tick returns **HTTP 409 Conflict** (`DISPATCH_CAPACITY_EXCEEDED`).

### Cap Reset on Subsequent Ticks
- Once an allocation departs at tick boundary ($T \to T + 1$) and transitions to `IN_TRANSIT`:
  - It no longer counts toward the subsequent tick's dispatch cap.
  - The full `dispatch_capacity_per_tick` is available again for fresh allocations at tick $T + 1$.
  - Previous-tick `PENDING` allocations that departed do not constrain subsequent tick dispatching.

---

## 4. Live SSE Streaming & Inactivity Watchdog

### Keepalive Comment Detection
- The simulator emits `: connected` upon stream establishment and comments `: keepalive` after 15 seconds of inactivity.
- **Architectural Safeguard:** Standard SSE parsers (e.g. `httpx_sse.EventSource.aiter_sse()`) consume and discard comment lines, which would starve standard event watchdogs.
- **FSIP Implementation (`SSEListener`):** Iterates over `es.response.aiter_lines()`. Every raw line starting with `:` (including `: keepalive` and `: connected`) resets the 30-second watchdog timer before decoding structured event data with `SSEDecoder`.
- This guarantees continuous stream liveness even when the simulation is paused or during periods without state events.

### Disconnect & Polling Supervision
- When stream connection drops (or during `stream_disconnect` fault):
  - `SSEListener` logs the disconnect, sets `fsip_sse_connected = 0`, increments `fsip_sse_reconnects_total`, and backs off (1s doubling up to 30s + jitter).
  - `SyncLoop` continues autonomous background polling (`/v1/instance`) every `ENGINE_POLL_INTERVAL_SEC` (default 5.0s), preventing engine desynchronization.
  - When connection is re-established, `on_connect` immediately invokes `SyncLoop.full_sync()`, refreshing state truth via authoritative REST endpoints.

---

## 5. Health & Readiness Contracts

### Health Endpoints (`services/engine/app/main.py`)
- `/health/live`:
  - Returns HTTP 200 `{"status": "ok", "service": "engine"}` as long as the engine process is up.
- `/health/ready`:
  - **Never synchronized:** If `store.instance is None` and `last_good_at` is empty, returns **HTTP 503 Service Unavailable** (`NOT_READY`, `No successful sync yet`).
  - **Healthy:** If fresh synchronization occurred within 30 seconds and no degraded flags are active, returns **HTTP 200 OK** with `{"status": "ready", "last_sync_age_s": ..., "engine_mode": ...}`.
  - **Degraded with cached state:** If the simulator is down or stale, but valid cached state exists in memory, returns **HTTP 200 OK** with `{"status": "degraded", ...}`. This prevents upstream load balancers/Kubernetes from dropping traffic when the engine can still serve cached insights.
