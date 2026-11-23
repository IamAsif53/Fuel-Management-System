import fs from "node:fs";
import path from "node:path";

async function run() {
  const targetUrl = process.argv[2] || "http://localhost:3000/overview";
  const outputPath = process.argv[3] || "F:/NewTask/fuel-platform/docs/evidence/screenshots/phase03-overview.png";

  console.log(`Creating target tab for ${targetUrl}...`);
  const res = await fetch(`http://127.0.0.1:9222/json/new?${encodeURIComponent(targetUrl)}`, {
    method: "PUT",
  });
  const tab = await res.json();
  const wsUrl = tab.webSocketDebuggerUrl;
  console.log(`Connecting to CDP at ${wsUrl}...`);

  const ws = new WebSocket(wsUrl);

  await new Promise((resolve) => ws.addEventListener("open", resolve, { once: true }));

  console.log("Setting viewport to 1440x900...");
  ws.send(
    JSON.stringify({
      id: 1,
      method: "Emulation.setDeviceMetricsOverride",
      params: {
        width: 1440,
        height: 900,
        deviceScaleFactor: 1,
        mobile: false,
      },
    })
  );

  console.log("Waiting 3.5s for React Query state fetch and render...");
  await new Promise((r) => setTimeout(r, 3500));

  console.log("Capturing screenshot...");
  ws.send(
    JSON.stringify({
      id: 2,
      method: "Page.captureScreenshot",
      params: {
        format: "png",
        captureBeyondViewport: false,
      },
    })
  );

  const screenshotData = await new Promise((resolve, reject) => {
    ws.addEventListener("message", (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.id === 2) {
          if (msg.result && msg.result.data) {
            resolve(msg.result.data);
          } else {
            reject(new Error("No screenshot data in response: " + JSON.stringify(msg)));
          }
        }
      } catch (e) {
        reject(e);
      }
    });
  });

  const buffer = Buffer.from(screenshotData, "base64");
  fs.mkdirSync(path.dirname(outputPath), { recursive: true });
  fs.writeFileSync(outputPath, buffer);
  console.log(`Saved screenshot to ${outputPath} (${buffer.length} bytes)`);

  // Close tab
  await fetch(`http://127.0.0.1:9222/json/close/${tab.id}`);
  ws.close();
}

run().catch((err) => {
  console.error("Error capturing screenshot:", err);
  process.exit(1);
});
