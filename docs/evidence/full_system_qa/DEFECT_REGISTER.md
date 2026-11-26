# FSIP — Prioritized Defect Register

**Audit Date:** 2026-11-26  
**Baseline:** `main @ 3a2ae78` (with uncommitted repair diffs) + live Docker Compose stack (`fsip-*`)  
**Total Defects Logged:** **24** (**4× P0 Blocker/Critical**, **9× P1 High**, **8× P2 Medium**, **3× P3 Low**)

---

## Severity Definitions
- **P0 — Critical / Acceptance Blocker:** Core workflow deadlocked, safety gate bypassed, severe credential exposure, data loss/corruption.
- **P1 — High:** Major functional inaccuracy, server 500 error, broken feature/filtering, broken CI/automated tests, security misconfiguration.
- **P2 — Medium:** Spec divergence, hardcoded KPIs/health telemetry, broken external/deep links, CSS theme failure, responsive UI clipping.
- **P3 — Low:** Minor demand baseline variance, non-blocking visual polish, phrasing defects.

---

## P0 — Critical / Acceptance Blockers

### DEF-001 — Four-Eyes Supervisory Approval (`>7,000 L`) Permanently Deadlocked & Auth Routes Missing
- **Severity / Priority:** `P0` / Immediate Blocker
- **Category:** Decision Lifecycle / Authorization & Auth
- **Component:** `services/api/app/routers/auth*` (missing), `services/api/app/routers/decisions.py`, `services/engine/app/decision/engine.py:880-923`
- **Root Cause:** `/api/auth/login` and `/api/auth/me` (`21_API_CONTRACTS.md` §2.7) do not exist (`404 Not Found`). Authentication uses a single static token (`change-me-operator`) resolving to actor `"operator"`, and `POST /api/decisions/{id}/approve` overrides the request body's `operator` field with that auth identity.
- **Reproduction Steps:**
  1. Generate any typical optimizer decision (`#1678`, `15,020 L > 7,000 L`).
  2. Call `POST /api/decisions/1678/approve` with `{"operator": "qa-op", "reviewed_driver": true}`. It enters `AWAITING_SECOND_APPROVAL` with `approved_by: "operator"`.
  3. Call `POST /api/decisions/1678/approve` as a second operator `{"operator": "supervisor", "reviewed_driver": true}`.
- **Expected:** Second distinct human operator completes four-eyes sign-off -> `APPROVED` -> `EXECUTED`; distinct user JWT accounts (`operator`, `supervisor`) authenticate via `/api/auth/login`.
- **Actual:** Both `operator="operator"` and `operator="supervisor"` fail with **HTTP `403 FOUR_EYES_SAME_ACTOR`**. In Postgres (`id=1678`), decision remains trapped in `AWAITING_SECOND_APPROVAL` until expiry.
- **Evidence:** Live check output in `task-19616`: `SECOND_APPROVE_DIFF_OP: (403, '...FOUR_EYES_SAME_ACTOR...')`, `POST /api/auth/login -> 404`, `GET /api/auth/me -> 404`.

---

### DEF-002 — `POST /api/decisions/{id}/approve` Silently Ignores `selected_line_ids`, `modified_quantities`, and `operator`
- **Severity / Priority:** `P0` / Immediate Blocker
- **Category:** Decision Execution / Operator Override Integrity
- **Component:** `services/api/app/routers/decisions.py` (`approve` handler) -> `services/engine/app/main.py:412`
- **Root Cause:** Although engine `/engine/decisions/{id}/approve` (`engine.py:766-768, 825-833`) supports `selected_line_ids`, `modified_quantities`, and `actor`, the public API route drops/ignores those fields when proxying to the engine. *(Note: During `DEEP-03`, an earlier call on `#1631` also returned HTTP `500 ENGINE_ERROR`.)*
- **Reproduction Steps:**
  1. Pick pending decision `#1678` (4 lines: IDs `2862` [6,400 L], `2863` [2,470 L], `2864` [4,880 L], `2865` [1,270 L]).
  2. `POST /api/decisions/1678/approve` with `{"operator":"qa-op","reviewed_driver":true,"selected_line_ids":[2862],"modified_quantities":{"2862":1200}}`.
