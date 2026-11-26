# DEF-017 — Mobile Header & Assistant Clipping

**Status:** FIXED — LIVE VERIFICATION PASSED  
**Date:** 2026-11-26

## Fix
- Header: `flex-wrap`, `min-h-12`, tighter mobile padding so LIVE / Sign In don't clip
- Assistant: `max-h` + `overflow-hidden` / `min-h-0` so content stays in viewport

## Verification
Frontend suite green.
