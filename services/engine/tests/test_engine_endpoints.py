"""Tests for Engine control and decision internal endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from app.db.models import Decision, DecisionLine, SimRun
from app.db.session import check_db_health, resolve_database_url
from app.decision.engine import DecisionEngine
from app.decision.executor import Executor
from app.main import app
from app.sim.client import SimResult
from app.state.store import WorldState
from fsip_common.models import Depot, Instance, Route, Station
from sqlalchemy import NullPool, delete, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from starlette.testclient import TestClient

pytestmark = [pytest.mark.asyncio]

DB_TEST_URL = resolve_database_url()
INTERNAL_HEADERS = {"x-internal-token": "change-me-internal"}


@pytest_asyncio.fixture
async def db_engine():
    engine = create_async_engine(DB_TEST_URL, poolclass=NullPool)
    is_healthy = await check_db_health(engine)
    if not is_healthy:
        pytest.skip(f"Database at {DB_TEST_URL} is not reachable.")
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def session_factory(db_engine: AsyncEngine):
    return async_sessionmaker(bind=db_engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture
async def test_sim_run_id(session_factory):
    async with session_factory() as session:
        run = SimRun(
            scenario_id="endpoints-test-run",
            tick_minutes=15,
            first_tick=0,
        )
        session.add(run)
        await session.commit()
        run_id = run.id

    yield run_id

    async with session_factory() as session:
        decs = (await session.execute(select(Decision.id).where(Decision.sim_run_id == run_id))).scalars().all()
        if decs:
            await session.execute(delete(DecisionLine).where(DecisionLine.decision_id.in_(decs)))
        await session.execute(delete(Decision).where(Decision.sim_run_id == run_id))
        await session.execute(delete(SimRun).where(SimRun.id == run_id))
        await session.commit()


@pytest.fixture
def test_world_state(test_sim_run_id: int) -> WorldState:
    ws = WorldState()
    ws.sim_run_id = test_sim_run_id
    ws.instance = Instance(
        id=1,
        scenario_id="endpoints-test",
        scenario_version="1.0",
        seed=42,
        sim_time=datetime.now(UTC),
        tick=10,
        tick_minutes=15,
        status="RUNNING",
    )
    ws.operating_mode = "advisory"
    ws.policy = "heuristic"

    # Add dummy station, depot, route
    st = Station(
        id="station-mirpur",
        name="Mirpur",
        region_id="dhaka",
        status="OPEN",
        demand_profile="urban_commercial",
        demand_multiplier=1.0,
        capacity={"DIESEL": 10000.0, "PETROL": 8000.0, "OCTANE": 5000.0},
        inventory={"DIESEL": 2000.0, "PETROL": 3000.0, "OCTANE": 2000.0},
    )
    depot = Depot(
        id="depot-gazipur",
        name="Gazipur",
        region_id="dhaka",
        status="OPEN",
        capacity={"DIESEL": 50000.0, "PETROL": 40000.0, "OCTANE": 30000.0},
        inventory={"DIESEL": 30000.0, "PETROL": 25000.0, "OCTANE": 20000.0},
        dispatch_capacity_per_tick=10000.0,
    )
    route = Route(
        id="route-gazipur-mirpur",
        source_depot_id="depot-gazipur",
        destination_station_id="station-mirpur",
        transit_ticks=2,
        max_shipment=5000.0,
        status="AVAILABLE",
    )
    ws.stations = [st]
    ws.depots = [depot]
    ws.routes = [route]
    return ws


@pytest.fixture
def client_app(session_factory, test_world_state: WorldState):
    mock_sim = AsyncMock()
    mock_sim.instance.return_value = SimResult(
        data={"id": 1, "tick": 10, "status": "RUNNING"},
        stale=False,
        status=200,
        latency_ms=5.0,
    )
    mock_sim.create_allocation.return_value = SimResult(
        data={"id": 901, "status": "PENDING"},
        stale=False,
        status=201,
        latency_ms=10.0,
    )

    executor = Executor(sim_client=mock_sim, session_factory=session_factory)
    decision_engine = DecisionEngine(session_factory=session_factory, executor=executor)

    mock_sync = AsyncMock()
    mock_sync.stop = MagicMock()
    mock_sync.add_hook = MagicMock()
    mock_sync._evaluate_mode = MagicMock(return_value="NORMAL")
    mock_sse = AsyncMock()
    mock_sse.stop = MagicMock()

    with TestClient(app) as test_client:
        app.state.sync_loop = mock_sync
        app.state.sse_listener = mock_sse
        app.state.client = mock_sim
        app.state.store = test_world_state
        app.state.decision_engine = decision_engine
        app.state.executor = executor
        yield test_client


async def test_auth_enforcement(client_app: TestClient):
    # Missing token -> 401
    resp = client_app.post("/engine/control/mode", json={"mode": "autopilot"})
    assert resp.status_code == 401

    # Bad token -> 401
    resp = client_app.post(
        "/engine/control/mode",
        headers={"x-internal-token": "wrong"},
        json={"mode": "autopilot"},
    )
    assert resp.status_code == 401


async def test_control_mode_and_policy(client_app: TestClient):
    # Mode switch
    resp = client_app.post(
        "/engine/control/mode",
        headers=INTERNAL_HEADERS,
        json={"mode": "autopilot"},
    )
    assert resp.status_code == 200, resp.json()
    assert resp.json()["mode"] == "autopilot"

    # Bad mode -> 400
    resp = client_app.post(
        "/engine/control/mode",
        headers=INTERNAL_HEADERS,
        json={"mode": "invalid"},
    )
    assert resp.status_code == 400

    # Policy switch
    resp = client_app.post(
        "/engine/control/policy",
        headers=INTERNAL_HEADERS,
        json={"policy": "heuristic"},
    )
    assert resp.status_code == 200, resp.json()
    assert resp.json()["policy"] == "heuristic"


async def test_plan_now(client_app: TestClient):
    resp = client_app.post("/engine/plan-now", headers=INTERNAL_HEADERS)
    assert resp.status_code == 200, resp.json()
    data = resp.json()
    assert data["status"] in ("ok", "no_op")


async def test_approve_and_reject_decision(
    client_app: TestClient,
    session_factory,
    test_sim_run_id: int,
):
    async with session_factory() as session:
        dec = Decision(
            sim_run_id=test_sim_run_id,
            tick=10,
            policy="heuristic",
            status="PENDING_APPROVAL",
        )
        session.add(dec)
        await session.flush()

        line = DecisionLine(
            decision_id=dec.id,
            line_no=1,
            source_depot_id="depot-gazipur",
            destination_station_id="station-mirpur",
            route_id="route-gazipur-mirpur",
            fuel_type="DIESEL",
            quantity=2000.0,
            idempotency_key=f"fsip-endp-{dec.id}-1",
            status="PROPOSED",
        )
        session.add(line)
        await session.commit()
        dec_id = dec.id

    # Approve
    resp = client_app.post(
        f"/engine/decisions/{dec_id}/approve",
        headers=INTERNAL_HEADERS,
        json={"operator": "lead_op"},
    )
    assert resp.status_code == 200, resp.json()
    assert resp.json()["status"] == "ok"
    assert resp.json()["decision_status"] == "EXECUTED"

    # 404 on nonexistent
    resp = client_app.post(
        "/engine/decisions/999999/approve",
        headers=INTERNAL_HEADERS,
        json={"operator": "lead_op"},
    )
    assert resp.status_code == 404


async def test_approve_decision_partial_lines_and_constraint_422(
    client_app: TestClient,
    session_factory,
    test_sim_run_id: int,
):
    """Verify DEF-002: line subset & modified_quantities work and constraint violation returns 422 (not 500)."""
    async with session_factory() as session:
        dec = Decision(
            sim_run_id=test_sim_run_id,
            tick=10,
            policy="optimizer",
            status="PENDING_APPROVAL",
            confidence=0.95,
        )
        session.add(dec)
        await session.flush()

        ln1 = DecisionLine(
            decision_id=dec.id,
            line_no=1,
            source_depot_id="depot-gazipur",
            destination_station_id="station-mirpur",
            route_id="route-gazipur-mirpur",
            fuel_type="DIESEL",
            quantity=6400.0,
            idempotency_key=f"fsip-def002-{dec.id}-1",
            status="PLANNED",
        )
        ln2 = DecisionLine(
            decision_id=dec.id,
            line_no=2,
            source_depot_id="depot-gazipur",
            destination_station_id="station-mirpur",
            route_id="route-gazipur-mirpur",
            fuel_type="PETROL",
            quantity=3000.0,
            idempotency_key=f"fsip-def002-{dec.id}-2",
            status="PLANNED",
        )
        session.add_all([ln1, ln2])
        await session.commit()
        dec_id, ln1_id, ln2_id = dec.id, ln1.id, ln2.id

    # 1. Quantity violating route max_shipment (50000L) must return 422 CONSTRAINT_VIOLATION, NOT 500
    bad_resp = client_app.post(
        f"/engine/decisions/{dec_id}/approve",
        headers=INTERNAL_HEADERS,
        json={
            "operator": "lead_op",
            "selected_line_ids": [ln1_id],
            "modified_quantities": {str(ln1_id): 50000.0},
            "reviewed_driver": True,
        },
    )
    assert bad_resp.status_code == 422, bad_resp.text
    assert bad_resp.json()["detail"]["code"] == "CONSTRAINT_VIOLATION"
    assert len(bad_resp.json()["detail"]["violations"]) >= 1

    # 2. Invalid quantity (<= 0) returns 422 INVALID_QUANTITY
    zero_resp = client_app.post(
        f"/engine/decisions/{dec_id}/approve",
        headers=INTERNAL_HEADERS,
        json={
            "operator": "lead_op",
            "selected_line_ids": [ln1_id],
            "modified_quantities": {str(ln1_id): -10.0},
            "reviewed_driver": True,
        },
    )
    assert zero_resp.status_code == 422
    assert zero_resp.json()["detail"]["code"] == "INVALID_QUANTITY"

    # 3. Unknown line ID returns 422 INVALID_LINE_SELECTION
    unk_resp = client_app.post(
        f"/engine/decisions/{dec_id}/approve",
        headers=INTERNAL_HEADERS,
        json={
            "operator": "lead_op",
            "selected_line_ids": [999999],
            "reviewed_driver": True,
        },
    )
    assert unk_resp.status_code == 422
    assert unk_resp.json()["detail"]["code"] == "INVALID_LINE_SELECTION"

    # 4. Valid subset + reduction below 7000 L bypasses Four-Eyes cleanly and cancels unselected lines
    ok_resp = client_app.post(
        f"/engine/decisions/{dec_id}/approve",
        headers=INTERNAL_HEADERS,
        json={
            "operator": "lead_op",
            "selected_line_ids": [ln1_id],
            "modified_quantities": {str(ln1_id): 1200.0},
            "reviewed_driver": True,
        },
    )
    assert ok_resp.status_code == 200, ok_resp.text
    assert ok_resp.json()["decision_status"] == "EXECUTED"
    assert ok_resp.json()["approved_by"] == "lead_op"

    async with session_factory() as session:
        refreshed = (
            await session.execute(select(DecisionLine).where(DecisionLine.decision_id == dec_id))
        ).scalars().all()
        by_id = {ln.id: ln for ln in refreshed}
        assert by_id[ln1_id].quantity == 1200.0
        assert by_id[ln1_id].status == "SUBMITTED"
        assert by_id[ln2_id].status == "CANCELLED"


async def test_control_mode_autopilot_live_probe_rejects_paused_stale_data(client_app: TestClient):
    """Verify DEF-003: enabling autopilot probes simulator freshness immediately and rejects 409 STALE_DATA."""
    mock_sim_probe = AsyncMock()
    mock_sim_probe.instance.return_value = SimResult(
        data={"id": 1, "tick": 12, "status": "PAUSED"},
        stale=True,
        status=200,
        latency_ms=4.0,
    )
    old_client = getattr(app.state, "client", None)
    app.state.client = mock_sim_probe
    app.state.store.stale = False
    app.state.store.degraded_reasons.clear()
    app.state.store.engine_mode = "NORMAL"

    try:
        resp = client_app.post(
            "/engine/control/mode",
            headers=INTERNAL_HEADERS,
            json={"mode": "autopilot"},
        )
        assert resp.status_code == 409, resp.text
        assert resp.json()["detail"]["code"] == "STALE_DATA"
        assert app.state.store.stale is True
    finally:
        app.state.client = old_client
        app.state.store.stale = False
        app.state.store.degraded_reasons.clear()
        app.state.store.engine_mode = "NORMAL"


async def test_control_mode_autopilot_allows_soft_error_budget(client_app: TestClient):
    """Soft SLO budget exhaustion must not block entering Autopilot when otherwise healthy."""
    mock_sim_probe = AsyncMock()
    mock_sim_probe.instance.return_value = SimResult(
        data={"id": 1, "tick": 12, "status": "RUNNING"},
        stale=False,
        status=200,
        latency_ms=3.0,
    )
    old_client = getattr(app.state, "client", None)
    old_mode = getattr(app.state.store, "operating_mode", "advisory")
    app.state.client = mock_sim_probe
    app.state.store.stale = False
    app.state.store.degraded_reasons = {"error_budget_exhausted"}
    app.state.store.engine_mode = "NORMAL"
    app.state.store.operating_mode = "advisory"

    try:
        resp = client_app.post(
            "/engine/control/mode",
            headers=INTERNAL_HEADERS,
            json={"mode": "autopilot"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json().get("operating_mode") == "autopilot" or resp.json().get("mode") == "autopilot"
        assert app.state.store.operating_mode == "autopilot"
    finally:
        app.state.client = old_client
        app.state.store.stale = False
        app.state.store.degraded_reasons.clear()
        app.state.store.engine_mode = "NORMAL"
        app.state.store.operating_mode = old_mode
        client_app.post(
            "/engine/control/mode",
            headers=INTERNAL_HEADERS,
            json={"mode": "advisory"},
        )


async def test_manual_allocation_valid_and_invalid(client_app: TestClient):
    # Valid manual allocation
    resp = client_app.post(
        "/engine/manual-allocation",
        headers=INTERNAL_HEADERS,
        json={
            "source_depot_id": "depot-gazipur",
            "destination_station_id": "station-mirpur",
            "route_id": "route-gazipur-mirpur",
            "fuel_type": "DIESEL",
            "quantity": 2500.0,
            "reason": "Emergency top-up",
        },
    )
    assert resp.status_code == 200, resp.json()
    data = resp.json()
    assert data["status"] == "ok"
    assert data["decision_id"] is not None
    assert data["execution_status"] == "EXECUTED"

    # Invalid manual allocation (exceeds route max shipment of 5000)
    resp = client_app.post(
        "/engine/manual-allocation",
        headers=INTERNAL_HEADERS,
        json={
            "source_depot_id": "depot-gazipur",
            "destination_station_id": "station-mirpur",
            "route_id": "route-gazipur-mirpur",
            "fuel_type": "DIESEL",
            "quantity": 10000.0,
            "reason": "Exceeds max shipment",
        },
    )
    print("INVALID RESP:", resp.status_code, resp.text)
    assert resp.status_code == 422
    err = resp.json()["detail"]
    assert err["code"] == "CONSTRAINT_VIOLATION"
