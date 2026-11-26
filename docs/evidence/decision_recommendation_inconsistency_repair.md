# FSIP Evidence Report: Decision and Recommendation API Inconsistency Repair & Verification

**Date:** 2026-11-26  
**Status:** Fully Resolved & Verified in Production Docker Stack  
**System Under Test:** FSIP (Fuel Supply Intelligence Platform)  

---

## 1. Executive Summary & Root Cause Analysis

### Observed Anomalies
1. **Historical Run Contamination:**
   - `/api/state` reported active simulation `sim_run_id=1894, tick=7`.
   - `/api/recommendations` returned 51 historical decisions, including records from `sim_run_id=1538, tick=192`, with status `PENDING_APPROVAL`.
   - `/api/decisions` listed old decisions (such as ID 1205 from run 1538 at tick 192) ahead of active run decisions because the ledger sorted by `tick DESC`.
   - Operators attempting to approve these ghost recommendations received HTTP 409 conflicts because the underlying simulation run was dead.
2. **State Store vs Recommendation Desynchronization:**
   - `GET /api/state` reported decision ID 1586 in `pending_recommendations` while `GET /api/recommendations` reported decision ID 1587 as `PENDING_APPROVAL`.
   - The decision ledger simultaneously reported ID 1586 as `EXPIRED`.
3. **Apparent Expiry Boundary Contradiction:**
   - Decision ID 1586 had `expires_at_tick=11` but was reported `EXPIRED` at tick 10.

---

## 2. In-Depth Root Cause Breakdown

### A. The Cross-Run Contamination
1. **Unscoped Database Query in `fetch_recommendations` (`services/api/app/db.py`):**
   The query filtered strictly on `status IN ('PENDING_APPROVAL', 'PROPOSED', 'AWAITING_SECOND_APPROVAL')` without filtering by `sim_run_id`. Historical unfinalized decisions from dead runs remained in `PENDING_APPROVAL` indefinitely.
2. **Missing Multi-Run Expiry:**
   When the simulator reset or advanced to a new `sim_run_id`, `expire_stale_decisions` only inspected decisions where `Decision.sim_run_id == store.sim_run_id`.
3. **Flawed Sort Order in Decision Ledger (`fetch_decisions`):**
   The decision ledger sorted records by `ORDER BY tick DESC, id DESC`. Tick count resets to 0 on new runs, causing long-running past runs (tick 192) to sort ahead of freshly started runs (tick 7).

### B. Discrepancy 1: Why `/api/state` Held ID 1586 While `/api/recommendations` Held ID 1587
Tracing the execution order in `services/engine/app/state/sync.py` and `services/engine/app/decision/engine.py` revealed a synchronization ordering bug:
1. In `SyncLoop.full_sync()` (`services/engine/app/state/sync.py:590`), `await self.publisher.publish_state(self.store)` was invoked **before** lifecycle hooks were executed (line 657).
2. The hook `decision_engine.on_cycle(self.store)` ran **after** the state had already been serialized and published to Redis `fsip:state`.
3. When `on_cycle` executed at tick 10:
   - It superseded decision 1586 and created fresh decision 1587.
   - It updated in-memory `store.pending = [{"decision_id": 1587, ...}]`.
   - However, `_sync_store_pending` never called `publish_state`, and `sync.py` did not re-publish after hooks completed.
4. Consequently, Redis `fsip:state` retained the pre-hook snapshot containing decision 1586 from tick 9.
5. In `services/api/app/services/state_service.py`, `get_current_state` read directly from Redis `fsip:state` without reconciling against the database, serving the stale decision ID 1586.

### C. Discrepancy 2: Why ID 1586 Was `EXPIRED` at Tick 10 Despite `expires_at_tick=11`
Two distinct lifecycle mechanisms govern recommendations in FSIP:
1. **Time-Based Expiry Boundary (`expire_stale_decisions`):**
   - Per `05_INTELLIGENCE_DESIGN.md` §4.5: *"recommendation not acted on by expires_at_tick (t + 2) -> EXPIRED; the next cycle re-plans with fresh data"*.
   - Evaluated by `current_tick >= expires_at_tick` (or `expires_at_tick <= current_tick`).
   - Cleans up recommendations when planning is paused or when decision interval > 1.
2. **Event-Based Plan Supersession (`_supersede_older_decisions`):**
   - Per `23_MODULE_SPECS.md`: *"twin impact + alternatives, confidence (05 §4.4), explanation, persist, supersede older PENDING, approval policy"*.
   - When the engine runs a planning cycle and creates a fresh recommendation for the active simulation run, all previous unacted pending recommendations for that run are superseded.
   - The implementation transitions the superseded record to `status = "EXPIRED"` and records `rejected_reason = "Superseded by fresh plan at tick {cur_tick}"`.
   - Therefore, at tick 10, decision 1586 was transitioned to `EXPIRED` because a fresh plan (1587) was generated, **not** because it reached its tick expiry limit of 11.

---

## 3. Implemented Fixes

### 1. Engine Store Synchronization & Real-Time Publishing
- **`services/engine/app/decision/engine.py`:**
  - In `_sync_store_pending`: queries the database for decisions where `Decision.sim_run_id == store.sim_run_id`, `Decision.status.in_(["PENDING_APPROVAL", "AWAITING_SECOND_APPROVAL", "PROPOSED"])`, and `(Decision.expires_at_tick > current_tick) | (Decision.expires_at_tick.is_(None))`.
  - Immediately invokes `await self.publisher.publish_state(store)` upon updating `store.pending`, guaranteeing that Redis `fsip:state` is updated synchronously whenever recommendations change.
  - In `approve_decision` and `reject_decision`: triggers `_sync_store_pending` so approvals and rejections reflect immediately in the public state projection.
