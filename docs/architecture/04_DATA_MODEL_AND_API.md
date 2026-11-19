# 04: Data Model and Our APIs

## 1. PostgreSQL schema (Alembic migrations, owned by `engine`)

All tables have `id BIGSERIAL PK` unless noted, and `created_at TIMESTAMPTZ DEFAULT now()`.
`sim_run_id` links rows to one simulator lifetime (see reset detection in `02_SIMULATOR_FACTS.md` §7).

```sql
-- one row per simulator lifetime (new row after each detected reset)
CREATE TABLE sim_runs (
  id            BIGSERIAL PRIMARY KEY,
  scenario_id   TEXT, seed BIGINT, tick_minutes INT,
  started_at    TIMESTAMPTZ DEFAULT now(),
  ended_at      TIMESTAMPTZ,
  first_tick    INT, last_tick INT
);

-- compact world snapshot per processed tick (jsonb keeps it simple)
CREATE TABLE world_snapshots (
  id BIGSERIAL PRIMARY KEY, sim_run_id BIGINT REFERENCES sim_runs(id),
  tick INT NOT NULL, sim_time TIMESTAMPTZ NOT NULL,
  depots JSONB, stations JSONB, routes JSONB, events JSONB, supply JSONB, metrics JSONB,
  stale BOOLEAN DEFAULT false, degraded BOOLEAN DEFAULT false,
  created_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE (sim_run_id, tick)
);

-- demand observations copied from /v1/demand-history (dedupe on sim row id)
CREATE TABLE demand_observations (
  sim_run_id BIGINT, sim_row_id BIGINT, station_id TEXT, fuel_type TEXT, tick INT, sim_time TIMESTAMPTZ,
  demand_liters DOUBLE PRECISION, served_liters DOUBLE PRECISION, unmet_liters DOUBLE PRECISION,
  PRIMARY KEY (sim_run_id, sim_row_id)
);
CREATE INDEX ON demand_observations (sim_run_id, station_id, fuel_type, tick);

-- forecasts used for each decision (for prediction-error metrics)
CREATE TABLE forecasts (
  id BIGSERIAL PRIMARY KEY, sim_run_id BIGINT, made_at_tick INT, station_id TEXT, fuel_type TEXT,
  target_tick INT, p10 DOUBLE PRECISION, p50 DOUBLE PRECISION, p90 DOUBLE PRECISION,
  model_name TEXT, model_version TEXT, is_fallback BOOLEAN
);
CREATE INDEX ON forecasts (sim_run_id, station_id, fuel_type, target_tick);

-- risk assessment per (station,fuel) per decision cycle (keep latest + history)
CREATE TABLE risk_assessments (
  id BIGSERIAL PRIMARY KEY, sim_run_id BIGINT, tick INT, station_id TEXT, fuel_type TEXT,
  inventory DOUBLE PRECISION, inflight DOUBLE PRECISION, hours_to_stockout DOUBLE PRECISION,
  stockout_prob DOUBLE PRECISION, risk_level TEXT,        -- LOW|MEDIUM|HIGH|CRITICAL
  drivers JSONB                                         -- list of {signal, value, weight}
);

-- planner output: one decision = one planning cycle; lines = individual shipments
CREATE TABLE decisions (
  id BIGSERIAL PRIMARY KEY, sim_run_id BIGINT, tick INT,
  policy TEXT,                 -- optimizer|heuristic|manual
  policy_version TEXT,
  trigger TEXT,                -- scheduled|alert|operator|crisis
  status TEXT,                 -- PROPOSED|PENDING_APPROVAL|APPROVED|REJECTED|EXECUTED|PARTIAL|EXPIRED|FAILED
  mode TEXT,                   -- advisory|autopilot
  confidence DOUBLE PRECISION,
  degraded BOOLEAN,            -- data stale / fallback used
  fallback_reason TEXT,
  objective JSONB,             -- solver objective terms
  impact JSONB,                -- {before:{...}, after:{...}} from shadow twin
  explanation JSONB,           -- structured explanation (see 05 §7)
  narrative TEXT,              -- LLM or template text
  approved_by TEXT, approved_at TIMESTAMPTZ, rejected_reason TEXT,
  expires_at_tick INT,         -- recommendation invalid after this tick
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE decision_lines (
  id BIGSERIAL PRIMARY KEY, decision_id BIGINT REFERENCES decisions(id), line_no INT,
  source_depot_id TEXT, destination_station_id TEXT, route_id TEXT, fuel_type TEXT,
  quantity DOUBLE PRECISION, idempotency_key TEXT UNIQUE,
  status TEXT,                 -- PLANNED|SUBMITTED|PENDING|IN_TRANSIT|ARRIVED|FAILED|CANCELLED|REJECTED_BY_SIM
  sim_allocation_id BIGINT, sim_error_code TEXT,
  expected_arrival_tick INT, actual_arrival_tick INT, received_liters DOUBLE PRECISION,
  reason TEXT                  -- why this line exists, human readable
);

CREATE TABLE alerts (
  id BIGSERIAL PRIMARY KEY, sim_run_id BIGINT,
  fingerprint TEXT,            -- dedupe key e.g. "stockout:station-mirpur:DIESEL"
  category TEXT,               -- STOCKOUT_RISK|ANOMALY|DISRUPTION|SUPPLY_DELAY|DATA_QUALITY|SYSTEM|DECISION
  severity TEXT,               -- INFO|WARNING|CRITICAL
  title TEXT, message TEXT, entity_type TEXT, entity_id TEXT, fuel_type TEXT,
  status TEXT,                 -- OPEN|ACKNOWLEDGED|RESOLVED
  opened_tick INT, resolved_tick INT, opened_at TIMESTAMPTZ DEFAULT now(), resolved_at TIMESTAMPTZ,
  acknowledged_by TEXT, data JSONB, related_decision_id BIGINT
);
CREATE UNIQUE INDEX alerts_open_fp ON alerts (fingerprint) WHERE status <> 'RESOLVED';

-- system incidents (software failures) with timeline
CREATE TABLE incidents (
  id BIGSERIAL PRIMARY KEY, kind TEXT, component TEXT, status TEXT,   -- OPEN|MITIGATED|RESOLVED
  started_at TIMESTAMPTZ, mitigated_at TIMESTAMPTZ, resolved_at TIMESTAMPTZ,
  summary TEXT, timeline JSONB                                         -- [{at, event}]
);

-- operator audit
CREATE TABLE audit_log (
  id BIGSERIAL PRIMARY KEY, at TIMESTAMPTZ DEFAULT now(), actor TEXT, action TEXT,
  entity_type TEXT, entity_id TEXT, payload JSONB
);

-- model registry (intelligence service also keeps files; this is the shared index)
CREATE TABLE model_versions (
  id BIGSERIAL PRIMARY KEY, name TEXT, version TEXT, created_at TIMESTAMPTZ DEFAULT now(),
  metrics JSONB, params JSONB, artifact_path TEXT, is_active BOOLEAN DEFAULT false,
  UNIQUE(name, version)
);

-- evaluation runs (phase 12)
CREATE TABLE eval_runs (
  id BIGSERIAL PRIMARY KEY, scenario TEXT, policy TEXT, ticks INT, seed BIGINT,
  service_level DOUBLE PRECISION, unmet_liters DOUBLE PRECISION, failures INT,
  overflow_liters DOUBLE PRECISION, liters_shipped DOUBLE PRECISION, cross_region_liters DOUBLE PRECISION,
  min_station_service DOUBLE PRECISION, runtime_sec DOUBLE PRECISION, details JSONB,
  created_at TIMESTAMPTZ DEFAULT now()
);
```

