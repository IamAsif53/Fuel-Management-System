# DEF-008 Remediation Evidence

## Summary
- **Defect:** `DEF-008` (`P1`) — Decisions Ledger rendered `0 L across 0 lines` for every decision, plus cold-load hydration raced before `state.sim_run_id` settled and fetched unfiltered cross-run history, plus multi-worker uvicorn `_DECISIONS_CACHE` served stale `PENDING_APPROVAL` status across workers after partial approval.
- **Root Causes:**
  1. `fetch_decisions()` in `services/api/app/db.py` queried only base `decisions` table columns and omitted `decision_lines`, `line_count`, `active_line_count`, `total_liters`, `decision_outcomes`, and `explanation`/`impact`/`objective`.
  2. `Decisions.tsx` fired `loadDecisions()` on initial mount while `state?.sim_run_id` was still `undefined`, causing `fetchDecisions({ sim_run_id: undefined })` to leak 480+ historical records into the Active Run view, lacked request sequence cancellation, and did not listen for `reset.detected`, `decision.executed`, or `decision.expired` SSE events.
  3. `services/api/app/routers/decisions.py` cached `GET /api/recommendations` and `GET /api/decisions` responses in per-process memory (`_DECISIONS_CACHE`), which remained stale for 4s in the other 3 `--workers 4` uvicorn processes after approval/rejection or Engine mutations.
- **Changes Made:**
  1. [`services/api/app/db.py`](file:///f:/NewTask/fuel-platform/services/api/app/db.py): Extended `fetch_decisions()` to select `objective, impact, explanation` and batch-query `decision_lines` and `decision_outcomes` via `ANY(:decision_ids)`. Added `lines`, `line_count`, `active_line_count`, `total_liters` (summing non-CANCELLED active lines when partially executed, or all lines when none are non-CANCELLED), and `outcome`.
  2. [`services/api/app/routers/decisions.py`](file:///f:/NewTask/fuel-platform/services/api/app/routers/decisions.py): Removed per-worker `_DECISIONS_CACHE` lookups in `get_recommendations()` and `list_decisions()` so all uvicorn workers consistently read authoritative PostgreSQL state.
  3. [`frontend/src/api/types.ts`](file:///f:/NewTask/fuel-platform/frontend/src/api/types.ts): Added optional `line_count`, `active_line_count`, and `total_liters` properties on `Decision`.
  4. [`frontend/src/pages/Decisions.tsx`](file:///f:/NewTask/fuel-platform/frontend/src/pages/Decisions.tsx): Deferred Active Run fetching until `state?.sim_run_id` hydrates, added monotonic `requestSeqRef` race protection, handled `decision.executed` / `decision.expired` / `reset.detected` SSE events, and displayed accurate non-cancelled totals (`1,500 L across 1 of 5 lines` for partial executions).

## Automated Regression Tests
- **Backend (`services/api`):**
  - Added `test_fetch_decisions_hydrates_lines_totals_and_outcomes` & `test_list_decisions_does_not_serve_stale_cached_status` in `services/api/tests/test_decisions_api.py`.
  - `.venv/Scripts/pytest.exe services/api/tests/test_decisions_api.py -v`: **17 passed** in `3.30s`.
- **Frontend (`frontend`):**
  - Added cold-load hydration deferral + partial-line literal rendering test in `frontend/src/test/decisions.test.tsx`.
  - `npx vitest run src/test/decisions.test.tsx`: **4 passed**.
  - `tsc -b && vite build` inside `fsip/frontend:dev` image build: **PASSED**.

## Live Docker & Browser Verification
1. Created multi-line decision `#1759` in live active Run `#2116` (`5` lines, `19,350 L` planned) and partially approved Line `#3106` at `1,500 L` (`supervisor` actor):
   - Immediate `GET /api/decisions?sim_run_id=2116&limit=10` returned `status="EXECUTED"`, `line_count=5`, `active_line_count=1`, `total_liters=1500`, preserving Lines `2..5` as `CANCELLED` in `lines`.
2. Cold-loaded `http://localhost:3000/decisions` in headless Chrome Playwright:
   - Network interception verified ONLY `GET /api/decisions?sim_run_id=2116&limit=50&offset=0` fired on cold load (zero unfiltered requests).
   - Zero `(Past)` badges rendered under `Active Run (#2116)`.
   - `#DEC-1759` rendered **`1,500 L across 1 of 5 lines`** (zero occurrences of `0 L across 0 lines`), expanded cleanly with all 5 lines and hydrated `ExplainPanel` data.
   - Switching `Scope` to `All Runs (Cross-run history)` loaded historical runs with non-zero hydrated volumes and lines.
   - Screenshots saved:
     - `decisions_active_expanded.png`
     - `decisions_all_runs_hydrated.png`
