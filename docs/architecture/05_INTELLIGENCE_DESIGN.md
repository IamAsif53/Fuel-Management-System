# 05: Intelligence Design

Five layers, each with a fallback and each measurable:

| Layer | Primary | Fallback | Where |
|---|---|---|---|
| Forecast | LightGBM quantile model + seasonal-profile model (choose by backtest) | in-engine seasonal profile (no ML) | intelligence svc / engine `intelligence/fallback.py` |
| Risk | Monte Carlo projected inventory → stockout ETA + probability | deterministic projection (p50 only) | engine `risk/` |
| Detection | residual z-score + CUSUM + rules | rules only | engine `detect/` (+ intelligence `/anomaly/score`) |
| Decision | LP rolling-horizon optimizer (PuLP/CBC) | greedy heuristic | engine `decision/` |
| Explanation | structured explanation + LLM narrative | structured + template narrative | engine `explain/` |

Notation: `t` = current tick, `s` = station, `d` = depot, `f` = fuel, `r` = route, `H` = planning horizon (48 ticks = 12 h).

---

## 1. Forecasting

### 1.1 Target
`demand_liters` per (station, fuel, tick), **not** served (served is censored by stockouts).

### 1.2 Model A: seasonal profile (also used as the fallback)
Learned purely from history (do not hard-code the PDF numbers. Use them only in tests to check the learned values):
```
base[s,f,h]  = median over history of ( demand / demand_multiplier_at_that_tick )  for hour-of-day h
level[s,f]   = EWMA(alpha=0.3) of ( actual / (base * multiplier) ) over the last 8 ticks   # adapts to drift
forecast p50 = base[s,f,hour(t+k)] * multiplier_now[s] * level[s,f]
p10/p90      = p50 * (1 + q10/q90 of historical relative residuals)                          # ≈ ±noise
```
`demand_multiplier_now` comes from `/v1/stations` (live spike signal). For future ticks inside a known
SCHEDULED/ACTIVE `demand_spike` event, apply the event multiplier for the ticks it covers (and remove it after
`end_tick`). This is "event-aware forecasting".
Cold start (no history yet): use profile-free estimate = mean of the last 4 observations / multiplier, flat.

### 1.3 Model B: LightGBM quantile regression
- Training data: generated in phase 05 by `training/generate_dataset.py`. It drives the simulator with
  `/admin/reset` + random events + `/admin/step` for N simulated days per run, several runs with different random
  event sets (the seed is fixed in the image; variety comes from events). It also runs a greedy allocator so
  stations are not empty (realistic regime).
- Features per row (station, fuel, target tick): `hour`, `dow` (day index mod 7), `minute_of_day`,
  `station_id` (cat), `fuel` (cat), `profile` (cat), `region_factor`, `demand_multiplier` at target tick (known if
  an event schedule exists, else current), `lag_1`, `lag_2`, `lag_4`, `lag_96` (same time yesterday),
  `rolling_mean_8`, `rolling_std_8`, `horizon_k` (k = 1..48), `event_spike_active` flag.
- Three models: objective `quantile` with alpha 0.1/0.5/0.9 (or one model per quantile).
- Backtest: rolling origin over the last 20% of time. Metrics: MAE, MAPE, WAPE, pinball loss, interval coverage
  (target 80% ± 5).
- Model selection: active model = lowest WAPE on backtest. If LightGBM is not at least 2% better than seasonal,
  **keep seasonal active** (simpler is better, and say so in the report).
- Registry: `artifacts/{name}/{version}/model.joblib + meta.json` (metrics, params, data hash, created_at,
  git sha). Version = semver; the active pointer is in `artifacts/active.json` and the `model_versions` table.

