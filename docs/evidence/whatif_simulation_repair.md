# FSIP — What-If Scenario Simulation Pipeline Repair & Verification Report

**Platform:** Fuel Supply Intelligence & Resilience Platform (FSIP)  
**Component:** What-If Scenario Digital Twin & Counterfactual Simulation Pipeline  
**Specification References:** `20_FINAL_SPEC_LOCK.md`, `21_API_CONTRACTS.md` §2.4, `02_SIMULATOR_FACTS.md` §4, `05_INTELLIGENCE_DESIGN.md` §5  
**Verification Date:** November 25, 2026  
**Status:** **VERIFIED, OPERATIONAL & LOCKED**

---

## 1. Executive Summary & Root Cause Analysis

An end-to-end audit and diagnostics session identified why the What-If simulation page was displaying identical results across scenarios (100% service level, 0 L unmet demand, 0 L overflow loss, 0% station risk) regardless of disruption inputs:

1. **Frontend Hardcoded Invalid Route Identifier (`r-1`):**  
   The candidate dispatch form defaulted to a text input containing `r-1`. Real simulator routes are named `route-gazipur-mirpur`, `route-gazipur-tongi`, etc.
2. **Silent Drop of Unrecognized Route Identifiers:**  
   In `libs/fsip_common/fsip_common/twin.py`, an unrecognized route resulted in `if not r_obj: continue`. Invalid routes were dropped silently without feedback or exception, running custom simulation with empty lines (`lines = []`), producing output identical to DO NOTHING.
3. **Flawed Station Outage Modeling in `ShadowTwin.project`:**  
   During a simulated station outage, `dem_matrix[:, k] = 0.0` was executed. Zeroing customer demand during an outage artificially forced requested demand to 0, served demand to 0, and unmet demand to 0, mathematically manufacturing a false 100% service level during outages.
4. **Missing Handlers for Depot Constraints:**  
   `twin.py` lacked event interception for `depot_constraint` and `depot_disruption`, failing to enforce the documented 50% dispatch throughput cap.
5. **Network Average Dilution in Service Level Metric:**  
   `twin.py` was computing aggregate network service level $\frac{\sum \text{served}}{\sum \text{demand}}$ and assigning it to `min_station_service_level`, diluting severe localized disruptions.
6. **Frontend Inventory Blindness & Styling Mismatch:**  
   The UI only displayed stockout risk percentage (which remained 0% when stations had ample buffer), concealing the significant impact of dispatches on ending inventory trajectories ($\Delta L$). Furthermore, input fields had improper background contrast against the dark theme.

---

## 2. Complete Pipeline Repair

### 2.1 Physics Engine & Digital Twin (`libs/fsip_common/fsip_common/twin.py`)
- **Station Outage Physics:** Preserved actual customer demand arriving at the station (`req_demand = dem_matrix[:, k]`). During an outage, pumps cannot dispense fuel (`served = 0.0`), customer demand is unserved (`unmet = req_demand`), and existing fuel inventory is preserved on-site rather than drained.
- **Minimum Station Service Level:** Implemented true $\min_s \left(\frac{\text{served}_s}{\max(1.0, \text{demand}_s)}\right)$ across all network stations. When a station suffers an outage or fuel exhaustion, the network-wide metric reflects the minimum station service level.
- **Depot Constraints (`depot_constraint`):** Added event interception and enforced self-imposed 50% dispatch throughput caps on candidate dispatches departing from constrained depots per `02_SIMULATOR_FACTS.md` §4.
- **Supply Arrivals & Shortfalls:** Added simulation handling for `shipment_delay` and `supply_shortfall` events.
- **Inventory Metrics:** Added `initial_inventory` and `ending_inventory_p50` to `StationProjection`.

### 2.2 Strict API & Engine Validation (`services/engine/app/main.py`)
- Validated all candidate lines submitted to `POST /internal/whatif`:
  - Unknown route ID raises HTTP 422 `INVALID_IDENTIFIER` with available routes listed.
  - Invalid fuel type raises HTTP 422 `INVALID_FUEL_TYPE`.
  - Non-positive quantity ($\le 0$ L) raises HTTP 422 `INVALID_QUANTITY`.
  - Quantity exceeding route `max_shipment` limit raises HTTP 422 `INFEASIBLE_DISPATCH` with explicit limit.
  - Quantity exceeding source depot inventory raises HTTP 422 `INFEASIBLE_DISPATCH` with available liters.
