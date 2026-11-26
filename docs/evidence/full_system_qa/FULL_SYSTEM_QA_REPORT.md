# FSIP — Independent Full-System QA & Acceptance Audit Report

**Audit Date:** 2026-11-26  
**Auditor Role:** Independent Senior QA Engineer, Test Architect & Acceptance Auditor  
**Repository:** `https://github.com/IamAsif53/Fuel-Management-System` (`f:\NewTask\fuel-platform`)  
**Git State:** Branch `main`, `HEAD = 3a2ae78` (dirty working tree with 11 modified files + 4 untracked files prior to this QA directory)  
**Target Environments Audited:**
- Local Stack (`http://localhost:3000` UI/Nginx proxy, `:18080` API, `:8100` Engine, `:8200` Intelligence, `:8000` Simulator, `:15432` TimescaleDB, `:16379` Redis, `:9090` Prometheus, `:13001` Grafana, `:9093` Alertmanager, `:3100` Loki)
- Public Cloudflare Quick Tunnel: `https://utilities-toddler-proxy-menu.trycloudflare.com` *(Note: prompt URL `https://cooperation-camcorder-obtain-resolution.trycloudflare.com` is dead — NXDOMAIN / curl exit `6`)*

---

## 1. Executive Verdict

### **Acceptance Decision: `NOT READY`**

While individual microservices start, serve HTTP `200`, and show a visually polished dark-mode console, independent end-to-end QA against the running system reveals **4 P0 Acceptance Blockers**, **9 P1 High Severity Defects**, **8 P2 Medium Defects**, and **3 P3 Low Defects**. Previous completion claims in `PROGRESS.md` and `docs/evidence/final_release_readiness.md` (`"Verdict: READY"`, `547/547` passing tests, `0` lint errors) do **not** reflect the current behavior of the running repository and live stack.

Core operational guarantees are currently broken:
1. **Four-eyes approval (`>7,000 L`) is permanently deadlocked (`DEF-001`)**: Almost every optimizer plan exceeds `7,000 L` (`10.9k–23.0k L`), entering `AWAITING_SECOND_APPROVAL`, where a second distinct operator (`"supervisor"`) is still rejected with `403 FOUR_EYES_SAME_ACTOR` because real multi-user JWT auth (`/api/auth/login`) is absent and the API overrides `operator` with the single static `"operator"` token identity.
2. **Human line selection & quantity overrides are silently dropped (`DEF-002`)**: Sending `selected_line_ids` and `modified_quantities` to `POST /api/decisions/{id}/approve` returns HTTP `200` while discarding edits and keeping all original `PLANNED` lines unchanged.
3. **Autopilot stale-data safety check fails when paused (`DEF-003`) & public JS bundle leaks operator token (`DEF-003b`)**: Enabling Autopilot while `stale_data` chaos is injected on a paused simulator succeeds (`200 OK`), and `"change-me-operator"` is shipped verbatim inside the production JS bundle over the public tunnel.
4. **Domain views & AI Assistant show false data (`DEF-005`, `DEF-006`, `DEF-008`, `DEF-009`, `DEF-011`)**: `/risk` shows `0%` stockout probability across all stations and returns `0` rows on Dhaka filter; `/decisions` renders `0 L across 0 lines`; `/overview` displays `0` OPEN ALERTS while `/alerts` has `2` OPEN alerts; AI Assistant drops all open alerts and pending decisions silently; Control Panel `"Demand Spike — Dhaka"` spikes all Chattogram + Dhaka stations (`1.8×`); `/api/whatif` returns HTTP `500` on invalid input; CI lint & unit tests fail (`ruff` 5 errors, `services/api` 1 failed, `services/engine` 2 failed, `frontend/e2e` 4 failed).

---

## 2. Repository & Environment Baseline Inspection

- **Git State:** `main` at `3a2ae78`, containing uncommitted changes from prior repair tasks across `Decisions.tsx`, `Recommendations.tsx`, `db.py`, `routers/decisions.py`, `state_service.py`, `engine/decision/engine.py`, `sync.py`, `models.py`, and regression tests.
- **Specification Hierarchy:** `20_FINAL_SPEC_LOCK.md` > `21_API_CONTRACTS.md` > `23_MODULE_SPECS.md` > `22_UI_SPEC.md` > `13_ARCHITECTURE_V2.md` > domain specs. (`24_PHASE00_FILE_CONTENTS.md` was verified absent from `docs/architecture/`.)
- **Containers Running:** 14 of 16 compose services running (`simulator`, `timescaledb`, `redis`, `intelligence`, `engine`, `api`, `frontend`, `prometheus`, `grafana`, `alertmanager`, `loki`, `promtail`, `cadvisor`, `node-exporter`). `mailpit` and `chaos-proxy` profiles were not started; `notifier` and `jaeger` services referenced in docs do not exist in `docker-compose.yml`.
- **Tunnel Verification:**
  - `https://cooperation-camcorder-obtain-resolution.trycloudflare.com/` -> **DEAD / NXDOMAIN** (`curl exit 6`, HTTP `000`).
  - `https://utilities-toddler-proxy-menu.trycloudflare.com/` -> **ACTIVE** (HTTP `200` on `/` and `/api/state`, `task-19309` PID `13000`).

