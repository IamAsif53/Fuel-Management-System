# 23: Module Specs (files, signatures, behaviour, tests)

For each module: **file → public interface → behaviour rules → tests (Given / When / Then)**. Signatures are binding (other
modules call them). Private helpers are free. Types come from `fsip_common.models` (in `24`) unless noted. Use `from __future__
import annotations`. "Tested blueprint" means copy from `reference/` and adapt.

---

## A. libs/fsip_common (shared)

### A1 `models.py`, `timeutil.py`, `logging.py`, `settings.py`: exact content in `24`.

### A2 `forecast_seasonal.py` (P05; used by engine fallback + intelligence)
```python
@dataclass
class SeriesHistory:            # one (station, fuel)
    station_id: str; fuel_type: str
    ticks: list[int]; demand: list[float]; multiplier: list[float]

class SeasonalProfileModel:
    name = "seasonal"; version = "builtin"
    def __init__(self, tick_minutes: int = 15, ewma_alpha: float = 0.3, level_window: int = 8): ...
    def fit(self, histories: list[SeriesHistory]) -> "SeasonalProfileModel": ...
        # base[(s,f,hour)] = median(demand/multiplier) per hour-of-day; residual quantiles q10/q90 of (actual/pred - 1)
    def predict(self, h: SeriesHistory, start_tick: int, horizon: int, multiplier_now: float,
                multiplier_schedule: list[tuple[int, int, float]] = ()) -> dict[str, list[float]]:
        # returns {"tick": [...], "p10": [...], "p50": [...], "p90": [...]}; all finite, >= 0, p10<=p50<=p90
```
Rules: the hour of a tick = `(tick * tick_minutes // 60) % 24` (the simulator starts at 00:00). Cold start (no base for an hour) →
mean of the last 4 observations / multiplier. Event-aware: the schedule multiplier applies inside [from, to).
Tests: G synthetic data generated with the real hour factors (02 §8) + 10% noise, 3 days | fit+predict | p50 within ±5% of the
true mean per hour; G multiplier_now=1.8 | predict | p50 scales ×1.8; G a schedule that ends at t+10 | predict | ticks ≥ t+10 are
back to 1.0; G empty history | predict | no exception, flat non-negative values.

### A3 `conformal.py` (P18): copy `AdaptiveConformal` from `reference/blueprints/analytics.py` (tested).
### A4 `twin.py` (P08/P18)
```python
@dataclass
class TwinInputs:
    tick: int; tick_minutes: int
    stations: list[Station]; depots: list[Depot]; routes: list[Route]
    inflight: list[tuple[str, str, int, float]]          # (station_id, fuel, arrival_tick, qty)
    supply: list[SupplyArrival]; events: list[SimEvent]
    demand_samples: dict[tuple[str, str], np.ndarray]    # (s,f) -> array (N, H), already includes multipliers
def project(inp: TwinInputs, lines: list[PlanLine], extra_events: list[SimEvent] = (), horizon: int = 96,
            policy: Callable[[TwinState], list[PlanLine]] | None = None) -> TwinResult
@dataclass
class TwinResult:
    per_station: dict[tuple[str, str], dict]   # {"inv_p10": [...], "inv_p50": [...], "inv_p90": [...],
                                               #  "stockout_prob": float, "first_stockout_tick_p50": int|None,
                                               #  "unmet_mean": float, "overflow_mean": float}
    per_depot: dict[tuple[str, str], dict]     # {"inv_p50": [...], "clipped_mean": float}
    network: dict                              # {"unmet_mean", "overflow_mean", "min_station_sl", "jain"}
```
Rules (must mirror the simulator, `02` §3-4): depot deduction at line creation; departure next tick; if the route is blocked at
departure → the line FAILS and the fuel is lost; arrival at `created + transit`; station overflow is clipped and lost; OUTAGE → served
0; supply arrives at `planned_tick` (+delay), depot clipping; `policy` (if given) is called every tick for closed-loop runs (N-1).
Tests: deterministic mode (N=1, noise 0) equals a hand-computed 6-tick example; a blocked route → failed loss counted; overflow
counted; calibration test vs the real simulator (P08 8.2).

---

## B. services/engine

### B1 `sim/circuit.py`, `sim/client.py`: tested blueprint `reference/blueprints/sim_client.py`
Add: metrics in `_request` (`fsip_sim_request_duration_seconds{endpoint,status}`, `fsip_sim_errors_total{endpoint,code}`),
breaker gauge `fsip_breaker_state{name}` (0 CLOSED, 1 OPEN, 2 HALF_OPEN), and a log on state change.
Tests (respx): 503 ×2 then 200 → success with 3 calls; 503 ×5 → `CircuitOpenError` next; a 409 domain error is not retried and
does not open the breaker; stale header → `SimResult.stale`; 422 → `SimValidationError`; timeout → `SimUnavailable`.

