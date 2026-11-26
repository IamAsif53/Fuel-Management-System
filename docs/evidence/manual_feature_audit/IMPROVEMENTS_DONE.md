# Improvement pass — completed

Live verify (post rebuild): chaos empty body 200 · prepare-autopilot → tick 2 · decisions `EXECUTED` by `autopilot` · metrics acceptance excludes expired.

| Improvement | Status | Where |
|---|---|---|
| Autopilot frozen banner + blocked reasons | Done | `AutopilotStatusBanner.tsx` in AppLayout |
| Soft-SLO recovery meter | Done | Same banner (`domainSloBudget`) |
| Expired recommendation UX | Done | Recommendations cards: Expired badge, disabled actions, Plan Now cue |
| Chaos API empty body defaults | Done | `control.py` + test |
| Assistant a11y | Done | `role=log` / `aria-live` / article messages |
| Acceptance KPIs excl. expired/failed | Done | `db.fetch_decision_metrics` |
| Autopilot demo arming | Done | `POST /control/demo/prepare-autopilot` + Control UI button |
| Autopilot ≤7k subset auto-dispatch | Done | Engine `evaluate_autopilot_pending` volume-only partial |

Tests: frontend shell+assistant; API chaos empty body; engine subset helpers.
