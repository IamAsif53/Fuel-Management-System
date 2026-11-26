/**
 * Capture genuine FSIP UI screenshots from a running frontend for README docs.
 * Usage (from fuel-platform/frontend):
 *   node ../scripts/capture_readme_screenshots.mjs
 * Env:
 *   FSIP_BASE_URL=http://127.0.0.1:8088
 */
import { createRequire } from "node:module";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, "..");
const require = createRequire(path.join(ROOT, "frontend", "package.json"));
const { chromium, devices } = require("@playwright/test");
const OUT = path.join(ROOT, "docs", "screenshots");
const BASE = process.env.FSIP_BASE_URL || "http://127.0.0.1:8088";

const DESKTOP = { width: 1440, height: 900 };
const MOBILE = devices["iPhone 12"]; // 390×844

function ensureDirs() {
  for (const sub of ["desktop", "mobile", "workflows"]) {
    fs.mkdirSync(path.join(OUT, sub), { recursive: true });
  }
}

async function settle(page, ms = 2500) {
  await page.waitForLoadState("domcontentloaded");
  try {
    await page.waitForLoadState("networkidle", { timeout: 15000 });
  } catch {
    /* charts / SSE may keep connections open */
  }
  await page.waitForTimeout(ms);
}

async function hideDemoPasswordHint(page) {
  await page.addStyleTag({
    content: `
      [data-testid="login-modal"] p.text-\\[11px\\],
      [data-testid="login-modal"] p:has-text("Demo accounts") {
        visibility: hidden !important;
      }
    `,
  }).catch(() => {});
  await page.evaluate(() => {
    document.querySelectorAll("[data-testid='login-modal'] p").forEach((p) => {
      if ((p.textContent || "").includes("Demo accounts")) {
        p.style.visibility = "hidden";
      }
    });
  });
}

async function goto(page, route) {
  await page.goto(`${BASE}${route}`, { waitUntil: "domcontentloaded", timeout: 60000 });
  await settle(page);
}

async function shot(page, relPath, opts = {}) {
  const full = path.join(OUT, relPath);
  fs.mkdirSync(path.dirname(full), { recursive: true });
  await page.screenshot({
    path: full,
    fullPage: opts.fullPage ?? true,
    animations: "disabled",
  });
  console.log("saved", relPath);
  return relPath;
}

async function injectSession(page, role) {
  const res = await page.request.post(`${BASE}/api/auth/login`, {
    data: { username: role, password: role },
  });
  if (!res.ok()) {
    throw new Error(`Login API failed for ${role}: ${res.status()} ${await res.text()}`);
  }
  const body = await res.json();
  const user = {
    username: body.username || role,
    actor: body.username || role,
    role: body.role || role,
  };
  await page.addInitScript(
    ({ token, user }) => {
      localStorage.setItem("fsip_auth_token", token);
      localStorage.setItem("fsip_auth_user", JSON.stringify(user));
    },
    { token: body.access_token, user }
  );
  await page.evaluate(
    ({ token, user }) => {
      localStorage.setItem("fsip_auth_token", token);
      localStorage.setItem("fsip_auth_user", JSON.stringify(user));
    },
    { token: body.access_token, user }
  );
}

async function loginAs(page, role) {
  await injectSession(page, role);
  await page.reload({ waitUntil: "domcontentloaded" });
  await settle(page, 2000);
}