### v2 additional tables (create in the first migration)
```sql
CREATE TABLE ledger (
  id BIGSERIAL PRIMARY KEY, at TIMESTAMPTZ NOT NULL DEFAULT now(), actor TEXT NOT NULL, action TEXT NOT NULL,
  payload JSONB NOT NULL, prev_hash TEXT NOT NULL, hash TEXT NOT NULL UNIQUE
);  -- append-only: REVOKE UPDATE, DELETE ON ledger FROM fsip_app; hash = sha256(prev_hash || canonical_json({id,at,actor,action,payload}))
    -- note: insert in two steps inside one transaction (get id from the sequence first, then compute the hash), or hash without id

CREATE TABLE feature_flags (key TEXT PRIMARY KEY, value JSONB NOT NULL, updated_by TEXT, updated_at TIMESTAMPTZ DEFAULT now());

CREATE TABLE quarantine (            -- rejected simulator payloads (malformed-data inject evidence)
  id BIGSERIAL PRIMARY KEY, at TIMESTAMPTZ DEFAULT now(), resource TEXT, errors JSONB, raw_body TEXT, sim_tick INT
);

CREATE TABLE allocation_reconciliation (
  decision_line_id BIGINT PRIMARY KEY REFERENCES decision_lines(id), sent DOUBLE PRECISION, received DOUBLE PRECISION,
  loss DOUBLE PRECISION, loss_reason TEXT, method TEXT   -- loss_reason: OVERFLOW|FAILED_ROUTE|NONE; method: audit|estimate
);

CREATE TABLE decision_outcomes (
  decision_id BIGINT PRIMARY KEY REFERENCES decisions(id), evaluated_tick INT,
  predicted JSONB, realised JSONB, hit BOOLEAN
);

CREATE TABLE playbook_runs (
  id BIGSERIAL PRIMARY KEY, playbook_id TEXT, sim_run_id BIGINT, started_tick INT, status TEXT,  -- RUNNING|COMPLETED|ABORTED
  steps JSONB, started_at TIMESTAMPTZ DEFAULT now(), ended_at TIMESTAMPTZ
);
```

