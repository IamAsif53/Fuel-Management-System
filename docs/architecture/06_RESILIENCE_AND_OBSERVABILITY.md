# 06: Resilience and Observability

## 1. Failure matrix (implement ALL rows; each row has a chaos drill in phase 10)

| # | Failure | Detection | Automatic response | Operator sees | Recovery | Drill script |
|---|---|---|---|---|---|---|
| F1 | Simulator `unavailable` (503) | client 503 count, breaker opens after 5 failures | retries (3, exp backoff 0.2→1.6 s + jitter), then breaker OPEN; engine serves **cached last-good state**; planning paused; NO writes | red banner "Simulator unreachable – showing data from tick N (x s old)"; Health page breaker OPEN | half-open probe every 15 s via `/v1/instance`; on success: full resync, breaker CLOSED, incident resolved | `scripts/chaos/sim_unavailable.sh 60` |
| F2 | Simulator `error_rate` 25% | per-endpoint error ratio | retries absorb most; per-request failure does not fail the cycle (use the previous value for that resource for ≤ 2 ticks) | warning badge "Simulator flaky (error rate 24%)" | automatic | `sim_error_rate.sh 0.3 90` |
| F3 | Simulator `latency` 500-2000 ms | p95 of sim calls | timeouts 2 s, parallel GETs, coalesce ticks; if a cycle > 5 s, skip forecasting refresh (reuse last) | "Simulator slow: p95 1.2 s"; Grafana latency panel | automatic | `sim_latency.sh 1500 60` |
| F4 | `stale_data` header | `x-simulator-stale: true` | mark snapshot stale; **autopilot paused**; recommendations flagged "stale data – manual review" | amber "STALE DATA" badge | when the header is gone for 2 consecutive reads | `sim_stale.sh 60` |
| F5 | SSE `stream_disconnect` / dropped | 503 on connect or no bytes > 30 s | reconnect with backoff (1,2,4,8…30 s); **polling fallback** (`/v1/instance` every 1 s) keeps the loop going; full REST resync on reconnect | "Live stream down – polling mode" | auto | `sse_disconnect.sh 60` |
| F6 | Malformed/missing fields | pydantic validation error | reject the payload, keep last-good for that resource, `DATA_QUALITY` alert, metric; if > 3 consecutive cycles → degraded mode (advisory only) | Data Quality alert with the offending field path | auto when valid data returns | `malformed_proxy.sh` (chaos-proxy corrupts responses) |
| F7 | Intelligence service down/slow | timeout 1.5 s / breaker | **fallback seasonal forecaster** inside engine; decision confidence lowered; `fsip_fallback_activations_total{component="forecast"}` | "Prediction service DOWN – using fallback model (seasonal@builtin)" | breaker half-open probe; switch back automatically | `kill_intelligence.sh` (docker stop, then start) |
| F8 | Optimizer error/timeout/infeasible | exception / status | heuristic policy for that cycle; reason stored | decision tagged "fallback: heuristic (solver timeout)" | next cycle tries the optimizer again | unit test + `OPTIMIZER_FORCE_FAIL=true` env drill |
| F9 | Postgres down | SQLAlchemy errors / ready probe | engine keeps operating (in-memory + Redis); DB writes buffered in an in-memory ring (max 5,000 items) and flushed on recovery; api falls back to Redis-only reads | "History DB unavailable – history writes buffered" | auto flush | `kill_db.sh 30` |
| F10 | Redis down | connection errors | engine continues and writes to DB; api reads the latest `world_snapshots` from Postgres (slower) and SSE polls the DB every 2 s | "Cache down – degraded latency" | auto | `docker stop fsip-redis` |
| F11 | Engine crash | container restart / api ready check | `restart: unless-stopped`; on boot: rebuild state from simulator REST + DB (outcomes of in-flight lines reconciled via `/v1/allocations` idempotency keys) | "Engine restarting" then normal | auto | `docker restart fsip-engine` |
| F12 | API traffic surge | rate, p95 | api is stateless and serves cached state; rate limit 600 req/min/IP → 429; scale with `docker compose up --scale api=3` (nginx upstream round-robin) | Grafana | auto | k6 `spike.js` |
| F13 | Allocation rejected (409) | response code | map code → action (02 §3); `DISPATCH_CAPACITY_EXCEEDED` → retry next tick; others → drop + alert `ALLOCATION_REJECTED` | Decision line status + reason | n/a | unit tests |
| F14 | Allocation FAILED (route disrupted at departure) | status FAILED in allocations | alert `ALLOCATION_FAILED` (fuel lost), re-plan via an alternative route; rule 2 should prevent it | alert | n/a | harness scenario |
| F15 | Simulator reset | tick decreases / notice | new sim_run, clear state, keep history | toast "Simulator reset detected – new run #N" | auto | `scripts/demo/reset.sh` |
| F16 | Low prediction confidence | confidence < 0.7 | human review required | "Needs review" badge | n/a | harness |
| F17 | Model drift | rolling WAPE | switch to the seasonal model + alert | Model card shows drift | operator can roll back the model version | inject spike without event awareness |
| F18 | Bad deploy | CI health check / smoke test fails | CI blocks; manual `make rollback VERSION=x` re-deploys the previous image tags | n/a | | phase 13 |

