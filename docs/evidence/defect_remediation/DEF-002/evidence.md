# DEF-002 — Decision Approval `selected_line_ids`, `modified_quantities` & HTTP 500 Constraint Fix

## Status: FIXED — LIVE VERIFICATION PASSED

## Root Cause
1. `ApproveRequest` in `services/api/app/routers/decisions.py` only defined `line_ids` and `quantities`, dropping external `selected_line_ids` and `modified_quantities` fields silently. Even when `line_ids` was passed, the cognitive forcing check computed `max_q` over deselected lines, and validation for empty selection, unknown line IDs, or non-positive quantities was missing.
2. In `services/engine/app/decision/engine.py` & `services/engine/app/main.py`, `DecisionConstraintViolationError` lacked a `.violations` attribute while `main.py:440-444` iterated `exc.violations`, raising `AttributeError` and turning constraint violations (e.g., over-max quantities) into HTTP `500 INTERNAL_ERROR` (`#1631` crash).
3. `DecisionEngine.approve_decision` did not reject empty `selected_line_ids`, unknown line IDs, or non-finite / `<= 0` modified quantities with `422` errors prior to DB mutation.

## Changes Implemented
- `services/api/app/routers/decisions.py`:
  - Extended `ApproveRequest` to accept canonical `line_ids`/`quantities` and aliases `selected_line_ids`/`modified_quantities`.
  - Added strict `422` validation for `NO_LINES_SELECTED`, `INVALID_LINE_SELECTION`, and `INVALID_QUANTITY` (non-int ID, non-finite or `<= 0` qty).
  - Scoped F-TRUST cognitive forcing (`> 5,000 L`) strictly to the selected & modified lines.
  - Corrected engine response status read (`decision_status` fallback) for `DECISION_FIRST_APPROVAL` vs `DECISION_APPROVED` audit action.
- `services/engine/app/decision/engine.py` & `services/engine/app/main.py`:
  - Added `violations` list parameter/attribute to `DecisionConstraintViolationError` and passed `violations` when raised; added defensive `getattr(exc, "violations", None) or []` in `/engine/decisions/{id}/approve`.
  - Validated `selected_line_ids` non-empty, line ID ownership, and finite positive quantities cleanly before ORM line mutations.
- `frontend/src/pages/Recommendations.tsx`:
  - Scoped `hasLargeLine` cognitive forcing check to selected lines and guarded Approve when zero lines are selected.

## Regression Tests Passing
- API pytest (`20 passed`): `services/api/tests/test_decisions_api.py` (including `test_approve_decision_selected_lines_and_modified_quantities_aliases`) & `test_assistant_api.py`.
- Engine pytest (`17 passed`): `services/engine/tests/test_engine_decision.py` & `test_engine_endpoints.py` (including `test_approve_decision_partial_lines_and_constraint_422`).
- Frontend Vitest (`11 passed` across `decisions.test.tsx`, `explain.test.tsx`, `client.test.ts`) + `tsc -b --noEmit`.

## Live Verification (Rebuilt `api`, `engine`, `frontend`)
- Prepared live multi-line Decision `#1730` (6 lines, `22,990 L`):
  - `selected_line_ids: []` -> `422 NO_LINES_SELECTED`
  - `selected_line_ids: [9999999]` -> `422 INVALID_LINE_SELECTION`
  - `modified_quantities: {"<line1>": 0}` -> `422 INVALID_QUANTITY`
  - `modified_quantities: {"<line1>": 50000}` -> **`422 CONSTRAINT_VIOLATION`** (`[R4_MAX_SHIPMENT] ... [R7B_OVERFLOW_AT_ARRIVAL]`), decision preserved `PENDING_APPROVAL`.
  - `POST /api/decisions/1730/approve` with `selected_line_ids` + `modified_quantities: 1200` -> `200 EXECUTED`, `approved_by="operator"`, simulator created matching allocation `#1` (`quantity=1200`), remaining 5 lines `CANCELLED`.
- Chrome Playwright UI on Decision `#1731` (6 lines): unchecked 5 lines, edited line 1 (`#2979`) to `1500` L, clicked Approve -> `#2979` `SUBMITTED (1500 L)`, lines `2980..2984` `CANCELLED`.
- Screenshots: `def002_ui_partial_selection.png`, `def002_decisions_ledger.png`.
