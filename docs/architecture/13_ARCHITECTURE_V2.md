# 13: Architecture v2 (supersedes `03_ARCHITECTURE.md` where they differ)

`03` remains valid for layout, ports and the control loop. This file lists the **changes** introduced after the deeper
research, why each one helps the hackathon score, and exactly how to build it. If something here conflicts with
`03`/`04`, **this file wins**.

## 1. What changed and why

| # | Change | Why (judging criterion) | Risk control |
|---|---|---|---|
| V2-1 | **TimescaleDB** (`timescale/timescaledb:2.17.2-pg16`) instead of plain Postgres | Time-series history (snapshots, demand, risk, forecasts) with hypertables + continuous aggregates → fast replay and charts (Architecture, Observability) | 100% Postgres-compatible; migrations do `CREATE EXTENSION IF NOT EXISTS timescaledb` and fall back to plain tables if it is unavailable |
| V2-2 | **Redis Streams** `fsip:domain-events` instead of pub/sub | Replayable event log: browser SSE resumes with `Last-Event-ID` (unlike the simulator), the notifier uses a consumer group (event-driven architecture, queue-based processing = brief §14) | `XADD MAXLEN ~ 20000`; if Redis is down → DB polling fallback (unchanged) |
| V2-3 | New **notifier** service (+ **Mailpit** for the demo) | Alerts reach humans (Resilience & Incident Response); escalation, grouping, dedupe | Optional for core operations; failures are isolated |
| V2-4 | **OpenTelemetry tracing → Jaeger** promoted to core (was optional) | One trace per decision cycle across engine → intelligence → simulator; strong observability evidence | `OTEL_ENABLED=true` default; the sampler is parent-based 100% (low volume) |
| V2-5 | **Shadow twin + scenario lab moved to `libs/fsip_common`**, heavy runs (N-1, policy comparison, F-SMPC) executed in **intelligence** (`/lab/*`) | Keeps the engine control loop fast; CPU-heavy analysis is isolated and can fail independently | Engine never waits on `/lab/*`; results are cached |
| V2-6 | **JWT auth with roles** (viewer/operator/supervisor) in api | Security hygiene (brief §19), four-eyes control | Demo users from env; viewer endpoints stay open for judges |
| V2-7 | **feature_flags** table + `/api/admin/flags` | Runtime toggles (autopilot, LLM, scarcity policy, OTel, demo controls) without a redeploy; flags = rollback mechanism | Defaults in code; DB read cached 5 s |
| V2-8 | **Break-glass static plan** written to a shared volume and served by nginx | Works even when api/engine are down (Colonial lesson) | Plain HTML/CSV files |
| V2-9 | **Hash-chained ledger** table | Tamper-evident accountability (Pakistan lesson) | Append-only; verify endpoint |
| V2-10 | **Compose profiles**: default (core + observability), `chaos`, `mlops`, `loadtest` | Lean default start for judges; extras on demand | `docker compose --profile chaos up -d` |
| V2-11 | **Optional MLflow** (`profile: mlops`) as the experiment tracker/registry | The brief recommends experiment tracking + model versioning | The file registry stays the default source of truth; MLflow mirrors it if `MLFLOW_TRACKING_URI` is set |
| V2-12 | **PuLP pinned to 2.9.0** (classic API, bundled CBC) + `highspy` as the alternative solver | PuLP 4.x changed its API completely and ships no bundled CBC (verified 2026-11-19); a normal AI knows the 2.x API | `requirements.txt: pulp==2.9.0, highspy>=1.7` |

## 2. Updated diagram

```mermaid
flowchart LR
  SIM[BUP Simulator :8000]
  subgraph Control["Control plane (single writer)"]
    ENG[engine :8100<br/>sync · detect · forecast-client · risk · LP/heuristic · executor · ledger]
  end
  subgraph Intel["Intelligence plane"]
    INT[intelligence :8200<br/>forecast · conformal · anomaly · /lab N-1, policy compare, SMPC]
  end
  subgraph IT["IT plane (scalable)"]
    API[api ×N :8080<br/>REST · SSE(replayable) · auth/RBAC · reports · assistant]
    FE[frontend nginx :3000<br/>SPA + /breakglass static]
    NOT[notifier :8300<br/>rules · grouping · escalation]
  end
  subgraph Data
    TS[(TimescaleDB :5432)]
    RS[(Redis :6379<br/>state cache + Streams)]
    VOL[(breakglass volume)]
  end
  subgraph Obs
    PROM[Prometheus] --> GRAF[Grafana :3001]
    LOKI[Loki] --> GRAF
    JAEG[Jaeger :16686] --> GRAF
    AM[Alertmanager] --> API
  end
  MAIL[Mailpit :8025]

  SIM -- REST/SSE --> ENG
  ENG -- POST /v1/allocations --> SIM
  ENG -- forecast (timeout+breaker) --> INT
  ENG -- XADD domain events / SET state --> RS
  ENG -- history, ledger --> TS
  ENG -- write plan files --> VOL
  API --> RS & TS
  API -- internal cmds --> ENG
  API -- /lab requests --> INT
  NOT -- XREADGROUP --> RS
  NOT --> MAIL
  FE --> API
  FE -- /breakglass --> VOL
```

## 3. Services & ports (full list, v2)

