"""Unit tests for live forecast accuracy and drift monitoring (Task 5.7)."""

from __future__ import annotations

from collections import deque
from unittest.mock import AsyncMock, MagicMock

import pytest
from app.intelligence.client import ForecastBundle, ForecastProvider
from app.intelligence.monitor import (
    ForecastQualityMonitor,
)
from app.state.store import WorldState
from fsip_common.models import DemandObservation


class DummyStore(WorldState):
    """WorldState test double."""

    def __init__(self, tick: int = 10, sim_run_id: int = 1):
        super().__init__()
        self._tick = tick
        self.sim_run_id = sim_run_id
        self.degraded_reasons = set()
        self.demand_rows = {}

    @property
    def tick(self) -> int:
        return self._tick

    @tick.setter
    def tick(self, val: int) -> None:
        self._tick = val


def make_forecast_bundle(
    made_at_tick: int,
    station_id: str = "station-1",
    fuel_type: str = "DIESEL",
    p10: float = 80.0,
    p50: float = 100.0,
    p90: float = 120.0,
    horizons: list[int] | None = None,
) -> ForecastBundle:
    """Create a ForecastBundle fixture for testing."""
    horizons = horizons or [1, 2, 4, 8, 12, 24, 48]
    ticks = [made_at_tick + h for h in horizons]
    p10s = [p10] * len(ticks)
    p50s = [p50] * len(ticks)
    p90s = [p90] * len(ticks)

    return ForecastBundle(
        model_name="seasonal",
        model_version="builtin",
        is_fallback=False,
        made_at_tick=made_at_tick,
        forecasts={
            (station_id, fuel_type): {
                "tick": ticks,
                "p10": p10s,
                "p50": p50s,
                "p90": p90s,
            }
        },
    )


def make_obs(
    tick: int,
    demand_liters: float,
    station_id: str = "station-1",
    fuel_type: str = "DIESEL",
) -> DemandObservation:
    """Create a DemandObservation fixture."""
    return DemandObservation(
        id=tick,
        station_id=station_id,
        fuel_type=fuel_type,
        tick=tick,
        sim_time="2026-01-01T00:15:00+00:00",
        demand_liters=demand_liters,
        served_liters=demand_liters,
        unmet_liters=0.0,
    )


@pytest.mark.asyncio
async def test_record_forecast_and_eviction():
    """Verify forecast storage and old forecast eviction."""
    monitor = ForecastQualityMonitor()
    bundle = make_forecast_bundle(made_at_tick=10)
    monitor.record_forecast(bundle)

    # Check that predictions for t=11, 14, 22 are cached
    assert (10, 11, "station-1", "DIESEL") in monitor._forecast_store
    assert monitor._forecast_store[(10, 11, "station-1", "DIESEL")] == (80.0, 100.0, 120.0)

    # Add a bundle far in the future (made_at=350)
    future_bundle = make_forecast_bundle(made_at_tick=350)
    monitor.record_forecast(future_bundle)

    # Tick 11 prediction should be evicted (350 - 200 = 150 > 11)
    assert (10, 11, "station-1", "DIESEL") not in monitor._forecast_store
    assert (350, 351, "station-1", "DIESEL") in monitor._forecast_store


@pytest.mark.asyncio
async def test_evaluate_tick_matching_and_metrics():
    """Verify tick evaluation matches forecasts at horizons 1, 4, 12 and updates gauges."""
    monitor = ForecastQualityMonitor(backtest_wape=0.05, min_eval_ticks=1)
    store = DummyStore(tick=20)
    provider = MagicMock(spec=ForecastProvider)
    provider.drift_fallback = False

    # Seed forecasts made at t=19 (k=1), t=16 (k=4), t=8 (k=12) all targeting t=20
    b_k1 = make_forecast_bundle(made_at_tick=19, p10=90.0, p50=100.0, p90=110.0)
    b_k4 = make_forecast_bundle(made_at_tick=16, p10=85.0, p50=102.0, p90=115.0)
    b_k12 = make_forecast_bundle(made_at_tick=8, p10=80.0, p50=98.0, p90=120.0)
    monitor.record_forecast(b_k1)
    monitor.record_forecast(b_k4)
    monitor.record_forecast(b_k12)

    # Realized demand at t=20 is 105.0 liters
    obs = make_obs(tick=20, demand_liters=105.0)
    store.demand_rows[("station-1", "DIESEL")] = deque([obs])

    res = await monitor.evaluate_tick(store, provider)

    assert res["tick"] == 20
    # k=1 error: |105 - 100| = 5.0. WAPE = 5.0 / 105.0 ~ 0.0476
    assert abs(res["rolling_wape"] - (5.0 / 105.0)) < 0.001
    # 105 is inside [90, 110], coverage = 1.0
    assert res["rolling_coverage"] == 1.0
    assert not res["is_drifting"]


