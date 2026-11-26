"""Unit tests for AlertManager, IncidentManager, and Risk-Originated Alerts."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from app.alerts.manager import (
    AlertManager,
    AlertSpec,
    IncidentManager,
    generate_risk_alert_specs,
)
from app.risk.projection import DepotFuelProjection
from app.risk.stockout import RiskResult
from app.state.store import WorldState
from fsip_common.models import Allocation, Route, Station, SupplyArrival


@pytest.mark.asyncio
async def test_alert_manager_deduplication_and_escalation():
    """Verify in-place deduplication and severity escalation without silent downgrading."""
    redis_mock = AsyncMock()
    mgr = AlertManager(redis_client=redis_mock)

    # 1. Raise initial WARNING alert
    spec1 = AlertSpec(
        fingerprint="stockout:st-1:DIESEL",
        category="STOCKOUT_RISK",
        severity="WARNING",
        title="st-1 DIESEL high risk",
        message="Stockout in 5h",
        opened_tick=10,
    )
    a1 = await mgr.raise_(spec1)
    assert a1.status == "OPEN"
    assert a1.severity == "WARNING"
    assert len(mgr.active_alerts) == 1

    # 2. Re-raise with CRITICAL -> Escalates in place
    spec2 = AlertSpec(
        fingerprint="stockout:st-1:DIESEL",
        category="STOCKOUT_RISK",
        severity="CRITICAL",
        title="st-1 DIESEL critical risk",
        message="Stockout in 1h",
        opened_tick=11,
    )
    a2 = await mgr.raise_(spec2)
    assert a2 is a1  # Same object in place
    assert a2.severity == "CRITICAL"
    assert a2.title == "st-1 DIESEL critical risk"
    assert len(mgr.active_alerts) == 1

    # 3. Re-raise with WARNING -> Must NOT downgrade severity
    spec3 = AlertSpec(
        fingerprint="stockout:st-1:DIESEL",
        category="STOCKOUT_RISK",
        severity="WARNING",
        title="st-1 DIESEL warning",
        message="Stockout in 4h",
        opened_tick=12,
    )
    a3 = await mgr.raise_(spec3)
    assert a3.severity == "CRITICAL"  # Preserves CRITICAL
    assert len(mgr.active_alerts) == 1


@pytest.mark.asyncio
async def test_alert_manager_two_clean_cycle_resolution():
    """Verify alert auto-resolution strictly after condition absent for 2 consecutive cycles."""
    redis_mock = AsyncMock()
    mgr = AlertManager(redis_client=redis_mock)

    fp = "stockout:st-tongi:DIESEL"
    spec = AlertSpec(
        fingerprint=fp,
        category="STOCKOUT_RISK",
        severity="CRITICAL",
        title="Tongi critical",
        opened_tick=10,
    )
    await mgr.raise_(spec)
    assert fp in mgr.active_alerts

    # Cycle 1: condition cleared (fp not in active set)
    resolved_1 = await mgr.clear_check(active_fingerprints=set(), current_tick=11)
    assert len(resolved_1) == 0
    assert fp in mgr.active_alerts
    assert mgr.clean_cycles[fp] == 1

    # Interruption: condition returns briefly in Cycle 1.5 -> resets clean cycles to 0
    await mgr.clear_check(active_fingerprints={fp}, current_tick=12)
    assert mgr.clean_cycles[fp] == 0
    assert fp in mgr.active_alerts

    # Cycle 2: absent again
    await mgr.clear_check(active_fingerprints=set(), current_tick=13)
    assert mgr.clean_cycles[fp] == 1
    assert fp in mgr.active_alerts

    # Cycle 3: absent second consecutive time -> RESOLVES!
    resolved_2 = await mgr.clear_check(active_fingerprints=set(), current_tick=14)
    assert len(resolved_2) == 1
    assert resolved_2[0].fingerprint == fp
    assert resolved_2[0].status == "RESOLVED"
    assert resolved_2[0].resolved_tick == 14
    assert fp not in mgr.active_alerts


@pytest.mark.asyncio
async def test_alert_ack_is_not_resolution():
    """Verify that acknowledging an alert does not resolve it."""
    redis_mock = AsyncMock()
    mgr = AlertManager(redis_client=redis_mock)

    spec = AlertSpec(
        fingerprint="depot_depletion:depot-1:DIESEL",
        category="DEPOT_DEPLETION_RISK",
        severity="WARNING",
        title="Depot depletion in 8h",
        opened_tick=5,
    )
    alert = await mgr.raise_(spec)
    alert.id = 101

    acked = await mgr.ack(101, actor="operator_alice")
    assert acked is not None
    assert acked.status == "ACKNOWLEDGED"
    assert acked.acknowledged_by == "operator_alice"
    assert "depot_depletion:depot-1:DIESEL" in mgr.active_alerts


def test_generate_risk_alert_specs_all_categories():
    """Verify that generate_risk_alert_specs emits the exact locked categories."""
    store = WorldState()
    from fsip_common.models import Instance
    store.instance = Instance(
        id=1,
        scenario_id="baseline",
        scenario_version="1.0.0",
        seed=1,
        tick_minutes=15,
        status="RUNNING",
        sim_time="2026-01-01T00:00:00Z",
        tick=40,
    )
    store.sim_run_id = 7

    # Station with OUTAGE and critical risk
    store.stations = [
        Station(
            id="station-mirpur",
            name="Mirpur Station",
            region_id="region-dhaka",
            demand_profile="urban_high",
            demand_multiplier=1.0,
            status="OUTAGE",
            capacity={"DIESEL": 10000.0, "PETROL": 10000.0, "OCTANE": 10000.0},
            inventory={"DIESEL": 500.0, "PETROL": 5000.0, "OCTANE": 5000.0},
        )
    ]

    # Route with DISRUPTED
    store.routes = [
        Route(
            id="route-g-m",
            source_depot_id="depot-g",
            destination_station_id="station-mirpur",
            transit_ticks=2,
            max_shipment=7000.0,
            status="DISRUPTED",
        )
    ]

    # Supply arrival with DELAYED
    store.supply = [
        SupplyArrival(
            id="supply-1",
            depot_id="depot-g",
            fuel_type="DIESEL",
            quantity=15000.0,
            planned_tick=50,
            status="DELAYED",
        )
    ]

    # Allocation with FAILED
    store.allocations = [
        Allocation(
            id=99,
            idempotency_key="key-99",
            route_id="route-g-m",
            source_depot_id="depot-g",
            destination_station_id="station-mirpur",
            fuel_type="DIESEL",
            quantity=5000.0,
            status="FAILED",
            created_tick=30,
        )
    ]

    # Risk result for station-mirpur DIESEL
    r_res = RiskResult(
        station_id="station-mirpur",
        fuel_type="DIESEL",
        tick=40,
        risk_level="CRITICAL",
        hours_to_stockout=1.0,
        stockout_prob_lead=0.8,
        stockout_prob_horizon=0.95,
        inventory=500.0,
        inflight=0.0,
        capacity=10000.0,
        fill_pct=0.05,
        fill_pct_with_inflight=0.05,
        cover_hours=1.0,
        lead_ticks=5,
        lead_time_hours=1.25,
        drivers=["station_outage"],
    )
    station_risks = {("station-mirpur", "DIESEL"): r_res}

    # Depot projection with predicted overflow waste and depletion
    d_proj = DepotFuelProjection(
        depot_id="depot-g",
        fuel_type="PETROL",
        current_tick=40,
        current_inventory=48000.0,
        capacity=50000.0,
        supply_arrivals=[(42, 10000.0)],
        average_outflow_per_tick=100.0,
    )
    depot_projections = {("depot-g", "PETROL"): d_proj}

    specs, active_fps = generate_risk_alert_specs(store, station_risks, depot_projections)

    categories = {s.category for s in specs}
    assert "STOCKOUT_RISK" in categories
    assert "STATION_OUTAGE" in categories
    assert "ROUTE_DISRUPTED" in categories
    assert "SUPPLY_DELAY" in categories
    assert "ALLOCATION_FAILED" in categories
    assert "DEPOT_OVERFLOW_RISK" in categories
    assert len(active_fps) == len(specs)


@pytest.mark.asyncio
async def test_incident_manager_lifecycle_and_timeline():
    """Verify IncidentManager creates, mitigates, and resolves incidents with timeline ordering."""
    redis_mock = AsyncMock()
    mgr = IncidentManager(redis_client=redis_mock)

    # 1. Component down
    inc = await mgr.component_down("fsip-intelligence", "Connection refused on port 8200")
    assert inc.status == "OPEN"
    assert len(inc.timeline) == 1
    assert "detected: Connection refused" in inc.timeline[0]["event"]

    # 2. Mitigated
    mit = await mgr.mitigated("fsip-intelligence", "Fallback seasonal@builtin activated")
    assert mit is not None
    assert mit.status == "MITIGATED"
    assert len(mit.timeline) == 2
    assert "mitigated: Fallback" in mit.timeline[1]["event"]

    # 3. Recovered
    rec = await mgr.recovered("fsip-intelligence", "Health probe returned 200 OK")
    assert rec is not None
    assert rec.status == "RESOLVED"
    assert len(rec.timeline) == 3
    assert "recovered: Health probe" in rec.timeline[2]["event"]
    assert "fsip-intelligence" not in mgr.active_incidents


@pytest.mark.asyncio
async def test_alert_manager_open_counts_reset_and_world_state_projection():
    """Verify DEF-009: AlertManager tracks OPEN counts accurately, resets on new run, and WorldState publishes them."""
    redis_mock = AsyncMock()
    mgr = AlertManager(redis_client=redis_mock)

    await mgr.raise_(
        AlertSpec(
            fingerprint="fp-w",
            category="INVENTORY_VARIANCE",
            severity="WARNING",
            title="Variance at Mirpur",
            opened_tick=3,
            sim_run_id=10,
        )
    )
    a2 = await mgr.raise_(
        AlertSpec(
            fingerprint="fp-c",
            category="STOCKOUT_RISK",
            severity="CRITICAL",
            title="Critical Tongi",
            opened_tick=4,
            sim_run_id=10,
        )
    )
    assert mgr.get_open_counts() == {"INFO": 0, "WARNING": 1, "CRITICAL": 1}

    store = WorldState(sim_run_id=10)
    store.alerts_open_count = mgr.get_open_counts()
    store.alerts_open = mgr.get_open_alerts_summary()
    pub = store.to_public_json()
    assert pub["alerts_open_count"] == {"INFO": 0, "WARNING": 1, "CRITICAL": 1}
    assert len(pub["alerts_open"]) == 2

    # Acknowledging decrements OPEN count cleanly
    a2.id = 99
    await mgr.ack(99, actor="operator")
    assert mgr.get_open_counts() == {"INFO": 0, "WARNING": 1, "CRITICAL": 0}

    # Simulation reset clears open counts and active alerts
    mgr.reset()
    store.reset(new_sim_run_id=11)
    assert mgr.get_open_counts() == {"INFO": 0, "WARNING": 0, "CRITICAL": 0}
    assert store.to_public_json()["alerts_open_count"] == {"INFO": 0, "WARNING": 0, "CRITICAL": 0}

