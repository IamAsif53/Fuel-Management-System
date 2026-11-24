"""Unit tests for Risk, Alert, Forecast, Cover, and Incident APIs."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import orjson
import pytest
from app.config import get_settings
from app.db import get_session
from app.main import app


@pytest.fixture
def auth_headers():
    settings = get_settings()
    return {"Authorization": f"Bearer {settings.api_operator_token}"}


@pytest.fixture
def sample_world_state():
    return {
        "sim_run_id": 1,
        "tick": 40,
        "sim_time": "2026-01-01T10:00:00Z",
        "stations": [
            {
                "id": "station-dhaka",
                "name": "Dhaka Station",
                "region_id": "dhaka",
                "inventory": {"DIESEL": 5000.0, "PETROL": 10000.0},
                "demand_multiplier": 1.0,
                "risk": {
                    "DIESEL": {
                        "level": "CRITICAL",
                        "hours_to_stockout": 2.5,
                        "stockout_prob": 0.95,
                        "drivers": ["low_inventory"],
                    },
                    "PETROL": {
                        "level": "LOW",
                        "hours_to_stockout": 40.0,
                        "stockout_prob": 0.01,
                        "drivers": [],
                    },
                },
            },
            {
                "id": "station-chattogram",
                "name": "Chattogram Station",
                "region_id": "chattogram",
                "inventory": {"DIESEL": 12000.0, "PETROL": 4000.0},
                "demand_multiplier": 1.2,
                "risk": {
                    "DIESEL": {
                        "level": "HIGH",
                        "hours_to_stockout": 8.0,
                        "stockout_prob": 0.60,
                        "drivers": ["high_demand"],
                    },
                    "PETROL": {
                        "level": "MEDIUM",
                        "hours_to_stockout": 14.0,
                        "stockout_prob": 0.30,
                        "drivers": [],
                    },
                },
            },
        ],
        "depots": [
            {
                "id": "depot-central",
                "name": "Central Depot",
                "region_id": "dhaka",
                "inventory": {"DIESEL": 80000.0, "PETROL": 50000.0, "OCTANE": 30000.0},
            }
        ],
        "allocations_active": [
            {"fuel_type": "DIESEL", "quantity": 5000.0, "status": "IN_TRANSIT"}
        ],
        "alerts_open": [
            {
                "id": 1,
                "fingerprint": "fp-1",
                "category": "STOCKOUT_RISK",
                "severity": "CRITICAL",
                "title": "Dhaka DIESEL stockout",
                "status": "OPEN",
            }
        ],
        "scarcity": {"DIESEL": False, "PETROL": False, "OCTANE": False},
        "degraded": False,
    }


@pytest.mark.asyncio
async def test_get_risk_sorted_and_filtered(sample_world_state):
    """Verify /api/risk returns rows sorted CRITICAL -> LOW then hours ascending, and respects filters."""
    mock_redis = MagicMock()
    mock_redis.get = AsyncMock(return_value=orjson.dumps(sample_world_state).decode())
    app.state.redis = mock_redis

    mock_session = AsyncMock()

    async def override_get_session():
        yield mock_session

    app.dependency_overrides[get_session] = override_get_session
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Unfiltered: CRITICAL first, then HIGH, MEDIUM, LOW
            res = await client.get("/api/risk")
            assert res.status_code == 200
            data = res.json()
            assert data["tick"] == 40
            rows = data["rows"]
            assert len(rows) == 4
            assert rows[0]["risk_level"] == "CRITICAL"
            assert rows[1]["risk_level"] == "HIGH"
            assert rows[2]["risk_level"] == "MEDIUM"
            assert rows[3]["risk_level"] == "LOW"

            # 2. Filter by fuel=DIESEL
            res_fuel = await client.get("/api/risk?fuel=DIESEL")
            assert res_fuel.status_code == 200
            rows_fuel = res_fuel.json()["rows"]
            assert len(rows_fuel) == 2
            assert all(r["fuel_type"] == "DIESEL" for r in rows_fuel)

            # 3. Filter by min_level=HIGH (only CRITICAL and HIGH)
            res_min = await client.get("/api/risk?min_level=HIGH")
            assert res_min.status_code == 200
            rows_min = res_min.json()["rows"]
            assert len(rows_min) == 2
            assert set(r["risk_level"] for r in rows_min) == {"CRITICAL", "HIGH"}

            # 4. Filter by region_id=dhaka
            res_reg = await client.get("/api/risk?region_id=dhaka")
            assert res_reg.status_code == 200
            rows_reg = res_reg.json()["rows"]
            assert len(rows_reg) == 2
            assert all(r["region_id"] == "dhaka" for r in rows_reg)
    finally:
        app.dependency_overrides.pop(get_session, None)


@pytest.mark.asyncio
async def test_get_alerts_and_ack(auth_headers):
    """Verify /api/alerts returns alert list and /api/alerts/{id}/ack updates alert."""
    mock_session = AsyncMock()

    mock_alert = {
        "id": 10,
        "sim_run_id": 1,
        "fingerprint": "fp-test",
        "category": "STOCKOUT_RISK",
        "severity": "CRITICAL",
        "title": "Low Diesel",
        "message": "Immediate refill required",
        "entity_type": "station",
        "entity_id": "station-dhaka",
        "fuel_type": "DIESEL",
        "status": "OPEN",
        "opened_tick": 30,
        "resolved_tick": None,
        "opened_at": "2026-01-01T08:00:00Z",
        "resolved_at": None,
        "acknowledged_by": None,
        "data": {},
        "related_decision_id": None,
    }

    with (
        patch("app.routers.risk_alerts.fetch_alerts", new_callable=AsyncMock) as mock_fetch,
        patch("app.routers.risk_alerts.ack_alert", new_callable=AsyncMock) as mock_ack,
    ):
        mock_fetch.return_value = ([mock_alert], 1)
        mock_ack.return_value = {**mock_alert, "status": "ACKNOWLEDGED", "acknowledged_by": "operator"}

        async def override_get_session():
            yield mock_session

        app.dependency_overrides[get_session] = override_get_session
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                # 1. GET /api/alerts
                res = await client.get("/api/alerts?status=OPEN")
                assert res.status_code == 200
                data = res.json()
                assert data["total"] == 1
                assert len(data["items"]) == 1
                assert data["items"][0]["status"] == "OPEN"

                # 2. POST /api/alerts/10/ack without auth -> 401
                res_unauth = await client.post("/api/alerts/10/ack")
                assert res_unauth.status_code == 401

                # 3. POST /api/alerts/10/ack with auth -> 200
                res_ack = await client.post("/api/alerts/10/ack", headers=auth_headers)
                assert res_ack.status_code == 200
                ack_data = res_ack.json()
                assert ack_data["status"] == "ACKNOWLEDGED"
                assert ack_data["acknowledged_by"] == "operator"

                # 4. POST /api/alerts/999/ack not found -> 404
                mock_ack.return_value = None
                res_404 = await client.post("/api/alerts/999/ack", headers=auth_headers)
                assert res_404.status_code == 404
                assert res_404.json()["detail"]["code"] == "ALERT_NOT_FOUND"
        finally:
            app.dependency_overrides.pop(get_session, None)


@pytest.mark.asyncio
async def test_get_forecast():
    """Verify /api/forecast returns ForecastSeries format."""
    mock_session = AsyncMock()
    mock_series = {
        "station_id": "station-dhaka",
        "fuel_type": "DIESEL",
        "model": "seasonal@builtin",
        "is_fallback": False,
        "history": [{"tick": 38, "demand": 450.0}, {"tick": 39, "demand": 470.0}],
        "points": [
            {"tick": 40, "p10": 420.0, "p50": 460.0, "p90": 510.0},
            {"tick": 41, "p10": 430.0, "p50": 475.0, "p90": 525.0},
        ],
        "coverage_80": 0.80,
    }

    with patch("app.routers.risk_alerts.fetch_forecast_series", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = mock_series

        async def override_get_session():
            yield mock_session

        app.dependency_overrides[get_session] = override_get_session
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                res = await client.get("/api/forecast?station_id=station-dhaka&fuel=DIESEL")
                assert res.status_code == 200
                data = res.json()
                assert data["station_id"] == "station-dhaka"
                assert data["fuel_type"] == "DIESEL"
                assert data["model"] == "seasonal@builtin"
                assert len(data["points"]) == 2
                assert data["points"][0]["p50"] == 460.0
        finally:
            app.dependency_overrides.pop(get_session, None)


@pytest.mark.asyncio
async def test_get_cover(sample_world_state):
    """Verify /api/cover returns network and regional days-of-cover and scarcity flags."""
    mock_redis = MagicMock()
    mock_redis.get = AsyncMock(return_value=orjson.dumps(sample_world_state).decode())
    app.state.redis = mock_redis

    mock_session = AsyncMock()

    async def override_get_session():
        yield mock_session

    app.dependency_overrides[get_session] = override_get_session
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.get("/api/cover")
            assert res.status_code == 200
            data = res.json()
            assert "network" in data
            assert "regions" in data
            assert data["supply_ends_tick"] == 212
            assert "scarcity" in data
            assert "DIESEL" in data["network"]
            assert data["network"]["DIESEL"]["available"] > 0
            assert data["network"]["DIESEL"]["cover_days"] > 0
    finally:
        app.dependency_overrides.pop(get_session, None)


@pytest.mark.asyncio
async def test_get_incidents():
    """Verify /api/incidents returns incident list."""
    mock_session = AsyncMock()
    mock_incidents = [
        {
            "id": 1,
            "kind": "component_down",
            "component": "intelligence",
            "status": "OPEN",
            "started_at": "2026-01-01T04:00:00Z",
            "mitigated_at": None,
            "resolved_at": None,
            "summary": "Intelligence service unreachable",
            "timeline": [],
        }
    ]

    with patch("app.routers.risk_alerts.fetch_incidents", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = mock_incidents

        async def override_get_session():
            yield mock_session

        app.dependency_overrides[get_session] = override_get_session
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                res = await client.get("/api/incidents")
                assert res.status_code == 200
                data = res.json()
                assert "items" in data
                assert len(data["items"]) == 1
                assert data["items"][0]["component"] == "intelligence"
        finally:
            app.dependency_overrides.pop(get_session, None)
