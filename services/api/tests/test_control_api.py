"""Unit tests for Simulator Control, Crisis Presets, and Chaos Drill API endpoints."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from app.config import get_settings
from app.main import app


@pytest.fixture
def auth_headers():
    from app.auth import create_access_token

    settings = get_settings()
    token, _ = create_access_token("operator", "operator", settings)
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_control_status_enabled(auth_headers):
    """GET /api/control/status returns simulator instance state and supported options."""
    mock_inst = {
        "id": 1,
        "scenario_id": "baseline",
        "sim_time": "2026-01-02T00:15:00",
        "tick": 97,
        "status": "PAUSED",
    }
    with patch("app.routers.control._call_simulator", new_callable=AsyncMock) as mock_sim:
        mock_sim.side_effect = lambda req, settings, method, path, **kwargs: (
            mock_inst if path == "/v1/instance" else []
        )
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            res = await client.get("/api/control/status")
            assert res.status_code == 200
            data = res.json()
            assert data["demo_controls_enabled"] is True
            assert data["sim_status"] == "PAUSED"
            assert data["tick"] == 97
            assert len(data["presets"]) == 8
            assert len(data["faults"]) == 5


@pytest.mark.asyncio
async def test_control_sim_actions(auth_headers):
    """Test run, pause, step (x1 and x10), and reset operations."""
    with patch("app.routers.control._call_simulator", new_callable=AsyncMock) as mock_sim, \
         patch("app.routers.control._record_audit", new_callable=AsyncMock):

        # 1. Run
        mock_sim.return_value = {"status": "running", "tick": 97}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            res = await client.post("/api/control/sim/run", headers=auth_headers)
            assert res.status_code == 200
            assert res.json()["action"] == "run"
            assert res.json()["sim"]["status"] == "running"

            # 2. Pause
            mock_sim.return_value = {"status": "paused", "tick": 97}
            res = await client.post("/api/control/sim/pause", headers=auth_headers)
            assert res.status_code == 200
            assert res.json()["action"] == "pause"

            # 3. Step x1
            mock_sim.return_value = {"tick": 98, "sim_time": "2026-01-02T00:30:00"}
            res = await client.post("/api/control/sim/step", headers=auth_headers, json={"count": 1})
            assert res.status_code == 200
            assert res.json()["tick"] == 98
            assert res.json()["count"] == 1

            # 4. Step x10
            mock_sim.return_value = {"tick": 108, "sim_time": "2026-01-02T03:00:00"}
            res = await client.post("/api/control/sim/step", headers=auth_headers, json={"count": 10})
            assert res.status_code == 200
            assert res.json()["count"] == 10

            # 5. Reset
            mock_sim.return_value = {"status": "reset", "tick": 0}
            res = await client.post("/api/control/sim/reset", headers=auth_headers)
            assert res.status_code == 200
            assert res.json()["action"] == "reset"


@pytest.mark.asyncio
async def test_control_unauthorized():
    """Missing bearer returns HTTP 401."""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post("/api/control/sim/run")
        assert res.status_code == 401
        assert res.json()["detail"]["code"] == "UNAUTHORIZED"


@pytest.mark.asyncio
async def test_crisis_presets_all_eight(auth_headers):
    """Test all 8 documented crisis presets inject into simulator events."""
    presets = [
        "demand_spike_dhaka",
        "route_cut_mirpur",
        "depot_constraint_patiya",
        "shipment_delay_gazipur",
        "supply_shortfall",
        "station_outage_tongi",
        "single_route_cut",
        "combined_crisis",
    ]

    with patch("app.routers.control._call_simulator", new_callable=AsyncMock) as mock_sim, \
         patch("app.routers.control._record_audit", new_callable=AsyncMock):

        mock_sim.side_effect = lambda req, settings, method, path, json_data=None, **kwargs: (
            {"tick": 100, "status": "PAUSED"} if method == "GET" else {"id": 42, "status": "SCHEDULED"}
        )

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            for p in presets:
                res = await client.post(f"/api/control/scenario/{p}", headers=auth_headers)
                assert res.status_code == 200, f"Preset {p} failed: {res.text}"
                data = res.json()
                assert data["status"] == "ok"
                assert data["preset"] == p
                assert len(data["events_injected"]) >= 1


@pytest.mark.asyncio
async def test_invalid_preset(auth_headers):
    """Unknown preset returns HTTP 400 INVALID_PRESET."""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post("/api/control/scenario/unknown_apocalypse", headers=auth_headers)
        assert res.status_code == 400
        assert res.json()["detail"]["code"] == "INVALID_PRESET"


@pytest.mark.asyncio
async def test_chaos_fault_accepts_empty_body(auth_headers):
    """Bare POST /control/chaos/{fault} should use duration/parameter defaults (no 422)."""
    with patch("app.routers.control._call_simulator", new_callable=AsyncMock) as mock_sim, \
         patch("app.routers.control._record_audit", new_callable=AsyncMock):
        mock_sim.return_value = {"status": "ok", "type": "latency"}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            res = await client.post("/api/control/chaos/latency", headers=auth_headers)
            assert res.status_code == 200, res.text
            body = res.json()
            assert body["fault"] == "latency"
            assert body["duration_seconds"] == 60
            assert body["parameters"]["delay_ms"] == 1500


@pytest.mark.asyncio
async def test_chaos_faults_and_clear(auth_headers):
    """Test injecting 5 supported fault types and clearing faults."""
    faults = ["unavailable", "error_rate", "latency", "stale_data", "stream_disconnect"]
    with patch("app.routers.control._call_simulator", new_callable=AsyncMock) as mock_sim, \
         patch("app.routers.control._record_audit", new_callable=AsyncMock):

        mock_sim.return_value = {"id": 1, "status": "active"}

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            for f in faults:
                res = await client.post(
                    f"/api/control/chaos/{f}",
                    headers=auth_headers,
                    json={"duration_seconds": 60},
                )
                assert res.status_code == 200, f"Fault {f} failed: {res.text}"
                assert res.json()["fault"] == f

            # Clear
            mock_sim.return_value = {"status": "cleared"}
            res_clear = await client.post("/api/control/chaos/clear", headers=auth_headers)
            assert res_clear.status_code == 200
            assert res_clear.json()["action"] == "clear_faults"


@pytest.mark.asyncio
async def test_invalid_fault_type(auth_headers):
    """Unknown fault type returns HTTP 400 INVALID_FAULT_TYPE."""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post(
            "/api/control/chaos/nuclear_strike",
            headers=auth_headers,
            json={"duration_seconds": 60},
        )
        assert res.status_code == 400
        assert res.json()["detail"]["code"] == "INVALID_FAULT_TYPE"


@pytest.mark.asyncio
async def test_demo_controls_disabled(auth_headers):
    """When DEMO_CONTROLS is disabled, actions return HTTP 403 DEMO_CONTROLS_DISABLED."""
    settings = get_settings()
    with patch.object(settings, "demo_controls", False):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            res = await client.post("/api/control/sim/run", headers=auth_headers)
            assert res.status_code == 403
            assert res.json()["detail"]["code"] == "DEMO_CONTROLS_DISABLED"

            res_status = await client.get("/api/control/status")
            assert res_status.status_code == 200
            assert res_status.json()["demo_controls_enabled"] is False


@pytest.mark.asyncio
async def test_control_logs(auth_headers):
    """GET /api/control/logs returns recent control audit actions."""
    mock_logs = [
        {
            "id": 1,
            "at": "2026-11-25T19:00:00Z",
            "actor": "operator",
            "action": "control.sim.step",
            "payload": {"status": "SUCCESS", "result": "Advanced 1 tick"},
        }
    ]
    with patch("app.routers.control.fetch_audit_logs", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = mock_logs
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            res = await client.get("/api/control/logs")
            assert res.status_code == 200
            data = res.json()
            assert len(data["logs"]) == 1
            assert data["logs"][0]["action"] == "control.sim.step"
