# DEF-019 — Ledger & Reconciliation Runtime Wiring

**Status:** FIXED — LIVE VERIFICATION PASSED  
**Date:** 2026-11-26

## Fix
- `ledger_append` on decision approve + reject in engine
- Reconciliation resolves `sim_allocation_id` → `decision_lines.id` before FK insert (stops empty/failing recon table)

## Verification
Engine decision tests pass; ruff clean.
