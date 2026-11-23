"""Unit tests for the idempotent allocation Executor."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from app.db.models import Decision, DecisionLine, SimRun
from app.db.session import check_db_health, resolve_database_url
from app.decision.executor import Executor
from app.sim.client import (
    SimDomainError,
    SimFaultInjected,
    SimResult,
    SimUnavailable,
)
from sqlalchemy import NullPool, delete, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

pytestmark = [pytest.mark.asyncio]

DB_TEST_URL = resolve_database_url()


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
            scenario_id="executor-test-run",
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


class MockPublisher:
    def __init__(self):
        self.events: list[dict[str, Any]] = []

    async def event(self, type: str, data: dict, sim_run_id: int | None = None, tick: int | None = None):
        self.events.append({"type": type, "data": data, "sim_run_id": sim_run_id, "tick": tick})
        return "mock-event-id"


async def _create_test_decision_with_lines(
    session_factory,
    sim_run_id: int,
    num_lines: int = 1,
) -> tuple[Decision, list[DecisionLine]]:
    async with session_factory() as session:
        dec = Decision(
            sim_run_id=sim_run_id,
            tick=10,
            policy="heuristic",
            status="APPROVED",
        )
        session.add(dec)
        await session.flush()

        lines = []
        for i in range(1, num_lines + 1):
            line = DecisionLine(
                decision_id=dec.id,
                line_no=i,
                source_depot_id="depot-gazipur",
                destination_station_id="station-mirpur",
                route_id="route-gazipur-mirpur",
                fuel_type="DIESEL",
                quantity=3000.0,
                idempotency_key=f"fsip-test-{dec.id}-{i}",
                status="APPROVED",
                expected_arrival_tick=12,
            )
            session.add(line)
            lines.append(line)

        await session.commit()
        return dec, lines


async def test_execute_all_lines_success(session_factory, test_sim_run_id):
    dec, lines = await _create_test_decision_with_lines(session_factory, test_sim_run_id, num_lines=2)
    mock_sim = AsyncMock()
    mock_sim.create_allocation.side_effect = [
        SimResult(data={"id": 1001, "status": "PENDING"}, stale=False, status=201, latency_ms=10.0),
        SimResult(data={"id": 1002, "status": "PENDING"}, stale=False, status=201, latency_ms=12.0),
    ]

    publisher = MockPublisher()
    executor = Executor(
        sim_client=mock_sim,
        session_factory=session_factory,
        publisher=publisher,
    )

    result_dec = await executor.execute(dec, lines)

    assert result_dec.status == "EXECUTED"
    assert lines[0].status == "SUBMITTED"
    assert lines[0].sim_allocation_id == 1001
    assert lines[1].status == "SUBMITTED"
    assert lines[1].sim_allocation_id == 1002

    # Verify database persistence
    async with session_factory() as session:
        db_dec = await session.get(Decision, dec.id)
        assert db_dec.status == "EXECUTED"
        res = await session.execute(select(DecisionLine).where(DecisionLine.decision_id == dec.id))
        db_lines = res.scalars().all()
        assert all(ln.status == "SUBMITTED" for ln in db_lines)

    # Verify event published
    assert len(publisher.events) == 1
    assert publisher.events[0]["type"] == "decision.executed"
    assert publisher.events[0]["data"]["submitted_lines"] == 2


async def test_execute_deferred_on_dispatch_capacity_exceeded(session_factory, test_sim_run_id):
    dec, lines = await _create_test_decision_with_lines(session_factory, test_sim_run_id, num_lines=1)
    mock_sim = AsyncMock()
    mock_sim.create_allocation.side_effect = SimDomainError(
        code="DISPATCH_CAPACITY_EXCEEDED",
        message="Depot out of dispatch capacity",
        status=409,
    )

    publisher = MockPublisher()
    executor = Executor(
        sim_client=mock_sim,
        session_factory=session_factory,
        publisher=publisher,
    )

    result_dec = await executor.execute(dec, lines)

    assert result_dec.status == "FAILED"
    assert lines[0].status == "DEFERRED"
    assert lines[0].sim_error_code == "DISPATCH_CAPACITY_EXCEEDED"
    assert "Dispatch capacity exceeded" in lines[0].reason

    assert len(publisher.events) == 1
    assert publisher.events[0]["type"] == "decision.failed"
    assert publisher.events[0]["data"]["deferred_lines"] == 1


async def test_execute_replayed_already_exists(session_factory, test_sim_run_id):
    dec, lines = await _create_test_decision_with_lines(session_factory, test_sim_run_id, num_lines=1)
    mock_sim = AsyncMock()
    mock_sim.create_allocation.side_effect = SimDomainError(
        code="ALREADY_EXISTS",
        message="Duplicate idempotency key",
        status=409,
        data={"allocation_id": 888},
    )

    executor = Executor(sim_client=mock_sim, session_factory=session_factory)
    result_dec = await executor.execute(dec, lines)

    assert result_dec.status == "EXECUTED"
    assert lines[0].status == "SUBMITTED"
    assert lines[0].sim_allocation_id == 888


async def test_execute_rejected_by_sim(session_factory, test_sim_run_id):
    dec, lines = await _create_test_decision_with_lines(session_factory, test_sim_run_id, num_lines=1)
    mock_sim = AsyncMock()
    mock_sim.create_allocation.side_effect = SimDomainError(
        code="ROUTE_CLOSED",
        message="Road blocked due to flood",
        status=409,
    )

    executor = Executor(sim_client=mock_sim, session_factory=session_factory)
    result_dec = await executor.execute(dec, lines)

    assert result_dec.status == "FAILED"
    assert lines[0].status == "REJECTED_BY_SIM"
    assert lines[0].sim_error_code == "ROUTE_CLOSED"
    assert "REJECTED: ROUTE_CLOSED" in lines[0].reason


async def test_execute_transient_retry_and_success(session_factory, test_sim_run_id):
    dec, lines = await _create_test_decision_with_lines(session_factory, test_sim_run_id, num_lines=1)
    mock_sim = AsyncMock()
    mock_sim.create_allocation.side_effect = [
        SimFaultInjected("Simulated fault"),
        SimResult(data={"id": 2002}, stale=False, status=201, latency_ms=15.0),
    ]

    executor = Executor(
        sim_client=mock_sim,
        session_factory=session_factory,
        initial_backoff=0.01,
    )
    result_dec = await executor.execute(dec, lines)

    assert result_dec.status == "EXECUTED"
    assert lines[0].status == "SUBMITTED"
    assert lines[0].sim_allocation_id == 2002
    assert mock_sim.create_allocation.call_count == 2


async def test_execute_transient_exhausted_retries(session_factory, test_sim_run_id):
    dec, lines = await _create_test_decision_with_lines(session_factory, test_sim_run_id, num_lines=1)
    mock_sim = AsyncMock()
    mock_sim.create_allocation.side_effect = SimUnavailable("Connection refused")

    executor = Executor(
        sim_client=mock_sim,
        session_factory=session_factory,
        max_retries=3,
        initial_backoff=0.01,
    )
    result_dec = await executor.execute(dec, lines)

    assert result_dec.status == "FAILED"
    assert lines[0].status == "FAILED"
    assert lines[0].sim_error_code == "SIMULATOR_UNAVAILABLE"
    assert mock_sim.create_allocation.call_count == 3


async def test_execute_partial_status(session_factory, test_sim_run_id):
    dec, lines = await _create_test_decision_with_lines(session_factory, test_sim_run_id, num_lines=2)
    mock_sim = AsyncMock()
    mock_sim.create_allocation.side_effect = [
        SimResult(data={"id": 3001}, stale=False, status=201, latency_ms=10.0),
        SimDomainError(code="DISPATCH_CAPACITY_EXCEEDED", message="No capacity", status=409),
    ]

    publisher = MockPublisher()
    executor = Executor(
        sim_client=mock_sim,
        session_factory=session_factory,
        publisher=publisher,
    )
    result_dec = await executor.execute(dec, lines)

    assert result_dec.status == "PARTIAL"
    assert lines[0].status == "SUBMITTED"
    assert lines[1].status == "DEFERRED"

    assert len(publisher.events) == 1
    assert publisher.events[0]["type"] == "decision.partial"
    assert publisher.events[0]["data"]["submitted_lines"] == 1
    assert publisher.events[0]["data"]["deferred_lines"] == 1
