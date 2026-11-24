"""Twin Calibration Integration Test (Task 8.2).

Per PHASE_08_OPTIMIZER_AND_WHATIF.md Task 8.2:
1. Resets the simulator (seed 42).
2. Applies the heuristic for 96 ticks with stepping.
3. At every 12th tick (12, 24, 36, 48, 60, 72, 84), projects 12 ticks ahead with ShadowTwin
   (using the actual subsequent allocations).
4. Compares projected inventory trajectories against actual simulator inventories.
5. Asserts Mean Absolute Error (MAE) < 3% of station capacity.
6. Saves full calibration evidence to docs/evidence/twin_calibration.csv.
"""
from __future__ import annotations

import csv
import os
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from app.decision.heuristic import plan_heuristic
from app.decision.twin import ShadowTwin
from app.sim.circuit import CircuitBreaker
from app.sim.client import SimClient
from app.state.store import WorldState
from fsip_common.models import Allocation, Depot, Instance, Route, Station

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

SIM_TEST_URL = os.environ.get("SIM_URL", "http://localhost:8000")
FUELS = ["DIESEL", "PETROL", "OCTANE"]


@pytest_asyncio.fixture
async def sim() -> SimClient:
    breaker = CircuitBreaker("sim_test", failure_threshold=20, reset_timeout=5.0)
    client = SimClient(base_url=SIM_TEST_URL, timeout=30.0, breaker=breaker)
    try:
        health = await client.health()
        if health.status != 200:
            pytest.skip(f"Simulator at {SIM_TEST_URL} returned {health.status}")
    except Exception as exc:
        pytest.skip(f"Simulator at {SIM_TEST_URL} unreachable: {exc}")
    yield client
    await client.close()


def _build_store_from_data(
    inst: dict[str, Any],
    stations: list[dict[str, Any]],
    depots: list[dict[str, Any]],
    routes: list[dict[str, Any]],
    allocs: list[dict[str, Any]],
    events: list[dict[str, Any]],
    demands: list[dict[str, Any]],
) -> WorldState:
    """Build a typed WorldState from simulator API responses."""
    store = WorldState(sim_run_id=inst.get("id", 1))
    store.instance = Instance(
        id=inst.get("id", 1),
        scenario_id=inst.get("scenario_id", "sim"),
        scenario_version=inst.get("scenario_version", "1.0"),
        seed=inst.get("seed", 42),
        tick=inst.get("tick", 0),
        tick_minutes=inst.get("tick_minutes", 15),
        sim_time=inst.get("sim_time", "2026-01-01T00:00:00Z"),
        status=inst.get("status", "RUNNING"),
    )
    store.stations = [Station(**st) for st in stations]
    store.depots = [Depot(**d) for d in depots]
    store.routes = [Route(**r) for r in routes]
    store.events = events
    store.allocations = [Allocation.model_validate(a) for a in allocs]
    store.demand_history = demands
    return store


