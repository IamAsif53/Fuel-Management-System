# DEF-007b — Control Panel Presets & Chaos Parameter Mapping Evidence

## Status: FIXED — LIVE VERIFICATION PASSED

## Root Cause
`services/api/app/routers/control.py` sent mismatched simulator parameter keys (`region: "dhaka"` instead of `region_ids: ["region-dhaka"]`, `depot_id` instead of `depot_ids`, `multiplier: 0.5` on `supply_shortfall` instead of `factor: 0.5`, and `latency_ms: 1500` instead of `delay_ms: 1500`), causing Dhaka demand spikes to hit Chattogram stations too, depot constraints/delays to fail target filtering, and latency faults to default to 500ms instead of 1500ms.

## Changes Implemented
- `services/api/app/routers/control.py`:
  - `demand_spike_dhaka` & `combined_crisis`: updated `demand_spike` parameters to `{"multiplier": 1.8, "region_ids": ["region-dhaka"]}`.
  - `depot_constraint_patiya`: updated `depot_constraint` parameters to `{"depot_ids": ["depot-patiya"]}`.
  - `shipment_delay_gazipur` & `combined_crisis`: updated `shipment_delay` parameters to `{"depot_ids": ["depot-gazipur"], "delay_ticks": 8}`.
  - `supply_shortfall`: updated `factor` to `0.5`.
  - `control_inject_chaos_fault`: defaulted `latency` fault to `{"delay_ms": 1500}` and normalized legacy `latency_ms` alias -> `delay_ms`.
- `services/api/tests/test_control_presets_contract.py`: added contract regression tests covering all corrected simulator parameter keys.

## Verification
- API pytest (`test_control_api.py` + `test_control_presets_contract.py`): **11 passed, 0 failed**.
- Live Docker verification against `http://localhost:3000/api` + simulator `http://localhost:8000`:
  - `demand_spike_dhaka` + 1 step -> `station-mirpur: 1.8`, `station-tongi: 1.8`, `station-karnaphuli: 1.0`, `station-coxsbazar: 1.0`.
  - `depot_constraint_patiya` + 1 step -> `depot-patiya: CONSTRAINED`, `depot-gazipur: OPEN`.
  - `shipment_delay_gazipur` -> `{'depot_ids': ['depot-gazipur'], 'delay_ticks': 8}`.
  - `supply_shortfall` -> `{'factor': 0.5}`.
  - `chaos/latency` -> `{'delay_ms': 1500}`, measured `/v1/instance` latency `1.52s`.
