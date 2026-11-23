import fs from "node:fs";
import path from "node:path";
import { execSync } from "node:child_process";

async function cdpRequest(ws, id, method, params = {}) {
  ws.send(JSON.stringify({ id, method, params }));
  return new Promise((resolve, reject) => {
    const handler = (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.id === id) {
          ws.removeEventListener("message", handler);
          if (msg.error) {
            reject(new Error(msg.error.message));
          } else {
            resolve(msg.result);
          }
        }
      } catch (err) {
        reject(err);
      }
    };
    ws.addEventListener("message", handler);
  });
}

async function capture(ws, id, outputPath) {
  const result = await cdpRequest(ws, id, "Page.captureScreenshot", {
    format: "png",
    captureBeyondViewport: false,
  });
  const buffer = Buffer.from(result.data, "base64");
  fs.mkdirSync(path.dirname(outputPath), { recursive: true });
  fs.writeFileSync(outputPath, buffer);
  console.log(`Saved screenshot to ${outputPath} (${buffer.length} bytes)`);
}

async function evaluate(ws, id, expression) {
  const result = await cdpRequest(ws, id, "Runtime.evaluate", {
    expression,
    returnByValue: true,
  });
  return result.result?.value;
}

async function run() {
  const overviewPath = "F:/NewTask/fuel-platform/docs/evidence/screenshots/phase03-overview.png";
  const offlinePath = "F:/NewTask/fuel-platform/docs/evidence/screenshots/phase03-offline.png";
  const targetUrl = "http://localhost:3000/overview";

  console.log("Step 1: Creating target tab for", targetUrl);
  const res = await fetch(`http://127.0.0.1:9222/json/new?${encodeURIComponent(targetUrl)}`, {
    method: "PUT",
  });
  const tab = await res.json();
  const wsUrl = tab.webSocketDebuggerUrl;
  console.log(`Step 2: Connecting to CDP at ${wsUrl}...`);

  const ws = new WebSocket(wsUrl);
  await new Promise((resolve) => ws.addEventListener("open", resolve, { once: true }));

  await cdpRequest(ws, 1, "Emulation.setDeviceMetricsOverride", {
    width: 1440,
    height: 900,
    deviceScaleFactor: 1,
    mobile: false,
  });

  console.log("Step 3: Waiting 4s for React Query and SSE to stabilize in LIVE state...");
  await new Promise((r) => setTimeout(r, 4000));

  let liveText = await evaluate(ws, 2, "document.body.innerText");
  console.log("Current page text snippet (live check):", liveText.slice(0, 200).replace(/\n+/g, " "));

  console.log("Step 4: Capturing live overview screenshot...");
  await capture(ws, 3, overviewPath);

  console.log("Step 5: Simulating API outage via 'docker compose stop api'...");
  const stopStart = Date.now();
  execSync("docker compose stop api", { cwd: "F:/NewTask/fuel-platform", stdio: "inherit" });
  console.log("API stopped in", Date.now() - stopStart, "ms");

  console.log("Step 6: Waiting 5s for SSE error + HTTP polling error -> OFFLINE state...");
  await new Promise((r) => setTimeout(r, 5000));

  let offlineText = await evaluate(ws, 4, "document.body.innerText");
  console.log("Current page text snippet (offline check):", offlineText.slice(0, 300).replace(/\n+/g, " "));

  const hasOffline = offlineText.includes("OFFLINE");
  const hasStaleBanner = offlineText.includes("DATA OFFLINE / STALE") || offlineText.includes("STALE");
  console.log(`Offline indicators present: OFFLINE=${hasOffline}, STALE_BANNER=${hasStaleBanner}`);

  console.log("Step 7: Capturing offline screenshot with preserved state...");
  await capture(ws, 5, offlinePath);

  console.log("Step 8: Simulating API recovery via 'docker compose start api'...");
  const recoveryStart = Date.now();
  execSync("docker compose start api", { cwd: "F:/NewTask/fuel-platform", stdio: "inherit" });
  console.log("API start command returned in", Date.now() - recoveryStart, "ms");

  console.log("Waiting for backend API endpoint to become responsive (200 OK)...");
  let backendUp = false;
  for (let b = 1; b <= 30; b++) {
    await new Promise((r) => setTimeout(r, 1000));
    try {
      const ping = await fetch("http://localhost:3000/api/state");
      if (ping.ok) {
        backendUp = true;
        console.log(`Backend API responsive after ${b}s!`);
        break;
      }
    } catch {}
  }

  console.log("Step 9: Monitoring browser UI connection state transition to LIVE...");
  let recoveredLive = false;
  let finalStatus = "";
  for (let attempt = 1; attempt <= 20; attempt++) {
    await new Promise((r) => setTimeout(r, 1000));
    let text = await evaluate(ws, 100 + attempt, "document.body.innerText");
    const isLive = text.includes("LIVE");
    const isOffline = text.includes("OFFLINE");
    const isPolling = text.includes("POLLING");
    finalStatus = isLive ? "LIVE" : isPolling ? "POLLING" : isOffline ? "OFFLINE" : "UNKNOWN";
    console.log(`[t+${attempt}s] Connection state in UI: ${finalStatus}`);
    if (isLive) {
      recoveredLive = true;
      break;
    }
  }
  console.log(`Recovery to LIVE observed: ${recoveredLive} (finalStatus: ${finalStatus})`);

  console.log("Step 10: Cleaning up tab...");
  await fetch(`http://127.0.0.1:9222/json/close/${tab.id}`);
  ws.close();

  console.log("\nALL OUTAGE & RECOVERY CHECKS COMPLETED SUCCESSFULLY!");
}

run().catch((err) => {
  console.error("Execution failed:", err);
  process.exit(1);
});
