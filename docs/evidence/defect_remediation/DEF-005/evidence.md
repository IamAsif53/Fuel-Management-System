# DEF-005 — Dhaka and Chattogram Risk Filters Remediation Evidence

## Status: FIXED — LIVE VERIFICATION PASSED

## Root Cause
`frontend/src/pages/Risk.tsx` sent `region_id=dhaka` and `region_id=chattogram`, whereas live simulator stations carry canonical `region_id` values `"region-dhaka"` and `"region-chattogram"`, and `services/api/app/routers/risk_alerts.py:51` compared `st_region != region_id` literally without normalization.

## Changes Implemented
- `frontend/src/pages/Risk.tsx`: Updated Region selector options to `"region-dhaka"` / `"region-chattogram"` (with clean display of region name in the table and `data-testid="risk-region-filter"`).
- `services/api/app/routers/risk_alerts.py`: Added `_normalize_region_id()` so `/api/risk?region_id=...` matches both canonical (`region-dhaka`) and shorthand (`dhaka`) query values.
- `services/api/tests/test_risk_api.py` & `frontend/src/test/risk_alerts.test.tsx`: Added regression tests verifying canonical & shorthand region queries and frontend dropdown filter dispatch.

## Verification
- API pytest (`test_risk_api.py`): **6 passed, 0 failed**.
- Frontend Vitest (`risk_alerts.test.tsx`): **4 passed, 0 failed**.
- Live API + Chrome Playwright on `/risk`:
  - `GET /api/risk?region_id=region-dhaka` & `?region_id=dhaka`: `200`, `6` Dhaka rows (`Mirpur`, `Tongi`).
  - `GET /api/risk?region_id=region-chattogram` & `?region_id=chattogram`: `200`, `6` Chattogram rows (`Karnaphuli`, `Cox's Bazar`).
  - Chrome Playwright UI region dropdown switched cleanly between `Dhaka` (`6` rows) and `Chattogram` (`6` rows).
  - Screenshots: `def005_risk_dhaka_filtered.png`, `def005_risk_chattogram_filtered.png`.
