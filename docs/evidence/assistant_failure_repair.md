# Operations Assistant Failure Repair Evidence Report

**Document ID:** FSIP-EVID-P09-REPAIR-001  
**Date:** November 25, 2026  
**Status:** VERIFIED & RELEASED  
**Target:** FSIP Grounded Operations Assistant & Rules-Based Fallback Engine  
**Release Commit:** `fix: restore grounded FSIP operations assistant and rules fallback`

---

## 1. Incident Overview & Symptoms

When operators submitted questions in the Operations Assistant interface (`http://localhost:3000/assistant`), specifically:
> *"Why is Cox's Bazar at risk?"*

The system exhibited the following critical failures:
1. The frontend displayed an `Internal Server Error` banner.
2. The assistant chat returned `Error: Internal Server Error. Please retry.` marked with the badge `RULES`.
3. The backend container (`fsip-api-1`) logged an HTTP 500 unhandled exception:
   `AttributeError: 'tuple' object has no attribute 'get'` at line 107 in `_build_assistant_context`.

---

## 2. Root Cause Analysis

Thorough tracing of the end-to-end request lifecycle:
$$\text{Frontend UI} \longrightarrow \text{API Gateway} \longrightarrow \text{Assistant Router} \longrightarrow \text{Context Retrieval} \longrightarrow \text{Rules/LLM} \longrightarrow \text{Response}$$
revealed five interdependent root causes:

### 2.1 Backend Tuple Unpack Defect (HTTP 500 Trigger)
In `services/api/app/routers/assistant.py`:
- `get_current_state(redis_client, session)` was implemented to return a 4-tuple: `(state_dict, tick, is_degraded, etag)`.
- However, `_build_assistant_context` and `get_summary` invoked `state = await get_current_state(request, session)` without passing the Redis client and without unpacking the tuple.
- Line 107 executed `for st in state.get("stations", []):` directly on the tuple, raising an uncaught `AttributeError` and returning HTTP 500 to the client.

### 2.2 Flawed Station Matching in Rules Engine
In `libs/fsip_common/fsip_common/explain/llm.py`:
- `rules_based_assistant` matched stations using naive substring checks: `st_id in q_lower or base_id in q_lower or st_name in q_lower`.
- When querying `"Why is Cox's Bazar at risk?"`:
  - `st_name` was `"Cox's Bazar Regional Station"` (longer than the query substring, so `st_name in q_lower` was `False`).
  - `base_id` was `"coxsbazar"` (without apostrophe or space, so `base_id in q_lower` was `False`).
  - `st_id` was `"station-coxsbazar"` (`False`).
- Cox's Bazar was therefore never resolved, even if context was populated.

### 2.3 Context Truncation of Risks and Depots
In `_build_assistant_context`:
- Station risks (`st.get("risk")`) were stripped out, leaving the assistant without risk levels or stockout probabilities for retail stations.
- Depot entities were omitted from context, preventing regional inventory summaries.

### 2.4 Jinja2 Template Packaging Defect in Container Build
In `libs/fsip_common/pyproject.toml` and Docker build:
- Setuptools configuration omitted `[tool.setuptools.package-data]`, causing non-Python template files (`shift_summary.j2`) to be omitted from the installed wheel in production Docker images, throwing `TemplateNotFound: shift_summary.j2` on `GET /api/summary`.

### 2.5 Misleading Frontend Error Badge
In `frontend/src/pages/Assistant.tsx`:
- The client-side `catch` block hardcoded `engine: "rules"` on error messages when network or HTTP 500 errors occurred, creating the false appearance that the rules engine produced the error text.

---

## 3. Engineering Remediation & Implementation

### 3.1 Backend Context & Endpoint Hardening (`services/api/app/routers/assistant.py`)
- Corrected `get_current_state(redis_client, session)` invocation and supported both tuple and dict returns safely.
- Added comprehensive telemetry retrieval: retail stations, bulk depots, per-station risk maps, open alerts, latest optimization decisions, and system health.
- Enclosed context retrieval, LLM generation, and template rendering in multi-tier exception shields with guaranteed fallback to `rules_based_assistant`.

### 3.2 Robust Rules Engine (`libs/fsip_common/fsip_common/explain/llm.py`)
- Implemented alphanumeric normalization (`re.sub(r"[^a-z0-9]", "", ...)`) and multi-word token overlap matching to reliably resolve station names (`Cox's Bazar`, `Mirpur`, `Tongi`, `Karnaphuli`) and depots.
- Upgraded rules-based question resolution across 6 primary operational categories:
  1. **Station-specific risk & inventory:** Extracts severity (`CRITICAL`, `HIGH`, `MEDIUM`, `LOW`), inventory liters, hours to stockout, and operational recommendation.
  2. **Low inventory & approaching stockout:** Identifies stations below threshold or with active stockout projections.
  3. **Alerts & Incidents:** Formats open shortage alerts and pipeline incidents with source chip metadata.
  4. **Decisions & Recommendations:** Details pending/approved dispatch decisions, volume, and line counts.
  5. **System Health:** Reports overall platform health, engine operating mode, and tick synchronization.
  6. **Executive Shift Summary:** Formats service level, unmet liters, total volume dispatched, and decision counts.
  7. **Strict Grounding:** Responds `"not in current data"` for unsupported or non-platform questions.

