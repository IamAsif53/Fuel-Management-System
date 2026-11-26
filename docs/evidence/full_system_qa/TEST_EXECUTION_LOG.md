# FSIP — Independent QA Test Execution Log

All commands were executed directly during this QA audit against repository `f:\NewTask\fuel-platform` (Branch `main`, HEAD `3a2ae78` with uncommitted prior repair edits) and the live running 14-container Docker Compose stack. No production application files were modified.

---

## 1. Summary Table of Executed Test & Verification Suites

| # | Suite / Check | Working Dir | Exit Code | Total | Passed | Failed | Skipped / Deselected | Warnings | Key Result / Log Path |
|---|---|---|---:|---:|---:|---:|---:|---:|---|
| 1 | `ruff check libs services evaluation` | Repo root | **1** | 5 findings | — | **5 lint** | — | — | **FAIL** — 4× `E501` (`db.py:408,423`, `test_engine_decision.py:356,364`), 1× `I001` (`test_decisions_api.py:478`) |
| 2 | `pytest -ra` (`libs/fsip_common`) | `libs/fsip_common` | **0** | 107 | **107** | 0 | 0 | 0 | **PASS** ([pytest_libs_fsip_common.log](logs/pytest_libs_fsip_common.log)) |
| 3 | `pytest -ra` (`services/intelligence`) | `services/intelligence` | **0** | 8 | **8** | 0 | 0 | 1 | **PASS** (`starlette` deprecation warning) ([pytest_services_intelligence.log](logs/pytest_services_intelligence.log)) |
| 4 | `pytest -ra` (`services/api`) | `services/api` | **1** | 68 | **67** | **1** | 0 | 5 | **FAIL** — `test_entities.py::test_get_station_success` (`StopAsyncIteration`) ([pytest_services_api.log](logs/pytest_services_api.log)) |
| 5 | `pytest -ra` (`services/engine`, default `-m "not integration"`) | `services/engine` | **1** | 358 | **325** | **2** | **31 deselected** | 7 | **FAIL** — `test_solver_latency_p95_under_500ms` (625.2ms > 500ms), `test_sync_loop_invokes_publisher` (2 calls vs 1) ([pytest_services_engine.log](logs/pytest_services_engine.log)) |
| 6 | `pytest -ra` (`evaluation` from `evaluation/`) | `evaluation` | **2** | — | 0 | **2 collection errors** | — | — | **FAIL** — `ModuleNotFoundError: No module named 'evaluation'` when invoked per subdir |
| 7 | `PYTHONPATH=. pytest evaluation/tests -ra` | Repo root | **0** | 8 | **5** | 0 | **3 skipped** | 1 | **PASS (with skips)** — `test_harness.py` skipped (sim at `:8005` not running) ([pytest_evaluation.log](logs/pytest_evaluation.log)) |
| 8 | `npm test` (Vitest) | `frontend` | **0** | 60 (14 files) | **60** | 0 | 0 | React Router v7 stderr | **PASS (mocked HTTP)** |
| 9 | `npm run lint` (`tsc -b --noEmit`) | `frontend` | **0** | — | Pass | 0 | — | — | **PASS** |
| 10 | `npm run build` (`tsc -b && vite build`) | `frontend` | **0** | — | Built | 0 | — | 1 (`816.42 kB` chunk) | **PASS** |
| 11 | Existing `npx playwright test` (`e2e/fsip.spec.ts`) | `frontend` | **1** | 4 | 0 | **4** | 0 | — | **FAIL** — Missing `chromium_headless_shell-1148\chrome-win\headless_shell.exe` |
| 12 | QA Playwright Suite 1 (`ui_routes_responsive.spec.mjs`, Chrome) | `docs/evidence/.../playwright` | **0** | 3 | **3** | 0 | 0 | — | **PASS** — Collected UI, tunnel, CSS, responsive findings ([stage3_ui_responsive_audit.json](logs/stage3_ui_responsive_audit.json)) |
| 13 | QA Playwright Suite 2 (`e2e_workflows.spec.mjs`, Chrome) | `docs/evidence/.../playwright` | **0** | 7 | **7** | 0 | 0 | — | **PASS** — Executed WF-01..WF-09 & captured defects ([stage5_e2e_workflows.json](logs/stage5_e2e_workflows.json)) |
| 14 | QA Playwright Suite 3 (`wf_deep_checks.spec.mjs`, Chrome) | `docs/evidence/.../playwright` | **0** | 3 | **3** | 0 | 0 | — | **PASS** — Deep bug repros (Risk filter, Presets, Chaos 409, SSE Reset, Modify) ([stage5_deep_checks.json](logs/stage5_deep_checks.json)) |

