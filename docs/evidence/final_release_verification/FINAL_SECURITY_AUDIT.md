# FSIP — Final Security Audit

**Date:** 2026-11-26  
**Scope:** Frontend bundles, auth model, CORS, secrets, control surfaces

## Product auth model (brief-aligned)

Participant brief does **not** require enterprise Sign-In. Current mode:

- Default identity: open demo **operator** (no bearer required)
- Human gate: **Approve / Reject** clicks (plus cognitive-forcing review when required)
- Optional JWT `/api/auth/login` remains for automated tests only — **no Sign-In UI**
- Multi-user four-eyes login intentionally removed

## Findings

| Check | Result | Evidence |
|---|---|---|
| Static `change-me-operator` in frontend nginx assets | **CLEAN** (0 matches) | `api/def003b_bundle.txt`, docker `grep` |
| Legacy localStorage token keys | Purged on load (`removeItem`) | `frontend/src/api/client.ts` |
| Revoked static bearer accepted as privileged identity | **No** — falls back to open demo operator | `GET /api/auth/me` with `Bearer change-me-operator` → operator open-mode |
| CORS evil origin with credentials | **Rejected** HTTP 400, no ACAO/ACC | `api/def012b_cors.txt` |
| CORS localhost:3000 | Allowed explicitly | live OPTIONS |
| Autopilot under stale/paused+chaos | **Blocked** HTTP 409 `STALE_DATA` | live probe |
| Admin `/admin/*` as production engine path | Not used for production allocations | architecture preserved |
| Compute endpoints | Open-mode operator; validation still 422 | DEF-007/020 |
| Secrets in reports | None printed | this audit |

## Residual risk (accepted for hackathon demo)

- Open operator mode means any browser client on the demo network can approve/reject. Acceptable for local/demo per brief; **not** production multi-tenant security.
- Optional JWT secret remains server-side only.

## Verdict

**PASS for demonstration security gates** (DEF-003b, DEF-012b, autopilot stale gate, no shipped frontend secrets).  
Enterprise multi-user RBAC / four-eyes is **out of scope** by explicit product decision.
