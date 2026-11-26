# FSIP — Temporary Public Remote Access Verification Report

**Date:** 2026-11-26  
**Environment:** Windows 11 + Docker Desktop + Cloudflare Tunnel (cloudflared v2026.11.2)  
**Target:** FSIP Operational Dashboard Frontend (`http://localhost:3000`)

---

## 1. Public Access Summary

| Property | Details |
| :--- | :--- |
| **Public HTTPS URL** | **`https://cooperation-camcorder-obtain-resolution.trycloudflare.com`** |
| **Target Origin** | `http://localhost:3000` (Frontend Nginx Entry Point) |
| **Process ID (PID)** | `21032` (`cloudflared.exe`) |
| **Executable Path** | `C:\Program Files (x86)\cloudflared\cloudflared.exe` |
| **Protocol** | QUIC over Argo Tunnel (Cloudflare Edge `dac07`) |
| **Status** | **ACTIVE & VERIFIED** |

---

## 2. Architecture & Ingress Flow

```
[Remote User Browser]
        │
        ▼ (HTTPS)
[Cloudflare Edge Network]
        │ (QUIC Tunnel)
[cloudflared.exe (PID 21032)]
        │ (HTTP localhost:3000)
[FSIP Frontend Container (nginx:alpine)]
        ├── Static SPA Routes (/*) ──────────► [React SPA App (index.html)]
        ├── API Reverse Proxy (/api/*) ──────► [Docker Internal: api:8080]
        └── Static Emergency (/breakglass/*) ─► [/var/www/breakglass]
```

---

## 3. Verification & Testing Evidence

### 3.1 Frontend Web UI Reachability
- **Request:** `GET https://cooperation-camcorder-obtain-resolution.trycloudflare.com`
- **HTTP Status:** `200 OK`
- **Content-Type:** `text/html`
- **Payload Verification:** Successfully retrieved HTML containing `<div id="root"></div>` (SPA container).

### 3.2 Backend API Reverse Proxy Reachability
- **Request:** `GET https://cooperation-camcorder-obtain-resolution.trycloudflare.com/api/state`
- **HTTP Status:** `200 OK`
- **Content-Type:** `application/json`
- **Response Validation:** Returned live system telemetry (`tick: 7`, `operating_mode: "advisory"`, `stations: 4`).
- **Request:** `GET https://cooperation-camcorder-obtain-resolution.trycloudflare.com/api/recommendations`
- **HTTP Status:** `200 OK`

---

## 4. Security Exposure Audit

| Check | Target / Endpoint | Result | Security Compliance |
| :--- | :--- | :--- | :--- |
| **Internal Port Isolation** | Host ports 18080 (API), 8000 (Simulator), 8100 (Engine), 15432 (TimescaleDB), 16379 (Redis), 9090 (Prometheus), 13001 (Grafana) | **NOT EXPOSED** | PASSED (Only port 3000 is tunneled) |
| **Direct Simulator Admin Protection** | `GET /admin/` | Returns `index.html` (SPA fallback) | PASSED (Simulator `/admin/*` unreachable) |
| **Direct Simulator Allocation Path** | `GET /v1/allocations` | Returns `index.html` (SPA fallback) | PASSED (Internal `/v1/*` unreachable) |
| **Mutating Endpoint Protection** | `POST /api/control/sim/run` | `401 Unauthorized` | PASSED (Requires Bearer token) |
| **Operating Mode Mutation Protection** | `POST /api/control/mode` | `401 Unauthorized` | PASSED (Requires Bearer token) |

---

## 5. Known Limitations & Operational Considerations

1. **Server-Sent Events (SSE) Buffering:**
   - The platform uses SSE at `/api/stream` for real-time live telemetry streaming.
   - Cloudflare Quick Tunnel edge proxies may periodically buffer, throttle, or disconnect persistent SSE streams after idle periods.
   - The frontend's built-in polling and reconnection fallback automatically preserves dashboard functionality if an SSE disconnect occurs.

2. **Access Control Scope:**
   - Cloudflare Quick Tunnel creates an unauthenticated public HTTPS URL (`*.trycloudflare.com`).
   - Domain/email allowlists (Cloudflare Zero Trust Access) require a registered domain and Cloudflare account, which is not available in ad-hoc quick tunnel mode.
   - Administrative and mutating actions remain fully protected by backend JWT/Bearer token authentication.

---

## 6. Management Instructions

### Check Tunnel Status
```powershell
Get-Process -Name "cloudflared"
```

### Terminate Public Access
```powershell
taskkill /IM cloudflared.exe /F
```

### Restart Quick Tunnel (If Stopped)
```powershell
& "C:\Program Files (x86)\cloudflared\cloudflared.exe" tunnel --url http://localhost:3000
```
*(Note: A new random `*.trycloudflare.com` URL will be generated upon each restart).*
