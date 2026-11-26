# DEF-021 — Cross-Run History Mixing & Phase Badges

**Status:** FIXED — LIVE VERIFICATION PASSED  
**Date:** 2026-11-26

## Fix
- Station/depot history + demand queries scoped by active `sim_run_id`
- Sidebar phase badges (P8/P9/P10) removed

## Verification
API entities use run-scoped fetches; frontend suite green.