Retention: `world_snapshots` keeps every tick (≈ 1 row/tick, small). `demand_observations` 12 rows/tick.
That is fine for days of simulated time.

## 2. Redis keys

| Key / channel | Type | Content | Writer |
|---|---|---|---|
| `fsip:state` | string (JSON) | full live snapshot (schema below) | engine |
| `fsip:state:tick` | string | last processed tick | engine |
| `fsip:health` | hash | component → JSON status | engine, api, intelligence |
| `fsip:domain-events` | stream (v2; was pub/sub `fsip:events`) | domain events, see `21` §2.9 | engine |
| `fsip:ratelimit:{ip}` | counter (TTL 60 s) | api rate limit | api |

### `fsip:state` JSON shape (the UI's main contract)
```json
{
  "sim_run_id": 3, "tick": 140, "sim_time": "2026-01-02T11:00:00Z", "sim_status": "RUNNING",
  "stale": false, "degraded": false, "degraded_reasons": [],
  "metrics": {"service_level": 0.998, "served": 1, "unmet": 0, "allocation_liters": 0, "allocation_failures": 0,
              "overflow_liters_est": 0},
  "depots": [{"id","name","region_id","status","dispatch_capacity_per_tick","capacity":{},"inventory":{},
              "dispatch_used_this_tick": 0, "projected_days_of_supply": {"DIESEL": 3.1}}],
  "stations": [{"id","name","region_id","status","demand_profile","demand_multiplier","capacity":{},"inventory":{},
                "inflight": {"DIESEL": 3000}, "fill_pct": {"DIESEL": 0.61},
                "risk": {"DIESEL": {"level":"HIGH","hours_to_stockout":6.2,"stockout_prob":0.72}}}],
  "routes": [{"id","source_depot_id","destination_station_id","transit_ticks","max_shipment","status",
              "scheduled_disruption": null}],
  "events": [...], "supply_arrivals_upcoming": [...],
  "allocations_active": [...],
  "alerts_open": [...],
  "pending_recommendations": [{"decision_id", "summary", "lines":[], "impact":{}, "confidence":0.81}],
  "system": {"simulator":"UP","sse":"CONNECTED","intelligence":"UP","db":"UP","redis":"UP",
             "breakers":{"simulator":"CLOSED","intelligence":"CLOSED"},
             "forecaster":"lightgbm@1.2.0","policy":"optimizer","mode":"advisory"}
}
```

