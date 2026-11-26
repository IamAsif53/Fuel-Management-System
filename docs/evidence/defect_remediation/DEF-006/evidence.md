# DEF-006 — Stockout Probability, Risk Calculations & In-Flight Tuple Fix

## Status: FIXED — LIVE VERIFICATION PASSED

## Root Cause
1. `services/engine/app/risk/stockout.py` emits `stockout_prob_lead` and `stockout_prob_horizon`, whereas `services/api/app/routers/risk_alerts.py:67` read `r_info.get("stockout_prob") or 0.0` (non-existent), always returning `0.0` (`0%`) on `/api/risk` and `/risk`.
2. `risk_alerts.py:66` used `r_info.get("hours_to_stockout") or 999.0`, silently turning imminent zero-hour stockout (`0.0`) into `999.0` (`Safe (>96h)`).
3. `services/engine/app/risk/stockout.py:263` assumed every item in `all_inflight` was an object/dict with `.destination_station_id` or `.get(...)`, crashing `assess()` with `AttributeError: 'tuple' object has no attribute 'get'` whenever `WorldState.inflight()` returned active shipment tuples `(station, fuel, arr_tick, qty)`.

## Changes Implemented
- `services/api/app/routers/risk_alerts.py`:
  - Mapped `stockout_prob` from `max(stockout_prob_lead, stockout_prob_horizon)` when `stockout_prob` is absent, and exposed `stockout_prob_lead`, `stockout_prob_horizon`, and `cover_hours` on each row.
  - Replaced `or 999.0` truthiness fallback with explicit `is not None` check so `0.0 h` stockouts remain `0.0`.
  - Updated sort key to order by `(risk_level_order, hours_to_stockout, -stockout_prob)`.
- `services/engine/app/risk/stockout.py`:
  - Added tuple/list handling `(destination_station_id, fuel_type, expected_arrival_tick, quantity)` alongside dict/object support in `assess_station_fuel`.
- `services/api/tests/test_risk_api.py` & `services/engine/tests/test_stockout.py`:
  - Added `test_get_risk_maps_horizon_lead_prob_and_zero_hours` and `test_assess_handles_inflight_tuples_from_world_state`.

## Verification
- API pytest (`test_risk_api.py`): **7 passed, 0 failed**.
- Engine pytest (`test_stockout.py`): **7 passed, 0 failed**.
- Frontend Vitest (`risk_alerts.test.tsx`): **4 passed, 0 failed**.
- Live stress & in-flight test (`combined_crisis` + manual allocation + step):
  - `/api/risk` reported live elevated rows (`risk_level: "HIGH"`, `hours_to_stockout: 3.5`, `stockout_prob: 1.0`, `stockout_prob_horizon: 1.0`, `cover_hours: 4.52`, `drivers: ["high_horizon_stockout_prob (100%)", "demand_spike_active"]`).
  - Chrome Playwright `/risk` rendered `HIGH`, `3.5 h`, `100%`, and causal driver badges cleanly (`def006_risk_elevated_live.png`).
