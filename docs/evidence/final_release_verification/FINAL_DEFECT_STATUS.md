# FSIP — Final Defect Status

**Statuses used:** VERIFIED FIXED | FIXED AND REGRESSION TESTED | STILL OPEN | PARTIALLY FIXED | BLOCKED | NOT REPRODUCIBLE — REVIEW REQUIRED | INTENTIONALLY SUPERSEDED (brief-aligned open mode)

| Defect ID | Previous status | Current status | Fix/verification evidence |
|---|---|---|---|
| DEF-001 | FIXED — LIVE (login + four-eyes) | **INTENTIONALLY SUPERSEDED (brief-aligned open mode)** | Sign-In UI removed per brief §19/§26 + user order; Operator chip; single-click large approve; optional JWT for tests only. `baseline/DEFECT_REVERIFICATION.md`, `browser/BROWSER_WORKFLOWS.md` |
| DEF-002 | FIXED — LIVE | **FIXED AND REGRESSION TESTED** | Live approve with `selected_line_ids`/`modified_quantities`; engine/API decision tests green |
| DEF-003 | FIXED — LIVE | **VERIFIED FIXED** | Autopilot → `409 STALE_DATA` when stale/paused+chaos; engine live-probe test passes |
| DEF-003b | FIXED — LIVE | **VERIFIED FIXED** | Frontend bundle scan zero `change-me*`; static token not privileged |
| DEF-004 | FIXED — LIVE | **VERIFIED FIXED** | Engine recovers NORMAL; `/health/ready` fresh |
| DEF-004b | FIXED — LIVE | **VERIFIED FIXED** | `useLiveState` reset handling + Vitest; SSE not re-fired this session (code+prior live) |
| DEF-005 | FIXED — LIVE | **VERIFIED FIXED** | `/risk?region_id=dhaka|chattogram` → 6 rows each canonical region |
| DEF-006 | FIXED — LIVE | **VERIFIED FIXED** | Risk rows expose stockout probs/drivers; live HIGH samples observed |
| DEF-007 | FIXED — LIVE | **VERIFIED FIXED** | Invalid What-If → `422 VALIDATION_ERROR` |
| DEF-007b | FIXED — LIVE | **VERIFIED FIXED** | Crisis presets apply correct params / regional isolation |
| DEF-008 | FIXED — LIVE | **VERIFIED FIXED** | Decisions return liters/lines; `sim_run_id` scoped |
| DEF-009 | FIXED — LIVE | **VERIFIED FIXED** | Overview open-alert count matches alerts ledger (0==0 probe; prior non-zero evidence retained) |
| DEF-010 | FIXED — LIVE | **FIXED AND REGRESSION TESTED** | Full suites green this pass (see FINAL_TEST_EXECUTION_LOG) |
| DEF-011 | FIXED — LIVE | **VERIFIED FIXED** | Assistant live ask + tests |
| DEF-012 | FIXED — LIVE | **VERIFIED FIXED** | Tailwind tokens present |
| DEF-012b | FIXED — LIVE | **VERIFIED FIXED** | Evil origin CORS 400 |
| DEF-013 | FIXED — LIVE | **FIXED AND REGRESSION TESTED** | Routes + `/what-if` & `/system-health` aliases added this pass |
| DEF-014 | FIXED — LIVE | **VERIFIED FIXED** | `/decisions/metrics` live |
| DEF-015 | FIXED — LIVE | **VERIFIED FIXED** | NetworkGraph layout; overview screenshot |
| DEF-016 | FIXED — LIVE | **VERIFIED FIXED** | Notifier Unconfigured truthful; Grafana links |
| DEF-017 | FIXED — LIVE | **VERIFIED FIXED** | Mobile 390px assistant not clipped |
| DEF-018 | FIXED — LIVE | **VERIFIED FIXED** | What-If UI + live browser run |
| DEF-019 | FIXED — LIVE | **VERIFIED FIXED** | Ledger path on approve; notifier honesty |
| DEF-020 | FIXED — LIVE | **VERIFIED FIXED** | Demand baseline ~91.4k; open-mode compute + validation |
| DEF-021 | FIXED — LIVE | **VERIFIED FIXED** | History scoped by `sim_run_id`; phase badges removed |
| DEF-022 | FIXED — LIVE | **VERIFIED FIXED** | Narrative station/line correctness |

## New issues found & fixed this pass

| Issue | Status |
|---|---|
| API tests still expecting 401 without bearer after Sign-In removal | Fixed (open-mode expectations) |
| Live engine expired parallel test-run decisions (`expire_stale_decisions` step 2) | Fixed — only expire ended `SimRun`s; regression in engine tests |
| Missing `/what-if` and `/system-health` aliases | Fixed in `App.tsx` |
| Ruff F401 unused import | Fixed |
| Playwright connection refused (Docker Desktop down mid-run) | Environment restored; **4/4 PASS** |
