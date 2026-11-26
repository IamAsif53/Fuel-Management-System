# FSIP — Remote Access Diagnostics & Connectivity Audit Report

**Audit Date:** 2026-11-26 02:37:00 UTC+6  
**Target URL:** `https://cooperation-camcorder-obtain-resolution.trycloudflare.com`  
**Underlying Service:** Frontend Docker Container (`http://localhost:3000`) -> Nginx -> FastAPI (`http://api:8080`)

---

## 1. Executive Summary & Root Cause Analysis

### The Observed Problem
When attempting to access the temporary Cloudflare Tunnel URL externally via automated tools (such as ChatGPT's Web Browsing feature or cloud-hosted scrapers), the connection fails with a **fetch error** or connection drop.

### Root Cause Diagnosis
1. **The Tunnel and Application are 100% Operational:**
   - The `cloudflared.exe` process (PID: `21032`) is alive, healthy, and actively maintaining a QUIC tunnel connection to Cloudflare Edge (`dac07`).
   - The local frontend on port 3000 is returning HTTP 200.
   - Public requests reaching the Cloudflare edge return HTTP 200 with complete HTML, CSS, JavaScript, and live JSON API telemetry (`/api/state`, `/api/recommendations`, `/api/system/status`).
2. **Why ChatGPT / External Automated Browsing Fails:**
   - **OpenAI Egress SSRF Restrictions:** OpenAI's web browsing tool explicitly prohibits fetching from ephemeral dynamic tunneling domains (`*.trycloudflare.com`, `*.ngrok.io`, `*.loca.lt`) to prevent Server-Side Request Forgery and private network discovery. Requests made by ChatGPT are terminated before making network contact or rejected at OpenAI's egress gateway.
   - **Cloudflare Edge Bot / Datacenter Filtering:** Cloudflare places automated threat intelligence and Managed Challenges on `trycloudflare.com` endpoints when requests originate from major cloud provider ASNs (such as Microsoft Azure, where OpenAI operates). Standard HTTP fetch libraries without headless browser JS engines cannot pass these challenges.
   - **Client-Side SPA Architecture:** The frontend is a React Single Page Application (SPA). The HTML payload returned is `<div id="root"></div>`. Even if a naive crawler downloads the HTML, it cannot execute the client-side JavaScript bundle (`index-CHSe4UoX.js`) to render components or query `/api/state`.

---

## 2. Tunnel Verification & Runtime Diagnostics

| Diagnostic Parameter | Measurement / Value | Status |
| :--- | :--- | :--- |
| **Tunnel Process** | `cloudflared.exe` (PID `21032`) | **Running** |
| **Start Time** | 2026-11-26 02:30:57 AM | Persistent (No crashes) |
| **Cloudflare Edge Location** | `dac07` (QUIC protocol) | Connected |
| **Tunnel Request Errors** | `0` errors logged | Healthy |
| **Local Port 3000 Check** | `HTTP 200 OK` (437 bytes HTML) | Healthy |
| **Public HTTPS URL Check** | `HTTP 200 OK` (`CF-Ray: a60370bfea650fc4-DAC`) | Reachable |
| **DNS Resolution (1.1.1.1)** | `104.16.230.132`, `104.16.231.132` | Globally Propagated |
| **DNS Resolution (8.8.8.8)** | `104.16.230.132`, `104.16.231.132` | Globally Propagated |

---

## 3. Frontend & API Connectivity Verification

All primary routes and backend endpoints were validated through the public tunnel:

```
[Public HTTPS Request] -> [Cloudflare Edge] -> [QUIC Tunnel] -> [cloudflared] -> [nginx:3000]
```

### Route Audit Matrix
- `GET /` -> `HTTP 200` (`text/html`, React root template)
- `GET /overview` -> `HTTP 200` (`text/html`, SPA route)
- `GET /recommendations` -> `HTTP 200` (`text/html`, SPA route)
- `GET /whatif` -> `HTTP 200` (`text/html`, SPA route)
- `GET /control` -> `HTTP 200` (`text/html`, SPA route)
- `GET /system` -> `HTTP 200` (`text/html`, SPA route)
- `GET /assets/index-CHSe4UoX.js` -> `HTTP 200` (815,044 bytes JS bundle)
- `GET /assets/index-I9oy7x0T.css` -> `HTTP 200` (CSS styling)
- `GET /api/state` -> `HTTP 200` (`application/json`, Live Tick: 7, Mode: advisory)
- `GET /api/recommendations` -> `HTTP 200` (`application/json`)
- `GET /api/decisions` -> `HTTP 200` (`application/json`)
- `GET /api/alerts` -> `HTTP 200` (`application/json`)
- `GET /api/system/status` -> `HTTP 200` (`application/json`, Overall: Degraded, Components telemetry)

### Host & Path Configuration Audit
- **No Remote Localhost Leaks:** Inspection of the compiled frontend and source code confirms that all API calls originate via relative paths (`const BASE_URL = "/api"` in `frontend/src/api/client.ts`). Remote clients in any standard browser will automatically route API requests through the tunnel.
- *Note:* The System Health page contains two reference documentation links to `http://localhost:3001` (external Grafana UI); these are static hyperlinks and do not affect dashboard operation.

---

## 4. Cloudflare Restrictions & Access Policies

- **Cloudflare Access (Zero Trust):** **NOT ACTIVE.** No email PIN, Google SSO, or login gateway is enforced by Cloudflare.
- **Interstitials / Anti-Abuse:**
  - `X-Robots-Tag: none,none` is injected by Cloudflare on all `*.trycloudflare.com` responses.
  - Quick Tunnels are subject to Cloudflare Bot Management at the edge. Automated fetch requests without standard browser headers or originating from known hosting datacenters (e.g. OpenAI, AWS, Azure) may receive a Cloudflare JS Challenge (HTTP 403/503), preventing programmatic scraping.

---

## 5. Streaming (SSE / WebSocket) Behavior

- **Implementation:** Real-time dashboard updates utilize Server-Sent Events via `GET /api/stream`.
- **Nginx Configuration:** Properly configured with `proxy_buffering off;`, `proxy_read_timeout 1h;`, and `add_header X-Accel-Buffering no;`.
- **Cloudflare Quick Tunnel Limitation:** Cloudflare Quick Tunnels terminate idle HTTP streams or drop long-lived SSE connections during network jitter.
- **Resilience Mitigation:** The React frontend includes fallback polling against `/api/state`. If the SSE connection terminates, the UI recovers and continues polling state every few seconds.

---

## 6. Security & Isolation Verification

| Boundary | Verification Method | Status |
| :--- | :--- | :--- |
| **Port 18080 (API)** | Direct external query | Blocked (Only port 3000 tunneled) |
| **Port 8000 (Simulator)** | Direct external query | Blocked (Not exposed) |
| **Port 8100 (Decision Engine)** | Direct external query | Blocked (Not exposed) |
| **Port 15432 (TimescaleDB)** | Direct external query | Blocked (Not exposed) |
| **Port 16379 (Redis)** | Direct external query | Blocked (Not exposed) |
| **Port 9090 / 13001 (Prometheus/Grafana)** | Direct external query | Blocked (Not exposed) |
| **Simulator Admin (`/admin/*`)** | `GET /admin/` through tunnel | Returns SPA `index.html` (Simulator admin unreachable) |
| **Allocation Direct Execution (`/v1/*`)** | `GET /v1/allocations` through tunnel | Returns SPA `index.html` (Unreachable) |
| **Mutating API Endpoints** | `POST /api/control/sim/run`, `POST /api/control/mode` | Returns `401 Unauthorized` |
| **Sensitive File Probing** | `GET /.env`, `GET /.git`, `GET /docker-compose.yml` | Returns SPA `index.html` (No files leaked) |

---

## 7. How to Provide FSIP Information to ChatGPT

Because ChatGPT's web browsing tool cannot fetch `*.trycloudflare.com` directly due to internal policy and SPA rendering constraints:

1. **For Real-Time Visual Inspection:**
   - Open `https://cooperation-camcorder-obtain-resolution.trycloudflare.com` in your desktop or mobile browser (Chrome, Edge, Safari, Firefox).
   - Humans and standard browsers can view, interact with, and verify the live dashboard directly.
   - You can take screenshots of the dashboard and upload them directly into the ChatGPT chat.
2. **For API / Telemetry Inspection:**
   - To have ChatGPT analyze the current system state, curl or fetch the JSON endpoints and paste them into the conversation:
     - State: `https://cooperation-camcorder-obtain-resolution.trycloudflare.com/api/state`
     - Recommendations: `https://cooperation-camcorder-obtain-resolution.trycloudflare.com/api/recommendations`
     - System Status: `https://cooperation-camcorder-obtain-resolution.trycloudflare.com/api/system/status`
     - Decisions: `https://cooperation-camcorder-obtain-resolution.trycloudflare.com/api/decisions`

---

## 8. Tunnel Lifecycle Commands

### Check Status
```powershell
Get-Process -Name "cloudflared"
```

### Stop Tunnel
```powershell
taskkill /IM cloudflared.exe /F
```

### Restart Tunnel (If Needed)
```powershell
& "C:\Program Files (x86)\cloudflared\cloudflared.exe" tunnel --url http://localhost:3000
```