### 1.4 Intelligence service API
```
POST /forecast
  {"tick": 140, "horizon": 48, "series": [{"station_id","fuel_type","history":[{tick,demand,multiplier}...],
    "multiplier_now":1.0, "multiplier_schedule":[{"from_tick","to_tick","multiplier"}]}]}
→ {"model":"lightgbm","version":"1.2.0","generated_at":"…",
   "forecasts":[{"station_id","fuel_type","points":[{"tick","p10","p50","p90"}...]}]}
POST /anomaly/score   {"points":[{station_id,fuel_type,tick,actual,p50,p10,p90}]} → [{…,"z","is_anomaly"}]
GET  /models          → registry listing
POST /models/activate {"name","version"}   (operator rollback of model)
GET  /health/live, /health/ready (ready = a model is loaded), /metrics
```
p95 latency target for `/forecast` (12 series × 48 h): < 150 ms. Pre-compute features vectorised with numpy.

### 1.5 Prediction quality monitoring (live)
After each tick the engine compares realised demand against the p50 forecast made 1, 4 and 12 ticks earlier
(stored in `forecasts`). Metrics: `fsip_forecast_abs_error_liters{horizon}` histogram,
`fsip_forecast_interval_coverage` gauge (rolling 96 ticks), `fsip_forecast_wape` gauge.
**Drift detection:** if rolling WAPE > 2× backtest WAPE for 8 ticks → alert `MODEL_DRIFT` (WARNING) and switch the
engine to the seasonal model automatically (it adapts faster through its `level` term). Record an incident.

---

## 2. Risk engine (projected inventory, stockout ETA, probability)

### 2.1 Deterministic projection (per s,f; k = 1..H)
```
inv[0] = current station inventory
arrivals[k] = sum of our IN_TRANSIT/PENDING allocations to (s,f) with expected arrival at t+k
              (PENDING: departs next tick → arrival t + 1 + transit_ticks - 1; verify the exact offset in
               phase 01 with a test and encode it in ONE helper function `expected_arrival_tick()`)
inv[k] = min(capacity, max(0, inv[k-1] + arrivals[k] - demand_p50[k]))   # station OUTAGE: demand served=0
overflow[k] = max(0, inv[k-1] + arrivals[k] - demand[k] - capacity)
hours_to_stockout = first k where inv[k] == 0, × tick_minutes/60 (∞ if none within H)
```
The same is done for **depots**: `inv_d[k] = inv_d[k-1] + supply_arrivals[k] (status SCHEDULED/DELAYED, at
planned_tick) - planned_outflow[k]`, clipped at capacity (clipping = predicted supply waste → alert
`DEPOT_OVERFLOW_RISK`, which recommends pushing stock downstream early).

### 2.2 Stockout probability (Monte Carlo)
- N = 300 samples. For each sample, draw each future tick's demand from the forecast distribution (triangular or
  normal fitted from p10/p50/p90, truncated at 0), with an optional correlated shock
  (`shock ~ N(1, 0.05)` shared over the horizon) to capture level uncertainty.
- `P(stockout within L)`, where L = lead time = the minimum transit over available routes to s + 1 tick of decision
  latency + 2 ticks of buffer. Also `P(stockout within H)`.
- Vectorise with numpy: shape (N, H) per series → 12 series → trivial cost.

### 2.3 Risk level
```
CRITICAL : stockout_prob_L ≥ 0.5  OR hours_to_stockout ≤ lead_time_hours            OR station OUTAGE
HIGH     : stockout_prob_H ≥ 0.5  OR hours_to_stockout ≤ lead_time_hours + 4h
MEDIUM   : stockout_prob_H ≥ 0.2  OR fill_pct (incl. in-flight) < 0.35
LOW      : otherwise
```
`drivers` (for explanations): list of the signals that pushed the level up, e.g. `demand_multiplier=1.8 (spike)`,
`route-gazipur-mirpur DISRUPTED`, `depot PETROL projected 0 in 9h`, `forecast p90 > p50 by 25%`.

### 2.4 Alerts produced
`STOCKOUT_RISK` (HIGH → WARNING, CRITICAL → CRITICAL), `STOCKOUT_OCCURRED` (unmet_liters > 0 on a new row),
`DEPOT_DEPLETION_RISK`, `DEPOT_OVERFLOW_RISK`, `SUPPLY_DELAY`, `ROUTE_DISRUPTED`, `STATION_OUTAGE`,
`DEMAND_ANOMALY`, `MODEL_DRIFT`, `DATA_QUALITY`, `ALLOCATION_FAILED`, `OVERFLOW_LOSS`.
Dedupe by fingerprint; auto-resolve when the condition clears for 2 consecutive cycles.

