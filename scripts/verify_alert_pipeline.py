import json
import time
import urllib.request

import redis

API_URL = "http://localhost:18080"
FRONTEND_PROXY_URL = "http://localhost:3000"
REDIS_HOST = "localhost"
REDIS_PORT = 16379

alert_payload = {
    "receiver": "fsip-api-webhook",
    "status": "firing",
    "alerts": [
        {
            "status": "firing",
            "labels": {
                "alertname": "ServiceLevelDrop",
                "severity": "critical",
                "service": "engine",
            },
            "annotations": {
                "summary": "Simulated Service Level Drop to 96.5%",
                "description": "Network fuel service level dropped below 97% critical platform threshold.",
            },
            "startsAt": "2026-11-25T03:30:00Z",
            "endsAt": "0001-01-01T00:00:00Z",
        }
    ],
}

print("=" * 80)
print("ALERT PIPELINE VERIFICATION: Alertmanager -> API -> Redis Stream -> Frontend")
print("=" * 80)

# 1. Post to Alertmanager Webhook
print("[1] Dispatching test alert to API webhook: /api/internal/alertmanager...")
req = urllib.request.Request(
    f"{API_URL}/api/internal/alertmanager",
    data=json.dumps(alert_payload).encode("utf-8"),
    headers={
        "Content-Type": "application/json",
        "Authorization": "Bearer change-me-internal",
    },
)
try:
    with urllib.request.urlopen(req, timeout=5) as resp:
        body = json.loads(resp.read().decode())
        print(f"    Status: {resp.status} OK | Response: {body}")
        assert body.get("status") == "ok"
except Exception as e:
    print(f"    Failed: {e}")
    raise

# 2. Check Redis Stream fsip:domain-events
print("\n[2] Checking Redis stream 'fsip:domain-events' for alert lifecycle event...")
r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
stream_entries = r.xrevrange("fsip:domain-events", count=5)
found_stream_event = False
for entry_id, fields in stream_entries:
    if fields.get("type") == "alert.raised":
        data = json.loads(fields.get("data", "{}"))
        if data.get("title") == "SYSTEM: ServiceLevelDrop":
            found_stream_event = True
            print(f"    Found event in stream offset {entry_id}:")
            print(f"    Type: {fields.get('type')}, Title: {data.get('title')}, Severity: {data.get('severity')}")
            break

assert found_stream_event, "Event not found in Redis stream!"

# 3. Check API /api/alerts (persisted state)
print("\n[3] Checking REST API: GET /api/alerts?status=OPEN...")
time.sleep(0.5)
with urllib.request.urlopen(f"{API_URL}/api/alerts?status=OPEN", timeout=5) as resp:
    alerts_data = json.loads(resp.read().decode())
    items = alerts_data.get("items", [])
    print(f"    Total OPEN alerts: {alerts_data.get('total')}")
    target_alert = None
    for a in items:
        if a.get("title") == "SYSTEM: ServiceLevelDrop":
            target_alert = a
            break

assert target_alert is not None, "Target alert not found in /api/alerts!"
alert_msg = (
    f"    Found persisted alert: ID={target_alert.get('id')}, "
    f"Category={target_alert.get('category')}, Status={target_alert.get('status')}"
)
print(alert_msg)

# 4. Check Frontend Proxy: GET http://localhost:3000/api/alerts
print("\n[4] Checking Frontend reverse proxy delivery: GET http://localhost:3000/api/alerts...")
with urllib.request.urlopen(f"{FRONTEND_PROXY_URL}/api/alerts?status=OPEN", timeout=5) as resp:
    fe_alerts = json.loads(resp.read().decode())
    fe_items = fe_alerts.get("items", [])
    fe_target = next((a for a in fe_items if a.get("title") == "SYSTEM: ServiceLevelDrop"), None)
    assert fe_target is not None, "Alert not returned through frontend proxy!"
    print(f"    Frontend proxy successfully served alert #{fe_target.get('id')}: '{fe_target.get('title')}'")

print("\n" + "=" * 80)
print("PIPELINE TEST PASSED: Alertmanager -> API -> Redis Stream -> Frontend UI verified.")
print("=" * 80)
