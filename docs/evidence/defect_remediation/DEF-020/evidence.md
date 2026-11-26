# DEF-020 — F-COVER Baseline & Auth on Compute Endpoints

**Status:** FIXED — LIVE VERIFICATION PASSED  
**Date:** 2026-11-26

## Fix
- `NOMINAL_DAILY_DEMAND` aligned to simulator profile totals (~40.2k/33.6k/17.6k)
- `/api/whatif` and `/api/assistant/ask` require authenticated viewer+
- Assistant tests: auth override + explicit 401 unauthenticated test

## Verification
API pytest 80 passed including assistant auth gate.
