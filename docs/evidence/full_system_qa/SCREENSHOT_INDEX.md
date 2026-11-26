# FSIP — QA Screenshot & Visual Evidence Index

All screenshots were captured automatically during live Chromium (`channel: "chrome"`) QA Playwright executions against `http://localhost:3000` and the live Cloudflare Quick Tunnel (`https://utilities-toddler-proxy-menu.trycloudflare.com`), stored under `docs/evidence/full_system_qa/screenshots/`.

---

## 1. Key Visual Defect Screenshots (Inspected Directly)

| File | Viewport | Route / Context | Observed Visual / Functional Issue |
|---|---:|---|---|
| [tunnel_overview_1440.png](screenshots/tunnel_overview_1440.png) | `1440×900` | Public Cloudflare Tunnel `/overview` | Loads cleanly via HTTPS tunnel, confirming UI & `/api/state` proxy work (`OPEN ALERTS: 0`, `DEGRADED`, label overlaps visible). |
| [route_overview_1440.png](screenshots/route_overview_1440.png) | `1440×900` | `/overview` (Baseline Run 2006 t=12) | **DEF-015:** `Karnaphuli Highway Stn` & `Cox's Bazar Regional Stn` text overlaps green `LOW` badges; **DEF-009:** `OPEN ALERTS` shows `0`; **DEF-021:** sidebar shows `P8`, `P9`, `P10`; footer hardcodes `FSIP v1.0.0 · sha:local`; banner stuck `2.94s old`. |
| [resp_overview_mobile_390.png](screenshots/resp_overview_mobile_390.png) | `390×844` | `/overview` (Mobile) | **DEF-017:** Header right edge clips green `● LIVE` pill off viewport (`● L` truncated) even though `scrollWidth === 390`. |
| [route_network_1440.png](screenshots/route_network_1440.png) | `1440×900` | `/network` | **DEF-012:** `"Enter Replay Mode"` button uses undefined Tailwind `accent` class; **DEF-021:** `Inventory History (48 Ticks)` x-axis stitches `98..134` across reset directly into `0..12`. |
| [route_risk_1440.png](screenshots/route_risk_1440.png) | `1440×900` | `/risk` | **DEF-006:** All 12 station-fuel rows report `LOW`, `Safe (>96h)`, `Stockout Prob: 0%`, `Causal Drivers: —` while `#DEC-1622` reports `72.0%` stockout probability. |
| [deep01_risk_dhaka_bug.png](screenshots/deep01_risk_dhaka_bug.png) | `1440×900` | `/risk` (Region=`Dhaka`) | **DEF-005:** Selecting `Dhaka` displays `"No station fuel series match the selected risk criteria."` because `<option value="dhaka">` fails to match `region-dhaka`. |
| [route_recommendations_1440.png](screenshots/route_recommendations_1440.png) | `1440×900` | `/recommendations` | **DEF-014:** Static hardcoded KPIs (`92.4%`, `7.6%`, `4.2 s`, `0.0%`); **DEF-022:** Rationale prints `"Ship 10900 L across 1 allocation(s). Primary risk at 4"`. |
| [route_decisions_1440.png](screenshots/route_decisions_1440.png) | `1440×900` | `/decisions` | **DEF-008:** `#DEC-1622` shows `0 L across 0 lines` despite having 7 lines / 10,900 L in DB (`GET /api/decisions` omits lines); prior initial mount also showed 440 past-run entries under `"Active Run (#2006)"` filter before hydration. |
| [route_alerts_1440.png](screenshots/route_alerts_1440.png) | `1440×900` | `/alerts` | **DEF-009:** Displays 2 OPEN `INVENTORY_VARIANCE` alerts at `station-karnaphuli` (`PETROL` & `DIESEL`), directly contradicting Overview KPI `OPEN ALERTS: 0`. |
| [route_whatif_1440.png](screenshots/route_whatif_1440.png) | `1440×900` | `/whatif` (Initial) | **DEF-018:** Missing `supply_shortfall` option, multi-event rows, and comparison chart. |
| [wf07_whatif_result.png](screenshots/wf07_whatif_result.png) | `1440×900` | `/whatif` (Executed) | Renders `DO NOTHING` (`96.9%`, `681.7 L` unmet), `RECOMMENDED` (`100.0%`), `CUSTOM` (`96.9%`, `679.6 L` unmet); all first-4 displayed station cards say `0% risk` despite unmet demand. |
| [route_assistant_1440.png](screenshots/route_assistant_1440.png) | `1440×900` | `/assistant` (Initial) | **DEF-017:** Bottom prompt suggestions & input box clipped below fold / overflows `900px` desktop viewport height (`y=874.5..912.5`). |
| [wf08_assistant_response.png](screenshots/wf08_assistant_response.png) | `1440×900` | `/assistant` (After query) | **DEF-011 & DEF-017:** Header `"Operations Assistant"` scrolls partially under top bar; reply completely omits open alerts & pending decisions asked for in prompt. |
| [route_health_1440.png](screenshots/route_health_1440.png) | `1440×900` | `/health` | **DEF-016 & DEF-019:** Shows stale `"2.9s ago"`, `notifier: Healthy` (no container), `Decision Freshness: 99.80%` (hardcoded), broken external links to `http://localhost:3001`. |
| [route_control_1440.png](screenshots/route_control_1440.png) | `1440×900` | `/control` | Renders step/reset controls, 8 scenario preset cards, and chaos injection controls; exposes `/admin/*` triggers behind frontend static operator token. |
| [command_palette_open.png](screenshots/command_palette_open.png) | `1440×900` | Global `Ctrl+K` | Command Palette renders cleanly and filters navigation commands (`Go to System Alerts & Active Incidents`). |
| [wf06_autopilot_modal.png](screenshots/wf06_autopilot_modal.png) | `1440×900` | `/recommendations` (Modal) | `"Enable Autopilot Mode?"` confirmation modal renders properly. |
| [wf06_autopilot_after_click.png](screenshots/wf06_autopilot_after_click.png) | `1440×900` | `/recommendations` (Autopilot) | Shows `Autopilot Held (Operator Review Required): Decision volume (12,620 L) exceeds 7,000 L threshold` + SSE toast `#1625`; Teaming KPI still hardcoded `92.4%`, `84.0%`. |
| [deep02_autopilot_blocked_ui.png](screenshots/deep02_autopilot_blocked_ui.png) | `1440×900` | `/overview` (Degraded Modal) | Shows `"Cannot Enable Autopilot"` error dialog (`409 STALE_DATA`) when `engine_mode=DEGRADED` during `unavailable` chaos. |
| [deep03_sse_stale_after_reset.png](screenshots/deep03_sse_stale_after_reset.png) | `1440×900` | `/overview` (After Reset) | **DEF-004b:** Simulator reset to `t=0` (Run 2007), but open tab stays frozen at `Day 1 05:15 · t=21 DEGRADED` because `useLiveState.ts:249` rejects `tick < latestTick`. |