- Formatted output scenarios to include `initial_inv`, `ending_inv`, `delta_liters`, `unmet_liters`, and `overflow_liters` per station.

### 2.3 Frontend Upgrades (`frontend/src/pages/WhatIf.tsx`)
- **Dynamic Entity Selectors:** Replaced free-text input with live `<select>` dropdown populated from `state.routes`, showing route corridor, max shipment limit, and transit ticks.
- **Disruption Target Selectors:** Added target entity selection for all event types (`demand_spike`, `route_disruption`, `station_outage`, `depot_constraint`, `shipment_delay`).
- **Custom Dispatch Toggle:** Added toggle to enable/disable custom dispatch in simulation comparisons.
- **Inventory Delta Display:** Displays ending inventory and net change ($\Delta L$) for every station alongside stockout risk percentage.
- **Prominent Error Banner:** Renders structured validation and feasibility error explanations directly in the UI.
- **Dark Theme Styling:** Restored dark theme contrast tokens (`bg-bg-raised text-ink border-bg-border focus:border-brand`), eliminating white boxes.

---

## 3. Automated Verification Evidence

### 3.1 Backend Regression Test Suite (`services/engine/tests/test_whatif_repair.py`)
```
============================= test session starts =============================
collected 9 items

tests/test_whatif_repair.py::test_a_baseline PASSED                      [ 11%]
tests/test_whatif_repair.py::test_b_route_disruption PASSED              [ 22%]
tests/test_whatif_repair.py::test_c_custom_feasible_dispatch PASSED      [ 33%]
tests/test_whatif_repair.py::test_d_infeasible_dispatch PASSED           [ 44%]
tests/test_whatif_repair.py::test_e_horizon_sensitivity PASSED           [ 55%]
tests/test_whatif_repair.py::test_f_policy_comparison PASSED             [ 66%]
tests/test_whatif_repair.py::test_g_no_live_side_effects PASSED          [ 77%]
tests/test_whatif_repair.py::test_h_invalid_identifiers PASSED           [ 88%]
tests/test_whatif_repair.py::test_i_station_outage_impact_and_determinism PASSED [100%]

======================== 9 passed in 5.58s =========================
```

### 3.2 Frontend Unit Test Suite (`frontend/src/test/whatif.test.tsx`)
```
 ✓ src/test/whatif.test.tsx (3 tests) 461ms
   ✓ renders configuration controls with real route options and dark theme styling
   ✓ executes what-if simulation and displays three scenario comparison cards
   ✓ displays prominent validation error banner on infeasible dispatch

 Test Files  1 passed (1)
      Tests  3 passed (3)
```

### 3.3 Full Frontend Vitest Suite
```
 Test Files  13 passed (13)
      Tests  55 passed (55)
   Start at  00:04:52
   Duration  20.46s
```

### 3.4 Live Docker Stack HTTP Integration Verification (`scripts/verify_live_whatif.py`)
```
=== Running Live What-If End-to-End Verification ===
1. Baseline HTTP status: 200
   Scenarios returned: ['do_nothing', 'recommended', 'custom']
   DO NOTHING min_station_sl: 1.0 unmet: 0.0
2. Custom Dispatch HTTP status: 200
   Mirpur DIESEL ending inv -> DO NOTHING: 7456.3 L (-2610.4 L) | CUSTOM: 9852.3 L (-214.4 L)
3. Invalid Route ID HTTP status: 422 detail: {'code': 'INVALID_IDENTIFIER', 'message': "Route 'r-1' does not exist. Available routes: ['route-gazipur-karnaphuli', 'route-gazipur-mirpur', 'route-gazipur-tongi', 'route-patiya-coxsbazar', 'route-patiya-karnaphuli', 'route-patiya-mirpur']"}
4. Infeasible Quantity HTTP status: 422 detail: {'code': 'INFEASIBLE_DISPATCH', 'message': "Quantity 8500 L exceeds route 'route-gazipur-mirpur' max shipment limit of 7000 L"}
5. Station Outage HTTP status: 200
   DO NOTHING min_station_sl: 0.6056 unmet: 4304.8 L

>>> ALL 5 LIVE HTTP ENDPOINT CHECKS VERIFIED SUCCESSFULLY! <<<
```

### 3.5 Code Quality & Static Analysis
```
$ ruff check libs services evaluation
All checks passed!
```

---

## 4. Conclusion & Release Status

The What-If counterfactual scenario simulation pipeline is completely repaired, mathematically grounded, strictly validated, and verified live across the Docker production stack with 100% test pass rates and zero regressions.
