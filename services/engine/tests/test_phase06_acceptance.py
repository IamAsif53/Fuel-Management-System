"""Phase 06 Acceptance & Verification Test Suite.

Verifies the 4 critical acceptance gates defined in PHASE_06_FORECASTING.md:
1. Tongi DIESEL STOCKOUT_RISK alert triggers before tick 66.
2. Dhaka demand spike x1.8 raises Dhaka risk within 1-2 ticks.
3. Alerts auto-resolve strictly after 2 clean cycles post-refill.
4. Monte Carlo risk assessment p95 latency is strictly < 50 ms.
"""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest
from app.alerts.manager import AlertManager, AlertSpec, generate_risk_alert_specs
from app.risk.stockout import assess
from app.state.store import WorldState
from fsip_common.models import Depot, Instance, Route, Station


def _make_sample_store(
    tongi_diesel_inv: float = 6500.0,
    dhaka_diesel_inv: float = 7500.0,
    dhaka_mult: float = 1.0,
    tick: int = 45,
) -> WorldState:
    store = WorldState(sim_run_id=1)
    instance = Instance(
        id=1,
        scenario_id="scenario_06",
        scenario_version="1.0",
        seed=42,
        tick=tick,
        tick_minutes=15,
        sim_time="2026-01-01T11:15:00Z",
        status="RUNNING",
    )
    tongi = Station(
        id="station-tongi",
        name="Tongi Station",
        region_id="dhaka",
        status="OPEN",
        capacity={"DIESEL": 50000.0, "PETROL": 30000.0, "OCTANE": 20000.0},
        inventory={"DIESEL": tongi_diesel_inv, "PETROL": 15000.0, "OCTANE": 10000.0},
        demand_profile="industrial",
        demand_multiplier=1.2,
    )
    dhaka_st = Station(
        id="station-dhaka",
        name="Dhaka Station",
        region_id="dhaka",
        status="OPEN",
        capacity={"DIESEL": 60000.0, "PETROL": 40000.0, "OCTANE": 20000.0},
        inventory={"DIESEL": dhaka_diesel_inv, "PETROL": 20000.0, "OCTANE": 12000.0},
        demand_profile="commercial",
        demand_multiplier=dhaka_mult,
    )
    depot = Depot(
        id="depot-dhaka",
        name="Dhaka Main Depot",
        region_id="dhaka",
        status="OPEN",
        dispatch_capacity_per_tick=50000.0,
        capacity={"DIESEL": 200000.0, "PETROL": 100000.0, "OCTANE": 50000.0},
        inventory={"DIESEL": 100000.0, "PETROL": 50000.0, "OCTANE": 25000.0},
    )
    routes = [
        Route(
            id="r-tongi",
            source_depot_id="depot-dhaka",
            destination_station_id="station-tongi",
            transit_ticks=3,
            max_shipment=8000.0,
            status="AVAILABLE",
        ),
        Route(
            id="r-dhaka",
            source_depot_id="depot-dhaka",
            destination_station_id="station-dhaka",
            transit_ticks=2,
            max_shipment=10000.0,
            status="AVAILABLE",
        ),
    ]
    store.apply({
        "instance": instance,
        "stations": [tongi, dhaka_st],
        "depots": [depot],
        "routes": routes,
        "supply": [],
        "events": [],
        "allocations": [],
    }, tick=tick)
    return store


def test_tongi_diesel_stockout_risk_before_tick_66():
    """Acceptance Check 1: Verify Tongi DIESEL STOCKOUT_RISK alert triggers before tick 66."""
    store = _make_sample_store(tongi_diesel_inv=4500.0, tick=48)  # tick 48 < 66

    station_risks, depot_projections = assess(store)
    tongi_res = station_risks.get(("station-tongi", "DIESEL"))
    assert tongi_res is not None
    assert tongi_res.risk_level in ("CRITICAL", "HIGH")
    assert tongi_res.hours_to_stockout < 8.0  # stockout within 8 hours (< 32 ticks)

    # Verify alert generation
    alert_specs, active_fps = generate_risk_alert_specs(
        store=store,
        station_risks=station_risks,
        depot_projections=depot_projections,
    )

    tongi_alert = next(
        (a for a in alert_specs if a.entity_id == "station-tongi" and a.category == "STOCKOUT_RISK"),
        None,
    )
    assert tongi_alert is not None
    assert tongi_alert.severity in ("CRITICAL", "WARNING")
    assert "DIESEL" in tongi_alert.title or "DIESEL" in (tongi_alert.message or "")


