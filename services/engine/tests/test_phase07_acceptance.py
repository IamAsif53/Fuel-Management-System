"""Phase 07 Acceptance Test Suite (Task 7.8).

Validates all Phase 07 detection and reconciliation acceptance gates:
1. Synthetic series step change -> CUSUM fires within <= 4 ticks.
2. Single demand spike -> point anomaly (|z| > 3.0).
3. Explained demand spike -> severity INFO with 'explained by event #N'.
4. Unexplained demand spike -> severity WARNING.
5. Supply delay detection -> SUPPLY_DELAY alert showing depot cover lost.
6. Pre-emptive route disruption -> scheduled 10 ticks ahead triggers immediate alert.
7. Chaos-proxy +7% inventory perturbation -> triggers INVENTORY_VARIANCE within <= 3 ticks.
8. Bottleneck and Regional Risk detectors trigger on qualification.
"""

from __future__ import annotations

from app.detect.anomaly import DemandAnomalyDetector
from app.detect.detectors import (
    BottleneckDetector,
    DisruptionDetector,
    RegionalRiskDetector,
    SupplyDelayDetector,
)
from app.detect.recon import (
    StationBalanceTracker,
)
from app.state.store import WorldState
from fsip_common.models import Depot, Instance, Route, Station


def _make_sample_store(tick: int = 100) -> WorldState:
    """Helper to build a WorldState with sample stations and depots."""
    store = WorldState(sim_run_id=1)
    store.instance = Instance(
        id=1,
        scenario_id="scenario_07",
        scenario_version="1.0",
        seed=42,
        tick=tick,
        tick_minutes=15,
        sim_time="2026-01-01T11:15:00Z",
        status="RUNNING",
    )
    st1 = Station(
        id="st-1",
        name="Station 1",
        region_id="reg-dhaka",
        capacity={"DIESEL": 10000.0, "PETROL": 8000.0, "OCTANE": 5000.0},
        inventory={"DIESEL": 5000.0, "PETROL": 4000.0, "OCTANE": 2500.0},
        demand_profile="urban",
        demand_multiplier=1.0,
        status="OPEN",
    )
    st2 = Station(
        id="st-2",
        name="Station 2",
        region_id="reg-dhaka",
        capacity={"DIESEL": 10000.0, "PETROL": 8000.0, "OCTANE": 5000.0},
        inventory={"DIESEL": 4500.0, "PETROL": 3800.0, "OCTANE": 2000.0},
        demand_profile="highway",
        demand_multiplier=1.0,
        status="OPEN",
    )
    dep1 = Depot(
        id="dep-1",
        name="Depot 1",
        region_id="reg-dhaka",
        capacity={"DIESEL": 50000.0, "PETROL": 40000.0, "OCTANE": 20000.0},
        inventory={"DIESEL": 30000.0, "PETROL": 25000.0, "OCTANE": 15000.0},
        dispatch_capacity_per_tick=5000.0,
        status="OPEN",
    )
    r1 = Route(
        id="r-1",
        source_depot_id="dep-1",
        destination_station_id="st-1",
        transit_ticks=4,
        max_shipment=5000.0,
        status="AVAILABLE",
    )
    store.stations = [st1, st2]
    store.depots = [dep1]
    store.routes = [r1]
    return store


# 1. Step Change CUSUM fires within <= 4 ticks
def test_step_change_cusum_fires_within_4_ticks() -> None:
    detector = DemandAnomalyDetector(k=0.5, h=5.0)
    # Baseline nominal: p50=250, p10=200, p90=300 -> sigma ~ 39.06
    # Inject persistent step change of +1.8x = 450 L
    # z = (450 - 250) / 39.06 = 5.12
    # CUSUM increment = 5.12 - 0.5 = 4.62
    fired_at_tick = None
    for step in range(1, 5):
        tick = 100 + step
        res = detector.update(
            station_id="st-1",
            fuel_type="DIESEL",
            tick=tick,
            actual_demand=450.0,
            prior_forecast={"p10": 200.0, "p50": 250.0, "p90": 300.0},
        )
        if res is not None and res.is_cusum_shift:
            fired_at_tick = step
            break

    assert fired_at_tick is not None, "CUSUM shift failed to detect step change"
    assert fired_at_tick <= 4, f"CUSUM shift took {fired_at_tick} ticks, expected <= 4"


