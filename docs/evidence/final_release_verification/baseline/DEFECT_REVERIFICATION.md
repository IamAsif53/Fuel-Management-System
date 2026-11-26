# FSIP Final Release — Live Defect Reverification

**Run:** 2026-11-26 (live stack at `F:\NewTask\fuel-platform`)  
**Stack:** Frontend `:3000`, API `:18080/api`, Engine `:8100`, Simulator `:8000`, Redis `:16379`, TimescaleDB `:15432`  
**Method:** Prior `docs/evidence/defect_remediation/DEF-*/evidence.md` + live HTTP (`curl`/httpx), `docker exec fsip-frontend` bundle scan, browser spot-check (`http://localhost:3000`), targeted pytest.

**Brief alignment (product intent):** Participant console is **open hackathon mode** — no Sign-In/JWT UI; human gate is Approve/Reject; large allocations may approve in **one** operator click. DEF-001 four-eyes/login UI is **superseded**, not a release blocker.

---

| ID | Status | Concise verification line |
|----|--------|---------------------------|
| **DEF-001** | **INTENTIONALLY SUPERSEDED (brief-aligned open mode)** | `UserMenu.tsx` is Operator chip only (no login modal); `client.ts` open demo operator, no bearer; `POST /auth/login` still exists but optional; decision `#1914` 20,170 L approved → `EXECUTED` with one `selected_line_ids` call (no `AWAITING_SECOND_APPROVAL`). |
| **DEF-002** | **VERIFIED FIXED** | Live `POST /decisions/1914/approve` with `selected_line_ids` + `modified_quantities` → `200`, line 1 `1500 L` `SUBMITTED`, other lines `CANCELLED`; sim_allocation `#1`. |
| **DEF-003** | **VERIFIED FIXED** | With `stale_data` chaos while `PAUSED`, `GET /state` → `stale=true`, `engine_mode=DEGRADED`; `POST /control/mode autopilot` → **`409 STALE_DATA`**. (Paused + fresh data still allows autopilot — by design.) |
| **DEF-003b** | **VERIFIED FIXED** | `docker exec fsip-frontend`: **`change-me-operator` / `change-me` count = 0** in `/usr/share/nginx/html/assets/*.js`; source `frontend/` has no `change-me`; bundle only references `fsip_operator_token` in `removeItem` purge. |
| **DEF-004** | **VERIFIED FIXED** | `GET :8100/health/ready` → `200`, `engine_mode=NORMAL`, `last_sync_age_s<1s` while running; chaos cleared after DEF-003 probe → `engine_mode=NORMAL`. |
| **DEF-004b** | **VERIFIED FIXED** | `useLiveState.ts` handles `reset.detected` + `latestSimRunId` monotonic updates (code); not re-tested via live SSE reset this run. |
| **DEF-005** | **VERIFIED FIXED** | `GET /risk?region_id=` → **6 rows** each for `region-dhaka`, `dhaka`, `chattogram`, `region-chattogram`. |
| **DEF-006** | **VERIFIED FIXED** | `/risk` rows expose `risk_level`, `hours_to_stockout`, `stockout_prob_horizon`, `drivers` (no tuple errors); baseline tick shows `LOW`/`0%` (expected at t=0). |
| **DEF-007** | **VERIFIED FIXED** | Invalid `POST /whatif` and `POST /decisions/what-if` → **`422 VALIDATION_ERROR`** (not 500). |
| **DEF-007b** | **VERIFIED FIXED** | `POST /control/scenario/demand_spike_dhaka` → Dhaka stations `demand_multiplier=1.8`, Chattogram `1.0`; preset schema uses `region_ids`, `multiplier`; `depot_constraint_patiya` → Patiya `CONSTRAINED` after steps. |
| **DEF-008** | **VERIFIED FIXED** | `GET /decisions?sim_run_id=…` → non-zero `total_liters` / `line_count` (e.g. `#1925` 23,000 L, 6 lines); approve payload returns populated `lines`. |
| **DEF-009** | **VERIFIED FIXED** | `alerts_open_count` sum matches ledger OPEN count (`0==0` at probe tick); `verify_consistency.py` passed state vs recommendations for active run. |
| **DEF-010** | **FIXED AND REGRESSION TESTED** | Live spot: `pytest services/api/tests/test_auth.py` **5 passed**; prior full-suite counts in DEF-010 evidence unchanged this run. |
| **DEF-011** | **VERIFIED FIXED** | `POST /assistant/ask` → `200`; honest narrative with zero open alerts; no swallowed tuple errors (prior tests + live). |
| **DEF-012** | **VERIFIED FIXED** | Tailwind theme tokens (`surface`, `panel`, `shadow-panel`) present in `tailwind.config.js` and used in UI pages. |
| **DEF-012b** | **VERIFIED FIXED** | `OPTIONS` + `Origin: https://evil.example` → **400**, no `ACAO`/`ACC`; allowed `http://localhost:3000` → **200** with explicit `access-control-allow-origin` (no wildcard reflection). |
| **DEF-013** | **VERIFIED FIXED** | `/system` redirects to `/health`; `GET /control/status` → `demo_controls_enabled=true`; Control nav visible in browser. |
| **DEF-014** | **VERIFIED FIXED** | `GET /decisions/metrics` → `200` (live). |
| **DEF-015** | **VERIFIED FIXED** | `NetworkGraph.tsx` layout/truncation fixes present (code); not re-screenshot this run. |
| **DEF-016** | **VERIFIED FIXED** | `GET /system/status` → notifier `Unconfigured` with truthful message; `SystemHealth.tsx` Grafana links on `:13001`. |
| **DEF-017** | **VERIFIED FIXED** | `Header.tsx` / `Assistant.tsx` mobile/overflow clamps present (code + prior evidence). |
| **DEF-018** | **VERIFIED FIXED** | Browser `/whatif`: `supply_shortfall`, `shipment_delay`/`delay_ticks`, projection disclaimer visible. |
| **DEF-019** | **VERIFIED FIXED** | `engine.py` calls `ledger_append` on approve/reject (code); approve live `#1914` succeeded (runtime path exercised). |
| **DEF-020** | **VERIFIED FIXED** | `NOMINAL_DAILY_DEMAND` sums **91,400 L/d**; `GET /cover` → `200`; open-mode what-if returns 422 validation (not auth block). |
| **DEF-021** | **VERIFIED FIXED** | DB helpers filter by `sim_run_id`; Sidebar phase badges removed (grep). |
| **DEF-022** | **VERIFIED FIXED** | Live approve narrative: real line counts / stations in `explanation.headline` (e.g. "Ship 20,170 L across 5 allocations…"). |

---

## Open / partial (this run)

**None rated STILL OPEN or PARTIAL** against brief-aligned product intent.  

**Notes (not blockers):**
- DEF-004b: SSE reset after sim reset **not re-fired** in this session (code only).
- DEF-009: KPI sync with **non-zero** alerts **not re-fired** (stack had 0 open alerts).
- Autopilot eligibility text may still mention "four-eyes" thresholds; **behavior** is single-click approve per brief (DEF-001 superseded).