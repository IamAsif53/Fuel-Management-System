"""Tests for ForecastProvider and fallback behavior in engine.

Specifications:
- 05_INTELLIGENCE_DESIGN.md §1.2, §1.4, §8
- PHASE_05_FORECASTING.md Task 5.6
"""

from datetime import UTC, datetime

import pytest
import respx
from app.intelligence.client import ForecastBundle, ForecastProvider
from app.sim.circuit import CircuitBreaker
from app.state.store import WorldState
from fsip_common.models import DemandObservation, Instance, SimMetrics, Station


def _make_sample_store() -> WorldState:
    """Creates a populated WorldState with station, demand observations, and instance."""
    store = WorldState()
    store.instance = Instance(
        id=1,
        scenario_id="baseline",
        scenario_version="1.0",
        seed=42,
        status="RUNNING",
        tick=100,
        sim_time=datetime.now(UTC),
        tick_minutes=15,
    )
    store.metrics = SimMetrics(
        served_demand_liters=1000.0,
        unmet_demand_liters=0.0,
        service_level=1.0,
        allocation_liters=5000.0,
        allocation_failures=0,
    )
    store.sim_run_id = 42

    st_mirpur = Station(
        id="station-mirpur",
        region_id="dhaka",
        name="Mirpur Station",
        demand_profile="urban_high",
        capacity={"DIESEL": 15000.0, "PETROL": 14000.0, "OCTANE": 9000.0},
        inventory={"DIESEL": 8000.0, "PETROL": 7000.0, "OCTANE": 5000.0},
        demand_multiplier=1.0,
        status="OPEN",
    )
    store.stations = [st_mirpur]

    # Populate demand observations
    obs_list = []
    for t in range(50, 101):
        obs = DemandObservation(
            id=t,
            station_id="station-mirpur",
            fuel_type="DIESEL",
            tick=t,
            demand_liters=80.0,
            served_liters=80.0,
            unmet_liters=0.0,
            demand_multiplier=1.0,
            sim_time=datetime.now(UTC),
        )
        obs_list.append(obs)
    store.apply_demand(obs_list)

    return store


@pytest.mark.asyncio
async def test_forecast_client_normal_operation() -> None:
    """When intelligence service is up, returns model predictions and clears degraded reasons."""
    store = _make_sample_store()
    provider = ForecastProvider(base_url="http://test-intelligence:8200")

    mock_resp = {
        "model": "lightgbm",
        "version": "1.0.0",
        "series": [
            {
                "station_id": "station-mirpur",
                "fuel_type": "DIESEL",
                "points": [
                    {"tick": 101, "p10": 70.0, "p50": 85.0, "p90": 100.0},
                    {"tick": 104, "p10": 72.0, "p50": 88.0, "p90": 105.0},
                    {"tick": 112, "p10": 75.0, "p50": 90.0, "p90": 110.0},
                    {"tick": 148, "p10": 70.0, "p50": 85.0, "p90": 100.0},
                ],
            }
        ],
    }

    with respx.mock(base_url="http://test-intelligence:8200") as respx_mock:
        respx_mock.post("/forecast").respond(status_code=200, json=mock_resp)

        bundle = await provider.get_forecast(store, horizon=48)

        assert isinstance(bundle, ForecastBundle)
        assert bundle.is_fallback is False
        assert bundle.model_name == "lightgbm"
        assert bundle.model_version == "1.0.0"
        assert bundle.get_p50("station-mirpur", "DIESEL", 101) == 85.0
        assert "prediction_service_down" not in store.degraded_reasons


@pytest.mark.asyncio
async def test_forecast_client_fallback_on_http_error() -> None:
    """When intelligence service returns 500, executes fallback and sets degraded reason."""
    store = _make_sample_store()
    provider = ForecastProvider(base_url="http://test-intelligence:8200")

    with respx.mock(base_url="http://test-intelligence:8200") as respx_mock:
        respx_mock.post("/forecast").respond(status_code=500, text="Internal Error")

        bundle = await provider.get_forecast(store, horizon=48)

        assert bundle.is_fallback is True
        assert bundle.model_name == "seasonal"
        assert bundle.model_version == "builtin"
        assert "prediction_service_down" in store.degraded_reasons

        # Verify fallback produced non-negative p50 predictions
        p50_val = bundle.get_p50("station-mirpur", "DIESEL", 101)
        assert p50_val is not None and p50_val > 0.0


@pytest.mark.asyncio
async def test_forecast_client_breaker_protection() -> None:
    """Breaker opens after failure threshold and immediately executes fallback without HTTP call."""
    store = _make_sample_store()
    breaker = CircuitBreaker("intelligence-test", failure_threshold=2, reset_timeout=10.0)
    provider = ForecastProvider(base_url="http://test-intelligence:8200", breaker=breaker)

    with respx.mock(base_url="http://test-intelligence:8200") as respx_mock:
        respx_mock.post("/forecast").respond(status_code=503)

        # 1st failure
        await provider.get_forecast(store, horizon=48)
        assert breaker.state == CircuitBreaker.CLOSED

        # 2nd failure -> trips breaker
        await provider.get_forecast(store, horizon=48)
        assert breaker.state == CircuitBreaker.OPEN
        assert "intelligence_breaker_open" in store.degraded_reasons

        # 3rd call -> circuit OPEN, no network call made
        call_count_before = len(respx_mock.calls)
        bundle3 = await provider.get_forecast(store, horizon=48)
        assert len(respx_mock.calls) == call_count_before  # No new call
        assert bundle3.is_fallback is True


def test_forecast_to_db_rows_selective_offsets() -> None:
    """to_db_rows produces records only for offsets t+1, t+4, t+12, t+48."""
    made_at = 100
    points = [
        {"tick": made_at + offset, "p10": 10.0, "p50": 50.0, "p90": 90.0}
        for offset in [1, 2, 3, 4, 12, 24, 48]
    ]
    bundle = ForecastBundle(
        model_name="lightgbm",
        model_version="1.0.0",
        is_fallback=False,
        made_at_tick=made_at,
        forecasts={
            ("station-mirpur", "DIESEL"): {
                "tick": [p["tick"] for p in points],
                "p10": [p["p10"] for p in points],
                "p50": [p["p50"] for p in points],
                "p90": [p["p90"] for p in points],
            }
        },
    )

    rows = bundle.to_db_rows(sim_run_id=1)
    target_ticks = {r["target_tick"] for r in rows}
    assert target_ticks == {101, 104, 112, 148}
    assert len(rows) == 4