---

## 3. Architecture & Spec Compliance vs Claims

Comparison against prior claims (`docs/evidence/final_release_readiness.md`, `PROGRESS.md`):
- Claim `"547 total automated tests passed with 0 failures and 0 Ruff lint errors"` -> **FALSE on current workspace**: `ruff` fails with 5 errors; `services/api` fails `test_get_station_success`; `services/engine` fails `test_solver_latency_p95_under_500ms` (`625.2ms > 500ms`) and `test_sync_loop_invokes_publisher`; `frontend/e2e` fails `4/4` on missing `headless_shell.exe`; `evaluation/tests` fails collection unless invoked from root `PYTHONPATH=.`.
- Claim `"overall=Healthy / engine_mode=NORMAL / ready"` -> **MISLEADING when idle/paused**: Baseline idle observation showed `engine_mode="DEGRADED"` (`degraded=false`) stuck indefinitely (`last_sync_age_s=1075s+` on `/health/ready`) because paused ticks skip `full_sync()` recovery cycles while Redis `fsip:health` stayed frozen at `last_sync_age_s: 2.94` (`DEF-004`).
- Claim `"Tamper-evident ledger & reconciliation verified"` -> **NOT WIRED IN RUNTIME**: Postgres `ledger` and `allocation_reconciliation` both have **`0` rows** after live approvals and dispatches (`DEF-019`).

---

## 4. What Works Well

- **Core Optimization & Simulation Loop (when stepping):** LP optimizer (`HiGHS`/PuLP) solves feasible plans (`#1622`, `#1625`, `#1630`, `#1635–1638`, `#1678`) with explainable `why_at_risk`, `expected_impact`, and constraint metadata.
- **Run-Scoped Decision & Expiry Consistency:** After recent repair patches, `/api/state`, `/api/recommendations`, `/api/decisions?sim_run_id=...`, and Postgres agree on active run pending decisions (`#1622` in Run 2006; `#1635–1638` in Run 2007) and exclude `EXPIRED` rows (`#1635`, `#1636` transitioned cleanly at expiry).
- **F-TRUST Cognitive Review & Manual Dispatch:** Approving without `reviewed_driver: true` returns `422 REVIEW_REQUIRED` cleanly; Manual Dispatch (`POST /api/manual-allocation`) executes cleanly (`201 Created`, `EXECUTED`, e.g., `#1634` / line `2792`).
- **Four-Eyes Hold Threshold Display:** In Autopilot (`NORMAL` mode), decisions exceeding `7,000 L` total (`#1625` 12,620 L, `#1636` 20,170 L) display the yellow `"Autopilot Held (Operator Review Required)"` banner instead of silently executing without review.
- **Basic Resilience During Unavailable Faults:** Injecting `unavailable` (HTTP 503) transitions engine to `DEGRADED` (`10` `*_failed` reasons), surfaces the `"Cannot Enable Autopilot"` (`409`) error modal in UI, and recovers cleanly to `NORMAL` after clearing chaos + 2 ticks.
- **Navigation Shell & Tunnel Boundary:** Zero `pageerror`s, zero uncaught console errors on core routes, working `Ctrl+K` Command Palette, working tunnel reverse proxy, and `/admin`, `/.env`, `/.git/HEAD` do not leak filesystem or simulator internals over HTTP.
- **Prometheus & Grafana Dashboards:** Prometheus scrapes `6/6` targets (`up`); Grafana reports DB `ok` with all 4 FSIP dashboards provisioned cleanly.

---

## 5. Critical Bugs & Blockers (Summary)