### B2 `sim/validation.py`
```python
class ValidatedSnapshot(BaseModel): instance: Instance; regions: list[Region]; depots: list[Depot]; stations: list[Station];
    routes: list[Route]; events: list[SimEvent]; supply: list[SupplyArrival]; allocations: list[Allocation]; metrics: SimMetrics
def validate_resource(name: str, raw: Any) -> Any          # raises SimInvalidPayload(resource, errors: list[dict], raw)
def cross_check(snap: ValidatedSnapshot) -> list[str]      # world_issues + tick monotonic (handled in sync)
```
On `SimInvalidPayload`: insert into `quarantine` (resource, errors, raw_body ≤ 64 KB, tick), increment
`fsip_sim_invalid_responses_total{resource}`, raise a DATA_QUALITY alert with the first error's `loc`.
Tests: each malformed variant from `24` test_models → SimInvalidPayload with the correct `loc`.

### B3 `sim/sse.py`
```python
class SSEListener:
    def __init__(self, base_url: str, on_tick: Callable[[int], Awaitable[None]],
                 on_notice: Callable[[dict], Awaitable[None]], on_connect: Callable[[], Awaitable[None]]): ...
    async def run(self) -> None        # forever; reconnect backoff 1,2,4..30 s (+jitter); 30 s byte-silence -> reconnect
    connected: bool; reconnects: int
```
Events used: `simulation.tick` → `on_tick(tick)`; `simulator.notice` → `on_notice`; (re)connect → `on_connect` (full resync).
`allocation.status_changed` / `inventory.updated` → just trigger `on_tick(current)` coalesced (REST is the truth).
Tests: a fake SSE server (httpx MockTransport streaming) delivering `: connected`, a tick, a keepalive → callbacks called;
503 on connect → backoff retry; silence → reconnect.

### B4 `state/store.py`
```python
@dataclass
class WorldState:
    sim_run_id: int; snapshot: ValidatedSnapshot | None
    demand: dict[tuple[str, str], deque[DemandObservation]]   # maxlen 2000 per series
    last_good_at: dict[str, float]; stale: bool; degraded_reasons: set[str]
    engine_mode: str; operating_mode: str; policy: str
    risk: dict[tuple[str, str], RiskCell]; pending: list[DecisionSummary]; bottleneck: dict | None
    def apply(self, partial: dict[str, Any | Exception], tick: int) -> None   # keep last-good per failed resource
    def inflight(self) -> list[tuple[str, str, int, float]]                   # our PENDING/IN_TRANSIT allocations
    def to_public_json(self) -> dict                                          # exactly the WorldState of 21 §2.1
```
Tests: apply with one resource failing keeps the old value + adds a degraded reason; to_public_json validates against a pydantic
`WorldStateOut` schema (the same model the api uses).

### B5 `state/sync.py`
```python
class SyncLoop:
    def __init__(self, client: SimClient, store: WorldState, hooks: list[Callable[[WorldState], Awaitable[None]]],
                 poll_interval: float): ...
    async def run(self) -> None                 # polls /v1/instance every poll_interval as a fallback tick source
    async def request_cycle(self, tick: int) -> None   # called by SSE; coalescing: at most 1 running + 1 pending
    async def full_sync(self) -> None
```
Cycle order = `03` §6. Reset detection = `02` §7 → `on_reset` (new sim_run row, clear the store, event `reset.detected`).
Demand fetch: `limit = min(2000, 12 * max(1, tick - last_tick) + 12)`, dedupe by id. Metrics: `fsip_cycle_duration_seconds`,
`fsip_ticks_behind`, `fsip_loop_restarts_total{loop}`.
Tests: two `request_cycle` calls during a slow cycle → exactly 2 cycles total (not 3); a tick going backwards → reset path; a demand
fetch dedupe.

### B6 `state/publisher.py`
```python
class Publisher:
    async def publish_state(self, store: WorldState) -> None     # SET fsip:state (orjson), SET fsip:state:tick, XADD state.updated
    async def event(self, type: str, data: dict) -> str          # XADD fsip:domain-events MAXLEN ~ 20000 -> stream id
```
Redis errors are caught, logged and counted (`fsip_redis_errors_total`) and never raised into the cycle.