---

## 3. Detection (anomalies, bottlenecks, disruptions)

| Detector | Signal | Method | Output |
|---|---|---|---|
| Demand anomaly | new demand rows | z = (actual − p50)/σ, σ from (p90−p10)/2.56. Point anomaly \|z\| > 3. **CUSUM** on standardized residuals (k=0.5, h=5) for sustained shifts | `DEMAND_ANOMALY` alert. If a demand_spike event explains it → severity INFO and the message says "explained by event #id" |
| Inventory anomaly | station inventory delta per tick | expected Δ = arrivals − served (served from demand rows). Residual > max(50 L, 5%) → anomaly | `DATA_QUALITY` or `INVENTORY_ANOMALY` (possible leak/data error) |
| Supply delay | supply-arrivals | status DELAYED, or planned_tick < t and not ARRIVED | `SUPPLY_DELAY` with the depot projection impact (hours of cover lost) |
| Route/station/depot disruption | statuses + events | transitions AVAILABLE→DISRUPTED etc. Scheduled events with start within H | `ROUTE_DISRUPTED` / `STATION_OUTAGE` / `DEPOT_CONSTRAINED`, plus a "pre-emptive" alert for SCHEDULED events |
| Bottleneck | dispatch utilisation | depot dispatch used ≥ 90% for 3 consecutive ticks while CRITICAL risks exist | `BOTTLENECK` |
| Regional emerging disruption | aggregate | ≥ 2 stations in the same region HIGH+ within 4 ticks | `REGIONAL_RISK` incident |
| Data quality | validation | pydantic failures, NaN, negative, missing fuel keys, tick going backwards (without reset) | `DATA_QUALITY` + `fsip_sim_invalid_responses_total` |

Detection metrics: `fsip_anomalies_detected_total{type}`, `fsip_alerts_open{category,severity}`,
`fsip_shortage_alert_rate` (alerts per 96 ticks).

---

## 4. Decision engine

### 4.1 Shared feasibility rules (`decision/constraints.py`). EVERY policy and every manual line must pass these
1. Route exists, `route.source == depot`, `route.destination == station`.
2. Route AVAILABLE **now**, and **no SCHEDULED/ACTIVE `route_disruption` covering tick t+1** that includes this route
   (empty `route_ids` = all routes). Otherwise the line would FAIL at departure and the fuel is lost.
3. Depot status in {OPEN, CONSTRAINED}. Station status OPEN (the simulator rejects otherwise). Note that we cannot
   pre-ship to a station that is in OUTAGE.
4. `quantity ≤ route.max_shipment`. Split larger needs into multiple lines (allowed).
5. `quantity ≤ depot.inventory[f] − reserve[d,f]` where `reserve = DEPOT_RESERVE_FRACTION × capacity` (default 0, see §4.3 note). During
   scarcity mode the reserve is set by the fair-share plan instead.
6. Σ quantities from depot this tick (all fuels, including lines already submitted this tick) ≤
   `dispatch_capacity_per_tick` (× 0.5 if CONSTRAINED, our own policy).
7. `station.inventory[f] + quantity ≤ capacity[f]` (simulator check), **and** projected inventory at arrival +
   quantity ≤ capacity × 0.98 (our overflow-waste check, which includes in-flight shipments and forecast demand until arrival).
8. Minimum line size 300 L (avoid noise shipments) unless the station is CRITICAL.
9. Not stale: if data is stale/degraded → lines may be proposed but **never auto-executed**.

