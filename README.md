# Fuel Supply Intelligence & Resilience Platform (FSIP)

> **BUP CSE FEST 2026 — Final Hackathon** (Powered by **Poridhi.io**)  
> Autonomous, Closed-Loop Fuel Supply Intelligence, Risk Mitigation & Dispatch Optimization Platform

[![CI Pipeline](https://github.com/IamAsif53/Fuel-Management-System/actions/workflows/ci.yml/badge.svg)](https://github.com/IamAsif53/Fuel-Management-System/actions)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/)
[![Node 20+](https://img.shields.io/badge/node-20%2B-green.svg)](https://nodejs.org/)
[![Docker Compose](https://img.shields.io/badge/docker--compose-v2-blue)](https://docs.docker.com/compose/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## 1. Executive Summary & Problem Context

The **Fuel Supply Intelligence & Resilience Platform (FSIP)** is an enterprise-grade operational decision-support and autonomous dispatch platform engineered for the downstream fuel distribution network of Bangladesh.

Operating across major fuel depots (e.g., Gazipur, Patiya) and critical urban and highway retail station networks (Mirpur, Tongi, Karnaphuli, Cox's Bazar), fuel logistics in emerging economies face severe systemic challenges:
1. **Vulnerable Transit Corridors:** Route disruptions (strikes, highway gridlocks, infrastructure failures) can instantly isolate single-route distribution points like Tongi and Cox's Bazar.
2. **Strict Physical Constraints:** Allocations dispatched during route disruptions lose fuel permanently with no inventory refund; station tank overflows cause unrecoverable fuel spills.
3. **Finite Upstream Supply:** External supply shipments terminate permanently after tick 212 (~2.2 simulated days), necessitating proactive inventory rationing rather than naive reactive restocking.
4. **Simulator Fault Injections:** Upstream telemetry is prone to artificial network latency, service outages (HTTP 503), stale cache headers, and SSE connection drops.

FSIP solves these challenges by combining **resilient telemetry ingestion**, **multi-step LightGBM demand forecasting**, **automated risk scoring**, and **linear programming (LP) dispatch optimization** behind an intuitive operator console.

![FSIP Overview Dashboard](docs/screenshots/desktop/overview.png)

---

## 2. System Architecture

FSIP follows an event-driven, decoupled microservices architecture designed to maintain high availability even during upstream simulator faults or network degradation:

```
                                  +------------------------------------+
                                  |    BUP Fuel Supply Simulator       |
                                  |    (v1.0.0 Docker Environment)     |
                                  +-----------------+------------------+
                                                    |  REST / SSE Stream
                                                    v
+------------------+         +-----------------------------------------+
| Operator Console | <-----> |           API Gateway (FastAPI)         |
| React 18 + Vite  | (Proxy) |   /api/state, /api/stream, /api/system  |
+------------------+         +--------------------+--------------------+
                                                  |
                     +----------------------------+----------------------------+
                     |                                                         |
                     v                                                         v
    +--------------------------------+                        +--------------------------------+
    |         Engine Service         |                        |      Intelligence Service      |
    |  - State Sync Loop (0.5s-2s)   |                        |  - LightGBM Demand Forecaster  |
    |  - Circuit Breaker & Retries   | <==== Internal REST == |  - Lead-Time Stockout Scoring  |
    |  - Closed-Loop Allocations     |                        |  - PuLP / HiGHS LP Optimizer   |
    |  - Degraded Fallback Policy    |                        |  - Digital Twin Scenario Lab   |
    +---------------+----------------+                        +--------------------------------+
                    |                                                         |
                    +----------------------------+----------------------------+
                                                 |
                                                 v
                     +---------------------------------------------------------+
                     |                 Data & Event Backbone                   |
                     |  - TimescaleDB 2.17: Hypertables (telemetry, decisions) |
                     |  - Redis 7.4: Pub/Sub & Redis Streams (events, replay)  |
                     |  - Prometheus & Grafana: Real-time telemetry monitoring |
                     +---------------------------------------------------------+
```

### Component Roles & Responsibilities

| Service | Technology | Port | Primary Responsibility |
|---|---|---|---|
| **`simulator`** | Python / SQLite | `8000` | Upstream ground truth simulation environment. |
| **`engine`** | Python 3.12 / FastAPI | `8100` | Autonomous state synchronization loop, upstream health probes, circuit breakers, allocation validation, and dispatch execution. |
| **`intelligence`**| Python 3.12 / LightGBM / PuLP | `8200` | 24–96 tick demand forecasting, risk probability modeling, and Linear Programming dispatch optimization. |
| **`api`** | Python 3.12 / FastAPI | `8080` (host `18080`) | Operator API gateway, role-based authorization, SSE event broadcasting, decision approval endpoints. |
| **`frontend`** | React 18 / Vite / Tailwind | `80` (host `3000`) | High-density operator cockpit: interactive network map, stockout alerts, live inventory graphs, and manual approval workflows. |
| **`timescaledb`** | PostgreSQL 16 + TimescaleDB | `5432` (host `15432`) | Time-series hypertables for tank inventory history, metrics, audit logs, and operational decisions. |
| **`redis`** | Redis 7.4 Alpine | `6379` (host `16379`) | Fast caching, domain event streaming (`fsip:domain-events`), and state synchronization. |
| **`observability`**| Prometheus + Grafana | `9090` / `13001` | Service-level telemetry, p95 latencies, SLO tracking, and system health dashboards. |

---

## 3. Engineering Principles & Operating Rules

1. **Safety First (Zero Fuel Waste):** The dispatch optimizer never allocates fuel exceeding route transit capacities, depot dispatch limits, or receiving station capacity headroom at expected arrival tick.
2. **Defensive Against Faults:** Upstream simulator faults (HTTP 503, stale data headers, connection resets) are isolated via exponential backoff and half-open circuit breakers. Stale data is never treated as current state.
3. **Graceful Degradation:** If the Machine Learning forecaster is unreachable, the system automatically falls back to an internal seasonal historical average model without interrupting dispatch recommendations.
4. **Human-in-the-Loop Governance:** Two distinct operating modes:
   - **Advisory Mode:** Decisions are staged as recommendations for human operator review and 1-click approval.
   - **Autopilot Mode:** Standard low-risk replenishment is autonomously dispatched; high-consequence allocations (> 7,000 L) require two-operator ("four-eyes") authorization.
5. **Strict Test Contracts:** All data models are validated against 24 captured simulator response fixtures, guaranteeing that malformed fields or unexpected enums are quarantined before touching business logic.

---

## 4. Seven-Day Phased Execution Plan

| Phase | Milestone | Focus Areas | Target Date |
|---|---|---|---|
| **Phase 00** | **Bootstrap & Foundation** | Repo structure, compose graph, shared domain models, contract tests, frontend skeleton, and CI pipeline. | **Thu 19 Nov (Done)** |
| **Phase 01** | **Simulator Integration** | Resilient async HTTP client, circuit breaker, exponential backoff, SSE listener, and real-time state sync. | **Fri 20 Nov** |
| **Phase 02** | **Persistence & Events** | TimescaleDB schemas, Alembic migrations, Redis event streaming, and state cache fallbacks. | **Fri 20 Nov** |
| **Phase 03** | **Live Network Dashboard** | **MVP Release**: Real-time network map, station tank levels, supply calendar, and alert badges. | **Sat 21 Nov** |
| **Phase 04** | **Baseline Dispatcher** | Allocation pipeline, route constraint checks, greedy baseline heuristic, and manual operator actions. | **Sat 21 Nov** |
| **Phase 05** | **Demand Forecasting** | LightGBM multi-step regression, feature engineering (hour-of-day, trend, profile multipliers), and WAPE tracking. | **Sun 22 Nov** |
| **Phase 06** | **Risk Engine & Alerts** | Lead-time to stockout calculation, starvation probability scoring, and severity-graded incident management. | **Sun 22 Nov** |
| **Phase 07** | **Digital Twin & What-If**| In-memory network simulator for counterfactual scenario testing and policy sandboxing. | **Mon 23 Nov** |
| **Phase 08** | **LP Dispatch Optimizer** | PuLP / HiGHS linear programming formulation maximizing minimum network service level under multi-depot constraints. | **Mon 23 Nov** |
| **Phase 09** | **Explainability & LLM** | Human-readable dispatch reasoning, trade-off explanations, and generative situational briefs. | **Tue 24 Nov** |
| **Phase 10** | **Operational Hardening** | Role-based auth (JWT), four-eyes authorization, emergency break-glass static plan export, and chaos injection. | **Tue 24 Nov** |
| **Phase 11** | **Full Observability** | Prometheus SLO metrics, alert manager webhooks, and comprehensive Grafana operations panels. | **Wed 25 Nov** |
| **Phase 12** | **Evaluation & Benchmarks**| Side-by-side performance benchmarking: LP Optimizer vs. Baseline Heuristic under multi-disruption crisis events. | **Wed 25 Nov** |
| **Phase 13–16**| **Submission & Rehearsal** | Feature freeze, k6 stress testing, demo walkthrough runbook, documentation polish, and final video recording. | **Thu 26 Nov** |

---

## 5. Current Implementation Status (Day 1 / Phase 00)

- [x] **Repository Skeleton & Version Pins:** Exact dependency configurations locked (`20_FINAL_SPEC_LOCK.md`).
- [x] **Shared Core Library (`libs/fsip_common`):** Fully implemented Pydantic v2 domain models for fuels (`DIESEL`, `PETROL`, `OCTANE`), nodes, routes, allocations, and events.
- [x] **Contract Test Verification:** **26/26 contract tests passing** against 24 simulator response and error fixtures.
- [x] **Service Health Endpoints:** **7/7 unit tests passing** across `engine`, `intelligence`, and `api` services. Total: **33/33 Python unit tests passing**.
- [x] **Frontend Shell:** React 18, Tailwind CSS, Lucide icons, and accessible `StatusPill` component. **5/5 Vitest tests passing**; production Vite build completes with zero errors.
- [x] **Observability Scaffolding:** Prometheus metrics collection and Grafana dashboard provisioning ready.
- [x] **Code Quality:** 100% compliant with `ruff` linting rules.

---

## 6. Architecture & Specification Index

Comprehensive technical documentation is maintained under [`docs/architecture/`](docs/architecture/):

- [`01_PROJECT_OVERVIEW.md`](docs/architecture/01_PROJECT_OVERVIEW.md) — Operational problem context, user personas, and target outcomes.
- [`02_SIMULATOR_FACTS.md`](docs/architecture/02_SIMULATOR_FACTS.md) — Verified technical behavior, undocumented quirks, and endpoints of the simulator.
- [`03_ARCHITECTURE.md`](docs/architecture/03_ARCHITECTURE.md) — End-to-end system topology, data flows, and service boundaries.
- [`04_DATA_MODEL_AND_API.md`](docs/architecture/04_DATA_MODEL_AND_API.md) — Database schemas, hypertable configurations, and state structures.
- [`05_INTELLIGENCE_DESIGN.md`](docs/architecture/05_INTELLIGENCE_DESIGN.md) — Forecasting models, feature stores, and LP optimization equations.
- [`06_RESILIENCE_AND_OBSERVABILITY.md`](docs/architecture/06_RESILIENCE_AND_OBSERVABILITY.md) — Circuit breaker patterns, degraded modes, and SLO definitions.
- [`07_FRONTEND_DESIGN.md`](docs/architecture/07_FRONTEND_DESIGN.md) — Operator dashboard design system, layout hierarchy, and state flow.
- [`13_ARCHITECTURE_V2.md`](docs/architecture/13_ARCHITECTURE_V2.md) — Consolidated architecture specification.
- [`17_TIMELINE_7_DAYS.md`](docs/architecture/17_TIMELINE_7_DAYS.md) — Day-by-day milestone delivery schedule.
- [`20_FINAL_SPEC_LOCK.md`](docs/architecture/20_FINAL_SPEC_LOCK.md) — Locked dependency versions, ports, and architectural constraints.
- [`21_API_CONTRACTS.md`](docs/architecture/21_API_CONTRACTS.md) — Strict REST and SSE API contract specifications.
- [`22_UI_SPEC.md`](docs/architecture/22_UI_SPEC.md) — UI design tokens, component behavior, and accessibility standards.
- [`23_MODULE_SPECS.md`](docs/architecture/23_MODULE_SPECS.md) — Deep module specifications for each service.

---

## 7. Getting Started

### Prerequisites
- **Python:** `3.12` or `3.13`
- **Node.js:** `v20+` & `npm 10+`
- **Docker:** Docker Desktop with Compose v2 (required for container orchestration)

### Quick Local Setup
```bash
# 1. Clone the repository
git clone https://github.com/IamAsif53/Fuel-Management-System.git
cd Fuel-Management-System

# 2. Configure environment
cp .env.example .env

# 3. Create virtual environment & install dependencies
python -m venv .venv
source .venv/bin/activate       # On Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
pip install -e libs/fsip_common

# 4. Run tests & linting
pytest libs/fsip_common services/engine services/intelligence services/api
ruff check libs services

# 5. Build and test frontend
cd frontend
npm install
npm test
npm run build
cd ..
```

### Running with Docker Compose
```bash
# Start complete stack (core services)
docker compose up -d --build

# Verify service health
bash scripts/smoke_test.sh

# Start with Prometheus & Grafana observability
docker compose --profile obs up -d
```

---

## 8. CI/CD, Deployment & Release Governance (Phase 13)

### Automated Pipeline (`.github/workflows/ci.yml`)
The FSIP platform is continuously validated via GitHub Actions across 6 automated stages:
1. **Lint & Style:** Ruff code analysis across `libs/`, `services/`, and `evaluation/`.
2. **Python Matrix Tests:** Pinned Python 3.12 running unit tests across all four packages (`libs/fsip_common`, `services/engine`, `services/intelligence`, `services/api`) backed by live ephemeral TimescaleDB and Redis service containers.
3. **Frontend Tests:** Node 20 executing Vitest unit tests, TypeScript type checks, and production Vite compilation.
4. **Container Image Build:** Docker Buildx builds immutable images stamped with `APP_VERSION` and `GIT_SHA`. The upstream simulator is strictly pulled from its pinned external repository and never modified or rebuilt.
5. **E2E Smoke & Evaluation:** Boots the integrated compose stack, executes `scripts/smoke_test.sh`, runs Playwright E2E integration tests, and runs a baseline deterministic evaluation.
6. **Publish to GHCR:** Authorized commits to `main` and semver release tags (`v*`) publish tagged images to GitHub Container Registry (`ghcr.io/iamasif53/fsip-*`).

### Safe Production Deployment & Automated Rollback
```bash
# Deploy a verified version to production (using docker-compose.prod.yml override)
bash scripts/deploy.sh v1.0.0

# In case of deployment rollback:
make rollback VERSION=v1.0.0
```
- **Production Hardening (`docker-compose.prod.yml`):**
  - Database (`15432:5432`) and cache (`16379:6379`) host ports are closed to the host network (internal communication only).
  - Source code volume binds are removed; runs strictly on immutable built images.
  - Interactive demo controls and chaos proxies are disabled (`FSIP_DEMO_CONTROLS_ENABLED=false`).
  - Automated rollback automatically triggers if service healthchecks or `smoke_test.sh` fail during deployment.

### Branch Protection & Repository Governance
To preserve system reliability and locked simulator contracts, repository administrators must configure GitHub branch protection rules for `main`:
1. **Require status checks to pass before merging:**
   - Require `Lint & Style`, `Python Unit Tests`, `Frontend Unit & Build Tests`, `Docker Build Validation`, and `End-to-End Evaluation & Smoke`.
2. **Require branches to be up to date before merging.**
3. **Require pull request reviews before merging.**
4. **Strictly disallow force pushes and branch deletions.**
5. **All pull requests must follow `.github/pull_request_template.md`.**

---

## 9. Deploy on Poridhi Lab (examiner URL)

Poridhi **Load Balancer** can only reach apps running **inside a Poridhi Lab VM**. A home Wi‑Fi IP (e.g. `192.168.x.x`) will return **504**.

### Steps
1. Open a Poridhi Lab terminal and get the lab IP:
   ```bash
   hostname -I
   ```
   Use the primary `10.x` address (not `172.17.0.1` Docker bridge).
2. Clone and enter the repo (folder names are **case-sensitive** — use Tab-complete):
   ```bash
   git clone https://github.com/IamAsif53/Fuel-Management-System.git
   cd ~/Fuel-Management-system
   ```
3. Avoid reserved port `3000`. Start on **8088**:
   ```bash
   cp .env.example .env
   echo "FE_HOST_PORT=8088" >> .env
   docker compose up -d --build
   docker compose ps
   curl -I http://127.0.0.1:8088
   ```
4. In Poridhi **Load Balancer**, expose lab IP + port **`8088`**. Share the generated `*.lb.poridhi.io` URL.

### Deploy evidence (Poridhi Lab)

![Healthy docker compose on Poridhi](docs/deploy/poridhi/screenshots/01-compose-ps-healthy.png)

![Frontend 200 OK on lab :8088](docs/deploy/poridhi/screenshots/02-curl-frontend-200.png)

![Load Balancer expose 10.61.15.204:8088](docs/deploy/poridhi/screenshots/03-load-balancer-expose.png)

---

## 10. Demo Walkthrough Video

Full console walkthrough recorded from the running FSIP application:

**[Download / view demo video](docs/demo/fsip-demo-walkthrough.mp4)**

```text
docs/demo/fsip-demo-walkthrough.mp4
```

> Stored with **Git LFS**. After clone, run `git lfs pull` if the video file is a small pointer instead of the full MP4.

---

## 11. Application Screenshots

Genuine captures from the **running** Fuel Ops Console (`http://127.0.0.1:8088`, Playwright, desktop 1440×900 / mobile 390×844). Capture report: [`docs/screenshots/CAPTURE_REPORT.md`](docs/screenshots/CAPTURE_REPORT.md).

### Authentication
Role-based sign-in for operator / supervisor / viewer before approvals and control actions.

![Sign-in](docs/screenshots/desktop/login.png)

### Overview Dashboard
Live KPIs, service level, and supply-network topology.

![Overview](docs/screenshots/desktop/overview.png)

### Network Monitoring
Station/depot inventory, routes, and in-transit allocations.

![Network](docs/screenshots/desktop/network.png)

### Risk Analysis
Stockout risk scoring with region / fuel filters.

![Risk Analysis](docs/screenshots/desktop/risk-analysis.png)

### Recommendations
Prescriptive dispatch inbox with human–AI metrics and approve/reject.

![Recommendations](docs/screenshots/desktop/recommendations.png)

### Decisions & Allocation Ledger
Tamper-evident decision history (allocation ledger / reconciliation surface).

![Decisions](docs/screenshots/desktop/decisions.png)

![Allocation Ledger](docs/screenshots/desktop/allocation-ledger.png)

![Decision Details](docs/screenshots/desktop/decision-details.png)

### What-If Analysis
Counterfactual digital-twin scenarios and comparison results.

![What-If](docs/screenshots/desktop/what-if.png)

### Operations Assistant
Grounded answers from live platform state (risks, alerts, health).

![Assistant](docs/screenshots/desktop/assistant.png)

### System Health / Observability
Service status, latency, and SLO compliance.

![System Health](docs/screenshots/desktop/system-health.png)

### Simulator Control Panel
Demo controls for stepping, chaos, and Autopilot prep (when `DEMO_CONTROLS=true`).

![Simulator Control](docs/screenshots/desktop/simulator-control.png)

### Alerts
Operational alert inbox and acknowledgement.

![Alerts](docs/screenshots/desktop/alerts.png)

### Important workflows

| Workflow | Screenshot |
|---|---|
| Dashboard metrics | ![Dashboard metrics](docs/screenshots/workflows/dashboard-metrics.png) |
| Risk filter by region | ![Risk region filter](docs/screenshots/workflows/risk-region-filter.png) |
| What-If run & results | ![What-If results](docs/screenshots/workflows/whatif-results.png) |
| Assistant Q&A | ![Assistant answer](docs/screenshots/workflows/assistant-answer.png) |
| Approval as Operator | ![Operator approval](docs/screenshots/workflows/approval-operator.png) |
| Four-eyes as Supervisor | ![Supervisor four-eyes](docs/screenshots/workflows/approval-supervisor-foureye.png) |

### Mobile responsiveness

![Overview mobile](docs/screenshots/mobile/overview-mobile.png)

![Risk mobile](docs/screenshots/mobile/risk-mobile.png)

![Assistant mobile](docs/screenshots/mobile/assistant-mobile.png)

Re-capture after UI changes:

```bash
# from fuel-platform/ with stack on :8088
cd frontend
FSIP_BASE_URL=http://127.0.0.1:8088 node ../scripts/capture_readme_screenshots.mjs
```

---

## 12. License

This project is developed for the **BUP CSE FEST 2026 Hackathon** and is licensed under the MIT License.