### Degraded-mode state machine (engine)
```
NORMAL ──(stale | sim breaker OPEN | ≥3 invalid cycles | intelligence fallback)──▶ DEGRADED
DEGRADED: autopilot suspended, recommendations still produced (if data available), UI banner shows reasons
DEGRADED ──(all reasons cleared for 2 cycles)──▶ NORMAL
SIM breaker OPEN for > 60 s ──▶ SAFE_HOLD: no planning, show last-good state with age, keep probing
```
`fsip_engine_mode{mode="NORMAL|DEGRADED|SAFE_HOLD"}` gauge (1 for the current mode).

### Patterns to implement (code-level)
- `CircuitBreaker(name, failure_threshold, reset_timeout)` with states CLOSED/OPEN/HALF_OPEN and a metric
  `fsip_breaker_state{name}` (0/1/2).
- `SimClient`: one `httpx.AsyncClient` (timeout 2 s, pool limits 20), `tenacity` retry only on idempotent GETs and on
  POST allocations (safe because of the idempotency key), `Retry-After` respected, error envelope parsing for
  both `detail` and `error` shapes. Every call is measured: `fsip_sim_request_duration_seconds{endpoint,status}`.
- Bulkheads: separate semaphores for sim reads (8), sim writes (2), intelligence calls (4).
- Timeouts everywhere (no unbounded awaits). Cancellation-safe loops (`asyncio.TaskGroup` + try/except per task,
  restart crashed sub-loops with backoff, `fsip_loop_restarts_total{loop}`).
- Graceful shutdown: SIGTERM → stop accepting, finish the in-flight cycle (max 5 s), flush buffers.

## 2. Observability

### 2.1 Metrics (Prometheus, all prefixed `fsip_`)
**Application (api, engine, intelligence):** `http_requests_total{service,route,method,status}`,
`http_request_duration_seconds{service,route}` (histogram, buckets .005….5), `fsip_sim_request_duration_seconds`,
`fsip_sim_errors_total{endpoint,code}`, `fsip_sse_connected` (0/1), `fsip_sse_reconnects_total`,
`fsip_cycle_duration_seconds`, `fsip_ticks_behind` (sim tick − processed tick), `fsip_breaker_state{name}`,
`fsip_engine_mode{mode}`, `fsip_stale_data` (0/1), `fsip_db_buffered_writes`.
**System:** cAdvisor (container CPU/mem/network) + node-exporter (host). Add both to compose.
**Intelligence:** listed in `05_INTELLIGENCE_DESIGN.md` §8.
**Domain KPIs:** `fsip_service_level`, `fsip_unmet_liters_total`, `fsip_station_inventory_liters{station,fuel}`,
`fsip_depot_inventory_liters{depot,fuel}`, `fsip_stockout_prob{station,fuel}`, `fsip_inflight_liters{station,fuel}`.