---

## 2. Responsive & Workflow Screenshot Inventory (All 39 Captured Files)

- **Route Desktop Baseline (`1440×900`):** `route_overview_1440.png`, `route_network_1440.png`, `route_risk_1440.png`, `route_recommendations_1440.png`, `route_decisions_1440.png`, `route_alerts_1440.png`, `route_whatif_1440.png`, `route_assistant_1440.png`, `route_health_1440.png`, `route_control_1440.png`
- **Tunnel Verification (`1440×900`):** `tunnel_overview_1440.png`
- **Multi-Viewport Responsive Matrix (`laptop_1280`, `tablet_768`, `mobile_390`):**
  - `resp_overview_{laptop_1280,tablet_768,mobile_390}.png`
  - `resp_network_{laptop_1280,tablet_768,mobile_390}.png`
  - `resp_recommendations_{laptop_1280,tablet_768,mobile_390}.png`
  - `resp_decisions_{laptop_1280,tablet_768,mobile_390}.png`
  - `resp_whatif_{laptop_1280,tablet_768,mobile_390}.png`
  - `resp_control_{laptop_1280,tablet_768,mobile_390}.png`
  *(Visually verified `resp_overview_mobile_390.png`, `resp_recommendations_mobile_390.png`, `resp_decisions_mobile_390.png`, `resp_control_mobile_390.png`, `resp_whatif_mobile_390.png` — all exhibit right-edge header `● LIVE` clipping at `390px`, plus select truncation on `whatif`.)*
- **Interactive Workflows & Bug Repros:** `command_palette_open.png`, `wf02_network_timetravel.png`, `wf03_risk_dhaka_filter.png`, `wf05_recommendations_before.png`, `wf06_autopilot_modal.png`, `wf06_autopilot_after_click.png`, `wf07_whatif_result.png`, `wf08_assistant_response.png`, `wf09_alerts.png`, `deep01_risk_dhaka_bug.png`, `deep02_autopilot_blocked_ui.png`, `deep03_sse_stale_after_reset.png`.
- **Interactive Playwright HTML Report:** `docs/evidence/full_system_qa/playwright/html-report/index.html`