def test_dhaka_demand_spike_raises_risk_within_two_ticks():
    """Acceptance Check 2: Verify Dhaka demand spike x1.8 raises Dhaka risk within 1-2 ticks."""
    # Tick T (normal demand mult = 1.0)
    store_normal = _make_sample_store(dhaka_mult=1.0, tick=40)
    risks_normal, _ = assess(store_normal)
    dhaka_normal = risks_normal[("station-dhaka", "DIESEL")]

    # Tick T+1 (demand spike mult = 1.8)
    store_spiked = _make_sample_store(dhaka_mult=1.8, tick=41)
    risks_spiked, _ = assess(store_spiked)
    dhaka_spiked = risks_spiked[("station-dhaka", "DIESEL")]

    # Risk hours must decrease significantly and stockout probability increase
    assert dhaka_spiked.hours_to_stockout is not None
    if dhaka_normal.hours_to_stockout is not None:
        assert dhaka_spiked.hours_to_stockout < dhaka_normal.hours_to_stockout
    assert dhaka_spiked.stockout_prob_horizon >= dhaka_normal.stockout_prob_horizon
    assert dhaka_spiked.risk_level in ("HIGH", "CRITICAL", "MEDIUM")


@pytest.mark.asyncio
async def test_alerts_autoresolve_after_two_clean_cycles():
    """Acceptance Check 3: Verify alerts auto-resolve strictly after 2 clean cycles post-refill."""
    mock_redis = MagicMock()
    mock_redis.xadd = AsyncMock()

    mock_session = MagicMock()
    mock_begin = MagicMock()
    mock_begin.__aenter__ = AsyncMock()
    mock_begin.__aexit__ = AsyncMock()
    mock_session.begin = MagicMock(return_value=mock_begin)
    mock_session.execute = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_sm = MagicMock()
    mock_sm.return_value.__aenter__.return_value = mock_session
    mock_sm.return_value.__aexit__ = AsyncMock()

    mgr = AlertManager(redis_client=mock_redis, session_factory=mock_sm)

    # 1. Raise alert in Cycle 1
    spec = AlertSpec(
        fingerprint="station:station-tongi:DIESEL:stockout_risk",
        category="STOCKOUT_RISK",
        severity="CRITICAL",
        title="Tongi DIESEL Stockout Risk",
        entity_type="station",
        entity_id="station-tongi",
        fuel_type="DIESEL",
        opened_tick=50,
        sim_run_id=1,
    )
    await mgr.raise_(spec)
    assert "station:station-tongi:DIESEL:stockout_risk" in mgr.active_alerts
    alert = mgr.active_alerts["station:station-tongi:DIESEL:stockout_risk"]
    assert alert.status == "OPEN"

    # 2. Cycle 2: Condition clears (not in active_fingerprints)
    # 1st clean cycle -> clean_cycles count reaches 1, alert remains OPEN
    resolved_1 = await mgr.clear_check(active_fingerprints=set(), current_tick=51)
    assert len(resolved_1) == 0
    assert "station:station-tongi:DIESEL:stockout_risk" in mgr.active_alerts
    assert mgr.active_alerts["station:station-tongi:DIESEL:stockout_risk"].status == "OPEN"
    assert mgr.clean_cycles["station:station-tongi:DIESEL:stockout_risk"] == 1

    # 3. Cycle 3: Condition still clear
    # 2nd clean cycle -> clean_cycles count reaches 2 -> auto-resolves!
    resolved_2 = await mgr.clear_check(active_fingerprints=set(), current_tick=52)
    assert len(resolved_2) == 1
    assert resolved_2[0].fingerprint == "station:station-tongi:DIESEL:stockout_risk"
    assert resolved_2[0].status == "RESOLVED"
    assert resolved_2[0].resolved_tick == 52
    assert "station:station-tongi:DIESEL:stockout_risk" not in mgr.active_alerts


def test_monte_carlo_p95_latency_under_50ms():
    """Acceptance Check 4: Verify Monte Carlo assessment p95 latency is strictly < 50 ms."""
    store = _make_sample_store(tick=40)

    # Warmup
    assess(store)

    # Measure 50 iterations
    latencies = []
    for i in range(50):
        store.instance.tick = 40 + i
        t0 = time.perf_counter()
        station_risks, _ = assess(store)
        lat = (time.perf_counter() - t0) * 1000.0
        latencies.append(lat)
        assert len(station_risks) == 6  # 2 stations x 3 fuels

    p95 = float(np.percentile(latencies, 95))
    median = float(np.median(latencies))
    print(f"\n[Performance Evidence] Risk assessment p95 across series: median={median:.2f}ms, p95={p95:.2f}ms")
    assert p95 < 50.0, f"p95 latency {p95:.2f}ms exceeded 50ms threshold"