> **Important QA Note on "Passed" QA Playwright Tests (#12–#14):** Our custom QA Playwright suites were telemetry-collecting harnesses that completed (`exit 0`) and recorded exact DOM, HTTP status, database, and screenshot evidence demonstrating **multiple critical functional and security defects** documented in [DEFECT_REGISTER.md](DEFECT_REGISTER.md).

---

## 2. Detailed Command Logs & Observations

### 2.1 Ruff Linter
```powershell
f:\NewTask\fuel-platform\.venv\Scripts\ruff.exe check f:\NewTask\fuel-platform\libs f:\NewTask\fuel-platform\services f:\NewTask\fuel-platform\evaluation
# Exit: 1
# Found 5 errors:
# - services/api/app/db.py:408:121: E501 Line too long (144 > 120)
# - services/api/app/db.py:423:121: E501 Line too long (144 > 120)
# - services/api/tests/test_decisions_api.py:478:1: I001 Import block is un-sorted or un-formatted
# - services/engine/tests/test_engine_decision.py:356:121: E501 Line too long (133 > 120)
# - services/engine/tests/test_engine_decision.py:364:121: E501 Line too long (123 > 120)
```

### 2.2 `services/api` Pytest Failure Detail
- **Command:** `.venv\Scripts\python.exe -m pytest -ra` (in `f:\NewTask\fuel-platform\services\api`)
- **Result:** `1 failed, 67 passed, 5 warnings in 72.25s (0:01:12)` (`EXIT=1`)
- **Failure:** `tests/test_entities.py::test_get_station_success`
  - `app/routers/entities.py:41` -> `fetch_station_demand_history` -> `session.execute` raised `StopAsyncIteration` because mock `side_effect=[mock_demand_result, mock_snap_result]` provides only 2 query results while the endpoint triggers an additional reconciliation query.
  - Note: Log shows warning `api.state.pending_recs_reconcile_skipped` and unawaited coroutine `RuntimeWarning` in `state_service.py:51,80`.

### 2.3 `services/engine` Pytest Failure Detail
- **Command:** `.venv\Scripts\python.exe -m pytest -ra` (in `f:\NewTask\fuel-platform\services\engine`)
- **Result:** `2 failed, 325 passed, 31 deselected, 7 warnings in 156.03s (0:02:36)` (`EXIT=1`)
- **Deselected:** 31 tests deselected by `pytest.ini` `addopts = -m "not integration"` (need running simulator, reset it).
- **Failures:**
  1. `tests/test_phase08_acceptance.py::test_solver_latency_p95_under_500ms`: `AssertionError: Solver p95 latency 625.2 ms exceeded 500 ms budget` (`625.1599ms >= 500.0ms`).
  2. `tests/test_publisher.py::test_sync_loop_invokes_publisher`: `AssertionError: Expected publish_state to have been awaited once. Awaited 2 times.` (Mock received 2 publishes in a single `full_sync()` cycle).

### 2.4 Tunnel & Endpoint Live Verification Probes
- **Prompt-cited quick tunnel (`https://cooperation-camcorder-obtain-resolution.trycloudflare.com/`):** Returned curl exit code `6` (`Could not resolve host`, HTTP `000`) — expired.
- **Active running `cloudflared` tunnel (`task-19309`, `https://utilities-toddler-proxy-menu.trycloudflare.com/`):** Returned HTTP `200` on `/` (`437 B`) and HTTP `200` on `/api/state` (`13,390 B`), and stayed reachable throughout audit (`TUNNEL_FINAL_HTTP=200`).
- **Existing repo E2E (`frontend/e2e/fsip.spec.ts`):** `4 failed, exit 1` because `C:\Users\USER\AppData\Local\ms-playwright\chromium_headless_shell-1148\chrome-win\headless_shell.exe` was missing. QA Playwright runs succeeded using system Google Chrome (`channel: "chrome"`).
- **Clean Baseline Restoration:** Simulator cleanly paused at `sim_run_id: 2040, tick: 2, sim_status: PAUSED, engine_mode: NORMAL, operating_mode: advisory, degraded: false`, chaos faults cleared, tunnel & local HTTP `200`.
