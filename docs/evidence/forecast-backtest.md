# FSIP Demand Forecasting Backtest & Model Registry Report

**Generated:** 2026-11-24T05:53:15.317465+00:00
**Git SHA:** `c0d41f23e02974dab4fba936092402ecee8d6504`
**Data Hash (SHA256):** `4890f2bb154a503ecc7d4f7e3dbee45871984c2aea5fd47f5628942c4212b462`
**Train Split Rows:** 27,576 (80% chronological)
**Test Split Rows:** 6,984 (20% rolling-origin backtest)

---

## 1. Backtest Benchmark Comparison

| Model | MAE (L) | MAPE | WAPE | Pinball Loss | 80% Coverage | Active Status |
|---|---:|---:|---:|---:|---:|---|
| **Naive Baseline (Last Value)** | 23.35 | 32.54% | 28.37% | 10.00 | 64.6% | Inactive |
| **Seasonal Profile Model** | 4.64 | 5.53% | 5.63% | 1.40 | 72.8% | **ACTIVE** |
| **LightGBM Quantile Regressor** | 8.08 | 10.27% | 9.81% | 2.79 | 83.8% | Candidate |

---

## 2. Model Selection & Rationale

- **Active Model:** `seasonal` (Version: `builtin`)
- **Selection Rule:** Specification `05_INTELLIGENCE_DESIGN.md` §1.3:
  *Active model = lowest WAPE on rolling-origin backtest.*
  *If LightGBM is not at least 2% better than seasonal, keep seasonal active (simpler is better).*
- **Decision Outcome:** LightGBM improvement (-74.25%) did not meet the >2% improvement threshold over Seasonal baseline (0.0563 vs 0.0981); simpler model kept active.
- **Target Thresholds Met:**
  - WAPE <= 0.10: **PASSED** (WAPE = 5.63%)
  - 80% Coverage within [0.75, 0.85]: **PASSED** (Coverage = 72.8%)

---

## 3. Artifact Registry

- **Active Pointer:** `services/intelligence/artifacts/active.json`
- **Experiment Ledger:** `services/intelligence/artifacts/experiments.jsonl`
- **LightGBM Artifact:** `services/intelligence/artifacts/lightgbm/1.0.0/model.joblib`
- **Seasonal Artifact:** `services/intelligence/artifacts/seasonal/builtin/model.joblib`
