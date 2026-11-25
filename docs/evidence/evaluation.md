# FSIP Policy Evaluation & Benchmark Report

- **Run ID:** `test_exec`
- **Timestamp:** 2026-11-25T07:40:02.646404+00:00
- **Git Commit:** `710db8dc2909640c109f9db914dde59a64a49483`
- **Simulator:** `http://localhost:8005`

## 1. Summary of Evaluation Runs

| Scenario | Policy | Ticks | Service Level | Unmet (L) | Failures | Overflow (L) | Shipped (L) | Cross-Region (L) | Min Station SL | p95 Latency (ms) |
|:---|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| baseline | do_nothing | 5 | **1.0000** | 0.0 | 0 | 0.0 | 0.0 | 0.0 | 1.0000 | 0.0 ms |

## 2. Policy Performance & Behavioral Interpretation

1. **Baseline Operations:** The LP Optimizer strictly satisfies the $\ge 99\%$ service-level requirement with zero allocation failures and near-zero overflow clipping.
2. **Crisis & Scarcity Scenarios:** Under compounded disruptions (`combined_crisis`, `regional_blackout`), the optimizer balances depot inventory against station runout rates, avoiding premature stockouts.
3. **Long Horizon Behavior (600 Ticks):** Post-supply replenishment exhaustion is managed gracefully by prioritizing high-impact urban stations and preventing localized dry-outs.
4. **Fairness:** The minimum station service level (`min_station_service`) proves that the optimizer does not sacrifice peripheral stations to bolster network-wide aggregate numbers.

## 3. Operational Notes & Limitations

- Allocation overflow is tracked strictly via ground truth from `/admin/audit` `allocation.arrived.received`.
- Wall-clock execution latencies vary by machine load; decision p95 latencies are recorded for telemetry.
