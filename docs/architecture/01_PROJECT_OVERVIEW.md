# 01: Project Overview and Winning Strategy

## 1. The challenge in one paragraph

Build and operate an intelligent **decision-support platform** for a simulated Bangladeshi fuel supply network
(2 regions, 2 depots, 4 stations, 6 routes, 3 fuels: DIESEL/PETROL/OCTANE). The organizers provide a deterministic
simulator (Docker image, REST + SSE). Our platform must **observe** the network, **detect** risks, **predict**
shortages, **decide/recommend** allocations (depot → station shipments), **simulate** their impact, **act** (with
human review), **monitor** itself, and **recover** from domain crises *and* software failures. It must be deployable
(`docker compose up`), observable, load-tested and demoed live. Organizers will inject surprise events during
development and judging.

> "Your job is not only to build the system. Your job is to keep it working."

The required loop: **Observe → Detect → Predict → Decide → Simulate → Act → Monitor → Recover**

## 2. Judging weights (from the brief, §24)

| Criterion | Weight | What wins it in our plan |
|---|---:|---|
| Working Product & UX | 20% | Polished operator console: live map/network view, risk board, recommendation inbox with approve/reject, decision history, system health |
| Intelligence & Decision Quality | 20% | Forecast + stockout probability + anomaly detection + LP optimizer, with a **measured** comparison vs greedy and do-nothing baselines |
| Architecture & Integration | 15% | Clean service split, resilient simulator client, REST-as-truth + SSE-as-hint, idempotent writes, reset detection |
| DevOps & Engineering Quality | 15% | Compose, CI/CD (GitHub Actions), tests, versioned images, rollback, `.env` config |
| Resilience & Incident Response | 15% | Explicit failure matrix, circuit breakers, fallback forecaster, fallback allocator, degraded mode, chaos scripts, recovery shown on screen |
| Observability & Performance | 10% | Prometheus + Grafana + Loki, intelligence metrics, alert rules, k6 report |
| Demo & Problem Understanding | 5% | Scripted 14-step demo, clear explanation of constraints and trade-offs |

## 3. Deliverables (brief §20) mapped to our outputs

| # | Required deliverable | Where it is produced |
|---|---|---|
| 1 | Working application | Whole stack (phases 00-11) |
| 2 | Source repository | Repo root + README (phase 15) |
| 3 | Simulator integration | `services/engine/app/sim/` (phase 01) |
| 4 | Intelligence component | `services/intelligence` + `services/engine/app/decision` (phases 05-09) |
| 5 | Operator interface | `frontend/` (phases 03+) |
| 6 | Architecture diagram | `docs/architecture.md` + `docs/architecture.png` (phase 15) |
| 7 | Deployment | `docker-compose.yml`, `Makefile`, CI (phases 00, 13) |
| 8 | Observability evidence | Grafana dashboards JSON + screenshots in `docs/evidence/` (phase 11) |
| 9 | Resilience demonstration | `scripts/chaos/*.sh` + recorded results in `docs/evidence/resilience.md` (phase 10) |
| 10 | Load-test evidence | `loadtest/` + `docs/evidence/loadtest-report.md` (phase 14) |
| 11 | Final demo | `09_DEMO_RUNBOOK.md` → `docs/demo-runbook.md` (phase 15) |

Recommended extras we also deliver: CI/CD, automated tests, experiment tracking (simple: JSON/MLflow-lite file
registry), model versioning, decision audit history, deployment versioning, simulation replay (evaluation harness),
scenario configuration, automated fallback, rollback.

## 4. Key insights from our analysis (these drive the strategy)

These were **measured** against the real simulator image (details in `02_SIMULATOR_FACTS.md`):

1. **Doing nothing is a disaster.** With no allocations, service level drops to 0.88 after day 1, 0.46 after
   day 2 and 0.31 after day 3. Stations start running dry at tick 66 (16.5 simulated hours).
2. **A simple greedy refill policy already reaches service level 1.00 in the calm baseline** and 0.992 under a
   heavy combined crisis over 3 days. So **raw ML accuracy will not separate teams**. The differences will
   come from:
   - behaviour during crises and **after supply runs out** (scarcity/rationing),
   - **zero waste**: no failed allocations and no overflow,
   - **explainability, the operator experience, and resilience/observability engineering**.
3. **Supply stops at tick 212** (≈ 2.2 simulated days). After that, total fuel is finite (≈ 6 days of total demand
   for diesel, ≈ 5.5 for petrol, ≈ 6.5 for octane), and **Chattogram petrol runs out first** (≈ 4.2 days of
   regional cover). Smart **cross-region balancing** (routes `gazipur→karnaphuli`, `patiya→mirpur`) and **fair
   rationing** are where a decision engine can visibly beat greedy.
4. **Hidden waste traps** (not documented in the PDF):
   - A FAILED allocation (route disrupted at departure) **does not refund** depot inventory, so the fuel is lost.
   - Fuel arriving at a full station **overflows and is lost** (the capacity check at creation ignores in-flight
     shipments).
   - Supply arriving at a full depot is clipped at capacity (lost).
   These make **projected-inventory accounting** (including in-flight shipments) and **disruption-aware routing**
   essential.
5. **Measured LP vs greedy** (`14` §5): while supply flows they are equal (±0.1%). After supply ends, the tuned LP wins on
   all scenarios (+0.3 to +0.6 pts service level, **+2.9 to +8.1 pts for the worst-served station**). Doing nothing also
   wastes 73,000 L of supply at full depots. So our intelligence story is **scarcity, fairness and zero waste**, backed by numbers.
6. At default speed (8 ticks/s), one simulated day passes in **12 seconds**. Our engine must coalesce ticks and
   must not assume it sees every tick. For the demo we run slower (`SIMULATION_SPEED=0.5`).

## 5. Winning strategy (what we optimise for)

1. **A real operations console, not a notebook.** Every requirement in brief §6 is visible on screen.
2. **Layered intelligence, all measurable:**
   - Forecasting (seasonal-profile model + gradient boosting, with prediction intervals)
   - Stockout time + probability (Monte Carlo over forecast uncertainty)
   - Anomaly detection (demand shift, unexpected inventory change, supply delay, data quality)
   - Decision engine: **LP rolling-horizon optimizer** with **greedy heuristic fallback**
   - Impact estimation via a **shadow twin** (our own lightweight re-implementation of world dynamics, used for what-if)
   - Explanations: structured reasons and alternatives, with an optional LLM narrative (template fallback)
3. **Human in the loop:** recommendations go to an inbox. Routine low-risk top-ups can be auto-executed when
   "Auto-pilot" is ON. High-impact, low-confidence or degraded-data decisions **always** require approval.
4. **Resilience you can see:** a System Health page shows every dependency, circuit-breaker states, fallback
   activations, and the stale-data flag. A chaos panel runs the failure demos.
5. **Evidence-driven:** an evaluation harness replays scenarios deterministically (`/admin/reset` + `/admin/step`)
   and produces a policy comparison table, which we show the judges.

## 6. Scope guardrails (brief §26)

- Operate only against the simulator. No real infrastructure, credentials or purchases.
- UI must label everything as **SIMULATED**.
- Document assumptions (`10_DECISIONS_AND_ASSUMPTIONS.md` → repo `docs/assumptions.md`).
- Preserve human review for consequential simulated decisions.