- **`services/engine/app/state/sync.py`:**
  - Added post-hook state publication in `full_sync`: `await self.publisher.publish_state(self.store)` runs after all registered lifecycle hooks complete, ensuring any state modifications made by hooks are reflected in Redis.

### 2. State Service Database Reconciliation
- **`services/api/app/services/state_service.py`:**
  - In `get_current_state`: dynamically reconciles `state["pending_recommendations"]` against `fetch_recommendations(session, sim_run_id=sim_run_id, current_tick=tick)`.
  - Guarantees that `/api/state` and `/api/recommendations` derive from the exact same database truth, completely preventing stale or expired decisions from appearing in `/api/state`.

### 3. Database Scoping & Sorting
- **`services/api/app/db.py`:**
  - `fetch_recommendations`: defaults to active run scoping and current tick validation while supporting `all_runs=true` for cross-run audit.
  - `fetch_decisions`: sorts by `sim_run_id DESC NULLS LAST, tick DESC, id DESC`.
- **`services/engine/app/db/models.py`:**
  - Added composite indexes:
    - `ix_decisions_sim_run_tick`: `(sim_run_id DESC, tick DESC, id DESC)`
    - `ix_decisions_status_expiry`: `(status, expires_at_tick)`

### 4. Frontend UI Run Filtering
- **`frontend/src/pages/Decisions.tsx`:** Added Run Scope toggle ("Active Run" vs "All Runs") and Run badges.
- **`frontend/src/pages/Recommendations.tsx`:** Added Run badges and expiry tick indicators.

---

## 4. Regression Test Suites

### Engine Regression Tests (`services/engine/tests/test_engine_decision.py`)
Added `test_store_pending_and_redis_snapshot_synchronized_after_supersession`:
- Creates decision 1 at tick 9 (`expires_at_tick=11`).
- Creates decision 2 at tick 10 (`expires_at_tick=12`).
- Verifies decision 1 in database transitions to `status="EXPIRED"` with `rejected_reason="Superseded by fresh plan at tick 10"`, while retaining its natural expiry boundary `expires_at_tick=11`.
- Verifies `state.pending` contains **only** decision 2.
- Verifies `publisher.publish_state` is called with the synchronized pending list.
- **Result:** `11 passed in 17.72s`.

### API Regression Tests (`services/api/tests/test_decisions_api.py`)
Added `test_state_service_reconciles_pending_recommendations_and_filters_superseded_or_expired`:
- Injects a stale Redis snapshot containing superseded decision 1586.
- Configures database mock with active decision 1587.
- Verifies `get_current_state` replaces stale Redis items with active DB decision 1587.
- Verifies `fetch_recommendations` was invoked with active `sim_run_id=1979, current_tick=10`.
- **Result:** `13 passed in 3.29s`.

---

## 5. Live Endpoint Verification Evidence

Live captures executed sequentially against the running Docker application:

### Sequential Capture at Tick 12 (`sim_run_id=1992`)
**Timestamp:** `2026-11-25T21:33:19.030537+00:00`

#### 1. `GET /api/state`
```json
{
  "sim_run_id": 1992,
  "tick": 12,
  "sim_status": "PAUSED",
  "pending_recommendations": [
    {
      "decision_id": 1605,
      "summary": "Recommendation for tick 12: Ship 10900 L across 2 allocation(s)...",
      "confidence": 0.99,
      "total_liters": 10900.0,
      "expires_at_tick": 14
    }
  ]
}
```

#### 2. `GET /api/recommendations`
```json
{
  "items": [
    {
      "id": 1605,
      "sim_run_id": 1992,
      "tick": 12,
      "status": "PENDING_APPROVAL",
      "expires_at_tick": 14,
      "confidence": 0.99,
      "policy": "optimizer@1.0.0"
    }
  ]
}
```

#### 3. `GET /api/decisions?sim_run_id=1992`
```
=== DECISIONS FOR ACTIVE RUN 1992 ===
  ID: 1605 | tick: 12 | status: PENDING_APPROVAL | expires_at_tick: 14 | rejected_reason: None
  ID: 1604 | tick: 11 | status: EXPIRED          | expires_at_tick: 13 | rejected_reason: Superseded by fresh plan at tick 12
  ID: 1603 | tick: 10 | status: EXPIRED          | expires_at_tick: 12 | rejected_reason: Superseded by fresh plan at tick 11
```

### Verification Matrix
| Criteria | `/api/state` | `/api/recommendations` | `/api/decisions` | Agreement |
|---|---|---|---|---|
| Active `sim_run_id` | 1992 | 1992 | 1992 | **MATCH** |
| Current Tick | 12 | 12 | 12 | **MATCH** |
| Active Pending Decision ID | 1605 | 1605 | 1605 | **MATCH** |
| Expiry Tick | 14 | 14 | 14 | **MATCH** |
| Stale/Superseded IDs (1603, 1604) in Pending? | **No** (0) | **No** (0) | **No** (Marked EXPIRED) | **MATCH** |
