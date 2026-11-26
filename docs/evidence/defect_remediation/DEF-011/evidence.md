# DEF-011 Remediation Evidence

## Summary
- **Defect:** `DEF-011` (`P1`) — AI Assistant silently dropped active alerts & decisions (`tuple` unpacking `TypeError` caught and swallowed) and reported `"System overall status is Healthy"` while `engine_mode == "DEGRADED"`.
- **Root Causes:**
  1. `services/api/app/routers/assistant.py` iterated over `await fetch_alerts(...)` and `await fetch_decisions(...)` directly without unpacking their `(items, total)` tuple return values, failing on `str/tuple.get` inside `except Exception`.
  2. `assistant.py` did not pass `sim_run_id` to `fetch_alerts` / `fetch_decisions`, and mapped `a.get("station_id")` instead of `a.get("entity_id")`.
  3. `health["overall"]` checked only `is_degraded or state.get("degraded")` and ignored `engine_mode != "NORMAL"`.
- **Changes Made:**
  1. [`services/api/app/routers/assistant.py`](file:///f:/NewTask/fuel-platform/services/api/app/routers/assistant.py): Unpacked `(items, total)` tuples cleanly, scoped alert & decision lookups to active `sim_run_id`, mapped `station_id`/`entity_id`/`category`, and set `overall = "Degraded"` whenever `engine_mode != "NORMAL"`.
  2. [`services/api/tests/test_assistant_api.py`](file:///f:/NewTask/fuel-platform/services/api/tests/test_assistant_api.py): Added `test_assistant_unpacks_tuples_scopes_active_run_and_reflects_degraded_engine_mode`.

## Verification
- `pytest tests/test_assistant_api.py -v`: **7 passed**.
- Live `POST /api/assistant/ask` after Docker `api` rebuild:
  - Alerts query returned `[WARNING] #320 ...` with `sources` containing `"type": "alert"`.
  - Decisions query returned `Latest recommendation #1767 ...` with `sources` containing `"type": "decision"`.
  - Health query under `stale_data` chaos (`DEGRADED`) returned `"System overall status is Degraded. Engine is operating in DEGRADED mode..."`.
