# DEF-001 — Real Multi-User Auth, RBAC & Four-Eyes Approval (`>7,000 L`)

## Status: FIXED — LIVE VERIFICATION PASSED

## Original Reproduction & Root Cause
- Prior implementation accepted a single shared static bearer token (`change-me-operator`) or unauthenticated client calls, had no `/api/auth/login`, `/api/auth/me`, or `/api/auth/logout` session endpoints, lacked a UI sign-in/sign-out menu (`Header.tsx`), and allowed `operator` role access to supervisor-only endpoints (`POST /api/control/policy`).
- In addition, `Recommendations.tsx` immediately filtered approved decisions out of local UI state even when the backend returned `status: "AWAITING_SECOND_APPROVAL"` for decisions `> 7,000 L`, hiding the second-approval step in the UI.

## Changes Implemented
- `services/api/app/auth.py`: added PBKDF2-HMAC-SHA256 credential verification against `DEMO_USERS`/`API_AUTH_USERS_JSON`, signed HS256 JWT minting with unique `jti`, `exp`, `actor`, and `role`, Redis + in-memory `jti` revocation on logout, strict `require_viewer`, `require_operator`, and `require_supervisor` RBAC dependencies.
- `services/api/app/routers/auth.py` & `main.py`: implemented `POST /api/auth/login`, `GET /api/auth/me`, `POST /api/auth/logout`.
- `services/api/app/routers/decisions.py` & `notifications.py`: restricted `POST /api/control/policy` to `require_supervisor`; protected `POST /api/notifications/test` and `POST /api/notifications/dispatch` with `require_operator`.
- `frontend/src/api/client.ts`, `frontend/src/components/layout/UserMenu.tsx`, `Header.tsx`: added JWT storage & auth modal state, `login()`, `fetchMe()`, `logout()`, `UserMenu` badge/sign-in modal in header (zero prefilled credentials), and proper `ApiError` error message rendering.
- `frontend/src/pages/Recommendations.tsx`: retained `AWAITING_SECOND_APPROVAL` cards in UI with first-approver indicator (`Needs Supervisor Approval (1/2 — by <actor>)`) until supervisor sign-off completes.

## Regression Tests Added / Updated & Passing
- Backend pytest (`41 passed, 0 failed`):
  `services/api/tests/test_auth.py`, `test_control_api.py`, `test_decisions_api.py`, `test_risk_api.py`, `test_assistant_api.py`, `test_phase19_notifications.py`.
- Frontend Vitest (`15 test files, 63 passed, 0 failed` including `user_menu.test.tsx` and `client.test.ts`), `npm run lint` (0 TS errors), `npm run build`.

## Live Verification Results (Rebuilt `api` & `frontend` Containers)
- Login / Me / RBAC / Logout against `http://localhost:3000/api`:
  - `POST /api/auth/login` succeeded for `viewer`, `operator`, `supervisor` (`expires_in: 28800`), matching `GET /api/auth/me`.
  - `viewer` -> `POST /api/control/mode` -> `403 FORBIDDEN`.
  - `operator` -> `POST /api/control/policy` -> `403 FORBIDDEN`.
  - `supervisor` -> `POST /api/control/policy` -> `200 OK` (verified switch & restored `heuristic`).
  - `POST /api/auth/logout` -> `200 OK`, subsequent `GET /api/auth/me` -> `401 Token has been revoked`.
- Real Chrome Playwright UI + Four-Eyes verification (live decision `#1689`, `16,420 L`):
  - Blank login modal opened cleanly; wrong password showed `Invalid username or password`.
  - Signed in as `operator`, approved `#1689` -> UI retained card displaying `Needs Supervisor Approval (1/2 — by operator)`.
  - Duplicate approval by same `operator` blocked with `403 FOUR_EYES_SAME_ACTOR` alert: `Approval failed: Four-eyes policy violation: second approval must be by a different operator.`
  - Signed out, signed in as `supervisor`, approved `#1689` -> transitioned to `EXECUTED`, `approved_by: "operator, supervisor"` (verified on both `/decisions` UI and public Cloudflare tunnel `GET /api/decisions/1689`).
  - Screenshots: `def001_login_modal.png`, `def001_signed_in_operator.png`, `def001_four_eyes_1of2.png`, `def001_four_eyes_completed.png`.
