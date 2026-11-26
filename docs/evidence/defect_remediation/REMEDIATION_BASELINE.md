# FSIP — Remediation Baseline & Repository Protection Record

**Captured:** 2026-11-26T13:30+06:00  
**Branch:** `main`  
**Starting HEAD:** `3a2ae7889557d880dbcf8a6fcbe8065a4d0fcf5f`  
**Missing Reference Doc Checked:** `24_PHASE00_FILE_CONTENTS.md` — confirmed absent across repository (`docs/architecture/` starts from `01`..`23`); specification resolution proceeds via `20_FINAL_SPEC_LOCK.md` > `21_API_CONTRACTS.md` > `23_MODULE_SPECS.md` > `22_UI_SPEC.md` > `13_ARCHITECTURE_V2.md`.

## Protected Pre-Existing Dirty Tree
Full patch saved verbatim prior to any modifications: `docs/evidence/defect_remediation/pre_remediation_dirty_tree.patch`.

Pre-existing modified files (`11` tracked):
- `frontend/src/pages/Decisions.tsx`
- `frontend/src/pages/Recommendations.tsx`
- `services/api/app/db.py`
- `services/api/app/routers/decisions.py`
- `services/api/app/services/state_service.py`
- `services/api/tests/test_decisions_api.py`
- `services/engine/app/db/models.py`
- `services/engine/app/decision/engine.py`
- `services/engine/app/state/sync.py`
- `services/engine/tests/test_engine_decision.py`
- `services/intelligence/artifacts/active.json`

Pre-existing untracked files (`5` paths):
- `docs/evidence/decision_recommendation_inconsistency_repair.md`
- `docs/evidence/full_system_qa/`
- `docs/evidence/remote_access_diagnostics.md`
- `docs/evidence/temporary_public_access.md`
- `scripts/verify_consistency.py`

## Running Containers at Start
14 active containers: `fsip-simulator`, `fsip-timescaledb`, `fsip-redis`, `fsip-engine`, `fsip-intelligence`, `fsip-api-1`, `fsip-frontend`, `fsip-prometheus`, `fsip-grafana`, `fsip-alertmanager`, `fsip-loki`, `fsip-promtail`, `fsip-cadvisor`, `fsip-node-exporter`.

## Simulator & Engine Baseline at Start
- `sim_run_id`: `2040`
- `tick`: `2`
- `sim_status`: `PAUSED`
- `engine_mode`: `NORMAL`
- `operating_mode`: `advisory`
- `degraded`: `false` (`degraded_reasons: []`)
- Active simulator events: `[]` (Count `0`)
