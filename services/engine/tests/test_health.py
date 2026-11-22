"""Engine health endpoint tests for Phase 01."""
import time

from app.config import get_settings
from app.main import app
from app.state.store import WorldState
from fastapi.testclient import TestClient
from fsip_common.models import Instance


def test_live():
    client = TestClient(app)
    assert client.get("/health/live").json() == {"status": "ok", "service": "engine"}


def test_ready_ok_and_fail():
    store = WorldState(engine_mode="NORMAL")
    store.instance = Instance(
        id=1,
        scenario_id="baseline",
        scenario_version="1.0.0",
        seed=9001,
        sim_time="2026-03-01T00:00:00Z",
        tick=1,
        tick_minutes=15,
        status="RUNNING",
    )
    store.last_good_at["instance"] = time.time()

    app.state.store = store
    client = TestClient(app)
    r = client.get("/health/ready")
    assert r.status_code == 200
    assert r.json()["status"] == "ready"

    # Not synced yet -> 503
    empty_store = WorldState()
    app.state.store = empty_store
    r = client.get("/health/ready")
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "NOT_READY"


def test_debug_requires_token():
    client = TestClient(app)
    assert client.get("/debug/state").status_code == 401
    token = get_settings().engine_internal_token
    store = WorldState()
    store.instance = Instance(
        id=1,
        scenario_id="baseline",
        scenario_version="1.0.0",
        seed=9001,
        sim_time="2026-03-01T00:00:00Z",
        tick=1,
        tick_minutes=15,
        status="RUNNING",
    )
    app.state.store = store
    r = client.get("/debug/state", headers={"X-Internal-Token": token})
    assert r.status_code == 200
