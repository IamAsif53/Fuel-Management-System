from __future__ import annotations

import os

import httpx

_login = httpx.post(
    os.environ.get("FSIP_API_BASE", "http://localhost:18080/api") + "/auth/login",
    json={
        "username": os.environ.get("FSIP_OPERATOR_USER", "operator"),
        "password": os.environ.get("FSIP_OPERATOR_PASS", "operator"),
    },
    timeout=10.0,
)
_login.raise_for_status()
headers = {
    "Authorization": f"Bearer {_login.json()['access_token']}",
    "Content-Type": "application/json",
}

print("=== Running Live What-If End-to-End Verification ===")
client = httpx.Client(timeout=10.0)

r = client.post(
    "http://localhost:18080/api/whatif",
    json={"horizon_ticks": 48, "lines": [], "events": []},
    headers=headers,
)
print("1. Baseline HTTP status:", r.status_code)
assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
print("What-If baseline OK")
