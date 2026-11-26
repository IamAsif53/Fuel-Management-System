"""Contract regression tests verifying DEF-007b simulator preset and chaos keys."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from app.auth import create_access_token
from app.config import get_settings
from app.main import app
from app.routers.control import SUPPORTED_PRESETS


def _op_headers() -> dict[str, str]:
    tok, _ = create_access_token("operator", "operator", get_settings())
    return {"Authorization": f"Bearer {tok}"}


def test_presets_use_exact_simulator_parameter_keys():
    """Verify presets use canonical region_ids, depot_ids, and factor keys expected by the simulator."""
    dhaka_spike = SUPPORTED_PRESETS["demand_spike_dhaka"]["events"][0]["parameters"]
    assert dhaka_spike == {"multiplier": 1.8, "region_ids": ["region-dhaka"]}
    assert "region" not in dhaka_spike

    patiya = SUPPORTED_PRESETS["depot_constraint_patiya"]["events"][0]["parameters"]
    assert patiya == {"depot_ids": ["depot-patiya"]}
    assert "depot_id" not in patiya

    gazipur_delay = SUPPORTED_PRESETS["shipment_delay_gazipur"]["events"][0]["parameters"]
    assert gazipur_delay == {"depot_ids": ["depot-gazipur"], "delay_ticks": 8}
    assert "depot_id" not in gazipur_delay

    shortfall = SUPPORTED_PRESETS["supply_shortfall"]["events"][0]["parameters"]
    assert shortfall == {"factor": 0.5}
    assert "multiplier" not in shortfall

    combined = SUPPORTED_PRESETS["combined_crisis"]["events"]
    assert combined[0]["parameters"] == {"multiplier": 1.8, "region_ids": ["region-dhaka"]}
    assert combined[2]["parameters"] == {"depot_ids": ["depot-gazipur"], "delay_ticks": 8}


@pytest.mark.asyncio
async def test_chaos_latency_uses_delay_ms_key():
    """Verify POST /api/control/chaos/latency forwards delay_ms=1500 (not latency_ms) to simulator."""
    with (
        patch("app.routers.control._call_simulator", new_callable=AsyncMock) as mock_sim,
        patch("app.routers.control._record_audit", new_callable=AsyncMock),
    ):
        mock_sim.return_value = {"id": 1, "type": "latency"}
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.post(
                "/api/control/chaos/latency",
                headers=_op_headers(),
                json={"duration_seconds": 30},
            )
            assert res.status_code == 200
            sent = mock_sim.await_args[1]["json_data"]
            assert sent["parameters"] == {"delay_ms": 1500}
            assert "latency_ms" not in sent["parameters"]

            # Also normalizes explicit legacy latency_ms input -> delay_ms
            res2 = await client.post(
                "/api/control/chaos/latency",
                headers=_op_headers(),
                json={"duration_seconds": 30, "parameters": {"latency_ms": 900}},
            )
            assert res2.status_code == 200
            sent2 = mock_sim.await_args[1]["json_data"]
            assert sent2["parameters"] == {"delay_ms": 900}