@pytest.mark.asyncio
async def test_drift_detection_and_fallback_switch():
    """Verify drift detection triggers after 8 consecutive high-error ticks."""
    monitor = ForecastQualityMonitor(
        backtest_wape=0.05,
        drift_multiplier=2.0,  # threshold = 0.10
        drift_patience_ticks=8,
        min_eval_ticks=1,
    )
    provider = MagicMock(spec=ForecastProvider)
    provider.drift_fallback = False

    store = DummyStore(tick=10)

    # Simulate 8 ticks of high error (WAPE > 0.10, e.g. actual=100, predicted=80 -> err=20 / 100 = 0.20)
    for t in range(10, 18):
        store.tick = t
        b = make_forecast_bundle(made_at_tick=t - 1, p10=70.0, p50=80.0, p90=90.0)
        monitor.record_forecast(b)

        obs = make_obs(tick=t, demand_liters=100.0)
        store.demand_rows[("station-1", "DIESEL")] = deque([obs])

        res = await monitor.evaluate_tick(store, provider)

    # After 8 ticks above threshold (0.20 > 0.10), drift MUST trigger
    assert res["is_drifting"] is True
    assert monitor.is_drifting is True
    assert provider.set_drift_fallback.called
    provider.set_drift_fallback.assert_called_with(True)
    assert "model_drift_fallback" in store.degraded_reasons


@pytest.mark.asyncio
async def test_drift_recovery():
    """Verify recovery after consecutive low-error ticks."""
    monitor = ForecastQualityMonitor(
        backtest_wape=0.05,
        drift_multiplier=2.0,  # threshold = 0.10
        drift_patience_ticks=2,
        recovery_patience_ticks=3,
        min_eval_ticks=1,
        window_ticks=3,
    )
    provider = MagicMock(spec=ForecastProvider)
    provider.drift_fallback = False
    store = DummyStore(tick=1)

    # 1. Trigger drift (2 high-error ticks)
    for t in range(1, 3):
        store.tick = t
        b = make_forecast_bundle(made_at_tick=t - 1, p50=50.0)
        monitor.record_forecast(b)
        obs = make_obs(tick=t, demand_liters=100.0)
        store.demand_rows[("station-1", "DIESEL")] = deque([obs])
        await monitor.evaluate_tick(store, provider)

    assert monitor.is_drifting is True

    # 2. Recover with low-error ticks (actual=100, predicted=100 -> err=0)
    # Window size is 3, so after 3 low-error ticks (t=3,4,5), window only contains healthy ticks
    # and consecutive recovery ticks will reach recovery_patience_ticks (3)
    for t in range(3, 8):
        store.tick = t
        b = make_forecast_bundle(made_at_tick=t - 1, p50=100.0)
        monitor.record_forecast(b)
        obs = make_obs(tick=t, demand_liters=100.0)
        store.demand_rows[("station-1", "DIESEL")] = deque([obs])
        res = await monitor.evaluate_tick(store, provider)

    assert res["is_drifting"] is False
    assert monitor.is_drifting is False
    provider.set_drift_fallback.assert_called_with(False)
    assert "model_drift_fallback" not in store.degraded_reasons


@pytest.mark.asyncio
async def test_drift_alert_and_incident_lifecycle(monkeypatch):
    """Verify alert and incident persistence, deduplication, and resolution."""
    mock_upsert = AsyncMock()
    mock_open_inc = AsyncMock()
    mock_open_inc.return_value = MagicMock(id=99)
    mock_resolve_alert = AsyncMock()
    mock_resolve_inc = AsyncMock()

    monkeypatch.setattr("app.intelligence.monitor.upsert_alert", mock_upsert)
    monkeypatch.setattr("app.intelligence.monitor.open_incident", mock_open_inc)
    monkeypatch.setattr("app.intelligence.monitor.resolve_alert", mock_resolve_alert)
    monkeypatch.setattr("app.intelligence.monitor.resolve_incident", mock_resolve_inc)

    mock_session = AsyncMock()
    mock_session.begin = MagicMock()
    mock_session.begin.return_value.__aenter__ = AsyncMock(return_value=None)
    mock_session.begin.return_value.__aexit__ = AsyncMock(return_value=None)
    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__.return_value = mock_session
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=None)

    monitor = ForecastQualityMonitor(
        session_factory=mock_session_factory,
        backtest_wape=0.05,
        drift_multiplier=2.0,
        drift_patience_ticks=2,
        recovery_patience_ticks=2,
        min_eval_ticks=1,
        window_ticks=2,
    )
    provider = MagicMock(spec=ForecastProvider)
    provider.drift_fallback = False
    store = DummyStore(tick=1, sim_run_id=42)

    # 1. Trigger drift across 3 high-error ticks
    for t in range(1, 4):
        store.tick = t
        b = make_forecast_bundle(made_at_tick=t - 1, p50=50.0)
        monitor.record_forecast(b)
        obs = make_obs(tick=t, demand_liters=100.0)
        store.demand_rows[("station-1", "DIESEL")] = deque([obs])
        await monitor.evaluate_tick(store, provider)

    # Drift triggered at t=2; t=3 should NOT trigger duplicate alert/incident
    assert mock_upsert.call_count == 1
    assert mock_open_inc.call_count == 1
    alert_arg = mock_upsert.call_args[0][1]
    assert alert_arg["category"] == "MODEL_DRIFT"
    assert alert_arg["severity"] == "WARNING"
    assert alert_arg["sim_run_id"] == 42
    assert "alert:model_drift:42" in alert_arg["fingerprint"]

    # 2. Recover across 3 low-error ticks
    for t in range(4, 7):
        store.tick = t
        b = make_forecast_bundle(made_at_tick=t - 1, p50=100.0)
        monitor.record_forecast(b)
        obs = make_obs(tick=t, demand_liters=100.0)
        store.demand_rows[("station-1", "DIESEL")] = deque([obs])
        await monitor.evaluate_tick(store, provider)

    assert monitor.is_drifting is False
    assert mock_resolve_alert.call_count == 1
    assert mock_resolve_inc.call_count == 1
    assert mock_resolve_inc.call_args[1]["incident_id"] == 99
