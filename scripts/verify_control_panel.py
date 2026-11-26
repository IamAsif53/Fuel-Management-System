"""Live verification script for Simulator Control Panel & Chaos Drills."""
from __future__ import annotations

import os

import httpx

API_BASE = os.environ.get("FSIP_API_BASE", "http://localhost:18080/api")

print("=== Running Live Control Panel End-to-End Verification ===")
client = httpx.Client(base_url=API_BASE, timeout=15.0)
_login = client.post(
    "/auth/login",
    json={
        "username": os.environ.get("FSIP_OPERATOR_USER", "operator"),
        "password": os.environ.get("FSIP_OPERATOR_PASS", "operator"),
    },
)
_login.raise_for_status()
HEADERS = {
    "Authorization": f"Bearer {_login.json()['access_token']}",
    "Content-Type": "application/json",
}

r = client.get("/control/status")
print(f"1. Status check -> HTTP {r.status_code}")
assert r.status_code == 200, f"Failed: {r.text}"
assert r.json()["demo_controls_enabled"] is True

r_run = client.post("/control/sim/run", headers=HEADERS)
print(f"2. Sim run -> HTTP {r_run.status_code}")
assert r_run.status_code == 200

r_unauth = client.post("/control/sim/run")
print(f"3. Unauthorized -> HTTP {r_unauth.status_code}")
assert r_unauth.status_code == 401

print("\n>>> CONTROL PANEL CHECKS VERIFIED <<<")
