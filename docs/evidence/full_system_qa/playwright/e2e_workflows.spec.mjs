import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { test, expect } = require("f:/NewTask/fuel-platform/frontend/node_modules/@playwright/test");

const ROOT_QA = path.resolve("f:/NewTask/fuel-platform/docs/evidence/full_system_qa");
const SHOTS_DIR = path.join(ROOT_QA, "screenshots");
const LOGS_DIR = path.join(ROOT_QA, "logs");

const LOCAL_URL = process.env.QA_BASE_URL || "http://localhost:3000";
const SIM_URL = process.env.QA_SIM_URL || "http://localhost:8000";
const API_URL = process.env.QA_API_URL || "http://localhost:18080";
const TOKEN = "change-me-operator";

const results = {
  timestamp: new Date().toISOString(),
  workflows: {},
};

async function apiGet(request, pathUrl, headers = {}) {
  const r = await request.get(`${LOCAL_URL}${pathUrl}`, { headers });
  const text = await r.text();
  let json = null;
  try {
    json = JSON.parse(text);
  } catch {
    json = null;
  }
  return { status: r.status(), json, text: text.slice(0, 500) };
}

async function apiPost(request, pathUrl, data = {}, headers = { Authorization: `Bearer ${TOKEN}` }) {
  const r = await request.post(`${LOCAL_URL}${pathUrl}`, { data, headers });
  const text = await r.text();
  let json = null;
  try {
    json = JSON.parse(text);
  } catch {
    json = null;
  }
  return { status: r.status(), json, text: text.slice(0, 600) };
}

