# DEF-013 — Routes, Deep Links, DEMO_CONTROLS Gate

**Status:** FIXED — LIVE VERIFICATION PASSED  
**Date:** 2026-11-26

## Fix
- `App.tsx`: `/system` → `/health`; `/decisions/:decisionId`
- `Network.tsx`: honors `?station=` / `?depot=` (tab switch + scroll + highlight)
- `Decisions.tsx`: expands/scrolls focused decision id; ALL-runs scope for deep link
- `Sidebar.tsx`: hides Control when `/api/control/status` reports `demo_controls_enabled=false`; removed phase badges (also DEF-021)

## Verification
- Network unit tests (MemoryRouter) pass
- Frontend suite 65/65
