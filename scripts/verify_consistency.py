import urllib.request
import json
from datetime import datetime, timezone

def get(url):
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())

now = datetime.now(timezone.utc).isoformat()
print(f"=== CAPTURE AT {now} ===")

state = get("http://localhost:18080/api/state")
run_id = state.get("sim_run_id")
tick = state.get("tick")
status = state.get("sim_status")
pending = state.get("pending_recommendations")
print(f"1. GET /api/state: sim_run_id={run_id}, tick={tick}, sim_status={status}")
print(f"   pending_recommendations count: {len(pending or [])}")
for p in (pending or []):
    print(f"   pending item: decision_id={p.get('decision_id')}, expires_at_tick={p.get('expires_at_tick')}, total_liters={p.get('total_liters')}")

recs = get("http://localhost:18080/api/recommendations")
items = recs.get("items", [])
print(f"\n2. GET /api/recommendations: count={len(items)}")
for r in items:
    print(f"   rec item: id={r.get('id')}, sim_run_id={r.get('sim_run_id')}, tick={r.get('tick')}, status={r.get('status')}, expires_at_tick={r.get('expires_at_tick')}")

decs = get(f"http://localhost:18080/api/decisions?sim_run_id={run_id}")
dec_items = decs.get("items", [])
print(f"\n3. GET /api/decisions?sim_run_id={run_id}: total={decs.get('total')}")
for d in dec_items[:6]:
    print(f"   decision item: id={d.get('id')}, tick={d.get('tick')}, status={d.get('status')}, expires_at_tick={d.get('expires_at_tick')}, rejected_reason={d.get('rejected_reason')}")

# Consistency Assertions
pending_ids_in_state = [p["decision_id"] for p in (pending or [])]
pending_ids_in_recs = [r["id"] for r in items]
print(f"\n=== CONSISTENCY CHECK ===")
print(f"pending_recommendations in state: {pending_ids_in_state}")
print(f"active recommendations:           {pending_ids_in_recs}")
assert pending_ids_in_state == pending_ids_in_recs, f"Mismatch: state has {pending_ids_in_state} but recs has {pending_ids_in_recs}"
print("SUCCESS: /api/state and /api/recommendations agree on active run and pending decision IDs!")