### B7 `state/mode.py`
```python
class ModeController:
    def evaluate(self, store: WorldState, breaker_open_for_s: float, invalid_cycles: int, forecast_fallback: bool,
                 feasible_routes: int) -> str     # NORMAL | DEGRADED | SAFE_HOLD | NO_FEASIBLE_ROUTES; emits mode.changed
```
Rules `06` (state machine) + NO_FEASIBLE_ROUTES when every route is DISRUPTED/blocked for the next departure. Tests: each transition.

### B8 `db/models.py`, `db/session.py`, `db/repo.py`, `db/buffer.py`
Tables: `04` §1 + v2 tables in `04` + hypertables per `13` §6. Alembic revision `0001_initial` creates all of them.
```python
class WriteBuffer:          # all writes go through it
    def __init__(self, sessionmaker, max_items: int = 5000): ...
    def submit(self, fn: Callable[[AsyncSession], Awaitable[None]], kind: str) -> None    # never blocks; drops oldest if full
    async def run(self) -> None                                                         # worker: batch commit, retry w/ backoff
```
Repo functions (async, take a session): `create_sim_run`, `close_sim_run`, `upsert_snapshot`, `insert_demand_rows`,
`insert_forecasts`, `insert_risk_rows`, `create_decision(decision, lines) -> Decision`, `update_decision_status`,
`update_line`, `get_decision`, `list_decisions`, `upsert_alert`, `resolve_alert`, `open_incident`, `append_incident_event`,
`resolve_incident`, `audit`, `ledger_append`, `insert_quarantine`, `insert_reconciliation`, `insert_outcome`.
Tests (pytest + a Postgres/Timescale service container or `docker compose up timescaledb`): round-trip of each; the buffer keeps
items while the DB is down and flushes after.

### B9 `intelligence/client.py`
```python
class ForecastProvider:
    async def forecast(self, store: WorldState, horizon: int) -> ForecastBundle   # never raises
@dataclass
class ForecastBundle:
    model: str; version: str; is_fallback: bool
    series: dict[tuple[str, str], dict[str, np.ndarray]]   # p10/p50/p90 arrays length = horizon
```
Remote call with timeout + breaker; on any failure or output-validation failure → `SeasonalProfileModel` fitted on the store's
demand buffer (refit every 16 ticks), `is_fallback=True`, degraded reason `prediction_service_down`, metric
`fsip_fallback_activations_total{component="forecast"}`. Tests: respx 200 → remote; timeout → fallback; NaN in the output → fallback.

### B10 `risk/projection.py`, `risk/stockout.py`
```python
def station_projection(st: Station, fuel: str, inflight: list[tuple[str, str, int, float]], demand_p50: np.ndarray,
                       tick: int, outage_mask: np.ndarray) -> dict      # {"inv": array, "overflow": array, "ticks_to_stockout": int|None}
def depot_projection(d: Depot, fuel: str, supply: list[SupplyArrival], planned_outflow: np.ndarray, tick: int) -> dict
                       # {"inv": array, "clipped": array}
def assess(store: WorldState, fb: ForecastBundle) -> dict[tuple[str, str], RiskCell]     # uses stockout_probability()
def risk_level(p_lead: float, p_h: float, hours: float | None, lead_h: float, fill: float, outage: bool) -> str
```
`stockout_probability` = tested blueprint. Rules `05` §2.3. Performance: ≤ 50 ms for 12 series (`fsip_risk_duration_seconds`).
Tests: hand cases (arrival before stockout, overflow, outage), monotonicity, level table cases (one per row of `05` §2.3).

### B11 `detect/anomaly.py`, `detect/recon.py`, `detect/surge.py`, `detect/rules.py`
```python
class DemandAnomalyDetector:  def update(self, obs: DemandObservation, prev_fc: tuple[float, float, float]) -> AnomalyResult | None
class ReconciliationTracker:   def update(self, store: WorldState, new_rows: list[DemandObservation]) -> list[VarianceAlarm]
                               def line_reconciliation(self, line, allocation, station_before: float, capacity: float) -> dict
class SurgeDetector:           def update(self, alarms: list[AnomalyResult], store: WorldState) -> list[SurgeAlert]
def rule_alerts(prev: WorldState, cur: WorldState) -> list[AlertSpec]   # disruption/outage/constraint/supply delay/pre-emptive
```
CUSUM = tested blueprint. Explained-by-event matching per `05` §3. Tests from P07 + F-RECON acceptance.