# 2. Single demand spike point anomaly (|z| > 3.0)
def test_single_spike_point_anomaly() -> None:
    detector = DemandAnomalyDetector()
    # Nominal: p50=250, p10=200, p90=300 -> sigma ~ 39.06
    # Single extreme spike: actual = 500 L -> z = 6.4 > 3.0
    res = detector.update(
        station_id="st-1",
        fuel_type="DIESEL",
        tick=105,
        actual_demand=500.0,
        prior_forecast={"p10": 200.0, "p50": 250.0, "p90": 300.0},
    )
    assert res is not None
    assert res.is_point_anomaly is True
    assert res.z_score > 3.0


# 3. Explained demand spike -> severity INFO
def test_explained_demand_spike_severity_info() -> None:
    detector = DemandAnomalyDetector()
    events = [
        {
            "id": 101,
            "type": "demand_spike",
            "start_tick": 90,
            "end_tick": 120,
            "parameters": {"station_ids": ["st-1"], "fuel_type": "DIESEL", "multiplier": 1.8},
        }
    ]
    res = detector.update(
        station_id="st-1",
        fuel_type="DIESEL",
        tick=100,
        actual_demand=450.0,
        prior_forecast={"p10": 200.0, "p50": 250.0, "p90": 300.0},
        events=events,
    )
    assert res is not None
    assert res.is_explained is True
    assert res.explaining_event_id == 101
    assert res.severity == "INFO"
    assert "explained by event #101" in res.message


# 4. Unexplained demand spike -> severity WARNING
def test_unexplained_demand_spike_severity_warning() -> None:
    detector = DemandAnomalyDetector()
    # No events active
    res = detector.update(
        station_id="st-1",
        fuel_type="DIESEL",
        tick=100,
        actual_demand=450.0,
        prior_forecast={"p10": 200.0, "p50": 250.0, "p90": 300.0},
        events=[],
    )
    assert res is not None
    assert res.is_explained is False
    assert res.explaining_event_id is None
    assert res.severity == "WARNING"


# 5. Supply delay detection and lost cover hours
def test_supply_delay_detection_and_lost_cover() -> None:
    detector = SupplyDelayDetector()
    store = _make_sample_store(tick=100)
    store.supply_arrivals = [
        {
            "id": 77,
            "depot_id": "dep-1",
            "fuel_type": "DIESEL",
            "quantity": 10000.0,
            "planned_tick": 95,  # Planned 5 ticks ago
            "status": "DELAYED",
        }
    ]
    alerts = detector.detect(store)
    assert len(alerts) == 1
    a = alerts[0]
    assert a.category == "SUPPLY_DELAY"
    assert a.severity == "WARNING"
    assert a.entity_id == "dep-1"
    assert a.data["hours_lost"] >= 4.0  # 10,000 / 2,000 = 5.0h
    assert "DELAYED" in a.message


# 6. Pre-emptive route disruption alert for scheduled event
def test_scheduled_disruption_preemptive_alert() -> None:
    detector = DisruptionDetector(horizon=48)
    store = _make_sample_store(tick=100)
    # Event scheduled 10 ticks ahead (tick 110)
    store.events = [
        {
            "id": 88,
            "type": "route_disruption",
            "start_tick": 110,
            "end_tick": 134,
            "parameters": {"route_ids": ["r-1"]},
        }
    ]
    alerts = detector.detect(store)
    assert len(alerts) >= 1
    disr_alert = [al for al in alerts if al.category == "ROUTE_DISRUPTED" and "r-1" in al.fingerprint][0]
    assert disr_alert.severity == "WARNING"
    assert "will be DISRUPTED from tick 110" in disr_alert.message
    assert "pre-stock" in disr_alert.message.lower()


