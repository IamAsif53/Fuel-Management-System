# DEF-007 — What-If Invalid Payload `422` Validation Evidence

## Status: FIXED — LIVE VERIFICATION PASSED

## Root Cause
`POST /api/whatif` & `POST /api/decisions/what-if` (`services/api/app/routers/decisions.py:559`) and `POST /internal/whatif` (`services/engine/app/main.py:754`) accepted raw `dict[str, Any]` without Pydantic bounds/schema checks (`horizon_ticks = min(96, int(req.get("horizon_ticks", 48)))`), allowing negative `horizon_ticks` (`-5`) and arbitrary keys (`scenario_type: "nonexistent_type"`) to crash inside `ShadowTwin` with HTTP `500`.

## Changes Implemented
- `services/api/app/routers/decisions.py`: Added strict Pydantic models (`WhatIfRequest`, `WhatIfEventRequest`, `WhatIfLineRequest` with `ConfigDict(extra="forbid")`, `horizon_ticks` `1..96`, `Literal` event & compare types, `start_offset >= 0`, `duration_ticks >= 1`) and typed `post_whatif`.
- `services/engine/app/main.py`: Added matching strict `InternalWhatIfRequest` validation on `/internal/whatif` while preserving domain `422` error codes (`INVALID_IDENTIFIER`, `INVALID_FUEL_TYPE`, `INVALID_QUANTITY`, `INFEASIBLE_DISPATCH`).
- `services/api/tests/test_decisions_api.py` & `services/engine/tests/test_whatif_repair.py`: Added `test_whatif_invalid_payload_returns_422_not_500` and `test_j_invalid_schema_bounds_return_422`.

## Verification
- API pytest (`test_decisions_api.py`): **15 passed, 0 failed**.
- Engine pytest (`test_whatif_repair.py`): **10 passed, 0 failed**.
- Live `POST http://localhost:3000/api/whatif`:
  - `{"scenario_type": "nonexistent_type", "horizon_ticks": -5}` -> **HTTP `422 VALIDATION_ERROR`**.
  - `{"horizon_ticks": 24, "lines": [], "events": []}` -> **HTTP `200`** (`['do_nothing', 'recommended', 'custom']`).
