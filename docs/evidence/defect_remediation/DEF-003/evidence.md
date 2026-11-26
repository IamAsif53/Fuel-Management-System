# DEF-003 — Prevent Autopilot Activation When Data Is Stale While Paused

## Status: FIXED — LIVE VERIFICATION PASSED

## Root Cause
1. `SyncLoop.run()` (`services/engine/app/state/sync.py:726-728`) skipped all sync/freshness checks whenever `cur_tick == self.last_tick`, so injecting `stale_data` chaos while the simulator was `PAUSED` never updated `store.stale` or `store.engine_mode`.
2. `POST /engine/control/mode` (`services/engine/app/main.py:529-552`) only read cached in-memory `store.stale` / `store.degraded` flags and never probed live `/v1/instance` headers when enabling `autopilot`.

## Changes Implemented
- `services/engine/app/main.py`: Added an immediate live simulator freshness probe (`await sim_client.instance()`) inside `POST /engine/control/mode` when `mode == "autopilot"`. If `res.stale` is true or simulator polling fails, updates `store.stale` / `degraded_reasons`, evaluates `mode_manager`, publishes updated state, and rejects with HTTP `409 STALE_DATA`.
- `services/engine/app/state/sync.py`: Extracted `_handle_paused_poll` in `SyncLoop.run()` so idle polls while paused inspect `/v1/instance` `res.stale`, set `store.stale = True` and `engine_mode = "DEGRADED"`, and emit `state.updated` via Redis when fault status changes.

## Regression Tests Passing
- Engine pytest (`31 passed` across `test_sync.py`, `test_engine_endpoints.py`, `test_phase10_resilience.py`):
  - `test_control_mode_autopilot_live_probe_rejects_paused_stale_data`
  - `test_paused_poll_detects_stale_and_recovers_to_normal`

## Live Verification (Rebuilt `engine`)
- Simulator `PAUSED` at `tick=0`, `engine_mode=NORMAL`.
- Injected `POST /api/control/chaos/stale_data {"duration_seconds": 20}` (`200 OK`).
- Called `POST /api/control/mode {"mode": "autopilot"}` at `0s` and at `1.5s` while `PAUSED`:
  - Both returned **HTTP `409 STALE_DATA`**: `"Cannot enable Autopilot when data is stale or system is degraded: data is stale; system is degraded (stale_data); engine mode is DEGRADED (must be NORMAL)"`.
- Confirmed `GET /api/state` while still paused showed `tick=0, engine_mode="DEGRADED", stale=true, degraded_reasons=["stale_data"]`.
