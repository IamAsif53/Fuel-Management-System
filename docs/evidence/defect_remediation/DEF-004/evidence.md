# DEF-004 — Paused DEGRADED Lock & Redis `fsip:health` Freeze Remediation

## Status: FIXED — LIVE VERIFICATION PASSED

## Root Cause
1. `SyncLoop.run()` only ran `request_cycle()` / `full_sync()` on tick changes. When the engine started or recovered to `clean_cycles=1` (`EngineModeManager`) while the simulator was `PAUSED`, the required 2nd recovery cycle never executed, leaving `engine_mode` locked in `DEGRADED`.
2. Neither `store.last_good_at["instance"]` nor Redis `fsip:health["engine"]` / `fsip:state` refreshed while paused, causing `/health/ready` `last_sync_age_s` to climb unbounded (1000+ s) and UI age banners to freeze indefinitely.

## Changes Implemented
- `services/engine/app/state/publisher.py`: Extracted `_build_health_json` helper and added `publish_health(store, include_state=True)` to refresh Redis `fsip:health["engine"]`, `fsip:state`, and `fsip:state:tick` without flooding `fsip:domain-events` SSE stream on steady paused polls.
- `services/engine/app/state/sync.py`: Updated `_handle_paused_poll` so that:
  - When simulator is paused and healthy, `last_good_at["instance"]` and `publish_health()` refresh every poll interval (`~1s`).
  - When `store.engine_mode != "NORMAL"` or `store.degraded` clears on paused polls, `request_cycle(cur_tick)` runs recovery verification cycles (`clean_cycles` advances `1 -> 2 -> NORMAL`) automatically without requiring tick advancement.
  - Poll failures increment `cycle_failed` cleanly on `EngineModeManager`.

## Live Verification (Rebuilt `engine`)
- Observed live engine startup recovery log while paused at `tick=0`:
  - `08:36:11Z` startup poll transient disconnect -> `NORMAL -> DEGRADED` (`tick=0`)
  - `08:36:12Z` paused recovery cycle 1 -> `sim.sync.ok`
  - `08:36:14Z` paused recovery cycle 2 -> `DEGRADED -> NORMAL` (`reason="All degradation conditions cleared for 2 cycles", tick=0`).
- Verified continuous Redis heartbeat at paused `tick=0`: `fsip:health updated_at` advanced `08:36:57Z -> 08:37:00Z` with `last_sync_age_s=0.0..0.89s` and `age_seconds=0.0s`.
- Verified live `stale_data` chaos clear while paused at `tick=0`: engine automatically recovered `DEGRADED -> NORMAL` (`stale=false`, `age=0.09s`, `/health/ready last_sync_age_s=0.13s`) with ZERO ticks stepped.
