# 17: 7-Day Delivery Timeline (deadline Thu 2026-11-26)

Strategy: **always have a working demo.** Build vertically (end-to-end thin slice first, then deepen). From the end of
Day 2 onward, `main` must always be demoable. Feature freeze: **Wed 25 Nov 18:00**. After that, only fixes, docs and rehearsal.

| Day | Date | Goal (end-of-day demo state) | Phases / features | Hard checkpoint (must be true by evening) |
|---|---|---|---|---|
| D0 | Thu 19 Nov | Plan + skeleton running | P00 | `make up` + `make smoke` green; fixtures captured; **progress update #1** sent |
| D1 | Fri 20 Nov | Engine mirrors the simulator into DB/Redis; api serves state | P01, P02 | `/api/state` live; faults F1/F4/F5 handled at the client level |
| D2 | Sat 21 Nov | **MVP DEMO:** UI shows the network, shortages (naive risk), recommendations, approve → allocation arrives | P03, P04 (+ naive risk from P06.1) | baseline 2 days autopilot SL ≥ 0.99, 0 failures; screenshots; **progress update #2 (MVP)** |
| D3 | Sun 22 Nov | Real intelligence: forecast (seasonal first, then LightGBM if time), Monte Carlo risk, alerts, anomaly basics | P05 (seasonal + intel svc + fallback), P06, P07 (demand, supply delay, data quality) | stockout alert lead ≥ 8 ticks; prediction-service-down fallback works |
| D4 | Mon 23 Nov | Optimizer + twin + explanations + resilience | P08 (LP from the blueprint), P09 (templates; LLM optional), P10 core drills | eval quick_compare LP ≥ greedy; chaos run_all core drills pass; **progress update #3** |
| D5 | Tue 24 Nov | Observability + T2 differentiators | P11, P17 (replay, fairness/scarcity), P18 (N-1, surge, conformal) as far as possible | Grafana 4 dashboards; SLO alerts; scarcity mode demo |
| D6 | Wed 25 Nov | Evidence + delivery | P12 eval report, P13 CI, P14 load test, P15 docs; P19 items only if green | full eval matrix; k6 report; CI green; README; **18:00 freeze**; rehearsal ×2; **progress update #4** |
| D7 | Thu 26 Nov | Submit | buffer, rehearsal #3, backup video, tag `v1.0.0` | submission + **final update** |

## Daily rhythm (for the human team + the AI)
- 09:00: read PROGRESS.md, pick today's phases, write "today's plan" (3 bullets).
- Every 2-3 h: run `make smoke` + the relevant acceptance commands; commit.
- 18:00: stabilise (no new features after 18:00 on any day); fix red tests.
- 20:00: send the progress update (`18_PROGRESS_UPDATES.md`), update PROGRESS.md.

## Cut list (if behind schedule, cut in this order; never cut T1)
1. P16 everything (RL, K8s) → 2. F-SMPC, F-PARETO, F-MAP, F-CANARY → 3. MLflow profile, Jaeger → 4. F-NOTIFY escalation
(keep the webhook) → 5. LightGBM (keep the seasonal + conformal model; it is near the noise floor anyway) → 6. F-RBAC
four-eyes (keep the single operator token) → 7. Loki (keep JSON logs + `docker logs`).
**Never cut:** simulator integration, risk alerts, recommendations + approve, LP or heuristic, resilience drills F1/F4/F5/F7,
system health page, Grafana basic dashboard, k6 report, compose up, README, demo rehearsal.

## Risk register
| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Frontend takes longer than planned | High | High | Tailwind + simple SVG; build components once, reuse on Replay |
| LP numerical/feasibility issues | Medium | High | Tested blueprint in `14` §5; heuristic fallback always on |
| Timescale/Redis Streams learning curve | Medium | Medium | Both have plain fallbacks (plain tables / pub-sub); switch is allowed (log deviation) |
| Demo machine RAM | Medium | Medium | `docker stats`; drop cadvisor/jaeger first |
| Venue internet down | Medium | Medium | All images pre-pulled; map falls back to SVG; LLM falls back to templates |
| Organizer changes the simulator image | Low | High | Nothing hard-coded; run the harness on new image within 1 h |
| Scope creep | High | High | Tiers + cut list + 18:00 rule |
