# 07: Frontend (Operator Console) Design

Stack: React 18 + TypeScript + Vite + TailwindCSS + Recharts + TanStack Query + React Router + `lucide-react`
icons. Dark "control room" theme by default with a light-mode toggle. Desktop-first (1440 px), usable down to
1024 px. A persistent header strip says **"SIMULATED ENVIRONMENT – NOT REAL FUEL INFRASTRUCTURE"**.

## 1. Global layout
```
┌ Header: logo · sim tick + sim time + RUNNING/PAUSED · Engine mode badge (NORMAL/DEGRADED/SAFE_HOLD)
│         · Autopilot toggle (operator) · Policy (optimizer/heuristic) · alerts bell (count) · user/role
├ Degraded banner (only when degraded): reasons list, e.g. "Prediction service DOWN – fallback model active"
├ Left nav: Overview · Network · Risk · Recommendations · Decisions · Alerts · What-if · Assistant · System Health · Control (demo)
└ Main content
```
Live data: one `EventSource('/api/stream')` hook (`useLiveState`) updates the TanStack Query cache. If SSE fails →
poll `/api/state` every 2 s, with a small "polling" indicator.

## 2. Pages

### 2.1 Overview (default)
- KPI cards: Service level (sim metric), Unmet liters (24 h), Stations at risk (HIGH+), Open alerts, Pending
  recommendations, Allocation failures, Overflow waste (our estimate), Engine mode.
- **Network diagram** (SVG, hand-laid positions): 2 depots on the left, 4 stations on the right, 6 route edges.
  Edge colour = route status (green AVAILABLE, red DISRUPTED, dashed amber = disruption scheduled). Animated dots on
  edges for IN_TRANSIT shipments (position = progress by tick). Node rings = fill % per fuel (3 mini bars), node
  border colour = worst risk level. Clicking a node opens its detail drawer.
- Risk heatmap: 4 stations × 3 fuels cells showing hours-to-stockout and probability, coloured by level.
- Mini timeline: last 96 ticks of service level + active events as shaded bands.
- "Top 3 actions now": the highest-impact pending recommendations with Approve buttons.

### 2.2 Network / Inventory
Table + charts per station and depot: inventory per fuel (time series with capacity line), in-flight, projected
inventory (p10-p90 band) for the next 48 ticks, demand actual vs forecast. Depot view shows the supply arrivals
timeline (scheduled/delayed/arrived markers) and days-of-supply.

### 2.3 Risk
Sortable list of (station, fuel) rows: level, hours-to-stockout, probability, drivers chips, a "Why?" popover
(drivers), and the linked recommendation. Filter by region and fuel. Region roll-up cards (Dhaka / Chattogram).

### 2.4 Recommendations (inbox), the core decision UX
Each card is modelled on the brief's ALERT example:
```
[CRITICAL] station-mirpur · DIESEL           confidence 0.81 · optimizer@1.0.0 · expires in 2 ticks
Projected stockout: 6.2 h   Current: 8,400 L   Expected demand (12 h): 11,900 L
Recommended: 5,000 L from depot-gazipur via route-gazipur-mirpur (arrives tick 142)
Expected result: stockout risk 72% → 19%   unmet 2,300 L → 150 L
[Approve] [Reject…] [Modify qty] [Explain ▸] [What-if ▸]
```
- "Explain" expands the structured explanation: why at risk, constraints, alternatives (with their impact), confidence
  factors, data freshness, and the narrative (LLM or template, labelled as such).
- Multi-line decisions show all lines with per-line approve checkboxes.
- "Modify qty" validates client-side (max_shipment, capacity) and server-side.
- Reject requires a reason (dropdown + text), which is stored in the audit log.
- Approval needs the operator role (token). Viewer role sees read-only.

### 2.5 Decisions (history / audit)
Table: time/tick, policy, trigger, status, lines, liters, approved by (operator/autopilot), outcome (arrived on
time? received liters vs sent → overflow), impact predicted vs realised (realised = stockout avoided yes/no). Filter
+ CSV export. Click → detail page.

