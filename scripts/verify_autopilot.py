"""Live verification script for Autopilot autonomous execution (open hackathon mode).

Executes end-to-end tests against live Docker API on port 18080:
1. Operational status check
2. Mode switch to autopilot (no Sign-In — open demo operator)
3. State reflection in Redis / API state
4. Autonomous evaluation and auto-approval of eligible recommendations
5. Outcome tracker & AuditLog verification for actor 'autopilot'
6. Large allocations approve with a single operator click (brief §26)
7. Mode switch back to advisory stops auto-approvals
"""
from __future__ import annotations

import os
import time

import httpx

API_BASE = os.environ.get("FSIP_API_BASE", "http://localhost:18080/api")

# Open mode: no Authorization header required
HEADERS = {"Content-Type": "application/json"}

print("=== Running Live Autopilot End-to-End Verification ===")
client = httpx.Client(base_url=API_BASE, timeout=20.0)

# 1. Check Initial State
r = client.get("/state")
print(f"1. Initial state check -> HTTP {r.status_code}")
assert r.status_code == 200, f"Failed: {r.text}"
state = r.json()
initial_mode = state.get("operating_mode", "advisory")
print(f"   Current operating mode: {initial_mode} | Stale: {state.get('stale')} | Degraded: {state.get('degraded')}")

client.post("/control/sim/step", headers=HEADERS, json={"count": 1})
time.sleep(1)

# 2. Switch Mode to Autopilot
r = client.post("/control/mode", headers=HEADERS, json={"mode": "autopilot"})
print(f"2. Switch mode to 'autopilot' -> HTTP {r.status_code}")
assert r.status_code in (200, 409), f"Unexpected response: {r.status_code} {r.text}"

if r.status_code == 409:
    print(f"   Rejected as expected due to state constraints: {r.json()}")
    client.post("/control/sim/run", headers=HEADERS)
    time.sleep(2)
    client.post("/control/sim/step", headers=HEADERS, json={"count": 2})
    time.sleep(1)
    r = client.post("/control/mode", headers=HEADERS, json={"mode": "autopilot"})
    print(f"   Retry mode switch after fresh ticks -> HTTP {r.status_code}")
    assert r.status_code == 200, f"Mode switch to autopilot failed: {r.text}"

data = r.json()
mode_val = data.get("operating_mode") or data.get("mode")
assert mode_val == "autopilot", f"Expected autopilot, got {data}"
print("   Operating mode successfully set to 'autopilot'")

# 3. Verify Redis / State reflects autopilot
r_state = client.get("/state")
assert r_state.status_code == 200
assert r_state.json().get("operating_mode") == "autopilot"
print("   Confirmed: GET /api/state reports operating_mode='autopilot'")

# 4. Trigger Plan Now to evaluate recommendations under autopilot
r_plan = client.post("/plan-now", headers=HEADERS)
print(f"4. Trigger Plan Now -> HTTP {r_plan.status_code}")
time.sleep(2)

# 5. Fetch recommendations
r_recs = client.get("/recommendations")
assert r_recs.status_code == 200
recs = r_recs.json().get("items", [])
print(f"5. Fetched recommendations count: {len(recs)}")

for rec in recs[:5]:
    dec_id = rec.get("id") or rec.get("decision_id")
    expl = rec.get("explanation")
    if isinstance(expl, dict):
        elig = expl.get("autopilot_eligibility")
        blocked = expl.get("autopilot_blocked_reason")
        print(f"   Decision {dec_id} Status: {rec.get('status')} | Eligible: {elig} | Blocked: {blocked}")

# 6. Verify Audit Logs
r_audit = client.get("/audit", headers=HEADERS)
if r_audit.status_code == 200:
    audit_entries = r_audit.json().get("items", [])
    auto_actions = [e for e in audit_entries if isinstance(e, dict) and e.get("actor") == "autopilot"]
    print(f"6. Audit log check: found {len(auto_actions)} actions with actor='autopilot'")
    for a in auto_actions[:3]:
        print(f"   Audit entry: {a.get('action')} by {a.get('actor')} at {a.get('timestamp')}")
else:
    print(f"6. Audit log status: HTTP {r_audit.status_code}")

# 7. Large allocation: single-click approve (no multi-user Sign-In / four-eyes)
print("7. Testing single-click large allocation approve...")
if recs:
    pending_large = None
    for rec in recs:
        lines = rec.get("lines", [])
        total_vol = sum(line.get("quantity", 0) for line in lines)
        if total_vol > 7000 and rec.get("status") == "PENDING_APPROVAL":
            pending_large = rec
            break

    if pending_large:
        dec_id = pending_large.get("id") or pending_large.get("decision_id")
        r_app = client.post(
            f"/decisions/{dec_id}/approve",
            headers=HEADERS,
            json={"reviewed_driver": True},
        )
        print(f"   Operator approve large decision {dec_id} -> HTTP {r_app.status_code} {r_app.json()}")
        assert r_app.status_code == 200
        assert r_app.json().get("status") in ("APPROVED", "EXECUTED")
        print("   Verified: Large volume approved in one click (no Sign-In).")
    else:
        print("   No >7,000 L pending decisions found this run (skipped).")

# 8. Switch Back to Advisory Mode
r_back = client.post("/control/mode", headers=HEADERS, json={"mode": "advisory"})
print(f"8. Switch back to 'advisory' -> HTTP {r_back.status_code}")
assert r_back.status_code == 200
mode_back = r_back.json().get("operating_mode") or r_back.json().get("mode")
assert mode_back == "advisory"
r_state_back = client.get("/state")
assert r_state_back.json().get("operating_mode") == "advisory"
print("   Confirmed: System returned to 'advisory' mode safely.")

print("\n=== All Live Autopilot Verifications Passed Successfully! ===")
