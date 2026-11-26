# DEF-016 — Truthful System Health

**Status:** FIXED — LIVE VERIFICATION PASSED  
**Date:** 2026-11-26

## Fix
- Notifier reports `Unconfigured` unless SMTP/webhook env is set
- Decision Freshness derived from engine health timestamps (not hardcoded 0.998)
- Grafana links use host `:13001` (compose default), not dead `:3001`

## Verification
API system router updated; frontend SystemHealth links updated; suite green.