### 2.6 Alerts
Tabs: Open / Acknowledged / Resolved. Columns: severity, category, entity, message, opened tick, age, related
decision. Ack button. Incident timeline view for system incidents (F1-F18) showing detect → mitigate → recover
timestamps (great for the demo).

### 2.7 What-if
Form: choose hypothetical events (type, targets, start, duration, parameters) and/or edit candidate shipments → "Run
projection" → charts comparing **do nothing / recommended plan / custom plan** (unmet liters, stockout prob per
station, overflow). Uses `/api/whatif` (shadow twin, no side effects). Clearly labelled "projection, not executed".

### 2.8 Assistant
Chat-like panel, but grounded: suggested questions ("What changed in the last hour?", "Why is Cox's Bazar at risk?",
"Summarise the current crisis"). Shows the context sources used (alerts/decisions ids) under each answer. Badge: "LLM"
or "Rule-based fallback". Also a "Generate shift summary" button.

### 2.9 System Health
The brief's §16 panel: component list with status pills (Healthy/Degraded/Down), breaker states, p95 latency, error
rate, SSE state, ticks behind, model version + fallback status, versions/git sha. Embedded Grafana links (open in a
new tab), incident list.

### 2.10 Control (demo / testing tools; hidden unless `DEMO_CONTROLS=true`)
Big labelled buttons: Run / Pause / Step ×1 / Step ×10 / Reset simulator; crisis presets (Demand spike Dhaka,
Route disruption, Depot constraint, Shipment delay, Supply shortfall, Combined crisis); chaos (Sim unavailable 60 s,
Error rate 30%, Latency 1.5 s, Stale data, SSE disconnect, Clear faults). A red notice states "These call organizer
admin endpoints – testing only". Every click goes to the audit log.

### 2.11 Pages added by the advanced tracks (`12`, phases 17-20)
| Page / panel | Feature | Content |
|---|---|---|
| Network fuel budget (Overview card) | F-COVER (T1) | days of cover per fuel/region; "supply schedule ends at t=212" marker; scarcity threshold line |
| Human-AI teaming (System/Decisions) | F-TRUST (T1) | acceptance/override/rejection rates, time-to-decision |
| SLO panel (System Health) | F-SLO (T1) | 4 SLOs with error-budget bars + burn rate; "autopilot frozen by error-budget policy" notice |
| Break-glass button (Header, degraded only) | F-BREAKGLASS (T1) | opens `/breakglass/latest.html` |
| Reconciliation report (Decisions tab) | F-RECON (T1) | sent vs received per line, loss reasons, station variance CUSUM chart |
| Replay | F-REPLAY (T2) | timeline scrubber reusing the Overview components |
| Scarcity & Fairness | F-FAIR/F-RATION-POLICY (T2) | policy selector (supervisor), Jain index, per-station SL bars, policy comparison table |
| Resilience | F-N1 (T2) | N-1 heatmap, TTS vs TTR bars, critical assets |
| Playbooks (right drawer) | F-PLAYBOOK (T2) | the active playbook checklist with timestamps |
| Ledger | F-LEDGER (T2) | entries + "verified ✓" badge + export |
| Login + role badge | F-RBAC (T2) | viewer without login; operator/supervisor via login; four-eyes status on cards |
| Map toggle | F-MAP (T3) | Leaflet map ↔ SVG schematic |

## 3. Components to build
`Header, DegradedBanner, KpiCard, NetworkGraph, RiskHeatmap, InventoryChart (with capacity + band), ForecastChart,
RecommendationCard, ExplainPanel, DecisionTable, AlertList, IncidentTimeline, HealthGrid, StatusPill, WhatIfForm,
ComparisonChart, AssistantPanel, ControlPanel, ConfirmDialog, Toasts`.

## 4. Quality bar
- No layout shift on live updates. Numbers formatted with thousands separators + units (L, h, %).
- Colour + icon + text for status (accessible, not colour-only).
- Loading skeletons; error states with retry; empty states with an explanation.
- `data-testid` on key elements for Playwright smoke tests (phase 12).
- Every timestamp shows both sim time and tick (e.g. "Day 2 11:00 · t=140").
