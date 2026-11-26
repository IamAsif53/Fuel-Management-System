# DEF-004b — SSE State Synchronization After Simulator Reset (`useLiveState.ts`)

## Status: FIXED — LIVE VERIFICATION PASSED

## Root Cause
`LiveStateManager.fetchLatestState()` (`frontend/src/hooks/useLiveState.ts:249`) compared only `newState.tick >= this.latestTick` without comparing `newState.sim_run_id`. When a simulator reset started a new run (`sim_run_id` incremented, `tick` reset to `0`), the monotonic check rejected all state updates until `tick >= latestTick`, freezing open browser tabs on the old pre-reset tick. `sseEvents` also omitted `"reset.detected"`.

## Changes Implemented
- `frontend/src/hooks/useLiveState.ts`:
  - Tracked `latestSimRunId` alongside `latestTick` (and cleared it in `reset()`).
  - Updated monotonic state acceptance to compare `(sim_run_id, tick)` tuples: any increment `incomingRun > latestSimRunId` accepts lower reset ticks cleanly, while stale responses from the same run (`incomingTick < latestTick`) or older runs (`incomingRun < latestSimRunId`) are still rejected.
  - Subscribed `EventSource` to `"reset.detected"` (resetting `currentEtag` and triggering immediate `fetchLatestState()`).
- `frontend/src/test/useLiveState.test.tsx`: Added test #9 verifying `reset.detected` SSE update accepts `sim_run_id` increment (`10 -> 11`) with `tick=0` and still drops stale packets from old runs.

## Verification
- Frontend Vitest (`useLiveState.test.tsx`): **9 passed, 0 failed**.
- `tsc -b --noEmit` & production container build (`fsip-frontend`) succeeded.
- Live Chrome Playwright open-tab test:
  - Stepped simulator to `sim_run_id=2101, tick=4`; verified open `/overview` tab rendered `t=4` with `LIVE` SSE indicator.
  - Triggered `POST /api/control/sim/reset` -> `sim_run_id=2104, tick=0` **without reloading the browser tab**.
  - Open tab updated automatically via SSE to `t=0` within seconds (`LIVE_DEF004B_VERIFICATION_PASSED`).
  - Screenshots: `def004b_before_reset.png`, `def004b_after_reset_no_reload.png`.
