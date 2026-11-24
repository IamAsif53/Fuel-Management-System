"""Unit tests for the Engine Lifespan, Supervision, and Endpoints (Task 1.8)."""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.config import get_settings
from app.main import (
    FSIP_LOOP_RESTARTS,
    _supervised_runner,
    app,
)
from app.state.store import WorldState
from fastapi.testclient import TestClient
from fsip_common.models import Instance


# =============================================================================
# 1. Lifespan starts both background tasks
# =============================================================================
@pytest.mark.asyncio
async def test_lifespan_starts_both_background_tasks():
    with (
        patch("app.main.SimClient") as mock_client_cls,
        patch("app.main.run_migrations", new_callable=AsyncMock) as mock_run_migrations,
    ):
        mock_client = AsyncMock()
        mock_client_cls.return_value = mock_client

        with patch("app.main.SyncLoop") as mock_sync_cls, patch("app.main.SSEListener") as mock_sse_cls:
            mock_sync = AsyncMock()
            mock_sync.stop = MagicMock()
            mock_sync.add_hook = MagicMock()
            mock_sync_cls.return_value = mock_sync
            mock_sse = AsyncMock()
            mock_sse.stop = MagicMock()
            mock_sse_cls.return_value = mock_sse

            with TestClient(app) as client:
                mock_run_migrations.assert_awaited()
                assert hasattr(app.state, "sync_loop")
                assert hasattr(app.state, "sse_listener")
                assert len(app.state.tasks) == 2
                assert app.state.tasks[0].get_name() == "supervised-sync-loop"
                assert app.state.tasks[1].get_name() == "supervised-sse-listener"
                # Initial sync was called
                mock_sync.full_sync.assert_awaited()

                # Live endpoint works
                resp = client.get("/health/live")
                assert resp.status_code == 200


# =============================================================================
# 2. Supervised restart: SyncLoop crash
# =============================================================================
@pytest.mark.asyncio
async def test_supervised_restart_sync_loop_on_crash():
    stop_event = asyncio.Event()
    call_count = 0

    async def flaky_sync_loop():
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            raise RuntimeError("Sync loop sudden crash")
        # Run until stopped
        await stop_event.wait()

    task = asyncio.create_task(
        _supervised_runner("sync", flaky_sync_loop, stop_event, initial_backoff=0.01, max_backoff=0.05)
    )

    t_deadline = time.monotonic() + 1.5
    while time.monotonic() < t_deadline and call_count < 3:
        await asyncio.sleep(0.01)

    stop_event.set()
    await task

    # Task crashed twice and was restarted
    assert call_count >= 3


# =============================================================================
# 3. Supervised restart: SSEListener crash
# =============================================================================
@pytest.mark.asyncio
async def test_supervised_restart_sse_listener_on_crash():
    stop_event = asyncio.Event()
    call_count = 0

    async def flaky_sse_listener():
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            raise RuntimeError("SSE listener connection crash")
        await stop_event.wait()

    task = asyncio.create_task(
        _supervised_runner("sse", flaky_sse_listener, stop_event, initial_backoff=0.01, max_backoff=0.05)
    )

    t_deadline = time.monotonic() + 1.5
    while time.monotonic() < t_deadline and call_count < 3:
        await asyncio.sleep(0.01)

    stop_event.set()
    await task

    assert call_count >= 3


# =============================================================================
# 4. Crash in one task does not affect the other
# =============================================================================
@pytest.mark.asyncio
async def test_crash_in_one_task_does_not_affect_the_other():
    stop_event = asyncio.Event()
    sync_crashes = 0
    sse_still_running = True

    async def crashing_sync():
        nonlocal sync_crashes
        sync_crashes += 1
        raise RuntimeError("Sync crashed!")

    async def steady_sse():
        nonlocal sse_still_running
        try:
            await stop_event.wait()
        finally:
            sse_still_running = False

    t_sync = asyncio.create_task(
        _supervised_runner("sync", crashing_sync, stop_event, initial_backoff=0.01)
    )
    t_sse = asyncio.create_task(
        _supervised_runner("sse", steady_sse, stop_event, initial_backoff=0.01)
    )

    t_deadline = time.monotonic() + 1.5
    while time.monotonic() < t_deadline and sync_crashes < 2:
        await asyncio.sleep(0.01)

    # Sync crashed multiple times
    assert sync_crashes >= 2
    # SSE remained steadily running without being cancelled
    assert not t_sse.done()

    stop_event.set()
    await asyncio.gather(t_sync, t_sse, return_exceptions=True)


# =============================================================================
# 5. Shutdown cancels and awaits both tasks cleanly
# =============================================================================
@pytest.mark.asyncio
async def test_shutdown_cancels_and_awaits_both_tasks():
    with (
        patch("app.main.SimClient") as mock_client_cls,
        patch("app.main.run_migrations", new_callable=AsyncMock) as mock_run_migrations,
    ):
        mock_client = AsyncMock()
        mock_client_cls.return_value = mock_client

        with patch("app.main.SyncLoop") as mock_sync_cls, patch("app.main.SSEListener") as mock_sse_cls:
            mock_sync = AsyncMock()
            mock_sync.stop = MagicMock()
            mock_sync.add_hook = MagicMock()
            mock_sync_cls.return_value = mock_sync
            mock_sse = AsyncMock()
            mock_sse.stop = MagicMock()
            mock_sse_cls.return_value = mock_sse

            with TestClient(app):
                mock_run_migrations.assert_awaited()
                tasks = list(app.state.tasks)
                assert len(tasks) == 2
                assert not any(t.done() for t in tasks)

            # After context manager exits:
            assert all(t.done() for t in tasks)
            mock_sync.stop.assert_called_once()
            mock_sse.stop.assert_called_once()
            mock_client.close.assert_awaited_once()


