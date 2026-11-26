import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { test, expect } = require("f:/NewTask/fuel-platform/frontend/node_modules/@playwright/test");

const ROOT_QA = path.resolve("f:/NewTask/fuel-platform/docs/evidence/full_system_qa");
const SHOTS_DIR = path.join(ROOT_QA, "screenshots");
const LOGS_DIR = path.join(ROOT_QA, "logs");

const TUNNEL_URL = process.env.QA_TUNNEL_URL || "https://utilities-toddler-proxy-menu.trycloudflare.com";
const LOCAL_URL = process.env.QA_BASE_URL || "http://localhost:3000";

const ROUTES = [
  { path: "/overview", name: "overview" },
  { path: "/network", name: "network" },
  { path: "/risk", name: "risk" },
  { path: "/recommendations", name: "recommendations" },
  { path: "/decisions", name: "decisions" },
  { path: "/alerts", name: "alerts" },
  { path: "/whatif", name: "whatif" },
  { path: "/assistant", name: "assistant" },
  { path: "/health", name: "health" },
  { path: "/control", name: "control" },
];

const VIEWPORTS = [
  { name: "desktop_1440", width: 1440, height: 900 },
  { name: "laptop_1280", width: 1280, height: 720 },
  { name: "tablet_768", width: 768, height: 1024 },
  { name: "mobile_390", width: 390, height: 844 },
];

const auditReport = {
  timestamp: new Date().toISOString(),
  tunnelUrl: TUNNEL_URL,
  localUrl: LOCAL_URL,
  tunnelSmoke: {},
  routes: {},
  fallbackRoutes: {},
  tailwindTokens: {},
  responsive: [],
  uiInteractions: {},
};

function attachCollectors(page, bucket) {
  page.on("console", (msg) => {
    const type = msg.type();
    if (type === "error" || type === "warning") {
      bucket.console.push({ type, text: msg.text() });
    }
  });
  page.on("pageerror", (err) => {
    bucket.pageErrors.push(String(err));
  });
  page.on("response", (res) => {
    if (res.status() >= 400) {
      bucket.httpErrors.push({
        url: res.url(),
        status: res.status(),
        statusText: res.statusText(),
      });
    }
  });
  page.on("requestfailed", (req) => {
    bucket.failedRequests.push({
      url: req.url(),
      method: req.method(),
      failure: req.failure()?.errorText || "unknown",
    });
  });
}

