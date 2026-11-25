"""Tests for Phase 18 Advanced Contingency & Uncertainty Analytics.

Verifies:
1. N-1 Contingency Analysis (/api/contingency/n-1).
2. Conformal Prediction intervals (/api/contingency/conformal).
3. Demand Suppression modeling (/api/contingency/demand-suppression).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import orjson
import pytest
from app.main import app


@pytest.fixture
def mock_world_state():
    return {
        "sim_run_id": 101,
        "tick": 120,
        "sim_time": "2026-01-02T06:00:00Z",
        "stale": False,
        "degraded": False,
        "depots": [
            {
                "id": "dp-1",
                "name": "Central Depot",
                "inventory": {"DIESEL": 60000.0, "PETROL": 40000.0, "OCTANE": 20000.0},
                "status": "OPEN",
            },
            {
                "id": "dp-2",
                "name": "Northern Depot",
                "inventory": {"DIESEL": 40000.0, "PETROL": 30000.0, "OCTANE": 15000.0},
                "status": "OPEN",
            },
        ],
        "stations": [
            {
                "id": "st-1",
                "name": "Urban Station 1",
                "region_id": "reg-dhaka",
                "capacity": {"DIESEL": 20000.0, "PETROL": 15000.0, "OCTANE": 10000.0},
                "inventory": {"DIESEL": 5000.0, "PETROL": 4000.0, "OCTANE": 2500.0},
                "demand_multiplier": 1.2,
                "status": "OPEN",
            },
            {
                "id": "st-2",
                "name": "Remote Station 2",
                "region_id": "reg-chittagong",
                "capacity": {"DIESEL": 15000.0, "PETROL": 10000.0, "OCTANE": 8000.0},
                "inventory": {"DIESEL": 2000.0, "PETROL": 1500.0, "OCTANE": 1000.0},
                "demand_multiplier": 0.8,
                "status": "OPEN",
            },
        ],
        "routes": [
            # st-1 has two sources (dp-1, dp-2) -> NOT a SPOF
            {"id": "r-1", "source_depot_id": "dp-1", "destination_station_id": "st-1", "status": "AVAILABLE"},
            {"id": "r-2", "source_depot_id": "dp-2", "destination_station_id": "st-1", "status": "AVAILABLE"},
            # st-2 has only ONE source (dp-1) -> dp-1 and r-3 are SPOFs for st-2!
            {"id": "r-3", "source_depot_id": "dp-1", "destination_station_id": "st-2", "status": "AVAILABLE"},
        ],
        "supply_upcoming": [
            {"id": "sup-1", "depot_id": "dp-1", "fuel_type": "DIESEL", "quantity": 40000.0, "eta_tick": 128}
        ],
    }


@pytest.mark.asyncio
async def test_n1_contingency_analysis(mock_world_state):
    """Verify /api/contingency/n-1 detects single points of failure and scores resilience."""
    mock_redis = MagicMock()
    mock_redis.get = AsyncMock(return_value=orjson.dumps(mock_world_state).decode())
    app.state.redis = mock_redis

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get("/api/contingency/n-1")
        assert res.status_code == 200
        data = res.json()

        assert data["tick"] == 120
        assert data["total_contingencies_evaluated"] >= 5  # 2 depots + 3 routes + 1 supply
        assert data["single_points_of_failure_count"] >= 1
        assert "depot:dp-1" in data["spof_identifiers"]
        assert "route:r-3" in data["spof_identifiers"]
        assert 0.0 <= data["network_resilience_score"] <= 1.0


@pytest.mark.asyncio
async def test_conformal_prediction_intervals(mock_world_state):
    """Verify /api/contingency/conformal generates statistically calibrated intervals."""
    mock_redis = MagicMock()
    mock_redis.get = AsyncMock(return_value=orjson.dumps(mock_world_state).decode())
    app.state.redis = mock_redis

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get("/api/contingency/conformal?coverage=0.90&horizon_ticks=24")
        assert res.status_code == 200
        data = res.json()

        assert data["target_coverage"] == 0.90
        assert data["empirical_coverage"] >= 0.85
        assert data["valid_calibration"] is True
        assert data["calibration_sample_size"] == 30
        assert len(data["station_intervals"]) == 2

        # Check interval bounds monotonicity: lower <= point <= upper
        st1_diesel = data["station_intervals"][0]["fuels"]["DIESEL"]["projections"]
        for p in st1_diesel:
            assert p["lower_bound"] <= p["point_estimate"] <= p["upper_bound"]
            assert p["interval_width"] == round(p["upper_bound"] - p["lower_bound"], 1)


@pytest.mark.asyncio
async def test_demand_suppression_modeling(mock_world_state):
    """Verify /api/contingency/demand-suppression models demand rationing impact."""
    mock_redis = MagicMock()
    mock_redis.get = AsyncMock(return_value=orjson.dumps(mock_world_state).decode())
    app.state.redis = mock_redis

    payload = {
        "reduction_pct": 25.0,
        "duration_ticks": 96,
        "fuel_type": "ALL",
    }

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post("/api/contingency/demand-suppression", json=payload)
        assert res.status_code == 200
        data = res.json()

        summary = data["summary"]
        assert summary["overall_consumption_reduction_pct"] == 25.0
        assert summary["preserved_fuel_liters"] > 0
        assert summary["estimated_extended_cover_hours"] > 0

        # Verify extended dryout tick is greater than baseline dryout tick
        for st_proj in data["station_projections"]:
            assert st_proj["extended_dryout_tick"] >= st_proj["baseline_dryout_tick"]
            assert st_proj["gain_ticks"] >= 0