async def test_twin_calibration_mae_under_3pct(sim: SimClient) -> None:
    """Run 96 ticks with heuristic allocations and calibrate ShadowTwin accuracy."""
    # 1. Reset simulator to reproducible baseline seed 42
    reset_res = await sim.admin("POST", "reset", json={"seed": 42})
    assert reset_res.status == 200, f"Reset failed: {reset_res.data}"

    history_snapshots: dict[int, WorldState] = {}
    history_allocations: dict[int, list[Any]] = {}

    # Run for 96 ticks
    total_ticks = 96
    for t in range(total_ticks):
        # Fetch current state
        inst_r = await sim.instance()
        stations_r = await sim.stations()
        depots_r = await sim.depots()
        routes_r = await sim.routes()
        allocs_r = await sim.allocations()
        events_r = await sim.events()
        demands_r = await sim.demand_history(limit=200)

        inst = inst_r.data
        cur_tick = inst.get("tick", t)

        store = _build_store_from_data(
            inst=inst,
            stations=stations_r.data or [],
            depots=depots_r.data or [],
            routes=routes_r.data or [],
            allocs=allocs_r.data or [],
            events=events_r.data or [],
            demands=demands_r.data or [],
        )
        history_snapshots[cur_tick] = store

        # Heuristic decision
        h_plan = plan_heuristic(store)
        submitted_lines: list[Any] = []
        for line in h_plan.lines:
            payload = {
                "source_depot_id": line.source_depot_id,
                "destination_station_id": line.destination_station_id,
                "route_id": line.route_id,
                "fuel_type": line.fuel_type,
                "quantity": line.quantity,
                "idempotency_key": f"calib-t{cur_tick}-{line.route_id}-{line.fuel_type}-{line.quantity:.0f}",
            }
            res = await sim.create_allocation(payload)
            if res.status in (200, 201):
                # Tag with created_tick for subsequent twin replay
                line_obj = dict(
                    route_id=line.route_id,
                    fuel_type=line.fuel_type,
                    quantity=line.quantity,
                    created_tick=cur_tick,
                    source_depot_id=line.source_depot_id,
                    destination_station_id=line.destination_station_id,
                )
                submitted_lines.append(line_obj)

        history_allocations[cur_tick] = submitted_lines

        # Advance simulator by 1 tick
        await sim.admin_step()

    # Capture final state at tick 96
    inst_final = await sim.instance()
    stations_final = await sim.stations()
    depots_final = await sim.depots()
    routes_final = await sim.routes()
    allocs_final = await sim.allocations()
    events_final = await sim.events()
    demands_final = await sim.demand_history(limit=200)

    cur_tick = inst_final.data.get("tick", 96)
    history_snapshots[cur_tick] = _build_store_from_data(
        inst=inst_final.data,
        stations=stations_final.data or [],
        depots=depots_final.data or [],
        routes=routes_final.data or [],
        allocs=allocs_final.data or [],
        events=events_final.data or [],
        demands=demands_final.data or [],
    )

    # 2. Evaluate calibration at checkpoints: 12, 24, 36, 48, 60, 72, 84
    checkpoints = [12, 24, 36, 48, 60, 72, 84]
    lookahead = 12
    records: list[dict[str, Any]] = []

    for cp_tick in checkpoints:
        target_tick = cp_tick + lookahead
        if cp_tick not in history_snapshots or target_tick not in history_snapshots:
            continue

        base_store = history_snapshots[cp_tick]
        real_target_store = history_snapshots[target_tick]

        # Gather all actual allocations submitted during [cp_tick, target_tick - 1]
        subsequent_lines: list[Any] = []
        for tk in range(cp_tick, target_tick):
            subsequent_lines.extend(history_allocations.get(tk, []))

        # Project 12 ticks ahead with ShadowTwin
        twin_proj = ShadowTwin.project(
            state=base_store,
            lines=subsequent_lines,
            horizon=lookahead,
            n_samples=50,
        )

        real_stations_map = {st.id: st for st in real_target_store.stations}

        for (st_id, f_type), proj in twin_proj.stations.items():
            st_real = real_stations_map.get(st_id)
            if not st_real:
                continue

            cap = float(st_real.capacity.get(f_type, 10000.0))
            real_inv = float(st_real.inventory.get(f_type, 0.0))

            # Projected inventory at horizon index 11 (tick cp_tick + 12)
            proj_inv = proj.inventory_p50[lookahead - 1] if len(proj.inventory_p50) >= lookahead else 0.0

            abs_err = abs(proj_inv - real_inv)
            pct_err = (abs_err / cap) * 100.0

            records.append({
                "checkpoint_tick": cp_tick,
                "target_tick": target_tick,
                "station_id": st_id,
                "fuel_type": f_type,
                "capacity": cap,
                "real_inventory": round(real_inv, 1),
                "projected_p50": round(proj_inv, 1),
                "abs_error_liters": round(abs_err, 1),
                "error_pct_capacity": round(pct_err, 3),
            })

    # Save to CSV
    evidence_dir = Path("docs/evidence")
    evidence_dir.mkdir(parents=True, exist_ok=True)
    csv_path = evidence_dir / "twin_calibration.csv"

    with open(csv_path, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "checkpoint_tick",
                "target_tick",
                "station_id",
                "fuel_type",
                "capacity",
                "real_inventory",
                "projected_p50",
                "abs_error_liters",
                "error_pct_capacity",
            ],
        )
        writer.writeheader()
        writer.writerows(records)

    # 3. Compute Mean Absolute Error (MAE)
    assert len(records) > 0, "No calibration records gathered"
    mean_error_pct = sum(r["error_pct_capacity"] for r in records) / len(records)
    mean_abs_error_liters = sum(r["abs_error_liters"] for r in records) / len(records)

    print(
        f"\n[Twin Calibration Result] Count={len(records)}, "
        f"Mean Error={mean_error_pct:.2f}% of capacity (Mean Liters={mean_abs_error_liters:.1f} L)"
    )

    # Assert MAE < 3% of station capacity
    assert mean_error_pct < 3.0, (
        f"Twin Calibration MAE {mean_error_pct:.2f}% exceeded 3% of capacity threshold!"
    )