### 2.2 Logs
structlog JSON with fields: `ts, level, service, event, sim_run_id, tick, decision_id, trace_id, component`.
Mandatory log events: `sim.sync.ok|failed`, `sim.invalid_payload`, `sse.connected|disconnected`, `breaker.state_change`,
`decision.created|approved|rejected|executed|expired`, `allocation.submitted|rejected|failed|arrived|overflow`,
`alert.opened|resolved`, `fallback.activated|deactivated`, `incident.opened|resolved`, `reset.detected`.
Promtail ships docker logs → Loki. Grafana "Logs" panel filtered by `{service="engine"} |= "decision"`.

### 2.3 Tracing (optional, cheap win)
OpenTelemetry auto-instrumentation for FastAPI + httpx, OTLP → Jaeger all-in-one (`jaegertracing/all-in-one:1.60`,
already on the dev machine). Behind env flag `OTEL_ENABLED=false` by default. Only do this in phase 11 if time allows.

### 2.4 Grafana dashboards (provisioned JSON, committed)
1. **FSIP – Operations Overview**: service level, unmet liters, stockout prob heatmap (station×fuel), station and depot
   inventory time series, in-flight liters, open alerts by severity, decisions/hour, autopilot vs manual.
2. **FSIP – Service Health**: request rate/latency p50/p95/p99/error rate per service, sim call latency + errors,
   breaker states, SSE connected, ticks behind, engine mode, container CPU/mem.
3. **FSIP – Intelligence**: forecast error (by horizon), WAPE, interval coverage, model version, confidence,
   fallback activations, solver duration, alerts rate, anomaly counts.
4. **FSIP – Logs & Incidents**: Loki logs panels + annotations from incidents.

### 2.5 Alert rules (`observability/prometheus/alerts.yml` → Alertmanager → webhook to api `/api/internal/alertmanager`, which stores them as SYSTEM alerts and shows them in the UI)
| Alert | Expr (sketch) | For | Severity |
|---|---|---|---|
| SimulatorDown | `fsip_breaker_state{name="simulator"} == 1` | 30s | critical |
| HighErrorRate | `sum(rate(http_requests_total{status=~"5.."}[1m])) / sum(rate(http_requests_total[1m])) > 0.05` | 1m | warning |
| HighLatencyP95 | `histogram_quantile(0.95, sum by (le,service)(rate(http_request_duration_seconds_bucket[5m]))) > 0.5` | 2m | warning |
| EngineFallingBehind | `fsip_ticks_behind > 10` | 1m | warning |
| PredictionFallbackActive | `increase(fsip_fallback_activations_total{component="forecast"}[2m]) > 0` | 0m | warning |
| StaleData | `fsip_stale_data == 1` | 30s | warning |
| ServiceLevelDrop | `fsip_service_level < 0.97` | 2m | critical |
| StockoutRiskCritical | `max(fsip_stockout_prob) > 0.7` | 1m | critical |
| ContainerDown | `up == 0` | 30s | critical |

### 2.6 Health endpoint content (`GET /api/system/status`)
```json
{"overall":"DEGRADED","engine_mode":"DEGRADED","components":{
  "backend_api":{"status":"Healthy","p95_ms":42,"error_rate":0.001},
  "database":{"status":"Healthy"},"redis":{"status":"Healthy"},
  "fuel_simulator":{"status":"Healthy","breaker":"CLOSED","p95_ms":12,"stale":false,"sse":"CONNECTED"},
  "prediction_service":{"status":"Down","breaker":"OPEN","fallback":"seasonal@builtin"},
  "decision_engine":{"status":"Healthy","policy":"optimizer@1.0.0","last_cycle_ms":180,"ticks_behind":0}},
 "versions":{"api":"1.3.0","engine":"1.3.0","intelligence":"1.3.0","frontend":"1.3.0","git_sha":"abc123"},
 "p95_latency_ms":164,"error_rate":0.004}
```
This mirrors the brief's §16 example. The UI shows it as the System Status panel.