async function captureDesktop(browser) {
  const context = await browser.newContext({
    viewport: DESKTOP,
    deviceScaleFactor: 1,
  });
  const page = await context.newPage();
  page.setDefaultTimeout(45000);

  // --- Login / Authentication (no password typed; demo hint hidden) ---
  await goto(page, "/overview");
  await page.getByTestId("login-btn").click();
  await page.getByTestId("login-modal").waitFor({ state: "visible" });
  await hideDemoPasswordHint(page);
  await page.getByTestId("login-username").fill("operator");
  await shot(page, "desktop/login.png", { fullPage: false });
  await page.keyboard.press("Escape").catch(() => {});
  await injectSession(page, "operator");
  await page.reload({ waitUntil: "domcontentloaded" });
  await settle(page, 3000);

  // --- Core pages ---
  await goto(page, "/overview");
  await settle(page, 4000);
  await shot(page, "desktop/overview.png");

  await goto(page, "/network");
  await settle(page, 4000);
  await shot(page, "desktop/network.png");

  await goto(page, "/risk");
  await settle(page, 3500);
  await shot(page, "desktop/risk-analysis.png");

  // Risk filter by region (if options exist)
  const regionSelect = page.locator("select").filter({ has: page.locator("option", { hasText: "All Regions" }) }).first();
  if (await regionSelect.count()) {
    const options = await regionSelect.locator("option").allTextContents();
    const regionOpt = options.find((t) => t && t !== "All Regions");
    if (regionOpt) {
      await regionSelect.selectOption({ label: regionOpt });
      await settle(page, 2500);
      await shot(page, "workflows/risk-region-filter.png");
    }
  }

  await goto(page, "/recommendations");
  await settle(page, 3500);
  await shot(page, "desktop/recommendations.png");

  // Recommendation details / approve affordance if a card exists
  const firstCard = page.locator("[data-testid^='rec-card-']").first();
  if (await firstCard.count()) {
    await firstCard.scrollIntoViewIfNeeded();
    await settle(page, 1000);
    await shot(page, "workflows/recommendation-details.png");
  }

  await goto(page, "/decisions");
  await settle(page, 3500);
  await shot(page, "desktop/decisions.png");
  // Allocation ledger / reconciliation surface = decisions ledger
  await shot(page, "desktop/allocation-ledger.png");

  // Decision details: expand first row or deep-link
  const firstRow = page.locator("[data-testid^='decision-row-']").first();
  if (await firstRow.count()) {
    const testId = await firstRow.getAttribute("data-testid");
    const id = testId?.replace("decision-row-", "");
    const expandBtn = firstRow.locator("button").first();
    if (await expandBtn.count()) {
      await expandBtn.click();
      await settle(page, 1500);
    }
    if (id) {
      await goto(page, `/decisions/${id}`);
      await settle(page, 2500);
    }
    await shot(page, "desktop/decision-details.png");
  } else {
    console.log("skip decision-details (no rows)");
  }

  await goto(page, "/whatif");
  await settle(page, 2500);
  await shot(page, "desktop/what-if.png");

  // Run what-if simulation
  const runBtn = page.getByRole("button", { name: /Run What-If Simulation/i });
  if (await runBtn.count()) {
    await runBtn.click();
    await settle(page, 5000);
    await shot(page, "workflows/whatif-results.png");
  }

  await goto(page, "/assistant");
  await settle(page, 2000);
  await shot(page, "desktop/assistant.png");
  const ask = page.getByRole("button", { name: /How is system health right now/i }).or(
    page.getByText("How is system health right now?")
  );
  if (await ask.count()) {
    await ask.first().click();
    await settle(page, 5000);
    await shot(page, "workflows/assistant-answer.png");
  } else {
    const input = page.locator("textarea, input[type='text']").last();
    if (await input.count()) {
      await input.fill("How is system health right now?");
      await page.getByRole("button", { name: /send/i }).or(page.locator("button").filter({ has: page.locator("svg") })).last().click();
      await settle(page, 5000);
      await shot(page, "workflows/assistant-answer.png");
    }
  }

  await goto(page, "/health");
  await settle(page, 3500);
  await shot(page, "desktop/system-health.png");

  await goto(page, "/control");
  await settle(page, 3500);
  await shot(page, "desktop/simulator-control.png");

  await goto(page, "/alerts");
  await settle(page, 3500);
  await shot(page, "desktop/alerts.png");

  // --- Four-eyes workflow (operator → supervisor) ---
  await goto(page, "/recommendations");
  await settle(page, 2000);
  await shot(page, "workflows/approval-operator.png");
  await loginAs(page, "supervisor");
  await goto(page, "/recommendations");
  await settle(page, 2500);
  await shot(page, "workflows/approval-supervisor-foureye.png");
  // Also show decisions as supervisor
  await goto(page, "/decisions");
  await settle(page, 2000);
  await shot(page, "workflows/decisions-supervisor.png");

  // Dashboard metrics (overview again as signed-in)
  await goto(page, "/overview");
  await settle(page, 3000);
  await shot(page, "workflows/dashboard-metrics.png");

  await context.close();
}

async function captureMobile(browser) {
  const context = await browser.newContext({
    ...MOBILE,
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 2,
    isMobile: true,
    hasTouch: true,
  });
  const page = await context.newPage();
  page.setDefaultTimeout(45000);

  await goto(page, "/overview");
  await injectSession(page, "operator");
  await page.reload({ waitUntil: "domcontentloaded" });
  await settle(page, 3500);
  await shot(page, "mobile/overview-mobile.png", { fullPage: true });

  await goto(page, "/risk");
  await settle(page, 3000);
  await shot(page, "mobile/risk-mobile.png", { fullPage: true });

  await goto(page, "/assistant");
  await settle(page, 2500);
  await shot(page, "mobile/assistant-mobile.png", { fullPage: true });

  await context.close();
}

async function main() {
  ensureDirs();
  console.log("Capturing from", BASE);
  const browser = await chromium.launch({
    headless: true,
    channel: process.env.PW_CHANNEL || "chrome",
  });
  try {
    await captureDesktop(browser);
    await captureMobile(browser);
  } finally {
    await browser.close();
  }

  // Manifest
  const walk = (dir, acc = []) => {
    for (const ent of fs.readdirSync(dir, { withFileTypes: true })) {
      const p = path.join(dir, ent.name);
      if (ent.isDirectory()) walk(p, acc);
      else if (ent.name.endsWith(".png")) acc.push(path.relative(ROOT, p).replace(/\\/g, "/"));
    }
    return acc;
  };
  const files = walk(OUT).sort();
  const report = [
    "# Screenshot capture report",
    "",
    `Captured: ${new Date().toISOString()}`,
    `Base URL: ${BASE}`,
    `Viewport desktop: ${DESKTOP.width}×${DESKTOP.height}`,
    `Viewport mobile: 390×844`,
    "",
    "## Files",
    "",
    ...files.map((f) => `- \`${f}\``),
    "",
  ].join("\n");
  fs.writeFileSync(path.join(OUT, "CAPTURE_REPORT.md"), report);
  console.log("Wrote docs/screenshots/CAPTURE_REPORT.md");
  console.log(`Total screenshots: ${files.length}`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