Full repro steps and payloads are documented in [DEFECT_REGISTER.md](DEFECT_REGISTER.md). Highlights:
- **P0 (`DEF-001`):** Four-eyes 2nd approval deadlocks (`403 FOUR_EYES_SAME_ACTOR` even for `"supervisor"`); `/api/auth/login` & `/api/auth/me` return `404`.
- **P0 (`DEF-002`):** `POST /api/decisions/{id}/approve` silently drops `selected_line_ids`, `modified_quantities`, and `operator` (plus earlier `#1631` `500 ENGINE_ERROR`).
- **P0 (`DEF-003`):** `stale_data` chaos on paused simulator still allows `POST /api/control/mode {"mode":"autopilot"}` -> `200 OK`.
- **P0 (`DEF-003b`):** Public frontend production JS bundle embeds `"change-me-operator"` bearer secret.
- **P1 (`DEF-004`, `DEF-004b`):** Engine stuck `DEGRADED` when paused (`last_sync_age_s` > `1075s`); SSE drops post-reset `tick < latestTick` updates so open `/overview` tabs freeze on pre-reset tick `t=21`.
- **P1 (`DEF-005`, `DEF-006`, `DEF-007b`):** Risk page shows `0%` prob everywhere & Dhaka filter returns `0` rows; Control `"Demand Spike — Dhaka"` spikes all 4 stations nationwide (`1.8×`).
- **P1 (`DEF-007`, `DEF-008`, `DEF-009`, `DEF-011`, `DEF-012b`):** What-If returns `500` on bad input; `/decisions` displays `0 L across 0 lines`; `/overview` `OPEN ALERTS: 0` contradicts `/alerts` (`2`); Assistant drops alerts & decisions (`Healthy` + `DEGRADED` contradiction); CORS reflects `https://evil.example` with `Allow-Credentials: true`.

---

## 6. Interactive User Workflow Results

| Workflow | Outcome | Key Evidence |
|---|---|---|
| WF-01 Overview KPIs & EntityDrawer | **PARTIAL / FAIL** | Service Level `100%`, Pending `1` match `/api/state`; **but** `OPEN ALERTS` says `0` vs `2` in DB (**DEF-009**), and long station names overlap `LOW` badges (**DEF-015**). |
| WF-02 Network Deep-Link & Time-Travel | **PARTIAL** | `/network` loads; `"Enter Replay Mode"` styling transparent (**DEF-012**); `?station=STN-004` ignored (**DEF-013**); history chart stitches ticks across reset (**DEF-021**). |
| WF-03 Risk Filter & Probability | **FAIL** | Dhaka filter returns `0` matching rows (**DEF-005**); all rows report `0%` prob & `Safe (>96h)` even when recommendations report `72%`/`100%` risk (**DEF-006**). |
| WF-04 Control Stepping, SSE & Presets | **FAIL** | Step `+1` updates open tab via SSE, **but** Reset freezes open tab on pre-reset tick (**DEF-004b**); Dhaka spike preset spikes Chattogram stations too (**DEF-007b**). |
| WF-05 Decisions Lifecycle (Modify / 4-Eyes / Manual) | **FAIL** | F-TRUST `422` & Manual Dispatch `201` pass; **but** partial/quantity edits ignored (**DEF-002**) & 2nd 4-eyes approval deadlocked `403` (**DEF-001**). |
| WF-06 Autopilot E2E & Stale Guard | **FAIL** | Held `>7,000 L` plans cleanly once `NORMAL`; **but** paused engine stuck `DEGRADED` (**DEF-004**) & `stale_data` chaos failed to block `POST /api/control/mode` (**DEF-003**). |
| WF-07 What-If Scenario Simulation | **FAIL** | Runs `do_nothing` / `recommended` / `custom`; **but** invalid input throws **HTTP 500** (**DEF-007**); omits `supply_shortfall` UI & comparison chart (**DEF-018**). |
| WF-08 Assistant Q&A | **FAIL** | Answers station lookup, **but** drops alerts & pending decisions (**DEF-011**), contradicts self (`Healthy` + `DEGRADED`), and prompt bar overflows bottom of `900px` screen (**DEF-017**). |
| WF-09 Alerts, Health & Notifications | **FAIL** | `/alerts` ack works & shows `2` open alerts, **but** `/health` hardcodes `notifier` & `Decision Freshness`, links dead `:3001` (**DEF-016**), and `ledger`/`recon` tables remain empty (**DEF-019**). |

---

## 7. Backend, API, TimescaleDB & Redis Consistency Verification

- **Sequential API Parity (`Run 2006 Tick 12` & `Run 2007`):** Verified `/api/state.pending_recommendations`, `/api/recommendations`, `/api/decisions?sim_run_id=...`, and Postgres `decisions` match on active run IDs and exclude `EXPIRED` records cleanly (`DR-01`, `DR-02` **PASS**).
- **TimescaleDB Integrity:**
  - `0` orphaned rows in `decision_lines`.
  - `0` duplicate `(sim_run_id, tick, station_id, fuel_type)` keys in `demand_observations`.
  - Both TimescaleDB hypertables (`demand_observations`, `world_snapshots`) confirmed active.
  - Anomaly: `alerts` contains 2 duplicate OPEN rows (`id=78, 79`, `station-karnaphuli`) with `sim_run_id = NULL`, and both `ledger` (`0` rows) & `allocation_reconciliation` (`0` rows) are unused at runtime.
