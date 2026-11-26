# Manual Feature Audit — Fuel Ops Console

**Stack:** `http://localhost:3000` (UI) + `http://localhost:18080` (API)  
**Result:** **20 OK / 1 PARTIAL / 0 FAIL** (21 features checked)

Evidence: `FEATURE_MATRIX.json`, `api_probe.json`, `workflow_probe.json`, `four_eyes_reject_autopilot.json`

## Feature matrix

| Feature | Result | Notes |
|---|---|---|
| Sign-In + Operator / Supervisor switch | OK | Demo accounts; role powers apply immediately |
| Operator RBAC | OK | Approve/reject/mode; policy change forbidden (403) |
| Supervisor RBAC | OK | Policy change allowed; completes four-eyes |
| Overview | OK | KPIs, topology, F-COVER, risk heatmap |
| Network | OK | Stations / Depots / Routes |
| Risk | OK | Station Risk, N-1, Conformal filters |
| Alerts | OK | Open / Ack / filters |
| System Health | OK | Components + SLO + Grafana links |
| Decisions ledger | OK | Filters + CSV/JSON export |
| Approve | OK | Why + cognitive checkbox → dispatch |
| Reject | OK | Status `REJECTED` |
| Four-eyes (>7k L) | OK | 1st `AWAITING_SECOND_APPROVAL`, same-actor 403, 2nd `EXECUTED` |
| Cognitive forcing | OK | UI gate + API `REVIEW_REQUIRED` |
| Autopilot enable | OK | Confirm dialog; mode switches to Autopilot |
| Autopilot auto-dispatch | **PARTIAL** | Mode works; auto-approve often 0 (see below) |
| Control run/pause/step | OK | API + UI |
| Crisis presets | OK | e.g. Demand Spike Dhaka |
| Chaos inject/clear | OK | latency needs JSON body; clear OK |
| What-If | OK | do_nothing / recommended / custom comparison |
| Assistant | OK | Grounded rules answers |
| Plan Now + policy | OK | Pending recs generated |

## Autopilot PARTIAL — why

Enable succeeds (`operating_mode=autopilot`), but live plans are typically ~23,000 L and eligibility reports:

1. Volume exceeds 7,000 L → requires four-eyes (never auto)
2. Domain SLO error budget exhausted → autopilot decisions frozen

So Autopilot **mode** is healthy; **hands-free dispatch** rarely fires in the stressed demo state. That is policy-correct, but easy to misread as “broken.”

## Improvement proposals

1. **Autopilot demo clarity** — Banner when mode=autopilot but 0 eligible: “Frozen: four-eyes volume / SLO budget” with the exact blocked_reasons (already on the rec card; promote to header).
2. **Demo “autopilot-eligible” preset** — A control preset that plans several ≤7k L lines while SL is healthy, so auto-approve is visible in demos without weakening production guards.
3. **Soft-SLO messaging** — Keep soft budget from blocking *mode entry* (already fixed); show a progress/recovery meter so operators know when auto-dispatch will resume.
4. **Expired rec UX** — Cards sometimes show expired tick while Approve is disabled for other reasons; hide/expire more aggressively or label “Expired — Plan Now.”
5. **Chaos API DX** — `POST /control/chaos/{fault}` should accept empty body with defaults (UI sends body; bare curl 422 is confusing).
6. **Assistant a11y** — Chat bubbles aren’t in the accessibility tree; add roles/`aria-live` for screen readers and automated UI audits.
7. **Acceptance-rate KPIs** — Recommendations page showed 0% acceptance early in the run; seed or scope metrics to non-expired decisions so demos don’t look empty.
