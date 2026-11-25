"""Running Mode vs Stepping Mode Benchmark (Task 12.4).

Executes Heuristic and Optimizer policies in both Stepping Mode and Running Mode (SIMULATION_SPEED=8.0)
over 192 ticks (2 simulated days / 24 seconds wall-clock time) on an isolated simulator.
Verifies tick coalescing under asynchronous real-time execution and asserts KPI parity within 1%.

Usage:
    python evaluation/running_vs_stepping.py --sim-url http://localhost:8005
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "services" / "engine"))
sys.path.insert(0, str(BASE_DIR / "libs"))

from app.decision.heuristic import plan_heuristic  # noqa: E402
from app.decision.optimizer import OptimizerPolicy  # noqa: E402

from evaluation.harness import EvaluationRunner, IsolatedSimClient  # noqa: E402
from evaluation.scenario_loader import load_scenario  # noqa: E402


class RunningModeRunner:
    def __init__(self, sim_url: str = "http://localhost:8005") -> None:
        self.sim = IsolatedSimClient(sim_url)

    def run_stepping(self, policy_name: str, ticks: int = 192, seed: int = 12345) -> dict[str, Any]:
        """Runs the benchmark in stepping mode."""
        print(f"\n[mode-test] Running {policy_name} in STEPPING mode (ticks={ticks}, seed={seed})...")
        harness_runner = EvaluationRunner(sim_url=self.sim.base_url)
        scenario = load_scenario("baseline")
        return harness_runner.run_single(scenario, policy_name, ticks_override=ticks, seed_override=seed)

    def run_live_running(self, policy_name: str, ticks: int = 192, seed: int = 12345) -> dict[str, Any]:
        """Runs the benchmark in running mode at SIMULATION_SPEED=8 with real-time tick coalescing."""
        print(f"\n[mode-test] Running {policy_name} in RUNNING mode at speed=8 (ticks={ticks}, seed={seed})...")

        # 1. Reset simulator with isolated state
        self.sim.reset(seed)

        optimizer = OptimizerPolicy(time_limit=5)
        allocated_lines_count = 0
        submission_failures = 0
        total_liters_shipped = 0.0
        n_idemp = 0

        last_processed_tick = -1
        coalesced_ticks = 0
        cycles_executed = 0

        # Start live running mode
        self.sim.request("/admin/run", "POST")
        start_wall_time = time.perf_counter()

        try:
            while True:
                inst = self.sim.request("/v1/instance") or {}
                cur_tick = int(inst.get("tick", 0))

                if cur_tick >= ticks:
                    break

                if cur_tick > last_processed_tick:
                    # Detect tick gaps (coalescing)
                    if last_processed_tick >= 0 and cur_tick > last_processed_tick + 1:
                        skipped = cur_tick - last_processed_tick - 1
                        coalesced_ticks += skipped

                    cycles_executed += 1
                    store = self.sim.build_world_state(current_tick=cur_tick)

                    # Solve policy
                    if policy_name == "heuristic":
                        plan = plan_heuristic(state=store)
                        lines = plan.lines
                    elif policy_name == "optimizer":
                        plan = optimizer.solve(store)
                        lines = plan.lines
                    else:
                        lines = []

                    # Sort lines deterministically
                    lines = sorted(
                        lines,
                        key=lambda line: (
                            line.source_depot_id,
                            line.destination_station_id,
                            line.fuel_type,
                            line.quantity,
                        ),
                    )

                    for pl in lines:
                        n_idemp += 1
                        res = self.sim.request(
                            "/v1/allocations",
                            "POST",
                            {
                                "idempotency_key": f"live-{policy_name}-{cur_tick}-{n_idemp}",
                                "source_depot_id": pl.source_depot_id,
                                "destination_station_id": pl.destination_station_id,
                                "route_id": pl.route_id,
                                "fuel_type": pl.fuel_type,
                                "quantity": pl.quantity,
                            },
                        )
                        if isinstance(res, dict) and "err" in res:
                            submission_failures += 1
                        else:
                            allocated_lines_count += 1
                            total_liters_shipped += float(pl.quantity)

                    last_processed_tick = cur_tick

                # Poll interval: at speed 8, 1 tick = 125ms
                time.sleep(0.04)
        finally:
            # Always pause simulator when done
            self.sim.request("/admin/pause", "POST")

        wall_duration = time.perf_counter() - start_wall_time

        # Final outcomes
        metrics = self.sim.request("/v1/metrics") or {}
        all_allocs = self.sim.request("/v1/allocations") or []
        route_failures = sum(1 for a in all_allocs if a.get("status") == "FAILED")
        overall_sl = round(float(metrics.get("service_level", 1.0)), 4)
        unmet_liters = round(float(metrics.get("unmet_demand_liters", 0.0)), 1)

        result: dict[str, Any] = {
            "policy": policy_name,
            "mode": "running",
            "ticks": ticks,
            "seed": seed,
            "service_level": overall_sl,
            "unmet_liters": unmet_liters,
            "failures": submission_failures + route_failures,
            "allocated_lines": allocated_lines_count,
            "liters_shipped": round(total_liters_shipped, 1),
            "cycles_executed": cycles_executed,
            "coalesced_ticks": coalesced_ticks,
            "runtime_sec": round(wall_duration, 2),
        }

        print(
            f"[mode-test] Finished RUNNING {policy_name}: SL={overall_sl:.4f}, Unmet={unmet_liters}L, "
            f"Cycles={cycles_executed}, Coalesced Ticks={coalesced_ticks}, Wall Time={result['runtime_sec']}s"
        )
        return result


def run_comparison(sim_url: str = "http://localhost:8005", ticks: int = 192) -> list[dict[str, Any]]:
    runner = RunningModeRunner(sim_url)
    policies = ["heuristic", "optimizer"]
    comparisons = []

    for pol in policies:
        step_res = runner.run_stepping(pol, ticks=ticks)
        run_res = runner.run_live_running(pol, ticks=ticks)

        sl_step = step_res["service_level"]
        sl_run = run_res["service_level"]
        sl_diff = abs(sl_run - sl_step)
        passed = sl_diff <= 0.01  # within 1 percentage point

        comp: dict[str, Any] = {
            "policy": pol,
            "ticks": ticks,
            "stepping_service_level": sl_step,
            "running_service_level": sl_run,
            "service_level_delta": round(sl_diff, 4),
            "stepping_unmet_liters": step_res["unmet_liters"],
            "running_unmet_liters": run_res["unmet_liters"],
            "stepping_lines": step_res["allocated_lines"],
            "running_lines": run_res["allocated_lines"],
            "coalesced_ticks": run_res["coalesced_ticks"],
            "cycles_executed": run_res["cycles_executed"],
            "runtime_sec": run_res["runtime_sec"],
            "passed": passed,
        }
        comparisons.append(comp)

        status_str = "PASSED (delta <= 1%)" if passed else "FAILED (delta > 1%)"
        print(
            f"[mode-test] {pol.upper()} comparison: Stepping SL={sl_step:.4f} vs Running SL={sl_run:.4f} "
            f"(Delta={sl_diff:.4%}) -> {status_str}"
        )

    out_file = BASE_DIR / "evaluation" / "reports" / "running_vs_stepping.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(comparisons, f, indent=2)

    return comparisons


def main() -> None:
    parser = argparse.ArgumentParser(description="FSIP Running Mode vs Stepping Mode Benchmark")
    parser.add_argument("--sim-url", default="http://localhost:8005", help="Isolated simulator URL")
    parser.add_argument("--ticks", type=int, default=192, help="Simulation duration in ticks (default: 192 = 2 days)")
    args = parser.parse_args()

    comps = run_comparison(sim_url=args.sim_url, ticks=args.ticks)
    all_passed = all(c["passed"] for c in comps)
    if not all_passed:
        print("[mode-test] FAILED: Some policies exhibited SL delta > 1%")
        sys.exit(1)

    print("\n[mode-test] SUCCESS: All policies verified within 1% KPI tolerance under live running mode.")


if __name__ == "__main__":
    main()
