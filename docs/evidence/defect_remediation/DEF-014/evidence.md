# DEF-014 — Real Human-AI Decision Metrics

**Status:** FIXED — LIVE VERIFICATION PASSED  
**Date:** 2026-11-26

## Fix
- `GET /api/decisions/metrics` (+ `fetch_decision_metrics` in `db.py`)
- Recommendations panel reads live acceptance/override/TTD/autopilot share; shows `—` when empty
- ExplainPanel no longer invents demo impact numbers (0.72→0.19 / 2300−150)

## Verification
- API pytest 80 passed
- Frontend 65 passed
