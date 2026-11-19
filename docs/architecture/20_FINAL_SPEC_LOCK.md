# 20: FINAL SPEC LOCK (single source of truth)

If two documents disagree, follow this precedence (highest first):
**20 (this file) > 24 (exact file contents) > 21 (API contracts) > 23 (module specs) > 22 (UI spec) > 13 (architecture v2)
> phase files > 12 > 05/06/07/08 > 03/04 > 01/02.**
Everything in this file is decided. Do not re-decide it; if it is impossible, log a Deviation in `10_DECISIONS_AND_ASSUMPTIONS.md`.

---

## 1. Resolved conflicts (old text → locked value)
| Topic | Older docs said | LOCKED |
|---|---|---|
| Database | Postgres 16 (`postgres` service) in 03 | **TimescaleDB** image `timescale/timescaledb:2.17.2-pg16`, service/host name **`timescaledb`**, container `fsip-timescaledb` |
| Event fan-out | Redis pub/sub `fsip:events` (03) | **Redis Stream `fsip:domain-events`** (13 §5). `fsip:state` (string) stays the snapshot cache |
| api container | `fsip-api` | **no container_name** (scalable). Use `docker compose stop api` / `start api` in scripts |
| Host ports | api 8080, Postgres 5432, Redis 6379, Grafana 3001 | **api 18080, Postgres 15432, Redis 16379, Grafana 13001** (host side only; env-configurable). Internal ports unchanged |
| Planning horizon | 48 ticks (05 §2, §4.3) | **96 ticks** for the optimizer; the risk engine projects **48** ticks (12 h) for stockout ETA and probability |
| Depot reserve | 5% (03 env) | **0** by default; scarcity reserve only by supervisor action |
| PuLP | "≥2.8" | **`PuLP==2.9.0`** + `highspy==1.8.1` |
| Tracing | optional (06) | Jaeger is **T2**: build it in phase 11 only if T1 is green; `OTEL_ENABLED=false` by default |
| Observability services | all in the default profile (13) | default profile = core app; **`--profile obs`** = prometheus, grafana, loki, promtail, alertmanager, cadvisor, node-exporter, jaeger; `--profile chaos` = chaos-proxy; `--profile mlops` = mlflow |
| Notifier/Mailpit | default (13) | T2 (phase 19); `--profile notify` until phase 19 is green, then default |
| Chaos scripts DB container | `fsip-postgres` | **`fsip-timescaledb`** |
| Forecast model location | the seasonal model in engine *or* common | **`libs/fsip_common/forecast_seasonal.py`** (used by both engine fallback and intelligence) |
| Twin location | `engine/decision/twin.py` (03) | **`libs/fsip_common/twin.py`** (13 V2-5) |
| Overview URL | `/` | `/` redirects to **`/overview`** once phase 03 is done (Phase 00 redirects to `/health`) |

## 2. Locked versions
- **Python 3.12**. Python pins = `requirements-dev.txt` in `24` (fastapi 0.115.6, uvicorn 0.32.1, httpx 0.27.2, httpx-sse 0.4.0,
  pydantic 2.10.4, pydantic-settings 2.7.0, prometheus-client 0.21.1, structlog 24.4.0, SQLAlchemy 2.0.36, asyncpg 0.30.0,
  alembic 1.14.0, redis 5.2.1, tenacity 9.0.0, orjson 3.10.12, numpy 2.1.3, pandas 2.2.3, scikit-learn 1.5.2,
  lightgbm 4.5.0, joblib 1.4.2, PuLP 2.9.0, highspy 1.8.1, Jinja2 3.1.4, python-jose 3.3.0, anthropic 0.40.0,
  pytest 8.3.4, pytest-asyncio 0.24.0, respx 0.21.1, hypothesis 6.122.3, ruff 0.8.4; plus pyarrow 18.1.0 in intelligence).
  They were chosen as **well-known, stable APIs** (newer majors such as pandas 3, SQLAlchemy 2.1, pytest-asyncio 1.x and
  PuLP 4 changed APIs). They were verified to install together on Python 3.12.
- **Node 20** (Docker) / any Node ≥ 20 locally. Frontend pins = `frontend/package.json` in `24` (react 18.3.1, react-router-dom
  6.28.0, @tanstack/react-query 5.62.7, recharts 2.15.0, lucide-react 0.468.0, clsx 2.1.1, vite 5.4.11, typescript 5.7.2,
  tailwindcss **3.4.17** (v3 config style; **not** Tailwind 4), vitest 2.1.8, @testing-library/react 16.1.0, jsdom 25.0.1).
  Additions allowed later: `react-leaflet@4.2.1` + `leaflet@1.9.4` (T3 map), `@playwright/test@1.49.1` (e2e).