- **Expected:** Lines `2863–2865` become `CANCELLED`, line `2862` updates to `1200.0 L` (`<= 7,000 L`), approved immediately by `qa-op`.
- **Actual:** Returned HTTP `200` with `approved_by: "operator"`, `status: "AWAITING_SECOND_APPROVAL"`, and ALL 4 lines untouched (`status: "PLANNED"`, original quantities totaling `15,020.0 L`).
- **Evidence:** Direct JSON response captured in `task-19611`; `stage5_deep_checks.json:232-241` (`approveResult: 500 ENGINE_ERROR` on `#1631`).

---

### DEF-003 — Autopilot Activation Succeeds (`200 OK`) While `stale_data` Chaos Fault Is Active on Paused Simulator
- **Severity / Priority:** `P0` / Safety Bypass
- **Category:** Safety Gate / Resilience
- **Component:** `services/engine/app/state/sync.py:727-728`, `services/engine/app/main.py:528-555`
- **Root Cause:** When the simulator is `PAUSED`, `SyncLoop.run()` only calls `request_cycle()` when `cur_tick != self.last_tick`. Injecting `stale_data` chaos while paused does not trigger a full sync or set `store.stale = True` before the operator calls `POST /api/control/mode` `{ "mode": "autopilot" }`.
- **Reproduction Steps:**
  1. With simulator paused and `engine_mode = NORMAL`, `POST /api/control/chaos/stale_data {"duration_sec": 20}`.
  2. Wait 1.5s, then `POST /api/control/mode {"mode": "autopilot"}`.