### B12 `decision/constraints.py`
```python
@dataclass
class PlanLine: route_id: str; source_depot_id: str; destination_station_id: str; fuel_type: str; quantity: float; reason: str = ""
@dataclass
class Violation: rule: str; message: str          # rule ids: R1_ROUTE_MATCH, R2_ROUTE_BLOCKED, R3_NODE_STATUS, R4_MAX_SHIPMENT,
                                                  # R5_DEPOT_INVENTORY, R6_DISPATCH, R7_STATION_CAPACITY, R7B_OVERFLOW_AT_ARRIVAL,
                                                  # R8_MIN_SIZE, R9_STALE
def route_blocked(events: list[SimEvent], routes: dict[str, Route], route_id: str, tick: int) -> bool
def check_line(store: WorldState, line: PlanLine, used_dispatch: dict[str, float], reserve: dict[tuple[str, str], float],
               demand_p10_until_arrival: float, critical: bool) -> list[Violation]
def check_plan(store: WorldState, lines: list[PlanLine], ...) -> tuple[list[PlanLine], list[tuple[PlanLine, list[Violation]]]]
```
100% branch coverage. Tests: one per rule (pass + fail) + the empty-filter disruption + a hypothesis property (random states → the
heuristic/optimizer output has no violations).

### B13 `decision/heuristic.py`
`def plan_heuristic(store, risk, fb, cfg) -> Plan` with `Plan(lines, reasons, policy="heuristic", version="1.0.0",
objective=None, solver_status="n/a")`. The algorithm is `05` §4.2 (reference: `greedy()` in `reference/policy_benchmark.py`).

### B14 `decision/optimizer.py`
`def plan_optimizer(store, risk, fb, cfg) -> Plan`. Port `lp()` from `reference/policy_benchmark.py` (tested, v4 defaults in `20`
§5) replacing `demand()` with `fb.series[...]["p50"]` (and p10 for the overflow guard). Name constraints
(`dispatch_{depot}`, `depotinv_{depot}_{fuel}_{slot}`, `cap_{station}_{fuel}_{m}`) for the bottleneck detector.
`OPTIMIZER_FORCE_FAIL=true` → raise → heuristic. Return `solver_status` + objective terms.
Tests: feasibility property; with a scheduled disruption covering t+1 → no x on that route; with scarcity → every station gets >
0; a timeout (patch timeLimit=0.001) → fallback path works.

### B15 `decision/engine.py`
```python
class DecisionEngine:
    async def on_cycle(self, store: WorldState, risk, fb) -> Decision | None
        # every ENGINE_DECISION_INTERVAL_TICKS: plan (policy per cfg, fallback chain optimizer→heuristic), constraints,
        # twin impact + alternatives, confidence (05 §4.4), explanation, persist, supersede older PENDING, approval policy
        # (05 §4.5 / 20 §5), execute if autopilot gate passes, publish decision.* events
    async def approve(self, decision_id: int, actor: str, role: str, line_ids=None, quantities=None, reviewed_driver=False) -> Decision
    async def reject(self, decision_id: int, actor: str, reason_code: str, comment: str) -> Decision
    async def plan_now(self, actor: str) -> None
```
Error mapping to API codes: expired → `RECOMMENDATION_EXPIRED`; stale → `STALE_DATA`; violations → `CONSTRAINT_VIOLATION`; cognitive
forcing not satisfied → `REVIEW_REQUIRED`; four-eyes → `AWAITING_SECOND_APPROVAL` status or `FORBIDDEN`.
Tests: supersede; expiry; autopilot gate truth table (each condition false → PENDING_APPROVAL); approve a subset; modify qty re-validated.

### B16 `decision/executor.py`
```python
class Executor:
    async def execute(self, decision: Decision, lines: list[DecisionLine]) -> Decision
    async def track_outcomes(self, store: WorldState) -> None      # match /v1/allocations by idempotency_key each cycle
```
Mapping: 201/200 → SUBMITTED (+sim id); 409 `DISPATCH_CAPACITY_EXCEEDED` → DEFERRED; other 409/404 → REJECTED_BY_SIM (code
stored, alert `ALLOCATION_REJECTED`); 503/timeout after retries → stays PLANNED and is retried next cycle **with the same key**;
422 → REJECTED_BY_SIM + a DATA_QUALITY bug alert. Decision status: all submitted → EXECUTED; some → PARTIAL; none → FAILED.
Tests: every code path with respx; a replay with the same key after a timeout yields one allocation.

