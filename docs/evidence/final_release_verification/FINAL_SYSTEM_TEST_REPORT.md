# FSIP — Final System Test Report

**Project:** Fuel Supply Intelligence & Resilience Platform  
**Date:** 2026-11-26  
**Branch:** `docs/fsip-plan-quality-evaluation`  
**Baseline HEAD (pre-commit):** `3d7171bbedac7dff3865b125e02aa4f62adfe17d`

## Executive summary

Final engineering pass re-verified prior DEF-001…022 remediation against the **current** tree and live Docker stack, fixed open-mode test drift + a live-engine decision-expiry race, restored full automated gates, and confirmed browser workflows without Sign-In.

## Environment

| Layer | Result |
|---|---|
| Docker Compose services | healthy (api, engine, frontend, simulator, redis, timescaledb, intelligence, obs stack) |
| Redis | `fsip:state` present; `fsip:domain-events` streaming |
| DB | Timescale reachable; decisions persisted |
| Frontend | http://localhost:3000 — Operator chip, no Sign-In |

## Automated results (final)

See `FINAL_TEST_EXECUTION_LOG.md` — **ruff clean; API 80; Engine 333; Vitest 64; Playwright 4; tsc clean.**

## Live workflows

| Workflow | Result |
|---|---|
| A Auth / four-eyes login | **N/A — intentionally open mode** (Approve/Reject is the gate) |
| B Allocation approval | **PASS** (API + browser + Playwright) |
| C Autopilot safety | **PASS** (409 when stale) |
| D SSE reset | Code+prior live; not re-fired this session |
| E Risk / alerts | **PASS** |
| F What-If | **PASS** |
| G Assistant | **PASS** |
| H Recovery / chaos clear | Exercised during DEF-003 probe |

Browser details: `browser/BROWSER_WORKFLOWS.md`.

## Critical fix this pass

`DecisionEngine.expire_stale_decisions` previously expired **all** pending decisions from any non-active `sim_run_id`, which raced the live engine against parallel integration tests (and would incorrectly expire still-open parallel runs). Now only expires other-run decisions when `SimRun.ended_at` is set.

## Residual notes

- Open demo operator is intentional for hackathon demonstration.
- Performance budgets passed in the final engine suite run after isolation fix (earlier host-load flakes not reproduced).