# 7. Chaos-proxy +7% inventory perturbation -> triggers INVENTORY_VARIANCE within <= 3 ticks
def test_chaos_proxy_inventory_variance_cusum() -> None:
    tracker = StationBalanceTracker(k=25.0, h=300.0)
    # Station capacity = 10,000 L -> 7% perturbation = +700 L
    # Baseline initialization at tick 100
    tracker.update(
        station_id="st-1",
        fuel_type="DIESEL",
        current_inventory=5000.0,
        capacity=10000.0,
        arrivals_liters=0.0,
        served_liters=100.0,
        tick=100,
    )

    # Next tick: inventory jumped by +700 L unaccounted for
    # Expected inventory: 5000 - 100 = 4900
    # Actual inventory: 5600 (delta = +600)
    # Variance = +600 - (0 - 100) = +700 L
    # CUSUM increment = 700 - 25 = 675 > 300 threshold!
    anom_alert, var_alert = tracker.update(
        station_id="st-1",
        fuel_type="DIESEL",
        current_inventory=5600.0,
        capacity=10000.0,
        arrivals_liters=0.0,
        served_liters=100.0,
        tick=101,
    )
    assert var_alert is not None, "Inventory variance CUSUM did not trigger on +7% perturbation"
    assert var_alert.category == "INVENTORY_VARIANCE"
    assert var_alert.severity == "WARNING"
    assert var_alert.data["cusum_val"] > 300.0


# 8. Bottleneck and Regional Risk detectors
def test_bottleneck_and_regional_risk_detectors() -> None:
    store = _make_sample_store(tick=100)
    # Both st-1 and st-2 have HIGH risk in reg-dhaka
    from app.risk.stockout import RiskResult

    store.risk_results[("st-1", "DIESEL")] = RiskResult(
        station_id="st-1",
        fuel_type="DIESEL",
        tick=100,
        risk_level="HIGH",
        hours_to_stockout=2.5,
        stockout_prob_lead=0.6,
        stockout_prob_horizon=0.7,
        inventory=5000.0,
        inflight=0.0,
        capacity=10000.0,
        fill_pct=0.5,
        fill_pct_with_inflight=0.5,
        cover_hours=12.0,
        lead_ticks=6,
        lead_time_hours=1.5,
        drivers=["demand spike"],
    )
    store.risk_results[("st-2", "DIESEL")] = RiskResult(
        station_id="st-2",
        fuel_type="DIESEL",
        tick=100,
        risk_level="HIGH",
        hours_to_stockout=3.0,
        stockout_prob_lead=0.55,
        stockout_prob_horizon=0.65,
        inventory=4500.0,
        inflight=0.0,
        capacity=10000.0,
        fill_pct=0.45,
        fill_pct_with_inflight=0.45,
        cover_hours=14.0,
        lead_ticks=6,
        lead_time_hours=1.5,
        drivers=["low inventory"],
    )

    # Regional Risk Detector
    reg_detector = RegionalRiskDetector()
    reg_alerts = reg_detector.detect(store)
    assert len(reg_alerts) == 1
    assert reg_alerts[0].category == "REGIONAL_RISK"
    assert "reg-dhaka" in reg_alerts[0].fingerprint

    # Bottleneck Detector
    # Setup depot dispatch >= 90% (4600 / 5000 = 92%)
    store.allocations = [{"id": 1, "source_depot_id": "dep-1", "quantity": 4600.0, "created_tick": 100}]
    btl_detector = BottleneckDetector(threshold=0.90, required_ticks=3)
    alerts = btl_detector.detect(store)
    assert len(alerts) == 0  # 1st tick
    alerts = btl_detector.detect(store)
    assert len(alerts) == 0  # 2nd tick
    alerts = btl_detector.detect(store)
    assert len(alerts) == 1  # 3rd tick -> fires!
    assert alerts[0].category == "BOTTLENECK"
    assert alerts[0].entity_id == "dep-1"
