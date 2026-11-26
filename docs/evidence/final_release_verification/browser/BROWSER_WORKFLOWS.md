# FSIP Live Browser Verification — BROWSER_WORKFLOWS

**Date:** 2026-11-26  
**Target:** http://localhost:3000  
**Method:** cursor-ide-browser MCP (navigate, snapshot, CDP, screenshots)  
**Evidence:** PNG screenshots in this directory (copied from MCP temp store).

## Pages visited

| Route | Final URL | Load result | Screenshot |
|-------|-----------|-------------|------------|
| `/` | `/overview` | Pass — KPIs, network graph, heatmap, inventory | `01-overview.png` |
| `/network` | `/network` | Pass — stations/depots/routes | `02-network.png` |
| `/risk` | `/risk` | Pass — risk table + filters | `03-risk-chattogram.png` |
| `/recommendations` | `/recommendations` | Pass — pending rec with human gate | `04-recommendations.png` |
| `/decisions` | `/decisions` | Pass — ledger UI (0 rows during run; no blank shell) | `05-decisions.png` |
| `/whatif` | `/whatif` | Pass — form + scenario results after submit | `06-whatif-results.png` |
| `/what-if` | `/overview` | **Fail** — no what-if page; SPA fallback to overview | — |
| `/assistant` | `/assistant` | Pass | `07-assistant-health.png` |
| `/health` | `/health` | Pass — full observability panel | `10-system-health-loaded.png` |
| `/system` | `/health` | Pass — intentional redirect (`App.tsx`) | `10-system-health-loaded.png` |
| `/system-health` | `/overview` | **Fail** — no alias; fallback to overview | — |
| `/alerts` | `/alerts` | Pass | `08-alerts.png` |
| `/control` | `/control` | Pass — simulator / chaos controls | `09-control.png` |

## Critical checks

| # | Check | Result | Notes |
|---|--------|--------|-------|
| 1 | No Sign In button/modal | **PASS** | `hasSignIn: false` on all sampled pages; green **Operator** chip present |
| 2 | Risk filter Dhaka / Chattogram | **PASS** | Dhaka rows show Dhaka stations; Chattogram shows Karnaphuli Highway, etc. |
| 3 | Recommendations approve/reject | **PASS** | **Reject** enabled; **Approve & Dispatch** visible (disabled until qty validation satisfied) |
| 4 | What-If baseline submit | **PASS** | “Run What-If Simulation” completed; scenario cards `do nothing`, `recommended`, `custom` |
| 5 | Assistant health question | **PASS** | Quick prompt answered: Degraded / tick 56 / 98.84% SL (RULES) |
| 6 | System health notifier status | **PASS** | Notification Dispatcher shows **Unconfigured** (truthful); platform **Degraded** matches banner |
| 7 | Mobile ~390px header/assistant | **PASS** | CDP `390×844`; header & h1 not clipped (`clipped: false`) | `11-mobile-assistant-390.png` |

## Console / network observations

- **CDP `Log.enable`:** No blocking console errors surfaced in automation output.
- **Failed HTTP (CDP `performance` resource entries, status ≥ 400):** **None observed** during session.
- **Environmental (not UI defects):** Simulator **PAUSED** / **SYSTEM DEGRADED** (`model_drift_fallback`, `error_budget_exhausted`) — UI reflects state consistently.

## Screenshot inventory

| File | Description |
|------|-------------|
| `01-overview.png` | Home / overview dashboard |
| `02-network.png` | Network & inventory |
| `03-risk-chattogram.png` | Risk with Chattogram filter |
| `04-recommendations.png` | Pending recommendation + Reject/Approve |
| `05-decisions.png` | Decisions ledger |
| `06-whatif-results.png` | What-if form (results section in DOM after run) |
| `07-assistant-health.png` | Assistant health Q&A |
| `08-alerts.png` | Alerts & system incident |
| `09-control.png` | Control (demo) / simulator |
| `10-system-health-loaded.png` | System health (loaded) |
| `10-system-health.png` | Transient loading frame (retained for audit) |
| `11-mobile-assistant-390.png` | Mobile assistant viewport |

## Summary

| Area | Verdict |
|------|---------|
| Primary app routes (`/overview`, `/network`, `/risk`, `/recommendations`, `/decisions`, `/whatif`, `/assistant`, `/health`, `/alerts`, `/control`) | **PASS** |
| Critical UX gates (no sign-in, operator chip, filters, human approve/reject, what-if, assistant, health, mobile) | **PASS** |
| Route aliases `/what-if`, `/system-health` | **FAIL** (redirect to overview) |
| **Overall release browser gate** | **CONDITIONAL PASS** — ship canonical paths; document or add aliases for hyphenated URLs if external docs link to them |