- **Images:** simulator `asifmahmoud414/bup-fuel-supply-simulator:1.0.0`, `timescale/timescaledb:2.17.2-pg16`,
  `redis:7.4-alpine`, `python:3.12-slim`, `node:20-alpine`, `nginx:1.27-alpine`, `prom/prometheus:v2.55.1`,
  `grafana/grafana:11.2.0`, `grafana/loki:3.2.1`, `grafana/promtail:3.2.1`, `prom/alertmanager:v0.27.0`,
  `gcr.io/cadvisor/cadvisor:v0.49.1`, `prom/node-exporter:v1.8.2`, `jaegertracing/all-in-one:1.60`,
  `axllent/mailpit:v1.21`, `grafana/k6:0.54.0`, `ghcr.io/mlflow/mlflow:v2.17.2`.

## 3. Services, container names, ports
| Service (DNS name) | Container name | Internal port | Host port env (default) | Profile |
|---|---|---|---|---|
| simulator | fsip-simulator | 8000 | SIM_HOST_PORT (8000) | default |
| timescaledb | fsip-timescaledb | 5432 | PG_HOST_PORT (15432) | default |
| redis | fsip-redis | 6379 | REDIS_HOST_PORT (16379) | default |
| engine | fsip-engine | 8100 | ENGINE_HOST_PORT (8100) | default |
| intelligence | fsip-intelligence | 8200 | INTEL_HOST_PORT (8200) | default |
| api | (none; scalable) | 8080 | API_HOST_PORT (18080), removed when scaling | default |
| frontend | fsip-frontend | 80 | FE_HOST_PORT (3000) | default |
| notifier | fsip-notifier | 8300 | NOTIFIER_HOST_PORT (8300) | notify → default after P19 |
| mailpit | fsip-mailpit | 8025 / 1025 | MAILPIT_HOST_PORT (8025) | notify |
| prometheus | fsip-prometheus | 9090 | PROM_HOST_PORT (9090) | obs |
| grafana | fsip-grafana | 3000 | GRAFANA_HOST_PORT (13001) | obs |
| loki | fsip-loki | 3100 | LOKI_HOST_PORT (3100) | obs |
| promtail | fsip-promtail | - | - | obs |
| alertmanager | fsip-alertmanager | 9093 | AM_HOST_PORT (9093) | obs |
| cadvisor | fsip-cadvisor | 8080 | CADVISOR_HOST_PORT (8081) | obs |
| node-exporter | fsip-node-exporter | 9100 | - | obs |
| jaeger | fsip-jaeger | 16686 / 4317 | JAEGER_HOST_PORT (16686) | obs |
| chaos-proxy | fsip-chaos-proxy | 8001 | CHAOS_HOST_PORT (8001) | chaos |
| mlflow | fsip-mlflow | 5000 | MLFLOW_HOST_PORT (5000) | mlops |
Compose project name: **`fsip`** (network `fsip_default`). Every doc's `localhost:8080` means **`localhost:${API_HOST_PORT}`**
or, better, through the frontend proxy **`localhost:3000/api/...`**.

## 4. Environment variables
The full list with defaults is `.env.example` in `24` plus these later additions (append when the phase starts):
`REDIS_STREAM_MAXLEN=20000` (P02), `OTEL_EXPORTER_OTLP_ENDPOINT=http://jaeger:4317` (P11), `NOTIFY_SMTP_HOST=mailpit`,
`NOTIFY_SMTP_PORT=1025`, `NOTIFY_WEBHOOK_URL=`, `NOTIFY_ESCALATE_AFTER_SEC=120` (P19), `BREAKGLASS_DIR=/data/breakglass` (P10),
`OPTIMIZER_FORCE_FAIL=false`, `INTEL_FORCE_GARBAGE=false` (P10 drills), `MLFLOW_TRACKING_URI=` (mlops).
Settings classes read env vars case-insensitively (pydantic-settings); the field name = the env var in lower case.

## 5. Locked domain parameters (defaults)
| Parameter | Value | Source |
|---|---|---|
| Station reorder threshold (heuristic) | projected fill < 0.60 or risk ≥ HIGH | 05 §4.2 |
| Station target fill | 0.90 | benchmark |
| Min line size | 300 L (unless CRITICAL) | 05 §4.1 |
| Overflow guard | projected inventory at arrival (with **p10 demand**) + qty ≤ 0.98 × capacity | 14 §5 gap fix |
| LP horizon / checkpoints | 96 ticks; [2,3,4,6,8,12,16,20,24,32,40,48,64,80,96] | benchmark v4 |
| LP future slots | [4,8,12,16,20,28,36,48,64] | benchmark v4 |
| LP weights | unmet 10, overflow 40, safety 2, terminal 0.5, depot clip 20, transit 0.002, fairness 5 | benchmark v4 |
| Safety stock | demand over next 16 ticks + 0.15 × cumulative demand (→ conformal band in P18) | benchmark |
| Solver | PULP_CBC_CMD(msg=False, timeLimit=5); non-Optimal → heuristic | 14 |
| Constrained depot | self-imposed 50% dispatch cap | ADR-15 |
| Risk projection | 48 ticks, Monte Carlo N=300, seed per (run,tick,station,fuel) | 05 §2 |
| Lead time | min transit over available routes + 1 + 2 ticks | 05 §2.2 |
| Recommendation expiry | t + 2 ticks | 05 §4.5 |
| Autopilot gate | confidence ≥ 0.7, every line ≤ 7,000 L, not stale, no CRITICAL data-quality alert, engine NORMAL | 05 §4.5 |
| Four-eyes threshold | decision total > 7,000 L | 12 F-RBAC |
| Breaker | 5 failures → OPEN, reset 15 s | 06 |
| Sim timeout / retries | 2.0 s / 3 (backoff 0.2·2^n × jitter, max 2 s) | 14 §1 |
| Intelligence timeout | 1.5 s | 06 F7 |
| SAFE_HOLD | sim breaker OPEN > 60 s | 06 |
| Alert auto-resolve | condition clear for 2 cycles | 05 §2.4 |
| Scarcity enter/exit | cover < 1.0 for 2 cycles / > 1.1 for 4 cycles | P17 |