### 4.2 Heuristic policy (fallback, phase 04). Deterministic and fast
```
for each (s,f) sorted by (risk level desc, hours_to_stockout asc):
    proj = inventory + inflight
    if proj/capacity < REORDER_FILL (0.60) or risk ≥ HIGH:
        need = STATION_TARGET_FILL × capacity − projected_inventory_at_arrival(best route)
        for route in available routes to s sorted by (transit asc, depot days-of-supply desc):
            q = min(need, route.max_shipment, depot available after reserve, depot dispatch left,
                    capacity room at arrival)
            if q ≥ 300: add line, update local copies (depot inv, dispatch used, inflight); need -= q
            if need ≤ 0: break
```
Measured: this policy (minus some checks) reached service level 1.00 in baseline and 0.992 in the crisis (see 02 §9).

### 4.3 Optimizer policy (phase 08): rolling-horizon LP

> **Benchmark-driven corrections (2026-11-19). These override the defaults below where they differ.** The tested
> implementation is `reference/policy_benchmark.py` (see `14` §5):
> - The horizon is **96 ticks** (checkpoints up to 96, future slots up to 64), not 48. A 12 h horizon cannot see regional imbalance.
> - **Rolling safety stock at every checkpoint is mandatory** (without it the SL dropped to 0.963 on day 1).
> - The **terminal fill target is 0.9 × capacity** (the simulator does not price transport, so stock downstream is pure buffer).
> - The **depot reserve is 0 by default** (`DEPOT_RESERVE_FRACTION=0`). A permanent 2% reserve stranded ~8,000 L after supply
>   ended. Reserves are a supervisor action during scarcity (F-RESERVE).
> - Weights used: unmet 10, overflow 40, safety 2, terminal 0.5, depot clipping 20, transit 0.002, fairness z 5.
> - Solver: PuLP **2.9.0** + CBC, `timeLimit=5`; p95 solve+build ≈ 45-70 ms.
Solve every cycle and execute only this tick's shipments (model-predictive control).

**Decision variables**
- `x[r,f] ≥ 0`: liters shipped on route r for fuel f **this tick** (integer splits handled afterwards).
- `y[r,f,j] ≥ 0` for planning steps j = 1..J (J = 4 "future dispatch slots" at ticks t+4, t+8, t+12, t+16):
  tentative future shipments. They let the LP plan ahead (e.g. not draining a depot now that is needed later). Not executed.
- `u[s,f,m] ≥ 0`: shortfall (unmet demand) at checkpoint m (checkpoints every 4 ticks up to H).
- `o[s,f,m] ≥ 0`: overflow at checkpoint m (should be ~0).
- `z ≥ 0`: max-min fairness variable (scarcity mode only).

**Projected station inventory at checkpoint m (linear)**
```
I[s,f,m] = inv_now + inflight_arriving_by(m) + Σ_r x[r,f]·[t+1+transit_r ≤ T_m] + Σ_{r,j} y[r,f,j]·[arrive ≤ T_m]
           − D50[s,f, ≤T_m]  (cumulative p50 demand)  + u[s,f,m] − o[s,f,m]
0 ≤ I[s,f,m] ≤ capacity
```
(`u` lets inventory "not go negative", so u represents unmet liters. `o` represents liters that would overflow.)

**Constraints**
- Depot fuel: `Σ_r∈out(d) x[r,f] + Σ y ≤ inv_d[f] − reserve + supply_arriving_before(slot)` for each slot.
- Dispatch: `Σ_{r∈out(d),f} x[r,f] ≤ dispatch_left[d]`; same per future slot with full dispatch cap.
- Route cap: `x[r,f] ≤ route.max_shipment × n_splits_allowed (2)`; `x[r,f] = 0` if the route fails rule 2;
  `x[r,f] = 0` if the station is not OPEN.
- Station-capacity at creation: `x[r,f] ≤ capacity − inventory_now` (sim rule).