# =============================================================================
# 6. Restart metrics increment correctly
# =============================================================================
@pytest.mark.asyncio
async def test_restart_metrics_increment():
    stop_event = asyncio.Event()
    call_count = 0

    assert FSIP_LOOP_RESTARTS is not None
    initial_val = FSIP_LOOP_RESTARTS.labels(loop="test_loop")._value.get()

    async def crashing_loop():
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            raise RuntimeError("Crash!")
        await stop_event.wait()

    task = asyncio.create_task(
        _supervised_runner("test_loop", crashing_loop, stop_event, initial_backoff=0.01)
    )

    t_deadline = time.monotonic() + 1.5
    while time.monotonic() < t_deadline and call_count < 3:
        await asyncio.sleep(0.01)

    stop_event.set()
    await task

    final_val = FSIP_LOOP_RESTARTS.labels(loop="test_loop")._value.get()
    assert final_val >= initial_val + 2


# =============================================================================
# 7. GET /health/live returns HTTP 200
# =============================================================================
def test_health_live_endpoint():
    client = TestClient(app)
    resp = client.get("/health/live")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "service": "engine"}


# =============================================================================
# 8. GET /health/ready returns ready when recent sync succeeded
# =============================================================================
def test_health_ready_recent_sync():
    store = WorldState(engine_mode="NORMAL")
    store.instance = Instance(
        id=1,
        scenario_id="baseline",
        scenario_version="1.0.0",
        seed=9001,
        sim_time="2026-03-01T00:00:00Z",
        tick=5,
        tick_minutes=15,
        status="RUNNING",
    )
    store.last_good_at["instance"] = time.time() - 2.0  # 2 seconds ago (< 30s)

    app.state.store = store
    client = TestClient(app)
    resp = client.get("/health/ready")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ready"
    assert data["last_sync_age_s"] < 30.0
    assert data["engine_mode"] == "NORMAL"


# =============================================================================
# 9. GET /health/ready returns degraded when cached state is degraded or old
# =============================================================================
def test_health_ready_degraded_cached_state():
    store = WorldState(engine_mode="NORMAL")
    store.instance = Instance(
        id=1,
        scenario_id="baseline",
        scenario_version="1.0.0",
        seed=9001,
        sim_time="2026-03-01T00:00:00Z",
        tick=5,
        tick_minutes=15,
        status="RUNNING",
    )
    # Sync is 45s old (> 30s)
    store.last_good_at["instance"] = time.time() - 45.0
    store.degraded_reasons.add("sim_unavailable")

    app.state.store = store
    client = TestClient(app)
    resp = client.get("/health/ready")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "degraded"
    assert data["last_sync_age_s"] >= 45.0
    assert data["engine_mode"] == "NORMAL"


# =============================================================================
# 10. GET /health/ready returns HTTP 503 when never synced
# =============================================================================
def test_health_ready_503_when_never_synced():
    store = WorldState()
    assert store.instance is None
    assert len(store.last_good_at) == 0

    app.state.store = store
    client = TestClient(app)
    resp = client.get("/health/ready")
    assert resp.status_code == 503
    data = resp.json()
    assert data["detail"]["code"] == "NOT_READY"


# =============================================================================
# 11. GET /metrics exposes required metrics
# =============================================================================
def test_metrics_endpoint_exposes_metrics():
    client = TestClient(app)
    resp = client.get("/metrics")
    assert resp.status_code == 200
    text = resp.text
    assert "fsip_engine_info" in text
    assert "fsip_sse_connected" in text
    assert "fsip_sse_reconnects_total" in text
    assert "fsip_cycle_duration_seconds" in text
    assert "fsip_ticks_behind" in text


# =============================================================================
# 12. GET /debug/state rejects missing and invalid token
# =============================================================================
def test_debug_state_authentication():
    client = TestClient(app)
    # Missing token
    resp1 = client.get("/debug/state")
    assert resp1.status_code == 401
    assert resp1.json()["detail"]["code"] == "UNAUTHORIZED"

    # Invalid token
    resp2 = client.get("/debug/state", headers={"X-Internal-Token": "bad-token"})
    assert resp2.status_code == 401
    assert resp2.json()["detail"]["code"] == "UNAUTHORIZED"


# =============================================================================
# 13. GET /debug/state returns public JSON projection for valid token
# =============================================================================
def test_debug_state_valid_token():
    store = WorldState(sim_run_id=5, engine_mode="NORMAL")
    store.instance = Instance(
        id=1,
        scenario_id="baseline",
        scenario_version="1.0.0",
        seed=9001,
        sim_time="2026-03-01T00:00:00Z",
        tick=12,
        tick_minutes=15,
        status="RUNNING",
    )

    settings = get_settings()
    token = settings.engine_internal_token

    app.state.store = store
    client = TestClient(app)
    resp = client.get("/debug/state", headers={"X-Internal-Token": token})
    assert resp.status_code == 200
    data = resp.json()
    assert data["sim_run_id"] == 5
    assert data["tick"] == 12
    assert data["engine_mode"] == "NORMAL"
    assert "depots" in data
    assert "stations" in data
    assert "routes" in data