test.describe("Stage 3 & 4 — Live Browser UI, Tunnel, Styles & Responsive Audit", () => {
  test.afterAll(() => {
    fs.writeFileSync(
      path.join(LOGS_DIR, "stage3_ui_responsive_audit.json"),
      JSON.stringify(auditReport, null, 2),
      "utf-8",
    );
  });

  test("1. Live Public Tunnel access check & security surface check", async ({ browser }) => {
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const page = await context.newPage();
    const bucket = { console: [], pageErrors: [], httpErrors: [], failedRequests: [] };
    attachCollectors(page, bucket);

    const res = await page.goto(TUNNEL_URL, { waitUntil: "domcontentloaded" });
    expect(res?.status()).toBe(200);
    await page.waitForURL(/\/overview/, { timeout: 15_000 });
    await page.waitForTimeout(2000);

    const h1Text = await page.locator("h1").first().textContent({ timeout: 1000 }).catch(() => null);
    const bodyText = await page.locator("body").innerText({ timeout: 5000 });
    await page.screenshot({ path: path.join(SHOTS_DIR, "tunnel_overview_1440.png"), fullPage: true });

    // Check exposed paths via tunnel
    const probePaths = ["/api/state", "/admin", "/v1/instance", "/.env", "/.git/HEAD"];
    const probes = {};
    for (const p of probePaths) {
      const r = await context.request.get(`${TUNNEL_URL}${p}`);
      const txt = await r.text();
      probes[p] = { status: r.status(), contentType: r.headers()["content-type"] || "", len: txt.length, sample: txt.slice(0, 140) };
    }

    auditReport.tunnelSmoke = {
      finalUrl: page.url(),
      h1Text,
      renderedNonEmpty: bodyText.trim().length > 100,
      hasDegradedBanner: bodyText.includes("DEGRADED") || bodyText.includes("Degraded"),
      consoleErrors: bucket.console.filter((c) => c.type === "error"),
      pageErrors: bucket.pageErrors,
      httpErrors: bucket.httpErrors,
      failedRequests: bucket.failedRequests,
      probes,
    };

    await context.close();
    expect(bodyText.trim().length).toBeGreaterThan(50);
  });

  test("2. All Routes audit (console, network, direct reload, back/forward, fallback routes, Tailwind tokens)", async ({ page }) => {
    for (const r of ROUTES) {
      const bucket = { console: [], pageErrors: [], httpErrors: [], failedRequests: [] };
      const tempPage = await page.context().newPage();
      attachCollectors(tempPage, bucket);

      await tempPage.goto(`${LOCAL_URL}${r.path}`, { waitUntil: "domcontentloaded" });
      await tempPage.waitForTimeout(1800);

      const urlAfterLoad = tempPage.url();
      const textLen = (await tempPage.locator("body").innerText({ timeout: 3000 })).trim().length;
      const heading = await tempPage.locator("h1, h2").first().textContent({ timeout: 1000 }).catch(() => "");

      // Direct reload test
      await tempPage.reload({ waitUntil: "domcontentloaded" });
      await tempPage.waitForTimeout(1200);
      const urlAfterReload = tempPage.url();

      await tempPage.screenshot({
        path: path.join(SHOTS_DIR, `route_${r.name}_1440.png`),
        fullPage: true,
      });

      // Specific inspection per page
      let extra = {};
      if (r.name === "health") {
        const hrefs = await tempPage.locator("a").evaluateAll((els) => els.map((a) => ({ text: a.textContent?.trim(), href: a.getAttribute("href") })));
        extra = { externalLinks: hrefs };
      } else if (r.name === "recommendations") {
        const pageTxt = await tempPage.locator("body").innerText({ timeout: 2000 });
        extra = {
          containsHardcodedMetrics:
            pageTxt.includes("92.4%") && pageTxt.includes("7.6%") && pageTxt.includes("4.2 s"),
        };
      } else if (r.name === "overview") {
        const bodyTxt = await tempPage.locator("body").innerText({ timeout: 2000 });
        extra = { bodySnippet: bodyTxt.slice(0, 600) };
      } else if (r.name === "decisions") {
        const decTxt = await tempPage.locator("body").innerText({ timeout: 2000 });
        extra = {
          showsZeroLinesBug: decTxt.includes("0 L across 0 lines"),
          showsPastRunsUnderActiveScope: decTxt.includes("Active Run") && decTxt.includes("(Past)"),
        };
      }

      auditReport.routes[r.path] = {
        urlAfterLoad,
        urlAfterReload,
        textLen,
        heading: heading?.trim(),
        consoleErrors: bucket.console.filter((c) => c.type === "error"),
        consoleWarnings: bucket.console.filter((c) => c.type === "warning").slice(0, 10),
        pageErrors: bucket.pageErrors,
        httpErrors: bucket.httpErrors,
        failedRequests: bucket.failedRequests,
        extra,
      };

      await tempPage.close();
    }

    // Back / Forward browser history test across 3 routes
    await page.goto(`${LOCAL_URL}/overview`, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(600);
    await page.goto(`${LOCAL_URL}/risk`, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(600);
    await page.goto(`${LOCAL_URL}/decisions`, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(600);
    await page.goBack();
    await page.waitForTimeout(400);
    const backUrl = page.url();
    await page.goForward();
    await page.waitForTimeout(400);
    const forwardUrl = page.url();

    // Missing / spec-divergent routes check: /decisions/1, /system, /network?station=STN-004
    for (const p of ["/decisions/1", "/system", "/network?station=STN-004", "/nonexistent-route"]) {
      await page.goto(`${LOCAL_URL}${p}`, { waitUntil: "domcontentloaded" });
      await page.waitForTimeout(800);
      const drawerVisible = await page.locator("aside, [role='dialog']").count();
      const heading = await page.locator("h1, h2").first().textContent({ timeout: 1000 }).catch(() => "");
      auditReport.fallbackRoutes[p] = {
        resolvedUrl: page.url(),
        heading: heading?.trim(),
        drawerOrDialogCount: drawerVisible,
      };
    }

    // Computed style probe for undefined Tailwind theme tokens (`bg-surface`, `text-accent`, `border-panel-border`, `bg-bg-base`)
    await page.goto(`${LOCAL_URL}/network`, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(1000);
    const tokenStyles = await page.evaluate(() => {
      const probeClasses = ["bg-surface", "text-accent", "bg-accent", "border-panel-border", "bg-bg-base", "text-ink-contrast"];
      const out = {};
      for (const cls of probeClasses) {
        const el = document.createElement("div");
        el.className = cls;
        el.textContent = "probe";
        el.style.borderStyle = "solid";
        el.style.borderWidth = "1px";
        document.body.appendChild(el);
        const cs = getComputedStyle(el);
        out[cls] = {
          backgroundColor: cs.backgroundColor,
          color: cs.color,
          borderColor: cs.borderColor,
        };
        el.remove();
      }
      // Also inspect actual active tab button on /network
      const activeBtn = Array.from(document.querySelectorAll("button")).find((b) =>
        b.className.includes("bg-accent") || b.textContent?.includes("Topology"),
      );
      out.networkActiveBtn = activeBtn
        ? {
            text: activeBtn.textContent?.trim(),
            className: activeBtn.className,
            bg: getComputedStyle(activeBtn).backgroundColor,
            color: getComputedStyle(activeBtn).color,
          }
        : null;
      return out;
    });

    // Command Palette (Ctrl+K) + EntityDrawer check on /overview
    await page.goto(`${LOCAL_URL}/overview`, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(1000);
    await page.keyboard.press("Control+k");
    await page.waitForTimeout(400);
    const paletteInputCount = await page.locator('input[placeholder*="Search"], input[placeholder*="command"]').count();
    let paletteNavUrl = null;
    if (paletteInputCount > 0) {
      await page.keyboard.type("Alerts");
      await page.waitForTimeout(300);
      await page.screenshot({ path: path.join(SHOTS_DIR, "command_palette_open.png") });
      await page.keyboard.press("Enter");
      await page.waitForTimeout(500);
      paletteNavUrl = page.url();
    }

    auditReport.tailwindTokens = tokenStyles;
    auditReport.uiInteractions = {
      backUrl,
      forwardUrl,
      commandPaletteOpened: paletteInputCount > 0,
      commandPaletteNavUrl: paletteNavUrl,
    };
  });

  test("3. Responsive Layout & Overflow Sweep across 4 viewports", async ({ browser }) => {
    for (const vp of VIEWPORTS) {
      const context = await browser.newContext({ viewport: { width: vp.width, height: vp.height } });
      const page = await context.newPage();

      for (const r of ROUTES) {
        await page.goto(`${LOCAL_URL}${r.path}`, { waitUntil: "domcontentloaded" });
        await page.waitForTimeout(900);

        const metrics = await page.evaluate(() => {
          const doc = document.documentElement;
          const overflowPx = doc.scrollWidth - doc.clientWidth;
          const header = document.querySelector("header");
          const headerRect = header ? header.getBoundingClientRect() : null;
          const aside = document.querySelector("aside");
          const asideRect = aside ? aside.getBoundingClientRect() : null;
          return {
            clientWidth: doc.clientWidth,
            scrollWidth: doc.scrollWidth,
            overflowPx,
            scrollHeight: doc.scrollHeight,
            headerHeight: headerRect ? Math.round(headerRect.height) : null,
            sidebarWidth: asideRect ? Math.round(asideRect.width) : null,
          };
        });

        auditReport.responsive.push({
          viewport: vp.name,
          width: vp.width,
          height: vp.height,
          route: r.path,
          ...metrics,
        });

        // Save screenshots for laptop/tablet/mobile on key pages or whenever overflow occurs
        if (
          vp.name !== "desktop_1440" &&
          (["overview", "network", "recommendations", "decisions", "whatif", "control"].includes(r.name) ||
            metrics.overflowPx > 2)
        ) {
          await page.screenshot({
            path: path.join(SHOTS_DIR, `resp_${r.name}_${vp.name}.png`),
            fullPage: false,
          });
        }
      }
      await context.close();
    }
  });
});