**Objective (minimise)**
```
  Σ_{s,f,m}  W_unmet · prio[s] · decay(m) · u[s,f,m]               # unmet demand, earlier = worse
+ Σ_{s,f,m}  W_overflow · o[s,f,m]                                  # wasted fuel (W_overflow > W_unmet)
+ Σ_{r,f}    W_transit · transit_r · (x[r,f] + Σ_j y[r,f,j])         # prefer short routes
+ Σ_{s,f}    W_safety · max(0, safety_stock[s,f] − I[s,f,M])         # end-of-horizon safety stock (linearised via aux var)
− W_fair · z                                                        # scarcity mode: maximise min cover ratio
```
Default weights: `W_unmet=10, W_overflow=12, W_transit=0.01, W_safety=1, W_fair=50`, `decay(m)=1.0 → 0.6`
linearly. `prio[s]` = 1.0 by default; configurable per station in `config/priorities.yaml` (e.g. a hypothetical
"hospital corridor" can be weighted 1.5). Document weights in the UI ("Policy settings").

**Scarcity mode** (activated when the network-wide projected supply for fuel f over the next 96 ticks < projected
demand): add `cover[s,f] = I[s,f,M] / D50[s,f,≤M] ≥ z` for all s, so the LP maximises the minimum coverage
(fair rationing across stations and regions). The UI shows a "SCARCITY MODE: PETROL" banner with the reason.

**Post-processing**
- Split `x[r,f]` into lines ≤ max_shipment, round to 10 L, drop lines < 300 L (unless CRITICAL).
- Re-run `constraints.py` on every line. Drop and log any violation. **Never send an infeasible line.**
- Solver time limit 2 s (`PULP_CBC_CMD(timeLimit=2, msg=False)`). On timeout/infeasible/error →
  heuristic fallback, `fsip_fallback_activations_total{component="optimizer"}` += 1, decision.fallback_reason set.

### 4.4 Confidence score (0-1) per decision
```
confidence = 0.4·forecast_quality + 0.2·data_freshness + 0.2·solver_status + 0.2·(1 − twin_uncertainty)
forecast_quality = clip(1 − rolling_WAPE/0.5, 0, 1); data_freshness = 0 if stale else 1;
solver_status = 1 optimal, 0.6 heuristic fallback, 0.3 forecast fallback;
twin_uncertainty = (stockout_prob_after_p90 − stockout_prob_after_p10)
```
Confidence < `AUTO_APPROVE_MIN_CONFIDENCE` → always requires human approval ("Low confidence → human review").

### 4.5 Approval / execution policy
| Condition | Action |
|---|---|
| mode = advisory | all decisions → PENDING_APPROVAL |
| mode = autopilot AND confidence ≥ 0.7 AND every line ≤ 7000 L AND data not stale AND no CRITICAL data-quality alert | auto-execute; record `approved_by="autopilot"` |
| otherwise | PENDING_APPROVAL |
| recommendation not acted on by `expires_at_tick` (t + 2) | EXPIRED; the next cycle re-plans with fresh data |
| operator approves an expired/stale decision | api returns 409 `RECOMMENDATION_EXPIRED`, UI offers "Re-plan" |

Executor: re-validate with the latest state, POST each line with its idempotency key, retry on 503/timeout with the
**same** key (safe), map 409 codes to line statuses, and never retry a 409 with the same body (except
DISPATCH_CAPACITY_EXCEEDED → requeue next tick as a new line with a new key).

---

## 5. Shadow twin (what-if and impact, the "Simulate" step)

`decision/twin.py`: a pure-python, side-effect-free re-implementation of world dynamics using the facts in
`02_SIMULATOR_FACTS.md` (demand from forecast, arrival timing, overflow clipping, outage = served 0, departure
failure if the route is disrupted, supply arrivals with delays). Inputs: current state + candidate lines + optional
hypothetical events. Output per (s,f): inventory trajectory p10/p50/p90, stockout prob, unmet liters, overflow
liters over H. Uses the same Monte Carlo samples as the risk engine.

Uses:
1. **Expected impact** on each recommendation: `stockout risk 72% → 19%, unmet 2,300 L → 150 L`.
2. **What-if page**: the operator picks a hypothetical event (e.g. "route-gazipur-mirpur disrupted for 24 ticks",
   "Dhaka demand ×1.8") and sees the projected impact with and without the recommended plan.
3. **Alternatives**: the twin evaluates 2 alternatives (heuristic plan, do-nothing) so the explanation shows
   "why this and not that".

