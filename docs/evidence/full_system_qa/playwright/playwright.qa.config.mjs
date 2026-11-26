import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

/** @type {import('@playwright/test').PlaywrightTestConfig} */
export default {
  testDir: __dirname,
  testMatch: ["**/*.spec.mjs"],
  timeout: 180_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [
    ["list"],
    ["html", { outputFolder: path.join(__dirname, "html-report"), open: "never" }],
    ["json", { outputFile: path.join(__dirname, "..", "logs", "playwright-results.json") }],
  ],
  outputDir: path.join(__dirname, "test-results"),
  use: {
    baseURL: process.env.QA_BASE_URL || "http://localhost:3000",
    channel: "chrome",
    headless: true,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "off",
    viewport: { width: 1440, height: 900 },
  },
};
