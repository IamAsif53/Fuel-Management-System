"""Live verification script for Simulator Control Panel & Chaos Drills (Phase 10).

Executes end-to-end tests against live Docker API on port 18080:
1. Status and options discovery
2. Simulation stepping (x1, x10)
3. Pause and run state transitions
4. All 8 crisis scenario presets
5. All 5 chaos fault vectors
6. Clear active faults
7. Audit log persistence
8. Authorization enforcement
"""
from __future__ import annotations

import httpx

API_BASE = "http://localhost:18080/api"
HEADERS = {
    "Authorization": "Bearer change-me-operator",
    "Content-Type": "application/json",
}

print("=== Running Live Control Panel End-to-End Verification ===")
client = httpx.Client(base_url=API_BASE, timeout=15.0)

# 1. Status Check
r = client.get("/control/status")
print(f"1. Status check -> HTTP {r.status_code}")
assert r.status_code == 200, f"Failed: {r.text}"
status_data = r.json()
assert status_data["demo_controls_enabled"] is True, "DEMO_CONTROLS should be enabled"
assert len(status_data["presets"]) == 8, f"Expected 8 presets, got {len(status_data['presets'])}"
assert len(status_data["faults"]) == 5, f"Expected 5 faults, got {len(status_data['faults'])}"
initial_tick = status_data["tick"]
print(f"   Sim state: {status_data['sim_status']} | Tick: {initial_tick} | Presets: {len(status_data['presets'])}")

# 2. Stepping x1
r = client.post("/control/sim/step", headers=HEADERS, json={"count": 1})
print(f"2. Step x1 -> HTTP {r.status_code}")
assert r.status_code == 200
step1_data = r.json()
assert step1_data["count"] == 1
assert step1_data["tick"] == initial_tick + 1
print(f"   Advanced from tick {initial_tick} -> {step1_data['tick']}")

# 3. Stepping x10
r = client.post("/control/sim/step", headers=HEADERS, json={"count": 10})
print(f"3. Step x10 -> HTTP {r.status_code}")
assert r.status_code == 200
step10_data = r.json()
assert step10_data["count"] == 10
assert step10_data["tick"] == initial_tick + 11
print(f"   Advanced +10 ticks -> tick {step10_data['tick']}")

# 4. Run & Pause
r_run = client.post("/control/sim/run", headers=HEADERS)
print(f"4a. Run -> HTTP {r_run.status_code}")
assert r_run.status_code == 200
assert r_run.json()["sim"]["status"] == "RUNNING"

r_pause = client.post("/control/sim/pause", headers=HEADERS)
print(f"4b. Pause -> HTTP {r_pause.status_code}")
assert r_pause.status_code == 200
assert r_pause.json()["sim"]["status"] == "PAUSED"

# 5. Test all 8 Crisis Presets
presets = [
    "demand_spike_dhaka",
    "route_cut_mirpur",
    "depot_constraint_patiya",
    "shipment_delay_gazipur",
    "supply_shortfall",
    "station_outage_tongi",
    "single_route_cut",
    "combined_crisis",
]
print("\n5. Testing 8 Crisis Presets:")
for p in presets:
    r_preset = client.post(f"/control/scenario/{p}", headers=HEADERS)
    assert r_preset.status_code == 200, f"Preset {p} failed: {r_preset.text}"
    p_data = r_preset.json()
    print(f"   ✓ Preset '{p}': title='{p_data['title']}' | injected={len(p_data['events_injected'])} event(s)")

# 6. Test 5 Chaos Fault Vectors
faults = ["unavailable", "error_rate", "latency", "stale_data", "stream_disconnect"]
print("\n6. Testing 5 Chaos Fault Vectors:")
for f in faults:
    r_fault = client.post(f"/control/chaos/{f}", headers=HEADERS, json={"duration_seconds": 30})
    assert r_fault.status_code == 200, f"Fault {f} failed: {r_fault.text}"
    print(f"   ✓ Fault '{f}': duration={r_fault.json()['duration_seconds']}s")

# 7. Clear Faults
r_clear = client.post("/control/chaos/clear", headers=HEADERS)
print(f"\n7. Clear Faults -> HTTP {r_clear.status_code}")
assert r_clear.status_code == 200

# 8. Check Control Action Audit Logs
r_logs = client.get("/control/logs")
print(f"8. Control Audit Logs -> HTTP {r_logs.status_code}")
assert r_logs.status_code == 200
logs = r_logs.json().get("logs", [])
assert len(logs) >= 5, f"Expected at least 5 audit logs, got {len(logs)}"
print(f"   Retrieved {len(logs)} audit action records (newest: {logs[0]['action']} by {logs[0]['actor']})")

# 9. Authorization Protection
r_unauth = client.post("/control/sim/run")
print(f"9. Unauthorized check -> HTTP {r_unauth.status_code}")
assert r_unauth.status_code == 401
assert r_unauth.json()["detail"]["code"] == "UNAUTHORIZED"

print("\n>>> ALL 9 LIVE CONTROL PANEL CHECKS VERIFIED SUCCESSFULLY! <<<")
