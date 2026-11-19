# Agent rules for fuel-platform
You are building the Fuel Supply Intelligence & Resilience Platform. The full spec is in ../fuel-platform-plan/.
Deadline: Thu 2026-11-26. A working demo beats an unfinished feature.

Source-of-truth order: 20_FINAL_SPEC_LOCK.md > 24_PHASE00_FILE_CONTENTS.md > 21_API_CONTRACTS.md > 23_MODULE_SPECS.md >
22_UI_SPEC.md > 13_ARCHITECTURE_V2.md > phases/*.md > the other docs. Never contradict 20.

Hard rules:
1. Work on exactly ONE phase file at a time, tasks in order. Do not start the next phase until every acceptance item passes.
2. Use the exact names, versions, ports, env vars, enums and response shapes from 20/21/23. Do not upgrade pinned versions.
3. Start from the tested reference code in ../fuel-platform-plan/reference/ whenever a task says so. Do not rewrite it from scratch.
4. Never modify the simulator. Only the engine writes to it (POST /v1/allocations, cancel). /admin/* only in tests, harness,
   chaos scripts and demo-control endpoints.
5. Every network call has a timeout. Never write to the simulator on stale/invalid data. Never send a line that fails
   decision/constraints.py.
6. After each task: run its tests + `make test` + `make lint` (+ `npm test` if the frontend changed) and `make smoke` if
   services changed. Paste short real outputs into PROGRESS.md. Commit as `phase-NN: <task>`.
7. If something in the spec is impossible: pick the simplest option that keeps the acceptance criteria true, log it in
   10_DECISIONS_AND_ASSUMPTIONS.md → Deviations, and continue. Do not stop and do not silently change contracts.
8. Do not invent simulator behaviour: 02_SIMULATOR_FACTS.md is verified. If you need a new fact, test it against the running
   simulator and record it.
9. No secrets in git. .env is ignored; update .env.example when adding a variable.
10. Read 19_GLOSSARY_PITFALLS_FAQ.md before coding; re-read it when a test fails for an unclear reason.
