from __future__ import annotations

import os
import httpx

# Open hackathon mode: no Sign-In / bearer required
headers = {"Content-Type": "application/json"}

print("=== Running Live What-If End-to-End Verification ===")
client = httpx.Client(timeout=10.0)

# 1. Baseline
r = client.post(
    "http://localhost:18080/api/whatif",
    json={"horizon_ticks": 48, "lines": [], "events": []},
    headers=headers,
)
print("1. Baseline HTTP status:", r.status_code)
assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
d = r.json()
print("   Scenarios returned:", [s["name"] for s in d["scenarios"]])
print("   DO NOTHING min_station_sl:", d["scenarios"][0]["min_station_sl"], "unmet:", d["scenarios"][0]["unmet"])

# 2. Custom dispatch
r = client.post(
    "http://localhost:18080/api/whatif",
    json={
        "horizon_ticks": 48,
        "lines": [{"route_id": "route-gazipur-mirpur", "fuel_type": "DIESEL", "quantity": 2400}],
        "events": [],
    },
    headers=headers,
)
print("2. Custom Dispatch HTTP status:", r.status_code)
assert r.status_code == 200
d = r.json()
dn_mirpur = next(
    s for s in d["scenarios"][0]["stations"]
    if s["station_id"] == "station-mirpur" and s["fuel_type"] == "DIESEL"
)
cust_mirpur = next(
    s for s in d["scenarios"][2]["stations"]
    if s["station_id"] == "station-mirpur" and s["fuel_type"] == "DIESEL"
)
print(
    f"   Mirpur DIESEL ending inv -> DO NOTHING: {dn_mirpur['ending_inv']} L "
    f"({dn_mirpur['delta_liters']} L) | CUSTOM: {cust_mirpur['ending_inv']} L ({cust_mirpur['delta_liters']} L)"
)
assert cust_mirpur["ending_inv"] > dn_mirpur["ending_inv"], "Custom should increase ending inventory"

# 3. Invalid Route ID ('r-1')
r = client.post(
    "http://localhost:18080/api/whatif",
    json={
        "horizon_ticks": 48,
        "lines": [{"route_id": "r-1", "fuel_type": "DIESEL", "quantity": 2400}],
        "events": [],
    },
    headers=headers,
)
print("3. Invalid Route ID HTTP status:", r.status_code, "detail:", r.json().get("detail"))
assert r.status_code == 422
assert r.json().get("detail", {}).get("code") == "INVALID_IDENTIFIER"

# 4. Infeasible Quantity (exceeding route max shipment of 7000 L)
r = client.post(
    "http://localhost:18080/api/whatif",
    json={
        "horizon_ticks": 48,
        "lines": [{"route_id": "route-gazipur-mirpur", "fuel_type": "DIESEL", "quantity": 8500}],
        "events": [],
    },
    headers=headers,
)
print("4. Infeasible Quantity HTTP status:", r.status_code, "detail:", r.json().get("detail"))
assert r.status_code == 422
assert r.json().get("detail", {}).get("code") == "INFEASIBLE_DISPATCH"

# 5. Station Outage
r = client.post(
    "http://localhost:18080/api/whatif",
    json={
        "horizon_ticks": 48,
        "lines": [],
        "events": [{
            "type": "station_outage",
            "start_offset": 0,
            "duration_ticks": 24,
            "parameters": {"station_id": "station-mirpur"},
        }],
    },
    headers=headers,
)
print("5. Station Outage HTTP status:", r.status_code)
assert r.status_code == 200
d = r.json()
print("   DO NOTHING min_station_sl:", d["scenarios"][0]["min_station_sl"], "unmet:", d["scenarios"][0]["unmet"], "L")
assert d["scenarios"][0]["unmet"] > 0, "Outage should produce unmet demand"
assert d["scenarios"][0]["min_station_sl"] < 1.0, "Outage should drop min station service level"

print("\n>>> ALL 5 LIVE HTTP ENDPOINT CHECKS VERIFIED SUCCESSFULLY! <<<")