- **Redis Health & Streams:** `fsip:domain-events` stream active (`1,809` entries); `fsip:health` hash freezes stale `last_sync_age_s: 2.94` while `/health/ready` climbs past `1,075s` when simulator pauses (`DEF-004`).

---

## 8. Browser UI, UX, Console, Network & Responsive Findings

- **Console & Network (`10` primary routes):** Zero `pageerror`s, zero console errors, zero `>= 400` responses during normal route navigation & reload. Minor React Router v7 future-flag console warnings appear on every load.
- **CSS / Tailwind Audit (`getComputedStyle`):** Custom theme utilities (`bg-surface`, `text-accent`, `bg-accent`, `border-panel-border`, `bg-bg-base`) compile to transparent / unstyled CSS (`DEF-012`).
- **Responsive Sweep (`1440×900`, `1280×720`, `768×1024`, `390×844`):**
  - `documentElement.scrollWidth === clientWidth` across all 40 combinations.
  - Visual inspection exposed **two layout clipping defects (`DEF-017`)**: `390×844` mobile header clips the right-hand `● LIVE` badge on all routes; `1440×900` desktop Assistant input box extends below `y=900` (`y=874.5..912.5`).
  - SVG station label overlaps `LOW` badges on `/overview` (`DEF-015`).

---

## 9. Safety, Security & Authorization Assessment

- **Public Access Boundary:** Tunnel URL `https://utilities-toddler-proxy-menu.trycloudflare.com` exposes only `:3000`. `/admin/*`, `/v1/instance`, `/.env`, `/.git/HEAD` hit Nginx SPA HTML fallback rather than backend internals.
- **Token Leakage Nullifies Auth Boundary (`DEF-003b`):** Because `change-me-operator` is bundled in plaintext inside `/assets/index-CpEOeSFy.js`, any public visitor can extract the static operator token and invoke authenticated `/api/control/*` and `/api/decisions/*` mutations over the public tunnel.
- **No Real Multi-User Auth (`DEF-001`):** `/api/auth/login` and `/api/auth/me` (`404`) are absent, causing four-eyes deadlock (`DEF-001`).
- **CORS Reflection (`DEF-012b`):** `OPTIONS /api/state` echoes `Access-Control-Allow-Origin: https://evil.example` with `Access-Control-Allow-Credentials: true`.
- **Unauthenticated Compute Endpoints:** `POST /api/whatif` and `POST /api/assistant/ask` succeed without auth (`200`), permitting unauthenticated CPU/MILP exhaustion.

---

## 10. Performance, Reliability, Resilience & Observability

- **Solver Benchmark:** `test_solver_latency_p95_under_500ms` **FAILED locally at `625.2 ms` p95** (`> 500 ms` locked budget).
- **Resilience Matrix:**
  - `unavailable` chaos: Cleanly degrades, blocks Autopilot (`409`), recovers in 2 steps after clear (**PASS**).
  - `stale_data` chaos while paused: Fails to block `POST /api/control/mode {"mode":"autopilot"}` (**FAIL, DEF-003**).
  - Idle paused state: Traps engine in `DEGRADED` (`clean_cycles=1`) indefinitely until 2 ticks step (**FAIL, DEF-004**).
- **Observability Stack:**
  - Prometheus `http://localhost:9090`: `6/6` scrape targets `up`.
  - Alertmanager `http://localhost:9093`: healthy (`200`), webhook pipeline working.
  - Grafana `http://localhost:13001`: DB `ok`, 4 dashboards provisioned — **but `/health` links point to dead port `:3001`** (`DEF-016`).
  - Loki `http://localhost:3100`: `/loki/api/v1/labels` works, `/ready` returned `503 Ingester not ready`.

---

## 11. Test Execution Summary & Requirement Coverage

- Complete command log, counts, exit codes, failures, skipped, deselected, and warnings: [TEST_EXECUTION_LOG.md](TEST_EXECUTION_LOG.md).
- Full Requirement-to-Test Matrix across Functional, Data, Security, UI/UX, and Reliability: [REQUIREMENT_COVERAGE.md](REQUIREMENT_COVERAGE.md).
  - **Untested / Blocked Items Honestly Noted:** 31 `services/engine` integration tests (deselected by default `pytest.ini`), 3 `evaluation/test_harness.py` scenarios (skipped without isolated `:8005` sim), Mailpit email receipt (not started in compose), sustained multi-day soak/load tests (out of interactive QA scope), and full Autopilot execution of a `<= 7,000 L` plan (no baseline/run plan had total `<= 7,000 L` during step sweep, and quantity modification is broken per `DEF-002`).