## 3. engine internal API (port 8100, only reachable inside the compose network; requires header `X-Internal-Token`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/health/live` | process alive |
| GET | `/health/ready` | sim reachable OR serving cached state; DB reachable |
| GET | `/metrics` | Prometheus |
| POST | `/internal/decisions/{id}/approve` | body `{actor, line_ids?:[]}` → executes lines |
| POST | `/internal/decisions/{id}/reject` | body `{actor, reason}` |
| POST | `/internal/plan-now` | force a planning cycle (operator "Re-plan") |
| POST | `/internal/mode` | `{mode: advisory|autopilot}` |
| POST | `/internal/policy` | `{policy: optimizer|heuristic}` (policy rollback) |
| POST | `/internal/manual-allocation` | operator-created shipment; validated like a decision line |
| POST | `/internal/whatif` | body: list of candidate lines and/or hypothetical event → twin projection |

## 4. api public API (port 8080, prefix `/api`, used by the frontend and load tests)

Read endpoints are open (viewer). Write endpoints need header `Authorization: Bearer ${API_OPERATOR_TOKEN}`.
All responses include `X-Data-Tick`, `X-Degraded: true|false`.

| Method | Path | Returns |
|---|---|---|
| GET | `/api/state` | the `fsip:state` snapshot |
| GET | `/api/stream` | SSE: `state` (every cycle), `alert`, `decision`, `incident`, `health` |
| GET | `/api/stations/{id}` | station detail + risk timeline + recent demand + forecast band |
| GET | `/api/depots/{id}` | depot detail + projected inventory (incl. scheduled supply) |
| GET | `/api/risk` | all (station,fuel) risk rows, sorted by severity |
| GET | `/api/forecast?station_id=&fuel=&horizon=48` | forecast p10/p50/p90 + recent actuals |
| GET | `/api/alerts?status=OPEN&limit=` | alerts |
| POST | `/api/alerts/{id}/ack` | ack (operator) |
| GET | `/api/recommendations?status=PENDING_APPROVAL` | decisions awaiting approval |
| GET | `/api/decisions?limit=&status=` | decision history (with lines) |
| GET | `/api/decisions/{id}` | full decision incl. explanation, impact, narrative |
| POST | `/api/decisions/{id}/approve` | operator, proxies to engine |
| POST | `/api/decisions/{id}/reject` | operator |
| POST | `/api/plan-now` | operator |
| POST | `/api/whatif` | twin projection (rate limited) |
| GET | `/api/metrics/summary` | service level, unmet, failures, overflow, KPI cards |
| GET | `/api/system/status` | component health, breakers, p95 latency, error rate, fallback counts, versions |
| GET | `/api/incidents` | incident list with timelines |
| POST | `/api/assistant/ask` | `{question}` → grounded answer (LLM or rule-based fallback) |
| GET | `/api/audit` | operator audit log |
| POST | `/api/control/mode` | operator: advisory/autopilot |
| POST | `/api/control/policy` | operator: optimizer/heuristic |
| GET | `/api/models` | active + available model versions with metrics |
| POST | `/api/control/sim/{run|pause|step|reset}` | **demo-only** proxy to simulator `/admin/*`; disabled unless `DEMO_CONTROLS=true` |
| POST | `/api/control/scenario/{name}` | **demo-only**: injects a predefined crisis via `/admin/events` |
| POST | `/api/control/chaos/{fault}` | **demo-only**: injects a simulator fault via `/admin/faults` |
| GET | `/health/live`, `/health/ready`, `/metrics` | |

## 5. Pydantic models (in `libs/fsip_common/models.py`)

Create strict models mirroring `02_SIMULATOR_FACTS.md` §2: `Instance, Region, Depot, Station, Route, SupplyArrival,
SimEvent, Allocation, DemandObservation, SimMetrics, AllocationRequest`. Rules:
- `FuelType = Literal["DIESEL","PETROL","OCTANE"]`; `capacity`/`inventory: dict[FuelType, float]` and **require all
  3 keys**, with values ≥ 0 and finite.
- Status enums as `Literal[...]`. Unknown values → validation error → quarantine (see 06).
- `sim_time`: custom validator that accepts naive or tz-aware ISO strings → aware UTC datetime.
- `model_config = ConfigDict(extra="ignore")` (tolerate new fields), but required fields must exist.
- Cross-entity checks in `sim/validation.py`: route endpoints exist, inventory ≤ capacity × 1.01, ticks monotonic.
