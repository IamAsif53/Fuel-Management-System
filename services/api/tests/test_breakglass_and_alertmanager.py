"""Unit tests for Phase 10 breakglass CSV and Phase 11 alertmanager webhook in API."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from app.db import get_session
from app.main import app
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def mock_session():
    session = AsyncMock()
    mock_res = MagicMock()
    mock_res.scalars.return_value.all.return_value = []
    session.execute.return_value = mock_res
    return session


@pytest.mark.asyncio
async def test_breakglass_plan_csv(mock_session):
    app.dependency_overrides[get_session] = lambda: mock_session
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/breakglass/plan.csv")
            assert resp.status_code == 200
            assert "text/csv" in resp.headers.get("content-type", "")
            assert "depot,station,route,fuel,quantity,reason,generated_tick" in resp.text
    finally:
        app.dependency_overrides.pop(get_session, None)


@pytest.mark.asyncio
async def test_alertmanager_webhook():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        payload = {
            "alerts": [
                {
                    "status": "firing",
                    "labels": {"alertname": "SimulatorDown", "severity": "critical"},
                    "annotations": {"summary": "Simulator circuit breaker tripped OPEN"},
                }
            ]
        }
        resp = await client.post(
            "/api/internal/alertmanager",
            json=payload,
            headers={"Authorization": "Bearer change-me-internal"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("status") == "ok"
        assert data.get("processed") == 1