### B17 `explain/explainer.py`, `explain/templates/*.j2`, `explain/llm.py`
```python
def build_explanation(decision, plan, store, risk, impact, alternatives, confidence_factors) -> dict   # 05 §7.1 exact keys
def render_template(name: str, ctx: dict) -> str
class NarrativeService: async def narrate(self, explanation: dict) -> tuple[str, str]   # (text, "llm"|"template"); never raises
def numbers_in(text: str) -> set[str]; def guard(text: str, explanation: dict) -> bool   # every number in text exists in the JSON
```
Tests: the keys; the guard rejects an invented number; the LLM timeout → template.

### B18 `alerts/manager.py`, `alerts/incidents.py`
```python
class AlertManager:
    async def raise_(self, spec: AlertSpec) -> Alert          # dedupe by fingerprint, escalate severity, publish
    async def clear_check(self, active_fingerprints: set[str]) -> None   # resolve after 2 clean cycles
    async def ack(self, alert_id: int, actor: str) -> Alert
class IncidentManager:
    async def component_down(self, component: str, detail: str) -> None
    async def mitigated(self, component: str, how: str) -> None
    async def recovered(self, component: str) -> None
```
Tests: dedupe/escalate/resolve; the incident timeline order.

### B19 `ledger.py`, `breakglass.py`, `playbooks/runner.py`, `observability/metrics.py`
- `ledger_append(session, actor, action, payload) -> LedgerRow` (advisory lock, hash = tested blueprint); `verify(session)`.
- `write_breakglass(store, plan, dir)` → `latest.html` + `latest.csv` (atomic write: temp file + rename) every 4 ticks.
- Playbook runner: YAML schema in P19; `on_alert(alert)`, `on_cycle(store)`; steps `auto|manual`; events `playbook.*`.
- `metrics.py`: define ALL Prometheus metrics in one place (names in `05` §8 and `06` §2.1) so they are registered once.

## C. services/api
| File | Content |
|---|---|
| `app/main.py` | from `24` + include routers + middleware (metrics, rate limit, error handler mapping to `{"detail":{code,message}}`) |
| `app/schemas.py` | pydantic models for every request/response in `21` (names identical to `21` §1/§5) |
| `app/deps.py` | `get_redis`, `get_session`, `current_user` (JWT or the operator token), `require_role(role)` |
| `app/ratelimit.py` | Redis INCR + EXPIRE 60 per ip+route group; in-memory fallback; 429 + Retry-After |
| `app/state_cache.py` | read `fsip:state` bytes; cache in-process per tick (ETag); DB fallback |
| `app/routers/*.py` | one per group in `21` §2; write endpoints forward to the engine internal API with `X-Internal-Token` |
| `app/stream.py` | SSE from the Redis stream with Last-Event-ID replay (`XRANGE` then `XREAD BLOCK`) |
Tests: each router with httpx AsyncClient + fakeredis-style mocks (or a real Redis container); auth/role matrix; 304 ETag; SSE replay.

## D. services/intelligence
| File | Content |
|---|---|
| `app/main.py` | from `24` + `/forecast`, `/anomaly/score`, `/models`, `/models/activate`, `/lab/*` |
| `app/registry.py` | `artifacts/<name>/<version>/{model.joblib, meta.json}`, `artifacts/active.json`; `load_active()`, `activate(name, version)`, `list()` |
| `app/models/gbm.py` | `GBMQuantileModel.fit(df) / predict(...)` same output contract as A2 |
| `app/lab.py` | N-1 + policy comparison using `fsip_common.twin` + `plan_heuristic` (copy of the engine function in common, or an HTTP call to the engine; prefer moving the heuristic to `fsip_common/heuristic.py`) |
| `training/generate_dataset.py`, `training/train.py` | P05 tasks 5.1 / 5.4 (CLI with argparse; writes `data/demand.parquet`, `docs/evidence/forecast-backtest.md`, `artifacts/experiments.jsonl`) |
Tests: the forecast contract (shape, p10≤p50≤p90), activate unknown → 404, train on a tiny synthetic frame runs < 10 s.

## E. evaluation/harness.py (P12)
CLI: `--scenario NAME|all --policy NAME|all --ticks N --sim-url URL --out DIR --assert-service-level X --assert-no-failures`.
Start from `reference/policy_benchmark.py` (`run()` already computes SL, unmet, failures, overflow via audit, depot clipping,
per-station SL, p95 decision time); swap its inline policies for the engine's `plan_heuristic` / `plan_optimizer` (imported
in-process with a `WorldState` built from the snapshot). Outputs `results.json`, `results.csv`, `report.md` (tables + matplotlib PNGs).