---

## 12. Missing / Unimplemented / Incomplete Items

1. Multi-user JWT authentication (`/api/auth/login`, `/api/auth/me`, `UserMenu`, role enforcement for `operator` vs `approver`/`admin`).
2. `ScarcityBadge` header component and Decision Detail page `/decisions/:id` (`22_UI_SPEC.md` §1.3, §2.5).
3. Runtime `ledger` hash-chain appends & `allocation_reconciliation` recording (`DEF-019`).
4. What-If UI features (`supply_shortfall`, multi-event inputs, editable delay ticks, `ComparisonChart`, decision prefill).
5. UI workflows for `/api/reports/reconciliation`, `/api/contingency/demand-suppression`, `/api/control/reserve/release`, `/api/models/activate`, `/api/lab/compare-policies`, `/api/ledger*`.
6. Containerized `notifier` & `jaeger`/trace collector (`docker-compose.yml`), accurate `APP_VERSION` & `GIT_SHA` build injection.

---

## 13. Prioritized Fix Roadmap

1. **Immediate (P0 Release Gate):**
   - Implement real multi-role JWT auth (`/api/auth/login`, `/api/auth/me`), remove `"change-me-operator"` from frontend bundle (`DEF-001`, `DEF-003b`).
   - Fix `POST /api/decisions/{id}/approve` forwarding so `selected_line_ids`, `modified_quantities`, and authenticated actor identity reach `DecisionEngine.approve_decision()` (`DEF-001`, `DEF-002`).
   - Evaluate `stale_data` / simulator health periodically even when `PAUSED` so Autopilot rejects (`409`) immediately when stale and engine doesn't stall in recovery cycle 1 (`DEF-003`, `DEF-004`).
2. **Short-Term (P1 Core Functional & CI Fix):**
   - Restrict CORS origins (no wildcard `*` with `allow_credentials=True`, `DEF-012b`).
   - Fix SSE `useLiveState.ts` reset guard to compare `(sim_run_id, tick)` (`DEF-004b`).
   - Fix `/api/risk` key lookup (`stockout_prob_lead` / `stockout_prob_horizon`) and `Risk.tsx` region values (`region-dhaka`, `region-chattogram`) (`DEF-005`, `DEF-006`).
   - Add Pydantic validation to `WhatIfRequest` -> `422` (`DEF-007`), fix Control preset/chaos parameter keys (`region_ids`, `depot_ids`, `factor`, `delay_ms`, `DEF-007b`).
   - Include line counts/volumes in `GET /api/decisions` + wait for `sim_run_id` before Active Run fetch (`DEF-008`); sync Overview `OPEN ALERTS` with DB (`DEF-009`); unpack `(items, total)` tuples in `assistant.py` (`DEF-011`).
   - Resolve `ruff` (5 errors), `test_entities.py`, `test_publisher.py`, optimizer p95 latency regression, and `frontend/e2e` browser install (`DEF-010`).
3. **Polish & Completeness (P2/P3):**
   - Define Tailwind theme tokens in `tailwind.config.js` (`DEF-012`), fix `/overview` SVG label truncation (`DEF-015`), mobile header `● LIVE` clipping, & Assistant bottom overflow (`DEF-017`).
   - Replace hardcoded Recommendation KPIs (`DEF-014`), hardcoded SystemHealth `notifier`/`Decision Freshness`/`:3001` links (`DEF-016`), add missing routes/deep-links & `DEMO_CONTROLS` enforcement (`DEF-013`), and wire `ledger`/`reconciliation` (`DEF-019`).

---

## 14. Artifacts Index

- [DEFECT_REGISTER.md](DEFECT_REGISTER.md) — Detailed P0–P3 defect catalog
- [REQUIREMENT_COVERAGE.md](REQUIREMENT_COVERAGE.md) — Requirement-to-Test traceability matrix
- [TEST_EXECUTION_LOG.md](TEST_EXECUTION_LOG.md) — Exact commands, test counts, warnings, and logs
- [SCREENSHOT_INDEX.md](SCREENSHOT_INDEX.md) — Complete 39-screenshot & HTML report index
- Machine-readable logs & Playwright QA specs: `docs/evidence/full_system_qa/{logs,playwright,screenshots}/`