Twin calibration test (phase 08): run the twin and the real simulator side by side for 96 ticks with the same
allocations. Mean abs error of station inventory must be < 3% of capacity (the noise makes exact equality
impossible; deterministic parts must match).

---

## 6. Optional: reinforcement learning (phase 16 only)
Formulation from the brief: state = inventory, inflight, forecast, depot inv, route status; action = refill
quantities per (route,fuel) bucketed {0, 25, 50, 75, 100% of max}; reward = −unmet − 1.2·overflow − 0.01·transit.
Train PPO (stable-baselines3) **inside the shadow twin** (fast), evaluate in the real simulator via the harness.
Only ship it if it beats the heuristic in the harness. Otherwise report the honest comparison (this is still
valuable: "we tried RL, here is why LP wins").

---

## 7. Explanations

### 7.1 Structured explanation (always present) stored in `decisions.explanation`
```json
{
  "headline": "Ship 5,000 L DIESEL from depot-gazipur to station-mirpur",
  "why_at_risk": [
    {"signal":"projected_stockout","value":"6.2 h","detail":"inventory 8,400 L vs expected demand 11,900 L in 12 h"},
    {"signal":"demand_multiplier","value":1.8,"detail":"demand_spike event #3 (Dhaka) active until tick 160"},
    {"signal":"route_status","value":"route-gazipur-mirpur AVAILABLE","detail":"alternative route patiya-mirpur is 4 ticks"}
  ],
  "constraints": ["depot-gazipur dispatch left 7,000/12,000 this tick", "station capacity room 6,600 L at arrival"],
  "expected_impact": {"stockout_prob_before":0.72,"stockout_prob_after":0.19,"unmet_before":2300,"unmet_after":150},
  "confidence": 0.81, "confidence_factors": {...},
  "alternatives": [
     {"name":"patiya→mirpur 5,000 L","stockout_prob_after":0.34,"why_not":"4-tick transit arrives after the peak"},
     {"name":"do nothing","stockout_prob_after":0.72}
  ],
  "policy": "optimizer@1.0.0", "forecast_model": "lightgbm@1.2.0", "degraded": false
}
```
The UI renders this as an "Explain" card that matches the brief's alert example (§9 of the brief).

### 7.2 Narrative (LLM, optional)
- Only if `ANTHROPIC_API_KEY` is set. Model `LLM_MODEL` (default `claude-haiku-5-5`), timeout 8 s, max_tokens 400.
- The prompt contains ONLY the structured explanation JSON + a system instruction: "You are an operations analyst.
  Explain this SIMULATED fuel allocation recommendation in ≤ 120 words for a control-room operator. Use only the
  facts given. Do not invent numbers."
- **The LLM never decides or modifies allocations.** It only explains, summarises and answers questions.
- Fallback: a Jinja2 template that produces the same structure in plain sentences. Metrics:
  `fsip_llm_requests_total{result}`, `fsip_llm_latency_seconds`.
- Uses: decision narrative, incident summary ("what happened in the last 2 hours"), shift summary, and the
  **Ops Assistant** (`/api/assistant/ask`). The assistant gets a compact JSON context (current state summary, open
  alerts, last 10 decisions, active events) and answers grounded questions like "Why is Cox's Bazar at risk?".
  Rule-based fallback: keyword intent → canned query over the state (e.g. "risk" → top risks list).

## 8. Intelligence metrics (Prometheus, required for the "Intelligence" observability row)
`fsip_forecast_abs_error_liters`, `fsip_forecast_wape`, `fsip_forecast_interval_coverage`,
`fsip_model_confidence` (last decision), `fsip_decisions_total{policy,status}`, `fsip_decision_latency_seconds`,
`fsip_solver_duration_seconds`, `fsip_fallback_activations_total{component}`,
`fsip_shortage_alerts_total{severity}`, `fsip_recommendations_pending`, `fsip_stockout_prob{station,fuel}`,
`fsip_service_level`, `fsip_unmet_liters_total`, `fsip_overflow_liters_total`, `fsip_allocation_failures_total`.
