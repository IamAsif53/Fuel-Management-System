# DEF-012b — CORS Wildcard With Credentials Remediation Evidence

## Status: FIXED — LIVE VERIFICATION PASSED

## Root Cause
`services/api/app/main.py:85-91` configured `CORSMiddleware` with `allow_origins=["*"]` alongside `allow_credentials=True`, causing arbitrary untrusted origins (e.g., `https://evil.example`) to be reflected back in `Access-Control-Allow-Origin` with `Access-Control-Allow-Credentials: true`.

## Changes Implemented
- `services/api/app/config.py`: Added `cors_origins` (`CORS_ORIGINS` / `API_CORS_ORIGINS`) defaulting to explicit local frontend origins (`http://localhost:3000,http://127.0.0.1:3000,http://localhost:5173,http://127.0.0.1:5173`).
- `services/api/app/main.py`: Replaced `allow_origins=["*"]` + `allow_credentials=True` with explicit `_cors_origins` (stripping any wildcard `*`), explicit methods/headers, and `allow_credentials=False` (bearer JWT auth uses `Authorization` headers, not ambient cookies).
- `services/api/tests/test_auth.py`: Added `test_cors_rejects_untrusted_origin_with_credentials`.

## Verification (Unit + Rebuilt `api` Live Stack)
- `pytest services/api/tests/test_auth.py`: **5 passed, 0 failed**.
- Live `OPTIONS http://localhost:3000/api/state`:
  - `Origin: https://evil.example` -> `400 Bad Request`, `access-control-allow-origin: None`, `access-control-allow-credentials: None`.
  - `Origin: http://localhost:3000` -> `200 OK`, `access-control-allow-origin: http://localhost:3000`, `access-control-allow-credentials: None`.
