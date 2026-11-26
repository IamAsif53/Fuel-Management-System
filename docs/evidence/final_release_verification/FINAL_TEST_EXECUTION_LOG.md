# FSIP — Final Test Execution Log

**Date:** 2026-11-26  
**Python:** `.venv\Scripts\python.exe`  
**Working tree root:** `F:\NewTask\fuel-platform`

| Test suite | Total | Passed | Failed | Skipped | Result | Log |
|---|---:|---:|---:|---:|---|---|
| ruff (`services/api,engine,intelligence`, `libs/fsip_common`, `evaluation`) | — | — | 0 | — | **PASS** | `logs/ruff_final.txt` |
| API pytest | 80 | 80 | 0 | 0 | **PASS** | `logs/pytest_api_final.txt` |
| Engine pytest | 333 (+32 deselected) | 333 | 0 | 0* | **PASS** | `logs/pytest_engine_final.txt` |
| Intelligence pytest | 8 | 8 | 0 | 0 | **PASS** | `logs/pytest_intelligence.log` |
| fsip_common pytest | 107 | 107 | 0 | 0 | **PASS** | `logs/pytest_fsip_common.log` |
| evaluation pytest | 8 | 5 | 0 | 3 | **PASS** (skips env-gated) | `logs/pytest_evaluation.log` |
| Frontend `tsc -b` | — | — | 0 | — | **PASS** | `logs/tsc.txt` / playwright terminal |
| Vitest | 64 | 64 | 0 | 0 | **PASS** | `logs/vitest.txt` |
| Playwright e2e | 4 | 4 | 0 | 0 | **PASS** | `logs/playwright_final.txt` |
| docker compose config | 1 | 1 | 0 | 0 | **PASS** | `logs/docker_compose_config.log` |
| ESLint | — | — | — | — | **NOT RUN** (no eslint script) | — |

\* Deselected engine tests are explicit marks, not failures.

## Commands (representative)

```text
ruff check services/api services/engine services/intelligence libs/fsip_common evaluation
PYTHONPATH=services/api python -m pytest services/api/tests -q
PYTHONPATH=services/engine python -m pytest services/engine/tests -q
cd frontend && npx vitest run && npx tsc -b && npx playwright test
docker compose config
```

## Fixes applied during this pass that restored green gates

1. Open-mode test expectations (no 401 without bearer) in API control/notifications/alerts tests.
2. Engine `expire_stale_decisions` only expires **ended** other-run decisions (stops live-engine races with parallel test runs).
3. Ruff F401 unused import in autopilot tests.
4. Frontend route aliases `/what-if`, `/system-health`.
