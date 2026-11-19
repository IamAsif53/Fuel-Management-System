# 22: UI Spec (design tokens, wireframes, components, states, test ids)

Complements `07_FRONTEND_DESIGN.md` (what) with **how it looks and behaves exactly**. Data shapes: `21` §5.

## 1. Design system
### 1.1 Colour tokens (Tailwind `theme.extend.colors`, already in `24` → `frontend/tailwind.config.js`)
| Token | Hex | Use |
|---|---|---|
| `bg` / `bg-panel` / `bg-raised` / `bg-border` | #0B1220 / #111A2E / #17223A / #24314F | page / cards / hover+inputs / borders |
| `ink` / `ink-muted` / `ink-faint` | #E6EDF7 / #93A4C3 / #5B6B8C | text primary / secondary / tertiary |
| `brand` / `brand-soft` | #3B82F6 / #1E3A6B | primary buttons, active nav, links |
| `risk-low/medium/high/critical` | #22C55E / #EAB308 / #F97316 / #EF4444 | risk levels (+ icon + text, never colour alone) |
| `fuel-diesel/petrol/octane` | #38BDF8 / #A78BFA / #F472B6 | fuel series in all charts, fuel chips |
| `state-healthy/degraded/down/unknown` | #22C55E / #EAB308 / #EF4444 / #64748B | component health |
The SIMULATED strip is amber-500 on black text. Dark theme only (judges' screens + control-room look); no light mode needed.

### 1.2 Typography & spacing
Inter (system fallback), base 14 px; page titles `text-xl font-semibold`; card titles `text-sm font-medium text-ink-muted
uppercase tracking-wide`; KPI numbers `text-2xl font-semibold tabular-nums`. Spacing: page padding `p-6`, card `p-4`, grid gap
`gap-3`/`gap-4`, radius `rounded-lg`. Icons: lucide-react 16 px (nav) / 14 px (pills).

### 1.3 Layout
- Min supported width 1280 px (demo laptop/projector); at < 1280 the nav collapses to icons. Desktop-first; no mobile work.
- Shell: SIMULATED strip (20 px) → Header (48 px) → [Nav 224 px | Main scroll] (from `24` App.tsx).
- Header (left→right): logo + "Fuel Ops Console" · `ClockBadge` (clock_label + RUNNING/PAUSED dot) · `EngineModeBadge` ·
  `ScarcityBadge` (only if any fuel is scarce) · spacer · `ConnectionDot` (live/polling/offline) · `AutopilotToggle`
  (operator) · `PolicyChip` · `AlertsBell` (count by severity) · `UserMenu` (role badge, login/logout).
- `DegradedBanner` under the header when `state.degraded || engine_mode != NORMAL`: amber (DEGRADED) or red (SAFE_HOLD /
  NO_FEASIBLE_ROUTES), reasons as bullet chips, data age ("data from t=131, 42 s old"), a Break-glass link.

### 1.4 Formatting (helpers in `src/lib/format.ts`, see `24`)
Liters `12,346 L` · percent `95.3%` · hours `6.2 h` / `30 min` / `> horizon` · sim clock `Day 2 11:00 · t=140` · wall time
`10:16:03` local. Probabilities are shown as percentages. Use tabular numbers everywhere.

### 1.5 Status semantics
| Thing | Visual |
|---|---|
| Risk level | `RiskBadge`: coloured dot + text (`CRITICAL`) + optional hours ("6.2 h") |
| Health | `StatusPill` (icon + text) |
| Route status | edge colour: AVAILABLE ink-faint/green, DISRUPTED red solid, scheduled disruption amber dashed |
| Decision status | `DecisionStatusChip`: PENDING_APPROVAL brand, AWAITING_SECOND_APPROVAL purple, EXECUTED green, REJECTED ink-faint, EXPIRED ink-faint italic, FAILED red, PARTIAL amber |
| Degraded data | everything derived from stale data gets a small amber "stale" chip |

## 2. Wireframes (ASCII; boxes = cards)

### 2.1 Overview (`/overview`)
```
┌ KPI row ──────────────────────────────────────────────────────────────────────────────────────┐
│ [Service level 99.8%] [Unmet 24h 240 L] [Stations at risk 1] [Open alerts 3] [Pending recs 1] │
│ [Failures 0] [Overflow 0 L] [Fairness (Jain) 0.99]                                            │
└───────────────────────────────────────────────────────────────────────────────────────────────┘
┌ Network (NetworkGraph, 60% width) ─────────────────┐ ┌ Risk heatmap (RiskHeatmap) ───────────┐
│  [Gazipur depot]────────────[Mirpur]  ◉ ◉ ◉        │ │          DIESEL   PETROL   OCTANE      │
│        │ ╲  ●→ (in transit)                        │ │ Mirpur   HIGH6.2h  LOW      LOW        │
│        │  ╲────────────────[Tongi]   ◉ ◉ ◉         │ │ Tongi    LOW       MED 14h  LOW        │
│        │   ╲ (4t)                                  │ │ Karnaph. LOW       LOW      LOW        │
│  [Patiya depot]──────────[Karnaphuli] ◉ ◉ ◉        │ │ Cox's B. LOW       LOW      CRIT 3.1h  │
│        ╲───────────────────[Cox's Bazar] ◉ ◉ ◉     │ └───────────────────────────────────────┘
│  legend: ─ available  ━ red disrupted  ┄ scheduled │ ┌ Top actions now (max 3 RecommendationMini) ┐
└────────────────────────────────────────────────────┘ │ CRIT Cox's OCTANE: ship 3,000 L [Approve]  │
┌ Network fuel budget (FuelBudgetCard, F-COVER) ─────┐ └────────────────────────────────────────────┘
│ DIESEL 5.8 d  PETROL 4.1 d ▲scarcity line  OCTANE  │ ┌ Service level timeline (96 ticks, events shaded) ┐
│ "Supply schedule ends at t=212 (in 72 ticks)"      │ └──────────────────────────────────────────────────┘
└────────────────────────────────────────────────────┘
```
NetworkGraph geometry (SVG viewBox 0 0 1000 520): depots at x=140 (Gazipur y=150, Patiya y=380); stations at x=820 (Mirpur
y=70, Tongi y=210, Karnaphuli y=330, Cox's Bazar y=450). Nodes: rounded rect 150×56; inside: name + 3 mini fill bars (fuel
colours, height ∝ fill_pct, red outline if the cell is HIGH+). Edge label = transit ticks. In-transit dots move along the edge at
`progress` (from AllocationView), coloured by fuel, radius 6, tooltip with qty + ETA. Clicking a node opens `EntityDrawer`.
Cross-region edges (gazipur→karnaphuli, patiya→mirpur) are drawn as curves.

### 2.2 Network (`/network`)
A tab per entity type (Stations | Depots | Routes). Station card grid (2 columns): header (name, status, region, profile,
multiplier chip if ≠ 1.0) + `InventoryChart` (3 lines per fuel, dashed capacity line, p10-p90 projected band for the next 48
ticks, a "now" marker, a stockout marker) + a mini table (fuel | inventory | in-flight | fill | cover | risk). Depot card: inventory
chart + supply timeline (markers: SCHEDULED hollow, DELAYED amber, ARRIVED solid) + dispatch used/limit bar + headroom
warning. Routes table: id, depot→station, transit, max, status, scheduled disruption, in-flight count.

### 2.3 Risk (`/risk`)
Filter bar (region, fuel, min level) → a table sorted by severity: [RiskBadge] station · fuel · hours to stockout · P(lead) ·
P(12h) · inventory · in-flight · cover · drivers (chips, max 3, "+n") · linked recommendation (button). Region roll-up cards on top
(Dhaka / Chattogram: worst level, # cells HIGH+, regional cover).

### 2.4 Recommendations (`/recommendations`), the decision UX
```
┌ RecommendationCard ───────────────────────────────────────────────────────────────────────────┐
│ [CRITICAL] station-coxsbazar · OCTANE          conf 0.81 ● optimizer@1.0.0 · expires in 2 ticks│
│ Projected stockout 3.1 h · Current 900 L · Expected demand (12 h) 2,100 L                        │
│ ┌ Lines ───────────────────────────────────────────────────────────────────────────────────────┐ │
│ │ ☑ 3,000 L OCTANE  depot-patiya → coxsbazar  via route-patiya-coxsbazar (3 ticks, ETA t=143) │ │
│ └──────────────────────────────────────────────────────────────────────────────────────────────┘ │
│ Expected result: stockout risk 72% → 19% · unmet 2,300 L → 150 L                                │
│ [▸ Why? (required)]  [What-if ▸]                                                                 │
│ ┌ ExplainPanel (expanded) ─ drivers · constraints · alternatives (for/against) · confidence ─┐  │
│ │ ☐ I reviewed the main risk driver                                                           │  │
│ └─────────────────────────────────────────────────────────────────────────────────────────────┘  │
│ [Approve] (disabled until reviewed if big/low-conf/degraded)  [Modify qty]  [Reject…]           │
└──────────────────────────────────────────────────────────────────────────────────────────────────┘
```
- Sorted by worst level, then expiry. Empty state: "No recommendations. All stations are covered for the next 12 h." + a mini
  risk summary.
- **Modify qty:** inline number input per line; client validation (> 0, ≤ max_shipment, ≤ capacity room shown as a hint);
  server 422 violations are shown inline under the line.
- **Reject:** modal with reason radio (codes from `21` §2.3) + comment (required if OTHER).
- After approve: optimistic status → spinner → the result chip per line (SUBMITTED/PENDING/REJECTED_BY_SIM with code).
- Expired: the card greys out with a "Re-plan" button (POST /plan-now).
- Four-eyes: the chip "Needs supervisor approval (1/2)".

### 2.5 Decisions (`/decisions`)
Table: tick/time · policy · trigger · status chip · lines · liters · approved by (icon: person/robot) · outcome (✓ arrived on
time / ⚠ overflow 173 L / ✗ failed) · predicted vs realised (P18/P19). Row click → `/decisions/:id` (full explanation,
narrative with source label, lines with lifecycle timeline, ledger hash). Filters + "Export CSV" (client-side).

### 2.6 Alerts (`/alerts`)
Tabs Open | Acknowledged | Resolved | Incidents. Alert row: severity icon · category · title · entity · opened (tick, age) ·
[Ack] · related decision link. Grouped by fingerprint (count badge). Incidents tab: `IncidentTimeline` (vertical: detected →
mitigated → recovered, with durations).

### 2.7 What-if (`/whatif`)
Left: form (hypothetical events builder rows: type select, target multi-select, start offset, duration, params; candidate lines
editor; "prefill from decision #id"). Right: `ComparisonChart` (bars: unmet, overflow, min station SL per scenario) + a per-station
stockout-probability table. Disclaimer bar "Projection by our model – not executed". The Run button shows the latency.

### 2.8 Assistant (`/assistant`)
Suggested question chips · chat history (question → answer card with an engine badge `LLM`/`Rules` and source chips linking to
alerts/decisions/stations) · "Generate shift summary" button → summary card.

### 2.9 System Health (`/health`)
Overall pill + engine mode · component grid (from `24` SystemHealth.tsx, extended with breaker/p95/error rate/stale/SSE/
fallback rows) · SLO panel (bars) · incidents (latest 5) · versions footer · Grafana links (open in a new tab: Ops, Service
Health, Intelligence).

### 2.10 Control (`/control`, only if DEMO_CONTROLS)
Red notice "Calls organizer admin endpoints – testing/demo only". Three cards: Simulator (Run, Pause, Step 1, Step 10, Reset
with confirm), Crisis presets (8 buttons from `21` §2.8), Chaos (5 faults with a duration select 30/60/120 s + Clear). A last-action log.

### 2.11 Advanced pages (T2/T3)
Replay (timeline scrubber + Overview components fed by a historical frame), Scarcity & Fairness, Resilience (N-1 heatmap),
Ledger, Login. Layouts follow the same card system; the details are in `12` and the phase files.

## 3. Components (props → behaviour). File: `src/components/<Name>.tsx`
| Component | Props | Notes / test id |
|---|---|---|
| `StatusPill` | `status` | done in `24`; `status-pill` |
| `RiskBadge` | `level, hours?` | `risk-badge` |
| `KpiCard` | `label, value: string, sub?, tone?: 'ok'\|'warn'\|'bad', testId` | |
| `ClockBadge` | `state: WorldState` | `sim-clock` |
| `EngineModeBadge` | `mode: EngineMode` | `engine-mode` |
| `ConnectionDot` | `connection: 'live'\|'polling'\|'offline'` | `connection` |
| `DegradedBanner` | `state` | `degraded-banner` |
| `NetworkGraph` | `state, onSelect(entity)` | `network-graph`; nodes `node-<id>`, edges `edge-<route_id>` |
| `RiskHeatmap` | `stations: StationView[]` | cells `risk-cell-<station>-<fuel>` |
| `FuelBudgetCard` | `cover` (from /api/cover) | `fuel-budget` |
| `InventoryChart` | `history, projection?, capacity: FuelMap, fuel?: Fuel` | Recharts ComposedChart |
| `ForecastChart` | `series: ForecastSeries` | Area p10-p90 + line p50 + actual dots + anomaly markers |
| `RecommendationCard` | `decision: Decision, onApprove, onReject, onModify` | `rec-card-<id>`, `approve-<id>`, `reject-<id>`, `why-<id>`, `reviewed-<id>` |
| `ExplainPanel` | `explanation, narrative?, narrativeSource?` | `explain-<id>` |
| `DecisionTable` | `items, onOpen` | `decision-row-<id>` |
| `AlertList` | `items, onAck` | `alert-row-<id>` |
| `IncidentTimeline` | `incident` | |
| `HealthGrid` | `status: SystemStatus` | `component-<key>` (done in `24`) |
| `WhatIfForm` / `ComparisonChart` | | |
| `AssistantPanel` | | `assistant-input`, `assistant-answer` |
| `ControlPanel` | | `ctl-step`, `ctl-preset-<name>`, `ctl-chaos-<fault>` |
| `ConfirmDialog`, `Toasts`, `EntityDrawer`, `ErrorBoundary` | | |

## 4. Data & live-update rules
- `useLiveState()` (`src/hooks/useLiveState.ts`): EventSource('/api/stream'); on `state.updated` → refetch `/api/state` with the
  ETag (TanStack Query `queryKey ['state']`); on alert/decision/incident events → `invalidateQueries` for those lists + a toast
  for CRITICAL alerts and executed decisions; reconnect backoff 1→30 s; if no message for 30 s → switch to polling `/api/state`
  every 2 s (`connection='polling'`); offline after 3 failures.
- All pages read live data from the `['state']` query; detail pages use their own queries with `refetchInterval` 5 s.
- Never block the UI on the stream; render the last data with its age.

## 5. States checklist (every page)
Loading skeleton (same layout, grey blocks) · Error card with message + Retry · Empty state with an explanation · Degraded chip on
data derived from stale/degraded state · Keyboard focus visible · No layout shift on updates (fixed heights for KPI and chart cards).

## 6. Frontend tests (minimum)
Vitest: format utils (done), StatusPill (done), RiskBadge text, NetworkGraph renders 6 nodes + 6 edges from a fixture state
(`src/test/fixtures/state.json`, made by saving a real `/api/state`), RecommendationCard: Approve disabled until reviewed when
total > 5,000 L; useLiveState reducer (event → invalidation). Playwright (P12): overview loads + shows the tick; approve flow; the
health page lists components; the control page is hidden when `DEMO_CONTROLS=false`.
