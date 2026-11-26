# DEF-003b — Exposed Static Operator Token Reproduction & Remediation Evidence

## Status: FIXED — LIVE VERIFICATION PASSED

## Original Reproduction (Before Fix)
- Command: `docker exec fsip-frontend sh -c "grep -rn 'change-me-operator' /usr/share/nginx/html/assets/"`
- Observed Output: `index-CpEOeSFy.js` contained plaintext `"change-me-operator"` (compiled from `frontend/src/api/client.ts:20-21`).
- Command: `POST http://localhost:3000/api/control/sim/pause` with `Authorization: Bearer change-me-operator`
- Observed Output: `200 OK` (full operator access granted using publicly readable client token).

## Root Cause
1. `frontend/src/api/client.ts` defaulted `operatorToken` to literal `"change-me-operator"` (`localStorage` fallback).
2. `services/api/app/auth.py` accepted static `settings.api_operator_token` (`"change-me-operator"`), bypassing signed JWT verification.
3. `.env` configured `API_OPERATOR_TOKEN=change-me-operator` and `JWT_SECRET=change-me-jwt-secret`, and `services/api/app/config.py` hardcoded `api_operator_token = "change-me-operator"`.

## Changes Implemented
- Removed `API_OPERATOR_TOKEN=change-me-operator` and rotated `JWT_SECRET` in `.env` and updated `.env.example`.
- `services/api/app/config.py`: replaced `"change-me-operator"` default with `""` and wired `api_jwt_secret` to `AliasChoices("JWT_SECRET", "API_JWT_SECRET")` + `demo_users` / `auth_users_json`.
- `services/api/app/auth.py`: removed static token acceptance; explicitly rejects `REVOKED_STATIC_TOKENS = {"change-me-operator"}` with `401 UNAUTHORIZED`; enforced HS256 JWT + `jti` revocation check.
- `frontend/src/api/client.ts` & `frontend/src/components/layout/UserMenu.tsx`: removed default `"change-me-operator"` token; no credentials or secrets exist in frontend source or bundle; unauthenticated users sign in via blank credentials form (`POST /api/auth/login`).
- `scripts/verify_autopilot.py`, `scripts/verify_control_panel.py`, `scripts/verify_live_whatif.py`: replaced `"change-me-operator"` with `/api/auth/login`.

## Post-Fix Verification (Tests + Live Stack)
- Frontend build scan (`frontend/dist`): `dist_secret_matches=0`.
- Live container nginx scan:
  `docker exec fsip-frontend sh -c "grep -rn 'change-me-operator' /usr/share/nginx/html || echo FRONTEND_CONTAINER_CLEAN"` -> `FRONTEND_CONTAINER_CLEAN`.
- Playwright live browser bundle scan (`http://localhost:3000/assets/index-C6JHG3QP.js`): `hasLeak: false`.
- Live HTTP with `Authorization: Bearer change-me-operator`:
  - `GET /api/auth/me` -> `401 {"detail":{"code":"UNAUTHORIZED","message":"Static credential has been revoked; sign in via /api/auth/login"}}`
  - `POST /api/control/mode` -> `401 UNAUTHORIZED`
  - `POST /api/control/sim/pause` -> `401 UNAUTHORIZED`
  - `POST /api/decisions/1/approve` -> `401 UNAUTHORIZED`
- Unauthenticated write attempts (`POST /api/control/mode`, `/api/control/sim/pause`, `/api/notifications/test`) -> `401 Missing bearer authentication token`.
- Screenshot: `def003b_signed_out_clean.png`.
