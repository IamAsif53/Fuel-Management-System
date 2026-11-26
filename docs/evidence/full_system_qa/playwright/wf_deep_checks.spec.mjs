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
const TOKEN = "change-me-operator";

const deep = {
  timestamp: new Date().toISOString(),
};

async function apiGet(request, u) {
  const r = await request.get(`${LOCAL_URL}${u}`);
  return { status: r.status(), json: await r.json().catch(() => null) };
}

async function apiPost(request, u, data = {}) {
  const r = await request.post(`${LOCAL_URL}${u}`, {
    data,
    headers: { Authorization: `Bearer ${TOKEN}` },
  });
  return { status: r.status(), json: await r.json().catch(() => null), text: await r.text().catch(() => "") };
}

test.describe("Stage 5 Deep Verification — Specific Bug & Lifecycle Reproduction", () => {
  test.afterAll(async ({ request }) => {
    await apiPost(request, "/api/control/chaos/clear").catch(() => {});
    await apiPost(request, "/api/control/mode", { mode: "advisory" }).catch(() => {});
    await apiPost(request, "/api/control/sim/pause").catch(() => {});
    fs.writeFileSync(path.join(LOGS_DIR, "stage5_deep_checks.json"), JSON.stringify(deep, null, 2), "utf-8");
  });

  test("DEEP-01: Risk page <main> Dhaka filter & /api/risk payload", async ({ page, request }) => {
    const rawAll = await apiGet(request, "/api/risk");
    const rawDhaka = await apiGet(request, "/api/risk?region_id=dhaka");
    const rawRealDhaka = await apiGet(request, "/api/risk?region_id=region-dhaka");

    await page.goto(`${LOCAL_URL}/risk`, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(1000);

    const regionSelect = page.locator("main select").first();
    const regionOptions = await regionSelect.locator("option").evaluateAll((els) =>
      els.map((o) => ({ text: o.textContent?.trim(), value: o.value })),
    );
    const initialRows = await page.locator("main tbody tr").count();
    await regionSelect.selectOption("dhaka");
    await page.waitForTimeout(700);
    const rowsAfterDhaka = await page.locator("main tbody tr").count();
    await page.screenshot({ path: path.join(SHOTS_DIR, "deep01_risk_dhaka_bug.png"), fullPage: true });

    // Also test Assistant "Show me active shortage alerts" button/query + Shift Summary
    await page.goto(`${LOCAL_URL}/assistant`, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(800);
    const inputBox = page.locator("main input[type='text'], main textarea").first();
    const boxRect = await inputBox.boundingBox();
    const summaryBtn = page.getByRole("button", { name: /Generate Shift Summary/i }).first();
    let summaryResp = null;
    if ((await summaryBtn.count()) > 0) {
      const p = page.waitForResponse((res) => res.url().includes("/api/summary") || res.url().includes("/api/assistant"), { timeout: 10_000 }).catch(() => null);
      await summaryBtn.click();
      const res = await p;
      await page.waitForTimeout(700);
      summaryResp = res ? { status: res.status(), json: await res.json().catch(() => null) } : null;
    }

    // WhatIf Scenario Type <main select> options check
    await page.goto(`${LOCAL_URL}/whatif`, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(800);
    const whatifEventOptions = await page.locator("main select").first().locator("option").evaluateAll((els) =>
      els.map((o) => ({ text: o.textContent?.trim(), value: o.value })),
    );

    deep.riskAndForms = {
      rawAllCount: rawAll.json?.rows?.length ?? null,
      rawAllFirstRow: rawAll.json?.rows?.[0] ?? null,
      rawDhakaCount: rawDhaka.json?.rows?.length ?? null,
      rawRealDhakaCount: rawRealDhaka.json?.rows?.length ?? null,
      regionOptions,
      initialRows,
      rowsAfterDhaka,
      assistantInputBoundingBox: boxRect,
      assistantSummaryResp: summaryResp,
      whatifEventOptions,
    };
  });

  test("DEEP-02: Control Presets simulator parameter mismatch & Chaos latency/stale Autopilot 409", async ({ page, request }) => {
    await page.goto(`${LOCAL_URL}/control`, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(900);

    // Trigger Demand Spike — Dhaka preset
    const dhakaCard = page.locator("main div").filter({ hasText: /^Demand Spike — Dhaka/ }).first();
    const dhakaInjectBtn = page.locator("button:has-text('Inject Preset')").nth(0);
    await dhakaInjectBtn.click();
    await page.waitForTimeout(600);
    await page.locator("button:has-text('Inject Preset')").nth(2).click(); // Depot Constraint Patiya
    await page.waitForTimeout(600);
    await page.locator("button:has-text('Inject Preset')").nth(4).click(); // Supply Shortfall
    await page.waitForTimeout(800);

    const simEventsAfterPresets = await (await request.get(`${SIM_URL}/v1/events`)).json();
    const simStationsAfterPresets = await (await request.get(`${SIM_URL}/v1/stations`)).json();

    // Inject latency chaos via UI & verify /api/control/status
    const latencyBtn = page.locator("button:has-text('Inject Fault')").nth(2);
    await latencyBtn.click().catch(() => {});
    await page.waitForTimeout(800);
    const controlStatusLatency = await apiGet(request, "/api/control/status");

    // Clear chaos, then inject unavailable (HTTP 503) and step so engine enters DEGRADED, then test Header UI Autopilot 409!
    await apiPost(request, "/api/control/chaos/clear");
    await apiPost(request, "/api/control/mode", { mode: "advisory" });
    await apiPost(request, "/api/control/chaos/unavailable", { duration_sec: 15 });
    await page.waitForTimeout(1500);
    // Force a cycle/step or check status
    await apiPost(request, "/api/control/sim/step", { ticks: 1 }).catch(() => {});
    await page.waitForTimeout(2000);
    const stateWhileUnavailable = await apiGet(request, "/api/state");

    // Try switching to Autopilot in Header UI while unavailable/degraded
    await page.goto(`${LOCAL_URL}/overview`, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(800);
    const modeBtn = page.locator("header button").filter({ hasText: /Advisory|Autopilot/i }).first();
    let blockedUiCheck = {};
    if ((await modeBtn.count()) > 0) {
      await modeBtn.click();
      await page.waitForTimeout(400);
      const confirmBtn = page.getByRole("button", { name: /Confirm|Enable Autopilot|Switch/i }).last();
      const resPromise = page.waitForResponse((r) => r.url().includes("/api/control/mode"), { timeout: 8000 }).catch(() => null);
      await confirmBtn.click().catch(() => {});
      const res = await resPromise;
      await page.waitForTimeout(600);
      const bodyTxt = await page.locator("body").innerText();
      await page.screenshot({ path: path.join(SHOTS_DIR, "deep02_autopilot_blocked_ui.png") });
      blockedUiCheck = {
        status: res ? res.status() : null,
        body: res ? await res.text().catch(() => null) : null,
        uiErrorDisplayed: bodyTxt.includes("409") || bodyTxt.includes("Cannot") || bodyTxt.includes("degraded") || bodyTxt.includes("stale") || bodyTxt.includes("unavailable") || bodyTxt.includes("DEGRADED"),
      };
    }

    // Clear chaos & step twice to restore NORMAL
    await apiPost(request, "/api/control/chaos/clear");
    await apiPost(request, "/api/control/sim/step", { ticks: 1 });
    await page.waitForTimeout(2000);
    await apiPost(request, "/api/control/sim/step", { ticks: 1 });
    await page.waitForTimeout(2000);

    deep.presetsAndChaos = {
      simEventsAfterPresets,
      simStationsDemandMultipliers: Array.isArray(simStationsAfterPresets)
        ? simStationsAfterPresets.map((s) => ({ id: s.id, demand_multiplier: s.demand_multiplier }))
        : simStationsAfterPresets,
      controlStatusLatency: controlStatusLatency.json?.active_faults,
      stateWhileUnavailable: {
        engine_mode: stateWhileUnavailable.json?.engine_mode,
        degraded: stateWhileUnavailable.json?.degraded,
        degraded_reasons: stateWhileUnavailable.json?.degraded_reasons,
      },
      blockedUiCheck,
    };
  });

  test("DEEP-03: Decision Approve / Modify / Reject / Manual / Autopilot / SSE Reset Sync", async ({ page, request }) => {
    // 1. Ensure a pending decision exists
    await apiPost(request, "/api/plan-now");
    await page.waitForTimeout(2000);

    await page.goto(`${LOCAL_URL}/recommendations`, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(1000);

    // Check current pending decision
    const recs = await apiGet(request, "/api/recommendations");
    const pendingItem = recs.json?.items?.[0];

    // Approve partial (only 1st line, modified qty) via UI or API after expanding Cognitive Review ("Why")
    let approveResult = null;
    if (pendingItem) {
      // Test UI expand "Why" & Approve Partial / Modify
      const whyToggle = page.locator("main button").filter({ hasText: /Why|Risk Driver|Details|Reasoning/i }).first();
      if ((await whyToggle.count()) > 0) {
        await whyToggle.click().catch(() => {});
        await page.waitForTimeout(400);
      }
      // Approve just line 0 directly via UI if visible, or test API approve with reviewed_driver=true
      const lineId0 = pendingItem.lines?.[0]?.id;
      if (lineId0) {
        approveResult = await apiPost(request, `/api/decisions/${pendingItem.id}/approve`, {
          operator: "qa-operator",
          reviewed_driver: true,
          selected_line_ids: [lineId0],
          modified_quantities: { [lineId0]: 1200 },
        });
      }
    }

    // Generate another decision and test REJECT
    await apiPost(request, "/api/control/sim/step", { ticks: 1 });
    await page.waitForTimeout(2000);
    let recs2 = await apiGet(request, "/api/recommendations");
    if (!recs2.json?.items?.length) {
      await apiPost(request, "/api/plan-now");
      await page.waitForTimeout(2000);
      recs2 = await apiGet(request, "/api/recommendations");
    }
    let rejectResult = null;
    if (recs2.json?.items?.[0]) {
      rejectResult = await apiPost(request, `/api/decisions/${recs2.json.items[0].id}/reject`, {
        operator: "qa-operator",
        reason: "QA rejection test",
      });
    }

    // Test Manual Allocation
    const manualRes = await apiPost(request, "/api/manual-allocation", {
      source_depot_id: "depot-gazipur",
      destination_station_id: "station-mirpur",
      route_id: "route-gazipur-mirpur",
      fuel_type: "DIESEL",
      quantity: 1000,
      reason: "QA manual test",
      operator: "qa-operator",
    });

    // Test Reset + live SSE tick monotonicity bug (`useLiveState.ts:249` `newState.tick >= this.latestTick`)
    const overviewPage = await page.context().newPage();
    await overviewPage.goto(`${LOCAL_URL}/overview`, { waitUntil: "domcontentloaded" });
    await overviewPage.waitForTimeout(1200);
    const headerBeforeReset = await overviewPage.locator("header").innerText();
    const apiStateBeforeReset = await apiGet(request, "/api/state");

    // Reset simulator via Control API -> starts new run at tick 0
    const resetRes = await apiPost(request, "/api/control/sim/reset");
    await overviewPage.waitForTimeout(2500);
    const apiStateAfterReset = await apiGet(request, "/api/state");
    const headerAfterResetNoReload = await overviewPage.locator("header").innerText();
    await overviewPage.screenshot({ path: path.join(SHOTS_DIR, "deep03_sse_stale_after_reset.png") });
    await overviewPage.close();

    // Check Autopilot auto-approval over 3 ticks after reset (when early tick demand/lines may be <=7,000L total)
    await apiPost(request, "/api/control/sim/step", { ticks: 1 });
    await page.waitForTimeout(2000);
    await apiPost(request, "/api/control/sim/step", { ticks: 1 });
    await page.waitForTimeout(2000);
    const modeSwitchClean = await apiPost(request, "/api/control/mode", { mode: "autopilot" });
    const autopilotSteps = [];
    for (let i = 0; i < 3; i++) {
      await apiPost(request, "/api/control/sim/step", { ticks: 1 });
      await page.waitForTimeout(2500);
      const st = await apiGet(request, "/api/state");
      const decs = await apiGet(request, `/api/decisions?sim_run_id=${st.json?.sim_run_id}&limit=10`);
      autopilotSteps.push({
        tick: st.json?.tick,
        engine_mode: st.json?.engine_mode,
        operating_mode: st.json?.operating_mode,
        decisions: (decs.json?.items || []).map((d) => ({ id: d.id, tick: d.tick, status: d.status, mode: d.mode })),
      });
    }
    await apiPost(request, "/api/control/mode", { mode: "advisory" });

    // Check Recommendations KPIs again after real approvals & rejections
    await page.goto(`${LOCAL_URL}/recommendations`, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(1000);
    const recTextAfterOps = await page.locator("main").innerText();

    deep.lifecycleAndReset = {
      approveResult,
      rejectResult,
      manualRes,
      resetTest: {
        resetStatus: resetRes.status,
        beforeTick: apiStateBeforeReset.json?.tick,
        beforeRun: apiStateBeforeReset.json?.sim_run_id,
        afterResetApiTick: apiStateAfterReset.json?.tick,
        afterResetApiRun: apiStateAfterReset.json?.sim_run_id,
        openOverviewHeaderBeforeReset: headerBeforeReset,
        openOverviewHeaderAfterResetWithoutReload: headerAfterResetNoReload,
        sseIgnoredBackwardTickOnReset: !headerAfterResetNoReload.includes(`t=${apiStateAfterReset.json?.tick}`),
      },
      modeSwitchClean,
      autopilotSteps,
      recommendationsStillShowsHardcoded924: recTextAfterOps.includes("92.4%") && recTextAfterOps.includes("7.6%"),
    };
  });
});