test.describe("Stage 5 & 7 — Interactive E2E Workflows & Chaos Recovery", () => {
  test.afterAll(async ({ request }) => {
    // Ensure simulator restored cleanly: clear faults, switch mode to advisory, pause sim
    await apiPost(request, "/api/control/chaos/clear").catch(() => {});
    await apiPost(request, "/api/control/mode", { mode: "advisory" }).catch(() => {});
    await apiPost(request, "/api/control/sim/pause").catch(() => {});
    fs.writeFileSync(
      path.join(LOGS_DIR, "stage5_e2e_workflows.json"),
      JSON.stringify(results, null, 2),
      "utf-8",
    );
  });

  test("WF-01: Overview KPI parity, Fuel Cover math, and EntityDrawer", async ({ page, request }) => {
    const stateRes = await apiGet(request, "/api/state");
    const coverRes = await apiGet(request, "/api/cover");

    await page.goto(`${LOCAL_URL}/overview`, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(1500);

    const kpiTexts = await page.locator("main").innerText();
    // Try opening an EntityDrawer by clicking a station button/node or SVG circle/row if clickable
    const stationBtn = page.locator("button, [role='button']").filter({ hasText: /Cox's Bazar|Dhaka|STN-001|STN-004/ }).first();
    let drawerOpened = false;
    let drawerText = "";
    if ((await stationBtn.count()) > 0) {
      await stationBtn.click({ force: true }).catch(() => {});
      await page.waitForTimeout(500);
      const drawer = page.locator("aside, [role='dialog']").last();
      if ((await drawer.count()) > 0 && (await drawer.isVisible().catch(() => false))) {
        drawerOpened = true;
        drawerText = (await drawer.innerText()).slice(0, 400);
        await page.screenshot({ path: path.join(SHOTS_DIR, "wf01_entity_drawer.png") });
      }
    }

    const stations = stateRes.json?.stations || [];
    const totalStationDiesel = stations.reduce((acc, s) => acc + Number(s.inventory?.DIESEL || 0), 0);

    results.workflows.wf01_overview = {
      apiStateTick: stateRes.json?.tick,
      apiSimRunId: stateRes.json?.sim_run_id,
      engineMode: stateRes.json?.engine_mode,
      operatingMode: stateRes.json?.operating_mode,
      degraded: stateRes.json?.degraded,
      totalStationDieselLiters: totalStationDiesel,
      apiCoverSummary: coverRes.json,
      drawerOpened,
      drawerSnippet: drawerText,
      overviewSnippet: kpiTexts.slice(0, 700),
    };

    expect(stateRes.status).toBe(200);
  });

  test("WF-02: Network deep-link (?station=) & Time-Travel slider", async ({ page }) => {
    await page.goto(`${LOCAL_URL}/network?station=STN-004`, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(1200);
    const hasDrawerForQueryParam = await page.locator("text=Cox's Bazar").locator("..").isVisible().catch(() => false);
    const asideDialogs = await page.locator("[role='dialog'], .fixed.right-0").count();

    // Test TimeTravel slider or mode tabs if present
    const slider = page.locator('input[type="range"]').first();
    const hasSlider = (await slider.count()) > 0;
    let sliderValueBefore = null;
    let sliderValueAfter = null;
    if (hasSlider) {
      sliderValueBefore = await slider.inputValue().catch(() => null);
      const min = Number((await slider.getAttribute("min")) || "0");
      await slider.fill(String(min));
      await page.waitForTimeout(500);
      sliderValueAfter = await slider.inputValue().catch(() => null);
    }
    await page.screenshot({ path: path.join(SHOTS_DIR, "wf02_network_timetravel.png") });

    results.workflows.wf02_network = {
      urlWithQuery: page.url(),
      asideDialogsOnDeepLink: asideDialogs,
      hasDrawerForQueryParam,
      hasTimeTravelSlider: hasSlider,
      sliderValueBefore,
      sliderValueAfter,
    };
  });

  test("WF-03: Risk Forecast region filter bug & /api/risk stockout_prob field check", async ({ page, request }) => {
    const rawRisk = await apiGet(request, "/api/risk");
    const rawRiskDhaka = await apiGet(request, "/api/risk?region_id=dhaka");
    const rawRiskRealDhaka = await apiGet(request, "/api/risk?region_id=region-dhaka");

    await page.goto(`${LOCAL_URL}/risk`, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(1200);

    const rowsInitial = await page.locator("tbody tr").count();
    const select = page.locator("select").first();
    let rowsAfterDhakaSelect = rowsInitial;
    let selectedOptionValue = null;
    if ((await select.count()) > 0) {
      const options = await select.locator("option").evaluateAll((els) => els.map((o) => ({ text: o.textContent, value: o.value })));
      await select.selectOption({ index: 1 }).catch(() => {});
      await page.waitForTimeout(800);
      selectedOptionValue = await select.inputValue().catch(() => null);
      rowsAfterDhakaSelect = await page.locator("tbody tr").count();
      await page.screenshot({ path: path.join(SHOTS_DIR, "wf03_risk_dhaka_filter.png") });
      results.workflows.wf03_risk_options = options;
    }

    const firstRowProbSample = rawRisk.json?.items?.[0] || rawRisk.json?.stations?.[0] || rawRisk.json?.[0] || null;
    results.workflows.wf03_risk = {
      rawRiskStatus: rawRisk.status,
      rawRiskKeys: rawRisk.json ? Object.keys(rawRisk.json) : [],
      rawRiskCount: Array.isArray(rawRisk.json?.items) ? rawRisk.json.items.length : (Array.isArray(rawRisk.json) ? rawRisk.json.length : null),
      rawRiskDhakaCount: Array.isArray(rawRiskDhaka.json?.items) ? rawRiskDhaka.json.items.length : (Array.isArray(rawRiskDhaka.json) ? rawRiskDhaka.json.length : null),
      rawRiskRealDhakaCount: Array.isArray(rawRiskRealDhaka.json?.items) ? rawRiskRealDhaka.json.items.length : (Array.isArray(rawRiskRealDhaka.json) ? rawRiskRealDhaka.json.length : null),
      uiRowsInitial: rowsInitial,
      selectedOptionValue,
      uiRowsAfterDhakaSelect: rowsAfterDhakaSelect,
      firstRowSample: firstRowProbSample,
    };
  });

  test("WF-04: Control Panel Step x1, Live SSE vs Reload, Presets & Simulator Mutation Check", async ({ page, request }) => {
    // 1. Capture baseline simulator events and station demands before preset
    const simEventsBefore = await request.get(`${SIM_URL}/v1/events`);
    const simStationsBefore = await request.get(`${SIM_URL}/v1/stations`);
    const stateBefore = await apiGet(request, "/api/state");

    // Open a second tab on /overview to verify live SSE update when Control steps
    const overviewPage = await page.context().newPage();
    await overviewPage.goto(`${LOCAL_URL}/overview`, { waitUntil: "domcontentloaded" });
    await overviewPage.waitForTimeout(1200);
    const overviewTextBefore = await overviewPage.locator("header").innerText();

    // Navigate main page to /control and click Step +1
    await page.goto(`${LOCAL_URL}/control`, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(1000);
    const stepBtn = page.getByRole("button", { name: /Step \+1|Step 1|Step ×1/i }).first();
    const stepClicked = (await stepBtn.count()) > 0;
    if (stepClicked) {
      await stepBtn.click();
    } else {
      await apiPost(request, "/api/control/sim/step", { ticks: 1 });
    }
    await page.waitForTimeout(3000);

    const stateAfterStep1 = await apiGet(request, "/api/state");
    const recsAfterStep1 = await apiGet(request, "/api/recommendations");
    const decsAfterStep1 = await apiGet(request, "/api/decisions");
    const overviewTextAfterStep1 = await overviewPage.locator("header").innerText();

    // Step +1 a second time to observe if clean_cycles clears DEGRADED -> NORMAL
    if (stepClicked) {
      await stepBtn.click();
      await page.waitForTimeout(3000);
    }
    const stateAfterStep2 = await apiGet(request, "/api/state");

    // Test preset scenario via Control Panel ("Demand Spike" / Dhaka) and inspect simulator /v1/events
    const dhakaPresetBtn = page.getByRole("button", { name: /Demand Spike.*Dhaka|Dhaka.*Spike/i }).first();
    let presetClicked = false;
    if ((await dhakaPresetBtn.count()) > 0) {
      await dhakaPresetBtn.click();
      presetClicked = true;
      await page.waitForTimeout(1200);
    }
    const simEventsAfterPreset = await (await request.get(`${SIM_URL}/v1/events`)).json();
    const simStationsAfterPreset = await (await request.get(`${SIM_URL}/v1/stations`)).json();

    // Test Clear Faults / clean events? Notice Control preset creates simulator events; let's inspect chaos latency too
    const latencyBtn = page.getByRole("button", { name: /Latency|Delay/i }).first();
    let latencyClicked = false;
    if ((await latencyBtn.count()) > 0) {
      await latencyBtn.click();
      latencyClicked = true;
      await page.waitForTimeout(1000);
    }
    const controlStatusDuringChaos = await apiGet(request, "/api/control/status");
    const clearFaultsBtn = page.getByRole("button", { name: /Clear Faults/i }).first();
    if ((await clearFaultsBtn.count()) > 0) {
      await clearFaultsBtn.click();
      await page.waitForTimeout(1000);
    }
    await overviewPage.close();

    results.workflows.wf04_control = {
      stepClicked,
      tickBefore: stateBefore.json?.tick,
      engineModeBefore: stateBefore.json?.engine_mode,
      tickAfterStep1: stateAfterStep1.json?.tick,
      engineModeAfterStep1: stateAfterStep1.json?.engine_mode,
      tickAfterStep2: stateAfterStep2.json?.tick,
      engineModeAfterStep2: stateAfterStep2.json?.engine_mode,
      overviewHeaderTickChangedViaSSE: overviewTextBefore !== overviewTextAfterStep1,
      overviewHeaderBefore: overviewTextBefore,
      overviewHeaderAfter: overviewTextAfterStep1,
      threeWayConsistencyAfterStep1: {
        statePendingIds: (stateAfterStep1.json?.pending_recommendations || []).map((d) => d.id),
        recsPendingIds: (Array.isArray(recsAfterStep1.json) ? recsAfterStep1.json : recsAfterStep1.json?.items || [])
          .filter((d) => d.status === "PENDING_APPROVAL")
          .map((d) => d.id),
        decisionsPendingIds: (decsAfterStep1.json?.items || [])
          .filter((d) => d.status === "PENDING_APPROVAL" && d.sim_run_id === stateAfterStep1.json?.sim_run_id)
          .map((d) => d.id),
      },
      presetClicked,
      simEventsBeforeCount: (await simEventsBefore.json())?.length ?? null,
      simEventsAfterPreset,
      simStationsBeforeSample: (await simStationsBefore.json())?.[0],
      simStationsAfterPresetSample: simStationsAfterPreset?.[0],
      latencyClicked,
      controlStatusDuringChaos: controlStatusDuringChaos.json,
    };
  });

  test("WF-05 & WF-06: Recommendations Approval/Rejection, 4-eyes, Hardcoded KPIs & Autopilot E2E", async ({ page, request }) => {
    // First clear faults and pause so we know engine_mode and pending decisions
    await apiPost(request, "/api/control/chaos/clear");
    // Trigger Plan Now if no pending decision exists
    let stateNow = await apiGet(request, "/api/state");
    if ((stateNow.json?.pending_recommendations || []).length === 0) {
      await apiPost(request, "/api/plan-now");
      await page.waitForTimeout(2500);
      stateNow = await apiGet(request, "/api/state");
    }

    await page.goto(`${LOCAL_URL}/recommendations`, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(1500);
    await page.screenshot({ path: path.join(SHOTS_DIR, "wf05_recommendations_before.png"), fullPage: true });
    const kpiBefore = await page.locator("main").innerText();

    // Capture current engine_mode & try switching to Autopilot via Header UI
    const modeSwitchBtn = page.locator("header button").filter({ hasText: /Advisory|Autopilot/i }).first();
    let modeModalResult = { tested: false };
    if ((await modeSwitchBtn.count()) > 0) {
      await modeSwitchBtn.click();
      await page.waitForTimeout(400);
      const modalVisible = await page.locator("[role='dialog'], .fixed.inset-0").count();
      if (modalVisible > 0) {
        await page.screenshot({ path: path.join(SHOTS_DIR, "wf06_autopilot_modal.png") });
        const confirmBtn = page.getByRole("button", { name: /Confirm|Enable Autopilot|Switch/i }).last();
        const responsePromise = page.waitForResponse((res) => res.url().includes("/api/control/mode"), { timeout: 8000 }).catch(() => null);
        await confirmBtn.click().catch(() => {});
        const modeRes = await responsePromise;
        await page.waitForTimeout(800);
        const bodyAfter = await page.locator("body").innerText();
        modeModalResult = {
          tested: true,
          httpStatus: modeRes ? modeRes.status() : null,
          responseBody: modeRes ? await modeRes.text().catch(() => null) : null,
          errorShownInUI: bodyAfter.includes("409") || bodyAfter.includes("Cannot") || bodyAfter.includes("degraded") || bodyAfter.includes("stale") || bodyAfter.includes("Error"),
          headerNow: await page.locator("header").innerText(),
        };
        await page.screenshot({ path: path.join(SHOTS_DIR, "wf06_autopilot_after_click.png") });
      }
    }

    // Also test stale_data fault -> POST /api/control/mode {mode: "autopilot"} must return 409
    await apiPost(request, "/api/control/chaos/stale_data", { duration_sec: 20 });
    await page.waitForTimeout(1500);
    const autopilotWhileStale = await apiPost(request, "/api/control/mode", { mode: "autopilot" });
    await apiPost(request, "/api/control/chaos/clear");

    // Step twice cleanly after chaos clear to see if engine recovers to NORMAL and then try autopilot cleanly
    await apiPost(request, "/api/control/sim/step", { ticks: 1 });
    await page.waitForTimeout(2500);
    await apiPost(request, "/api/control/sim/step", { ticks: 1 });
    await page.waitForTimeout(2500);
    const stateAfterRecovery = await apiGet(request, "/api/state");
    const autopilotWhenClean = await apiPost(request, "/api/control/mode", { mode: "autopilot" });
    let stateDuringAutopilot = null;
    if (autopilotWhenClean.status === 200) {
      await apiPost(request, "/api/control/sim/step", { ticks: 1 });
      await page.waitForTimeout(3000);
      stateDuringAutopilot = await apiGet(request, "/api/state");
      await apiPost(request, "/api/control/mode", { mode: "advisory" });
    }

    // Test decision rejection / approval API & check if Recommendations KPIs change
    const recsNow = await apiGet(request, "/api/recommendations");
    const pendingList = Array.isArray(recsNow.json) ? recsNow.json : recsNow.json?.items || [];
    let decisionActionTest = { attempted: false };
    if (pendingList.length > 0) {
      const targetId = pendingList[0].id;
      const badApprove = await apiPost(request, `/api/decisions/${targetId}/approve`, { operator: "operator", reviewed_driver: false });
      decisionActionTest = {
        attempted: true,
        targetId,
        approveWithoutReviewStatus: badApprove.status,
        approveWithoutReviewBody: badApprove.json || badApprove.text,
      };
    }

    await page.reload({ waitUntil: "domcontentloaded" });
    await page.waitForTimeout(1200);
    const kpiAfter = await page.locator("main").innerText();

    results.workflows.wf05_06_decisions_autopilot = {
      initialEngineMode: stateNow.json?.engine_mode,
      pendingCountInitial: (stateNow.json?.pending_recommendations || []).length,
      hardcodedKpisPresentBefore: kpiBefore.includes("92.4%") && kpiBefore.includes("7.6%") && kpiBefore.includes("4.2 s"),
      hardcodedKpisPresentAfter: kpiAfter.includes("92.4%") && kpiAfter.includes("7.6%") && kpiAfter.includes("4.2 s"),
      modeModalResult,
      autopilotWhileStale,
      stateAfterRecovery: {
        tick: stateAfterRecovery.json?.tick,
        engine_mode: stateAfterRecovery.json?.engine_mode,
        degraded: stateAfterRecovery.json?.degraded,
      },
      autopilotWhenClean,
      stateDuringAutopilotTick: stateDuringAutopilot?.json?.tick ?? null,
      decisionActionTest,
    };
  });

  test("WF-07: What-If Simulation & API validation", async ({ page, request }) => {
    await page.goto(`${LOCAL_URL}/whatif`, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(1200);

    const scenarioOptions = await page.locator("select").first().locator("option").evaluateAll((els) =>
      els.map((o) => ({ text: o.textContent, value: o.value })),
    ).catch(() => []);

    const runBtn = page.getByRole("button", { name: /Run Simulation|Run What-If|Simulate/i }).first();
    let uiWhatIfResult = null;
    if ((await runBtn.count()) > 0) {
      const resPromise = page.waitForResponse((res) => res.url().includes("/api/whatif"), { timeout: 15_000 }).catch(() => null);
      await runBtn.click();
      const res = await resPromise;
      await page.waitForTimeout(1200);
      await page.screenshot({ path: path.join(SHOTS_DIR, "wf07_whatif_result.png"), fullPage: true });
      uiWhatIfResult = {
        status: res ? res.status() : null,
        body: res ? await res.json().catch(() => null) : null,
      };
    }

    const invalidWhatIf = await apiPost(request, "/api/whatif", {
      scenario_type: "nonexistent_type",
      horizon_ticks: -5,
    });

    results.workflows.wf07_whatif = {
      scenarioOptions,
      uiWhatIfResult,
      invalidWhatIfStatus: invalidWhatIf.status,
      invalidWhatIfBody: invalidWhatIf.json || invalidWhatIf.text,
    };
  });

  test("WF-08 & WF-09: Assistant Groundedness & Alerts / System Health", async ({ page, request }) => {
    await page.goto(`${LOCAL_URL}/assistant`, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(1000);

    const input = page.locator("input[type='text'], textarea").first();
    let askResponse = null;
    if ((await input.count()) > 0) {
      await input.fill("Why is Cox's Bazar at risk and what open alerts or pending decisions exist?");
      const resPromise = page.waitForResponse((res) => res.url().includes("/api/assistant/ask"), { timeout: 20_000 }).catch(() => null);
      await page.getByRole("button", { name: /Ask|Send/i }).first().click();
      const res = await resPromise;
      await page.waitForTimeout(1000);
      await page.screenshot({ path: path.join(SHOTS_DIR, "wf08_assistant_response.png"), fullPage: true });
      askResponse = {
        status: res ? res.status() : null,
        json: res ? await res.json().catch(() => null) : null,
      };
    }

    // Open alerts count from /api/alerts vs Assistant
    const alertsRes = await apiGet(request, "/api/alerts?status=OPEN");
    const decisionsRes = await apiGet(request, "/api/decisions");
    const reconRes = await apiGet(request, "/api/reports/reconciliation");
    const sysRes = await apiGet(request, "/api/system/status");

    await page.goto(`${LOCAL_URL}/alerts`, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(1000);
    await page.screenshot({ path: path.join(SHOTS_DIR, "wf09_alerts.png"), fullPage: true });

    results.workflows.wf08_09_assistant_alerts_health = {
      askResponse,
      liveOpenAlertsTotal: alertsRes.json?.total ?? alertsRes.json?.items?.length ?? null,
      liveDecisionsTotal: decisionsRes.json?.total ?? null,
      reconReportStatus: reconRes.status,
      systemStatusSummary: {
        overall: sysRes.json?.overall,
        engine_mode: sysRes.json?.engine_mode,
        notifierReportedStatus: sysRes.json?.components?.notifier,
        versions: sysRes.json?.versions,
        slo: sysRes.json?.slo,
      },
    };
  });
});
