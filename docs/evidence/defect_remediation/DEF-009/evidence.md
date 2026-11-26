# DEF-009 Remediation Evidence

## Summary
- **Defect:** `DEF-009` (`P1`) — Overview `OPEN ALERTS` KPI (`0`) & Header Bell badge contradicted `/alerts` ledger & PostgreSQL (`OPEN` alerts from `sim_run_id=NULL` / prior runs).
- **Root Causes:**
  1. `WorldState.to_public_json()` in `services/engine/app/state/store.py` hardcoded `"alerts_open_count": {"INFO": 0, "WARNING": 0, "CRITICAL": 0}` instead of publishing live open alert counts.
  2. `SyncLoop` detector/reconciliation `AlertSpec` objects missed `sim_run_id` and `on_reset` did not reset `AlertManager` or resolve stale prior-run alerts violating Postgres partial unique index `alerts_open_fp`.
  3. `upsert_alert()` inserted alerts for a new `sim_run_id` at `tick=0` before async `WriteBuffer` flushed `sim_runs(id)`, hitting `ForeignKeyViolationError` (`alerts_sim_run_id_fkey`) and leaving alerts in Redis without DB rows.
  4. `GET /api/alerts` did not filter by active `sim_run_id` by default, and `state_service.get_current_state()` did not reconcile `alerts_open_count` with PostgreSQL.
- **Changes Made:**
  1. [`services/engine/app/alerts/manager.py`](file:///f:/NewTask/fuel-platform/services/engine/app/alerts/manager.py): Added `reset()`, `resolve_stale_runs()`, `get_open_counts()`, `get_open_alerts_summary()`, and cross-run isolation in `raise_()`.
  2. [`services/engine/app/db/repo.py`](file:///f:/NewTask/fuel-platform/services/engine/app/db/repo.py): Updated `upsert_alert()` to resolve stale prior-run open rows cleanly, ensure `sim_runs(id)` exists (`ON CONFLICT DO NOTHING`), and added `resolve_stale_run_alerts()`.
  3. [`services/engine/app/state/store.py`](file:///f:/NewTask/fuel-platform/services/engine/app/state/store.py) & [`sync.py`](file:///f:/NewTask/fuel-platform/services/engine/app/state/sync.py): Populated live `alerts_open_count` / `alerts_open`, cleared on reset, stamped `sim_run_id` on all alert specs, and resolved prior-run DB alerts on startup/reset.
  4. [`services/api/app/db.py`](file:///f:/NewTask/fuel-platform/services/api/app/db.py), [`state_service.py`](file:///f:/NewTask/fuel-platform/services/api/app/services/state_service.py), [`risk_alerts.py`](file:///f:/NewTask/fuel-platform/services/api/app/routers/risk_alerts.py): Scoped `GET /api/alerts` to active `sim_run_id` by default (`all_runs=true` optional), reconciled `alerts_open_count` in `get_current_state()`, and published `alert.acknowledged` SSE event.
  5. [`frontend/src/api/client.ts`](file:///f:/NewTask/fuel-platform/frontend/src/api/client.ts), [`useLiveState.ts`](file:///f:/NewTask/fuel-platform/frontend/src/hooks/useLiveState.ts), [`Alerts.tsx`](file:///f:/NewTask/fuel-platform/frontend/src/pages/Alerts.tsx): Added Active/All Run scope filter, SSE `alert.*` listeners, and immediate `refetch()` after acknowledge.

## Automated & Live Verification
- **Tests:** `41` Engine (`test_alerts.py`, `test_store.py`, `test_sync.py`) + `25` API (`test_risk_api.py`, `test_decisions_api.py`) + `21` Vitest (`risk_alerts.test.tsx`, `useLiveState.test.tsx`, `overview.test.tsx`) + `tsc -b && vite build`: **ALL PASSED**.
- **Live Docker & Chrome Playwright (`verify_def009.mjs`):**
  - Reset Run `#2123`: `state.alerts_open_count = {INFO:0, WARNING:0, CRITICAL:0}`, `/api/alerts?status=OPEN = 0`.
  - Stress tick 2: `state = 6` (`WARNING: 6`), `/api/alerts = 6`, Overview `kpi-open-alerts = 6`, Header badge `6`, `/alerts` UI rows `6`.
  - Acknowledge 1 alert: `state = 5`, `/api/alerts = 5`, Overview `kpi-open-alerts = 5`.
  - Final reset Run `#2125`: `state = 0`, `/api/alerts = 0`.
  - Screenshots saved: `overview_open_alerts_synced.png`, `alerts_page_synced.png`.