- **Expected:** HTTP `409 STALE_DATA` rejecting Autopilot activation per `20_FINAL_SPEC_LOCK.md` & `21_API_CONTRACTS.md` §2.6.
- **Actual:** Returned **HTTP `200 OK`**: `{"status":"ok","mode":"autopilot","operating_mode":"autopilot","auto_approved":0}`.
- **Evidence:** [stage5_e2e_workflows.json](logs/stage5_e2e_workflows.json#L335-L344) (`WF-06`, `autopilotWhileStale.status === 200`).

---

### DEF-003b — Hardcoded Static Operator Secret `change-me-operator` Embedded in Public Frontend JS Bundle
- **Severity / Priority:** `P0` / Security Exposure
- **Category:** Authentication / Secrets Hygiene
- **Component:** `frontend/src/api/client.ts:19-21`, production bundle `/usr/share/nginx/html/assets/index-CpEOeSFy.js`
- **Root Cause:** Client bundle defaults `operatorToken` to literal `"change-me-operator"` (matching `.env` default) and compiles it unguarded into public Nginx static assets.
- **Reproduction Steps:** `docker exec fsip-frontend sh -c "grep -rn 'change-me-operator' /usr/share/nginx/html/assets/"` (or view source/DevTools over public Cloudflare tunnel). Then call `POST <tunnel>/api/control/sim/pause` with `Authorization: Bearer change-me-operator`.
- **Expected:** No privileged bearer token or default credential shipped in browser JS; users sign in for scoped short-lived tokens.
- **Actual:** `"change-me-operator"` is present in plaintext inside `index-CpEOeSFy.js` and authorizes simulator control, chaos injection, scenario presets, and decision writes over the public tunnel.
- **Evidence:** Command output from `BUNDLE SECRET CHECK` + `STATIC_PAUSE_LOCAL: 200 OK`.

---

## P1 — High Severity Defects

### DEF-004 — Engine Locks in `DEGRADED` Across Paused Idle Periods & Redis Health Snapshot Freezes Stale Data
- **Severity / Priority:** `P1`
- **Category:** Reliability / Health Reporting
- **Component:** `services/engine/app/state/sync.py:727-728`, `services/engine/app/state/mode.py:137-143`, `services/engine/app/state/publisher.py`
- **Root Cause:** `SyncLoop` skips `full_sync()` whenever `cur_tick == last_tick`. If the engine entered `DEGRADED` on startup/transient issue and cleared to recovery cycle 1 (`clean_cycles=1`), it requires a **2nd** full sync (`clean_cycles >= 2`) to return to `NORMAL`. While paused, that 2nd sync never runs, AND `/health/ready` (`last_sync_age_s`) grows unbounded (`1075.03s`), while Redis hash `fsip:health` stays frozen with `"last_sync_age_s": 2.94`, `"updated_at": "2026-11-26T06:28:57Z"` for 20+ minutes.
- **Reproduction Steps:** Start stack with paused sim (baseline at Run 2006 Tick 12) and compare `GET http://localhost:8100/health/ready`, `GET /api/state`, `redis-cli HGETALL fsip:health`, and the UI header banner.
- **Expected:** Healthy paused engine resolves to `NORMAL`, keeps readiness fresh via heartbeats, and surfaces accurate staleness age.
- **Actual:** `engine_mode` stayed stuck at `"DEGRADED"` (even though `degraded: false`, `degraded_reasons: []`), banner forever claimed `"2.94s old"`, `/health/ready` reported `last_sync_age_s: 1075.03`, and only stepping 2 ticks manually (`WF-04`: tick 12 -> 13 -> 14) unlocked `NORMAL`.
- **Evidence:** Baseline probes + [stage5_e2e_workflows.json](logs/stage5_e2e_workflows.json#L110-L116) + [route_overview_1440.png](screenshots/route_overview_1440.png).

---

### DEF-004b — Live SSE Stream Ignores Simulator Reset (`useLiveState.ts:249`), Freezing Open Browsers on Pre-Reset Tick
- **Severity / Priority:** `P1`
- **Category:** Real-Time UI State Sync
- **Component:** `frontend/src/hooks/useLiveState.ts:249`
- **Root Cause:** Stale-tick guard checks `if (newState.tick !== undefined && newState.tick >= this.latestTick)` without checking `sim_run_id !== this.simRunId`.
- **Reproduction Steps:** Keep `/overview` open in tab A at `t=21` (Run 2006). Trigger `POST /api/control/sim/reset` (Run 2007, `t=0`). Wait 2.5s without reloading tab A.
- **Expected:** Tab A updates live via SSE `reset.detected`/`state` event to `t=0`.
- **Actual:** API reports `sim_run_id: 2007, tick: 0`, but open Tab A remains frozen displaying `Day 1 05:15 · t=21 DEGRADED` until manual page refresh.
- **Evidence:** [stage5_deep_checks.json](logs/stage5_deep_checks.json#L253-L262) (`sseIgnoredBackwardTickOnReset: true`) + [deep03_sse_stale_after_reset.png](screenshots/deep03_sse_stale_after_reset.png).

---

### DEF-005 — Risk Page Dhaka/Chattogram Region Filter Mismatches Backend IDs and Returns Zero Rows
- **Severity / Priority:** `P1`
- **Category:** Filtering / UI-API Contract Mismatch
- **Component:** `frontend/src/pages/Risk.tsx:147-148`, `services/api/app/routers/risk_alerts.py:51`
- **Root Cause:** `Risk.tsx` renders `<option value="dhaka">` and `<option value="chattogram">`, while stations have `region_id` `"region-dhaka"` / `"region-chattogram"` and `risk_alerts.py` checks exact equality `st_region != region_id`.
- **Reproduction Steps:** Navigate to `/risk` and select `"Dhaka"` in the Region dropdown.
- **Expected:** 6 rows (`station-mirpur`, `station-tongi` × 3 fuels).
- **Actual:** Sends `GET /api/risk?region_id=dhaka` -> returns `0` rows; UI renders `"No station fuel series match the selected risk criteria."` (`GET /api/risk?region_id=region-dhaka` returns 6 rows).
- **Evidence:** [stage5_deep_checks.json](logs/stage5_deep_checks.json#L16-L33) + [deep01_risk_dhaka_bug.png](screenshots/deep01_risk_dhaka_bug.png).

---

### DEF-006 — `/api/risk` Always Reports `0%` Stockout Probability & `Safe (>96h)`, Contradicting Recommendations
- **Severity / Priority:** `P1`
- **Category:** Domain Data Correctness
- **Component:** `services/api/app/routers/risk_alerts.py:67`, `services/engine/app/risk/stockout.py:90-91`
- **Root Cause:** `stockout.py` emits `stockout_prob_lead` and `stockout_prob_horizon`, whereas `risk_alerts.py` reads `r_info.get("stockout_prob") or 0.0` (which is absent).
- **Reproduction Steps:** Open `/recommendations` (`#DEC-1622` warns `Mirpur DIESEL projected_stockout (6.2 h)`, `stockout_prob_before: 0.72`) and open `/risk` at the exact same tick (`t=12`).
- **Expected:** `/risk` displays matching elevated stockout risk tier, hours-to-stockout, probability (~72%), and causal drivers.
- **Actual:** All 12 station-fuel rows display `LOW`, `Safe (>96h)`, `Stockout Prob: 0%`, and `Causal Drivers: —`.
- **Evidence:** [route_risk_1440.png](screenshots/route_risk_1440.png), [route_recommendations_1440.png](screenshots/route_recommendations_1440.png), [stage5_deep_checks.json](logs/stage5_deep_checks.json#L5-L15).

---

### DEF-007 — `POST /api/whatif` Returns HTTP `500 Internal Server Error` on Invalid Payload
- **Severity / Priority:** `P1`
- **Category:** Input Validation / Error Handling
- **Component:** `services/api/app/routers/decisions.py:487` & `services/engine/app/main.py`
- **Root Cause:** Missing Pydantic enum/bounds validation (`horizon_ticks <= 0`, unknown `scenario_type`) causes unhandled internal exception wrapped as `500 ENGINE_ERROR`.
- **Reproduction Steps:** `POST /api/whatif` with `{"scenario_type": "nonexistent_type", "horizon_ticks": -5}`.
- **Expected:** HTTP `422 VALIDATION_ERROR` with field error details per `20_FINAL_SPEC_LOCK.md`.
- **Actual:** HTTP **`500`**: `{"detail":{"code":"ENGINE_ERROR","message":"Internal Server Error"}}`.
- **Evidence:** [stage5_e2e_workflows.json](logs/stage5_e2e_workflows.json#L2613-L2619).

---

### DEF-007b — Control Panel Presets & Chaos Latency Use Wrong Simulator Parameter Keys (Dhaka Spike Mutates Chattogram Too)
- **Severity / Priority:** `P1`
- **Category:** Simulator Control / Domain Integrity
- **Component:** `services/api/app/routers/control.py:30,52,63,74,500`
- **Root Cause:** Presets send `"region": "dhaka"` (vs `region_ids: ["region-dhaka"]`), `"depot_id"` (vs `depot_ids`), `"multiplier": 0.5` on `supply_shortfall` (vs `factor`), and `"latency_ms": 1500` (vs `delay_ms`).
- **Reproduction Steps:** Trigger `POST /api/control/scenario/demand_spike_dhaka` and step 1 tick. Inspect `GET http://localhost:8000/v1/stations`.
- **Expected:** Only Dhaka stations (`station-mirpur`, `station-tongi`) scale `demand_multiplier` to `1.8`; Chattogram stays `1.0`.
- **Actual:** Simulator failed to match `"region": "dhaka"` filter and spiked **ALL 4 stations nationwide** (`station-mirpur`, `station-tongi`, `station-karnaphuli`, `station-coxsbazar`) to `demand_multiplier: 1.8`.
- **Evidence:** Live check output in `task-19608` (`MULTIPLIER_AFTER: {'station-mirpur': 1.8, 'station-tongi': 1.8, 'station-karnaphuli': 1.8, 'station-coxsbazar': 1.8}`); `stage5_deep_checks.json:84-136`.

---

### DEF-008 — Decisions Ledger Renders `0 L across 0 lines` for Every Decision (Plus Initial Hydration Past-Run Leak)
- **Severity / Priority:** `P1`
- **Category:** Data Correctness / UI State Race
- **Component:** `services/api/app/routers/decisions.py:158` (`GET /api/decisions`) & `frontend/src/pages/Decisions.tsx`
- **Root Cause:** `GET /api/decisions` omits `lines` and `total_liters` from decision items (`API DECISIONS ITEM SHAPE`). `Decisions.tsx` sums `d.lines` and displays `0 L across 0 lines` even when a decision has 7 lines / 10,900 L. Additionally, on initial cold load before `state.sim_run_id` hydrates, `Decisions.tsx` may fetch unfiltered history (`440` entries) and render past runs (`#1993`, `#1992`, `#1979`) under `"Scope: Active Run (#2006)"`.
- **Reproduction Steps:** Navigate to `/decisions` when `#DEC-1622` (7 lines, 10,900 L) is pending.
- **Expected:** Displays `10,900 L across 7 lines`, and only Run `#2006` entries when Active Run scope is selected.
- **Actual:** Displays `0 L across 0 lines`; cold initial capture showed 440 past-run entries under Active Run header.
- **Evidence:** `API DECISIONS ITEM SHAPE` JSON + [route_decisions_1440.png](screenshots/route_decisions_1440.png) + earlier initial capture `route_decisions_1440.png` (`133,885` B vs `67,181` B).

---

### DEF-009 — Overview `OPEN ALERTS` KPI (`0`) Contradicts `/alerts` Page & PostgreSQL (`2 OPEN` Alerts)
- **Severity / Priority:** `P1`
- **Category:** Cross-View State Consistency
- **Component:** `services/api/app/services/state_service.py` / `engine` Redis state vs `services/api/app/routers/risk_alerts.py` (`/api/alerts`)
- **Root Cause:** Overview reads `state.alerts_open_count` (`{INFO: 0, WARNING: 0, CRITICAL: 0}` from Redis snapshot), while `/alerts` queries PostgreSQL `alerts WHERE status='OPEN'`, which holds 2 unacknowledged `INVENTORY_VARIANCE` alerts at `station-karnaphuli` (`id=78, 79`, `sim_run_id=NULL`, `opened_tick=6`).
- **Reproduction Steps:** Compare `/overview` KPI card `OPEN ALERTS` against `/alerts` page at Run 2006 Tick 12.
- **Expected:** Consistent open alert counts across Overview badge, KPI, and `/alerts` ledger; alerts scoped cleanly by `sim_run_id`.
- **Actual:** `/overview` shows `0` (green "Unresolved"), while `/alerts` shows `2 OPEN` alerts (`station-karnaphuli PETROL` and `DIESEL`).
- **Evidence:** [route_overview_1440.png](screenshots/route_overview_1440.png), [route_alerts_1440.png](screenshots/route_alerts_1440.png), SQL `alerts` output (`id=78,79`).

---

### DEF-010 — Multiple CI Quality Gates Failing (`ruff`, `services/api`, `services/engine`, `frontend/e2e`, `evaluation`)
- **Severity / Priority:** `P1`
- **Category:** Automated Tests & Build/CI Health
- **Component:** `.github/workflows/ci.yml`, `services/api`, `services/engine`, `evaluation`, `frontend/e2e`
- **Failures Observed:**
  1. `ruff check libs services evaluation` fails with **5 lint errors** (`E501` in `db.py:408,423`, `test_engine_decision.py:356,364`; `I001` in `test_decisions_api.py:478`).
  2. `services/api` pytest: **1 failed** (`tests/test_entities.py::test_get_station_success` `StopAsyncIteration`, 67 passed, 5 warnings).
  3. `services/engine` pytest: **2 failed** (`tests/test_phase08_acceptance.py::test_solver_latency_p95_under_500ms` at `625.2ms > 500ms`; `tests/test_publisher.py::test_sync_loop_invokes_publisher` awaited 2× vs 1×; 325 passed, **31 integration tests deselected**).
  4. Existing `frontend/e2e/fsip.spec.ts`: **4 failed** (cannot launch missing `chromium_headless_shell.exe` in local Playwright cache).
  5. `evaluation/tests` fails test collection (`ModuleNotFoundError: No module named 'evaluation'`) unless run with repo-root `PYTHONPATH=.` (where 5 pass, **3 skipped**).
- **Evidence:** Full logs in `docs/evidence/full_system_qa/logs/pytest_*.log` and [TEST_EXECUTION_LOG.md](TEST_EXECUTION_LOG.md).

---

### DEF-011 — AI Assistant Silently Drops Open Alerts & Pending Decisions and Gives Contradictory Health Summary
- **Severity / Priority:** `P1`
- **Category:** AI Assistant Grounding
- **Component:** `services/api/app/routers/assistant.py:181,190,198,207`, `services/api/app/db.py:301,564`
- **Root Cause:** `fetch_alerts` and `fetch_decisions` return `tuple[list[dict], int]`, but `assistant.py` iterates directly over the return value expecting a list of dicts. Exceptions are caught and silenced into empty `alerts_compact = []` and `decisions_compact = []`.
- **Reproduction Steps:** Query `POST /api/assistant/ask` with `{"question": "Why is Cox's Bazar at risk and what open alerts or pending decisions exist?"}` and `{"question": "status"}`.
- **Expected:** Assistant grounds answer with active open `INVENTORY_VARIANCE` alerts and pending `#DEC-*`, and accurately reflects `DEGRADED` status.
- **Actual:** Open alerts and pending decisions are completely omitted (`sources: [{"type":"station","id":"station-coxsbazar"}]` only); status query answered: `"System overall status is Healthy. Engine is operating in DEGRADED mode at tick 12."`
- **Evidence:** [wf08_assistant_response.png](screenshots/wf08_assistant_response.png), [stage5_e2e_workflows.json](logs/stage5_e2e_workflows.json#L2621-L2635), `UNAUTH_ASSISTANT` response.

---

### DEF-012b — Insecure CORS Wildcard Reflected with `allow_credentials=True`
- **Severity / Priority:** `P1`
- **Category:** Security Misconfiguration
- **Component:** `services/api/app/main.py:85-91`
- **Root Cause:** `CORSMiddleware` configures `allow_origins=["*"]` with `allow_credentials=True`.
- **Reproduction Steps:** `curl -i -X OPTIONS http://localhost:3000/api/state -H "Origin: https://evil.example" -H "Access-Control-Request-Method: POST"`.
- **Expected:** Reject untrusted origins or disable credentials when wildcarding.
- **Actual:** Returns `access-control-allow-origin: https://evil.example` alongside `access-control-allow-credentials: true`.
- **Evidence:** `AUTH & CORS CHECK` output (`curl -i OPTIONS`).

---

## P2 — Medium Severity Defects

### DEF-012 — Undefined Custom Tailwind Classes (`bg-surface`, `text-accent`, `border-panel-border`, `bg-bg-base`) Compile to Transparent/Default CSS
- **Severity / Priority:** `P2`
- **Category:** Styling / UI Spec Compliance
- **Component:** `frontend/tailwind.config.js` vs `Network.tsx`, `Decisions.tsx`, `Control.tsx`, `WhatIf.tsx`
- **Expected:** Dark-ops design tokens (`22_UI_SPEC.md` §1.1) render active tab highlights, borders, and surface cards cleanly.
- **Actual:** Browser `getComputedStyle()` confirms `bg-surface`, `bg-accent`, `bg-bg-base` compute to `rgba(0, 0, 0, 0)`, `text-accent` inherits `rgb(230, 237, 247)`, `border-panel-border` falls back to light gray `rgb(229, 231, 235)`. `"Enter Replay Mode"` button on `/network` renders with transparent background (`rgba(0, 0, 0, 0)`).
- **Evidence:** [stage3_ui_responsive_audit.json](logs/stage3_ui_responsive_audit.json#L427-L464).

---

### DEF-013 — Missing Routes (`/decisions/:id`, `/system`), Ignored Deep-Link Params (`/network?station=`), & Unenforced `DEMO_CONTROLS` UI Hide Flag
- **Severity / Priority:** `P2`
- **Category:** Navigation / Feature Flags
- **Component:** `frontend/src/App.tsx`, `frontend/src/pages/Network.tsx`, `frontend/src/components/layout/Sidebar.tsx`, `frontend/src/pages/Control.tsx:120`
- **Actual:** `/decisions/1` and `/system` silently redirect to `/overview` via `*`; `/network?station=STN-004` renders 0 drawers/dialogs; `Sidebar.tsx` unconditionally links `/control` regardless of `DEMO_CONTROLS=false`, and `Control.tsx` defaults `demo_controls_enabled ?? true`.
- **Evidence:** [stage3_ui_responsive_audit.json](logs/stage3_ui_responsive_audit.json#L405-L426).

---

### DEF-014 — Recommendations Human-AI Teaming Metrics Are Hardcoded Static Numbers
- **Severity / Priority:** `P2`
- **Category:** Misleading / Fake UI Telemetry
- **Component:** `frontend/src/pages/Recommendations.tsx:333-356`
- **Actual:** `Acceptance Rate` (`92.4%`), `Override Rate` (`7.6%`), `Avg Time-to-Decision` (`4.2 s`), and `Autopilot Share` (`84.0%` vs `0.0%` toggle) are hardcoded string literals that remain completely unchanged across approvals, rejections, and resets.
- **Evidence:** [route_recommendations_1440.png](screenshots/route_recommendations_1440.png), [wf06_autopilot_after_click.png](screenshots/wf06_autopilot_after_click.png), [stage5_deep_checks.json](logs/stage5_deep_checks.json#L326).

---

### DEF-015 — Overview Supply Network Topology SVG Station Names Collide With `LOW` Risk Badges
- **Severity / Priority:** `P2`
- **Category:** Visual / Layout Defect
- **Component:** `frontend/src/components/overview/NetworkGraph.tsx`
- **Actual:** Long labels `"Karnaphuli Highway Stn"` and `"Cox's Bazar Regional Stn"` extend horizontally over their right-aligned green `LOW` badge boxes, reducing legibility.
- **Evidence:** [route_overview_1440.png](screenshots/route_overview_1440.png), [tunnel_overview_1440.png](screenshots/tunnel_overview_1440.png).

---

### DEF-016 — System Health Page Uses Dead Links (`localhost:3001`), Hardcoded `notifier`/`Decision Freshness`, `dev/unknown` Versions, & Loki `503 /ready`
- **Severity / Priority:** `P2`
- **Category:** Observability / Accurate Status Reporting
- **Component:** `frontend/src/pages/SystemHealth.tsx:422,431`, `services/api/app/routers/system.py:105,127-128,165-169`, `docker-compose.yml`
- **Actual:** External links point to `http://localhost:3001` & `http://localhost:3001/explore` (Grafana runs on `13001`; no Jaeger/Tempo trace store exists in compose); `/api/system/status` hardcodes `"notifier": {"status": "Healthy"}` (no notifier container exists) and `"Decision Freshness": 0.998`; backend reports `versions: dev / git_sha: unknown` while UI footer hardcodes `FSIP v1.0.0 · sha:local`; Loki `/ready` returns `503 Ingester not ready` (though `/loki/api/v1/labels` returns streams).
- **Evidence:** `SYSTEM STATUS` JSON, [route_health_1440.png](screenshots/route_health_1440.png), [stage3_ui_responsive_audit.json](logs/stage3_ui_responsive_audit.json#L365-L372), `LOKI_HTTP=503`.

---

### DEF-017 — Responsive Clipping: Mobile `390px` Header Clips `● LIVE` & Desktop `1440×900` Assistant Input Overflows Viewport Bottom
- **Severity / Priority:** `P2`
- **Category:** Responsive Design / Accessibility
- **Component:** `frontend/src/components/layout/Header.tsx`, `frontend/src/pages/Assistant.tsx`
- **Actual:** At `390×844` mobile, header clips `● LIVE` off the right edge across all pages (`● L` visible). At `1440×900` desktop, Assistant suggestion pills and prompt `<input>` (`boundingBox y=874.5, height=38 -> bottom=912.5 > 900`) extend past the bottom viewport edge; after submitting a query, heading `"Operations Assistant"` scrolls clipped beneath `<Header>`.
- **Evidence:** [resp_overview_mobile_390.png](screenshots/resp_overview_mobile_390.png), [route_assistant_1440.png](screenshots/route_assistant_1440.png), [wf08_assistant_response.png](screenshots/wf08_assistant_response.png), [stage5_deep_checks.json](logs/stage5_deep_checks.json#L34-L39).

---

### DEF-018 — What-If UI Omits `supply_shortfall` Event Type, Multi-Event Builder, & `ComparisonChart` (`22_UI_SPEC.md` §2.7)
- **Severity / Priority:** `P2`
- **Category:** Feature Completeness vs Spec
- **Component:** `frontend/src/pages/WhatIf.tsx`
- **Actual:** Event Type `<select>` exposes only `demand_spike`, `route_disruption`, `station_outage`, `depot_constraint`, `shipment_delay` — omitting `supply_shortfall`, multi-event rows, multi-select targets, decision prefill, and `ComparisonChart` (`recharts`). Also station cards in result summary all show `0% risk` even when scenario SL is `96.9%` (`681.7 L` unmet demand).
- **Evidence:** [stage5_deep_checks.json](logs/stage5_deep_checks.json#L60-L81), [wf07_whatif_result.png](screenshots/wf07_whatif_result.png).

---

### DEF-019 — Ledger (`ledger`), Reconciliation (`allocation_reconciliation`), & Notifier Runtime Integration Remain Empty / Disconnected
- **Severity / Priority:** `P2`
- **Category:** Unfinished Architecture Features
- **Component:** `services/engine`, `services/api/app/services/notifier.py:26`, `docker-compose.yml`
- **Actual:** Postgres `ledger` table (`0` rows) and `allocation_reconciliation` (`0` rows) remain completely empty even after live recommendation approvals (`#1678`) and executed manual dispatches (`#1634`). No `notifier` container or `mailpit` container runs by default, and `notifier.py` reads `FSIP_SMTP_HOST` (default `localhost`) while `.env` sets `NOTIFY_SMTP_HOST=mailpit`. Missing UI panels for reconciliation, demand suppression, reserve release, model activation, and policy comparison.
- **Evidence:** `POSTGRES DETAILED CHECKS` (`ledger_cnt=0`, `recon_cnt=0`), `task-19572` / `task-19616`.

---

## P3 — Low Severity Defects

### DEF-020 — `F-COVER` Overstates Daily Network Demand (`120k L/d` vs ~`93.1k L/d`) & Unauthenticated Read-Compute Endpoints
- **Severity / Priority:** `P3`
- **Category:** Domain Calibration / Rate-Limiting
- **Component:** `services/api/app/routers/risk_alerts.py:28`, `decisions.py`, `assistant.py`
- **Actual:** `NOMINAL_DAILY_DEMAND` hardcodes `60,000 + 40,000 + 20,000 = 120,000 L/d` (`demand_96` in `/api/cover` response), exceeding the actual simulator ~`93,100 L/d` baseline (`02_SIMULATOR_FACTS.md` §8). `POST /api/whatif` and `POST /api/assistant/ask` accept unauthenticated `200` requests.
- **Evidence:** [stage5_e2e_workflows.json](logs/stage5_e2e_workflows.json#L11-L28), `UNAUTH_WHATIF: 200`, `UNAUTH_ASSISTANT: 200`.

---

### DEF-021 — Network Inventory History Stitches Ticks Across Resets & Production Sidebar Shows Dev Phase Badges (`P8`–`P10`)
- **Severity / Priority:** `P3`
- **Category:** UI Polish / Chart Data Hygiene
- **Component:** `frontend/src/pages/Network.tsx`, `frontend/src/components/layout/Sidebar.tsx`
- **Actual:** `Inventory History (48 Ticks)` plots historical ticks (`98..134`) from an earlier run directly adjacent to `0..12` from the current run; sidebar renders internal milestone badges `P8`, `P9`, `P10` next to What-if, Assistant, and Control. Also `/health` counts against Redis `engine.errors` pushed `API Availability` below SLO target (`0.9937 < 0.995`, `budget_remaining: 0`) while `overall` reported `"Healthy"`.
- **Evidence:** [route_network_1440.png](screenshots/route_network_1440.png), [stage5_e2e_workflows.json](logs/stage5_e2e_workflows.json#L2639-L2665).

---

### DEF-022 — Optimizer Narrative Grammar Bug & Risk Engine AttributeError Tracebacks in Logs
- **Severity / Priority:** `P3`
- **Category:** Text Formatting / Runtime Warning Hygiene
- **Component:** `services/engine/app/decision/engine.py`, `services/engine/app/risk/stockout.py:263`, `services/engine/app/state/sync.py:505`
- **Actual:** Decision narrative reads `"Ship 10900 L across 1 allocation(s). Primary risk at 4"` when shipping **7 lines** to **4 stations** (confusing station count `4` as the risk station name and hardcoding `1 allocation(s)`). Earlier startup logs in `fsip-engine` also show `AttributeError: 'tuple' object has no attribute 'get'` (`stockout.py:263`) and `'Allocation' object has no attribute 'get'` (`sync.py:505`).
- **Evidence:** [route_recommendations_1440.png](screenshots/route_recommendations_1440.png), `fsip-engine` container logs (`task-19592`).