| Service | Image/build | Port | Profile |
|---|---|---|---|
| simulator | `asifmahmoud414/bup-fuel-supply-simulator:1.0.0` | 8000 | default |
| timescaledb | `timescale/timescaledb:2.17.2-pg16` | 5432 | default |
| redis | `redis:7.4-alpine` | 6379 | default |
| engine | build `services/engine` | 8100 | default |
| intelligence | build `services/intelligence` | 8200 | default |
| api | build `services/api` (no container_name; scalable) | 8080 (via frontend proxy) | default |
| notifier | build `services/notifier` | 8300 | default |
| frontend | build `frontend` | 3000 | default |
| mailpit | `axllent/mailpit:v1.21` | 8025 (UI), 1025 (SMTP) | default |
| prometheus | `prom/prometheus:v2.55.1` | 9090 | default |
| alertmanager | `prom/alertmanager:v0.27.0` | 9093 | default |
| grafana | `grafana/grafana:11.2.0` | 3001 | default |
| loki | `grafana/loki:3.2.1` | 3100 | default |
| promtail | `grafana/promtail:3.2.1` | - | default |
| jaeger | `jaegertracing/all-in-one:1.60` | 16686, 4317 | default |
| cadvisor | `gcr.io/cadvisor/cadvisor:v0.49.1` | 8081 | default |
| node-exporter | `prom/node-exporter:v1.8.2` | 9100 | default |
| chaos-proxy | build `chaos-proxy` | 8001 | chaos |
| mlflow | `ghcr.io/mlflow/mlflow:v2.17.2` | 5000 | mlops |
| k6 | `grafana/k6:0.54.0` | - | loadtest |

Image tags above are suggestions known to exist as of late 2024. If a pull fails, use the nearest existing tag and
record it in the Deviations log.

Memory budget for the whole default profile: ≈ 4-5 GB RAM. If the dev/demo machine is tight, drop cadvisor/node-exporter/jaeger
first (they are the least visible to judges) and note it.

## 4. New/changed env vars (append to `.env.example`)
```dotenv
DATABASE_URL=postgresql+asyncpg://fsip:change-me@timescaledb:5432/fsip
REDIS_STREAM_MAXLEN=20000
OTEL_ENABLED=true
OTEL_EXPORTER_OTLP_ENDPOINT=http://jaeger:4317
JWT_SECRET=change-me-jwt
DEMO_USERS=viewer:viewer:viewer,operator:operator:operator,supervisor:supervisor:supervisor   # user:pass:role (demo only)
FOUR_EYES_THRESHOLD_LITERS=7000
NOTIFY_SMTP_HOST=mailpit
NOTIFY_SMTP_PORT=1025
NOTIFY_WEBHOOK_URL=                     # optional Slack-compatible webhook
NOTIFY_ESCALATE_AFTER_SEC=120
BREAKGLASS_DIR=/data/breakglass
SAFETY_TICKS=16
SAFETY_NOISE=0.15
SAFETY_WEIGHT=2
SCARCITY_POLICY=history_based           # history_based | equal_cover | priority_first | quota
MLFLOW_TRACKING_URI=                    # set only with --profile mlops
```

## 5. Domain events (Redis Stream `fsip:domain-events`)
Every event: `{"id": <stream id>, "type": "...", "at": iso, "sim_run_id": n, "tick": t, "data": {...}}`.
Types: `state.updated` (lightweight: tick + changed ids; clients fetch `/api/state` with ETag), `alert.opened|updated|resolved`,
`decision.created|approved|rejected|executed|expired|outcome`, `incident.opened|updated|resolved`, `mode.changed`,
`playbook.started|step|completed`, `ledger.appended`, `reset.detected`, `scarcity.entered|exited`.

- Engine produces (XADD). api `/api/stream` reads with `XREAD BLOCK 15000 STREAMS fsip:domain-events <last-id>`; it honours the
  browser's `Last-Event-ID` header to **replay missed events** after a reconnect, and sends `id:` on every SSE message.
- Notifier: `XREADGROUP GROUP notifier n1`, XACK after delivery, `XAUTOCLAIM` stale messages on restart.

## 6. TimescaleDB specifics
```sql
CREATE EXTENSION IF NOT EXISTS timescaledb;
-- world_snapshots, demand_observations, risk_assessments, forecasts need a time column:
-- add  sim_time TIMESTAMPTZ NOT NULL  and make (sim_run_id, sim_time, ...) part of the PK for hypertables.
SELECT create_hypertable('demand_observations', by_range('sim_time', INTERVAL '1 day'), if_not_exists => TRUE);
SELECT create_hypertable('world_snapshots',    by_range('sim_time', INTERVAL '1 day'), if_not_exists => TRUE);
-- hourly continuous aggregate for charts
CREATE MATERIALIZED VIEW demand_hourly WITH (timescaledb.continuous) AS
SELECT sim_run_id, station_id, fuel_type, time_bucket('1 hour', sim_time) AS bucket,
       sum(demand_liters) demand, sum(served_liters) served, sum(unmet_liters) unmet
FROM demand_observations GROUP BY 1,2,3,4;
```
Note: hypertables require the partition column in every unique index. Adjust the PK in `04` accordingly
(`PRIMARY KEY (sim_run_id, sim_row_id, sim_time)`). Simulated time starts at 2026-01-01; that is fine.

## 7. Tracing conventions
- Root span `engine.cycle` (attributes: tick, sim_run_id, mode, policy). Children: `sim.sync`, `intelligence.forecast` (with
  HTTP propagation into the intelligence service), `risk.compute`, `decision.plan` (solver status), `decision.execute`
  (one span per line, with the sim status code).
- api spans for operator requests; `trace_id` is added to structlog logs → Grafana links logs ↔ traces (Loki derived field).

## 8. Updated repo layout additions
```
services/notifier/            # rules.py, channels/{email,webhook}.py, main.py
libs/fsip_common/twin.py      # moved from the engine (shared)
libs/fsip_common/conformal.py
libs/fsip_common/forecast_seasonal.py
config/{policy.yaml, priorities.yaml, notify.yaml, playbooks/*.yaml, slo.yaml}
observability/prometheus/{slo.yml, alerts.yml}
deploy/{versions.env, docker-compose.prod.yml}
```