## 6. Naming conventions (exact strings)
- **Idempotency key:** `fsip-{sim_run_id}-{decision_id}-{line_no}`; manual: `fsip-{sim_run_id}-m{audit_id}`.
- **Metrics:** prefix `fsip_` for domain/engine; `http_requests_total{service,route,method,status}` and
  `http_request_duration_seconds{service,route}` for every HTTP service; `route` = the route **template**.
- **Log events:** dotted lower-case (`sim.sync.ok`, `decision.executed`, …), list in 06 §2.2.
- **Redis:** `fsip:state`, `fsip:state:tick`, `fsip:health` (hash), `fsip:domain-events` (stream),
  `fsip:lab:n1`, `fsip:ratelimit:{ip}`.
- **Domain event types:** 13 §5.
- **Enums (Python `Literal` + TS union, identical spelling):**
  - risk level `LOW|MEDIUM|HIGH|CRITICAL`
  - alert severity `INFO|WARNING|CRITICAL`; alert status `OPEN|ACKNOWLEDGED|RESOLVED`
  - alert category `STOCKOUT_RISK|STOCKOUT_OCCURRED|DEPOT_DEPLETION_RISK|DEPOT_OVERFLOW_RISK|SUPPLY_DELAY|SUPPLY_SHORTFALL|ROUTE_DISRUPTED|STATION_OUTAGE|DEPOT_CONSTRAINED|DEMAND_ANOMALY|REGIONAL_DEMAND_SURGE|INVENTORY_VARIANCE|BOTTLENECK|MODEL_DRIFT|DATA_QUALITY|ALLOCATION_FAILED|ALLOCATION_REJECTED|OVERFLOW_LOSS|SYSTEM|DECISION`
  - decision status `PROPOSED|PENDING_APPROVAL|AWAITING_SECOND_APPROVAL|APPROVED|REJECTED|EXECUTED|PARTIAL|EXPIRED|FAILED`
  - line status `PLANNED|SUBMITTED|DEFERRED|PENDING|IN_TRANSIT|ARRIVED|FAILED|CANCELLED|REJECTED_BY_SIM`
  - engine mode `NORMAL|DEGRADED|SAFE_HOLD|NO_FEASIBLE_ROUTES`; operating mode `advisory|autopilot`
  - policy `optimizer|heuristic|manual`; scarcity policy `history_based|equal_cover|priority_first|quota`
  - component health `Healthy|Degraded|Down`; breaker `CLOSED|OPEN|HALF_OPEN`
  - role `viewer|operator|supervisor`
- **Error body of OUR APIs:** always `{"detail": {"code": "UPPER_SNAKE", "message": "human text", "data": {...optional}}}`
  (same shape as the simulator's domain errors). Validation errors: FastAPI default 422.

## 7. Coding rules
- Python: async everywhere for I/O; **every network call has a timeout**; no bare `except:`; type hints; `ruff check` clean;
  pydantic models at every boundary; no `print` (structlog); pure functions for maths (projection, LP building, twin) so they
  are unit-testable without I/O.
- Never import service code across services; share only via `libs/fsip_common`.
- Engine is the **only** writer to the simulator (`POST /v1/allocations`, cancel). `/admin/*` only in tests, harness, chaos
  scripts and the api's demo-control endpoints behind `DEMO_CONTROLS=true`.
- TypeScript: strict mode; API types only from `src/api/types.ts`; no `any` (use `unknown` + narrowing); every async UI
  state has loading / error / empty variants; `data-testid` on elements used by tests (names in 22).
- Tests live next to each service (`services/*/tests`, `frontend/src/**/*.test.tsx`, `frontend/e2e`). Each task in 23 lists its tests.
- Commits: `phase-NN: <what>`; one task ≈ one commit.

## 8. Definition of Done (per task)
1. The code matches the contract in 21/23 (names, fields, status codes).
2. Its tests from 23 exist and pass; `make test` and `make lint` are green; `npm test` is green if the frontend was touched.
3. `make up && make smoke` is still green.
4. PROGRESS.md is ticked with a one-line note; deviations are logged.
