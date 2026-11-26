# FSIP Final Release Verification — Baseline

**Captured:** 2026-11-26 (UTC+6)  
**Branch:** `docs/fsip-plan-quality-evaluation`  
**HEAD:** `3d7171bbedac7dff3865b125e02aa4f62adfe17d`  
**Working tree:** Dirty — prior DEF remediation + Sign-In removal (open-mode) uncommitted; preserved intact.

## Runtime

| Component | Status | Notes |
|---|---|---|
| Frontend `:3000` | healthy | Sign-In removed; Operator chip |
| API `:18080` | healthy | Open demo operator (no bearer) |
| Engine `:8100` | healthy | |
| Simulator `:8000` | healthy | |
| Redis `:16379` | healthy | `fsip:state` present; `fsip:domain-events` XLEN=2976 |
| TimescaleDB `:15432` | healthy | `decisions` count=500 |
| Grafana / Prometheus / Loki / Alertmanager | up | |

## Live state snapshot

- `sim_run_id`: 2248
- `tick`: 0
- `sim_status`: PAUSED
- `operating_mode`: advisory
- `stale`: false
- `degraded`: false
- `engine_mode`: NORMAL
- Open alerts: 0
- Pending recommendations: 1 (decision 1908, ~22990 L)

## Prior remediation claim

`docs/evidence/defect_remediation/FINAL_ACCEPTANCE_REPORT.md` claimed **24/24 FIXED**.  
This final pass **re-verifies** every defect against current code + live stack.  
**Intentional product change since that report:** Sign-In / multi-user four-eyes UI removed per participant brief §19/§26 and explicit user order. Human gate remains Approve/Reject.

## Constraints honored

- No `docker compose down -v`
- No simulator source changes
- No destructive git
- Unrelated dirty-tree work preserved
