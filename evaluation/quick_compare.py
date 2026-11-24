"""Policy Comparison Benchmark: Heuristic vs Rolling-Horizon LP Optimizer (Task 8.9).

Per PHASE_08_OPTIMIZER_AND_WHATIF.md & 14_CODE_BLUEPRINTS.md §5:
Compares:
- Baseline (nominal operations, 288 & 600 ticks)
- Crisis (combined demand surge, route cuts, outages, delay, 288 & 600 ticks)
- Single route cut (Tongi / Cox's Bazar route isolation)

Evaluates Key Performance Indicators (KPIs):
- Overall Network Service Level (target >= 99% on baseline, >= greedy on crisis/600)
- Unmet demand liters
- Total allocation failures (target: 0)
- Overflow clipping loss (target: 0 L with p10 guard)
- Per-station minimum service level (fairness)
- Solver p95 latency (target < 500 ms)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

# Ensure services/engine is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "services" / "engine"))

import numpy as np
from app.decision.heuristic import plan_heuristic
from app.decision.optimizer import OptimizerPolicy
from app.state.store import WorldState
from fsip_common.models import Allocation, Depot, Instance, Route, Station

SCENARIOS: dict[str, list[dict[str, Any]]] = {
    "baseline": [],
    "crisis": [
        {"type": "demand_spike", "start_tick": 20, "duration_ticks": 96, "parameters": {"multiplier": 1.6}},
        {"type": "supply_shortfall", "start_tick": 30, "duration_ticks": 1, "parameters": {"factor": 0.5}},
        {
            "type": "route_disruption",
            "start_tick": 40,
            "duration_ticks": 48,
            "parameters": {"route_ids": ["route-patiya-karnaphuli", "route-gazipur-mirpur"]},
        },
        {"type": "shipment_delay", "start_tick": 50, "duration_ticks": 1, "parameters": {"delay_ticks": 24}},
        {
            "type": "station_outage",
            "start_tick": 100,
            "duration_ticks": 16,
            "parameters": {"station_ids": ["station-tongi"]},
        },
    ],
    "single_route_cut": [
        {
            "type": "route_disruption",
            "start_tick": 30,
            "duration_ticks": 40,
            "parameters": {"route_ids": ["route-gazipur-tongi", "route-patiya-coxsbazar"]},
        },
        {"type": "demand_spike", "start_tick": 30, "duration_ticks": 40, "parameters": {"multiplier": 1.4}},
    ],
}


class SimRunner:
    def __init__(self, base_url: str = "http://localhost:8000") -> None:
        self.base_url = base_url.rstrip("/")

    def req(self, path: str, method: str = "GET", body: Any = None) -> Any:
        url = f"{self.base_url}{path}"
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"}
        req = urllib.request.Request(url, method=method, data=data, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as err:
            return {"err": err.code, "body": err.read().decode()[:300]}
        except Exception as exc:
            return {"err": 500, "body": str(exc)}

    def build_store_from_sim(self) -> WorldState:
        inst_data = self.req("/v1/instance")
        stations_data = self.req("/v1/stations") or []
        depots_data = self.req("/v1/depots") or []
        routes_data = self.req("/v1/routes") or []
        allocations_data = self.req("/v1/allocations") or []
        supplies_data = self.req("/v1/supply-arrivals") or []
        events_data = self.req("/v1/events") or []

        store = WorldState(sim_run_id=inst_data.get("id", 1))
        store.instance = Instance(
            id=inst_data.get("id", 1),
            scenario_id=inst_data.get("scenario_id", "sim"),
            scenario_version=inst_data.get("scenario_version", "1.0"),
            seed=inst_data.get("seed", 42),
            tick=inst_data.get("tick", 0),
            tick_minutes=inst_data.get("tick_minutes", 15),
            sim_time=inst_data.get("sim_time", "2026-01-01T00:00:00Z"),
            status=inst_data.get("status", "RUNNING"),
        )
        store.stations = [
            Station(
                id=s["id"],
                name=s["name"],
                region_id=s["region_id"],
                status=s["status"],
                capacity=s["capacity"],
                inventory=s["inventory"],
                demand_profile=s.get("demand_profile", "urban"),
                demand_multiplier=float(s.get("demand_multiplier", 1.0)),
            )
            for s in stations_data
        ]
        store.depots = [
            Depot(
                id=d["id"],
                name=d["name"],
                region_id=d["region_id"],
                status=d["status"],
                dispatch_capacity_per_tick=float(d.get("dispatch_capacity_per_tick", 50000.0)),
                capacity=d["capacity"],
                inventory=d["inventory"],
            )
            for d in depots_data
        ]
        store.routes = [
            Route(
                id=r["id"],
                source_depot_id=r["source_depot_id"],
                destination_station_id=r["destination_station_id"],
                transit_ticks=int(r["transit_ticks"]),
                max_shipment=float(r["max_shipment"]),
                status=r["status"],
            )
            for r in routes_data
        ]
        store.allocations = [
            Allocation(
                id=a["id"],
                idempotency_key=a.get("idempotency_key", f"sim-alloc-{a['id']}"),
                decision_id=a.get("decision_id"),
                route_id=a.get("route_id", "route-1"),
                source_depot_id=a["source_depot_id"],
                destination_station_id=a["destination_station_id"],
                fuel_type=a["fuel_type"],
                quantity=float(a["quantity"]),
                status=a["status"],
                created_tick=int(a.get("created_tick", 0)),
                transit_ticks=int(a.get("transit_ticks", 4)),
            )
            for a in allocations_data
        ]
        store.supply_arrivals = supplies_data
        store.events = events_data
        return store

    def run_benchmark(self, scenario: str, policy_name: str, ticks: int) -> dict[str, Any]:
        # Reset simulator
        self.req("/admin/reset", "POST")
        self.req("/admin/faults/clear", "POST")

        # Inject scenario events
        events = SCENARIOS.get(scenario, [])
        for ev in events:
            self.req("/admin/events", "POST", ev)

        optimizer = OptimizerPolicy(time_limit=5)

        solve_latencies_ms: list[float] = []
        submission_failures = 0
        allocated_lines_count = 0
        n_idemp = 0

        for tick_idx in range(ticks):
            store = self.build_store_from_sim()

            t0 = time.perf_counter()
            if policy_name == "heuristic":
                h_plan = plan_heuristic(state=store)
                lines = h_plan.lines
            elif policy_name == "optimizer":
                opt_plan = optimizer.solve(store)
                lines = opt_plan.lines
            else:
                lines = []
            dur_ms = (time.perf_counter() - t0) * 1000.0
            solve_latencies_ms.append(dur_ms)

            # Submit lines
            for pl in lines:
                n_idemp += 1
                res = self.req(
                    "/v1/allocations",
                    "POST",
                    {
                        "idempotency_key": f"bench-{policy_name}-{tick_idx}-{n_idemp}",
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

            # Step simulator
            self.req("/admin/step", "POST")

        # Gather final outcome metrics
        metrics = self.req("/v1/metrics")
        all_allocs = self.req("/v1/allocations") or []
        failed_shipments = sum(1 for a in all_allocs if a.get("status") == "FAILED")

        # Compute per-station service level
        station_sls: dict[str, float] = {}
        stations_data = self.req("/v1/stations") or []
        for st in stations_data:
            st_id = st["id"]
            rows = self.req(f"/v1/demand-history?station_id={st_id}&limit=2000") or []
            tot_dem = sum(float(r.get("demand_liters", 0.0)) for r in rows)
            tot_unmet = sum(float(r.get("unmet_liters", 0.0)) for r in rows)
            sl = 1.0 - (tot_unmet / max(1.0, tot_dem)) if tot_dem > 0 else 1.0
            station_sls[st_id] = round(sl, 4)

        min_station_sl = min(station_sls.values()) if station_sls else 1.0
        p95_solve_ms = float(np.percentile(solve_latencies_ms, 95)) if solve_latencies_ms else 0.0

        return {
            "scenario": scenario,
            "policy": policy_name,
            "ticks": ticks,
            "service_level": round(float(metrics.get("service_level", 1.0)), 4),
            "unmet_liters": round(float(metrics.get("unmet_liters", 0.0)), 1),
            "overflow_liters": round(float(metrics.get("overflow_liters", 0.0)), 1),
            "submission_failures": submission_failures,
            "failed_shipments": failed_shipments,
            "min_station_sl": min_station_sl,
            "p95_solve_ms": round(p95_solve_ms, 2),
            "allocated_lines": allocated_lines_count,
            "station_sls": station_sls,
        }


def print_comparison_table(results: list[dict[str, Any]]) -> None:
    print("\n" + "=" * 90)
    print("POLICY BENCHMARK COMPARISON TABLE (Quick Compare)")
    print("=" * 90)
    headers = [
        "Scenario",
        "Policy",
        "Ticks",
        "Overall SL",
        "Unmet (L)",
        "Overflow",
        "Failures",
        "Min Station SL",
        "p95 (ms)",
    ]
    row_fmt = "{:<16} {:<12} {:<8} {:<12} {:<12} {:<10} {:<10} {:<16} {:<10}"
    print(row_fmt.format(*headers))
    print("-" * 90)
    for r in results:
        print(
            row_fmt.format(
                r["scenario"],
                r["policy"],
                r["ticks"],
                f"{r['service_level']*100:.2f}%",
                f"{r['unmet_liters']:,.0f}",
                f"{r['overflow_liters']:,.0f}",
                f"{r['failed_shipments']}",
                f"{r['min_station_sl']*100:.2f}%",
                f"{r['p95_solve_ms']:.1f}",
            )
        )
    print("=" * 90 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="FSIP Quick Policy Compare Benchmark")
    parser.add_argument("--sim-url", default="http://localhost:8000")
    parser.add_argument("--ticks", type=int, default=288)
    parser.add_argument("--scenarios", nargs="+", default=["baseline", "crisis"])
    args = parser.parse_args()

    runner = SimRunner(base_url=args.sim_url)
    all_results = []
    for sc in args.scenarios:
        for pol in ["heuristic", "optimizer"]:
            print(f"Running scenario={sc}, policy={pol}, ticks={args.ticks}...")
            res = runner.run_benchmark(scenario=sc, policy_name=pol, ticks=args.ticks)
            all_results.append(res)

    print_comparison_table(all_results)