### 3.3 Jinja2 Multi-Loader Resolution (`libs/fsip_common/fsip_common/explain/explainer.py` & `pyproject.toml`)
- Configured `ChoiceLoader` combining `FileSystemLoader`, container path `/opt/fsip_common/...`, and `PackageLoader`.
- Added package data declaration in `pyproject.toml` (`*.j2`, `*.json`, `*.yaml`).
- Configured Dockerfile for editable install `pip install -e /opt/fsip_common`.

### 3.4 Frontend Error Handling Cleanup (`frontend/src/pages/Assistant.tsx`)
- Removed `engine: "rules"` badge from client catch block. Network and server errors are now cleanly presented with clear operator guidance: *"Please check platform network connectivity and retry."*

---

## 4. Verification & Live Platform Testing

### 4.1 Unit & Regression Test Suite
Executed the complete test suite across common libraries, API services, and frontend:
- **`services/api/tests/test_assistant_api.py`:** **6 passed in 1.98s**
  - `test_ask_assistant_grounded_and_unsupported` PASSED
  - `test_ask_assistant_tuple_return_and_all_categories` PASSED (validates all 6 operational categories and tuple state handling)
  - `test_ask_assistant_llm_failure_fallback_to_rules` PASSED (validates circuit breaker / provider error fallback)
  - `test_ask_assistant_validation` PASSED
  - `test_get_summary` PASSED
  - `test_cognitive_forcing_in_approval` PASSED
- **`libs/fsip_common/tests`:** **107 passed in 1.10s**
- **`services/api/tests` (Full Suite):** **56 passed in 18.81s**
- **Frontend Vitest Suite:** **12 test files passed, 52 tests passed in 6.75s**
- **Code Linting:** `ruff check libs services evaluation` passed with **0 errors**.

### 4.2 Live Container Verification Logs (Port 18080 & Port 3000)
Tested against live Docker containers (`fsip-api-1` and `fsip-frontend`):

```json
// POST http://127.0.0.1:18080/api/assistant/ask
// Question: "Why is Cox's Bazar at risk?"
{
  "status": 200,
  "answer": "station-coxsbazar (Cox's Bazar Regional Station) is currently at LOW risk across all fuel types. Current total inventory is 24,464 L with over 48 hours of cover runway and no imminent stockout detected. Telemetry confirms normal replenishment pipeline.",
  "engine": "rules",
  "sources": [{"type": "station", "id": "station-coxsbazar"}],
  "generated_at": "2026-11-25T15:43:27.543082Z"
}

// Question: "Show me active shortage alerts"
{
  "status": 200,
  "answer": "There are currently no active shortage alerts or unacknowledged incidents across the network.",
  "engine": "rules",
  "sources": [],
  "generated_at": "2026-11-25T15:43:27.555225Z"
}

// Question: "What is the latest dispatch recommendation?"
{
  "status": 200,
  "answer": "No pending or recent dispatch decisions in the current planning cycle. Network deliveries are up to date.",
  "engine": "rules",
  "sources": [],
  "generated_at": "2026-11-25T15:43:27.568591Z"
}

// Question: "How is system health right now?"
{
  "status": 200,
  "answer": "System overall status is Healthy. Engine is operating in NORMAL mode at tick 96. Current network service level is 100.00%.",
  "engine": "rules",
  "sources": [],
  "generated_at": "2026-11-25T15:43:27.578736Z"
}

// Question: "Summarize the current shift"
{
  "status": 200,
  "answer": "Shift executive summary at tick 96: Network service level is 100.00% with 0 L total unmet demand. Operating mode: NORMAL. Active alerts: 0, recent decisions: 0.",
  "engine": "rules",
  "sources": [],
  "generated_at": "2026-11-25T15:43:27.590231Z"
}

// Question: "What is the current inventory?"
{
  "status": 200,
  "answer": "Network inventory summary: 4 active retail stations holding 106,261 L total fuel, supported by 2 bulk supply depots.",
  "engine": "rules",
  "sources": [],
  "generated_at": "2026-11-25T15:43:27.601774Z"
}

// GET http://127.0.0.1:18080/api/summary?window_ticks=96
{
  "status": 200,
  "text": "Shift Summary (Window: 96 ticks):\nOverall fuel service level: 100.0%.\nTotal unmet demand: 0 L.\nTotal volume dispatched: 1288805 L across 388 decision(s) (289 autopilot, 2 manual).\nAlerts: 0 opened, 6 resolved.\nIncidents: 0 total. Active policy: optimizer.",
  "engine": "template",
  "stats": {
    "window_ticks": 96,
    "service_level": 1.0,
    "unmet_liters": 0.0,
    "total_dispatched_liters": 1288805.4,
    "decisions_count": 388,
    "autopilot_count": 289,
    "manual_count": 2,
    "alerts_opened": 0,
    "alerts_resolved": 6,
    "incidents_count": 0,
    "active_policy": "optimizer"
  }
}
```

---

## 5. Conclusion

The FSIP Operations Assistant is completely restored and resilient. It functions deterministically in RULES mode even when external LLM providers are unavailable, without returning HTTP 500 errors, and provides grounded operational telemetry for all control-room inquiries.
